"""T35 / W26: truthfulness of terminal release diagnostics in
``ControlDaemon._release_terminal_accounting`` — regression tests for the
proposed diagnostic contract only; production code is not touched here.

Scope:
``ControlDaemon._release_terminal_accounting`` (_control_daemon.py:13134-13159)
performs the coordinator release and the subsequent release-audit event
(``W26{kind}ReservationReleased``, written via ``store.add_event``) in one try
block.  A fault writing that audit event can therefore enter the generic
exception branch after the coordinator has already returned an outcome, and
that branch currently records ``reservation_retained=True`` without separately
establishing it (_control_daemon.py:13149-13154).  A coordinator may also
raise after its own side effect, in which case the actual outcome is unknown
to its caller.  The five explicit cases below pin the T35-brief proposed
truthful diagnostic contract as five fault models.

What is REAL (product code under test):
- ``ControlDaemon._release_terminal_accounting`` is invoked as the real
  unbound function against an inert receiver that exposes only the single
  ``self`` attribute the method reads (``store``).  No ``ControlDaemon``
  instance is constructed; no W26 coordinator, Worker, engine, scheduler,
  SSH or network is involved.
- The real ``OperationStore`` (memory-only SQLite, path ``":memory:"``)
  performs admission (``begin``, _operation_store.py:406-455), the
  release-audit and diagnostic writes (``add_event``,
  _operation_store.py:2240-2245) and the readback the assertions use
  (``events``, _operation_store.py:3345-3353).  Each case gets a separate
  fresh store, closed in ``finally``.  The ``:memory:"`` constructor may
  attempt a no-op ``Path('.').mkdir(..., exist_ok=True)``
  (_operation_store.py:177); no OS-wide no-filesystem-operations claim is
  made.

What is FAKE (bounded scaffolding, no attestation value):
- ``_SyntheticCoordinator`` is a call-boundary fake with entirely synthetic
  outcomes and markers.  It establishes injected ordering (a released marker
  set before an injected raise) and the observed coordinator call count
  only; it proves nothing about real resource behaviour and never relaxes
  the owner/fencing contract.
- ``_AddEventProbe`` is installed on the fresh store's ``add_event`` after
  setup and restored in ``finally``.  ``InjectedReleaseAuditFailure`` and
  ``InjectedCoordinatorFault`` are synthetic fault models, not observed
  production faults or disk-failure simulations.

Contract status (do not misread): the diagnostic keys ``failure_stage``,
``observed_status``, the prohibition on asserting ``reservation_retained=True``
without separate establishment, and the HELD-must-not-use-a-
``ReservationReleased``-event-name rule are the proposed internal contract
named in the T35 brief.  They are NOT an existing public schema and are not
implemented in the current candidate, so cases 2-5 are expected to fail
against current code until the fix lands.  These tests were written, not
executed, per task rules; runtime outcomes are intentionally unknown.
"""
from __future__ import annotations

from typing import Any, cast

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._operation_store import OperationStore

RELEASE_AUDIT_EVENT = "W26DiskReservationReleased"


class InjectedReleaseAuditFailure(RuntimeError):
    """Synthetic fault raised in place of one ``add_event`` insertion."""


class InjectedCoordinatorFault(RuntimeError):
    """Synthetic fault raised by the coordinator after its side effect."""


class _AccountingReceiver:
    """Inert receiver exposing only ``store`` — the sole attribute read by
    ``_release_terminal_accounting``.  The ledger is the real
    ``OperationStore``, memory-only."""

    def __init__(self) -> None:
        self.store = OperationStore(":memory:")


class _SyntheticCoordinator:
    """Call-boundary fake for ``release_terminal_if_reserved``.

    ``side_effect_then_fault=True`` sets the synthetic released marker and
    then raises before returning any outcome — establishing injected
    ordering only (case 3).  ``calls`` records every invocation and
    ``fault_raised`` records the injected fault, so each test can assert
    exactly one coordinator call and exactly which fault fired.
    """

    def __init__(self, outcome: dict[str, Any] | None = None, *,
                 side_effect_then_fault: bool = False) -> None:
        self.calls: list[str] = []
        self.outcome = outcome
        self.side_effect_then_fault = side_effect_then_fault
        self.released_marker = False
        self.fault_raised = False

    def release_terminal_if_reserved(self, job_id: str) -> dict[str, Any] | None:
        self.calls.append(job_id)
        if self.side_effect_then_fault:
            self.released_marker = True
            self.fault_raised = True
            raise InjectedCoordinatorFault(
                "synthetic coordinator fault after its synthetic side effect")
        if self.outcome is not None and self.outcome.get("status") == "RELEASED":
            self.released_marker = True
        return self.outcome


class _AddEventProbe:
    """Records every ``add_event`` attempt on the task-owned fresh store.

    Each event named in ``fault_events`` faults exactly once, on its first
    attempt and before the original insertion; other calls are passed
    through to the real store.  With ``fault_any``, every attempt faults
    before any insertion.  Every test restores ``store.add_event`` in
    ``finally``.
    """

    def __init__(self, store: OperationStore, *,
                 fault_events: tuple[str, ...] = (), fault_any: bool = False) -> None:
        self.store = store
        self.original_add_event = store.add_event
        self.remaining_fault_events = set(fault_events)
        self.fault_any = fault_any
        self.attempts: list[str] = []
        self.faults_raised: list[str] = []

    def install(self) -> None:
        self.store.add_event = self._probe

    def restore(self) -> None:
        self.store.add_event = self.original_add_event

    def _probe(self, job_id: str, event: str,
               metadata: dict[str, Any] | None = None) -> None:
        self.attempts.append(event)
        if self.fault_any or event in self.remaining_fault_events:
            self.remaining_fault_events.discard(event)
            self.faults_raised.append(event)
            raise InjectedReleaseAuditFailure(f"synthetic add_event fault: {event}")
        return self.original_add_event(job_id, event, metadata)


def _admitted_job(receiver: _AccountingReceiver, *, request_id: str,
                  idempotency_key: str) -> str:
    """Open one fresh QUEUED operation/job pair through the real store.begin
    with harmless synthetic identifiers (no special hash or auth refs)."""
    record, reused = receiver.store.begin(
        request_id=request_id,
        idempotency_key=idempotency_key,
        request_hash="t35-synthetic-request-hash",
        operation="study.run",
        metadata={"synthetic_marker": "t35-resource-release-audit-truth"},
    )
    assert reused is False, record
    assert record["job_id"], record
    return record["job_id"]


def _released_outcome() -> dict[str, Any]:
    return {"status": "RELEASED", "reservation_id": "synthetic-reservation-t35-001",
            "idempotent": True, "evidence_classification": "SOFTWARE_ONLY"}


def _held_outcome() -> dict[str, Any]:
    return {"status": "HELD_QUIESCENCE_UNPROVEN",
            "reservation_id": "synthetic-reservation-t35-004",
            "idempotent": False, "evidence_classification": "SOFTWARE_ONLY"}


def test_normal_terminal_release_returns_complete_with_accurate_durable_released_event():
    """Case 1 (positive control, no fault): the synthetic coordinator returns
    RELEASED exactly once, the method returns state=COMPLETE, and the real
    durable release-audit row in the real store reflects the observed
    status and outcome metadata."""
    receiver = _AccountingReceiver()
    probe = _AddEventProbe(receiver.store)
    try:
        job_id = _admitted_job(receiver, request_id="t35-truth1-req",
                               idempotency_key="t35-truth1-key")
        coordinator = _SyntheticCoordinator(_released_outcome())
        probe.install()

        returned = ControlDaemon._release_terminal_accounting(
            cast(ControlDaemon, receiver), job_id, coordinator, "Disk")

        assert coordinator.calls == [job_id], coordinator.calls
        assert probe.attempts == [RELEASE_AUDIT_EVENT], probe.attempts
        assert probe.faults_raised == [], probe.faults_raised

        assert returned["state"] == "COMPLETE", returned
        assert returned["reservation_state"] == "RELEASED", returned
        assert returned["diagnostic_durable"] is True, returned

        events = receiver.store.events(job_id)
        assert [event["event"] for event in events] == [RELEASE_AUDIT_EVENT], events
        metadata = events[0]["metadata"]
        assert metadata["status"] == "RELEASED", metadata
        assert metadata["reservation_id"] == "synthetic-reservation-t35-001", metadata
        assert metadata["idempotent"] is True, metadata
        assert metadata["evidence_classification"] == "SOFTWARE_ONLY", metadata
    finally:
        probe.restore()
        receiver.store.close()


def test_release_audit_write_fault_returns_non_complete_and_diagnostic_marks_release_audit_stage():
    """Case 2: the coordinator marks its own synthetic state released and
    returns RELEASED; the injected fault fires exactly on the
    ``W26DiskReservationReleased`` write before that row is inserted.  The
    permitted later diagnostic write is recorded in the real store.
    Proposed invariant: the returned state is not COMPLETE while the normal
    release audit is unconfirmed; the diagnostic does not claim
    ``reservation_retained=True``; it distinguishes
    ``failure_stage='release_audit'`` and reports the actually returned
    ``observed_status='RELEASED'``.  No second coordinator call.  These
    diagnostic keys are a proposed internal contract, not a pre-existing
    public schema."""
    receiver = _AccountingReceiver()
    probe = _AddEventProbe(receiver.store, fault_events=(RELEASE_AUDIT_EVENT,))
    try:
        job_id = _admitted_job(receiver, request_id="t35-truth2-req",
                               idempotency_key="t35-truth2-key")
        coordinator = _SyntheticCoordinator(_released_outcome())
        probe.install()

        returned = ControlDaemon._release_terminal_accounting(
            cast(ControlDaemon, receiver), job_id, coordinator, "Disk")

        # Exactly the release-audit write faulted, and nothing else.
        assert probe.faults_raised == [RELEASE_AUDIT_EVENT], probe.faults_raised
        assert len(probe.attempts) == 2, probe.attempts
        assert probe.attempts[0] == RELEASE_AUDIT_EVENT, probe.attempts
        assert probe.attempts[1] != RELEASE_AUDIT_EVENT, probe.attempts

        assert coordinator.calls == [job_id], coordinator.calls
        assert coordinator.released_marker is True
        assert returned["state"] != "COMPLETE", returned

        # Real store readback: the failed audit row is absent and exactly
        # one durable diagnostic row survived.
        events = receiver.store.events(job_id)
        assert len(events) == 1, events
        assert [event["event"] for event in events] != [RELEASE_AUDIT_EVENT], events
        diagnostic = events[0]["metadata"]
        assert diagnostic.get("reservation_retained") is not True, diagnostic
        assert diagnostic.get("failure_stage") == "release_audit", diagnostic
        assert diagnostic.get("observed_status") == "RELEASED", diagnostic
        assert diagnostic.get("cause_type") == "InjectedReleaseAuditFailure", diagnostic
    finally:
        probe.restore()
        receiver.store.close()


def test_coordinator_fault_after_side_effect_records_coordinator_stage_with_unknown_observed_status():
    """Case 3: the fake coordinator sets its synthetic released marker, then
    raises before returning any outcome; the diagnostic write succeeds.
    The synthetic marker establishes injected ordering, not real natural
    behaviour.  Assert no ``reservation_retained=True`` claim,
    ``failure_stage='coordinator'``, an explicitly unknown observed outcome
    (``observed_status`` either ``None`` or ``'UNKNOWN'``), a returned state
    that is not COMPLETE, and one coordinator call only."""
    receiver = _AccountingReceiver()
    probe = _AddEventProbe(receiver.store)
    try:
        job_id = _admitted_job(receiver, request_id="t35-truth3-req",
                               idempotency_key="t35-truth3-key")
        coordinator = _SyntheticCoordinator(side_effect_then_fault=True)
        probe.install()

        returned = ControlDaemon._release_terminal_accounting(
            cast(ControlDaemon, receiver), job_id, coordinator, "Disk")

        # The injected coordinator fault is the only fault that fired.
        assert coordinator.fault_raised is True, coordinator
        assert coordinator.released_marker is True, coordinator
        assert probe.faults_raised == [], probe.faults_raised

        assert coordinator.calls == [job_id], coordinator.calls
        assert returned["state"] != "COMPLETE", returned

        events = receiver.store.events(job_id)
        assert len(events) == 1, events
        diagnostic = events[0]["metadata"]
        assert diagnostic.get("reservation_retained") is not True, diagnostic
        assert diagnostic.get("failure_stage") == "coordinator", diagnostic
        assert "observed_status" in diagnostic, diagnostic
        assert diagnostic.get("observed_status") in (None, "UNKNOWN"), diagnostic
        assert diagnostic.get("cause_type") == "InjectedCoordinatorFault", diagnostic
    finally:
        probe.restore()
        receiver.store.close()


def test_held_quiescence_outcome_returns_held_without_reservation_released_event_name():
    """Case 4 (proposed truthfulness invariant): the coordinator returns
    ``HELD_QUIESCENCE_UNPROVEN`` with no injected fault.  The method returns
    HELD and the single durable event does not use a
    ``ReservationReleased``-style event name while accurately carrying the
    HELD status.  No particular alternative event name is asserted.
    No second coordinator call."""
    receiver = _AccountingReceiver()
    probe = _AddEventProbe(receiver.store)
    try:
        job_id = _admitted_job(receiver, request_id="t35-truth4-req",
                               idempotency_key="t35-truth4-key")
        coordinator = _SyntheticCoordinator(_held_outcome())
        probe.install()

        returned = ControlDaemon._release_terminal_accounting(
            cast(ControlDaemon, receiver), job_id, coordinator, "Disk")

        assert coordinator.calls == [job_id], coordinator.calls
        assert len(probe.attempts) == 1, probe.attempts
        assert probe.faults_raised == [], probe.faults_raised

        assert returned["state"] == "HELD", returned
        assert returned["reservation_state"] == "HELD_QUIESCENCE_UNPROVEN", returned
        assert returned["diagnostic_durable"] is True, returned

        events = receiver.store.events(job_id)
        assert len(events) == 1, events
        assert all("ReservationReleased" not in event["event"] for event in events), events
        carried = {event["metadata"].get("status") for event in events} | {
            event["metadata"].get("observed_status") for event in events}
        assert "HELD_QUIESCENCE_UNPROVEN" in carried, events
    finally:
        probe.restore()
        receiver.store.close()


def test_every_diagnostic_event_write_failing_returns_non_complete_with_durable_false():
    """Case 5: the synthetic coordinator returns RELEASED; the first
    release-audit event write raises and any permitted fault-diagnostic
    event write also raises.  The result is not COMPLETE, states
    ``diagnostic_durable=False``, and the coordinator was called exactly
    once.  Because every insertion failed, no durable audit is claimed and
    the real store holds no event row."""
    receiver = _AccountingReceiver()
    probe = _AddEventProbe(receiver.store, fault_any=True)
    try:
        job_id = _admitted_job(receiver, request_id="t35-truth5-req",
                               idempotency_key="t35-truth5-key")
        coordinator = _SyntheticCoordinator(_released_outcome())
        probe.install()

        returned = ControlDaemon._release_terminal_accounting(
            cast(ControlDaemon, receiver), job_id, coordinator, "Disk")

        assert coordinator.calls == [job_id], coordinator.calls
        # The audit event and one later diagnostic event were both attempted
        # and both faulted; the diagnostic name itself is not pinned.
        assert len(probe.attempts) == 2, probe.attempts
        assert probe.attempts[0] == RELEASE_AUDIT_EVENT, probe.attempts
        assert probe.attempts[1] != probe.attempts[0], probe.attempts
        assert probe.faults_raised == probe.attempts, probe.attempts

        assert returned["state"] != "COMPLETE", returned
        assert returned["diagnostic_durable"] is False, returned

        events = receiver.store.events(job_id)
        assert events == [], events
    finally:
        probe.restore()
        receiver.store.close()
