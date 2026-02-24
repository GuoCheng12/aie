# doc/roadmap.md

## Roadmap: Uncertainty-aware AIE Mechanism Discovery

This file is the **high-level, stable plan** across V0/V1/V2.

- Detailed step-by-step execution for the **current version** lives in `doc/process.md`.
- Implementation logs live in `doc/process_summary.md`.
- When we move to the next version, archive the old detailed process into `doc/process_v0.md`, `doc/process_v1.md`, etc.

---

## Big picture goal
We want an uncertainty-aware pipeline for mechanistic hypothesis generation and discovery in AIE.

Key idea: uncertainty is computed primarily from **structured evidence** (experimental observables + computed descriptors + anchor-space density), not from LLM self-confidence.

---

## Versions

### V0 — Closed-loop pipeline without full KG (CURRENT)
**Goal**
Build a working pipeline that:
1) standardizes the private dataset
2) computes RDKit + aTB descriptors (with caching + failure tracking)
3) merges features into a unified table
4) computes UQ scores (coverage/novelty/aleatoric) + conservative router actions
5) generates auditable reports + novelty hypothesis logs

**Core components**
- Data Agent: private dataset → standardized record schema
- Chem Agent: aTB → structured descriptors + QC + delta features
- UQ Router: coverage/novelty/aleatoric → action
- Report generator: per-molecule report + novelty log

**Definition of Done**
See `.codex/AGENTS.md` and archived `doc/process_v0.md` for the authoritative V0 acceptance checklist/history.

**Key risks**
- Unit normalization (qy/tau/emission fields) — see `doc/process_v0.md` P1 for rules
- aTB stage failures and resumability — see `doc/process_v0.md` P2 for failure policy
- Missing values and schema drift — see `doc/schemas.md` for column definitions

---

### V1 — Evidence table + light graph (traceable provenance)
**Goal**
Make hypotheses and decisions traceable to evidence.
Add:
- Evidence table with source, conditions, and weights
- Light graph connections: Molecule ↔ Evidence ↔ Condition
- Retrieval for reasoning: return relevant evidence snippets and structured conditions
- Lightweight Orchestrator + plugin Agents to execute Case-file-driven workflows (single source of truth stays in case file)

**Deliverables**
- `data/evidence_table.parquet` with provenance fields
- Graph layer (networkx/neo4j-lite) linking evidence to molecules and conditions
- Updated reports: include evidence provenance IDs and conditions
- V1 deliverable is a provenance-first Evidence Graph; GraphRAG context retrieval returns evidence-backed subgraphs.
- Orchestrator framework blueprint (`src/orchestrator/*`, `src/agents/*`, `src/artifacts/*`) with auditable `agent_runs` writeback into case file.

**V1 Orchestrator milestones (parallel with P4/P5)**
- **V1-ORCH-P0**: Define orchestrator/agent contracts (case-in, patch/artifact-out, audit fields).
- **V1-ORCH-P1**: Single-sample run loop (`test.csv` aTB-success lane) with deterministic agent ordering.
- **V1-ORCH-P2**: Wire literature agent slot as parallel workstream (owned by teammate), keep strict/relaxed writeback policy gates.
- **V1-ORCH-P3**: Bridge orchestrator outputs to P5 reports and post-UQ review slot.

**UQ changes**
- Coverage includes evidence mass/quality (not just feature-space density)
- Aleatoric includes evidence conflict signals (same mechanism contradicted under similar conditions)

---

### Real-world Constraints (applies to all versions)

**Data incompleteness is normal:**
- Experimental observables are often missing (current train-only facts keep emission_solid/emission_aggr as the minimum private observations)
- Historical / general risk: aTB may fail partially (SCF/OPT/NEB stage failures; large molecules may miss some geometry stats)
- System must support **partial evidence** and degrade gracefully

**Design implication:**
- All pipelines must treat missing data as first-class citizens (not errors)
- UQ and routing must work under incomplete evidence profiles

**Current project status (V0→V1 transition):**
- Facts DB migration in progress: `private_clean.parquet` is being converged to train-only schema from `data/train.csv`; `data/test.csv` is reserved for simulated input/eval only.
- aTB full batch is completed and cached under `cache/atb/`; cache-derived tables exist (`data/atb_qc.parquet`, `data/atb_features.parquet`).
- Case File consumes aTB as evidence/readiness only via `evidence_readiness.atb.cache_status` (and key fields in `features_summary`), plus `risk_scores.atb_neighbor_consistency` as an extra neighborhood-risk signal (structure retrieval unchanged).

---

### V2 — Full Domain KG + GraphRAG + dynamic write-back evolution
**Goal**
Introduce a domain knowledge graph as explicit external memory:
- Retrieve nearest mechanism subgraph for a query molecule
- Use subgraph as ICL anchor for explanation generation
- When uncertainty indicates novelty, write back a hypothesis node/branch with strict provenance and status transitions

**V2 Design (refined)**
- **SMILES-first workflow**: Pre-UQ computable from SMILES alone; no experimental record required for initial assessment
- **Structure-first neighbor retrieval**: Anchor/reference retrieval remains primarily structure-based (ECFP/Tanimoto; later learned embedding) to avoid semantic drift from noisy continuous features
- **Mechanism candidates**: Sourced from neighbor `mechanism_id` distribution PLUS signature/template evidence from offline **domainRAG** store (curated mechanism descriptions, not LLM-mined)
- **Pre-UQ split (Risk + Readiness)**:
  - Risk Scores: top1_sim, novelty_struct, mechanism_entropy (SMILES-computable)
  - Evidence Readiness: target_atb_status, neighbor_atb_coverage, minimal_experiment_available
- **Evidence Ladder**: target aTB → neighbor aTB → literature search → minimal experiment (emission first)
- **Post-UQ Agent (after LLM outputs)**: executed by a dedicated agent that reads (`case_file` + `master_output`) and emits gating + next_actions; avoid hard-coded thresholds; decides finalize / request more evidence / allow write-back

**Deliverables**
- Knowledge graph storage + retrieval (GraphRAG)
- Subgraph retrieval API returning structured triples + citations
- Hypothesis lifecycle: `hypothesis → candidate → validated/refuted` with governance

**UQ changes**
- Novelty decisions consider both feature-space OOD and graph-space coverage gaps
- Stronger “safety gates” against hallucination: no new branch without evidence and/or high-fidelity computation triggers

---

## Milestone tracking (update periodically)
- Current version: V1
- V0 status: completed (P1–P6/P7 delivered; structure-only anchors retained)
- Current milestone: **V1-MA-P1** (multi-agent framework baseline)
  - Deliverable: one-shot orchestrator loop for one `test.csv` sample with auditable patch-based case evolution.
  - Sequence: Data Agent -> Chem Agent -> Ready Agent -> (conditional) Reasoning Agent -> Judge Agent -> Ready Agent.
- Parallel lane:
  - **P4-pre (offline_pdf lane)**: run single-sample emission completion with MinerU + LLM extraction.
  - **P4 (web_search lane, teammate owner)**: stabilize citations/sources passthrough before strict writeback.
- Blockers:
  - web_search strict evidence chain remains unstable (candidate-only acceptable, strict writeback blocked).
  - not blocking MA-P1 because offline_pdf lane is the current unblocker.
- Notes:
  - aTB cache is complete and remains first-choice chem evidence source when available.
  - Case file stays the single mutable artifact; evidence_table remains read-only during this refactor.
  - Ready Agent is the sole gate/action owner (`current_gate`, `action_rationale`, action-plan ordering).
## 2026-02 Addendum: Emission completion lane split (offline_pdf unblocker first)

### Current milestone update (doc-level)

- aTB lane: complete (full run done)
- Literature web-search lane: partial (candidate retrieval available, strict citation/sources writeback still unstable)
- Current unblocker for end-to-end demo: `offline_pdf` mode

### Deliverables (V1/P4 lane refinement)

- **P4-pre E0 (offline_pdf lane; current execution target)**:
  - single-sample end-to-end emission completion from `test.csv`
  - outputs locked to: `agent_patch (RFC6902) + evidence_candidates_staging + replay artifacts`
  - includes 4-state gate, idempotency key with extractor/normalizer config hashes, and no evidence-table writeback
- **P4-pre E1 (offline_pdf lane hardening)**:
  - deterministic rerun behavior, clearer failure buckets, stronger replay verification
- **P4-pre E2 (post-closure abstraction)**:
  - extract registry/policy/types after E0/E1 behavior is stable
- **P4 (web_search lane, parallel teammate track)**:
  - replace/augment offline lane with `web_search + pdf_fetch`
  - gated by stable citations/sources passthrough
  - strict evidence writeback only after traceability is satisfied

### Architecture continuity

- Keep one orchestrator and one case schema.
- Switch only `evidence_acquire.emission.mode` (`offline_pdf` <-> `web_search`) without architecture rewrite.
