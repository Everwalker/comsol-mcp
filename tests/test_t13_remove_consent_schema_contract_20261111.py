"""T13 regression: the published node.remove consent JSON Schema must agree
with the executable pure validator ``validate_consent``.

Scope is deliberately the ``acknowledge`` leaf only.  The schema is obtained
from the real public describe route ``_g2_tools.registry_describe('node.remove')``
(nested path ``data.input_schema.properties.authorization.properties.acknowledge``)
and applied to candidate values with the existing ``jsonschema`` library.  The
same values go independently through the real pure function
``comsol_mcp._g2_general_remove.validate_consent`` against one legal NodePath.
Both judgements must agree with the consent contract:

* all three open proof obligations named exactly once (any order) -> both accept;
* one obligation missing, one obligation repeated, an unknown item, or an
  empty list -> both refuse.

This module is pure in-process inspection: no node.find, no cursor, no
discovery, no fallback engine path, no first-model UNKNOWN probe, no T14
membership, and no Worker/daemon/COMSOL/SSH access.  ``validate_consent``
never touches a receiver, and every refusal here is asserted to be a
PreWriteRefusal raised before any path-dependent work.

Known looseness pinned by the failing-side assertion: the published leaf is
``{"type": "array", "minItems": 1, "maxItems": 3, "items": {"enum": [...]}}``
with no ``uniqueItems``.  A two-item list of valid enum values therefore
passes the schema while the validator refuses it; this test asserts the
validator's stricter expectation on both sides, so a schema that stays loose
fails loudly instead of hiding the contract gap.
"""
from __future__ import annotations

import copy
from typing import Any

import jsonschema
import pytest

from comsol_mcp import _g2_tools
from comsol_mcp._execution_contract import PreWriteRefusal
from comsol_mcp._g2_general_remove import REQUIRED_ACKNOWLEDGEMENTS, validate_consent

ACK_EXTERNAL = "external_reference_impact_unverified"
ACK_DESCENDANT = "descendant_scope_follows_engine_semantics"
ACK_ENUMERATION = "enumeration_scope_candidate_vocabulary"
CANONICAL_ORDER = [ACK_EXTERNAL, ACK_DESCENDANT, ACK_ENUMERATION]

# One legal non-model-root NodePath wire value (same shape the reviewed T13
# portable tests use); validate_consent only parses it, it never resolves it.
TARGET: dict[str, Any] = {"segments": [
    {"collection": "component", "tag": "c1"},
    {"collection": "geom", "tag": "g1"},
    {"collection": "feature", "tag": "f1"},
]}


def acknowledge_leaf_schema() -> dict[str, Any]:
    """Fetch the acknowledge subschema through the actual public describe route."""
    described = _g2_tools.registry_describe("node.remove")
    assert described["success"] is True
    entry = described["data"]
    assert entry["operation_id"] == "node.remove"
    leaf = (entry["input_schema"]["properties"]["authorization"]
            ["properties"]["acknowledge"])
    # Exact nested shape of the published description, not a guessed API.
    assert isinstance(leaf, dict)
    assert leaf["type"] == "array"
    assert isinstance(leaf.get("items"), dict)
    assert sorted(leaf["items"]["enum"]) == sorted(CANONICAL_ORDER)
    assert sorted(REQUIRED_ACKNOWLEDGEMENTS) == sorted(CANONICAL_ORDER)
    return leaf


def schema_accepts(acknowledge: list[str]) -> bool:
    """jsonschema judgement of the acknowledge leaf against the published subschema."""
    return jsonschema.Draft202012Validator(acknowledge_leaf_schema()).is_valid(acknowledge)


def validator_accepts(acknowledge: list[str]) -> bool:
    """Pure-function judgement of the same value with a fixed legal path/authorization."""
    authorization = {"scope": "target_subtree",
                     "target_path": copy.deepcopy(TARGET),
                     "acknowledge": list(acknowledge)}
    try:
        result = validate_consent(copy.deepcopy(TARGET), authorization)
    except PreWriteRefusal:
        return False
    assert result["acknowledge"] == sorted(CANONICAL_ORDER)
    return True


@pytest.mark.parametrize("acknowledge", [
    list(CANONICAL_ORDER),
    list(reversed(CANONICAL_ORDER)),
    [ACK_DESCENDANT, ACK_ENUMERATION, ACK_EXTERNAL],
], ids=["canonical_order", "reversed_order", "rotation"])
def test_each_obligation_exactly_once_is_accepted_by_both_sides(acknowledge: list[str]) -> None:
    assert schema_accepts(acknowledge) is True
    assert validator_accepts(acknowledge) is True


def test_missing_one_obligation_is_refused_by_both_sides() -> None:
    acknowledge = [ACK_EXTERNAL, ACK_DESCENDANT]
    assert schema_accepts(acknowledge) is False
    assert validator_accepts(acknowledge) is False


def test_duplicate_obligation_is_refused_by_both_sides() -> None:
    acknowledge = [*CANONICAL_ORDER, ACK_EXTERNAL]
    assert schema_accepts(acknowledge) is False
    assert validator_accepts(acknowledge) is False


def test_unknown_obligation_is_refused_by_both_sides() -> None:
    acknowledge = [ACK_EXTERNAL, ACK_DESCENDANT, "totally_unknown_obligation"]
    assert schema_accepts(acknowledge) is False
    assert validator_accepts(acknowledge) is False


def test_empty_acknowledge_list_is_refused_by_both_sides() -> None:
    assert schema_accepts([]) is False
    assert validator_accepts([]) is False
