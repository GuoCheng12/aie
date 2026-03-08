"""
Agent registry for the multi-agent loop.
"""

from __future__ import annotations

from typing import List

from src.agents.base import CaseAgent
from src.agents.chem_agent import ChemAgent
from src.agents.data_agent import DataCaseAgent
from src.agents.judge_agent import JudgeAgent
from src.agents.reasoning_agent import ReasoningAgent
from src.agents.ready_agent import ReadyAgent


def build_default_agents(*, neighbor_topk: int = 10) -> List[CaseAgent]:
    """
    Default execution order:
      1) Data
      2) Chem
      3) Ready
      4) Reasoning (conditional)
      5) Judge
      6) Ready (final)
    """
    return [
        DataCaseAgent(top_k=int(neighbor_topk)),
        ChemAgent(),
        ReadyAgent(),
        ReasoningAgent(use_llm=True),
        JudgeAgent(use_llm=False),
        ReadyAgent(),
    ]


def build_setup_agents(*, neighbor_topk: int = 10) -> List[CaseAgent]:
    """
    Setup-only execution order for iterative round runner:
      1) Data
      2) Chem
      3) Ready
    """
    return [
        DataCaseAgent(top_k=int(neighbor_topk)),
        ChemAgent(),
        ReadyAgent(),
    ]
