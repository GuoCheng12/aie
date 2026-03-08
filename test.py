import pandas as pd
import numpy as np

df = pd.read_parquet("data/rdkit_features.parquet")

print("=== Column names (excluding inchikey) ===")
print([c for c in df.columns if c != "inchikey"])

print("\n=== DataFrame shape ===")
print(f"Rows: {len(df)}, Columns: {len(df.columns)}")

print("\n=== First 5 rows (basic descriptors) ===")
print(df[['inchikey', 'mw', 'logp', 'tpsa', 'n_heavy_atoms']].head())

print("\n=== ECFP fingerprint examples ===")
for i in range(3):
    fp = df['ecfp_2048'].iloc[i]
    ik = df['inchikey'].iloc[i]
    print(f"\nRow {i}: {ik}")
    print(f"  Type: {type(fp)}")
    print(f"  Shape: {fp.shape if hasattr(fp, 'shape') else 'N/A'}")
    print(f"  Non-zero bits: {np.sum(fp != 0)}")
    print(f"  First 50 bits: {fp[:50].tolist()}")
    # Show which bit positions are set to 1
    nonzero_positions = np.where(fp != 0)[0]
    print(f"  Non-zero positions (first 20): {nonzero_positions[:20].tolist()}")
