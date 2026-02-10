"""
Batch-create SMILES-first case files from data/test.csv.

This utility is evaluation-only:
- reads test rows
- creates case files from SMILES
- never writes to private_clean/evidence_table
"""

import argparse
from pathlib import Path

import pandas as pd

from src.cases.create_case_from_smiles import create_case_from_smiles


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch create case files from test.csv (eval-only)")
    parser.add_argument("--input-csv", default="data/test.csv", help="Path to test CSV")
    parser.add_argument("--smiles-col", default="SMILES", help="SMILES column name")
    parser.add_argument("--outdir", default="cases/test_inputs", help="Output case directory")
    parser.add_argument("--k", type=int, default=10, help="Top-k neighbors")
    parser.add_argument("--limit", type=int, default=None, help="Optional row limit")
    args = parser.parse_args()

    csv_path = Path(args.input_csv)
    if not csv_path.exists():
        raise FileNotFoundError(f"Input CSV not found: {csv_path}")

    df = pd.read_csv(csv_path)
    if args.smiles_col not in df.columns:
        raise ValueError(f"Missing SMILES column '{args.smiles_col}' in {csv_path}")

    rows = df if args.limit is None else df.head(args.limit)

    total = len(rows)
    ok = 0
    failed = 0
    for idx, row in rows.iterrows():
        smiles = row.get(args.smiles_col)
        if pd.isna(smiles) or str(smiles).strip() == "":
            failed += 1
            continue
        try:
            create_case_from_smiles(str(smiles), k=args.k, outdir=args.outdir)
            ok += 1
        except Exception:
            failed += 1

    print(f"input_rows={total} created_cases={ok} failed={failed} outdir={args.outdir}")


if __name__ == "__main__":
    main()
