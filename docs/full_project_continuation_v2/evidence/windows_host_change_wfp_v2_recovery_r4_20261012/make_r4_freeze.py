from __future__ import annotations

import difflib
import hashlib
import json
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha(path: Path) -> str:
    return sha256(path.read_bytes())


def junit_counts(path: Path) -> dict[str, int]:
    root = ET.parse(path).getroot()
    suites = [node for node in root.iter("testsuite")]
    counts = {key: sum(int(suite.attrib.get(key, 0)) for suite in suites)
              for key in ("tests", "failures", "errors", "skipped")}
    counts["passed"] = counts["tests"] - counts["failures"] - counts["errors"] - counts["skipped"]
    return counts


def guard_counts(path: Path) -> dict[str, int]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return {"events": len(rows),
            "active_events": sum(row.get("event") == "GUARD_ACTIVE_BEFORE_CHILD_ENTRYPOINT"
                                 for row in rows),
            "deny_events": sum("DENY" in str(row.get("event", "")).upper()
                               or "BLOCK" in str(row.get("event", "")).upper()
                               for row in rows)}


def main(repository: Path) -> None:
    r3_path = repository / "docs/full_project_continuation_v2/evidence/windows_host_change_wfp_r3_20261012/SOURCE_FREEZE_V2_R3.json"
    evidence_dir = repository / "docs/full_project_continuation_v2/evidence/windows_host_change_wfp_v2_recovery_r4_20261012"
    preservation_dir = repository / "local-preservation/windows_host_change_wfp_v2_recovery_r4_20261012"
    r3 = json.loads(r3_path.read_text())
    allowed_delta = {"comsol_mcp/_operation_store.py", "tests/test_windows_host_change.py"}
    sources: list[dict] = []
    patch_parts: list[str] = []

    for old in r3["sources"]:
        source = repository / old["path"]
        raw = source.read_bytes()
        digest = sha256(raw)
        changed = digest != old["sha256"]
        if changed != (old["path"] in allowed_delta):
            raise RuntimeError(f"unexpected R3 source drift: {old['path']}")
        snapshot_rel = Path("local-preservation/windows_host_change_wfp_v2_recovery_r4_20261012/source") / old["path"]
        snapshot = repository / snapshot_rel
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(raw)
        if file_sha(snapshot) != digest:
            raise RuntimeError(f"snapshot mismatch: {old['path']}")
        sources.append({
            "path": old["path"], "sha256": digest, "size_bytes": len(raw),
            "preimage_r3_sha256": old["sha256"], "changed_since_r3": changed,
            "snapshot_path": snapshot_rel.as_posix(),
        })
        if changed:
            old_snapshot = repository / old["snapshot_path"]
            old_lines = old_snapshot.read_text(encoding="utf-8").splitlines(keepends=True)
            new_lines = raw.decode("utf-8").splitlines(keepends=True)
            patch_parts.extend(difflib.unified_diff(
                old_lines, new_lines,
                fromfile="a/" + old["path"], tofile="b/" + old["path"],
                n=3,
            ))

    if {row["path"] for row in sources if row["changed_since_r3"]} != allowed_delta:
        raise RuntimeError("R4 delta does not match the approved two-file narrow scope")

    for dependency in r3["dependencies"]:
        path = repository / dependency["path"]
        if file_sha(path) != dependency["sha256"]:
            raise RuntimeError(f"dependency drift during R4 freeze: {dependency['path']}")

    patch_path = evidence_dir / "RECOVERY_ABSORBING_STATE_R4.patch"
    patch_path.write_text("".join(patch_parts), encoding="utf-8")

    preimage_path = repository / "local-preservation/windows_host_change_wfp_v2_recovery_r4_preimage_20261012/PRESERVATION.json"
    preimage_sha = file_sha(preimage_path)
    preservation = {
        "schema": "comsol-mcp-source-preservation/1",
        "captured_at_utc": "2026-10-12",
        "purpose": "Full 10-source V2 WFP candidate after the narrow RECOVERY_REQUIRED absorbing-state correction",
        "parent_freeze": r3_path.relative_to(repository).as_posix(),
        "parent_freeze_sha256": file_sha(r3_path),
        "files": [{"path": row["path"], "snapshot_path": row["snapshot_path"],
                   "sha256": row["sha256"], "size_bytes": row["size_bytes"]}
                  for row in sources],
        "scope_fingerprint": sha256(canonical([(row["path"], row["sha256"])
                                                for row in sources]).encode("utf-8")),
        "classification": "SOFTWARE_SOURCE_PRESERVATION_ONLY",
    }
    preservation_path = preservation_dir / "PRESERVATION.json"
    preservation_path.parent.mkdir(parents=True, exist_ok=True)
    preservation_path.write_text(json.dumps(preservation, sort_keys=True, indent=2) + "\n",
                                 encoding="utf-8")

    focused_junit = evidence_dir / "junit_focused001.xml"
    affected_junit = evidence_dir / "junit_affected001.xml"
    focused_guard = evidence_dir / "strict_guard_focused001.jsonl"
    affected_guard = evidence_dir / "strict_guard_affected001.jsonl"
    repro_dir = repository / "docs/full_project_continuation_v2/evidence/windows_host_change_wfp_v2_recovery_repro_20261012"
    artifacts = []
    for path in (focused_junit, affected_junit, focused_guard, affected_guard,
                 repro_dir / "REPRODUCTION_RECEIPT.json",
                 repro_dir / "repro_result_attempt003.json",
                 repro_dir / "network-guard.jsonl",
                 repro_dir / "repro_store_recovery_state.py",
                 evidence_dir / "make_r4_freeze.py"):
        artifacts.append({"path": path.relative_to(repository).as_posix(),
                          "sha256": file_sha(path), "size_bytes": path.stat().st_size})

    git_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repository,
                              check=True, capture_output=True, text=True).stdout.strip()
    scope_fingerprint = sha256(canonical([(row["path"], row["sha256"])
                                          for row in sources]).encode("utf-8"))
    freeze = {
        "schema": "comsol-mcp.windows-host-change-wfp-v2-recovery-r4-freeze/1",
        "captured_at_utc": "2026-10-12",
        "base_commit": r3.get("current_git_head"),
        "current_git_head": git_head,
        "scope": "R3 V2 WFP apply/restore plus RECOVERY_REQUIRED absorbing-state correction; software only.",
        "source_scope_fingerprint": scope_fingerprint,
        "sources": sources,
        "dependencies": r3["dependencies"],
        "preimage_manifest": preimage_path.relative_to(repository).as_posix(),
        "preimage_manifest_sha256": preimage_sha,
        "previous_freeze": r3_path.relative_to(repository).as_posix(),
        "previous_freeze_sha256": file_sha(r3_path),
        "preservation_manifest": preservation_path.relative_to(repository).as_posix(),
        "preservation_manifest_sha256": file_sha(preservation_path),
        "diff_artifact": {"path": patch_path.relative_to(repository).as_posix(),
                           "sha256": file_sha(patch_path), "size_bytes": patch_path.stat().st_size},
        "checks": [
            {"name": "focused APPLY/RESTORE recovery fence controls",
             "invocation": "pytest tests/test_windows_host_change.py -k 'v2_apply_recovery_required_is_absorbing_if_unknown_finish_write_fails or v2_restore_recovery_required_is_absorbing_if_unknown_finish_write_fails' -q",
             "result": junit_counts(focused_junit),
             "guard": guard_counts(focused_guard)},
            {"name": "affected V2 WFP/Store regression",
             "invocation": "pytest tests/test_windows_host_change.py tests/test_windows_t037_native.py tests/test_operation_store.py tests/test_operation_store_migrations.py -k 'not historical_fixture_commit_and_blob_identity_resolve' -q --disable-warnings --maxfail=1",
             "result": junit_counts(affected_junit),
             "guard": guard_counts(affected_guard)},
            {"name": "py_compile",
             "result": "passed for comsol_mcp/_operation_store.py and tests/test_windows_host_change.py"},
            {"name": "diff_check", "result": "passed"},
        ],
        "artifacts": artifacts,
        "runtime": {"python_path": "/private/tmp/comsol-v2-runtime-20261011-new/comsol-mcp-recovery-runtime-20261004-001/python312/bin/python",
                    "python_version": "3.12.13"},
        "platform_status": {"software_store_state_machine": "SCOPED_PASS",
                             "windows_kernel": "NOT_RUN",
                             "windows_os_mutation": "NOT_RUN",
                             "effective_wfp_arbitration": "UNVERIFIED"},
        "limitations": [
            "The hash-consistent suffix negative control inserts a deliberately crafted journal row with raw test SQL; it proves production replay refusal, not database tamper resistance.",
            "The fake WFP adapter and native Windows kernel were not run; the tests establish Store/executor protocol behavior only.",
            "The inherited historical Git-object fixture remains deselected and its original missing-object failure is preserved in the R3 packet."
        ],
    }
    freeze_path = evidence_dir / "SOURCE_FREEZE_V2_R4.json"
    freeze_path.write_text(json.dumps(freeze, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"freeze": freeze_path.relative_to(repository).as_posix(),
                      "freeze_sha256": file_sha(freeze_path),
                      "preservation": preservation_path.relative_to(repository).as_posix(),
                      "preservation_sha256": file_sha(preservation_path),
                      "source_scope_fingerprint": scope_fingerprint,
                      "source_count": len(sources),
                      "changed_paths": sorted(allowed_delta)}, sort_keys=True, indent=2))


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
