"""T35 — adversarial addendum to the synthetic project-boundary job LIST/READ contracts.

This file adds a small number of ADDITIONAL synthetic contract controls on top
of ``tests/test_t35_job_read_project_scope_20261112.py`` (parent v1).  It reuses
that module's audited helpers unchanged (fake per-project authority, inert
receiver delegating to the real unbound ``ControlDaemon`` methods, pass-through
read spies on a shared ordered timeline, synthetic job fixtures, real-route
dispatch wrappers) by importing them; nothing here reimplements or modifies the
parent, the product, or any prior test.

Honesty limits (identical discipline to the parent):
- PROPOSED-CONTRACT controls, not executed here and not claims of real
  leakage, public MCP reachability, authenticated principals or native
  behavior.  Like parent v1 they are expected to be RED against the current
  source; runtime evidence is recorded separately by the parent flow.
- Fresh ``OperationStore(':memory:')`` per store, unique synthetic literals; no
  real daemon, DB, service/Worker/SSH/COMSOL/network, historical job, or
  UNKNOWN state is touched.  job.cancel/force-stop and job.reconcile are not
  exercised.  Fixtures create synthetic record shapes through the ordinary
  store write paths — this is a fabricated record shape, never a claim that
  real durable metadata is tamperable.

Static basis (ranges read in this task; same frozen snapshot family as parent):
- ``_operation_store.py`` begin 406-455: the SAME serialized metadata dict is
  inserted into BOTH ``operations`` and ``jobs`` (436-446), so a job created
  with a top-level ``project_id`` has a consistent durable job/operation
  binding — the shape the ``session.start``/``connect`` jobs are reported to
  carry (that session-shape attribution comes from the delegation context; the
  exact daemon lines were not line-pinned here and are marked UNKNOWN).
- ``_operation_store.py`` update_job 2010-2053: metadata merges into the
  ``jobs`` row only (2031-2042); the ``operations`` row is touched for
  status/result (2044-2054) but never its metadata — the exact mechanism the
  divergence control below manufactures synthetically.
- ``_operation_store.py`` list_jobs 2145-2176: permissive OR metadata filter
  (2160-2166) and SQL-level ``total`` COUNT plus ``LIMIT/OFFSET`` (2169-2174),
  i.e. today pagination/total are computed from the OR filter, with no
  envelope-derived authority.
- ``_control_daemon.py`` canonical ``job.list`` 12053-12074 (filter/cursor/
  offset mapping into ``_control_read("job_list", ...)``) and the job_list
  branch 13291-13314: response ``data`` carries ``jobs``/``total``/``offset``/
  ``limit``/``has_more`` straight from ``OperationStore.list_jobs``.
- Positive controls (authorized same-project list pages, top-level-binding
  job.log/job.result reads, own-project jobs visible next to excluded
  divergent records) prevent a blanket denial from passing this suite.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._operation_store import OperationStore

from tests.test_t35_job_read_project_scope_20261112 import (
    PROJECT_A,
    PROJECT_B,
    _FakeProjectAuthority,
    _InertDispatchReceiver,
    _assert_authority_precedes_first_read,
    _attempt,
    _canonical_route,
    _create_synthetic_job,
    _install_read_spies,
    _serialized,
)


def _create_top_level_project_bound_job(
    store: OperationStore,
    *,
    project_id: str,
    tag: str,
    event_markers: tuple[str, ...] = (),
    result_marker: str | None = None,
) -> str:
    """Synthetic session.start/connect-shaped binding: TOP-LEVEL ``project_id``
    only (no ``execution`` subtree) written through ``begin``, which the store
    writes identically to the operations and jobs rows (evidence 436-446), so
    the durable job/operation binding is consistent by construction."""
    record, reused = store.begin(
        request_id=f"t35ja-req-{tag}",
        idempotency_key=f"t35ja-idem-{tag}",
        request_hash="1" * 64,
        operation="job.synthetic-producer",
        metadata={"t35_synthetic": True, "project_id": project_id},
    )
    assert reused is False
    job_id = record["job_id"]
    assert isinstance(job_id, str) and job_id
    for marker in event_markers:
        store.add_event(job_id, "T35JAReadEvent", {"marker": marker})
    if result_marker is not None:
        store.update_job(
            job_id,
            "SUCCEEDED",
            {"t35_synthetic": True},
            result={"success": True, "data": {"marker": result_marker}},
        )
    return job_id


def _list_page(
    receiver: _InertDispatchReceiver, envelope_project: str, *, offset: int, limit: int
) -> dict[str, Any]:
    """Envelope-only canonical ``job.list`` page — no domain filter supplied,
    so the envelope project is the ONLY project signal on the wire."""
    return _canonical_route(receiver, "job.list", {"offset": offset, "limit": limit}, envelope_project)


# ---------------------------------------------------------------------------
# (1) Interleaved A/B jobs; B paginates with a small page size.  The project
#     gate must run BEFORE pagination and before total/has_more: no page may
#     contain A data, total/has_more must describe only B jobs, and every B job
#     must be reachable across the pages.
# ---------------------------------------------------------------------------

def test_t35ja_job_list_B_envelope_paginates_after_project_filter_all_B_jobs_reachable() -> None:
    """PROPOSED CONTRACT (RED vs current source): A and B jobs interleaved in
    one store; a B envelope (B-inspect authority) pages ``job.list`` with
    limit=2 and NO explicit filter.  Every page must contain only B jobs, the
    union of all pages must equal the complete B-job set, and every page's
    ``total``/``has_more`` must be computed post-filter (total == B count;
    has_more true until the final page).  A-job ids and sentinels must appear
    nowhere in any page payload."""
    a_ids, b_ids = [], []
    store = OperationStore(":memory:")
    try:
        # Interleave creation so rowid-DESC pages mix A and B rows.
        for index in range(3):
            a_ids.append(
                _create_synthetic_job(
                    store,
                    project_id=PROJECT_A,
                    tag=f"pg-a{index}",
                    result_marker=f"T35JA-RES-PGA{index}-a1f{index}",
                )
            )
            b_ids.append(
                _create_synthetic_job(
                    store,
                    project_id=PROJECT_B,
                    tag=f"pg-b{index}",
                    result_marker=f"T35JA-RES-PGB{index}-b2e{index}",
                )
            )
        b_ids.append(
            _create_synthetic_job(
                store,
                project_id=PROJECT_B,
                tag="pg-b3",
                result_marker="T35JA-RES-PGB3-b2e3",
            )
        )
        timeline: list[tuple[str, ...]] = []
        _install_read_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_B: {"inspect"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)
        # Exercise the production decimal-cursor parser on the inert receiver.
        setattr(receiver, "_page_cursor_offset", ControlDaemon._page_cursor_offset)

        limit = 2
        collected: list[str] = []
        offset = 0
        cursor: str | None = None
        for page_index in range(8):  # fixed bound prevents an infinite pagination loop
            response = (
                _list_page(receiver, PROJECT_B, offset=offset, limit=limit)
                if cursor is None
                else _canonical_route(receiver, "job.list", {"cursor": cursor, "limit": limit}, PROJECT_B)
            )
            assert response.get("success") is True, f"page {page_index}: expected success, got {response!r}"
            data = response.get("data")
            assert isinstance(data, dict), f"page {page_index}: missing list payload {response!r}"
            page_ids = [row["job_id"] for row in data["jobs"]]
            # Filter strictly precedes pagination: no A row may reach any page.
            assert set(page_ids) <= set(b_ids), (
                f"page {page_index}: non-B job(s) served under B envelope: {page_ids!r}"
            )
            collected.extend(page_ids)
            # total/has_more are post-filter metadata, not raw-store metadata.
            assert data["total"] == len(b_ids), (
                f"page {page_index}: total pre-counted unfiltered jobs: {data['total']!r}"
            )
            blob = _serialized(None, response)
            for foreign_id in a_ids:
                assert foreign_id not in blob, f"page {page_index}: A job id leaked into B page"
            for index in range(3):
                sentinel = f"T35JA-RES-PGA{index}-a1f{index}"
                assert sentinel not in blob, f"page {page_index}: A sentinel leaked into B page"
            has_more = bool(data.get("has_more"))
            expected_has_more = offset + limit < len(b_ids)
            assert has_more is expected_has_more, (
                f"page {page_index}: has_more {has_more} inconsistent with filtered total"
            )
            next_cursor = data.get("next_cursor")
            assert next_cursor == (str(offset + len(page_ids)) if has_more else None)
            if not has_more:
                break
            assert isinstance(next_cursor, str)
            cursor = next_cursor
            offset += len(page_ids)
        else:
            raise AssertionError("B-envelope pagination never terminated within bound")
        # Completeness: every B job reachable, none duplicated across pages.
        assert sorted(collected) == sorted(b_ids), (
            f"reachable set {sorted(collected)!r} != complete B set {sorted(b_ids)!r}"
        )
        assert authority.calls == [(PROJECT_B, "inspect")] * (page_index + 1)
        _assert_authority_precedes_first_read(timeline, "B-envelope paginated job.list")
    finally:
        store.close()


# ---------------------------------------------------------------------------
# (2) Explicit list filter naming A under an outer B envelope: must never
#     serve A data.  Refusal OR clamping to the envelope is implementer's
#     choice; serving A rows under either outcome shape is the forbidden defect.
# ---------------------------------------------------------------------------

def test_t35ja_job_list_explicit_filter_A_under_B_envelope_never_returns_A() -> None:
    """PROPOSED CONTRACT (RED vs current source): B envelope (B-inspect only)
    plus ``filter.project_id = A``.  The caller is NOT authorized for A, so the
    foreign A job's id and sentinel must be absent from the entire outcome
    (refusal payload or success payload alike).  If the response is a success,
    it must be the envelope's own data — the B job present — never the
    filtered-away A rows."""
    foreign_marker = "T35JA-RES-FILTERCLASH-4b7d"
    store = OperationStore(":memory:")
    try:
        foreign_job = _create_synthetic_job(
            store, project_id=PROJECT_A, tag="clash-a", result_marker=foreign_marker
        )
        own_job = _create_synthetic_job(store, project_id=PROJECT_B, tag="clash-b")
        timeline: list[tuple[str, ...]] = []
        _install_read_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_B: {"inspect"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        exc, response = _attempt(
            lambda: _canonical_route(
                receiver,
                "job.list",
                {"filter": {"project_id": PROJECT_A}, "offset": 0, "limit": 10},
                PROJECT_B,
            )
        )
        blob = _serialized(exc, response)
        assert foreign_job not in blob, "conflicting filter: foreign A job id disclosed"
        assert foreign_marker not in blob, "conflicting filter: foreign A sentinel disclosed"
        if exc is None and isinstance(response, dict) and response.get("success") is True:
            # A success here may only ever be envelope-B data (a refusal is an
            # equally acceptable outcome — asserted above by non-disclosure).
            listed_ids = [row["job_id"] for row in response["data"]["jobs"]]
            assert own_job in listed_ids, (
                f"conflicting filter: B-envelope success must still serve B's own jobs: {listed_ids!r}"
            )
        if any(entry[0] in ("list_jobs", "job_row", "events") for entry in timeline):
            _assert_authority_precedes_first_read(timeline, "filter-A-under-B-envelope job.list")
    finally:
        store.close()


# ---------------------------------------------------------------------------
# (3) Positive control: session.start/connect-shaped synthetic jobs carrying
#     ONLY a top-level durable ``project_id`` (job/operation consistent via
#     begin 436-446) must read normally under the authorized project — the
#     proposed gate must accept that binding shape, not just
#     ``execution.project_id``; blanket denial cannot pass.
# ---------------------------------------------------------------------------

def test_t35ja_top_level_project_id_only_job_log_authorized_bound_project_readable() -> None:
    """PROPOSED CONTRACT (RED vs current source): a fresh synthetic job whose
    durable binding is ONLY top-level ``project_id`` (same dict in jobs and
    operations rows) is served on canonical ``job.log`` under the authorized
    matching envelope with the exact sentinel events and the authority call
    ordered before the job/events reads."""
    events = ("T35JA-EVT-TOPLEVELLOG-71c2", "T35JA-EVT-TOPLEVELLOG2-8e05")
    store = OperationStore(":memory:")
    try:
        job_id = _create_top_level_project_bound_job(
            store, project_id=PROJECT_A, tag="toplevel-log", event_markers=events
        )
        timeline: list[tuple[str, ...]] = []
        _install_read_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_A: {"inspect"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        response = _canonical_route(receiver, "job.log", {"job_id": job_id}, PROJECT_A)
        assert response.get("success") is True
        assert response["data"]["job_id"] == job_id
        assert [event["metadata"]["marker"] for event in response["data"]["events"]] == list(events)
        assert authority.calls == [(PROJECT_A, "inspect")]
        assert timeline[0] == ("authority", PROJECT_A, "inspect")
        # The current bounded log route reads the job row through the
        # project-bound ``job_log_read_snapshot`` binding (spy "binding")
        # rather than the unbound ``store.job`` spy; the binding snapshot
        # is the durable job/operation row read that must precede the page.
        assert (timeline.count(("binding", job_id))) >= 1
        assert [entry for entry in timeline if entry[0] == "events"] == [("events", job_id)]
        assert timeline.index(("binding", job_id)) < timeline.index(("events", job_id))
    finally:
        store.close()


def test_t35ja_top_level_project_id_only_job_result_authorized_bound_project_readable() -> None:
    """PROPOSED CONTRACT (RED vs current source): same top-level-only binding
    on canonical ``job.result`` — the authorized matching envelope must receive
    the exact sentinel result; a gate that only recognizes
    ``execution.project_id`` would wrongly fail this closed job."""
    result_marker = "T35JA-RES-TOPLEVELGAMMA-9fb4"
    store = OperationStore(":memory:")
    try:
        job_id = _create_top_level_project_bound_job(
            store, project_id=PROJECT_A, tag="toplevel-result", result_marker=result_marker
        )
        timeline: list[tuple[str, ...]] = []
        _install_read_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_A: {"inspect"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        response = _canonical_route(receiver, "job.result", {"job_id": job_id}, PROJECT_A)
        assert response.get("success") is True
        assert response["data"]["job_id"] == job_id
        assert response["data"]["result"]["data"]["marker"] == result_marker
        assert authority.calls == [(PROJECT_A, "inspect")]
        assert timeline[0] == ("authority", PROJECT_A, "inspect")
        assert ("job_row", job_id) in timeline[1:]
    finally:
        store.close()


# ---------------------------------------------------------------------------
# (4) Divergent durable binding: a synthetic job whose JOBS-row metadata was
#     later updated to B (update_job merges the jobs row only, 2031-2042)
#     while its OPERATIONS-row metadata still says A (begin 436-446).  With no
#     trustworthy job/operation agreement, neither A nor B project listings may
#     disclose it — while each project's own consistent jobs remain visible, so
#     a blanket list denial cannot pass.
# ---------------------------------------------------------------------------

def _run_divergent_projection(envelope_project: str, own_tag: str) -> None:
    store = OperationStore(":memory:")
    try:
        # Consistent by construction, then synthetically diverged: jobs row
        # metadata becomes B, operations row metadata stays A.
        divergent_job = _create_top_level_project_bound_job(
            store, project_id=PROJECT_A, tag=f"div-{envelope_project[-1]}"
        )
        store.update_job(divergent_job, "RUNNING", {"project_id": PROJECT_B})
        store.update_job(
            divergent_job,
            "SUCCEEDED",
            {"t35_synthetic": True},
            result={"success": True, "data": {"marker": "T35JA-RES-DIVERGENT-3cd8"}},
        )
        own_job = _create_synthetic_job(store, project_id=envelope_project, tag=own_tag)
        foreign = PROJECT_B if envelope_project == PROJECT_A else PROJECT_A
        other_job = _create_synthetic_job(store, project_id=foreign, tag=f"div-other-{own_tag}")

        timeline: list[tuple[str, ...]] = []
        _install_read_spies(store, timeline)
        authority = _FakeProjectAuthority({envelope_project: {"inspect"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        response = _list_page(receiver, envelope_project, offset=0, limit=10)
        assert response.get("success") is True, (
            f"{envelope_project} envelope listing divergent-binding scenario must still serve its own jobs: {response!r}"
        )
        listed_ids = [row["job_id"] for row in response["data"]["jobs"]]
        assert own_job in listed_ids, f"{envelope_project}: own consistent job missing: {listed_ids!r}"
        assert other_job not in listed_ids
        blob = _serialized(None, response)
        assert divergent_job not in blob, (
            f"{envelope_project}: job/operation-divergent record disclosed under this project"
        )
        assert "T35JA-RES-DIVERGENT-3cd8" not in blob, (
            f"{envelope_project}: divergent-record sentinel disclosed under this project"
        )
        assert other_job not in blob
        assert authority.calls == [(envelope_project, "inspect")]
        _assert_authority_precedes_first_read(timeline, f"divergent scan under {envelope_project}")
    finally:
        store.close()


def test_t35ja_job_metadata_diverged_from_operation_excluded_under_project_A() -> None:
    """PROPOSED CONTRACT (RED vs current source): synthetic record whose job-row
    metadata says B while its operation-row metadata says A must NOT be served
    under an A envelope listing (an operation-derived listing would follow the
    stale A binding), while A's own consistent job stays visible."""
    _run_divergent_projection(PROJECT_A, "div-own-a")


def test_t35ja_job_metadata_diverged_from_operation_excluded_under_project_B() -> None:
    """PROPOSED CONTRACT (RED vs current source): the same divergent record
    must NOT be served under a B envelope either — the OR filter
    (2160-2166) currently matches the jobs row's B claim and leaks it, exactly
    the defect this control catches — while B's own consistent job stays
    visible."""
    _run_divergent_projection(PROJECT_B, "div-own-b")
