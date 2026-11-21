"""T35 — extra synthetic admission cases for ``job.reconcile`` (plus cancel permission gaps).

PROPOSED CONTRACT, NOT ENDORSED CURRENT BEHAVIOR — and PROVEN RED BY CONSTRUCTION,
NOT BY EXECUTION: the author of this file has NOT run it (no pytest, no import
smoke).  Every test below states inline which assertion is expected to fail
first when someone does run it; the module note at the bottom repeats this.
Nothing in this file is evidence that any gate exists today.

This file adds ONLY the admission cases that ``tests/test_t35_job_control_project_scope_20261112.py``
left uncovered for the job-control routes:

1. ``job.reconcile`` with the outer ``execution.project_id`` ABSENT (canonical
   and legacy routes) — a missing identity must fail closed, not act as a
   wildcard;
2. ``job.reconcile`` on a NEW synthetic QUEUED record with NO usable durable
   project binding anywhere (canonical and legacy routes) — no guessed
   ownership, record untouched;
3. ``job.reconcile`` for an envelope project that holds only ``"inspect"`` —
   the catalog effect for reconcile is STATE_WRITE, so the modeled gate
   ``authorize_operation(project, "project_write")`` must refuse (canonical
   and legacy routes), each test pairing an authorized twin through the real
   QUEUED early-return so a blanket denial cannot pass as correct;
4. ``job.cancel`` permission insufficiency (the task's "必要时" case): an
   envelope project holding ``{"inspect", "project_write"}`` but NOT
   ``"host_control"`` must be refused on both cancel routes — reconcile's
   weaker write grant must not authorize the HOST_CONTROL effect.

Cross-project (A-bound record vs B envelope) and the positive QUEUED_ABORT /
QUEUED-early-return controls are ALREADY pinned by the 20261112 file, which is
imported here (read-only reuse of its private helpers) and deliberately left
unmodified.  This file adds no new cross-project case and reruns nothing.

Static basis (live repo at authoring time 20261113, line numbers verified by
reading the files; ``_control_daemon.py`` is 14242 lines):
- ``CONTROL_READS`` (88-96), ``CACHED_JOB_ACTIONS`` (97-100, includes
  ``job.reconcile`` and ``job.cancel``), ``_SCOPED_CACHED_JOB_READS`` (101,
  EXCLUDES reconcile and cancel) and ``_authorize_cached_job_read`` (125-131,
  requires nonempty outer ``execution.project_id`` then
  ``authorize_operation(project, "inspect")`` — applied only to the scoped
  reads via the gate at 12070-12073).
- ``_dispatch_admitted`` (1033-…): ``timeouts = self._timeouts(execution)``
  at 1076; canonical CACHED_JOB_ACTIONS → ``_dispatch_catalog_job_control``
  at 1083-1084; legacy ``job_reconcile``/``job.reconcile`` →
  ``_dispatch_job_reconcile`` at 1086-1087; legacy cancel ONLY when
  ``arguments["force_stop"] is True`` at 1088-1089 (never sent here);
  execution-blind ``self._control_read(operation, arguments)`` at 1093.  The
  handler tail at 1417-1418 turns a raised ``ExecutionContractError`` (fake
  authority denial included) into an error response via ``_exception``; the
  generic 1419-1421 handler calls ``self._log_exception()`` first, and the
  inert receiver (imported) replaces that with a fail-loud no-I/O sentinel.
- Wrapper ``_dispatch_catalog_job_control`` 5444-5447; impl
  ``_dispatch_catalog_job_control_impl`` 12046-12145: real catalog
  ``validate_call`` at 12049 (reconcile schema accepts exactly ``{"job_id"}``,
  cancel accepts ``job_id``/``reason`` and forbids stray identity fields at
  12050-12056), scoped-read gate 12070-12073 (misses reconcile/cancel),
  ``job.cleanup``'s ``authorize_operation(project_id, "host_control")``
  precedent at 12125, reconcile short-circuit into ``_dispatch_job_reconcile``
  at 12139-12140, force-cancel at 12141-12142 (unreachable here), and the
  execution-blind ``_control_read`` fall-through at 12143-12145 for ordinary
  cancel.
- ``_dispatch_job_reconcile`` 11944-11997: binding-blind ``self.store.job``
  at 11948; any status outside {UNKNOWN, RECONCILING} — every job here is
  QUEUED — early-returns ``{"success": True, "data": job}`` at 11951-11952
  with the WHOLE row (nested operation row and materialized durable result,
  ``OperationStore._job`` 5636-5642); the Worker tail 11953-11997 is
  structurally unreachable for QUEUED and is never exercised.
- ``_control_read`` job_cancel branch 13382-13389 → ``_cancel_job`` 13412-…;
  its QUEUED branch 13462-13475 calls the real
  ``OperationStore.cancel_queued`` (2351-2411: job+operation to CANCELLED,
  OPERATION_CANCELLED durable result, 'CANCELLED' event) and returns the
  QUEUED_ABORT shape.
- ``OperationStore``: ``begin`` (408-457) writes ONE metadata dict to BOTH
  the jobs and the operations row, both status QUEUED; ``job`` (2000-2003);
  ``update_job`` (2005-2065) merges metadata and persists the durable
  operations result without leaving QUEUED; ``add_event`` (2413-2418);
  ``events`` (3518-3526).
- Project-binding primitives exist but are UNWIRED into these routes:
  ``comsol_mcp/_job_project_scope.py::consistent_project_id`` (pure; trusted
  fields are metadata top-level ``project_id`` and ``execution.project_id``
  per side; any ambiguity/absence yields ``None``) and
  ``OperationStore.project_job_read_snapshot`` (2147-2217, "nothing here is
  wired into the daemon").  The fixtures below bind jobs through exactly
  those trusted fields (``begin`` metadata ``execution.project_id`` on both
  rows) and unbound fixtures leave both fields absent, so a conforming gate
  can pair authority with the durable binding without inventing fields.
- Catalog (``comsol_mcp/data/g2/02_ACTION_CATALOG.json``): ``job.cancel``
  entry 11282-11366 — effect HOST_CONTROL, scope project (11287-11288),
  input ``job_id`` required / ``reason`` optional string (11293-11331);
  ``job.reconcile`` entry 11369-11402 — effect STATE_WRITE, scope project
  (11374-11375), input exactly ``job_id`` with ``additionalProperties``
  false (11380-11394).  Following the 20261112 file's modeled gate names:
  cancel → ``authorize_operation(project, "host_control")`` (as
  ``job.cleanup`` already does at 12125), reconcile →
  ``authorize_operation(project, "project_write")``.  Both are real
  ``PROJECT_PERMISSIONS`` names (``comsol_mcp/_project_authority.py``:24).
  The implementing mechanism (dispatcher gate vs re-routing the alias) is
  NOT pinned — only the observable envelope-gated behavior is.

Scope and honesty limits:
- Control-plane boundary specification only, exercised in-process through the
  real unbound ``ControlDaemon._dispatch_admitted`` on the imported inert
  receiver, with the fake per-project authority imported from the 20261112
  file.  A supplied execution-envelope project ID is never an authenticated
  principal; the fake authority models the dispatcher gate only — no MCP
  wire, real grant, or public exploitability claim.
- Every test opens a fresh ``OperationStore(':memory:')`` (closed in
  ``finally``) and creates ONLY task-authored QUEUED records with unique
  T35JX literals.  No real or historical job, no real or synthetic
  UNKNOWN/RECONCILING state, no ``force_stop`` (so ``_dispatch_job_cancel``
  and every force/Worker branch stay unreachable — the receiver does not
  even expose it), no Worker, process, service, SSH, endpoint, credential,
  model, network, file DB or disk state.  Fixture spies (installed AFTER
  fixture writes, from the 20261112 module) replace only instance attributes
  of that same in-memory store and always return the real methods' values.
- Refusals must not leak the synthetic sentinels (checked over the fully
  serialized refusal) and must leave job status, metadata, finished_at, the
  operation status/result and the event list byte-identical before/after
  (read-back snapshots).  Positive twins keep the tests falsifiable in the
  other direction: the authorized reconcile twin must still get the real
  QUEUED early-return row, and the authorized cancel twin must still produce
  the real QUEUED_ABORT durable cancel.
- Deliberately NOT touched: force-stop, UNKNOWN/RECONCILING reconciliation,
  ``job.resume``, ``job.cleanup``, solver dispatch, the 20261112 file itself,
  and the protected earlier read/authority tests.

Expected first failure when run (all eight tests are RED by construction —
today NONE of the four reconcile/cancel routes consults ``project_authority``
or compares any durable binding):
- the four reconcile and two no-binding/no-envelope tests: the first failing
  assertion is the refusal check inside ``_assert_refused_without_sentinel``
  ("expected a refusal"), because ``_dispatch_job_reconcile`` (11951-11952)
  returns ``success True`` with the whole row; the sentinel-leak, snapshot
  and ``_no_store_writes`` checks are never reached (reconcile writes nothing
  even today);
- the two permission-denied cancel tests: same refusal check fires first,
  but note today's harm is worse — the route durably CANCELS the synthetic
  record before any assertion can observe the missing gate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Read-only reuse of the 20261112 job-control file's private helpers (its
# module level is only imports/constants — importing runs no test).  That
# file stays untouched; nothing here re-declares or edits it.
from tests import test_t35_job_control_project_scope_20261112 as _t35jc

from comsol_mcp._operation_store import OperationStore

assert Path(_t35jc.__file__).resolve() == (
    Path(__file__).resolve().parent / "test_t35_job_control_project_scope_20261112.py"
)

_FakeProjectAuthority = _t35jc._FakeProjectAuthority
_InertDispatchReceiver = _t35jc._InertDispatchReceiver
_install_store_spies = _t35jc._install_store_spies
_create_queued_job = _t35jc._create_queued_job
_snapshot = _t35jc._snapshot
_outer_route = _t35jc._outer_route
_canonical_route = _t35jc._canonical_route
_legacy_route = _t35jc._legacy_route
_attempt = _t35jc._attempt
_assert_refused_without_sentinel = _t35jc._assert_refused_without_sentinel
_no_store_writes = _t35jc._no_store_writes

PROJECT_A = "t35jx-proj-A-20261113"


# ---------------------------------------------------------------------------
# (1) Missing outer ``execution.project_id`` — ``job.reconcile``, both routes.
#     Today: canonical reaches the impl (1083-1084 → 12139-12140) and legacy
#     reaches ``_dispatch_job_reconcile`` (1086-1087) regardless of the
#     envelope; both return the whole QUEUED row with ``success True``.  The
#     refusal check in ``_assert_refused_without_sentinel`` is the first
#     assertion to fail.
# ---------------------------------------------------------------------------

def test_t35jx_canonical_reconcile_without_outer_project_identity_fails_closed() -> None:
    """PROPOSED CONTRACT (RED today): canonical ``job.reconcile`` on an
    A-bound QUEUED job with ``execution={}`` (project identity explicitly
    absent) must fail closed — absence is not a wildcard.  Today 12139-12140
    short-circuits into the execution-blind ``_dispatch_job_reconcile`` and
    serves the full row, so the refusal assertion fails first."""
    marker = "T35JX-RES-CANRECNOENV-11a7"
    event_marker = "T35JX-EVT-CANRECNOENV-11a7"
    store = OperationStore(":memory:")
    try:
        job_id = _create_queued_job(
            store, project_id=PROJECT_A, tag="jx-canrec-noenv",
            content_marker=marker, event_markers=(event_marker,), result_marker=marker,
        )
        before = _snapshot(store, job_id)
        assert before["job_status"] == "QUEUED" and before["operation_status"] == "QUEUED"
        timeline: list[tuple[str, ...]] = []
        _install_store_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_A: {"project_write"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        exc, response = _attempt(
            lambda: _outer_route(receiver, "job.reconcile", {"job_id": job_id}, {})
        )
        _assert_refused_without_sentinel(
            exc, response, (marker, event_marker), label="no-envelope canonical job.reconcile"
        )
        after = _snapshot(store, job_id)
        assert after == before, (
            f"no-envelope reconcile changed durable state: before={before!r} after={after!r}"
        )
        _no_store_writes(timeline, "no-envelope canonical job.reconcile")
    finally:
        store.close()


def test_t35jx_legacy_reconcile_without_outer_project_identity_fails_closed() -> None:
    """PROPOSED CONTRACT (RED today): the legacy ``job_reconcile`` alias with
    ``execution={}`` (``_legacy_route`` envelope_project=None) must fail
    closed.  Today outer 1086-1087 routes it straight into
    ``_dispatch_job_reconcile`` envelope-blind; the refusal assertion fails
    first.  Fail-closed-on-absence only — no authenticated-principal claim."""
    marker = "T35JX-RES-LECRECNOENV-22b8"
    event_marker = "T35JX-EVT-LECRECNOENV-22b8"
    store = OperationStore(":memory:")
    try:
        job_id = _create_queued_job(
            store, project_id=PROJECT_A, tag="jx-legrec-noenv",
            content_marker=marker, event_markers=(event_marker,), result_marker=marker,
        )
        before = _snapshot(store, job_id)
        timeline: list[tuple[str, ...]] = []
        _install_store_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_A: {"project_write"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        exc, response = _attempt(
            lambda: _legacy_route(receiver, "job_reconcile", {"job_id": job_id}, envelope_project=None)
        )
        _assert_refused_without_sentinel(
            exc, response, (marker, event_marker), label="no-envelope legacy job_reconcile"
        )
        after = _snapshot(store, job_id)
        assert after == before, (
            f"legacy no-envelope reconcile changed durable state: before={before!r} after={after!r}"
        )
        _no_store_writes(timeline, "no-envelope legacy job_reconcile")
    finally:
        store.close()


# ---------------------------------------------------------------------------
# (2) Unbound QUEUED records — ``job.reconcile``, both routes.  The fixture
#     omits BOTH trusted binding fields (top-level metadata ``project_id`` and
#     metadata ``execution.project_id``) on BOTH durable rows, so
#     ``consistent_project_id`` (unwired helper) would return ``None`` for the
#     pair; no implementation may guess ownership.
# ---------------------------------------------------------------------------

def test_t35jx_canonical_reconcile_unbound_queued_job_fails_closed() -> None:
    """PROPOSED CONTRACT (RED today): an authorized A envelope on a NEW
    synthetic QUEUED record with no usable project binding anywhere must fail
    closed and must not disclose the record's row.  Today 11948 reads the
    binding-blind row and 11951-11952 returns it whole — the refusal
    assertion fails first; the record must stay QUEUED with identical
    events/operation result."""
    marker = "T35JX-RES-CANRECUNBND-33c9"
    event_marker = "T35JX-EVT-CANRECUNBND-33c9"
    store = OperationStore(":memory:")
    try:
        unbound_job = _create_queued_job(
            store, project_id=None, tag="jx-canrec-unbnd",
            content_marker=marker, event_markers=(event_marker,), result_marker=marker,
        )
        before = _snapshot(store, unbound_job)
        assert "execution" not in before["job_metadata"], "fixture must leave both trusted fields absent"
        timeline: list[tuple[str, ...]] = []
        _install_store_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_A: {"project_write"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        exc, response = _attempt(
            lambda: _canonical_route(receiver, "job.reconcile", {"job_id": unbound_job}, PROJECT_A)
        )
        _assert_refused_without_sentinel(
            exc, response, (marker, event_marker), label="unbound canonical job.reconcile"
        )
        after = _snapshot(store, unbound_job)
        assert after == before, f"unbound record changed: before={before!r} after={after!r}"
        _no_store_writes(timeline, "unbound canonical job.reconcile")
    finally:
        store.close()


def test_t35jx_legacy_reconcile_unbound_queued_job_fails_closed() -> None:
    """PROPOSED CONTRACT (RED today): the legacy alias shares the same
    binding-blind QUEUED disclosure; an A envelope over an unbound synthetic
    record must fail closed, leave it QUEUED, and withhold the sentinels.
    The refusal assertion fails first today."""
    marker = "T35JX-RES-LECRECUNBND-44da"
    event_marker = "T35JX-EVT-LECRECUNBND-44da"
    store = OperationStore(":memory:")
    try:
        unbound_job = _create_queued_job(
            store, project_id=None, tag="jx-legrec-unbnd",
            content_marker=marker, event_markers=(event_marker,), result_marker=marker,
        )
        before = _snapshot(store, unbound_job)
        assert "execution" not in before["job_metadata"], "fixture must leave both trusted fields absent"
        timeline: list[tuple[str, ...]] = []
        _install_store_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_A: {"project_write"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        exc, response = _attempt(
            lambda: _legacy_route(
                receiver, "job_reconcile", {"job_id": unbound_job}, envelope_project=PROJECT_A
            )
        )
        _assert_refused_without_sentinel(
            exc, response, (marker, event_marker), label="unbound legacy job_reconcile"
        )
        after = _snapshot(store, unbound_job)
        assert after == before, f"unbound legacy record changed: before={before!r} after={after!r}"
        _no_store_writes(timeline, "unbound legacy job_reconcile")
    finally:
        store.close()


# ---------------------------------------------------------------------------
# (3) Permission insufficiency for reconcile — envelope project A holds ONLY
#     "inspect".  Per the catalog effect split (STATE_WRITE at catalog
#     11374-11375), the modeled gate is authorize_operation(A,
#     "project_write"); an inspect-only grant must be refused even though the
#     binding itself would match.  Each test pairs an authorized twin (same
#     store, same A binding) served through the REAL QUEUED early-return
#     (11951-11952) so a blanket denial cannot pass as correct.
# ---------------------------------------------------------------------------

def test_t35jx_canonical_reconcile_project_A_lacking_project_write_denied() -> None:
    """PROPOSED CONTRACT (RED today): A envelope + A-bound QUEUED job, but
    the fake authority grants A ONLY {"inspect"}.  Reconcile's STATE_WRITE
    effect requires "project_write", so the attempt must be refused with no
    sentinel disclosure and no state change, and the modeled gate must at
    least have been asked for (A, "project_write").  Today no authority is
    consulted at all and the full row is served — the refusal assertion fails
    first; the authorized twin (real QUEUED early-return row) then pins that
    the fixture itself is servable."""
    marker = "T35JX-RES-CANRECNOPRM-55eb"
    event_marker = "T35JX-EVT-CANRECNOPRM-55eb"
    twin_marker = "T35JX-RES-CANRECPRMPOS-56ec"
    store = OperationStore(":memory:")
    try:
        job_id = _create_queued_job(
            store, project_id=PROJECT_A, tag="jx-canrec-noperm",
            content_marker=marker, event_markers=(event_marker,), result_marker=marker,
        )
        twin = _create_queued_job(
            store, project_id=PROJECT_A, tag="jx-canrec-prmpos",
            content_marker=twin_marker, event_markers=(twin_marker,),
        )
        before = _snapshot(store, job_id)
        timeline: list[tuple[str, ...]] = []
        _install_store_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_A: {"inspect"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        exc, response = _attempt(
            lambda: _canonical_route(receiver, "job.reconcile", {"job_id": job_id}, PROJECT_A)
        )
        _assert_refused_without_sentinel(
            exc, response, (marker, event_marker), label="inspect-only canonical job.reconcile"
        )
        after = _snapshot(store, job_id)
        assert after == before, (
            f"inspect-only reconcile changed durable state: before={before!r} after={after!r}"
        )
        _no_store_writes(timeline, "inspect-only canonical job.reconcile")
        # The modeled gate was still asked for the permission the project lacks:
        assert authority.calls == [(PROJECT_A, "project_write")], (
            f"expected a project_write gate call for A, got {authority.calls!r}"
        )

        # Positive twin: same store/binding shape, real project_write grant —
        # the REAL QUEUED early-return row must still be served (20261112
        # style), proving the denial above is permission-driven, not a
        # fixture artifact.
        twin_timeline: list[tuple[str, ...]] = []
        twin_authority = _FakeProjectAuthority({PROJECT_A: {"project_write"}}, timeline=twin_timeline)
        twin_receiver = _InertDispatchReceiver(store, twin_authority)
        twin_response = _canonical_route(twin_receiver, "job.reconcile", {"job_id": twin}, PROJECT_A)
        assert twin_response.get("success") is True
        assert twin_response["data"]["job_id"] == twin
        assert twin_response["data"]["status"] == "QUEUED"
        assert twin_response["data"]["metadata"]["content_marker"] == twin_marker
        # Store spies remain bound to the original timeline, not the twin
        # authority's separate call timeline.
        _no_store_writes(timeline, "authorized job.reconcile twin")
    finally:
        store.close()


def test_t35jx_legacy_reconcile_project_A_lacking_project_write_denied() -> None:
    """PROPOSED CONTRACT (RED today): the legacy ``job_reconcile`` alias must
    share the same permission gate — A envelope, A only {"inspect"} → refusal
    without sentinel leak and without state change, with the modeled
    (A, "project_write") call recorded.  Today 1086-1087 never consults any
    authority, so the refusal assertion fails first; the authorized twin
    keeps the test falsifiable."""
    marker = "T35JX-RES-LECRECNOPRM-67fd"
    event_marker = "T35JX-EVT-LECRECNOPRM-67fd"
    twin_marker = "T35JX-RES-LECRECPRMPOS-68fe"
    store = OperationStore(":memory:")
    try:
        job_id = _create_queued_job(
            store, project_id=PROJECT_A, tag="jx-legrec-noperm",
            content_marker=marker, event_markers=(event_marker,), result_marker=marker,
        )
        twin = _create_queued_job(
            store, project_id=PROJECT_A, tag="jx-legrec-prmpos",
            content_marker=twin_marker, event_markers=(twin_marker,),
        )
        before = _snapshot(store, job_id)
        timeline: list[tuple[str, ...]] = []
        _install_store_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_A: {"inspect"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        exc, response = _attempt(
            lambda: _legacy_route(
                receiver, "job_reconcile", {"job_id": job_id}, envelope_project=PROJECT_A
            )
        )
        _assert_refused_without_sentinel(
            exc, response, (marker, event_marker), label="inspect-only legacy job_reconcile"
        )
        after = _snapshot(store, job_id)
        assert after == before, (
            f"inspect-only legacy reconcile changed durable state: before={before!r} after={after!r}"
        )
        _no_store_writes(timeline, "inspect-only legacy job_reconcile")
        assert authority.calls == [(PROJECT_A, "project_write")], (
            f"expected a project_write gate call for A, got {authority.calls!r}"
        )

        twin_timeline: list[tuple[str, ...]] = []
        twin_authority = _FakeProjectAuthority({PROJECT_A: {"project_write"}}, timeline=twin_timeline)
        twin_receiver = _InertDispatchReceiver(store, twin_authority)
        twin_response = _legacy_route(
            twin_receiver, "job_reconcile", {"job_id": twin}, envelope_project=PROJECT_A
        )
        assert twin_response.get("success") is True
        assert twin_response["data"]["job_id"] == twin
        assert twin_response["data"]["status"] == "QUEUED"
        _no_store_writes(timeline, "authorized legacy job_reconcile twin")
    finally:
        store.close()


# ---------------------------------------------------------------------------
# (4) Permission insufficiency for ordinary (never force_stop) ``job.cancel``,
#     both routes.  The catalog effect is HOST_CONTROL (11287-11288), so the
#     modeled gate is authorize_operation(A, "host_control") — even the
#     stronger-than-inspect pair {"inspect", "project_write"} (enough for the
#     proposed reconcile gate) must NOT authorize a cancel.  Today the
#     fall-throughs at 12143-12145 / 1093 cancel the record outright, which
#     is exactly the harm pinned here; ``force_stop`` is never sent, so the
#     refusal must also not introduce force-stop semantics.
# ---------------------------------------------------------------------------

def test_t35jx_canonical_cancel_project_A_lacking_host_control_denied() -> None:
    """PROPOSED CONTRACT (RED today): canonical ``job.cancel``, A envelope,
    A-bound QUEUED job, but A holds ONLY {"inspect", "project_write"}.  The
    cancel effect must be refused (no sentinel leak, record untouched, no
    durable write, no force-stop vocabulary), with the modeled
    (A, "host_control") call recorded.  Today 12143-12145 runs the real
    QUEUED_ABORT cancel (13462-13475 → cancel_queued 2351-2411) — the refusal
    assertion fails first, after the record has already been mutated.  The
    host_control twin then proves the route and fixture really do cancel."""
    marker = "T35JX-RES-CANCANNOHC-79ab"
    event_marker = "T35JX-EVT-CANCANNOHC-79ab"
    twin_marker = "T35JX-RES-CANCANHCPOS-80bc"
    store = OperationStore(":memory:")
    try:
        job_id = _create_queued_job(
            store, project_id=PROJECT_A, tag="jx-cancan-nohc",
            content_marker=marker, event_markers=(event_marker,), result_marker=marker,
        )
        twin = _create_queued_job(
            store, project_id=PROJECT_A, tag="jx-cancan-hcpos",
            content_marker=twin_marker, event_markers=(twin_marker,),
        )
        before = _snapshot(store, job_id)
        timeline: list[tuple[str, ...]] = []
        _install_store_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_A: {"inspect", "project_write"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        exc, response = _attempt(
            lambda: _canonical_route(
                receiver, "job.cancel",
                {"job_id": job_id, "reason": "synthetic t35jx insufficient-permission cancel"},
                PROJECT_A,
            )
        )
        _assert_refused_without_sentinel(
            exc, response, (marker,),
            label="host_control-lacking canonical job.cancel", deny_no_force=True,
        )
        after = _snapshot(store, job_id)
        assert after == before, (
            f"insufficient-permission cancel changed A-bound durable state: before={before!r} after={after!r}"
        )
        _no_store_writes(timeline, "host_control-lacking canonical job.cancel")
        assert authority.calls == [(PROJECT_A, "host_control")], (
            f"expected a host_control gate call for A, got {authority.calls!r}"
        )

        # Positive twin: the SAME store with a real host_control grant still
        # performs the real durable QUEUED_ABORT cancel (in ':memory:' only).
        twin_timeline: list[tuple[str, ...]] = []
        twin_authority = _FakeProjectAuthority({PROJECT_A: {"host_control"}}, timeline=twin_timeline)
        twin_receiver = _InertDispatchReceiver(store, twin_authority)
        twin_response = _canonical_route(
            twin_receiver, "job.cancel",
            {"job_id": twin, "reason": "synthetic t35jx authorized cancel twin"}, PROJECT_A,
        )
        assert twin_response.get("success") is True
        assert twin_response["data"]["status"] == "CANCELLED"
        assert twin_response["data"]["cancel_accepted"] is True
        assert twin_response["data"]["mode"] == "QUEUED_ABORT"
        twin_after = _snapshot(store, twin)
        assert twin_after["job_status"] == "CANCELLED"
        assert twin_after["operation_status"] == "CANCELLED"
    finally:
        store.close()


def test_t35jx_legacy_cancel_project_A_lacking_host_control_denied() -> None:
    """PROPOSED CONTRACT (RED today): the legacy ordinary ``job_cancel``
    alias (execution-blind ``_control_read`` at 1093 → 13382-13389 →
    ``_cancel_job``) must honour the same host_control gate — A holds ONLY
    {"inspect", "project_write"} → refusal, untouched record, no durable
    write, no force-stop vocabulary, modeled (A, "host_control") call
    recorded.  Refusal assertion fails first today; the host_control twin
    keeps the legacy route's real QUEUED_ABORT semantics pinned."""
    marker = "T35JX-RES-LEGCANNOHC-8bcd"
    event_marker = "T35JX-EVT-LEGCANNOHC-8bcd"
    twin_marker = "T35JX-RES-LEGCANHCPOS-9ace"
    store = OperationStore(":memory:")
    try:
        job_id = _create_queued_job(
            store, project_id=PROJECT_A, tag="jx-legcan-nohc",
            content_marker=marker, event_markers=(event_marker,), result_marker=marker,
        )
        twin = _create_queued_job(
            store, project_id=PROJECT_A, tag="jx-legcan-hcpos",
            content_marker=twin_marker, event_markers=(twin_marker,),
        )
        before = _snapshot(store, job_id)
        timeline: list[tuple[str, ...]] = []
        _install_store_spies(store, timeline)
        authority = _FakeProjectAuthority({PROJECT_A: {"inspect", "project_write"}}, timeline=timeline)
        receiver = _InertDispatchReceiver(store, authority)

        exc, response = _attempt(
            lambda: _legacy_route(
                receiver, "job_cancel",
                {"job_id": job_id, "reason": "synthetic t35jx legacy insufficient-permission cancel"},
                envelope_project=PROJECT_A,
            )
        )
        _assert_refused_without_sentinel(
            exc, response, (marker,),
            label="host_control-lacking legacy job_cancel", deny_no_force=True,
        )
        after = _snapshot(store, job_id)
        assert after == before, (
            f"legacy insufficient-permission cancel changed A-bound durable state: before={before!r} after={after!r}"
        )
        _no_store_writes(timeline, "host_control-lacking legacy job_cancel")
        assert authority.calls == [(PROJECT_A, "host_control")], (
            f"expected a host_control gate call for A, got {authority.calls!r}"
        )

        twin_timeline: list[tuple[str, ...]] = []
        twin_authority = _FakeProjectAuthority({PROJECT_A: {"host_control"}}, timeline=twin_timeline)
        twin_receiver = _InertDispatchReceiver(store, twin_authority)
        twin_response = _legacy_route(
            twin_receiver, "job_cancel",
            {"job_id": twin, "reason": "synthetic t35jx legacy authorized cancel twin"},
            envelope_project=PROJECT_A,
        )
        assert twin_response.get("success") is True
        assert twin_response["data"]["status"] == "CANCELLED"
        assert twin_response["data"]["cancel_accepted"] is True
        assert twin_response["data"]["mode"] == "QUEUED_ABORT"
        twin_after = _snapshot(store, twin)
        assert twin_after["job_status"] == "CANCELLED"
    finally:
        store.close()
