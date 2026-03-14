"""
Compact structure-prior profile derived from canonical SMILES and RDKit descriptors.

This profile is intentionally mechanism-agnostic. It summarizes generic structure
axes that can be used in R0/R1 without privileging any single mechanistic
direction.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Optional

from rdkit import Chem

MAX_BYTES = 2048


def _to_float(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except Exception:
        return None
    if out != out:
        return None
    return out


def _to_int(value: Any) -> Optional[int]:
    try:
        out = int(value)
    except Exception:
        return None
    return out


def _bucket(value: Optional[float], low_cut: float, high_cut: float) -> str:
    if value is None:
        return "unknown"
    if value < low_cut:
        return "low"
    if value < high_cut:
        return "mid"
    return "high"


def _donor_acceptor_topology(n_hbd: Optional[int], n_hba: Optional[int], tpsa: Optional[float]) -> str:
    if n_hbd is None or n_hba is None:
        return "unknown"
    score = 0
    if n_hbd > 0:
        score += 1
    if n_hba > 0:
        score += 1
    if n_hbd > 0 and n_hba > 1:
        score += 1
    if tpsa is not None and tpsa >= 40.0:
        score += 1
    if score >= 3:
        return "strong"
    if score >= 1:
        return "mixed"
    return "weak"


def _intramolecular_hbond_candidates(n_hbd: Optional[int], n_hba: Optional[int], n_rot: Optional[int], aromatic_rings: Optional[int]) -> str:
    if n_hbd is None or n_hba is None:
        return "unknown"
    if n_hbd <= 0 or n_hba <= 0:
        return "none"
    if (n_rot or 0) <= 2 and (aromatic_rings or 0) >= 1:
        return "likely"
    return "possible"


def _overall_structure_prior(
    *,
    donor_acceptor_topology: str,
    aromatic_core_density: str,
    flexibility_proxy: str,
    conjugation_proxy: str,
    intramolecular_hbond_candidates: str,
    reliability: str,
) -> str:
    parts = [
        f"Conjugation is {conjugation_proxy}",
        f"aromatic-core density is {aromatic_core_density}",
        f"flexibility is {flexibility_proxy}",
        f"donor/acceptor topology is {donor_acceptor_topology}",
    ]
    if intramolecular_hbond_candidates in {"possible", "likely"}:
        parts.append(f"intramolecular H-bond candidates are {intramolecular_hbond_candidates}")
    parts.append(f"reliability is {reliability}")
    return "; ".join(parts) + "."


def _notes(
    *,
    donor_acceptor_topology: str,
    intramolecular_hbond_candidates: str,
    aromatic_core_density: str,
    flexibility_proxy: str,
    conjugation_proxy: str,
) -> list[str]:
    out = [
        f"Donor/acceptor topology is {donor_acceptor_topology} under the current topology heuristic.",
        f"Aromatic-core density is {aromatic_core_density}, flexibility is {flexibility_proxy}, and conjugation is {conjugation_proxy}.",
    ]
    if intramolecular_hbond_candidates in {"possible", "likely"}:
        out.append(f"Intramolecular H-bond candidates are {intramolecular_hbond_candidates} as structural context.")
    return out[:3]


def _trim(payload: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(payload)
    raw = json.dumps(out, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(raw) <= MAX_BYTES:
        return out
    out["notes"] = list(out.get("notes") or [])[:1]
    raw = json.dumps(out, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(raw) <= MAX_BYTES:
        return out
    out["notes"] = []
    return out


def compute_structure_prior_profile(
    canonical_smiles: str,
    descriptors: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    desc = dict(descriptors or {})
    n_rot = _to_int(desc.get("n_rotatable_bonds"))
    n_hbd = _to_int(desc.get("n_hbd"))
    n_hba = _to_int(desc.get("n_hba"))
    n_rings = _to_int(desc.get("n_rings"))
    n_aromatic_rings = _to_int(desc.get("n_aromatic_rings"))
    n_heavy_atoms = _to_int(desc.get("n_heavy_atoms"))
    tpsa = _to_float(desc.get("tpsa"))
    logp = _to_float(desc.get("logp"))

    mol = Chem.MolFromSmiles(str(canonical_smiles or "").strip()) if canonical_smiles else None
    reliability = "high" if mol is not None else "low"

    aromatic_ratio: Optional[float] = None
    if n_aromatic_rings is not None and n_rings is not None and n_rings > 0:
        aromatic_ratio = float(n_aromatic_rings) / float(n_rings)
    aromatic_core_density = _bucket(aromatic_ratio, 0.30, 0.70) if aromatic_ratio is not None else _bucket(float(n_aromatic_rings or 0), 1.0, 3.0)
    flexibility_proxy = _bucket(float(n_rot) if n_rot is not None else None, 2.0, 6.0)

    conjugation_score: Optional[float] = None
    if n_aromatic_rings is not None or n_heavy_atoms is not None:
        conjugation_score = float((n_aromatic_rings or 0) * 2 + (n_heavy_atoms or 0) / 10.0)
    conjugation_proxy = _bucket(conjugation_score, 2.0, 5.0)

    donor_acceptor_topology = _donor_acceptor_topology(n_hbd, n_hba, tpsa)
    hbond_candidates = _intramolecular_hbond_candidates(n_hbd, n_hba, n_rot, n_aromatic_rings)

    profile = {
        "version": "structure_prior_v1",
        "descriptor_snapshot": {
            "n_rotatable_bonds": n_rot,
            "n_hbd": n_hbd,
            "n_hba": n_hba,
            "n_rings": n_rings,
            "n_aromatic_rings": n_aromatic_rings,
            "tpsa": tpsa,
            "logp": logp,
        },
        "donor_acceptor_topology": donor_acceptor_topology,
        "intramolecular_hbond_candidates": hbond_candidates,
        "aromatic_core_density": aromatic_core_density,
        "flexibility_proxy": flexibility_proxy,
        "conjugation_proxy": conjugation_proxy,
        "overall_structure_prior": _overall_structure_prior(
            donor_acceptor_topology=donor_acceptor_topology,
            aromatic_core_density=aromatic_core_density,
            flexibility_proxy=flexibility_proxy,
            conjugation_proxy=conjugation_proxy,
            intramolecular_hbond_candidates=hbond_candidates,
            reliability=reliability,
        ),
        "reliability": reliability,
        "notes": _notes(
            donor_acceptor_topology=donor_acceptor_topology,
            intramolecular_hbond_candidates=hbond_candidates,
            aromatic_core_density=aromatic_core_density,
            flexibility_proxy=flexibility_proxy,
            conjugation_proxy=conjugation_proxy,
        ),
    }
    return _trim(profile)


__all__ = ["compute_structure_prior_profile"]
