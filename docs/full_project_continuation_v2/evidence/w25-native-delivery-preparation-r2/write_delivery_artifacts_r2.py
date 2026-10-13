#!/usr/bin/env python3
"""Write immutable R2 runtime and Desktop launch-candidate receipts."""
from __future__ import annotations

import hashlib
import json
import pathlib
import plistlib
import stat
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

REPO = pathlib.Path(__file__).resolve().parents[4]
EVIDENCE = pathlib.Path(__file__).resolve().parent
RUNTIME_PLAN = json.loads((EVIDENCE / "ISOLATED_ARM64_RUNTIME_PLAN_R2.json").read_text())
RUNTIME_RECEIPT = json.loads((EVIDENCE / "ARM64_RUNTIME_INSTALL_RECEIPT_R2.json").read_text())
MODEL_COPY = json.loads((EVIDENCE / "GUI_MODEL_TASK_COPY_R2.json").read_text())
TASK = pathlib.Path(RUNTIME_RECEIPT["task_root"])


def sha(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def exact_file(path: pathlib.Path) -> dict:
    info = path.stat()
    return {"path": str(path), "sha256": sha(path), "size_bytes": info.st_size,
            "device": info.st_dev, "inode": info.st_ino}


def write_once(path: pathlib.Path, value: dict) -> str:
    if path.exists() or path.is_symlink():
        raise SystemExit(f"no-clobber refusal: {path}")
    raw = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    fd = path.open("xb")
    with fd:
        fd.write(raw)
        fd.flush()
        import os
        os.fsync(fd.fileno())
    return hashlib.sha256(raw).hexdigest()


def main() -> int:
    app = pathlib.Path("/Applications/COMSOL64/Multiphysics/COMSOL Multiphysics.app")
    info_path = app / "Contents/Info.plist"
    plist = plistlib.loads(info_path.read_bytes())
    app_paths = {
        "bundle_info_plist": info_path,
        "bundle_launcher": app / "Contents/MacOS/ComsolLauncher",
        "native_command": pathlib.Path("/Applications/COMSOL64/Multiphysics/bin/macarm64/comsol"),
        "native_command_ini": pathlib.Path("/Applications/COMSOL64/Multiphysics/bin/macarm64/comsol.ini"),
    }
    app_files = {key: exact_file(path) for key, path in app_paths.items()}
    model_path = pathlib.Path(MODEL_COPY["target"]["path"])
    with zipfile.ZipFile(model_path) as mph:
        dmodel = ET.fromstring(mph.read("dmodel.xml"))
        studies = [{"tag": node.attrib.get("tag"), "name": node.attrib.get("name")}
                   for node in dmodel.iter("Study")]
        solvers = [{"tag": node.attrib.get("tag"), "name": node.attrib.get("name")}
                   for node in dmodel.iter("SolverSequence")]
        model_shape = {
            "fileversion": mph.read("fileversion").decode().strip(),
            "study_nodes": studies,
            "study_count": len(studies),
            "solver_sequence_nodes": solvers,
            "solver_sequence_count": len(solvers),
            "embedded_solution_present": "solution1.mphbin" in mph.namelist(),
        }
    launch_candidate = {
        "schema": "comsol-mcp.w25-desktop-launch-candidate/2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PREPARED_NOT_LAUNCHED_WAITING_FOR_SERIAL_NATIVE_LANE",
        "host_observed_readonly": {"os": "macOS 27.0", "architecture": "arm64"},
        "installed_bundle": {
            "path": str(app),
            "identifier": plist.get("CFBundleIdentifier"),
            "bundle_short_version": plist.get("CFBundleShortVersionString"),
            "bundle_version": plist.get("CFBundleVersion"),
            "minimum_os": plist.get("LSMinimumSystemVersion"),
            "architectures": plist.get("LSArchitecturePriority"),
            "files": app_files,
            "runtime_build": "UNVERIFIED_FROM_INSTALLED_PLIST; confirm only after serial launch",
        },
        "model": {
            "source_path": MODEL_COPY["source"]["path"],
            "source_identity": MODEL_COPY["source"]["identity"],
            "source_sha256": MODEL_COPY["source"]["sha256"],
            "task_copy_receipt": str(EVIDENCE / "GUI_MODEL_TASK_COPY_R2.json"),
            "task_copy_receipt_sha256": sha(EVIDENCE / "GUI_MODEL_TASK_COPY_R2.json"),
            "path": str(model_path),
            "identity": MODEL_COPY["target"]["file_identity"],
            "sha256": MODEL_COPY["target"]["sha256"],
            "size_bytes": MODEL_COPY["target"]["size_bytes"],
            "registered_artifact": False,
            "shape": model_shape,
        },
        "documented_launch": {
            "argv": [str(app_paths["native_command"]), "-open", str(model_path)],
            "shell": False,
            "document": {
                "doc_id": 6232, "chunk_id": 19821,
                "title": "COMSOL 6.4 - COMSOL Commands on macOS",
                "sha256": "36a9fa631e932121c1d6332d43dd86b433d80f237b88463d54544c310788500b",
                "support": "standalone COMSOL command supports -open <file>, described as Open application",
            },
            "argv_executed": False,
        },
        "serial_preflight": {
            "launch_authorized_by_goal": True,
            "launch_now": False,
            "reason": "COMSOL native lane currently serializes all runtime work; launch waits for explicit lane handoff.",
            "process_inventory": "UNAVAILABLE; prior read-only process-list attempt returned operation-not-permitted; recheck before any scheduled launch.",
            "must_refuse_if_existing_comsol_gui": True,
            "require_exact_single_process_identity": ["PID", "process_birth", "UID", "executable_identity", "bundle_id", "login/session"],
        },
        "observation_budget": {
            "deadline_seconds": 600, "processes": 1, "windows": 1, "ax_nodes": 4096,
            "optional_exact_window_capture_count": 1, "mutations": 0,
            "study_submissions": 0, "solver_calls": 0, "permission_prompts": 0,
        },
        "gates": [
            "macOS 14.0+ required before SCScreenshotManager; current host is macOS 27.0.",
            "Preflight only: AXIsProcessTrusted and CGPreflightScreenCaptureAccess; false/unknown means no prompt and no capture.",
            "Unique Quartz CGWindow owner/window must match ScreenCaptureKit SCWindow.windowID() and owningApplication().processID().",
            "Read bounded AX metadata only from the exact COMSOL app/window; do not inspect document text or other windows.",
            "Any unexpected existing app, identity drift, permission/license prompt, ambiguous window, or source/copy drift stops the task.",
            "This saved fixture has Study std1, SolverSequence sol1, and an embedded solution; never run Study or solver.",
            "Do not create an observed selector profile until native observations on the exact runtime/build have been independently reviewed.",
        ],
        "status_limits": [
            "Task-owned file copy only; not yet registered as a project artifact.",
            "COMSOL Desktop launch, runtime build identity, GUI observation, capture, selector profile, and Desktop-to-Worker model binding remain NOT_RUN.",
            "No GUI, COMSOL, solver, or permission API was called while preparing this candidate.",
        ],
    }
    launch_path = EVIDENCE / "DESKTOP_LAUNCH_CANDIDATE_R2.json"
    launch_sha = write_once(launch_path, launch_candidate)

    wheels = []
    for row in RUNTIME_PLAN["wheelhouse"]["wheels"]:
        path = TASK / "wheelhouse" / row["filename"]
        observed = exact_file(path)
        if observed["sha256"] != row["sha256"] or observed["size_bytes"] != row["size_bytes"]:
            raise SystemExit(f"wheel mismatch: {path}")
        wheels.append(observed)
    if len(wheels) != 8 or sum(row["size_bytes"] for row in wheels) != 7388559:
        raise SystemExit("wheelhouse count/aggregate size mismatch")

    parent_refs = []
    for label, relative in (
        ("R5_source_freeze", "local-preservation/w25-native-adapter-20261012-r5-r3/R5_SOURCE_FREEZE_R3.json"),
        ("WFP_V2_R4_source_freeze", "docs/full_project_continuation_v2/evidence/windows_host_change_wfp_v2_recovery_r4_20261012/SOURCE_FREEZE_V2_R4.json"),
        ("WFP_V2_R4_preservation", "local-preservation/windows_host_change_wfp_v2_recovery_r4_20261012/PRESERVATION.json"),
    ):
        parent_refs.append({"name": label, "path": relative, "sha256": sha(REPO / relative)})

    freeze = {
        "schema": "comsol-mcp.w25-arm64-delivery-freeze/2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "SOFTWARE_PREPARATION_PASS; GUI_NOT_RUN; R5_WFP_COMBINED_TEST_PENDING",
        "task_runtime": {
            "root": str(TASK), "root_identity": RUNTIME_RECEIPT["task_root_identity"],
            "python": RUNTIME_RECEIPT["interpreter"], "site": RUNTIME_RECEIPT["site_path"],
            "site_identity": RUNTIME_RECEIPT["site_identity"],
            "site_file_count": RUNTIME_RECEIPT["site_manifest"]["file_count"],
            "site_total_bytes": RUNTIME_RECEIPT["site_manifest"]["total_bytes"],
            "site_manifest": {"path": str(EVIDENCE / "ARM64_SITE_FILES_R2.json"),
                              "sha256": sha(EVIDENCE / "ARM64_SITE_FILES_R2.json")},
        },
        "wheelhouse": {"count": len(wheels), "total_bytes": sum(x["size_bytes"] for x in wheels),
                       "files": wheels},
        "requirements": {
            "task_path": RUNTIME_PLAN["requirements_file"],
            "task_sha256": RUNTIME_RECEIPT["requirements_sha256"],
            "evidence_copy": str(EVIDENCE / "requirements-arm64-R2.txt"),
            "evidence_copy_sha256": sha(EVIDENCE / "requirements-arm64-R2.txt"),
        },
        "download": {"argv": RUNTIME_RECEIPT["download"]["argv"],
                     "exit_code": RUNTIME_RECEIPT["download"]["exit_code"],
                     "stdout_status": "NOT_CAPTURED",
                     "script_sha256": RUNTIME_RECEIPT["download"]["script_sha256"]},
        "install": {"argv": RUNTIME_RECEIPT["install"]["argv"],
                    "exit_code": RUNTIME_RECEIPT["install"]["exit_code"],
                    "stdout_status": "NOT_CAPTURED",
                    "method": RUNTIME_RECEIPT["install"]["method"]},
        "import_smoke": {
            "status": "PASS_SOFTWARE_ONLY",
            "modules": [row["module"] for row in RUNTIME_RECEIPT["post_install_imports"]],
            "screencapturekit_binding_present": True,
            "screen_capture_api_invoked": False,
            "gui_calls": 0, "permission_checks": 0, "comsol_launches": 0, "solver_calls": 0,
            "verifier_sha256": RUNTIME_RECEIPT["verification_script_sha256"],
        },
        "model_copy": {"receipt_path": str(EVIDENCE / "GUI_MODEL_TASK_COPY_R2.json"),
                       "receipt_sha256": sha(EVIDENCE / "GUI_MODEL_TASK_COPY_R2.json"),
                       "status": MODEL_COPY["status"], "target": MODEL_COPY["target"]},
        "desktop_launch_candidate": {"path": str(launch_path), "sha256": launch_sha,
                                     "status": launch_candidate["status"]},
        "parent_freezes": parent_refs,
        "limits": [
            "Download and pip install stdout were not captured; no raw output is claimed.",
            "The recovered Python runtime, system Python, live pyproject.toml and uv.lock were not modified.",
            "Windows native ACL behavior, Intel wheel build, COMSOL GUI, model binding, and numerical acceptance are NOT_RUN.",
        ],
    }
    freeze_path = EVIDENCE / "ARM64_DELIVERY_FREEZE_R2.json"
    freeze_sha = write_once(freeze_path, freeze)
    print(json.dumps({"launch_candidate": str(launch_path), "launch_candidate_sha256": launch_sha,
                      "freeze": str(freeze_path), "freeze_sha256": freeze_sha,
                      "wheel_count": len(wheels), "wheel_bytes": sum(x["size_bytes"] for x in wheels)},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
