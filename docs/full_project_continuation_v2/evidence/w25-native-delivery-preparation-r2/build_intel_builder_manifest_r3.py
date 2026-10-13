#!/usr/bin/env python3
"""Build/verify a hash-bound, metadata-only Intel builder input inventory."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tomllib
from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
MANIFEST = HERE / "INTEL_X86_BUILDER_INPUT_MANIFEST_R3.json"
FREEZE = HERE / "INTEL_X86_BUILDER_INPUT_FREEZE_R3.json"
SCRIPT_REL = str(Path(__file__).resolve().relative_to(REPO))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def file_record(rel: str, role: str, source_url: str | None = None) -> dict:
    path = REPO / rel
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"expected regular non-symlink input: {rel}")
    result = {
        "path": rel,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "role": role,
    }
    if source_url:
        result["source_url"] = source_url
    return result


def read_json(rel: str) -> dict:
    return json.loads((REPO / rel).read_text(encoding="utf-8"))


def build_manifest() -> dict:
    rel_root = "docs/full_project_continuation_v2/evidence/w25-native-delivery-preparation-r2"
    cargo_audit_rel = f"{rel_root}/CARGO_REGISTRY_API_AUDIT_R2.json"
    cargo_audit = read_json(cargo_audit_rel)
    rust_audit_rel = f"{rel_root}/RUST_HOST_TARGET_COMPONENT_AUDIT_R2.json"
    rust_audit = read_json(rust_audit_rel)
    py_meta_rel = f"{rel_root}/PYTHON_AND_BUILD_INPUT_METADATA_R2.json"
    py_meta = read_json(py_meta_rel)
    raw_inventory_rel = f"{rel_root}/FIRST_PARTY_METADATA_RAW_INVENTORY_R2.json"
    openssl_release_rel = "docs/full_project_continuation_v2/evidence/release_data_lifecycle/desktop-builder-candidate-r4-w25/openssl-4.0.2-release-api.json"
    openssl_fields_rel = "docs/full_project_continuation_v2/evidence/release_data_lifecycle/desktop-builder-candidate-r4-w25/metadata-used-fields-r4-w25.json"
    r4_plan_rel = "docs/full_project_continuation_v2/evidence/release_data_lifecycle/desktop-builder-candidate-r4-w25/candidate-plan-r4-w25.json"
    r4_freeze_rel = "docs/full_project_continuation_v2/evidence/release_data_lifecycle/desktop-builder-candidate-r4-w25/SOURCE_FREEZE_R4.json"
    r4_review_rel = "docs/full_project_continuation_v2/evidence/release_data_lifecycle/desktop-builder-candidate-r1/root-official-metadata-review-r1/ROOT_METADATA_REVIEW_R1.json"
    pyca_lock_rel = py_meta["cryptography_source"]["locked_cargo_file"]["path"]
    root_header_finding_rel = f"{rel_root}/ROOT_INTEL_HEADER_PLAN_FINDING_R2.json"
    base_download_plan_rel = "docs/full_project_continuation_v2/evidence/release_data_lifecycle/release-delivery-plan-r1/download-plan.json"

    files: dict[str, str] = {}

    def add(rel: str, role: str, url: str | None = None) -> None:
        files[rel] = role if url is None else role + "\nURL=" + url

    # Frozen audits and source/build contract documents.
    for entry in [
        (cargo_audit_rel, "cargo-lock-and-crates-io-audit"),
        (rust_audit_rel, "rust-host-target-component-audit"),
        (py_meta_rel, "python-rust-openssl-build-input-audit"),
        (f"{rel_root}/PY_O3_CROSS_GUIDANCE_R2.md", "pyo3-cross-build-source-analysis"),
        (f"{rel_root}/python-rust-selected-metadata-r2.json", "selected-first-party-http-metadata"),
        (f"{rel_root}/supplemental-first-party-metadata-r2.json", "supplemental-first-party-http-metadata"),
        (raw_inventory_rel, "first-party-raw-response-inventory"),
        (f"{rel_root}/rust/channel-rust-1.83.0.toml", "official-rust-channel-manifest", "https://static.rust-lang.org/dist/channel-rust-1.83.0.toml"),
        (f"{rel_root}/rust/channel-rust-1.83.0.toml.sha256", "official-rust-channel-manifest-sha256", "https://static.rust-lang.org/dist/channel-rust-1.83.0.toml.sha256"),
        (f"{rel_root}/rust/rustup-init-aarch64.sha256", "official-rustup-bootstrap-sha256", "https://static.rust-lang.org/rustup/dist/aarch64-apple-darwin/rustup-init.sha256"),
        (f"{rel_root}/python/cryptography-50.0.1-pypi.json", "pypi-cryptography-50.0.1-json", "https://pypi.org/pypi/cryptography/50.0.1/json"),
        (f"{rel_root}/python/cffi-2.1.1-pypi.json", "pypi-cffi-2.1.1-json", "https://pypi.org/pypi/cffi/2.1.1/json"),
        (f"{rel_root}/python/setuptools-80.9.0-pypi.json", "pypi-setuptools-80.9.0-json", "https://pypi.org/pypi/setuptools/80.9.0/json"),
        (f"{rel_root}/pypi/maturin-1.14.1.json", "pypi-maturin-1.14.1-json", "https://pypi.org/pypi/maturin/1.14.1/json"),
        (f"{rel_root}/python/pyca-cryptography-50.0.1-pyproject.toml", "pinned-upstream-cryptography-pyproject-source"),
        (f"{rel_root}/python/pyca-cryptography-50.0.1-Cargo.toml", "pinned-upstream-cryptography-cargo-workspace-source"),
        (f"{rel_root}/python/pyca-cryptography-50.0.1-src_rust_cryptography-cffi_build.rs", "pinned-upstream-cffi-build-script-source"),
        (f"{rel_root}/python/cpython-v3.12.13-configure.ac", "pinned-official-cpython-configure-source", "https://raw.githubusercontent.com/python/cpython/v3.12.13/configure.ac"),
        (root_header_finding_rel, "root-review-finding-correcting-cpython-header-route"),
        (pyca_lock_rel, "pinned-upstream-cryptography-cargo-lock-source"),
        ("docs/full_project_continuation_v2/evidence/release_data_lifecycle/desktop-builder-candidate-r1/official-metadata-supplement-r1/cryptography-50.0.1-installation.rst", "pinned-upstream-cryptography-installation-doc"),
        ("docs/full_project_continuation_v2/evidence/release_data_lifecycle/desktop-builder-candidate-r1/official-metadata-supplement-r1/cryptography-50.0.1-rust-minimum-excerpt.txt", "pinned-upstream-rust-msrv-source-excerpt"),
        ("docs/full_project_continuation_v2/evidence/release_data_lifecycle/desktop-builder-candidate-r1/official-metadata-supplement-r1/cryptography-50.0.1-source-reference.json", "pinned-upstream-source-reference"),
        ("docs/full_project_continuation_v2/evidence/release_data_lifecycle/desktop-builder-candidate-r1/official-metadata-supplement-r1/cryptography-50.0.1-Cargo.lock", "prior-pinned-upstream-cargo-lock-copy"),
        ("docs/full_project_continuation_v2/evidence/release_data_lifecycle/desktop-builder-candidate-r4-w25/openssl-4.0.2-release-api.json", "official-github-openssl-release-api-raw", "https://api.github.com/repos/openssl/openssl/releases/tags/openssl-4.0.2"),
        (openssl_fields_rel, "official-github-openssl-release-api-used-fields"),
        (r4_plan_rel, "desktop-builder-candidate-plan-r4"),
        (r4_freeze_rel, "desktop-builder-candidate-source-freeze-r4"),
        (r4_review_rel, "root-review-of-upstream-metadata-candidate"),
        ("pyproject.toml", "local-project-dependency-lock-input"),
        ("uv.lock", "local-project-locked-dependency-input"),
        ("docs/full_project_continuation_v2/evidence/w25-native-delivery-preparation-r2/build_r2_evidence.py", "metadata-and-runtime-evidence-builder-script"),
        ("docs/full_project_continuation_v2/evidence/w25-native-delivery-preparation-r2/fetch_pinned_arm64_wheels_r2.py", "approved-arm64-wheel-fetch-script-reference"),
        ("docs/full_project_continuation_v2/evidence/w25-native-delivery-preparation-r2/verify_arm64_runtime_r2.py", "arm64-runtime-verification-script-reference"),
        ("docs/full_project_continuation_v2/evidence/release_data_lifecycle/desktop-builder-candidate-r4-w25/candidate_plan_tool.py", "candidate-plan-validator-script"),
        ("docs/full_project_continuation_v2/evidence/release_data_lifecycle/desktop-builder-candidate-r4-w25/DRY_RUN_COMMANDS.txt", "candidate-plan-dry-run-command-reference"),
        (base_download_plan_rel, "locked-base-dev-python-build-dependency-plan"),
    ]:
        rel, role, *url = entry
        add(rel, role, url[0] if url else None)

    # Bind all 32 raw first-party crates.io responses referenced by the audit.
    for crate in cargo_audit["crates"]:
        add(crate["raw_response"]["path"], f"crates-io-api:{crate['name']}@{crate['version']}", crate["api_url"])

    # Pin exact first-party metadata archive rows for Rust host tools / target std.
    rustup = rust_audit["official_manifest_audit"]["rustup_init"]
    add(rust_audit["official_manifest_audit"]["manifest"]["path"], "official-rust-channel-manifest")
    add(rust_audit["official_manifest_audit"]["manifest"]["official_sha256_sidecar"]["path"], "official-rust-channel-manifest-sha256")
    add(rustup["official_checksum_sidecar"]["path"], "official-rustup-bootstrap-sha256")

    # Include the archived source release evidence used to select CPython source.
    py_release = py_meta["python_release"]["source_release"]
    add(py_release["page"]["path"], "python-org-release-page-body", "https://www.python.org/downloads/release/python-31213/")
    add(py_release["compressed_capture"]["path"], "compressed-python-org-release-page-response", "https://www.python.org/downloads/release/python-31213/")

    # Include current and parent evidence needed to understand acquisition readiness.
    for rel, role in [
        (f"{rel_root}/ARM64_DELIVERY_FREEZE_R2.json", "host-arm64-runtime-preparation-freeze"),
        (f"{rel_root}/ARM64_SITE_FILES_R2.json", "host-arm64-task-runtime-site-inventory"),
        (f"{rel_root}/INTEL_X86_BUILDER_INPUT_MANIFEST_R2.json", "prior-intel-builder-input-manifest-superseded-by-r3-clarification"),
        (f"{rel_root}/INTEL_X86_BUILDER_INPUT_FREEZE_R2.json", "prior-intel-builder-input-freeze-superseded-by-r3-clarification"),
    ]:
        add(rel, role)

    input_records = []
    for rel, role_text in sorted(files.items()):
        role, *urlpart = role_text.split("\nURL=", 1)
        input_records.append(file_record(rel, role, urlpart[0] if urlpart else None))

    crates = []
    for crate in cargo_audit["crates"]:
        name, version = crate["name"], crate["version"]
        raw_crate = read_json(crate["raw_response"]["path"])["version"]
        crates.append({
            "name": name,
            "version": version,
            "lock_checksum_sha256": crate["lock_checksum_sha256"],
            "crates_io_checksum_sha256": crate["crates_io_api_checksum_sha256"],
            "checksum_match": crate["checksum_match"],
            "archive_size_bytes": crate["crate_size_bytes"],
            "archive_url": "https://crates.io" + raw_crate["dl_path"],
            "api_url": crate["api_url"],
            "rust_version": crate["rust_version"],
            "api_response": crate["raw_response"],
        })

    rust_manifest = rust_audit["official_manifest_audit"]["manifest"]
    rust_components = rust_audit["official_manifest_audit"]["components"]
    rustup_info = rust_audit["official_manifest_audit"]["rustup_init"]
    arm_freeze = read_json(f"{rel_root}/ARM64_DELIVERY_FREEZE_R2.json")
    external_inputs = []
    for wheel in arm_freeze["wheelhouse"]["files"]:
        path = Path(wheel["path"])
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"expected pinned wheel file: {path}")
        actual = {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256_file(path), "role": "related-existing-arm64-desktop-gui-runtime-wheel-not-an-intel-builder-prerequisite"}
        if actual["bytes"] != wheel["size_bytes"] or actual["sha256"] != wheel["sha256"]:
            raise RuntimeError(f"wheelhouse identity/hash mismatch: {path}")
        external_inputs.append(actual)
    cpython_config_rel = f"{rel_root}/python/cpython-v3.12.13-configure.ac"
    base_download_plan = read_json(base_download_plan_rel)
    pycparser_row = next(
        item for target in base_download_plan["targets"].values()
        for item in target.get("artifacts", [])
        if item.get("name") == "pycparser" and item.get("version") == "3.0"
    )
    build_backend = py_meta["python_release"]["pypi_build_backend"]
    return {
        "schema": "comsol-mcp.w25-intel-x86-builder-input-manifest/3",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "hash-bound, metadata-only inputs and first-party references for evaluating an arm64-hosted x86_64 macOS CPython 3.12 cryptography 50.0.1 build",
        "status": "OPEN_NOT_BUILD_READY; METADATA_ONLY; NO_INTEL_ARCHIVE_DOWNLOADED_OR_BUILD_RUN",
        "target": {
            "host": "macOS arm64",
            "target_arch": "x86_64-apple-darwin",
            "python_target": "CPython 3.12 ABI3 (cryptography metadata supports CPython>=3.9; release pin under assessment is 3.12)",
            "cryptography": "50.0.1",
            "locked_project_files": ["pyproject.toml", "uv.lock"],
            "sdk_inventory": "macOS SDK 27.0; Apple Clang reports x86_64 code generation; no cross-build executed",
        },
        "inputs": input_records,
        "related_arm64_gui_runtime_context_only": {
            "status": "BOUND_FOR_CONTEXT_ONLY_NOT_INTEL_BUILD_INPUTS",
            "purpose": "These eight files are the previously installed ScreenCaptureKit/GUI task-runtime wheelhouse. They are included only as contextual state and are not Intel compiler/build prerequisites.",
            "files": external_inputs,
        },
        "cargo_registry": {
            "lock_source": file_record(pyca_lock_rel, "pinned-upstream-cargo-lock-source"),
            "registry_crate_count": len(crates),
            "all_lock_checksums_match_first_party_crates_io_api": cargo_audit["all_lock_checksums_match_first_party_crates_io_api"],
            "total_crate_archive_bytes": sum(c["archive_size_bytes"] for c in crates),
            "crates_with_missing_rust_version": [c["name"] + "@" + c["version"] for c in crates if c["rust_version"] is None],
            "crates": crates,
            "downloaded": False,
        },
        "rust_toolchain": {
            "version": rust_audit["official_manifest_audit"]["manifest_channel"],
            "minimum_from_cryptography_source": rust_audit["rust_version_floor_from_cryptography_source"],
            "max_declared_locked_crate_msrv": rust_audit["cargo_locked_crates_max_declared_msrv"],
            "official_channel_manifest": {
                **rust_manifest,
                "first_party_url": rust_manifest["official_url"],
            },
            "host_bootstrap": {
                "target": "aarch64-apple-darwin",
                "url": rustup_info["url"],
                "archive_size_head_bytes": rustup_info["head_content_length_bytes"],
                "archive_sha256_from_official_sidecar": rustup_info["sha256_from_official_sidecar"],
                "sidecar_url": rustup_info["official_checksum_sidecar"]["url"],
                "sidecar_file": rustup_info["official_checksum_sidecar"],
            },
            "components": rust_components,
            "downloaded_or_installed": False,
        },
        "python_and_build": {
            "pinned_source_metadata": py_meta["python_release"]["source_release"]["xz_source"],
            "source_build_required_for_x86_headers": True,
            "target_headers_or_config_built": False,
            "cpython_target_header_route_correction": {
                "status": "UNIVERSAL2_CONFIGURATION_CANDIDATE_NOT_RUN",
                "historical_cross_host_assessment": "The generic --build/--host route recorded in R2 guidance is not valid for Darwin targets: pinned CPython configure.ac cross-build cases reject unknown hosts including x86_64-apple-darwin.",
                "source": file_record(cpython_config_rel, "pinned-official-cpython-configure-source", "https://raw.githubusercontent.com/python/cpython/v3.12.13/configure.ac"),
                "source_findings": [
                    {"lines": "540-566", "finding": "Darwin is not in the recognized cross-host cases; the fallback emits cross build not supported for $host."},
                    {"lines": "601-627", "finding": "The second host-platform cross-build case also rejects unknown hosts."},
                    {"lines": "310-394", "finding": "The source exposes --enable-universalsdk and makes --with-universal-archs valid when that SDK is enabled; universal2 is an accepted architecture value."},
                    {"lines": "2582-2587", "finding": "The universal2 branch sets compiler architectures to arm64 and x86_64."},
                ],
                "candidate": "Use the official CPython 3.12.13 universal2 configuration on the ARM64 host with the pinned installed SDK, then inspect generated headers and compile/link tests for both slices. This is a candidate path, not a validated recipe.",
                "prohibited_shortcut": "Do not set MACHDEP, ac_cv_* answers, target _sysconfigdata, or PYO3_CROSS_LIB_DIR by guess; do not reuse ARM-only pyconfig.h as x86 evidence.",
                "execution": "NOT_RUN",
            },
            "upstream_cryptography_source_files": [
                py_meta["cryptography_source"]["pyproject"],
                py_meta["cryptography_source"]["cargo_workspace"],
                py_meta["cryptography_source"]["cffi_build_rs"],
                py_meta["cryptography_source"]["locked_cargo_file"],
            ],
            "abi3": {
                "features": py_meta["cryptography_source"]["pyo3_workspace_features"],
                "stable_abi_claim": "official PyO3 docs permit abi3 build without target interpreter; does not satisfy cryptography CFFI's target-header requirement",
                "target_python_include_required_by_cffi_build_script": True,
            },
            "build_backend_candidates": build_backend,
            "host_build_dependency_closure": {
                "host_python": "The existing CPython 3.12.13 ARM64 build interpreter (already installed, source path and headers hash-bound above); no x86 interpreter execution is needed for the PyO3 abi3 configuration.",
                "maturin": build_backend["maturin"],
                "cffi": build_backend["cffi"],
                "setuptools": build_backend["setuptools_candidate"],
                "pycparser": {
                    "name": pycparser_row["name"],
                    "version": pycparser_row["version"],
                    "filename": pycparser_row["filename"],
                    "size_bytes": pycparser_row["size_bytes"],
                    "sha256": pycparser_row["sha256"],
                    "url": pycparser_row["url"],
                    "target_tags": pycparser_row["target_tags"],
                    "existing_flat_wheelhouse_state": pycparser_row["local_target_wheelhouse"],
                    "locked_plan_path": base_download_plan_rel,
                    "locked_plan_sha256": sha256_file(REPO / base_download_plan_rel),
                    "acquired_for_task": False,
                },
                "assessment": "Exact pinned host-wheel inputs are identified; pycparser wheel is absent from the flat local wheelhouse and still requires authorized acquisition. Maturin/cffi/setuptools wheels are metadata-pinned here but were not installed as an Intel builder environment.",
            },
        },
        "openssl": py_meta["openssl_target"],
        "official_documents": [
            {"title": "PyO3 v0.29.0 building and distribution", "url": "https://pyo3.rs/v0.29.0/building-and-distribution.html", "local_analysis": file_record(f"{rel_root}/PY_O3_CROSS_GUIDANCE_R2.md", "analysis-of-official-pyo3-cross-build-guidance")},
            {"title": "Maturin distribution and cross-compilation", "url": "https://www.maturin.rs/distribution.html", "local_analysis": "PYTHON_AND_BUILD_INPUT_METADATA_R2.json#maturin_guide"},
            {"title": "CPython 3.12 configure cross-compilation", "url": "https://docs.python.org/3.12/using/configure.html", "local_analysis": "PY_O3_CROSS_GUIDANCE_R2.md"},
            {"title": "CPython v3.12.13 configure.ac", "url": "https://raw.githubusercontent.com/python/cpython/v3.12.13/configure.ac", "pinned_local_source": file_record(cpython_config_rel, "pinned-official-cpython-configure-source")},
            {"title": "cryptography 50.0.1 upstream source", "url": py_meta["cryptography_source"]["source_metadata_url"], "pinned_local_source_inputs": [py_meta["cryptography_source"]["pyproject"], py_meta["cryptography_source"]["cargo_workspace"], py_meta["cryptography_source"]["cffi_build_rs"], py_meta["cryptography_source"]["locked_cargo_file"]]},
            {"title": "crates.io version API", "url_pattern": "https://crates.io/api/v1/crates/{name}/{version}"},
            {"title": "Rust official channel distribution", "url": rust_manifest["official_url"]},
            {"title": "OpenSSL GitHub release API", "url": "https://api.github.com/repos/openssl/openssl/releases/tags/openssl-4.0.2", "archive_url": py_meta["openssl_target"]["canonical_url"]},
        ],
        "scripts": [record for record in input_records if record["path"].endswith(".py") or record["path"].endswith(".txt") and "DRY_RUN" in record["path"]],
        "local_project_lock_state": {
            "pyproject": next(x for x in input_records if x["path"] == "pyproject.toml"),
            "uv_lock": next(x for x in input_records if x["path"] == "uv.lock"),
            "modified_by_this_metadata_only_work": False,
        },
        "local_toolchain_inventory": py_meta["local_toolchain_inventory"],
        "explicit_open_gates": [
            "No Rust/rustup/Cargo/Maturin is installed; exact host and x86 std inputs are metadata-pinned but not acquired.",
            "32 Cargo crate archives are metadata-pinned but not acquired; six have no declared rust_version; source/build compatibility remains untested.",
            "CPython 3.12.13 x86 target headers/config are absent. Generic Darwin --host cross configuration is explicitly rejected by configure.ac; the universal2 header route is a candidate only and remains unrun.",
            "OpenSSL 4.0.2 source is metadata-pinned but not acquired/built for x86_64; local Homebrew OpenSSL dylibs are arm64 only.",
            "macOS SDK target flags, deployment floor, final linked-library closure and Mach-O x86_64 validation remain unverified.",
            "Rosetta executable and receipt are absent; no x86 process or installation prompt was attempted.",
            "Maturin's general PyO3 cross-build support does not establish this macOS cryptography/CFFI build; no wheel built or installed.",
        ],
        "scope_limits": [
            "Metadata-only; no archive or wheel download, package installation, build, x86 process launch, COMSOL launch, GUI effect, source edit, or live lock/version change.",
            "This is an input freeze, not acquisition/build readiness, wheel installability, or release acceptance.",
        ],
    }


def verify_manifest(data: dict) -> list[str]:
    failures = []
    for item in data["inputs"]:
        p = REPO / item["path"]
        if not p.is_file() or p.is_symlink():
            failures.append(f"missing-or-nonregular:{item['path']}")
            continue
        actual_size = p.stat().st_size
        actual_sha = sha256_file(p)
        if actual_size != item["bytes"] or actual_sha != item["sha256"]:
            failures.append(f"input-drift:{item['path']}")
    context = data.get("related_arm64_gui_runtime_context_only", {})
    for item in context.get("files", []):
        p = Path(item["path"])
        if not p.is_file() or p.is_symlink():
            failures.append(f"missing-or-nonregular-external:{item['path']}")
            continue
        if p.stat().st_size != item["bytes"] or sha256_file(p) != item["sha256"]:
            failures.append(f"external-input-drift:{item['path']}")
    cargo = data["cargo_registry"]
    if len(cargo["crates"]) != 32 or not cargo["all_lock_checksums_match_first_party_crates_io_api"]:
        failures.append("cargo-lock-metadata-consistency")
    for crate in cargo["crates"]:
        if crate["lock_checksum_sha256"] != crate["crates_io_checksum_sha256"]:
            failures.append(f"cargo-checksum:{crate['name']}@{crate['version']}")
        raw = read_json(crate["api_response"]["path"])["version"]
        if (raw.get("crate") != crate["name"] or raw.get("num") != crate["version"]
                or raw.get("checksum") != crate["lock_checksum_sha256"]
                or raw.get("crate_size") != crate["archive_size_bytes"]
                or raw.get("rust_version") != crate["rust_version"]):
            failures.append(f"cargo-raw-api-fields:{crate['name']}@{crate['version']}")
    lock = tomllib.loads((REPO / cargo["lock_source"]["path"]).read_text(encoding="utf-8"))
    registry_lock = {
        (package["name"], package["version"]): package.get("checksum")
        for package in lock["package"]
        if package.get("source", "").startswith("registry+")
    }
    for crate in cargo["crates"]:
        if registry_lock.get((crate["name"], crate["version"])) != crate["lock_checksum_sha256"]:
            failures.append(f"cargo-lock-raw:{crate['name']}@{crate['version']}")
    configure_rel = next(
        item["path"] for item in data["inputs"]
        if item["role"] == "pinned-official-cpython-configure-source"
    )
    configure_text = (REPO / configure_rel).read_text(encoding="utf-8")
    for needle in [
        "AC_MSG_ERROR([cross build not supported for $host])",
        '"universal2", "intel-64"',
        'UNIVERSAL_ARCH_FLAGS="-arch arm64 -arch x86_64"',
    ]:
        if needle not in configure_text:
            failures.append("cpython-configure-source-finding-mismatch")
    return failures


def write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        failures = verify_manifest(manifest)
        if failures:
            raise SystemExit("FAIL " + ",".join(failures))
        print(f"PASS inputs={len(manifest['inputs'])} crates={len(manifest['cargo_registry']['crates'])}")
        return 0

    data = build_manifest()
    failures = verify_manifest(data)
    if failures:
        raise SystemExit("FAIL " + ",".join(failures))
    payload = (json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
    write_new(MANIFEST, payload)
    script_path = Path(__file__).resolve()
    script_sha = sha256_file(script_path)
    freeze = {
        "schema": "comsol-mcp.w25-intel-x86-builder-input-freeze/3",
        "status": "FROZEN_METADATA_INPUTS_ONLY; BUILD_NOT_RUN; NOT_BUILD_READY",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "manifest": {
            "path": str(MANIFEST.relative_to(REPO)),
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
        "generator": {"path": SCRIPT_REL, "bytes": script_path.stat().st_size, "sha256": script_sha},
        "verified_input_count": len(data["inputs"]) + len(data.get("related_arm64_gui_runtime_context_only", {}).get("files", [])),
        "cargo_crate_count": len(data["cargo_registry"]["crates"]),
        "live_source_or_lock_modified": False,
        "downloads_installs_builds_or_x86_execution": False,
        "validation": "The generator recomputed every recorded input size/SHA and verified all 32 lock/API crate checksums before writing this freeze.",
    }
    write_new(FREEZE, (json.dumps(freeze, sort_keys=True, indent=2) + "\n").encode())
    print(f"FROZEN inputs={len(data['inputs'])} crates={len(data['cargo_registry']['crates'])} manifest_sha256={freeze['manifest']['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
