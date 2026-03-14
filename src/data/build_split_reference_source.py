"""
Build a unified split-level reference source from split_list/1~3_level CSV files.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Mapping

import pandas as pd


DEFAULT_SPLIT_FILES = [
    (1, "1_level.csv"),
    (2, "2_level.csv"),
    (3, "3_level.csv"),
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_split_csv(path: Path, difficulty_level: int) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = df.copy()
    if "mechanism_id" in df.columns:
        labels = df["mechanism_id"].astype(str).str.strip().str.lower()
        if (labels == "other").any():
            raise ValueError(f"split_file_contains_other_label:{path}")
    df["difficulty_level"] = int(difficulty_level)
    if "source_split_file" not in df.columns:
        df["source_split_file"] = path.name
    if "source_row_index" not in df.columns:
        df["source_row_index"] = range(len(df))
    return df


def build_split_reference_source(
    *,
    split_dir: str | Path = "data/split_list",
    output_root: str | Path = "data/reference_indices/split_levels_v2",
    split_files: Iterable[tuple[int, str]] = DEFAULT_SPLIT_FILES,
) -> Mapping[str, str]:
    split_root = Path(split_dir)
    out_root = Path(output_root)
    sources_dir = out_root / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)

    frames: List[pd.DataFrame] = []
    per_level_counts: dict[str, int] = {}
    for level, filename in split_files:
        path = split_root / filename
        if not path.exists():
            raise FileNotFoundError(f"split_file_not_found:{path}")
        frame = _load_split_csv(path, int(level))
        frames.append(frame)
        per_level_counts[str(level)] = int(len(frame))

    merged = pd.concat(frames, ignore_index=True)
    out_path = sources_dir / "all_levels_reference.parquet"
    merged.to_parquet(out_path, index=False)

    manifest = {
        "generated_at": _now_iso(),
        "split_dir": str(split_root),
        "output_root": str(out_root),
        "source_files": [{"difficulty_level": int(level), "file": filename} for level, filename in split_files],
        "rows_total": int(len(merged)),
        "rows_per_level": per_level_counts,
        "output_parquet": str(out_path),
    }
    manifest_path = sources_dir / "build_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "all_levels_reference_parquet": str(out_path),
        "manifest_path": str(manifest_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build unified split-level reference parquet.")
    parser.add_argument("--split-dir", type=str, default="data/split_list")
    parser.add_argument("--output-root", type=str, default="data/reference_indices/split_levels_v2")
    args = parser.parse_args()
    result = build_split_reference_source(
        split_dir=args.split_dir,
        output_root=args.output_root,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
