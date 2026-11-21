"""T13 cursor integrity probe: synthetic tree, real find_nodes cursor logic."""
from __future__ import annotations

import base64
import json
import pytest

# The attempted in-range tamper run was explicitly BLOCKED before execution.
# Do not execute it, reroute it, or count this protected module as passing.
pytest.skip("BLOCKED: in-range cursor-tamper execution lacks user consent; do not retry or reroute",
            allow_module_level=True)

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._g2_engine import find_nodes
from tests.test_g3_r02_discovery import _workplane_tree


def _first_page(worker):
    page = find_nodes(worker, "m", {"tag": "r1"}, limit=10, model_revision=7,
                      budget={"max_nodes": 1, "max_seconds": 20.0})
    assert page["truncated"] and page["next_cursor"] and not page["complete"], page
    return page


def test_genuine_cursor_can_reach_nested_match_and_complete():
    worker = _workplane_tree()
    first = _first_page(worker)
    final = find_nodes(worker, "m", {"tag": "r1"}, limit=10, model_revision=7,
                       cursor=first["next_cursor"], budget={"max_nodes": 100, "max_seconds": 20.0})
    assert final["complete"] is True, final
    assert any(row["tag"] == "r1" for row in final["results"]), final


def test_tampered_cursor_cannot_claim_complete_after_skipping_nested_match():
    worker = _workplane_tree()
    first = _first_page(worker)
    # Locate the matching node's genuine visit index with small read-only
    # pages. This makes the forged offset plausible (not beyond the tree),
    # so a mere upper-bound check cannot satisfy the contract.
    probe_cursor = first["next_cursor"]
    while True:
        probe = find_nodes(worker, "m", {"tag": "r1"}, limit=10, model_revision=7,
                           cursor=probe_cursor, budget={"max_nodes": 1, "max_seconds": 20.0})
        if any(row["tag"] == "r1" for row in probe["results"]):
            assert probe["next_cursor"] is not None, probe
            skipped_match_at = probe["visited"]
            break
        assert probe["next_cursor"] is not None, probe
        probe_cursor = probe["next_cursor"]
    payload = json.loads(base64.urlsafe_b64decode(first["next_cursor"].encode("ascii")))
    assert skipped_match_at > payload["visited"]
    payload["visited"] = skipped_match_at
    forged = base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).decode()
    try:
        result = find_nodes(worker, "m", {"tag": "r1"}, limit=10, model_revision=7,
                            cursor=forged, budget={"max_nodes": 100, "max_seconds": 20.0})
    except ExecutionContractError as exc:
        assert exc.code in {"INVALID_CURSOR", "CURSOR_STALE"}, exc
    else:
        assert not result["complete"] or any(row["tag"] == "r1" for row in result["results"]), result
