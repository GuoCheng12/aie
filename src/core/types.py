"""
Shared dataclasses/types for multi-agent orchestration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


AgentPatch = List[Dict[str, Any]]


@dataclass
class AgentContext:
    run_id: str
    run_dir: Path
    case_path: Path
    base_url: str
    model: str
    llm_api_key_env: str = "OPENAI_API_KEY"
    llm_max_output_tokens: int = 1500
    llm_reasoning_effort: Optional[str] = None
    mineru_bin: str = "third_party/MinerU/.venv/bin/mineru"
    mineru_output_root: Path = Path("third_party/MinerU/output")
    mineru_backend: str = "hybrid-auto-engine"
    mineru_method: Optional[str] = None
    mineru_lang: Optional[str] = None
    mineru_start_page: Optional[int] = None
    mineru_end_page: Optional[int] = None
    mineru_timeout_sec: int = 1200
    force: bool = False


@dataclass
class AgentResult:
    patch: AgentPatch = field(default_factory=list)
    status: str = "success"
    warnings: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    raw_outputs: Dict[str, Any] = field(default_factory=dict)

