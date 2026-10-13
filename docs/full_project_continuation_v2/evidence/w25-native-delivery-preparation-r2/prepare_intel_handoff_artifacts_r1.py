#!/usr/bin/env python3
"""Create append-only Intel archive and W25 observation handoff records.

This script is metadata-only. It does not access the network, install packages,
start COMSOL, or execute tests. Output creation is exclusive; existing records
are never replaced.
"""

from __future__ import annotations

import hashlib
import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


BASE = Path(__file__).resolve().parent
CHECKOUT = BASE.parents[3]
WORKSPACE = CHECKOUT.parent
FREEZE = BASE / "INTEL_X86_BUILDER_INPUT_FREEZE_R3.json"
INPUTS = BASE / "INTEL_X86_BUILDER_INPUT_MANIFEST_R3.json"
RUST = BASE / "RUST_HOST_TARGET_COMPONENT_AUDIT_R2.json"
PYTHON = BASE / "PYTHON_AND_BUILD_INPUT_METADATA_R2.json"
CARGO = BASE / "CARGO_REGISTRY_API_AUDIT_R2.json"
HEADER_FINDING = BASE / "ROOT_INTEL_HEADER_PLAN_FINDING_R2.json"
ROOT_REVIEW = BASE / "ROOT_RUNTIME_AND_COMBINED_REVIEW_R2.json"
CLOSURE = BASE / "COMBINED_R5_WFP_R4_CLOSURE_R1.json"
CANDIDATE_PLAN = CHECKOUT / "docs/full_project_continuation_v2/evidence/release_data_lifecycle/desktop-builder-candidate-r4-w25/candidate-plan-r4-w25.json"
COMBINED = BASE / "combined-r5-wfp-r4-r1"


EXPECTED = {
    "INTEL_X86_BUILDER_INPUT_FREEZE_R3.json": "b0b8ac8e0e955c0da3fb2bb8ce480b1f4f5ff80ebccaff43f917c37b7738a5c9",
    "INTEL_X86_BUILDER_INPUT_MANIFEST_R3.json": "2e9aa01d3e56e1ca2630944aec6db45646ee5c0447e39dafdf630c694fd2d58c",
    "RUST_HOST_TARGET_COMPONENT_AUDIT_R2.json": "f92e971ef3c4b08bb4aa79d8c22e9dd46bd9a9e32b8796acb6ecf3c3f2027f79",
    "PYTHON_AND_BUILD_INPUT_METADATA_R2.json": "14116a58d52ac340258cf3cfefe56d3ca6e321ee051b3127b5131ffed3e71042",
    "CARGO_REGISTRY_API_AUDIT_R2.json": "65664ed08d30c6ee829148b1a30964f09d177050b9165ed5d8933e69a3b2dfdd",
    "ROOT_INTEL_HEADER_PLAN_FINDING_R2.json": "49e6ebfa85d926ee504371e31e5ef84c303340cca1956eea4e0924da724265a6",
    "ROOT_RUNTIME_AND_COMBINED_REVIEW_R2.json": "b80676dbf964c8026036dfaef6a61ab4aef561fed6f80d2d4977b006aa631998",
    "COMBINED_R5_WFP_R4_CLOSURE_R1.json": "",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def stamp(path: Path) -> dict:
    return {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_once(path: Path, value: dict | str) -> None:
    payload = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    data = payload.encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def verify_sources() -> dict[str, dict]:
    paths = [FREEZE, INPUTS, RUST, PYTHON, CARGO, HEADER_FINDING, ROOT_REVIEW, CLOSURE, CANDIDATE_PLAN]
    refs = {}
    for path in paths:
        actual = stamp(path)
        expected = EXPECTED.get(path.name)
        if expected and actual["sha256"] != expected:
            raise SystemExit(f"frozen source hash mismatch: {path.name}")
        refs[path.name] = actual
    return refs


def recursive_find(node, predicate):
    if isinstance(node, dict):
        if predicate(node):
            return node
        for value in node.values():
            result = recursive_find(value, predicate)
            if result is not None:
                return result
    elif isinstance(node, list):
        for value in node:
            result = recursive_find(value, predicate)
            if result is not None:
                return result
    return None


def archive_record(artifact_id, filename, url, size, digest, role, source, version=None, extra=None):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or "/" in filename or "\\" in filename:
        raise SystemExit(f"unsafe or invalid archive metadata: {artifact_id}")
    row = {
        "artifact_id": artifact_id,
        "filename": filename,
        "version": version,
        "url": url,
        "origin_host": parsed.hostname,
        "expected_bytes": int(size),
        "expected_sha256": digest,
        "role": role,
        "source_evidence": source,
        "download_status": "NOT_ACQUIRED",
    }
    if extra:
        row.update(extra)
    return row


def build_archive_manifest(source_refs: dict[str, dict]) -> dict:
    input_manifest = load(INPUTS)
    rust = load(RUST)
    py = load(PYTHON)
    candidate = load(CANDIDATE_PLAN)
    cargo = input_manifest["cargo_registry"]["crates"]
    if len(cargo) != 32 or not all(row.get("checksum_match") for row in cargo):
        raise SystemExit("the frozen Cargo lock closure is not 32 exact checksum matches")

    artifacts = []
    for component in input_manifest["rust_toolchain"]["components"]:
        artifacts.append(archive_record(
            f"rust-{component['component']}-{component['target']}",
            component["url"].rsplit("/", 1)[1],
            component["url"],
            component["head_content_length_bytes"],
            component["sha256"],
            "Rust 1.83.0 offline host toolchain/target std component",
            "RUST_HOST_TARGET_COMPONENT_AUDIT_R2.json",
            version="1.83.0",
            extra={"target": component["target"], "etag": component["etag"]},
        ))

    for crate in cargo:
        name, version = crate["name"], crate["version"]
        filename = f"{name}-{version}.crate"
        artifacts.append(archive_record(
            f"cargo-{name}-{version}", filename, crate["archive_url"],
            crate["archive_size_bytes"], crate["lock_checksum_sha256"],
            "exact Cargo.lock source archive", crate["api_response"]["path"],
            version=version,
            extra={"crate": name, "lock_checksum_matches_crates_io": True},
        ))

    openssl = input_manifest["openssl"]
    artifacts.append(archive_record(
        "openssl-source-4.0.2", openssl["name"], openssl["canonical_url"],
        openssl["bytes"], openssl["sha256"], "x86_64 static OpenSSL source build",
        openssl["raw_api_snapshot"]["path"], version="4.0.2",
        extra={"github_asset_id": openssl["asset_id"], "release_tag": openssl["tag"]},
    ))

    python_source = input_manifest["python_and_build"]["pinned_source_metadata"]
    artifacts.append(archive_record(
        "cpython-source-3.12.13", python_source["filename"], python_source["url"],
        python_source["head_content_length_bytes"], python_source["sha256"],
        "CPython universal2 generated headers and host build source",
        "PYTHON_AND_BUILD_INPUT_METADATA_R2.json", version="3.12.13",
        extra={"required_architectures": ["arm64", "x86_64"], "route_status": "UNIVERSAL2_CONFIGURATION_CANDIDATE_NOT_RUN"},
    ))

    closure = input_manifest["python_and_build"]["host_build_dependency_closure"]
    pypi_wheels = [
        ("maturin", closure["maturin"]["host_wheel"]),
        ("cffi", closure["cffi"]["host_wheel"]),
        ("setuptools", closure["setuptools"]["wheel"]),
        ("pycparser", closure["pycparser"]),
    ]
    for name, wheel in pypi_wheels:
        version = closure[name].get("version")
        if name == "maturin":
            version = "1.14.1"
        if name == "cffi":
            version = "2.1.1"
        if name == "setuptools":
            version = "80.9.0"
        artifacts.append(archive_record(
            f"python-build-wheel-{name}-{version}", wheel["filename"], wheel["url"],
            wheel.get("size", wheel.get("size_bytes")),
            wheel.get("digests", {}).get("sha256", wheel.get("sha256")),
            "pinned CPython 3.12.13 host-side build dependency",
            "PYTHON_AND_BUILD_INPUT_METADATA_R2.json", version=version,
            extra={"wheel_tag": wheel["filename"].removesuffix(".whl")},
        ))

    sdist = recursive_find(candidate, lambda row: row.get("filename") == "cryptography-50.0.1.tar.gz" and row.get("url"))
    if not sdist:
        raise SystemExit("pinned cryptography source distribution is absent from candidate plan")
    artifacts.append(archive_record(
        "cryptography-sdist-50.0.1", sdist["filename"], sdist["url"],
        sdist["size_bytes"], sdist["sha256"], "cryptography 50.0.1 source for the x86_64 ABI3 build",
        "desktop-builder-candidate-r4-w25/candidate-plan-r4-w25.json", version="50.0.1",
        extra={"metadata_url": sdist.get("metadata_url"), "metadata_source": sdist.get("metadata_source")},
    ))

    artifacts.sort(key=lambda row: row["artifact_id"])
    if len(artifacts) != 43 or len({row["artifact_id"] for row in artifacts}) != len(artifacts):
        raise SystemExit("unexpected artifact closure size or duplicate artifact ids")

    return {
        "schema": "comsol-mcp.w25-intel-x86-archive-acquisition-manifest/1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PREPARED_NOT_ROOT_VALIDATED_NO_ARCHIVE_FETCH",
        "purpose": "Exact source/build-input closure for a task-local Intel x86_64 cryptography builder on the arm64 host.",
        "target": {
            "host": "macOS arm64",
            "target": "x86_64-apple-darwin",
            "cpython_source": "3.12.13 universal2 candidate",
            "cryptography": "50.0.1 ABI3",
            "openssl": "4.0.2",
            "rust": "1.83.0",
        },
        "parent_freezes": {
            "input_freeze": source_refs[FREEZE.name],
            "input_manifest": source_refs[INPUTS.name],
            "corrected_cpython_header_plan": source_refs[HEADER_FINDING.name],
        },
        "metadata_sources": source_refs,
        "archive_count": len(artifacts),
        "archive_total_bytes": sum(row["expected_bytes"] for row in artifacts),
        "artifact_groups": {
            "rust_components": 4,
            "cargo_lock_crates": 32,
            "openssl_source": 1,
            "cpython_source": 1,
            "host_build_wheels": 4,
            "cryptography_source": 1,
        },
        "artifacts": artifacts,
        "safety_boundary": {
            "network_started": False,
            "install_started": False,
            "build_started": False,
            "requires_root_manifest_review_before_acquisition": True,
            "requires_exact_manifest_sha_approval_before_downloader_execution": True,
            "task_local_destination_only": True,
            "do_not_use_rustup_or_cargo_to_fetch_unlisted_inputs": True,
            "do_not_modify_live_pyproject_or_uv_lock": True,
            "do_not_run_x86_executables_or_request_rosetta": True,
            "python_headers": "Use only validated CPython 3.12.13 universal2-generated headers; never reuse ARM-only pyconfig.h or synthesize target sysconfig evidence.",
        },
        "open_build_gates": [
            "Root review of this exact acquisition manifest is pending.",
            "The CPython universal2 generated-header and dual-slice route has not run.",
            "OpenSSL target build, link closure, deployment floor, final Mach-O inspection, and Intel runtime installation remain NOT_RUN.",
            "No x86_64 wheel has been built or installed.",
        ],
    }


def junit_row(attempt: str, filename: str, expected: dict, deselected: int) -> dict:
    xml_path = COMBINED / filename
    root = ET.parse(xml_path).getroot()
    suites = list(root.iter("testsuite"))
    if not suites:
        raise SystemExit(f"missing testsuite node: {filename}")
    cases = list(root.iter("testcase"))
    failures = sum(len(case.findall("failure")) for case in cases)
    errors = sum(len(case.findall("error")) for case in cases)
    skipped = sum(len(case.findall("skipped")) for case in cases)
    direct_passes = len(cases) - failures - errors - skipped
    suite_tests = sum(int(suite.attrib.get("tests", "0")) for suite in suites)
    suite_failures = sum(int(suite.attrib.get("failures", "0")) for suite in suites)
    suite_errors = sum(int(suite.attrib.get("errors", "0")) for suite in suites)
    suite_skipped = sum(int(suite.attrib.get("skipped", "0")) for suite in suites)
    subtests = suite_tests - len(cases)
    if (direct_passes, failures, errors, skipped, subtests) != (
        expected["passed"], expected["failed"], expected["errors"], expected["skipped"], expected["subtests"]
    ):
        raise SystemExit(f"independent JUnit parse disagrees with Root child-suite counts: {filename}")
    if (suite_failures, suite_errors, suite_skipped) != (failures, errors, skipped):
        raise SystemExit(f"suite/testcase count mismatch: {filename}")

    stem = filename.removesuffix(".junit.xml")
    result_path = COMBINED / f"{stem}_RESULT.json"
    stdout_path = COMBINED / f"{stem.replace('_RESULT', '')}.stdout.log"
    stderr_path = COMBINED / f"{stem.replace('_RESULT', '')}.stderr.log"
    if attempt == "1":
        stdout_path = COMBINED / "COMBINED_R5_WFP_R4.stdout.log"
        stderr_path = COMBINED / "COMBINED_R5_WFP_R4.stderr.log"
    result = load(result_path)
    result_counts = result.get("junit", {})
    if result_counts.get("tests") != 0:
        raise SystemExit(f"expected the preserved root-count defect in {result_path.name}")
    return {
        "attempt": attempt,
        "status": expected["status"],
        "raw_xml": stamp(xml_path),
        "raw_result_json": stamp(result_path),
        "stdout_log": stamp(stdout_path),
        "stderr_log": stamp(stderr_path),
        "incorrect_preserved_result_junit_root_count": result_counts,
        "corrected_child_suite_and_testcase_counts": {
            "passed": direct_passes,
            "failed": failures,
            "errors": errors,
            "skipped": skipped,
            "deselected_outside_junit": deselected,
            "subtests_in_xml_aggregate": subtests,
            "junit_testsuite_tests": suite_tests,
            "junit_testsuite_case_nodes": len(cases),
        },
    }


def build_count_correction(source_refs: dict[str, dict]) -> dict:
    expectations = [
        ("1", "COMBINED_R5_WFP_R4.junit.xml", {"passed": 103, "failed": 1, "errors": 0, "skipped": 0, "subtests": 0, "status": "RETAINED_FAILURE"}, 0),
        ("2", "COMBINED_R5_WFP_R4_ATTEMPT2.junit.xml", {"passed": 191, "failed": 1, "errors": 0, "skipped": 9, "subtests": 20, "status": "RETAINED_FAILURE"}, 0),
        ("3", "COMBINED_R5_WFP_R4_ATTEMPT3.junit.xml", {"passed": 193, "failed": 0, "errors": 0, "skipped": 9, "subtests": 20, "status": "SCOPED_PASS_WITH_HISTORICAL_FIXTURE_DESELECTED"}, 1),
    ]
    attempts = [junit_row(*row) for row in expectations]
    return {
        "schema": "comsol-mcp.w25-combined-junit-count-correction/1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "APPEND_ONLY_METADATA_CORRECTION; RAW_TESTS_NOT_RERUN",
        "source_evidence": {
            "root_runtime_review": source_refs[ROOT_REVIEW.name],
            "combined_closure": source_refs[CLOSURE.name],
        },
        "finding": "The preserved RESULT JSON generator reads the testsuites root node, which has no counters. Child testsuite/testcase nodes in the immutable raw XML carry the authoritative results.",
        "attempts": attempts,
        "retention": {
            "raw_xml_modified": False,
            "raw_result_json_modified": False,
            "stdout_stderr_modified": False,
            "tests_rerun": False,
            "historical_attempt_failures_preserved": True,
            "historical_git_fixture_deselected_only_in_attempt_3": True,
        },
    }


def observer_packet(source_refs: dict[str, dict]) -> str:
    candidate = load(BASE / "DESKTOP_LAUNCH_CANDIDATE_R2.json")
    task_root = Path(candidate["model"]["path"]).parents[1]
    argv = candidate["documented_launch"]["argv"]
    return f"""# W25 native Desktop observer handoff R1

Status: **PREPARED / NOT LAUNCHED / WAITING FOR SERIAL NATIVE LANE**.

This is a read-only handoff packet. No COMSOL GUI, CUA, TCC permission API,
model API, solver, or study was invoked while creating it. The full bounded
observation plan remains in `FIRST_GUI_OBSERVATION_PLAN_R2.md`.

## Frozen launch candidate

- Candidate: `DESKTOP_LAUNCH_CANDIDATE_R2.json`, SHA-256
  `{sha256(BASE / 'DESKTOP_LAUNCH_CANDIDATE_R2.json')}`.
- Task copy: `{candidate['model']['path']}`; SHA-256
  `{candidate['model']['sha256']}`; 617,475 bytes; device/inode
  `{candidate['model']['identity']['device']}/{candidate['model']['identity']['inode']}`.
- Bundle path/ID: `{candidate['installed_bundle']['path']}` /
  `{candidate['installed_bundle']['identifier']}`. Installed bundle version is
  6.4.0; runtime build 293 still needs verification after an admitted launch.
- Documented argv (not executed): `{" ".join(argv)}`.
- The candidate retains one study, one solver sequence, and an embedded
  solution. The observation has zero study submissions, solver calls, model
  mutations, and permission prompts.

## Fresh preflight evidence and refusal rule

This agent's `ps` and `pgrep` process inventory requests returned
`operation not permitted`; global COMSOL presence is therefore **UNKNOWN**.
This agent started no child operation and holds no active PID, COMSOL handle,
or GUI session. A future owner must obtain a fresh process inventory and
refuse launch if it is unavailable, ambiguous, or finds any existing COMSOL
GUI/server that conflicts with the serialized native lane.

After the native lane is handed off, require exact PID, process birth time,
UID, executable identity, bundle ID, login/session, and verified launcher
ancestry. A launcher may create a Desktop/JVM process tree; record the exact
owned tree from observed parent/child and identity evidence rather than
assuming one operating-system PID. Bound the entire task and cleanup to the
existing 600-second window. Do not terminate a shared Server.

Check Accessibility and Screen Recording permissions without prompting. A
false/unknown result is a typed refusal. Do not use CUA. Read at most 4,096 AX
nodes from the uniquely matched COMSOL window and pair Quartz window owner/ID
with ScreenCaptureKit window/owner identity. Permit at most one optional
window-only capture after both permissions pass; retain it locally only.
Do not read document text, inspect unrelated windows, focus/click/type, save,
close, mutate, run a study, or call a solver. Do not create the selector
profile until observations from the exact build are reviewed.

## Current frozen evidence boundaries

- ARM64 runtime install receipt remains `{source_refs['ARM64_RUNTIME_INSTALL_RECEIPT_R2.json']['sha256']}`; the already installed task-local site and its 886-file inventory were independently accepted within the recorded software-only scope. No reinstall or import/test rerun is requested.
- Intel R3 freeze/manifest are in the sibling `INTEL_X86_BUILDER_INPUT_FREEZE_R3.json` and `INTEL_X86_BUILDER_INPUT_MANIFEST_R3.json`; archive acquisition is not approved by this packet and remains **NOT STARTED**.
- GUI observation, runtime build confirmation, selector profile, Desktop-to-Worker binding, native permissions, and scientific acceptance remain **NOT RUN**.
"""


def main() -> None:
    refs = verify_sources()
    freeze = load(FREEZE)
    input_manifest = load(INPUTS)
    if freeze["manifest"]["sha256"] != refs[INPUTS.name]["sha256"]:
        raise SystemExit("R3 freeze does not bind the present input manifest")
    if input_manifest["status"] != "OPEN_NOT_BUILD_READY; METADATA_ONLY; NO_INTEL_ARCHIVE_DOWNLOADED_OR_BUILD_RUN":
        raise SystemExit("unexpected R3 input manifest status")

    runtime = load(BASE / "ARM64_RUNTIME_INSTALL_RECEIPT_R2.json")
    refs["ARM64_RUNTIME_INSTALL_RECEIPT_R2.json"] = stamp(BASE / "ARM64_RUNTIME_INSTALL_RECEIPT_R2.json")
    if refs["ARM64_RUNTIME_INSTALL_RECEIPT_R2.json"]["sha256"] != "d8166ea89a6a12803103d72b04c78b0b44201d5ef39d026692c65833aec13ef7":
        raise SystemExit("ARM64 receipt bytes differ from the corrected current receipt")
    if runtime["approved_wheel_count"] != 8 or runtime["host"]["architecture"] != "arm64":
        raise SystemExit("ARM64 scoped install receipt does not match expected handoff")

    outputs = [
        BASE / "INTEL_X86_ARCHIVE_ACQUISITION_MANIFEST_R1.json",
        BASE / "COMBINED_JUNIT_COUNT_CORRECTION_R1.json",
        BASE / "W25_NATIVE_OBSERVER_HANDOFF_R1.md",
    ]
    if any(path.exists() for path in outputs):
        raise SystemExit("refusing to overwrite an existing handoff artifact")
    values = [build_archive_manifest(refs), build_count_correction(refs), observer_packet(refs)]
    for path, value in zip(outputs, values, strict=True):
        write_once(path, value)
    for path in outputs:
        print(json.dumps(stamp(path), sort_keys=True))


if __name__ == "__main__":
    main()
