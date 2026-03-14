"""Train a calibrated structure classifier for StructureAgent."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.structure.structure_classifier import train_structure_classifier

try:
    from tqdm.auto import tqdm
except Exception:  # pragma: no cover - tqdm is optional at import time
    tqdm = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train StructureAgent classifier artifacts")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--outdir", default="artifacts/structure_agent/latest")
    parser.add_argument("--calibration-method", default="sigmoid", choices=["sigmoid", "isotonic"])
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--validation-fraction", type=float, default=0.2)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--batch-size", type=int, default=64)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    progress_bar = None

    def _on_epoch(row: dict) -> None:
        if progress_bar is None:
            return
        progress_bar.update(1)
        progress_bar.set_postfix(
            {
                "train_loss": row.get("train_loss"),
                "train_acc": row.get("train_accuracy"),
                "valid_loss": row.get("valid_loss"),
                "valid_acc": row.get("valid_accuracy"),
            },
            refresh=True,
        )

    if tqdm is not None:
        progress_bar = tqdm(total=max(1, int(args.epochs)), desc="structure-clf", unit="epoch")
    result = train_structure_classifier(
        data_dir=args.data_dir,
        outdir=args.outdir,
        calibration_method=args.calibration_method,
        epochs=args.epochs,
        validation_fraction=args.validation_fraction,
        random_state=args.random_state,
        batch_size=args.batch_size,
        progress_callback=_on_epoch if progress_bar is not None else None,
    )
    if progress_bar is not None:
        progress_bar.close()
    out = Path(args.outdir)
    (out / "train_summary.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
