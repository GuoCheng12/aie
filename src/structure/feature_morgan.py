"""Feature Morgan and Morgan count fingerprints for structure priors."""

from __future__ import annotations

from functools import lru_cache
from typing import Dict, Mapping, Optional

from rdkit import Chem
from rdkit.Chem import rdFingerprintGenerator


CountFingerprint = Dict[int, int]


def _mol_from_smiles(smiles: str) -> Optional[Chem.Mol]:
    txt = str(smiles or "").strip()
    if not txt:
        return None
    return Chem.MolFromSmiles(txt)


@lru_cache(maxsize=16)
def _count_generator(radius: int, n_bits: int, use_features: bool):
    kwargs = {"radius": int(radius), "fpSize": int(n_bits)}
    if use_features:
        kwargs["atomInvariantsGenerator"] = rdFingerprintGenerator.GetMorganFeatureAtomInvGen()
    return rdFingerprintGenerator.GetMorganGenerator(**kwargs)


def _hashed_morgan_count(mol: Chem.Mol, *, radius: int, n_bits: int, use_features: bool) -> CountFingerprint:
    fp = _count_generator(radius, n_bits, use_features).GetCountFingerprint(mol)
    return {int(k): int(v) for k, v in fp.GetNonzeroElements().items() if int(v) > 0}


def compute_morgan_count(smiles: str, radius: int = 2, n_bits: int = 2048) -> CountFingerprint:
    mol = _mol_from_smiles(smiles)
    if mol is None:
        return {}
    return _hashed_morgan_count(mol, radius=radius, n_bits=n_bits, use_features=False)


def compute_feature_morgan_count(smiles: str, radius: int = 2, n_bits: int = 2048) -> CountFingerprint:
    mol = _mol_from_smiles(smiles)
    if mol is None:
        return {}
    return _hashed_morgan_count(mol, radius=radius, n_bits=n_bits, use_features=True)


def count_tanimoto(a: Mapping[int, int], b: Mapping[int, int]) -> float:
    if not a or not b:
        return 0.0
    keys = set(a) | set(b)
    numer = 0.0
    denom = 0.0
    for key in keys:
        av = float(a.get(key, 0))
        bv = float(b.get(key, 0))
        numer += min(av, bv)
        denom += max(av, bv)
    if denom <= 0.0:
        return 0.0
    return float(numer / denom)


__all__ = [
    "CountFingerprint",
    "compute_morgan_count",
    "compute_feature_morgan_count",
    "count_tanimoto",
]
