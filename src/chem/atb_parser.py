"""
src/chem/atb_parser.py

Parse AIE-aTB result.json → features.json

Parses the output from third_party/aTB/main.py and extracts
structured descriptors for our pipeline.
"""

import json
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

from src.utils.logging import get_logger

logger = get_logger(__name__)


def parse_result_json(result_path: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Parse AIE-aTB result.json and extract features.

    Args:
        result_path: Path to result.json

    Returns:
        Tuple of (features_dict, fail_stage)
        - If success: (features_dict, None)
        - If failure: (None, fail_stage_string)
    """
    # Check file exists
    if not result_path.exists():
        logger.warning(f"result.json not found at {result_path}")
        return None, "feature_parse"

    # Try to load JSON
    try:
        with open(result_path, "r") as f:
            result = json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse result.json: {e}")
        return None, "feature_parse"

    # Check for required keys and detect failure stage
    fail_stage = detect_fail_stage(result)
    if fail_stage:
        return None, fail_stage

    # Extract features
    try:
        features = extract_features(result)
        return features, None
    except Exception as e:
        logger.error(f"Failed to extract features: {e}")
        return None, "feature_parse"


def detect_fail_stage(result: Dict[str, Any]) -> Optional[str]:
    """
    Detect which stage failed based on missing keys in result.json.

    Order of detection:
    1. ground_state missing → "opt"
    2. excited_state missing → "excit"
    3. NEB missing → "neb"
    4. volume missing in either state → "volume"

    Returns:
        fail_stage string if failure detected, None if success
    """
    # Check ground_state (S0 optimization)
    if "ground_state" not in result:
        logger.warning("ground_state missing from result.json")
        return "opt"

    gs = result["ground_state"]

    # Check excited_state (S1 optimization)
    if "excited_state" not in result:
        logger.warning("excited_state missing from result.json")
        return "excit"

    es = result["excited_state"]

    # Check NEB
<<<<<<< HEAD
    if "NEB" not in result:
=======
    if "exciting_path_mean_volume" not in result:
>>>>>>> 605e931 (add ionic caculator & rota. const. & excited energy)
        logger.warning("NEB missing from result.json")
        return "neb"

    # Check volumes
    if "volume" not in gs or "volume" not in es:
        logger.warning("volume missing from ground_state or excited_state")
        return "volume"

    return None


def extract_features(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract structured features from valid result.json.

    Maps AIE-aTB output to our features.json schema:
    - s0_volume, s1_volume, delta_volume
    - s0_homo_lumo_gap, s1_homo_lumo_gap, delta_gap
    - s0_dihedral_avg, s1_dihedral_avg, delta_dihedral
    - s0_charge_dipole, s1_charge_dipole, delta_dipole (computed if possible)
    - excitation_energy (null in V0)
    - neb_mean_volume

    Args:
        result: Parsed result.json dict

    Returns:
        features dict matching our schema
    """
    gs = result["ground_state"]
    es = result["excited_state"]
<<<<<<< HEAD
    neb = result.get("NEB")
=======
    neb = result.get("exciting_path_mean_volume")
>>>>>>> 605e931 (add ionic caculator & rota. const. & excited energy)

    # Volume
    s0_volume = gs.get("volume")
    s1_volume = es.get("volume")
    delta_volume = (s1_volume - s0_volume) if (s0_volume is not None and s1_volume is not None) else None

    # HOMO-LUMO gap (stored as string in result.json)
    s0_gap_str = gs.get("HOMO-LUMO")
    s1_gap_str = es.get("HOMO-LUMO")
    s0_homo_lumo_gap = float(s0_gap_str) if s0_gap_str else None
    s1_homo_lumo_gap = float(s1_gap_str) if s1_gap_str else None
    delta_gap = (s1_homo_lumo_gap - s0_homo_lumo_gap) if (s0_homo_lumo_gap is not None and s1_homo_lumo_gap is not None) else None

    # Dihedral average (from structure.DA)
    s0_struct = gs.get("structure", {})
    s1_struct = es.get("structure", {})
    s0_dihedral_avg = s0_struct.get("DA")
    s1_dihedral_avg = s1_struct.get("DA")
    delta_dihedral = (s1_dihedral_avg - s0_dihedral_avg) if (s0_dihedral_avg is not None and s1_dihedral_avg is not None) else None

    # Charge dipole - compute from Mulliken charges if available
<<<<<<< HEAD
    s0_charge_dipole = compute_charge_dipole(gs.get("charge"))
    s1_charge_dipole = compute_charge_dipole(es.get("charge"))
    delta_dipole = (s1_charge_dipole - s0_charge_dipole) if (s0_charge_dipole is not None and s1_charge_dipole is not None) else None

=======
    delta_dipole = compute_charge_dipole(result.get("charge"))
>>>>>>> 605e931 (add ionic caculator & rota. const. & excited energy)
    # Additional structure properties (for reference)
    s0_bonds_avg = s0_struct.get("bonds")
    s1_bonds_avg = s1_struct.get("bonds")
    s0_angles_avg = s0_struct.get("angles")
    s1_angles_avg = s1_struct.get("angles")

<<<<<<< HEAD
    features = {
=======
    # rotational_constant
    s0_rc_a = gs['rotational_constant'].get('A')
    s0_rc_b = gs['rotational_constant'].get('B')
    s0_rc_c = gs['rotational_constant'].get('C')
    s1_rc_a = es['rotational_constant'].get('A')
    s1_rc_b = es['rotational_constant'].get('B')
    s1_rc_c = es['rotational_constant'].get('C')
    s0_rap = gs['rays_asymmetry_parameter']
    s1_rap = es['rays_asymmetry_parameter']


    features = {
        # rotational_constant
        "s0_rotational_constant_a" : s0_rc_a,
        "s0_rotational_constant_b" : s0_rc_b,
        "s0_rotational_constant_c" : s0_rc_c,
        "s1_rotational_constant_a" : s1_rc_a,
        "s1_rotational_constant_b" : s1_rc_b,
        "s1_rotational_constant_c" : s1_rc_c,
        "s0_rays_asymmetry_parameter" : s0_rap,
        "s1_rays_asymmetry_parameter" : s1_rap,
>>>>>>> 605e931 (add ionic caculator & rota. const. & excited energy)
        # Volume
        "s0_volume": s0_volume,
        "s1_volume": s1_volume,
        "delta_volume": delta_volume,
        # HOMO-LUMO gap
        "s0_homo_lumo_gap": s0_homo_lumo_gap,
        "s1_homo_lumo_gap": s1_homo_lumo_gap,
        "delta_gap": delta_gap,
        # Dihedral
        "s0_dihedral_avg": s0_dihedral_avg,
        "s1_dihedral_avg": s1_dihedral_avg,
        "delta_dihedral": delta_dihedral,
        # Charge dipole (computed from Mulliken charges)
<<<<<<< HEAD
        "s0_charge_dipole": s0_charge_dipole,
        "s1_charge_dipole": s1_charge_dipole,
        "delta_dipole": delta_dipole,
        # Excitation energy - not directly in result.json, set null for V0
        "excitation_energy": None,
        # NEB mean volume
        "neb_mean_volume": neb,
=======
        "delta_dipole": delta_dipole,
        # Excitation energy - not directly in result.json, set null for V0
        "excitation_energy": es.get('excited_energy'),
        # NEB mean volume
        "exciting_path_mean_volume": neb,
>>>>>>> 605e931 (add ionic caculator & rota. const. & excited energy)
        # Extra structure metrics (informational)
        "s0_bonds_avg": s0_bonds_avg,
        "s1_bonds_avg": s1_bonds_avg,
        "s0_angles_avg": s0_angles_avg,
        "s1_angles_avg": s1_angles_avg,
    }

    return features


def compute_charge_dipole(charge_data: Optional[Dict[str, Any]]) -> Optional[float]:
    """
    Compute a simple charge dipole metric from Mulliken charges.

    For V0, we compute the sum of absolute charges as a simple metric.
    A more sophisticated dipole calculation would require coordinates.

    Args:
        charge_data: Dict with "element" and "charge" lists

    Returns:
        Sum of absolute charges, or None if data unavailable
    """
    if not charge_data:
        return None

    charges = charge_data.get("charge", [])
    if not charges:
        return None

    # Simple metric: sum of absolute charges (charge separation indicator)
    return sum(abs(c) for c in charges)


def save_features_json(features: Dict[str, Any], cache_path: Path) -> Path:
    """
    Save features dict to features.json in cache directory.

    Args:
        features: Features dict
        cache_path: Cache directory path

    Returns:
        Path to saved features.json
    """
    features_path = cache_path / "features.json"
    with open(features_path, "w") as f:
        json.dump(features, f, indent=2)

    logger.info(f"Saved features.json to {features_path}")
    return features_path
