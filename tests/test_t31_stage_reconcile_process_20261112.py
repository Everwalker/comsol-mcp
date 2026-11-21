"""T31/U07: cross-process SQLite reopen + same-key read-only replay of one
synthetic ``experiment.stage_define`` durable record (2026-11-12).

Scope discipline (read this before trusting any assertion below):
Each test scenario is handled by exactly TWO sequential, independent,
task-owned Python subprocesses (this same file re-invoked as ``__main__``)
over one brand-new, exclusively created SQLite database file:

- Process A admits one synthetic stage_define through the REAL unbound
  ``ControlDaemon._dispatch_admitted`` + its REAL stage_define route on an
  inert fake receiver, leaving one
  QUEUED operation/job.  For the RUNNING scenario it then applies a REAL
  ``OperationStore.transition_status`` CAS QUEUED->RUNNING to *simulate* a
  claim; nothing else runs.  It closes the
  store and exits normally.
- Process B opens the same task-owned database file with a fresh REAL
  ``OperationStore``, calls the REAL ``OperationStore.reconcile_after_restart``
  directly (whose ``update_job`` also mirrors status into ``operations``), and
  replays the SAME idempotency key
  and payload through the same REAL route, expecting the original operation/
  job/request identity, RECONCILING status, zero scheduler admissions and
  unchanged record counts.  Only the same key is ever replayed here; there is
  deliberately NO cross-key retry in process B (key isolation is fenced by
  tests/test_t31_stage_define_pending_idempotency_20261111.py).

What is REAL here: ``OperationStore`` on a real file-backed SQLite database
(WAL, ``_migrate`` schema), ``begin``,
``transition_status``, ``reconcile_after_restart``, the ``_dispatch_admitted``
/ stage_define routes as unbound functions, the real ``validate_call``/
``validate_stage_plan_definition``/``canonical_request_hash`` path, and
``ControlDaemon._pending``.

What is FAKE (bounded, never an attestation): no ControlDaemon instance is
ever constructed, started or wired — ``reconcile_after_restart`` is invoked
directly and this must NOT be read as "the daemon restarted".  Authorization
(``_authorize_project_execution``), project timeout caps, session context and
the session scheduler are inert stubs copied in spirit from the read
(never imported) scaffolding of tests/
test_t31_stage_define_pending_idempotency_20261111.py (HEAD ba5f1598 candidate
source); the scheduler callback is captured, never executed, so submit counts
measure route admissions only, never Worker/COMSOL dispatch.  All plan data,
identities and the database file are synthetic and task-owned; NO historical
UNKNOWN job or database is read or reproduced.

If they pass, these tests establish ONLY: the persisted synthetic record of a
stage_define survives a store close + normal child-process exit, reopens in a
separate process, is moved QUEUED->RECONCILING or RUNNING->RECONCILING by the
real reconciliation call, and a same-key replay reuses that record without
new rows or re-admission. They do NOT prove: process crash/SIGKILL, fsync/os-level
durability, hardware restart, daemon startup wiring, real scheduler/worker/
backend behaviour, real project authorization or session connectivity, native
COMSOL execution, UNKNOWN semantics across reopen, or concurrent access.

Scratch hygiene: each test creates an exclusive directory beneath a supplied
``T31X_RECONCILE_SCRATCH_DIR``, or under a fresh private temporary base when no
runner is supplied. The restricted parent runner also supplies an
identity-checked child launcher. The test does not explicitly delete files;
SQLite may manage its own journal sidecars. Every database
path handed to a child is absolute, bound on the command line, and re-checked
by the child (must-be-new in phase A, must-exist in phase B) — never a
default or ``:memory:`` binding.

The author's draft was not executed. Execution status for any revision must be
read from its own source-bound run record. Two test functions = two
scenarios = two databases; within one scenario the two children run strictly
sequentially (the orchestrator waits on each returncode).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import traceback
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

# The child processes are this very file; bind the repo root from __file__ so
# the import works no matter what cwd/sys.path the parent runner supplies.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._operation_store import OperationStore

_SCRATCH_ENV = "T31X_RECONCILE_SCRATCH_DIR"
_DB_NAME = "t31x-operations.sqlite3"
_PHASE_A_MARKER = "T31X_PHASE_A_OK"
_PHASE_B_MARKER = "T31X_PHASE_B_OK"

# Sentinel returned by the fake ``_execution_session_context``; the fake
# scheduler asserts it is the context it receives, proving the real route
# handed its session context to ``session_scheduler.submit`` (phase A only).
_SESSION_CONTEXT_SENTINEL = object()


def _stage_plan(plan_id="heat-cycle", first_id="preheat"):
    """Synthetic v1 stage plan (read-not-imported reproduction of the
    validated shape in tests/test_stage_definition.py::``_plan``; checked by
    the real ``validate_call``/``validate_stage_plan_definition`` anyway)."""
    return {
        "plan_id": plan_id,
        "stages": [
            {
                "stage_id": first_id,
                "ordinal": 1,
                "depends_on": [],
                "study_target": {"segments": [{"collection": "study", "tag": "std1"}]},
                "source_selection": {"kind": "initial_state", "strategy": "declared_initial"},
                "target_selection": {"dataset": "dset1", "solution": "sol1"},
                "variable_mappings": [{
                    "source_variable": "T", "target_variable": "T_preheat",
                    "source_unit": "K", "target_unit": "K", "mapping_method": "identity",
                }],
                "reference_state": {"strategy": "initial_state"},
                "checks": [{
                    "kind": "continuity", "check_id": "temperature-boundary",
                    "source_variable": "T", "target_variable": "T_preheat", "unit": "K",
                    "tolerance": {"absolute": 0.1, "relative": 0.0},
                }],
            },
            {
                "stage_id": "cooldown",
                "ordinal": 2,
                "depends_on": [first_id],
                "study_target": {"segments": [{"collection": "study", "tag": "std2"}]},
                "source_selection": {"kind": "stage", "stage_id": first_id,
                                     "selection": {"dataset": "dset1", "solution": "sol1"}},
                "target_selection": {"dataset": "dset2", "solution": "sol2"},
                "variable_mappings": [{
                    "source_variable": "T_preheat", "target_variable": "T_cool",
                    "source_unit": "K", "target_unit": "K", "mapping_method": "interpolate",
                }],
                "reference_state": {"strategy": "predecessor_stage", "source_stage_id": first_id},
                "checks": [{
                    "kind": "conservation", "check_id": "temperature-balance",
                    "quantity": "temperature",
                    "terms": [
                        {"side": "source", "variable": "T_preheat", "coefficient": 1.0},
                        {"side": "target", "variable": "T_cool", "coefficient": -1.0},
                    ],
                    "unit": "K", "tolerance": {"absolute": 0.2, "relative": 0.01},
                }],
            },
        ],
    }


def _execution_envelope(*, request_id, idempotency_key):
    """Execution envelope carrying the full managed identity (outer is
    authoritative, exactly as the production stage_define route expects).
    Identities are t31x-synthetic; nothing historical is reused."""
    return {
        "project_id": "project-t31x-offline",
        "session_id": "session-t31x-offline",
        "model_ref": {
            "schema_version": 1,
            "session_id": "session-t31x-offline",
            "server_instance_id": "server-t31x-offline",
            "model_tag": "model-t31x-offline",
            "generation": 1,
        },
        "expected_revision": 0,
        "idempotency_key": idempotency_key,
        "request_id": request_id,
    }


class _InFlightFuture:
    """Future modelling an admitted-but-unfinished scheduler callback."""

    def result(self, timeout=None):
        raise FutureTimeout("t31x fake future: stage definition still in flight")


class _InFlightScheduler:
    """Fake ``session_scheduler``: captures submits, never runs callbacks."""

    def __init__(self):
        self.calls: list[tuple[object, object]] = []
        self.executed: list[object] = []  # stays empty by construction

    def submit(self, context, callback):
        self.calls.append((context, callback))
        return _InFlightFuture()


class _FakeProjectAuthority:
    """Inert timeout-cap passthrough (explicitly NOT real project authority)."""

    def apply_timeout_caps(self, project_id, timeouts):
        assert isinstance(project_id, str) and project_id
        return dict(timeouts)


class _StageDefineReceiver:
    """Inert receiver for the unbound ControlDaemon methods; the REAL
    file-backed OperationStore is injected by the phase, never created
    in-memory, because the whole point is the cross-process file reopen."""

    _dispatch_experiment_stage_define = ControlDaemon._dispatch_experiment_stage_define
    _pending = ControlDaemon._pending
    _finish = ControlDaemon._finish
    _release_terminal_accounting = ControlDaemon._release_terminal_accounting
    _release_terminal_disk_reservation = ControlDaemon._release_terminal_disk_reservation
    _release_terminal_resource_reservations = ControlDaemon._release_terminal_resource_reservations
    _error = staticmethod(ControlDaemon._error)
    _exception = ControlDaemon._exception
    # Re-wrap explicitly as a staticmethod: plain attribute access would hand
    # the receiver as a first positional argument and break the call signature.
    _timeouts = staticmethod(ControlDaemon._timeouts)

    def __init__(self, store: OperationStore):
        self.lock = threading.RLock()
        self.store = store
        self.session_scheduler = _InFlightScheduler()
        self.project_authority = _FakeProjectAuthority()
        self._disk_resource_coordinator = None
        self._memory_resource_coordinator = None
        self.log_exceptions: list[str] = []

    # --- FAKE security/session surface (documented in the module docstring) -
    def _authorize_project_execution(self, operation, arguments, execution):
        # Deliberately inert: NOT a project-policy/authorization proof.
        return None

    def _execution_session_context(self, execution):
        return _SESSION_CONTEXT_SENTINEL

    def _log_exception(self):
        self.log_exceptions.append(traceback.format_exc())


def _dispatch(receiver, *, definition, execution):
    """One admission through the REAL unbound _dispatch_admitted route."""
    return ControlDaemon._dispatch_admitted(receiver, {
        "operation": "experiment.stage_define",
        "arguments": {"definition": definition},
        "execution": dict(execution),
    })


def _operation_rows(store):
    return store.db.execute(
        "SELECT operation_id,request_id,idempotency_key,request_hash,operation,status,result"
        " FROM operations ORDER BY rowid"
    ).fetchall()


def _job_rows(store):
    return store.db.execute(
        "SELECT job_id,operation_id,status FROM jobs ORDER BY rowid"
    ).fetchall()


def _count(store, table):
    return store.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _bound_db_path(raw: str) -> Path:
    """Every child re-binds its database path: absolute, explicit, never a
    default and never ``:memory:``."""
    db = Path(raw)
    if not db.is_absolute():
        raise AssertionError(f"database path must be absolute and bound, got {raw!r}")
    if str(db) == ":memory:":
        raise AssertionError("in-memory binding cannot exercise cross-process reopen")
    return db


# ---------------------------------------------------------------------------
# Phase A: one real admission (REAL route + fake scheduler) and a real
# transition_status CAS when the scenario simulates a claim.  Normal close.
# ---------------------------------------------------------------------------

def _phase_produce(*, db_path: str, scenario: str, summary_path: Path) -> dict:
    db = _bound_db_path(db_path)
    if db.exists() or db.is_symlink():
        raise AssertionError(f"task-owned database must be brand new: {db} already exists")
    # The new database is reserved exclusively; SQLite subsequently opens only
    # this same task-owned path. Never overwrite an existing file or symlink.
    descriptor = os.open(db, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    store = OperationStore(db)
    try:
        receiver = _StageDefineReceiver(store)
        idempotency_key = f"t31x-key-{scenario}"
        response = _dispatch(receiver, definition=_stage_plan(),
                             execution=_execution_envelope(request_id="t31x-produce-req",
                                                           idempotency_key=idempotency_key))
        assert response["success"] is True, response
        assert response["data"]["status"] == "QUEUED", response
        assert response["data"]["rpc_wait_expired"] is True, response
        assert response["data"]["engine_dispatched"] is False, response
        assert response["data"]["stage_attempt"] is None, response

        # The first request really reached the fake scheduler exactly once and
        # its callback was captured but never executed (no registration ran).
        assert len(receiver.session_scheduler.calls) == 1, receiver.session_scheduler.calls
        context, callback = receiver.session_scheduler.calls[0]
        assert context is _SESSION_CONTEXT_SENTINEL
        assert getattr(callback, "__name__", "") == "register_definition"
        assert receiver.session_scheduler.executed == []
        assert receiver.log_exceptions == []

        rows = _operation_rows(store)
        jobs = _job_rows(store)
        assert len(rows) == 1 and rows[0]["operation"] == "experiment.stage_define"
        assert rows[0]["status"] == "QUEUED" and rows[0]["result"] is None
        assert len(jobs) == 1 and jobs[0]["status"] == "QUEUED"
        assert jobs[0]["operation_id"] == rows[0]["operation_id"]
        assert _count(store, "artifacts") == 0 and _count(store, "stage_attempts") == 0
        job_id = jobs[0]["job_id"]

        if scenario == "running":
            # REAL CAS simulating a claim QUEUED->RUNNING.  Verified to also
            # mirror the status into operations (_operation_store.py:2073-2079).
            accepted, authoritative, updated = store.transition_status(
                job_id, ("QUEUED",), "RUNNING")
            assert accepted is True and authoritative == "RUNNING", (accepted, authoritative)
            assert updated is not None and updated["status"] == "RUNNING", updated

        expected = "RUNNING" if scenario == "running" else "QUEUED"
        row_after = _operation_rows(store)[0]
        job_after = _job_rows(store)[0]
        assert job_after["status"] == expected, job_after
        assert row_after["status"] == expected, row_after
        assert row_after["result"] is None

        summary = {
            "phase": "produce",
            "pid": os.getpid(),
            "scenario": scenario,
            "db_path": str(db),
            "operation_id": row_after["operation_id"],
            "job_id": job_id,
            "request_id": row_after["request_id"],
            "request_hash": row_after["request_hash"],
            "idempotency_key": row_after["idempotency_key"],
            "operation": row_after["operation"],
            "operation_status_after": row_after["status"],
            "job_status_after": job_after["status"],
            "operation_result_null": row_after["result"] is None,
            "scheduler_submits": len(receiver.session_scheduler.calls),
            "scheduler_executed": len(receiver.session_scheduler.executed),
            "counts": {
                "operations": _count(store, "operations"),
                "jobs": _count(store, "jobs"),
                "artifacts": _count(store, "artifacts"),
                "stage_attempts": _count(store, "stage_attempts"),
            },
        }
    finally:
        # Normal, deliberate close: the crash path is explicitly NOT modelled.
        store.close()
    return summary


# ---------------------------------------------------------------------------
# Phase B: independent process reopens the same owned file, calls the REAL
# reconcile_after_restart directly (no daemon start claimed), then replays
# the SAME key through the REAL route expecting read-only reuse.
# ---------------------------------------------------------------------------

def _phase_reconcile_replay(*, db_path: str, scenario: str,
                            prior_summary_path: Path) -> dict:
    db = _bound_db_path(db_path)
    if db.is_symlink() or not db.is_file():
        raise AssertionError(f"reopen requires the phase-A database: {db} missing")
    prior = json.loads(prior_summary_path.read_text())
    assert prior["scenario"] == scenario and prior["db_path"] == str(db), prior

    store = OperationStore(db)
    try:
        # The reopened file must contain exactly what phase A reported, with
        # no phase-A process state carried over except durable rows.
        rows = _operation_rows(store)
        jobs = _job_rows(store)
        assert len(rows) == 1 and len(jobs) == 1, (len(rows), len(jobs))
        assert rows[0]["operation_id"] == prior["operation_id"]
        assert rows[0]["request_id"] == prior["request_id"]
        assert rows[0]["idempotency_key"] == prior["idempotency_key"]
        assert rows[0]["request_hash"] == prior["request_hash"]
        assert rows[0]["operation"] == "experiment.stage_define"
        assert rows[0]["result"] is None
        job_id = prior["job_id"]
        assert jobs[0]["job_id"] == job_id
        expected_before = "RUNNING" if scenario == "running" else "QUEUED"
        assert jobs[0]["status"] == expected_before, jobs[0]

        # REAL reconciliation, called directly on the store.  This is NOT a
        # ControlDaemon restart attestation.  update_job (1951-2011) mirrors
        # the new status into the operations row as well.
        changed = store.reconcile_after_restart()
        assert changed == [job_id], changed
        assert _job_rows(store)[0]["status"] == "RECONCILING"
        assert _operation_rows(store)[0]["status"] == "RECONCILING"

        # Same-key read-only replay through the REAL route.  No cross-key
        # retry is attempted anywhere in this phase by design.
        receiver = _StageDefineReceiver(store)
        replay = _dispatch(receiver, definition=_stage_plan(),
                           execution=_execution_envelope(request_id="t31x-replay-req",
                                                         idempotency_key=prior["idempotency_key"]))
        assert replay["success"] is True, replay
        assert replay["data"]["status"] == "RECONCILING", replay
        assert replay["data"]["job_id"] == job_id, replay
        execution = replay["execution"]
        # Original durable identity is preserved; the replay's new request_id
        # must not shadow the phase-A request identity.
        assert execution["operation_id"] == prior["operation_id"], execution
        assert execution["job_id"] == job_id, execution
        assert execution["request_hash"] == prior["request_hash"], execution
        assert execution["request_id"] == prior["request_id"], execution
        assert execution["idempotency_key"] == prior["idempotency_key"], execution

        # No re-dispatch and no new durable effects anywhere.
        assert len(receiver.session_scheduler.calls) == 0, receiver.session_scheduler.calls
        assert receiver.session_scheduler.executed == []
        assert receiver.log_exceptions == []
        assert _count(store, "operations") == 1
        assert _count(store, "jobs") == 1
        assert _count(store, "artifacts") == 0
        assert _count(store, "stage_attempts") == 0
        assert _operation_rows(store)[0]["result"] is None

        summary = {
            "phase": "reconcile-replay",
            "pid": os.getpid(),
            "scenario": scenario,
            "db_path": str(db),
            "operation_id": prior["operation_id"],
            "job_id": job_id,
            "request_id": prior["request_id"],
            "request_hash": prior["request_hash"],
            "idempotency_key": prior["idempotency_key"],
            "job_status_before_reconcile": expected_before,
            "reconcile_returned_job_ids": list(changed),
            "job_status_after_reconcile": _job_rows(store)[0]["status"],
            "operation_status_after_reconcile": _operation_rows(store)[0]["status"],
            "replay": {
                "data_status": replay["data"]["status"],
                "job_id": replay["data"]["job_id"],
                "operation_id": execution["operation_id"],
                "request_id": execution["request_id"],
                "request_hash": execution["request_hash"],
                "idempotency_key": execution["idempotency_key"],
                "replay_request_id_sent": "t31x-replay-req",
            },
            "scheduler_submits": len(receiver.session_scheduler.calls),
            "counts": {
                "operations": _count(store, "operations"),
                "jobs": _count(store, "jobs"),
                "artifacts": _count(store, "artifacts"),
                "stage_attempts": _count(store, "stage_attempts"),
            },
        }
    finally:
        store.close()
    return summary


# ---------------------------------------------------------------------------
# Child entry point
# ---------------------------------------------------------------------------

def _child_main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="test_t31_stage_reconcile_process_20261112.py",
        description="t31x phase child: one owned synthetic stage_define ledger "
                    "across two sequential processes (see module docstring).")
    parser.add_argument("--phase", choices=("produce", "reconcile-replay"), required=True)
    parser.add_argument("--db", required=True,
                        help="absolute, task-owned SQLite path bound by the parent test")
    parser.add_argument("--scenario", choices=("queued", "running"), required=True)
    parser.add_argument("--summary", required=True, help="JSON summary output path")
    parser.add_argument("--prior-summary", default=None)
    args = parser.parse_args(argv)

    # This binds a test child to its orchestrator's scenario directory; it is
    # not a general filesystem-ownership or project-authorization primitive.
    scenario_dir = Path.cwd()
    if scenario_dir.name != f"{args.scenario}-across-process":
        raise AssertionError("child cwd must be the matching scenario directory")
    if Path(args.db) != scenario_dir / _DB_NAME:
        raise AssertionError("child database must be the scenario database")
    summary_path = Path(args.summary)
    expected_summary = scenario_dir / ("phaseA.json" if args.phase == "produce" else "phaseB.json")
    if summary_path != expected_summary:
        raise AssertionError("child summary must be the matching scenario file")
    if args.phase == "produce":
        if args.prior_summary is not None:
            parser.error("produce phase takes no --prior-summary")
        summary = _phase_produce(db_path=args.db, scenario=args.scenario,
                                 summary_path=summary_path)
        marker = _PHASE_A_MARKER
    else:
        if args.prior_summary != str(scenario_dir / "phaseA.json"):
            parser.error("reconcile-replay requires the matching prior summary")
        summary = _phase_reconcile_replay(db_path=args.db, scenario=args.scenario,
                                          prior_summary_path=Path(args.prior_summary))
        marker = _PHASE_B_MARKER

    with summary_path.open("x", encoding="utf-8") as output:
        output.write(json.dumps(summary, indent=2, sort_keys=True))
    print(f"{marker} scenario={args.scenario} db={summary['db_path']} "
          f"operation_id={summary['operation_id']} job_id={summary['job_id']}")
    return 0


# ---------------------------------------------------------------------------
# Parent orchestrator (pytest surface): launches the two sequential children
# and re-asserts the contract from the durable, task-owned JSON summaries.
# ---------------------------------------------------------------------------

def _owned_scratch(test_name: str) -> Path:
    """Explicitly owned scratch; deliberately NEVER deleted (no pytest
    tmp_path/rmtree here — the parent audit keeps the database afterwards)."""
    env_dir = os.environ.get(_SCRATCH_ENV)
    base = Path(env_dir) if env_dir else Path(tempfile.mkdtemp(prefix="t31x-reconcile-"))
    if not base.is_absolute() or base.is_symlink() or not base.is_dir():
        raise AssertionError(f"{_SCRATCH_ENV} must name an existing non-symlink absolute directory")
    base = base.resolve(strict=True)
    scratch = base / test_name
    if scratch.exists() or scratch.is_symlink():
        raise AssertionError(f"scratch must be exclusive and new: {scratch} already exists")
    scratch.mkdir(mode=0o700)
    return scratch


def _run_child(*, phase: str, marker: str, scratch: Path, db_path: Path,
               scenario: str, summary_name: str,
               prior_summary: Path | None = None) -> dict:
    summary_path = scratch / summary_name
    launcher = os.environ.get("T31X_CHILD_LAUNCHER")
    if launcher is not None and (not launcher or not Path(launcher).is_absolute()):
        raise AssertionError("T31X_CHILD_LAUNCHER must be non-empty and absolute")
    guarded = launcher is not None
    launcher = launcher or str(Path(__file__).resolve())
    cmd = [sys.executable, "-I", "-B", "-X", f"pycache_prefix={scratch / ('pycache-' + phase)}", launcher,
           "--phase", phase, "--db", str(db_path), "--scenario", scenario,
           "--summary", str(summary_path)]
    if prior_summary is not None:
        cmd += ["--prior-summary", str(prior_summary)]
    proc = subprocess.run(cmd, cwd=str(scratch), capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise AssertionError(
            f"child phase={phase} scenario={scenario} exited rc={proc.returncode}\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}")
    print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, file=sys.stderr, end="")
    if guarded:
        assert "T31X_CHILD_POST_IDENTITY_OK" in proc.stdout, proc.stdout
    assert marker in proc.stdout, proc.stdout
    return json.loads(summary_path.read_text())


def _run_scenario(test_name: str, scenario: str) -> None:
    scratch = _owned_scratch(test_name)
    db_path = scratch / _DB_NAME
    assert not db_path.exists(), db_path

    summary_a = _run_child(phase="produce", marker=_PHASE_A_MARKER, scratch=scratch,
                           db_path=db_path, scenario=scenario, summary_name="phaseA.json")
    # Sequential by construction: phase B starts only after A exited 0.
    summary_b = _run_child(phase="reconcile-replay", marker=_PHASE_B_MARKER, scratch=scratch,
                           db_path=db_path, scenario=scenario, summary_name="phaseB.json",
                           prior_summary=scratch / "phaseA.json")
    assert type(summary_a["pid"]) is int and type(summary_b["pid"]) is int
    assert summary_a["pid"] != summary_b["pid"] and os.getpid() not in (summary_a["pid"], summary_b["pid"])

    expected_after_a = "RUNNING" if scenario == "running" else "QUEUED"
    identity_keys = ("operation_id", "job_id", "request_id", "request_hash", "idempotency_key")

    assert summary_a["operation"] == "experiment.stage_define"
    assert summary_a["operation_status_after"] == expected_after_a
    assert summary_a["job_status_after"] == expected_after_a
    assert summary_a["operation_result_null"] is True
    assert summary_a["scheduler_submits"] == 1
    assert summary_a["scheduler_executed"] == 0
    assert summary_a["counts"] == {"operations": 1, "jobs": 1, "artifacts": 0,
                                   "stage_attempts": 0}

    for key in identity_keys:
        assert summary_b[key] == summary_a[key], (key, summary_b[key], summary_a[key])
    assert summary_b["job_status_before_reconcile"] == expected_after_a
    # REAL reconcile_after_restart returned exactly the one owned job and
    # moved it (and its mirrored operation row) to RECONCILING.
    assert summary_b["reconcile_returned_job_ids"] == [summary_a["job_id"]]
    assert summary_b["job_status_after_reconcile"] == "RECONCILING"
    assert summary_b["operation_status_after_reconcile"] == "RECONCILING"

    replay = summary_b["replay"]
    for key in identity_keys:
        assert replay[key] == summary_a[key], (key, replay[key], summary_a[key])
    assert replay["replay_request_id_sent"] == "t31x-replay-req"
    assert replay["data_status"] == "RECONCILING"
    assert summary_b["scheduler_submits"] == 0, "same-key replay re-entered the scheduler path"
    assert summary_b["counts"] == {"operations": 1, "jobs": 1, "artifacts": 0,
                                   "stage_attempts": 0}

    # Record sidecar visibility without treating either presence or absence
    # as proof that a particular close or crash-recovery path succeeded.
    assert db_path.exists(), db_path
    sidecars = {name: (scratch / name).exists() for name in (_DB_NAME + "-wal", _DB_NAME + "-shm")}
    print("T31X_SIDECAR_OBSERVATION", json.dumps(sidecars, sort_keys=True))


def test_t31_stage_reconcile_reopen_after_queued_cross_process():
    """QUEUED case: process A leaves the stage_define job QUEUED (real route,
    fake scheduler captured once); after A closes and exits normally, process
    B reopens the same owned database, the REAL reconcile_after_restart moves
    the job/operation to RECONCILING, and the SAME key replayed through the
    REAL route is a read-only reuse of the original identity with zero
    scheduler admissions.  Synthetic data only; no daemon start, crash,
    scheduler, worker, authorization or UNKNOWN attestation."""
    _run_scenario("queued-across-process", "queued")


def test_t31_stage_reconcile_reopen_after_running_cross_process():
    """RUNNING case: same two-sequential-process shape, but phase A applies
    the REAL OperationStore.transition_status CAS QUEUED->RUNNING to simulate
    a claim and confirms both job and operation rows show RUNNING before the
    normal close.  Phase B reconciles RUNNING->RECONCILING and replays the
    same key with zero re-dispatch.  The simulated claim is NOT evidence of a
    real scheduler claim or Worker execution."""
    _run_scenario("running-across-process", "running")


if __name__ == "__main__":
    raise SystemExit(_child_main())
