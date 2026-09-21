"""Offline regressions for the C04 evaluation-policy read path.

The interrupted run read the evaluation policy off a field the driver had invented and then treated a
*datasheet* field as missing.  The product publishes the declaration where it actually lives —
``input_schema.properties.evaluation_policy`` — so these checks pin:

* the path the driver reads and the source it records (schema first, data-level second, never guessed),
* a pure_read verdict is only that line's evidence when it *is* the policy refusal (or a success that
  echoes the non-mutating policy) — any other error proves nothing about the policy,
* the four expression kinds are pre-registered with the routing condition each one needs, and a kind
  whose condition the model cannot establish stays NOT_RUN instead of being sent as a guess.
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


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def driver():
    return _load("phase4_driver_policy", DRIVER_PATH)


def test_the_policy_is_read_from_the_published_schema_path(driver) -> None:
    """The path C04 requires, and the recorded source, for each of the three real shapes."""
    declaration = {"type": "string", "enum": ["pure_read", "ephemeral_mutation"], "default": "pure_read"}
    schema = {"type": "object", "properties": {"evaluation_policy": declaration, "expressions_json": {}}}
    value, source, note = driver._evaluation_policy({"status": "SUPPORTED"}, schema)
    assert value == declaration
    assert source == driver._EVALUATION_POLICY_PATH == "input_schema.properties.evaluation_policy"
    assert "input schema" in note
    # A data-level copy is the fallback, and it says so.
    value, source, note = driver._evaluation_policy({"evaluation_policy": {"mode": "pure_read"}}, None)
    assert source == "data.evaluation_policy" and "data level" in note
    # Nothing published: the reader says so instead of inventing a value.
    value, source, note = driver._evaluation_policy({}, {"type": "object", "properties": {}})
    assert value is None and source is None and note.startswith("not published")


def test_describe_publishes_the_schema_and_the_policy_source(driver) -> None:
    """``_describe_row`` hands the case the full schema plus the path the policy came from."""
    declaration = {"type": "string", "enum": ["pure_read", "ephemeral_mutation"]}

    class _Host:
        def __init__(self) -> None:
            self.calls: list[tuple[str, Any]] = []
            self.tools = {"operation_describe"}

        async def call(self, tool: str, arguments: Any = None, **kwargs: Any) -> dict[str, Any]:
            self.calls.append((tool, arguments))
            return {"success": True, "data": {"operation_id": "evaluate_expressions", "executable": True,
                                              "implementation_status": "SUPPORTED_UNVERIFIED",
                                              "wire_compatibility": {"execution_fields": []},
                                              "input_schema": {"type": "object",
                                                               "properties": {"evaluation_policy": declaration,
                                                                              "expressions_json": {}}}},
                    "execution": {}, "_outer_isError": False}

    row = driver.asyncio.run(driver._describe_row(_Host(), "evaluate_expressions"))
    assert row["available"] is True
    assert row["input_schema"]["properties"]["evaluation_policy"] == declaration
    assert row["evaluation_policy"] == declaration
    assert row["evaluation_policy_source"] == "input_schema.properties.evaluation_policy"
    assert "evaluation_policy" in row["input_schema_properties"]


def test_the_expression_kinds_are_preregistered_with_their_routing_conditions(driver) -> None:
    """A constant, a model expression, a solved field and an illegal expression — each with its need."""
    kinds = {row["kind"] for row in driver.T033_EXPRESSIONS}
    assert kinds == {"constant", "model_expression", "solved_field", "illegal_expression"}
    by_kind = {row["kind"]: row for row in driver.T033_EXPRESSIONS}
    assert by_kind["constant"]["requires"] == (), "a constant needs no model condition"
    assert "never computes the value itself" in by_kind["constant"]["routing"]
    assert by_kind["model_expression"]["requires"] == ("model_parameters",)
    assert by_kind["solved_field"]["requires"] == ("solved_solution",)
    assert "stored" in by_kind["solved_field"]["routing"]
    for row in driver.T033_EXPRESSIONS:
        assert row["name"].startswith("phase4_"), row
        assert row["expression"] and row["routing"]


def test_a_pure_read_verdict_needs_the_policy_refusal_not_any_error(driver) -> None:
    """The C04 strictness: a missing node or a stale revision is not evidence about pure_read."""
    # The gate-refused line keeps its BLOCKED verdict (unknown engine state), never a PASS.
    case = driver.Case(case_id="GUARD_T033", package="GUARD", acceptance=("G3 §10 T033",))
    args = driver.build_parser().parse_args(["--live"])
    state = {"ref": {"session_id": "s", "server_instance_id": "i", "model_tag": "m"}, "revision": 1}

    class _Engine:
        """Refuses pure_read with the policy's own refusal, and answers the ephemeral calls."""

        def __init__(self) -> None:
            self.operations: list[str] = []

        async def action(self, operation, arguments=None, **kwargs):  # noqa: ANN001
            self.operations.append(operation)
            body = arguments or {}
            if body.get("evaluation_policy") == "pure_read":
                return {"success": False, "data": {"status": "POLICY_REFUSED"},
                        "error": {"code": "EVALUATION_POLICY_VIOLATION",
                                  "message": "pure_read evaluation refused: the request would mutate the model"},
                        "execution": {"job_id": "job-1"}, "_outer_isError": True}
            expressions = str(body.get("expressions_json") or "")
            if "sin(" in expressions:
                # The engine's own parser refuses the malformed expression: that refusal is the
                # evidence this kind exists for.
                return {"success": False, "data": {"status": "PARSE_ERROR"},
                        "error": {"code": "INVALID_EXPRESSION", "message": "syntax error: unexpected end of input"},
                        "execution": {"job_id": "job-3"}, "_outer_isError": True}
            return {"success": True, "data": {"expressions": [{"name": "phase4_constant", "value": 9}],
                                              "status": "OK"},
                    "execution": {"job_id": "job-2"}, "_outer_isError": False}

    engine = _Engine()
    host = _HostForCase(driver, engine)
    driver.asyncio.run(driver._case_guard_t033(host, engine, case, args, state))
    pure = case.assertions["t033_pure"]
    assert pure["policy_refusal"] is True and pure["policy_refusal_basis"]
    assert any(token.startswith("POLICY") for token in pure["policy_refusal_basis"]), pure["policy_refusal_basis"]
    assert case.subcases["pure_read_rejects_or_isolates"]["status"] == "PASS"
    # The line the engine answered keeps its own verdict; the constant's value is the engine's.
    kinds = {row["kind"]: row for row in case.assertions["t033_expression_kinds"]}
    assert kinds["constant"]["verdict"] == "value_recorded" and kinds["constant"]["value"] == 9
    assert kinds["illegal_expression"]["verdict"] in {"refused", "ROUTING_GAP"}


class _HostForCase:
    """The host surface a case body uses, with the describe payloads it needs."""

    def __init__(self, driver, engine) -> None:
        self.driver = driver
        self.engine = engine
        self.context = driver.ExecutionContext(run="t")
        self.tools = {"operation_describe"}
        self.transcript: list[dict[str, Any]] = []
        self.reconciliations: list[dict[str, Any]] = []

    def release_summary(self, payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
        """No reconcile read was needed in this fake: nothing is claimed (never a bare True)."""
        del payload
        return None

    async def call(self, tool: str, arguments: Any = None, **kwargs: Any) -> dict[str, Any]:
        operation = (arguments or {}).get("operation_id") if isinstance(arguments, dict) else None
        declaration = {"type": "string", "enum": ["pure_read", "ephemeral_mutation"]}
        return {"success": True, "data": {"operation_id": operation, "executable": True,
                                          "implementation_status": "SUPPORTED_UNVERIFIED",
                                          "wire_compatibility": {"execution_fields": []},
                                          "input_schema": {"type": "object",
                                                           "properties": {"evaluation_policy": declaration}}},
                "execution": {}, "_outer_isError": False}


def test_the_policy_vocabulary_is_not_widened_by_the_reader(driver) -> None:
    """A declared policy that is not the documented text stays an observation, not a synonym."""
    value, source, _ = driver._evaluation_policy({"evaluation_policy": {"mode": "SOMETHING_ELSE"}}, None)
    assert value == {"mode": "SOMETHING_ELSE"} and source == "data.evaluation_policy"
    # The published path is only read from the operation's own schema: no other key is proxied.
    schema = {"type": "object", "properties": {"policy": {"type": "string"}}}
    value, source, note = driver._evaluation_policy({"evaluation_policy": {"mode": "x"}}, schema)
    assert source == "data.evaluation_policy", "a field named policy is not evaluation_policy"
    assert "no input-schema declaration published" in note, note
    json.dumps({"kinds": driver.T033_EXPRESSIONS}, default=str)
