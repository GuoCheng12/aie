"""
Verification script: Compare our Tanimoto implementation against RDKit's official DataStructs.TanimotoSimilarity

This script:
1. Loads pairs from anchor_neighbors_ecfp.parquet (especially sim=1.0 and >=0.95)
2. Recomputes Tanimoto using RDKit's official implementation
3. Compares results to detect any bugs in our implementation
"""

import pandas as pd
import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem, DataStructs

# Load data
print("Loading data...")
neighbors_df = pd.read_parquet("data/anchor_neighbors_ecfp.parquet")
molecule_table = pd.read_parquet("data/molecule_table.parquet")

# Build InChIKey → SMILES lookup
smiles_lookup = molecule_table.set_index("inchikey")["canonical_smiles"].to_dict()

def get_rdkit_fingerprint(smiles: str, radius: int = 2, n_bits: int = 2048):
    """Get RDKit Morgan fingerprint as ExplicitBitVect."""
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    try:
        from rdkit.Chem import rdFingerprintGenerator
        fpgen = rdFingerprintGenerator.GetMorganGenerator(radius=radius, fpSize=n_bits)
        return fpgen.GetFingerprint(mol)
    except (ImportError, AttributeError):
        return AllChem.GetMorganFingerprintAsBitVect(mol, radius, nBits=n_bits)

def rdkit_tanimoto(smiles1: str, smiles2: str) -> float:
    """Compute Tanimoto using RDKit's official implementation."""
    fp1 = get_rdkit_fingerprint(smiles1)
    fp2 = get_rdkit_fingerprint(smiles2)
    if fp1 is None or fp2 is None:
        return None
    return DataStructs.TanimotoSimilarity(fp1, fp2)

# Get pairs to verify
print("\n" + "=" * 70)
print("TANIMOTO VERIFICATION: Our Implementation vs RDKit DataStructs")
print("=" * 70)

# 1. Get all sim=1.0 pairs
top1_df = neighbors_df[neighbors_df["rank"] == 1]
sim_1_pairs = top1_df[top1_df["tanimoto_sim"] >= 0.9999].head(10)

# 2. Get sim >= 0.95 but < 1.0 pairs
sim_high_pairs = top1_df[(top1_df["tanimoto_sim"] >= 0.95) & (top1_df["tanimoto_sim"] < 0.9999)].head(5)

# 3. Get some medium similarity pairs
sim_med_pairs = top1_df[(top1_df["tanimoto_sim"] >= 0.5) & (top1_df["tanimoto_sim"] < 0.6)].head(5)

# Combine all pairs to verify
pairs_to_verify = pd.concat([sim_1_pairs, sim_high_pairs, sim_med_pairs])

print(f"\nVerifying {len(pairs_to_verify)} pairs:")
print(f"  - sim=1.0: {len(sim_1_pairs)} pairs")
print(f"  - sim>=0.95: {len(sim_high_pairs)} pairs")
print(f"  - sim 0.5-0.6: {len(sim_med_pairs)} pairs")

print("\n" + "-" * 70)
print(f"{'InChIKey 1':<30} {'InChIKey 2':<30} {'Our Sim':>8} {'RDKit':>8} {'Match':>6}")
print("-" * 70)

consistent = 0
inconsistent = 0
errors = 0

for _, row in pairs_to_verify.iterrows():
    ik1 = row["inchikey"]
    ik2 = row["neighbor_inchikey"]
    our_sim = row["tanimoto_sim"]

    smiles1 = smiles_lookup.get(ik1)
    smiles2 = smiles_lookup.get(ik2)

    if smiles1 is None or smiles2 is None:
        print(f"{ik1[:28]:<30} {ik2[:28]:<30} {our_sim:>8.4f} {'N/A':>8} {'ERR':>6}")
        errors += 1
        continue

    rdkit_sim = rdkit_tanimoto(smiles1, smiles2)

    if rdkit_sim is None:
        print(f"{ik1[:28]:<30} {ik2[:28]:<30} {our_sim:>8.4f} {'N/A':>8} {'ERR':>6}")
        errors += 1
        continue

    # Check if values match (within floating point tolerance)
    is_match = abs(our_sim - rdkit_sim) < 1e-6
    match_str = "OK" if is_match else "DIFF"

    if is_match:
        consistent += 1
    else:
        inconsistent += 1

    print(f"{ik1[:28]:<30} {ik2[:28]:<30} {our_sim:>8.4f} {rdkit_sim:>8.4f} {match_str:>6}")

print("-" * 70)

# Summary
print("\n" + "=" * 70)
print("VERIFICATION SUMMARY")
print("=" * 70)
print(f"  Consistent (match):     {consistent}")
print(f"  Inconsistent (differ):  {inconsistent}")
print(f"  Errors (N/A):           {errors}")
print(f"  Total verified:         {consistent + inconsistent + errors}")

if inconsistent == 0 and errors == 0:
    print("\n  RESULT: Implementation is CORRECT")
elif inconsistent > 0:
    print(f"\n  RESULT: BUG DETECTED - {inconsistent} pairs have different Tanimoto values")
else:
    print(f"\n  RESULT: Could not fully verify due to {errors} errors")

# Additional debug: For any inconsistent pairs, show details
if inconsistent > 0:
    print("\n" + "=" * 70)
    print("DEBUG: Investigating inconsistent pairs")
    print("=" * 70)

    for _, row in pairs_to_verify.iterrows():
        ik1 = row["inchikey"]
        ik2 = row["neighbor_inchikey"]
        our_sim = row["tanimoto_sim"]

        smiles1 = smiles_lookup.get(ik1)
        smiles2 = smiles_lookup.get(ik2)

        if smiles1 is None or smiles2 is None:
            continue

        rdkit_sim = rdkit_tanimoto(smiles1, smiles2)

        if rdkit_sim is not None and abs(our_sim - rdkit_sim) >= 1e-6:
            print(f"\nPair: {ik1} - {ik2}")
            print(f"  Our Tanimoto: {our_sim:.6f}")
            print(f"  RDKit Tanimoto: {rdkit_sim:.6f}")
            print(f"  Difference: {abs(our_sim - rdkit_sim):.6f}")
            print(f"  SMILES 1: {smiles1[:100]}...")
            print(f"  SMILES 2: {smiles2[:100]}...")
