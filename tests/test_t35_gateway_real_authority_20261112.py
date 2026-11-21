"""T35 — GatewayRegistry routed tools vs the REAL ProjectAuthority (synthetic, read-only).

This file is UNRUN as of creation (authored without executing pytest, by
explicit delegation). Nothing here is a claim of runtime behaviour until a
pinned runner executes it against a frozen source snapshot.

What is real (no blanket mocking):
- ``comsol_mcp._mcp_gateway.GatewayRegistry`` is the actual class: the real
  profile gate (``current_tool_profile``/``is_tool_published``), the real
  ``add_tool`` signature rewriting, and the real ``routed`` coroutine
  (validation, ``original.bind``/``apply_defaults``, dispatcher call,
  ``mcp_result`` envelope) are exercised unchanged. The only replaced piece
  is the injected target object's ``add_tool`` (a capturing stub) and the
  injected dispatcher.
- The dispatcher hands the request to the REAL unbound
  ``ControlDaemon._dispatch_admitted`` (frozen parent snapshot of
  comsol_mcp/_control_daemon.py, 1044) against an inert receiver that binds
  the real unbound helpers ``_timeouts``, ``_control_read`` (13303),
  ``_error`` and ``_exception``. The legacy underscore names
  ``job_log``/``job_list`` are ``CONTROL_READS`` members routed at 1116-1118
  through ``_SCOPED_LEGACY_JOB_READS`` (102-105) and the module-level
  ``_authorize_cached_job_read`` (136-142), which calls the REAL
  ``ProjectAuthority.authorize_operation`` before the job-log snapshot or
  content methods. The authority itself reads a synthetic project record.
- The bounded ``job.log`` branch of ``_control_read`` (frozen snapshot
  13438-13493) opens ``OperationStore.job_log_read_snapshot`` (frozen store
  2221-2329: one read transaction, metadata-only binding probe under the
  ``max_binding_bytes`` admission cap, ``operations.result`` and job event
  content never touched) and only when that snapshot yields ``True`` calls
  ``OperationStore.job_log_page_bounded`` (frozen store 3857+: byte-budgeted
  event page inside the same store transaction). The unbounded
  ``job()``/``events()`` pair is NOT on this route, so the spies watch four
  read entry points: the bounded snapshot and page prove the live wiring
  was actually entered, while the legacy ``job``/``events`` spies guard
  against any silent fallback to the old unbounded path.
- The authority decisions run the actual production methods
  ``ProjectAuthority._record``, ``get_project``, ``_permissions`` and
  ``authorize_operation`` — never reimplemented or faked here.
- All durable reads/writes go through the real ``OperationStore`` paths
  (``begin``/``add_event``/``update_job``/``job``/``events``/``list_jobs``/
  ``job_log_read_snapshot``/``job_log_page_bounded``) on a fresh
  ``OperationStore(':memory:')``.

HONESTY LIMITS — deliberately NOT verified by these tests:
- The real ``ProjectAuthority.__init__`` is BYPASSED via ``__new__``. The
  workspace-root resolution (``Path.resolve(strict=True)``/``is_dir``), the
  pinned ``open_project_root_binding`` root fd binding, ``_resolve_workspace``
  and ``_create_workspace`` are NEVER executed here: real workspace, fd,
  root-escape and symlink behaviour remains completely untested. The
  instance-level ``_verify_workspace_binding`` is replaced by a STRICT
  NO-I/O spy that only validates the synthetic workspace string.
- Synthetic project rows are inserted directly into the ``:memory:``
  ``projects`` table (schema per _operation_store.py 311-314 and the
  ``_create`` record shape per _project_authority.py 385-396); the real
  ``project.create`` path and any real directory creation/cleanup are never
  touched (WinRM-approval constraint: no real dirs, no real projects).
- ``execution.project_id`` is ONLY a caller-selected selector. There is no
  authenticated per-principal identity, no public MCP wire/daemon socket,
  and no real deployment path claim anywhere in this file.
- Only synthetic read-only job reads run. No cancel, no reconcile, no
  Worker, no COMSOL, no network, no real database, no UNKNOWN state.
- The generic ``_log_exception`` tail (which would write
  ``control-errors.log``) is replaced by a failure-only no-I/O sentinel; it
  must never be reached, so a generic-exception defect fails loudly instead
  of hiding behind an EXECUTION_STATE_UNKNOWN response.

Run prerequisites (for the future pinned runner): the ``mcp`` package must
be importable (``_mcp_gateway`` imports ``mcp.types``); ``pytest``;
``COMSOL_MCP_TOOL_PROFILE`` unset or ``full`` — asserted inside each harness
via the real ``GatewayRegistry.profile``. ``asyncio.run`` owns the
``asyncio.to_thread`` default executor and shuts it down on exit (Runner
closes the loop and its default executor), so no executor leaks.

Design note (not a claim): this adapts the inert-receiver and synthetic-job
fixture SHAPE from the already-approved
``tests/test_t35_job_read_project_scope_20261112.py`` but keeps this file
self-contained (its own ``_InertDispatchReceiver``/``_create_synthetic_job``
with a REAL authority) so import identity and fixtures stay runner-pinnable
without cross-test imports. Legacy dotted-name canonical routes
(``job.log``/``job.list``) are intentionally not registered here because the
real ``_tools_control`` signatures are the legacy underscore ones; every
call still crosses the same outer ``_dispatch_admitted`` authority gate.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from comsol_mcp import _tools_control
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._mcp_gateway import GatewayRegistry
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._project_authority import PROJECT_SCHEMA_VERSION, ProjectAuthority

PROJECT_A = "t35gw-proj-A-20261112"
PROJECT_B = "t35gw-proj-B-20261112"
PROJECT_C_NO_INSPECT = "t35gw-proj-Cnoinspect-20261112"
PROJECT_MISSING = "t35gw-proj-missing-20261112"

# A purely literal synthetic workspace name. It is never created, resolved,
# opened, or stat'ed anywhere in this file — see the module docstring.
SYNTHETIC_WORKSPACE_PREFIX = "/t35gw-synthetic-workspace-unverified/"

# Timeline markers split by what a durable read actually touches:
# - BINDING_READ_KINDS: the bounded job.log binding probe runs metadata-only
#   binding SQL (job_log_read_snapshot) — never job/result/event content.
#   A refusal that reaches _control_read may legitimately perform this
#   probe and then deny.
# - CONTENT_READ_KINDS: job rows, full event lists, bounded event pages and
#   project-bound job lists. No refusal may trigger any of these.
BINDING_READ_KINDS = frozenset({"joblog_snapshot"})
CONTENT_READ_KINDS = frozenset({"job_row", "events", "joblog_page", "list_jobs"})
READ_KINDS = BINDING_READ_KINDS | CONTENT_READ_KINDS


class _InertDispatchReceiver:
    """Inert receiver delegating to the REAL unbound daemon helpers.

    Same provenance idea as the approved t35jr receiver, but it carries the
    REAL ``ProjectAuthority`` (not a fake). Only ``.store`` /
    ``.project_authority`` plus ``_timeouts``, ``_control_read``, ``_error``
    and ``_exception`` are touched by the exercised legacy-read routes
    (1116-1118 -> 13303+; the bounded job.log branch is 13438-13493 and
    reaches the store only through ``job_log_read_snapshot`` /
    ``job_log_page_bounded``); ``_log_exception`` is a failure-only sentinel
    because the real logger would write ``control-errors.log``.
    """

    def __init__(self, store: OperationStore, project_authority: ProjectAuthority) -> None:
        self.store = store
        self.project_authority = project_authority

    @staticmethod
    def _timeouts(execution: dict[str, Any]) -> dict[str, Any]:
        return ControlDaemon._timeouts(execution)

    def _control_read(self, operation: str, arguments: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        return ControlDaemon._control_read(self, operation, arguments, **kwargs)  # type: ignore[arg-type]

    @staticmethod
    def _error(code: str, message: str, **details: Any) -> dict[str, Any]:
        return ControlDaemon._error(code, message, **details)

    @staticmethod
    def _exception(exc: BaseException) -> dict[str, Any]:
        return ControlDaemon._exception(exc)

    @staticmethod
    def _log_exception() -> None:
        raise AssertionError(
            "T35GW inert receiver reached the generic _dispatch_admitted exception path"
        )


def _make_synthetic_backed_authority(
    store: OperationStore,
    timeline: list[tuple[str, ...]],
    host_state: dict[str, set[str]],
    *,
    grant_ceiling: frozenset[str] = frozenset({"inspect", "project_write"}),
) -> ProjectAuthority:
    """REAL ProjectAuthority methods over a __new__-bypassed instance.

    CONSTRUCTOR BYPASSED: no workspace-root resolution, no root fd binding,
    no is_dir check happens (all UNVERIFIED here). Only the attributes the
    exercised read-authority path touches are set explicitly:
    ``.store``, ``.permission_provider``, ``.grant_ceiling`` — plus an
    instance-level replacement of ``_verify_workspace_binding`` that is a
    strict string-only spy with ZERO filesystem I/O: it must still reject
    malformed/unexpected workspace strings so ``_record``'s call site stays
    on the real code path, but it proves nothing about real bindings.
    """
    authority = ProjectAuthority.__new__(ProjectAuthority)
    authority.store = store
    authority.grant_ceiling = frozenset(grant_ceiling)

    def permission_provider() -> set[str]:
        # Host-injected provider (trusted injection by design); recording
        # it gives the ordered authority marker without touching any
        # production method body.
        timeline.append(("authority", "host_permissions"))
        return set(host_state["granted"])

    authority.permission_provider = permission_provider

    def _verify_workspace_binding_spy(raw_path: Any) -> None:
        timeline.append(("authority", "workspace_string_only_check"))
        if (not isinstance(raw_path, str) or not raw_path
                or not raw_path.startswith(SYNTHETIC_WORKSPACE_PREFIX)
                or ".." in raw_path or any(ord(char) < 32 for char in raw_path)):
            raise ExecutionContractError(
                "PROJECT_WORKSPACE_IDENTITY_UNKNOWN",
                "synthetic workspace string failed the no-I/O spy",
            )

    authority._verify_workspace_binding = _verify_workspace_binding_spy  # type: ignore[method-assign]
    return authority


def _insert_synthetic_project(store: OperationStore, project_id: str, permissions: list[str]) -> None:
    """Insert one pure-synthetic ``projects`` row matching ``_record``'s schema.

    Row columns per _operation_store.py 311-314; ``record_json`` self-consistency
    per ``_project_authority._record`` 246-249 (schema_version/project_id/
    workspace/revision must match the row). The real ``project.create`` path
    (mkdir, collision transaction) is NEVER executed.
    """
    now = "2026-11-12T00:00:00.000Z"
    workspace = f"{SYNTHETIC_WORKSPACE_PREFIX}{project_id}"
    record = {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "project_id": project_id,
        "label": "synthetic t35 gateway project",
        "workspace": workspace,
        "revision": 1,
        "contract": {},
        "policy": {"permissions": sorted(permissions), "timeouts": {}},
        "policy_change_log": [],
        "created_at": now,
        "updated_at": now,
    }
    with store.lock:
        store.db.execute(
            "INSERT INTO projects(project_id,workspace,schema_version,revision,record_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (project_id, workspace, PROJECT_SCHEMA_VERSION, 1, json.dumps(record, sort_keys=True), now, now),
        )


def _create_synthetic_job(
    store: OperationStore,
    *,
    project_id: str,
    tag: str,
    event_marker: str,
    result_marker: str,
) -> str:
    """One durable job bound to ``project_id`` via the real store write paths.

    ``begin`` writes the same metadata to BOTH the operations and jobs rows,
    so ``consistent_project_id`` sees an unambiguous binding on each side.
    ``update_job`` merges (never erases) job metadata (_operation_store 2038).
    """
    metadata: dict[str, Any] = {"t35_synthetic": True, "execution": {"project_id": project_id}}
    record, reused = store.begin(
        request_id=f"t35gw-req-{tag}",
        idempotency_key=f"t35gw-idem-{tag}",
        request_hash="1" * 64,
        operation="job.synthetic-producer",
        metadata=metadata,
    )
    assert reused is False
    job_id = record["job_id"]
    assert isinstance(job_id, str) and job_id
    store.add_event(job_id, "T35GatewayReadEvent", {"marker": event_marker})
    store.update_job(
        job_id, "SUCCEEDED", {"t35_synthetic": True},
        result={"success": True, "data": {"marker": result_marker}},
    )
    return job_id


def _install_read_spies(store: OperationStore, timeline: list[tuple[str, ...]]) -> None:
    """Pass-through recorders on the shared ordered timeline only.

    Return values always come from the real methods; installed after fixture
    setup so setup writes never enter the timeline. Five read entry points
    are watched: the bounded job.log snapshot and the bounded page prove the
    live wiring was genuinely entered (``joblog_snapshot`` at the real
    snapshot context's yield, ``joblog_page`` at the real page call), the legacy
    ``job``/``events`` spies guard against a silent fallback to the old
    unbounded path, and ``list_jobs`` covers the job_list route.
    """
    real_job = store.job
    real_events = store.events
    real_list_jobs = store.list_jobs
    real_job_log_read_snapshot = store.job_log_read_snapshot
    real_job_log_page_bounded = store.job_log_page_bounded

    def spy_job(job_id: str) -> Any:
        timeline.append(("job_row", job_id))
        return real_job(job_id)

    def spy_events(job_id: str, offset: int = 0, limit: int = 100) -> Any:
        timeline.append(("events", job_id))
        return real_events(job_id, offset=offset, limit=limit)

    def spy_list_jobs(*args: Any, **kwargs: Any) -> Any:
        timeline.append(("list_jobs", str(kwargs.get("project_id")), str(kwargs.get("enforce_project_binding"))))
        return real_list_jobs(*args, **kwargs)

    @contextmanager
    def spy_job_log_read_snapshot(job_id: str, project_id: Any, *,
                                  max_binding_bytes: int) -> Any:
        # Record the real context's yield, not merely its construction.
        with real_job_log_read_snapshot(
            job_id, project_id, max_binding_bytes=max_binding_bytes,
        ) as bound:
            assert store.db.in_transaction and type(bound) is bool
            timeline.append(("joblog_snapshot", job_id, str(project_id), str(bound)))
            yield bound

    def spy_job_log_page_bounded(job_id: str, offset: int, limit: int, *,
                                 max_response_bytes: int) -> Any:
        # Recorded at the real call: the byte-budgeted event page was
        # actually fetched inside the open snapshot transaction. The
        # window is str-encoded like the list_jobs marker so every
        # timeline entry stays a tuple[str, ...].
        assert store.db.in_transaction
        timeline.append(("joblog_page", job_id, str(offset), str(limit)))
        return real_job_log_page_bounded(
            job_id, offset, limit, max_response_bytes=max_response_bytes,
        )

    store.job = spy_job  # type: ignore[method-assign]
    store.events = spy_events  # type: ignore[method-assign]
    store.list_jobs = spy_list_jobs  # type: ignore[method-assign]
    store.job_log_read_snapshot = spy_job_log_read_snapshot  # type: ignore[method-assign]
    store.job_log_page_bounded = spy_job_log_page_bounded  # type: ignore[method-assign]


class _CapturingMCP:
    """Minimal capture stub for the ``mcp`` object GatewayRegistry registers into."""

    def __init__(self) -> None:
        self.registered: dict[str, tuple[Any, dict[str, Any]]] = {}

    def add_tool(self, function: Any, **options: Any) -> None:
        self.registered[function.__name__] = (function, dict(options))


class _Harness:
    def __init__(self, store: OperationStore, timeline: list[tuple[str, ...]],
                 host_state: dict[str, set[str]], routed: dict[str, Any]) -> None:
        self.store = store
        self.timeline = timeline
        self.host_state = host_state
        self.routed = routed

    def call(self, tool: str, **kwargs: Any) -> Any:
        """Drive the REAL routed coroutine.

        ``asyncio.run`` owns the default executor used by the real
        ``asyncio.to_thread(self.dispatcher, ...)`` and shuts it down when
        the run completes — no executor leaks past this helper.
        """
        return asyncio.run(self.routed[tool](**kwargs))

    def close(self) -> None:
        self.store.close()


def _build_harness(
    *,
    project_permissions: Mapping[str, list[str]],
    initial_host_permissions: set[str],
) -> _Harness:
    store = OperationStore(":memory:")
    try:
        timeline: list[tuple[str, ...]] = []
        host_state: dict[str, set[str]] = {"granted": set(initial_host_permissions)}
        authority = _make_synthetic_backed_authority(store, timeline, host_state)
        for project_id, permissions in project_permissions.items():
            _insert_synthetic_project(store, project_id, permissions)
        receiver = _InertDispatchReceiver(store, authority)

        def dispatcher(operation: str, arguments: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
            return ControlDaemon._dispatch_admitted(
                receiver,  # type: ignore[arg-type]
                {"operation": operation, "arguments": dict(arguments), "execution": dict(execution)},
            )

        mcp_stub = _CapturingMCP()
        registry = GatewayRegistry(mcp=mcp_stub, dispatcher=dispatcher)
        # Pin the run prerequisite: registration publication below assumes
        # the real profile gate is the full catalog profile.
        assert registry.profile == "full", (
            f"COMSOL_MCP_TOOL_PROFILE must be unset/full for this harness, got {registry.profile!r}"
        )

        # Register the REAL _tools_control functions with their real
        # signatures (legacy public names; same names as operation ids).
        registry.add_tool(_tools_control.job_log)
        registry.add_tool(_tools_control.job_list)

        assert set(mcp_stub.registered) == {"job_log", "job_list"}
        routed: dict[str, Any] = {}
        for name, legacy_function in (("job_log", _tools_control.job_log), ("job_list", _tools_control.job_list)):
            captured, options = mcp_stub.registered[name]
            assert options == {}, f"{name}: unexpected registration options {options!r}"
            assert captured.__name__ == name
            original_names = list(inspect.signature(legacy_function).parameters)
            routed_names = list(inspect.signature(captured).parameters)
            assert routed_names == original_names + ["execution"], (
                f"{name}: routed signature {routed_names} must be the legacy params plus keyword-only execution"
            )
            execution_param = inspect.signature(captured).parameters["execution"]
            assert execution_param.kind is inspect.Parameter.KEYWORD_ONLY
            assert execution_param.default is None
            routed[name] = captured
        _install_read_spies(store, timeline)
        return _Harness(store, timeline, host_state, routed)
    except BaseException:
        store.close()
        raise


def _payload(result: Any) -> dict[str, Any]:
    payload = result.structuredContent
    assert isinstance(payload, dict), f"expected dict structuredContent, got {payload!r}"
    return payload


def _blob(result: Any) -> str:
    parts: list[str] = []
    for part in (result.content or []):
        text = getattr(part, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return json.dumps({"structuredContent": result.structuredContent, "content": parts},
                      sort_keys=True, default=str)


def _assert_refusal(result: Any, *, code: str, message: str, sentinels: tuple[str, ...], label: str) -> dict[str, Any]:
    """Exact production refusal shape through mcp_result + ControlDaemon._exception/_error.

    ``_exception`` (frozen snapshot 13956-13960) yields {"success": False,
    "data": {}, "error": as_dict()} and ``_error`` (13952-13954) yields the
    same keys with safe_retry False; neither sets stage/details here, so the
    error dict keys must be exactly code/message/safe_retry.
    """
    payload = _payload(result)
    assert payload.get("success") is False, f"{label}: expected refusal, got {payload!r}"
    assert set(payload) == {"success", "data", "error"}, f"{label}: unexpected payload keys {sorted(payload)}"
    assert payload["data"] == {}, f"{label}: expected empty data, got {payload['data']!r}"
    error = payload["error"]
    assert set(error) == {"code", "message", "safe_retry"}, f"{label}: unexpected error keys {sorted(error)}"
    assert error["code"] == code, f"{label}: expected error code {code}, got {error!r}"
    assert error["message"] == message, f"{label}: expected exact message, got {error!r}"
    assert error["safe_retry"] is False, f"{label}: expected safe_retry False"
    assert result.isError is True, f"{label}: mcp_result must mark refusals isError"
    blob = _blob(result)
    for sentinel in sentinels:
        assert sentinel not in blob, f"{label}: sentinel leaked into refusal payload"
    return payload


def _assert_no_content_reads(timeline: list[tuple[str, ...]], label: str) -> None:
    """No job/result/event-content durable read survived the denial.

    This bans job rows, full event lists, bounded event pages and bound job
    lists. The metadata-only binding probe (``joblog_snapshot``) is a
    separate kind judged per test: it must be absent when the outer
    authority gate refused before ``_control_read``, and may be present
    when the denial itself came from the binding probe.
    """
    offending = [entry for entry in timeline if entry[0] in CONTENT_READ_KINDS]
    assert not offending, (
        f"{label}: a content read happened despite the denial: {timeline!r}"
    )


def _assert_authority_precedes_reads(timeline: list[tuple[str, ...]], label: str) -> None:
    authority_at = [i for i, entry in enumerate(timeline) if entry[0] == "authority"]
    read_at = [i for i, entry in enumerate(timeline) if entry[0] in READ_KINDS]
    assert authority_at, f"{label}: expected a recorded authority-phase marker"
    assert read_at, f"{label}: expected a recorded durable read"
    assert min(authority_at) < min(read_at), (
        f"{label}: a durable read preceded the authority phase: {timeline!r}"
    )


# ---------------------------------------------------------------------------
# A/A: same synthetic project, host grant + project policy both contain
# inspect -> the real chain authorizes and the synthetic sentinel is served.
# ---------------------------------------------------------------------------

def test_t35gw_a1_job_log_same_project_authorized_success_with_synthetic_sentinel() -> None:
    event_marker = "T35GW-EVT-SAMEPROJ-9c31"
    result_marker = "T35GW-RES-SAMEPROJ-4d7e"
    harness = _build_harness(
        project_permissions={PROJECT_A: ["inspect", "project_write"]},
        initial_host_permissions={"inspect", "project_write"},
    )
    try:
        job_id = _create_synthetic_job(
            harness.store, project_id=PROJECT_A, tag="same-proj",
            event_marker=event_marker, result_marker=result_marker,
        )
        result = harness.call("job_log", job_id=job_id, execution={"project_id": PROJECT_A})
        payload = _payload(result)
        assert payload.get("success") is True, f"same-project read should succeed: {payload!r}"
        assert set(payload) == {"success", "data"}, f"unexpected envelope keys {sorted(payload)}"
        data = payload["data"]
        assert set(data) == {"job_id", "events", "next_offset"}, f"unexpected job_log data keys {sorted(data)}"
        assert data["job_id"] == job_id
        assert data["next_offset"] == 1
        assert [event["metadata"]["marker"] for event in data["events"]] == [event_marker]
        assert result.isError is False
        assert event_marker in _blob(result)
        # Ordered authority -> binding -> page proof on the REAL methods:
        # _record's workspace verification, then _permissions' host
        # provider, then the bounded binding snapshot, strictly before the
        # bounded page actually fetches the sentinel event. The legacy
        # job()/events() pair must stay untouched — no fallback to the old
        # unbounded path may serve this read.
        timeline = harness.timeline
        verify_at = [i for i, entry in enumerate(timeline) if entry == ("authority", "workspace_string_only_check")]
        provider_at = [i for i, entry in enumerate(timeline) if entry == ("authority", "host_permissions")]
        snapshot_at = [i for i, entry in enumerate(timeline) if entry[0] == "joblog_snapshot"]
        page_at = [i for i, entry in enumerate(timeline) if entry[0] == "joblog_page"]
        assert verify_at and provider_at and snapshot_at and page_at, (
            f"authority/binding-snapshot/bounded-page markers missing: {timeline!r}"
        )
        assert verify_at[0] < provider_at[0] < snapshot_at[0] < page_at[0], (
            f"authority -> binding -> page order violated: {timeline!r}"
        )
        assert ("joblog_snapshot", job_id, PROJECT_A, "True") in timeline, (
            f"the bounded snapshot must bind against the authorized outer project: {timeline!r}"
        )
        assert page_at == [timeline.index(("joblog_page", job_id, "0", "100"))], (
            f"exactly one bounded page read with the default window is expected: {timeline!r}"
        )
        assert not any(entry[0] in ("job_row", "events") for entry in timeline), (
            f"the bounded job.log route must not fall back to job()/events(): {timeline!r}"
        )
    finally:
        harness.close()


# ---------------------------------------------------------------------------
# B authorized, job bound to A: the durable-binding gate hides the job and
# nothing of its sentinel payload is disclosed. Current real behaviour: the
# bounded job_log_read_snapshot probe (metadata-only binding SQL) runs,
# yields False, and the daemon answers NODE_NOT_FOUND ("job not found")
# without ever calling job_log_page_bounded or the legacy job()/events().
# ---------------------------------------------------------------------------

def test_t35gw_b1_job_log_B_authorized_cannot_read_A_bound_job_no_sentinel() -> None:
    event_marker = "T35GW-EVT-FOREIGN-1f60"
    result_marker = "T35GW-RES-FOREIGN-6a2b"
    harness = _build_harness(
        project_permissions={PROJECT_A: ["inspect"], PROJECT_B: ["inspect"]},
        initial_host_permissions={"inspect"},
    )
    try:
        foreign_job = _create_synthetic_job(
            harness.store, project_id=PROJECT_A, tag="foreign",
            event_marker=event_marker, result_marker=result_marker,
        )
        result = harness.call("job_log", job_id=foreign_job, execution={"project_id": PROJECT_B})
        _assert_refusal(
            result, code="NODE_NOT_FOUND", message="job not found",
            sentinels=(event_marker, result_marker), label="B envelope vs A-bound job.log",
        )
        timeline = harness.timeline
        assert ("authority", "host_permissions") in timeline, "B authority phase should have run first"
        # Allowed-vs-forbidden split on this denial: the metadata-only
        # binding probe IS permitted (and must actually run) for the
        # scoped B request; every content read is forbidden.
        assert ("joblog_snapshot", foreign_job, PROJECT_B, "False") in timeline, (
            f"the bounded binding probe must run before the NODE_NOT_FOUND denial: {timeline!r}"
        )
        assert not any(entry[0] == "joblog_page" for entry in timeline), (
            f"a snapshot that yields False must never reach the bounded page: {timeline!r}"
        )
        _assert_no_content_reads(timeline, "B envelope vs A-bound job.log")
    finally:
        harness.close()


# ---------------------------------------------------------------------------
# Missing project identity -> fail closed before any durable job read.
# ---------------------------------------------------------------------------

def test_t35gw_c1_job_log_missing_outer_project_refused() -> None:
    event_marker = "T35GW-EVT-NOOUTERPROJ-7b52"
    result_marker = "T35GW-RES-NOOUTERPROJ-0c89"
    harness = _build_harness(
        project_permissions={PROJECT_A: ["inspect"]},
        initial_host_permissions={"inspect"},
    )
    try:
        job_id = _create_synthetic_job(
            harness.store, project_id=PROJECT_A, tag="no-outer-proj",
            event_marker=event_marker, result_marker=result_marker,
        )
        result = harness.call("job_log", job_id=job_id, execution={})
        _assert_refusal(
            result, code="PROJECT_IDENTITY_REQUIRED",
            message="job reads require outer execution.project_id",
            sentinels=(event_marker, result_marker), label="job.log without outer project",
        )
        # _authorize_cached_job_read (136-142) refuses before the authority
        # record/provider is even consulted: no authority markers, no
        # binding probe, no page, no content read of any kind.
        assert harness.timeline == [], f"expected no authority/read markers, got {harness.timeline!r}"
    finally:
        harness.close()


def test_t35gw_c2_job_log_unknown_project_refused_by_record() -> None:
    event_marker = "T35GW-EVT-UNKNOWNPROJ-3e47"
    result_marker = "T35GW-RES-UNKNOWNPROJ-8a15"
    harness = _build_harness(
        project_permissions={PROJECT_A: ["inspect"]},
        initial_host_permissions={"inspect"},
    )
    try:
        job_id = _create_synthetic_job(
            harness.store, project_id=PROJECT_A, tag="unknown-proj",
            event_marker=event_marker, result_marker=result_marker,
        )
        result = harness.call("job_log", job_id=job_id, execution={"project_id": PROJECT_MISSING})
        # Real production path: get_project -> _record -> missing row.
        _assert_refusal(
            result, code="PROJECT_NOT_FOUND", message="project does not exist",
            sentinels=(event_marker, result_marker), label="job.log unknown project",
        )
        timeline = harness.timeline
        assert not any(entry[0] == "authority" for entry in timeline), (
            f"missing row must fail inside _record before provider/verify markers: {timeline!r}"
        )
        assert not any(entry[0] in READ_KINDS for entry in timeline), (
            f"the outer refusal must never open the job-log snapshot or content method: {timeline!r}"
        )
    finally:
        harness.close()


# ---------------------------------------------------------------------------
# Dynamic host revocation: the REAL _permissions() consults the host
# provider on every authorize; removing inspect mid-life turns the same
# previously successful read into a refusal.
# ---------------------------------------------------------------------------

def test_t35gw_d1_job_log_host_inspect_dynamically_revoked_refused() -> None:
    event_marker = "T35GW-EVT-HOSTREVOKE-52cd"
    result_marker = "T35GW-RES-HOSTREVOKE-9b73"
    harness = _build_harness(
        project_permissions={PROJECT_A: ["inspect", "project_write"]},
        initial_host_permissions={"inspect", "project_write"},
    )
    try:
        job_id = _create_synthetic_job(
            harness.store, project_id=PROJECT_A, tag="host-revoke",
            event_marker=event_marker, result_marker=result_marker,
        )
        first = harness.call("job_log", job_id=job_id, execution={"project_id": PROJECT_A})
        assert _payload(first).get("success") is True, "pre-revocation read must succeed"
        assert event_marker in _blob(first)

        harness.host_state["granted"] = {"project_write"}  # host revokes inspect
        second = harness.call("job_log", job_id=job_id, execution={"project_id": PROJECT_A})
        # Real authorize_operation: project policy still says inspect, but the
        # live host grant no longer does -> PERMISSION_DENIED (line 289-290).
        _assert_refusal(
            second, code="PERMISSION_DENIED", message="project-scoped action requires inspect",
            sentinels=(event_marker, result_marker), label="job.log after host inspect revocation",
        )
        timeline = harness.timeline
        provider_markers = [entry for entry in timeline if entry == ("authority", "host_permissions")]
        assert len(provider_markers) == 2, f"provider must be consulted once per authorize: {timeline!r}"
        snapshot_reads = [entry for entry in timeline if entry[0] == "joblog_snapshot"]
        page_reads = [entry for entry in timeline if entry[0] == "joblog_page"]
        assert snapshot_reads == [("joblog_snapshot", job_id, PROJECT_A, "True")], (
            f"only the pre-revocation round may enter the binding snapshot: {timeline!r}"
        )
        assert page_reads == [("joblog_page", job_id, "0", "100")], (
            f"exactly the pre-revocation bounded page may read content: {timeline!r}"
        )
        assert not any(entry[0] in ("job_row", "events") for entry in timeline), (
            f"the revoked second round must not fall back to job()/events(): {timeline!r}"
        )
        _assert_authority_precedes_reads(timeline, "host-revoked job.log window")
    finally:
        harness.close()


# ---------------------------------------------------------------------------
# Project policy ceiling: host grants inspect but the persisted project
# policy omits it -> refuse; the request cannot grant its own capability.
# ---------------------------------------------------------------------------

def test_t35gw_e1_job_log_project_policy_without_inspect_refused() -> None:
    event_marker = "T35GW-EVT-POLICYNOINSPECT-c48e"
    result_marker = "T35GW-RES-POLICYNOINSPECT-2f96"
    harness = _build_harness(
        project_permissions={PROJECT_A: ["inspect"], PROJECT_C_NO_INSPECT: ["project_write"]},
        initial_host_permissions={"inspect", "project_write"},
    )
    try:
        job_id = _create_synthetic_job(
            harness.store, project_id=PROJECT_A, tag="policy-no-inspect",
            event_marker=event_marker, result_marker=result_marker,
        )
        result = harness.call("job_log", job_id=job_id, execution={"project_id": PROJECT_C_NO_INSPECT})
        _assert_refusal(
            result, code="PERMISSION_DENIED", message="project-scoped action requires inspect",
            sentinels=(event_marker, result_marker), label="job.log project policy without inspect",
        )
        timeline = harness.timeline
        assert ("authority", "host_permissions") in timeline, (
            "host provider must have been consulted (host DOES grant inspect); the denial comes from the policy side"
        )
        assert not any(entry[0] in BINDING_READ_KINDS for entry in timeline), (
            f"the policy-side refusal is raised at the outer gate, before any binding probe: {timeline!r}"
        )
        _assert_no_content_reads(timeline, "job.log project policy without inspect")
    finally:
        harness.close()


# ---------------------------------------------------------------------------
# Legacy job_list inner/outer disagreement: the envelope authorizes A, but a
# different project_id inside the domain arguments is refused
# (_control_read 13336-13338) without any list read. A same-project positive
# control prevents a blanket denial from passing this check.
# ---------------------------------------------------------------------------

def test_t35gw_f1_legacy_job_list_inner_outer_project_mismatch_refused() -> None:
    event_marker = "T35GW-EVT-LISTMISMATCH-60ab"
    result_marker = "T35GW-RES-LISTMISMATCH-d714"
    harness = _build_harness(
        project_permissions={PROJECT_A: ["inspect"], PROJECT_B: ["inspect"]},
        initial_host_permissions={"inspect"},
    )
    try:
        job_id = _create_synthetic_job(
            harness.store, project_id=PROJECT_A, tag="list-mismatch",
            event_marker=event_marker, result_marker=result_marker,
        )
        mismatch = harness.call(
            "job_list", project_id=PROJECT_B, execution={"project_id": PROJECT_A},
        )
        _assert_refusal(
            mismatch, code="PROJECT_IDENTITY_MISMATCH",
            message="job.list filter differs from the execution envelope",
            sentinels=(event_marker, result_marker), label="legacy job_list inner/outer mismatch",
        )
        timeline = harness.timeline
        assert not any(entry[0] in BINDING_READ_KINDS for entry in timeline), (
            f"job_list must never enter the job.log binding snapshot: {timeline!r}"
        )
        _assert_no_content_reads(timeline, "legacy job_list inner/outer mismatch")

        # Same-project positive control: authorized A envelope with the
        # matching inner filter is served through the strict binding list.
        positive = harness.call(
            "job_list", project_id=PROJECT_A, execution={"project_id": PROJECT_A},
        )
        payload = _payload(positive)
        assert payload.get("success") is True, f"same-project job_list should succeed: {payload!r}"
        listed_ids = [row["job_id"] for row in payload["data"]["jobs"]]
        assert job_id in listed_ids
        assert ("list_jobs", PROJECT_A, "True") in harness.timeline, (
            f"authorized list read must use enforce_project_binding=True: {harness.timeline!r}"
        )
        _assert_authority_precedes_reads(harness.timeline, "legacy job_list authorized window")
    finally:
        harness.close()
