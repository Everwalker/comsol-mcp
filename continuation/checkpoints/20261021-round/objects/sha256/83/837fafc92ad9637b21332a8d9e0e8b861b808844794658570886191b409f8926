"""Analytical W17 oracles exercised through the public MCP execution path."""
from __future__ import annotations

import json
import math
from pathlib import Path

ORACLES = {
    "C04": {"integral": [6., 9.], "average": [2., 3.], "std": [0., 0.], "rms": [2., 3.]},
    "C06": {"integral": [24.], "average": [4.], "std": [math.sqrt(10 / 3)], "rms": [math.sqrt(58 / 3)]},
    "C06_centered_variance": {"expression": "1e6[m]+x", "std": 1 / math.sqrt(3), "tolerance": 1e-8, "reason": "Centered variance avoids large-offset cancellation; tolerance exceeds the binary64 ulp of the shifted field."},
    "C07": {"volume": 12 * math.pi, "mean_radius": 4 / 3, "side_area": 12 * math.pi},
    "tolerance": 1e-9,
    "reason": "Analytical polynomial/constant integrands, exact native quadrature to binary64 roundoff; tolerances fixed before execution.",
}


def leaves(value):
    if isinstance(value, list):
        return [item for child in value for item in leaves(child)]
    return [value]


def close(actual, expected, tol=1e-9):
    rows = leaves(actual)
    assert len(rows) == len(expected), (rows, expected)
    for observed, target in zip(rows, expected):
        assert isinstance(observed, (float, int)) and not isinstance(observed, bool)
        assert math.isfinite(observed) and abs(observed - target) <= tol, (observed, target, tol)


async def run_numeric_cases(client, run_dir: Path, *, build, execute_java, m1_only=False):
    """Callbacks build a new MCP-owned fixture; no private Worker operations here."""
    from phase4_run_mcp import _success
    run_dir.mkdir(exist_ok=True)
    (run_dir / "oracles.json").write_text(json.dumps(ORACLES, indent=2) + "\n")
    class CheckpointRecords(dict):
        def __setitem__(self, key, value):
            super().__setitem__(key, value)
            (run_dir / "assertions.json").write_text(json.dumps(self, indent=2) + "\n")
    records = CheckpointRecords()

    async def action(operation, body, label, *, success=True, expected_codes=None):
        reply = await client.action(operation, body, reconcile=False)
        (run_dir / (label + ".json")).write_text(json.dumps(reply, indent=2) + "\n")
        if (reply.get("error") or {}).get("code") == "EXECUTION_STATE_UNKNOWN":
            job = reply.get("execution", {}).get("job_id")
            assert job, reply
            from phase4_run_mcp import _execution
            quiescent = False
            for operation in ("job_status", "job_result", "job_reconcile"):
                observed = await client.host.call(operation, {"job_id": job,
                    "execution": _execution(key=label + "-" + operation)}, reconcile=False)
                (run_dir / (label + "-" + operation + ".json")).write_text(json.dumps(observed, indent=2) + "\n")
                data = observed.get("data") or {}
                if data.get("status") in ("SUCCEEDED", "FAILED", "CANCELLED", "REJECTED") or (data.get("metadata") or {}).get("reconciled_quiescent") is True:
                    quiescent = True
            assert quiescent, "Original UNKNOWN job was not proven quiescent; no further mutation allowed"
            refreshed = await client.host.call("model_inspect", {"refresh": True,
                "execution": _execution(key=label + "-model-inspect", request="model_inspect",
                    ref=client.state.get("ref"), revision=client.state.get("revision"))}, reconcile=False)
            (run_dir / (label + "-model-inspect.json")).write_text(json.dumps(refreshed, indent=2) + "\n")
            execution = refreshed.get("execution") or (refreshed.get("data") or {}).get("execution") or {}
            assert _success(refreshed) and execution.get("dirty") is False, refreshed
            assert isinstance(execution.get("revision"), int) and not isinstance(execution.get("revision"), bool), refreshed
            client._adopt_readback(refreshed)

        assert _success(reply) is success, reply
        if not success:
            error = reply.get("error") or {}
            code = (error.get("details") or {}).get("cause_code") or error.get("code")
            assert code and code != "EXECUTION_STATE_UNKNOWN", reply
            if expected_codes is not None:
                assert code in expected_codes, (code, expected_codes, reply)
        return reply.get("data", {})

    async def evaluate(expressions, aggregate, label, **spec):
        return await action("result.evaluate", {"spec": {
            "expressions": expressions, "aggregate": aggregate, "complex_mode": "real",
            "solution": {"dataset": "dset1"}, **spec}}, label)

    await build("c04_code", "C04Builder", "constant-volume")
    for aggregate, expected in ORACLES["C04"].items():
        result = await evaluate(["2", "3"], aggregate, "C04-" + aggregate)
        close(result["values"], expected)
    records["C04"] = {"status": "PASS", "scope": "LIVE_PUBLIC_MCP", "expressions": 2}
    if m1_only:
        (run_dir / "assertions.json").write_text(json.dumps(records, indent=2) + "\n")
        return records

    for dimension, entities, expected, label in ((2, [1, 2, 3, 4, 5, 6], 14., "surface-all"),
                                                (2, [1], 3., "surface-subset"),
                                                (3, [1], 3., "volume")):
        result = await evaluate(["1"], "integral", "C05-3d-" + label,
            selection={"kind": "explicit", "component": "comp1", "geometry": "geom1",
                       "entity_dimension": dimension, "entities": entities})
        close(result["values"], [expected])

    await build("c06_code", "C06Builder", "polynomial-area")
    for aggregate, expected in ORACLES["C06"].items():
        result = await evaluate(["x+2*y"], aggregate, "C06-" + aggregate)
        close(result["values"], expected)
    # The weight is nonconstant and positive over the entire rectangle.
    # w=1+x, f=x: M=12; integral(w*x)=14; mean=7/6.
    weighted = await evaluate(["x"], "average", "C06-weighted", weight_expression="1+x/1[m]")
    close(weighted["values"], [7 / 6])
    shifted_std = await evaluate(["1e6[m]+x"], "std", "C06-centered-large-offset")
    close(shifted_std["values"], [ORACLES["C06_centered_variance"]["std"]], tol=ORACLES["C06_centered_variance"]["tolerance"])
    records["C06"] = {"status": "PASS", "scope": "LIVE_PUBLIC_MCP", "nonconstant_positive_weight": True}

    for entities, target, label in (([1, 2, 3, 4], 10., "boundary-all"), ([1], 3., "boundary-subset")):
        result = await evaluate(["1"], "integral", "C05-2d-" + label,
            selection={"kind": "explicit", "component": "comp1", "geometry": "geom1",
                       "entity_dimension": 1, "entities": entities})
        close(result["values"], [target])

    # Real complex-valued expressions on an actual solved mesh.
    for mode, expected in {"real": 3., "imag": 4., "abs": 5., "phase": math.atan2(4., 3.)}.items():
        result = await evaluate(["3+4*i"], "average", "C09-" + mode, complex_mode=mode)
        close(result["values"], [expected])
    preserved = await action("result.evaluate", {"spec": {"expressions": ["3+4*i"],
        "aggregate": "average", "solution": {"dataset": "dset1"}}}, "C09-default-preserve")
    complex_values = leaves(preserved["values"])
    assert len(complex_values) == 1 and isinstance(complex_values[0], dict), preserved
    close(complex_values[0]["real"], [3.])
    close(complex_values[0]["imag"], [4.])
    varying = await action("result.evaluate", {"spec": {"expressions": ["x+2*i*y"],
        "aggregate": "average", "solution": {"dataset": "dset1"}}}, "C09-varying-preserve")
    values = leaves(varying["values"])
    assert len(values) == 1 and isinstance(values[0], dict), varying
    close(values[0]["real"], [1.])
    close(values[0]["imag"], [3.])
    for mode, target in (("real", 1.), ("imag", 3.)):
        result = await evaluate(["x+2*i*y"], "average", "C09-varying-" + mode, complex_mode=mode)
        close(result["values"], [target])
    # Transformation before statistics: phase of x*(3+4i) is constant
    # almost everywhere, so its spatial standard deviation is zero.
    phase_std = await evaluate(["x*(3+4*i)"], "std", "C09-phase-before-std", complex_mode="phase")
    close(phase_std["values"], [0.])
    for aggregate, target in (("average", 5.), ("std", 5 / math.sqrt(3)), ("rms", 10 / math.sqrt(3))):
        result = await evaluate(["x*(3+4*i)"], aggregate, "C09-abs-before-" + aggregate, complex_mode="abs")
        close(result["values"], [target])
    # Raw Interp transformations must describe the output value type separately
    # from COMSOL's original complex status, and preserve every point.
    raw_x = [.5, 1., 1.5]
    for mode in ("preserve", "real", "imag", "abs", "phase"):
        raw_result = await action("result.at_points", {"spec": {
            "expressions": ["x+2*i*y"], "complex_mode": mode,
            "solution": {"dataset": "dset1"}},
            "points": [{"x": x, "y": 1.} for x in raw_x],
            "coordinate_unit": "m", "frame": "spatial"}, "C09-raw-interp-" + mode)
        raw_values = leaves(raw_result["values"])
        assert raw_result["field_array"]["is_complex"] is (mode == "preserve"), raw_result["field_array"]
        if mode == "preserve":
            assert len(raw_values) == len(raw_x), raw_values
            for cell, x in zip(raw_values, raw_x):
                close(cell["real"], [x]); close(cell["imag"], [2.])
        else:
            expected = ({"real": raw_x, "imag": [2.] * 3,
                         "abs": [math.hypot(x, 2.) for x in raw_x],
                         "phase": [math.atan2(2., x) for x in raw_x]})[mode]
            close(raw_values, expected)
    records["C09"] = {"status": "PASS", "scope": "LIVE_PUBLIC_MCP",
                      "constant_and_spatial_complex_modes": "PASS", "transform_before_statistics": "PASS",
                      "failure_injection_scope": "CONTROL tests separately required"}


    async def at_points(points, unit, label, **extra):
        return await action("result.at_points", {"spec": {"expressions": ["x+2*y"],
            "complex_mode": "real", "solution": {"dataset": "dset1"}},
            "points": points, "coordinate_unit": unit, "frame": "spatial", **extra}, label)
    meters = await at_points([{"x": .25, "y": .5}, {"x": 1., "y": 2.}], "m", "C10-m")
    millimeters = await at_points([{"x": 250., "y": 500.}, {"x": 1000., "y": 2000.}], "mm", "C10-mm")
    close(meters["values"], [1.25, 5.])
    close(millimeters["values"], [1.25, 5.])
    for frame in ["material", "unsupported-frame"]:
        await action("result.at_points", {"spec": {"expressions": ["x"], "solution": {"dataset": "dset1"}},
                     "points": [{"x": .5, "y": .5}], "frame": frame, "coordinate_unit": "m"},
                     "C10-reject-" + frame, success=False, expected_codes={"API_UNSUPPORTED"})
    await action("result.at_points", {"spec": {"expressions": ["T"], "solution": {"dataset": "dset1"}},
        "points": [{"x": .25, "y": .5, "z": .1}], "frame": "spatial", "coordinate_unit": "m"},
        "C10-reject-dimension", success=False, expected_codes={"DIMENSION_MISMATCH"})
    await build("chain_a_mm", "ChainABuilder", "millimeter-geometry")
    for unit, scale in (("m", 1.), ("mm", 1000.)):
        result = await action("result.at_points", {"spec": {"expressions": ["T"],
            "complex_mode": "real", "solution": {"dataset": "dset1"}},
            "points": [{"x": x * scale, "y": .005 * scale} for x in (.0125, .025, .0375)],
            "coordinate_unit": unit, "frame": "spatial"}, "C10-mm-geometry-" + unit)
        close(result["values"], [308.15, 323.15, 338.15], tol=1e-7)
        assert result["coordinate_readback"]["status"] == "VERIFIED", result
    records["C10"] = {"status": "PARTIAL", "scope": "LIVE_PUBLIC_MCP", "unit_conversion": "PASS",
                      "remaining": ["CONTROL nonfinite rejection and swapped-coordinate readback"]}

    await build("c05_1d", "ChainABuilder", "nondefault-component-line")
    for dimension, entities, target, label in ((0, [1, 2], 4., "endpoints"),
                                              (0, [1], 2., "endpoint-subset"), (1, [1], 4., "line")):
        result = await evaluate(["2"], "integral", "C05-1d-" + label,
            selection={"kind": "explicit", "component": "body", "geometry": "lineg",
                       "entity_dimension": dimension, "entities": entities})
        close(result["values"], [target])
    for aggregate, target in (("average", 1.), ("std", 1.), ("rms", math.sqrt(2)),
                              ("minimum", 0.), ("maximum", 2.)):
        result = await evaluate(["x"], aggregate, "C05-endpoint-" + aggregate,
            selection={"kind": "explicit", "component": "body", "geometry": "lineg",
                       "entity_dimension": 0, "entities": [1, 2]})
        close(result["values"], [target])
    records["C05"] = {"status": "PASS", "scope": "LIVE_PUBLIC_MCP",
                      "dimensions": [0, 1, 2, 3], "nondefault_component": "body", "nondefault_geometry": "lineg"}

    await build("c07_code", "C07Builder", "axisymmetric")
    volume = await evaluate(["1"], "integral", "C07-volume")
    mean_radius = await evaluate(["r"], "average", "C07-mean-radius")
    close(volume["values"], [12 * math.pi])
    close(mean_radius["values"], [4 / 3])
    # The outer radial boundary is boundary 4 of this versioned rectangle.
    side = await evaluate(["1"], "integral", "C07-side", selection={"kind": "explicit", "component": "comp1", "geometry": "geom1", "entity_dimension": 1, "entities": [4]})
    close(side["values"], [12 * math.pi])
    for axisym_result in (volume, mean_radius, side):
        proof = axisym_result.get("axisymmetric_measure_evidence")
        assert axisym_result.get("axisymmetric_applied_count") == 1, axisym_result
        assert isinstance(proof, list) and proof, axisym_result
        for row in proof:
            assert row.get("native_property") in ("intvolume", "intsurface"), row
            assert row.get("readback_verified") is True and row.get("manual_radial_weighting") is False, row
            assert str(row.get("readback_value")).lower() in ("on", "true", "1"), row
    records["C07"] = {"status": "PASS", "scope": "LIVE_PUBLIC_MCP", "independent_cylinder_oracle": ORACLES["C07"]}
    await build("chain_b_code", "ChainBBuilder", "parameter-time-axes")
    sweep_source = """
import com.comsol.model.*; import java.util.*;
public final class OuterSweep {
 public static Object run(Model model, Map<String,Object> args) {
  model.param().set("p1", "1"); model.param().set("p2", "3");
  model.physics("ht").feature("temp2").set("T0", "293.15[K]+p1*10[K]+p2*1[K]");
  model.study("std1").create("param", "Parametric");
  model.study("std1").feature("param").set("pname", new String[]{"p1","p2"});
  model.study("std1").feature("param").set("plistarr", new String[]{"1 2","3 4"});
  model.study("std1").feature("param").set("punit", new String[]{"",""});
  model.study("std1").feature("param").set("sweeptype", "filled");
  model.study("std1").run(); return Collections.singletonMap("status", "SOLVED");
 }
}
"""
    await execute_java("OuterSweep", sweep_source)
    axes = await action("dataset.solution_indices", {"path": {"segments": [
        {"accessor": "result"}, {"collection": "dataset", "tag": "dset2"}]}}, "C08-indices")
    assert axes["binding_complete"] is True, axes
    assert axes["outer_indices"] == [1, 2, 3, 4] and axes["inner_indices"] == [1, 2, 3, 4, 5], axes
    expression = "100*p1+10*p2+t/1[s]+x/1[m]"
    async def sample(solution, label, success=True):
        return await action("result.at_points", {"spec": {"expressions": [expression, "-2*(" + expression + ")"],
            "complex_mode": "real", "solution": {"dataset": "dset2", **solution}},
            "points": [{"x": x, "y": .005} for x in [.0125, .025, .0375]],
            "frame": "spatial", "coordinate_unit": "m"}, label, success=success)
    all_values = await sample({"outer": "all", "inner": "all"}, "C08-all")
    field = all_values["field_array"]
    assert field["axes"] == ["expression", "outer", "inner", "point"] and field["shape"] == [2, 4, 5, 3], field
    parameter_pairs = set()
    for oi, outer in enumerate(axes["outer_indices"]):
        for ii, inner in enumerate(axes["inner_indices"]):
            metadata = axes["parameters"]["by_pair"][f"{outer}:{inner}"]
            assert len(metadata["names"]) == len(metadata["values"]) == len(metadata["units"]) == 3, metadata
            params = dict(zip(metadata["names"], metadata["values"]))
            assert params["t"] == .5 * ii
            parameter_pairs.add((params["p1"], params["p2"]))
            expected = [100 * params["p1"] + 10 * params["p2"] + params["t"] + x for x in [.0125, .025, .0375]]
            close(field["values"][0][oi][ii], expected)
            close(field["values"][1][oi][ii], [-2 * value for value in expected])
    assert parameter_pairs == {(1., 3.), (1., 4.), (2., 3.), (2., 4.)}
    for aggregate in ("average", "std"):
        aggregate_result = await action("result.evaluate", {"spec": {
            "expressions": [expression, "-2*(" + expression + ")"], "aggregate": aggregate,
            "complex_mode": "real", "solution": {"dataset": "dset2", "outer": "all", "inner": "all"}}},
            "C08-aggregate-" + aggregate)
        assert aggregate_result["field_array"]["shape"] == [2, 4, 5, 1], aggregate_result
        for oi, outer in enumerate(axes["outer_indices"]):
            for ii, inner in enumerate(axes["inner_indices"]):
                middle_value = field["values"][0][oi][ii][1]
                targets = [middle_value, -2 * middle_value] if aggregate == "average" else [.05 / math.sqrt(12), .1 / math.sqrt(12)]
                for ei, target in enumerate(targets):
                    close(aggregate_result["field_array"]["values"][ei][oi][ii], [target])
    measured = await action("result.evaluate", {"spec": {"expressions": ["1"], "aggregate": "integral",
        "complex_mode": "real", "solution": {"dataset": "dset2", "outer": "all", "inner": "all"},
        "selection": {"kind": "explicit", "component": "comp1", "geometry": "geom1",
                      "entity_dimension": 2, "entities": [1]}}}, "C08-selection-per-axis-measure")
    assert measured["field_array"]["shape"] == [1, 4, 5, 1], measured
    close(measured["values"], [.0005] * 20)
    for outer, inner, oi, ii, label in [("first", "last", 0, 4, "first-last"), ("last", "first", 3, 0, "last-first"),
                                     ([2], [3], 1, 2, "subset")]:
        subset = await sample({"outer": outer, "inner": inner}, "C08-" + label)
        assert subset["field_array"]["shape"] == [2, 1, 1, 3], subset
        for expression_index in [0, 1]:
            close(subset["field_array"]["values"][expression_index][0][0], field["values"][expression_index][oi][ii])
    for invalid, label in [({"outer": [0]}, "zero"), ({"inner": [999]}, "out-of-range"),
                           ({"dataset": "absent"}, "wrong-dataset"), ({"solution": "sol1"}, "wrong-solution")]:
        await sample(invalid, "C08-reject-" + label, success=False)
    records["C08"] = {"status": "PASS", "scope": "LIVE_PUBLIC_MCP", "shape": [2, 4, 5, 3],
                      "parameter_pairs": sorted(parameter_pairs), "analytic_expression": expression}
    (run_dir / "numeric_cases.json").write_text(json.dumps(records, indent=2) + "\n")
    return records
