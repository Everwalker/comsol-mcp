"""T35 synthetic tests for the pure job/operation project-binding helper.

Outer-behavior RED precondition: the parent's 20-ID contract run against the
real outer daemon dispatcher failed 20F with no skips, recorded at
``_state/t35-log-overflow-parent-20261112/job-read-a1-red-case/red.log``.

These tests exercise only the pure helper with synthetic in-memory mappings.
They never touch a store, daemon, network, credentials, model, Worker, or any
other real resource. They are intentionally NOT run by the authoring child;
the parent audits and executes them during integration.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from comsol_mcp._job_project_scope import consistent_project_id

PROJECT = "t35-project-alpha"
OTHER = "t35-project-beta"


@pytest.mark.parametrize(
    ("job_metadata", "operation_metadata", "expected"),
    [
        pytest.param(
            {"project_id": PROJECT},
            {"project_id": PROJECT},
            PROJECT,
            id="flat_flat",
        ),
        pytest.param(
            {"execution": {"project_id": PROJECT}},
            {"execution": {"project_id": PROJECT}},
            PROJECT,
            id="nested_nested",
        ),
        pytest.param(
            {
                "project_id": PROJECT,
                "project_root": "/srv/alpha",
                "cleanup": {"metadata_compacted": True},
            },
            {
                "operation": "job.synthetic-producer",
                "execution": {"project_id": PROJECT},
            },
            PROJECT,
            id="compacted_flat_job_nested_operation",
        ),
        pytest.param(
            {"execution": {"project_id": PROJECT, "runtime_id": "rt-1"}},
            {"project_id": PROJECT},
            PROJECT,
            id="nested_job_flat_operation",
        ),
        pytest.param(
            {"project_id": PROJECT, "execution": {"project_id": PROJECT}},
            {"project_id": PROJECT, "execution": {"project_id": PROJECT}},
            PROJECT,
            id="both_fields_agree_both_sides",
        ),
        pytest.param(
            {"project_id": PROJECT, "execution": {"project_id": PROJECT}},
            {"execution": {"project_id": PROJECT, "runtime_id": "rt-1"}},
            PROJECT,
            id="one_side_both_fields_other_nested_only",
        ),
        pytest.param(
            {"project_id": PROJECT},
            {"execution": {"project_id": PROJECT}, "arguments": {"project_id": OTHER}},
            PROJECT,
            id="untrusted_arguments_conflict_ignored",
        ),
        pytest.param(
            {"project_id": PROJECT, "project_root": OTHER},
            {"execution": {"project_id": PROJECT}},
            PROJECT,
            id="untrusted_project_root_conflict_ignored",
        ),
        pytest.param(
            {"project_id": "   "}, {"project_id": "   "}, "   ",
            id="whitespace_only_exact_match",
        ),
    ],
)
def test_accepts_independent_matching_bindings(
    job_metadata: object, operation_metadata: object, expected: str
) -> None:
    assert consistent_project_id(job_metadata, operation_metadata) == expected


@pytest.mark.parametrize(
    ("job_metadata", "operation_metadata"),
    [
        pytest.param(None, {"project_id": PROJECT}, id="job_metadata_none"),
        pytest.param(["project_id", PROJECT], {"project_id": PROJECT}, id="job_metadata_list"),
        pytest.param(PROJECT, {"project_id": PROJECT}, id="job_metadata_str"),
        pytest.param({"project_id": PROJECT}, None, id="operation_metadata_none"),
        pytest.param({"project_id": PROJECT}, 7, id="operation_metadata_int"),
        pytest.param(
            {"execution": 3}, {"execution": {"project_id": PROJECT}},
            id="job_execution_nonmapping_int",
        ),
        pytest.param(
            {"project_id": PROJECT, "execution": "flat-string"},
            {"execution": {"project_id": PROJECT}},
            id="job_execution_nonmapping_str_despite_valid_flat",
        ),
        pytest.param({"project_id": PROJECT}, {"execution": []}, id="operation_execution_nonmapping_list"),
        pytest.param(
            {"project_id": PROJECT}, {"t35_synthetic": True},
            id="operation_names_no_project_id",
        ),
        pytest.param(
            {"project_id": PROJECT}, {"arguments": {"project_id": PROJECT}},
            id="operation_arguments_only_gives_no_binding",
        ),
        pytest.param(
            {"t35_synthetic": True}, {"t35_synthetic": True},
            id="no_project_id_anywhere",
        ),
        pytest.param(
            {"execution": {}}, {"execution": {"project_id": PROJECT}},
            id="empty_execution_names_no_binding",
        ),
        pytest.param(
            {"project_root": "/srv/alpha"}, {"execution": {"project_id": PROJECT}},
            id="project_root_only_gives_no_binding",
        ),
        pytest.param({"project_id": ""}, {"project_id": ""}, id="empty_flat_both_sides"),
        pytest.param(
            {"execution": {"project_id": ""}}, {"execution": {"project_id": PROJECT}},
            id="empty_nested_on_job",
        ),
        pytest.param({"project_id": 42}, {"project_id": PROJECT}, id="int_flat_claim"),
        pytest.param(
            {"execution": {"project_id": [PROJECT]}}, {"execution": {"project_id": PROJECT}},
            id="list_nested_claim",
        ),
        pytest.param({"project_id": {"value": PROJECT}}, {"project_id": PROJECT}, id="dict_flat_claim"),
        pytest.param({"project_id": None}, {"project_id": PROJECT}, id="present_none_flat_claim"),
        pytest.param({"project_id": True}, {"project_id": PROJECT}, id="bool_flat_claim"),
        pytest.param(
            {"project_id": PROJECT, "execution": {"project_id": OTHER}},
            {"execution": {"project_id": PROJECT}},
            id="within_job_conflict",
        ),
        pytest.param(
            {"project_id": PROJECT},
            {"project_id": OTHER, "execution": {"project_id": PROJECT}},
            id="within_operation_conflict",
        ),
        pytest.param({"project_id": PROJECT}, {"project_id": OTHER}, id="cross_side_flat_conflict"),
        pytest.param(
            {"project_id": PROJECT, "execution": {"project_id": PROJECT}},
            {"execution": {"project_id": OTHER}},
            id="cross_side_conflict_job_both_fields",
        ),
        pytest.param(
            {"project_id": PROJECT}, {"project_id": PROJECT.upper()},
            id="case_difference_is_not_agreement",
        ),
        pytest.param(
            {"project_id": PROJECT}, {"project_id": f"{PROJECT} "},
            id="trailing_space_is_not_agreement",
        ),
    ],
)
def test_refuses_ambiguous_missing_malformed_or_conflicting_claims(
    job_metadata: object, operation_metadata: object
) -> None:
    assert consistent_project_id(job_metadata, operation_metadata) is None


def test_helper_is_pure_and_deterministic() -> None:
    job_metadata = {"project_id": PROJECT, "cleanup": {"metadata_compacted": True}}
    operation_metadata = {
        "operation": "session.start",
        "execution": {"project_id": PROJECT},
    }
    job_before = deepcopy(job_metadata)
    operation_before = deepcopy(operation_metadata)
    first = consistent_project_id(job_metadata, operation_metadata)
    second = consistent_project_id(job_metadata, operation_metadata)
    assert first == PROJECT
    assert second == first
    assert job_metadata == job_before
    assert operation_metadata == operation_before
