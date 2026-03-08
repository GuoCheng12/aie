# doc/schemas.md

## Core Artifact Schemas (V0)

> This file defines column schemas for all parquet artifacts in V0.
> Use this as the single source of truth when implementing data pipelines.

---

## Multi-agent case schema (authoritative for orchestrator loop)

The orchestrator path uses Case File as the single mutable artifact. All agents write via RFC6902 patch and path-scoped permissions.

### Core case namespaces

- `query.*`: input identity (`input_smiles`, `canonical_smiles`, `inchikey`, `code`, `reference`, `aliases[]`)
- `neighbors[]`: structure neighbors (ECFP/top-k)
- `risk_scores.*`: structural priors + optional `risk_scores.readiness_*` (Ready Agent only)
- `evidence_readiness.atb.*`: cache status/features summary
- `evidence_readiness.literature.*`: literature acquisition state + source list
- `evidence_readiness.experiment.*`: wet-lab request state
- `target_fields.*`: current best extracted targets (`emission_aggr_nm`, `emission_solid_or_film_nm`)
- `target_fields_provenance.*`: provenance for each target field
- `evidence_candidates_staging[]`: candidate evidence rows (append-only)
- `current_gate.*`: readiness gate (Ready Agent only)
- `action_rationale`: rationale text (Ready Agent only)
- `action_plan[]`: prioritized actions (Ready Agent only)
- `post_uq.*`: judge output namespace
- `agent_runs[]`: per-step auditable run records (append-only)
- `runtime.run_lane`: release lane selector (`atb_cache_only|offline_pdf|full`)

### `agent_runs[]` (required)

Each step must append one run row:

```json
{
  "agent_name": "chem_agent",
  "version": "1.0.0",
  "status": "success",
  "status_reason_code": null,
  "started_at": "2026-02-24T00:00:00Z",
  "ended_at": "2026-02-24T00:00:02Z",
  "inputs_hash": "sha256...",
  "idempotency_key": "sha256...",
  "artifacts": [{"kind": "step_artifacts", "path": "artifacts/<run_id>/02_chem_agent"}],
  "warnings": []
}
```

`status_reason_code` is required when `status="skipped"`.
Allowed values:
- `idempotency_hit`
- `gate_blocked_reasoning`
- `lane_disabled`
- `missing_required_input`
- `upstream_failed`
- `not_applicable`

### `evidence_candidates_staging[]` (Chem Agent)

```json
{
  "candidate_id": "0:1",
  "field": "emission_aggr_nm",
  "normalized_value_nm": 540.0,
  "raw_value": "540",
  "unit": "nm",
  "condition": "aggregate in water fraction 90%",
  "condition_bucket": "aggr",
  "value_source_kind": "table",
  "source_type": "offline_pdf",
  "source_ref": "/abs/path/paper.pdf",
  "source_locator": "Table 1 (page 4)",
  "page": 4,
  "bbox": null,
  "identity_match": "matched",
  "identity_match_confidence": 0.9,
  "confidence": 0.88,
  "verification_status": "verified",
  "rejection_reason": null,
  "run_id": "<run_id>"
}
```

### Gate ownership and write rules

- Only **Ready Agent** may write:
  - `current_gate.*`
  - `action_rationale`
  - `action_plan`
  - optional `risk_scores.readiness_*`
- Data/Chem/Reasoning/Judge must not write `current_gate`, `action_rationale`, or `action_plan`.

### Replay artifact contract (per run step)

For each `{run_id}/{step}` directory:

- `00_input_snapshot.json`
- `01_raw_outputs.json` (aggregated raw outputs)
- optional split raw files (`01_raw_*.json`) with `01_raw_index.json`
- `03_patch.json`
- `04_case_before.json`
- `05_case_after.json`
- `06_case_diff.json`
- `manifest.json` (sha256 for all files)

### Case-centric output layout (release runtime)

When `case-run` uses `--output-layout case_centric` (default), outputs are organized under:

- `<artifacts_dir>/cases/<case_id>/latest/`
- `<artifacts_dir>/cases/<case_id>/runs/<timestamp>__<run_id8>/`
- `<artifacts_dir>/cases/<case_id>/history_index.json`
- `<artifacts_dir>/cases/<case_id>/latest.json`

`run_id` remains required in all trace JSON for audit joins.

#### `run_summary.json` additions

Runtime summary now includes:

- `primary_output_dir`
- `latest_dir`
- `history_index_path`
- `legacy_paths` (when compatibility pointers are enabled)

#### `quick_view.json` contract

`latest/quick_view.json` is a stable, human-readable summary:

```json
{
  "case_id": "IK...",
  "run_id": "abc123...",
  "run_time": "2026-03-04T15:42:10Z",
  "final_label": "unknown",
  "final_confidence": 0.51,
  "final_gate": {"state": "ready_conservative", "reasoning_mode": "conservative"},
  "rounds_executed": 3,
  "stop_recommendation": {"should_stop": true, "reason_code": "stagnation_no_new_evidence"},
  "used_evidence_ids_top": ["E31", "E32", "E24"],
  "paths": {
    "case_json": ".../latest/case.json",
    "run_summary_json": ".../latest/run_summary.json",
    "rounds_dir": ".../latest/rounds",
    "llm_dir": ".../latest/llm"
  }
}
```

#### `history_index.json` contract

Per-case run index:

```json
{
  "case_id": "IK...",
  "retain_runs": 10,
  "updated_at": "2026-03-04T15:42:30Z",
  "runs": [
    {
      "run_id": "abc123...",
      "run_name": "20260304T154210Z__abc12345",
      "run_time": "2026-03-04T15:42:10Z",
      "status": "ok",
      "final_label": "unknown",
      "final_confidence": 0.51,
      "run_dir": ".../runs/20260304T154210Z__abc12345"
    }
  ]
}
```

### Master reasoning (v3 final lock)

`master_output_schema_version` remains `v3`. Runtime default is `tagged_repair`; strict provider JSON schema is optional.

#### Required tagged fields in model output (tagged_repair mode)

- `TEMPLATE_USED`
- `STATUS`
- `PRIMARY_LABEL`
- `PRIMARY_CONFIDENCE`
- `PRIMARY`
- `COMPETING`
- `EVIDENCE`
- `PREDICTIONS`
- `LIMITS`
- `NEXT_ACTIONS`

Missing required tagged fields result in `failed_schema_validation`.

#### `mechanism_label` normalization

- Runtime config key: `reasoning_config.allowed_mechanism_labels`.
- Default: `["TICT","ESIPT","ICT","other","unknown"]`.
- Accepted pool for `PRIMARY_LABEL`:
  - `allowed_mechanism_labels`
  - plus `reasoning_pack.mechanism_context.candidate_mechanisms_top3` labels
  - plus `unknown` / `other`
- If parsed label is outside pool: normalize to `unknown` and emit warning.

#### Confidence fields (raw vs final)

- `PRIMARY_CONFIDENCE` is treated as model raw value only.
- Final written value `mechanism_claim.confidence` is computed by soft-penalty policy.
- `master_reasoning_meta` must include:
  - `raw_confidence_from_model`
  - `final_confidence`
  - `penalty_components`
  - `confidence_formula_version`

#### R1 self-trend stats block (pack-only)

When active profile is `R1+`, reasoning pack contains:

- `risk_scores.atb_trends_self`

Compact structure:

```json
{
  "enabled": true,
  "fields_used": ["delta_dihedral", "delta_gap", "delta_volume", "excitation_energy"],
  "delta_dihedral_abs_deg": 0.0,
  "delta_dihedral_bucket": "none|weak|strong",
  "delta_dihedral_direction": "increase|decrease|mixed|unknown",
  "delta_gap_direction": "decrease|flat|increase|unknown",
  "delta_gap_bucket": "weak|moderate|strong|unknown",
  "delta_volume_direction": "decrease|flat|increase|unknown",
  "delta_volume_bucket": "weak|moderate|strong|unknown",
  "overall_motion_proxy": "low|medium|high|unknown",
  "reliability": "low|medium|high",
  "notes": []
}
```

Rules:
- self-trend is computed from target aTB only and is independent from neighbor distributions.
- all thresholds/buckets must come from configured policy thresholds (no ad-hoc bands).
- serialized object must stay compact (target `<1KB`).

#### R2 discriminative stats block

When active profile is `R2+`, reasoning pack contains:

- `risk_scores.neighbor_atb_stats_by_label`

Compact structure:

```json
{
  "sample_size": 0,
  "reliability": "low|medium|high",
  "fields": {
    "delta_dihedral": {
      "target": 0.0,
      "neighbors_median": 0.0,
      "neighbors_iqr": 0.0,
      "target_percentile": 0.0,
      "z_robust": 0.0
    },
    "delta_gap": {},
    "delta_volume": {}
  },
  "by_label": {
    "ICT": {"n": 0, "median": 0.0, "iqr": 0.0, "percentile_of_target": 0.0}
  },
  "closest_label_by_field": {"delta_dihedral": "ICT|unknown"},
  "separation_score": 0.0,
  "summary": []
}
```

Rules:
- Include only neighbors with `cache_status=="success"` and complete delta fields.
- No full neighbor feature dump in pack.
- Deterministic trim order must guarantee serialized size `<3072` bytes.

#### Evidence IDs for R2 discriminative block

- `E21`: delta_dihedral comparative summary
- `E22`: delta_gap comparative summary
- `E23`: by-label comparative summary (conditional)
- `E24`: reliability + sample size (+ separation note)

#### Evidence IDs for R1 self-trend block

- `E_ATB_TREND_1`: self delta_dihedral bucket + absolute magnitude
- `E_ATB_TREND_2`: self delta_gap direction + bucket
- `E_ATB_TREND_3`: self delta_volume direction + bucket
- `E_ATB_TREND_4`: self overall motion proxy + reliability

`value_preview` for E21/E22 must stay minimal:
`{target, neighbors_median, neighbors_iqr, target_percentile, z_robust}`.


## 1. `data/private_clean.parquet`

Train-only facts table (authoritative source: `data/train.csv`) plus a small set of derived columns.

> **Rules**
> - Business columns are constrained to the train CSV schema.
> - Allowed derived columns: `canonical_smiles`, `inchikey`, and explicit missing indicators.
> - `data/test.csv` is NOT merged into this table.
> - Invalid SMILES produce null `canonical_smiles`/`inchikey` and are routed conservatively downstream.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| id | int64 | No | Record ID from train.csv |
| code | string | Yes | Molecule code |
| SMILES | string | No | Original SMILES from train.csv |
| reference | string | Yes | Source/reference field from train.csv |
| molecular_weight | float64 | Yes | Molecular weight |
| emission_solid | float64 | Yes | Emission in solid state (nm) |
| emission_aggr | float64 | Yes | Emission in aggregate state (nm) |
| features_id | int64 | Yes | Feature ID |
| mechanism_id | string | Yes | Mechanism label (string) |
| doi | string | Yes | DOI (train-only field) |
| canonical_smiles | string | Yes | RDKit-canonicalized SMILES |
| inchikey | string | Yes | InChIKey from canonical SMILES |
| emission_solid_missing | bool | No | True if emission_solid is missing/invalid |
| emission_aggr_missing | bool | No | True if emission_aggr is missing/invalid |

---

## 2. `data/molecule_table.parquet`

Unique molecules by InChIKey with ID mapping.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| inchikey | string | No | **Primary key**, unique |
| canonical_smiles | string | No | RDKit-canonicalized SMILES |
| source_smiles | string | Yes | Representative original SMILES from private_clean (`SMILES`) |
| id_list | list[int64] | No | List of original IDs mapping to this molecule |
| n_records | int64 | No | Count of records for this molecule |

---

## 3. `data/rdkit_features.parquet`

RDKit-computed molecular descriptors.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| inchikey | string | No | **Primary key** |
| mw | float64 | No | Molecular weight |
| logp | float64 | Yes | Crippen LogP |
| tpsa | float64 | Yes | Topological polar surface area |
| n_rotatable_bonds | int64 | Yes | Rotatable bond count |
| n_hbd | int64 | Yes | H-bond donors |
| n_hba | int64 | Yes | H-bond acceptors |
| n_rings | int64 | Yes | Ring count |
| n_aromatic_rings | int64 | Yes | Aromatic ring count |
| n_heavy_atoms | int64 | Yes | Heavy atom count |
| ecfp_2048 | list[int8] | Yes | ECFP4 fingerprint (2048 bits, packed) |

---

## 4. `data/atb_features.parquet`

aTB-computed micro-physical descriptors (cache-derived).

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| inchikey | string | No | **Primary key** |
| delta_volume | float64 | Yes | S1 - S0 volume |
| delta_gap | float64 | Yes | S1 - S0 HOMO-LUMO gap |
| delta_dihedral | float64 | Yes | S1 - S0 dihedral |
| excitation_energy | float64 | Yes | Vertical excitation energy (eV); pure float cast (no scaling) |
| s0_volume | float64 | Yes | S0 molecular volume (Å³) |
| s1_volume | float64 | Yes | S1 molecular volume (Å³) |
| s0_homo_lumo_gap | float64 | Yes | S0 HOMO-LUMO gap (eV) |
| s1_homo_lumo_gap | float64 | Yes | S1 HOMO-LUMO gap (eV) |
| s0_dihedral_avg | float64 | Yes | Average dihedral angle (S0) |
| s1_dihedral_avg | float64 | Yes | Average dihedral angle (S1) |
| s0_charge_dipole | float64 | Yes | Dipole moment (S0) |
| s1_charge_dipole | float64 | Yes | Dipole moment (S1) |
| delta_dipole | float64 | Yes | S1 - S0 dipole |

---

## 5. `data/atb_qc.parquet`

aTB run quality control / audit log (cache-derived).

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| inchikey | string | No | **Primary key** |
| cache_status | string | No | "success" / "partial" / "failed" / "pending" / "absent" |
| fail_stage | string | Yes | Stage where failure occurred |
| error_msg | string | Yes | Truncated error message (max 500 chars) |
| runtime_sec | float64 | Yes | Total runtime in seconds |
| atb_version | string | Yes | aTB pipeline version used |
| timestamp | string | Yes | ISO 8601 timestamp of run |
| has_features_json | bool | No | Whether features.json exists and is readable |
| keyfield_complete | bool | No | True if all key fields are present |
| missing_fields | list[str] | Yes | Missing key fields (if partial) |

---

## 6. `data/X_full.parquet`

Merged feature matrix (experimental + RDKit + aTB).

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| inchikey | string | No | **Primary key** |
| id | int64 | No | Representative original ID |
| *...all numeric columns from private_clean (emission, qy, tau)...* | float64 | Yes | Experimental observables |
| *...all columns from rdkit_features (except inchikey)...* | varies | Yes | RDKit descriptors |
| *...all columns from atb_features (except inchikey, run_status, fail_stage)...* | float64 | Yes | aTB descriptors |
| *..._missing columns...* | bool | No | Missing indicators |
| atb_cache_status | string | Yes | Cache-derived status ("success"/"partial"/"failed"/"pending"/"absent") |
| atb_keyfield_complete | bool | Yes | True if all key aTB fields are present |
| atb_available | bool | No | True if aTB cache_status == success AND keyfield_complete |

**Note**: Numeric features are z-score normalized. Scaler saved to `data/scaler.pkl`.

---

## 7. `data/uq_scores.parquet`

Uncertainty quantification scores and router decisions.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| inchikey | string | No | **Primary key** |
| id | int64 | No | Representative original ID |
| coverage | float64 | No | Combined coverage score [0,1] |
| coverage_sim | float64 | No | Feature-space similarity to anchors [0,1] |
| coverage_meta | float64 | No | Metadata completeness [0,1] |
| novelty | float64 | No | Novelty/outlierness score [0,1] |
| aleatoric | float64 | No | Aleatoric uncertainty (prototype entropy) [0,1] |
| router_action | string | No | "Known/Stable" / "In-domain ambiguous" / "Evidence-insufficient" / "Novelty-candidate" |
| recommended_next_steps | string | Yes | JSON array of recommended actions |
| top_k_neighbors | string | No | JSON array of {inchikey, distance} for top-k anchors |

---

## 8. `data/hypothesis_log.parquet` (or `.jsonl`)

Log of novelty candidates with provenance.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| inchikey | string | No | Molecule InChIKey |
| id | int64 | No | Original ID |
| timestamp | string | No | ISO 8601 when logged |
| coverage | float64 | No | Coverage score |
| novelty | float64 | No | Novelty score |
| aleatoric | float64 | No | Aleatoric score |
| top_k_neighbors | string | No | JSON array of neighbor inchikeys + distances |
| key_descriptors | string | No | JSON object of notable descriptor values |
| recommended_next_steps | string | No | JSON array of suggested actions |
| hypothesis_status | string | No | "open" (initial) / "under_review" / "validated" / "refuted" |

---

## 9. `data/run_manifest.json`

Pipeline run metadata for reproducibility.

```json
{
  "run_id": "uuid",
  "timestamp": "ISO 8601",
  "git_commit": "hash or 'untracked'",
  "python_version": "3.x.x",
  "rdkit_version": "x.x.x",
  "atb_version": "x.x.x",
  "encoding_used": "utf-8",
  "n_molecules_input": 1226,
  "n_molecules_processed": ...,
  "config_snapshot": { ... }
}
```

---

## 10. `reports/{id}.json` (P6a Pre-aTB Report Schema)

Per-record JSON reports generated by P6a. See `doc/process.md` P6a section for full documentation.

**Top-level structure**:

| Field | Type | Description |
|-------|------|-------------|
| report_version | string | "P6a_pre_atb_p5b" |
| record_summary | object | Core record identifiers and photophysical data |
| risk_scores | object | UQ scores and router decision |
| evidence_readiness | object | Evidence availability and action plan |
| neighbors_ecfp | list[object] | Top-10 ECFP neighbors with mechanism labels |
| recommended_next_steps | list[string] | Ordered action plan |

**record_summary** fields:

| Field | Type | Description |
|-------|------|-------------|
| id | int64 | Record ID |
| inchikey | string | InChIKey (null if invalid SMILES) |
| canonical_smiles | string | Canonicalized SMILES |
| code | string | Molecule code |
| tested_solvent | string | Solvent used (if available) |
| mechanism_id_hint | string | Mechanism label from dataset |
| photophysical | object | {absorption, absorption_peak_nm, emission_*, qy_*} |

**risk_scores** fields:

| Field | Type | Description |
|-------|------|-------------|
| coverage | float64 | Combined coverage score [0,1] |
| C_sim | float64 | Similarity-based coverage [0,1] |
| C_meta | float64 | Metadata completeness [0,1] |
| novelty | float64 | Novelty score (percentile-scaled) [0,1] |
| novelty_raw | float64 | Raw novelty (1 - top1_sim) |
| top1_sim | float64 | Top-1 neighbor similarity |
| mechanism_entropy | float64 | Neighbor label entropy [0,1] or null |
| M_eff | int64 | Effective number of mechanism labels |
| top_label | string | Most probable mechanism label |
| top_label_prob | float64 | Probability of top label |
| router_action_p5b | string | Router decision |
| thresholds | object | {cov_low, cov_high, nov_high, mech_ent_high} |

---

## 11. Case File schema extension for V1 Orchestrator (incremental)

These fields are additive and keep the existing Case File as the single source of truth.

### 11.1 `evidence_writeback_policy` (top-level, optional)

| Field | Type | Nullable | Description |
|------|------|----------|-------------|
| evidence_writeback_policy | enum(`strict`,`relaxed`,`off`) | Yes | Gate for evidence-table writeback behavior. Default recommendation: `relaxed` in current literature state. |

**Semantics**
- `strict`: only provenance-complete evidence can be written to `evidence_table`.
- `relaxed`: candidate-only literature updates allowed in case file; no `evidence_table` writeback for literature claims.
- `off`: disable evidence writeback entirely.

### 11.2 `current_gate.reasoning_mode` semantics

| Value | Meaning |
|------|---------|
| `normal` | Evidence is sufficient/consistent; reasoner can run normally. |
| `conservative` | Reasoner can run, but must hedge and request escalation evidence. |
| `blocked` | Do not run reasoner; collect blocking evidence first. |

### 11.3 `action_plan` structured action object

Current action plan is an ordered list of action objects.

```json
{
  "action": "run_master_reasoner",
  "priority": 1,
  "status": "not_started",
  "inputs": {"inchikey": "...", "requested_fields": ["emission_solid", "emission_aggr"]},
  "expected_outputs": ["master_output.json"],
  "blocking": false,
  "notes": "Short execution guidance"
}
```

| Field | Type | Required | Description |
|------|------|----------|-------------|
| action | string | Yes | Action enum label (e.g., `run_master_reasoner`, `compute_target_atb`, `literature_search_web`). |
| priority | int | Yes | Execution order, strictly increasing from 1. |
| status | enum(`not_started`,`pending`,`done`,`skipped`) | Yes | Action state. |
| inputs | object | Yes | Runtime inputs for the target agent/action. |
| expected_outputs | list[string] | Yes | Expected artifacts or case fields produced. |
| blocking | bool | Yes | Whether this action gates readiness. |
| notes | string | Yes | Operator/LLM guidance. |

### 11.4 `agent_runs[]` (top-level, required for orchestrated execution)

`agent_runs` records auditable per-agent execution traces.

```json
{
  "agent_name": "graph_retriever",
  "version": "v1",
  "started_at": "2026-02-16T10:00:00Z",
  "ended_at": "2026-02-16T10:00:03Z",
  "inputs_hash": "sha256:...",
  "artifacts": [{"path": "artifacts/...json", "kind": "context"}],
  "warnings": [],
  "status": "success",
  "cost": {"usd": 0.0},
  "usage": {"input_tokens": 0, "output_tokens": 0}
}
```

| Field | Type | Required | Description |
|------|------|----------|-------------|
| agent_name | string | Yes | Registered agent id (`case_builder`, `graph_retriever`, etc.). |
| version | string | Yes | Agent implementation/version tag. |
| started_at | string (ISO8601) | Yes | Start timestamp. |
| ended_at | string (ISO8601) | Yes | End timestamp. |
| inputs_hash | string | Yes | Stable hash over normalized inputs for replay/audit. |
| artifacts | list[object] | Yes | Produced artifact references (`path`, `kind`, optional `checksum`). |
| warnings | list[string] | Yes | Non-fatal warnings/degradation notes. |
| status | enum(`success`,`partial`,`failed`,`skipped`,`stubbed`) | Yes | Run status (`stubbed` is used by E0 master/post-UQ placeholders). |
| cost | object | No | Optional cost accounting. |
| usage | object | No | Optional token/compute usage accounting. |

### 11.5 Literature writeback gate (case-level behavior)

- When literature source passthrough is unstable, write only:
  - `evidence_readiness.literature.candidates[]`
  - `evidence_readiness.literature.mode="relaxed"`
  - `evidence_readiness.literature.verification_status="candidates_only"`
- In this state, no `literature_claim` rows may be appended to `data/evidence_table.parquet`.

**evidence_readiness** fields:

| Field | Type | Description |
|-------|------|-------------|
| target_atb_status | string | "absent" / "pending" / "success" / "failed" / "partial" |
| target_atb_missing_fields | list[str] | aTB fields missing if partial |
| target_atb_keyfield_complete | bool | True if key aTB fields are present |
| neighbor_atb_success_rate | float64 or null | Fraction of neighbors with aTB success (null in V0) |
| neighbor_atb_keyfield_rate | float64 or null | Fraction with key aTB fields (null in V0) |
| minimal_experiment_available | object | {has_emission, has_qy, has_tau, has_solvent} |
| missing_critical_fields | list[str] | Critical experimental fields missing |
| evidence_ladder_action_plan | list[str] | Ordered actions per evidence ladder |

**Mapping note (report vs case-file readiness)**:
- Record-based reports flatten readiness (e.g., `target_atb_status`).
- SMILES-first case file uses a nested state machine:
  - `evidence_readiness.atb.cache_status` / `evidence_readiness.atb.request_status`
  - `evidence_readiness.literature.status`
  - `evidence_readiness.experiment.status`
  - `evidence_readiness.minimal_experiment_available.*`

Minimal mapping (examples):
- `target_atb_status` ↔ `evidence_readiness.atb.cache_status`
- `neighbor_atb_success_rate` ↔ `evidence_readiness.neighbor_atb_success_rate`
- `has_emission` ↔ `evidence_readiness.minimal_experiment_available.has_emission`

**Evidence Ladder Action Priority** (reflected in evidence_ladder_action_plan):
1. `compute_target_atb` - if target_atb_status ∈ {absent, pending}
2. `literature_search` - if target_atb_status == "failed"
3. `request_min_experiment_emission` - if has_emission == false
4. `collect_{field}` - for each missing critical field

**neighbors_ecfp** item fields:

| Field | Type | Description |
|-------|------|-------------|
| rank | int64 | Neighbor rank (1 = most similar) |
| neighbor_inchikey | string | Neighbor InChIKey |
| tanimoto_sim | float64 | Tanimoto similarity [0,1] |
| mechanism_label | string | Neighbor's mechanism label |

**Privacy**: The `comment` field from private_clean is NEVER included in reports.

---

## 10A. Candidate Papers Output (P4-pre only; no writeback)

Used by `src/agents/web_search_candidate_papers.py` in the relaxed candidate stage.
This output is a candidate artifact only and must not directly write to `data/evidence_table.parquet`.

```json
{
  "papers": [
    {
      "title": "string",
      "doi": "string|null",
      "url": "string|null",
      "pdf_url": "string|null",
      "source_url": "string",
      "source_title": "string|null",
      "why_this_matches": "string"
    }
  ],
  "stats": {
    "sources_in": 0,
    "papers_out": 0,
    "deduped": 0
  }
}
```

**Trust-boundary constraints (P4-pre two-stage)**
- Stage A (`web_search_preview`) collects `sources`; Stage B (no tools) structures candidates only from Stage A sources.
- `paper.source_url` must map to Stage A sources (exact URL match or normalized same-origin match).
- `doi` may be filled only when visible in Stage A source fields (`title`/`snippet`/`url`); otherwise `doi=null`.
- If `sources_in==0` or `papers_out==0`, keep candidate output empty and escalate to non-literature evidence actions.

---

## V1 Planned Artifacts (minimal schemas)

### `data/evidence_table.parquet` (V1 EvidenceClaim)
| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| evidence_id | string | No | UUID/string |
| subject_inchikey | string | Yes | Molecule InChIKey (nullable if unknown) |
| evidence_type | string | No | enum: private_observation \| atb_computation \| literature_claim |
| field | string | No | e.g., emission_solid, emission_aggr, delta_gap, excitation_energy |
| value_num | float64 | Yes | Parsed numeric value when possible (for filtering/aggregation); null if not parseable/applicable |
| value | string | Yes | Raw extracted string value (audit/debug); always keep original text when available |
| unit | string | Yes | Unit or null |
| condition_state | string | No | enum: sol \| solid \| aggr \| crys \| unknown |
| condition_solvent | string | Yes | Solvent name or null/unknown |
| source_type | string | No | enum: private_db \| atb_cache \| paper_doi |
| source_id | string | No | record_id / inchikey / DOI |
| timestamp | string | No | ISO 8601 |
| timestamp_source | string | Yes | enum (atb only): atb_qc \| build_fallback |
| confidence | float64 | No | [0,1] |
| extraction_method | string | No | e.g., manual \| mineru \| atb_parser |
| quality_flag | string | Yes | Data-quality annotation (default OK); e.g., OUT_OF_RANGE_NEGATIVE / OUTLIER_TAU_EXTREME |
| quality_score | float64 | Yes | [0,1] downweight factor; OK=1.0, warning ~0.7, severe ~0.3 |

Notes:
- If `value_num` is non-null, `value` should still preserve the original extracted text (before normalization) when available.
- If `value_num` is null, consumers should fall back to `value` for categorical/text claims.
- `unit` should be the canonical unit corresponding to `field` when `value_num` is used.
- confidence is 1.0 for internal sources in V1-P1 (private_db/atb_cache); literature_claim will use extraction confidence (<1) in V1-P4.
- Evidence table preserves raw values; data-quality issues are annotated (quality_flag/quality_score) rather than corrected. Downstream components may downweight low-quality evidence.
- Train-only facts policy: `private_observation` rows are restricted to `field in {"emission_solid","emission_aggr"}`.
- For train-only private observations, `condition_state` should be `solid` (emission_solid) or `aggr` (emission_aggr); no `sol` private-observation rows are expected.

### `data/graph_nodes.parquet` (V1 Light Graph)
| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| node_id | string | No | Unique node ID |
| node_type | string | No | Molecule \| Evidence \| Condition |
| key | string | No | inchikey / evidence_id / condition_id |
| props_json | string | Yes | JSON metadata |

### `data/graph_edges.parquet` (V1 Light Graph)
| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| src_id | string | No | Source node_id |
| rel_type | string | No | Relation type (see allowed list) |
| dst_id | string | No | Destination node_id |
| weight | float64 | Yes | Optional (e.g., tanimoto for SIMILAR_TO) |
| evidence_id | string | Yes | Nullable; points to evidence_table row when applicable |
| props_json | string | Yes | JSON metadata |

**Allowed edge types (V1)**:
- Molecule → Evidence: `HAS_OBSERVATION`, `HAS_COMPUTATION`, `HAS_EVIDENCECLAIM`
- Evidence → Condition: `UNDER_CONDITION`
- Molecule ↔ Molecule: `SIMILAR_TO` (weight = tanimoto)

**Note**: V1 has no Hypothesis/Mechanism writeback nodes yet (EvidenceClaim only).

---

## V2 Planned Fields (not required for V0 artifacts)

> These fields are planned for V2 implementation. V0/V1 artifacts do not include them.

### SMILES-First Pre-UQ Fields (V2)

> **Note**: C_meta is record-mode only; SMILES-only pre-UQ uses readiness fields instead.

#### Risk Scores (SMILES-computable)

| Field | Type | Description |
|-------|------|-------------|
| top1_sim | float64 | ECFP Tanimoto to nearest neighbor [0,1] |
| mean_topk_sim | float64 | Mean ECFP Tanimoto over top-k neighbors [0,1] |
| neighbor_gap | float64 | top1_sim - top2_sim (differentiation signal) |
| novelty_struct | float64 | 1 - top1_sim, optionally percentile-scaled [0,1] |
| mechanism_entropy | float64 | Neighbor label entropy proxy [0,1] |
| mechanism_hint | string | Top label from neighbor distribution |
| hint_confidence | float64 | Probability of top label [0,1] |

#### Readiness Fields (gate workflow)

| Field | Type | Description |
|-------|------|-------------|
| target_atb_status | string | absent / pending / success / failed |
| neighbor_atb_success_rate | float64 | Fraction of top-k with aTB success |
| neighbor_atb_keyfield_rate | float64 | Fraction with key aTB fields present |
| has_emission | bool | Emission data available |
| has_qy | bool | Quantum yield data available |
| has_tau | bool | Lifetime data available |
| has_solvent | bool | Solvent info available |
| missing_evidence_list | list[str] | Required evidence for candidate mechanisms |
| action_plan | list[str] | Evidence ladder actions (compute_atb, literature_search, etc.) |

### Legacy Record-Mode Fields

| Field | Type | Description |
|-------|------|-------------|
| coverage | float64 | Combined C_sim + C_meta score [0,1] (record-mode only) |
| evidence_availability_profile | dict | Structured summary: experimental vs aTB vs computed fields available |

### Post-UQ Fields per Hypothesis

| Field | Type | Description |
|-------|------|-------------|
| hypothesis_id | string | Unique identifier for this hypothesis |
| mechanism_candidate | string | Suggested mechanism |
| coherence_score | float64 | Internal consistency of hypothesis |
| support_score | float64 | How well evidence supports this hypothesis |
| conflict_score | float64 | Evidence contradicting this hypothesis |
| writeback_allowed | bool | Whether hypothesis can be written to KG |
| actions | list[str] | Recommended next steps for this hypothesis |

> **Note**: V2 will replace `mechanism_entropy` with evidence-conditioned `p(m|E_x)` entropy.

---

## 11. Case File (SMILES-first) JSON Schema (V0.7)

The Case File is the central artifact for SMILES-first workflow. It is created by the Data Agent and updated in-place by the Chem Agent. Agents do NOT pass files back and forth—they update the same artifact.

> **Schema v0.7** adds:
> - `neighbor_atb` evidence pack for each neighbor (cache status + features_summary)
> - `neighbor_atb_success_rate` and `neighbor_atb_keyfield_rate` metrics
> - `candidate_mechanisms` (top-3 with probabilities)
> - `mechanism_signatures` (domainRAG signature templates)
> - `features_summary` for target molecule's aTB features

**File location**: `cases/{case_id}.json`

### Top-level Structure

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| case_id | string | Yes | Unique identifier (inchikey if valid, else uuid) |
| case_version | string | Yes | Schema version ("0.7") |
| query | object | Yes | Input SMILES and derived identifiers |
| risk_scores | object | Yes | SMILES-computable UQ scores |
| evidence_readiness | object | Yes | State machine for evidence collection |
| neighbors | list[object] | Yes | Top-k structural neighbors with aTB evidence |
| action_plan | list[object] | Yes | Ordered LLM-friendly action objects (**legacy**: list[string]) |
| action_rationale | list[string] | No | Short ordered rationale strings for the action plan |
| master_reasoning | object\|null | No | Master reasoner structured output (strict JSON) |
| master_reasoning_meta | object | No | Run metadata: model/prompt versions/hashes/errors |
| master_reasoning_status | string | No | `completed` / `failed_schema_validation` / `failed_llm` / `stubbed` |
| master_reasoning_used_evidence_paths | list[string] | No | Canonical case-path references used by master output |
| post_uq | object | No | Reserved: Post-UQ agent output (V1-P6 stub; updates gate/actions, no write-back) |
| history | list[object] | Yes | Append-only event log |
| candidate_mechanisms | list[object] | Yes | Top-3 candidate mechanisms with probabilities |
| mechanism_signatures | object | Yes | Signature templates for candidate mechanisms |

### query

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| input_smiles | string | Yes | Original SMILES as provided |
| canonical_smiles | string | Yes | RDKit-canonicalized (null if invalid) |
| inchikey | string | Yes | InChIKey from canonical (null if invalid) |
| created_at | string | Yes | ISO 8601 timestamp |

### risk_scores

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| top1_sim | float | Yes | ECFP Tanimoto to nearest neighbor [0,1] |
| mean_topk_sim | float | Yes | Mean Tanimoto over top-k neighbors |
| neighbor_gap | float | Yes | top1_sim - top2_sim (differentiation signal) |
| novelty_struct | float | Yes | 1 - top1_sim [0,1] |
| mechanism_entropy | float | Yes | Neighbor label entropy [0,1] or null |
| mechanism_hint | string | Yes | Top mechanism label from neighbors |
| hint_confidence | float | Yes | Probability of top label [0,1] |
| atb_neighbor_consistency | object | No | Robust outlier check: target aTB delta vs neighbors' aTB delta distribution (see below) |
| atb_neighbor_features_all | list[object] | No | Neighbor aTB rows (`features_summary` only) used for compact derived stats |
| atb_trends_self | object | No | **Reasoning-pack projection only**: legacy target-only trend projection for compatibility |
| atb_trend_profile | object | No | **Reasoning-pack projection only**: self-only target aTB trend profile (`atb_trend_v1`) used by Master in R1+ |
| neighbor_atb_stats | object | No | Compact R2+ discriminative stats derived from target-vs-neighbor aTB distributions |

#### risk_scores.atb_neighbor_consistency (optional)

This block is computed from **structure-only** top-k neighbors (ECFP retrieval unchanged). aTB is used only as evidence/readiness augmentation.

**Inclusion rule for neighbor distribution**:
- Use only neighbors with `neighbor_atb.cache_status == "success"` AND all required delta fields present in `neighbor_atb.features_summary`.
- Neighbors with failed/partial/missing delta fields are excluded from the distribution.
- If the target aTB is missing/non-success, set `flag="target_missing"` and leave z-score aggregates null.

**Robust z-score math**:
- For each field `f`: `z_f = (x_f - median_f) / (1.4826 * MAD_f)`.
- If `MAD_f == 0`:
  - if `x_f == median_f`, use `z_f = 0`;
  - else use `z_f = null` and add warning `mad_zero:<field>`.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| enabled | bool | Yes | Whether this check is enabled in the pipeline |
| sample_size | int | Yes | Number of neighbors used in the distribution |
| fields_used | list[string] | Yes | Delta fields used (e.g., ["delta_gap","delta_dihedral","delta_volume"]) |
| median | object | Yes | Map: field -> float\|null (neighbor median per field) |
| mad | object | Yes | Map: field -> float\|null (neighbor MAD per field) |
| z_scores | object | Yes | Map: field -> float\|null (robust z-score per field) |
| outlier_score_max | float\|null | Yes | `max(abs(z_f))` over valid dimensions |
| outlier_score_rss | float\|null | Yes | `sqrt(mean(z_f^2))` over valid dimensions |
| outlier_dims | list[string] | Yes | Fields where `abs(z_f) >= thresholds.z_max` |
| flag | string | Yes | `"target_missing" | "insufficient_neighbors" | "inlier" | "outlier"` |
| reliability | string | Yes | `"low" | "medium" | "high"` |
| thresholds | object | Yes | `{ "z_max": float, "min_sample_size": int }` |
| warnings | list[string] | Yes | Deterministic warning codes (e.g., `mad_zero:delta_gap`) |
| updated_at | string | Yes | ISO8601 UTC timestamp when this block is computed |

**Reliability heuristic**:
- `low`: `sample_size < 8`, OR any `MAD_f == 0`, OR fewer than 2 valid z-score dimensions.
- `medium`: `sample_size >= 8`, at least 2 valid dimensions, and all MADs stable (`MAD_f > 0`).
- `high`: `sample_size >= 15`, 3 valid dimensions, and all MADs stable.

**Optional shortcut signal**:
- `risk_scores.readiness_atb_neighbor_flag` may mirror `atb_neighbor_consistency.flag` for gate/rationale convenience.  
  This is optional and must not replace the full object.

#### risk_scores.neighbor_atb_stats (optional, R2+)

Compact discriminative block for iterative R2/R3 reasoning; optimized for token budget and auditability.

**Direction conventions (locked):**
- `target_percentile` uses mid-rank and is in `[0,1]`:
  - `target_percentile = (count_lt + 0.5 * count_eq) / n`
  - lower = target on lower side of neighbor distribution, higher = upper side.
- `z_robust` sign is fixed:
  - `(target - neighbors_median) / (1.4826 * MAD)`
  - positive => target above median; negative => below median.

**delta_dihedral representation:**
- keep both raw and absolute channels:
  - `fields.delta_dihedral` (signed audit value),
  - `fields.abs_delta_dihedral` (primary discriminative axis for percentile/z/by-label comparison).

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| sample_size | int | Yes | Neighbor count after success+key-field filtering |
| fields | object | Yes | Per-field compact stats (`delta_dihedral`, `abs_delta_dihedral`, `delta_gap`, `delta_volume`) |
| by_label | object | No | Label-stratified medians; emitted only when at least 2 labels and each emitted label has `n>=2` |
| summary | list[string] | Yes | Human-readable compact findings (kept in stats only, not duplicated in evidence registry) |
| reliability | string | Yes | `"low" | "medium" | "high"` |

Per-field object keys (`fields.<name>`):
- `target`, `neighbors_median`, `neighbors_iqr`, `target_percentile`, `z_robust`

Low-sample behavior:
- if `sample_size < 5`: `reliability="low"` and `z_robust=null`; percentile can still be populated if computable.

#### risk_scores.atb_trend_profile (optional, R1+; reasoning-pack only)

Self-only trend profile computed from target `evidence_readiness.atb.features_summary` (no neighbor input).

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| version | string | Yes | Fixed `atb_trend_v1` |
| abs_values | object | Yes | `{delta_dihedral, delta_gap, delta_volume}` absolute magnitudes |
| buckets | object | Yes | Bucketed strength per delta field (`low/mid/high`) |
| direction | object | Yes | Sign-only trend direction (`increase/decrease/flat/unknown`) |
| overall_motion_proxy | string | Yes | `low/medium/high` derived from dihedral+volume buckets |
| ct_proxy | object | Yes | Compact CT proxy summary (`delta_gap_bucket`) |
| reliability | string | Yes | `low/medium/high` from parseable key fields |
| notes | list[string] | Yes | Short compact notes; no free-form threshold invention |

#### risk_scores.atb_neighbor_features_all (optional)

This list carries compact neighbor aTB rows (success-only), sorted by neighbor rank.
`features_summary` is retained; full neighbor `features.json` payload is excluded from master prompt context.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| neighbor_inchikey | string | Yes | Neighbor identifier |
| rank | int\|null | Yes | Neighbor rank in structure retrieval |
| sim | float\|null | Yes | Neighbor structural similarity |
| delta_gap | float\|null | Yes | Neighbor aTB delta_gap (if present) |
| delta_dihedral | float\|null | Yes | Neighbor aTB delta_dihedral (if present) |
| delta_volume | float\|null | Yes | Neighbor aTB delta_volume (if present) |
| features_summary | object\|null | Yes | Neighbor summary features (when available) |

### post_uq (reserved; V1-P6 stub)

This block is reserved for a **dedicated Post-UQ agent** that reads (`case_file` + `master_output`) and emits a gating decision + next actions.
It is defined here for forward compatibility; it may be absent until V1-P6 is implemented.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| status | string | Yes | "not_run" / "completed" / "error" |
| confidence | float | No | Agent confidence in its decision [0,1] (not LLM self-confidence) |
| contradictions | list[object] | No | Detected contradictions/conflicts with evidence (optional) |
| missing_evidence | list[string] | No | Missing evidence fields blocking or weakening conclusions |
| recommended_actions | list[object] | No | Recommended next actions (same object shape as `action_plan`) |

### master_reasoning* (release runtime)

Master output is written to top-level root keys to avoid collision with legacy `reasoning.*` payloads.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| master_reasoning | object\|null | No | Strict schema output from master reasoner |
| master_reasoning_meta | object | No | `{run_id, inputs_hash, pack_hash, pack_version, prompt_bundle_version, template_version, model, status, errors[], updated_at}` |
| master_reasoning_status | string | No | `completed` / `failed_schema_validation` / `failed_llm` / `stubbed` |
| master_reasoning_used_evidence_paths | list[string] | No | Deduplicated referenced case paths resolved from cited evidence IDs |
| reasoning.master_reasoning | object\|null | No | Mirror write for compact reasoning namespace |
| reasoning.status | string | No | Mirror of `master_reasoning_status` |
| reasoning.used_evidence_paths | list[string] | No | Resolved canonical case paths from evidence IDs |
| reasoning.used_evidence_ids | list[string] | No | Deduplicated cited evidence IDs (`E1...`) |
| reasoning.used_evidence | list[object] | No | Expanded citation rows `{evidence_id, case_path, value_preview, label, role, note}` |

#### master_reasoning evidence reference contract

- Every `evidence_used` entry must use object form:
  - `{evidence_id, note, role}`
- `evidence_id` must:
  - match `^E[0-9]+$` or `^E_ATB_TREND_[1-4]$`,
  - resolve through `reasoning_pack.evidence_registry[]`,
  - for `source_type=case`: `case_path` must resolve to an existing non-null/non-empty value in case JSON,
  - for `source_type=derived_pack`: `pack_path` must resolve to an existing non-null/non-empty value in reasoning_pack.
- `reasoning_pack.evidence_registry` is the only citation namespace for master:
  - list rows with `{evidence_id, source_type, label, value_preview, role_hint, note_hint}` and either:
    - `source_type=case`: `{case_path}`,
    - `source_type=derived_pack`: `{pack_path, derived_from_case_paths?}`,
  - capped (target 10-20 rows) and sorted by importance,
  - no `path_map` / `allowed_evidence_paths` payload in the model prompt.
- R2+ derived evidence IDs:
  - `E21` abs-delta-dihedral distribution summary,
  - `E22` delta-gap distribution summary,
  - `E23` label-stratified comparison (conditional),
  - `E24` reliability note.
- R1+ self-trend evidence IDs:
  - `E31` aTB torsion trend (`/evidence_readiness/atb/features_summary/delta_dihedral`, note from self profile bucket/direction),
  - `E32` aTB CT proxy trend (`/evidence_readiness/atb/features_summary/delta_gap`),
  - `E33` aTB volume trend (`/evidence_readiness/atb/features_summary/delta_volume`),
  - `E34` aTB overall motion proxy (`/risk_scores/atb_trend_profile/overall_motion_proxy`, derived-pack evidence),
  - legacy compatibility IDs:
  - `E_ATB_TREND_1..4` from `risk_scores.atb_trends_self` (target-only trend interpretation).
- Debug-only hints in case file:
  - `risk_scores.mechanism_hint` and `risk_scores.hint_confidence` may exist for routing/debug,
  - but both are excluded from master model input (`reasoning_pack`) and are forbidden as master evidence references.
- Neighbor support rule:
  - when `risk_scores.top1_sim < 0.55`, evidence paths under `/neighbors/*` may not use `role="support"`.
- aTB citation minimum in `supporting_chain`:
  - require at least 2 citations with prefix `/evidence_readiness/atb/features_summary/`,
  - and at least 1 of them with `role="support"`.
- required chain shape:
  - `supporting_chain` length must be 4 with ordered `step_id` = `A,B,C,D`.
  - each step must include at least one `evidence_used` entry.
  - step A must include at least one aTB citation.
  - step D requires explicit discrimination intent (or `predictions` length >= 3).

#### master_output_schema_v3 (default)

- `mechanism_claim.primary_hypothesis.atb_support_level`: `"none" | "weak" | "strong"`
- `competing_hypotheses[*].atb_support_level`: `"none" | "weak" | "strong"`
- `supporting_chain[*].step_id`: `"A" | "B" | "C" | "D"`
- `supporting_chain[*].step_name`: enum:
  - `"ct_family" | "torsion_access" | "aIE_bridge" | "neighbor_priors" | "discriminators" | "limits"`
- validator rejects invented thresholds/ranges in generated text unless numeric values come from `reasoning_config.thresholds`.

#### aTB support-level rubric (validator)

- from `abs(evidence_readiness.atb.features_summary.delta_dihedral)`:
  - `< 8.0` => `none`
  - `>= 8.0 and < 15.0` => `weak`
  - `>= 15.0` => `strong`
- validator requires primary hypothesis support level to be consistent with this mapping.

### evidence_readiness

Evidence readiness contains the state machine for evidence collection across three tracks (aTB, literature, experiment) plus availability flags and a reasoning gate.

#### evidence_readiness.atb

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| cache_status | string | Yes | "absent" / "pending" / "success" / "failed" / "partial" (historical fact from cache) |
| request_status | string | Yes | "not_requested" / "requested" / "done" (workflow state for this case) |
| missing_fields | list[string] | Yes | aTB fields missing if partial |
| features_summary | object | No | Summary of key aTB features if cache_status=success (see below) |
| last_update | string | Yes | ISO 8601 timestamp of last status change |
| error_stage | string | No | Stage where failure occurred (if failed) |
| error_msg | string | No | Error message (truncated, max 500 chars) |

**features_summary** (attached when cache_status=success or partial):

| Field | Type | Description |
|-------|------|-------------|
| delta_volume | float | S1 - S0 volume difference |
| delta_gap | float | S1 - S0 HOMO-LUMO gap difference |
| delta_dihedral | float | S1 - S0 dihedral angle difference |
| excitation_energy | float | Vertical excitation energy (raw float cast from cache, no unit conversion) |
| s0_volume | float | (optional) S0 molecular volume |
| s1_volume | float | (optional) S1 molecular volume |
| _excitation_energy_raw | string | (debug) Raw value as read from cache for validation |

#### evidence_readiness - neighbor coverage metrics (top-level)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| neighbor_atb_success_rate | float | No | Fraction of top-k neighbors with successful aTB cache |
| neighbor_atb_keyfield_rate | float | No | Fraction with all 4 key aTB fields present |

> **Note (v0.7)**: `features_summary` is attached when the target molecule has aTB cache. Neighbor coverage metrics (`neighbor_atb_success_rate`, `neighbor_atb_keyfield_rate`) are at `evidence_readiness` top-level, NOT nested under `atb`.

#### evidence_readiness.literature

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| status | string | Yes | "not_started" / "pending" / "found" / "not_found" (workflow state) |
| mode | string | No | "relaxed" / "strict" (strict requires auditable citations before writeback) |
| verification_status | string | No | "absent" / "candidates_only" / "verified" / "blocked" (writeback gate) |
| candidates | list[object] | No | Candidate papers/leads (relaxed; `verification="unverified"`); not written to evidence_table |
| verified_sources | list[object] | No | Strict-only verified sources with locator info; only these may write EvidenceClaim rows |
| sources | list[string] | Yes | Legacy list of identifiers (DOIs, URLs) |
| last_update | string | Yes | ISO 8601 timestamp |
| notes | string | No | Free-form notes from search |

**candidates[]** item fields (relaxed; unverified):

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| title | string | Yes | Paper title |
| year | int | No | Publication year |
| doi | string | No | DOI if present; may be null/unverified |
| url | string | No | Landing page URL (may be null) |
| pdf_url | string | No | Direct PDF URL if clearly available |
| source_url | string | No | Source/citation URL if gateway surfaces it |
| verification | string | Yes | Always "unverified" for candidates |
| why_relevant | string | No | Short relevance note |
| retrieved_at | string | No | ISO 8601 when retrieved (optional) |

**verified_sources[]** item fields (strict; auditable; writeback allowed):

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| source_url | string | Yes | Source URL from web_search citations/sources |
| doi | string | No | DOI (must be verified from source); null if unknown |
| title | string | No | Paper title |
| locator | object | Yes | Locator for claims (e.g., {type: page|table|snippet, value: ...}) |
| retrieved_at | string | Yes | ISO 8601 when fetched |
| verification | string | Yes | Always "verified" |

**Writeback rule (V1)**:
- Only `verified_sources[]` entries may be used to create `literature_claim` rows written back into `data/evidence_table.parquet`.
- `candidates[]` are leads only; do NOT write them into evidence_table.

#### evidence_readiness.experiment

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| status | string | Yes | "not_requested" / "requested" / "received_partial" / "received_full" |
| requested_fields | list[string] | Yes | Fields requested (train-only baseline: ["emission_solid", "emission_aggr"]) |
| received_fields | list[string] | Yes | Fields actually received |
| last_update | string | Yes | ISO 8601 timestamp |
| notes | string | No | Free-form notes |

#### evidence_readiness.minimal_experiment_available

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| has_emission | bool | Yes | At least one of {emission_solid, emission_aggr} available |
| has_qy | bool | Yes | Reserved for future datasets; typically false in train-only facts |
| has_tau | bool | Yes | Reserved for future datasets; typically false in train-only facts |
| has_solvent | bool | Yes | Reserved for future datasets; typically false in train-only facts |

#### evidence_readiness.current_gate

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| ready_for_reasoning | bool | Yes | True when sufficient evidence exists |
| reason | string | Yes | Human-readable explanation |
| reasoning_mode | string | No | "blocked" / "normal" / "conservative" (LLM-friendly behavior mode) |

**Gate logic (V0.7)**:
- `ready_for_reasoning = true` if:
  - (`atb.cache_status == "success"` AND all key fields present in features_summary) OR
  - `minimal_experiment_available.has_emission == true`
- Otherwise `false` with reason explaining what's missing
- **Note**: Gate uses `cache_status` (historical fact), not `request_status` (workflow state)

**Action plan consistency (V0.7)**:
- New case files use structured action objects and may include non-blocking follow-ups even when `ready_for_reasoning == true`.
- **Legacy**: older docs/cases used `action_plan = ["run_master_reasoner"]` when ready; keep as backward compatibility reference.

### neighbors

List of top-k structural neighbors (typically k=10) with attached aTB evidence.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| rank | int | Yes | 1 = most similar |
| neighbor_inchikey | string | Yes | Neighbor's InChIKey |
| sim | float | Yes | Tanimoto similarity [0,1] |
| neighbor_mechanism_label | string | Yes | Neighbor's mechanism_id |
| neighbor_atb | object | Yes | Neighbor's aTB cache evidence (see below) |

**neighbor_atb** object:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| cache_status | string | Yes | "absent" / "pending" / "success" / "failed" / "partial" |
| missing_fields | list[string] | No | Key fields missing (for partial) |
| features_summary | object | No | Same structure as target features_summary (if cache_status=success) |

### action_plan

Ordered list of LLM-friendly action objects.

**Current schema (v0.7+)**:
Each action is an object:
- `action`: string enum (see below)
- `priority`: int (1..N), strictly increasing
- `status`: "not_started" | "pending" | "done" | "skipped"
- `inputs`: object (action-specific)
- `expected_outputs`: list[string] (high-level expected artifacts/fields)
- `blocking`: bool (true if this action gates readiness)
- `notes`: string (short guidance for an LLM controller)

**Allowed actions (minimum set)**:
- `run_master_reasoner`
- `compute_target_atb`
- `retry_target_atb_alt_settings`
- `literature_search_web`
- `mineru_extract_pdf`
- `request_min_experiment_emission`
- `request_experiment_qy`
- `request_experiment_tau`
- `request_experiment_solvent_details`
- `expand_structure_neighbors`

**Legacy (backward compatibility)**:
Older case files may contain `action_plan: list[string]` with values like:
`["compute_target_atb","literature_search","request_min_experiment_emission"]`.
Validators should accept both formats, but new case creation should write the object form.

### candidate_mechanisms

Top-3 candidate mechanisms derived from neighbor label distribution (similarity-weighted).

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| label | string | Yes | Mechanism label (e.g., "ICT", "TICT", "ESIPT") |
| prob | float | Yes | Probability [0,1] from softmax-weighted aggregation |

### mechanism_signatures

Map of mechanism label → signature template (from domainRAG). Each entry:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| required_atb_fields | list[string] | Yes | aTB fields needed to identify this mechanism |
| required_experiment_fields | list[string] | Yes | Experimental fields needed |
| disambiguation_actions | list[string] | Yes | Actions to distinguish from other mechanisms |
| structure_triggers | list[string] | No | Structural patterns triggering this mechanism |

### history

Append-only event log tracking all updates to the case file.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| timestamp | string | Yes | ISO 8601 when event occurred |
| actor | string | Yes | "data_agent" / "chem_agent" / "system" / "user" |
| event_type | string | Yes | Event type (see below) |
| details | object | No | Event-specific details |

**Event types**:
- `"case_created"` - Initial case file creation
- `"action_marked"` - Action status changed (details: {action, new_status})
- `"atb_updated"` - aTB status changed
- `"literature_updated"` - Literature search status changed
- `"experiment_updated"` - Experiment status changed
- `"gate_evaluated"` - Reasoning gate re-evaluated
- `"manual_edit"` - Manual user edit

### Example Case File (minimal)

```json
{
  "case_id": "MXWJVTOOROXGIU-UHFFFAOYSA-N",
  "case_version": "0.7",
  "query": {
    "input_smiles": "c1ccc(C(=C(c2ccccc2)c2ccccc2)c2ccccc2)cc1",
    "canonical_smiles": "c1ccc(C(=C(c2ccccc2)c2ccccc2)c2ccccc2)cc1",
    "inchikey": "MXWJVTOOROXGIU-UHFFFAOYSA-N",
    "created_at": "2026-01-22T10:30:00Z"
  },
  "risk_scores": {
    "top1_sim": 0.85,
    "mean_topk_sim": 0.62,
    "neighbor_gap": 0.12,
    "novelty_struct": 0.15,
    "mechanism_entropy": 0.45,
    "mechanism_hint": "RIR",
    "hint_confidence": 0.78
  },
  "evidence_readiness": {
    "atb": {
      "cache_status": "success",
      "request_status": "not_requested",
      "missing_fields": [],
      "features_summary": {
        "delta_volume": 2.5,
        "delta_gap": -0.15,
        "delta_dihedral": -5.2,
        "excitation_energy": 3.1
      },
      "neighbor_atb_success_rate": 0.6,
      "neighbor_atb_keyfield_rate": 0.5,
      "last_update": "2026-01-22T10:30:00Z",
      "error_stage": null,
      "error_msg": null
    },
    "literature": {
      "status": "not_started",
      "mode": "relaxed",
      "verification_status": "absent",
      "candidates": [],
      "verified_sources": [],
      "sources": [],
      "last_update": "2026-01-22T10:30:00Z",
      "notes": null
    },
    "experiment": {
      "status": "not_requested",
      "requested_fields": [],
      "received_fields": [],
      "last_update": "2026-01-22T10:30:00Z",
      "notes": null
    },
    "minimal_experiment_available": {
      "has_emission": false,
      "has_qy": false,
      "has_tau": false,
      "has_solvent": false
    },
    "current_gate": {
      "ready_for_reasoning": true,
      "reason": "atb_success"
    }
  },
  "neighbors": [
    {
      "rank": 1,
      "neighbor_inchikey": "XXXXX-YYYYY-Z",
      "sim": 0.85,
      "neighbor_mechanism_label": "RIR",
      "neighbor_atb": {
        "cache_status": "success",
        "features_summary": {"delta_volume": 1.8, "delta_gap": -0.12, "delta_dihedral": -4.1, "excitation_energy": 3.0}
      }
    },
    {
      "rank": 2,
      "neighbor_inchikey": "AAAAA-BBBBB-C",
      "sim": 0.73,
      "neighbor_mechanism_label": "RIR",
      "neighbor_atb": {"cache_status": "absent"}
    }
  ],
  "candidate_mechanisms": [
    {"label": "RIR", "prob": 0.78},
    {"label": "TICT", "prob": 0.15},
    {"label": "neutral aromatic", "prob": 0.07}
  ],
  "mechanism_signatures": {
    "RIR": {
      "required_atb_fields": ["delta_dihedral", "delta_volume"],
      "required_experiment_fields": ["qy_sol", "qy_solid"],
      "disambiguation_actions": ["compare_dihedral_change", "check_qy_enhancement"]
    },
    "TICT": {
      "required_atb_fields": ["delta_gap", "delta_dihedral"],
      "required_experiment_fields": ["emission_sol", "tested_solvent"],
      "disambiguation_actions": ["check_solvent_polarity_dependence"]
    }
  },
  "action_plan": ["run_master_reasoner"],
  "history": [
    {
      "timestamp": "2026-01-22T10:30:00Z",
      "actor": "data_agent",
      "event_type": "case_created",
      "details": {"source": "smiles_input"}
    }
  ]
}
## 2026-02 Addendum: Case File fields for emission acquisition switch

This addendum extends the current Case File schema for train-only emission completion workflows.

### New/updated blocks

```yaml
evidence_acquire:
  emission:
    mode: "offline_pdf"        # enum: offline_pdf | web_search
    strictness: "relaxed"      # enum: strict | relaxed
    last_idempotency_key: "string|null"
    extractor_name: "string|null"
    extractor_version: "string|null"
    extractor_config_hash: "string|null"
    normalizer_config_hash: "string|null"
    mapping_version: "string|null"
    pdf_page_selection_hash: "string|null"
    state: "blocked_input_missing|failed_extract|extracted_no_writeback|ready_for_reasoning|null"

inputs:
  offline_pdfs:                # optional, used by offline_pdf mode
    - path_or_id: "string"     # local path or managed file id
      sha256: "string|null"
      note: "string|null"
      provided_by: "string|null"

literature:
  candidates:
    - doi: "string|null"
      title: "string|null"
      url: "string|null"
      pdf_url: "string|null"
      source_type: "offline_pdf|web_search|manual"
      source_ref: "string|null"  # URL / local file id / citation locator

evidence_writeback_policy:
  mode: "strict|relaxed|off"
  note: "string|null"

current_gate:
  state: "blocked_input_missing|failed_extract|extracted_no_writeback|ready_for_reasoning"
  ready_for_reasoning: "bool"
  reason: "string"

reasons: ["string", "..."]       # append-only reason codes
next_actions: ["string", "..."]  # append-only suggested actions
```

### Emission target fields (explicit scope)

Only these two target outputs are in scope for emission completion:

```yaml
target_fields:
  emission_aggr_nm: "number|null"
  emission_solid_or_film_nm: "number|null"
```

With provenance:

```yaml
target_fields_provenance:
  emission_aggr_nm:
    source_type: "offline_pdf|web_search|manual"
    source_locator: "string|null"    # e.g., Table 1 p2
    confidence: "number|null"
    extracted_at: "datetime|null"
  emission_solid_or_film_nm:
    source_type: "offline_pdf|web_search|manual"
    source_locator: "string|null"
    confidence: "number|null"
    extracted_at: "datetime|null"
```

### E0 candidate staging schema (append-only in case)

```yaml
evidence_candidates_staging:
  - candidate_id: "string"
    field: "emission_aggr_nm|emission_solid_or_film_nm|null"
    condition_bucket: "aggregate|film|solid|powder|crystal|unknown"
    normalized_value_nm: "number|null"
    raw_value: "string|number|null"
    unit: "nm"
    value_source_kind: "table|figure|text"
    source_type: "offline_pdf|web_search|manual"
    source_ref: "string"
    source_locator: "string|null"
    page: "int|null"
    bbox: "object|null"
    identity_match: "matched|ambiguous|unmatched"
    identity_match_confidence: "number(0..1)"
    confidence: "number(0..1)"
    verification_status: "verified|unverified|rejected"
    rejection_reason: "string|null"
    run_id: "string"
    idempotency_key: "string"
    artifact_ref: "string"
```

### E0 replay artifact contract

```yaml
artifacts/{run_id}/:
  - 00_input_snapshot.json
  - 01_extractor_raw.json
  - 02_candidates_normalized.json
  - 03_agent_patch.json           # RFC6902 JSON Patch
  - 04_case_before.json
  - 05_case_after.json
  - 06_case_diff.json
  - manifest.json                 # sha256 for each artifact
```

`agent_runs[].inputs_hash` must equal the sha256 of canonical bytes from `00_input_snapshot.json`.

### Writeback gate semantics

- `offline_pdf` can produce strict evidence when PDF input and locator are explicit; strict case writeback is allowed.
- `web_search` stays candidate-only unless citations/sources are complete enough for strict traceability.
- Under current policy, relaxed web-search outputs must remain in `literature.candidates[]` and must not be written into `evidence_table`.
- E0 hard guard: evidence-table writeback is disabled (`WRITEBACK_EVIDENCE_TABLE=false`); case file is the only write target.
