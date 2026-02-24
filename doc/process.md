# V1 Detailed Plan (CURRENT)

## Goal
Build the Evidence Layer + Light KG + Chem Agent literature evidence loop, while keeping SMILES-first case file as the shared artifact.

## Current execution mode (multi-agent refactor, authoritative)

This repo now treats the production path as a **multi-agent orchestrator loop** (not CLI-first glue code):

1. `DataAgent.run(case)` -> patch
2. `ChemAgent.run(case)` -> patch
3. `ReadyAgent.run(case)` -> patch (gate owner)
4. if gate ready: `ReasoningAgent.run(case)` -> patch/output
5. `JudgeAgent.run(case)` -> patch
6. `ReadyAgent.run(case)` again -> final gate/action reconciliation

All writes must be RFC6902 patch writes with:
- per-agent path whitelist
- append-only path enforcement
- per-step idempotency key
- replay artifacts (input snapshot, raw outputs, patch, case before/after/diff, manifest)

### Agent write permissions (hard policy)

- **Data Agent**
  - allowed: `query.*`, `neighbors[]`, structural `risk_scores.*`, `agent_runs[]`
  - forbidden: `current_gate`, `action_rationale`, `action_plan`, `target_fields*`
- **Chem Agent**
  - allowed: `evidence_readiness.atb.*`, `evidence_readiness.literature.*`, `evidence_readiness.experiment.*`,
    `target_fields*`, `target_fields_provenance*`, `evidence_candidates_staging[]`, `agent_runs[]`
  - forbidden: `current_gate`, `action_rationale`
- **Ready Agent (gate owner)**
  - allowed: `current_gate.*`, `action_rationale`, `action_plan`, optional `risk_scores.readiness_*`, `agent_runs[]`
  - forbidden: `target_fields*`, `evidence_candidates_staging[]`, `evidence_readiness.*`
- **Reasoning Agent**
  - allowed: `reasoning.*`, `agent_runs[]`
  - forbidden: `current_gate`, `action_rationale`, `target_fields*`
- **Judge Agent**
  - allowed: `post_uq.*`, append-only `action_plan[]` suggestions, `agent_runs[]`
  - forbidden: `current_gate` (only Ready Agent may write gate)

### Offline PDF / web_search switch

- `evidence_acquire.emission.mode`:
  - `offline_pdf` (current default and milestone lane)
  - `web_search` (slot; non-blocking for this refactor)
- `offline_pdf` path:
  - MinerU parse -> LLM structured extraction -> candidate staging -> target field writeback to case (policy-gated)
- `web_search` path:
  - candidate-only unless strict citations/provenance are stable

### Guardrails locked for this refactor

- Ready Agent is the only writer for `current_gate` and `action_rationale`.
- Evidence table is **read-only** in this refactor (no writeback).
- Every agent step must append an `agent_runs[]` audit row with `inputs_hash` + `idempotency_key`.
- Replay artifacts are mandatory per run/step for deterministic audit.

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
  - Current implementation pattern (P4-pre two-stage):
    1) Stage A: Gemini native `generateContent + google_search` to collect grounding sources (then resolve redirect URLs to final landing URLs)
    2) Stage B: `Responses without tools` to structure papers only from Stage A sources
  - Source policy (Stage A → Stage B forwarding):
    - Default: `publisher_article_only_v1` (hard filter for publisher/article-like sources; blocks common noise hosts/pages while preserving trust boundary).
    - Optional: `allow_all` (no allowlist filtering; useful for debugging/recall checks).
    - Optional: `journal_allowlist_v1` (curated allowlist for drift reduction when strict venue constraints are desired):
      - Advanced Functional Materials (`advanced.onlinelibrary.wiley.com` / journal `16163028`)
      - Advanced Materials (`advanced.onlinelibrary.wiley.com` / journal `15214095`)
      - Materials Future (`iopscience.iop.org/journal/2752-5724`)
      - ACS Nano (`pubs.acs.org/journal/ancac3`)
      - Nature Materials (`nature.com/nmat`)
  - Trust boundary: Stage B may only emit candidate items whose `source_url` maps to Stage A sources (exact URL or normalized same-origin match). If source mapping fails, candidate is dropped.
  - Gate: when `sources=0` or `papers_out=0`, emit empty candidates and route next action to wetlab/min-experiment path; no EvidenceClaim writeback.
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

## Multi-Agent Architecture (V1)

### Why lightweight orchestrator first (not LangChain-first)
- We prioritize a minimal, explicit control loop over a framework-heavy stack to keep case-file semantics and writeback rules deterministic.
- Current V1 needs are narrow and auditable: read case → select actions → run bounded agents → write patch + artifacts + run logs.
- This keeps migration cost low while the literature lane is still unstable at the citation/provenance boundary.
- Migration to LangGraph/Temporal is enabled later when one of these conditions is met:
  - multi-tenant scheduling and retries become operational bottlenecks,
  - cross-run durable checkpoints and human-in-the-loop workflows need first-class orchestration,
  - agent graph branching/parallel fan-out exceeds current explicit policy loop complexity.

### Orchestrator responsibilities
- Read `cases/{case_id}.json` as the single source of truth.
- Interpret `current_gate`, `reasoning_mode`, and structured `action_plan` to decide execution order and stop conditions.
- Invoke agent plugins through a registry, pass normalized inputs, collect outputs.
- Apply validated patch writes back to case file, persist artifacts/raw dumps, append `agent_runs[]`.
- Emit run-level summary for reports/post-UQ handoff (no direct mechanism/hypothesis node writeback in V1).

### Agent I/O contract (uniform)
- **Input**: `{case, context, runtime_config}`.
  - `case`: full case file snapshot at dispatch time.
  - `context`: retrieval/evidence payloads (graph subgraph, aTB pack, literature candidates).
  - `runtime_config`: deterministic knobs (timeouts, budgets, mode strict/relaxed/off).
- **Output**: `{patch, artifacts, warnings, metrics, status}`.
  - `patch`: JSON-merge style delta for case file fields only.
  - `artifacts`: list of produced files (path + type + checksum optional).
  - `warnings`: non-fatal quality/degradation notes.
  - `metrics`: counters/timing/token-usage (optional).
  - `status`: `success|partial|failed|skipped`.

### Writeback mechanism and policy gates
- V1 write targets: case file always; evidence table only under policy gate.
- `evidence_writeback_policy`:
  - `strict`: requires provenance-complete citations/sources and locator evidence before `literature_claim` writeback.
  - `relaxed`: candidate-only flow; update case literature candidates but do NOT write to `evidence_table`.
  - `off`: no evidence-table writeback regardless of available evidence.
- Required audit fields on all write-capable outputs: `provenance`, `confidence`, `timestamp`, `raw_dump_path` (or equivalent raw reference).

### Literature degradation strategy (agent slot)
- When source passthrough is unstable or citations are insufficient:
  - keep outputs in case as `literature.candidates` with unverified markers,
  - do not write `evidence_table` claims,
  - route next action to wetlab/minimum-experiment or manual curation path.
- Literature deep-search and MinerU extraction stay as a parallel agent workstream; orchestrator keeps a stable slot/interface.

### Suggested repository layout (doc-level blueprint)
- `src/core/`
  - `patching.py`: RFC6902 apply + whitelist/append-only enforcement
  - `hashing.py`: stable input/output hash helpers
  - `artifacts.py`: replay bundle writer + manifest
  - `io.py`: case/artifact read-write helpers
- `src/orchestration/`
  - `orchestrator.py`: deterministic multi-agent loop runner
  - `registry.py`: agent construction and ordering
  - `policies.py`: gate-aware branching policy (reasoning conditional)
  - `run_one.py`: single command entrypoint for one sample
- `src/agents/`
  - `DataAgent` (structural priors only)
  - `ChemAgent` (aTB + offline_pdf extraction + staging/writeback)
  - `ReadyAgent` (gate owner)
  - `ReasoningAgent` (LLM master; stub fallback allowed)
  - `JudgeAgent` (post-UQ critique + suggestions)
- `src/tools/`
  - `llm_client.py`: OpenAI-compatible Responses wrapper
  - `mineru_runner.py`: MinerU resolve/run adapter

### Agent input/output/degradation map
- `case_builder`
  - Input: `query.smiles`, optional aliases/code from test row
  - Output: base case file fields (`query`, baseline `evidence_readiness`, initial `action_plan`)
  - Degrade: invalid SMILES -> `current_gate.blocked` + warning + no downstream reasoning
- `graph_retriever`
  - Input: `query.inchikey`, retrieval budgets
  - Output: subgraph context artifact + case context refs
  - Degrade: missing inchikey/graph miss -> empty subgraph + warning
- `atb_pack`
  - Input: `query.inchikey`, neighbor list, atb cache/tables
  - Output: target/neighbor aTB summaries, `risk_scores.atb_neighbor_consistency`
  - Degrade: `cache_status` absent/failed/partial -> write status and block/route actions per policy
- `literature_scout` (slot)
  - Input: inchikey + aliases + writeback policy
  - Output: candidates or verified sources, raw dumps, warnings
  - Degrade: sources unstable -> candidate-only writeback in case; never evidence table in relaxed mode
- `mineru_extractor` (slot)
  - Input: verified/landing URLs or PDFs
  - Output: extracted structured claims artifact, optional strict writeback-ready payload
  - Degrade: missing PDF/locator -> warning + skip
- `master_reasoner`
  - Input: case + assembled context (graph, aTB, literature candidate/verified status)
  - Output: `master_output` artifact and case summary patch
  - Degrade: if blocked gate -> skip and write rationale
- `post_uq_reviewer`
  - Input: case + master output + evidence coverage/conflict signals
  - Output: updated `current_gate`, `action_plan`, `post_uq` fields
  - Degrade: insufficient evidence -> conservative/blocked mode with explicit next actions

### READY_AGENT (independent readiness controller)
- Role:
  - Run after Data Agent / Chem Agent updates.
  - Read full case JSON and be the only writer of:
    - `current_gate`
    - `action_rationale`
    - `action_plan`
    - optional `risk_scores.readiness_*`
- Hard write scope:
  - RFC6902 patch paths limited to:
    - `/current_gate/*`
    - `/action_rationale`
    - `/action_plan`
    - `/risk_scores/readiness_*`
  - READY_AGENT must not write `target_fields` or other evidence payload fields.
- Gate states:
  - `blocked_input_missing`
  - `needs_manual`
  - `ready_for_reasoning`
  - `ready_conservative`
- Decision checks (ordered):
  1) Emission availability + required provenance (`source_ref`, `source_locator`, `confidence`)
  2) Anti-leakage for `emission_aggr_nm` (must include aggregation condition signal)
  3) Identity metadata (`identity_match`, `identity_match_confidence`, optional `matched_entity_in_paper`)
  4) Rationale/gate consistency
  5) aTB downgrade logic (aTB failure -> `ready_conservative` if emission evidence is otherwise sufficient)
- Action-plan policy:
  - `blocked_input_missing` -> `request_manual_pdf` (blocking)
  - extraction/manual gap -> `rerun_offline_pdf_extractor` or `manual_extract` (blocking)
  - identity uncertainty -> `manual_identity_verify_from_pdf`
  - ready states -> priority=1 `run_master_reasoner` / `run_master_reasoner_stub`
  - aTB failures in ready states -> queue `retry_target_atb`
- CLI integration (current):
  - `case`, `case-update`, `case-e0`, and `case-e2e` all invoke READY_AGENT after primary agent writes.
  - READY_AGENT is the final writer for `current_gate`, `action_rationale`, and `action_plan`.

### Single-sample execution plan (test.csv, aTB-success lane)
- Target: run one `test.csv` sample end-to-end with **Example A-first E0**:
  `offline_pdf -> patch + staging + replay`, while `master/post-uq` remain stubs.
- Steps:
  1) Create case from sample SMILES (`create_case_from_smiles`).
  2) Run E0 runner with `mode=offline_pdf`.
  3) Parse offline extraction payloads, normalize candidates, and stage them in case.
  4) Select deterministic writeback candidates for emission fields.
  5) Apply RFC6902 patch to case, append `agent_runs[]`, write replay artifacts.
  6) Run `master_reasoner_stub` + `post_uq_stub` (accounting only).
  7) Preserve append-only `history[]` events for each E0 agent write (`offline_pdf_emission_agent`, `master_reasoner_stub`, `post_uq_stub`).
- Acceptance output:
  - final case file with complete `agent_runs[]` lineage,
  - append-only `history[]` with per-agent write events,
  - `evidence_candidates_staging[]` populated,
  - `artifacts/{run_id}/00..06 + manifest.json`,
  - no `evidence_table` writeback.

### One-shot command (SMILES/code -> case build -> E0)
- Added CLI entrypoint `python -m src.cli case-e2e` to run the minimal end-to-end lane in one command:
  - resolve input (`--smiles` directly or `--code` from `data/test.csv`)
  - create base case (`create_case_from_smiles`)
  - snapshot `before_case_e0`
  - run E0 writeback (`sidecar_only` or `mineru_llm`)
  - emit compact JSON summary with before/after case paths + run artifact location
- This command is the default operator path for “single-line run-through” validation.
- Operator output modes:
  - `--artifact-mode full` (debug): keep `artifacts/{run_id}/00..06 + manifest`.
  - `--artifact-mode final_case_only` (default for clean runs): keep only two artifacts:
    - compact reasoning-only final case file (`cases/.../{case_id}.json`)
    - stable run log (`artifacts/.../{case_id}.run_log.json`) including LLM request/response trace.

### Implementation phases (reordered: Example A-first)
- **E0 — Patch + Staging + Replay (current milestone)**
  - Deliverables: minimal linear runner, RFC6902 patch output, full replay artifacts, idempotency, 4-state gate.
  - Scope: offline_pdf lane only; `master/post-uq` stubs only.
  - Hard guard: evidence-table writeback disabled.
- **E1 — Stability hardening**
  - Deliverables: deterministic reruns, stronger failure categorization, replay verification utilities.
  - Scope: keep linear runner; no registry/policy abstraction yet.
- **E2 — Abstraction after closure stability**
  - Deliverables: extract registry/policy/types from proven E0/E1 behavior.
  - Scope: preserve behavior while refactoring control flow.

### E0 output contract (locked)
- `agent_patch` (RFC6902): `artifacts/{run_id}/03_agent_patch.json`
  - Format: list of `{op,path,value}`.
  - Allowed ops: `add | replace | test`.
  - Allowed paths:
    - `/target_fields/emission_aggr_nm`
    - `/target_fields/emission_solid_or_film_nm`
    - `/target_fields_provenance/*`
    - `/evidence_acquire/emission/*`
    - `/evidence_candidates_staging/-` (append-only)
    - `/agent_runs/-` (append-only)
    - `/history/-` (append-only)
    - `/current_gate/*`
    - `/reasons/-`
    - `/next_actions/-`
- `evidence_candidates_staging[]` in case file is the canonical E0 candidate staging store.
- Replay artifacts directory:
  - `00_input_snapshot.json`
  - `01_extractor_raw.json`
  - `02_candidates_normalized.json`
  - `03_agent_patch.json`
  - `04_case_before.json`
  - `05_case_after.json`
  - `06_case_diff.json`
  - `manifest.json` (sha256 of each artifact)
- Stable run log (always emitted, independent of replay mode):
  - `{artifacts_dir}/{case_id}.run_log.json`
  - Includes: run metadata, gate result, reason codes, selected writeback fields, extractor diagnostics, and LLM request/response capture.
- State synchronization contract:
  - E0 must keep top-level and nested readiness state aligned:
    - `current_gate.*` <-> `evidence_readiness.current_gate.*`
    - `evidence_readiness.literature.status/sources/last_update/notes` reflect E0 extraction outcome.
- `inputs_hash`: sha256 of canonical bytes of `00_input_snapshot.json`; copied into every `agent_runs[].inputs_hash` for replay linkage.

### E0 gate state machine (locked)
- `blocked_input_missing`
  - Trigger: `mode=offline_pdf` and no valid `inputs.offline_pdfs`.
- `failed_extract`
  - Trigger: extractor/parsing exception (timeout, malformed output, missing extractor payload).
- `extracted_no_writeback`
  - Trigger: extraction succeeded but no writeback-eligible fields:
    - no candidates, or
    - all candidates rejected, or
    - candidates missing required provenance / identity constraints.
- `ready_for_reasoning`
  - Trigger:
    - `strictness=strict`: at least one verified target field is written with complete provenance.
    - `strictness=relaxed`: at least one non-rejected target field is written with minimal provenance (`source_locator` required; page may be missing).
- Parallel append-only diagnostics:
  - `reasons[]`: machine-readable reason codes
  - `next_actions[]`: suggested actions (`request_manual_pdf`, `manual_extract`, `run_master_reasoner_stub`)

### E0 idempotency key (locked)
- Definition:
  - `idempotency_key = sha256(canonical_json(key_material))`
- `key_material` includes:
  - `case_id`
  - `mode`
  - sorted `offline_pdfs[]` (`path_or_id + sha256`)
  - `extractor_name`, `extractor_version`
  - `extractor_config_hash`
  - `normalizer_config_hash`
  - `mapping_version`
  - `pdf_page_selection_hash` (empty string if not used)
- Behavior:
  - If key unchanged and `--force` is not set:
    - skip extraction/writeback/staging updates,
    - append only `agent_runs` with `status=skipped`.

### E0 field mapping and deterministic selection (locked)
- Targets:
  - `emission_aggr_nm`
  - `emission_solid_or_film_nm`
- Candidate filtering:
  - non-nm and non-convertible units -> rejected
  - invalid numeric value -> rejected
  - `identity_match=unmatched` -> rejected
  - `emission_aggr_nm` accepts only explicit aggregate context (anti-leakage)
- Writeback eligibility:
  - `strict`: only `verification_status=verified`.
  - `relaxed` (default): allow non-rejected candidates for case-file writeback when locator exists, even if page is unavailable.
- `emission_solid_or_film_nm` deterministic tie-break:
  1) `verification_status=verified`
  2) `value_source_kind`: `table > figure > text`
  3) condition priority: `film > solid/powder > crystal`
  4) confidence (desc)
  5) page (asc)
  6) `candidate_id` (lexicographic)

### E0 offline_pdf verification semantics (locked)
- `verified` in offline_pdf mode means:
  - locator is present; page is preferred but not mandatory when locator is clearly structured (`Table`/`Fig`/`Figure`),
  - value parsed to nm,
  - condition mapped to target field,
  - identity is not unmatched.
- This is independent from external citations/web-search provenance.

### E0 writeback boundary (hard guard)
- `WRITEBACK_EVIDENCE_TABLE = false` in E0 runner.
- Any request to enable evidence-table writeback must fail fast.
- Tests must assert evidence-table no-touch behavior.

### Execution-ready criteria (E0)
- Patch whitelist/ops are enforced and fail fast on violations.
- Gate transitions are deterministic for all failure/success states.
- Idempotent rerun does not duplicate staging/field writeback.
- Replay artifacts are complete and hash-linked.

### Emission Evidence Acquisition Mode (offline_pdf vs web_search)

#### Config switch
- `evidence_acquire.emission.mode`
  - Allowed: `offline_pdf` | `web_search`
  - Default (current phase): `offline_pdf`
- `evidence_acquire.emission.strictness`
  - Allowed: `strict` | `relaxed`
  - Default (current phase): `relaxed`

#### `offline_pdf` mode (current unblocker lane)
- Input:
  - `case.query.inchikey` / `case.query.smiles`
  - `case.inputs.offline_pdfs[]` (local path or file id)
- Flow:
  - PDF -> `mineru_extract_pdf` -> extraction JSON -> emission field mapper -> case file patch
  - Target writeback fields:
    - `target_fields.emission_aggr_nm`
    - `target_fields.emission_solid_or_film_nm`
    - `target_fields_provenance.*` (source type, locator, confidence, timestamp, raw dump ref)
- Degradation:
  - If PDF parse/extraction fails, write:
    - `evidence_readiness.literature.status="failed_offline_pdf"`
  - Add actions:
    - `request_manual_pdf`
    - `manual_extract`

#### `web_search` mode (agent slot; parallel workstream)
- Input:
  - `inchikey` + aliases from case file
- Output:
  - candidate papers list (`doi/title/url/pdf_url/source_type/source_ref`)
- Current status:
  - Implemented as interface/policy slot in this repo.
  - Search/deep extraction stability is owned by teammate workstream.
  - In unstable citation/source passthrough conditions, remain in candidate-only behavior.

#### Orchestrator switch/fallback rules
- If `mode=offline_pdf` and `inputs.offline_pdfs` is empty:
  - do not run extractor;
  - enqueue `request_manual_pdf`.
- If `mode=web_search` and web-search agent unavailable/fails:
  - fallback to `offline_pdf` when offline PDFs are provided;
  - otherwise enqueue `request_manual_pdf` (and optional manual_extract).

#### Writeback policy for emission completion
- `offline_pdf` lane:
  - Stable human-provided PDF input allows strict case-file writeback of emission fields.
  - EvidenceClaim writeback is allowed only under V1 guardrail (EvidenceClaim only, no mechanism/hypothesis node writeback) and strict provenance completeness.
- `web_search` lane (current unstable period):
  - Use `relaxed` behavior by default.
  - Write only candidates to case file (`literature.candidates` / `evidence_readiness.literature.candidates`).
  - Do not write `evidence_table` until strict provenance requirements are met.
- `strictness` interpretation:
  - `strict`: must trace to PDF/table/page/section locator.
  - `relaxed`: candidate-only case updates; no evidence-table writeback.

#### Single-sample text data flow (Example A)
- `test.csv` sample
  -> `create_case_from_smiles`
  -> (`evidence_acquire.emission.mode` switch)
  -> `mineru_extract_pdf` (offline_pdf lane)
  -> `emission_extract`
  -> `update_case_file` (target_fields + provenance + agent_runs)
  -> `ready_for_reasoning`
  -> `master_reasoner`

#### Example A minimal case fields (offline_pdf lane)
```json
{
  "query": {"smiles": "...", "inchikey": "..."},
  "inputs": {
    "offline_pdfs": [
      {"path_or_id": "inputs/papers/sample.pdf", "sha256": "...", "provided_by": "human"}
    ]
  },
  "evidence_acquire": {"emission": {"mode": "offline_pdf", "strictness": "strict"}},
  "target_fields": {
    "emission_aggr_nm": 621.0,
    "emission_solid_or_film_nm": 651.0
  },
  "target_fields_provenance": {
    "emission_aggr_nm": {"source_type": "offline_pdf", "source_locator": "Table 1, p2", "confidence": 0.94},
    "emission_solid_or_film_nm": {"source_type": "offline_pdf", "source_locator": "Table 1, p2", "confidence": 0.91}
  }
}
```
## 2026-02 Addendum: Emission Evidence Acquisition Mode (offline_pdf vs web_search)

This addendum defines a switch in the Multi-Agent / Case File flow for emission evidence acquisition. It is intentionally doc-only and architecture-level.

### Config switch

- `evidence_acquire.emission.mode` allowed values: `offline_pdf | web_search`
- Default (current phase): `offline_pdf`
- `evidence_acquire.emission.strictness` allowed values: `strict | relaxed`
- Current default strictness: `relaxed` for web-search-derived candidates; `strict` can be used for verified offline PDF extraction.

### Mode behavior

#### 1) `offline_pdf` (current unblocker path)

Input:
- `case.inchikey` / `case.smiles`
- `case.inputs.offline_pdfs[]` (local path or file id)

Flow:
- PDF -> MinerU (or equivalent parser) -> extraction JSON -> case-file patch
- Write emission targets into case file:
  - `target_fields.emission_aggr_nm`
  - `target_fields.emission_solid_or_film_nm`
- Write provenance into:
  - `target_fields_provenance.*` with `source_type=offline_pdf`, locator (page/table/section), confidence, timestamp

Degrade-on-failure:
- If PDF parse/extract fails, write:
  - `case.evidence_readiness.literature.status = "failed_offline_pdf"`
- Append action items:
  - `request_manual_pdf` and/or `manual_extract`

#### 2) `web_search` (agent slot, parallel track)

Input:
- `inchikey` + aliases from case file

Output:
- Candidate papers list (`literature.candidates[]`)

Current state:
- Online search chain works, but citations/sources pass-through is not consistently stable.
- Therefore the repository keeps this as an interface + policy slot.
- This path must not block the offline PDF lane.

### Orchestrator mode-switch policy

- If `mode=offline_pdf` and `inputs.offline_pdfs` is empty:
  - do not run extraction
  - set action plan to include `request_manual_pdf`
- If `mode=web_search` and web-search agent fails/unavailable:
  - fallback to `offline_pdf` when PDF input exists
  - otherwise request manual path (`request_manual_pdf` / `manual_extract`)

### Strict/relaxed writeback policy

- `strict`:
  - requires traceable evidence (PDF + page/table/section or equivalent locator)
  - allows strict emission writeback to case file
  - EvidenceClaim writeback is allowed under V1 guardrail (EvidenceClaim-only; no mechanism/hypothesis writeback)
- `relaxed`:
  - allows candidate accumulation in `literature.candidates[]`
  - does not write to `evidence_table` until strict traceability is satisfied

### Single-sample textual data flow (Example A lane)

`test.csv sample -> create_case_from_smiles -> mode switch -> mineru_extract_pdf -> emission_extract -> update_case_file -> ready_for_reasoning -> master_reasoner`

### Example A: offline_pdf single-sample minimal fields

```yaml
case_id: "case_test_001"
inputs:
  smiles: "..."
  offline_pdfs:
    - path_or_id: "data/manual_pdfs/sample_001.pdf"
      sha256: "optional_hash"
evidence_acquire:
  emission:
    mode: "offline_pdf"
    strictness: "strict"
target_fields:
  emission_aggr_nm: 523.0
  emission_solid_or_film_nm: 548.0
target_fields_provenance:
  emission_aggr_nm:
    source_type: "offline_pdf"
    source_locator: "Table 1, p.2"
    confidence: 0.92
  emission_solid_or_film_nm:
    source_type: "offline_pdf"
    source_locator: "Figure 3 caption, p.4"
    confidence: 0.88
evidence_readiness:
  literature:
    status: "verified"
```
