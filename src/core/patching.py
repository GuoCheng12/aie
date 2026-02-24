"""
RFC6902 patch validation and application with path-scope enforcement.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List, Sequence


class PatchValidationError(ValueError):
    pass


def _pointer_tokens(path: str) -> List[str]:
    if not path.startswith("/"):
        raise PatchValidationError(f"invalid_json_pointer:{path}")
    if path == "/":
        return []
    return [p.replace("~1", "/").replace("~0", "~") for p in path.split("/")[1:]]


def _path_allowed(path: str, allowed_prefixes: Sequence[str]) -> bool:
    for prefix in allowed_prefixes:
        if path == prefix or path.startswith(prefix):
            return True
    return False


def _path_in_append_scope(path: str, append_only_prefixes: Sequence[str]) -> bool:
    for prefix in append_only_prefixes:
        if path == prefix or path.startswith(prefix):
            return True
    return False


def validate_patch(
    patch_ops: Sequence[Dict[str, Any]],
    *,
    allowed_prefixes: Sequence[str],
    append_only_prefixes: Sequence[str],
    allowed_ops: Iterable[str] = ("add", "replace", "test"),
) -> None:
    allowed_set = set(allowed_ops)
    for idx, op in enumerate(patch_ops):
        if not isinstance(op, dict):
            raise PatchValidationError(f"patch_op_not_object:{idx}")
        kind = str(op.get("op") or "")
        if kind not in allowed_set:
            raise PatchValidationError(f"patch_op_not_allowed:{idx}:{kind}")
        path = str(op.get("path") or "")
        _pointer_tokens(path)
        if not _path_allowed(path, allowed_prefixes):
            raise PatchValidationError(f"patch_path_forbidden:{path}")

        if _path_in_append_scope(path, append_only_prefixes):
            if kind != "add":
                raise PatchValidationError(f"append_only_requires_add:{path}")
            if not path.endswith("/-"):
                raise PatchValidationError(f"append_only_requires_dash_index:{path}")


def _resolve_parent(doc: Any, tokens: List[str]) -> Any:
    cur = doc
    for tok in tokens:
        if isinstance(cur, dict):
            if tok not in cur:
                cur[tok] = {}
            cur = cur[tok]
        elif isinstance(cur, list):
            try:
                idx = int(tok)
            except Exception as exc:
                raise PatchValidationError(f"list_index_invalid:{tok}") from exc
            if idx < 0 or idx >= len(cur):
                raise PatchValidationError(f"list_index_oob:{idx}")
            cur = cur[idx]
        else:
            raise PatchValidationError("patch_parent_not_container")
    return cur


def apply_patch(doc: Dict[str, Any], patch_ops: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    out = copy.deepcopy(doc)
    for op in patch_ops:
        kind = str(op["op"])
        path = str(op["path"])
        value = op.get("value")
        tokens = _pointer_tokens(path)
        if not tokens:
            raise PatchValidationError("root_patch_not_supported")
        parent_tokens = tokens[:-1]
        leaf = tokens[-1]
        parent = _resolve_parent(out, parent_tokens)

        if kind == "test":
            actual = _read_pointer(out, tokens)
            if actual != value:
                raise PatchValidationError(f"test_failed:{path}")
            continue

        if isinstance(parent, dict):
            if kind in {"add", "replace"}:
                parent[leaf] = value
            else:
                raise PatchValidationError(f"unsupported_op:{kind}")
        elif isinstance(parent, list):
            if leaf == "-":
                if kind != "add":
                    raise PatchValidationError(f"list_append_requires_add:{path}")
                parent.append(value)
            else:
                try:
                    idx = int(leaf)
                except Exception as exc:
                    raise PatchValidationError(f"list_index_invalid:{leaf}") from exc
                if idx < 0:
                    raise PatchValidationError(f"list_index_negative:{idx}")
                if kind == "replace":
                    if idx >= len(parent):
                        raise PatchValidationError(f"list_index_oob:{idx}")
                    parent[idx] = value
                elif kind == "add":
                    if idx > len(parent):
                        raise PatchValidationError(f"list_index_oob:{idx}")
                    if idx == len(parent):
                        parent.append(value)
                    else:
                        parent.insert(idx, value)
                else:
                    raise PatchValidationError(f"unsupported_list_op:{kind}")
        else:
            raise PatchValidationError(f"patch_parent_not_container:{path}")
    return out


def _read_pointer(doc: Any, tokens: Sequence[str]) -> Any:
    cur = doc
    for tok in tokens:
        if isinstance(cur, dict):
            if tok not in cur:
                raise PatchValidationError(f"pointer_missing_key:{tok}")
            cur = cur[tok]
        elif isinstance(cur, list):
            try:
                idx = int(tok)
            except Exception as exc:
                raise PatchValidationError(f"pointer_invalid_index:{tok}") from exc
            if idx < 0 or idx >= len(cur):
                raise PatchValidationError(f"pointer_index_oob:{idx}")
            cur = cur[idx]
        else:
            raise PatchValidationError("pointer_parent_not_container")
    return cur

