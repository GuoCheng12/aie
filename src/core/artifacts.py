"""
Replay artifact writer for per-agent steps.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from src.core.hashing import sha256_file
from src.core.safe_fs import safe_write_text


def _json_dump(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_write_text(path, json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def _diff_paths(before: Any, after: Any, prefix: str = "") -> List[str]:
    out: List[str] = []
    if type(before) != type(after):
        out.append(prefix or "/")
        return out
    if isinstance(before, dict):
        keys = sorted(set(before.keys()) | set(after.keys()))
        for k in keys:
            p = f"{prefix}/{k}" if prefix else f"/{k}"
            if k not in before or k not in after:
                out.append(p)
            else:
                out.extend(_diff_paths(before[k], after[k], p))
        return out
    if isinstance(before, list):
        n = max(len(before), len(after))
        for i in range(n):
            p = f"{prefix}/{i}" if prefix else f"/{i}"
            if i >= len(before) or i >= len(after):
                out.append(p)
            else:
                out.extend(_diff_paths(before[i], after[i], p))
        return out
    if before != after:
        out.append(prefix or "/")
    return out


class StepArtifactWriter:
    def __init__(self, step_dir: Path):
        self.step_dir = Path(step_dir)
        self.step_dir.mkdir(parents=True, exist_ok=True)
        self._files: List[Path] = []

    def write_json(self, name: str, obj: Any) -> Path:
        p = self.step_dir / name
        _json_dump(p, obj)
        self._files.append(p)
        return p

    def write_case_bundle(
        self,
        *,
        input_snapshot: Dict[str, Any],
        raw_outputs: Dict[str, Any],
        patch: List[Dict[str, Any]],
        case_before: Dict[str, Any],
        case_after: Dict[str, Any],
    ) -> Dict[str, str]:
        out_paths: Dict[str, str] = {}
        out_paths["00_input_snapshot"] = str(self.write_json("00_input_snapshot.json", input_snapshot))
        out_paths["01_raw_outputs"] = str(self.write_json("01_raw_outputs.json", raw_outputs))
        raw_index: Dict[str, str] = {}
        for raw_name, raw_obj in raw_outputs.items():
            safe = raw_name.replace("/", "_")
            p = self.write_json(f"01_raw_{safe}.json", raw_obj)
            out_paths[f"raw:{raw_name}"] = str(p)
            raw_index[raw_name] = str(p)
        if raw_index:
            out_paths["01_raw_index"] = str(self.write_json("01_raw_index.json", raw_index))
        out_paths["patch"] = str(self.write_json("03_patch.json", patch))
        out_paths["case_before"] = str(self.write_json("04_case_before.json", case_before))
        out_paths["case_after"] = str(self.write_json("05_case_after.json", case_after))
        out_paths["case_diff"] = str(
            self.write_json("06_case_diff.json", {"changed_paths": _diff_paths(case_before, case_after)})
        )
        manifest = self._build_manifest()
        out_paths["manifest"] = str(self.write_json("manifest.json", manifest))
        return out_paths

    def _build_manifest(self) -> Dict[str, Any]:
        rows = []
        for p in sorted(set(self._files), key=lambda x: x.name):
            if not p.exists():
                continue
            rows.append(
                {
                    "name": p.name,
                    "path": str(p),
                    "sha256": sha256_file(p),
                    "size_bytes": p.stat().st_size,
                }
            )
        return {"files": rows}
