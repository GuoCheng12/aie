"""
src/chem/atb_runner.py

Subprocess wrapper for AIE-aTB (third_party/aTB/main.py).

Calls AIE-aTB as a black-box subprocess and handles status tracking.
"""

import json
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

from src.utils.logging import get_logger
from src.chem.atb_parser import parse_result_json, save_features_json

logger = get_logger(__name__)

# Default configuration
DEFAULT_CONFIG = {
    "npara": 4,
    "maxcore": 4000,
    "nimg": 3,
    "neb_fmax": 0.1,
    "opt_fmax": 0.03,
    "timeout": 3600,  # 1 hour timeout
}

# Path to AIE-aTB main.py (relative to project root)
ATB_SCRIPT = "third_party/aTB/main.py"


def get_atb_version() -> str:
    """
    Get AIE-aTB version string.

    For V0, we use "AIE-aTB-v0" as placeholder.
    In production, could extract git hash from third_party/aTB.

    Returns:
        Version string
    """
    # TODO: Extract actual version from third_party/aTB git hash
    return "AIE-aTB-v0"


def run_atb(
    inchikey: str,
    smiles: str,
    cache_path: Path,
    config: Optional[Dict[str, Any]] = None,
    project_root: Optional[Path] = None,
) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Run AIE-aTB for a single molecule.

    Args:
        inchikey: InChIKey of the molecule
        smiles: Canonical SMILES string
        cache_path: Cache directory path (will be used as workdir)
        config: Optional config overrides (npara, maxcore, etc.)
        project_root: Project root directory (for locating ATB_SCRIPT)

    Returns:
        Tuple of (run_status, fail_stage, error_msg)
        - Success: ("success", None, None)
        - Failure: ("failed", fail_stage, error_msg)
    """
    cfg = {**DEFAULT_CONFIG, **(config or {})}

    if project_root is None:
        project_root = Path.cwd()

    atb_script = project_root / ATB_SCRIPT

    if not atb_script.exists():
        logger.error(f"AIE-aTB script not found at {atb_script}")
        return "failed", "opt", f"AIE-aTB script not found: {atb_script}"

    # Ensure cache directory exists
    cache_path.mkdir(parents=True, exist_ok=True)

    # Store canonical SMILES for audit
    smiles_file = cache_path / "canonical_smiles.txt"
    with open(smiles_file, "w") as f:
        f.write(smiles)

    # Build command
    cmd = [
        "python", str(atb_script),
        "--smiles", smiles,
        "--workdir", str(cache_path),
        "--npara", str(cfg["npara"]),
        "--maxcore", str(cfg["maxcore"]),
        "--nimg", str(cfg["nimg"]),
        "--neb_fmax", str(cfg["neb_fmax"]),
        "--opt_fmax", str(cfg["opt_fmax"]),
    ]

    logger.info(f"Running AIE-aTB for {inchikey}")
    logger.debug(f"Command: {' '.join(cmd)}")

    start_time = time.time()

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=cfg["timeout"],
            cwd=str(project_root),
        )

        runtime_sec = time.time() - start_time

        # Log stdout/stderr for debugging
        if result.stdout:
            logger.debug(f"AIE-aTB stdout:\n{result.stdout[-2000:]}")
        if result.stderr:
            logger.warning(f"AIE-aTB stderr:\n{result.stderr[-500:]}")

        if result.returncode != 0:
            error_msg = result.stderr[-500:] if result.stderr else f"Exit code: {result.returncode}"
            logger.error(f"AIE-aTB failed for {inchikey}: {error_msg}")

            # Try to detect which stage failed from partial output
            fail_stage = detect_fail_stage_from_output(cache_path, result.stdout, result.stderr)
            return "failed", fail_stage, error_msg

        # Parse result.json
        result_json_path = cache_path / "result.json"
        features, fail_stage = parse_result_json(result_json_path)

        if fail_stage:
            return "failed", fail_stage, "Incomplete result.json"

        # Save features.json
        save_features_json(features, cache_path)

        logger.info(f"AIE-aTB completed successfully for {inchikey} in {runtime_sec:.1f}s")
        return "success", None, None

    except subprocess.TimeoutExpired:
        runtime_sec = time.time() - start_time
        error_msg = f"Timeout after {cfg['timeout']}s"
        logger.error(f"AIE-aTB timeout for {inchikey}: {error_msg}")

        # Try to detect which stage was running when timeout occurred
        fail_stage = detect_fail_stage_from_output(cache_path, "", "")
        return "failed", fail_stage or "opt", error_msg

    except Exception as e:
        error_msg = str(e)[:500]
        logger.error(f"AIE-aTB exception for {inchikey}: {error_msg}")
        return "failed", "opt", error_msg


def detect_fail_stage_from_output(
    cache_path: Path,
    stdout: str,
    stderr: str,
) -> str:
    """
    Detect failure stage from partial output and cache state.

    Args:
        cache_path: Cache directory path
        stdout: Subprocess stdout
        stderr: Subprocess stderr

    Returns:
        Detected fail_stage string
    """
    # Check which directories/files exist
    opt_dir = cache_path / "opt"
    excit_dir = cache_path / "excit"
    neb_dir = cache_path / "neb"
    result_json = cache_path / "result.json"

    # If result.json exists but is incomplete, parse to find stage
    if result_json.exists():
        try:
            with open(result_json, "r") as f:
                result = json.load(f)
            if "ground_state" not in result:
                return "opt"
            if "excited_state" not in result:
                return "excit"
            if "NEB" not in result:
                return "neb"
            return "volume"
        except json.JSONDecodeError:
            return "feature_parse"

    # Infer from directory state
    if not opt_dir.exists() or not (opt_dir / "opted.xyz").exists():
        return "opt"
    if not excit_dir.exists() or not (excit_dir / "excited.xyz").exists():
        return "excit"
    if not neb_dir.exists():
        return "neb"

    # Check stdout for stage keywords
    if "NEB" in stdout or "neb" in stdout.lower():
        return "neb"
    if "excit" in stdout.lower():
        return "excit"

    return "opt"  # Default to earliest stage


def create_status_json(
    inchikey: str,
    run_status: str,
    fail_stage: Optional[str],
    error_msg: Optional[str],
    runtime_sec: Optional[float],
    cache_path: Path,
) -> Path:
    """
    Create or update status.json in cache directory.

    Args:
        inchikey: InChIKey of the molecule
        run_status: "success", "failed", or "pending"
        fail_stage: Stage where failure occurred (if failed)
        error_msg: Error message (if failed)
        runtime_sec: Total runtime in seconds
        cache_path: Cache directory path

    Returns:
        Path to status.json
    """
    cache_path.mkdir(parents=True, exist_ok=True)

    status = {
        "inchikey": inchikey,
        "run_status": run_status,
        "fail_stage": fail_stage,
        "error_msg": error_msg[:500] if error_msg else None,
        "timestamp": datetime.now().isoformat(),
        "atb_version": get_atb_version() if run_status == "success" else None,
        "runtime_sec": round(runtime_sec, 2) if runtime_sec else None,
    }

    status_path = cache_path / "status.json"
    with open(status_path, "w") as f:
        json.dump(status, f, indent=2)

    logger.debug(f"Saved status.json: run_status={run_status}")
    return status_path
