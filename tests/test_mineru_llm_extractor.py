import json
import shutil
from pathlib import Path

from src.cases.mineru_llm_extractor import (
    _infer_page_from_content_list,
    build_mineru_prompt_payload,
    parse_llm_candidates,
    resolve_or_run_mineru,
)


def _write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def test_parse_llm_candidates_filters_and_normalizes():
    payload = {
        "candidates": [
            {
                "value": "610",
                "unit": "nm",
                "condition": "thin film",
                "source_locator": "Table 1",
                "page": 2,
                "value_source_kind": "table",
                "identity_match": "matched",
                "identity_match_confidence": 1.2,
                "confidence": -0.3,
                "bbox": {"x": 1, "y": 2},
            },
            {
                "value": {"bad": "value"},
                "unit": "nm",
                "condition": "aggregate",
                "source_locator": "Fig 2",
                "page": 3,
                "value_source_kind": "figure",
                "identity_match": "matched",
                "identity_match_confidence": 0.9,
                "confidence": 0.9,
                "bbox": None,
            },
            {
                "value": 540,
                "unit": None,
                "condition": None,
                "source_locator": None,
                "page": "4",
                "value_source_kind": "unknown_kind",
                "identity_match": "not_known",
                "identity_match_confidence": None,
                "confidence": None,
                "bbox": "bad",
            },
        ]
    }

    result = parse_llm_candidates(payload)
    candidates = result["candidates"]

    assert len(candidates) == 2
    assert candidates[0]["identity_match_confidence"] == 1.0
    assert candidates[0]["confidence"] == 0.0
    assert candidates[1]["unit"] == "nm"
    assert candidates[1]["value_source_kind"] == "text"
    assert candidates[1]["identity_match"] == "ambiguous"
    assert candidates[1]["bbox"] is None
    assert any(w.startswith("candidate_invalid_value") for w in result["warnings"])


def test_parse_llm_candidates_accepts_legacy_emission_payload():
    payload = {
        "identity_match": "code_match",
        "identity_match_confidence": 0.87,
        "emission_aggr_nm": 545,
        "emission_solid_or_film_nm": 612,
        "evidence": {
            "aggr_source": "Fig. 2, page 4",
            "solid_or_film_source": "Table 1, page 3",
            "conditions_aggr": "THF/water fw=90%",
            "conditions_solid_or_film": "spin-coated film",
        },
    }

    result = parse_llm_candidates(payload)
    assert "llm_schema_legacy_payload" in result["warnings"]
    assert len(result["candidates"]) == 2

    solid = next(c for c in result["candidates"] if "film" in c["condition"])
    aggr = next(c for c in result["candidates"] if "fw=90%" in c["condition"])

    assert solid["value"] == 612
    assert solid["value_source_kind"] == "table"
    assert solid["page"] == 3
    assert solid["identity_match"] == "matched"

    assert aggr["value"] == 545
    assert aggr["value_source_kind"] == "figure"
    assert aggr["page"] == 4


def test_parse_llm_candidates_infers_page_from_locator_when_missing():
    payload = {
        "candidates": [
            {
                "value": 578,
                "unit": "nm",
                "condition": "powder",
                "source_locator": "Fig. 2 caption (page 6)",
                "page": None,
                "value_source_kind": "figure",
                "identity_match": "matched",
                "identity_match_confidence": 0.95,
                "confidence": 0.9,
                "bbox": None,
            }
        ]
    }
    result = parse_llm_candidates(payload)
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["page"] == 6
    assert "candidate_page_inferred_from_locator:0" in result["warnings"]


def test_infer_page_from_content_list_with_figure_locator():
    page = _infer_page_from_content_list(
        "Fig. 2, text describing solid-state emission",
        [
            {"page": 4, "type": "text", "text": "Introduction"},
            {"page": 7, "type": "figure", "text": "Fig. 2 Emission spectra in different states"},
        ],
    )
    assert page == 7


def test_resolve_or_run_mineru_uses_existing_outputs(tmp_path: Path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_text("pdf-bytes", encoding="utf-8")

    output_root = tmp_path / "output"
    run_dir = output_root / "3001" / "hybrid_auto"
    run_dir.mkdir(parents=True, exist_ok=True)

    shutil.copyfile(pdf, run_dir / "3001_origin.pdf")
    (run_dir / "3001.md").write_text("Table 1: film 610 nm", encoding="utf-8")
    _write_json(
        run_dir / "3001_content_list_v2.json",
        [{"page_idx": 1, "type": "table", "text": "film 610 nm"}],
    )

    meta = resolve_or_run_mineru(
        pdf_path=pdf,
        output_root=output_root,
        mineru_bin=str(tmp_path / "missing-mineru-bin"),
        backend="hybrid-auto-engine",
        method=None,
        lang=None,
        start_page=None,
        end_page=None,
        timeout_sec=5,
    )

    assert meta["resolve_mode"] == "existing"
    assert Path(meta["md_path"]).exists()
    assert Path(meta["content_list_v2_path"]).exists()
    assert isinstance(meta["mineru_output_hash"], str)
    assert len(meta["mineru_output_hash"]) == 64


def test_resolve_or_run_mineru_existing_stem_fallback_when_hash_mismatch(tmp_path: Path):
    pdf = tmp_path / "DMA-AM.pdf"
    pdf.write_text("input-pdf-bytes", encoding="utf-8")

    output_root = tmp_path / "output"
    run_dir = output_root / "DMA-AM" / "hybrid_auto"
    run_dir.mkdir(parents=True, exist_ok=True)
    # Intentionally different bytes from input PDF.
    (run_dir / "DMA-AM_origin.pdf").write_text("rewritten-origin-bytes", encoding="utf-8")
    (run_dir / "DMA-AM.md").write_text("Table 1: film 610 nm", encoding="utf-8")
    _write_json(
        run_dir / "DMA-AM_content_list_v2.json",
        [{"page_idx": 1, "type": "table", "text": "film 610 nm"}],
    )

    meta = resolve_or_run_mineru(
        pdf_path=pdf,
        output_root=output_root,
        mineru_bin=str(tmp_path / "missing-mineru-bin"),
        backend="hybrid-auto-engine",
        method=None,
        lang=None,
        start_page=None,
        end_page=None,
        timeout_sec=5,
    )

    assert meta["resolve_mode"] == "existing_stem_fallback"
    assert meta["source_binding"] == "stem_fallback_no_hash_match"
    assert Path(meta["md_path"]).exists()
    assert Path(meta["content_list_v2_path"]).exists()


def test_resolve_or_run_mineru_cli_stem_fallback_when_origin_pdf_rewritten(
    tmp_path: Path, monkeypatch
):
    pdf = tmp_path / "DMA-AM.pdf"
    pdf.write_text("original-pdf-bytes", encoding="utf-8")

    output_root = tmp_path / "output"
    run_dir = output_root / "DMA-AM" / "hybrid_auto"
    run_dir.mkdir(parents=True, exist_ok=True)
    # Simulate MinerU rewritten/canonicalized origin pdf bytes that do not
    # match the original input file hash.
    (run_dir / "DMA-AM_origin.pdf").write_text("rewritten-pdf-bytes", encoding="utf-8")
    (run_dir / "DMA-AM.md").write_text("Table 1: film 610 nm", encoding="utf-8")
    _write_json(
        run_dir / "DMA-AM_content_list_v2.json",
        [{"page_idx": 1, "type": "table", "text": "film 610 nm"}],
    )

    def _unexpected_run(*args, **kwargs):
        raise AssertionError("subprocess.run should not be called for existing stem fallback")

    monkeypatch.setattr("subprocess.run", _unexpected_run)

    meta = resolve_or_run_mineru(
        pdf_path=pdf,
        output_root=output_root,
        mineru_bin="python",
        backend="hybrid-auto-engine",
        method=None,
        lang=None,
        start_page=None,
        end_page=None,
        timeout_sec=5,
    )

    assert meta["resolve_mode"] == "existing_stem_fallback"
    assert meta["source_binding"] == "stem_fallback_no_hash_match"
    assert Path(meta["md_path"]).exists()
    assert Path(meta["content_list_v2_path"]).exists()


def test_build_mineru_prompt_payload_uses_template_runtime_target():
    prompt = build_mineru_prompt_payload(
        case_context={
            "case_id": "CASE-1",
            "code": "DMA-AM",
            "canonical_smiles": "CN(C)c1ccc(/C=N/C(C#N)=C(N)/C#N)cc1",
            "inchikey": "QPBXVINHXQCKKI-BMPCYJNPSA-N",
        },
        source_ref="data/pdfs/DMA-AM.pdf",
        md_text="Table 1 reports thin film 610 nm.",
        content_list_excerpt=[{"page": 2, "type": "table", "text": "610 nm"}],
    )

    assert "You are extracting photophysical emission peak data" in prompt
    assert 'TARGET_CODE = "DMA-AM"' in prompt
    assert 'TARGET_INCHIKEY = "QPBXVINHXQCKKI-BMPCYJNPSA-N"' in prompt
    assert "MinerU markdown excerpt" in prompt
    assert "provide `page` as an integer PDF page number whenever possible" in prompt
