"""Regression tests for wrapped callback causes and legacy stage projection.

These use a fake in-memory adapter; they do not test persistence, a daemon,
a Worker, COMSOL, or real publication. A lower-stage projection is a recorded
claim, not proof of dispatch. Reserved-key type changes and post-wrapper slot
reassignment are separate compatibility boundaries, documented in the
T08 F-2 parent adjudication.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

import pytest

from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._control_daemon import _STAGE_CAUSE_DETAIL_KEYS, _stage_failure_cause_details

_CARRIER_ATTR = "_legacy_wrapped_cause_claims"


# --- minimal in-memory harness (real service + real projector, fake adapter) --

class FakeAdapter:
    def model_snapshot(self, model_tag):
        return {"model_tag": model_tag, "fingerprint": "fp1", "external_event_counter": 0}


class Harness:
    def __init__(self) -> None:
        self.ledger = SessionLedger(session_id="s1", server_instance_id="i1",
                                    server_ownership="mcp_managed")
        self.service = ExecutionService(self.ledger, FakeAdapter(),
                                        project_root=str(Path(__file__).resolve().parent))
        self.ref = self.ledger.bind_model("m1", ownership="mcp_owned", fingerprint="fp1")
        self.dispatches = 0
        self.inner: BaseException | None = None
        self.err: ExecutionContractError | None = None

    def raise_from_callback(self, exc_factory) -> ExecutionContractError:
        def cb(_args):
            self.dispatches += 1
            self.inner = exc_factory()
            raise self.inner

        with pytest.raises(ExecutionContractError) as caught:
            self.service.execute_legacy("node_create", cb, {"name": "n1"}, model_ref=self.ref,
                                        expected_revision=0, request_id="rq-1")
        self.err = caught.value
        return self.err

    @property
    def env(self) -> dict:
        assert self.err is not None
        return self.err.as_dict()


def _colliding_callback_exc() -> ExecutionContractError:
    """Structured callback error colliding with all four immediate keys."""
    return ExecutionContractError(
        "INNER_A", "inner-a-msg",
        details={"cause_code": "CLAIM_CODE_A", "cause_stage": "callback",
                 "cause_type": "MARKER_TYPE_X", "cause_message": "MARKER_MSG_X",
                 "publication_status": "PUBLISHED_LOCAL"})


class TrapStage(str):
    """Runtime-legal ``str`` subclass whose ``str()`` has side effects.

    Natural-producer reachability is UNKNOWN (unusual input, not proof of a
    native case); it exists here only so the projection order is observable.
    """

    def __str__(self):
        raise RuntimeError("synthetic stage stringification reached")


# --- tests: envelope layer split (baseline collides here) ---------------------

def test_envelope_keeps_immediate_cause_layer_on_collision():
    h = Harness()
    h.raise_from_callback(_colliding_callback_exc)
    assert h.err.code == "EXECUTION_STATE_UNKNOWN"
    assert h.env["stage"] == "post_dispatch"
    details = h.env["details"]
    # Immediate layer owns the four cause slots top-level.
    assert details["cause_type"] == "ExecutionContractError"
    assert details["cause_message"] == "inner-a-msg"
    assert details["cause_code"] == "INNER_A"
    # The inner error declared no stage, so the top-level slot keeps the
    # immediate None value; the callback's lower "callback" claim must never
    # be written into it (the adjudicated shape stores the slot with its
    # immediate value rather than omitting the key).
    assert details.get("cause_stage") is None, (
        f"lower cause_stage shadowed the immediate slot: {details.get('cause_stage')!r}")
    # Lower colliding claims survive nested, verbatim.
    assert details["callback_cause_facts"] == {"cause_code": "CLAIM_CODE_A",
                                              "cause_stage": "callback"}


def test_overwritten_lower_cause_type_message_stays_suppressed_strict_json():
    h = Harness()
    h.raise_from_callback(_colliding_callback_exc)
    env_json = json.dumps(h.env)  # for these ordinary values: raises TypeError unless fully JSON
    proj_json = json.dumps(_stage_failure_cause_details(h.err))
    for marker in ("MARKER_TYPE_X", "MARKER_MSG_X"):
        assert marker not in env_json, f"suppressed lower value leaked into envelope: {marker}"
        assert marker not in proj_json, f"suppressed lower value leaked into projection: {marker}"


def test_projection_keeps_lower_claim_alongside_immediate_layers():
    h = Harness()
    h.raise_from_callback(_colliding_callback_exc)
    proj = _stage_failure_cause_details(h.err)
    # Historical flat projection: immediate type/message + baseline-effective
    # lower code/stage claims (a claim, not proof), plus publication facts.
    assert proj == {"cause_type": "ExecutionContractError", "cause_code": "CLAIM_CODE_A",
                    "cause_stage": "callback", "cause_message": "inner-a-msg",
                    "publication_status": "PUBLISHED_LOCAL"}
    assert list(proj) == ["cause_type", "cause_code", "cause_stage", "cause_message",
                          "publication_status"], f"key order drifted: {list(proj)}"
    assert "callback_cause_facts" not in proj


def test_reserved_container_collision_value_preserved_nested():
    h = Harness()
    h.raise_from_callback(lambda: ExecutionContractError(
        "INNER_D", "inner-d-msg", stage="post_dispatch",
        details={"callback_cause_facts": {"cause_stage": "<hidden>"}}))
    details = h.env["details"]
    # Disclosed type change: top-level reserved key becomes the container; the
    # callback's original value is preserved inside it, nothing is lost.
    assert details["callback_cause_facts"] == {"callback_cause_facts": {"cause_stage": "<hidden>"}}
    assert details["cause_code"] == "INNER_D"
    assert details["cause_stage"] == "post_dispatch"
    proj = _stage_failure_cause_details(h.err)
    assert "callback_cause_facts" not in proj
    assert "<hidden>" not in json.dumps(proj), "forged nested value reached durable projection"


def test_explicit_none_lower_claim_omits_key():
    h = Harness()
    h.raise_from_callback(lambda: ExecutionContractError(
        "INNER_L1", "inner-l1-msg", stage="post_dispatch", details={"cause_stage": None}))
    proj = _stage_failure_cause_details(h.err)
    assert "cause_stage" not in proj, "explicit-None claim invented a projected key"
    assert list(proj) == ["cause_type", "cause_code", "cause_message"]
    assert proj == {"cause_type": "ExecutionContractError", "cause_code": "INNER_L1",
                    "cause_message": "inner-l1-msg"}


# --- tests: projection evaluation order (r3 regression axis) ------------------

def test_immediate_throwing_stage_never_stringified_when_claim_replaces_slot():
    h = Harness()
    h.raise_from_callback(lambda: ExecutionContractError(
        "INNER_T", "inner-t-msg", stage=TrapStage("post_dispatch"),
        details={"cause_stage": "callback"}))
    assert h.err.code == "EXECUTION_STATE_UNKNOWN"
    assert h.err.__cause__ is h.inner
    # The slot the carrier replaces must be decided before stringification:
    # projecting must RETURN, not evaluate the trap value.
    proj = _stage_failure_cause_details(h.err)
    assert proj == {"cause_type": "ExecutionContractError", "cause_code": "INNER_T",
                    "cause_stage": "callback", "cause_message": "inner-t-msg"}
    assert list(proj) == ["cause_type", "cause_code", "cause_stage", "cause_message"]


# --- tests: direct reads, forgery guards, chain integrity ---------------------

def test_direct_exception_without_carrier_projects_mapping_read():
    exc = ExecutionContractError("DIRECT_1", "direct", stage="post_dispatch",
                                 details={"cause_stage": "callback", "publication_status": "P"})
    assert _stage_failure_cause_details(exc) == {"cause_stage": "callback",
                                                 "publication_status": "P"}

    # Corrected arbitrary-Mapping probe.  ``ExecutionContractError.__init__``
    # copies constructor details via ``dict(details)``, so a Mapping passed to
    # the constructor is consumed by the constructor and the projector only
    # ever sees a fresh dict - a read counter from that path proves nothing
    # about the projector.  The probe below is therefore attached to
    # ``exc.details`` AFTER construction and its observation log is cleared
    # immediately before the projector call, so every recorded read can only
    # come from the actual projector.  This is a synthetic direct-projector
    # scenario: it does not claim any actual worker sent a Python Mapping
    # through the real service path.
    class _ProbeMapping(Mapping):
        def __init__(self, data):
            self._data = dict(data)
            self.reads: list[tuple[str, str]] = []

        def __getitem__(self, key):
            self.reads.append(("index", key))
            return self._data[key]

        # ``get`` is overridden explicitly on purpose: the Mapping ABC default
        # would route it through ``__getitem__`` and blur the two read kinds.
        def get(self, key, default=None):
            self.reads.append(("get", key))
            return self._data.get(key, default)

        def __iter__(self):
            return iter(self._data)

        def __len__(self):
            return len(self._data)

    data = {"cause_stage": "callback", "publication_status": "P"}
    probe = _ProbeMapping(data)
    exc2 = ExecutionContractError("DIRECT_2", "direct", stage="post_dispatch")
    # The probe never touched the constructor: no constructor read is
    # possible, and this assertion documents why (details is a fresh dict).
    assert exc2.details == {}
    exc2.details = probe
    assert exc2.details is probe, "identity: the projector must read THIS object"
    probe.reads.clear()
    assert _stage_failure_cause_details(exc2) == {"cause_stage": "callback",
                                                  "publication_status": "P"}
    # Desired non-carrier read order (historical semantics, identical in the
    # baseline and in the r4 correction): for every production cause key in
    # ``_STAGE_CAUSE_DETAIL_KEYS`` order, presence is tested via ``get``; only
    # a present, non-None value is additionally fetched via ``__getitem__``;
    # absent keys are touched by ``get`` alone and never invented.
    expected_reads: list[tuple[str, str]] = []
    for key in _STAGE_CAUSE_DETAIL_KEYS:
        expected_reads.append(("get", key))
        if data.get(key) is not None:
            expected_reads.append(("index", key))
    assert probe.reads == expected_reads, (
        f"projector read sequence drifted from presence-get/value-index in "
        f"production key order: {probe.reads}")
    # Reads were logged on the probe object itself (identity of the read
    # target): the projector read the attached Mapping, not a copy.
    assert probe.reads and exc2.details is probe


def test_forged_carrier_attribute_never_claims():
    for forged in (SimpleNamespace(effective_code="FORGED_CODE", effective_stage="FORGED_STAGE"),
                   {"effective_code": "FORGED_CODE", "effective_stage": "FORGED_STAGE"}):
        exc = ExecutionContractError("DIRECT_K", "direct", stage="post_dispatch",
                                     details={"cause_stage": "callback"})
        setattr(exc, _CARRIER_ATTR, forged)
        proj = _stage_failure_cause_details(exc)
        assert proj == {"cause_stage": "callback"}, f"direct projection drifted: {proj}"
        assert "FORGED" not in json.dumps(proj)


def test_tampered_chain_drops_recorded_claim():
    h = Harness()
    h.raise_from_callback(_colliding_callback_exc)
    h.err.__cause__ = RuntimeError("tampered-chain")
    after = _stage_failure_cause_details(h.err)
    assert "cause_stage" not in after, (
        f"lower claim survived a broken chain: {after.get('cause_stage')!r}")
    assert after.get("cause_code") == "INNER_A", (
        f"projection lost the immediate code after tampering: {after.get('cause_code')!r}")


# --- tests: unchanged normal operation -----------------------------------------

def test_successful_operation_reaches_success_envelope():
    h = Harness()
    res = h.service.execute_legacy("node_create",
                                   lambda a: {"success": True, "data": {"ok": 1}},
                                   {"name": "n1"}, model_ref=h.ref, expected_revision=0,
                                   request_id="rq-ok")
    assert res["success"] is True
    assert h.ledger._state_for(h.ref).dirty is False
    assert h.ledger.revision(h.ref) == 1
    json.dumps(res)


def test_non_conflicting_callback_mapping_merges_flat():
    h = Harness()
    h.raise_from_callback(lambda: ExecutionContractError(
        "INNER_F", "inner-f-msg", stage="post_dispatch",
        details={"publication_status": "OK1", "original_preservation": "KEPT",
                 "temporary_retention_observed": "YES"}))
    details = h.env["details"]
    for key, value in (("publication_status", "OK1"), ("original_preservation", "KEPT"),
                       ("temporary_retention_observed", "YES")):
        assert details.get(key) == value
    assert "callback_cause_facts" not in details
    proj = _stage_failure_cause_details(h.err)
    assert proj == {"cause_type": "ExecutionContractError", "cause_code": "INNER_F",
                    "cause_stage": "post_dispatch", "cause_message": "inner-f-msg",
                    "publication_status": "OK1", "original_preservation": "KEPT",
                    "temporary_retention_observed": "YES"}


def test_dirty_unknown_state_blocks_blind_retry():
    h = Harness()
    h.raise_from_callback(_colliding_callback_exc)
    assert h.ledger._state_for(h.ref).dirty is True
    assert h.err.code == "EXECUTION_STATE_UNKNOWN"
    assert h.env["safe_retry"] is False
    def _again(_args):
        raise AssertionError("no-blind-retry: callback must not be dispatched again")
    with pytest.raises(ExecutionContractError) as second:
        h.service.execute_legacy("node_create", _again, {"name": "n2"}, model_ref=h.ref,
                                 expected_revision=1, request_id="rq-2")
    assert second.value.code == "REVISION_CONFLICT"
    assert h.dispatches == 1, "callback was dispatched more than once"
