"""
Compact R0 prior views that separate structure facts, prior reliability, and
candidate generation.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


MAIN_PRIOR_LABELS: tuple[str, ...] = ("ICT", "TICT", "ESIPT", "neutral aromatic")


def _to_float(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except Exception:
        return None
    if out != out:
        return None
    return out


def _label_allowed(label: Any, allowed_labels: Sequence[str]) -> Optional[str]:
    txt = str(label or "").strip()
    if not txt:
        return None
    allowed = {str(v).strip().lower(): str(v).strip() for v in allowed_labels if str(v).strip()}
    return allowed.get(txt.lower())


def _top_distribution_label(dist: Mapping[str, Any], allowed_labels: Sequence[str]) -> tuple[Optional[str], float]:
    best_label: Optional[str] = None
    best_value = 0.0
    for raw_label, raw_value in (dist or {}).items():
        label = _label_allowed(raw_label, allowed_labels)
        value = _to_float(raw_value) or 0.0
        if label and value > best_value:
            best_label = label
            best_value = value
    return best_label, float(best_value)


def _consensus_strength(value: float) -> str:
    if value >= 0.60:
        return "high"
    if value >= 0.35:
        return "mid"
    return "low"


def _weighted_label_distribution(
    rows: Iterable[Mapping[str, Any]],
    *,
    label_key: str,
    weight_key: str = "sim",
    allowed_labels: Sequence[str] = MAIN_PRIOR_LABELS,
) -> Dict[str, float]:
    totals: Dict[str, float] = defaultdict(float)
    total_weight = 0.0
    for row in rows:
        label = _label_allowed((row or {}).get(label_key), allowed_labels)
        if not label:
            continue
        weight = _to_float((row or {}).get(weight_key)) or 0.0
        if weight <= 0.0:
            continue
        totals[label] += weight
        total_weight += weight
    if total_weight <= 0.0:
        return {}
    return {
        label: round(value / total_weight, 6)
        for label, value in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
    }


def compute_structure_fact_sheet(
    structure_prior_profile: Mapping[str, Any] | None,
    structure_motif_profile: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    prior = dict(structure_prior_profile or {})
    motif = dict(structure_motif_profile or {})
    notes: List[str] = []
    for value in (motif.get("notes") or []):
        text = str(value or "").strip()
        if text and text not in notes:
            notes.append(text)
    for value in (prior.get("notes") or []):
        text = str(value or "").strip()
        if text and text not in notes:
            notes.append(text)
    return {
        "version": "structure_fact_sheet_v1",
        "donor_acceptor_topology": prior.get("donor_acceptor_topology"),
        "intramolecular_hbond_geometry": motif.get("intramolecular_hbond_geometry"),
        "proton_transfer_topology_candidate": motif.get("proton_transfer_topology_candidate"),
        "tautomerizable_subgraph_strength": motif.get("tautomerizable_subgraph_strength"),
        "donor_acceptor_fragment_balance": motif.get("donor_acceptor_fragment_balance"),
        "donor_acceptor_path_multiplicity": motif.get("donor_acceptor_path_multiplicity"),
        "donor_acceptor_separation_regime": motif.get("donor_acceptor_separation_regime"),
        "aromatic_rigidity_signature": motif.get("aromatic_rigidity_signature"),
        "fused_aromatic_core_strength": motif.get("fused_aromatic_core_strength"),
        "aromatic_core_connectivity": motif.get("aromatic_core_connectivity"),
        "global_flexibility_vs_core_rigidity": motif.get("global_flexibility_vs_core_rigidity"),
        "planarity_proxy": motif.get("planarity_proxy"),
        "planarity_break_count": motif.get("planarity_break_count"),
        "conjugation_compactness": motif.get("conjugation_compactness"),
        "conjugation_continuity": motif.get("conjugation_continuity"),
        "heteroatom_cluster_pattern": motif.get("heteroatom_cluster_pattern"),
        "proton_transfer_local_geometry": motif.get("proton_transfer_local_geometry"),
        "reliability": motif.get("reliability") or prior.get("reliability") or "low",
        "notes": notes[:4],
    }


def compute_prior_reliability_profile(
    *,
    structure_retrieval_profile: Mapping[str, Any] | None,
    neighbors: Sequence[Mapping[str, Any]] | None,
    top1_sim: Any,
    mechanism_entropy: Any,
    novelty_struct: Any,
    allowed_labels: Sequence[str] = MAIN_PRIOR_LABELS,
) -> Dict[str, Any]:
    retrieval = dict(structure_retrieval_profile or {})
    feature_dist = {
        label: value
        for label, value in (retrieval.get("feature_neighbor_label_distribution") or {}).items()
        if _label_allowed(label, allowed_labels)
        for value in [_to_float(value) or 0.0]
    }
    scaffold_dist = {
        label: value
        for label, value in (retrieval.get("scaffold_neighbor_label_distribution") or {}).items()
        if _label_allowed(label, allowed_labels)
        for value in [_to_float(value) or 0.0]
    }
    neighbor_dist = _weighted_label_distribution(
        neighbors or [],
        label_key="neighbor_mechanism_label",
        weight_key="sim",
        allowed_labels=allowed_labels,
    )

    feature_top_label, feature_top_value = _top_distribution_label(feature_dist, allowed_labels)
    scaffold_top_label, scaffold_top_value = _top_distribution_label(scaffold_dist, allowed_labels)
    neighbor_top_label, neighbor_top_value = _top_distribution_label(neighbor_dist, allowed_labels)

    agreement_votes = [label for label in (feature_top_label, scaffold_top_label, neighbor_top_label) if label]
    agreement = "low"
    if agreement_votes:
        counts = defaultdict(int)
        for label in agreement_votes:
            counts[label] += 1
        top_vote = max(counts.values())
        if top_vote >= 3:
            agreement = "high"
        elif top_vote >= 2:
            agreement = "mid"

    top1 = _to_float(top1_sim) or 0.0
    entropy = _to_float(mechanism_entropy)
    novelty = _to_float(novelty_struct) or 0.0

    if agreement == "high" and top1 >= 0.55 and (entropy is None or entropy <= 0.45):
        prior_reliability = "high"
    elif agreement == "low" or top1 < 0.35 or (entropy is not None and entropy >= 0.80) or novelty >= 0.70:
        prior_reliability = "low"
    else:
        prior_reliability = "medium"

    if prior_reliability == "high" and (entropy is None or entropy <= 0.45):
        ambiguity = "low"
    elif prior_reliability == "low" or (entropy is not None and entropy >= 0.75):
        ambiguity = "high"
    else:
        ambiguity = "medium"

    notes = [
        f"Feature consensus is {_consensus_strength(feature_top_value)}, scaffold consensus is {_consensus_strength(scaffold_top_value)}, and neighbor consensus is {_consensus_strength(neighbor_top_value)}.",
        f"Cross-source agreement is {agreement}; prior reliability is {prior_reliability} and ambiguity is {ambiguity}.",
    ]
    if entropy is not None:
        notes.append(f"Neighbor-label entropy is {round(float(entropy), 3)} and structural novelty is {round(float(novelty), 3)}.")

    return {
        "version": "prior_reliability_v1",
        "feature_consensus_strength": _consensus_strength(feature_top_value),
        "scaffold_consensus_strength": _consensus_strength(scaffold_top_value),
        "neighbor_consensus_strength": _consensus_strength(neighbor_top_value),
        "cross_source_agreement": agreement,
        "prior_reliability": prior_reliability,
        "ambiguity_level": ambiguity,
        "feature_top_label": feature_top_label,
        "scaffold_top_label": scaffold_top_label,
        "neighbor_top_label": neighbor_top_label,
        "notes": notes[:3],
    }


def compute_candidate_slate_v2(
    *,
    structure_retrieval_profile: Mapping[str, Any] | None,
    neighbors: Sequence[Mapping[str, Any]] | None,
    top1_sim: Any,
    mechanism_entropy: Any,
    novelty_struct: Any,
    allowed_labels: Sequence[str] = MAIN_PRIOR_LABELS,
) -> Dict[str, Any]:
    retrieval = dict(structure_retrieval_profile or {})
    feature_dist = {
        label: _to_float(value) or 0.0
        for label, value in (retrieval.get("feature_neighbor_label_distribution") or {}).items()
        if _label_allowed(label, allowed_labels)
    }
    scaffold_dist = {
        label: _to_float(value) or 0.0
        for label, value in (retrieval.get("scaffold_neighbor_label_distribution") or {}).items()
        if _label_allowed(label, allowed_labels)
    }
    neighbor_dist = _weighted_label_distribution(
        neighbors or [],
        label_key="neighbor_mechanism_label",
        weight_key="sim",
        allowed_labels=allowed_labels,
    )
    reliability = compute_prior_reliability_profile(
        structure_retrieval_profile=structure_retrieval_profile,
        neighbors=neighbors,
        top1_sim=top1_sim,
        mechanism_entropy=mechanism_entropy,
        novelty_struct=novelty_struct,
        allowed_labels=allowed_labels,
    )

    scores: Dict[str, float] = {label: 0.0 for label in allowed_labels}
    for label in allowed_labels:
        scores[label] += 0.50 * float(feature_dist.get(label, 0.0))
        scores[label] += 0.30 * float(scaffold_dist.get(label, 0.0))
        scores[label] += 0.20 * float(neighbor_dist.get(label, 0.0))

    prior_reliability = str(reliability.get("prior_reliability") or "low")
    ambiguity_level = str(reliability.get("ambiguity_level") or "high")
    if prior_reliability == "low" or ambiguity_level == "high":
        uniform = 1.0 / float(len(allowed_labels))
        scores = {label: (0.60 * score) + (0.40 * uniform) for label, score in scores.items()}
        slate_confidence = "low"
        topk = 4
    elif prior_reliability == "medium":
        uniform = 1.0 / float(len(allowed_labels))
        scores = {label: (0.80 * score) + (0.20 * uniform) for label, score in scores.items()}
        slate_confidence = "medium"
        topk = 3
    else:
        slate_confidence = "high"
        topk = 3

    total = sum(scores.values())
    if total <= 0.0:
        norm_scores = {label: round(1.0 / float(len(allowed_labels)), 6) for label in allowed_labels}
        slate_confidence = "low"
        topk = 4
    else:
        norm_scores = {label: round(value / total, 6) for label, value in scores.items()}

    top_candidates = [
        {"label": label, "prob": prob}
        for label, prob in sorted(norm_scores.items(), key=lambda kv: (-kv[1], kv[0]))[:topk]
    ]
    return {
        "version": "candidate_slate_v2",
        "candidate_labels": [row["label"] for row in top_candidates],
        "candidate_scores": norm_scores,
        "top_candidates": top_candidates,
        "top3": top_candidates[:3],
        "consensus_source_breakdown": {
            "feature_retrieval": feature_dist,
            "murcko_retrieval": scaffold_dist,
            "ecfp_prior": neighbor_dist,
        },
        "slate_confidence": slate_confidence,
    }


__all__ = [
    "MAIN_PRIOR_LABELS",
    "compute_candidate_slate_v2",
    "compute_prior_reliability_profile",
    "compute_structure_fact_sheet",
]
