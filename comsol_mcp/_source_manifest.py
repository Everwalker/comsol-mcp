"""Cryptographic source manifest generation and verification for G3.5 release binding."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def generate_source_manifest(repo_root: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve()

    # Git metadata
    try:
        git_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(repo_root), text=True
        ).strip()
        git_tree = subprocess.check_output(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=str(repo_root), text=True
        ).strip()
        raw_dirty = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=str(repo_root), text=True
        ).strip()
        dirty_lines = [
            line for line in raw_dirty.splitlines()
            if not (line[2:].strip().startswith("evidence/") or line[2:].strip() == "evidence")
        ]
        is_dirty = bool(dirty_lines)
        dirty_output = "\n".join(dirty_lines)
    except Exception:
        git_commit = "UNKNOWN"
        git_tree = "UNKNOWN"
        dirty_output = ""
        is_dirty = True

    # Scan production python files
    py_files: dict[str, str] = {}
    comsol_mcp_dir = repo_root / "comsol_mcp"
    if comsol_mcp_dir.is_dir():
        for p in sorted(comsol_mcp_dir.rglob("*.py")):
            rel = str(p.relative_to(repo_root))
            py_files[rel] = sha256_file(p)

    # Scan java worker sources
    java_files: dict[str, str] = {}
    java_dir = comsol_mcp_dir / "worker_java"
    if java_dir.is_dir():
        for p in sorted(java_dir.rglob("*.java")):
            rel = str(p.relative_to(repo_root))
            java_files[rel] = sha256_file(p)

    # Scan catalog and schemas
    data_files: dict[str, str] = {}
    data_dir = comsol_mcp_dir / "data"
    if data_dir.is_dir():
        for p in sorted(data_dir.rglob("*.json")):
            rel = str(p.relative_to(repo_root))
            data_files[rel] = sha256_file(p)

    # Scan test files
    test_files: dict[str, str] = {}
    tests_dir = repo_root / "tests"
    if tests_dir.is_dir():
        for p in sorted(tests_dir.rglob("*.py")):
            rel = str(p.relative_to(repo_root))
            test_files[rel] = sha256_file(p)

    # Critical single-file hashes for direct fast comparison
    critical_hashes = {
        "gateway": py_files.get("comsol_mcp/_mcp_gateway.py", ""),
        "operation_store": py_files.get("comsol_mcp/_operation_store.py", ""),
        "control_daemon": py_files.get("comsol_mcp/_control_daemon.py", ""),
        "g3_w18": py_files.get("comsol_mcp/_g3_w18.py", ""),
        "java_worker": py_files.get("comsol_mcp/_java_worker.py", ""),
        "persistent_worker_java": java_files.get("comsol_mcp/worker_java/PersistentComsolWorker.java", ""),
        "action_catalog": data_files.get("comsol_mcp/data/g2/02_ACTION_CATALOG.json", ""),
    }

    manifest_body = {
        "schema": "comsol-mcp-g3/source-manifest/1",
        "git_commit": git_commit,
        "git_tree": git_tree,
        "is_dirty": is_dirty,
        "dirty_summary": dirty_output if is_dirty else "CLEAN",
        "critical_hashes": critical_hashes,
        "production_python_count": len(py_files),
        "production_python_files": py_files,
        "java_worker_files": java_files,
        "data_and_schema_files": data_files,
        "test_files": test_files,
    }

    # Canonical hash of manifest content
    canonical_json = json.dumps(manifest_body, sort_keys=True, indent=2).encode("utf-8")
    manifest_body["manifest_sha256"] = hashlib.sha256(canonical_json).hexdigest()
    return manifest_body


def verify_source_manifest(repo_root: Path, expected_manifest: dict[str, Any]) -> tuple[bool, list[str]]:
    current = generate_source_manifest(repo_root)
    mismatches = []

    if current["git_tree"] != expected_manifest.get("git_tree"):
        mismatches.append(f"Git tree mismatch: current={current['git_tree']}, expected={expected_manifest.get('git_tree')}")

    exp_crit = expected_manifest.get("critical_hashes", {})
    for key, exp_hash in exp_crit.items():
        curr_hash = current["critical_hashes"].get(key)
        if curr_hash != exp_hash:
            mismatches.append(f"Critical hash mismatch on {key}: current={curr_hash}, expected={exp_hash}")

    exp_py = expected_manifest.get("production_python_files", {})
    for path, exp_hash in exp_py.items():
        curr_hash = current["production_python_files"].get(path)
        if curr_hash != exp_hash:
            mismatches.append(f"File modified: {path} (current={curr_hash}, expected={exp_hash})")

    return len(mismatches) == 0, mismatches
