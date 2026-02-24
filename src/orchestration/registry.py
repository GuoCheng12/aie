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


def build_default_agents() -> List[CaseAgent]:
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
        DataCaseAgent(),
        ChemAgent(),
        ReadyAgent(),
        ReasoningAgent(use_llm=True),
        JudgeAgent(use_llm=False),
        ReadyAgent(),
    ]

