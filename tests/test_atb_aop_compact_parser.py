from pathlib import Path

from src.chem.atb_aop_compact import extract_aop_compact, extract_from_excit_text, extract_from_opt_text


def _opt_text(v1: float, v2: float) -> str:
    return f"""
 Dipole moment (field-independent basis, Debye):

   X=     {v1:.4f}    Y=     0.1000    Z=    -0.2000    Tot=     {abs(v1):.4f}

 random text
 Dipole moment (field-independent basis, Debye):

   X=     {v2:.4f}    Y=     0.2000    Z=    -0.4000    Tot=     {abs(v2):.4f}
"""


def _excit_block(e: float, wl: float, wn: float, f: float, dip: float, rot: float) -> str:
    return f"""
 Dipole moment (field-independent basis, Debye):

   X=     1.1000    Y=    -0.2000    Z=     0.4000    Tot=     1.1874

 Ground to excited state transition electric dipole moments(Au):
   state-state      X         Y        Z         Dip       Osc
     0     1    -1.0000    2.0000   -3.0000    {dip:.4f}    0.1200

 Ground to excited state transition magnetic dipole moments(Au):
   state-state      X         Y        Z
     0     1     0.1000   -0.2000    0.3000

 Rotatory Strengths (R) in cgs (10**-40 erg-esu-cm/Gauss)
   state-state       XX          YY          ZZ       R(length)
     0     1       1.0000      2.0000      3.0000    {rot:.4f}

 ========= Excitation energies and oscillator strengths =========

 State    1 : E =    {e:.4f} eV     {wl:.3f} nm      {wn:.2f} cm-1
 E(TD) =    -82.0      <S**2>= 0.000     f=  {f:.4f}
"""


def test_extract_from_opt_text_uses_last_block():
    payload = extract_from_opt_text(_opt_text(1.0, 2.0))
    assert payload["s0_permanent_dipole_debye"]["x"] == 2.0
    assert payload["s0_permanent_dipole_debye"]["tot"] == 2.0


def test_extract_from_excit_text_uses_last_state1_block():
    text = _excit_block(2.9, 420.0, 23000.0, 0.12, 3.0, 1.0) + "\n" + _excit_block(2.1, 590.0, 17000.0, 0.37, 7.1, -31.0)
    payload = extract_from_excit_text(text)
    assert payload["s1_transition_electric_dipole_au"]["dip"] == 7.1
    assert payload["s1_rotatory_strength_cgs"] == -31.0
    assert payload["s1_excitation"]["energy_ev"] == 2.1
    assert payload["s1_excitation"]["wavelength_nm"] == 590.0
    assert payload["s1_excitation"]["oscillator_strength_f"] == 0.37


def test_extract_aop_compact_handles_missing_sections_without_exception(tmp_path: Path):
    cache_dir = tmp_path / "IK"
    (cache_dir / "opt").mkdir(parents=True)
    (cache_dir / "excit").mkdir(parents=True)
    (cache_dir / "opt" / "opt_run.aop").write_text("no dipole section\n", encoding="utf-8")
    (cache_dir / "excit" / "excit_run.aop").write_text("no transition section\n", encoding="utf-8")

    payload = extract_aop_compact(cache_dir)
    assert payload["version"] == "aop_compact_v1"
    assert payload["s1_excitation"]["energy_ev"] is None
    assert payload["s1_transition_electric_dipole_au"]["dip"] is None
    assert payload["reliability"] == "low"
