"""
Build structure_reference_pool.parquet for StructureAgent runtime retrieval.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

import pandas as pd

from src.reasoning.structure_prior_profile import compute_structure_prior_profile
from src.structure.feature_morgan import compute_feature_morgan_count, compute_morgan_count
from src.structure.motif_detector import detect_structure_motifs
from src.structure.scaffold_retrieval import extract_murcko_scaffold


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _descriptor_snapshot(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "mw": row.get("mw"),
        "logp": row.get("logp"),
        "tpsa": row.get("tpsa"),
        "n_rotatable_bonds": row.get("n_rotatable_bonds"),
        "n_hbd": row.get("n_hbd"),
        "n_hba": row.get("n_hba"),
        "n_rings": row.get("n_rings"),
        "n_aromatic_rings": row.get("n_aromatic_rings"),
        "n_heavy_atoms": row.get("n_heavy_atoms"),
    }


def build_structure_reference_pool(
    *,
    molecule_table_path: str | Path,
    mechanism_label_map_path: str | Path,
    rdkit_features_path: str | Path,
    output_path: str | Path,
) -> pd.DataFrame:
    molecule_df = pd.read_parquet(molecule_table_path)
    label_df = pd.read_parquet(mechanism_label_map_path)
    rdkit_df = pd.read_parquet(rdkit_features_path)

    keep_label_cols = ["inchikey", "mechanism_label"]
    for col in ("difficulty_levels", "source_view"):
        if col in label_df.columns:
            keep_label_cols.append(col)
    label_df = label_df[keep_label_cols].copy()

    merged = (
        molecule_df.merge(label_df, on="inchikey", how="left")
        .merge(rdkit_df, on=["inchikey", "canonical_smiles"], how="left", suffixes=("", "_rdkit"))
    )

    rows = []
    for rec in merged.to_dict(orient="records"):
        smiles = str(rec.get("canonical_smiles") or "").strip()
        inchikey = str(rec.get("inchikey") or "").strip()
        if not smiles or not inchikey:
            continue
        descriptors = _descriptor_snapshot(rec)
        scaffold_info = extract_murcko_scaffold(smiles)
        motif_profile = detect_structure_motifs(smiles, descriptors)
        structure_prior_profile = compute_structure_prior_profile(smiles, descriptors)
        rows.append(
            {
                "inchikey": inchikey,
                "canonical_smiles": smiles,
                "mechanism_label": str(rec.get("mechanism_label") or "unknown"),
                "difficulty_levels": rec.get("difficulty_levels"),
                "morgan_count": _json_dumps(compute_morgan_count(smiles)),
                "feature_morgan_count": _json_dumps(compute_feature_morgan_count(smiles)),
                "murcko_scaffold_smiles": scaffold_info.get("murcko_scaffold_smiles"),
                "generic_scaffold_smiles": scaffold_info.get("generic_scaffold_smiles"),
                "descriptor_snapshot": _json_dumps(descriptors),
                "structure_prior_profile": _json_dumps(structure_prior_profile),
                "structure_motif_profile": _json_dumps(motif_profile),
            }
        )

    out_df = pd.DataFrame(rows)
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_parquet(out_path, index=False)
    return out_df


def main() -> None:
    parser = argparse.ArgumentParser(description="Build structure_reference_pool.parquet")
    parser.add_argument("--molecule-table", type=str, required=True)
    parser.add_argument("--mechanism-label-map", type=str, required=True)
    parser.add_argument("--rdkit-features", type=str, required=True)
    parser.add_argument("--output", type=str, required=True)
    args = parser.parse_args()

    df = build_structure_reference_pool(
        molecule_table_path=args.molecule_table,
        mechanism_label_map_path=args.mechanism_label_map,
        rdkit_features_path=args.rdkit_features,
        output_path=args.output,
    )
    print(json.dumps({"output": args.output, "rows": int(len(df))}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
