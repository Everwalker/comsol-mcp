"""Owned full-3D W23 fiber/lens fixture recipe and managed case identities.

This is the full-vector 3D continuation of the planar port checkpoint.  The
recipe is parameterized and executable by the companion Java builder, but this
module does not dispatch a COMSOL operation or claim native/scientific success.
Case plans bind each perturbation to an immutable project/model/revision and
explicit units before any model mutation or Study.run call.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any


class Full3DRecipeError(ValueError):
    """The full-3D recipe or managed case identity is not admissible."""


FIXTURE_ID = "w23_full3d_fiber_ball_lens_vector_pml_v1"
CASE_PLAN_ID = "w23_full3d_one_factor_tolerance_matrix_v1"
_TAG = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}$")
_MODES = ("x", "y")


def _fail(message: str) -> None:
    raise Full3DRecipeError(message)


def _sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def canonical_full3d_recipe() -> dict[str, Any]:
    """Return an owned, estimated 3D vector-optics fixture definition in SI."""
    recipe = {
        "schema_version": 1,
        "fixture_id": FIXTURE_ID,
        "status": "OWNED_RECIPE_NATIVE_NOT_RUN",
        "native_result": "NOT_RUN",
        "study_or_solver_invoked": False,
        "geometry": {
            "space_dimension": 3,
            "length_unit": "um",
            "propagation_axis": "+x",
            "input_port_x_um": -20.0,
            "input_fiber_end_x_um": -5.0,
            "lens_center_xyz_um": [0.0, 0.0, 0.0],
            "lens_radius_um": 4.0,
            "output_fiber_start_x_um": 5.0,
            "output_port_x_um": 20.0,
            "domain_xmax_um": 21.0,
            "air_halfwidth_yz_um": [6.0, 6.0],
            "pml_transverse_thickness_um": 2.0,
            "core_radius_um": 1.2,
            "cladding_radius_um": 2.5,
            "output_fiber_translation_xyz_um": [0.0, 0.0, 0.0],
            "port_planes": {"input": "x=-20 um", "output": "x=20 um",
                            "normal": "native surface normal readback required"},
            "angular_receiver": {
                "rotation_pivot": "output-fiber start center; shared by core and cladding solids",
                "positive_theta_y": "right-handed about global +y; x axis maps to (cos(y),0,-sin(y))",
                "positive_theta_z": "right-handed about global +z after y; x axis maps to (cos(z)cos(y),sin(z)cos(y),-sin(y))",
                "port_section": "finite local Cylinder selection about the same transformed output axis",
                "selection_readback": ["entity IDs", "area", "AABB-centroid", "per-face point and normal"],
                "selection_half_length_um": 0.01,
                "selection_radial_margin_um": 0.02,
                "domain_xmax_margin_um": 1.0,
            },
        },
        "materials": {
            "air": {"relative_permittivity": 1.0, "relative_permeability": 1.0,
                    "complex_loss": "zero requested; native property readback required"},
            "core": {"refractive_index": 1.47, "estimated": True},
            "cladding": {"refractive_index": 1.44, "estimated": True},
            "lens": {"refractive_index": 1.52, "estimated": True},
        },
        "optics": {
            "interface": "ElectromagneticWaves",
            "dimension": 3,
            "field_components": ["Ex", "Ey", "Ez", "Hx", "Hy", "Hz"],
            "vector_convention": "full three-component vector; 3D default must be read back",
            "vacuum_wavelength_um": 1.55,
            "frequency_hz": 193.414489032258e12,
            "input_power_w": 1.0,
            "normalization_unit": "W",
            "pml": {"coordinate_type": "Cartesian", "stretching": "polynomial",
                    "domains": "transverse outer shell only; native domain/axis readback required"},
            "ports": {"input": {"type": "Numeric", "number": "1", "excited": True},
                      "output": {"type": "Numeric", "number": "2", "excited": False}},
            "configured_study_steps": [
                {"tag": "bmaInput3d", "type": "BoundaryModeAnalysis", "port": "1",
                 "mode_frequency_expression": "f0"},
                {"tag": "bmaOutput3d", "type": "BoundaryModeAnalysis", "port": "2",
                 "mode_frequency_expression": "f0"},
                {"tag": "freq3d", "type": "Frequency", "frequency_expression": "f0"},
            ],
        },
        "mode_basis": {
            "reference_basis_id": "fundamental_spatial_mode_two_polarization_subspace_v1",
            "expected_subspace_dimension": 2,
            "labels": ["linear_x_reference", "linear_y_reference"],
            "tracking": "native complex power-overlap 2x2 subspace matrix; no single mode-index identity",
            "degeneracy_policy": "track subspace/principal angles; report basis rotation and conditioning",
            "native_status": "NOT_RUN",
        },
        "deformation_mapping": {
            "source": "registered W21 stage plus exact W17 ObservationRef in the same COMSOL Model",
            "target_component": "comp3d", "target_geometry": "geom3d",
            "target_selection": "sel3dDeformationTarget",
            "target_domains": ["input core", "output core", "input cladding", "output cladding", "ball lens"],
            "excluded_domains": ["air remainder", "PML shell"],
            "source_frame": "material", "destination_frame": "spatial",
            "vector_basis": "global_xyz", "coordinate_unit": "m",
            "mapping_route": "same-model component GeneralExtrusion with exact withsol source solution/time/index",
            "held_out_comparison": "direct source component native sampling vs target component extrusion at identical registered 3D coordinates",
            "native_status": "NOT_RUN",
        },
        "scope_limits": [
            "estimated optical constants and reduced fiber/lens dimensions are a mechanism fixture, not physical calibration",
            "an aligned baseline builder is not acceptance of xyz/angular/manufacturing scans",
            "W21 deformation mapping remains a separately bound native source route",
            "no solve, result, mode stability, efficiency, capture, or saved/reopened artifact is asserted",
        ],
    }
    recipe["recipe_sha256"] = _sha256(recipe)
    return recipe


def verify_recipe(recipe: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(recipe, Mapping) or recipe.get("fixture_id") != FIXTURE_ID:
        _fail("the owned full-3D fixture identity is required")
    if recipe.get("recipe_sha256") != _sha256({k: v for k, v in recipe.items() if k != "recipe_sha256"}):
        _fail("full-3D recipe digest mismatch")
    geometry = recipe.get("geometry")
    materials = recipe.get("materials")
    optics = recipe.get("optics")
    if not all(isinstance(value, Mapping) for value in (geometry, materials, optics)):
        _fail("full-3D geometry, material, and optics contracts are required")
    if geometry.get("space_dimension") != 3 or optics.get("dimension") != 3:
        _fail("planar or axisymmetric fixtures cannot satisfy the full-3D contract")
    if optics.get("vector_convention", "").startswith("out-of-plane"):
        _fail("a reduced polarization setting cannot satisfy this 3D vector fixture")
    try:
        lam = float(optics["vacuum_wavelength_um"])
        rc = float(geometry["core_radius_um"])
        ncore = float(materials["core"]["refractive_index"])
        nclad = float(materials["cladding"]["refractive_index"])
        lens_r = float(geometry["lens_radius_um"])
        rclad = float(geometry["cladding_radius_um"])
        air_y, air_z = (float(value) for value in geometry["air_halfwidth_yz_um"])
        pml = float(geometry["pml_transverse_thickness_um"])
        x_in = float(geometry["input_port_x_um"])
        x_fiber_end = float(geometry["input_fiber_end_x_um"])
        x_out_start = float(geometry["output_fiber_start_x_um"])
        x_out = float(geometry["output_port_x_um"])
        x_domain_max = float(geometry["domain_xmax_um"])
    except (KeyError, TypeError, ValueError) as error:
        _fail(f"full-3D recipe has incomplete/non-numeric dimensions: {error}")
    finite = (lam, rc, ncore, nclad, lens_r, rclad, air_y, air_z, pml,
              x_in, x_fiber_end, x_out_start, x_out, x_domain_max)
    if not all(math.isfinite(value) for value in finite) or min(lam, rc, ncore, nclad, lens_r, rclad, air_y, air_z, pml) <= 0:
        _fail("full-3D recipe dimensions and optical constants must be finite and positive")
    if not (ncore > nclad > 1.0 and float(materials["lens"]["refractive_index"]) > 1.0):
        _fail("the frozen dielectric contrast must be positive and explicit")
    if not (rc < rclad < min(air_y, air_z) and lens_r < min(air_y, air_z)):
        _fail("core/cladding/lens must fit strictly inside the air domain")
    if not (x_in < x_fiber_end < -lens_r < 0 < lens_r < x_out_start < x_out):
        _fail("input fiber, lens, output fiber, and port planes overlap or are out of order")
    angular = geometry.get("angular_receiver")
    if (not isinstance(angular, Mapping) or x_domain_max <= x_out
            or angular.get("port_section") != "finite local Cylinder selection about the same transformed output axis"
            or angular.get("selection_readback") != ["entity IDs", "area", "AABB-centroid", "per-face point and normal"]
            or angular.get("selection_half_length_um") != 0.01
            or angular.get("selection_radial_margin_um") != 0.02):
        _fail("full-3D angular receiver requires a rotated local section and geometry readback contract")
    steps = optics.get("configured_study_steps")
    expected_steps = [
        {"tag": "bmaInput3d", "type": "BoundaryModeAnalysis", "port": "1",
         "mode_frequency_expression": "f0"},
        {"tag": "bmaOutput3d", "type": "BoundaryModeAnalysis", "port": "2",
         "mode_frequency_expression": "f0"},
        {"tag": "freq3d", "type": "Frequency", "frequency_expression": "f0"},
    ]
    if steps != expected_steps:
        _fail("configured study steps must bind ordered input BMA, output BMA, and frequency f0 exactly")
    if optics.get("field_components") != ["Ex", "Ey", "Ez", "Hx", "Hy", "Hz"]:
        _fail("all six complex vector field components are required")
    mapping = recipe.get("deformation_mapping")
    expected_mapping = {
        "source": "registered W21 stage plus exact W17 ObservationRef in the same COMSOL Model",
        "target_component": "comp3d", "target_geometry": "geom3d",
        "target_selection": "sel3dDeformationTarget",
        "target_domains": ["input core", "output core", "input cladding", "output cladding", "ball lens"],
        "excluded_domains": ["air remainder", "PML shell"],
        "source_frame": "material", "destination_frame": "spatial",
        "vector_basis": "global_xyz", "coordinate_unit": "m",
        "mapping_route": "same-model component GeneralExtrusion with exact withsol source solution/time/index",
        "held_out_comparison": "direct source component native sampling vs target component extrusion at identical registered 3D coordinates",
        "native_status": "NOT_RUN",
    }
    if mapping != expected_mapping:
        _fail("full-3D deformation route must bind the registered W21 map into the native optical material selection")
    if recipe.get("native_result") != "NOT_RUN" or recipe.get("study_or_solver_invoked") is not False:
        _fail("a pure recipe verifier cannot upgrade native execution state")
    # V is a design calculation only: it does not establish a native mode count.
    v_number = 2 * math.pi * rc / lam * math.sqrt(ncore * ncore - nclad * nclad)
    return {"status": "SOFTWARE_RECIPE_VALID", "native_result": "NOT_RUN",
            "fixture_id": FIXTURE_ID, "recipe_sha256": recipe["recipe_sha256"],
            "v_number_design_estimate": v_number,
            "single_spatial_mode_design_target_below_2_405": v_number < 2.405,
            "native_mode_count": "NOT_RUN"}


def _case(case_name: str, factor: str, value: float, unit: str, sign: int,
          application_profile: str) -> dict[str, Any]:
    body = {"plan_id": CASE_PLAN_ID, "case_name": case_name, "factor": factor,
            "value": value, "unit": unit, "sign": sign,
            "application_profile": application_profile}
    body["case_id"] = "w23f3d_" + case_name + "_" + _sha256(body)[:12]
    body["status"] = "PLANNED_NOT_RUN"
    body["native_result"] = "NOT_RUN"
    return body


def expected_receiver_frame(factor: str, value: float) -> dict[str, Any]:
    """Independent software transform oracle for a one-factor output-fiber case."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        _fail("receiver transform value must be finite numeric data")
    value = float(value)
    start_x, dy, dz = 5.0, 0.0, 0.0
    radius, theta_y, theta_z = 2.5, 0.0, 0.0
    if factor == "baseline":
        if value != 0.0:
            _fail("baseline receiver transform requires a zero factor")
    elif factor == "receiver_gap_x_um":
        start_x += value
    elif factor == "receiver_dy_um":
        dy = value
    elif factor == "receiver_dz_um":
        dz = value
    elif factor == "receiver_theta_y_deg":
        theta_y = value
    elif factor == "receiver_theta_z_deg":
        theta_z = value
    elif factor == "cladding_radius_relative":
        radius *= 1.0 + value
    elif factor in {"core_radius_relative", "lens_radius_relative", "lens_index_delta"}:
        pass
    else:
        _fail(f"unregistered full-3D transform factor: {factor}")
    ry, rz = math.radians(theta_y), math.radians(theta_z)
    axis = [math.cos(rz) * math.cos(ry), math.sin(rz) * math.cos(ry), -math.sin(ry)]
    length = 20.0 - start_x
    center = [start_x + length * axis[0], dy + length * axis[1], dz + length * axis[2]]
    return {"rotation_order": "right-handed global +y then +z",
            "pivot_xyz_um": [start_x, dy, dz], "axis_xyz": axis,
            "center_xyz_um": center, "cladding_radius_um": radius,
            "theta_y_deg": theta_y, "theta_z_deg": theta_z,
            "selection_half_length_um": 0.01, "selection_radial_margin_um": 0.02}


def verify_receiver_port_section(case: Mapping[str, Any], readback: Mapping[str, Any]) -> dict[str, Any]:
    """Validate actual native section geometry without upgrading optical results."""
    expected = case.get("receiver_transform") if isinstance(case, Mapping) else None
    if not isinstance(expected, Mapping) or not isinstance(readback, Mapping):
        _fail("case transform and native receiver-section readback are required")
    if readback.get("evidence_scope") != "COMSOL_NATIVE_GEOMETRY_READBACK":
        _fail("receiver section must be an actual COMSOL geometry readback")
    if (readback.get("tag") != "sel3dOutputPort" or readback.get("selection_type") != "Cylinder"
            or readback.get("selection_geometry") != "geom3d"
            or readback.get("entity_dimension") != 2
            or readback.get("selection_condition") != "inside"
            or readback.get("selection_axis_type") != "cartesian"):
        _fail("receiver must use the frozen geometry-local 2-D Cylinder selection")
    entities = readback.get("entity_ids")
    if (not isinstance(entities, list) or not entities
            or any(type(item) is not int or item < 1 for item in entities)
            or len(entities) != len(set(entities))):
        _fail("native receiver section must contain unique positive boundary IDs")
    if readback.get("coordinate_unit") != "um" or readback.get("normal_basis") != "global_xyz":
        _fail("receiver coordinate and normal frames must read back as um/global_xyz")

    def vector(raw: Any, label: str) -> list[float]:
        if not isinstance(raw, (list, tuple)) or len(raw) != 3:
            _fail(f"{label} must be a 3-vector")
        values: list[float] = []
        for item in raw:
            if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)):
                _fail(f"{label} must contain finite numbers")
            values.append(float(item))
        return values

    def scalar(raw: Any, label: str) -> float:
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(float(raw)):
            _fail(f"{label} must be finite numeric data")
        return float(raw)

    expected_axis = vector(expected.get("axis_xyz"), "expected receiver axis")
    expected_center = vector(expected.get("center_xyz_um"), "expected receiver center")
    selection_axis = vector(readback.get("selection_axis_xyz"), "native selection axis")
    selection_base = vector(readback.get("selection_base_um"), "native selection base")
    selection_center = vector(readback.get("selection_center_um"), "native selection center")
    if any(abs(a - b) > 1e-8 for a, b in zip(selection_axis, expected_axis)):
        _fail("native selection axis differs from the shared rigid transform")
    axis_norm = math.sqrt(sum(value * value for value in selection_axis))
    if abs(axis_norm - 1.0) > 1e-10:
        _fail("native selection axis is not unit length")
    half_length = scalar(expected.get("selection_half_length_um"), "registered selection half-length")
    radial_margin = scalar(expected.get("selection_radial_margin_um"), "registered selection radial margin")
    bottom = scalar(readback.get("selection_bottom_um"), "native selection bottom")
    top = scalar(readback.get("selection_top_um"), "native selection top")
    if abs(bottom) > 1e-10 or abs(top - 2 * half_length) > 1e-8:
        _fail("native finite Cylinder axial limits differ from the frozen section slab")
    base_expected = [center - half_length * direction for center, direction in zip(expected_center, expected_axis)]
    if any(abs(a - b) > 1e-8 for a, b in zip(selection_base, base_expected)):
        _fail("native local section base differs from the transformed port plane")
    reconstructed_center = [base + (bottom + top) * 0.5 * direction
                            for base, direction in zip(selection_base, selection_axis)]
    if any(abs(a - b) > 1e-8 for a, b in zip(reconstructed_center, expected_center)):
        _fail("native local section center does not match the transformed output-fiber cap")
    if any(abs(a - b) > 1e-9 for a, b in zip(selection_center, reconstructed_center)):
        _fail("native reported selection center conflicts with its actual axis and limits")
    expected_radius = scalar(expected.get("cladding_radius_um"), "registered cladding radius")
    native_radius = scalar(readback.get("selection_radius_um"), "native selection radius")
    if abs(native_radius - (expected_radius + radial_margin)) > 1e-8:
        _fail("native local section radius differs from cladding plus the frozen selection margin")

    bounds = readback.get("bounding_box_um")
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 6:
        _fail("native local section requires a six-coordinate bounding box")
    bounds_values = [scalar(value, "native section bounds") for value in bounds]
    if any(bounds_values[2 * axis] > bounds_values[2 * axis + 1] for axis in range(3)):
        _fail("native local section bounding-box limits are reversed")
    centroid = vector(readback.get("centroid_um"), "native section AABB centroid")
    aabb_centroid = [(bounds_values[2 * axis] + bounds_values[2 * axis + 1]) / 2
                     for axis in range(3)]
    if any(abs(a - b) > 1e-9 for a, b in zip(centroid, aabb_centroid)):
        _fail("reported section centroid is not the midpoint of the native AABB")
    centroid_error = math.sqrt(sum((a - b) ** 2 for a, b in zip(centroid, expected_center)))
    if abs(scalar(readback.get("centroid_error_um"), "reported centroid error") - centroid_error) > 1e-9:
        _fail("reported section centroid error does not match its coordinates")
    if centroid_error > 0.005:
        _fail("native section AABB centroid misses its registered transformed center")

    area = scalar(readback.get("area_um2"), "native receiver-face area")
    expected_area = math.pi * expected_radius * expected_radius
    reported_expected_area = scalar(readback.get("expected_circle_area_um2"), "reported expected circle area")
    if area <= 0 or abs(reported_expected_area - expected_area) > max(1e-10, expected_area * 1e-10):
        _fail("native receiver area or registered circular-area expectation is invalid")
    area_error = abs(area - expected_area) / expected_area
    reported_area_error = scalar(readback.get("area_relative_error"), "reported area error")
    if abs(reported_area_error - area_error) > 1e-10:
        _fail("reported receiver area error differs from an independent recomputation")
    if readback.get("area_tolerance") != "3% geometry-measure approximation gate" or area_error > 0.03:
        _fail("native receiver area exceeds the frozen geometry-measure tolerance")

    faces = readback.get("faces")
    if not isinstance(faces, list) or len(faces) != len(entities):
        _fail("every selected receiver boundary must have a native point and normal")
    sign = readback.get("native_face_oriented_axis_sign")
    if type(sign) is not int or sign not in {-1, 1}:
        _fail("native receiver normal sign must be a signed unit orientation")
    face_ids: list[int] = []
    for face in faces:
        if not isinstance(face, Mapping) or type(face.get("boundary_id")) is not int:
            _fail("native receiver face point/normal receipt is malformed")
        face_ids.append(face["boundary_id"])
        vector(face.get("point_um"), "native receiver face point")
        normal = vector(face.get("unit_normal_xyz"), "native receiver face normal")
        normal_norm = math.sqrt(sum(value * value for value in normal))
        dot = sum(a * b for a, b in zip(normal, expected_axis))
        if abs(normal_norm - 1.0) > 1e-8 or abs(abs(dot) - 1.0) > 1e-5:
            _fail("native receiver face normal is not a unit vector parallel to transformed axis")
        expected_dot = scalar(face.get("axis_dot"), "native face axis dot")
        if abs(expected_dot - dot) > 1e-8 or (1 if dot >= 0 else -1) != sign:
            _fail("native receiver face orientation summary differs from its raw normal")
    if set(face_ids) != set(entities) or len(face_ids) != len(set(face_ids)):
        _fail("native receiver face details do not map one-to-one to selected boundary IDs")
    return {"status": "SOFTWARE_NATIVE_GEOMETRY_READBACK_VALID",
            "native_result": "NOT_RUN", "study_or_solver_invoked": False,
            "entity_ids": list(entities), "area_relative_error": area_error,
            "centroid_error_um": centroid_error, "normal_sign": sign,
            "selection_identity": "sel3dOutputPort/geom3d/entity_dimension_2"}


def build_case_matrix() -> list[dict[str, Any]]:
    """Create fixed one-factor cases; no result-driven range or tolerance edits."""
    rows = [{"plan_id": CASE_PLAN_ID, "case_name": "aligned_baseline", "factor": "baseline",
             "value": 0.0, "unit": "1", "sign": 0,
             "application_profile": "canonical_3d_fixture"}]
    cases = [
        ("axial_receiver_gap", "receiver_gap_x_um", 0.25, "um", "parameterized_fiber_start"),
        ("lateral_y", "receiver_dy_um", 0.25, "um", "parameterized_cylinder_translation"),
        ("lateral_z", "receiver_dz_um", 0.25, "um", "parameterized_cylinder_translation"),
        ("angular_y", "receiver_theta_y_deg", 0.5, "deg", "rotated_output_fiber_and_local_numeric_port_selection"),
        ("angular_z", "receiver_theta_z_deg", 0.5, "deg", "rotated_output_fiber_and_local_numeric_port_selection"),
        ("core_radius_tolerance", "core_radius_relative", 0.02, "1", "parameterized_geometry"),
        ("cladding_radius_tolerance", "cladding_radius_relative", 0.02, "1", "parameterized_geometry"),
        ("lens_radius_tolerance", "lens_radius_relative", 0.02, "1", "parameterized_geometry"),
        ("lens_index_tolerance", "lens_index_delta", 0.005, "1", "parameterized_material"),
    ]
    for name, factor, magnitude, unit, profile in cases:
        for sign, suffix in ((-1, "minus"), (1, "plus")):
            rows.append(_case(f"{name}_{suffix}", factor, sign * magnitude, unit, sign, profile))
    for row in rows:
        row["receiver_transform"] = expected_receiver_frame(row["factor"], row["value"])
    rows = [{**row, "case_id": row.get("case_id", "w23f3d_aligned_baseline_" + _sha256(row)[:12]),
             "status": "PLANNED_NOT_RUN", "native_result": "NOT_RUN"} for row in rows]
    return rows


def bind_case_matrix(
    recipe: Mapping[str, Any], *, project_id: str, model_ref: Mapping[str, Any],
    model_tag: str, revision: int, experiment_id: str,
) -> dict[str, Any]:
    """Bind cases to the managed project/model state before any native call."""
    verification = verify_recipe(recipe)
    if not isinstance(project_id, str) or not project_id:
        _fail("registered project id is required for the W23 case matrix")
    if not isinstance(model_ref, Mapping) or not model_ref:
        _fail("persisted managed ModelRef is required for the W23 case matrix")
    if not isinstance(model_tag, str) or not _TAG.fullmatch(model_tag):
        _fail("native model tag is malformed")
    if type(revision) is not int or revision < 0:
        _fail("nonnegative current managed revision is required")
    if not isinstance(experiment_id, str) or not experiment_id:
        _fail("durable experiment identity is required")
    cases = build_case_matrix()
    for row in cases:
        row["project_id"] = project_id
        row["model_ref"] = dict(model_ref)
        row["model_tag"] = model_tag
        row["expected_revision"] = revision
        row["experiment_id"] = experiment_id
        row["recipe_sha256"] = verification["recipe_sha256"]
        row["basis_identity"] = recipe["mode_basis"]["reference_basis_id"]
        row["study_sequence"] = [dict(step) for step in recipe["optics"]["configured_study_steps"]]
        row["case_identity_sha256"] = _sha256({key: value for key, value in row.items()
                                               if key != "case_identity_sha256"})
    return {"schema_version": 1, "plan_id": CASE_PLAN_ID,
            "status": "BOUND_MANAGED_CASE_PLAN_NOT_RUN", "native_result": "NOT_RUN",
            "study_or_solver_invoked": False, "project_id": project_id,
            "model_ref": dict(model_ref), "model_tag": model_tag, "expected_revision": revision,
            "experiment_id": experiment_id, "recipe_sha256": verification["recipe_sha256"],
            "cases": cases,
            "adapter_gates": {
            "translation_and_scalar_tolerances": "parameterized_in_3d_fixture",
            "angular_cases": "implemented: same rigid rotation for core/cladding and local Numeric-port selection; native area/centroid/normal readback NOT_RUN",
                "per_case_study_run_calls": "NOT_FROZEN; reconcile exact COMSOL run sequence before native budget",
            "degenerate_basis": "two-dimensional complex power-overlap subspace receipt required",
            }}


def _managed_execution_binding(*, project_id: str, model_ref: Mapping[str, Any],
                               model_tag: str, revision: int,
                               request_id: str, idempotency_key: str) -> dict[str, Any]:
    if not isinstance(project_id, str) or not project_id:
        _fail("registered project id is required for native fixture dispatch")
    if not isinstance(model_ref, Mapping) or not model_ref:
        _fail("persisted managed ModelRef is required for native fixture dispatch")
    session_id = model_ref.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        _fail("exact session_id from the persisted ModelRef is required for native fixture dispatch")
    if not isinstance(model_tag, str) or not _TAG.fullmatch(model_tag):
        _fail("native model tag is malformed")
    if type(revision) is not int or revision < 0:
        _fail("nonnegative managed revision is required for native fixture dispatch")
    for value, label in ((request_id, "request_id"), (idempotency_key, "idempotency_key")):
        if not isinstance(value, str) or not value.strip():
            _fail(f"{label} is required for native fixture dispatch")
    return {"project_id": project_id, "session_id": session_id,
            "model_ref": copy.deepcopy(dict(model_ref)),
            "model_tag": model_tag, "expected_revision": revision,
            "request_id": request_id, "idempotency_key": idempotency_key}


def build_full3d_fixture_dispatch(
    recipe: Mapping[str, Any], *, source_artifact: str,
    project_id: str, model_ref: Mapping[str, Any], model_tag: str, revision: int,
    request_id: str, idempotency_key: str,
) -> dict[str, Any]:
    """Build one managed, no-solve route for the exact owned full-3D fixture."""
    verification = verify_recipe(recipe)
    if not isinstance(source_artifact, str) or not source_artifact.strip():
        _fail("registered managed Java source artifact id is required")
    binding = _managed_execution_binding(
        project_id=project_id, model_ref=model_ref, model_tag=model_tag, revision=revision,
        request_id=request_id, idempotency_key=idempotency_key)
    java_args = {
        "phase": "build", "fixture_id": FIXTURE_ID,
        "recipe_sha256": verification["recipe_sha256"],
        "managed_identity": {key: binding[key] for key in
                             ("project_id", "model_ref", "model_tag", "expected_revision")},
        "native_result": "NOT_RUN", "study_or_solver_invoked": False,
    }
    return {
        "operation": "operation_call",
        "arguments": {"operation_id": "code.execute_java", "arguments": {
            "source_artifact": source_artifact,
            "entrypoint": "NativeW23Full3DFixture#run",
            "mode": "trusted", "arguments": java_args}},
        "execution": {"project_id": binding["project_id"], "session_id": binding["session_id"],
                      "model_ref": binding["model_ref"],
                      "expected_revision": binding["expected_revision"],
                      "request_id": binding["request_id"],
                      "idempotency_key": binding["idempotency_key"]},
        "dispatch_scope": "one managed Java fixture build; no Study.run or solver call",
        "native_result": "NOT_RUN", "study_or_solver_invoked": False,
    }


def build_full3d_case_dispatch(
    plan: Mapping[str, Any], case: Mapping[str, Any], *, source_artifact: str,
    request_id: str, idempotency_key: str,
) -> dict[str, Any]:
    """Build one revision-bound geometry mutation for a registered case only."""
    if (not isinstance(plan, Mapping) or plan.get("plan_id") != CASE_PLAN_ID
            or plan.get("status") != "BOUND_MANAGED_CASE_PLAN_NOT_RUN"
            or plan.get("native_result") != "NOT_RUN"
            or plan.get("study_or_solver_invoked") is not False):
        _fail("a bound full-3D managed case plan is required")
    if not isinstance(case, Mapping) or case not in plan.get("cases", []):
        _fail("case identity is not a member of the bound full-3D case plan")
    expected_identity = _sha256({key: value for key, value in case.items()
                                 if key != "case_identity_sha256"})
    if case.get("case_identity_sha256") != expected_identity:
        _fail("full-3D case identity digest mismatch")
    for key in ("project_id", "model_ref", "model_tag", "expected_revision",
                "experiment_id", "recipe_sha256"):
        if case.get(key) != plan.get(key):
            _fail(f"bound full-3D case differs from plan {key}")
    if not isinstance(source_artifact, str) or not source_artifact.strip():
        _fail("registered managed Java source artifact id is required")
    binding = _managed_execution_binding(
        project_id=plan["project_id"], model_ref=plan["model_ref"], model_tag=plan["model_tag"],
        revision=plan["expected_revision"], request_id=request_id, idempotency_key=idempotency_key)
    java_args = {"phase": "apply_case", "fixture_id": FIXTURE_ID,
                 "recipe_sha256": plan["recipe_sha256"],
                 "managed_identity": {key: binding[key] for key in
                                      ("project_id", "model_ref", "model_tag", "expected_revision")},
                 "case": copy.deepcopy(dict(case)),
                 "native_result": "NOT_RUN", "study_or_solver_invoked": False}
    return {
        "operation": "operation_call",
        "arguments": {"operation_id": "code.execute_java", "arguments": {
            "source_artifact": source_artifact,
            "entrypoint": "NativeW23Full3DFixture#run",
            "mode": "trusted", "arguments": java_args}},
        "execution": {"project_id": binding["project_id"], "session_id": binding["session_id"],
                      "model_ref": binding["model_ref"],
                      "expected_revision": binding["expected_revision"],
                      "request_id": binding["request_id"],
                      "idempotency_key": binding["idempotency_key"]},
        "dispatch_scope": "one managed full-3D geometry case mutation; no Study.run or solver call",
        "native_result": "NOT_RUN", "study_or_solver_invoked": False,
    }


def verify_deformation_target_readback(readback: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(readback, Mapping) or readback.get("evidence_scope") != "COMSOL_NATIVE_GEOMETRY_READBACK":
        _fail("deformation target must be a native geometry-selection readback")
    if (readback.get("tag") != "sel3dDeformationTarget"
            or readback.get("selection_type") != "Explicit"
            or readback.get("component_tag") != "comp3d"
            or readback.get("geometry_tag") != "geom3d"
            or readback.get("entity_dimension") != 3):
        _fail("deformation target selection identity is not the owned 3D optical material selection")
    entities = readback.get("entity_ids")
    if (not isinstance(entities, list) or not entities
            or any(type(item) is not int or item < 1 for item in entities)
            or len(entities) != len(set(entities))):
        _fail("deformation target must carry unique positive native domain IDs")
    if (readback.get("coordinate_frame") != "spatial" or readback.get("coordinate_unit") != "m"
            or readback.get("vector_basis") != "global_xyz"):
        _fail("deformation target must bind spatial metre coordinates and global xyz vectors")
    if readback.get("selected_material_domains") != [
            "core input", "core output", "cladding input", "cladding output", "ball lens"]:
        _fail("deformation target must include all and only registered optical material domains")
    if readback.get("excluded_domains") != ["air remainder", "PML shell"]:
        _fail("deformation target must exclude air and PML from source displacement extrapolation")
    if readback.get("entity_ids_derived_from") != "native geometry result selections after geometry build":
        _fail("deformation target IDs must derive from this model's generated geometry selections")
    if readback.get("deformation_mapping") != "W21 source displacement field; no caller arrays":
        _fail("deformation target must remain linked to registered W21 source fields")
    return {"status": "SOFTWARE_NATIVE_TARGET_SELECTION_VALID", "native_result": "NOT_RUN",
            "entity_ids": list(entities), "selection": "comp3d/geom3d/sel3dDeformationTarget"}


def verify_case_result(plan: Mapping[str, Any], case: Mapping[str, Any], result: Mapping[str, Any],
                       *, current_revision: int) -> dict[str, Any]:
    """Refuse foreign/stale case results; never promote readback to science PASS."""
    if plan.get("native_result") != "NOT_RUN" or plan.get("study_or_solver_invoked") is not False:
        _fail("case result verifier must use a bound pre-run plan")
    if case not in plan.get("cases", []):
        _fail("case identity is not a member of the frozen managed plan")
    if type(current_revision) is not int or current_revision != case.get("expected_revision"):
        _fail("managed model revision changed before the case mutation")
    for key in ("project_id", "model_ref", "model_tag", "experiment_id", "case_id",
                "case_identity_sha256", "recipe_sha256"):
        if result.get(key) != case.get(key):
            _fail(f"native case response differs from immutable {key}")
    if result.get("study_or_solver_invoked") is not False:
        _fail("case configuration/readback entrypoint must not submit a study or solver")
    if result.get("native_result") != "NOT_RUN":
        _fail("geometry/material readback cannot claim optical numerical acceptance")
    port_receipt = verify_receiver_port_section(case, result.get("receiver_port_section", {}))
    deformation_receipt = verify_deformation_target_readback(
        result.get("deformation_target_selection", {}))
    return {"status": "SOFTWARE_CASE_IDENTITY_PASS", "native_result": "NOT_RUN",
            "case_id": case["case_id"], "case_identity_sha256": case["case_identity_sha256"],
            "result": "CONFIGURATION_ONLY", "receiver_port_section": port_receipt,
            "deformation_target_selection": deformation_receipt}


def validate_mode_basis_matrix(matrix: Sequence[Sequence[complex]], *, expected_dimension: int = 2,
                               minimum_singular_value: float = 1e-6) -> dict[str, Any]:
    """Check that a native complex overlap matrix preserves a degenerate subspace.

    This software gate rejects missing/ill-conditioned basis receipts.  A
    native raw field/overlap receipt and its independently computed powers are
    still required; this helper cannot establish mode stability itself.
    """
    if expected_dimension != 2 or not isinstance(matrix, Sequence) or len(matrix) != expected_dimension:
        _fail("the registered two-polarization basis requires a complete 2x2 overlap matrix")
    rows: list[list[complex]] = []
    for row in matrix:
        if not isinstance(row, Sequence) or len(row) != expected_dimension:
            _fail("degenerate-mode basis matrix must be square and complete")
        converted = []
        for value in row:
            try:
                item = complex(value)
            except (TypeError, ValueError):
                _fail("basis matrix entries must be finite native complex overlaps")
            if not math.isfinite(item.real) or not math.isfinite(item.imag):
                _fail("basis matrix entries must be finite")
            converted.append(item)
        rows.append(converted)
    # Eigenvalues of A* A, calculated analytically for a 2x2 Hermitian matrix.
    a = sum(abs(rows[i][0]) ** 2 for i in range(2))
    d = sum(abs(rows[i][1]) ** 2 for i in range(2))
    b = sum(rows[i][0].conjugate() * rows[i][1] for i in range(2))
    trace = a + d
    discriminant = math.sqrt(max(0.0, (a - d) ** 2 + 4.0 * abs(b) ** 2))
    eigenvalues = ((trace + discriminant) / 2.0, (trace - discriminant) / 2.0)
    singular_values = tuple(math.sqrt(max(0.0, value)) for value in eigenvalues)
    if not math.isfinite(minimum_singular_value) or minimum_singular_value <= 0:
        _fail("basis conditioning threshold must be positive and frozen")
    valid = min(singular_values) >= minimum_singular_value
    return {"status": "SOFTWARE_SUBSPACE_CONDITIONING_PASS" if valid else "SOFTWARE_SUBSPACE_CONDITIONING_FAIL",
            "native_result": "NOT_RUN", "expected_dimension": expected_dimension,
            "singular_values": list(singular_values),
            "minimum_singular_value": minimum_singular_value,
            "mode_labels_alone_used": False,
            "native_basis_stability": "NOT_RUN"}
