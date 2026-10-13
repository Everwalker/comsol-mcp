#!/usr/bin/env python3
"""Compose a private R5 + exact WFP R4 candidate without editing live sources."""
from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
from datetime import datetime, timezone

REPO = pathlib.Path(__file__).resolve().parents[4]
EVIDENCE = pathlib.Path(__file__).resolve().parent
R5_FREEZE = REPO / "local-preservation/w25-native-adapter-20261012-r5-r3/R5_SOURCE_FREEZE_R3.json"
WFP_FREEZE = REPO / "docs/full_project_continuation_v2/evidence/windows_host_change_wfp_v2_recovery_r4_20261012/SOURCE_FREEZE_V2_R4.json"
WFP_PRESERVATION = REPO / "local-preservation/windows_host_change_wfp_v2_recovery_r4_20261012/PRESERVATION.json"
R5_ROOT = pathlib.Path("/private/tmp/w25-r5-r3-approved-r2-dependencies-20261012")
WFP_ROOT = REPO / "local-preservation/windows_host_change_wfp_v2_recovery_r4_20261012/source"
DEST = pathlib.Path("/private/tmp/w25-r5-r3-wfp-v2-r4-candidate-20261012")
EXPECTED_WFP_FREEZE = "157c16f1c0b7287318c47540759beb0ec705f4a3c83523d9b18761eaf8f0ffc5"
EXPECTED_WFP_PRESERVATION = "c3bcb1d1dcdba60ce1863b0ecd378214df416dd6aed5bfab26c077036f5b4754"


def sha(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    r5 = json.loads(R5_FREEZE.read_text())
    wfp = json.loads(WFP_FREEZE.read_text())
    r5_sha = sha(R5_FREEZE)
    wfp_sha = sha(WFP_FREEZE)
    preservation_sha = sha(WFP_PRESERVATION)
    if wfp_sha != EXPECTED_WFP_FREEZE:
        raise SystemExit(f"WFP R4 freeze changed: {wfp_sha}")
    if preservation_sha != EXPECTED_WFP_PRESERVATION:
        raise SystemExit(f"WFP R4 preservation changed: {preservation_sha}")
    if pathlib.Path(r5["candidate_source_root"]) != R5_ROOT:
        raise SystemExit("unexpected R5 candidate root")
    if DEST.exists() or DEST.is_symlink():
        raise SystemExit(f"no-clobber destination exists: {DEST}")

    for rel, expected in r5["posttest_source_hashes"].items():
        path = R5_ROOT / rel
        if not path.is_file() or sha(path) != expected:
            raise SystemExit(f"R5 source changed: {rel}")
    for row in r5["test_files"]:
        path = R5_ROOT / row["path"]
        if not path.is_file() or sha(path) != row["sha256"]:
            raise SystemExit(f"R5 test changed: {row['path']}")

    shutil.copytree(R5_ROOT, DEST, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"), symlinks=False)
    overlays = []
    for row in wfp["sources"]:
        source = WFP_ROOT / row["path"]
        if not source.is_file() or sha(source) != row["sha256"]:
            raise SystemExit(f"WFP R4 snapshot source mismatch: {row['path']}")
        target = DEST / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if sha(target) != row["sha256"]:
            raise SystemExit(f"WFP R4 overlay mismatch: {row['path']}")
        overlays.append({"path": row["path"], "sha256": sha(target),
                         "size_bytes": target.stat().st_size, "kind": "WFP_R4_SNAPSHOT_SOURCE"})

    for row in wfp["dependencies"]:
        target = DEST / row["path"]
        if not target.is_file() or sha(target) != row["sha256"]:
            source = REPO / row["path"]
            if not source.is_file() or sha(source) != row["sha256"]:
                raise SystemExit(f"exact WFP dependency unavailable: {row['path']}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        if sha(target) != row["sha256"]:
            raise SystemExit(f"WFP dependency overlay mismatch: {row['path']}")
        overlays.append({"path": row["path"], "sha256": sha(target),
                         "size_bytes": target.stat().st_size, "kind": "WFP_R4_DECLARED_DEPENDENCY"})

    files = []
    for path in sorted(DEST.rglob("*")):
        if path.is_symlink():
            raise SystemExit(f"symlink in candidate: {path}")
        if path.is_file():
            files.append({"path": path.relative_to(DEST).as_posix(),
                          "size_bytes": path.stat().st_size, "sha256": sha(path)})
    tree_sha = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    receipt = {
        "schema": "comsol-mcp.w25-r5-wfp-v2-private-candidate/2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "COMPOSED_FROZEN_NOT_TESTED",
        "candidate_root": str(DEST),
        "r5_freeze": {"path": str(R5_FREEZE.relative_to(REPO)), "sha256": r5_sha},
        "r5_candidate_root": str(R5_ROOT),
        "wfp_r4_freeze": {"path": str(WFP_FREEZE.relative_to(REPO)), "sha256": wfp_sha},
        "wfp_r4_preservation": {"path": str(WFP_PRESERVATION.relative_to(REPO)), "sha256": preservation_sha},
        "wfp_source_scope_fingerprint": wfp.get("source_scope_fingerprint"),
        "source_overlays": overlays,
        "source_file_count": len(files),
        "source_files": files,
        "candidate_tree_sha256": tree_sha,
        "tests": {
            "r5_modules": [row["path"] for row in r5["test_files"]],
            "wfp_r4_modules": [row["path"] for row in wfp["sources"] if row["path"].startswith("tests/")],
            "status": "NOT_RUN"
        },
        "limits": ["private exact-source software candidate only",
                   "no live source edits", "no COMSOL, GUI, or solver calls"]
    }
    output = DEST / "evidence" / "COMBINED_CANDIDATE_R2.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"status": receipt["status"], "root": str(DEST),
                      "source_file_count": len(files), "tree_sha256": tree_sha,
                      "r5_freeze_sha256": r5_sha, "wfp_r4_freeze_sha256": wfp_sha,
                      "preservation_sha256": preservation_sha, "receipt_sha256": sha(output)},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
