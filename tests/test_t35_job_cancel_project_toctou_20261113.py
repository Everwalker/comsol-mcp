"""T35 — write-time project-binding race (TOCTOU) regression for QUEUED ``job.cancel``.

This file extends ``tests/test_t35_job_control_project_scope_20261112.py`` from
"the envelope project must match the DURABLE binding the route read" to the
narrower, harder question: what happens when the binding changes AFTER the
route's pre-check read but BEFORE the real durable cancel write.  A
task-owned harness flips this test's own job-side project binding A→B at the
exact boundary of the real ``OperationStore.cancel_queued`` call (the
operation-side metadata stays A), records that the flip really applied, and
then requires the real cancel to REFUSE: no job/operation CANCELLED, no
event, no result, no metadata change beyond the single deliberately injected
job-side difference.

Static basis (live sources at authoring time, all re-verified in-file):
- ``ControlDaemon._dispatch_admitted`` (1033) routes canonical
  ``job.cancel`` (``CACHED_JOB_ACTIONS``, 1083-1084) and legacy ordinary
  ``job_cancel`` (no ``force_stop``, falls to the execution-blind
  ``self._control_read(operation, arguments)`` at 1093) into the same real
  body: the ``{"job_cancel", "job.cancel"}`` branch of ``_control_read``
  (13382-13389) calls ``_cancel_job`` (13412-13526).
- ``_cancel_job`` reads the durable row once at 13416 (status decided at
  13421), then for QUEUED calls the REAL ``store.cancel_queued`` at 13463.
  Between those two points sits the window this file weaponises.  Today
  NOTHING re-checks the project binding at the write boundary:
  ``OperationStore.cancel_queued`` (2351-23411 region 2351-2411) CAS-guards
  only ``status='QUEUED'`` (2365-2368, 2389-2394) — no metadata read, no
  binding comparison, no authority.
- A refused ``cancel_queued`` must ALSO not be laundered through the
  status-after-read fallbacks: the re-read branch at 13476-13489
  (TERMINAL_NOOP via post-read) and the 13495-13525 UNSUPPORTED_NATIVE_CANCEL
  tail (``add_event`` + ``update_job`` writes).  Any of those changes durable
  state, and the before/after read-back below forbids them.
- ``consistent_project_id`` (``comsol_mcp/_job_project_scope.py``) is the
  unintegrated pure helper that would classify the post-flip record as
  UNBOUND (job claims [B], operation claims [A] → disagreement → ``None``);
  this file pins the observable refusal, not the implementing mechanism.
- Store facts used by the harness: ``begin`` (408-457) writes the SAME
  metadata to both rows (so A-binding is durable on both sides at setup);
  ``update_job`` (2005-2065) merges jobs-row metadata top-level keys only
  (2038) and with ``result=None`` leaves the operations result column
  untouched (2055-2061); ``_job`` (5636-5642) via ``get_operation``/``_op``
  (5629-5634) materializes the nested operation row INCLUDING its durable
  metadata, so one ``store.job`` read verifies both sides.

Scope and honesty limits:
- This proves ONLY a MANUALLY INJECTED, same-process, same-thread, fully
  deterministic interleaving at one call site.  It does not spawn threads or
  processes, does not exercise SQLite cross-connection contention, and is
  NOT a claim that a natural cross-process TOCTOU window is reachable in
  production.  It is a regression pin: once a write-time binding re-check is
  implemented, it must sit INSIDE or strictly before the real
  ``cancel_queued``, not merely at the pre-check read.
- The fake per-project authority is inherited from the sibling file and
  models the dispatcher gate only; a supplied envelope project ID is not an
  authenticated principal.  Gate ORDERING on these routes is pinned by the
  sibling file; this file deliberately does NOT require an authority call, so
  the positive controls are GREEN today and a blanket denial cannot masquerade
  as the proposed behaviour.
- All projects/jobs/events/results are unique synthetic literals in a fresh
  ``OperationStore(':memory:')`` per test; jobs are task-created and ONLY
  ever QUEUED (until a positive-control cancel).  No UNKNOWN/RECONCILING
  jobs, no ``force_stop`` (never sent; refusals additionally asserted free of
  force-stop semantics), no Worker, daemon, process, service, SSH, endpoint,
  credential, model, network, real COMSOL, disk DB or old resource is touched.
  The only writes are durable ':memory:' rows made through the real
  ``OperationStore`` paths.
- Real components used unchanged: the real unbound ``_dispatch_admitted``
  chain (via the sibling inert receiver), ``_control_read``, ``_cancel_job``,
  and ``OperationStore`` begin/add_event/update_job/cancel_queued/job/events.
  The harness wrapper is installed on the sibling's already-installed
  cancel spy; it delegates to the real code on every call and never replaces
  a return value.  Its own flip write goes through the REAL ``update_job``
  captured BEFORE spy installation, tagged distinctly in the shared timeline,
  so harness writes can never masquerade as route writes.
- Expected status: the two positive controls pass today; the two TOCTOU
  refusals are RED today — the first failing assertion in each is the
  ``cancel_queued`` refusal pin, because today the real
  ``cancel_queued`` returns ``(True, "CANCELLED")`` for the post-flip record.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from comsol_mcp._execution_contract import ExecutionContractError  # noqa: E402
from comsol_mcp._operation_store import OperationStore  # noqa: E402

from tests.test_t35_job_control_project_scope_20261112 import (  # noqa: E402
    DENIAL_CODE,
    PROJECT_A,
    PROJECT_B,
    _FakeProjectAuthority,
    _InertDispatchReceiver,
    _assert_refused_without_sentinel,
    _attempt,
    _canonical_route,
    _create_queued_job,
    _install_store_spies,
    _legacy_route,
    _snapshot,
)

FLIP_ENTRY = "harness_flip"
ROUTE_WRITE_KINDS = ("add_event", "update_job")


def _toc_snapshot(store: OperationStore, job_id: str) -> dict[str, Any]:
    """Sibling durable snapshot PLUS the nested durable operation metadata.

    ``_job``/``_op`` (5629-5642) materialize the operation row's metadata, so
    the flip can be verified on BOTH sides through public store reads.
    """
    snap = _snapshot(store, job_id)
    job_row = store.job(job_id)
    assert job_row is not None and job_row["operation"] is not None
    snap["operation_metadata"] = job_row["operation"]["metadata"]
    return snap


def _install_pre_cancel_binding_flip(
    store: OperationStore,
    timeline: list[tuple[str, ...]],
    *,
    job_id: str,
    real_update_job: Any,
    real_job_read: Any,
    flips: dict[str, Any],
) -> None:
    """Deterministic same-process interleaving at the real ``cancel_queued`` boundary.

    When the route calls ``store.cancel_queued`` for ``job_id`` and the
    harness is armed, the flip runs FIRST: the jobs-row
    ``metadata.execution.project_id`` is moved A→B through the real
    ``update_job`` captured before spy installation (the operations-row
    metadata stays A), then both sides are read back and recorded.  Only then
    is the spy — and hence the REAL ``cancel_queued`` — invoked, so the
    injected interleaving sits exactly after the ``_cancel_job`` pre-check
    read (13416) and before the durable cancel write (13463/2388-2405).
    """
    spy_cancel_queued = store.cancel_queued

    def flipping_cancel_queued(cancel_job_id: str, reason: str = "cancelled by user",
                               **kwargs: Any) -> Any:
        if cancel_job_id == job_id and flips.get("armed") is True:
            real_update_job(cancel_job_id, "QUEUED", {"execution": {"project_id": PROJECT_B}})
            row = real_job_read(cancel_job_id)
            assert row is not None and row["operation"] is not None, (
                "T35TOC harness flip lost its own durable job row"
            )
            flips["job_project_after_flip"] = (row["metadata"].get("execution") or {}).get("project_id")
            flips["operation_project_after_flip"] = (
                row["operation"]["metadata"].get("execution") or {}
            ).get("project_id")
            flips["injected"] = (
                flips["job_project_after_flip"] == PROJECT_B
                and flips["operation_project_after_flip"] == PROJECT_A
            )
            timeline.append((
                FLIP_ENTRY, cancel_job_id,
                str(flips["job_project_after_flip"]), str(flips["operation_project_after_flip"]),
            ))
        flips["cancel_called"] = True
        # Forward the strict binding kwargs (project_id/enforce_project_
        # binding) so the real write transaction keeps its own re-check.
        outcome = spy_cancel_queued(cancel_job_id, reason=reason, **kwargs)
        flips["cancel_result"] = (outcome[0], outcome[1])
        return outcome

    store.cancel_queued = flipping_cancel_queued  # type: ignore[method-assign]


def _first_index(timeline: list[tuple[str, ...]], kind: str) -> int | None:
    return next((i for i, entry in enumerate(timeline) if entry[0] == kind), None)


def _setup_store_with_flip_capable_spies(
    store: OperationStore, timeline: list[tuple[str, ...]], *, job_id: str, armed: bool,
) -> dict[str, Any]:
    """Sibling spies first, then the flip wrapper; harness writes bypass the spies."""
    real_update_job = store.update_job
    real_job_read = store.job
    _install_store_spies(store, timeline)
    flips: dict[str, Any] = {"armed": armed}
    _install_pre_cancel_binding_flip(
        store, timeline, job_id=job_id,
        real_update_job=real_update_job, real_job_read=real_job_read, flips=flips,
    )
    return flips


def _assert_injection_recorded(flips: dict[str, Any], timeline: list[tuple[str, ...]], label: str) -> None:
    """The interleaving really happened, exactly once, in the forced order.

    Order on the shared timeline: route pre-check read (``job_row``) <
    harness flip < real ``cancel_queued`` call.
    """
    assert flips.get("injected") is True, (
        f"{label}: harness flip to project B was not observably applied: {flips!r}"
    )
    assert flips.get("cancel_called") is True, f"{label}: the real cancel_queued was never reached"
    assert _first_index(timeline, FLIP_ENTRY) is not None, f"{label}: flip missing from timeline"
    read_at = _first_index(timeline, "job_row")
    flip_at = _first_index(timeline, FLIP_ENTRY)
    cancel_at = _first_index(timeline, "cancel_queued")
    assert read_at is not None and flip_at is not None and cancel_at is not None, (
        f"{label}: expected pre-check read, flip and cancel call: {timeline!r}"
    )
    assert read_at < flip_at < cancel_at, (
        f"{label}: interleaving out of order: {timeline!r}"
    )
    cancel_calls = [entry for entry in timeline if entry[0] == "cancel_queued"]
    assert len(cancel_calls) == 1, f"{label}: exactly one real cancel call expected: {timeline!r}"


def _assert_real_cancel_refused(flips: dict[str, Any], label: str) -> None:
    """PROPOSED CONTRACT (RED today): ``cancel_queued`` itself must refuse the
    job whose binding went A→B after the pre-check read.  Today it returns
    ``(True, "CANCELLED")`` — the first failing assertion of these tests."""
    assert flips["cancel_result"][0] is False, (
        f"{label}: the real cancel_queued accepted the post-flip record instead of "
        f"refusing: {flips['cancel_result']!r}"
    )


def _assert_durable_state_intact_except_flip(
    before: dict[str, Any], after: dict[str, Any], label: str
) -> None:
    """State is identical before/after EXCEPT the one deliberately injected diff.

    The sole permitted difference is ``job_metadata.execution.project_id``
    A→B — the harness's own flip.  Everything else (statuses, finished_at,
    durable operation result, events, operation metadata) must be byte-identical,
    which also forbids laundering through the post-read fallbacks at
    13476-13489 or the UNSUPPORTED_NATIVE_CANCEL writes at 13495-13525.
    """
    expected = dict(before)
    expected["job_metadata"] = {
        **before["job_metadata"], "execution": {"project_id": PROJECT_B},
    }
    assert after == expected, (
        f"{label}: durable state changed beyond the single injected job-metadata "
        f"project flip: before={before!r} after={after!r}"
    )
    assert after["job_status"] == "QUEUED" and after["operation_status"] == "QUEUED", (
        f"{label}: the refused cancel left job/operation in {after['job_status']!r}/"
        f"{after['operation_status']!r}, expected QUEUED/QUEUED"
    )
    assert after["finished_at"] is None, f"{label}: refused cancel set finished_at"
    assert after["events"] == before["events"], f"{label}: refused cancel changed events"
    assert after["operation_result"] == before["operation_result"] is not None or (
        after["operation_result"] == before["operation_result"]
    ), f"{label}: refused cancel changed the durable operation result"


def _assert_no_extra_route_writes(timeline: list[tuple[str, ...]], label: str) -> None:
    """Besides the (required-to-be-refused) single cancel call, the route wrote nothing."""
    extra = [entry for entry in timeline if entry[0] in ROUTE_WRITE_KINDS]
    assert not extra, f"{label}: route performed durable writes besides the cancel attempt: {extra!r}"


def _assert_fake_authority_grant_is_live() -> None:
    """Initial-permission sanity: the fake authority is neither a no-op nor a
    blanket denial (prevents 'everything refuses' from passing as correct)."""
    probe = _FakeProjectAuthority({PROJECT_A: {"host_control"}})
    probe.authorize_operation(PROJECT_A, "host_control")  # must NOT raise
    err, _ = _attempt(lambda: {"probe": probe.authorize_operation(PROJECT_A, "compute")})
    assert isinstance(err, ExecutionContractError) and err.code == DENIAL_CODE, (
        "fake authority must deny an ungranted action for the granted project"
    )
    err_b, _ = _attempt(lambda: {"probe": probe.authorize_operation(PROJECT_B, "host_control")})
    assert isinstance(err_b, ExecutionContractError) and err_b.code == DENIAL_CODE, (
        "fake authority must be strictly per-project"
    )


# ---------------------------------------------------------------------------
# Canonical ``job.cancel`` (CACHED_JOB_ACTIONS, outer 1083-1084 → impl →
# _control_read 13382-13389 → _cancel_job).  Ordinary only; force_stop never sent.
# ---------------------------------------------------------------------------

def test_t35toc_canonical_positive_control_no_flip_cancels_and_authority_grant_is_live() -> None:
    """POSITIVE CONTROL (GREEN today): with the flip harness installed but DISARMED,
    an A envelope plus an A-bound QUEUED job still completes the REAL
    QUEUED_ABORT cancel (CANCELLED job/operation, OPERATION_CANCELLED durable
    result, CANCELLED event) and no flip entry ever appears.  This keeps the
    TOCTOU refusals below from being satisfied by a blanket failure.  Gate
    ordering on this route is deliberately NOT asserted here — the sibling
    file pins it; this file isolates the write-time binding window."""
    marker = "T35TOC-RES-CANPOS-9c41"
    store = OperationStore(":memory:")
    try:
        job_id = _create_queued_job(
            store, project_id=PROJECT_A, tag="toc-can-pos", content_marker=marker,
            event_markers=("T35TOC-EVT-CANPOS-9c41",), result_marker=marker,
        )
        before = _toc_snapshot(store, job_id)
        assert before["job_status"] == "QUEUED" and before["operation_status"] == "QUEUED"
        timeline: list[tuple[str, ...]] = []
        flips = _setup_store_with_flip_capable_spies(
            store, timeline, job_id=job_id, armed=False,
        )
        authority = _FakeProjectAuthority({PROJECT_A: {"host_control"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        response = _canonical_route(
            receiver, "job.cancel",
            {"job_id": job_id, "reason": "synthetic t35 toctou positive cancel"}, PROJECT_A,
        )
        assert response.get("success") is True
        data = response["data"]
        assert data["status"] == "CANCELLED"
        assert data["cancel_accepted"] is True
        assert data["mode"] == "QUEUED_ABORT"
        assert data["engine_stopped"] is False and data["engine_dispatched"] is False

        assert [entry for entry in timeline if entry[0] == "cancel_queued"] == [
            ("cancel_queued", job_id)
        ]
        assert _first_index(timeline, FLIP_ENTRY) is None, (
            "disarmed harness must not inject: "
            f"{timeline!r}"
        )
        assert flips.get("injected") is not True

        after = _toc_snapshot(store, job_id)
        assert after["job_status"] == "CANCELLED" and after["operation_status"] == "CANCELLED"
        assert after["finished_at"] is not None
        assert after["operation_result"]["error"]["code"] == "OPERATION_CANCELLED"
        assert any(
            event["event"] == "CANCELLED" and event["metadata"].get("mode") == "QUEUED_ABORT"
            for event in after["events"]
        )
        # No flip touched the binding: both sides still name A.
        assert after["job_metadata"]["execution"]["project_id"] == PROJECT_A
        assert after["operation_metadata"]["execution"]["project_id"] == PROJECT_A
        _assert_fake_authority_grant_is_live()
    finally:
        store.close()


def test_t35toc_canonical_binding_flips_A_to_B_after_precheck_cancel_must_refuse() -> None:
    """PROPOSED CONTRACT (RED today): authority for A is granted (a refusal must
    therefore come from the write-time binding check, not missing permission);
    the pre-check read at 13416 sees A; the harness flips the jobs-row
    execution project A→B immediately before the REAL ``cancel_queued`` while
    the operations row keeps A; the real cancel must refuse, and the durable
    record must stay QUEUED with identical events/result — the only permitted
    difference is the injected job-side flip itself."""
    marker = "T35TOC-RES-CANFLIP-5b7d"
    store = OperationStore(":memory:")
    try:
        job_id = _create_queued_job(
            store, project_id=PROJECT_A, tag="toc-can-flip", content_marker=marker,
            event_markers=("T35TOC-EVT-CANFLIP-5b7d",), result_marker=marker,
        )
        before = _toc_snapshot(store, job_id)
        assert before["operation_result"]["data"]["marker"] == marker
        timeline: list[tuple[str, ...]] = []
        flips = _setup_store_with_flip_capable_spies(
            store, timeline, job_id=job_id, armed=True,
        )
        authority = _FakeProjectAuthority({PROJECT_A: {"host_control"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        exc, response = _attempt(
            lambda: _canonical_route(
                receiver, "job.cancel",
                {"job_id": job_id, "reason": "synthetic t35 toctou flip cancel"}, PROJECT_A,
            )
        )
        _assert_injection_recorded(flips, timeline, "canonical TOCTOU job.cancel")
        _assert_real_cancel_refused(flips, "canonical TOCTOU job.cancel")
        _assert_refused_without_sentinel(
            exc, response, (marker,),
            label="post-flip canonical job.cancel", deny_no_force=True,
        )
        after = _toc_snapshot(store, job_id)
        _assert_durable_state_intact_except_flip(before, after, "canonical TOCTOU job.cancel")
        _assert_no_extra_route_writes(timeline, "canonical TOCTOU job.cancel")
        # Permission sanity for the implemented-gate future: any authority call
        # that DOES happen must be the granted A host_control and must precede
        # the injected flip (i.e. the refusal below is binding-based, not authz).
        if authority.calls:
            assert authority.calls == [(PROJECT_A, "host_control")]
            authority_at = _first_index(timeline, "authority")
            flip_at = _first_index(timeline, FLIP_ENTRY)
            assert authority_at is not None and flip_at is not None and authority_at < flip_at
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Legacy ``job_cancel`` (no force_stop → execution-blind _control_read at 1093).
# Same window, same contract, so the alias cannot stay an unguarded door.
# ---------------------------------------------------------------------------

def test_t35toc_legacy_positive_control_no_flip_cancels_and_authority_grant_is_live() -> None:
    """POSITIVE CONTROL (GREEN today): the legacy alias with the flip harness
    DISARMED still completes the real QUEUED_ABORT cancel end-to-end, so the
    legacy refusal below cannot be bought with a blanket failure."""
    marker = "T35TOC-RES-LEGPOS-2e88"
    store = OperationStore(":memory:")
    try:
        job_id = _create_queued_job(
            store, project_id=PROJECT_A, tag="toc-leg-pos", content_marker=marker,
            event_markers=("T35TOC-EVT-LEGPOS-2e88",), result_marker=marker,
        )
        before = _toc_snapshot(store, job_id)
        timeline: list[tuple[str, ...]] = []
        flips = _setup_store_with_flip_capable_spies(
            store, timeline, job_id=job_id, armed=False,
        )
        authority = _FakeProjectAuthority({PROJECT_A: {"host_control"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        response = _legacy_route(
            receiver, "job_cancel",
            {"job_id": job_id, "reason": "synthetic t35 toctou legacy positive cancel"},
            envelope_project=PROJECT_A,
        )
        assert response.get("success") is True
        assert response["data"]["status"] == "CANCELLED"
        assert response["data"]["cancel_accepted"] is True
        assert response["data"]["mode"] == "QUEUED_ABORT"

        assert [entry for entry in timeline if entry[0] == "cancel_queued"] == [
            ("cancel_queued", job_id)
        ]
        assert _first_index(timeline, FLIP_ENTRY) is None
        assert flips.get("injected") is not True

        after = _toc_snapshot(store, job_id)
        assert after["job_status"] == "CANCELLED" and after["operation_status"] == "CANCELLED"
        assert after["operation_result"]["error"]["code"] == "OPERATION_CANCELLED"
        assert after["job_metadata"]["execution"]["project_id"] == PROJECT_A
        assert after["operation_metadata"]["execution"]["project_id"] == PROJECT_A
        _assert_fake_authority_grant_is_live()
    finally:
        store.close()


def test_t35toc_legacy_binding_flips_A_to_B_after_precheck_cancel_must_refuse() -> None:
    """PROPOSED CONTRACT (RED today): same injected interleaving on the legacy
    alias — jobs-row execution project flipped A→B immediately before the
    real ``cancel_queued`` (operations row keeps A) — the real cancel must
    refuse and leave the durable record QUEUED with identical events/result;
    only the injected job-side flip may differ.  Proves the same-process
    window is closed on BOTH public names, not just the canonical one."""
    marker = "T35TOC-RES-LEGFLIP-d0a3"
    store = OperationStore(":memory:")
    try:
        job_id = _create_queued_job(
            store, project_id=PROJECT_A, tag="toc-leg-flip", content_marker=marker,
            event_markers=("T35TOC-EVT-LEGFLIP-d0a3",), result_marker=marker,
        )
        before = _toc_snapshot(store, job_id)
        assert before["operation_result"]["data"]["marker"] == marker
        timeline: list[tuple[str, ...]] = []
        flips = _setup_store_with_flip_capable_spies(
            store, timeline, job_id=job_id, armed=True,
        )
        authority = _FakeProjectAuthority({PROJECT_A: {"host_control"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        exc, response = _attempt(
            lambda: _legacy_route(
                receiver, "job_cancel",
                {"job_id": job_id, "reason": "synthetic t35 toctou legacy flip cancel"},
                envelope_project=PROJECT_A,
            )
        )
        _assert_injection_recorded(flips, timeline, "legacy TOCTOU job_cancel")
        _assert_real_cancel_refused(flips, "legacy TOCTOU job_cancel")
        _assert_refused_without_sentinel(
            exc, response, (marker,),
            label="post-flip legacy job_cancel", deny_no_force=True,
        )
        after = _toc_snapshot(store, job_id)
        _assert_durable_state_intact_except_flip(before, after, "legacy TOCTOU job_cancel")
        _assert_no_extra_route_writes(timeline, "legacy TOCTOU job_cancel")
        if authority.calls:
            assert authority.calls == [(PROJECT_A, "host_control")]
            authority_at = _first_index(timeline, "authority")
            flip_at = _first_index(timeline, FLIP_ENTRY)
            assert authority_at is not None and flip_at is not None and authority_at < flip_at
    finally:
        store.close()
