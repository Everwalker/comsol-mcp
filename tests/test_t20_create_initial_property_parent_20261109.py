"""T20: node.create initial properties must enter the same partial path screen.

The real managed backend and Unit-A service run against the existing fake
Worker, with the isolation check stubbed. Paths and files are synthetic; this
is not native COMSOL or filesystem containment evidence.
"""
from __future__ import annotations

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from tests.test_unit_a_node_public_api import ROOT, backend, execution, tv


def _invoke_create(b, body, ref, route):
    request = body if route != "registry_call" else {"operation_id": "node.create", "arguments": body}
    return b.invoke(route, request, execution(ref), "create-screen-test", None)


@pytest.mark.parametrize("route", ["node.create", "node_create", "registry_call"])
@pytest.mark.parametrize("form", ["scalar", "matrix", "mixed"])
def test_external_initial_property_is_refused_before_create(backend, tmp_path, route, form):
    b, worker, ref, tickets = backend
    b.project_authorizer = lambda project_id, permission: {"workspace": str(tmp_path)}
    b.endpoint_key = "127.0.0.1:2036"  # identity input only; no socket is opened
    outside = str(tmp_path.parent / "synthetic-external-target.dat")
    unsafe = {"name": "string", "value": tv(outside)}
    if form == "matrix":
        properties = [{"name": "string_matrix", "value": tv([["ok", outside]], "string", [1, 2])}]
    elif form == "mixed":
        properties = [{"name": "expr", "value": tv("linear")}, unsafe]
    else:
        properties = [unsafe]
    body = {"parent": ROOT, "collection": "feature", "tag": "screened_new",
            "type_id": "Rectangle", "properties": properties}
    try:
        result = _invoke_create(b, body, ref, route)
    except ExecutionContractError as exc:
        assert exc.code == "ACCESS_VIOLATION", exc
    else:
        assert result.get("success") is False, result
        assert (result.get("error") or {}).get("code") == "ACCESS_VIOLATION", result
    assert "screened_new" not in worker.model_node.list.items
    assert not [call for call in worker.model_node.list.calls if call[0] == "create"]
    assert tickets == []


def test_safe_initial_plain_string_still_creates(backend, tmp_path):
    b, worker, ref, tickets = backend
    b.project_authorizer = lambda project_id, permission: {"workspace": str(tmp_path)}
    b.endpoint_key = "127.0.0.1:2036"
    body = {"parent": ROOT, "collection": "feature", "tag": "safe_new",
            "type_id": "Rectangle", "properties": [{"name": "string", "value": tv("linear")}]}
    result = _invoke_create(b, body, ref, "node.create")
    assert result["success"] is True, result
    assert worker.model_node.list.items["safe_new"].values["string"] == "linear"
    assert len(tickets) == 1
