"""Murcko scaffold extraction and scaffold-level retrieval."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional

from rdkit import Chem
from rdkit.Chem.Scaffolds import MurckoScaffold

from src.structure.feature_morgan import compute_feature_morgan_count, count_tanimoto


def extract_murcko_scaffold(smiles: str) -> Dict[str, Optional[str]]:
    txt = str(smiles or "").strip()
    if not txt:
        return {"murcko_scaffold_smiles": None, "generic_scaffold_smiles": None}
    mol = Chem.MolFromSmiles(txt)
    if mol is None:
        return {"murcko_scaffold_smiles": None, "generic_scaffold_smiles": None}
    try:
        scaffold = MurckoScaffold.MurckoScaffoldSmiles(mol=mol)
    except Exception:
        scaffold = ""
    generic = None
    if scaffold:
        try:
            scaffold_mol = Chem.MolFromSmiles(scaffold)
            if scaffold_mol is not None:
                generic_mol = MurckoScaffold.MakeScaffoldGeneric(scaffold_mol)
                generic = Chem.MolToSmiles(generic_mol, canonical=True)
        except Exception:
            generic = None
    return {
        "murcko_scaffold_smiles": scaffold or None,
        "generic_scaffold_smiles": generic,
    }


def _label_distribution(rows: Iterable[Mapping[str, Any]]) -> Dict[str, float]:
    total = 0.0
    counts: Dict[str, float] = {}
    for row in rows:
        label = str(row.get("mechanism_label") or "unknown").strip() or "unknown"
        weight = float(row.get("sim") or 0.0)
        counts[label] = counts.get(label, 0.0) + weight
        total += weight
    if total <= 0.0:
        return {}
    return {k: round(v / total, 6) for k, v in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)}


def _consensus_strength(feature_dist: Mapping[str, float], scaffold_dist: Mapping[str, float]) -> str:
    feature_top = next(iter(feature_dist.items()), (None, 0.0))
    scaffold_top = next(iter(scaffold_dist.items()), (None, 0.0))
    if feature_top[0] and scaffold_top[0] and feature_top[0] == scaffold_top[0] and feature_top[1] >= 0.45 and scaffold_top[1] >= 0.45:
        return "high"
    if max(feature_top[1], scaffold_top[1]) >= 0.35:
        return "mid"
    return "low"


def compute_scaffold_neighbors(
    target_smiles: str,
    reference_rows: List[Mapping[str, Any]],
    *,
    topk: int = 10,
    target_inchikey: Optional[str] = None,
    target_canonical_smiles: Optional[str] = None,
) -> Dict[str, Any]:
    scaffold_info = extract_murcko_scaffold(target_smiles)
    target_generic = scaffold_info.get("generic_scaffold_smiles")
    target_scaffold = scaffold_info.get("murcko_scaffold_smiles")
    target_fp = compute_feature_morgan_count(target_generic or target_scaffold or target_smiles)

    scored: List[Dict[str, Any]] = []
    for idx, row in enumerate(reference_rows):
        inchikey = str(row.get("inchikey") or "").strip()
        if target_inchikey and inchikey == target_inchikey:
            continue
        ref_smiles = str(row.get("canonical_smiles") or "").strip()
        if target_canonical_smiles and ref_smiles and ref_smiles == target_canonical_smiles:
            continue
        ref_generic = str(row.get("generic_scaffold_smiles") or "").strip()
        ref_scaffold = str(row.get("murcko_scaffold_smiles") or "").strip()
        ref_basis = ref_generic or ref_scaffold or ref_smiles
        sim = 0.0
        if target_generic and ref_generic and target_generic == ref_generic:
            sim = 1.0
        elif target_scaffold and ref_scaffold and target_scaffold == ref_scaffold:
            sim = 0.9
        else:
            ref_fp = row.get("scaffold_feature_morgan_count")
            if not isinstance(ref_fp, dict):
                ref_fp = compute_feature_morgan_count(ref_basis)
            sim = count_tanimoto(target_fp, ref_fp)
        if sim <= 0.0:
            continue
        scored.append(
            {
                "case_index": idx,
                "inchikey": inchikey,
                "sim": round(float(sim), 6),
                "mechanism_label": str(row.get("mechanism_label") or "unknown"),
                "murcko_scaffold_smiles": ref_scaffold or None,
                "generic_scaffold_smiles": ref_generic or None,
            }
        )

    scored.sort(key=lambda row: row["sim"], reverse=True)
    top_rows = scored[: max(1, int(topk))]
    return {
        "murcko_scaffold_smiles": target_scaffold,
        "generic_scaffold_smiles": target_generic,
        "murcko_topk": top_rows,
        "scaffold_neighbor_label_distribution": _label_distribution(top_rows),
        "retrieval_consensus_strength": _consensus_strength({}, _label_distribution(top_rows)),
    }


__all__ = [
    "extract_murcko_scaffold",
    "compute_scaffold_neighbors",
]
