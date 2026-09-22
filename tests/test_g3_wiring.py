"""G3 wiring invariants: catalogue effect -> write-ticket classification.

The parent wires the G3 domain modules into the control plane in three
places: the registry's executable surface, the write-ticket effect mapping in
``_managed_backend``, and the legacy-tool classification in
``_execution_contract``.  These tests lock the contracts that must hold for
*every* published G3 operation, so a future W14-W16 module cannot land with an
unclassified effect, a missing isolation decision, or an operation the
registry refuses to describe as executable.
"""
from __future__ import annotations

import pytest

from comsol_mcp._execution_contract import (
    _G3_CATALOG_EFFECTS,
    ExecutionContractError,
    permission_for_legacy_tool,
)
from comsol_mcp._g2_registry import is_implemented
from comsol_mcp._g3_ops import DISPATCH, EFFECTS, IMPLEMENTED_OPERATIONS, OPERATION_ORIGINS, REQUIRES_ISOLATION
from comsol_mcp._managed_backend import _G3_EFFECT_MAP

_ALLOWED_PERMISSIONS = {"inspect", "project_write", "compute", "trusted_code", "host_control"}


def test_every_published_operation_is_implemented_and_classified():
    assert IMPLEMENTED_OPERATIONS == frozenset(DISPATCH)
    assert DISPATCH, "no G3 operations are published"
    for operation_id in sorted(DISPATCH):
        assert is_implemented(operation_id), operation_id
        permission = permission_for_legacy_tool(operation_id.replace(".", "_"))
        assert permission in _ALLOWED_PERMISSIONS, (operation_id, permission)


def test_effects_come_from_the_catalogue_and_translate_everywhere():
    assert set(EFFECTS) == set(DISPATCH)
    for operation_id, effect in EFFECTS.items():
        # Both translation tables must understand the effect the aggregator
        # records; a new catalogue effect needs a conscious update, not a
        # silent default.
        assert str(effect).upper() in _G3_EFFECT_MAP, (operation_id, effect)
        assert str(effect).upper() in _G3_CATALOG_EFFECTS, (operation_id, effect)


def test_requires_isolation_is_exactly_the_non_read_surface():
    expected = {op for op, effect in EFFECTS.items() if str(effect).upper() != "READ"}
    assert REQUIRES_ISOLATION == expected
    for op, effect in EFFECTS.items():
        if str(effect).upper() == "READ":
            assert op not in REQUIRES_ISOLATION, op


def test_unknown_operation_alias_is_refused():
    with pytest.raises(ExecutionContractError) as exc:
        permission_for_legacy_tool("no_such_g3_operation")
    assert exc.value.code == "PERMISSION_DENIED"


def test_alias_round_trip_matches_g2_alias_generation():
    """``_g2_alias`` must be the inverse of the classification lookup."""

    from comsol_mcp._managed_backend import ManagedBackend

    for operation_id in DISPATCH:
        assert ManagedBackend._g2_alias(operation_id) == operation_id.replace(".", "_")


def test_probe_operations_are_reachable_from_host_dispatch():
    """The implemented probe operations must be dispatchable, and only those.

    The F10 module existed but was absent from ``_g3_ops._MODULES``, so no host
    request could reach ``probe.list``/``probe.create``/``probe.remove`` while the
    G2 action catalogue advertised them.
    """

    from comsol_mcp._g3_ops import dispatch as dispatch_operation

    for operation_id in ("probe.list", "probe.create", "probe.remove"):
        assert operation_id in IMPLEMENTED_OPERATIONS, operation_id
        assert OPERATION_ORIGINS[operation_id] == "_probe_manage"

    # Declared by the catalogue but not implemented: the host path must refuse them
    # with a structured error rather than reporting a success that never happened.
    for unimplemented in ("probe.update", "probe.history"):
        assert unimplemented not in IMPLEMENTED_OPERATIONS, unimplemented
        with pytest.raises(ExecutionContractError) as exc:
            dispatch_operation(unimplemented, None, "Model", {})
        assert exc.value.code == "UNSUPPORTED_OPERATION"
