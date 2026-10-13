#!/usr/bin/env python3
"""Offline integrity validator for the W25 dependency/observation candidate."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
ARM = json.loads((HERE / "macos-arm64-w25-runtime-candidate-r1.json").read_text())
INTEL = json.loads((HERE / "intel-cryptography-build-closure-r1.json").read_text())
INVENTORY = json.loads((HERE / "host-runtime-and-trust-inventory-r1.json").read_text())
FREEZE_PATH = HERE / "SOURCE_FREEZE_R1.json"
HEX64 = re.compile(r"^[0-9a-f]{64}$")

checks: list[dict[str, object]] = []

def check(name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "status": "PASS" if passed else "FAIL", "detail": detail})
    if not passed:
        raise SystemExit(f"FAIL {name}: {detail}")

rows = ARM["required_runtime_wheel_closure"]
check("arm64-eight-wheel-closure", len(rows) == 8 and sum(r["size_bytes"] for r in rows) == 7_388_559,
      f"count={len(rows)} bytes={sum(r['size_bytes'] for r in rows)}")
check("arm64-unique-identities", len({r["artifact_id"] for r in rows}) == 8 and len({r["url"] for r in rows}) == 8,
      "all artifact IDs and URLs are unique")
check("arm64-pinned-metadata", all(HEX64.fullmatch(r["sha256"]) and r["size_bytes"] > 0 and r["url"].startswith("https://") for r in rows),
      "every selected wheel has a SHA-256, positive byte count and HTTPS URL")
check("macos-api-floor", ARM["os_floor_correction"]["corrected_api_floor"] == "14.0" and "12.3" in ARM["os_floor_correction"]["old_r4_candidate_value"],
      "package platform metadata remains distinct from SCScreenshotManager 14.0 API availability")
check("no-live-dependency-edit", ARM["live_project_state"]["version"] == "0.1.9" and ARM["live_project_state"]["uv_lock_sha256"] == INVENTORY["live_project"]["uv_lock_sha256"],
      "candidate preserves live version and lock identity")
check("intel-open-status", INTEL["status"].startswith("NOT_BUILD_CLOSED") and INTEL["rust_toolchain"]["expected_archive_sha256"] is None,
      "Intel build remains open; no missing Rust archive hash is presented as verified")
crates = INTEL["cargo_lock"]["registry_packages"]
check("cargo-lock-rows", len(crates) == INTEL["cargo_lock"]["registry_package_count"] and all(HEX64.fullmatch(r["sha256_from_locked_Cargo_lock"]) and r["size_bytes"] is None for r in crates),
      f"{len(crates)} locked crates retain checksums while unavailable sizes remain explicit nulls")
check("private-profile-evidence", any(x["observed"] == "PASS" for x in INVENTORY["trusted_profile_locations"]) and any("REFUSED" == x["observed"] for x in INVENTORY["trusted_profile_locations"]),
      "only the current task temp root passed; unsafe/shared locations are refused")
check("native-observation-not-claimed", any("No native GUI" in s for s in ARM["limitations"]) and ARM["status"] == "SCOPED_METADATA_AND_LOCAL_INVENTORY_ONLY",
      "candidate makes no native GUI or COMSOL runtime claim")

freeze = json.loads(FREEZE_PATH.read_text())
source_errors = []
for item in freeze["input_bindings"]:
    path = REPO / item["path"]
    if not path.is_file():
        source_errors.append(f"missing {item['path']}")
        continue
    raw = path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != item["sha256"] or len(raw) != item["size_bytes"]:
        source_errors.append(f"changed {item['path']}")
check("source-input-bindings", not source_errors, "all bound input hashes and sizes match" if not source_errors else "; ".join(source_errors))

print(json.dumps({"schema":"w25-delivery-plan-validation/1","status":"PASS_OFFLINE_METADATA_AND_BINDING_ONLY","checks":checks,"network_used":False,"downloads":0,"installs":0,"gui_or_comsol_started":False}, sort_keys=True, indent=2))
