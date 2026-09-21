"""Snapshot replay of the interrupted run's own failed request/return pair.

The live run of ``20260920T130620Z`` left one request per operation under a constant idempotency key
and got back ``EXECUTION_STATE_UNKNOWN``.  Those files are the *original failure evidence*: this test
replays them (never rewrites them) and checks that the new chain reads, normalizes, key-mints and
classifies exactly that material:

* the four case files still hash as the run's ``SHA256SUMS`` says, before and after the replay,
* the envelope reader unpacks the recorded return through the published paths (the old reader read
  the outer ``success`` and lost the error code),
* the old key is recognized as the pre-C02 shape (constant per operation, no case/step/sequence) and
  the new mint cannot reproduce it,
* the recorded refusal classifies as an unfinished job (``unknown_job``) and BLOCKED, never as a FAIL
  of the operation's contract.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
DRIVER_PATH = ROOT / "tools" / "phase4_run_mcp.py"
RUN_DIR = ROOT / "evidence" / "phase4" / "runs" / "20260920T130620Z-g3-live" / "driver5c"
CASE_DIR = RUN_DIR / "cases" / "GUARD_T033"
CASE_FILES = ("assertions.json", "environment.json", "requests.json", "results.json")
#: The case whose recorded return *names the job* that closed the gate (a first-hand UNKNOWN), which
#: is the material the unfinished-job chain has to read.
GATE_CASE = "GUARD_T010"
GATE_DIR = RUN_DIR / "cases" / GATE_CASE
OLD_KEY = "phase4-driver5cphase4-describe-evaluate_expressions"

pytestmark = pytest.mark.skipif(not CASE_DIR.is_dir(),
                                reason="the interrupted run's evidence is not present in this checkout")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def driver():
    return _load("phase4_driver_old_run_replay", DRIVER_PATH)


@pytest.fixture(scope="module")
def recorded() -> dict[str, Any]:
    requests = json.loads((CASE_DIR / "requests.json").read_text())["requests"]
    results = json.loads((CASE_DIR / "results.json").read_text())["results"]
    return {"requests": requests, "results": results}


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_recorded_failure_evidence_is_untouched_by_the_replay() -> None:
    """The old failure keeps its exact bytes: a replay never rewrites the evidence it reads."""
    sums = {}
    for line in (RUN_DIR / "SHA256SUMS").read_text().splitlines():
        digest, _, name = line.partition("  ")
        sums[name.strip()] = digest.strip()
    before = {}
    for name in CASE_FILES:
        path = CASE_DIR / name
        before[name] = _hash(path)
        assert sums[f"cases/GUARD_T033/{name}"] == before[name], f"{name} does not match the run's own SHA256SUMS"
    # Consume the pair the way the new chain does.
    _ = json.loads((CASE_DIR / "requests.json").read_text())
    _ = json.loads((CASE_DIR / "results.json").read_text())
    for name in CASE_FILES:
        assert _hash(CASE_DIR / name) == before[name], "reading the evidence must not change it"


def test_the_recorded_return_is_read_through_the_published_paths(driver, recorded) -> None:
    """The old envelope carries success/error/execution: the reader must find the error code."""
    result = recorded["results"][0]
    payload = dict(result["structuredContent"])
    # The recorded file keeps the decoded content twice (a text body and structuredContent): the
    # replay reads the decoded one and leaves the text copy as the record's own copy.
    inner = json.loads(result["content"][0]["text"])
    assert payload == inner
    witness = driver.unpack_envelope(result["operation"], payload)
    assert witness.success is False
    assert witness.error_code == "EXECUTION_STATE_UNKNOWN"
    assert witness.job_id is None, "the recorded refusal names no job of its own"
    assert witness.source["success"] == "payload.success" and witness.source["error"] == "payload.error"
    assert payload["execution"]["request_id"] == OLD_KEY
    assert driver._unknown_outcome(payload) is not None, "an unknown engine state must be recognized"


def test_the_recorded_refusal_classifies_as_an_unfinished_job_not_a_contract_failure(driver, recorded) -> None:
    """``EXECUTION_STATE_UNKNOWN`` is BLOCKED, never a FAIL of the operation's contract."""
    payload = dict(recorded["results"][0]["structuredContent"])
    context = driver.ExecutionContext(run="phase4-driver5c")
    request = {"model_ref": {"session_id": "s1", "server_instance_id": "i1", "model_tag": "m1"},
               "execution": {"request_id": OLD_KEY}}
    row = context.observe("operation_describe", payload, request=request)
    assert row["error_code"] == "EXECUTION_STATE_UNKNOWN"
    # This recorded refusal names no job (it is the gate's answer to one operation): the context has
    # nothing to query, and it does not invent a job id to look busy with.
    assert context.evidence()["unfinished_jobs"] == []
    assert context.precheck({"execution": {"request_id": OLD_KEY}}, tool="geometry.build") is None
    # The classification of the recorded refusal: an external blocker that is *not* re-filed,
    # because nothing in it names a revision/signature/path defect.
    cause = driver.classify_first_cause({"status": "BLOCKED", "reason": row["error_code"],
                                         "error_code": row["error_code"]}, case_id="GUARD_T033")
    assert cause["class"] == "EXTERNAL_BLOCKER" and cause["guard"] is None
    outcome = driver._unknown_outcome(payload)
    assert outcome is not None
    assert outcome.get("requires_reconciliation") is not False, (
        "a recorded refusal that publishes no reconciliation flag must never be read as "
        "'reconciliation not needed'"
    )


def test_the_recorded_first_hand_unknown_names_the_job_that_blocks_the_gate(driver) -> None:
    """GUARD_T010's recorded UNKNOWN carries the job id: that job must be queried before anything new."""
    results = json.loads((GATE_DIR / "results.json").read_text())["results"]
    unknown = [row for row in results
               if ((row.get("structuredContent") or {}).get("error") or {}).get("code") == "EXECUTION_STATE_UNKNOWN"]
    assert unknown, "the recorded case must carry its first-hand UNKNOWN"
    payload = dict(unknown[0]["structuredContent"])
    job_id = payload["execution"]["job_id"]
    assert job_id and job_id != "None"
    context = driver.ExecutionContext(run="phase4-driver5c")
    row = context.observe(unknown[0]["operation"], payload,
                          request={"model_ref": {"session_id": "s1", "server_instance_id": "i1",
                                                 "model_tag": "m1"},
                                   "execution": {"request_id": payload["execution"]["request_id"]}})
    assert row["job_id"] == job_id
    assert context.evidence()["unfinished_jobs"] == [job_id]
    decision = context.precheck({"execution": {"request_id": "phase4-driver5cphase4-find-next"}},
                                tool="geometry.feature_create")
    assert decision is not None and decision["action"] == "query_first" and decision["reason"] == "unknown_job"
    assert decision["detail"]["unfinished_jobs"] == [job_id]
    # Only the published reconcile read may touch it, and its budget is bounded.
    assert context.precheck({"execution": {}}, tool="job_reconcile") is None
    assert context.query_budget(job_id) is True and context.query_budget(job_id) is True
    assert context.query_budget(job_id) is False
    context.resolve_unfinished(job_id, outcome="released")
    assert context.evidence()["unfinished_jobs"] == []
    # The recorded gate message is kept verbatim as the reason (no paraphrase, no rewrite).
    assert payload["error"]["message"] == "reconcile unfinished engine work before new operations"


def test_the_old_key_is_normalized_and_cannot_be_minted_again(driver, recorded) -> None:
    """The old shape is named for what it is: a constant per operation, with no per-request part."""
    key = recorded["requests"][0]["arguments"]["execution"]["idempotency_key"]
    assert key == OLD_KEY
    assert key.startswith("phase4-driver5c"), "the run prefix is the only unique part of the old key"
    stem = key[len("phase4-driver5c"):]
    assert stem == "phase4-describe-evaluate_expressions"
    assert not any(part.isdigit() for part in stem.split("-")), "no case/step/sequence anywhere"
    context = driver.ExecutionContext(run="phase4-driver5c")
    first = context.mint(case="phase4", step="describe-evaluate_expressions", body={"operation_id": "x"})
    second = context.mint(case="phase4", step="describe-evaluate_expressions", body={"operation_id": "x"})
    assert first.key != second.key != key
    assert first.sequence == 1 and second.sequence == 2
    assert first.key.endswith("-1") and second.key.endswith("-2")
    # A retry of the recorded request has no plan in this context, so the check is *skipped*, never
    # treated as a pass.
    assert context.reuse_for_retry(key, body={"operation_id": "x"}) is None
    assert [r for r in context.evidence()["replays"] if r.get("checked") is False]


def test_the_recorded_requests_are_usable_as_replay_input(driver, recorded) -> None:
    """Each recorded request has the fields the new chain keys and binds a body on."""
    for entry in recorded["requests"]:
        assert entry["operation"] and isinstance(entry["arguments"], dict)
        execution = entry["arguments"]["execution"]
        assert execution["idempotency_key"] == execution["request_id"] == OLD_KEY
        plan = driver.RequestPlan(run="phase4-driver5c", case="phase4", step="describe", sequence=1,
                                  key=execution["idempotency_key"], request_id=execution["request_id"],
                                  body_sha256=driver._body_sha256(entry["arguments"]), minted_at="0",
                                  explicit_key=True)
        assert plan.body_sha256 == driver._body_sha256(entry["arguments"]), "the body digest is stable"
        json.dumps(plan.as_dict(), default=str)
