"""Schema-only refusal controls for the known ModelRef bool schema_version defect.

Three pure in-memory checks using entirely synthetic identifiers. The two
refusal assertions document the current product defect: the discriminator
check `schema_version != MODEL_REF_SCHEMA_VERSION` lets JSON boolean true
pass because Python equates True with 1, so these assertions are expected to
be RED until the product is fixed. Scope is deliberately narrow: generation
is only ever the legal integer 1, and generation validation, bool revision
or ticket values, canonical_request_hash, permissions/fingerprints,
retire/rebind/external events, model or Worker/SSH/database interaction and
UNKNOWN states are out of scope, as is float handling (policy undecided).
No test here touches the fenced test_execution_contract.py IDs, and nothing
is executed by this file itself.
"""
from __future__ import annotations

import pytest  # pyright: ignore[reportMissingImports]

from comsol_mcp._execution_contract import (
    ExecutionContractError,
    ModelRef,
    model_ref_from_mapping,
)

SYNTHETIC = {
    "session_id": "synthetic-session-refusal-safe",
    "server_instance_id": "synthetic-server-refusal-safe",
    "model_tag": "synthetic-model-refusal-safe",
    "generation": 1,
}


def test_mapping_bool_schema_version_refused_as_model_identity_mismatch():
    with pytest.raises(ExecutionContractError) as refusal:
        model_ref_from_mapping({**SYNTHETIC, "schema_version": True})
    assert refusal.value.code == "MODEL_IDENTITY_MISMATCH"


def test_direct_construction_bool_schema_version_refused_as_model_identity_mismatch():
    with pytest.raises(ExecutionContractError) as refusal:
        ModelRef(**SYNTHETIC, schema_version=True)
    assert refusal.value.code == "MODEL_IDENTITY_MISMATCH"


def test_default_schema_version_positive_control_is_plain_integer_one():
    ref = ModelRef(**SYNTHETIC)
    assert type(ref.schema_version) is int and ref.schema_version == 1
    assert ref.generation == 1
