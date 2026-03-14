import json
import subprocess
import sys
from pathlib import Path


def test_eval_mechanism_benchmark_cli_help() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "src.cli", "eval-mechanism-benchmark", "--help"],
        capture_output=True,
        text=True,
        cwd="/Users/wuguocheng/workshop/Uncertainty_aware_AIE",
    )
    assert proc.returncode == 0
    assert "eval-mechanism-benchmark" in proc.stdout
    assert "--protocol" in proc.stdout
