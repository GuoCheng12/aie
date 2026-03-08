import json

from src.reasoning.neighbor_atb_stats import compute_neighbor_atb_stats


def _neighbors(n: int):
    rows = []
    for i in range(n):
        rows.append(
            {
                "neighbor_inchikey": f"N{i}",
                "rank": i + 1,
                "cache_status": "success",
                "features_summary": {
                    "delta_dihedral": float(i - 2),
                    "delta_gap": -0.2 + 0.05 * i,
                    "delta_volume": 0.1 * i,
                },
                "neighbor_mechanism_label": "TICT" if i % 2 == 0 else "ICT",
            }
        )
    return rows


def test_neighbor_atb_stats_small_output_and_shape():
    out = compute_neighbor_atb_stats(
        target_features_summary={"delta_dihedral": -4.2, "delta_gap": -0.1, "delta_volume": 0.15},
        neighbor_atb_features_all=_neighbors(8),
    )
    assert out["sample_size"] == 8
    assert "fields" in out
    assert "delta_dihedral" in out["fields"]
    assert "abs_delta_dihedral" in out["fields"]
    assert "summary" in out
    assert out["reliability"] in {"low", "medium", "high"}
    assert "separation_score" in out
    assert len(json.dumps(out, ensure_ascii=False)) < 3072


def test_neighbor_atb_stats_low_sample_sets_null_z():
    out = compute_neighbor_atb_stats(
        target_features_summary={"delta_dihedral": -9.7, "delta_gap": -0.3, "delta_volume": 0.14},
        neighbor_atb_features_all=_neighbors(3),
    )
    assert out["sample_size"] == 3
    assert out["reliability"] == "low"
    assert out["fields"]["abs_delta_dihedral"]["z_robust"] is None
    assert out["fields"]["delta_gap"]["z_robust"] is None
    assert out["fields"]["delta_volume"]["z_robust"] is None


def test_neighbor_atb_stats_deterministic_trim_under_budget():
    rows = []
    labels = ["A", "B", "C", "D", "E"]
    for i in range(60):
        rows.append(
            {
                "neighbor_inchikey": f"N{i}",
                "rank": i + 1,
                "cache_status": "success",
                "features_summary": {
                    "delta_dihedral": float((i % 9) - 4),
                    "delta_gap": -0.3 + 0.02 * (i % 11),
                    "delta_volume": 0.05 * (i % 7),
                },
                "neighbor_mechanism_label": labels[i % len(labels)],
            }
        )
    out = compute_neighbor_atb_stats(
        target_features_summary={"delta_dihedral": -7.5, "delta_gap": -0.15, "delta_volume": 0.1},
        neighbor_atb_features_all=rows,
    )
    assert len(json.dumps(out, ensure_ascii=False, sort_keys=True)) < 3072
    assert len(out.get("summary") or []) <= 3
