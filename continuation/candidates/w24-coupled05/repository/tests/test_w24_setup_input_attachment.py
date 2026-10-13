"""Actual immutable current11 producer bytes and production attachment gates."""
from pathlib import Path
import copy,json,uuid
import pytest
from tools import w24_setup_input_attachment as attachment
from tools import run_native_w24_cure_science as science
ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "CURRENT11_INPUT_ATTACHMENT_V1.json"
RECEIPT = attachment.EVIDENCE / "native-run-current11-001/configured_template_receipt.json"
def manifest_file(value):
    path = ROOT / "test-attachment-manifests" / (uuid.uuid4().hex+".json")
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(value,indent=2)+"\n")
    return path

def test_actual_current11_immutable_producer_gate_preserves_original_receipt_and_v1_claim():
    original = RECEIPT.read_bytes()
    result = science.validate_template_receipt(RECEIPT, attachment_manifest=MANIFEST)
    assert RECEIPT.read_bytes() == original
    assert "fixture_readback" not in json.loads(original)
    assert result["input_attachment"]["verified_file_count"] == 133
    assert result["input_attachment"]["comparison"]["matches"] is True
    assert result["cure_law_v2_capture"]["enabled"] is False
    assert result["input_attachment"]["historical_refs"].startswith("PROVENANCE_ONLY_TERMINATED")

@pytest.mark.parametrize("mutation", ["extra_schema","missing_schema","duplicate_file","missing_file","wrong_bytes","wrong_hash","wrong_uid","bool_uid","extra_row","role_alias","missing_role","wrong_receipt","escape","wrong_review"])
def test_attachment_closed_schema_origin_owner_and_complete_input_closure_refuse_tampering(mutation):
    value = json.loads(MANIFEST.read_text())
    if mutation == "extra_schema": value["extra"] = 1
    elif mutation == "missing_schema": value.pop("schema")
    elif mutation == "duplicate_file": value["files"].append(copy.deepcopy(value["files"][0]))
    elif mutation == "missing_file": value["files"].pop()
    elif mutation == "wrong_bytes": value["files"][0]["bytes"] += 1
    elif mutation == "wrong_hash": value["files"][0]["sha256"] = "0"*64
    elif mutation == "wrong_uid": value["files"][0]["uid"] += 1
    elif mutation == "bool_uid": value["files"][0]["uid"] = True
    elif mutation == "extra_row": value["files"][0]["extra"] = 1
    elif mutation == "role_alias": value["roles"]["build_readback"] = value["roles"]["reopen_readback"]
    elif mutation == "missing_role": value["roles"].pop("operations_db")
    elif mutation == "wrong_receipt": value["roles"]["template_receipt"] = value["roles"]["build_readback"]
    elif mutation == "escape": value["files"][0]["path"] = "/etc/hosts"
    elif mutation == "wrong_review": value["roles"]["root_review"] = value["roles"]["preservation_manifest"]
    with pytest.raises((attachment.AttachmentError,KeyError)):
        attachment.validate_attachment(manifest_file(value),RECEIPT)

def test_duplicate_json_keys_are_rejected_before_gate():
    p = manifest_file({});p.write_text('{"schema":1,"schema":2}')
    with pytest.raises(attachment.AttachmentError,match="duplicate"):
        attachment.validate_attachment(p,RECEIPT)

def test_actual_physical_configuration_or_mesh_gate_failure_cannot_enter_normalized_readiness(monkeypatch):
    from tools import run_native_w24_cure_preflight as preflight
    monkeypatch.setattr(preflight,"_compare_reopened_readback",lambda *args:{"matches":False})
    with pytest.raises(attachment.AttachmentError,match="strict 21field"):
        attachment.validate_attachment(MANIFEST,RECEIPT)

def test_modern_v1_is_typed_non_authorizing_readiness_and_cannot_pass_native_candidate_admission(tmp_path):
    output = tmp_path / "readiness.json"
    result = science.prepare_campaign_candidate(RECEIPT,output,attachment_manifest=MANIFEST)
    assert result["status"] == "NEEDS_NATIVE_V2_CONFIGURATION_AND_STABLE_GEL_SHAPE_TRANSFER"
    assert result["study_run_submissions_authorized_by_candidate"] == 0
    frozen = json.loads(output.read_text())
    assert frozen["approval_status"] == "NOT_APPROVED"
    assert frozen["native_admission"] == "REFUSED_READINESS_ONLY_NO_ENGINE_BIRTH"
    assert frozen["stress_components_from_complete_native_equation_view"]["status"] == "AUTHENTICATED_HISTORICAL_CONFIGURATION_MAPPING_NOT_EVALUATED"
    setup = science.validate_template_receipt(RECEIPT,attachment_manifest=MANIFEST)
    with pytest.raises(science.CampaignError,match="schema/status/freeze"):
        science.validate_campaign_candidate(output,result["freeze_sha256"],tmp_path/"absent-approval.json",setup)
