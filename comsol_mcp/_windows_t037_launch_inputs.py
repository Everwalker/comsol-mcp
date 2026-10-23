"""Closed normal-producer inputs bound to one live original T037 factory."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
import stat
import threading
from types import MappingProxyType
from typing import Any, Mapping

from ._execution_contract import ExecutionContractError
from . import _windows_worker_bootstrap as prepared

_INPUT_SEAL = object()
_BASE_KEYS = frozenset(("SystemRoot", "WINDIR", "SystemDrive", "PATH", "TEMP", "TMP"))
_ROLE_KEYS = {"server": _BASE_KEYS, "worker": _BASE_KEYS | {"JAVA_HOME", "COMSOL_MCP_WORKER_TOKEN"}}
_SHA = re.compile(r"^[0-9a-f]{64}$")


def _fail(message: str) -> None:
    raise ExecutionContractError("T037_LAUNCH_INPUTS_INVALID", message,
                                 safe_retry=False, stage="pre_dispatch")


def _canon_path(value: str) -> str:
    if (type(value) is not str or not ntpath.isabs(value) or value.startswith("\\\\")
            or ntpath.normpath(value) != value or "\x00" in value):
        _fail("launch input requires a canonical absolute local Windows path")
    return ntpath.normpath(value)


def _same_path(left: str, right: str) -> bool:
    return ntpath.normcase(_canon_path(left)) == ntpath.normcase(_canon_path(right))


def _manifest(factory: Any) -> prepared.PreparedLaunchManifest:
    from ._windows_t037 import T037SessionProcessFactory
    if (type(factory) is not T037SessionProcessFactory
            or type(factory.profile.prepared_launch) is not prepared.PreparedLaunchManifest
            or factory.profile.prepared_launch._seal is not prepared._MANIFEST_SEAL):
        _fail("normal T037 producer requires the exact loaded protected prepared-launch manifest")
    return factory.profile.prepared_launch


@dataclass(frozen=True)
class LaunchInputs:
    factory: Any = field(repr=False, compare=False)
    profile: Any = field(repr=False, compare=False)
    role: str
    argv: tuple[str, ...]
    executable: str
    cwd: str
    environment: Mapping[str, str] = field(repr=False)
    environment_block: str = field(repr=False)
    stdio: tuple[Any, Any, Any] = field(repr=False, compare=False)
    creationflags: int
    start_new_session: bool
    text: bool
    source_root: str
    source_bindings: tuple[tuple[str, str, str], ...]
    classpath: tuple[str, ...]
    private_token: str | None = field(default=None, repr=False, compare=False)
    _seal: object = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if self._seal is not _INPUT_SEAL:
            _fail("launch inputs must be built by the normal Server/Worker producer")
        if self.role not in {"server", "worker"} or not self.argv or self.argv[0] != self.executable:
            _fail("launch input role/executable/argv is malformed")
        if set(self.environment) != _ROLE_KEYS[self.role]:
            _fail("launch environment keys differ from the fixed role contract")
        if self.role == "server" and ("JAVA_HOME" in self.environment or self.private_token is not None):
            _fail("Server must not receive the Worker JDK or private token")
        if self.role == "worker" and (self.private_token is None
                or self.environment.get("COMSOL_MCP_WORKER_TOKEN") != self.private_token):
            _fail("Worker token must remain the exact private Worker token")

    @property
    def argv_sha256(self) -> str:
        return hashlib.sha256(json.dumps(list(self.argv), ensure_ascii=False,
            separators=(",", ":")).encode("utf-8")).hexdigest()

    @property
    def environment_sha256(self) -> str:
        return hashlib.sha256(json.dumps(dict(self.environment), sort_keys=True,
            separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()

    def validate_submission(self, factory, command, options, role):
        if (factory is not self.factory or factory.profile is not self.profile
                or role != self.role or tuple(command) != self.argv
                or options.get("executable", self.executable) != self.executable
                or not _same_path(options.get("cwd"), self.cwd)
                or type(options.get("env")) is not dict
                or options["env"] != dict(self.environment)
                or (options.get("stdin"), options.get("stdout"), options.get("stderr")) != self.stdio
                or options.get("creationflags", 0) != self.creationflags
                or options.get("start_new_session", False) is not self.start_new_session
                or options.get("text", False) is not self.text
                or options.get("close_fds", True) is not True):
            _fail("normal producer submission differs from its original sealed role inputs")
        if any(key not in {"cwd", "stdin", "stdout", "stderr", "env", "executable",
                           "creationflags", "start_new_session", "text", "close_fds"} for key in options):
            _fail("normal producer supplied an unknown process option")

    def validate_native_storage(self, child, api):
        if (child._original_factory is not self.factory or child._original_profile is not self.profile
                or child._original_role != self.role or tuple(child.args) != self.argv
                or not _same_path(child._creation_parameters[0], self.executable)):
            _fail("retained native child lost its exact normal producer input object")
        C = api.C
        command_line = child._call_storage[2]
        environment = child._call_storage[3]
        if (command_line.value != __import__("subprocess").list2cmdline(list(self.argv))
                or child._create_arguments[1] != self.executable
                or child._create_arguments[8] != self.cwd
                or environment is None
                or C.wstring_at(C.addressof(environment), len(self.environment_block)) != self.environment_block
                or C.cast(child._create_arguments[7], C.c_void_p).value != C.addressof(environment)):
            _fail("original CreateProcess storage differs from exact argv/env/cwd inputs")

    def verify_protected_material(self, factory):
        """Bind prepared files to the active original held-A reader before native birth."""
        manifest = _manifest(factory)
        if (self.factory is not factory or self.profile is not factory.profile
                or self.source_root != manifest.vendor_source_root):
            _fail("prepared material verification lost its exact normal factory/source binding")
        from ._windows_t037_targets import _descriptor, _require_held_finite_a
        held = _require_held_finite_a(factory)
        reader = held.reader

        def read_pin(path, *, directory, file_id, security_sha256, size=None, content_sha256=None):
            fact = reader.read(path, directory=directory, content=content_sha256 is not None)
            fingerprint = fact.get("fingerprint")
            if (type(fingerprint) is not tuple or len(fingerprint) < 3
                    or type(fingerprint[0]) is not tuple or len(fingerprint[0]) != 2
                    or tuple(map(str, fingerprint[0])) != tuple(file_id)
                    or size is not None and fingerprint[1] != size
                    or not directory and fingerprint[-1] != 1):
                _fail("prepared original file identity/extent differs from installer pins")
            if (content_sha256 is not None
                    and fact.get("content_sha256") != content_sha256):
                _fail("prepared original file bytes differ from the protected installer manifest")
            descriptor = fact.get("descriptor")
            if (type(descriptor) is not bytes
                    or hashlib.sha256(descriptor).hexdigest() != security_sha256):
                _fail("prepared original file security differs from installer pins")
            _descriptor(descriptor)
            label = getattr(fact.get("label"), "ace", None)
            if (label is None or label.rid not in (0x2000, 0x3000, 0x4000)
                    or not label.mask & 1 or label.flags & 8):
                _fail("prepared code/source requires an explicit Medium-or-higher no-write-up label")
            return fact

        def installed(relative, *, size, file_id, security_sha256, content_sha256):
            path = ntpath.join(manifest.installation_prefix, *relative.split("/"))
            return read_pin(path, directory=False, file_id=file_id,
                            security_sha256=security_sha256, size=size,
                            content_sha256=content_sha256)

        for row in manifest.vendor_files:
            read_pin(row["source_path"], directory=False, file_id=row["source_file_id"],
                     security_sha256=row["source_security_sha256"], size=row["source_size"],
                     content_sha256=row["source_sha256"])
            installed(row["target"], size=row["target_size"], file_id=row["target_file_id"],
                      security_sha256=row["target_security_sha256"], content_sha256=row["target_sha256"])
        for name in ("java", "javac"):
            row = manifest.jdk_files[name]
            installed(row["target"], size=row["size"], file_id=row["target_file_id"],
                      security_sha256=row["target_security_sha256"], content_sha256=row["sha256"])
        installed(manifest.worker_source_pin["target"], size=manifest.worker_source_pin["size"],
            file_id=manifest.worker_source_pin["target_file_id"],
            security_sha256=manifest.worker_source_pin["target_security_sha256"],
            content_sha256=manifest.worker_source_pin["sha256"])
        for row in manifest.generated_classes:
            installed(row["path"], size=row["size"], file_id=row["file_id"],
                      security_sha256=row["security_sha256"], content_sha256=row["sha256"])
        for role, relative in manifest.role_cwds.items():
            path = ntpath.join(manifest.installation_prefix, *relative.split("/"))
            read_pin(path, directory=True, file_id=manifest.role_cwd_facts[role]["file_id"],
                     security_sha256=manifest.role_cwd_facts[role]["security_sha256"])
            verify_regular_directory(path)
            if any(Path(path).iterdir()):
                _fail("prepared role cwd must remain an empty protected directory")
        return held


def _environment_block(values: Mapping[str, str]) -> str:
    rows = []
    seen = set()
    for key, value in values.items():
        if (type(key) is not str or type(value) is not str or not key or "=" in key
                or "\x00" in key or "\x00" in value or key.casefold() in seen):
            _fail("environment input contains a malformed or case-colliding key/value")
        seen.add(key.casefold())
        rows.append((key, value))
    rows.sort(key=lambda row: row[0].casefold())
    return "\x00".join(f"{key}={value}" for key, value in rows) + "\x00\x00"


def _os_roots(factory):
    roots = getattr(factory, "_launch_os_roots", None)
    if type(roots) is not tuple or len(roots) != 2:
        _fail("original native Windows and System32 roots were not read through the factory provider")
    windows_root, system_root = roots
    windows_root, system_root = _canon_path(windows_root), _canon_path(system_root)
    if (ntpath.normcase(ntpath.dirname(system_root)) != ntpath.normcase(windows_root)
            or ntpath.basename(system_root).casefold() != "system32"):
        _fail("original native Windows/System32 roots have an unexpected relationship")
    return windows_root, system_root


def _bindings(manifest):
    rows = []
    for row in manifest.vendor_files:
        rows.append((row["source_path"], row["source_sha256"], row["target_sha256"]))
    return tuple(rows)


def build_server_inputs(factory, runtime, directories, argv, *, stdio):
    manifest = _manifest(factory)
    if not _same_path(os.fspath(runtime.installation_root), manifest.vendor_source_root):
        _fail("Server runtime root differs from the installer-pinned original COMSOL source")
    image = next(row for row in manifest.vendor_files if row["target"] == manifest.server_image)
    expected_executable = ntpath.join(manifest.installation_prefix, *manifest.server_image.split("/"))
    if not _same_path(argv[0], expected_executable):
        _fail("Server executable does not resolve to the original installer-protected vendor view")
    if image["source_sha256"] != factory.profile.server_image_sha256:
        _fail("prepared Server source differs from the trusted profile image")
    windows_root, system_root = _os_roots(factory)
    temp = _canon_path(os.fspath(directories.temporary))
    environment = {
        "SystemRoot": windows_root, "WINDIR": windows_root,
        "SystemDrive": ntpath.splitdrive(windows_root)[0],
        "PATH": ";".join((ntpath.join(manifest.prepared_vendor_root, "bin", "win64"),
                           system_root, windows_root)),
        "TEMP": temp, "TMP": temp,
    }
    cwd = ntpath.join(manifest.installation_prefix, *manifest.role_cwds["server"].split("/"))
    block = _environment_block(environment)
    import subprocess
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    return LaunchInputs(factory, factory.profile, "server", tuple(argv), argv[0], cwd,
        MappingProxyType(environment), block, tuple(stdio), creationflags, False, False, manifest.vendor_source_root,
        _bindings(manifest), (), None, _INPUT_SEAL)


def build_worker_inputs(factory, paths, *, state_dir, temp_dir, preferences, endpoint,
                        server_lock_root, generation, token, classpath, stdio):
    manifest = _manifest(factory)
    if not _same_path(os.fspath(paths.comsol_root), manifest.vendor_source_root):
        _fail("Worker COMSOL root differs from the installer-pinned original runtime")
    expected_jdk_root = ntpath.dirname(ntpath.dirname(manifest.jdk_files["java"]["source_path"]))
    if not _same_path(os.fspath(paths.jdk_home), expected_jdk_root):
        _fail("Worker JDK root differs from the installer-pinned original JDK source")
    java_pin = manifest.jdk_files["java"]
    verify_regular_file(paths.executable("java"), size=java_pin["size"], sha256=java_pin["sha256"])
    classpath_rows = [row for row in manifest.vendor_files if row["role"] == "classpath"]
    expected_classpath = tuple(row["source_path"] for row in classpath_rows)
    if len(classpath) != len(expected_classpath) or any(not _same_path(str(left), right)
            for left, right in zip(classpath, expected_classpath)):
        _fail("Worker ordered COMSOL classpath differs from the protected prepared manifest")
    for path, row in zip(classpath, classpath_rows):
        verify_regular_file(path, size=row["source_size"], sha256=row["source_sha256"])
    if type(token) is not str or not token or "\x00" in token:
        _fail("Worker token is malformed")
    windows_root, system_root = _os_roots(factory)
    jdk_bin = ntpath.join(os.fspath(paths.jdk_home), "bin")
    comsol_bin = ntpath.join(os.fspath(paths.comsol_root), "bin", "win64")
    temp = _canon_path(os.fspath(temp_dir))
    environment = {
        "SystemRoot": windows_root, "WINDIR": windows_root,
        "SystemDrive": ntpath.splitdrive(windows_root)[0],
        "PATH": ";".join((jdk_bin, comsol_bin, system_root, windows_root)),
        "JAVA_HOME": _canon_path(os.fspath(paths.jdk_home)),
        "TEMP": temp, "TMP": temp, "COMSOL_MCP_WORKER_TOKEN": token,
    }
    class_dir = manifest.prepared_classes_root
    classpath_text = ";".join((class_dir, *map(str, classpath)))
    executable = os.fspath(paths.executable("java"))
    argv = [executable]
    if preferences is not None:
        argv.append(f"-Dcs.prefsdir={preferences}")
    argv.extend(("-cp", classpath_text, "comsol_mcp.worker_java.PersistentComsolWorker"))
    argv.extend(("--port", str(endpoint[1]), "--endpoint-file", os.fspath(state_dir / "worker_endpoint.json"),
                 "--server-lock-root", os.fspath(server_lock_root), "--generation", str(generation)))
    cwd = ntpath.join(manifest.installation_prefix, *manifest.role_cwds["worker"].split("/"))
    environment_block = _environment_block(environment)
    return LaunchInputs(factory, factory.profile, "worker", tuple(argv), executable, cwd,
        MappingProxyType(environment), environment_block, tuple(stdio), 0, True, True, manifest.vendor_source_root,
        _bindings(manifest), tuple(map(str, classpath)), token, _INPUT_SEAL)


def activate_launch_inputs(factory, inputs: LaunchInputs):
    if (type(inputs) is not LaunchInputs or inputs._seal is not _INPUT_SEAL
            or inputs.factory is not factory or inputs.profile is not factory.profile
            or getattr(factory, "_active_launch_input", None) is not None):
        _fail("only one exact same-factory launch input can be active")

    @contextmanager
    def scope():
        slot = {"inputs": inputs, "thread": threading.get_ident(), "consumed": False}
        factory._active_launch_input = slot
        try:
            yield inputs
        except BaseException:
            raise
        else:
            if not slot["consumed"]:
                _fail("normal producer returned without one original factory consumption")
        finally:
            if getattr(factory, "_active_launch_input", None) is slot:
                factory._active_launch_input = None
    return scope()


def consume_launch_inputs(factory, command, options, role):
    manifest = getattr(factory.profile, "prepared_launch", None)
    if manifest is None:
        return None
    slot = getattr(factory, "_active_launch_input", None)
    if (type(slot) is not dict or slot.get("thread") != threading.get_ident()
            or slot.get("consumed") is not False or type(slot.get("inputs")) is not LaunchInputs):
        _fail("prepared T037 launch requires its active original same-thread producer slot")
    inputs = slot["inputs"]
    inputs.validate_submission(factory, command, options, role)
    slot["consumed"] = True
    return inputs


def require_consumed_launch_inputs(factory, inputs):
    slot = getattr(factory, "_active_launch_input", None)
    if (type(slot) is not dict or slot.get("thread") != threading.get_ident()
            or slot.get("consumed") is not True or slot.get("inputs") is not inputs
            or type(inputs) is not LaunchInputs or inputs.factory is not factory):
        _fail("native storage requires the consumed original same-thread producer slot")


def verify_regular_file(path: str | os.PathLike[str], *, size: int, sha256: str) -> None:
    """Bounded no-reparse/hash check used for prepared runtime inputs and classes."""
    target = Path(path)
    if not target.is_absolute() or not _SHA.fullmatch(sha256) or type(size) is not int or size <= 0:
        _fail("prepared file verification inputs are malformed")
    current = target
    while True:
        try:
            info = current.lstat()
        except OSError:
            _fail("prepared source path is missing")
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            _fail("prepared runtime paths cannot contain symlinks or reparse points")
        if current == target:
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != size:
                _fail("prepared runtime source must be the exact regular single-link file")
            before = (info.st_dev, info.st_ino, info.st_size, getattr(info, "st_mtime_ns", None))
        if current.parent == current:
            break
        current = current.parent
    digest = hashlib.sha256()
    read = 0
    try:
        with target.open("rb") as stream:
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                read += len(block)
                if read > size:
                    _fail("prepared runtime source grew during read")
                digest.update(block)
        after_info = target.lstat()
    except OSError:
        _fail("prepared source could not be read")
    after = (after_info.st_dev, after_info.st_ino, after_info.st_size,
             getattr(after_info, "st_mtime_ns", None))
    if read != size or digest.hexdigest() != sha256 or before != after:
        _fail("prepared runtime source identity/content changed")


def verify_regular_directory(path: str | os.PathLike[str]) -> None:
    """Require an absolute ordinary directory and reject aliases in its ancestry."""
    target = Path(path)
    if not target.is_absolute():
        _fail("prepared directory verification requires an absolute path")
    current = target
    while True:
        try:
            info = current.lstat()
        except OSError:
            _fail("prepared directory path is missing")
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400):
            _fail("prepared directory ancestry must contain only ordinary directories")
        if current.parent == current:
            break
        current = current.parent


def verify_manifest_classes(manifest: prepared.PreparedLaunchManifest) -> str:
    root = Path(manifest.prepared_classes_root)
    for directory in (root, root / "comsol_mcp", root / "comsol_mcp" / "worker_java"):
        verify_regular_directory(directory)
    package = root / "comsol_mcp" / "worker_java"
    expected = {row["path"].rsplit("/", 1)[-1]: row for row in manifest.generated_classes}
    try:
        names = {entry.name for entry in package.iterdir()}
    except OSError:
        _fail("prepared Worker class directory cannot be enumerated")
    if names != set(expected):
        _fail("prepared Worker class output set differs from the protected compiler manifest")
    for name, row in expected.items():
        verify_regular_file(package / name, size=row["size"], sha256=row["sha256"])
    return str(root)


def verify_source_classpath(paths, manifest: prepared.PreparedLaunchManifest):
    classpath, _manifest_hash, _jar_count, _sampled_hash = paths.classpath()
    actual = tuple(Path(item) for item in classpath.split(paths.classpath_separator))
    rows = [row for row in manifest.vendor_files if row["role"] == "classpath"]
    expected = tuple(row["source_path"] for row in rows)
    if len(actual) != len(expected) or any(not _same_path(str(left), right) for left, right in zip(actual, expected)):
        _fail("original JavaWorkerPaths classpath differs from the prepared ordered classpath")
    for path, row in zip(actual, rows):
        verify_regular_file(path, size=row["source_size"], sha256=row["source_sha256"])
    return tuple(map(str, actual))


def verify_role_cwd(manifest: prepared.PreparedLaunchManifest, role: str) -> str:
    path = ntpath.join(manifest.installation_prefix, *manifest.role_cwds[role].split("/"))
    target = Path(path)
    verify_regular_directory(target)
    if any(target.iterdir()):
        _fail("prepared role working directory must remain empty")
    return path
