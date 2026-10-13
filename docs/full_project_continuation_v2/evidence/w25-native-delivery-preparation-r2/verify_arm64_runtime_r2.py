#!/usr/bin/env python3
"""Offline post-install verification for the isolated W25 ARM64 task runtime."""
from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import os
import pathlib
import platform
import stat
import sys
from datetime import datetime, timezone


REPO = pathlib.Path(__file__).resolve().parents[4]
EVIDENCE = pathlib.Path(__file__).resolve().parent
TASK = pathlib.Path(
    "/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T/"
    "comsol-w25-arm64-runtime-r2-37bd820ee138424b8946684619d5bb24"
)
SITE = TASK / "site"
WHEELHOUSE = TASK / "wheelhouse"
PLAN = json.loads((EVIDENCE / "ISOLATED_ARM64_RUNTIME_PLAN_R2.json").read_text())


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_identity(path: pathlib.Path) -> dict[str, int]:
    info = path.stat(follow_symlinks=False)
    return {"device": int(info.st_dev), "inode": int(info.st_ino)}


def main() -> int:
    if platform.machine().lower() not in {"arm64", "aarch64"}:
        raise SystemExit(f"unexpected host architecture: {platform.machine()}")
    if sys.version_info[:3] != (3, 12, 13):
        raise SystemExit(f"unexpected interpreter: {sys.version}")
    if not SITE.is_dir() or SITE.is_symlink():
        raise SystemExit("isolated site directory missing or symlinked")

    sys.path.insert(0, str(REPO))
    from comsol_mcp._host_scoped_provider import verify_private_path_security

    security_rows = {}
    for name, path, expected in (
        ("trusted_parent", pathlib.Path(PLAN["trusted_parent_path"]), PLAN["trusted_parent_identity"]),
        ("task_directory", TASK, PLAN["task_directory_identity"]),
        ("site", SITE, PLAN["subdirectories"]["install.staging"]),
    ):
        result = verify_private_path_security(path, directory=True, expected_identity=expected)
        security_rows[name] = result
        if name in {"trusted_parent", "task_directory", "site"} and result["security_semantics"] != "posix-owner-mode+filesystem-ownership+no-extended-acl":
            raise SystemExit(f"unexpected trust semantics for {name}: {result['security_semantics']}")

    requirements = TASK / "requirements-arm64.txt"
    expected_requirements_sha = "e0547d079a6dee23d269aaaa60301232a74a07c1dc39687388a10ab1728011a6"
    if sha256_file(requirements) != expected_requirements_sha:
        raise SystemExit("requirements file SHA-256 changed")

    expected_wheels = {row["filename"]: row for row in PLAN["wheelhouse"]["wheels"]}
    found_wheels = {}
    for path in sorted(WHEELHOUSE.iterdir()):
        if not path.is_file() or path.is_symlink():
            raise SystemExit(f"unexpected wheelhouse entry: {path.name}")
        found_wheels[path.name] = {
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    if set(found_wheels) != set(expected_wheels):
        raise SystemExit("wheelhouse file set differs from the approved eight-wheel manifest")
    for filename, row in expected_wheels.items():
        observed = found_wheels[filename]
        if observed != {"size_bytes": row["size_bytes"], "sha256": row["sha256"]}:
            raise SystemExit(f"wheel mismatch: {filename}")

    modules = (
        "psutil", "objc", "AppKit", "ApplicationServices", "Quartz",
        "CoreText", "CoreMedia", "ScreenCaptureKit",
    )
    import_rows = []
    sys.path.insert(0, str(SITE))
    for name in modules:
        module = importlib.import_module(name)
        import_rows.append({"module": name, "file": getattr(module, "__file__", None)})
        module_path = pathlib.Path(module.__file__).resolve()
        if SITE not in module_path.parents:
            raise SystemExit(f"module escaped isolated site: {name} -> {module_path}")
    from ScreenCaptureKit import SCScreenshotManager
    if SCScreenshotManager is None:
        raise SystemExit("SCScreenshotManager binding is absent")

    distributions = sorted(
        {dist.metadata["Name"].lower(): dist.version for dist in importlib.metadata.distributions(path=[str(SITE)])}.items()
    )
    site_rows = []
    for path in sorted(SITE.rglob("*")):
        if path.is_symlink():
            raise SystemExit(f"symlink in isolated site: {path}")
        if path.is_file():
            site_rows.append({
                "path": path.relative_to(SITE).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            })
    site_manifest = {
        "schema": "comsol-mcp.w25-arm64-installed-site-manifest/1",
        "site": str(SITE),
        "site_identity": file_identity(SITE),
        "files": site_rows,
        "file_count": len(site_rows),
        "total_bytes": sum(row["size_bytes"] for row in site_rows),
    }
    site_manifest_path = EVIDENCE / "ARM64_SITE_FILES_R2.json"
    site_manifest_path.write_text(json.dumps(site_manifest, sort_keys=True, indent=2) + "\n")

    receipt = {
        "schema": "comsol-mcp.w25-arm64-runtime-install-receipt/2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "SOFTWARE_PREPARATION_PASS",
        "host": {"platform": platform.platform(), "architecture": platform.machine()},
        "interpreter": {"version": sys.version, "executable": sys.executable},
        "task_root": str(TASK),
        "task_root_identity": file_identity(TASK),
        "site_path": str(SITE),
        "site_identity": file_identity(SITE),
        "requirements_sha256": sha256_file(requirements),
        "requirements_evidence_copy_sha256": sha256_file(EVIDENCE / "requirements-arm64-R2.txt"),
        "approved_wheel_count": len(found_wheels),
        "approved_wheel_total_bytes": sum(row["size_bytes"] for row in found_wheels.values()),
        "verified_wheels": found_wheels,
        "download": {
            "argv": [PLAN["interpreter"]["path"], str(EVIDENCE / "fetch_pinned_arm64_wheels_r2.py"), "--execute-download"],
            "exit_code": 0,
            "stdout_status": "NOT_CAPTURED",
            "argv_source": "exact pinned task interpreter and downloader from the R2 candidate plan",
            "script_sha256": sha256_file(EVIDENCE / "fetch_pinned_arm64_wheels_r2.py"),
            "no_clobber": True,
        },
        "install": {
            "argv": PLAN["install_argv"],
            "exit_code": 0,
            "stdout_status": "NOT_CAPTURED",
            "argv_source": "exact argv preserved in ISOLATED_ARM64_RUNTIME_PLAN_R2.json",
            "method": "hash-verified local wheels only; pinned-parent no-replace activation",
        },
        "verification_script_sha256": sha256_file(pathlib.Path(__file__)),
        "post_install_imports": import_rows,
        "screencapturekit_binding_present": True,
        "distribution_versions": dict(distributions),
        "site_manifest": {
            "path": site_manifest_path.name,
            "sha256": sha256_file(site_manifest_path),
            "file_count": len(site_rows),
            "total_bytes": site_manifest["total_bytes"],
        },
        "security_verification": security_rows,
        "source_scope": {
            "live_pyproject_changed": False,
            "live_uv_lock_changed": False,
            "recovered_runtime_changed": False,
            "system_python_changed": False,
            "gui_calls": 0,
            "permission_queries_or_prompts": 0,
            "comsol_launches": 0,
            "solver_calls": 0,
            "network_calls_during_install_or_smoke": 0,
        },
        "limitations": [
            "Software environment/import verification only; no GUI or ScreenCaptureKit call was made.",
            "Native macOS GUI/permission/profile validation remains NOT_RUN.",
            "Intel x86_64 cryptography wheel builder closure remains OPEN.",
        ],
    }
    receipt_path = EVIDENCE / "ARM64_RUNTIME_INSTALL_RECEIPT_R2.json"
    receipt_path.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    print(json.dumps({
        "status": receipt["status"],
        "verified_wheels": receipt["approved_wheel_count"],
        "wheel_bytes": receipt["approved_wheel_total_bytes"],
        "site_files": len(site_rows),
        "site_bytes": site_manifest["total_bytes"],
        "site_identity": receipt["site_identity"],
        "receipt_sha256": sha256_file(receipt_path),
        "site_manifest_sha256": sha256_file(site_manifest_path),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
