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
INITIAL_OUTPUT_ACCEPTANCE_SCOPE = "INITIAL_OUTPUT_AND_SOURCE_ARTIFACT_ONLY"
INITIAL_JOB_EVENT_SNAPSHOT_SCHEMA = "operation-store-job-event-snapshot/v1"
INITIAL_JOB_EVENT_SNAPSHOT_PAGE_SIZE = 1000
INITIAL_JOB_EVENT_SNAPSHOT_MAX_PAGES = 128


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
                if receiver == model_handle and method == "study" and args == [self.study_tag]:
                    self.study_handles.add(handle)
            return None
        if event_phase in {"unknown", "unresponsive"}:
            if request_id not in self.submitted_ids:
                raise ValueError("Worker terminal event has no matching submitted RPC")
        return None

    def _is_expected_read(self, receiver: str, method: str, args: list[Any], model_handle: str) -> bool:
        if receiver == model_handle:
            # The managed wrapper sets the current bound model before the
            # legacy callback enters ExecutionService. That state attribution
            # may read the unsaved model's path/label; accept only these exact
            # zero-argument getters on the phase-bound Model receiver.
            return (method == "study" and args == [self.study_tag]
                    or (method in {"getFilePath", "label"} and args == []))
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


def is_registered_initial_state_stage(plan: Any, stage: Any) -> bool:
    """Derive the initial-state route from the immutable stored plan row.

    This is deliberately not a caller flag.  The plan must be version 2 and
    the row must be the unique ordinal-one, dependency-free initial-state
    stage.  Initial output acceptance is a distinct route only when it has no
    successor-only target_variables and no source-dependent checks.
    """
    if not isinstance(plan, Mapping) or not isinstance(stage, Mapping):
        return False
    definition = plan.get("definition")
    if not isinstance(definition, Mapping) or definition.get("version") != 2:
        return False
    stages = definition.get("stages")
    if not isinstance(stages, list) or not stages:
        return False
    matching = [row for row in stages if isinstance(row, Mapping)
                and row.get("stage_id") == stage.get("stage_id")]
    ordinals = [row.get("ordinal") for row in stages if isinstance(row, Mapping)]
    if (len(matching) != 1 or matching[0] != stage
            or any(type(value) is not int or value < 1 for value in ordinals)
            or stage.get("ordinal") != 1 or min(ordinals) != 1
            or stage.get("depends_on") != []
            or stage.get("source_selection") != {
                "kind": "initial_state", "strategy": "declared_initial"}
            or stage.get("reference_state") != {"strategy": "initial_state"}
            or "target_variables" in stage
            or stage.get("checks") != []):
        return False
    return True


def initial_stage_managed_action_plan(plan: Any, stage: Any) -> list[str] | None:
    """Derive the finite observation sequence from the exact registered initial stage."""
    if not is_registered_initial_state_stage(plan, stage):
        return None
    mappings = stage.get("variable_mappings")
    if not isinstance(mappings, list) or not mappings:
        return None
    actions = [
        "study.inspect:preflight",
        "study.inspect:postsolve",
        "solver.inspect:active-variables",
        "Variables.xmeshInfo:active-variables",
        "dataset.solution_indices:output-tuple",
        "dataset.solution_indices:solution-mesh-association",
    ]
    for index, mapping in enumerate(mappings):
        if not isinstance(mapping, Mapping):
            return None
        actions.extend((f"dataset.solution_indices:field-binding:{index}",
                        f"result.evaluate:field:{index}"))
    actions.append("mesh.inspect:post-stage")
    return actions


def is_w21_initial_stage_runner_profile(plan: Any, stage: Any) -> bool:
    """Identify the runner's frozen single-field profile for its private 12-action cap."""
    definition = plan.get("definition") if isinstance(plan, Mapping) else None
    mappings = stage.get("variable_mappings") if isinstance(stage, Mapping) else None
    return bool(is_registered_initial_state_stage(plan, stage)
        and isinstance(definition, Mapping) and definition.get("plan_id") == "w21_initial_stage"
        and stage.get("stage_id") == "thermal_initial"
        and isinstance(mappings, list) and len(mappings) == 1
        and isinstance(mappings[0], Mapping)
        and mappings[0].get("target_variable") == "T"
        and mappings[0].get("target_unit") == "K")


def validate_native_admission(
    value: Any, binding: Mapping[str, Any], *, initial_state: bool = False,
) -> tuple[bool, list[str]]:
    """Accept only backend-produced, exact-bound complete native readback."""
    if not isinstance(value, Mapping):
        return False, ["backend admission readback unavailable"]
    missing: list[str] = []
    if value.get("contract") != ADMISSION_CONTRACT:
        missing.append("backend admission contract mismatch")
    expected_producer = ("managed-backend-initial-stage-preflight" if initial_state
                         else "managed-backend-native-readback")
    if value.get("producer") != expected_producer:
        missing.append("admission was not produced by the managed backend")
    expected_status = "READY_FOR_INITIAL_STATE_SOLVE" if initial_state else "VERIFIED"
    if value.get("status") != expected_status:
        missing.append(f"backend admission overall status is not {expected_status}")
    if value.get("binding") != dict(binding):
        missing.append("backend proof is not bound to the exact attempt/model/revision/plan")
    facts = value.get("facts")
    if not isinstance(facts, Mapping):
        missing.extend(REQUIRED_ADMISSION_FACTS)
    elif initial_state:
        required_initial = {
            "study_target_binding": "VERIFIED",
            "source_attempt_binding": "NOT_APPLICABLE",
            "target_field_identity": "POST_SOLVE_REQUIRED",
            "source_target_units": "POST_SOLVE_REQUIRED",
            "source_target_mesh": "POST_SOLVE_REQUIRED",
            "frame_identity": "POST_SOLVE_REQUIRED",
            "history_identity": "NOT_APPLICABLE",
        }
        for fact, expected in required_initial.items():
            if facts.get(fact) != expected:
                missing.append(f"initial-state fact {fact} must be {expected}")
        if value.get("initial_state_route") != "registered_first_stage":
            missing.append("initial-state route was not derived from the registered first stage")
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


def _initial_complex(value: Any) -> complex | None:
    if isinstance(value, Mapping) and set(value) == {"real", "imag"}:
        if _finite_number(value.get("real")) and _finite_number(value.get("imag")):
            return complex(float(value["real"]), float(value["imag"]))
        return None
    if _finite_number(value):
        return complex(float(value), 0.0)
    return None


def _initial_unit_controls_valid(row: Mapping[str, Any], variable: str, unit: str) -> bool:
    """Recompute automatic-unit numeric controls from the full three-expression Eval."""
    field = row.get("field_readback")
    array = field.get("field_array") if isinstance(field, Mapping) else None
    if not isinstance(array, Mapping) or field.get("status") != "VERIFIED":
        return False
    expressions = [variable, f"({variable})/1[{unit}]", "1"]
    metadata = array.get("metadata")
    shape, axes, values = array.get("shape"), array.get("axes"), array.get("values")
    if (axes != ["expression", "outer", "inner", "point"]
            or not isinstance(metadata, Mapping) or metadata.get("requested_expressions") != expressions
            or not isinstance(shape, list) or len(shape) != 4 or shape[:3] != [3, 1, 1]
            or type(shape[3]) is not int or shape[3] < 1
            or not isinstance(values, list) or len(values) != 3):
        return False
    rows: list[list[complex]] = []
    for expression_row in values:
        if (not isinstance(expression_row, list) or len(expression_row) != 1
                or not isinstance(expression_row[0], list) or len(expression_row[0]) != 1
                or not isinstance(expression_row[0][0], list)
                or len(expression_row[0][0]) != shape[3]):
            return False
        clean = [_initial_complex(value) for value in expression_row[0][0]]
        if any(value is None for value in clean):
            return False
        rows.append([value for value in clean if value is not None])
    unit_evidence = field.get("expression_unit_readback")
    units = unit_evidence.get("values") if isinstance(unit_evidence, Mapping) else None
    expected_units = {expressions[0]: unit, expressions[1]: "1", expressions[2]: "1"}
    controls = row.get("automatic_unit_control_evidence")
    if (not isinstance(units, Mapping) or dict(units) != expected_units
            or not isinstance(controls, Mapping)
            or controls.get("status") != "VERIFIED"
            or controls.get("scope") != "automatic_native_expression_unit_dimensionality"
            or controls.get("units_overridden") is not False
            or controls.get("declared_unit") != unit
            or controls.get("expressions") != expressions
            or controls.get("expression_unit_readbacks") != expected_units
            or controls.get("threshold") != {"absolute": 1e-10, "relative": 1e-12}):
        return False
    limits = controls.get("limits")
    if (not isinstance(limits, list)
            or "active degrees of freedom are not established" not in limits
            or "physics intrinsic units are not established" not in limits
            or "coordinate frame and solver history are not established" not in limits):
        return False
    numeric = controls.get("numeric_controls")
    if not isinstance(numeric, Mapping):
        return False

    def observed(name: str, left: list[complex], right: list[complex] | None) -> bool:
        if right is not None and len(left) != len(right):
            return False
        pairs = zip(left, right) if right is not None else ((value, 1 + 0j) for value in left)
        errors: list[float] = []
        limits: list[float] = []
        for lhs, rhs in pairs:
            error = abs(lhs - rhs)
            scale = max(abs(lhs), abs(rhs))
            limit = 1e-10 + 1e-12 * scale
            if not all(math.isfinite(value) for value in (error, scale, limit)):
                return False
            errors.append(error); limits.append(limit)
        status = "PASS" if all(error <= limit for error, limit in zip(errors, limits)) else "FAIL"
        record = numeric.get(name)
        return (isinstance(record, Mapping) and record.get("status") == status == "PASS"
                and record.get("point_count") == len(left)
                and _finite_nonnegative(record.get("max_absolute_error"))
                and _finite_nonnegative(record.get("max_allowed_error"))
                and math.isclose(float(record["max_absolute_error"]), max(errors, default=0.0), rel_tol=1e-15, abs_tol=1e-15)
                and math.isclose(float(record["max_allowed_error"]), max(limits, default=0.0), rel_tol=1e-15, abs_tol=1e-15))

    normalized = row.get("normalized_field")
    coordinates = field.get("coordinates")
    return (observed("normalized_matches_original", rows[0], rows[1])
            and observed("constant_equals_real_one", rows[2], None)
            and isinstance(normalized, Mapping)
            and normalized.get("values") == values[0][0][0]
            and isinstance(coordinates, Mapping)
            and normalized.get("coordinates") == coordinates.get("values")
            and normalized.get("unit_readback") == unit
            and normalized.get("coordinate_frame") == row.get("coordinate_frame")
            and normalized.get("automatic_unit_control_evidence") == dict(controls))


def validate_initial_output_readback(
    value: Any, *, binding: Mapping[str, Any], stage: Mapping[str, Any],
    stage_run_operation_id: str, model_revision: int,
) -> tuple[bool, list[str]]:
    """Validate every mapped native initial field and its actual source metadata."""
    if not isinstance(value, Mapping):
        return False, ["initial output readback unavailable"]
    missing: list[str] = []
    if value.get("contract") != OUTPUT_CONTRACT:
        missing.append("initial output contract")
    if value.get("producer") != "managed-backend-initial-output-readback":
        missing.append("managed initial output producer")
    if value.get("status") != "VERIFIED":
        missing.append("complete initial output producer status")
    if value.get("initial_output_scope") != INITIAL_OUTPUT_ACCEPTANCE_SCOPE:
        missing.append("scoped initial output acceptance")
    if value.get("initial_source_facts") != "NOT_APPLICABLE" or value.get("history_identity") != "NOT_APPLICABLE":
        missing.append("source/history facts are not applicable for initial state")
    if value.get("binding") != dict(binding):
        missing.append("exact attempt/model/plan binding")
    if value.get("stage_run_operation_id") != stage_run_operation_id:
        missing.append("exact solve operation binding")
    if (value.get("solve_revision") != model_revision or value.get("model_revision") != model_revision
            or type(value.get("output_revision")) is not int or value.get("output_revision") < model_revision):
        missing.append("post-solve revision binding")
    target_selection = stage.get("target_selection")
    tuple_value = value.get("output_tuple")
    output_binding = value.get("solution_binding")
    tuple_ok = (isinstance(target_selection, Mapping)
                and value.get("target_selection_sha256") == sha256_json(dict(target_selection))
                and _actual_pair_matches_solution_spec(tuple_value, output_binding, target_selection,
                    target_selection_sha256=sha256_json(dict(target_selection))))
    if not tuple_ok:
        missing.append("actual output dataset/solution/outer/inner/solnum tuple")

    dataset = value.get("dataset_identity")
    if (not isinstance(dataset, Mapping) or dataset.get("status") != "VERIFIED"
            or dataset.get("dataset_type") != "Solution"
            or not isinstance(tuple_value, Mapping)
            or dataset.get("dataset") != tuple_value.get("dataset")
            or dataset.get("solution") != tuple_value.get("solution")
            or dataset.get("frametype") not in {"mesh", "material", "spatial", "geometry"}
            or not isinstance(dataset.get("component"), str) or not dataset.get("component")
            or not isinstance(dataset.get("geometry"), str) or not dataset.get("geometry")
            or type(dataset.get("spatial_dimension")) is not int
            or dataset.get("spatial_dimension") not in {0, 1, 2, 3}):
        missing.append("actual Solution dataset component/geometry/frame")

    solver = value.get("study_solver_binding")
    solver_data = solver.get("solver_readback") if isinstance(solver, Mapping) else None
    variables: list[Mapping[str, Any]] = []
    def visit(rows: Any) -> None:
        if not isinstance(rows, list):
            return
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            if row.get("type_id") == "Variables":
                variables.append(row)
            visit(row.get("children"))
    if isinstance(solver_data, Mapping):
        visit(solver_data.get("features"))
    active = [row for row in variables if row.get("is_active") is True]
    study_target = stage.get("study_target")
    study_segments = study_target.get("segments") if isinstance(study_target, Mapping) else None
    study_tag = (study_segments[0].get("tag") if isinstance(study_segments, list) and len(study_segments) == 1
                 and isinstance(study_segments[0], Mapping) else None)
    sequences = solver.get("study_solver_sequences") if isinstance(solver, Mapping) else None
    if (not isinstance(solver, Mapping) or solver.get("status") != "VERIFIED"
            or solver.get("study_tag") != study_tag
            or not isinstance(solver_data, Mapping) or solver_data.get("kind") != "solver_sequence"
            or solver_data.get("solver") != solver.get("solver_tag")
            or solver_data.get("study") != study_tag or solver_data.get("is_attached") is not True
            or not isinstance(sequences, Mapping) or sequences.get("status") != "VERIFIED"
            or sequences.get("tags") != [solver.get("solver_tag")]
            or not variables or len(active) != 1):
        missing.append("unique active Variables feature bound to the registered Study solver")

    xmesh = value.get("variables_dof_readback")
    if (not isinstance(xmesh, Mapping) or xmesh.get("status") != "VERIFIED"
            or xmesh.get("feature_active") is not True
            or xmesh.get("feature_tag") != (active[0].get("tag") if active else None)
            or type(xmesh.get("n_dofs")) is not int or xmesh.get("n_dofs") <= 0
            or xmesh.get("cleanup") != "clearXmesh"):
        missing.append("active Variables.xmeshInfo solved-for DOF summary")
    names = xmesh.get("field_names") if isinstance(xmesh, Mapping) else None
    counts = xmesh.get("field_n_dofs") if isinstance(xmesh, Mapping) else None
    if (not isinstance(names, list) or not isinstance(counts, list) or len(names) != len(counts)
            or any(not isinstance(name, str) or not name for name in names)
            or any(type(count) is not int or count < 0 for count in counts)
            or sum(counts) != xmesh.get("n_dofs")):
        missing.append("complete Variables per-field DOF summary")
    dof_by_name = ({name: count for name, count in zip(names, counts)}
                   if isinstance(names, list) and isinstance(counts, list) and len(names) == len(counts) else {})

    association = value.get("solution_mesh_association")
    if (not isinstance(association, Mapping) or association.get("status") != "VERIFIED"
            or not isinstance(tuple_value, Mapping)
            or association.get("dataset") != tuple_value.get("dataset")
            or association.get("solution") != tuple_value.get("solution")
            or association.get("selected_tuple") != {key: tuple_value.get(key)
                for key in ("dataset", "solution", "outer", "inner", "solnum")}
            or association.get("geometry") != (dataset.get("geometry") if isinstance(dataset, Mapping) else None)
            or not isinstance(association.get("mesh_tag"), str) or not association.get("mesh_tag")):
        missing.append("actual SolutionInfo-to-mesh association for the selected tuple")

    mappings = stage.get("variable_mappings")
    fields = value.get("initial_field_readbacks")
    if not isinstance(mappings, list) or not mappings or not isinstance(fields, list) or len(fields) != len(mappings):
        missing.append("one initial field readback per declared mapping")
    else:
        for index, (mapping, row) in enumerate(zip(mappings, fields)):
            label = f"initial field {index}"
            if not isinstance(mapping, Mapping) or not isinstance(row, Mapping):
                missing.append(label)
                continue
            variable, unit = mapping.get("target_variable"), mapping.get("target_unit")
            expected_selection = {
                "kind": "all",
                "component": dataset.get("component") if isinstance(dataset, Mapping) else None,
                "geometry": dataset.get("geometry") if isinstance(dataset, Mapping) else None,
                "entity_dimension": dataset.get("spatial_dimension") if isinstance(dataset, Mapping) else None,
            }
            if (not isinstance(variable, str) or not isinstance(unit, str)
                    or row.get("mapping_index") != index or row.get("mapping") != dict(mapping)
                    or row.get("mapping_sha256") != sha256_json(dict(mapping))
                    or row.get("target_variable") != variable or row.get("target_unit") != unit
                    or type(dof_by_name.get(variable)) is not int or dof_by_name.get(variable) <= 0
                    or row.get("target_dof_count") != dof_by_name.get(variable)
                    or row.get("tuple") != tuple_value or row.get("solution_binding") != output_binding
                    or row.get("selection") != expected_selection
                    or row.get("coordinate_frame") != (dataset.get("frametype") if isinstance(dataset, Mapping) else None)
                    or not _initial_unit_controls_valid(row, variable, unit)):
                missing.append(f"{label} tuple/selection/unit/frame/DOF proof")
            raw = row.get("field_readback")
            coordinates = raw.get("coordinates") if isinstance(raw, Mapping) else None
            if (not isinstance(coordinates, Mapping)
                    or coordinates.get("coordinate_frame_status") != "VERIFIED"
                    or coordinates.get("coordinate_frame") != row.get("coordinate_frame")):
                missing.append(f"{label} actual coordinate frame")
            reference = row.get("evidence_ref")
            if (not isinstance(reference, Mapping) or not isinstance(reference.get("artifact_id"), str)
                    or not reference.get("artifact_id") or not _sha256(reference.get("sha256"))
                    or not isinstance(reference.get("file_path"), str) or not reference.get("file_path")
                    or type(reference.get("byte_size")) is not int or reference.get("byte_size") <= 0):
                missing.append(f"{label} persisted raw field artifact")
            elif not any(isinstance(item, Mapping)
                         and item.get("artifact_id") == reference.get("artifact_id")
                         and item.get("sha256") == reference.get("sha256")
                         for item in value.get("evidence_refs", [])):
                missing.append(f"{label} artifact reference is absent from the output envelope")

    # Validate operation order and revision effects independently. The daemon
    # additionally binds every request hash to its durable Worker event rows.
    chain = value.get("revision_chain")
    expected_steps: list[tuple[str, str, str]] = [
        ("study.inspect", "READ", "initial-stage-study-solver-binding"),
        ("solver.inspect", "READ", "initial-stage-active-variables"),
        ("solver.inspect", "EVALUATE", "initial-stage-active-variables-xmesh"),
        ("dataset.solution_indices", "READ", "initial-stage-output-tuple"),
        ("dataset.solution_indices", "READ", "initial-stage-solution-mesh-association"),
    ]
    if isinstance(mappings, list):
        expected_steps.extend(
            (operation, effect, readphase)
            for index, _ in enumerate(mappings)
            for operation, effect, readphase in (
                ("dataset.solution_indices", "READ", f"initial-target-field:{index}:solution-binding"),
                ("result.evaluate", "EVALUATE", f"initial-target-field:{index}"),
            )
        )
    if not isinstance(chain, list) or len(chain) != len(expected_steps):
        missing.append("complete initial managed read/evaluate revision chain")
    else:
        cursor = model_revision
        for index, (step, (expected_operation, expected_effect, expected_readphase)) in enumerate(
                zip(chain, expected_steps)):
            if not isinstance(step, Mapping):
                missing.append(f"revision chain {index}")
                continue
            operation = step.get("operation")
            effect = step.get("effect")
            delta = 1 if expected_effect == "EVALUATE" else 0
            workers = step.get("worker_requests")
            valid_workers = (isinstance(workers, list) and bool(workers)
                and all(isinstance(row, Mapping) and isinstance(row.get("worker_request_id"), str)
                        and _sha256(row.get("worker_request_hash"))
                        and type(row.get("worker_generation")) is int and row.get("phase") == "submitted"
                        for row in workers))
            if (operation != expected_operation or effect != expected_effect
                    or step.get("readphase") != expected_readphase
                    or step.get("expected_revision") != cursor or step.get("revision") != cursor + delta
                    or step.get("request_id") != step.get("child_request_id")
                    or not isinstance(step.get("child_operation_id"), str)
                    or not step.get("child_operation_id") or not valid_workers):
                missing.append(f"revision chain {index} operation/revision/Worker binding")
            if delta:
                expected_ticket_operation = (
                    "w21_initial_variables_xmesh"
                    if expected_readphase == "initial-stage-active-variables-xmesh"
                    else expected_operation.replace(".", "_")
                )
                if (step.get("revision_witness") != "managed-evaluate-ticket"
                        or not isinstance(step.get("managed_operation_id"), str)
                        or not step.get("managed_operation_id")
                        or step.get("managed_request_id") != step.get("child_request_id")
                        or not _sha256(step.get("request_hash"))
                        or step.get("managed_ticket_operation") != expected_ticket_operation):
                    missing.append(f"revision chain {index} managed Eval ticket")
            elif (step.get("revision_witness") != "service-inspect-no-ticket"
                  or step.get("managed_operation_id") is not None
                  or step.get("managed_request_id") is not None
                  or step.get("request_hash") is not None
                  or step.get("managed_ticket_operation") is not None):
                missing.append(f"revision chain {index} managed READ witness")
            cursor += delta
        if value.get("output_revision") != cursor:
            missing.append("final output revision")
    return not missing, missing


def _initial_event_index(rows: Any) -> tuple[dict[str, dict[str, list[tuple[Mapping[str, Any], Mapping[str, Any]]]]], bool]:
    """Index every durable Worker event and flag malformed or unsequenced rows."""
    indexed: dict[str, dict[str, list[tuple[Mapping[str, Any], Mapping[str, Any]]]]] = {}
    malformed = not isinstance(rows, list) or not rows
    if not isinstance(rows, list):
        return indexed, True
    previous_row_id = 0
    for row in rows:
        if not isinstance(row, Mapping) or row.get("event") != "worker_request":
            malformed = True
            continue
        row_id = row.get("id")
        if type(row_id) is not int or row_id <= previous_row_id:
            malformed = True
        elif type(row_id) is int:
            previous_row_id = row_id
        metadata = row.get("metadata")
        request_id = metadata.get("request_id") if isinstance(metadata, Mapping) else None
        phase = metadata.get("phase") if isinstance(metadata, Mapping) else None
        payload = metadata.get("metadata") if isinstance(metadata, Mapping) else None
        if (not isinstance(metadata, Mapping) or not isinstance(request_id, str) or not request_id
                or not isinstance(phase, str) or phase not in {"submitted", "observed"}
                or not isinstance(payload, Mapping) or payload.get("request_id") != request_id
                or not isinstance(metadata.get("request_hash"), str)
                or not isinstance(metadata.get("kind"), str)
                or not isinstance(metadata.get("operation_id"), str)
                or not isinstance(row.get("job_id"), str)):
            malformed = True
            continue
        indexed.setdefault(request_id, {}).setdefault(phase, []).append((row, metadata))
    return indexed, malformed


def _initial_job_event_snapshot_rows(evidence: Mapping[str, Any], *, job_id: Any
                                     ) -> tuple[bool, list[dict[str, Any]], list[dict[str, Any]]]:
    """Recompute the bounded all-event receipt and its exact Worker projection."""
    raw_rows = evidence.get("job_event_rows")
    declared_workers = evidence.get("worker_event_rows")
    collection = evidence.get("worker_event_collection")
    if not isinstance(raw_rows, list) or not isinstance(collection, Mapping):
        return False, [], []
    if any(not isinstance(row, Mapping) for row in raw_rows):
        return False, [], []
    rows = [dict(row) for row in raw_rows]
    ids = [row.get("id") for row in rows]
    if any(type(value) is not int or value <= 0 for value in ids):
        return False, rows, []
    worker_rows = [row for row in rows if row.get("event") == "worker_request"]
    workers_match = (
        isinstance(declared_workers, list)
        and all(isinstance(row, Mapping) for row in declared_workers)
        and [dict(row) for row in declared_workers] == worker_rows
    )
    count = len(rows)
    page_size = collection.get("page_size")
    max_pages = collection.get("max_page_count")
    expected_pages = collection.get("expected_page_count")
    pages = collection.get("pages")
    last_id = collection.get("snapshot_last_id")
    sentinel = collection.get("terminal_sentinel")
    if type(page_size) is not int or not 1 <= page_size <= INITIAL_JOB_EVENT_SNAPSHOT_PAGE_SIZE:
        return False, rows, worker_rows
    expected_page_count = (count + page_size - 1) // page_size if count else 0
    if (collection.get("schema") != INITIAL_JOB_EVENT_SNAPSHOT_SCHEMA
            or collection.get("status") != "COMPLETE"
            or collection.get("complete") is not True
            or collection.get("job_id") != job_id
            or type(collection.get("job_row_count")) is not int
            or collection.get("job_row_count") != 1
            or type(max_pages) is not int or max_pages != INITIAL_JOB_EVENT_SNAPSHOT_MAX_PAGES
            or type(expected_pages) is not int or expected_pages != expected_page_count
            or expected_page_count > max_pages
            or type(collection.get("expected_event_count")) is not int
            or collection.get("expected_event_count") != count
            or type(collection.get("fetched_event_count")) is not int
            or collection.get("fetched_event_count") != count
            or type(collection.get("fetched_job_event_count")) is not int
            or collection.get("fetched_job_event_count") != count
            or collection.get("limit") != page_size
            or type(collection.get("worker_request_event_count")) is not int
            or collection.get("worker_request_event_count") != len(worker_rows)
            or not workers_match
            or collection.get("event_ids") != ids
            or ids != sorted(set(ids))
            or type(last_id) is not int
            or last_id != (ids[-1] if ids else 0)
            or type(collection.get("id_sha256")) is not str
            or collection.get("id_sha256") != hashlib.sha256(
                ",".join(str(value) for value in ids).encode("ascii")
            ).hexdigest()
            or type(collection.get("events_sha256")) is not str
            or collection.get("events_sha256") != sha256_json(rows)
            or collection.get("sentinel_checked") is not True
            or collection.get("terminal_sentinel_checked") is not True
            or type(collection.get("terminal_sentinel_count")) is not int
            or collection.get("terminal_sentinel_count") != 0
            or type(collection.get("overflow_sentinel_count")) is not int
            or collection.get("overflow_sentinel_count") != 0
            or collection.get("issues") != []
            or not isinstance(sentinel, Mapping)
            or sentinel.get("checked") is not True
            or sentinel.get("job_id") != job_id
            or type(sentinel.get("snapshot_last_id")) is not int
            or sentinel.get("snapshot_last_id") != last_id
            or type(sentinel.get("offset")) is not int
            or sentinel.get("offset") != count
            or type(sentinel.get("count")) is not int
            or sentinel.get("count") != 0
            or sentinel.get("event_id") is not None
            or not isinstance(pages, list) or len(pages) != expected_page_count):
        return False, rows, worker_rows

    for index, page in enumerate(pages):
        offset = index * page_size
        page_rows = rows[offset:offset + page_size]
        page_ids = ids[offset:offset + page_size]
        if (not isinstance(page, Mapping)
                or type(page.get("index")) is not int
                or page.get("index") != index
                or type(page.get("offset")) is not int
                or page.get("offset") != offset
                or type(page.get("count")) is not int
                or page.get("count") != len(page_rows)
                or (page.get("first_id") is not None and type(page.get("first_id")) is not int)
                or page.get("first_id") != (page_ids[0] if page_ids else None)
                or (page.get("last_id") is not None and type(page.get("last_id")) is not int)
                or page.get("last_id") != (page_ids[-1] if page_ids else None)
                or page.get("id_sha256") != hashlib.sha256(
                    ",".join(str(value) for value in page_ids).encode("ascii")
                ).hexdigest()
                or page.get("events_sha256") != sha256_json(page_rows)):
            return False, rows, worker_rows
    if any(row.get("job_id") != job_id
           or not isinstance(row.get("event"), str) or not row.get("event")
           or not isinstance(row.get("metadata"), Mapping)
           for row in rows):
        return False, rows, worker_rows
    return True, rows, worker_rows


def _initial_event_generation(event: Mapping[str, Any]) -> int | None:
    """Resolve the Worker epoch from the event's managed binding or request."""
    wrapper = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else event
    candidates: list[int] = []
    for key in ("w21_stage_output_binding", "w21_backend_binding"):
        binding = wrapper.get(key) if isinstance(wrapper, Mapping) else None
        value = binding.get("worker_generation") if isinstance(binding, Mapping) else None
        if type(value) is int:
            candidates.append(value)
    payload = wrapper.get("metadata") if isinstance(wrapper, Mapping) else None
    value = payload.get("generation") if isinstance(payload, Mapping) else None
    if type(value) is int:
        candidates.append(value)
    reply = wrapper.get("reply") if isinstance(wrapper, Mapping) else None
    value = reply.get("generation") if isinstance(reply, Mapping) else None
    if type(value) is int:
        candidates.append(value)
    result = reply.get("result") if isinstance(reply, Mapping) else None
    value = result.get("generation") if isinstance(result, Mapping) else None
    if type(value) is int:
        candidates.append(value)
    unique = set(candidates)
    return next(iter(unique)) if len(unique) == 1 else None


def _initial_stage_backend_binding_matches(
    event: Mapping[str, Any], *, binding: Mapping[str, Any], operation_id: str,
    stage_request_id: str, phase: str, worker_epoch: int, model_tag: str,
    expected_revision: int, save_target_path: str | None,
) -> bool:
    """Require a root solve/save Worker event to carry its exact stage identity."""
    context = event.get("w21_backend_binding")
    payload = event.get("metadata")
    if not isinstance(context, Mapping) or not isinstance(payload, Mapping):
        return False
    expected = {
        "phase": phase,
        "model_tag": model_tag,
        "worker_generation": worker_epoch,
        "model_ref": binding.get("model_ref"),
        "project_id": binding.get("project_id"),
        "attempt_id": binding.get("attempt_id"),
        "expected_revision": expected_revision,
        "request_id": stage_request_id,
        "operation_id": operation_id,
        "binding_sha256": sha256_json(dict(binding)),
        "save_target_path": save_target_path,
    }
    if any(context.get(key) != value for key, value in expected.items()):
        return False
    kind = event.get("kind")
    model_handle = context.get("model_handle")
    if model_handle is None:
        # The exact model() command is emitted before its opaque handle exists.
        return bool(kind == "model" and payload.get("tag") == model_tag
                    and event.get("phase") in {"submitted", "observed"})
    if not isinstance(model_handle, str) or not model_handle:
        return False
    if kind == "call":
        # Study.run is invoked on the exact Study handle resolved by the
        # dispatch gate, while Model.save and auxiliary model reads use the
        # model handle. The pair validator binds each receiver to its
        # producer record or root-operation allowlist.
        return payload.get("generation") == worker_epoch
    if kind in {"model", "model_snapshot"}:
        return payload.get("tag") == model_tag
    return False


def _initial_known_tlist_failure(
    observed: Mapping[str, Any], *, request_id: str, worker_epoch: int,
) -> bool:
    """Accept only the exact optional range-expression probe's terminal failure."""
    payload = observed.get("metadata")
    reply = observed.get("reply")
    failure = reply.get("failure") if isinstance(reply, Mapping) else None
    if (not isinstance(payload, Mapping) or payload.get("type") != "call"
            or payload.get("method") != "getDoubleArray" or payload.get("args") != ["tlist"]
            or payload.get("generation") != worker_epoch
            or observed.get("phase") != "observed" or observed.get("status") != "FAILED"
            or not isinstance(reply, Mapping) or reply.get("ok") is not False
            or reply.get("status") != "FAILED" or reply.get("request_id") != request_id
            or reply.get("generation") != worker_epoch or reply.get("type") != "call"
            or not isinstance(failure, Mapping) or failure.get("code") != "ENGINE_CALL_FAILED"
            or not isinstance(failure.get("message"), str)):
        return False
    message = failure["message"].casefold()
    return "tlist" in message and "range" in message and "double array" in message


def _initial_event_pair_matches(
    phases: Mapping[str, list[tuple[Mapping[str, Any], Mapping[str, Any]]]], *,
    request_id: str, request_hash: str, kind: str, operation_id: str,
    job_id: str, worker_epoch: int, role: str, model_tag: str,
    operation: str | None = None, readphase: str | None = None,
    method: str | None = None, receiver: str | None = None,
    args: Any = None, args_required: bool = False, model_ref: Any = None,
    allow_known_tlist_failure: bool = False,
) -> bool:
    """Check one exact submitted/observed pair against its producer reference."""
    if set(phases) != {"submitted", "observed"}:
        return False
    submitted_rows, observed_rows = phases.get("submitted", []), phases.get("observed", [])
    if len(submitted_rows) != 1 or len(observed_rows) != 1:
        return False
    submitted, observed = submitted_rows[0], observed_rows[0]
    if (type(submitted[0].get("id")) is not int or type(observed[0].get("id")) is not int
            or submitted[0]["id"] >= observed[0]["id"]
            or submitted[0].get("job_id") != job_id or observed[0].get("job_id") != job_id):
        return False
    for row, event in (submitted, observed):
        payload = event.get("metadata")
        event_epoch = _initial_event_generation(row)
        if (event.get("request_id") != request_id or event.get("request_hash") != request_hash
                or event.get("kind") != kind or event.get("operation_id") != operation_id
                or not isinstance(payload, Mapping) or payload.get("request_id") != request_id
                or payload.get("type") != kind
                or event_epoch != worker_epoch):
            return False
        if kind in {"model", "model_snapshot"} and payload.get("tag") != model_tag:
            return False
        if kind == "call":
            actual_method = payload.get("method")
            actual_receiver = payload.get("handle")
            actual_args = payload.get("args")
            if (not isinstance(actual_method, str) or not actual_method
                    or not isinstance(actual_receiver, str) or not actual_receiver
                    or not isinstance(actual_args, list)
                    or (method is not None and actual_method != method)
                    or (receiver is not None and actual_receiver != receiver)
                    or (args_required and actual_args != args)
                    or payload.get("generation") != worker_epoch):
                return False
    if submitted[1].get("metadata") != observed[1].get("metadata"):
        return False
    if role in {"preflight", "managed"}:
        for _row, event in (submitted, observed):
            context = event.get("w21_stage_output_binding")
            if (not isinstance(context, Mapping)
                    or context.get("child_operation_id") != operation_id
                    or context.get("model_tag") != model_tag
                    or context.get("worker_generation") != worker_epoch
                    or context.get("operation") != operation
                    or context.get("readphase") != readphase
                    or context.get("model_ref") != model_ref):
                return False
            if role == "preflight":
                preflight_context = event.get("w21_stage_preflight")
                if (not isinstance(preflight_context, Mapping)
                        or preflight_context.get("child_operation_id") != operation_id
                        or preflight_context.get("operation") != operation
                        or preflight_context.get("readphase") != readphase):
                    return False
    elif role == "mesh":
        for _row, event in (submitted, observed):
            context = event.get("w21_stage_output")
            if (not isinstance(context, Mapping)
                    or context.get("child_operation_id") != operation_id
                    or context.get("stage_run_operation_id") is None
                    or context.get("operation") != operation
                    or context.get("readphase") != readphase):
                return False
    elif role in {"solve", "save"}:
        for _row, event in (submitted, observed):
            context = event.get("w21_backend_binding")
            if (not isinstance(context, Mapping) or context.get("phase") != role
                    or context.get("worker_generation") != worker_epoch
                    or context.get("model_tag") != model_tag
                    or not isinstance(context.get("model_handle"), str)
                    or not context.get("model_handle")):
                return False
    elif role != "auxiliary":
        return False
    reply = observed[1].get("reply")
    succeeded = bool(
        observed[1].get("phase") == "observed"
        and observed[1].get("status") == "SUCCEEDED"
        and isinstance(reply, Mapping)
        and reply.get("ok") is True
        and reply.get("status") == "SUCCEEDED"
        and reply.get("request_id") == request_id
        and reply.get("generation") == worker_epoch
    )
    if succeeded:
        return True
    return bool(allow_known_tlist_failure
                and observed[1].get("phase") == "observed"
                and _initial_known_tlist_failure(observed[1], request_id=request_id,
                                                 worker_epoch=worker_epoch))


def validate_initial_output_acceptance(
    evidence: Any, *, binding: Mapping[str, Any], stage: Mapping[str, Any],
    stage_run_operation_id: str, solve_revision: int, output_revision: int,
    observed_artifact_sha256: str,
) -> tuple[bool, list[str]]:
    """Validate the bounded initial-output result, source artifact and native read chain.

    This is intentionally scoped to the first saved initial output.  It does
    not establish physical validation, solver history, conservation, or later
    stage acceptance.
    """
    if not isinstance(evidence, Mapping):
        return False, ["initial output acceptance evidence unavailable"]
    missing: list[str] = []
    if (evidence.get("schema") != "w21-initial-output-acceptance/v1"
            or evidence.get("scope") != INITIAL_OUTPUT_ACCEPTANCE_SCOPE
            or evidence.get("stage_attempt_binding") != dict(binding)
            or evidence.get("stage_run_operation_id") != stage_run_operation_id
            or evidence.get("solve_revision") != solve_revision
            or evidence.get("output_revision") != output_revision):
        missing.append("exact initial acceptance contract and attempt binding")
    output = evidence.get("native_output_readback")
    output_ok, output_missing = validate_initial_output_readback(
        output, binding=binding, stage=stage,
        stage_run_operation_id=stage_run_operation_id,
        model_revision=solve_revision,
    )
    if not output_ok:
        missing.extend(output_missing)
    if (not isinstance(output, Mapping) or output.get("solve_revision") != solve_revision
            or output.get("output_revision") != output_revision):
        missing.append("exact solve/output revision binding")

    preflight = evidence.get("initial_stage_preflight")
    preflight_record = preflight.get("record") if isinstance(preflight, Mapping) else None
    preflight_artifact_id = preflight.get("artifact_id") if isinstance(preflight, Mapping) else None
    preflight_payload = dict(preflight_record) if isinstance(preflight_record, Mapping) else {}
    preflight_sha256 = preflight_payload.pop("sha256", None)
    target_segments = (stage.get("study_target", {}).get("segments")
                       if isinstance(stage.get("study_target"), Mapping) else None)
    study_tag = (target_segments[0].get("tag") if isinstance(target_segments, list)
                 and len(target_segments) == 1 and isinstance(target_segments[0], Mapping) else None)
    preflight_chain = preflight_record.get("revision_chain") if isinstance(preflight_record, Mapping) else None
    mappings_for_budget = stage.get("variable_mappings")
    expected_actions = [
        "study.inspect:preflight", "study.inspect:postsolve", "solver.inspect:active-variables",
        "Variables.xmeshInfo:active-variables", "dataset.solution_indices:output-tuple",
        "dataset.solution_indices:solution-mesh-association",
    ]
    if isinstance(mappings_for_budget, list):
        for index, _mapping in enumerate(mappings_for_budget):
            expected_actions.extend((f"dataset.solution_indices:field-binding:{index}",
                                     f"result.evaluate:field:{index}"))
    expected_actions.append("mesh.inspect:post-stage")
    if (not isinstance(preflight_record, Mapping)
            or preflight_artifact_id != f"w21-initial-stage-preflight:{binding.get('attempt_id')}"
            or not _sha256(preflight_sha256)
            or sha256_json(preflight_payload) != preflight_sha256
            or preflight_record.get("kind") != "w21_initial_stage_preflight"
            or preflight_record.get("binding") != dict(binding)
            or preflight_record.get("stage_id") != stage.get("stage_id")
            or preflight_record.get("study_tag") != study_tag
            or preflight_record.get("managed_internal_action_plan") != expected_actions
            or not isinstance(preflight_chain, list) or len(preflight_chain) != 1):
        missing.append("hash-verified persisted initial Study preflight record")
    else:
        step = preflight_chain[0]
        if (not isinstance(step, Mapping) or step.get("operation") != "study.inspect"
                or step.get("effect") != "READ"
                or step.get("expected_revision") != binding.get("expected_revision")
                or step.get("revision") != binding.get("expected_revision")
                or not isinstance(step.get("worker_requests"), list)
                or not step.get("worker_requests")):
            missing.append("initial preflight managed Study read chain")

    mesh = evidence.get("mesh_snapshot_readback")
    association = output.get("solution_mesh_association") if isinstance(output, Mapping) else None
    dataset = output.get("dataset_identity") if isinstance(output, Mapping) else None
    mesh_binding = mesh.get("binding") if isinstance(mesh, Mapping) else None
    mesh_ref = mesh.get("artifact_ref") if isinstance(mesh, Mapping) else None
    mesh_artifact = evidence.get("mesh_snapshot_artifact_record")
    mesh_artifact_payload = dict(mesh_artifact) if isinstance(mesh_artifact, Mapping) else {}
    mesh_artifact_sha = mesh_artifact_payload.pop("sha256", None)
    mesh_sequence = (mesh_artifact.get("resolved_mesh_sequence")
                     if isinstance(mesh_artifact, Mapping) else None)
    mesh_worker_requests = (mesh_artifact.get("worker_requests")
                            if isinstance(mesh_artifact, Mapping) else None)
    expected_mesh_path = {
        "segments": [
            {"collection": "component", "tag": dataset.get("component") if isinstance(dataset, Mapping) else None},
            {"collection": "mesh", "tag": association.get("mesh_tag") if isinstance(association, Mapping) else None},
        ],
    }
    if (not isinstance(mesh, Mapping)
            or mesh.get("contract") != "w21-stage-current-mesh-readback/v1"
            or mesh.get("producer") != "managed-backend-current-mesh-readback"
            or mesh.get("status") != "CURRENT_MESH_CAPTURE_ONLY"
            or mesh.get("historical_mesh") != "UNVERIFIED"
            or mesh.get("source_target_mapping") != "UNVERIFIED"
            or mesh.get("frame_identity") != "UNVERIFIED"
            or mesh.get("dof_identity") != "UNVERIFIED"
            or not isinstance(mesh_binding, Mapping)
            or mesh_binding.get("attempt_id") != binding.get("attempt_id")
            or mesh_binding.get("model_ref") != binding.get("model_ref")
            or mesh_binding.get("revision") != output_revision
            or mesh_binding.get("phase") != "post-stage"
            or mesh_binding.get("geometry") != (dataset.get("geometry") if isinstance(dataset, Mapping) else None)
            or mesh_binding.get("component") != (dataset.get("component") if isinstance(dataset, Mapping) else None)
            or mesh_binding.get("mesh") != (association.get("mesh_tag") if isinstance(association, Mapping) else None)
            or mesh.get("attempt_binding") != dict(binding)
            or mesh.get("model_revision") != output_revision
            or not isinstance(mesh.get("mesh_snapshot"), Mapping)
            or not isinstance(mesh.get("artifact_ref"), Mapping)
            or not _sha256(mesh_ref.get("sha256") if isinstance(mesh_ref, Mapping) else None)
            or not isinstance(mesh_artifact, Mapping)
            or mesh_artifact.get("schema") != "w21-stage-current-mesh-readback/v1"
            or mesh_artifact.get("capture_status") != "CAPTURED"
            or mesh_artifact.get("claim_scope") != "CURRENT_MESH_CAPTURE_ONLY"
            or mesh_artifact.get("historical_mesh") != "UNVERIFIED"
            or mesh_artifact.get("source_target_mapping") != "UNVERIFIED"
            or mesh_artifact.get("frame_identity") != "UNVERIFIED"
            or mesh_artifact.get("dof_identity") != "UNVERIFIED"
            or not _sha256(mesh_artifact_sha)
            or sha256_json(mesh_artifact_payload) != mesh_artifact_sha
            or mesh_ref.get("sha256") != mesh_artifact_sha
            or mesh_ref.get("artifact_id") != f"w21-current-mesh:{mesh_artifact_sha}"
            or mesh_artifact.get("project_id") != binding.get("project_id")
            or mesh_artifact.get("model_ref") != dict(binding.get("model_ref", {}))
            or mesh_artifact.get("attempt_binding") != dict(binding)
            or mesh_artifact.get("attempt_status") != "RUNNING"
            or mesh_artifact.get("observed_revision") != output_revision
            or mesh_artifact.get("phase") != "post-stage"
            or not isinstance(mesh_artifact.get("child_operation_id"), str)
            or not mesh_artifact.get("child_operation_id")
            or mesh_artifact.get("worker_generation") != mesh.get("worker_generation")
            or mesh_artifact.get("mesh_snapshot") != mesh.get("mesh_snapshot")
            or not isinstance(mesh_sequence, Mapping)
            or mesh_sequence.get("path") != expected_mesh_path
            or mesh_sequence.get("component") != expected_mesh_path["segments"][0]["tag"]
            or mesh_sequence.get("mesh") != expected_mesh_path["segments"][1]["tag"]
            or mesh_sequence.get("geometry") != (dataset.get("geometry") if isinstance(dataset, Mapping) else None)
            or not isinstance(mesh_sequence.get("receiver_handle"), str)
            or not mesh_sequence.get("receiver_handle")
            or not isinstance(mesh_worker_requests, list)
            or not mesh_worker_requests):
        missing.append("actual current mesh snapshot bound to the selected Solution mesh")
    else:
        # The managed mesh-read result is a compact summary. Its hash-bound
        # OperationStore artifact carries the resolved receiver and submit/
        # observe request pairs; bind those pairs back to the durable stage job.
        durable_event_phases: dict[tuple[str, str], Mapping[str, Any]] = {}
        for row in evidence.get("worker_event_rows", []) if isinstance(evidence.get("worker_event_rows"), list) else []:
            if not isinstance(row, Mapping) or row.get("event") != "worker_request":
                continue
            actual = row.get("metadata")
            if not isinstance(actual, Mapping):
                continue
            request_id, phase = actual.get("request_id"), actual.get("phase")
            if not isinstance(request_id, str) or phase not in {"submitted", "observed"}:
                continue
            key = (request_id, phase)
            if key in durable_event_phases:
                durable_event_phases = {}
                break
            durable_event_phases[key] = actual
        mesh_requests_by_id: dict[str, dict[str, Mapping[str, Any]]] = {}
        for row in mesh_worker_requests:
            if (not isinstance(row, Mapping)
                    or row.get("phase") not in {"submitted", "observed"}
                    or not isinstance(row.get("request_id"), str)
                    or not _sha256(row.get("request_hash"))
                    or not isinstance(row.get("metadata"), Mapping)):
                missing.append("current mesh snapshot Worker event binding")
                break
            request_id, phase = row["request_id"], row["phase"]
            if phase in mesh_requests_by_id.setdefault(request_id, {}):
                missing.append("current mesh snapshot duplicate Worker event")
                break
            mesh_requests_by_id[request_id][phase] = row
            actual = durable_event_phases.get((request_id, phase))
            if (not isinstance(actual, Mapping)
                    or actual.get("request_hash") != row.get("request_hash")
                    or actual.get("kind") != row.get("kind")
                    or actual.get("operation_id") != mesh_artifact.get("child_operation_id")
                    or actual.get("metadata") != row.get("metadata")
                    or actual.get("status") != (
                        row.get("reply", {}).get("status")
                        if isinstance(row.get("reply"), Mapping) else None
                    )):
                missing.append("current mesh snapshot request differs from durable Worker event")
                break
            context = actual.get("w21_stage_output")
            if (not isinstance(context, Mapping)
                    or context.get("stage_run_operation_id") != stage_run_operation_id
                    or context.get("operation") != "mesh.inspect"
                    or context.get("readphase") != "post-stage"
                    or context.get("expected_revision") != output_revision
                    or context.get("child_operation_id") != mesh_artifact.get("child_operation_id")):
                missing.append("current mesh Worker event is not bound to the exact post-stage read")
                break
        for request_id, phases in mesh_requests_by_id.items():
            if (set(phases) != {"submitted", "observed"}
                    or phases["submitted"].get("request_hash") != phases["observed"].get("request_hash")
                    or not isinstance(phases["observed"].get("reply"), Mapping)
                    or phases["observed"]["reply"].get("status") != "SUCCEEDED"):
                missing.append("current mesh Worker submit/observe pair is incomplete")
                break

    chain = output.get("revision_chain") if isinstance(output, Mapping) else None
    # The initial-stage runner profile counts managed observations, including
    # the nested Variables.xmeshInfo read and the final mesh snapshot.  The
    # generic stored-plan route itself does not impose a global 12-action
    # limit; the runner profile carries that frozen cap explicitly.
    read_count = len(preflight_chain) if isinstance(preflight_chain, list) else 0
    read_count += len(chain) if isinstance(chain, list) else 0
    read_count += 1 if isinstance(mesh, Mapping) else 0
    read_cap = evidence.get("internal_read_cap")
    if (evidence.get("managed_internal_action_plan") != expected_actions
            or (isinstance(output, Mapping)
                and output.get("managed_internal_action_plan") != expected_actions)
            or (not isinstance(preflight_record, Mapping)
                or preflight_record.get("internal_read_cap") != evidence.get("internal_read_cap"))
            or evidence.get("internal_read_count") != read_count
            or (read_cap is not None and (read_cap != 12 or read_count > read_cap))):
        missing.append("complete managed-action count and any frozen runner-profile cap")

    dispatches = evidence.get("solve_save_dispatches")
    if (not isinstance(dispatches, list) or len(dispatches) != 2
            or [row.get("operation") for row in dispatches if isinstance(row, Mapping)] != ["run_study", "save_model"]
            or any(not isinstance(row, Mapping) or row.get("kind") != "worker-dispatch"
                   or row.get("phase") != "submitted" or not _sha256(row.get("worker_request_hash"))
                   or not isinstance(row.get("worker_request_id"), str)
                   or type(row.get("worker_generation")) is not int
                   for row in dispatches)):
        missing.append("exact submitted solve/save Worker dispatch records")

    # Root solve/save event bindings use the expected pre-dispatch revision.
    # Derive it from the frozen attempt input and the validated output chain;
    # never let an auxiliary event supply the value used to validate itself.
    root_binding_revisions: dict[str, int] = {}
    if type(binding.get("expected_revision")) is int:
        root_binding_revisions["solve"] = binding["expected_revision"]
    if type(output_revision) is int:
        root_binding_revisions["save"] = output_revision
    if isinstance(dispatches, list):
        for phase, operation in (("solve", "run_study"), ("save", "save_model")):
            matching = [row for row in dispatches
                        if isinstance(row, Mapping) and row.get("operation") == operation]
            expected_revision = root_binding_revisions.get(phase)
            if (len(matching) != 1 or type(expected_revision) is not int
                    or matching[0].get("revision") != expected_revision):
                missing.append("solve/save dispatch revision matches its independent stage revision")

    worker_job_id = evidence.get("worker_job_id")
    worker_epoch = evidence.get("worker_epoch")
    collection_valid, _complete_job_event_rows, event_rows = _initial_job_event_snapshot_rows(
        evidence, job_id=worker_job_id,
    )
    event_index, malformed_events = _initial_event_index(event_rows)
    if not collection_valid:
        missing.append("complete bounded exact-job event snapshot and Worker projection")
    if (not isinstance(event_rows, list) or not event_rows or malformed_events
            or not isinstance(worker_job_id, str) or not worker_job_id
            or type(worker_epoch) is not int or worker_epoch < 1):
        missing.append("complete, ordered durable Worker lifecycle with exact job and epoch")
    else:
        model_ref = binding.get("model_ref")
        model_tag = model_ref.get("model_tag") if isinstance(model_ref, Mapping) else None
        specs: dict[str, dict[str, Any]] = {}
        required_order: list[str] = []

        def add_spec(request_id: Any, spec: dict[str, Any]) -> None:
            if not isinstance(request_id, str) or not request_id or request_id in specs:
                missing.append("unique Worker request references in initial acceptance evidence")
                return
            specs[request_id] = spec
            required_order.append(request_id)

        def add_chain_specs(rows: Any, *, role: str) -> None:
            if not isinstance(rows, list):
                return
            for step in rows:
                if not isinstance(step, Mapping) or not isinstance(step.get("worker_requests"), list):
                    missing.append("managed Worker request list in initial revision chain")
                    continue
                child_operation_id = step.get("child_operation_id")
                operation = step.get("operation")
                readphase = step.get("readphase")
                for request in step["worker_requests"]:
                    if not isinstance(request, Mapping):
                        missing.append("typed managed Worker request reference")
                        continue
                    add_spec(request.get("worker_request_id"), {
                        "hash": request.get("worker_request_hash"),
                        "kind": request.get("worker_kind"),
                        "operation_id": child_operation_id,
                        "job_id": worker_job_id,
                        "epoch": request.get("worker_generation"),
                        "role": role,
                        # Call rows bind the model through their managed
                        # sidecar and receiver; their compact request record
                        # leaves worker_model_tag null by design.
                        "model_tag": request.get("worker_model_tag") or model_tag,
                        "operation": operation,
                        "readphase": readphase,
                        "method": request.get("worker_method"),
                        "receiver": request.get("worker_receiver"),
                        "model_ref": model_ref,
                        "allow_known_tlist_failure": (
                            role in {"preflight", "managed"}
                            and request.get("worker_kind") == "call"
                            and request.get("worker_method") == "getDoubleArray"
                        ),
                    })

        add_chain_specs(preflight_chain, role="preflight")
        solve_id = None
        save_id = None
        if isinstance(dispatches, list) and len(dispatches) == 2:
            for dispatch in dispatches:
                if not isinstance(dispatch, Mapping):
                    continue
                operation = dispatch.get("operation")
                role = "solve" if operation == "run_study" else "save" if operation == "save_model" else "invalid"
                request_id = dispatch.get("worker_request_id")
                add_spec(request_id, {
                    "hash": dispatch.get("worker_request_hash"),
                    "kind": "call",
                    "operation_id": stage_run_operation_id,
                    "job_id": worker_job_id,
                    "epoch": dispatch.get("worker_generation"),
                    "role": role,
                    "model_tag": model_tag,
                    "method": dispatch.get("worker_method"),
                    "receiver": dispatch.get("worker_receiver"),
                    "args": dispatch.get("worker_args"),
                    "args_required": True,
                    "stage_request_id": dispatch.get("stage_request_id"),
                    "backend_expected_revision": dispatch.get("revision"),
                    "save_target_path": (
                        evidence.get("saved_artifact", {}).get("path")
                        if role == "save" and isinstance(evidence.get("saved_artifact"), Mapping)
                        else None
                    ),
                })
                if role == "solve":
                    solve_id = request_id
                elif role == "save":
                    save_id = request_id

        add_chain_specs(chain, role="managed")
        mesh_rows = mesh_artifact.get("worker_requests") if isinstance(mesh_artifact, Mapping) else None
        mesh_ids: list[str] = []
        if isinstance(mesh_rows, list):
            for request in mesh_rows:
                if not isinstance(request, Mapping) or request.get("phase") != "submitted":
                    continue
                payload = request.get("metadata")
                if not isinstance(payload, Mapping):
                    missing.append("mesh Worker request payload")
                    continue
                request_id = request.get("request_id")
                if isinstance(request_id, str):
                    mesh_ids.append(request_id)
                add_spec(request_id, {
                    "hash": request.get("request_hash"),
                    "kind": request.get("kind"),
                    "operation_id": mesh_artifact.get("child_operation_id"),
                    "job_id": worker_job_id,
                    "epoch": mesh_artifact.get("worker_generation"),
                    "role": "mesh",
                    "model_tag": model_tag,
                    "operation": "mesh.inspect",
                    "readphase": "post-stage",
                    "method": payload.get("method"),
                    "receiver": payload.get("handle"),
                    "model_ref": model_ref,
                })

        for request_id, spec in specs.items():
            if (not _sha256(spec.get("hash")) or spec.get("job_id") != worker_job_id
                    or spec.get("epoch") != worker_epoch or not isinstance(spec.get("operation_id"), str)
                    or not spec.get("operation_id") or spec.get("model_tag") != model_tag):
                missing.append("Worker request reference is bound to the exact job, operation, and epoch")
                continue
            phases = event_index.get(request_id)
            pair_matches = isinstance(phases, Mapping) and _initial_event_pair_matches(
                phases, request_id=request_id, request_hash=spec["hash"], kind=spec["kind"],
                operation_id=spec["operation_id"], job_id=spec["job_id"],
                worker_epoch=spec["epoch"], role=spec["role"], model_tag=spec["model_tag"],
                operation=spec.get("operation"), readphase=spec.get("readphase"),
                method=spec.get("method"), receiver=spec.get("receiver"),
                args=spec.get("args"), args_required=bool(spec.get("args_required")),
                model_ref=spec.get("model_ref"),
                allow_known_tlist_failure=bool(spec.get("allow_known_tlist_failure")),
            )
            if pair_matches and spec["role"] in {"solve", "save"}:
                backend_expected_revision = spec.get("backend_expected_revision")
                if type(backend_expected_revision) is not int:
                    pair_matches = False
                else:
                    for _row, event in phases.get("submitted", []) + phases.get("observed", []):
                        if not _initial_stage_backend_binding_matches(
                                event, binding=binding, operation_id=stage_run_operation_id,
                                stage_request_id=spec.get("stage_request_id"), phase=spec["role"],
                                worker_epoch=spec["epoch"], model_tag=spec["model_tag"],
                                expected_revision=backend_expected_revision,
                                save_target_path=spec.get("save_target_path")):
                            pair_matches = False
                            break
            if not pair_matches:
                missing.append("unique correlated successful Worker submit/observe pair for every required action")

        # Resolve the root Model receiver separately for each managed phase.
        # The required Study.run/Model.save dispatch ids identify the exact
        # event pair whose backend binding owns the phase's Model handle.
        # Auxiliary calls cannot establish their own receiver identity.
        phase_model_handles: dict[str, str] = {}
        if isinstance(dispatches, list):
            for phase, operation in (("solve", "run_study"), ("save", "save_model")):
                matching = [row for row in dispatches
                            if isinstance(row, Mapping) and row.get("operation") == operation]
                if len(matching) != 1:
                    continue
                dispatch = matching[0]
                request_id = dispatch.get("worker_request_id")
                phases = event_index.get(request_id) if isinstance(request_id, str) else None
                expected_revision = root_binding_revisions.get(phase)
                saved_artifact = evidence.get("saved_artifact")
                save_target_path = (saved_artifact.get("path")
                                    if phase == "save" and isinstance(saved_artifact, Mapping) else None)
                if (not isinstance(phases, Mapping) or set(phases) != {"submitted", "observed"}
                        or len(phases.get("submitted", [])) != 1 or len(phases.get("observed", [])) != 1
                        or type(expected_revision) is not int):
                    continue
                handles: list[str] = []
                binding_valid = True
                for _row, event in phases["submitted"] + phases["observed"]:
                    context = event.get("w21_backend_binding")
                    if (not isinstance(context, Mapping)
                            or not _initial_stage_backend_binding_matches(
                                event, binding=binding, operation_id=stage_run_operation_id,
                                stage_request_id=dispatch.get("stage_request_id"), phase=phase,
                                worker_epoch=worker_epoch, model_tag=model_tag,
                                expected_revision=expected_revision, save_target_path=save_target_path,
                            )):
                        binding_valid = False
                        break
                    model_handle = context.get("model_handle")
                    if not isinstance(model_handle, str) or not model_handle:
                        binding_valid = False
                        break
                    if phase == "save" and event.get("metadata", {}).get("handle") != model_handle:
                        binding_valid = False
                        break
                    handles.append(model_handle)
                if (binding_valid and len(handles) == 2 and len(set(handles)) == 1
                        and (phase != "save" or dispatch.get("worker_receiver") == handles[0])):
                    phase_model_handles[phase] = handles[0]
        if set(phase_model_handles) != {"solve", "save"}:
            missing.append("correlated solve/save Worker dispatches resolve exact phase Model handles")

        required_ids = set(specs)
        actual_ids = set(event_index)
        auxiliary_rows = evidence.get("worker_auxiliary_requests")
        declared_aux_ids = ([row.get("worker_request_id") for row in auxiliary_rows
                             if isinstance(row, Mapping)] if isinstance(auxiliary_rows, list) else [])
        extra_ids = actual_ids - required_ids
        if (not isinstance(auxiliary_rows, list) or len(declared_aux_ids) != len(auxiliary_rows)
                or len(set(declared_aux_ids)) != len(declared_aux_ids)
                or set(declared_aux_ids) != extra_ids):
            missing.append("explicit classification of every auxiliary Worker request")
        else:
            for request_id in declared_aux_ids:
                phases = event_index.get(request_id)
                if not isinstance(phases, Mapping) or set(phases) != {"submitted", "observed"}:
                    missing.append("auxiliary Worker request has a complete lifecycle")
                    continue
                pair = phases.get("submitted", [])
                if len(pair) != 1:
                    missing.append("unique auxiliary Worker submission")
                    continue
                _row, event = pair[0]
                payload = event.get("metadata")
                safe = (event.get("operation_id") == stage_run_operation_id
                        and event.get("request_hash") is not None
                        and event.get("kind") in {"model", "model_snapshot", "call"})
                if isinstance(payload, Mapping) and event.get("kind") in {"model", "model_snapshot"}:
                    safe = safe and payload.get("tag") == model_tag
                elif isinstance(payload, Mapping) and event.get("kind") == "call":
                    method_name = payload.get("method")
                    handle = payload.get("handle")
                    method_args = payload.get("args")
                    expected_args = [] if method_name in {"getFilePath", "label"} else [study_tag]
                    context = event.get("w21_backend_binding")
                    phase = context.get("phase") if isinstance(context, Mapping) else None
                    expected_model_handle = phase_model_handles.get(phase)
                    safe = (safe and method_name in {"getFilePath", "label", "study"}
                            and method_args == expected_args
                            and isinstance(handle, str) and bool(handle)
                            and isinstance(expected_model_handle, str)
                            and handle == expected_model_handle
                            and payload.get("generation") == worker_epoch)
                else:
                    safe = False
                if not safe or not _initial_event_pair_matches(
                        phases, request_id=request_id, request_hash=event.get("request_hash"),
                        kind=event.get("kind"), operation_id=stage_run_operation_id,
                        job_id=worker_job_id, worker_epoch=worker_epoch, role="auxiliary",
                        model_tag=model_tag):
                    missing.append("auxiliary Worker request is outside the safe root-operation allowlist")
                    continue
                for _row, terminal in phases.get("submitted", []) + phases.get("observed", []):
                    backend_binding = terminal.get("w21_backend_binding")
                    phase = backend_binding.get("phase") if isinstance(backend_binding, Mapping) else None
                    expected_revision = root_binding_revisions.get(phase)
                    if (terminal.get("kind") == "call"
                            and (not isinstance(backend_binding, Mapping)
                                 or backend_binding.get("model_handle") != phase_model_handles.get(phase)
                                 or terminal.get("metadata", {}).get("handle") != phase_model_handles.get(phase))):
                        missing.append("auxiliary Worker receiver matches the correlated Model handle for its phase")
                        break
                    stage_request_id = (f"{binding.get('attempt_id')}:{phase}"
                                        if phase in {"solve", "save"} else None)
                    saved_artifact_record = evidence.get("saved_artifact")
                    save_target_path = (saved_artifact_record.get("path")
                                        if phase == "save" and isinstance(saved_artifact_record, Mapping) else None)
                    if (phase not in {"solve", "save"}
                            or type(expected_revision) is not int
                            or not isinstance(backend_binding, Mapping)
                            or backend_binding.get("expected_revision") != expected_revision
                            or not _initial_stage_backend_binding_matches(
                                terminal, binding=binding, operation_id=stage_run_operation_id,
                                stage_request_id=stage_request_id, phase=phase,
                                worker_epoch=worker_epoch, model_tag=model_tag,
                                expected_revision=expected_revision, save_target_path=save_target_path)):
                        missing.append("auxiliary root Worker request has the exact stage binding")
                        break

        if solve_id is not None and save_id is not None:
            # The canonical order is preflight -> solve -> output read chain ->
            # current mesh snapshot -> atomic save. Root-scoped auxiliary reads
            # may interleave, but cannot replace or reorder these actions.
            preflight_ids = [request.get("worker_request_id") for step in preflight_chain
                             if isinstance(step, Mapping)
                             for request in step.get("worker_requests", [])
                             if isinstance(request, Mapping)] if isinstance(preflight_chain, list) else []
            output_ids = [request.get("worker_request_id") for step in chain
                          if isinstance(step, Mapping)
                          for request in step.get("worker_requests", [])
                          if isinstance(request, Mapping)] if isinstance(chain, list) else []
            ordered_required_ids = [*preflight_ids, solve_id, *output_ids, *mesh_ids, save_id]
            if (any(not isinstance(request_id, str) for request_id in ordered_required_ids)
                    or len(set(ordered_required_ids)) != len(ordered_required_ids)):
                missing.append("unique ordered initial-stage Worker action sequence")
            else:
                prior_observed_id = 0
                for request_id in ordered_required_ids:
                    phases = event_index.get(request_id)
                    submits = phases.get("submitted", []) if isinstance(phases, Mapping) else []
                    observes = phases.get("observed", []) if isinstance(phases, Mapping) else []
                    if (len(submits) != 1 or len(observes) != 1
                            or type(submits[0][0].get("id")) is not int
                            or type(observes[0][0].get("id")) is not int
                            or submits[0][0]["id"] <= prior_observed_id):
                        missing.append("initial-stage Worker actions are not in required order")
                        break
                    prior_observed_id = observes[0][0]["id"]

        if evidence.get("raw_worker_rpc_count") != len(event_index):
            missing.append("actual unique submitted raw Worker RPC count")

    artifact = evidence.get("saved_artifact")
    saved_hash = artifact.get("sha256") if isinstance(artifact, Mapping) else None
    if (not isinstance(artifact, Mapping) or not isinstance(artifact.get("path"), str)
            or not artifact.get("path") or not _sha256(saved_hash)
            or artifact.get("sha256") != observed_artifact_sha256
            or evidence.get("saved_artifact_observed_sha256") != observed_artifact_sha256
            or type(artifact.get("size")) is not int or artifact.get("size") <= 0):
        missing.append("saved source artifact bytes/hash/size")
    return not missing, missing


def hash_saved_artifact(path: Any) -> tuple[str, int]:
    """Hash the exact saved file after project-root containment was checked."""
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
            size += len(chunk)
    return hasher.hexdigest(), size
