from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from tools.run_native_w24_cure_science import CampaignError
from tools.run_native_w24_static_shape_setup import _validate_generated_study_solver_chain


_CANDIDATE_ROOT = Path(__file__).resolve().parents[2]
_RAW_RESPONSE = _CANDIDATE_ROOT / "evidence/actual_r7_failure/R7_BUILD_RESPONSE_RAW_R1.json"
_BASE_RECEIPT = _CANDIDATE_ROOT / "evidence/W24_R8_BASE_AND_ACTUAL_RAW_RECEIPT_R1.json"


def _actual_r7_chain() -> dict[str, Any]:
    raw = _RAW_RESPONSE.read_bytes()
    receipt = json.loads(_BASE_RECEIPT.read_text(encoding="utf-8"))
    expected_hash = receipt["actual_r7_raw_response"]["sha256"]
    assert hashlib.sha256(raw).hexdigest() == expected_hash
    response = json.loads(raw)
    return response["data"]["readback"]["readback"]["study_readback"]["generated_study_solver_chain"]


def _r8_producer_projection(actual_r7: dict[str, Any]) -> dict[str, Any]:
    """Apply only the R8 producer's schema/summary correction to captured R7 readbacks."""
    chain = copy.deepcopy(actual_r7)
    assert chain["schema"] == "W24_GENERATED_STUDY_SOLVER_CHAIN_R1"
    assert chain["semantic_findings"] == ["STUDY_STEP_DEFAULT_INITIALIZATION_DIFFERS:time"]
    assert chain["study_step_initialization_defaults"] == {
        "phasei": {"initmethod": "init", "initstudy": "zero", "useinitsol": "off"},
        "time": {"initmethod": "sol", "initstudy": "stdShape", "useinitsol": "off"},
    }
    chain["schema"] = "W24_GENERATED_STUDY_SOLVER_CHAIN_R2"
    chain["study_step_initialization_readback"] = chain.pop("study_step_initialization_defaults")
    chain["semantic_findings"] = []
    chain["status"] = "PHASE_INIT_TO_TIME_CHAIN_VALIDATED"
    return chain


def _set_step_initialization(chain: dict[str, Any], tag: str, name: str, value: str) -> None:
    step = next(row for row in chain["study_steps"] if row["requested_tag"] == tag)
    property_row = step["properties"]["properties"][name]
    property_row["value_capture"]["value"] = value
    chain["study_step_initialization_readback"][tag][name] = value


def test_r8_consumer_accepts_exact_actual_r7_topology_after_step_specific_contract_fix():
    raw_chain = _actual_r7_chain()
    chain = _r8_producer_projection(raw_chain)
    result = _validate_generated_study_solver_chain(
        chain,
        expected_solver_tags=["sol1", "sol2"],
        expected_attached_tag="sol1",
        expected_stored_tag="sol2",
    )
    assert result == {
        "attached_sequence_tag": "sol1",
        "stored_output_tag": "sol2",
        "sequence_tags": ["sol1", "sol2"],
    }


def test_r8_consumer_keeps_unprojected_r7_failure_rejected():
    with pytest.raises(CampaignError):
        _validate_generated_study_solver_chain(_actual_r7_chain())


@pytest.mark.parametrize(
    ("property_name", "bad_value"),
    [("initmethod", "init"), ("initstudy", "zero"), ("useinitsol", "on")],
)
def test_r8_consumer_rejects_wrong_transient_step_initialization(property_name: str, bad_value: str):
    chain = _r8_producer_projection(_actual_r7_chain())
    _set_step_initialization(chain, "time", property_name, bad_value)
    with pytest.raises(CampaignError):
        _validate_generated_study_solver_chain(chain)


def test_r8_consumer_rejects_a_nonempty_semantic_finding_even_with_complete_raw_topology():
    chain = _r8_producer_projection(_actual_r7_chain())
    chain["semantic_findings"] = ["UNEXPECTED_CHAIN_FINDING"]
    with pytest.raises(CampaignError):
        _validate_generated_study_solver_chain(chain)

