# Uncertainty-aware AIE Mechanism Discovery (V0)

Automated mechanistic hypothesis generation for Aggregation-Induced Emission (AIE) molecules using uncertainty quantification.

## Quick Start

### Installation

```bash
# Create virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Or use pip install in editable mode
pip install -e .
```

### Running the Pipeline

**P1: Data Standardization** (generates private_clean.parquet, molecule_table.parquet, rdkit_features.parquet)
```bash
python -m src.data.pipeline
```

Check outputs in `data/` folder and review `data/run_manifest.json` for execution details.

### Project Structure

```
.
├── src/                  # Source code
│   ├── data/            # Data loading & standardization
│   ├── chem/            # aTB wrapper (Chem Agent)
│   ├── features/        # Feature engineering & merging
│   ├── uq/              # Uncertainty quantification
│   ├── reports/         # Report generation
│   └── utils/           # Logging & helpers
├── data/                # Generated data artifacts (gitignored)
├── cache/               # aTB computation cache (gitignored)
├── reports/             # Per-molecule JSON reports (gitignored)
├── config/              # Configuration files
├── tests/               # Unit tests
└── doc/                 # Documentation
```

## Documentation

- **[CLAUDE.md](CLAUDE.md)**: Project charter, design principles, acceptance criteria
- **[doc/roadmap.md](doc/roadmap.md)**: High-level roadmap (V0 → V1 → V2)
- **[doc/process.md](doc/process.md)**: Detailed V0 implementation plan (CURRENT)
- **[doc/process_summary.md](doc/process_summary.md)**: Implementation log
- **[doc/schemas.md](doc/schemas.md)**: Data artifact schemas

## V0 Workflow

1. **Data standardization** (P1): Load `data/data.csv` → normalize units → canonicalize SMILES → generate `private_clean.parquet`
2. **aTB computation** (P2): Run aTB on unique molecules → cache results → generate `atb_features.parquet`
3. **Feature merge** (P3): Merge experimental + RDKit + aTB features → normalize → generate `X_full.parquet`
4. **Anchor index** (P4): Select high-quality anchors → build FAISS index
5. **UQ scores** (P5): Compute coverage/novelty/aleatoric → router decision → generate `uq_scores.parquet`
6. **Reports** (P6): Generate per-molecule JSON reports + hypothesis log for novelty candidates
7. **Tests** (P7): Unit tests for canonicalization, unit conversion, router logic

## Configuration

Edit `config/default.yaml` to adjust:
- UQ thresholds (coverage/novelty/aleatoric percentiles)
- Feature engineering parameters (ECFP radius, scaling method)
- Anchor selection criteria
- Logging settings

## Design Principles

1. **No LLM self-confidence**: UQ derived from structured data (observables + descriptors + anchor density)
2. **Everything auditable**: Store inputs, versions, descriptors, failures, router decisions
3. **Cache by InChIKey**: Never recompute expensive aTB calculations
4. **Explicit missingness**: Add `{field}_missing` columns, never silently impute
5. **Standardized units**: qy in [0,1], tau in ns, emission/absorption in nm
6. **Conservative router**: Only mark novelty candidate when novelty is high AND (coverage is low OR aleatoric is high)
7. **Version all runs**: Store git hash, package versions, timestamps in `run_manifest.json`

## Status

**Current milestone**: P0 complete (repo bootstrap)
**Next milestone**: P1 (data standardization)

See [doc/process.md](doc/process.md) for the full TODO checklist.

## License

[Specify license here]

## Contact

[Specify contact info here]
