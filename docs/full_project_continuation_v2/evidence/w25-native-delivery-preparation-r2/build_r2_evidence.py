#!/usr/bin/env python3
"""Offline integrity/audit generator for W25 cross-build candidate R2."""
from __future__ import annotations

import gzip
import hashlib
import json
import platform
import re
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
EVIDENCE = ROOT / "docs/full_project_continuation_v2/evidence/w25-native-delivery-preparation-r2"
RELEASE_R1 = ROOT / "docs/full_project_continuation_v2/evidence/w25-native-delivery-preparation-r1"
BUILDER_R1 = ROOT / "docs/full_project_continuation_v2/evidence/release_data_lifecycle/desktop-builder-candidate-r1/official-metadata-supplement-r1"
BUILDER_R4 = ROOT / "docs/full_project_continuation_v2/evidence/release_data_lifecycle/desktop-builder-candidate-r4-w25"
TASK = Path("/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T/comsol-w25-arm64-runtime-r2-37bd820ee138424b8946684619d5bb24")
PYTHON = Path("/private/tmp/comsol-v2-runtime-20261011-new/comsol-mcp-recovery-runtime-20261004-001/python312/bin/python3")


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def stamp(path: Path) -> dict[str, object]:
    return {"path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
            "bytes": path.stat().st_size, "sha256": sha(path)}


def rust_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", value))


def main() -> None:
    cargo_lock = BUILDER_R1 / "cryptography-50.0.1-Cargo.lock"
    lock = tomllib.loads(cargo_lock.read_text(encoding="utf-8"))
    registry = [p for p in lock["package"] if str(p.get("source", "")).startswith("registry+")]
    assert len(registry) == 32, f"registry crate count drifted: {len(registry)}"
    crate_rows = []
    for package in sorted(registry, key=lambda p: (p["name"], p["version"])):
        name, version = package["name"], package["version"]
        raw_path = EVIDENCE / "cratesio" / f"{name}-{version}.json"
        response = load(raw_path)["version"]
        assert response["crate"] == name and response["num"] == version
        matches = response["checksum"] == package["checksum"]
        assert matches, f"Cargo.lock checksum mismatch: {name} {version}"
        crate_rows.append({
            "name": name, "version": version,
            "lock_checksum_sha256": package["checksum"],
            "crates_io_api_checksum_sha256": response["checksum"],
            "checksum_match": matches,
            "crate_size_bytes": response.get("crate_size"),
            "rust_version": response.get("rust_version"),
            "api_url": f"https://crates.io/api/v1/crates/{name}/{version}",
            "raw_response": stamp(raw_path),
        })
    known_msrv = [row["rust_version"] for row in crate_rows if row["rust_version"]]
    max_msrv = max(known_msrv, key=rust_tuple)
    null_msrv = [row["name"] for row in crate_rows if row["rust_version"] is None]
    (EVIDENCE / "CARGO_REGISTRY_API_AUDIT_R2.json").write_text(json.dumps({
        "schema": "comsol-mcp.w25-cargo-registry-api-audit/2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_lock": stamp(cargo_lock),
        "registry_crate_count": len(crate_rows),
        "all_lock_checksums_match_first_party_crates_io_api": all(x["checksum_match"] for x in crate_rows),
        "known_declared_msrv_max": max_msrv,
        "known_declared_msrv_max_crates": [x["name"] for x in crate_rows if x["rust_version"] and rust_tuple(x["rust_version"]) == rust_tuple(max_msrv)],
        "rust_version_not_declared_in_api": null_msrv,
        "no_declared_rust_version_is_not_proof_of_compatibility": True,
        "crates": crate_rows,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    manifest = EVIDENCE / "rust/channel-rust-1.83.0.toml"
    manifest_sha_file = EVIDENCE / "rust/channel-rust-1.83.0.toml.sha256"
    manifest_doc = tomllib.loads(manifest.read_text(encoding="utf-8"))
    sidecar_sha = manifest_sha_file.read_text(encoding="ascii").split()[0]
    assert sidecar_sha == sha(manifest)
    selected = load(EVIDENCE / "python-rust-selected-metadata-r2.json")
    component_rows = []
    for component, target in [
        ("rustc", "aarch64-apple-darwin"),
        ("cargo", "aarch64-apple-darwin"),
        ("rust-std", "aarch64-apple-darwin"),
        ("rust-std", "x86_64-apple-darwin"),
    ]:
        first_party = manifest_doc["pkg"][component]["target"][target]
        observation = next(x for x in selected["selected_rust_components"] if x["component"] == component and x["target"] == target)
        assert first_party["xz_hash"] == observation["sha256"]
        component_rows.append({
            "component": component, "target": target,
            "url": first_party["xz_url"], "sha256": first_party["xz_hash"],
            "head_content_length_bytes": int(observation["size_head"]),
            "etag": observation.get("etag"), "last_modified": observation.get("last_modified"),
            "metadata_source": "official Rust 1.83.0 channel manifest + prior metadata-only HEAD",
        })
    raw_inventory = load(EVIDENCE / "FIRST_PARTY_METADATA_RAW_INVENTORY_R2.json")
    rustup_url = "https://static.rust-lang.org/rustup/dist/aarch64-apple-darwin/rustup-init"
    rustup_head = raw_inventory["rust"]["manifest"]["bootstrap"][rustup_url]
    rustup_sidecar = raw_inventory["rust"]["manifest"]["bootstrap"][rustup_url + ".sha256"]
    rustup_hash = (EVIDENCE / "rust/rustup-init-aarch64.sha256").read_text(encoding="ascii").split()[0]
    assert rustup_hash in rustup_sidecar["body"]
    rust_info = {
        "manifest_date": manifest_doc["date"],
        "manifest_channel": raw_inventory["rust"]["manifest"]["channel"],
        "manifest": {**stamp(manifest), "official_url": "https://static.rust-lang.org/dist/channel-rust-1.83.0.toml",
                     "official_sha256_sidecar": stamp(manifest_sha_file), "sidecar_payload_sha256": sidecar_sha},
        "rustup_init": {"url": rustup_url, "sha256_from_official_sidecar": rustup_hash,
                        "head_content_length_bytes": int(rustup_head["headers"]["Content-Length"]),
                        "official_checksum_sidecar": {**stamp(EVIDENCE / "rust/rustup-init-aarch64.sha256"),
                                                       "url": rustup_url + ".sha256",
                                                       "response_sha256": digest_bytes(rustup_sidecar["body"].encode("utf-8")),
                                                       "response_bytes": len(rustup_sidecar["body"].encode("utf-8"))}},
        "components": component_rows,
    }
    (EVIDENCE / "RUST_HOST_TARGET_COMPONENT_AUDIT_R2.json").write_text(json.dumps({
        "schema": "comsol-mcp.w25-rust-cross-components/2", "created_utc": datetime.now(timezone.utc).isoformat(),
        "official_manifest_audit": rust_info,
        "rust_version_floor_from_cryptography_source": "1.83.0",
        "cargo_locked_crates_max_declared_msrv": max_msrv,
        "msrv_assessment": "All non-null crates.io rust_version metadata is <= 1.83.0. Several crates do not declare rust_version; source/compilation compatibility remains to be verified by a build.",
        "rust_build_execution": "NOT_RUN",
        "archive_downloads": 0,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    release_html = EVIDENCE / "python/python-3.12.13-release.html"
    release_gzip = EVIDENCE / "python/python-3.12.13-release.html.gz"
    assert gzip.decompress(release_gzip.read_bytes()) == release_html.read_bytes()
    html = release_html.read_text(encoding="utf-8")
    py_archive_sha = re.sub(r"\s+", "", "c08bc65a81971c1d d578318282650336 9466c7e67374d164 6519adf05207b684")
    assert py_archive_sha in re.sub(r"<[^>]*>", "", html).replace("\u200b", "").replace("\xa0", " ").replace(" ", "")
    pyproject = tomllib.loads((EVIDENCE / "python/pyca-cryptography-50.0.1-pyproject.toml").read_text(encoding="utf-8"))
    cargo_toml = tomllib.loads((EVIDENCE / "python/pyca-cryptography-50.0.1-Cargo.toml").read_text(encoding="utf-8"))
    cffi_build = (EVIDENCE / "python/pyca-cryptography-50.0.1-src_rust_cryptography-cffi_build.rs").read_text(encoding="utf-8")
    assert cargo_toml["workspace"]["package"]["rust-version"] == "1.83.0"
    assert set(cargo_toml["workspace"]["dependencies"]["pyo3"]["features"]) == {"abi3", "abi3t"}
    assert "setuptools.command.build_ext" in cffi_build and "PYO3_CROSS_LIB_DIR" in cffi_build
    maturin_raw = EVIDENCE / "pypi/maturin-1.14.1.json"
    maturin = load(maturin_raw)["info"]
    maturin_files = load(maturin_raw)["urls"]
    maturin_wheel = next(x for x in maturin_files if x["filename"].endswith("universal2.whl"))
    setuptools_raw = EVIDENCE / "python/setuptools-80.9.0-pypi.json"
    setuptools = load(setuptools_raw)
    setuptools_wheel = next(x for x in setuptools["urls"] if x["filename"] == "setuptools-80.9.0-py3-none-any.whl")
    cffi_raw = EVIDENCE / "python/cffi-2.1.1-pypi.json"
    cffi = load(cffi_raw)
    cffi_wheel = next(x for x in cffi["urls"] if "cp312-cp312-macosx_11_0_arm64.whl" in x["filename"])
    py_archive = {
        "filename": "Python-3.12.13.tar.xz", "url": "https://www.python.org/ftp/python/3.12.13/Python-3.12.13.tar.xz",
        "sha256": py_archive_sha, "head_content_length_bytes": 20801708,
        "metadata_source": "python.org version page, exact SHA-256 and size from release table + metadata-only HEAD",
    }
    openssl_raw = BUILDER_R4 / "openssl-4.0.2-release-api.json"
    openssl_release = load(openssl_raw)
    openssl_asset = next(x for x in openssl_release["assets"] if x["name"] == "openssl-4.0.2.tar.gz")
    assert openssl_asset["id"] == 529116792 and openssl_asset["size"] == 55153883
    assert openssl_asset["digest"] == "sha256:736b467530f916737b7031310ccb21d8218c6229e61e8e160cd1d3458cd543a8"
    openssl = {"name": openssl_asset["name"], "tag": openssl_release["tag_name"], "asset_id": openssl_asset["id"],
               "canonical_url": openssl_asset["browser_download_url"], "bytes": openssl_asset["size"],
               "sha256": openssl_asset["digest"].split(":", 1)[1], "raw_api_snapshot": stamp(openssl_raw)}
    py_record = {
        "source_release": {"page": stamp(release_html), "compressed_capture": stamp(release_gzip),
                           "xz_source": py_archive, "source_release_only": "The version page lists .tgz and .tar.xz source artifacts, not a macOS installer.",
                           "macos_pkg_probe": {"pkg_url": "https://www.python.org/ftp/python/3.12.13/python-3.12.13-macos11.pkg", "status": 404}},
        "pypi_build_backend": {"maturin": {"name": maturin["name"], "version": maturin["version"],
                                           "requires_python": maturin["requires_python"], "requires_dist": maturin.get("requires_dist", []),
                                           "host_wheel": {k: maturin_wheel.get(k) for k in ["filename", "url", "size", "digests"]},
                                           "host_wheel_is_universal2": True, "raw_metadata": stamp(maturin_raw)},
                               "setuptools_candidate": {"version": "80.9.0", "requires_python": setuptools["info"]["requires_python"],
                                                         "requires_dist_active_extras_only": "No active runtime/build extras selected; package is py3-none-any.",
                                                         "wheel": {k: setuptools_wheel.get(k) for k in ["filename", "url", "size", "digests"]},
                                                         "raw_metadata": stamp(setuptools_raw)},
                               "cffi": {"version": "2.1.1", "requires_python": cffi["info"]["requires_python"],
                                        "requires_dist": cffi["info"].get("requires_dist", []),
                                        "host_wheel": {k: cffi_wheel.get(k) for k in ["filename", "url", "size", "digests"]},
                                        "raw_metadata": stamp(cffi_raw)}},
        "openssl": openssl,
    }
    (EVIDENCE / "PYTHON_AND_BUILD_INPUT_METADATA_R2.json").write_text(json.dumps({
        "schema": "comsol-mcp.w25-python-cross-build-inputs/2", "created_utc": datetime.now(timezone.utc).isoformat(),
        "python_release": py_record,
        "cryptography_source": {
            "version": "50.0.1", "source_metadata_url": "https://github.com/pyca/cryptography/tree/50.0.1",
            "cargo_workspace": stamp(EVIDENCE / "python/pyca-cryptography-50.0.1-Cargo.toml"),
            "pyproject": stamp(EVIDENCE / "python/pyca-cryptography-50.0.1-pyproject.toml"),
            "cffi_build_rs": stamp(EVIDENCE / "python/pyca-cryptography-50.0.1-src_rust_cryptography-cffi_build.rs"),
            "locked_cargo_file": stamp(cargo_lock),
            "rust_version": cargo_toml["workspace"]["package"]["rust-version"],
            "pyo3_workspace_features": cargo_toml["workspace"]["dependencies"]["pyo3"]["features"],
            "build_system_requires": pyproject["build-system"]["requires"],
            "abi3_notes": ["pyo3 0.29 workspace enables both abi3 and abi3t", "The 50.0.1 cffi build script explicitly defines Py_LIMITED_API 0x030900f0 for Abi3", "PyO3's official guide supports building stable-ABI modules without a Python interpreter via PYO3_NO_PYTHON; this removes the requirement for an x86 target interpreter for PyO3 config, not the CFFI build script's target header requirement."],
            "cffi_build_script_requires": ["A host interpreter runs _cffi_src/build_openssl.py", "If PYO3_CROSS_LIB_DIR is unset the script queries host setuptools include_dirs", "If set, it derives a Python include path from the cross-libdir parent and basename", "Do not point at ARM CPython headers for x86 target or fabricate _sysconfigdata/libpython paths."],
        },
        "pyo3_guide": {"url": "https://pyo3.rs/v0.29.0/building-and-distribution.html", "accessed_utc": "2026-10-12T04:13:08Z",
                       "relevant_sections": {"stable_abi_without_interpreter": "§ Building stable ABI extension modules without a Python interpreter", "cross_compile": "§ Cross Compiling; target and host interpreter optional for abi3; PYO3_CROSS_LIB_DIR needed only to link libpython or when _sysconfigdata is required"}},
        "maturin_guide": {"url": "https://www.maturin.rs/distribution.html", "accessed_utc": "2026-10-12T04:13:08Z",
                          "cross_compile_note": "Docs say PyO3/bin bindings have decent support, while other bindings may work but are not regularly tested; non-Linux Zig cross behavior is not regularly tested. No build has been run."},
        "target_python_header_path": {
            "local_runtime": {"path": str(PYTHON), "version": "3.12.13", "platform": "macosx-26.0-arm64",
                              "sysconfigdata": "/opt/homebrew/Cellar/python@3.12/3.12.13_2/Frameworks/Python.framework/Versions/3.12/lib/python3.12/_sysconfigdata__darwin_darwin.py",
                              "include": "/opt/homebrew/opt/python@3.12/Frameworks/Python.framework/Versions/3.12/include/python3.12",
                              "pyconfig_h_sha256": "2bf9ae23f0553f42f1042c5e37e23698cf193dc7ccc9d63f8afa37e5ec0f9f5e",
                              "python_h_sha256": "729ef157f6026e6e1b3104593f87dddc597c3b83b60c7c2965878c62a56c6f7d",
                              "usable_for_x86_target": False},
            "documented_source_generation_path": "Use the exact python.org CPython 3.12.13 source artifact and official CPython cross-build mechanism (--build for arm host, --host x86_64 target, --with-build-python host Python); configure generates pyconfig.h. The 3.12.13 release page has no macOS binary installer. This path has not been run and requires its own cross-config validation; no target sysconfig file/header has been synthesized.",
            "status": "OPEN_TARGET_PYCONFIG_AND_HEADERS_NOT_BUILT",
        },
        "openssl_target": {**openssl, "build_input_downloaded": False,
                            "local_homebrew_openssl_arches": ["arm64"], "usable_as_x86_target": False},
        "local_toolchain_inventory": {
            "host": platform.machine(), "macos_sdk_version": "27.0", "sdk_path": "/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk",
            "xcode_select_path": "/Library/Developer/CommandLineTools", "clang_path": "/Library/Developer/CommandLineTools/usr/bin/clang",
            "clang_print_targets_reports_x86_64": True, "rustc_installed": False, "cargo_installed": False,
            "rustup_installed": False, "maturin_installed_in_shell": False,
            "rosetta_package_receipt": "absent", "rosetta_executable": "absent", "no_x86_process_launched": True,
            "homebrew_openssl": "3.6.4/3.6.2; dylibs are arm64 only"},
        "builder_closure_status": "OPEN_NOT_BUILD_READY",
        "blockers": [
            "Rust 1.83.0 host components and x86_64-apple-darwin std are exactly identified but absent locally and not acquired.",
            "All 32 Cargo registry lock checksums match crates.io metadata; crate archives remain not downloaded.",
            "PyO3 stable ABI supports omitting the x86 Python interpreter/config, but cryptography-cffi build.rs still compiles generated C against a Python header include path.",
            "CPython 3.12.13 x86 pyconfig.h/include tree is absent; source cross-build is the first-party route, but is untested and its target config/build closure is not yet established.",
            "Maturin docs do not claim regular test coverage for macOS cross builds; no x86 Maturin build or final Mach-O inspection exists.",
            "OpenSSL source is precisely identified; an x86_64 static OpenSSL build and target/link/deployment validation are not done.",
            "Rosetta is absent; do not launch any x86 process or trigger installation prompt."],
        "forbidden_claims": ["Intel wheel built", "Intel wheel installable", "portable release closure complete"],
        "native_or_build_work": "NOT_RUN",
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # Retain an offline structural validator with the generated audit.
    assert all(x["checksum_match"] for x in crate_rows)
    assert max_msrv in {"1.83", "1.83.0"}
    assert (manifest_doc["pkg"]["rust-std"]["target"]["x86_64-apple-darwin"]["xz_hash"] == "9562c98c59c6344f53a4f4c331e34cc88975153b8c25dd8b7a11ce00077ee3cb")
    print(json.dumps({"status": "PASS_OFFLINE_METADATA_INTEGRITY", "cargo_registry_rows": len(crate_rows),
                      "known_max_msrv": max_msrv, "null_msrv_count": len(null_msrv),
                      "rust_components": len(component_rows), "native_build": "NOT_RUN"}, sort_keys=True))


if __name__ == "__main__":
    main()
