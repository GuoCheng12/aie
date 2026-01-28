# doc/schemas.md

## Core Artifact Schemas (V0)

> This file defines column schemas for all parquet artifacts in V0.
> Use this as the single source of truth when implementing data pipelines.

---

## 1. `data/private_clean.parquet`

Standardized private dataset with unit normalization and missing masks.

> **Note**: Invalid SMILES produce null `canonical_smiles`/`inchikey` and are routed to "Evidence-insufficient" by the UQ router.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| id | int64 | No | Original row ID |
| code | string | Yes | Molecule code |
| smiles | string | No | Original SMILES (as-is from CSV) |
| canonical_smiles | string | Yes | RDKit-canonicalized SMILES (null if invalid SMILES) |
| inchikey | string | Yes | InChIKey from canonical SMILES (null if invalid SMILES) |
| molecular_weight | float64 | Yes | MW (g/mol) |
| absorption | string | Yes | Raw absorption field (unparsed) |
| absorption_peak_nm | float64 | Yes | Parsed peak wavelength (nm), if extractable |
| emission_sol | float64 | Yes | Emission wavelength in solution (nm) |
| emission_solid | float64 | Yes | Emission wavelength in solid (nm) |
| emission_aggr | float64 | Yes | Emission wavelength in aggregate (nm) |
| emission_crys | float64 | Yes | Emission wavelength in crystal (nm) |
| qy_sol | float64 | Yes | Quantum yield in solution, normalized [0,1] |
| qy_solid | float64 | Yes | Quantum yield in solid, normalized [0,1] |
| qy_aggr | float64 | Yes | Quantum yield in aggregate, normalized [0,1] |
| qy_crys | float64 | Yes | Quantum yield in crystal, normalized [0,1] |
| qy_sol_raw | float64 | Yes | Raw qy_sol (percent, 0–100) |
| qy_solid_raw | float64 | Yes | Raw qy_solid (percent) |
| qy_aggr_raw | float64 | Yes | Raw qy_aggr (percent) |
| qy_crys_raw | float64 | Yes | Raw qy_crys (percent) |
| qy_unit_inferred | string | No | "percent" (constant for this dataset) |
| tau_sol | float64 | Yes | Lifetime in solution (ns) |
| tau_solid | float64 | Yes | Lifetime in solid (ns) |
| tau_aggr | float64 | Yes | Lifetime in aggregate (ns) |
| tau_crys | float64 | Yes | Lifetime in crystal (ns) |
| tau_sol_raw | float64 | Yes | Raw tau_sol |
| tau_solid_raw | float64 | Yes | Raw tau_solid |
| tau_aggr_raw | float64 | Yes | Raw tau_aggr |
| tau_crys_raw | float64 | Yes | Raw tau_crys |
| tau_sol_outlier | bool | Yes | True if tau_sol > outlier threshold |
| tau_solid_outlier | bool | Yes | True if tau_solid > outlier threshold |
| tau_aggr_outlier | bool | Yes | True if tau_aggr > outlier threshold |
| tau_crys_outlier | bool | Yes | True if tau_crys > outlier threshold |
| tested_solvent | string | Yes | Solvent used in testing |
| mechanism_id | string | Yes | Coarse mechanism label (aggregated per inchikey for P5b mechanism_entropy) |
| features_id | int64 | Yes | Features label |
| emission_sol_missing | bool | No | True if emission_sol is null |
| emission_solid_missing | bool | No | True if emission_solid is null |
| emission_aggr_missing | bool | No | True if emission_aggr is null |
| emission_crys_missing | bool | No | True if emission_crys is null |
| qy_sol_missing | bool | No | True if qy_sol is null |
| qy_solid_missing | bool | No | True if qy_solid is null |
| qy_aggr_missing | bool | No | True if qy_aggr is null |
| qy_crys_missing | bool | No | True if qy_crys is null |
| tau_sol_missing | bool | No | True if tau_sol is null |
| tau_solid_missing | bool | No | True if tau_solid is null |
| tau_aggr_missing | bool | No | True if tau_aggr is null |
| tau_crys_missing | bool | No | True if tau_crys is null |
| absorption_missing | bool | No | True if absorption is null |
| tested_solvent_missing | bool | No | True if tested_solvent is null |

---

## 2. `data/molecule_table.parquet`

Unique molecules by InChIKey with ID mapping.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| inchikey | string | No | **Primary key**, unique |
| canonical_smiles | string | No | RDKit-canonicalized SMILES |
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

## V1 Planned Artifacts (minimal schemas)

### `data/evidence_table.parquet` (V1 EvidenceClaim)
| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| evidence_id | string | No | UUID/string |
| subject_inchikey | string | Yes | Molecule InChIKey (nullable if unknown) |
| evidence_type | string | No | enum: private_observation \| atb_computation \| literature_claim |
| field | string | No | e.g., emission_sol, qy_aggr, delta_gap, excitation_energy |
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
| action_plan | list[string] | Yes | Ordered evidence ladder actions |
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
| status | string | Yes | "not_started" / "pending" / "found" / "not_found" |
| sources | list[string] | Yes | Found source identifiers (DOIs, URLs) |
| last_update | string | Yes | ISO 8601 timestamp |
| notes | string | No | Free-form notes from search |

#### evidence_readiness.experiment

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| status | string | Yes | "not_requested" / "requested" / "received_partial" / "received_full" |
| requested_fields | list[string] | Yes | Fields requested (e.g., ["emission_sol", "qy_sol"]) |
| received_fields | list[string] | Yes | Fields actually received |
| last_update | string | Yes | ISO 8601 timestamp |
| notes | string | No | Free-form notes |

#### evidence_readiness.minimal_experiment_available

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| has_emission | bool | Yes | At least one emission_* field available |
| has_qy | bool | Yes | At least one qy_* field available |
| has_tau | bool | Yes | At least one tau_* field available |
| has_solvent | bool | Yes | tested_solvent available |

#### evidence_readiness.current_gate

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| ready_for_reasoning | bool | Yes | True when sufficient evidence exists |
| reason | string | Yes | Human-readable explanation |

**Gate logic (V0.7)**:
- `ready_for_reasoning = true` if:
  - (`atb.cache_status == "success"` AND all key fields present in features_summary) OR
  - `minimal_experiment_available.has_emission == true`
- Otherwise `false` with reason explaining what's missing
- **Note**: Gate uses `cache_status` (historical fact), not `request_status` (workflow state)

**Action plan consistency (V0.7)**:
- If `ready_for_reasoning == true`: action_plan = `["run_master_reasoner"]`
- Else: Follow evidence ladder (compute_target_atb → literature_search → request_min_experiment_emission)

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

Ordered list of evidence ladder actions. Each action is a string:
- `"compute_target_atb"` - run aTB computation
- `"literature_search"` - search for literature evidence
- `"request_min_experiment_emission"` - request emission measurement
- `"request_min_experiment_qy"` - request QY measurement
- `"collect_{field}"` - collect specific missing field
- `"run_master_reasoner"` - proceed to reasoning (only when ready_for_reasoning=true)

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
