"""
Materialize leakage-safe reference views from all_levels_reference.parquet.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional

import pandas as pd

from src.data.pipeline import run_p1_pipeline
from src.features.anchor_ecfp import build_anchor_neighbors
from src.reasoning.r0_prior_profiles import MAIN_PRIOR_LABELS
from src.structure.build_structure_reference_pool import build_structure_reference_pool
from src.uq.mechanism_label_map import run_build_label_map


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


VIEW_DEFINITIONS: Dict[str, Optional[int]] = {
    "all_levels_full": None,
    "leave_level_1": 1,
    "leave_level_2": 2,
    "leave_level_3": 3,
}


def _build_one_view(
    *,
    source_df: pd.DataFrame,
    view_name: str,
    excluded_level: Optional[int],
    views_root: Path,
    neighbor_k: int,
) -> Mapping[str, str]:
    if excluded_level is None:
        view_df = source_df.copy()
    else:
        view_df = source_df[source_df["difficulty_level"].astype("Int64") != int(excluded_level)].copy()

    view_dir = views_root / view_name
    view_dir.mkdir(parents=True, exist_ok=True)
    view_source_path = view_dir / "input_source.parquet"
    view_df.to_parquet(view_source_path, index=False)

    run_p1_pipeline(
        input_parquet=str(view_source_path),
        output_dir=str(view_dir),
        fact_schema_version="v2026-03-10-split-levels",
    )

    label_map_path = view_dir / "mechanism_label_map.parquet"
    run_build_label_map(
        private_clean_path=str(view_dir / "private_clean.parquet"),
        output_path=str(label_map_path),
        source_view=view_name,
    )
    main_prior_label_map_path = view_dir / "mechanism_label_map_main_prior.parquet"
    run_build_label_map(
        private_clean_path=str(view_dir / "private_clean.parquet"),
        output_path=str(main_prior_label_map_path),
        source_view=view_name,
        allowed_labels=MAIN_PRIOR_LABELS,
    )

    neighbors_path = view_dir / "anchor_neighbors_ecfp.parquet"
    build_anchor_neighbors(
        output_path=str(neighbors_path),
        rdkit_features_path=str(view_dir / "rdkit_features.parquet"),
        k=int(neighbor_k),
    )

    structure_pool_path = view_dir / "structure_reference_pool.parquet"
    pool_df = build_structure_reference_pool(
        molecule_table_path=str(view_dir / "molecule_table.parquet"),
        mechanism_label_map_path=str(label_map_path),
        rdkit_features_path=str(view_dir / "rdkit_features.parquet"),
        output_path=str(structure_pool_path),
    )
    main_prior_structure_pool_path = view_dir / "structure_reference_pool_main_prior.parquet"
    main_prior_pool_df = build_structure_reference_pool(
        molecule_table_path=str(view_dir / "molecule_table.parquet"),
        mechanism_label_map_path=str(main_prior_label_map_path),
        rdkit_features_path=str(view_dir / "rdkit_features.parquet"),
        output_path=str(main_prior_structure_pool_path),
    )

    manifest = {
        "generated_at": _now_iso(),
        "view_name": view_name,
        "excluded_level": excluded_level,
        "neighbor_k": int(neighbor_k),
        "rows_input": int(len(view_df)),
        "rows_pool": int(len(pool_df)),
        "rows_main_prior_pool": int(len(main_prior_pool_df)),
        "artifacts": {
            "private_clean": str(view_dir / "private_clean.parquet"),
            "molecule_table": str(view_dir / "molecule_table.parquet"),
            "rdkit_features": str(view_dir / "rdkit_features.parquet"),
            "mechanism_label_map": str(label_map_path),
            "mechanism_label_map_main_prior": str(main_prior_label_map_path),
            "anchor_neighbors_ecfp": str(neighbors_path),
            "structure_reference_pool": str(structure_pool_path),
            "structure_reference_pool_main_prior": str(main_prior_structure_pool_path),
        },
    }
    manifest_path = view_dir / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"view_name": view_name, "view_dir": str(view_dir), "manifest_path": str(manifest_path)}


def build_split_reference_views(
    *,
    source_parquet: str | Path = "data/reference_indices/split_levels_v2/sources/all_levels_reference.parquet",
    output_root: str | Path = "data/reference_indices/split_levels_v2",
    neighbor_k: int = 10,
    view_names: Optional[Iterable[str]] = None,
) -> Mapping[str, object]:
    src_path = Path(source_parquet)
    if not src_path.exists():
        raise FileNotFoundError(f"source_parquet_not_found:{src_path}")

    source_df = pd.read_parquet(src_path)
    if "difficulty_level" not in source_df.columns:
        raise ValueError("source_missing_difficulty_level")

    out_root = Path(output_root)
    views_root = out_root / "views"
    views_root.mkdir(parents=True, exist_ok=True)

    selected_views = list(view_names or VIEW_DEFINITIONS.keys())
    results = []
    for view_name in selected_views:
        if view_name not in VIEW_DEFINITIONS:
            raise ValueError(f"unsupported_view:{view_name}")
        result = _build_one_view(
            source_df=source_df,
            view_name=view_name,
            excluded_level=VIEW_DEFINITIONS[view_name],
            views_root=views_root,
            neighbor_k=neighbor_k,
        )
        results.append(result)

    summary = {
        "generated_at": _now_iso(),
        "source_parquet": str(src_path),
        "output_root": str(out_root),
        "views": results,
    }
    summary_path = out_root / "views_manifest.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"views_manifest": str(summary_path), "views_built": [row["view_name"] for row in results]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build split-level reference views.")
    parser.add_argument(
        "--source-parquet",
        type=str,
        default="data/reference_indices/split_levels_v2/sources/all_levels_reference.parquet",
    )
    parser.add_argument("--output-root", type=str, default="data/reference_indices/split_levels_v2")
    parser.add_argument("--neighbor-k", type=int, default=10)
    parser.add_argument(
        "--views",
        type=str,
        default="all_levels_full,leave_level_1,leave_level_2,leave_level_3",
        help="Comma-separated view list",
    )
    args = parser.parse_args()
    views = [v.strip() for v in str(args.views).split(",") if v.strip()]
    result = build_split_reference_views(
        source_parquet=args.source_parquet,
        output_root=args.output_root,
        neighbor_k=int(args.neighbor_k),
        view_names=views,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
