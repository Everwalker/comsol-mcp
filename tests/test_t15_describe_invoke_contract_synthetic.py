"""Synthetic T15 controls: api.describe callability claims must match the invoke gate.

The invoke gate (prepare_invoke -> supports) requires the exact signature row AND its
owner interface inside the observed receiver metadata.  A reviewed signature whose
owner interface is absent from the observed interface list (a synthetic witness of
Java interface-list truncation, where getMethods can surface an inherited declaring
interface) must stay REVIEWED but must not be published as callable.  Software-only
controls; they do not claim any native COMSOL observation.
"""
from __future__ import annotations

import jsonschema
import pytest

from comsol_mcp import _g2_public_api as api
from comsol_mcp import _g2_registry as registry
from comsol_mcp._execution_contract import ExecutionContractError
from tests import test_public_receiver_version_domain_r31r2 as contract

PATH = {"segments": [{"collection": "feature", "tag": "featureA"}]}
READ_REQUEST = {"path": PATH, "method": "comments", "arguments": [], "declared_effect": "READ"}
ROW_KEYS = {"interface", "method", "parameters", "returns", "policy_status", "invoke_allowed",
            "effect", "permission", "supported_releases", "argument_contract", "return_contract",
            "documentation", "controlled_alternative"}


def observed_descriptor(owner_interface_listed: bool) -> dict:
    interfaces = [api.MODEL_ENTITY] if owner_interface_listed else ["com.comsol.model.Truncated"]
    return {"runtime_version": "COMSOL Multiphysics 6.4 (开发版本: 293)",
            "interfaces": interfaces,
            "methods": [
                {"interface": api.MODEL_ENTITY, "method": "comments", "parameters": [],
                 "returns": "java.lang.String"},
                {"interface": api.MODEL_ENTITY, "method": "comments",
                 "parameters": ["java.lang.String"], "returns": api.MODEL_ENTITY},
                {"interface": api.MODEL_ENTITY, "method": "resolveModelPath", "parameters": [],
                 "returns": "java.lang.String"},
            ]}


def described_rows(worker) -> list[dict]:
    return api.describe_api(worker, "m", {"path": PATH})["methods"]


def test_reviewed_signature_without_receiver_interface_evidence_is_not_callable():
    worker = contract.DescriptorWorker(observed_descriptor(owner_interface_listed=False))
    with pytest.raises(ExecutionContractError) as error:
        api.prepare_invoke(worker, "m", READ_REQUEST)
    assert error.value.code == "API_UNSUPPORTED"
    comments = [row for row in described_rows(worker) if row["method"] == "comments"]
    assert len(comments) == 2
    for row in comments:
        assert row["policy_status"] == "REVIEWED"
        assert row["invoke_allowed"] is False
        assert row["controlled_alternative"] is None


def test_full_receiver_evidence_keeps_reviewed_signatures_callable():
    worker = contract.DescriptorWorker(observed_descriptor(owner_interface_listed=True))
    comments = [row for row in described_rows(worker) if row["method"] == "comments"]
    assert comments
    assert all(row["policy_status"] == "REVIEWED" and row["invoke_allowed"] is True
               for row in comments)
    prepared = api.prepare_invoke(worker, "m", READ_REQUEST)
    assert prepared["effect"] == "inspect"


def test_unreviewed_signature_stays_unreviewed_and_not_callable():
    worker = contract.DescriptorWorker(observed_descriptor(owner_interface_listed=True))
    unknown = next(row for row in described_rows(worker) if row["method"] == "resolveModelPath")
    assert unknown["policy_status"] == "UNREVIEWED"
    assert unknown["invoke_allowed"] is False
    assert unknown["controlled_alternative"] == {
        "operation": "code.execute_java", "permission": "trusted_code",
        "authorization_required": True, "authorizes_this_signature": False,
    }


@pytest.mark.parametrize("owner_interface_listed", [True, False])
def test_describe_row_output_schema_is_unchanged(owner_interface_listed):
    worker = contract.DescriptorWorker(observed_descriptor(owner_interface_listed))
    methods = described_rows(worker)
    assert all(set(row) == ROW_KEYS for row in methods)
    methods_schema = registry.operation_describe("api.describe")["data_schema"]["properties"]["methods"]
    jsonschema.validate(methods, methods_schema)

