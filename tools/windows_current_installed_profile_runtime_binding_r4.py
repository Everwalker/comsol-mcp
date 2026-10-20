"""Bind a Main-reviewed current175-r9 App artifact to one installed Windows COMSOL runtime.

This is a read-only software binding helper. It does not create approval or
once files, construct a ControlDaemon, start a Server/Worker, or dispatch a
COMSOL operation.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping
from comsol_mcp._session_context import SessionRuntimeConfig
from comsol_mcp._runtime_installation import inspect_installation, runtime_id_for_root
from tools import w26_application_bundle as application


SCHEMA = "cv3-windows-current-installed-profile-runtime-binding/4"
TARGET_BUILDS = {
    "windows-x64/comsol6.3": "6.3.0.290",
    "windows-x64/comsol6.4": "6.4.0.293",
}
TARGET_CATALOG_VERSIONS = {
    "windows-x64/comsol6.3": "6.3.0",
    "windows-x64/comsol6.4": "6.4.0",
}
TARGET_BUILD_NUMBERS = {
    "windows-x64/comsol6.3": 290,
    "windows-x64/comsol6.4": 293,
}
WINDOWS_TARGET = "win_amd64"
SOURCE_MEMBERS = (
    "comsol_mcp/_control_daemon.py",
    "comsol_mcp/_java_worker.py",
    "comsol_mcp/_runtime_installation.py",
    "comsol_mcp/_session_context.py",
    "comsol_mcp/_session_server.py",
    "comsol_mcp/_host_scoped_provider.py",
    "comsol_mcp/_windows_host_preflight_owner.py",
    "comsol_mcp/_windows_parameter_publish.py",
    "comsol_mcp/_windows_state_restore.py",
    "tools/release_transition_lifecycle.py",
    "tools/w26_application_bundle.py",
    "tools/w26_application_lifecycle.py",
    "tools/w26_application_sources_175_r4.lock.json",
    "tools/w26_application_sources_175_r5.lock.json",
    "tools/w26_application_sources_175_r6.lock.json",
    "tools/w26_application_sources_175_r7_final.lock.json",
    "tools/w26_application_sources_175_r8_prepared.lock.json",
    "tools/w26_application_sources_175_r9_prepared.lock.json",
    "tools/w26_dependency_unit.py",
    "tools/w26_prepare_current_inputs.py",
    "tools/w26_runtime_actor.py",
    "tools/w26_sealed_data.py",
    "tools/w26_sealed_data_r9.py",
    "tools/w26_target_inputs.py",
    "docs/full_project_continuation_v3/state/CURRENT175_R9_POST_INTEGRATION_MAIN_REVIEW_R1.json",
    "tools/windows_current_installed_profile_runtime_binding_r4.py",
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PROFILE_KEYS = {"wheel", "result", "candidate", "once", "review", "backup_source_sha256",
                 "source_provenance"}
_JAVA_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){1,3}\Z")
_SOURCE_PROFILE = "current175-r9"
R9_SOURCE_LOCK_SHA256 = "a524e533f01ed5e5ea0f06cfa5caf49ab0ddf50474258b672265c13f62f987a2"
R9_MEMBER_FINGERPRINT_SHA256 = "401d720192bba5456c8c576f9ed845a74e0555b9c8d4a5e9fa04dcb28d89ef26"
R9_SOURCE_PROFILE_REVIEW_SHA256 = "bf417ddbdbc6230cb06e2ab51c89b4d4477bc92fdbc507438651327b10d8390f"
R9_SOURCE_PROFILE_REVIEW_STATUS = "PASS_SCOPED_EXACT_SOURCE_PROFILE_PREPARATION"
ORIGINAL_MATRIX_NAME = "ORIGINAL_WINDOWS_REQUIRED_MATRIX.json"
ORIGINAL_MATRIX_SHA256 = "be5d224ea3cc304ddea7d9b3a52d5dfb7b53f1d4e3db592c947cbb7e44829160"


class CurrentProfileNotReady(RuntimeError):
    def __init__(self, readiness: Mapping[str, Any]):
        self.readiness = dict(readiness)
        super().__init__(str(self.readiness.get("status", "NOT_READY_CURRENT_PROFILE")))


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _file_record(path: Path, *, maximum: int = 512 * 1024 * 1024) -> dict[str, Any]:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("runtime input must be a regular non-symlink file")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size < 0 or before.st_size > maximum:
            raise RuntimeError("runtime input type/size is outside the fixed bound")
        digest = hashlib.sha256()
        count = 0
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            count += len(block)
            if count > maximum:
                raise RuntimeError("runtime input exceeded its fixed byte bound")
            digest.update(block)
        after = os.fstat(fd)
        named = path.lstat()
        key = lambda row: (row.st_dev, row.st_ino, row.st_mode, row.st_size,
                           row.st_mtime_ns, row.st_ctime_ns)
        if (key(before) != key(after) or key(before) != key(named)
                or count != before.st_size or stat.S_ISLNK(named.st_mode)):
            raise RuntimeError("runtime input changed or was aliased while being read")
        return {"path": str(path.resolve(strict=True)), "bytes": count,
                "sha256": digest.hexdigest(), "device": int(after.st_dev),
                "file_id": int(after.st_ino)}
    finally:
        os.close(fd)


def _verify_record(record: Any, *, label: str, exact_path: Path | None = None) -> dict[str, Any]:
    if (not isinstance(record, Mapping)
            or not isinstance(record.get("path"), str)
            or type(record.get("bytes")) is not int or record["bytes"] <= 0
            or not isinstance(record.get("sha256"), str)
            or not _SHA256.fullmatch(record["sha256"])):
        raise RuntimeError(f"{label} file identity record is malformed")
    path = Path(record["path"])
    if exact_path is not None and path.resolve(strict=True) != exact_path.resolve(strict=True):
        raise RuntimeError(f"{label} path differs from its closed location")
    observed = _file_record(path)
    if any(observed.get(key) != record.get(key) for key in ("path", "bytes", "sha256")):
        raise RuntimeError(f"{label} bytes differ from the supplied current identity")
    return observed


def source_manifest(repository_root: Path) -> dict[str, dict[str, Any]]:
    """Hash the narrow production source and producer set used by this seam."""
    root = Path(repository_root).resolve(strict=True)
    if not root.is_dir():
        raise RuntimeError("source repository root is not a directory")
    result = {}
    for relative in SOURCE_MEMBERS:
        path = root / relative
        if not path.resolve(strict=True).is_relative_to(root):
            raise RuntimeError("runtime source escaped the exact repository root")
        result[relative] = _file_record(path)
    return result


def capture_windows_task_root_proof(root: Path) -> dict[str, Any]:
    """Capture the existing Windows same-handle identity and DACL proof.

    This read-only call is deliberately unavailable on other platforms. Local
    software tests inject an explicit fixture proof; that fixture cannot be
    used as a Windows native acceptance record.
    """
    if os.name != "nt":
        raise RuntimeError("native Windows task-root proof is unavailable on this host")
    from comsol_mcp import _windows_state_restore as restore

    path = Path(root).resolve(strict=True)
    with restore._chain(path) as chain:
        chain.verify()
        fingerprint = chain.api.full_fingerprint(chain.leaf, directory=True)
        descriptor = chain.directory_proofs[-1][2]
        if fingerprint[0] != chain.directory_proofs[-1][1]:
            raise RuntimeError("task-root identity changed within the pinned Windows handle chain")
        volume, file_id = fingerprint[0]
    return {
        "schema": "windows-same-handle-task-root-proof/1",
        "provider": "comsol_mcp._windows_state_restore._chain.full_fingerprint+security",
        "path": str(path),
        "identity": {
            "volume_serial": volume, "file_id": file_id, "size": fingerprint[1],
            "created": fingerprint[2], "changed": fingerprint[3], "written": fingerprint[4],
            "link_count": fingerprint[5],
        },
        "security_descriptor_sha256": _sha(canonical_bytes(descriptor)),
    }


def _validate_root_proof(root: Path, proof: Any) -> dict[str, Any]:
    identity = proof.get("identity") if isinstance(proof, Mapping) else None
    if os.name == "nt":
        required_identity = {"volume_serial", "file_id", "size", "created", "changed", "written", "link_count"}
    else:
        required_identity = {"device", "inode"}
    if (not isinstance(proof, Mapping)
            or proof.get("schema") != "windows-same-handle-task-root-proof/1"
            or not isinstance(proof.get("provider"), str)
            or proof.get("path") != str(Path(root).resolve(strict=True))
            or not isinstance(identity, Mapping)
            or set(identity) != required_identity
            or any(type(value) is not int for value in identity.values())
            or not isinstance(proof.get("security_descriptor_sha256"), str)
            or not _SHA256.fullmatch(proof["security_descriptor_sha256"])):
        raise RuntimeError("task root requires the exact current identity and security proof")
    expected_provider = (
        "comsol_mcp._windows_state_restore._chain.full_fingerprint+security"
        if os.name == "nt" else "test-fixture-only-not-native-acceptance"
    )
    if proof["provider"] != expected_provider:
        raise RuntimeError("task-root proof provider is not the platform's exact production reader")
    if os.name == "nt":
        if (identity["volume_serial"] < 0 or identity["file_id"] <= 0 or identity["size"] != 0
                or identity["link_count"] < 1):
            raise RuntimeError("Windows task-root full identity fields are invalid")
    return json.loads(canonical_bytes(dict(proof)))


def _validate_profile(profile: Any, source_profile: str) -> dict[str, Any]:
    if (not isinstance(profile, Mapping) or set(profile) != _PROFILE_KEYS
            or source_profile != _SOURCE_PROFILE):
        raise RuntimeError("current W26 application profile verification result is incomplete")
    for name in ("wheel", "result", "candidate", "once", "review"):
        _verify_record(profile[name], label="current W26 " + name)
    if not isinstance(profile["backup_source_sha256"], str) or not _SHA256.fullmatch(profile["backup_source_sha256"]):
        raise RuntimeError("current W26 source-profile digest is malformed")
    wheel = profile["wheel"]
    if not Path(wheel["path"]).name.startswith("comsol_mcp-") or not Path(wheel["path"]).name.endswith("-py3-none-any.whl"):
        raise RuntimeError("current W26 installed application wheel is not the exact universal package")
    return json.loads(canonical_bytes(dict(profile)))


def _validate_w26_profile_paths(profile: Mapping[str, Any], producer_root: Path) -> None:
    root = Path(producer_root).resolve(strict=True)
    expected_paths = {
        "result": root / "APPLICATION_BUNDLE_RESULT.json",
        "once": root / "UNIT_ONCE_GUARD.json",
        "review": root / "MAIN_SOURCE_REVIEW.json",
    }
    for name, expected in expected_paths.items():
        if Path(profile[name]["path"]).resolve(strict=True) != expected.resolve(strict=True):
            raise RuntimeError("W26 verified producer record escaped its exact application root")
    candidate = Path(profile["candidate"]["path"]).resolve(strict=True)
    if candidate.parent != root or not candidate.name.startswith("ACTOR_CANDIDATE_"):
        raise RuntimeError("W26 verified application candidate escaped its producer root")
    wheel = Path(profile["wheel"]["path"]).resolve(strict=True)
    if (wheel.parent != root / "appwheel"
            or wheel.name != "comsol_mcp-0.1.10-py3-none-any.whl"):
        raise RuntimeError("W26 verified wheel is not the exact current application artifact")


def _build_w26_source_profile_proof(
        descriptor: Mapping[str, Any], profile: Mapping[str, Any],
        source_lock: Mapping[str, Any]) -> dict[str, Any]:
    """Bind R9 output to the reviewed lock and two immutable reader-verified snapshots."""
    if (descriptor.get("schema") != "w26-current-installed-application/1"
            or descriptor.get("source_profile") != _SOURCE_PROFILE
            or not isinstance(descriptor.get("root"), str)
            or not isinstance(descriptor.get("loader_source_closure"), Mapping)):
        raise RuntimeError("W26 source-profile descriptor is not the exact current175-r9 producer")
    producer_root = Path(descriptor["root"]).resolve(strict=True)
    provenance = _validate_r9_source_provenance(profile.get("source_provenance"), producer_root,
                                                source_lock, profile)
    _validate_w26_profile_paths(profile, producer_root)
    core = {
        "schema": "w26-current-source-profile-proof/2",
        "status": "PASS_W26_R9_SEMANTIC_PROFILE_READER",
        "source_profile": _SOURCE_PROFILE,
        "verified_by": "tools.w26_sealed_data_r9.current_application_profile",
        "source_lock_sha256": source_lock["sha256"],
        "source_member_fingerprint_sha256": source_lock["member_fingerprint_sha256"],
        "source_provenance_sha256": _sha(canonical_bytes(provenance)),
        "producer_root": str(producer_root),
        "descriptor_sha256": _sha(canonical_bytes(dict(descriptor))),
        "loader_source_closure_sha256": _sha(canonical_bytes(descriptor.get("loader_source_closure"))),
        "artifact_profile_sha256": _sha(canonical_bytes(dict(profile))),
        "source_provenance": provenance,
        "producer_records": {name: dict(profile[name]) for name in
                             ("wheel", "result", "candidate", "once", "review")},
        "backup_source_sha256": profile["backup_source_sha256"],
    }
    return {**core, "proof_sha256": _sha(canonical_bytes(core))}


def _validate_w26_source_profile_proof(proof: Any, profile: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(proof, Mapping):
        raise RuntimeError("current W26 profile requires the semantic-reader proof")
    core = {key: value for key, value in proof.items() if key != "proof_sha256"}
    source_lock = _r9_source_lock_binding(Path(__file__).resolve().parents[1])
    provenance = _validate_r9_source_provenance(
        profile.get("source_provenance"), Path(proof.get("producer_root", "")), source_lock, profile,
    )
    if (set(proof) != {
            "schema", "status", "source_profile", "verified_by", "producer_root",
            "source_lock_sha256", "source_member_fingerprint_sha256", "source_provenance_sha256",
            "descriptor_sha256", "loader_source_closure_sha256", "artifact_profile_sha256",
            "source_provenance", "producer_records", "backup_source_sha256", "proof_sha256",
        }
            or proof.get("schema") != "w26-current-source-profile-proof/2"
            or proof.get("status") != "PASS_W26_R9_SEMANTIC_PROFILE_READER"
            or proof.get("source_profile") != _SOURCE_PROFILE
            or proof.get("verified_by") != "tools.w26_sealed_data_r9.current_application_profile"
            or proof.get("source_lock_sha256") != source_lock["sha256"]
            or proof.get("source_member_fingerprint_sha256") != source_lock["member_fingerprint_sha256"]
            or proof.get("source_provenance") != provenance
            or proof.get("source_provenance_sha256") != _sha(canonical_bytes(provenance))
            or not isinstance(proof.get("producer_root"), str)
            or not Path(proof["producer_root"]).is_absolute()
            or not isinstance(proof.get("descriptor_sha256"), str)
            or not _SHA256.fullmatch(proof["descriptor_sha256"])
            or not isinstance(proof.get("loader_source_closure_sha256"), str)
            or not _SHA256.fullmatch(proof["loader_source_closure_sha256"])
            or proof.get("artifact_profile_sha256") != _sha(canonical_bytes(dict(profile)))
            or proof.get("producer_records") != {
                name: dict(profile[name]) for name in ("wheel", "result", "candidate", "once", "review")
            }
            or proof.get("backup_source_sha256") != profile.get("backup_source_sha256")
            or proof.get("proof_sha256") != _sha(canonical_bytes(core))):
        raise RuntimeError("W26 source-profile proof is not bound to the exact semantic-reader output")
    _validate_w26_profile_paths(profile, Path(proof["producer_root"]))
    return json.loads(canonical_bytes(dict(proof)))


def _r9_source_lock_binding(repository_root: Path) -> dict[str, Any]:
    """Verify Main-approved R9 source preparation separately from any App build."""
    root = Path(repository_root).resolve(strict=True)
    if application.LATEST_SOURCE_PROFILE != _SOURCE_PROFILE:
        raise RuntimeError("the declared latest W26 generation is not current175-r9")
    lock_path = Path(application.CURRENT175_R9_SOURCE_LOCK).resolve(strict=True)
    expected_path = root / "tools" / "w26_application_sources_175_r9_prepared.lock.json"
    if lock_path != expected_path.resolve(strict=True):
        raise RuntimeError("the R9 source lock path differs from the Main-reviewed location")
    lock_record = _file_record(lock_path)
    if lock_record["sha256"] != R9_SOURCE_LOCK_SHA256:
        raise RuntimeError("R9 source lock differs from the exact Main-reviewed preparation")
    lock_value = json.loads(lock_path.read_text(encoding="utf-8", errors="strict"))
    members = lock_value.get("members")
    if (lock_value.get("schema") != "w26-application-source-closure/1"
            or lock_value.get("profile") != _SOURCE_PROFILE
            or lock_value.get("member_count") != 175
            or lock_value.get("package_count") != 172
            or lock_value.get("root_count") != 3
            or not isinstance(members, Mapping) or len(members) != 175):
        raise RuntimeError("R9 source lock schema, profile, or member counts differ")
    member_fingerprint = _sha(canonical_bytes(dict(members)))
    if member_fingerprint != R9_MEMBER_FINGERPRINT_SHA256:
        raise RuntimeError("R9 source member fingerprint differs from Main-reviewed preparation")
    live_members = application.source_members(root)
    observed_contract = application.source_contract(live_members, _SOURCE_PROFILE)
    if (observed_contract.get("path") != str(lock_path)
            or observed_contract.get("sha256") != R9_SOURCE_LOCK_SHA256):
        raise RuntimeError("production W26 source-contract method returned a different R9 lock")
    review_path = root / "docs/full_project_continuation_v3/state/CURRENT175_R9_POST_INTEGRATION_MAIN_REVIEW_R1.json"
    review_record = _file_record(review_path)
    if review_record["sha256"] != R9_SOURCE_PROFILE_REVIEW_SHA256:
        raise RuntimeError("R9 source-profile Main review receipt differs from the reviewed source packet")
    review = json.loads(review_path.read_text(encoding="utf-8", errors="strict"))
    if (review.get("status") != R9_SOURCE_PROFILE_REVIEW_STATUS
            or review.get("lock", {}).get("path") != str(lock_path)
            or review.get("lock", {}).get("sha256") != R9_SOURCE_LOCK_SHA256
            or review.get("verified_live_members") != 175):
        raise RuntimeError("R9 Main review does not approve this exact 175-member preparation")
    return {
        "path": str(lock_path),
        "bytes": lock_record["bytes"],
        "sha256": lock_record["sha256"],
        "source_profile": _SOURCE_PROFILE,
        "member_count": 175,
        "package_count": 172,
        "root_count": 3,
        "member_fingerprint_sha256": member_fingerprint,
        "main_review_path": str(review_path),
        "main_review_sha256": review_record["sha256"],
        "main_review_status": review["status"],
        "preparation_only": True,
        "app_build": review.get("app_build"),
        "native_GUI": review.get("native_GUI"),
    }


def _validate_r9_source_provenance(
        provenance: Any, producer_root: Path, source_lock: Mapping[str, Any],
        profile: Mapping[str, Any]) -> dict[str, Any]:
    expected_keys = {
        "schema", "source_profile", "source_lock_path", "source_lock_sha256",
        "member_count", "package_count", "root_count", "member_fingerprint_sha256",
        "source_snapshot_path", "source_snapshot_member_count", "source_snapshot_fingerprint_sha256",
        "origin_snapshot_path", "origin_snapshot_member_count", "origin_snapshot_fingerprint_sha256",
        "source_snapshot_record_path", "source_snapshot_record_sha256",
    }
    if not isinstance(provenance, Mapping) or set(provenance) != expected_keys:
        raise RuntimeError("R9 reader source provenance receipt is incomplete or not closed")
    root = Path(producer_root).resolve(strict=True)
    snapshot_record_path = Path(provenance.get("source_snapshot_record_path", ""))
    expected = {
        "schema": "w26-source-provenance-snapshot-binding/1",
        "source_profile": _SOURCE_PROFILE,
        "source_lock_path": source_lock["path"],
        "source_lock_sha256": R9_SOURCE_LOCK_SHA256,
        "member_count": 175,
        "package_count": 172,
        "root_count": 3,
        "member_fingerprint_sha256": R9_MEMBER_FINGERPRINT_SHA256,
        "source_snapshot_path": str(root / "source-input"),
        "source_snapshot_member_count": 175,
        "source_snapshot_fingerprint_sha256": R9_MEMBER_FINGERPRINT_SHA256,
        "origin_snapshot_path": str(root / "source-origin"),
        "origin_snapshot_member_count": 175,
        "origin_snapshot_fingerprint_sha256": R9_MEMBER_FINGERPRINT_SHA256,
    }
    if any(provenance.get(key) != value for key, value in expected.items()):
        raise RuntimeError("R9 reader provenance differs from the Main-reviewed lock and immutable snapshots")
    if (not snapshot_record_path.is_absolute()
            or snapshot_record_path.parent != root
            or not snapshot_record_path.name.startswith("APP_INPUT_BINDING_")
            or not isinstance(provenance.get("source_snapshot_record_sha256"), str)
            or not _SHA256.fullmatch(provenance["source_snapshot_record_sha256"])):
        raise RuntimeError("R9 source-snapshot binding record path or digest is malformed")
    observed_record = _file_record(snapshot_record_path)
    if observed_record["sha256"] != provenance["source_snapshot_record_sha256"]:
        raise RuntimeError("R9 source-snapshot binding record differs from its reader receipt")
    candidate_path = Path(profile["candidate"]["path"])
    candidate = json.loads(candidate_path.read_text(encoding="utf-8", errors="strict"))
    prepared_record = candidate.get("prepared")
    if (not isinstance(prepared_record, Mapping)
            or prepared_record.get("path") != str(snapshot_record_path)
            or prepared_record.get("sha256") != observed_record["sha256"]):
        raise RuntimeError("R9 candidate does not bind the exact source-snapshot record read by its reader")
    return json.loads(canonical_bytes(dict(provenance)))


def _validate_target_inputs(target_inputs: Any) -> dict[str, Any]:
    if (not isinstance(target_inputs, Mapping)
            or target_inputs.get("schema") != "W26_CURRENT_THREE_TARGET_INPUTS_V1"
            or target_inputs.get("status") != "INPUTS_VERIFIED_NOT_INSTALLED_OR_RELEASED"):
        raise RuntimeError("current W26 cross-platform input profile is not the exact read-only input result")
    targets = target_inputs.get("targets")
    if not isinstance(targets, Mapping) or WINDOWS_TARGET not in targets:
        raise RuntimeError("W26 current Windows x64 target input is missing")
    rows = targets[WINDOWS_TARGET].get("rows") if isinstance(targets[WINDOWS_TARGET], Mapping) else None
    if not isinstance(rows, list) or len(rows) != 34:
        raise RuntimeError("W26 Windows x64 dependency closure is not the exact 34-row input set")
    return json.loads(canonical_bytes(dict(target_inputs)))


def _validate_installed_identity(target: str, identity: Any) -> dict[str, Any]:
    if not isinstance(target, str) or target not in TARGET_BUILDS:
        raise RuntimeError("installed COMSOL identity target is outside the exact Windows profile")
    expected_full_product_version = TARGET_BUILDS[target]
    expected_catalog_version = TARGET_CATALOG_VERSIONS[target]
    expected_build_number = TARGET_BUILD_NUMBERS[target]
    if (not isinstance(identity, Mapping)
            or set(identity) != {
                "schema", "status", "target", "catalog_version", "full_product_version",
                "build_number", "installation_root",
                "desktop_executable", "server_executable", "jdk_home",
            }
            or identity.get("schema") != "windows-current-comsol-install-identity/2"
            or identity.get("status") != "READONLY_INSTALLED_IDENTITY_VERIFIED"
            or identity.get("target") != target
            or identity.get("catalog_version") != expected_catalog_version
            or identity.get("full_product_version") != expected_full_product_version
            or type(identity.get("build_number")) is not int
            or identity.get("build_number") != expected_build_number):
        raise RuntimeError("installed COMSOL identity is not bound to the exact Windows target/build")
    root = Path(identity.get("installation_root", ""))
    is_junction = getattr(root, "is_junction", lambda: False)
    if not root.is_absolute() or root.is_symlink() or is_junction() or not root.is_dir():
        raise RuntimeError("installed COMSOL root is not an existing absolute real directory")
    root = root.resolve(strict=True)
    if str(root) != identity["installation_root"]:
        raise RuntimeError("installed COMSOL root is not in its canonical absolute form")
    if os.name == "nt":
        expected_roots = {
            "windows-x64/comsol6.3": Path(r"C:\Program Files\COMSOL\COMSOL63\Multiphysics"),
            "windows-x64/comsol6.4": Path(r"C:\Program Files\COMSOL\COMSOL64\Multiphysics"),
        }
        if os.path.normcase(str(root)) != os.path.normcase(str(expected_roots[target])):
            raise RuntimeError("installed COMSOL root differs from the observed current-host installation")
    expected_readme = root / "readme.txt"
    readme = _file_record(expected_readme)
    readme_text = expected_readme.read_text(encoding="utf-8", errors="strict")
    if _file_record(expected_readme) != readme:
        raise RuntimeError("installed COMSOL README changed while its version was read")
    expected_major = expected_catalog_version.split(".", 1)[0] + "." + expected_catalog_version.split(".", 2)[1]
    readme_lines = {line.strip() for line in readme_text.splitlines()}
    if (f"COMSOL {expected_full_product_version} README" not in readme_lines
            or f"for COMSOL {expected_major}." not in readme_lines):
        raise RuntimeError("installed COMSOL README does not identify the exact expected product build")
    desktop_path = root / "bin" / "win64" / "comsol.exe"
    server_path = root / "bin" / "win64" / "comsolmphserver.exe"
    desktop = _verify_record(identity.get("desktop_executable"), label="COMSOL Desktop executable",
                             exact_path=desktop_path)
    server = _verify_record(identity.get("server_executable"), label="COMSOL Server executable", exact_path=server_path)
    desktop_version = _windows_product_version(Path(desktop["path"]))
    server_version = _windows_product_version(Path(server["path"]))
    if (_verify_record(identity["desktop_executable"], label="COMSOL Desktop executable",
                       exact_path=desktop_path) != desktop
            or _verify_record(identity["server_executable"], label="COMSOL Server executable",
                              exact_path=server_path) != server):
        raise RuntimeError("installed COMSOL executable changed while its version resource was read")
    if (desktop_version != expected_full_product_version
            or server_version != expected_full_product_version):
        raise RuntimeError("installed executable ProductVersion differs from the exact target build")

    jdk_home = Path(identity.get("jdk_home", ""))
    is_junction = getattr(jdk_home, "is_junction", lambda: False)
    if not jdk_home.is_absolute() or jdk_home.is_symlink() or is_junction() or not jdk_home.is_dir():
        raise RuntimeError("the external JDK home is not an existing absolute real directory")
    jdk_home = jdk_home.resolve(strict=True)
    if str(jdk_home) != identity["jdk_home"]:
        raise RuntimeError("external JDK home is not in its canonical absolute form")
    from comsol_mcp._java_worker import JavaWorkerPaths

    paths = JavaWorkerPaths(root, jdk_home, platform_name="nt")
    classpath_string, manifest_sha256, jar_count, jar_content_sha256 = paths.classpath()
    classpath_paths = tuple(Path(value).resolve(strict=True)
                            for value in classpath_string.split(";") if value)
    manifest = _file_record(root / "bin" / "comsolclientpath.txt")
    java = _file_record(paths.executable("java"))
    javac = _file_record(paths.executable("javac"))
    release_path = jdk_home / "release"
    release_record = _file_record(release_path)
    release_lines = {}
    release_text = release_path.read_text(encoding="utf-8", errors="strict")
    if _file_record(release_path) != release_record:
        raise RuntimeError("JDK release metadata changed while it was read")
    for line in release_text.splitlines():
        if "=" in line:
            name, value = line.split("=", 1)
            release_lines[name.strip()] = value.strip().strip('"')
    java_version = release_lines.get("JAVA_VERSION", "")
    java_arch = release_lines.get("OS_ARCH", "").casefold()
    if not _JAVA_VERSION.fullmatch(java_version) or java_arch not in {"amd64", "x86_64", "x64"}:
        raise RuntimeError("the JDK release file does not identify a usable Windows x64 Java runtime")
    if (len(classpath_paths) != jar_count or not classpath_paths
            or any(not path.is_relative_to(root) for path in classpath_paths)):
        raise RuntimeError("production COMSOL classpath is incomplete or escaped its installed root")
    if manifest_sha256 != manifest["sha256"]:
        raise RuntimeError("COMSOL classpath manifest changed while the production resolver ran")
    if (not Path(java["path"]).is_relative_to(jdk_home)
            or not Path(javac["path"]).is_relative_to(jdk_home)
            or not Path(release_record["path"]).is_relative_to(jdk_home)):
        raise RuntimeError("the Java toolchain resolved outside its exact JDK root")
    if _file_record(root / "bin" / "comsolclientpath.txt") != manifest:
        raise RuntimeError("COMSOL classpath manifest changed while the production resolver ran")
    production_runtime_id = runtime_id_for_root(root)
    production_inspection = inspect_installation(production_runtime_id, system="Windows")
    observed_installation = production_inspection.get("installation")
    if (not isinstance(observed_installation, Mapping)
            or observed_installation.get("runtime_id") != production_runtime_id
            or observed_installation.get("root") != str(root)
            or observed_installation.get("metadata_only") is not True
            or observed_installation.get("comsol_started") is not False
            or observed_installation.get("license_checked_out") is not False
            or observed_installation.get("version", {}).get("status") != "OBSERVED"
            or observed_installation.get("version", {}).get("value") != expected_catalog_version
            or observed_installation.get("build", {}).get("status") != "OBSERVED"
            or observed_installation.get("build", {}).get("value") != expected_build_number):
        raise RuntimeError("production runtime_id parser/inspection does not identify the exact installed root/build")
    return {
        "schema": identity["schema"], "status": identity["status"],
        "target": target,
        "comsol_version": expected_catalog_version,
        "catalog_version": expected_catalog_version,
        "full_product_version": expected_full_product_version,
        "build_number": expected_build_number,
        "installation_root": str(root.resolve(strict=True)),
        "runtime_id": production_runtime_id,
        "production_inspection": production_inspection,
        "readme": readme, "desktop_executable": desktop, "server_executable": server,
        "product_versions": {str(desktop_path): desktop_version, str(server_path): server_version},
        "jdk_home": str(jdk_home), "java_executable": java, "javac_executable": javac,
        "java_release": {**release_record, "version": java_version, "architecture": java_arch},
        "classpath_manifest": {**manifest, "manifest_sha256": manifest_sha256},
        "classpath_identity": {"entry_count": jar_count, "jar_content_sha256": jar_content_sha256},
        "classpath": [{"path": str(path)} for path in classpath_paths],
    }


def _windows_product_version(executable: Path) -> str:
    """Read a PE ProductVersion resource without starting the executable."""
    if os.name != "nt":
        raise RuntimeError("Windows executable version-resource reading is unavailable on this host")
    try:
        import win32api
        info = win32api.GetFileVersionInfo(str(executable), "\\")
        product_ms = int(info.get("ProductVersionMS", 0))
        product_ls = int(info.get("ProductVersionLS", 0))
    except Exception as exc:
        raise RuntimeError("installed COMSOL ProductVersion resource is unavailable") from exc
    if not product_ms and not product_ls:
        raise RuntimeError("installed COMSOL ProductVersion resource is empty")
    return ".".join(str(part) for part in (
        product_ms >> 16, product_ms & 0xFFFF, product_ls >> 16, product_ls & 0xFFFF,
    ))


def current_source_readiness(repository_root: Path) -> dict[str, Any]:
    """Report approved source preparation without promoting it to an App or runtime binding."""
    root = Path(repository_root).resolve(strict=True)
    source_lock = _r9_source_lock_binding(root)
    reader_path = root / "tools/w26_sealed_data_r9.py"
    review = {
        "status": source_lock["main_review_status"],
        "path": source_lock["main_review_path"],
        "sha256": source_lock["main_review_sha256"],
    }
    return {
        "schema": "w26-current-r9-source-readiness/1",
        "status": "PASS_R9_SOURCE_PROFILE_PREPARATION_ONLY",
        "source_profile": _SOURCE_PROFILE,
        "source_lock": source_lock,
        "reader": {"path": str(reader_path), "exists": reader_path.is_file()},
        "main_review": review,
        "current_app_build": "NOT_RUN",
        "current_app_reader_result": "NOT_AVAILABLE",
        "current_runtime_binding_created": False,
        "T037": "UNVERIFIED",
        "Windows_native_acceptance": "NOT_RUN",
    }


def current_application_readiness(repository_root: Path,
                                  current_application_root: Path | None = None) -> dict[str, Any]:
    """Keep approved R9 source preparation separate from an actual R9 App artifact."""
    source = current_source_readiness(repository_root)
    root = Path(current_application_root) if current_application_root is not None else None
    reader_path = Path(source["reader"]["path"])
    if not reader_path.is_file():
        status = "NOT_READY_R9_SEMANTIC_READER_PENDING"
    elif root is None:
        status = "NOT_READY_CURRENT_R9_APP_BUILD_PENDING"
    elif not root.is_absolute() or not root.is_dir():
        status = "NOT_READY_CURRENT_R9_APP_ROOT_UNAVAILABLE"
    else:
        status = "READY_FOR_EXACT_R9_READER_CALL_ONLY"
    return {
        "schema": "w26-current-r9-app-readiness/1",
        "status": status,
        "source_preparation": source,
        "current_application_root": str(root) if root is not None else None,
        "actual_app_reader_result": "NOT_AVAILABLE",
        "runtime_binding_created": False,
    }


def _current_r9_loader_sources(*, reader: Any, target_project_path: Path) -> dict[str, Any]:
    """Build the exact R9 reader closure from production W26 source records."""
    from tools import w26_runtime_actor
    from tools.w26_prepare_current_inputs import _helpers

    loader_sources = w26_runtime_actor.source_fingerprints(w26_runtime_actor.APP_LIFECYCLE)
    helper = _helpers()
    for path in (
        Path(target_project_path),
        Path(__file__).with_name("w26_target_inputs.py"),
        application.CURRENT175_R4_SOURCE_LOCK,
        application.CURRENT175_R5_SOURCE_LOCK,
        application.CURRENT175_R6_SOURCE_LOCK,
        application.CURRENT175_R7_SOURCE_LOCK,
        application.CURRENT175_R8_SOURCE_LOCK,
        application.CURRENT175_R9_SOURCE_LOCK,
        Path(reader.__file__).resolve(strict=True),
    ):
        row = helper.raw_file(Path(path).resolve(strict=True))[1]
        loader_sources[row["path"]] = row
    return loader_sources


def verify_current_w26_inputs(*, current_application_root: Path | None,
                              target_inputs_record: Mapping[str, Any],
                              target_project_path: Path,
                              repository_root: Path) -> dict[str, Any]:
    """Build and verify a profile only from a real R9 App producer root."""
    readiness = current_application_readiness(repository_root, current_application_root)
    if readiness["status"] != "READY_FOR_EXACT_R9_READER_CALL_ONLY":
        raise CurrentProfileNotReady(readiness)
    import importlib
    from tools.w26_target_inputs import verify_targets

    reader = importlib.import_module("tools.w26_sealed_data_r9")
    loader_sources = _current_r9_loader_sources(
        reader=reader, target_project_path=target_project_path,
    )
    descriptor = reader.build_current_application_profile(
        Path(current_application_root).resolve(strict=True), loader_sources, reader.BASE,
    )
    if (not isinstance(descriptor, Mapping)
            or descriptor.get("schema") != "w26-current-installed-application/1"
            or descriptor.get("source_profile") != _SOURCE_PROFILE):
        raise RuntimeError("R9 producer did not emit the exact current175-r9 profile descriptor")
    profile = reader.current_application_profile(dict(descriptor), reader.BASE)
    profile = _validate_profile(profile, _SOURCE_PROFILE)
    source_lock = _r9_source_lock_binding(Path(repository_root))
    targets = verify_targets(dict(target_inputs_record), Path(target_project_path))
    return {
        "application_profile_descriptor": dict(descriptor),
        "application_profile": profile,
        "target_inputs": targets,
        "source_profile_proof": _build_w26_source_profile_proof(descriptor, profile, source_lock),
    }


def build_from_current_w26(*, repository_root: Path, task_root: Path,
                           task_root_proof: Mapping[str, Any], target: str,
                           current_application_root: Path | None,
                           target_inputs_record: Mapping[str, Any],
                           target_project_path: Path, installed_identity: Mapping[str, Any],
                           matrix_path: Path) -> dict[str, Any]:
    """Build a runtime candidate only after the exact actual R9 App passes its reader."""
    verified = verify_current_w26_inputs(
        current_application_root=current_application_root,
        target_inputs_record=target_inputs_record,
        target_project_path=target_project_path,
        repository_root=repository_root,
    )
    return _build_runtime_binding_from_verified_outputs(
        repository_root=repository_root, task_root=task_root, task_root_proof=task_root_proof,
        target=target, source_profile_proof=verified["source_profile_proof"],
        current_application_profile=verified["application_profile"],
        current_target_inputs=verified["target_inputs"],
        installed_identity=installed_identity, matrix_path=matrix_path,
    )


def _read_frozen_original_matrix(matrix_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    source = Path(matrix_path)
    is_junction = getattr(source, "is_junction", lambda: False)
    if source.is_symlink() or is_junction():
        raise RuntimeError("original Windows matrix cannot be a reparse-point alias")
    path = source.resolve(strict=True)
    if path.name != ORIGINAL_MATRIX_NAME:
        raise RuntimeError("original Windows matrix must use the exact frozen source filename")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size <= 0 or before.st_size > 4 * 1024 * 1024:
            raise RuntimeError("original Windows matrix is not a bounded regular file")
        chunks = []
        count = 0
        while True:
            block = os.read(fd, 64 * 1024)
            if not block:
                break
            count += len(block)
            if count > 4 * 1024 * 1024:
                raise RuntimeError("original Windows matrix exceeded its fixed byte bound")
            chunks.append(block)
        raw = b"".join(chunks)
        after = os.fstat(fd)
        named = path.lstat()
        row_key = lambda row: (row.st_dev, row.st_ino, row.st_mode, row.st_size,
                               row.st_mtime_ns, row.st_ctime_ns)
        if (row_key(before) != row_key(after) or row_key(before) != row_key(named)
                or stat.S_ISLNK(named.st_mode) or count != before.st_size):
            raise RuntimeError("original Windows matrix changed or was aliased while being read")
    finally:
        os.close(fd)
    digest = hashlib.sha256(raw).hexdigest()
    if digest != ORIGINAL_MATRIX_SHA256:
        raise RuntimeError("original Windows matrix differs from the frozen 60-case source checkpoint")
    record = {"path": str(path), "bytes": len(raw), "sha256": digest,
              "device": int(after.st_dev), "file_id": int(after.st_ino)}
    try:
        value = json.loads(raw.decode("utf-8", "strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("original Windows acceptance matrix is not valid UTF-8 JSON") from exc
    required = [row for row in value.get("tests", []) if row.get("required") is True]
    if value.get("count") != 60 or len(required) != 60 or len(value.get("tests", [])) != 60:
        raise RuntimeError("original Windows acceptance matrix is not the exact 60 required rows")
    if (any(not isinstance(row, Mapping) or not isinstance(row.get("id"), str)
            or not isinstance(row.get("targets"), Mapping) for row in required)
            or len({row["id"] for row in required}) != 60):
        raise RuntimeError("original matrix required rows do not have 60 unique target identities")
    return value, record


def _validate_original_matrix(matrix_path: Path, target: str) -> dict[str, Any]:
    value, record = _read_frozen_original_matrix(matrix_path)
    required = [row for row in value["tests"] if row["required"] is True]
    target_rows = [row.get("targets", {}).get(target) for row in required]
    if any(not isinstance(row, Mapping) or row.get("status") != "NOT_RUN"
           or row.get("evidence") != [] for row in target_rows):
        raise RuntimeError("original matrix changed or already contains target acceptance state")
    return {"path": record["path"], "bytes": record["bytes"],
            "sha256": record["sha256"], "required_rows": 60, "target_status": "NOT_RUN"}


def project_windows_matrix_route(*, repository_root: Path, matrix_path: Path) -> dict[str, Any]:
    """Prepare target routing while keeping the original 60-row matrix intact."""
    repo = Path(repository_root).resolve(strict=True)
    expected_matrix = (
        repo.parent / "_state/windows-restored-host-current-20261014/ORIGINAL_WINDOWS_REQUIRED_MATRIX.json"
    )
    supplied_matrix = Path(matrix_path).resolve(strict=True)
    if supplied_matrix != expected_matrix.resolve(strict=True):
        raise RuntimeError("Windows route projection requires the exact frozen V3 matrix path")
    source_readiness = current_application_readiness(repo)
    value, record = _read_frozen_original_matrix(supplied_matrix)
    required_rows = []
    for row in value["tests"]:
        if row["required"] is not True:
            raise RuntimeError("Windows route projection refuses a non-required matrix row")
        required_rows.append(row)
    if len(required_rows) != 60:
        raise RuntimeError("Windows route projection requires all 60 frozen matrix rows")

    target_routes = {}
    for target in sorted(TARGET_BUILDS):
        for row in required_rows:
            target_state = row["targets"].get(target)
            if (not isinstance(target_state, Mapping)
                    or target_state.get("status") != "NOT_RUN"
                    or target_state.get("evidence") != []):
                raise RuntimeError("Windows route projection found changed target acceptance evidence")
        target_routes[target] = {
            "comsol_build": TARGET_BUILDS[target],
            "test_ids": [row["id"] for row in required_rows],
            "route_count": 60,
            "route_status": "NOT_RUN",
            "evidence": [],
            "runtime_id": None,
            "binding_id": None,
            "process_creation": "NOT_RUN",
            "COMSOL_or_JVM": "NOT_RUN",
        }
    if sum(route["route_count"] for route in target_routes.values()) != 120:
        raise RuntimeError("Windows route projection must preserve 60 rows for each of two targets")
    return {
        "schema": "cv3-windows-current-installed-profile-r9-route-preparation/1",
        "status": "NOT_READY_CURRENT_R9_APP_REQUIRED",
        "matrix": {
            "path": record["path"], "bytes": record["bytes"],
            "sha256": record["sha256"], "required_rows": 60,
        },
        # Preserve every original matrix field/row once; the routing map below
        # refers to those immutable IDs and is not a second acceptance table.
        "original_matrix": value,
        "source_profile_preparation": source_readiness["source_preparation"]["source_lock"],
        "application_readiness": source_readiness,
        "target_routes": target_routes,
        "projected_route_count": 120,
        "target_statuses": {target: "NOT_RUN" for target in sorted(TARGET_BUILDS)},
        "runtime_binding_created": False,
        "remote_preparation": "NOT_AUTHORIZED_BY_THIS_PLAN",
        "native_execution": "NOT_RUN",
    }


def _build_runtime_binding_from_verified_outputs(
        *, repository_root: Path, task_root: Path, task_root_proof: Mapping[str, Any],
        target: str, source_profile_proof: Mapping[str, Any],
        current_application_profile: Mapping[str, Any], current_target_inputs: Mapping[str, Any],
        installed_identity: Mapping[str, Any], matrix_path: Path) -> dict[str, Any]:
    """Bind current W26 source/profile, installed COMSOL identity, producer, and task root.

    The application profile and target inputs must be outputs of the existing
    W26 readers. This helper returns a software binding; it creates no
    directory, once file, or process.
    """
    if target not in TARGET_BUILDS:
        raise RuntimeError("unsupported Windows COMSOL target")
    repo = Path(repository_root).resolve(strict=True)
    root = Path(task_root).resolve(strict=True)
    if root == repo or root.is_relative_to(repo):
        raise RuntimeError("current runtime task root must be outside the source checkout")
    proof = _validate_root_proof(root, task_root_proof)
    if os.name == "nt":
        observed_proof = capture_windows_task_root_proof(root)
        if observed_proof != proof:
            raise RuntimeError("current task-root full identity or DACL differs from the submitted proof")
        proof = observed_proof
    source_proof = _validate_w26_source_profile_proof(source_profile_proof, current_application_profile)
    source_profile = source_proof["source_profile"]
    profile = _validate_profile(current_application_profile, source_profile)
    target_inputs = _validate_target_inputs(current_target_inputs)
    installed = _validate_installed_identity(target, installed_identity)
    matrix = _validate_original_matrix(matrix_path, target)
    sources = source_manifest(repo)

    target_runtime_root = root / "current-installed" / target.replace("/", "-")
    session_state_root = target_runtime_root / "session-state"
    preferences = target_runtime_root / "preferences"
    digest_input = {
        "target": target,
        "runtime_id": installed["runtime_id"],
        "comsol_version": installed["comsol_version"],
        "version_identity": {
            "catalog_version": installed["catalog_version"],
            "full_product_version": installed["full_product_version"],
            "build_number": installed["build_number"],
        },
        "application_profile_sha256": _sha(canonical_bytes(profile)),
        "target_inputs_sha256": _sha(canonical_bytes(target_inputs)),
        "source_profile_proof": source_proof,
        "installed_identity": installed,
        "root_proof": proof,
        "source_manifest": sources,
        "matrix_sha256": matrix["sha256"],
        "target_runtime_root": str(target_runtime_root),
    }
    runtime_digest = _sha(canonical_bytes(digest_input))
    binding_id = "windows-current-profile-binding:" + runtime_digest
    return {
        "schema": SCHEMA,
        "status": "SOFTWARE_BINDING_ONLY_PENDING_MAIN_REVIEW_NO_NATIVE_AUTHORITY",
        "scope": "W26_SOURCE_ARTIFACT_PROFILE_AND_WINDOWS_INSTALL_METADATA_BINDING_ONLY",
        "target": target,
        "runtime_id": installed["runtime_id"],
        "binding_id": binding_id,
        "binding_digest": runtime_digest,
        "source_profile": source_profile,
        "source_profile_proof": source_proof,
        "application_profile": profile,
        "application_artifact_profile": {
            "status": "PASS_W26_R9_BUILD_ARTIFACT_PROFILE_ONLY",
            "source_profile": source_profile,
            "semantic_reader": source_proof["verified_by"],
            "artifact_profile_sha256": source_proof["artifact_profile_sha256"],
            "wheel_artifact_record": profile["wheel"],
        },
        "installed_python_application": {
            "status": "NOT_CHECKED",
            "installed_venv_identity": "NOT_CHECKED",
            "installed_distribution_RECORD": "NOT_CHECKED",
            "note": "This binding verifies W26 source/build artifact records only; it does not inspect or certify an installed Windows Python venv.",
        },
        "application_profile_sha256": digest_input["application_profile_sha256"],
        "current_target_inputs_sha256": digest_input["target_inputs_sha256"],
        "installed_identity": installed,
        "repository_root": str(repo),
        "source_manifest": sources,
        "task_root": str(root),
        "task_root_proof": proof,
        "target_runtime_root": str(target_runtime_root),
        "runtime": {
            "runtime_id": installed["runtime_id"],
            "comsol_version": installed["comsol_version"],
            "catalog_version": installed["catalog_version"],
            "full_product_version": installed["full_product_version"],
            "build_number": installed["build_number"],
            "installation_root": installed["installation_root"],
            "java_executable": installed["java_executable"]["path"],
            "classpath": [row["path"] for row in installed["classpath"]],
            "preferences_dir": str(preferences),
            "session_state_root": str(session_state_root),
        },
        "original_windows_matrix": matrix,
        "gates": {
            "T037_task_specific_isolation": "UNVERIFIED",
            "JDK_target_compatibility": "UNVERIFIED",
            "W26_application_artifact_profile": "PASS_SOURCE_PROFILE_ONLY",
            "Windows_installed_python_venv": "NOT_CHECKED",
            "Windows_installed_python_RECORD": "NOT_CHECKED",
            "Main_native_once": "NOT_CREATED",
            "original_Popen_or_Windows_handle": "NOT_BORN",
            "COMSOL_or_JVM": "NOT_RUN",
            "replay": False,
        },
    }


def verify_runtime_binding(binding: Mapping[str, Any], **inputs: Any) -> dict[str, Any]:
    """Re-run both W26 semantic readers before confirming this current binding."""
    expected = build_from_current_w26(**inputs)
    if json.loads(canonical_bytes(dict(binding))) != expected:
        raise RuntimeError("current installed-profile runtime binding drifted")
    return expected


def make_session_runtime_config(binding: Mapping[str, Any]) -> SessionRuntimeConfig:
    """Create the immutable production ControlDaemon runtime value; no process is born."""
    if (binding.get("schema") != SCHEMA
            or binding.get("status") != "SOFTWARE_BINDING_ONLY_PENDING_MAIN_REVIEW_NO_NATIVE_AUTHORITY"):
        raise RuntimeError("runtime binding is not the closed software-only candidate")
    runtime = binding.get("runtime")
    installed = binding.get("installed_identity")
    if not isinstance(runtime, Mapping) or not isinstance(installed, Mapping):
        raise RuntimeError("runtime binding lacks the exact immutable session runtime")
    target = binding.get("target")
    if not isinstance(target, str) or target not in TARGET_BUILDS:
        raise RuntimeError("runtime binding target is not the exact Windows product profile")
    expected_catalog_version = TARGET_CATALOG_VERSIONS[target]
    expected_full_product_version = TARGET_BUILDS[target]
    expected_build_number = TARGET_BUILD_NUMBERS[target]
    if (installed.get("target") != target
            or installed.get("schema") != "windows-current-comsol-install-identity/2"
            or installed.get("comsol_version") != expected_catalog_version
            or installed.get("catalog_version") != expected_catalog_version
            or installed.get("full_product_version") != expected_full_product_version
            or type(installed.get("build_number")) is not int
            or installed.get("build_number") != expected_build_number
            or runtime.get("comsol_version") != expected_catalog_version
            or runtime.get("catalog_version") != expected_catalog_version
            or runtime.get("full_product_version") != expected_full_product_version
            or type(runtime.get("build_number")) is not int
            or runtime.get("build_number") != expected_build_number):
        raise RuntimeError("runtime version fields mix the catalog and full product/build domains")
    expected_runtime_id = runtime_id_for_root(Path(installed.get("installation_root", "")))
    if (binding.get("runtime_id") != expected_runtime_id
            or installed.get("runtime_id") != expected_runtime_id
            or runtime.get("runtime_id") != expected_runtime_id):
        raise RuntimeError("runtime binding identifier is not the production installation-root identity")
    expected_runtime_values = {
        "comsol_version": installed.get("comsol_version"),
        "installation_root": installed.get("installation_root"),
        "java_executable": installed.get("java_executable", {}).get("path"),
        "classpath": [row.get("path") for row in installed.get("classpath", [])],
        "preferences_dir": str(Path(binding.get("target_runtime_root", "")) / "preferences"),
        "session_state_root": str(Path(binding.get("target_runtime_root", "")) / "session-state"),
    }
    if any(runtime.get(key) != value for key, value in expected_runtime_values.items()):
        raise RuntimeError("runtime configuration differs from its exact installed identity")
    digest_input = {
        "target": binding.get("target"),
        "runtime_id": expected_runtime_id,
        "comsol_version": installed.get("catalog_version"),
        "version_identity": {
            "catalog_version": installed.get("catalog_version"),
            "full_product_version": installed.get("full_product_version"),
            "build_number": installed.get("build_number"),
        },
        "application_profile_sha256": binding.get("application_profile_sha256"),
        "target_inputs_sha256": binding.get("current_target_inputs_sha256"),
        "source_profile_proof": binding.get("source_profile_proof"),
        "installed_identity": installed,
        "root_proof": binding.get("task_root_proof"),
        "source_manifest": binding.get("source_manifest"),
        "matrix_sha256": binding.get("original_windows_matrix", {}).get("sha256"),
        "target_runtime_root": binding.get("target_runtime_root"),
    }
    binding_digest = _sha(canonical_bytes(digest_input))
    if (binding.get("binding_digest") != binding_digest
            or binding.get("binding_id") != "windows-current-profile-binding:" + binding_digest
            or binding.get("source_profile_proof", {}).get("source_profile") != _SOURCE_PROFILE
            or binding.get("application_artifact_profile", {}).get("source_profile") != _SOURCE_PROFILE
            or binding.get("installed_python_application", {}).get("status") != "NOT_CHECKED"):
        raise RuntimeError("runtime binding digest or separate application profile scope differs")
    return SessionRuntimeConfig(
        runtime_id=expected_runtime_id,
        comsol_version=runtime["catalog_version"],
        installation_root=Path(runtime["installation_root"]),
        java_executable=Path(runtime["java_executable"]),
        classpath=tuple(Path(item) for item in runtime["classpath"]),
        preferences_dir=Path(runtime["preferences_dir"]),
        session_state_root=Path(runtime["session_state_root"]),
    )


def assert_shared_owned_preferences(runtime: SessionRuntimeConfig, project_id: str, session_id: str,
                                    server_identity: Any, *, platform_name: str = "win32") -> Path:
    """Use the production Server/Worker preference verifier for one exact birth."""
    from comsol_mcp._session_server import owned_server_preferences_directory

    observed = owned_server_preferences_directory(
        runtime, project_id, session_id, server_identity, platform_name=platform_name,
    )
    session_key = hashlib.sha256(f"{project_id}\0{session_id}".encode("utf-8")).hexdigest()
    expected = (runtime.session_state_root / "sessions" / session_key /
                "owned-server" / "preferences").resolve(strict=True)
    if observed != expected:
        raise RuntimeError("Core Server and Worker did not resolve the same owned preferences directory")
    return observed

