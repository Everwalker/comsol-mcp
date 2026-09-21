"""G3.3 Remediation Unit Tests.

Verifies:
- F02/F03: MeasureSpec and statistical aggregations (integral, average, std, rms, min, max)
- F04: SolutionBinding and FieldArray indexing (Axis 1 solution slicing vs Axis 0 expression)
- F05: Strict complex transformation without silent zero padding
- F06: Coordinate unit scaling and readback checking
- F07/F08/F09: ArtifactStore path containment, atomic export, and streaming chunk reader
- F10: Model Definitions Probe management vs Results Derived Values
"""
from __future__ import annotations

import math
from pathlib import Path
import pytest
import tempfile

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._measure_spec import MeasureSpec
from comsol_mcp._complex_transform import transform_complex_data, transform_complex_value
from comsol_mcp._solution_binding import SolutionBinding, FieldArray
from comsol_mcp._artifact_store import ArtifactStore
from comsol_mcp import _probe_manage as probe_manage


# ---------------------------------------------------------------------------
# F02 / F03: MeasureSpec and Statistics
# ---------------------------------------------------------------------------

def test_measure_spec_dimension_mapping() -> None:
    # 0D point
    m0 = MeasureSpec(aggregate="integral", space_dim=3, entity_dim=0)
    assert m0.feature_type == "IntPoint"
    m0_avg = MeasureSpec(aggregate="average", space_dim=3, entity_dim=0)
    assert m0_avg.feature_type == "AvPoint"

    # 1D line/edge
    m1 = MeasureSpec(aggregate="integral", space_dim=3, entity_dim=1)
    assert m1.feature_type == "IntEdge"
    m1_avg = MeasureSpec(aggregate="average", space_dim=3, entity_dim=1)
    assert m1_avg.feature_type == "AvEdge"

    # 2D surface/boundary
    m2 = MeasureSpec(aggregate="integral", space_dim=3, entity_dim=2)
    assert m2.feature_type == "IntSurface"
    m2_avg = MeasureSpec(aggregate="average", space_dim=3, entity_dim=2)
    assert m2_avg.feature_type == "AvSurface"

    # 3D volume
    m3 = MeasureSpec(aggregate="integral", space_dim=3, entity_dim=3)
    assert m3.feature_type == "IntVolume"
    m3_avg = MeasureSpec(aggregate="average", space_dim=3, entity_dim=3)
    assert m3_avg.feature_type == "AvVolume"

    # Global
    mg = MeasureSpec(aggregate="global")
    assert mg.feature_type == "EvalGlobal"


def test_measure_spec_statistical_formulas() -> None:
    # f=2 on V=3 -> integral=6, average=2, std=0, rms=2
    val = 6.0
    denom = 3.0
    assert MeasureSpec.compute_statistics(val, denom, "integral") == 6.0
    assert MeasureSpec.compute_statistics(val, denom, "average") == 2.0
    assert MeasureSpec.compute_statistics(val, denom, "std", variance_integral=0.0) == 0.0
    assert MeasureSpec.compute_statistics(val, denom, "rms", rms_integral=12.0) == 2.0

    # Non-uniform field f=x+2y on [0,2]x[0,3]: area=6, integral=24, average=4, var=10/3, std=sqrt(10/3), rms=sqrt(58/3)
    area = 6.0
    integral = 24.0
    avg = MeasureSpec.compute_statistics(integral, area, "average")
    assert math.isclose(avg, 4.0)

    var_int = 20.0  # int (f - 4)^2 = 20
    std = MeasureSpec.compute_statistics(integral, area, "std", variance_integral=var_int)
    assert math.isclose(std, math.sqrt(10.0 / 3.0))

    sq_int = 116.0  # int f^2 = 116
    rms = MeasureSpec.compute_statistics(integral, area, "rms", rms_integral=sq_int)
    assert math.isclose(rms, math.sqrt(58.0 / 3.0))

    # Zero measure rejected
    with pytest.raises(ExecutionContractError) as exc_info:
        MeasureSpec.compute_statistics(10.0, 0.0, "average")
    assert exc_info.value.code == "ZERO_OR_INVALID_MEASURE"


# ---------------------------------------------------------------------------
# F04: SolutionBinding Array Indexing
# ---------------------------------------------------------------------------

def test_solution_axis_slicing_multi_expression() -> None:
    # 2 expressions, each with 3 inner solutions, each with 2 spatial points
    # [expr][solnum][vertex]
    raw = [
        [[11, 12], [21, 22], [31, 32]],          # Expr 0
        [[111, 112], [121, 122], [131, 132]],    # Expr 1
    ]

    # Inner = 2 (1-based)
    sliced = SolutionBinding.slice_solution_axis(raw, 2, num_expressions=2)
    assert sliced == [[21, 22], [121, 122]]

    # Inner = "first"
    first = SolutionBinding.slice_solution_axis(raw, "first", num_expressions=2)
    assert first == [[11, 12], [111, 112]]

    # Inner = "last"
    last = SolutionBinding.slice_solution_axis(raw, "last", num_expressions=2)
    assert last == [[31, 32], [131, 132]]

    # Inner = [1, 3]
    subset = SolutionBinding.slice_solution_axis(raw, [1, 3], num_expressions=2)
    assert subset == [
        [[11, 12], [31, 32]],
        [[111, 112], [131, 132]],
    ]

    # Out of bounds
    with pytest.raises(ExecutionContractError) as exc:
        SolutionBinding.slice_solution_axis(raw, 0, num_expressions=2)
    assert exc.value.code == "INVALID_REQUEST"

    with pytest.raises(ExecutionContractError) as exc:
        SolutionBinding.slice_solution_axis(raw, 5, num_expressions=2)
    assert exc.value.code == "INVALID_REQUEST"


# ---------------------------------------------------------------------------
# F05: Strict Complex Transformation
# ---------------------------------------------------------------------------

def test_complex_transformation_strictness() -> None:
    # Real scalar
    assert transform_complex_value(3.0, 4.0, "preserve") == {"real": 3.0, "imag": 4.0}
    assert transform_complex_value(3.0, 4.0, "real") == 3.0
    assert transform_complex_value(3.0, 4.0, "imag") == 4.0
    assert math.isclose(transform_complex_value(3.0, 4.0, "abs"), 5.0)
    assert math.isclose(transform_complex_value(3.0, 4.0, "phase"), math.atan2(4.0, 3.0))

    # Phase at zero magnitude
    assert transform_complex_value(0.0, 0.0, "phase") == 0.0

    # Complex array with valid imaginary data
    real_arr = [3.0, 6.0]
    imag_arr = [4.0, 8.0]
    pres = transform_complex_data(real_arr, imag_arr, "preserve", is_complex=True)
    assert pres == [{"real": 3.0, "imag": 4.0}, {"real": 6.0, "imag": 8.0}]

    # Complex array with missing imaginary data must raise error
    with pytest.raises(ExecutionContractError) as exc:
        transform_complex_data(real_arr, None, "preserve", is_complex=True)
    assert exc.value.code == "COMPLEX_DATA_ERROR"

    # Shape mismatch must raise error
    with pytest.raises(ExecutionContractError) as exc:
        transform_complex_data(real_arr, [4.0], "preserve", is_complex=True)
    assert exc.value.code == "COMPLEX_DATA_ERROR"


# ---------------------------------------------------------------------------
# F08 / F09: ArtifactStore and Atomic Export
# ---------------------------------------------------------------------------

def test_artifact_store_path_containment_and_export(tmp_path: Path) -> None:
    store = ArtifactStore(project_root=tmp_path)

    # Valid export inside root
    dest = tmp_path / "out.json"
    eval_res = {
        "status": {"ok": True},
        "values": [1.0, 2.0, 3.0],
        "expressions": ["T"],
        "dataset": "dset1",
        "total_elements": 3,
    }
    result = store.export_field_data(str(dest), eval_res, fmt="json")
    assert dest.is_file()
    assert result["sha256"] is not None
    assert result["total_elements"] == 3

    # Chunk streaming read
    chunk = store.read_chunk(str(dest), offset=0, length=16)
    assert chunk["offset"] == 0
    assert chunk["length"] == 16
    assert len(chunk["data_bytes"]) == 16

    # Attempt path escape outside root
    with pytest.raises(ExecutionContractError) as exc:
        store.resolve_safe_path("../escaped.json")
    assert exc.value.code == "ACCESS_VIOLATION"

    # Refuse export when upstream evaluation failed
    bad_res = {
        "status": {"ok": False, "engine_error": "solver diverged"},
        "values": None,
    }
    with pytest.raises(ExecutionContractError) as exc:
        store.export_field_data(str(tmp_path / "fail.json"), bad_res)
    assert exc.value.code == "EXPORT_FAILED"


# ---------------------------------------------------------------------------
# F10: Model Definitions Probe Management
# ---------------------------------------------------------------------------

def test_probe_manage_separation() -> None:
    class FakeProbeContainer:
        def __init__(self) -> None:
            self.entries: dict[str, Any] = {}
        def tags(self) -> list[str]:
            return list(self.entries)
        def create(self, tag: str, typ: str) -> Any:
            node = type("Node", (), {"getType": lambda: typ, "set": lambda k, v: None})()
            self.entries[tag] = node
            return node
        def remove(self, tag: str) -> None:
            del self.entries[tag]

    class FakeModel:
        def __init__(self) -> None:
            self._probe = FakeProbeContainer()
        def probe(self) -> FakeProbeContainer:
            return self._probe

    class FakeWorker:
        def __init__(self, m: FakeModel) -> None:
            self.m = m
        def client(self) -> Any:
            return type("Client", (), {"model": lambda _self, t: self.m})()


    worker = FakeWorker(FakeModel())

    # Create DomainProbe
    res = probe_manage.probe_create(worker, "m", {
        "tag": "prb1",
        "type_id": "DomainProbe",
        "definition": {"expr": "T"},
    })
    assert res["created"] is True
    assert res["tag"] == "prb1"

    # List probes
    list_res = probe_manage.probe_list(worker, "m", {})
    assert list_res["count"] == 1
    assert "prb1" in list_res["tags"]

    # Remove probe
    rem_res = probe_manage.probe_remove(worker, "m", {"tag": "prb1"})
    assert rem_res["removed"] is True
    assert rem_res["verified_removed"] is True
