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
See `CLAUDE.md` § "V0 Acceptance Criteria" for the authoritative checklist.

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

**Deliverables**
- `data/evidence_table.parquet` with provenance fields
- Graph layer (networkx/neo4j-lite) linking evidence to molecules and conditions
- Updated reports: include evidence provenance IDs and conditions
- V1 deliverable is a provenance-first Evidence Graph; GraphRAG context retrieval returns evidence-backed subgraphs.

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
- V0 status: completed (P1–P6/P7 delivered; P2 cache integrated; P3b X_full built; structure-only anchors)
- Current milestone: V1-P4-pre + train-only facts migration (facts DB refresh to `train.csv` schema)
- Blockers: Literature evidence chain (gateway does not reliably pass through citations/sources; strict evidence writeback is blocked)
  - Literature search tool (web_search / deep research / external gateway): call chain works, but citations/sources passthrough is limited or unstable (strict provenance blocked)
  - MinerU: depends on obtaining PDF / landing URLs (blocked by the above)
- Notes:
  - aTB status in this repo: DONE (full cache available; readiness uses cache_status + keyfield completeness + neighborhood consistency)
  - Data refresh track: private facts are train-only; test set is non-fact evaluation input
  - V1 planning started; V0 process archived in doc/process_v0.md
