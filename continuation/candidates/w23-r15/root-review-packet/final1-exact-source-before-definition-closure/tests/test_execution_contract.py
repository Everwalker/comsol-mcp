from pathlib import Path

import pytest

from comsol_mcp._execution_contract import (
    ExecutionContractError,
    ModelRef,
    SessionLedger,
    canonical_project_path,
    canonical_request_hash,
    model_ref_from_mapping,
    permission_for_legacy_tool,
    redact_credentials_ref,
)


def ledger():
    value = SessionLedger("s-1", "server-1")
    ref = value.bind_model("model1", fingerprint="a")
    return value, ref


def test_model_ref_has_only_server_identity_and_rebind_prevents_aba():
    value, ref = ledger()
    assert "label" not in ref.as_dict() and "path" not in ref.as_dict()
    value.retire_model(ref)
    replacement = value.rebind_model("model1", fingerprint="a")
    assert replacement.generation > ref.generation
    with pytest.raises(ExecutionContractError, match="stale"):
        value.revision(ref)
    with pytest.raises(ExecutionContractError, match="non-identity"):
        model_ref_from_mapping({**replacement.as_dict(), "label": "a misleading label"})


def test_canonical_hash_ignores_tracking_and_rpc_wait_but_not_semantics():
    _, ref = ledger()
    first = canonical_request_hash("set_parameters", {"x": 1}, ref, 2, request_id="a", rpc_timeout_s=1)
    second = canonical_request_hash("set_parameters", {"x": 1}, ref, 2, request_id="b", rpc_timeout_s=99)
    assert first == second
    assert first != canonical_request_hash("set_parameters", {"x": 2}, ref, 2)
    assert first != canonical_request_hash("set_parameters", {"x": 1}, ref, 3)


def test_write_requires_revision_permissions_and_fingerprint():
    value, ref = ledger()
    with pytest.raises(ExecutionContractError, match="expected_revision"):
        value.begin_write("set_parameters", {}, ref, None, fingerprint="a")
    with pytest.raises(ExecutionContractError, match="fingerprint"):
        value.begin_write("set_parameters", {}, ref, 0, fingerprint="wrong")
    value.permissions = {"inspect"}
    with pytest.raises(ExecutionContractError, match="permission"):
        value.begin_write("set_parameters", {}, ref, 0, fingerprint="a")


def test_external_event_fails_closed_until_explicit_observation():
    value, ref = ledger()
    value.observe_external_change(ref, fingerprint="outside")
    with pytest.raises(ExecutionContractError, match="external"):
        value.begin_write("set_parameters", {}, ref, 0, fingerprint="a")
    value.mark_external_observed(ref, fingerprint="outside")
    ticket = value.begin_write("set_parameters", {}, ref, 1, fingerprint="outside")
    assert ticket.expected_revision == 1


@pytest.mark.parametrize("outcome", ["partial", "unknown"])
def test_partial_and_unknown_are_dirty_revision_advancing_and_not_retryable(outcome):
    value, ref = ledger()
    ticket = value.begin_write("set_parameters", {}, ref, 0, fingerprint="a")
    result = value.finish(ticket, outcome=outcome)
    assert result["revision"] == 1
    assert result["dirty"] is True
    assert result["safe_retry"] is False


def test_legacy_effects_cover_current_surface_and_unknown_rejects():
    assert permission_for_legacy_tool("server_info") == "inspect"
    assert permission_for_legacy_tool("server_start") == "host_control"
    with pytest.raises(ExecutionContractError, match="no effect"):
        permission_for_legacy_tool("invent_future_tool")
    from comsol_mcp._execution_contract import permission_for_effect
    with pytest.raises(ExecutionContractError, match="dynamic"):
        permission_for_effect("dynamic", declared_effect="inspect")


def test_bool_generation_and_revision_are_rejected_and_ticket_is_single_use():
    with pytest.raises(ExecutionContractError, match="generation"):
        ModelRef("s", "server", "model", True)
    value, ref = ledger()
    with pytest.raises(ExecutionContractError, match="expected_revision"):
        value.begin_write("set_parameters", {}, ref, True, fingerprint="a")
    ticket = value.begin_write("set_parameters", {}, ref, 0, fingerprint="a")
    with pytest.raises(ExecutionContractError, match="already active"):
        value.begin_write("set_parameters", {}, ref, 0, fingerprint="a")
    value.finish(ticket, outcome="failed")
    with pytest.raises(ExecutionContractError, match="stale"):
        value.finish(ticket, outcome="failed")


def test_external_change_while_running_cannot_report_success():
    value, ref = ledger()
    ticket = value.begin_write("set_parameters", {}, ref, 0, fingerprint="a")
    value.observe_external_change(ref, fingerprint="other-client")
    result = value.finish(ticket, outcome="succeeded", changed=True)
    assert result["outcome"] == "unknown"
    assert result["safe_retry"] is False


def test_project_path_resolves_symlink_escape_and_credentials_are_redacted(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    assert canonical_project_path(root, "runs/model.mph") == root / "runs/model.mph"
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ExecutionContractError, match="escapes"):
        canonical_project_path(root, "escape/secret.mph")
    (root / ".phase1-private").mkdir()
    with pytest.raises(ExecutionContractError, match="private"):
        canonical_project_path(root, ".phase1-private/token")
    assert redact_credentials_ref("file:///private/credential") == {"credentials_configured": True}
