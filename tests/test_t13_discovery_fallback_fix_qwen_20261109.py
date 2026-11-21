"""T13 Python discovery-fallback UNKNOWN handling fix tests (qwen, 2026-11-09).

Contract under test (fallback path only — plain workers without
``probe_children``/``walk_nodes``, disposable fake endpoints, no COMSOL,
Worker service, SSH, or real model touched):

* after the first structured worker/engine UNKNOWN (``EXECUTION_STATE_UNKNOWN``
  code, or an ``execution_state_unknown``/``engine_state_unknown`` record, or a
  cause chain carrying one — the project's existing structured
  representations read by ``_worker_failure_unknown``), the same discovery
  operation must make **no further dispatch** to that engine and must surface
  the operation as unknown (raised ``EXECUTION_STATE_UNKNOWN`` or an
  explicit non-complete unknown marker);
* a completed ordinary getter exception must be an explicit error row with
  ``complete=false`` — never flattened into "getter absent"/success;
* positive controls: genuine absence (``AttributeError`` -> API_UNSUPPORTED),
  METHOD_NOT_APPLICABLE refusals, message text that merely *mentions*
  EXECUTION_STATE_UNKNOWN, and plain engine failures on non-UNKNOWN probes
  must keep their existing tolerant/continue behaviour and must not be
  converted into an UNKNOWN abort.

These tests complement ``tests/test_t13_discovery_unknown_qwen_20261109.py``
(the expected-contract RED supplied by main) with the additional fault shapes
the fix must cover: ``tags()`` probes, structured-flag representations,
child resolution, and each identity getter's three failure classes.
"""

from __future__ import annotations

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._g2_engine import (
    COLLECTION_CANDIDATES,
    children_node,
    find_nodes,
)

CANDIDATE_NAMES = tuple(row["collection"] for row in COLLECTION_CANDIDATES)


def _unknown(message: str = "dispatch state unknown") -> ExecutionContractError:
    return ExecutionContractError("EXECUTION_STATE_UNKNOWN", message)


class _Container:
    def __init__(self, node, name: str, log: list[str]):
        self._node, self._name, self._log = node, name, log

    def tags(self):
        self._log.append(f"{self._name}.tags")
        err = self._node._raise_once.pop(f"{self._name}.tags", None)
        if err is not None:
            self._log.append(f"FAULT:{self._name}.tags")
            raise err
        return list(self._node._children.get(self._name, {}))


class _Node:
    """Fake engine node endpoint recording every dispatched call in order.

    ``raise_once`` maps a call key (collection accessor name, ``<name>.tags``
    for the tags() probe, ``<name>.get`` for tagged child resolution, and the
    identity getters ``tag``/``getType``/``label``) to one exception raised on
    the first such call.
    """

    def __init__(self, *, tag: str, type_id: str, label: str = "",
                 children: dict[str, dict] | None = None,
                 raise_once: dict[str, BaseException] | None = None,
                 log: list[str] | None = None):
        self._tag, self._type_id, self._label = tag, type_id, label
        self._children = {name: dict(kids) for name, kids in (children or {}).items()}
        self._raise_once = dict(raise_once or {})
        self._log = log if log is not None else []

    def _fail(self, key: str) -> None:
        err = self._raise_once.pop(key, None)
        if err is not None:
            self._log.append(f"FAULT:{key}")
            raise err

    def tag(self):
        self._log.append("tag")
        self._fail("tag")
        return self._tag

    def getType(self):
        self._log.append("getType")
        self._fail("getType")
        return self._type_id

    def label(self):
        self._log.append("label")
        self._fail("label")
        return self._label

    def __getattr__(self, name):
        if name not in CANDIDATE_NAMES:
            raise AttributeError(name)

        def accessor(*args):
            if not args:
                self._log.append(name)
                self._fail(name)
                return _Container(self, name, self._log)
            self._log.append(f"{name}.get:{args[0]}")
            self._fail(f"{name}.get")
            child = self._children.get(name, {}).get(args[0])
            if child is None:
                raise ExecutionContractError("NODE_NOT_FOUND", f"no {name} child {args[0]!r}")
            return child

        return accessor


class _PlainWorker:
    """No probe_children/walk_nodes -> genuine Python fallback discovery."""

    def __init__(self, node, generation: int = 7):
        self.node = node
        self._generation = generation

    @property
    def generation(self):
        return self._generation

    def client(self):
        outer = self

        class _Client:
            def model(self, _tag):
                return outer.node

        return _Client()


def _assert_no_dispatch_after(log: list[str], fault_key: str) -> None:
    assert f"FAULT:{fault_key}" in log, log
    assert log[log.index(f"FAULT:{fault_key}") + 1:] == [], log


# ---------------------------------------------------------------------------
# 1. Collection-probe UNKNOWN must abort the whole operation immediately.
# ---------------------------------------------------------------------------

def test_tags_probe_unknown_aborts_children_and_forbids_later_probes():
    log: list[str] = []
    model = _Node(tag="model", type_id="Model", log=log,
                  raise_once={"mesh.tags": _unknown("mesh tags() dispatch state unknown")},
                  children={"component": {"comp1": _Node(tag="comp1", type_id="Component", log=log)},
                            "material": {"mat1": _Node(tag="mat1", type_id="Material", log=log)}})
    with pytest.raises(ExecutionContractError) as exc:
        children_node(_PlainWorker(model), "m", {"segments": []}, limit=500)
    assert exc.value.code == "EXECUTION_STATE_UNKNOWN"
    _assert_no_dispatch_after(log, "mesh.tags")
    assert "material" not in log  # candidates after the UNKNOWN were never touched


def test_structured_execution_state_unknown_flag_also_aborts_children():
    log: list[str] = []
    dropped = ExecutionContractError("ENGINE_CALL_FAILED", "transport state dropped")
    dropped.failure = {"execution_state_unknown": True}
    model = _Node(tag="model", type_id="Model", log=log,
                  raise_once={"geom": dropped},
                  children={"component": {"comp1": _Node(tag="comp1", type_id="Component", log=log)}})
    with pytest.raises(ExecutionContractError) as exc:
        children_node(_PlainWorker(model), "m", {"segments": []}, limit=500)
    assert exc.value.code == "EXECUTION_STATE_UNKNOWN"
    _assert_no_dispatch_after(log, "geom")


def test_child_resolve_unknown_aborts_walk_and_forbids_later_probes():
    log: list[str] = []
    model = _Node(tag="model", type_id="Model", log=log,
                  children={"component": {"comp1": _Node(tag="comp1", type_id="GeometryComponent", log=log)}},
                  raise_once={"component.get": _unknown("component(comp1) dispatch state unknown")})
    with pytest.raises(ExecutionContractError) as exc:
        find_nodes(_PlainWorker(model), "m", {"tag": "comp1"}, limit=10)
    assert exc.value.code == "EXECUTION_STATE_UNKNOWN"
    _assert_no_dispatch_after(log, "component.get")


# ---------------------------------------------------------------------------
# 2. Identity-getter UNKNOWN must not be swallowed into "absent"/match-dropped.
# ---------------------------------------------------------------------------

def test_tag_getter_unknown_aborts_walk_and_forbids_later_probes():
    log: list[str] = []
    comp = _Node(tag="comp1", type_id="GeometryComponent", log=log,
                 raise_once={"tag": _unknown("tag() dispatch state unknown")})
    model = _Node(tag="model", type_id="Model", log=log,
                  children={"component": {"comp1": comp}})
    with pytest.raises(ExecutionContractError) as exc:
        find_nodes(_PlainWorker(model), "m", {"type_id": "GeometryComponent"}, limit=10)
    assert exc.value.code == "EXECUTION_STATE_UNKNOWN"
    _assert_no_dispatch_after(log, "tag")


# ---------------------------------------------------------------------------
# 3. Completed ordinary getter failures: explicit incomplete, never "absent".
# ---------------------------------------------------------------------------

def test_plain_getter_failure_forces_incomplete_but_walk_still_continues():
    log: list[str] = []
    comp_bad = _Node(tag="comp1", type_id="GeometryComponent", log=log,
                     raise_once={"getType": RuntimeError("engine getter threw after completing")})
    comp_ok = _Node(tag="comp2", type_id="GeometryComponent", log=log)
    model = _Node(tag="model", type_id="Model", log=log,
                  children={"component": {"comp1": comp_bad, "comp2": comp_ok}})
    result = find_nodes(_PlainWorker(model), "m", {"type_id": "GeometryComponent"}, limit=10)
    assert any(row["code"] == "ENGINE_CALL_FAILED" and "getType" in row["message"]
               for row in result["errors"]), result["errors"]
    assert result["complete"] is False and result["status"] == "INCOMPLETE"
    # ordinary (non-UNKNOWN) failure does not freeze the walk: comp2 still matched
    assert [row["tag"] for row in result["results"]] == ["comp2"]
    assert "FAULT:getType" in log and "label" in log


def test_plain_tag_getter_failure_keeps_path_tag_match_but_not_completeness():
    log: list[str] = []
    comp = _Node(tag="comp1", type_id="GeometryComponent", log=log,
                 raise_once={"tag": RuntimeError("engine tag() threw after completing")})
    model = _Node(tag="model", type_id="Model", log=log, children={"component": {"comp1": comp}})
    result = find_nodes(_PlainWorker(model), "m", {"tag": "comp1"}, limit=10)
    assert [row["tag"] for row in result["results"]] == ["comp1"]
    assert any(row["code"] == "ENGINE_CALL_FAILED" and "tag" in row["message"]
               for row in result["errors"]), result["errors"]
    assert result["complete"] is False and result["status"] == "INCOMPLETE"


# ---------------------------------------------------------------------------
# 4. Positive/negative controls: absence, refusals and decoy text stay ordinary.
# ---------------------------------------------------------------------------

def test_absent_and_not_applicable_getters_stay_tolerated_and_complete():
    log: list[str] = []
    not_applicable = ExecutionContractError("ENGINE_CALL_FAILED", "label() not applicable here")
    not_applicable.failure = {"code": "METHOD_NOT_APPLICABLE"}
    comp = _Node(tag="comp1", type_id="GeometryComponent", log=log,
                 raise_once={"getType": AttributeError("no getType() on this node"),
                             "label": not_applicable})
    model = _Node(tag="model", type_id="Model", log=log, children={"component": {"comp1": comp}})
    result = find_nodes(_PlainWorker(model), "m", {"tag": "comp1"}, limit=10)
    assert result["errors"] == [] and result["complete"] is True
    assert result["status"] == "SUCCEEDED"
    assert [row["tag"] for row in result["results"]] == ["comp1"]


def test_decoy_unknown_text_is_not_an_abort_and_probing_continues():
    log: list[str] = []
    decoy = ExecutionContractError("ENGINE_CALL_FAILED",
                                   "engine said EXECUTION_STATE_UNKNOWN in prose but no structured flag")
    model = _Node(tag="model", type_id="Model", log=log,
                  raise_once={"func": decoy},
                  children={"material": {"mat1": _Node(tag="mat1", type_id="Material", log=log)}})
    result = children_node(_PlainWorker(model), "m", {"segments": []}, limit=500)
    # recorded as an ordinary probe failure with its own code, other
    # candidates (after func in the fixed order) are still probed, and the
    # listing is explicitly incomplete.
    assert any(row["collection"] == "func" and row["code"] == "ENGINE_CALL_FAILED"
               for row in result["errors"]), result["errors"]
    assert not any(row["code"] == "EXECUTION_STATE_UNKNOWN" for row in result["errors"])
    assert "variable" in log  # a candidate after func was dispatched
    assert result["complete"] is False and result["incomplete"] is True


def test_plain_accessor_failure_in_walk_continues_but_reports_incomplete():
    log: list[str] = []
    model = _Node(tag="model", type_id="Model", log=log,
                  raise_once={"func": RuntimeError("engine func accessor threw")},
                  children={"component": {"comp1": _Node(tag="comp1", type_id="GeometryComponent", log=log)}})
    result = find_nodes(_PlainWorker(model), "m", {"tag": "comp1"}, limit=10)
    assert any(row["collection"] == "func" and row["code"] == "ENGINE_CALL_FAILED"
               for row in result["errors"]), result["errors"]
    assert [row["tag"] for row in result["results"]] == ["comp1"]
    assert result["complete"] is False and result["status"] == "INCOMPLETE"
