"""
Run-scoped LLM trace persistence helpers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.core.types import AgentContext
from src.core.safe_fs import safe_write_text


def _run_dir(ctx: AgentContext) -> Path:
    base = Path(ctx.llm_response_dir)
    out = base if bool(getattr(ctx, "llm_response_run_scoped", False)) else (base / str(ctx.run_id))
    out.mkdir(parents=True, exist_ok=True)
    return out


def _rounds_dir(ctx: AgentContext) -> Path:
    explicit = getattr(ctx, "llm_rounds_dir", None)
    if explicit is not None:
        out = Path(explicit)
    else:
        out = _run_dir(ctx) / "rounds"
    out.mkdir(parents=True, exist_ok=True)
    return out


def resolve_run_trace_dir(ctx: AgentContext, *, create: bool = True) -> Path:
    out = Path(ctx.llm_response_dir)
    if not bool(getattr(ctx, "llm_response_run_scoped", False)):
        out = out / str(ctx.run_id)
    if create:
        out.mkdir(parents=True, exist_ok=True)
    return out


def resolve_rounds_trace_dir(ctx: AgentContext, *, create: bool = True) -> Path:
    explicit = getattr(ctx, "llm_rounds_dir", None)
    if explicit is not None:
        out = Path(explicit)
    else:
        out = resolve_run_trace_dir(ctx, create=create) / "rounds"
    if create:
        out.mkdir(parents=True, exist_ok=True)
    return out


def write_agent_response_trace(
    *,
    ctx: AgentContext,
    agent_name: str,
    payload: Dict[str, Any],
) -> str:
    out = _run_dir(ctx) / f"{ctx.run_id}.{agent_name}.response.json"
    safe_write_text(out, json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(out)


def write_master_round_report(*, ctx: AgentContext, round_index: int, payload: Dict[str, Any]) -> str:
    out = _rounds_dir(ctx) / f"round_{int(round_index):02d}.master_round_report.json"
    safe_write_text(out, json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(out)


def write_eval_report(*, ctx: AgentContext, round_index: int, payload: Dict[str, Any]) -> str:
    out = _rounds_dir(ctx) / f"round_{int(round_index):02d}.eval_report.json"
    safe_write_text(out, json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(out)


def write_round_state(*, ctx: AgentContext, round_index: int, payload: Dict[str, Any]) -> str:
    out = _rounds_dir(ctx) / f"round_{int(round_index):02d}.round_state.json"
    safe_write_text(out, json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(out)


def build_reasoning_five_signals(
    *,
    run_id: str,
    case_id: str,
    status: str,
    model: str,
    reasoning_effort: Optional[str],
    parsed: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    p = parsed if isinstance(parsed, dict) else {}
    mechanism_claim = p.get("mechanism_claim") if isinstance(p.get("mechanism_claim"), dict) else {}
    primary = mechanism_claim.get("primary_hypothesis") if isinstance(mechanism_claim.get("primary_hypothesis"), dict) else {}

    def _collect_evidence_pool(obj: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        pool: Dict[str, Dict[str, Any]] = {}

        def _push(rows: Any) -> None:
            if not isinstance(rows, list):
                return
            for row in rows:
                if not isinstance(row, dict):
                    continue
                evidence_id = str(row.get("evidence_id") or "").strip()
                if not evidence_id:
                    continue
                rec = pool.setdefault(evidence_id, {"notes": [], "roles": []})
                note = str(row.get("note") or "").strip()
                role = str(row.get("role") or "").strip()
                if note and note not in rec["notes"]:
                    rec["notes"].append(note)
                if role and role not in rec["roles"]:
                    rec["roles"].append(role)

        _push(obj.get("evidence_used"))
        for key in ("supporting_chain", "competing_hypotheses", "predictions"):
            for row in obj.get(key) or []:
                if isinstance(row, dict):
                    _push(row.get("evidence_used"))
        return pool

    def _compose_mechanism_narrative() -> str:
        mech_label = str(primary.get("mechanism_label") or "unknown")
        rationale = str(primary.get("aie_rationale_type") or "mixed")
        base = str(primary.get("natural_language_mechanism") or "").strip()
        p1 = (
            f"Best current hypothesis under available evidence is {mech_label} with {rationale} rationale. "
            f"{base if base else 'This assignment is driven by current aTB and prior context, without external write-back evidence.'}"
        )
        p2 = (
            "The boundary between the top competing mechanisms remains unresolved under current evidence coverage: "
            "available cues support multiple plausible pathways, so additional discriminators are recommended."
        )
        pred_rows = [str((x or {}).get("prediction") or "").strip() for x in (p.get("predictions") or []) if isinstance(x, dict)]
        pred_rows = [x for x in pred_rows if x][:3]
        if pred_rows:
            p3 = "Falsifiable next tests should separate hypotheses by targeted readouts: " + "; ".join(pred_rows) + "."
        else:
            p3 = (
                "Falsifiable next tests should separate the top competing hypotheses using orthogonal perturbations and direct "
                "spectro-kinetic measurements."
            )
        return f"{p1}\n\n{p2}\n\n{p3}"

    evidence_pool = _collect_evidence_pool(p)

    def _note_from_pool(evidence_id: str, fallback: str) -> str:
        rec = evidence_pool.get(evidence_id) or {}
        notes = rec.get("notes") or []
        note = str(notes[0]).strip() if notes else ""
        return note or fallback

    chain_specs: List[Tuple[str, str, str, bool]] = [
        # uncertainty bounds
        ("E2", "context", "Gate reasoning mode constrains confidence and keeps mechanistic claims conservative.", False),
        ("E4", "context", "Top similarity acts as an uncertainty bound on neighbor-prior transfer.", False),
        ("E6", "context", "Mechanism entropy indicates ambiguity in neighbor-label evidence.", False),
        # aTB cues
        ("E11", "support", "aTB torsional cue directly affects discrimination among top competing mechanisms.", False),
        ("E12", "context", "aTB gap shift provides CT-family context but is not standalone mechanism proof.", False),
        ("E14", "context", "Excitation-energy cue refines plausibility across competing CT-family hypotheses.", True),
        # missing discriminators
        ("E19", "context", "Missing literature validation keeps mechanism assignment provisional.", False),
        ("E20", "context", "Missing experimental discriminator prevents decisive hypothesis separation.", False),
        ("E10", "context", "aTB cache/readiness status bounds reliability of current feature evidence.", True),
    ]

    competing: List[Dict[str, Any]] = []
    for row in p.get("competing_hypotheses") or []:
        if not isinstance(row, dict):
            continue
        competing.append(
            {
                "name": row.get("name"),
                "confidence": row.get("confidence"),
                "evidence_used": row.get("evidence_used") if isinstance(row.get("evidence_used"), list) else [],
            }
        )

    evidence_chain: List[Dict[str, Any]] = []
    for evidence_id, role, fallback_note, optional in chain_specs:
        if optional and evidence_id not in evidence_pool:
            continue
        evidence_chain.append(
            {
                "evidence_id": evidence_id,
                "role": role,
                "note": _note_from_pool(evidence_id, fallback_note),
            }
        )

    # Fill to minimum 8 items with remaining evidence IDs already produced by model output.
    if len(evidence_chain) < 8:
        seen = {str(x.get("evidence_id") or "") for x in evidence_chain}
        for evidence_id in sorted(evidence_pool.keys(), key=lambda x: int(x[1:]) if x.startswith("E") and x[1:].isdigit() else x):
            if evidence_id in seen:
                continue
            role = str((evidence_pool.get(evidence_id) or {}).get("roles", ["context"])[0] or "context")
            evidence_chain.append(
                {
                    "evidence_id": evidence_id,
                    "role": role if role in {"support", "counter", "context"} else "context",
                    "note": _note_from_pool(evidence_id, "This evidence contributes to mechanism inference uncertainty bounds."),
                }
            )
            seen.add(evidence_id)
            if len(evidence_chain) >= 8:
                break

    evidence_chain = evidence_chain[:12]

    return {
        "run_id": run_id,
        "case_id": case_id,
        "agent": "reasoning_agent",
        "status": status,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "five_signals": {
            "conclusion": {
                "status": p.get("status"),
                "template_used": p.get("template_used"),
                "mechanism_label": primary.get("mechanism_label"),
                "aie_rationale_type": primary.get("aie_rationale_type"),
                "natural_language_mechanism": _compose_mechanism_narrative(),
            },
            "confidence": {
                "value": mechanism_claim.get("confidence"),
                "reasoning_mode_used": mechanism_claim.get("reasoning_mode_used"),
            },
            "competing_hypotheses": competing,
            "evidence_chain": evidence_chain,
            "limits_and_next_actions": {
                "limits": p.get("limits") if isinstance(p.get("limits"), list) else [],
                "recommended_next_actions": p.get("recommended_next_actions")
                if isinstance(p.get("recommended_next_actions"), list)
                else [],
            },
        },
    }


def write_reasoning_five_signals(
    *,
    ctx: AgentContext,
    payload: Dict[str, Any],
) -> str:
    out = _run_dir(ctx) / f"{ctx.run_id}.reasoning_agent.summary5.json"
    safe_write_text(out, json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(out)
