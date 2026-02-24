import pytest

from src.core.patching import PatchValidationError, apply_patch, validate_patch


def test_patch_whitelist_blocks_forbidden_path():
    patch = [{"op": "add", "path": "/current_gate/state", "value": "ready_for_reasoning"}]
    with pytest.raises(PatchValidationError):
        validate_patch(
            patch,
            allowed_prefixes=("/query/",),
            append_only_prefixes=(),
        )


def test_append_only_enforced():
    patch = [{"op": "replace", "path": "/agent_runs/0", "value": {"x": 1}}]
    with pytest.raises(PatchValidationError):
        validate_patch(
            patch,
            allowed_prefixes=("/agent_runs",),
            append_only_prefixes=("/agent_runs",),
        )


def test_apply_patch_append_list_and_replace_dict():
    before = {"agent_runs": [], "query": {"inchikey": None}}
    patch = [
        {"op": "replace", "path": "/query/inchikey", "value": "AAA"},
        {"op": "add", "path": "/agent_runs/-", "value": {"agent_name": "data_agent"}},
    ]
    validate_patch(
        patch,
        allowed_prefixes=("/query/", "/agent_runs"),
        append_only_prefixes=("/agent_runs",),
    )
    after = apply_patch(before, patch)
    assert after["query"]["inchikey"] == "AAA"
    assert len(after["agent_runs"]) == 1
    assert after["agent_runs"][0]["agent_name"] == "data_agent"

