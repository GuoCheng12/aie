"""
src/data/rdkit_descriptors.py

Compute RDKit molecular descriptors (ECFP fingerprints and basic descriptors).
"""

import pandas as pd
import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem, Descriptors, Crippen, Lipinski
from typing import Optional, Dict

from src.utils.logging import get_logger

logger = get_logger(__name__)


def compute_ecfp(smiles: str, radius: int = 2, n_bits: int = 2048) -> Optional[np.ndarray]:
    """
    Compute ECFP (Morgan) fingerprint from SMILES.

    Args:
        smiles: SMILES string
        radius: Fingerprint radius (default: 2 for ECFP4)
        n_bits: Number of bits (default: 2048)

    Returns:
        Fingerprint as numpy array, or None if invalid
    """
    if pd.isna(smiles) or smiles == "":
        return None

    try:
        mol = Chem.MolFromSmiles(str(smiles))
        if mol is None:
            return None

        # Use newer MorganGenerator API if available (RDKit 2023.9+)
        try:
            from rdkit.Chem import rdFingerprintGenerator
            fpgen = rdFingerprintGenerator.GetMorganGenerator(radius=radius, fpSize=n_bits)
            fp = fpgen.GetFingerprint(mol)
        except (ImportError, AttributeError):
            # Fallback to old API for older RDKit versions
            fp = AllChem.GetMorganFingerprintAsBitVect(mol, radius, nBits=n_bits)

        return np.array(fp, dtype=np.int8)
    except Exception as e:
        logger.debug(f"Failed to compute ECFP for SMILES '{smiles}': {e}")
        return None


def compute_basic_descriptors(smiles: str) -> Dict[str, Optional[float]]:
    """
    Compute basic molecular descriptors from SMILES.

    Descriptors:
    - mw: Molecular weight
    - logp: Crippen LogP
    - tpsa: Topological polar surface area
    - n_rotatable_bonds: Number of rotatable bonds
    - n_hbd: Number of H-bond donors
    - n_hba: Number of H-bond acceptors
    - n_rings: Number of rings
    - n_aromatic_rings: Number of aromatic rings
    - n_heavy_atoms: Number of heavy atoms

    Args:
        smiles: SMILES string

    Returns:
        Dictionary of descriptor values
    """
    result = {
        "mw": None,
        "logp": None,
        "tpsa": None,
        "n_rotatable_bonds": None,
        "n_hbd": None,
        "n_hba": None,
        "n_rings": None,
        "n_aromatic_rings": None,
        "n_heavy_atoms": None,
    }

    if pd.isna(smiles) or smiles == "":
        return result

    try:
        mol = Chem.MolFromSmiles(str(smiles))
        if mol is None:
            return result

        result["mw"] = Descriptors.MolWt(mol)
        result["logp"] = Crippen.MolLogP(mol)
        result["tpsa"] = Descriptors.TPSA(mol)
        result["n_rotatable_bonds"] = Lipinski.NumRotatableBonds(mol)
        result["n_hbd"] = Lipinski.NumHDonors(mol)
        result["n_hba"] = Lipinski.NumHAcceptors(mol)
        result["n_rings"] = Lipinski.RingCount(mol)
        result["n_aromatic_rings"] = Lipinski.NumAromaticRings(mol)
        result["n_heavy_atoms"] = Lipinski.HeavyAtomCount(mol)

        return result
    except Exception as e:
        logger.debug(f"Failed to compute descriptors for SMILES '{smiles}': {e}")
        return result


def compute_rdkit_features(
    df: pd.DataFrame,
    smiles_col: str = "canonical_smiles",
    ecfp_radius: int = 2,
    ecfp_bits: int = 2048,
) -> pd.DataFrame:
    """
    Compute RDKit features for a molecule table.

    Creates a feature table with one row per unique InChIKey.

    Args:
        df: DataFrame with inchikey and canonical_smiles columns
        smiles_col: Column name for SMILES (default: canonical_smiles)
        ecfp_radius: ECFP radius (default: 2)
        ecfp_bits: ECFP bit count (default: 2048)

    Returns:
        DataFrame with inchikey + RDKit features
    """
    if "inchikey" not in df.columns or smiles_col not in df.columns:
        raise ValueError(f"DataFrame must have 'inchikey' and '{smiles_col}' columns")

    logger.info(f"Computing RDKit features for {len(df)} molecules")

    # Compute ECFP fingerprints
    logger.info(f"Computing ECFP{ecfp_radius*2} fingerprints ({ecfp_bits} bits)")
    ecfp_list = []
    for smiles in df[smiles_col]:
        fp = compute_ecfp(smiles, radius=ecfp_radius, n_bits=ecfp_bits)
        ecfp_list.append(fp)

    # Compute basic descriptors
    logger.info("Computing basic molecular descriptors")
    descriptor_dicts = df[smiles_col].apply(compute_basic_descriptors).tolist()
    descriptors_df = pd.DataFrame(descriptor_dicts)

    # Combine results
    features_df = pd.DataFrame({"inchikey": df["inchikey"]})
    features_df = pd.concat([features_df, descriptors_df], axis=1)
    features_df["ecfp_2048"] = ecfp_list

    # Report results
    n_valid_ecfp = sum(fp is not None for fp in ecfp_list)
    n_valid_desc = descriptors_df["mw"].notna().sum()

    logger.info(f"RDKit features computed:")
    logger.info(f"  Valid ECFP: {n_valid_ecfp}/{len(df)}")
    logger.info(f"  Valid descriptors: {n_valid_desc}/{len(df)}")

    return features_df
