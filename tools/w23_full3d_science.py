"""Independent 3-D quadrature checks for managed W23 native field readbacks.

This module accepts only fresh COMSOL Interp readbacks, their exact native
solution identities, and a frozen circular-port sampling contract.  It never
accepts caller-supplied field arrays as native data and never changes native
status from NOT_RUN to PASS.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4


class Full3DScienceError(ValueError):
    """A native field readback or independent comparison is inadmissible."""


class ManagedRouteOutcomeError(RuntimeError):
    """One public managed dispatch failed or became unobservable; never replay it."""

    def __init__(self, label: str, outcome: str, message: str, *, response: Any = None,
                 job_id: str | None = None):
        super().__init__(f"{label}: {message}")
        self.label = label
        self.outcome = outcome
        self.response = response
        self.job_id = job_id
        self.retry_forbidden = True


_AXES = ("x", "y", "z")
_VECTOR_FIELDS = {
    "signal": tuple(f"ewfd.{prefix}{axis}" for prefix in ("E", "H") for axis in _AXES),
    "reference_mode": tuple(f"ewfd.{prefix}{axis}_2" for prefix in ("Emode", "Hmode") for axis in _AXES),
    "incident_reference": tuple(f"ewfd.{prefix}{axis}_1" for prefix in ("Emode", "Hmode") for axis in _AXES),
    "capture_signal": tuple(f"ewfd.{prefix}{axis}" for prefix in ("E", "H") for axis in _AXES),
    "bma_basis_mapping_pair": (
        tuple(f"ewfd.{prefix}{axis}" for prefix in ("E", "H") for axis in _AXES)
        + tuple(f"ewfd.{prefix}{axis}_2" for prefix in ("Emode", "Hmode") for axis in _AXES)
    ),
}
_NORMAL_FIELDS = ("nx", "ny", "nz")
_COMPONENT = "comp3d"
_GEOMETRY = "geom3d"
_PLANE_IDS = {"input": "input_port", "output": "receiver_port"}
_POWER_UNIT = "W"
_MODEL_REF_KEYS = {"schema_version", "session_id", "server_instance_id", "model_tag", "generation"}
_TAG = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}$")
_BMA_PROBE_STUDY = "std3dBmaOutputProbe"
_BMA_PROBE_STEP = "bmaOutputProbe"
# Frozen software comparison policy.  Values were selected before any W23
# native run; they are not inferred or widened from observed native results.
# The absolute cross tolerance is dimensionless after normalization by the
# positive reference-power scale 4*sqrt(P_signal*P_mode).  Only expected
# values inside that explicit near-zero band use the absolute floor.  Every
# larger nonzero comparison retains the prior strict relative limit.
FULL3D_COMPARISON_POLICY = {
    "policy_id": "w23.full3d.native_vs_quadrature.atol_rtol.v1",
    "relative_tolerance": 1e-3,
    "cross_absolute_normalized_tolerance": 1e-5,
    "eta_absolute_tolerance": 1e-10,
    "capture_flux_absolute_normalized_tolerance": 1e-5,
}

# Frozen W23 convergence recipe. These are engineering stability thresholds,
# not a COMSOL physical law, a fitted convergence order, or full acceptance.
FULL3D_CONVERGENCE_POLICY = {
    "schema_id": "urn:comsol-mcp:w23:full3d-convergence-recipe:1.0.0",
    "policy_id": "w23.full3d.mesh-and-quadrature-stability.v1",
    "mesh_scale_factors": [1.0, 0.8, 0.64],
    "quadrature_levels": [
        {"level_id": "q32x64", "radial_intervals": 32, "angular_points": 64},
        {"level_id": "q64x128", "radial_intervals": 64, "angular_points": 128},
        {"level_id": "q128x256", "radial_intervals": 128, "angular_points": 256},
    ],
    "adjacent_eta_absolute_delta_limit": 1e-3,
    "adjacent_signed_power_over_pin_absolute_delta_limit": 1e-3,
    "normalized_power_unit": "1",
    "mesh_levels_require_distinct_study_producers": True,
    "mesh_levels_require_distinct_models": True,
    "same_mesh_quadrature_levels_reuse_one_stored_solution_cohort": True,
    "power_balance_status": "NOT_RUN_MISSING_NATIVE_CLOSED_CONTROL_VOLUME_READBACK",
    "receiver_radiation_status": "UNVERIFIED_MISSING_LONGITUDINAL_PML_DOMAIN_BACKED_PORT_READBACK",
    "native_convergence_status": "NOT_RUN",
    "scientific_acceptance": "NOT_RUN",
    "convergence_order_or_error_bound": "NOT_CLAIMED",
    "phase_or_degenerate_coefficient_comparison": "NOT_USED",
}

# This is a narrow engineering identity-probe threshold, not a COMSOL physical
# law or full scientific acceptance limit. It reuses the already frozen
# dimensionless absolute/relative tolerances without tuning against native data.
BMA_FIELD_MAPPING_POLICY = {
    "policy_id": "w23.full3d.bma_port_mode_identity_probe.atol_rtol.v1",
    "normalized_absolute_tolerance": 1e-5,
    "relative_tolerance": 1e-3,
    "normalized_residual_limit": 1.01e-3,
    "source_policy_id": FULL3D_COMPARISON_POLICY["policy_id"],
    "scope": "engineering identity probe only; not physical or full scientific acceptance",
    "field_mapping_status": "UNVERIFIED_UNTIL_NATIVE_REVIEW",
}

COMSOL_PORT_MODE_FIELD_KB_EVIDENCE = {
    "manual": "COMSOL 6.4 - Port Mode Field Variables",
    "document_path": "doc/help/wtpwebapps/ROOT/doc/com.comsol.help.rf/rf_ug_modeling.05.22.html",
    "chunk_id": 104589,
    "source_sha256": "d568ce649f47507fb6de28d2276d7a9774a9d0073a45d72ec107ec7b6f9c0bdb",
    "claim": "The EmodeC_K and HmodeC_K suffix K denotes the port name, not the BMA eigensolution ordinal.",
}

COMSOL_INTERP_UNIT_KB_EVIDENCE = {
    "manual": "COMSOL 6.4 - Interp",
    "document_path": "doc/help/wtpwebapps/ROOT/doc/com.comsol.help.comsol/comsol_api_results.52.082.html",
    "chunk_id": 17294,
    "source_sha256": "156412ab29631518953b57d6948b88a0181bf5312984b1a0f9e840a6ad848421",
    "claim": "Interp exposes a unit String property for the expressions in expr; paired sampling uses separate same-grid groups for V/m, A/m, and 1.",
}

_BMA_PAIR_E_FIELDS = tuple(f"ewfd.E{axis}" for axis in _AXES) + tuple(
    f"ewfd.Emode{axis}_2" for axis in _AXES)
_BMA_PAIR_H_FIELDS = tuple(f"ewfd.H{axis}" for axis in _AXES) + tuple(
    f"ewfd.Hmode{axis}_2" for axis in _AXES)
_BMA_PAIR_NORMAL_FIELDS = _NORMAL_FIELDS
_BMA_PAIR_FIELD_NAMES = _BMA_PAIR_E_FIELDS + _BMA_PAIR_H_FIELDS + _BMA_PAIR_NORMAL_FIELDS
_BMA_PAIR_UNIT_GROUPS = {
    "electric": {"unit": "V/m", "expressions": list(_BMA_PAIR_E_FIELDS)},
    "magnetic": {"unit": "A/m", "expressions": list(_BMA_PAIR_H_FIELDS)},
    "normal": {"unit": "1", "expressions": list(_BMA_PAIR_NORMAL_FIELDS)},
}


def _field_unit_groups(expressions: Sequence[str]) -> dict[str, dict[str, Any]]:
    rows = list(expressions)
    groups = {
        "electric": {"unit": "V/m", "expressions": [row for row in rows if row.startswith("ewfd.E")]},
        "magnetic": {"unit": "A/m", "expressions": [row for row in rows if row.startswith("ewfd.H")]},
        "normal": {"unit": "1", "expressions": [row for row in rows if row in _NORMAL_FIELDS]},
    }
    if (any(not group["expressions"] for group in groups.values())
            or sum(len(group["expressions"]) for group in groups.values()) != len(rows)):
        _fail("full-3D raw sample requires exact nonempty E/H/normal SI unit groups")
    return groups


def _fail(message: str) -> None:
    raise Full3DScienceError(message)


def _sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_full3d_convergence_recipe(fixture_recipe: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Freeze the approved W23 mesh/quadrature levels without claiming a solve.

    The geometric and optical inputs remain sourced from the published
    ``canonical_full3d_recipe``.  Only the two existing mesh size expressions
    are scaled; geometry, materials, physics, solver and PML settings are
    represented by the exact fixture-recipe digest and remain fixed.
    """
    if fixture_recipe is None:
        from tools.w23_full3d import canonical_full3d_recipe, verify_recipe

        fixture_recipe = canonical_full3d_recipe()
    else:
        from tools.w23_full3d import verify_recipe
    if not isinstance(fixture_recipe, Mapping):
        _fail("the published canonical full-3D fixture recipe is required")
    optics = fixture_recipe.get("optics")
    materials = fixture_recipe.get("materials")
    if not isinstance(optics, Mapping) or not isinstance(materials, Mapping):
        _fail("the canonical fixture recipe omitted frozen optics/materials")
    wavelength_um = _finite_number(optics.get("vacuum_wavelength_um"), "vacuum wavelength")
    lens = materials.get("lens")
    lens_index = _finite_number(lens.get("refractive_index"), "lens refractive index") \
        if isinstance(lens, Mapping) else math.nan
    if wavelength_um <= 0.0 or lens_index <= 0.0:
        _fail("positive baseline wavelength and lens index are required for the mesh recipe")
    fixture_digest = verify_recipe(fixture_recipe)["recipe_sha256"]
    levels = []
    for index, scale in enumerate(FULL3D_CONVERGENCE_POLICY["mesh_scale_factors"], start=1):
        hmax_um = wavelength_um / (5.0 * lens_index) * scale
        hmin_um = wavelength_um / (12.0 * lens_index) * scale
        if not math.isfinite(hmax_um) or not math.isfinite(hmin_um) or hmin_um <= 0 or hmax_um < hmin_um:
            _fail("derived mesh sizes are non-finite or inconsistent")
        levels.append({
            "level_id": f"mesh{index}", "level_index": index,
            "scale_factor": scale,
            "hmax_expression": "lambda0/(5*w23Nlens)" if scale == 1.0
                else f"(lambda0/(5*w23Nlens))*{scale:g}",
            "hmin_expression": "lambda0/(12*w23Nlens)" if scale == 1.0
                else f"(lambda0/(12*w23Nlens))*{scale:g}",
            "hmax_um_expected": hmax_um, "hmin_um_expected": hmin_um,
            "status": "PLANNED_NOT_RUN", "native_result": "NOT_RUN",
        })
    identity = {
        "schema_id": FULL3D_CONVERGENCE_POLICY["schema_id"],
        "policy": dict(FULL3D_CONVERGENCE_POLICY),
        "fixture_id": fixture_recipe.get("fixture_id"),
        "fixture_recipe_sha256": fixture_digest,
        "fixture_settings_frozen": {
            "geometry": fixture_recipe.get("geometry"),
            "materials": fixture_recipe.get("materials"),
            "optics": fixture_recipe.get("optics"),
            "mode_basis": fixture_recipe.get("mode_basis"),
        },
        "mesh_levels": levels,
        "quadrature_levels": [dict(row) for row in FULL3D_CONVERGENCE_POLICY["quadrature_levels"]],
        "power_balance": {
            "status": FULL3D_CONVERGENCE_POLICY["power_balance_status"],
            "required_control_volume": "explicit closed physical-domain boundary excluding PML",
            "required_terms": ["signed outward Poynting flux in W", "native Q_abs volume integral in W",
                               "positive native incident Pin in W"],
            "formula": "R=(closed physical-domain outward flux + volume integral Q_abs)/Pin",
            "count_boundary_once": True,
            "never_double_count_receiver_flux_and_downstream_pml_absorption": True,
        },
        "receiver_radiation": {
            "status": FULL3D_CONVERGENCE_POLICY["receiver_radiation_status"],
            "existing_fixture_observation": (
                "transverse y-z PML shell only; output port is x=20 um, domain ends x=21 um, "
                "and no verified longitudinal PML/backed receiver segment is present"),
        },
        "resource_gate": {
            "status": "NOT_FROZEN",
            "required_before_native_solve": [
                "actual mesh element and solver DOF readback",
                "memory/resource ceiling compatible with the authorized host",
                "wall budget including exact cleanup reserve",
            ],
        },
        "native_result": "NOT_RUN",
        "scientific_acceptance": "NOT_RUN",
        "study_or_solver_invoked": False,
    }
    return {**identity, "recipe_sha256": _sha256(identity)}


def build_full3d_mesh_level_fixture_dispatch(
    convergence_recipe: Mapping[str, Any], *, mesh_level_id: str,
    mesh_scale_factor: float, source_artifact: str, project_id: str,
    model_ref: Mapping[str, Any], model_tag: str, revision: int,
    request_id: str, idempotency_key: str,
) -> dict[str, Any]:
    """Build one exact no-solve fixture route at a frozen pre-solve mesh level."""
    from tools.w23_full3d import build_full3d_fixture_dispatch, canonical_full3d_recipe

    fixture_recipe = canonical_full3d_recipe()
    expected_recipe = build_full3d_convergence_recipe(fixture_recipe)
    if not isinstance(convergence_recipe, Mapping) or dict(convergence_recipe) != expected_recipe:
        _fail("mesh-level setup requires the exact frozen canonical W23 convergence recipe")
    level = next((row for row in expected_recipe["mesh_levels"]
                  if row["level_id"] == mesh_level_id), None)
    if (not isinstance(level, Mapping) or isinstance(mesh_scale_factor, bool)
            or not isinstance(mesh_scale_factor, (int, float))
            or not math.isfinite(float(mesh_scale_factor))
            or float(mesh_scale_factor) != level["scale_factor"]):
        _fail("mesh-level setup ID and factor must match one frozen W23 convergence level")
    request = build_full3d_fixture_dispatch(
        fixture_recipe, source_artifact=source_artifact,
        project_id=project_id, model_ref=model_ref, model_tag=model_tag,
        revision=revision, request_id=request_id, idempotency_key=idempotency_key)
    bounded = copy.deepcopy(request)
    java_args = bounded["arguments"]["arguments"]["arguments"]
    java_args["mesh_level_id"] = mesh_level_id
    java_args["mesh_scale_factor"] = float(mesh_scale_factor)
    bounded["dispatch_scope"] = (
        f"one managed Java fixture build at frozen {mesh_level_id} mesh scale; "
        "no Study.run or solver call")
    return bounded


def recompute_full3d_mesh_stability_from_registered_routes(
    recipe: Mapping[str, Any], mesh_runs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Recompute mesh-series metrics only from exact managed terminal routes.

    Each row is one no-solve fixture build with a predeclared mesh level, one
    std3d solve, a source-cohort probe routed after that solve, and the
    registered v2 IntSurface operation. The function derives powers and the
    two-mode projection from terminal native terms; caller-supplied
    metrics/deltas/status fields are ignored. Capture
    aperture, independent raw-field quadrature, full producer lineage, and
    power-balance gates stay explicitly NOT_RUN/UNVERIFIED here.
    """
    from jsonschema import validate as validate_jsonschema
    from comsol_mcp._w23_basis_v2_results import NATIVE_RESULT_SCHEMA, validate_request_shape
    from tools.w23_mode_basis_v2 import _basis_reference_identity, compute_two_mode_basis_projection

    if not isinstance(recipe, Mapping) or not isinstance(mesh_runs, Sequence) \
            or isinstance(mesh_runs, (str, bytes)) or len(mesh_runs) != 3:
        _fail("three frozen mesh-level route records are required for W23 stability recomputation")
    from tools.w23_full3d import canonical_full3d_recipe

    expected_recipe = build_full3d_convergence_recipe(canonical_full3d_recipe())
    if dict(recipe) != expected_recipe:
        _fail("convergence recipe differs from the frozen canonical W23 policy or fixture digest")

    def managed_binding(request: Mapping[str, Any], route_result: Mapping[str, Any], *,
                        revision_delta: int, cap: Any, label: str) -> dict[str, Any]:
        return validate_full3d_bma_mapping_route_result(
            request, route_result, expected_revision_delta=revision_delta,
            max_execution_timeout_s=cap)

    def required_route(row: Mapping[str, Any], request_key: str, result_key: str,
                       cap_key: str, *, revision_delta: int, label: str) -> tuple[Mapping[str, Any], Mapping[str, Any], dict[str, Any]]:
        request, result, cap = row.get(request_key), row.get(result_key), row.get(cap_key)
        if not isinstance(request, Mapping) or not isinstance(result, Mapping):
            _fail(f"{label} requires its original public request and terminal job result")
        binding = managed_binding(request, result, revision_delta=revision_delta,
                                  cap=cap, label=label)
        return request, result, binding

    outputs = []
    model_refs: list[dict[str, Any]] = []
    study_jobs: list[str] = []
    request_ids: list[str] = []
    operation_ids: list[str] = []
    job_ids: list[str] = []
    explicit_configs: list[dict[str, Any]] = []
    basis_layouts: list[dict[str, Any]] = []
    expected_levels = recipe["mesh_levels"]
    for index, (run, level) in enumerate(zip(mesh_runs, expected_levels), start=1):
        if not isinstance(run, Mapping) or run.get("mesh_level_id") != level["level_id"]:
            _fail("mesh route records are missing, reordered, or detached from the frozen level IDs")
        build_request, build_route, build_binding = required_route(
            run, "build_request", "build_route_result", "build_max_execution_timeout_s",
            revision_delta=1, label=f"{level['level_id']} fixture/mesh setup")
        build_args = build_request.get("arguments")
        build_java = build_args.get("arguments") if isinstance(build_args, Mapping) else None
        build_payload = build_java.get("arguments") if isinstance(build_java, Mapping) else None
        build_exec = build_request.get("execution")
        if (not isinstance(build_args, Mapping)
                or build_args.get("operation_id") != "code.execute_java"
                or not isinstance(build_java, Mapping)
                or build_java.get("entrypoint") != "NativeW23Full3DFixture#run"
                or build_java.get("mode") != "trusted"
                or not isinstance(build_payload, Mapping)
                or build_payload.get("phase") != "build"
                or build_payload.get("fixture_id") != "w23_full3d_fiber_ball_lens_vector_pml_v1"
                or build_payload.get("recipe_sha256") != recipe["fixture_recipe_sha256"]
                or build_payload.get("mesh_level_id") != level["level_id"]
                or build_payload.get("mesh_scale_factor") != level["scale_factor"]
                or build_payload.get("native_result") != "NOT_RUN"
                or build_payload.get("study_or_solver_invoked") is not False
                or not isinstance(build_exec, Mapping)):
            _fail(f"{level['level_id']} setup is not the exact frozen no-solve fixture build route")
        build_response = build_route.get("response")
        build_readback = _java_action_readback(build_response, f"{level['level_id']} setup readback") \
            if isinstance(build_response, Mapping) else None
        expected_build_identity = {
            "project_id": build_exec.get("project_id"), "model_ref": build_exec.get("model_ref"),
            "model_tag": build_exec.get("model_ref", {}).get("model_tag")
                if isinstance(build_exec.get("model_ref"), Mapping) else None,
            "expected_revision": build_exec.get("expected_revision"),
        }
        build_mesh = build_readback.get("mesh") if isinstance(build_readback, Mapping) else None
        build_size = build_mesh.get("size_properties") if isinstance(build_mesh, Mapping) else None
        build_props = build_size.get("requested_properties") if isinstance(build_size, Mapping) else None

        def build_string_property(properties: Any, key: str) -> Any:
            item = properties.get(key) if isinstance(properties, Mapping) else None
            return item.get("string_readback") if isinstance(item, Mapping) \
                and item.get("has_property_exact") is True else None

        if (not isinstance(build_readback, Mapping)
                or build_readback.get("fixture_id") != "w23_full3d_fiber_ball_lens_vector_pml_v1"
                or build_readback.get("recipe_sha256") != recipe["fixture_recipe_sha256"]
                or build_readback.get("status") != "BUILT_CONFIGURED_NOT_SOLVED"
                or build_readback.get("native_result") != "NOT_RUN"
                or build_readback.get("study_or_solver_invoked") is not False
                or build_readback.get("managed_identity") != expected_build_identity
                or not isinstance(build_mesh, Mapping)
                or build_mesh.get("tag") != "mesh3d" or build_mesh.get("geometry") != "geom3d"
                or build_mesh.get("mesh_level_id") != level["level_id"]
                or build_mesh.get("mesh_scale_factor") != level["scale_factor"]
                or build_mesh.get("hmax") != level["hmax_expression"]
                or build_mesh.get("hmin") != level["hmin_expression"]
                or build_string_property(build_props, "custom") != "on"
                or build_string_property(build_props, "hmax") != level["hmax_expression"]
                or build_string_property(build_props, "hmin") != level["hmin_expression"]
                or type(build_mesh.get("elements")) is not int or build_mesh["elements"] <= 0):
            _fail(f"{level['level_id']} terminal build result does not prove its actual pre-solve mesh setting")
        study_request, study_result, study_binding = required_route(
            run, "study_request", "study_route_result", "study_max_execution_timeout_s",
            revision_delta=1, label=f"{level['level_id']} std3d solve")
        study_args = study_request.get("arguments")
        study_operation_args = study_args.get("arguments") if isinstance(study_args, Mapping) else None
        study_exec = study_request.get("execution")
        if (not isinstance(study_args, Mapping) or study_args.get("operation_id") != "study.run"
                or not isinstance(study_operation_args, Mapping)
                or study_operation_args.get("study") != {"segments": [{"collection": "study", "tag": "std3d"}]}
                or isinstance(study_operation_args.get("timeout_s"), bool)
                or not isinstance(study_operation_args.get("timeout_s"), (int, float))
                or not math.isfinite(float(study_operation_args["timeout_s"]))
                or study_operation_args["timeout_s"] <= 0.0
                or not isinstance(study_exec, Mapping)
                or study_exec.get("expected_revision") != build_binding["revision_after"]
                or study_exec.get("project_id") != build_exec.get("project_id")
                or study_exec.get("model_ref") != build_exec.get("model_ref")):
            _fail(f"{level['level_id']} producer route is not the complete frozen std3d Study.run")

        probe_request, probe_route, probe_binding = required_route(
            run, "mesh_probe_request", "mesh_probe_route_result", "mesh_probe_max_execution_timeout_s",
            revision_delta=1, label=f"{level['level_id']} source-cohort mesh readback")
        probe_exec = probe_request.get("execution")
        probe_args = probe_request.get("arguments")
        java_args = probe_args.get("arguments") if isinstance(probe_args, Mapping) else None
        raw_args = java_args.get("arguments") if isinstance(java_args, Mapping) else None
        contract = raw_args.get("contract") if isinstance(raw_args, Mapping) else None
        if (not isinstance(probe_exec, Mapping)
                or probe_exec.get("expected_revision") != study_binding["revision_after"]
                or probe_exec.get("model_ref") != study_exec.get("model_ref")
                or probe_exec.get("project_id") != study_exec.get("project_id")
                or not isinstance(probe_args, Mapping)
                or probe_args.get("operation_id") != "code.execute_java"
                or not isinstance(java_args, Mapping)
                or java_args.get("entrypoint") != "NativeW23Full3DFixture#run"
                or java_args.get("mode") != "trusted"
                or not isinstance(raw_args, Mapping)
                or raw_args.get("phase") != "bma_basis_fields"
                or raw_args.get("study_or_solver_invoked") is not False
                or not isinstance(contract, Mapping)
                or contract.get("role") != "bma_basis_mapping_pair"
                or contract.get("provenance_schema") != "w23.full3d.numeric_port_bma_basis_pair.v1"):
            _fail(f"{level['level_id']} mesh snapshot is not a public post-solve BMA-basis sample")
        probe_response = probe_route.get("response")
        raw_probe = _java_action_readback(probe_response, f"{level['level_id']} mesh source-cohort readback") \
            if isinstance(probe_response, Mapping) else None
        if not isinstance(raw_probe, Mapping):
            _fail(f"{level['level_id']} mesh probe omitted its terminal Java readback")
        source_snapshot = validate_native_source_cohort_snapshot(contract, raw_probe)
        config = raw_probe["source_cohort"]["before"]["fixture_explicit_configuration"]
        mesh = config.get("mesh") if isinstance(config, Mapping) else None
        size = mesh.get("size_properties") if isinstance(mesh, Mapping) else None
        props = size.get("requested_properties") if isinstance(size, Mapping) else None
        expected_hmax = level["hmax_expression"]
        expected_hmin = level["hmin_expression"]

        def string_property(properties: Any, key: str) -> Any:
            item = properties.get(key) if isinstance(properties, Mapping) else None
            return item.get("string_readback") if isinstance(item, Mapping) \
                and item.get("has_property_exact") is True else None

        element_count = mesh.get("elements") if isinstance(mesh, Mapping) else None
        if (not isinstance(mesh, Mapping) or mesh.get("tag") != "mesh3d"
                or mesh.get("geometry") != "geom3d" or type(element_count) is not int or element_count <= 0
                or element_count != build_mesh.get("elements")
                or string_property(props, "custom") != "on"
                or string_property(props, "hmax") != expected_hmax
                or string_property(props, "hmin") != expected_hmin):
            _fail(f"{level['level_id']} post-solve mesh differs from its pre-solve setup readback")

        overlap_request, overlap_route, overlap_binding = required_route(
            run, "overlap_request", "overlap_route_result", "overlap_max_execution_timeout_s",
            revision_delta=0, label=f"{level['level_id']} registered v2 overlap")
        overlap_exec = overlap_request.get("execution")
        overlap_args = overlap_request.get("arguments")
        operation_args = overlap_args.get("arguments") if isinstance(overlap_args, Mapping) else None
        definition = operation_args.get("definition") if isinstance(operation_args, Mapping) else None
        if (not isinstance(overlap_exec, Mapping)
                or overlap_exec.get("expected_revision") != probe_binding["revision_after"]
                or overlap_exec.get("model_ref") != probe_exec.get("model_ref")
                or overlap_exec.get("project_id") != probe_exec.get("project_id")
                or not isinstance(operation_args, Mapping)
                or overlap_args.get("operation_id") != "result.mode_overlap_basis_v2"
                or not isinstance(definition, Mapping)):
            _fail(f"{level['level_id']} v2 result is not the current-model registered public operation")
        try:
            validate_request_shape({"definition": dict(definition)})
        except Exception as exc:
            raise Full3DScienceError(f"{level['level_id']} v2 definition failed its published schema") from exc
        basis_request = definition["basis_request"]
        if (basis_request.get("project_id") != overlap_exec.get("project_id")
                or basis_request.get("model_ref") != overlap_exec.get("model_ref")
                or basis_request.get("model_revision") != overlap_exec.get("expected_revision")):
            _fail(f"{level['level_id']} v2 definition is detached from its live project/ModelRef/revision")
        native_route_response = overlap_route.get("response")
        native_result = native_route_response.get("data") if isinstance(native_route_response, Mapping) else None
        if not isinstance(native_result, Mapping):
            _fail(f"{level['level_id']} registered v2 route omitted its native result object")
        try:
            validate_jsonschema(instance=dict(native_result), schema=NATIVE_RESULT_SCHEMA)
        except Exception as exc:
            raise Full3DScienceError(f"{level['level_id']} terminal v2 result failed its published schema") from exc
        expected_identity = {
            "basis_id": basis_request["basis_id"], "case": dict(basis_request["case"]),
            "project_id": basis_request["project_id"], "model_ref": dict(basis_request["model_ref"]),
            "model_tag": basis_request["model_ref"]["model_tag"],
            "model_revision": basis_request["model_revision"],
            "geometry_revision": basis_request["geometry_revision"],
            "frequency_hz": basis_request["frequency_hz"],
            "coordinate_frame": basis_request["coordinate_frame"],
            "mode_ids": [row["mode_id"] for row in basis_request["basis_modes"]],
            "mode_indices": [row["mode_index"] for row in basis_request["basis_modes"]],
        }
        if (native_result.get("basis_request_id") != basis_request.get("request_id")
                or native_result.get("definition_sha256") != definition.get("definition_sha256")
                or native_result.get("identity") != expected_identity
                or native_result.get("managed_execution_binding") != {
                    "project_id": overlap_exec["project_id"], "model_ref": dict(overlap_exec["model_ref"]),
                    "model_revision": overlap_exec["expected_revision"],
                    "source": "managed_observation_context"}):
            _fail(f"{level['level_id']} terminal v2 result is detached from its registered source request")
        native_provenance = native_result.get("native_mode_provenance")
        if (not isinstance(native_provenance, Mapping)
                or native_provenance.get("configuration_and_solution_lineage_status") != "UNVERIFIED"
                or native_provenance.get("field_variable_to_eigensolution_mapping_status")
                != "UNVERIFIED_NATIVE_FIELD_SAMPLE_REQUIRED"):
            _fail("mesh trend cannot upgrade unresolved BMA/field lineage")
        mode_sources = [row["source"] for row in basis_request["basis_modes"]]
        probe_source = contract.get("source")
        if not any(all(probe_source.get(key) == source.get(key) for key in
                       ("dataset_id", "solution_id", "outer_index", "inner_index", "solnum"))
                     for source in mode_sources):
            _fail(f"{level['level_id']} source-cohort mesh probe is not one of the registered v2 basis solutions")

        terms = native_result["native_integrals"]["terms"]

        def complex_term(term_id: str) -> complex:
            record = terms.get(term_id)
            cleanup = record.get("cleanup") if isinstance(record, Mapping) else None
            if (not isinstance(record, Mapping) or record.get("unit") != "W"
                    or record.get("feature_type") != "IntSurface"
                    or not isinstance(cleanup, Mapping) or cleanup.get("removed") is not True
                    or cleanup.get("cleanup_failed") is not False):
                _fail(f"{level['level_id']} {term_id} lacks raw W integral/temporary cleanup evidence")
            return complex(_finite_number(record.get("real"), term_id + ".real"),
                           _finite_number(record.get("imag"), term_id + ".imag"))

        gram = [[complex_term("G00"), complex_term("G01")],
                [complex_term("G10"), complex_term("G11")]]
        coupling = [complex_term("b0"), complex_term("b1")]
        signal_power = complex_term("P_signal")
        incident_power = complex_term("P_incident")
        if (abs(signal_power.imag) > 1e-10 * max(abs(signal_power.real), 1e-30)
                or abs(incident_power.imag) > 1e-10 * max(abs(incident_power.real), 1e-30)
                or signal_power.real <= 0.0 or incident_power.real <= 0.0):
            _fail(f"{level['level_id']} native signed powers must be finite, real, and Pin positive")
        projection = compute_two_mode_basis_projection(
            gram, coupling, signal_power_w=signal_power.real,
            incident_power_w=incident_power.real,
            source_identity=_basis_reference_identity(basis_request))
        native_integrals = native_result["native_integrals"]
        if (native_integrals.get("gram_matrix") != [[terms["G00"], terms["G01"]],
                                                      [terms["G10"], terms["G11"]]]
                or native_integrals.get("coupling_vector") != [terms["b0"], terms["b1"]]
                or native_integrals.get("signal_power") != terms["P_signal"]
                or native_integrals.get("incident_reference_power") != terms["P_incident"]):
            _fail(f"{level['level_id']} v2 term summary differs from its exact raw term table")

        model_ref = dict(overlap_exec["model_ref"])
        if (build_exec.get("model_ref") != model_ref
                or study_exec.get("model_ref") != model_ref
                or probe_exec.get("model_ref") != model_ref
                or build_exec.get("project_id") != overlap_exec.get("project_id")):
            _fail(f"{level['level_id']} mesh setup, solve, sample, and overlap do not share one managed model")
        model_refs.append(model_ref)
        study_jobs.append(study_result["job_id"])
        request_ids.extend([build_request["execution"]["request_id"],
                            study_request["execution"]["request_id"],
                            probe_request["execution"]["request_id"],
                            overlap_request["execution"]["request_id"]])
        operation_ids.extend([build_binding["operation_instance_id"],
                              study_binding["operation_instance_id"],
                              probe_binding["operation_instance_id"],
                              overlap_binding["operation_instance_id"]])
        job_ids.extend([build_binding["job_id"], study_binding["job_id"],
                        probe_binding["job_id"], overlap_binding["job_id"]])
        explicit_configs.append(dict(config))
        layout = dict(basis_request)
        for name in ("request_id", "model_ref", "model_revision"):
            layout.pop(name, None)
        for role in ("signal_source", "incident_source"):
            layout[role] = {key: value for key, value in basis_request[role].items()
                            if key not in {"model_ref", "model_revision"}}
        layout["basis_modes"] = [
            {**{key: value for key, value in mode.items() if key != "source"},
             "source": {key: value for key, value in mode["source"].items()
                        if key not in {"model_ref", "model_revision"}}}
            for mode in basis_request["basis_modes"]]
        integral_plan = layout.get("native_integral_plan")
        if isinstance(integral_plan, Mapping) and isinstance(integral_plan.get("terms"), list):
            normalized_plan = dict(integral_plan)
            normalized_terms = []
            for term in integral_plan["terms"]:
                normalized_term = dict(term)
                for source_key in ("a_source", "b_source"):
                    source = normalized_term.get(source_key)
                    if isinstance(source, Mapping):
                        normalized_term[source_key] = {
                            key: value for key, value in source.items()
                            if key not in {"model_ref", "model_revision"}}
                normalized_terms.append(normalized_term)
            normalized_plan["terms"] = normalized_terms
            layout["native_integral_plan"] = normalized_plan
        basis_layouts.append(layout)
        outputs.append({"mesh_level_id": level["level_id"],
            "model_ref": model_ref, "revision": overlap_binding["revision_after"],
            "mesh_setup_readback": dict(build_mesh),
            "mesh_readback": dict(mesh), "source_cohort_status": source_snapshot["status"],
            "source_cohort_sha256": _sha256(source_snapshot),
            "native_basis_result_sha256": _sha256(dict(native_result)),
            "eta_basis_from_registered_terms": projection["eta_basis_raw"],
            "signal_power_w": signal_power.real, "incident_pin_w": incident_power.real,
            "signed_signal_power_over_pin": signal_power.real / incident_power.real,
            "basis_diagonal_power_over_pin_gauge_dependent": [
                complex_term("G00").real / incident_power.real,
                complex_term("G11").real / incident_power.real],
            "v2_field_mapping": "UNVERIFIED",
            "producer_step_solution_lineage": "UNVERIFIED"})

    if (len({json.dumps(row, sort_keys=True) for row in model_refs}) != 3
            or len(set(study_jobs)) != 3
            or len(set(request_ids)) != len(request_ids)
            or len(set(operation_ids)) != len(operation_ids)
            or len(set(job_ids)) != len(job_ids)):
        _fail("three mesh levels require distinct ModelRefs and non-replayed producer/sample/integral jobs")
    base_config = dict(explicit_configs[0])
    base_config.pop("mesh", None)
    for config in explicit_configs[1:]:
        fixed = dict(config)
        fixed.pop("mesh", None)
        if fixed != base_config:
            _fail("mesh levels changed frozen non-mesh W23 fixture configuration readbacks")
    if any(layout != basis_layouts[0] for layout in basis_layouts[1:]):
        differing_keys = sorted({key for layout in basis_layouts[1:]
            for key in set(layout) | set(basis_layouts[0])
            if layout.get(key) != basis_layouts[0].get(key)})
        _fail("mesh levels do not share the exact frozen optics/source/surface/basis definition: "
              f"different top-level fields={differing_keys}")
    element_counts = [row["mesh_readback"]["elements"] for row in outputs]
    if len(set(element_counts)) != 3:
        _fail("each frozen mesh scale must read back a distinct actual finite-element count")

    adjacent = []
    within_limits = True
    limit = float(FULL3D_CONVERGENCE_POLICY["adjacent_eta_absolute_delta_limit"])
    power_limit = float(FULL3D_CONVERGENCE_POLICY["adjacent_signed_power_over_pin_absolute_delta_limit"])
    for index in range(1, len(outputs)):
        previous, current = outputs[index - 1], outputs[index]
        eta_delta = abs(current["eta_basis_from_registered_terms"]
                         - previous["eta_basis_from_registered_terms"])
        signal_delta = abs(current["signed_signal_power_over_pin"]
                           - previous["signed_signal_power_over_pin"])
        basis_diagonal_diagnostic_deltas = [
            abs(current["basis_diagonal_power_over_pin_gauge_dependent"][axis]
                - previous["basis_diagonal_power_over_pin_gauge_dependent"][axis])
            for axis in range(2)]
        row = {"from_mesh": previous["mesh_level_id"], "to_mesh": current["mesh_level_id"],
               "eta_basis_absolute_delta": eta_delta,
               "signed_signal_power_over_pin_absolute_delta": signal_delta,
               "basis_diagonal_power_over_pin_gauge_dependent_diagnostic_deltas":
                   basis_diagonal_diagnostic_deltas,
               "basis_diagonal_power_used_as_gate": False,
               "source": "recomputed_from_exact_terminal_registered_v2_integral_terms"}
        adjacent.append(row)
        within_limits = within_limits and eta_delta <= limit and signal_delta <= power_limit
    return {"status": "SOFTWARE_MESH_SERIES_RECOMPUTED_CAPTURE_AND_QUADRATURE_PENDING",
        "evidence_scope": "exact public request/operation/job chains and route-returned registered v2 integrals",
        "mesh_runs": outputs, "adjacent_deltas": adjacent,
        "basis_and_signed_power_thresholds": "WITHIN_LIMITS" if within_limits else "LIMIT_EXCEEDED",
        "basis_diagonal_power_diagnostic": (
            "G00_AND_G11_OVER_PIN_ARE_GAUGE_DEPENDENT_AND_EXCLUDED_FROM_CONVERGENCE_GATE"),
        "eta_capture_convergence": "NOT_RUN_MISSING_ROUTE_BOUND_CORE_CAPTURE_INTEGRALS",
        "quadrature_convergence": "NOT_RUN_MISSING_ROUTE_BOUND_THREE_LEVEL_RAW_FIELD_SERIES",
        "power_balance": "NOT_RUN_MISSING_NATIVE_CLOSED_CONTROL_VOLUME_READBACK",
        "producer_step_solution_lineage": "UNVERIFIED",
        "numeric_port_mode_field_mapping": "UNVERIFIED",
        "native_convergence_status": "NOT_RUN",
        "scientific_acceptance": "NOT_RUN",
        "convergence_order_or_error_bound": "NOT_CLAIMED",
        "caller_metrics_or_deltas_accepted": False,
        "recipe_sha256": recipe["recipe_sha256"]}


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        _fail(f"{label} must be finite numeric data")
    return float(value)


def _vector3(value: Any, label: str) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        _fail(f"{label} must be a three-vector")
    return tuple(_finite_number(item, label) for item in value)  # type: ignore[return-value]


def _normalize(value: Sequence[float], label: str) -> tuple[float, float, float]:
    norm = math.sqrt(sum(float(item) * float(item) for item in value))
    if not math.isfinite(norm) or norm <= 0.0:
        _fail(f"{label} must have positive finite length")
    return tuple(float(item) / norm for item in value)  # type: ignore[return-value]


def _cross(left: Sequence[float], right: Sequence[float]) -> tuple[float, float, float]:
    return (left[1] * right[2] - left[2] * right[1],
            left[2] * right[0] - left[0] * right[2],
            left[0] * right[1] - left[1] * right[0])


def circular_port_quadrature(
    center_m: Sequence[float], axis_xyz: Sequence[float], radius_m: float, *,
    radial_intervals: int, angular_points: int,
) -> dict[str, Any]:
    """Construct deterministic polar Simpson/trapezoid points and area weights.

    The radial Simpson rule integrates ``r dr`` and the periodic trapezoid rule
    integrates angle. The center is represented once with zero area weight.
    The same routine independently reconstructs expected native Interp points.
    """
    center = _vector3(center_m, "port center")
    axis = _normalize(_vector3(axis_xyz, "port axis"), "port axis")
    radius = _finite_number(radius_m, "port radius")
    if radius <= 0.0:
        _fail("port radius must be positive")
    if type(radial_intervals) is not int or radial_intervals < 2 or radial_intervals % 2:
        _fail("radial_intervals must be a positive even integer")
    if type(angular_points) is not int or angular_points < 8 or angular_points % 4:
        _fail("angular_points must be an integer multiple of four and at least eight")

    reference = (0.0, 0.0, 1.0) if abs(axis[2]) < 0.9 else (0.0, 1.0, 0.0)
    tangent_1 = _normalize(_cross(reference, axis), "port tangent basis")
    tangent_2 = _normalize(_cross(axis, tangent_1), "port tangent basis")
    dr = radius / radial_intervals
    dphi = 2.0 * math.pi / angular_points
    points: list[tuple[float, float, float]] = [center]
    weights: list[float] = [0.0]
    for radial_index in range(1, radial_intervals + 1):
        radial = dr * radial_index
        simpson_coefficient = (1 if radial_index == radial_intervals else
                               4 if radial_index % 2 else 2)
        ring_weight = simpson_coefficient * dr * radial / 3.0 * dphi
        for angular_index in range(angular_points):
            phi = dphi * angular_index
            c, s = math.cos(phi), math.sin(phi)
            points.append(tuple(center[k] + radial * (c * tangent_1[k] + s * tangent_2[k])
                                for k in range(3)))
            weights.append(ring_weight)
    identity = {
        "profile": "w23.full3d.circular_port.radial_simpson_periodic_trapezoid.v1",
        "center_m": list(center), "axis_xyz": list(axis), "tangent_1_xyz": list(tangent_1),
        "tangent_2_xyz": list(tangent_2), "radius_m": radius,
        "radial_intervals": radial_intervals, "angular_points": angular_points,
        "sample_count": len(points), "coordinate_unit": "m", "measure_unit": "m^2",
    }
    return {**identity, "quadrature_sha256": _sha256(identity),
            "coordinates_m": [list(point) for point in points], "weights_m2": weights,
            "weight_sum_m2": math.fsum(weights)}


def rectangular_port_quadrature(
    center_m: Sequence[float], axis_xyz: Sequence[float], half_widths_uv_m: Sequence[float], *,
    u_intervals: int, v_intervals: int,
) -> dict[str, Any]:
    """Tensor-product composite Simpson quadrature for a rectangular plane."""
    center = _vector3(center_m, "port center")
    axis = _normalize(_vector3(axis_xyz, "port axis"), "port axis")
    if not isinstance(half_widths_uv_m, (list, tuple)) or len(half_widths_uv_m) != 2:
        _fail("rectangular half widths must contain two local coordinates")
    half_u = _finite_number(half_widths_uv_m[0], "port half width u")
    half_v = _finite_number(half_widths_uv_m[1], "port half width v")
    if half_u <= 0.0 or half_v <= 0.0:
        _fail("rectangular half widths must be positive")
    if (type(u_intervals) is not int or u_intervals < 2 or u_intervals % 2
            or type(v_intervals) is not int or v_intervals < 2 or v_intervals % 2):
        _fail("rectangular Simpson interval counts must be positive even integers")
    reference = (0.0, 0.0, 1.0) if abs(axis[2]) < 0.9 else (0.0, 1.0, 0.0)
    tangent_u = _normalize(_cross(reference, axis), "port tangent basis")
    tangent_v = _normalize(_cross(axis, tangent_u), "port tangent basis")
    du, dv = 2.0 * half_u / u_intervals, 2.0 * half_v / v_intervals
    points: list[tuple[float, float, float]] = []
    weights: list[float] = []
    for iu in range(u_intervals + 1):
        u = -half_u + iu * du
        wu = 1 if iu in (0, u_intervals) else (4 if iu % 2 else 2)
        for iv in range(v_intervals + 1):
            v = -half_v + iv * dv
            wv = 1 if iv in (0, v_intervals) else (4 if iv % 2 else 2)
            points.append(tuple(center[k] + u * tangent_u[k] + v * tangent_v[k]
                                for k in range(3)))
            weights.append(wu * wv * du * dv / 9.0)
    identity = {
        "profile": "w23.full3d.rectangular.tensor_simpson.v1",
        "center_m": list(center), "axis_xyz": list(axis),
        "tangent_u_xyz": list(tangent_u), "tangent_v_xyz": list(tangent_v),
        "half_widths_uv_m": [half_u, half_v],
        "radial_intervals": u_intervals, "angular_points": v_intervals,
        "u_intervals": u_intervals, "v_intervals": v_intervals,
        "sample_count": len(points), "coordinate_unit": "m", "measure_unit": "m^2",
    }
    return {**identity, "quadrature_sha256": _sha256(identity),
            "coordinates_m": [list(point) for point in points], "weights_m2": weights,
            "weight_sum_m2": math.fsum(weights)}


def build_raw_field_contract(
    *, case: Mapping[str, Any], role: str, source: Mapping[str, Any],
    plane: Mapping[str, Any], radial_intervals: int, angular_points: int,
) -> dict[str, Any]:
    """Bind one fresh vector-field extraction to a native source and local plane."""
    if role not in _VECTOR_FIELDS:
        _fail("raw-field role must name one registered signal/mode/capture/mapping source")
    if not isinstance(case, Mapping) or not isinstance(case.get("case_id"), str):
        _fail("a registered immutable full-3D case identity is required")
    required_source = ("dataset_id", "solution_id", "outer_index", "inner_index", "solnum")
    if not isinstance(source, Mapping) or any(key not in source for key in required_source):
        _fail("raw-field extraction must bind a complete native dataset/solution/index tuple")
    if (not isinstance(source["dataset_id"], str) or not source["dataset_id"]
            or not isinstance(source["solution_id"], str) or not source["solution_id"]
            or any(type(source[key]) is not int or source[key] < 1
                   for key in ("outer_index", "inner_index", "solnum"))):
        _fail("native source identity contains an invalid tag or solution index")
    if not isinstance(plane, Mapping):
        _fail("native local port frame is required")
    plane_id = plane.get("plane_id")
    expected_plane = "input_port" if role == "incident_reference" else "receiver_port"
    if plane_id != expected_plane:
        _fail("field role is bound to the wrong local native port plane")
    if (plane.get("component") != _COMPONENT or plane.get("geometry") != _GEOMETRY
            or plane.get("entity_dimension") != 2 or plane.get("coordinate_unit") != "um"):
        _fail("field plane must be a native 2-D boundary selection in comp3d/geom3d micrometre geometry")
    center_um = _vector3(plane.get("center_xyz_um"), "plane center")
    axis = _normalize(_vector3(plane.get("axis_xyz"), "plane axis"), "plane axis")
    native_sign = plane.get("native_normal_sign")
    if type(native_sign) is not int or native_sign not in {-1, 1}:
        _fail("native surface normal orientation must be read back as exactly +1 or -1")
    shape = plane.get("aperture_shape")
    frame = {"plane_id": plane_id, "component": _COMPONENT, "geometry": _GEOMETRY,
             "selection_tag": plane.get("selection_tag"), "entity_dimension": 2,
             "center_xyz_um": list(center_um), "center_xyz_m": [item * 1e-6 for item in center_um],
             "axis_xyz": list(axis), "native_normal_sign": native_sign,
             # native_normal_sign converts COMSOL's outward normal to the
             # physical +propagation axis; the quadrature formula uses that
             # forward axis directly.
             "physical_forward_normal_xyz": list(axis), "aperture_shape": shape,
             "coordinate_unit": "m", "normal_basis": "global_xyz"}
    if not isinstance(frame["selection_tag"], str) or not frame["selection_tag"]:
        _fail("local native selection tag is required")
    if shape == "circular":
        radius_um = _finite_number(plane.get("sample_radius_um"), "plane sample radius")
        if radius_um <= 0.0:
            _fail("port sample radius must be positive")
        frame.update({"sample_radius_um": radius_um, "sample_radius_m": radius_um * 1e-6})
        quadrature = circular_port_quadrature(
            frame["center_xyz_m"], axis, frame["sample_radius_m"],
            radial_intervals=radial_intervals, angular_points=angular_points)
    elif shape == "rectangle":
        raw_half_widths = plane.get("half_widths_uv_um")
        if not isinstance(raw_half_widths, (list, tuple)) or len(raw_half_widths) != 2:
            _fail("rectangular port needs two native-read-back local half widths")
        half_widths_um = [_finite_number(value, "port half width") for value in raw_half_widths]
        if min(half_widths_um) <= 0.0:
            _fail("rectangular port half widths must be positive")
        frame["half_widths_uv_um"] = half_widths_um
        frame["half_widths_uv_m"] = [value * 1e-6 for value in half_widths_um]
        quadrature = rectangular_port_quadrature(
            frame["center_xyz_m"], axis, frame["half_widths_uv_m"],
            u_intervals=radial_intervals, v_intervals=angular_points)
    else:
        _fail("native plane must bind a registered circular or rectangular aperture")
    field_names = list(_VECTOR_FIELDS[role] + _NORMAL_FIELDS)
    unit_groups = _field_unit_groups(field_names)
    identity = {"case_id": case["case_id"], "case_identity_sha256": case.get("case_identity_sha256"),
                "role": role, "source": {key: source[key] for key in required_source},
                "plane": frame, "quadrature_sha256": quadrature["quadrature_sha256"],
                "expressions": field_names, "unit_groups": unit_groups}
    return {"schema_version": 1, "contract_id": _sha256(identity), **identity,
            "quadrature": {key: quadrature[key] for key in (
                "profile", "radial_intervals", "angular_points", "sample_count",
                "quadrature_sha256", "coordinate_unit", "measure_unit") if key in quadrature},
            "field_names": field_names,
            "native_result": "NOT_RUN", "study_or_solver_invoked": False}


def build_raw_field_dispatch(
    contract: Mapping[str, Any], *, source_artifact: str, quadrature: Mapping[str, Any],
    project_id: str, model_ref: Mapping[str, Any], model_tag: str, revision: int,
    request_id: str, idempotency_key: str,
) -> dict[str, Any]:
    if not isinstance(contract, Mapping) or not isinstance(contract.get("contract_id"), str):
        _fail("a complete frozen raw-field contract is required")
    if (not isinstance(quadrature, Mapping)
            or quadrature.get("quadrature_sha256") != contract.get("quadrature_sha256")
            or not isinstance(quadrature.get("coordinates_m"), list)):
        _fail("native sampling coordinates must be reconstructed from the exact bound quadrature")
    if not isinstance(source_artifact, str) or not source_artifact.strip():
        _fail("project-registered full-3D fixture artifact is required")
    binding = _managed_route_binding(
        project_id=project_id, model_ref=model_ref, model_tag=model_tag, revision=revision,
        request_id=request_id, idempotency_key=idempotency_key)
    phase = ("bma_basis_fields" if contract.get("role") == "bma_basis_mapping_pair"
             else "raw_fields")
    return {"operation": "operation_call",
            "arguments": {"operation_id": "code.execute_java", "arguments": {
                "source_artifact": source_artifact,
                "entrypoint": "NativeW23Full3DFixture#run", "mode": "trusted",
                "arguments": {"phase": phase, "contract": dict(contract),
                              "coordinates_m": quadrature["coordinates_m"],
                              "native_result": "NOT_RUN", "study_or_solver_invoked": False}}},
            "execution": {key: binding[key] for key in
                          ("project_id", "session_id", "model_ref", "expected_revision",
                           "request_id", "idempotency_key")},
            "dispatch_scope": ("one managed paired BMA-basis field readback using three same-grid unit groups; "
                               "no Study.run or solver call" if phase == "bma_basis_fields"
                               else "one managed native Interp readback; no Study.run or solver call")}


def resolve_full3d_bma_basis_sources(
    dataset_rows: Sequence[Mapping[str, Any]],
    index_by_tag: Mapping[str, Mapping[str, Any]], *,
    producer_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind both BMA basis rows to one exact managed Solution dataset.

    ``basis_ordinal`` is assigned only from the ordered, already validated
    SolutionInfo rows produced by the one-step BMA sequence.  It is kept apart
    from Numeric Port ``PortModeNumber`` and from any dataset parameter whose
    name happens to contain ``mode``.
    """
    if (not isinstance(producer_evidence, Mapping)
            or producer_evidence.get("status") != "VERIFIED_CONTROLLED_SINGLE_BMA_PRODUCER"
            or producer_evidence.get("native_result") != "COMSOL_NATIVE_BMA_PRODUCER_RUN_READBACK"
            or producer_evidence.get("basis_ordinal_assignment")
            != "UNVERIFIED_NATIVE_FIELD_MAPPING_REQUIRED"
            or producer_evidence.get("numeric_port_mode_field_mapping") != "UNVERIFIED"):
        _fail("two exact native BMA producer SolutionInfo rows are required before field sampling")
    sequence_tag = producer_evidence.get("solver_sequence_tag")
    pairs = producer_evidence.get("eigensolution_solution_pairs")
    if (not isinstance(sequence_tag, str) or not _TAG.fullmatch(sequence_tag)
            or not isinstance(pairs, list) or len(pairs) != 2):
        _fail("producer evidence lacks the exact two-row SolutionInfo basis axis")
    normalized_pairs: list[dict[str, Any]] = []
    for pair in pairs:
        if not isinstance(pair, Mapping):
            _fail("producer SolutionInfo basis row is malformed")
        outer, inner, solnum = pair.get("outer_index"), pair.get("inner_index"), pair.get("solnum")
        if (type(outer) is not int or outer < 1 or type(inner) is not int or inner < 1
                or type(solnum) is not int or solnum < 1 or solnum != inner
                or pair.get("solver_sequence_tag") != sequence_tag):
            _fail("producer SolutionInfo row does not map to the exact BMA sequence axes")
        normalized_pairs.append({"outer_index": outer, "inner_index": inner,
                                 "solnum": solnum, "solver_sequence_tag": sequence_tag})
    if (len({row["inner_index"] for row in normalized_pairs}) != 2
            or len({row["solnum"] for row in normalized_pairs}) != 2
            or len({row["outer_index"] for row in normalized_pairs}) != 1):
        _fail("BMA basis rows must share one outer index and have distinct inner/solnum axes")

    candidates: list[Mapping[str, Any]] = []
    for row in dataset_rows:
        if not isinstance(row, Mapping):
            _fail("native result-dataset inventory contains a malformed row")
        if row.get("type_id") == "Solution" and row.get("solution") == sequence_tag:
            candidates.append(row)
    if len(candidates) != 1:
        _fail("the exact BMA solver sequence must bind exactly one native Solution dataset")
    dataset = candidates[0]
    dataset_tag = dataset.get("tag")
    if (not isinstance(dataset_tag, str) or not _TAG.fullmatch(dataset_tag)
            or (dataset.get("component") not in {None, "", _COMPONENT})
            or (dataset.get("geometry") not in {None, "", _GEOMETRY})):
        _fail("BMA result dataset identity or optional component/geometry readback is inconsistent")
    indices = index_by_tag.get(dataset_tag)
    if (not isinstance(indices, Mapping) or indices.get("dataset") != dataset_tag
            or indices.get("solution") != sequence_tag
            or indices.get("binding_complete") is not True
            or indices.get("axis_metadata_complete") is not True
            or indices.get("pair_mapping_complete") is not True
            or indices.get("parameters_complete") is not True
            or indices.get("solution_count") != 2):
        _fail("BMA Solution dataset lacks complete native SolutionInfo/index parameter readback")

    expected_outer = normalized_pairs[0]["outer_index"]
    expected_inner = {row["inner_index"] for row in normalized_pairs}
    outer_indices, inner_indices = indices.get("outer_indices"), indices.get("inner_indices")
    if (not isinstance(outer_indices, list) or len(outer_indices) != 1
            or outer_indices[0] != expected_outer
            or not isinstance(inner_indices, list) or len(inner_indices) != 2
            or set(inner_indices) != expected_inner):
        _fail("dataset index API and exact BMA SolutionInfo producer rows disagree")

    raw_solnum_pairs = indices.get("solnum_pairs")
    if not isinstance(raw_solnum_pairs, list) or len(raw_solnum_pairs) != 2:
        _fail("dataset index API omitted the exact stored-solnum mapping for both producer rows")
    indexed_pairs: dict[tuple[int, int], int] = {}
    for row in raw_solnum_pairs:
        if not isinstance(row, Mapping):
            _fail("dataset stored-solnum row is malformed")
        outer, inner, solnum = row.get("outer"), row.get("inner"), row.get("solnum")
        if (type(outer) is not int or type(inner) is not int or type(solnum) is not int
                or outer < 1 or inner < 1 or solnum < 1 or (outer, inner) in indexed_pairs):
            _fail("dataset stored-solnum mapping is invalid or duplicated")
        indexed_pairs[(outer, inner)] = solnum
    expected_solnums = {(row["outer_index"], row["inner_index"]): row["solnum"]
                        for row in normalized_pairs}
    if indexed_pairs != expected_solnums:
        _fail("dataset SolutionInfo-to-solnum mapping differs from the controlled BMA producer")

    by_pair = indices.get("parameters")
    by_pair = by_pair.get("by_pair") if isinstance(by_pair, Mapping) else None
    if not isinstance(by_pair, Mapping):
        _fail("dataset parameter readback lacks per-pair native axis provenance")
    resolved: list[dict[str, Any]] = []
    for basis_ordinal, pair in enumerate(normalized_pairs, start=1):
        key = f"{pair['outer_index']}:{pair['inner_index']}"
        parameter_row = by_pair.get(key)
        if (not isinstance(parameter_row, Mapping)
                or parameter_row.get("solnum") != pair["solnum"]):
            _fail("per-pair native dataset parameters do not bind the exact producer solnum")
        names, values, units = (parameter_row.get(name) for name in ("names", "values", "units"))
        if (not isinstance(names, list) or not isinstance(values, list) or not isinstance(units, list)
                or len(names) != len(values) or len(names) != len(units)
                or any(not isinstance(name, str) or not name for name in names)):
            _fail("dataset native parameter names/values/units are incomplete")
        resolved.append({
            "basis_ordinal": basis_ordinal,
            "basis_axis": "ordered SolutionInfo.getSolnum(outer,true) row ordinal",
            "source": {"dataset_id": dataset_tag, "solution_id": sequence_tag,
                       **pair},
            "native_parameter_readback": {"names": list(names), "values": list(values),
                                           "units": list(units)},
        })
    return {"status": "SOFTWARE_BMA_BASIS_DATASET_BINDING_VALIDATED",
            "native_result": "NOT_RUN",
            "basis_sources": resolved,
            "dataset_binding": {"dataset_id": dataset_tag, "solution_id": sequence_tag,
                                "type_id": "Solution", "source": "managed dataset.list + dataset.solution_indices"},
            "numeric_port_mode_number": "NOT_INFERRED_FROM_BASIS_ORDINAL",
            "basis_ordinal_mapping": "UNVERIFIED_NATIVE_FIELD_MAPPING_REQUIRED",
            "field_mapping_status": "UNVERIFIED"}


def build_full3d_bma_basis_mapping_contracts(
    *, case: Mapping[str, Any], preparation: Mapping[str, Any],
    producer_evidence: Mapping[str, Any], basis_binding: Mapping[str, Any],
    plane: Mapping[str, Any], radial_intervals: int, angular_points: int,
) -> list[dict[str, Any]]:
    """Create two same-grid generic/Port-field contracts, one per BMA row."""
    port = preparation.get("receiver_port") if isinstance(preparation, Mapping) else None
    if (not isinstance(preparation, Mapping)
            or preparation.get("status") != "ISOLATED_BMA_SEQUENCE_PREPARED_NOT_SOLVED"
            or preparation.get("field_mapping_status") != "UNVERIFIED"
            or not isinstance(port, Mapping)
            or port.get("feature_tag") != "portOut3d"
            or port.get("feature_type") != "Port"
            or port.get("port_type") != "Numeric"
            or port.get("port_name") != "2"
            or port.get("port_mode_number_readback") != "1"
            or port.get("selection_tag") != "sel3dOutputPort"):
        _fail("validated actual Numeric Port 2/PortModeNumber 1 preparation readback is required")
    if (not isinstance(producer_evidence, Mapping)
            or producer_evidence.get("status") != "VERIFIED_CONTROLLED_SINGLE_BMA_PRODUCER"
            or producer_evidence.get("solver_sequence_tag")
            != preparation.get("solver_sequence", {}).get("tag")
            or producer_evidence.get("field_mapping_status") not in {None, "UNVERIFIED"}
            or not isinstance(basis_binding, Mapping)
            or basis_binding.get("status") != "SOFTWARE_BMA_BASIS_DATASET_BINDING_VALIDATED"
            or basis_binding.get("field_mapping_status") != "UNVERIFIED"):
        _fail("exact producer and independently resolved two-row dataset binding are required")
    if not isinstance(plane, Mapping) or plane.get("selection_tag") != port.get("selection_tag"):
        _fail("paired fields must use the actual output Port selection")
    raw_sources = basis_binding.get("basis_sources")
    if not isinstance(raw_sources, list) or len(raw_sources) != 2:
        _fail("exactly two validated BMA basis source tuples are required")
    contracts: list[dict[str, Any]] = []
    for expected_ordinal, basis_source in enumerate(raw_sources, start=1):
        if (not isinstance(basis_source, Mapping)
                or basis_source.get("basis_ordinal") != expected_ordinal
                or basis_source.get("basis_axis") != "ordered SolutionInfo.getSolnum(outer,true) row ordinal"
                or not isinstance(basis_source.get("source"), Mapping)):
            _fail("BMA basis source ordering or separate basis-axis identity is malformed")
        source = basis_source["source"]
        contract = build_raw_field_contract(
            case=case, role="bma_basis_mapping_pair", source=source,
            plane=plane, radial_intervals=radial_intervals, angular_points=angular_points)
        semantic_identity = {
            "provenance_schema": "w23.full3d.numeric_port_bma_basis_pair.v1",
            "basis_axis": {"ordinal": expected_ordinal, "axis": basis_source["basis_axis"],
                           "outer_index": source["outer_index"],
                           "inner_index": source["inner_index"], "solnum": source["solnum"],
                           "solution_id": source["solution_id"],
                           "solver_sequence_tag": producer_evidence["solver_sequence_tag"],
                           "native_parameter_readback": dict(basis_source["native_parameter_readback"])},
            "port_mode_axis": {"feature_tag": port["feature_tag"],
                               "feature_type": port["feature_type"],
                               "port_type": port["port_type"],
                               "port_name": port["port_name"],
                               "port_mode_number_readback": port["port_mode_number_readback"],
                               "selection_tag": port["selection_tag"],
                               "boundary_ids": list(port["boundary_ids"]),
                               "field_suffix_semantics": dict(COMSOL_PORT_MODE_FIELD_KB_EVIDENCE)},
            "field_groups": {
                "generic_bma_eigensolution": list(_VECTOR_FIELDS["signal"]),
                "configured_numeric_port_mode_field": list(_VECTOR_FIELDS["reference_mode"]),
                "shared_surface_normal": list(_NORMAL_FIELDS),
            },
            "unit_groups": {key: dict(value) for key, value in _BMA_PAIR_UNIT_GROUPS.items()},
            "unit_readback_policy": {
                "source": dict(COMSOL_INTERP_UNIT_KB_EVIDENCE),
                "raw_real_imag_preserved": True,
                "native_unit_property_readback_required": True,
            },
            "mapping_policy": dict(BMA_FIELD_MAPPING_POLICY),
            "basis_ordinal_mapping": "UNVERIFIED_NATIVE_FIELD_MAPPING_REQUIRED",
            "numeric_port_mode_field_mapping": "UNVERIFIED",
        }
        contract.update(semantic_identity)
        identity = {key: value for key, value in contract.items()
                    if key not in {"contract_id", "native_result", "study_or_solver_invoked"}}
        contract["contract_id"] = _sha256(identity)
        contracts.append(contract)
    if (contracts[0]["quadrature_sha256"] != contracts[1]["quadrature_sha256"]
            or contracts[0]["plane"] != contracts[1]["plane"]
            or contracts[0]["field_names"] != contracts[1]["field_names"]):
        _fail("both BMA basis rows must share one frozen coordinate/weight/normal field contract")
    return contracts


def build_full3d_bma_basis_mapping_dispatch(
    contract: Mapping[str, Any], *, source_artifact: str,
    quadrature: Mapping[str, Any], project_id: str,
    model_ref: Mapping[str, Any], model_tag: str, revision: int,
    request_id: str, idempotency_key: str,
) -> dict[str, Any]:
    """Dispatch one paired field sample for exactly one validated BMA basis row."""
    if (not isinstance(contract, Mapping)
            or contract.get("role") != "bma_basis_mapping_pair"
            or contract.get("provenance_schema") != "w23.full3d.numeric_port_bma_basis_pair.v1"
            or contract.get("field_names") != list(_VECTOR_FIELDS["bma_basis_mapping_pair"] + _NORMAL_FIELDS)
            or contract.get("field_groups") != {
                "generic_bma_eigensolution": list(_VECTOR_FIELDS["signal"]),
                "configured_numeric_port_mode_field": list(_VECTOR_FIELDS["reference_mode"]),
                "shared_surface_normal": list(_NORMAL_FIELDS)}
            or contract.get("unit_groups") != _BMA_PAIR_UNIT_GROUPS
            or not isinstance(contract.get("unit_readback_policy"), Mapping)
            or contract["unit_readback_policy"].get("source") != COMSOL_INTERP_UNIT_KB_EVIDENCE
            or contract.get("mapping_policy") != BMA_FIELD_MAPPING_POLICY
            or contract.get("basis_ordinal_mapping") != "UNVERIFIED_NATIVE_FIELD_MAPPING_REQUIRED"
            or contract.get("numeric_port_mode_field_mapping") != "UNVERIFIED"):
        _fail("paired BMA field dispatch requires the exact versioned, still-unverified contract")
    identity = {key: value for key, value in contract.items()
                if key not in {"contract_id", "native_result", "study_or_solver_invoked"}}
    if contract.get("contract_id") != _sha256(identity):
        _fail("paired BMA field contract identity/hash is inconsistent")
    basis_axis = contract.get("basis_axis")
    port_axis = contract.get("port_mode_axis")
    source = contract.get("source")
    if (not isinstance(basis_axis, Mapping) or basis_axis.get("ordinal") not in {1, 2}
            or not isinstance(port_axis, Mapping) or port_axis.get("port_name") != "2"
            or port_axis.get("port_mode_number_readback") != "1"
            or not isinstance(source, Mapping)
            or basis_axis.get("inner_index") != source.get("inner_index")
            or basis_axis.get("solnum") != source.get("solnum")
            or basis_axis.get("outer_index") != source.get("outer_index")):
        _fail("paired BMA field contract mixes the Numeric Port mode axis and basis solution axis")
    request = build_raw_field_dispatch(
        contract, source_artifact=source_artifact, quadrature=quadrature,
        project_id=project_id, model_ref=model_ref, model_tag=model_tag,
        revision=revision, request_id=request_id, idempotency_key=idempotency_key)
    return request


def resolve_full3d_bma_receiver_plane(
    apply_readback: Mapping[str, Any], *, preparation: Mapping[str, Any],
    baseline_case: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive the field-sampling plane only from matching live fixture readbacks."""
    port = preparation.get("receiver_port") if isinstance(preparation, Mapping) else None
    section = apply_readback.get("receiver_port_section") if isinstance(apply_readback, Mapping) else None
    transform = apply_readback.get("receiver_transform") if isinstance(apply_readback, Mapping) else None
    if (not isinstance(port, Mapping) or port.get("feature_tag") != "portOut3d"
            or port.get("feature_type") != "Port" or port.get("port_type") != "Numeric"
            or port.get("port_name") != "2" or port.get("port_mode_number_readback") != "1"
            or port.get("selection_tag") != "sel3dOutputPort"
            or port.get("entity_dimension") != 2
            or not isinstance(section, Mapping)
            or section.get("evidence_scope") != "COMSOL_NATIVE_GEOMETRY_READBACK"
            or section.get("tag") != port.get("selection_tag")
            or section.get("selection_type") != "Cylinder"
            or section.get("entity_dimension") != 2
            or section.get("coordinate_unit") != "um"
            or section.get("normal_basis") != "global_xyz"
            or not isinstance(transform, Mapping)
            or not isinstance(baseline_case, Mapping)
            or baseline_case.get("factor") != "baseline"):
        _fail("baseline receiver plane must come from the actual Port-2 preparation and geometry readbacks")
    boundary_ids = port.get("boundary_ids")
    entity_ids = section.get("entity_ids")
    if (not isinstance(boundary_ids, list) or not boundary_ids
            or any(type(item) is not int or item < 1 for item in boundary_ids)
            or len(set(boundary_ids)) != len(boundary_ids)
            or not isinstance(entity_ids, list) or not entity_ids
            or any(type(item) is not int or item < 1 for item in entity_ids)
            or len(set(entity_ids)) != len(entity_ids)
            or set(boundary_ids) != set(entity_ids)):
        _fail("sampled plane boundary IDs must exactly equal the prepared Numeric Port selection")
    axis = _normalize(_vector3(section.get("selection_axis_xyz"), "native receiver selection axis"),
                      "native receiver selection axis")
    center = _vector3(section.get("selection_center_um"), "native receiver selection center")
    expected_axis = _normalize(_vector3(transform.get("axis_xyz"), "receiver transform axis"),
                                "receiver transform axis")
    expected_center = _vector3(transform.get("center_xyz_um"), "receiver transform center")
    case_transform = baseline_case.get("receiver_transform")
    if not isinstance(case_transform, Mapping):
        _fail("baseline registered case lacks its expected receiver transform")
    case_axis = _normalize(_vector3(case_transform.get("axis_xyz"), "baseline receiver axis"),
                           "baseline receiver axis")
    case_center = _vector3(case_transform.get("center_xyz_um"), "baseline receiver center")
    if any(abs(axis[index] - value) > 1e-10 for index, value in enumerate(expected_axis)) \
            or any(abs(center[index] - value) > 1e-8 for index, value in enumerate(expected_center)) \
            or any(abs(axis[index] - value) > 1e-10 for index, value in enumerate(case_axis)) \
            or any(abs(center[index] - value) > 1e-8 for index, value in enumerate(case_center)):
        _fail("native receiver selection frame differs from both managed transform and registered baseline")
    radius = _finite_number(section.get("nominal_aperture_radius_um"), "native port aperture radius")
    selection_radius = _finite_number(section.get("selection_radius_um"), "native selection radius")
    margin = _finite_number(section.get("selection_margin_um"), "native selection margin")
    area = _finite_number(section.get("area_um2"), "native port area")
    expected_area = _finite_number(section.get("expected_circle_area_um2"), "native expected port area")
    area_error = _finite_number(section.get("area_relative_error"), "native port area error")
    if (radius <= 0.0 or margin < 0.0 or area <= 0.0 or expected_area <= 0.0
            or abs(selection_radius - (radius + margin)) > 1e-10
            or not math.isclose(expected_area, math.pi * radius * radius, rel_tol=1e-12, abs_tol=1e-12)
            or area_error < 0.0 or area_error > 0.03):
        _fail("native circular port measure/radius readback is inconsistent with the registered aperture gate")
    faces = section.get("faces")
    if (not isinstance(faces, list) or len(faces) != len(entity_ids)
            or any(not isinstance(face, Mapping) for face in faces)):
        _fail("native selected-face outward-normal readback is incomplete")
    face_by_id: dict[int, Mapping[str, Any]] = {}
    for face in faces:
        boundary_id = face.get("boundary_id")
        if type(boundary_id) is not int or boundary_id not in entity_ids or boundary_id in face_by_id:
            _fail("native outward-normal readback has foreign or duplicate boundary IDs")
        normal = _normalize(_vector3(face.get("unit_normal_xyz"), "native face outward normal"),
                            "native face outward normal")
        observed_dot = _finite_number(face.get("axis_dot"), "native face/axis dot product")
        expected_dot = sum(normal[index] * axis[index] for index in range(3))
        if (not math.isclose(observed_dot, expected_dot, rel_tol=0.0, abs_tol=1e-10)
                or abs(abs(observed_dot) - 1.0) > 1e-5):
            _fail("native face normal does not prove a consistent actual orientation relative to the receiver frame")
        face_by_id[boundary_id] = face
    if set(face_by_id) != set(entity_ids):
        _fail("native outward-normal readback does not cover every selected Port face")
    signs = {1 if float(face["axis_dot"]) > 0.0 else -1 for face in face_by_id.values()}
    native_sign = section.get("native_face_oriented_axis_sign")
    if len(signs) != 1 or type(native_sign) is not int or signs != {native_sign}:
        _fail("reported native face orientation sign disagrees with per-face direction readback")
    return {"plane_id": "receiver_port", "component": _COMPONENT, "geometry": _GEOMETRY,
            "selection_tag": port["selection_tag"], "entity_dimension": 2,
            "boundary_ids": list(entity_ids), "center_xyz_um": list(center),
            "axis_xyz": list(axis), "native_normal_sign": native_sign,
            "aperture_shape": "circular", "sample_radius_um": radius,
            "coordinate_unit": "um", "normal_basis": "global_xyz",
            "area_um2": area, "area_relative_error": area_error,
            "native_outward_normal_evidence": {
                "source": "same-selection COMSOL GeomSequence.faceNormal readback",
                "observed_axis_dot_by_boundary": {
                    str(boundary_id): face_by_id[boundary_id]["axis_dot"]
                    for boundary_id in sorted(face_by_id)},
                "native_face_oriented_axis_sign": native_sign,
                "requested_propagation_axis_preserved": True,
            }}


def validate_full3d_bma_mapping_route_result(
    request: Mapping[str, Any], route_result: Mapping[str, Any], *,
    expected_revision_delta: int, max_execution_timeout_s: float,
) -> dict[str, Any]:
    """Authenticate one exact public request/operation/job/revision chain."""
    if (not isinstance(request, Mapping) or request.get("operation") != "operation_call"
            or not isinstance(route_result, Mapping) or route_result.get("outcome") != "SUCCEEDED"
            or route_result.get("retry_forbidden") is not True
            or not isinstance(route_result.get("job_id"), str) or not route_result.get("job_id")
            or type(expected_revision_delta) is not int or expected_revision_delta not in {0, 1}
            or isinstance(max_execution_timeout_s, bool)
            or not isinstance(max_execution_timeout_s, (int, float))
            or not math.isfinite(float(max_execution_timeout_s)) or max_execution_timeout_s <= 0):
        _fail("managed paired-field route lacks one successful, non-replayed public job")
    logical_execution = request.get("execution")
    nested = request.get("arguments")
    operation_id = nested.get("operation_id") if isinstance(nested, Mapping) else None
    operation_args = nested.get("arguments") if isinstance(nested, Mapping) else None
    if (not isinstance(logical_execution, Mapping)
            or not isinstance(operation_id, str) or not operation_id
            or not isinstance(operation_args, Mapping)
            or not isinstance(logical_execution.get("request_id"), str)
            or not logical_execution.get("request_id")
            or not isinstance(logical_execution.get("idempotency_key"), str)
            or not logical_execution.get("idempotency_key")
            or type(logical_execution.get("expected_revision")) is not int
            or logical_execution.get("expected_revision") < 0):
        _fail("managed paired-field logical request identity is incomplete")
    submitted = route_result.get("submitted_request")
    submitted_execution = submitted.get("execution") if isinstance(submitted, Mapping) else None
    if not isinstance(submitted, Mapping) or not isinstance(submitted_execution, Mapping):
        _fail("public route omitted its exact bounded request")
    timeout_names = ("execution_timeout_s", "queue_timeout_s", "rpc_timeout_s")
    timeouts = {name: submitted_execution.get(name) for name in timeout_names}
    if (submitted.get("operation") != "operation_call"
            or submitted.get("arguments") != nested
            or any(submitted_execution.get(name) != logical_execution.get(name)
                   for name in ("project_id", "session_id", "model_ref", "expected_revision",
                                "request_id", "idempotency_key"))
            or set(submitted_execution) != set(logical_execution) | set(timeout_names)
            or any(isinstance(value, bool) or not isinstance(value, (int, float))
                   or not math.isfinite(float(value)) or value <= 0.0
                   for value in timeouts.values())
            or timeouts["execution_timeout_s"] > max_execution_timeout_s
            or timeouts["queue_timeout_s"] != min(60.0, timeouts["execution_timeout_s"])
            or timeouts["rpc_timeout_s"] != min(30.0, timeouts["execution_timeout_s"])):
        _fail("bounded public route changed the frozen managed request or exceeded its time cap")
    if dict(submitted) != {**dict(request),
            "execution": {**dict(logical_execution), **timeouts}}:
        _fail("submitted managed request does not exactly equal its bounded logical request")
    try:
        from comsol_mcp._execution_contract import canonical_request_hash

        expected_request_hash = canonical_request_hash(
            operation_id, operation_args, logical_execution["model_ref"],
            logical_execution["expected_revision"],
            project_id=logical_execution["project_id"],
            session_id=logical_execution["session_id"],
            queue_timeout_s=timeouts["queue_timeout_s"],
            execution_timeout_s=timeouts["execution_timeout_s"],
            no_progress_warning_s=submitted_execution.get("no_progress_warning_s"))
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        raise Full3DScienceError("canonical public mapping request hash could not be recomputed") from exc
    if not isinstance(expected_request_hash, str) or re.fullmatch(r"[0-9a-f]{64}", expected_request_hash) is None:
        _fail("canonical public mapping request hash is malformed")
    job_id = route_result["job_id"]
    initial = route_result.get("dispatch_response")
    initial_execution = initial.get("execution") if isinstance(initial, Mapping) else None
    waits = route_result.get("job_wait_responses")
    operation_response = route_result.get("response")
    response_execution = operation_response.get("execution") if isinstance(operation_response, Mapping) else None
    if (not isinstance(initial, Mapping) or initial.get("success") is not True
            or not isinstance(initial_execution, Mapping)
            or _managed_job_id(initial) != job_id
            or initial_execution.get("request_id") != logical_execution["request_id"]
            or initial_execution.get("idempotency_key") != logical_execution["idempotency_key"]
            or not isinstance(initial_execution.get("operation_id"), str)
            or not initial_execution.get("operation_id")
            or initial_execution.get("job_id") != job_id
            or initial_execution.get("request_hash") != expected_request_hash
            or not isinstance(waits, list) or not waits
            or not isinstance(operation_response, Mapping)
            or operation_response.get("success") is not True
            or not isinstance(response_execution, Mapping)):
        _fail("initial managed dispatch does not bind the exact request, operation and job")
    terminal_wait = waits[-1]
    terminal_job = terminal_wait.get("data") if isinstance(terminal_wait, Mapping) else None
    stored_operation = terminal_job.get("operation") if isinstance(terminal_job, Mapping) else None
    operation_instance_id = initial_execution["operation_id"]
    identities = {"request_id": logical_execution["request_id"],
                  "idempotency_key": logical_execution["idempotency_key"],
                  "operation_id": operation_instance_id, "job_id": job_id,
                  "request_hash": expected_request_hash}
    stored_metadata = stored_operation.get("metadata") if isinstance(stored_operation, Mapping) else None
    stored_execution = stored_metadata.get("execution") if isinstance(stored_metadata, Mapping) else None
    stored_arguments = stored_metadata.get("arguments") if isinstance(stored_metadata, Mapping) else None
    terminal_job_metadata = terminal_job.get("metadata") if isinstance(terminal_job, Mapping) else None
    if (not isinstance(terminal_wait, Mapping) or terminal_wait.get("success") is not True
            or not isinstance(terminal_job, Mapping) or terminal_job.get("job_id") != job_id
            or terminal_job.get("status") != "SUCCEEDED"
            or terminal_job.get("operation_id") != operation_instance_id
            or terminal_job.get("result") != dict(operation_response)
            or not isinstance(stored_operation, Mapping)
            or stored_operation.get("operation") != "operation_call"
            or any(stored_operation.get(name) != value for name, value in identities.items()
                   if name != "job_id")
            or not isinstance(stored_metadata, Mapping)
            or stored_metadata.get("operation") != "operation_call"
            or stored_arguments != dict(nested)
            or not isinstance(stored_execution, Mapping)
            or any(stored_execution.get(name) != logical_execution.get(name)
                   for name in ("project_id", "session_id", "model_ref", "expected_revision",
                                "request_id", "idempotency_key"))
            or any(stored_execution.get(name) != timeouts[name] for name in timeout_names)
            or not isinstance(terminal_job_metadata, Mapping)
            or terminal_job_metadata.get("operation") != "operation_call"
            or terminal_job_metadata.get("arguments") != dict(nested)
            or terminal_job_metadata.get("execution") != dict(stored_execution)
            or any(response_execution.get(name) != value for name, value in identities.items())
            or response_execution.get("session_id") != logical_execution.get("session_id")
            or response_execution.get("model_ref") != logical_execution.get("model_ref")
            or response_execution.get("revision") != logical_execution["expected_revision"] + expected_revision_delta):
        _fail("terminal managed result/job is not bound to the original request and legal revision transition")
    return {"status": "VERIFIED_PUBLIC_MAPPING_ROUTE_BINDING",
            "operation_type": operation_id, "request_id": logical_execution["request_id"],
            "idempotency_key": logical_execution["idempotency_key"],
            "request_hash": expected_request_hash, "operation_instance_id": identities["operation_id"],
            "job_id": job_id, "revision_before": logical_execution["expected_revision"],
            "revision_after": response_execution["revision"],
            "revision_delta": expected_revision_delta, "retry_forbidden": True}


def validate_full3d_sampling_cohort_revision_chain(
    route_records: Sequence[Mapping[str, Any]], sample_records: Sequence[Mapping[str, Any]], *,
    project_id: str, model_ref: Mapping[str, Any], start_revision: int,
    source_artifact: str, staged_source_proof: Mapping[str, Any],
    execution_owner: Mapping[str, Any], producer_evidence: Mapping[str, Any],
    producer_route_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the narrow no-solve route segment that samples one stored solution.

    The result may support an offline immutable-solution cohort only after the
    actual public route envelopes, task-owned process/session identities,
    frozen source bytes, native before/after source snapshots and temporary
    Interp cleanup all agree. It is not a full-model immutability proof and
    does not change the public routes' live ModelRef/revision requirements.
    """
    if (not isinstance(route_records, Sequence) or isinstance(route_records, (str, bytes))
            or len(route_records) != 4 or not isinstance(sample_records, Sequence)
            or isinstance(sample_records, (str, bytes)) or len(sample_records) != 2
            or not isinstance(project_id, str) or not project_id.strip()
            or not isinstance(model_ref, Mapping) or set(model_ref) != _MODEL_REF_KEYS
            or type(start_revision) is not int or start_revision < 0):
        _fail("one exact dataset-list/index plus two field-sampling route records are required")
    producer_pairs = producer_evidence.get("eigensolution_solution_pairs") \
        if isinstance(producer_evidence, Mapping) else None
    if (not isinstance(producer_evidence, Mapping)
            or producer_evidence.get("status") != "VERIFIED_CONTROLLED_SINGLE_BMA_PRODUCER"
            or producer_evidence.get("managed_operation_id") != "code.execute_java"
            or producer_evidence.get("managed_model_revision_after") != start_revision
            or not isinstance(producer_evidence.get("managed_request_id"), str)
            or not isinstance(producer_evidence.get("managed_idempotency_key"), str)
            or not isinstance(producer_evidence.get("managed_operation_instance_id"), str)
            or not isinstance(producer_evidence.get("managed_job_id"), str)
            or not isinstance(producer_evidence.get("managed_request_hash"), str)
            or re.fullmatch(r"[0-9a-f]{64}", producer_evidence["managed_request_hash"]) is None
            or not isinstance(producer_pairs, list) or len(producer_pairs) != 2):
        _fail("paired field cohort does not continue the exact verified two-row BMA producer revision")
    if not isinstance(producer_route_evidence, Mapping):
        _fail("source-cohort chain omits the original producer request, route, and Java readback")
    preparation = producer_route_evidence.get("preparation")
    producer_request = producer_route_evidence.get("request")
    producer_route = producer_route_evidence.get("route_result")
    producer_readback = producer_route_evidence.get("readback")
    if (not isinstance(preparation, Mapping) or not isinstance(producer_request, Mapping)
            or not isinstance(producer_route, Mapping) or not isinstance(producer_readback, Mapping)):
        _fail("original producer route evidence is incomplete")
    reparsed_producer = validate_full3d_bma_probe_run_readback(
        producer_readback, preparation=preparation, project_id=project_id,
        model_tag=model_ref.get("model_tag"), model_ref=model_ref,
        run_request=producer_request, route_result=producer_route)
    if dict(reparsed_producer) != dict(producer_evidence):
        _fail("producer summary is detached from its original terminal Java response")
    expected_labels = ("bma_basis_dataset_list", "bma_basis_dataset_solution_indices",
                       "bma_basis_fields_ordinal1", "bma_basis_fields_ordinal2")
    expected_deltas = (0, 0, 1, 1)
    expected_cap = (90, 90, 180, 180)
    if tuple(row.get("label") if isinstance(row, Mapping) else None for row in route_records) != expected_labels:
        _fail("source-cohort route segment contains a missing, reordered, or foreign managed operation")

    source_hash = staged_source_proof.get("sha256") if isinstance(staged_source_proof, Mapping) else None
    source_path = staged_source_proof.get("path") if isinstance(staged_source_proof, Mapping) else None
    source_workspace = staged_source_proof.get("workspace") if isinstance(staged_source_proof, Mapping) else None
    if (not isinstance(staged_source_proof, Mapping)
            or staged_source_proof.get("status") != "PROJECT_LOCAL_SOURCE_STAGED"
            or staged_source_proof.get("source_artifact") != source_artifact
            or not isinstance(source_hash, str) or re.fullmatch(r"[0-9a-f]{64}", source_hash) is None
            or not isinstance(source_path, str) or not isinstance(source_workspace, str)):
        _fail("source-cohort segment lacks its exact staged fixture and frozen source hash")
    try:
        staged_path = Path(source_path)
        workspace_path = Path(source_workspace).resolve(strict=True)
        if (staged_path.is_symlink() or staged_path.resolve(strict=True).parent != workspace_path
                or staged_path.name != source_artifact or not staged_path.is_file()
                or hashlib.sha256(staged_path.read_bytes()).hexdigest() != source_hash):
            _fail("project-local Java source no longer matches its frozen staged hash")
    except Full3DScienceError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise Full3DScienceError("project-local frozen Java source cannot be revalidated") from exc

    owner = execution_owner
    server = owner.get("server_process_identity") if isinstance(owner, Mapping) else None
    listener = owner.get("listener") if isinstance(owner, Mapping) else None
    session = owner.get("session_identity") if isinstance(owner, Mapping) else None
    owner_model_ref = owner.get("model_ref") if isinstance(owner, Mapping) else None
    pid = server.get("pid") if isinstance(server, Mapping) else None
    birth = server.get("start_epoch_ms") if isinstance(server, Mapping) else None
    port = listener.get("port") if isinstance(listener, Mapping) else None
    endpoint = listener.get("endpoint") if isinstance(listener, Mapping) else None
    if (not isinstance(owner, Mapping) or owner.get("candidate_owned_server") is not True
            or owner.get("gui_attached") is not False or owner.get("project_id") != project_id
            or not isinstance(server, Mapping) or type(pid) is not int or pid <= 1
            or type(birth) is not int or birth <= 0
            or not isinstance(listener, Mapping)
            or listener.get("status") != "LOOPBACK_LISTENER_VERIFIED_BEFORE_WORKER"
            or type(port) is not int or not 1 <= port <= 65535
            or endpoint != f"127.0.0.1:{port}" or listener.get("pid") != pid
            or not isinstance(session, Mapping) or not isinstance(owner_model_ref, Mapping)
            or session.get("project_id") != project_id
            or session.get("endpoint") not in (endpoint, {"host": "127.0.0.1", "port": port})
            or not isinstance(session.get("session_id"), str) or not session["session_id"]
            or not isinstance(session.get("server_instance_id"), str)
            or not isinstance(session.get("worker_instance_id"), str)
            or type(session.get("worker_epoch")) is not int or session["worker_epoch"] < 1
            or dict(owner_model_ref) != dict(model_ref)
            or model_ref.get("session_id") != session.get("session_id")
            or model_ref.get("server_instance_id") != session.get("server_instance_id")):
        _fail("source-cohort segment lacks one private owned server, one connected Worker, and the exact ModelRef")

    validated_routes = []
    next_revision = start_revision
    route_by_label: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(route_records):
        label = expected_labels[index]
        request = row.get("request")
        route_result = row.get("route_result")
        cap = row.get("max_execution_timeout_s")
        if cap != expected_cap[index] or not isinstance(request, Mapping):
            _fail(f"{label} does not preserve its approved operation wait cap/request")
        execution = request.get("execution")
        if (not isinstance(execution, Mapping) or execution.get("project_id") != project_id
                or execution.get("model_ref") != dict(model_ref)
                or execution.get("expected_revision") != next_revision):
            _fail(f"{label} does not continue the exact project/Worker/ModelRef revision chain")
        operation = request.get("arguments")
        operation_id = operation.get("operation_id") if isinstance(operation, Mapping) else None
        operation_args = operation.get("arguments") if isinstance(operation, Mapping) else None
        if index == 0:
            valid_operation = operation_id == "dataset.list"
        elif index == 1:
            valid_operation = operation_id == "dataset.solution_indices"
        else:
            user_args = operation_args.get("arguments") if isinstance(operation_args, Mapping) else None
            expected_ordinal = index - 1
            valid_operation = (operation_id == "code.execute_java"
                and isinstance(operation_args, Mapping)
                and operation_args.get("source_artifact") == source_artifact
                and operation_args.get("entrypoint") == "NativeW23Full3DFixture#run"
                and operation_args.get("mode") == "trusted"
                and isinstance(user_args, Mapping)
                and user_args.get("phase") == "bma_basis_fields"
                and user_args.get("study_or_solver_invoked") is False
                and isinstance(user_args.get("contract"), Mapping)
                and user_args["contract"].get("basis_axis", {}).get("ordinal") == expected_ordinal)
        if not valid_operation:
            _fail(f"{label} operation is outside the read-only dataset/paired-field cohort allowlist")
        binding = validate_full3d_bma_mapping_route_result(
            request, route_result, expected_revision_delta=expected_deltas[index],
            max_execution_timeout_s=expected_cap[index])
        if binding.get("revision_before") != next_revision:
            _fail(f"{label} public route has a discontinuous before revision")
        terminal_response = route_result.get("response")
        if not isinstance(terminal_response, Mapping) or terminal_response.get("success") is not True:
            _fail(f"{label} omitted its successful terminal response body")
        terminal_data = terminal_response.get("data")
        if index < 2:
            route_readback = row.get("readback")
            if (not isinstance(terminal_data, Mapping) or not isinstance(route_readback, Mapping)
                    or dict(route_readback) != dict(terminal_data)):
                _fail(f"{label} cached readback differs from the exact terminal public response data")
        else:
            actual_readback = _java_action_readback(terminal_response, label)
            route_readback = row.get("readback")
            if not isinstance(route_readback, Mapping) or actual_readback != dict(route_readback):
                _fail(f"{label} cached Java readback differs from the exact terminal public Worker response")
        next_revision = binding["revision_after"]
        validated_routes.append({"label": label, **binding})
        route_by_label[label] = dict(row)

    dataset_listing = route_by_label["bma_basis_dataset_list"].get("readback")
    dataset_indices = route_by_label["bma_basis_dataset_solution_indices"].get("readback")
    dataset_rows = dataset_listing.get("datasets") if isinstance(dataset_listing, Mapping) else None
    dataset_tag = dataset_indices.get("dataset") if isinstance(dataset_indices, Mapping) else None
    if (not isinstance(dataset_rows, list) or not isinstance(dataset_indices, Mapping)
            or not isinstance(dataset_tag, str) or not dataset_tag
            or dataset_indices.get("solution") != producer_evidence.get("solver_sequence_tag")):
        _fail("terminal dataset/index readbacks do not expose one complete producer-bound solution dataset")
    dataset_binding = resolve_full3d_bma_basis_sources(
        dataset_rows, {dataset_tag: dataset_indices}, producer_evidence=producer_evidence)
    expected_basis_sources = dataset_binding.get("basis_sources")
    if (dataset_binding.get("native_result") != "NOT_RUN"
            or not isinstance(expected_basis_sources, list) or len(expected_basis_sources) != 2):
        _fail("terminal dataset/index readbacks do not resolve the exact two producer SolutionInfo rows")

    snapshots = []
    sample_tuples = []
    for index, sample in enumerate(sample_records, start=1):
        label = f"bma_basis_fields_ordinal{index}"
        if not isinstance(sample, Mapping) or sample.get("label") != label:
            _fail("paired native sample labels do not bind the two ordered basis rows")
        route_row = route_by_label[label]
        raw_request_args = route_row["request"]["arguments"]["arguments"]["arguments"]
        contract = raw_request_args.get("contract")
        raw = sample.get("readback")
        quadrature = sample.get("quadrature")
        route_readback = route_row.get("readback")
        if (not isinstance(contract, Mapping) or not isinstance(raw, Mapping)
                or not isinstance(quadrature, Mapping)
                or not isinstance(route_readback, Mapping)
                or dict(raw) != dict(route_readback)
                or raw_request_args.get("contract") != sample.get("contract")
                or raw_request_args.get("coordinates_m") != quadrature.get("coordinates_m")):
            _fail("paired sample readback, contract, or coordinates are detached from the exact public route")
        _reconstruct_contract_quadrature(contract, quadrature)
        expected_basis = expected_basis_sources[index - 1]
        basis_axis = contract.get("basis_axis")
        expected_source = dict(expected_basis["source"])
        expected_source.pop("solver_sequence_tag", None)
        if (contract.get("source") != expected_source
                or not isinstance(basis_axis, Mapping)
                or basis_axis.get("ordinal") != index
                or basis_axis.get("axis") != expected_basis.get("basis_axis")
                or any(basis_axis.get(name) != expected_basis["source"].get(source_name)
                       for name, source_name in (("outer_index", "outer_index"),
                                                 ("inner_index", "inner_index"),
                                                 ("solnum", "solnum"),
                                                 ("solution_id", "solution_id"),
                                                 ("solver_sequence_tag", "solver_sequence_tag")))
                or basis_axis.get("native_parameter_readback")
                != expected_basis.get("native_parameter_readback")):
            _fail("field sample contract is detached from terminal dataset/index and producer readbacks")
        validate_native_field_readback(contract, raw, expected_quadrature=quadrature)
        cohort = raw.get("source_cohort")
        snapshot = cohort.get("before") if isinstance(cohort, Mapping) else None
        if not isinstance(snapshot, Mapping):
            _fail("paired sample lacks its native before/after stored-solution snapshot")
        snapshots.append(snapshot)
        source = contract.get("source")
        selected = snapshot.get("stored_solution", {}).get("selected_tuple") \
            if isinstance(snapshot.get("stored_solution"), Mapping) else None
        if (not isinstance(source, Mapping) or not isinstance(selected, Mapping)
                or any(source.get(key) != selected.get(key)
                       for key in ("outer_index", "inner_index", "solnum"))
                or source.get("solution_id") != selected.get("solver_sequence_tag")
                or selected.get("solver_sequence_tag") != source.get("solution_id")
                or snapshot.get("dataset", {}).get("tag") != source.get("dataset_id")):
            _fail("native paired sample tuple does not match its dispatched SolutionInfo source identity")
        sample_tuples.append((source["dataset_id"], source["solution_id"], selected["outer_index"],
                              selected["inner_index"], selected["solnum"]))
    if sample_tuples[0][0:3] != sample_tuples[1][0:3] or sample_tuples[0] == sample_tuples[1] \
            or sample_tuples[0][3] == sample_tuples[1][3] or sample_tuples[0][4] == sample_tuples[1][4]:
        _fail("paired BMA samples must share one solution/dataset/outer tuple and have distinct inner/solnum rows")
    expected_producer_pairs = sorted(
        (row.get("outer_index"), row.get("inner_index"), row.get("solnum"), row.get("solver_sequence_tag"))
        for row in producer_pairs if isinstance(row, Mapping))
    observed_producer_pairs = sorted(
        (row[2], row[3], row[4], row[1]) for row in sample_tuples)
    if (len(expected_producer_pairs) != 2 or expected_producer_pairs != observed_producer_pairs
            or producer_evidence.get("solver_sequence_tag") != sample_tuples[0][1]):
        _fail("paired samples are not the exact distinct SolutionInfo tuples returned by the verified BMA producer")

    def cohort_projection(snapshot: Mapping[str, Any]) -> dict[str, Any]:
        solution = dict(snapshot["stored_solution"])
        solution.pop("selected_tuple", None)
        return {"dataset": dict(snapshot["dataset"]), "stored_solution": solution,
                "fixture_explicit_configuration": dict(snapshot["fixture_explicit_configuration"])}

    if cohort_projection(snapshots[0]) != cohort_projection(snapshots[1]):
        _fail("paired native tuples do not share the same stored computation/configuration identity")
    try:
        if hashlib.sha256(Path(source_path).read_bytes()).hexdigest() != source_hash:
            _fail("frozen Java source changed during the paired sampling segment")
    except OSError as exc:
        raise Full3DScienceError("frozen Java source could not be rechecked after paired sampling") from exc
    return {"status": "CONTROLLED_SAMPLING_COHORT_CHAIN_VALIDATED",
            "native_result": "STRUCTURAL_ROUTE_AND_SOURCE_SNAPSHOT_EVIDENCE_ONLY",
            "offline_stored_solution_cohort": "ELIGIBLE_FOR_INDEPENDENT_SCIENCE_COMPARISON",
            "scientific_acceptance": "UNVERIFIED_PENDING_NATIVE_ARTIFACT_AND_REVIEW",
            "source_artifact": source_artifact, "source_sha256": source_hash,
            "project_id": project_id, "model_ref": dict(model_ref),
            "server_identity": {"pid": pid, "start_epoch_ms": birth, "port": port},
            "session_identity": dict(session), "revision_start": start_revision,
            "revision_end": next_revision, "routes": validated_routes,
            "producer_identity": {key: producer_evidence[key] for key in (
                "managed_request_id", "managed_idempotency_key", "managed_operation_instance_id",
                "managed_job_id", "managed_request_hash", "managed_model_revision_before",
                "managed_model_revision_after", "solver_sequence_tag")},
            "sample_tuples": [list(value) for value in sample_tuples],
            "stored_solution_identity_sha256": _sha256(cohort_projection(snapshots[0])),
            "fixture_explicit_configuration_sha256": _sha256(
                snapshots[0]["fixture_explicit_configuration"]),
            "temporary_interps_removed_before_route_completion": True}


def _managed_route_binding(*, project_id: str, model_ref: Mapping[str, Any], model_tag: str,
                           revision: int, request_id: str,
                           idempotency_key: str) -> dict[str, Any]:
    if not isinstance(project_id, str) or not project_id.strip():
        _fail("authoritative registered project id is required")
    if not isinstance(model_ref, Mapping) or set(model_ref) != _MODEL_REF_KEYS:
        _fail("persisted project-bound ModelRef is required")
    if (type(model_ref.get("schema_version")) is not int or model_ref.get("schema_version") != 1
            or any(not isinstance(model_ref.get(key), str) or not model_ref[key].strip()
                   for key in ("session_id", "server_instance_id", "model_tag"))
            or type(model_ref.get("generation")) is not int or model_ref["generation"] < 1):
        _fail("persisted ModelRef is incomplete or malformed")
    if not isinstance(model_tag, str) or not model_tag.strip():
        _fail("native model tag is required")
    if type(revision) is not int or revision < 0:
        _fail("managed expected revision must be a nonnegative integer")
    if any(not isinstance(value, str) or not value.strip()
           for value in (request_id, idempotency_key)):
        _fail("managed request and idempotency identifiers are required")
    if model_ref.get("model_tag") != model_tag:
        _fail("persisted ModelRef tag differs from the native model tag")
    session_id = model_ref.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        _fail("managed operations require the exact session_id from the persisted ModelRef")
    return {"project_id": project_id, "model_ref": dict(model_ref), "model_tag": model_tag,
            "session_id": session_id, "expected_revision": revision, "request_id": request_id,
            "idempotency_key": idempotency_key}


def _operation_call(operation_id: str, arguments: Mapping[str, Any], *,
                    binding: Mapping[str, Any], dispatch_scope: str) -> dict[str, Any]:
    return {"operation": "operation_call",
            "arguments": {"operation_id": operation_id, "arguments": dict(arguments)},
            "execution": {key: binding[key] for key in
                          ("project_id", "session_id", "model_ref", "expected_revision", "request_id", "idempotency_key")},
            "dispatch_scope": dispatch_scope}


def build_full3d_study_run_dispatch(
    *, project_id: str, model_ref: Mapping[str, Any], model_tag: str, revision: int,
    request_id: str, idempotency_key: str, timeout_s: float,
) -> dict[str, Any]:
    """Build exactly one managed Study.run for the ordered 3-step std3d study."""
    timeout = _finite_number(timeout_s, "managed Study.run timeout")
    if timeout <= 0:
        _fail("managed Study.run timeout must be positive")
    binding = _managed_route_binding(
        project_id=project_id, model_ref=model_ref, model_tag=model_tag, revision=revision,
        request_id=request_id, idempotency_key=idempotency_key)
    return _operation_call("study.run", {
        "study": {"segments": [{"collection": "study", "tag": "std3d"}]},
        "timeout_s": timeout,
    }, binding=binding, dispatch_scope=(
        "one actual managed Study.run submission for std3d; configured step order is "
        "bmaInput3d, bmaOutput3d, freq3d; underlying solver-call count remains runtime-evidence-bound"))


def build_full3d_solution_inventory_dispatch(
    *, source_artifact: str, project_id: str, model_ref: Mapping[str, Any], model_tag: str,
    revision: int, request_id: str, idempotency_key: str,
) -> dict[str, Any]:
    """Read the generated solver-tree StudyStep bindings after a managed solve."""
    binding = _managed_route_binding(
        project_id=project_id, model_ref=model_ref, model_tag=model_tag, revision=revision,
        request_id=request_id, idempotency_key=idempotency_key)
    return _operation_call("code.execute_java", {
        "source_artifact": source_artifact,
        "entrypoint": "NativeW23Full3DFixture#run",
        "mode": "trusted", "arguments": {"phase": "solution_inventory"},
    }, binding=binding, dispatch_scope=(
        "managed native readback of std3d step bindings, generated solver tree and datasets; no solve"))


def build_full3d_bma_probe_prepare_dispatch(
    *, source_artifact: str, project_id: str, model_ref: Mapping[str, Any], model_tag: str,
    revision: int, request_id: str, idempotency_key: str,
) -> dict[str, Any]:
    """Create/read back an isolated one-step output-Port BMA solver sequence; no compute."""
    if not isinstance(source_artifact, str) or not source_artifact.strip():
        _fail("registered managed Java source artifact id is required")
    binding = _managed_route_binding(
        project_id=project_id, model_ref=model_ref, model_tag=model_tag, revision=revision,
        request_id=request_id, idempotency_key=idempotency_key)
    java_args = {"phase": "prepare_bma_output_probe",
        "managed_identity": {key: binding[key] for key in
                             ("project_id", "model_ref", "model_tag", "expected_revision")}}
    request = _operation_call("code.execute_java", {
        "source_artifact": source_artifact,
        "entrypoint": "NativeW23Full3DFixture#run",
        "mode": "trusted", "arguments": java_args,
    }, binding=binding, dispatch_scope=(
        "create an isolated study containing only receiver Port 2 BMA(neigs=2,f0), generate and read back its full solver tree; no solver run"))
    request["native_result"] = "NOT_RUN"
    request["study_or_solver_invoked"] = False
    return request


def build_full3d_bma_probe_run_dispatch(
    *, source_artifact: str, solver_sequence_tag: str,
    project_id: str, model_ref: Mapping[str, Any], model_tag: str,
    revision: int, request_id: str, idempotency_key: str,
) -> dict[str, Any]:
    """Submit one full SolverSequence.runAll on the exact prepared BMA-only study."""
    if not isinstance(source_artifact, str) or not source_artifact.strip():
        _fail("registered managed Java source artifact id is required")
    if not isinstance(solver_sequence_tag, str) or not _TAG.fullmatch(solver_sequence_tag):
        _fail("an exact native prepared solver-sequence tag is required")
    binding = _managed_route_binding(
        project_id=project_id, model_ref=model_ref, model_tag=model_tag, revision=revision,
        request_id=request_id, idempotency_key=idempotency_key)
    java_args = {"phase": "run_bma_output_probe", "study_tag": _BMA_PROBE_STUDY,
        "solver_sequence_tag": solver_sequence_tag,
        "managed_identity": {key: binding[key] for key in
                             ("project_id", "model_ref", "model_tag", "expected_revision")}}
    request = _operation_call("code.execute_java", {
        "source_artifact": source_artifact,
        "entrypoint": "NativeW23Full3DFixture#run",
        "mode": "trusted", "arguments": java_args,
    }, binding=binding, dispatch_scope=(
        "one full SolverSequence.runAll for the exact solver sequence attached to the isolated single-output-Port-BMA study; no frequency/input-BMA step"))
    request["native_result"] = "NOT_RUN"
    request["study_or_solver_invoked"] = False
    request["planned_solver_calls"] = 1
    request["planned_study_run_calls"] = 0
    return request


def validate_full3d_bma_probe_preparation(
    readback: Mapping[str, Any], *, project_id: str, model_tag: str,
    model_ref: Mapping[str, Any],
) -> dict[str, Any]:
    """Admit only an isolated, exact Port-2 BMA sequence; preparation is not a solve."""
    if (not isinstance(readback, Mapping)
            or readback.get("fixture_id") != "w23_full3d_fiber_ball_lens_vector_pml_v1"
            or readback.get("status") != "BMA_OUTPUT_PROBE_CONFIGURED_NOT_SOLVED"
            or readback.get("native_result") != "COMSOL_NATIVE_BMA_PROBE_CONFIGURATION_READBACK"
            or readback.get("study_or_solver_invoked") is not False
            or readback.get("producer_status") != "PREPARED_ONLY_NOT_PRODUCER_EVIDENCE"
            or readback.get("field_mapping_status") != "UNVERIFIED"):
        _fail("BMA probe preparation is missing its exact native no-solve status")
    identity = readback.get("managed_identity")
    if (not isinstance(identity, Mapping) or identity.get("project_id") != project_id
            or identity.get("model_tag") != model_tag or identity.get("model_ref") != dict(model_ref)):
        _fail("BMA probe preparation does not bind the authoritative project and ModelRef")

    port = readback.get("receiver_port")
    boundary_ids = port.get("boundary_ids") if isinstance(port, Mapping) else None
    if (not isinstance(port, Mapping) or port.get("feature_tag") != "portOut3d"
            or port.get("feature_type") != "Port" or port.get("port_type") != "Numeric"
            or port.get("port_name") != "2"
            or port.get("port_mode_number_readback") != "1"
            or port.get("selection_tag") != "sel3dOutputPort"
            or type(port.get("entity_dimension")) is not int or port.get("entity_dimension") != 2
            or not isinstance(boundary_ids, list) or not boundary_ids
            or any(type(item) is not int or item < 1 for item in boundary_ids)
            or len(set(boundary_ids)) != len(boundary_ids)):
        _fail("actual Numeric Port 2 configuration or boundary selection readback is incomplete")

    original_steps = readback.get("original_std3d_steps")
    expected_original = [
        {"tag": "bmaInput3d", "feature_type": "BoundaryModeAnalysis",
         "PortName": "1", "modeFreq": "f0", "neigs": 2},
        {"tag": "bmaOutput3d", "feature_type": "BoundaryModeAnalysis",
         "PortName": "2", "modeFreq": "f0", "neigs": 2},
        {"tag": "freq3d", "feature_type": "Frequency", "plist": "f0"},
    ]
    if original_steps != expected_original:
        _fail("the original std3d input-BMA/output-BMA/frequency baseline changed")

    probe = readback.get("probe_study")
    study_tag = probe.get("study_tag") if isinstance(probe, Mapping) else None
    study_steps = probe.get("study_steps") if isinstance(probe, Mapping) else None
    expected_probe_steps = [{"tag": "bmaOutputProbe", "feature_type": "BoundaryModeAnalysis",
                             "PortName": "2", "modeFreq": "f0", "neigs": 2}]
    if study_tag != "std3dBmaOutputProbe" or study_steps != expected_probe_steps:
        _fail("probe parent study must contain exactly one output Port 2 BMA step")

    sequence = readback.get("solver_sequence")
    solver_tag = sequence.get("tag") if isinstance(sequence, Mapping) else None
    tree = sequence.get("solver_tree_features") if isinstance(sequence, Mapping) else None
    bindings = sequence.get("study_step_bindings_in_solver_tree_order") if isinstance(sequence, Mapping) else None
    if (not isinstance(solver_tag, str) or not _TAG.fullmatch(solver_tag)
            or not isinstance(sequence, Mapping) or sequence.get("parent_study") != study_tag
            or not isinstance(tree, list) or not tree
            or not isinstance(bindings, list) or len(bindings) != 1):
        _fail("generated BMA solver sequence or its actual feature inventory is missing")
    paths: set[str] = set()
    for row in tree:
        if (not isinstance(row, Mapping) or not isinstance(row.get("path"), str)
                or not row.get("path") or not isinstance(row.get("feature_type"), str)
                or not row.get("feature_type") or row["path"] in paths):
            _fail("generated BMA solver feature list is malformed or duplicated")
        paths.add(row["path"])
    binding = bindings[0]
    if (not isinstance(binding, Mapping) or binding.get("study") != study_tag
            or binding.get("studystep") != "bmaOutputProbe"
            or binding.get("feature_type") != "StudyStep"
            or binding.get("path") not in paths):
        _fail("generated solver sequence is missing the exact output-BMA StudyStep binding")
    feature_types = {row.get("feature_type") for row in tree if isinstance(row, Mapping)}
    if not {"Variables", "Eigenvalue", "StoreSolution"} <= feature_types:
        _fail("generated BMA solver tree lacks the Variables/Eigenvalue/StoreSolution computation path")

    before = readback.get("pre_solve_solution_state")
    if (not isinstance(before, Mapping) or type(before.get("is_valid")) is not bool
            or before.get("solver_sequence_is_empty") is not True
            or before.get("outer_solnums") != [] or before.get("solution_pairs") != []
            or before.get("pair_count") != 0):
        _fail("new isolated solver sequence does not have a proven empty pre-solve solution state")
    return {"status": "ISOLATED_BMA_SEQUENCE_PREPARED_NOT_SOLVED",
            "native_result": "PREPARATION_ONLY_NOT_PRODUCER_EVIDENCE",
            "producer_status": "UNVERIFIED_UNTIL_EXACT_SEQUENCE_RUN_AND_SOLUTION_READBACK",
            "field_mapping_status": "UNVERIFIED",
            "project_id": project_id, "model_tag": model_tag,
            "model_ref": dict(model_ref), "receiver_port": dict(port),
            "probe_study": dict(probe), "solver_sequence": dict(sequence),
            "pre_solve_solution_state": dict(before)}


def validate_full3d_bma_probe_run_readback(
    readback: Mapping[str, Any], *, preparation: Mapping[str, Any],
    project_id: str, model_tag: str, model_ref: Mapping[str, Any],
    run_request: Mapping[str, Any], route_result: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify exact isolated-sequence run plus two distinct native solution tuples."""
    if (not isinstance(readback, Mapping)
            or readback.get("fixture_id") != "w23_full3d_fiber_ball_lens_vector_pml_v1"
            or readback.get("status") != "BMA_OUTPUT_PROBE_SOLVER_SEQUENCE_RETURNED_TWO_SOLUTION_ROWS"
            or readback.get("native_result") != "COMSOL_NATIVE_BMA_PRODUCER_RUN_READBACK"
            or readback.get("study_or_solver_invoked") is not True
            or readback.get("solver_calls") != 1 or readback.get("study_run_calls") != 0
            or readback.get("producer_status") != "CONTROLLED_SINGLE_STEP_BMA_PRODUCER_VERIFIED"
            or readback.get("field_mapping_status") != "UNVERIFIED"
            or readback.get("basis_ordinal_mapping") != "UNVERIFIED_NATIVE_FIELD_MAPPING_REQUIRED"):
        _fail("BMA probe response does not prove one completed exact-sequence solve and native readback")
    identity = readback.get("managed_identity")
    if (not isinstance(identity, Mapping) or identity.get("project_id") != project_id
            or identity.get("model_tag") != model_tag or identity.get("model_ref") != dict(model_ref)):
        _fail("BMA producer response does not bind the authoritative project and ModelRef")
    if (preparation.get("status") != "ISOLATED_BMA_SEQUENCE_PREPARED_NOT_SOLVED"
            or preparation.get("native_result") != "PREPARATION_ONLY_NOT_PRODUCER_EVIDENCE"
            or preparation.get("project_id") != project_id or preparation.get("model_tag") != model_tag
            or preparation.get("model_ref") != dict(model_ref)):
        _fail("a validated isolated BMA preparation receipt is required before producer admission")
    expected_probe = preparation.get("probe_study")
    expected_sequence = preparation.get("solver_sequence")
    if (not isinstance(expected_probe, Mapping) or not isinstance(expected_sequence, Mapping)
            or not isinstance(expected_sequence.get("tag"), str)
            or not expected_sequence.get("tag")):
        _fail("validated preparation receipt omitted the exact BMA study/solver identity")
    if not isinstance(run_request, Mapping):
        _fail("original managed BMA run request is missing")
    request_execution = run_request.get("execution") if isinstance(run_request, Mapping) else None
    operation = run_request.get("arguments") if isinstance(run_request, Mapping) else None
    java_arguments = operation.get("arguments") if isinstance(operation, Mapping) else None
    native_arguments = java_arguments.get("arguments") if isinstance(java_arguments, Mapping) else None
    native_identity = native_arguments.get("managed_identity") if isinstance(native_arguments, Mapping) else None
    if (run_request.get("operation") != "operation_call"
            or not isinstance(request_execution, Mapping)
            or request_execution.get("project_id") != project_id
            or request_execution.get("session_id") != model_ref.get("session_id")
            or request_execution.get("model_ref") != dict(model_ref)
            or request_execution.get("expected_revision") != identity.get("expected_revision")
            or not isinstance(request_execution.get("request_id"), str)
            or not request_execution.get("request_id")
            or not isinstance(request_execution.get("idempotency_key"), str)
            or not request_execution.get("idempotency_key")
            or not isinstance(operation, Mapping)
            or operation.get("operation_id") != "code.execute_java"
            or not isinstance(java_arguments, Mapping)
            or java_arguments.get("entrypoint") != "NativeW23Full3DFixture#run"
            or java_arguments.get("mode") != "trusted"
            or not isinstance(java_arguments.get("source_artifact"), str)
            or not java_arguments.get("source_artifact")
            or not isinstance(native_arguments, Mapping)
            or native_arguments.get("phase") != "run_bma_output_probe"
            or native_arguments.get("study_tag") != "std3dBmaOutputProbe"
            or native_arguments.get("solver_sequence_tag") != expected_sequence.get("tag")
            or native_identity != {"project_id": project_id, "model_ref": dict(model_ref),
                                   "model_tag": model_tag,
                                   "expected_revision": request_execution.get("expected_revision")}
            or run_request.get("native_result") != "NOT_RUN"
            or run_request.get("study_or_solver_invoked") is not False
            or run_request.get("planned_solver_calls") != 1
            or run_request.get("planned_study_run_calls") != 0):
        _fail("original managed run request is missing or differs from the exact BMA sequence/model revision")
    if (not isinstance(route_result, Mapping)
            or route_result.get("outcome") != "SUCCEEDED"
            or route_result.get("retry_forbidden") is not True
            or not isinstance(route_result.get("job_id"), str)
            or not route_result.get("job_id")):
        _fail("managed BMA run lacks a terminal, non-replayed public job identity")
    job_id = route_result["job_id"]
    submitted_request = route_result.get("submitted_request")
    submitted_execution = submitted_request.get("execution") if isinstance(submitted_request, Mapping) else None
    if not isinstance(submitted_request, Mapping) or not isinstance(submitted_execution, Mapping):
        _fail("managed BMA run omitted the exact bounded request sent through public dispatch")
    timeout_names = ("execution_timeout_s", "queue_timeout_s", "rpc_timeout_s")
    timeouts = {name: submitted_execution.get(name) for name in timeout_names}
    if (submitted_request.get("operation") != run_request.get("operation")
            or submitted_request.get("arguments") != run_request.get("arguments")
            or any(submitted_execution.get(name) != request_execution.get(name)
                   for name in ("project_id", "session_id", "model_ref", "expected_revision",
                                "request_id", "idempotency_key"))
            or set(submitted_execution) != set(request_execution) | set(timeout_names)
            or any(isinstance(value, bool) or not isinstance(value, (int, float))
                   or not math.isfinite(float(value)) or value <= 0.0
                   for value in timeouts.values())
            or timeouts["execution_timeout_s"] > 600.0
            or timeouts["queue_timeout_s"] != min(60.0, timeouts["execution_timeout_s"])
            or timeouts["rpc_timeout_s"] != min(30.0, timeouts["execution_timeout_s"])):
        _fail("bounded public request changed the frozen BMA request or its admitted timeouts")
    expected_execution = {**dict(request_execution), **timeouts}
    if dict(submitted_request) != {**dict(run_request), "execution": expected_execution}:
        _fail("actual public BMA request differs from the frozen logical request and bounded timeout envelope")
    try:
        from comsol_mcp._execution_contract import canonical_request_hash

        nested_operation = operation.get("operation_id")
        nested_arguments = operation.get("arguments")
        if nested_operation != "code.execute_java" or not isinstance(nested_arguments, Mapping):
            _fail("original public request does not contain the exact code.execute_java arguments")
        expected_request_hash = canonical_request_hash(
            nested_operation, nested_arguments, request_execution["model_ref"],
            request_execution["expected_revision"],
            project_id=request_execution["project_id"],
            session_id=request_execution["session_id"],
            queue_timeout_s=timeouts["queue_timeout_s"],
            execution_timeout_s=timeouts["execution_timeout_s"],
            no_progress_warning_s=submitted_execution.get("no_progress_warning_s"),
        )
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        raise Full3DScienceError("canonical managed BMA request hash could not be recomputed") from exc
    if not isinstance(expected_request_hash, str) or re.fullmatch(r"[0-9a-f]{64}", expected_request_hash) is None:
        _fail("canonical managed BMA request hash is malformed")
    dispatch_response = route_result.get("dispatch_response")
    dispatch_waits = route_result.get("job_wait_responses")
    operation_response = route_result.get("response")
    initial_execution = dispatch_response.get("execution") if isinstance(dispatch_response, Mapping) else None
    if (not isinstance(dispatch_response, Mapping)
            or _managed_job_id(dispatch_response) != job_id
            or not isinstance(initial_execution, Mapping)
            or initial_execution.get("request_id") != request_execution["request_id"]
            or initial_execution.get("idempotency_key") != request_execution["idempotency_key"]
            or not isinstance(initial_execution.get("operation_id"), str)
            or not initial_execution.get("operation_id")
            or initial_execution.get("job_id") != job_id
            or initial_execution.get("request_hash") != expected_request_hash
            or not isinstance(dispatch_waits, list) or not dispatch_waits
            or not isinstance(operation_response, Mapping)
            or operation_response.get("success") is not True):
        _fail("managed BMA dispatch/job ledger does not preserve the exact submitted request and job")
    terminal_wait = dispatch_waits[-1]
    terminal_job = terminal_wait.get("data") if isinstance(terminal_wait, Mapping) else None
    stored_operation = terminal_job.get("operation") if isinstance(terminal_job, Mapping) else None
    terminal_result_execution = operation_response.get("execution") if isinstance(operation_response, Mapping) else None
    if not isinstance(stored_operation, Mapping):
        _fail("public terminal job omitted its authoritative stored operation identity")
    expected_operation_id = initial_execution["operation_id"]
    identity_fields = {
        "request_id": request_execution["request_id"],
        "idempotency_key": request_execution["idempotency_key"],
        "operation_id": expected_operation_id,
        "job_id": job_id,
        "request_hash": expected_request_hash,
    }
    if (not isinstance(terminal_result_execution, Mapping)
            or any(terminal_result_execution.get(name) != value
                   for name, value in identity_fields.items())
            or any(stored_operation.get(name) != value for name, value in identity_fields.items()
                   if name != "job_id")):
        _fail("terminal public result or stored operation is not bound to the original request identity")
    if (not isinstance(terminal_wait, Mapping) or terminal_wait.get("success") is not True
            or not isinstance(terminal_job, Mapping) or terminal_job.get("job_id") != job_id
            or terminal_job.get("operation_id") != expected_operation_id
            or terminal_job.get("status") != "SUCCEEDED"
            or terminal_job.get("result") != dict(operation_response)):
        _fail("public job.wait result is not the exact terminal managed BMA response")
    response_data = operation_response.get("data")
    worker = response_data.get("worker") if isinstance(response_data, Mapping) else None
    wrapper = response_data.get("readback") if isinstance(response_data, Mapping) else None
    worker_result = worker.get("result") if isinstance(worker, Mapping) else None
    response_execution = terminal_result_execution
    response_revision = response_execution.get("revision") if isinstance(response_execution, Mapping) else None
    if (not isinstance(worker, Mapping) or worker.get("ok") is not True
            or worker.get("status") != "SUCCEEDED"
            or not isinstance(worker_result, Mapping) or worker_result.get("readback") != dict(readback)
            or not isinstance(wrapper, Mapping) or wrapper.get("executed") is not True
            or wrapper.get("readback") != dict(readback)
            or not isinstance(response_execution, Mapping)
            or response_execution.get("model_ref") != dict(model_ref)
            or type(response_revision) is not int
            or response_revision != request_execution.get("expected_revision") + 1):
        _fail("terminal public job response is not bound to the exact Worker readback and one legal managed revision transition")
    invocation = readback.get("invocation")
    if (readback.get("probe_study") != expected_probe
            or readback.get("solver_sequence") != expected_sequence
            or not isinstance(invocation, Mapping)
            or invocation.get("method") != "SolverSequence.runAll"
            or invocation.get("solver_sequence_tag") != expected_sequence.get("tag")
            or invocation.get("parent_study_tag") != "std3dBmaOutputProbe"
            or invocation.get("parent_study_step_tag") != "bmaOutputProbe"
            or invocation.get("method_returned") is not True):
        _fail("solver invocation/readback no longer matches the exact prepared one-step BMA sequence")
    before = readback.get("pre_solve_solution_state")
    if before != preparation.get("pre_solve_solution_state") or before.get("solution_pairs") != []:
        _fail("producer solve did not begin from the exact empty prepared sequence")

    post = readback.get("post_solve_solution_state")
    outers = post.get("outer_solnums") if isinstance(post, Mapping) else None
    pairs = post.get("solution_pairs") if isinstance(post, Mapping) else None
    if (not isinstance(post, Mapping) or post.get("is_valid") is not True
            or post.get("solver_sequence_is_empty") is not False
            or not isinstance(outers, list) or len(outers) != 1
            or any(type(item) is not int or item < 1 for item in outers)
            or len(set(outers)) != len(outers)
            or not isinstance(pairs, list) or len(pairs) != 2
            or post.get("pair_count") != 2):
        _fail("producer run returned an empty, invalid, or non-two-solution SolutionInfo axis")
    sequence_tag = expected_sequence.get("tag")
    seen: set[tuple[int, int]] = set()
    seen_inner: set[int] = set()
    seen_outer: set[int] = set()
    checked_pairs: list[dict[str, Any]] = []
    for pair in pairs:
        if not isinstance(pair, Mapping):
            _fail("producer SolutionInfo pair is malformed")
        outer, inner, solnum = pair.get("outer_index"), pair.get("inner_index"), pair.get("solnum")
        if (type(outer) is not int or outer < 1 or outer not in outers
                or type(inner) is not int or inner < 1
                or type(solnum) is not int or solnum != inner
                or pair.get("solver_sequence_tag") != sequence_tag
                or (outer, inner) in seen or inner in seen_inner):
            _fail("producer SolutionInfo pairs are duplicated or do not map to the exact BMA sequence")
        seen.add((outer, inner))
        seen_inner.add(inner)
        seen_outer.add(outer)
        checked_pairs.append({"outer_index": outer, "inner_index": inner,
                              "solnum": solnum, "solver_sequence_tag": sequence_tag})
    if seen_outer != set(outers):
        _fail("producer SolutionInfo rows do not cover the complete outer-solution axis")
    return {"status": "VERIFIED_CONTROLLED_SINGLE_BMA_PRODUCER",
            "native_result": "COMSOL_NATIVE_BMA_PRODUCER_RUN_READBACK",
            "producer_step_binding": "VERIFIED_BY_ISOLATED_ONE_STEP_STUDY_AND_EXACT_SEQUENCE_RUNALL",
            "producer_study_tag": "std3dBmaOutputProbe",
            "producer_step_tag": "bmaOutputProbe",
            "solver_sequence_tag": sequence_tag,
            "eigensolution_row_count": 2,
            "eigensolution_solution_pairs": checked_pairs,
            "managed_operation_id": "code.execute_java",
            "managed_request_id": request_execution["request_id"],
            "managed_idempotency_key": request_execution["idempotency_key"],
            "managed_request_hash": expected_request_hash,
            "managed_operation_instance_id": expected_operation_id,
            "managed_job_id": job_id,
            "managed_model_revision_before": request_execution["expected_revision"],
            "managed_model_revision_after": response_revision,
            "basis_ordinal_assignment": "UNVERIFIED_NATIVE_FIELD_MAPPING_REQUIRED",
            "numeric_port_mode_field_mapping": "UNVERIFIED",
            "project_id": project_id, "model_tag": model_tag,
            "model_ref": dict(model_ref)}


def build_full3d_dataset_list_dispatch(
    *, project_id: str, model_ref: Mapping[str, Any], model_tag: str, revision: int,
    request_id: str, idempotency_key: str,
) -> dict[str, Any]:
    binding = _managed_route_binding(
        project_id=project_id, model_ref=model_ref, model_tag=model_tag, revision=revision,
        request_id=request_id, idempotency_key=idempotency_key)
    return _operation_call("dataset.list", {}, binding=binding,
                           dispatch_scope="managed native result-dataset inventory readback")


def build_full3d_dataset_indices_dispatch(
    dataset_tag: str, *, project_id: str, model_ref: Mapping[str, Any], model_tag: str,
    revision: int, request_id: str, idempotency_key: str,
) -> dict[str, Any]:
    if not isinstance(dataset_tag, str) or not _TAG.fullmatch(dataset_tag):
        _fail("solution dataset tag must be an exact COMSOL tag")
    binding = _managed_route_binding(
        project_id=project_id, model_ref=model_ref, model_tag=model_tag, revision=revision,
        request_id=request_id, idempotency_key=idempotency_key)
    return _operation_call("dataset.solution_indices", {"path": dataset_tag}, binding=binding,
                           dispatch_scope="managed native dataset-to-solution/index metadata readback")


def build_full3d_save_dispatch(
    *, source_artifact: str, path: str, project_id: str, model_ref: Mapping[str, Any],
    model_tag: str, revision: int, request_id: str, idempotency_key: str,
) -> dict[str, Any]:
    """Save one case model below its authoritative registered project workspace."""
    if not isinstance(path, str) or not path.strip():
        _fail("managed MPH destination path is required")
    binding = _managed_route_binding(
        project_id=project_id, model_ref=model_ref, model_tag=model_tag, revision=revision,
        request_id=request_id, idempotency_key=idempotency_key)
    return _operation_call("code.execute_java", {
        "source_artifact": source_artifact,
        "entrypoint": "NativeW23Full3DFixture#run",
        "mode": "trusted", "arguments": {"phase": "save", "path": path},
    }, binding=binding, dispatch_scope=(
        "save one native case into the persisted project's child workspace; no solver call"))


def build_full3d_model_load_request(
    *, path: str, project_id: str, session_id: str, request_id: str, idempotency_key: str,
) -> dict[str, Any]:
    """Reopen one existing MPH using the production project-scoped model_load route."""
    if not isinstance(path, str) or not path.strip():
        _fail("managed saved-model path is required")
    if any(not isinstance(value, str) or not value.strip()
           for value in (project_id, session_id, request_id, idempotency_key)):
        _fail("authoritative project/session and managed load identifiers are required")
    return {"operation": "model_load", "arguments": {"path": path},
            "execution": {"project_id": project_id, "session_id": session_id,
                          "request_id": request_id,
                          "idempotency_key": idempotency_key},
            "dispatch_scope": "one production project-scoped managed model_load; no caller root override"}


def build_full3d_project_create_request(
    *, label: str, request_id: str, idempotency_key: str,
) -> dict[str, Any]:
    """Create the registered child workspace before any engine birth."""
    if not isinstance(label, str) or not label.strip():
        _fail("registered science project label is required")
    if any(not isinstance(value, str) or not value.strip()
           for value in (request_id, idempotency_key)):
        _fail("project.create requires distinct request and idempotency identifiers")
    return {"operation": "project.create",
            "arguments": {"label": label.strip(), "workspace": "science",
                          "policy": {"permissions": ["inspect", "project_write", "compute", "trusted_code"]}},
            "execution": {"request_id": request_id, "idempotency_key": idempotency_key},
            "dispatch_scope": "offline authoritative project.create; no Worker or COMSOL engine call"}


def bind_full3d_project_create_response(
    response: Mapping[str, Any], *, authorized_container: str,
) -> dict[str, Any]:
    """Bind only the persisted project.create record and its exact child path."""
    if not isinstance(response, Mapping) or response.get("success") is not True:
        _fail("production project.create did not succeed")
    data = response.get("data")
    project = data.get("project") if isinstance(data, Mapping) else None
    if not isinstance(project, Mapping):
        _fail("project.create response lacks the persisted project record")
    project_id, workspace = project.get("project_id"), project.get("workspace")
    if (not isinstance(project_id, str) or not project_id.strip()
            or not isinstance(workspace, str) or not workspace.strip()):
        _fail("project.create response lacks authoritative project_id/workspace")
    from pathlib import Path
    try:
        root = Path(authorized_container).resolve(strict=True)
        child = Path(workspace).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        _fail(f"registered science workspace cannot be resolved: {type(exc).__name__}")
    if not root.is_dir() or not child.is_dir() or child.parent != root or child.name != "science":
        _fail("registered science workspace is not the exact authorized child")
    policy = project.get("policy")
    permissions = policy.get("permissions") if isinstance(policy, Mapping) else None
    if not isinstance(permissions, list) or not {
            "inspect", "project_write", "compute", "trusted_code"}.issubset(set(permissions)):
        _fail("persisted science project policy lacks the required inspect/write/compute/trusted-code grants")
    return {"status": "PROJECT_CREATE_BOUND", "project_id": project_id,
            "workspace": str(child), "schema_version": project.get("schema_version"),
            "revision": project.get("revision"), "label": project.get("label"),
            "contract": project.get("contract"), "policy": project.get("policy"),
            "native_result": "NOT_RUN"}


def build_full3d_server_connect_request(
    *, project_id: str, host: str, port: int, request_id: str, idempotency_key: str,
) -> dict[str, Any]:
    if any(not isinstance(value, str) or not value.strip()
           for value in (project_id, host, request_id, idempotency_key)):
        _fail("server_connect requires project, endpoint, request and idempotency identities")
    if type(port) is not int or not 1 <= port <= 65535:
        _fail("server_connect port must be a valid TCP port")
    return {"operation": "server_connect", "arguments": {"host": host, "port": port},
            "execution": {"project_id": project_id, "request_id": request_id,
                          "idempotency_key": idempotency_key},
            "dispatch_scope": "connect this managed session to the already-owned server endpoint"}


def bind_full3d_connection_response(
    response: Mapping[str, Any], *, expected_endpoint: str,
) -> dict[str, Any]:
    if not isinstance(response, Mapping) or response.get("success") is not True:
        _fail("managed server_connect did not succeed")
    data = response.get("data")
    worker = data.get("worker") if isinstance(data, Mapping) else None
    if not isinstance(data, Mapping) or not isinstance(worker, Mapping):
        _fail("server_connect lacks its runtime connection readback")
    session_id, server_id = data.get("session_id"), data.get("server_instance_id")
    endpoint = data.get("endpoint")
    epoch = worker.get("generation", worker.get("connection_epoch"))
    if (not isinstance(session_id, str) or not session_id.strip()
            or not isinstance(server_id, str) or not server_id.strip()
            or endpoint != expected_endpoint or type(epoch) is not int or epoch < 1):
        _fail("server_connect session/epoch/endpoint differs from the owned runtime")
    return {"status": "MANAGED_SESSION_BOUND", "session_id": session_id,
            "server_instance_id": server_id, "worker_epoch": epoch,
            "endpoint": endpoint, "native_result": "NOT_RUN"}


def build_full3d_model_create_request(
    *, project_id: str, session_id: str, name: str,
    request_id: str, idempotency_key: str,
) -> dict[str, Any]:
    if any(not isinstance(value, str) or not value.strip()
           for value in (project_id, session_id, name, request_id, idempotency_key)):
        _fail("model_create requires authoritative project/session and request identities")
    return {"operation": "model_create", "arguments": {"name": name},
            "execution": {"project_id": project_id, "session_id": session_id,
                          "request_id": request_id, "idempotency_key": idempotency_key},
            "dispatch_scope": "one production project-scoped model_create"}


def bind_full3d_model_create_response(
    response: Mapping[str, Any], *, project_id: str, session: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(response, Mapping) or response.get("success") is not True:
        _fail("managed project-scoped model_create did not succeed")
    execution = response.get("execution")
    ref = execution.get("model_ref") if isinstance(execution, Mapping) else None
    revision = execution.get("revision") if isinstance(execution, Mapping) else None
    if not isinstance(ref, Mapping) or set(ref) != _MODEL_REF_KEYS:
        _fail("model_create did not return an exact ModelRef")
    if (ref.get("schema_version") != 1 or ref.get("session_id") != session.get("session_id")
            or ref.get("server_instance_id") != session.get("server_instance_id")
            or not isinstance(ref.get("model_tag"), str) or type(ref.get("generation")) is not int
            or ref["generation"] < 1 or type(revision) is not int or revision < 0):
        _fail("model_create ModelRef/revision is not bound to the observed session epoch")
    return {"status": "MODEL_CREATED_PROJECT_SCOPED", "project_id": project_id,
            "session_id": session["session_id"], "worker_epoch": session["worker_epoch"],
            "model_ref": dict(ref), "model_tag": ref["model_tag"],
            "revision": revision, "native_result": "NOT_RUN"}


def stage_full3d_fixture_source(
    source_path: str | Path, *, project_workspace: str | Path,
    expected_sha256: str,
) -> dict[str, Any]:
    """Copy the exact Java source into the registered science workspace once."""
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        _fail("frozen full-3D Java source SHA-256 is required")
    try:
        source = Path(source_path)
        if source.is_symlink():
            _fail("fixture source must not be a symlink")
        source = source.resolve(strict=True)
        workspace = Path(project_workspace).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        _fail(f"fixture source/workspace cannot be resolved: {type(exc).__name__}")
    if not source.is_file() or source.suffix.lower() != ".java" or not workspace.is_dir():
        _fail("fixture source must be a Java file and the registered workspace an existing directory")
    payload = source.read_bytes()
    source_hash = hashlib.sha256(payload).hexdigest()
    if source_hash != expected_sha256:
        _fail("full-3D Java source differs from the frozen source hash")
    destination = workspace / "NativeW23Full3DFixture.java"
    if destination.exists() or destination.is_symlink():
        _fail("refusing to overwrite a preexisting project-local Java source")
    try:
        with destination.open("xb") as stream:
            stream.write(payload)
    except OSError as exc:
        _fail(f"project-local Java source staging failed: {type(exc).__name__}")
    if destination.is_symlink() or destination.resolve(strict=True).parent != workspace:
        _fail("staged Java source escaped the registered project workspace")
    staged_hash = hashlib.sha256(destination.read_bytes()).hexdigest()
    if staged_hash != expected_sha256:
        _fail("staged Java source SHA-256 differs from its frozen source")
    return {"status": "PROJECT_LOCAL_SOURCE_STAGED", "source_artifact": destination.name,
            "workspace": str(workspace), "path": str(destination), "bytes": len(payload),
            "sha256": staged_hash, "native_result": "NOT_RUN"}


def _managed_job_id(response: Mapping[str, Any]) -> str | None:
    execution, data = response.get("execution"), response.get("data")
    candidates = [execution.get("job_id") if isinstance(execution, Mapping) else None,
                  data.get("job_id") if isinstance(data, Mapping) else None]
    return next((value for value in candidates if isinstance(value, str) and value), None)


def dispatch_public_managed_route(
    daemon: Any, request: Mapping[str, Any], *, label: str,
    timeout_s: float = 300.0, poll_interval_s: float = 0.1,
) -> dict[str, Any]:
    """Use only ControlDaemon.dispatch, including public job.wait for queued work.

    The operation request is sent once. Repeated job.wait calls observe only the
    same durable job; any dispatch/wait ambiguity is returned as UNKNOWN and is
    never turned into a second operation submission.
    """
    if not callable(getattr(daemon, "dispatch", None)):
        _fail("managed W23 route requires the public ControlDaemon.dispatch API")
    timeout = _finite_number(timeout_s, "public managed-route wait timeout")
    interval = _finite_number(poll_interval_s, "public managed-route poll interval")
    if timeout <= 0.0 or interval <= 0.0:
        _fail("managed-route timeout and poll interval must be positive")
    raw_request = dict(request)
    try:
        initial = daemon.dispatch(raw_request)
    except BaseException as exc:
        raise ManagedRouteOutcomeError(label, "UNKNOWN",
            f"public dispatch raised {type(exc).__name__}; request is not replayed") from exc
    if not isinstance(initial, Mapping):
        raise ManagedRouteOutcomeError(label, "UNKNOWN", "dispatch returned a non-object; request is not replayed",
                                       response=initial)
    job_id = _managed_job_id(initial)
    if job_id is None:
        if initial.get("success") is True:
            return {"response": dict(initial), "dispatch_response": dict(initial),
                    "job_wait_responses": [], "job_id": None,
                    "outcome": "SUCCEEDED", "retry_forbidden": True}
        error = initial.get("error")
        code = error.get("code") if isinstance(error, Mapping) else None
        outcome = "UNKNOWN" if code in {"EXECUTION_STATE_UNKNOWN", "WORKER_STATE_UNKNOWN"} else "FAILED"
        raise ManagedRouteOutcomeError(label, outcome, "public dispatch did not succeed",
                                       response=dict(initial))

    deadline = time.monotonic() + timeout
    waits: list[dict[str, Any]] = []
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            raise ManagedRouteOutcomeError(label, "UNKNOWN",
                "public job.wait deadline expired while observing the same job; operation is not replayed",
                response={"dispatch": dict(initial), "job_wait_responses": waits}, job_id=job_id)
        wait_request = {"operation": "job.wait",
                        "arguments": {"job_id": job_id,
                                      "timeout_s": min(remaining, max(interval, 1.0)),
                                      "poll_interval_s": min(interval, 1.0)},
                        "execution": {}}
        try:
            observed = daemon.dispatch(wait_request)
        except BaseException as exc:
            raise ManagedRouteOutcomeError(label, "UNKNOWN",
                f"public job.wait raised {type(exc).__name__}; job {job_id} remains unreplayed",
                response={"dispatch": dict(initial), "job_wait_responses": waits}, job_id=job_id) from exc
        if not isinstance(observed, Mapping) or observed.get("success") is not True:
            waits.append(dict(observed) if isinstance(observed, Mapping) else {"non_object": repr(observed)})
            raise ManagedRouteOutcomeError(label, "UNKNOWN",
                f"public job.wait could not establish job {job_id} state; operation is not replayed",
                response={"dispatch": dict(initial), "job_wait_responses": waits}, job_id=job_id)
        waits.append(dict(observed))
        job = observed.get("data")
        if not isinstance(job, Mapping) or job.get("job_id") != job_id:
            raise ManagedRouteOutcomeError(label, "UNKNOWN",
                f"public job.wait response is not bound to exact job {job_id}",
                response={"dispatch": dict(initial), "job_wait_responses": waits}, job_id=job_id)
        status = job.get("status")
        if job.get("wait_expired") is True or status not in {"SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED", "UNKNOWN", "LOST"}:
            continue
        if status != "SUCCEEDED":
            raise ManagedRouteOutcomeError(label, "UNKNOWN" if status in {"UNKNOWN", "LOST"} else "FAILED",
                f"job {job_id} reached terminal status {status}; operation is not replayed",
                response={"dispatch": dict(initial), "job_wait_responses": waits, "job": dict(job)},
                job_id=job_id)
        terminal = job.get("result")
        if not isinstance(terminal, Mapping) or terminal.get("success") is not True:
            raise ManagedRouteOutcomeError(label, "UNKNOWN",
                f"job {job_id} is marked SUCCEEDED but its terminal result is absent or inconsistent",
                response={"dispatch": dict(initial), "job_wait_responses": waits, "job": dict(job)},
                job_id=job_id)
        return {"response": dict(terminal), "dispatch_response": dict(initial),
                "job_wait_responses": waits, "job_id": job_id,
                "outcome": "SUCCEEDED", "retry_forbidden": True}


def _java_action_readback(response: Mapping[str, Any], label: str) -> dict[str, Any]:
    if response.get("success") is not True:
        raise ManagedRouteOutcomeError(label, "FAILED", "managed Java operation failed",
                                       response=dict(response))
    data = response.get("data")
    worker = data.get("worker") if isinstance(data, Mapping) else None
    wrapper = data.get("readback") if isinstance(data, Mapping) else None
    if (not isinstance(worker, Mapping) or worker.get("ok") is not True
            or worker.get("status") != "SUCCEEDED" or not isinstance(wrapper, Mapping)
            or wrapper.get("executed") is not True or not isinstance(wrapper.get("readback"), Mapping)):
        raise ManagedRouteOutcomeError(label, "UNKNOWN",
            "terminal managed Java result lacks matching Worker/readback evidence",
            response=dict(response))
    if not isinstance(worker.get("result"), Mapping) or worker["result"].get("readback") != wrapper["readback"]:
        raise ManagedRouteOutcomeError(label, "UNKNOWN",
            "Worker and managed Java readbacks disagree", response=dict(response))
    return dict(wrapper["readback"])


def _updated_full3d_model_state(
    response: Mapping[str, Any], *, prior_model: Mapping[str, Any],
) -> dict[str, Any]:
    execution = response.get("execution")
    ref = execution.get("model_ref") if isinstance(execution, Mapping) else None
    revision = execution.get("revision") if isinstance(execution, Mapping) else None
    if not isinstance(ref, Mapping) or set(ref) != _MODEL_REF_KEYS or type(revision) is not int or revision < 0:
        raise ManagedRouteOutcomeError("model state readback", "UNKNOWN",
            "managed mutation omitted the authoritative returned ModelRef/revision; no next mutation is issued",
            response=dict(response))
    for key in _MODEL_REF_KEYS:
        if ref.get(key) != prior_model.get("model_ref", {}).get(key):
            raise ManagedRouteOutcomeError("model state readback", "UNKNOWN",
                f"managed mutation changed ModelRef identity field {key}; no next mutation is issued",
                response=dict(response))
    return {**dict(prior_model), "model_ref": dict(ref), "revision": revision}


def _check_full3d_fixture_readback(
    readback: Mapping[str, Any], *, recipe_sha256: str, project_id: str,
    model: Mapping[str, Any], case: Mapping[str, Any] | None = None,
) -> None:
    status = "GEOMETRY_CASE_CONFIGURED_NOT_SOLVED" if case is not None else "BUILT_CONFIGURED_NOT_SOLVED"
    if (readback.get("fixture_id") != "w23_full3d_fiber_ball_lens_vector_pml_v1"
            or readback.get("recipe_sha256") != recipe_sha256
            or readback.get("status") != status or readback.get("native_result") != "NOT_RUN"
            or readback.get("study_or_solver_invoked") is not False):
        _fail("native full-3D fixture readback is not the exact configured/no-solve result")
    identity = readback.get("managed_identity")
    if (not isinstance(identity, Mapping) or identity.get("project_id") != project_id
            or identity.get("model_tag") != model.get("model_tag")
            or identity.get("model_ref") != model.get("model_ref")):
        _fail("native fixture readback is not bound to the current authoritative project/ModelRef")
    if case is not None:
        expected_pairs = (("case_id", "case_id"), ("case_identity_sha256", "case_identity_sha256"),
                          ("experiment_id", "experiment_id"), ("factor", "factor"),
                          ("factor_value", "value"))
        if any(readback.get(output_key) != case.get(case_key)
               for output_key, case_key in expected_pairs):
            _fail("native geometry readback differs from the exact registered case identity")


def execute_full3d_managed_configuration_chain(
    daemon: Any, *, project_create_response: Mapping[str, Any], authorized_container: str | Path,
    connection_response: Mapping[str, Any], expected_endpoint: str,
    fixture_source_path: str | Path, fixture_source_sha256: str,
    recipe: Mapping[str, Any], experiment_id: str, model_name: str,
    wait_timeout_s: float = 300.0,
) -> dict[str, Any]:
    """Stage source, create/bind one model, and run the no-solve 3-D build/baseline chain.

    This only reaches a configured fixture. It returns the exact single
    ``study.run`` request for a later separately reviewed native campaign and
    never submits it here.
    """
    from tools.w23_full3d import (
        bind_case_matrix, build_full3d_case_dispatch, build_full3d_fixture_dispatch,
        canonical_full3d_recipe, verify_recipe,
    )

    if not isinstance(recipe, Mapping):
        recipe = canonical_full3d_recipe()
    recipe_check = verify_recipe(recipe)
    project = bind_full3d_project_create_response(
        project_create_response, authorized_container=str(authorized_container))
    session = bind_full3d_connection_response(
        connection_response, expected_endpoint=expected_endpoint)
    staged = stage_full3d_fixture_source(fixture_source_path,
        project_workspace=project["workspace"], expected_sha256=fixture_source_sha256)

    def call_id(label: str) -> tuple[str, str]:
        return f"w23f3d-{label}-{uuid4()}", f"w23f3d-idem-{label}-{uuid4()}"

    request_id, key = call_id("model-create")
    create_request = build_full3d_model_create_request(
        project_id=project["project_id"], session_id=session["session_id"], name=model_name,
        request_id=request_id, idempotency_key=key)
    created = dispatch_public_managed_route(daemon, create_request, label="model_create",
                                             timeout_s=wait_timeout_s)
    model = bind_full3d_model_create_response(
        created["response"], project_id=project["project_id"], session=session)

    request_id, key = call_id("fixture-build")
    build_request = build_full3d_fixture_dispatch(
        recipe, source_artifact=staged["source_artifact"], project_id=project["project_id"],
        model_ref=model["model_ref"], model_tag=model["model_tag"], revision=model["revision"],
        request_id=request_id, idempotency_key=key)
    built = dispatch_public_managed_route(daemon, build_request, label="full3d_fixture_build",
                                          timeout_s=wait_timeout_s)
    model = _updated_full3d_model_state(built["response"], prior_model=model)
    build_readback = _java_action_readback(built["response"], "full3d_fixture_build")
    _check_full3d_fixture_readback(
        build_readback, recipe_sha256=recipe_check["recipe_sha256"],
        project_id=project["project_id"], model=model)

    plan = bind_case_matrix(recipe, project_id=project["project_id"],
        model_ref=model["model_ref"], model_tag=model["model_tag"], revision=model["revision"],
        experiment_id=experiment_id)
    baseline = next((row for row in plan["cases"] if row.get("factor") == "baseline"), None)
    if not isinstance(baseline, Mapping):
        _fail("registered full-3D case plan lacks its unique baseline row")
    if sum(1 for row in plan["cases"] if row.get("factor") == "baseline") != 1:
        _fail("registered full-3D case plan has duplicate baseline rows")
    request_id, key = call_id("baseline-apply")
    apply_request = build_full3d_case_dispatch(
        plan, baseline, source_artifact=staged["source_artifact"],
        request_id=request_id, idempotency_key=key)
    applied = dispatch_public_managed_route(daemon, apply_request, label="full3d_baseline_apply",
                                            timeout_s=wait_timeout_s)
    model = _updated_full3d_model_state(applied["response"], prior_model=model)
    apply_readback = _java_action_readback(applied["response"], "full3d_baseline_apply")
    _check_full3d_fixture_readback(
        apply_readback, recipe_sha256=recipe_check["recipe_sha256"],
        project_id=project["project_id"], model=model, case=baseline)

    request_id, key = call_id("study-run")
    study_request = build_full3d_study_run_dispatch(
        project_id=project["project_id"], model_ref=model["model_ref"],
        model_tag=model["model_tag"], revision=model["revision"], request_id=request_id,
        idempotency_key=key, timeout_s=wait_timeout_s)
    return {"status": "FULL3D_CONFIGURED_STUDY_READY_NOT_RUN",
            "project": project, "session": session, "staged_fixture": staged,
            "model": model, "recipe": {"fixture_id": recipe.get("fixture_id"),
                                        "recipe_sha256": recipe_check["recipe_sha256"]},
            "case_plan": plan, "selected_case": dict(baseline),
            "build_readback": build_readback, "apply_readback": apply_readback,
            "study_run_request": study_request,
            "study_or_solver_invoked": False, "native_result": "NOT_RUN",
            "native_solve_authorized_or_submitted": False,
            "calls": {"model_create": created, "fixture_build": built,
                      "baseline_apply": applied}}


def build_full3d_mode_overlap_definition(
    sources: Mapping[str, Mapping[str, Any]], *, output_normal_sign: int,
    input_normal_sign: int, capture_normal_sign: int, power_floor_w: float = 1e-12,
) -> dict[str, Any]:
    """Bind native 3-D source identities and named boundaries to result.mode_overlap."""
    expected_roles = {"signal", "reference_mode", "incident_reference"}
    if not isinstance(sources, Mapping) or set(sources) != expected_roles:
        _fail("native source inventory must uniquely bind signal, output mode and input mode")
    for value, label in ((output_normal_sign, "output"), (input_normal_sign, "input"),
                         (capture_normal_sign, "capture")):
        if type(value) is not int or value not in {-1, 1}:
            _fail(f"native {label} surface normal sign must be measured as exactly +1 or -1")
    if capture_normal_sign != output_normal_sign:
        _fail("capture and output surfaces must share the same physical forward-normal convention")
    floor = _finite_number(power_floor_w, "native power floor")
    if floor <= 0:
        _fail("native 3-D power floor must be positive")

    def source(role: str) -> dict[str, Any]:
        row = sources[role]
        required = ("dataset_id", "solution_id", "outer_index", "inner_index")
        if not isinstance(row, Mapping) or any(key not in row for key in required):
            _fail(f"native {role} source is missing exact dataset/solution/inner/outer identity")
        if (not isinstance(row["dataset_id"], str) or not row["dataset_id"].strip()
                or not isinstance(row["solution_id"], str) or not row["solution_id"].strip()
                or any(type(row[key]) is not int or row[key] < 1
                       for key in ("outer_index", "inner_index"))):
            _fail(f"native {role} source identity is malformed")
        return {key: row[key] for key in required}

    def fields(electric_prefix: str, magnetic_prefix: str, suffix: str = "") -> dict[str, Any]:
        return {"electric": {axis: f"ewfd.{electric_prefix}{axis}{suffix}" for axis in _AXES},
                "magnetic": {axis: f"ewfd.{magnetic_prefix}{axis}{suffix}" for axis in _AXES}}

    definition = {
        "schema_version": "1.0.0",
        "phasor_convention": {
            "time_dependence": "exp(-i omega t)",
            "complex_field_representation": "full_physical_complex_phasor_including_reconstructed_envelope_phase",
        },
        "output_surface": {"plane_id": "receiver_port",
                           "selection": {"component": "comp3d", "geometry": "geom3d",
                                         "tag": "sel3dOutputPort"},
                           "normal_sign": output_normal_sign},
        "signal": {"field_id": "w23_full3d_frequency_signal", "source": source("signal"),
                   "fields": fields("E", "H")},
        "reference_mode": {"mode_id": "w23_full3d_output_mode_1",
                           "mode_axis_parameter": "modeIndex",
                           "source": source("reference_mode"),
                           "fields": fields("Emode", "Hmode", "_2")},
        "incident_reference": {
            "reference_id": "w23_full3d_input_mode_1", "input_plane_id": "input_port",
            "selection": {"component": "comp3d", "geometry": "geom3d", "tag": "sel3dInputPort"},
            "normal_sign": input_normal_sign,
            "source": source("incident_reference"),
            "fields": fields("Emode", "Hmode", "_1"),
        },
        "power_floor": {"value": floor, "unit": "W"},
        "capture": {"aperture_id": "w23_full3d_output_core_capture",
                    "plane_id": "receiver_port",
                    "selection": {"component": "comp3d", "geometry": "geom3d",
                                  "tag": "sel3dOutputCoreCapture"},
                    "normal_sign": capture_normal_sign,
                    "incident_reference_id": "w23_full3d_input_mode_1"},
    }
    return definition


def resolve_full3d_native_sources(
    dataset_rows: Sequence[Mapping[str, Any]],
    index_by_tag: Mapping[str, Mapping[str, Any]],
    solver_tags_by_step: Mapping[str, Sequence[str]], *,
    frequency_hz: float,
) -> dict[str, Any]:
    """Resolve unique native frequency/BMA sources from live managed inventories.

    No tag naming convention or dataset order is used as a proxy for solver
    provenance. Every COMSOL Solution dataset must have complete native axes.
    """
    expected_frequency = _finite_number(frequency_hz, "registered optical frequency")
    if expected_frequency <= 0:
        _fail("registered optical frequency must be positive")
    solution_rows = [row for row in dataset_rows
                     if isinstance(row, Mapping)
                     and str(row.get("type_id", "")).lower() == "solution"]
    unresolved = [row.get("tag") for row in solution_rows
                  if not isinstance(row.get("tag"), str)
                  or row.get("tag") not in index_by_tag
                  or index_by_tag[row["tag"]].get("binding_complete") is not True]
    if unresolved:
        _fail("native source uniqueness cannot be proven while Solution dataset axes are incomplete: "
              + ", ".join(str(tag) for tag in unresolved))
    required_steps = {"signal": "freq3d", "reference_mode": "bmaOutput3d",
                      "incident_reference": "bmaInput3d"}
    role_solutions: dict[str, set[str]] = {}
    for role, step in required_steps.items():
        values = solver_tags_by_step.get(step)
        if not isinstance(values, (list, tuple)) or not values:
            _fail(f"generated native solver tree does not bind configured step {step}")
        if any(not isinstance(value, str) or not value.strip() for value in values):
            _fail(f"solver tree contains a malformed sequence tag for {step}")
        role_solutions[role] = set(values)

    chosen: dict[str, dict[str, Any]] = {}
    aliases = {"f", "freq", "frequency"}
    unit_scale = {"Hz": 1.0, "kHz": 1e3, "MHz": 1e6, "GHz": 1e9, "THz": 1e12}
    for role, allowed_solutions in role_solutions.items():
        candidates: list[dict[str, Any]] = []
        for row in solution_rows:
            tag = row.get("tag")
            if (not isinstance(tag, str) or row.get("component") != "comp3d"
                    or row.get("geometry") != "geom3d"):
                continue
            axes = index_by_tag.get(tag)
            if not isinstance(axes, Mapping) or axes.get("binding_complete") is not True:
                continue
            solution_id = axes.get("solution")
            if solution_id not in allowed_solutions or row.get("solution") != solution_id:
                continue
            params = axes.get("parameters")
            pairs = params.get("by_pair") if isinstance(params, Mapping) else None
            if not isinstance(pairs, Mapping):
                continue
            for pair_key, pair in pairs.items():
                if not isinstance(pair, Mapping):
                    continue
                names, values, units = (list(pair.get(key) or []) for key in ("names", "values", "units"))
                if len(names) != len(values) or len(names) != len(units):
                    continue
                frequency_slots = [index for index, name in enumerate(names)
                                   if str(name).strip().lower() in aliases]
                if len(frequency_slots) != 1:
                    continue
                fi = frequency_slots[0]
                unit = str(units[fi] or "").strip()
                value = values[fi]
                if (unit not in unit_scale or isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or not math.isfinite(float(value))
                        or not math.isclose(float(value) * unit_scale[unit], expected_frequency,
                                            rel_tol=1e-12, abs_tol=0.0)):
                    continue
                mode_slots = [index for index, name in enumerate(names)
                              if str(name).strip().lower() == "modeindex"]
                if role == "signal":
                    if mode_slots and (len(mode_slots) != 1 or values[mode_slots[0]] != 1):
                        continue
                elif (len(mode_slots) != 1 or isinstance(values[mode_slots[0]], bool)
                      or values[mode_slots[0]] != 1):
                    continue
                try:
                    outer, inner = (int(value) for value in str(pair_key).split(":"))
                except (TypeError, ValueError):
                    continue
                solnum = pair.get("solnum")
                if (outer < 1 or inner < 1 or type(solnum) is not int or solnum < 1):
                    continue
                candidates.append({"dataset_id": tag, "solution_id": solution_id,
                    "outer_index": outer, "inner_index": inner, "solnum": solnum,
                    "frequency_hz": expected_frequency,
                    "native_parameter_names": names, "native_parameter_values": values,
                    "native_parameter_units": units})
        if len(candidates) != 1:
            _fail(f"{role} requires exactly one native dataset/index tuple at the frozen frequency; found {len(candidates)}")
        chosen[role] = candidates[0]
    signal, mode, incident = (chosen[key] for key in
                              ("signal", "reference_mode", "incident_reference"))
    if (signal["outer_index"] != mode["outer_index"]
            or signal["outer_index"] != incident["outer_index"]
            or signal["inner_index"] != incident["inner_index"]):
        _fail("native full-3D source indices do not bind one shared outer and signal/input inner index")
    return {"status": "SOFTWARE_UNIQUE_FULL3D_NATIVE_SOURCE_BINDINGS",
            "native_result": "NOT_RUN", "sources": chosen,
            "solver_step_sequence_tags": {key: list(value) for key, value in solver_tags_by_step.items()},
            "caller_arrays_used": False}


def build_full3d_mode_overlap_dispatch(
    definition: Mapping[str, Any], *, project_id: str, model_ref: Mapping[str, Any],
    model_tag: str, revision: int, request_id: str, idempotency_key: str,
) -> dict[str, Any]:
    if not isinstance(definition, Mapping):
        _fail("native result.mode_overlap definition is required")
    binding = _managed_route_binding(
        project_id=project_id, model_ref=model_ref, model_tag=model_tag, revision=revision,
        request_id=request_id, idempotency_key=idempotency_key)
    return _operation_call("result.mode_overlap", {"definition": dict(definition)},
                           binding=binding,
                           dispatch_scope=("one managed native result.mode_overlap call; "
                                           "no caller field arrays; native result remains separately audited"))


def _expression_map(raw: Mapping[str, Any], contract: Mapping[str, Any]) -> dict[str, list[complex]]:
    if (raw.get("native_result") != "COMSOL_NATIVE_RAW"
            or raw.get("study_or_solver_invoked") is not False
            or raw.get("complex_readback") is not True):
        _fail("field source must be a fresh COMSOL native complex readback without hidden solve")
    rows = raw.get("expressions")
    if not isinstance(rows, list):
        _fail("native Interp response must include expression rows")
    result: dict[str, list[complex]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("expression"), str):
            _fail("native expression row is malformed")
        name = row["expression"]
        if name in result:
            _fail(f"native expression {name!r} is repeated")
        real, imag = row.get("real"), row.get("imag")
        count = contract["quadrature"]["sample_count"]
        if not isinstance(real, list) or not isinstance(imag, list) or len(real) != count or len(imag) != count:
            _fail(f"native field {name!r} sample axes differ from the exact quadrature contract")
        values = []
        for r, i in zip(real, imag):
            a, b = _finite_number(r, name + ".real"), _finite_number(i, name + ".imag")
            values.append(complex(a, b))
        result[name] = values
    if set(result) != set(contract["field_names"]):
        _fail("native Interp expression inventory differs from the frozen six-vector-plus-normal contract")
    return result


def _readback_coordinates(raw: Mapping[str, Any], expected: Sequence[Sequence[float]]) -> None:
    coordinates = raw.get("coordinates_m")
    if not isinstance(coordinates, list) or len(coordinates) != 3:
        _fail("native Interp must read back three global coordinate arrays in SI metres")
    count = len(expected)
    if any(not isinstance(axis, list) or len(axis) != count for axis in coordinates):
        _fail("native Interp coordinate axes do not match the frozen point count")
    for index, point in enumerate(expected):
        for axis in range(3):
            observed = _finite_number(coordinates[axis][index], "native coordinate")
            if not math.isclose(observed, float(point[axis]), rel_tol=0.0, abs_tol=2e-12):
                _fail("native Interp coordinate readback differs from the independently reconstructed local plane grid")


def validate_native_source_cohort_snapshot(
    contract: Mapping[str, Any], raw: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate before/after native stored-solution and explicit fixture identity.

    This is deliberately a fixture-scoped identity check. It does not claim
    arbitrary COMSOL model immutability or override live ModelRef/revision
    checks in the public managed routes.
    """
    source = contract.get("source") if isinstance(contract, Mapping) else None
    cohort = raw.get("source_cohort") if isinstance(raw, Mapping) else None
    if (not isinstance(source, Mapping) or not isinstance(cohort, Mapping)
            or cohort.get("schema_id") != "urn:comsol-mcp:w23:source-cohort-snapshot:1.0.0"
            or cohort.get("native_result") != "COMSOL_NATIVE_SOURCE_COHORT_SNAPSHOTS"):
        _fail("native field sample lacks the versioned before/after source-cohort readback")
    before, after = cohort.get("before"), cohort.get("after")
    if not isinstance(before, Mapping) or not isinstance(after, Mapping) or dict(before) != dict(after):
        _fail("native dataset, stored solution, or explicit fixture configuration changed during field sampling")
    dataset = before.get("dataset")
    solution = before.get("stored_solution")
    fixture = before.get("fixture_explicit_configuration")
    if (not isinstance(dataset, Mapping) or dataset.get("tag") != source.get("dataset_id")
            or dataset.get("feature_type") != "Solution"
            or not isinstance(dataset.get("properties"), Mapping)
            or dataset["properties"].get("solution") != source.get("solution_id")
            or not isinstance(solution, Mapping)
            or solution.get("solution_tag") != source.get("solution_id")
            or not isinstance(fixture, Mapping)
            or fixture.get("schema_id") != "urn:comsol-mcp:w23:fixture-explicit-config:1.0.0"
            or fixture.get("fixture_id") != "w23_full3d_fiber_ball_lens_vector_pml_v1"):
        _fail("native field sample cohort is not bound to its exact dataset/solution and W23 fixture")
    selected = solution.get("selected_tuple")
    expected_tuple = {"outer_index": source.get("outer_index"),
                      "inner_index": source.get("inner_index"),
                      "solnum": source.get("solnum"),
                      "solver_sequence_tag": source.get("solution_id")}
    if selected != expected_tuple:
        _fail("native stored-solution selected tuple differs from the raw-field contract")
    computation_date = solution.get("computation_date_ms")
    computation_version = solution.get("computation_version")
    parameter_axis = solution.get("parameter_axis")
    solution_info = solution.get("solution_info")
    if (type(computation_date) is not int or computation_date <= 0
            or not isinstance(computation_version, str) or not computation_version.strip()
            or not isinstance(parameter_axis, list) or not isinstance(solution_info, Mapping)
            or solution_info.get("is_valid") is not True
            or solution_info.get("solver_sequence_is_empty") is not False
            or not isinstance(solution_info.get("solution_pairs"), list)):
        _fail("native stored computation date/version or SolutionInfo identity is missing")
    pair_rows = solution_info["solution_pairs"]
    if selected not in pair_rows:
        _fail("selected native source tuple is absent from the complete SolutionInfo pair readback")
    seen_parameters: set[str] = set()
    for row in parameter_axis:
        if (not isinstance(row, Mapping) or not isinstance(row.get("name"), str)
                or not row["name"].strip() or row["name"] in seen_parameters):
            _fail("native stored-solution parameter axis is malformed or duplicated")
        value = _finite_number(row.get("value"), "native stored-solution parameter value")
        seen_parameters.add(row["name"])
        if not math.isfinite(value):
            _fail("native stored-solution parameter axis contains a nonfinite value")

    parameters = fixture.get("parameters")
    expected_parameter_tags = {
        "lambda0", "f0", "w23Ncore", "w23Nclad", "w23Nlens", "w23CoreR", "w23CladR",
        "w23LensR", "w23AirHalfY", "w23AirHalfZ", "w23PmlT", "w23XIn", "w23XInEnd",
        "w23XOutStart", "w23XOut", "w23XDomainMax", "w23OutDy", "w23OutDz",
        "w23ThetaY", "w23ThetaZ",
    }
    if (not isinstance(parameters, Mapping) or not expected_parameter_tags.issubset(parameters)
            or any(not isinstance(parameters.get(key), str) or not parameters[key].strip()
                   for key in expected_parameter_tags)):
        _fail("native explicit W23 geometry/frequency parameter readback is incomplete")
    geometry = fixture.get("geometry")
    bbox = geometry.get("bounding_box") if isinstance(geometry, Mapping) else None
    bbox_values = [item for row in bbox for item in row] if (
        isinstance(bbox, list) and bbox and all(isinstance(row, list) for row in bbox)) else (
            bbox if isinstance(bbox, list) else [])
    if (not isinstance(geometry, Mapping) or geometry.get("dimension") != 3
            or geometry.get("length_unit") != "um"
            or type(geometry.get("domain_count")) is not int or geometry["domain_count"] <= 0
            or not isinstance(bbox, list) or len(bbox_values) != 6
            or any(isinstance(item, bool) or not isinstance(item, (int, float))
                   or not math.isfinite(float(item)) for item in bbox_values)):
        _fail("native explicit W23 geometry readback is incomplete")
    mesh = fixture.get("mesh")
    size_properties = mesh.get("size_properties") if isinstance(mesh, Mapping) else None
    requested_size = size_properties.get("requested_properties") if isinstance(size_properties, Mapping) else None
    if (not isinstance(mesh, Mapping) or mesh.get("tag") != "mesh3d"
            or mesh.get("geometry") != "geom3d"
            or type(mesh.get("elements")) is not int or mesh["elements"] <= 0
            or not isinstance(requested_size, Mapping)):
        _fail("native explicit fixture mesh tag/element readback is incomplete")
    for key in ("custom", "hmax", "hmin"):
        row = requested_size.get(key)
        if (not isinstance(row, Mapping) or row.get("has_property_exact") is not True
                or not isinstance(row.get("string_readback"), str)
                or not row["string_readback"].strip()):
            _fail(f"native fixture mesh {key} setting lacks actual readback")

    materials = fixture.get("materials")
    if (not isinstance(materials, list) or not materials
            or any(not isinstance(row, Mapping) or not isinstance(row.get("tag"), str)
                   or not isinstance(row.get("selection_tag"), str)
                   or not isinstance(row.get("domain_ids"), list) or not row["domain_ids"]
                   or not isinstance(row.get("relative_permittivity"), list)
                   or not isinstance(row.get("relative_permeability"), list)
                   or len(row["relative_permittivity"]) != 3
                   or len(row["relative_permeability"]) != 3
                   or any(not isinstance(matrix_row, list) or len(matrix_row) != 3
                          or any(not isinstance(cell, str) or not cell.strip() for cell in matrix_row)
                          for matrix_row in [*row["relative_permittivity"], *row["relative_permeability"]])
                   for row in materials)
            or {row["tag"] for row in materials} != {
                "matCore3d", "matCoreOut3d", "matCladIn3d", "matCladOut3d",
                "matLens3d", "matAir3d", "matPmlAir3d"}):
        _fail("native explicit W23 material/selection/matrix readback is incomplete")
    physics = fixture.get("physics")
    features = physics.get("features") if isinstance(physics, Mapping) else None
    if (not isinstance(physics, Mapping) or physics.get("tag") != "ewfd"
            or physics.get("feature_type") != "ElectromagneticWaves"
            or not isinstance(features, list)
            or not {"portIn3d", "portOut3d"}.issubset(
                {row.get("tag") for row in features if isinstance(row, Mapping)})):
        _fail("native explicit EWFD feature/Port configuration readback is incomplete")
    ports_by_tag: dict[str, Mapping[str, Any]] = {}
    for row in features:
        if not isinstance(row, Mapping) or not isinstance(row.get("tag"), str) \
                or not isinstance(row.get("feature_type"), str) \
                or not isinstance(row.get("selection_ids"), list):
            _fail("native explicit EWFD feature inventory is malformed")
        if row.get("feature_type") == "Port":
            port_properties = row.get("port_properties")
            requested = port_properties.get("requested_properties") if isinstance(port_properties, Mapping) else None
            if not isinstance(requested, Mapping):
                _fail("native Numeric Port feature properties are not read back")
            for name in ("PortType", "PortName", "PortModeNumber", "PortOrientation"):
                prop = requested.get(name)
                if (not isinstance(prop, Mapping) or prop.get("has_property_exact") is not True
                        or not isinstance(prop.get("string_readback"), str)
                        or not prop["string_readback"].strip()):
                    _fail(f"native Port {name} lacks actual configuration readback")
            ports_by_tag[row["tag"]] = requested
    for tag, port_name in (("portIn3d", "1"), ("portOut3d", "2")):
        port = ports_by_tag.get(tag)
        if (not isinstance(port, Mapping)
                or port["PortType"].get("string_readback") != "Numeric"
                or port["PortName"].get("string_readback") != port_name
                or port["PortModeNumber"].get("string_readback") != "1"):
            _fail("native Numeric Port 1/2 mode configuration differs from frozen fixture")

    selections = fixture.get("selections")
    selection = selections.get(contract["plane"].get("selection_tag")) if isinstance(selections, Mapping) else None
    if (not isinstance(selection, Mapping) or selection.get("entity_dimension") != 2
            or not isinstance(selection.get("entity_ids"), list) or not selection["entity_ids"]
            or any(type(item) is not int or item < 1 for item in selection["entity_ids"])
            or len(set(selection["entity_ids"])) != len(selection["entity_ids"])):
        _fail("native source-cohort snapshot omits the exact nonempty sampled boundary identity")
    if isinstance(contract["plane"].get("boundary_ids"), list) \
            and sorted(selection["entity_ids"]) != sorted(contract["plane"]["boundary_ids"]):
        _fail("native source-cohort sampled boundary IDs differ from the frozen plane contract")
    plane_tag = contract["plane"].get("selection_tag")
    if plane_tag == "sel3dInputPort":
        expected_port_ids = next((row["selection_ids"] for row in features
                                  if isinstance(row, Mapping) and row.get("tag") == "portIn3d"), None)
        if not isinstance(expected_port_ids, list) or sorted(expected_port_ids) != sorted(selection["entity_ids"]):
            _fail("native input sample selection differs from the configured Numeric Port 1 boundaries")
    elif plane_tag == "sel3dOutputPort":
        expected_port_ids = next((row["selection_ids"] for row in features
                                  if isinstance(row, Mapping) and row.get("tag") == "portOut3d"), None)
        if not isinstance(expected_port_ids, list) or sorted(expected_port_ids) != sorted(selection["entity_ids"]):
            _fail("native output sample selection differs from the configured Numeric Port 2 boundaries")
    elif plane_tag == "sel3dOutputCoreCapture":
        output_port_ids = next((row["selection_ids"] for row in features
                                 if isinstance(row, Mapping) and row.get("tag") == "portOut3d"), None)
        if not isinstance(output_port_ids, list) or not set(selection["entity_ids"]).issubset(output_port_ids):
            _fail("native core-capture selection is not contained in the configured Numeric Port 2 boundaries")

    study_steps = fixture.get("study_steps")
    study_tag = solution.get("study_tag")
    if not isinstance(study_steps, Mapping) or study_tag not in study_steps \
            or not isinstance(study_steps[study_tag], list) or not study_steps[study_tag]:
        _fail("native stored-solution producer Study configuration is not in the fixture snapshot")
    pml = fixture.get("pml")
    pml_requested = pml.get("requested_properties") if isinstance(pml, Mapping) else None
    if not isinstance(pml_requested, Mapping):
        _fail("native explicit PML configuration readback is incomplete")
    for key in ("ScalingType", "stretchingType", "typicalWavelength"):
        row = pml_requested.get(key)
        if (not isinstance(row, Mapping) or row.get("has_property_exact") is not True
                or not isinstance(row.get("string_readback"), str)
                or not row["string_readback"].strip()):
            _fail(f"native PML {key} setting lacks actual readback")
    fixture_digest = _sha256(dict(fixture))
    storage_digest = _sha256(dict(solution))
    return {
        "status": "NATIVE_SOURCE_COHORT_SNAPSHOT_VALIDATED",
        "scope": "explicit W23 fixture configuration and exact stored solution; not arbitrary model immutability",
        "dataset_tag": dataset["tag"], "solution_tag": solution["solution_tag"],
        "study_tag": study_tag, "selected_tuple": dict(selected),
        "computation_date_ms": computation_date, "computation_version": computation_version,
        "fixture_explicit_configuration_sha256": fixture_digest,
        "stored_solution_identity_sha256": storage_digest,
    }


def validate_native_field_readback(
    contract: Mapping[str, Any], raw: Mapping[str, Any], *,
    expected_quadrature: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(contract, Mapping) or not isinstance(raw, Mapping):
        _fail("native field response and frozen contract are required")
    expected_id = contract.get("contract_id")
    if raw.get("contract_id") != expected_id or raw.get("quadrature_sha256") != contract.get("quadrature_sha256"):
        _fail("native field response differs from the exact field/quadrature contract")
    if raw.get("source") != contract.get("source") or raw.get("plane") != contract.get("plane"):
        _fail("native field response has a foreign dataset/index or port plane")
    if raw.get("native_result") != "COMSOL_NATIVE_RAW":
        _fail("software or mock field values cannot be promoted to native readback")
    cleanup = raw.get("cleanup")
    prefix = "w23bm" if contract.get("provenance_schema") == "w23.full3d.numeric_port_bma_basis_pair.v1" else "w23rf"
    contract_id = contract.get("contract_id")
    expected_cleanup_tags = ([prefix + contract_id[0:10] + suffix for suffix in ("e", "h", "n")]
                             if isinstance(contract_id, str) and re.fullmatch(r"[0-9a-f]{64}", contract_id)
                             else [])
    if (not isinstance(cleanup, Mapping)
            or cleanup.get("created_count") != 3 or cleanup.get("expected_count") != 3
            or cleanup.get("removed") is not True or cleanup.get("cleanup_failed") is not False
            or cleanup.get("tags") != expected_cleanup_tags or cleanup.get("error") != ""):
        _fail("all three request-owned SI Interp tags must have exact successful native cleanup readback")
    if raw.get("sample_count") != contract["quadrature"]["sample_count"]:
        _fail("native field readback sample count differs from contract")
    expected_units = _field_unit_groups(contract.get("field_names", []))
    if (contract.get("unit_groups") != expected_units or raw.get("units_preserved") is not True
            or raw.get("unit_readback") != {key: value["unit"] for key, value in expected_units.items()}):
        _fail("native field readback does not prove the separate SI E/H/normal unit groups")
    unit_by_expression = {expression: (group, values["unit"])
                          for group, values in expected_units.items()
                          for expression in values["expressions"]}
    rows = raw.get("expressions")
    if (not isinstance(rows, list) or any(not isinstance(row, Mapping)
            or unit_by_expression.get(row.get("expression")) != (row.get("unit_group"), row.get("unit"))
            for row in rows)):
        _fail("native field expression rows omit or alter their unit-group readback")
    _readback_coordinates(raw, expected_quadrature["coordinates_m"])
    cohort_validation = validate_native_source_cohort_snapshot(contract, raw)
    fields = _expression_map(raw, contract)
    axis = _vector3(contract["plane"]["axis_xyz"], "expected plane axis")
    sign = contract["plane"]["native_normal_sign"]
    normal_values = [fields[name] for name in _NORMAL_FIELDS]
    expected_normal = tuple(sign * item for item in axis)
    for sample in range(contract["quadrature"]["sample_count"]):
        observed = tuple(normal_values[index][sample] for index in range(3))
        if max(abs(item.imag) for item in observed) > 1e-10:
            _fail("native surface normal readback must be real")
        real_normal = tuple(item.real for item in observed)
        norm = math.sqrt(sum(item * item for item in real_normal))
        dot = sum(real_normal[index] * expected_normal[index] for index in range(3))
        if abs(norm - 1.0) > 1e-8 or abs(dot - 1.0) > 1e-6:
            _fail("native sampled boundary normals are not consistently outward along the registered port axis")
    return {"status": "NATIVE_RAW_VECTOR_READBACK_VALIDATED", "native_result": "COMSOL_NATIVE_RAW",
            "contract_id": expected_id, "source": dict(contract["source"]),
            "plane": dict(contract["plane"]), "sample_count": contract["quadrature"]["sample_count"],
            "field_names": list(contract["field_names"]), "cleanup": dict(cleanup),
            "source_cohort": cohort_validation,
            "coordinate_sha256": _sha256(expected_quadrature["coordinates_m"]),
            "normal_orientation": "native normals match the measured outward sign; declared sign maps to physical propagation direction"}


def _weighted_vector_inner(
    left: Sequence[Sequence[complex]], right: Sequence[Sequence[complex]],
    weights: Sequence[float],
) -> complex:
    if len(left) != 3 or len(right) != 3 or any(
            len(left[axis]) != len(weights) or len(right[axis]) != len(weights)
            for axis in range(3)):
        _fail("paired vector samples and quadrature weights have inconsistent axes")
    return sum((weights[index] * sum(
        left[axis][index].conjugate() * right[axis][index] for axis in range(3))
        for index in range(len(weights))), 0j)


def _weighted_vector_rms(values: Sequence[Sequence[complex]], weights: Sequence[float]) -> float:
    total_weight = math.fsum(weights)
    if not math.isfinite(total_weight) or total_weight <= 0.0:
        _fail("paired field quadrature must have positive finite total area")
    norm2 = _weighted_vector_inner(values, values, weights).real
    if not math.isfinite(norm2) or norm2 < 0.0:
        _fail("paired vector field has an invalid Hermitian norm")
    result = math.sqrt(norm2 / total_weight)
    if not math.isfinite(result):
        _fail("paired vector RMS norm is nonfinite")
    return result


def _basis_mapping_group_diagnostic(
    bma: Sequence[Sequence[complex]], port: Sequence[Sequence[complex]],
    weights: Sequence[float], *, group: str,
) -> dict[str, Any]:
    bma_rms = _weighted_vector_rms(bma, weights)
    port_rms = _weighted_vector_rms(port, weights)
    if bma_rms <= 0.0 or port_rms <= 0.0:
        _fail(f"complete {group} vector field must have a positive finite RMS norm")
    scale = max(bma_rms, port_rms)
    difference = [[bma[axis][index] - port[axis][index]
                   for index in range(len(weights))] for axis in range(3)]
    residual = _weighted_vector_rms(difference, weights) / scale
    if not math.isfinite(residual):
        _fail(f"normalized {group} vector residual is nonfinite")
    numerator = _weighted_vector_inner(port, bma, weights)
    overlap = numerator / (bma_rms * port_rms * math.fsum(weights))
    if not math.isfinite(overlap.real) or not math.isfinite(overlap.imag):
        _fail(f"normalized {group} Hermitian overlap is nonfinite")
    return {"group": group, "bma_vector_rms": bma_rms, "port_mode_vector_rms": port_rms,
            "scale": scale, "unadjusted_normalized_residual": residual,
            "normalized_hermitian_overlap": {"real": overlap.real, "imag": overlap.imag,
                                              "magnitude": abs(overlap)},
            "phase_corrected_or_component_permuted": False}


def _subspace_projection_diagnostic(
    basis: Sequence[Sequence[Sequence[complex]]], target: Sequence[Sequence[complex]],
    weights: Sequence[float], *, group: str,
) -> dict[str, Any]:
    """Report a two-vector Hermitian projection without using it as a pass gate."""
    if len(basis) != 2:
        _fail("subspace diagnostic requires exactly two SolutionInfo basis vectors")
    g00 = _weighted_vector_inner(basis[0], basis[0], weights).real
    g11 = _weighted_vector_inner(basis[1], basis[1], weights).real
    g01 = _weighted_vector_inner(basis[0], basis[1], weights)
    b0 = _weighted_vector_inner(basis[0], target, weights)
    b1 = _weighted_vector_inner(basis[1], target, weights)
    target_norm2 = _weighted_vector_inner(target, target, weights).real
    determinant = g00 * g11 - abs(g01) ** 2
    scale = max(g00 * g11, 1e-300)
    if (not all(math.isfinite(item) for item in
                (g00, g11, g01.real, g01.imag, b0.real, b0.imag,
                 b1.real, b1.imag, target_norm2, determinant))
            or g00 <= 0.0 or g11 <= 0.0 or target_norm2 <= 0.0):
        _fail(f"{group} subspace Gram data are nonfinite or zero")
    if determinant <= 1e-12 * scale:
        return {"group": group, "status": "RANK_DEFICIENT_GRAM_DIAGNOSTIC",
                "projection_fraction": None, "gram_determinant": determinant,
                "decision_use": "DIAGNOSTIC_ONLY_NOT_A_ONE_TO_ONE_MAPPING_GATE"}
    x0 = (g11 * b0 - g01 * b1) / determinant
    x1 = (-g01.conjugate() * b0 + g00 * b1) / determinant
    projected_norm2 = (b0.conjugate() * x0 + b1.conjugate() * x1).real
    fraction = projected_norm2 / target_norm2
    if not math.isfinite(fraction):
        _fail(f"{group} subspace projection diagnostic is nonfinite")
    return {"group": group, "status": "COMPUTED_HERMITIAN_TWO_BASIS_PROJECTION",
            "projection_fraction": fraction, "gram_determinant": determinant,
            "decision_use": "DIAGNOSTIC_ONLY_NOT_A_ONE_TO_ONE_MAPPING_GATE"}


def _reconstruct_contract_quadrature(
    contract: Mapping[str, Any], observed: Mapping[str, Any],
) -> None:
    plane = contract.get("plane")
    shape = plane.get("aperture_shape") if isinstance(plane, Mapping) else None
    quadrature_metadata = contract.get("quadrature")
    if not isinstance(plane, Mapping) or not isinstance(quadrature_metadata, Mapping):
        _fail("paired field contract omitted its immutable local plane/quadrature definition")
    if shape == "circular":
        reconstructed = circular_port_quadrature(
            plane.get("center_xyz_m"), plane.get("axis_xyz"), plane.get("sample_radius_m"),
            radial_intervals=quadrature_metadata.get("radial_intervals"),
            angular_points=quadrature_metadata.get("angular_points"))
    elif shape == "rectangle":
        reconstructed = rectangular_port_quadrature(
            plane.get("center_xyz_m"), plane.get("axis_xyz"), plane.get("half_widths_uv_m"),
            u_intervals=quadrature_metadata.get("u_intervals"),
            v_intervals=quadrature_metadata.get("v_intervals"))
    else:
        _fail("paired field contract does not identify a supported aperture quadrature")
    if (reconstructed.get("quadrature_sha256") != contract.get("quadrature_sha256")
            or observed.get("quadrature_sha256") != reconstructed.get("quadrature_sha256")
            or observed.get("coordinates_m") != reconstructed.get("coordinates_m")
            or observed.get("weights_m2") != reconstructed.get("weights_m2")
            or observed.get("weight_sum_m2") != reconstructed.get("weight_sum_m2")):
        _fail("paired coordinates/weights do not exactly reconstruct from the frozen quadrature contract")


def validate_full3d_bma_basis_mapping_samples(
    contracts: Sequence[Mapping[str, Any]], raw_readbacks: Sequence[Mapping[str, Any]],
    quadratures: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate paired native sample schemas and calculate fixed-axis diagnostics.

    This software validator never authenticates the public operation envelope
    and never promotes a mapping to VERIFIED. The caller must independently
    retain the managed dispatch/job receipt for each raw Interp operation.
    """
    if (not isinstance(contracts, Sequence) or isinstance(contracts, (str, bytes))
            or not isinstance(raw_readbacks, Sequence) or isinstance(raw_readbacks, (str, bytes))
            or not isinstance(quadratures, Sequence) or isinstance(quadratures, (str, bytes))
            or len(contracts) != 2 or len(raw_readbacks) != 2 or len(quadratures) != 2):
        _fail("paired field validation requires exactly two contracts, raw readbacks, and quadratures")
    checked: list[dict[str, Any]] = []
    field_maps: list[dict[str, list[complex]]] = []
    weights_by_row: list[list[float]] = []
    basis_source_rows: list[Mapping[str, Any]] = []
    for expected_ordinal, (contract, raw, quadrature) in enumerate(
            zip(contracts, raw_readbacks, quadratures), start=1):
        if (not isinstance(contract, Mapping) or not isinstance(raw, Mapping)
                or not isinstance(quadrature, Mapping)
                or contract.get("role") != "bma_basis_mapping_pair"
                or contract.get("provenance_schema") != "w23.full3d.numeric_port_bma_basis_pair.v1"
                or contract.get("field_names") != list(_VECTOR_FIELDS["bma_basis_mapping_pair"] + _NORMAL_FIELDS)
                or contract.get("field_groups") != {
                    "generic_bma_eigensolution": list(_VECTOR_FIELDS["signal"]),
                    "configured_numeric_port_mode_field": list(_VECTOR_FIELDS["reference_mode"]),
                    "shared_surface_normal": list(_NORMAL_FIELDS)}
                or contract.get("mapping_policy") != BMA_FIELD_MAPPING_POLICY
                or contract.get("unit_groups") != _BMA_PAIR_UNIT_GROUPS
                or not isinstance(contract.get("unit_readback_policy"), Mapping)
                or contract["unit_readback_policy"].get("source") != COMSOL_INTERP_UNIT_KB_EVIDENCE
                or contract.get("basis_ordinal_mapping") != "UNVERIFIED_NATIVE_FIELD_MAPPING_REQUIRED"
                or contract.get("numeric_port_mode_field_mapping") != "UNVERIFIED"):
            _fail("paired BMA contract is not the exact versioned, unverified field identity probe")
        identity = {key: value for key, value in contract.items()
                    if key not in {"contract_id", "native_result", "study_or_solver_invoked"}}
        if contract.get("contract_id") != _sha256(identity):
            _fail("paired BMA contract identity/hash is inconsistent")
        basis_axis = contract.get("basis_axis")
        port_axis = contract.get("port_mode_axis")
        source = contract.get("source")
        contract_plane = contract.get("plane")
        if (not isinstance(basis_axis, Mapping) or basis_axis.get("ordinal") != expected_ordinal
                or basis_axis.get("axis") != "ordered SolutionInfo.getSolnum(outer,true) row ordinal"
                or not isinstance(port_axis, Mapping)
                or port_axis.get("feature_tag") != "portOut3d"
                or port_axis.get("feature_type") != "Port"
                or port_axis.get("port_type") != "Numeric"
                or port_axis.get("port_name") != "2"
                or port_axis.get("port_mode_number_readback") != "1"
                or port_axis.get("selection_tag") != "sel3dOutputPort"
                or not isinstance(contract_plane, Mapping)
                or port_axis.get("selection_tag") != contract_plane.get("selection_tag")
                or not isinstance(port_axis.get("boundary_ids"), list)
                or not port_axis.get("boundary_ids")
                or any(type(item) is not int or item < 1 for item in port_axis["boundary_ids"])
                or len(set(port_axis["boundary_ids"])) != len(port_axis["boundary_ids"])
                or port_axis.get("field_suffix_semantics") != COMSOL_PORT_MODE_FIELD_KB_EVIDENCE
                or not isinstance(source, Mapping)
                or basis_axis.get("outer_index") != source.get("outer_index")
                or basis_axis.get("inner_index") != source.get("inner_index")
                or basis_axis.get("solnum") != source.get("solnum")
                or basis_axis.get("solution_id") != source.get("solution_id")
                or basis_axis.get("solver_sequence_tag") != source.get("solution_id")):
            _fail("basis ordinal and Numeric Port mode number axes are mixed or incomplete")
        basis_source_rows.append(source)
        if (contract.get("quadrature_sha256") != quadrature.get("quadrature_sha256")
                or not isinstance(quadrature.get("coordinates_m"), list)
                or not isinstance(quadrature.get("weights_m2"), list)
                or len(quadrature["coordinates_m"]) != len(quadrature["weights_m2"])
                or contract.get("plane") != contracts[0].get("plane")):
            _fail("paired BMA field contracts do not use one exact plane/coordinate/weight grid")
        _reconstruct_contract_quadrature(contract, quadrature)
        weights = [_finite_number(value, "BMA mapping quadrature weight")
                   for value in quadrature["weights_m2"]]
        if any(value < 0.0 for value in weights) or math.fsum(weights) <= 0.0:
            _fail("BMA mapping quadrature weights must be nonnegative with positive total area")
        if raw.get("units_preserved") is not True:
            _fail("paired BMA raw sample omitted explicit native unit readback")
        if raw.get("unit_readback") != {key: value["unit"] for key, value in _BMA_PAIR_UNIT_GROUPS.items()}:
            _fail("paired BMA unit-property readback differs from the frozen V/m, A/m, and dimensionless groups")
        if (raw.get("basis_axis") != basis_axis or raw.get("port_mode_axis") != port_axis
                or raw.get("basis_ordinal_mapping") != "UNVERIFIED_NATIVE_FIELD_MAPPING_REQUIRED"
                or raw.get("numeric_port_mode_field_mapping") != "UNVERIFIED"
                or raw.get("field_mapping_status") != "UNVERIFIED"):
            _fail("native paired raw readback changed or omitted the separate mode/basis axis evidence")
        rows = raw.get("expressions")
        if not isinstance(rows, list):
            _fail("paired BMA Interp response omitted raw expression rows")
        expected_group = {name: ("electric", "V/m") for name in _BMA_PAIR_E_FIELDS}
        expected_group.update({name: ("magnetic", "A/m") for name in _BMA_PAIR_H_FIELDS})
        expected_group.update({name: ("normal", "1") for name in _BMA_PAIR_NORMAL_FIELDS})
        for row in rows:
            if (not isinstance(row, Mapping) or row.get("expression") not in expected_group
                    or row.get("unit_group") != expected_group[row["expression"]][0]
                    or row.get("unit") != expected_group[row["expression"]][1]):
                _fail("paired BMA sample row has a missing/foreign expression or unit")
        validation = validate_native_field_readback(
            contract, raw, expected_quadrature=quadrature)
        fields = _expression_map(raw, contract)
        # Require the paired field extraction to preserve both real/imag arrays
        # and their explicitly read-back unit group for every requested field.
        if set(row["expression"] for row in rows) != set(expected_group) or len(rows) != len(expected_group):
            _fail("paired BMA expression inventory is incomplete or duplicated")
        checked.append({"basis_ordinal": expected_ordinal,
                        "contract_id": contract["contract_id"],
                        "source": dict(source), "readback_validation": validation,
                        "raw_readback_sha256": _sha256(raw)})
        field_maps.append(fields)
        weights_by_row.append(weights)

    first_source, second_source = basis_source_rows
    if (first_source.get("dataset_id") != second_source.get("dataset_id")
            or first_source.get("solution_id") != second_source.get("solution_id")
            or first_source.get("outer_index") != second_source.get("outer_index")
            or first_source.get("inner_index") == second_source.get("inner_index")
            or first_source.get("solnum") == second_source.get("solnum")
            or contracts[0].get("quadrature_sha256") != contracts[1].get("quadrature_sha256")
            or weights_by_row[0] != weights_by_row[1]):
        _fail("paired basis rows must share dataset/solution/outer/grid and use distinct inner/solnum indices")

    weights = weights_by_row[0]
    if not math.isclose(math.fsum(weights), quadratures[0].get("weight_sum_m2"),
                        rel_tol=1e-12, abs_tol=1e-30):
        _fail("paired field quadrature area weights differ from their frozen sum")
    e_names_bma = tuple(f"ewfd.E{axis}" for axis in _AXES)
    e_names_port = tuple(f"ewfd.Emode{axis}_2" for axis in _AXES)
    h_names_bma = tuple(f"ewfd.H{axis}" for axis in _AXES)
    h_names_port = tuple(f"ewfd.Hmode{axis}_2" for axis in _AXES)

    def group(fields: Mapping[str, list[complex]], names: Sequence[str]) -> tuple[list[complex], ...]:
        return tuple(fields[name] for name in names)

    per_basis: list[dict[str, Any]] = []
    candidate_matches: list[int] = []
    for ordinal, fields in enumerate(field_maps, start=1):
        electric = _basis_mapping_group_diagnostic(
            group(fields, e_names_bma), group(fields, e_names_port), weights, group="E")
        magnetic = _basis_mapping_group_diagnostic(
            group(fields, h_names_bma), group(fields, h_names_port), weights, group="H")
        electric["fixed_component_candidate_match"] = (
            electric["unadjusted_normalized_residual"] <= BMA_FIELD_MAPPING_POLICY["normalized_residual_limit"])
        magnetic["fixed_component_candidate_match"] = (
            magnetic["unadjusted_normalized_residual"] <= BMA_FIELD_MAPPING_POLICY["normalized_residual_limit"])
        matches = electric["fixed_component_candidate_match"] and magnetic["fixed_component_candidate_match"]
        if matches:
            candidate_matches.append(ordinal)
        per_basis.append({"basis_ordinal": ordinal, "source": dict(basis_source_rows[ordinal - 1]),
                          "E": electric, "H": magnetic,
                          "fixed_component_candidate_match": matches})

    # The normalized E and H correlations contribute one shared diagnostic
    # phase; this scalar is reported, never applied to either field group.
    phase_diagnostics = []
    for ordinal, fields in enumerate(field_maps, start=1):
        e_bma, e_port = group(fields, e_names_bma), group(fields, e_names_port)
        h_bma, h_port = group(fields, h_names_bma), group(fields, h_names_port)
        e_norm = _weighted_vector_rms(e_bma, weights) * _weighted_vector_rms(e_port, weights) * math.fsum(weights)
        h_norm = _weighted_vector_rms(h_bma, weights) * _weighted_vector_rms(h_port, weights) * math.fsum(weights)
        corr_e = _weighted_vector_inner(e_port, e_bma, weights) / e_norm
        corr_h = _weighted_vector_inner(h_port, h_bma, weights) / h_norm
        shared = corr_e + corr_h
        shared_norm = abs(shared)
        phase_diagnostics.append({
            "basis_ordinal": ordinal,
            "E_normalized_correlation": {"real": corr_e.real, "imag": corr_e.imag},
            "H_normalized_correlation": {"real": corr_h.real, "imag": corr_h.imag},
            "one_shared_phase_diagnostic": ({"real": shared.real / shared_norm,
                                             "imag": shared.imag / shared_norm}
                                            if shared_norm > 0.0 else None),
            "E_H_phase_disagreement_magnitude": abs(corr_e - corr_h),
            "applied_to_samples": False,
            "decision_use": "DIAGNOSTIC_ONLY_NOT_A_FIXED_COMPONENT_PASS_GATE",
        })

    subspace = []
    basis_e = [group(fields, e_names_bma) for fields in field_maps]
    basis_h = [group(fields, h_names_bma) for fields in field_maps]
    for ordinal, fields in enumerate(field_maps, start=1):
        subspace.append({
            "port_sample_basis_ordinal": ordinal,
            "E": _subspace_projection_diagnostic(basis_e, group(fields, e_names_port), weights, group="E"),
            "H": _subspace_projection_diagnostic(basis_h, group(fields, h_names_port), weights, group="H"),
            "use": "DIAGNOSTIC_ONLY_NOT_A_ONE_TO_ONE_MAPPING_GATE",
        })
    return {
        "status": "SOFTWARE_PAIRED_BMA_FIELD_SAMPLE_DIAGNOSTICS_VALID",
        "evidence_scope": "software schema/numerical validation only; managed operation envelope must be retained separately",
        "native_result": "NOT_RUN",
        "field_mapping_status": "UNVERIFIED",
        "numeric_port_mode_field_mapping": "UNVERIFIED",
        "basis_ordinal_mapping": "UNVERIFIED_NATIVE_FIELD_MAPPING_REQUIRED",
        "axis_separation": {"basis_axis": "SolutionInfo ordered row ordinal plus inner/solnum",
                            "numeric_port_axis": {"port_name": "2", "PortModeNumber": 1}},
        "policy": dict(BMA_FIELD_MAPPING_POLICY),
        "sample_receipts": checked,
        "per_basis_fixed_component_diagnostics": per_basis,
        "candidate_fixed_component_basis_ordinals": candidate_matches,
        "one_to_one_candidate_basis_ordinal": candidate_matches[0] if len(candidate_matches) == 1 else None,
        "common_phase_diagnostics": phase_diagnostics,
        "subspace_projection_diagnostics": subspace,
        "phase_rotation_or_component_permutation_applied": False,
    }


def _cross_dot(e: Sequence[complex], h: Sequence[complex], normal: Sequence[float], *,
               conjugate_e: bool = False, conjugate_h: bool = False) -> complex:
    values_e = [value.conjugate() if conjugate_e else value for value in e]
    values_h = [value.conjugate() if conjugate_h else value for value in h]
    cross = (values_e[1] * values_h[2] - values_e[2] * values_h[1],
             values_e[2] * values_h[0] - values_e[0] * values_h[2],
             values_e[0] * values_h[1] - values_e[1] * values_h[0])
    return sum(cross[index] * normal[index] for index in range(3))


def _field_vector(fields: Mapping[str, list[complex]], role: str, prefix: str,
                  index: int) -> tuple[complex, complex, complex]:
    if role in {"signal", "capture_signal"}:
        expression = lambda axis: f"ewfd.{prefix}{axis}"
    elif role == "reference_mode":
        expression = lambda axis: f"ewfd.{prefix}mode{axis}_2"
    elif role == "incident_reference":
        expression = lambda axis: f"ewfd.{prefix}mode{axis}_1"
    else:
        _fail("field vector role is not registered")
    return tuple(fields[expression(axis)][index] for axis in _AXES)  # type: ignore[return-value]


def _integrate(values: Sequence[complex], weights: Sequence[float]) -> complex:
    if len(values) != len(weights):
        _fail("quadrature field and area-weight axes differ")
    return sum((value * weight for value, weight in zip(values, weights)), 0j)


def independent_mode_overlap_integrals(
    signal_raw: Mapping[str, Any], mode_raw: Mapping[str, Any], incident_raw: Mapping[str, Any], *,
    signal_contract: Mapping[str, Any], mode_contract: Mapping[str, Any], incident_contract: Mapping[str, Any],
    signal_quadrature: Mapping[str, Any], mode_quadrature: Mapping[str, Any],
    incident_quadrature: Mapping[str, Any], power_floor_w: float = 1e-12,
    capture_raw: Mapping[str, Any] | None = None,
    capture_contract: Mapping[str, Any] | None = None,
    capture_quadrature: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Independently recompute full-vector powers and reciprocal numerator in W."""
    for label, contract in (("signal", signal_contract), ("mode", mode_contract), ("incident", incident_contract)):
        if contract.get("role") != {"signal": "signal", "mode": "reference_mode", "incident": "incident_reference"}[label]:
            _fail(f"{label} raw-field contract has the wrong source role")
    validations = [
        validate_native_field_readback(signal_contract, signal_raw, expected_quadrature=signal_quadrature),
        validate_native_field_readback(mode_contract, mode_raw, expected_quadrature=mode_quadrature),
        validate_native_field_readback(incident_contract, incident_raw, expected_quadrature=incident_quadrature),
    ]
    capture_args = (capture_raw, capture_contract, capture_quadrature)
    if any(item is not None for item in capture_args):
        if not all(item is not None for item in capture_args):
            _fail("capture raw field, contract, and quadrature must be supplied together")
        assert capture_raw is not None and capture_contract is not None and capture_quadrature is not None
        if capture_contract.get("role") != "capture_signal":
            _fail("capture aperture raw fields must use the capture_signal role")
        if capture_contract.get("source") != signal_contract.get("source"):
            _fail("capture aperture must bind the same native frequency-signal solution as the output plane")
        validations.append(validate_native_field_readback(
            capture_contract, capture_raw, expected_quadrature=capture_quadrature))
    if (signal_contract["plane"] != mode_contract["plane"]
            or signal_contract["quadrature_sha256"] != mode_contract["quadrature_sha256"]):
        _fail("signal and output mode must be sampled on the identical local port plane and quadrature grid")
    if (incident_contract["plane"]["plane_id"] != "input_port"
            or signal_contract["plane"]["plane_id"] != "receiver_port"):
        _fail("incident and receiver sources are bound to the wrong native port planes")
    signal_fields = _expression_map(signal_raw, signal_contract)
    mode_fields = _expression_map(mode_raw, mode_contract)
    incident_fields = _expression_map(incident_raw, incident_contract)
    signal_weights = signal_quadrature["weights_m2"]
    mode_weights = mode_quadrature["weights_m2"]
    incident_weights = incident_quadrature["weights_m2"]
    if not (len(signal_weights) == len(mode_weights) == signal_contract["quadrature"]["sample_count"]):
        _fail("output field and mode quadrature weights do not match")
    output_normal = signal_contract["plane"]["physical_forward_normal_xyz"]
    input_normal = incident_contract["plane"]["physical_forward_normal_xyz"]
    signal_flux, mode_flux, cross_terms = [], [], []
    for index in range(len(signal_weights)):
        signal_e = _field_vector(signal_fields, "signal", "E", index)
        signal_h = _field_vector(signal_fields, "signal", "H", index)
        mode_e = _field_vector(mode_fields, "reference_mode", "E", index)
        mode_h = _field_vector(mode_fields, "reference_mode", "H", index)
        signal_flux.append(0.5 * _cross_dot(signal_e, signal_h, output_normal, conjugate_h=True).real)
        mode_flux.append(0.5 * _cross_dot(mode_e, mode_h, output_normal, conjugate_h=True).real)
        first = _cross_dot(signal_e, mode_h, output_normal, conjugate_h=True)
        second = _cross_dot(mode_e, signal_h, output_normal, conjugate_e=True)
        cross_terms.append(first + second)
    input_flux = []
    for index in range(len(incident_weights)):
        incident_e = _field_vector(incident_fields, "incident_reference", "E", index)
        incident_h = _field_vector(incident_fields, "incident_reference", "H", index)
        input_flux.append(0.5 * _cross_dot(incident_e, incident_h, input_normal, conjugate_h=True).real)
    signal_power = _integrate(signal_flux, signal_weights).real
    mode_power = _integrate(mode_flux, mode_weights).real
    incident_power = _integrate(input_flux, incident_weights).real
    cross = _integrate(cross_terms, signal_weights)
    floor = _finite_number(power_floor_w, "power floor")
    if floor <= 0.0 or min(signal_power, mode_power, incident_power) <= floor:
        _fail("native port power must be positive and exceed the frozen W normalization floor")
    amplitude_denominator = 4.0 * math.sqrt(signal_power) * math.sqrt(mode_power)
    normalized_overlap = (abs(cross) / amplitude_denominator) ** 2
    eta_mode = normalized_overlap * signal_power / incident_power
    result = {"status": "SOFTWARE_RECOMPUTED_FROM_NATIVE_RAW_FIELDS",
            "evidence_scope": "independent full-vector quadrature of fresh native COMSOL Interp data",
            "integration": signal_contract["quadrature"]["profile"], "unit": _POWER_UNIT,
            "geometry_dimension": 3,
            "signal_power": signal_power, "reference_mode_power": mode_power,
            "incident_reference_power": incident_power,
            "reciprocal_overlap_numerator": {"real": cross.real, "imag": cross.imag},
            "normalized_overlap": normalized_overlap, "eta_mode": eta_mode,
            "power_floor_w": floor,
            "sample_counts": {"signal": signal_contract["quadrature"]["sample_count"],
                              "reference_mode": mode_contract["quadrature"]["sample_count"],
                              "incident_reference": incident_contract["quadrature"]["sample_count"]},
            "native_raw_validations": validations, "caller_field_arrays_accepted": False,
            "native_result": "NOT_RUN"}
    if capture_raw is not None:
        assert capture_contract is not None and capture_quadrature is not None
        capture_fields = _expression_map(capture_raw, capture_contract)
        capture_weights = capture_quadrature["weights_m2"]
        capture_normal = capture_contract["plane"]["physical_forward_normal_xyz"]
        capture_flux = []
        for index in range(len(capture_weights)):
            electric = _field_vector(capture_fields, "capture_signal", "E", index)
            magnetic = _field_vector(capture_fields, "capture_signal", "H", index)
            capture_flux.append(0.5 * _cross_dot(
                electric, magnetic, capture_normal, conjugate_h=True).real)
        capture_power = _integrate(capture_flux, capture_weights).real
        if not math.isfinite(capture_power):
            _fail("native aperture flux is not finite")
        result.update({"capture_aperture_signal_flux": capture_power,
                       "eta_capture": capture_power / incident_power,
                       "capture_aperture_id": capture_contract["plane"]["selection_tag"],
                       "capture_sample_count": capture_contract["quadrature"]["sample_count"]})
    return result


def compare_native_mode_overlap(
    native: Mapping[str, Any], independent: Mapping[str, Any],
) -> dict[str, Any]:
    if native.get("status") != "SUCCEEDED" or native.get("result_status") != "COMPUTED_NATIVE_INTEGRALS":
        _fail("managed result.mode_overlap did not return native COMSOL numerical integrals")
    if independent.get("status") != "SOFTWARE_RECOMPUTED_FROM_NATIVE_RAW_FIELDS":
        _fail("independent vector-field quadrature provenance is missing")
    surface = native.get("surface")
    if (not isinstance(surface, Mapping) or surface.get("geometry_dimension") != 3
            or surface.get("power_unit") != "W"):
        _fail("3-D native overlap surface must report full vector geometry and W integrals")
    tolerance = FULL3D_COMPARISON_POLICY["relative_tolerance"]
    integrals = native.get("integrals")
    if not isinstance(integrals, Mapping):
        _fail("native overlap result omitted integral records")
    relative_errors: dict[str, float] = {}
    absolute_normalized_errors: dict[str, float] = {}
    absolute_efficiency_errors: dict[str, float] = {}
    failures: list[str] = []
    for key in ("signal_power", "reference_mode_power", "incident_reference_power"):
        row = integrals.get(key)
        expected = _finite_number(independent.get(key), f"independent {key}")
        if not isinstance(row, Mapping) or row.get("unit") != _POWER_UNIT:
            _fail(f"native {key} unit is not W")
        observed = complex(_finite_number(row.get("real"), f"native {key}.real"),
                           _finite_number(row.get("imag", 0), f"native {key}.imag"))
        if abs(observed.imag) > 1e-10 * max(abs(observed.real), 1e-30):
            _fail(f"native {key} should be a real time-averaged power")
        relative_errors[key] = abs(observed.real - expected) / max(abs(expected), 1e-30)
        if relative_errors[key] > tolerance:
            failures.append(f"{key} relative error exceeds {tolerance:g}")
    row = integrals.get("reciprocal_overlap_numerator")
    expected_row = independent.get("reciprocal_overlap_numerator")
    if not isinstance(row, Mapping) or not isinstance(expected_row, Mapping) or row.get("unit") != _POWER_UNIT:
        _fail("native and independent complex reciprocal numerators are required in W")
    observed_cross = complex(_finite_number(row.get("real"), "native cross real"),
                             _finite_number(row.get("imag"), "native cross imaginary"))
    expected_cross = complex(_finite_number(expected_row.get("real"), "independent cross real"),
                             _finite_number(expected_row.get("imag"), "independent cross imaginary"))
    signal_power = _finite_number(independent.get("signal_power"), "independent signal power")
    mode_power = _finite_number(independent.get("reference_mode_power"), "independent mode power")
    if signal_power <= 0.0 or mode_power <= 0.0:
        _fail("independent cross normalization requires positive signal and mode powers")
    cross_power_scale = 4.0 * math.sqrt(signal_power) * math.sqrt(mode_power)
    cross_absolute_error = abs(observed_cross - expected_cross) / cross_power_scale
    expected_cross_normalized = abs(expected_cross) / cross_power_scale
    cross_atol = FULL3D_COMPARISON_POLICY["cross_absolute_normalized_tolerance"]
    if expected_cross_normalized <= cross_atol:
        absolute_normalized_errors["reciprocal_overlap_numerator"] = cross_absolute_error
        if cross_absolute_error > cross_atol:
            failures.append("reciprocal overlap normalized absolute error exceeds the frozen zero-channel limit")
    else:
        relative_errors["reciprocal_overlap_numerator"] = (
            abs(observed_cross - expected_cross) / abs(expected_cross))
        if relative_errors["reciprocal_overlap_numerator"] > tolerance:
            failures.append(f"reciprocal overlap relative error exceeds {tolerance:g}")
    native_eta = _finite_number(native.get("eta_mode"), "native eta_mode")
    expected_eta = _finite_number(independent.get("eta_mode"), "independent eta_mode")
    eta_absolute_error = abs(native_eta - expected_eta)
    eta_atol = FULL3D_COMPARISON_POLICY["eta_absolute_tolerance"]
    if abs(expected_eta) <= eta_atol:
        absolute_efficiency_errors["eta_mode"] = eta_absolute_error
        if eta_absolute_error > eta_atol:
            failures.append("eta_mode absolute error exceeds the frozen zero-channel limit")
    else:
        relative_errors["eta_mode"] = eta_absolute_error / abs(expected_eta)
        if relative_errors["eta_mode"] > tolerance:
            failures.append(f"eta_mode relative error exceeds {tolerance:g}")
    independent_capture = "eta_capture" in independent
    capture = native.get("eta_capture")
    if independent_capture:
        if not isinstance(capture, Mapping) or capture.get("status") != "COMPUTED_NATIVE_APERTURE_FLUX":
            _fail("independent core-aperture flux requires an actual native capture integral")
        denominator = capture.get("denominator")
        numerator = integrals.get("capture_aperture_signal_flux")
        if (not isinstance(denominator, Mapping) or denominator.get("unit") != _POWER_UNIT
                or not isinstance(numerator, Mapping) or numerator.get("unit") != _POWER_UNIT):
            _fail("native capture numerator and input-power denominator must both be reported in W")
        denominator_value = _finite_number(denominator.get("value"), "native capture denominator")
        native_incident_row = integrals.get("incident_reference_power")
        if not isinstance(native_incident_row, Mapping):
            _fail("capture normalization must bind the native incident-power integral")
        incident_power = _finite_number(native_incident_row.get("real"), "native incident power")
        if abs(denominator_value - incident_power) > 1e-12 * max(abs(incident_power), 1e-30):
            _fail("native capture denominator value differs from the native incident integral")
        numerator_value = _finite_number(numerator.get("real"), "native capture flux")
        if abs(_finite_number(numerator.get("imag", 0), "native capture flux imaginary")) > 1e-10 * max(abs(numerator_value), 1e-30):
            _fail("native signed time-average capture flux has a non-real residual")
        eta_from_integrals = numerator_value / denominator_value
        native_capture_eta = _finite_number(capture.get("value"), "native eta_capture")
        internal_eta_error = abs(native_capture_eta - eta_from_integrals)
        if abs(eta_from_integrals) <= eta_atol:
            internal_eta_consistent = internal_eta_error <= eta_atol
        else:
            internal_eta_consistent = internal_eta_error / abs(eta_from_integrals) <= 1e-12
        if not internal_eta_consistent:
            _fail("native eta_capture does not equal its reported signed numerator divided by denominator")
        expected_capture_flux = _finite_number(independent.get("capture_aperture_signal_flux"),
                                                "independent capture flux")
        expected_capture_eta = _finite_number(independent.get("eta_capture"), "independent eta_capture")
        incident_reference_power = _finite_number(
            independent.get("incident_reference_power"), "independent incident-reference power")
        if incident_reference_power <= 0.0:
            _fail("capture-flux normalization requires positive independent incident power")
        capture_flux_error = abs(numerator_value - expected_capture_flux) / incident_reference_power
        expected_capture_flux_normalized = abs(expected_capture_flux) / incident_reference_power
        capture_flux_atol = FULL3D_COMPARISON_POLICY["capture_flux_absolute_normalized_tolerance"]
        if expected_capture_flux_normalized <= capture_flux_atol:
            absolute_normalized_errors["capture_aperture_signal_flux"] = capture_flux_error
            if capture_flux_error > capture_flux_atol:
                failures.append("capture flux normalized absolute error exceeds the frozen zero-channel limit")
        else:
            relative_errors["capture_aperture_signal_flux"] = (
                abs(numerator_value - expected_capture_flux) / abs(expected_capture_flux))
            if relative_errors["capture_aperture_signal_flux"] > tolerance:
                failures.append(f"capture flux relative error exceeds {tolerance:g}")
        capture_eta_error = abs(native_capture_eta - expected_capture_eta)
        if abs(expected_capture_eta) <= eta_atol:
            absolute_efficiency_errors["eta_capture"] = capture_eta_error
            if capture_eta_error > eta_atol:
                failures.append("eta_capture absolute error exceeds the frozen zero-channel limit")
        else:
            relative_errors["eta_capture"] = capture_eta_error / abs(expected_capture_eta)
            if relative_errors["eta_capture"] > tolerance:
                failures.append(f"eta_capture relative error exceeds {tolerance:g}")
    elif isinstance(capture, Mapping) and capture.get("status") == "COMPUTED_NATIVE_APERTURE_FLUX":
        _fail("native result reports capture efficiency without an independent core-aperture recomputation")
    if (any(not math.isfinite(value) for value in
            (*relative_errors.values(), *absolute_normalized_errors.values(),
             *absolute_efficiency_errors.values())) or failures):
        _fail("native COMSOL integrals differ from independently sampled vector quadrature: "
              f"failures={failures}; relative={relative_errors}; "
              f"normalized_absolute={absolute_normalized_errors}; "
              f"efficiency_absolute={absolute_efficiency_errors}")
    return {"status": "SOFTWARE_NATIVE_VS_INDEPENDENT_FULL3D_COMPARISON_VALID",
            "native_result": "NOT_RUN", "comparison_policy": dict(FULL3D_COMPARISON_POLICY),
            "relative_errors": relative_errors,
            "normalized_absolute_errors": absolute_normalized_errors,
            "efficiency_absolute_errors": absolute_efficiency_errors,
            "unit": _POWER_UNIT,
            "native_result_status": native.get("result_status"),
            "independent_evidence_scope": independent.get("evidence_scope")}


__all__ = [
    "Full3DScienceError", "ManagedRouteOutcomeError", "FULL3D_COMPARISON_POLICY",
    "FULL3D_CONVERGENCE_POLICY", "build_full3d_convergence_recipe",
    "build_full3d_mesh_level_fixture_dispatch",
    "recompute_full3d_mesh_stability_from_registered_routes",
    "BMA_FIELD_MAPPING_POLICY", "COMSOL_PORT_MODE_FIELD_KB_EVIDENCE",
    "COMSOL_INTERP_UNIT_KB_EVIDENCE",
    "build_raw_field_contract", "build_raw_field_dispatch",
    "resolve_full3d_bma_basis_sources", "build_full3d_bma_basis_mapping_contracts",
    "build_full3d_bma_basis_mapping_dispatch", "validate_full3d_bma_basis_mapping_samples",
    "resolve_full3d_bma_receiver_plane", "validate_full3d_bma_mapping_route_result",
    "validate_full3d_sampling_cohort_revision_chain",
    "circular_port_quadrature", "rectangular_port_quadrature", "compare_native_mode_overlap",
    "independent_mode_overlap_integrals", "validate_native_field_readback",
    "validate_native_source_cohort_snapshot",
    "build_full3d_study_run_dispatch", "build_full3d_solution_inventory_dispatch",
    "build_full3d_bma_probe_prepare_dispatch", "build_full3d_bma_probe_run_dispatch",
    "validate_full3d_bma_probe_preparation", "validate_full3d_bma_probe_run_readback",
    "build_full3d_dataset_list_dispatch", "build_full3d_dataset_indices_dispatch",
    "build_full3d_save_dispatch", "build_full3d_model_load_request",
    "build_full3d_mode_overlap_definition", "build_full3d_mode_overlap_dispatch",
    "resolve_full3d_native_sources", "stage_full3d_fixture_source",
    "dispatch_public_managed_route", "execute_full3d_managed_configuration_chain",
]
