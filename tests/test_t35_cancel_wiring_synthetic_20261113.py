"""T35 wiring — synthetic permission/write-boundary tests for real-daemon
``job.cancel`` routing on QUEUED and RUNNING jobs.

These tests encode the PROPOSED daemon-side authority wiring for the ordinary
(non-force-stop) cancel routes and are NOT claims of authentication, public
MCP behaviour, or native cancellation.  They exercise the REAL unbound
``ControlDaemon._dispatch_admitted`` end-to-end so that wiring cannot be
satisfied by reimplementing dispatch in the test.

Static basis (live ``comsol_mcp/_control_daemon.py`` at authoring time):
- ``_dispatch_admitted`` (1033-…) is the only entry point used.  Canonical
  ``job.cancel`` is a ``CACHED_JOB_ACTIONS`` member (97-100) and routes at
  1083-1084 into ``_dispatch_catalog_job_control`` (5444-5447) -> impl
  (12046-12145).  Single-layer ``registry_call``/``operation_call`` envelopes
  hit 1096-1099 -> ``_cached_registry_control`` (1423-1513), whose ``job.``
  branch (1497-1513) forwards the same inner call into the same catalog impl.
  Legacy ``job_cancel`` is in ``CONTROL_READS`` (88-96); it is NOT in
  ``_SCOPED_LEGACY_JOB_READS`` (102-105) and never carries ``force_stop: True``
  here, so it falls to the execution-blind ``self._control_read(operation,
  arguments)`` at 1093.  All three families then reach the ``job_cancel``
  branch of ``_control_read`` (13382-13389) -> ``_cancel_job`` (13412-13532)
  -> ``OperationStore.cancel_queued`` (2351-2486) for QUEUED, or the
  CancelRequested-intent branch (13501-13515) for RUNNING.
- Today NONE of these routes consults ``project_authority`` on cancel
  (``_authorize_cached_job_read`` (125-131) applies only to
  ``_SCOPED_CACHED_JOB_READS`` (12070-12073), which excludes ``job.cancel``),
  and ``_cancel_job`` calls ``cancel_queued`` without the strict
  project-binding kwargs.  ``OperationStore.cancel_queued``'s
  ``enforce_project_binding`` (dff4a3ee) is a durable-consistency primitive,
  not an authentication result, so the daemon gate is what these tests demand.
- Catalog metadata (comsol_mcp/data/g2/02_ACTION_CATALOG.json, 11282-11331):
  ``job.cancel`` effect HOST_CONTROL, scope project.  Following that effect
  split (and the existing ``job.cleanup`` precedent at 12125), the fake
  authority below models the gate as ``authorize_operation(project,
  'host_control')``; the exact wiring mechanism (dispatcher gate, re-routing,
  helper names) is deliberately NOT pinned — only the observable
  envelope-gated behaviour is.

Contract asserted per route (canonical / registry_call-nested /
operation_call-nested / legacy):
- QUEUED, same-project, authorized: real QUEUED_ABORT success must keep the
  real durable outcome (both jobs+operations rows CANCELLED, CANCELLED event,
  durable OPERATION_CANCELLED-shaped result) while a recorded
  ``authority`` call strictly precedes every recorded durable write.
- QUEUED, envelope project B that IS itself host_control-authorized, job
  bound to A: refusal with zero durable writes and byte-identical job/
  operation/events/result read-back.  A binding pre-lookup read of the job
  row is legitimate and NOT counted as a side effect — the write boundary is
  what is enforced (this is what distinguishes ordinary business reads from
  the missing permission check).
- QUEUED, envelope project lacking any grant (binding would match): refusal
  strictly BEFORE any content read or write touching the job — the durable
  store-binding primitive must not substitute for authorization.
- QUEUED, missing ``execution.project_id``: fail-closed refusal before any
  content read or write.
- RUNNING, same-project, authorized: the existing intent-recording behaviour
  (CancelRequested event + ``cancel_requested`` metadata, status stays
  RUNNING, durable operations result untouched, Worker untouched) must
  survive wiring, with the authority call preceding the intent writes.
- RUNNING, authorized foreign envelope project: refusal, zero durable writes,
  status/events/durable result byte-identical before/after.

Scope and honesty limits:
- All fixtures are fresh ``OperationStore(':memory:')`` instances with
  task-authored synthetic jobs in QUEUED or RUNNING only; no UNKNOWN or
  RECONCILING state, no force_stop is ever sent, no Worker, process, thread,
  service, SSH, endpoint, credential, model, network, historical job, prior
  test file, or historical resource is touched.  Every durable write is an
  ':memory:' row.  ``force_stop`` routing (``_dispatch_job_cancel`` /
  ``_scoped_force_stop``) is stubbed on the receiver with a raising sentinel
  so accidental entry fails loudly.
- The receiver is a ``ControlDaemon`` instance created via ``__new__`` WITHOUT
  ``__init__``: every method it can reach is the real class implementation
  (nothing is reimplemented), while uninitialized state (``self.home``,
  sessions, services, workers) raises loudly instead of executing real
  side effects.  ``_log_exception`` is replaced by a no-I/O sentinel so the
  generic unknown-state handler cannot mask a defect.
- Store spies are installed strictly AFTER fixture setup, are pure
  ``*args/**kwargs`` pass-throughs that never replace return values, and
  share one ordered timeline with the fake authority, so ordering is
  provable and fixture writes never pollute it.  Positive controls assert
  durable outcomes, not merely spy recordings.
- Refusals are checked as "not successful" without pinning any specific
  error code/message wording, and without requiring any not-yet-existing
  helper name.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Mapping

import pytest  # pyright: ignore[reportMissingImports]

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._operation_store import OperationStore

PROJECT_A = "t35cw-proj-A-20261113"
PROJECT_B = "t35cw-proj-B-20261113"

METHOD_KINDS = ("job", "events", "cancel_queued", "add_event", "update_job")
SQL_KINDS = ("sql_select", "sql_write")

ROUTE_SPECS = ("canonical", "registry_call", "operation_call", "legacy")


class _FakeProjectAuthority:
    """Explicit per-project grants; every authorize call shares one timeline.

    Models ONLY the dispatcher-level gate (host_control for the HOST_CONTROL
    cancel effect per the catalogue); it is not the production
    ``ProjectAuthority`` and a supplied envelope project ID is never an
    authenticated principal.
    """

    def __init__(self, permissions: Mapping[str, set[str]], timeline: list[tuple[Any, ...]]) -> None:
        self.permissions: dict[str, set[str]] = {
            project: set(actions) for project, actions in permissions.items()
        }
        self.timeline = timeline

    def authorize_operation(self, project_id: str, action: str) -> None:
        self.timeline.append(("authority", project_id, action))
        if action not in self.permissions.get(project_id, set()):
            raise ExecutionContractError(
                "T35CW_FAKE_AUTHORITY_DENIED", "synthetic t35 cancel-wiring fake authority denial"
            )


def _make_receiver(store: OperationStore, authority: _FakeProjectAuthority) -> ControlDaemon:
    """Inert receiver whose entire method surface is the REAL unbound daemon.

    ``ControlDaemon.__new__`` skips ``__init__`` (no home, service, worker,
    session, or file state exists), so anything outside the exercised cancel
    routes fails loudly.  The force-stop entry points are shadowed with
    raising sentinels because this file never sends ``force_stop``;
    ``_log_exception`` becomes a no-I/O sentinel so the generic
    EXECUTION_STATE_UNKNOWN handler cannot hide a defect behind a written
    ``control-errors.log``.
    """

    def _forbidden(name: str):
        def _raise(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError(f"T35CW receiver reached forbidden real path {name}")
        return _raise

    receiver = ControlDaemon.__new__(ControlDaemon)
    receiver.store = store
    receiver.project_authority = authority  # type: ignore
    receiver._log_exception = _forbidden("_log_exception")
    receiver._dispatch_job_cancel = _forbidden("_dispatch_job_cancel")
    receiver._scoped_force_stop = _forbidden("_scoped_force_stop")
    return receiver


def _install_store_spies(store: OperationStore, timeline: list[tuple[Any, ...]]) -> dict[str, Any]:
    """Pass-through recorders installed strictly after fixture setup.

    ``*args``/``**kwargs`` are forwarded verbatim, so a wired daemon may call
    ``cancel_queued`` with the existing strict-binding kwargs (or any future
    kwargs) unchanged; return values always come from the real bound methods.
    The originals are returned for out-of-timeline snapshots.
    """
    real = {
        "job": store.job,
        "events": store.events,
        "cancel_queued": store.cancel_queued,
        "add_event": store.add_event,
        "update_job": store.update_job,
    }

    def _make(kind: str):
        def _spy(*args: Any, **kwargs: Any) -> Any:
            job_id = args[0] if args else kwargs.get("job_id")
            timeline.append((kind, job_id))
            return real[kind](*args, **kwargs)
        return _spy

    for kind in real:
        setattr(store, kind, _make(kind))
    # Witness direct SQL as well as the named store methods: an integrated
    # atomic primitive may legitimately use a different public method name.
    def _trace(statement: str) -> None:
        words = statement.split(None, 1)
        if not words:
            return
        operation = words[0].upper()
        if operation in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
            timeline.append(("sql_write", None))
        elif operation == "SELECT":
            timeline.append(("sql_select", None))
    store.db.set_trace_callback(_trace)
    return real


def _seed_job(
    store: OperationStore,
    *,
    tag: str,
    bound_project: str | None,
    status: str,
    event_markers: tuple[str, ...] = (),
    result_marker: str | None = None,
) -> str:
    """Create one task-authored QUEUED (then optionally RUNNING) durable job.

    ``begin`` writes the SAME metadata to both the jobs and the operations
    row, so a bound project is unambiguous on both sides; ``RUNNING`` is
    reached through the ordinary ``update_job`` path.  A durable operations
    result is seeded through ``update_job(result=...)`` without leaving the
    current status.
    """
    assert status in {"QUEUED", "RUNNING"}
    metadata: dict[str, Any] = {
        "t35cw_synthetic": True,
        "content_marker": f"T35CW-SENT-CONTENT-{tag}",
    }
    if bound_project is not None:
        metadata["execution"] = {"project_id": bound_project}
    record, reused = store.begin(
        request_id=f"t35cw-req-{tag}",
        idempotency_key=f"t35cw-idem-{tag}",
        request_hash="1" * 64,
        operation="job.synthetic-producer",
        metadata=metadata,
    )
    assert reused is False
    job_id = record["job_id"]
    assert isinstance(job_id, str) and job_id
    if status == "RUNNING":
        store.update_job(job_id, "RUNNING")
    for marker in event_markers:
        store.add_event(job_id, "T35CWFixtureEvent", {"marker": marker})
    if result_marker is not None:
        store.update_job(
            job_id, status, {"t35cw_seed": True},
            result={"success": True, "data": {"marker": result_marker}},
        )
    settled = store.job(job_id)
    assert settled is not None and settled["status"] == status
    return job_id


def _snapshot(real_job: Any, real_events: Any, job_id: str) -> dict[str, Any]:
    """Durable read-back of both rows, metadata, result and full event log."""
    job = real_job(job_id)
    assert job is not None
    return {
        "job_status": job["status"],
        "job_metadata": job["metadata"],
        "finished_at": job["finished_at"],
        "operation_status": job["operation"]["status"],
        "operation_result": job["operation"]["result"],
        "events": real_events(job_id, limit=1000),
    }


def _build_request(
    route: str, arguments: Mapping[str, Any], execution: Mapping[str, Any]
) -> dict[str, Any]:
    """Real admitted-envelope shapes; identity stays in the outer envelope."""
    if route == "canonical":
        return {
            "operation": "job.cancel",
            "arguments": dict(arguments),
            "execution": dict(execution),
        }
    if route in {"registry_call", "operation_call"}:
        return {
            "operation": route,
            "arguments": {
                "operation_id": "job.cancel",
                "arguments": dict(arguments),
            },
            "execution": dict(execution),
        }
    if route == "legacy":
        return {
            "operation": "job_cancel",
            "arguments": dict(arguments),
            "execution": dict(execution),
        }
    raise AssertionError(f"unknown route {route!r}")


def _dispatch(receiver: ControlDaemon, route: str, arguments: Mapping[str, Any],
              execution: Mapping[str, Any]) -> dict[str, Any]:
    """The real outer route; nothing below it is bypassed by the test."""
    response = ControlDaemon._dispatch_admitted(receiver, _build_request(route, arguments, execution))
    assert isinstance(response, dict), f"route {route}: non-dict response {response!r}"
    return response


def _assert_refused(response: dict[str, Any], *, label: str, sentinels: tuple[str, ...]) -> None:
    """No pinned error wording: refusal means success is not True, and the
    payload discloses none of the fixture content sentinels."""
    assert response.get("success") is not True, f"{label}: expected a refusal, got {response!r}"
    blob = json.dumps(response, sort_keys=True, default=str)
    for sentinel in sentinels:
        assert sentinel not in blob, f"{label}: fixture sentinel leaked into refusal payload"


def _assert_no_job_touch(timeline: list[tuple[Any, ...]], job_id: str, label: str) -> None:
    touches = [entry for entry in timeline if entry[0] in SQL_KINDS
               or (entry[0] in METHOD_KINDS and entry[1] == job_id)]
    assert not touches, f"{label}: job content was read/written despite pre-content refusal: {touches!r}"


def _assert_gate_precedes_writes(timeline: list[tuple[Any, ...]], label: str) -> None:
    authority_at = [i for i, entry in enumerate(timeline) if entry[0] == "authority"]
    write_at = [i for i, entry in enumerate(timeline) if entry[0] == "sql_write"]
    assert authority_at, f"{label}: expected a recorded authority call before any durable write"
    assert write_at, f"{label}: expected the durable control write to have happened"
    assert min(authority_at) < min(write_at), (
        f"{label}: a durable write preceded the authority call: {timeline!r}"
    )


def _fixture(
    *,
    store: OperationStore,
    tag: str,
    bound_project: str | None,
    status: str,
    permissions: Mapping[str, set[str]],
    event_markers: tuple[str, ...] = (),
    result_marker: str | None = None,
):
    fixture_events = tuple(f"T35CW-SENT-EVT-{tag}-{marker}" for marker in event_markers)
    job_id = _seed_job(
        store, tag=tag, bound_project=bound_project, status=status,
        event_markers=fixture_events, result_marker=result_marker,
    )
    before = _snapshot(store.job, store.events, job_id)
    timeline: list[tuple[Any, ...]] = []
    authority = _FakeProjectAuthority(permissions, timeline)
    receiver = _make_receiver(store, authority)
    real = _install_store_spies(store, timeline)
    assert timeline == [], "fixture writes leaked into the shared timeline"
    assert authority.timeline is timeline, "authority and store spies must share one timeline"
    sentinels = (
        f"T35CW-SENT-CONTENT-{tag}",
        *fixture_events,
    ) + ((f"T35CW-SENT-RESULT-{tag}",) if result_marker is not None else ())
    for sentinel in sentinels:
        assert sentinel in json.dumps(before), f"fixture sentinel missing: {sentinel}"
    return store, authority, receiver, real, timeline, job_id, before, sentinels


def _cancel_arguments(job_id: str) -> dict[str, Any]:
    """Ordinary cancel only — ``force_stop`` is never present in this file."""
    return {"job_id": job_id}


# ---------------------------------------------------------------------------
# QUEUED positive: authorized same-project cancel on every route.
# RED on the current daemon only via the gate-ordering assertion (today no
# authority call is recorded at all); the durable QUEUED_ABORT outcome itself
# must keep the real cancel_queued semantics.
# ---------------------------------------------------------------------------

def _check_queued_same_project_authorized_cancel_succeeds_with_gate_before_write(route: str, store: OperationStore) -> None:
    store, _authority, receiver, real, timeline, job_id, _before, _sentinels = _fixture(
        store=store, tag=f"QPOS-{route}", bound_project=PROJECT_A, status="QUEUED",
        permissions={PROJECT_A: {"host_control"}},
    )
    response = _dispatch(receiver, route, _cancel_arguments(job_id), {"project_id": PROJECT_A})
    assert response.get("success") is True, f"{route}: positive QUEUED cancel failed: {response!r}"
    data = response.get("data") or {}
    assert data.get("job_id") == job_id
    assert data.get("status") == "CANCELLED"
    assert data.get("cancel_accepted") is True
    _assert_gate_precedes_writes(timeline, f"{route} queued positive")
    after = _snapshot(real["job"], real["events"], job_id)
    assert after["job_status"] == "CANCELLED"
    assert after["operation_status"] == "CANCELLED"
    assert after["operation_result"] is not None
    assert after["operation_result"].get("success") is not True
    assert any(event["event"] == "CANCELLED" for event in after["events"]), (
        f"{route}: missing durable CANCELLED event"
    )


# ---------------------------------------------------------------------------
# QUEUED negative: an itself-authorized FOREIGN envelope project must not
# change an A-bound job.  A legitimate binding pre-lookup read is not a side
# effect; the boundary is durable writes plus unchanged durable state.
# ---------------------------------------------------------------------------

def _check_queued_authorized_foreign_project_refused_state_preserved(route: str, store: OperationStore) -> None:
    tag = f"QXPROJ-{route}"
    store, _authority, receiver, real, timeline, job_id, before, sentinels = _fixture(
        store=store, tag=tag, bound_project=PROJECT_A, status="QUEUED",
        permissions={PROJECT_A: {"host_control"}, PROJECT_B: {"host_control"}},
        event_markers=("e1",),
        result_marker=f"T35CW-SENT-RESULT-{tag}",
    )
    response = _dispatch(receiver, route, _cancel_arguments(job_id), {"project_id": PROJECT_B})
    _assert_refused(response, label=f"{route} queued foreign-project", sentinels=sentinels)
    assert ("authority", PROJECT_B, "host_control") in timeline
    after = _snapshot(real["job"], real["events"], job_id)
    assert after == before, f"{route}: durable job/operation/events/result changed despite refusal"


# ---------------------------------------------------------------------------
# QUEUED negative: envelope project lacking any grant (durable binding would
# match) must be refused BEFORE any content read or write — the store's
# project-binding primitive is consistency, not authorization.
# ---------------------------------------------------------------------------

def _check_queued_unauthorized_envelope_refused_before_content_access(route: str, store: OperationStore) -> None:
    store, _authority, receiver, real, timeline, job_id, before, sentinels = _fixture(
        store=store, tag=f"QNOAUTH-{route}", bound_project=PROJECT_A, status="QUEUED",
        permissions={},
    )
    response = _dispatch(receiver, route, _cancel_arguments(job_id), {"project_id": PROJECT_A})
    _assert_refused(response, label=f"{route} queued unauthorized", sentinels=sentinels)
    assert ("authority", PROJECT_A, "host_control") in timeline
    _assert_no_job_touch(timeline, job_id, f"{route} queued unauthorized")
    after = _snapshot(real["job"], real["events"], job_id)
    assert after == before, f"{route}: durable state changed despite unauthorized pre-content refusal"


# ---------------------------------------------------------------------------
# QUEUED negative: missing outer execution.project_id fails closed before any
# content read or write on every route.
# ---------------------------------------------------------------------------

def _check_queued_missing_envelope_project_refused_before_content_access(route: str, store: OperationStore) -> None:
    store, _authority, receiver, real, timeline, job_id, before, sentinels = _fixture(
        store=store, tag=f"QNOENV-{route}", bound_project=PROJECT_A, status="QUEUED",
        permissions={PROJECT_A: {"host_control"}},
    )
    response = _dispatch(receiver, route, _cancel_arguments(job_id), {})
    _assert_refused(response, label=f"{route} queued missing-envelope", sentinels=sentinels)
    _assert_no_job_touch(timeline, job_id, f"{route} queued missing-envelope")
    after = _snapshot(real["job"], real["events"], job_id)
    assert after == before, f"{route}: durable state changed despite missing-envelope refusal"


# ---------------------------------------------------------------------------
# RUNNING positive: authorized same-project ordinary cancel keeps the real
# intent-recording behaviour (no Worker, no status change, durable result
# untouched) while the authority call precedes the intent writes.
# ---------------------------------------------------------------------------

def _check_running_same_project_authorized_cancel_records_intent(route: str, store: OperationStore) -> None:
    tag = f"RPOS-{route}"
    store, _authority, receiver, real, timeline, job_id, before, _sentinels = _fixture(
        store=store, tag=tag, bound_project=PROJECT_A, status="RUNNING",
        permissions={PROJECT_A: {"host_control"}},
        event_markers=("e1",),
        result_marker=f"T35CW-SENT-RESULT-{tag}",
    )
    response = _dispatch(receiver, route, _cancel_arguments(job_id), {"project_id": PROJECT_A})
    assert response.get("success") is True, f"{route}: RUNNING intent cancel failed: {response!r}"
    _assert_gate_precedes_writes(timeline, f"{route} running intent")
    after = _snapshot(real["job"], real["events"], job_id)
    assert after["job_status"] == "RUNNING", f"{route}: intent cancel must not change RUNNING status"
    assert after["job_metadata"].get("cancel_requested") is True
    assert any(event["event"] == "CancelRequested" for event in after["events"]), (
        f"{route}: missing CancelRequested intent event"
    )
    before_ids = [event["id"] for event in before["events"]]
    after_ids = [event["id"] for event in after["events"]]
    assert after_ids[: len(before_ids)] == before_ids, f"{route}: fixture events were not preserved"
    assert after["operation_result"] == before["operation_result"], (
        f"{route}: intent cancel must leave the durable operations result untouched"
    )


# ---------------------------------------------------------------------------
# RUNNING negative: an itself-authorized foreign envelope project must be
# refused with status/events/durable result byte-identical (memory only, no
# Worker, force-stop unreachable).
# ---------------------------------------------------------------------------

def _check_running_authorized_foreign_project_refused_state_preserved(route: str, store: OperationStore) -> None:
    tag = f"RXPROJ-{route}"
    store, _authority, receiver, real, timeline, job_id, before, sentinels = _fixture(
        store=store, tag=tag, bound_project=PROJECT_A, status="RUNNING",
        permissions={PROJECT_A: {"host_control"}, PROJECT_B: {"host_control"}},
        event_markers=("e1", "e2"),
        result_marker=f"T35CW-SENT-RESULT-{tag}",
    )
    response = _dispatch(receiver, route, _cancel_arguments(job_id), {"project_id": PROJECT_B})
    _assert_refused(response, label=f"{route} running foreign-project", sentinels=sentinels)
    assert ("authority", PROJECT_B, "host_control") in timeline
    after = _snapshot(real["job"], real["events"], job_id)
    assert after == before, (
        f"{route}: RUNNING status/events/result changed despite foreign-project refusal"
    )
    assert not any(event["event"] == "CancelRequested" for event in after["events"])


# ---------------------------------------------------------------------------
# Public test entry points: each route is an independently collected case,
# with its own ':memory:' store, even when another route fails.
# ---------------------------------------------------------------------------

@pytest.fixture
def owned_store():
    store = OperationStore(":memory:")
    try:
        yield store
    finally:
        store.db.set_trace_callback(None)
        store.close()


@pytest.mark.parametrize("route", ROUTE_SPECS)
def test_queued_same_project_authorized_cancel_succeeds_with_gate_before_write(route, owned_store) -> None:
    _check_queued_same_project_authorized_cancel_succeeds_with_gate_before_write(route, owned_store)


@pytest.mark.parametrize("route", ROUTE_SPECS)
def test_queued_authorized_foreign_project_refused_state_preserved(route, owned_store) -> None:
    _check_queued_authorized_foreign_project_refused_state_preserved(route, owned_store)


@pytest.mark.parametrize("route", ROUTE_SPECS)
def test_queued_unauthorized_envelope_refused_before_content_access(route, owned_store) -> None:
    _check_queued_unauthorized_envelope_refused_before_content_access(route, owned_store)


@pytest.mark.parametrize("route", ROUTE_SPECS)
def test_queued_missing_envelope_project_refused_before_content_access(route, owned_store) -> None:
    _check_queued_missing_envelope_project_refused_before_content_access(route, owned_store)


@pytest.mark.parametrize("route", ROUTE_SPECS)
def test_running_same_project_authorized_cancel_records_intent(route, owned_store) -> None:
    _check_running_same_project_authorized_cancel_records_intent(route, owned_store)


@pytest.mark.parametrize("route", ROUTE_SPECS)
def test_running_authorized_foreign_project_refused_state_preserved(route, owned_store) -> None:
    _check_running_authorized_foreign_project_refused_state_preserved(route, owned_store)
