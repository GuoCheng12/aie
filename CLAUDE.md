# CLAUDE.md

## Project: Uncertainty-aware AIE Mechanism Discovery (V0 → V1 → V2)

### Why this repo exists
We are building an uncertainty-aware, multi-agent system to automate mechanistic hypothesis generation for AIE (Aggregation-Induced Emission) molecules.

We have a private dataset (~1000+ molecules) with rich experimental observables but often **without reliable mechanism labels**. We will use:
- **Data Agent**: fetch/standardize private dataset records
- **Chem Agent**: compute micro-physical descriptors using our aTB pipeline (S0/S1 geometry, charges, volumes, etc.)
- **Reasoner** (later): generate explainable hypotheses using structured evidence (LLM is used for explanation, not for scoring)
- **UQ Router**: compute coverage/aleatoric/novelty and decide next actions (e.g., request more computation / mark novelty candidate)

V0 focuses on a **working closed-loop pipeline** (data → features → UQ → router → report/log), not a full GraphRAG KG.

---

## Versions Overview

### V0 (Current): Feature-space anchors + UQ Router (NO full KG required)
Core deliverable:
- Ingest private dataset + standardize units/types + canonicalize SMILES
- Run aTB on molecules (batch + caching) to produce structured descriptors
- Merge experimental + RDKit + aTB features into a unified table
- Compute UQ scores:
  - **coverage**: closeness to an anchor reference space + metadata completeness
  - **novelty**: outlierness in the anchor feature space
  - **aleatoric**: “intrinsic ambiguity” measured by entropy of soft assignment to prototypes (unsupervised) OR a weakly supervised mechanism scorer if available
- Router returns one of:
  - Known/Stable
  - In-domain ambiguous
  - Evidence-insufficient
  - Novelty-candidate
- Generate `report.json` per molecule and an auditable `hypothesis_log` for novelty candidates.

### V1: Evidence table + light graph (traceable provenance)
Add an evidence layer (Paper/DB snippets, conditions, weights). Router + reasoning can cite provenance.

### V2: Full Domain KG + GraphRAG + dynamic write-back evolution
Mechanism subgraph retrieval as explicit memory; new hypotheses create new branches with provenance + status transitions.

---

## Non-negotiable design principles
1. **Do NOT use LLM self-confidence as the main uncertainty signal.**
   UQ must be derived from structured data: experimental observables, aTB descriptors, and anchor-space density.
2. **Everything must be auditable.**
   Store inputs, versions, computed descriptors, failures, and router decisions.
3. **Cache by InChIKey.**
   aTB is expensive; never recompute the same molecule unless explicitly requested.
4. **Handle missingness explicitly.**
   Missing values must not silently become zeros. Add missing indicators (`{field}_missing` columns).
5. **Units must be standardized** (qy in [0,1] from %, tau in ns, emission/absorption in nm).
6. **Keep V0 conservative**: do not declare "new mechanism" unless novelty is high AND (coverage is low OR aleatoric is high). Mark as `hypothesis`.
7. **Version all pipeline runs.**
   Store in `data/run_manifest.json`: git commit hash (or "untracked"), Python version, RDKit version, aTB version, timestamp, encoding used, input/output counts.

---

## Known gotchas / lessons (add more as discovered)
- aTB integration pitfall: avoid using global `args` inside helpers; always pass args explicitly (e.g., volume computation and workdir usage). Record stage failures.
- aTB can fail at different stages (`opt`, `excit`, `neb`, `volume`, `feature_parse`). Failures must be recorded with `fail_stage` and routed to `Evidence-insufficient` with recommended next steps (retry / different initial conformer / skip NEB / etc.).
- Amesp can segfault on large molecules (error code -11); consider size-based skipping or reduced parallelism for stability.

---

## V0 Acceptance Criteria (Definition of Done)
At minimum:
1. `data/private_clean.parquet` exists with standardized types/units + missing masks.
2. `data/molecule_table.parquet` exists with canonical SMILES + InChIKey de-dup.
3. `data/rdkit_features.parquet` exists.
4. `data/atb_features.parquet` exists (per InChIKey) with:
   - run_status + fail_stage
   - S0/S1 structure stats + volume + HOMO-LUMO gap + charge metrics (at least summarized)
   - delta features (S1 - S0)
5. `data/X_full.parquet` exists (merged experimental + RDKit + aTB).
6. `data/uq_scores.parquet` exists with coverage/aleatoric/novelty + router_action (+ recommended_next_steps).
7. Per-molecule `reports/{id}.json` can be generated.
8. `data/hypothesis_log.jsonl|parquet` records novelty candidates with provenance:
   - id/inchikey, scores, neighbors, key descriptors, suggested next actions.

---

## Documentation workflow rules (IMPORTANT)

We intentionally separate **long-term roadmap**, **current version detailed plan**, and **implementation logs** to avoid re-reading large documents every time.

### Files and their roles
- `doc/roadmap.md`: **High-level plan** across V0/V1/V2 (stable, low-frequency edits).
- `doc/process.md`: **Detailed plan for the CURRENT version only** (e.g., V0 right now). This is the active planning doc.
- `doc/process_summary.md`: **Chronological implementation log** (what changed, results, surprises, failures).
- (Optional) `doc/status.md`: Short “current state” dashboard (current milestone, blockers, next tasks).

### Required update protocol
For every meaningful coding task:
1. **BEFORE coding**: Update `doc/process.md` with the concrete plan (scope, steps, expected outputs).
2. **AFTER coding**: Update `doc/process_summary.md` with what happened (outputs, metrics, bugs, deviations).
3. If a **major/important lesson** is learned, add it to `CLAUDE.md` under “Known gotchas / lessons”.

### Version transitions (archiving)
When we move from one version to the next:
- Archive the old detailed plan:
  - Rename `doc/process.md` → `doc/process_v0.md` (or `process_v1.md`, etc.)
- Create a new `doc/process.md` for the next version’s detailed plan.
- Do **not** delete or rewrite old archived plans; treat them as read-only history.

### How to talk to Claude Code (doc scoping)
When requesting work, explicitly reference the docs to read using the `@` syntax:
- Always include `@CLAUDE.md @doc/roadmap.md @doc/process.md`
- For debugging, also include `@doc/process_summary.md`

---

## Repo conventions (for Claude Code)
- Always follow the documentation protocol:
  - Update `doc/process.md` before coding
  - Update `doc/process_summary.md` after coding
  - Add major lessons to `CLAUDE.md`
- Prefer readable, modular code with clear I/O contracts (JSON schema).
- Add minimal tests for parsing/standardization and for UQ calculations (unit tests).
- All scripts must be runnable from CLI with clear args; no hard-coded paths.
- Prefer deterministic, resumable batch execution (caching, checkpoints).

---

## Suggested initial directory structure
├── src/
│ ├── data/ # parsing + standardization
│ ├── chem/ # aTB wrapper + parsing outputs
│ ├── features/ # RDKit + merge + scaling
│ ├── uq/ # coverage/novelty/aleatoric + router
│ ├── reports/ # report generator
│ └── utils/ # logging, schemas, helpers
├── data/ # generated parquet/jsonl, ignored in git if large
├── reports/ # per-molecule outputs
└── doc/
├── process.md
└── process_summary.md

---

## Notes on privacy
- Private dataset fields and values must not be logged in plaintext beyond what is necessary for debugging.
- Hypothesis logs should include IDs/inchikey, not sensitive “comment” content unless explicitly approved.
