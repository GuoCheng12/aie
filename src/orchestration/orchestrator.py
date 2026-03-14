"""
Conversationless multi-agent orchestrator with patch-scoped writes.
"""

from __future__ import annotations

import json
import traceback
from copy import deepcopy
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.agents.base import CaseAgent
from src.core.artifacts import StepArtifactWriter
from src.core.hashing import sha256_file, sha256_json
from src.core.patching import PatchValidationError, apply_patch, validate_patch
from src.core.types import (
    AgentContext,
    AgentResult,
    SKIPPED_REASON_CODES,
    SKIPPED_REASON_GATE_BLOCKED_REASONING,
    SKIPPED_REASON_IDEMPOTENCY_HIT,
    SKIPPED_REASON_NOT_APPLICABLE,
)
from src.orchestration.policies import gate_allows_reasoning
from src.orchestration.run_status import atomic_write_json, emit_progress_event, now_iso8601


FINAL_STATUSES = {"success", "partial", "stubbed"}
READY_AGENT_NAME = "ready_agent"
GATE_OWNER_PREFIXES = (
    "/current_gate",
    "/current_gate/",
    "/action_rationale",
    "/action_plan",
    "/action_plan/",
)
EVIDENCE_TABLE_PATH = Path("data/evidence_table.parquet")


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
        status_reason_code: Optional[str],
        warnings: List[str],
        artifacts: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return {
            "agent_name": agent.name,
            "version": agent.version,
            "status": status,
            "status_reason_code": status_reason_code,
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

    @staticmethod
    def _path_has_prefix(path: str, prefix: str) -> bool:
        return path == prefix or path.startswith(prefix)

    def _assert_gate_owner(self, agent_name: str, patch_ops: Sequence[Dict[str, Any]]) -> None:
        if agent_name == READY_AGENT_NAME:
            return
        for op in patch_ops:
            path = str(op.get("path") or "")
            if any(self._path_has_prefix(path, p) for p in GATE_OWNER_PREFIXES):
                raise PatchValidationError(f"gate_owner_violation:{agent_name}:{path}")

    @staticmethod
    def _evidence_table_hash(path: Path) -> Optional[str]:
        if not path.exists():
            return None
        return sha256_file(path)

    def _progress_stage(self, agent_name: str) -> str:
        return f"agent:{agent_name}"

    def _emit_agent_progress(
        self,
        *,
        agent_name: str,
        status: str,
        elapsed_ms: int,
        agent_index: int,
        agent_total: int,
    ) -> None:
        emit_progress_event(
            round_index=int(getattr(self.ctx, "progress_round_index", 0) or 0),
            max_rounds=int(getattr(self.ctx, "progress_max_rounds", 1) or 1),
            active_profile=str(getattr(self.ctx, "progress_active_profile", "single") or "single"),
            stage=self._progress_stage(agent_name),
            status=str(status),
            elapsed_ms=int(elapsed_ms),
            extra={
                "agent_index": int(agent_index),
                "agent_total": int(agent_total),
            },
        )

    def _write_status(
        self,
        *,
        agent_name: str,
        last_event: str,
        errors: Optional[List[Dict[str, str]]] = None,
    ) -> None:
        status_path = getattr(self.ctx, "status_path", None)
        if not status_path:
            return
        p = Path(status_path)
        prev_errors: List[Dict[str, str]] = []
        prev_eval_report: Optional[str] = None
        if p.exists():
            try:
                prev = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(prev, dict):
                    if isinstance(prev.get("errors"), list):
                        prev_errors = list(prev.get("errors") or [])
                    prev_eval_report = prev.get("latest_eval_report")
            except Exception:
                pass
        payload = {
            "run_id": self.ctx.run_id,
            "case_id": str(self.ctx.case_path.stem or ""),
            "round_index": int(getattr(self.ctx, "progress_round_index", 0) or 0),
            "max_rounds": int(getattr(self.ctx, "progress_max_rounds", 1) or 1),
            "active_profile": str(getattr(self.ctx, "progress_active_profile", "single") or "single"),
            "round_runner_mode": "setup_or_default",
            "stage": self._progress_stage(agent_name),
            "last_event": last_event,
            "last_updated_at": now_iso8601(),
            "errors": list(prev_errors if errors is None else (errors or [])),
            "round_dir": str(self.ctx.run_dir),
            "latest_eval_report": prev_eval_report,
        }
        atomic_write_json(p, payload)

    def _execute_agent(
        self,
        *,
        idx: int,
        case: Dict[str, Any],
        agent: CaseAgent,
        force_skip_reason: str = "",
    ) -> Tuple[Dict[str, Any], Dict[str, Any], bool]:
        case_before = deepcopy(case)
        inputs = agent.build_inputs(case_before, self.ctx)
        inputs_hash = sha256_json(inputs)
        run_config_hash = sha256_json(self.ctx.idempotency_scope())
        idempotency_key = sha256_json(
            {
                "case_id": case_before.get("case_id"),
                "agent_name": agent.name,
                "agent_version": agent.version,
                "inputs_hash": inputs_hash,
                "run_config_hash": run_config_hash,
            }
        )

        step_dir = Path(self.ctx.run_dir) / f"{idx:02d}_{agent.name}"
        writer = StepArtifactWriter(step_dir)

        if force_skip_reason:
            result = AgentResult(
                patch=[],
                status="skipped",
                status_reason_code=force_skip_reason,
                warnings=[force_skip_reason],
                raw_outputs={"skip": {"reason": force_skip_reason}},
            )
        elif (not self.ctx.force) and self._agent_run_exists(case_before, agent.name, idempotency_key):
            result = AgentResult(
                patch=[],
                status="skipped",
                status_reason_code=SKIPPED_REASON_IDEMPOTENCY_HIT,
                warnings=[SKIPPED_REASON_IDEMPOTENCY_HIT],
                raw_outputs={"skip": {"reason": SKIPPED_REASON_IDEMPOTENCY_HIT}},
            )
        else:
            try:
                result = agent.run(case_before, self.ctx, inputs)
            except Exception as exc:
                result = AgentResult(
                    patch=[],
                    status=agent.status_on_exception(),
                    warnings=[f"agent_exception:{exc}"],
                    raw_outputs={"exception": {"error": str(exc), "traceback": traceback.format_exc()}},
                )

        status_reason_code = result.status_reason_code
        if result.status == "skipped":
            if not status_reason_code:
                status_reason_code = SKIPPED_REASON_NOT_APPLICABLE
            if status_reason_code not in SKIPPED_REASON_CODES:
                raise PatchValidationError(f"invalid_skipped_reason_code:{status_reason_code}")

        patch = list(result.patch or [])
        run_record = self._run_record(
            agent=agent,
            inputs_hash=inputs_hash,
            idempotency_key=idempotency_key,
            status=result.status,
            status_reason_code=status_reason_code,
            warnings=list(result.warnings or []),
            artifacts=[{"kind": "step_artifacts", "path": str(step_dir)}],
        )
        patch.append({"op": "add", "path": "/agent_runs/-", "value": run_record})

        hard_fail = False
        try:
            self._assert_gate_owner(agent.name, patch)
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
                "status_reason_code": status_reason_code,
                "warnings": list(result.warnings or []),
                "idempotency_key": idempotency_key,
                "inputs_hash": inputs_hash,
                "run_config_hash": run_config_hash,
                "step_dir": str(step_dir),
                "artifact_paths": artifact_paths,
                "patch_ops": len(patch),
            }
            return case_after, step_summary, hard_fail
        except PatchValidationError as exc:
            hard_fail = True
            fail_warnings = list(result.warnings or []) + [f"patch_validation_failed:{exc}"]
            fail_record = self._run_record(
                agent=agent,
                inputs_hash=inputs_hash,
                idempotency_key=idempotency_key,
                status="failed",
                status_reason_code=None,
                warnings=fail_warnings,
                artifacts=[{"kind": "step_artifacts", "path": str(step_dir)}],
            )
            fallback_patch = [{"op": "add", "path": "/agent_runs/-", "value": fail_record}]
            validate_patch(
                fallback_patch,
                allowed_prefixes=agent.allowed_patch_prefixes,
                append_only_prefixes=agent.append_only_prefixes,
            )
            case_after = apply_patch(case_before, fallback_patch)
            raw_outputs = dict(result.raw_outputs or {})
            raw_outputs["patch_validation_error"] = {
                "error": str(exc),
                "attempted_patch": patch,
            }
            artifact_paths = writer.write_case_bundle(
                input_snapshot=inputs,
                raw_outputs=raw_outputs,
                patch=fallback_patch,
                case_before=case_before,
                case_after=case_after,
            )
            step_summary = {
                "agent": agent.name,
                "status": "failed",
                "status_reason_code": None,
                "warnings": fail_warnings,
                "idempotency_key": idempotency_key,
                "inputs_hash": inputs_hash,
                "run_config_hash": run_config_hash,
                "step_dir": str(step_dir),
                "artifact_paths": artifact_paths,
                "patch_ops": len(fallback_patch),
            }
            return case_after, step_summary, hard_fail

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

        ev_before_exists = EVIDENCE_TABLE_PATH.exists()
        ev_before_hash = self._evidence_table_hash(EVIDENCE_TABLE_PATH)

        summaries: List[Dict[str, Any]] = []
        for idx, agent in enumerate(self.agents, start=1):
            t_agent = perf_counter()
            self._emit_agent_progress(
                agent_name=agent.name,
                status="running",
                elapsed_ms=0,
                agent_index=idx,
                agent_total=len(self.agents),
            )
            self._write_status(agent_name=agent.name, last_event=f"{agent.name}_start", errors=None)
            if agent.name == "reasoning_agent" and not gate_allows_reasoning(cur):
                cur, step_summary, hard_fail = self._execute_agent(
                    idx=idx,
                    case=cur,
                    agent=agent,
                    force_skip_reason=SKIPPED_REASON_GATE_BLOCKED_REASONING,
                )
            else:
                cur, step_summary, hard_fail = self._execute_agent(idx=idx, case=cur, agent=agent)
            summaries.append(step_summary)
            err_rows: Optional[List[Dict[str, str]]] = None
            if str(step_summary.get("status") or "") == "failed":
                err_rows = [
                    {
                        "code": "agent_failed",
                        "path": f"/agent_runs/{idx - 1}",
                        "detail": "; ".join(str(x) for x in (step_summary.get("warnings") or []) if str(x)),
                    }
                ]
            self._emit_agent_progress(
                agent_name=agent.name,
                status=str(step_summary.get("status") or "completed"),
                elapsed_ms=int((perf_counter() - t_agent) * 1000),
                agent_index=idx,
                agent_total=len(self.agents),
            )
            self._write_status(
                agent_name=agent.name,
                last_event=f"{agent.name}_{step_summary.get('status') or 'completed'}",
                errors=err_rows,
            )
            if hard_fail:
                break

        ev_after_exists = EVIDENCE_TABLE_PATH.exists()
        ev_after_hash = self._evidence_table_hash(EVIDENCE_TABLE_PATH)
        if ev_before_exists != ev_after_exists:
            raise RuntimeError("evidence_table_no_touch_violation:file_presence_changed")
        if ev_before_hash != ev_after_hash:
            raise RuntimeError("evidence_table_no_touch_violation:content_hash_changed")

        summary = {
            "run_id": self.ctx.run_id,
            "run_dir": str(self.ctx.run_dir),
            "steps": summaries,
            "final_gate": cur.get("current_gate"),
            "agent_runs_total": len(cur.get("agent_runs", [])),
            "evidence_table_hash_before": ev_before_hash,
            "evidence_table_hash_after": ev_after_hash,
        }
        return cur, summary
