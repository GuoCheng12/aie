"""
src/cli.py

CLI for Uncertainty-aware AIE pipeline (Mode A orchestration).

Commands:
- fetch: Fetch record by id
- compute-atb: Check aTB cache and mark pending if missing
- run: Full orchestration (fetch + atb + report)
- uq: Online UQ for arbitrary SMILES (pre-P6 test)
- report: Generate P6a pre-aTB report for a specific record
"""

import sys
import csv
import json
import argparse
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.agents.data_agent import DataAgent
from src.agents.atb_agent import ATBAgent
from src.utils.logging import setup_logger

logger = setup_logger(__name__, level="INFO")

# STRICT ALLOWLIST: Fields safe to include in reports
# Excludes sensitive fields like "comment" which may contain private information
REPORT_FIELD_ALLOWLIST = [
    # Core identifiers
    "id", "code", "SMILES", "canonical_smiles", "inchikey",
    "reference", "doi",

    # Train-only private observations
    "emission_solid", "emission_aggr",

    # Additional train columns
    "molecular_weight",

    # Mechanism/feature IDs
    "features_id", "mechanism_id",

    # Missing indicators (train-only)
    "emission_solid_missing", "emission_aggr_missing",
]

# BLOCKED FIELDS: Never include in reports (privacy/sensitivity)
REPORT_FIELD_BLOCKLIST = [
    "comment",  # May contain sensitive researcher notes or private information
]


def filter_record_fields(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Filter record fields according to allowlist/blocklist.

    Args:
        record: Full record dictionary

    Returns:
        Filtered dictionary with only allowed fields
    """
    filtered = {}
    for key in REPORT_FIELD_ALLOWLIST:
        if key in record:
            filtered[key] = record[key]

    # Double-check blocklist (should already be excluded by allowlist)
    for blocked_key in REPORT_FIELD_BLOCKLIST:
        if blocked_key in filtered:
            logger.warning(f"Blocked field '{blocked_key}' found in filtered record, removing")
            del filtered[blocked_key]

    return filtered


def _run_ready_agent_on_case(case_path: Path) -> Dict[str, Any]:
    from src.agents.ready_agent import review_case_and_patch, apply_ready_agent_patch

    case_before = json.loads(case_path.read_text(encoding="utf-8"))
    patch_ops = review_case_and_patch(case_before)
    case_after = apply_ready_agent_patch(case_before, patch_ops)
    case_path.write_text(json.dumps(case_after, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "patch_ops": patch_ops,
        "current_gate": case_after.get("current_gate"),
    }


def fetch_command(args):
    """Fetch record by id and display."""
    try:
        agent = DataAgent(data_dir=args.data_dir)
        record = agent.get_record_by_id(args.id)

        # Print formatted output
        if args.json:
            print(json.dumps(record, indent=2))
        else:
            print(f"Record id={args.id}")
            print(f"  InChIKey: {record.get('inchikey', 'N/A')}")
            print(f"  SMILES: {record.get('canonical_smiles', 'N/A')}")
            print(f"  Emission (solid): {record.get('emission_solid', 'N/A')}")
            print(f"  Emission (aggr): {record.get('emission_aggr', 'N/A')}")

    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to fetch record: {e}")
        sys.exit(1)


def compute_atb_command(args):
    """Check aTB cache and mark pending if missing."""
    try:
        # Fetch record to get InChIKey
        data_agent = DataAgent(data_dir=args.data_dir)
        record = data_agent.get_record_by_id(args.id)

        inchikey = record.get("inchikey")
        if not inchikey:
            logger.error(f"Record id={args.id} has no valid InChIKey (invalid SMILES)")
            sys.exit(1)

        # Check cache
        atb_agent = ATBAgent(cache_dir=args.cache_dir)
        cache_exists = atb_agent.check_cache(inchikey)

        if cache_exists:
            status = atb_agent.load_status(inchikey)
            print(f"Cache HIT for {inchikey}")
            print(f"  Status: {status.get('run_status', 'unknown')}")
            if status.get("fail_stage"):
                print(f"  Failed at: {status['fail_stage']}")
        else:
            print(f"Cache MISS for {inchikey}")
            print(f"  Marking as pending (no real aTB computation in Mode A)")
            smiles = record.get("canonical_smiles")
            status_file = atb_agent.mark_pending(inchikey, smiles)
            print(f"  Created: {status_file}")

    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to check aTB cache: {e}")
        sys.exit(1)


def run_command(args):
    """Full orchestration: fetch + atb + assemble + report."""
    try:
        # Step 1: Fetch record
        logger.info(f"[1/5] Fetching record id={args.id}")
        data_agent = DataAgent(data_dir=args.data_dir)
        record = data_agent.get_record_by_id(args.id)

        inchikey = record.get("inchikey")
        smiles = record.get("canonical_smiles")

        if not inchikey:
            logger.error(f"Record id={args.id} has no valid InChIKey (invalid SMILES)")
            sys.exit(1)

        # Step 2: Get missing summary
        logger.info("[2/5] Computing missing value summary")
        missing_summary = data_agent.get_missing_summary(record)

        # Step 3: Check aTB cache
        logger.info("[3/5] Checking aTB cache")
        atb_agent = ATBAgent(cache_dir=args.cache_dir)
        cache_summary = atb_agent.get_cache_summary(inchikey)

        atb_status = "miss"
        atb_features = None

        if cache_summary["cache_exists"]:
            atb_status = "hit"
            run_status = cache_summary.get("run_status")

            if run_status == "success" and cache_summary["features_available"]:
                atb_features = atb_agent.load_features(inchikey)
            elif run_status == "pending":
                atb_status = "pending"
            elif run_status == "failed":
                atb_status = "failed"
        else:
            # Mark as pending
            logger.info(f"Cache miss, marking {inchikey} as pending")
            atb_agent.mark_pending(inchikey, smiles)
            atb_status = "pending"

        # Step 4: Load UQ scores if available
        logger.info("[4/6] Loading UQ scores (P5a)")
        uq_info = None
        uq_path = Path(args.data_dir) / "uq_scores_pre_atb.parquet"
        
        if uq_path.exists():
            try:
                import pandas as pd
                uq_df = pd.read_parquet(uq_path)
                uq_row = uq_df[uq_df['id'] == args.id]
                
                if len(uq_row) > 0:
                    row = uq_row.iloc[0]
                    uq_info = {
                        "coverage": float(row['coverage']) if pd.notna(row['coverage']) else None,
                        "C_sim": float(row['C_sim']) if pd.notna(row['C_sim']) else None,
                        "C_meta": float(row['C_meta']) if pd.notna(row['C_meta']) else None,
                        "novelty": float(row['novelty']) if pd.notna(row['novelty']) else None,
                        "aleatoric": float(row['aleatoric']) if pd.notna(row['aleatoric']) else None,
                        "router_action": str(row['router_action']),
                        "recommended_next_steps": json.loads(row['recommended_next_steps']) if isinstance(row['recommended_next_steps'], str) else row['recommended_next_steps'],
                        "notes": row.get('notes', '')
                    }
                    logger.info(f"  P5a loaded: router_action={uq_info['router_action']}")
                else:
                    logger.warning(f"  Record id={args.id} not found in UQ scores")
            except Exception as e:
                logger.warning(f"  Failed to load UQ scores: {e}")
        else:
            logger.info(f"  UQ scores not computed yet ({uq_path} not found)")
            logger.info("  Run: python -m src.uq.compute_uq_pre_atb")

        # Step 5: Load P5b UQ scores if available
        logger.info("[5/6] Loading UQ scores (P5b with mechanism_entropy)")
        uq_info_p5b = None
        uq_p5b_path = Path(args.data_dir) / "uq_scores_pre_atb_p5b.parquet"
        
        if uq_p5b_path.exists():
            try:
                import pandas as pd
                uq_p5b_df = pd.read_parquet(uq_p5b_path)
                uq_p5b_row = uq_p5b_df[uq_p5b_df['id'] == args.id]
                
                if len(uq_p5b_row) > 0:
                    row = uq_p5b_row.iloc[0]
                    uq_info_p5b = {
                        "mechanism_entropy": float(row['mechanism_entropy']) if pd.notna(row['mechanism_entropy']) else None,
                        "M_eff": int(row['M_eff']) if pd.notna(row['M_eff']) else None,
                        "top_label": str(row['top_label']) if pd.notna(row['top_label']) else None,
                        "router_action_p5b": str(row['router_action_p5b']),
                        "recommended_next_steps_p5b": json.loads(row['recommended_next_steps_p5b']) if isinstance(row['recommended_next_steps_p5b'], str) else row['recommended_next_steps_p5b']
                    }
                    logger.info(f"  P5b loaded: router_action_p5b={uq_info_p5b['router_action_p5b']}")
                else:
                    logger.warning(f"  Record id={args.id} not found in P5b UQ scores")
            except Exception as e:
                logger.warning(f"  Failed to load P5b UQ scores: {e}")
        else:
            logger.info(f"  P5b UQ scores not computed yet ({uq_p5b_path} not found)")
            logger.info("  Run: python -m src.uq.compute_uq_pre_atb_p5b")

        # Step 6: Assemble output
        logger.info("[6/6] Assembling output")

        # Filter record fields using strict allowlist (excludes sensitive fields like "comment")
        filtered_fields = filter_record_fields(record)

        # Determine primary router action (prefer P5b if available)
        primary_router_action = None
        if uq_info_p5b:
            primary_router_action = uq_info_p5b.get('router_action_p5b')
        elif uq_info:
            primary_router_action = uq_info.get('router_action')

        output = {
            "id": args.id,
            "inchikey": inchikey,
            "canonical_smiles": smiles,
            "primary_router_action": primary_router_action,  # P5b preferred
            "record_fields": filtered_fields,
            "missing_summary": missing_summary,
            "atb_status": atb_status,
            "atb_features": atb_features,
            "uq_scores": uq_info,
            "uq_scores_p5b": uq_info_p5b,
            "paths": {
                "cache_dir": cache_summary["cache_path"],
                "status_file": cache_summary["status_file"],
                "features_file": cache_summary.get("features_file"),
                "report_path": f"reports/{args.id}.json"
            }
        }

        # Write to report file if requested
        if args.write_report:
            reports_dir = Path("reports")
            reports_dir.mkdir(exist_ok=True)
            report_path = reports_dir / f"{args.id}.json"

            with open(report_path, "w") as f:
                json.dump(output, f, indent=2)

            logger.info(f"Wrote report to {report_path}")

        # Print to stdout
        print(json.dumps(output, indent=2))

    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to run orchestration: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


# ============================================================================
# Online UQ for arbitrary SMILES (uq --smiles)
# ============================================================================

def canonicalize_smiles(smiles: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Canonicalize SMILES using RDKit and compute InChIKey.

    Returns:
        (canonical_smiles, inchikey) or (None, None) if invalid
    """
    try:
        from rdkit import Chem
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return None, None
        canonical = Chem.MolToSmiles(mol, canonical=True)
        inchikey = Chem.MolToInchiKey(mol)
        return canonical, inchikey
    except Exception:
        return None, None


def compute_ecfp_fingerprint(smiles: str) -> Optional[np.ndarray]:
    """
    Compute ECFP4 fingerprint (2048 bits) for a SMILES.

    Returns:
        np.ndarray of shape (2048,) or None if invalid
    """
    try:
        from rdkit import Chem
        from rdkit.Chem import rdFingerprintGenerator

        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return None

        generator = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)
        fp = generator.GetFingerprintAsNumPy(mol)
        return fp.astype(np.int8)
    except Exception:
        return None


def to_binary_fingerprint(fp: np.ndarray) -> np.ndarray:
    """Coerce fingerprint to binary (0/1) uint8 array."""
    fp_array = np.asarray(fp)
    return (fp_array > 0).astype(np.uint8)


def tanimoto_similarity(fp1: np.ndarray, fp2: np.ndarray) -> float:
    """Compute Tanimoto similarity between two binary fingerprints."""
    fp1_bin = to_binary_fingerprint(fp1)
    fp2_bin = to_binary_fingerprint(fp2)

    intersection = np.sum(np.logical_and(fp1_bin, fp2_bin))
    union = np.sum(np.logical_or(fp1_bin, fp2_bin))

    if union == 0:
        return 0.0
    return float(intersection / union)


def compute_softmax_weights(similarities: np.ndarray, beta: float = 10.0) -> np.ndarray:
    """Compute softmax weights from similarities."""
    scaled = beta * similarities
    # Numerically stable softmax
    scaled = scaled - np.max(scaled)
    exp_scaled = np.exp(scaled)
    return exp_scaled / np.sum(exp_scaled)


def compute_mechanism_entropy_online(
    neighbor_labels: List[str],
    neighbor_sims: np.ndarray,
    beta: float = 10.0,
    exclude_labels: Optional[List[str]] = None
) -> Dict:
    """
    Compute mechanism_entropy for neighbors.

    Args:
        neighbor_labels: List of mechanism labels for each neighbor
        neighbor_sims: Array of similarities for each neighbor
        beta: Softmax temperature (higher = more weight on high-similarity neighbors)
        exclude_labels: Labels to exclude from entropy calculation (default: ["other", "unknown"])

    Returns:
        Dict with mechanism_entropy, M_eff, top_label, n_excluded_neighbors
    """
    if exclude_labels is None:
        exclude_labels = ["other", "unknown"]

    if len(neighbor_labels) == 0:
        return {
            'mechanism_entropy': None,
            'M_eff': 0,
            'top_label': None,
            'n_excluded_neighbors': 0,
            'excluded_labels': exclude_labels
        }

    weights = compute_softmax_weights(neighbor_sims, beta)
    n_excluded = sum(1 for label in neighbor_labels if label in exclude_labels)

    # Aggregate weights by label, excluding specified labels
    label_weights = {}
    for label, weight in zip(neighbor_labels, weights):
        if label not in exclude_labels:
            label_weights[label] = label_weights.get(label, 0.0) + weight

    # If all neighbors are excluded, return None entropy
    if len(label_weights) == 0:
        # Find top label from ALL neighbors (including excluded) for reference
        all_label_weights = {}
        for label, weight in zip(neighbor_labels, weights):
            all_label_weights[label] = all_label_weights.get(label, 0.0) + weight
        all_labels = list(all_label_weights.keys())
        all_probs = np.array([all_label_weights[l] for l in all_labels])
        top_label = all_labels[np.argmax(all_probs)] if all_labels else None

        return {
            'mechanism_entropy': None,
            'M_eff': 0,
            'top_label': top_label,
            'n_excluded_neighbors': int(n_excluded),
            'excluded_labels': exclude_labels,
            'note': 'All neighbors have excluded labels (other/unknown)'
        }

    # Re-normalize weights after exclusion
    labels = list(label_weights.keys())
    raw_probs = np.array([label_weights[label] for label in labels])
    probs = raw_probs / np.sum(raw_probs)  # Re-normalize to sum to 1

    M_eff = len(labels)

    if M_eff <= 1:
        mechanism_entropy = 0.0
    else:
        # Compute normalized entropy
        probs_nonzero = probs[probs > 0]
        H = -np.sum(probs_nonzero * np.log(probs_nonzero))
        mechanism_entropy = H / np.log(M_eff)

    top_idx = np.argmax(probs)
    top_label = labels[top_idx]

    return {
        'mechanism_entropy': float(mechanism_entropy),
        'M_eff': int(M_eff),
        'top_label': top_label,
        'n_excluded_neighbors': int(n_excluded),
        'excluded_labels': exclude_labels
    }


def search_neighbors(
    query_fp: np.ndarray,
    query_inchikey: Optional[str],
    rdkit_df: pd.DataFrame,
    k: int = 10
) -> List[Dict]:
    """
    Search for top-k neighbors by Tanimoto similarity.

    Args:
        query_fp: Query fingerprint
        query_inchikey: Query InChIKey (to exclude self)
        rdkit_df: DataFrame with inchikey and ecfp_2048 columns
        k: Number of neighbors

    Returns:
        List of {inchikey, sim} sorted by similarity descending
    """
    similarities = []

    for _, row in rdkit_df.iterrows():
        neighbor_ik = row['inchikey']

        # Skip self
        if query_inchikey and neighbor_ik == query_inchikey:
            continue

        neighbor_fp = row['ecfp_2048']
        if neighbor_fp is None:
            continue

        sim = tanimoto_similarity(query_fp, neighbor_fp)
        similarities.append({'inchikey': neighbor_ik, 'sim': sim})

    # Sort by similarity descending
    similarities.sort(key=lambda x: x['sim'], reverse=True)

    return similarities[:k]


def uq_command(args):
    """Online UQ for arbitrary SMILES."""
    notes = []

    # Check for empty SMILES
    if not args.smiles or args.smiles.strip() == "":
        error_output = {
            "error": "Empty SMILES provided",
            "input_smiles": args.smiles,
            "query": {"canonical_smiles": None, "inchikey": None}
        }
        print(json.dumps(error_output, indent=2))
        sys.exit(1)

    # Step 1: Canonicalize SMILES
    canonical_smiles, inchikey = canonicalize_smiles(args.smiles)

    if canonical_smiles is None or canonical_smiles == "":
        error_output = {
            "error": "Invalid SMILES",
            "input_smiles": args.smiles,
            "query": {"canonical_smiles": None, "inchikey": None}
        }
        print(json.dumps(error_output, indent=2))
        sys.exit(1)

    # Step 2: Compute ECFP fingerprint
    query_fp = compute_ecfp_fingerprint(canonical_smiles)
    if query_fp is None:
        error_output = {
            "error": "Failed to compute ECFP fingerprint",
            "input_smiles": args.smiles,
            "query": {"canonical_smiles": canonical_smiles, "inchikey": inchikey}
        }
        print(json.dumps(error_output, indent=2))
        sys.exit(1)

    # Step 3: Load reference fingerprints
    rdkit_path = Path(args.data_dir) / "rdkit_features.parquet"
    if not rdkit_path.exists():
        error_output = {
            "error": f"Reference fingerprints not found: {rdkit_path}",
            "query": {"canonical_smiles": canonical_smiles, "inchikey": inchikey}
        }
        print(json.dumps(error_output, indent=2))
        sys.exit(1)

    rdkit_df = pd.read_parquet(rdkit_path)
    # Filter out empty inchikeys
    rdkit_df = rdkit_df[rdkit_df['inchikey'].notna() & (rdkit_df['inchikey'] != '')]

    # Step 4: Search neighbors
    neighbors = search_neighbors(query_fp, inchikey, rdkit_df, k=args.k)

    if len(neighbors) == 0:
        error_output = {
            "error": "No neighbors found",
            "query": {"canonical_smiles": canonical_smiles, "inchikey": inchikey}
        }
        print(json.dumps(error_output, indent=2))
        sys.exit(1)

    # Step 5: Load mechanism labels
    label_map_path = Path(args.data_dir) / "mechanism_label_map.parquet"
    neighbor_labels = []

    if label_map_path.exists():
        label_map_df = pd.read_parquet(label_map_path)
        label_dict = dict(zip(label_map_df['inchikey'], label_map_df['mechanism_label']))

        for n in neighbors:
            label = label_dict.get(n['inchikey'], 'unknown')
            n['mechanism_label'] = label
            neighbor_labels.append(label)
    else:
        notes.append("mechanism_label_map.parquet not found, labels set to unknown")
        for n in neighbors:
            n['mechanism_label'] = 'unknown'
            neighbor_labels.append('unknown')

    # Step 6: Compute UQ scores
    neighbor_sims = np.array([n['sim'] for n in neighbors])

    # C_sim = mean of top-k similarities
    C_sim = float(np.mean(neighbor_sims))

    # C_meta = 0.0 for SMILES-only queries (no experimental evidence)
    C_meta = 0.0
    missing_fields = ["emission_solid", "emission_aggr"]

    # coverage = 0.7*C_sim + 0.3*C_meta
    coverage = 0.7 * C_sim + 0.3 * C_meta

    # novelty: use top1_sim
    top1_sim = neighbor_sims[0] if len(neighbor_sims) > 0 else 0.0
    novelty_raw = 1.0 - top1_sim

    # Load thresholds from manifest or use defaults
    manifest_path = Path(args.data_dir) / "uq_manifest_pre_atb_p5b.json"
    thresholds = {}
    beta = 10.0

    if manifest_path.exists():
        with open(manifest_path) as f:
            manifest = json.load(f)
        thresholds = manifest.get('thresholds', {})
        notes.append(f"Loaded thresholds from {manifest_path.name}")
    else:
        # Try to compute from existing data
        uq_path = Path(args.data_dir) / "uq_scores_pre_atb_p5b.parquet"
        if uq_path.exists():
            uq_df = pd.read_parquet(uq_path)
            valid = uq_df[uq_df['C_sim'].notna()]
            thresholds = {
                'cov_low': float(valid['coverage'].quantile(0.20)),
                'cov_high': float(valid['coverage'].quantile(0.80)),
                'nov_high': float(valid['novelty'].quantile(0.80)),
                'mech_ent_high': 0.8  # default
            }
            notes.append("Computed thresholds from uq_scores_pre_atb_p5b.parquet")
        else:
            # Fallback defaults
            thresholds = {
                'cov_low': 0.4,
                'cov_high': 0.6,
                'nov_high': 0.7,
                'mech_ent_high': 0.8
            }
            notes.append("Using default thresholds (manifest not found)")

    # Percentile-normalize novelty using p05/p95 from neighbors file
    neighbors_path = Path(args.data_dir) / "anchor_neighbors_ecfp.parquet"
    p05, p95 = 0.0, 1.0  # defaults

    if neighbors_path.exists():
        neighbors_df = pd.read_parquet(neighbors_path)
        top1_df = neighbors_df[neighbors_df['rank'] == 1]
        novelty_raw_all = 1.0 - top1_df['tanimoto_sim']
        p05 = float(novelty_raw_all.quantile(0.05))
        p95 = float(novelty_raw_all.quantile(0.95))

    if p95 > p05:
        novelty = float(np.clip((novelty_raw - p05) / (p95 - p05), 0, 1))
    else:
        novelty = novelty_raw

    # Compute mechanism_entropy
    mech_result = compute_mechanism_entropy_online(neighbor_labels, neighbor_sims, beta)
    mechanism_entropy = mech_result['mechanism_entropy']

    # Router action (P5b policy)
    cov_low = thresholds.get('cov_low', 0.4)
    cov_high = thresholds.get('cov_high', 0.6)
    nov_high = thresholds.get('nov_high', 0.7)
    mech_ent_high = thresholds.get('mech_ent_high', 0.8)

    if coverage < cov_low:
        router_action = "Evidence-insufficient"
    elif novelty >= nov_high and (coverage < cov_high or (mechanism_entropy is not None and mechanism_entropy >= mech_ent_high)):
        router_action = "Novelty-candidate"
    elif mechanism_entropy is not None and mechanism_entropy >= mech_ent_high:
        router_action = "In-domain ambiguous"
    else:
        router_action = "Known/Stable"

    # recommended_next_steps
    recommended_next_steps = []
    if router_action == "Evidence-insufficient":
        recommended_next_steps = ["check_smiles_validity", "collect_experimental_data"] + missing_fields[:5]
    elif router_action == "Novelty-candidate":
        recommended_next_steps = ["manual_review", "request_atb_compute_on_linux", "literature_search"]
    elif router_action == "In-domain ambiguous":
        recommended_next_steps = ["compare_with_neighbors", "check_mechanism_label_consistency"]

    # Build output
    output = {
        "query": {
            "canonical_smiles": canonical_smiles,
            "inchikey": inchikey
        },
        "neighbors": neighbors,
        "uq": {
            "C_sim": round(C_sim, 4),
            "C_meta": C_meta,
            "coverage": round(coverage, 4),
            "novelty": round(novelty, 4),
            "novelty_raw": round(novelty_raw, 4),
            "top1_sim": round(top1_sim, 4),
            "mechanism_entropy": round(mechanism_entropy, 4) if mechanism_entropy is not None else None,
            "M_eff": mech_result['M_eff'],
            "top_label": mech_result['top_label'],
            "router_action_p5b": router_action,
            "recommended_next_steps_p5b": recommended_next_steps
        },
        "diagnostics": {
            "used_thresholds": {
                "cov_low": round(cov_low, 4),
                "cov_high": round(cov_high, 4),
                "nov_high": round(nov_high, 4),
                "mech_ent_high": round(mech_ent_high, 4)
            },
            "novelty_percentiles": {"p05": round(p05, 4), "p95": round(p95, 4)},
            "used_beta": beta,
            "k": args.k,
            "missing_fields": missing_fields,
            "notes": notes
        }
    }

    print(json.dumps(output, indent=2))


# ============================================================================
# P6a Report Generation (report --id)
# ============================================================================

# ============================================================================
# Case File Commands (SMILES-first workflow)
# ============================================================================

def case_command(args):
    """Create a Case File from SMILES."""
    try:
        from src.cases.create_case_from_smiles import create_case_from_smiles

        logger.info(f"Creating case from SMILES: {args.smiles[:50]}...")

        case = create_case_from_smiles(
            smiles=args.smiles,
            k=args.k,
            outdir=args.outdir
        )

        case_path = Path(args.outdir) / f"{case['case_id']}.json"
        ready_result = _run_ready_agent_on_case(case_path)
        case = json.loads(case_path.read_text(encoding="utf-8"))

        # Summary output
        print(f"Case created: {case_path}")
        print(f"  case_id: {case['case_id']}")
        print(f"  inchikey: {case['query']['inchikey']}")
        print(f"  top1_sim: {case['risk_scores']['top1_sim']}")
        print(f"  mechanism_hint: {case['risk_scores']['mechanism_hint']}")
        print(f"  cache_status: {case['evidence_readiness']['atb']['cache_status']}")
        print(f"  request_status: {case['evidence_readiness']['atb']['request_status']}")
        
        # V0.7 fields
        atb = case['evidence_readiness']['atb']
        er = case['evidence_readiness']
        if atb.get('features_summary'):
            print(f"  features_summary: present ({len(atb['features_summary'])} fields)")
        else:
            print(f"  features_summary: absent")
        # Neighbor metrics are at evidence_readiness top-level (not under atb)
        print(f"  neighbor_atb_success_rate: {er.get('neighbor_atb_success_rate')}")
        print(f"  neighbor_atb_keyfield_rate: {er.get('neighbor_atb_keyfield_rate')}")
        
        candidates = case.get('candidate_mechanisms', [])
        if candidates:
            top_mech = candidates[0]
            print(f"  top_candidate: {top_mech['label']} (prob={top_mech['prob']:.3f})")
        
        print(f"  ready_for_reasoning: {case['evidence_readiness']['current_gate']['ready_for_reasoning']}")
        print(f"  action_plan: {case['action_plan']}")
        print(f"  ready_agent_gate: {ready_result['current_gate']}")

        # Print full JSON if requested
        if args.print_json:
            print("\nFull case JSON:")
            print(json.dumps(case, indent=2))

    except ValueError as e:
        logger.error(f"Invalid input: {e}")
        sys.exit(1)
    except FileNotFoundError as e:
        logger.error(f"Required data file not found: {e}")
        logger.error("Ensure data/rdkit_features.parquet and data/mechanism_label_map.parquet exist")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to create case: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def case_update_command(args):
    """Update a Case File with an action (Chem Agent stub)."""
    try:
        from src.cases.chem_agent_update_case_stub import update_case_file

        if not Path(args.case).exists():
            logger.error(f"Case file not found: {args.case}")
            sys.exit(1)

        logger.info(f"Updating case: {args.case}")
        logger.info(f"Action: {args.action}")

        case = update_case_file(args.case, args.action)
        case_path = Path(args.case)
        ready_result = _run_ready_agent_on_case(case_path)
        case = json.loads(case_path.read_text(encoding="utf-8"))

        # Summary output
        atb = case['evidence_readiness']['atb']
        # Support both new (cache_status) and legacy (status) schema for display
        cache_status = atb.get('cache_status') or atb.get('status', 'unknown')
        request_status = atb.get('request_status', 'N/A')
        print(f"\nUpdated state:")
        print(f"  atb.cache_status: {cache_status}")
        print(f"  atb.request_status: {request_status}")
        print(f"  literature.status: {case['evidence_readiness']['literature']['status']}")
        print(f"  experiment.status: {case['evidence_readiness']['experiment']['status']}")
        print(f"  ready_for_reasoning: {case['evidence_readiness']['current_gate']['ready_for_reasoning']}")
        print(f"  reason: {case['evidence_readiness']['current_gate']['reason']}")
        print(f"  action_plan: {case.get('action_plan', [])}")
        print(f"  history events: {len(case['history'])}")
        print(f"  ready_agent_gate: {ready_result['current_gate']}")

        # Print full JSON if requested
        if args.print_json:
            print("\nFull case JSON:")
            print(json.dumps(case, indent=2))

    except ValueError as e:
        logger.error(f"Invalid case file: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to update case: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def case_e0_command(args):
    """Compatibility alias: case-e0 -> case-run (offline_pdf lane)."""
    try:
        if not Path(args.case).exists():
            logger.error(f"Case file not found: {args.case}")
            sys.exit(1)
        logger.warning("DEPRECATED: case-e0 is an alias. Please use case-run.")
        case_obj = json.loads(Path(args.case).read_text(encoding="utf-8"))
        smiles = str((case_obj.get("query") or {}).get("input_smiles") or "").strip()
        if not smiles:
            raise ValueError("case-e0 alias requires query.input_smiles in --case file")
        offline_pdf = None
        if args.offline_pdf:
            offline_pdf = args.offline_pdf[0]
        ns = _build_case_run_namespace(
            smiles=smiles,
            code=(case_obj.get("query") or {}).get("code"),
            offline_pdf=offline_pdf,
            run_lane="offline_pdf",
            artifacts_dir=args.artifacts_dir,
            outdir=str(Path(args.case).parent),
            base_url=args.llm_base_url,
            model=args.llm_model,
            llm_api_key_env=args.llm_api_key_env,
            llm_max_output_tokens=args.llm_max_output_tokens,
            llm_reasoning_effort=args.llm_reasoning_effort,
            llm_temperature=getattr(args, "llm_temperature", 0.2),
            mineru_bin=args.mineru_bin,
            mineru_output_root=args.mineru_output_root,
            mineru_backend=args.mineru_backend,
            mineru_method=args.mineru_method,
            mineru_lang=args.mineru_lang,
            mineru_start_page=args.mineru_start_page,
            mineru_end_page=args.mineru_end_page,
            mineru_timeout_sec=args.mineru_timeout_sec,
            force=bool(args.force),
        )
        summary = _run_case_run(ns)
        print(json.dumps(summary, indent=2))
    except ValueError as e:
        logger.error(f"Invalid input: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to run case-e0: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def _load_smiles_from_test_csv(test_csv_path: Path, code: str, smiles_col: str = "SMILES") -> Dict[str, str]:
    if not test_csv_path.exists():
        raise FileNotFoundError(f"test csv not found: {test_csv_path}")

    with test_csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if str(row.get("code", "")).strip() == str(code).strip():
                smiles = str(row.get(smiles_col, "")).strip()
                if not smiles:
                    raise ValueError(f"row found but empty {smiles_col} for code={code}")
                return row
    raise ValueError(f"code not found in {test_csv_path}: {code}")


def _build_case_run_namespace(**overrides) -> argparse.Namespace:
    defaults = {
        "test_csv": "data/test.csv",
        "row_index": None,
        "code": None,
        "smiles": None,
        "offline_pdf": None,
        "run_lane": "atb_cache_only",
        "output_layout": "case_centric",
        "retain_runs": 10,
        "output_timestamp_format": "utc_compact",
        "write_legacy_run_view": True,
        "emit_stage_snapshots": False,
        "stage_snapshots_dir": "cases/stage_snapshots",
        "artifacts_dir": "artifacts",
        "llm_response_dir": "artifacts/llm_responses",
        "outdir": "cases/multi_agent",
        "base_url": "http://35.220.164.252:3888/v1",
        "model": "gpt-5.2",
        "llm_api_key_env": "OPENAI_API_KEY",
        "llm_max_output_tokens": 1500,
        "llm_reasoning_effort": "medium",
        "llm_temperature": 0.2,
        "llm_use_json_schema": False,
        "mineru_bin": "third_party/MinerU/.venv/bin/mineru",
        "mineru_output_root": "third_party/MinerU/output",
        "mineru_backend": "hybrid-auto-engine",
        "mineru_method": None,
        "mineru_lang": None,
        "mineru_start_page": None,
        "mineru_end_page": None,
        "mineru_timeout_sec": 1200,
        "force": False,
        "neighbor_topk": 10,
        "reference_index_root": "data/reference_indices/split_levels_v2/views",
        "reference_view": "all_levels_full",
        "iterative": False,
        "round_runner_mode": "dryrun_then_commit",
        "max_rounds": 4,
        "round_start_profile": "R0",
        "pre_r2_failure_recovery_mode": "force_r2",
        "evaluator_use_llm": False,
        "evaluator_model": None,
        "evaluator_reasoning_effort": None,
        "evaluator_confidence_adjustment_enabled": False,
        "evaluator_confidence_adjustment_max_abs_delta": 0.05,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _run_case_run(namespace: argparse.Namespace) -> Dict[str, Any]:
    from src.orchestration.run_one import run_one

    return run_one(namespace)


def case_e2e_command(args):
    """Compatibility alias: case-e2e -> case-run (offline_pdf lane)."""
    try:
        logger.warning("DEPRECATED: case-e2e is an alias. Please use case-run.")
        ns = _build_case_run_namespace(
            test_csv=args.test_csv,
            code=args.code,
            smiles=args.smiles,
            offline_pdf=args.pdf,
            run_lane="offline_pdf",
            artifacts_dir=args.artifacts_dir,
            outdir=args.outdir,
            base_url=args.llm_base_url,
            model=args.llm_model,
            llm_api_key_env=args.llm_api_key_env,
            llm_max_output_tokens=args.llm_max_output_tokens,
            llm_reasoning_effort=args.llm_reasoning_effort,
            llm_temperature=getattr(args, "llm_temperature", 0.2),
            llm_use_json_schema=bool(getattr(args, "llm_use_json_schema", False)),
            mineru_bin=args.mineru_bin,
            mineru_output_root=args.mineru_output_root,
            mineru_backend=args.mineru_backend,
            mineru_method=args.mineru_method,
            mineru_lang=args.mineru_lang,
            mineru_start_page=args.mineru_start_page,
            mineru_end_page=args.mineru_end_page,
            mineru_timeout_sec=args.mineru_timeout_sec,
            force=bool(args.force),
        )
        summary = _run_case_run(ns)
        print(json.dumps(summary, indent=2, ensure_ascii=False))

    except ValueError as e:
        logger.error(f"Invalid input: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to run case-e2e: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def case_e2e_atb_command(args):
    """Compatibility alias: case-e2e-atb -> case-run (atb_cache_only lane)."""
    try:
        logger.warning("DEPRECATED: case-e2e-atb is an alias. Please use case-run.")
        ns = _build_case_run_namespace(
            test_csv=args.test_csv,
            code=args.code,
            smiles=args.smiles,
            run_lane="atb_cache_only",
            emit_stage_snapshots=True,
            stage_snapshots_dir=args.snapshots_dir,
            outdir=args.outdir,
        )
        summary = _run_case_run(ns)
        print(json.dumps(summary, indent=2, ensure_ascii=False))

    except ValueError as e:
        logger.error(f"Invalid input: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to run case-e2e-atb: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def case_run_command(args):
    """Official release command for multi-agent case execution."""
    try:
        ns = _build_case_run_namespace(
            test_csv=args.test_csv,
            row_index=args.row_index,
            code=args.code,
            smiles=args.smiles,
            offline_pdf=args.offline_pdf,
            run_lane=args.run_lane,
            output_layout=str(getattr(args, "output_layout", "case_centric")),
            retain_runs=int(getattr(args, "retain_runs", 10)),
            output_timestamp_format=str(getattr(args, "output_timestamp_format", "utc_compact")),
            write_legacy_run_view=bool(getattr(args, "write_legacy_run_view", True)),
            emit_stage_snapshots=bool(args.emit_stage_snapshots),
            stage_snapshots_dir=args.stage_snapshots_dir,
            artifacts_dir=args.artifacts_dir,
            llm_response_dir=getattr(args, "llm_response_dir", "artifacts/llm_responses"),
            outdir=args.outdir,
            base_url=args.base_url,
            model=args.model,
            llm_api_key_env=args.llm_api_key_env,
            llm_max_output_tokens=args.llm_max_output_tokens,
            llm_reasoning_effort=args.llm_reasoning_effort,
            mineru_bin=args.mineru_bin,
            mineru_output_root=args.mineru_output_root,
            mineru_backend=args.mineru_backend,
            mineru_method=args.mineru_method,
            mineru_lang=args.mineru_lang,
            mineru_start_page=args.mineru_start_page,
            mineru_end_page=args.mineru_end_page,
            mineru_timeout_sec=args.mineru_timeout_sec,
            force=bool(args.force),
            neighbor_topk=int(getattr(args, "neighbor_topk", 10)),
            reference_index_root=str(
                getattr(args, "reference_index_root", "data/reference_indices/split_levels_v2/views")
            ),
            reference_view=str(getattr(args, "reference_view", "all_levels_full")),
            iterative=bool(getattr(args, "iterative", False)),
            round_runner_mode=str(getattr(args, "round_runner_mode", "dryrun_then_commit")),
            max_rounds=int(getattr(args, "max_rounds", 4)),
            round_start_profile=str(getattr(args, "round_start_profile", "R0")),
            pre_r2_failure_recovery_mode=str(getattr(args, "pre_r2_failure_recovery_mode", "force_r2")),
            evaluator_use_llm=bool(getattr(args, "evaluator_use_llm", False)),
            evaluator_model=getattr(args, "evaluator_model", None),
            evaluator_reasoning_effort=getattr(args, "evaluator_reasoning_effort", None),
            evaluator_confidence_adjustment_enabled=bool(
                getattr(args, "evaluator_confidence_adjustment_enabled", False)
            ),
            evaluator_confidence_adjustment_max_abs_delta=float(
                getattr(args, "evaluator_confidence_adjustment_max_abs_delta", 0.05)
            ),
        )
        summary = _run_case_run(ns)
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    except Exception as e:
        logger.error(f"Failed to run case-run: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def ready_agent_command(args):
    """Run READY_AGENT on an existing case and rewrite only gate/rationale/plan."""
    try:
        from src.agents.ready_agent import review_case_and_patch, apply_ready_agent_patch

        case_path = Path(args.case)
        if not case_path.exists():
            raise FileNotFoundError(f"case not found: {case_path}")

        case_before = json.loads(case_path.read_text(encoding="utf-8"))
        patch_ops = review_case_and_patch(case_before)

        out = {
            "ok": True,
            "case_path": str(case_path),
            "dry_run": bool(args.dry_run),
            "patch_ops": patch_ops,
        }

        if not args.dry_run:
            case_after = apply_ready_agent_patch(case_before, patch_ops)
            case_path.write_text(json.dumps(case_after, indent=2, ensure_ascii=False), encoding="utf-8")
            out["current_gate"] = case_after.get("current_gate")

        print(json.dumps(out, indent=2, ensure_ascii=False))
    except Exception as e:
        logger.error(f"Failed to run ready-agent: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def eval_mechanism_benchmark_command(args):
    """Compare multi-agent vs zero-shot mechanism-label accuracy on test.csv."""
    try:
        from src.eval.evaluate_mechanism_benchmark import run_benchmark

        report = run_benchmark(args)
        if bool(getattr(args, "print_report", False)):
            print(json.dumps(report, indent=2, ensure_ascii=False))
    except Exception as e:
        logger.error(f"Failed to run eval-mechanism-benchmark: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def report_command(args):
    """Generate P6a pre-aTB report for a specific record."""
    try:
        from src.reports.generate_reports_pre_atb_p5b import (
            load_data, generate_report
        )

        # Load all required data
        logger.info("Loading data for report generation...")
        private_clean, uq_scores, neighbors, mechanism_labels, manifest = load_data()

        # Generate report
        logger.info(f"Generating report for id={args.id}")
        report = generate_report(
            args.id, private_clean, uq_scores, neighbors, mechanism_labels, manifest
        )

        if 'error' in report:
            logger.error(report['error'])
            sys.exit(1)

        # Write to file if requested
        if args.write:
            reports_dir = Path(args.output_dir)
            reports_dir.mkdir(parents=True, exist_ok=True)
            report_path = reports_dir / f"{args.id}.json"

            with open(report_path, 'w') as f:
                json.dump(report, f, indent=2)

            logger.info(f"Report written to {report_path}")

        # Print report to stdout
        print(json.dumps(report, indent=2))

    except FileNotFoundError as e:
        logger.error(f"Required data file not found: {e}")
        logger.error("Run the following first:")
        logger.error("  python -m src.uq.compute_uq_pre_atb_p5b")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to generate report: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Uncertainty-aware AIE Pipeline CLI (Mode A)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m src.cli fetch --id 1
  python -m src.cli compute-atb --id 1
  python -m src.cli run --id 1 --write-report
  python -m src.cli uq --smiles "c1ccccc1" --k 10
  python -m src.cli report --id 1 --write
  python -m src.cli case --smiles "c1ccccc1" --write
  python -m src.cli case-update --case cases/XXX.json --action compute_target_atb
        """
    )

    # Global arguments
    parser.add_argument(
        "--data-dir",
        default="data",
        help="Data directory (default: data)"
    )
    parser.add_argument(
        "--cache-dir",
        default="cache/atb",
        help="aTB cache directory (default: cache/atb)"
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # fetch command
    fetch_parser = subparsers.add_parser("fetch", help="Fetch record by id")
    fetch_parser.add_argument("--id", type=int, required=True, help="Record id")
    fetch_parser.add_argument("--json", action="store_true", help="Output full JSON")
    fetch_parser.set_defaults(func=fetch_command)

    # compute-atb command
    compute_parser = subparsers.add_parser("compute-atb", help="Check aTB cache and mark pending")
    compute_parser.add_argument("--id", type=int, required=True, help="Record id")
    compute_parser.set_defaults(func=compute_atb_command)

    # run command
    run_parser = subparsers.add_parser("run", help="Full orchestration (fetch + atb + report)")
    run_parser.add_argument("--id", type=int, required=True, help="Record id")
    run_parser.add_argument("--write-report", action="store_true", help="Write report to reports/{id}.json")
    run_parser.set_defaults(func=run_command)

    # uq command (online UQ for arbitrary SMILES)
    uq_parser = subparsers.add_parser("uq", help="Online UQ for arbitrary SMILES (pre-P6 test)")
    uq_parser.add_argument("--smiles", type=str, required=True, help="SMILES string")
    uq_parser.add_argument("--k", type=int, default=10, help="Number of neighbors (default: 10)")
    uq_parser.set_defaults(func=uq_command)

    # report command (P6a pre-aTB report generation)
    report_parser = subparsers.add_parser("report", help="Generate P6a pre-aTB report for a record")
    report_parser.add_argument("--id", type=int, required=True, help="Record id")
    report_parser.add_argument("--write", action="store_true", help="Write report to output directory")
    report_parser.add_argument("--output-dir", type=str, default="reports",
                               help="Output directory for reports (default: reports)")
    report_parser.set_defaults(func=report_command)

    # case command (Create Case File from SMILES)
    case_parser = subparsers.add_parser("case", help="Create Case File from SMILES (SMILES-first workflow)")
    case_parser.add_argument("--smiles", type=str, required=True, help="Input SMILES string")
    case_parser.add_argument("--k", type=int, default=10, help="Number of neighbors (default: 10)")
    case_parser.add_argument("--outdir", type=str, default="cases",
                             help="Output directory for case files (default: cases)")
    case_parser.add_argument("--print", action="store_true", dest="print_json",
                             help="Print full case JSON to stdout")
    case_parser.set_defaults(func=case_command)

    # case-update command (Update Case File with action)
    case_update_parser = subparsers.add_parser("case-update",
                                                help="Update Case File with action (Chem Agent stub)")
    case_update_parser.add_argument("--case", type=str, required=True,
                                    help="Path to case JSON file")
    case_update_parser.add_argument("--action", type=str, required=True,
                                    help="Action to perform (compute_target_atb, literature_search, "
                                         "request_min_experiment_emission, simulate_atb_success, etc.)")
    case_update_parser.add_argument("--print", action="store_true", dest="print_json",
                                    help="Print full case JSON to stdout")
    case_update_parser.set_defaults(func=case_update_command)

    # case-run command (official release runtime)
    case_run_parser = subparsers.add_parser(
        "case-run",
        help="Official release runtime: run multi-agent case loop (default lane: atb_cache_only)",
    )
    case_run_parser.add_argument("--test-csv", type=str, default="data/test.csv")
    case_run_parser.add_argument("--row-index", type=int, default=None)
    case_run_parser.add_argument("--code", type=str, default=None)
    case_run_parser.add_argument("--smiles", type=str, default=None)
    case_run_parser.add_argument("--offline-pdf", type=str, default=None)
    case_run_parser.add_argument(
        "--run-lane",
        type=str,
        default="atb_cache_only",
        choices=["atb_cache_only", "offline_pdf", "full"],
    )
    case_run_parser.add_argument(
        "--output-layout",
        type=str,
        default="case_centric",
        choices=["case_centric", "run_centric"],
    )
    case_run_parser.add_argument("--retain-runs", type=int, default=10)
    case_run_parser.add_argument(
        "--output-timestamp-format",
        type=str,
        default="utc_compact",
        choices=["utc_compact"],
    )
    case_run_parser.add_argument(
        "--write-legacy-run-view",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    case_run_parser.add_argument("--emit-stage-snapshots", action="store_true")
    case_run_parser.add_argument("--stage-snapshots-dir", type=str, default="cases/stage_snapshots")
    case_run_parser.add_argument("--artifacts-dir", type=str, default="artifacts")
    case_run_parser.add_argument("--llm-response-dir", type=str, default="artifacts/llm_responses")
    case_run_parser.add_argument("--outdir", type=str, default="cases/multi_agent")
    case_run_parser.add_argument("--base-url", type=str, default="http://35.220.164.252:3888/v1")
    case_run_parser.add_argument("--model", type=str, default="gpt-5.2")
    case_run_parser.add_argument("--llm-api-key-env", type=str, default="OPENAI_API_KEY")
    case_run_parser.add_argument("--llm-max-output-tokens", type=int, default=1500)
    case_run_parser.add_argument("--llm-reasoning-effort", type=str, default="medium")
    case_run_parser.add_argument("--llm-temperature", type=float, default=0.2)
    case_run_parser.add_argument("--llm-use-json-schema", action="store_true")
    case_run_parser.add_argument("--mineru-bin", type=str, default="third_party/MinerU/.venv/bin/mineru")
    case_run_parser.add_argument("--mineru-output-root", type=str, default="third_party/MinerU/output")
    case_run_parser.add_argument("--mineru-backend", type=str, default="hybrid-auto-engine")
    case_run_parser.add_argument("--mineru-method", type=str, default=None)
    case_run_parser.add_argument("--mineru-lang", type=str, default=None)
    case_run_parser.add_argument("--mineru-start-page", type=int, default=None)
    case_run_parser.add_argument("--mineru-end-page", type=int, default=None)
    case_run_parser.add_argument("--mineru-timeout-sec", type=int, default=1200)
    case_run_parser.add_argument("--force", action="store_true")
    case_run_parser.add_argument("--neighbor-topk", type=int, default=10)
    case_run_parser.add_argument(
        "--reference-index-root",
        type=str,
        default="data/reference_indices/split_levels_v2/views",
    )
    case_run_parser.add_argument(
        "--reference-view",
        type=str,
        default="all_levels_full",
        choices=["auto", "all_levels_full", "leave_level_1", "leave_level_2", "leave_level_3"],
    )
    case_run_parser.add_argument("--iterative", action="store_true", help="Enable iterative rounds (R0..R3) after setup agents.")
    case_run_parser.add_argument(
        "--round-runner-mode",
        type=str,
        default="dryrun_then_commit",
        choices=["dryrun_then_commit", "commit_all_rounds"],
    )
    case_run_parser.add_argument("--max-rounds", type=int, default=4)
    case_run_parser.add_argument("--round-start-profile", type=str, default="R0")
    case_run_parser.add_argument(
        "--pre-r2-failure-recovery-mode",
        type=str,
        default="force_r2",
        choices=["force_r2", "degraded_retry"],
    )
    case_run_parser.add_argument("--evaluator-use-llm", action="store_true")
    case_run_parser.add_argument("--evaluator-model", type=str, default=None)
    case_run_parser.add_argument("--evaluator-reasoning-effort", type=str, default=None)
    case_run_parser.add_argument(
        "--evaluator-confidence-adjustment-enabled",
        action=argparse.BooleanOptionalAction,
        default=False,
    )
    case_run_parser.add_argument("--evaluator-confidence-adjustment-max-abs-delta", type=float, default=0.05)
    case_run_parser.set_defaults(func=case_run_command)

    # case-e0 command (deprecated alias -> case-run)
    case_e0_parser = subparsers.add_parser(
        "case-e0",
        help="DEPRECATED alias to case-run (offline_pdf lane)",
    )
    case_e0_parser.add_argument("--case", type=str, required=True, help="Path to case JSON file")
    case_e0_parser.add_argument("--artifacts-dir", type=str, default="artifacts",
                                help="Artifacts root directory (default: artifacts)")
    case_e0_parser.add_argument(
        "--artifact-mode",
        type=str,
        default="final_case_only",
        choices=["full", "final_case_only"],
        help="Artifact persistence mode (default: final_case_only). 'final_case_only' skips run_id artifact JSON files.",
    )
    case_e0_parser.add_argument("--mode", type=str, default=None, help="Override mode (default from case)")
    case_e0_parser.add_argument("--offline-pdf", action="append", default=None,
                                help="Override offline PDF paths (repeatable)")
    case_e0_parser.add_argument("--force", action="store_true", help="Ignore idempotency key and rerun")
    case_e0_parser.add_argument(
        "--extractor-mode",
        type=str,
        default="sidecar_only",
        choices=["sidecar_only", "mineru_llm"],
        help="Extractor path (default: sidecar_only)",
    )
    case_e0_parser.add_argument("--extractor-name", type=str, default="mineru_offline_adapter")
    case_e0_parser.add_argument("--extractor-version", type=str, default="0.1.0")
    case_e0_parser.add_argument("--extractor-config-json", type=str, default="",
                                help="Extractor config JSON object")
    case_e0_parser.add_argument("--normalizer-config-json", type=str, default="",
                                help="Normalizer config JSON object")
    case_e0_parser.add_argument("--mapping-version", type=str, default="e0_v2")
    case_e0_parser.add_argument("--pdf-page-selection-json", type=str, default="",
                                help="PDF page selection JSON object")
    case_e0_parser.add_argument("--mineru-bin", type=str, default="third_party/MinerU/.venv/bin/mineru")
    case_e0_parser.add_argument("--mineru-output-root", type=str, default="third_party/MinerU/output")
    case_e0_parser.add_argument("--mineru-backend", type=str, default="hybrid-auto-engine")
    case_e0_parser.add_argument("--mineru-method", type=str, default=None)
    case_e0_parser.add_argument("--mineru-lang", type=str, default=None)
    case_e0_parser.add_argument("--mineru-start-page", type=int, default=None)
    case_e0_parser.add_argument("--mineru-end-page", type=int, default=None)
    case_e0_parser.add_argument("--mineru-timeout-sec", type=int, default=1200)
    case_e0_parser.add_argument("--llm-base-url", type=str, default="http://35.220.164.252:3888/v1")
    case_e0_parser.add_argument("--llm-model", type=str, default="deepseek-v3.2")
    case_e0_parser.add_argument("--llm-api-key-env", type=str, default="OPENAI_API_KEY")
    case_e0_parser.add_argument("--llm-max-output-tokens", type=int, default=1500)
    case_e0_parser.add_argument("--llm-reasoning-effort", type=str, default=None)
    case_e0_parser.add_argument("--llm-prompt-version", type=str, default="mineru_llm_prompt_v1")
    case_e0_parser.add_argument("--llm-schema-version", type=str, default="mineru_llm_candidates_v1")
    case_e0_parser.add_argument(
        "--writeback-evidence-table",
        action="store_true",
        help="Forbidden in E0; kept as hard-fail guard",
    )
    case_e0_parser.add_argument(
        "--evidence-table-path",
        type=str,
        default="data/evidence_table.parquet",
        help="Evidence table path for guard checks (default: data/evidence_table.parquet)",
    )
    case_e0_parser.set_defaults(func=case_e0_command)

    # case-e2e command (deprecated alias -> case-run)
    case_e2e_parser = subparsers.add_parser(
        "case-e2e",
        help="DEPRECATED alias to case-run (offline_pdf lane)",
    )
    case_e2e_parser.add_argument("--code", type=str, default=None, help="Molecule code to resolve from test.csv (e.g., DBA-AM)")
    case_e2e_parser.add_argument("--smiles", type=str, default=None, help="Direct SMILES input (bypass test.csv lookup)")
    case_e2e_parser.add_argument("--test-csv", type=str, default="data/test.csv", help="Test CSV for code lookup")
    case_e2e_parser.add_argument("--smiles-col", type=str, default="SMILES", help="SMILES column name in test CSV")
    case_e2e_parser.add_argument("--pdf", type=str, required=True, help="Offline PDF path for emission extraction")
    case_e2e_parser.add_argument("--k", type=int, default=10, help="Top-k neighbors for case creation")
    case_e2e_parser.add_argument("--outdir", type=str, default="cases/test_inputs", help="Case output directory")
    case_e2e_parser.add_argument("--artifacts-dir", type=str, default="artifacts/e2e", help="Artifacts output root")
    case_e2e_parser.add_argument(
        "--artifact-mode",
        type=str,
        default="final_case_only",
        choices=["full", "final_case_only"],
        help="Artifact persistence mode (default: final_case_only). 'final_case_only' keeps only the final case file + stable run log.",
    )
    case_e2e_parser.add_argument("--mode", type=str, default="offline_pdf", help="E0 mode override")
    case_e2e_parser.add_argument("--force", action="store_true", help="Ignore E0 idempotency key and rerun")
    case_e2e_parser.add_argument(
        "--extractor-mode",
        type=str,
        default="mineru_llm",
        choices=["sidecar_only", "mineru_llm"],
        help="E0 extractor mode (default: mineru_llm)",
    )
    case_e2e_parser.add_argument("--extractor-name", type=str, default="mineru_offline_adapter")
    case_e2e_parser.add_argument("--extractor-version", type=str, default="0.1.0")
    case_e2e_parser.add_argument("--extractor-config-json", type=str, default="", help="Extractor config JSON object")
    case_e2e_parser.add_argument("--normalizer-config-json", type=str, default="", help="Normalizer config JSON object")
    case_e2e_parser.add_argument("--mapping-version", type=str, default="e0_v2")
    case_e2e_parser.add_argument("--pdf-page-selection-json", type=str, default="", help="PDF page selection JSON object")
    case_e2e_parser.add_argument("--mineru-bin", type=str, default="third_party/MinerU/.venv/bin/mineru")
    case_e2e_parser.add_argument("--mineru-output-root", type=str, default="third_party/MinerU/output")
    case_e2e_parser.add_argument("--mineru-backend", type=str, default="hybrid-auto-engine")
    case_e2e_parser.add_argument("--mineru-method", type=str, default=None)
    case_e2e_parser.add_argument("--mineru-lang", type=str, default=None)
    case_e2e_parser.add_argument("--mineru-start-page", type=int, default=None)
    case_e2e_parser.add_argument("--mineru-end-page", type=int, default=None)
    case_e2e_parser.add_argument("--mineru-timeout-sec", type=int, default=1200)
    case_e2e_parser.add_argument("--llm-base-url", type=str, default="http://35.220.164.252:3888/v1")
    case_e2e_parser.add_argument("--llm-model", type=str, default="deepseek-v3.2")
    case_e2e_parser.add_argument("--llm-api-key-env", type=str, default="OPENAI_API_KEY")
    case_e2e_parser.add_argument("--llm-max-output-tokens", type=int, default=1500)
    case_e2e_parser.add_argument("--llm-reasoning-effort", type=str, default=None)
    case_e2e_parser.add_argument("--llm-prompt-version", type=str, default="mineru_llm_prompt_v1")
    case_e2e_parser.add_argument("--llm-schema-version", type=str, default="mineru_llm_candidates_v1")
    case_e2e_parser.add_argument(
        "--writeback-evidence-table",
        action="store_true",
        help="Forbidden in E0; kept as hard-fail guard",
    )
    case_e2e_parser.add_argument(
        "--evidence-table-path",
        type=str,
        default="data/evidence_table.parquet",
        help="Evidence table path for guard checks (default: data/evidence_table.parquet)",
    )
    case_e2e_parser.set_defaults(func=case_e2e_command)

    # case-e2e-atb command (deprecated alias -> case-run)
    case_e2e_atb_parser = subparsers.add_parser(
        "case-e2e-atb",
        help="DEPRECATED alias to case-run (atb_cache_only lane)",
    )
    case_e2e_atb_parser.add_argument("--code", type=str, default=None, help="Molecule code to resolve from test.csv (e.g., DBA-AM)")
    case_e2e_atb_parser.add_argument("--smiles", type=str, default=None, help="Direct SMILES input (bypass test.csv lookup)")
    case_e2e_atb_parser.add_argument("--test-csv", type=str, default="data/test.csv", help="Test CSV for code lookup")
    case_e2e_atb_parser.add_argument("--smiles-col", type=str, default="SMILES", help="SMILES column name in test CSV")
    case_e2e_atb_parser.add_argument("--k", type=int, default=10, help="Top-k neighbors for case creation")
    case_e2e_atb_parser.add_argument("--outdir", type=str, default="cases/test_inputs", help="Case output directory")
    case_e2e_atb_parser.add_argument(
        "--snapshots-dir",
        type=str,
        default="cases/stage_snapshots",
        help="Directory to store 3 snapshots (data/chem/ready)",
    )
    case_e2e_atb_parser.add_argument(
        "--require-atb-success",
        action="store_true",
        help="Fail if chem-stage cache_status is not success",
    )
    case_e2e_atb_parser.set_defaults(func=case_e2e_atb_command)

    # ready-agent command (gate/rationale/plan reviewer)
    ready_parser = subparsers.add_parser(
        "ready-agent",
        help="Run READY_AGENT over a case (writes only current_gate/action_rationale/action_plan)",
    )
    ready_parser.add_argument("--case", type=str, required=True, help="Path to case JSON file")
    ready_parser.add_argument("--dry-run", action="store_true", help="Print patch only; do not rewrite case")
    ready_parser.set_defaults(func=ready_agent_command)

    eval_benchmark_parser = subparsers.add_parser(
        "eval-mechanism-benchmark",
        help="Compare multi-agent and zero-shot mechanism-label accuracy on data/test.csv",
    )
    eval_benchmark_parser.add_argument("--test-csv", type=str, default="data/test.csv")
    eval_benchmark_parser.add_argument(
        "--protocol",
        type=str,
        default="compare",
        choices=["multi_agent", "zero_shot", "compare"],
    )
    eval_benchmark_parser.add_argument("--model", type=str, default="gpt-5.2")
    eval_benchmark_parser.add_argument("--base-url", type=str, default="http://35.220.164.252:3888/v1")
    eval_benchmark_parser.add_argument("--reasoning-effort", type=str, default="medium")
    eval_benchmark_parser.add_argument("--temperature", type=float, default=0.0)
    eval_benchmark_parser.add_argument("--llm-api-key-env", type=str, default="OPENAI_API_KEY")
    eval_benchmark_parser.add_argument("--llm-max-output-tokens", type=int, default=1500)
    eval_benchmark_parser.add_argument("--outdir", type=str, default="artifacts/eval_compare")
    eval_benchmark_parser.add_argument("--eval-id", type=str, default=None)
    eval_benchmark_parser.add_argument("--start-row", type=int, default=0)
    eval_benchmark_parser.add_argument("--max-rows", type=int, default=None)
    eval_benchmark_parser.add_argument("--force", action="store_true")
    eval_benchmark_parser.add_argument("--show-progress", action=argparse.BooleanOptionalAction, default=True)
    eval_benchmark_parser.add_argument(
        "--run-lane",
        type=str,
        default="atb_cache_only",
        choices=["atb_cache_only", "offline_pdf", "full"],
    )
    eval_benchmark_parser.add_argument("--iterative", action=argparse.BooleanOptionalAction, default=False)
    eval_benchmark_parser.add_argument("--max-rounds", type=int, default=4)
    eval_benchmark_parser.add_argument("--round-start-profile", type=str, default="R0")
    eval_benchmark_parser.add_argument("--neighbor-topk", type=int, default=10)
    eval_benchmark_parser.add_argument("--evaluator-use-llm", action=argparse.BooleanOptionalAction, default=False)
    eval_benchmark_parser.add_argument("--evaluator-model", type=str, default=None)
    eval_benchmark_parser.add_argument("--evaluator-reasoning-effort", type=str, default=None)
    eval_benchmark_parser.add_argument("--llm-use-json-schema", action="store_true")
    eval_benchmark_parser.add_argument(
        "--output-layout",
        type=str,
        default="case_centric",
        choices=["case_centric", "run_centric"],
    )
    eval_benchmark_parser.add_argument("--retain-runs", type=int, default=10)
    eval_benchmark_parser.add_argument(
        "--write-legacy-run-view",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    eval_benchmark_parser.add_argument(
        "--round-runner-mode",
        type=str,
        default="dryrun_then_commit",
        choices=["dryrun_then_commit", "commit_all_rounds"],
    )
    eval_benchmark_parser.add_argument("--print-report", action=argparse.BooleanOptionalAction, default=False)
    eval_benchmark_parser.set_defaults(func=eval_mechanism_benchmark_command)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Run command
    args.func(args)


if __name__ == "__main__":
    main()
