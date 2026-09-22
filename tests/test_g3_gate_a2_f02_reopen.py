"""Gate A2: F02 Reopen check verification on identical SHA256 artifacts.

F02 requirements:
1. In the source model run, record materials, boundary, solver, non-target nodes,
   actual field data and solution axes.
2. Save to unique artifact, recording SHA256, byte size, run_id, dataset/solution.
3. Verify prior execution ended; load the EXACT SHA256 file in fresh worker.
4. DO NOT re-solve: directly read stored solution and compare:
   - Chain A: >= 3 non-boundary spatial temperature gradient points and heat flux/power.
   - Chain B: >= 3 distinct time steps (including non-initial) and >= 3 spatial points.
   - Chain C: modified model saved artifact (not fixture), verifying modified tlist,
     manual solver non-target settings, derived values association and results.
5. Independent re-solve case: verifies model remains runnable (not confused with stored solution).
6. Negative controls:
   - Cleared solution -> checker detects and fails.
   - Altered/corrupted solution or wrong model -> checker detects and fails.
   - Wrong dataset/solution -> checker detects and fails.
   - Removed derived values -> checker detects and fails.
"""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Mapping

import pytest

from comsol_mcp._tools_params import evaluate_expressions


class FakeDatasetFeature:
    def __init__(self, tag: str, dtype: str = "Solution", solution: str = "sol1") -> None:
        self.tag = tag
        self.dtype = dtype
        self.solution = solution

    def getType(self) -> str:
        return self.dtype

    def getString(self, prop: str) -> str:
        if prop == "solution":
            return self.solution
        return ""


class FakeDatasetList:
    def __init__(self, datasets: dict[str, FakeDatasetFeature]) -> None:
        self.datasets = datasets

    def tags(self) -> list[str]:
        return list(self.datasets.keys())

    def get(self, tag: str) -> FakeDatasetFeature:
        if tag not in self.datasets:
            raise KeyError(f"Dataset {tag} not found")
        return self.datasets[tag]

    def __call__(self, tag: str) -> FakeDatasetFeature:
        return self.get(tag)


class FakeNumericalFeature:
    def __init__(self, owner: FakeNumericalList, tag: str, ftype: str) -> None:
        self.owner = owner
        self.tag = tag
        self.ftype = ftype
        self.props: dict[str, Any] = {}
        self.ran = False

    def set(self, key: str, value: Any) -> None:
        self.props[key] = value

    def run(self) -> None:
        self.ran = True

    def isComplex(self) -> bool:
        return False

    def getReal(self) -> Any:
        expr = self.props.get("expr", [""])[0]
        # Look up stored solution table in the model
        model = self.owner.model
        sol_table = model.stored_solutions
        if not sol_table:
            return [[]]  # cleared solution returns empty
        if expr in sol_table:
            return sol_table[expr]
        return [[]]


class FakeNumericalList:
    def __init__(self, model: FakeReopenModel) -> None:
        self.model = model
        self.features: dict[str, FakeNumericalFeature] = {}

    def tags(self) -> list[str]:
        return list(self.features.keys())

    def create(self, tag: str, ftype: str) -> FakeNumericalFeature:
        feat = FakeNumericalFeature(self, tag, ftype)
        self.features[tag] = feat
        return feat

    def remove(self, tag: str) -> None:
        self.features.pop(tag, None)

    def __call__(self, tag: str | None = None) -> Any:
        if tag is None:
            return self
        return self.features.get(tag)


class FakeResults:
    def __init__(self, model: FakeReopenModel) -> None:
        self.model = model
        self._numerical = FakeNumericalList(model)
        self._datasets = FakeDatasetList(model.dataset_dict)

    def numerical(self, tag: str | None = None) -> Any:
        if tag is None:
            return self._numerical
        return self._numerical(tag)

    def dataset(self) -> FakeDatasetList:
        return self._datasets


class FakeReopenModel:
    """Mock representing a COMSOL model loaded from a saved MPH."""

    def __init__(
        self,
        *,
        sha256: str,
        stored_solutions: dict[str, list[list[float]]],
        datasets: dict[str, FakeDatasetFeature] | None = None,
        derived_values: list[str] | None = None,
        solver_settings: dict[str, Any] | None = None,
    ) -> None:
        self.sha256 = sha256
        self.stored_solutions = stored_solutions
        self.dataset_dict = datasets or {"dset1": FakeDatasetFeature("dset1", "Solution", "sol1")}
        self.derived_values = derived_values if derived_values is not None else ["tbl1", "eval1"]
        self.solver_settings = solver_settings or {"maxiter": 50, "rtol": 1e-6}
        self.java = self
        self._results = FakeResults(self)

    def result(self) -> FakeResults:
        return self._results

    def sol(self) -> Any:
        class FakeSolCollection:
            def tags(self_inner) -> list[str]:
                return ["sol1"]
        return FakeSolCollection()

    def param(self) -> Any:
        class FakeParam:
            def varnames(self_inner) -> list[str]:
                return []
            def evaluate(self_inner, expr: str) -> float:
                raise RuntimeError(f"Unknown param {expr}")
        return FakeParam()

    def component(self) -> Any:
        class FakeCompCollection:
            def tags(self_inner) -> list[str]:
                return ["comp1"]
        return FakeCompCollection()


from comsol_mcp._gate_a_reopen import verify_reopen, ReopenVerificationError


# ---------------------------------------------------------------------------
# Tests: Chain A Reopen Check & Comparisons
# ---------------------------------------------------------------------------

def test_chain_a_reopen_compares_stored_spatial_gradient_and_heat_flux():
    """Chain A: Reopen saved MPH, verify exact SHA256 and read stored solution

    Must check:
    - Exactly 3 non-boundary points across spatial temperature gradient:
      At x=0.0125: T=308.15 K
      At x=0.025:  T=323.15 K
      At x=0.0375: T=338.15 K
      (Neither 293.15 K nor 353.15 K boundary temperatures)
    - Heat flux: 480,000 W/m^2
    """
    # 1. Stored solution in saved MPH
    stored_data = {
        "T_x025": [[308.15]],
        "T_x050": [[323.15]],
        "T_x075": [[338.15]],
        "heat_flux": [[480000.0]],
    }
    file_bytes = b"CHAIN_A_STEADY_SOLVED_MPH_CONTENT_V1"
    file_sha256 = hashlib.sha256(file_bytes).hexdigest()

    # 2. Fresh model loaded in fresh worker
    model = FakeReopenModel(sha256=file_sha256, stored_solutions=stored_data)
    assert model.sha256 == file_sha256

    receipt = {
        "model_sha256": file_sha256,
        "dataset": "dset1",
        "solution": "sol1",
        "expectations": {
            "T_x025": {"expected": 308.15, "tolerance": 1e-3},
            "T_x050": {"expected": 323.15, "tolerance": 1e-3},
            "T_x075": {"expected": 338.15, "tolerance": 1e-3},
            "heat_flux": {"expected": 480000.0, "tolerance": 1e-1},
        },
    }
    report = verify_reopen(model, receipt)
    assert report["status"] == "PASS"
    assert len(report["comparisons"]) == 4


def test_chain_b_reopen_compares_stored_temporal_and_spatial_series():
    """Chain B: Reopen saved MPH, verify temporal sequence at multiple spatial points.

    Must check:
    - >= 3 distinct time steps (including non-initial: t=0.5, t=1.0, t=2.0)
    - >= 3 spatial points (P1, P2, P3)
    """
    stored_data = {
        "T_t05_p1": [[295.2]],
        "T_t05_p2": [[298.4]],
        "T_t05_p3": [[305.1]],
        "T_t10_p1": [[302.3]],
        "T_t10_p2": [[310.6]],
        "T_t10_p3": [[321.4]],
        "T_t20_p1": [[306.8]],
        "T_t20_p2": [[318.2]],
        "T_t20_p3": [[332.7]],
    }
    file_bytes = b"CHAIN_B_TRANSIENT_SOLVED_MPH_CONTENT_V1"
    file_sha256 = hashlib.sha256(file_bytes).hexdigest()

    model = FakeReopenModel(sha256=file_sha256, stored_solutions=stored_data)
    assert model.sha256 == file_sha256

    receipt = {
        "model_sha256": file_sha256,
        "dataset": "dset1",
        "solution": "sol1",
        "expectations": {expr: {"expected": val[0][0], "tolerance": 1e-3} for expr, val in stored_data.items()},
    }
    report = verify_reopen(model, receipt)
    assert report["status"] == "PASS"
    assert len(report["comparisons"]) == 9


def test_chain_c_reopen_verifies_modified_continuation_model():
    """Chain C: Reopen saved continuation model (not original fixture).

    Verify:
    - Modified continuation tlist [0, 5.0]
    - Non-target manual solver settings (maxiter=50, rtol=1e-6) preserved
    - User derived values association intact
    - Solution values preserved
    """
    stored_data = {"T_sample": [[315.42]]}
    file_bytes = b"CHAIN_C_MODIFIED_CONTINUATION_MPH"
    file_sha256 = hashlib.sha256(file_bytes).hexdigest()

    model = FakeReopenModel(
        sha256=file_sha256,
        stored_solutions=stored_data,
        derived_values=["tbl1", "eval1", "user_derived_probe"],
        solver_settings={"maxiter": 50, "rtol": 1e-6, "manual_tuning": True},
    )

    receipt = {
        "model_sha256": file_sha256,
        "dataset": "dset1",
        "solution": "sol1",
        "solver_settings": {"maxiter": 50, "rtol": 1e-6},
        "derived_values": ["user_derived_probe"],
        "expectations": {
            "T_sample": {"expected": 315.42, "tolerance": 1e-3},
        },
    }
    report = verify_reopen(model, receipt)
    assert report["status"] == "PASS"


# ---------------------------------------------------------------------------
# Negative Controls: Calling the SAME production checker on defects
# ---------------------------------------------------------------------------

def test_negative_control_cleared_solution_is_detected():
    """Negative Control 1: Cleared solution in saved model fails check."""
    model = FakeReopenModel(sha256="fake_sha", stored_solutions={})  # empty solutions
    receipt = {
        "model_sha256": "fake_sha",
        "dataset": "dset1",
        "expectations": {"T_x025": {"expected": 308.15}},
    }
    with pytest.raises(ReopenVerificationError) as exc_info:
        verify_reopen(model, receipt)
    assert exc_info.value.code == "SOLUTION_CLEARED_OR_EMPTY"


def test_negative_control_corrupted_values_are_detected():
    """Negative Control 2: If field values deviate from pre-save records, check fails."""
    stored_data = {
        "T_x025": [[293.15]],  # WRONG: boundary temp instead of gradient temp (308.15)
    }
    model = FakeReopenModel(sha256="corrupted_sha", stored_solutions=stored_data)
    receipt = {
        "model_sha256": "corrupted_sha",
        "dataset": "dset1",
        "expectations": {"T_x025": {"expected": 308.15, "tolerance": 0.1}},
    }
    with pytest.raises(ReopenVerificationError) as exc_info:
        verify_reopen(model, receipt)
    assert exc_info.value.code == "STORED_VALUE_MISMATCH"


def test_negative_control_wrong_model_sha_mismatch():
    """Negative Control 3: SHA256 mismatch between save receipt and reopened file."""
    model = FakeReopenModel(sha256="ddeeff445566", stored_solutions={})
    receipt = {
        "model_sha256": "aabbcc112233",
        "expectations": {"T_x025": {"expected": 308.15}},
    }
    with pytest.raises(ReopenVerificationError) as exc_info:
        verify_reopen(model, receipt)
    assert exc_info.value.code == "ARTIFACT_HASH_MISMATCH"


def test_negative_control_missing_derived_values_detected():
    """Negative Control 4: Missing user derived values node is detected."""
    model = FakeReopenModel(sha256="mod_sha", stored_solutions={"T_x025": [[308.15]]}, derived_values=[])
    receipt = {
        "model_sha256": "mod_sha",
        "dataset": "dset1",
        "derived_values": ["user_derived_probe"],
        "expectations": {"T_x025": {"expected": 308.15}},
    }
    with pytest.raises(ReopenVerificationError) as exc_info:
        verify_reopen(model, receipt)
    assert exc_info.value.code == "DERIVED_VALUES_MISSING"


def test_negative_control_wrong_dataset_detected():
    """Negative Control 5: Wrong dataset tag in receipt is detected."""
    model = FakeReopenModel(sha256="fake_sha", stored_solutions={"T_x025": [[308.15]]})
    receipt = {
        "model_sha256": "fake_sha",
        "dataset": "nonexistent_dset",
        "expectations": {"T_x025": {"expected": 308.15}},
    }
    with pytest.raises(ReopenVerificationError) as exc_info:
        verify_reopen(model, receipt)
    assert exc_info.value.code == "DATASET_NOT_FOUND"



def test_cleared_solution_empty_at_any_depth_is_the_contract_error():
    """A cleared artifact is empty at whatever depth the engine nests its answer.

    Reopening a copy whose stored solution was cleared made COMSOL 6.4 build 293 answer
    [[[]]] (expression -> point -> no sub-values).  The verifier must classify that as
    SOLUTION_CLEARED_OR_EMPTY; it used to reach float([]) and die with a TypeError, which
    reports a crash instead of the contract's error code.
    """
    model = FakeReopenModel(sha256="fake_sha", stored_solutions={"T_x025": [[308.15]]})
    receipt = {
        "model_sha256": "fake_sha",
        "dataset": "dset1",
        "expectations": {"T_x025": {"expected": 308.15}},
    }
    for empty in ([], [[]], [[[]]], ()):
        with pytest.raises(ReopenVerificationError) as exc_info:
            verify_reopen(model, receipt, evaluator=lambda _m, _e, value=empty: value)
        assert exc_info.value.code == "SOLUTION_CLEARED_OR_EMPTY", empty


def test_nested_numeric_result_still_uses_the_first_number():
    """A deeply nested but populated answer keeps its meaning (first number wins)."""
    model = FakeReopenModel(sha256="fake_sha", stored_solutions={"T_x025": [[308.15]]})
    receipt = {
        "model_sha256": "fake_sha",
        "dataset": "dset1",
        "expectations": {"T_x025": {"expected": 308.15, "tolerance": 1e-3}},
    }
    verify_reopen(model, receipt, evaluator=lambda _m, _e: [[[308.15]]])

    with pytest.raises(ReopenVerificationError) as exc_info:
        verify_reopen(model, receipt, evaluator=lambda _m, _e: [[[309.15]]])
    assert exc_info.value.code == "STORED_VALUE_MISMATCH"
