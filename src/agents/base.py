"""
Base contract for orchestration agents.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Sequence

from src.core.types import AgentContext, AgentResult


class CaseAgent(ABC):
    name: str = "base_agent"
    version: str = "0.0.1"
    allowed_patch_prefixes: Sequence[str] = ()
    append_only_prefixes: Sequence[str] = ()

    @abstractmethod
    def build_inputs(self, case: Dict[str, Any], ctx: AgentContext) -> Dict[str, Any]:
        pass

    @abstractmethod
    def run(self, case: Dict[str, Any], ctx: AgentContext, inputs: Dict[str, Any]) -> AgentResult:
        pass

    def status_on_exception(self) -> str:
        return "failed"

