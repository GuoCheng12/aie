"""
src/cases/create_case_from_smiles.py

Create a Case File from a SMILES string (Data Agent).

This module computes risk_scores via ECFP neighbor search, initializes
evidence_readiness placeholders, builds the initial action_plan, and
writes the case file.

CLI:
    python -m src.cases.create_case_from_smiles --smiles "<SMILES>" --k 10 --outdir cases
"""

import argparse
import json
import uuid
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.cases.atb_neighbor_consistency import compute_atb_neighbor_consistency
from src.cases.case_schema import (
    CASE_VERSION,
    KEY_ATB_FIELDS,
    AtbCacheStatus,
    AtbRequestStatus,
    Actor,
    EventType,
    now_iso,
    create_empty_evidence_readiness,
    create_history_event,
    validate_case_file,
    evaluate_gate,
)
from src.chem.atb_cache import (
    get_atb_cache_status,
    get_atb_features_summary as _get_atb_features_summary,
    get_atb_evidence_pack,
)


# =============================================================================
# SMILES Processing
# =============================================================================

def canonicalize_smiles(smiles: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Canonicalize SMILES and compute InChIKey.

    Returns:
        (canonical_smiles, inchikey) or (None, None) if invalid
    """
    try:
        from rdkit import Chem
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return None, None
        canonical = Chem.MolToSmiles(mol, isomericSmiles=True)
        inchikey = Chem.MolToInchiKey(mol)
        return canonical, inchikey
    except Exception:
        return None, None


def compute_ecfp(smiles: str) -> Optional[np.ndarray]:
    """Compute ECFP4 fingerprint (2048 bits) from SMILES."""
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return None
        fp = AllChem.GetMorganFingerprintAsBitVect(mol, 2, nBits=2048)
        return np.array(fp, dtype=np.uint8)
    except Exception:
        return None


# =============================================================================
# Neighbor Search
# =============================================================================

def tanimoto_similarity(fp1: np.ndarray, fp2: np.ndarray) -> float:
    """Compute Tanimoto similarity between two binary fingerprints."""
    fp1_bool = fp1.astype(bool)
    fp2_bool = fp2.astype(bool)
    intersection = np.sum(fp1_bool & fp2_bool)
    union = np.sum(fp1_bool | fp2_bool)
    if union == 0:
        return 0.0
    return float(intersection / union)


def search_neighbors(
    query_fp: np.ndarray,
    query_inchikey: Optional[str],
    rdkit_df: pd.DataFrame,
    label_map: pd.DataFrame,
    k: int = 10
) -> List[Dict[str, Any]]:
    """
    Search for top-k neighbors by Tanimoto similarity.

    Returns:
        List of {rank, neighbor_inchikey, sim, neighbor_mechanism_label}
    """
    # Build label lookup
    label_dict = dict(zip(label_map['inchikey'], label_map['mechanism_label']))

    results = []
    for _, row in rdkit_df.iterrows():
        neighbor_ik = row['inchikey']

        # Skip self
        if query_inchikey and neighbor_ik == query_inchikey:
            continue

        # Get fingerprint
        fp = np.array(row['ecfp_2048'], dtype=np.uint8)
        sim = tanimoto_similarity(query_fp, fp)

        label = label_dict.get(neighbor_ik, 'unknown')
        results.append({
            'neighbor_inchikey': neighbor_ik,
            'sim': sim,
            'neighbor_mechanism_label': label
        })

    # Sort by similarity descending
    results.sort(key=lambda x: x['sim'], reverse=True)

    # Take top-k and add rank
    top_k = results[:k]
    for i, r in enumerate(top_k):
        r['rank'] = i + 1

    return top_k


# =============================================================================
# Risk Scores Computation
# =============================================================================

def compute_softmax_weights(sims: np.ndarray, beta: float = 10.0) -> np.ndarray:
    """Compute softmax weights from similarities."""
    scaled = beta * sims
    scaled = scaled - np.max(scaled)
    exp_scaled = np.exp(scaled)
    return exp_scaled / np.sum(exp_scaled)


def compute_mechanism_entropy(
    neighbors: List[Dict[str, Any]],
    exclude_labels: List[str] = None,
    beta: float = 10.0
) -> Tuple[Optional[float], str, float]:
    """
    Compute mechanism entropy from neighbor labels.

    Returns:
        (entropy, top_label, top_label_prob)
    """
    if exclude_labels is None:
        exclude_labels = ["other", "unknown"]

    if not neighbors:
        return None, "unknown", 0.0

    sims = np.array([n['sim'] for n in neighbors])
    labels = [n['neighbor_mechanism_label'] for n in neighbors]
    weights = compute_softmax_weights(sims, beta)

    # Aggregate weights by label, excluding specified labels
    label_weights = {}
    for label, weight in zip(labels, weights):
        if label not in exclude_labels:
            label_weights[label] = label_weights.get(label, 0.0) + weight

    # If all neighbors are excluded
    if len(label_weights) == 0:
        # Find top from all
        all_weights = {}
        for label, weight in zip(labels, weights):
            all_weights[label] = all_weights.get(label, 0.0) + weight
        if all_weights:
            top_label = max(all_weights, key=all_weights.get)
            return None, top_label, all_weights[top_label]
        return None, "unknown", 0.0

    # Re-normalize
    label_list = list(label_weights.keys())
    raw_probs = np.array([label_weights[l] for l in label_list])
    probs = raw_probs / np.sum(raw_probs)

    M_eff = len(label_list)
    if M_eff <= 1:
        entropy = 0.0
    else:
        probs_nonzero = probs[probs > 0]
        H = -np.sum(probs_nonzero * np.log(probs_nonzero))
        entropy = float(H / np.log(M_eff))

    top_idx = np.argmax(probs)
    top_label = label_list[top_idx]
    top_prob = float(probs[top_idx])

    return entropy, top_label, top_prob


def compute_risk_scores(neighbors: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute all risk scores from neighbors."""
    if not neighbors:
        return {
            'top1_sim': 0.0,
            'mean_topk_sim': 0.0,
            'neighbor_gap': 0.0,
            'novelty_struct': 1.0,
            'mechanism_entropy': None,
            'mechanism_hint': 'unknown',
            'hint_confidence': 0.0
        }

    sims = [n['sim'] for n in neighbors]
    top1_sim = sims[0] if sims else 0.0
    top2_sim = sims[1] if len(sims) > 1 else 0.0
    mean_topk_sim = float(np.mean(sims))
    neighbor_gap = top1_sim - top2_sim
    novelty_struct = 1.0 - top1_sim

    entropy, hint, confidence = compute_mechanism_entropy(neighbors)

    return {
        'top1_sim': round(top1_sim, 6),
        'mean_topk_sim': round(mean_topk_sim, 6),
        'neighbor_gap': round(neighbor_gap, 6),
        'novelty_struct': round(novelty_struct, 6),
        'mechanism_entropy': round(entropy, 6) if entropy is not None else None,
        'mechanism_hint': hint,
        'hint_confidence': round(confidence, 6)
    }


# =============================================================================
# aTB Cache Check
# =============================================================================

def check_atb_cache_status(inchikey: Optional[str]) -> str:
    """Wrapper for cache_status lookup (single source of truth)."""
    return get_atb_cache_status(inchikey)


# =============================================================================
# aTB Features Summary (V0.7)
# =============================================================================

def get_atb_features_summary(inchikey: Optional[str]) -> Tuple[Optional[Dict], List[str]]:
    """Wrapper for features_summary (single source of truth)."""
    return _get_atb_features_summary(inchikey)


def get_neighbor_atb_evidence(neighbor_inchikey: str) -> Dict[str, Any]:
    """Wrapper for neighbor aTB evidence (single source of truth)."""
    return get_atb_evidence_pack(neighbor_inchikey)


def get_private_observation_summary(inchikey: Optional[str]) -> Dict[str, Any]:
    """
    Summarize train-only private observations for this inchikey.

    The facts table currently exposes emission_solid/emission_aggr only.
    """
    summary = {
        "matched_records": 0,
        "has_emission_solid": False,
        "has_emission_aggr": False,
        "has_emission": False,
    }
    if inchikey is None:
        return summary

    private_clean_path = Path("data/private_clean.parquet")
    if not private_clean_path.exists():
        return summary

    try:
        private_clean = pd.read_parquet(private_clean_path)
    except Exception:
        return summary

    if "inchikey" not in private_clean.columns:
        return summary

    matched = private_clean[private_clean["inchikey"] == inchikey]
    if matched.empty:
        return summary

    summary["matched_records"] = int(len(matched))

    if "emission_solid_missing" in matched.columns:
        has_emission_solid = bool((~matched["emission_solid_missing"].astype(bool)).any())
    else:
        has_emission_solid = bool(matched.get("emission_solid", pd.Series(dtype=float)).notna().any())

    if "emission_aggr_missing" in matched.columns:
        has_emission_aggr = bool((~matched["emission_aggr_missing"].astype(bool)).any())
    else:
        has_emission_aggr = bool(matched.get("emission_aggr", pd.Series(dtype=float)).notna().any())

    summary["has_emission_solid"] = has_emission_solid
    summary["has_emission_aggr"] = has_emission_aggr
    summary["has_emission"] = bool(has_emission_solid or has_emission_aggr)
    return summary


# =============================================================================
# Mechanism Signatures (domainRAG stub)
# =============================================================================

def load_mechanism_signatures() -> Dict[str, Any]:
    """Load mechanism signatures from domainRAG YAML file."""
    signatures_path = Path('data/domainrag/mechanism_signatures.yaml')
    if not signatures_path.exists():
        return {}
    
    try:
        import yaml
        with open(signatures_path, 'r') as f:
            return yaml.safe_load(f)
    except Exception:
        return {}


def compute_candidate_mechanisms(
    neighbors: List[Dict[str, Any]],
    beta: float = 10.0,
    top_n: int = 3
) -> List[Dict[str, Any]]:
    """
    Compute candidate mechanisms from neighbor label distribution.
    
    Uses similarity-weighted probability: w_j ∝ exp(beta * sim_j)
    
    Returns:
        List of {label, prob} for top-n candidates
    """
    if not neighbors:
        return [{'label': 'unknown', 'prob': 1.0}]
    
    sims = np.array([n['sim'] for n in neighbors])
    labels = [n['neighbor_mechanism_label'] for n in neighbors]
    
    # Compute softmax weights
    weights = compute_softmax_weights(sims, beta)
    
    # Aggregate by label
    label_weights = {}
    for label, weight in zip(labels, weights):
        label_weights[label] = label_weights.get(label, 0.0) + weight
    
    # Sort by weight descending
    sorted_labels = sorted(label_weights.items(), key=lambda x: x[1], reverse=True)
    
    # Take top-n
    candidates = []
    for label, prob in sorted_labels[:top_n]:
        candidates.append({
            'label': label,
            'prob': round(float(prob), 6)
        })
    
    return candidates if candidates else [{'label': 'unknown', 'prob': 1.0}]


def get_mechanism_signatures_for_candidates(
    candidates: List[Dict[str, Any]],
    all_signatures: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Get signature templates for candidate mechanisms.
    
    Returns:
        Map of label -> signature (excluding description)
    """
    signatures = {}
    unknown_sig = all_signatures.get('unknown', {})
    
    for c in candidates:
        label = c['label']
        # Handle underscore vs space in label names
        sig = all_signatures.get(label) or all_signatures.get(label.replace(' ', '_'))
        
        if sig:
            # Extract relevant fields only
            signatures[label] = {
                'required_atb_fields': sig.get('required_atb_fields', []),
                'required_experiment_fields': sig.get('required_experiment_fields', []),
                'disambiguation_actions': sig.get('disambiguation_actions', [])
            }
        else:
            # Fallback to unknown
            signatures[label] = {
                'required_atb_fields': unknown_sig.get('required_atb_fields', []),
                'required_experiment_fields': unknown_sig.get('required_experiment_fields', []),
                'disambiguation_actions': unknown_sig.get('disambiguation_actions', [])
            }
    
    return signatures


# =============================================================================
# Action Plan Builder (V0.7, LLM-friendly)
# =============================================================================

def _make_action(
    *,
    action: str,
    priority: int,
    blocking: bool,
    inputs: Optional[Dict[str, Any]] = None,
    expected_outputs: Optional[List[str]] = None,
    notes: str = "",
    status: str = "not_started",
) -> Dict[str, Any]:
    return {
        "action": action,
        "priority": int(priority),
        "status": status,
        "inputs": inputs or {},
        "expected_outputs": expected_outputs or [],
        "blocking": bool(blocking),
        "notes": notes,
    }


def build_llm_action_plan_v07(
    *,
    inchikey: Optional[str],
    canonical_smiles: Optional[str],
    cache_status: str,
    atb_missing_fields: List[str],
    atb_neighbor_flag: str,
    has_emission: bool,
    has_solvent: bool,
    retry_failed_atb: bool = True,
    aliases: Optional[List[str]] = None,
) -> Tuple[List[Dict[str, Any]], List[str], str]:
    """
    Build a structured, LLM-friendly action plan.

    Returns:
        (action_plan_objects, action_rationale, reasoning_mode)
    """
    aliases = aliases or []
    plan: List[Dict[str, Any]] = []
    rationale: List[str] = []

    # Determine reasoning_mode (ready/blocked is still driven by gate evaluation elsewhere).
    # Here we only encode behavior style for the reasoner.
    if cache_status == AtbCacheStatus.SUCCESS.value:
        reasoning_mode = "conservative" if atb_neighbor_flag == "outlier" else "normal"
    else:
        reasoning_mode = "conservative" if has_emission else "blocked"

    # Helper inputs shared by evidence actions
    common_inputs = {
        "inchikey": inchikey,
        "canonical_smiles": canonical_smiles,
        "aliases": aliases,
    }

    # Case A: aTB success (key fields complete) -> ready
    if cache_status == AtbCacheStatus.SUCCESS.value:
        if atb_neighbor_flag == "outlier":
            rationale.extend([
                "target aTB is available but micro-deltas are out-of-distribution vs structural neighborhood",
                "escalate external evidence to validate mechanism",
            ])
            plan.append(_make_action(
                action="run_master_reasoner",
                priority=1,
                blocking=False,
                inputs={**common_inputs, "reasoning_mode": "conservative"},
                expected_outputs=["reasoning_summary", "candidate_mechanisms_ranked"],
                notes="Proceed, but explicitly cite the aTB neighbor outlier risk and propose multiple plausible hypotheses.",
            ))
            plan.append(_make_action(
                action="literature_search_web",
                priority=2,
                blocking=False,
                inputs={**common_inputs, "query_strategy": "inchikey+aliases"},
                expected_outputs=["candidate_papers_json", "citations_if_available"],
                notes="Search by InChIKey + alias/common names. Citations/URLs may be unavailable depending on gateway policy.",
            ))
            plan.append(_make_action(
                action="mineru_extract_pdf",
                priority=3,
                blocking=False,
                inputs={**common_inputs, "from_action": "literature_search_web"},
                expected_outputs=["evidence_claim_rows", "extracted_tables"],
                notes="Extract emission/qy/tau/solvent/state tables from PDFs returned by literature_search_web.",
            ))
            if not has_emission:
                plan.append(_make_action(
                    action="request_min_experiment_emission",
                    priority=4,
                    blocking=False,
                    inputs={**common_inputs, "requested_fields": ["emission_solid", "emission_aggr"]},
                    expected_outputs=["emission_*"],
                    notes="Request minimal emission measurements to ground the mechanism hypotheses.",
                ))
            plan.append(_make_action(
                action="expand_structure_neighbors",
                priority=5,
                blocking=False,
                inputs={**common_inputs, "top_k": 50, "metric": "tanimoto_ecfp"},
                expected_outputs=["neighbors_expanded"],
                notes="Expand structure-only neighborhood for a broader baseline comparison (does not change the index).",
            ))
            return plan, rationale, reasoning_mode

        # Inlier/borderline -> normal reasoning
        rationale.append("target aTB is available and consistent with structural neighborhood baseline")
        plan.append(_make_action(
            action="run_master_reasoner",
            priority=1,
            blocking=False,
            inputs={**common_inputs, "reasoning_mode": "normal"},
            expected_outputs=["reasoning_summary", "candidate_mechanisms_ranked"],
            notes="Proceed with normal reasoning using target + neighbor evidence.",
        ))
        pr = 2
        if not has_emission:
            plan.append(_make_action(
                action="request_min_experiment_emission",
                priority=pr,
                blocking=False,
                inputs={**common_inputs, "requested_fields": ["emission_solid", "emission_aggr"]},
                expected_outputs=["emission_*"],
                notes="Optional follow-up: collect emission to validate predicted mechanism.",
            ))
            pr += 1
        if not has_solvent:
            plan.append(_make_action(
                action="request_experiment_solvent_details",
                priority=pr,
                blocking=False,
                inputs={**common_inputs},
                expected_outputs=["condition_metadata"],
                notes="Optional follow-up: collect solvent/condition details for proper condition matching.",
            ))
        return plan, rationale, reasoning_mode

    # Case B: absent/pending -> blocked unless emission exists
    if cache_status in {AtbCacheStatus.ABSENT.value, AtbCacheStatus.PENDING.value}:
        rationale.append("target aTB is missing and minimal experiment evidence is insufficient")
        plan.append(_make_action(
            action="compute_target_atb",
            priority=1,
            blocking=True,
            inputs={**common_inputs},
            expected_outputs=["cache/atb/.../status.json", "cache/atb/.../features.json"],
            notes="Run aTB (cached by InChIKey). This is blocking for reasoning when no emission is available.",
        ))
        plan.append(_make_action(
            action="literature_search_web",
            priority=2,
            blocking=False,
            inputs={**common_inputs, "query_strategy": "inchikey+aliases"},
            expected_outputs=["candidate_papers_json", "citations_if_available"],
            notes="Parallel non-blocking step: literature search for emission/qy/tau evidence.",
        ))
        plan.append(_make_action(
            action="request_min_experiment_emission",
            priority=3,
            blocking=False,
            inputs={**common_inputs, "requested_fields": ["emission_solid", "emission_aggr"]},
            expected_outputs=["emission_*"],
            notes="Parallel non-blocking step: request minimal emission if feasible.",
        ))
        return plan, rationale, reasoning_mode

    # Case C: failed
    if cache_status == AtbCacheStatus.FAILED.value:
        if has_emission:
            rationale.append("target aTB failed but emission evidence exists; proceed conservatively")
            plan.append(_make_action(
                action="run_master_reasoner",
                priority=1,
                blocking=False,
                inputs={**common_inputs, "reasoning_mode": "conservative", "atb_cache_status": "failed"},
                expected_outputs=["reasoning_summary", "candidate_mechanisms_ranked"],
                notes="Proceed, but be explicit that target aTB failed; rely more on experimental/literature evidence.",
            ))
            plan.append(_make_action(
                action="literature_search_web",
                priority=2,
                blocking=False,
                inputs={**common_inputs, "query_strategy": "inchikey+aliases"},
                expected_outputs=["candidate_papers_json", "citations_if_available"],
                notes="Collect external evidence to compensate for missing aTB.",
            ))
            plan.append(_make_action(
                action="mineru_extract_pdf",
                priority=3,
                blocking=False,
                inputs={**common_inputs, "from_action": "literature_search_web"},
                expected_outputs=["evidence_claim_rows", "extracted_tables"],
                notes="Extract emission/qy/tau/solvent/state evidence from PDFs.",
            ))
            plan.append(_make_action(
                action="request_experiment_solvent_details",
                priority=4,
                blocking=False,
                inputs={**common_inputs},
                expected_outputs=["condition_metadata"],
                notes="Optional: request condition details to improve evidence matching quality.",
            ))
            return plan, rationale, reasoning_mode

        rationale.append("target aTB failed and no emission evidence; cannot proceed to reasoning")
        first_action = "retry_target_atb_alt_settings" if retry_failed_atb else "compute_target_atb"
        plan.append(_make_action(
            action=first_action,
            priority=1,
            blocking=True,
            inputs={**common_inputs, "fail_policy": "retry" if retry_failed_atb else "no_retry"},
            expected_outputs=["cache/atb/.../status.json", "cache/atb/.../features.json"],
            notes="Blocking: retry aTB with alternate settings/conformers if enabled; otherwise compute aTB.",
        ))
        plan.append(_make_action(
            action="literature_search_web",
            priority=2,
            blocking=False,
            inputs={**common_inputs, "query_strategy": "inchikey+aliases"},
            expected_outputs=["candidate_papers_json", "citations_if_available"],
            notes="Non-blocking: literature search for emission evidence.",
        ))
        plan.append(_make_action(
            action="request_min_experiment_emission",
            priority=3,
            blocking=False,
            inputs={**common_inputs, "requested_fields": ["emission_solid", "emission_aggr"]},
            expected_outputs=["emission_*"],
            notes="Non-blocking: request minimal emission to open reasoning if aTB keeps failing.",
        ))
        return plan, rationale, reasoning_mode

    # Case D: partial (missing key fields)
    if cache_status == AtbCacheStatus.PARTIAL.value:
        if has_emission:
            rationale.append(f"target aTB is partial (missing_fields={atb_missing_fields}); proceed conservatively")
            plan.append(_make_action(
                action="run_master_reasoner",
                priority=1,
                blocking=False,
                inputs={**common_inputs, "reasoning_mode": "conservative", "atb_cache_status": "partial", "missing_fields": atb_missing_fields},
                expected_outputs=["reasoning_summary", "candidate_mechanisms_ranked"],
                notes="Proceed, but explicitly note missing aTB fields; rely more on experimental/literature evidence.",
            ))
            # High-priority follow-up to complete aTB (non-blocking because emission exists).
            plan.append(_make_action(
                action="retry_target_atb_alt_settings" if retry_failed_atb else "compute_target_atb",
                priority=2,
                blocking=False,
                inputs={**common_inputs, "missing_fields": atb_missing_fields},
                expected_outputs=["cache/atb/.../features.json (complete)"],
                notes="Follow-up: complete missing aTB delta fields for stronger mechanistic grounding.",
            ))
            plan.append(_make_action(
                action="literature_search_web",
                priority=3,
                blocking=False,
                inputs={**common_inputs, "query_strategy": "inchikey+aliases"},
                expected_outputs=["candidate_papers_json", "citations_if_available"],
                notes="Supplement with literature evidence to compensate for partial aTB.",
            ))
            return plan, rationale, reasoning_mode

        rationale.append(f"target aTB is partial (missing_fields={atb_missing_fields}) and no emission evidence; blocked")
        plan.append(_make_action(
            action="retry_target_atb_alt_settings" if retry_failed_atb else "compute_target_atb",
            priority=1,
            blocking=True,
            inputs={**common_inputs, "missing_fields": atb_missing_fields},
            expected_outputs=["cache/atb/.../features.json (complete)"],
            notes="Blocking: complete missing aTB delta fields to open reasoning when no emission is available.",
        ))
        plan.append(_make_action(
            action="literature_search_web",
            priority=2,
            blocking=False,
            inputs={**common_inputs, "query_strategy": "inchikey+aliases"},
            expected_outputs=["candidate_papers_json", "citations_if_available"],
            notes="Non-blocking: literature search for emission evidence.",
        ))
        plan.append(_make_action(
            action="request_min_experiment_emission",
            priority=3,
            blocking=False,
            inputs={**common_inputs, "requested_fields": ["emission_solid", "emission_aggr"]},
            expected_outputs=["emission_*"],
            notes="Non-blocking: request minimal emission to open reasoning if aTB remains partial.",
        ))
        return plan, rationale, reasoning_mode

    # Fallback
    rationale.append("unrecognized aTB status; defaulting to blocked evidence ladder")
    plan.append(_make_action(
        action="compute_target_atb",
        priority=1,
        blocking=True,
        inputs={**common_inputs},
        expected_outputs=["cache/atb/.../status.json", "cache/atb/.../features.json"],
        notes="Blocking: compute aTB to proceed.",
    ))
    plan.append(_make_action(
        action="literature_search_web",
        priority=2,
        blocking=False,
        inputs={**common_inputs, "query_strategy": "inchikey+aliases"},
        expected_outputs=["candidate_papers_json", "citations_if_available"],
        notes="Non-blocking: literature search.",
    ))
    return plan, rationale, reasoning_mode


# =============================================================================
# Main Case Creation
# =============================================================================

def create_case_from_smiles(
    smiles: str,
    k: int = 10,
    outdir: str = "cases"
) -> Dict[str, Any]:
    """
    Create a Case File from SMILES (V0.7).

    Args:
        smiles: Input SMILES string
        k: Number of neighbors to retrieve
        outdir: Output directory for case files

    Returns:
        Created case dict
    """
    timestamp = now_iso()

    # Canonicalize SMILES
    canonical, inchikey = canonicalize_smiles(smiles)

    # Generate case_id
    case_id = inchikey if inchikey else str(uuid.uuid4())

    # Compute ECFP
    query_fp = compute_ecfp(canonical if canonical else smiles)

    # Load reference data
    rdkit_df = pd.read_parquet('data/rdkit_features.parquet')
    label_map = pd.read_parquet('data/mechanism_label_map.parquet')

    # Search neighbors
    if query_fp is not None:
        neighbors = search_neighbors(query_fp, inchikey, rdkit_df, label_map, k)
    else:
        neighbors = []

    # =========================================================================
    # V0.7: Attach neighbor aTB evidence pack
    # =========================================================================
    n_success = 0
    n_keyfield_complete = 0
    
    for neighbor in neighbors:
        neighbor_ik = neighbor['neighbor_inchikey']
        neighbor_atb = get_neighbor_atb_evidence(neighbor_ik)
        neighbor['neighbor_atb'] = neighbor_atb
        
        # Count for metrics
        if neighbor_atb.get('cache_status') == AtbCacheStatus.SUCCESS.value:
            n_success += 1
            n_keyfield_complete += 1
    
    # Compute neighbor coverage metrics
    neighbor_atb_success_rate = n_success / k if k > 0 else None
    neighbor_atb_keyfield_rate = n_keyfield_complete / k if k > 0 else None

    # =========================================================================
    # V0.7: Candidate mechanisms + signatures
    # =========================================================================
    candidate_mechanisms = compute_candidate_mechanisms(neighbors, beta=10.0, top_n=3)
    all_signatures = load_mechanism_signatures()
    mechanism_signatures = get_mechanism_signatures_for_candidates(candidate_mechanisms, all_signatures)

    # Compute risk scores
    risk_scores = compute_risk_scores(neighbors)

    # =========================================================================
    # V0.7: Target aTB cache status + features_summary
    # =========================================================================
    cache_status = check_atb_cache_status(inchikey)
    features_summary, missing_fields = get_atb_features_summary(inchikey)

    # Initialize evidence_readiness with new schema
    evidence_readiness = create_empty_evidence_readiness(timestamp)
    evidence_readiness['atb']['cache_status'] = cache_status
    evidence_readiness['atb']['request_status'] = AtbRequestStatus.NOT_REQUESTED.value
    evidence_readiness['atb']['missing_fields'] = missing_fields
    
    # Attach features_summary if available
    if features_summary:
        evidence_readiness['atb']['features_summary'] = features_summary
    
    # Attach neighbor coverage metrics at TOP-LEVEL (not under atb)
    evidence_readiness['neighbor_atb_success_rate'] = round(neighbor_atb_success_rate, 4) if neighbor_atb_success_rate is not None else None
    evidence_readiness['neighbor_atb_keyfield_rate'] = round(neighbor_atb_keyfield_rate, 4) if neighbor_atb_keyfield_rate is not None else None

    # Attach train-only private observation availability (emission_solid/emission_aggr).
    private_obs = get_private_observation_summary(inchikey)
    evidence_readiness["minimal_experiment_available"]["has_emission"] = private_obs["has_emission"]
    evidence_readiness["minimal_experiment_available"]["has_qy"] = False
    evidence_readiness["minimal_experiment_available"]["has_tau"] = False
    evidence_readiness["minimal_experiment_available"]["has_solvent"] = False

    # =========================================================================
    # V0.7: aTB neighborhood consistency check (delta outlier score)
    # =========================================================================
    # Uses structure-only neighbors already retrieved; does NOT affect retrieval/indexing.
    try:
        risk_scores["atb_neighbor_consistency"] = compute_atb_neighbor_consistency(
            target_cache_status=cache_status,
            target_features_summary=features_summary,
            neighbors=neighbors,
            neighbor_label_entropy=risk_scores.get("mechanism_entropy"),
            required_fields=["delta_gap", "delta_dihedral", "delta_volume"],
            min_sample_size=5,
        )
    except Exception:
        # Do not fail case creation due to an optional diagnostic block.
        risk_scores["atb_neighbor_consistency"] = {
            "enabled": True,
            "sample_size": 0,
            "fields_used": ["delta_gap", "delta_dihedral", "delta_volume"],
            "flag": "target_missing",
            "reliability": "low",
        }

    # Re-evaluate gate after setting cache_status and features_summary
    ready, reason = evaluate_gate(evidence_readiness)
    evidence_readiness['current_gate']['ready_for_reasoning'] = ready
    evidence_readiness['current_gate']['reason'] = reason

    # Build action plan (V0.7: LLM-friendly objects + reasoning_mode)
    mea = evidence_readiness.get("minimal_experiment_available", {})
    has_emission = bool(mea.get("has_emission", False))
    has_solvent = bool(mea.get("has_solvent", False))
    atb_flag = (risk_scores.get("atb_neighbor_consistency") or {}).get("flag", "target_missing")
    action_plan, action_rationale, reasoning_mode = build_llm_action_plan_v07(
        inchikey=inchikey,
        canonical_smiles=canonical,
        cache_status=cache_status,
        atb_missing_fields=missing_fields,
        atb_neighbor_flag=atb_flag,
        has_emission=has_emission,
        has_solvent=has_solvent,
        retry_failed_atb=True,
        aliases=[],
    )
    evidence_readiness["current_gate"]["reasoning_mode"] = reasoning_mode

    # Create history
    history = [
        create_history_event(
            Actor.DATA_AGENT.value,
            EventType.CASE_CREATED.value,
            {'source': 'smiles_input', 'input_smiles': smiles}
        )
    ]

    # Build case
    case = {
        'case_id': case_id,
        'case_version': CASE_VERSION,
        'query': {
            'input_smiles': smiles,
            'canonical_smiles': canonical,
            'inchikey': inchikey,
            'created_at': timestamp
        },
        'risk_scores': risk_scores,
        'evidence_readiness': evidence_readiness,
        'neighbors': neighbors,
        'candidate_mechanisms': candidate_mechanisms,
        'mechanism_signatures': mechanism_signatures,
        'action_plan': action_plan,
        'action_rationale': action_rationale,
        'history': history
    }

    # Validate
    is_valid, errors = validate_case_file(case)
    if not is_valid:
        raise ValueError(f"Generated case file is invalid: {errors}")

    # Write to disk
    outdir_path = Path(outdir)
    outdir_path.mkdir(parents=True, exist_ok=True)
    case_path = outdir_path / f"{case_id}.json"

    with open(case_path, 'w') as f:
        json.dump(case, f, indent=2)

    return case


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Create Case File from SMILES (Data Agent)'
    )
    parser.add_argument('--smiles', type=str, required=True,
                        help='Input SMILES string')
    parser.add_argument('--k', type=int, default=10,
                        help='Number of neighbors (default: 10)')
    parser.add_argument('--outdir', type=str, default='cases',
                        help='Output directory (default: cases)')
    parser.add_argument('--print', action='store_true', dest='print_output',
                        help='Print case to stdout')

    args = parser.parse_args()

    print(f"Creating case from SMILES: {args.smiles[:50]}...")
    case = create_case_from_smiles(args.smiles, args.k, args.outdir)

    case_path = Path(args.outdir) / f"{case['case_id']}.json"
    print(f"Case created: {case_path}")
    print(f"  case_id: {case['case_id']}")
    print(f"  inchikey: {case['query']['inchikey']}")
    print(f"  top1_sim: {case['risk_scores']['top1_sim']}")
    print(f"  mechanism_hint: {case['risk_scores']['mechanism_hint']}")
    print(f"  cache_status: {case['evidence_readiness']['atb']['cache_status']}")
    print(f"  request_status: {case['evidence_readiness']['atb']['request_status']}")
    print(f"  ready_for_reasoning: {case['evidence_readiness']['current_gate']['ready_for_reasoning']}")
    print(f"  action_plan: {case['action_plan']}")

    if args.print_output:
        print("\nFull case JSON:")
        print(json.dumps(case, indent=2))


if __name__ == '__main__':
    main()
