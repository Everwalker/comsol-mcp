"""Software-only numerical contract tests for W23 result.mode_overlap."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import unittest

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._mode_overlap import (
    APPLICABILITY_PROFILE,
    DEFINITION_SCHEMA,
    DEFINITION_SCHEMA_VERSION,
    PLANARITY_BOUND_POLICY,
    PLANARITY_ULP_SAFETY_FACTOR,
    RESULT_SCHEMA,
    RESULT_SCHEMA_VERSION,
    compute_mode_overlap,
)


ETA_0 = 376.730313668
ROOT = Path(__file__).resolve().parents[1]
PHASOR_CONVENTION = {
    "time_dependence": "exp(-i omega t)",
    "complex_field_representation": "full_physical_complex_phasor_including_reconstructed_envelope_phase",
}


def _cross(a: tuple[complex, complex, complex], b: tuple[complex, complex, complex]) -> tuple[complex, complex, complex]:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _source(plane_id: str, solution_id: str) -> dict[str, object]:
    return {
        "model_ref": "model:7",
        "model_revision": "revision:12",
        "dataset_id": f"dataset:{solution_id}",
        "solution_id": solution_id,
        "frequency_hz": 193.1e12,
        "plane_id": plane_id,
    }


def _field(field_id: str, plane_id: str, coordinates: list[list[float]],
           amplitudes: list[complex], polarization: tuple[complex, complex, complex], *,
           include_mode: bool = False,
           normal: tuple[float, float, float] = (0.0, 0.0, 1.0)) -> dict[str, object]:
    electric: list[list[complex]] = []
    magnetic: list[list[complex]] = []
    forward = normal
    for amplitude in amplitudes:
        e = tuple(amplitude * component for component in polarization)
        h = tuple(component / ETA_0 for component in _cross(forward, e))
        electric.append(list(e))
        magnetic.append(list(h))

    def parts(values: list[list[complex]], unit: str) -> dict[str, object]:
        return {
            "unit": unit,
            "real": [[component.real for component in row] for row in values],
            "imag": [[component.imag for component in row] for row in values],
        }

    result: dict[str, object] = {
        "field_id": field_id,
        "source": _source(plane_id, f"solution:{field_id}"),
        "phasor_convention": copy.deepcopy(PHASOR_CONVENTION),
        "coordinate_unit": "m",
        "sample_coordinates": copy.deepcopy(coordinates),
        "component_order": ["x", "y", "z"],
        "electric_field": parts(electric, "V/m"),
        "magnetic_field": parts(magnetic, "A/m"),
    }
    if include_mode:
        result["mode"] = {
            "mode_id": "port-mode:TE0/polarization-x",
            "eigenvalue": {"real": 4.2e6, "imag": 0.0},
            "eigenvalue_unit": "1/m",
        }
    return result


def _incident(power: float, *, plane_id: str = "plane:input", unit: str = "W",
              reference_id: str = "incident:port-1") -> dict[str, object]:
    return {
        "reference_id": reference_id,
        "power": power,
        "unit": unit,
        "input_plane_id": plane_id,
        "source": _source(plane_id, "solution:incident-reference"),
        "phasor_convention": copy.deepcopy(PHASOR_CONVENTION),
    }


def _definition(signal_amplitudes: list[complex] | None = None,
                mode_amplitudes: list[complex] | None = None,
                *, weights: list[float] | None = None,
                geometry_dimension: int = 3,
                signal_polarization: tuple[complex, complex, complex] = (1 + 0j, 0j, 0j),
                mode_polarization: tuple[complex, complex, complex] = (1 + 0j, 0j, 0j),
                normal: tuple[float, float, float] = (0.0, 0.0, 1.0)) -> dict[str, object]:
    weights = weights or [1.0, 1.0, 1.0]
    count = len(weights)
    coordinates = [[0.0, (index / max(count - 1, 1)), 0.0] for index in range(count)]
    signal_amplitudes = signal_amplitudes or [1 + 0j] * len(weights)
    mode_amplitudes = mode_amplitudes or [1 + 0j] * len(weights)
    weight_unit = "m^2" if geometry_dimension == 3 else "m"
    power_unit = "W" if geometry_dimension == 3 else "W/m"
    measure = "area" if geometry_dimension == 3 else "line_per_unit_depth"
    base_incident = 0.5 / ETA_0 * sum(w * (abs(a) ** 2) for w, a in zip(weights, mode_amplitudes))
    plane = {
        "plane_id": "plane:output-port",
        "geometry_dimension": geometry_dimension,
        "integration_measure": measure,
        "coordinate_unit": "m",
        "coordinates": coordinates,
        "quadrature_weight_unit": weight_unit,
        "quadrature_weights": weights,
        "normal": list(normal),
    }
    return {
        "schema_version": DEFINITION_SCHEMA_VERSION,
        "profile": APPLICABILITY_PROFILE,
        "phasor_convention": copy.deepcopy(PHASOR_CONVENTION),
        "plane": plane,
        "signal": _field("field:signal", plane["plane_id"], coordinates, signal_amplitudes, signal_polarization, normal=normal),
        "reference_mode": _field("field:reference-mode", plane["plane_id"], coordinates, mode_amplitudes, mode_polarization, include_mode=True, normal=normal),
        "power_floor": {"value": 1e-15, "unit": power_unit},
        "incident_reference_power": _incident(base_incident, unit=power_unit),
    }


def _set_coordinates(definition: dict[str, object], coordinates: list[list[float]]) -> None:
    definition["plane"]["coordinates"] = copy.deepcopy(coordinates)
    for field_name in ("signal", "reference_mode"):
        definition[field_name]["sample_coordinates"] = copy.deepcopy(coordinates)


def _rotate_and_translate(definition: dict[str, object], matrix: list[list[float]],
                          translation: list[float]) -> None:
    def transform_point(point: list[float], *, translate: bool) -> list[float]:
        rotated = [sum(matrix[row][column] * point[column] for column in range(3)) for row in range(3)]
        return [rotated[index] + (translation[index] if translate else 0.0) for index in range(3)]

    coordinates = definition["plane"]["coordinates"]
    definition["plane"]["coordinates"] = [transform_point(point, translate=True) for point in coordinates]
    definition["plane"]["normal"] = transform_point(definition["plane"]["normal"], translate=False)
    for field_name in ("signal", "reference_mode"):
        field = definition[field_name]
        field["sample_coordinates"] = copy.deepcopy(definition["plane"]["coordinates"])
        for component_name in ("electric_field", "magnetic_field"):
            samples = field[component_name]
            real_rows = samples["real"]
            imag_rows = samples["imag"]
            new_real: list[list[float]] = []
            new_imag: list[list[float]] = []
            for real, imag in zip(real_rows, imag_rows):
                vector = [complex(real[index], imag[index]) for index in range(3)]
                rotated = [sum(matrix[row][column] * vector[column] for column in range(3)) for row in range(3)]
                new_real.append([value.real for value in rotated])
                new_imag.append([value.imag for value in rotated])
            samples["real"] = new_real
            samples["imag"] = new_imag


def _expect_error(test: unittest.TestCase, code: str, definition: dict[str, object]) -> None:
    with test.assertRaises(ExecutionContractError) as caught:
        compute_mode_overlap(definition)
    test.assertEqual(caught.exception.code, code)


def _assert_matches_schema(test: unittest.TestCase, value: object, schema: dict[str, object],
                            root: dict[str, object] | None = None, path: str = "$" ) -> None:
    """Check the JSON Schema subset used by the published v1 contracts."""
    root = schema if root is None else root
    if "$ref" in schema:
        ref = schema["$ref"]
        test.assertIsInstance(ref, str)
        target: object = root
        for segment in ref[2:].split("/"):
            target = target[segment]
        _assert_matches_schema(test, value, target, root, path)
    for branch in schema.get("allOf", []):
        _assert_matches_schema(test, value, branch, root, path)
    expected_type = schema.get("type")
    if expected_type is not None:
        types = expected_type if isinstance(expected_type, list) else [expected_type]
        predicates = {
            "object": lambda x: isinstance(x, dict),
            "array": lambda x: isinstance(x, list),
            "string": lambda x: isinstance(x, str),
            "number": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x)),
            "integer": lambda x: isinstance(x, int) and not isinstance(x, bool),
            "boolean": lambda x: isinstance(x, bool),
            "null": lambda x: x is None,
        }
        test.assertTrue(any(predicates[t](value) for t in types), f"{path} must have JSON type {expected_type!r}")
    if "const" in schema:
        test.assertEqual(value, schema["const"], f"{path} does not match const")
    if "enum" in schema:
        test.assertIn(value, schema["enum"], f"{path} is outside enum")
    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            test.assertIn(key, value, f"{path} missing required key {key!r}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            test.assertFalse(set(value) - set(properties), f"{path} has unsupported keys")
        for key, child in properties.items():
            if key in value:
                _assert_matches_schema(test, value[key], child, root, f"{path}.{key}")
    if isinstance(value, list):
        if "minItems" in schema:
            test.assertGreaterEqual(len(value), schema["minItems"], path)
        if "maxItems" in schema:
            test.assertLessEqual(len(value), schema["maxItems"], path)
        if schema.get("uniqueItems"):
            test.assertEqual(len(value), len({json.dumps(item, sort_keys=True) for item in value}), path)
        if "items" in schema:
            for index, item in enumerate(value):
                _assert_matches_schema(test, item, schema["items"], root, f"{path}[{index}]")
    if isinstance(value, str) and "minLength" in schema:
        test.assertGreaterEqual(len(value), schema["minLength"], path)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema:
            test.assertGreaterEqual(value, schema["minimum"], path)
        if "exclusiveMinimum" in schema:
            test.assertGreater(value, schema["exclusiveMinimum"], path)


class ModeOverlapAnalyticTests(unittest.TestCase):
    def test_identical_forward_mode_has_known_power_and_unit_overlap(self) -> None:
        result = compute_mode_overlap(_definition())
        expected_power = 0.5 * 3.0 / ETA_0
        self.assertAlmostEqual(result["signal_power"]["value"], expected_power, places=14)
        self.assertAlmostEqual(result["reference_mode_power"]["value"], expected_power, places=14)
        self.assertAlmostEqual(result["unnormalized_overlap_numerator"]["real"], 2.0 * 3.0 / ETA_0, places=14)
        self.assertAlmostEqual(result["unnormalized_overlap_numerator"]["imag"], 0.0, places=14)
        self.assertAlmostEqual(result["overlap_amplitude"]["real"], 1.0, places=14)
        self.assertAlmostEqual(result["normalized_overlap"], 1.0, places=14)
        self.assertEqual(result["signal_power"]["unit"], "W")
        self.assertEqual(result["evidence_status"], {
            "scope": "SOFTWARE_ONLY", "native_status": "NOT_RUN",
            "input_boundary": "CALLER_SUPPLIED_ARRAYS_NOT_NATIVE_EVIDENCE",
        })
        self.assertEqual(result["phasor_convention"], PHASOR_CONVENTION)
        self.assertEqual(result["surface"]["planarity_roundoff"]["policy_id"], PLANARITY_BOUND_POLICY)
        self.assertEqual(result["surface"]["planarity_roundoff"]["safety_factor"], PLANARITY_ULP_SAFETY_FACTOR)

    def test_supported_time_sign_is_explicit_and_preserved(self) -> None:
        definition = _definition()
        convention = copy.deepcopy(PHASOR_CONVENTION)
        convention["time_dependence"] = "exp(+i omega t)"
        definition["phasor_convention"] = copy.deepcopy(convention)
        for field_name in ("signal", "reference_mode"):
            definition[field_name]["phasor_convention"] = copy.deepcopy(convention)
        definition["incident_reference_power"]["phasor_convention"] = copy.deepcopy(convention)
        result = compute_mode_overlap(definition)
        self.assertEqual(result["phasor_convention"], convention)
        self.assertEqual(result["identities"]["signal"]["phasor_convention"], convention)
        self.assertEqual(result["incident_reference_power"]["phasor_convention"], convention)

    def test_rotated_and_translated_common_plane_preserves_overlap(self) -> None:
        definition = _definition()
        _set_coordinates(definition, [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
        baseline = compute_mode_overlap(definition)
        angle_y = 0.37
        angle_z = -0.62
        cy, sy = math.cos(angle_y), math.sin(angle_y)
        cz, sz = math.cos(angle_z), math.sin(angle_z)
        rotation_y = [[cy, 0.0, sy], [0.0, 1.0, 0.0], [-sy, 0.0, cy]]
        rotation_z = [[cz, -sz, 0.0], [sz, cz, 0.0], [0.0, 0.0, 1.0]]
        rotation = [
            [sum(rotation_y[row][k] * rotation_z[k][column] for k in range(3)) for column in range(3)]
            for row in range(3)
        ]
        transformed = copy.deepcopy(definition)
        _rotate_and_translate(transformed, rotation, [1234.5, -2345.6, 3456.7])
        rotated_result = compute_mode_overlap(transformed)
        self.assertAlmostEqual(rotated_result["signal_power"]["value"], baseline["signal_power"]["value"], places=12)
        self.assertAlmostEqual(rotated_result["overlap_amplitude"]["real"], baseline["overlap_amplitude"]["real"], places=12)
        self.assertAlmostEqual(rotated_result["overlap_amplitude"]["imag"], baseline["overlap_amplitude"]["imag"], places=12)
        self.assertAlmostEqual(rotated_result["normalized_overlap"], baseline["normalized_overlap"], places=12)
        roundoff = rotated_result["surface"]["planarity_roundoff"]
        self.assertEqual(roundoff["policy_id"], PLANARITY_BOUND_POLICY)
        self.assertEqual(roundoff["safety_factor"], 64)
        self.assertGreater(roundoff["maximum_sample_bound_m"], 0.0)
        self.assertLess(roundoff["maximum_sample_bound_m"], 1e-9)

    def test_half_amplitude_signal_keeps_overlap_one_but_eta_mode_quarter(self) -> None:
        definition = _definition(signal_amplitudes=[0.5 + 0j] * 3)
        result = compute_mode_overlap(definition)
        self.assertAlmostEqual(result["normalized_overlap"], 1.0, places=14)
        self.assertAlmostEqual(result["projected_mode_power"]["value"], 0.25 * 1.5 / ETA_0, places=14)
        self.assertAlmostEqual(result["eta_mode"]["value"], 0.25, places=14)
        self.assertEqual(result["eta_mode"]["denominator"]["input_plane_id"], "plane:input")
        self.assertEqual(result["eta_mode"]["denominator"]["reference_id"], "incident:port-1")

    def test_signal_and_reference_rescaling_have_distinct_power_effects(self) -> None:
        base = compute_mode_overlap(_definition())
        signal_scaled = compute_mode_overlap(_definition(signal_amplitudes=[3 + 0j] * 3))
        mode_scaled = compute_mode_overlap(_definition(mode_amplitudes=[5 + 0j] * 3))
        self.assertAlmostEqual(signal_scaled["normalized_overlap"], 1.0, places=14)
        self.assertAlmostEqual(signal_scaled["projected_mode_power"]["value"], 9 * base["projected_mode_power"]["value"], places=14)
        self.assertAlmostEqual(mode_scaled["normalized_overlap"], 1.0, places=14)
        self.assertAlmostEqual(mode_scaled["projected_mode_power"]["value"], base["projected_mode_power"]["value"], places=14)

    def test_global_phase_changes_complex_amplitude_not_power_or_efficiency(self) -> None:
        phase = 0.73
        rotated = [complex(math.cos(phase), math.sin(phase))] * 3
        base = compute_mode_overlap(_definition())
        result = compute_mode_overlap(_definition(signal_amplitudes=rotated))
        self.assertAlmostEqual(result["overlap_amplitude"]["real"], math.cos(phase), places=14)
        self.assertAlmostEqual(result["overlap_amplitude"]["imag"], math.sin(phase), places=14)
        self.assertAlmostEqual(result["normalized_overlap"], 1.0, places=14)
        self.assertAlmostEqual(result["signal_power"]["value"], base["signal_power"]["value"], places=14)
        self.assertAlmostEqual(result["eta_mode"]["value"], 1.0, places=14)

    def test_orthogonal_polarization_has_zero_overlap(self) -> None:
        result = compute_mode_overlap(_definition(
            signal_polarization=(0j, 1 + 0j, 0j),
            mode_polarization=(1 + 0j, 0j, 0j),
        ))
        self.assertAlmostEqual(result["normalized_overlap"], 0.0, places=14)
        self.assertAlmostEqual(result["projected_mode_power"]["value"], 0.0, places=14)
        self.assertAlmostEqual(result["eta_mode"]["value"], 0.0, places=14)

    def test_displaced_profile_matches_independent_closed_form(self) -> None:
        result = compute_mode_overlap(
            _definition(signal_amplitudes=[1 + 0j, 2 + 0j, 1 + 0j],
                        mode_amplitudes=[0 + 0j, 1 + 0j, 2 + 0j])
        )
        # Sum(signal*mode)=4; sum(signal^2)=6; sum(mode^2)=5.
        self.assertAlmostEqual(result["overlap_amplitude"]["real"], 4.0 / math.sqrt(30.0), places=14)
        self.assertAlmostEqual(result["normalized_overlap"], 8.0 / 15.0, places=14)

    def test_nonzero_imaginary_data_is_not_silently_dropped(self) -> None:
        with_imaginary = _definition(signal_amplitudes=[1 + 1j] * 3)
        retained = compute_mode_overlap(with_imaginary)
        dropped = copy.deepcopy(with_imaginary)
        for field_name in ("electric_field", "magnetic_field"):
            field = dropped["signal"][field_name]
            field["imag"] = [[0.0, 0.0, 0.0] for _ in field["real"]]
        omitted = compute_mode_overlap(dropped)
        self.assertAlmostEqual(retained["normalized_overlap"], 1.0, places=14)
        self.assertAlmostEqual(omitted["normalized_overlap"], 1.0, places=14)
        self.assertAlmostEqual(retained["signal_power"]["value"], 3.0 / ETA_0, places=14)
        self.assertAlmostEqual(omitted["signal_power"]["value"], 1.5 / ETA_0, places=14)
        self.assertAlmostEqual(retained["eta_mode"]["value"], 2.0, places=14)
        self.assertAlmostEqual(omitted["eta_mode"]["value"], 1.0, places=14)

    def test_capture_is_aperture_flux_with_its_own_reference_denominator(self) -> None:
        definition = _definition()
        definition["capture"] = {
            "aperture_id": "receiver-aperture:two-of-three",
            "plane_id": "plane:output-port",
            "sample_indices": [0, 2],
            "incident_reference_power": _incident(2.0 / ETA_0, reference_id="incident:capture-reference"),
        }
        result = compute_mode_overlap(definition)
        self.assertAlmostEqual(result["eta_mode"]["value"], 1.0, places=14)
        capture = result["eta_capture"]
        self.assertAlmostEqual(capture["numerator"]["value"], 1.0 / ETA_0, places=14)
        self.assertAlmostEqual(capture["value"], 0.5, places=14)
        self.assertEqual(capture["region"]["aperture_id"], "receiver-aperture:two-of-three")
        self.assertEqual(capture["denominator"]["reference_id"], "incident:capture-reference")
        self.assertEqual(capture["denominator"]["input_plane_id"], "plane:input")

    def test_two_dimensional_line_integral_reports_watts_per_metre(self) -> None:
        result = compute_mode_overlap(_definition(
            geometry_dimension=2, weights=[0.25, 0.75],
            signal_polarization=(0j, 0j, 1 + 0j),
            mode_polarization=(0j, 0j, 1 + 0j),
            normal=(1.0, 0.0, 0.0),
        ))
        self.assertAlmostEqual(result["signal_power"]["value"], 0.5 / ETA_0, places=14)
        self.assertEqual(result["signal_power"]["unit"], "W/m")
        self.assertEqual(result["surface"]["integration_measure"], "line_per_unit_depth")
        self.assertEqual(result["eta_mode"]["denominator"]["unit"], "W/m")


class ModeOverlapFailureTests(unittest.TestCase):
    def test_main_overlap_planarity_reproducer_rejects_warped_common_samples(self) -> None:
        evidence_path = ROOT / "docs" / "full_project_execution" / "w23_overlap" / "evidence" / "main_overlap_planarity_reproducer.json"
        evidence = json.loads(evidence_path.read_text())
        definition = evidence["input"]
        self.assertEqual(definition["plane"]["normal"], [0.0, 0.0, 1.0])
        self.assertEqual(definition["plane"]["coordinates"][1][2], 0.1)
        for field_name in ("signal", "reference_mode"):
            self.assertEqual(definition[field_name]["sample_coordinates"], definition["plane"]["coordinates"])
        _expect_error(self, evidence["expected"]["error_code"], definition)

    def test_registered_surface_with_tilt_against_normal_is_rejected(self) -> None:
        definition = _definition()
        coordinates = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.1], [0.0, 1.0, 0.0]]
        _set_coordinates(definition, coordinates)
        _expect_error(self, "NON_PLANAR_SAMPLING_GEOMETRY", definition)

    def test_micrometre_plane_rejects_half_nanometre_physical_warp(self) -> None:
        definition = _definition()
        coordinates = [[0.0, 0.0, 0.0], [0.0, 0.5e-6, 0.5e-9], [0.0, 1.0e-6, 0.0]]
        definition["plane"]["quadrature_weights"] = [1e-12, 1e-12, 1e-12]
        definition["incident_reference_power"]["power"] *= 1e-12
        _set_coordinates(definition, coordinates)
        _expect_error(self, "NON_PLANAR_SAMPLING_GEOMETRY", definition)

    def test_nonfinite_local_difference_or_ulp_bound_is_rejected(self) -> None:
        definition = _definition()
        coordinates = [[0.0, 0.0, -1.0e308], [0.0, 1.0, 1.0e308], [0.0, 2.0, 0.0]]
        _set_coordinates(definition, coordinates)
        _expect_error(self, "NON_FINITE_GEOMETRY", definition)

    def test_missing_unknown_or_unreconstructed_phasor_conventions_fail(self) -> None:
        definition = _definition()
        del definition["signal"]["phasor_convention"]
        _expect_error(self, "INVALID_REQUEST", definition)
        definition = _definition()
        definition["phasor_convention"]["time_dependence"] = "unknown"
        _expect_error(self, "UNSUPPORTED_PHASOR_CONVENTION", definition)
        definition = _definition()
        definition["reference_mode"]["phasor_convention"]["complex_field_representation"] = "unreconstructed_envelope"
        _expect_error(self, "UNSUPPORTED_FIELD_REPRESENTATION", definition)

    def test_signal_reference_and_incident_phasor_conventions_must_match(self) -> None:
        definition = _definition()
        definition["signal"]["phasor_convention"]["time_dependence"] = "exp(+i omega t)"
        _expect_error(self, "PHASOR_CONVENTION_MISMATCH", definition)
        definition = _definition()
        definition["reference_mode"]["phasor_convention"]["time_dependence"] = "exp(+i omega t)"
        _expect_error(self, "PHASOR_CONVENTION_MISMATCH", definition)
        definition = _definition()
        definition["incident_reference_power"]["phasor_convention"]["time_dependence"] = "exp(+i omega t)"
        _expect_error(self, "PHASOR_CONVENTION_MISMATCH", definition)
        definition = _definition()
        definition["capture"] = {
            "aperture_id": "receiver-aperture:test",
            "plane_id": "plane:output-port",
            "sample_indices": [0],
            "incident_reference_power": _incident(1.0 / ETA_0, reference_id="incident:capture-reference"),
        }
        definition["capture"]["incident_reference_power"]["phasor_convention"]["time_dependence"] = "exp(+i omega t)"
        _expect_error(self, "PHASOR_CONVENTION_MISMATCH", definition)

    def test_missing_imaginary_representation_fails_even_when_values_are_real(self) -> None:
        definition = _definition()
        del definition["signal"]["electric_field"]["imag"]
        _expect_error(self, "COMPLEX_DATA_ERROR", definition)

    def test_mismatched_plane_and_coordinates_fail(self) -> None:
        definition = _definition()
        definition["reference_mode"]["source"]["plane_id"] = "plane:other"
        _expect_error(self, "PLANE_MISMATCH", definition)
        definition = _definition()
        definition["reference_mode"]["sample_coordinates"][1][1] += 1e-12
        _expect_error(self, "COORDINATE_MISMATCH", definition)

    def test_coordinate_and_field_units_must_be_explicit_and_compatible(self) -> None:
        definition = _definition()
        definition["signal"]["coordinate_unit"] = "mm"
        _expect_error(self, "UNIT_MISMATCH", definition)
        definition = _definition()
        definition["reference_mode"]["magnetic_field"]["unit"] = "V/m"
        _expect_error(self, "UNIT_MISMATCH", definition)

    def test_nonunit_and_reversed_normals_fail_without_renormalization(self) -> None:
        definition = _definition()
        definition["plane"]["normal"] = [0.0, 0.0, 1.01]
        _expect_error(self, "INVALID_NORMAL", definition)
        definition = _definition()
        definition["plane"]["normal"] = [0.0, 0.0, -1.0]
        _expect_error(self, "NON_POSITIVE_FORWARD_POWER", definition)

    def test_zero_negative_and_near_zero_powers_fail(self) -> None:
        definition = _definition(signal_amplitudes=[0j] * 3)
        _expect_error(self, "NON_POSITIVE_FORWARD_POWER", definition)
        definition = _definition()
        definition["reference_mode"]["magnetic_field"]["real"] = [[0.0, 0.0, 0.0] for _ in range(3)]
        definition["reference_mode"]["magnetic_field"]["imag"] = [[0.0, 0.0, 0.0] for _ in range(3)]
        _expect_error(self, "NON_POSITIVE_FORWARD_POWER", definition)
        definition = _definition()
        definition["incident_reference_power"]["power"] = 0.0
        _expect_error(self, "NON_POSITIVE_INCIDENT_POWER", definition)
        definition = _definition()
        definition["incident_reference_power"]["power"] = -1.0
        _expect_error(self, "NON_POSITIVE_INCIDENT_POWER", definition)
        definition = _definition()
        definition["incident_reference_power"]["power"] = 1e-16
        _expect_error(self, "NON_POSITIVE_INCIDENT_POWER", definition)

    def test_malformed_ragged_and_nonfinite_shapes_fail(self) -> None:
        definition = _definition()
        definition["signal"]["electric_field"]["imag"][1] = [0.0, 0.0]
        _expect_error(self, "MODE_OVERLAP_DATA_ERROR", definition)
        definition = _definition()
        definition["signal"]["magnetic_field"]["real"][0][1] = float("nan")
        _expect_error(self, "MODE_OVERLAP_DATA_ERROR", definition)
        definition = _definition()
        definition["plane"]["quadrature_weights"][0] = -1.0
        _expect_error(self, "MODE_OVERLAP_DATA_ERROR", definition)

    def test_dimension_measure_and_reference_units_must_match(self) -> None:
        definition = _definition(geometry_dimension=2)
        definition["plane"]["quadrature_weight_unit"] = "m^2"
        _expect_error(self, "UNIT_MISMATCH", definition)
        definition = _definition(geometry_dimension=3)
        definition["incident_reference_power"]["unit"] = "W/m"
        _expect_error(self, "UNIT_MISMATCH", definition)

    def test_efficiency_is_reported_raw_and_never_clamped(self) -> None:
        definition = _definition(signal_amplitudes=[2 + 0j] * 3)
        definition["eta_mode_upper_tolerance"] = 1e-6
        result = compute_mode_overlap(definition)
        self.assertAlmostEqual(result["eta_mode"]["value"], 4.0, places=14)
        self.assertEqual(result["eta_mode_range_diagnostic"]["status"], "EXCEEDS_DECLARED_UPPER_BOUND")
        self.assertFalse(result["eta_mode_range_diagnostic"]["clamped"])
        self.assertAlmostEqual(result["eta_mode_range_diagnostic"]["raw_eta_mode"], 4.0, places=14)

    def test_source_identity_frequency_and_schema_are_checked(self) -> None:
        definition = _definition()
        definition["reference_mode"]["source"]["model_revision"] = "revision:stale"
        _expect_error(self, "SOURCE_MISMATCH", definition)
        definition = _definition()
        definition["schema_version"] = "0.9.0"
        _expect_error(self, "SCHEMA_VERSION_UNSUPPORTED", definition)
        definition = _definition()
        definition["reference_mode"]["source"]["frequency_hz"] += 1.0
        _expect_error(self, "SOURCE_MISMATCH", definition)
        definition = _definition()
        definition["profile"] = "arbitrary_lossy_mode"
        _expect_error(self, "APPLICABILITY_PROFILE_UNSUPPORTED", definition)


class ModeOverlapSchemaTests(unittest.TestCase):
    def test_published_schemas_match_runtime_contract(self) -> None:
        docs = ROOT / "docs" / "full_project_execution" / "w23_overlap"
        self.assertEqual(json.loads((docs / "definition.schema.json").read_text()), DEFINITION_SCHEMA)
        self.assertEqual(json.loads((docs / "result.schema.json").read_text()), RESULT_SCHEMA)
        self.assertEqual(DEFINITION_SCHEMA["$id"], "urn:comsol-mcp:result.mode_overlap:kernel-definition:1.1.0")
        self.assertEqual(RESULT_SCHEMA["$id"], "urn:comsol-mcp:result.mode_overlap:result:1.2.0")
        self.assertEqual(DEFINITION_SCHEMA_VERSION, "1.1.0")
        self.assertEqual(RESULT_SCHEMA_VERSION, "1.2.0")
        _assert_matches_schema(self, _definition(), DEFINITION_SCHEMA)

    def test_result_is_json_serializable_and_disclaims_native_evidence(self) -> None:
        result = compute_mode_overlap(_definition())
        json.dumps(result, allow_nan=False)
        _assert_matches_schema(self, result, RESULT_SCHEMA)
        self.assertEqual(result["evidence_status"]["scope"], "SOFTWARE_ONLY")
        self.assertEqual(result["evidence_status"]["native_status"], "NOT_RUN")


if __name__ == "__main__":
    unittest.main()
