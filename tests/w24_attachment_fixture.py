"""Portable software-only attachment closure; no COMSOL/model authorization.

Historical raw operation/EquationView bytes are retained separately. The
rebound paths, saved-template bytes, SQLite and audit below are synthetic.
Every production attachment validator still runs against the complete closure.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import uuid

import pytest

from tests.test_w24_current11_config_projection import _actual_pair
from tests.test_w24_cure_science_runner import _synthetic_template_candidate
from tools import w24_setup_input_attachment as attachment
from tools import w24_witnessed_stress as witness

FIXTURES = Path(__file__).parent / "fixtures/w24_current11"


@pytest.fixture
def attachment_case(tmp_path, monkeypatch):
    work, receipt_path, _candidate_path, _candidate, _approval = _synthetic_template_candidate(tmp_path)
    evidence = work / "evidence"
    run = evidence / "native-run-current11-001"
    run.mkdir(parents=True)
    monkeypatch.setattr(attachment, "EVIDENCE", evidence)
    monkeypatch.setattr(attachment, "WORK", work)
    # Existing software factory writes a synthetic MPH and registered workspace
    # proof. Replace only its raw-table fixture with exact historical bytes.
    receipt = json.loads(receipt_path.read_text())
    workspace = Path(receipt["project_workspace"])
    raw = (FIXTURES / "solid_equation_view_expression_inventory.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == witness.INVENTORY_SHA
    inventory = json.loads(raw)
    inventory_paths = [workspace / "solid_equation_view_expression_inventory.json",
                       workspace / "solid_equation_view_expression_inventory_reopened.json",
                       run / "solid_equation_view_expression_inventory.json"]
    for path in inventory_paths:
        path.write_bytes(raw)
    mapping = dict(receipt["equation_view_inventory"])
    mapping.update(path=str(inventory_paths[0]), registered_project_path=str(inventory_paths[0]),
                   evidence_copy_path=str(inventory_paths[2]), size_bytes=len(raw),
                   sha256=witness.INVENTORY_SHA, feature_count=inventory["feature_count"],
                   expression_row_count=inventory["expression_row_count"],
                   stress_candidate_row_count=inventory["stress_candidate_row_count"])
    receipt["equation_view_inventory"] = mapping
    receipt["reopened_equation_view_inventory"] = dict(mapping, path=str(inventory_paths[1]))
    pair_root = work / "mesh-fixture"
    pair_root.mkdir()
    before, after = _actual_pair(pair_root)
    origins = []
    build_reopen = []
    for stage, readback in zip(("build", "reopen"), (before, after)):
        origin = json.loads(Path(readback["mesh_volume_observation"]["evidence_artifact_path"]).read_text())["native_response"]
        origins.append(origin)
        wrapped = {"fixture_readback": readback, stage + "_response": origin,
                   "study_run_submissions_so_far": [], "saved_template_sha256": receipt["sha256"]}
        target = run / ("native_" + stage + "_readback.json")
        target.write_text(json.dumps(wrapped))
        build_reopen.append(target)
    receipt.pop("fixture_readback")
    receipt["reopen_readback"] = str(build_reopen[1])
    receipt["save_response"] = {"success": True, "scope": "SYNTHETIC_SAVED_TEMPLATE_ONLY"}
    receipt_path = run / "configured_template_receipt.json"
    receipt_path.write_text(json.dumps(receipt))
    db = run / "private-control-store/work/control/operations.sqlite3"
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as conn:
        conn.execute("create table operations(status text,result text)")
        conn.executemany("insert into operations values('SUCCEEDED',?)",
                         [(json.dumps(origin),) for origin in [*origins, receipt["save_response"]]])
    preservation = run / "preservation_manifest.json"
    preservation.write_text(json.dumps({"files": []}))
    decision = work / "root_stress_decision.json"
    decision.write_bytes((FIXTURES / "root_stress_decision.json").read_bytes())
    monkeypatch.setattr(witness, "DECISION_PATH", decision)
    # The actual historical decision and EquationView remain byte-pinned.
    assert hashlib.sha256(decision.read_bytes()).hexdigest() == witness.DECISION_SHA
    row = lambda p: {"path": str(p), "bytes": p.stat().st_size,
                     "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "uid": os.getuid()}
    review = run / "synthetic_root_review.json"
    files = sorted(p for p in work.rglob("*") if p.is_file())
    review.write_text(json.dumps({"scope": "SYNTHETIC_SOFTWARE_AUDIT_ONLY",
                                "verified_references": [row(p) for p in files]}))
    monkeypatch.setattr(attachment, "ROOT_REVIEW_SHA", row(review)["sha256"])
    files.append(review)
    roles = {"root_review": str(review), "template_receipt": str(receipt_path),
             "build_readback": str(build_reopen[0]), "reopen_readback": str(build_reopen[1]),
             "preservation_manifest": str(preservation), "operations_db": str(db)}
    manifest_root = Path(__file__).resolve().parents[1] / "test-attachment-manifests"
    manifest_root.mkdir(exist_ok=True)
    manifest = manifest_root / (uuid.uuid4().hex + ".json")
    manifest.write_text(json.dumps({"schema": attachment.SCHEMA, "files": [row(p) for p in files], "roles": roles}))
    yield {"manifest": manifest, "receipt": receipt_path, "file_count": len(files),
           "manifest_root": manifest_root}
    # Preserve failure evidence in the task-owned work tree. These are not
    # historical MPH bytes, Root approvals or reusable native launch inputs.
