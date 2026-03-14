"""StructureAgent: SMILES-first structure priors for R0 candidate generation."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from src.agents.base import CaseAgent
from src.cases.create_case_from_smiles import canonicalize_smiles
from src.core.types import AgentContext, AgentResult
from src.data.rdkit_descriptors import compute_basic_descriptors
from src.reasoning.r0_prior_profiles import compute_structure_fact_sheet
from src.reasoning.structure_prior_profile import compute_structure_prior_profile
from src.structure.motif_detector import detect_structure_motifs
from src.structure.scaffold_retrieval import compute_scaffold_neighbors
from src.structure.structure_classifier import (
    DEFAULT_MODEL_ROOT,
    build_reference_rows,
    feature_neighbors,
    retrieval_fallback_candidate_distribution,
)


class StructureAgent(CaseAgent):
    name = "structure_agent"
    version = "1.0.0"
    allowed_patch_prefixes = (
        "/risk_scores/",
        "/agent_runs/-",
    )
    append_only_prefixes = ("/agent_runs",)

    def __init__(
        self,
        *,
        data_dir: str = "data",
        classifier_model_root: str | Path = DEFAULT_MODEL_ROOT,
        retrieval_topk: int = 10,
        candidate_topk: int = 5,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.classifier_model_root = Path(classifier_model_root)
        self.retrieval_topk = int(retrieval_topk)
        self.candidate_topk = int(candidate_topk)
        self._reference_rows: Optional[List[Dict[str, Any]]] = None

    def _load_reference_rows(self) -> List[Dict[str, Any]]:
        if self._reference_rows is None:
            self._reference_rows = build_reference_rows(data_dir=self.data_dir)
        return self._reference_rows

    def build_inputs(self, case: Dict[str, Any], ctx: AgentContext) -> Dict[str, Any]:
        query = case.get("query") or {}
        return {
            "case_id": case.get("case_id"),
            "input_smiles": query.get("input_smiles"),
            "classifier_model_root": str(self.classifier_model_root),
            "retrieval_topk": self.retrieval_topk,
            "candidate_topk": self.candidate_topk,
            "data_dir": str(self.data_dir),
        }

    def run(self, case: Dict[str, Any], ctx: AgentContext, inputs: Dict[str, Any]) -> AgentResult:
        smiles = str(inputs.get("input_smiles") or "").strip()
        if not smiles:
            return AgentResult(
                patch=[],
                status="failed",
                warnings=["missing_input_smiles"],
                raw_outputs={"structure_agent_raw": {"error": "missing_input_smiles"}},
            )

        canonical_smiles, inchikey = canonicalize_smiles(smiles)
        if canonical_smiles is None:
            return AgentResult(
                patch=[],
                status="failed",
                warnings=["invalid_smiles"],
                raw_outputs={"structure_agent_raw": {"error": "invalid_smiles", "input_smiles": smiles}},
            )

        descriptors = compute_basic_descriptors(canonical_smiles)
        structure_prior_profile = compute_structure_prior_profile(canonical_smiles, descriptors)
        structure_motif_profile = detect_structure_motifs(canonical_smiles, descriptors)
        structure_fact_sheet = compute_structure_fact_sheet(structure_prior_profile, structure_motif_profile)

        reference_rows = self._load_reference_rows()
        target_record = {
            "inchikey": inchikey,
            "canonical_smiles": canonical_smiles,
            "descriptor_snapshot": descriptors,
            "structure_prior_profile": structure_prior_profile,
            "structure_motif_profile": structure_motif_profile,
        }
        from src.structure.feature_morgan import compute_feature_morgan_count, compute_morgan_count
        from src.structure.scaffold_retrieval import extract_murcko_scaffold

        scaffold_info = extract_murcko_scaffold(canonical_smiles)
        target_record.update(
            {
                "morgan_count": compute_morgan_count(canonical_smiles),
                "feature_morgan_count": compute_feature_morgan_count(canonical_smiles),
                "scaffold_info": scaffold_info,
                "murcko_scaffold_smiles": scaffold_info.get("murcko_scaffold_smiles"),
                "generic_scaffold_smiles": scaffold_info.get("generic_scaffold_smiles"),
            }
        )

        feature_topk = feature_neighbors(target_record, reference_rows, topk=self.retrieval_topk)
        scaffold_result = compute_scaffold_neighbors(
            canonical_smiles,
            reference_rows,
            topk=self.retrieval_topk,
            target_inchikey=inchikey,
            target_canonical_smiles=canonical_smiles,
        )
        feature_dist = self._weighted_distribution(feature_topk)
        scaffold_dist = scaffold_result.get("scaffold_neighbor_label_distribution") or {}
        consensus = self._consensus_strength(feature_dist, scaffold_dist)
        structure_retrieval_profile = {
            "version": "structure_retrieval_v1",
            "feature_morgan_topk": feature_topk,
            "murcko_topk": scaffold_result.get("murcko_topk") or [],
            "feature_neighbor_label_distribution": feature_dist,
            "scaffold_neighbor_label_distribution": scaffold_dist,
            "retrieval_consensus_strength": consensus,
        }

        structure_candidate_distribution = retrieval_fallback_candidate_distribution(
            feature_neighbors=feature_topk,
            scaffold_neighbors=structure_retrieval_profile["murcko_topk"],
            topk=self.candidate_topk,
        )
        structure_candidate_distribution.setdefault("version", "structure_candidate_dist_v1")

        patch = [
            {"op": "add", "path": "/risk_scores/structure_prior_profile", "value": structure_prior_profile},
            {"op": "add", "path": "/risk_scores/structure_motif_profile", "value": structure_motif_profile},
            {"op": "add", "path": "/risk_scores/structure_fact_sheet", "value": structure_fact_sheet},
            {"op": "add", "path": "/risk_scores/structure_retrieval_profile", "value": structure_retrieval_profile},
            {"op": "add", "path": "/risk_scores/structure_candidate_distribution", "value": structure_candidate_distribution},
        ]
        return AgentResult(
            patch=patch,
            status="success",
            warnings=[],
            raw_outputs={
                "structure_agent_raw": {
                    "canonical_smiles": canonical_smiles,
                    "inchikey": inchikey,
                    "rdkit_descriptors": descriptors,
                    "structure_prior_profile": deepcopy(structure_prior_profile),
                    "structure_motif_profile": deepcopy(structure_motif_profile),
                    "structure_fact_sheet": deepcopy(structure_fact_sheet),
                    "structure_retrieval_profile": deepcopy(structure_retrieval_profile),
                    "structure_candidate_distribution": deepcopy(structure_candidate_distribution),
                    "classifier_model_root": str(self.classifier_model_root),
                    "classifier_loaded": False,
                    "candidate_distribution_mode": "retrieval_fallback_only",
                    "reference_data_dir": str(self.data_dir),
                }
            },
        )

    @staticmethod
    def _weighted_distribution(rows: List[Mapping[str, Any]]) -> Dict[str, float]:
        totals: Dict[str, float] = {}
        total_weight = 0.0
        for row in rows:
            label = str(row.get("mechanism_label") or "unknown")
            weight = float(row.get("sim") or 0.0)
            totals[label] = totals.get(label, 0.0) + weight
            total_weight += weight
        if total_weight <= 0.0:
            return {}
        return {k: round(v / total_weight, 6) for k, v in sorted(totals.items(), key=lambda kv: kv[1], reverse=True)}

    @staticmethod
    def _consensus_strength(feature_dist: Mapping[str, float], scaffold_dist: Mapping[str, float]) -> str:
        f_top = next(iter(feature_dist.items()), (None, 0.0))
        s_top = next(iter(scaffold_dist.items()), (None, 0.0))
        if f_top[0] and s_top[0] and f_top[0] == s_top[0] and f_top[1] >= 0.45 and s_top[1] >= 0.45:
            return "high"
        if max(float(f_top[1]), float(s_top[1])) >= 0.35:
            return "mid"
        return "low"


__all__ = ["StructureAgent"]
