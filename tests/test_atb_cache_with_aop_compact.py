import json
from pathlib import Path

from src.chem.atb_cache import get_atb_cache_record


def _write_cache_fixture(cache_root: Path, inchikey: str) -> Path:
    cdir = cache_root / inchikey[:2] / inchikey
    (cdir / "opt").mkdir(parents=True, exist_ok=True)
    (cdir / "excit").mkdir(parents=True, exist_ok=True)

    (cdir / "status.json").write_text(
        json.dumps(
            {
                "inchikey": inchikey,
                "run_status": "success",
                "fail_stage": None,
                "error_msg": None,
            }
        ),
        encoding="utf-8",
    )
    (cdir / "features.json").write_text(
        json.dumps(
            {
                "delta_volume": 0.3,
                "delta_gap": -0.2,
                "delta_dihedral": 1.4,
                "excitation_energy": 2.1,
                "delta_dipole": {
                    "element": ["C", "N", "O"],
                    "charge_variation": [0.01, -0.02, 0.03],
                },
            }
        ),
        encoding="utf-8",
    )
    (cdir / "opt" / "opt_run.aop").write_text(
        """
 Dipole moment (field-independent basis, Debye):

   X=     1.0000    Y=     0.1000    Z=    -0.2000    Tot=     1.0247
""",
        encoding="utf-8",
    )
    (cdir / "excit" / "excit_run.aop").write_text(
        """
 Dipole moment (field-independent basis, Debye):

   X=     1.1000    Y=     0.1000    Z=    -0.2000    Tot=     1.1225

 Ground to excited state transition electric dipole moments(Au):
   state-state      X         Y        Z         Dip       Osc
     0     1    -1.0000    2.0000   -3.0000    4.0000    0.1200

 Ground to excited state transition magnetic dipole moments(Au):
   state-state      X         Y        Z
     0     1     0.1000   -0.2000    0.3000

 Rotatory Strengths (R) in cgs (10**-40 erg-esu-cm/Gauss)
   state-state       XX          YY          ZZ       R(length)
     0     1       1.0000      2.0000      3.0000    4.0000

 ========= Excitation energies and oscillator strengths =========
 State    1 : E =    2.5000 eV     495.000 nm      20000.00 cm-1
 E(TD) =    -82.0      <S**2>= 0.000     f=  0.5678
""",
        encoding="utf-8",
    )
    return cdir


def test_get_atb_cache_record_merges_aop_compact_scalars(tmp_path: Path):
    inchikey = "FROQVQNFQFCXNO-KSZJGORMSA-N"
    cache_root = tmp_path / "cache" / "atb"
    cdir = _write_cache_fixture(cache_root, inchikey)

    record = get_atb_cache_record(inchikey, cache_dir=str(cache_root))
    assert record["cache_status"] == "success"
    summary = record.get("features_summary") or {}
    assert summary.get("s0_perm_dipole_tot_debye") == 1.0247
    assert summary.get("s1_perm_dipole_tot_debye") == 1.1225
    assert round(summary.get("delta_perm_dipole_tot_debye"), 4) == 0.0978
    assert summary.get("s1_transition_electric_dip_au") == 4.0
    assert summary.get("s1_rotatory_strength_cgs") == 4.0
    assert summary.get("s1_oscillator_strength_f") == 0.5678
    assert summary.get("s1_excitation_wavelength_nm") == 495.0
    assert summary.get("aop_compact_reliability_score") in {1.0, 2.0}

    compact_path = cdir / "aop_compact.json"
    assert compact_path.exists()
    compact = json.loads(compact_path.read_text(encoding="utf-8"))
    assert compact.get("version") == "aop_compact_v1"
