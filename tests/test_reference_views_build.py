from pathlib import Path

import pandas as pd

from src.data import build_split_reference_views as views_mod


def _make_source_df() -> pd.DataFrame:
    rows = []
    for level in (1, 2, 3):
        rows.append(
            {
                "id": level,
                "code": f"L{level}",
                "SMILES": "CCO",
                "reference": "ref",
                "molecular_weight": 100.0,
                "emission_solid": 500.0,
                "emission_aggr": 510.0,
                "features_id": level,
                "mechanism_id": "ICT",
                "doi": "10.1/test",
                "difficulty_level": level,
                "source_split_file": f"{level}_level.csv",
                "source_row_index": 0,
            }
        )
    return pd.DataFrame(rows)


def test_reference_views_build_and_leave_level_exclusion(tmp_path: Path, monkeypatch) -> None:
    source_path = tmp_path / "sources" / "all_levels_reference.parquet"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    _make_source_df().to_parquet(source_path, index=False)

    def fake_run_p1_pipeline(*, input_parquet, output_dir, fact_schema_version):
        inp = pd.read_parquet(input_parquet)
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        base = inp.copy()
        base["canonical_smiles"] = base["SMILES"]
        base["inchikey"] = base["id"].apply(lambda x: f"IK{x}")
        base.to_parquet(out / "private_clean.parquet", index=False)
        base[["inchikey", "canonical_smiles"]].to_parquet(out / "molecule_table.parquet", index=False)
        rdkit = base[["inchikey", "canonical_smiles"]].copy()
        rdkit["ecfp_2048"] = [[0, 1, 0] for _ in range(len(rdkit))]
        rdkit.to_parquet(out / "rdkit_features.parquet", index=False)
        (out / "run_manifest.json").write_text("{}", encoding="utf-8")

    def fake_run_build_label_map(*, private_clean_path, output_path, source_view=None, allowed_labels=None):
        df = pd.read_parquet(private_clean_path)
        label = "ICT"
        if allowed_labels is not None:
            label = next(iter(allowed_labels))
        out = pd.DataFrame(
            {
                "inchikey": df["inchikey"],
                "mechanism_label": [label] * len(df),
                "difficulty_levels": [[1]] * len(df),
                "source_view": [source_view] * len(df),
            }
        )
        out.to_parquet(output_path, index=False)
        return out

    def fake_build_anchor_neighbors(*, output_path, rdkit_features_path, k):
        pd.DataFrame(
            {"inchikey": ["IK1"], "neighbor_inchikey": ["IK2"], "rank": [1], "tanimoto_sim": [0.5]}
        ).to_parquet(output_path, index=False)
        return pd.DataFrame()

    def fake_build_structure_reference_pool(*, molecule_table_path, mechanism_label_map_path, rdkit_features_path, output_path):
        out = pd.DataFrame(
            {
                "inchikey": ["IK1"],
                "canonical_smiles": ["CCO"],
                "mechanism_label": ["ICT"],
                "feature_morgan_count": ["{}"],
                "morgan_count": ["{}"],
                "structure_prior_profile": ["{}"],
                "structure_motif_profile": ["{}"],
            }
        )
        out.to_parquet(output_path, index=False)
        return out

    monkeypatch.setattr(views_mod, "run_p1_pipeline", fake_run_p1_pipeline)
    monkeypatch.setattr(views_mod, "run_build_label_map", fake_run_build_label_map)
    monkeypatch.setattr(views_mod, "build_anchor_neighbors", fake_build_anchor_neighbors)
    monkeypatch.setattr(views_mod, "build_structure_reference_pool", fake_build_structure_reference_pool)

    result = views_mod.build_split_reference_views(
        source_parquet=source_path,
        output_root=tmp_path,
        neighbor_k=3,
    )
    assert set(result["views_built"]) == {
        "all_levels_full",
        "leave_level_1",
        "leave_level_2",
        "leave_level_3",
    }

    for view_name in result["views_built"]:
        view_dir = tmp_path / "views" / view_name
        for required in (
            "private_clean.parquet",
            "molecule_table.parquet",
            "rdkit_features.parquet",
            "mechanism_label_map.parquet",
            "mechanism_label_map_main_prior.parquet",
            "anchor_neighbors_ecfp.parquet",
            "structure_reference_pool.parquet",
            "structure_reference_pool_main_prior.parquet",
            "run_manifest.json",
            "input_source.parquet",
        ):
            assert (view_dir / required).exists(), f"missing {required} for {view_name}"

    leave1 = pd.read_parquet(tmp_path / "views" / "leave_level_1" / "input_source.parquet")
    assert 1 not in leave1["difficulty_level"].astype(int).tolist()
    leave3 = pd.read_parquet(tmp_path / "views" / "leave_level_3" / "input_source.parquet")
    assert 3 not in leave3["difficulty_level"].astype(int).tolist()
