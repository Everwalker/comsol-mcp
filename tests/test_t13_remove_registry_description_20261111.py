"""T13 static description regression for the public node.remove contract.

Offline-only check: it imports ``comsol_mcp._g2_tools`` and calls the real
``registry_describe('node.remove')`` Python route once per module, then
inspects the published ``runtime_dispatch_contract`` wording. No engine,
Worker, daemon, model, network or dispatch behaviour is exercised, and the
test demands no new behaviour and no new permissions -- it only requires the
description to match what ``comsol_mcp/_g2_general_remove.py`` already does
and already discloses today.

Why the current description is wrong: the node.remove ``limits`` entry
"remove refuses unknown dependency coverage" reads as a blanket refusal,
while the published consent-gated ordinary/nested removal route executes a
single authorized ``ModelEntityList.remove(String)`` and explicitly reports
``reference_risk.level = "UNSCANNED"`` with ``dependency_check.complete =
False`` and no dependency-closure claim. The model-root NodeGroup structural
ungroup is a separate reviewed route that never accepts the consent field.

Evidence boundary: the 2026-11-10 run
``docs/universal_mcp_execution/evidence/full/T13-general-remove-live-managed-fake-20261110.md``
proves only the bounded in-memory managed-fake route check. It is not
evidence of a real MCP, native, or COMSOL deletion, and nothing asserted
here may be read as native acceptance.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import pytest

from comsol_mcp import _g2_tools

REQUIRED_ACKNOWLEDGEMENTS = {
    "external_reference_impact_unverified",
    "descendant_scope_follows_engine_semantics",
    "enumeration_scope_candidate_vocabulary",
}

# Words that scope a refusal claim to the missing-consent case (or another
# explicitly named lane). An unscoped refusal claim contradicts the executed
# consent route and must not survive in the published contract.
REFUSAL_QUALIFIERS = ("consent", "authorization", "unconsented", "unauthorized")


@pytest.fixture(scope="module")
def described() -> Mapping[str, Any]:
    response = _g2_tools.registry_describe("node.remove")
    assert response["success"] is True, response
    entry = response["data"]
    assert isinstance(entry, Mapping)
    assert entry.get("operation_id") == "node.remove"
    return entry


def _contract(entry: Mapping) -> Mapping:
    contract = entry.get("runtime_dispatch_contract")
    assert isinstance(contract, Mapping) and contract, (
        "node.remove must publish a runtime_dispatch_contract"
    )
    return contract


def _strings(value) -> list[str]:
    found: list[str] = []
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str):
                found.append(key)
            found.extend(_strings(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.extend(_strings(item))
    return found


def _haystack(contract: Mapping) -> str:
    return "\n".join(_strings(contract)).lower()


# --- RED drivers: the contract must disclose the executed consent route ---


def test_consent_route_is_disclosed_not_blanket_refused(described) -> None:
    text = _haystack(_contract(described))
    assert "consent" in text or "authorization" in text, (
        "runtime_dispatch_contract must disclose that ordinary/nested "
        "node.remove proceeds through an explicit consent/authorization "
        "object; describing removal only as a refusal contradicts "
        "_g2_general_remove.validate_consent"
    )


def test_unscanned_reference_risk_is_disclosed(described) -> None:
    text = _haystack(_contract(described))
    assert "unscanned" in text and "reference" in text, (
        "runtime_dispatch_contract must state that the consented route "
        "discloses external-reference impact as UNSCANNED (no public "
        "incoming-reference getter, no dependency-closure claim), as "
        "_g2_general_remove.execute already publishes in reference_risk"
    )


def test_no_unscoped_unknown_dependency_refusal_claim(described) -> None:
    for claim in _strings(_contract(described)):
        low = claim.lower()
        if re.search(r"refus\w*\s+unknown\s+dependency\s+coverage", low):
            assert any(qualifier in low for qualifier in REFUSAL_QUALIFIERS), (
                f"published limit {claim!r} claims a blanket removal "
                "refusal; the consent-gated route executes with "
                "reference_risk=UNSCANNED, so any refusal wording must be "
                "explicitly scoped to the missing-consent case"
            )


def test_model_root_nodegroup_ungroup_is_a_separate_route(described) -> None:
    text = _haystack(_contract(described))
    assert "nodegroup" in text and "ungroup" in text, (
        "runtime_dispatch_contract must identify the model-root NodeGroup "
        "structural ungroup as its own published route (_g2_group_remove) "
        "distinct from the consent-gated general removal"
    )


# --- GREEN locks: accuracy fixes must not remove existing honesty ---


def test_native_acceptance_stays_unverified(described) -> None:
    text = _haystack(_contract(described))
    assert "software" in text and "unverified" in text, (
        "the contract must keep the software-only scope with native "
        "capability/dependency acceptance separately UNVERIFIED; the "
        "2026-11-10 managed-fake evidence is not native deletion proof"
    )


def test_consent_input_contract_remains_exact(described) -> None:
    """The disclosure fix must not soften consent into a free pass."""
    properties = described["input_schema"]["properties"]
    authorization = properties["authorization"]
    assert set(authorization["required"]) == {"scope", "target_path", "acknowledge"}
    assert authorization["additionalProperties"] is False
    assert authorization["properties"]["scope"]["enum"] == ["target_subtree"]
    acknowledge = authorization["properties"]["acknowledge"]
    assert acknowledge["minItems"] == 3 and acknowledge["maxItems"] == 3
    assert acknowledge["uniqueItems"] is True
    assert set(acknowledge["items"]["enum"]) == REQUIRED_ACKNOWLEDGEMENTS


def test_closed_data_schema_still_names_disclosure_fields(described) -> None:
    data_schema = described["data_schema"]
    assert data_schema["additionalProperties"] is False
    for name in ("authorization", "retention", "reference_risk"):
        assert name in data_schema["properties"], (
            f"the closed node.remove data_schema must keep the disclosure "
            f"field {name!r} published by the consent route"
        )
