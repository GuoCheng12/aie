# doc/process.md

## V0 Detailed Plan (CURRENT)

> Rules:
> - Update this file BEFORE coding every meaningful change.
> - This file contains ONLY the CURRENT version's detailed plan (V0 now).
> - When switching to V1, archive this file as `doc/process_v0.md` and create a new `doc/process.md` for V1.

---

## V0 Implementation TODO

### P0. Repo bootstrap
- [x] Create directory structure (src/, data/, cache/, config/, reports/, tests/)
- [x] Create `.gitignore` (ignore data/, cache/, reports/, *.pkl, *.parquet, etc.)
- [x] Create `config/default.yaml` (placeholder for future configs)
- [x] Create `src/utils/logging.py` (basic logging setup)
- [x] Create `pyproject.toml` or `requirements.txt` (rdkit, pandas, numpy, pyarrow, faiss-cpu)

### P1. Data standardization ✅ COMPLETE
- [x] Create `src/data/loader.py` (CSV encoding fallback, load data/data.csv)
- [x] Create `src/data/standardizer.py` (qy/tau normalization, missing masks)
- [x] Create `src/data/canonicalizer.py` (RDKit SMILES → InChIKey)
- [x] Create `src/data/rdkit_descriptors.py` (compute ECFP, MW, LogP, TPSA, etc.)
- [x] Create `src/data/pipeline.py` (P1 main pipeline script)
- [x] **Execute**: Install dependencies (`pip install -r requirements.txt`) and run `python -m src.data.pipeline`
- [x] Generate `data/private_clean.parquet` (1225 rows, 77 columns, 221K)
- [x] Generate `data/molecule_table.parquet` (1050 unique molecules, 65K)
- [x] Generate `data/rdkit_features.parquet` (1050 molecules, 123K)
- [x] Generate `data/run_manifest.json` (encoding: latin1, rdkit 2025.09.3)

### P1.5. Mode A orchestration skeleton (P2 prep) ✅ COMPLETE
- [x] Create `src/agents/data_agent.py` (fetch record by id/inchikey from parquet)
- [x] Create `src/agents/atb_agent.py` (check cache, load status, mark pending)
- [x] Create `src/cli.py` (CLI with fetch/compute-atb/run commands)
- [x] Add minimal tests (`tests/test_data_agent.py`, `tests/test_atb_agent.py`)
- [x] CLI commands working: `python -m src.cli run --id <id>`

### P2. aTB wrapper (Chem Agent)
- [x] Create `src/chem/atb_runner.py` (subprocess wrapper for `third_party/aTB/main.py`)
- [x] Create `src/chem/atb_parser.py` (parse result.json → features.json)
- [x] Create `src/chem/batch_runner.py` (iterate molecule_table, call runner, update status)
- [x] Implement resumability logic (skip if status.json run_status=="success" or "failed")
- [x] Add `--retry-failed` flag for selective retry
- [x] Skip ionic molecules in V0 (see DEFERRED below)
- [x] Improve RDKit conformer generation in `third_party/aTB/main.py` (ETKDG + fallback + UFF optimize)
- [x] Add optional size filter in batch runner (`--max-heavy-atoms`) to skip large molecules and record `fail_stage="size"`
- [x] Update fail_stage detection to classify RDKit embedding failures as `conformer` and document new `size` stage
- [x] Make RDKit ETKDG parameter setting compatible across versions (guard `maxAttempts`)
- [x] Add CLI flag to include ionic molecules (override V0 skip)
- [ ] Generate `data/atb_features.parquet`
- [ ] Generate `data/atb_qc.parquet`
- [ ] Batch run validation on neutral molecules

**DEFERRED (V0)**: Ionic molecule support
- Ionic molecules (~72 of 1050, 7%) are skipped with `run_status="skipped"`, `fail_stage="ionic"` by default
- Charge auto-detection added to `third_party/aTB/main.py` (ready but not validated)
- Re-enable after validating charge handling on a few test ionic molecules (or use `--include-ionic` for ad-hoc runs)

### P3. Feature merge
- [ ] Create `src/features/merger.py` (join private_clean + rdkit + atb on inchikey)
- [ ] Create `src/features/scaler.py` (z-score normalization, save scaler)
- [ ] Generate `data/X_full.parquet`
- [ ] Generate `data/feature_config.yaml`
- [ ] Generate `data/scaler.pkl`

### P4. Anchor reference space + index
- [ ] Create `src/features/anchor_selector.py` (filter by completeness + atb_available)
- [ ] Create `src/features/indexer.py` (build FAISS index, top-k query API)
- [ ] Generate `data/anchor_index.faiss`
- [ ] Generate `data/anchor_meta.parquet`

### P5. UQ scores + router
- [ ] Create `src/uq/coverage.py` (C_sim + C_meta computation)
- [ ] Create `src/uq/novelty.py` (kNN distance, percentile normalization)
- [ ] Create `src/uq/aleatoric.py` (GMM fit, entropy computation)
- [ ] Create `src/uq/router.py` (deterministic if/elif cascade logic)
- [ ] Generate `data/uq_scores.parquet`
- [ ] Update `data/feature_config.yaml` (add K, thresholds)

### P6. Reports + hypothesis log
- [ ] Create `src/reports/generator.py` (per-molecule JSON report)
- [ ] Create `src/reports/hypothesis_logger.py` (append to hypothesis_log)
- [ ] Generate `reports/{id}.json` for all molecules
- [ ] Generate `data/hypothesis_log.parquet` (or .jsonl)

### P7. Minimal tests
- [ ] Create `tests/test_canonicalization.py` (SMILES → InChIKey consistency)
- [ ] Create `tests/test_units.py` (qy/tau normalization correctness)
- [ ] Create `tests/test_router.py` (router determinism + thresholds)

---

### Progress Update Rule
- When a milestone subtask is completed, change `- [ ]` to `- [x]`.
- After completing any full milestone (P0, P1, P2, etc.), append a dated entry to `doc/process_summary.md` with:
  - What was implemented
  - Files/outputs produced
  - Issues encountered (if any)
  - Next actions

---

## V0 Goal
Build a closed-loop pipeline on the private dataset (1000+ rows):
1) clean + standardize data
2) compute RDKit + aTB descriptors (with caching and failure tracking)
3) merge features into `X_full`
4) compute UQ scores (coverage/novelty/aleatoric) + router action
5) generate per-molecule reports + novelty hypothesis log

---

## Inputs
### Private dataset columns (given)
`id, code, AggIndex, SMILES, color_in_powder, molecular_weight, absorption,
emission_sol, emission_solid, emission_aggr, emission_crys,
qy_sol, qy_solid, qy_aggr, qy_crys,
tau_sol, tau_solid, tau_aggr, tau_crys,
features_id, mechanism_id,
photostability, thermostability,
solubility_* (multiple), pka, comment, tested_solvent,
application1..4, molar_*`

### External tools
- RDKit for canonicalization + descriptors
- aTB pipeline (AIE-aTB) wrapped as a “Chem Agent”
- Feature index: FAISS (preferred) or pgvector (optional)

---

## V0 Milestones (P0–P7)

### P0. Repo bootstrap
**Scope**
- Create project directory structure
- Add config placeholder(s) and logging conventions
- Add `.gitignore` for generated data outputs

**Outputs**
- repo skeleton ready

---

### P1. Data standardization (Data Agent output contract)
**Scope**
- Parse private CSV
- Enforce types, normalize missing values (`null`)
- Canonicalize SMILES via RDKit, compute InChIKey
- Create missing masks for critical fields

**CSV Encoding Protocol**
- Try encodings in order: `utf-8-sig` → `utf-8` → `gb18030` → `latin1`
- Record `encoding_used` in `data/run_manifest.json`
- Fail loudly if all encodings fail

**Unit Normalization Rules**

1. **qy_* columns** (quantum yield):
   - Data is in **percent (0–100)**. Normalize: `qy = qy_raw / 100` → [0,1]
   - Keep `qy_{condition}_raw` (original percent value)
   - Store `qy_unit_inferred = "percent"` (constant for this dataset)
   - Add `qy_inferred_confidence = "high"` (based on max values clearly >1)

2. **tau_* columns** (lifetime):
   - Default unit: **ns** (based on bulk median ~few units)
   - Keep `tau_{condition}_raw` (original value)
   - Flag outliers: `tau_{condition}_outlier = True` if value > 3×IQR above Q3 OR > 1000 ns
   - Optionally compute `tau_{condition}_log = log10(tau + 1e-9)` for modeling
   - Support `config/units_override.yaml` for manual per-row corrections (future)

3. **absorption/emission**:
   - Preserve raw string in `absorption` column
   - Parse peak wavelength to `absorption_peak_nm` if extractable (regex for numeric nm values)
   - emission_* columns assumed to be in nm; keep as-is

**Missing Value Protocol**
- For each critical field F, add boolean column `{F}_missing` (True = null/NaN/empty)
- Critical fields: `emission_sol`, `emission_solid`, `emission_aggr`, `emission_crys`, `qy_sol`, `qy_solid`, `qy_aggr`, `qy_crys`, `tau_sol`, `tau_solid`, `tau_aggr`, `tau_crys`, `absorption`, `tested_solvent`
- Downstream usage:
  - Coverage `C_meta` penalizes missingness (1 - missing_rate)
  - UQ calculations mask missing values (not imputed)
  - Reports show missingness summary

**Outputs**
- `data/private_clean.parquet` (see `doc/schemas.md` for columns)
- `data/molecule_table.parquet` (unique inchikey + canonical_smiles + id_list mapping)
- `data/rdkit_features.parquet` (ECFP + basic descriptors)
- `data/run_manifest.json` (encoding, versions, counts)

---

### P1.5. Mode A orchestration skeleton (P2 prep)
**Scope**
- Build minimal single-molecule query orchestration (NOT batch aTB computation yet)
- Enable end-to-end workflow: fetch record → check aTB cache → assemble output
- Prepare cache infrastructure for future P2 batch computation

**Goal**
Given an experimental `id`, the system can:
1. Fetch the cleaned record + inchikey/smiles from `data/private_clean.parquet`
2. Check whether aTB cache exists for that molecule (inchikey)
3. If cache hit: load cached aTB features/status and assemble output
4. If cache miss: create a placeholder 'pending' status.json and return clear message
5. Generate structured JSON output for the given id (optionally write `reports/{id}.json`)

**Modules**

1. **src/agents/data_agent.py**
   - `get_record_by_id(id: int) -> dict`: Fetch record from private_clean.parquet
   - `get_molecule_by_inchikey(inchikey: str) -> dict`: Fetch molecule from molecule_table.parquet
   - Error handling for missing ids/inchikeys

2. **src/agents/atb_agent.py**
   - `get_cache_path(inchikey: str) -> Path`: Return cache directory path
   - `check_cache(inchikey: str) -> bool`: Check if cache exists
   - `load_status(inchikey: str) -> dict`: Load status.json from cache
   - `mark_pending(inchikey: str) -> None`: Create placeholder status.json with run_status="pending"
   - Uses cache structure: `cache/atb/{inchikey[:2]}/{inchikey}/status.json`

3. **src/cli.py**
   - CLI commands using argparse:
     - `fetch --id <id>`: Fetch and display record from parquet
     - `compute-atb --id <id>`: Check cache + mark pending if missing (NO real computation)
     - `run --id <id>`: Full orchestration (fetch + atb check + assemble + report)
   - Output structured JSON to stdout
   - Optionally write to `reports/{id}.json`

**Output JSON Schema**
```json
{
  "id": 123,
  "inchikey": "XXXXX-YYYYY-Z",
  "canonical_smiles": "...",
  "record_fields": {
    "emission_sol": 450.0,
    "qy_sol": 0.65,
    "tau_sol": 3.2,
    "...": "..."
  },
  "missing_summary": {
    "n_missing": 3,
    "missing_fields": ["emission_crys", "qy_crys", "tau_crys"]
  },
  "atb_status": "hit|miss|pending",
  "atb_features": {...} or null,
  "paths": {
    "cache_dir": "cache/atb/XX/XXXXX-YYYYY-Z/",
    "report_path": "reports/123.json"
  }
}
```

**Constraints**
- NO real aTB computation (defer to P2)
- NO batch processing (single-molecule only)
- Cache placeholder only (status.json with run_status="pending")
- Keep minimal and clean

**Schema Enforcement**
- **status.json**: STRICT adherence to 7-field schema (inchikey, run_status, fail_stage, error_msg, timestamp, atb_version, runtime_sec)
  - NO extra fields like "canonical_smiles" or "note" in status.json
  - SMILES stored separately in `canonical_smiles.txt` if provided
- **Report fields**: STRICT allowlist to exclude sensitive fields
  - Allowlist: ~60 fields (photophysical properties, observables, IDs, normalized values, missing indicators)
  - Blocklist: `comment` field (may contain sensitive researcher notes)
  - See `src/cli.py:REPORT_FIELD_ALLOWLIST` and `REPORT_FIELD_BLOCKLIST`

**Tests**
- `tests/test_data_agent.py`: Test fetching known id, error handling for missing id
- `tests/test_atb_agent.py`: Test cache path generation, mark_pending functionality, STRICT schema validation
- `tests/test_cli.py`: Test report field filtering (allowlist/blocklist compliance)

**Outputs**
- `src/agents/data_agent.py`
- `src/agents/atb_agent.py`
- `src/cli.py`
- `tests/test_data_agent.py`
- `tests/test_atb_agent.py`
- CLI executable: `python -m src.cli run --id <id>`

---

### P2. aTB wrapper (Chem Agent)
**Scope**
- Batch-run aTB for unique molecules (by inchikey), with caching and resumability.
- Record stage failures and provide audit trail.
- Output structured per-molecule results and a consolidated table.

**V0 Implementation: Black-box Integration**

For V0, the aTB agent calls `third_party/aTB/main.py` as a **black-box subprocess**:

```bash
# Entrypoint (from project root)
python third_party/aTB/main.py \
    --smiles '<canonical_smiles>' \
    --workdir 'cache/atb/{inchikey[:2]}/{inchikey}' \
    --npara 4 --maxcore 4000
```

**Key CLI arguments**:
| Argument | Default | Description |
|----------|---------|-------------|
| `--smiles` | None | Canonical SMILES (from molecule_table) |
| `--workdir` | `work_dirs` | Output directory = our cache path |
| `--nimg` | 3 | NEB intermediate images |
| `--npara` | 2 | Amesp parallel processes |
| `--maxcore` | 4000 | Memory per core (MB) |

**Cache Structure** (after AIE-aTB run)
```
cache/atb/
├── {inchikey[:2]}/           # 2-char prefix for filesystem efficiency
│   └── {inchikey}/
│       ├── status.json       # Our run metadata (strict 7-field schema)
│       ├── features.json     # Parsed descriptors (from result.json)
│       ├── canonical_smiles.txt  # SMILES audit copy
│       ├── opt/
│       │   ├── opt_run.aip   # Amesp input
│       │   ├── opt_run.aop   # Amesp output (~3MB)
│       │   └── opted.xyz     # Optimized S0 geometry
│       ├── excit/
│       │   ├── excit_run.aip
│       │   ├── excit_run.aop
│       │   └── excited.xyz   # Optimized S1 geometry
│       ├── neb/
│       │   ├── neb.log       # NEB optimization log
│       │   └── volume_results/
│       │       └── volumes.log
│       └── result.json       # ★ AIE-aTB primary output (we parse this)
```

**status.json schema:**
```json
{
  "inchikey": "XXXXX-YYYYY-Z",
  "run_status": "success|failed|pending|skipped",
  "fail_stage": null | "conformer" | "opt" | "excit" | "neb" | "volume" | "feature_parse" | "timeout" | "ionic" | "size",
  "error_msg": null | "truncated error (max 500 chars)",
  "timestamp": "ISO 8601",
  "atb_version": "x.x.x",
  "runtime_sec": 123.4
}
```

**Note**: `run_status="skipped"` with `fail_stage="ionic"` indicates molecules temporarily skipped in V0 due to ionic charge handling limitations.

**AIE-aTB result.json → features.json Mapping**

AIE-aTB writes `result.json` in the workdir. We parse it to create our `features.json`:

```
result.json structure:
{
  "ground_state": {
    "HOMO-LUMO": "1.83",           // string → s0_homo_lumo_gap (float)
    "structure": {
      "bonds": 1.23,               // avg bond length (Å)
      "angles": 115.4,             // avg bond angle (°)
      "DA": 5.88                   // avg dihedral → s0_dihedral_avg
    },
    "volume": 513.0,               // → s0_volume (Å³)
    "charge": {...}                // per-atom Mulliken charges
  },
  "excited_state": {
    "HOMO-LUMO": "1.54",           // → s1_homo_lumo_gap
    "structure": {...},            // DA → s1_dihedral_avg
    "volume": 513.0,               // → s1_volume
    "charge": {...}
  },
  "NEB": 512.8                     // mean NEB volume (informational)
}
```

**features.json schema** (what we store):
```json
{
  "s0_volume": 513.0,
  "s1_volume": 513.0,
  "delta_volume": 0.0,
  "s0_homo_lumo_gap": 1.83,
  "s1_homo_lumo_gap": 1.54,
  "delta_gap": -0.29,
  "s0_dihedral_avg": 5.88,
  "s1_dihedral_avg": -2.81,
  "delta_dihedral": -8.69,
  "s0_charge_dipole": null,
  "s1_charge_dipole": null,
  "delta_dipole": null,
  "excitation_energy": null,
  "neb_mean_volume": 512.8
}
```

**Failure Stage Detection** (in order):
1. Check stderr for "Bad Conformer Id" or "RDKit embedding failed" → `"conformer"` (RDKit failed to generate 3D structure)
2. Check stderr for "CalculationFailed" or "error code -11" → stage based on path in error
3. Check stderr for "IndexError" + "parse_aop_energy" → amesp output parsing failure
4. Check if `result.json` exists and is valid JSON → `"feature_parse"` if fails
5. Check for `ground_state` key → `"opt"` if missing
6. Check for `excited_state` key → `"excit"` if missing
7. Check for `NEB` key → `"neb"` if missing
8. Check for `volume` in both states → `"volume"` if missing
9. Timeout → `"timeout"` (default 3600s)

**P2 Implementation Notes**

Critical design decisions to prevent cache/input drift:

1. **SMILES Source (Single Source of Truth)**
   - P2 batch runner MUST iterate over `data/molecule_table.parquet` (1050 unique InChIKeys)
   - Use `canonical_smiles` from molecule_table for aTB input
   - DO NOT iterate over `data/private_clean.parquet` (contains duplicate InChIKeys)
   - `cache/.../canonical_smiles.txt` is for audit/debug ONLY; do NOT read from it as input source

   ```python
   # CORRECT: P2 batch runner
   molecule_table = pd.read_parquet("data/molecule_table.parquet")
   for _, row in molecule_table.iterrows():
       inchikey = row["inchikey"]
       smiles = row["canonical_smiles"]  # ✅ Single source of truth
       run_atb(inchikey, smiles)

   # WRONG: Do not use private_clean (has duplicates)
   # WRONG: Do not read from cache/canonical_smiles.txt (circular dependency)
   ```

2. **atb_version Handling**
   - `atb_version` MUST remain `null` for `run_status == "pending"`
   - Set `atb_version` ONLY after successful computation completes
   - Recommended format: `"AIE-aTB-{git_hash}"` (e.g., `"AIE-aTB-abc1234"`)
   - Represents actual computation provenance, not intent

   ```python
   # After successful aTB computation:
   status["atb_version"] = get_atb_version()  # e.g., subprocess git rev-parse --short HEAD
   status["run_status"] = "success"
   ```

3. **Cache Consistency**
   - molecule_table is authoritative InChIKey→SMILES registry (created in P1)
   - Mode A stores SMILES to cache for auditability (redundant but harmless)
   - P2 should store SMILES from molecule_table (consistent source)

**Failure Handling Policy (V0)**
- On failure, record `fail_stage` and `error_msg` (truncated to 500 chars)
- **No automatic retry in V0**. Router marks as `Evidence-insufficient`.
- `recommended_next_steps` populated based on fail_stage:
  - `conformer` failure: `["check_smiles_validity", "try_alternative_smiles", "manual_structure"]`
  - `opt` failure: `["retry_with_different_conformer", "check_smiles_validity"]`
  - `excit` failure: `["skip_excited_state", "use_simpler_method"]`
  - `neb` failure: `["skip_neb", "use_relaxed_scan"]`
  - `volume` failure: `["retry_volume_calc"]`
  - `feature_parse` failure: `["manual_inspection", "report_bug"]`
  - `timeout` failure: `["increase_timeout", "check_molecule_size", "simplify_calculation"]`
  - `size` failure: `["retry_with_more_memory", "reduce_npara", "skip_large_molecule"]`
- Partial results: If S0 succeeds but S1 fails, keep S0 features; mark S1 features as null.

**Batch Runner CLI**
```bash
# Normal run (skips both succeeded and failed)
python -m src.chem.batch_runner --limit 20 --npara 4 --maxcore 4000

# Include ionic molecules (override V0 skip)
python -m src.chem.batch_runner --include-ionic

# Optional: skip large molecules using RDKit heavy-atom counts
python -m src.chem.batch_runner --max-heavy-atoms 40

# Retry only failed molecules
python -m src.chem.batch_runner --limit 20 --retry-failed

# Force rerun everything (including succeeded)
python -m src.chem.batch_runner --limit 20 --force-rerun

# Consolidate existing cache to parquet (no new runs)
python -m src.chem.batch_runner --consolidate-only
```

**Resumability**
- By default, skip molecules with `run_status == "success"` OR `run_status == "failed"`
- Use `--retry-failed` to re-run only failed molecules (skips succeeded)
- Use `--force-rerun` to re-run ALL molecules (including succeeded)
- Log skipped molecules to console with reason

**Minimum descriptor set**
- S0: volume, homo_lumo_gap, dihedral_avg, charge_dipole
- S1: volume, homo_lumo_gap, dihedral_avg, charge_dipole, excitation_energy
- Delta: S1-S0 for volume, gap, dihedral, dipole

**Outputs**
- `data/atb_features.parquet` (see `doc/schemas.md`)
- `data/atb_qc.parquet` (run_status, fail_stage, error_msg, runtime, timestamp)
- `cache/atb/` directory with per-molecule workdirs

---

### P3. Feature merge
**Scope**
- Merge private_clean + rdkit_features + atb_features on inchikey
- Standardize numeric features (z-score), save scaler
- Add missing indicators for critical fields

**Outputs**
- `data/X_full.parquet`
- `data/feature_config.yaml`
- `data/scaler.pkl` (or equivalent)

---

### P4. Anchor reference space + index
**V0 default**
- Anchors = subset of rows with:
  - sufficient metadata completeness
  - successful aTB descriptors

**Scope**
- Build FAISS index on selected feature vector
- Provide a top-k query API returning neighbor ids + distances

**Outputs**
- `data/anchor_index.faiss`
- `data/anchor_meta.parquet`

---

### P5. UQ scores + router
**Coverage**
- `C_sim` = mean similarity to top-k anchors (k=5 default, cosine similarity, normalized to [0,1])
- `C_meta` = 1 - missing_rate over critical fields (14 fields, see P1)
- `coverage = 0.7*C_sim + 0.3*C_meta`

**Novelty**
- Method: **kNN distance** (mean distance to k=5 nearest anchors)
- Normalize to [0,1] using percentile scaling on anchor population
- Higher = more outlier-like

**Aleatoric (V0)**
- Prototype-based entropy:
  - Fit GMM with `K = min(20, n_anchors // 10)`, minimum K=5
  - Compute soft assignment `p(cluster|x)` for each molecule
  - `aleatoric = entropy(p) / log(K)`, normalized to [0,1]
- Store K in `data/feature_config.yaml`

**Router Decision Table**

Thresholds (computed on anchor population percentiles):
- `cov_low` = 20th percentile of coverage
- `cov_high` = 80th percentile of coverage
- `ale_high` = 80th percentile of aleatoric
- `nov_high` = 80th percentile of novelty

**Decision logic (deterministic if/elif cascade, evaluated in order):**

```python
def route(coverage, aleatoric, novelty):
    # Priority 1: Evidence-insufficient (low coverage blocks all else)
    if coverage < cov_low:
        return "Evidence-insufficient"

    # Priority 2: Novelty-candidate (CONSERVATIVE GATE)
    # Only allow if: novelty high AND (coverage low-to-mid OR aleatoric high)
    if novelty >= nov_high and (coverage < cov_high or aleatoric >= ale_high):
        return "Novelty-candidate"

    # Priority 3: In-domain ambiguous (high uncertainty but not novel)
    if aleatoric >= ale_high:
        return "In-domain ambiguous"

    # Priority 4: Known/Stable (default)
    return "Known/Stable"
```

**Decision table summary:**

| coverage | aleatoric | novelty | action |
|----------|-----------|---------|--------|
| < low | any | any | Evidence-insufficient |
| ≥ low | any | ≥ high AND (cov < high OR ale ≥ high) | Novelty-candidate |
| ≥ low | ≥ high | < high | In-domain ambiguous |
| ≥ low | < high | < high | Known/Stable |
| ≥ high | < high | ≥ high | Known/Stable (conservative: high coverage blocks novelty claim) |

**recommended_next_steps** (JSON array):
- Evidence-insufficient: `["collect_more_measurements", "retry_atb_different_conformer", "check_smiles_validity"]`
- Novelty-candidate: `["manual_review", "request_high_fidelity_calc", "literature_search"]`
- In-domain ambiguous: `["compare_with_neighbors", "check_mechanism_label_consistency"]`
- Known/Stable: `[]` (empty)

**Outputs**
- `data/uq_scores.parquet` (see `doc/schemas.md` for columns)
- `data/feature_config.yaml` (K, thresholds, method choices)

---

### P6. Reports + hypothesis log
**Scope**
- For each row id, generate `reports/{id}.json` including:
  - key experimental fields (no sensitive comment dump)
  - key aTB deltas (if available)
  - top-k anchor neighbors (ids + distances)
  - coverage/novelty/aleatoric
  - router action + next steps
- For Novelty-candidate, append to `data/hypothesis_log.jsonl|parquet` with provenance

**Outputs**
- `reports/*.json`
- `data/hypothesis_log.*`

---

### P7. Minimal tests
**Scope**
- unit tests:
  - SMILES canonicalization + InChIKey consistency
  - unit conversion correctness (qy/tau)
  - UQ score ranges + router determinism

---

## Open questions (to be resolved during V0)
- absorption/emission field formats (single peak vs list vs string)
- confirm qy/tau units in private dataset
- choose novelty algorithm and K for prototypes
