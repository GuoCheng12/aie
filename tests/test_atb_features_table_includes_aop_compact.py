import json
from pathlib import Path

import pandas as pd

from src.chem.build_atb_tables_from_cache import build_tables


def _prepare_cache(cache_root: Path, inchikey: str) -> None:
    cdir = cache_root / inchikey[:2] / inchikey
    (cdir / "opt").mkdir(parents=True, exist_ok=True)
    (cdir / "excit").mkdir(parents=True, exist_ok=True)
    (cdir / "status.json").write_text(
        json.dumps({"inchikey": inchikey, "run_status": "success", "fail_stage": None, "error_msg": None}),
        encoding="utf-8",
    )
    (cdir / "features.json").write_text(
        json.dumps(
            {
                "delta_volume": 0.1,
                "delta_gap": 0.2,
                "delta_dihedral": 0.3,
                "excitation_energy": 2.0,
                "delta_dipole": {
                    "element": ["C", "N"],
                    "charge_variation": [0.02, -0.01],
                },
            }
        ),
        encoding="utf-8",
    )
    (cdir / "opt" / "opt_run.aop").write_text(
        """
 Dipole moment (field-independent basis, Debye):

   X=     2.0000    Y=     0.1000    Z=    -0.2000    Tot=     2.0125
""",
        encoding="utf-8",
    )
    (cdir / "excit" / "excit_run.aop").write_text(
        """
 Dipole moment (field-independent basis, Debye):

   X=     2.3000    Y=    -0.8000    Z=     1.6000    Tot=     2.9090

 Ground to excited state transition electric dipole moments(Au):
   state-state      X         Y        Z         Dip       Osc
     0     1    -1.0000    2.0000   -3.0000    5.5000    0.2200

 Ground to excited state transition magnetic dipole moments(Au):
   state-state      X         Y        Z
     0     1     0.1000   -0.2000    0.3000

 Rotatory Strengths (R) in cgs (10**-40 erg-esu-cm/Gauss)
   state-state       XX          YY          ZZ       R(length)
     0     1       1.0000      2.0000      3.0000    6.0000

 ========= Excitation energies and oscillator strengths =========
 State    1 : E =    2.8000 eV     442.000 nm      22500.00 cm-1
 E(TD) =    -82.0      <S**2>= 0.000     f=  0.4321
""",
        encoding="utf-8",
    )


def test_build_tables_includes_aop_compact_columns(tmp_path: Path):
    inchikey = "AAAAAAAAAAAAAA-UHFFFAOYSA-N"
    mol_table = tmp_path / "molecule_table.parquet"
    pd.DataFrame([{"inchikey": inchikey}]).to_parquet(mol_table, index=False)

    cache_root = tmp_path / "cache" / "atb"
    _prepare_cache(cache_root, inchikey)

    out = tmp_path / "out"
    build_tables(molecule_table_path=str(mol_table), cache_dir=str(cache_root), output_dir=str(out))
    feat = pd.read_parquet(out / "atb_features.parquet")

    assert "s1_transition_electric_dip_au" in feat.columns
    assert "s1_oscillator_strength_f" in feat.columns
    assert "s1_excitation_wavelength_nm" in feat.columns
    assert "aop_compact_reliability_score" in feat.columns

    row = feat.iloc[0].to_dict()
    assert row["s1_transition_electric_dip_au"] == 5.5
    assert row["s1_oscillator_strength_f"] == 0.4321
    assert row["s1_excitation_wavelength_nm"] == 442.0
    assert row["delta_perm_dipole_tot_debye"] is not None
