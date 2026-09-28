"""Managed, Worker-backed W21 stage output readback.

The stage output reader deliberately uses the existing G3 result operations and
managed execution service.  It does not turn a requested SolutionSpec, unit,
frame, or caller-provided array into engine evidence.  Result-evaluation
features are transient model nodes, so their service tickets and revision
changes are returned as an explicit chain for the stage daemon to verify.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from ._execution_contract import ExecutionContractError
from ._stage_contract import sha256_json

OUTPUT_CONTRACT = "w21-stage-output-readback/v1"
_SOLUTION_INFO_SOURCE = "SolutionInfo.getSolnum(outer, strict)"
_MAX_FIELD_SCALARS = 65_536
_MAX_FIELD_JSON_BYTES = 8 * 1024 * 1024


class _EvidenceUnavailable(Exception):
    """A deterministic gap in a requested readback, not an engine failure."""


def _finite(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, ValueError):
        return False


def _numeric_scalars(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return 1
    if isinstance(value, Mapping):
        return sum(_numeric_scalars(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return sum(_numeric_scalars(item) for item in value)
    return 0


def _plain_json(value: Any) -> Any:
    """Detach JSON-shaped evidence without accepting nonfinite values."""
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_plain_json(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise _EvidenceUnavailable("nonfinite numeric evidence")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise _EvidenceUnavailable("result payload is not finite JSON evidence")


def _unverified(binding: Mapping[str, Any], stage_run_operation_id: str, solve_revision: int,
                target_selection: Mapping[str, Any], reason: str, *,
                revision_chain: list[dict[str, Any]] | None = None,
                output_tuple: Mapping[str, Any] | None = None,
                solution_binding: Mapping[str, Any] | None = None,
                checks: list[dict[str, Any]] | None = None,
                evidence_refs: list[dict[str, Any]] | None = None,
                output_revision: int | None = None) -> dict[str, Any]:
    return {
        "contract": OUTPUT_CONTRACT,
        "producer": "managed-backend-stage-output-readback",
        "status": "UNVERIFIED",
        "binding": dict(binding),
        "stage_run_operation_id": stage_run_operation_id,
        "solve_revision": solve_revision,
        "model_revision": solve_revision,
        "output_revision": output_revision if type(output_revision) is int else solve_revision,
        "target_selection_sha256": sha256_json(dict(target_selection)),
        "output_tuple": dict(output_tuple) if isinstance(output_tuple, Mapping) else None,
        "solution_binding": dict(solution_binding) if isinstance(solution_binding, Mapping) else None,
        "checks": list(checks or []),
        "evidence_refs": list(evidence_refs or []),
        "revision_chain": list(revision_chain or []),
        "missing": [reason],
    }


def _check_tuple(solution_spec: Mapping[str, Any], raw: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve one DSL SolutionSpec against an actual SolutionInfo pair table."""
    dataset = solution_spec.get("dataset")
    if not isinstance(dataset, str) or raw.get("dataset") != dataset:
        raise _EvidenceUnavailable("dataset identity did not match the requested SolutionSpec")
    solution = raw.get("solution")
    if not isinstance(solution, str) or not solution:
        raise _EvidenceUnavailable("dataset did not resolve one actual SolutionInfo sequence")
    if solution_spec.get("solution") is not None and solution_spec.get("solution") != solution:
        raise _EvidenceUnavailable("requested solution tag differs from the actual dataset binding")
    pairs = raw.get("solnum_pairs")
    if (raw.get("binding_complete") is not True or raw.get("pair_mapping_complete") is not True
            or _SOLUTION_INFO_SOURCE not in str(raw.get("binding_source", ""))
            or not isinstance(pairs, list) or not pairs):
        raise _EvidenceUnavailable("complete SolutionInfo.getSolnum pair mapping is unavailable")
    pair_rows: list[dict[str, int]] = []
    seen: set[tuple[int, int]] = set()
    by_outer: dict[int, list[int]] = {}
    for row in pairs:
        if not isinstance(row, Mapping):
            raise _EvidenceUnavailable("SolutionInfo pair row is malformed")
        outer, inner, solnum = row.get("outer"), row.get("inner"), row.get("solnum")
        if any(type(value) is not int or value < 1 for value in (outer, inner, solnum)):
            raise _EvidenceUnavailable("SolutionInfo pair has an invalid index")
        key = (outer, inner)
        if key in seen:
            raise _EvidenceUnavailable("SolutionInfo pair mapping contains duplicate tuples")
        seen.add(key)
        by_outer.setdefault(outer, []).append(inner)
        pair_rows.append({"outer": outer, "inner": inner, "solnum": solnum})

    outer_labels = raw.get("outer_indices")
    if not isinstance(outer_labels, list) or outer_labels != list(by_outer):
        raise _EvidenceUnavailable("outer axis does not match the actual SolutionInfo pair sequence")
    inner_labels = raw.get("inner_indices")
    expected_inner_labels = (list(next(iter(by_outer.values())))
                             if by_outer and all(values == next(iter(by_outer.values())) for values in by_outer.values())
                             else sorted({value for values in by_outer.values() for value in values}))
    if inner_labels != expected_inner_labels:
        raise _EvidenceUnavailable("inner axis does not match the actual SolutionInfo pair sequence")

    parameters = raw.get("parameters")
    parameter_rows = parameters.get("by_pair") if isinstance(parameters, Mapping) else None
    requested_filters = any(solution_spec.get(name) is not None for name in ("time", "frequency", "parameters"))
    if requested_filters and (raw.get("parameters_complete") is not True
                              or not isinstance(raw.get("parameters_source"), str)
                              or not all(method in raw["parameters_source"] for method in
                                         ("SolutionInfo.getPNames", "getPvals", "getUnits"))
                              or not isinstance(parameter_rows, Mapping)):
        raise _EvidenceUnavailable("complete native SolutionInfo parameter values and units are unavailable")

    filters: dict[str, Any] = {}
    candidates = list(pair_rows)
    outer_selector = solution_spec.get("outer")
    if isinstance(outer_selector, list):
        if len(outer_selector) != 1:
            raise _EvidenceUnavailable("output SolutionSpec must select one outer index")
        candidates = [row for row in candidates if row["outer"] == outer_selector[0]]
    elif outer_selector in {"first", "last"}:
        if not outer_labels:
            raise _EvidenceUnavailable("actual outer axis is empty")
        selected = outer_labels[0] if outer_selector == "first" else outer_labels[-1]
        candidates = [row for row in candidates if row["outer"] == selected]
    elif outer_selector == "all":
        raise _EvidenceUnavailable("output SolutionSpec cannot resolve outer=all to one tuple")
    elif outer_selector is not None:
        raise _EvidenceUnavailable("output SolutionSpec outer selector is unsupported")

    # Per-outer first/last semantics follow the actual sorted inner labels used
    # by the frozen output validator; ambiguity is rejected below.
    inner_selector = solution_spec.get("inner")
    if isinstance(inner_selector, list):
        if len(inner_selector) != 1:
            raise _EvidenceUnavailable("output SolutionSpec must select one inner index")
        candidates = [row for row in candidates if row["inner"] == inner_selector[0]]
    elif inner_selector in {"first", "last"}:
        candidates = [row for row in candidates if row["inner"] == (
            min(by_outer[row["outer"]]) if inner_selector == "first" else max(by_outer[row["outer"]])
        )]
    elif inner_selector == "all":
        raise _EvidenceUnavailable("output SolutionSpec cannot resolve inner=all to one tuple")
    elif inner_selector is not None:
        raise _EvidenceUnavailable("output SolutionSpec inner selector is unsupported")

    def param_row(pair: Mapping[str, int]) -> Mapping[str, Any]:
        row = parameter_rows.get(f"{pair['outer']}:{pair['inner']}") if isinstance(parameter_rows, Mapping) else None
        if (not isinstance(row, Mapping) or row.get("solnum") != pair["solnum"]
                or not isinstance(row.get("names"), list)
                or not isinstance(row.get("values"), list)
                or not isinstance(row.get("units"), list)
                or not (len(row["names"]) == len(row["values"]) == len(row["units"]))):
            raise _EvidenceUnavailable("SolutionInfo parameter row is incomplete for an exact tuple")
        return row

    for axis, aliases in (("time", {"time", "t"}), ("frequency", {"frequency", "freq"})):
        requested = solution_spec.get(axis)
        if requested is None:
            continue
        if not isinstance(requested, list) or len(requested) != 1:
            raise _EvidenceUnavailable(f"output SolutionSpec {axis} must select one exact quantity")
        quantity = requested[0]
        if not isinstance(quantity, Mapping) or not _finite(quantity.get("value")) or not isinstance(quantity.get("unit"), str):
            raise _EvidenceUnavailable(f"output SolutionSpec {axis} quantity is malformed")
        matches: list[tuple[dict[str, int], str, float, str]] = []
        for pair in candidates:
            row = param_row(pair)
            indices = [index for index, name in enumerate(row["names"])
                       if isinstance(name, str) and name.casefold() in aliases]
            if len(indices) != 1:
                continue
            index = indices[0]
            value, unit = row["values"][index], row["units"][index]
            if (_finite(value) and unit == quantity["unit"]
                    and math.isclose(float(value), float(quantity["value"]), rel_tol=1e-12, abs_tol=1e-15)):
                matches.append((pair, row["names"][index], float(value), unit))
        if not matches or len({item[1] for item in matches}) != 1 or len({item[3] for item in matches}) != 1:
            raise _EvidenceUnavailable(f"native SolutionInfo {axis} values/units do not resolve the requested quantity")
        parameter_name = matches[0][1]
        filters[axis] = {"status": "VERIFIED", "parameter_name": parameter_name,
                         "value": matches[0][2], "unit": matches[0][3],
                         "source": raw["parameters_source"]}
        candidates = [item[0] for item in matches]

    requested_parameters = solution_spec.get("parameters")
    if requested_parameters is not None:
        if not isinstance(requested_parameters, Mapping):
            raise _EvidenceUnavailable("output SolutionSpec parameter selector is malformed")
        matched = []
        for pair in candidates:
            row = param_row(pair)
            ok = True
            for name, expected in requested_parameters.items():
                if name not in row["names"]:
                    ok = False
                    break
                index = row["names"].index(name)
                expected_value = expected.get("value") if isinstance(expected, Mapping) else expected
                expected_unit = expected.get("unit") if isinstance(expected, Mapping) else None
                if (row["values"][index] != expected_value
                        or (expected_unit is not None and row["units"][index] != expected_unit)):
                    ok = False
                    break
            if ok:
                matched.append(pair)
        candidates = matched
    if len(candidates) != 1:
        raise _EvidenceUnavailable("SolutionSpec did not resolve exactly one actual outer/inner/solnum tuple")
    pair = candidates[0]
    normalized_binding = dict(raw)
    normalized_binding["outer_indices"] = list(by_outer)
    normalized_binding["inner_indices"] = expected_inner_labels
    normalized_binding["inner_indices_by_outer"] = {key: sorted(set(values)) for key, values in by_outer.items()}
    normalized_binding["selection_resolution"] = {
        "status": "VERIFIED",
        "target_selection_sha256": sha256_json(dict(solution_spec)),
        "resolved_pair": dict(pair),
        "filters": filters,
        "source": "managed dataset.solution_indices readback; SolutionInfo.getSolnum(outer, strict)",
    }
    return ({"dataset": dataset, "solution": solution, **pair}, normalized_binding)


def _field_payload(evaluation: Mapping[str, Any], expression: str) -> tuple[dict[str, Any], dict[str, Any]]:
    evidence = evaluation.get("strict_field_readback")
    if not isinstance(evidence, Mapping) or evidence.get("status") != "VERIFIED":
        raise _EvidenceUnavailable("strict native field/coordinate readback is incomplete")
    field = evidence.get("field_array")
    coordinates = evidence.get("coordinates")
    if not isinstance(field, Mapping) or not isinstance(coordinates, Mapping):
        raise _EvidenceUnavailable("strict field or native coordinate payload is missing")
    values = field.get("values")
    coords = coordinates.get("values")
    shape = field.get("shape")
    if (not isinstance(shape, list) or len(shape) != 4 or shape[:3] != [1, 1, 1]
            or not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], list)
            or len(values[0]) != 1 or not isinstance(values[0][0], list)
            or len(values[0][0]) != 1 or not isinstance(values[0][0][0], list)
            or len(values[0][0][0]) != shape[3] or not isinstance(coords, list)
            or any(not isinstance(axis, list) or len(axis) != shape[3] for axis in coords)):
        raise _EvidenceUnavailable("strict field/coordinate shapes do not form one complete selected tuple")
    scalars = _numeric_scalars(values) + _numeric_scalars(coords)
    if scalars > _MAX_FIELD_SCALARS:
        raise _EvidenceUnavailable("strict field/coordinate scalar cap was exceeded")
    try:
        payload_bytes = len(json.dumps({"values": values, "coordinates": coords}, allow_nan=False,
                                       separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise _EvidenceUnavailable("strict field/coordinate payload contains nonfinite data") from exc
    if payload_bytes > _MAX_FIELD_JSON_BYTES:
        raise _EvidenceUnavailable("strict field/coordinate JSON cap was exceeded")
    real_values: list[Any] = []
    for item in values[0][0][0]:
        if isinstance(item, Mapping):
            real_part, imaginary_part = item.get("real"), item.get("imag")
            if not _finite(real_part) or not _finite(imaginary_part):
                raise _EvidenceUnavailable("pointwise complex field contains a nonfinite or nonnumeric component")
            real_values.append({"real": float(real_part), "imag": float(imaginary_part)})
        elif _finite(item):
            real_values.append(float(item))
        else:
            raise _EvidenceUnavailable("pointwise field contains a nonfinite or nonnumeric value")
    coord_rows: list[list[float]] = []
    for row in coords:
        normalized = []
        for item in row:
            if not _finite(item):
                raise _EvidenceUnavailable("native coordinates contain a nonfinite value")
            normalized.append(float(item))
        coord_rows.append(normalized)
    unit_values = evidence.get("expression_unit_readback", {}).get("values", {})
    unit = unit_values.get(expression) if isinstance(unit_values, Mapping) else None
    field_evidence = _plain_json(dict(evidence))
    intrinsic_units = evidence.get("intrinsic_unit_readback")
    unit_evidence = intrinsic_units.get(expression) if isinstance(intrinsic_units, Mapping) else None
    frame_evidence = coordinates
    return ({"values": real_values, "coordinates": coord_rows, "unit_readback": unit,
             "unit_evidence": _plain_json(dict(unit_evidence)) if isinstance(unit_evidence, Mapping) else None,
             "configured_unit_evidence": _plain_json(dict(evidence.get("expression_unit_readback")))
                 if isinstance(evidence.get("expression_unit_readback"), Mapping) else None,
             "coordinate_frame": coordinates.get("coordinate_frame"),
             "coordinate_frame_evidence": _plain_json(dict(frame_evidence)),
             "selection_readback": _plain_json(evidence.get("selection_readback")),
             "shape": list(shape)}, field_evidence)


def _selection_readback_matches(value: Any, requested: Mapping[str, Any], *, role: str = "primary") -> bool:
    """Bind numerical feature evidence to the frozen component selection."""
    if not isinstance(value, Mapping):
        return False
    if value.get("role") != role or value.get("source") != "actual_transient_numerical_feature":
        return False
    if (value.get("component") != requested.get("component")
            or value.get("geometry") != requested.get("geometry")
            or value.get("entity_dimension") != requested.get("entity_dimension")
            or value.get("kind") != requested.get("kind")):
        return False
    entities = value.get("entities")
    if (not isinstance(entities, list) or not entities
            or any(type(entity) is not int or entity < 1 for entity in entities)):
        return False
    kind = requested.get("kind")
    if kind == "named" and value.get("tag") != requested.get("tag"):
        return False
    if kind == "explicit":
        expected = requested.get("entities")
        if (not isinstance(expected, list) or any(type(entity) is not int or entity < 1 for entity in expected)
                or sorted(entities) != sorted(expected)):
            return False
    native = value.get("native_selection_readback")
    return (isinstance(native, Mapping)
            and native.get("geometry") == requested.get("geometry")
            and native.get("dimension") == requested.get("entity_dimension")
            and native.get("entities") == entities
            and native.get("is_inheriting") is not True)


def _integral_value(evaluation: Mapping[str, Any], expression: str, pair: Mapping[str, Any],
                    selection: Mapping[str, Any]) -> tuple[float, Any, dict[str, Any]]:
    if not isinstance(evaluation.get("strict_metric_evidence"), Mapping):
        raise _EvidenceUnavailable("native integration feature selection/solution evidence is missing")
    fa = evaluation.get("field_array")
    if not isinstance(fa, Mapping) or not isinstance(fa.get("values"), list):
        raise _EvidenceUnavailable("native integral result field array is missing")
    values = fa["values"]
    # Aggregate result must be a scalar for exactly the selected tuple. Accept
    # only singleton axes, not a first-item fallback over a larger payload.
    def singleton(value: Any) -> Any:
        while isinstance(value, list):
            if len(value) != 1:
                raise _EvidenceUnavailable("native integral result is not a singleton exact tuple")
            value = value[0]
        return value
    scalar = singleton(values)
    if not _finite(scalar):
        raise _EvidenceUnavailable("native integral result is nonfinite or complex")
    metric = evaluation["strict_metric_evidence"]
    actual_pairs = metric.get("selected_solution_pairs")
    if not isinstance(actual_pairs, list) or actual_pairs != [
        {"outer": pair["outer"], "inner": pair["inner"], "solnum": pair["solnum"]}
    ]:
        raise _EvidenceUnavailable("native integral did not read back exactly the selected solution tuple")
    selections = metric.get("selection_features")
    primary = [item for item in selections if isinstance(item, Mapping) and item.get("role") == "primary"] \
        if isinstance(selections, list) else []
    if len(primary) != 1 or not _selection_readback_matches(primary[0], selection):
        raise _EvidenceUnavailable("native integral numerical feature selection was not read back")
    unit_values = metric.get("expression_unit_readback")
    unit = unit_values.get(expression) if isinstance(unit_values, Mapping) else None
    return float(scalar), unit, _plain_json(dict(metric))


def _coordinate_pairs_equal(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return (left.get("coordinates") == right.get("coordinates")
            and left.get("shape") == right.get("shape"))


def _intrinsic_unit_matches(payload: Mapping[str, Any], expected_unit: Any) -> bool:
    evidence = payload.get("unit_evidence")
    if not isinstance(evidence, Mapping) or evidence.get("status") != "VERIFIED":
        return False
    return (evidence.get("intrinsic_unit") == expected_unit
            and evidence.get("field_dimensionality") == "VERIFIED")


def _coordinate_frame_matches(payload: Mapping[str, Any], expected_frame: Any) -> bool:
    evidence = payload.get("coordinate_frame_evidence")
    return (isinstance(evidence, Mapping)
            and evidence.get("coordinate_frame_status") == "VERIFIED"
            and evidence.get("coordinate_frame") == expected_frame)


def _max_abs_pairwise_error(source: Sequence[float], target: Sequence[float]) -> tuple[float, float]:
    if not source or len(source) != len(target):
        raise _EvidenceUnavailable("source and target pointwise field shapes do not match")

    def scalar(value: Any) -> complex:
        if isinstance(value, Mapping):
            real, imaginary = value.get("real"), value.get("imag")
            if not _finite(real) or not _finite(imaginary):
                raise _EvidenceUnavailable("pointwise complex field contains a nonfinite component")
            return complex(float(real), float(imaginary))
        if not _finite(value):
            raise _EvidenceUnavailable("pointwise field contains a nonfinite value")
        return complex(float(value), 0.0)

    pairs = [(scalar(a), scalar(b)) for a, b in zip(source, target)]
    errors = [abs(a - b) for a, b in pairs]
    scales = [max(abs(a), abs(b)) for a, b in pairs]
    if any(not math.isfinite(value) for value in errors + scales):
        raise _EvidenceUnavailable("pointwise error or reference scale overflowed")
    return max(errors), max(scales)


def _artifact_ref(backend: Any, payload: Mapping[str, Any], *, binding: Mapping[str, Any],
                  stage_run_operation_id: str, model_revision: int, child_operation_id: str,
                  managed_operation_id: str | None, request_id: str, request_hash: str | None,
                  revision_witness: str, worker_requests: Sequence[Mapping[str, Any]], readphase: str,
                  check_definition_sha256: str, selection_sha256: str,
                  tuple_binding: Mapping[str, Any]) -> dict[str, Any]:
    from ._artifact_store import ArtifactStore, trusted_project_root

    identity = uuid4().hex
    name = f"w21_stage_readbacks/{identity}.json"
    exported = ArtifactStore(trusted_project_root(backend.worker)).export_field_data(
        name,
        {"values": _plain_json(dict(payload)), "status": {"ok": True},
         "provenance": {
             "binding": dict(binding), "stage_run_operation_id": stage_run_operation_id,
             "model_revision": model_revision, "child_operation_id": child_operation_id,
             "managed_operation_id": managed_operation_id,
             "request_id": request_id, "request_hash": request_hash, "readphase": readphase,
             "revision_witness": revision_witness,
             "worker_requests": [dict(row) for row in worker_requests],
             "check_definition_sha256": check_definition_sha256,
             "selection_sha256": selection_sha256, "tuple_binding": dict(tuple_binding),
         }},
        "json",
    )
    record = {
        "schema_version": 1, "kind": "w21_stage_output_readback",
        "project_id": binding["project_id"], "model_ref": dict(binding["model_ref"]),
        "stage_attempt_id": binding["attempt_id"], "stage_run_operation_id": stage_run_operation_id,
        "model_revision": model_revision, "child_operation_id": child_operation_id,
        "managed_operation_id": managed_operation_id,
        "request_id": request_id, "request_hash": request_hash, "readphase": readphase,
        "revision_witness": revision_witness,
        "worker_requests": [dict(row) for row in worker_requests],
        "check_definition_sha256": check_definition_sha256,
        "selection_sha256": selection_sha256, "tuple_binding": dict(tuple_binding),
        "artifact": dict(exported), "sha256": exported["sha256"],
    }
    key = f"w21-stage-output-readback:{identity}"
    backend.store.persist_artifact(key, record)
    return {"artifact_id": key, "file_path": exported.get("file_path"),
            "sha256": exported["sha256"], "byte_size": exported.get("byte_size"),
            "readphase": readphase, "child_operation_id": child_operation_id,
            "managed_operation_id": managed_operation_id,
            "request_id": request_id, "request_hash": request_hash,
            "revision_witness": revision_witness,
            "worker_requests": [dict(row) for row in worker_requests],
            "observation_revision": model_revision}


def produce_stage_output_readback(
    backend: Any, *, binding: Mapping[str, Any], stage: Mapping[str, Any], plan: Mapping[str, Any],
    stage_run_operation_id: str, model_revision: int, solve_result: Mapping[str, Any],
    event_callback: Any = None, authorize_callback: Any = None,
) -> dict[str, Any]:
    """Read actual result fields/integrals through managed G3 service tickets."""
    from ._managed_backend import _G3_EFFECT_MAP

    if (not isinstance(binding, Mapping) or not isinstance(stage, Mapping)
            or not isinstance(plan, Mapping) or not isinstance(solve_result, Mapping)):
        raise ExecutionContractError("INVALID_REQUEST", "stage output readback identity is malformed", stage="validation")
    model_ref = binding.get("model_ref")
    project_id = binding.get("project_id")
    if (not isinstance(model_ref, Mapping) or not isinstance(project_id, str)
            or not isinstance(stage_run_operation_id, str) or not stage_run_operation_id
            or type(model_revision) is not int):
        raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "stage output readback lacks exact run identity", stage="validation")
    if backend.service is None or backend.worker is None:
        raise ExecutionContractError("ENGINE_UNRESPONSIVE", "managed Worker/service is unavailable", stage="validation")
    ref = dict(model_ref)
    # ModelRef construction is kept on the existing parser to avoid accepting
    # any caller-defined representation of the managed ledger key.
    from ._execution_contract import model_ref_from_mapping
    ledger_ref = model_ref_from_mapping(ref)
    state = backend.service.ledger._state_for(ledger_ref)
    if state.dirty or state.revision != model_revision:
        raise ExecutionContractError("REVISION_CONFLICT", "stage output readback does not start at the exact clean solve revision", stage="validation")
    if backend.model_project_binding(ref) != {"attribution": "PROJECT_BOUND", "project_id": project_id}:
        raise ExecutionContractError("PROJECT_IDENTITY_MISMATCH", "stage output ModelRef is not bound to the same project", stage="validation")

    target_selection = stage.get("target_selection")
    if not isinstance(target_selection, Mapping):
        raise ExecutionContractError("INVALID_REQUEST", "stage target SolutionSpec is unavailable", stage="validation")
    checks = stage.get("checks")
    if not isinstance(checks, list):
        raise ExecutionContractError("INVALID_REQUEST", "stage checks are malformed", stage="validation")
    current_revision = model_revision
    operation_index = 0
    chain: list[dict[str, Any]] = []
    all_events: list[dict[str, Any]] = []
    evidence_refs: list[dict[str, Any]] = []

    def managed_call(operation: str, arguments: Mapping[str, Any], *, readphase: str,
                     strict_mode: str | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        nonlocal current_revision, operation_index
        operation_index += 1
        child_operation_id = f"w21-output-{uuid4().hex}"
        child_request_id = f"w21-output-request-{uuid4().hex}"
        child_execution = {
            "project_id": project_id, "session_id": ref.get("session_id"), "model_ref": ref,
            "expected_revision": current_revision, "request_id": child_request_id,
            "idempotency_key": f"{binding['attempt_id']}:output:{operation_index}",
        }
        if callable(authorize_callback):
            authorize_callback(operation, dict(arguments), child_execution)
        events: list[dict[str, Any]] = []
        worker = getattr(backend, "worker", None)
        worker_generation = getattr(worker, "generation", None)
        if callable(worker_generation):
            worker_generation = worker_generation()
        if type(worker_generation) is not int or worker_generation < 1:
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN",
                "stage output Worker epoch is unavailable or malformed",
                stage="pre_dispatch",
            )

        def capture(event: Any) -> None:
            if not isinstance(event, Mapping):
                raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "Worker output event is malformed", stage="post_dispatch")
            copied = dict(event)
            # Preserve the Worker's original command kind and RPC metadata. This
            # sidecar carries the actual managed model binding across command
            # kinds such as model_snapshot and model, which do not have a Java
            # receiver/method pair.
            copied["w21_stage_output_binding"] = {
                "model_ref": dict(ref), "model_tag": ref.get("model_tag"),
                "worker_generation": worker_generation,
                "child_operation_id": child_operation_id,
                "operation": operation, "readphase": readphase,
            }
            events.append(copied)
            all_events.append({"readphase": readphase, "operation": operation, "event": copied})
            if copied.get("operation_id") != child_operation_id:
                raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "Worker output event has a different child operation identity", stage="post_dispatch")
            if callable(event_callback):
                event_callback(copied, readphase=readphase, operation=operation,
                               child_operation_id=child_operation_id,
                               expected_revision=current_revision)

        mode_token = None
        mode_context = getattr(backend, "_stage_output_mode_context", None)
        if strict_mode is not None and mode_context is not None:
            mode_token = mode_context.set(strict_mode)
        try:
            reply = backend.invoke(operation, dict(arguments), child_execution, child_operation_id, capture)
        finally:
            if mode_token is not None:
                mode_context.reset(mode_token)
        if not isinstance(reply, Mapping) or reply.get("success") is not True or not isinstance(reply.get("execution"), Mapping):
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", f"managed {operation} output ticket did not complete", stage="post_dispatch")
        execution = reply["execution"]
        revision = execution.get("revision")
        managed_operation_id = execution.get("operation_id")
        managed_request_id = execution.get("request_id")
        ticket_hash = execution.get("request_hash")
        state_now = backend.service.ledger._state_for(ledger_ref)
        read_only = _G3_EFFECT_MAP.get(str(operation_effect(operation)).upper()) == "inspect"
        expected_delta = 0 if read_only else 1
        if (execution.get("model_ref") != ref or execution.get("session_id") != ref.get("session_id")
                or execution.get("project_id") not in (None, project_id)
                or (not read_only and (managed_operation_id is None
                    or not isinstance(managed_operation_id, str) or not managed_operation_id
                    or managed_request_id != child_request_id
                    or not isinstance(ticket_hash, str) or len(ticket_hash) != 64))
                or (read_only and (managed_operation_id is not None
                    or managed_request_id is not None or ticket_hash is not None))
                or type(revision) is not int or revision != state_now.revision or state_now.dirty
                or revision != current_revision + expected_delta):
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN",
                f"managed {operation} output ticket revision chain is invalid "
                f"(expected={current_revision + expected_delta}, returned={revision}, live={state_now.revision}, dirty={state_now.dirty}, "
                f"model_match={execution.get('model_ref') == ref}, session_match={execution.get('session_id') == ref.get('session_id')}, "
                f"project_match={execution.get('project_id') in (None, project_id)})",
                stage="post_dispatch",
            )
        event_rows = []
        for event in events:
            kind = event.get("kind")
            if kind not in {"call", "model", "model_snapshot"}:
                raise ExecutionContractError(
                    "EXECUTION_STATE_UNKNOWN",
                    f"managed {operation} emitted an unsupported Worker command kind",
                    stage="post_dispatch",
                )
            metadata = event.get("metadata")
            sidecar = event.get("w21_stage_output_binding")
            request_id = event.get("request_id")
            request_hash = event.get("request_hash")
            if (not isinstance(metadata, Mapping) or metadata.get("type") != kind
                    or metadata.get("request_id") != request_id
                    or not isinstance(request_id, str) or not request_id
                    or not isinstance(request_hash, str) or len(request_hash) != 64
                    or not isinstance(sidecar, Mapping)
                    or sidecar.get("model_ref") != ref
                    or sidecar.get("model_tag") != ref.get("model_tag")
                    or sidecar.get("worker_generation") != worker_generation
                    or sidecar.get("child_operation_id") != child_operation_id
                    or sidecar.get("operation") != operation
                    or sidecar.get("readphase") != readphase):
                raise ExecutionContractError(
                    "EXECUTION_STATE_UNKNOWN",
                    f"managed {operation} Worker command is not bound to its exact model/readphase",
                    stage="post_dispatch",
                )
            if kind == "call":
                if (metadata.get("generation") != worker_generation
                        or not isinstance(metadata.get("handle"), str) or not metadata.get("handle")
                        or not isinstance(metadata.get("method"), str) or not metadata.get("method")
                        or not isinstance(metadata.get("args"), list)):
                    raise ExecutionContractError(
                        "EXECUTION_STATE_UNKNOWN",
                        "submitted stage output call has no exact receiver/generation/method metadata",
                        stage="post_dispatch",
                    )
                model_tag = None
                method = metadata["method"]
                receiver = metadata["handle"]
            else:
                if metadata.get("tag") != ref.get("model_tag"):
                    raise ExecutionContractError(
                        "EXECUTION_STATE_UNKNOWN",
                        "stage output model command names a different model tag",
                        stage="post_dispatch",
                    )
                model_tag = metadata["tag"]
                method = None
                receiver = None
            if event.get("phase") != "submitted":
                continue
            event_rows.append({
                "worker_request_id": request_id, "worker_request_hash": request_hash,
                "worker_kind": kind, "worker_model_tag": model_tag,
                "worker_method": method, "worker_receiver": receiver,
                "worker_generation": worker_generation, "phase": "submitted",
            })
        if not event_rows:
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", f"managed {operation} returned without a submitted Worker request", stage="post_dispatch")
        chain_row = {
            "operation": operation, "readphase": readphase,
            "child_operation_id": child_operation_id,
            "child_request_id": child_request_id, "request_id": child_request_id,
            "managed_operation_id": managed_operation_id, "request_hash": ticket_hash,
            "managed_request_id": managed_request_id,
            "revision_witness": "service-inspect-no-ticket" if read_only else "managed-evaluate-ticket",
            "expected_revision": current_revision, "revision": revision,
            "effect": operation_effect(operation), "worker_requests": event_rows,
        }
        chain.append(chain_row)
        current_revision = revision
        return dict(reply), events

    def operation_effect(operation: str) -> str:
        from ._g3_ops import EFFECTS
        return EFFECTS.get(operation, "READ")

    def solution_binding_for(spec: Mapping[str, Any], readphase: str) -> tuple[dict[str, Any], dict[str, Any]]:
        reply, _ = managed_call("dataset.solution_indices", {"path": spec["dataset"]}, readphase=readphase)
        data = reply.get("data")
        if not isinstance(data, Mapping):
            raise _EvidenceUnavailable("dataset.solution_indices returned no native binding")
        try:
            tuple_value, resolved_binding = _check_tuple(spec, data)
        except _EvidenceUnavailable:
            raise
        return tuple_value, resolved_binding

    def read_field(spec: Mapping[str, Any], selection: Mapping[str, Any], expression: str,
                   readphase: str, check_hash: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        if (selection.get("kind") not in {"named", "explicit", "all"}
                or any(not isinstance(selection.get(name), str) or not selection.get(name)
                       for name in ("component", "geometry"))
                or type(selection.get("entity_dimension")) is not int):
            raise _EvidenceUnavailable("field selection kind/component/geometry/dimension is unsupported")
        tuple_value, resolved_binding = solution_binding_for(spec, readphase + ":solution-binding")
        evaluation_spec = {
            "expressions": [expression],
            "solution": {"dataset": spec["dataset"], "solution": tuple_value["solution"],
                         "outer": tuple_value["outer"], "inner": tuple_value["inner"]},
            "aggregate": "none", "complex_mode": "preserve", "selection": dict(selection),
        }
        reply, _ = managed_call("result.evaluate", {"spec": evaluation_spec}, readphase=readphase,
                                strict_mode="strict_field_readback")
        data = reply.get("data")
        if not isinstance(data, Mapping):
            raise _EvidenceUnavailable("result.evaluate returned no strict field readback")
        actual = data.get("strict_field_readback")
        if not isinstance(actual, Mapping):
            raise _EvidenceUnavailable("result.evaluate did not return strict native field evidence")
        actual_tuple = actual.get("solution_tuple")
        if (not isinstance(actual_tuple, Mapping)
                or actual_tuple.get("outer") != tuple_value["outer"]
                or actual_tuple.get("inner") != tuple_value["inner"]
                or actual_tuple.get("solnum") != tuple_value["solnum"]):
            raise _EvidenceUnavailable("strict field reader returned a different actual SolutionInfo tuple")
        payload, raw_evidence = _field_payload(data, expression)
        if not _selection_readback_matches(raw_evidence.get("selection_readback"), selection):
            raise _EvidenceUnavailable("native field numerical feature selection differs from the frozen selector")
        ticket = chain[-1]
        field_ref = _artifact_ref(
            backend, {"tuple": tuple_value, "binding": resolved_binding,
                      "field": raw_evidence, "normalized_field": payload},
            binding=binding, stage_run_operation_id=stage_run_operation_id,
            model_revision=ticket["revision"], child_operation_id=ticket["child_operation_id"],
            managed_operation_id=ticket["managed_operation_id"],
            request_id=ticket["request_id"], request_hash=ticket["request_hash"],
            revision_witness=ticket["revision_witness"], worker_requests=ticket["worker_requests"],
            readphase=readphase, check_definition_sha256=check_hash,
            selection_sha256=sha256_json(dict(selection)), tuple_binding=tuple_value,
        )
        return tuple_value, resolved_binding, payload, raw_evidence, field_ref

    def read_integral(spec: Mapping[str, Any], term: Mapping[str, Any], readphase: str,
                      check_hash: str) -> tuple[dict[str, Any], dict[str, Any], float, Any, dict[str, Any]]:
        selection = term.get("selection")
        if (not isinstance(selection, Mapping) or selection.get("kind") not in {"named", "explicit", "all"}
                or any(not isinstance(selection.get(name), str) or not selection.get(name)
                       for name in ("component", "geometry"))
                or type(term.get("entity_dimension")) is not int):
            raise _EvidenceUnavailable("native-integral term selection kind/component/geometry/dimension is unsupported")
        tuple_value, resolved_binding = solution_binding_for(spec, readphase + ":solution-binding")
        expression = term["variable"]
        evaluation_spec = {
            "expressions": [expression],
            "solution": {"dataset": spec["dataset"], "solution": tuple_value["solution"],
                         "outer": tuple_value["outer"], "inner": tuple_value["inner"]},
            "aggregate": "integral", "entity_dim": term["entity_dimension"],
            "selection": dict(term["selection"]),
        }
        reply, _ = managed_call("result.evaluate", {"spec": evaluation_spec}, readphase=readphase,
                                strict_mode="strict_metric_evidence")
        data = reply.get("data")
        if not isinstance(data, Mapping):
            raise _EvidenceUnavailable("result.evaluate returned no native integral result")
        integral, unit, metric_evidence = _integral_value(
            data, expression, tuple_value, term["selection"],
        )
        ticket = chain[-1]
        evidence_ref = _artifact_ref(
            backend, {"tuple": tuple_value, "binding": resolved_binding,
                      "term": dict(term), "native_integral": integral,
                      "unit_readback": unit, "strict_metric_evidence": metric_evidence},
            binding=binding, stage_run_operation_id=stage_run_operation_id,
            model_revision=ticket["revision"], child_operation_id=ticket["child_operation_id"],
            managed_operation_id=ticket["managed_operation_id"],
            request_id=ticket["request_id"], request_hash=ticket["request_hash"],
            revision_witness=ticket["revision_witness"], worker_requests=ticket["worker_requests"],
            readphase=readphase, check_definition_sha256=check_hash,
            selection_sha256=sha256_json(dict(term["selection"])), tuple_binding=tuple_value,
        )
        return tuple_value, resolved_binding, integral, unit, {"metric": metric_evidence, "evidence_ref": evidence_ref}

    try:
        output_tuple, output_binding = solution_binding_for(target_selection, "stage-output-tuple")
    except _EvidenceUnavailable as exc:
        return _unverified(binding, stage_run_operation_id, model_revision, target_selection, str(exc),
                           revision_chain=chain, output_revision=current_revision)

    output_ticket = chain[-1]
    output_tuple_ref = _artifact_ref(
        backend, {"output_tuple": output_tuple, "solution_binding": output_binding},
        binding=binding, stage_run_operation_id=stage_run_operation_id,
        model_revision=output_ticket["revision"], child_operation_id=output_ticket["child_operation_id"],
        managed_operation_id=output_ticket["managed_operation_id"],
        request_id=output_ticket["request_id"], request_hash=output_ticket["request_hash"],
        revision_witness=output_ticket["revision_witness"], worker_requests=output_ticket["worker_requests"],
        readphase="stage-output-tuple",
        check_definition_sha256=sha256_json({"kind": "stage-output-tuple", "target_selection": dict(target_selection)}),
        selection_sha256=sha256_json(dict(target_selection)), tuple_binding=output_tuple,
    )
    evidence_refs.append(output_tuple_ref)

    if not checks:
        return _unverified(binding, stage_run_operation_id, model_revision, target_selection,
                           "stage declares no checks; output cannot automatically pass",
                           revision_chain=chain, output_tuple=output_tuple,
                           solution_binding=output_binding, evidence_refs=evidence_refs,
                           output_revision=current_revision)

    observed_checks: list[dict[str, Any]] = []
    for check in checks:
        if not isinstance(check, Mapping):
            raise ExecutionContractError("INVALID_REQUEST", "stage contains a malformed output check", stage="validation")
        check_id = check.get("check_id")
        check_hash = sha256_json(dict(check))
        row: dict[str, Any] = {
            "check_id": check_id, "check_definition_sha256": check_hash,
            "binding": dict(binding), "stage_run_operation_id": stage_run_operation_id,
            "model_revision": model_revision, "status": "UNVERIFIED", "unit": None,
            "observed_error": None, "reference_scale": None,
            "missing": [],
        }
        try:
            if check.get("kind") == "continuity":
                required_v2 = ("source_solution", "target_solution", "source_selection", "target_selection",
                               "boundary_time")
                if any(not isinstance(check.get(name), Mapping) for name in required_v2):
                    raise _EvidenceUnavailable("continuity check lacks the version-2 source/target readback contract")
                if (not isinstance(check.get("source_variable"), str) or not check["source_variable"]
                        or not isinstance(check.get("target_variable"), str) or not check["target_variable"]
                        or not isinstance(check.get("frame"), str) or not check["frame"]):
                    raise _EvidenceUnavailable("continuity expression/frame contract is malformed")
                source_tuple, source_binding, source_payload, source_raw, source_ref = read_field(
                    check["source_solution"], check["source_selection"], check["source_variable"],
                    f"{check_id}:source-field", check_hash)
                target_tuple, target_binding, target_payload, target_raw, target_ref = read_field(
                    check["target_solution"], check["target_selection"], check["target_variable"],
                    f"{check_id}:target-field", check_hash)
                evidence_refs.extend((source_ref, target_ref))
                row.update(source_tuple=source_tuple, source_solution_binding=source_binding,
                           target_tuple=target_tuple, target_solution_binding=target_binding,
                           source_selection_sha256=sha256_json(dict(check["source_selection"])),
                           target_selection_sha256=sha256_json(dict(check["target_selection"])),
                           source_unit_readback=source_payload.get("unit_readback"),
                           target_unit_readback=target_payload.get("unit_readback"),
                           source_coordinate_frame=source_payload.get("coordinate_frame"),
                           target_coordinate_frame=target_payload.get("coordinate_frame"),
                           source_observation_revision=source_ref["observation_revision"],
                           target_observation_revision=target_ref["observation_revision"],
                           evidence_ref={"sha256": source_ref["sha256"], "artifact_id": source_ref["artifact_id"],
                                         "target_sha256": target_ref["sha256"], "target_artifact_id": target_ref["artifact_id"]})
                if _coordinate_pairs_equal(source_payload, target_payload):
                    error, scale = _max_abs_pairwise_error(source_payload["values"], target_payload["values"])
                    row["measured_error"], row["measured_reference_scale"] = error, scale
                    unit_verified = (
                        source_payload.get("unit_readback") == target_payload.get("unit_readback")
                        and _intrinsic_unit_matches(source_payload, check.get("unit"))
                        and _intrinsic_unit_matches(target_payload, check.get("unit"))
                    )
                    frame_verified = (
                        _coordinate_frame_matches(source_payload, check.get("frame"))
                        and _coordinate_frame_matches(target_payload, check.get("frame"))
                        and source_payload.get("coordinate_frame") == target_payload.get("coordinate_frame")
                    )
                    if unit_verified and frame_verified:
                        row["unit"] = check["unit"]
                        row["observed_error"], row["reference_scale"] = error, scale
                        row["measurement_status"] = "VERIFIED_FIELD_UNITS_AND_FRAME"
                    else:
                        row["missing"].append(
                            "intrinsic field-unit and coordinate-frame evidence is unavailable"
                        )
                else:
                    row["missing"].append("native source/target coordinates do not match exactly")
            elif check.get("kind") == "conservation":
                required_v2 = ("source_solution", "target_solution", "terms")
                if any(not isinstance(check.get(name), Mapping if name != "terms" else list) for name in required_v2):
                    raise _EvidenceUnavailable("conservation check lacks the version-2 source/target native-integral contract")
                if not check.get("terms"):
                    raise _EvidenceUnavailable("conservation check has no native integral terms")
                term_rows = []
                coefficient_values: list[float] = []
                integral_values: list[float] = []
                term_refs = []
                for term_index, term in enumerate(check.get("terms", [])):
                    side_spec = check.get(f"{term['side']}_solution")
                    term_tuple, term_binding, integral_value, unit_readback, metric = read_integral(
                        side_spec, term, f"{check_id}:term:{term_index}", check_hash)
                    metric_evidence = metric["metric"]
                    term_ref = metric["evidence_ref"]
                    evidence_refs.append(term_ref); term_refs.append(term_ref)
                    term_rows.append({"term_index": term_index, "term_sha256": sha256_json(dict(term)),
                                      "selection_sha256": sha256_json(dict(term["selection"])),
                                      "selection_status": "VERIFIED" if metric_evidence.get("selection_features") else "UNVERIFIED",
                                      "solution_tuple": term_tuple, "solution_binding": term_binding,
                                      "integral_value": integral_value,
                                      "unit_readback": unit_readback,
                                      "observation_revision": term_ref["observation_revision"],
                                      "evidence_ref": {"sha256": term_ref["sha256"], "artifact_id": term_ref["artifact_id"]}})
                    coefficient = float(term["coefficient"])
                    contribution = coefficient * integral_value
                    if not math.isfinite(contribution):
                        raise _EvidenceUnavailable("native integral coefficient multiplication overflowed")
                    coefficient_values.append(coefficient); integral_values.append(integral_value)
                signed_error = sum(c * value for c, value in zip(coefficient_values, integral_values))
                observed_error = abs(signed_error)
                reference_scale = sum(abs(c * value) for c, value in zip(coefficient_values, integral_values))
                if not math.isfinite(signed_error) or not math.isfinite(observed_error) or not math.isfinite(reference_scale):
                    raise _EvidenceUnavailable("native integral signed error or reference scale overflowed")
                target_term_rows = [item for item in term_rows
                                    if check["terms"][item["term_index"]]["side"] == "target"]
                row.update(target_tuple=output_tuple, target_solution_binding=output_binding,
                           target_term_tuples=[item["solution_tuple"] for item in target_term_rows],
                           target_term_solution_bindings=[item["solution_binding"] for item in target_term_rows],
                           target_term_tuple=(target_term_rows[0]["solution_tuple"] if len(target_term_rows) == 1 else None),
                           target_term_solution_binding=(target_term_rows[0]["solution_binding"] if len(target_term_rows) == 1 else None),
                           source_tuple=next((item["solution_tuple"] for item in term_rows
                                              if check["terms"][item["term_index"]]["side"] == "source"), None),
                           source_solution_binding=next((item["solution_binding"] for item in term_rows
                                                         if check["terms"][item["term_index"]]["side"] == "source"), None),
                           term_readbacks=term_rows, signed_error=signed_error,
                           observed_error=observed_error,
                           reference_scale=reference_scale,
                           evidence_ref={"sha256": term_refs[0]["sha256"], "artifact_id": term_refs[0]["artifact_id"],
                                         "term_refs": [{"sha256": item["sha256"], "artifact_id": item["artifact_id"]}
                                                       for item in term_refs]},
                           measurement_status="MEASURED_NATIVE_INTEGRAL")
                actual_units = [item.get("unit_readback") for item in term_rows]
                if not actual_units or any(unit is None for unit in actual_units):
                    row["missing"].append("native integral unit readback is unavailable")
                else:
                    row["unit_readbacks"] = actual_units
                    row["missing"].append("intrinsic integral unit dimensionality is not established by configured feature unit readback")
            else:
                row["missing"].append("unsupported check operator")
        except _EvidenceUnavailable as exc:
            row["missing"].append(str(exc))
        # The G3 result adapter reports configured/model-dependent units and
        # leaves field coordinate frames UNVERIFIED. Keep those gaps explicit;
        # the output validator will not infer a requested unit as native proof.
        if row.get("missing"):
            row["status"] = "UNVERIFIED"
        else:
            tolerance = check.get("tolerance", {})
            threshold = float(tolerance.get("absolute", 0.0)) + float(tolerance.get("relative", 0.0)) * float(row["reference_scale"])
            if not math.isfinite(threshold):
                row["missing"].append("check threshold overflowed")
                row["status"] = "UNVERIFIED"
            else:
                row["status"] = "PASS" if float(row["observed_error"]) <= threshold else "FAIL"
        observed_checks.append(row)

    status_verified = all(row.get("status") in {"PASS", "FAIL"} for row in observed_checks)
    output = {
        "contract": OUTPUT_CONTRACT, "producer": "managed-backend-stage-output-readback",
        "status": "VERIFIED" if status_verified else "UNVERIFIED",
        "binding": dict(binding), "stage_run_operation_id": stage_run_operation_id,
        "solve_revision": model_revision, "model_revision": model_revision,
        "output_revision": current_revision,
        "target_selection_sha256": sha256_json(dict(target_selection)),
        "output_tuple": output_tuple, "solution_binding": output_binding,
        "checks": observed_checks, "evidence_refs": evidence_refs,
        "revision_chain": chain,
        "worker_events": [{"readphase": item["readphase"], "operation": item["operation"],
                            "operation_id": item["event"].get("operation_id"),
                            "phase": item["event"].get("phase"),
                            "request_id": item["event"].get("request_id"),
                            "request_hash": item["event"].get("request_hash")}
                           for item in all_events],
    }
    return output


__all__ = ["OUTPUT_CONTRACT", "produce_stage_output_readback"]
