"""
src/graph/build_light_graph_v1_p2.py

V1-P2: Export Light KG tables (nodes/edges) from V1 evidence_table plus
structure-only similarity edges (ECFP Tanimoto).

Inputs:
- data/evidence_table.parquet
- data/anchor_neighbors_ecfp.parquet

Outputs:
- data/graph_nodes.parquet
- data/graph_edges.parquet
- data/graph_build_manifest.json

Usage:
    python -m src.graph.build_light_graph_v1_p2
"""

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

from src.utils.logging import get_logger


logger = get_logger(__name__)


ALLOWED_NODE_TYPES = {"Molecule", "Evidence", "Condition"}
ALLOWED_EDGE_TYPES = {
    "HAS_OBSERVATION",
    "HAS_COMPUTATION",
    "HAS_EVIDENCECLAIM",
    "UNDER_CONDITION",
    "SIMILAR_TO",
}


def now_iso() -> str:
    return datetime.now().isoformat()


def norm_str(val: Any) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, float) and np.isnan(val):
        return None
    s = str(val).strip()
    return s if s != "" else None


def json_dumps(obj: Dict[str, Any]) -> str:
    # Keep ASCII for portability and stable diffs.
    return json.dumps(obj, ensure_ascii=True, sort_keys=True)


def _py_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, float) and np.isnan(x):
        return None
    try:
        return float(x)
    except Exception:
        return None


def build_nodes(evidence: pd.DataFrame) -> Tuple[pd.DataFrame, Set[str], Dict[str, int]]:
    # Molecule nodes (only when inchikey is present)
    mol_keys = (
        evidence["subject_inchikey"]
        .dropna()
        .astype(str)
        .map(lambda s: s.strip())
    )
    mol_keys = mol_keys[mol_keys != ""].unique().tolist()
    molecules_set = set(mol_keys)

    nodes: List[Dict[str, Any]] = []

    for ik in sorted(molecules_set):
        nodes.append({
            "node_id": f"mol:{ik}",
            "node_type": "Molecule",
            "key": ik,
            "props_json": json_dumps({"inchikey": ik}),
        })

    # Evidence nodes (one per evidence_id)
    has_quality = "quality_flag" in evidence.columns or "quality_score" in evidence.columns

    for _, r in evidence.iterrows():
        eid = norm_str(r.get("evidence_id"))
        if eid is None:
            # This is a build correctness issue (should never happen); keep going but log.
            logger.warning("Found null/empty evidence_id row; skipping evidence node")
            continue

        props: Dict[str, Any] = {
            "evidence_type": norm_str(r.get("evidence_type")),
            "field": norm_str(r.get("field")),
            "value": norm_str(r.get("value")),
            "value_num": _py_float(r.get("value_num")),
            "unit": norm_str(r.get("unit")),
            "confidence": _py_float(r.get("confidence")),
            "source_type": norm_str(r.get("source_type")),
            "source_id": norm_str(r.get("source_id")),
            "timestamp": norm_str(r.get("timestamp")),
            "condition_state": norm_str(r.get("condition_state")),
            "condition_solvent": norm_str(r.get("condition_solvent")) or "unknown",
        }

        # Preserve additional fields when available; keep optional to avoid schema coupling.
        ts_source = norm_str(r.get("timestamp_source"))
        if ts_source is not None:
            props["timestamp_source"] = ts_source

        extraction_method = norm_str(r.get("extraction_method"))
        if extraction_method is not None:
            props["extraction_method"] = extraction_method

        if has_quality:
            qf = norm_str(r.get("quality_flag"))
            qs = _py_float(r.get("quality_score"))
            if qf is not None:
                props["quality_flag"] = qf
            if qs is not None:
                props["quality_score"] = qs

        nodes.append({
            "node_id": f"ev:{eid}",
            "node_type": "Evidence",
            "key": eid,
            "props_json": json_dumps(props),
        })

    # Condition nodes (dedupe by condition_id)
    cond_ids: Set[str] = set()
    for _, r in evidence.iterrows():
        state = norm_str(r.get("condition_state")) or "unknown"
        solvent = norm_str(r.get("condition_solvent")) or "unknown"
        cond_id = f"cond:{state}:{solvent}"
        cond_ids.add(cond_id)

    for cid in sorted(cond_ids):
        # cid is also used as key to keep mapping simple and stable.
        _, state, solvent = cid.split(":", 2)
        nodes.append({
            "node_id": cid,
            "node_type": "Condition",
            "key": cid,
            "props_json": json_dumps({"condition_state": state, "condition_solvent": solvent}),
        })

    df_nodes = pd.DataFrame(nodes, columns=["node_id", "node_type", "key", "props_json"])

    counts_by_type = df_nodes["node_type"].value_counts(dropna=False).to_dict()

    # Basic sanity
    if df_nodes["node_id"].duplicated().any():
        dupes = df_nodes[df_nodes["node_id"].duplicated()]["node_id"].head(5).tolist()
        raise ValueError(f"Duplicate node_id detected (first 5): {dupes}")
    bad_types = sorted(set(df_nodes["node_type"].dropna()) - ALLOWED_NODE_TYPES)
    if bad_types:
        raise ValueError(f"Invalid node_type values: {bad_types}")

    return df_nodes, molecules_set, counts_by_type


def build_edges(
    evidence: pd.DataFrame,
    molecules_set: Set[str],
    anchor_neighbors: pd.DataFrame,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    edges: List[Dict[str, Any]] = []

    # Evidence-driven edges
    rel_map = {
        "private_observation": "HAS_OBSERVATION",
        "atb_computation": "HAS_COMPUTATION",
        "literature_claim": "HAS_EVIDENCECLAIM",
    }

    n_subject_null_skipped = 0

    for _, r in evidence.iterrows():
        eid = norm_str(r.get("evidence_id"))
        if eid is None:
            continue

        etype = norm_str(r.get("evidence_type")) or ""
        if etype not in rel_map:
            raise ValueError(f"Unexpected evidence_type in evidence_table: {etype!r}")

        ik = norm_str(r.get("subject_inchikey"))
        if ik is None:
            n_subject_null_skipped += 1
        else:
            # Molecule -> Evidence
            rel_type = rel_map[etype]
            edges.append({
                "src_id": f"mol:{ik}",
                "rel_type": rel_type,
                "dst_id": f"ev:{eid}",
                "weight": None,
                "evidence_id": eid,
                "props_json": json_dumps({
                    "field": norm_str(r.get("field")),
                    "source_type": norm_str(r.get("source_type")),
                }),
            })

        # Evidence -> Condition (always)
        state = norm_str(r.get("condition_state")) or "unknown"
        solvent = norm_str(r.get("condition_solvent")) or "unknown"
        cond_id = f"cond:{state}:{solvent}"
        edges.append({
            "src_id": f"ev:{eid}",
            "rel_type": "UNDER_CONDITION",
            "dst_id": cond_id,
            "weight": None,
            "evidence_id": eid,
            "props_json": json_dumps({}),
        })

    # Similarity edges (structure-only)
    kept_sim = 0
    dropped_missing_nodes = 0
    dropped_null_keys = 0
    dropped_bad_weight = 0

    required_cols = {"inchikey", "neighbor_inchikey", "rank", "tanimoto_sim"}
    missing = sorted(required_cols - set(anchor_neighbors.columns))
    if missing:
        raise ValueError(f"anchor_neighbors missing required columns: {missing}")

    for _, r in anchor_neighbors.iterrows():
        src_ik = norm_str(r.get("inchikey"))
        dst_ik = norm_str(r.get("neighbor_inchikey"))
        if src_ik is None or dst_ik is None:
            dropped_null_keys += 1
            continue
        if (src_ik not in molecules_set) or (dst_ik not in molecules_set):
            dropped_missing_nodes += 1
            continue

        w = _py_float(r.get("tanimoto_sim"))
        if w is None or w < 0.0 or w > 1.0:
            dropped_bad_weight += 1
            continue

        rank = r.get("rank")
        try:
            rank_int = int(rank)
        except Exception:
            rank_int = None

        edges.append({
            "src_id": f"mol:{src_ik}",
            "rel_type": "SIMILAR_TO",
            "dst_id": f"mol:{dst_ik}",
            "weight": w,
            "evidence_id": None,
            "props_json": json_dumps({
                "rank": rank_int,
                "metric": "tanimoto_ecfp",
            }),
        })
        kept_sim += 1

    df_edges = pd.DataFrame(edges, columns=["src_id", "rel_type", "dst_id", "weight", "evidence_id", "props_json"])
    counts_by_rel = df_edges["rel_type"].value_counts(dropna=False).to_dict()

    bad_rels = sorted(set(df_edges["rel_type"].dropna()) - ALLOWED_EDGE_TYPES)
    if bad_rels:
        raise ValueError(f"Invalid rel_type values: {bad_rels}")

    stats = {
        "counts_by_rel_type": counts_by_rel,
        "evidence_edges": {
            "n_subject_inchikey_null_skipped_mol_to_ev": n_subject_null_skipped,
        },
        "similarity_edges": {
            "total_anchor_rows": int(len(anchor_neighbors)),
            "kept_similar_to": int(kept_sim),
            "dropped_missing_molecule_nodes": int(dropped_missing_nodes),
            "dropped_null_inchikey": int(dropped_null_keys),
            "dropped_bad_weight": int(dropped_bad_weight),
        },
    }

    return df_edges, stats


def write_manifest(path: Path, manifest: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(description="V1-P2: build light graph tables from evidence_table + ECFP neighbors")
    parser.add_argument("--evidence", default="data/evidence_table.parquet")
    parser.add_argument("--neighbors", default="data/anchor_neighbors_ecfp.parquet")
    parser.add_argument("--out-nodes", default="data/graph_nodes.parquet")
    parser.add_argument("--out-edges", default="data/graph_edges.parquet")
    parser.add_argument("--manifest", default="data/graph_build_manifest.json")
    args = parser.parse_args()

    build_ts = now_iso()

    logger.info(f"Loading evidence_table: {args.evidence}")
    evidence = pd.read_parquet(args.evidence)
    logger.info(f"Loading anchor_neighbors_ecfp: {args.neighbors}")
    neighbors = pd.read_parquet(args.neighbors)

    nodes_df, molecules_set, node_counts = build_nodes(evidence)
    edges_df, edge_stats = build_edges(evidence, molecules_set, neighbors)

    out_nodes = Path(args.out_nodes)
    out_edges = Path(args.out_edges)
    out_nodes.parent.mkdir(parents=True, exist_ok=True)
    out_edges.parent.mkdir(parents=True, exist_ok=True)

    logger.info(f"Writing graph_nodes: {out_nodes} ({len(nodes_df)} rows)")
    nodes_df.to_parquet(out_nodes, index=False)
    logger.info(f"Writing graph_edges: {out_edges} ({len(edges_df)} rows)")
    edges_df.to_parquet(out_edges, index=False)

    manifest = {
        "build_timestamp": build_ts,
        "inputs": {
            "evidence_table_path": args.evidence,
            "evidence_table_rows": int(len(evidence)),
            "anchor_neighbors_path": args.neighbors,
            "anchor_neighbors_rows": int(len(neighbors)),
        },
        "nodes": {
            "counts_by_node_type": node_counts,
            "total_nodes": int(len(nodes_df)),
        },
        "edges": {
            "counts_by_rel_type": edge_stats["counts_by_rel_type"],
            "total_edges": int(len(edges_df)),
        },
        "integrity": edge_stats,
    }
    write_manifest(Path(args.manifest), manifest)
    logger.info(f"Wrote manifest: {args.manifest}")

    logger.info("Done.")


if __name__ == "__main__":
    main()

