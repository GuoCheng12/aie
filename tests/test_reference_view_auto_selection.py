import argparse
from pathlib import Path

from src.orchestration import run_one as run_one_mod


def test_reference_view_auto_selects_leave_level() -> None:
    args = argparse.Namespace(
        reference_view="auto",
        reference_index_root="data/reference_indices/split_levels_v2/views",
        smiles=None,
    )
    row = {"SMILES": "CCO", "difficulty_level": 3}
    view, level = run_one_mod._resolve_reference_view(args, row)
    assert view == "leave_level_3"
    assert level == 3


def test_reference_view_auto_for_smiles_defaults_all_levels(tmp_path: Path) -> None:
    root = tmp_path / "views"
    (root / "all_levels_full").mkdir(parents=True, exist_ok=True)
    args = argparse.Namespace(
        reference_view="auto",
        reference_index_root=str(root),
        smiles="CCO",
    )
    row = {"SMILES": "CCO"}
    data_dir, view, level = run_one_mod._resolve_reference_data_dir(args, row)
    assert data_dir == root / "all_levels_full"
    assert view == "all_levels_full"
    assert level is None
