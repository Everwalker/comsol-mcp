"""R26 arity-aware witness and documented Physics identifier/name metadata."""
from __future__ import annotations

import pytest

from comsol_mcp._domain_outcome import DispatchWitness, is_mutation_call
from tools.w23_direction_discovery import correlate_physics_identity


def test_physics_identifier_witness_is_read_only_only_for_zero_arg_call():
    witness = DispatchWitness()
    witness.record("identifier", command="call", receiver="owned-physics")
    assert witness.mutation_issued is False
    assert witness.dispatches == [{
        "command": "call", "method": "identifier", "args_count": 0,
        "receiver": "owned-physics", "is_mutation": False,
    }]

    setter = DispatchWitness()
    setter.record("identifier", "emw", command="call", receiver="owned-physics")
    assert setter.mutation_issued is True
    assert setter.dispatches[0]["is_mutation"] is True


def test_identifier_read_classification_stays_fail_closed_for_other_arities_and_commands():
    assert is_mutation_call("identifier", (), command="call", receiver="physics") is False
    assert is_mutation_call("identifier", ("emw",), command="call", receiver="physics") is True
    assert is_mutation_call("identifier", (), command="g2_nodegroup_read", receiver="physics") is True
    assert is_mutation_call("unreviewed_identifier", (), command="call", receiver="physics") is True


@pytest.mark.parametrize(
    ("tag", "identifier", "identifier_status", "alias_equal", "expected_status"),
    [
        ("ewfd", "ewfd", "OBSERVED", True, "IDENTIFIER_SCOPE_MISMATCH_UNRESOLVED"),
        ("ewfd", "emw", "OBSERVED", False, "IDENTIFIER_SCOPE_MATCH"),
        ("ewfd", None, "UNAVAILABLE", None, "IDENTIFIER_UNRESOLVED"),
    ],
)
def test_documented_identifier_name_scope_correlates_exactly_and_keeps_tag_separate(
        tag, identifier, identifier_status, alias_equal, expected_status):
    result = correlate_physics_identity(
        tag, identifier, identifier_status, variable_prefix="emw")

    assert result["status"] == expected_status
    assert result["physics_identifier_semantics"] == "DOCUMENTED_NAME_SCOPE_METADATA_CORRELATION_ONLY"
    assert result["physics_identifier_api_deprecated"] is True
    assert result["physics_identifier_tag_alias_equal"] is alias_equal
    assert result["observed_field_variable_prefix"] == "emw"
    assert result["identifier_prefix_equal"] is (identifier == "emw" if identifier_status == "OBSERVED" else None)
    if identifier_status == "OBSERVED":
        assert result["physics_identifier"] == identifier
        assert result["physics_tag"] == "ewfd"


def test_duplicate_or_incomplete_identifier_inventory_never_resolves():
    duplicate = correlate_physics_identity(
        "ewfd", "emw", "OBSERVED", physics_identifier_inventory=[
            {"identifier": "emw", "identifier_status": "OBSERVED"},
            {"identifier": "emw", "identifier_status": "OBSERVED"},
        ])
    incomplete = correlate_physics_identity(
        "ewfd", "emw", "OBSERVED", physics_identifier_inventory=[
            {"identifier": "emw", "identifier_status": "OBSERVED"},
            {"identifier": None, "identifier_status": "UNAVAILABLE"},
        ])
    assert duplicate["status"] == "IDENTIFIER_SCOPE_AMBIGUOUS_UNRESOLVED"
    assert incomplete["status"] == "IDENTIFIER_INVENTORY_INCOMPLETE_UNRESOLVED"

