"""Safe filesystem helpers with denylist-based write guard."""

from __future__ import annotations

from pathlib import Path


_DENYLIST_RELATIVE = {
    "data/evidence_table.parquet",
}


def _norm(path: Path) -> Path:
    p = Path(path)
    try:
        return p.resolve()
    except Exception:
        return p.absolute()


def _denylist_paths() -> set[Path]:
    out: set[Path] = set()
    root = _norm(Path.cwd())
    for rel in _DENYLIST_RELATIVE:
        out.add(_norm(root / rel))
    return out


def is_denied_write_path(path: Path) -> bool:
    target = _norm(Path(path))
    denied = _denylist_paths()
    if target in denied:
        return True
    # Block sidecar/temp writes targeting denied path variants (e.g. .tmp).
    for d in denied:
        if str(target).startswith(str(d)):
            return True
    return False


def guard_write_path(path: Path) -> None:
    if is_denied_write_path(path):
        raise PermissionError(f"safe_fs_write_denied:{Path(path)}")


def safe_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    p = Path(path)
    guard_write_path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding=encoding)
