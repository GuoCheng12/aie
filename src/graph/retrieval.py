"""
src/graph/retrieval.py

V1-P3: Subgraph Retrieval API for GraphRAG context.

Uses V1 Light KG tables produced in V1-P2:
- data/graph_nodes.parquet
- data/graph_edges.parquet
- data/evidence_table.parquet

No new node types. No mechanism/hypothesis writeback in V1.

CLI:
    python -m src.graph.retrieval --inchikey <INCHIKEY> --hops 2 --max_nodes 50 --max_edges 200
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from src.utils.logging import get_logger


logger = get_logger(__name__)


DEFAULT_NODES_PATH = "data/graph_nodes.parquet"
DEFAULT_EDGES_PATH = "data/graph_edges.parquet"
DEFAULT_EVIDENCE_PATH = "data/evidence_table.parquet"


ALLOWED_REL_TYPES = {
    "HAS_OBSERVATION",
    "HAS_COMPUTATION",
    "HAS_EVIDENCECLAIM",
    "UNDER_CONDITION",
    "SIMILAR_TO",
}


FIELD_PRIORITY = [
    "delta_gap",
    "delta_dihedral",
    "delta_volume",
    "excitation_energy",
    "emission_solid",
    "emission_aggr",
]
FIELD_RANK = {f: i for i, f in enumerate(FIELD_PRIORITY)}


def _norm_str(x: Any) -> Optional[str]:
    if x is None:
        return None
    if isinstance(x, float) and np.isnan(x):
        return None
    s = str(x).strip()
    return s if s != "" else None


def _safe_json_loads(s: Any) -> Dict[str, Any]:
    if s is None:
        return {}
    if isinstance(s, float) and np.isnan(s):
        return {}
    try:
        return json.loads(str(s))
    except Exception:
        return {}


def _py_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, float) and np.isnan(x):
        return None
    try:
        return float(x)
    except Exception:
        return None


@dataclass(frozen=True)
class EdgeRec:
    src_id: str
    rel_type: str
    dst_id: str
    weight: Optional[float]
    evidence_id: Optional[str]
    props_json: Optional[str]


class GraphStore:
    def __init__(self, nodes_path: str, edges_path: str, evidence_path: str) -> None:
        self.nodes_path = nodes_path
        self.edges_path = edges_path
        self.evidence_path = evidence_path

        self._nodes = pd.read_parquet(nodes_path)
        self._edges = pd.read_parquet(edges_path)
        self._evidence = pd.read_parquet(evidence_path)

        # node_id -> node_type / props_json
        self._node_type: Dict[str, str] = dict(
            zip(self._nodes["node_id"].astype(str), self._nodes["node_type"].astype(str))
        )
        self._node_props_json: Dict[str, Optional[str]] = dict(
            zip(self._nodes["node_id"].astype(str), self._nodes.get("props_json", pd.Series([None] * len(self._nodes))).astype(object))
        )
        self._node_props_cache: Dict[str, Dict[str, Any]] = {}

        # src_id -> outgoing edges
        self._adj: Dict[str, List[EdgeRec]] = {}
        self._build_adjacency()

        # evidence_id -> (evidence_type, field)
        self._evidence_meta: Dict[str, Tuple[str, str]] = {}
        self._build_evidence_meta()

    def _build_adjacency(self) -> None:
        adj: Dict[str, List[EdgeRec]] = {}
        for _, r in self._edges.iterrows():
            src = str(r["src_id"])
            rel = str(r["rel_type"])
            dst = str(r["dst_id"])
            weight = _py_float(r.get("weight"))
            eid = _norm_str(r.get("evidence_id"))
            props_json = None if (r.get("props_json") is None or (isinstance(r.get("props_json"), float) and np.isnan(r.get("props_json")))) else str(r.get("props_json"))
            adj.setdefault(src, []).append(
                EdgeRec(
                    src_id=src,
                    rel_type=rel,
                    dst_id=dst,
                    weight=weight,
                    evidence_id=eid,
                    props_json=props_json,
                )
            )
        self._adj = adj

    def _build_evidence_meta(self) -> None:
        if "evidence_id" not in self._evidence.columns:
            return
        cols = {"evidence_id", "evidence_type", "field"}
        missing = sorted(cols - set(self._evidence.columns))
        if missing:
            return
        meta = {}
        for _, r in self._evidence[["evidence_id", "evidence_type", "field"]].iterrows():
            eid = _norm_str(r.get("evidence_id"))
            if eid is None:
                continue
            et = _norm_str(r.get("evidence_type")) or ""
            field = _norm_str(r.get("field")) or ""
            meta[eid] = (et, field)
        self._evidence_meta = meta

    def has_node(self, node_id: str) -> bool:
        return node_id in self._node_type

    def node_type(self, node_id: str) -> Optional[str]:
        return self._node_type.get(node_id)

    def node_props(self, node_id: str) -> Dict[str, Any]:
        if node_id in self._node_props_cache:
            return self._node_props_cache[node_id]
        props = _safe_json_loads(self._node_props_json.get(node_id))
        self._node_props_cache[node_id] = props
        return props

    def get_node_record(self, node_id: str) -> Dict[str, Any]:
        ntype = self._node_type.get(node_id)
        if ntype is None:
            raise KeyError(f"node_id not found: {node_id}")
        return {"node_id": node_id, "node_type": ntype, "props": self.node_props(node_id)}

    def edges_from(self, src_id: str, rel_type: Optional[str] = None) -> List[EdgeRec]:
        out = self._adj.get(src_id, [])
        if rel_type is None:
            return out
        return [e for e in out if e.rel_type == rel_type]

    def evidence_meta(self, evidence_id: str) -> Tuple[str, str]:
        return self._evidence_meta.get(evidence_id, ("", ""))


_DEFAULT_STORE: Optional[GraphStore] = None


def reset_cache() -> None:
    global _DEFAULT_STORE
    _DEFAULT_STORE = None


def _get_store() -> GraphStore:
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = GraphStore(DEFAULT_NODES_PATH, DEFAULT_EDGES_PATH, DEFAULT_EVIDENCE_PATH)
    return _DEFAULT_STORE


def _etype_rank(etype: str, prefer: Sequence[str]) -> int:
    try:
        return list(prefer).index(etype)
    except ValueError:
        return len(prefer)


def _field_rank(field: str) -> int:
    return FIELD_RANK.get(field, len(FIELD_RANK) + 1)


def get_subgraph(
    inchikey: str,
    hops: int = 2,
    max_nodes: int = 50,
    max_edges: int = 200,
    max_neighbors: int = 10,
    per_neighbor_evidence_cap: int = 5,
    prefer_evidence_types: List[str] = ["atb_computation", "private_observation"],
) -> Dict[str, Any]:
    """
    Retrieve a small evidence-backed subgraph around a molecule for GraphRAG context.
    """

    store = _get_store()

    query_inchikey = inchikey
    mol_id = f"mol:{inchikey}"

    stats: Dict[str, Any] = {
        "hops": int(hops),
        "max_nodes": int(max_nodes),
        "max_edges": int(max_edges),
        "max_neighbors": int(max_neighbors),
        "per_neighbor_evidence_cap": int(per_neighbor_evidence_cap),
        "truncated": False,
        "missing_molecule": False,
        # More interpretable stats (filled after construction)
        "included_target_evidence_count_by_type": {"obs": 0, "comp": 0},
        "included_neighbor_count": 0,
        "included_neighbor_evidence_total": 0,
        # Drop stats (target vs neighbor vs condition)
        "dropped_neighbors": 0,
        "dropped_target_evidence_count": 0,
        "dropped_neighbor_evidence_count": 0,
        "dropped_condition_count": 0,
        "dropped_due_to_node_budget": 0,
        "dropped_due_to_edge_budget": 0,
    }

    if max_nodes <= 0 or max_edges <= 0:
        stats["truncated"] = True
        return {
            "query_inchikey": query_inchikey,
            "nodes": [],
            "edges": [],
            "provenance_refs": [],
            "stats": stats,
        }

    if not store.has_node(mol_id):
        stats["missing_molecule"] = True
        return {
            "query_inchikey": query_inchikey,
            "nodes": [],
            "edges": [],
            "provenance_refs": [],
            "stats": stats,
        }

    included_nodes: List[str] = []
    included_nodes_set = set()
    included_edges: List[Dict[str, Any]] = []
    included_edges_set = set()
    provenance: set[str] = set()

    # For interpretability: track neighbor molecules included via SIMILAR_TO.
    neighbor_mol_ids: set[str] = set()

    def _can_add_node() -> bool:
        return len(included_nodes) < max_nodes

    def _can_add_edge() -> bool:
        return len(included_edges) < max_edges

    def _add_node(node_id: str) -> bool:
        if node_id in included_nodes_set:
            return True
        if not _can_add_node():
            stats["truncated"] = True
            stats["dropped_due_to_node_budget"] += 1
            return False
        included_nodes.append(node_id)
        included_nodes_set.add(node_id)
        return True

    def _add_edge(src: str, rel: str, dst: str, props: Dict[str, Any]) -> bool:
        if not _can_add_edge():
            stats["truncated"] = True
            stats["dropped_due_to_edge_budget"] += 1
            return False
        key = (src, rel, dst, json.dumps(props, sort_keys=True, ensure_ascii=True))
        if key in included_edges_set:
            return True
        included_edges.append({"src": src, "rel": rel, "dst": dst, "props": props})
        included_edges_set.add(key)
        return True

    def _has_capacity(add_nodes: int, add_edges: int) -> bool:
        return (len(included_nodes) + add_nodes <= max_nodes) and (len(included_edges) + add_edges <= max_edges)

    def _add_evidence_block(mol_node_id: str, evidence_id: str) -> bool:
        """
        Add Evidence node + mol->ev edge + condition node + ev->cond edge.
        Returns False if budgets prevent adding (caller decides how to count drops).
        """
        ev_node_id = f"ev:{evidence_id}"

        # mol -> ev
        etype, field = store.evidence_meta(evidence_id)
        rel = {
            "private_observation": "HAS_OBSERVATION",
            "atb_computation": "HAS_COMPUTATION",
            "literature_claim": "HAS_EVIDENCECLAIM",
        }.get(etype, "HAS_EVIDENCECLAIM")

        # ev -> cond
        cond_edges = store.edges_from(ev_node_id, rel_type="UNDER_CONDITION")
        if cond_edges:
            ce = cond_edges[0]
            cond_id = ce.dst_id
        else:
            # Fallback (should not happen in V1-P2 graph) – keep retrieval robust.
            # Best-effort: read condition fields from evidence_table meta if present.
            props = store.node_props(ev_node_id)
            state = _norm_str(props.get("condition_state")) or "unknown"
            solvent = _norm_str(props.get("condition_solvent")) or "unknown"
            cond_id = f"cond:{state}:{solvent}"

        # Atomic budget check (avoid adding dangling nodes without their connecting edges)
        need_nodes = 0
        if ev_node_id not in included_nodes_set:
            need_nodes += 1
        if cond_id not in included_nodes_set:
            need_nodes += 1
        need_edges = 2  # mol->ev and ev->cond
        if not _has_capacity(need_nodes, need_edges):
            stats["truncated"] = True
            # Evidence blocks also imply a condition context; count when we would need a new condition node.
            if cond_id not in included_nodes_set:
                stats["dropped_condition_count"] += 1
            if len(included_nodes) + need_nodes > max_nodes:
                stats["dropped_due_to_node_budget"] += 1
            if len(included_edges) + need_edges > max_edges:
                stats["dropped_due_to_edge_budget"] += 1
            return False

        if not _add_node(ev_node_id):
            return False
        if not _add_edge(mol_node_id, rel, ev_node_id, {"evidence_id": evidence_id, "field": field}):
            return False
        provenance.add(evidence_id)

        if not _add_node(cond_id):
            return False
        if not _add_edge(ev_node_id, "UNDER_CONDITION", cond_id, {"evidence_id": evidence_id}):
            return False
        provenance.add(evidence_id)
        return True

    # Always include target molecule node
    _add_node(mol_id)

    # -------------------------
    # P0: target evidence first
    # -------------------------
    comp_edges = store.edges_from(mol_id, rel_type="HAS_COMPUTATION")
    obs_edges = store.edges_from(mol_id, rel_type="HAS_OBSERVATION")
    chosen = comp_edges if len(comp_edges) > 0 else obs_edges

    # Sort evidence deterministically by (etype pref, field priority, evidence_id)
    cand_eids: List[str] = []
    for e in chosen:
        if e.evidence_id is None:
            continue
        cand_eids.append(e.evidence_id)

    def _evidence_sort_key(eid: str) -> Tuple[int, int, str]:
        et, field = store.evidence_meta(eid)
        return (_etype_rank(et, prefer_evidence_types), _field_rank(field), eid)

    cand_eids = sorted(set(cand_eids), key=_evidence_sort_key)

    for eid in cand_eids:
        ok = _add_evidence_block(mol_id, eid)
        if not ok:
            # Remaining target evidence are lower priority; stop.
            remaining = len(cand_eids) - (cand_eids.index(eid))
            stats["dropped_target_evidence_count"] += max(1, remaining)
            break

    # -------------------------
    # P1: top structural neighbors
    # -------------------------
    neighbors_included: List[EdgeRec] = []
    sim_edges = store.edges_from(mol_id, rel_type="SIMILAR_TO")

    def _sim_key(e: EdgeRec) -> Tuple[float, str]:
        w = e.weight if e.weight is not None else -1.0
        return (-w, e.dst_id)

    sim_edges_sorted = sorted(sim_edges, key=_sim_key)
    sim_edges_sorted = sim_edges_sorted[: max(0, int(max_neighbors))]

    for e in sim_edges_sorted:
        dst = e.dst_id
        if not store.has_node(dst):
            # Should not happen for V1-P2 graph after filtering, but be robust.
            continue
        props = _safe_json_loads(e.props_json)
        if e.weight is not None:
            props["weight"] = float(e.weight)

        need_nodes = 0 if dst in included_nodes_set else 1
        need_edges = 1
        if not _has_capacity(need_nodes, need_edges):
            stats["truncated"] = True
            stats["dropped_neighbors"] += 1
            continue
        if not _add_node(dst):
            stats["dropped_neighbors"] += 1
            continue
        if not _add_edge(mol_id, "SIMILAR_TO", dst, props):
            stats["dropped_neighbors"] += 1
            continue
        neighbors_included.append(e)
        neighbor_mol_ids.add(dst)

    # hops=1 stops here (no neighbor evidence expansion)
    if int(hops) >= 2:
        # -------------------------
        # P2: neighbor evidence (capped)
        # -------------------------
        for e in neighbors_included:
            nb = e.dst_id
            nb_comp = store.edges_from(nb, rel_type="HAS_COMPUTATION")
            nb_obs = store.edges_from(nb, rel_type="HAS_OBSERVATION")
            nb_chosen = nb_comp if len(nb_comp) > 0 else nb_obs

            nb_eids: List[str] = []
            for ee in nb_chosen:
                if ee.evidence_id is None:
                    continue
                nb_eids.append(ee.evidence_id)

            nb_eids = sorted(set(nb_eids), key=_evidence_sort_key)
            nb_eids = nb_eids[: max(0, int(per_neighbor_evidence_cap))]

            for eid in nb_eids:
                ok = _add_evidence_block(nb, eid)
                if not ok:
                    stats["dropped_neighbor_evidence_count"] += 1
                    # Stop adding more evidence for this neighbor when budget is hit.
                    break

    # Assemble output with deterministic ordering (insertion order already deterministic)
    out_nodes = [store.get_node_record(nid) for nid in included_nodes]
    out_edges = included_edges
    provenance_refs = sorted(provenance)

    # Stats: counts + truncation recap
    node_types = {}
    for nid in included_nodes:
        nt = store.node_type(nid) or "Unknown"
        node_types[nt] = node_types.get(nt, 0) + 1
    rel_types = {}
    for ed in included_edges:
        rel = ed["rel"]
        rel_types[rel] = rel_types.get(rel, 0) + 1
    stats["nodes_by_type"] = node_types
    stats["edges_by_rel"] = rel_types
    stats["total_nodes"] = len(out_nodes)
    stats["total_edges"] = len(out_edges)
    stats["provenance_refs_count"] = len(provenance_refs)

    # Filled interpretability fields (counts derived from included subgraph only)
    target_comp = 0
    target_obs = 0
    neighbor_count = 0
    neighbor_evidence_total = 0
    for ed in included_edges:
        if ed["src"] == mol_id and ed["rel"] == "SIMILAR_TO":
            neighbor_count += 1
        if ed["src"] == mol_id and ed["rel"] == "HAS_COMPUTATION":
            target_comp += 1
        if ed["src"] == mol_id and ed["rel"] == "HAS_OBSERVATION":
            target_obs += 1
        if ed["rel"] in {"HAS_OBSERVATION", "HAS_COMPUTATION", "HAS_EVIDENCECLAIM"} and ed["src"] != mol_id and str(ed["src"]).startswith("mol:"):
            neighbor_evidence_total += 1

    stats["included_target_evidence_count_by_type"] = {"obs": int(target_obs), "comp": int(target_comp)}
    stats["included_neighbor_count"] = int(len(neighbor_mol_ids)) if neighbor_mol_ids else int(neighbor_count)
    stats["included_neighbor_evidence_total"] = int(neighbor_evidence_total)

    return {
        "query_inchikey": query_inchikey,
        "nodes": out_nodes,
        "edges": out_edges,
        "provenance_refs": provenance_refs,
        "stats": stats,
    }


def _summarize_subgraph(sg: Dict[str, Any]) -> Dict[str, Any]:
    nodes = sg.get("nodes", [])
    edges = sg.get("edges", [])
    node_counts: Dict[str, int] = {}
    for n in nodes:
        node_counts[n.get("node_type")] = node_counts.get(n.get("node_type"), 0) + 1
    edge_counts: Dict[str, int] = {}
    for e in edges:
        edge_counts[e.get("rel")] = edge_counts.get(e.get("rel"), 0) + 1
    return {
        "node_counts": node_counts,
        "edge_counts": edge_counts,
        "provenance_refs_head": sg.get("provenance_refs", [])[:5],
        "stats": sg.get("stats", {}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="V1-P3: retrieve a small evidence-backed subgraph for GraphRAG context")
    parser.add_argument("--inchikey", required=True)
    parser.add_argument("--hops", type=int, default=2)
    parser.add_argument("--max_nodes", type=int, default=50)
    parser.add_argument("--max_edges", type=int, default=200)
    parser.add_argument("--max_neighbors", type=int, default=10)
    parser.add_argument("--per_neighbor_evidence_cap", type=int, default=5)
    parser.add_argument("--out", default=None, help="Optional path to write full JSON output")
    parser.add_argument("--print_json", action="store_true", help="Print full JSON to stdout")
    args = parser.parse_args()

    sg = get_subgraph(
        args.inchikey,
        hops=args.hops,
        max_nodes=args.max_nodes,
        max_edges=args.max_edges,
        max_neighbors=args.max_neighbors,
        per_neighbor_evidence_cap=args.per_neighbor_evidence_cap,
    )

    summary = _summarize_subgraph(sg)
    logger.info(f"query_inchikey: {sg.get('query_inchikey')}")
    logger.info(f"node_counts: {summary['node_counts']}")
    logger.info(f"edge_counts: {summary['edge_counts']}")
    logger.info(f"provenance_refs (first 5): {summary['provenance_refs_head']}")
    logger.info(
        "truncated: "
        f"{summary['stats'].get('truncated')} "
        f"dropped_neighbors={summary['stats'].get('dropped_neighbors')} "
        f"dropped_target_evidence={summary['stats'].get('dropped_target_evidence_count')} "
        f"dropped_neighbor_evidence={summary['stats'].get('dropped_neighbor_evidence_count')} "
        f"dropped_condition={summary['stats'].get('dropped_condition_count')}"
    )
    logger.info(
        "included: "
        f"target_evidence={summary['stats'].get('included_target_evidence_count_by_type')} "
        f"neighbors={summary['stats'].get('included_neighbor_count')} "
        f"neighbor_evidence_total={summary['stats'].get('included_neighbor_evidence_total')}"
    )
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w") as f:
            json.dump(sg, f, indent=2, ensure_ascii=True, sort_keys=True)
        logger.info(f"Wrote JSON: {args.out}")
    if args.print_json:
        print(json.dumps(sg, indent=2, ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()
