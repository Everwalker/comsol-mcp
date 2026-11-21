"""Synthetic verification of the T15 version-policy tests, not native evidence.

The historical R31R2 descriptor capture is intentionally not replaced on disk;
these tests exercise the actual test functions with a clearly synthetic input.
"""
from __future__ import annotations

import pytest

from comsol_mcp import _g2_public_api as api
from tests import test_public_receiver_version_domain_r31r2 as contract


@pytest.fixture
def synthetic_descriptor(monkeypatch):
    descriptor = {
        "runtime_version": "COMSOL Multiphysics 6.4 (开发版本: 293)",
        "interfaces": [api.MODEL_ENTITY],
        "labels": [
            {"interface": api.MODEL_ENTITY, "method": "label", "parameters": [],
             "returns": "java.lang.String"},
            {"interface": api.MODEL_ENTITY, "method": "label",
             "parameters": ["java.lang.String"], "returns": api.MODEL_ENTITY},
        ],
    }
    monkeypatch.setattr(contract, "actual_native_descriptor", lambda: descriptor)


@pytest.mark.parametrize("raw", [
    "COMSOL Multiphysics 6.4 (开发版本: 294)",
    "COMSOL Multiphysics 6.3 (开发版本: 293)",
])
def test_synthetic_supported_release_runs_label_action(synthetic_descriptor, raw):
    contract.test_actual_copied_native_root_label_descriptor_accepts_supported_release(raw)


@pytest.mark.parametrize("raw", [
    "COMSOL Multiphysics 6.2 (开发版本: 293)",
    "COMSOL Multiphysics 6.5 (开发版本: 293)",
])
def test_synthetic_unsupported_release_refuses_label_action(synthetic_descriptor, raw):
    contract.test_actual_copied_native_root_label_descriptor_rejects_unsupported_release(raw)


def test_synthetic_generic_effect_and_raw_version_controls(synthetic_descriptor):
    contract.test_generic_api_describe_prepare_invoke_keep_effect_and_raw_version_evidence()


def test_synthetic_original_label_positive_control(synthetic_descriptor):
    contract.test_actual_copied_native_root_label_descriptor_runs_through_public_label_action()


def test_synthetic_original_order_positive_control(synthetic_descriptor):
    contract.test_localized_runtime_order_witness_flows_from_typed_index_producer_to_w23_consumer()
