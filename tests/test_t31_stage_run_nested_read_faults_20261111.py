"""T31/U07: extra ``unknown_result`` read-fault fences for ``experiment.stage_run``.

Task-owned, offline, never executed by the author of this file.  This is a
companion to ``tests/test_t31_stage_run_nested_unknown_20261111.py`` for the
four additional nested fault shapes in the inner UNKNOWN projection of
``ControlDaemon._execute_admitted_experiment_stage``:

  1. after the (fake) dispatch witness is durable, the FIRST durable read of
     ``unknown_result`` raises ``StagePlanStoreConflict``;
  2. the same point raises ``ExecutionContractError``;
  3. the SECOND durable read of ``unknown_result`` returns ``None``;
  4. the second read raises and the read fault stays armed (sticky) for
     subsequent reads from ``unknown_result``; the outer generic handler's
     separately scoped recovery read is not faulted by this fixture.

The behavioral expectations are the REQUIRED post-fix contract for the
candidate2 fix (parent adjudication:
``docs/universal_mcp_execution/evidence/full/T31-stage-run-candidate2-review-parent-adjudication-20261111.md``).
The pinned pre-fix preimage
``_state/t31-stage-run-nested-unknown-parent-20261111/control_before_stage_run_fix.py.txt``
(SHA-256 quoted by the parent task as
6114cfe98412abe7d9b7f137978ca6273effe05e5ba72ff34da411ea7a501eaa) is kept
only as background for where the injected faults fire.  This task performed
no hashing, ran nothing, and makes no claim about the current live
candidate, which the parent may change later.  Harness technique is an independent
re-copy of the parent module's proven scaffolding (plan registration,
synthetic predecessor seeding, inert receiver, inline scheduler, fake
backend emitting real-shaped Worker events, controlled store-subclass
monkeypatch); nothing from that test module is imported or run.  No real
process, thread, Worker, SSH, COMSOL or filesystem state is involved.

Source anchors in the pinned preimage (``_control_daemon.py`` line numbers):
  * 2948-2956  durable ``store.begin`` + reused-key short circuit (replay
               returns the stored result without re-admission)
  * 3174       call into ``_execute_admitted_experiment_stage``
  * 3181-3186  inner ``except StagePlanStoreConflict``: ``_error(exc.code, ...)``
               with hardcoded ``engine_dispatched=False`` + ``stage_attempt_created``,
               then ``_finish(record, result, "FAILED")``
  * 3187-3188  inner ``except ExecutionContractError``: ``_exception(exc)``
               (empty ``data``), then ``_finish(..., "FAILED")``
  * 3189-3210  generic ``except Exception``: 3190 ``_log_exception``;
               3193-3202 stale-attempt CAS (rejected, swallowed at 3201);
               3203-3209 response hardcodes ``engine_dispatched=False`` and
               embeds the stale local ``attempt``; finish status UNKNOWN
  * 3415-3444  solve-phase durable read + ``RUNNING``/``engine_dispatched=True``
               ``worker-dispatch`` durable write (the fake witness)
  * 3446-3473  ``unknown_result``: durable read #1 at 3447; guarded UNKNOWN
               write 3449-3464 (inner ``except Exception: pass`` at 3464);
               durable read #2 at 3466; response at 3467-3473 computes
               ``engine_dispatched = bool(final_attempt and ...)`` — a
               ``None`` read silently becomes a False claim; finish UNKNOWN
  * 3475-3499  ``DISPATCH_INTENT`` durable write before the one
               ``backend.invoke`` at 3521-3525
  * 4053-4054  ``except Exception as exc: return unknown_result(exc)`` — an
               error raised INSIDE ``unknown_result`` escapes this handler
               and lands in one of the preflight branches above
  * 13601-13608 ``_error`` (``safe_retry`` defaults False) / ``_exception``
               (``error = exc.as_dict()``, ``data = {}``)
Store anchors in ``comsol_mcp/_operation_store.py``:
  * 92-96      ``STAGE_ATTEMPT_TRANSITIONS`` (RUNNING->UNKNOWN allowed;
               UNKNOWN has NO outgoing transition)
  * 116-121    ``StagePlanStoreConflict(code, message)``
  * 1870-1934  ``finish`` keeps UNKNOWN durable-nonterminal and retains the
               result envelope (replay path at 2953-2956 returns it)
  * 4673-4752  ``update_stage_attempt`` CAS: 4699-4700 version CAS;
               4703-4704 transition guard; 4705-4706 "engine dispatch
               evidence cannot be cleared"; 4725-4726 evidence append-only
Contract anchors in ``comsol_mcp/_execution_contract.py``:
  * 23-29      ``ExecutionContractError(code, message, *, safe_retry=False)``
  * 41-45      ``as_dict()``

What is REAL (product code actually executed): the whole
``_dispatch_experiment_stage_run`` route with real ``validate_call`` /
``canonical_request_hash`` / identity gates, the real durable attempt API,
the real native-admission gate, the real ``StageWorkerDispatchGate``, the
real ``unknown_result``, all three real preflight ``except`` branches, and a
brand-new ``OperationStore(":memory:")`` doing all admission/CAS/idempotency.
The real CAS guards are probed directly to prove the synthetic dispatch
witness cannot be cleared or rewritten.

What is FAKE (bounded scaffolding, never an engine/authorization claim):
no ``ControlDaemon`` instance; inert ``_StageRunReceiver`` with sentinels for
authorization/session context; an inline scheduler (proves reachability only,
not threading); a fake backend emitting synthetic ``wrk-`` Worker *events*
marked ``T31-TEST-ONLY-FAKE-*`` — a recorded fake dispatch witness, NOT a
real engine RPC; fake admission is a ``TEST_FIXTURE_ONLY_FAKE_PROOF``; the
ACCEPTED predecessor is seeded through the store's own DB handle with an
explicit synthetic marker.

EXPECTATIONS (this file was written, not executed; assertions state the
REQUIRED contract against the fixed candidate, per the parent adjudication):
  * Tests 1/2 (first durable read inside ``unknown_result`` raises a real
    StagePlanStoreConflict / ExecutionContractError): a typed fault raised
    while reporting UNKNOWN now routes through the existing generic UNKNOWN
    handler, so the envelope must be EXECUTION_STATE_UNKNOWN carrying the
    injected exception's own class as ``error.type``, ``safe_retry=False``,
    operation status UNKNOWN, and exactly one generic-branch log entry.
    The preimage's injected-code envelope, hardcoded
    ``engine_dispatched=False`` / empty ``data`` maps and silent unlogged
    path were failure characteristics of the fault and are no longer
    asserted as facts; the durable-witness, non-clearable-CAS and
    same-key replay assertions stay.
  * Test 3 (second read -> None): finish UNKNOWN + ``safe_retry=False``
    (preimage GREEN) plus the honest projection: an unreadable final
    attempt must appear as an explicit ``None`` with an explicit ``None``
    ``engine_dispatched`` — never a ``bool(None and ...) == False`` claim
    (the preimage projection was RED).
  * Test 4 (second read raises, sticky): the escaping read failure reaches
    the generic 3189 branch (one log + chained traceback evidence, stale
    CAS rejected); finish UNKNOWN + safe_retry=False must hold, an attempt
    embedded in the response must equal the durable row, and the
    engine_dispatched claim must agree with that attempt.

Recovery/verification reads in this file use ``store.original_get_stage_attempt``
(the un-replaced ``OperationStore`` getter bound to the same instance), never
the fault-injecting override; the helpers assert those reads produce no new
injection bookkeeping, proving the original bound getter was used.
"""
from __future__ import annotations

import contextlib
import threading
import traceback
from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import ExecutionContractError, model_ref_from_mapping
from comsol_mcp._java_worker import _request_hash
from comsol_mcp._operation_store import OperationStore, StagePlanStoreConflict
from comsol_mcp._stage_contract import (
    canonical_json,
    sha256_json,
    validate_stage_plan_definition,
)
from comsol_mcp._w21_stage_backend import ADMISSION_CONTRACT

PROJECT_ID = "project-t31-nested-read-faults"
SESSION_ID = "session-t31-nested-read-faults"
STAGE_ID = "cooldown"
PREDECESSOR_STAGE_ID = "preheat"
STUDY_TAG = "std2"
WORKER_GENERATION = 71
MODEL_REF_INPUT = {
    "schema_version": 1,
    "session_id": SESSION_ID,
    "server_instance_id": "server-t31-nested-read-faults",
    "model_tag": "model-t31-nested-read-faults",
    "generation": 1,
}
MODEL_REF = model_ref_from_mapping(MODEL_REF_INPUT).as_dict()
FAKE_MODEL_HANDLE = "T31-TEST-ONLY-FAKE-MODEL-HANDLE"
FAKE_STUDY_HANDLE = "T31-TEST-ONLY-FAKE-STUDY-HANDLE"

_MISSING = object()


class InjectedSolveResponseLoss(RuntimeError):
    """Injected ordinary fault #1: loss of the (synthetic) solve response.

    Not an ``ExecutionContractError``/``StagePlanStoreConflict``, so the
    inner ``except Exception`` at preimage 4053 routes it to
    ``unknown_result``.
    """


class InjectedStageConflictAtFirstRead(StagePlanStoreConflict):
    """Fault #2 of test 1: a real StagePlanStoreConflict subtype raised by
    the durable read at preimage 3447, distinguishable by traceback name and
    by its injected-only code."""

    INJECTED_CODE = "T31_TEST_ONLY_FIRST_READ_STAGE_CONFLICT"


class InjectedContractFaultAtFirstRead(ExecutionContractError):
    """Fault #2 of test 2: a real ExecutionContractError subtype raised at
    the same read; the fix routes this escaping typed fault into the
    generic UNKNOWN handler instead of the preimage 3187 branch."""

    INJECTED_CODE = "T31_TEST_ONLY_FIRST_READ_CONTRACT_FAULT"


class InjectedDurableReadFailure(RuntimeError):
    """Fault #2 of test 4: sticky durable-read failure inside
    ``unknown_result`` that must escape into the generic branch at 3189."""


# --------------------------------------------------------------------------
# Synthetic plan / durable seeding (real store APIs, test-only content)
# --------------------------------------------------------------------------

def _stage_plan_definition() -> dict[str, Any]:
    """Synthetic v2 two-stage declaration (shape re-derived from the parent
    module's plan; no T14 geometry/membership and no W13 selection_measure
    result is sought anywhere in this file)."""
    return {
        "version": 2,
        "plan_id": "t31-nested-read-faults-plan",
        "stages": [
            {
                "stage_id": PREDECESSOR_STAGE_ID,
                "ordinal": 1,
                "depends_on": [],
                "study_target": {"segments": [{"collection": "study", "tag": "std1"}]},
                "source_selection": {"kind": "initial_state", "strategy": "declared_initial"},
                "target_selection": {"dataset": "dset1", "solution": "sol1"},
                "mapping_profile": "same_name_same_mesh_initialization",
                "variable_mappings": [{
                    "source_variable": "T", "target_variable": "T", "source_unit": "K",
                    "target_unit": "K", "mapping_method": "identity",
                }],
                "reference_state": {"strategy": "initial_state"},
                "checks": [],
            },
            {
                "stage_id": STAGE_ID,
                "ordinal": 2,
                "depends_on": [PREDECESSOR_STAGE_ID],
                "study_target": {"segments": [{"collection": "study", "tag": STUDY_TAG}]},
                "source_selection": {
                    "kind": "stage", "stage_id": PREDECESSOR_STAGE_ID,
                    "selection": {
                        "dataset": "dset1", "solution": "sol1", "outer": [1], "inner": [2],
                    },
                },
                "target_selection": {
                    "dataset": "dset2", "solution": "sol2", "outer": [1], "inner": [1],
                },
                "target_variables": {"segments": [
                    {"collection": "sol", "tag": "sol2"},
                    {"collection": "feature", "tag": "v2"},
                ]},
                "mapping_profile": "same_name_same_mesh_initialization",
                "variable_mappings": [{
                    "source_variable": "T", "target_variable": "T", "source_unit": "K",
                    "target_unit": "K", "mapping_method": "identity",
                }],
                "reference_state": {"strategy": "predecessor_stage",
                                    "source_stage_id": PREDECESSOR_STAGE_ID},
                "checks": [],
            },
        ],
    }


def _register_synthetic_plan(store: OperationStore, model_ref: dict[str, Any]) -> dict[str, Any]:
    definition = validate_stage_plan_definition(_stage_plan_definition())
    record = {
        "schema_version": 1,
        "kind": "w21_stage_plan",
        "project_id": PROJECT_ID,
        "model_ref": dict(model_ref),
        "plan_id": definition["plan_id"],
        "definition": definition,
        "definition_sha256": sha256_json(definition),
        "declaration_revision": 0,
        "declaration_status": "DECLARED_UNVERIFIED",
        "declaration_evidence": {
            "fixture": "T31-OFFLINE-TEST-ONLY-PLAN-REGISTRATION",
            "native_validation": False,
            "scientific_acceptance_claim": False,
        },
    }
    record["sha256"] = sha256_json(record)
    stored, created = store.register_stage_plan(
        project_id=PROJECT_ID, model_ref=dict(model_ref), plan_record=record,
    )
    assert created is True and stored["plan_id"] == definition["plan_id"]
    return stored


def _seed_test_only_accepted_predecessor(store: "FaultInjectingStageStore",
                                         model_ref: dict[str, Any]) -> dict[str, Any]:
    """Required single ACCEPTED predecessor, written through the store's own
    connection with an explicit synthetic marker (claims no native result and
    no scientific acceptance)."""
    record, reused = store.begin_stage_attempt(
        project_id=PROJECT_ID, model_ref=dict(model_ref), stage_id=PREDECESSOR_STAGE_ID,
        expected_revision=0, request_id="t31-test-only-predecessor-request",
        operation_id="t31-test-only-predecessor-operation",
        idempotency_key="t31-test-only-predecessor-idempotency", request_hash="a" * 64,
    )
    assert reused is False
    synthetic = dict(record)
    synthetic.pop("sha256", None)
    synthetic.update({
        "status": "ACCEPTED", "version": record["version"] + 1,
        "engine_dispatched": True, "execution_status": "SOLVE_SUCCEEDED",
        "acceptance_status": "ACCEPTED",
        "evidence": [{"kind": "test_only_synthetic_predecessor", "synthetic": True,
                      "native_result": False, "scientific_acceptance_claim": False}],
        "result": {"fixture_only": True, "native_acceptance_claimed": False},
    })
    synthetic["sha256"] = sha256_json(synthetic)
    store.db.execute(
        "UPDATE stage_attempts SET status=?,version=?,record_json=? WHERE attempt_id=?",
        (synthetic["status"], synthetic["version"], canonical_json(synthetic),
         synthetic["attempt_id"]),
    )
    assert store.original_get_stage_attempt(PROJECT_ID, model_ref, record["attempt_id"]) == synthetic
    return synthetic


# --------------------------------------------------------------------------
# Fault-injecting durable store (real OperationStore + arming-controlled
# read faults aimed at the two durable reads inside ``unknown_result``)
# --------------------------------------------------------------------------

class FaultInjectingStageStore(OperationStore):
    """Real :memory: store; the override fails only arming-gated durable
    reads whose caller frame is ``unknown_result`` (preimage 3447 = call 1,
    3466 = call 2).  ``original_get_stage_attempt`` keeps the UNREPLACED
    base-class getter for trusted recovery re-reads."""

    def __init__(self, path: str, unknown_read_fault: dict[str, Any] | None):
        super().__init__(path)
        self.original_get_stage_attempt = super().get_stage_attempt
        self.unknown_read_fault = unknown_read_fault
        self.read_fault_armed = False
        self.unknown_result_read_calls = 0
        self.read_fault_hits: list[dict[str, Any]] = []
        self.durable_read_calls: list[dict[str, Any]] = []

    @staticmethod
    def _nearest_control_frame() -> dict[str, Any]:
        frames = [frame for frame in traceback.extract_stack()
                  if frame.filename and frame.filename.endswith("_control_daemon.py")]
        if not frames:
            return {"function": None, "line": None, "source": None}
        frame = frames[-1]
        return {"function": frame.name, "line": frame.lineno,
                "source": frame.filename.rsplit("/", 1)[-1]}

    def _make_read_fault(self, action: str) -> Exception:
        if action == "raise_stage_conflict":
            return InjectedStageConflictAtFirstRead(
                InjectedStageConflictAtFirstRead.INJECTED_CODE,
                "T31 test-only injected first durable read failure inside unknown_result")
        if action == "raise_contract_fault":
            return InjectedContractFaultAtFirstRead(
                InjectedContractFaultAtFirstRead.INJECTED_CODE,
                "T31 test-only injected first durable read failure inside unknown_result")
        return InjectedDurableReadFailure(
            "T31 test-only injected durable stage-attempt read failure "
            "inside the inner UNKNOWN handler")

    def get_stage_attempt(self, project_id: str, model_ref: dict[str, Any],
                          attempt_id: str) -> dict[str, Any] | None:
        frame = self._nearest_control_frame()
        if (self.read_fault_armed and self.unknown_read_fault is not None
                and frame["function"] == "unknown_result"):
            self.unknown_result_read_calls += 1
            plan = self.unknown_read_fault
            target = plan["target_call"]
            engaged = (self.unknown_result_read_calls == target
                       or (plan.get("sticky", False) and self.unknown_result_read_calls > target))
            if engaged:
                self.read_fault_hits.append({
                    **frame, "attempt_id": attempt_id,
                    "call_index": self.unknown_result_read_calls,
                    "action": plan["action"],
                })
                if plan["action"] == "return_none":
                    return None
                raise self._make_read_fault(plan["action"])
        self.durable_read_calls.append({**frame, "attempt_id": attempt_id})
        return self.original_get_stage_attempt(project_id, model_ref, attempt_id)


# --------------------------------------------------------------------------
# Fake permission / model / ledger / scheduler / backend surface
# --------------------------------------------------------------------------

class _FakeProjectAuthority:
    """Inert project surface.  NOT a project-policy or authorization proof."""

    def apply_timeout_caps(self, project_id: str, timeouts: dict[str, Any]) -> dict[str, Any]:
        assert isinstance(project_id, str) and project_id
        return dict(timeouts)

    def get_project(self, project_id: str) -> dict[str, Any]:
        return {"project_id": project_id,
                "workspace": "/t31-offline/never-opened (TEST-ONLY path, never touched)"}

    def bind_project_workspace(self, project: dict[str, Any]) -> dict[str, Any]:
        return {"fixture": "T31-TEST-ONLY-WORKSPACE-BINDING",
                "project_id": project["project_id"]}


class _FakeLedger:
    session_id = SESSION_ID

    def __init__(self) -> None:
        self.state = SimpleNamespace(active_operation_id=None, dirty=False, revision=0)
        self.state_reads = 0

    def _state_for(self, model_ref: Any) -> SimpleNamespace:
        self.state_reads += 1
        assert model_ref is not None
        return self.state


class _FakeService:
    def __init__(self) -> None:
        self.ledger = _FakeLedger()


_READ_FAULT_PLANS: dict[str, dict[str, Any]] = {
    "first_read_stage_conflict": {"target_call": 1, "action": "raise_stage_conflict"},
    "first_read_contract_fault": {"target_call": 1, "action": "raise_contract_fault"},
    "second_read_returns_none": {"target_call": 2, "action": "return_none"},
    "second_read_sticky_failure": {"target_call": 2, "action": "raise_read_failure",
                                   "sticky": True},
}


class _FakeBackend:
    """Fake managed backend: records calls, emits synthetic events, then
    raises injected fault #1 AFTER the fake dispatch witness is durable.
    No Worker, no COMSOL, no filesystem access; every emitted id/handle is
    ``T31-TEST-ONLY``.  This is a recorded fake dispatch witness, not a real
    engine operation."""

    worker = None  # no Worker object exists in this harness

    def __init__(self, store: FaultInjectingStageStore, mode: str) -> None:
        self.store = store
        self.mode = mode
        assert mode in _READ_FAULT_PLANS, mode
        self.calls: list[dict[str, Any]] = []
        self.emitted_events: list[dict[str, Any]] = []
        self.fault_hits: list[dict[str, Any]] = []
        self.solve_marker: dict[str, Any] | None = None

    @staticmethod
    def _model_project_key(model_ref: dict[str, Any]) -> str:
        return (f"t31-offline-model-project-key:{model_ref['model_tag']}"
                f":{model_ref['generation']}")

    @staticmethod
    def model_project_binding(model_ref: dict[str, Any]) -> dict[str, Any]:
        return {"project_id": PROJECT_ID, "attribution": "PROJECT_BOUND",
                "model_ref": dict(model_ref)}

    @contextlib.contextmanager
    def project_root_scope(self, binding: Any):
        assert isinstance(binding, dict) and binding["project_id"] == PROJECT_ID
        yield binding

    def stage_native_admission(self, *, binding: Any, plan: Any, stage: Any) -> dict[str, Any]:
        del plan, stage
        return {
            "contract": ADMISSION_CONTRACT,
            "producer": "managed-backend-native-readback",
            "status": "VERIFIED",
            "binding": deepcopy(dict(binding)),
            "facts": {
                "source_attempt_binding": "VERIFIED", "target_field_identity": "VERIFIED",
                "source_target_units": "VERIFIED", "source_target_mesh": "VERIFIED",
                "frame_identity": "VERIFIED", "history_identity": "VERIFIED",
            },
            "evidence_refs": [{"kind": "TEST_FIXTURE_ONLY_FAKE_PROOF",
                               "sha256": "e" * 64, "native_proof": False}],
        }

    def invoke(self, operation: str, arguments: dict[str, Any], execution: dict[str, Any],
               operation_id: str, event_callback: Any) -> dict[str, Any]:
        assert operation == "run_study", operation
        marker = execution.get("_w21_stage_marker")
        assert isinstance(marker, dict) and isinstance(marker.get("attempt_id"), str)
        self.solve_marker = dict(marker)
        self.calls.append({"operation": operation, "study_tag": arguments["study_tag"],
                           "operation_id": operation_id})

        # One managed Study read for the exact target tag, then the solve RPC.
        self._emit_call(event_callback, operation_id, "study", FAKE_MODEL_HANDLE, [STUDY_TAG],
                        phase_pair=True,
                        reply_result={"$worker_handle": FAKE_STUDY_HANDLE,
                                      "generation": WORKER_GENERATION, "java_type": "Study"})
        self._emit_call(event_callback, operation_id, "run", FAKE_STUDY_HANDLE, [],
                        phase_pair=False)
        self._raise_solve_loss(
            note="injected after the synthetic Worker submission and its durable "
                 "dispatch evidence")
        raise AssertionError("unreachable fake invoke path")

    def _emit_call(self, event_callback: Any, operation_id: str, method: str, receiver: str,
                   args: list[Any], *, phase_pair: bool, reply_result: Any = None) -> None:
        worker_request_id = f"wrk-{uuid4()}"
        metadata = {"type": "call", "request_id": worker_request_id, "handle": receiver,
                    "generation": WORKER_GENERATION, "method": method, "args": args}
        binding = {"phase": "solve", "model_tag": MODEL_REF["model_tag"],
                   "model_handle": FAKE_MODEL_HANDLE, "worker_generation": WORKER_GENERATION,
                   "save_target_path": None}
        common = {"request_id": worker_request_id, "kind": "call", "operation_id": operation_id,
                  "request_hash": _request_hash(metadata), "metadata": metadata,
                  "w21_backend_binding": binding}
        submitted = {**common, "phase": "submitted"}
        self.emitted_events.append(dict(submitted))
        event_callback(submitted)
        if phase_pair:
            observed = {**common, "phase": "observed", "status": "ok",
                        "reply": {"ok": True, "status": "ok", "result": reply_result}}
            self.emitted_events.append(dict(observed))
            event_callback(observed)

    def _raise_solve_loss(self, *, note: str) -> None:
        self.fault_hits.append({"injected_error": InjectedSolveResponseLoss.__name__,
                                "after_fake_dispatch_evidence": True, "note": note})
        # Fault #1 is caught at preimage 4053 and routed into unknown_result;
        # arm the durable-read fault so it fires on the targeted read there.
        self.store.read_fault_armed = True
        raise InjectedSolveResponseLoss(f"T31 test-only injected solve response loss: {note}")


class _CompletedFuture:
    def __init__(self, value: Any) -> None:
        self._value = value

    def result(self, timeout: Any = None) -> Any:
        return self._value


class _InlineRecordingScheduler:
    """Fake session_scheduler: records the submit, then runs it inline.
    ``calls`` counts scheduler admissions, never real Worker dispatch."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, Any]] = []
        self.completed: list[Any] = []

    def submit(self, context: Any, callback: Any) -> _CompletedFuture:
        self.calls.append((context, callback))
        value = callback()
        self.completed.append(value)
        return _CompletedFuture(value)


class _StageRunReceiver:
    """Inert receiver for the unbound ControlDaemon methods."""

    _dispatch_experiment_stage_run = ControlDaemon._dispatch_experiment_stage_run
    _execute_admitted_experiment_stage = ControlDaemon._execute_admitted_experiment_stage
    _pending = ControlDaemon._pending
    _finish = ControlDaemon._finish
    _error = staticmethod(ControlDaemon._error)
    _exception = ControlDaemon._exception
    _release_terminal_accounting = ControlDaemon._release_terminal_accounting
    _release_terminal_disk_reservation = ControlDaemon._release_terminal_disk_reservation
    _release_terminal_resource_reservations = ControlDaemon._release_terminal_resource_reservations
    _timeouts = staticmethod(ControlDaemon._timeouts)
    _redact_session_worker_event = staticmethod(ControlDaemon._redact_session_worker_event)

    def __init__(self, mode: str) -> None:
        self.lock = threading.RLock()
        self.store = FaultInjectingStageStore(":memory:", _READ_FAULT_PLANS[mode])
        self.session_scheduler = _InlineRecordingScheduler()
        self.project_authority = _FakeProjectAuthority()
        self.service = _FakeService()
        self.backend = _FakeBackend(self.store, mode)
        self._disk_resource_coordinator = None
        self._memory_resource_coordinator = None
        self.log_exceptions: list[str] = []

    def _authorize_project_execution(self, operation, arguments, execution):
        # Deliberately inert; not an authorization proof.
        return None

    def _execution_session_context(self, execution):
        return SimpleNamespace(
            backend=self.backend, service=self.service,
            project_id=PROJECT_ID, session_id=SESSION_ID,
        )

    def _log_exception(self) -> None:
        # Overrides the real file-logging helper (preimage 13610) so no
        # filesystem write occurs; the captured traceback is the
        # reachability evidence for the generic except branch at 3189.
        self.log_exceptions.append(traceback.format_exc())


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _new_receiver(mode: str) -> tuple[_StageRunReceiver, dict[str, Any]]:
    """Fresh memory-only durable state: plan, revision binding, predecessor."""
    receiver = _StageRunReceiver(mode)
    model_ref = dict(MODEL_REF)
    _register_synthetic_plan(receiver.store, model_ref)
    receiver.store.put_metadata("revisions", receiver.backend._model_project_key(model_ref), {
        "model_ref": model_ref, "revision": 0, "dirty": False,
        "attribution": "PROJECT_BOUND", "project_id": PROJECT_ID,
    })
    predecessor = _seed_test_only_accepted_predecessor(receiver.store, model_ref)
    return receiver, {"model_ref": model_ref, "predecessor": predecessor}


def _dispatch_stage_run(receiver: _StageRunReceiver, predecessor_id: str, *,
                        request_id: str, idempotency_key: str) -> dict[str, Any]:
    # Duck-typed unbound call: the inert _StageRunReceiver supplies exactly
    # the bound attributes the real route touches.  getattr keeps the same
    # runtime call while stating the intentional dynamic self.
    dispatch_admitted = getattr(ControlDaemon, "_dispatch_admitted")
    return dispatch_admitted(receiver, {
        "operation": "experiment.stage_run",
        "arguments": {"stage_id": STAGE_ID, "source_attempt_id": predecessor_id},
        "execution": {
            "project_id": PROJECT_ID, "session_id": SESSION_ID, "model_ref": dict(MODEL_REF),
            "expected_revision": 0, "idempotency_key": idempotency_key,
            "request_id": request_id,
        },
    })


def _attempt_rows(store: OperationStore, model_ref: dict[str, Any],
                  stage_id: str = STAGE_ID) -> list[dict[str, Any]]:
    return store.list_stage_attempts(PROJECT_ID, model_ref, stage_id=stage_id)


def _dispatch_evidence(attempt: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in attempt["evidence"] if row.get("kind") == "worker-dispatch"]


def _evidence_kinds(attempt: dict[str, Any]) -> list[str]:
    return [row.get("kind") for row in attempt["evidence"]]


def _operation_row(store: OperationStore) -> Any:
    return store.db.execute(
        "SELECT operation_id,status,result FROM operations WHERE operation='experiment.stage_run'"
        " ORDER BY rowid"
    ).fetchone()


def _operation_count(store: OperationStore) -> int:
    return store.db.execute(
        "SELECT COUNT(*) FROM operations WHERE operation='experiment.stage_run'"
    ).fetchone()[0]


def _recovery_read(store: FaultInjectingStageStore, model_ref: dict[str, Any],
                   attempt_id: str) -> dict[str, Any] | None:
    """Trusted durable re-read through the UNREPLACED bound base-class
    getter.  The assertions prove the fault-injecting override was not used
    (no new call/hit bookkeeping)."""
    calls_before = len(store.durable_read_calls)
    handler_reads_before = store.unknown_result_read_calls
    hits_before = len(store.read_fault_hits)
    attempt = store.original_get_stage_attempt(PROJECT_ID, model_ref, attempt_id)
    assert len(store.durable_read_calls) == calls_before
    assert store.unknown_result_read_calls == handler_reads_before
    assert len(store.read_fault_hits) == hits_before
    return attempt


def _fake_run_submitted_event(backend: _FakeBackend) -> dict[str, Any]:
    events = [row for row in backend.emitted_events
              if row["phase"] == "submitted" and row["metadata"]["method"] == "run"]
    assert len(events) == 1, events
    return events[0]


def _assert_durable_dispatch_witness(durable: dict[str, Any], backend: _FakeBackend) -> None:
    """Content-level verification of the durable fake witness (recomputed
    request hash, exact handle/generation/method) — never by name alone."""
    assert durable["engine_dispatched"] is True, durable
    witness = _dispatch_evidence(durable)
    assert len(witness) == 1, durable["evidence"]
    event = _fake_run_submitted_event(backend)
    assert witness[0]["worker_request_id"] == event["request_id"]
    assert witness[0]["worker_request_hash"] == _request_hash(event["metadata"])
    assert witness[0]["worker_receiver"] == FAKE_STUDY_HANDLE
    assert witness[0]["worker_generation"] == WORKER_GENERATION
    assert witness[0]["worker_method"] == "run"


def _solve_attempt_id(receiver: _StageRunReceiver) -> str:
    marker = receiver.backend.solve_marker
    assert marker is not None, (
        "the fake backend never observed the durable solve marker from preimage 3508-3514"
    )
    return marker["attempt_id"]


def _assert_first_handler_reached(receiver: _StageRunReceiver, attempt_id: str,
                                  hit_index: int) -> dict[str, Any]:
    """Both injected faults hit real branches: fault #1 routed the inner
    ``except Exception`` (preimage 4053) into ``unknown_result`` — the only
    way fault #2 can fire with caller frame ``unknown_result`` — and fault #2
    fired exactly at the targeted durable read."""
    assert len(receiver.backend.calls) == 1, receiver.backend.calls
    assert len(receiver.backend.fault_hits) == 1, receiver.backend.fault_hits
    assert receiver.backend.fault_hits[0]["after_fake_dispatch_evidence"] is True
    assert len(receiver.session_scheduler.calls) == 1, receiver.session_scheduler.calls
    assert receiver.session_scheduler.calls[0][0].backend is receiver.backend
    # The intent row was durable before the (synthetic) solve call.
    marker = receiver.backend.solve_marker
    assert marker is not None
    assert marker["attempt_id"] == attempt_id
    assert marker["phase"] == "solve"
    hits = receiver.store.read_fault_hits
    assert len(hits) == 1, hits
    assert hits[0]["attempt_id"] == attempt_id
    assert hits[0]["source"] == "_control_daemon.py"
    assert hits[0]["function"] == "unknown_result", (
        "the injected durable read fault did not fire inside the inner UNKNOWN "
        f"handler; observed caller {hits[0]!r}"
    )
    assert hits[0]["call_index"] == hit_index, hits
    return hits[0]


def _probe_attempt_not_rewritable(store: FaultInjectingStageStore,
                                  model_ref: dict[str, Any], durable: dict[str, Any],
                                  *, expect_code: str, expect_fragment: str) -> None:
    """The synthetic durable dispatch state cannot be rewritten/cleared by
    the generic CAS API; afterwards the row must be byte-identical."""
    cleared = None
    raised: StagePlanStoreConflict | None = None
    try:
        cleared = store.update_stage_attempt(
            PROJECT_ID, model_ref, durable["attempt_id"],
            expected_version=durable["version"], status=durable["status"],
            engine_dispatched=False, execution_status="UNKNOWN",
            acceptance_status="UNKNOWN", evidence=list(durable["evidence"]),
            result={"reason": "T31 guard probe"},
        )
    except StagePlanStoreConflict as exc:
        raised = exc
    assert cleared is None, "durable dispatch witness was unexpectedly rewritable"
    assert raised is not None and raised.code == expect_code, raised
    assert expect_fragment in str(raised), str(raised)
    after = _recovery_read(store, model_ref, durable["attempt_id"])
    assert after == durable, "the rejected probe changed the durable row"


def _assert_honest_dispatch_projection(result: dict[str, Any],
                                       trusted: dict[str, Any] | None,
                                       *, label: str) -> None:
    """engine_dispatched may never be a false negative.  The response's own
    attempt field is the product's observation at response-construction
    time: when it is absent/None the durable row was unreadable there, so
    the claim must be an explicit None.  The later test-only recovery read
    shows the durable witness exists but cannot retroactively inform that
    earlier response, and it never licenses a False.  When the response
    does contain an attempt, that attempt must agree with the trusted
    durable row and the claim must match the attempt's engine_dispatched."""
    claim = result["data"].get("engine_dispatched", _MISSING)
    trusted_status = trusted["status"] if trusted is not None else None
    trusted_version = trusted["version"] if trusted is not None else None
    trusted_dispatched = trusted["engine_dispatched"] if trusted is not None else None
    assert claim is not False, (
        f"{label}: DEFECT — the response asserts engine_dispatched=False although "
        f"a synthetic dispatch witness was recorded (durable status={trusted_status} "
        f"version={trusted_version} engine_dispatched={trusted_dispatched}); project "
        "the value observable at response time, or an explicit None when the "
        "durable read failed — never a false negative."
    )
    returned = result["data"].get("attempt")
    if returned is None:
        assert claim is None, (
            f"{label}: the response embeds no attempt, so the product never "
            f"observed the durable row at response time; engine_dispatched must "
            f"then be an explicit None, got {claim!r}. The later test-only "
            "recovery read is fixture-private and cannot inform the response."
        )
        return
    assert trusted is not None, (
        f"{label}: the response embeds an attempt but no trusted durable row "
        "was supplied for cross-checking"
    )
    assert returned.get("attempt_id") == trusted["attempt_id"], (
        f"{label}: the returned attempt names a different attempt than the "
        "durable row"
    )
    assert returned.get("status") == trusted["status"], (
        f"{label}: returned status={returned.get('status')!r} contradicts the "
        f"durable status={trusted['status']!r}"
    )
    assert returned.get("engine_dispatched") == trusted["engine_dispatched"], (
        f"{label}: returned engine_dispatched={returned.get('engine_dispatched')!r} "
        f"contradicts the durable engine_dispatched={trusted['engine_dispatched']!r}"
    )
    assert returned.get("version") == trusted["version"], (
        f"{label}: returned version={returned.get('version')!r} contradicts the "
        f"durable version={trusted['version']!r}"
    )
    assert claim is returned.get("engine_dispatched"), (
        f"{label}: the response claims engine_dispatched={claim!r} although its "
        f"own embedded attempt says {returned.get('engine_dispatched')!r}"
    )


def _assert_required_unknown_envelope(result: dict[str, Any], store: OperationStore) -> None:
    """Required for every nested read fault: the response must identify an
    UNKNOWN execution state with no automatic retry invitation."""
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN", result
    assert result["error"]["safe_retry"] is False, result
    assert _operation_row(store)["status"] == "UNKNOWN", (
        f"durable operation status is {_operation_row(store)['status']!r}; a nested "
        "UNKNOWN-handler fault must not finalize as a different terminal state"
    )


def _assert_no_second_admission_on_replay(receiver: _StageRunReceiver, store: OperationStore,
                                          model_ref: dict[str, Any],
                                          before_attempts: int) -> None:
    assert len(receiver.session_scheduler.calls) == 1, (
        "same idempotency key re-admitted the scheduler callback"
    )
    assert len(receiver.backend.calls) == 1, (
        "same idempotency key reached backend.invoke again (second synthetic solve)"
    )
    assert len(_attempt_rows(store, model_ref)) == before_attempts, (
        "same idempotency key created a second durable stage attempt"
    )


def _same_key_replay_only_observes(receiver: _StageRunReceiver, ids: dict[str, Any],
                                   result: dict[str, Any]) -> None:
    """Same-key replay must only observe the stored result of the already
    created synthetic job: one operation, no second scheduler admission, no
    second backend.invoke, no second attempt (preimage 2953-2956 with the
    UNKNOWN result retained by store.finish at 1870-1934)."""
    store, model_ref = receiver.store, ids["model_ref"]
    before_attempts = len(_attempt_rows(store, model_ref))
    replay = _dispatch_stage_run(receiver, ids["predecessor"]["attempt_id"],
                                 request_id="t31-req-replay",
                                 idempotency_key="t31-key-single-use")
    assert replay["success"] is False
    assert replay["error"]["code"] == result["error"]["code"], replay
    assert replay["error"]["safe_retry"] is False
    assert replay["data"] == result["data"], (
        "same-key replay returned a different dispatch projection than the stored result"
    )
    assert _operation_count(store) == 1
    _assert_no_second_admission_on_replay(receiver, store, model_ref, before_attempts)


# --------------------------------------------------------------------------
# Test 1 — first durable read inside unknown_result raises a REAL
# StagePlanStoreConflict (the fix routes this escaping typed fault through
# the generic UNKNOWN handler instead of the preimage 3181 inner branch)
# --------------------------------------------------------------------------

def test_first_unknown_read_stage_conflict_keeps_durable_witness_and_unknown_state():
    receiver, ids = _new_receiver("first_read_stage_conflict")
    store, model_ref = receiver.store, ids["model_ref"]

    result = _dispatch_stage_run(receiver, ids["predecessor"]["attempt_id"],
                                 request_id="t31-req-t1", idempotency_key="t31-key-single-use")

    attempt_id = _solve_attempt_id(receiver)
    hit = _assert_first_handler_reached(receiver, attempt_id, hit_index=1)
    assert hit["action"] == "raise_stage_conflict"

    # --- required envelope: the fix routes a typed fault raised inside
    # the inner UNKNOWN handler through the generic UNKNOWN handler, so
    # the preimage 3181-3186 behaviour (injected code, hardcoded
    # engine_dispatched=False, silent unlogged path) is a fault signature
    # and must no longer be asserted as fact ------------------------------
    assert result["success"] is False, result
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN", (
        "a durable read fault inside the UNKNOWN handler must not finalize as a "
        "typed FAILED envelope (the preimage 3181-3186 route)"
    )
    assert result["error"]["type"] == InjectedStageConflictAtFirstRead.__name__, (
        "the projected error type must be the class of the exception actually "
        "raised while reporting UNKNOWN"
    )
    assert result["error"]["safe_retry"] is False, result
    assert len(receiver.log_exceptions) == 1, (
        "the generic UNKNOWN handler logs exactly once on this route (preimage 3190)"
    )

    # --- durable ORIGINAL state, re-read via the un-replaced getter ------
    durable = _recovery_read(store, model_ref, attempt_id)
    assert durable is not None
    assert durable["status"] == "RUNNING", durable
    assert durable["engine_dispatched"] is True, durable
    _assert_durable_dispatch_witness(durable, receiver.backend)
    # unknown_result died before its guarded write: no interruption row, and
    # no generic-branch control-state row either.
    assert "stage-interruption" not in _evidence_kinds(durable), durable["evidence"]
    assert "control-state" not in _evidence_kinds(durable), durable["evidence"]

    # --- the synthetic dispatch True cannot be cleared -------------------
    _probe_attempt_not_rewritable(
        store, model_ref, durable, expect_code="STAGE_ATTEMPT_STATE_UNKNOWN",
        expect_fragment="engine dispatch evidence cannot be cleared")

    # --- REQUIRED contract (EXECUTION_STATE_UNKNOWN envelope, operation
    # UNKNOWN finish, honest dispatch projection) -------------------------
    _assert_required_unknown_envelope(result, store)
    _assert_honest_dispatch_projection(result, durable, label="first-read-conflict")

    # --- same-key replay only observes the stored result -----------------
    _same_key_replay_only_observes(receiver, ids, result)


# --------------------------------------------------------------------------
# Test 2 — same read raises a REAL ExecutionContractError (the fix routes
# this escaping typed fault through the generic UNKNOWN handler instead of
# the preimage 3187 branch, whose envelope had an empty data map)
# --------------------------------------------------------------------------

def test_first_unknown_read_contract_error_keeps_durable_witness_and_unknown_state():
    receiver, ids = _new_receiver("first_read_contract_fault")
    store, model_ref = receiver.store, ids["model_ref"]

    result = _dispatch_stage_run(receiver, ids["predecessor"]["attempt_id"],
                                 request_id="t31-req-t2", idempotency_key="t31-key-single-use")

    attempt_id = _solve_attempt_id(receiver)
    hit = _assert_first_handler_reached(receiver, attempt_id, hit_index=1)
    assert hit["action"] == "raise_contract_fault"

    # --- required envelope (see the note in test 1): the preimage
    # 3187-3188 behaviour (injected code, empty _exception data map, silent
    # unlogged path) is a fault signature and must no longer be asserted
    # as fact -------------------------------------------------------------
    assert result["success"] is False, result
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN", (
        "a durable read fault inside the UNKNOWN handler must not finalize as a "
        "typed FAILED envelope (the preimage 3187-3188 route)"
    )
    assert result["error"]["type"] == InjectedContractFaultAtFirstRead.__name__, (
        "the projected error type must be the class of the exception actually "
        "raised while reporting UNKNOWN"
    )
    assert result["error"]["safe_retry"] is False, result
    assert len(receiver.log_exceptions) == 1, (
        "the generic UNKNOWN handler logs exactly once on this route (preimage 3190)"
    )

    durable = _recovery_read(store, model_ref, attempt_id)
    assert durable is not None
    assert durable["status"] == "RUNNING", durable
    assert durable["engine_dispatched"] is True, durable
    _assert_durable_dispatch_witness(durable, receiver.backend)
    assert "stage-interruption" not in _evidence_kinds(durable), durable["evidence"]
    assert "control-state" not in _evidence_kinds(durable), durable["evidence"]

    _probe_attempt_not_rewritable(
        store, model_ref, durable, expect_code="STAGE_ATTEMPT_STATE_UNKNOWN",
        expect_fragment="engine dispatch evidence cannot be cleared")

    # --- REQUIRED contract (same envelope, UNKNOWN finish, projection) ---
    _assert_required_unknown_envelope(result, store)
    _assert_honest_dispatch_projection(result, durable, label="first-read-contract")

    # --- same-key replay only observes the stored result -----------------
    _same_key_replay_only_observes(receiver, ids, result)


# --------------------------------------------------------------------------
# Test 3 — second durable read inside unknown_result returns None
# (preimage 3466 -> bool(None and ...) silently claims False at 3470)
# --------------------------------------------------------------------------

def test_second_unknown_read_returning_none_must_not_claim_not_dispatched():
    receiver, ids = _new_receiver("second_read_returns_none")
    store, model_ref = receiver.store, ids["model_ref"]

    result = _dispatch_stage_run(receiver, ids["predecessor"]["attempt_id"],
                                 request_id="t31-req-t3", idempotency_key="t31-key-single-use")

    attempt_id = _solve_attempt_id(receiver)
    hit = _assert_first_handler_reached(receiver, attempt_id, hit_index=2)
    assert hit["action"] == "return_none"
    # Read #1 succeeded, so the guarded UNKNOWN write (3449-3464) ran with
    # engine_dispatched preserved from the durable row.
    assert receiver.store.unknown_result_read_calls == 2
    assert receiver.log_exceptions == [], (
        "a None second read must not escape into the generic 3189 branch"
    )

    # --- envelope: UNKNOWN state, the original reported exception class,
    # and the explicit-None attempt the fix must produce ------------------
    assert result["success"] is False, result
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert result["error"]["type"] == InjectedSolveResponseLoss.__name__
    assert result["data"]["attempt"] is None, (
        "the unreadable second read must be surfaced as an explicit None "
        "(preimage 3466-3470 embedded it directly; it must never be turned "
        "into a False dispatch claim)"
    )

    # --- durable ORIGINAL state via the un-replaced getter ---------------
    durable = _recovery_read(store, model_ref, attempt_id)
    assert durable is not None
    assert durable["status"] == "UNKNOWN", durable
    assert durable["engine_dispatched"] is True, durable
    _assert_durable_dispatch_witness(durable, receiver.backend)
    assert any(row.get("kind") == "stage-interruption"
               and row.get("error_type") == InjectedSolveResponseLoss.__name__
               for row in durable["evidence"]), durable["evidence"]

    # UNKNOWN attempts have no outgoing transition through generic CAS
    # (store 92-96); the separately recorded operation UNKNOWN is nonterminal.
    # This probe checks that the existing True witness cannot be cleared.
    _probe_attempt_not_rewritable(
        store, model_ref, durable, expect_code="STAGE_ATTEMPT_TRANSITION_INVALID",
        expect_fragment="status transition is not permitted")

    # --- REQUIRED contract (UNKNOWN finish, no retry invitation, and the
    # honest projection enforced by the helper) ---------------------------
    assert result["error"]["safe_retry"] is False, result
    assert _operation_row(store)["status"] == "UNKNOWN", _operation_row(store)["status"]
    _assert_honest_dispatch_projection(result, durable, label="second-read-none")

    # --- same-key replay only observes the stored result -----------------
    _same_key_replay_only_observes(receiver, ids, result)


# --------------------------------------------------------------------------
# Test 4 — second read raises and the read fault stays armed for later reads
# from unknown_result. The generic handler's separate read is not faulted by
# this caller-specific plan; the error still reaches the generic branch.
# --------------------------------------------------------------------------

def test_sticky_second_read_failure_still_returns_unknown_without_false_claim():
    receiver, ids = _new_receiver("second_read_sticky_failure")
    store, model_ref = receiver.store, ids["model_ref"]

    result = _dispatch_stage_run(receiver, ids["predecessor"]["attempt_id"],
                                 request_id="t31-req-t4", idempotency_key="t31-key-single-use")

    attempt_id = _solve_attempt_id(receiver)
    hit = _assert_first_handler_reached(receiver, attempt_id, hit_index=2)
    assert hit["action"] == "raise_read_failure"
    # The targeted inner-handler read raised and the sticky plan remains
    # armed for reads from unknown_result; it does not cover the outer read.
    assert store.read_fault_armed is True
    fault_plan = store.unknown_read_fault
    assert fault_plan is not None
    assert fault_plan.get("sticky") is True

    # Fault #2 escaped into the generic branch: _log_exception runs only at
    # preimage 3190 on this route; chaining keeps fault #1 visible.
    assert len(receiver.log_exceptions) == 1, receiver.log_exceptions
    logged = receiver.log_exceptions[0]
    for marker in ("_execute_admitted_experiment_stage", "unknown_result",
                   "get_stage_attempt", InjectedDurableReadFailure.__name__,
                   InjectedSolveResponseLoss.__name__, "preflight_and_record"):
        assert marker in logged, f"missing generic-branch marker {marker!r}:\n{logged}"

    # --- envelope: UNKNOWN state + the class of the exception that
    # escaped the UNKNOWN reporting (test 4's sticky read fault) ----------
    assert result["success"] is False, result
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert result["error"]["type"] == InjectedDurableReadFailure.__name__

    # --- durable ORIGINAL state via the un-replaced getter ---------------
    # Read #1 succeeded, so the guarded UNKNOWN write ran before the sticky
    # failure; the generic branch's stale CAS (3193-3202, version from the
    # ADMITTED row) is rejected at store 4699-4700, leaving that row exact.
    durable = _recovery_read(store, model_ref, attempt_id)
    assert durable is not None
    assert durable["status"] == "UNKNOWN", durable
    assert durable["engine_dispatched"] is True, durable
    _assert_durable_dispatch_witness(durable, receiver.backend)
    assert any(row.get("kind") == "stage-interruption"
               and row.get("error_type") == InjectedSolveResponseLoss.__name__
               for row in durable["evidence"]), durable["evidence"]
    assert "control-state" not in _evidence_kinds(durable), (
        f"the rejected stale CAS still wrote: {durable['evidence']}"
    )

    _probe_attempt_not_rewritable(
        store, model_ref, durable, expect_code="STAGE_ATTEMPT_TRANSITION_INVALID",
        expect_fragment="status transition is not permitted")

    # --- REQUIRED contract (safe_retry False, UNKNOWN finish, projection;
    # the preimage's hardcoded False at 3206 + stale attempt stay DEFECTS) -
    assert result["error"]["safe_retry"] is False, result
    assert _operation_row(store)["status"] == "UNKNOWN", _operation_row(store)["status"]
    _assert_honest_dispatch_projection(result, durable, label="sticky-second-read")
    returned = result["data"].get("attempt")
    assert not (returned is not None
                and returned.get("attempt_id") == durable["attempt_id"]
                and (returned.get("status") != durable["status"]
                     or returned.get("engine_dispatched") != durable["engine_dispatched"]
                     or returned.get("version") != durable["version"])), (
        "DEFECT: the saved attempt contradicts the returned attempt "
        f"(durable status={durable['status']} version={durable['version']} "
        f"engine_dispatched={durable['engine_dispatched']}; returned "
        f"status={returned.get('status') if returned else None} "
        f"version={returned.get('version') if returned else None} "
        f"engine_dispatched={returned.get('engine_dispatched') if returned else None})"
    )

    # --- same-key replay only observes the stored result -----------------
    _same_key_replay_only_observes(receiver, ids, result)
