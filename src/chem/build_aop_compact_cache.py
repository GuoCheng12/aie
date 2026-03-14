"""Build or refresh aop_compact.json artifacts from cached .aop files.

Usage:
    python -m src.chem.build_aop_compact_cache --cache-dir cache/atb --refresh
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict

from src.chem.atb_aop_compact import extract_aop_compact


def _iter_cache_dirs(cache_dir: Path):
    for status_path in cache_dir.rglob("status.json"):
        yield status_path.parent


def build_aop_compact_cache(cache_dir: Path, refresh: bool = False) -> Dict[str, int]:
    stats = {
        "total": 0,
        "written": 0,
        "skipped_existing": 0,
        "with_opt_aop": 0,
        "with_excit_aop": 0,
        "opt_fail_marker": 0,
        "excit_fail_marker": 0,
    }

    for cdir in _iter_cache_dirs(cache_dir):
        stats["total"] += 1
        compact_path = cdir / "aop_compact.json"
        if compact_path.exists() and not refresh:
            stats["skipped_existing"] += 1
            continue

        opt_path = cdir / "opt" / "opt_run.aop"
        excit_path = cdir / "excit" / "excit_run.aop"
        if opt_path.exists():
            stats["with_opt_aop"] += 1
        if excit_path.exists():
            stats["with_excit_aop"] += 1
        if not opt_path.exists() and not excit_path.exists():
            continue

        payload = extract_aop_compact(cdir)
        flags = payload.get("convergence_flags") or {}
        if flags.get("opt_has_fail_marker"):
            stats["opt_fail_marker"] += 1
        if flags.get("excit_has_fail_marker"):
            stats["excit_fail_marker"] += 1

        compact_path.parent.mkdir(parents=True, exist_ok=True)
        with open(compact_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        stats["written"] += 1

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Build compact aTB .aop summaries in cache")
    parser.add_argument("--cache-dir", default="cache/atb", help="Cache root directory")
    parser.add_argument("--refresh", action="store_true", help="Rewrite even if aop_compact.json already exists")
    args = parser.parse_args()

    stats = build_aop_compact_cache(Path(args.cache_dir), refresh=bool(args.refresh))
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
