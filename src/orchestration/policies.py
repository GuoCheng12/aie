"""
Orchestration policies for step transitions.
"""

from __future__ import annotations

from typing import Any, Dict


def gate_allows_reasoning(case: Dict[str, Any]) -> bool:
    gate = case.get("current_gate") or {}
    state = str(gate.get("state") or "")
    if state in {"ready_for_reasoning", "ready_conservative"}:
        return True
    return bool(gate.get("ready_for_reasoning") is True)

