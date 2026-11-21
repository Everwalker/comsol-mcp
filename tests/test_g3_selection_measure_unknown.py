"""W13 selection measure/validate/query_spatial UNKNOWN propagation (task-owned, fake-only).

Permanent TDD companion for the 2026-11-10 W13/W15 live integration.  It calls the
actual W13 ``selection_measure`` / ``selection_validate`` / ``selection_query_spatial``
public-domain functions, the actual explicit-selection resolver, the actual
``_require_component`` / ``_require_geometry`` helpers and the actual
``measure_selection`` adapter.  Only in-memory synthetic worker/model/component/
geometry/measure objects implement the invoked methods: no real COMSOL, Worker,
daemon, model file, network or filesystem target is touched.  The measurement
selection setup (``selection.geom(dim)`` / ``selection.set``) is transient fake
state and is not claimed to be a pre-write/no-mutation proof; real Worker
acquisition/transport/quiescence stays unverified by these fakes.

Identity gate modes — resolved exactly once at module import (i.e. before test
execution) from a snapshot of the environment, then never re-read: an unrelated
fixture that sets or clears a same-named key later cannot alter the selected
mode or the captured pins, and this module never logs any G3L environment value:

* Complete external mode: all eight gate keys the gate reads are present at
  import.  Their caller-provided exact paths/SHA values are captured once and
  enforced — at import and before/after every test — against the actually
  loaded module/test paths and current file digests.  A wrong pin fails closed
  at import with no automatic correction.  This is the externally pinned
  identity-evidence mode.
* All-absent mode: none of the eight keys (nor the ``G3L_W15_SHA`` marker) is
  present at import.  Expected checkout paths and digests are derived from
  this checkout's own bytes at import time *without setting any environment
  variable*, and are then verified against the loaded module/test paths and
  digests at import and per test exactly as before.  This is checkout
  self-consistency only — NOT an independent expected-source pin, and it can
  never be claimed as externally pinned acceptance evidence.
* Partial mode: any strict subset of the eight keys — including a lone
  ``G3L_W15_SHA`` — fails closed at import, *before* any comsol_mcp product
  import and before any test body is defined.  Under ordinary pytest
  discovery the target-scoped read-only adapter in ``tests/conftest.py``
  rejects first with exactly one collection error; this module's own gate is
  the second line of defense for any other collector that imports this file
  directly.  This design is not claimed to be universally compatible with
  every pytest plugin, and these synthetic fake-only tests are not
  U02/T14/native acceptance.

Env identity gate (exact-path, not tree-membership):
  G3L_ROOT, G3L_TEST, G3L_TEST_SHA,
  G3L_INIT_SHA, G3L_W13_SHA, G3L_W15_SHA, G3L_COMMON_SHA, G3L_ENGINE_SHA,
  G3L_CONTRACT_SHA
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

# Gate key names; G3L_W15_SHA is named in the header above but only acts as a
# partial-configuration marker, never as a pin.
_REQUIRED_GATE_KEYS = (
    "G3L_ROOT",
    "G3L_TEST",
    "G3L_TEST_SHA",
    "G3L_INIT_SHA",
    "G3L_W13_SHA",
    "G3L_COMMON_SHA",
    "G3L_ENGINE_SHA",
    "G3L_CONTRACT_SHA",
)
_MARKER_GATE_KEYS = ("G3L_W15_SHA",)

_MODULE_BASENAMES = {
    "G3L_INIT_SHA": "__init__.py",
    "G3L_W13_SHA": "_g3_w13.py",
    "G3L_COMMON_SHA": "_g3_common.py",
    "G3L_ENGINE_SHA": "_g2_engine.py",
    "G3L_CONTRACT_SHA": "_execution_contract.py",
}


class G3LIdentityGateError(RuntimeError):
    """Raised by the identity gate; messages never include G3L env values."""


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- mode resolution: exactly once, before any product import ---------------
_ENV_SNAPSHOT = {
    key: os.environ[key]
    for key in _REQUIRED_GATE_KEYS + _MARKER_GATE_KEYS
    if key in os.environ
}
_present_required = [key for key in _REQUIRED_GATE_KEYS if key in _ENV_SNAPSHOT]
_present_marker = [key for key in _MARKER_GATE_KEYS if key in _ENV_SNAPSHOT]

if len(_present_required) == len(_REQUIRED_GATE_KEYS):
    _MODE = "complete-external-pins"
    _CAPTURED_PINS = {key: _ENV_SNAPSHOT[key] for key in _REQUIRED_GATE_KEYS}
elif _present_required or _present_marker:
    raise G3LIdentityGateError(
        "g3l-identity-gate: partial G3L identity configuration — "
        f"present-key-names={sorted(_ENV_SNAPSHOT)} "
        f"missing-key-names={[key for key in _REQUIRED_GATE_KEYS if key not in _ENV_SNAPSHOT]}; "
        "failing closed before product import (values not logged)"
    )
else:
    _MODE = "checkout-derived(not externally pinned evidence)"
    _CAPTURED_PINS = None

import comsol_mcp
import comsol_mcp._execution_contract as contract
import comsol_mcp._g2_engine as engine
import comsol_mcp._g3_common as common
import comsol_mcp._g3_w13 as w13

# Expected paths/digests are fixed once here — from the caller-captured pins
# in complete mode, or from this checkout's own bytes in all-absent mode —
# and are never re-read from the environment afterwards.
if _MODE == "complete-external-pins":
    _expected_root = Path(_CAPTURED_PINS["G3L_ROOT"]).resolve()
    _expected_test = Path(_CAPTURED_PINS["G3L_TEST"]).resolve()
    _expected_digests = {
        key: _CAPTURED_PINS[key] for key in _REQUIRED_GATE_KEYS if key.endswith("_SHA")
    }
else:
    _expected_root = Path(__file__).resolve().parent.parent
    _expected_test = Path(__file__).resolve()
    _expected_digests = {"G3L_TEST_SHA": _digest(_expected_test)}
    _expected_digests.update({
        key: _digest(_expected_root / "comsol_mcp" / name)
        for key, name in _MODULE_BASENAMES.items()
    })


def verify_identity() -> None:
    current = Path(__file__).resolve()
    if current != _expected_test:
        raise G3LIdentityGateError(
            f"g3l-identity-gate: loaded test path != G3L_TEST pin (mode={_MODE}; values not logged)"
        )
    if _digest(current) != _expected_digests["G3L_TEST_SHA"]:
        raise G3LIdentityGateError(
            f"g3l-identity-gate: current test-file digest != G3L_TEST_SHA pin (mode={_MODE}; values not logged)"
        )
    pins = (
        (comsol_mcp, "__init__.py", "G3L_INIT_SHA"),
        (w13, "_g3_w13.py", "G3L_W13_SHA"),
        (common, "_g3_common.py", "G3L_COMMON_SHA"),
        (engine, "_g2_engine.py", "G3L_ENGINE_SHA"),
        (contract, "_execution_contract.py", "G3L_CONTRACT_SHA"),
    )
    for module, name, key in pins:
        expected = _expected_root / "comsol_mcp" / name
        if expected.is_symlink():
            raise G3LIdentityGateError(
                f"g3l-identity-gate: expected source is a symlink for {key} (mode={_MODE}; values not logged)"
            )
        if Path(module.__file__).resolve() != expected:
            raise G3LIdentityGateError(
                f"g3l-identity-gate: loaded module file != pinned path for {key} (mode={_MODE}; values not logged)"
            )
        if _digest(expected) != _expected_digests[key]:
            raise G3LIdentityGateError(
                f"g3l-identity-gate: source digest != pin for {key} (mode={_MODE}; values not logged)"
            )


verify_identity()


@pytest.fixture(autouse=True)
def identity_guard():
    verify_identity()
    yield
    verify_identity()


# ---------------------------------------------------------------------------
# In-memory fakes (the only fixtures).  Every engine-visible call is appended
# to one shared ordered log, so "no later call after the fault" is assertable.
# ---------------------------------------------------------------------------


class FakeTags:
    def __init__(self, calls, label, tags, fail=False):
        self.calls, self.label, self.values, self.fail = calls, label, tags, fail

    def tags(self):
        self.calls.append(self.label + ".tags")
        if self.fail:
            raise contract.ExecutionContractError("EXECUTION_STATE_UNKNOWN", "synthetic tags fault")
        return list(self.values)


class FakeGeometry:
    def __init__(self, calls, fault):
        self.calls, self.fault = calls, fault

    def lengthUnit(self):
        self.calls.append("geometry.lengthUnit")
        if self.fault == "unit_unknown":
            raise contract.ExecutionContractError("EXECUTION_STATE_UNKNOWN", "synthetic unit fault")
        if self.fault == "unit_ordinary":
            raise RuntimeError("ordinary missing unit")
        return "mm"


class FakeSelection:
    def __init__(self, calls, fault):
        self.calls, self.fault = calls, fault

    def geom(self, *args):
        self.calls.append("selection.geom")
        if args:
            assert args == (2,)
            return None
        if self.fault == "geom_probe_unknown":
            raise contract.ExecutionContractError("EXECUTION_STATE_UNKNOWN", "synthetic selection.geom fault")
        return "geomA"

    def set(self, entities):
        self.calls.append("selection.set")
        assert entities == [1]


class FakeMeasure:
    def __init__(self, calls, fault):
        self.calls, self.fault = calls, fault
        self.selection_node = FakeSelection(calls, fault)

    def selection(self):
        self.calls.append("measure.selection")
        return self.selection_node

    def getVolume(self):
        self.calls.append("measure.getVolume")
        if self.fault == "metric_unknown":
            raise contract.ExecutionContractError("EXECUTION_STATE_UNKNOWN", "synthetic metric fault")
        if self.fault == "metric_ordinary":
            raise RuntimeError("ordinary metric failure")
        if self.fault == "metric_array":
            return [4.0, 5.0]
        return 4.0

    def getArea(self):
        self.calls.append("measure.getArea")
        return 2.0


class FakeComponent:
    def __init__(self, calls, fault):
        self.calls, self.fault = calls, fault
        self.measure_node = FakeMeasure(calls, fault)
        self.geometry = FakeGeometry(calls, fault)

    def measure(self):
        self.calls.append("component.measure")
        return self.measure_node

    def geom(self, *args):
        if not args:
            self.calls.append("component.geom()")
            if self.fault == "geom_unknown":
                raise contract.ExecutionContractError("EXECUTION_STATE_UNKNOWN", "synthetic geom fault")
            return FakeTags(self.calls, "geometry_collection", ["geomA"], self.fault == "geom_tags_unknown")
        self.calls.append("component.geom(tag)")
        assert args == ("geomA",)
        return self.geometry


class FakeModel:
    def __init__(self, calls, fault):
        self.calls, self.fault = calls, fault
        self.component_node = FakeComponent(calls, fault)

    def component(self, *args):
        if not args:
            self.calls.append("model.component()")
            if self.fault == "component_unknown":
                raise contract.ExecutionContractError("EXECUTION_STATE_UNKNOWN", "synthetic component fault")
            tags = ["other"] if self.fault == "component_missing" else ["compA"]
            return FakeTags(self.calls, "component_collection", tags)
        self.calls.append("model.component(tag)")
        assert args == ("compA",)
        return self.component_node


class FakeWorker:
    def __init__(self, fault):
        self.calls = []
        self.model_node = FakeModel(self.calls, fault)

    def client(self):
        self.calls.append("worker.client")
        return self

    def model(self, model_tag):
        self.calls.append("worker.model")
        assert model_tag == "fake-model"
        return self.model_node


def measure_spec(with_geometry=True, with_component=True):
    spec = {"kind": "explicit", "entity_dimension": 2, "entities": [1]}
    if with_component:
        spec["component"] = "compA"
    if with_geometry:
        spec["geometry"] = "geomA"
    return spec


def execute(route, fault, *, with_geometry=True, with_component=True):
    worker = FakeWorker(fault)
    selection = measure_spec(with_geometry, with_component)
    if route == "measure":
        action = lambda: w13.selection_measure(worker, "fake-model", {
            "selection": selection, "metrics": ["volume", "area"]})
    elif route == "validate":
        action = lambda: w13.selection_validate(worker, "fake-model", {
            "selection": selection, "expectations": {"measures": {
                "volume": {"value": 4.0}, "area": {"value": 2.0}}}})
    else:
        raise AssertionError(route)
    caught = None
    result = None
    try:
        result = action()
    except contract.ExecutionContractError as exc:
        caught = exc
    return worker.calls, caught, result


FAULTS = {
    "component_unknown": "model.component()",
    "geom_unknown": "component.geom()",
    "geom_tags_unknown": "geometry_collection.tags",
    "unit_unknown": "geometry.lengthUnit",
    "metric_unknown": "measure.getVolume",
}

EXTRA_STOP_ASSERTIONS = {
    "component_unknown": lambda calls: calls.count("model.component()") == 1,
    "geom_unknown": lambda calls: "component.geom(tag)" not in calls,
    "geom_tags_unknown": lambda calls: "component.geom(tag)" not in calls,
    "unit_unknown": lambda calls: "measure.getVolume" not in calls and "measure.getArea" not in calls,
    "metric_unknown": lambda calls: "measure.getArea" not in calls,
}


# (1) the same 10 required UNKNOWN-abort behaviors as the parent counterexample:
# each fault is reached, surfaces as a typed EXECUTION_STATE_UNKNOWN, and no
# subsequent fake engine call starts.
@pytest.mark.parametrize("route", ["measure", "validate"])
@pytest.mark.parametrize("fault", list(FAULTS))
def test_unknown_propagates_without_followup_engine_call(route, fault):
    calls, caught, result = execute(route, fault)
    assert FAULTS[fault] in calls, (route, fault, calls, result)
    assert caught is not None and caught.code == "EXECUTION_STATE_UNKNOWN", (route, fault, calls, result)
    assert calls[-1] == FAULTS[fault], (route, fault, calls)
    assert EXTRA_STOP_ASSERTIONS[fault](calls), (route, fault, calls)


# (2) the same 6 normal / ordinary optional-miss controls.
@pytest.mark.parametrize("route", ["measure", "validate"])
@pytest.mark.parametrize("fault", ["healthy", "component_missing", "unit_ordinary"])
def test_normal_or_ordinary_optional_miss_keeps_measurement_behavior(route, fault):
    calls, caught, result = execute(route, fault)
    assert caught is None and result is not None, (route, fault, calls, caught)
    assert "measure.getVolume" in calls and "measure.getArea" in calls
    expected_unit = "mm" if fault == "healthy" else None
    if route == "measure":
        assert result["metrics"]["volume"]["value"]["data"] == 4.0
        assert result["metrics"]["volume"]["length_unit"] == expected_unit
    else:
        assert result["verdict"] == "PASS" and result["ok"] is True
        assert result["measures"]["volume"]["length_unit"] == expected_unit


# (3) validate must not PASS when a required measure is an ordinary getter error.
def test_validate_does_not_pass_when_required_metric_is_unavailable():
    calls, caught, result = execute("validate", "metric_ordinary")
    assert caught is None and result is not None, (calls, caught)
    assert result["measures"]["volume"]["status"] == "ERROR"
    assert result["verdict"] != "PASS" and result["ok"] is False, (calls, result)


# (4) measure keeps ordinary per-metric errors and continues later metrics.
def test_measure_preserves_ordinary_metric_error_and_later_metric():
    calls, caught, result = execute("measure", "metric_ordinary")
    assert caught is None and result is not None, (calls, caught)
    assert result["metrics"]["volume"]["error"]["code"]
    assert result["metrics"]["area"]["value"]["data"] == 2.0
    assert calls[-2:] == ["measure.getVolume", "measure.getArea"]


# (5) non-comparable requested metric must not yield PASS.
def test_validate_does_not_pass_when_required_metric_is_not_comparable():
    worker = FakeWorker("metric_array")
    result = w13.selection_validate(worker, "fake-model", {
        "selection": measure_spec(),
        "expectations": {"measures": {"volume": {"value": 4.0}}}})
    assert result["measures"]["volume"]["status"] == "NOT_COMPARABLE", result["measures"]
    assert result["verdict"] != "PASS" and result["ok"] is False, result["verdict"]
    assert any(row["check"] == "measure.volume" and row["status"] == "NOT_COMPARABLE"
               for row in result["checks"]), result["checks"]


# (6) requested measures with an absent component must not yield PASS.
def test_validate_does_not_pass_when_measures_requested_without_component():
    worker = FakeWorker("healthy")
    result = w13.selection_validate(worker, "fake-model", {
        "selection": measure_spec(with_component=False),
        "expectations": {"measures": {"volume": {"value": 4.0}}}})
    assert result["verdict"] != "PASS" and result["ok"] is False, result["verdict"]
    assert result["measures"] == {}
    assert any(row["check"] == "measures" and row["status"] == "ERROR"
               for row in result["checks"]), result["checks"]
    assert "component.measure" not in worker.calls


# (7) fallback selection.geom() probe UNKNOWN on both routes.
@pytest.mark.parametrize("route", ["measure", "validate"])
def test_fallback_selection_geom_unknown_stops_both_routes(route):
    calls, caught, result = execute(route, "geom_probe_unknown", with_geometry=False)
    assert "selection.geom" in calls, (route, calls, result)
    assert caught is not None and caught.code == "EXECUTION_STATE_UNKNOWN", (route, calls, result)
    assert calls[-1] == "selection.geom", (route, calls)
    assert calls.count("selection.geom") == 2, (route, calls)
    assert "geometry.lengthUnit" not in calls, (route, calls)
    assert "measure.getVolume" not in calls and "measure.getArea" not in calls, (route, calls)


# (8) spatial query lengthUnit UNKNOWN must propagate before any temporary
# selection create; ordinary unsupported-unit refusal and successful unit reads
# stay as before.
class SpatialGeom:
    def __init__(self, calls, fault):
        self.calls, self.fault = calls, fault

    def lengthUnit(self):
        self.calls.append("geometry.lengthUnit")
        if self.fault == "unit_unknown":
            raise contract.ExecutionContractError("EXECUTION_STATE_UNKNOWN", "synthetic unit fault")
        if self.fault == "unit_ordinary":
            raise RuntimeError("ordinary missing unit")
        return "mm"


class SpatialComponent:
    def __init__(self, calls, geom_node):
        self.calls, self.geom_node = calls, geom_node

    def geom(self, *args):
        if not args:
            self.calls.append("component.geom()")
            return FakeTags(self.calls, "geometry_collection", ["geomA"])
        self.calls.append("component.geom(tag)")
        assert args == ("geomA",)
        return self.geom_node

    def selection(self):
        self.calls.append("component.selection()")
        raise AssertionError("temporary selection container must not be reached")


class SpatialModel:
    def __init__(self, calls, geom_node):
        self.calls = calls
        self.component_node = SpatialComponent(calls, geom_node)

    def component(self, *args):
        if not args:
            self.calls.append("model.component()")
            return FakeTags(self.calls, "component_collection", ["compA"])
        self.calls.append("model.component(tag)")
        assert args == ("compA",)
        return self.component_node


class SpatialWorker:
    def __init__(self, fault, tolerance_unit):
        self.calls = []
        self.tolerance_unit = tolerance_unit
        self.model_node = SpatialModel(self.calls, SpatialGeom(self.calls, fault))

    def client(self):
        self.calls.append("worker.client")
        return self

    def model(self, model_tag):
        self.calls.append("worker.model")
        assert model_tag == "fake-model"
        return self.model_node


def run_spatial(fault, tolerance_unit="mm"):
    worker = SpatialWorker(fault, tolerance_unit)
    caught = None
    try:
        w13.selection_query_spatial(worker, "fake-model", {
            "component": "compA", "geometry": "geomA", "dimension": 2,
            "query": {"kind": "box", "xmin": 0.0, "xmax": 1.0, "ymin": 0.0, "ymax": 1.0},
            "tolerance": {"value": 0.1, "unit": tolerance_unit}})
    except contract.ExecutionContractError as exc:
        caught = exc
    return worker.calls, caught


def test_spatial_length_unit_unknown_stops_before_temporary_selection():
    calls, caught = run_spatial("unit_unknown")
    assert "geometry.lengthUnit" in calls, calls
    assert caught is not None and caught.code == "EXECUTION_STATE_UNKNOWN", (calls, caught)
    assert calls[-1] == "geometry.lengthUnit", calls
    assert "component.selection()" not in calls, calls


def test_spatial_ordinary_missing_unit_keeps_api_unsupported_refusal():
    calls, caught = run_spatial("unit_ordinary")
    assert caught is not None and caught.code == "API_UNSUPPORTED", (calls, caught)
    assert "geometry.lengthUnit" in calls, calls
    assert "component.selection()" not in calls, calls


def test_spatial_successful_unit_read_is_preserved():
    calls, caught = run_spatial("healthy", tolerance_unit="m")
    assert caught is not None and caught.code == "INVALID_REQUEST", (calls, caught)
    assert "does not match the geometry length unit 'mm'" in str(caught)
    assert "geometry.lengthUnit" in calls, calls
    assert "component.selection()" not in calls, calls
