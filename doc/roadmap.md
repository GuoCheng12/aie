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
- Unit normalization (qy/tau/emission fields) — see `doc/process.md` P1 for rules
- aTB stage failures and resumability — see `doc/process.md` P2 for failure policy
- Missing values and schema drift — see `doc/schemas.md` for column definitions

---

### V1 — Evidence table + light graph (traceable provenance)
**Goal**
Make hypotheses and decisions traceable to evidence.
Add:
- Evidence table with source, conditions, and weights
- Light graph connections: Molecule ↔ Evidence ↔ MechanismTemplate/Prototype
- Retrieval for reasoning: return relevant evidence snippets and structured conditions

**Deliverables**
- `data/evidence_table.parquet` with provenance fields
- Graph layer (networkx/neo4j-lite) linking evidence to molecules and templates
- Updated reports: include evidence provenance IDs and conditions

**UQ changes**
- Coverage includes evidence mass/quality (not just feature-space density)
- Aleatoric includes evidence conflict signals (same mechanism contradicted under similar conditions)

---

### V2 — Full Domain KG + GraphRAG + dynamic write-back evolution
**Goal**
Introduce a domain knowledge graph as explicit external memory:
- Retrieve nearest mechanism subgraph for a query molecule
- Use subgraph as ICL anchor for explanation generation
- When uncertainty indicates novelty, write back a hypothesis node/branch with strict provenance and status transitions

**Deliverables**
- Knowledge graph storage + retrieval (GraphRAG)
- Subgraph retrieval API returning structured triples + citations
- Hypothesis lifecycle: `hypothesis → candidate → validated/refuted` with governance

**UQ changes**
- Novelty decisions consider both feature-space OOD and graph-space coverage gaps
- Stronger “safety gates” against hallucination: no new branch without evidence and/or high-fidelity computation triggers

---

## Milestone tracking (update periodically)
- Current version: V0
- Current milestone: (fill in)
- Next milestone: (fill in)
- Blockers: (fill in)
- Notes: (fill in)
