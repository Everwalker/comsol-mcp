"""ModelRef wire-version type controls with entirely synthetic identifiers.

The schema discriminator is an integer; JSON boolean true must not be accepted
as version 1 just because Python equates them. This pure contract test does not
load or modify an actual model, database, Worker, project, or SSH connection.
"""
from __future__ import annotations

import pytest  # pyright: ignore[reportMissingImports]

from comsol_mcp._execution_contract import ExecutionContractError, ModelRef, model_ref_from_mapping


BASE = {'session_id': 'synthetic-session', 'server_instance_id': 'synthetic-server',
        'model_tag': 'synthetic-model', 'generation': 1}


def test_model_ref_from_mapping_rejects_json_boolean_schema_version():
    with pytest.raises(ExecutionContractError) as failure:
        model_ref_from_mapping({**BASE, 'schema_version': True})
    assert failure.value.code == 'MODEL_IDENTITY_MISMATCH'


def test_model_ref_constructor_rejects_boolean_schema_version():
    with pytest.raises(ExecutionContractError) as failure:
        ModelRef(**BASE, schema_version=True)
    assert failure.value.code == 'MODEL_IDENTITY_MISMATCH'


def test_model_ref_accepts_explicit_integer_schema_version_one():
    actual = model_ref_from_mapping({**BASE, 'schema_version': 1}).as_dict()
    assert type(actual['schema_version']) is int and actual['schema_version'] == 1
    assert actual['generation'] == 1


def test_model_ref_omitted_schema_version_defaults_to_integer_one():
    actual = model_ref_from_mapping(dict(BASE)).as_dict()
    assert type(actual['schema_version']) is int and actual['schema_version'] == 1


def test_model_ref_rejects_unsupported_integer_schema_version():
    with pytest.raises(ExecutionContractError) as failure:
        model_ref_from_mapping({**BASE, 'schema_version': 2})
    assert failure.value.code == 'MODEL_IDENTITY_MISMATCH'


def test_model_ref_rejects_boolean_generation():
    with pytest.raises(ExecutionContractError) as failure:
        model_ref_from_mapping({**BASE, 'generation': True})
    assert failure.value.code == 'MODEL_IDENTITY_MISMATCH'
