"""
Conversationless multi-agent orchestrator with patch-scoped writes.
"""

from __future__ import annotations

import traceback
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from src.agents.base import CaseAgent
from src.core.artifacts import StepArtifactWriter
from src.core.hashing import sha256_json
from src.core.patching import PatchValidationError, apply_patch, validate_patch
from src.core.types import AgentContext, AgentResult
from src.orchestration.policies import gate_allows_reasoning


FINAL_STATUSES = {"success", "partial", "stubbed"}


class Orchestrator:
    def __init__(self, *, agents: Sequence[CaseAgent], ctx: AgentContext):
        self.agents = list(agents)
        self.ctx = ctx

    @staticmethod
    def _agent_run_exists(case: Dict[str, Any], agent_name: str, idempotency_key: str) -> bool:
        for row in case.get("agent_runs", []) or []:
            if not isinstance(row, dict):
                continue
            if str(row.get("agent_name")) != agent_name:
                continue
            if str(row.get("idempotency_key") or "") != idempotency_key:
                continue
            if str(row.get("status") or "") in FINAL_STATUSES:
                return True
        return False

    def _run_record(
        self,
        *,
        agent: CaseAgent,
        inputs_hash: str,
        idempotency_key: str,
        status: str,
        warnings: List[str],
        artifacts: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return {
            "agent_name": agent.name,
            "version": agent.version,
            "status": status,
            "started_at": self._now(),
            "ended_at": self._now(),
            "inputs_hash": inputs_hash,
            "idempotency_key": idempotency_key,
            "artifacts": artifacts,
            "warnings": warnings,
        }

    @staticmethod
    def _now() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _execute_agent(
        self,
        *,
        idx: int,
        case: Dict[str, Any],
        agent: CaseAgent,
        force_skip_reason: str = "",
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        case_before = deepcopy(case)
        inputs = agent.build_inputs(case_before, self.ctx)
        inputs_hash = sha256_json(inputs)
        idempotency_key = sha256_json(
            {
                "case_id": case_before.get("case_id"),
                "agent_name": agent.name,
                "agent_version": agent.version,
                "inputs_hash": inputs_hash,
            }
        )

        step_dir = Path(self.ctx.run_dir) / f"{idx:02d}_{agent.name}"
        writer = StepArtifactWriter(step_dir)

        if force_skip_reason:
            result = AgentResult(
                patch=[],
                status="skipped",
                warnings=[force_skip_reason],
                raw_outputs={"skip": {"reason": force_skip_reason}},
            )
        elif (not self.ctx.force) and self._agent_run_exists(case_before, agent.name, idempotency_key):
            result = AgentResult(
                patch=[],
                status="skipped",
                warnings=["idempotency_hit"],
                raw_outputs={"skip": {"reason": "idempotency_hit"}},
            )
        else:
            try:
                result = agent.run(case_before, self.ctx, inputs)
            except Exception as exc:  # keep loop alive; audit failure as run record
                result = AgentResult(
                    patch=[],
                    status=agent.status_on_exception(),
                    warnings=[f"agent_exception:{exc}"],
                    raw_outputs={"exception": {"error": str(exc), "traceback": traceback.format_exc()}},
                )

        patch = list(result.patch or [])
        run_record = self._run_record(
            agent=agent,
            inputs_hash=inputs_hash,
            idempotency_key=idempotency_key,
            status=result.status,
            warnings=list(result.warnings or []),
            artifacts=[{"kind": "step_artifacts", "path": str(step_dir)}],
        )
        patch.append({"op": "add", "path": "/agent_runs/-", "value": run_record})

        validate_patch(
            patch,
            allowed_prefixes=agent.allowed_patch_prefixes,
            append_only_prefixes=agent.append_only_prefixes,
        )
        case_after = apply_patch(case_before, patch)

        artifact_paths = writer.write_case_bundle(
            input_snapshot=inputs,
            raw_outputs=result.raw_outputs or {},
            patch=patch,
            case_before=case_before,
            case_after=case_after,
        )

        step_summary = {
            "agent": agent.name,
            "status": result.status,
            "warnings": list(result.warnings or []),
            "idempotency_key": idempotency_key,
            "inputs_hash": inputs_hash,
            "step_dir": str(step_dir),
            "artifact_paths": artifact_paths,
            "patch_ops": len(patch),
        }
        return case_after, step_summary

    def run(self, case: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        cur = deepcopy(case)
        cur.setdefault("agent_runs", [])
        cur.setdefault("action_plan", [])
        cur.setdefault("risk_scores", {})
        cur.setdefault("query", {})
        cur.setdefault("evidence_readiness", {})
        cur.setdefault("target_fields", {})
        cur.setdefault("target_fields_provenance", {})
        cur.setdefault("evidence_candidates_staging", [])
        cur.setdefault("current_gate", {})
        cur.setdefault("post_uq", {})

        summaries: List[Dict[str, Any]] = []
        for idx, agent in enumerate(self.agents, start=1):
            if agent.name == "reasoning_agent" and not gate_allows_reasoning(cur):
                cur, step_summary = self._execute_agent(
                    idx=idx,
                    case=cur,
                    agent=agent,
                    force_skip_reason="gate_blocked_reasoning",
                )
            else:
                cur, step_summary = self._execute_agent(idx=idx, case=cur, agent=agent)
            summaries.append(step_summary)

        summary = {
            "run_id": self.ctx.run_id,
            "run_dir": str(self.ctx.run_dir),
            "steps": summaries,
            "final_gate": cur.get("current_gate"),
            "agent_runs_total": len(cur.get("agent_runs", [])),
        }
        return cur, summary

