"""
src/graph/validate_retrieval.py

V1-P3 validation / smoke for subgraph retrieval (no new artifacts).

Runs retrieval for 3 representative molecules:
1) One with HAS_COMPUTATION evidence (aTB present)
2) One with only HAS_OBSERVATION
3) One random

Usage:
    python -m src.graph.validate_retrieval
"""

import argparse
import random
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

from src.graph.retrieval import ALLOWED_REL_TYPES, get_subgraph
from src.utils.logging import get_logger


logger = get_logger(__name__)


def _node_edge_counts(sg: Dict[str, Any]) -> Tuple[Dict[str, int], Dict[str, int]]:
    node_counts: Dict[str, int] = {}
    for n in sg.get("nodes", []):
        t = n.get("node_type")
        node_counts[t] = node_counts.get(t, 0) + 1
    edge_counts: Dict[str, int] = {}
    for e in sg.get("edges", []):
        r = e.get("rel")
        edge_counts[r] = edge_counts.get(r, 0) + 1
    return node_counts, edge_counts


def _basic_checks(sg: Dict[str, Any], max_nodes: int, max_edges: int) -> List[str]:
    errors: List[str] = []
    nodes = sg.get("nodes", [])
    edges = sg.get("edges", [])
    prov = sg.get("provenance_refs", [])

    if len(nodes) > max_nodes:
        errors.append(f"node budget violated: {len(nodes)} > {max_nodes}")
    if len(edges) > max_edges:
        errors.append(f"edge budget violated: {len(edges)} > {max_edges}")

    node_ids = {n["node_id"] for n in nodes}
    for e in edges:
        if e.get("rel") not in ALLOWED_REL_TYPES:
            errors.append(f"unexpected rel_type: {e.get('rel')}")
        if e.get("src") not in node_ids:
            errors.append(f"dangling edge src: {e.get('src')}")
        if e.get("dst") not in node_ids:
            errors.append(f"dangling edge dst: {e.get('dst')}")

    # If any evidence nodes exist, provenance_refs should not be empty
    has_evidence_node = any(n.get("node_type") == "Evidence" for n in nodes)
    if has_evidence_node and len(prov) == 0:
        errors.append("provenance_refs empty but Evidence nodes present")

    # provenance_refs should be subset of included Evidence node IDs
    ev_ids = {n["node_id"].replace("ev:", "", 1) for n in nodes if n.get("node_type") == "Evidence"}
    prov_bad = sorted(set(prov) - ev_ids)
    if prov_bad:
        errors.append(f"provenance_refs contains ids not in Evidence nodes (examples={prov_bad[:5]})")

    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate V1-P3 subgraph retrieval")
    parser.add_argument("--max_nodes", type=int, default=50)
    parser.add_argument("--max_edges", type=int, default=200)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)

    edges = pd.read_parquet("data/graph_edges.parquet")
    mol_comp = sorted(
        {s.replace("mol:", "", 1) for s in edges.loc[edges["rel_type"] == "HAS_COMPUTATION", "src_id"].astype(str)}
    )
    mol_obs = sorted(
        {s.replace("mol:", "", 1) for s in edges.loc[edges["rel_type"] == "HAS_OBSERVATION", "src_id"].astype(str)}
    )
    mol_only_obs = sorted(set(mol_obs) - set(mol_comp))

    nodes = pd.read_parquet("data/graph_nodes.parquet")
    mol_all = sorted(
        {k for k in nodes.loc[nodes["node_type"] == "Molecule", "key"].astype(str).tolist() if k.strip() != ""}
    )

    picks: List[Tuple[str, str]] = []
    if mol_comp:
        picks.append(("with_computation", mol_comp[0]))
    if mol_only_obs:
        picks.append(("only_observation", mol_only_obs[0]))
    if mol_all:
        picks.append(("random", random.choice(mol_all)))

    logger.info(f"picks: {picks}")
    ok = True

    def _global_target_counts(inchikey: str) -> Tuple[int, int]:
        mol_id = f"mol:{inchikey}"
        n_comp = int(((edges["rel_type"] == "HAS_COMPUTATION") & (edges["src_id"].astype(str) == mol_id)).sum())
        n_obs = int(((edges["rel_type"] == "HAS_OBSERVATION") & (edges["src_id"].astype(str) == mol_id)).sum())
        return n_comp, n_obs

    for label, ik in picks:
        global_comp, global_obs = _global_target_counts(ik)

        sg = get_subgraph(
            ik,
            hops=2,
            max_nodes=args.max_nodes,
            max_edges=args.max_edges,
            max_neighbors=10,
            per_neighbor_evidence_cap=5,
        )
        node_counts, edge_counts = _node_edge_counts(sg)
        stats = sg.get("stats", {})
        logger.info(f"[{label}] inchikey={ik}")
        logger.info(f"  target_global_counts: HAS_COMPUTATION={global_comp} HAS_OBSERVATION={global_obs}")
        logger.info(f"  target_included_counts: {stats.get('included_target_evidence_count_by_type')}")
        if label == "only_observation":
            # "only_observation" refers to the target molecule itself (neighbors may still have computations).
            if global_comp != 0:
                ok = False
                logger.error(f"  LABEL CHECK FAIL: only_observation target has HAS_COMPUTATION={global_comp}")
            elif global_obs == 0:
                ok = False
                logger.error("  LABEL CHECK FAIL: only_observation target has 0 observation edges")
            else:
                logger.info("  LABEL CHECK OK: only_observation target has 0 computation edges")

        logger.info(f"  node_counts={node_counts}")
        logger.info(f"  edge_counts={edge_counts}")
        logger.info(f"  provenance_refs_head={sg.get('provenance_refs', [])[:5]}")
        logger.info(
            "  truncated="
            f"{stats.get('truncated')} "
            f"dropped_neighbors={stats.get('dropped_neighbors')} "
            f"dropped_target_evidence={stats.get('dropped_target_evidence_count')} "
            f"dropped_neighbor_evidence={stats.get('dropped_neighbor_evidence_count')} "
            f"dropped_condition={stats.get('dropped_condition_count')}"
        )
        logger.info(
            "  included_neighbors="
            f"{stats.get('included_neighbor_count')} "
            f"included_neighbor_evidence_total={stats.get('included_neighbor_evidence_total')}"
        )

        errors = _basic_checks(sg, args.max_nodes, args.max_edges)
        if errors:
            ok = False
            logger.error(f"  FAIL ({len(errors)} issues)")
            for e in errors[:20]:
                logger.error(f"    - {e}")
        else:
            logger.info("  PASS")

    if not ok:
        raise SystemExit(1)
    logger.info("ALL CASES: PASS")


if __name__ == "__main__":
    main()
