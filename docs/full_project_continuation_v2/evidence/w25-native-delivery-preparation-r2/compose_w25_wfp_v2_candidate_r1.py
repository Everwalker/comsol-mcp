#!/usr/bin/env python3
"""Compose a private exact-source candidate; does not run tests or touch live sources."""
from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
from datetime import datetime, timezone


REPO = pathlib.Path(__file__).resolve().parents[4]
R5_FREEZE = REPO / "local-preservation/w25-native-adapter-20261012-r5-r3/R5_SOURCE_FREEZE_R3.json"
WFP_FREEZE = REPO / "docs/full_project_continuation_v2/evidence/windows_host_change_wfp_r3_20261012/SOURCE_FREEZE_V2_R3.json"
WFP_PRESERVATION = REPO / "local-preservation/windows_host_change_wfp_v2_r3_20261012/PRESERVATION.json"
R5_COMPOSED_ROOT = pathlib.Path("/private/tmp/w25-r5-r3-approved-r2-dependencies-20261012")
WFP_SNAPSHOT_ROOT = REPO / "local-preservation/windows_host_change_wfp_v2_r3_20261012/source"
DEST = pathlib.Path("/private/tmp/w25-r5-r3-wfp-v2-r3-candidate-20261012")


def sha(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    r5 = json.loads(R5_FREEZE.read_text())
    wfp = json.loads(WFP_FREEZE.read_text())
    preservation = json.loads(WFP_PRESERVATION.read_text())
    wfp_sha = sha(WFP_FREEZE)
    preservation_sha = sha(WFP_PRESERVATION)
    if wfp_sha != "618648c68d76bb1a3d2204d41adcdc12631aceb3b642bc6c666425e2e4fa8727":
        raise SystemExit(f"WFP freeze changed: {wfp_sha}")
    if preservation_sha != "949f3094ef7e5b66ccb3571a8a5c4b69df9355e76425d34d27d4f123887eb6f5":
        raise SystemExit(f"WFP preservation manifest changed: {preservation_sha}")
    if DEST.exists() or DEST.is_symlink():
        raise SystemExit(f"no-clobber candidate destination exists: {DEST}")

    r5_root = pathlib.Path(r5["candidate_source_root"])
    if r5_root != R5_COMPOSED_ROOT:
        raise SystemExit(f"unexpected R5 candidate root: {r5_root}")
    for rel, expected in r5["posttest_source_hashes"].items():
        path = r5_root / rel
        if not path.is_file() or sha(path) != expected:
            raise SystemExit(f"R5 posttest source mismatch: {rel}")
    for row in r5["test_files"]:
        path = r5_root / row["path"]
        if not path.is_file() or sha(path) != row["sha256"]:
            raise SystemExit(f"R5 test input mismatch: {row['path']}")

    shutil.copytree(
        r5_root,
        DEST,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
        symlinks=False,
    )

    overlays = []
    # Overlay every file belonging to the exact WFP V2 frozen source set.
    for row in wfp["sources"]:
        source = WFP_SNAPSHOT_ROOT / row["path"]
        if not source.is_file() or sha(source) != row["sha256"]:
            raise SystemExit(f"WFP frozen source mismatch: {row['path']}")
        destination = DEST / row["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        overlays.append({"path": row["path"], "source": str(source),
                         "sha256": sha(destination), "size_bytes": destination.stat().st_size,
                         "kind": "WFP_V2_FROZEN_SOURCE"})

    # The WFP freeze names four exact shared dependencies. Resolve each by a
    # hash-pinned copy; the currently present copy is accepted only on a match.
    for row in wfp["dependencies"]:
        destination = DEST / row["path"]
        if sha(destination) != row["sha256"]:
            # This is an immutable dependency present in the stable live tree,
            # not an unpinned implementation choice. Hash is checked before and
            # after copy and the live file is never edited.
            source = REPO / row["path"]
            if not source.is_file() or sha(source) != row["sha256"]:
                raise SystemExit(f"WFP declared dependency unavailable at exact SHA: {row['path']}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        if sha(destination) != row["sha256"]:
            raise SystemExit(f"WFP dependency copy mismatch: {row['path']}")
        overlays.append({"path": row["path"], "source": str(REPO / row["path"]),
                         "sha256": sha(destination), "size_bytes": destination.stat().st_size,
                         "kind": "WFP_V2_DECLARED_DEPENDENCY"})

    actual = []
    for path in sorted(DEST.rglob("*")):
        if path.is_symlink():
            raise SystemExit(f"candidate contains symlink: {path}")
        if path.is_file():
            actual.append({"path": path.relative_to(DEST).as_posix(),
                           "size_bytes": path.stat().st_size, "sha256": sha(path)})
    receipt = {
        "schema": "comsol-mcp.w25-r5-wfp-v2-private-candidate/1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "COMPOSED_NOT_TESTED_WAITING_FOR_WFP_R4_RECOVERY_FIX",
        "candidate_root": str(DEST),
        "r5_freeze": {"path": str(R5_FREEZE.relative_to(REPO)), "sha256": sha(R5_FREEZE)},
        "r5_candidate_root": str(r5_root),
        "wfp_v2_freeze": {"path": str(WFP_FREEZE.relative_to(REPO)), "sha256": wfp_sha},
        "wfp_preservation_manifest": {"path": str(WFP_PRESERVATION.relative_to(REPO)), "sha256": preservation_sha},
        "wfp_scope_fingerprint": wfp.get("source_scope_fingerprint"),
        "source_overlays": overlays,
        "source_file_count": len(actual),
        "source_files": actual,
        "candidate_tree_sha256": hashlib.sha256(json.dumps(actual, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "test_status": "NOT_RUN; parent directed deferral until Windows freezes the narrow V2 recovery fix",
        "limits": ["private software candidate only", "no live source edits", "no COMSOL, GUI, or solver calls"],
    }
    out = DEST / "evidence" / "COMBINED_CANDIDATE_R1.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"status": receipt["status"], "root": str(DEST),
                      "file_count": len(actual), "tree_sha256": receipt["candidate_tree_sha256"],
                      "r5_freeze_sha256": receipt["r5_freeze"]["sha256"],
                      "wfp_freeze_sha256": wfp_sha, "receipt_sha256": sha(out)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
