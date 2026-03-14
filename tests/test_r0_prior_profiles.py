from src.reasoning.r0_prior_profiles import (
    MAIN_PRIOR_LABELS,
    compute_candidate_slate_v2,
    compute_prior_reliability_profile,
    compute_structure_fact_sheet,
)


def test_structure_fact_sheet_keeps_compact_phenomenon_fields() -> None:
    fact_sheet = compute_structure_fact_sheet(
        {
            "donor_acceptor_topology": "mixed",
            "reliability": "high",
            "notes": ["Conjugated aromatic topology with moderate flexibility."],
        },
        {
            "intramolecular_hbond_geometry": "favorable",
            "proton_transfer_topology_candidate": "possible",
            "tautomerizable_subgraph_strength": "mid",
            "donor_acceptor_fragment_balance": "low",
            "donor_acceptor_path_multiplicity": "mid",
            "donor_acceptor_separation_regime": "mid",
            "aromatic_rigidity_signature": "high",
            "fused_aromatic_core_strength": "high",
            "aromatic_core_connectivity": "fused",
            "global_flexibility_vs_core_rigidity": "rigid_core_with_mobile_periphery",
            "planarity_proxy": "mid",
            "planarity_break_count": 2,
            "conjugation_compactness": "high",
            "conjugation_continuity": "mostly_continuous",
            "heteroatom_cluster_pattern": "mixed",
            "proton_transfer_local_geometry": "possible",
            "reliability": "high",
            "notes": ["Aromatic scaffold is fused with high rigidity."],
        },
    )
    assert fact_sheet["version"] == "structure_fact_sheet_v1"
    assert fact_sheet["aromatic_core_connectivity"] == "fused"
    assert fact_sheet["global_flexibility_vs_core_rigidity"] == "rigid_core_with_mobile_periphery"
    joined = " ".join(fact_sheet["notes"]).lower()
    for bad in ("ict", "tict", "esipt", "neutral aromatic"):
        assert bad not in joined


def test_prior_reliability_profile_detects_cross_source_conflict() -> None:
    profile = compute_prior_reliability_profile(
        structure_retrieval_profile={
            "feature_neighbor_label_distribution": {"ICT": 1.0},
            "scaffold_neighbor_label_distribution": {"neutral aromatic": 1.0},
        },
        neighbors=[
            {"neighbor_mechanism_label": "TICT", "sim": 0.55},
            {"neighbor_mechanism_label": "TICT", "sim": 0.50},
        ],
        top1_sim=0.31,
        mechanism_entropy=0.88,
        novelty_struct=0.74,
        allowed_labels=MAIN_PRIOR_LABELS,
    )
    assert profile["cross_source_agreement"] == "low"
    assert profile["prior_reliability"] == "low"
    assert profile["ambiguity_level"] == "high"


def test_candidate_slate_v2_excludes_nonstandard_labels_and_flattens_when_ambiguous() -> None:
    slate = compute_candidate_slate_v2(
        structure_retrieval_profile={
            "feature_neighbor_label_distribution": {"ICT": 0.7, "clusterluminescence": 0.3},
            "scaffold_neighbor_label_distribution": {"neutral aromatic": 1.0},
        },
        neighbors=[
            {"neighbor_mechanism_label": "ESIPT", "sim": 0.28},
            {"neighbor_mechanism_label": "other", "sim": 0.27},
            {"neighbor_mechanism_label": "ESIPT+ICT/TICT", "sim": 0.26},
        ],
        top1_sim=0.29,
        mechanism_entropy=0.91,
        novelty_struct=0.77,
        allowed_labels=MAIN_PRIOR_LABELS,
    )
    labels = slate["candidate_labels"]
    assert len(labels) == 4
    assert set(labels).issubset(set(MAIN_PRIOR_LABELS))
    assert slate["slate_confidence"] == "low"
    assert "clusterluminescence" not in slate["candidate_scores"]
