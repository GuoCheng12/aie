# V1 Detailed Plan (CURRENT)

## Goal
Build the Evidence Layer + Light KG + Chem Agent literature evidence loop, while keeping SMILES-first case file as the shared artifact.

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
- **V1-P4**: Chem Agent literature loop (InChIKey/common name search via DeepSearch; MinerU extraction → EvidenceClaim rows)
  - Input: Case File (inchikey + alias/common names + candidate mechanisms) OR direct (inchikey + alias list)
  - Output: append `literature_claim` rows to `evidence_table` (DOI/source + extracted values + conditions + confidence);
    update Case File `literature.status` and `literature.sources` list
  - Retrieval keys (initial): InChIKey + common name/alias (no SMILES search as primary)
- **V1-P5**: Reports reference evidence IDs + provenance; add writeback for EvidenceClaim (not new mechanisms yet)

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

## Risks & Guardrails
- **Provenance**: every EvidenceClaim must include source, timestamp, and extraction method.
- **Condition mismatch**: keep measurement conditions explicit; avoid merging incompatible conditions.
- **No overwrites**: evidence appends only; never mutate private_clean or experimental facts.
- **Traceability**: all graph edges must map back to evidence_table row IDs.
