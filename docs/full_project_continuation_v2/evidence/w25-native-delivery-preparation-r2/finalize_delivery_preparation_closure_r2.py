#!/usr/bin/env python3
"""Create the final R2 delivery-preparation packet without altering prior receipts."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

REPO = pathlib.Path("/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository")
EVIDENCE = pathlib.Path(__file__).resolve().parent
COMBINED = EVIDENCE / "combined-r5-wfp-r4-r1"
RUNTIME_FREEZE = EVIDENCE / "ARM64_DELIVERY_FREEZE_R2.json"
COMBINED_CLOSURE = EVIDENCE / "COMBINED_R5_WFP_R4_CLOSURE_R1.json"
FINAL = EVIDENCE / "DELIVERY_PREPARATION_CLOSURE_R2.json"


def sha(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    if FINAL.exists() or FINAL.is_symlink():
        raise SystemExit(f"no-clobber refusal: {FINAL}")
    closure = json.loads(COMBINED_CLOSURE.read_text())
    raw_xml = COMBINED / "COMBINED_R5_WFP_R4_ATTEMPT3.junit.xml"
    xml_root = ET.parse(raw_xml).getroot()
    skipped = []
    for node in xml_root.iter("testcase"):
        for item in node.findall("skipped"):
            skipped.append({
                "class": node.attrib.get("classname"),
                "test": node.attrib.get("name"),
                "reason": item.attrib.get("message"),
            })
    if len(skipped) != 9:
        raise SystemExit(f"expected exactly nine platform skips, observed {len(skipped)}")
    if any(row["reason"] != "requires actual Windows kernel/filesystem" for row in skipped):
        raise SystemExit("skip reasons differ from the preserved JUnit")
    copied_files = closure["raw_preserved_files"]
    files_check = []
    for row in copied_files:
        path = pathlib.Path(row["target"])
        if sha(path) != row["sha256"] or path.stat().st_size != row["size_bytes"]:
            raise SystemExit(f"preserved artifact changed: {path}")
        files_check.append({"path": row["target"], "sha256": row["sha256"],
                            "size_bytes": row["size_bytes"]})
    refs = []
    for name, rel in (
        ("R5_root_review", "docs/full_project_continuation_v2/evidence/w25_native_adapter_root_review_r5/ROOT_SOURCE_REVIEW_R3.json"),
        ("WFP_R4_root_review", "docs/full_project_continuation_v2/evidence/windows_host_change_wfp_v2_recovery_r4_20261012/ROOT_SOURCE_REVIEW_R4.json"),
        ("WFP_R4_source_freeze", "docs/full_project_continuation_v2/evidence/windows_host_change_wfp_v2_recovery_r4_20261012/SOURCE_FREEZE_V2_R4.json"),
        ("WFP_R4_preservation", "local-preservation/windows_host_change_wfp_v2_recovery_r4_20261012/PRESERVATION.json"),
    ):
        path = REPO / rel
        refs.append({"name": name, "path": rel, "sha256": sha(path)})
    runtime = json.loads(RUNTIME_FREEZE.read_text())
    result = {
        "schema": "comsol-mcp.w25-delivery-preparation-closure/2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS_SCOPED_SOFTWARE_PREPARATION_AND_PRIVATE_REGRESSION",
        "arm64_runtime": {"path": str(RUNTIME_FREEZE), "sha256": sha(RUNTIME_FREEZE),
                          "status": runtime["status"],
                          "wheel_count": runtime["wheelhouse"]["count"],
                          "wheel_bytes": runtime["wheelhouse"]["total_bytes"],
                          "installed_site_manifest": runtime["task_runtime"]["site_manifest"],
                          "installed_site_files": runtime["task_runtime"]["site_file_count"],
                          "installed_site_bytes": runtime["task_runtime"]["site_total_bytes"],
                          "download_stdout": "NOT_CAPTURED",
                          "install_stdout": "NOT_CAPTURED",
                          "recovered_runtime_changed": False,
                          "system_python_changed": False,
                          "live_pyproject_or_uv_lock_changed": False},
        "task_owned_desktop_candidate": {
            "launch_plan": {"path": str(EVIDENCE / "DESKTOP_LAUNCH_CANDIDATE_R2.json"),
                            "sha256": sha(EVIDENCE / "DESKTOP_LAUNCH_CANDIDATE_R2.json"),
                            "status": "PREPARED_NOT_LAUNCHED_WAITING_FOR_SERIAL_NATIVE_LANE"},
            "model_copy_receipt": {"path": str(EVIDENCE / "GUI_MODEL_TASK_COPY_R2.json"),
                                   "sha256": sha(EVIDENCE / "GUI_MODEL_TASK_COPY_R2.json")},
            "installed_app_bundle_version": "6.4.0",
            "runtime_build_293": "UNVERIFIED; requires later serial launch",
            "execution": {"gui_launched": False, "window_observed": False,
                          "capture": False, "permission_queries": 0, "solver_calls": 0},
            "authorization_note": "Task-owned directory/model-copy preparation is authorized by the existing Goal. COMSOL Desktop launch/observation waits for the current serial Native lane; no separate permission question is introduced. TCC prompts or permission changes remain out of scope.",
        },
        "combined_private_candidate": {
            "closure": {"path": str(COMBINED_CLOSURE), "sha256": sha(COMBINED_CLOSURE),
                        "status": closure["status"]},
            "candidate_root": closure["candidate"]["path"],
            "source_file_count": closure["candidate"]["source_count"],
            "candidate_source_tree_sha256": closure["candidate"]["source_tree_sha256"],
            "frozen_input_refs": refs,
            "test_modules": closure["test_scope"]["modules"],
            "result": {
                "pytest": closure["test_scope"]["pytest_stdout_summary"],
                "junit": closure["test_scope"]["junit_raw"],
                "source_files_hashed_before_and_after": closure["test_scope"]["source_hash_map_bound"]["file_count"],
                "source_input_sha256_map_before": closure["test_scope"]["source_hash_map_bound"]["before_sha256"],
                "source_input_sha256_map_after": closure["test_scope"]["source_hash_map_bound"]["after_sha256"],
                "source_inputs_identical": closure["test_scope"]["source_inputs_identical"],
                "network_attempts": closure["test_scope"]["environment"]["network_guard_attempts"],
                "explicitly_deselected": "tests/test_operation_store_migrations.py::historical_fixture_commit_and_blob_identity_resolve (private candidate has no historical Git object database; the exact WFP R4 invocation excludes it)",
                "skipped_tests": skipped,
                "subtests": 20,
            },
            "attempt_history": closure["preserved_prior_attempts"],
            "raw_artifacts": files_check,
        },
        "limits": [
            "Software tests ran against immutable private candidate sources; no COMSOL server or Desktop was started.",
            "The nine Windows kernel/filesystem controls remain skipped on macOS; Windows kernel is NOT_RUN.",
            "The isolated task runtime/import smoke is software-only; it does not prove ScreenCaptureKit invocation, GUI permission, or native selector behavior.",
            "Intel cryptography wheel build, actual delivery, and product/numerical acceptance remain OPEN or NOT_RUN.",
        ],
    }
    raw = (json.dumps(result, sort_keys=True, indent=2) + "\n").encode()
    fd = os.open(FINAL, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(raw)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                raise OSError("short closure write")
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)
    print(json.dumps({"path": str(FINAL), "sha256": hashlib.sha256(raw).hexdigest(),
                      "skip_count": len(skipped), "status": result["status"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
