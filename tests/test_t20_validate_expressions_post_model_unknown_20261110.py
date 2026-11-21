"""Post-model expression probes preserve structured UNKNOWN outcomes."""
from __future__ import annotations

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._g3_w20_validation import STATUS_FAIL, STATUS_PASS, validate_expressions


class _Probe:
    def __init__(self, *, site=None, fail_at=1, failure=None):
        self.site = site
        self.fail_at = fail_at
        self.failure = failure
        self.events = []
        self.param_calls = 0
        self.evaluate_calls = 0
        self.fired = False

    def client(self):
        self.events.append("client")
        return self

    def model(self, tag):
        self.events.append(("model", tag))
        return self

    def param(self):
        self.param_calls += 1
        self.events.append(("param", self.param_calls))
        if self.site == "param" and self.param_calls == self.fail_at:
            self.fired = True
            assert self.failure is not None
            raise self.failure()
        return self

    def evaluate(self, expression):
        self.evaluate_calls += 1
        self.events.append(("evaluate", expression))
        if self.site == "evaluate" and self.evaluate_calls == self.fail_at:
            self.fired = True
            assert self.failure is not None
            raise self.failure()
        return 1.0


def _failure(kind):
    if kind == "direct":
        return ExecutionContractError("EXECUTION_STATE_UNKNOWN", "synthetic unknown")
    inner = ExecutionContractError(
        "ENGINE_CALL_FAILED", "synthetic structured unknown",
        stage="validation", safe_retry=True,
        details={"execution_state_unknown": True},
    )
    if kind == "structured":
        return inner
    outer = RuntimeError("synthetic explicitly chained error")
    outer.__cause__ = inner
    return outer


def _run(probe, expressions):
    result = None
    error = None
    try:
        result = validate_expressions(probe, "fake-model", {"expressions": expressions})
    except Exception as exc:
        error = exc
    return result, error


def _assert_lookup(probe):
    assert probe.events.count("client") == 1, probe.events
    assert probe.events.count(("model", "fake-model")) == 1, probe.events


@pytest.mark.parametrize("site", ["param", "evaluate"])
@pytest.mark.parametrize("kind", ["direct", "structured", "chained"])
def test_post_model_unknown_stops_the_next_expression(site, kind):
    probe = _Probe(site=site, failure=lambda: _failure(kind))
    result, error = _run(probe, ["alpha", "beta"])
    _assert_lookup(probe)
    assert probe.fired, probe.events
    assert error is not None, f"UNKNOWN was swallowed: events={probe.events!r}, result={result!r}"
    assert isinstance(error, ExecutionContractError)
    assert error.code == "EXECUTION_STATE_UNKNOWN"
    assert error.stage == "post_dispatch"
    assert error.safe_retry is False
    assert probe.param_calls == 1, probe.events
    assert probe.evaluate_calls == (0 if site == "param" else 1), probe.events


@pytest.mark.parametrize("site", ["param", "evaluate"])
def test_unknown_after_one_success_stops_the_remaining_expression(site):
    probe = _Probe(site=site, fail_at=2, failure=lambda: _failure("direct"))
    result, error = _run(probe, ["alpha", "beta", "gamma"])
    _assert_lookup(probe)
    assert probe.fired, probe.events
    assert error is not None, f"UNKNOWN was swallowed: events={probe.events!r}, result={result!r}"
    assert isinstance(error, ExecutionContractError)
    assert error.code == "EXECUTION_STATE_UNKNOWN"
    assert error.safe_retry is False
    assert probe.param_calls == 2, probe.events
    assert probe.evaluate_calls == (1 if site == "param" else 2), probe.events


def test_healthy_expressions_are_both_checked():
    probe = _Probe()
    result, error = _run(probe, ["alpha", "beta"])
    _assert_lookup(probe)
    assert error is None
    assert result is not None
    assert result["status"] == STATUS_PASS
    assert probe.param_calls == 2
    assert probe.evaluate_calls == 2


def test_ordinary_evaluation_error_remains_a_finding_and_continues():
    probe = _Probe(site="evaluate", failure=lambda: RuntimeError("synthetic ordinary error"))
    result, error = _run(probe, ["alpha", "beta"])
    _assert_lookup(probe)
    assert probe.fired
    assert error is None
    assert result is not None
    assert result["status"] == STATUS_FAIL
    assert result["findings"][0]["status"] == STATUS_FAIL
    assert "Engine evaluate failed" in result["findings"][0]["reason"]
    assert result["findings"][1]["status"] == STATUS_PASS
    assert probe.param_calls == 2
    assert probe.evaluate_calls == 2
