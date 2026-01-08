"""
src/chem/batch_runner.py

Batch runner for AIE-aTB computations.

Iterates over molecule_table.parquet, runs AIE-aTB for each molecule,
and consolidates results into atb_features.parquet and atb_qc.parquet.
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

import pandas as pd

from src.utils.logging import get_logger
from src.agents.atb_agent import ATBAgent
from src.chem.atb_runner import run_atb, create_status_json, get_atb_version
from src.chem.atb_parser import parse_result_json

logger = get_logger(__name__)


def run_batch(
    molecule_table_path: str = "data/molecule_table.parquet",
    cache_dir: str = "cache/atb",
    output_dir: str = "data",
    limit: Optional[int] = None,
    force_rerun: bool = False,
    config: Optional[Dict[str, Any]] = None,
    project_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Run AIE-aTB batch computation.

    IMPORTANT: Uses molecule_table.parquet as single source of truth.
    DO NOT use private_clean.parquet (has duplicates).

    Args:
        molecule_table_path: Path to molecule_table.parquet
        cache_dir: Cache directory for aTB results
        output_dir: Output directory for parquet files
        limit: Limit number of molecules (for dry-run)
        force_rerun: Force rerun even if cache exists
        config: Optional config overrides for aTB
        project_root: Project root directory

    Returns:
        Summary dict with counts and timing
    """
    if project_root is None:
        project_root = Path.cwd()

    # Load molecule table (SINGLE SOURCE OF TRUTH)
    logger.info(f"Loading molecule table from {molecule_table_path}")
    molecule_table = pd.read_parquet(molecule_table_path)
    total_molecules = len(molecule_table)
    logger.info(f"Found {total_molecules} unique molecules")

    if limit:
        molecule_table = molecule_table.head(limit)
        logger.info(f"Limited to {len(molecule_table)} molecules (dry-run mode)")

    # Initialize agent for cache operations
    atb_agent = ATBAgent(cache_dir=cache_dir)

    # Results collection
    features_rows = []
    qc_rows = []
    skipped = 0
    succeeded = 0
    failed = 0

    start_time = time.time()

    invalid_smiles = 0

    for idx, row in molecule_table.iterrows():
        inchikey = row["inchikey"]
        smiles = row["canonical_smiles"]  # From molecule_table (single source of truth)
        # Skip molecules with empty/invalid inchikey (failed canonicalization in P1)
        if not inchikey or len(inchikey) < 2:
            logger.warning(f"[{idx+1}/{len(molecule_table)}] Skipping invalid InChIKey (empty): SMILES={smiles[:50]}...")
            invalid_smiles += 1
            continue

        logger.info(f"[{idx+1}/{len(molecule_table)}] Processing {inchikey}")

        # Get cache path
        cache_path = atb_agent.get_cache_path(inchikey)

        # Check existing cache
        if not force_rerun and atb_agent.check_cache(inchikey):
            status = atb_agent.load_status(inchikey)
            if status.get("run_status") == "success":
                logger.info(f"  Skipping {inchikey}: already succeeded")
                skipped += 1

                # Load existing features
                features = atb_agent.load_features(inchikey)
                if features:
                    features_rows.append({"inchikey": inchikey, **features})
                    qc_rows.append({
                        "inchikey": inchikey,
                        "run_status": "success",
                        "fail_stage": None,
                        "error_msg": None,
                        "runtime_sec": status.get("runtime_sec"),
                        "atb_version": status.get("atb_version"),
                        "timestamp": status.get("timestamp"),
                    })
                continue

        # Run AIE-aTB
        run_start = time.time()
        run_status, fail_stage, error_msg = run_atb(
            inchikey=inchikey,
            smiles=smiles,
            cache_path=cache_path,
            config=config,
            project_root=project_root,
        )
        runtime_sec = time.time() - run_start

        # Save status.json
        create_status_json(
            inchikey=inchikey,
            run_status=run_status,
            fail_stage=fail_stage,
            error_msg=error_msg,
            runtime_sec=runtime_sec,
            cache_path=cache_path,
        )

        # Collect results
        qc_row = {
            "inchikey": inchikey,
            "run_status": run_status,
            "fail_stage": fail_stage,
            "error_msg": error_msg,
            "runtime_sec": round(runtime_sec, 2),
            "atb_version": get_atb_version() if run_status == "success" else None,
            "timestamp": datetime.now().isoformat(),
        }
        qc_rows.append(qc_row)

        if run_status == "success":
            succeeded += 1
            features = atb_agent.load_features(inchikey)
            if features:
                features_rows.append({"inchikey": inchikey, **features})
        else:
            failed += 1
            # Add row with nulls for failed molecules
            features_rows.append({
                "inchikey": inchikey,
                "run_status": run_status,
                "fail_stage": fail_stage,
            })

    total_time = time.time() - start_time

    # Save parquet files
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    if features_rows:
        features_df = pd.DataFrame(features_rows)
        features_path = output_path / "atb_features.parquet"
        features_df.to_parquet(features_path, index=False)
        logger.info(f"Saved {len(features_df)} rows to {features_path}")

    if qc_rows:
        qc_df = pd.DataFrame(qc_rows)
        qc_path = output_path / "atb_qc.parquet"
        qc_df.to_parquet(qc_path, index=False)
        logger.info(f"Saved {len(qc_df)} rows to {qc_path}")

    # Summary
    summary = {
        "total_molecules": len(molecule_table),
        "invalid_smiles": invalid_smiles,
        "skipped": skipped,
        "succeeded": succeeded,
        "failed": failed,
        "total_time_sec": round(total_time, 2),
        "avg_time_per_molecule_sec": round(total_time / max(1, succeeded + failed), 2),
    }

    logger.info(f"Batch complete: {summary}")
    return summary


def consolidate_cache_to_parquet(
    cache_dir: str = "cache/atb",
    output_dir: str = "data",
) -> Dict[str, int]:
    """
    Consolidate all cache results into parquet files.

    Useful for recovering after partial runs or when cache exists
    but parquet files need to be regenerated.

    Args:
        cache_dir: Cache directory
        output_dir: Output directory for parquet files

    Returns:
        Dict with row counts
    """
    cache_path = Path(cache_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    features_rows = []
    qc_rows = []

    # Walk cache directory
    for prefix_dir in sorted(cache_path.iterdir()):
        if not prefix_dir.is_dir():
            continue

        for mol_dir in sorted(prefix_dir.iterdir()):
            if not mol_dir.is_dir():
                continue

            inchikey = mol_dir.name
            status_file = mol_dir / "status.json"
            features_file = mol_dir / "features.json"

            if not status_file.exists():
                continue

            with open(status_file, "r") as f:
                status = json.load(f)

            qc_rows.append({
                "inchikey": inchikey,
                "run_status": status.get("run_status"),
                "fail_stage": status.get("fail_stage"),
                "error_msg": status.get("error_msg"),
                "runtime_sec": status.get("runtime_sec"),
                "atb_version": status.get("atb_version"),
                "timestamp": status.get("timestamp"),
            })

            if features_file.exists():
                with open(features_file, "r") as f:
                    features = json.load(f)
                features_rows.append({"inchikey": inchikey, **features})
            else:
                features_rows.append({
                    "inchikey": inchikey,
                    "run_status": status.get("run_status"),
                    "fail_stage": status.get("fail_stage"),
                })

    # Save parquet files
    if features_rows:
        features_df = pd.DataFrame(features_rows)
        features_path = output_path / "atb_features.parquet"
        features_df.to_parquet(features_path, index=False)
        logger.info(f"Consolidated {len(features_df)} rows to {features_path}")

    if qc_rows:
        qc_df = pd.DataFrame(qc_rows)
        qc_path = output_path / "atb_qc.parquet"
        qc_df.to_parquet(qc_path, index=False)
        logger.info(f"Consolidated {len(qc_df)} rows to {qc_path}")

    return {
        "features_rows": len(features_rows),
        "qc_rows": len(qc_rows),
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AIE-aTB Batch Runner")
    parser.add_argument(
        "--molecule-table",
        default="data/molecule_table.parquet",
        help="Path to molecule_table.parquet",
    )
    parser.add_argument(
        "--cache-dir",
        default="cache/atb",
        help="Cache directory",
    )
    parser.add_argument(
        "--output-dir",
        default="data",
        help="Output directory for parquet files",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of molecules (for dry-run)",
    )
    parser.add_argument(
        "--force-rerun",
        action="store_true",
        help="Force rerun even if cache exists",
    )
    parser.add_argument(
        "--npara",
        type=int,
        default=4,
        help="Number of parallel Amesp processes",
    )
    parser.add_argument(
        "--maxcore",
        type=int,
        default=4000,
        help="Memory per core in MB",
    )
    parser.add_argument(
        "--consolidate-only",
        action="store_true",
        help="Only consolidate existing cache to parquet (no new runs)",
    )

    args = parser.parse_args()

    if args.consolidate_only:
        result = consolidate_cache_to_parquet(
            cache_dir=args.cache_dir,
            output_dir=args.output_dir,
        )
        print(f"Consolidation complete: {result}")
    else:
        config = {
            "npara": args.npara,
            "maxcore": args.maxcore,
        }

        summary = run_batch(
            molecule_table_path=args.molecule_table,
            cache_dir=args.cache_dir,
            output_dir=args.output_dir,
            limit=args.limit,
            force_rerun=args.force_rerun,
            config=config,
        )
        print(f"Batch summary: {summary}")
