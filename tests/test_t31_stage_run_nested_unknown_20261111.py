"""T31/U07: nested UNKNOWN-handler fault fence for ``experiment.stage_run``.

Task-owned, offline, never executed by the author of this file.  The
file is a RED/GREEN fence for one conditional source risk named in
``docs/universal_mcp_execution/evidence/full/T31-W21-stage-run-unknown-source-analysis-parent-20261111.md``
(items 2, 4 and the ruling): if the inner UNKNOWN projection of
``ControlDaemon._execute_admitted_experiment_stage`` itself fails on a durable
store read, the escaped error lands in the generic ``except Exception`` of
``preflight_and_record``, whose response hardcodes ``engine_dispatched=False``
and carries a possibly stale local attempt.

Source anchors in ``comsol_mcp/_control_daemon.py`` (line numbers of the
current candidate; the file only carries ``stage_define`` message changes):
  * 2838  ``_dispatch_experiment_stage_run`` (route body, admission, gates)
  * 2948  durable ``store.begin`` + reused-key short circuit (2953-2956)
  * 2963  ``preflight_and_record`` inner body; 3093-3108 native admission gate
  * 3174  call into ``_execute_admitted_experiment_stage``
  * 3181 / 3187  ``StagePlanStoreConflict`` / ``ExecutionContractError`` branches
  * 3189-3210  generic ``except Exception``: stale-attempt CAS (3193-3202),
               response with hardcoded ``engine_dispatched`` False (3203-3209)
  * 3231  ``_execute_admitted_experiment_stage`` (docstring: deliberately
          dormant for the production backend; fake backends may drive it)
  * 3396-3444 ``store_worker_event`` solve phase: gate + ``worker-dispatch``
          evidence + RUNNING/engine_dispatched durable write
  * 3446-3473 ``unknown_result``: durable reads at 3447 and 3466, dispatch
          projection at 3470
  * 3475-3525 DISPATCH_INTENT durable write, then the single ``backend.invoke``
  * 4053-4054 ``except Exception as exc: return unknown_result(exc)``
Store anchors in ``comsol_mcp/_operation_store.py``:
  * 92-96   ``STAGE_ATTEMPT_TRANSITIONS`` (no outgoing UNKNOWN transition)
  * 4673-4752 ``update_stage_attempt`` CAS; 4699 CAS version, 4705-4706
            "engine dispatch evidence cannot be cleared"
  * 1870-1934 ``finish`` keeps UNKNOWN as a durable nonterminal observation

What is REAL here (product code actually executed):
  * ``ControlDaemon._dispatch_admitted`` and the whole
    ``_dispatch_experiment_stage_run`` route, including the real
    ``validate_call``/``canonical_request_hash``/identity gates, the real
    durable attempt API (``begin_stage_attempt``/``update_stage_attempt``/
    ``get_stage_attempt``), the real native-admission gate
    ``validate_native_admission``, the real ``StageWorkerDispatchGate``, the
    real ``unknown_result`` and the real generic ``except Exception`` branch,
    plus real ``_finish``/``_error``/``_exception``/``_pending``/``_timeouts``/
    ``_redact_session_worker_event`` bound to an inert receiver.
  * A brand-new ``OperationStore(":memory:")`` (memory-only, no DB file) does
    all admission, CAS, idempotency and event persistence.
  * The real ``StagePlanStoreConflict`` CAS guard is probed directly to show a
    dispatched attempt cannot be downgraded to ``engine_dispatched=False``.

What is FAKE (bounded test scaffolding, never an engine/authorization claim):
  * No ``ControlDaemon`` instance, no ControlDaemon/Worker/SSH/COMSOL/thread
    machinery.  ``_StageRunReceiver`` is inert; authorization stubs and
    ``_execution_session_context`` return sentinels.
  * ``_InlineRecordingScheduler.submit`` runs the captured callback inline and
    returns a completed future, so the real
    ``future.result(timeout=min(rpc_timeout_s, 5.0))`` line still runs.  It
    proves code reachability, not scheduler/thread behaviour.
  * The fake backend emits synthetic Worker *events* only.  They satisfy the
    real dispatch-gate shape (``wrk-`` UUID request ids, the real
    ``_request_hash`` digest, a test-only receiver handle named
    ``T31-TEST-ONLY-FAKE-*``).  This is a *recorded fake dispatch witness*;
    it is NOT a real engine/Worker RPC, and no test here may be read as
    evidence that any COMSOL study ran.
  * The fake admission is a ``TEST_FIXTURE_ONLY_FAKE_PROOF`` mapping; it is
    not native proof of anything.
  * The ACCEPTED predecessor attempt is seeded through the store's own DB
    handle with an explicit synthetic marker (shape read from
    ``tests/test_stage_definition.py::_seed_test_only_accepted_predecessor``,
    which was read but is never imported or run).

Plan data is a self-contained synthetic v2 declaration, then normalised by
the real ``validate_stage_plan_definition``.  The parent removed the author's
unused continuity/geometry declaration before running this test: no T14
geometry/membership/managed-spatial or W13 selection_measure geometry result
is sought.  The author's final pre-parent bytes are retained in the evidence
directory.

EXPECTATIONS (this file was written, not executed):
  * ``test_after_dispatch_nested_read_failure_must_not_claim_not_dispatched``
    encodes the REQUIRED invariant and is expected to FAIL (RED) against the
    current unfixed product at _control_daemon.py:3203-3209: the response
    asserts ``engine_dispatched=False`` while the durable attempt still holds
    a dispatch witness.  Its mechanical reachability/evidence assertions all
    pass; the invariant assertion is last so the failure message is the
    defect statement.
  * The intact inner-handler control is expected to pass.  The no-witness
    cross-fault also requires that a returned attempt not be stale; it may
    therefore fail against the present candidate.  An unknown dispatch value
    is allowed where the response cannot establish the truth.
  * All tests additionally fence same-idempotency-key replay: it may only
    observe the stored result of the newly created synthetic job; it must not
    re-submit to the scheduler, must not reach ``backend.invoke`` again, and
    must not create a second attempt.

Source-candidate note: the dispatcher reported the current
``comsol_mcp/_control_daemon.py`` candidate as SHA-256
``6114cfe98412abe7d9b7f137978ca6273effe05e5ba72ff34da411ea7a501eaa``; this
task performed no hashing (no shell), so that value is quoted, not verified,
and no claim is made about it being a native or accepted state.
"""
from __future__ import annotations

import contextlib
import json
import threading
import traceback
from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import model_ref_from_mapping
from comsol_mcp._java_worker import _request_hash
from comsol_mcp._operation_store import OperationStore, StagePlanStoreConflict
from comsol_mcp._stage_contract import (
    canonical_json,
    sha256_json,
    validate_stage_plan_definition,
)
from comsol_mcp._w21_stage_backend import ADMISSION_CONTRACT

PROJECT_ID = "project-t31-nested-unknown"
SESSION_ID = "session-t31-nested-unknown"
STAGE_ID = "cooldown"
PREDECESSOR_STAGE_ID = "preheat"
STUDY_TAG = "std2"
WORKER_GENERATION = 71
MODEL_REF_INPUT = {
    # Normalized through the real ModelRef contract below so the durable scope
    # digests, the revision-binding row and the dispatch gate all see one
    # identical dict.
    "schema_version": 1,
    "session_id": SESSION_ID,
    "server_instance_id": "server-t31-nested-unknown",
    "model_tag": "model-t31-nested-unknown",
    "generation": 1,
}
MODEL_REF = model_ref_from_mapping(MODEL_REF_INPUT).as_dict()
FAKE_MODEL_HANDLE = "T31-TEST-ONLY-FAKE-MODEL-HANDLE"
FAKE_STUDY_HANDLE = "T31-TEST-ONLY-FAKE-STUDY-HANDLE"


class InjectedSolveResponseLoss(RuntimeError):
    """Injected ordinary fault #1: loss of the (synthetic) solve response.

    A plain ``RuntimeError`` subclass, deliberately NOT an
    ``ExecutionContractError``/``StagePlanStoreConflict``, so the inner
    ``except Exception`` at _control_daemon.py:4053 handles it.
    """


class InjectedDurableReadFailure(RuntimeError):
    """Injected ordinary fault #2: a failing durable read inside
    ``unknown_result`` (_control_daemon.py:3447), which must escape into the
    generic ``except Exception`` at _control_daemon.py:3189."""


# --------------------------------------------------------------------------
# Synthetic plan / durable seeding (real store APIs, test-only content)
# --------------------------------------------------------------------------

def _stage_plan_definition() -> dict[str, Any]:
    """Synthetic v2 two-stage declaration.

    Provenance: reproduced in spirit from tests/test_stage_definition.py
    ``_plan``/``_plan_v2`` (lines 66-162), which were read but never imported
    or executed, so that this file stays independent of that module's fixtures
    and heavy imports.  It is a plan *declaration* only; the run stage
    (``cooldown``) is an ordinary successor stage, so the registered
    initial-state preflight route is not exercised here.
    """
    return {
        "version": 2,
        "plan_id": "t31-nested-unknown-plan",
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
    """Register the plan through the real durable registration API."""
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


def _seed_test_only_accepted_predecessor(store: OperationStore,
                                         model_ref: dict[str, Any]) -> dict[str, Any]:
    """Give the successor stage its required single ACCEPTED predecessor.

    The generic attempt CAS forbids ACCEPTED, so this uses the store's own
    connection exactly as the read-only-seen fixture in
    tests/test_stage_definition.py does.  The row is visibly synthetic and
    claims no native result and no scientific acceptance.
    """
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
    assert store.get_stage_attempt(PROJECT_ID, model_ref, record["attempt_id"]) == synthetic
    return synthetic


# --------------------------------------------------------------------------
# Fault-injecting durable store (real OperationStore + one armed read fault)
# --------------------------------------------------------------------------

class FaultInjectingStageStore(OperationStore):
    """Real in-memory OperationStore with one arming-controlled read fault.

    ``get_stage_attempt`` is the durable read used by ``unknown_result``
    (_control_daemon.py:3447 and 3466).  When armed, the *next* such read
    raises :class:`InjectedDurableReadFailure` and the hit record names the
    ``_control_daemon.py`` caller frame, which is the reachability proof that
    the failure happened inside the inner UNKNOWN handler.
    """

    def __init__(self, path: str):
        super().__init__(path)
        self.nested_read_fault_armed = False
        self.nested_read_fault_hits: list[dict[str, Any]] = []
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

    def get_stage_attempt(self, project_id: str, model_ref: dict[str, Any],
                          attempt_id: str) -> dict[str, Any] | None:
        frame = self._nearest_control_frame()
        self.durable_read_calls.append({**frame, "attempt_id": attempt_id})
        if self.nested_read_fault_armed:
            self.nested_read_fault_armed = False
            self.nested_read_fault_hits.append({**frame, "attempt_id": attempt_id,
                                                "error_type": InjectedDurableReadFailure.__name__})
            raise InjectedDurableReadFailure(
                "T31 test-only injected durable stage-attempt read failure "
                "inside the inner UNKNOWN handler"
            )
        return super().get_stage_attempt(project_id, model_ref, attempt_id)


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
    """Minimal ledger identity/revision surface."""

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


class _FakeBackend:
    """Fake managed backend: only records calls and emits synthetic events.

    No Worker, no COMSOL, no filesystem access.  ``invoke`` emits the exact
    event *shape* the real ``StageWorkerDispatchGate`` requires, so the real
    durable dispatch-evidence write runs, then raises the injected ordinary
    fault.  Every emitted request id/handle is marked ``T31-TEST-ONLY``; the
    recorded witness is a fake, not a real engine operation.
    """

    worker = None  # no Worker object exists in this harness

    def __init__(self, store: FaultInjectingStageStore, mode: str) -> None:
        self.store = store
        self.mode = mode
        assert mode in ("after_dispatch_nested_fault", "after_dispatch_clean",
                        "before_dispatch_nested_fault"), mode
        # Both nested-fault modes arm the durable-read fault inside the
        # inner UNKNOWN handler; they differ only in whether a fake dispatch
        # witness exists yet.
        self.nested_fault_mode = mode.endswith("_nested_fault")
        self.calls: list[dict[str, Any]] = []
        self.emitted_events: list[dict[str, Any]] = []
        self.fault_hits: list[dict[str, Any]] = []
        self.solve_marker: dict[str, Any] | None = None

    # --- identity/key helpers used by the real route -----------------------
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

    # --- fake native admission (explicit fixture, never native proof) -----
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

    # --- the only solve path ----------------------------------------------
    def invoke(self, operation: str, arguments: dict[str, Any], execution: dict[str, Any],
               operation_id: str, event_callback: Any) -> dict[str, Any]:
        assert operation == "run_study", operation
        marker = execution.get("_w21_stage_marker")
        assert isinstance(marker, dict) and isinstance(marker.get("attempt_id"), str)
        self.solve_marker = dict(marker)
        self.calls.append({"operation": operation, "study_tag": arguments["study_tag"],
                           "operation_id": operation_id})

        if self.mode == "before_dispatch_nested_fault":
            self._raise_solve_loss(after_fake_dispatch=False,
                                   note="injected before any synthetic Worker event")

        # One managed Study read for the exact target tag, then the solve RPC.
        self._emit_call(event_callback, operation_id, "study", FAKE_MODEL_HANDLE, [STUDY_TAG],
                        phase_pair=True,
                        reply_result={"$worker_handle": FAKE_STUDY_HANDLE,
                                      "generation": WORKER_GENERATION, "java_type": "Study"})
        self._emit_call(event_callback, operation_id, "run", FAKE_STUDY_HANDLE, [],
                        phase_pair=False)
        self._raise_solve_loss(after_fake_dispatch=True,
                               note="injected after the synthetic Worker submission "
                                    "and its durable dispatch evidence")
        # Unreachable: _raise_solve_loss always raises.
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

    def _raise_solve_loss(self, *, after_fake_dispatch: bool, note: str) -> None:
        self.fault_hits.append({"injected_error": InjectedSolveResponseLoss.__name__,
                                "after_fake_dispatch_evidence": after_fake_dispatch,
                                "note": note})
        if self.nested_fault_mode:
            # Arm the durable-read fault so the first read inside
            # unknown_result (_control_daemon.py:3447) fails.
            self.store.nested_read_fault_armed = True
        raise InjectedSolveResponseLoss(f"T31 test-only injected solve response loss: {note}")


class _CompletedFuture:
    def __init__(self, value: Any) -> None:
        self._value = value

    def result(self, timeout: Any = None) -> Any:
        return self._value


class _InlineRecordingScheduler:
    """Fake session_scheduler: records the submit, then runs it inline.

    Running the callback inline keeps the real
    ``future.result(timeout=min(rpc_timeout_s, 5.0))`` line live while making
    the fault timing deterministic.  ``calls`` counts scheduler admissions,
    never real Worker dispatch.
    """

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
    # Explicit staticmethod re-wrapping: plain attribute access would pass the
    # receiver as the first positional argument.
    _timeouts = staticmethod(ControlDaemon._timeouts)
    _redact_session_worker_event = staticmethod(ControlDaemon._redact_session_worker_event)

    def __init__(self, mode: str) -> None:
        self.lock = threading.RLock()
        self.store = FaultInjectingStageStore(":memory:")
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
        # Overrides the real file-logging helper so no filesystem write occurs;
        # the captured traceback is the reachability evidence for the generic
        # except branch at _control_daemon.py:3189.
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
    return ControlDaemon._dispatch_admitted(receiver, {
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


def _job_event_rows(store: OperationStore, job_id: str) -> list[dict[str, Any]]:
    rows = store.db.execute(
        "SELECT event, metadata FROM job_events WHERE job_id=? ORDER BY rowid", (job_id,)
    ).fetchall()
    return [{"event": row["event"], "metadata": json.loads(row["metadata"] or "{}")}
            for row in rows]


def _operation_row(store: OperationStore) -> Any:
    return store.db.execute(
        "SELECT operation_id,status,result FROM operations WHERE operation='experiment.stage_run'"
        " ORDER BY rowid"
    ).fetchone()


def _operation_count(store: OperationStore) -> int:
    return store.db.execute(
        "SELECT COUNT(*) FROM operations WHERE operation='experiment.stage_run'"
    ).fetchone()[0]


def _job_id_for(store: OperationStore) -> str:
    job = store.operation_job(_operation_row(store)["operation_id"])
    assert job is not None
    return job["job_id"]


def _assert_common_reachability(receiver: _StageRunReceiver) -> None:
    """Fault #1 must have been taken by the inner handler, exactly once."""
    assert len(receiver.backend.calls) == 1, receiver.backend.calls
    assert len(receiver.backend.fault_hits) == 1, receiver.backend.fault_hits
    assert len(receiver.session_scheduler.calls) == 1, receiver.session_scheduler.calls
    # The real route handed its own session context to the scheduler submit.
    assert receiver.session_scheduler.calls[0][0].backend is receiver.backend


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


# --------------------------------------------------------------------------
# RED fence: nested handler failure must not assert "not dispatched"
# --------------------------------------------------------------------------

def test_after_dispatch_nested_read_failure_must_not_claim_not_dispatched():
    """DEFECT-FENCING TEST — expected RED against the current product.

    Sequence actually executed:
      1. real admission + durable attempt + DISPATCH_INTENT write;
      2. fake solve emits one Study read and one ``run`` submission, which the
         real ``StageWorkerDispatchGate`` accepts, so the real durable write
         records ``worker-dispatch`` evidence with
         ``status=RUNNING``/``engine_dispatched=True`` (a FAKE witness);
      3. injected ordinary fault #1 (solve response loss) is caught by the
         inner ``except Exception`` and routed to ``unknown_result``;
      4. injected ordinary fault #2 fails the durable read at
         ``unknown_result`` line 3447, so the error escapes into the generic
         ``except Exception`` at _control_daemon.py:3189, whose stale CAS is
         rejected by the real store guard and whose response hardcodes
         ``engine_dispatched=False``.
    """
    receiver, ids = _new_receiver("after_dispatch_nested_fault")
    store, model_ref = receiver.store, ids["model_ref"]

    result = _dispatch_stage_run(receiver, ids["predecessor"]["attempt_id"],
                                 request_id="t31-req-nested-1",
                                 idempotency_key="t31-key-nested-1")

    # --- reachability hit records ----------------------------------------
    _assert_common_reachability(receiver)
    assert receiver.backend.fault_hits[0]["after_fake_dispatch_evidence"] is True
    assert receiver.backend.emitted_events[0]["phase"] == "submitted"
    # The fake Worker submission was persisted durably before the gate acted.
    fake_run_events = [row for row in _job_event_rows(store, _job_id_for(store))
                       if row["event"] == "worker_request"
                       and row["metadata"].get("kind") == "call"
                       and row["metadata"].get("metadata", {}).get("method") == "run"
                       and row["metadata"].get("phase") == "submitted"]
    assert len(fake_run_events) == 1, fake_run_events
    assert fake_run_events[0]["metadata"]["metadata"]["handle"] == FAKE_STUDY_HANDLE

    attempt_id = receiver.backend.solve_marker["attempt_id"]
    # The intent row was durable before the (synthetic) solve call.
    assert receiver.backend.solve_marker["phase"] == "solve"

    nested_hits = receiver.store.nested_read_fault_hits
    assert len(nested_hits) == 1, nested_hits
    assert nested_hits[0]["attempt_id"] == attempt_id
    assert nested_hits[0]["source"] == "_control_daemon.py"
    assert nested_hits[0]["function"] == "unknown_result", (
        "the injected durable read fault did not fire inside the inner UNKNOWN "
        f"handler; observed caller {nested_hits[0]!r}"
    )

    # Fault #2 escaped into the generic branch: _log_exception runs only at
    # _control_daemon.py:3190 on this route.
    assert len(receiver.log_exceptions) == 1, receiver.log_exceptions
    logged = receiver.log_exceptions[0]
    for marker in ("_execute_admitted_experiment_stage", "unknown_result",
                   "get_stage_attempt", InjectedDurableReadFailure.__name__,
                   "preflight_and_record"):
        assert marker in logged, f"missing generic-branch marker {marker!r}:\n{logged}"
    # Exception chaining: the generic branch sees fault #2 with fault #1 still
    # as the implicit context, so both injected names must be on record.
    assert InjectedSolveResponseLoss.__name__ in logged, (
        f"the original solve fault is missing from the chained traceback:\n{logged}"
    )

    # --- durable state after the cross-fault ------------------------------
    durable = store.get_stage_attempt(PROJECT_ID, model_ref, attempt_id)
    assert durable is not None
    assert durable["status"] == "RUNNING", durable
    assert durable["engine_dispatched"] is True, durable
    assert len(_dispatch_evidence(durable)) == 1, durable["evidence"]
    fake_witness = _dispatch_evidence(durable)[0]
    assert fake_witness["worker_method"] == "run"
    assert fake_witness["worker_receiver"] == FAKE_STUDY_HANDLE
    assert fake_witness["worker_request_id"].startswith("wrk-")
    # The inner handler died before it could publish an interruption row.
    assert not [row for row in durable["evidence"]
                if row.get("kind") == "stage-interruption"], durable["evidence"]
    assert durable["status"] != "UNKNOWN"

    # The real CAS guard forbids clearing that dispatch witness at all.
    cleared = None
    try:
        cleared = store.update_stage_attempt(
            PROJECT_ID, model_ref, attempt_id, expected_version=durable["version"],
            status="UNKNOWN", engine_dispatched=False, execution_status="UNKNOWN",
            acceptance_status="UNKNOWN", evidence=list(durable["evidence"]),
            result={"reason": "T31 guard probe"},
        )
    except StagePlanStoreConflict as exc:
        assert exc.code == "STAGE_ATTEMPT_STATE_UNKNOWN", exc.code
        assert "dispatch evidence cannot be cleared" in str(exc)
    assert cleared is None, "durable dispatch witness was unexpectedly clearable"

    # --- the returned envelope -------------------------------------------
    assert result["success"] is False, result
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN", result
    assert result["error"]["type"] == InjectedDurableReadFailure.__name__, result
    # Required today and after any repair: never invite an automatic retry.
    assert result["error"]["safe_retry"] is False, result
    assert _operation_row(store)["status"] == "UNKNOWN"

    # --- same-key replay may only observe, never re-dispatch --------------
    before_attempts = len(_attempt_rows(store, model_ref))
    replay = _dispatch_stage_run(receiver, ids["predecessor"]["attempt_id"],
                                 request_id="t31-req-nested-replay",
                                 idempotency_key="t31-key-nested-1")
    assert replay["success"] is False and replay["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert replay["error"]["safe_retry"] is False
    assert replay["data"]["engine_dispatched"] == result["data"]["engine_dispatched"]
    assert _operation_count(store) == 1
    _assert_no_second_admission_on_replay(receiver, store, model_ref, before_attempts)

    # --- the required invariants (expected RED on the unfixed product) -----
    response_claim = result["data"]["engine_dispatched"]
    assert not (durable["engine_dispatched"] is True and response_claim is False), (
        "DEFECT: the response asserts engine_dispatched=False although the durable "
        f"attempt still holds a recorded dispatch witness "
        f"(durable status={durable['status']} engine_dispatched={durable['engine_dispatched']} "
        f"version={durable['version']}; response claim={response_claim!r}). "
        "Either project the durable witness or say explicitly unknown "
        "(None with an unknown marker) — never a false negative."
    )
    returned_attempt = result["data"]["attempt"]
    assert not (returned_attempt is not None
                and returned_attempt.get("attempt_id") == durable["attempt_id"]
                and (returned_attempt.get("status") != durable["status"]
                     or returned_attempt.get("engine_dispatched")
                     != durable["engine_dispatched"]
                     or returned_attempt.get("version") != durable["version"])), (
        "DEFECT: the saved attempt contradicts the returned attempt "
        f"(durable status={durable['status']} version={durable['version']} "
        f"engine_dispatched={durable['engine_dispatched']}; returned "
        f"status={returned_attempt.get('status')} version={returned_attempt.get('version')} "
        f"engine_dispatched={returned_attempt.get('engine_dispatched')})"
    )


# --------------------------------------------------------------------------
# Controls (expected GREEN today, and after any repair)
# --------------------------------------------------------------------------

def test_after_dispatch_inner_unknown_handler_preserves_durable_witness():
    """Control: with the inner handler intact, the durable True witness wins.

    Only fault #1 is injected; ``unknown_result`` reads the durable attempt,
    keeps ``engine_dispatched`` True, marks UNKNOWN, and the response agrees.
    This isolates the RED test's trigger to the nested handler failure itself.
    """
    receiver, ids = _new_receiver("after_dispatch_clean")
    store, model_ref = receiver.store, ids["model_ref"]

    result = _dispatch_stage_run(receiver, ids["predecessor"]["attempt_id"],
                                 request_id="t31-req-clean-1",
                                 idempotency_key="t31-key-clean-1")

    _assert_common_reachability(receiver)
    assert receiver.store.nested_read_fault_hits == []
    assert receiver.log_exceptions == [], (
        "the intact inner handler must not reach the generic branch"
    )

    attempt_id = receiver.backend.solve_marker["attempt_id"]
    durable = store.get_stage_attempt(PROJECT_ID, model_ref, attempt_id)
    assert durable is not None
    assert durable["status"] == "UNKNOWN" and durable["engine_dispatched"] is True, durable
    assert any(row.get("kind") == "stage-interruption"
               and row.get("error_type") == InjectedSolveResponseLoss.__name__
               for row in durable["evidence"]), durable["evidence"]

    assert result["success"] is False
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert result["error"]["type"] == InjectedSolveResponseLoss.__name__
    assert result["error"]["safe_retry"] is False
    assert result["data"]["engine_dispatched"] is True
    returned = result["data"]["attempt"]
    assert returned["attempt_id"] == durable["attempt_id"]
    assert returned["status"] == durable["status"]
    assert returned["engine_dispatched"] == durable["engine_dispatched"]

    before = len(_attempt_rows(store, model_ref))
    replay = _dispatch_stage_run(receiver, ids["predecessor"]["attempt_id"],
                                 request_id="t31-req-clean-replay",
                                 idempotency_key="t31-key-clean-1")
    assert replay["data"]["engine_dispatched"] is True
    assert replay["error"]["safe_retry"] is False
    _assert_no_second_admission_on_replay(receiver, store, model_ref, before)


def test_nested_read_failure_without_dispatch_witness_stays_honest():
    """Control: the same nested read fault is honest when nothing dispatched.

    Fault #1 fires before any synthetic Worker event, so the durable attempt
    is still ``DISPATCH_INTENT`` with no dispatch witness; the escaped fault
    #2 reaches the generic branch, whose stale CAS is rejected (the inner
    handler had already bumped the durable version with its DISPATCH_INTENT
    write), so the durable row keeps ``engine_dispatched=False``.  The
    response must not claim a confirmed dispatch.  An explicit unknown is also
    acceptable, since absence of a durable witness need not prove no dispatch
    when persistence itself fails.  A returned attempt must not be stale.
    """
    receiver, ids = _new_receiver("before_dispatch_nested_fault")
    store, model_ref = receiver.store, ids["model_ref"]

    result = _dispatch_stage_run(receiver, ids["predecessor"]["attempt_id"],
                                 request_id="t31-req-before-1",
                                 idempotency_key="t31-key-before-1")

    _assert_common_reachability(receiver)
    assert receiver.backend.fault_hits[0]["after_fake_dispatch_evidence"] is False
    assert receiver.backend.emitted_events == []
    assert len(receiver.store.nested_read_fault_hits) == 1
    assert receiver.store.nested_read_fault_hits[0]["function"] == "unknown_result"
    assert len(receiver.log_exceptions) == 1
    assert InjectedDurableReadFailure.__name__ in receiver.log_exceptions[0]

    attempt_id = receiver.backend.solve_marker["attempt_id"]
    durable = store.get_stage_attempt(PROJECT_ID, model_ref, attempt_id)
    assert durable is not None
    # The outer UNKNOWN write was rejected by the version CAS, so the durable
    # row still reflects only the DISPATCH_INTENT write.
    assert durable["status"] == "DISPATCH_INTENT", durable
    assert durable["engine_dispatched"] is False, durable
    assert _dispatch_evidence(durable) == []

    assert result["success"] is False
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert result["error"]["safe_retry"] is False
    assert result["data"]["engine_dispatched"] is False or result["data"]["engine_dispatched"] is None
    returned = result["data"]["attempt"]
    if returned is not None:
        assert returned["attempt_id"] == durable["attempt_id"]
        assert returned["engine_dispatched"] == durable["engine_dispatched"]
        assert returned["version"] == durable["version"], (
            "returned attempt is stale compared with the observable durable attempt "
            f"(returned version={returned['version']} durable version={durable['version']})"
        )

    before = len(_attempt_rows(store, model_ref))
    replay = _dispatch_stage_run(receiver, ids["predecessor"]["attempt_id"],
                                 request_id="t31-req-before-replay",
                                 idempotency_key="t31-key-before-1")
    assert replay["error"]["safe_retry"] is False
    assert replay["data"]["engine_dispatched"] == result["data"]["engine_dispatched"]
    _assert_no_second_admission_on_replay(receiver, store, model_ref, before)
