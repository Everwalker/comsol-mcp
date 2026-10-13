from __future__ import annotations

import copy

import pytest

from comsol_mcp._g2_contract import ExecutionContractError
from comsol_mcp._java_worker import JavaWorkerError, JavaWorkerTimeout
from comsol_mcp._w23_basis_v2_provenance import _attempt, read_native_mode_provenance
from tests.test_w23_mode_basis_v2_native_plan import _request
from tools.w23_mode_basis_v2_aggregate import AggregationError, _verify_native_mode_provenance


FREQUENCY = 193.414489032258e12


class _FeatureList:
    def __init__(self, features):
        self._features = features

    def tags(self):
        return list(self._features)

    def get(self, tag):
        return self._features[tag]


class _Selection:
    def __init__(self, entities):
        self._entities = entities

    def entities(self, dimension):
        assert dimension == 2
        return list(self._entities)


class _PropertyFeature:
    def __init__(self, tag, kind, properties, *, selection=None):
        self._tag = tag
        self._kind = kind
        self._properties = dict(properties)
        self._selection = selection

    def tag(self):
        return self._tag

    def name(self):
        return f"Name {self._tag}"

    def getType(self):
        return self._kind

    def hasProperty(self, name):
        return name in self._properties

    def getString(self, name):
        value = self._properties[name]
        if not isinstance(value, str):
            raise TypeError(f"{name} is not a string property")
        return value

    def getInt(self, name):
        value = self._properties[name]
        if type(value) is not int:
            raise TypeError(f"{name} is not an integer property")
        return value

    def selection(self):
        if self._selection is None:
            raise AttributeError("selection")
        return self._selection


class _SolverStep(_PropertyFeature):
    def __init__(self, tag, study, step):
        super().__init__(tag, "StudyStep", {"study": study, "studystep": step})
        self._children = _FeatureList({})

    def feature(self):
        return self._children


class _SolverSequence:
    def __init__(self, step_tag="bmaOutput3d", study_tag="std3d"):
        self._study_tag = study_tag
        self._features = _FeatureList({"st1": _SolverStep("st1", study_tag, step_tag)})

    def study(self):
        return self._study_tag

    def feature(self, tag=None):
        return self._features if tag is None else self._features.get(tag)


class _SolutionInfo:
    def __init__(self, *, mode_inner, solver_tag="solBMA"):
        self.mode_inner = mode_inner
        self.solver_tag = solver_tag

    def getSolverSequence(self, outer):
        assert outer == 1
        return self.solver_tag

    def getSol(self, outer):
        assert outer == 1
        return self.solver_tag

    def getSolnum(self, outer, strict):
        assert outer == 1 and strict is True
        return [1, 2]


class _ModeSolution:
    def __init__(self, inner):
        self._inner = inner

    def getSolutioninfo(self):
        return _SolutionInfo(mode_inner=self._inner)


class _Study:
    def __init__(self, *, bma_port="2", mode_freq="f0", neigs=2,
                 step_tag="bmaOutput3d", step_type="BoundaryModeAnalysis"):
        self._bma = _PropertyFeature(
            step_tag, step_type,
            {"PortName": bma_port, "modeFreq": mode_freq, "neigs": neigs})
        self._features = _FeatureList({step_tag: self._bma})

    def feature(self, tag=None):
        return self._features if tag is None else self._features.get(tag)

    def getSolverSequences(self, kind):
        assert kind == "SolverSequence"
        return ["solBMA"]


class _Physics:
    def __init__(self, *, port_mode_number=1, port_name="2", port_type="Numeric",
                 entity_ids=(17, 18)):
        port = _PropertyFeature("portOut3d", "Port", {
            "PortType": port_type, "PortName": port_name,
            "PortModeNumber": port_mode_number,
        }, selection=_Selection(entity_ids))
        self._features = _FeatureList({"portOut3d": port})

    def getType(self):
        return "ElectromagneticWaves"

    def feature(self, tag=None):
        return self._features if tag is None else self._features.get(tag)


class _Component:
    def __init__(self, **port_options):
        self._physics = _Physics(**port_options)

    def physics(self, tag):
        assert tag == "ewfd"
        return self._physics


class _Param:
    def evaluate(self, expression, unit):
        assert unit == "Hz"
        return FREQUENCY if expression == "f0" else FREQUENCY / 2.0


class _NativeModel:
    def __init__(self, *, bma_port="2", mode_freq="f0", neigs=2,
                 step_tag="bmaOutput3d", port_mode_number=1,
                 port_name="2", port_type="Numeric", entity_ids=(17, 18),
                 solver_tag="solBMA", step_type="BoundaryModeAnalysis"):
        self._component = _Component(port_mode_number=port_mode_number,
                                     port_name=port_name, port_type=port_type,
                                     entity_ids=entity_ids)
        self._study = _Study(bma_port=bma_port, mode_freq=mode_freq,
                             neigs=neigs, step_tag=step_tag, step_type=step_type)
        self._sequence = _SolverSequence(step_tag=step_tag)
        self._solver_tag = solver_tag
        self._solutions = {"sModeA": _ModeSolution(1), "sModeB": _ModeSolution(2)}

    def component(self, tag):
        assert tag == "comp3d"
        return self._component

    def sol(self, tag=None):
        if tag is None:
            return _FeatureList(self._solutions)
        if tag in self._solutions:
            return self._solutions[tag]
        if tag == self._solver_tag:
            return self._sequence
        raise KeyError(tag)

    def study(self, tag=None):
        if tag is None:
            return _FeatureList({"std3d": self._study})
        assert tag == "std3d"
        return self._study

    def param(self):
        return _Param()


def _lineage_inputs(*, model=None):
    request = copy.deepcopy(_request())
    # This fixture models a single output BMA with two stored inner solutions.
    request["basis_modes"][0]["source"].update(inner_index=1, solnum=1)
    request["basis_modes"][1]["source"].update(inner_index=2, solnum=2)
    roles = {}
    for index, mode in enumerate(request["basis_modes"]):
        source = mode["source"]
        roles[f"mode_{index}"] = {
            "source": dict(source),
            "native_binding": {"binding_complete": True,
                               "dataset": source["dataset_id"],
                               "solution": source["solution_id"],
                               "component": "comp3d", "geometry": "geom3d"},
            "solution_axes": {"outer_index": 1, "inner_index": source["inner_index"],
                              "solnum": source["solnum"],
                              "parameters_by_pair": {"lambda": (1.55e-6 + index * 1e-9, "m")}},
            "mode_axis_parameter": "lambda",
        }
    return request, roles, model or _NativeModel(), {"entity_ids": [17, 18]}


def test_mode_provenance_keeps_selected_solution_producer_step_unverified_and_separates_port_mode_number():
    request, roles, model, selection = _lineage_inputs()

    result = read_native_mode_provenance(model, request, roles, selection)

    assert result["configuration_and_solution_lineage_status"] == "UNVERIFIED"
    assert result["configuration_containment_status"] == "SOLUTIONINFO_SEQUENCE_CONTAINS_OUTPUT_PORT_BMA_CONFIGURATION"
    assert result["status"] == "UNVERIFIED"
    assert result["basis_ordinal_semantics"] == "BASIS_EIGENSOLUTION_ORDINAL_NOT_NUMERIC_PORT_MODE_NUMBER"
    assert result["numeric_port"]["feature_tag"] == "portOut3d"
    assert result["numeric_port"]["port_name"] == "2"
    assert result["numeric_port"]["port_mode_number"] == 1
    for index in range(2):
        row = result["mode_readbacks"][f"mode_{index}"]
        assert row["basis_mode_ordinal"] == index + 1
        assert row["numeric_port_mode_number"] == 1
        assert row["solution_info_readback"]["inner_index"] == index + 1
        assert row["solution_info_readback"]["solnum"] == index + 1
        assert row["solution_to_solver_sequence"]["solver_sequence_tag"] == "solBMA"
        assert row["solver_to_bma_step"]["step_tag"] == "bmaOutput3d"
        assert row["solver_to_bma_step"]["status"] == "UNVERIFIED_SELECTED_SOLUTION_PRODUCER_STEP"
        assert row["solver_to_bma_step"]["configuration_containment_status"] == (
            "SOLUTIONINFO_SEQUENCE_CONTAINS_OUTPUT_PORT_BMA_CONFIGURATION")
        assert row["solver_to_bma_step"]["producer_step_binding_status"] == (
            "UNVERIFIED_SELECTED_SOLUTION_PRODUCER_STEP_API_UNAVAILABLE")
        assert row["solver_to_bma_step"]["neigs"] == 2
        assert row["basis_ordinal_to_field_mapping_status"] == "UNVERIFIED_NATIVE_FIELD_SAMPLE_REQUIRED"
    assert result["mode_readbacks"]["mode_1"]["solution_info_readback"]["mode_axis_parameter"] == {
        "parameter": "lambda", "value": 1.55e-6 + 1e-9, "unit": "m",
        "semantics": "SOLUTIONINFO_PARAMETER_VALUE_NOT_BASIS_ORDINAL",
    }


def test_aggregator_keeps_configuration_containment_unauthenticated_and_lineage_pending():
    request, roles, model, selection = _lineage_inputs()
    provenance = read_native_mode_provenance(model, request, roles, selection)
    native_envelope = {
        "native_mode_provenance": provenance,
        "surface_readbacks": {"output": {"entity_ids": [17, 18]}},
        "source_readbacks": {
            f"mode_{index}": {
                "mode_axis_parameter": "lambda",
                "solution_axes": {"parameters_by_pair": {
                    "lambda": (1.55e-6 + index * 1e-9, "m")}},
            }
            for index in range(2)
        },
    }

    status = _verify_native_mode_provenance(request, native_envelope)

    assert status == "PRESENT_IN_ENVELOPE_UNVERIFIED"
    assert provenance["numeric_port"]["port_mode_number"] == 1
    assert provenance["mode_readbacks"]["mode_1"]["basis_mode_ordinal"] == 2
    assert provenance["field_variable_to_eigensolution_mapping_status"] == "UNVERIFIED_NATIVE_FIELD_SAMPLE_REQUIRED"

    forged_label = copy.deepcopy(native_envelope)
    forged_label["native_mode_provenance"]["mode_readbacks"]["mode_1"]["basis_mode_ordinal"] = 1
    with pytest.raises(AggregationError, match="relabels its basis ordinal"):
        _verify_native_mode_provenance(request, forged_label)

    forged_lineage = copy.deepcopy(native_envelope)
    forged_lineage["native_mode_provenance"]["configuration_and_solution_lineage_status"] = "VERIFIED"
    forged_lineage["native_mode_provenance"]["status"] = (
        "NATIVE_PORT_BMA_SOLUTIONINFO_LINEAGE_READBACK_FIELD_MAPPING_UNVERIFIED")
    with pytest.raises(AggregationError, match="without a selected-solution producer-step readback"):
        _verify_native_mode_provenance(request, forged_lineage)


@pytest.mark.parametrize("overrides", [
    {"bma_port": "1"},
    {"step_type": "Frequency"},
    {"neigs": 1},
    {"mode_freq": "f0/2"},
    {"port_name": "3"},
    {"entity_ids": (17, 19)},
    {"port_type": "UserDefined"},
])
def test_mode_provenance_rejects_native_metadata_contradictions(overrides):
    request, roles, _model, selection = _lineage_inputs()
    model = _NativeModel(**overrides)

    with pytest.raises(ExecutionContractError) as caught:
        read_native_mode_provenance(model, request, roles, selection)
    assert caught.value.code == "NATIVE_MODE_PROVENANCE_MISMATCH"


@pytest.mark.parametrize("port_mode_number", [0, -1])
def test_mode_provenance_rejects_nonpositive_native_port_mode_number(port_mode_number):
    request, roles, _model, selection = _lineage_inputs()
    model = _NativeModel(port_mode_number=port_mode_number)

    with pytest.raises(ExecutionContractError, match="PortModeNumber is not a positive integer"):
        read_native_mode_provenance(model, request, roles, selection)


def test_mode_provenance_does_not_promote_combined_sequence_containment_to_producer_link():
    request, roles, model, selection = _lineage_inputs()
    model._sequence._features = _FeatureList({
        "stInputBma": _SolverStep("stInputBma", "std3d", "bmaInput3d"),
        "stOutputBma": _SolverStep("stOutputBma", "std3d", "bmaOutput3d"),
        "stFrequency": _SolverStep("stFrequency", "std3d", "freq"),
    })
    model._study._features = _FeatureList({
        "bmaInput3d": _PropertyFeature("bmaInput3d", "BoundaryModeAnalysis",
                                       {"PortName": "1", "modeFreq": "f0", "neigs": 1}),
        "bmaOutput3d": model._study._bma,
        "freq": _PropertyFeature("freq", "Frequency", {"plist": "f0"}),
    })

    result = read_native_mode_provenance(model, request, roles, selection)

    assert result["configuration_and_solution_lineage_status"] == "UNVERIFIED"
    assert result["configuration_containment_status"] == "SOLUTIONINFO_SEQUENCE_CONTAINS_OUTPUT_PORT_BMA_CONFIGURATION"
    for index in range(2):
        bma = result["mode_readbacks"][f"mode_{index}"]["solver_to_bma_step"]
        assert bma["status"] == "UNVERIFIED_SELECTED_SOLUTION_PRODUCER_STEP"
        assert bma["step_tag"] == "bmaOutput3d"
        assert {row["studystep"] for row in bma["study_step_bindings"]} == {
            "bmaInput3d", "bmaOutput3d", "freq"}


def test_mode_provenance_retains_explicit_unverified_when_solutioninfo_sequence_link_is_unavailable():
    request, roles, _model, selection = _lineage_inputs()

    class MissingSequenceLink(_NativeModel):
        def __init__(self):
            super().__init__()
            self._solutions["sModeA"] = _NoSequenceSolution()
            self._solutions["sModeB"] = _NoSequenceSolution()

    class _NoSequenceInfo:
        def getSolverSequence(self, _outer):
            return None

        def getSol(self, _outer):
            return None

        def getSolnum(self, _outer, _strict):
            return [1, 2]

    class _NoSequenceSolution:
        def getSolutioninfo(self):
            return _NoSequenceInfo()

    result = read_native_mode_provenance(MissingSequenceLink(), request, roles, selection)

    assert result["configuration_and_solution_lineage_status"] == "UNVERIFIED"
    assert result["mode_readbacks"]["mode_0"]["solution_to_solver_sequence"]["status"] == "UNVERIFIED"
    assert any(row["code"] == "SOLVER_SEQUENCE_UNAVAILABLE" for row in result["unverified_reasons"])


def test_mode_provenance_does_not_claim_two_modes_from_duplicate_solutioninfo_indices():
    request, roles, model, selection = _lineage_inputs()
    roles["mode_1"]["source"]["inner_index"] = 1
    roles["mode_1"]["source"]["solnum"] = 1
    roles["mode_1"]["native_binding"]["solution"] = roles["mode_1"]["source"]["solution_id"]
    roles["mode_1"]["solution_axes"]["inner_index"] = 1
    roles["mode_1"]["solution_axes"]["solnum"] = 1

    with pytest.raises(ExecutionContractError, match="distinct SolutionInfo"):
        read_native_mode_provenance(model, request, roles, selection)


def test_wrapped_worker_timeout_is_reraised_and_stops_all_followup_native_reads():
    request, roles, _model, selection = _lineage_inputs()
    timeout = JavaWorkerTimeout("RPC timeout; worker request may still be executing")
    wrapped = ExecutionContractError("ENGINE_CALL_FAILED", "wrapped native call failure")
    wrapped.__cause__ = timeout

    class TimedOutModel(_NativeModel):
        def __init__(self):
            super().__init__()
            self.rpc_calls = []

        def component(self, tag):
            self.rpc_calls.append(("component", tag))
            raise wrapped

    model = TimedOutModel()
    with pytest.raises(ExecutionContractError) as caught:
        read_native_mode_provenance(model, request, roles, selection)

    assert caught.value is wrapped
    assert model.rpc_calls == [("component", "comp3d")]


def test_attempt_propagates_direct_and_wrapped_structured_unknown_states():
    inner_unknown = ExecutionContractError("EXECUTION_STATE_UNKNOWN", "inner outcome is unknown")
    wrapped_unknown = ExecutionContractError("ENGINE_CALL_FAILED", "outer RPC wrapper")
    wrapped_unknown.__cause__ = inner_unknown
    attribute_unknown = ExecutionContractError("ENGINE_CALL_FAILED", "explicit unknown flag")
    attribute_unknown.execution_state_unknown = True
    wrapped_attribute_unknown = ExecutionContractError("ENGINE_CALL_FAILED", "outer RPC wrapper")
    wrapped_attribute_unknown.__cause__ = attribute_unknown
    reply_unknown = JavaWorkerError("worker returned explicit unknown state",
                                    reply={"execution_state_unknown": True})
    # Keep the wire field as the only signal so this exercises reply decoding,
    # independently of JavaWorkerError's convenience attribute.
    reply_unknown.execution_state_unknown = False
    wrapped_reply_unknown = ExecutionContractError("ENGINE_CALL_FAILED", "worker response wrapper")
    wrapped_reply_unknown.__cause__ = reply_unknown
    failure_unknown = JavaWorkerError("worker returned explicit unknown failure",
                                      reply={"failure": {"execution_state_unknown": True}})
    failure_unknown.execution_state_unknown = False

    def raise_error(error):
        raise error

    for error in (inner_unknown, wrapped_unknown, attribute_unknown,
                  wrapped_attribute_unknown, reply_unknown, wrapped_reply_unknown,
                  failure_unknown):
        errors = []
        with pytest.raises(type(error)) as caught:
            _attempt(errors, "injected unknown", lambda error=error: raise_error(error))
        assert caught.value is error
        assert errors == []

    errors = []
    with pytest.raises(ExecutionContractError) as caught:
        _attempt(errors, "worker status", lambda: {"status": "UNKNOWN"})
    assert caught.value.code == "EXECUTION_STATE_UNKNOWN"
    assert errors == []
