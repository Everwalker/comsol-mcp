"""Offline regressions for the first live-run findings.

Every check here runs without COMSOL and without the ``--live`` flag:

* the engine-reconciliation gate (an ``EXECUTION_STATE_UNKNOWN`` envelope must be
  reconciled through the published ``job_reconcile`` tool, and a call the control plane
  *refused* must be replayed once under a fresh idempotency identity),
* the probe targets R01/R03/GUARD_T010/W15_T017 create for themselves instead of
  depending on a property that happens to exist,
* the payload vocabularies the live run sent wrong (transaction invariants, material
  readback, solver tree, guard denials).
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any, Mapping

import pytest

ROOT = Path(__file__).resolve().parents[1]
DRIVER_PATH = ROOT / "tools" / "phase4_run_mcp.py"
CATALOGUE = ROOT / "comsol_mcp" / "data" / "g2" / "02_ACTION_CATALOG.json"

#: The control-plane refusal message captured from the first live run.
GATE_MESSAGE = "reconcile unfinished engine work before new operations"

FAKE_CREDENTIAL = "AKIAphase4notarealcredential0"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def driver():
    return _load("phase4_driver_reconcile_probes", DRIVER_PATH)


def _envelope(operation_id: str, job_id: str, *, success: bool, code: str | None = None,
              message: str | None = None, data: Mapping[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "success": success,
        "data": dict(data or {}),
        "execution": {"idempotency_key": f"phase4-{operation_id}", "job_id": job_id,
                      "operation_id": f"op-{operation_id}", "request_hash": "0" * 64,
                      "request_id": f"phase4-{operation_id}"},
        "_outer_isError": not success,
    }
    if code is not None:
        payload["error"] = {"code": code, "message": message or code, "safe_retry": False}
    return payload


def _gate_refusal(operation_id: str, job_id: str) -> dict[str, Any]:
    """The envelope the control daemon returns while an unfinished job is unresolved."""
    return _envelope(operation_id, job_id, success=False, code="EXECUTION_STATE_UNKNOWN",
                     message=GATE_MESSAGE)


def _unknown_result(operation_id: str, job_id: str) -> dict[str, Any]:
    """The envelope a transaction receives when its own engine outcome is unknown."""
    return _envelope(operation_id, job_id, success=False, code="EXECUTION_STATE_UNKNOWN",
                     message="the engine did not report an outcome for this operation",
                     data={"execution_state_unknown": True, "status": "UNKNOWN",
                           "verification_status": "NOT_RUN", "requires_model_reconciliation": True})


def _reconciled(job_id: str, *, quiescent: bool = True) -> dict[str, Any]:
    return _envelope("job_reconcile", f"job-reconcile-{job_id}", success=True,
                     data={"job_id": job_id, "status": "UNKNOWN",
                           "metadata": {"reconciled_quiescent": quiescent}})


def _refreshed(job_id: str, revision: int, *, dirty: bool = False) -> dict[str, Any]:
    """The envelope a published ``model_inspect(refresh=true)`` reconcile read returns."""
    payload = _envelope("model_inspect", job_id, success=True)
    payload["execution"] = {**payload["execution"], "revision": revision, "dirty": dirty}
    return payload


class _RecordingHost:
    """A ``ProductionHost`` without its session: the reconcile hooks are exercised directly.

    The double mirrors the bookkeeping the driver's host keeps: the reconciliation records
    (``reconciliations``), the driver's own ledger of UNKNOWN jobs (``job_ledger`` /
    ``unresolved_jobs``), the gate refusals and the ``still_blocked`` rows, and the published
    ``model_inspect(refresh=true)`` reconcile reads (``refreshes``/``last_refresh``) that the
    managed-revision release path records and the release evidence reads back.  Its ``_call_once``
    feeds every answer through ``_observe_envelope`` exactly as the real one does.
    """

    def __init__(self, driver, responses, run_state=None):
        host = driver.ProductionHost.__new__(driver.ProductionHost)
        self.host = host
        self.responses = list(responses)
        self.calls: list[tuple[str, Mapping[str, Any]]] = []
        host.tools = {"job_reconcile": object(), "model_inspect": object(), "operation_describe": object(),
                      "session_health": object()}
        host.reconciliations = []
        host.refreshes = []
        host.last_refresh = None
        host.run_state = run_state
        host.job_ledger = {}
        host.unresolved_jobs = []
        host.gate_refusals = {"count": 0, "first": None, "last": None, "by_tool": {}}
        host.still_blocked = []
        host.last_readback = None
        host._reconciling = False
        host.reconcile_calls = 0
        host._call_once = self._call_once

    async def _call_once(self, name, arguments):  # noqa: ANN001
        self.calls.append((name, dict(arguments or {})))
        if name == "job_reconcile":
            self.host.reconcile_calls += 1
        if not self.responses:
            raise AssertionError(f"unexpected call to {name!r}: {json.dumps(dict(arguments or {}), default=str)[:400]}")
        payload = self.responses.pop(0)
        payload = {**payload, "_outer_isError": payload.get("_outer_isError", payload.get("success") is not True)}
        self.host._observe_envelope(name, payload)
        return payload


def _arguments(*, key: str, operation_id: str) -> dict[str, Any]:
    return {"execution": {"idempotency_key": key, "request_id": key},
            "arguments": {"operation_id": operation_id, "arguments": {"path": {"segments": []}}}}


# ---------------------------------------------------------------------------
# A) the control-plane reconciliation gate
# ---------------------------------------------------------------------------


def test_unknown_outcome_classification(driver):
    """The gate refusal and a first-hand unknown result are both recognised, with their job."""
    refusal = _gate_refusal("model_load", "job-refused")
    outcome = driver._unknown_outcome(refusal)
    assert outcome is not None and outcome["gate"] is True
    assert outcome["job_id"] == "job-refused"

    unknown = _unknown_result("operation_call", "job-unknown")
    outcome = driver._unknown_outcome(unknown)
    assert outcome is not None and outcome["gate"] is False
    assert outcome["job_id"] == "job-unknown"

    assert driver._unknown_outcome(_envelope("model_load", "job-ok", success=True)) is None
    plain = _envelope("model_load", "job-bad", success=False, code="INVALID_REQUEST", message="bad body")
    assert driver._unknown_outcome(plain) is None
    assert driver._envelope_job_id(refusal) == "job-refused"


def test_gate_refusal_is_reconciled_then_replayed_with_a_fresh_identity(driver):
    """A refused call is released through job_reconcile and replayed exactly once.

    The blocking job is one whose earlier reconciliation could not be confirmed (the worker
    was still working); the gate therefore refuses the next call until the release succeeds.
    """
    host = _RecordingHost(driver, [
        _unknown_result("operation_call", "job-unknown"),          # the operation's own result
        _reconciled("job-unknown", quiescent=False),                # still working: not released
        _gate_refusal("model_load", "job-refused"),                 # the next call hits the gate
        _reconciled("job-unknown"),                                 # now quiescent: released
        _envelope("model_load", "job-retried", success=True, data={"status": "APPLIED"}),
    ])

    first = asyncio.run(host.host.call("operation_call", _arguments(key="phase4-a", operation_id="geometry.build")))
    assert first["success"] is False and driver._unknown_outcome(first) is not None
    assert host.host.unresolved_jobs == ["job-unknown"]
    assert host.calls[1][0] == "job_reconcile" and host.calls[1][1]["job_id"] == "job-unknown"
    assert host.host.reconciliations[0]["released"] is False

    second = asyncio.run(host.host.call("model_load", {"execution": {"idempotency_key": "phase4-b"}}))
    assert second["success"] is True, "the refused call must be replayed after the gate was released"
    assert [name for name, _ in host.calls] == ["operation_call", "job_reconcile", "model_load",
                                                "job_reconcile", "model_load"]
    retry_arguments = host.calls[4][1]
    assert retry_arguments["execution"]["idempotency_key"] == "phase4-b-gate-retry"
    assert host.host.unresolved_jobs == []
    records = host.host.reconciliations
    assert len(records) == 2 and records[1]["released"] is True
    assert records[1]["retry"]["still_unknown"] is False and records[1]["retry"]["success"] is True
    json.dumps(records, default=str)


def test_a_first_hand_unknown_result_is_never_replayed(driver):
    """The operation's own unknown result is evidence: reconcile it, do not re-send it."""
    host = _RecordingHost(driver, [_unknown_result("operation_call", "job-unknown"),
                                   _reconciled("job-unknown")])
    payload = asyncio.run(host.host.call("operation_call", _arguments(key="phase4-c", operation_id="transaction.apply")))
    assert payload["success"] is False
    assert [name for name, _ in host.calls] == ["operation_call", "job_reconcile"]
    assert host.host.unresolved_jobs == [], "a reconciled job must not keep blocking the gate"
    record = host.host.reconciliations[0]
    assert record["gate"] is False and record["retry"].startswith("not performed")
    assert "this call's evidence" in record["retry"]
    assert record["released"] is True


def test_gate_without_a_release_keeps_the_original_result(driver):
    """A gate that does not open is reported as a rejection, never replayed blindly."""
    host = _RecordingHost(driver, [_gate_refusal("model_load", "job-refused"),
                                   _reconciled("job-unknown", quiescent=False)])
    host.host.unresolved_jobs = ["job-unknown"]
    payload = asyncio.run(host.host.call("model_load", {"execution": {"idempotency_key": "phase4-d"}}))
    assert driver._unknown_outcome(payload) is not None
    assert [name for name, _ in host.calls] == ["model_load", "job_reconcile"]
    record = host.host.reconciliations[0]
    assert record["released"] is False
    assert record["retry"].startswith("not performed")
    assert host.host.unresolved_jobs == ["job-unknown"]
    assert record["still_blocked"]["phase"] == "release-failed"
    assert host.host.still_blocked[-1]["tool"] == "model_load"
    assert host.host.gate_refusals["count"] == 1


def test_a_replayed_call_refused_again_is_recorded_as_still_blocked(driver):
    """The release opened the gate once; the replay was refused again: recorded, not retried again."""
    host = _RecordingHost(driver, [
        _gate_refusal("model_load", "job-refused"),
        _reconciled("job-unknown"),
        _gate_refusal("model_load", "job-refused-again"),
    ])
    host.host.unresolved_jobs = ["job-unknown"]
    payload = asyncio.run(host.host.call("model_load", {"execution": {"idempotency_key": "phase4-e"}}))
    assert driver._unknown_outcome(payload) is not None
    assert [name for name, _ in host.calls] == ["model_load", "job_reconcile", "model_load"]
    record = host.host.reconciliations[0]
    assert record["released"] is True and record["retry"]["still_unknown"] is True
    assert record["still_blocked"]["phase"] == "replay-gate-refused"
    assert host.host.still_blocked[-1]["job_id"] == "job-refused-again"
    assert host.host.gate_refusals["count"] == 2, "every refusal the control plane returned is counted"


def test_a_job_that_reported_quiescent_is_never_reconciled_again(driver):
    """A released job is not retried, and the ledger publishes itself into the run state."""
    state: dict[str, Any] = {}
    host = _RecordingHost(driver, [_unknown_result("operation_call", "job-unknown"),
                                   _reconciled("job-unknown"),
                                   _gate_refusal("model_load", "job-refused"),
                                   _envelope("session_health", "job-health", success=True,
                                             data={"status": "READY", "active_jobs": []})],
                          run_state=state)
    asyncio.run(host.host.call("operation_call", _arguments(key="phase4-f", operation_id="geometry.build")))
    assert state["unknown_jobs"]["released_jobs"] == ["job-unknown"]
    payload = asyncio.run(host.host.call("model_load", {"execution": {"idempotency_key": "phase4-g"}}))
    assert driver._unknown_outcome(payload) is not None
    assert [name for name, _ in host.calls] == ["operation_call", "job_reconcile", "model_load", "session_health"]
    assert host.host.reconcile_calls == 1, "a quiescent job must not keep the release path busy"
    record = host.host.reconciliations[-1]
    assert record["released"] is False and "no ledger job is unreleased" in record["job_reconcile"]
    assert record["discovery"]["active_jobs"] == [], "the published health read exposes no unresolved job"
    assert host.host.still_blocked[-1]["phase"] == "release-failed"
    ledger = state["unknown_jobs"]
    assert ledger["entries"][0]["job_id"] == "job-unknown" and ledger["entries"][0]["released"] is True
    assert ledger["gate_refusals"]["last"]["message"] == GATE_MESSAGE


def test_a_revision_conflict_replay_that_reports_unknown_is_ledgered_and_released(driver):
    """The live root cause: the UNKNOWN of a *replayed* write must reach the ledger.

    Observed in the third live run: the retry of a managed-revision conflict returned its own
    ``EXECUTION_STATE_UNKNOWN`` job, that job was never reconciled, and the control gate then
    refused every remaining call in the run.
    """
    host = _RecordingHost(driver, [
        _envelope("model_load", "job-stale", success=False, code="REVISION_CONFLICT",
                  message="expected_revision does not match managed revision"),
        _refreshed("job-refresh", 12),
        _unknown_result("operation_call", "job-replayed-unknown"),
        _reconciled("job-replayed-unknown"),
    ])
    request = {"execution": {"idempotency_key": "phase4-x", "request_id": "phase4-x",
                             "expected_revision": 11, "model_ref": {"model_tag": "mcp1"}}}
    payload = asyncio.run(host.host.call("model_load", request))
    assert driver._unknown_outcome(payload) is not None
    assert [name for name, _ in host.calls] == ["model_load", "model_inspect", "model_load", "job_reconcile"]
    assert host.calls[2][1]["execution"]["idempotency_key"] == "phase4-x-revision-retry"
    assert host.host.job_ledger["job-replayed-unknown"]["released"] is True
    assert host.host.unresolved_jobs == [], "the replayed write's own UNKNOWN job must not block the gate"
    record = host.host.reconciliations[-1]
    assert record["trigger"] == "managed revision conflict replay"
    assert record["targets"] == ["job-replayed-unknown"] and record["released"] is True


def test_a_gate_refusal_releases_every_ledger_job_then_replays_once(driver):
    """The refusal names only the job it refused; the ledger names the ones that block the gate."""
    host = _RecordingHost(driver, [
        _unknown_result("operation_call", "job-a"),
        _reconciled("job-a", quiescent=False),
        _unknown_result("operation_call", "job-b"),
        _reconciled("job-b", quiescent=False),
        _gate_refusal("model_load", "job-refused"),
        _reconciled("job-a"),
        _reconciled("job-b"),
        _envelope("model_load", "job-retried", success=True, data={"status": "APPLIED"}),
    ])
    asyncio.run(host.host.call("operation_call", _arguments(key="phase4-a", operation_id="geometry.build")))
    asyncio.run(host.host.call("operation_call", _arguments(key="phase4-b", operation_id="mesh.create")))
    assert host.host.unresolved_jobs == ["job-a", "job-b"]
    payload = asyncio.run(host.host.call("model_load", {"execution": {"idempotency_key": "phase4-c"}}))
    assert payload["success"] is True, "the refused call must be replayed after every ledger job was released"
    assert [name for name, _ in host.calls] == ["operation_call", "job_reconcile", "operation_call", "job_reconcile",
                                                "model_load", "job_reconcile", "job_reconcile", "model_load"]
    assert host.calls[-1][1]["execution"]["idempotency_key"] == "phase4-c-gate-retry"
    assert host.host.unresolved_jobs == []
    record = host.host.reconciliations[-1]
    assert record["gate"] is True and record["released"] is True
    assert record["targets"] == ["job-a", "job-b"] and record["retry"]["success"] is True
    assert "still_blocked" not in record, "a released and replayed call is not blocked"


def test_the_ledger_is_attached_to_the_case_evidence(driver):
    """Case documents and the run index carry the ledger: it is the only record of the gate's jobs."""
    case = driver.Case(case_id="LEDGER", package="X", acceptance=("G3",))
    host = _RecordingHost(driver, [_unknown_result("operation_call", "job-unknown"),
                                   _reconciled("job-unknown")])
    asyncio.run(host.host.call("operation_call", _arguments(key="phase4-h", operation_id="geometry.build")))
    driver._export_reconciliations(case, host.host)
    ledger = case.assertions["unknown_job_ledger"]
    assert ledger["released_jobs"] == ["job-unknown"]
    assert ledger["entries"][0]["operation"] == "operation_call"
    assert ledger["entries"][0]["observations"][0]["error_code"] == "EXECUTION_STATE_UNKNOWN"
    assert case.assertions["engine_reconciliations"], "the release records are exported as before"
    json.dumps(case.assertions["unknown_job_ledger"], default=str)


class _PreviewClient:
    """One fixed answer for the static-preview probe."""

    def __init__(self, payload):
        self.payload = payload

    async def action(self, operation, arguments, **kwargs):  # noqa: ANN001
        return self.payload


def test_static_preview_gate_refusal_is_blocked_not_a_preview_verdict(driver):
    """A gated preview never reached the validator: blocked, never "the preview did not reject"."""
    gated = _gate_refusal("transaction.preview", "job-refused")
    verdict, observed = asyncio.run(driver._preview_static_rejection(_PreviewClient(gated), [],
                                                                     ("INVALID_REQUEST",), "k"))
    assert verdict == driver.PREVIEW_BLOCKED and observed["gate"] is True
    reason = driver._preview_rejection_reason(verdict, observed, "an empty index array")
    assert "unknown engine state" in reason and "did not reject" not in reason
    assert "control plane" in reason

    unknown = _envelope("transaction.preview", "job-unknown", success=False, code="EXECUTION_STATE_UNKNOWN",
                        message="the preview could not establish its state")
    verdict, observed = asyncio.run(driver._preview_static_rejection(_PreviewClient(unknown), [],
                                                                     ("INVALID_REQUEST",), "k"))
    assert verdict == driver.PREVIEW_BLOCKED and observed["gate"] is False
    assert "unknown engine state" in driver._preview_rejection_reason(verdict, observed, "a probe")

    rejected = _envelope("transaction.preview", "job-preview", success=False, code="INVALID_REQUEST",
                         message="an empty index array is not accepted")
    verdict, observed = asyncio.run(driver._preview_static_rejection(_PreviewClient(rejected), [],
                                                                     ("INVALID_REQUEST",), "k"))
    assert verdict == driver.PREVIEW_REJECTED and observed["error_code"] == "INVALID_REQUEST"

    accepted = _envelope("transaction.preview", "job-preview-2", success=True, data={"static_only": True})
    verdict, observed = asyncio.run(driver._preview_static_rejection(_PreviewClient(accepted), [],
                                                                     ("INVALID_REQUEST",), "k"))
    assert verdict == driver.PREVIEW_UNESTABLISHED
    assert "did not reject" in driver._preview_rejection_reason(verdict, observed, "a probe")


def test_gate_refusal_without_a_known_blocking_job_records_the_gap(driver):
    """A refusal that names no blocking job is recorded as such instead of guessed around."""
    host = _RecordingHost(driver, [
        _gate_refusal("model_load", "job-refused"),
        _envelope("session_health", "job-health", success=True, data={"status": "READY", "active_jobs": []}),
        _reconciled("job-refused", quiescent=False),
    ])
    payload = asyncio.run(host.host.call("model_load", {"execution": {"idempotency_key": "phase4-e"}}))
    assert driver._unknown_outcome(payload) is not None
    record = host.host.reconciliations[0]
    assert record["discovery"]["available"] is True
    assert record["discovery"]["active_jobs"] == []
    assert "no published read lists unresolved jobs" in record["discovery"]["note"]
    assert record["released"] is False and record["retry"].startswith("not performed")


def test_a_stale_revision_refusal_is_released_through_the_recorded_refresh(driver):
    """The managed-revision release path: the reconcile read is recorded, then one replay.

    A write refused with ``REVISION_CONFLICT`` is released through the published
    ``model_inspect(refresh=true)`` read; that read is recorded on the host (``refreshes`` /
    ``last_refresh``) and the replay carries the revision the read reported, never the cached
    one.  The double has to keep the same bookkeeping or this path raises ``AttributeError``.
    """
    host = _RecordingHost(driver, [
        _envelope("model_load", "job-stale", success=False, code="REVISION_CONFLICT",
                  message="expected_revision does not match managed revision"),
        _refreshed("job-refresh", 12),
        _envelope("model_load", "job-retried", success=True, data={"status": "APPLIED"}),
    ])
    request = {"execution": {"idempotency_key": "phase4-r", "request_id": "phase4-r",
                             "expected_revision": 11, "model_ref": {"model_tag": "mcp1"}}}
    payload = asyncio.run(host.host.call("model_load", request))
    assert payload["success"] is True, payload
    assert [name for name, _ in host.calls] == ["model_load", "model_inspect", "model_load"]
    refresh = host.host.refreshes[-1]
    assert refresh["tool"] == "model_inspect" and refresh["refresh"] is True
    assert refresh["released"] is True and refresh["revision"] == 12
    assert host.host.last_refresh is refresh, "the last reconcile read must be the adopted one"
    retry = host.calls[2][1]
    assert retry["execution"]["idempotency_key"] == "phase4-r-revision-retry"
    assert retry["execution"]["expected_revision"] == 12
    record = host.host.reconciliations[-1]
    assert record["trigger"] == "managed revision conflict"
    assert record["precondition"] == "stale-expected-revision"
    assert record["refresh"]["revision"] == 12 and record["retry"]["success"] is True
    json.dumps(host.host.refreshes, default=str)


def test_job_reconcile_is_a_published_control_tool():
    """The release path is the product's own published read, not a private shim."""
    tools_control = _load("comsol_mcp_tools_control", ROOT / "comsol_mcp" / "_tools_control.py")
    published: set[str] = set()

    class _Registry:
        def add_tool(self, function):
            published.add(function.__name__)

    tools_control.register(_Registry())
    assert "job_reconcile" in published


# ---------------------------------------------------------------------------
# B) probe targets the driver creates for itself
# ---------------------------------------------------------------------------


class _FakeActionClient:
    """Records every call and answers with the envelopes a live engine would return."""

    def __init__(self, driver, *, create_status: str = "APPLIED"):
        self.driver = driver
        self.create_status = create_status
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def action(self, operation, arguments, **kwargs):  # noqa: ANN001
        self.calls.append((operation, dict(arguments)))
        tag = str(dict(arguments).get("tag") or "")
        if operation == "node.property_schema":
            return _envelope(operation, f"job-{operation}", success=True, data={"properties": [
                {"name": "hmax", "kind": "float64", "shape_rank": 0, "metadata_status": "KNOWN", "unit": "m"},
                {"name": "hauto", "kind": "int32", "shape_rank": 0, "metadata_status": "KNOWN"},
                {"name": "unknown_probe", "kind": "float64", "shape_rank": 0, "metadata_status": "UNKNOWN"},
            ]})
        if self.create_status == "ADOPTED":
            return _envelope(operation, f"job-{operation}", success=False, code="TAG_CONFLICT",
                             message=f"tag {tag!r} already exists")
        return _envelope(operation, f"job-{operation}", success=True, data={"tag": tag, "status": "APPLIED"})


def _fixture_calls(driver, client, state):
    args = driver.build_parser().parse_args([])
    fixture = asyncio.run(driver._probe_fixture(client, args, state))
    return fixture, client.calls


def test_probe_fixture_creates_its_targets_through_published_operations(driver):
    """Every fixture target comes from a published operation with a wire-valid payload."""
    client = _FakeActionClient(driver)
    state: dict[str, Any] = {}
    fixture, calls = _fixture_calls(driver, client, state)
    published = {row["operation_id"] for row in json.loads(CATALOGUE.read_text())["operations"]}
    used = {operation for operation, _ in calls}
    assert used, "the fixture must actually call the engine"
    assert used <= published, f"the fixture uses unpublished operations: {sorted(used - published)}"
    assert fixture.ready("material") and fixture.ready("temperature") and fixture.ready("component")
    assert fixture.verified_roots(), "the fixture must expose the paths it created"
    for operation, arguments in calls:
        for value in _walk(arguments):
            if isinstance(value, Mapping) and {"kind", "shape", "data"} <= set(value):
                assert isinstance(value["shape"], list) and "data" in value, (operation, value)
        for value in _walk(arguments):
            if isinstance(value, Mapping) and set(value) == {"collection", "tag"}:
                assert all(isinstance(item, str) and item for item in value.values()), (operation, value)
    json.dumps(fixture.evidence(), default=str)
    assert state["probe_fixture"]["built"] is True


def test_probe_fixture_adopts_existing_targets_after_a_reported_conflict(driver):
    """A tag conflict is adoption of an existing node, never a silent success."""
    client = _FakeActionClient(driver, create_status="ADOPTED")
    fixture, calls = _fixture_calls(driver, client, {})
    adopted = [row for row in fixture.evidence()["steps"] if row["status"] == "ADOPTED"]
    assert adopted, "a reported conflict must be recorded as an adoption"
    assert any(row["step"].endswith("_adopt") for row in fixture.evidence()["steps"]), (
        "the fixture must write the target it adopted")
    updated = {operation for operation, _ in calls if operation.endswith("_update") or operation.endswith("_set_properties")}
    assert {"geometry.feature_update", "material.set_properties"} & updated


def test_probe_fixture_group_schema_marks_only_known_properties(driver):
    """The material property group is read from the engine; UNKNOWN metadata is excluded."""
    client = _FakeActionClient(driver)
    fixture, _calls = _fixture_calls(driver, client, {})
    assert fixture.group_schema["success"] is True
    rows = fixture.group_candidates(("float64", "int32"))
    names = {row["name"] for row in rows}
    assert names == {"hmax", "hauto"}, f"the schema readback must gate the candidates: {names}"
    assert all(row["source"] == "probe_fixture" for row in rows)
    assert all(row["path"]["segments"][-1]["collection"] == "propertyGroup" for row in rows)


def test_probe_fixture_reports_a_refused_target_as_blocked(driver):
    """A fixture step the build cannot run is reported, not skipped silently."""
    client = _FakeActionClient(driver)

    async def _refusing(operation, arguments, **kwargs):
        client.calls.append((operation, dict(arguments)))
        return _envelope(operation, f"job-{operation}", success=False, code="EXECUTION_STATE_UNKNOWN",
                         message=GATE_MESSAGE)

    client.action = _refusing  # type: ignore[assignment]
    fixture, _calls = _fixture_calls(driver, client, {})
    statuses = {row["status"] for row in fixture.evidence()["steps"]}
    assert "BLOCKED" in statuses, statuses
    assert fixture.ready("material") is False


# ---------------------------------------------------------------------------
# C) transaction invariants are declared in the product's vocabulary
# ---------------------------------------------------------------------------


def test_transaction_invariants_use_the_product_vocabulary(driver):
    """``type`` (not ``kind``) plus a real engine action: the first live run sent both wrong."""
    path = {"segments": [{"collection": "component", "tag": "comp1"}]}
    declaration = driver._r04_invariant("node_exists", path)
    assert declaration == {"type": "node_exists", "path": path, "required": True}
    assert "kind" not in declaration
    action = driver._r04_probe_action(path)
    assert action["operation_id"] == "node.children", (
        "node.inspect on the model root is refused as an unknown execution state on this build")
    assert action["arguments"]["path"] == path


def test_violated_required_invariant_is_a_failed_verification(driver):
    """A violated required invariant: success=false, verification_status=FAILED, actions applied."""
    record = {
        "success": False,
        "error": {"code": "VERIFICATION_FAILED", "message": "invariant check 'node_exists' failed"},
        "data": {"execution_status": "SUCCEEDED", "verification_status": "FAILED",
                 "invariant_results": {"status": "FAIL", "counts": {"total": 1, "passed": 0, "failed": 1},
                                       "checks": [{"status": "FAIL", "required": True, "type": "node_exists",
                                                   "reason": "node_not_found"}]}},
    }
    status, reason, observed = driver._r04_violation_verdict(record)
    assert status == "PASS" and reason is None, observed
    assert observed["verification_status"] == "FAILED" and observed["execution_status"] == "SUCCEEDED"
    assert observed["failing_required_checks"] == [{"status": "FAIL", "required": True, "type": "node_exists",
                                                    "reason": "node_not_found"}]

    passing = {"success": True, "data": {"execution_status": "SUCCEEDED", "verification_status": "VERIFIED",
                                         "invariant_results": {"status": "VERIFIED", "checks": []}}}
    status, reason, _observed = driver._r04_violation_verdict(passing)
    assert status == "FAIL", "a passing verification must not satisfy the violation acceptance"

    status, reason, _observed = driver._r04_violation_verdict(_unknown_result("transaction.apply", "job-u"))
    assert status == "BLOCKED" and "unknown" in str(reason)


# ---------------------------------------------------------------------------
# D) material readback
# ---------------------------------------------------------------------------


def _material_write_ok(driver, payload_properties):
    return {"success": True, "data": {"applied": [{"name": row["name"], "readback": row["value"],
                                                   "metadata_status": "KNOWN", "readback_match": True}
                                                  for row in payload_properties]}}


def test_material_expression_round_trip_verdicts(driver):
    """The requested text must survive the readback; an unknown engine state is blocked."""
    requested = driver._material_expression_properties()
    heatcapacity = next(row for row in requested if row["name"] == "heatcapacity")
    assert "T" in heatcapacity["value"]["data"], "the probe must stay temperature dependent"

    ok = _material_write_ok(driver, requested)
    status, reason, _ = driver._material_property_verdict(ok, requested)
    assert status == "PASS" and reason is None

    dropped = {"success": True, "data": {"applied": [{"name": "heatcapacity", "readback_match": False},
                                                     {"name": "density", "readback_match": True},
                                                     {"name": "thermalconductivity", "readback_match": True}]}}
    status, reason, _ = driver._material_property_verdict(dropped, requested)
    assert status == "FAIL" and "heatcapacity" in str(reason)

    status, reason, _ = driver._material_property_verdict(_unknown_result("operation_call", "job-u"), requested)
    assert status == "BLOCKED", reason

    status, reason, _ = driver._material_property_verdict(
        _envelope("call", "job-r", success=False, code="PERMISSION_DENIED", message="denied"), requested)
    assert status == "BLOCKED", reason


def test_material_readback_verdict_matches_the_requested_text(driver):
    """The independent readback is compared against the requested text, not a substring."""
    requested = [{"name": "heatcapacity",
                  "value": {"kind": "expression", "shape": [],
                            "data": "500[J/(kg*K)]+0.1[J/(kg*K^2)]*(T-293.15[K])"}}]
    exact = _envelope("node.property_get", "job-1", success=True, data={"properties": [
        {"name": "heatcapacity", "value": requested[0]["value"]}]})
    status, reason, _ = driver._material_readback_verdict(exact, requested)
    assert status == "PASS" and reason is None

    normalized = _envelope("node.property_get", "job-2", success=True, data={"properties": [
        {"name": "heatcapacity",
         "value": {"kind": "expression", "shape": [],
                   "data": "500[J/(kg*K)]+0.1[J/(kg*K^2)]*(T-293.15[K])"}}]})
    status, _reason, _ = driver._material_readback_verdict(normalized, requested)
    assert status == "PASS"

    lost = _envelope("node.property_get", "job-3", success=True, data={"properties": [
        {"name": "heatcapacity", "value": {"kind": "expression", "shape": [], "data": "500[J/(kg*K)]"}}]})
    status, reason, detail = driver._material_readback_verdict(lost, requested)
    assert status == "FAIL" and "heatcapacity" in str(reason)
    assert detail["verdicts"]["heatcapacity"] == "not_preserved"

    missing = _envelope("node.property_get", "job-4", success=True, data={"properties": []})
    status, reason, _ = driver._material_readback_verdict(missing, requested)
    assert status == "FAIL" and "did not return" in str(reason)


# ---------------------------------------------------------------------------
# E) solver tree
# ---------------------------------------------------------------------------


def test_solver_tree_verdict(driver):
    """An empty solver list and a gated read are blocked; only a real tree passes."""
    solvers = [{"solver": "sol1", "study": "std1",
                "path": {"segments": [{"collection": "sol", "tag": "sol1"}]}}]
    status, reason, detail = driver._solver_tree_verdict(
        _envelope("solver.list", "job-1", success=True, data={"solvers": solvers, "solver_count": 1}))
    assert status == "PASS" and reason is None and detail["solver_count"] == 1

    status, reason, _ = driver._solver_tree_verdict(
        _envelope("solver.list", "job-2", success=True, data={"solvers": [], "solver_count": 0}))
    assert status == "BLOCKED" and "no solver sequence" in str(reason)

    status, reason, _ = driver._solver_tree_verdict(_unknown_result("solver.list", "job-3"))
    assert status == "BLOCKED"

    status, reason, _ = driver._solver_tree_verdict(
        _envelope("solver.list", "job-4", success=True, data={"solvers": [{"solver": "sol1"}]}))
    assert status == "FAIL" and "readable path" in str(reason)


def test_solver_feature_path_is_taken_from_the_inspect_payload(driver):
    payload = _envelope("solver.inspect", "job-1", success=True, data={"features": [
        {"path": {"segments": [{"collection": "sol", "tag": "sol1"}, {"collection": "feature", "tag": "st1"}]}},
        {"path": {"segments": [{"collection": "sol", "tag": "sol1"}, {"collection": "feature", "tag": "fc1"}]}},
    ]})
    found = driver._first_feature_path(payload)
    assert found == {"segments": [{"collection": "sol", "tag": "sol1"}, {"collection": "feature", "tag": "st1"}]}
    assert driver._first_feature_path(_envelope("solver.inspect", "job-2", success=True, data={})) is None


# ---------------------------------------------------------------------------
# F) guard denials
# ---------------------------------------------------------------------------


def test_denial_verdict_requires_an_explicit_refusal(driver):
    """A denial is explicit or blocked; a gated call is never read as "denied"."""
    denied = _envelope("docs.index", "job-1", success=False, code="PERMISSION_DENIED",
                       message="documentation source is outside the approved project roots")
    status, reason, detail = driver._denial_verdict(denied, "an outside source was accepted")
    assert status == "PASS" and reason is None
    assert detail["error_code"] == "PERMISSION_DENIED"

    gated = _gate_refusal("docs.index", "job-2")
    status, reason, _ = driver._denial_verdict(gated, "an outside source was accepted")
    assert status == "BLOCKED" and "unknown" in str(reason)

    accepted = _envelope("docs.index", "job-3", success=True, data={"indexed": 1})
    status, reason, _ = driver._denial_verdict(accepted, "an outside source was accepted")
    assert status == "FAIL"

    unnamed = _envelope("docs.index", "job-4", success=False, code="INTERNAL_ERROR", message="boom")
    status, reason, _ = driver._denial_verdict(unnamed, "an outside source was accepted")
    assert status == "FAIL" and "INTERNAL_ERROR" in str(reason)


def test_leak_scan_splits_driver_documents_from_a_caller_owned_runtime_home(driver, tmp_path):
    """Private paths in the driver's own documents fail; the runtime home is reported separately."""
    run_dir = tmp_path / "run"
    document = run_dir / "cases" / "GUARD_T035" / "assertions.json"
    document.parent.mkdir(parents=True)
    document.write_text(json.dumps({"note": f"index at {Path.home()}/.g3-private/x"}), encoding="utf-8")
    runtime = run_dir / "mcp-home" / "control-private"
    runtime.mkdir(parents=True)
    (runtime / "operations.sqlite3").write_text(f"path={Path.home()}/mcp-home/state\n", encoding="utf-8")

    scan = driver._leak_scan(run_dir, private_home=run_dir / "mcp-home")
    assert {hit["file"] for hit in scan["private_path_hits"]} == {"cases/GUARD_T035/assertions.json"}
    assert {hit["file"] for hit in scan["runtime_state_hits"]} == {"mcp-home/control-private/operations.sqlite3"}
    assert scan["private_home_scanned"].endswith("mcp-home")

    without_home = driver._leak_scan(run_dir)
    assert {hit["file"] for hit in without_home["private_path_hits"]} == {
        "cases/GUARD_T035/assertions.json", "mcp-home/control-private/operations.sqlite3"}
    assert without_home["runtime_state_hits"] == []


def test_credential_scan_never_treats_its_own_fixture_as_a_leak(driver, tmp_path):
    """The fixture literal is classified; the same literal anywhere else is a leak."""
    run_dir = tmp_path / "run"
    fixture = run_dir / "credentials_COMSOL_6.4.md"
    fixture.parent.mkdir(parents=True)
    fixture.write_text(driver.FAKE_CREDENTIAL, encoding="utf-8")
    leaked = run_dir / "cases" / "X" / "assertions.json"
    leaked.parent.mkdir(parents=True)
    leaked.write_text(json.dumps({"value": driver.FAKE_CREDENTIAL}), encoding="utf-8")

    classified = driver._leak_scan(run_dir)
    assert [hit["file"] for hit in classified["credential_fixture_hits"]] == [fixture.name]
    assert [hit["file"] for hit in classified["credential_leak_hits"]] == ["cases/X/assertions.json"]

    excluded = driver._leak_scan(run_dir, exclude={fixture.name})
    assert [hit["file"] for hit in excluded["credential_leak_hits"]] == ["cases/X/assertions.json"]
    assert excluded["credential_fixture_hits"] == []


def test_probe_fixture_payloads_validate_against_the_published_schemas(driver):
    """Every payload the fixture sends is validated against the published input schema.

    Only the operation's own fields are validated: the design catalogue declares the
    *envelope* identity as a string, while the G3 wire carries a structured model ref, so the
    envelope fields are asserted separately (present in the schema's property list).
    """
    import jsonschema
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    envelope = {"project_id", "session_id", "model_ref", "expected_revision", "idempotency_key", "request_id"}
    commons = json.loads((CATALOGUE.parent / "common.schema.json").read_text())
    registry = Registry().with_resource("common.schema.json", Resource.from_contents(commons, default_specification=DRAFT202012))
    catalogue = {row["operation_id"]: row for row in json.loads(CATALOGUE.read_text())["operations"]}
    client = _FakeActionClient(driver)
    _fixture, calls = _fixture_calls(driver, client, {})
    checked = 0
    for operation, arguments in calls:
        row = catalogue.get(operation)
        assert row is not None, f"the fixture sends an unpublished operation: {operation}"
        schema = row["input_schema"]
        properties = schema.get("properties") or {}
        extra = sorted(set(arguments) - set(properties))
        assert not extra, f"{operation} sends fields the published schema does not declare: {extra}"
        required = [key for key in schema.get("required", []) if key in properties and key not in envelope]
        missing = [key for key in required if arguments.get(key) is None]
        assert not missing, f"{operation} omits required fields: {missing}"
        body = {key: dict(arguments)[key] for key in arguments}
        jsonschema.Draft202012Validator({**schema, "required": required}, registry=registry).validate(body)
        checked += 1
    assert checked >= 8, f"the fixture payload schemas were not exercised: {checked}"


def test_probe_fixture_payloads_are_dispatched_by_the_product_code(driver):
    """The fixture's payloads reach the real operation code; only tree content can refuse them."""
    from comsol_mcp._execution_contract import ExecutionContractError
    from comsol_mcp._g3_ops import DISPATCH

    w14 = _load("g3_w14_fake_for_probes", ROOT / "tests" / "test_g3_w14.py")
    w15 = _load("g3_w15_fake_for_probes", ROOT / "tests" / "test_g3_w15.py")
    w16 = _load("g3_w16_fake_for_probes", ROOT / "tests" / "test_g3_w16.py")
    worker14, _, _ = w14.world(geometries={"p4geom": w14.geometry("p4geom")})
    worker15 = w15.worker_for(w15.build_model())
    worker16 = w16.worker_for(w16.build_model())
    workers = {"geometry": worker14, "mesh": worker16, "physics": worker15, "material": worker15,
               "definition": worker14}
    # A refusal that names why the *tree* could not serve the payload; anything else is a
    # payload-shape defect and fails this test.
    domain_refusals = {"NODE_NOT_FOUND", "TAG_CONFLICT", "TAG_EXISTS", "INVALID_NODE_PATH", "INVALID_REQUEST",
                       "API_UNSUPPORTED", "PROPERTY_UNKNOWN", "UNSUPPORTED_OPERATION", "INVALID_INVARIANT"}
    client = _FakeActionClient(driver)
    _fixture, calls = _fixture_calls(driver, client, {})
    dispatched: list[str] = []
    refused: dict[str, str] = {}
    for operation, arguments in calls:
        if operation not in DISPATCH:
            continue
        try:
            result = DISPATCH[operation](workers[operation.split(".", 1)[0]], "Model", dict(arguments))
        except ExecutionContractError as exc:
            assert exc.code in domain_refusals, f"{operation} refused the fixture payload with {exc.code}: {exc}"
            refused[operation] = exc.code
            continue
        assert isinstance(result, Mapping), f"{operation} did not return an envelope"
        dispatched.append(operation)
    assert len(dispatched) + len(refused) >= 8, f"the fixture payloads were not dispatched: {calls}"
    assert dispatched, f"no fixture payload was accepted by the product code: {refused}"


def test_probe_candidates_records_a_broken_fixture_without_raising(driver):
    """A fixture that cannot be built yields no candidates plus the recorded reason."""
    client = _FakeActionClient(driver)

    async def _explode(operation, arguments, **kwargs):
        raise RuntimeError("engine transport failed")

    client.action = _explode  # type: ignore[assignment]
    args = driver.build_parser().parse_args([])
    candidates, evidence = asyncio.run(driver._probe_candidates(client, args, {}, ("float64",)))
    assert candidates == []
    assert evidence["status"] == "FAILED" and "engine transport failed" in evidence["reason"]

    async def _missing(operation, arguments, **kwargs):
        raise driver.CapabilityUnavailable(f"{operation} is not available")

    client.action = _missing  # type: ignore[assignment]
    candidates, evidence = asyncio.run(driver._probe_candidates(client, args, {}, ("float64",)))
    assert candidates == [] and evidence["status"] == "BLOCKED"
    assert "not available" in evidence["reason"]


# ---------------------------------------------------------------------------
# H) the control gate never decides an acceptance line (R01 / GUARD_T033 live verdicts)
# ---------------------------------------------------------------------------


def _describe_ok(operation_id: str) -> dict[str, Any]:
    """The envelope ``operation_describe`` returns for an executable operation."""
    return {"success": True, "data": {"operation_id": operation_id, "executable": True,
                                      "implementation_status": "SUPPORTED_UNVERIFIED",
                                      "mcp_tool_name": operation_id.replace(".", "_"),
                                      "effect": "READ", "route": "strict",
                                      "input_schema": {"type": "object", "properties": {}},
                                      "wire_compatibility": {"execution_fields": []}}}


def _live_state() -> dict[str, Any]:
    return {"ref": {"session_id": "session-1", "server_instance_id": "instance-1", "model_tag": "mcp1",
                    "generation": 1, "schema_version": 1},
            "revision": 9, "model_origin": "created"}


class _GatedPropertyEngine:
    """An ``ActionClient`` double modelling the third live run's R01 scenario.

    A few typed properties answer normally; the unsupported *expression* write on a float64
    property returns its own ``EXECUTION_STATE_UNKNOWN`` job ("legacy callback raised after write
    dispatch"), the unit write is refused properly, and the non-finite probe is refused by the
    control gate — exactly the envelopes the live run recorded.
    """

    NODE = {"segments": [{"collection": "component", "tag": "comp1"},
                         {"collection": "geom", "tag": "g1"},
                         {"collection": "feature", "tag": "blk1"}]}
    SCHEMA = (
        {"name": "txt", "kind": "string", "shape_rank": 0, "metadata_status": "KNOWN"},
        {"name": "arr", "kind": "string", "shape_rank": 1, "metadata_status": "KNOWN"},
        {"name": "lx", "kind": "float64", "shape_rank": 0, "metadata_status": "KNOWN"},
        {"name": "tag", "kind": "expression", "shape_rank": 0, "metadata_status": "KNOWN", "unit": "K"},
    )

    def __init__(self, driver):
        self.driver = driver
        self.calls: list[tuple[str, Mapping[str, Any]]] = []
        self.store = {"txt": {"kind": "string", "shape": [], "data": "solid"},
                      "arr": {"kind": "string", "shape": [2], "data": ["solid", "solid"]},
                      "lx": {"kind": "float64", "shape": [], "data": 0.001},
                      "tag": {"kind": "expression", "shape": [], "data": "300[K]", "unit": "K"}}
        self.serial = 0

    def _answer(self, operation, *, success, code=None, message=None, data=None):  # noqa: ANN001
        self.serial += 1
        return _envelope(operation, f"job-{operation}-{self.serial}", success=success, code=code,
                         message=message, data=data)

    async def action(self, operation, arguments, **kwargs):  # noqa: ANN001
        arguments = dict(arguments or {})
        self.calls.append((operation, arguments))
        if operation == "node.find":
            return self._answer(operation, success=True,
                                data={"results": [{"path": dict(self.NODE), "type_id": "Block"}],
                                      "count": 1, "complete": True, "status": "COMPLETE"})
        if operation == "node.property_schema":
            return self._answer(operation, success=True, data={"properties": [dict(row) for row in self.SCHEMA]})
        if operation == "node.property_get":
            names = [str(name) for name in (arguments.get("names") or [])]
            rows = [{"name": name, "value": dict(self.store[name])} for name in names if name in self.store]
            return self._answer(operation, success=True, data={"properties": rows})
        if operation == "node.property_set":
            properties = list(arguments.get("properties") or [])
            entry = dict(properties[0]) if properties and isinstance(properties[0], Mapping) else {}
            name, value = entry.get("name"), dict(entry.get("value") or {})
            if name == "lx" and value.get("kind") == "expression":
                return self._answer(operation, success=False, code="EXECUTION_STATE_UNKNOWN",
                                    message="legacy callback raised after write dispatch")
            if name == "lx" and value.get("kind") == "float64" and value.get("data") in {"NaN", "Infinity"}:
                return self._answer(operation, success=False, code="EXECUTION_STATE_UNKNOWN", message=GATE_MESSAGE)
            if name == "lx" and value.get("unit"):
                return self._answer(operation, success=False, code="PROPERTY_TYPE_MISMATCH",
                                    message="unit may only accompany expression/string typed values")
            stored = dict(self.store.get(name) or {})
            return self._answer(operation, success=True, data={"applied": [{
                "name": name, "readback_match": stored.get("data") == value.get("data"),
                "comparison": {"rule": "exact_text_expression_string_mapping"},
                "readback": stored}], "applied_count": 1})
        if operation == "transaction.preview":
            return self._answer(operation, success=False, code="PROPERTY_TYPE_MISMATCH",
                                message="typed value must be an object")
        raise AssertionError(f"unexpected operation {operation}")


def test_r01_negative_probes_are_blocked_by_the_gate_not_failed(driver):
    """The gate and an unknown engine state never decide an R01 acceptance line.

    Third live run: both negative probes were reported FAIL ("not rejected") although their
    envelopes only said the engine state was unknown.  The positive R01 lines keep their truth.
    """
    case = driver.Case(case_id="R01_LIVE", package="R", acceptance=("G3 §7 R01",))
    args = driver.build_parser().parse_args(["--live"])
    state = _live_state()
    host = _RecordingHost(driver, [_describe_ok(operation) for operation in driver._plan_ops("R01_LIVE")])
    client = _GatedPropertyEngine(driver)
    asyncio.run(driver._case_r01(host.host, client, case, args, state))
    statuses = {name: row["status"] for name, row in case.subcases.items()}
    assert statuses["incompatible_kind_or_unit_rejected_before_write"] == "BLOCKED"
    assert statuses["nonfinite_text_strictly_rejected"] == "BLOCKED"
    reason = case.subcases["incompatible_kind_or_unit_rejected_before_write"]["reason"]
    assert "unknown engine state" in reason and "was not rejected" not in reason
    assert case.assertions["r01_incompatible"]["expression_unknown_engine_state"]["gate"] is False
    assert case.assertions["r01_nonfinite"]["nan_unknown_engine_state"]["gate"] is True
    # The lines the engine really answered keep their verdicts: the gate did not blank the case.
    assert statuses["expression_string_same_text_verified"] == "PASS"
    assert statuses["expression_array_or_matrix_verified"] == "PASS"
    assert statuses["unit_expression_text_preserved"] == "PASS"
    assert statuses["non_matching_readback_not_silently_accepted"] == "PASS"
    assert case.status == "BLOCKED"
    json.dumps(case.assertions, default=str)


class _GatedEvaluationEngine:
    """``evaluate_expressions`` as the control gate answers it: refused before the engine."""

    def __init__(self):
        self.calls = 0
        self.operations: list[str] = []

    async def action(self, operation, arguments, **kwargs):  # noqa: ANN001
        self.calls += 1
        self.operations.append(operation)
        return _gate_refusal(operation, f"job-refused-{self.calls}")


def test_guard_t033_live_subcases_are_blocked_when_the_gate_refuses(driver):
    """T033's live lines get the T035 treatment: a gated evaluation is neither FAIL nor PASS."""
    case = driver.Case(case_id="GUARD_T033", package="GUARD", acceptance=("G3 §7 GUARD.T033",))
    args = driver.build_parser().parse_args(["--live"])
    state = _live_state()
    host = _RecordingHost(driver, [_describe_ok("evaluate_expressions")])
    client = _GatedEvaluationEngine()
    asyncio.run(driver._case_guard_t033(host.host, client, case, args, state))
    statuses = {name: row["status"] for name, row in case.subcases.items()}
    assert statuses["pure_read_rejects_or_isolates"] == "BLOCKED"
    assert statuses["ephemeral_mutation_recorded_and_serial"] == "BLOCKED"
    assert statuses["only_own_temporary_nodes_cleaned"] == "BLOCKED"
    assert statuses["static_evaluation_policy_documented"] == "BLOCKED"
    assert "unknown engine state" in case.subcases["ephemeral_mutation_recorded_and_serial"]["reason"]
    # C04: the expression kinds are pre-registered and each is called at most once; the two kinds
    # whose routing condition a gate-refused discovery cannot establish stay NOT_RUN instead of
    # being sent as guesses.  Exactly one evaluation per routed kind, never a retry.
    assert client.operations.count("evaluate_expressions") == 5
    kinds = {row["kind"]: row["verdict"] for row in case.assertions["t033_expression_kinds"]}
    assert kinds["constant"] == "UNKNOWN" and kinds["illegal_expression"] == "UNKNOWN"
    assert kinds["model_expression"] == "NOT_RUN" and kinds["solved_field"] == "NOT_RUN"
    assert statuses["evaluation_expression_kinds_routed"] == "BLOCKED"
    assert case.status == "BLOCKED"


class _GatedPreviewClient:
    """Every static preview the case makes is refused by the control gate."""

    def __init__(self):
        self.calls = 0

    async def action(self, operation, arguments, **kwargs):  # noqa: ANN001
        self.calls += 1
        return _gate_refusal(operation, f"job-preview-{self.calls}")


def test_r04_static_invariant_probe_is_blocked_when_the_gate_refuses(driver):
    """R04's live blocker: the probes were gate-refused, not "rejected with odd codes"."""
    case = driver.Case(case_id="R04_LIVE", package="R", acceptance=("G3 §7 R04",))
    args = driver.build_parser().parse_args([])
    state = _live_state()
    host = _RecordingHost(driver, [_describe_ok(operation) for operation in driver._plan_ops("R04_LIVE")])
    client = _GatedPreviewClient()
    asyncio.run(driver._case_r04(host.host, client, case, args, state))
    row = case.subcases["static_unsupported_invariant_rejected_pre_write"]
    assert row["status"] == "BLOCKED"
    assert "unknown engine state" in row["reason"] and "documented vocabulary" not in row["reason"]
    detail = case.assertions["r04_unsupported_invariant_preview"]
    assert detail["per_probe"] == {"malformed": "BLOCKED", "unsupported": "BLOCKED"}
    assert detail["verdict"] == "BLOCKED" and client.calls == 2
    assert detail["unknown_engine_state"]["malformed"]["gate"] is True


def test_a_gated_tree_read_never_makes_the_tree_diff_a_pass(driver):
    """T005's live lines: two unreadable trees compare equal, which is not a pass."""
    case = driver.Case(case_id="TREE", package="X", acceptance=("G3",))
    assert driver._tree_read_blocked(case, "temporary_nodes_cleaned",
                                     {"before": None, "cleanup": _gate_refusal("model_tree", "job-tree")}) is True
    row = case.subcases["temporary_nodes_cleaned"]
    assert row["status"] == "BLOCKED" and "unknown engine state" in row["reason"]
    assert row["unknown_engine_state"]["gate"] is True

    clean = driver.Case(case_id="TREE2", package="X", acceptance=("G3",))
    read = {"success": True, "data": {"tree": []}, "execution": {"job_id": "job-tree-ok"}}
    assert driver._tree_read_blocked(clean, "temporary_nodes_cleaned", {"before": read, "cleanup": read}) is False
    assert "temporary_nodes_cleaned" not in clean.subcases, "an ordinary read keeps the diff's own verdict"


def _walk(value: Any):
    if isinstance(value, Mapping):
        yield value
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk(item)


# ---------------------------------------------------------------------------
# H) the fifth live run's long tail: adoption, published shapes and derivations
# ---------------------------------------------------------------------------


def _ref(model_tag: str = "mcp13", generation: int = 1) -> dict[str, Any]:
    return {"session_id": "session-1", "server_instance_id": "instance-1", "model_tag": model_tag,
            "generation": generation, "schema_version": 1}


def _stale_refusal(operation_id: str, job_id: str) -> dict[str, Any]:
    return _envelope(operation_id, job_id, success=False, code="REVISION_CONFLICT",
                     message="expected_revision does not match managed revision")


def _external_change(operation_id: str, job_id: str) -> dict[str, Any]:
    return _envelope(operation_id, job_id, success=False, code="REVISION_CONFLICT",
                     message="external model change requires reconciliation")


def _applied(operation_id: str, job_id: str) -> dict[str, Any]:
    return _envelope(operation_id, job_id, success=True, data={"status": "APPLIED"})


def _with_revision(payload: dict[str, Any], revision: int, *, dirty: bool = False,
                   ref: Mapping[str, Any] | None = None) -> dict[str, Any]:
    payload["execution"] = {**payload["execution"], "revision": revision, "dirty": dirty}
    if ref is not None:
        payload["execution"]["model_ref"] = dict(ref)
    return payload


def _action_client(driver, host, state, *, project_id: str = "phase4"):
    host.tools = {**host.tools, "operation_call": object()}
    host.transcript = []
    return driver.ActionClient(host, types.SimpleNamespace(project_id=project_id), state)


def test_every_reconcile_read_uses_its_own_idempotency_identity(driver):
    """One key for every reconcile read made the daemon replay the *first* stored answer.

    The fifth live run recorded 50 of its 52 ``model_inspect(refresh=true)`` reads refused with
    ``IDEMPOTENCY_CONFLICT``: the daemon answered from its store instead of reading the engine, so
    no release ever adopted a new revision and every later write was refused as stale.  Each read
    is a fresh question and therefore carries its own identity.
    """
    host = _RecordingHost(driver, [
        _stale_refusal("model_load", "job-a"), _refreshed("job-refresh-a", 12),
        _applied("model_load", "job-a-retry"),
        _external_change("model_load", "job-b"), _refreshed("job-refresh-b", 13),
        _applied("model_load", "job-b-retry"),
    ])
    request = {"execution": {"idempotency_key": "phase4-x", "request_id": "phase4-x",
                             "expected_revision": 11, "model_ref": _ref()}}
    for _ in range(2):
        payload = asyncio.run(host.host.call("model_load", dict(request)))
        assert payload["success"] is True, payload
    keys = [call["execution"]["idempotency_key"] for name, call in host.calls if name == "model_inspect"]
    assert len(keys) == 2, host.calls
    assert len(set(keys)) == 2, f"a reused reconcile key is replayed, never answered again: {keys}"
    assert [row["revision"] for row in host.host.refreshes] == [12, 13]
    assert [row["sent_expected_revision"] for row in host.host.refreshes] == [11, 11]


def test_a_refused_call_adopts_the_revision_its_own_reconcile_read(driver):
    """A refusal carries no readback: the release's reconcile read is what the cache must adopt.

    Without the adoption the *next* call compares against the revision the run held before the
    release — the live tail where one refused call was followed by another refusal for the same
    reason (W13_T006/T015/T048/T016, W14_T009/T034, W15_T007/T017, R01/R03/R04).
    """
    state = {"ref": _ref(), "revision": 11}
    host = _RecordingHost(driver, [
        _external_change("operation_call", "job-refused"),
        _refreshed("job-refresh", 12),
        _applied("operation_call", "job-retried"),
        _applied("operation_call", "job-next"),
    ])
    client = _action_client(driver, host.host, state)
    body = {"path": {"segments": [{"collection": "component", "tag": "comp1"}]}, "names": ["lx"]}
    first = asyncio.run(client.action("node.property_set", body, key="k1", request="r1"))
    assert first["success"] is True, first
    assert state["revision"] == 12, "the release's own reconcile read is the adopted revision"
    second = asyncio.run(client.action("node.property_set", body, key="k2", request="r2"))
    assert second["success"] is True, second
    sent = [call["execution"]["expected_revision"] for name, call in host.calls if name == "operation_call"]
    assert sent == [11, 12, 12], f"the call after the release must not compare against a stale revision: {sent}"
    retry = host.calls[2][1]
    assert retry["execution"]["idempotency_key"] == "k1-revision-retry"


def test_a_call_never_adopts_another_calls_reconcile_read(driver):
    """Only the reconcile read of the call now returning is adopted (the revision never rewinds).

    The release at revision 20 belongs to the *first* call.  The second call reads revision 21 off
    its own envelope and must keep it: adopting the older ``last_refresh`` would rewind the cache
    and make the next write stale again.
    """
    state = {"ref": _ref(), "revision": 11}
    host = _RecordingHost(driver, [
        _external_change("operation_call", "job-refused"),
        _refreshed("job-refresh", 20),
        _applied("operation_call", "job-retried"),
        _with_revision(_envelope("operation_call", "job-read", success=True, data={"status": "READ"}), 21,
                       ref=_ref()),
    ])
    client = _action_client(driver, host.host, state)
    body = {"path": {"segments": [{"collection": "component", "tag": "comp1"}]}, "names": ["lx"]}
    assert asyncio.run(client.action("node.property_set", body, key="k1", request="r1"))["success"] is True
    assert state["revision"] == 20
    assert asyncio.run(client.action("node.property_get", body, key="k2", request="r2"))["success"] is True
    assert state["revision"] == 21, "the call's own readback is newer than an earlier call's refresh"


def test_a_direct_read_keeps_the_cached_revision_current(driver):
    """A read a case makes through ``host.call`` is an engine call too: its revision is adopted.

    The chain and probe steps read the model tree directly, which is how the live run left a stale
    revision behind after a *successful* step and had the next call refused.
    """
    state = {"ref": _ref(), "revision": 11}
    host = _RecordingHost(driver, [
        _with_revision(_envelope("model_tree", "job-tree", success=True, data={"components": ["comp1"]}),
                       14, ref=_ref()),
        _applied("operation_call", "job-next"),
    ])
    client = _action_client(driver, host.host, state)
    asyncio.run(host.host.call("model_tree", {"depth": 2, "execution": {
        "idempotency_key": "phase4-tree", "request_id": "phase4-tree", "expected_revision": 11,
        "model_ref": _ref()}}))
    assert host.host.last_readback["revision"] == 14
    body = {"path": {"segments": [{"collection": "component", "tag": "comp1"}]}, "names": ["lx"]}
    payload = asyncio.run(client.action("node.property_set", body, key="k2", request="r2"))
    assert payload["success"] is True, payload
    assert host.calls[-1][1]["execution"]["expected_revision"] == 14, host.calls[-1][1]["execution"]


def test_the_engine_published_rank_decides_the_material_write_shape(driver):
    """The engine's own ``getValueType`` rank is authoritative and build dependent.

    Chain A's ``material.create`` wrote the Programming Guide's 3x3 ``thermalconductivity`` and this
    build's engine refused it ("property expects array rank 1, received 2"): only the *values* may
    be re-shaped, and only into the rank the refusal names.
    """
    refusal = {"error": {"message": "property expects array rank 1, received 2"}}
    assert driver._engine_expected_rank({"data": {"failed": [refusal]}}) == 1
    assert driver._engine_expected_rank({"data": {"failed": [{"error": {"message": "nope"}}]}}) is None
    matrix = {"name": "thermalconductivity",
              "value": {"kind": "expression", "shape": [3, 3], "unit": "W/(m*K)",
                        "data": [["10[W/(m*K)]", "0", "0"], ["0", "10[W/(m*K)]", "0"],
                                 ["0", "0", "10[W/(m*K)]"]]}}
    rows, note = driver._aligned_property_rows([matrix], 1)
    assert rows is not None and "flattened from rank 2 to rank 1" in note
    assert rows[0]["value"]["shape"] == [9]
    assert rows[0]["value"]["data"] == ["10[W/(m*K)]", "0", "0", "0", "10[W/(m*K)]", "0", "0", "0", "10[W/(m*K)]"]
    assert rows[0]["value"]["kind"] == "expression"
    # A scalar written next to a tensor keeps its own shape (the engine's rank belongs to the
    # property that refused), and a value with no faithful reshape is never invented.
    scalar = {"name": "density", "value": {"kind": "expression", "shape": [], "data": "1[kg/m^3]"}}
    rows, note = driver._aligned_property_rows([matrix, scalar], 1)
    assert rows is not None and rows[1]["value"]["shape"] == []
    assert "density: rank 0 is not aligned to the engine's rank 1, kept as it was" in note
    rows, note = driver._aligned_property_rows([scalar], 2)
    assert rows is not None and rows[0]["value"]["shape"] == []
    assert note.startswith("no property could be reshaped")
    anisotropic = {"name": "k", "value": {"kind": "expression", "shape": [3, 3],
                                          "data": [["1", "0", "0"], ["0", "2", "0"], ["0", "0", "3"]]}}
    rows, note = driver._aligned_property_rows([anisotropic], 0)
    assert rows is not None and rows[0]["value"]["shape"] == [3, 3]
    assert note.startswith("no property could be reshaped")
    isotropic = {"name": "k", "value": {"kind": "expression", "shape": [3, 3],
                                        "data": [["2", "0", "0"], ["0", "2", "0"], ["0", "0", "2"]]}}
    rows, note = driver._aligned_property_rows([isotropic], 0)
    assert rows is not None and rows[0]["value"]["data"] == "2" and rows[0]["value"]["shape"] == []


def test_a_refused_material_definition_is_reissued_in_the_engines_rank(driver):
    """The refusal is the metadata source: the refused properties are re-issued once, reshaped."""
    client = _FakeActionClient(driver)
    create = _envelope("material.create", "job-material", success=True, data={
        "status": "PARTIAL_FAILURE", "tag": "mat1",
        "failed": [{"action": "set_properties", "group": "def", "partial_change": True,
                    "error": {"code": "PROPERTY_TYPE_MISMATCH",
                              "message": "property expects array rank 1, received 2"},
                    "requested_properties": ["thermalconductivity", "density", "heatcapacity"]}],
        "applied": [{"action": "create", "tag": "mat1"}]})
    evidence = asyncio.run(driver._realign_refused_properties(
        client, create, path={"segments": []}, group="def",
        properties=driver.CHAIN_A_MATERIAL_PROPERTIES, key_stem="chain-a-material", request_stem="chain-a"))
    assert evidence is not None and evidence["aligned"] is True
    assert evidence["expected_rank"] == 1
    operation, arguments = client.calls[-1]
    assert operation == "material.set_properties" and arguments["group"] == "def"
    written = {row["name"]: row["value"] for row in arguments["properties"]}
    assert written["thermalconductivity"]["shape"] == [9], written["thermalconductivity"]
    assert written["density"]["shape"] == [], "an already-scalar property keeps its shape"
    assert len(arguments["properties"]) == len(driver.CHAIN_A_MATERIAL_PROPERTIES)
    # A complete definition is never rewritten, and a refusal that names no rank is not guessed at.
    assert asyncio.run(driver._realign_refused_properties(
        client, _applied("material.create", "job-ok"), path={"segments": []}, group="def",
        properties=driver.CHAIN_A_MATERIAL_PROPERTIES, key_stem="k", request_stem="r")) is None
    assert asyncio.run(driver._realign_refused_properties(
        client, _envelope("material.create", "job-x", success=False, code="NODE_NOT_FOUND",
                          message="no such node"), path={"segments": []}, group="def",
        properties=driver.CHAIN_A_MATERIAL_PROPERTIES, key_stem="k", request_stem="r")) is None


def test_an_engine_insulation_is_adopted_only_when_the_read_verifies_it(driver):
    """``ins1`` is refused because the engine created it; the refusal alone proves nothing.

    COMSOL's Heat Transfer interface brings its own Thermal Insulation feature, so chain A's create
    was refused with ``TAG_CONFLICT``.  The line may only rest on a *published read* that names an
    insulation feature at that tag.
    """
    class _InsulationClient:
        def __init__(self, results):
            self.driver = driver
            self.results = results
            self.calls: list[tuple[str, dict[str, Any]]] = []

        async def action(self, operation, arguments, **kwargs):  # noqa: ANN001
            self.calls.append((operation, dict(arguments)))
            return _envelope(operation, "job-read", success=True, data={"results": list(self.results)})

    refused = _envelope("physics.feature_create", "job-ins", success=False, code="TAG_CONFLICT",
                        message="tag 'ins1' already exists")
    verified = _InsulationClient([{"tag": "ins1", "type_id": "ThermalInsulation", "label": "Thermal Insulation"}])
    evidence = asyncio.run(driver._adopt_engine_insulation(
        verified, refused, physics_path={"segments": []}, tag="ins1", key_stem="chain-a-insulation",
        request_stem="chain-a"))
    assert evidence is not None and evidence["verified_as_insulation"] is True
    assert evidence["refused_with"] == "TAG_CONFLICT" and evidence["type_id"] == "ThermalInsulation"
    assert verified.calls[-1][0] == "node.find"
    assert verified.calls[-1][1]["query"] == {"tag": "ins1"}

    unverified = _InsulationClient([{"tag": "ins1", "type_id": "HeatSource"}])
    evidence = asyncio.run(driver._adopt_engine_insulation(
        unverified, refused, physics_path={"segments": []}, tag="ins1", key_stem="k", request_stem="r"))
    assert evidence is not None and evidence["verified_as_insulation"] is False
    assert "not established" in evidence["verdict"] or "did not report" in evidence["verdict"]
    # An unrelated refusal is not an adoption at all.
    assert asyncio.run(driver._adopt_engine_insulation(
        unverified, _envelope("physics.feature_create", "job-x", success=False, code="INVALID_REQUEST",
                              message="nope"), physics_path={"segments": []}, tag="ins1",
        key_stem="k", request_stem="r")) is None


def test_the_bound_geometry_tag_is_taken_from_the_models_own_tree(driver):
    """``mesh.create`` binds a sequence that must exist in the *bound* model, not in the CLI defaults.

    Live, T018's ``mesh.create`` was refused with ``NODE_NOT_FOUND`` ("geometry 'geom1' does not
    exist in component 'comp1'") because the bound model's sequence carried another tag.
    """
    class _TreeHost:
        def __init__(self, payload):
            self.tools = {"model_tree": object()}
            self.payload = payload
            self.calls: list[tuple[str, dict[str, Any]]] = []

        async def call(self, name, arguments, **kwargs):  # noqa: ANN001
            self.calls.append((name, dict(arguments)))
            return self.payload

    args = driver.build_parser().parse_args([])
    tree = _envelope("model_tree", "job-tree", success=True, data={"component_details": [
        {"tag": "comp1", "geometries": ["p4geom"], "materials": ["mat1"], "meshes": ["mesh1"],
         "physics": ["ht"], "studies": ["std1"]}]})
    host = _TreeHost(tree)
    client = types.SimpleNamespace(host=host, state={"ref": _ref(), "revision": 11})
    resolution = asyncio.run(driver._bound_geometry_tag(client, args))
    assert resolution["verified"] is True and resolution["tag"] == "p4geom"
    assert resolution["configured_tag"] == args.geometry_tag and resolution["tag"] != args.geometry_tag
    assert host.calls and host.calls[0][0] == "model_tree"

    host = _TreeHost(tree)
    resolution = asyncio.run(driver._bound_geometry_tag(types.SimpleNamespace(host=host, state={}), args))
    assert resolution["tag"] == "p4geom", "the model's own sequence wins over the configured tag"
    # A model that reports no sequence keeps the configured tag and says the resolution is unverified.
    host = _TreeHost(_envelope("model_tree", "job-tree", success=True,
                               data={"component_details": [{"tag": "comp1", "geometries": []}]}))
    resolution = asyncio.run(driver._bound_geometry_tag(types.SimpleNamespace(host=host, state={}), args))
    assert resolution["verified"] is False and resolution["tag"] == args.geometry_tag


def test_the_solver_updates_are_taken_from_the_engines_own_publish_table(driver):
    """The sub-feature and the property come from ``solver.inspect`` — never from a configured tag.

    Live, T020's update addressed a ``StudyStep`` with ``maxiter`` and was refused ("the feature does
    not support these fields"); the inspect payload publishes exactly which settings exist.
    """
    inspect = _envelope("solver.inspect", "job-solver", success=True, data={"features": [
        {"path": {"segments": [{"collection": "sol", "tag": "sol1"}, {"collection": "feature", "tag": "st1"}]},
         "tag": "st1", "type_id": "StudyStep", "label": "Step 1", "depth": 0,
         "settings": {"linpsol": "PARDISO", "tlist": "range(0,1,10)"},
         "settings_metadata": {"linpsol": {"value_type": "String", "kind": "string", "shape_rank": 0,
                                          "metadata_status": "KNOWN"},
                               "tlist": {"value_type": "DoubleArray", "kind": "string", "shape_rank": 1,
                                         "metadata_status": "KNOWN"}},
         "children": [
             {"path": {"segments": [{"collection": "sol", "tag": "sol1"}, {"collection": "feature", "tag": "st1"},
                                    {"collection": "feature", "tag": "se1"}]},
              "tag": "se1", "type_id": "Segregated", "label": "Stationary Solver", "depth": 1,
              "settings": {"rtol": 1e-6}, "settings_metadata": {
                  "rtol": {"value_type": "Double", "kind": "float64", "shape_rank": 0,
                           "metadata_status": "KNOWN"},
                  "message": {"value_type": "String", "kind": "string", "shape_rank": 0,
                              "metadata_status": "KNOWN"}},
              "children": [
                  {"path": {"segments": [{"collection": "sol", "tag": "sol1"},
                                         {"collection": "feature", "tag": "st1"},
                                         {"collection": "feature", "tag": "se1"},
                                         {"collection": "feature", "tag": "fc1"}]},
                   "tag": "fc1", "type_id": "FullyCoupled", "label": "Direct", "depth": 2,
                   "settings": {"maxiter": 8},
                   "settings_metadata": {"maxiter": {"value_type": "Int", "kind": "int32", "shape_rank": 0,
                                                    "metadata_status": "KNOWN"},
                                         "unknown_one": {"value_type": "?", "kind": "float64",
                                                         "shape_rank": 0, "metadata_status": "UNKNOWN"}},
                   "children": []}]}]}]})
    target = driver._solver_update_target(inspect)
    assert target is not None
    assert target["name"] == "maxiter" and target["kind"] == "int32" and target["value"] == 9
    assert target["path"]["segments"][-1] == {"collection": "feature", "tag": "fc1"}
    assert target["current"] == 8
    assert driver._solver_update_target(
        _envelope("solver.inspect", "job", success=True, data={"features": []})) is None


def test_the_solver_sequence_path_comes_from_the_solver_listing(driver):
    """``solver.inspect`` addresses a solver sequence: a study path is refused (``INVALID_NODE_PATH``).

    The live chain C sent ``study/std1`` and was refused, so the path is read from the published
    ``solver.list`` instead of being assembled from a configured tag.
    """
    listing = _envelope("solver.list", "job-list", success=True, data={"solvers": [
        {"solver": "sol1", "study": "std1",
         "path": {"segments": [{"collection": "sol", "tag": "sol1"}]}, "label": "Solver 1"}]})
    path, origin = driver._solver_sequence_path(listing, study="std1")
    assert path == {"segments": [{"collection": "sol", "tag": "sol1"}]}
    assert "published solver listing" in origin and "sol1" in origin
    assert driver._solver_sequence_path(
        _envelope("solver.list", "job", success=True, data={"solvers": []}), study="std1") == (
        None, "the published solver listing reported no solver sequence")
    assert driver._solver_sequence_path(
        _envelope("solver.list", "job", success=True, data={"solvers": [{"solver": "sol2", "study": "std1"}]}),
        study="std1")[0] is None


def test_a_control_plane_requery_reuses_the_same_identity(driver):
    """A "resubmit with the same idempotency key" envelope is re-asked — under that same key.

    The idempotency key is what makes the daemon answer with the stored result instead of executing
    twice, so re-asking is safe and is what the product's own message asks for.  Live, chain A's
    ``mesh.statistics`` stayed unresolved although the message said how to resolve it.  Every other
    first-hand UNKNOWN keeps the rule: reconciled, never replayed.
    """
    host = _RecordingHost(driver, [
        _envelope("mesh.statistics", "job-unknown", success=False, code="EXECUTION_STATE_UNKNOWN",
                  message="Control response unavailable; query or resubmit with the same idempotency key to reconcile."),
        _reconciled("job-unknown"),
        _envelope("mesh.statistics", "job-unknown", success=True, data={"statistics": {"elements": 812}}),
    ])
    request = {"execution": {"idempotency_key": "phase4-chain-a-stats", "request_id": "phase4-chain-a-stats",
                             "expected_revision": 5, "model_ref": _ref()}}
    payload = asyncio.run(host.host.call("mesh.statistics", dict(request)))
    assert payload["success"] is True and payload["data"]["statistics"]["elements"] == 812
    keys = [call["execution"]["idempotency_key"] for _, call in host.calls if _ == "mesh.statistics"]
    assert keys == ["phase4-chain-a-stats", "phase4-chain-a-stats"], keys
    assert host.host.requeries and host.host.requeries[-1]["outcome"]["success"] is True
    assert host.host.reconciliations, "the UNKNOWN job is still reconciled, so the gate re-opens"

    # A different first-hand UNKNOWN (no instruction) is never re-asked.
    host = _RecordingHost(driver, [
        _unknown_result("operation_call", "job-plain"), _reconciled("job-plain")])
    request = {"execution": {"idempotency_key": "phase4-plain", "request_id": "phase4-plain",
                             "expected_revision": 5, "model_ref": _ref()}}
    payload = asyncio.run(host.host.call("operation_call", dict(request)))
    assert payload["success"] is False and payload["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert [name for name, _ in host.calls] == ["operation_call", "job_reconcile"]


def test_the_probe_fixture_is_rebuilt_when_its_model_is_no_longer_bound(driver):
    """The fixture belongs to the model it was created in: a rebind makes its paths unresolvable.

    Live, GUARD_T010 reused the suite fixture while a case's own model was bound, so every ``p4*``
    path resolved to ``NODE_NOT_FOUND`` and the probe reported "no scalar property could be
    discovered or created".
    """
    client = _FakeActionClient(driver)
    state: dict[str, Any] = {"ref": _ref("mcp13")}
    args = driver.build_parser().parse_args([])
    asyncio.run(driver._probe_fixture(client, args, state))
    built_calls = len(client.calls)
    assert state["probe_fixture"]["model_ref"]["model_tag"] == "mcp13"

    state["ref"] = _ref("mcp14", generation=2)
    fixture = asyncio.run(driver._probe_fixture(client, args, state))
    assert len(client.calls) > built_calls, "the fixture must be recreated in the newly bound model"
    assert fixture.evidence()["model_ref"]["model_tag"] == "mcp14"
    records = [row for row in fixture.evidence()["steps"] if row.get("step") == "cache_invalidated"]
    assert records and records[-1]["status"] == "REBUILT" and "bound model changed" in records[-1]["reason"]

    # Still bound: one published read verifies the cache instead of recreating it.
    before = len(client.calls)
    again = asyncio.run(driver._probe_fixture(client, args, state))
    assert len(client.calls) == before + 1, "a bound fixture is verified with one read, not rebuilt"
    assert client.calls[-1][0] == "node.property_schema"
    assert again.ready("material") and again.verified_roots()


def test_the_license_probe_is_bound_to_the_model_it_reports_on(driver):
    """``runtime.license_inspect`` is routed through the ledger: without a ref it is refused first.

    Live, T042's second probe carried ``require_model=False`` and was refused with
    ``MODEL_IDENTITY_MISMATCH`` ("model_ref is required for this operation") *before* the product
    answered, so the acceptance line failed on the probe's routing instead of on the product's
    license answer.
    """
    source = Path(driver.__file__).read_text(encoding="utf-8")
    start = source.index('inspect_payload = await client.action("runtime.license_inspect"')
    assert "require_model=True" in source[start:start + 400], source[start:start + 400]


def test_the_blocked_license_verdict_names_the_product_answer(driver):
    """A blocked license probe reports the product's own message, not just its code."""
    status, reason, *_ = driver._check_license_probe(_envelope(
        "runtime.license_inspect", "job-license", success=False, code="BLOCKED_LICENSE",
        message="no non-checkout license answer was available for 1 of 1 requested product(s): HeatTransfer"))
    assert status == "BLOCKED"
    assert "HeatTransfer" in reason and reason.count("BLOCKED_LICENSE") == 1, reason
