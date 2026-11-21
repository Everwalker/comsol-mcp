"""T31/U07: safety of the caller-facing hint when stage_define registration
seals an EXECUTION_STATE_UNKNOWN result.

Targeted inconsistency (source-observed, not touched here):
the generic ``except Exception`` branch of the captured ``register_definition``
closure (_control_daemon.py:2799-2808) seals a durable UNKNOWN whose message
currently instructs the caller to "replay with the same idempotency key".  But
the very same route's reused branch (_control_daemon.py:2699-2700) returns the
stored result for a replayed key without ever re-admitting work, so a same-key
replay re-runs NOTHING — proven here by the captured-callback submit count and
independently fenced by tests/test_t31_stage_define_registration_parent_20261111.py
::``test_unknown_result_replay_retains_original_failure_without_resubmit``
(read, not modified).  The hint must therefore distinguish "query the original
job" from "re-execute", and must not invite an automatic retry/re-submission.

What is REAL in these tests (product code under test):
- ``ControlDaemon._dispatch_experiment_stage_define`` runs unbound against an
  inert fake receiver (fixture receiver below); no ControlDaemon is constructed.
- The actual captured ``register_definition`` closure — including its generic
  ``except Exception`` → ``_log_exception`` → ``_error`` → ``_finish(...,
  "UNKNOWN")`` path (_control_daemon.py:2799-2808) — executes exactly once.
- ``_error``/``_finish`` run for real: the UNKNOWN envelope's layout (empty
  ``data``; ``safe_retry``/``engine_dispatched``/``type`` under ``error``;
  stamped ``execution`` ids at _control_daemon.py:13598-13600 and 13019-13022)
  is asserted exactly as returned, not as hoped-for.
- The real ``OperationStore`` on ``":memory:"`` seals UNKNOWN durably and keeps
  ``finished_at`` unset (documented at _operation_store.py:1876-1879, coded at
  1912-1915), then replays the stored envelope for the same key + payload.

What is FAKE (deliberate, bounded, and NOT an authorization/scheduling proof):
- ``_authorize_project_execution`` and ``project_authority.apply_timeout_caps``
  are the fixture's inert stubs; session context is ``None`` so the closure
  resolves the fake ``_default_backend``/ledger below.
- ``_FakeLedger._state_for`` records one hit inside the closure and then raises
  this test's synthetic RuntimeError — a test-only trigger that lands the
  closure in the product's own generic except clause.
- ``session_scheduler`` only captures ``(context, callback)``; the submit count
  measures scheduler admission calls only — never real Worker/engine dispatch,
  network, COMSOL work, or real project permission.
- No disk SQLite, no Worker, no network, no ControlDaemon instance.  The only
  UNKNOWN ever created here is a fresh in-memory row owned by this test; the
  test never queries, repairs, clears, or replays any historical/real UNKNOWN.

EXPECTATION: every assertion up to the final message-fence block passes on the
current code and proves the real UNKNOWN path was reached.  The message fence
block is the requirement and is expected RED until the hint at
_control_daemon.py:2801-2807 is corrected.  These tests were written, not
executed, per task rules.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from comsol_mcp._control_daemon import ControlDaemon
from tests.test_t31_stage_define_pending_idempotency_20261111 import (
    _StageDefineReceiver,
    _count,
    _dispatch,
    _execution_envelope,
    _job_rows,
    _operation_rows,
    _stage_plan,
)

# Test-only marker: the synthetic RuntimeError raised inside the fake ledger so
# the captured closure enters the product's generic except Exception branch.
_SYNTHETIC_FAILURE = "t31-synthetic-register-definition-crash-20261111"

# Exact current hint phrase (_control_daemon.py:2803).  A corrected hint may
# reword or qualify the replay observation (e.g. "replaying the same key only
# re-returns this result"), but must not keep the bare imperative form.
_BANNED_HINT_PHRASE = "replay with the same idempotency key"


class _FakeLedger:
    """Records the closure's state lookup, optionally crashing right after."""

    def __init__(self, *, session_id: str, model_ref: dict, ledger_calls: list[str],
                 crash_after_state: bool):
        self.session_id = session_id
        self._model_ref = model_ref
        self._ledger_calls = ledger_calls
        self._crash_after_state = crash_after_state

    def _state_for(self, ref):
        assert ref.as_dict() == self._model_ref
        self._ledger_calls.append("ledger_state")
        if self._crash_after_state:
            raise RuntimeError(_SYNTHETIC_FAILURE)
        return SimpleNamespace(active_operation_id=None, dirty=False, revision=0)


class _FakeBackend:
    """Fake backend/ledger pair; reached only because the fake session
    context is None (closure falls back to ``self._default_backend``)."""

    def __init__(self, *, model_ref: dict, ledger_calls: list[str], crash_after_state: bool):
        self._model_ref = model_ref
        self._ledger_calls = ledger_calls
        self.service = SimpleNamespace(ledger=_FakeLedger(
            session_id=model_ref["session_id"], model_ref=model_ref,
            ledger_calls=ledger_calls, crash_after_state=crash_after_state,
        ))

    def _model_project_key(self, observed):
        assert observed == self._model_ref
        self._ledger_calls.append("model_project_key")
        return "memory-only-model-key"


def _arm_fake_session_surface(receiver: Any, *, execution: dict, ledger_calls: list[str],
                              crash_after_state: bool) -> None:
    """Install the fake context/backend (never real session connectivity)."""
    # The shared receiver assigns this static method as a plain class function,
    # which incorrectly injects self when the UNKNOWN branch calls it.
    receiver._error = ControlDaemon._error
    receiver._execution_session_context = lambda _execution: None
    receiver._default_backend = _FakeBackend(
        model_ref=dict(execution["model_ref"]), ledger_calls=ledger_calls,
        crash_after_state=crash_after_state,
    )


def test_registration_unknown_hint_distinguishes_job_query_from_retry():
    """DEFECT-FENCING TEST — the path assertions pass today; the final message
    fence is expected RED against _control_daemon.py:2801-2807.

    Ordering matters: every assertion proving the real UNKNOWN path (fresh
    QUEUED admission, one captured submit, closure executed once, generic
    except entered, exact return layout, durable UNKNOWN rows, same-key replay
    with no resubmit) runs BEFORE the message fence, so a RED failure can only
    come from the hint itself, never from the path.
    """
    receiver: Any = _StageDefineReceiver()
    store = receiver.store
    scheduler = receiver.session_scheduler
    execution = _execution_envelope(request_id="t31-unknown-msg-first",
                                    idempotency_key="t31-unknown-msg-key")
    ledger_calls: list[str] = []
    try:
        _arm_fake_session_surface(receiver, execution=execution,
                                  ledger_calls=ledger_calls, crash_after_state=True)

        # 1. Normal first dispatch: fresh QUEUED operation/job, exactly one
        #    captured scheduler.submit (an admission count, NOT a real
        #    Worker/engine dispatch or permission proof).
        plan = _stage_plan()
        first = _dispatch(receiver, definition=plan, execution=execution)
        assert first["success"] is True and first["data"]["status"] == "QUEUED", first
        operation_id = first["execution"]["operation_id"]
        job_id = first["execution"]["job_id"]
        request_hash = first["execution"]["request_hash"]
        rows = _operation_rows(store)
        jobs = _job_rows(store)
        assert len(rows) == 1 and rows[0]["operation_id"] == operation_id
        assert rows[0]["status"] == "QUEUED" and rows[0]["result"] is None
        assert len(jobs) == 1 and jobs[0]["job_id"] == job_id
        assert jobs[0]["status"] == "QUEUED"
        assert len(scheduler.calls) == 1, scheduler.calls
        # Typed as Any: the fixture annotates calls as tuple[object, object];
        # the real captured closure returns dict envelopes (asserted below).
        context: Any = scheduler.calls[0][0]
        register_definition: Any = scheduler.calls[0][1]
        assert context is None and callable(register_definition)
        assert getattr(register_definition, "__name__", "") == "register_definition"
        assert ledger_calls == []  # captured only; the closure has not run yet

        # 2. Manually run the only captured real closure, exactly once.  The
        #    synthetic RuntimeError raised just after the ledger state lookup
        #    must enter the product's own except Exception branch.
        unknown = register_definition()
        assert ledger_calls == ["ledger_state"], ledger_calls
        assert len(receiver.log_exceptions) == 1
        assert _SYNTHETIC_FAILURE in receiver.log_exceptions[0], receiver.log_exceptions[0]

        # 3. Exact returned field layout from _error (_control_daemon.py:
        #    13598-13600) + _finish stamping (13019-13022): engine_dispatched
        #    and safe_retry live under "error"; "data" stays empty.
        assert unknown["success"] is False, unknown
        assert unknown["data"] == {}, unknown
        error = unknown["error"]
        assert error["code"] == "EXECUTION_STATE_UNKNOWN", error
        assert error["safe_retry"] is False, error
        assert error["engine_dispatched"] is False, error
        assert error["type"] == "RuntimeError", error
        assert isinstance(error["message"], str) and error["message"]
        stamped = unknown["execution"]
        assert stamped["operation_id"] == operation_id
        assert stamped["job_id"] == job_id
        assert stamped["request_id"] == "t31-unknown-msg-first"
        assert stamped["request_hash"] == request_hash
        assert stamped["idempotency_key"] == "t31-unknown-msg-key"

        # 4. Durable UNKNOWN persistence (real OperationStore): both rows
        #    UNKNOWN with finished_at left unset and the result envelope kept;
        #    nothing was registered because the closure crashed at the ledger.
        assert store.job(job_id)["status"] == "UNKNOWN"
        assert store.job(job_id)["result"] == unknown
        op_row = store.db.execute(
            "SELECT status, finished_at FROM operations WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        assert op_row["status"] == "UNKNOWN" and op_row["finished_at"] is None
        job_row = store.db.execute(
            "SELECT status, finished_at FROM jobs WHERE job_id=?", (job_id,),
        ).fetchone()
        assert job_row["status"] == "UNKNOWN" and job_row["finished_at"] is None
        assert _count(store, "artifacts") == 0 and _count(store, "stage_attempts") == 0

        # 5. Same key + same payload, only a new request_id: the reused branch
        #    (_control_daemon.py:2699-2700) hands back the stored UNKNOWN with
        #    the ORIGINAL operation/job/request identity and admits nothing new
        #    (no second key is ever submitted by this test).
        replay = _dispatch(receiver, definition=plan,
                           execution={**execution, "request_id": "t31-unknown-msg-replay"})
        assert replay == unknown, replay
        assert replay["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert replay["error"]["safe_retry"] is False
        assert replay["execution"]["operation_id"] == operation_id
        assert replay["execution"]["job_id"] == job_id
        assert replay["execution"]["request_id"] == "t31-unknown-msg-first"
        assert len(scheduler.calls) == 1, "a same-key UNKNOWN replay must not re-submit"
        assert ledger_calls == ["ledger_state"], "replay must not re-run the closure"
        assert len(_operation_rows(store)) == 1 and len(_job_rows(store)) == 1

        # ---- TARGET MESSAGE FENCE (expected RED on current code) ----------
        # The hint must (a) point the caller at the original job for status
        # queries, (b) explicitly forbid automatic retry/re-submission, and
        # (c) not keep the unqualified "replay with the same idempotency key"
        # instruction, because the replay behaviour proven above re-returns the
        # stored UNKNOWN and never re-executes the registration.
        message = error["message"].lower()
        assert "job" in message, (
            "UNKNOWN hint must reference the durable job the caller can query: "
            f"got {error['message']!r}"
        )
        assert "do not" in message and "retry" in message, (
            "UNKNOWN hint must explicitly forbid automatic retry/re-submission: "
            f"got {error['message']!r}"
        )
        assert _BANNED_HINT_PHRASE not in message, (
            "UNKNOWN hint must not carry the unqualified replay-with-same-key "
            f"instruction it currently does: got {error['message']!r}"
        )
    finally:
        store.close()


def test_healthy_stage_define_registration_control():
    """Positive control on its own fresh receiver/store: with the same fake
    ledger NOT crashing, the captured closure registers exactly one plan and
    seals SUCCEEDED — showing the synthetic RuntimeError is the only deviation
    from the healthy path exercised above."""
    receiver: Any = _StageDefineReceiver()
    store = receiver.store
    scheduler = receiver.session_scheduler
    execution = _execution_envelope(request_id="t31-unknown-msg-ok-first",
                                    idempotency_key="t31-unknown-msg-ok-key")
    model_ref = dict(execution["model_ref"])
    ledger_calls: list[str] = []
    try:
        _arm_fake_session_surface(receiver, execution=execution,
                                  ledger_calls=ledger_calls, crash_after_state=False)
        store.put_metadata("revisions", "memory-only-model-key", {
            "model_ref": model_ref, "project_id": execution["project_id"],
            "attribution": "PROJECT_BOUND", "revision": 0, "dirty": False,
        })

        plan = _stage_plan()
        first = _dispatch(receiver, definition=plan, execution=execution)
        assert first["success"] is True and first["data"]["status"] == "QUEUED", first
        assert len(scheduler.calls) == 1, scheduler.calls
        context: Any = scheduler.calls[0][0]
        register_definition: Any = scheduler.calls[0][1]
        assert context is None and callable(register_definition)
        assert ledger_calls == []

        result = register_definition()
        assert result["success"] is True, result
        assert result["data"]["registration"] == "CREATED"
        assert result["data"]["worker_rpc_performed"] is False
        assert result["data"]["solve_started"] is False
        assert result["execution"]["operation_id"] == first["execution"]["operation_id"]
        assert result["execution"]["job_id"] == first["execution"]["job_id"]
        assert ledger_calls == ["ledger_state", "model_project_key"], ledger_calls
        assert store.job(first["execution"]["job_id"])["status"] == "SUCCEEDED"
        assert len(scheduler.calls) == 1
        assert len(_operation_rows(store)) == 1 and len(_job_rows(store)) == 1
    finally:
        store.close()
