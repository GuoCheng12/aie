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
from src.agents.structure_agent import StructureAgent


def build_default_agents(*, neighbor_topk: int = 10, data_dir: str = "data") -> List[CaseAgent]:
    """
    Default execution order:
      1) Structure
      2) Data
      3) Chem
      4) Ready
      5) Reasoning (conditional)
      6) Judge
      7) Ready (final)
    """
    return [
        StructureAgent(data_dir=data_dir),
        DataCaseAgent(data_dir=data_dir, top_k=int(neighbor_topk)),
        ChemAgent(),
        ReadyAgent(),
        ReasoningAgent(use_llm=True),
        JudgeAgent(use_llm=False),
        ReadyAgent(),
    ]


def build_setup_agents(*, neighbor_topk: int = 10, data_dir: str = "data") -> List[CaseAgent]:
    """
    Setup-only execution order for iterative round runner:
      1) Structure
      2) Data
      3) Chem
      4) Ready
    """
    return [
        StructureAgent(data_dir=data_dir),
        DataCaseAgent(data_dir=data_dir, top_k=int(neighbor_topk)),
        ChemAgent(),
        ReadyAgent(),
    ]
