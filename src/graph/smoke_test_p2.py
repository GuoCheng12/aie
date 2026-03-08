"""
src/graph/smoke_test_p2.py

V1-P2 Smoke Test (analysis/validation only)

Evaluates Light KG quality without implementing V1-P3 subgraph API.

Inputs:
- data/graph_nodes.parquet
- data/graph_edges.parquet
- data/evidence_table.parquet
- data/graph_build_manifest.json

Optional (for explaining dangling evidence):
- data/private_clean.parquet
"""

import argparse
import json
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


ALLOWED_REL_TYPES = {
    "HAS_OBSERVATION",
    "HAS_COMPUTATION",
    "HAS_EVIDENCECLAIM",
    "UNDER_CONDITION",
    "SIMILAR_TO",
}

HAS_RELS = {"HAS_OBSERVATION", "HAS_COMPUTATION", "HAS_EVIDENCECLAIM"}


def _norm_str(x: Any) -> Optional[str]:
    if x is None:
        return None
    if isinstance(x, float) and np.isnan(x):
        return None
    s = str(x).strip()
    return s if s != "" else None


def _pct(n: int, d: int) -> str:
    if d <= 0:
        return "n/a"
    return f"{(100.0 * n / d):.2f}%"


def _quantiles(series: pd.Series, qs: List[float]) -> Dict[float, float]:
    s = series.dropna().astype(float)
    if len(s) == 0:
        return {q: float("nan") for q in qs}
    out = s.quantile(qs).to_dict()
    return {float(k): float(v) for k, v in out.items()}


def _load_optional_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    with open(path, "r") as f:
        return json.load(f)


def main() -> None:
    parser = argparse.ArgumentParser(description="V1-P2 Light KG smoke test (no API)")
    parser.add_argument("--nodes", default="data/graph_nodes.parquet")
    parser.add_argument("--edges", default="data/graph_edges.parquet")
    parser.add_argument("--evidence", default="data/evidence_table.parquet")
    parser.add_argument("--manifest", default="data/graph_build_manifest.json")
    parser.add_argument("--private-clean", default="data/private_clean.parquet")
    parser.add_argument("--symmetry-sample", type=int, default=200)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)

    nodes_path = Path(args.nodes)
    edges_path = Path(args.edges)
    evidence_path = Path(args.evidence)
    manifest_path = Path(args.manifest)

    nodes = pd.read_parquet(nodes_path)
    edges = pd.read_parquet(edges_path)
    evidence = pd.read_parquet(evidence_path)
    manifest = _load_optional_json(manifest_path)

    print("V1-P2 Light KG Smoke Test Report")
    print("=" * 80)

    # ------------------------------------------------------------------
    # A) Basic integrity recap
    # ------------------------------------------------------------------
    print("\nA) Basic integrity recap")
    print("-" * 80)
    if manifest is not None:
        print(f"manifest.build_timestamp: {manifest.get('build_timestamp')}")
        sim_stats = (((manifest.get("integrity") or {}).get("similarity_edges")) or {})
        if sim_stats:
            print(
                "manifest.similarity_edges: "
                f"total={sim_stats.get('total_anchor_rows')} kept={sim_stats.get('kept_similar_to')} "
                f"dropped_missing_nodes={sim_stats.get('dropped_missing_molecule_nodes')}"
            )
    print(f"loaded nodes: {len(nodes)} from {nodes_path}")
    print(f"loaded edges: {len(edges)} from {edges_path}")
    print(f"loaded evidence_table: {len(evidence)} from {evidence_path}")

    node_counts = nodes["node_type"].value_counts(dropna=False).to_dict()
    edge_counts = edges["rel_type"].value_counts(dropna=False).to_dict()
    print(f"node counts by node_type: {node_counts}")
    print(f"edge counts by rel_type: {edge_counts}")

    rel_bad = sorted(set(edges["rel_type"].dropna()) - ALLOWED_REL_TYPES)
    print(f"allowed rel_types only: {'YES' if not rel_bad else 'NO'}")
    if rel_bad:
        print(f"  unexpected rel_types: {rel_bad}")

    node_ids = set(nodes["node_id"].astype(str))
    src_ok = edges["src_id"].astype(str).isin(node_ids)
    dst_ok = edges["dst_id"].astype(str).isin(node_ids)
    ok_both = int((src_ok & dst_ok).sum())
    print(f"edges with src in nodes: {int(src_ok.sum())}/{len(edges)} ({_pct(int(src_ok.sum()), len(edges))})")
    print(f"edges with dst in nodes: {int(dst_ok.sum())}/{len(edges)} ({_pct(int(dst_ok.sum()), len(edges))})")
    print(f"edges with src+dst in nodes: {ok_both}/{len(edges)} ({_pct(ok_both, len(edges))})")

    # ------------------------------------------------------------------
    # B) Dangling Evidence analysis
    # ------------------------------------------------------------------
    print("\nB) Dangling Evidence analysis (missing inchikey evidence)")
    print("-" * 80)
    under = edges[edges["rel_type"] == "UNDER_CONDITION"]
    has_edges = edges[edges["rel_type"].isin(HAS_RELS)]
    # evidence_id is used as the join key everywhere in V1 graph.
    all_ev = set(under["evidence_id"].dropna().astype(str))
    has_incoming = set(has_edges["evidence_id"].dropna().astype(str))
    dangling = sorted(all_ev - has_incoming)
    print(f"dangling Evidence (UNDER_CONDITION but no incoming HAS_*): {len(dangling)}/{len(all_ev)} ({_pct(len(dangling), len(all_ev))})")

    if len(dangling) > 0:
        sample_ids = dangling[:10]
        samp = evidence[evidence["evidence_id"].astype(str).isin(sample_ids)].copy()
        cols = ["evidence_id", "evidence_type", "field", "source_id", "condition_state", "condition_solvent"]
        # Keep output deterministic and compact.
        samp = samp[cols].sort_values(["evidence_type", "field", "source_id"]).head(10)
        print("\nSample (up to 10) dangling evidence rows:")
        for _, r in samp.iterrows():
            print(
                f"- {r['evidence_id']} | {r['evidence_type']} | {r['field']} | source_id={r['source_id']} | "
                f"cond={r['condition_state']}/{r['condition_solvent']}"
            )

        # Optional traceback into private_clean
        pc_path = Path(args.private_clean)
        if pc_path.exists():
            try:
                private_clean = pd.read_parquet(pc_path)
                # Most dangling evidence should be private_observation with source_id==private_clean.id
                trace = samp[samp["evidence_type"] == "private_observation"].head(5)
                if len(trace) > 0 and "id" in private_clean.columns:
                    print("\nTraceback (optional) into private_clean for a few dangling private_observation rows:")
                    for _, rr in trace.iterrows():
                        sid = _norm_str(rr.get("source_id"))
                        if sid is None or sid == "unknown_record":
                            print(f"- source_id={sid} (cannot map to private_clean.id)")
                            continue
                        try:
                            rid = int(sid)
                        except Exception:
                            print(f"- source_id={sid} (non-int; cannot map to private_clean.id)")
                            continue
                        rec = private_clean[private_clean["id"] == rid]
                        if rec.empty:
                            print(f"- source_id={sid} (no matching private_clean.id)")
                            continue
                        rec0 = rec.iloc[0]
                        print(
                            f"- source_id={sid} private_clean.inchikey={_norm_str(rec0.get('inchikey'))} "
                            f"canonical_smiles={_norm_str(rec0.get('canonical_smiles'))}"
                        )
                else:
                    print("\nTraceback skipped: private_clean.id column not found or no dangling private_observation rows in sample.")
            except Exception as e:
                print(f"\nTraceback skipped: failed to read/inspect {pc_path}: {e}")
        else:
            print("\nTraceback skipped: data/private_clean.parquet not found.")

    # ------------------------------------------------------------------
    # C) Molecule degree distribution
    # ------------------------------------------------------------------
    print("\nC) Molecule degree distribution")
    print("-" * 80)
    mol_nodes = nodes[nodes["node_type"] == "Molecule"]["node_id"].astype(str).tolist()
    mol_set = set(mol_nodes)

    def out_deg(rel: str) -> pd.Series:
        s = edges[(edges["rel_type"] == rel) & (edges["src_id"].astype(str).isin(mol_set))]["src_id"].astype(str)
        counts = s.value_counts()
        # Align to all molecule nodes (missing -> 0)
        return pd.Series({m: int(counts.get(m, 0)) for m in mol_nodes})

    deg_obs = out_deg("HAS_OBSERVATION")
    deg_comp = out_deg("HAS_COMPUTATION")
    deg_sim = out_deg("SIMILAR_TO")

    def summarize(name: str, series: pd.Series) -> None:
        q = _quantiles(series, [0.0, 0.5, 0.95, 1.0])
        print(f"{name} out-degree: min={q[0.0]} median={q[0.5]} p95={q[0.95]} max={q[1.0]}")

    summarize("HAS_OBSERVATION", deg_obs)
    summarize("HAS_COMPUTATION", deg_comp)
    summarize("SIMILAR_TO", deg_sim)

    n_with_comp = int((deg_comp > 0).sum())
    print(f"Molecules with >=1 computation evidence: {n_with_comp}/{len(mol_nodes)} ({_pct(n_with_comp, len(mol_nodes))})")

    # ------------------------------------------------------------------
    # D) Manual “2-hop ICL pack” spot-check (no API)
    # ------------------------------------------------------------------
    print("\nD) Manual 2-hop ICL pack spot-check (no API)")
    print("-" * 80)

    # Build helper maps for quick lookups.
    # mol -> list of (evidence_id, rel_type)
    mol_to_ev = edges[edges["rel_type"].isin(HAS_RELS)].copy()
    mol_to_ev = mol_to_ev[mol_to_ev["src_id"].astype(str).isin(mol_set)]

    mol_to_sim = edges[edges["rel_type"] == "SIMILAR_TO"].copy()
    mol_to_sim = mol_to_sim[mol_to_sim["src_id"].astype(str).isin(mol_set)]

    # Pick 3 molecules:
    mol_has_comp = [m for m, d in deg_comp.items() if d > 0]
    mol_only_obs = [m for m in mol_nodes if deg_obs[m] > 0 and deg_comp[m] == 0]
    mol_random = random.choice(mol_nodes) if mol_nodes else None

    picks: List[Tuple[str, str]] = []
    if mol_has_comp:
        picks.append(("with_computation", sorted(mol_has_comp)[0]))
    if mol_only_obs:
        picks.append(("only_observation", sorted(mol_only_obs)[0]))
    if mol_random is not None:
        picks.append(("random", mol_random))

    def summarize_evidence(ev_ids: List[str], top_fields: int) -> Tuple[str, Dict[str, int]]:
        if not ev_ids:
            return "(no evidence)", {}
        sub = evidence[evidence["evidence_id"].astype(str).isin(ev_ids)]
        counts_type = sub["evidence_type"].value_counts(dropna=False).to_dict()
        fields = sub["field"].value_counts().head(top_fields).index.astype(str).tolist()
        return ", ".join(fields), counts_type

    for label, mol_id in picks:
        ik = mol_id.replace("mol:", "", 1)
        print(f"\n[{label}] Molecule A: {ik}")

        # 1-hop evidence of A
        ev_ids_a = mol_to_ev[mol_to_ev["src_id"].astype(str) == mol_id]["evidence_id"].dropna().astype(str).tolist()
        fields_a, counts_a = summarize_evidence(ev_ids_a, top_fields=15)
        print(f"  A evidence_type counts: {counts_a}")
        print(f"  A fields (top 15): {fields_a}")

        # top-3 SIMILAR_TO neighbors
        sims = mol_to_sim[mol_to_sim["src_id"].astype(str) == mol_id].copy()
        sims["weight_num"] = pd.to_numeric(sims["weight"], errors="coerce")
        sims = sims.sort_values("weight_num", ascending=False).head(3)
        if sims.empty:
            print("  A neighbors: (no SIMILAR_TO edges)")
            continue

        print("  A top-3 SIMILAR_TO neighbors:")
        for _, er in sims.iterrows():
            nb = str(er["dst_id"]).replace("mol:", "", 1)
            w = float(er["weight_num"]) if not np.isnan(er["weight_num"]) else None
            print(f"    - {nb} (weight={w})")

        # For each neighbor: collect up to 5 evidence nodes (prefer computation)
        for _, er in sims.iterrows():
            nb_mol = str(er["dst_id"])
            nb_ik = nb_mol.replace("mol:", "", 1)
            comp_ids = mol_to_ev[(mol_to_ev["src_id"].astype(str) == nb_mol) & (mol_to_ev["rel_type"] == "HAS_COMPUTATION")]["evidence_id"].dropna().astype(str).tolist()
            obs_ids = mol_to_ev[(mol_to_ev["src_id"].astype(str) == nb_mol) & (mol_to_ev["rel_type"] == "HAS_OBSERVATION")]["evidence_id"].dropna().astype(str).tolist()
            pick_ids = (comp_ids[:5] if len(comp_ids) > 0 else obs_ids[:5])
            fields_b, counts_b = summarize_evidence(pick_ids, top_fields=10)
            mode = "COMPUTATION" if len(comp_ids) > 0 else "OBSERVATION"
            print(f"    Neighbor B: {nb_ik} (evidence sample pref={mode})")
            print(f"      B evidence_type counts (sample): {counts_b}")
            print(f"      B fields (top 10, sample): {fields_b}")

    # ------------------------------------------------------------------
    # E) SIMILAR_TO quality checks
    # ------------------------------------------------------------------
    print("\nE) SIMILAR_TO quality checks")
    print("-" * 80)
    sim = edges[edges["rel_type"] == "SIMILAR_TO"].copy()
    sim["w"] = pd.to_numeric(sim["weight"], errors="coerce")
    bad_w = sim["w"].isna() | (sim["w"] < 0.0) | (sim["w"] > 1.0)
    print(f"SIMILAR_TO weight in [0,1]: {'YES' if int(bad_w.sum()) == 0 else 'NO'} (bad={int(bad_w.sum())})")
    q = _quantiles(sim["w"], [0.0, 0.5, 0.95, 1.0])
    print(f"SIMILAR_TO weight summary: min={q[0.0]} median={q[0.5]} p95={q[0.95]} max={q[1.0]}")

    # Optional symmetry check (directed edges)
    n_sample = min(int(args.symmetry_sample), len(sim))
    if n_sample > 0:
        sample = sim.sample(n=n_sample, random_state=args.seed)
        pairs = set(zip(sim["src_id"].astype(str), sim["dst_id"].astype(str)))
        rev_exists = 0
        for s, d in zip(sample["src_id"].astype(str), sample["dst_id"].astype(str)):
            if (d, s) in pairs:
                rev_exists += 1
        print(f"symmetry check (sample {n_sample}): reverse edge exists for {rev_exists}/{n_sample} ({_pct(rev_exists, n_sample)})")
    else:
        print("symmetry check skipped: no SIMILAR_TO edges")


if __name__ == "__main__":
    main()

