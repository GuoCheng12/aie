"""Compact extraction helpers for aTB .aop artifacts.

The extractor converts verbose opt/excit .aop outputs into a small, stable
summary that can be consumed by cache/parquet/reasoning without exposing raw
text blocks to the LLM prompt.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Dict, Match, Optional

FAIL_MARKER = "Stop : Fail to convergence on Geom Opt!"

_PERM_DIPOLE_RE = re.compile(
    r"Dipole moment \(field-independent basis, Debye\):"
    r"[\s\S]{0,320}?X=\s*([-+]?\d+(?:\.\d+)?)"
    r"\s+Y=\s*([-+]?\d+(?:\.\d+)?)"
    r"\s+Z=\s*([-+]?\d+(?:\.\d+)?)"
    r"\s+Tot=\s*([-+]?\d+(?:\.\d+)?)",
    flags=re.IGNORECASE,
)

_TRANSITION_ELECTRIC_RE = re.compile(
    r"Ground to excited state transition electric dipole moments\(Au\):"
    r"[\s\S]{0,400}?"
    r"^\s*0\s+1\s+"
    r"([-+]?\d+(?:\.\d+)?)\s+"
    r"([-+]?\d+(?:\.\d+)?)\s+"
    r"([-+]?\d+(?:\.\d+)?)\s+"
    r"([-+]?\d+(?:\.\d+)?)\s+"
    r"([-+]?\d+(?:\.\d+)?)\s*$",
    flags=re.MULTILINE | re.IGNORECASE,
)

_TRANSITION_MAGNETIC_RE = re.compile(
    r"Ground to excited state transition magnetic dipole moments\(Au\):"
    r"[\s\S]{0,320}?"
    r"^\s*0\s+1\s+"
    r"([-+]?\d+(?:\.\d+)?)\s+"
    r"([-+]?\d+(?:\.\d+)?)\s+"
    r"([-+]?\d+(?:\.\d+)?)\s*$",
    flags=re.MULTILINE | re.IGNORECASE,
)

_ROTATORY_RE = re.compile(
    r"Rotatory Strengths \(R\) in cgs"
    r"[\s\S]{0,320}?"
    r"^\s*0\s+1\s+"
    r"([-+]?\d+(?:\.\d+)?)\s+"
    r"([-+]?\d+(?:\.\d+)?)\s+"
    r"([-+]?\d+(?:\.\d+)?)\s+"
    r"([-+]?\d+(?:\.\d+)?)\s*$",
    flags=re.MULTILINE | re.IGNORECASE,
)

_STATE1_EXCITATION_RE = re.compile(
    r"State\s+1\s+:\s*E\s*=\s*([-+]?\d+(?:\.\d+)?)\s*eV"
    r"\s*([-+]?\d+(?:\.\d+)?)\s*nm"
    r"\s*([-+]?\d+(?:\.\d+)?)\s*cm-1",
    flags=re.IGNORECASE,
)

_STATE1_F_RE = re.compile(
    r"State\s+1\s+:\s*E\s*=\s*[-+]?\d+(?:\.\d+)?\s*eV"
    r"[\s\S]{0,280}?f=\s*([-+]?\d+(?:\.\d+)?)",
    flags=re.IGNORECASE,
)


def _to_float(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out):
        return None
    return out


def _as_xyz_tot(match: Optional[Match[str]]) -> Dict[str, Optional[float]]:
    if not match:
        return {"x": None, "y": None, "z": None, "tot": None}
    return {
        "x": _to_float(match.group(1)),
        "y": _to_float(match.group(2)),
        "z": _to_float(match.group(3)),
        "tot": _to_float(match.group(4)),
    }


def _as_xyz_dip(match: Optional[Match[str]]) -> Dict[str, Optional[float]]:
    if not match:
        return {"x": None, "y": None, "z": None, "dip": None}
    return {
        "x": _to_float(match.group(1)),
        "y": _to_float(match.group(2)),
        "z": _to_float(match.group(3)),
        "dip": _to_float(match.group(4)),
    }


def _as_xyz(match: Optional[Match[str]]) -> Dict[str, Optional[float]]:
    if not match:
        return {"x": None, "y": None, "z": None}
    return {
        "x": _to_float(match.group(1)),
        "y": _to_float(match.group(2)),
        "z": _to_float(match.group(3)),
    }


def pick_last_match(text: str, pattern: re.Pattern[str]) -> Optional[Match[str]]:
    """Return the last regex match in text for final-iteration extraction."""
    if not text:
        return None
    matches = list(pattern.finditer(text))
    return matches[-1] if matches else None


def extract_from_opt_text(text: str) -> Dict[str, Any]:
    """Extract compact S0 signals from opt_run.aop text."""
    dipole = _as_xyz_tot(pick_last_match(text, _PERM_DIPOLE_RE))
    return {
        "has_fail_marker": FAIL_MARKER in (text or ""),
        "s0_permanent_dipole_debye": dipole,
    }


def extract_from_excit_text(text: str) -> Dict[str, Any]:
    """Extract compact S1/transition signals from excit_run.aop text."""
    s1_perm = _as_xyz_tot(pick_last_match(text, _PERM_DIPOLE_RE))
    s1_electric = _as_xyz_dip(pick_last_match(text, _TRANSITION_ELECTRIC_RE))
    s1_magnetic = _as_xyz(pick_last_match(text, _TRANSITION_MAGNETIC_RE))

    rotatory_match = pick_last_match(text, _ROTATORY_RE)
    s1_rotatory = _to_float(rotatory_match.group(4)) if rotatory_match else None

    state1_excit = pick_last_match(text, _STATE1_EXCITATION_RE)
    s1_excitation = {
        "energy_ev": _to_float(state1_excit.group(1)) if state1_excit else None,
        "wavelength_nm": _to_float(state1_excit.group(2)) if state1_excit else None,
        "wavenumber_cm1": _to_float(state1_excit.group(3)) if state1_excit else None,
        "oscillator_strength_f": None,
    }
    state1_f = pick_last_match(text, _STATE1_F_RE)
    if state1_f:
        s1_excitation["oscillator_strength_f"] = _to_float(state1_f.group(1))

    return {
        "has_fail_marker": FAIL_MARKER in (text or ""),
        "s1_permanent_dipole_debye": s1_perm,
        "s1_transition_electric_dipole_au": s1_electric,
        "s1_transition_magnetic_dipole_au": s1_magnetic,
        "s1_rotatory_strength_cgs": s1_rotatory,
        "s1_excitation": s1_excitation,
    }


def _degrade_reliability(level: str) -> str:
    if level == "high":
        return "medium"
    if level == "medium":
        return "low"
    return "low"


def _compute_reliability(payload: Dict[str, Any], opt_fail: bool, excit_fail: bool) -> str:
    score = 0
    if _to_float((payload.get("s0_permanent_dipole_debye") or {}).get("tot")) is not None:
        score += 1
    if _to_float((payload.get("s1_permanent_dipole_debye") or {}).get("tot")) is not None:
        score += 1
    if _to_float((payload.get("s1_transition_electric_dipole_au") or {}).get("dip")) is not None:
        score += 1
    mag = payload.get("s1_transition_magnetic_dipole_au") or {}
    if any(_to_float(mag.get(axis)) is not None for axis in ("x", "y", "z")):
        score += 1
    if _to_float(payload.get("s1_rotatory_strength_cgs")) is not None:
        score += 1
    excit = payload.get("s1_excitation") or {}
    if all(_to_float(excit.get(k)) is not None for k in ("energy_ev", "wavelength_nm", "wavenumber_cm1", "oscillator_strength_f")):
        score += 1

    if score >= 5:
        level = "high"
    elif score >= 3:
        level = "medium"
    else:
        level = "low"

    if opt_fail or excit_fail:
        level = _degrade_reliability(level)
    return level


def extract_aop_compact(cache_dir: Path) -> Dict[str, Any]:
    """Extract compact `.aop` summary for one cache molecule directory."""
    opt_path = cache_dir / "opt" / "opt_run.aop"
    excit_path = cache_dir / "excit" / "excit_run.aop"

    opt_text = opt_path.read_text(encoding="utf-8", errors="ignore") if opt_path.exists() else ""
    excit_text = excit_path.read_text(encoding="utf-8", errors="ignore") if excit_path.exists() else ""

    opt_data = extract_from_opt_text(opt_text)
    excit_data = extract_from_excit_text(excit_text)

    s0 = opt_data.get("s0_permanent_dipole_debye") or {"x": None, "y": None, "z": None, "tot": None}
    s1 = excit_data.get("s1_permanent_dipole_debye") or {"x": None, "y": None, "z": None, "tot": None}
    s0_tot = _to_float(s0.get("tot"))
    s1_tot = _to_float(s1.get("tot"))

    payload: Dict[str, Any] = {
        "version": "aop_compact_v1",
        "source_files": {
            "opt": "opt/opt_run.aop",
            "excit": "excit/excit_run.aop",
        },
        "convergence_flags": {
            "opt_has_fail_marker": bool(opt_data.get("has_fail_marker")),
            "excit_has_fail_marker": bool(excit_data.get("has_fail_marker")),
        },
        "s0_permanent_dipole_debye": s0,
        "s1_permanent_dipole_debye": s1,
        "delta_permanent_dipole_tot_debye": (s1_tot - s0_tot) if (s0_tot is not None and s1_tot is not None) else None,
        "s1_transition_electric_dipole_au": excit_data.get("s1_transition_electric_dipole_au")
        or {"x": None, "y": None, "z": None, "dip": None},
        "s1_transition_magnetic_dipole_au": excit_data.get("s1_transition_magnetic_dipole_au")
        or {"x": None, "y": None, "z": None},
        "s1_rotatory_strength_cgs": excit_data.get("s1_rotatory_strength_cgs"),
        "s1_excitation": excit_data.get("s1_excitation")
        or {
            "energy_ev": None,
            "wavelength_nm": None,
            "wavenumber_cm1": None,
            "oscillator_strength_f": None,
        },
    }

    reliability = _compute_reliability(
        payload,
        opt_fail=bool(opt_data.get("has_fail_marker")),
        excit_fail=bool(excit_data.get("has_fail_marker")),
    )
    payload["reliability"] = reliability

    notes = ["compact extraction only; no mechanism interpretation"]
    if bool(opt_data.get("has_fail_marker")) or bool(excit_data.get("has_fail_marker")):
        notes.append("geometry optimization fail marker detected in opt/excit output; reliability downgraded")
    payload["notes"] = notes[:2]
    return payload


__all__ = [
    "extract_aop_compact",
    "extract_from_excit_text",
    "extract_from_opt_text",
    "pick_last_match",
]
