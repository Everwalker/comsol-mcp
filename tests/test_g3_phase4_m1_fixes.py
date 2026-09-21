"""Offline regressions for the first M1 live gate (W13_T006, GUARD_T010, isolation restore).

The M1 run of 2026-09-20 (``evidence/phase4_1/runs/20260920T235502Z-g3_1-m1``) recorded three
driver-side defects.  Each one is pinned here against the run's *own* recorded material — which a
replay only ever reads, never rewrites — and against the product semantics that material exposes:

* **W13_T006** called ``variable.group_create(component='comp1')`` on the run's freshly created
  (empty) model.  The product answered, correctly, ``NODE_NOT_FOUND: component 'comp1' does not
  exist``, and the run filed it as a product gap (``IMPLEMENTATION_GAP``): the case never
  established the component prerequisite it addresses (G3.1 §4).  The case now creates *and reads
  back* its own component, and a component it cannot establish is ONE ``DEPENDENCY_BLOCKED`` root
  cause — never a defect of the operation that then refuses to address it.  The recorded refusal
  also publishes the product's own mutation witness (``mutation_issued: false``), which is what
  classifies it as NOT_EXECUTED instead of a first-hand unknown state that freezes the run.
* **GUARD_T010** retried ``node.property_set`` under the same idempotency key with a body it
  re-derived: the first write moved ``expected_revision`` 20 -> 21, the product hashes that field,
  and the answer was ``IDEMPOTENCY_CONFLICT`` — indistinguishable from the case's *own* conflict
  probe (recorded two sequences later).  The retry now resends the dispatched bytes verbatim, and
  the acceptance line compares the product's own operation identity.
* **Case isolation**: the run's shared model binding was captured *after* the isolation step, so the
  documented restore was a no-op and every later ``shared_bound`` case silently inherited the last
  isolated model (the M1 run's T010/T005 ran on R04's own, probe-dirtied model ``mcp2``).
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib.util
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping

import pytest

ROOT = Path(__file__).resolve().parents[1]
DRIVER_PATH = ROOT / "tools" / "phase4_run_mcp.py"
RUN_DIR = ROOT / "evidence" / "phase4_1" / "runs" / "20260920T235502Z-g3_1-m1"
T006_DIR = RUN_DIR / "cases" / "W13_T006_variables"
T010_DIR = RUN_DIR / "cases" / "GUARD_T010"
#: The M1c run (2026-09-21) — the second gate, whose two blocked cases are replayed below.
M1C_RUN_DIR = ROOT / "evidence" / "phase4_1" / "runs" / "20260921T001454Z-g3_1-m1c"
M1C_T006_DIR = M1C_RUN_DIR / "cases" / "W13_T006_variables"
M1C_T015_DIR = M1C_RUN_DIR / "cases" / "W13_T015_units"
#: The product's recorded answer to the M1c T006 component *list* request (sequence 7).
M1C_LIST_REFUSAL = "missing required operation arguments: tag"
CASE_FILES = ("assertions.json", "environment.json", "requests.json", "results.json")
#: The recorded refusal this module replays (``variable.group_create`` in W13_T006).
T006_REFUSAL_MESSAGE = "variable.group_create failed with NODE_NOT_FOUND (component 'comp1' does not exist)"
#: The product's answer to the recorded T010 "retry".
T010_CONFLICT_MESSAGE = "idempotency key was reused with a different request body"
LIVE_LINES = ("variables_two_in_one_group", "varnames_contains_modified_variable",
              "no_bogus_name_expr_variables", "expression_evaluates_after_modification",
              "component_and_global_scope")

pytestmark = pytest.mark.skipif(not RUN_DIR.is_dir(),
                                reason="the M1 run's evidence is not present in this checkout")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def driver():
    return _load("phase4_driver_m1_fixes", DRIVER_PATH)


def _case_material(case_dir: Path) -> dict[str, Any]:
    return {name.split(".")[0]: json.loads((case_dir / name).read_text(encoding="utf-8"))
            for name in CASE_FILES}


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digests(run_dir: Path) -> dict[str, str]:
    sums: dict[str, str] = {}
    for line in (run_dir / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, _, name = line.partition("  ")
        if name.strip():
            sums[name.strip()] = digest.strip()
    return sums


# ---------------------------------------------------------------------------
# A) the recorded M1 material: read, never rewritten
# ---------------------------------------------------------------------------


def test_the_recorded_m1_case_files_still_hash_as_the_run_says() -> None:
    """A replay reads the failure evidence; it never rewrites it."""
    sums = _digests(RUN_DIR)
    before: dict[str, str] = {}
    for case, case_dir in (("W13_T006_variables", T006_DIR), ("GUARD_T010", T010_DIR)):
        for name in CASE_FILES:
            key = f"cases/{case}/{name}"
            assert sums[key] == _hash(case_dir / name), f"{key} does not match the run's own SHA256SUMS"
            before[key] = sums[key]
    for key, digest in before.items():
        assert _hash(RUN_DIR / key) == digest, "reading the evidence must not change it"


def _t006_refusal(material: Mapping[str, Any]) -> dict[str, Any]:
    for row in material["results"]["results"]:
        payload = row.get("structuredContent") or {}
        message = str(((payload.get("error") or {}).get("message")) or "")
        if T006_REFUSAL_MESSAGE in message:
            return dict(payload)
    raise AssertionError("the recorded W13_T006 refusal is not in the material")


def test_the_recorded_node_not_found_refusal_is_not_executed_under_its_own_stage_evidence(driver) -> None:
    """C03: the product's own witness decides the stage — reads only, no mutation, NOT_EXECUTED.

    The recorded envelope wraps the operation's failure (``error.details.cause_code``) inside
    ``EXECUTION_STATE_UNKNOWN``.  Reading only the wrapper filed the call as a first-hand unknown
    state; with the published witness (``mutation_issued: false``, ``unproven_pre_dispatch:
    false``) it provably never changed anything.
    """
    payload = _t006_refusal(_case_material(T006_DIR))
    product = driver.product_dispatch_stage(payload)
    assert product is not None, "the recorded refusal publishes error.details"
    assert product["stage"] == "post_dispatch" and product["stage_known"] is True
    assert product["cause_code"] == "NODE_NOT_FOUND"
    assert product["cause_message"] == "component 'comp1' does not exist"
    assert product["mutation_issued"] is False and product["mutation_method"] is None
    assert product["engine_calls"] == 2 and product["methods"] == ["component", "tags"]
    assert product["unproven_pre_dispatch"] is False and product["proves_not_executed"] is True
    assert driver.observed_dispatch_stage(payload) == "dispatched_without_mutation"
    assert "dispatched_without_mutation" in driver.NOT_EXECUTED_STAGES
    # The driver-side reading of the same refusal keeps the cause and the product's own stage.
    context = driver.ExecutionContext(run="phase4-m1")
    evidence = context.refusal_evidence(payload)
    assert evidence["dispatch_stage"] == "dispatched_without_mutation"
    assert evidence["error_code"] == "EXECUTION_STATE_UNKNOWN"
    assert evidence["product_dispatch"]["cause_code"] == "NODE_NOT_FOUND"
    assert evidence["product_dispatch"]["stage"] == "post_dispatch"
    assert evidence["product_dispatch"]["source"] == "error.details"
    # An equivalent refusal *without* a witness proves nothing and stays unknown (never assumed).
    bare = {"success": False, "data": {},
            "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": "no witness published"}}
    assert driver.product_dispatch_stage(bare) is None
    assert driver.observed_dispatch_stage(bare) == "unknown"
    assert driver.ExecutionContext(run="t").refusal_evidence(bare)["dispatch_stage"] == "unknown"


def test_the_recorded_refusal_classifies_as_dependency_blocked_not_a_product_gap(driver) -> None:
    """The container the fixture never created is the case's dependency (C03), not a product gap."""
    payload = _t006_refusal(_case_material(T006_DIR))
    product = driver.product_dispatch_stage(payload) or {}
    message = str((payload.get("error") or {}).get("message") or "")
    cause = driver.classify_first_cause(
        {"status": "BLOCKED", "error_code": "EXECUTION_STATE_UNKNOWN",
         "cause_code": product.get("cause_code"), "reason": message}, case_id="W13_T006_variables")
    assert cause["class"] == "DEPENDENCY_BLOCKED"
    assert cause["cause_code"] == "NODE_NOT_FOUND" and cause["raised_code"] == "EXECUTION_STATE_UNKNOWN"
    assert cause["cause_source"] == "product error.details.cause_code"
    # The same wrapper *without* a nested cause keeps its own class: nothing is re-filed by wording.
    plain = driver.classify_first_cause({"status": "BLOCKED", "error_code": "EXECUTION_STATE_UNKNOWN",
                                        "reason": "the engine did not report an outcome"},
                                       case_id="W13_T006_variables")
    assert plain["class"] == "EXTERNAL_BLOCKER" and plain["cause_code"] is None
    # The recorded run's own index filed this refusal as IMPLEMENTATION_GAP: the correction is a
    # change of *classification*, and the original evidence is left exactly as it was.
    index = json.loads((RUN_DIR / "index.json").read_text(encoding="utf-8"))
    recorded = (index.get("first_cause") or {}).get("per_case", {}).get("W13_T006_variables", {})
    assert recorded.get("class") == "IMPLEMENTATION_GAP"
    assert "NODE_NOT_FOUND" in str(recorded.get("reason") or "")
    assert index["first_cause"]["counts"]["DEPENDENCY_BLOCKED"] == 0


def _t010_sequences(material: Mapping[str, Any], key_fragment: str) -> list[Mapping[str, Any]]:
    """The recorded ``operation_call`` requests under one key, in dispatch order.

    M1 recorded three of them: the first write (seq 139), the "retry" that re-derived its body
    (seq 141) and the case's own conflict probe (seq 143).
    """
    return [row for row in material["requests"]["requests"]
            if row.get("operation") == "operation_call"
            and ((row.get("arguments") or {}).get("operation_id") == "node.property_set")
            and key_fragment in json.dumps(row)]


def _t010_result(material: Mapping[str, Any], sequence: int) -> dict[str, Any]:
    for row in material["results"]["results"]:
        if row.get("sequence") == sequence:
            return dict(row["structuredContent"])
    raise AssertionError(f"no recorded result for sequence {sequence}")


def test_the_recorded_t010_retry_resent_a_rewritten_revision_not_the_request(driver) -> None:
    """The old "retry" differed from the dispatched body in ``expected_revision`` (20 -> 21).

    ``expected_revision`` is part of what the product hashes into its request identity, so its
    answer to the pair can only mean "a different request under this key" — a driver defect.
    """
    material = _case_material(T010_DIR)
    recorded = _t010_sequences(material, "guard-t010-repeat")
    assert len(recorded) == 3, [row["sequence"] for row in recorded]
    first, retry, probe = recorded
    first_arguments, retry_arguments = dict(first["arguments"]), dict(retry["arguments"])
    first_execution = dict(first_arguments["execution"])
    retry_execution = dict(retry_arguments["execution"])
    assert first_execution["idempotency_key"] == retry_execution["idempotency_key"]
    assert first_execution["request_id"] == retry_execution["request_id"], \
        "the pair is one logical request: same key, same request id"
    # The *only* difference is the revision the second call re-derived from its own state.
    assert first_execution["expected_revision"] == first_arguments["arguments"]["expected_revision"] == 20
    assert retry_execution["expected_revision"] == retry_arguments["arguments"]["expected_revision"] == 21
    trimmed = {**retry_arguments,
               "arguments": {**retry_arguments["arguments"], "expected_revision": 20},
               "execution": {**retry_execution, "expected_revision": 20}}
    assert trimmed == first_arguments, "the recorded pair differs in nothing but the revision"
    assert driver._body_sha256(driver._plan_body(retry_arguments)) != \
        driver._body_sha256(driver._plan_body(first_arguments))
    assert driver._body_sha256(driver._plan_body(trimmed)) == \
        driver._body_sha256(driver._plan_body(first_arguments))
    # A same-key call carrying that rewritten body is refused by the driver *before* dispatch ...
    context = driver.ExecutionContext(run=RUN_DIR.name)
    plan = context.mint(case="GUARD_T010", step="repeat", body=first_arguments, explicit_key="repeat-key")
    context.bind_body(plan.key, driver._plan_body(first_arguments))
    decision = context.reuse_for_retry(plan.key, body=driver._plan_body(retry_arguments))
    assert decision is not None and decision["reason"] == "external_observation"
    assert "a retry must resend the identical body" in decision["detail"]
    assert context.reuse_for_retry(plan.key, body=driver._plan_body(first_arguments)) is None
    # ... and the recorded answer to that call is the product's own conflict, not a replay.
    conflict = _t010_result(material, int(retry["sequence"]))
    assert ((conflict.get("error") or {}).get("code")) == "IDEMPOTENCY_CONFLICT"
    assert ((conflict.get("error") or {}).get("message")) == T010_CONFLICT_MESSAGE
    assert "execution" not in conflict, "a conflict publishes no operation identity at all"
    # The conflict is a *documented* pre-engine refusal: the daemon refused it before dispatch, so
    # nothing was executed — which is exactly why the old line, comparing a stamped identity with
    # nothing, could never establish a retry.
    assert driver.observed_dispatch_stage(conflict) == "refused_before_engine"
    assert driver.observed_dispatch_stage(conflict) in driver.NOT_EXECUTED_STAGES
    # The third call under the same key is the case's own conflict probe: a different body again.
    assert probe["arguments"]["arguments"].get("provenance") == {"phase4_conflict_probe": True}


def test_the_recorded_retry_and_the_conflict_probe_are_indistinguishable(driver) -> None:
    """The recorded "retry" (seq 141) and the case's conflict probe (seq 143) have identical answers.

    That is why the M1 run could not tell a true retry from a conflict: the "retry" *was* a conflict
    probe, so the acceptance line compared two identities of which the second was never stamped.
    """
    material = _case_material(T010_DIR)
    recorded = _t010_sequences(material, "guard-t010-repeat")
    retry, probe = recorded[1], recorded[2]
    assert probe["sequence"] > retry["sequence"], "the probe is the last call under this key"
    retry_body, probe_body = dict(retry["arguments"]), dict(probe["arguments"])
    assert probe_body["arguments"].get("provenance") == {"phase4_conflict_probe": True}
    assert retry_body["arguments"].get("provenance") is None, \
        "the old retry carried no provenance marker: it was the plain re-derived body"
    retry_answer, probe_answer = (_t010_result(material, int(row["sequence"]))
                                 for row in (retry, probe))
    assert retry_answer["error"] == probe_answer["error"]
    assert retry_answer["data"] == probe_answer["data"] == {}
    # The recorded run's own index filed the retry comparison as an implementation gap of the
    # product; the driver's own defect is what the correction removes.
    index = json.loads((RUN_DIR / "index.json").read_text(encoding="utf-8"))
    recorded = (index.get("first_cause") or {}).get("per_case", {}).get("GUARD_T010", {})
    assert recorded.get("class") == "IMPLEMENTATION_GAP"
    assert recorded.get("subcase") == "repeated_key_not_reexecuted"
    assert "different operation identity" in str(recorded.get("reason") or "")


def test_the_recorded_identity_is_the_one_the_product_stamped(driver) -> None:
    """The identity lives in ``execution``; ``data.operation_id`` is not an operation identity."""
    material = _case_material(T010_DIR)
    first = _t010_sequences(material, "guard-t010-repeat")[0]
    payload = _t010_result(material, int(first["sequence"]))
    assert driver.execution_identity(payload)["operation_id"] == payload["execution"]["operation_id"]
    identity = driver.execution_identity(payload)
    assert identity["job_id"] == payload["execution"]["job_id"]
    assert identity["request_hash"] == payload["execution"]["request_hash"]
    assert identity["operation_id"] and identity["source"], "the reader records where it read"
    assert "execution" in json.dumps(identity["source"])
    assert driver._envelope_identity(payload)["operation_id"] == identity["operation_id"]
    # A driver-shaped refusal carries the operation *name* in ``data.operation_id``: fallback only.
    synthetic = {"success": False, "data": {"operation_id": "variable.group_create"},
                 "error": {"code": "DRIVER_PRE_DISPATCH_REFUSAL"}, "execution": {}}
    assert driver.execution_identity(synthetic)["operation_id"] is None
    assert driver._envelope_identity(synthetic)["operation_id"] == "variable.group_create"


# ---------------------------------------------------------------------------
# B) the product semantics these two cases depend on, as a stdio double
# ---------------------------------------------------------------------------


def _envelope(*, success: bool, data: Mapping[str, Any] | None = None, code: str | None = None,
              message: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"success": success, "data": dict(data or {})}
    if code is not None:
        payload["error"] = {"code": code, "message": message or code, "safe_retry": False}
    return payload


def _refused_envelope(operation: str, code: str, message: str, *, stage: str = "validation",
                      mutation_issued: bool = False, details: bool = True) -> dict[str, Any]:
    """The *published* clean-refusal envelope, exactly as the product writes it.

    ``comsol_mcp._managed_backend._refusal_envelope`` publishes the declared stage and the
    mutation witness in both documented positions (``error.stage`` and ``data.dispatch_stage``/
    ``data.witness``, with ``data.refused: true``); ``tests/test_m1_product_repairs.py`` pins that
    writer, this module pins what the driver's own stage reader makes of it.  ``details`` models a
    raise that carries its own details for its own reason (the multi-geometry ``API_UNSUPPORTED``),
    which must not hide the refusal block's stage evidence.
    """
    refusal: dict[str, Any] = {"code": code, "message": message, "safe_retry": False, "stage": stage}
    if details:
        refusal["details"] = {"cause_code": code, "cause_message": message}
    return {
        "success": False,
        "error": refusal,
        "data": {"status": "REFUSED", "refused": True, "operation": operation, "dispatch_stage": stage,
                 "witness": {"engine_calls": 4, "methods": ["component", "get", "getType", "tags"],
                             "mutation_issued": mutation_issued, "mutation_method": None}},
        "execution_state_unknown": False,
    }


class _ProductEngine:
    """The control daemon's semantics, reduced to what W13_T006 and GUARD_T010 touch.

    In particular it keeps *one result per idempotency key*, validated by the request hash the
    product itself computes (``expected_revision`` included) — which is exactly why a same-key call
    with a re-derived body is answered with ``IDEMPOTENCY_CONFLICT`` instead of executing again.
    """

    NODE = {"segments": [{"collection": "component", "tag": "comp1"},
                         {"collection": "geom", "tag": "geom1"},
                         {"collection": "feature", "tag": "blk1"}]}

    def __init__(self, *, components: tuple[str, ...] = (), revision: int = 20,
                 component_create_error: tuple[str, str] | None = None,
                 hide_component_tag: bool = False,
                 component_appears_between_read_and_create: bool = False) -> None:
        self.components = list(components)
        self.revision = revision
        self.component_create_error = component_create_error
        self.hide_component_tag = hide_component_tag
        #: The read-then-create order is not atomic: the tag appears after the listing was read.
        self.component_appears_between_read_and_create = component_appears_between_read_and_create
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.dispatched: list[dict[str, Any]] = []
        self.store: dict[str, dict[str, Any]] = {}
        self.variables: dict[str, str] = {}
        self.features: dict[str, str] = {}

    # -- the product's own request hash -------------------------------------------------------
    @staticmethod
    def request_hash(operation: str, arguments: Mapping[str, Any], execution: Mapping[str, Any]) -> str:
        payload = {"operation": operation, "arguments": dict(arguments),
                   "model_ref": execution.get("model_ref"),
                   "expected_revision": execution.get("expected_revision"),
                   "session_id": execution.get("session_id")}
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def stamp(self, payload: dict[str, Any], execution: Mapping[str, Any], digest: str, *,
              operation_id: str | None = None, job_id: str | None = None) -> dict[str, Any]:
        block = {key: execution.get(key) for key in ("idempotency_key", "request_id", "session_id")}
        block.update({"model_ref": execution.get("model_ref"), "revision": self.revision, "dirty": False,
                      "operation_id": operation_id or str(uuid.uuid4()), "job_id": job_id or str(uuid.uuid4()),
                      "request_hash": digest})
        payload["execution"] = {key: value for key, value in block.items() if value is not None}
        return payload

    def describe(self, operation_id: str | None) -> dict[str, Any]:
        return _envelope(success=True, data={
            "operation_id": operation_id, "executable": True,
            "implementation_status": "SUPPORTED_UNVERIFIED",
            "mcp_tool_name": str(operation_id or "").replace(".", "_"), "effect": "project_write",
            "route": "strict", "output_contract": True,
            "input_schema": {"type": "object", "properties": {"idempotency_key": {"type": "string"},
                                                              "request_id": {"type": "string"}}},
            "wire_compatibility": {"execution_fields": ["idempotency_key", "request_id"]}})

    # -- one request as the daemon answers it -------------------------------------------------
    def handle(self, tool: str, request: Mapping[str, Any]) -> dict[str, Any]:
        self.requests.append((tool, copy.deepcopy(dict(request))))
        if tool == "job_reconcile":
            job = str(request.get("job_id") or request.get("job") or "")
            return _envelope(success=True, data={
                "job_id": job, "status": "SUCCEEDED", "reconciled_quiescent": True,
                "replay_performed": False,
                "reconciliation": [{"request_id": f"wrk-{job or 'unknown'}", "status": "SUCCEEDED",
                                    "source": "durable_worker_observation"}]})
        if tool == "model_inspect":
            execution = dict(request.get("execution") or {})
            return self.stamp(_envelope(success=True, data={}), execution,
                              self.request_hash("model_inspect", {}, execution))
        operation = str(request.get("operation_id") or tool)
        arguments = dict(request.get("arguments") or {})
        execution = dict(request.get("execution") or {})
        key = str(execution.get("idempotency_key") or "")
        digest = self.request_hash(operation, arguments, execution)
        stored = self.store.get(key)
        if stored is not None:
            if stored["request_hash"] != digest:
                return _envelope(success=False, code="IDEMPOTENCY_CONFLICT", message=T010_CONFLICT_MESSAGE)
            # The daemon re-answers the stored result: no second execution, same identity.
            return copy.deepcopy(stored["envelope"])
        payload = self.execute(operation, arguments, execution, digest)
        self.store[key] = {"request_hash": digest, "envelope": copy.deepcopy(payload)}
        return payload

    def execute(self, operation: str, arguments: Mapping[str, Any], execution: Mapping[str, Any],
                digest: str) -> dict[str, Any]:
        self.dispatched.append({"operation": operation, "arguments": copy.deepcopy(dict(arguments)),
                                "execution": copy.deepcopy(dict(execution)), "request_hash": digest})
        if operation == "node.find":
            return self.stamp(_envelope(success=True, data={
                "results": [{"path": dict(self.NODE), "type_id": "Block", "properties": {}}],
                "count": 1, "complete": True, "truncated": False, "visited": 1, "errors": [],
                "status": "COMPLETE"}), execution, digest)
        if operation == "node.property_schema":
            return self.stamp(_envelope(success=True, data={"properties": [
                {"name": "lx", "kind": "float64", "shape_rank": 0, "metadata_status": "KNOWN",
                 "shape": []}]}), execution, digest)
        if operation == "node.property_get":
            return self.stamp(_envelope(success=True, data={"values": [
                {"name": name, "value": {"kind": "float64", "shape": [], "data": 0.001}}
                for name in (arguments.get("names") or [])]}), execution, digest)
        if operation == "node.property_set":
            self.revision += 1
            properties = [dict(row) for row in (arguments.get("properties") or []) if isinstance(row, Mapping)]
            return self.stamp(_envelope(success=True, data={
                "applied": [{**row, "readback": copy.deepcopy(row.get("value")), "readback_match": True,
                             "comparison": {"matched": True, "rule": "exact"}} for row in properties],
                "applied_count": len(properties), "failed": [], "cleanup_failed": False,
                "dispatch_stage": "post_dispatch", "domain_state": "succeeded",
                "not_executed": False, "execution_state_unknown": False}), execution, digest)
        if operation == "definition.component_manage":
            action = str(arguments.get("action") or "")
            tag = str(arguments.get("tag") or "")
            if action == "list":
                tags = [] if self.hide_component_tag else list(self.components)
                return self.stamp(_envelope(success=True, data={
                    "action": "list", "component_count": len(tags),
                    "components": [{"tag": item} for item in tags], "readback": {"tags": list(tags)}}),
                    execution, digest)
            if action == "create":
                if self.component_create_error is not None:
                    code, message = self.component_create_error
                    return _envelope(success=False, code=code, message=message)
                if self.component_appears_between_read_and_create and tag not in self.components:
                    # Another session created the tag between the driver's read and its create:
                    # the product answers "already exists" and the tag is visible from now on.
                    self.components.append(tag)
                    return _refused_envelope(
                        "definition.component_manage", "TAG_CONFLICT",
                        f"definition.component_manage failed with TAG_CONFLICT (component {tag!r} "
                        "already exists)")
                if tag in self.components:
                    return _envelope(success=False, code="TAG_CONFLICT", message=f"component {tag!r} exists")
                self.components.append(tag)
                self.revision += 1
                return self.stamp(_envelope(success=True, data={
                    "action": "create", "tag": tag, "created": True,
                    "readback": {"tags": list(self.components)}}), execution, digest)
            raise AssertionError(f"unexpected definition.component_manage action {action!r}")
        if operation == "geometry.sequence_create":
            self.revision += 1
            return self.stamp(_envelope(success=True, data={
                "tag": arguments.get("tag"), "component": arguments.get("component"),
                "dimension": arguments.get("dimension"), "created": True,
                "readback": {"tags": [arguments.get("tag")]}}), execution, digest)
        if operation == "variable.group_create":
            component = str(arguments.get("component") or "")
            if component and component not in self.components:
                # The refusal the M1 run recorded: correct behaviour, missing fixture.
                payload = _envelope(
                    success=False, code="EXECUTION_STATE_UNKNOWN",
                    message=(f"variable.group_create failed with NODE_NOT_FOUND (component {component!r} "
                             "does not exist); the callback did not declare a pre-dispatch stage, so the "
                             "engine state cannot be shown to be unchanged"))
                payload["error"]["stage"] = "post_dispatch"
                payload["error"]["details"] = {
                    "cause_code": "NODE_NOT_FOUND",
                    "cause_message": f"component {component!r} does not exist",
                    "dispatch_stage": "post_dispatch", "unproven_pre_dispatch": False,
                    "witness": {"engine_calls": 2, "methods": ["component", "tags"],
                                "mutation_issued": False, "mutation_method": None}}
                return self.stamp(payload, execution, digest)
            self.revision += 1
            segments: list[dict[str, Any]] = []
            if component:
                segments.append({"collection": "component", "tag": component})
            segments.append({"collection": "variable", "tag": arguments.get("tag")})
            return self.stamp(_envelope(success=True, data={
                "scope": "component" if component else "global", "component": component or None,
                "tag": arguments.get("tag"), "created": True,
                "path": {"segments": segments},
                "readback": {"tags": [arguments.get("tag")]}}), execution, digest)
        if operation == "variable.set":
            for row in arguments.get("variables") or []:
                self.variables[str(row.get("name"))] = str(row.get("expression"))
            self.revision += 1
            return self.stamp(_envelope(success=True, data={"variables": [
                {"name": name, "expression": expression} for name, expression in self.variables.items()]}),
                execution, digest)
        if operation == "variable.get":
            names = [str(name) for name in (arguments.get("names") or [])]
            return self.stamp(_envelope(success=True, data={
                "variables": [{"name": name, "expression": self.variables.get(name)} for name in names],
                "varnames": list(names), "names": list(names)}), execution, digest)
        if operation == "evaluate_expressions":
            rows = json.loads(str(arguments.get("expressions_json") or "[]"))
            known = {"q1": 2.0, "q2": 3.0, "1+1": 2.0, "g1": 10.0, "g2": 40.0}
            results = [{"name": row.get("name"), "expression": str(row.get("expression")),
                        "last_value": known.get(str(row.get("expression")))}
                       for row in rows]
            return self.stamp(_envelope(success=True, data={
                "results": results, "ephemeral_mutation": True,
                "evaluation_policy": arguments.get("evaluation_policy")}), execution, digest)
        if operation == "create_feature":
            tag = str(arguments.get("tag") or "")
            feature_type = str(arguments.get("feature_type") or "")
            if tag in self.features:
                if self.features[tag] == feature_type:
                    return self.stamp(_envelope(success=True, data={
                        "tag": tag, "created": False, "readback": {"tag": tag}}), execution, digest)
                return _envelope(success=False, code="TAG_CONFLICT",
                                 message=f"tag {tag!r} already carries another feature type")
            self.features[tag] = feature_type
            self.revision += 1
            return self.stamp(_envelope(success=True, data={
                "tag": tag, "created": True, "readback": {"tag": tag}}), execution, digest)
        raise AssertionError(f"unexpected operation {operation!r}")


class _StdioDouble:
    """A ``ProductionHost`` without its session: the real bookkeeping, a scripted product behind it."""

    def __init__(self, driver, engine: _ProductEngine, *, args: Any = None,
                 run_state: dict[str, Any] | None = None, run_dir: Path | None = None) -> None:
        self.driver = driver
        self.engine = engine
        args = args if args is not None else driver.build_parser().parse_args(["--live"])
        self.run_dir = run_dir if run_dir is not None else Path(tempfile_mkdtemp())
        self.transcript: list[dict[str, Any]] = []
        host = driver.ProductionHost(args, self.run_dir, self.transcript, label="double", run_state=run_state)
        host.tools = {"operation_describe": object(), "operation_call": object(),
                      "job_reconcile": object(), "model_inspect": object()}
        host._call_once = self._call_once
        self.host = host

    async def _call_once(self, name: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        request = dict(arguments or {})
        if name == "operation_describe":
            payload = self.engine.describe(request.get("operation_id"))
        else:
            payload = self.engine.handle(name, request)
        payload = {**payload, "_outer_isError": payload.get("success") is not True}
        payload["_structuredContent"] = {key: value for key, value in payload.items()
                                         if not key.startswith("_")}
        self.transcript.append({"host": "double", "operation": name, "arguments": request,
                                "payload": payload, "outer_isError": payload["_outer_isError"]})
        execution = request.get("execution")
        self.host._observe_envelope(name, payload,
                                   request=execution if isinstance(execution, Mapping) else None)
        return payload


def tempfile_mkdtemp() -> str:
    import tempfile
    return tempfile.mkdtemp(prefix="phase4-double-")


def _live_args(driver):
    return driver.build_parser().parse_args(["--live"])


def _live_state(*, ref: Mapping[str, Any], revision: int, case: str) -> dict[str, Any]:
    return {"ref": dict(ref), "revision": revision, "model_origin": "created",
            "request_scope": {"case": case, "step": None}}


def _bound_ref() -> dict[str, Any]:
    return {"session_id": "session-1", "server_instance_id": "instance-1", "model_tag": "mcp1",
            "generation": 1, "schema_version": 1}


# ---------------------------------------------------------------------------
# C) GUARD_T010: a true retry over the real ActionClient
# ---------------------------------------------------------------------------


def _run_t010(driver, engine: _ProductEngine) -> tuple[Any, Any, Any, dict[str, Any]]:
    args = _live_args(driver)
    host = _StdioDouble(driver, engine, args=args)
    state = _live_state(ref=_bound_ref(), revision=20, case="GUARD_T010")
    client = driver.ActionClient(host.host, args, state)
    case = driver.Case(case_id="GUARD_T010", package="GUARD", acceptance=("G3 §10 T010",))
    asyncio.run(driver._case_guard_t010(host.host, client, case, args, state))
    case.finalize_inventory()
    return case, client, host, state


def _t010_write_requests(engine: _ProductEngine) -> list[dict[str, Any]]:
    """Every ``node.property_set`` request that reached the wire, in dispatch order."""
    return [request for _tool, request in engine.requests
            if str(request.get("operation_id")) == "node.property_set"]


def _t010_executions(engine: _ProductEngine) -> list[dict[str, Any]]:
    """The ``node.property_set`` requests the engine actually executed (a replay is not one)."""
    return [row for row in engine.dispatched if row["operation"] == "node.property_set"]


def test_guard_t010_true_retry_resends_the_dispatched_bytes_under_the_same_key(driver) -> None:
    """The pair under one key is one request: identical bytes in, the product's own identity out."""
    engine = _ProductEngine(revision=20)
    case, client, host, _state = _run_t010(driver, engine)
    statuses = {name: row["status"] for name, row in case.subcases.items()}
    assert statuses["repeated_key_not_reexecuted"] == "PASS", case.subcases["repeated_key_not_reexecuted"]
    assert statuses["request_hash_conflict_rejected"] == "PASS", case.subcases["request_hash_conflict_rejected"]
    assert statuses["static_idempotency_contract_published"] == "PASS"
    assert case.status == "PASS", (case.status, case.reason)
    requests = _t010_write_requests(engine)
    assert len(requests) == 3, [request.get("operation_id") for _tool, request in engine.requests]
    first, retry, conflict = requests
    first_body, retry_body = dict(first["arguments"]), dict(retry["arguments"])
    first_execution, retry_execution = dict(first["execution"]), dict(retry["execution"])
    assert first_execution["idempotency_key"] == retry_execution["idempotency_key"]
    assert first_body == retry_body, "the retry must resend the dispatched bytes"
    assert first_execution == retry_execution, "nothing about the retry is re-derived"
    assert retry_execution["expected_revision"] == first_execution["expected_revision"] == 20
    assert (_ProductEngine.request_hash("node.property_set", retry_body, retry_execution)
            == _ProductEngine.request_hash("node.property_set", first_body, first_execution)), \
        "the product's own request hash must see one and the same request"
    # The pair is ONE execution: the retry replayed the stored result instead of writing again.
    executions = [row["operation"] for row in engine.dispatched]
    assert executions.count("node.property_set") == 1, executions
    # The duplicate probe dispatches three creates: the second reports the existing tag without
    # creating anything, the third (same tag, different type) is refused — only the first creates.
    assert executions.count("create_feature") == 3, executions
    assert engine.revision == 22, "one property write + one feature create moved the revision"
    detail = case.assertions["t010_repeat"]
    assert detail["key"].startswith("guard-t010-repeat")
    assert detail["same_body"] is True and detail["same_operation_id"] is True
    assert detail["first_identity"]["operation_id"] == detail["retry_identity"]["operation_id"]
    assert detail["first_identity"]["operation_id"] and detail["first_identity"]["job_id"]
    assert detail["first_identity"]["request_hash"] == detail["retry_identity"]["request_hash"]
    assert detail["retry_record"]["same_body"] is True and detail["retry_record"]["key"] == detail["key"]
    assert client.retries[-1]["same_body"] is True and client.retries[-1]["key"] == detail["key"]
    # The conflict probe kept the dispatched revision and differs by exactly one documented field.
    assert conflict["execution"]["expected_revision"] == 20
    assert set(conflict["arguments"]) - set(first_body) == {"provenance"}
    assert {row["execution"]["idempotency_key"] for row in requests} == {first_execution["idempotency_key"]}
    replays = host.host.context.evidence()["replays"]
    retry_rows = [row for row in replays if row.get("checked") is True]
    assert len(retry_rows) == 1 and retry_rows[0]["same_body"] is True
    collisions = [row for row in replays if row.get("same_body") is False]
    assert len(collisions) == 1, "only the case's own conflict probe may reuse the key with a new body"
    assert collisions[0]["explicit"] is True and collisions[0]["key"] == detail["key"]


def test_a_same_key_call_with_a_re_derived_body_is_recorded_and_refused_by_the_product(driver) -> None:
    """The pre-fix shape stays visible: a changed body is a new plan, and the product says so.

    This is the negative half of C02.  The driver records the reuse (never silently), the product
    refuses it as ``IDEMPOTENCY_CONFLICT``, and no second write reaches the engine.
    """
    engine = _ProductEngine(revision=20)
    args = _live_args(driver)
    host = _StdioDouble(driver, engine, args=args)
    state = _live_state(ref=_bound_ref(), revision=20, case="GUARD_T010")
    client = driver.ActionClient(host.host, args, state)
    body = {"path": {"segments": [{"collection": "component", "tag": "comp1"}]},
            "properties": [{"name": "lx", "value": {"kind": "float64", "shape": [], "data": 0.001}}]}
    key = "guard-t010-revision-moved"
    first = asyncio.run(client.action("node.property_set", body, key=key, request="repeat"))
    assert first["success"] is True
    writes_before = len(_t010_write_requests(engine))
    assert state["revision"] == 21, "the first write moved the revision the product reports"
    # The "retry" below is the shape the pre-fix code sent: the same key with a re-derived body.
    second = asyncio.run(client.action("node.property_set", body, key=key, request="repeat"))
    assert second["success"] is False and driver._error_code(second) == "IDEMPOTENCY_CONFLICT"
    assert len(_t010_executions(engine)) == writes_before, "the conflict never reached the engine"
    replays = [row for row in client.host.context.evidence()["replays"]
               if row.get("same_body") is False]
    assert replays and replays[-1]["explicit"] is True
    assert "deliberate explicit-key reuse" in str(replays[-1].get("intent"))
    assert replays[-1]["key"] == key and replays[-1]["previous_request"] == key
    # The dispatched record is the body that may be retried, and only this key may carry it.
    record = client.dispatched[key]
    assert record["body_sha256"] == driver._body_sha256(driver._plan_body(record["arguments"]))
    with pytest.raises(driver.CapabilityUnavailable) as excinfo:
        asyncio.run(client.retry("guard-t010-never-dispatched"))
    assert "has no dispatched request to retry" in str(excinfo.value)
    # A faithful retry of the last dispatch re-sends those bytes; the key already stores a
    # *different* request, so the product answers its conflict instead of executing anything.
    payload, retry_record = asyncio.run(client.retry(key))
    assert retry_record["same_body"] is True and retry_record["retry_body_sha256"] == record["body_sha256"]
    assert retry_record["error_code"] == "IDEMPOTENCY_CONFLICT", detail_of(payload)
    assert len(_t010_executions(engine)) == writes_before, "a refused retry executes nothing"


def detail_of(payload: Mapping[str, Any]) -> str:
    return json.dumps({key: value for key, value in payload.items() if not key.startswith("_")})[:400]


# ---------------------------------------------------------------------------
# D) W13_T006: the component prerequisite is created and read back
# ---------------------------------------------------------------------------


def _run_t006(driver, engine: _ProductEngine) -> tuple[Any, Any, Any, dict[str, Any]]:
    args = _live_args(driver)
    host = _StdioDouble(driver, engine, args=args)
    state = _live_state(ref=_bound_ref(), revision=0, case="W13_T006_variables")
    client = driver.ActionClient(host.host, args, state)
    case = driver.Case(case_id="W13_T006_variables", package="W13", acceptance=("G3 §7 W13.T006",))
    asyncio.run(driver._case_w13_t006(host.host, client, case, args, state))
    case.finalize_inventory()
    return case, client, host, state


def test_the_t006_prerequisite_reads_and_creates_and_reads_the_component_back(driver) -> None:
    """``own_geometry`` is an observation, not a declaration: list, create, list back, then use."""
    engine = _ProductEngine(components=(), revision=0)
    case, _client, _host, _state = _run_t006(driver, engine)
    established = case.assertions["prerequisites"]["established"]
    assert established["component"] == "comp1" and established["status"] == "SATISFIED"
    assert established["verified"] is True and "comp1" in established["component_tags"]
    assert [row["step"] for row in established["steps"]] == ["w13-t006-component-list",
                                                            "w13-t006-component",
                                                            "w13-t006-component-list-verify"]
    assert {row["operation"] for row in established["steps"]} == {"definition.component_manage"}
    assert "created and read back" in established["reason"]
    assert established["created"] is True and established["adopted"] is False
    # The prerequisite precedes the acceptance steps, on the model the case addresses.
    sequence = [row["operation"] for row in engine.dispatched]
    assert sequence.index("definition.component_manage") < sequence.index("variable.group_create")
    statuses = {name: row["status"] for name, row in case.subcases.items()}
    for name in LIVE_LINES:
        assert statuses[name] == "PASS", (name, case.subcases[name])
    assert case.status == "PASS" and "first_cause" not in case.assertions


def test_a_component_that_cannot_be_established_is_the_cases_own_dependency(driver) -> None:
    """A refused create is ONE ``DEPENDENCY_BLOCKED`` root cause: no live line is attempted."""
    engine = _ProductEngine(components=(), revision=0,
                            component_create_error=("API_UNSUPPORTED",
                                                    "this build cannot create a component through the "
                                                    "published operation"))
    case, _client, _host, _state = _run_t006(driver, engine)
    established = case.assertions["prerequisites"]["established"]
    assert established["status"] != "SATISFIED" and established["verified"] is False
    assert "API_UNSUPPORTED" in established["reason"]
    root = established["root_cause"]
    assert root["operation"] == "definition.component_manage" and root["error_code"] == "API_UNSUPPORTED"
    blocked = case.assertions["dependency_blocked"]
    assert blocked["missing"] == ["component:comp1"]
    assert blocked["root_cause"]["operation"] == "definition.component_manage"
    cause = case.assertions["first_cause"]
    assert cause["class"] == "DEPENDENCY_BLOCKED" and cause["source"] == "declared"
    statuses = {name: row["status"] for name, row in case.subcases.items()}
    for name in LIVE_LINES:
        assert statuses[name] == "NOT_RUN", (name, case.subcases[name])
        assert "dependency not established: component:comp1" in case.subcases[name]["reason"]
    assert case.status == "BLOCKED"
    # No live step ran at all: nothing addressed the variable group the case was to create.
    assert not [row for row in engine.dispatched if row["operation"] == "variable.group_create"]
    # The run-level classifier reads the case's declared cause: one dependency, not a product gap.
    assert driver.case_first_cause(case)["class"] == "DEPENDENCY_BLOCKED"


def test_a_component_list_without_the_tag_is_not_a_verified_prerequisite(driver) -> None:
    """A successful create whose list readback lacks the tag proves nothing: no live line runs."""
    engine = _ProductEngine(components=(), revision=0, hide_component_tag=True)
    case, _client, _host, _state = _run_t006(driver, engine)
    established = case.assertions["prerequisites"]["established"]
    assert established["status"] == "UNSATISFIED" and established["verified"] is False
    assert established["component_tags"] == []
    assert "without 'comp1'" in established["reason"]
    assert established["root_cause"]["cause_code"] == "NODE_NOT_FOUND"
    assert case.assertions["first_cause"]["class"] == "DEPENDENCY_BLOCKED"
    assert case.status == "BLOCKED"
    assert not [row for row in engine.dispatched if row["operation"] == "variable.group_create"]


def test_an_unpublished_container_operation_is_a_prerequisite_capability_gap(driver) -> None:
    """An unpublished container route blocks the prerequisite itself — it is never a FAIL."""
    engine = _ProductEngine(components=(), revision=0)
    args = _live_args(driver)
    host = _StdioDouble(driver, engine, args=args)
    host.host.tools = {"operation_describe": object()}  # operation_call is not published either
    client = driver.ActionClient(host.host, args, {"ref": _bound_ref(), "revision": 0})
    record = asyncio.run(driver._establish_component_prerequisite(client, args, prefix="w13-t006"))
    assert record["status"] == "BLOCKED" and record["verified"] is False
    assert record["root_cause"]["operation"] == "definition.component_manage"
    assert "not published" in record["reason"]
    assert record["steps"] == []


# ---------------------------------------------------------------------------
# E) the M1c gate: read-first component, and the requests it replays
# ---------------------------------------------------------------------------


def test_the_component_prerequisite_reads_before_it_creates(driver) -> None:
    """A container that is already there is adopted on sight — the refused create is never sent.

    M1c sent ``definition.component_manage create comp1`` on a model that already carried ``comp1``
    (the cases before it leave their containers behind): the product answered, correctly,
    ``TAG_CONFLICT``, and the pre-repair envelope turned that correct refusal into the run's first
    blocker.  Reading first removes the dispatch, not just the classification.
    """
    engine = _ProductEngine(components=("comp1",), revision=4)
    case, client, _host, _state = _run_t006(driver, engine)
    established = case.assertions["prerequisites"]["established"]
    assert established["status"] == "SATISFIED" and established["verified"] is True
    assert established["adopted"] is True and established["created"] is False
    assert established["component_tags"] == ["comp1"]
    assert [row["step"] for row in established["steps"]] == ["w13-t006-component-list"]
    assert "already contains 'comp1': no create was dispatched" in established["reason"]
    # No create reached the engine at all — the only component call is the read.
    component_calls = [row for row in engine.dispatched
                       if row["operation"] == "definition.component_manage"]
    assert [row["arguments"]["action"] for row in component_calls] == ["list"]
    assert "tag" not in component_calls[0]["arguments"]
    # C03 is unchanged: an established prerequisite is not a product gap, and the case runs.
    statuses = {name: row["status"] for name, row in case.subcases.items()}
    for name in LIVE_LINES:
        assert statuses[name] == "PASS", (name, case.subcases[name])
    assert case.status == "PASS" and "first_cause" not in case.assertions


def test_a_create_refused_as_tag_conflict_is_adopted_with_its_stage_evidence(driver) -> None:
    """The read-then-create order is not atomic: a tag that appeared meanwhile is adopted.

    The adoption keeps the refusal's *own* stage evidence with the step (the published
    ``dispatch_stage``/witness the product writes for a proven pre-dispatch raise) and still only
    concludes anything after the follow-up listing observes the tag — the same fallback C03 keeps
    for every "already exists" container answer.
    """
    engine = _ProductEngine(components=(), revision=0, component_appears_between_read_and_create=True)
    case, _client, _host, _state = _run_t006(driver, engine)
    established = case.assertions["prerequisites"]["established"]
    assert established["status"] == "SATISFIED" and established["verified"] is True
    assert established["adopted"] is True and established["created"] is False
    assert "reported present by a refused create and read back" in established["reason"]
    create_row = [row for row in established["steps"] if row["step"] == "w13-t006-component"][0]
    assert create_row["status"] == "ADOPTED" and create_row["error_code"] == "TAG_CONFLICT"
    adoption = create_row["adoption"]
    assert adoption["dispatch_stage"] == "dispatched_without_mutation"
    assert adoption["proves_not_executed"] is True and adoption["error_code"] == "TAG_CONFLICT"
    assert case.status == "PASS" and "first_cause" not in case.assertions


def test_the_geometry_container_reads_the_component_before_it_creates_it(driver) -> None:
    """W13_T015's container fixture: a component that is present is adopted, never re-created.

    The recorded M1c blocker of T015 is exactly this step (``definition.component_manage`` answered
    ``TAG_CONFLICT`` for the ``comp1`` the earlier cases left behind); the fixture now reads the
    component list first and goes straight on to its geometry sequence.
    """
    engine = _ProductEngine(components=("comp1",), revision=7)
    args = _live_args(driver)
    host = _StdioDouble(driver, engine, args=args)
    state = _live_state(ref=_bound_ref(), revision=7, case="W13_T015_units")
    client = driver.ActionClient(host.host, args, state)
    ok, evidence = asyncio.run(driver._ensure_geometry_container(client, args, prefix="w13-t015",
                                                                build_block=False))
    assert ok is True, evidence.get("reason")
    assert [row["operation"] for row in engine.dispatched] == ["definition.component_manage",
                                                              "geometry.sequence_create"]
    assert engine.dispatched[0]["arguments"]["action"] == "list"
    assert "tag" not in engine.dispatched[0]["arguments"]
    component_evidence = evidence["component_evidence"]
    assert component_evidence["adopted"] is True and component_evidence["created"] is False
    assert [row["step"] for row in evidence["steps"]] == ["w13-t015-component-list", "w13-t015-geometry"]
    assert evidence["geometry"] == "geom1"


def test_the_recorded_m1c_component_list_request_is_accepted_by_the_product_validator(driver) -> None:
    """The M1c list body (``action: list``, no tag) is what the driver sends, and it is valid.

    The recorded sequence 7 of ``W13_T006_variables`` is refused by the product *before dispatch*
    (``INVALID_REQUEST: missing required operation arguments: tag``) because the catalogue's flat
    ``required`` list named ``tag`` unconditionally; the operation itself refuses a tag for
    ``list``.  Both halves are pinned here against the run's own recorded body.
    """
    if not M1C_T006_DIR.is_dir():  # pragma: no cover - evidence absent from this checkout
        pytest.skip("the M1c run's evidence is not present in this checkout")
    from comsol_mcp._g2_registry import validate_call

    recorded = json.loads((M1C_T006_DIR / "requests.json").read_text(encoding="utf-8"))["requests"]
    results = json.loads((M1C_T006_DIR / "results.json").read_text(encoding="utf-8"))["results"]
    list_request = [row for row in recorded if row.get("operation") == "operation_call"
                    and row["arguments"].get("operation_id") == "definition.component_manage"
                    and row["arguments"]["arguments"].get("action") == "list"][0]
    refused = [row for row in results if row.get("sequence") == list_request["sequence"]][0]
    assert refused["error_code"] == "INVALID_REQUEST"
    assert refused["error_message"] == M1C_LIST_REFUSAL
    assert "tag" not in list_request["arguments"]["arguments"]

    def wire_body(request: Mapping[str, Any]) -> dict[str, Any]:
        """The body the managed wire validates: the execution identity fills the gaps.

        Exactly the merge ``ManagedBackend`` performs before ``validate_call`` (the public
        fallback keeps the outer execution envelope separate from the logical operation body).
        """
        body = dict(request["arguments"])
        execution = dict(request["execution"])
        for name in ("project_id", "session_id", "model_ref", "expected_revision",
                     "idempotency_key", "request_id"):
            if name not in body and name in execution:
                body[name] = execution[name]
        return body

    # Replayed verbatim: the product validator now accepts the recorded body it once refused.
    validate_call("definition.component_manage", wire_body(list_request["arguments"]))
    # ... and so does the create body the recorded run sent (unchanged semantics).
    create_request = [row for row in recorded if row.get("operation") == "operation_call"
                      and row["arguments"].get("operation_id") == "definition.component_manage"
                      and row["arguments"]["arguments"].get("action") == "create"][0]
    validate_call("definition.component_manage", wire_body(create_request["arguments"]))
    # The driver's own list request carries exactly the recorded field set.
    engine = _ProductEngine(components=("comp1",), revision=4)
    _case, _client, _host, _state = _run_t006(driver, engine)
    dispatched = [row for row in engine.dispatched
                  if row["operation"] == "definition.component_manage"][0]
    assert set(dispatched["arguments"]) == set(list_request["arguments"]["arguments"])


def test_the_recorded_m1c_tag_conflict_is_no_longer_an_unexecuted_unknown(driver) -> None:
    """The published TAG_CONFLICT envelope is read as a refused-before-engine dispatch (C03).

    M1c recorded (``W13_T015_units`` sequence 5) a fail-closed wrapper with the callback's own
    witness: four read methods, ``mutation_issued: false``.  The product now publishes that same
    proof *as* the refusal (``data.refused``/``dispatch_stage``/``witness``), so the driver's stage
    reader still files it as NOT_EXECUTED — the accounting the run needs to keep a correct
    "already exists" answer out of the unknown ledger.
    """
    envelope = _refused_envelope("definition.component_manage", "TAG_CONFLICT",
                                 "definition.component_manage failed with TAG_CONFLICT (component "
                                 "'comp1' already exists)")
    assert driver.observed_dispatch_stage(envelope) == "dispatched_without_mutation"
    assert driver.observed_dispatch_stage(envelope) in driver.NOT_EXECUTED_STAGES
    product = driver.product_dispatch_stage(envelope)
    assert product is not None and product["source"] == "data.refused"
    assert product["stage"] == "validation" and product["stage_known"] is True
    assert product["mutation_issued"] is False and product["proves_not_executed"] is True
    assert product["cause_code"] == "TAG_CONFLICT"
    # A witness that saw a mutation keeps the fail-closed unknown: the declaration is not the proof.
    mutated = _refused_envelope("definition.component_manage", "TAG_CONFLICT", "already exists",
                                mutation_issued=True)
    assert mutated["data"]["dispatch_stage"] == "validation"
    assert driver.observed_dispatch_stage(mutated) == "unknown"
    assert driver.product_dispatch_stage(mutated)["proves_not_executed"] is False
    # A stage that does not precede the write does not prove anything either.
    late = _refused_envelope("definition.component_manage", "TAG_CONFLICT", "already exists",
                             stage="post_dispatch")
    assert driver.product_dispatch_stage(late)["proves_not_executed"] is False
    # A refusal that carries its own details (the multi-geometry API_UNSUPPORTED raise) is read
    # from the refusal block, not from the details: the stage evidence must not be hidden.
    documented = _refused_envelope("physics.create", "API_UNSUPPORTED", "no geometry-tag overload",
                                   details=True)
    assert driver.product_dispatch_stage(documented)["source"] == "data.refused"
    assert driver.product_dispatch_stage(documented)["proves_not_executed"] is True
    assert driver.product_dispatch_stage(documented)["stage"] == "validation"
    # ... and a refusal envelope with no details at all (what ``tag_conflict`` writes) is the same.
    bare = _refused_envelope("definition.component_manage", "TAG_CONFLICT", "already exists",
                             details=False)
    assert driver.product_dispatch_stage(bare)["source"] == "data.refused"
    assert driver.observed_dispatch_stage(bare) == "dispatched_without_mutation"


# ---------------------------------------------------------------------------
# F) isolation restore, the M1 slice and the new evidence fields
# ---------------------------------------------------------------------------


def test_the_runs_shared_binding_is_captured_before_the_isolation_step(driver) -> None:
    """The restore used to be a no-op: the shared ref was captured *after* the isolation step.

    Live, that leak made every later ``shared_bound`` case inherit the last isolated model (the M1
    run's T010/T005 ran on R04's own, probe-dirtied model ``mcp2``).  The loop must capture the
    shared binding first and put it back in its ``finally``.
    """
    import ast

    tree = ast.parse(DRIVER_PATH.read_text(encoding="utf-8"))
    loop = next(node for node in ast.walk(tree)
                if isinstance(node, ast.For) and any(
                    isinstance(child, ast.Name) and child.id == "selected" and isinstance(child.ctx, ast.Load)
                    for child in ast.walk(node.iter)))
    body = loop.body
    capture = next(index for index, node in enumerate(body)
                   if isinstance(node, ast.Assign) and any(
                       isinstance(target, ast.Name) and target.id == "shared_ref_before"
                       for target in node.targets))
    isolation = next(index for index, node in enumerate(body)
                     if isinstance(node, ast.Assign) and any(
                         isinstance(target, ast.Name) and target.id == "isolation" for target in node.targets))
    assert capture < isolation, "the shared binding must be captured before the isolation step"
    finally_block = next(node for node in ast.walk(loop) if isinstance(node, ast.Try))
    restore = ast.unparse(finally_block)
    assert "shared_model_restored" in restore and "state['ref'] = dict(shared_ref_before)" in restore
    # The recorded M1 run shows the leak the fix removes: T006/T015 ran on mcp1, T010/T005 on the
    # model R04 created for itself, and neither isolated case recorded a restore.
    tags: dict[str, list[str]] = {}
    for case_dir in sorted((RUN_DIR / "cases").iterdir()):
        text = json.dumps(_case_material(case_dir)["requests"])
        tags[case_dir.name] = sorted({segment for segment in text.split('"') if segment.startswith("mcp")})
    assert tags["W13_T006_variables"] == tags["W13_T015_units"] == ["mcp1"]
    assert tags["R04_LIVE"] == tags["GUARD_T010"] == tags["GUARD_T005"] == ["mcp2"]
    for name in ("R04_LIVE", "GUARD_T033"):
        isolation = _case_material(RUN_DIR / "cases" / name)["assertions"]["case"]["assertions"]["isolation"]
        assert isolation["applied"] is True and "shared_model_restored" not in isolation


def test_the_summary_and_index_observe_the_isolated_binding_and_the_causes(driver) -> None:
    """Item 3: the first cause, the isolation/prerequisite state and the stage are observable."""
    case = driver.Case(case_id="W13_T006_variables", package="W13", acceptance=("G3 §7 W13.T006",))
    case.assertions["isolation"] = {"declaration": {"mode": "own_model",
                                                    "rationale": "the case writes its own model"},
                                    "applied": True,
                                    "shared_model_restored": {"ref": _bound_ref(), "revision": 7}}
    case.assertions["prerequisites"] = {
        "status": "SATISFIED", "unsatisfied": [],
        "established": {"component": "comp1", "status": "SATISFIED",
                        "reason": "the component 'comp1' was created/adopted and read back"}}
    case.assertions["first_cause"] = driver.classify_first_cause(
        {"status": "BLOCKED", "error_code": "EXECUTION_STATE_UNKNOWN", "cause_code": "NODE_NOT_FOUND",
         "reason": "variable.group_create failed with NODE_NOT_FOUND (component 'comp1' does not exist)"},
        case_id="W13_T006_variables")
    case.finish("BLOCKED", reason=case.assertions["first_cause"]["reason"])
    first_cause_lines = "\n".join(driver._first_cause_lines([case]))
    assert "cause=EXECUTION_STATE_UNKNOWN/NODE_NOT_FOUND" in first_cause_lines
    assert "DEPENDENCY_BLOCKED" in first_cause_lines
    isolation_lines = "\n".join(driver._isolation_lines([case]))
    assert "prerequisites=SATISFIED" in isolation_lines
    assert "established: comp1 → SATISFIED" in isolation_lines
    assert "shared binding restored: mcp1" in isolation_lines
    context = driver.ExecutionContext(run="t")
    context.note_dispatch(request_id="r1", tool="variable.group_create",
                          stage="dispatched_without_mutation", model_ref=_bound_ref(), revision=3)
    context.note_dispatch(request_id="r2", tool="node.property_set", stage="dispatched",
                          model_ref=_bound_ref(), revision=4)
    lines = "\n".join(driver._context_lines(context.evidence()))
    assert "dispatch stages recorded:" in lines
    assert "dispatched_without_mutation 1" in lines and "dispatched 1" in lines
    assert "identical body verified: 0" in lines


@pytest.mark.integration
def test_the_m1_stage_reruns_offline_and_publishes_the_new_fields(tmp_path: Path) -> None:
    """`--stage M1` stays repeatable without an engine, and its evidence carries the new fields."""
    import subprocess

    run_dir = tmp_path / "m1-offline"
    reply = subprocess.run([sys.executable, str(DRIVER_PATH), "--stage", "M1", "--run-dir", str(run_dir)],
                           cwd=ROOT, capture_output=True, text=True, timeout=600)
    assert reply.returncode in {0, 3, 4}, reply.stdout + reply.stderr
    index = json.loads((run_dir / "index.json").read_text(encoding="utf-8"))
    assert index["slice"]["stage"] == "M1"
    assert index["slice"]["selected"] == ["W13_T006_variables", "W13_T015_units", "R04_LIVE",
                                          "GUARD_T010", "GUARD_T005", "GUARD_T033"]
    assert set(index["first_cause"]["counts"]) == {"DEPENDENCY_BLOCKED", "HARNESS_FAILURE",
                                                   "IMPLEMENTATION_GAP", "EXTERNAL_BLOCKER"}
    assert "dispatches" in index["execution_context"]
    for case_id in index["slice"]["selected"]:
        document = json.loads((run_dir / "cases" / case_id / "assertions.json").read_text(encoding="utf-8"))
        assertions = document["case"]["assertions"]
        prerequisites = assertions["prerequisites"]
        assert prerequisites["status"] in {"SATISFIED", "UNSATISFIED"}
        assert isinstance(prerequisites["checks"], dict)
        assert "shared_model_restored" in assertions["isolation"] or assertions["isolation"]["applied"] is False
        assert assertions["isolation"]["declaration"]["mode"] in {"shared_bound", "own_model",
                                                                 "checkpoint_restore"}
    summary = (run_dir / "summary.md").read_text(encoding="utf-8")
    for section in ("## First causes (C03)", "## Case isolation and prerequisites (C03)",
                    "## Execution context (C02)", "dispatch stages recorded:"):
        assert section in summary, section
