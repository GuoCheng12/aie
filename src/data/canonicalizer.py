"""
src/data/canonicalizer.py

SMILES canonicalization and InChIKey generation using RDKit.
"""

import pandas as pd
from rdkit import Chem
from typing import Optional, Tuple

from src.utils.logging import get_logger

logger = get_logger(__name__)


def canonicalize_smiles(smiles: str) -> Optional[str]:
    """
    Canonicalize a SMILES string using RDKit.

    Args:
        smiles: Input SMILES string

    Returns:
        Canonical SMILES, or None if invalid
    """
    if pd.isna(smiles) or smiles == "":
        return None

    try:
        mol = Chem.MolFromSmiles(str(smiles))
        if mol is None:
            return None
        return Chem.MolToSmiles(mol, canonical=True)
    except Exception as e:
        logger.debug(f"Failed to canonicalize SMILES '{smiles}': {e}")
        return None


def smiles_to_inchikey(smiles: str) -> Optional[str]:
    """
    Convert SMILES to InChIKey using RDKit.

    Args:
        smiles: Input SMILES string

    Returns:
        InChIKey, or None if invalid
    """
    if pd.isna(smiles) or smiles == "":
        return None

    try:
        mol = Chem.MolFromSmiles(str(smiles))
        if mol is None:
            return None
        return Chem.MolToInchiKey(mol)
    except Exception as e:
        logger.debug(f"Failed to generate InChIKey for SMILES '{smiles}': {e}")
        return None


def add_canonical_smiles_and_inchikey(df: pd.DataFrame, smiles_col: str = "SMILES") -> pd.DataFrame:
    """
    Add canonical SMILES and InChIKey columns to DataFrame.

    Args:
        df: DataFrame with SMILES column
        smiles_col: Name of SMILES column (default: "SMILES")

    Returns:
        DataFrame with 'canonical_smiles' and 'inchikey' columns added
    """
    df = df.copy()

    if smiles_col not in df.columns:
        raise ValueError(f"SMILES column '{smiles_col}' not found in DataFrame")

    logger.info(f"Canonicalizing {len(df)} SMILES strings")

    # Canonicalize SMILES
    df["canonical_smiles"] = df[smiles_col].apply(canonicalize_smiles)

    # Generate InChIKey
    df["inchikey"] = df[smiles_col].apply(smiles_to_inchikey)

    # Report results
    n_valid_smiles = df["canonical_smiles"].notna().sum()
    n_valid_inchikey = df["inchikey"].notna().sum()
    n_invalid = len(df) - n_valid_smiles

    logger.info(f"Canonicalization results:")
    logger.info(f"  Valid SMILES: {n_valid_smiles}/{len(df)}")
    logger.info(f"  Valid InChIKeys: {n_valid_inchikey}/{len(df)}")
    logger.info(f"  Invalid SMILES: {n_invalid}/{len(df)}")

    if n_invalid > 0:
        logger.warning(f"{n_invalid} invalid SMILES will have null canonical_smiles/inchikey")

    return df


def create_molecule_table(df: pd.DataFrame) -> pd.DataFrame:
    """
    Create molecule table with unique molecules by InChIKey.

    Groups rows by InChIKey and creates:
    - inchikey: unique identifier
    - canonical_smiles: canonical SMILES
    - id_list: list of original row IDs mapping to this molecule
    - n_records: count of records for this molecule

    Args:
        df: DataFrame with 'id', 'inchikey', 'canonical_smiles' columns

    Returns:
        DataFrame with one row per unique InChIKey
    """
    required_cols = ["id", "inchikey", "canonical_smiles"]
    for col in required_cols:
        if col not in df.columns:
            raise ValueError(f"Required column '{col}' not found in DataFrame")

    # Filter out rows with invalid InChIKey
    valid_df = df[df["inchikey"].notna()].copy()
    n_invalid = len(df) - len(valid_df)

    if n_invalid > 0:
        logger.warning(f"Excluding {n_invalid} rows with invalid InChIKey from molecule table")

    # Group by InChIKey
    logger.info(f"Creating molecule table from {len(valid_df)} valid rows")

    molecule_table = (
        valid_df.groupby("inchikey")
        .agg(
            {
                "canonical_smiles": "first",  # Should be same for all rows with same InChIKey
                "id": lambda x: list(x),  # Collect all IDs
            }
        )
        .reset_index()
    )

    molecule_table.rename(columns={"id": "id_list"}, inplace=True)
    molecule_table["n_records"] = molecule_table["id_list"].apply(len)

    logger.info(f"Molecule table created: {len(molecule_table)} unique molecules")
    logger.info(f"  Single record: {(molecule_table['n_records'] == 1).sum()}")
    logger.info(f"  Multiple records: {(molecule_table['n_records'] > 1).sum()}")
    logger.info(f"  Max records per molecule: {molecule_table['n_records'].max()}")

    return molecule_table
