"""
Tail run_status.json under artifacts root.

Usage:
  python -m src.orchestration.tail_status --artifacts-dir artifacts/multi_agent
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict


def _read_status(path: Path) -> Dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Tail run_status.json")
    parser.add_argument("--artifacts-dir", type=str, required=True, help="Artifacts root containing run_status.json")
    parser.add_argument("--interval-sec", type=float, default=0.5)
    args = parser.parse_args()

    status_path = Path(args.artifacts_dir) / "run_status.json"
    last_blob = ""
    try:
        while True:
            data = _read_status(status_path)
            if data is not None:
                blob = json.dumps(data, ensure_ascii=False, sort_keys=True)
                if blob != last_blob:
                    print(json.dumps(data, indent=2, ensure_ascii=False), flush=True)
                    last_blob = blob
            time.sleep(max(float(args.interval_sec), 0.1))
    except KeyboardInterrupt:
        print("[tail_status] stopped", flush=True)


if __name__ == "__main__":
    main()
