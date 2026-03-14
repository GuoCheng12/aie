from pathlib import Path

from src.chem.atb_aop_compact import extract_aop_compact


def _opt_text() -> str:
    return """
 Dipole moment (field-independent basis, Debye):

   X=     2.0000    Y=     0.1000    Z=    -0.2000    Tot=     2.0125
"""


def _excit_text(with_fail_marker: bool = False) -> str:
    marker = "\n Stop : Fail to convergence on Geom Opt!\n" if with_fail_marker else ""
    return f"""
 Dipole moment (field-independent basis, Debye):

   X=     2.4000    Y=    -0.8000    Z=     1.6000    Tot=     3.0282

 Ground to excited state transition electric dipole moments(Au):
   state-state      X         Y        Z         Dip       Osc
     0     1    -1.2000    1.6000   -1.7000    7.0000    0.3700

 Ground to excited state transition magnetic dipole moments(Au):
   state-state      X         Y        Z
     0     1     0.1000   -0.0500   -0.0600

 Rotatory Strengths (R) in cgs (10**-40 erg-esu-cm/Gauss)
   state-state       XX          YY          ZZ       R(length)
     0     1     -90.0000    -70.0000     70.0000    -31.0000

 ========= Excitation energies and oscillator strengths =========
 State    1 : E =    2.1361 eV     580.414 nm      17229.06 cm-1
 E(TD) =    -82.283688511      <S**2>= 0.000     f=  0.3700
 {marker}
"""


def test_reliability_high_when_all_compact_signals_present(tmp_path: Path):
    cache_dir = tmp_path / "IK"
    (cache_dir / "opt").mkdir(parents=True)
    (cache_dir / "excit").mkdir(parents=True)
    (cache_dir / "opt" / "opt_run.aop").write_text(_opt_text(), encoding="utf-8")
    (cache_dir / "excit" / "excit_run.aop").write_text(_excit_text(False), encoding="utf-8")

    payload = extract_aop_compact(cache_dir)
    assert payload["reliability"] == "high"


def test_reliability_downgraded_when_fail_marker_present(tmp_path: Path):
    cache_dir = tmp_path / "IK"
    (cache_dir / "opt").mkdir(parents=True)
    (cache_dir / "excit").mkdir(parents=True)
    (cache_dir / "opt" / "opt_run.aop").write_text(_opt_text(), encoding="utf-8")
    (cache_dir / "excit" / "excit_run.aop").write_text(_excit_text(True), encoding="utf-8")

    payload = extract_aop_compact(cache_dir)
    assert payload["convergence_flags"]["excit_has_fail_marker"] is True
    assert payload["reliability"] == "medium"
