"""
Evaluation-only mechanism label normalization.

This module is intentionally isolated from runtime reasoning output logic.
Ground-truth and predicted labels share the same canonical mapping for
benchmark alignment, but the call sites are split to keep intent explicit.
"""

from __future__ import annotations

import re
from typing import Dict, List

CANONICAL_LABELS: List[str] = [
    "TICT",
    "ICT",
    "ESIPT",
    "neutral aromatic",
    "other",
    "unknown",
]


def _canonical_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").strip().lower()).strip()


_ALIASES: Dict[str, str] = {
    "tict": "TICT",
    "tict like": "TICT",
    "ict": "ICT",
    "ict like": "ICT",
    "esipt": "ESIPT",
    "neutral aromatic": "neutral aromatic",
    "other": "other",
    "unknown": "unknown",
    # Locked by benchmark policy.
    "clusterluminescence": "unknown",
    "esipt ict tict": "unknown",
}


def normalize_label(raw: str | None) -> str:
    key = _canonical_key(raw or "")
    if not key:
        return "unknown"
    return _ALIASES.get(key, "unknown")


def normalize_ground_truth_label(raw: str | None) -> str:
    return normalize_label(raw)


def normalize_prediction_label(raw: str | None) -> str:
    return normalize_label(raw)
