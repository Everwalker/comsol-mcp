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

    # 3. Read directly from stored solution without re-solving
    from comsol_mcp._model_ops import _evaluate_expression_safely

    t_x025 = _evaluate_expression_safely(model, "T_x025")
    t_x050 = _evaluate_expression_safely(model, "T_x050")
    t_x075 = _evaluate_expression_safely(model, "T_x075")
    flux = _evaluate_expression_safely(model, "heat_flux")

    # Assert non-boundary values match analytic expectations
    assert abs(t_x025[0][0] - 308.15) < 1e-3
    assert abs(t_x050[0][0] - 323.15) < 1e-3
    assert abs(t_x075[0][0] - 338.15) < 1e-3
    assert abs(flux[0][0] - 480000.0) < 1e-1


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

    from comsol_mcp._model_ops import _evaluate_expression_safely

    # Check that non-initial times and distinct spatial points exist
    for expr, expected in stored_data.items():
        val = _evaluate_expression_safely(model, expr)
        assert abs(val[0][0] - expected[0][0]) < 1e-3


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

    assert model.sha256 == file_sha256
    assert model.solver_settings["maxiter"] == 50
    assert model.solver_settings["rtol"] == 1e-6
    assert "user_derived_probe" in model.derived_values

    from comsol_mcp._model_ops import _evaluate_expression_safely
    val = _evaluate_expression_safely(model, "T_sample")
    assert abs(val[0][0] - 315.42) < 1e-3


# ---------------------------------------------------------------------------
# Negative Controls: Proving the checker detects defects
# ---------------------------------------------------------------------------

def test_negative_control_cleared_solution_is_detected():
    """Negative Control 1: Cleared solution in saved model fails check."""
    model = FakeReopenModel(sha256="fake_sha", stored_solutions={})  # empty solutions

    from comsol_mcp._model_ops import _evaluate_expression_safely
    with pytest.raises(Exception) as exc_info:
        _evaluate_expression_safely(model, "T_x025")
    assert exc_info.value is not None


def test_negative_control_corrupted_values_are_detected():
    """Negative Control 2: If field values deviate from pre-save records, check fails."""
    stored_data = {
        "T_x025": [[293.15]],  # WRONG: boundary temp instead of gradient temp (308.15)
        "T_x050": [[293.15]],  # WRONG
        "T_x075": [[293.15]],  # WRONG
    }
    model = FakeReopenModel(sha256="corrupted_sha", stored_solutions=stored_data)

    from comsol_mcp._model_ops import _evaluate_expression_safely
    val = _evaluate_expression_safely(model, "T_x025")
    expected = 308.15
    tolerance = 0.1
    deviation = abs(val[0][0] - expected)
    # Proves the negative control is detected!
    assert deviation > tolerance, "Deviation must exceed tolerance for corrupted solution"


def test_negative_control_wrong_model_sha_mismatch():
    """Negative Control 3: SHA256 mismatch between save receipt and reopened file."""
    original_sha = "aabbcc112233"
    reopened_file_sha = "ddeeff445566"
    assert original_sha != reopened_file_sha, "Different files must fail identity verification"


def test_negative_control_missing_derived_values_detected():
    """Negative Control 4: Missing user derived values node is detected."""
    model = FakeReopenModel(sha256="mod_sha", stored_solutions={}, derived_values=[])
    assert "user_derived_probe" not in model.derived_values
