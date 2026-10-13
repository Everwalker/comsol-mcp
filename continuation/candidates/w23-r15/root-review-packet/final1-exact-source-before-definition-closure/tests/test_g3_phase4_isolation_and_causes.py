"""Offline regressions for case isolation, prerequisites and first-cause classification (C03).

The live run of 2026-09-20 produced a long tail of "independent" defects that were really one root
cause, and it filed a revision conflict as a missing CAD license.  These checks pin the corrections:

* every case declares how it is isolated (its own model, a verified checkpoint restore, or the
  run's shared model under the serial flow) and the declaration is checked, not assumed,
* a case's prerequisites are checked against what the run actually established, and a missing one
  becomes ONE ``DEPENDENCY_BLOCKED`` finding pointing at the root cause,
* each case contributes at most one first cause from the closed vocabulary, and a label that claims
  an external blocker while the evidence names a revision/signature/path defect is re-filed.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

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
    return _load("phase4_driver_isolation", DRIVER_PATH)


def _case(driver, case_id: str = "X", *, status: str = "NOT_RUN") -> Any:
    case = driver.Case(case_id=case_id, package="P", acceptance=("G3 §10",))
    case.status = status
    return case


def test_every_live_case_declares_how_it_is_isolated(driver) -> None:
    """No case may write into another case's model without saying so."""
    for case_id in driver.CASE_ORDER:
        declaration = driver.case_isolation(case_id)
        assert declaration["mode"] in driver.ISOLATION_MODES, (case_id, declaration)
        assert declaration["rationale"].strip(), case_id
        # The cases that used to pollute the single shared model have their own model now.
        if case_id in {"R01_LIVE", "R03_LIVE", "R04_LIVE", "GUARD_T033"}:
            assert declaration["mode"] == "own_model", case_id
    assert driver.case_isolation("W16_T019_chainC_continue")["mode"] == "checkpoint_restore"


def test_an_isolated_case_is_prepared_before_its_body_and_the_shared_model_returns(driver) -> None:
    """Own-model isolation creates a model; a checkpoint restore must be *verified*, not assumed."""
    case = _case(driver, "R04_LIVE")
    args = driver.build_parser().parse_args(["--live"])
    state: dict[str, Any] = {}

    class _RefusingEngine:
        async def action(self, operation, arguments, **kwargs):  # noqa: ANN001
            return {"success": False, "error": {"code": "ENGINE_UNREACHABLE", "message": "offline"},
                    "data": {}, "execution": {}}

    record = driver.asyncio.run(driver._case_isolation_step(case, _RefusingEngine(), args, state,
                                                            _MinimalHost(driver)))
    assert record["declaration"]["mode"] == "own_model"
    assert record["applied"] is False
    assert record["reason"], "a failed isolation has to say why, not silently continue"
    # Offline, the isolation is not attempted at all — and that is not filed as an external blocker.
    offline = driver.asyncio.run(driver._case_isolation_step(_case(driver, "R04_LIVE"),
                                                             _RefusingEngine(),
                                                             driver.build_parser().parse_args([]),
                                                             {}, _MinimalHost(driver)))
    assert offline["applied"] is False and "offline" in offline["reason"]
    assert "first_cause" not in offline


class _MinimalHost:
    """The host surface ``_case_isolation_step`` touches (nothing else is needed offline)."""

    def __init__(self, driver) -> None:
        self.driver = driver
        self.context = driver.ExecutionContext(run="t")
        #: The published tools this host claims to expose: the isolation step only needs the
        #: describe of the operations it will run.
        self.tools = {"operation_describe"}

    async def call(self, tool: str, arguments: Any = None, **kwargs: Any) -> dict[str, Any]:  # pragma: no cover
        del arguments, kwargs
        return {"success": False, "error": {"code": "SERVER_UNAVAILABLE", "message": "offline"},
                "data": {}, "execution": {}}


def test_prerequisites_are_checked_against_what_the_run_established(driver) -> None:
    """A missing prerequisite is one dependency-blocked finding, never dozens of case defects."""
    blocked = driver.case_prerequisites("W16_T019_chainA_steady", {}, live=True)
    assert blocked["status"] == "UNSATISFIED"
    assert blocked["unsatisfied"] == ["bound_model"]
    assert blocked["unknown_declarations"] == []
    assert "DEPENDENCY_BLOCKED" in blocked["note"]
    satisfied = driver.case_prerequisites("W16_T019_chainA_steady",
                                          {"ref": {"session_id": "s", "server_instance_id": "i",
                                                   "model_tag": "m"}, "revision": 3}, live=True)
    assert satisfied["status"] == "SATISFIED" and satisfied["unsatisfied"] == []
    # Offline the same declaration is not applicable: nothing is claimed and nothing is blocked.
    offline = driver.case_prerequisites("W16_T019_chainA_steady", {}, live=False)
    assert offline["status"] == "SATISFIED" and offline["unsatisfied"] == []
    # A case whose predecessor did not run in this selection says so instead of pretending, and a
    # predecessor that finished FAIL is not silently treated as available.
    offline = driver.case_prerequisites("W16_T019_chainC_continue", {}, live=False)
    predecessor = offline["checks"]["predecessor:W16_T019_chainA_steady"]
    assert predecessor["applicable"] is False and "did not run in this selection" in predecessor["evidence"]
    failed = driver.case_prerequisites("W16_T019_chainC_continue",
                                       {"completed_cases": {"W16_T019_chainA_steady": "FAIL"}}, live=True)
    assert failed["checks"]["predecessor:W16_T019_chainA_steady"]["satisfied"] is False
    assert failed["unsatisfied"] == ["bound_model", "predecessor:W16_T019_chainA_steady"]
    passed = driver.case_prerequisites("W16_T019_chainC_continue",
                                       {"completed_cases": {"W16_T019_chainA_steady": "PASS"}}, live=True)
    assert passed["checks"]["predecessor:W16_T019_chainA_steady"]["evidence"].endswith("finished PASS")
    # Every declaration in the table belongs to the published vocabulary.
    for case_id, declared in driver.CASE_PREREQUISITES.items():
        for name in declared:
            base = str(name).split(":", 1)[0]
            assert base in driver.PREREQUISITE_CHECKS, (case_id, name)


def test_isolation_is_a_declared_prerequisite_of_its_own(driver) -> None:
    """A case that needs its own model declares it, and the check reads the applied record."""
    not_applied = driver.case_prerequisites(
        "GUARD_T033", {"ref": {"session_id": "s", "server_instance_id": "i", "model_tag": "m"}}, live=True,
        isolation={"declaration": {"mode": "own_model"}, "applied": False, "reason": "the engine refused"})
    assert "isolated_model" in not_applied["unsatisfied"] and not_applied["status"] == "UNSATISFIED"
    applied = driver.case_prerequisites(
        "GUARD_T033", {"ref": {"session_id": "s", "server_instance_id": "i", "model_tag": "m"}}, live=True,
        isolation={"declaration": {"mode": "own_model"}, "applied": True})
    assert "isolated_model" not in applied["unsatisfied"]
    assert applied["checks"]["isolated_model"]["evidence"] == "the case's declared isolation (own_model) was applied"


def test_one_first_cause_per_case_from_the_closed_vocabulary(driver) -> None:
    """Each case contributes at most one class: the run summary cannot explode into dozens of them."""
    case = _case(driver, "X", status="FAIL")
    for index in range(12):
        case.subcases[f"line-{index}"] = {"status": "FAIL", "reason": f"failure {index}"}
    cause = driver.case_first_cause(case)
    assert cause["class"] in driver.FIRST_CAUSE_CLASSES
    summary = driver._first_cause_summary([case])
    assert summary["per_case"]["X"]["class"] == cause["class"]
    assert sum(summary["counts"].values()) == 1
    assert set(summary["counts"]) == set(driver.FIRST_CAUSE_CLASSES)


def test_a_declared_first_cause_is_honoured_only_inside_the_vocabulary(driver) -> None:
    declared = _case(driver, "Y", status="BLOCKED")
    declared.assertions["first_cause"] = "DEPENDENCY_BLOCKED"
    assert driver.case_first_cause(declared)["class"] == "DEPENDENCY_BLOCKED"
    assert driver.case_first_cause(declared)["source"] == "declared"
    invalid = _case(driver, "Z", status="FAIL")
    invalid.assertions["first_cause"] = "SOMETHING_NEW"
    cause = driver.case_first_cause(invalid)
    assert cause["class"] == "HARNESS_FAILURE" and cause["source"] == "declared-invalid"


def test_a_case_that_closed_without_a_failing_subcase_still_gets_one_cause(driver) -> None:
    case = _case(driver, "C", status="BLOCKED")
    case.reason = "no model_ref was bound, so no live step could run"
    cause = driver.case_first_cause(case)
    assert cause is not None and cause["class"] in driver.FIRST_CAUSE_CLASSES
    assert cause["subcase"] == "(case level)" and cause["reason"]


def test_an_external_blocker_label_is_refiled_when_the_evidence_is_a_defect(driver) -> None:
    """The G3.1 §4 correction: a revision/signature/path defect is never "a missing CAD license"."""
    row = {"status": "FAIL", "reason": "CAD license unavailable: the geometry could not be imported",
           "error_code": "BLOCKED_LICENSE",
           "observed": {"error_code": "REVISION_CONFLICT", "message": "the managed revision moved"}}
    cause = driver.classify_first_cause(row, case_id="W14_T034_local_paths")
    assert cause["class"] in {"IMPLEMENTATION_GAP", "HARNESS_FAILURE"}
    assert cause["guard"]["reclassified_from"] == "EXTERNAL_BLOCKER"
    assert cause["guard"]["matched_evidence"].lower().startswith("revision")
    assert "never an unobtainable resource" in cause["guard"]["why"]
    # The same claim with a path problem inside the evidence is re-filed too.
    path_row = {"status": "FAIL", "reason": "no CAD license seat available",
                "error_code": "LICENSE_UNAVAILABLE",
                "refusal": {"code": "INVALID_NODE_PATH", "message": "no such file: /tmp/x.mph"}}
    path_cause = driver.classify_first_cause(path_row, case_id="W14_T034_local_paths")
    assert path_cause["class"] == "IMPLEMENTATION_GAP" and path_cause["guard"]["reclassified_from"] == "EXTERNAL_BLOCKER"
    # A genuine external blocker keeps its class and gets no guard.
    allowed = driver.classify_first_cause({"status": "BLOCKED", "reason": "the product is not installed",
                                           "error_code": "PRODUCT_UNAVAILABLE"}, case_id="W15_T042_license")
    assert allowed["class"] == "EXTERNAL_BLOCKER" and allowed["guard"] is None
    # A dependency-blocked line names its prerequisite instead of a resource.
    dependency = driver.classify_first_cause(
        {"status": "NOT_RUN", "reason": "prerequisite subcase empty_model_geometry_block did not pass"},
        case_id="W16_T019_chainA_steady")
    assert dependency["class"] == "DEPENDENCY_BLOCKED"


def test_the_summary_and_the_index_observe_the_new_classification(driver, tmp_path: Path) -> None:
    case = _case(driver, "W16_T019_chainA_steady", status="FAIL")
    case.subcases["solve_produced_solution"] = {"status": "FAIL", "reason": "the solve was refused",
                                                 "error_code": "REVISION_CONFLICT"}
    case.assertions["isolation"] = {"declaration": {"mode": "own_model", "rationale": "probe"},
                                    "applied": True, "shared_model_restored": {"ref": {"model_tag": "shared"}}}
    case.assertions["prerequisites"] = driver.case_prerequisites("W16_T019_chainA_steady", {}, live=False)
    case.finish()
    lines = "\n".join(driver._first_cause_lines([case]))
    assert "First causes" in lines and "IMPLEMENTATION_GAP" in lines
    assert "W16_T019_chainA_steady" in lines
    isolation_lines = "\n".join(driver._isolation_lines([case]))
    assert "own_model" in isolation_lines and "prerequisites" in isolation_lines
    args = driver.build_parser().parse_args(["--only", "W16_T019_chainA_steady"])
    slice_lines = "\n".join(driver._slice_lines(args, driver.select_cases(args)))
    assert "M2" in slice_lines and "W16_T019_chainA_steady" in slice_lines
    context_lines = "\n".join(driver._context_lines(driver.ExecutionContext(run="t").evidence()))
    assert "refusals classified as" in context_lines and "stale_expected" in context_lines
    del tmp_path


def test_a_slice_narrows_and_never_widens_the_run(driver) -> None:
    """``--only``/``--stage`` select a subset for offline verification; they never pull cases in."""
    full = driver.build_parser().parse_args([])
    assert driver.select_cases(full) == list(driver.CASE_ORDER)
    only = driver.build_parser().parse_args(["--only", "GUARD_T010,GUARD_T033"])
    assert driver.select_cases(only) == ["GUARD_T010", "GUARD_T033"]
    staged = driver.build_parser().parse_args(["--stage", "M2"])
    assert driver.select_cases(staged) == ["W16_T019_chainA_steady", "W16_T019_chainB_transient"]
    both = driver.build_parser().parse_args(["--stage", "M1", "--only", "GUARD_T033,W16_T019_chainA_steady"])
    assert driver.select_cases(both) == ["GUARD_T033"], "a stage excludes what the slice names"
    unknown = driver.build_parser().parse_args(["--only", "NOT_A_CASE"])
    assert driver.select_cases(unknown) == []
    summary = driver._slice_summary(only, driver.select_cases(only))
    assert summary["selected"] == ["GUARD_T010", "GUARD_T033"] and summary["stages"]["M1"]
    json.dumps(summary, default=str)
