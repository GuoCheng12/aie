"""
src/data/pipeline.py

P1 Data standardization pipeline.
Generates: private_clean.parquet, molecule_table.parquet, rdkit_features.parquet, run_manifest.json
"""

import json
import sys
from datetime import datetime
from pathlib import Path
import subprocess

from src.data.loader import load_private_dataset
from src.data.standardizer import standardize_dataset
from src.data.canonicalizer import add_canonical_smiles_and_inchikey, create_molecule_table
from src.data.rdkit_descriptors import compute_rdkit_features
from src.utils.logging import setup_logger

logger = setup_logger(__name__, level="INFO")


def get_git_commit() -> str:
    """Get current git commit hash, or 'untracked' if not in git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return result.stdout.strip()
    except Exception:
        return "untracked"


def get_package_versions() -> dict:
    """Get versions of key packages."""
    versions = {}
    try:
        import rdkit
        versions["rdkit"] = rdkit.__version__
    except:
        versions["rdkit"] = "unknown"

    try:
        import pandas
        versions["pandas"] = pandas.__version__
    except:
        versions["pandas"] = "unknown"

    try:
        import numpy
        versions["numpy"] = numpy.__version__
    except:
        versions["numpy"] = "unknown"

    versions["python"] = sys.version.split()[0]

    return versions


def run_p1_pipeline(
    input_csv: str = "data/data.csv",
    output_dir: str = "data",
):
    """
    Run P1 data standardization pipeline.

    Steps:
    1. Load CSV with encoding fallback
    2. Standardize dataset (qy/tau normalization, missing masks)
    3. Add canonical SMILES + InChIKey
    4. Create molecule table (unique by InChIKey)
    5. Compute RDKit features
    6. Save artifacts
    7. Generate run manifest

    Args:
        input_csv: Path to input CSV (default: data/data.csv)
        output_dir: Output directory (default: data)
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 80)
    logger.info("P1 Data Standardization Pipeline")
    logger.info("=" * 80)

    # Step 1: Load data
    logger.info("\n[Step 1/7] Loading CSV data")
    df_raw, encoding_used = load_private_dataset(input_csv)
    n_input_rows = len(df_raw)

    # Step 2: Standardize
    logger.info("\n[Step 2/7] Standardizing dataset")
    df_clean = standardize_dataset(df_raw)

    # Step 3: Canonicalize SMILES + InChIKey
    logger.info("\n[Step 3/7] Canonicalizing SMILES and generating InChIKeys")
    df_clean = add_canonical_smiles_and_inchikey(df_clean, smiles_col="SMILES")

    # Step 4: Create molecule table
    logger.info("\n[Step 4/7] Creating molecule table")
    molecule_table = create_molecule_table(df_clean)

    # Step 5: Compute RDKit features
    logger.info("\n[Step 5/7] Computing RDKit features")
    rdkit_features = compute_rdkit_features(
        molecule_table,
        smiles_col="canonical_smiles",
        ecfp_radius=2,
        ecfp_bits=2048,
    )

    # Step 6: Save artifacts
    logger.info("\n[Step 6/7] Saving artifacts")

    # private_clean.parquet
    clean_path = output_path / "private_clean.parquet"
    df_clean.to_parquet(clean_path, index=False)
    logger.info(f"Saved: {clean_path} ({len(df_clean)} rows, {len(df_clean.columns)} columns)")

    # molecule_table.parquet
    mol_table_path = output_path / "molecule_table.parquet"
    molecule_table.to_parquet(mol_table_path, index=False)
    logger.info(f"Saved: {mol_table_path} ({len(molecule_table)} unique molecules)")

    # rdkit_features.parquet
    rdkit_path = output_path / "rdkit_features.parquet"
    rdkit_features.to_parquet(rdkit_path, index=False)
    logger.info(f"Saved: {rdkit_path} ({len(rdkit_features)} molecules)")

    # Step 7: Generate run manifest
    logger.info("\n[Step 7/7] Generating run manifest")
    manifest = {
        "run_id": datetime.now().isoformat(),
        "timestamp": datetime.now().isoformat(),
        "git_commit": get_git_commit(),
        **get_package_versions(),
        "encoding_used": encoding_used,
        "n_molecules_input": int(n_input_rows),
        "n_molecules_processed": int(len(df_clean)),
        "n_unique_molecules": int(len(molecule_table)),
        "n_valid_inchikeys": int(df_clean["inchikey"].notna().sum()),
        "artifacts": {
            "private_clean": str(clean_path),
            "molecule_table": str(mol_table_path),
            "rdkit_features": str(rdkit_path),
        },
    }

    manifest_path = output_path / "run_manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    logger.info(f"Saved: {manifest_path}")

    logger.info("\n" + "=" * 80)
    logger.info("P1 Pipeline Complete!")
    logger.info("=" * 80)
    logger.info(f"Input rows: {n_input_rows}")
    logger.info(f"Output rows: {len(df_clean)}")
    logger.info(f"Unique molecules: {len(molecule_table)}")
    logger.info(f"Encoding used: {encoding_used}")


if __name__ == "__main__":
    run_p1_pipeline()
