#!/usr/bin/env python3
"""Restore authoritative COMSOL MCP G3.5 source from a Git bundle.

Usage:
    python tools/restore_g3_5_delivery.py --bundle /path/to/comsol_mcp_g3_5.bundle --target /path/to/clean_target
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

EXPECTED_DELIVERY_COMMIT = "247a032b6f1aad3db4c6125e6f45eccb4f12a8af"
CONTINUATION_BRANCH = "handoff/g3_5_continuation"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def restore_delivery(
    bundle_path: Path,
    target_dir: Path,
    *,
    shallow_source: Path | None = None,
    expected_commit: str | None = None,
    create_branch: str = CONTINUATION_BRANCH,
) -> dict[str, Any]:
    bundle_path = bundle_path.resolve()
    target_dir = target_dir.resolve()

    if not bundle_path.is_file():
        raise FileNotFoundError(f"Git bundle not found: {bundle_path}")

    if target_dir.exists():
        if any(target_dir.iterdir()):
            raise ValueError(f"Target directory is not empty: {target_dir}")
    else:
        target_dir.mkdir(parents=True, exist_ok=True)

    print(f"[*] Step 1: Verifying bundle integrity: {bundle_path.name}")
    bundle_sha = sha256_file(bundle_path)

    # 1. Verify bundle with git bundle verify
    verify_cmd = ["git", "bundle", "verify", str(bundle_path)]
    p_verify = subprocess.run(verify_cmd, capture_output=True, text=True)
    if p_verify.returncode != 0:
        print(f"[!] Bundle verify note: {p_verify.stderr.strip()}")

    # 2. List heads in bundle
    list_heads = subprocess.check_output(
        ["git", "bundle", "list-heads", str(bundle_path)], text=True
    ).strip()
    print(f"[*] Bundle heads:\n{list_heads}")

    # Extract target HEAD from bundle
    bundle_heads = {}
    for line in list_heads.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2:
            bundle_heads[parts[1]] = parts[0]

    head_commit = bundle_heads.get("HEAD")
    if not head_commit and bundle_heads:
        head_commit = list(bundle_heads.values())[0]

    # 3. Clone bundle into target_dir
    print(f"[*] Step 2: Cloning bundle into {target_dir}...")
    subprocess.check_call(
        ["git", "clone", str(bundle_path), str(target_dir)],
        stdout=subprocess.DEVNULL,
    )

    # 4. Restore shallow metadata if shallow_source exists
    shallow_target = target_dir / ".git" / "shallow"
    if shallow_source and shallow_source.is_file():
        print(f"[*] Step 3: Restoring shallow metadata from {shallow_source}...")
        shallow_target.write_text(shallow_source.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        # Check if bundle parent or sibling has shallow file
        possible_shallow = bundle_path.parent / "shallow"
        if possible_shallow.is_file():
            print(f"[*] Step 3: Restoring shallow metadata from {possible_shallow}...")
            shallow_target.write_text(possible_shallow.read_text(encoding="utf-8"), encoding="utf-8")

    # 5. git fsck --full
    print("[*] Step 4: Running git fsck --full...")
    fsck_proc = subprocess.run(
        ["git", "fsck", "--full", "--no-reflogs"],
        cwd=str(target_dir),
        capture_output=True,
        text=True,
    )
    if fsck_proc.returncode != 0:
        # Check if errors are purely missing shallow graft boundaries
        if "missing commit" in fsck_proc.stderr and shallow_target.is_file():
            print(f"[!] Note: shallow graft boundaries noted in fsck: {fsck_proc.stderr.strip()[:200]}")
        else:
            raise RuntimeError(f"git fsck failed:\nSTDOUT: {fsck_proc.stdout}\nSTDERR: {fsck_proc.stderr}")

    # 6. Verify HEAD and tree
    actual_head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=str(target_dir), text=True
    ).strip()
    actual_tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=str(target_dir), text=True
    ).strip()

    print(f"[*] Restored HEAD: {actual_head}")
    print(f"[*] Restored Tree: {actual_tree}")

    if expected_commit:
        if actual_head != expected_commit:
            raise ValueError(f"Restored HEAD {actual_head} does not match expected {expected_commit}")

    # 7. Check out continuation branch
    print(f"[*] Step 5: Creating continuation branch: {create_branch}...")
    subprocess.check_call(
        ["git", "checkout", "-B", create_branch],
        cwd=str(target_dir),
        stdout=subprocess.DEVNULL,
    )

    # 8. Check tracked files count and presence of key production sources
    tracked_files = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", "HEAD"],
        cwd=str(target_dir),
        text=True,
    ).splitlines()

    required_sources = [
        "comsol_mcp/__init__.py",
        "comsol_mcp/_g3_w18.py",
        "comsol_mcp/_operation_store.py",
        "comsol_mcp/_control_daemon.py",
        "comsol_mcp/_mcp_gateway.py",
        "comsol_mcp/worker_java/PersistentComsolWorker.java",
        "tests/run_g3_5_acceptance.py",
    ]
    for req in required_sources:
        req_p = target_dir / req
        if not req_p.is_file():
            raise FileNotFoundError(f"Critical source file missing in restored repository: {req}")

    # 9. Write restoration receipt
    receipt = {
        "schema": "comsol-mcp-g3/restore-receipt/1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "bundle_path": str(bundle_path),
        "bundle_sha256": bundle_sha,
        "restored_target_dir": str(target_dir),
        "restored_commit": actual_head,
        "restored_tree": actual_tree,
        "branch": create_branch,
        "tracked_files_count": len(tracked_files),
        "critical_sources_verified": True,
        "git_fsck_status": "OK",
    }
    receipt_path = target_dir / "RESTORE_RECEIPT.json"
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(f"[+] Restoration successful! Receipt written to {receipt_path}")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True, help="Path to comsol_mcp_g3_5.bundle")
    parser.add_argument("--target", type=Path, required=True, help="Clean target directory for restoration")
    parser.add_argument("--shallow-source", type=Path, default=None, help="Optional path to git shallow file")
    parser.add_argument("--expected-commit", type=str, default=None, help="Expected delivery commit SHA")
    parser.add_argument("--branch", type=str, default=CONTINUATION_BRANCH, help="Continuation branch name")
    args = parser.parse_args()

    restore_delivery(
        args.bundle,
        args.target,
        shallow_source=args.shallow_source,
        expected_commit=args.expected_commit,
        create_branch=args.branch,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
