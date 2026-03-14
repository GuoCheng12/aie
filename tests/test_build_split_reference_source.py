import json
from pathlib import Path

import pandas as pd
import pytest

from src.data.build_split_reference_source import build_split_reference_source


def _write_split_csv(path: Path, start_id: int, n_rows: int) -> None:
    rows = []
    for idx in range(n_rows):
        rows.append(
            {
                "id": start_id + idx,
                "code": f"C{start_id + idx}",
                "SMILES": "CCO",
                "reference": "ref",
                "molecular_weight": 100.0 + idx,
                "emission_solid": 500 + idx,
                "emission_aggr": 510 + idx,
                "features_id": start_id + idx,
                "mechanism_id": "ICT",
                "doi": "10.1/test",
                "aTB": "success",
            }
        )
    pd.DataFrame(rows).to_csv(path, index=False)


def test_build_split_reference_source(tmp_path: Path) -> None:
    split_dir = tmp_path / "split_list"
    split_dir.mkdir(parents=True, exist_ok=True)
    _write_split_csv(split_dir / "1_level.csv", 1, 2)
    _write_split_csv(split_dir / "2_level.csv", 100, 1)
    _write_split_csv(split_dir / "3_level.csv", 200, 2)

    output_root = tmp_path / "reference_indices" / "split_levels_v2"
    result = build_split_reference_source(split_dir=split_dir, output_root=output_root)

    out_path = Path(result["all_levels_reference_parquet"])
    manifest_path = Path(result["manifest_path"])
    assert out_path.exists()
    assert manifest_path.exists()

    merged = pd.read_parquet(out_path)
    assert len(merged) == 5
    assert set(["difficulty_level", "source_split_file", "source_row_index"]).issubset(merged.columns)
    assert set(merged["difficulty_level"].astype(int).tolist()) == {1, 2, 3}

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["rows_total"] == 5
    assert manifest["rows_per_level"] == {"1": 2, "2": 1, "3": 2}


def test_build_split_reference_source_rejects_other_label(tmp_path: Path) -> None:
    split_dir = tmp_path / "split_list"
    split_dir.mkdir(parents=True, exist_ok=True)
    _write_split_csv(split_dir / "1_level.csv", 1, 1)
    _write_split_csv(split_dir / "2_level.csv", 2, 1)
    bad = pd.DataFrame(
        [
            {
                "id": 3,
                "code": "C3",
                "SMILES": "CCO",
                "reference": "ref",
                "molecular_weight": 100.0,
                "emission_solid": 500.0,
                "emission_aggr": 510.0,
                "features_id": 3,
                "mechanism_id": "other",
                "doi": "10.1/test",
                "aTB": "success",
            }
        ]
    )
    bad.to_csv(split_dir / "3_level.csv", index=False)

    with pytest.raises(ValueError, match="split_file_contains_other_label"):
        build_split_reference_source(split_dir=split_dir, output_root=tmp_path / "reference_indices")


def test_build_split_reference_source_preserves_existing_source_provenance(tmp_path: Path) -> None:
    split_dir = tmp_path / "split_list"
    split_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(
        [
            {
                "id": 1,
                "code": "A",
                "SMILES": "CCO",
                "reference": "ref",
                "molecular_weight": 100.0,
                "emission_solid": 500.0,
                "emission_aggr": 510.0,
                "features_id": 1,
                "mechanism_id": "ICT",
                "doi": "10.1/test",
                "aTB": "success",
                "difficulty_level": 2,
                "source_split_file": "3_level.csv",
                "source_row_index": 7,
                "original_level": 3,
            }
        ]
    )
    df.to_csv(split_dir / "1_level.csv", index=False)
    _write_split_csv(split_dir / "2_level.csv", 2, 1)
    _write_split_csv(split_dir / "3_level.csv", 3, 1)

    result = build_split_reference_source(split_dir=split_dir, output_root=tmp_path / "reference_indices")
    merged = pd.read_parquet(result["all_levels_reference_parquet"])
    row = merged.loc[merged["code"] == "A"].iloc[0]
    assert row["difficulty_level"] == 1
    assert row["source_split_file"] == "3_level.csv"
    assert int(row["source_row_index"]) == 7
