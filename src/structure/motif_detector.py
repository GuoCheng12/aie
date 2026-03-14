"""Generic structure motif detection from SMILES."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from rdkit import Chem, RDConfig
from rdkit.Chem import ChemicalFeatures


def _bucket(value: Optional[float], low_cut: float, high_cut: float) -> str:
    if value is None:
        return "unknown"
    if value < low_cut:
        return "low"
    if value < high_cut:
        return "mid"
    return "high"


@lru_cache(maxsize=1)
def _feature_factory() -> ChemicalFeatures.FreeChemicalFeatureFactory:
    return ChemicalFeatures.BuildFeatureFactory(str(RDConfig.RDDataDir) + "/BaseFeatures.fdef")


def _feature_atom_ids(mol: Chem.Mol, family: str) -> Set[int]:
    atom_ids: Set[int] = set()
    for feat in _feature_factory().GetFeaturesForMol(mol):
        if feat.GetFamily() != family:
            continue
        atom_ids.update(int(i) for i in feat.GetAtomIds())
    return atom_ids


def _shortest_path_length(mol: Chem.Mol, a: int, b: int) -> Optional[int]:
    path = Chem.GetShortestPath(mol, int(a), int(b))
    if not path:
        return None
    return max(0, len(path) - 1)


def _count_intramolecular_hbond_pairs(mol: Chem.Mol, donors: Sequence[int], acceptors: Sequence[int]) -> Tuple[int, Optional[int]]:
    count = 0
    best_path: Optional[int] = None
    for d in donors:
        for a in acceptors:
            if d == a:
                continue
            dist = _shortest_path_length(mol, d, a)
            if dist is None:
                continue
            if best_path is None or dist < best_path:
                best_path = dist
            if 3 <= dist <= 8:
                count += 1
    return count, best_path


def _tautomerizable_candidates(mol: Chem.Mol) -> Tuple[int, List[str]]:
    motif_patterns = {
        "phenol_donor": Chem.MolFromSmarts("[OX2H]-c"),
        "enolizable_donor": Chem.MolFromSmarts("[CX3](=O)[CH1,CH2][OX2H]"),
        "imine_acceptor": Chem.MolFromSmarts("[CX3]=[NX2]"),
        "carbonyl_acceptor": Chem.MolFromSmarts("[CX3]=[OX1]"),
        "hetero_acceptor_ring": Chem.MolFromSmarts("[n,o,s]1aaaa1"),
    }
    matched: List[str] = []
    for name, patt in motif_patterns.items():
        if patt is not None and mol.HasSubstructMatch(patt):
            matched.append(name)
    donor_like = any(name in matched for name in ("phenol_donor", "enolizable_donor"))
    acceptor_like = any(name in matched for name in ("imine_acceptor", "carbonyl_acceptor", "hetero_acceptor_ring"))
    count = int(donor_like and acceptor_like)
    if donor_like and acceptor_like and "phenol_donor" in matched and "imine_acceptor" in matched:
        count += 1
    return count, matched


def _fused_aromatic_core(mol: Chem.Mol) -> bool:
    ring_info = mol.GetRingInfo()
    atom_rings = [set(r) for r in ring_info.AtomRings()]
    aromatic_rings = [r for r in atom_rings if all(mol.GetAtomWithIdx(i).GetIsAromatic() for i in r)]
    for i, ring_a in enumerate(aromatic_rings):
        for ring_b in aromatic_rings[i + 1 :]:
            if len(ring_a.intersection(ring_b)) >= 2:
                return True
    return False


def _aromatic_scaffold_type(n_aromatic_rings: int, fused_aromatic_core: bool) -> str:
    if n_aromatic_rings <= 0:
        return "simple"
    if fused_aromatic_core and n_aromatic_rings >= 2:
        return "fused"
    if n_aromatic_rings >= 3:
        return "extended"
    return "mixed" if n_aromatic_rings >= 2 else "simple"


def _separation_regime(path_length: Optional[int]) -> str:
    if path_length is None:
        return "unknown"
    if path_length <= 4:
        return "short"
    if path_length <= 7:
        return "mid"
    return "long"


def _bucket_int(value: int, low_cut: int, high_cut: int) -> str:
    if value < low_cut:
        return "low"
    if value < high_cut:
        return "mid"
    return "high"


def _heteroatom_cluster_pattern(mol: Chem.Mol) -> str:
    hetero = [atom.GetIdx() for atom in mol.GetAtoms() if atom.GetAtomicNum() not in {1, 6}]
    if len(hetero) <= 1:
        return "sparse"
    close_pairs = 0
    for i, atom_a in enumerate(hetero):
        for atom_b in hetero[i + 1 :]:
            dist = _shortest_path_length(mol, atom_a, atom_b)
            if dist is not None and dist <= 3:
                close_pairs += 1
    if close_pairs >= 2:
        return "clustered"
    if close_pairs >= 1:
        return "mixed"
    return "distributed"


def detect_structure_motifs(smiles: str, descriptors: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    txt = str(smiles or "").strip()
    mol = Chem.MolFromSmiles(txt) if txt else None
    if mol is None:
        return {
            "version": "structure_motif_v1",
            "intramolecular_hbond_motif": "unknown",
            "tautomerizable_motif": "unknown",
            "donor_acceptor_path_strength": "unknown",
            "aromatic_scaffold_type": "unknown",
            "flexibility_regime": "unknown",
            "motif_density": "unknown",
            "reliability": "low",
            "notes": ["Invalid or missing SMILES prevented motif analysis."],
        }

    desc = dict(descriptors or {})
    n_rot = int(desc.get("n_rotatable_bonds") or 0)
    n_aromatic_rings = int(desc.get("n_aromatic_rings") or 0)
    donors = sorted(_feature_atom_ids(mol, "Donor"))
    acceptors = sorted(_feature_atom_ids(mol, "Acceptor"))
    hbond_pairs, shortest_da = _count_intramolecular_hbond_pairs(mol, donors, acceptors)
    tautomer_count, tautomer_matches = _tautomerizable_candidates(mol)
    fused = _fused_aromatic_core(mol)

    if not donors or not acceptors or shortest_da is None:
        donor_acceptor_path_strength = "weak"
    elif 3 <= shortest_da <= 7:
        donor_acceptor_path_strength = "strong"
    else:
        donor_acceptor_path_strength = "mid"

    if hbond_pairs <= 0:
        hbond_motif = "none"
    elif hbond_pairs == 1:
        hbond_motif = "possible"
    else:
        hbond_motif = "likely"

    if hbond_pairs <= 0 or shortest_da is None:
        intramolecular_hbond_geometry = "none"
    elif 4 <= shortest_da <= 7:
        intramolecular_hbond_geometry = "favorable"
    else:
        intramolecular_hbond_geometry = "possible"

    if tautomer_count <= 0:
        tautomerizable_motif = "none"
    elif tautomer_count == 1:
        tautomerizable_motif = "possible"
    else:
        tautomerizable_motif = "likely"

    if tautomer_count <= 0:
        tautomerizable_subgraph_strength = "low"
    elif tautomer_count == 1:
        tautomerizable_subgraph_strength = "mid"
    else:
        tautomerizable_subgraph_strength = "high"

    if hbond_motif == "none" or tautomerizable_motif == "none":
        proton_transfer_topology_candidate = "none"
    elif intramolecular_hbond_geometry == "favorable" and tautomerizable_subgraph_strength in {"mid", "high"}:
        proton_transfer_topology_candidate = "likely"
    else:
        proton_transfer_topology_candidate = "possible"

    aromatic_scaffold_type = _aromatic_scaffold_type(n_aromatic_rings, fused)
    flexibility_regime = _bucket(float(n_rot), 2.0, 6.0)
    motif_score = len(donors) + len(acceptors) + min(hbond_pairs, 2) + min(tautomer_count, 2)
    motif_density = _bucket(float(motif_score), 2.0, 5.0)
    aromatic_rigidity_score = float(n_aromatic_rings) + (1.5 if fused else 0.0) + (1.0 if n_rot <= 1 else 0.0)
    aromatic_rigidity_signature = _bucket(aromatic_rigidity_score, 1.5, 3.5)
    fused_aromatic_core_strength = "high" if fused and n_aromatic_rings >= 2 else ("mid" if fused else "none")
    planarity_proxy = "high" if (fused and n_rot <= 1) else ("mid" if (n_aromatic_rings >= 2 and n_rot <= 3) else "low")
    donor_acceptor_separation_regime = _separation_regime(shortest_da)
    conjugation_compactness_score = float(n_aromatic_rings) + (1.0 if fused else 0.0) + (1.0 if n_rot <= 2 else 0.0)
    conjugation_compactness = _bucket(conjugation_compactness_score, 1.5, 3.5)
    donor_acceptor_fragment_balance = _bucket(float(abs(len(donors) - len(acceptors))), 1.0, 3.0)
    donor_acceptor_path_multiplicity = _bucket_int(hbond_pairs + max(0, min(len(donors), len(acceptors)) - 1), 1, 3)
    aromatic_core_connectivity = "fused" if fused else ("extended" if n_aromatic_rings >= 3 else ("linked" if n_aromatic_rings >= 2 else "simple"))
    if aromatic_rigidity_signature == "high" and flexibility_regime == "low":
        global_flexibility_vs_core_rigidity = "rigid_core"
    elif aromatic_rigidity_signature in {"mid", "high"} and flexibility_regime == "mid":
        global_flexibility_vs_core_rigidity = "rigid_core_with_mobile_periphery"
    elif aromatic_rigidity_signature == "low" and flexibility_regime == "high":
        global_flexibility_vs_core_rigidity = "globally_flexible"
    else:
        global_flexibility_vs_core_rigidity = "mixed"
    planarity_break_count = int(max(0, n_rot))
    if fused and n_rot <= 1:
        conjugation_continuity = "continuous"
    elif n_aromatic_rings >= 2 and n_rot <= 3:
        conjugation_continuity = "mostly_continuous"
    else:
        conjugation_continuity = "segmented"
    proton_transfer_local_geometry = intramolecular_hbond_geometry if proton_transfer_topology_candidate != "none" else "none"
    heteroatom_cluster_pattern = _heteroatom_cluster_pattern(mol)

    notes = [
        f"Detected {len(donors)} donor site(s) and {len(acceptors)} acceptor site(s) with {donor_acceptor_separation_regime} donor-acceptor separation.",
        f"Intramolecular H-bond geometry is {intramolecular_hbond_geometry}; tautomerizable subgraph strength is {tautomerizable_subgraph_strength}.",
        f"Aromatic scaffold is {aromatic_scaffold_type} with {aromatic_rigidity_signature} rigidity and {planarity_proxy} planarity proxy.",
    ]
    if tautomer_matches:
        notes.append(f"Matched structural motif families: {', '.join(sorted(tautomer_matches))}.")

    return {
        "version": "structure_motif_v1",
        "intramolecular_hbond_motif": hbond_motif,
        "intramolecular_hbond_geometry": intramolecular_hbond_geometry,
        "tautomerizable_motif": tautomerizable_motif,
        "proton_transfer_topology_candidate": proton_transfer_topology_candidate,
        "tautomerizable_subgraph_strength": tautomerizable_subgraph_strength,
        "donor_acceptor_path_strength": donor_acceptor_path_strength,
        "donor_acceptor_fragment_balance": donor_acceptor_fragment_balance,
        "donor_acceptor_path_multiplicity": donor_acceptor_path_multiplicity,
        "donor_acceptor_separation_regime": donor_acceptor_separation_regime,
        "aromatic_scaffold_type": aromatic_scaffold_type,
        "aromatic_rigidity_signature": aromatic_rigidity_signature,
        "fused_aromatic_core_strength": fused_aromatic_core_strength,
        "aromatic_core_connectivity": aromatic_core_connectivity,
        "global_flexibility_vs_core_rigidity": global_flexibility_vs_core_rigidity,
        "planarity_proxy": planarity_proxy,
        "planarity_break_count": planarity_break_count,
        "conjugation_compactness": conjugation_compactness,
        "conjugation_continuity": conjugation_continuity,
        "proton_transfer_local_geometry": proton_transfer_local_geometry,
        "heteroatom_cluster_pattern": heteroatom_cluster_pattern,
        "flexibility_regime": flexibility_regime,
        "motif_density": motif_density,
        "donor_sites": len(donors),
        "acceptor_sites": len(acceptors),
        "possible_intramolecular_hbond_pairs": hbond_pairs,
        "tautomerizable_motif_candidates": tautomer_count,
        "conjugation_span_bucket": _bucket(float((n_aromatic_rings * 2) + max(0, 2 - n_rot)), 2.0, 5.0),
        "fused_aromatic_core": fused,
        "reliability": "high",
        "notes": notes[:4],
    }


__all__ = ["detect_structure_motifs"]
