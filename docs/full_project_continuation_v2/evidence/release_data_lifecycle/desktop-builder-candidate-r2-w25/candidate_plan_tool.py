#!/usr/bin/env python3
"""Offline validator and command renderer for a candidate artifact plan.

This file has no network or installation implementation. ``--dry-run`` only
prints commands; ``--verify-cache`` hashes already-present files locally.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import stat
import sys
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[4]
PLAN_PATH = HERE / "candidate-plan-r2-w25.json"
BASE_PLAN_PATH = REPO / "docs/full_project_continuation_v2/evidence/release_data_lifecycle/release-delivery-plan-r1/download-plan.json"


class CandidatePlanError(Exception):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _artifact_id(url: str, sha256: str) -> str:
    return hashlib.sha256((url + "\n" + sha256).encode("utf-8")).hexdigest()


def _load() -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        candidate = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
        base = json.loads(BASE_PLAN_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CandidatePlanError(f"plan input cannot be read: {exc}") from exc
    return candidate, base


def validate() -> tuple[dict[str, Any], dict[str, Any]]:
    candidate, base = _load()
    if candidate.get("schema") != "comsol-mcp-desktop-builder-candidate-plan/1":
        raise CandidatePlanError("unsupported candidate plan schema")
    if candidate.get("status") != "CANDIDATE_ONLY_NOT_DOWNLOADED_NOT_INSTALLED_NOT_BUILT_NOT_LOCKED":
        raise CandidatePlanError("candidate status does not match this plan-only tool")

    for binding in candidate["input_binding"].values():
        path = REPO / binding["path"]
        if not path.is_file() or path.is_symlink():
            raise CandidatePlanError(f"bound input is missing or unsafe: {binding['path']}")
        expected_sha = binding.get("sha256")
        if expected_sha is not None and _sha256(path) != expected_sha:
            raise CandidatePlanError(f"bound input hash changed: {binding['path']}")

    artifacts = candidate["unique_download_set"]
    by_id: dict[str, dict[str, Any]] = {}
    by_identity: dict[tuple[str, str], dict[str, Any]] = {}
    for row in artifacts:
        url, digest = row.get("url"), row.get("sha256")
        size = row.get("size_bytes")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise CandidatePlanError("every download row needs an HTTPS URL")
        if (not isinstance(digest, str) or len(digest) != 64 or
                any(ch not in "0123456789abcdef" for ch in digest)):
            raise CandidatePlanError(f"invalid lowercase SHA256 for {row.get('filename')}")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise CandidatePlanError(f"invalid byte count for {row.get('filename')}")
        identity = (url, digest)
        if identity in by_identity:
            raise CandidatePlanError(f"duplicate URL/SHA pair: {row.get('filename')}")
        expected_id = _artifact_id(url, digest)
        if row.get("artifact_id") != expected_id:
            raise CandidatePlanError(f"artifact id mismatch for {row.get('filename')}")
        if expected_id in by_id:
            raise CandidatePlanError("artifact ID collision")
        if row.get("downloaded") is not False:
            raise CandidatePlanError("candidate plan must not report a download")
        if row.get("artifact_kind") == "wheel":
            filename = row.get("filename", "")
            if not filename.endswith(".whl"):
                raise CandidatePlanError(f"wheel row has non-wheel filename: {filename}")
            tail = filename[:-4].split("-")[-3:]
            expanded = {
                f"{py}-{abi}-{platform}"
                for py in tail[0].split(".")
                for abi in tail[1].split(".")
                for platform in tail[2].split(".")
            }
            if expanded != set(row.get("wheel_tags", [])):
                raise CandidatePlanError(f"wheel filename/tag mismatch: {filename}")
        by_identity[identity] = row
        by_id[expected_id] = row

    # The candidate includes the prior locked base/dev artifacts verbatim by
    # URL/SHA/size. This prevents the merged plan from silently omitting rows.
    base_identity = {
        (row["url"], row["sha256"]): row
        for target in base["targets"].values()
        for row in target["artifacts"]
    }
    for identity, row in base_identity.items():
        merged = by_identity.get(identity)
        if merged is None or merged.get("size_bytes") != row.get("size_bytes"):
            raise CandidatePlanError(f"base/dev artifact omitted or changed: {row.get('filename')}")

    for target, closure in candidate["target_closures"].items():
        ids = closure.get("artifacts", [])
        if len(ids) != len(set(ids)):
            raise CandidatePlanError(f"duplicate artifact in target closure {target}")
        try:
            size = sum(by_id[aid]["size_bytes"] for aid in ids)
        except KeyError as exc:
            raise CandidatePlanError(f"unknown artifact in target closure {target}") from exc
        if size != closure.get("download_bytes_if_staged_separately"):
            raise CandidatePlanError(f"closure byte total mismatch for {target}")

    expected_bytes = sum(row["size_bytes"] for row in artifacts)
    totals = candidate["download_totals"]
    if (totals.get("unique_url_sha_pairs") != len(artifacts) or
            totals.get("unique_artifact_bytes") != expected_bytes):
        raise CandidatePlanError("unique download count/byte total mismatch")

    desktop = candidate["desktop_extra_candidate"]
    union_ids = set(desktop["macos_arm64"]["artifact_ids"]) | set(desktop["macos_x86_64"]["artifact_ids"])
    desktop_bytes = sum(by_id[aid]["size_bytes"] for aid in union_ids)
    if (desktop.get("unique_desktop_artifact_count") != len(union_ids) or
            desktop.get("unique_desktop_bytes") != desktop_bytes):
        raise CandidatePlanError("Desktop candidate unique total mismatch")

    return candidate, base


def _render_downloads(candidate: dict[str, Any], cache_root: Path) -> str:
    lines = [
        "# DRY RUN ONLY. These lines are printed, never executed by this tool.",
        "# Fetch once into a new task cache, verify, then stage exact target subsets.",
        f"CACHE_ROOT={shlex.quote(os.fspath(cache_root))}",
        "mkdir -p \"$CACHE_ROOT\"",
    ]
    for row in candidate["unique_download_set"]:
        out = f'"$CACHE_ROOT/{row["filename"]}"'
        url = shlex.quote(row["url"])
        lines.append(f"curl --fail --location --output {out} {url}")
        lines.append(
            "# expect " + row["filename"] +
            f" size={row['size_bytes']} sha256={row['sha256']}"
        )
    lines.append(
        f"python3 {shlex.quote(os.fspath(HERE / 'candidate_plan_tool.py'))} "
        '--verify-cache "$CACHE_ROOT"'
    )
    return "\n".join(lines) + "\n"


def verify_cache(candidate: dict[str, Any], cache_root: Path) -> dict[str, Any]:
    if not cache_root.is_dir() or cache_root.is_symlink():
        raise CandidatePlanError("cache root must be an existing regular directory")
    verified = []
    missing = []
    for row in candidate["unique_download_set"]:
        path = cache_root / row["filename"]
        try:
            info = path.lstat()
        except FileNotFoundError:
            missing.append(row["filename"])
            continue
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise CandidatePlanError(f"cache member is not a regular non-symlink file: {path.name}")
        if info.st_size != row["size_bytes"]:
            raise CandidatePlanError(f"byte count mismatch: {path.name}")
        if _sha256(path) != row["sha256"]:
            raise CandidatePlanError(f"SHA256 mismatch: {path.name}")
        verified.append(path.name)
    if missing:
        raise CandidatePlanError(f"cache incomplete: {len(missing)} planned files missing; first={missing[0]}")
    return {"status": "PASS_LOCAL_HASH_AND_SIZE_ONLY", "verified_count": len(verified),
            "network_accessed": False, "installed": False, "built": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate", action="store_true", help="offline validate plan, source bindings, rows, and totals")
    mode.add_argument("--dry-run", action="store_true", help="print, but never run, the pinned fetch commands")
    mode.add_argument("--verify-cache", metavar="DIR", type=Path, help="hash/size-check a pre-populated local cache")
    parser.add_argument("--cache-root", type=Path, default=Path("/private/tmp/comsol-mcp-candidate-downloads-r1"))
    args = parser.parse_args(argv)
    try:
        candidate, _base = validate()
        if args.validate:
            print(json.dumps({
                "status": "PASS_CANDIDATE_PLAN_OFFLINE_VALIDATION",
                "schema": candidate["schema"],
                "unique_download_count": candidate["download_totals"]["unique_url_sha_pairs"],
                "unique_download_bytes": candidate["download_totals"]["unique_artifact_bytes"],
                "target_bytes": {name: row["download_bytes_if_staged_separately"]
                                 for name, row in candidate["target_closures"].items()},
                "network_accessed": False,
                "installed": False,
                "built": False,
            }, indent=2, sort_keys=True))
        elif args.dry_run:
            print(_render_downloads(candidate, args.cache_root), end="")
        else:
            print(json.dumps(verify_cache(candidate, args.verify_cache), indent=2, sort_keys=True))
        return 0
    except CandidatePlanError as exc:
        print(f"candidate plan error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
