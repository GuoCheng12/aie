"""
Shared dataclasses/types for multi-agent orchestration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


AgentPatch = List[Dict[str, Any]]

SKIPPED_REASON_IDEMPOTENCY_HIT = "idempotency_hit"
SKIPPED_REASON_GATE_BLOCKED_REASONING = "gate_blocked_reasoning"
SKIPPED_REASON_LANE_DISABLED = "lane_disabled"
SKIPPED_REASON_MISSING_REQUIRED_INPUT = "missing_required_input"
SKIPPED_REASON_UPSTREAM_FAILED = "upstream_failed"
SKIPPED_REASON_NOT_APPLICABLE = "not_applicable"

SKIPPED_REASON_CODES = {
    SKIPPED_REASON_IDEMPOTENCY_HIT,
    SKIPPED_REASON_GATE_BLOCKED_REASONING,
    SKIPPED_REASON_LANE_DISABLED,
    SKIPPED_REASON_MISSING_REQUIRED_INPUT,
    SKIPPED_REASON_UPSTREAM_FAILED,
    SKIPPED_REASON_NOT_APPLICABLE,
}


@dataclass
class AgentContext:
    run_id: str
    run_dir: Path
    case_path: Path
    base_url: str
    model: str
    llm_api_key_env: str = "OPENAI_API_KEY"
    llm_max_output_tokens: int = 1500
    llm_reasoning_effort: Optional[str] = "medium"
    llm_temperature: float = 0.2
    llm_use_json_schema: bool = False
    llm_response_dir: Path = Path("artifacts/llm_responses")
    llm_response_run_scoped: bool = False
    llm_rounds_dir: Optional[Path] = None
    run_lane: str = "atb_cache_only"
    mineru_bin: str = "third_party/MinerU/.venv/bin/mineru"
    mineru_output_root: Path = Path("third_party/MinerU/output")
    mineru_backend: str = "hybrid-auto-engine"
    mineru_method: Optional[str] = None
    mineru_lang: Optional[str] = None
    mineru_start_page: Optional[int] = None
    mineru_end_page: Optional[int] = None
    mineru_timeout_sec: int = 1200
    force: bool = False
    status_path: Optional[Path] = None
    progress_round_index: int = 0
    progress_max_rounds: int = 1
    progress_active_profile: str = "single"

    def idempotency_scope(self) -> Dict[str, Any]:
        return {
            "run_lane": self.run_lane,
            "llm": {
                "base_url": self.base_url,
                "model": self.model,
                "api_key_env": self.llm_api_key_env,
                "max_output_tokens": self.llm_max_output_tokens,
                "reasoning_effort": self.llm_reasoning_effort,
                "temperature": self.llm_temperature,
                "use_json_schema": self.llm_use_json_schema,
            },
            "mineru": {
                "bin": self.mineru_bin,
                "output_root": str(self.mineru_output_root),
                "backend": self.mineru_backend,
                "method": self.mineru_method,
                "lang": self.mineru_lang,
                "start_page": self.mineru_start_page,
                "end_page": self.mineru_end_page,
                "timeout_sec": self.mineru_timeout_sec,
            },
        }


@dataclass
class AgentResult:
    patch: AgentPatch = field(default_factory=list)
    status: str = "success"
    status_reason_code: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    raw_outputs: Dict[str, Any] = field(default_factory=dict)
