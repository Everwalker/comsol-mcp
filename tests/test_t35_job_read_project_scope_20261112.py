"""T35 — synthetic project-boundary contract for ``job.log`` / ``job.result`` reads.

The contract tests below encode a PROPOSED project gate and are not claims of
public MCP, real grants, or native behavior. The earlier author and revision
snapshots, including two pre-fix characterizations, are preserved separately
under ``_state/t35-log-overflow-parent-20261112/``. Runtime evidence is recorded
separately and remains bound to the tested source and test identities.

Static basis (frozen snapshot
``_state/t35-log-overflow-parent-20261112/daemon_logger_author.py.txt``;
assumed identical to live ``comsol_mcp/_control_daemon.py`` — parent verifies):
- ``_dispatch_admitted`` 1018-1403 is the real outer route exercised here:
  CACHED_JOB_ACTIONS canonical names route at 1068-1069 to
  ``_dispatch_catalog_job_control`` (wrapper 5426-5429 into impl 12028-12120);
  CONTROL_READS legacy underscore names route at 1070-1075 straight to
  ``_control_read`` with NO execution parameter; ``registry_call`` routes at
  1078-1081 into ``_cached_registry_control`` 1405-1495, whose nested ``job.*``
  path lands at 1495 back on the same cached job route.  A raised
  ExecutionContractError is converted to an error response at 1399-1400 via
  ``_exception``; the generic handler at 1401-1403 calls ``_log_exception``,
  which writes to ``control-errors.log`` (13731-13733) and must never be
  reached — the inert receiver replaces it with a failure-only no-I/O sentinel.
- ``_timeouts`` 13710-13719, ``_error`` 13721-13723 and ``_exception``
  13725-13729 are the real helpers used on these paths.
- impl 12028-12120: ONLY ``solver.log_read`` performs
  ``project_authority.authorize_operation(project_id, "inspect")`` plus the
  envelope/inner project comparison (12039-12048); every other canonical job
  action falls through 12049-12120 with no authority call and no comparison of
  ``execution.project_id`` against the durable job binding.  ``job.log`` has an
  earlier dedicated branch (12076-12083); it does NOT share the status/result
  result tail.
- ``_control_read`` 13236-13367: ``job_list`` has its own branch 13291-13314
  delegating to ``OperationStore.list_jobs``, whose project filter is the
  permissive durable-metadata OR
  (``$.project_id OR $.execution.project_id OR $.project_root``;
  ``_operation_store.py`` 2160-2166); ``job_wait`` has its own branch
  13315-13339 and with a zero timeout on a terminal job returns the entire job
  row including its durable result; the shared 13366-13367 tail serves
  ``job_status``/``job_result`` (whole row, result materialized); the ``job_log``
  dedicated branch 13354-13359 reads ``store.job`` then ``store.events``.
- ``OperationStore.job`` returns the row with its durable result materialized,
  so it is a full job read, not a metadata-only lookup.
Catalog metadata (comsol_mcp/data/g2/02_ACTION_CATALOG.json, job.status/
job.log/job.result entries: ``"scope": "project"``, closed input schemas)
marks these as project-scoped, but catalog metadata alone proves nothing
about wire reachability.

Scope and honesty limits:
- This is a control-plane software boundary specification exercised in-process
  through the real unbound ``ControlDaemon._dispatch_admitted`` against an
  inert receiver with a FAKE per-project permission authority.  A supplied
  execution-envelope project ID is never treated as an authenticated user
  principal; the fake authority models the dispatcher gate only, not the MCP
  wire, real grants, or any public exploitability claim.  No real
  principal/MCP/disk behavior is claimed.
- All projects/jobs/events/results are unique synthetic literals in a fresh
  ``OperationStore(':memory:')``.  No real daemon is constructed or started,
  no service/Worker/SSH/COMSOL/network/old DB/UNKNOWN state is touched, and no
  historical job is used.
- Real components used unchanged (no blanket mocking of the function under
  test): the actual unbound ``_dispatch_admitted``,
  ``_dispatch_catalog_job_control``, ``_dispatch_catalog_job_control_impl``,
  ``_cached_registry_control``, ``_control_read``, ``_timeouts``, ``_error``
  and ``_exception`` invoked against the inert receiver; the real catalog
  validation inside the impl; the real ``OperationStore`` begin/add_event/
  update_job/job/events/list_jobs paths.  Store spies are pass-through
  recorders that never replace return values.
- Proposed contract asserted: (1) a non-empty ``execution.project_id`` in the
  OUTER envelope is required — the per-project identity is never carried in
  untrusted domain arguments, including on the legacy route; (2)
  ``authorize_operation(project, "inspect")`` is recorded in one shared ordered
  timeline and runs before any sensitive events/result retrieval (a full
  ``store.job`` row read to bind the job is permitted after the call, and
  because that row carries the result, result non-disclosure is asserted at
  the response level); (3) the durable job's bound project must match, else
  refuse without disclosing the bound job's sentinel payload; (4) a record
  with no usable project binding fails closed instead of guessed ownership;
  (5) the canonical direct route, the ``registry_call`` nested route, and the
  legacy underscore route all pass through the same outer ``_dispatch_admitted``
  gate; (6) narrowly on the same class: ``job.status``, zero-timeout
  ``job.wait`` and ``job.list`` honour the envelope project — ``job.list``
  must not treat the permissive ``list_jobs`` metadata OR filter as authority,
  including against a synthetic record with a forged ``project_root`` claim.
- Deliberately NOT touched: job.cancel/force-stop, job.reconcile, and the
  protected prior solver-dispatch-authority test paths.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._operation_store import OperationStore

DENIAL_CODE = "T35JR_FAKE_AUTHORITY_DENIED"
DENIAL_MESSAGE = "synthetic t35 job-read fake authority denial"

PROJECT_A = "t35jr-proj-A-20261112"
PROJECT_B = "t35jr-proj-B-20261112"

READ_KINDS = ("binding", "job_row", "events", "list_jobs")


class _FakeProjectAuthority:
    """Fake authority with explicit per-project permissions; records calls.

    Deliberately not the production ``ProjectAuthority``; it models only the
    dispatcher-level check the proposed contract requires.  Authority calls
    are appended to the same shared ordered timeline used by the store read
    spies, so ordering (authority before content retrieval) is provable.
    """

    def __init__(
        self,
        permissions: Mapping[str, set[str]] | None = None,
        timeline: list[tuple[str, ...]] | None = None,
    ) -> None:
        self.permissions: dict[str, set[str]] = {
            project: set(actions) for project, actions in (permissions or {}).items()
        }
        self.calls: list[tuple[str, str]] = []
        self.timeline = timeline

    def authorize_operation(self, project_id: str, action: str) -> None:
        self.calls.append((project_id, action))
        if self.timeline is not None:
            self.timeline.append(("authority", project_id, action))
        if action not in self.permissions.get(project_id, set()):
            raise ExecutionContractError(DENIAL_CODE, DENIAL_MESSAGE)


class _InertDispatchReceiver:
    """Inert receiver that delegates to the REAL unbound daemon methods.

    It exposes exactly the methods/attributes the exercised real code paths
    touch: ``.store`` / ``.project_authority`` carry task state; ``_timeouts``,
    ``_error``, ``_exception``, ``_dispatch_catalog_job_control``,
    ``_dispatch_catalog_job_control_impl``, ``_cached_registry_control`` and
    ``_control_read`` are bound to the actual ``ControlDaemon`` implementations
    (snapshot 1018-1403, 5426-5429, 12028-12120, 1405-1495, 13236-13367,
    13710-13729), never reimplemented.  ``_log_exception`` would write
    ``control-errors.log`` (13731-13733); it is replaced by a failure-only
    no-I/O sentinel because the real logger must never be invoked here.  Its
    sentinel raise escapes the outer ``except Exception`` handler, so reaching
    the generic unknown-state path fails the test loudly instead of hiding a
    real defect behind an EXECUTION_STATE_UNKNOWN response.
    """

    def __init__(self, store: OperationStore, project_authority: _FakeProjectAuthority) -> None:
        self.store = store
        self.project_authority = project_authority

    @staticmethod
    def _timeouts(execution: dict[str, Any]) -> dict[str, Any]:
        return ControlDaemon._timeouts(execution)

    def _dispatch_catalog_job_control(
        self, inner_operation: str, inner_arguments: dict[str, Any], *, execution: dict[str, Any]
    ) -> dict[str, Any]:
        return ControlDaemon._dispatch_catalog_job_control(
            self, inner_operation, inner_arguments, execution=execution  # type: ignore[arg-type]
        )

    def _dispatch_catalog_job_control_impl(
        self, inner_operation: str, inner_arguments: dict[str, Any], *, execution: dict[str, Any]
    ) -> dict[str, Any]:
        return ControlDaemon._dispatch_catalog_job_control_impl(
            self, inner_operation, inner_arguments, execution=execution  # type: ignore[arg-type]
        )

    def _cached_registry_control(
        self, outer_operation: str, outer_arguments: dict[str, Any], *, execution: dict[str, Any]
    ) -> dict[str, Any] | None:
        return ControlDaemon._cached_registry_control(
            self, outer_operation, outer_arguments, execution=execution  # type: ignore[arg-type]
        )

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
            "T35JR inert receiver reached the generic _dispatch_admitted exception path"
        )


def _install_read_spies(store: OperationStore, timeline: list[tuple[str, ...]]) -> None:
    """Pass-through recorders for durable reads on the shared ordered timeline.

    Return values always come from the real methods.  Install only after
    fixture setup so setup writes never enter the timeline.  ``store.job`` is
    a full job read (the returned row materializes the durable result), so
    for ``job.result``/``job.status`` non-disclosure is asserted at the
    response level, not by forbidding the post-authorization binding read.
    The current bounded log route is observed at its binding snapshot and
    bounded event page; neither spy alters the underlying return value.
    """
    real_job = store.job
    real_events = store.events
    real_list_jobs = store.list_jobs
    real_log_snapshot = store.job_log_read_snapshot
    real_log_page = store.job_log_page_bounded

    def spy_job(job_id: str) -> Any:
        timeline.append(("job_row", job_id))
        return real_job(job_id)

    def spy_events(job_id: str, offset: int = 0, limit: int = 100) -> Any:
        timeline.append(("events", job_id))
        return real_events(job_id, offset=offset, limit=limit)

    def spy_list_jobs(*args: Any, **kwargs: Any) -> Any:
        timeline.append(("list_jobs", str(kwargs.get("project_id"))))
        return real_list_jobs(*args, **kwargs)

    def spy_log_snapshot(job_id: str, *args: Any, **kwargs: Any) -> Any:
        timeline.append(("binding", job_id))
        return real_log_snapshot(job_id, *args, **kwargs)

    def spy_log_page(job_id: str, *args: Any, **kwargs: Any) -> Any:
        timeline.append(("events", job_id))
        return real_log_page(job_id, *args, **kwargs)

    store.job = spy_job  # type: ignore[method-assign]
    store.events = spy_events  # type: ignore[method-assign]
    store.list_jobs = spy_list_jobs  # type: ignore[method-assign]
    store.job_log_read_snapshot = spy_log_snapshot  # type: ignore[method-assign]
    store.job_log_page_bounded = spy_log_page  # type: ignore[method-assign]


def _install_status_wait_read_spy(
    store: OperationStore, timeline: list[tuple[str, ...]],
) -> None:
    """Record the bounded status/wait row read without replacing its result."""
    real_status_wait = store.job_for_status_wait

    def spy_status_wait(job_id: str, *, max_inline_result_bytes: int) -> Any:
        timeline.append(("job_row", job_id))
        return real_status_wait(
            job_id, max_inline_result_bytes=max_inline_result_bytes,
        )

    store.job_for_status_wait = spy_status_wait  # type: ignore[method-assign]


def _create_synthetic_job(
    store: OperationStore,
    *,
    project_id: str | None,
    tag: str,
    event_markers: tuple[str, ...] = (),
    result_marker: str | None = None,
    extra_job_metadata: Mapping[str, Any] | None = None,
) -> str:
    """Create one durable job through the real begin/add_event/update_job paths.

    ``project_id=None`` yields a historic-looking synthetic record with no
    usable project binding anywhere in its durable metadata.
    ``extra_job_metadata`` writes ordinary synthetic job-metadata fields at
    fixture creation time (e.g. a forged ``project_root`` claim for the
    ``list_jobs`` OR-filter trap); it is a fabricated record shape, not a
    claim that real durable metadata is tamperable.
    """
    metadata: dict[str, Any] = {"t35_synthetic": True}
    if project_id is not None:
        metadata["execution"] = {"project_id": project_id}
    record, reused = store.begin(
        request_id=f"t35jr-req-{tag}",
        idempotency_key=f"t35jr-idem-{tag}",
        request_hash="1" * 64,
        operation="job.synthetic-producer",
        metadata=metadata,
    )
    assert reused is False
    job_id = record["job_id"]
    assert isinstance(job_id, str) and job_id
    for marker in event_markers:
        store.add_event(job_id, "T35JReadEvent", {"marker": marker})
    job_updates: dict[str, Any] = dict(extra_job_metadata or {})
    if result_marker is not None:
        store.update_job(
            job_id, "SUCCEEDED", job_updates or {"t35_synthetic": True},
            result={"success": True, "data": {"marker": result_marker}},
        )
    elif job_updates:
        store.update_job(job_id, "RUNNING", job_updates)
    return job_id


def _outer_route(
    receiver: _InertDispatchReceiver,
    operation: str,
    arguments: Mapping[str, Any],
    execution: Mapping[str, Any],
) -> dict[str, Any]:
    """The real unbound outer route — nothing below bypasses it.

    ``_dispatch_admitted`` turns a raised ExecutionContractError (including a
    fake-authority denial) into an error response via ``_exception``
    (1399-1400), so refusals here surface as non-success responses, not raises.
    """
    return ControlDaemon._dispatch_admitted(
        receiver,  # type: ignore[arg-type]
        {
            "operation": operation,
            "arguments": dict(arguments),
            "execution": dict(execution),
        },
    )


def _canonical_route(
    receiver: _InertDispatchReceiver, operation: str, inner: Mapping[str, Any], envelope_project: str
) -> dict[str, Any]:
    """Canonical cached route via outer ``_dispatch_admitted`` (1068-1069)."""
    return _outer_route(
        receiver, operation, inner, {"project_id": envelope_project}
    )


def _nested_registry_route(
    receiver: _InertDispatchReceiver, operation: str, inner: Mapping[str, Any], envelope_project: str
) -> dict[str, Any]:
    """``registry_call`` nested route via outer ``_dispatch_admitted``
    (1078-1081 into ``_cached_registry_control`` 1405-1495)."""
    response = _outer_route(
        receiver,
        "registry_call",
        {"operation_id": operation, "arguments": dict(inner)},
        {"project_id": envelope_project},
    )
    assert isinstance(response, dict)
    return response


def _legacy_route(
    receiver: _InertDispatchReceiver,
    legacy_operation: str,
    arguments: Mapping[str, Any],
    *,
    envelope_project: str | None,
) -> dict[str, Any]:
    """Legacy underscore route via outer ``_dispatch_admitted`` (1070-1075).

    The project identity is routed ONLY in the outer execution envelope, never
    as a domain argument; ``envelope_project=None`` makes the absence explicit
    as ``execution={}`` for the fail-closed-on-absence control.  In the frozen
    snapshot ``_control_read`` ignores the envelope entirely — which is exactly
    what the proposed legacy contract must turn into an envelope-gated refusal.
    """
    execution: dict[str, Any] = {} if envelope_project is None else {"project_id": envelope_project}
    return _outer_route(receiver, legacy_operation, arguments, execution)


def _attempt(call: Callable[[], dict[str, Any]]) -> tuple[ExecutionContractError | None, dict[str, Any] | None]:
    try:
        return None, call()
    except ExecutionContractError as exc:
        return exc, None


def _serialized(exc: ExecutionContractError | None, response: dict[str, Any] | None) -> str:
    payload = exc.as_dict() if exc is not None else response
    return json.dumps(payload, sort_keys=True, default=str)


def _assert_denied_without_sentinel(
    exc: ExecutionContractError | None,
    response: dict[str, Any] | None,
    sentinels: tuple[str, ...],
    *,
    denial_code: str | None = None,
    label: str,
) -> None:
    """Proposed-contract refusal: not successful, sentinel nowhere, sensitive payload absent."""
    assert exc is not None or (
        isinstance(response, dict) and response.get("success") is not True
    ), f"{label}: expected a refusal, got {response!r}"
    blob = _serialized(exc, response)
    for sentinel in sentinels:
        assert sentinel not in blob, f"{label}: sentinel leaked into refusal payload"
    if denial_code is not None:
        observed = exc.code if exc is not None else str(
            (response or {}).get("error", {}).get("code")
        )
        assert observed == denial_code, f"{label}: expected distinctive fake denial code, got {observed!r}"


def _no_events_read(timeline: list[tuple[str, ...]], label: str) -> None:
    assert not any(kind == "events" for kind, *_ in timeline), f"{label}: sensitive events read happened"


def _assert_no_read_before_authority_denial(timeline: list[tuple[str, ...]], label: str) -> None:
    """Denied-inspect control: the recorded denial precedes (and prevents) every read."""
    assert timeline and timeline[0][0] == "authority", f"{label}: expected the authority call first"
    assert not any(kind in READ_KINDS for kind, *_ in timeline), (
        f"{label}: a job/events/list read happened despite the authority denial: {timeline!r}"
    )


def _assert_authority_precedes_first_read(timeline: list[tuple[str, ...]], label: str) -> None:
    """Authorized control: an authority call is recorded strictly before the first
    job/events/list content retrieval (a binding read after the call is allowed)."""
    authority_at = [i for i, entry in enumerate(timeline) if entry[0] == "authority"]
    read_at = [i for i, entry in enumerate(timeline) if entry[0] in READ_KINDS]
    assert authority_at, f"{label}: expected a recorded authority call"
    if read_at:
        assert min(authority_at) < min(read_at), (
            f"{label}: a read happened before the authority call: {timeline!r}"
        )


# ---------------------------------------------------------------------------
# (1) Positive controls under the PROPOSED gate — currently RED because the
# frozen snapshot never consults any authority on the job.log/job.result path.
# ---------------------------------------------------------------------------

def test_t35jread_job_log_authorized_bound_project_uses_authority_before_events() -> None:
    """PROPOSED CONTRACT (RED vs frozen snapshot): authorized project A + A-bound
    job succeeds on the canonical outer route with exactly the synthetic
    sentinel events, and the ordered timeline proves
    ``authorize_operation(A, 'inspect')`` occurs before the first job/events
    read and that the log read order is binding-then-events."""
    events = ("T35JR-EVT-LOGALPHA-4c17", "T35JR-EVT-LOGBRAVO-8de2")
    store = OperationStore(":memory:")
    try:
        job_id = _create_synthetic_job(store, project_id=PROJECT_A, tag="pos-log", event_markers=events)
        timeline: list[tuple[str, ...]] = []
        _install_read_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_A: {"inspect"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        response = _canonical_route(receiver, "job.log", {"job_id": job_id}, PROJECT_A)
        assert response.get("success") is True
        assert response["data"]["job_id"] == job_id
        assert [event["metadata"]["marker"] for event in response["data"]["events"]] == list(events)
        # Authority gate is part of the proposed positive contract too:
        assert authority.calls == [(PROJECT_A, "inspect")]
        assert timeline[0] == ("authority", PROJECT_A, "inspect")
        assert ("binding", job_id) in timeline[1:]
        assert [entry for entry in timeline if entry[0] == "events"] == [("events", job_id)]
        assert timeline.index(("binding", job_id)) < timeline.index(("events", job_id))
    finally:
        store.close()


def test_t35jread_job_result_authorized_bound_project_uses_authority_before_row_disclosure() -> None:
    """PROPOSED CONTRACT (RED vs frozen snapshot): authorized A + A-bound result
    returns the exact sentinel result while the timeline proves the authority
    call precedes the full job-row read (``OperationStore.job`` materializes
    the durable result, so this is a full job read, not metadata-only)."""
    result_marker = "T35JR-RES-RESULTGAMMA-61b0"
    store = OperationStore(":memory:")
    try:
        job_id = _create_synthetic_job(
            store, project_id=PROJECT_A, tag="pos-result", result_marker=result_marker
        )
        timeline: list[tuple[str, ...]] = []
        _install_read_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_A: {"inspect"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        response = _canonical_route(receiver, "job.result", {"job_id": job_id}, PROJECT_A)
        assert response.get("success") is True
        assert response["data"]["job_id"] == job_id
        assert response["data"]["status"] == "SUCCEEDED"
        assert response["data"]["result"]["data"]["marker"] == result_marker
        assert authority.calls == [(PROJECT_A, "inspect")]
        assert timeline[0] == ("authority", PROJECT_A, "inspect")
        assert ("job_row", job_id) in timeline[1:]
    finally:
        store.close()


# ---------------------------------------------------------------------------
# (2) Cross-project: inspect-only for B, job bound to A, job ID known.
# ---------------------------------------------------------------------------

def test_t35jread_job_log_inspect_only_project_B_cannot_read_project_A_bound_events() -> None:
    """PROPOSED CONTRACT (RED vs frozen snapshot): caller whose fake authority
    grants inspect only for B must not obtain A-bound sentinel events on the
    canonical outer route — the recorded B authority call precedes any events
    retrieval and the refusal carries no sentinel; a post-authorization full
    job-row read for the binding comparison is permitted."""
    events = ("T35JR-EVT-FOREIGNDELTA-93af",)
    store = OperationStore(":memory:")
    try:
        foreign_job = _create_synthetic_job(
            store, project_id=PROJECT_A, tag="xproj-log", event_markers=events
        )
        timeline: list[tuple[str, ...]] = []
        _install_read_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_B: {"inspect"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        exc, response = _attempt(
            lambda: _canonical_route(receiver, "job.log", {"job_id": foreign_job}, PROJECT_B)
        )
        _assert_denied_without_sentinel(
            exc, response, events, label="cross-project job.log"
        )
        _no_events_read(timeline, "cross-project job.log")
        _assert_authority_precedes_first_read(timeline, "cross-project job.log")
        assert authority.calls == [(PROJECT_B, "inspect")]
    finally:
        store.close()


def test_t35jread_job_result_inspect_only_project_B_cannot_read_project_A_bound_result() -> None:
    """PROPOSED CONTRACT (RED vs frozen snapshot): B-inspect caller must not
    receive the A-bound sentinel result even though the post-authorization full
    job-row read returns the row carrying it — the refusal must withhold the
    payload at the response level."""
    result_marker = "T35JR-RES-FOREIGNSIGMA-07ce"
    store = OperationStore(":memory:")
    try:
        foreign_job = _create_synthetic_job(
            store, project_id=PROJECT_A, tag="xproj-result", result_marker=result_marker
        )
        timeline: list[tuple[str, ...]] = []
        _install_read_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_B: {"inspect"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        exc, response = _attempt(
            lambda: _canonical_route(receiver, "job.result", {"job_id": foreign_job}, PROJECT_B)
        )
        _assert_denied_without_sentinel(
            exc, response, (result_marker,), label="cross-project job.result"
        )
        _assert_authority_precedes_first_read(timeline, "cross-project job.result")
        assert authority.calls == [(PROJECT_B, "inspect")]
    finally:
        store.close()


# ---------------------------------------------------------------------------
# (3) Denied project inspect fails before any sensitive retrieval.
# ---------------------------------------------------------------------------

def test_t35jread_job_log_denied_authority_fails_before_events_read_with_distinctive_code() -> None:
    """PROPOSED CONTRACT (RED vs frozen snapshot): authority with no inspect for
    the envelope project refuses with the distinctive fake denial before the
    store is touched at all — the shared timeline proves the denial precedes
    any job/events read (none happens)."""
    events = ("T35JR-EVT-DENYPROBE-2b88",)
    store = OperationStore(":memory:")
    try:
        job_id = _create_synthetic_job(
            store, project_id=PROJECT_B, tag="deny-log", event_markers=events
        )
        timeline: list[tuple[str, ...]] = []
        _install_read_spies(store, timeline)
        authority = _FakeProjectAuthority({}, timeline=timeline)  # no grants at all for B
        receiver = _InertDispatchReceiver(store, authority)

        exc, response = _attempt(
            lambda: _canonical_route(receiver, "job.log", {"job_id": job_id}, PROJECT_B)
        )
        _assert_denied_without_sentinel(
            exc, response, events, denial_code=DENIAL_CODE, label="denied job.log"
        )
        _assert_no_read_before_authority_denial(timeline, "denied job.log")
        assert authority.calls == [(PROJECT_B, "inspect")]
    finally:
        store.close()


def test_t35jread_job_result_denied_authority_fails_before_result_disclosure() -> None:
    """PROPOSED CONTRACT (RED vs frozen snapshot): same denial requirement on
    the result route — distinctive fake code, no sentinel result, and zero
    store reads on the timeline before/after the denial."""
    result_marker = "T35JR-RES-DENYPROBE-55f1"
    store = OperationStore(":memory:")
    try:
        job_id = _create_synthetic_job(
            store, project_id=PROJECT_B, tag="deny-result", result_marker=result_marker
        )
        timeline: list[tuple[str, ...]] = []
        _install_read_spies(store, timeline)
        authority = _FakeProjectAuthority({}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        exc, response = _attempt(
            lambda: _canonical_route(receiver, "job.result", {"job_id": job_id}, PROJECT_B)
        )
        _assert_denied_without_sentinel(
            exc, response, (result_marker,), denial_code=DENIAL_CODE, label="denied job.result"
        )
        _assert_no_read_before_authority_denial(timeline, "denied job.result")
        assert authority.calls == [(PROJECT_B, "inspect")]
    finally:
        store.close()


# ---------------------------------------------------------------------------
# (4) Historic-looking NEW synthetic record without a usable project binding
#     fails closed instead of guessed ownership.
# ---------------------------------------------------------------------------

def test_t35jread_job_log_record_without_project_binding_fails_closed() -> None:
    """PROPOSED CONTRACT (RED vs frozen snapshot): a NEW synthetic record with no
    project identity anywhere in its durable metadata must fail closed even when
    the caller supplies an authorized project — no guessed ownership, no
    sentinel events."""
    events = ("T35JR-EVT-UNBOUNDTAU-e349",)
    store = OperationStore(":memory:")
    try:
        unbound_job = _create_synthetic_job(
            store, project_id=None, tag="unbound-log", event_markers=events
        )
        timeline: list[tuple[str, ...]] = []
        _install_read_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_A: {"inspect"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        exc, response = _attempt(
            lambda: _canonical_route(receiver, "job.log", {"job_id": unbound_job}, PROJECT_A)
        )
        _assert_denied_without_sentinel(exc, response, events, label="unbound job.log")
        _no_events_read(timeline, "unbound job.log")
    finally:
        store.close()


def test_t35jread_job_result_record_without_project_binding_fails_closed() -> None:
    """PROPOSED CONTRACT (RED vs frozen snapshot): unbound synthetic record with
    a durable result must fail closed; the authorized envelope project does not
    create ownership."""
    result_marker = "T35JR-RES-UNBOUNDOMEGA-c6d4"
    store = OperationStore(":memory:")
    try:
        unbound_job = _create_synthetic_job(
            store, project_id=None, tag="unbound-result", result_marker=result_marker
        )
        timeline: list[tuple[str, ...]] = []
        _install_read_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_A: {"inspect"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        exc, response = _attempt(
            lambda: _canonical_route(receiver, "job.result", {"job_id": unbound_job}, PROJECT_A)
        )
        _assert_denied_without_sentinel(exc, response, (result_marker,), label="unbound job.result")
    finally:
        store.close()


def test_t35jread_canonical_route_without_envelope_project_identity_fails_closed() -> None:
    """PROPOSED CONTRACT (RED vs frozen snapshot): an explicitly absent
    ``execution.project_id`` is a missing identity, not a wildcard — the read
    must fail closed instead of bypassing the gate."""
    events = ("T35JR-EVT-NOENVELOPE-78ab",)
    store = OperationStore(":memory:")
    try:
        job_id = _create_synthetic_job(
            store, project_id=PROJECT_A, tag="noenv-log", event_markers=events
        )
        timeline: list[tuple[str, ...]] = []
        _install_read_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_A: {"inspect"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        exc, response = _attempt(
            lambda: _outer_route(receiver, "job.log", {"job_id": job_id}, {})
        )
        _assert_denied_without_sentinel(exc, response, events, label="no-envelope job.log")
        _no_events_read(timeline, "no-envelope job.log")
    finally:
        store.close()


# ---------------------------------------------------------------------------
# (5) Route equivalence: canonical direct, registry_call nested, legacy alias —
#     all through the same real outer _dispatch_admitted.
# ---------------------------------------------------------------------------

def test_t35jread_nested_registry_call_job_log_cross_project_denied_without_sentinel() -> None:
    """PROPOSED CONTRACT (RED vs frozen snapshot): ``registry_call`` nested
    ``job.log`` routed through the real outer dispatcher must hit the same gate
    — B envelope + A-bound job refuses with no sentinel and no events read,
    even though the nested route only adds a transport wrapper
    (snapshot 1078-1081 into 1405-1495)."""
    events = ("T35JR-EVT-NESTEDFOREIGN-1f60",)
    store = OperationStore(":memory:")
    try:
        foreign_job = _create_synthetic_job(
            store, project_id=PROJECT_A, tag="nested-xproj", event_markers=events
        )
        timeline: list[tuple[str, ...]] = []
        _install_read_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_B: {"inspect"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        exc, response = _attempt(
            lambda: _nested_registry_route(
                receiver, "job.log", {"job_id": foreign_job}, PROJECT_B
            )
        )
        _assert_denied_without_sentinel(exc, response, events, label="nested cross-project job.log")
        _no_events_read(timeline, "nested cross-project job.log")
        _assert_authority_precedes_first_read(timeline, "nested cross-project job.log")
        assert authority.calls == [(PROJECT_B, "inspect")]
    finally:
        store.close()


def test_t35jread_nested_registry_call_job_log_authorized_bound_project_success() -> None:
    """PROPOSED CONTRACT (RED vs frozen snapshot): the nested route serves the
    authorized bound case with the same authority-first ordering as the direct
    route — wrapper adds no bypass and no double gate."""
    events = ("T35JR-EVT-NESTEDALPHA-a052",)
    store = OperationStore(":memory:")
    try:
        job_id = _create_synthetic_job(
            store, project_id=PROJECT_A, tag="nested-pos", event_markers=events
        )
        timeline: list[tuple[str, ...]] = []
        _install_read_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_A: {"inspect"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        response = _nested_registry_route(receiver, "job.log", {"job_id": job_id}, PROJECT_A)
        assert response.get("success") is True
        assert [event["metadata"]["marker"] for event in response["data"]["events"]] == list(events)
        assert authority.calls == [(PROJECT_A, "inspect")]
        assert timeline[0] == ("authority", PROJECT_A, "inspect")
        assert ("binding", job_id) in timeline[1:]
        assert [entry for entry in timeline if entry[0] == "events"] == [("events", job_id)]
        assert timeline.index(("binding", job_id)) < timeline.index(("events", job_id))
    finally:
        store.close()


def test_t35jread_legacy_underscore_job_log_B_envelope_cannot_disclose_A_bound_events() -> None:
    """PROPOSED CONTRACT (RED vs frozen snapshot): the legacy ``job_log`` route
    (outer 1070-1075 into the execution-blind ``_control_read``) must not
    disclose an A-bound job's events when the outer execution envelope names an
    authorized B.  The project identity is routed ONLY through the outer
    envelope, not through untrusted domain arguments.  The claim is
    dispatcher-level, not an authenticated principal; the durable binding
    comparison is the floor.  Mechanism (envelope parameter into the legacy
    reader vs re-routing the alias through the gated catalog path) is left to
    the implementer — only the envelope-gated refusal is pinned."""
    events = ("T35JR-EVT-LEGACYFOREIGN-b7e3",)
    store = OperationStore(":memory:")
    try:
        foreign_job = _create_synthetic_job(
            store, project_id=PROJECT_A, tag="legacy-xproj", event_markers=events
        )
        timeline: list[tuple[str, ...]] = []
        _install_read_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_B: {"inspect"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        exc, response = _attempt(
            lambda: _legacy_route(receiver, "job_log", {"job_id": foreign_job}, envelope_project=PROJECT_B)
        )
        _assert_denied_without_sentinel(exc, response, events, label="legacy job_log B envelope")
        _no_events_read(timeline, "legacy job_log B envelope")
        _assert_authority_precedes_first_read(timeline, "legacy job_log B envelope")
        assert authority.calls == [(PROJECT_B, "inspect")]
    finally:
        store.close()


def test_t35jread_legacy_underscore_job_log_authorized_bound_project_uses_authority_before_events() -> None:
    """PROPOSED CONTRACT (RED vs frozen snapshot): same-project positive control
    on the legacy route — a legitimate A envelope plus A-bound job must succeed
    with the exact sentinel events and the authority call ordered before the
    job/events reads, so a blanket legacy denial cannot pass as correct."""
    events = ("T35JR-EVT-LEGACYALPHA-9d41",)
    store = OperationStore(":memory:")
    try:
        job_id = _create_synthetic_job(
            store, project_id=PROJECT_A, tag="legacy-pos", event_markers=events
        )
        timeline: list[tuple[str, ...]] = []
        _install_read_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_A: {"inspect"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        response = _legacy_route(receiver, "job_log", {"job_id": job_id}, envelope_project=PROJECT_A)
        assert response.get("success") is True
        assert [event["metadata"]["marker"] for event in response["data"]["events"]] == list(events)
        assert authority.calls == [(PROJECT_A, "inspect")]
        assert timeline[0] == ("authority", PROJECT_A, "inspect")
        assert ("binding", job_id) in timeline[1:]
        assert [entry for entry in timeline if entry[0] == "events"] == [("events", job_id)]
        assert timeline.index(("binding", job_id)) < timeline.index(("events", job_id))
    finally:
        store.close()


def test_t35jread_legacy_underscore_job_log_without_execution_project_identity_fails_closed() -> None:
    """PROPOSED CONTRACT (RED vs frozen snapshot): a legacy call with the outer
    execution project identity EXPLICITLY absent (``execution={}``) must fail
    closed.  This requires fail-closed-on-absence only; it does not claim that
    the omitted identity is authenticated by anything on today's wire."""
    events = ("T35JR-EVT-LEGACYNOENV-52c8",)
    store = OperationStore(":memory:")
    try:
        job_id = _create_synthetic_job(
            store, project_id=PROJECT_A, tag="legacy-noenv", event_markers=events
        )
        timeline: list[tuple[str, ...]] = []
        _install_read_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_A: {"inspect"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        exc, response = _attempt(
            lambda: _legacy_route(receiver, "job_log", {"job_id": job_id}, envelope_project=None)
        )
        _assert_denied_without_sentinel(exc, response, events, label="legacy job_log no envelope identity")
        _no_events_read(timeline, "legacy job_log no envelope identity")
    finally:
        store.close()


def test_t35jread_legacy_underscore_job_result_unbound_record_fails_closed() -> None:
    """PROPOSED CONTRACT (RED vs frozen snapshot): legacy ``job_result`` with an
    authorized A envelope on an unbound synthetic record fails closed at the
    lowest cached reader, so no route into ``_control_read`` can guess
    ownership."""
    result_marker = "T35JR-RES-LEGACYUNBOUND-de29"
    store = OperationStore(":memory:")
    try:
        unbound_job = _create_synthetic_job(
            store, project_id=None, tag="legacy-unbound", result_marker=result_marker
        )
        timeline: list[tuple[str, ...]] = []
        _install_read_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_A: {"inspect"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        exc, response = _attempt(
            lambda: _legacy_route(receiver, "job_result", {"job_id": unbound_job}, envelope_project=PROJECT_A)
        )
        _assert_denied_without_sentinel(exc, response, (result_marker,), label="legacy job_result unbound")
    finally:
        store.close()


# ---------------------------------------------------------------------------
# (6) Narrowly scoped same-class checks: job.status, zero-timeout job.wait,
#     and job.list including the permissive-metadata OR-filter trap.
# ---------------------------------------------------------------------------

def test_t35jread_job_status_B_authorized_cannot_disclose_A_bound_result_row() -> None:
    """PROPOSED CONTRACT (RED vs frozen snapshot): canonical ``job.status`` on
    the outer route shares the 13366-13367 whole-row tail with job.result; a
    B envelope with B-inspect authority must not disclose the A-bound job row
    (including its durable result sentinel).  No cancel/reconcile path is
    exercised."""
    result_marker = "T35JR-RES-STATUSFOREIGN-3e77"
    store = OperationStore(":memory:")
    try:
        foreign_job = _create_synthetic_job(
            store, project_id=PROJECT_A, tag="status-xproj", result_marker=result_marker
        )
        timeline: list[tuple[str, ...]] = []
        _install_read_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_B: {"inspect"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        exc, response = _attempt(
            lambda: _canonical_route(receiver, "job.status", {"job_id": foreign_job}, PROJECT_B)
        )
        _assert_denied_without_sentinel(exc, response, (result_marker,), label="cross-project job.status")
        _no_events_read(timeline, "cross-project job.status")
        _assert_authority_precedes_first_read(timeline, "cross-project job.status")
        assert authority.calls == [(PROJECT_B, "inspect")]
    finally:
        store.close()


def test_t35jread_job_wait_zero_timeout_B_authorized_cannot_disclose_A_bound_result() -> None:
    """PROPOSED CONTRACT (RED vs frozen snapshot): zero-timeout canonical
    ``job.wait`` on a terminal A-bound job returns the whole row from its
    dedicated 13315-13339 branch without sleeping; a B envelope with B-inspect
    authority must not disclose that A-bound sentinel result."""
    result_marker = "T35JR-RES-WAITFOREIGN-8ab5"
    store = OperationStore(":memory:")
    try:
        foreign_job = _create_synthetic_job(
            store, project_id=PROJECT_A, tag="wait-xproj", result_marker=result_marker
        )
        timeline: list[tuple[str, ...]] = []
        _install_read_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_B: {"inspect"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        exc, response = _attempt(
            lambda: _canonical_route(
                receiver, "job.wait", {"job_id": foreign_job, "timeout_s": 0.0}, PROJECT_B
            )
        )
        _assert_denied_without_sentinel(exc, response, (result_marker,), label="cross-project job.wait")
        _no_events_read(timeline, "cross-project job.wait")
        _assert_authority_precedes_first_read(timeline, "cross-project job.wait")
        assert authority.calls == [(PROJECT_B, "inspect")]
    finally:
        store.close()


def test_t35jread_job_list_B_authorized_forged_project_root_excludes_foreign_job() -> None:
    """PROPOSED CONTRACT (RED vs frozen snapshot): ``job.list`` B-envelope
    positive plus foreign-control in one store.  One synthetic A-bound job
    carries a FORGED synthetic ``project_root=B`` job-metadata claim (written
    through the ordinary fixture write path — not a claim that real durable
    metadata is tamperable), which the permissive ``list_jobs`` OR filter
    (``$.project_id OR $.execution.project_id OR $.project_root``,
    ``_operation_store.py`` 2160-2166) matches as a B hit.  The gated route
    must return ONLY the genuinely B-bound job, must not disclose the foreign
    job's id or sentinel payload, and must record the B authority call before
    the list read — treating the OR filter as authority is exactly the defect
    this control catches.  The normal authorized list positive (the B-bound
    job present) is asserted in the same test."""
    forged_marker = "T35JR-RES-LISTFORGED-2d9f"
    foreign_job = ""
    store = OperationStore(":memory:")
    try:
        foreign_job = _create_synthetic_job(
            store,
            project_id=PROJECT_A,
            tag="list-forged-a",
            result_marker=forged_marker,
            extra_job_metadata={"project_root": PROJECT_B},
        )
        own_job = _create_synthetic_job(store, project_id=PROJECT_B, tag="list-true-b")
        timeline: list[tuple[str, ...]] = []
        _install_read_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_B: {"inspect"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        response = _canonical_route(
            receiver, "job.list", {"filter": {"project_id": PROJECT_B}}, PROJECT_B
        )
        assert response.get("success") is True
        listed_ids = [row["job_id"] for row in response["data"]["jobs"]]
        # Normal authorized positive: the genuine B-bound job is present.
        assert own_job in listed_ids
        # The forged-claim A-bound job must not be served under B authority.
        assert foreign_job not in listed_ids
        blob = _serialized(None, response)
        assert forged_marker not in blob, "forged-claim foreign sentinel leaked into job.list"
        assert foreign_job not in blob, "foreign job id leaked into job.list"
        assert authority.calls == [(PROJECT_B, "inspect")]
        _assert_authority_precedes_first_read(timeline, "job.list B envelope")
    finally:
        store.close()


# Same-project positive controls prevent a blanket denial from satisfying the
# status/wait cross-project checks above. Earlier vulnerability characterizations
# remain available in the separately preserved, unrun revision snapshot.

def test_t35jread_job_status_A_authorized_returns_A_bound_result() -> None:
    marker = "T35JR-RES-STATUSPOS-513c"
    store = OperationStore(":memory:")
    try:
        job_id = _create_synthetic_job(store, project_id=PROJECT_A, tag="status-pos", result_marker=marker)
        timeline: list[tuple[str, ...]] = []
        _install_read_spies(store, timeline)
        _install_status_wait_read_spy(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_A: {"inspect"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)
        response = _canonical_route(receiver, "job.status", {"job_id": job_id}, PROJECT_A)
        assert response.get("success") is True
        assert response["data"]["job_id"] == job_id
        assert response["data"]["result"]["data"]["marker"] == marker
        assert authority.calls == [(PROJECT_A, "inspect")]
        assert timeline[0] == ("authority", PROJECT_A, "inspect")
        assert ("job_row", job_id) in timeline[1:]
        _assert_authority_precedes_first_read(timeline, "same-project job.status")
    finally:
        store.close()


def test_t35jread_job_wait_A_authorized_returns_A_bound_result() -> None:
    marker = "T35JR-RES-WAITPOS-139a"
    store = OperationStore(":memory:")
    try:
        job_id = _create_synthetic_job(store, project_id=PROJECT_A, tag="wait-pos", result_marker=marker)
        timeline: list[tuple[str, ...]] = []
        _install_read_spies(store, timeline)
        _install_status_wait_read_spy(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_A: {"inspect"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)
        response = _canonical_route(receiver, "job.wait", {"job_id": job_id, "timeout_s": 0.0}, PROJECT_A)
        assert response.get("success") is True
        assert response["data"]["job_id"] == job_id
        assert response["data"]["result"]["data"]["marker"] == marker
        assert authority.calls == [(PROJECT_A, "inspect")]
        assert timeline[0] == ("authority", PROJECT_A, "inspect")
        assert ("job_row", job_id) in timeline[1:]
        _assert_authority_precedes_first_read(timeline, "same-project job.wait")
    finally:
        store.close()
