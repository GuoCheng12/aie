# V1 Detailed Plan (CURRENT)

## Goal
Build the Evidence Layer + Light KG + Chem Agent literature evidence loop, while keeping SMILES-first case file as the shared artifact.

---

## Data Refresh (train-only facts, current migration)
- **Fact source**: `data/train.csv` is now the only source for `data/private_clean.parquet`.
- **Business columns (authoritative)**: `id`, `code`, `SMILES`, `reference`, `molecular_weight`, `emission_solid`, `emission_aggr`, `features_id`, `mechanism_id`, `doi`.
- **Allowed derived columns in private_clean**: `canonical_smiles`, `inchikey`, and explicit missing flags (`emission_solid_missing`, `emission_aggr_missing`).
- **Explicitly excluded from facts**: `data/test.csv` (used only for simulated user input / evaluation; never merged into private_clean or private_observation evidence).
- **Compatibility rule**: downstream V1/V0 scripts must not assume legacy private fields (`absorption/qy/tau/tested_solvent`); when encountered, they must be removed or explicitly skipped.

### Rebuild order after train-only migration
1. P1 ingestion: `private_clean.parquet` -> `molecule_table.parquet` -> `rdkit_features.parquet`
2. Anchor space: `anchor_neighbors_ecfp.parquet` (structure-only)
3. UQ (pre-aTB): `mechanism_label_map.parquet` -> `mechanism_entropy_pre_atb.parquet` -> `uq_scores_pre_atb.parquet` -> `uq_scores_pre_atb_p5b.parquet`
4. Reports/queues: `reports/*.json` + `queue_*_pre_atb_p5b.parquet` + dashboard
5. V1 evidence/graph: `evidence_table.parquet` -> `graph_nodes.parquet` + `graph_edges.parquet` (retrieval consumes updated graph directly)

---

## V1 Objectives
- Add a structured evidence_table with provenance, conditions, and confidence.
- Build a light graph (nodes/edges) for traceable evidence ↔ molecule ↔ conditions.
- Provide subgraph retrieval for GraphRAG context (structure-first neighbors + evidence).
- Extend Chem Agent to harvest literature evidence into EvidenceClaim rows.
- Update reports to cite evidence IDs and provenance (no new mechanisms yet).

## V1 Deliverables
- `data/evidence_table.parquet`
- `data/graph_nodes.parquet`
- `data/graph_edges.parquet`
- `src/graph/retrieval.py` (subgraph API)
- `src/agents/chem_literature_agent.py`
- `reports/{id}.json` (with evidence references)

## V1 Milestones
- **V1-P0**: Define evidence_table schema + provenance rules (doc/schemas.md)
- **V1-P1**: Build evidence_table from existing sources (private_clean + atb_features/atb_qc); later append literature EvidenceClaim rows
  - Inputs: `data/private_clean.parquet`, `data/atb_features.parquet`, `data/atb_qc.parquet`
  - Output: `data/evidence_table.parquet` (per `doc/schemas.md` V1 schema)
  - Manifest: `data/evidence_table_build_manifest.json` (counts by evidence_type/field + invalid rows)
  - Validator: `python -m src.graph.validate_evidence_table`
- **V1-P2**: Export light graph tables (nodes.parquet, edges.parquet) from evidence_table + similarity edges
  - Export: evidence_table → Molecule/Evidence/Condition nodes + HAS_* / UNDER_CONDITION edges
  - Add structure-only SIMILAR_TO edges from `data/anchor_neighbors_ecfp.parquet` (no aTB / no mechanism nodes)
- **V1-P3**: Implement subgraph retrieval API (inchikey → 1–2 hop neighborhood) for GraphRAG context
  - Input: `inchikey` (+ optional budgets: `max_nodes`, `max_edges`)
  - Output: `{nodes, edges, provenance_refs}`
    - nodes: list of `{node_id, node_type, props}`
    - edges: list of `{src, rel, dst, props}`
    - provenance_refs: list of `evidence_id` included
  - Budget guideline: default `max_nodes=50`, `max_edges=200`
- **V1-P4-pre**: Literature candidate loop (relaxed; NOT writeback)
  - Input: Case File (inchikey + alias/common names + candidate mechanisms) OR direct (inchikey + alias list)
  - Retrieval tool: `literature_search_tool` (implementation may be `web_search` / deep research / external gateway). Tool is replaceable; strict/relaxed rules remain the same.
  - Output: populate Case File `evidence_readiness.literature.candidates[]` (candidate papers/leads; `verification="unverified"`);
    set `evidence_readiness.literature.mode="relaxed"` and `verification_status="candidates_only"`.
  - Constraint: relaxed candidates may not have citations/sources (gateway passthrough may be incomplete); do NOT write to `evidence_table` in this phase.
  - Gateway behavior (known degradation): gateway may append non-JSON text after JSON; parser may recover the leading JSON but treat this as a relaxed-mode signal.
  - Retrieval keys (initial): InChIKey + common name/alias (no SMILES search as primary)
- **V1-P4-strict**: Verified literature evidence loop (writeback allowed)
  - Requirement: each paper/claim must have traceable `source_url` (and DOI when available) plus locator info (e.g., page number / table reference / quoted snippet).
  - Output: move entries into Case File `evidence_readiness.literature.verified_sources[]` and set `mode="strict"`, `verification_status="verified"`;
    append `literature_claim` rows to `data/evidence_table.parquet` with provenance + conditions + confidence.
- **V1-P5**: Reports reference evidence IDs + provenance; add writeback for EvidenceClaim (not new mechanisms yet)
- **V1-P6 (stub)**: Post-UQ agent slot (no write-back yet)
  - Input: `case_file` + `master_output` (Master Reasoner output)
  - Output: update Case File `evidence_readiness.current_gate` + `action_plan` based on evidence coverage/conflict signals; no new mechanism/hypothesis nodes in V1

## Interfaces
- **Case File** (`cases/{case_id}.json`) remains the shared artifact.
- **Evidence Table** (`data/evidence_table.parquet`) stores EvidenceClaim rows with provenance + conditions.
- **Graph Tables** (`data/graph_nodes.parquet`, `data/graph_edges.parquet`) derived from evidence_table + similarity edges.
- **Reports** cite evidence IDs and provenance; no mechanism writeback in V1.

## V1 Policy Notes
- Anchor retrieval remains **structure-only** (ECFP/structural embeddings). aTB is evidence/readiness only.
- Literature evidence is stored as EvidenceClaim with provenance + conditions; never overwrites `private_clean`.
- SMILES-first case file remains the shared artifact; evidence/readiness should be attached, not used for retrieval.
- Guardrail: V1 writes back EvidenceClaim only; no new mechanism/hypothesis nodes yet.
- EvidenceClaim values should be typed: use `value_num` when parseable (filterable), and keep raw extracted text in `value` for audit/fallback.

### aTB Neighborhood Consistency Check (structure retrieval unchanged)
This is an **extra case-file signal** for the Master Reasoner / post-UQ. It does **NOT** affect anchor retrieval or neighbor indexing (still ECFP/structure-only).

**When to compute**:
- Use existing top-k **STRUCTURE neighbors** (from ECFP retrieval already in the case).
- Require target `evidence_readiness.atb.cache_status == "success"` AND target delta fields exist.
- Build neighbor distribution using only neighbors with:
  - `neighbor_atb.cache_status == "success"`, AND
  - all required delta fields present in `neighbor_atb.features_summary`.

**Robust z-scores (per delta dimension)**:
- For each delta field `d`:
  - `median_d = median(neighbor_vals_d)`
  - `mad_d = median(|neighbor_vals_d - median_d|)`
  - `z_d = (target_d - median_d) / (1.4826 * mad_d + eps)`

**Outputs (case file)**:
- `outlier_score_max = max(|z_d|)`
- `outlier_score_rss = sqrt(mean(z_d^2))` (optional)
- `outlier_dims = [d for d if |z_d| >= 3.5]`
- `sample_size` = number of neighbors used in the distribution

**Interpretation** (suggested thresholds on `outlier_score_max`):
- `< 2`: inlier
- `[2, 3.5)`: borderline
- `>= 3.5`: outlier
- If `sample_size < 5`: insufficient_sample (do not compute z-scores)

### Case file action plan semantics (LLM-friendly)
The Case File action plan is designed to be **directly executable by an LLM** (as a controller/reasoner). It is an ordered list of **structured action objects**, plus a short rationale list.

**Definitions**:
- `evidence_readiness.current_gate.reasoning_mode ∈ {"blocked","normal","conservative"}`:
  - `blocked`: do not run the reasoner yet; must collect blocking evidence first.
  - `normal`: run the reasoner normally; evidence is sufficient and consistent with neighborhood.
  - `conservative`: reasoner may run, but must explicitly hedge and request escalation evidence.
- `action_plan`: ordered list of action objects (not strings).
- `action_rationale`: ordered short strings explaining why these actions were chosen and prioritized.

**Decision rules (pre-UQ / SMILES-first)**:
- If target `atb.cache_status=="success"` AND `risk_scores.atb_neighbor_consistency.flag ∈ {"inlier","borderline"}`:
  - `reasoning_mode="normal"`, `ready_for_reasoning=true`
- If target `atb.cache_status=="success"` AND `flag=="outlier"`:
  - `reasoning_mode="conservative"`, `ready_for_reasoning=true` (do NOT block)
  - Must add evidence escalation actions (literature/MinerU/expand neighbors) to validate mechanism hypotheses
- If target `atb.cache_status ∈ {"failed","absent","pending","partial"}` AND `has_emission==false`:
  - `reasoning_mode="blocked"`, `ready_for_reasoning=false`
- If target `atb.cache_status=="failed"` AND `has_emission==true`:
  - `reasoning_mode="conservative"`, `ready_for_reasoning=true`

**Important**:
- aTB-outlier does NOT change structure retrieval; it only changes `reasoning_mode` and the recommended next actions.

## Risks & Guardrails
- **Provenance**: every EvidenceClaim must include source, timestamp, and extraction method.
- **Condition mismatch**: keep measurement conditions explicit; avoid merging incompatible conditions.
- **No overwrites**: evidence appends only; never mutate private_clean or experimental facts.
- **Traceability**: all graph edges must map back to evidence_table row IDs.
