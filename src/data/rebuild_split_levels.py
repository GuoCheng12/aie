"""
Rebuild split_list into three difficulty levels and export removed `other` rows.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import pandas as pd


LEGACY_SPLIT_FILES: Tuple[Tuple[int, str], ...] = (
    (1, "1_level.csv"),
    (2, "2_level.csv"),
    (3, "3_level.csv"),
    (4, "4_level.csv"),
)

LEVEL_REMAP = {
    1: 1,
    2: 2,
    3: 2,
    4: 3,
}

MAIN_SPLIT_COLUMNS = [
    "id",
    "code",
    "SMILES",
    "reference",
    "molecular_weight",
    "emission_solid",
    "emission_aggr",
    "features_id",
    "mechanism_id",
    "doi",
    "aTB",
    "difficulty_level",
    "original_level",
    "source_split_file",
    "source_row_index",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_legacy_splits(source_dir: Path) -> pd.DataFrame:
    frames: List[pd.DataFrame] = []
    for original_level, filename in LEGACY_SPLIT_FILES:
        path = source_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"legacy_split_file_not_found:{path}")
        frame = pd.read_csv(path).copy()
        frame["original_level"] = int(original_level)
        frame["difficulty_level"] = int(LEVEL_REMAP[int(original_level)])
        frame["source_split_file"] = filename
        frame["source_row_index"] = range(len(frame))
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def _write_split_csv(df: pd.DataFrame, output_path: Path) -> int:
    ordered = df.copy()
    for col in MAIN_SPLIT_COLUMNS:
        if col not in ordered.columns:
            ordered[col] = None
    ordered = ordered[MAIN_SPLIT_COLUMNS]
    ordered.to_csv(output_path, index=False)
    return int(len(ordered))


def rebuild_split_levels(
    *,
    source_dir: str | Path = "data/split_list_legacy_v1",
    output_dir: str | Path = "data/split_list",
    other_benchmark_path: str | Path = "data/other_benchmark.csv",
) -> Dict[str, object]:
    source_root = Path(source_dir)
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    benchmark_path = Path(other_benchmark_path)
    benchmark_path.parent.mkdir(parents=True, exist_ok=True)

    merged = _load_legacy_splits(source_root)
    merged["mechanism_id"] = merged["mechanism_id"].astype(str).str.strip()

    other_mask = merged["mechanism_id"].str.lower() == "other"
    other_rows = merged.loc[other_mask].copy()
    non_other_rows = merged.loc[~other_mask].copy()

    rows_per_level: Dict[str, int] = {}
    labels_per_level: Dict[str, Dict[str, int]] = {}
    for new_level in (1, 2, 3):
        level_rows = non_other_rows.loc[non_other_rows["difficulty_level"] == new_level].copy()
        output_path = output_root / f"{new_level}_level.csv"
        rows_per_level[str(new_level)] = _write_split_csv(level_rows, output_path)
        labels_per_level[str(new_level)] = {
            str(k): int(v)
            for k, v in level_rows["mechanism_id"].value_counts(dropna=False).sort_index().to_dict().items()
        }

    benchmark_rows = other_rows.copy()
    benchmark_rows["new_level"] = benchmark_rows["difficulty_level"].astype("Int64")
    benchmark_rows.to_csv(benchmark_path, index=False)

    manifest = {
        "generated_at": _now_iso(),
        "source_dir": str(source_root),
        "output_dir": str(output_root),
        "other_benchmark_path": str(benchmark_path),
        "legacy_source_files": [filename for _, filename in LEGACY_SPLIT_FILES],
        "level_remap": {str(k): int(v) for k, v in LEVEL_REMAP.items()},
        "rows_per_level": rows_per_level,
        "labels_per_level": labels_per_level,
        "other_benchmark_rows": int(len(other_rows)),
        "other_rows_per_original_level": {
            str(k): int(v)
            for k, v in other_rows["original_level"].value_counts(dropna=False).sort_index().to_dict().items()
        },
    }
    manifest_path = output_root / "build_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "output_dir": str(output_root),
        "other_benchmark_path": str(benchmark_path),
        "manifest_path": str(manifest_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild split_list into three levels and export other_benchmark.")
    parser.add_argument("--source-dir", type=str, default="data/split_list_legacy_v1")
    parser.add_argument("--output-dir", type=str, default="data/split_list")
    parser.add_argument("--other-benchmark-path", type=str, default="data/other_benchmark.csv")
    args = parser.parse_args()
    result = rebuild_split_levels(
        source_dir=args.source_dir,
        output_dir=args.output_dir,
        other_benchmark_path=args.other_benchmark_path,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
