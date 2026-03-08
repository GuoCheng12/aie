"""
Static check: main-chain modules must not hardcode legacy private fields.

This checker is intentionally narrow:
- scans the current train-only mainline modules
- fails on explicit legacy private field tokens
- fails if mainline imports/references deprecated merge_pre_atb
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


MAIN_CHAIN_FILES = [
    "src/data/loader.py",
    "src/data/standardizer.py",
    "src/data/canonicalizer.py",
    "src/data/pipeline.py",
    "src/features/anchor_ecfp.py",
    "src/uq/compute_uq_pre_atb.py",
    "src/uq/compute_uq_pre_atb_p5b.py",
    "src/reports/generate_reports_pre_atb_p5b.py",
    "src/reports/export_queues_pre_atb_p5b.py",
    "src/graph/build_evidence_table_v1_p1.py",
    "src/graph/build_light_graph_v1_p2.py",
    "src/graph/retrieval.py",
    "src/cases/create_case_from_smiles.py",
    "src/agents/data_agent.py",
    "src/cli.py",
]


LEGACY_FIELD_PATTERN = re.compile(
    r"""["'](
        absorption|
        absorption_peak_nm|
        emission_sol|
        emission_crys|
        qy_sol|
        qy_aggr|
        qy_solid|
        qy_crys|
        tau_sol|
        tau_aggr|
        tau_solid|
        tau_crys|
        tested_solvent
    )["']""",
    re.VERBOSE,
)


def run_check(repo_root: Path) -> int:
    errors: list[str] = []

    for rel_path in MAIN_CHAIN_FILES:
        file_path = repo_root / rel_path
        if not file_path.exists():
            errors.append(f"missing main-chain file: {rel_path}")
            continue

        content = file_path.read_text(encoding="utf-8")

        field_hits = sorted(set(m.group(1) for m in LEGACY_FIELD_PATTERN.finditer(content)))
        if field_hits:
            errors.append(f"{rel_path}: legacy field tokens found: {field_hits}")

        if "merge_pre_atb" in content:
            errors.append(f"{rel_path}: references deprecated merge_pre_atb")

    if errors:
        print("TRAIN-ONLY STATIC CHECK: FAIL")
        for item in errors:
            print(f"- {item}")
        return 1

    print("TRAIN-ONLY STATIC CHECK: PASS")
    print(f"Checked files: {len(MAIN_CHAIN_FILES)}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Static check for train-only main-chain field usage")
    parser.add_argument("--repo-root", default=".", help="Repository root path")
    args = parser.parse_args()

    exit_code = run_check(Path(args.repo_root).resolve())
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
