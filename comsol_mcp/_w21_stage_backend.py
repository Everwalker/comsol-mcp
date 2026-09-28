"""Private admission, dispatch, and result contracts for the W21 stage route.

This module contains validators only.  Native evidence is produced by the
selected ManagedBackend; stage plans and request arguments are declarations,
not proof.  The generic production backend currently returns an explicitly
unverified admission until COMSOL target field/unit/frame evidence is
available.
"""
from __future__ import annotations

from collections.abc import Mapping
import math
import hashlib
from typing import Any

from ._stage_contract import sha256_json


ADMISSION_CONTRACT = "w21-stage-native-admission/v1"
OUTPUT_CONTRACT = "w21-stage-output-readback/v1"


class StageWorkerDispatchGate:
    """Bind a stage dispatch to the real RemoteModel/Study Worker handles.

    The high-level stage request id identifies the durable service ticket; the
    Worker creates a separate request id for each Java RPC.  Only an exact
    ``Study.run()`` or the atomic-save ``Model.save(temp, true)`` call can
    consume the corresponding dispatch intent.  Reads are tracked so returned
    Study handles can be associated with the requested tag, but never mark the
    attempt as running.
    """

    def __init__(self, *, operation_id: str, model_tag: str, study_tag: str,
                 save_target_path: str | None = None) -> None:
        self.operation_id = operation_id
        self.model_tag = model_tag
        self.study_tag = study_tag
        self.save_target_path = save_target_path
        self.model_handle: str | None = None
        self.worker_generation: int | None = None
        self.study_collections: set[str] = set()
        self.study_handles: set[str] = set()
        self.submitted_ids: set[str] = set()
        self.run_submitted = False
        self.save_submitted = False

    @staticmethod
    def _handle_result(event: Mapping[str, Any]) -> tuple[str, int] | None:
        reply = event.get("reply")
        result = reply.get("result") if isinstance(reply, Mapping) else None
        if not isinstance(result, Mapping) or not isinstance(result.get("$worker_handle"), str):
            return None
        generation = result.get("generation")
        if type(generation) is not int or generation < 1:
            return None
        return result["$worker_handle"], generation

    def observe(self, event: Any, *, phase: str, stage_request_id: str,
                backend_binding: Any) -> dict[str, Any] | None:
        """Validate one actual Worker event and return a new dispatch record.

        Raises before send for a wrong/duplicate mutation call.  Returns None
        for reads and terminal observations.
        """
        from ._domain_outcome import is_mutation_call
        from ._java_worker import _request_hash
        import uuid

        if not isinstance(event, Mapping) or event.get("kind") != "call":
            return None
        if event.get("operation_id") != self.operation_id:
            if event.get("phase") == "submitted":
                raise ValueError("Worker call is associated with a different operation id")
            return None
        if not isinstance(backend_binding, Mapping):
            raise ValueError("managed backend did not provide a bound RemoteModel identity")
        model_handle = backend_binding.get("model_handle")
        generation = backend_binding.get("worker_generation")
        if (backend_binding.get("model_tag") != self.model_tag
                or backend_binding.get("phase") != phase
                or not isinstance(model_handle, str) or not model_handle
                or type(generation) is not int or generation < 1):
            raise ValueError("managed backend Worker identity differs from the stage ModelRef")
        expected_save_path = str(self.save_target_path) if phase == "save" and self.save_target_path else None
        if backend_binding.get("save_target_path") != expected_save_path:
            raise ValueError("managed backend save target differs from the stage artifact binding")
        if self.model_handle is None:
            self.model_handle = model_handle
            self.worker_generation = generation
        elif self.worker_generation != generation:
            raise ValueError("stage Worker model receiver or generation changed during dispatch")
        elif self.model_handle != model_handle:
            # Each phase resolves its ModelRef through the managed backend.
            # The Worker may return a fresh opaque handle for the same model,
            # so allow a phase-bound save receiver after the solve completed.
            if phase == "save" and self.run_submitted:
                self.model_handle = model_handle
            else:
                raise ValueError("stage Worker model receiver changed during solve dispatch")

        request_id = event.get("request_id")
        if (not isinstance(request_id, str) or request_id == stage_request_id
                or not request_id.startswith("wrk-")
                or (event.get("phase") == "submitted" and request_id in self.submitted_ids)):
            raise ValueError("Worker RPC request id is missing, reused, or confused with the stage request id")
        try:
            uuid.UUID(request_id[4:])
        except (ValueError, AttributeError) as exc:
            raise ValueError("Worker RPC request id is not a generated wrk-UUID") from exc
        metadata = event.get("metadata")
        if not isinstance(metadata, Mapping):
            raise ValueError("Worker call event has no request metadata")
        receiver = metadata.get("handle")
        receiver_generation = metadata.get("generation")
        method = metadata.get("method")
        args = metadata.get("args")
        if (not isinstance(receiver, str) or type(receiver_generation) is not int
                or receiver_generation != generation or not isinstance(method, str)
                or not isinstance(args, list)):
            raise ValueError("Worker call receiver, generation, method, or arguments are malformed")
        request_hash = event.get("request_hash")
        if not _sha256(request_hash) or _request_hash(metadata) != request_hash:
            raise ValueError("Worker request hash is missing or malformed")

        event_phase = event.get("phase")
        if event_phase == "submitted":
            self.submitted_ids.add(request_id)
            if method == "run":
                if (phase != "solve" or receiver not in self.study_handles or args
                        or self.run_submitted):
                    raise ValueError("Study.run does not target the one resolved stage study or is duplicated")
                self.run_submitted = True
                return {
                    "dispatch": "solve", "worker_request_id": request_id,
                    "worker_request_hash": request_hash,
                    "worker_receiver": receiver, "worker_generation": generation,
                    "worker_method": method, "worker_args": args,
                }
            if method == "save":
                if (phase != "save" or self.save_target_path is None
                        or receiver != model_handle or len(args) != 2 or args[1] is not True
                        or not self._is_atomic_temp_save(args[0]) or self.save_submitted):
                    raise ValueError("Model.save does not target the bound stage artifact or is duplicated")
                self.save_submitted = True
                return {
                    "dispatch": "save", "worker_request_id": request_id,
                    "worker_request_hash": request_hash,
                    "worker_receiver": receiver, "worker_generation": generation,
                    "worker_method": method, "worker_args": args,
                }
            if is_mutation_call(method, args, command="call", receiver=receiver):
                raise ValueError(f"unexpected mutating Worker call before stage {phase}: {method}")
            if not self._is_expected_read(receiver, method, args, model_handle):
                raise ValueError("Worker read is not bound to the stage model or resolved study")
            return None

        if event_phase == "observed":
            if request_id not in self.submitted_ids:
                raise ValueError("Worker observation has no matching submitted RPC")
            returned = self._handle_result(event)
            if returned is not None:
                handle, returned_generation = returned
                if returned_generation != generation:
                    raise ValueError("Worker returned a handle from a different generation")
                if receiver == model_handle and method == "study" and args == []:
                    self.study_collections.add(handle)
                elif (receiver == model_handle and method == "study" and args == [self.study_tag]) or (
                    receiver in self.study_collections and method == "get" and args == [self.study_tag]
                ):
                    self.study_handles.add(handle)
            return None
        if event_phase in {"unknown", "unresponsive"}:
            if request_id not in self.submitted_ids:
                raise ValueError("Worker terminal event has no matching submitted RPC")
        return None

    def _is_expected_read(self, receiver: str, method: str, args: list[Any], model_handle: str) -> bool:
        if receiver == model_handle:
            return method == "study" and (args == [] or args == [self.study_tag])
        if receiver in self.study_collections:
            return ((method == "tags" and args == [])
                    or (method == "get" and args == [self.study_tag]))
        if receiver in self.study_handles:
            return method == "label" and args == []
        return False

    def _is_atomic_temp_save(self, value: Any) -> bool:
        import re
        from pathlib import Path

        if not isinstance(value, str):
            return False
        target = Path(self.save_target_path or "")
        path = Path(value)
        return (path.parent == target.parent
                and re.fullmatch(rf"\.{re.escape(target.name)}\.[0-9a-f]{{32}}\.tmp\.mph", path.name) is not None)

REQUIRED_ADMISSION_FACTS = (
    "source_attempt_binding",
    "target_field_identity",
    "source_target_units",
    "source_target_mesh",
    "frame_identity",
    "history_identity",
)


def _sha256(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(char in "0123456789abcdef" for char in value.lower()))


def stage_attempt_binding(attempt: Mapping[str, Any]) -> dict[str, Any]:
    """Return exact immutable identities a backend proof must bind to."""
    return {
        "project_id": attempt["project_id"],
        "model_ref": attempt["model_ref"],
        "expected_revision": attempt["expected_revision"],
        "plan_id": attempt["plan_id"],
        "plan_sha256": attempt["plan_sha256"],
        "definition_sha256": attempt["definition_sha256"],
        "stage_id": attempt["stage_id"],
        "ordinal": attempt["ordinal"],
        "attempt_id": attempt["attempt_id"],
        "operation_id": attempt["operation_id"],
        "request_hash": attempt["request_hash"],
        "source_attempt_id": attempt.get("source_attempt_id"),
    }


def validate_native_admission(value: Any, binding: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """Accept only backend-produced, exact-bound complete native readback."""
    if not isinstance(value, Mapping):
        return False, ["backend admission readback unavailable"]
    missing: list[str] = []
    if value.get("contract") != ADMISSION_CONTRACT:
        missing.append("backend admission contract mismatch")
    if value.get("producer") != "managed-backend-native-readback":
        missing.append("admission was not produced by the managed backend")
    if value.get("status") != "VERIFIED":
        missing.append("backend admission overall status is not VERIFIED")
    if value.get("binding") != dict(binding):
        missing.append("backend proof is not bound to the exact attempt/model/revision/plan")
    facts = value.get("facts")
    if not isinstance(facts, Mapping):
        missing.extend(REQUIRED_ADMISSION_FACTS)
    else:
        for fact in REQUIRED_ADMISSION_FACTS:
            if facts.get(fact) != "VERIFIED":
                missing.append(fact)
    references = value.get("evidence_refs")
    if (not isinstance(references, list) or not references
            or any(not isinstance(item, Mapping) or not _sha256(item.get("sha256")) for item in references)):
        missing.append("backend proof evidence references")
    return not missing, missing


def _finite_nonnegative(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return False
    return math.isfinite(number) and number >= 0


def _finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, ValueError):
        return False


def _selector_matches(selector: Any, actual: int, labels: list[int]) -> bool:
    if selector is None:
        return True
    if isinstance(selector, list):
        return len(selector) == 1 and selector[0] == actual
    if selector == "all":
        return len(labels) == 1 and actual == labels[0]
    if selector == "first":
        return bool(labels) and actual == labels[0]
    if selector == "last":
        return bool(labels) and actual == labels[-1]
    return False


def _actual_pair_matches_solution_spec(
    tuple_value: Any, solution_binding: Any, spec: Mapping[str, Any], *,
    target_selection_sha256: str | None = None,
) -> bool:
    if not isinstance(tuple_value, Mapping) or not isinstance(solution_binding, Mapping):
        return False
    dataset, solution = tuple_value.get("dataset"), tuple_value.get("solution")
    outer, inner, solnum = (tuple_value.get("outer"), tuple_value.get("inner"),
                            tuple_value.get("solnum"))
    if (dataset != spec.get("dataset") or not isinstance(solution, str) or not solution
            or (spec.get("solution") is not None and solution != spec.get("solution"))
            or type(outer) is not int or outer < 1 or type(inner) is not int or inner < 1
            or type(solnum) is not int or solnum < 1
            or solution_binding.get("dataset") != dataset
            or solution_binding.get("solution") != solution
            or solution_binding.get("binding_complete") is not True
            or solution_binding.get("pair_mapping_complete") is not True
            or "SolutionInfo.getSolnum(outer, strict)" not in str(solution_binding.get("binding_source", ""))):
        return False
    pairs = solution_binding.get("solnum_pairs")
    if not isinstance(pairs, list):
        return False
    normalized: list[tuple[int, int, int]] = []
    pair_keys: set[tuple[int, int]] = set()
    inner_by_outer: dict[int, list[int]] = {}
    for row in pairs:
        if not isinstance(row, Mapping):
            return False
        ro, ri, rs = row.get("outer"), row.get("inner"), row.get("solnum")
        if type(ro) is not int or ro < 1 or type(ri) is not int or ri < 1 or type(rs) is not int or rs < 1:
            return False
        pair_key = (ro, ri)
        if pair_key in pair_keys:
            return False
        pair_keys.add(pair_key)
        inner_by_outer.setdefault(ro, []).append(ri)
        normalized.append((ro, ri, rs))
    if not normalized or normalized.count((outer, inner, solnum)) != 1:
        return False
    outer_labels = solution_binding.get("outer_indices")
    if (not isinstance(outer_labels, list)
            or any(type(item) is not int or item < 1 for item in outer_labels)):
        return False
    pair_outer_labels = list(inner_by_outer)
    if outer_labels != pair_outer_labels or outer not in outer_labels:
        return False
    inner_axis = solution_binding.get("inner_indices")
    if (not isinstance(inner_axis, list)
            or any(type(item) is not int or item < 1 for item in inner_axis)):
        return False
    first_inner_axis = next(iter(inner_by_outer.values()))
    expected_inner_axis = (
        first_inner_axis
        if all(axis == first_inner_axis for axis in inner_by_outer.values())
        else sorted({item for axis in inner_by_outer.values() for item in axis})
    )
    if inner_axis != expected_inner_axis:
        return False
    declared_inner_by_outer = solution_binding.get("inner_indices_by_outer")
    if declared_inner_by_outer is not None:
        if not isinstance(declared_inner_by_outer, Mapping):
            return False
        normalized_inner_by_outer: dict[int, list[int]] = {}
        for key, values in declared_inner_by_outer.items():
            if type(key) is int:
                outer_key = key
            elif isinstance(key, str) and key.isdecimal():
                outer_key = int(key)
            else:
                return False
            if (outer_key in normalized_inner_by_outer or not isinstance(values, list)
                    or any(type(item) is not int or item < 1 for item in values)):
                return False
            normalized_inner_by_outer[outer_key] = list(values)
        if normalized_inner_by_outer != inner_by_outer:
            return False
    inner_labels = sorted(row[1] for row in normalized if row[0] == outer)
    if (not inner_labels or not _selector_matches(spec.get("outer"), outer, outer_labels)
            or not _selector_matches(spec.get("inner"), inner, inner_labels)):
        return False
    if target_selection_sha256 is not None:
        resolution = solution_binding.get("selection_resolution")
        if (not isinstance(resolution, Mapping)
                or resolution.get("target_selection_sha256") != target_selection_sha256
                or resolution.get("resolved_pair") != {"outer": outer, "inner": inner, "solnum": solnum}
                or resolution.get("status") != "VERIFIED"):
            return False

    requested_params = spec.get("parameters")
    requested_time = spec.get("time")
    requested_frequency = spec.get("frequency")
    if requested_params or requested_time or requested_frequency:
        parameters_source = solution_binding.get("parameters_source")
        if (solution_binding.get("parameters_complete") is not True
                or not isinstance(parameters_source, str)
                or not all(method in parameters_source for method in ("SolutionInfo.getPNames", "getPvals", "getUnits"))):
            return False
        parameters = solution_binding.get("parameters")
        by_pair = parameters.get("by_pair") if isinstance(parameters, Mapping) else None
        row = by_pair.get(f"{outer}:{inner}") if isinstance(by_pair, Mapping) else None
        if not isinstance(row, Mapping) or row.get("solnum") != solnum:
            return False
        names, values, units = row.get("names"), row.get("values"), row.get("units")
        if (not isinstance(names, list) or not isinstance(values, list) or not isinstance(units, list)
                or len(names) != len(values) or len(names) != len(units)
                or any(not isinstance(name, str) or not name for name in names)):
            return False
        if len(names) != len(set(names)):
            return False
        filters = solution_binding.get("selection_resolution")
        filter_rows = filters.get("filters") if isinstance(filters, Mapping) else None
        if not isinstance(filter_rows, Mapping):
            return False
        for axis, requested in (("time", requested_time), ("frequency", requested_frequency)):
            if requested is None:
                continue
            if not isinstance(requested, list) or len(requested) != 1:
                return False
            actual = filter_rows.get(axis)
            if not isinstance(actual, Mapping) or actual.get("status") != "VERIFIED":
                return False
            name = actual.get("parameter_name")
            if not isinstance(name, str) or names.count(name) != 1:
                return False
            expected_names = {"time", "t"} if axis == "time" else {"frequency", "freq"}
            if name.casefold() not in expected_names:
                return False
            index = names.index(name)
            expected = requested[0]
            if (not isinstance(expected, Mapping) or not _finite_number(actual.get("value"))
                    or not _finite_number(values[index]) or actual.get("value") != expected.get("value")
                    or actual.get("unit") != expected.get("unit")
                    or values[index] != expected.get("value") or units[index] != expected.get("unit")):
                return False
        if isinstance(requested_params, Mapping):
            for name, expected in requested_params.items():
                if name not in names:
                    return False
                index = names.index(name)
                expected_value = expected.get("value") if isinstance(expected, Mapping) and "value" in expected else expected
                if values[index] != expected_value:
                    return False
                if isinstance(values[index], (int, float)) and not _finite_number(values[index]):
                    return False
                if isinstance(expected, Mapping) and expected.get("unit") is not None and units[index] != expected["unit"]:
                    return False
    return True


def _binding_matches_same_solution(left: Any, right: Any) -> bool:
    """Bind a check tuple to the same typed solver sequence and model revision."""
    if not isinstance(left, Mapping) or not isinstance(right, Mapping):
        return False
    source = "SolutionInfo.getSolnum(outer, strict)"
    return (
        isinstance(left.get("solution"), str) and bool(left.get("solution"))
        and left.get("solution") == right.get("solution")
        and left.get("binding_complete") is True
        and right.get("binding_complete") is True
        and left.get("pair_mapping_complete") is True
        and right.get("pair_mapping_complete") is True
        and source in str(left.get("binding_source", ""))
        and source in str(right.get("binding_source", ""))
    )


def _tuple_parameter_matches_quantity(
    tuple_value: Any, solution_binding: Any, quantity: Any, *, axis: str,
) -> bool:
    """Prove a time/frequency value and unit from the selected SolutionInfo row."""
    if not isinstance(tuple_value, Mapping) or not isinstance(solution_binding, Mapping):
        return False
    if not isinstance(quantity, Mapping) or not _finite_number(quantity.get("value")):
        return False
    unit = quantity.get("unit")
    if not isinstance(unit, str) or not unit:
        return False
    outer, inner, solnum = tuple_value.get("outer"), tuple_value.get("inner"), tuple_value.get("solnum")
    if type(outer) is not int or type(inner) is not int or type(solnum) is not int:
        return False
    if solution_binding.get("parameters_complete") is not True:
        return False
    source = solution_binding.get("parameters_source")
    if not isinstance(source, str) or not all(
        method in source for method in ("SolutionInfo.getPNames", "getPvals", "getUnits")
    ):
        return False
    parameters = solution_binding.get("parameters")
    by_pair = parameters.get("by_pair") if isinstance(parameters, Mapping) else None
    row = by_pair.get(f"{outer}:{inner}") if isinstance(by_pair, Mapping) else None
    if not isinstance(row, Mapping) or row.get("solnum") != solnum:
        return False
    names, values, units = row.get("names"), row.get("values"), row.get("units")
    if (not isinstance(names, list) or not isinstance(values, list) or not isinstance(units, list)
            or len(names) != len(values) or len(names) != len(units)):
        return False
    aliases = {"t", "time"} if axis == "time" else {"f", "freq", "frequency"}
    matches = [index for index, name in enumerate(names)
               if isinstance(name, str) and name.casefold() in aliases]
    if len(matches) != 1:
        return False
    index = matches[0]
    value = values[index]
    if (not _finite_number(value) or units[index] != unit
            or not math.isclose(float(value), float(quantity["value"]), rel_tol=1e-12, abs_tol=1e-15)):
        return False
    return True


def validate_output_readback(
    value: Any, *, binding: Mapping[str, Any], stage_run_operation_id: str,
    model_revision: int, target_selection: Mapping[str, Any], checks: list[dict[str, Any]],
) -> tuple[bool, list[str], bool]:
    """Require run-bound SolutionBinding tuple and recalculated finite checks.

    The first return value means the evidence is internally valid. The third
    separately reports whether every recomputed check passes; a valid FAIL is
    never promoted to acceptance.
    """
    if not isinstance(value, Mapping) or value.get("contract") != OUTPUT_CONTRACT:
        return False, ["backend output readback unavailable"], False
    missing: list[str] = []
    if value.get("status") != "VERIFIED":
        missing.append("verified output readback")
    if value.get("binding") != dict(binding):
        missing.append("output readback attempt/model/plan/source binding")
    if value.get("stage_run_operation_id") != stage_run_operation_id:
        missing.append("exact stage solve operation binding")
    if type(value.get("model_revision")) is not int or value.get("model_revision") != model_revision:
        missing.append("post-solve model revision binding")
    if value.get("target_selection_sha256") != sha256_json(dict(target_selection)):
        missing.append("frozen target SolutionSpec binding")
    tuple_value = value.get("output_tuple")
    solution_binding = value.get("solution_binding")
    tuple_ok = _actual_pair_matches_solution_spec(
        tuple_value, solution_binding, target_selection,
        target_selection_sha256=sha256_json(dict(target_selection)),
    )
    if not tuple_ok:
        missing.append("exact output solution tuple")
    observed_checks = value.get("checks")
    checks_passed = bool(checks)
    if not isinstance(observed_checks, list):
        missing.append("declared continuity/conservation checks")
    else:
        if not checks:
            missing.append("at least one declared acceptance check")
        declared_ids = [item.get("check_id") for item in checks]
        observed_ids = [item.get("check_id") for item in observed_checks if isinstance(item, Mapping)]
        if (len(observed_checks) != len(checks)
                or any(not isinstance(item, Mapping) for item in observed_checks)
                or any(not isinstance(item, str) or not item for item in declared_ids + observed_ids)
                or len(declared_ids) != len(set(declared_ids))
                or len(observed_ids) != len(set(observed_ids))
                or set(observed_ids) != set(declared_ids)):
            missing.append("unique one-to-one frozen check coverage")
        by_id = {item.get("check_id"): item for item in observed_checks if isinstance(item, Mapping)}
        for check in checks:
            observed = by_id.get(check.get("check_id"))
            if not isinstance(observed, Mapping):
                missing.append(f"check:{check.get('check_id', 'unknown')}")
                checks_passed = False
                continue
            tolerance = check.get("tolerance") if isinstance(check.get("tolerance"), Mapping) else {}
            error, scale = observed.get("observed_error"), observed.get("reference_scale")
            absolute, relative = tolerance.get("absolute"), tolerance.get("relative")
            expected_status = None
            if (_finite_nonnegative(error) and _finite_nonnegative(scale)
                    and _finite_nonnegative(absolute) and _finite_nonnegative(relative)
                    and observed.get("check_definition_sha256") == sha256_json(check)
                    and observed.get("unit") == check.get("unit")
                    and observed.get("binding") == value.get("binding")
                    and observed.get("stage_run_operation_id") == stage_run_operation_id
                    and observed.get("model_revision") == model_revision
                    and isinstance(observed.get("evidence_ref"), Mapping)
                    and _sha256(observed["evidence_ref"].get("sha256"))):
                threshold = float(absolute) + float(relative) * float(scale)
                if math.isfinite(threshold):
                    expected_status = "PASS" if float(error) <= threshold else "FAIL"
                target_spec = check.get("target_solution")
                source_spec = check.get("source_solution")
                if check.get("kind") == "continuity":
                    if isinstance(source_spec, Mapping) or isinstance(target_spec, Mapping):
                        # Version 2 continuity compares the predecessor and the
                        # newly produced solution at boundary_time. Its target
                        # tuple may differ from the stage's terminal output tuple.
                        boundary_time = check.get("boundary_time")
                        source_tuple = observed.get("source_tuple")
                        source_binding = observed.get("source_solution_binding")
                        check_target_tuple = observed.get("target_tuple")
                        check_target_binding = observed.get("target_solution_binding")
                        if (not isinstance(source_spec, Mapping)
                                or not isinstance(target_spec, Mapping)
                                or not isinstance(boundary_time, Mapping)
                                or not _actual_pair_matches_solution_spec(
                                    source_tuple, source_binding, source_spec,
                                    target_selection_sha256=sha256_json(dict(source_spec)))
                                or not _tuple_parameter_matches_quantity(
                                    source_tuple, source_binding, boundary_time, axis="time")
                                or not _actual_pair_matches_solution_spec(
                                    check_target_tuple, check_target_binding, target_spec,
                                    target_selection_sha256=sha256_json(dict(target_spec)))
                                or not _binding_matches_same_solution(check_target_binding, solution_binding)
                                or not isinstance(tuple_value, Mapping)
                                or check_target_tuple.get("solution") != tuple_value.get("solution")
                                or not _tuple_parameter_matches_quantity(
                                    check_target_tuple, check_target_binding, boundary_time, axis="time")):
                            expected_status = None
                    elif observed.get("target_tuple") != tuple_value:
                        # Version 1 continuity has no frozen source/target
                        # SolutionSpec or boundary time; keep its output-tuple
                        # binding contract unchanged.
                        expected_status = None
                elif check.get("kind") == "conservation":
                    if (observed.get("target_tuple") != tuple_value
                            or (isinstance(source_spec, Mapping)
                                and not _actual_pair_matches_solution_spec(
                                    observed.get("source_tuple"), observed.get("source_solution_binding"),
                                    source_spec, target_selection_sha256=sha256_json(dict(source_spec))))):
                        expected_status = None
                    term_rows = observed.get("term_readbacks")
                    terms = check.get("terms")
                    if (not isinstance(terms, list) or not isinstance(term_rows, list)
                            or len(term_rows) != len(terms)):
                        expected_status = None
                    else:
                        integral_values: list[float] = []
                        coefficients: list[float] = []
                        for index, (term, term_row) in enumerate(zip(terms, term_rows)):
                            side_spec = check.get(f"{term.get('side')}_solution") if isinstance(term, Mapping) else None
                            if (not isinstance(term_row, Mapping)
                                    or term_row.get("term_index") != index
                                    or term_row.get("term_sha256") != sha256_json(term)
                                    or term_row.get("selection_sha256") != sha256_json(term.get("selection"))
                                    or term_row.get("selection_status") != "VERIFIED"
                                    or not _finite_number(term_row.get("integral_value"))
                                    or not isinstance(side_spec, Mapping)
                                    or not _actual_pair_matches_solution_spec(
                                        term_row.get("solution_tuple"), term_row.get("solution_binding"),
                                        side_spec, target_selection_sha256=sha256_json(dict(side_spec))
                                    )):
                                expected_status = None
                                break
                            if term.get("side") == "target" and (
                                    not _binding_matches_same_solution(term_row.get("solution_binding"), solution_binding)
                                    or term_row["solution_tuple"].get("solution") != tuple_value.get("solution")):
                                expected_status = None
                                break
                            coefficient = term.get("coefficient")
                            contribution = float(coefficient) * float(term_row["integral_value"])
                            if not math.isfinite(contribution):
                                expected_status = None
                                break
                            coefficients.append(float(coefficient))
                            integral_values.append(float(term_row["integral_value"]))
                        if expected_status is not None:
                            target_rows = [term_row for term, term_row in zip(terms, term_rows)
                                           if isinstance(term, Mapping) and term.get("side") == "target"]
                            target_tuples = [term_row.get("solution_tuple") for term_row in target_rows]
                            target_bindings = [term_row.get("solution_binding") for term_row in target_rows]
                            expected_single_tuple = target_tuples[0] if len(target_tuples) == 1 else None
                            expected_single_binding = target_bindings[0] if len(target_bindings) == 1 else None
                            # Older valid envelopes may contain only the authoritative
                            # term_readbacks. Validate these producer convenience
                            # summaries when supplied, without making them required.
                            target_term_summaries = {
                                "target_term_tuples": target_tuples,
                                "target_term_solution_bindings": target_bindings,
                                "target_term_tuple": expected_single_tuple,
                                "target_term_solution_binding": expected_single_binding,
                            }
                            if any(key in observed and observed.get(key) != expected
                                   for key, expected in target_term_summaries.items()):
                                expected_status = None
                        if expected_status is not None:
                            computed_error = abs(sum(c * v for c, v in zip(coefficients, integral_values)))
                            computed_scale = sum(abs(c * v) for c, v in zip(coefficients, integral_values))
                            if (not math.isfinite(computed_error) or not math.isfinite(computed_scale)
                                    or not math.isclose(float(error), computed_error, rel_tol=1e-12, abs_tol=1e-15)
                                    or not math.isclose(float(scale), computed_scale, rel_tol=1e-12, abs_tol=1e-15)):
                                expected_status = None
                else:
                    expected_status = None
            if expected_status is None or observed.get("status") != expected_status:
                missing.append(f"check:{check.get('check_id', 'unknown')} definition/numeric evidence")
                checks_passed = False
            elif expected_status != "PASS":
                checks_passed = False
    return not missing, missing, checks_passed and not missing


def hash_saved_artifact(path: Any) -> tuple[str, int]:
    """Hash the exact saved file after project-root containment was checked."""
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
            size += len(chunk)
    return hasher.hexdigest(), size
