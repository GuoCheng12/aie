# ARCHIVED: V0 Detailed Plan (archived on 2026-01-27)
# Status: V0 complete; retained as read-only history.
# Scope summary: P1–P6/P7 delivered; P2 cache integrated into atb_qc/atb_features; P3b X_full built.
# Retrieval policy: anchor space remains structure-only (ECFP); aTB used only for evidence/readiness.
# This file is an archive; do not edit unless correcting historical record.

# doc/process.md

## V0 Detailed Plan (CURRENT)

> Rules:
> - Update this file BEFORE coding every meaningful change.
> - This file contains ONLY the CURRENT version's detailed plan (V0 now).
> - When switching to V1, archive this file as `doc/process_v0.md` and create a new `doc/process.md` for V1.

---

## V2 Design Notes (for future implementation; V0 remains pre-aTB)

> These notes capture refined design decisions for V2. V0 uses pre-aTB approximations.

### 1. Data Incompleteness as a First-Class Design Assumption

- **Missing experimental fields is expected** (~60% of records have at least one missing emission/qy/tau value)
- **aTB may produce partial outputs** (SCF/OPT/NEB stage failures; large molecules may miss geometry stats)
- Treat partial evidence as **valid input**, not errors
- Introduce **evidence availability profile**: a structured summary of what evidence exists vs what is missing for each molecule

### 2. Retrieval Policy (Structure-First)

- Anchor/reference retrieval remains **primarily structure-based** (ECFP/Tanimoto; later learned embedding)
- Do NOT let continuous features (aTB geometry stats, experimental observables) dominate neighbor retrieval globally—this causes **semantic drift** (retrieving molecules with similar numbers but different chemistry)
- **Recommended fusion strategy**: two-stage retrieval
  1. Structure candidate generation (ECFP top-k)
  2. Micro-evidence re-ranking within candidates (using mechanism labels, experimental similarity, aTB features)

### 3. Candidate Mechanism Sourcing (Hybrid)

- **Primary source**: neighbor `mechanism_id` distribution (local label recall from structural neighbors)
- **Supplemental source**: pull mechanism "signatures/templates" from **offline domainRAG** (a curated store of mechanism descriptions, discriminative evidence patterns, and missing-evidence checklists)
- **Clarification**: domainRAG is NOT LLM-mined paper text; it is curated structured knowledge
- **Purpose of signatures**:
  - Provide discriminative evidence requirements for each candidate mechanism
  - Guide missing-evidence planning (what to measure/compute next)
  - Act as safety net under label noise, sparse neighborhoods, or OOD queries

> **Terminology Note (V0.7)**:
> - **Neighbor signature** in our context primarily means neighbor offline evidence: neighbor aTB cache (cache_status, features_summary) + experimental observations from dataset. This is attached post-retrieval.
> - **Mechanism signature templates** are curated knowledge from domainRAG that describe what evidence distinguishes each mechanism type. These serve as a planning/verification layer.
> - Two-stage retrieval remains **structure-first** (ECFP Tanimoto); neighbor evidence is attached after structural retrieval, not used for retrieval ranking.

### 4. ICL Anchor Construction (What is Included)

For each query molecule, the ICL context includes:
1. **Query molecule**: SMILES/InChIKey; may lack experimental data
2. **Top-k structural neighbors** + their partial evidence (experimental + aTB if available)
3. **Candidate mechanisms** from neighbors (`mechanism_id` distribution)
4. **Signature/template snippets** for those candidate mechanisms (from domainRAG)
5. **Missing-evidence checklist**: what the query lacks that would help discriminate between candidates

### 5. UQ Strategy Update: Pre-UQ vs Post-UQ

**Pre-UQ (SMILES-first)** = Risk Scores + Evidence Readiness (see §6 below)
- Computable from SMILES alone (no experimental record required)
- Controls workflow: compute/search/measure vs proceed to reasoning
- V0/V1: `novelty`, `mechanism_entropy`; V2 adds readiness gates

**Post-UQ (after LLM outputs hypotheses):**
- Evaluates hypothesis-specific support/coherence vs available evidence
- Detects conflicts between hypothesis and evidence
- Decides gating: finalize / request more evidence / allow write-back hypothesis branch
- V2 fields: `coherence_score`, `support_score`, `conflict_score`, `writeback_allowed`

**LLM confidence is NOT used as a primary UQ signal.** Uncertainty comes from structured evidence analysis.

### 6. Pre-UQ Split: SMILES-First (Risk Scores + Evidence Readiness)

#### (A) Risk Scores (SMILES-only computable)
- `top1_sim`: ECFP Tanimoto similarity to nearest neighbor
- `mean_topk_sim`: mean ECFP Tanimoto over top-k neighbors (k=10 default)
- `neighbor_gap`: top1_sim - top2_sim (differentiation signal)
- `novelty_struct`: 1 - top1_sim (optionally percentile-scaled)
- `mechanism_entropy`: similarity-weighted entropy of neighbors' mechanism labels
  - Weights: `w_j ∝ exp(β * sim_j)`, β=10 default
  - `p(m|x) = Σ w_j I[label_j=m] / Σ w_j`
  - `mechanism_entropy = H(p) / log(M_eff)` in [0,1]
  - Also output: `mechanism_hint` (top label), `hint_confidence` (top label probability)

#### (B) Evidence Readiness (gates workflow)
- `target_atb_status`: absent | pending | success | failed (from cache/status.json)
- `neighbor_atb_success_rate`: fraction of top-k neighbors with aTB status == success
- `neighbor_atb_keyfield_rate`: fraction with key aTB fields (delta_volume, delta_gap, delta_dihedral, excitation_energy)
- Minimal experiment flags: `has_emission`, `has_qy`, `has_tau`, `has_solvent`
- `missing_evidence_list`: required evidence for candidate mechanisms (signature-driven) or minimal set
- **Evidence Ladder (action priority)**:
  1. If `target_atb_status` ∈ {absent, pending} → `compute_target_atb`
  2. If `target_atb_status == failed` → `literature_search`
  3. If literature not found → `request_minimal_experiment` (emission first)
  4. Expand to qy/tau/solvent as needed for disambiguation

#### Policy Notes
- **SMILES-only pre-UQ does NOT use C_meta** (no experimental record available)
- **Record-mode (id-based) UQ** may still use C_meta for experimental completeness
- Readiness gates the workflow; risk scores shape reasoning style and write-back gating

### 7. Case File Workflow (SMILES-first)

The **Case File** is the central artifact for SMILES-first workflow. It replaces the traditional "pass files between agents" pattern with a single shared artifact that agents update in-place.

#### Workflow Overview

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Data Agent    │────>│   Case File     │<────│   Chem Agent    │
│  (creates case) │     │  (single JSON)  │     │ (updates case)  │
└─────────────────┘     └────────┬────────┘     └─────────────────┘
                                 │
                                 v
                        ┌─────────────────┐
                        │ Master Reasoner │
                        │ (reads when     │
                        │  gate = true)   │
                        └─────────────────┘
```

#### Agent Responsibilities

**Data Agent** (`create_case`):
1. Accepts input SMILES
2. Canonicalizes SMILES, computes InChIKey
3. Computes risk_scores via ECFP neighbor search:
   - top1_sim, mean_topk_sim, neighbor_gap, novelty_struct
   - mechanism_entropy, mechanism_hint, hint_confidence
4. Initializes evidence_readiness with placeholders:
   - atb.cache_status = "absent" (or checks cache)
   - atb.request_status = "not_requested"
   - literature.status = "not_started"
   - experiment.status = "not_requested"
   - current_gate.ready_for_reasoning = false
5. Builds initial action_plan (evidence ladder)
6. Writes case file, appends "case_created" to history

**Chem Agent** (`update_case`):
1. Reads current case file
2. Executes next action from action_plan:
   - `compute_target_atb`: runs aTB, updates atb.cache_status and atb.request_status
   - `literature_search`: searches literature, updates literature.status
   - `request_min_experiment_*`: marks experiment as requested
3. Re-evaluates current_gate after each update
4. Appends event to history
5. Writes updated case file back to disk

**Master Reasoner** (future):
- Only runs when `current_gate.ready_for_reasoning == true`
- Reads case file as input context
- Generates hypothesis, writes back to case file or separate hypothesis log

#### Gate Logic (V0.5)

```python
def evaluate_gate(evidence_readiness):
    atb = evidence_readiness['atb']
    min_exp = evidence_readiness['minimal_experiment_available']
    # key_atb_fields_present: check delta_gap/delta_dihedral/delta_volume/excitation_energy
    feats = atb.get('features_summary', {})
    key_atb_fields_present = all(
        feats.get(k) is not None for k in ["delta_gap", "delta_dihedral", "delta_volume", "excitation_energy"]
    )

    if atb.get('cache_status') == 'success' and key_atb_fields_present:
        return True, "atb_success"
    if min_exp['has_emission']:
        return True, "has_emission_data"

    return False, "missing_target_atb_and_min_experiment"
```

#### Key Principles

1. **Single artifact**: Agents update the same case file, not pass copies back and forth
2. **Append-only history**: Every change is logged with actor, timestamp, event_type
3. **Evidence ladder**: Actions are prioritized (aTB → literature → experiment)
4. **Gate before reasoning**: LLM reasoning only starts when evidence is sufficient
5. **Schema-enforced**: All case files must validate against `doc/schemas.md` §11

#### CLI Commands

```bash
# Create case from SMILES
python -m src.cli case --smiles "c1ccccc1" --write

# Update case (Chem Agent stub)
python -m src.cli case-update --case cases/<case_id>.json --action compute_target_atb

# Validate case file
python -m src.cases.validate_case_file --case cases/<case_id>.json
```

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

### P3. Feature merge (CURRENT: P3b with aTB block)
- [x] P3a pre-aTB merge complete (`data/X_full_pre_atb.parquet`)
- [ ] P3b post-aTB merge: build `data/X_full.parquet` with aTB block
- [ ] Create `src/features/merge_with_atb.py` (join X_full_pre_atb + atb_features + atb_qc on inchikey)
- [ ] Update `data/feature_config.yaml` (add aTB block; anchor retrieval stays structure-only)
- [ ] Update `data/scaler.pkl` (continuous columns only; do NOT scale ECFP)

### P4. Anchor reference space + index

P4 is divided into sub-stages to allow UQ development in parallel with aTB computation:
- **P4a**: ECFP-only anchor space (current, pre-aTB)
- **P4b**: Add RDKit descriptors (future)
- **P4c**: Add aTB descriptors + final FAISS index (future, post-P2)

#### P4a. Initial Anchor Space (ECFP-only, pre-aTB) ✅ URGENT BRANCH

**Purpose**: Build anchor reference space using ONLY ECFP fingerprints while P2 (aTB) computation is still running. This enables UQ development to proceed in parallel.

**Scope**
- Use `ecfp_2048` from `data/rdkit_features.parquet` (1050 molecules)
- Similarity metric: **Tanimoto** on binary fingerprints (NOT cosine on raw 2048 array)
- Compute top-k neighbors (k=10) for ALL valid molecules (excluding self)

**Tanimoto Implementation Notes**
- Fingerprints are stored as `np.int8` arrays; coerce to boolean via `(fp > 0).astype(np.uint8)` before computing
- Use `np.logical_and` for intersection, not raw bitwise `&`
- Guard against non-{0,1} values with assertion or coercion

**Implementation**
- [x] `src/features/anchor_ecfp.py`: Build neighbor relationships
- [x] `src/features/validate_anchor_space.py`: Sanity checks and reports
- [x] `tests/test_anchor_ecfp.py`: Unit tests for InChIKey filtering and Tanimoto

**Outputs**
- `data/anchor_neighbors_ecfp.parquet`: neighbor relationships

| Column | Type | Description |
|--------|------|-------------|
| inchikey | string | Query molecule |
| neighbor_inchikey | string | Neighbor molecule |
| rank | int | 1-10 (1 = most similar) |
| tanimoto_sim | float | Tanimoto similarity [0,1] |

**CLI**
```bash
# Build anchor neighbors (ECFP-only)
python -m src.features.anchor_ecfp --k 10

# Validate and print report
python -m src.features.validate_anchor_space

# Run unit tests
pytest tests/test_anchor_ecfp.py -v
```

#### P4a+ Hybrid Anchor (ECFP + partial aTB subset, validation only)

**Purpose**: Sanity-check whether adding aTB features improves reference space quality. Uses ONLY the subset of molecules with successful aTB cache (`S_atb`). Does NOT replace P4a outputs.

**Subset Selection (S_atb)**
- Scan `cache/atb/{prefix}/{inchikey}/status.json` for `run_status == "success"`
- Require valid `features.json` exists and is parseable
- Further filter to `S_atb_hybrid`: molecules where ALL 4 aTB features below are non-null

**aTB Features Used (minimal stable set)**
- `delta_volume` (float)
- `delta_gap` (float)
- `delta_dihedral` (float)
- `excitation_energy` (string → float, safely cast)

**Similarity Fusion**
- `sim_ecfp` = Tanimoto on binary ECFP fingerprints (same as P4a)
- `sim_atb`:
  1. Build matrix X_atb (n × 4) for S_atb_hybrid
  2. Z-score each column (fit on S_atb_hybrid)
  3. L2-normalize each row
  4. cosine(u_i, u_j) → map to [0,1] via `(cosine + 1) / 2`
- `sim = w_ecfp * sim_ecfp + w_atb * sim_atb` (default: 0.7, 0.3)

**Implementation**
- [x] `src/features/anchor_hybrid_ecfp_atb_partial.py`: Hybrid neighbor builder
- [x] `src/features/validate_anchor_space_hybrid_partial_atb.py`: Validation + comparison vs P4a
- [x] `tests/test_anchor_hybrid_partial_atb.py`: Unit tests

**Outputs**
- `data/anchor_neighbors_hybrid_partial_atb.parquet`

| Column | Type | Description |
|--------|------|-------------|
| inchikey | string | Query molecule |
| neighbor_inchikey | string | Neighbor molecule |
| rank | int | 1-k (1 = most similar) |
| sim | float | Fused similarity [0,1] |
| sim_ecfp | float | ECFP Tanimoto [0,1] |
| sim_atb | float | aTB cosine (mapped) [0,1] |

- `data/anchor_hybrid_partial_atb_manifest.json`: Metadata (n_success_cache, n_used, features, weights, k)

**CLI**
```bash
# Build hybrid neighbors
python -m src.features.anchor_hybrid_ecfp_atb_partial --k 10 --w-ecfp 0.7 --w-atb 0.3

# Validate and compare vs ECFP-only
python -m src.features.validate_anchor_space_hybrid_partial_atb

# Run tests
pytest tests/test_anchor_hybrid_partial_atb.py -v
```

#### P4b. Add RDKit Descriptors (future)

**Scope**: Incorporate 9 RDKit descriptors (mw, logp, tpsa, n_rotatable_bonds, n_hbd, n_hba, n_rings, n_aromatic_rings, n_heavy_atoms) as second similarity block.

**Preprocessing for Descriptor Cosine**
1. Compute z-score normalization on anchor population (fit on anchors, transform all)
2. L2-normalize each descriptor vector (required for cosine similarity stability)
3. Then compute cosine similarity

**Fusion Strategy: Two-Stage Retrieval (RECOMMENDED)**

Based on P4a+ validation findings (35.1% ECFP drift with linear fusion), **two-stage retrieval** is the recommended approach when combining ECFP with continuous features:

1. **Stage 1 (Candidate Generation)**: Retrieve top-M candidates by ECFP Tanimoto (default M=50)
2. **Stage 2 (Reranking)**: Within those M candidates, rerank by fused similarity:
   - `sim = w1 * sim_ecfp + w2 * sim_descriptors`
   - Default weights: w1=0.7, w2=0.3 (tunable)

**Why Two-Stage?**
- Preserves structural similarity (ECFP acts as gatekeeper)
- Reduces "ECFP drift" (neighbors structurally dissimilar to query)
- P4a+ validation: two-stage achieved 0% low-ECFP neighbors vs 35.1% for linear fusion

**Health Metrics (MUST report)**

When building any fused anchor space, compute and report:
- `ecfp_median`: Median sim_ecfp among top-k neighbors
- `low_ecfp%`: Fraction of top-k neighbors with sim_ecfp < 0.2
- Thresholds:
  - `low_ecfp% > 30%` = WARNING (significant structural drift)
  - `low_ecfp% > 10%` = CAUTION
  - `low_ecfp% < 10%` = PASS

**Outputs**
- `data/anchor_neighbors_two_stage_rdkit.parquet` (ECFP + RDKit descriptors, two-stage)

#### P4c. Anchor Index (structure-only, post-P2)

**Decision (current)**: Anchor space remains **structure-only** (ECFP / structural embedding). **Do NOT** use aTB features for retrieval or re-ranking. aTB is used only as evidence/readiness in case files, reports, and UQ.

**Scope**: After P2 completes, build FAISS index on structure features only (ECFP or structural embedding).

- [ ] Create `src/features/anchor_selector.py` (filter by completeness + atb_available)
- [ ] Create `src/features/indexer.py` (build FAISS index, top-k query API)
- [ ] Generate `data/anchor_index.faiss`
- [ ] Generate `data/anchor_meta.parquet`

**Full feature vector**: Structure-only (ECFP / structural embedding)
**Final fusion**: Two-stage retrieval if combining multiple structure signals; no aTB block
**Health metrics**: Report `ecfp_median` and `low_ecfp%` for all fused spaces

### P5. UQ scores + router

**Mode note (current policy)**:
- Record-mode (id-based) UQ may use `C_meta` (experimental completeness).
- SMILES-first pre-UQ does NOT use `C_meta`; it uses Risk Scores + Evidence Readiness.
- Current V0 routing uses **P5b** (`mechanism_entropy`) for ambiguity and SMILES-first readiness gating (see V2 design notes).

#### P5a Pre-aTB UQ Computation ✅ COMPLETE
- [x] Create `src/uq/compute_uq_pre_atb.py` (C_sim + C_meta + coverage + novelty + aleatoric + router)
- [x] Create `src/uq/validate_uq_pre_atb.py` (validation and spot-check)
- [x] Create `tests/test_uq_pre_atb.py` (26 unit tests)
- [x] Update `src/cli.py` to include UQ scores in run command output
- [x] Generate `data/uq_scores_pre_atb.parquet` (1225 rows)
- [x] Generate `data/uq_manifest_pre_atb.json` (thresholds, percentiles, counts)
> **Note**: P5a router is legacy/diagnostic-only and kept for comparison; do not treat as current routing policy.

#### P5b Pre-aTB mechanism_entropy Router ✅ COMPLETE
- [x] Create `src/uq/mechanism_label_map.py` (MODE aggregation per inchikey)
- [x] Create `src/uq/compute_mechanism_entropy_pre_atb.py` (softmax-weighted label entropy)
- [x] Create `src/uq/compute_uq_pre_atb_p5b.py` (router using mechanism_entropy)
- [x] Create `src/uq/validate_uq_pre_atb_p5b.py` (validation)
- [x] Create `tests/test_mechanism_entropy_pre_atb.py` (20 unit tests)
- [x] Update `src/cli.py` to show both P5a and P5b scores
- [x] Generate `data/mechanism_label_map.parquet` (1050 molecules)
- [x] Generate `data/mechanism_entropy_pre_atb.parquet` (1049 molecules)
- [x] Generate `data/uq_scores_pre_atb_p5b.parquet` (1225 rows)
- [x] Generate `data/uq_manifest_pre_atb_p5b.json`

#### P5c Post-aTB UQ Computation (DEFERRED)
- [ ] Create `src/uq/coverage.py` (C_sim + C_meta computation with aTB features)
- [ ] Create `src/uq/aleatoric.py` (GMM fit, entropy computation)
- [ ] Generate `data/uq_scores.parquet` (full post-aTB)
- [ ] Update `data/feature_config.yaml` (add K, thresholds)

> **V2 Note**: P5a/P5b are pre-aTB approximations for Pre-UQ (see "V2 Design Notes" above).
> V2 will replace `mechanism_entropy` (neighbor label entropy) with evidence-conditioned `p(m|E_x)` entropy for "true multi-mechanism probability entropy", and add Post-UQ hypothesis evaluation.


### P6. Reports + hypothesis log

#### P6a. Pre-aTB Reports (SMILES-first aligned)

**Scope**: Generate per-record JSON reports using P5b UQ scores. Reports include BOTH Risk Scores (from UQ tables) and Evidence Readiness (from availability signals). In pre-aTB V0, readiness fields may be absent/placeholder.

**Privacy**: Reports must NEVER include sensitive fields (`comment`). Enforce allowlist/blocklist.

**Report Schema** (`reports/{id}.json`):

```json
{
  "report_version": "P6a_pre_atb_p5b",
  "record_summary": {
    "id": 123,
    "inchikey": "XXXXX-YYYYY-Z",
    "canonical_smiles": "...",
    "code": "...",
    "tested_solvent": "...",
    "mechanism_id_hint": "ICT",
    "photophysical": {
      "absorption": 450.0,
      "absorption_peak_nm": 450.0,
      "emission_sol": 520.0,
      "emission_solid": 550.0,
      "emission_crys": null,
      "qy_sol": 0.65,
      "qy_solid": 0.80,
      "qy_crys": null
    }
  },
  "risk_scores": {
    "coverage": 0.55,
    "C_sim": 0.60,
    "C_meta": 0.45,
    "novelty": 0.35,
    "novelty_raw": 0.25,
    "top1_sim": 0.75,
    "mechanism_entropy": 0.42,
    "M_eff": 3,
    "top_label": "ICT",
    "top_label_prob": 0.85,
    "router_action_p5b": "Known/Stable",
    "thresholds": {
      "cov_low": 0.39,
      "cov_high": 0.58,
      "nov_high": 0.67,
      "mech_ent_high": 0.88
    }
  },
  "evidence_readiness": {
    "target_atb_status": "absent|pending|success|failed|partial",
    "target_atb_missing_fields": [],
    "neighbor_atb_success_rate": null,
    "neighbor_atb_keyfield_rate": null,
    "minimal_experiment_available": {
      "has_emission": true,
      "has_qy": true,
      "has_tau": false,
      "has_solvent": false
    },
    "missing_critical_fields": ["tau_sol", "tau_solid", "tested_solvent"],
    "evidence_ladder_action_plan": [
      "compute_target_atb",
      "collect_tau_sol",
      "collect_tau_solid",
      "collect_tested_solvent"
    ]
  },
  "neighbors_ecfp": [
    {"rank": 1, "neighbor_inchikey": "...", "tanimoto_sim": 0.75, "mechanism_label": "ICT"},
    {"rank": 2, "neighbor_inchikey": "...", "tanimoto_sim": 0.68, "mechanism_label": "TICT"}
  ],
  "recommended_next_steps": [
    "compute_target_atb",
    "collect_tau_sol",
    "collect_tau_solid",
    "collect_tested_solvent"
  ]
}
```

**Evidence Readiness Fields** (must be present in every report):

| Field | Type | Description |
|-------|------|-------------|
| target_atb_status | string | "absent" / "pending" / "success" / "failed" / "partial" |
| target_atb_missing_fields | list[str] | aTB fields missing if partial; empty otherwise |
| target_atb_keyfield_complete | bool | True if key aTB fields are present (delta_gap/delta_dihedral/delta_volume/excitation_energy) |
| neighbor_atb_success_rate | float or null | Fraction of top-k neighbors with aTB success (null in V0) |
| neighbor_atb_keyfield_rate | float or null | Fraction with key aTB fields (null in V0) |
| minimal_experiment_available | object | {has_emission, has_qy, has_tau, has_solvent} booleans |
| missing_critical_fields | list[str] | Critical fields that are missing |
| evidence_ladder_action_plan | list[str] | Ordered actions per evidence ladder |

**Note (aTB as evidence only)**:
- P6 readiness uses **cache-derived** aTB status/fields (cache_status/partial/missing_fields + features_summary).
- aTB is not used for neighbor retrieval or re-ranking; it only informs evidence_readiness.

**Evidence Ladder Action Priority**:
1. If `target_atb_status` ∈ {absent, pending} → `"compute_target_atb"`
2. If `target_atb_status` == "failed" → `"literature_search"`
3. If `has_emission` == false → `"request_min_experiment_emission"`
4. Then: collect missing fields (absorption, emission_*, qy_*, tau_*, tested_solvent)

**Note**: In current V0 pre-aTB, `target_atb_status` is usually "absent" (placeholder). neighbor_atb_* rates are null.

**Implementation**:
- [x] Create `src/reports/generate_reports_pre_atb_p5b.py`
- [x] Create `src/reports/export_queues_pre_atb_p5b.py` (router action queues)
- [x] Create `src/reports/validate_reports_pre_atb_p5b.py`
- [x] Generate `reports/{id}.json` for all 1225 records
- [x] Generate `data/queue_*.parquet` files by router action
- [x] Generate `data/p6_dashboard_pre_atb_p5b.json`

**CLI**:
```bash
# Generate all reports
python -m src.reports.generate_reports_pre_atb_p5b

# Export queues
python -m src.reports.export_queues_pre_atb_p5b

# Validate
python -m src.reports.validate_reports_pre_atb_p5b

# Single report via CLI
python -m src.cli report --id 1 --write
```

#### P6b. Post-aTB Reports - DEFERRED
- [ ] Create `src/reports/generator.py` (full post-aTB reports)
- [ ] Create `src/reports/hypothesis_logger.py` (append to hypothesis_log)
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
     - `uq --smiles "<SMILES>" [--k 10]`: **Online UQ test** for arbitrary SMILES (pre-P6)
   - Output structured JSON to stdout
   - Optionally write to `reports/{id}.json`

**Online UQ Command (`uq --smiles`)**

Test command for computing UQ scores on arbitrary SMILES (not necessarily in dataset):
```bash
python -m src.cli uq --smiles "c1ccccc1" --k 10
```

Output JSON structure:
```json
{
  "query": {"canonical_smiles": "...", "inchikey": "..."},
  "neighbors": [{"inchikey": "...", "sim": 0.75, "mechanism_label": "ICT"}, ...],
  "uq": {
    "C_sim": 0.45, "C_meta": 0.0, "coverage": 0.315,
    "novelty": 0.82, "mechanism_entropy": 0.65,
    "router_action_p5b": "Evidence-insufficient",
    "recommended_next_steps_p5b": ["check_smiles_validity", ...]
  },
  "diagnostics": {"used_thresholds": {...}, "used_beta": 10, "notes": [...]}
}
```

Notes:
- C_meta = 0.0 for SMILES-only queries (no experimental evidence)
- Uses P5b router policy with mechanism_entropy
- Computes neighbors on-the-fly against rdkit_features.parquet

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

> **Note**: Verbose historical notes, debug stories, and detailed code examples have been moved to `doc/process_summary.md` under "P2 Notes (historical)".

**Objective**
Batch-run AIE-aTB for unique molecules (by inchikey) with caching, resumability, and failure tracking. Consolidate cached results into `data/atb_features.parquet` and `data/atb_qc.parquet` for downstream use.

**Inputs**
- `data/molecule_table.parquet` — 1050 unique InChIKeys with canonical_smiles (SINGLE SOURCE OF TRUTH for SMILES)
- `third_party/aTB/main.py` — AIE-aTB entry point (called as subprocess)

**Outputs**
- `cache/atb/{prefix}/{inchikey}/` — Per-molecule workdirs with status.json, features.json, result.json
- `data/atb_features.parquet` — Consolidated descriptors **built from cache** (see schemas.md)
- `data/atb_qc.parquet` — Cache-derived QC table (cache_status, fail_stage, error_msg, runtime, timestamp)

**Cache Structure**
```
cache/atb/{inchikey[:2]}/{inchikey}/
├── status.json        # Our run metadata (strict 7-field schema)
├── features.json      # Parsed descriptors (from result.json)
├── canonical_smiles.txt  # SMILES audit copy
├── opt/, excit/, neb/ # aTB workdirs
└── result.json        # AIE-aTB primary output
```

**status.json Schema (strict 7 fields)**
```json
{
  "inchikey": "XXXXX-YYYYY-Z",
  "run_status": "success|failed|pending|skipped",
  "fail_stage": null | "conformer" | "opt" | "excit" | "neb" | "volume" | "feature_parse" | "timeout" | "ionic" | "size",
  "error_msg": null | "truncated error (max 500 chars)",
  "timestamp": "ISO 8601",
  "atb_version": null | "AIE-aTB-{git_hash}",
  "runtime_sec": 123.4
}
```

**Failure Stages (detection order)**
1. `conformer` — RDKit failed to embed 3D structure
2. `opt` — S0 optimization failed
3. `excit` — S1 optimization failed
4. `neb` — NEB calculation failed
5. `volume` — Volume calculation failed
6. `feature_parse` — result.json parse error
7. `timeout` — Exceeded time limit
8. `ionic` — Skipped due to ionic charge (V0)
9. `size` — Skipped due to molecule size

**Failure Policy (V0)**
- On failure: record fail_stage + error_msg; router marks as "Evidence-insufficient"
- No automatic retry; use `--retry-failed` for selective retry
- Partial results allowed: if S0 succeeds but S1 fails, keep S0 features

**Linux Execution Checklist**
When resuming P2 on Linux:
- [ ] Verify `third_party/aTB/main.py` works with test SMILES
- [ ] Confirm AMESP license and environment
- [ ] Set appropriate `--npara` and `--maxcore` for hardware
- [ ] Run initial batch with `--limit 20` to validate
- [ ] Monitor for conformer failures (common with complex structures)
- [ ] Use `--max-heavy-atoms 40` if large molecules cause issues
- [ ] Run with `--retry-failed` after fixing issues
- [ ] Consolidate with `--consolidate-only` when done

**Batch Runner CLI**
```bash
python -m src.chem.batch_runner --limit 20 --npara 4 --maxcore 4000
python -m src.chem.batch_runner --retry-failed
python -m src.chem.batch_runner --consolidate-only
```

**Cache → Parquet (P2 integration)**
```bash
python -m src.chem.build_atb_tables_from_cache
```

**Deferred (V0)**
- Ionic molecules (~72 of 1050, 7%) skipped with `fail_stage="ionic"`
- Charge auto-detection implemented but not validated
- Re-enable with `--include-ionic` after validation

---

### P3. Feature merge (P3b CURRENT)

P3 is divided into two stages:
- **P3a**: Pre-aTB merge (experimental + RDKit descriptors only) — COMPLETE
- **P3b**: Post-aTB merge (add aTB features) — CURRENT

#### P3a. Feature merge (pre-aTB, complete)

**Scope**
- Merge `private_clean.parquet` (1225 rows, record-level) + `rdkit_features.parquet` (1050 rows, molecule-level) on inchikey
- Left join from private_clean to preserve all experimental records
- Preserve `ecfp_2048` as array column (do NOT scale fingerprints)
- Include 9 RDKit descriptors: mw, logp, tpsa, n_rotatable_bonds, n_hbd, n_hba, n_rings, n_aromatic_rings, n_heavy_atoms
- Preserve all `{field}_missing` indicator columns from private_clean
- Standardize selected numeric features (z-score), save scaler

**Feature blocks in P3a output**:
1. **Experimental observables**: emission_*, qy_*, tau_*, absorption_peak_nm, tested_solvent, etc.
2. **RDKit descriptors**: 9 continuous descriptors (will be z-scored)
3. **ECFP fingerprints**: ecfp_2048 array (preserved as-is, NOT scaled)
4. **Missing indicators**: All {field}_missing columns
5. **Metadata**: id, code, inchikey, canonical_smiles, molecular_weight, mechanism_id, features_id

**Standardization**:
- Z-score normalize RDKit descriptors (mw, logp, tpsa, n_rotatable_bonds, n_hbd, n_hba, n_rings, n_aromatic_rings, n_heavy_atoms)
- Fit scaler on all available rows (ignore NaN values during fit)
- Save scaler to `data/scaler_pre_atb.pkl`

**Outputs (already generated in V0)**
- `data/X_full_pre_atb.parquet`: Merged feature table (1225 rows)
- `data/feature_config_pre_atb.yaml`: Documents feature blocks, columns, scaler details
- `data/scaler_pre_atb.pkl`: StandardScaler fitted on RDKit descriptors

**CLI**
```bash
# Run merge
python -m src.features.merge_pre_atb

# Validate merge
python -m src.features.validate_merge_pre_atb

# Run tests
pytest tests/test_merge_pre_atb.py -v
```

#### P3b. Feature merge (post-aTB, CURRENT)

**Scope**:
- Load `X_full_pre_atb.parquet` + `atb_features.parquet` + `atb_qc.parquet`
- Left join on inchikey to add aTB descriptors + QC fields
- Update scaler to include aTB features (continuous only)
- Generate final `data/X_full.parquet`

**Outputs**:
- `data/X_full.parquet`
- `data/feature_config.yaml`
- `data/scaler.pkl`

**CLI**
```bash
# Run post-aTB merge
python -m src.features.merge_with_atb
```

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

P5 is divided into sub-stages to accommodate P2 (aTB) being temporarily delayed:
- **P5a**: Pre-aTB UQ scores using ECFP-only anchor space (CURRENT)
- **P5b**: Full UQ scores with aTB features (FUTURE, after P2 completes)

**Mode note (current policy)**:
- Record-mode (id-based) UQ may use `C_meta` (experimental completeness).
- SMILES-first pre-UQ does NOT use `C_meta`; it uses Risk Scores + Evidence Readiness.
- Current V0 routing uses **P5b** (`mechanism_entropy`) for ambiguity and SMILES-first readiness gating (pre-UQ design note).

---

#### P5a. Pre-aTB UQ Computation (ECFP-only)

**Purpose**: Compute UQ scores and router actions using ONLY ECFP-based neighbors while P2 (aTB) is temporarily skipped/delayed. This enables UQ development to proceed in parallel.

**Inputs**
- `data/private_clean.parquet` (1225 rows, record-level, has inchikey + {field}_missing)
- `data/anchor_neighbors_ecfp.parquet` (1049 molecules, k=10, tanimoto similarities)

**Outputs**
- `data/uq_scores_pre_atb.parquet` (1225 rows, record-level)
- `data/uq_manifest_pre_atb.json` (thresholds, percentiles, counts, timestamp)

**Score Definitions (Pre-aTB)**

1. **C_sim (Coverage - Similarity)**
   - Source: top-k Tanimoto similarities from P4a neighbor table
   - Computation: `C_sim = mean(top_k_similarities)` where k=10
   - If inchikey missing from neighbor table → C_sim = NaN

2. **C_meta (Coverage - Metadata)**
   - Source: 14 `{field}_missing` columns from P1
   - Computation: `missing_rate = sum(missing) / 14`; `C_meta = 1 - missing_rate`
   - Range: [0, 1] where 1 = no missing fields

3. **coverage (Combined)**
   - Computation: `coverage = 0.7 * C_sim + 0.3 * C_meta`
   - If C_sim is NaN → coverage = NaN, route to Evidence-insufficient

4. **novelty (Pre-aTB)**
   - Definition: derived from top-1 similarity (structural novelty)
   - `novelty_raw = 1 - top1_sim` (higher = less similar = more novel)
   - Percentile scaling: `novelty = clip((novelty_raw - p05) / (p95 - p05), 0, 1)`
   - Percentiles p05/p95 computed on valid-population only

5. **aleatoric (Pre-aTB proxy)**
   - Definition: entropy of normalized top-k similarities (neighbor ambiguity)
   - For each query, take top-k similarities s_i (rank 1..k)
   - Convert to probabilities: `p_i = s_i / sum(s_i)`. If sum(s_i)==0 → aleatoric=1.0
   - `aleatoric = entropy(p) / log(k)` normalized to [0,1]
   - Interpretation: low aleatoric = dominant neighbor; high = spread across neighbors

**⚠️ P5a Aleatoric Policy Note:**
- P5a `aleatoric` = neighbor-similarity entropy (diagnostic proxy only); tends to saturate near 1.0.
- Pre-aTB ambiguity routing should **NOT** rely on this proxy for "In-domain ambiguous" classification.
- **Primary routing signals**: coverage + novelty.
- **Recommended for ambiguity**: use `mechanism_entropy` (see P5b below) instead of `aleatoric`.

---

> **⚠️ P5a Router is LEGACY/DIAGNOSTIC ONLY**
>
> The P5a router below uses `aleatoric` (neighbor-similarity entropy), which saturates and is unreliable.
> **V0's primary router should use P5b** (`router_action_p5b`), which uses `mechanism_entropy` for "In-domain ambiguous".
> P5a is kept here for historical comparison and diagnostic purposes only.

---

**Router Logic (P5a — legacy, kept for comparison)**

Thresholds computed on valid-population percentiles only:
- `cov_low` = 20th percentile of coverage
- `cov_high` = 80th percentile of coverage  
- `nov_high` = 80th percentile of novelty
- `ale_high` = 80th percentile of aleatoric

```python
def route_pre_atb(inchikey, C_sim, coverage, novelty, aleatoric, thresholds):
    cov_low, cov_high, nov_high, ale_high = thresholds
    
    # Priority 0: Invalid/missing data
    if pd.isna(C_sim) or pd.isna(coverage) or coverage < cov_low:
        return "Evidence-insufficient"
    
    # Priority 1: Novelty-candidate (CONSERVATIVE GATE)
    if novelty >= nov_high and (coverage < cov_high or aleatoric >= ale_high):
        return "Novelty-candidate"
    
    # Priority 2: In-domain ambiguous
    if aleatoric >= ale_high:
        return "In-domain ambiguous"
    
    # Priority 3: Known/Stable (default)
    return "Known/Stable"
```

**recommended_next_steps (Pre-aTB):**
- Evidence-insufficient: `["check_smiles_validity", "<top 5 missing fields>", "verify_inchikey"]`
- Novelty-candidate: `["manual_review", "request_atb_compute_on_linux", "<missing fields>"]`
- In-domain ambiguous: `["compare_with_neighbors", "<missing fields>"]`
- Known/Stable: `[]` or `["<missing fields if any>"]`

**CLI**
```bash
# Compute UQ scores (pre-aTB)
python -m src.uq.compute_uq_pre_atb

# Validate results
python -m src.uq.validate_uq_pre_atb
```

---

#### P5b (pre-aTB): mechanism_entropy (neighborhood label ambiguity proxy)

**Purpose**: Replace saturated `aleatoric` with a more meaningful ambiguity signal using neighbor mechanism labels.

**Definition**:
- For query x, take top-k neighbors from `anchor_neighbors_ecfp.parquet`
- Let neighbor labels be coarse `mechanism_id` from `private_clean.parquet`
- **EXCLUDE "other" and "unknown" labels** from entropy calculation (these represent unlabeled data, not competing hypotheses)
- Compute p(m|x) by similarity-weighted counting over KNOWN labels only: `p(m) = Σ_{j∈neighbors with label m, m∉{other,unknown}} w_j / Σ w_j`
- Re-normalize weights after exclusion to sum to 1
- `mechanism_entropy = H(p) / log(M_eff)` where M_eff = number of distinct KNOWN labels in neighborhood
- Output: `mechanism_entropy` in [0, 1]; NaN if all neighbors have excluded labels

**Recommended weighting**:
- `w_j ∝ exp(β * sim_j)` with β=10 (softmax)
- Normalize to sum=1 after excluding "other"/"unknown"

**Excluded labels rationale**:
- "other" and "unknown" represent unlabeled or ambiguous data, NOT known mechanism hypotheses
- High entropy should indicate genuine ambiguity among KNOWN mechanisms (ICT, ESIPT, TICT, etc.)
- Including "other" would inflate M_eff and dilute the entropy signal

**Interpretation**:
- `mechanism_entropy` measures **neighborhood label ambiguity**, not guaranteed multi-mechanism truth
- Low entropy = neighbors agree on mechanism; high entropy = neighbors have mixed labels
- This is a boundary/ambiguity detector, NOT a claim that the molecule has multiple mechanisms

**Router policy update (P5b)**:
- Replace `aleatoric >= ale_high` trigger with `mechanism_entropy >= mech_ent_high`
- **mech_ent_high is computed at MOLECULE-level** (unique inchikeys) to avoid duplicate-record bias
- Keep `aleatoric` as diagnostic-only field in output

---

#### P5c. Full UQ Scores (post-aTB) - DEFERRED

**Scope** (future, after P2 completes):
- Use anchor space with aTB features (P4c)
- Compute GMM-based prototype uncertainty

**V2 Note**: V2 will introduce evidence-conditioned mechanism distributions `p(m | E_x)` using mechanism signatures / evidence scoring; its entropy corresponds to "true multi-mechanism probability entropy."

---

#### P5 General (applies to all sub-stages)

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
