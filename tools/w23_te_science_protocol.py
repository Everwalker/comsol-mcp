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


SAMPLE_COUNT = 321  # Existing full-science coarse grid: 320 even intervals.
REFINED_SAMPLE_COUNT = 641  # Existing full-science fine grid doubles intervals.
SAVED_MPH_SAMPLE_COUNT = 641  # Zero-solve saved-MPH refinement starts from prior fine grid.
SAVED_MPH_REFINED_SAMPLE_COUNT = 1281  # New zero-solve refinement doubles intervals.
SAVED_MPH_QUADRATURE_SAMPLE_COUNTS = (SAVED_MPH_SAMPLE_COUNT, SAVED_MPH_REFINED_SAMPLE_COUNT)
QUADRATURE_SAMPLE_COUNTS = tuple(sorted({SAMPLE_COUNT, REFINED_SAMPLE_COUNT,
                                         *SAVED_MPH_QUADRATURE_SAMPLE_COUNTS}))
INTEGRAL_RELATIVE_TOLERANCE = 1e-3
PHASE_FIELD_RELATIVE_TOLERANCE = 1e-3
PHASE_INTEGRAL_RELATIVE_TOLERANCE = 1e-3
EXPECTED_FIELD_PHASE_FACTOR = 1j
POWER_UNIT_2D = "W/m"
CAPTURE_APERTURE_ID = "receiver_core_aperture"
CAPTURE_Y_MIN_M = -0.5e-6
CAPTURE_Y_MAX_M = 0.5e-6
BMA_LEGACY_REQUESTED_UNITS = ("1/m", "1", "1/m")
BMA_NATIVE_DEFAULT_NEFF_REQUESTED_UNITS = ("1/m", "", "1/m")
NATIVE_DEFAULT_ALLOWED_EXPRESSIONS = frozenset({
    "emw.neff_1", "emw.neff_2", "emw.nx", "emw.ny",
})


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


def _finite_complex_row(real: Any, imag: Any, *, label: str) -> complex:
    return complex(_as_finite_float(real, f"{label}.real"),
                   _as_finite_float(imag, f"{label}.imag"))


def _capture_value(row: Any, *, label: str) -> Any:
    if not isinstance(row, Mapping) or row.get("status") != "CAPTURED" or "return_value" not in row:
        raise ScienceProtocolError(f"{label} is not an exact captured Java getter value")
    return row["return_value"]


def _raw_java_sequence(row: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize the fixture's direct Java getters without inventing axes."""
    tag = row.get("tag")
    direct = row.get("direct_sequence_parameter_axis_observation")
    if not isinstance(tag, str) or not tag or not isinstance(direct, Mapping):
        raise ScienceProtocolError("Java SolverSequence row lacks its exact tag or direct parameter-axis observation")
    get_type = _capture_value(row.get("sequence_type_getter"), label=f"{tag}.getType")
    sequence_type = _capture_value(row.get("sequence_kind_getter"), label=f"{tag}.getSequenceType")
    study = _capture_value(row.get("attached_study_getter"), label=f"{tag}.study")
    attached = _capture_value(row.get("is_attached_getter"), label=f"{tag}.isAttached")
    size = _capture_value(direct.get("get_size"), label=f"{tag}.getSize")
    size_multi = _capture_value(direct.get("get_size_multi"), label=f"{tag}.getSizeMulti")
    names = _capture_value(direct.get("parameter_names"), label=f"{tag}.getPNames")
    if (not isinstance(size, list) or len(size) != 2
            or any(type(item) is not int or item < 0 for item in size)
            or not isinstance(size_multi, list) or len(size_multi) != 2
            or any(type(item) is not int or item < 0 for item in size_multi)
            or not isinstance(names, list) or not names
            or any(not isinstance(name, str) or not name for name in names)):
        raise ScienceProtocolError(f"{tag} direct sequence dimensions/names are not exact typed arrays")

    raw_numbers = direct.get("solution_numbers")
    if not isinstance(raw_numbers, list) or len(raw_numbers) != size[1]:
        raise ScienceProtocolError(f"{tag} direct sequence values do not cover its documented solnum count")
    values: list[dict[str, Any]] = []
    for index, number in enumerate(raw_numbers, start=1):
        if not isinstance(number, Mapping) or number.get("solnum") != index:
            raise ScienceProtocolError(f"{tag} direct sequence solnum order is not the documented one-based order")
        real = _capture_value(number.get("real"), label=f"{tag}.getPVals({index})")
        imag = _capture_value(number.get("imaginary"), label=f"{tag}.getPValsImag({index})")
        units_raw = number.get("parameter_units")
        if (not isinstance(real, list) or not isinstance(imag, list)
                or len(real) != len(names) or len(imag) != len(names)
                or not isinstance(units_raw, list) or len(units_raw) != len(names)):
            raise ScienceProtocolError(f"{tag} direct sequence row {index} has mismatched typed parameter arrays")
        units: list[dict[str, Any]] = []
        for name, unit in zip(names, units_raw):
            if not isinstance(unit, Mapping) or unit.get("name") != name:
                raise ScienceProtocolError(f"{tag} direct unit row does not match parameter {name!r}")
            units.append({"name": name,
                          "status": "OBSERVED" if unit.get("status") == "CAPTURED" else "ERROR",
                          "value": unit.get("value"),
                          "raw_status": unit.get("status"),
                          "error": unit.get("error")})
        values.append({"solnum": index, "real": real, "imaginary": imag,
                       "parameter_units": units})

    info = row.get("solution_info")
    if not isinstance(info, Mapping):
        raise ScienceProtocolError(f"{tag} lacks the exact same-tag SolutionInfo capture")
    metadata_calls = info.get("metadata_calls")
    if not isinstance(metadata_calls, list):
        raise ScienceProtocolError(f"{tag} SolutionInfo getter journal is missing")
    by_method = {call.get("method"): call for call in metadata_calls if isinstance(call, Mapping)}
    for method in ("getLevelNames", "getLevels", "getOuterSolnum"):
        if method not in by_method:
            raise ScienceProtocolError(f"{tag} SolutionInfo.{method} is not in the raw getter journal")
    scope = {
        "getLevelNames": _capture_value(by_method["getLevelNames"], label=f"{tag}.getLevelNames"),
        "getLevels": _capture_value(by_method["getLevels"], label=f"{tag}.getLevels"),
        "getOuterSolnum": _capture_value(by_method["getOuterSolnum"], label=f"{tag}.getOuterSolnum"),
        "getPNamesOuter": (_capture_value(by_method["getPNamesOuter"],
                                            label=f"{tag}.getPNamesOuter")
                            if isinstance(by_method.get("getPNamesOuter"), Mapping)
                            and by_method["getPNamesOuter"].get("status") == "CAPTURED"
                            else None),
        "getPUnitsOuter": (_capture_value(by_method["getPUnitsOuter"],
                                            label=f"{tag}.getPUnitsOuter")
                            if isinstance(by_method.get("getPUnitsOuter"), Mapping)
                            and by_method["getPUnitsOuter"].get("status") == "CAPTURED"
                            else None),
        "read_errors": [dict(call) for call in metadata_calls
                        if isinstance(call, Mapping) and call.get("status") == "ERROR"
                        and call.get("method") in {"getLevelNames", "getLevels", "getOuterSolnum"}],
        "matrix_getter_errors": [dict(call) for call in metadata_calls
                                  if isinstance(call, Mapping) and call.get("status") == "ERROR"
                                  and call.get("method") in {"getPNames", "getPvals", "getPvalsImag", "getUnits"}],
    }
    return {
        "tag": tag,
        "sequence_type": sequence_type,
        "solution_type": get_type,
        "study_tag": study,
        "attached": attached,
        "get_size": list(size),
        "get_size_multi": list(size_multi),
        "pnames": list(names),
        "parameter_values_status": direct.get("status"),
        "parameter_values_by_solnum": values,
        "solution_info_count_scope": scope,
        "solution_info_axis_metadata_complete": False,
        "parameters_complete": False,
        "raw_getter_source": "COMSOL 6.4 direct SolverSequence/ SolutionInfo typed getter receipts",
    }


def normalize_saved_mph_diagnostic_inventory(
    java_readback: Mapping[str, Any],
    production_dataset_rows: Sequence[Mapping[str, Any]],
    solution_index_receipts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build strict resolver inputs from raw fixture and managed API receipts.

    This is a representation-only normalization. It retains the failed
    SolutionInfo matrix calls and never upgrades tuple completeness.
    """
    if not isinstance(java_readback, Mapping):
        raise ScienceProtocolError("raw Java saved-MPH readback must be an object")
    if (java_readback.get("diagnostic_phase") != "saved_solution_diagnostic"
            or java_readback.get("explicit_study_or_solver_run_calls_in_this_phase") != 0
            or java_readback.get("internal_solver_execution_count") != "UNKNOWN_NOT_INFERRED_FROM_READBACK"):
        raise ScienceProtocolError("raw Java readback does not carry the frozen zero-explicit-run diagnostic boundary")
    study_tag = java_readback.get("study_tag")
    configured = java_readback.get("study_steps_in_configured_order")
    sequence_rows = java_readback.get("solver_sequences")
    datasets = java_readback.get("datasets")
    if (not isinstance(study_tag, str) or not study_tag
            or not isinstance(configured, list) or not isinstance(sequence_rows, list)
            or not isinstance(datasets, list) or not isinstance(production_dataset_rows, Sequence)
            or not isinstance(solution_index_receipts, Sequence)):
        raise ScienceProtocolError("raw Java and managed dataset inventories are incomplete")

    study_steps: list[dict[str, Any]] = []
    for row in configured:
        if not isinstance(row, Mapping) or not isinstance(row.get("tag"), str):
            raise ScienceProtocolError("configured study-step capture is malformed")
        type_row = row.get("feature_type_getter")
        feature_type = _capture_value(type_row, label=f"study.feature({row['tag']}).getType")
        properties = row.get("selected_properties")
        if not isinstance(properties, Mapping):
            raise ScienceProtocolError("configured study-step selected property capture is missing")
        normalized = {"tag": row["tag"], "feature_type": feature_type,
                      "configured_order": row.get("configured_order")}
        for name in ("PortName", "modeFreq", "plist"):
            value = properties.get(name)
            if not isinstance(value, Mapping):
                raise ScienceProtocolError(f"study step {row['tag']} lacks {name} property capture")
            if value.get("status") == "CAPTURED":
                normalized[name] = _capture_value(value, label=f"study.feature({row['tag']}).{name}")
            elif value.get("status") == "PROPERTY_ABSENT":
                normalized[name] = None
            else:
                raise ScienceProtocolError(f"study step {row['tag']} {name} property readback failed")
        study_steps.append(normalized)

    normalized_sequences: list[dict[str, Any]] = []
    for raw_seq in sequence_rows:
        if not isinstance(raw_seq, Mapping):
            raise ScienceProtocolError("raw Java SolverSequence list contains a malformed row")
        sequence = _raw_java_sequence(raw_seq)
        solver_tree = raw_seq.get("solver_tree")
        if not isinstance(solver_tree, Mapping) or solver_tree.get("status") != "CAPTURED":
            raise ScienceProtocolError(f"{sequence['tag']} solver-tree capture is incomplete")
        raw_features = solver_tree.get("features")
        if not isinstance(raw_features, list):
            raise ScienceProtocolError(f"{sequence['tag']} solver-tree feature rows are missing")
        features: list[dict[str, Any]] = []
        bindings: list[dict[str, Any]] = []
        for raw_feature in raw_features:
            if not isinstance(raw_feature, Mapping) or type(raw_feature.get("tree_order")) is not int:
                raise ScienceProtocolError(f"{sequence['tag']} solver-tree row lacks its captured order")
            feature = {key: raw_feature.get(key) for key in
                       ("path", "tree_order", "feature_type", "property_names")}
            if raw_feature.get("feature_type") in {"StoreSolution", "CopySolution"}:
                prop = raw_feature.get("property_sol")
                if isinstance(prop, Mapping) and prop.get("status") == "CAPTURED":
                    feature["stored_solution_reference"] = _capture_value(
                        prop, label=f"{sequence['tag']} {raw_feature.get('path')}.sol")
                    feature["stored_solution_reference_getter"] = "StoreSolution.getString('sol')"
                else:
                    feature["stored_solution_reference"] = None
                    feature["stored_solution_reference_getter"] = "StoreSolution.getString('sol')"
                    feature["stored_solution_reference_read_error"] = (
                        dict(prop) if isinstance(prop, Mapping) else {"status": "MISSING"})
            if raw_feature.get("feature_type") == "StudyStep":
                study_prop = raw_feature.get("property_study")
                step_prop = raw_feature.get("property_studystep")
                study_value = (_capture_value(study_prop, label=f"{sequence['tag']} StudyStep.study")
                               if isinstance(study_prop, Mapping) and study_prop.get("status") == "CAPTURED"
                               else None)
                step_value = (_capture_value(step_prop, label=f"{sequence['tag']} StudyStep.studystep")
                              if isinstance(step_prop, Mapping) and step_prop.get("status") == "CAPTURED"
                              else None)
                bindings.append({"study": study_value, "studystep": step_value,
                                 "solver_sequence": sequence["tag"],
                                 "tree_order": raw_feature["tree_order"],
                                 "path": raw_feature.get("path")})
            features.append(feature)
        sequence["solver_tree_features"] = features
        sequence["study_step_bindings_in_solver_tree_order"] = bindings
        normalized_sequences.append(sequence)

    raw_java_dataset_by_tag: dict[str, Mapping[str, Any]] = {}
    for row in datasets:
        if not isinstance(row, Mapping) or not isinstance(row.get("tag"), str):
            raise ScienceProtocolError("raw Java dataset capture contains a malformed row")
        if row["tag"] in raw_java_dataset_by_tag:
            raise ScienceProtocolError("raw Java dataset capture contains duplicate tags")
        raw_java_dataset_by_tag[row["tag"]] = row

    production_by_tag: dict[str, Mapping[str, Any]] = {}
    for row in production_dataset_rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("tag"), str):
            raise ScienceProtocolError("production dataset.list contains a malformed row")
        if row["tag"] in production_by_tag:
            raise ScienceProtocolError("production dataset.list contains duplicate tags")
        production_by_tag[row["tag"]] = row
    if set(raw_java_dataset_by_tag) != set(production_by_tag):
        raise ScienceProtocolError("direct Java and managed dataset tag sets differ")
    for tag, java_dataset in raw_java_dataset_by_tag.items():
        prop = java_dataset.get("selected_properties", {}).get("solution")
        production_solution = production_by_tag[tag].get("solution")
        if (not isinstance(prop, Mapping) or prop.get("status") != "CAPTURED"
                or _capture_value(prop, label=f"dataset({tag}).solution") != production_solution):
            raise ScienceProtocolError(f"direct Java and managed dataset solution properties differ for {tag}")

    return {
        "study_inventory": {"study_tag": study_tag,
                            "study_steps_in_configured_order": study_steps,
                            "solver_sequences_for_study": normalized_sequences,
                            "study_solver_sequence_queries": java_readback.get("study_solver_sequence_queries")},
        "dataset_inventory": {"datasets": list(production_dataset_rows),
                              "solution_index_receipts": list(solution_index_receipts)},
        "raw_java_sequences": normalized_sequences,
        "raw_java_datasets": raw_java_dataset_by_tag,
        "study_run_dispatches": 0,
        "solver_run_dispatches": 0,
        "internal_solver_execution_count": "UNKNOWN_NOT_INFERRED_FROM_READBACK",
        "parameters_complete": False,
        "solution_info_axis_metadata_complete": False,
    }


def reconcile_stored_sequence_parameter_observation(
    java_sequence: Mapping[str, Any], worker_observation: Mapping[str, Any],
) -> dict[str, Any]:
    """Reconcile raw Java and production Worker reads of the same solver tag.

    This intentionally leaves SolutionInfo tuple mapping and completeness
    untouched. It only proves both APIs returned the same sequence-level
    complex values, units, and declared sequence identity.
    """
    if not isinstance(java_sequence, Mapping) or not isinstance(worker_observation, Mapping):
        raise ScienceProtocolError("stored solver sequence observations must be objects")
    if java_sequence.get("parameter_values_status") != "RAW_TYPED_COMPLEX_VALUES_CAPTURED_NOT_INTERPRETED":
        raise ScienceProtocolError("direct Java solver sequence values are not fully captured")
    if worker_observation.get("status") != "OBSERVED_TYPED_COMPLEX_PARAMETER_VALUES":
        raise ScienceProtocolError("production Worker solver sequence values or units are incomplete")
    identity_fields = (
        ("sequence_type", "sequence_type"),
        ("solution_type", "solution_type"),
        ("study_tag", "study"),
        ("attached", "attached"),
        ("get_size", "get_size"),
        ("get_size_multi", "get_size_multi"),
        ("pnames", "pnames"),
    )
    mismatches = {
        java_key: {"java": java_sequence.get(java_key), "worker": worker_observation.get(worker_key)}
        for java_key, worker_key in identity_fields
        if java_sequence.get(java_key) != worker_observation.get(worker_key)
    }
    java_tag = java_sequence.get("tag")
    worker_tag = worker_observation.get("solution_tag")
    if not isinstance(java_tag, str) or java_tag != worker_tag:
        mismatches["solution_tag"] = {"java": java_tag, "worker": worker_tag}
    if mismatches:
        raise ScienceProtocolError(f"Java and Worker sequence identity differs: {mismatches}")

    names = java_sequence.get("pnames")
    size = java_sequence.get("get_size")
    java_values = java_sequence.get("parameter_values_by_solnum")
    worker_values = worker_observation.get("solution_values")
    if (not isinstance(names, list) or not names
            or not isinstance(size, list) or len(size) != 2
            or type(size[1]) is not int or size[1] < 1
            or not isinstance(java_values, list) or not isinstance(worker_values, list)
            or len(java_values) != size[1] or len(worker_values) != size[1]):
        raise ScienceProtocolError("stored solver sequence size/name/value rows do not reconcile")

    java_units_by_name: dict[str, str] = {}
    for row in java_values:
        if not isinstance(row, Mapping):
            raise ScienceProtocolError("Java sequence parameter row is malformed")
        units = row.get("parameter_units")
        if not isinstance(units, list) or len(units) != len(names):
            raise ScienceProtocolError("Java sequence unit readback does not match the parameter-name axis")
        for name, unit_row in zip(names, units):
            if (not isinstance(unit_row, Mapping) or unit_row.get("name") != name
                    or unit_row.get("status") != "OBSERVED"
                    or not isinstance(unit_row.get("value"), str) or not unit_row["value"].strip()):
                raise ScienceProtocolError(f"Java sequence unit readback is unavailable for {name!r}")
            unit = str(unit_row["value"])
            if name in java_units_by_name and java_units_by_name[name] != unit:
                raise ScienceProtocolError(f"Java sequence unit changed between solution numbers for {name!r}")
            java_units_by_name[name] = unit

    worker_units = worker_observation.get("unit_observations")
    if not isinstance(worker_units, list) or len(worker_units) != len(names):
        raise ScienceProtocolError("Worker sequence unit readback does not match the parameter-name axis")
    worker_units_by_name: dict[str, str] = {}
    for name, row in zip(names, worker_units):
        if (not isinstance(row, Mapping) or row.get("name") != name
                or not isinstance(row.get("value"), str) or not row["value"].strip()):
            raise ScienceProtocolError(f"Worker sequence unit readback is unavailable for {name!r}")
        worker_units_by_name[name] = str(row["value"])
    if java_units_by_name != worker_units_by_name:
        raise ScienceProtocolError("Java and Worker sequence units differ")

    comparisons = []
    for expected_solnum, (java_row, worker_row) in enumerate(zip(java_values, worker_values), start=1):
        if (java_row.get("solnum") != expected_solnum
                or worker_row.get("solnum") != expected_solnum):
            raise ScienceProtocolError("stored sequence solution numbers are not the exact one-based sequence")
        java_real, java_imag = java_row.get("real"), java_row.get("imaginary")
        worker_real_getter, worker_imag_getter = worker_row.get("real_getter"), worker_row.get("imag_getter")
        worker_real = worker_real_getter.get("raw") if isinstance(worker_real_getter, Mapping) else None
        worker_imag = worker_imag_getter.get("raw") if isinstance(worker_imag_getter, Mapping) else None
        if (not isinstance(java_real, list) or not isinstance(java_imag, list)
                or not isinstance(worker_real, list) or not isinstance(worker_imag, list)
                or len(java_real) != len(names) or len(java_imag) != len(names)
                or len(worker_real) != len(names) or len(worker_imag) != len(names)):
            raise ScienceProtocolError(f"stored sequence value shape is invalid at solnum {expected_solnum}")
        per_parameter = []
        for index, name in enumerate(names):
            java_value = _finite_complex_row(java_real[index], java_imag[index], label=f"Java {name}")
            worker_value = _finite_complex_row(worker_real[index], worker_imag[index], label=f"Worker {name}")
            if java_value != worker_value:
                raise ScienceProtocolError(f"Java and Worker raw values differ for {name!r} at solnum {expected_solnum}")
            per_parameter.append({"name": name, "real": java_value.real, "imag": java_value.imag,
                                 "unit": java_units_by_name[name]})
        comparisons.append({"solnum": expected_solnum, "parameters": per_parameter,
                            "raw_values_match_exactly": True})
    return {
        "status": "PASS_JAVA_WORKER_RAW_SEQUENCE_READBACK_MATCH",
        "scope": "raw solver-sequence identity/complex values/units only; SolutionInfo tuple mapping remains unchanged",
        "solution_tag": java_tag,
        "sequence_type": java_sequence.get("sequence_type"),
        "solution_type": java_sequence.get("solution_type"),
        "attached": java_sequence.get("attached"),
        "study_tag": java_sequence.get("study_tag"),
        "get_size": list(size), "pnames": list(names),
        "get_size_multi": list(java_sequence.get("get_size_multi") or []),
        "parameters_complete": False,
        "solution_info_axis_metadata_complete": False,
        "values": comparisons,
    }


def resolve_bma_stored_dataset_bindings(
    study_inventory: Mapping[str, Any], dataset_inventory: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve each BMA StudyStep through its adjacent StoreSolution to a dataset.

    The association follows the captured COMSOL solver-tree order and exact
    ``StoreSolution.sol`` readback. It does not infer roles from sol/dset tag
    ordering, numeric eigenvalues, or SolutionInfo tuple indexes.
    """
    if not isinstance(study_inventory, Mapping) or not isinstance(dataset_inventory, Mapping):
        raise ScienceProtocolError("study and dataset inventories must be objects")
    study_tag = study_inventory.get("study_tag")
    study_steps = study_inventory.get("study_steps_in_configured_order")
    sequences = study_inventory.get("solver_sequences_for_study")
    index_receipts = dataset_inventory.get("solution_index_receipts")
    if (not isinstance(study_tag, str) or not study_tag
            or not isinstance(study_steps, list) or not isinstance(sequences, list)
            or not isinstance(index_receipts, list)):
        raise ScienceProtocolError("study or dataset inventory lacks the exact W23 readback collections")

    step_by_tag: dict[str, Mapping[str, Any]] = {}
    for row in study_steps:
        if not isinstance(row, Mapping) or not isinstance(row.get("tag"), str):
            raise ScienceProtocolError("configured study-step inventory contains a malformed row")
        if row["tag"] in step_by_tag:
            raise ScienceProtocolError("configured study-step inventory contains a duplicate tag")
        step_by_tag[row["tag"]] = row
    bma_steps = [row for row in study_steps if row.get("feature_type") == "BoundaryModeAnalysis"]
    if len(bma_steps) != 2:
        raise ScienceProtocolError("the saved W23 artifact must expose exactly two BoundaryModeAnalysis steps")
    if any(not isinstance(row.get("PortName"), str) or not row["PortName"].strip()
           or not isinstance(row.get("modeFreq"), str) or not row["modeFreq"].strip()
           for row in bma_steps):
        raise ScienceProtocolError("each BMA step must preserve its actual PortName and modeFreq properties")
    bma_by_tag = {row["tag"]: row for row in bma_steps}
    if (set(bma_by_tag) != {"bmaInput", "bmaOutput"}
            or {str(row["PortName"]) for row in bma_steps} != {"1", "2"}
            or any(row.get("modeFreq") != "f0" for row in bma_steps)):
        raise ScienceProtocolError("saved W23 BMA steps do not retain exact bmaInput/bmaOutput PortName 1/2 and modeFreq=f0")
    frequency_steps = [row for row in study_steps if row.get("tag") == "freq"]
    if (len(frequency_steps) != 1 or frequency_steps[0].get("feature_type") != "Frequency"
            or frequency_steps[0].get("plist") != "f0"):
        raise ScienceProtocolError("saved W23 study does not retain its exact Frequency step with plist=f0")

    attached_sequences = [row for row in sequences if isinstance(row, Mapping)
                          and row.get("sequence_type") == "SolverSequence"
                          and row.get("study_tag") == study_tag and row.get("attached") is True]
    if len(attached_sequences) != 1:
        raise ScienceProtocolError("study must have exactly one attached SolverSequence with a matching study tag")
    main_sequence = attached_sequences[0]
    sequence_tag = main_sequence.get("tag")
    tree = main_sequence.get("solver_tree_features")
    step_bindings = main_sequence.get("study_step_bindings_in_solver_tree_order")
    if (not isinstance(sequence_tag, str) or not isinstance(tree, list)
            or not isinstance(step_bindings, list)):
        raise ScienceProtocolError("attached SolverSequence lacks ordered solver-tree bindings")
    tree_by_order: dict[int, Mapping[str, Any]] = {}
    for row in tree:
        if not isinstance(row, Mapping) or type(row.get("tree_order")) is not int:
            raise ScienceProtocolError("solver-tree row lacks an exact integer tree_order")
        if row["tree_order"] in tree_by_order:
            raise ScienceProtocolError("solver-tree contains duplicate tree_order values")
        tree_by_order[row["tree_order"]] = row
    if sorted(tree_by_order) != list(range(len(tree_by_order))):
        raise ScienceProtocolError("solver-tree order is not a contiguous captured traversal")
    bindings_by_step: dict[str, Mapping[str, Any]] = {}
    for binding in step_bindings:
        if not isinstance(binding, Mapping) or binding.get("solver_sequence") != sequence_tag:
            raise ScienceProtocolError("StudyStep binding is not attributed to the actual SolverSequence")
        step_name = binding.get("studystep")
        if not isinstance(step_name, str) or step_name in bindings_by_step:
            raise ScienceProtocolError("StudyStep binding has a missing or duplicate study step identity")
        if binding.get("study") != study_tag or type(binding.get("tree_order")) is not int:
            raise ScienceProtocolError("StudyStep binding does not prove its study and ordered tree position")
        tree_row = tree_by_order.get(binding["tree_order"])
        if (not isinstance(tree_row, Mapping) or tree_row.get("feature_type") != "StudyStep"
                or tree_row.get("path") != binding.get("path")):
            raise ScienceProtocolError("StudyStep binding does not match the exact solver-tree feature row")
        bindings_by_step[step_name] = binding
    ordered_bindings = sorted(bindings_by_step.values(), key=lambda row: row["tree_order"])

    dataset_matches: dict[str, list[Mapping[str, Any]]] = {}
    for receipt in index_receipts:
        if not isinstance(receipt, Mapping):
            raise ScienceProtocolError("dataset solution-index receipt is malformed")
        dataset = receipt.get("dataset")
        indices = receipt.get("solution_indices")
        if (not isinstance(dataset, Mapping) or dataset.get("type_id", "").lower() != "solution"
                or not isinstance(indices, Mapping)):
            continue
        solution = indices.get("solution")
        tag = dataset.get("tag")
        if isinstance(solution, str) and isinstance(tag, str):
            dataset_matches.setdefault(solution, []).append(receipt)

    resolved: list[dict[str, Any]] = []
    for bma in bma_steps:
        step_tag = bma["tag"]
        binding = bindings_by_step.get(step_tag)
        if binding is None:
            raise ScienceProtocolError(f"BMA step {step_tag!r} has no exact StudyStep solver-tree binding")
        current_order = binding["tree_order"]
        next_orders = [row["tree_order"] for row in ordered_bindings
                       if row["tree_order"] > current_order]
        next_order = min(next_orders) if next_orders else len(tree_by_order)
        stores = [row for order, row in sorted(tree_by_order.items())
                  if current_order < order < next_order and row.get("feature_type") == "StoreSolution"]
        if len(stores) != 1:
            raise ScienceProtocolError(f"BMA step {step_tag!r} must have exactly one following StoreSolution before the next StudyStep")
        store = stores[0]
        solution_tag = store.get("stored_solution_reference")
        if (store.get("stored_solution_reference_getter") != "StoreSolution.getString('sol')"
                or not isinstance(solution_tag, str) or not solution_tag.strip()
                or store.get("stored_solution_reference_read_error") is not None):
            raise ScienceProtocolError(f"BMA step {step_tag!r} StoreSolution.sol readback is unavailable")
        output_sequence_rows = [row for row in sequences if isinstance(row, Mapping)
                                and row.get("tag") == solution_tag]
        if len(output_sequence_rows) != 1:
            raise ScienceProtocolError(f"StoreSolution points to a missing or duplicate native solution {solution_tag!r}")
        output_sequence = output_sequence_rows[0]
        if (output_sequence.get("sequence_type") != "Stored"
                or output_sequence.get("solution_type") != "Eigenfrequency"
                or output_sequence.get("study_tag") != study_tag
                or output_sequence.get("attached") is not False):
            raise ScienceProtocolError(f"stored BMA solution {solution_tag!r} has an unexpected native sequence identity")
        matches = dataset_matches.get(solution_tag, [])
        if len(matches) != 1:
            raise ScienceProtocolError(f"stored BMA solution {solution_tag!r} must map to exactly one Solution dataset")
        dataset_receipt = matches[0]
        dataset_row = dataset_receipt["dataset"]
        indices = dataset_receipt["solution_indices"]
        binding_receipt = indices.get("dataset_binding")
        if (not isinstance(binding_receipt, Mapping)
                or binding_receipt.get("binding_complete") is not True
                or binding_receipt.get("solution") != solution_tag):
            raise ScienceProtocolError(f"dataset for stored BMA solution {solution_tag!r} lacks exact native dataset binding")
        worker_observation = indices.get("solver_sequence_parameter_observation")
        reconciled_observation = reconcile_stored_sequence_parameter_observation(
            output_sequence, worker_observation)
        if (reconciled_observation.get("pnames") != ["lambda"]
                or reconciled_observation.get("get_size", [None, None])[1] != 1):
            raise ScienceProtocolError("a BMA saved mode must expose one raw lambda value before scalar formula checks")
        resolved.append({
            "study_step_tag": step_tag,
            "study_tag": study_tag,
            "study_step_type": bma["feature_type"],
            "port_name": str(bma["PortName"]),
            "mode_frequency_expression": str(bma["modeFreq"]),
            "solver_sequence_tag": sequence_tag,
            "study_step_feature_path": binding["path"],
            "study_step_tree_order": current_order,
            "store_solution_feature_path": store.get("path"),
            "store_solution_tree_order": store.get("tree_order"),
            "store_solution_reference": solution_tag,
            "store_solution_reference_getter": store.get("stored_solution_reference_getter"),
            "dataset_tag": dataset_row["tag"],
            "dataset_solution_property": indices.get("solution"),
            "dataset_binding": dict(binding_receipt),
            "solution_info_axis_metadata_complete": indices.get("axis_metadata_complete"),
            "solution_info_parameters_complete": indices.get("parameters_complete"),
            "solution_info_read_errors": list(indices.get("read_errors") or []),
            "sequence_parameter_observation": reconciled_observation,
            "status": "VERIFIED_STEP_STORE_DATASET_BINDING_ONLY",
            "science_acceptance": "NOT_EVALUATED",
        })
    return {
        "status": "PASS_BMA_STUDY_STORE_DATASET_BINDINGS",
        "study_tag": study_tag,
        "solver_sequence_tag": sequence_tag,
        "bindings": resolved,
        "science_acceptance": "NOT_EVALUATED",
        "solution_info_axis_mapping_repaired": False,
        "basis": "captured StudyStep.studystep + ordered solver-tree feature + StoreSolution.getString('sol') + production dataset.solution binding",
    }


def resolve_main_frequency_dataset(
    study_inventory: Mapping[str, Any], dataset_inventory: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind the one main BMA frequency state without inventing outer=1.

    The count proof is a strict singleton for the exact attached main
    SolverSequence. ``SolutionInfo`` tuple completeness remains false; an
    empty ``getOuterSolnum`` is preserved as no outer axis.
    """
    if not isinstance(study_inventory, Mapping) or not isinstance(dataset_inventory, Mapping):
        raise ScienceProtocolError("main-frequency inputs must be objects")
    study_tag = study_inventory.get("study_tag")
    sequences = study_inventory.get("solver_sequences_for_study")
    steps = study_inventory.get("study_steps_in_configured_order")
    receipts = dataset_inventory.get("solution_index_receipts")
    if (not isinstance(study_tag, str) or not study_tag or not isinstance(sequences, list)
            or not isinstance(steps, list) or not isinstance(receipts, list)):
        raise ScienceProtocolError("main-frequency inventory is incomplete")
    main_rows = [row for row in sequences if isinstance(row, Mapping)
                 and row.get("sequence_type") == "SolverSequence"
                 and row.get("solution_type") == "Parametric"
                 and row.get("study_tag") == study_tag and row.get("attached") is True]
    if len(main_rows) != 1:
        raise ScienceProtocolError("saved W23 study must have exactly one attached Parametric SolverSequence")
    main = main_rows[0]
    tag = main.get("tag")
    if not isinstance(tag, str) or not tag:
        raise ScienceProtocolError("attached main SolverSequence has no exact native tag")
    bindings = main.get("study_step_bindings_in_solver_tree_order")
    tree = main.get("solver_tree_features")
    if not isinstance(bindings, list) or not isinstance(tree, list):
        raise ScienceProtocolError("attached main SolverSequence lacks ordered StudyStep/tree readback")
    matching_frequency_step = [row for row in bindings if isinstance(row, Mapping)
                               and row.get("study") == study_tag
                               and row.get("studystep") == "freq"
                               and row.get("solver_sequence") == tag]
    if len(matching_frequency_step) != 1:
        raise ScienceProtocolError("Frequency step is not exactly bound to the attached main SolverSequence")
    binding = matching_frequency_step[0]
    tree_matches = [row for row in tree if isinstance(row, Mapping)
                    and row.get("path") == binding.get("path")
                    and row.get("tree_order") == binding.get("tree_order")
                    and row.get("feature_type") == "StudyStep"]
    if len(tree_matches) != 1:
        raise ScienceProtocolError("Frequency StudyStep binding does not match the actual ordered solver-tree feature")

    dataset_matches: list[Mapping[str, Any]] = []
    for receipt in receipts:
        if not isinstance(receipt, Mapping):
            raise ScienceProtocolError("solution-index receipt is malformed")
        dataset = receipt.get("dataset")
        indices = receipt.get("solution_indices")
        if (isinstance(dataset, Mapping) and dataset.get("type_id", "").lower() == "solution"
                and dataset.get("solution") == tag and isinstance(indices, Mapping)):
            dataset_matches.append(receipt)
    if len(dataset_matches) != 1:
        raise ScienceProtocolError("attached main SolverSequence must bind to exactly one production Solution dataset")
    receipt = dataset_matches[0]
    dataset = receipt["dataset"]
    indices = receipt["solution_indices"]
    dataset_tag = dataset.get("tag")
    dataset_binding = indices.get("dataset_binding")
    worker = indices.get("solver_sequence_parameter_observation")
    if (not isinstance(dataset_tag, str) or not dataset_tag
            or not isinstance(dataset_binding, Mapping)
            or dataset_binding.get("binding_complete") is not True
            or dataset_binding.get("dataset") != dataset_tag
            or dataset_binding.get("dataset_type") != "Solution"
            or dataset_binding.get("solution") != tag
            or not isinstance(worker, Mapping)):
        raise ScienceProtocolError("frequency Solution dataset lacks an exact production dataset.solution binding/readback")
    reconciled = reconcile_stored_sequence_parameter_observation(main, worker)
    observation = worker.get("solution_info_count_scope")
    java_scope = main.get("solution_info_count_scope")
    if not isinstance(observation, Mapping) or not isinstance(java_scope, Mapping):
        raise ScienceProtocolError("frequency singleton lacks both Java and Worker SolutionInfo count-scope getters")
    expected_scope = {"getLevelNames": ["freq"], "getLevels": 1, "getOuterSolnum": []}
    for label, scope in (("Java", java_scope), ("Worker", observation)):
        for key, value in expected_scope.items():
            if scope.get(key) != value:
                raise ScienceProtocolError(f"{label} SolutionInfo {key} does not prove the exact no-outer freq singleton")
        if scope.get("read_errors"):
            raise ScienceProtocolError(f"{label} SolutionInfo frequency count-scope getter contains errors")
    if (java_scope.get("getPNamesOuter") != [] or java_scope.get("getPUnitsOuter") != []):
        raise ScienceProtocolError("Java SolutionInfo did not capture exact empty outer parameter name/unit arrays")
    java_size = main.get("get_size")
    if (not isinstance(java_size, list) or len(java_size) != 2
            or any(type(item) is not int for item in java_size)
            or java_size[0] <= 0 or java_size[1] != 1
            or main.get("get_size_multi") != [1, 1]
            or main.get("pnames") != ["freq"]
            or main.get("parameter_values_status") != "RAW_TYPED_COMPLEX_VALUES_CAPTURED_NOT_INTERPRETED"
            or worker.get("status") != "OBSERVED_TYPED_COMPLEX_PARAMETER_VALUES"
            or worker.get("get_size_multi") != [1, 1]
            or worker.get("pnames") != ["freq"]
            or worker.get("parameters_complete") is not False
            or indices.get("parameters_complete") is not False
            or indices.get("axis_metadata_complete") is not False):
        raise ScienceProtocolError("frequency sequence is not a strict one-state/no-tuple-completeness witness")
    direct_values = main.get("parameter_values_by_solnum")
    worker_values = worker.get("solution_values")
    if (not isinstance(direct_values, list) or len(direct_values) != 1
            or not isinstance(worker_values, list) or len(worker_values) != 1):
        raise ScienceProtocolError("frequency sequence does not expose exactly one raw numeric solution")
    params = reconciled.get("values")
    if (not isinstance(params, list) or len(params) != 1
            or not isinstance(params[0].get("parameters"), list)
            or len(params[0]["parameters"]) != 1
            or params[0]["parameters"][0].get("name") != "freq"
            or not isinstance(params[0]["parameters"][0].get("unit"), str)
            or not params[0]["parameters"][0].get("unit")):
        raise ScienceProtocolError("Java and Worker raw frequency getter values/labels do not reconcile as one exact scalar")
    parameter = params[0]["parameters"][0]
    frequency_real = _as_finite_float(parameter.get("real"), "observed main frequency")
    frequency_imag = _as_finite_float(parameter.get("imag"), "observed main frequency imaginary part")
    if frequency_real <= 0 or frequency_imag != 0.0:
        raise ScienceProtocolError("raw main frequency getter is not a positive real singleton")
    return {
        "status": "PASS_EXACT_ATTACHED_MAIN_COUNT_SINGLETON",
        "study_tag": study_tag,
        "study_step_tag": "freq",
        "study_step_binding": dict(binding),
        "solver_sequence_tag": tag,
        "sequence_identity": {key: main.get(key) for key in
                               ("sequence_type", "solution_type", "study_tag", "attached")},
        "dataset_tag": dataset_tag,
        "dataset_solution_property": tag,
        "dataset_binding": dict(dataset_binding),
        "raw_parameter": dict(parameter),
        "raw_unit_semantics": "UNRESOLVED_GETTER_LABEL_NO_CONVERSION",
        "physical_frequency_hz": "NOT_CAPTURED",
        "get_size": list(java_size),
        "get_size_multi": list(main["get_size_multi"]),
        "solution_info_count_scope": {"Java": dict(java_scope), "Worker": dict(observation)},
        "sequence_value_reconciliation": reconciled,
        "solution_info_axis_metadata_complete": False,
        "parameters_complete": False,
        "outer_indices_inferred": False,
        "science_acceptance": "NOT_EVALUATED",
    }


def field_scheme_roles(field_scheme: str = "legacy") -> dict[str, Any]:
    if field_scheme not in {"legacy", "emw_down"}:
        raise ScienceProtocolError("unknown explicit field_scheme")
    prefix = "ewfd" if field_scheme=="legacy" else "emw"
    return {"signal_e":{a:f"{prefix}.E{a}" if field_scheme=="legacy" else f"down(emw.E{a})" for a in "xyz"},
        "signal_h":{a:f"{prefix}.H{a}" if field_scheme=="legacy" else f"down(emw.H{a})" for a in "xyz"},
        "mode_e":{i:{a:f"{prefix}.Emode{a}_{i}" for a in "xyz"} for i in ("1","2")},
        "mode_h":{i:{a:f"{prefix}.Hmode{a}_{i}" for a in "xyz"} for i in ("1","2")},
        "nx":"nx" if field_scheme=="legacy" else "emw.nx", "ny":"ny" if field_scheme=="legacy" else "emw.ny",
        "prefix":prefix}


def _require_field_matrix(raw: Mapping[str, Any], field_scheme: str, plane: str) -> None:
    roles=field_scheme_roles(field_scheme)
    _raw_expression_map(raw)
    expected=list(roles["signal_e"].values())+list(roles["signal_h"].values())
    ports=("2","1") if field_scheme=="legacy" else ("2",) if plane=="output_x8" else ("1",) if plane=="input_xminus10" else ()
    if not ports:raise ScienceProtocolError("explicit field plane is not registered")
    for port in ports:expected+=list(roles["mode_e"][port].values())+list(roles["mode_h"][port].values())
    if field_scheme=="legacy":expected += ["ewfd.neff_1","ewfd.neff_2"]
    expected += [roles["nx"],roles["ny"]]
    actual=[row.get("expression") for row in raw.get("expressions",[]) if isinstance(row,Mapping)]
    if actual!=expected:
        raise ScienceProtocolError("complete producer field matrix differs from explicit field_scheme/plane; no relabel or missing-name fallback")


def bma_point_expressions(port_name: str, *, field_scheme: str = "legacy") -> tuple[str, str, str]:
    """Use only a validated native PortName; callers bind it to exact BMA store/dataset."""
    if port_name not in {"1", "2"}:
        raise ScienceProtocolError("BMA expression selection requires native PortName 1 or 2")
    prefix=field_scheme_roles(field_scheme)["prefix"]
    return (f"{prefix}.beta_{port_name}", f"{prefix}.neff_{port_name}", f"{prefix}.k0")


def _require_requested_unit_application(
    application: Any, expressions: Sequence[str], units: Sequence[str],
) -> None:
    if (not isinstance(application, Mapping)
            or application.get("status") != "VERIFIED_REQUESTED_UNITS_BEFORE_AND_AFTER_NUMERIC_READ"
            or application.get("requested_expressions") != list(expressions)
            or application.get("requested_units") != list(units)
            or len(units) != len(expressions)
            or application.get("setter") != "actual Interp NumericalFeature.setIndex(unit,requested,index)"
            or application.get("no_external_scaling_or_relabeling") is not True):
        raise ScienceProtocolError("production Interp lacks exact requested-unit application proof")
    native_defaults = [expression for expression, unit in zip(expressions, units) if unit == ""]
    unsupported = [expression for expression in native_defaults
                   if expression not in NATIVE_DEFAULT_ALLOWED_EXPRESSIONS]
    if unsupported:
        raise ScienceProtocolError(
            "production Interp empty-unit request is outside the exact built-in native-default allowlist")
    if native_defaults:
        if application.get("native_default_unit_expressions") != native_defaults:
            raise ScienceProtocolError(
                "production Interp native-default unit request is not bound to its exact expressions")
    elif ("native_default_unit_expressions" in application
          and application["native_default_unit_expressions"] != []):
        raise ScienceProtocolError(
            "production Interp proof declares native-default expressions for a nonblank unit request")
    for key, stage in (("before", "before_numeric_read"), ("after", "after_numeric_read")):
        observed = application.get(key)
        if (not isinstance(observed, Mapping)
                or observed.get("status") != "VERIFIED_EXACT_NATIVE_ARRAYS"
                or observed.get("stage") != stage
                or observed.get("expressions") != list(expressions)
                or observed.get("units") != list(units)
                or observed.get("source") != "same actual Interp NumericalFeature.getStringArray(expr/unit)"):
            raise ScienceProtocolError("production Interp requested-unit native expr/unit arrays changed or incomplete")


def verify_frequency_unit_scaling(
    hz: Mapping[str, Any], ghz: Mapping[str, Any],
    contexts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare two actual numerical results; never publish a converted/relabeled result."""
    points = (hz, ghz)
    if not isinstance(contexts, Sequence) or len(contexts) != 2:
        raise ScienceProtocolError("native frequency unit control needs two exact call contexts")
    first_identity = None
    first_points = None
    raw = []
    for point, unit, context in zip(points, ("Hz", "GHz"), contexts):
        if (not isinstance(point, Mapping) or point.get("status") != "PRODUCTION_NATIVE_COMPLEX_POINT_READBACK"
                or point.get("dataset") != "dset1" or point.get("solution") != "sol1"
                or point.get("sample_count") != 1 or point.get("expression_units") != {"freq": unit}
                or type(point.get("native_is_complex")) is not bool
                or not isinstance(context, Mapping)
                or context.get("model_ref_before") != context.get("model_ref_after")
                or context.get("revision_before") != context.get("revision_after")
                or context.get("worker_epoch_before") != context.get("worker_epoch_after")):
            raise ScienceProtocolError("native frequency unit control data/model/revision/Worker epoch changed or unverified")
        model_ref, epoch = context.get("model_ref_before"), context.get("worker_epoch_before")
        if (not isinstance(model_ref, Mapping) or not model_ref.get("model_tag")
                or not model_ref.get("session_id") or not model_ref.get("server_instance_id")
                or type(model_ref.get("generation")) is not int or model_ref["generation"] <= 0
                or type(context.get("revision_before")) is not int
                or not isinstance(epoch, Mapping) or not epoch.get("instance_id")
                or type(epoch.get("generation")) is not int or epoch["generation"] <= 0
                or not epoch.get("server")):
            raise ScienceProtocolError("native frequency unit control lacks actual model or Worker epoch identity")
        identity = (dict(model_ref), context["revision_before"], dict(epoch))
        if first_identity is None: first_identity = identity
        elif identity != first_identity:
            raise ScienceProtocolError("native dual-unit frequency reads do not share ModelRef/revision/Worker epoch")
        coords = point.get("coordinates_m")
        if (not isinstance(coords, list) or len(coords) != 2
                or any(not isinstance(row, list) or len(row) != 1 for row in coords)):
            raise ScienceProtocolError("native frequency unit control coordinate shape is invalid")
        for row in coords: _as_finite_float(row[0], "native frequency-control coordinate")
        if first_points is None: first_points = coords
        elif coords != first_points:
            raise ScienceProtocolError("native frequency unit control coordinates changed")
        _require_requested_unit_application(point.get("requested_unit_application"), ["freq"], [unit])
        rows = point.get("expressions")
        if (not isinstance(rows, list) or len(rows) != 1 or rows[0].get("expression") != "freq"
                or len(rows[0].get("real", [])) != 1 or len(rows[0].get("imag", [])) != 1):
            raise ScienceProtocolError("native frequency unit control scalar shape changed")
        value = _finite_complex_row(rows[0]["real"][0], rows[0]["imag"][0], label=f"actual native freq[{unit}]")
        if value.real <= 0:
            raise ScienceProtocolError("native frequency unit control real part is not positive")
        if point["native_is_complex"] is False and value.imag != 0.0:
            raise ScienceProtocolError("native real-only frequency unit control has nonzero imaginary value")
        raw.append(value)
    scale = 1e9
    tolerance = 1e-12
    real_ok = math.isclose(raw[0].real, raw[1].real * scale, rel_tol=tolerance, abs_tol=0.0)
    imag_ok = math.isclose(raw[0].imag, raw[1].imag * scale, rel_tol=tolerance, abs_tol=0.0)
    if not real_ok or not imag_ok:
        raise ScienceProtocolError("NATIVE_UNIT_SCALING_UNVERIFIED: actual getData Hz/GHz values do not scale by 1e9; no physical frequency acceptance, escalate to Root")
    return {"status": "PASS_ACTUAL_NATIVE_FREQ_HZ_GHZ_SCALING", "scale_relation": "freq_Hz=1e9*freq_GHz",
            "relative_tolerance": tolerance, "absolute_tolerance": 0.0,
            "native_raw_values": [{"unit": unit, "real": value.real, "imag": value.imag}
                                  for unit, value in zip(("Hz", "GHz"), raw)],
            "contexts": [dict(context) for context in contexts], "coordinates_m": first_points,
            "converted_or_relabeled_data_created": False, "science_acceptance": "NOT_EVALUATED"}


def resolve_physical_frequency_point_readback(
    point: Mapping[str, Any], count_witness: Mapping[str, Any],
) -> dict[str, Any]:
    """Read Hz from the same native Interp value/unit; raw getter labels stay separate."""
    if (not isinstance(point, Mapping) or not isinstance(count_witness, Mapping)
            or count_witness.get("status") != "PASS_EXACT_ATTACHED_MAIN_COUNT_SINGLETON"
            or point.get("status") != "PRODUCTION_NATIVE_COMPLEX_POINT_READBACK"
            or point.get("dataset") != count_witness.get("dataset_tag")
            or point.get("solution") != count_witness.get("solver_sequence_tag")
            or point.get("sample_count") != 1
            or type(point.get("native_is_complex")) is not bool
            or point.get("expression_units") != {"freq": "Hz"}
            or not isinstance(point.get("requested_unit_application"), Mapping)
            or point["requested_unit_application"].get("status") != "VERIFIED_REQUESTED_UNITS_BEFORE_AND_AFTER_NUMERIC_READ"
            or point["requested_unit_application"].get("requested_expressions") != ["freq"]
            or point["requested_unit_application"].get("requested_units") != ["Hz"]):
        raise ScienceProtocolError("physical frequency requires exact native dataset/solution scalar freq[Hz]")
    _require_requested_unit_application(point.get("requested_unit_application"), ["freq"], ["Hz"])
    rows = point.get("expressions")
    if (not isinstance(rows, list) or len(rows) != 1 or rows[0].get("expression") != "freq"
            or len(rows[0].get("real", [])) != 1 or len(rows[0].get("imag", [])) != 1):
        raise ScienceProtocolError("physical frequency numerical scalar shape is invalid")
    frequency = _finite_complex_row(rows[0]["real"][0], rows[0]["imag"][0], label="native freq[Hz]")
    if frequency.real <= 0 or frequency.imag != 0:
        raise ScienceProtocolError("physical frequency must be native positive real Hz")
    return {"status": "PASS_NATIVE_NUMERICAL_FREQUENCY_HZ",
            "dataset_tag": point["dataset"], "solution_tag": point["solution"],
            "frequency_hz": frequency.real, "frequency_imag_hz": frequency.imag,
            "frequency_unit": "Hz", "count_witness": dict(count_witness),
            "basis": "production Interp requested Hz + actual numerical value/unit readback",
            "getter_unit_conversion_applied": False, "science_acceptance": "NOT_EVALUATED"}


def reconcile_bma_mode_point_readbacks(
    bindings: Mapping[str, Any],
    mode_point_receipts: Mapping[str, Mapping[str, Any]],
    main_frequency: Mapping[str, Any], *, field_scheme: str = "legacy",
) -> dict[str, Any]:
    """Cross-check stored BMA lambda against production beta/neff/k0 values.

    The applicable COMSOL Wave Optics relation is beta_i=j*lambda and
    neff_i=beta_i/k0. This check requires the captured StoreSolution/StudyStep
    provenance, observed units, and the attached main frequency witness; it
    never infers an outer/inner tuple or a unit from a tag/value.
    """
    if (not isinstance(bindings, Mapping) or bindings.get("status") != "PASS_BMA_STUDY_STORE_DATASET_BINDINGS"
            or not isinstance(mode_point_receipts, Mapping)
            or not isinstance(main_frequency, Mapping)
            or main_frequency.get("status") != "PASS_NATIVE_NUMERICAL_FREQUENCY_HZ"):
        raise ScienceProtocolError("BMA mode formula check lacks exact study/store/dataset and main-frequency witnesses")
    rows = bindings.get("bindings")
    if not isinstance(rows, list) or {row.get("study_step_tag") for row in rows if isinstance(row, Mapping)} != {"bmaInput", "bmaOutput"}:
        raise ScienceProtocolError("BMA formula check requires exact input and output StoreSolution bindings")
    frequency = _as_finite_float(main_frequency.get("frequency_hz"), "main frequency Hz")
    if main_frequency.get("frequency_unit") != "Hz" or frequency <= 0:
        raise ScienceProtocolError("main frequency is not an observed positive Hz value")
    c_m_s = 299_792_458.0
    expected_k0 = 2.0 * math.pi * frequency / c_m_s
    output: list[dict[str, Any]] = []
    expected_tags = {"bmaInput": "1", "bmaOutput": "2"}
    for binding in rows:
        if not isinstance(binding, Mapping):
            raise ScienceProtocolError("BMA binding row is malformed")
        step = binding.get("study_step_tag")
        if step not in expected_tags or binding.get("port_name") != expected_tags[step]:
            raise ScienceProtocolError("BMA formula row does not match the exact input/output port role")
        if binding.get("mode_frequency_expression") != "f0":
            raise ScienceProtocolError("BMA modeFreq no longer binds to the captured f0 expression")
        point = mode_point_receipts.get(step)
        if (not isinstance(point, Mapping)
                or point.get("status") != "PRODUCTION_NATIVE_COMPLEX_POINT_READBACK"
                or point.get("dataset") != binding.get("dataset_tag")
                or point.get("solution") != binding.get("store_solution_reference")
                or point.get("sample_count") != 1
                or type(point.get("native_is_complex")) is not bool):
            raise ScienceProtocolError(f"{step} result.at_points readback is not bound to its exact stored BMA dataset")
        observation = binding.get("sequence_parameter_observation")
        values = observation.get("values") if isinstance(observation, Mapping) else None
        params = values[0].get("parameters") if isinstance(values, list) and len(values) == 1 and isinstance(values[0], Mapping) else None
        if (not isinstance(params, list) or len(params) != 1
                or params[0].get("name") != "lambda"
                or not isinstance(params[0].get("unit"), str) or not params[0].get("unit")):
            raise ScienceProtocolError(f"{step} stored BMA sequence lacks one reconciled raw lambda getter")
        lambda_value = _finite_complex_row(params[0].get("real"), params[0].get("imag"), label=f"{step} lambda")
        result_expressions = point.get("expressions")
        units = point.get("expression_units")
        if not isinstance(result_expressions, list) or not isinstance(units, Mapping):
            raise ScienceProtocolError(f"{step} native expression-unit readback is unavailable")
        expression_rows = {row.get("expression"): row for row in result_expressions if isinstance(row, Mapping)}
        expected_expressions = bma_point_expressions(binding["port_name"], field_scheme=field_scheme)
        if tuple(row.get("expression") for row in result_expressions) != expected_expressions:
            raise ScienceProtocolError(f"{step} wrong-port or unordered BMA expression readback")
        application = point.get("requested_unit_application")
        if (not isinstance(application, Mapping)
                or application.get("status") != "VERIFIED_REQUESTED_UNITS_BEFORE_AND_AFTER_NUMERIC_READ"
                or application.get("requested_expressions") != list(expected_expressions)
                or application.get("requested_units") not in (
                    list(BMA_LEGACY_REQUESTED_UNITS), list(BMA_NATIVE_DEFAULT_NEFF_REQUESTED_UNITS))):
            raise ScienceProtocolError(f"{step} lacks exact native requested-unit application proof")
        requested_units = application["requested_units"]
        _require_requested_unit_application(application, expected_expressions, requested_units)
        beta_expression, neff_expression, k0_expression = expected_expressions
        values_by_name: dict[str, complex] = {}
        for expression in expected_expressions:
            row = expression_rows.get(expression)
            if not isinstance(row, Mapping) or len(row.get("real", [])) != 1 or len(row.get("imag", [])) != 1:
                raise ScienceProtocolError(f"{step} saved readback omitted a scalar {expression} value")
            values_by_name[expression] = _finite_complex_row(row["real"][0], row["imag"][0], label=f"{step} {expression}")
            if point["native_is_complex"] is False and values_by_name[expression].imag != 0.0:
                raise ScienceProtocolError(f"{step} native real-only scalar has nonzero imaginary value")
        if (units.get(beta_expression) != "1/m" or units.get(k0_expression) != "1/m"
                or units.get(neff_expression) != requested_units[1]):
            raise ScienceProtocolError(
                f"{step} beta/k0 units must stay 1/m and neff readback must equal its exact requested unit "
                "('1' legacy or '' native default)"
            )
        beta = values_by_name[beta_expression]
        neff = values_by_name[neff_expression]
        k0 = values_by_name[k0_expression]
        beta_expected = 1j * lambda_value
        beta_error = abs(beta - beta_expected) / max(abs(beta_expected), 1.0)
        k0_error = abs(k0 - expected_k0) / max(abs(expected_k0), 1.0)
        neff_expected = beta / k0 if abs(k0) > 0 else complex(float("nan"), float("nan"))
        neff_error = abs(neff - neff_expected) / max(abs(neff_expected), 1.0)
        if not all(math.isfinite(value) for value in (beta_error, k0_error, neff_error)):
            raise ScienceProtocolError(f"{step} beta/neff/k0 cross-check contains non-finite values")
        if beta_error > 1e-8 or k0_error > 1e-8 or neff_error > 1e-8:
            raise ScienceProtocolError(f"{step} native BMA formula cross-check exceeds roundoff allowance: beta={beta_error}, k0={k0_error}, neff={neff_error}")
        output.append({
            "study_step_tag": step, "port_name": binding["port_name"],
            "dataset_tag": binding["dataset_tag"], "solution_tag": binding["store_solution_reference"],
            "lambda_raw_getter": {"real": lambda_value.real, "imag": lambda_value.imag, "unit_label": params[0]["unit"],
                                  "physical_dimension": "UNRESOLVED_NUMERIC_CONSISTENCY_ONLY"},
            "expressions": list(expected_expressions),
            "beta": {"real": beta.real, "imag": beta.imag, "unit": units[beta_expression]},
            "neff": {"real": neff.real, "imag": neff.imag, "unit": units[neff_expression]},
            "k0": {"real": k0.real, "imag": k0.imag, "unit": units[k0_expression]},
            "observed_frequency_hz": frequency,
            "formulae": {"beta_i": "beta_i=j*lambda", "neff_i": "neff_i=beta_i/k0",
                         "k0": "2*pi*observed_main_frequency_hz/c"},
            "relative_residuals": {"beta_vs_j_lambda": beta_error,
                                    "k0_vs_frequency": k0_error,
                                    "neff_vs_beta_over_k0": neff_error},
            "status": "PASS_BMA_FORMULA_CROSSCHECK_ONLY",
            "science_acceptance": "NOT_EVALUATED",
        })
    return {"status": "PASS_BMA_FORMULA_CROSSCHECK_ONLY", "bindings": output,
            "basis": "COMSOL Wave Optics Users Guide doc26574/chunk112469 PDF p.91; actual StudyStep/StoreSolution/dataset provenance and native expressions",
            "science_acceptance": "NOT_EVALUATED"}


def _require_point_selection_application(result: Mapping[str, Any], expected_selection: Mapping[str, Any]) -> None:
    application = result.get("feature_readback", {}).get("selection_application")
    if not isinstance(application, Mapping) or set(application)!={"before", "after"}:
        raise ScienceProtocolError("field point readback lacks actual before/after transient selection")
    entities = [8,10,12] if expected_selection.get("tag")=="selReceiverX8" else [1,3,5] if expected_selection.get("tag")=="selInputPort" else None
    if entities is None:raise ScienceProtocolError("field plane selection is not registered")
    before = after = None
    for stage,key in [("before_numeric_read","before"),("after_numeric_read","after")]:
        record = application[key];observed = record.get("native_selection_readback") if isinstance(record,Mapping) else None
        if (not isinstance(observed,Mapping) or record.get("stage")!=stage or record.get("component")!=expected_selection["component"]
            or record.get("geometry")!=expected_selection["geometry"] or record.get("entity_dimension")!=1
            or record.get("kind")!="named" or record.get("tag")!=expected_selection["tag"]
            or observed.get("geometry")!="geom1" or type(observed.get("dimension")) is not int or observed["dimension"]!=1
            or observed.get("named")!=expected_selection["tag"] or observed.get("entities")!=entities
            or observed.get("is_inheriting") is not False):
            raise ScienceProtocolError("field transient selection identity/membership/inheritance mismatch")
        if key=="before":before=observed
        else:after=observed
    if before!=after:raise ScienceProtocolError("field transient selection changed during point read")

def decode_production_complex_point_readback(
    result: Mapping[str, Any], *, dataset_tag: str, solution_tag: str,
    expressions: Sequence[str], expected_points: Sequence[Sequence[float]],
    expected_solution_count: int, plane_id: str,
    expected_count_witness: Mapping[str, Any] | None = None,
    expected_expression_units: Mapping[str, str | Sequence[str]] | None = None,
    require_x_plane_normal: bool = False,
    expected_requested_units: Sequence[str] | None = None,
    scalar_frequency: bool = False,
    scalar_bma_binding: Mapping[str, Any] | None = None,
    expected_selection: Mapping[str, Any] | None = None,
    field_scheme: str = "legacy",
) -> dict[str, Any]:
    """Decode one exact singleton through production result.at_points.

    ``expected_points`` are expressed in the coordinate unit reported by the
    native geometry readback.  A count-only attached-frequency witness may
    bound the result sample allocation without claiming SolutionInfo tuple
    completeness.
    """
    field_scheme_roles(field_scheme)
    if not isinstance(result, Mapping):
        raise ScienceProtocolError("production result.at_points response must be an object")
    if type(scalar_frequency) is not bool:
        raise ScienceProtocolError("scalar frequency route marker must be an actual bool")
    scalar_route = False
    if scalar_frequency:
        scalar_route = (scalar_bma_binding is None and tuple(expressions) == ("freq",)
                        and dataset_tag == "dset1" and solution_tag == "sol1" and len(expected_points) == 1
                        and list(expected_requested_units or []) in (["Hz"], ["GHz"])
                        and isinstance(expected_count_witness, Mapping)
                        and expected_count_witness.get("status") == "PASS_EXACT_ATTACHED_MAIN_COUNT_SINGLETON")
        if not scalar_route:
            raise ScienceProtocolError("real-only frequency route lacks its exact captured singleton/unit binding")
    if scalar_bma_binding is not None:
        port = scalar_bma_binding.get("port_name") if isinstance(scalar_bma_binding, Mapping) else None
        expected_step = {"1": "bmaInput", "2": "bmaOutput"}.get(port)
        scalar_route = (not scalar_frequency and expected_step is not None
                        and scalar_bma_binding.get("status") == "VERIFIED_STEP_STORE_DATASET_BINDING_ONLY"
                        and scalar_bma_binding.get("study_step_tag") == expected_step
                        and scalar_bma_binding.get("mode_frequency_expression") == "f0"
                        and scalar_bma_binding.get("dataset_tag") == dataset_tag
                        and scalar_bma_binding.get("store_solution_reference") == solution_tag
                        and tuple(expressions) == bma_point_expressions(port, field_scheme=field_scheme) and len(expected_points) == 1
                        and list(expected_requested_units or []) in (
                            list(BMA_LEGACY_REQUESTED_UNITS), list(BMA_NATIVE_DEFAULT_NEFF_REQUESTED_UNITS)))
        if not scalar_route:
            raise ScienceProtocolError("real-only BMA route lacks exact validated native port/store/dataset/units")
    native_is_complex = result.get("is_complex")
    status = result.get("status")
    cleanup = result.get("cleanup")
    if (not isinstance(status, Mapping) or status.get("ok") is not True
            or not isinstance(cleanup, Mapping) or cleanup.get("cleanup_failed") is True
            or result.get("complex_mode") != "preserve"
            or type(native_is_complex) is not bool
            or (native_is_complex is False and not scalar_route)):
        raise ScienceProtocolError("production result.at_points did not return a clean complex-preserve result")
    if result.get("dataset") != dataset_tag or result.get("solution") != solution_tag:
        raise ScienceProtocolError("production result.at_points did not retain the exact dataset/solution identity")
    binding = result.get("dataset_binding")
    if (not isinstance(binding, Mapping) or binding.get("binding_complete") is not True
            or binding.get("solution") != solution_tag):
        raise ScienceProtocolError("production result.at_points lacks exact native dataset.solution binding")
    axes = result.get("solution_axes")
    count_observation = axes.get("numeric_count_observation") if isinstance(axes, Mapping) else None
    count_statuses = {
        "VERIFIED_SINGLE_STORED_NUMERIC_COUNT",
        "VERIFIED_SINGLE_ATTACHED_PARAMETRIC_NUMERIC_COUNT",
    }
    if (not isinstance(axes, Mapping)
            or axes.get("pair_mapping_complete") is not False
            or not isinstance(count_observation, Mapping)
            or count_observation.get("status") not in count_statuses
            or count_observation.get("numeric_solution_count") != 1
            or count_observation.get("dataset") != dataset_tag
            or count_observation.get("solution") != solution_tag
            or not isinstance(count_observation.get("source"), str)
            or not count_observation.get("source")):
        raise ScienceProtocolError(
            "production point readback lacks the exact numeric singleton witness; "
            "outer/inner SolutionInfo mapping must remain explicitly incomplete")
    if count_observation.get("status") == "VERIFIED_SINGLE_ATTACHED_PARAMETRIC_NUMERIC_COUNT":
        observation = count_observation.get("observation")
        count_scope = observation.get("solution_info_count_scope") if isinstance(observation, Mapping) else None
        parameter_rows = observation.get("parameter_rows") if isinstance(observation, Mapping) else None
        if (not isinstance(expected_count_witness, Mapping)
                or expected_count_witness.get("status") != "PASS_EXACT_ATTACHED_MAIN_COUNT_SINGLETON"
                or expected_count_witness.get("dataset_tag") != dataset_tag
                or expected_count_witness.get("solver_sequence_tag") != solution_tag
                or not isinstance(observation, Mapping)
                or observation.get("sequence_type") != "SolverSequence"
                or observation.get("solution_type") != "Parametric"
                or observation.get("attached") is not True
                or observation.get("study") != expected_count_witness.get("study_tag")
                or observation.get("solution_identity_matches_dataset_study") is not True
                or observation.get("get_size") != expected_count_witness.get("get_size")
                or observation.get("get_size_multi") != [1, 1]
                or observation.get("pnames") != ["freq"]
                or not isinstance(count_scope, Mapping)
                or count_scope.get("getLevelNames") != ["freq"]
                or count_scope.get("getLevels") != 1
                or count_scope.get("getOuterSolnum") != []
                or count_scope.get("read_errors")
                or not isinstance(parameter_rows, list) or len(parameter_rows) != 1
                or parameter_rows[0].get("name") != "freq"
                or parameter_rows[0].get("unit") != expected_count_witness.get("raw_parameter", {}).get("unit")):
            raise ScienceProtocolError("attached result.at_points singleton does not match the independently resolved main frequency dataset witness")
        parameter_row = parameter_rows[0]
        observed_frequency = _as_finite_float(parameter_row.get("real"),
                                              "attached point-readback frequency")
        observed_frequency_imag = _as_finite_float(parameter_row.get("imag"),
                                                   "attached point-readback frequency imaginary part")
        if (observed_frequency != expected_count_witness.get("raw_parameter", {}).get("real")
                or observed_frequency_imag != expected_count_witness.get("raw_parameter", {}).get("imag")):
            raise ScienceProtocolError("attached point-readback frequency values differ from direct Java/Worker witness")
    budget = result.get("result_budget")
    records = budget.get("records") if isinstance(budget, Mapping) else None
    if (not isinstance(budget, Mapping) or budget.get("status") != "PASS"
            or not isinstance(records, list) or not records
            or any(not isinstance(row, Mapping) or row.get("status") != "PASS"
                   or row.get("allowed") is not True
                   or row.get("solution_axes_verification_scope") != "numeric_singleton_count_only"
                   or row.get("solution_axes_pair_mapping_complete") is not False
                   for row in records)):
        raise ScienceProtocolError(
            "production point readback lacks a passed pre-getData numeric budget bound "
            "that preserves incomplete outer/inner mapping")
    if result.get("field_array") is not None:
        raise ScienceProtocolError(
            "production point readback must not manufacture a FieldArray when tuple axes are unavailable")
    feature_readback = result.get("feature_readback")
    if (not isinstance(feature_readback, Mapping)
            or feature_readback.get("data_status") != "VERIFIED"
            or feature_readback.get("data") != dataset_tag):
        raise ScienceProtocolError("production Interp did not verify its native data dataset")
    if expected_requested_units is not None:
        _require_requested_unit_application(feature_readback.get("requested_unit_application"),
                                            expressions, expected_requested_units)
        if result.get("expression_units") != dict(zip(expressions, expected_requested_units)):
            raise ScienceProtocolError("production numerical units differ from actual requested-unit feature witness")
    coordinate_readback = result.get("coordinate_readback")
    if not isinstance(coordinate_readback, Mapping) or coordinate_readback.get("status") != "VERIFIED":
        raise ScienceProtocolError("production Interp coordinate readback is not verified")
    coordinate_unit = coordinate_readback.get("unit")
    unit_to_m = {"m": 1.0, "cm": 1e-2, "mm": 1e-3,
                 "um": 1e-6, "µm": 1e-6, "nm": 1e-9}
    if coordinate_unit not in unit_to_m:
        raise ScienceProtocolError("production coordinate readback has no supported native geometry unit")
    coordinates = coordinate_readback.get("coordinates")
    if (not isinstance(coordinates, list) or not expected_points
            or len(coordinates) != len(expected_points[0])
            or any(not isinstance(row, list) or len(row) != len(expected_points)
                   for row in coordinates)):
        raise ScienceProtocolError("production coordinate readback has an unexpected dimension-major shape")
    expected_dim = len(expected_points[0])
    if expected_dim < 1 or any(len(point) != expected_dim for point in expected_points):
        raise ScienceProtocolError("requested point coordinate rows have inconsistent dimensions")
    coordinates_m: list[list[float]] = [[] for _ in range(expected_dim)]
    for dimension in range(expected_dim):
        for point_index, expected in enumerate(expected_points):
            actual = _as_finite_float(coordinates[dimension][point_index], "native coordinate")
            if not math.isclose(actual, _as_finite_float(expected[dimension], "requested coordinate"),
                                rel_tol=1e-10, abs_tol=1e-12):
                raise ScienceProtocolError("production coordinate readback differs from the requested sample grid")
            coordinates_m[dimension].append(actual * unit_to_m[coordinate_unit])

    if (isinstance(expected_solution_count, bool) or type(expected_solution_count) is not int
            or expected_solution_count != 1):
        raise ScienceProtocolError("this readback route requires exactly one observed native solution number")
    if result.get("point_count") != len(expected_points):
        raise ScienceProtocolError("production result point_count differs from the requested sample count")
    actual_expressions = result.get("expressions")
    values = result.get("values")
    if (not isinstance(expressions, Sequence) or isinstance(expressions, (str, bytes))
            or not expressions or list(actual_expressions or []) != list(expressions)
            or not isinstance(values, list) or len(values) != len(expressions)):
        raise ScienceProtocolError("production complex point values do not match the exact expression list")
    actual_units = result.get("expression_units")
    if expected_expression_units is not None:
        if not isinstance(actual_units, Mapping):
            raise ScienceProtocolError("production complex point readback has no native expression-unit map")
        for expression in expressions:
            allowed = expected_expression_units.get(expression)
            unit = actual_units.get(expression)
            allowed_values = (list(allowed) if isinstance(allowed, Sequence)
                              and not isinstance(allowed, (str, bytes)) else [allowed])
            if (not isinstance(unit, str) or not allowed_values
                    or unit not in allowed_values):
                raise ScienceProtocolError(
                    f"production expression {expression!r} unit {unit!r} is not in the exact frozen unit set {allowed_values!r}")
    raw_rows: list[dict[str, Any]] = []
    for expression, expression_values in zip(expressions, values):
        if (not isinstance(expression, str) or not isinstance(expression_values, list)
                or len(expression_values) != expected_solution_count
                or not isinstance(expression_values[0], list)
                or len(expression_values[0]) != len(expected_points)):
            raise ScienceProtocolError(f"production complex result shape is invalid for {expression!r}")
        row = {"expression": expression, "real": [], "imag": []}
        for point_index, value in enumerate(expression_values[0]):
            if not isinstance(value, Mapping) or not {"real", "imag"} <= set(value):
                raise ScienceProtocolError(f"complex result for {expression!r} point {point_index} lacks real/imag")
            item = _finite_complex_row(value.get("real"), value.get("imag"), label=expression)
            if native_is_complex is False and item.imag != 0.0:
                raise ScienceProtocolError("native real-only scalar contains a nonzero imaginary value")
            row["real"].append(item.real)
            row["imag"].append(item.imag)
        raw_rows.append(row)
    if expected_selection is not None:
        _require_point_selection_application(result, expected_selection)
    normal_sign: int | None = None
    normal_status = "NOT_CAPTURED_BY_POINT_INTERPOLATION"
    if require_x_plane_normal:
        by_name = {row["expression"]: row for row in raw_rows}
        nx_row, ny_row = by_name.get(field_scheme_roles(field_scheme)["nx"]), by_name.get(field_scheme_roles(field_scheme)["ny"])
        if (not isinstance(nx_row, Mapping) or not isinstance(ny_row, Mapping)
                or len(nx_row["real"]) != len(expected_points)
                or len(ny_row["real"]) != len(expected_points)):
            raise ScienceProtocolError("x-plane field sample lacks the exact native nx/ny normal readbacks")
        nx = [_finite_complex_row(real, imag, label="nx")
              for real, imag in zip(nx_row["real"], nx_row["imag"])]
        ny = [_finite_complex_row(real, imag, label="ny")
              for real, imag in zip(ny_row["real"], ny_row["imag"])]
        if (any(abs(abs(value.real) - 1.0) > 1e-10 or abs(value.imag) > 1e-12 for value in nx)
                or any(abs(value.real) > 1e-10 or abs(value.imag) > 1e-12 for value in ny)
                or len({1 if value.real > 0 else -1 for value in nx}) != 1):
            raise ScienceProtocolError("native x-plane normals are not a constant unit x normal")
        normal_sign = 1 if nx[0].real > 0 else -1
        if expected_selection is not None and normal_sign != (1 if expected_selection["tag"]=="selReceiverX8" else -1):
            raise ScienceProtocolError("native normal direction differs from exact physics plane")
        normal_status = "VERIFIED_FROM_NATIVE_NX_NY"
    decoded = {
        "status": "PRODUCTION_NATIVE_COMPLEX_POINT_READBACK",
        "scope": "exact dataset-bound single numeric solution; sample identity/values only, not a native integral or scientific acceptance",
        "operation": "result.at_points",
        "dataset": dataset_tag, "solution": solution_tag,
        "plane": plane_id,
        "sample_count": len(expected_points),
        "complex_readback": True,
        "native_is_complex": native_is_complex,
        "real_only_scalar_route": scalar_route and native_is_complex is False,
        "coordinates_m": coordinates_m,
        "coordinate_geometry_unit": coordinate_unit,
        **({"requested_unit_application": dict(feature_readback["requested_unit_application"])}
           if expected_requested_units is not None else {}),
        "expression_units": dict(result.get("expression_units") or {}),
        "expressions": raw_rows,
        "normal_sign": normal_sign,
        "native_normal_x": normal_sign,
        "normal_sign_source": "native result.at_points nx readback" if require_x_plane_normal else None,
        "normal_status": normal_status,
        "science_acceptance": "NOT_EVALUATED",
    }
    native_default_expressions = [
        expression for expression, unit in zip(expressions, expected_requested_units or ())
        if unit == ""
    ]
    if native_default_expressions:
        decoded["native_default_unit_expressions"] = native_default_expressions
        decoded["native_default_unit_semantics"] = "native default or undefined; not dimensionless evidence"
    return decoded


def _plane_coordinates(raw: Mapping[str, Any], *, expected_x_m: float, field_scheme: str = "legacy") -> tuple[list[float], int, int]:
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
    nx, ny = normals.get(field_scheme_roles(field_scheme)["nx"]), normals.get(field_scheme_roles(field_scheme)["ny"])
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


def independent_integrals(output_raw: Mapping[str, Any], input_raw: Mapping[str, Any], *, field_scheme: str = "legacy") -> dict[str, Any]:
    """Recompute all four overlap integrals from COMSOL-exported E/H arrays."""
    _require_field_matrix(output_raw, field_scheme, "output_x8")
    _require_field_matrix(input_raw, field_scheme, "input_xminus10")
    if output_raw.get("plane") != "output_x8" or input_raw.get("plane") != "input_xminus10":
        raise ScienceProtocolError("native raw exports are not bound to x=8 um and x=-10 um planes")
    y_out, native_out_nx, declared_out_sign = _plane_coordinates(output_raw, expected_x_m=8e-6, field_scheme=field_scheme)
    y_in, native_in_nx, declared_in_sign = _plane_coordinates(input_raw, expected_x_m=-10e-6, field_scheme=field_scheme)
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
    signal_e = field_scheme_roles(field_scheme)["signal_e"]
    signal_h = field_scheme_roles(field_scheme)["signal_h"]
    output_mode_e = field_scheme_roles(field_scheme)["mode_e"]["2"]
    output_mode_h = field_scheme_roles(field_scheme)["mode_h"]["2"]
    incident_mode_e = field_scheme_roles(field_scheme)["mode_e"]["1"]
    incident_mode_h = field_scheme_roles(field_scheme)["mode_h"]["1"]
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
        "integration": f"composite Simpson, registered {output_raw['sample_count']}-point grid",
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


def independent_capture_aperture_flux(output_raw: Mapping[str, Any], *, field_scheme: str = "legacy") -> dict[str, Any]:
    """Integrate signed native E/H flux over the frozen core-only x=8 aperture."""
    _require_field_matrix(output_raw, field_scheme, "output_x8")
    if output_raw.get("plane") != "output_x8" or output_raw.get("sample_count") != REFINED_SAMPLE_COUNT:
        raise ScienceProtocolError(f"capture quadrature requires the {REFINED_SAMPLE_COUNT}-point native x=8 output field export")
    y, native_nx, declared_sign = _plane_coordinates(output_raw, expected_x_m=8e-6, field_scheme=field_scheme)
    if native_nx * declared_sign != 1:
        raise ScienceProtocolError("capture aperture normal does not follow the registered physical +x convention")
    selected = [index for index, coordinate in enumerate(y)
                if CAPTURE_Y_MIN_M - 1e-12 <= coordinate <= CAPTURE_Y_MAX_M + 1e-12]
    if len(selected) < 3 or len(selected) % 2 != 1:
        raise ScienceProtocolError("registered receiver-core capture aperture has no odd Simpson subgrid")
    selected_y = [y[index] for index in selected]
    if (not math.isclose(selected_y[0], CAPTURE_Y_MIN_M, rel_tol=0, abs_tol=1e-12)
            or not math.isclose(selected_y[-1], CAPTURE_Y_MAX_M, rel_tol=0, abs_tol=1e-12)):
        raise ScienceProtocolError(f"native {REFINED_SAMPLE_COUNT}-point grid does not exactly cover the frozen core capture bounds")
    fields = _raw_expression_map(output_raw)
    required = tuple(field_scheme_roles(field_scheme)[kind][axis] for kind in ("signal_e","signal_h") for axis in "yz")
    if any(name not in fields for name in required):
        raise ScienceProtocolError("capture quadrature is missing native signal E/H components")
    electric = field_scheme_roles(field_scheme)["signal_e"]
    magnetic = field_scheme_roles(field_scheme)["signal_h"]
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
        raise ScienceProtocolError(f"independent incident-power receipt is not the registered {REFINED_SAMPLE_COUNT}-point native-field recomputation")
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
                                  tolerance: float = INTEGRAL_RELATIVE_TOLERANCE,
                                  sample_counts: tuple[int, int] = (SAMPLE_COUNT, REFINED_SAMPLE_COUNT)) -> dict[str, Any]:
    """Require one of the two registered 321/641 or saved-MPH 641/1281 pairs."""
    if sample_counts not in {(SAMPLE_COUNT, REFINED_SAMPLE_COUNT), SAVED_MPH_QUADRATURE_SAMPLE_COUNTS}:
        raise ScienceProtocolError("quadrature comparison must use the registered full-science or saved-MPH pair")
    coarse_count, fine_count = sample_counts
    if coarse.get("status") != "SOFTWARE_RECOMPUTED_FROM_NATIVE_RAW_FIELDS" or coarse.get("sample_count") != coarse_count:
        raise ScienceProtocolError(f"coarse quadrature receipt is not the registered {coarse_count}-point recomputation")
    if fine.get("status") != "SOFTWARE_RECOMPUTED_FROM_NATIVE_RAW_FIELDS" or fine.get("sample_count") != fine_count:
        raise ScienceProtocolError(f"fine quadrature receipt is not the registered {fine_count}-point recomputation")
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
        raise ScienceProtocolError(f"{coarse_count}-to-{fine_count} quadrature refinement did not meet the frozen tolerance: {errors}")
    return {"status": f"PASS_{coarse_count}_TO_{fine_count}_QUADRATURE_REFINEMENT", "tolerance_relative": tolerance,
            "relative_errors": errors, "coarse_points": coarse_count,
            "fine_points": fine_count}


def software_global_phase_control(output_raw: Mapping[str, Any],
                                  input_raw: Mapping[str, Any], *,
                                  phase_factor: complex = 1j, field_scheme: str = "legacy") -> dict[str, Any]:
    """Test a known signal-only phase rotation on exported native fields.

    This is explicitly a software recomputation control. It does not replace
    changing the COMSOL input Port phase and rerunning the native study.
    """
    if not isinstance(phase_factor, complex) or abs(abs(phase_factor) - 1.0) > 1e-12:
        raise ScienceProtocolError("software global-phase control requires a unit-magnitude complex factor")
    base = independent_integrals(output_raw, input_raw, field_scheme=field_scheme)
    fields = _raw_expression_map(output_raw)
    signal_names = set(field_scheme_roles(field_scheme)["signal_e"].values()) | set(field_scheme_roles(field_scheme)["signal_h"].values())
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
    changed = independent_integrals(rotated, input_raw, field_scheme=field_scheme)
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
                             field_scheme: str = "legacy",
                             expected_factor: complex = EXPECTED_FIELD_PHASE_FACTOR) -> dict[str, Any]:
    """Require input phase change to rotate signal while output reference stays fixed."""
    field_scheme_roles(field_scheme)
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
    _require_field_matrix(raw0, field_scheme, "output_x8")
    _require_field_matrix(raw90, field_scheme, "output_x8")
    y0, nx0, sign0 = _plane_coordinates(raw0, expected_x_m=8e-6, field_scheme=field_scheme)
    y90, nx90, sign90 = _plane_coordinates(raw90, expected_x_m=8e-6, field_scheme=field_scheme)
    if (len(y0) != len(y90) or any(not math.isclose(a, b, rel_tol=0, abs_tol=1e-12)
                                    for a, b in zip(y0, y90))
            or nx0 != nx90 or sign0 != sign90):
        raise ScienceProtocolError("native phase pair output fields are not bound to the same coordinates and oriented plane")
    values0, values90 = _raw_expression_map(raw0), _raw_expression_map(raw90)
    signal_names = list(field_scheme_roles(field_scheme)["signal_e"].values()) + list(field_scheme_roles(field_scheme)["signal_h"].values())
    mode_names = list(field_scheme_roles(field_scheme)["mode_e"]["2"].values()) + list(field_scheme_roles(field_scheme)["mode_h"]["2"].values())
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
                       "fields": {"electric": field_scheme_roles("legacy")["signal_e"], "magnetic": field_scheme_roles("legacy")["signal_h"]}},
            "reference_mode": {"mode_id": "output_mode_1", "mode_axis_parameter": "modeIndex",
                               "source": {"dataset_id": "dset1", "solution_id": "sol1", "outer_index": 1, "inner_index": 1},
                               "fields": {"electric": field_scheme_roles("legacy")["mode_e"]["2"], "magnetic": field_scheme_roles("legacy")["mode_h"]["2"]}},
            "incident_reference": {"reference_id": "input_mode_1", "input_plane_id": "xminus10",
                                   "selection": {"component": "comp1", "geometry": "geom1", "tag": "selInputPort"}, "normal_sign": -1,
                                   "source": {"dataset_id": "dset1", "solution_id": "sol1", "outer_index": 1, "inner_index": 1},
                                   "fields": {"electric": field_scheme_roles("legacy")["mode_e"]["1"], "magnetic": field_scheme_roles("legacy")["mode_h"]["1"]}},
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
