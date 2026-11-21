"""T13 discovery-UNKNOWN behaviour probes (qwen, 2026-11-09).

Purpose: before T13 (authorized delete) can be built on the generic tree
observation path, pin what a structured ``EXECUTION_STATE_UNKNOWN`` actually
does inside ``_g2_engine`` discovery (``children_node`` / ``find_nodes``):

* the UNKNOWN dispatch fence (``result_unknown_dispatch_scope``) is an
  OPT-IN attempt-local latch. Its scope does not itself make continued
  discovery calls safe after a timed-out Worker request;
* what the contract DOES forbid is a false report: probe-level failures must
  appear in ``errors`` and force ``complete=false``/``INCOMPLETE``
  (children_node L1103-1108/L1145, find_nodes L1172-1173/L1205), never be
  flattened into success or NOT_FOUND;
* the production JavaWorker ships ``probe_children``/``walk_nodes`` (single
  engine-side batch); these fallback tests do not establish Java behaviour.

Every injected failure is a real
``ExecutionContractError('EXECUTION_STATE_UNKNOWN', ...)`` raised from a fake
endpoint method, so it travels the genuine production ``_call`` ->
``_probe_children_via_node`` -> ``_walk_fixture`` code paths.
"""

from __future__ import annotations

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._g2_engine import (
    COLLECTION_CANDIDATES,
    _call,
    children_node,
    find_nodes,
    result_unknown_dispatch_scope,
)

CANDIDATE_NAMES = tuple(row["collection"] for row in COLLECTION_CANDIDATES)


def _unknown(message: str = "engine state may have changed") -> ExecutionContractError:
    return ExecutionContractError("EXECUTION_STATE_UNKNOWN", message)


def _capture_outcome(call):
    try:
        return call()
    except ExecutionContractError as exc:
        return exc


def _assert_operation_unknown(outcome):
    if isinstance(outcome, ExecutionContractError):
        assert outcome.code == "EXECUTION_STATE_UNKNOWN"
    else:
        assert outcome.get("execution_state_unknown") is True, outcome
        assert outcome.get("complete") is False, outcome


class _Container:
    """Tag container returned by a collection accessor."""

    def __init__(self, node, name: str, log: list[str]):
        self._node, self._name, self._log = node, name, log

    def tags(self):
        self._log.append(f"{self._name}.tags")
        err = self._node._raise_once.pop(f"{self._name}.tags", None)
        if err is not None:
            raise err
        return list(self._node._children.get(self._name, {}))


class _UnknownEndpoint:
    """Fake engine node endpoint that can fail once with a structured error.

    ``raise_once`` maps method name -> exception instance raised (and removed)
    on the first call.  Every accessor and ``tag``/``getType``/``label`` getter
    is dispatched through here so the full call sequence is recorded in
    ``log`` in order.
    """

    def __init__(self, *, tag: str, type_id: str, label: str = "",
                 children: dict[str, dict] | None = None,
                 raise_once: dict[str, BaseException] | None = None,
                 log: list[str] | None = None):
        self._tag, self._type_id, self._label = tag, type_id, label
        self._children = {name: {k: v for k, v in kids.items()}
                          for name, kids in (children or {}).items()}
        self._raise_once = dict(raise_once or {})
        self._log = log if log is not None else []

    # --- identity getters (optional metadata used by _matches_query) ------
    def tag(self):
        self._log.append("tag")
        err = self._raise_once.pop("tag", None)
        if err is not None:
            raise err
        return self._tag

    def getType(self):
        self._log.append("getType")
        err = self._raise_once.pop("getType", None)
        if err is not None:
            self._log.append("FAULT:getType")
            raise err
        return self._type_id

    def label(self):
        self._log.append("label")
        err = self._raise_once.pop("label", None)
        if err is not None:
            raise err
        return self._label

    # --- collection accessors --------------------------------------------
    def __getattr__(self, name):
        if name not in CANDIDATE_NAMES:
            raise AttributeError(name)

        def accessor(*args):
            self._log.append(name if not args else f"{name}:{','.join(map(str, args))}")
            err = self._raise_once.pop(name, None)
            if err is not None:
                self._log.append(f"FAULT:{name}")
                raise err
            if not args:
                return _Container(self, name, self._log)
            child = self._children.get(name, {}).get(args[0])
            if child is None:
                raise ExecutionContractError("NODE_NOT_FOUND", f"no {name} child {args[0]!r}")
            return child

        return accessor


class _FixtureWorker:
    """Plain worker: no probe_children/walk_nodes -> Python fallback path."""

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


# ---------------------------------------------------------------------------
# 1. Fake-endpoint counterexample: UNKNOWN probe failure mid-child-listing.
# ---------------------------------------------------------------------------

def test_children_fallback_unknown_stops_remaining_probes_and_surfaces():
    log: list[str] = []
    model = _UnknownEndpoint(
        tag="model", type_id="Model", log=log,
        raise_once={"geom": _unknown("geom collection probe lost dispatch state")},
        children={"component": {"comp1": _UnknownEndpoint(tag="comp1", type_id="GeometryComponent", log=log)},
                  "material": {"mat1": _UnknownEndpoint(tag="mat1", type_id="Material", log=log)}},
    )
    worker = _FixtureWorker(model)  # no probe_children -> _probe_children_via_node

    outcome = _capture_outcome(lambda: children_node(worker, "m", {"segments": []}, limit=500))
    assert "FAULT:geom" in log
    assert log[log.index("FAULT:geom") + 1:] == [], log
    _assert_operation_unknown(outcome)


def test_find_nodes_fallback_unknown_probe_stops_and_surfaces():
    log: list[str] = []
    comp1 = _UnknownEndpoint(tag="comp1", type_id="GeometryComponent", log=log,
                             raise_once={"material": _unknown("material probe state unknown")})
    model = _UnknownEndpoint(tag="model", type_id="Model", log=log,
                             children={"component": {"comp1": comp1}})
    worker = _FixtureWorker(model)

    outcome = _capture_outcome(lambda: find_nodes(worker, "m", {"tag": "comp1"}, limit=10))
    assert "FAULT:material" in log
    assert log[log.index("FAULT:material") + 1:] == [], log
    _assert_operation_unknown(outcome)


# ---------------------------------------------------------------------------
# 2. Production-shape batch controls (worker exposes probe_children).
# ---------------------------------------------------------------------------

def test_batch_unknown_error_row_forces_incomplete_listing():
    class _BatchWorker(_FixtureWorker):
        def probe_children(self, node, candidates, *, request_id=None, rpc_timeout_s=None):
            return {"children": [{"collection": "material", "tag": "mat1"}],
                    "errors": [{"collection": "physics", "code": "EXECUTION_STATE_UNKNOWN",
                                "message": "engine dispatch state unknown"}]}

    model = _UnknownEndpoint(tag="model", type_id="Model")
    result = children_node(_BatchWorker(model), "m", {"segments": []}, limit=100)
    assert result["children"] == [{"collection": "material", "tag": "mat1"}]
    assert result["errors"][0]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert result["complete"] is False and result["incomplete"] is True


def test_batch_invalid_reply_hard_fails_unknown_not_success():
    class _BrokenBatchWorker(_FixtureWorker):
        def probe_children(self, node, candidates, *, request_id=None, rpc_timeout_s=None):
            return ["not", "a", "mapping"]

    model = _UnknownEndpoint(tag="model", type_id="Model")
    with pytest.raises(ExecutionContractError) as exc:
        children_node(_BrokenBatchWorker(model), "m", {"segments": []}, limit=100)
    assert exc.value.code == "EXECUTION_STATE_UNKNOWN"


# ---------------------------------------------------------------------------
# 3. Optional-getter semantics must stay intact (positive controls).
# ---------------------------------------------------------------------------

def test_unsupported_or_not_applicable_accessors_stay_omitted_not_errors():
    log: list[str] = []
    not_applicable = ExecutionContractError("ENGINE_CALL_FAILED", "method not applicable here")
    not_applicable.failure = {"code": "METHOD_NOT_APPLICABLE"}
    model = _UnknownEndpoint(
        tag="model", type_id="Model", log=log,
        raise_once={"selection": AttributeError("no selection() on this node"),
                    "pair": not_applicable},
        children={"material": {"mat1": _UnknownEndpoint(tag="mat1", type_id="Material", log=log)}},
    )
    result = children_node(_FixtureWorker(model), "m", {"segments": []}, limit=500)
    # API_UNSUPPORTED (missing accessor) and METHOD_NOT_APPLICABLE are "node
    # does not support this collection": omitted, no error row, still complete.
    assert result["errors"] == []
    assert result["complete"] is True
    assert result["children"] == [{"collection": "material", "tag": "mat1"}]


def test_plain_engine_failure_records_error_and_blocks_complete():
    log: list[str] = []
    model = _UnknownEndpoint(
        tag="model", type_id="Model", log=log,
        raise_once={"func": RuntimeError("engine threw a plain exception")},
    )
    result = children_node(_FixtureWorker(model), "m", {"segments": []}, limit=500)
    assert any(row["collection"] == "func" and row["code"] == "ENGINE_CALL_FAILED"
               for row in result["errors"])
    assert result["complete"] is False


def test_call_preserves_structured_unknown_without_wrapping():
    node = _UnknownEndpoint(tag="model", type_id="Model", raise_once={"tag": _unknown("state unknown")})
    with pytest.raises(ExecutionContractError) as exc:
        _call(node, "tag")
    assert exc.value.code == "EXECUTION_STATE_UNKNOWN"
    # _call re-raises the ExecutionContractError branch verbatim; it must not
    # be converted into ENGINE_CALL_FAILED and must not lose the message.
    assert exc.value.args[0] == "state unknown"


# ---------------------------------------------------------------------------
# 4. Opt-in fence discrimination: the first-UNKNOWN latch exists, but only
#    inside result_unknown_dispatch_scope (result-evaluate), attempt-local.
# ---------------------------------------------------------------------------

def test_opt_in_fence_blocks_calls_after_first_unknown_but_only_in_scope():
    log: list[str] = []
    node = _UnknownEndpoint(tag="model", type_id="Model", log=log,
                            raise_once={"tag": _unknown("latched failure")})
    with pytest.raises(ExecutionContractError) as scope_exit:
        with result_unknown_dispatch_scope(node) as attempt:
            with pytest.raises(ExecutionContractError) as first:
                _call(node, "tag")
            assert first.value.code == "EXECUTION_STATE_UNKNOWN"
            assert attempt.first_unknown is first.value
            calls_after = len(log)
            with pytest.raises(ExecutionContractError) as blocked:
                _call(node, "getType")
            # the blocked call returns the latched first exception, and the
            # endpoint was NOT touched again (no further dispatch after the
            # first UNKNOWN inside the fence).
            assert blocked.value is first.value
            assert len(log) == calls_after
    # the scope re-raises the latched UNKNOWN even though the body completed
    # "normally": a fenced attempt cannot swallow a first UNKNOWN.
    assert scope_exit.value is first.value
    # outside the scope the same node dispatches again: the fence is
    # attempt-local, proving discovery endpoints (no scope) have no latch.
    assert node.tag() == "model"


# ---------------------------------------------------------------------------
# 5. Regression for the originally characterized fallback gap. The original
#    characterization and its 9-pass run are preserved in evidence/full.
# ---------------------------------------------------------------------------

def test_fallback_getter_unknown_cannot_silently_drop_match():
    log: list[str] = []
    comp_bad = _UnknownEndpoint(
        tag="comp1", type_id="GeometryComponent", log=log,
        raise_once={"getType": _unknown("getType dispatch state unknown")})
    comp_ok = _UnknownEndpoint(tag="comp2", type_id="GeometryComponent", log=log)
    model = _UnknownEndpoint(tag="model", type_id="Model", log=log,
                             children={"component": {"comp1": comp_bad, "comp2": comp_ok}})
    outcome = _capture_outcome(
        lambda: find_nodes(_FixtureWorker(model), "m", {"type_id": "GeometryComponent"}, limit=10))
    assert "FAULT:getType" in log
    assert log[log.index("FAULT:getType") + 1:] == [], log
    _assert_operation_unknown(outcome)
