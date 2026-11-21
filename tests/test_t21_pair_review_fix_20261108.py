"""T21 Pair review-fix regressions (independent review F1-F4, 2026-11-08).

Complements (never replaces) the read-only parent findings file
``tests/test_t21_pair_parent_findings_20261108.py`` and the author's
``tests/test_t21_pair_selection_20261108.py``.  All cases run the real
adapters against the bounded fake engine; no COMSOL, Worker process, SSH or
network is touched, and the Java gate harness runs the production Worker
dispatcher without an engine.

Fix under test (review findings):

* F1 - ``_g3_common.apply_local_selection`` explicit binding must read the
  endpoint's geometry tag (``geom()``) and input dimension
  (``inputDimension()``, falling back to ``dim()`` = *maximum entity
  dimension*, KB AbstractSelection doc 7494/chunk 22758) back and compare
  them against the request; a swallowing endpoint can no longer produce a
  clean ``APPLIED``.  Legitimate paths stay runnable: a named binding, an
  explicit binding with a consistent endpoint, and an explicit binding
  without geometry context (``geom(dim)``, documented as valid only when
  the geometry is unambiguous, KB comsol_api_general.47.11 doc
  4082/chunk 16692) remain applied - the last one with an honest
  ``not_verifiable`` annotation instead of a fabricated geometry claim.
* F2 - after one side's ``ExecutionContractError`` the pair loop stops
  dispatching further side mutations and lists the remaining sides in
  ``not_executed`` (matching the ``_g3_w13`` envelope convention).
* F3 - a failure after ``pair().create()`` was already committed reports
  ``status=PARTIAL_FAILURE``/``partial_change=true`` even when the binding
  loop itself applied nothing.
* F4 - the Worker enforces Pair receiver + zero-argument arity for
  ``source``/``destination`` in real code (not only in a comment), and the
  new ``inputDimension`` allow-list entry is gated to zero-argument calls
  on ``Selection`` receivers only; the harness drives the production
  ``call()`` dispatcher with Proxy receivers and counts reflective calls.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from comsol_mcp import _g3_w14 as w14
from comsol_mcp._execution_contract import ExecutionContractError

try:  # pragma: no cover - import shim
    from tests.test_t21_pair_selection_20261108 import (  # type: ignore[no-redef]
        FLocalSel, FPairNode, pair_world,
    )
except ImportError:  # pragma: no cover
    from test_t21_pair_selection_20261108 import (  # type: ignore[no-redef]
        FLocalSel, FPairNode, pair_world,
    )

REPO = Path(__file__).resolve().parents[1]
WORKER_SOURCE = REPO / "comsol_mcp" / "worker_java" / "PersistentComsolWorker.java"
HARNESS = REPO / "tests" / "java" / "PersistentComsolWorkerPairSideSelectionHarness.java"
COMSOL_ROOT = Path("/Applications/COMSOL64/Multiphysics")


def create_pair(worker, arguments, model_tag: str = "Model") -> dict:
    return w14.OPERATIONS["definition.pair_manage"](worker, model_tag, dict(arguments))


# ---------------------------------------------------------------------------
# F1 - explicit binding must verify endpoint geometry and input dimension
# ---------------------------------------------------------------------------


class SwallowingSel(FLocalSel):
    """geom(gtag,dim) is accepted but the endpoint keeps other_geom/dim 0."""

    def geom(self, *args):
        if args:
            self.calls.append(("geom", args))
            self.geometry_ref = "other_geom"
            self.dimension = 0
            return None
        return self.geometry_ref

    def dim(self):
        return self.dimension


class WrongInputDimension(FLocalSel):
    """dim() agrees with the request but inputDimension() contradicts it.

    KB Selection doc 7700/chunk 23053: ``inputDimension()`` is the input
    dimension while ``dim()`` is only the maximum entity dimension; the
    authoritative probe must win over the weaker one.
    """

    def inputDimension(self) -> int:  # noqa: N802 - Java spelling
        return 0


class ReportingInputDimension(FLocalSel):
    def inputDimension(self) -> int:  # noqa: N802 - Java spelling
        return self.dimension if self.dimension is not None else -1


def test_explicit_endpoint_geometry_mismatch_is_not_applied():
    worker, _, comp1, _, _ = pair_world()
    comp1.collections["pair"].factory = lambda tag, *args: FPairNode(
        tag=tag, type_id=str(args[0]), destination_selection=SwallowingSel(None))
    result = create_pair(worker, {"action": "create", "component": "comp1", "tag": "pair1",
                                  "definition": {"type": "Contact", "geometry": "geom1",
                                                 "destination": {"kind": "explicit", "component": "comp1",
                                                                 "geometry": "geom1", "entity_dimension": 2,
                                                                 "entities": [3]}}})
    assert result["ok"] is False
    assert result["status"] == "EXECUTION_STATE_UNKNOWN"
    assert result["created"] is True
    assert result["partial_change"] is True
    assert result["failed"][0]["side"] == "destination"


def test_explicit_endpoint_input_dimension_mismatch_is_not_applied():
    worker, _, comp1, _, _ = pair_world()
    comp1.collections["pair"].factory = lambda tag, *args: FPairNode(
        tag=tag, type_id=str(args[0]), destination_selection=WrongInputDimension(None))
    result = create_pair(worker, {"action": "create", "component": "comp1", "tag": "pair1",
                                  "definition": {"type": "Contact", "geometry": "geom1",
                                                 "destination": {"kind": "explicit", "component": "comp1",
                                                                 "geometry": "geom1", "entity_dimension": 2,
                                                                 "entities": [3]}}})
    assert result["ok"] is False
    assert result["status"] == "EXECUTION_STATE_UNKNOWN"
    failed = result["failed"][0]
    assert "inputDimension" in failed["message"] or "input dimension" in failed["message"]


def test_explicit_binding_with_authoritative_input_dimension_readback_is_applied():
    worker, _, comp1, _, _ = pair_world()
    comp1.collections["pair"].factory = lambda tag, *args: FPairNode(
        tag=tag, type_id=str(args[0]), destination_selection=ReportingInputDimension(None))
    result = create_pair(worker, {"action": "create", "component": "comp1", "tag": "pair1",
                                  "definition": {"type": "Contact", "geometry": "geom1",
                                                 "destination": {"kind": "explicit", "component": "comp1",
                                                                 "geometry": "geom1", "entity_dimension": 2,
                                                                 "entities": [2, 5]}}})
    assert result["ok"] is True
    assert result["status"] == "APPLIED"
    applied = result["selection"]["destination"]["applied"]
    geom_set = [row for row in applied if row["method"] == "geom/set"]
    assert geom_set and geom_set[0]["dimension_verified_by"] == "inputDimension"
    assert geom_set[0]["geometry_verified"] is True


def test_named_and_conistent_explicit_bindings_stay_runnable():
    worker, _, comp1, _, _ = pair_world()
    result = create_pair(worker, {"action": "create", "component": "comp1", "tag": "pair1",
                                  "definition": {"type": "Contact", "geometry": "geom1",
                                                 "source": {"kind": "named", "component": "comp1", "tag": "sel1"},
                                                 "destination": {"kind": "explicit", "component": "comp1",
                                                                 "geometry": "geom1", "entity_dimension": 2,
                                                                 "entities": [4]}}})
    assert result["ok"] is True
    assert result["status"] == "APPLIED"
    assert result["selection"]["source"]["engine_state"]["named"] == "sel1"
    node = comp1.collections["pair"].items["pair1"]
    assert node.side_selections["destination"].geometry_ref == "geom1"


def test_explicit_without_geometry_context_is_applied_with_honest_note():
    # geom(dim) alone is only valid when the geometry is unambiguous (KB
    # comsol_api_general.47.11); the adapter must keep this legitimate path
    # runnable but may not claim the geometry was verified.
    worker, _, comp1, _, _ = pair_world()
    result = create_pair(worker, {"action": "create", "component": "comp1", "tag": "pair1",
                                  "definition": {"type": "Identity",
                                                 "destination": {"kind": "explicit", "component": "comp1",
                                                                 "entity_dimension": 2, "entities": [3]}}})
    assert result["ok"] is True
    assert result["status"] == "APPLIED"
    geom_set = [row for row in result["selection"]["destination"]["applied"] if row["method"] == "geom/set"]
    assert geom_set and geom_set[0]["geometry_verified"] is False
    assert geom_set[0]["geometry_verification"] == "not_verifiable_no_geometry_context"


# ---------------------------------------------------------------------------
# F2 - one side's contract failure stops further side dispatch
# ---------------------------------------------------------------------------


def test_contract_failure_on_source_lists_destination_as_not_executed_without_calls():
    class TransportUnknownNamed(FLocalSel):
        """models a dispatch-level UNKNOWN: the setter call itself fails."""

        def named(self, *args):
            self.calls.append(("named", args))
            if args:
                raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "dispatch result unknown")
            return self.named_ref

    worker, _, comp1, _, _ = pair_world()
    comp1.collections["pair"].factory = lambda tag, *args: FPairNode(
        tag=tag, type_id=str(args[0]), source_selection=TransportUnknownNamed(None))
    result = create_pair(worker, {"action": "create", "component": "comp1", "tag": "pair1",
                                  "definition": {"type": "Contact", "geometry": "geom1",
                                                 "source": {"kind": "named", "component": "comp1", "tag": "sel1"},
                                                 "destination": {"kind": "named", "component": "comp1", "tag": "sel2"}}})
    assert result["status"] == "EXECUTION_STATE_UNKNOWN"
    destination = comp1.collections["pair"].items["pair1"].side_selections["destination"]
    # no named() setter - with or without args - may reach the destination
    assert not [call for call in destination.calls if call[0] == "named" and call[1]]
    assert destination.named_ref is None
    assert any(item.get("side") == "destination" for item in result["not_executed"])
    assert result["execution_state_unknown"] is True


def test_engine_rejection_on_source_also_stops_destination():
    class NoSource(FPairNode):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.unavailable.add("source")

    worker, _, comp1, _, _ = pair_world()
    comp1.collections["pair"].factory = lambda tag, *args: NoSource(tag=tag, type_id=str(args[0]))
    result = create_pair(worker, {"action": "create", "component": "comp1", "tag": "pair1",
                                  "definition": {"type": "Contact", "geometry": "geom1",
                                                 "source": {"kind": "named", "component": "comp1", "tag": "sel1"},
                                                 "destination": {"kind": "named", "component": "comp1", "tag": "sel2"}}})
    destination = comp1.collections["pair"].items["pair1"].side_selections["destination"]
    assert destination.named_ref is None
    assert not [call for call in destination.calls if call[0] == "named" and call[1]]
    assert any(item.get("side") == "destination" for item in result["not_executed"])
    # the failure was not an UNKNOWN: status is the honest partial envelope
    assert result["status"] == "PARTIAL_FAILURE"
    assert result["partial_change"] is True


# ---------------------------------------------------------------------------
# F3 - a committed pair().create() counts as a partial change by itself
# ---------------------------------------------------------------------------


def test_committed_create_with_failing_first_side_is_partial_failure():
    class NoSource(FPairNode):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.unavailable.add("source")

    worker, _, comp1, _, _ = pair_world()
    comp1.collections["pair"].factory = lambda tag, *args: NoSource(tag=tag, type_id=str(args[0]))
    result = create_pair(worker, {"action": "create", "component": "comp1", "tag": "pair1",
                                  "definition": {"type": "Contact", "geometry": "geom1",
                                                 "source": {"kind": "named", "component": "comp1", "tag": "sel1"}}})
    assert "pair1" in comp1.collections["pair"].items
    assert result["created"] is True
    assert result["applied_count"] == 0
    assert result["status"] == "PARTIAL_FAILURE"
    assert result["partial_change"] is True
    assert result["execution_state_unknown"] is False


# ---------------------------------------------------------------------------
# F4 - Worker receiver/arity gates run in the production dispatcher
# ---------------------------------------------------------------------------


def _java_gate_harness() -> tuple[str, str]:
    javac = shutil.which("javac")
    java = shutil.which("java")
    if javac is None or java is None:
        pytest.skip("NOT_RUN: Java compiler/runtime unavailable for the Worker pair-side gate harness")
    api_dir = COMSOL_ROOT / "apiplugins"
    jars = sorted(api_dir.glob("*.jar")) if api_dir.is_dir() else []
    if not jars:
        pytest.skip("NOT_RUN: COMSOL Java API classpath unavailable for the Worker pair-side gate harness")
    assert javac is not None and java is not None
    return javac, java


def test_java_worker_has_real_pair_side_and_input_dimension_gates():
    source = WORKER_SOURCE.read_text(encoding="utf-8")
    methods_block = source.split("private static final Set<String> METHODS", 1)[1]
    methods_block = methods_block.split("private static final Set<String> MODEL_UTIL", 1)[0]
    names = set(re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"', methods_block))
    assert {"source", "destination", "inputDimension"} <= names
    # the gates must be real dispatcher code, not only comments
    assert "validatePairSideGetterArguments" in source
    assert "validateInputDimensionArguments" in source
    assert re.search(r"instanceof\s+Pair\b", source)
    assert re.search(r"instanceof\s+Selection\b", source)


def test_java_gate_harness_drives_production_dispatcher(tmp_path):
    javac, java = _java_gate_harness()
    jars = sorted((COMSOL_ROOT / "apiplugins").glob("*.jar"))
    classes = tmp_path / "pair-side-gate-classes"
    classes.mkdir()
    classpath = os.pathsep.join(str(path) for path in jars)
    compile_result = subprocess.run(
        [javac, "-encoding", "UTF-8", "-classpath", classpath, "-d", str(classes),
         str(WORKER_SOURCE), str(HARNESS)],
        cwd=REPO, capture_output=True, text=True, timeout=240,
    )
    assert compile_result.returncode == 0, compile_result.stdout + compile_result.stderr
    execution = subprocess.run(
        [java, "-cp", str(classes) + os.pathsep + classpath,
         "comsol_mcp.worker_java.PersistentComsolWorkerPairSideSelectionHarness"],
        cwd=REPO, capture_output=True, text=True, timeout=120,
    )
    assert execution.returncode == 0, execution.stdout + execution.stderr
    assert "T21_PAIR_SIDE_GATE_HARNESS_PASS" in execution.stdout
