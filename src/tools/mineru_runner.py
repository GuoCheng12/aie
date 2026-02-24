"""
MinerU adapter used by Chem Agent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.cases.mineru_llm_extractor import resolve_or_run_mineru


class MineruRunner:
    def __init__(
        self,
        *,
        mineru_bin: str,
        output_root: Path,
        backend: str,
        method: Optional[str] = None,
        lang: Optional[str] = None,
        start_page: Optional[int] = None,
        end_page: Optional[int] = None,
        timeout_sec: int = 1200,
    ) -> None:
        self.mineru_bin = mineru_bin
        self.output_root = Path(output_root)
        self.backend = backend
        self.method = method
        self.lang = lang
        self.start_page = start_page
        self.end_page = end_page
        self.timeout_sec = int(timeout_sec)

    def resolve_bundle(self, pdf_path: Path) -> Dict[str, Any]:
        return resolve_or_run_mineru(
            pdf_path=Path(pdf_path),
            output_root=self.output_root,
            mineru_bin=self.mineru_bin,
            backend=self.backend,
            method=self.method,
            lang=self.lang,
            start_page=self.start_page,
            end_page=self.end_page,
            timeout_sec=self.timeout_sec,
        )

    @staticmethod
    def build_content_excerpt(content_path: Path, max_items: int = 60, max_text_chars: int = 280) -> List[Dict[str, Any]]:
        data = json.loads(Path(content_path).read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        out: List[Dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            if len(out) >= max_items:
                break
            txt = str(item.get("text") or item.get("content") or item.get("caption") or "")
            if len(txt) > max_text_chars:
                txt = txt[: max_text_chars - 3] + "..."
            page = item.get("page")
            if page is None:
                page = item.get("page_idx") or item.get("page_no")
            try:
                page = int(page) if page is not None else None
            except Exception:
                page = None
            out.append(
                {
                    "page": page,
                    "type": str(item.get("type") or "unknown"),
                    "text": txt,
                }
            )
        return out

