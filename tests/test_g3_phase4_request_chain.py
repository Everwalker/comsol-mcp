"""Offline regressions for the C02 request / revision / idempotency chain.

Every check here runs without COMSOL and without ``--live``.  They exercise the run's *one*
execution context (revisions and dirty flags keyed by the full ModelRef, one identity per logical
request) and the envelope reader every wire call passes through:

* a request that contradicts itself is refused before dispatch (``generation_mismatch``),
* a new logical request gets its own key; the old per-operation constant key cannot be reproduced,
* a retry may reuse a key only with a byte-identical body,
* a deliberately stale/conflicting probe is never repaired automatically,
* a request whose engine outcome was never established is resolved through its *own* job first,
* exactly one recorded new plan is allowed, and only for a request proved NOT_EXECUTED.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Mapping

import pytest

ROOT = Path(__file__).resolve().parents[1]
DRIVER_PATH = ROOT / "tools" / "phase4_run_mcp.py"
EVIDENCE_DIR = ROOT / "evidence" / "phase4" / "runs" / "20260920T130620Z-g3-live" / "driver5c"
#: The per-operation constant key the interrupted run actually sent (see the case's
#: ``requests.json``): it is the defect this chain replaces, so the new mint must never produce it.
LEGACY_KEY = "phase4-driver5cphase4-describe-evaluate_expressions"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def driver():
    return _load("phase4_driver_request_chain", DRIVER_PATH)


def _ref(*, tag: str = "m1", session: str = "s1", instance: str = "i1",
         generation: int | None = None) -> dict[str, Any]:
    ref: dict[str, Any] = {"session_id": session, "server_instance_id": instance, "model_tag": tag}
    if generation is not None:
        ref["generation"] = generation
    return ref


def _envelope(operation: str, *, success: bool = True, job_id: str | None = "job-1",
              data: Mapping[str, Any] | None = None, code: str | None = None, message: str | None = None,
              ref: Mapping[str, Any] | None = None, revision: int | None = None,
              dirty: bool | None = None, request_id: str | None = None) -> dict[str, Any]:
    execution: dict[str, Any] = {}
    if job_id is not None:
        execution["job_id"] = job_id
    if ref is not None:
        execution["model_ref"] = dict(ref)
    if revision is not None:
        execution["revision"] = revision
    if dirty is not None:
        execution["dirty"] = dirty
    if request_id is not None:
        execution["request_id"] = request_id
    payload: dict[str, Any] = {"success": success, "data": dict(data or {}), "execution": execution,
                               "_outer_isError": not success}
    if code is not None:
        payload["error"] = {"code": code, "message": message or code, "safe_retry": False}
    payload["_structuredContent"] = {"success": success, "data": dict(data or {}), "execution": dict(execution),
                                     **({"error": payload["error"]} if code else {})}
    return payload


def test_revisions_and_dirty_are_keyed_by_the_full_model_ref(driver) -> None:
    """Two models in one run must not share a revision counter or a dirty flag."""
    context = driver.ExecutionContext(run="t")
    first, second = _ref(tag="modelA"), _ref(tag="modelB")
    context.observe("geometry.build", _envelope("geometry.build", ref=first, revision=4, dirty=True))
    context.observe("geometry.build", _envelope("geometry.build", ref=second, revision=11, dirty=False))
    assert context.revision_for(first) == 4 and context.dirty_for(first) is True
    assert context.revision_for(second) == 11 and context.dirty_for(second) is False
    # A model the run never observed has no state at all: nothing is inherited from "the run".
    assert context.revision_for(_ref(tag="modelC")) is None
    assert context.dirty_for(_ref(tag="modelC")) is None
    # The same tag on another session is another model.
    assert context.revision_for(_ref(tag="modelA", session="s9")) is None


def test_envelope_reader_uses_the_published_paths_only(driver) -> None:
    """A domain-level ``status`` inside ordinary data is not the operation's own outcome."""
    payload = {"success": True, "data": {"status": {"ok": False}, "revision": 99},
               "execution": {"job_id": "job-x", "revision": 3}, "_outer_isError": False}
    witness = driver.unpack_envelope("model.inspect", payload)
    assert witness.success is True and witness.error_code is None
    assert witness.revision == 3, "the envelope's own execution block decides, not a nested status"
    assert witness.source["success"] == "payload.success"
    # Two published copies of one field that disagree are reported, never resolved by preference.
    conflicting = {"success": True, "data": {}, "execution": {},
                   "_structuredContent": {"success": False, "data": {}, "execution": {}}}
    witness = driver.unpack_envelope("model.inspect", conflicting)
    assert witness.conflicts and "success" in witness.conflicts[0]
    # The outcome is not read out of a nested mapping that merely happens to spell "success".
    nested = {"data": {"result": {"success": True, "data": {"job_id": "job-nested"}}}}
    witness = driver.unpack_envelope("model.inspect", nested)
    assert witness.success is None and witness.job_id is None


def test_a_self_contradicting_request_is_refused_before_dispatch(driver) -> None:
    """Identity fields that disagree are refused, and the reason distinguishes the mismatch."""
    context = driver.ExecutionContext(run="t")
    # The driver sends the identity three times (top level, arguments, execution): the copies must
    # agree before anything reaches the wire.
    arguments = {"model_ref": _ref(tag="modelA"),
                 "execution": {"model_ref": _ref(tag="modelB"), "request_id": "r-1"}}
    conflict = driver.identity_conflict(arguments)
    assert conflict is not None and conflict["reason"] == "generation_mismatch"
    assert conflict["field"] == "model_ref" and sorted(conflict["paths"]) == ["execution.model_ref", "model_ref"]
    revision_conflict = driver.identity_conflict({"expected_revision": 2, "execution": {"expected_revision": 3}})
    assert revision_conflict is not None and revision_conflict["field"] == "expected_revision"
    # Agreeing copies are not a conflict.
    assert driver.identity_conflict({"model_ref": _ref(tag="modelA"),
                                     "execution": {"model_ref": _ref(tag="modelA")}}) is None
    decision = context.precheck(arguments, tool="node.property_set")
    assert decision is not None and decision["action"] == "refuse"
    assert decision["reason"] in driver.REJECTION_REASONS
    assert context.evidence()["rejection_counts"][decision["reason"]] == 1
    assert context.dispatches == [], "a refused request is never dispatched"


def test_an_envelope_naming_another_model_is_not_adopted(driver) -> None:
    """The "two models, one global revision" defect: adopting is refused, the row is recorded."""
    context = driver.ExecutionContext(run="t")
    request = {"model_ref": _ref(tag="modelA"), "execution": {"request_id": "r-1"}}
    row = context.observe("geometry.build", _envelope("geometry.build", ref=_ref(tag="modelB"), revision=7),
                          request=request)
    assert row["adopted"] is False
    assert context.revision_for(_ref(tag="modelB")) is None
    assert context.evidence()["rejection_counts"]["generation_mismatch"] == 1
    # The request's own model is what gets adopted when the envelope agrees with it.
    ok = context.observe("geometry.build", _envelope("geometry.build", ref=_ref(tag="modelA"), revision=7),
                         request=request)
    assert ok["adopted"] is True and context.revision_for(_ref(tag="modelA")) == 7


def test_every_new_logical_request_mints_its_own_key(driver) -> None:
    """A new request is a new identity: the interrupted run's per-operation constant is impossible."""
    context = driver.ExecutionContext(run="t")
    first = context.mint(case="W16_T019_chainA", step="geometry.build", body={"path": {"segments": []}})
    second = context.mint(case="W16_T019_chainA", step="geometry.build", body={"path": {"segments": [1]}})
    other = context.mint(case="W16_T019_chainB", step="geometry.build", body={"path": {"segments": []}})
    assert len({first.key, second.key, other.key}) == 3
    for plan in (first, second, other):
        assert plan.run == "t" and plan.case and plan.step and plan.sequence >= 1
        assert plan.key != LEGACY_KEY and "phase4-describe-" not in plan.key
        assert plan.key.startswith("t-"), "the key names the run, the case, the step and the sequence"
    assert (first.sequence, second.sequence) == (1, 2), "sequence counts within one case/step scope"
    # An explicit key is honoured verbatim (a case may probe the product's own contract) and the
    # reuse of a driver-minted key by another body is recorded as the driver defect it is.
    explicit = context.mint(case="C", step="s", body={"a": 1}, explicit_key=LEGACY_KEY)
    assert explicit.key == LEGACY_KEY and explicit.explicit_key is True
    context.mint(case="C", step="s", body={"a": 2}, explicit_key=LEGACY_KEY)
    replays = context.evidence()["replays"]
    assert any(row.get("intent", "").startswith("deliberate explicit-key reuse") for row in replays)


def test_a_retry_reuses_its_key_only_with_an_identical_body(driver) -> None:
    """Same key + identical body is the one legitimate reuse; a changed body is a new request."""
    context = driver.ExecutionContext(run="t")
    body = {"path": {"segments": [{"collection": "geom", "tag": "geom1"}]}}
    plan = context.mint(case="C", step="geometry.build")
    context.bind_body(plan.key, body)
    assert context.reuse_for_retry(plan.key, body=body) is None, "an identical retry may proceed"
    refusal = context.reuse_for_retry(plan.key, body={"path": {"segments": []}})
    assert refusal is not None and refusal["reason"] in driver.REJECTION_REASONS
    assert "identical body" in refusal["detail"]
    record = context.plan(plan.key).with_changes(retry_of=plan.request_id)
    assert record.retry_of == plan.request_id and record.key == plan.key
    # A request this context never minted (a direct host call) has nothing to compare: the check is
    # recorded as skipped, never as passed.
    assert context.reuse_for_retry("never-minted", body={"a": 1}) is None
    skipped = [row for row in context.evidence()["replays"] if row.get("checked") is False]
    assert skipped and "no plan was minted" in skipped[0]["intent"]


def test_an_unfinished_job_is_resolved_through_its_own_query_first(driver) -> None:
    context = driver.ExecutionContext(run="t")
    entry = context.note_unfinished("job-unknown", tool="geometry.build", error_code="EXECUTION_STATE_UNKNOWN",
                                    message="engine outcome unknown")
    assert entry["observations"] == 1 and context.unfinished_jobs() == ["job-unknown"]
    arguments = {"execution": {"request_id": "r-1"}}
    decision = context.precheck(arguments, tool="geometry.build")
    assert decision is not None and decision["action"] == "query_first" and decision["reason"] == "unknown_job"
    # A published query is bounded, and the reconcile read is exempt from its own gate.
    assert context.query_budget("job-unknown") is True and context.query_budget("job-unknown") is True
    assert context.query_budget("job-unknown") is False
    assert context.precheck(arguments, tool="job_reconcile") is None
    # ``reconcile=False`` marks a negative probe: the gate does not silently swallow its evidence.
    assert context.precheck(arguments, tool="geometry.build", reconcile=False) is None
    context.resolve_unfinished("job-unknown", outcome="released")
    assert context.unfinished_jobs() == [] and context.precheck(arguments, tool="geometry.build") is None


def test_stale_expected_and_external_observation_are_distinguished(driver) -> None:
    """The release path has to know whether *this driver* produced the revision it is behind."""
    self_caused = driver.ExecutionContext(run="t")
    ref = _ref(tag="modelA")
    self_caused.observe("geometry.build", _envelope("geometry.build", ref=ref, revision=5, dirty=True))
    self_caused.note_dispatch(request_id="r-1", tool="geometry.build", stage="dispatched", model_ref=ref,
                              revision=5)
    decision = self_caused.precheck({"expected_revision": 4, "execution": {"model_ref": ref}},
                                    tool="node.property_set")
    assert decision is not None and decision["reason"] == "stale_expected"
    assert decision["detail"]["self_caused"] is True

    external = driver.ExecutionContext(run="t")
    external.observe("geometry.build", _envelope("geometry.build", ref=ref, revision=5, dirty=True))
    # A call refused before dispatch never reached the engine, so the change is not self-caused.
    external.note_dispatch(request_id="r-2", tool="node.property_set", stage="refused_before_engine",
                           reason="REVISION_CONFLICT", model_ref=ref)
    decision = external.precheck({"expected_revision": 4, "execution": {"model_ref": ref}},
                                 tool="node.property_set")
    assert decision is not None and decision["reason"] == "external_observation"
    assert decision["detail"]["self_caused"] is False
    assert external.self_dispatched(ref) is False

    # A deliberate stale probe keeps the product's own refusal as its evidence: nothing is repaired.
    probe = driver.ExecutionContext(run="t")
    probe.observe("geometry.build", _envelope("geometry.build", ref=ref, revision=5, dirty=True))
    decision = probe.precheck({"expected_revision": 4, "execution": {"model_ref": ref}},
                              tool="node.property_set", negative_probe=True)
    assert decision is not None and decision["action"] == "proceed"
    assert "kept" in decision["detail"]["negative_probe"]
    record = probe.note_negative_probe(request_id="r-3", tool="node.property_set", model_ref=ref,
                                       note="the stale probe must be refused by the product")
    assert "refused" in record["auto_repair"]


def test_exactly_one_recorded_new_plan_and_only_for_a_proved_not_executed_request(driver) -> None:
    """A new plan needs a proved NOT_EXECUTED request *and* a legal re-verification."""
    context = driver.ExecutionContext(run="t")
    plan = context.mint(case="C", step="geometry.build", body={"path": {"segments": []}})
    reviewed = {"reconciled": True, "revision": 6, "note": "the published read re-verified the model"}

    refused, decision = context.grant_replan(plan.key, body={"path": {"segments": []}},
                                             evidence={"dispatch_stage": "unknown"}, why="a first-hand UNKNOWN")
    assert refused is None and decision["granted"] is False and decision["reason"] == "unknown_job"

    refused, decision = context.grant_replan(plan.key, body={"path": {"segments": []}},
                                             evidence={"dispatch_stage": "dispatched"}, why="the engine answered")
    assert refused is None and decision["granted"] is False and decision["reason"] == "external_observation"

    granted, decision = context.grant_replan(plan.key, body={"path": {"segments": []}},
                                             evidence={"dispatch_stage": "refused_before_engine",
                                                       "refusal": "REVISION_CONFLICT", "recheck": reviewed},
                                             why="the product refused it before the engine and the model was re-verified")
    assert granted is not None and decision["granted"] is True
    assert granted.replan_of == plan.key and granted.key != plan.key
    assert granted.sequence == plan.sequence + 1

    again, decision = context.grant_replan(plan.key, body={"path": {"segments": []}},
                                           evidence={"dispatch_stage": "refused_before_engine",
                                                     "recheck": reviewed}, why="a second attempt")
    assert again is None and decision["granted"] is False and "one recorded new plan" in decision["detail"]
    assert len([row for row in context.evidence()["replans"] if row.get("granted")]) == 1


def test_refusal_evidence_names_the_dispatch_stage_it_can_prove(driver) -> None:
    context = driver.ExecutionContext(run="t")
    pre_dispatch = context.refusal_evidence({"success": False, "data": {"status": "REVISION_CONFLICT"},
                                             "error": {"code": "REVISION_CONFLICT", "message": "stale revision"}})
    assert pre_dispatch["dispatch_stage"] == "refused_before_engine"
    unknown = context.refusal_evidence({"success": False,
                                        "error": {"code": "ENVELOPE_INVALID", "message": "unreadable"}})
    assert unknown["dispatch_stage"] == "unknown", "an undocumented refusal proves no stage"
    # The stage vocabulary the whole chain reasons with stays closed: a request is only replayable
    # when it provably never executed.  ``dispatched_without_mutation`` is the one stage that proves
    # NOT_EXECUTED from the *product's* own witness (``error.details.witness.mutation_issued is
    # False``) rather than from a refusal code: the callback reached the engine, issued reads only,
    # and changed nothing — which is what the M1 run's ``variable.group_create`` refusal reports.
    assert set(driver.NOT_EXECUTED_STAGES) <= set(driver.DISPATCH_STAGES)
    assert driver.NOT_EXECUTED_STAGES == frozenset({"refused_before_engine", "not_dispatched",
                                                     "dispatched_without_mutation"})
    witness_proven = context.refusal_evidence(
        {"success": False, "data": {},
         "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": "the callback failed after dispatch",
                   "details": {"dispatch_stage": "post_dispatch", "cause_code": "NODE_NOT_FOUND",
                               "unproven_pre_dispatch": False,
                               "witness": {"engine_calls": 2, "methods": ["component", "tags"],
                                           "mutation_issued": False, "mutation_method": None}}}})
    assert witness_proven["dispatch_stage"] == "dispatched_without_mutation"
    # A refusal that publishes no such witness keeps ``unknown``: the pre-dispatch proof is never
    # assumed from a *different* envelope's shape.
    assert driver.observed_dispatch_stage({"success": False, "data": {},
                                           "error": {"code": "EXECUTION_STATE_UNKNOWN",
                                                     "message": "no witness published"}}) == "unknown"


def test_the_run_evidence_exposes_the_chain_for_the_summary(driver) -> None:
    context = driver.ExecutionContext(run="t")
    plan = context.mint(case="C", step="geometry.build", body={"a": 1})
    context.observe("geometry.build", _envelope("geometry.build", ref=_ref(), revision=2, dirty=True,
                                                request_id=plan.key), request=plan.as_dict())
    evidence = context.evidence()
    assert set(evidence["rejection_reasons"]) == set(driver.REJECTION_REASONS)
    assert evidence["request_count"] == 1
    row = evidence["requests"][0]
    assert {"run", "case", "step", "sequence", "request_id", "body_sha256"} <= set(row)
    assert row["request_id"] == plan.key and row["sequence"] == plan.sequence
    assert evidence["model_states"] and evidence["model_states"][0]["token"]
    assert "rejection_counts" in evidence and "unfinished_jobs" in evidence
    json.dumps(evidence, default=str)


@pytest.mark.skipif(not EVIDENCE_DIR.is_dir(), reason="the interrupted run's evidence is not present")
def test_the_interrupted_runs_fixed_key_is_not_reproducible(driver) -> None:
    """Replay one real request of the interrupted run: its key was constant per operation."""
    request = json.loads((EVIDENCE_DIR / "cases" / "GUARD_T033" / "requests.json").read_text(encoding="utf-8"))["requests"][0]
    sent = request["arguments"]["execution"]["idempotency_key"]
    assert sent == LEGACY_KEY
    # The run prefix was the *only* part that made it unique; the per-request part carried no
    # case/step/sequence, so a second logical request of that operation would have collided.
    assert not any(part.isdigit() for part in sent.split("describe-")[-1].split("-"))
    context = driver.ExecutionContext(run="phase4-driver5c")
    other = context.mint(case="phase4", step="describe-evaluate_expressions", body={"operation_id": "x"})
    assert other.key != sent and other.sequence == 1
