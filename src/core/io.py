"""
I/O helpers for case files and JSON artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def ensure_parent(path: Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(path: Path, obj: Any) -> None:
    ensure_parent(Path(path))
    Path(path).write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def load_case(path: Path) -> Dict[str, Any]:
    return load_json(Path(path))


def save_case(path: Path, case: Dict[str, Any]) -> None:
    save_json(Path(path), case)

