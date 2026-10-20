#!/usr/bin/env python3
"""Prepare or execute one W25 current175-r9 source-external offline install.

The execution path reuses the qualified W26 LifecycleLaunch captures,
original-process runner, and macOS sandbox network-denial gate. It creates one
fresh 42-wheel product venv and reads package, distribution, and nonprompt
product-caller status. It does not upgrade, roll back, uninstall, launch
COMSOL/JVM/GUI, enumerate windows, or inspect pixels.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import socket
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from tools import full_project_release as release
from tools import w26_application_lifecycle as lifecycle
from tools import w26_application_lifecycle_r9_actor as lifecycle_r9
from tools import w26_runtime_actor as runtime
from tools import w26_sealed_data as sealed_data
from tools import w26_sealed_data_r9 as sealed_data_r9
from tools.w26_prepare_current_inputs import _helpers, prepare as prepare_inputs


REPOSITORY = Path(__file__).resolve().parents[1]
STATE = REPOSITORY / "docs/full_project_continuation_v3/state"
BASE_INPUT_MANIFEST = Path("/private/tmp/cvw26-lifecycle-e791728f/INPUTS_MANIFEST.json")
DEPENDENCY_ROOT = Path("/private/tmp/cvw26-dependencies-2cd91a47")
APP_LOCK = REPOSITORY / "tools/w26_application_sources_175_r9_prepared.lock.json"
RESUME = STATE / "RESUME.json"
APP_VERSION = "0.1.10"
CAPTURE_LABELS = (
    "current-offline-install",
    "current-package_fingerprint",
    "current-distribution_fingerprint",
    "current-caller-desktop-metadata-status",
)
CALLER_SOURCE_HASHES = {
    "comsol_mcp._desktop_macos": "99e2128d6a28cabed58bb455ef74da5b0cd45c0e46c419eb2a65c682b37e156b",
    "comsol_mcp._desktop_platforms": "4c50c4596cbc8d79dd610d66cada1deb38e400c607a5bb04b59f1766a9c76059",
    "comsol_mcp._desktop_service": "00691307ad6b2c780f7086b4a306b4adbb5360e986754c3e9e97e2a4ea38dcf1",
    "comsol_mcp._desktop_selector_profile": "c29d6a439e3d2fb1e2b0e55b04486335d3ec22c15527e16d3e72ef67855b6c6c",
}
CURRENT_DESKTOP_MODULES = {
    "comsol_mcp._desktop_macos": "_desktop_macos.py",
    "comsol_mcp._desktop_platforms": "_desktop_platforms.py",
    "comsol_mcp._desktop_service": "_desktop_service.py",
    "comsol_mcp._desktop_selector_profile": "_desktop_selector_profile.py",
}
DESKTOP_OVERLAY_SCHEMA = "w25-current175-r9-desktop-source-overlay/1"
CALLER_STATUS_CODE = """import hashlib,json,os,pathlib,sys,jpype
from importlib import metadata
assert metadata.version('comsol-mcp')=='0.1.10'
root=pathlib.Path.cwd().parent.resolve(strict=True)
manifest_path=root/'CURRENT_DESKTOP_OVERLAY.json'
manifest=json.loads(manifest_path.read_text())
assert manifest.get('schema')=='w25-current175-r9-desktop-source-overlay/1'
overlay=(root/'current-desktop-overlay'/'comsol_mcp').resolve(strict=True)
module_filenames={'comsol_mcp._desktop_macos':'_desktop_macos.py','comsol_mcp._desktop_platforms':'_desktop_platforms.py','comsol_mcp._desktop_service':'_desktop_service.py','comsol_mcp._desktop_selector_profile':'_desktop_selector_profile.py'}
assert overlay.is_dir() and manifest.get('package_path')==str(overlay) and manifest.get('module_filenames')==module_filenames
assert set(manifest.get('source_records',{}))==set(module_filenames) and set(manifest.get('overlay_records',{}))==set(module_filenames)
sources={}
for name,filename in module_filenames.items():
 row=manifest['source_records'][name];copy=manifest['overlay_records'][name];path=overlay/filename
 assert pathlib.Path(copy['path'])==path and pathlib.Path(row['path']).is_absolute()
 digest=hashlib.sha256(path.read_bytes()).hexdigest()
 assert digest==row['sha256']==copy['sha256']
 sources[name]={'path':str(path),'sha256':digest}
import comsol_mcp
assert all(not name.startswith('comsol_mcp._desktop_') for name in sys.modules)
comsol_mcp.__path__=[str(overlay)]+[str(p) for p in comsol_mcp.__path__ if str(p)!=str(overlay)]
from comsol_mcp._desktop_macos import MacOSAccessibilityObserver
import comsol_mcp._desktop_platforms as platforms_source,comsol_mcp._desktop_macos as macos_source,comsol_mcp._desktop_service as service_source,comsol_mcp._desktop_selector_profile as selector_source
before=bool(jpype.isJVMStarted());assert before is False
status=MacOSAccessibilityObserver().status()
after=bool(jpype.isJVMStarted());assert after is False
for module in (platforms_source,macos_source,service_source,selector_source):
 p=pathlib.Path(module.__file__);sources[module.__name__]={'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
assert sources=={module.__name__:{'path':str(pathlib.Path(module.__file__).resolve()),'sha256':hashlib.sha256(pathlib.Path(module.__file__).read_bytes()).hexdigest()} for module in (platforms_source,macos_source,service_source,selector_source)}
print(json.dumps({'caller_pid':os.getpid(),'executable':sys.executable,'installed_version':metadata.version('comsol-mcp'),'status':status,'source_hashes':sources,'jpype_started_before_after':[before,after],'prompt_requested':False,'window_tree_pixels_enumerated':False,'GUI_certification':'NOT_RUN'}))"""


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _prepare_current_desktop_overlay(root: Path, app_wheel: Mapping[str, Any]) -> dict[str, Any]:
    """Copy only the four reviewed Desktop modules beside the future test venv."""
    root = Path(root)
    package_dir = root / "current-desktop-overlay" / "comsol_mcp"
    package_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
    helper = _helpers()
    sources: dict[str, Any] = {}
    copies: dict[str, Any] = {}
    for module, filename in CURRENT_DESKTOP_MODULES.items():
        source_path = REPOSITORY / "comsol_mcp" / filename
        raw, source_record = helper.raw_file(source_path)
        if source_record["sha256"] != CALLER_SOURCE_HASHES[module]:
            raise RuntimeError("current desktop source changed after R9 source qualification: " + module)
        destination = package_dir / filename
        helper.copy_blob(destination, raw)
        copy_record = helper.raw_file(destination)[1]
        if copy_record["sha256"] != source_record["sha256"] or copy_record["bytes"] != source_record["bytes"]:
            raise RuntimeError("current desktop overlay copy differs from its exact source: " + module)
        sources[module] = source_record
        copies[module] = copy_record
    return helper.publish(root / "CURRENT_DESKTOP_OVERLAY.json", {
        "schema": DESKTOP_OVERLAY_SCHEMA,
        "status": "READY_CURRENT_SOURCE_OVERLAY_NOT_PRODUCT_WHEEL_OR_EXECUTION",
        "source_profile": "current175-r9",
        "app_wheel": dict(app_wheel),
        "source_root": str(REPOSITORY / "comsol_mcp"),
        "package_path": str(package_dir),
        "module_filenames": CURRENT_DESKTOP_MODULES,
        "source_records": sources,
        "overlay_records": copies,
        "package_wheel_unchanged": True,
    })


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _exception_text(exc: BaseException) -> str:
    try:
        return str(exc)[:512]
    except BaseException:
        return "diagnostic text unavailable"


def _record(path: Path) -> dict[str, Any]:
    return _helpers().raw_file(Path(path))[1]


def _read_record(record: Mapping[str, Any], *, maximum: int = 64 * 1024 * 1024) -> bytes:
    return _helpers().raw_file(Path(str(record["path"])), expected=record, maximum=maximum)[0]


def _read_json(path: Path, *, expected: Mapping[str, Any] | None = None) -> dict[str, Any]:
    raw, _record_row = _helpers().raw_file(Path(path), expected=expected)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def _selected_inventory(w26_inventory: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Close the W26 lifecycle capture list to one install and three reads."""
    by_label: dict[str, list[Mapping[str, Any]]] = {}
    for row in w26_inventory:
        by_label.setdefault(str(row.get("label")), []).append(row)
    if set(CAPTURE_LABELS) - set(by_label) or any(len(by_label[name]) != 1 for name in CAPTURE_LABELS):
        raise RuntimeError("W26 current r9 offline install/readback capture rows are missing or ambiguous")
    result = []
    for label in CAPTURE_LABELS:
        row = copy.deepcopy(dict(by_label[label][0]))
        row["max_calls"] = 1
        if label == "current-caller-desktop-metadata-status":
            argv = list(row["request_argv"])
            if argv[-2] != "-c":
                raise RuntimeError("W26 product caller capture is not a closed -c invocation")
            argv[-1] = CALLER_STATUS_CODE
            row["request_argv"] = argv
        result.append(row)
    install = result[0]
    if install.get("offline_listener_object") is not True or install.get("cap") != 600:
        raise RuntimeError("current r9 install must use its exact original offline listener row")
    if sum(row["max_calls"] for row in result) != len(CAPTURE_LABELS):
        raise RuntimeError("current r9 install capture budget differs")
    return result


def _expected_plan(w26_candidate: Mapping[str, Any], w26_plan: Mapping[str, Any]) -> dict[str, Any]:
    scope = runtime.activity_scope({"activity_kind": runtime.APP_LIFECYCLE})
    root = Path(w26_candidate["prepared"]["path"]).parent
    return {
        "schema": "w25-current-r9-offline-install-plan/1",
        **scope,
        "inventory": _selected_inventory(w26_plan["inventory"]),
        "capture_max_calls": len(CAPTURE_LABELS),
        "wheel_count": 42,
        "venv_path": str(root / "current-venv"),
        "effects": ["fresh current-venv", "current-install-evidence", "bounded original stage logs and receipts"],
        "allowed_write_paths": [str(root / name) for name in ("logs", "tmp", "evidence", "outside", "empty-home", "import-home", "current-venv", "current-install-evidence")],
        "outer_sandbox_profile_sha256": _sha(_sandbox_profile(root)),
        "network_isolation": "W26_LIFECYCLE_SANDBOX plus same-PID EPERM/EACCES probe and offline_install owned-listener capture",
        "forbidden": ["upgrade", "rollback", "uninstall", "data transition", "COMSOL", "JVM", "GUI", "AX tree", "pixels", "model", "endpoint", "selector profile"],
        "product_status_code_sha256": _sha(CALLER_STATUS_CODE.encode()),
        "current_desktop_overlay_record": _record(Path(root) / "CURRENT_DESKTOP_OVERLAY.json"),
        "current_desktop_overlay_mode": "four exact current175-r9 Desktop source copies over the byte-identical App wheel in the test venv",
    }


def _r9_reader_call(function, *args, **kwargs):
    """Route lifecycle input checks and plan construction through the versioned r9 reader."""
    old_reader = sealed_data.current_application_profile
    sealed_data.current_application_profile = lambda profile: sealed_data_r9.current_application_profile(profile, sealed_data_r9.BASE)
    try:
        return function(*args, **kwargs)
    finally:
        sealed_data.current_application_profile = old_reader


def _r9_reader_context():
    """Bind the exact versioned R9 reader, helper argv, and inventory for captures."""
    return lifecycle_r9.r9_execution_context()


def _application_binding(prepared: Mapping[str, Any]) -> dict[str, Any]:
    profile = prepared.get("current_application_profile")
    if not isinstance(profile, dict) or profile.get("source_profile") != "current175-r9":
        raise RuntimeError("R9 lifecycle prepared input is not bound to current175-r9")
    verified = _r9_reader_call(sealed_data_r9.current_application_profile, profile, sealed_data_r9.BASE)
    if verified["result"] != prepared.get("app_result") or verified["wheel"] != prepared.get("app_wheel"):
        raise RuntimeError("R9 lifecycle producer result/wheel differs from exact App profile")
    producer = _r9_reader_call(sealed_data_r9._producer_records, Path(profile["root"]), sealed_data_r9.BASE)
    source = json.loads(_read_record(profile["candidate"]))
    candidate_prepared = json.loads(_read_record(source["prepared"]))
    source_binding = candidate_prepared.get("source", {})
    source_lock = source_binding.get("source_lock")
    if source_binding.get("source_profile") != "current175-r9" or source_lock != _record(APP_LOCK):
        raise RuntimeError("R9 App producer does not bind the exact current175-r9 source lock")
    wheelhouse = prepared["wheelhouses"]["new"]
    return {
        "root": profile["root"], "source_profile": "current175-r9", "source_member_count": 175,
        "root_identity": producer["root_identity"],
        "candidate_filename": Path(profile["candidate"]["path"]).name,
        "candidate": profile["candidate"], "result": profile["result"],
        "review": producer["review"][1], "once": profile["once"],
        "r9_lock": _record(APP_LOCK), "source_lock": source_lock,
        "wheel": prepared["app_wheel"], "requirements_new": prepared["requirements_new"],
        "wheelhouse_count": len(wheelhouse), "wheelhouse_sha256": _sha(_json_bytes(wheelhouse)),
    }


def prepare(root: Path, *, current_application_root: Path, tag: str = "r9install") -> dict[str, Any]:
    """Create a fresh input/candidate root; no install or native Popen occurs."""
    root = Path(root)
    if root.parent != Path("/private/tmp") or root != root.resolve() or not re.fullmatch(r"cvw26-[a-z0-9-]{1,48}", root.name):
        raise RuntimeError("fresh current r9 install root must be canonical /private/tmp/cvw26-<safe suffix>")
    if root.exists() or root.is_symlink():
        raise RuntimeError("current r9 install candidate root already exists; no reuse")
    if not re.fullmatch(r"[a-z0-9-]{1,48}", tag):
        raise RuntimeError("current r9 install tag is invalid")
    current_application_root = Path(current_application_root)
    if not BASE_INPUT_MANIFEST.is_file() or not DEPENDENCY_ROOT.is_dir() or not current_application_root.is_dir():
        raise RuntimeError("fixed lifecycle inputs or actual current R9 App bundle producer root are unavailable")
    prepare_inputs(root, reuse_manifest=BASE_INPUT_MANIFEST)
    helper = _helpers()
    try:
        runtime_source, _runtime_source_record = helper.raw_file(DEPENDENCY_ROOT / "CURRENT_RUNTIME_INPUT.json")
        helper.copy_blob(root / "CURRENT_RUNTIME_INPUT.json", runtime_source)
        nested_record = lifecycle_r9.prepare(root, tag=tag, current_application_root=current_application_root)
    except BaseException as exc:
        failure_path = root / "PREPARATION_FAILURE.json"
        if not failure_path.exists():
            try:
                helper.publish(failure_path, {"schema": "w25-r9-install-candidate-prepare-failure/1",
                    "status": "PRESERVED_PREPARATION_FAILURE_NO_NATIVE_POPEN",
                    "error_type": type(exc).__name__, "error": str(exc)[:512],
                    "native_Popen": 0, "GUI": "NOT_RUN", "once": "NOT_CREATED"})
            except BaseException:
                pass
        raise
    nested_candidate_path = Path(nested_record["path"])
    helper.raw_file(nested_candidate_path, expected=nested_record)
    nested_candidate = _read_json(nested_candidate_path, expected=nested_record)
    nested_plan_record = nested_candidate["plan"]
    nested_plan = _read_json(Path(nested_plan_record["path"]), expected=nested_plan_record)
    if nested_candidate.get("status") != "READY_APPLICATION_LIFECYCLE_MAIN_REVIEW_NOT_NATIVE_APPROVED":
        raise RuntimeError("closed W26 lifecycle source candidate is not READY")
    prepared_record = nested_candidate["prepared"]
    prepared = _read_json(Path(prepared_record["path"]), expected=prepared_record)
    wheelhouse = prepared["wheelhouses"]["new"]
    if len(wheelhouse) != 42:
        raise RuntimeError("new r9 source-external wheelhouse must contain exactly 42 pinned wheels")
    application = _application_binding(prepared)
    desktop_overlay_record = _prepare_current_desktop_overlay(root, prepared["app_wheel"])
    desktop_overlay = _read_json(Path(desktop_overlay_record["path"]), expected=desktop_overlay_record)
    application["current_desktop_overlay"] = desktop_overlay_record
    if application["root"] != str(current_application_root):
        raise RuntimeError("exact current App root differs from the R9 lifecycle profile")
    plan_value = _expected_plan(nested_candidate, nested_plan)
    if plan_value["venv_path"] != str(root / "current-venv"):
        raise RuntimeError("fixed current r9 venv target differs from fresh root")
    plan_record = helper.publish(root / "W25_R9_INSTALL_PLAN.json", plan_value)
    source = json.loads(_read_record(nested_candidate["source"]).decode("utf-8"))
    own_source = helper.raw_file(Path(__file__).resolve())[1]
    source[own_source["path"]] = own_source
    source[desktop_overlay_record["path"]] = desktop_overlay_record
    for rows in (desktop_overlay["source_records"], desktop_overlay["overlay_records"]):
        for row in rows.values():
            source[row["path"]] = row
    source_record = helper.publish(root / "W25_R9_INSTALL_SOURCE.json", source)
    candidate_value = {
        "schema": "w25-current-r9-offline-install-candidate/1",
        "status": "READY_W25_CURRENT175_R9_OFFLINE_INSTALL_MAIN_SOURCE_REVIEW_NOT_EXECUTION_APPROVED",
        "root": str(root), "root_identity": nested_candidate["root_identity"],
        "prepared": prepared_record,
        "w26_lifecycle_candidate": nested_record,
        "w26_lifecycle_plan": nested_plan_record,
        "plan": plan_record, "source": source_record,
        "resume_sha256": helper.raw_file(RESUME)[1]["sha256"],
        "application": application,
        "current_desktop_overlay": desktop_overlay_record,
        "native_Popen": 0,
    }
    return helper.publish(root / "W25_R9_INSTALL_CANDIDATE.json", candidate_value)


def _verify_candidate(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    root = Path(root)
    helper = _helpers()
    candidate_path = root / "W25_R9_INSTALL_CANDIDATE.json"
    candidate_record = helper.raw_file(candidate_path)[1]
    candidate = _read_json(candidate_path, expected=candidate_record)
    if candidate.get("schema") != "w25-current-r9-offline-install-candidate/1" or \
            candidate.get("status") != "READY_W25_CURRENT175_R9_OFFLINE_INSTALL_MAIN_SOURCE_REVIEW_NOT_EXECUTION_APPROVED" or \
            candidate.get("root") != str(root) or candidate.get("native_Popen") != 0:
        raise RuntimeError("fresh r9 offline-install candidate schema/state differs")
    helper.secure_dir(root, candidate["root_identity"])
    if helper.raw_file(RESUME)[1]["sha256"] != candidate["resume_sha256"]:
        raise RuntimeError("RESUME changed after candidate preparation; prepare a fresh install candidate")
    for row in (candidate["prepared"], candidate["w26_lifecycle_candidate"], candidate["w26_lifecycle_plan"], candidate["plan"], candidate["source"]):
        helper.raw_file(Path(row["path"]), expected=row)
    source = _read_json(Path(candidate["source"]["path"]), expected=candidate["source"])
    for row in source.values():
        helper.raw_file(Path(row["path"]), expected=row)
    overlay_record = helper.raw_file(root / "CURRENT_DESKTOP_OVERLAY.json")[1]
    if candidate.get("current_desktop_overlay") != overlay_record or             candidate.get("application", {}).get("current_desktop_overlay") != overlay_record:
        raise RuntimeError("exact current desktop overlay manifest binding differs")
    overlay = _read_json(root / "CURRENT_DESKTOP_OVERLAY.json", expected=overlay_record)
    if overlay.get("schema") != DESKTOP_OVERLAY_SCHEMA or \
            overlay.get("status") != "READY_CURRENT_SOURCE_OVERLAY_NOT_PRODUCT_WHEEL_OR_EXECUTION" or \
            overlay.get("module_filenames") != CURRENT_DESKTOP_MODULES or \
            set(overlay.get("source_records", {})) != set(CURRENT_DESKTOP_MODULES) or \
            set(overlay.get("overlay_records", {})) != set(CURRENT_DESKTOP_MODULES) or \
            overlay.get("package_path") != str(root / "current-desktop-overlay" / "comsol_mcp") or \
            overlay.get("app_wheel", {}).get("sha256") != candidate["application"]["wheel"]["sha256"]:
        raise RuntimeError("strict four-module desktop overlay manifest differs")
    if source.get(overlay_record["path"]) != overlay_record:
        raise RuntimeError("desktop overlay manifest is absent from the install source closure")
    for module, filename in CURRENT_DESKTOP_MODULES.items():
        source_row = overlay["source_records"][module]
        copy_row = overlay["overlay_records"][module]
        helper.raw_file(Path(source_row["path"]), expected=source_row)
        helper.raw_file(Path(copy_row["path"]), expected=copy_row)
        if source_row["sha256"] != CALLER_SOURCE_HASHES[module] or \
                Path(source_row["path"]) != REPOSITORY / "comsol_mcp" / filename or \
                copy_row["sha256"] != source_row["sha256"] or \
                Path(copy_row["path"]) != root / "current-desktop-overlay" / "comsol_mcp" / filename or \
                source.get(source_row["path"]) != source_row or source.get(copy_row["path"]) != copy_row:
            raise RuntimeError("exact current desktop source overlay file differs: " + module)
    own = helper.raw_file(Path(__file__).resolve())[1]
    if source.get(own["path"]) != own:
        raise RuntimeError("install actor source is not pinned in its own candidate closure")
    nested_candidate = _read_json(Path(candidate["w26_lifecycle_candidate"]["path"]), expected=candidate["w26_lifecycle_candidate"])
    nested_plan = _read_json(Path(candidate["w26_lifecycle_plan"]["path"]), expected=candidate["w26_lifecycle_plan"])
    prepared = _read_json(Path(candidate["prepared"]["path"]), expected=candidate["prepared"])
    if nested_candidate.get("root_identity") != candidate["root_identity"] or nested_candidate.get("prepared") != candidate["prepared"] or \
            nested_candidate.get("plan") != candidate["w26_lifecycle_plan"]:
        raise RuntimeError("W26 lifecycle child candidate linkage differs")
    runtime.verify_actor(nested_candidate)
    _r9_reader_call(lifecycle.verify_inputs, root, prepared)
    expected_plan = _expected_plan(nested_candidate, nested_plan)
    plan = _read_json(Path(candidate["plan"]["path"]), expected=candidate["plan"])
    if plan != expected_plan:
        raise RuntimeError("W25 install-only plan differs from exact W26 offline install/readback captures")
    wheelhouse = prepared["wheelhouses"]["new"]
    expected_app = _application_binding(prepared)
    expected_app["current_desktop_overlay"] = overlay_record
    if candidate["application"] != expected_app or len(wheelhouse) != 42:
        raise RuntimeError("r9 application/wheelhouse closure differs from prepared exact input")
    effect_paths = ["current-venv", "current-install-evidence", "logs", "tmp", "evidence", "outside", "empty-home", "import-home"]
    for name in effect_paths:
        path = root / name
        if path.exists() or path.is_symlink():
            raise RuntimeError("fresh r9 install effect target is occupied; no reuse")
    if (root / "UNIT_ONCE_GUARD.json").exists() or (root / "UNIT_ONCE_GUARD.json").is_symlink():
        raise RuntimeError("r9 install once already consumed; no replay")
    return candidate, candidate_record, plan, nested_candidate


def build_expected_binding(root: Path) -> dict[str, Any]:
    """Show Main the exact closed one-use payload for independent signing."""
    candidate, candidate_record, plan, nested_candidate = _verify_candidate(root)
    review_record = _record(Path(root) / "MAIN_SOURCE_REVIEW.json")
    app = candidate["application"]
    return {
        "status": "MAIN_APPROVED_ONCE_W25_CURRENT175_R9_OFFLINE_INSTALL",
        "activity_kind": "W25_CURRENT175_R9_OFFLINE_INSTALL",
        "candidate_sha256": candidate_record["sha256"], "root": candidate["root"],
        "root_identity": candidate["root_identity"], "plan_sha256": candidate["plan"]["sha256"],
        "source_sha256": candidate["source"]["sha256"], "resume_sha256": candidate["resume_sha256"],
        "review_sha256": review_record["sha256"],
        "w26_lifecycle_candidate_sha256": nested_candidate.get("candidate_sha256", candidate["w26_lifecycle_candidate"]["sha256"]),
        "app_root": app["root"], "app_candidate_filename": app["candidate_filename"],
        "app_candidate_sha256": app["candidate"]["sha256"], "app_result_sha256": app["result"]["sha256"],
        "app_review_sha256": app["review"]["sha256"], "app_once_sha256": app["once"]["sha256"],
        "app_source_profile": app["source_profile"], "app_source_member_count": app["source_member_count"],
        "r9_lock_sha256": app["r9_lock"]["sha256"],
        "app_wheel_sha256": app["wheel"]["sha256"], "requirements_sha256": app["requirements_new"]["sha256"],
        "wheelhouse_count": app["wheelhouse_count"], "wheelhouse_sha256": app["wheelhouse_sha256"],
        "venv_path": plan["venv_path"], "capture_max_calls": len(CAPTURE_LABELS),
        "offline_listener_object": True, "w26_network_deny_probe_required": True,
        "package_distribution_status_readback": True, "nonprompt_current_product_caller_status": True,
        "retain_install_venv": True, "upgrade": False, "rollback": False, "uninstall": False,
        "data_transition": False, "COMSOL": False, "JVM": False, "GUI": False,
        "AX_tree": False, "pixels": False, "model_binding": False, "endpoint": False,
        "budget_seconds": plan["budget_seconds"], "cleanup_reserve_seconds": plan["cleanup_reserve_seconds"],
    }


def _verify_authority(root: Path, binding_path: Path, binding_sha: str):
    candidate, candidate_record, plan, nested_candidate = _verify_candidate(root)
    review_path = Path(root) / "MAIN_SOURCE_REVIEW.json"
    review_record = _record(review_path)
    review = _read_json(review_path, expected=review_record)
    if review.get("schema") != "w25-current-r9-offline-install-source-review/1" or \
            review.get("status") != "PASS_W25_CURRENT175_R9_OFFLINE_INSTALL_SOURCE_FOR_EXACT_CANDIDATE" or \
            review.get("candidate_sha256") != candidate_record["sha256"] or \
            review.get("plan_sha256") != candidate["plan"]["sha256"] or \
            review.get("source_sha256") != candidate["source"]["sha256"]:
        raise RuntimeError("Main exact-candidate r9 offline-install source review is missing or differs")
    if Path(binding_path) != Path(root) / "MAIN_ONCE_BINDING.json":
        raise RuntimeError("exact W25 current r9 Main once path required")
    binding_record = _record(binding_path)
    if binding_record["sha256"] != binding_sha:
        raise RuntimeError("Main r9 offline-install once bytes differ")
    binding = _read_json(binding_path, expected=binding_record)
    required = build_expected_binding(root)
    required["review_sha256"] = review_record["sha256"]
    if set(binding) != set(required) | {"nonce"} or any(
            type(binding.get(key)) is not type(value) or binding.get(key) != value for key, value in required.items()) or \
            not isinstance(binding.get("nonce"), str) or len(binding["nonce"]) < 16:
        raise RuntimeError("Main once does not bind exact r9 wheelhouse, install-only scope, and caller readback")
    return candidate, candidate_record, binding, binding_record, plan, nested_candidate


def _sandbox_profile(root: Path) -> bytes:
    allowed = [root / name for name in ("logs", "tmp", "evidence", "outside", "empty-home", "import-home",
                                       "current-venv", "current-install-evidence")]
    # Match the already-qualified W26 lifecycle boundary: the outer actor
    # sandbox must permit launching the exact nested helper while retaining
    # explicit network and write denials plus this root's write allowlist.
    text = "(version 1)\n(allow default)\n(deny network*)\n(deny file-write*)\n"
    text += "".join("(allow file-write* (subpath " + json.dumps(str(path)) + "))\n" for path in allowed)
    text += '(allow file-write* (literal "/dev/null"))\n'
    return text.encode("utf-8")


def _execute_authorized(root: Path, binding_path: Path, binding_sha: str) -> Path:
    """Run one exact r9 install/readback after Main source review and once."""
    began = time.monotonic()
    root = Path(root)
    candidate, candidate_record, binding, binding_record, plan, nested_candidate = _verify_authority(root, binding_path, binding_sha)
    helper = _helpers()
    prepared = _read_json(Path(candidate["prepared"]["path"]), expected=candidate["prepared"])
    nested_plan = _read_json(Path(candidate["w26_lifecycle_plan"]["path"]), expected=candidate["w26_lifecycle_plan"])
    current_mask = os.umask(0o077)
    os.umask(current_mask)
    if current_mask != 0o077:
        raise RuntimeError("fresh W25 r9 install actor must start under private 0077 umask")
    owner_plan = copy.deepcopy(nested_plan)
    owner_plan["inventory"] = copy.deepcopy(plan["inventory"])
    owner = runtime.RuntimeOwner(root, owner_plan, token=runtime._OWNER_TOKEN, began=began)
    runtime._RETAINED[str(root)] = owner
    old_subprocess = runtime.original.subprocess
    launch = None
    logsfd = None
    outputs: dict[str, Any] = {}
    started_records: list[dict[str, Any]] = []
    current = package = distributions = desktop = None
    failure: BaseException | None = None
    # The probe itself is the only effect authorization in the original offline
    # stage; it remains distinct from the release helper's owned install listener.
    helper.publish(root / "UNIT_ONCE_GUARD.json", {
        "schema": "w25-current-r9-offline-install-once/1", "binding_first_record": binding_record,
        "candidate_first_record": candidate_record, "nonce_sha256": _sha(binding["nonce"].encode()),
        "pid": os.getpid(), "consumed_utc": datetime.now(timezone.utc).isoformat(),
        "activity_kind": binding["activity_kind"], "native_Popen_before_guard": 0})
    try:
        for name in ("logs", "tmp", "evidence", "outside", "empty-home", "import-home"):
            (root / name).mkdir(mode=0o700, exist_ok=False)
        profile = _sandbox_profile(root)
        if _sha(profile) != plan["outer_sandbox_profile_sha256"]:
            raise RuntimeError("W25 install outer network-deny profile differs from reviewed plan")
        helper.copy_blob(root / "LIFECYCLE_SANDBOX.sb", profile)
        logsfd = os.open(root / "logs", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        runtime.original.subprocess = SimpleNamespace(Popen=owner.popen, run=owner.diagnostic_run,
                                                       TimeoutExpired=subprocess.TimeoutExpired)
    except BaseException as exc:
        runtime.original.fallback("W25 current r9 install prebirth setup failed: " + type(exc).__name__)
        try:
            helper.publish(root / "FIRST_FAILURE.json", {
                "schema": "w25-current-r9-offline-install-failure/1", "status": "PRESERVED_INSTALL_FAILURE_NOT_REPLAYABLE",
                "candidate_sha256": candidate_record["sha256"], "binding_sha256": binding_sha,
                "error_type": type(exc).__name__, "error": _exception_text(exc), "native_Popen": 0,
                "once_consumed": True, "rollback": "NOT_RUN", "uninstall": "NOT_RUN", "COMSOL_JVM_GUI": "NOT_RUN"})
        except BaseException as secondary:
            runtime.original.fallback("W25 install prebirth failure evidence write failed: " + type(secondary).__name__)
        if logsfd is not None:
            try: os.close(logsfd)
            except BaseException: pass
        runtime.original.subprocess = old_subprocess
        raise
    try:
        with socket.socket() as held_listener:
            held_listener.bind(("127.0.0.1", 0))
            held_listener.listen(1)
            held_listener.settimeout(0.25)
            port = held_listener.getsockname()[1]
            launch = lifecycle.LifecycleLaunch(root, owner, nested_candidate, binding, owner_plan, prepared, logsfd, port)
            with release._controlled_launch_scope(launch):
                current = release.offline_install(SimpleNamespace(
                    python=Path(prepared["python"]["path"]),
                    requirements=root / "requirements-new.lock", wheelhouse=root / "new-wheelhouse",
                    venv=root / "current-venv", evidence=root / "current-install-evidence"),
                    Path(release.__file__))
                if current.get("status") != "PASS_PROCESS_ISOLATED_INSTALL_AND_IMPORT" or \
                        current.get("doctor", {}).get("identity", {}).get("version") != APP_VERSION or \
                        current.get("network_probe", {}).get("status") != "PASS" or current.get("listener_accepted") is not False:
                    raise RuntimeError("actual W26 source-external network-denied r9 install/doctor failed")
                py = root / "current-venv/bin/python"
                package = release.package_fingerprint(py)
                expected_files = [{"path": row["path"].removeprefix("comsol_mcp/"), "sha256": row["sha256"]}
                                  for row in prepared["new_input"]["package_members"]]
                if package["files"] != expected_files:
                    raise RuntimeError("actual r9 installed package members/hashes differ from the source wheel")
                distributions = release.distribution_fingerprint(py)
                required_versions = {name: row["version"] for name, row in prepared["new_input"]["applicable_pins"].items()}
                actual_versions = {row["name"]: row["version"] for row in distributions["distributions"]}
                if actual_versions != {**required_versions, "pip": "25.0.1"}:
                    raise RuntimeError("actual current r9 installed distribution versions differ from the exact 42-wheel lock")
                status_capture = launch.capture([py.as_posix(), "-I", "-B", "-c", CALLER_STATUS_CODE],
                                                cwd=str(root / "current-venv"), capture_output=True, text=True)
                if status_capture.returncode != 0:
                    raise RuntimeError("actual current r9 product caller nonprompt status capture failed")
                desktop = json.loads(status_capture.stdout)
                module_files = {
                    "comsol_mcp._desktop_macos": "_desktop_macos.py",
                    "comsol_mcp._desktop_platforms": "_desktop_platforms.py",
                    "comsol_mcp._desktop_service": "_desktop_service.py",
                    "comsol_mcp._desktop_selector_profile": "_desktop_selector_profile.py",
                }
                overlay_package = root / "current-desktop-overlay" / "comsol_mcp"
                expected_source_records = {
                    module: {"path": str(overlay_package / module_files[module]), "sha256": expected}
                    for module, expected in CALLER_SOURCE_HASHES.items()
                }
                if desktop.get("installed_version") != APP_VERSION or desktop.get("source_hashes") != expected_source_records or \
                        desktop.get("jpype_started_before_after") != [False, False] or desktop.get("prompt_requested") is not False or \
                        desktop.get("window_tree_pixels_enumerated") is not False or desktop.get("GUI_certification") != "NOT_RUN":
                    raise RuntimeError("actual caller hash/permission/JVM/UI nonprompt boundary differs")
                status = desktop.get("status", {})
                if status.get("permission_status") not in {"AVAILABLE", "BLOCKED_PERMISSION", "UNKNOWN"} or \
                        status.get("metadata_status") not in {"AVAILABLE", "UNAVAILABLE"} or \
                        status.get("selector_profile") not in {"UNOBSERVED", "UNAVAILABLE"} or status.get("model_binding") != "UNVERIFIED" or \
                        status.get("control_status") != "UNSUPPORTED_CONTROL" or status.get("native_evidence") != "NOT_RUN":
                    raise RuntimeError("actual current product caller status schema/unsupported-control boundary differs")
            try:
                unexpected, _address = held_listener.accept()
                unexpected.close()
                raise RuntimeError("sandboxed lifecycle stage unexpectedly reached the held parent listener")
            except socket.timeout:
                pass
        for index, row in enumerate(CAPTURE_LABELS):
            original = launch.completed[index]
            terminal = helper.raw_file(root / "logs" / (original["stdout"].removesuffix(".stdout") + ".result.json"))[0]
            stage = json.loads(terminal)
            if stage.get("status") != "PASS" or stage.get("owned_process_terminal") is not True or \
                    stage.get("owned_group_absent") is not True or stage.get("birth", {}).get("status") != "OBSERVED":
                raise RuntimeError(f"original W26 capture did not terminally verify: {row}")
            started_records.append({"label": row, "original_capture": original, "result_sha256": _sha(terminal)})
        probes = []
        for index, label in enumerate(CAPTURE_LABELS):
            probe_path = root / "evidence" / ("PROBE_" + label + "-" + str(index) + ".json")
            probe = _read_json(probe_path)
            if probe.get("status") != "PASS_ACTUAL_EPERM":
                raise RuntimeError("W26 same-PID OS denial proof was not numeric EPERM")
            probes.append(_record(probe_path))
        network_report = _read_json(root / "current-install-evidence/offline-install-result.json")
        process_report = _read_json(root / "current-install-evidence/offline-install-process.log.json")
        result_value = {
            "schema": "w25-current-r9-offline-install-result/1",
            "status": "PASS_W25_CURRENT175_R9_OFFLINE_INSTALL_ACTUAL_CALLER_SCOPED_NOT_RELEASE",
            "candidate_sha256": candidate_record["sha256"], "binding_sha256": binding_sha,
            "w26_lifecycle_candidate_sha256": candidate["w26_lifecycle_candidate"]["sha256"],
            "source_profile": "current175-r9", "app_wheel": candidate["application"]["wheel"],
            "requirements": candidate["application"]["requirements_new"],
            "wheelhouse_count": 42, "wheelhouse_sha256": candidate["application"]["wheelhouse_sha256"],
            "venv": str(root / "current-venv"), "venv_retained_for_fresh_W25_GUI_candidate": True,
            "offline_install": current, "network_denial_process_report": process_report,
            "network_denial_result": network_report, "package": package, "distributions": distributions,
            "current_product_caller_status": desktop, "original_captures": started_records,
            "same_pid_network_probe_first_records": probes, "owner_samples": owner.samples,
            "install_orchestration": "existing W26 release.offline_install via LifecycleLaunch.capture and original StageUnknown owner",
            "rollback": "NOT_RUN", "uninstall": "NOT_RUN", "data_transition": "NOT_RUN",
            "COMSOL": "NOT_RUN", "JVM": "NOT_RUN", "GUI": "NOT_RUN", "AX_tree": "NOT_RUN",
            "pixels": "NOT_RUN", "model_binding": "UNVERIFIED", "endpoint": "NOT_OBSERVED",
            "selector_profile": status.get("selector_profile"), "independent_final_review": "NOT_RUN",
        }
        helper.publish(root / "CURRENT_R9_OFFLINE_INSTALL_RESULT.json", result_value)
        return root / "CURRENT_R9_OFFLINE_INSTALL_RESULT.json"
    except BaseException as exc:
        failure = exc
        if launch is not None and launch.pending_unknown is not None:
            failure = launch.pending_unknown
        runtime.retain_first_failure(owner, logsfd, failure, launch.completed if launch else [], stem="R9_INSTALL")
        try:
            helper.publish(root / "FIRST_FAILURE.json", {
                "schema": "w25-current-r9-offline-install-failure/1", "status": "PRESERVED_INSTALL_FAILURE_NOT_REPLAYABLE",
                "candidate_sha256": candidate_record["sha256"], "binding_sha256": binding_sha,
                "error_type": type(failure).__name__, "error": runtime.original.failure_detail("install", failure),
                "completed_captures": launch.completed if launch else [], "venv_exists": (root / "current-venv").exists(),
                "rollback": "NOT_RUN", "uninstall": "NOT_RUN", "COMSOL_JVM_GUI": "NOT_RUN"})
        except BaseException as secondary:
            runtime.original.fallback("W25 install failure evidence write failed: " + type(secondary).__name__)
        raise
    finally:
        runtime.original.subprocess = old_subprocess
        if logsfd is not None:
            try:
                os.close(logsfd)
            except BaseException:
                pass


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--root", type=Path, required=True)
    prepare_parser.add_argument("--tag", default="r9install")
    prepare_parser.add_argument("--current-application-root", type=Path, required=True)
    execute_parser = sub.add_parser("execute")
    execute_parser.add_argument("--root", type=Path, required=True)
    execute_parser.add_argument("--binding", type=Path, required=True)
    execute_parser.add_argument("--binding-sha", required=True)
    args = parser.parse_args(argv)
    if args.action == "prepare":
        print(json.dumps(prepare(args.root, current_application_root=args.current_application_root,
                                 tag=args.tag), sort_keys=True))
        return 0
    os.umask(0o077)
    print(json.dumps({"result": str(execute(args.root, args.binding, args.binding_sha))}, sort_keys=True))
    return 0


def execute(root: Path, binding_path: Path, binding_sha: str) -> Path:
    """Run one exactly reviewed four-capture r9 install/readback under the versioned reader."""
    with _r9_reader_context():
        return _execute_authorized(root, binding_path, binding_sha)


if __name__ == "__main__":
    raise SystemExit(main())
