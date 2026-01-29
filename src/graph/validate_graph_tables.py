"""
src/graph/validate_graph_tables.py

Validator for V1 Light KG tables:
- data/graph_nodes.parquet
- data/graph_edges.parquet

Checks:
- node_id uniqueness
- all edges refer to existing nodes
- rel_type allowed
- evidence edges have evidence_id and refer to an Evidence node
- UNDER_CONDITION dst is a Condition node
- SIMILAR_TO weight in [0,1] and connects Molecule nodes

Usage:
    python -m src.graph.validate_graph_tables
"""

import argparse
from typing import Any, Dict, List, Set

import numpy as np
import pandas as pd

from src.utils.logging import get_logger


logger = get_logger(__name__)


NODE_COLS = ["node_id", "node_type", "key", "props_json"]
EDGE_COLS = ["src_id", "rel_type", "dst_id", "weight", "evidence_id", "props_json"]

NODE_TYPES: Set[str] = {"Molecule", "Evidence", "Condition"}
REL_TYPES: Set[str] = {
    "HAS_OBSERVATION",
    "HAS_COMPUTATION",
    "HAS_EVIDENCECLAIM",
    "UNDER_CONDITION",
    "SIMILAR_TO",
}

EVIDENCE_RELS: Set[str] = {"HAS_OBSERVATION", "HAS_COMPUTATION", "HAS_EVIDENCECLAIM", "UNDER_CONDITION"}


def _is_null(x: Any) -> bool:
    if x is None:
        return True
    if isinstance(x, float) and np.isnan(x):
        return True
    return False


def validate(nodes: pd.DataFrame, edges: pd.DataFrame) -> List[str]:
    errors: List[str] = []

    missing_nodes = [c for c in NODE_COLS if c not in nodes.columns]
    missing_edges = [c for c in EDGE_COLS if c not in edges.columns]
    if missing_nodes:
        errors.append(f"Missing node columns: {missing_nodes}")
    if missing_edges:
        errors.append(f"Missing edge columns: {missing_edges}")
    if errors:
        return errors

    # Node checks
    if nodes["node_id"].isna().any():
        errors.append(f"node_id has nulls: {int(nodes['node_id'].isna().sum())}")
    dup = int(nodes["node_id"].duplicated().sum())
    if dup > 0:
        examples = nodes.loc[nodes["node_id"].duplicated(), "node_id"].astype(str).head(5).tolist()
        errors.append(f"node_id has {dup} duplicates (examples={examples})")

    bad_types = sorted(set(nodes["node_type"].dropna()) - NODE_TYPES)
    if bad_types:
        errors.append(f"Invalid node_type values: {bad_types}")

    node_type_map: Dict[str, str] = dict(zip(nodes["node_id"].astype(str), nodes["node_type"].astype(str)))
    node_ids = set(node_type_map.keys())

    # Edge checks
    bad_rels = sorted(set(edges["rel_type"].dropna()) - REL_TYPES)
    if bad_rels:
        errors.append(f"Invalid rel_type values: {bad_rels}")

    src_missing = edges[~edges["src_id"].astype(str).isin(node_ids)]
    if len(src_missing) > 0:
        examples = src_missing["src_id"].astype(str).head(5).tolist()
        errors.append(f"Edges with missing src_id nodes: {len(src_missing)} (examples={examples})")

    dst_missing = edges[~edges["dst_id"].astype(str).isin(node_ids)]
    if len(dst_missing) > 0:
        examples = dst_missing["dst_id"].astype(str).head(5).tolist()
        errors.append(f"Edges with missing dst_id nodes: {len(dst_missing)} (examples={examples})")

    # Evidence edges must have evidence_id and refer to an Evidence node
    evidence_edges = edges[edges["rel_type"].isin(EVIDENCE_RELS)]
    if len(evidence_edges) > 0:
        missing_eid = evidence_edges["evidence_id"].isna() | (evidence_edges["evidence_id"].astype(str).str.strip() == "")
        if missing_eid.any():
            examples = evidence_edges.loc[missing_eid, "rel_type"].astype(str).head(5).tolist()
            errors.append(f"Evidence edges with null/empty evidence_id: {int(missing_eid.sum())} (examples rel_type={examples})")

        # Evidence node existence: ev:{evidence_id}
        ev_node_ids = ("ev:" + evidence_edges["evidence_id"].astype(str)).tolist()
        missing_ev_nodes = [ev for ev in ev_node_ids if ev not in node_ids]
        if missing_ev_nodes:
            errors.append(f"Evidence edges refer to missing Evidence nodes: {len(missing_ev_nodes)} (examples={missing_ev_nodes[:5]})")

        # Structural consistency checks
        has_edges = edges[edges["rel_type"].isin({"HAS_OBSERVATION", "HAS_COMPUTATION", "HAS_EVIDENCECLAIM"})]
        if len(has_edges) > 0:
            bad_dst = has_edges["dst_id"].astype(str) != ("ev:" + has_edges["evidence_id"].astype(str))
            if bad_dst.any():
                examples = has_edges.loc[bad_dst, "dst_id"].astype(str).head(5).tolist()
                errors.append(f"HAS_* edges with dst_id != ev:evidence_id: {int(bad_dst.sum())} (examples dst_id={examples})")

        under = edges[edges["rel_type"] == "UNDER_CONDITION"]
        if len(under) > 0:
            bad_src = under["src_id"].astype(str) != ("ev:" + under["evidence_id"].astype(str))
            if bad_src.any():
                examples = under.loc[bad_src, "src_id"].astype(str).head(5).tolist()
                errors.append(f"UNDER_CONDITION edges with src_id != ev:evidence_id: {int(bad_src.sum())} (examples src_id={examples})")

            # dst must be Condition
            dst_types = under["dst_id"].astype(str).map(lambda nid: node_type_map.get(nid))
            bad_dst_type = dst_types != "Condition"
            if bad_dst_type.any():
                examples = under.loc[bad_dst_type, "dst_id"].astype(str).head(5).tolist()
                errors.append(f"UNDER_CONDITION edges with dst not Condition: {int(bad_dst_type.sum())} (examples dst_id={examples})")

    # SIMILAR_TO constraints
    sim = edges[edges["rel_type"] == "SIMILAR_TO"]
    if len(sim) > 0:
        # evidence_id should be null
        bad_eid = ~(sim["evidence_id"].isna())
        if bad_eid.any():
            examples = sim.loc[bad_eid, "evidence_id"].astype(str).head(5).tolist()
            errors.append(f"SIMILAR_TO edges with non-null evidence_id: {int(bad_eid.sum())} (examples={examples})")

        w = sim["weight"]
        bad_w = w.isna() | (w < 0.0) | (w > 1.0)
        if bad_w.any():
            examples = sim.loc[bad_w, ["src_id", "dst_id", "weight"]].head(5).to_dict(orient="records")
            errors.append(f"SIMILAR_TO edges with invalid weight: {int(bad_w.sum())} (examples={examples})")

        src_types = sim["src_id"].astype(str).map(lambda nid: node_type_map.get(nid))
        dst_types = sim["dst_id"].astype(str).map(lambda nid: node_type_map.get(nid))
        bad_types = (src_types != "Molecule") | (dst_types != "Molecule")
        if bad_types.any():
            examples = sim.loc[bad_types, ["src_id", "dst_id"]].head(5).to_dict(orient="records")
            errors.append(f"SIMILAR_TO edges must connect Molecule nodes: {int(bad_types.sum())} (examples={examples})")

    return errors


def print_summary(nodes: pd.DataFrame, edges: pd.DataFrame) -> None:
    logger.info(f"Nodes: {len(nodes)}")
    logger.info(f"Node counts by type: {nodes['node_type'].value_counts(dropna=False).to_dict()}")
    logger.info(f"Edges: {len(edges)}")
    logger.info(f"Edge counts by rel_type: {edges['rel_type'].value_counts(dropna=False).to_dict()}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate V1 Light KG tables (graph_nodes/graph_edges)")
    parser.add_argument("--nodes", default="data/graph_nodes.parquet")
    parser.add_argument("--edges", default="data/graph_edges.parquet")
    args = parser.parse_args()

    nodes = pd.read_parquet(args.nodes)
    edges = pd.read_parquet(args.edges)
    print_summary(nodes, edges)
    errors = validate(nodes, edges)
    if errors:
        logger.error("VALIDATION FAILED")
        for e in errors[:30]:
            logger.error(f"- {e}")
        raise SystemExit(1)

    logger.info("VALIDATION PASSED")


if __name__ == "__main__":
    main()

