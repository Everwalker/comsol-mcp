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


def _finite_complex_row(real: Any, imag: Any, *, label: str) -> complex:
    return complex(_as_finite_float(real, f"{label}.real"),
                   _as_finite_float(imag, f"{label}.imag"))


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
    if len({str(row["PortName"]) for row in bma_steps}) != len(bma_steps):
        raise ScienceProtocolError("BMA steps do not bind unique native PortName properties")

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


def decode_production_complex_point_readback(
    result: Mapping[str, Any], *, dataset_tag: str, solution_tag: str,
    expressions: Sequence[str], expected_points: Sequence[Sequence[float]],
    expected_solution_count: int, plane_id: str,
) -> dict[str, Any]:
    """Decode one exact stored solution through the public result.at_points path."""
    if not isinstance(result, Mapping):
        raise ScienceProtocolError("production result.at_points response must be an object")
    status = result.get("status")
    cleanup = result.get("cleanup")
    if (not isinstance(status, Mapping) or status.get("ok") is not True
            or not isinstance(cleanup, Mapping) or cleanup.get("cleanup_failed") is True
            or result.get("complex_mode") != "preserve"
            or result.get("is_complex") is not True):
        raise ScienceProtocolError("production result.at_points did not return a clean complex-preserve result")
    if result.get("dataset") != dataset_tag or result.get("solution") != solution_tag:
        raise ScienceProtocolError("production result.at_points did not retain the exact dataset/solution identity")
    binding = result.get("dataset_binding")
    if (not isinstance(binding, Mapping) or binding.get("binding_complete") is not True
            or binding.get("solution") != solution_tag):
        raise ScienceProtocolError("production result.at_points lacks exact native dataset.solution binding")
    axes = result.get("solution_axes")
    count_observation = axes.get("numeric_count_observation") if isinstance(axes, Mapping) else None
    if (not isinstance(axes, Mapping)
            or axes.get("pair_mapping_complete") is not False
            or not isinstance(count_observation, Mapping)
            or count_observation.get("status") != "VERIFIED_SINGLE_STORED_NUMERIC_COUNT"
            or count_observation.get("numeric_solution_count") != 1
            or count_observation.get("dataset") != dataset_tag
            or count_observation.get("solution") != solution_tag
            or not isinstance(count_observation.get("source"), str)
            or not count_observation.get("source")):
        raise ScienceProtocolError(
            "production point readback lacks the exact stored-solution numeric singleton witness; "
            "outer/inner SolutionInfo mapping must remain explicitly incomplete")
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
        raise ScienceProtocolError("this readback route requires exactly one observed stored solution number")
    if result.get("point_count") != len(expected_points):
        raise ScienceProtocolError("production result point_count differs from the requested sample count")
    actual_expressions = result.get("expressions")
    values = result.get("values")
    if (not isinstance(expressions, Sequence) or isinstance(expressions, (str, bytes))
            or not expressions or list(actual_expressions or []) != list(expressions)
            or not isinstance(values, list) or len(values) != len(expressions)):
        raise ScienceProtocolError("production complex point values do not match the exact expression list")
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
            row["real"].append(item.real)
            row["imag"].append(item.imag)
        raw_rows.append(row)
    return {
        "status": "PRODUCTION_NATIVE_COMPLEX_POINT_READBACK",
        "scope": "exact dataset-bound single stored solution; sample identity/values only, not a native integral or scientific acceptance",
        "operation": "result.at_points",
        "dataset": dataset_tag, "solution": solution_tag,
        "plane": plane_id,
        "sample_count": len(expected_points),
        "complex_readback": True,
        "coordinates_m": coordinates_m,
        "coordinate_geometry_unit": coordinate_unit,
        "expression_units": dict(result.get("expression_units") or {}),
        "expressions": raw_rows,
        "normal_sign": None,
        "normal_status": "NOT_CAPTURED_BY_POINT_INTERPOLATION",
        "science_acceptance": "NOT_EVALUATED",
    }


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
