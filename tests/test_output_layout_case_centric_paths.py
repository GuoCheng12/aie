import re
from pathlib import Path

from src.core.output_layout import OUTPUT_LAYOUT_CASE_CENTRIC, plan_output_layout


def test_case_centric_path_layout(tmp_path: Path):
    paths = plan_output_layout(
        artifacts_root=tmp_path / "artifacts",
        llm_response_root=tmp_path / "artifacts" / "llm_responses",
        case_id="CASE-123",
        run_id="abcdef1234567890abcdef1234567890",
        layout=OUTPUT_LAYOUT_CASE_CENTRIC,
        timestamp_format="utc_compact",
        write_legacy_run_view=True,
    )

    assert re.match(r"^\d{8}T\d{6}Z__abcdef12$", paths.run_name)
    assert paths.case_root == tmp_path / "artifacts" / "cases" / "CASE-123"
    assert paths.run_dir == paths.case_root / "runs" / paths.run_name
    assert paths.llm_run_dir == paths.run_dir / "llm"
    assert paths.rounds_dir == paths.run_dir / "rounds"
    assert paths.latest_dir == paths.case_root / "latest"
    assert paths.history_index_path == paths.case_root / "history_index.json"
