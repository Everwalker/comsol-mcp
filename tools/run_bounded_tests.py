#!/usr/bin/env python3
"""Run a frozen, offline, explicitly selected software-test candidate."""
from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shutil
import stat
import subprocess
import sys
import threading
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASELINE_SCHEMA = "BOUNDED_OFFLINE_TEST_BASELINE_V1"
MAX_JUNIT_BYTES = 2 * 1024 * 1024
MAX_SUMMARY_BYTES = 64 * 1024
MAX_STDOUT_BYTES = 2048
MAX_ERROR_CHARS = 360
MAX_FAILURES = 20
STAGE_TIMEOUT_S = 300


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def output_root(root: Path | None = None) -> Path:
    root = root or repo_root()
    return root.parents[1] / "execution-scratch" / "test-efficiency"


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, check=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, timeout=15,
    )
    return result.stdout.strip()


def _safe_repo_file(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"unsafe repository path: {relative}")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"repository path escapes checkout: {relative}")
    return resolved


def _safe_paths(values: Any, *, label: str, allow_nodes: bool = False) -> tuple[str, ...]:
    if not isinstance(values, list) or not values or len(values) > 512:
        raise ValueError(f"{label} must be a nonempty bounded list")
    normalized = []
    for value in values:
        if not isinstance(value, str) or not value or value.startswith("-") or "\0" in value:
            raise ValueError(f"{label} contains an invalid value")
        path_text = value.split("::", 1)[0] if allow_nodes else value
        path = Path(path_text)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError(f"{label} contains an unsafe path")
        if allow_nodes and path.parts[0] != "tests":
            raise ValueError(f"{label} must select explicit tests under tests/")
        normalized.append(value)
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{label} contains duplicate entries")
    return tuple(normalized)


def validate_overlay(root: Path, base_commit: str, overlay_paths: tuple[str, ...], scope: tuple[str, ...]) -> list[str]:
    errors = []
    if len(base_commit) != 40 or any(c not in "0123456789abcdef" for c in base_commit.lower()):
        return ["base commit must be a full Git object id"]
    try:
        head = _git(root, "rev-parse", "HEAD")
        ancestry = subprocess.run(
            ["git", "merge-base", "--is-ancestor", base_commit, head], cwd=root,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=15,
        )
        if ancestry.returncode != 0:
            return ["frozen base commit is not an ancestor of this candidate"]
        committed = set(filter(None, _git(root, "diff", "--name-only", f"{base_commit}..{head}", "--", *scope).splitlines()))
        changed = set(filter(None, _git(root, "diff", "--name-only", "HEAD", "--", *scope).splitlines()))
        untracked = set(filter(None, _git(root, "ls-files", "--others", "--exclude-standard", "--", *scope).splitlines()))
        dirty = changed | untracked
        actual = committed | dirty
        if actual != set(overlay_paths):
            extra = sorted(actual - set(overlay_paths))[:10]
            missing = sorted(set(overlay_paths) - actual)[:10]
            errors.append(f"candidate overlay mismatch; extra={extra}, missing={missing}")
    except (OSError, subprocess.SubprocessError) as exc:
        errors.append(f"cannot verify base-to-candidate overlay: {exc}")
    return errors


def validate_baseline(
    root: Path,
    baseline_path: Path,
    data: dict[str, Any],
    *,
    python_identity: dict[str, Any] | None = None,
    tmp_root: Path | None = None,
) -> list[str]:
    """Return fail-closed baseline/preflight findings; never launches tests."""
    errors: list[str] = []
    if data.get("schema") != BASELINE_SCHEMA:
        errors.append("baseline schema is missing or unsupported")
    candidate_id = data.get("candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id.strip() or len(candidate_id) > 96:
        errors.append("candidate id is missing or invalid")
    base_commit = data.get("baseline_commit", "")
    try:
        source_paths = _safe_paths(data.get("source_paths"), label="source_paths")
    except ValueError as exc:
        source_paths = ()
        errors.append(str(exc))
    declared_hashes = data.get("source_sha256")
    if not isinstance(declared_hashes, dict) or set(declared_hashes) != set(source_paths):
        errors.append("source hash map is missing, mixed, or has extra paths")
    else:
        for rel in source_paths:
            try:
                actual = sha256_file(_safe_repo_file(root, rel))
                if declared_hashes.get(rel) != actual:
                    errors.append(f"source hash mismatch: {rel}")
            except (OSError, ValueError) as exc:
                errors.append(f"source unavailable: {rel}: {exc}")

    try:
        production_files = set(filter(None, _git(root, "ls-files", "--", "comsol_mcp").splitlines()))
        production_files = {path for path in production_files if path.endswith((".py", ".json"))}
        if production_files - set(source_paths):
            errors.append("candidate source closure omits tracked comsol_mcp Python/data dependencies")
    except (OSError, subprocess.SubprocessError) as exc:
        errors.append(f"cannot enumerate tracked Python/data dependencies: {exc}")

    try:
        overlay_paths = _safe_paths(data.get("overlay_paths"), label="overlay_paths")
        scope = _safe_paths(data.get("overlay_scope"), label="overlay_scope")
        baseline_rel = baseline_path.resolve().relative_to(root.resolve()).as_posix()
        if baseline_rel not in overlay_paths:
            errors.append("baseline manifest is not part of the frozen overlay")
        if not set(overlay_paths) <= set(source_paths) | {baseline_rel}:
            errors.append("overlay contains files outside the candidate source closure")
        errors.extend(validate_overlay(root, str(base_commit), overlay_paths, scope))
    except (ValueError, OSError) as exc:
        errors.append(f"candidate overlay configuration is invalid: {exc}")

    try:
        focus_nodes = _safe_paths(data.get("focus_nodes"), label="focus_nodes", allow_nodes=True)
        regression_nodes = data.get("required_regression_nodes", [])
        if regression_nodes:
            regression_nodes = _safe_paths(regression_nodes, label="required_regression_nodes", allow_nodes=True)
        else:
            regression_nodes = ()
        if not set(node.split("::", 1)[0] for node in focus_nodes + regression_nodes) <= set(source_paths):
            errors.append("selected test source is absent from the candidate hash closure")
        runner_rel = Path(__file__).resolve().relative_to(root.resolve()).as_posix()
        if runner_rel not in source_paths:
            errors.append("runner source is absent from the candidate hash closure")
        focus_count = data.get("focus_test_cases")
        regression_count = data.get("regression_test_cases", 0)
        if type(focus_count) is not int or focus_count <= 0:
            errors.append("expected focused test-case count is missing or invalid")
        if type(regression_count) is not int or regression_count < 0 or bool(regression_nodes) != (regression_count > 0):
            errors.append("expected regression test-case count is missing or invalid")
    except ValueError as exc:
        errors.append(str(exc))

    fixture = data.get("historical_fixture", {})
    try:
        fixture_path = str(fixture["path"])
        _safe_repo_file(root, fixture_path)
        blob = _git(root, "rev-parse", f"{base_commit}:{fixture_path}")
        worktree_blob = _git(root, "hash-object", fixture_path)
        if blob != fixture.get("git_blob_sha1") or worktree_blob != blob:
            errors.append("selected historical Git fixture is absent or changed")
        if sha256_file(_safe_repo_file(root, fixture_path)) != fixture.get("sha256"):
            errors.append("selected historical fixture content hash differs")
    except (KeyError, OSError, ValueError, subprocess.SubprocessError) as exc:
        errors.append(f"selected historical fixture is unavailable: {exc}")

    environment = data.get("environment", {})
    if not isinstance(environment, dict):
        environment = {}
        errors.append("environment fingerprint configuration is invalid")
    configured_output = environment.get("output_root")
    if not isinstance(configured_output, str) or Path(configured_output).resolve() != output_root(root).resolve():
        errors.append("pinned evidence output root is missing or outside project execution scratch")
    python_executable = environment.get("python_executable")
    python_prefix = environment.get("python_prefix")
    if not isinstance(python_executable, str) or not Path(python_executable).is_absolute():
        errors.append("pinned interpreter path is missing or invalid")
    if not isinstance(python_prefix, str) or not Path(python_prefix).is_absolute():
        errors.append("pinned interpreter prefix is missing or invalid")
    if python_identity is not None:
        if python_identity.get("executable") != environment.get("python_executable"):
            errors.append("running interpreter differs from the pinned executable")
        if python_identity.get("version") != environment.get("python_version"):
            errors.append("running interpreter version differs from the pinned version")
        if python_identity.get("prefix") != environment.get("python_prefix"):
            errors.append("running interpreter prefix differs from the pinned venv")
        if python_identity.get("comsol_origin") != str((root / "comsol_mcp" / "__init__.py").resolve()):
            errors.append("comsol_mcp import resolves outside the candidate checkout")
        prefix = str(Path(str(environment.get("python_prefix", ""))).resolve())
        if not str(python_identity.get("pytest_origin", "")).startswith(prefix + "/"):
            errors.append("pytest import resolves outside the pinned venv")
        if python_identity.get("pytest_version") != environment.get("pytest_version"):
            errors.append("pytest version differs from the pinned environment")
    configured_tmp = environment.get("tmp_root")
    if not isinstance(configured_tmp, str) or (tmp_root is not None and tmp_root.resolve() != Path(configured_tmp).resolve()):
        errors.append("runtime temp root differs from the pinned APFS volume")
    image_path = environment.get("image_path")
    if not isinstance(image_path, str) or not image_path or not Path(image_path).is_absolute():
        errors.append("pinned scratch image path is missing or invalid")
    elif isinstance(configured_output, str) and not Path(image_path).resolve().is_relative_to(Path(configured_output).resolve()):
        errors.append("pinned scratch image is outside the project execution scratch root")

    java = data.get("java_compile")
    if java is not None:
        if not isinstance(java, dict) or java.get("mode") != "offline_no_processors":
            errors.append("Java compile must be an explicit offline no-processor configuration")
        else:
            try:
                java_sources = _safe_paths(java.get("sources"), label="java_compile.sources")
                if not set(java_sources) <= set(source_paths) or any(not path.endswith(".java") for path in java_sources):
                    errors.append("Java source allowlist is not covered by candidate source hashes")
                if not Path(str(java.get("javac", ""))).is_absolute():
                    errors.append("Java compiler path must be explicit and absolute")
            except ValueError as exc:
                errors.append(str(exc))
    baseline_bytes = baseline_path.read_bytes()
    if len(baseline_bytes) > 256 * 1024:
        errors.append("baseline manifest exceeds the bounded size")
    return errors


def _python_probe(root: Path, tmp_root: Path, python: Path, home_dir: Path | None = None) -> tuple[dict[str, Any] | None, str | None]:
    code = (
        "import json,sys,pytest,comsol_mcp; "
        "from pathlib import Path; "
        "print(json.dumps({'executable':sys.executable,'version':sys.version.split()[0],"
        "'prefix':sys.prefix,'pytest_origin':str(Path(pytest.__file__).resolve()),"
        "'pytest_version':pytest.__version__,'comsol_origin':str(Path(comsol_mcp.__file__).resolve())}))"
    )
    home_dir = home_dir or (tmp_root / "test-efficiency" / "probe-home")
    home_dir.mkdir(parents=True, exist_ok=True)
    env = _controlled_environment(root, tmp_root, python, home_dir)
    try:
        raw = subprocess.check_output(
            [str(python), "-c", code], cwd=root, env=env, stderr=subprocess.STDOUT,
            timeout=20,
        )
        identity = json.loads(raw.decode("utf-8"))
        identity["controlled_environment_sha256"] = sha256_bytes(canonical_json(env))
        return identity, None
    except (OSError, subprocess.SubprocessError, UnicodeError, json.JSONDecodeError) as exc:
        return None, f"interpreter/import probe failed: {exc}"


def _controlled_environment(root: Path, tmp_root: Path, python: Path, home_dir: Path | None = None) -> dict[str, str]:
    prefix = Path(os.path.abspath(python)).parent.parent
    home_dir = home_dir or (tmp_root / "test-efficiency" / "probe-home")
    return {
        "HOME": str(home_dir),
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": f"{prefix / 'bin'}:/usr/bin:/bin:/usr/sbin:/sbin",
        "PYTHONPATH": str(root),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_ADDOPTS": "",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "TEMP": str(tmp_root),
        "TMP": str(tmp_root),
        "TMPDIR": str(tmp_root),
        "TZ": "UTC",
        "VIRTUAL_ENV": str(prefix),
        "XDG_CACHE_HOME": str(tmp_root / "cache"),
    }


def _hdiutil_identity(image_path: Path, mount_path: Path) -> dict[str, Any] | None:
    try:
        raw = subprocess.check_output(["/usr/bin/hdiutil", "info", "-plist"], timeout=15)
        info = plistlib.loads(raw)
    except (OSError, subprocess.SubprocessError, plistlib.InvalidFileException):
        return None
    matches = []
    for image in info.get("images", []):
        if Path(str(image.get("image-path", ""))).resolve() != image_path.resolve():
            continue
        entities = image.get("system-entities", [])
        mounted = [entity for entity in entities if entity.get("mount-point") == str(mount_path)
                   and entity.get("content-hint") == "41504653-0000-11AA-AA11-00306543ECAC"]
        if mounted:
            matches.append({
                "image_path": str(image_path.resolve()),
                "mount_path": str(mount_path.resolve()),
                "filesystem": "apfs",
                "mounted_devices": [entity.get("dev-entry") for entity in mounted],
                "image_devices": [entity.get("dev-entry") for entity in entities],
            })
    return matches[0] if len(matches) == 1 else None


def _appledouble_entries(path: Path) -> list[str]:
    if not path.exists():
        return []
    found: list[str] = []
    for child in path.iterdir():
        if child.name.startswith("._"):
            found.append(str(child))
    return sorted(found)[:MAX_FAILURES]


def scratch_probe(output: Path, tmp_root: Path, image_path: Path, expected_output: Path) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    if output.resolve() != expected_output.resolve():
        errors.append("evidence output root is not the frozen external SSD scratch path")
    if not output.is_dir():
        errors.append("evidence output root is unavailable")
    output_appledouble = _appledouble_entries(output)
    tmp = tmp_root.resolve()
    if not tmp.is_dir():
        errors.append("APFS temp volume is missing or does not match the pinned mount")
    if not image_path.is_file() or not image_path.resolve().is_relative_to(expected_output.resolve()):
        errors.append("pinned sparse image is missing from the external evidence root")
    if _appledouble_entries(tmp):
        errors.append("AppleDouble files already exist in the APFS temp root")
    identity = _hdiutil_identity(image_path, tmp)
    if identity is None:
        errors.append("pinned APFS image is not mounted at the configured temp path")

    result: dict[str, Any] = {
        "output_root": str(output.resolve()),
        "tmp_root": str(tmp),
        "filesystem_probe": "NOT_RUN",
        "volume_identity": identity,
        "output_appledouble": output_appledouble,
        "appledouble": [],
        "extended_attributes": "NOT_RUN_optional",
    }
    if errors:
        result["filesystem_probe"] = "BLOCKED"
        return result, errors

    probe = tmp / f".test-efficiency-probe-{uuid.uuid4().hex}"
    try:
        probe.mkdir()
        data_file = probe / "payload.bin"
        data_file.write_bytes(b"test-efficiency-scratch-probe\n")
        if data_file.read_bytes() != b"test-efficiency-scratch-probe\n":
            errors.append("APFS scratch write/readback differs")
        data_file.chmod(0o640)
        if stat.S_IMODE(data_file.stat().st_mode) != 0o640:
            errors.append("APFS scratch permission-bit readback differs")
        link = probe / "payload-link"
        link.symlink_to(data_file.name)
        if not link.is_symlink() or link.resolve() != data_file.resolve() or link.read_bytes() != data_file.read_bytes():
            errors.append("APFS scratch symlink readback differs")
        entries = {child.name for child in probe.iterdir()}
        if entries != {"payload.bin", "payload-link"}:
            errors.append(f"APFS scratch directory contains unexpected entries: {sorted(entries)}")
        sidecars = _appledouble_entries(probe) + [str(p) for p in tmp.iterdir() if p.name == "._" + probe.name]
        if sidecars:
            result["appledouble"] = sidecars
            errors.append("AppleDouble sidecar appeared during APFS scratch probe; preserve it and use a clean APFS mount")
        if not errors:
            shutil.rmtree(probe)
            result["filesystem_probe"] = "PASS"
        else:
            result["filesystem_probe"] = "FAIL"
            result["probe_directory"] = str(probe)
    except OSError as exc:
        result["filesystem_probe"] = "FAIL"
        result["probe_directory"] = str(probe)
        errors.append(f"scratch write probe failed: {exc}")
    return result, errors


def candidate_fingerprint(
    root: Path, baseline_path: Path, data: dict[str, Any], identity: dict[str, Any],
    scratch_identity: dict[str, Any], tmp_root: Path,
) -> tuple[str | None, list[str]]:
    errors = validate_baseline(root, baseline_path, data, python_identity=identity, tmp_root=tmp_root)
    if errors:
        return None, errors
    source_paths = tuple(data["source_paths"])
    payload = {
        "candidate_id": data["candidate_id"],
        "baseline_sha256": sha256_file(baseline_path),
        "baseline_commit": data["baseline_commit"],
        "source_sha256": {rel: sha256_file(_safe_repo_file(root, rel)) for rel in source_paths},
        "environment": identity,
        "scratch_volume": scratch_identity,
    }
    return sha256_bytes(canonical_json(payload)), []


def parse_junit(path: Path, expected: int) -> dict[str, Any]:
    if not path.is_file():
        return {"status": "FAIL", "reason": "JUnit report missing", "tests": 0, "executed": 0,
                "failures": [], "failure_count": 0, "error_count": 0}
    if path.stat().st_size > MAX_JUNIT_BYTES:
        return {"status": "FAIL", "reason": "JUnit report exceeds size limit", "tests": 0,
                "executed": 0, "failures": [], "failure_count": 0, "error_count": 0}
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        return {"status": "FAIL", "reason": f"malformed JUnit: {exc}", "tests": 0,
                "executed": 0, "failures": [], "failure_count": 0, "error_count": 0}
    if root.tag not in {"testsuite", "testsuites"}:
        return {"status": "FAIL", "reason": "unexpected JUnit root", "tests": 0,
                "executed": 0, "failures": [], "failure_count": 0, "error_count": 0}
    cases = list(root.iter("testcase"))
    skipped = sum(1 for case in cases if case.find("skipped") is not None)
    failure_nodes = list(root.iter("failure"))
    error_nodes = list(root.iter("error"))
    failure_count = len(failure_nodes)
    error_count = len(error_nodes)
    failures: list[dict[str, str]] = []
    in_case = {id(child) for case in cases for child in case.iter()}
    for case in cases:
        for kind in ("failure", "error"):
            node = case.find(kind)
            if node is None:
                continue
            text = " ".join(filter(None, [node.get("message"), " ".join((node.text or "").split())]))
            failures.append({
                "nodeid": f"{case.get('classname', 'unknown')}::{case.get('name', 'unknown')}",
                "kind": kind,
                "error": text[:MAX_ERROR_CHARS],
            })
    for kind, nodes in (("failure", failure_nodes), ("error", error_nodes)):
        for node in nodes:
            if id(node) in in_case:
                continue
            parent = node
            suite_name = "suite"
            text = " ".join(filter(None, [node.get("message"), " ".join((node.text or "").split())]))
            failures.append({"nodeid": f"{suite_name}::<suite-level>", "kind": kind,
                             "error": text[:MAX_ERROR_CHARS]})

    executed = len(cases) - skipped
    reasons = []
    if not cases:
        reasons.append("zero test cases")
    if len(cases) != expected:
        reasons.append(f"expected {expected} test cases, got {len(cases)}")
    if executed <= 0:
        reasons.append("zero tests executed")
    if skipped:
        reasons.append(f"{skipped} tests skipped")
    if failure_count or error_count:
        reasons.append(f"{failure_count} failures and {error_count} errors")
    inconsistent: list[str] = []
    for suite in [element for element in root.iter() if element.tag in {"testsuite", "testsuites"}]:
        descendants = list(suite.iter("testcase"))
        actual = {
            "tests": len(descendants),
            "failures": len(list(suite.iter("failure"))),
            "errors": len(list(suite.iter("error"))),
            "skipped": sum(1 for case in descendants if case.find("skipped") is not None),
        }
        for key, count in actual.items():
            declared = suite.get(key)
            if declared is None:
                continue
            try:
                if int(declared) != count:
                    inconsistent.append(f"{suite.tag} {key}={declared} but cases report {count}")
            except ValueError:
                inconsistent.append(f"{suite.tag} {key} is not an integer")
    if inconsistent:
        reasons.extend(inconsistent[:MAX_FAILURES])
    return {
        "status": "PASS" if not reasons else "FAIL",
        "reason": "; ".join(reasons) if reasons else None,
        "tests": len(cases), "executed": executed, "skipped": skipped,
        "failure_count": failure_count, "error_count": error_count,
        "failures": failures[:MAX_FAILURES],
    }


def evaluate_test_stage(returncode: int | None, report: dict[str, Any], source_unchanged: bool) -> tuple[str, list[str]]:
    errors = []
    if returncode != 0:
        errors.append(f"test process exit was {returncode}")
    if report.get("status") != "PASS":
        errors.append(str(report.get("reason") or "JUnit acceptance failed"))
    if not source_unchanged:
        errors.append("candidate source/environment fingerprint changed during tests")
    return ("PASS" if not errors else "FAIL"), errors


def required_regression_plan(nodes: tuple[str, ...], requested: bool) -> str:
    if not nodes:
        return "NOT_CONFIGURED"
    return "RUN" if requested else "PARTIAL"


def run_capped(command: list[str], cwd: Path, env: dict[str, str], log_path: Path, timeout_s: int) -> dict[str, Any]:
    total = 0
    log_error: list[str] = []
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=0, close_fds=True,
        )
    except OSError as exc:
        log_path.write_text(f"process start failed: {exc}\n", encoding="utf-8")
        return {"returncode": None, "timeout": False, "bytes_seen": 0, "log_truncated": False,
                "log_error": None, "start_error": str(exc)[:MAX_ERROR_CHARS]}

    def drain() -> None:
        nonlocal total
        assert process is not None and process.stdout is not None
        log = None
        try:
            log = log_path.open("wb")
        except OSError as exc:
            log_error.append(str(exc)[:MAX_ERROR_CHARS])
        try:
            while True:
                block = process.stdout.read(8192)
                if not block:
                    break
                total += len(block)
                if log is not None:
                    try:
                        log.write(block)
                    except OSError as exc:
                        log_error.append(str(exc)[:MAX_ERROR_CHARS])
                        log.close()
                        log = None
        finally:
            if log is not None:
                try:
                    log.flush()
                    os.fsync(log.fileno())
                except OSError as exc:
                    log_error.append(str(exc)[:MAX_ERROR_CHARS])
                log.close()

    thread = threading.Thread(target=drain, name="bounded-test-log", daemon=True)
    thread.start()
    timed_out = False
    try:
        process.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait()
    thread.join(timeout=5)
    if thread.is_alive() and process.stdout:
        process.stdout.close()
        thread.join(timeout=1)
    return {"returncode": process.returncode, "timeout": timed_out, "bytes_seen": total,
            "log_truncated": False, "log_error": log_error or None, "start_error": None}


def _artifact(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return {"path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size}


def _write_summary(output: Path, summary: dict[str, Any]) -> None:
    raw = json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n"
    if len(raw) > MAX_SUMMARY_BYTES:
        raise ValueError("summary exceeds the fixed size limit")
    output.write_bytes(raw)
    sidecars = _appledouble_entries(output.parent)
    if sidecars:
        summary["output_appledouble_warning"] = sidecars
        raw = json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n"
        if len(raw) <= MAX_SUMMARY_BYTES:
            output.write_bytes(raw)


def _emit_summary(summary: dict[str, Any]) -> None:
    stages = []
    for stage in summary.get("stages", []):
        junit = stage.get("junit", {})
        stages.append({
            "name": stage.get("name"), "status": stage.get("status"),
            "returncode": stage.get("returncode"), "tests": junit.get("tests"),
            "executed": junit.get("executed"), "failures": junit.get("failure_count"),
            "errors": junit.get("error_count"),
        })
    compact = {
        "schema": summary.get("schema"), "run_id": summary.get("run_id"),
        "candidate_id": summary.get("candidate_id"), "status": summary.get("status"),
        "preflight": summary.get("preflight"), "native_acceptance": summary.get("native_acceptance"),
        "stages": stages,
        "failures": summary.get("failures", [])[:3],
        "errors": [str(error)[:180] for error in summary.get("errors", [])[:4]],
        "summary_path": summary.get("summary_path"),
    }
    raw = json.dumps(compact, sort_keys=True, ensure_ascii=False)
    if len(raw.encode("utf-8")) > MAX_STDOUT_BYTES:
        compact["failures"] = []
        compact["errors"] = compact["errors"][:2]
        compact["stages"] = [{"name": stage.get("name"), "status": stage.get("status")} for stage in stages]
        raw = json.dumps(compact, sort_keys=True, ensure_ascii=False)
    if len(raw.encode("utf-8")) > MAX_STDOUT_BYTES:
        raw = json.dumps({key: compact.get(key) for key in ("schema", "run_id", "status", "summary_path")},
                         sort_keys=True)
    print(raw)


def _run_test_stage(
    name: str, nodes: tuple[str, ...], expected: int, root: Path, run_dir: Path,
    tmp_root: Path, env: dict[str, str], initial_fingerprint: str,
    baseline_path: Path, baseline: dict[str, Any], identity: dict[str, Any], scratch: dict[str, Any],
) -> dict[str, Any]:
    junit = run_dir / f"{name}.junit.xml"
    log = run_dir / f"{name}.log"
    command = [str(identity["executable"]), "-m", "pytest", "-q", "-s", "-p", "no:cacheprovider",
               "--junitxml", str(junit), *nodes]
    process = run_capped(command, root, env, log, STAGE_TIMEOUT_S)
    parsed = parse_junit(junit, expected)
    after, fingerprint_errors = candidate_fingerprint(root, baseline_path, baseline, identity, scratch, tmp_root)
    unchanged = after == initial_fingerprint and not fingerprint_errors
    status, errors = evaluate_test_stage(process["returncode"], parsed, unchanged)
    if process["timeout"]:
        errors.append("test stage timed out")
        status = "FAIL"
    if process.get("log_error"):
        errors.append("raw process log could not be fully preserved: " + "; ".join(process["log_error"]))
        status = "FAIL"
    errors.extend(fingerprint_errors)
    return {
        "name": name,
        "status": status,
        "command": command,
        "returncode": process["returncode"],
        "timeout": process["timeout"],
        "log_truncated": process["log_truncated"],
        "log_bytes_seen": process["bytes_seen"],
        "log_error": process.get("log_error"),
        "junit": parsed,
        "errors": errors[:MAX_FAILURES],
        "artifacts": {"log": _artifact(log), "junit": _artifact(junit)},
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-required-regression", action="store_true")
    parser.add_argument("--with-java-compile", action="store_true")
    parser.add_argument("--baseline", type=Path, default=Path(__file__).with_name("test_efficiency_baseline.json"))
    args = parser.parse_args(argv)

    root = repo_root()
    baseline_path = args.baseline.resolve()
    evidence_root = output_root(root).resolve()
    evidence_root.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    summary_path = evidence_root / f"{run_id}.summary.json"
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        summary: dict[str, Any] = {
            "schema": "BOUNDED_TEST_RUN_V1", "run_id": run_id, "candidate_id": None,
            "status": "BLOCKED", "native_acceptance": "NOT_RUN", "baseline_commit": None,
            "candidate_fingerprint": None, "stages": [], "failures": [], "errors": [],
            "summary_path": str(summary_path), "artifacts": {},
        }
        summary["errors"] = [f"baseline unreadable: {exc}"[:MAX_ERROR_CHARS]]
        _write_summary(summary_path, summary)
        _emit_summary(summary)
        return 2

    environment = baseline.get("environment", {}) if isinstance(baseline, dict) else {}
    if not isinstance(environment, dict):
        environment = {}
    configured_output = Path(str(environment.get("output_root") or evidence_root)).resolve()
    configured_tmp = Path(str(environment.get("tmp_root") or "/unconfigured"))
    tmp_root = configured_tmp.resolve()
    image_path = Path(str(environment.get("image_path") or "/unconfigured"))
    python_path = Path(str(environment.get("python_executable") or "/unconfigured"))
    expected_output = output_root(root).resolve()
    evidence_root = configured_output if configured_output == expected_output else expected_output
    evidence_root.mkdir(parents=True, exist_ok=True)
    summary_path = evidence_root / f"{run_id}.summary.json"
    summary = {
        "schema": "BOUNDED_TEST_RUN_V1", "run_id": run_id,
        "candidate_id": baseline.get("candidate_id") if isinstance(baseline, dict) else None,
        "status": "BLOCKED", "native_acceptance": "NOT_RUN",
        "baseline_commit": baseline.get("baseline_commit") if isinstance(baseline, dict) else None,
        "candidate_fingerprint": None, "stages": [], "failures": [], "errors": [],
        "summary_path": str(summary_path), "artifacts": {},
    }
    scratch, scratch_errors = scratch_probe(evidence_root, tmp_root, image_path, configured_output)
    summary["scratch"] = scratch
    summary["errors"].extend(scratch_errors[:MAX_FAILURES])
    if scratch_errors:
        summary["status"] = "BLOCKED"
        _write_summary(summary_path, summary)
        _emit_summary(summary)
        return 2

    run_dir = tmp_root / "test-efficiency" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "home").mkdir()
    env = _controlled_environment(root, tmp_root, python_path, run_dir / "home")
    identity, identity_error = _python_probe(root, tmp_root, python_path, run_dir / "home")
    if identity_error:
        summary["errors"].append(identity_error[:MAX_ERROR_CHARS])
    if identity is None:
        summary["status"] = "BLOCKED"
        _write_summary(summary_path, summary)
        _emit_summary(summary)
        return 2
    summary["environment"] = identity
    baseline_errors = validate_baseline(
        root, baseline_path, baseline, python_identity=identity, tmp_root=tmp_root,
    )
    if baseline_errors:
        summary["errors"].extend(baseline_errors[:MAX_FAILURES])
        summary["status"] = "FAIL" if any("hash" in e or "stale" in e or "overlay" in e or "commit" in e for e in baseline_errors) else "BLOCKED"
        summary["artifacts"]["preflight_dir"] = str(run_dir)
        _write_summary(summary_path, summary)
        _emit_summary(summary)
        return 2

    fingerprint, fingerprint_errors = candidate_fingerprint(
        root, baseline_path, baseline, identity, scratch, tmp_root,
    )
    if fingerprint_errors or fingerprint is None:
        summary["errors"].extend(fingerprint_errors[:MAX_FAILURES])
        summary["status"] = "FAIL"
        _write_summary(summary_path, summary)
        _emit_summary(summary)
        return 2
    summary["candidate_fingerprint"] = fingerprint
    summary["preflight"] = "PASS"
    summary["artifacts"]["run_dir"] = str(run_dir)

    if args.with_java_compile:
        java = baseline.get("java_compile")
        if not isinstance(java, dict) or not java.get("sources"):
            summary["stages"].append({"name": "java_compile", "status": "BLOCKED",
                                      "errors": ["Java compilation was requested but no exact offline source allowlist is configured"]})
            summary["status"] = "BLOCKED"
            _write_summary(summary_path, summary)
            _emit_summary(summary)
            return 2
        if java.get("mode") != "offline_no_processors":
            summary["stages"].append({"name": "java_compile", "status": "BLOCKED", "errors": ["Java compile mode must disable annotation processors"]})
            summary["status"] = "BLOCKED"
            _write_summary(summary_path, summary)
            _emit_summary(summary)
            return 2
        source_paths = tuple(java["sources"])
        if not source_paths or any(Path(p).is_absolute() or ".." in Path(p).parts for p in source_paths):
            summary["status"] = "FAIL"
            summary["errors"].append("Java compile source path is unsafe")
            _write_summary(summary_path, summary)
            _emit_summary(summary)
            return 2
        classes = run_dir / "classes"
        classes.mkdir()
        javac = str(java["javac"])
        command = [javac, "-proc:none", "-d", str(classes), *[str(_safe_repo_file(root, p)) for p in source_paths]]
        result = run_capped(command, root, env, run_dir / "java-compile.log", STAGE_TIMEOUT_S)
        stage_status = "PASS" if result["returncode"] == 0 and not result["timeout"] and not result.get("log_error") else "FAIL"
        summary["stages"].append({"name": "java_compile", "status": stage_status,
                                  "returncode": result["returncode"], "timeout": result["timeout"],
                                  "log_truncated": result["log_truncated"],
                                  "log_bytes_seen": result["bytes_seen"], "log_error": result.get("log_error"),
                                  "artifacts": {"log": _artifact(run_dir / "java-compile.log")}})
        if stage_status != "PASS":
            summary["status"] = "FAIL"
            _write_summary(summary_path, summary)
            _emit_summary(summary)
            return 1

    focus = _run_test_stage(
        "focus", tuple(baseline["focus_nodes"]), int(baseline["focus_test_cases"]), root,
        run_dir, tmp_root, env, fingerprint, baseline_path, baseline, identity, scratch,
    )
    summary["stages"].append(focus)
    summary["failures"].extend(focus["junit"].get("failures", []))
    if focus["status"] != "PASS":
        summary["status"] = "FAIL"
        _write_summary(summary_path, summary)
        _emit_summary(summary)
        return 1

    regression_nodes = tuple(baseline.get("required_regression_nodes", []))
    regression_plan = required_regression_plan(regression_nodes, args.with_required_regression)
    if regression_plan == "PARTIAL":
        message = "required regression is configured but was not requested; scoped focus passed only"
        summary["status"] = "PARTIAL"
        summary["errors"].append(message)
        summary["stages"].append({"name": "required_regression", "status": "NOT_RUN",
                                  "errors": [message]})
        _write_summary(summary_path, summary)
        _emit_summary(summary)
        return 3
    if regression_plan == "RUN":
        regression = _run_test_stage(
            "required_regression", regression_nodes,
            int(baseline["regression_test_cases"]), root, run_dir, tmp_root, env,
            fingerprint, baseline_path, baseline, identity, scratch,
        )
        summary["stages"].append(regression)
        summary["failures"].extend(regression["junit"].get("failures", []))
        if regression["status"] != "PASS":
            summary["status"] = "FAIL"
            _write_summary(summary_path, summary)
            _emit_summary(summary)
            return 1
    else:
        summary["stages"].append({"name": "required_regression", "status": "NOT_CONFIGURED"})

    summary["status"] = "PASS"
    for stage in summary["stages"]:
        for artifact in stage.get("artifacts", {}).values():
            if artifact:
                summary["artifacts"].setdefault("test_outputs", []).append(artifact)
    _write_summary(summary_path, summary)
    _emit_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
