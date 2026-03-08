import json
from pathlib import Path

from src.core.output_layout import OUTPUT_LAYOUT_CASE_CENTRIC, plan_output_layout, write_legacy_pointers


def test_legacy_pointer_written_for_case_centric_layout(tmp_path: Path):
    paths = plan_output_layout(
        artifacts_root=tmp_path / "artifacts",
        llm_response_root=tmp_path / "artifacts" / "llm_responses",
        case_id="CASE-LG",
        run_id="1234567890abcdef1234567890abcdef",
        layout=OUTPUT_LAYOUT_CASE_CENTRIC,
        timestamp_format="utc_compact",
        write_legacy_run_view=True,
    )
    pointer = {
        "run_id": paths.run_id,
        "case_id": paths.case_id,
        "primary_output_dir": str(paths.run_dir),
    }
    out = write_legacy_pointers(paths=paths, pointer_payload=pointer)

    run_ptr = Path(str(out["legacy_run_summary"]))
    llm_ptr = Path(str(out["legacy_llm_pointer"]))
    assert run_ptr.exists()
    assert llm_ptr.exists()
    assert json.loads(run_ptr.read_text(encoding="utf-8"))["primary_output_dir"] == str(paths.run_dir)
    assert json.loads(llm_ptr.read_text(encoding="utf-8"))["run_id"] == paths.run_id
