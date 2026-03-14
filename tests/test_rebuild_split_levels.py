import json
from pathlib import Path

import pandas as pd

from src.data.rebuild_split_levels import rebuild_split_levels


def _write_level(path: Path, level: int, labels: list[str]) -> None:
    rows = []
    for idx, label in enumerate(labels):
        rows.append(
            {
                "id": level * 100 + idx,
                "code": f"L{level}_{idx}",
                "SMILES": "CCO",
                "reference": "ref",
                "molecular_weight": 100.0,
                "emission_solid": 500.0,
                "emission_aggr": 510.0,
                "features_id": level * 100 + idx,
                "mechanism_id": label,
                "doi": "10.1/test",
                "aTB": "success",
            }
        )
    pd.DataFrame(rows).to_csv(path, index=False)


def test_rebuild_split_levels_exports_other_benchmark(tmp_path: Path) -> None:
    legacy_dir = tmp_path / "split_list_legacy_v1"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    _write_level(legacy_dir / "1_level.csv", 1, ["ICT", "other"])
    _write_level(legacy_dir / "2_level.csv", 2, ["TICT", "other"])
    _write_level(legacy_dir / "3_level.csv", 3, ["ESIPT", "other"])
    _write_level(legacy_dir / "4_level.csv", 4, ["neutral aromatic", "other"])

    out_dir = tmp_path / "split_list"
    benchmark_path = tmp_path / "other_benchmark.csv"
    result = rebuild_split_levels(
        source_dir=legacy_dir,
        output_dir=out_dir,
        other_benchmark_path=benchmark_path,
    )

    level1 = pd.read_csv(out_dir / "1_level.csv")
    level2 = pd.read_csv(out_dir / "2_level.csv")
    level3 = pd.read_csv(out_dir / "3_level.csv")
    other = pd.read_csv(benchmark_path)
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))

    assert set(level1["mechanism_id"]) == {"ICT"}
    assert set(level2["mechanism_id"]) == {"TICT", "ESIPT"}
    assert set(level3["mechanism_id"]) == {"neutral aromatic"}
    assert "other" not in set(level1["mechanism_id"]) | set(level2["mechanism_id"]) | set(level3["mechanism_id"])
    assert set(other["mechanism_id"]) == {"other"}
    assert other["new_level"].astype(int).tolist() == [1, 2, 2, 3]
    assert manifest["rows_per_level"] == {"1": 1, "2": 2, "3": 1}
    assert manifest["other_benchmark_rows"] == 4
