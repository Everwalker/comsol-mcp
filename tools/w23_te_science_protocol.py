"""Pure protocol and numerical checks for the W23 native TE campaign.

This module never starts COMSOL.  Its output distinguishes software controls
from native evidence; callers must bind its numerical inputs to the exact raw
COMSOL field and integration receipts before reporting a native comparison.
"""
from __future__ import annotations

import cmath
import math
from collections.abc import Mapping, Sequence
from typing import Any


SAMPLE_COUNT = 321  # Coarse composite Simpson grid: 320 even intervals.
REFINED_SAMPLE_COUNT = 641  # Fine grid doubles intervals while preserving endpoints.
QUADRATURE_SAMPLE_COUNTS = (SAMPLE_COUNT, REFINED_SAMPLE_COUNT)
INTEGRAL_RELATIVE_TOLERANCE = 1e-3
PHASE_FIELD_RELATIVE_TOLERANCE = 1e-3
PHASE_INTEGRAL_RELATIVE_TOLERANCE = 1e-3
EXPECTED_FIELD_PHASE_FACTOR = 1j
POWER_UNIT_2D = "W/m"
CAPTURE_APERTURE_ID = "receiver_core_aperture"
CAPTURE_Y_MIN_M = -0.5e-6
CAPTURE_Y_MAX_M = 0.5e-6


class ScienceProtocolError(ValueError):
    """The frozen native evidence does not satisfy a software acceptance gate."""


def reconcile_direct_rpc_events(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Require every direct Worker RPC to have one exact terminal observation.

    ``PersistentJavaWorker._request`` is instrumented before the first direct
    operation.  Managed calls are reconciled separately from the SQLite
    OperationStore and are intentionally not copied into this direct journal.
    """
    if isinstance(events, (str, bytes)) or not isinstance(events, Sequence):
        raise ScienceProtocolError("direct RPC journal must be an event sequence")
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    malformed: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        if not isinstance(event, Mapping):
            malformed.append({"index": index, "reason": "event_not_mapping"})
            continue
        rpc_id = event.get("rpc_id")
        phase = event.get("phase")
        if not isinstance(rpc_id, str) or not rpc_id or phase not in {"submitted", "observed", "unknown"}:
            malformed.append({"index": index, "reason": "missing_rpc_identity_or_invalid_phase"})
            continue
        grouped.setdefault(rpc_id, []).append(event)

    records: list[dict[str, Any]] = []
    pending: list[str] = []
    ambiguous: list[str] = []
    unknown: list[str] = []
    orphans: list[str] = []
    for rpc_id, rows in sorted(grouped.items()):
        submitted = [row for row in rows if row.get("phase") == "submitted"]
        observed = [row for row in rows if row.get("phase") == "observed"]
        unknown_rows = [row for row in rows if row.get("phase") == "unknown"]
        if len(submitted) != 1 or len(observed) + len(unknown_rows) != 1:
            if not submitted:
                orphans.append(rpc_id)
            elif not observed and not unknown_rows:
                pending.append(rpc_id)
            if len(submitted) > 1 or len(observed) + len(unknown_rows) > 1:
                ambiguous.append(rpc_id)
        terminal = False
        terminal_status = None
        if len(submitted) == 1 and len(observed) == 1 and not unknown_rows:
            terminal_status = observed[0].get("status")
            terminal = terminal_status in {"SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED"}
            if not terminal:
                unknown.append(rpc_id)
        elif unknown_rows:
            unknown.append(rpc_id)
        records.append({"rpc_id": rpc_id, "submitted_count": len(submitted),
                        "observed_count": len(observed), "unknown_count": len(unknown_rows),
                        "terminal_status": terminal_status, "terminal": terminal})

    complete = not (malformed or pending or ambiguous or unknown or orphans)
    return {
        "status": "PASS_ALL_DIRECT_RPCS_TERMINAL" if complete else "BLOCKED_DIRECT_RPC_UNRESOLVED",
        "safe_for_owned_cleanup": complete,
        "event_count": len(events), "rpc_count": len(grouped), "records": records,
        "pending_rpc_ids": pending, "unknown_rpc_ids": unknown,
        "orphan_rpc_ids": orphans, "ambiguous_rpc_ids": ambiguous,
        "malformed_events": malformed,
    }


def combine_cleanup_gates(managed_reconciliation: Mapping[str, Any],
                          direct_reconciliation: Mapping[str, Any]) -> dict[str, Any]:
    """Keep managed operation terminality and direct RPC terminality distinct."""
    managed_safe = (isinstance(managed_reconciliation, Mapping)
                    and managed_reconciliation.get("safe_for_owned_cleanup") is True)
    direct_safe = (isinstance(direct_reconciliation, Mapping)
                   and direct_reconciliation.get("safe_for_owned_cleanup") is True)
    return {
        "status": "PASS_BOTH_RPC_JOURNALS_QUIESCENT" if managed_safe and direct_safe
                 else "BLOCKED_UNLESS_BOTH_RPC_JOURNALS_QUIESCENT",
        "safe_for_owned_cleanup": managed_safe and direct_safe,
        "managed_sqlite_ledger_safe": managed_safe,
        "direct_rpc_journal_safe": direct_safe,
        "managed_domain_unknown_preserved": (
            managed_reconciliation.get("durable_result") if isinstance(managed_reconciliation, Mapping) else None
        ),
    }


def _as_finite_float(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ScienceProtocolError(f"{label} must be finite numeric data")
    return float(value)


def simpson_integral(values: Sequence[complex], coordinates_m: Sequence[float]) -> complex:
    """Composite Simpson integral for a uniformly spaced, odd-size vector."""
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ScienceProtocolError("integrand must be a sequence")
    if isinstance(coordinates_m, (str, bytes)) or not isinstance(coordinates_m, Sequence):
        raise ScienceProtocolError("coordinates must be a sequence")
    if len(values) != len(coordinates_m) or len(values) not in QUADRATURE_SAMPLE_COUNTS:
        raise ScienceProtocolError(f"registered quadrature requires {QUADRATURE_SAMPLE_COUNTS} values and coordinates")
    coords = [_as_finite_float(x, "coordinate") for x in coordinates_m]
    h = coords[1] - coords[0]
    if h <= 0:
        raise ScienceProtocolError("quadrature coordinates must be strictly increasing")
    for index in range(2, len(coords)):
        delta = coords[index] - coords[index - 1]
        if not math.isclose(delta, h, rel_tol=1e-10, abs_tol=1e-18):
            raise ScienceProtocolError("registered quadrature coordinates must be uniformly spaced")
    complex_values: list[complex] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float, complex)):
            raise ScienceProtocolError("integrand contains a nonnumeric value")
        item = complex(value)
        if not math.isfinite(item.real) or not math.isfinite(item.imag):
            raise ScienceProtocolError("integrand contains nonfinite values")
        complex_values.append(item)
    total = complex_values[0] + complex_values[-1]
    total += 4 * sum(complex_values[1:-1:2], 0j)
    total += 2 * sum(complex_values[2:-1:2], 0j)
    return total * h / 3


def _simpson_uniform_any(values: Sequence[complex], coordinates_m: Sequence[float], *, label: str) -> complex:
    """Composite Simpson integration for an odd-size registered subgrid."""
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ScienceProtocolError(f"{label} integrand must be a sequence")
    if isinstance(coordinates_m, (str, bytes)) or not isinstance(coordinates_m, Sequence):
        raise ScienceProtocolError(f"{label} coordinates must be a sequence")
    if len(values) != len(coordinates_m) or len(values) < 3 or len(values) % 2 != 1:
        raise ScienceProtocolError(f"{label} requires matching values on an odd grid of at least three points")
    coords = [_as_finite_float(value, f"{label} coordinate") for value in coordinates_m]
    h = coords[1] - coords[0]
    if h <= 0:
        raise ScienceProtocolError(f"{label} coordinates must be strictly increasing")
    for index in range(2, len(coords)):
        if not math.isclose(coords[index] - coords[index - 1], h, rel_tol=1e-10, abs_tol=1e-18):
            raise ScienceProtocolError(f"{label} coordinates must be uniformly spaced")
    terms: list[complex] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float, complex)):
            raise ScienceProtocolError(f"{label} integrand contains a nonnumeric value")
        item = complex(value)
        if not math.isfinite(item.real) or not math.isfinite(item.imag):
            raise ScienceProtocolError(f"{label} integrand contains nonfinite values")
        terms.append(item)
    total = terms[0] + terms[-1]
    total += 4 * sum(terms[1:-1:2], 0j)
    total += 2 * sum(terms[2:-1:2], 0j)
    return total * h / 3


def _raw_expression_map(raw: Mapping[str, Any]) -> dict[str, list[complex]]:
    sample_count = raw.get("sample_count")
    if raw.get("complex_readback") is not True or sample_count not in QUADRATURE_SAMPLE_COUNTS:
        raise ScienceProtocolError("raw field receipt is not one of the frozen native complex quadrature exports")
    rows = raw.get("expressions")
    if not isinstance(rows, list):
        raise ScienceProtocolError("raw field receipt has no expression rows")
    result: dict[str, list[complex]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("expression"), str):
            raise ScienceProtocolError("raw expression row has no exact expression name")
        name = row["expression"]
        if name in result:
            raise ScienceProtocolError(f"raw field receipt contains duplicate expression {name!r}")
        real, imag = row.get("real"), row.get("imag")
        if (not isinstance(real, list) or not isinstance(imag, list)
                or len(real) != sample_count or len(imag) != sample_count):
            raise ScienceProtocolError(f"{name} does not contain exactly {sample_count} real and imaginary samples")
        result[name] = [complex(_as_finite_float(a, f"{name}.real"),
                                _as_finite_float(b, f"{name}.imag")) for a, b in zip(real, imag)]
    return result


def _plane_coordinates(raw: Mapping[str, Any], *, expected_x_m: float) -> tuple[list[float], int, int]:
    sample_count = raw.get("sample_count")
    coordinates = raw.get("coordinates_m")
    if (not isinstance(coordinates, list) or len(coordinates) != 2
            or sample_count not in QUADRATURE_SAMPLE_COUNTS
            or not all(isinstance(row, list) and len(row) == sample_count for row in coordinates)):
        raise ScienceProtocolError("native coordinate readback must be a registered 2xN matrix in metres")
    xs = [_as_finite_float(x, "x coordinate") for x in coordinates[0]]
    ys = [_as_finite_float(y, "y coordinate") for y in coordinates[1]]
    if any(not math.isclose(x, expected_x_m, rel_tol=0, abs_tol=1e-12) for x in xs):
        raise ScienceProtocolError("COMSOL coordinate readback does not lie on the frozen x plane in metres")
    if not math.isclose(ys[0], -8e-6, rel_tol=0, abs_tol=1e-12) or not math.isclose(ys[-1], 8e-6, rel_tol=0, abs_tol=1e-12):
        raise ScienceProtocolError("COMSOL coordinate readback does not cover the frozen y span in metres")
    normals = _raw_expression_map(raw)
    nx, ny = normals.get("nx"), normals.get("ny")
    if nx is None or ny is None:
        raise ScienceProtocolError("native boundary normal nx/ny was not exported")
    if any(abs(abs(value.real) - 1) > 1e-10 or abs(value.imag) > 1e-12 for value in nx):
        raise ScienceProtocolError("native x-plane normal is not a constant unit x normal")
    if any(abs(value.real) > 1e-10 or abs(value.imag) > 1e-12 for value in ny):
        raise ScienceProtocolError("native x-plane normal has a nonzero y component")
    signs = {1 if value.real > 0 else -1 for value in nx}
    if len(signs) != 1:
        raise ScienceProtocolError("native x-plane boundary normal changes orientation across the selection")
    normal_sign = raw.get("normal_sign")
    if isinstance(normal_sign, bool) or normal_sign not in (-1, 1):
        raise ScienceProtocolError("raw field receipt lacks the exact declared native normal_sign")
    return ys, next(iter(signs)), normal_sign


def _poynting_x(fields: Mapping[str, list[complex]], electric: Mapping[str, str],
                magnetic: Mapping[str, str], *, conjugate_electric: bool = False,
                conjugate_magnetic: bool = True) -> list[complex]:
    ey = fields[electric["y"]]
    ez = fields[electric["z"]]
    hy = fields[magnetic["y"]]
    hz = fields[magnetic["z"]]
    if conjugate_electric:
        ey, ez = [value.conjugate() for value in ey], [value.conjugate() for value in ez]
    if conjugate_magnetic:
        return [a * b.conjugate() - c * d.conjugate() for a, b, c, d in zip(ey, hz, ez, hy)]
    return [a * b - c * d for a, b, c, d in zip(ey, hz, ez, hy)]


def independent_integrals(output_raw: Mapping[str, Any], input_raw: Mapping[str, Any]) -> dict[str, Any]:
    """Recompute all four overlap integrals from COMSOL-exported E/H arrays."""
    if output_raw.get("plane") != "output_x8" or input_raw.get("plane") != "input_xminus10":
        raise ScienceProtocolError("native raw exports are not bound to x=8 um and x=-10 um planes")
    y_out, native_out_nx, declared_out_sign = _plane_coordinates(output_raw, expected_x_m=8e-6)
    y_in, native_in_nx, declared_in_sign = _plane_coordinates(input_raw, expected_x_m=-10e-6)
    if y_out != y_in:
        raise ScienceProtocolError("output and incident reference grids differ")
    if output_raw.get("sample_count") != input_raw.get("sample_count"):
        raise ScienceProtocolError("output and incident reference exports use different quadrature levels")
    output_orientation = native_out_nx * declared_out_sign
    incident_orientation = native_in_nx * declared_in_sign
    if output_orientation != 1 or incident_orientation != 1:
        raise ScienceProtocolError("declared normal_sign and native boundary normal do not both point along physical +x")
    out = _raw_expression_map(output_raw)
    inc = _raw_expression_map(input_raw)
    signal_e = {axis: f"ewfd.E{axis}" for axis in "xyz"}
    signal_h = {axis: f"ewfd.H{axis}" for axis in "xyz"}
    output_mode_e = {axis: f"ewfd.Emode{axis}_2" for axis in "xyz"}
    output_mode_h = {axis: f"ewfd.Hmode{axis}_2" for axis in "xyz"}
    incident_mode_e = {axis: f"ewfd.Emode{axis}_1" for axis in "xyz"}
    incident_mode_h = {axis: f"ewfd.Hmode{axis}_1" for axis in "xyz"}
    signal_flux = _poynting_x(out, signal_e, signal_h)
    mode_flux = _poynting_x(out, output_mode_e, output_mode_h)
    incident_flux = _poynting_x(inc, incident_mode_e, incident_mode_h)
    cross_signal_mode = _poynting_x(out, signal_e, output_mode_h)
    cross_mode_signal = _poynting_x(out, output_mode_e, signal_h,
                                    conjugate_electric=True, conjugate_magnetic=False)
    signal_power = simpson_integral([0.5 * (value * output_orientation).real for value in signal_flux], y_out)
    mode_power = simpson_integral([0.5 * (value * output_orientation).real for value in mode_flux], y_out)
    incident_power = simpson_integral([0.5 * (value * incident_orientation).real for value in incident_flux], y_in)
    cross = simpson_integral([(a + b) * output_orientation for a, b in zip(cross_signal_mode, cross_mode_signal)], y_out)
    return {
        "status": "SOFTWARE_RECOMPUTED_FROM_NATIVE_RAW_FIELDS",
        "evidence_scope": "independent quadrature of exported native E/H; not itself a native result",
        "integration": "composite Simpson, registered 321 or 641 point grid",
        "unit": POWER_UNIT_2D,
        "output_native_normal_x": native_out_nx,
        "output_normal_sign": declared_out_sign,
        "incident_native_normal_x": native_in_nx,
        "incident_normal_sign": declared_in_sign,
        "signal_power": signal_power.real,
        "reference_mode_power": mode_power.real,
        "incident_reference_power": incident_power.real,
        "reciprocal_overlap_numerator": {"real": cross.real, "imag": cross.imag},
        "sample_count": output_raw["sample_count"],
    }


def compare_native_integrals(native: Mapping[str, Any], independent: Mapping[str, Any], *,
                             tolerance: float = INTEGRAL_RELATIVE_TOLERANCE) -> dict[str, Any]:
    if native.get("status") != "SUCCEEDED" or native.get("result_status") != "COMPUTED_NATIVE_INTEGRALS":
        raise ScienceProtocolError("result.mode_overlap did not return a clean native integration result")
    if independent.get("status") != "SOFTWARE_RECOMPUTED_FROM_NATIVE_RAW_FIELDS":
        raise ScienceProtocolError("independent field quadrature provenance is missing")
    integrals = native.get("integrals")
    if not isinstance(integrals, Mapping):
        raise ScienceProtocolError("native result omitted integral records")
    native_values: dict[str, complex] = {}
    for key in ("signal_power", "reference_mode_power", "incident_reference_power"):
        row = integrals.get(key)
        if not isinstance(row, Mapping) or row.get("unit") != POWER_UNIT_2D:
            raise ScienceProtocolError(f"native {key} unit is not verified as W/m")
        native_values[key] = complex(_as_finite_float(row.get("real"), f"native {key}"),
                                     _as_finite_float(row.get("imag", 0), f"native {key}.imag"))
    cross_row = integrals.get("reciprocal_overlap_numerator")
    if not isinstance(cross_row, Mapping) or cross_row.get("unit") != POWER_UNIT_2D:
        raise ScienceProtocolError("native complex cross-integral unit is not verified as W/m")
    native_values["reciprocal_overlap_numerator"] = complex(
        _as_finite_float(cross_row.get("real"), "native overlap real"),
        _as_finite_float(cross_row.get("imag"), "native overlap imag"))
    relative: dict[str, float] = {}
    for key, native_value in native_values.items():
        independent_value = independent.get(key)
        if key == "reciprocal_overlap_numerator":
            if not isinstance(independent_value, Mapping):
                raise ScienceProtocolError("independent complex overlap numerator is missing")
            expected = complex(_as_finite_float(independent_value.get("real"), "quadrature overlap real"),
                               _as_finite_float(independent_value.get("imag"), "quadrature overlap imag"))
        else:
            expected = complex(_as_finite_float(independent_value, f"quadrature {key}"), 0)
        denom = max(abs(expected), 1e-30)
        relative[key] = abs(native_value - expected) / denom
    failed = {key: value for key, value in relative.items() if value > tolerance}
    if failed:
        raise ScienceProtocolError(f"COMSOL native integrals and independent raw-field quadrature differ: {failed}")
    return {"status": "PASS_NATIVE_VS_INDEPENDENT_QUADRATURE", "tolerance_relative": tolerance,
            "relative_errors": relative, "unit": POWER_UNIT_2D,
            "independent_provenance": independent.get("evidence_scope")}


def independent_capture_aperture_flux(output_raw: Mapping[str, Any]) -> dict[str, Any]:
    """Integrate signed native E/H flux over the frozen core-only x=8 aperture."""
    if output_raw.get("plane") != "output_x8" or output_raw.get("sample_count") != REFINED_SAMPLE_COUNT:
        raise ScienceProtocolError("capture quadrature requires the 641-point native x=8 output field export")
    y, native_nx, declared_sign = _plane_coordinates(output_raw, expected_x_m=8e-6)
    if native_nx * declared_sign != 1:
        raise ScienceProtocolError("capture aperture normal does not follow the registered physical +x convention")
    selected = [index for index, coordinate in enumerate(y)
                if CAPTURE_Y_MIN_M - 1e-12 <= coordinate <= CAPTURE_Y_MAX_M + 1e-12]
    if len(selected) < 3 or len(selected) % 2 != 1:
        raise ScienceProtocolError("registered receiver-core capture aperture has no odd Simpson subgrid")
    selected_y = [y[index] for index in selected]
    if (not math.isclose(selected_y[0], CAPTURE_Y_MIN_M, rel_tol=0, abs_tol=1e-12)
            or not math.isclose(selected_y[-1], CAPTURE_Y_MAX_M, rel_tol=0, abs_tol=1e-12)):
        raise ScienceProtocolError("native 641-point grid does not exactly cover the frozen core capture bounds")
    fields = _raw_expression_map(output_raw)
    required = ("ewfd.Ey", "ewfd.Ez", "ewfd.Hy", "ewfd.Hz")
    if any(name not in fields for name in required):
        raise ScienceProtocolError("capture quadrature is missing native signal E/H components")
    electric = {axis: f"ewfd.E{axis}" for axis in "xyz"}
    magnetic = {axis: f"ewfd.H{axis}" for axis in "xyz"}
    flux_x = _poynting_x(fields, electric, magnetic)
    values = [0.5 * (flux_x[index] * native_nx * declared_sign).real for index in selected]
    result = _simpson_uniform_any(values, selected_y, label="capture aperture Poynting flux").real
    return {
        "status": "SOFTWARE_RECOMPUTED_NATIVE_CAPTURE_FLUX",
        "evidence_scope": "independent Simpson quadrature of full-plane native E/H samples restricted to registered aperture coordinates",
        "aperture_id": CAPTURE_APERTURE_ID,
        "plane_id": "output_x8",
        "selection_tag": "selCoreCaptureX8",
        "y_bounds_m": [CAPTURE_Y_MIN_M, CAPTURE_Y_MAX_M],
        "sample_count": len(selected),
        "full_plane_sample_count": output_raw["sample_count"],
        "native_normal_x": native_nx,
        "normal_sign": declared_sign,
        "value": result,
        "unit": POWER_UNIT_2D,
    }


def compare_native_capture_flux(native: Mapping[str, Any], independent: Mapping[str, Any],
                                independent_integrals: Mapping[str, Any], *,
                                tolerance: float = INTEGRAL_RELATIVE_TOLERANCE) -> dict[str, Any]:
    if not isinstance(native, Mapping) or not isinstance(independent, Mapping) or not isinstance(independent_integrals, Mapping):
        raise ScienceProtocolError("native capture comparison inputs must be mappings")
    if native.get("status") != "SUCCEEDED" or native.get("result_status") != "COMPUTED_NATIVE_INTEGRALS":
        raise ScienceProtocolError("result.mode_overlap did not return a clean native integration result")
    capture = native.get("eta_capture") if isinstance(native, Mapping) else None
    if not isinstance(capture, Mapping) or capture.get("status") != "COMPUTED_NATIVE_APERTURE_FLUX":
        raise ScienceProtocolError("native result did not compute a separately registered capture aperture")
    if independent.get("status") != "SOFTWARE_RECOMPUTED_NATIVE_CAPTURE_FLUX":
        raise ScienceProtocolError("independent capture-aperture quadrature provenance is missing")
    if (independent_integrals.get("status") != "SOFTWARE_RECOMPUTED_FROM_NATIVE_RAW_FIELDS"
            or independent_integrals.get("unit") != POWER_UNIT_2D
            or independent_integrals.get("sample_count") != REFINED_SAMPLE_COUNT):
        raise ScienceProtocolError("independent incident-power receipt is not the registered 641-point native-field recomputation")
    region, numerator = capture.get("region"), capture.get("numerator")
    if not isinstance(region, Mapping) or not isinstance(numerator, Mapping):
        raise ScienceProtocolError("native capture result omitted region or signed-flux numerator")
    selection = region.get("selection")
    if not isinstance(selection, Mapping):
        raise ScienceProtocolError("native capture result omitted selection readback")
    if (region.get("aperture_id") != independent.get("aperture_id")
            or region.get("plane_id") != independent.get("plane_id")
            or selection.get("selection_tag") != independent.get("selection_tag")
            or region.get("normal_sign") != independent.get("normal_sign")):
        raise ScienceProtocolError("native capture region identity differs from the independent quadrature aperture")
    denominator = capture.get("denominator")
    integrals = native.get("integrals")
    incident_integral = integrals.get("incident_reference_power") if isinstance(integrals, Mapping) else None
    identity = native.get("identity")
    incident_identity = identity.get("incident_reference") if isinstance(identity, Mapping) else None
    if (not isinstance(denominator, Mapping) or not isinstance(incident_integral, Mapping)
            or denominator.get("reference_id") != incident_integral.get("reference_id")
            or not isinstance(incident_identity, Mapping)
            or denominator.get("input_plane_id") != incident_identity.get("input_plane_id")):
        raise ScienceProtocolError("native capture denominator is not bound to the independently integrated incident reference")
    if denominator.get("unit") != POWER_UNIT_2D or numerator.get("unit") != independent.get("unit"):
        raise ScienceProtocolError("native and independent capture flux units differ")
    value = _as_finite_float(numerator.get("real"), "native capture flux real")
    imaginary = _as_finite_float(numerator.get("imag"), "native capture flux imaginary")
    if abs(imaginary) > 64 * math.ulp(max(abs(value), 1e-12)):
        raise ScienceProtocolError("native time-average capture flux has a nonzero imaginary residual")
    expected = _as_finite_float(independent.get("value"), "independent capture flux")
    relative_error = abs(value - expected) / max(abs(value), abs(expected), 1e-30)
    if not math.isfinite(relative_error) or relative_error > tolerance:
        raise ScienceProtocolError(f"native capture flux and independent raw-field quadrature differ by {relative_error!r}")

    # The ratio is acceptance data too: identity links alone cannot establish
    # correct normalization. Rebuild eta from the separately recomputed input
    # power, then verify both native denominator copies and the reported ratio.
    expected_incident = _as_finite_float(
        independent_integrals.get("incident_reference_power"),
        "independently recomputed incident reference power")
    floor_record = native.get("normalization_power_floor")
    floor = (_as_finite_float(floor_record.get("value"), "native normalization power floor")
             if isinstance(floor_record, Mapping) else 0.0)
    if not math.isfinite(expected_incident) or expected_incident <= floor:
        raise ScienceProtocolError("independently recomputed incident reference power is nonpositive or below the native floor")
    denominator_value = _as_finite_float(denominator.get("value"), "native capture denominator value")
    incident_integral_value = _as_finite_float(incident_integral.get("real"), "native incident integral real")
    incident_integral_imag = _as_finite_float(incident_integral.get("imag", 0), "native incident integral imaginary")
    if incident_integral.get("unit") != POWER_UNIT_2D:
        raise ScienceProtocolError("native incident integral unit is not W/m")
    denominator_error = abs(denominator_value - expected_incident) / max(abs(expected_incident), 1e-30)
    incident_integral_error = abs(incident_integral_value - expected_incident) / max(abs(expected_incident), 1e-30)
    native_denominator_copy_error = abs(denominator_value - incident_integral_value) / max(abs(incident_integral_value), 1e-30)
    if (max(denominator_error, incident_integral_error, native_denominator_copy_error) > tolerance
            or abs(incident_integral_imag) > 64 * math.ulp(max(abs(incident_integral_value), floor))):
        raise ScienceProtocolError("native capture denominator value differs from independently recomputed incident power")

    capture_integral = integrals.get("capture_aperture_signal_flux") if isinstance(integrals, Mapping) else None
    if not isinstance(capture_integral, Mapping) or capture_integral.get("unit") != POWER_UNIT_2D:
        raise ScienceProtocolError("native capture integral record is missing or not in W/m")
    capture_integral_real = _as_finite_float(capture_integral.get("real"), "native capture integral real")
    capture_integral_imag = _as_finite_float(capture_integral.get("imag", 0), "native capture integral imaginary")
    duplicate_numerator_error = abs(capture_integral_real - value) / max(abs(value), abs(capture_integral_real), 1e-30)
    if (duplicate_numerator_error > tolerance
            or abs(capture_integral_imag - imaginary) > 64 * math.ulp(max(abs(imaginary), 1e-30))):
        raise ScienceProtocolError("native capture ratio numerator differs from its native integral record")

    expected_eta = expected / expected_incident
    native_eta = _as_finite_float(capture.get("value"), "native eta_capture")
    eta_error = abs(native_eta - expected_eta) / max(abs(native_eta), abs(expected_eta), 1e-30)
    native_ratio_error = abs(native_eta - value / denominator_value) / max(
        abs(native_eta), abs(value / denominator_value), 1e-30)
    if not math.isfinite(eta_error) or not math.isfinite(native_ratio_error) or max(eta_error, native_ratio_error) > tolerance:
        raise ScienceProtocolError("native eta_capture differs from independently recomputed signed aperture flux / incident power")
    return {"status": "PASS_NATIVE_CAPTURE_QUADRATURE_MATCH", "native_value": value,
            "independent_value": expected, "unit": numerator["unit"],
            "relative_error": relative_error, "tolerance": tolerance,
            "sample_count": independent.get("sample_count"),
            "native_eta_capture": native_eta, "independent_eta_capture": expected_eta,
            "eta_relative_error": eta_error,
            "native_denominator_value": denominator_value,
            "independent_incident_reference_power": expected_incident,
            "denominator_relative_error": denominator_error,
            "native_incident_integral_relative_error": incident_integral_error,
            "native_ratio_internal_relative_error": native_ratio_error}


def compare_quadrature_refinement(coarse: Mapping[str, Any], fine: Mapping[str, Any], *,
                                  tolerance: float = INTEGRAL_RELATIVE_TOLERANCE) -> dict[str, Any]:
    """Require registered 321/641 Simpson estimates to converge independently."""
    if coarse.get("status") != "SOFTWARE_RECOMPUTED_FROM_NATIVE_RAW_FIELDS" or coarse.get("sample_count") != SAMPLE_COUNT:
        raise ScienceProtocolError("coarse quadrature receipt is not the registered 321-point recomputation")
    if fine.get("status") != "SOFTWARE_RECOMPUTED_FROM_NATIVE_RAW_FIELDS" or fine.get("sample_count") != REFINED_SAMPLE_COUNT:
        raise ScienceProtocolError("fine quadrature receipt is not the registered 641-point recomputation")
    errors: dict[str, float] = {}
    for key in ("signal_power", "reference_mode_power", "incident_reference_power"):
        left = complex(_as_finite_float(coarse.get(key), f"coarse {key}"), 0)
        right = complex(_as_finite_float(fine.get(key), f"fine {key}"), 0)
        errors[key] = abs(right - left) / max(abs(right), 1e-30)
    left_row, right_row = coarse.get("reciprocal_overlap_numerator"), fine.get("reciprocal_overlap_numerator")
    if not isinstance(left_row, Mapping) or not isinstance(right_row, Mapping):
        raise ScienceProtocolError("quadrature refinement lacks complex reciprocal-overlap numerators")
    left = complex(_as_finite_float(left_row.get("real"), "coarse overlap real"),
                   _as_finite_float(left_row.get("imag"), "coarse overlap imag"))
    right = complex(_as_finite_float(right_row.get("real"), "fine overlap real"),
                    _as_finite_float(right_row.get("imag"), "fine overlap imag"))
    errors["reciprocal_overlap_numerator"] = abs(right - left) / max(abs(right), 1e-30)
    if any(error > tolerance for error in errors.values()):
        raise ScienceProtocolError(f"321-to-641 quadrature refinement did not meet the frozen tolerance: {errors}")
    return {"status": "PASS_321_TO_641_QUADRATURE_REFINEMENT", "tolerance_relative": tolerance,
            "relative_errors": errors, "coarse_points": SAMPLE_COUNT,
            "fine_points": REFINED_SAMPLE_COUNT}


def software_global_phase_control(output_raw: Mapping[str, Any],
                                  input_raw: Mapping[str, Any], *,
                                  phase_factor: complex = 1j) -> dict[str, Any]:
    """Test a known signal-only phase rotation on exported native fields.

    This is explicitly a software recomputation control. It does not replace
    changing the COMSOL input Port phase and rerunning the native study.
    """
    if not isinstance(phase_factor, complex) or abs(abs(phase_factor) - 1.0) > 1e-12:
        raise ScienceProtocolError("software global-phase control requires a unit-magnitude complex factor")
    base = independent_integrals(output_raw, input_raw)
    fields = _raw_expression_map(output_raw)
    signal_names = {f"ewfd.E{axis}" for axis in "xyz"} | {f"ewfd.H{axis}" for axis in "xyz"}
    if not signal_names.issubset(fields):
        raise ScienceProtocolError("software phase control is missing one or more signal E/H components")
    rotated = dict(output_raw)
    rotated_rows: list[dict[str, Any]] = []
    for row in output_raw.get("expressions", []):
        name = row["expression"]
        copied = dict(row)
        if name in signal_names:
            values = fields[name]
            changed = [phase_factor * value for value in values]
            copied["real"] = [value.real for value in changed]
            copied["imag"] = [value.imag for value in changed]
        else:
            copied["real"] = list(row["real"])
            copied["imag"] = list(row["imag"])
        rotated_rows.append(copied)
    rotated["expressions"] = rotated_rows
    changed = independent_integrals(rotated, input_raw)
    base_cross = complex(base["reciprocal_overlap_numerator"]["real"],
                         base["reciprocal_overlap_numerator"]["imag"])
    changed_cross = complex(changed["reciprocal_overlap_numerator"]["real"],
                            changed["reciprocal_overlap_numerator"]["imag"])
    if abs(base_cross) <= 1e-30:
        raise ScienceProtocolError("software phase control has no identifiable baseline cross integral")
    if any(float(base[key]) <= 0 or float(changed[key]) <= 0
           for key in ("signal_power", "reference_mode_power", "incident_reference_power")):
        raise ScienceProtocolError("software phase control requires positive physical forward powers")
    cross_ratio = changed_cross / base_cross
    if abs(cross_ratio - phase_factor) > PHASE_INTEGRAL_RELATIVE_TOLERANCE:
        raise ScienceProtocolError("software signal-only phase did not rotate the complex overlap numerator")
    invariant_errors: dict[str, float] = {}
    for key in ("signal_power", "reference_mode_power", "incident_reference_power"):
        error = abs(float(changed[key]) - float(base[key])) / max(abs(float(base[key])), 1e-30)
        invariant_errors[key] = error
    base_mode = float(base["reference_mode_power"])
    changed_mode = float(changed["reference_mode_power"])
    base_overlap_metric = abs(base_cross) / math.sqrt(float(base["signal_power"]) * base_mode)
    changed_overlap_metric = abs(changed_cross) / math.sqrt(float(changed["signal_power"]) * changed_mode)
    invariant_errors["normalized_cross_magnitude"] = abs(changed_overlap_metric - base_overlap_metric) / max(
        abs(base_overlap_metric), 1e-30)
    if any(error > PHASE_INTEGRAL_RELATIVE_TOLERANCE for error in invariant_errors.values()):
        raise ScienceProtocolError(f"software phase control changed a power/overlap magnitude: {invariant_errors}")
    return {
        "status": "PASS_SOFTWARE_SIGNAL_GLOBAL_PHASE_CONTROL",
        "scope": "software-only recomputation of native field exports; not a COMSOL phase solve",
        "phase_factor": {"real": phase_factor.real, "imag": phase_factor.imag},
        "observed_cross_integral_ratio": {"real": cross_ratio.real, "imag": cross_ratio.imag},
        "invariant_relative_errors": invariant_errors,
        "native_input_phase_solve_still_required": True,
    }


def verify_native_phase_pair(case0: Mapping[str, Any], case90: Mapping[str, Any], *,
                             expected_factor: complex = EXPECTED_FIELD_PHASE_FACTOR) -> dict[str, Any]:
    """Require input phase change to rotate signal while output reference stays fixed."""
    for label, case in (("phase0", case0), ("phase90", case90)):
        if case.get("phase_value") not in {"0[deg]", "90[deg]"}:
            raise ScienceProtocolError(f"{label} lacks the exact frozen native Port phase setting")
        if case.get("output_port_phase") != "0[deg]":
            raise ScienceProtocolError("output reference-mode Port phase changed during the native phase control")
        if case.get("native_overlap", {}).get("status") != "SUCCEEDED":
            raise ScienceProtocolError(f"{label} lacks a clean native result.mode_overlap result")
    if case0.get("phase_value") != "0[deg]" or case90.get("phase_value") != "90[deg]":
        raise ScienceProtocolError("native phase cases are reversed or duplicated")
    raw0 = case0.get("output_fields")
    raw90 = case90.get("output_fields")
    if not isinstance(raw0, Mapping) or not isinstance(raw90, Mapping):
        raise ScienceProtocolError("native phase pair lacks output raw complex fields")
    if raw0.get("plane") != "output_x8" or raw90.get("plane") != "output_x8":
        raise ScienceProtocolError("native phase pair is not bound to the x=8 um science receiver plane")
    if raw0.get("sample_count") != raw90.get("sample_count"):
        raise ScienceProtocolError("native phase pair uses different quadrature sample counts")
    y0, nx0, sign0 = _plane_coordinates(raw0, expected_x_m=8e-6)
    y90, nx90, sign90 = _plane_coordinates(raw90, expected_x_m=8e-6)
    if (len(y0) != len(y90) or any(not math.isclose(a, b, rel_tol=0, abs_tol=1e-12)
                                    for a, b in zip(y0, y90))
            or nx0 != nx90 or sign0 != sign90):
        raise ScienceProtocolError("native phase pair output fields are not bound to the same coordinates and oriented plane")
    values0, values90 = _raw_expression_map(raw0), _raw_expression_map(raw90)
    signal_names = [f"ewfd.E{axis}" for axis in "xyz"] + [f"ewfd.H{axis}" for axis in "xyz"]
    mode_names = [f"ewfd.Emode{axis}_2" for axis in "xyz"] + [f"ewfd.Hmode{axis}_2" for axis in "xyz"]
    ratios: list[complex] = []
    mode_relative_errors: list[float] = []
    for name in signal_names:
        if name not in values0 or name not in values90:
            raise ScienceProtocolError(f"phase control lacks signal field {name}")
        for old, new in zip(values0[name], values90[name]):
            if abs(old) <= 1e-30 and abs(new) <= 1e-30:
                continue
            if abs(old) <= 1e-30:
                raise ScienceProtocolError(f"{name} became nonzero from a zero phase-0 field")
            ratio = new / old
            if abs(old) > 1e-12 * max(abs(v) for v in values0[name]):
                ratios.append(ratio)
    if len(ratios) < 10:
        raise ScienceProtocolError("native phase pair has too few nonzero field samples for an identifiable phase test")
    mean_ratio = sum(ratios, 0j) / len(ratios)
    field_scatter = max(abs(value - expected_factor) for value in ratios)
    if abs(mean_ratio - expected_factor) > PHASE_FIELD_RELATIVE_TOLERANCE or field_scatter > 5 * PHASE_FIELD_RELATIVE_TOLERANCE:
        raise ScienceProtocolError("native signal field did not rotate by the frozen input phase factor relative to the fixed output mode")
    for name in mode_names:
        if name not in values0 or name not in values90:
            raise ScienceProtocolError(f"phase control lacks output reference mode field {name}")
        for old, new in zip(values0[name], values90[name]):
            if abs(old) > 1e-30 or abs(new) > 1e-30:
                mode_relative_errors.append(abs(new - old) / max(abs(old), 1e-30))
    if not mode_relative_errors or max(mode_relative_errors) > PHASE_FIELD_RELATIVE_TOLERANCE:
        raise ScienceProtocolError("output reference mode did not remain fixed during the native input phase control")
    native0 = case0["native_overlap"]
    native90 = case90["native_overlap"]
    a0, a90 = native0.get("overlap_amplitude"), native90.get("overlap_amplitude")
    if not isinstance(a0, Mapping) or not isinstance(a90, Mapping):
        raise ScienceProtocolError("native overlap amplitude readback is missing from one phase case")
    z0 = complex(_as_finite_float(a0.get("real"), "phase0 overlap real"),
                 _as_finite_float(a0.get("imag"), "phase0 overlap imag"))
    z90 = complex(_as_finite_float(a90.get("real"), "phase90 overlap real"),
                  _as_finite_float(a90.get("imag"), "phase90 overlap imag"))
    if abs(z0) <= 1e-30 or abs(z90 / z0 - expected_factor) > PHASE_INTEGRAL_RELATIVE_TOLERANCE:
        raise ScienceProtocolError("native overlap amplitude did not retain the input-only phase rotation")
    invariant_keys = ("normalized_overlap", "eta_mode")
    invariant_errors: dict[str, float] = {}
    for key in invariant_keys:
        left, right = native0.get(key), native90.get(key)
        leftf, rightf = _as_finite_float(left, f"phase0 {key}"), _as_finite_float(right, f"phase90 {key}")
        invariant_errors[key] = abs(rightf - leftf) / max(abs(leftf), 1e-30)
    if any(error > PHASE_INTEGRAL_RELATIVE_TOLERANCE for error in invariant_errors.values()):
        raise ScienceProtocolError("native phase change altered overlap magnitude or efficiency")
    integrals0 = native0.get("integrals")
    integrals90 = native90.get("integrals")
    if not isinstance(integrals0, Mapping) or not isinstance(integrals90, Mapping):
        raise ScienceProtocolError("native phase pair lacks the solver's real integrated power readbacks")
    for key in ("signal_power", "reference_mode_power", "incident_reference_power"):
        left, right = integrals0.get(key), integrals90.get(key)
        if not isinstance(left, Mapping) or not isinstance(right, Mapping):
            raise ScienceProtocolError(f"native phase pair lacks native {key} readbacks")
        if left.get("unit") != POWER_UNIT_2D or right.get("unit") != POWER_UNIT_2D:
            raise ScienceProtocolError(f"native phase pair {key} unit is not verified as W/m")
        left_value = complex(_as_finite_float(left.get("real"), f"phase0 {key}"),
                             _as_finite_float(left.get("imag", 0), f"phase0 {key}.imag"))
        right_value = complex(_as_finite_float(right.get("real"), f"phase90 {key}"),
                              _as_finite_float(right.get("imag", 0), f"phase90 {key}.imag"))
        error = abs(right_value - left_value) / max(abs(left_value), 1e-30)
        invariant_errors[key] = error
        if error > PHASE_INTEGRAL_RELATIVE_TOLERANCE:
            raise ScienceProtocolError(f"native input phase change altered {key}")
    return {"status": "PASS_NATIVE_INPUT_PHASE_CONTROL", "expected_field_factor": {"real": expected_factor.real, "imag": expected_factor.imag},
            "observed_signal_field_factor": {"real": mean_ratio.real, "imag": mean_ratio.imag},
            "signal_field_ratio_max_scatter": field_scatter,
            "output_mode_max_relative_change": max(mode_relative_errors),
            "native_overlap_amplitude_ratio": {"real": (z90 / z0).real, "imag": (z90 / z0).imag},
            "invariant_relative_errors": invariant_errors,
            "native_phase_fields_are_distinguishable": True}


def run_negative_controls() -> dict[str, Any]:
    """Execute representative refusal controls before any native dispatch."""
    from comsol_mcp._w23_results import validate_definition_shape
    from comsol_mcp._g2_contract import ExecutionContractError

    failures: list[dict[str, Any]] = []
    probes = [
        ("caller_array_provenance", {"field_arrays": [[1.0]], "coordinates": [[0.0]]}, "INVALID_REQUEST"),
        ("nonpositive_power_floor", {"power_floor": {"value": 0, "unit": POWER_UNIT_2D}}, "INVALID_REQUEST"),
    ]
    for label, patch, expected_code in probes:
        base = {
            "schema_version": "1.0.0",
            "phasor_convention": {"time_dependence": "exp(-i omega t)",
                                  "complex_field_representation": "full_physical_complex_phasor_including_reconstructed_envelope_phase"},
            "output_surface": {"plane_id": "x8", "selection": {"component": "comp1", "geometry": "geom1", "tag": "selReceiverX8"}, "normal_sign": 1},
            "signal": {"field_id": "signal", "source": {"dataset_id": "dset1", "solution_id": "sol1", "outer_index": 1, "inner_index": 1},
                       "fields": {"electric": {axis: f"ewfd.E{axis}" for axis in "xyz"}, "magnetic": {axis: f"ewfd.H{axis}" for axis in "xyz"}}},
            "reference_mode": {"mode_id": "output_mode_1", "mode_axis_parameter": "modeIndex",
                               "source": {"dataset_id": "dset1", "solution_id": "sol1", "outer_index": 1, "inner_index": 1},
                               "fields": {"electric": {axis: f"ewfd.Emode{axis}_2" for axis in "xyz"}, "magnetic": {axis: f"ewfd.Hmode{axis}_2" for axis in "xyz"}}},
            "incident_reference": {"reference_id": "input_mode_1", "input_plane_id": "xminus10",
                                   "selection": {"component": "comp1", "geometry": "geom1", "tag": "selInputPort"}, "normal_sign": -1,
                                   "source": {"dataset_id": "dset1", "solution_id": "sol1", "outer_index": 1, "inner_index": 1},
                                   "fields": {"electric": {axis: f"ewfd.Emode{axis}_1" for axis in "xyz"}, "magnetic": {axis: f"ewfd.Hmode{axis}_1" for axis in "xyz"}}},
            "power_floor": {"value": 1e-12, "unit": POWER_UNIT_2D},
        }
        if label == "caller_array_provenance":
            base.update(patch)
        else:
            base["power_floor"] = patch["power_floor"]
        try:
            validate_definition_shape(base)
        except ExecutionContractError as exc:
            failures.append({"control": label, "observed_code": exc.code,
                             "expected_code": expected_code, "passed": exc.code == expected_code})
        else:
            failures.append({"control": label, "observed_code": None,
                             "expected_code": expected_code, "passed": False})

    # The public shape schema supports both W (3-D) and W/m (2-D).  The
    # dimension-bound native receipt comparison must reject W/m geometry
    # evidence relabeled as W; test that route-specific unit check here.
    independent = {
        "status": "SOFTWARE_RECOMPUTED_FROM_NATIVE_RAW_FIELDS",
        "signal_power": 12e-6, "reference_mode_power": 2.5e-6,
        "incident_reference_power": 5e-6,
        "reciprocal_overlap_numerator": {"real": 46e-6, "imag": -20e-6},
    }
    native = {
        "status": "SUCCEEDED", "result_status": "COMPUTED_NATIVE_INTEGRALS",
        "integrals": {
            "signal_power": {"real": 12e-6, "imag": 0, "unit": "W"},
            "reference_mode_power": {"real": 2.5e-6, "imag": 0, "unit": POWER_UNIT_2D},
            "incident_reference_power": {"real": 5e-6, "imag": 0, "unit": POWER_UNIT_2D},
            "reciprocal_overlap_numerator": {"real": 46e-6, "imag": -20e-6, "unit": POWER_UNIT_2D},
        },
    }
    try:
        compare_native_integrals(native, independent)
    except ScienceProtocolError as exc:
        observed_unit_refusal = "unit is not verified as W/m" in str(exc)
        failures.append({"control": "wrong_2d_integral_unit", "observed_code": "UNIT_MISMATCH" if observed_unit_refusal else "OTHER_ERROR",
                         "expected_code": "UNIT_MISMATCH", "passed": observed_unit_refusal})
    else:
        failures.append({"control": "wrong_2d_integral_unit", "observed_code": None,
                         "expected_code": "UNIT_MISMATCH", "passed": False})
    if not all(row["passed"] for row in failures):
        raise ScienceProtocolError(f"pre-native negative controls failed: {failures}")
    return {"status": "PASS_SOFTWARE_NEGATIVE_CONTROLS", "scope": "schema-only; no native dispatch",
            "controls": failures}
