import json
from pathlib import Path

from src.agents.chem_agent_atb import apply_atb_cache_to_case_file


def _base_case(inchikey: str) -> dict:
    return {
        "case_id": inchikey,
        "case_version": "0.7",
        "query": {
            "input_smiles": "C",
            "canonical_smiles": "C",
            "inchikey": inchikey,
            "created_at": "2026-01-01T00:00:00Z",
        },
        "risk_scores": {
            "top1_sim": 0.0,
            "mean_topk_sim": 0.0,
            "neighbor_gap": 0.0,
            "novelty_struct": 1.0,
            "mechanism_entropy": 1.0,
            "mechanism_hint": "unknown",
            "hint_confidence": 0.0,
        },
        "evidence_readiness": {
            "atb": {
                "cache_status": "absent",
                "request_status": "not_requested",
                "missing_fields": [],
                "last_update": "2026-01-01T00:00:00Z",
            },
            "literature": {
                "status": "not_started",
                "sources": [],
                "last_update": "2026-01-01T00:00:00Z",
            },
            "experiment": {
                "status": "not_requested",
                "requested_fields": [],
                "received_fields": [],
                "last_update": "2026-01-01T00:00:00Z",
            },
            "minimal_experiment_available": {
                "has_emission": False,
                "has_qy": False,
                "has_tau": False,
                "has_solvent": False,
            },
            "current_gate": {
                "ready_for_reasoning": False,
                "reason": "init",
                "reasoning_mode": "blocked",
            },
        },
        "neighbors": [],
        "action_plan": [],
        "history": [],
        "candidate_mechanisms": [],
        "mechanism_signatures": {},
    }


def test_apply_atb_cache_to_case_file_success(tmp_path: Path):
    inchikey = "FROQVQNFQFCXNO-KSZJGORMSA-N"
    case_path = tmp_path / "case.json"
    case_path.write_text(json.dumps(_base_case(inchikey), indent=2), encoding="utf-8")

    cache_dir = tmp_path / "cache" / "atb"
    cdir = cache_dir / inchikey[:2] / inchikey
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "status.json").write_text(
        json.dumps(
            {
                "inchikey": inchikey,
                "run_status": "success",
                "fail_stage": None,
                "error_msg": None,
            }
        ),
        encoding="utf-8",
    )
    (cdir / "features.json").write_text(
        json.dumps(
            {
                "delta_volume": 1.2,
                "delta_gap": 0.4,
                "delta_dihedral": 3.1,
                "excitation_energy": 2.5,
                "delta_dipole": 0.7,
                "delta_bonds": 0.03,
                "delta_angles": 0.4,
                "exciting_path_mean_volume": 12.3,
                "s0_rays_asymmetry_parameter": 0.11,
                "s1_rays_asymmetry_parameter": 0.14,
                "s0_rotational_constant_a": 1.0,
                "s1_rotational_constant_a": 1.02,
            }
        ),
        encoding="utf-8",
    )

    out = apply_atb_cache_to_case_file(case_path=case_path, cache_dir=str(cache_dir))
    assert out["cache_status"] == "success"
    assert out["request_status"] == "done"

    case_after = json.loads(case_path.read_text(encoding="utf-8"))
    atb = case_after["evidence_readiness"]["atb"]
    assert atb["cache_status"] == "success"
    assert atb["request_status"] == "done"
    assert "features_summary" in atb
    assert atb["features_summary"]["delta_dipole"] == 0.7
    assert atb["features_summary"]["delta_bonds"] == 0.03
    assert atb["features_summary"]["delta_angles"] == 0.4
    assert atb["features_summary"]["exciting_path_mean_volume"] == 12.3
    assert atb["features_summary"]["s0_rays_asymmetry_parameter"] == 0.11
    assert atb["features_summary"]["s1_rotational_constant_a"] == 1.02
    assert len(case_after.get("agent_runs", [])) == 1
    assert case_after["agent_runs"][0]["agent_name"] == "chem_agent_atb"
    assert len(case_after.get("history", [])) >= 1
    assert "case_sections" in case_after
