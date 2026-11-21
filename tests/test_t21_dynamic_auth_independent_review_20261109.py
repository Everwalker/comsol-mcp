"""Reviewer-owned narrow controls for the T21 dynamic project-auth fix.

Independent of tests/test_dynamic_project_auth_qwen_20261109.py: this file
never imports the author's fixtures.  Every covered operation is derived from
the server-side catalogue (``_g3_ops.EFFECTS`` / ``_g2_registry.BY_ID``) rather
than a hardcoded sample list.  Only the engine endpoint is faked; the real
ControlDaemon precheck (``_authorize_project_execution``) and the real
ProjectAuthority double-grant run.  All state is created inside pytest
``tmp_path`` project dirs.  No COMSOL, Worker, socket, SSH, or network.

Source identity under review: comsol_mcp/_control_daemon.py
sha256 7166169770079b6ef35b28d58121999ec2e105b117136d3596820fdf88eb0810.
"""
from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest

from comsol_mcp import _g2_registry, _g3_ops, _managed_backend
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import (
    EFFECT_PERMISSIONS,
    ExecutionContractError,
    LEGACY_TOOL_EFFECTS,
    SessionLedger,
    _G3_CATALOG_EFFECTS,
)
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._g2_public_api import legacy_effect as public_api_legacy_effect

# The exact table the fix replaced (from the pre-fix snapshot hunk), so the
# OLD semantics can be re-evaluated without touching any source file.
OLD_PRECHECK_MAP: dict[str, str] = {
    "INSPECT": "inspect", "PROJECT_WRITE": "project_write",
    "READ": "inspect", "WRITE": "project_write", "STATE_WRITE": "project_write",
    "FILE_WRITE": "project_write", "COMPUTE": "compute", "EVALUATE": "project_write",
    "TRUSTED_CODE": "trusted_code", "HOST_CONTROL": "host_control",
}


def old_semantics(effect: Any) -> str | None:
    return OLD_PRECHECK_MAP.get(str(effect).upper())


CATALOG_DYNAMIC = tuple(sorted(o for o, e in _g3_ops.EFFECTS.items() if e == "DYNAMIC"))
DYNAMIC_G3 = tuple(sorted(o for o in CATALOG_DYNAMIC if _g3_ops.is_implemented(o)))


# ---------------------------------------------------------------------------
# 1. spelling compatibility with the replaced precheck table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spelling", sorted(OLD_PRECHECK_MAP))
def test_old_uppercase_spellings_resolve_identically(spelling: str) -> None:
    assert ControlDaemon._permission_for_effect(spelling) == OLD_PRECHECK_MAP[spelling]


@pytest.mark.parametrize("canonical", sorted(EFFECT_PERMISSIONS))
def test_canonical_lowercase_spellings_resolve_identically_to_old(canonical: str) -> None:
    # the old code did str(effect).upper() then its private table; the new
    # chain must return the same grant for every canonical spelling it used.
    assert ControlDaemon._permission_for_effect(canonical) == old_semantics(canonical)


@pytest.mark.parametrize("junk", [None, "", "   ", 123, 0.5, "MUTATE_THE_WORLD", "read_write"])
def test_junk_and_unknown_effects_stay_unmapped(junk: Any) -> None:
    assert ControlDaemon._permission_for_effect(junk) is None
    assert ControlDaemon._permission_for_effect(junk) == old_semantics(junk)


def test_lowercase_dynamic_marker_stays_refused_and_catalog_dynamic_is_the_only_delta() -> None:
    assert LEGACY_TOOL_EFFECTS["api_invoke"] == "dynamic"
    assert ControlDaemon._permission_for_effect("dynamic") is None
    assert old_semantics("dynamic") is None
    # the intended delta: the server-side catalogue effect DYNAMIC now maps to
    # the write class, where the old table left it unmapped (the fixed defect).
    assert old_semantics("DYNAMIC") is None
    assert ControlDaemon._permission_for_effect("DYNAMIC") == "project_write"


def test_mixed_case_dynamic_is_a_documented_widening_but_unreachable() -> None:
    # Finding (pinned, not asserted away): the upper-catalogue lookup accepts a
    # mixed-case spelling the old table and permission_for_effect would refuse.
    assert old_semantics("Dynamic") is None
    assert ControlDaemon._permission_for_effect("Dynamic") == "project_write"
    # Reachability guard: every effect literal the daemon can actually feed in
    # is pure upper- or pure lowercase, so no table can produce "Dynamic".
    literals = ({entry.effect for entry in _g2_registry.BY_ID.values()}
                | set(_g3_ops.EFFECTS.values())
                | set(LEGACY_TOOL_EFFECTS.values()))
    assert all(s.isupper() or s.islower() for s in literals), literals


# ---------------------------------------------------------------------------
# 2. the daemon precheck demands exactly the backend's own ticket class
# ---------------------------------------------------------------------------


def test_daemon_catalog_table_equals_managed_backend_ticket_table() -> None:
    assert _G3_CATALOG_EFFECTS == _managed_backend._G3_EFFECT_MAP


@pytest.mark.parametrize("effect", sorted(set(_g3_ops.EFFECTS.values())))
def test_every_declared_catalog_effect_resolves_to_the_backend_grant(effect: str) -> None:
    backend_grant = EFFECT_PERMISSIONS[_managed_backend._G3_EFFECT_MAP[effect]]
    assert ControlDaemon._permission_for_effect(effect) == backend_grant


def test_daemon_legacy_table_is_exactly_the_daemon_own_spellings() -> None:
    assert ControlDaemon._DAEMON_LEGACY_EFFECTS == {
        "INSPECT": "inspect", "PROJECT_WRITE": "project_write",
        "COMPUTE": "compute", "TRUSTED_CODE": "trusted_code",
        "HOST_CONTROL": "host_control",
    }


def test_api_invoke_effect_domain_cannot_be_dynamic_or_catalog_write() -> None:
    # provisional_effect validates every reviewed capability effect through
    # this gate; anything outside READ/WRITE/COMPUTE must raise.
    for ok in ("READ", "WRITE", "COMPUTE"):
        assert public_api_legacy_effect(ok) in EFFECT_PERMISSIONS
    for bad in ("DYNAMIC", "dynamic", "STATE_WRITE", "FILE_WRITE", "EVALUATE",
                "TRUSTED_CODE", "HOST_CONTROL", "inspect"):
        with pytest.raises(ExecutionContractError):
            public_api_legacy_effect(bad)


# ---------------------------------------------------------------------------
# 3. per-operation enforcement through the real daemon + ProjectAuthority
# ---------------------------------------------------------------------------


class NullWorker:
    generation = 1

    def client(self) -> Any:
        raise AssertionError("review controls must never touch the engine endpoint")

    def operation_context(self, *a: Any, **k: Any) -> Any:
        return nullcontext()


class SnapshotAdapter:
    def __init__(self, server_id: str) -> None:
        self.server_id = server_id

    def model_snapshot(self, tag: str) -> dict[str, Any]:
        return {"model_tag": tag, "server_instance_id": self.server_id,
                "external_event_counter": 0, "fingerprint": "fp-review"}


def _daemon(tmp_path: Path, name: str) -> ControlDaemon:
    container = tmp_path / name / "projects"
    container.mkdir(parents=True, exist_ok=True)
    ledger = SessionLedger(f"rev-{name}", f"revsrv-{name}",
                           permissions={"inspect", "project_write", "compute"})
    service = ExecutionService(ledger, SnapshotAdapter(f"revsrv-{name}"), project_root=container)
    return ControlDaemon(tmp_path / name / "control", service=service, registry={},
                         worker=NullWorker(), project_root=container)


def _create_project(daemon: ControlDaemon, name: str, permissions: list[str]) -> str:
    result = daemon.dispatch({
        "operation": "project.create",
        "arguments": {"label": name, "workspace": name,
                      "policy": {"permissions": permissions}},
        "execution": {"request_id": f"rc-{name}", "idempotency_key": f"rc-{name}"}})
    assert result["success"], result
    return result["data"]["project"]["project_id"]


@pytest.fixture()
def pair(tmp_path):
    daemon = _daemon(tmp_path, "pair")
    try:
        write_project = _create_project(daemon, "rev-write", ["inspect", "project_write", "compute"])
        inspect_project = _create_project(daemon, "rev-inspect-only", ["inspect"])
        yield daemon, write_project, inspect_project
    finally:
        daemon.close()


def test_catalog_declares_nine_implemented_dynamic_operations() -> None:
    # _g3_ops.EFFECTS carries exactly the nine implemented dynamic adapters;
    # the registry adds exactly two more envelope entries with effect DYNAMIC.
    assert len(CATALOG_DYNAMIC) == 9
    assert len(DYNAMIC_G3) == 9
    registry_dynamic = sorted(o for o, e in ((oid, entry.effect)
                                             for oid, entry in _g2_registry.BY_ID.items())
                               if e == "DYNAMIC")
    assert set(registry_dynamic) == set(CATALOG_DYNAMIC) | {"api.invoke", "registry.call"}
    for operation in DYNAMIC_G3:
        assert _g2_registry.BY_ID[operation].effect == "DYNAMIC"


def test_every_dynamic_operation_passes_precheck_only_with_project_write(pair) -> None:
    daemon, write_project, inspect_project = pair
    for operation in DYNAMIC_G3:
        env = {"project_id": write_project}
        assert daemon._authorize_project_execution(operation, {}, dict(env)) is None, operation
        with pytest.raises(ExecutionContractError) as raised:
            daemon._authorize_project_execution(operation, {}, {"project_id": inspect_project})
        assert raised.value.code == "PERMISSION_DENIED", operation
        assert "requires project_write" in str(raised.value), operation
        # read-shaped dynamic actions must not fall back to the inspect grant
        assert ControlDaemon._permission_for_effect("DYNAMIC") != "inspect"


def test_envelope_dynamic_entries_are_not_authorized_by_their_own_wrapper_effect(pair) -> None:
    daemon, _, inspect_project = pair
    # nested wrapper: the INNER dynamic operation governs (unwrap branch)
    with pytest.raises(ExecutionContractError) as raised:
        daemon._authorize_project_execution(
            "registry_call", {"operation_id": DYNAMIC_G3[0], "arguments": {}},
            {"project_id": inspect_project})
    assert raised.value.code == "PERMISSION_DENIED"
    assert "requires project_write" in str(raised.value)
    # dotted alias wrapper: its own catalogue DYNAMIC effect requires the write
    # grant at the envelope, so it cannot smuggle writes past an inspect-only
    # project at the daemon layer either.
    with pytest.raises(ExecutionContractError) as raised:
        daemon._authorize_project_execution(
            "registry.call", {"operation_id": DYNAMIC_G3[0], "arguments": {}},
            {"project_id": inspect_project})
    assert raised.value.code == "PERMISSION_DENIED"
    assert _g2_registry.BY_ID["registry.call"].effect == "DYNAMIC"


def test_unmapped_and_alias_spellings_fail_closed_before_grant(pair) -> None:
    daemon, write_project, _ = pair
    # a name present in NO effect table is refused even for a project_write
    # project: the precheck never defaults to a permissive grant.
    with pytest.raises(ExecutionContractError) as raised:
        daemon._authorize_project_execution("totally.unknown.operation", {},
                                            {"project_id": write_project})
    assert raised.value.code == "PROJECT_SCOPE_UNSUPPORTED"
    # underscore alias spellings of dynamic operations are absent from every
    # effect table, so they also fail closed at the daemon layer.
    alias = DYNAMIC_G3[0].replace(".", "_")
    assert alias not in _g2_registry.BY_ID and alias not in LEGACY_TOOL_EFFECTS
    with pytest.raises(ExecutionContractError) as raised:
        daemon._authorize_project_execution(alias, {}, {"project_id": write_project})
    assert raised.value.code == "PROJECT_SCOPE_UNSUPPORTED"


def test_project_grant_is_reconciled_against_live_service_grant(pair) -> None:
    # ProjectAuthority.authorize_operation demands BOTH the persisted project
    # policy and the live ledger grant; a project policy naming a permission
    # the service never holds cannot widen authority.
    daemon, write_project, _ = pair
    record = daemon.project_authority.authorize_operation(write_project, "project_write")
    assert record["project_id"] == write_project
    with pytest.raises(ExecutionContractError) as raised:
        daemon.project_authority.authorize_operation(write_project, "host_control")
    assert raised.value.code == "PERMISSION_DENIED"


# ---------------------------------------------------------------------------
# 4. dispatch-level proof: refusal happens before admission and dispatch
# ---------------------------------------------------------------------------


def test_dispatch_rejects_unknown_inner_effect_before_admission(tmp_path) -> None:
    daemon = _daemon(tmp_path, "dispatch")
    try:
        project = _create_project(daemon, "rev-dispatch", ["inspect", "project_write", "compute"])
        result = daemon.dispatch({
            "operation": "registry_call",
            "arguments": {"operation_id": "totally.unknown.operation", "arguments": {}},
            "execution": {"project_id": project, "request_id": "rev-unknown",
                          "idempotency_key": "rev-unknown"}})
        assert result["success"] is False
        assert (result.get("error") or {}).get("code") == "PROJECT_SCOPE_UNSUPPORTED", result
        # nothing was admitted: the precheck fired before queue/store admission
        assert "execution" not in result, result
    finally:
        daemon.close()


def test_dispatch_wrapper_denied_for_inspect_only_project(tmp_path) -> None:
    daemon = _daemon(tmp_path, "dispatch-deny")
    try:
        project = _create_project(daemon, "rev-dispatch-deny", ["inspect"])
        # the wrapper body is deliberately empty: the ACL gate fires on the
        # inner operation id before any schema/engine work, so no per-sample
        # valid body is needed here (valid-body witnesses live in the author
        # suite and the parent replay).
        result = daemon.dispatch({
            "operation": "registry_call",
            "arguments": {"operation_id": "definition.component_manage",
                          "arguments": {"action": "list"}},
            "execution": {"project_id": project, "request_id": "rev-deny",
                          "idempotency_key": "rev-deny"}})
        assert result["success"] is False
        assert (result.get("error") or {}).get("code") == "PERMISSION_DENIED", result
        assert "requires project_write" in result["error"]["message"]
        assert "execution" not in result, result
    finally:
        daemon.close()
