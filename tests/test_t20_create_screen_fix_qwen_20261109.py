"""T20 (2026-11-09 B1 fix): managed node.create initial properties enter the
T20 partial path-shape screen BEFORE any create mutation or write ticket.

Real ManagedBackend + ExecutionService + Unit-A descriptor against the existing
fake Worker; the isolation check is stubbed.  Paths and files are synthetic:
this is software-only route evidence, NOT native COMSOL, native Windows or a
filesystem containment proof.  The screen is a partial mitigation only: it
records open items (bare relative names such as ``f1`` remain undecided;
same-user scan-time swap can still move kernel resolution outside the root —
see the guard module docstring and the 2026-11-09 independent review B2).
"""
from __future__ import annotations

import pytest

from comsol_mcp._execution_contract import ExecutionContractError, PreWriteRefusal
from comsol_mcp._g2_engine import execute_node_action, prepare_node_action
from comsol_mcp._g2_property_path_guard import classify_string_leaf
from tests.test_unit_a_node_public_api import ROOT, backend, execution, tv


def _create(b, body, ref):
    return b.invoke("node.create", body, execution(ref), "create-screen-fix-qwen", None)


def _body(properties, tag):
    return {"parent": ROOT, "collection": "feature", "tag": tag,
            "type_id": "Rectangle", "properties": properties}


def test_unverified_root_refuses_path_shape_before_create(backend, tmp_path):
    """No durable workspace record: path-shaped values fail closed before create."""
    b, worker, ref, tickets = backend
    b.project_authorizer = lambda project_id, permission: {}
    b.endpoint_key = "127.0.0.1:2036"  # identity input only; no socket is opened
    inside = str(tmp_path / "inputs" / "a.dat")
    body = _body([{"name": "string", "value": tv(inside)}], "unrooted_new")
    try:
        result = _create(b, body, ref)
    except ExecutionContractError as exc:
        assert exc.code == "ACCESS_VIOLATION", exc
    else:
        assert result.get("success") is False, result
        assert (result.get("error") or {}).get("code") == "ACCESS_VIOLATION", result
    assert "unrooted_new" not in worker.model_node.list.items
    assert not [call for call in worker.model_node.list.calls if call[0] == "create"]
    assert tickets == []


def test_nonloopback_endpoint_refuses_path_shape_before_create(backend, tmp_path):
    """Without the loopback host proof, adjudication is unavailable: refuse."""
    b, worker, ref, tickets = backend
    b.project_authorizer = lambda project_id, permission: {"workspace": str(tmp_path)}
    b.endpoint_key = "10.0.0.5:2036"  # identity input only; never contacted
    inside = str(tmp_path / "inputs" / "a.dat")
    body = _body([{"name": "string", "value": tv([[inside, "ok"]], "string", [1, 2])}], "remotehost_new")
    try:
        result = _create(b, body, ref)
    except ExecutionContractError as exc:
        assert exc.code == "ACCESS_VIOLATION", exc
    else:
        assert result.get("success") is False, result
        assert (result.get("error") or {}).get("code") == "ACCESS_VIOLATION", result
    assert "remotehost_new" not in worker.model_node.list.items
    assert not [call for call in worker.model_node.list.calls if call[0] == "create"]
    assert tickets == []


def test_proven_in_project_existing_path_still_creates(backend, tmp_path):
    """A plain existing file proven inside the durable root passes untouched."""
    b, worker, ref, tickets = backend
    b.project_authorizer = lambda project_id, permission: {"workspace": str(tmp_path)}
    b.endpoint_key = "127.0.0.1:2036"
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    target = inputs / "a.dat"
    target.write_text("synthetic project data\n")
    body = _body([{"name": "string", "value": tv(str(target))}], "inroot_new")
    result = _create(b, body, ref)
    assert result["success"] is True, result
    assert worker.model_node.list.items["inroot_new"].values["string"] == str(target)
    assert len(tickets) == 1


def test_plain_text_initial_properties_still_create(backend, tmp_path):
    """Plain text/expressions/units are never refused by the shape screen."""
    b, worker, ref, tickets = backend
    b.project_authorizer = lambda project_id, permission: {"workspace": str(tmp_path)}
    b.endpoint_key = "127.0.0.1:2036"
    body = _body([{"name": "string", "value": tv("linear")},
                  {"name": "expr", "value": tv("1 [m/s^-1] + f(x)")}], "plain_new")
    result = _create(b, body, ref)
    assert result["success"] is True, result
    assert worker.model_node.list.items["plain_new"].values["string"] == "linear"
    assert len(tickets) == 1


def test_bare_relative_name_stays_undecided_not_safety_claim(backend, tmp_path):
    """Recorded open item (NOT a safety claim): the shape screen has no rule
    for bare relative names, so ``f1`` is neither classified nor refused and
    the create succeeds.  The open failure for bare ``f1`` semantics stays
    pinned RED by tests/test_t20_bare_relative_semantics_20261108.py; this
    test must not be read as ``f1`` being proven safe or contained."""
    b, worker, ref, tickets = backend
    b.project_authorizer = lambda project_id, permission: {"workspace": str(tmp_path)}
    b.endpoint_key = "127.0.0.1:2036"
    assert classify_string_leaf("f1") is None  # no decision, not "proven safe"
    body = _body([{"name": "string", "value": tv("f1")}], "bare_new")
    result = _create(b, body, ref)
    assert result["success"] is True, result
    assert len(tickets) == 1


def test_execute_node_action_dispatch_screen_refuses_before_create(backend):
    """Engine dispatch-boundary re-check (defense in depth): a provided guard
    refuses the initial properties strictly before the create call, so no node
    is created and the dispatch witness would stay mutation-free."""
    b, worker, ref, tickets = backend
    body = {"parent": ROOT, "collection": "feature", "tag": "engine_new",
            "type_id": "Rectangle",
            "properties": [{"name": "string", "value": tv("/tmp/synthetic-outside-target.dat")}]}
    prepared = prepare_node_action(worker, "m", "node.create", body)

    def guard(typed_value, *, where):
        raise PreWriteRefusal("ACCESS_VIOLATION", f"screened at {where}")

    with pytest.raises(PreWriteRefusal) as caught:
        execute_node_action(worker, "m", "node.create", prepared,
                            model_ref=ref, path_guard=guard)
    assert caught.value.code == "ACCESS_VIOLATION"
    assert caught.value.stage == "validation"
    assert "engine_new" not in worker.model_node.list.items
    assert not [call for call in worker.model_node.list.calls if call[0] == "create"]
    # Without a guard the adapter behaves exactly as before (direct adapter
    # callers carry their own obligation; this is not the managed route).
    result = execute_node_action(worker, "m", "node.create", prepared, model_ref=ref)
    assert result["success"] is True
    assert "engine_new" in worker.model_node.list.items
