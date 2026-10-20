"""Persistent exact R9 source bytes for historical software tests only."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools import w26_application_bundle as app


# Reuse the already frozen workspace-persistent R17 fixture as an explicit
# immutable input.  R18 did not copy or rebind those bytes, so its recorded
# file paths/first-records remain truthful.  This is source-software test data
# only; it is not an R18 App producer, approval, once, or native receipt.
PACKET = Path(__file__).resolve().parents[1]
PERSISTENT_INPUT_PACKET = (PACKET / "_state/w26-r17-current175-r12-runtime-input-bridge")
FIXTURE_ROOT = PERSISTENT_INPUT_PACKET / "inputs/r9-persistent-source-fixture-r17"
FIXTURE_MANIFEST = PERSISTENT_INPUT_PACKET / "evidence/R9_PERSISTENT_SOURCE_FIXTURE_R17_R2.json"
FIXTURE_MANIFEST_SHA256 = "ecbc6125baef3e5294d563adee8a9fc7749d4ac3b395db32d6106c4feaecaa0d"
R9_LOCK = Path(app.__file__).with_name("w26_application_sources_175_r9_prepared.lock.json")
R9_LOCK_SHA256 = "a524e533f01ed5e5ea0f06cfa5caf49ab0ddf50474258b672265c13f62f987a2"
R9_MEMBER_FINGERPRINT = "401d720192bba5456c8c576f9ed845a74e0555b9c8d4a5e9fa04dcb28d89ef26"
R9_EXTRA_HASHES = {
    "uv_lock": (279458, "2058044231f3ace0a36b537a12b041579af94b9042add9acfd18ca0c0d7505c1"),
    "operations": (14146, "03e2ca02a88e2c58cc496020e8966798b5dac3ba46fa35e5af13e5d56cd61345"),
}


def saved_r9_source_manifest() -> dict:
    """Read the persistent byte manifest without referring to old tmp receipts."""
    helper = app._helpers()
    raw, record = helper.raw_file(FIXTURE_MANIFEST)
    if record["sha256"] != FIXTURE_MANIFEST_SHA256:
        raise RuntimeError("persistent R9 source fixture manifest digest differs")
    value = json.loads(raw)
    if (value.get("schema") != "w26-r17-persistent-current175-r9-source-fixture/2"
            or value.get("scope") != (
                "exact frozen R9 source and extras bytes for historical source-software tests only; "
                "no App producer, once, Main approval, old inode, or native evidence recreated")
            or value.get("historical_r9_lock_sha256") != R9_LOCK_SHA256
            or value.get("member_fingerprint_sha256") != R9_MEMBER_FINGERPRINT
            or value.get("member_count") != 175
            or value.get("package_count") != 172
            or value.get("root_count") != 3):
        raise RuntimeError("persistent R9 source fixture envelope differs")
    lock_raw, lock_record = helper.raw_file(R9_LOCK)
    if (lock_record["sha256"] != R9_LOCK_SHA256
            or value.get("historical_r9_lock_sha256") != lock_record["sha256"]):
        raise RuntimeError("R9 source lock differs from the persistent fixture envelope")
    lock = json.loads(lock_raw)
    if (lock.get("profile") != "current175-r9"
            or app._canonical_source_member_map(lock.get("members", {})) != R9_MEMBER_FINGERPRINT
            or set(x.get("relative_path") for x in value.get("members", [])) != set(lock["members"])):
        raise RuntimeError("persistent fixture member map differs from the frozen R9 lock")
    return value


def saved_r9_source_fixture() -> tuple[Path, dict[str, Path]]:
    """Return exact persistent source bytes and extras for R9 source tests.

    The returned tree is a newly materialized, workspace-persistent source
    fixture. It is not the absent App producer tree and does not claim its old
    binding, once observation, inode identity, Main approval, or native result.
    """
    helper = app._helpers()
    manifest = saved_r9_source_manifest()
    source_root = FIXTURE_ROOT / "source-input"
    origin_root = FIXTURE_ROOT / "source-origin"
    if (Path(manifest["source_input"]) != source_root
            or Path(manifest["source_origin"]) != origin_root):
        raise RuntimeError("persistent R9 fixture source paths differ")
    source_members = app.source_members(source_root)
    origin_members = app.source_members(origin_root)
    expected_rows = {row["relative_path"]: row["expected"] for row in manifest["members"]}
    if set(source_members) != set(expected_rows) or set(origin_members) != set(expected_rows):
        raise RuntimeError("persistent R9 exact175 source fixture membership differs")

    source_records = {name: helper.raw_file(path)[1] for name, path in source_members.items()}
    origin_records = {name: helper.raw_file(path)[1] for name, path in origin_members.items()}
    rows_by_path = {row["relative_path"]: row for row in manifest["members"]}
    record_keys = ("path", "bytes", "sha256", "dev", "inode", "mode", "nlink")
    for relative, expected in expected_rows.items():
        for name, row in (("source_input", source_records[relative]),
                          ("source_origin", origin_records[relative])):
            if {key: row[key] for key in ("bytes", "sha256")} != expected:
                raise RuntimeError("persistent R9 source fixture file differs from old lock: " + relative)
            if ({key: row[key] for key in record_keys}
                    != rows_by_path[relative]["copies"][name]
                    or rows_by_path[relative]["historical_inode_reused"] is not False):
                raise RuntimeError("persistent R9 source fixture record differs from current R17 path: " + relative)
    source_lock_record = helper.raw_file(R9_LOCK)[1]
    source = {
        "source_profile": "current175-r9",
        "source_lock": source_lock_record,
        "original_source": str(source_root),
        "snapshot_source": str(source_root),
        "snapshot_members": source_records,
        "origin_provenance_snapshot": {
            "schema": "w26-source-origin-provenance-snapshot/1",
            "original_source_path": str(source_root),
            "original_members": source_records,
            "snapshot_source": str(origin_root),
            "snapshot_members": origin_records,
            "member_fingerprint_sha256": R9_MEMBER_FINGERPRINT,
        },
    }
    verified = app.verify_source_input_r9(source, require_live_origin=False)
    if verified != {
            "source_snapshot_member_count": 175,
            "source_snapshot_fingerprint_sha256": R9_MEMBER_FINGERPRINT,
            "origin_snapshot_member_count": 175,
            "origin_snapshot_fingerprint_sha256": R9_MEMBER_FINGERPRINT,
    }:
        raise RuntimeError("persistent R9 source snapshots failed old-reader exact-source validation")

    extra_paths: dict[str, Path] = {}
    for key, (size, digest) in R9_EXTRA_HASHES.items():
        filename = "uv.lock" if key == "uv_lock" else "RELEASE_OPERATIONS.md"
        row = manifest.get("extras", {}).get(key, {})
        path = Path(row.get("persistent_path", ""))
        raw, observed = helper.raw_file(path)
        if (len(raw), hashlib.sha256(raw).hexdigest()) != (size, digest):
            raise RuntimeError("persistent R9 exact extra input differs: " + filename)
        if (observed["path"] != row.get("persistent_path") or observed["sha256"] != row.get("sha256")
                or {key: observed[key] for key in record_keys} != row.get("copy_record")):
            raise RuntimeError("persistent R9 extra input observation differs: " + filename)
        extra_paths[key] = path
    return source_root, extra_paths
