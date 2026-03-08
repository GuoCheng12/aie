import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

import src.graph.retrieval as retrieval


def _write_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def _make_toy_graph(tmp_path: Path) -> None:
    # Molecules
    A = "A"
    B1 = "B1"
    B2 = "B2"
    B3 = "B3"
    C = "C"

    # Evidence IDs (stable strings)
    e_a1 = "e_a_atb_1"
    e_a2 = "e_a_atb_2"
    e_a_obs = "e_a_obs_1"
    e_b1_1 = "e_b1_obs_1"
    e_b1_2 = "e_b1_obs_2"
    e_b2_1 = "e_b2_atb_1"
    e_c_1 = "e_c_obs_1"

    # Evidence table (minimal; retrieval uses evidence_id/evidence_type/field)
    ev_rows = [
        # Target A has computation + observation (retrieval should pick computation)
        {"evidence_id": e_a1, "subject_inchikey": A, "evidence_type": "atb_computation", "field": "delta_gap"},
        {"evidence_id": e_a2, "subject_inchikey": A, "evidence_type": "atb_computation", "field": "delta_volume"},
        {"evidence_id": e_a_obs, "subject_inchikey": A, "evidence_type": "private_observation", "field": "emission_sol"},
        # Neighbor B1 has only observation
        {"evidence_id": e_b1_1, "subject_inchikey": B1, "evidence_type": "private_observation", "field": "emission_aggr"},
        {"evidence_id": e_b1_2, "subject_inchikey": B1, "evidence_type": "private_observation", "field": "absorption_peak_nm"},
        # Neighbor B2 has computation
        {"evidence_id": e_b2_1, "subject_inchikey": B2, "evidence_type": "atb_computation", "field": "excitation_energy"},
        # Molecule C has only observation
        {"evidence_id": e_c_1, "subject_inchikey": C, "evidence_type": "private_observation", "field": "qy_sol"},
    ]

    def full_row(r):
        base = {
            "value_num": None,
            "value": None,
            "unit": None,
            "condition_state": "sol",
            "condition_solvent": "THF",
            "source_type": "private_db",
            "source_id": "1",
            "timestamp": "2026-01-01T00:00:00",
            "timestamp_source": None,
            "confidence": 1.0,
            "extraction_method": "test",
            "quality_flag": "OK",
            "quality_score": 1.0,
        }
        base.update(r)
        if base["evidence_type"] == "atb_computation":
            base["source_type"] = "atb_cache"
            base["source_id"] = base["subject_inchikey"]
            base["condition_state"] = "unknown"
            base["condition_solvent"] = "unknown"
        return base

    evidence = pd.DataFrame([full_row(r) for r in ev_rows])

    # Nodes
    nodes = []
    for ik in [A, B1, B2, B3, C]:
        nodes.append({
            "node_id": f"mol:{ik}",
            "node_type": "Molecule",
            "key": ik,
            "props_json": json.dumps({"inchikey": ik}, sort_keys=True),
        })
    for r in ev_rows:
        eid = r["evidence_id"]
        et = r["evidence_type"]
        field = r["field"]
        nodes.append({
            "node_id": f"ev:{eid}",
            "node_type": "Evidence",
            "key": eid,
            "props_json": json.dumps({"evidence_type": et, "field": field}, sort_keys=True),
        })
    # Conditions (sol/THF and unknown/unknown)
    for cid, props in [
        ("cond:sol:THF", {"condition_state": "sol", "condition_solvent": "THF"}),
        ("cond:unknown:unknown", {"condition_state": "unknown", "condition_solvent": "unknown"}),
    ]:
        nodes.append({
            "node_id": cid,
            "node_type": "Condition",
            "key": cid,
            "props_json": json.dumps(props, sort_keys=True),
        })
    nodes_df = pd.DataFrame(nodes)

    # Edges
    edges = []
    # A evidence edges
    edges.append({"src_id": "mol:A", "rel_type": "HAS_COMPUTATION", "dst_id": f"ev:{e_a1}", "weight": None, "evidence_id": e_a1, "props_json": json.dumps({"field": "delta_gap"})})
    edges.append({"src_id": "mol:A", "rel_type": "HAS_COMPUTATION", "dst_id": f"ev:{e_a2}", "weight": None, "evidence_id": e_a2, "props_json": json.dumps({"field": "delta_volume"})})
    edges.append({"src_id": "mol:A", "rel_type": "HAS_OBSERVATION", "dst_id": f"ev:{e_a_obs}", "weight": None, "evidence_id": e_a_obs, "props_json": json.dumps({"field": "emission_sol"})})
    # B1 evidence edges
    edges.append({"src_id": "mol:B1", "rel_type": "HAS_OBSERVATION", "dst_id": f"ev:{e_b1_1}", "weight": None, "evidence_id": e_b1_1, "props_json": json.dumps({"field": "emission_aggr"})})
    edges.append({"src_id": "mol:B1", "rel_type": "HAS_OBSERVATION", "dst_id": f"ev:{e_b1_2}", "weight": None, "evidence_id": e_b1_2, "props_json": json.dumps({"field": "absorption_peak_nm"})})
    # B2 computation edge
    edges.append({"src_id": "mol:B2", "rel_type": "HAS_COMPUTATION", "dst_id": f"ev:{e_b2_1}", "weight": None, "evidence_id": e_b2_1, "props_json": json.dumps({"field": "excitation_energy"})})
    # C observation edge
    edges.append({"src_id": "mol:C", "rel_type": "HAS_OBSERVATION", "dst_id": f"ev:{e_c_1}", "weight": None, "evidence_id": e_c_1, "props_json": json.dumps({"field": "qy_sol"})})

    # UNDER_CONDITION edges for each evidence
    def cond_for(etype):
        return "cond:unknown:unknown" if etype == "atb_computation" else "cond:sol:THF"

    for r in ev_rows:
        cid = cond_for(r["evidence_type"])
        eid = r["evidence_id"]
        edges.append({"src_id": f"ev:{eid}", "rel_type": "UNDER_CONDITION", "dst_id": cid, "weight": None, "evidence_id": eid, "props_json": json.dumps({})})

    # SIMILAR_TO edges from A (tie on weight for B2/B3 to test dst_id tie-break)
    edges.append({"src_id": "mol:A", "rel_type": "SIMILAR_TO", "dst_id": "mol:B1", "weight": 0.9, "evidence_id": None, "props_json": json.dumps({"rank": 1, "metric": "tanimoto_ecfp"})})
    edges.append({"src_id": "mol:A", "rel_type": "SIMILAR_TO", "dst_id": "mol:B2", "weight": 0.8, "evidence_id": None, "props_json": json.dumps({"rank": 2, "metric": "tanimoto_ecfp"})})
    edges.append({"src_id": "mol:A", "rel_type": "SIMILAR_TO", "dst_id": "mol:B3", "weight": 0.8, "evidence_id": None, "props_json": json.dumps({"rank": 3, "metric": "tanimoto_ecfp"})})

    edges_df = pd.DataFrame(edges)

    _write_parquet(nodes_df, tmp_path / "graph_nodes.parquet")
    _write_parquet(edges_df, tmp_path / "graph_edges.parquet")
    _write_parquet(evidence, tmp_path / "evidence_table.parquet")


class TestGraphRetrievalV1P3(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_nodes = retrieval.DEFAULT_NODES_PATH
        self._orig_edges = retrieval.DEFAULT_EDGES_PATH
        self._orig_evidence = retrieval.DEFAULT_EVIDENCE_PATH

    def tearDown(self) -> None:
        retrieval.DEFAULT_NODES_PATH = self._orig_nodes
        retrieval.DEFAULT_EDGES_PATH = self._orig_edges
        retrieval.DEFAULT_EVIDENCE_PATH = self._orig_evidence
        retrieval.reset_cache()

    def _use_tmp_graph(self, tmp_path: Path) -> None:
        retrieval.DEFAULT_NODES_PATH = str(tmp_path / "graph_nodes.parquet")
        retrieval.DEFAULT_EDGES_PATH = str(tmp_path / "graph_edges.parquet")
        retrieval.DEFAULT_EVIDENCE_PATH = str(tmp_path / "evidence_table.parquet")
        retrieval.reset_cache()

    def test_missing_inchikey_returns_empty_subgraph(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make_toy_graph(tmp)
            self._use_tmp_graph(tmp)
            sg = retrieval.get_subgraph("MISSING", max_nodes=50, max_edges=200)
            self.assertTrue(sg["stats"]["missing_molecule"])
            self.assertEqual(sg["nodes"], [])
            self.assertEqual(sg["edges"], [])

    def test_budget_enforcement_max_nodes_max_edges(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make_toy_graph(tmp)
            self._use_tmp_graph(tmp)
            sg = retrieval.get_subgraph("A", max_nodes=2, max_edges=1, hops=2, max_neighbors=10, per_neighbor_evidence_cap=5)
            self.assertLessEqual(len(sg["nodes"]), 2)
            self.assertLessEqual(len(sg["edges"]), 1)
            self.assertTrue(sg["stats"]["truncated"])

    def test_deterministic_output_ordering(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make_toy_graph(tmp)
            self._use_tmp_graph(tmp)
            sg1 = retrieval.get_subgraph("A", max_nodes=50, max_edges=200, hops=2, max_neighbors=3, per_neighbor_evidence_cap=2)
            sg2 = retrieval.get_subgraph("A", max_nodes=50, max_edges=200, hops=2, max_neighbors=3, per_neighbor_evidence_cap=2)
            self.assertEqual(sg1, sg2)

    def test_provenance_refs_match_included_evidence_edges(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make_toy_graph(tmp)
            self._use_tmp_graph(tmp)
            sg = retrieval.get_subgraph("A", max_nodes=50, max_edges=200, hops=2, max_neighbors=2, per_neighbor_evidence_cap=1)
            ev_ids = sorted([n["node_id"].replace("ev:", "", 1) for n in sg["nodes"] if n["node_type"] == "Evidence"])
            self.assertEqual(sg["provenance_refs"], ev_ids)

    def test_neighbor_caps_enforced(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make_toy_graph(tmp)
            self._use_tmp_graph(tmp)
            sg = retrieval.get_subgraph("A", max_nodes=50, max_edges=200, hops=2, max_neighbors=1, per_neighbor_evidence_cap=1)

            sim_edges = [e for e in sg["edges"] if e["rel"] == "SIMILAR_TO"]
            self.assertLessEqual(len(sim_edges), 1)

            if sim_edges:
                nb_mol = sim_edges[0]["dst"]
                nb_evidence_edges = [
                    e
                    for e in sg["edges"]
                    if e["src"] == nb_mol and e["rel"] in {"HAS_OBSERVATION", "HAS_COMPUTATION", "HAS_EVIDENCECLAIM"}
                ]
                self.assertLessEqual(len(nb_evidence_edges), 1)


if __name__ == "__main__":
    unittest.main()

