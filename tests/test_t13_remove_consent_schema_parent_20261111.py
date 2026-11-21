"""Additional T13 consent-schema controls, without any engine access.

These controls use the author's actual describe/validator helpers.  In
particular, the duplicate stays within maxItems so maxItems alone cannot make
the duplicate-negative control pass.  The validator is checked first so a
schema failure cannot prevent the executable pre-write refusal from being
observed.  This is not an MCP transport or native COMSOL test.
"""
from __future__ import annotations

import pytest

from tests.test_t13_remove_consent_schema_contract_20261111 import (
    ACK_DESCENDANT,
    ACK_EXTERNAL,
    schema_accepts,
    validator_accepts,
)


@pytest.mark.parametrize(
    "acknowledge",
    [
        [ACK_EXTERNAL, ACK_DESCENDANT],
        [ACK_EXTERNAL, ACK_EXTERNAL, ACK_DESCENDANT],
    ],
    ids=["one_missing", "duplicate_within_max_items"],
)
def test_invalid_acknowledgements_are_refused_by_validator_and_schema(acknowledge):
    assert validator_accepts(acknowledge) is False
    assert schema_accepts(acknowledge) is False
