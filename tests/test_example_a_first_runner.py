import json
import shutil
from pathlib import Path

import pytest

import src.cases.example_a_first_runner as e0_runner
from src.cases.example_a_first_runner import _apply_patch, run_case_example_a_e0
from src.cases.mineru_llm_extractor import MineruLLMExtractorError


def _base_case(case_id: str = "CASE-E0-1") -> dict:
    return {
        "case_id": case_id,
        "case_version": "0.7",
        "query": {
            "input_smiles": "C",
            "canonical_smiles": "C",
            "inchikey": "VNWKTOKETHGBQD-UHFFFAOYSA-N",
            "created_at": "2026-02-17T00:00:00Z",
        },
        "evidence_readiness": {
            "current_gate": {
                "ready_for_reasoning": False,
                "reason": "init",
            }
        },
        "inputs": {"offline_pdfs": []},
    }


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def _make_precomputed_mineru_output(tmp_path: Path, pdf: Path, stem: str = "1001") -> Path:
    output_root = tmp_path / "mineru_output"
    run_dir = output_root / stem / "hybrid_auto"
    run_dir.mkdir(parents=True, exist_ok=True)

    origin_pdf = run_dir / f"{stem}_origin.pdf"
    shutil.copyfile(pdf, origin_pdf)

    md_path = run_dir / f"{stem}.md"
    md_path.write_text(
        "Table 1: thin film emission 610 nm; aggregate emission 540 nm.",
        encoding="utf-8",
    )
    _write_json(
        run_dir / f"{stem}_content_list_v2.json",
        [
            {"page_idx": 2, "type": "table", "text": "thin film 610 nm"},
            {"page_idx": 3, "type": "figure", "text": "aggregate emission 540 nm"},
        ],
    )
    return output_root


def test_blocked_input_missing_when_no_offline_pdf(tmp_path: Path):
    case_path = tmp_path / "case.json"
    _write_json(case_path, _base_case())

    summary = run_case_example_a_e0(
        case_path=case_path,
        artifacts_dir=tmp_path / "artifacts",
        evidence_table_path=tmp_path / "evidence_table.parquet",
    )
    case_after = json.loads(case_path.read_text(encoding="utf-8"))

    assert summary["gate_state"] == "blocked_input_missing"
    assert case_after["current_gate"]["state"] == "blocked_input_missing"
    assert case_after["evidence_readiness"]["literature"]["status"] == "not_started"
    assert case_after["evidence_readiness"]["current_gate"]["ready_for_reasoning"] is False
    assert "request_manual_pdf" in case_after["next_actions"]
    assert len(case_after.get("history", [])) >= 3


def test_failed_extract_when_extractor_json_missing(tmp_path: Path):
    case = _base_case("CASE-E0-2")
    case["inputs"]["offline_pdfs"] = [{"path_or_id": str(tmp_path / "missing.pdf")}]
    case_path = tmp_path / "case.json"
    _write_json(case_path, case)

    summary = run_case_example_a_e0(
        case_path=case_path,
        artifacts_dir=tmp_path / "artifacts",
        evidence_table_path=tmp_path / "evidence_table.parquet",
    )
    case_after = json.loads(case_path.read_text(encoding="utf-8"))

    assert summary["gate_state"] == "failed_extract"
    assert case_after["evidence_readiness"]["literature"]["status"] == "not_found"
    assert case_after["evidence_readiness"]["current_gate"]["reason"] == "extractor_failure"


def test_extracted_no_writeback_when_all_rejected(tmp_path: Path):
    case = _base_case("CASE-E0-3")
    pdf = tmp_path / "paper.pdf"
    pdf.write_text("dummy", encoding="utf-8")
    case["inputs"]["offline_pdfs"] = [{"path_or_id": str(pdf)}]
    case_path = tmp_path / "case.json"
    _write_json(case_path, case)

    # Sidecar extractor output with condition unmapped -> rejected.
    extractor_json = tmp_path / "paper.json"
    _write_json(
        extractor_json,
        {
            "candidates": [
                {
                    "value": 520,
                    "unit": "nm",
                    "condition": "room temperature solution",
                    "source_locator": "text paragraph",
                    "page": 3,
                    "value_source_kind": "text",
                    "identity_match": "matched",
                    "identity_match_confidence": 0.9,
                    "confidence": 0.9,
                }
            ]
        },
    )

    summary = run_case_example_a_e0(
        case_path=case_path,
        artifacts_dir=tmp_path / "artifacts",
        evidence_table_path=tmp_path / "evidence_table.parquet",
    )
    case_after = json.loads(case_path.read_text(encoding="utf-8"))

    assert summary["gate_state"] == "extracted_no_writeback"
    assert len(case_after["evidence_candidates_staging"]) == 1
    assert case_after["evidence_candidates_staging"][0]["verification_status"] == "rejected"


def test_relaxed_mode_allows_unverified_case_writeback(tmp_path: Path):
    case = _base_case("CASE-E0-3B")
    case["evidence_acquire"] = {"emission": {"strictness": "relaxed"}}
    pdf = tmp_path / "paper.pdf"
    pdf.write_text("dummy", encoding="utf-8")
    case["inputs"]["offline_pdfs"] = [{"path_or_id": str(pdf)}]
    case_path = tmp_path / "case.json"
    _write_json(case_path, case)

    extractor_json = tmp_path / "paper.json"
    _write_json(
        extractor_json,
        {
            "candidates": [
                {
                    "value": 578,
                    "unit": "nm",
                    "condition": "powder",
                    "source_locator": "Fig. 2 caption and text",
                    "page": None,
                    "value_source_kind": "figure",
                    "identity_match": "matched",
                    "identity_match_confidence": 0.95,
                    "confidence": 0.95,
                }
            ]
        },
    )

    summary = run_case_example_a_e0(
        case_path=case_path,
        artifacts_dir=tmp_path / "artifacts",
        evidence_table_path=tmp_path / "evidence_table.parquet",
    )
    case_after = json.loads(case_path.read_text(encoding="utf-8"))

    assert summary["gate_state"] == "ready_for_reasoning"
    assert case_after["target_fields"]["emission_solid_or_film_nm"] == 578
    assert case_after["target_fields_provenance"]["emission_solid_or_film_nm"]["verified"] is True


def test_strict_mode_allows_verified_without_page_if_locator_is_structured(tmp_path: Path):
    case = _base_case("CASE-E0-3C")
    case["evidence_acquire"] = {"emission": {"strictness": "strict"}}
    pdf = tmp_path / "paper.pdf"
    pdf.write_text("dummy", encoding="utf-8")
    case["inputs"]["offline_pdfs"] = [{"path_or_id": str(pdf)}]
    case_path = tmp_path / "case.json"
    _write_json(case_path, case)

    extractor_json = tmp_path / "paper.json"
    _write_json(
        extractor_json,
        {
            "candidates": [
                {
                    "value": 578,
                    "unit": "nm",
                    "condition": "powder",
                    "source_locator": "Fig. 2 caption and text",
                    "page": None,
                    "value_source_kind": "figure",
                    "identity_match": "matched",
                    "identity_match_confidence": 0.95,
                    "confidence": 0.95,
                }
            ]
        },
    )

    summary = run_case_example_a_e0(
        case_path=case_path,
        artifacts_dir=tmp_path / "artifacts",
        evidence_table_path=tmp_path / "evidence_table.parquet",
    )
    case_after = json.loads(case_path.read_text(encoding="utf-8"))

    assert summary["gate_state"] == "ready_for_reasoning"
    assert case_after["target_fields"]["emission_solid_or_film_nm"] == 578
    assert case_after["target_fields_provenance"]["emission_solid_or_film_nm"]["verified"] is True


def test_strict_mode_still_requires_page_when_locator_is_not_structured(tmp_path: Path):
    case = _base_case("CASE-E0-3D")
    case["evidence_acquire"] = {"emission": {"strictness": "strict"}}
    pdf = tmp_path / "paper.pdf"
    pdf.write_text("dummy", encoding="utf-8")
    case["inputs"]["offline_pdfs"] = [{"path_or_id": str(pdf)}]
    case_path = tmp_path / "case.json"
    _write_json(case_path, case)

    extractor_json = tmp_path / "paper.json"
    _write_json(
        extractor_json,
        {
            "candidates": [
                {
                    "value": 578,
                    "unit": "nm",
                    "condition": "powder",
                    "source_locator": "main text paragraph",
                    "page": None,
                    "value_source_kind": "text",
                    "identity_match": "matched",
                    "identity_match_confidence": 0.95,
                    "confidence": 0.95,
                }
            ]
        },
    )

    summary = run_case_example_a_e0(
        case_path=case_path,
        artifacts_dir=tmp_path / "artifacts",
        evidence_table_path=tmp_path / "evidence_table.parquet",
    )
    case_after = json.loads(case_path.read_text(encoding="utf-8"))

    assert summary["gate_state"] == "extracted_no_writeback"
    assert case_after.get("target_fields", {}) == {}


def test_ready_for_reasoning_and_film_priority_tiebreaker(tmp_path: Path):
    case = _base_case("CASE-E0-4")
    pdf = tmp_path / "paper.pdf"
    pdf.write_text("dummy", encoding="utf-8")
    case["inputs"]["offline_pdfs"] = [{"path_or_id": str(pdf)}]
    case_path = tmp_path / "case.json"
    _write_json(case_path, case)

    extractor_json = tmp_path / "paper.json"
    _write_json(
        extractor_json,
        {
            "candidates": [
                {
                    "value": 610,
                    "unit": "nm",
                    "condition": "thin film",
                    "source_locator": "Table 1",
                    "page": 2,
                    "value_source_kind": "table",
                    "identity_match": "matched",
                    "identity_match_confidence": 0.95,
                    "confidence": 0.7,
                },
                {
                    "value": 590,
                    "unit": "nm",
                    "condition": "solid powder",
                    "source_locator": "Table 1",
                    "page": 2,
                    "value_source_kind": "table",
                    "identity_match": "matched",
                    "identity_match_confidence": 0.95,
                    "confidence": 0.9,
                },
                {
                    "value": 540,
                    "unit": "nm",
                    "condition": "aggregate in poor solvent",
                    "source_locator": "Fig 2",
                    "page": 4,
                    "value_source_kind": "figure",
                    "identity_match": "matched",
                    "identity_match_confidence": 0.9,
                    "confidence": 0.8,
                },
            ]
        },
    )

    summary = run_case_example_a_e0(
        case_path=case_path,
        artifacts_dir=tmp_path / "artifacts",
        evidence_table_path=tmp_path / "evidence_table.parquet",
    )
    case_after = json.loads(case_path.read_text(encoding="utf-8"))

    assert summary["gate_state"] == "ready_for_reasoning"
    assert case_after["target_fields"]["emission_solid_or_film_nm"] == 610
    assert case_after["target_fields"]["emission_aggr_nm"] == 540
    assert case_after["evidence_readiness"]["literature"]["status"] == "found"
    assert len(case_after["evidence_readiness"]["literature"]["sources"]) >= 1
    assert case_after["evidence_readiness"]["current_gate"]["reasoning_mode"] == "normal"
    assert case_after["case_sections"]["for_master_reasoning"]["target_fields"]["emission_solid_or_film_nm"] == 610
    assert len(case_after["case_sections"]["update_history"]["agent_runs"]) == len(case_after["agent_runs"])
    event_types = [h.get("event_type") for h in case_after.get("history", [])]
    assert "literature_updated" in event_types
    assert "gate_evaluated" in event_types


def test_idempotent_skip_does_not_duplicate_staging(tmp_path: Path):
    case = _base_case("CASE-E0-5")
    pdf = tmp_path / "paper.pdf"
    pdf.write_text("dummy", encoding="utf-8")
    case["inputs"]["offline_pdfs"] = [{"path_or_id": str(pdf)}]
    case_path = tmp_path / "case.json"
    _write_json(case_path, case)

    extractor_json = tmp_path / "paper.json"
    _write_json(
        extractor_json,
        {
            "candidates": [
                {
                    "value": 500,
                    "unit": "nm",
                    "condition": "thin film",
                    "source_locator": "Table 2",
                    "page": 5,
                    "value_source_kind": "table",
                    "identity_match": "matched",
                    "identity_match_confidence": 0.95,
                    "confidence": 0.8,
                }
            ]
        },
    )

    run_case_example_a_e0(case_path=case_path, artifacts_dir=tmp_path / "artifacts")
    after_first = json.loads(case_path.read_text(encoding="utf-8"))
    staging_len_first = len(after_first["evidence_candidates_staging"])

    summary_second = run_case_example_a_e0(case_path=case_path, artifacts_dir=tmp_path / "artifacts")
    after_second = json.loads(case_path.read_text(encoding="utf-8"))
    staging_len_second = len(after_second["evidence_candidates_staging"])

    assert summary_second["ready_for_reasoning"] is True
    assert staging_len_second == staging_len_first
    assert any(r.get("status") == "skipped" for r in after_second["agent_runs"])


def test_final_case_only_writes_case_and_single_run_log(tmp_path: Path):
    case = _base_case("CASE-E0-5B")
    pdf = tmp_path / "paper.pdf"
    pdf.write_text("dummy", encoding="utf-8")
    case["inputs"]["offline_pdfs"] = [{"path_or_id": str(pdf)}]
    case_path = tmp_path / "case.json"
    _write_json(case_path, case)

    extractor_json = tmp_path / "paper.json"
    _write_json(
        extractor_json,
        {
            "candidates": [
                {
                    "value": 510,
                    "unit": "nm",
                    "condition": "thin film",
                    "source_locator": "Table 2",
                    "page": 5,
                    "value_source_kind": "table",
                    "identity_match": "matched",
                    "identity_match_confidence": 0.95,
                    "confidence": 0.8,
                }
            ]
        },
    )

    artifacts_root = tmp_path / "artifacts"
    summary = run_case_example_a_e0(
        case_path=case_path,
        artifacts_dir=artifacts_root,
        artifact_mode="final_case_only",
    )

    assert summary["artifacts_dir"] is None
    run_log_path = Path(summary["run_log_path"])
    assert run_log_path.exists()
    run_log = json.loads(run_log_path.read_text(encoding="utf-8"))
    assert run_log["run_id"] == summary["run_id"]
    assert run_log["case_id"] == "CASE-E0-5B"
    assert run_log["target_fields_after"]["emission_solid_or_film_nm"] == 510
    assert not (artifacts_root / summary["run_id"]).exists()
    persisted_case = json.loads(case_path.read_text(encoding="utf-8"))
    assert persisted_case["target_fields"]["emission_solid_or_film_nm"] == 510
    assert persisted_case["_case_compaction"]["mode"] == "reasoning_only"
    assert "history" not in persisted_case
    assert "agent_runs" not in persisted_case


def test_patch_whitelist_failfast_for_disallowed_path():
    with pytest.raises(ValueError):
        _apply_patch(
            {"a": {}},
            [{"op": "add", "path": "/not_allowed/path", "value": 1}],
        )


def test_evidence_table_write_function_not_called_in_normal_e0_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    case = _base_case("CASE-E0-6")
    pdf = tmp_path / "paper.pdf"
    pdf.write_text("dummy", encoding="utf-8")
    case["inputs"]["offline_pdfs"] = [{"path_or_id": str(pdf)}]
    case_path = tmp_path / "case.json"
    _write_json(case_path, case)

    extractor_json = tmp_path / "paper.json"
    _write_json(
        extractor_json,
        {
            "candidates": [
                {
                    "value": 505,
                    "unit": "nm",
                    "condition": "thin film",
                    "source_locator": "Table 1",
                    "page": 1,
                    "value_source_kind": "table",
                    "identity_match": "matched",
                    "identity_match_confidence": 0.95,
                    "confidence": 0.9,
                }
            ]
        },
    )

    called = {"value": False}

    def _fail_if_called(*, evidence_table_path: Path, rows: list[dict]):
        called["value"] = True
        raise AssertionError("evidence-table writer must not be called in E0 normal flow")

    monkeypatch.setattr(e0_runner, "_write_evidence_table_rows", _fail_if_called)

    run_case_example_a_e0(
        case_path=case_path,
        artifacts_dir=tmp_path / "artifacts",
        evidence_table_path=tmp_path / "evidence_table.parquet",
    )
    assert called["value"] is False


def test_evidence_table_guard_fails_fast_when_writeback_requested(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    case = _base_case("CASE-E0-6B")
    pdf = tmp_path / "paper.pdf"
    pdf.write_text("dummy", encoding="utf-8")
    case["inputs"]["offline_pdfs"] = [{"path_or_id": str(pdf)}]
    case_path = tmp_path / "case.json"
    _write_json(case_path, case)

    called = {"value": False}

    def _writer(*, evidence_table_path: Path, rows: list[dict]):
        called["value"] = True
        raise RuntimeError("writeback forbidden")

    monkeypatch.setattr(e0_runner, "_write_evidence_table_rows", _writer)

    with pytest.raises(RuntimeError, match="writeback forbidden"):
        run_case_example_a_e0(
            case_path=case_path,
            artifacts_dir=tmp_path / "artifacts",
            writeback_evidence_table=True,
            evidence_table_path=tmp_path / "evidence_table.parquet",
        )
    assert called["value"] is True


def test_mineru_llm_success_with_precomputed_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    case = _base_case("CASE-E0-7")
    pdf = tmp_path / "paper.pdf"
    pdf.write_text("dummy pdf", encoding="utf-8")
    case["inputs"]["offline_pdfs"] = [{"path_or_id": str(pdf)}]
    case_path = tmp_path / "case.json"
    _write_json(case_path, case)

    output_root = _make_precomputed_mineru_output(tmp_path, pdf, stem="2001")

    def _fake_extract_candidates_with_llm(**kwargs):
        return {
            "candidates": [
                {
                    "value": 610,
                    "unit": "nm",
                    "condition": "thin film",
                    "source_locator": "Table 1",
                    "page": 2,
                    "value_source_kind": "table",
                    "identity_match": "matched",
                    "identity_match_confidence": 0.95,
                    "confidence": 0.9,
                    "bbox": None,
                },
                {
                    "value": 540,
                    "unit": "nm",
                    "condition": "aggregate in poor solvent",
                    "source_locator": "Fig 2",
                    "page": 3,
                    "value_source_kind": "figure",
                    "identity_match": "matched",
                    "identity_match_confidence": 0.95,
                    "confidence": 0.85,
                    "bbox": None,
                },
            ],
            "warnings": [],
            "request": {"model": "mock"},
            "response": {"output": []},
            "md_excerpt": "mock",
            "content_list_v2_excerpt": [],
            "llm_prompt_version": "mock",
            "llm_schema_version": "mock",
        }

    monkeypatch.setattr(e0_runner, "extract_candidates_with_llm", _fake_extract_candidates_with_llm)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    summary = run_case_example_a_e0(
        case_path=case_path,
        artifacts_dir=tmp_path / "artifacts",
        extractor_mode="mineru_llm",
        mineru_output_root=output_root,
        mineru_bin=str(tmp_path / "does-not-matter"),
        evidence_table_path=tmp_path / "evidence_table.parquet",
    )
    case_after = json.loads(case_path.read_text(encoding="utf-8"))
    run_dir = Path(summary["artifacts_dir"])

    assert summary["gate_state"] == "ready_for_reasoning"
    assert case_after["target_fields"]["emission_solid_or_film_nm"] == 610
    assert case_after["target_fields"]["emission_aggr_nm"] == 540
    assert case_after["case_sections"]["for_master_reasoning"]["current_gate"]["state"] == "ready_for_reasoning"
    assert len(case_after["case_sections"]["update_history"]["history"]) == len(case_after["history"])
    assert case_after["evidence_acquire"]["emission"]["extractor_mode"] == "mineru_llm"
    assert case_after["evidence_acquire"]["emission"]["llm_prompt_version"] == "mineru_llm_prompt_v1"
    assert (run_dir / "01a_mineru_inputs.json").exists()
    assert (run_dir / "01b_mineru_md_excerpt.txt").exists()
    assert (run_dir / "01c_mineru_content_list_v2_excerpt.json").exists()
    assert (run_dir / "01d_llm_request.json").exists()
    assert (run_dir / "01e_llm_response_raw.json").exists()


def test_mineru_llm_fails_when_mineru_not_available(tmp_path: Path):
    case = _base_case("CASE-E0-8")
    pdf = tmp_path / "paper.pdf"
    pdf.write_text("dummy pdf", encoding="utf-8")
    case["inputs"]["offline_pdfs"] = [{"path_or_id": str(pdf)}]
    case_path = tmp_path / "case.json"
    _write_json(case_path, case)

    summary = run_case_example_a_e0(
        case_path=case_path,
        artifacts_dir=tmp_path / "artifacts",
        extractor_mode="mineru_llm",
        mineru_output_root=tmp_path / "empty_output",
        mineru_bin=str(tmp_path / "missing-mineru-bin"),
        evidence_table_path=tmp_path / "evidence_table.parquet",
    )

    assert summary["gate_state"] == "failed_extract"


def test_mineru_llm_failed_extract_on_llm_schema_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    case = _base_case("CASE-E0-9")
    pdf = tmp_path / "paper.pdf"
    pdf.write_text("dummy pdf", encoding="utf-8")
    case["inputs"]["offline_pdfs"] = [{"path_or_id": str(pdf)}]
    case_path = tmp_path / "case.json"
    _write_json(case_path, case)

    output_root = _make_precomputed_mineru_output(tmp_path, pdf, stem="2002")

    def _raise_schema_error(**kwargs):
        raise MineruLLMExtractorError("llm_schema_invalid:bad_json")

    monkeypatch.setattr(e0_runner, "extract_candidates_with_llm", _raise_schema_error)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    summary = run_case_example_a_e0(
        case_path=case_path,
        artifacts_dir=tmp_path / "artifacts",
        extractor_mode="mineru_llm",
        mineru_output_root=output_root,
        mineru_bin=str(tmp_path / "unused"),
        evidence_table_path=tmp_path / "evidence_table.parquet",
    )

    assert summary["gate_state"] == "failed_extract"


def test_mineru_llm_extracted_no_writeback_when_all_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    case = _base_case("CASE-E0-10")
    pdf = tmp_path / "paper.pdf"
    pdf.write_text("dummy pdf", encoding="utf-8")
    case["inputs"]["offline_pdfs"] = [{"path_or_id": str(pdf)}]
    case_path = tmp_path / "case.json"
    _write_json(case_path, case)

    output_root = _make_precomputed_mineru_output(tmp_path, pdf, stem="2003")

    def _fake_extract_rejected(**kwargs):
        return {
            "candidates": [
                {
                    "value": 520,
                    "unit": "nm",
                    "condition": "solution state",
                    "source_locator": "text paragraph",
                    "page": 2,
                    "value_source_kind": "text",
                    "identity_match": "matched",
                    "identity_match_confidence": 0.8,
                    "confidence": 0.8,
                    "bbox": None,
                }
            ],
            "warnings": [],
            "request": {"model": "mock"},
            "response": {"output": []},
            "md_excerpt": "mock",
            "content_list_v2_excerpt": [],
            "llm_prompt_version": "mock",
            "llm_schema_version": "mock",
        }

    monkeypatch.setattr(e0_runner, "extract_candidates_with_llm", _fake_extract_rejected)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    summary = run_case_example_a_e0(
        case_path=case_path,
        artifacts_dir=tmp_path / "artifacts",
        extractor_mode="mineru_llm",
        mineru_output_root=output_root,
        mineru_bin=str(tmp_path / "unused"),
        evidence_table_path=tmp_path / "evidence_table.parquet",
    )
    case_after = json.loads(case_path.read_text(encoding="utf-8"))

    assert summary["gate_state"] == "extracted_no_writeback"
    assert case_after["evidence_candidates_staging"][0]["verification_status"] == "rejected"
