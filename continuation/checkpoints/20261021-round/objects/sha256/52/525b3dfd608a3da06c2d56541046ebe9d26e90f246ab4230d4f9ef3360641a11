"""Bind a current W26 application profile to one installed Windows COMSOL runtime.

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


SCHEMA = "cv3-windows-current-installed-profile-runtime-binding/2"
TARGET_BUILDS = {
    "windows-x64/comsol6.3": "6.3.0.290",
    "windows-x64/comsol6.4": "6.4.0.293",
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
    "tools/w26_application_lifecycle_r6_actor.py",
    "tools/w26_application_sources_175_r4.lock.json",
    "tools/w26_application_sources_175_r5.lock.json",
    "tools/w26_application_sources_175_r6.lock.json",
    "tools/w26_dependency_unit.py",
    "tools/w26_prepare_current_inputs.py",
    "tools/w26_runtime_actor.py",
    "tools/w26_sealed_data.py",
    "tools/w26_sealed_data_r6.py",
    "tools/w26_target_inputs.py",
    "tools/windows_current_installed_profile_runtime_binding_r2.py",
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PROFILE_KEYS = {"wheel", "result", "candidate", "once", "review", "backup_source_sha256"}
_JAVA_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){1,3}\Z")
ORIGINAL_MATRIX_NAME = "ORIGINAL_WINDOWS_REQUIRED_MATRIX.json"
ORIGINAL_MATRIX_SHA256 = "be5d224ea3cc304ddea7d9b3a52d5dfb7b53f1d4e3db592c947cbb7e44829160"


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
            or not isinstance(source_profile, str)
            or re.fullmatch(r"current175-r[1-9][0-9]*", source_profile) is None):
        raise RuntimeError("current W26 application profile verification result is incomplete")
    for name in ("wheel", "result", "candidate", "once", "review"):
        _verify_record(profile[name], label="current W26 " + name)
    if not isinstance(profile["backup_source_sha256"], str) or not _SHA256.fullmatch(profile["backup_source_sha256"]):
        raise RuntimeError("current W26 source-profile digest is malformed")
    wheel = profile["wheel"]
    if not Path(wheel["path"]).name.startswith("comsol_mcp-") or not Path(wheel["path"]).name.endswith("-py3-none-any.whl"):
        raise RuntimeError("current W26 installed application wheel is not the exact universal package")
    return json.loads(canonical_bytes(dict(profile)))


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
    expected_version = TARGET_BUILDS[target]
    if (not isinstance(identity, Mapping)
            or set(identity) != {
                "schema", "status", "target", "comsol_version", "installation_root",
                "desktop_executable", "server_executable", "jdk_home",
            }
            or identity.get("schema") != "windows-current-comsol-install-identity/1"
            or identity.get("status") != "READONLY_INSTALLED_IDENTITY_VERIFIED"
            or identity.get("target") != target
            or identity.get("comsol_version") != expected_version):
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
    expected_major = expected_version.split(".", 1)[0] + "." + expected_version.split(".", 2)[1]
    readme_lines = {line.strip() for line in readme_text.splitlines()}
    if (f"COMSOL {expected_version} README" not in readme_lines
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
    if desktop_version != expected_version or server_version != expected_version:
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
    return {
        "schema": identity["schema"], "status": identity["status"],
        "target": target, "comsol_version": expected_version,
        "installation_root": str(root.resolve(strict=True)),
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


def verify_current_w26_inputs(*, application_profile_descriptor: Mapping[str, Any],
                              target_inputs_record: Mapping[str, Any],
                              target_project_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the existing W26 semantic readers and return their verified outputs."""
    from tools.w26_sealed_data_r6 import current_application_profile
    from tools.w26_target_inputs import verify_targets

    profile = current_application_profile(dict(application_profile_descriptor))
    targets = verify_targets(dict(target_inputs_record), Path(target_project_path))
    return profile, targets


def build_from_current_w26(*, repository_root: Path, task_root: Path,
                           task_root_proof: Mapping[str, Any], target: str,
                           application_profile_descriptor: Mapping[str, Any],
                           target_inputs_record: Mapping[str, Any],
                           target_project_path: Path, installed_identity: Mapping[str, Any],
                           matrix_path: Path) -> dict[str, Any]:
    """Build a runtime candidate only from current, reverified W26 artifacts."""
    profile, targets = verify_current_w26_inputs(
        application_profile_descriptor=application_profile_descriptor,
        target_inputs_record=target_inputs_record,
        target_project_path=target_project_path,
    )
    source_profile = application_profile_descriptor.get("source_profile")
    if not isinstance(source_profile, str):
        raise RuntimeError("W26 application profile descriptor lacks its exact source-profile identity")
    return build_runtime_binding(
        repository_root=repository_root, task_root=task_root, task_root_proof=task_root_proof,
        target=target, source_profile=source_profile,
        current_application_profile=profile, current_target_inputs=targets,
        installed_identity=installed_identity, matrix_path=matrix_path,
    )


def _validate_original_matrix(matrix_path: Path, target: str) -> dict[str, Any]:
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
    if value.get("count") != 60 or len(required) != 60:
        raise RuntimeError("original Windows acceptance matrix is not the exact 60 required rows")
    target_rows = [row.get("targets", {}).get(target) for row in required]
    if any(not isinstance(row, Mapping) or row.get("status") != "NOT_RUN"
           or row.get("evidence") != [] for row in target_rows):
        raise RuntimeError("original matrix changed or already contains target acceptance state")
    return {"path": record["path"], "bytes": record["bytes"],
            "sha256": record["sha256"], "required_rows": 60, "target_status": "NOT_RUN"}


def build_runtime_binding(*, repository_root: Path, task_root: Path, task_root_proof: Mapping[str, Any],
                          target: str, source_profile: str, current_application_profile: Mapping[str, Any],
                          current_target_inputs: Mapping[str, Any], installed_identity: Mapping[str, Any],
                          matrix_path: Path) -> dict[str, Any]:
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
        "comsol_version": installed["comsol_version"],
        "application_profile_sha256": _sha(canonical_bytes(profile)),
        "target_inputs_sha256": _sha(canonical_bytes(target_inputs)),
        "installed_identity": installed,
        "root_proof": proof,
        "source_manifest": sources,
        "matrix_sha256": matrix["sha256"],
        "target_runtime_root": str(target_runtime_root),
    }
    runtime_digest = _sha(canonical_bytes(digest_input))
    return {
        "schema": SCHEMA,
        "status": "SOFTWARE_BINDING_ONLY_PENDING_MAIN_REVIEW_NO_NATIVE_AUTHORITY",
        "scope": "CURRENT_SOURCE_AND_INSTALLED_RUNTIME_BINDING_ONLY",
        "target": target,
        "source_profile": source_profile,
        "application_profile": profile,
        "application_profile_sha256": digest_input["application_profile_sha256"],
        "current_target_inputs_sha256": digest_input["target_inputs_sha256"],
        "installed_identity": installed,
        "repository_root": str(repo),
        "source_manifest": sources,
        "task_root": str(root),
        "task_root_proof": proof,
        "target_runtime_root": str(target_runtime_root),
        "runtime_id": "windows-current-" + target.rsplit("/", 1)[1] + "-" + runtime_digest[:20],
        "runtime": {
            "comsol_version": installed["comsol_version"],
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
            "Main_native_once": "NOT_CREATED",
            "original_Popen_or_Windows_handle": "NOT_BORN",
            "COMSOL_or_JVM": "NOT_RUN",
            "replay": False,
        },
    }


def verify_runtime_binding(binding: Mapping[str, Any], **inputs: Any) -> dict[str, Any]:
    """Recompute every profile/source/product/root binding before later admission."""
    expected = build_runtime_binding(**inputs)
    if json.loads(canonical_bytes(dict(binding))) != expected:
        raise RuntimeError("current installed-profile runtime binding drifted")
    return expected


def make_session_runtime_config(binding: Mapping[str, Any]) -> SessionRuntimeConfig:
    """Create the immutable production ControlDaemon runtime value; no process is born."""
    if (binding.get("schema") != SCHEMA
            or binding.get("status") != "SOFTWARE_BINDING_ONLY_PENDING_MAIN_REVIEW_NO_NATIVE_AUTHORITY"):
        raise RuntimeError("runtime binding is not the closed software-only candidate")
    runtime = binding.get("runtime")
    if not isinstance(runtime, Mapping):
        raise RuntimeError("runtime binding lacks the exact immutable session runtime")
    return SessionRuntimeConfig(
        runtime_id=binding["runtime_id"],
        comsol_version=runtime["comsol_version"],
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

