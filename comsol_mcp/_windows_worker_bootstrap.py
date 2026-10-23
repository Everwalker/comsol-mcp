"""Closed, installer-owned prepared Worker/vendor launch material.

The plan describes finite source files and fixed paths. It never supplies a
command, executable, Java source, option list, process owner, or runtime proof.
The protected manifest written after actual compiler retirement is data only.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import ntpath
import re
import stat
from types import MappingProxyType
import xml.etree.ElementTree as ET

from . import _windows_host_bootstrap as b

RECIPE_SCHEMA = "comsol-mcp.windows-prepared-launch-recipe/1"
MANIFEST_SCHEMA = "comsol-mcp.windows-prepared-launch-manifest/1"
MANIFEST_REFERENCE_SCHEMA = "comsol-mcp.windows-prepared-launch-manifest-reference/1"
WORKER_SOURCE = "python312/tools/Lib/site-packages/comsol_mcp/worker_java/PersistentComsolWorker.java"
WORKER_SOURCE_SHA256 = "ac626ffa3d505906f0844880eb6a96a3a570fbccb44a83c21be463be29966a37"
COMPILER = "jdk11/bin/javac.exe"
JAVA = "jdk11/bin/java.exe"
VENDOR_PREFIX = "prepared/vendor/"
OUTPUT_DIR = "prepared/bootstrap/classes"
MANIFEST_PATH = "prepared/launch-manifest.json"
INSTALLER_CWD = "prepared/roles/installer-cwd"
SERVER_CWD = "prepared/roles/server-cwd"
WORKER_CWD = "prepared/roles/worker-cwd"
COMPILER_TEMP = "service-state/prepared-launch/compiler-temp"
SERVER_TEMP = "service-state/prepared-launch/server-temp"
WORKER_TEMP = "service-state/prepared-launch/worker-temp"
MAIN_CLASS = "comsol_mcp.worker_java.PersistentComsolWorker"
JAVA_OPTIONS = ("-proc:none", "-encoding", "UTF-8")
MAX_VENDOR_FILES = 2048
MAX_CLASS_OUTPUTS = 4096
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
_MANIFEST_SEAL = object()


def _within_rel(path: str, root: str) -> bool:
    p, r = path.casefold(), root.casefold()
    return p == r or p.startswith(r + "/")


@dataclass(frozen=True)
class VendorFile:
    source: str
    target: str
    size: int
    sha256: str
    role: str


@dataclass(frozen=True)
class PreparedLaunchRecipe:
    vendor_source_root: str
    vendor_files: tuple[VendorFile, ...]
    classpath: tuple[str, ...]
    server_image: str
    server_config: str
    version_file: str
    worker_source: str = WORKER_SOURCE
    compiler: str = COMPILER
    output_dir: str = OUTPUT_DIR
    manifest_path: str = MANIFEST_PATH
    installer_cwd: str = INSTALLER_CWD
    server_cwd: str = SERVER_CWD
    worker_cwd: str = WORKER_CWD
    compiler_temp: str = COMPILER_TEMP
    server_temp: str = SERVER_TEMP
    worker_temp: str = WORKER_TEMP
    schema: str = RECIPE_SCHEMA

    @property
    def vendor_targets(self) -> tuple[str, ...]:
        return tuple(row.target for row in self.vendor_files)

    @property
    def directories(self) -> tuple[str, ...]:
        paths = {self.output_dir, self.manifest_path.rsplit("/", 1)[0], self.installer_cwd,
                 self.server_cwd, self.worker_cwd, self.compiler_temp, self.server_temp, self.worker_temp}
        paths.update(row.target.rsplit("/", 1)[0] for row in self.vendor_files)
        result = {""}
        for path in paths:
            parts = path.split("/")
            result.update("/".join(parts[:index]) for index in range(1, len(parts) + 1))
        return tuple(sorted(result, key=lambda path: (path.count("/"), len(path), path)))

    def target_for_source(self, source: str) -> str:
        rows = [row.target for row in self.vendor_files if row.source.casefold() == source.casefold()]
        if len(rows) != 1:
            b.fail("PREPARED_VENDOR_SOURCE", "one original vendor source mapping required")
        return rows[0]


class CompilerRecord:
    """Private same-owner record for one suspended fixed compiler child."""
    def __init__(self, owner, recipe, argv, environment, cwd):
        self.owner, self.recipe = owner, recipe
        self.argv, self.environment, self.cwd = tuple(argv), dict(environment), cwd
        self.job = self.stdio = self.process = self.thread = None
        self.process_info = self.startup = self.environment_buffer = self.command_buffer = None
        self.create_call = self.assign_call = self.resume_call = self.wait_call = None
        self.exit_call = self.job_query_call = self.birth_call = None
        self.exit_code = self.birth = None
        self.job_total = self.job_active = None
        self.output_capture = None
        self.capture_complete = False
        self.capture_nodes = None
        self.capture_file_paths = None
        self.capture_bindings = None
        self.namespace_before_capture = None
        self.source_sha256 = self.classpath_sha256 = None
        self.process_creation_attempted = False

    def retirement_verified(self, bootstrap):
        owner = self.owner
        handles = (self.job, self.stdio, self.process, self.thread)
        if (bootstrap.owner is not owner or any(type(handle) is not b._Handle or handle not in owner.handles
                or handle.owner is not owner or not handle.closed or handle.close_call is None
                or handle.close_call.state != "SUCCEEDED" for handle in handles)):
            return False
        if (self.create_call not in owner.calls or self.create_call.state != "SUCCEEDED"
                or self.create_call.name != "CreateProcessW" or self.process.opened is not self.create_call
                or self.thread.opened is not self.create_call or not self.process.result_from_output
                or not self.thread.result_from_output or self.process.parent is not self.job
                or self.assign_call not in owner.calls or self.assign_call.name != "AssignProcessToJobObject"
                or self.assign_call.state != "SUCCEEDED"):
            return False
        if (self.resume_call not in owner.calls or self.resume_call.name != "ResumeThread"
                or self.resume_call.mode != "RESUME_COUNT" or self.resume_call.state != "SUCCEEDED"
                or self.resume_call.scalar != 1):
            return False
        if (self.wait_call not in owner.calls or self.wait_call.name != "WaitForSingleObject"
                or self.wait_call.mode != "WAIT_RESULT" or self.wait_call.state != "SUCCEEDED"
                or self.wait_call.scalar != 0):
            return False
        if (self.exit_call not in owner.calls or self.exit_call.name != "GetExitCodeProcess"
                or self.exit_call.state != "SUCCEEDED" or type(self.exit_code) is not int
                or self.exit_code == 259):
            return False
        if (self.job_query_call not in owner.calls or self.job_query_call.name != "QueryInformationJobObject"
                or self.job_query_call.state != "SUCCEEDED" or self.job_active != 0
                or self.job_total != 1 or self.birth_call not in owner.calls
                or self.birth_call.name != "GetProcessTimes" or self.birth_call.state != "SUCCEEDED"
                or type(self.birth) is not tuple or len(self.birth) != 2):
            return False
        return self.output_capture is not None

    def failed_no_child_verified(self, bootstrap):
        owner = self.owner
        return (bootstrap.owner is owner and self.process_creation_attempted
            and self.create_call in owner.calls and self.create_call.name == "CreateProcessW"
            and self.create_call.state == "KNOWN_FAILURE" and self.create_call.scalar == 0
            and self.process_info is not None
            and b._raw_process_information_handle(self.process_info, "process") == 0
            and b._raw_process_information_handle(self.process_info, "thread") == 0
            and all(handle is not None and handle in owner.handles and handle.closed
                    and handle.state == "NO_OUTPUT" for handle in (self.process, self.thread))
            and all(handle is not None and handle in owner.handles and handle.closed
                    and handle.close_call is not None and handle.close_call.state == "SUCCEEDED"
                    for handle in (self.job, self.stdio)))

    def public_manifest_record(self):
        if not self.retirement_verified(type("_Original", (), {"owner": self.owner})()):
            b.fail("COMPILER_RETIREMENT", "only an actually retired original compiler can enter the manifest")
        argv_raw = b.canonical(list(self.argv))
        env_raw = b.canonical(self.environment)
        return {
            "executable": self.argv[0],
            "argv": list(self.argv),
            "argv_sha256": b.digest(argv_raw),
            "cwd": self.cwd,
            "environment": dict(sorted(self.environment.items())),
            "environment_sha256": b.digest(env_raw),
            "worker_source_sha256": self.source_sha256,
            "ordered_classpath_sha256": self.classpath_sha256,
            "resume_previous_suspend_count": self.resume_call.scalar,
            "birth_filetime_100ns": list(self.birth),
            "exit_code": self.exit_code,
            "job_total_processes": self.job_total,
            "job_active_processes": self.job_active,
            "generated_files": list(self.output_capture),
        }


def validate_recipe(value, *, prefix: str, source_root: str, files) -> PreparedLaunchRecipe:
    b._fields(value, {"schema", "vendor_source_root", "vendor_files", "classpath",
                      "server_image", "server_config", "version_file"})
    if value["schema"] != RECIPE_SCHEMA:
        b.fail("PREPARED_LAUNCH_SCHEMA", "one closed prepared-launch recipe version is supported")
    vendor_root = b._absolute(value["vendor_source_root"])
    if (b._within(vendor_root, prefix) or b._within(prefix, vendor_root)
            or b._within(vendor_root, source_root) or b._within(source_root, vendor_root)):
        b.fail("PREPARED_VENDOR_ROOT", "original vendor, staging and new prefix roots must be disjoint")
    rows = value["vendor_files"]
    if type(rows) is not list or not 2 <= len(rows) <= MAX_VENDOR_FILES:
        b.fail("PREPARED_VENDOR_INVENTORY", "bounded finite original vendor file inventory required")
    seen_sources, seen_targets, total = set(), set(), 0
    parsed = []
    roles = {"server_image", "server_config", "version", "classpath"}
    roles.add("runtime")
    for row in rows:
        b._fields(row, {"source", "target", "size", "sha256", "role"})
        source, target = b._relative(row["source"]), b._relative(row["target"])
        if (not target.startswith(VENDOR_PREFIX) or target[len(VENDOR_PREFIX):] != source
                or len(ntpath.join(prefix, *target.split("/"))) > 240
                or source.casefold() in seen_sources or target.casefold() in seen_targets
                or row["role"] not in roles or type(row["size"]) is not int
                or not 0 < row["size"] <= b.MAX_FILE):
            b.fail("PREPARED_VENDOR_INVENTORY", "regular-file vendor rows must be finite, unique and bounded")
        seen_sources.add(source.casefold())
        seen_targets.add(target.casefold())
        total += row["size"]
        parsed.append(VendorFile(source, target, row["size"], b._hex(row["sha256"]), row["role"]))
    if total > b.MAX_TOTAL:
        b.fail("PREPARED_VENDOR_INVENTORY", "prepared vendor copy exceeds the existing install bound")
    for role, field in (("server_image", "server_image"), ("server_config", "server_config"),
                        ("version", "version_file")):
        if sum(row.role == role for row in parsed) != 1:
            b.fail("PREPARED_VENDOR_INVENTORY", "exact server image/config/version source rows required")
        if value[field] != next(row.target for row in parsed if row.role == role):
            b.fail("PREPARED_VENDOR_BINDING", "named vendor target must reference its exact source row")
    classpath = value["classpath"]
    classpath_rows = [row.target for row in parsed if row.role == "classpath"]
    if (type(classpath) is not list or not 1 <= len(classpath) <= MAX_VENDOR_FILES
            or tuple(classpath) != tuple(classpath_rows)
            or len(set(path.casefold() for path in classpath)) != len(classpath)):
        b.fail("PREPARED_CLASSPATH", "ordered classpath must exactly enumerate the pinned vendor JAR rows")
    source_paths = {item.path: item for item in files}
    source = source_paths.get(WORKER_SOURCE)
    compiler = source_paths.get(COMPILER)
    java = source_paths.get(JAVA)
    if source is None or compiler is None or java is None:
        b.fail("PREPARED_COMPILER_INPUT", "fixed Worker source and original JDK java/javac inventory required")
    if (compiler.role != "jdk" or java.role != "jdk"):
        b.fail("PREPARED_COMPILER_INPUT", "compiler and Java launcher must be the pinned original JDK files")
    if source.size != 150507 or source.sha256 != WORKER_SOURCE_SHA256:
        b.fail("PREPARED_COMPILER_INPUT", "fixed Worker source must equal the published source bytes")
    critical_runtime = "ext/graphicsmagick/win64/CORE_RL_magick_.dll"
    if sum(row.source.casefold() == critical_runtime.casefold() and row.role == "runtime"
           for row in parsed) != 1:
        b.fail("PREPARED_VENDOR_INVENTORY", "the existing fixed Server image-library dependency must be pinned as runtime data")
    result = PreparedLaunchRecipe(vendor_root, tuple(parsed), tuple(classpath), value["server_image"],
        value["server_config"], value["version_file"])
    for path in (result.output_dir, result.manifest_path, result.installer_cwd, result.server_cwd,
                 result.worker_cwd, result.compiler_temp, result.server_temp, result.worker_temp):
        b._relative(path)
        if len(ntpath.join(prefix, *path.split("/"))) > 240:
            b.fail("PREPARED_LAUNCH_PATH", "fixed prepared-launch path exceeds the existing bound")
    return result


def compiler_argv(recipe: PreparedLaunchRecipe, prefix: str) -> tuple[str, ...]:
    cp = ";".join(ntpath.join(prefix, *path.split("/")) for path in recipe.classpath)
    return (ntpath.join(prefix, *recipe.compiler.split("/")), *JAVA_OPTIONS, "-classpath", cp,
            "-d", ntpath.join(prefix, *recipe.output_dir.split("/")),
            ntpath.join(prefix, *recipe.worker_source.split("/")))


def compiler_environment(recipe: PreparedLaunchRecipe, prefix: str, windows_root: str,
                         system_root: str) -> dict[str, str]:
    windows_root, system_root = b._absolute(windows_root), b._absolute(system_root)
    if ntpath.normcase(ntpath.dirname(system_root)) != ntpath.normcase(windows_root):
        b.fail("PREPARED_OS_ROOT", "original Windows/System32 roots do not have their expected relationship")
    drive = ntpath.splitdrive(windows_root)[0]
    vendor_bin = ntpath.join(prefix, *ntpath.dirname(recipe.server_image).split("/"))
    jdk_bin = ntpath.join(prefix, "jdk11", "bin")
    return {
        "SystemRoot": windows_root,
        "WINDIR": windows_root,
        "SystemDrive": drive,
        "PATH": ";".join((jdk_bin, vendor_bin, system_root, windows_root)),
        "JAVA_HOME": ntpath.join(prefix, "jdk11"),
        "TEMP": ntpath.join(prefix, *recipe.compiler_temp.split("/")),
        "TMP": ntpath.join(prefix, *recipe.compiler_temp.split("/")),
    }


def manifest_document(*, plan_sha256: str, recipe: PreparedLaunchRecipe, prefix: str,
                      profile_sha256: str, config_files, compiler_record, outputs,
                      vendor_target_facts, original):
    """Create protected installation data only after known compiler retirement."""
    b._hex(plan_sha256)
    b._hex(profile_sha256)
    rows = []
    for path, captured in outputs:
        image = getattr(captured, "image", captured)
        rel = b._relative(path)
        if not rel.casefold().startswith((recipe.output_dir + "/").casefold()) or not rel.endswith(".class"):
            b.fail("PREPARED_OUTPUT", "only regular class files under the fixed output namespace are accepted")
        rows.append({"path": rel, "size": image.size, "sha256": image.sha256,
                     "file_id": [str(image.identity[0]), str(image.identity[1])],
                     "security_sha256": b.digest(image.raw_sd)})
    rows.sort(key=lambda row: row["path"].casefold())
    if ((not rows and compiler_record.get("exit_code") == 0) or len(rows) > MAX_CLASS_OUTPUTS
            or len({x["path"].casefold() for x in rows}) != len(rows)):
        b.fail("PREPARED_OUTPUT", "complete bounded generated class set required")
    configs = [{"path": path, "sha256": b.digest(raw), "size": len(raw)} for path, raw in config_files]
    def install_file(path):
        item = next((row for row in original.intent.files if row.path == path), None)
        source = original.sources.get(path)
        target = original.nodes.get(path)
        if item is None or source is None or target is None or target.image.directory:
            b.fail("PREPARED_SOURCE_PIN", "fixed installer source and target file pins are required")
        source_handle, source_image = source
        source_path = ntpath.join(original.intent.source_root, *path.split("/"))
        return {
            "source_path": source_path, "target": path,
            "size": item.size, "sha256": item.sha256,
            "source_file_id": [str(source_image.identity[0]), str(source_image.identity[1])],
            "source_security_sha256": b.digest(source_image.raw_sd),
            "target_file_id": [str(target.image.identity[0]), str(target.image.identity[1])],
            "target_security_sha256": b.digest(target.image.raw_sd),
        }

    jdk_files = {name: install_file(path) for name, path in (
        ("java", "jdk11/bin/java.exe"), ("javac", "jdk11/bin/javac.exe"))}
    worker_source = install_file(recipe.worker_source)
    role_cwd_facts = {}
    for role, relative in (("server", recipe.server_cwd), ("worker", recipe.worker_cwd)):
        node = original.nodes.get(relative)
        if node is None or not node.image.directory:
            b.fail("PREPARED_ROLE_CWD", "both fixed role cwd directories must be retained by the original installer")
        role_cwd_facts[role] = {
            "file_id": [str(node.image.identity[0]), str(node.image.identity[1])],
            "security_sha256": b.digest(node.image.raw_sd),
        }
    return {
        "schema": MANIFEST_SCHEMA,
        "prepared": compiler_record.get("exit_code") == 0 and bool(rows),
        "plan_sha256": plan_sha256,
        "installation_prefix": prefix,
        "service_profile_sha256": profile_sha256,
        "vendor_source_root": recipe.vendor_source_root,
        "vendor_files": [{"source": row.source, "target": row.target,
                          "source_size": row.size, "source_sha256": row.sha256, "role": row.role,
                          **vendor_target_facts[row.target]}
                         for row in recipe.vendor_files],
        "server_image": recipe.server_image,
        "server_config": recipe.server_config,
        "version_file": recipe.version_file,
        "ordered_classpath": list(recipe.classpath),
        "worker_source": recipe.worker_source,
        "worker_source_sha256": compiler_record["worker_source_sha256"],
        "worker_source_pin": worker_source,
        "jdk_files": jdk_files,
        "compiler": compiler_record,
        "generated_classes": rows,
        "role_cwds": {"server": recipe.server_cwd, "worker": recipe.worker_cwd},
        "role_cwd_facts": role_cwd_facts,
        "runtime_temporaries": {"server": recipe.server_temp, "worker": recipe.worker_temp},
        "configuration_files": configs,
    }


@dataclass(frozen=True)
class PreparedLaunchManifest:
    """Immutable installation data read through the existing protected reader."""
    path: str
    installation_prefix: str
    service_profile_sha256: str
    plan_sha256: str
    vendor_source_root: str
    vendor_files: tuple[MappingProxyType, ...]
    server_image: str
    server_config: str
    version_file: str
    ordered_classpath: tuple[str, ...]
    worker_source: str
    worker_source_sha256: str
    worker_source_pin: MappingProxyType
    jdk_files: MappingProxyType
    generated_classes: tuple[MappingProxyType, ...]
    role_cwds: MappingProxyType
    role_cwd_facts: MappingProxyType
    runtime_temporaries: MappingProxyType
    configuration_files: tuple[MappingProxyType, ...]
    _seal: object

    def __post_init__(self):
        if self._seal is not _MANIFEST_SEAL:
            raise ValueError("prepared launch data must come from the protected manifest loader")

    @property
    def prepared_vendor_root(self) -> str:
        return ntpath.join(self.installation_prefix, "prepared", "vendor")

    @property
    def prepared_classes_root(self) -> str:
        return ntpath.join(self.installation_prefix, *OUTPUT_DIR.split("/"))

    @property
    def prepared_jdk_root(self) -> str:
        return ntpath.join(self.installation_prefix, "jdk11")


def manifest_reference(relative_path: str = MANIFEST_PATH) -> dict[str, str]:
    if relative_path != MANIFEST_PATH:
        raise ValueError("prepared manifest reference is fixed")
    return {"schema": MANIFEST_REFERENCE_SCHEMA, "path": relative_path}


def _manifest_fields(value, fields, name):
    if type(value) is not dict or set(value) != set(fields):
        raise ValueError(f"prepared manifest {name} fields are not closed")


def _manifest_hex(value):
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("prepared manifest SHA-256 is malformed")
    return value


def _manifest_relative(value):
    if type(value) is not str or not value or "\\" in value or value.startswith("/"):
        raise ValueError("prepared manifest relative path is malformed")
    parts = value.split("/")
    if any(part in {"", ".", ".."} or part.endswith((" ", ".")) or ":" in part for part in parts):
        raise ValueError("prepared manifest relative path is not canonical")
    return value


def _manifest_absolute(value):
    if (type(value) is not str or len(value) > 240 or not re.fullmatch(r"[A-Za-z]:\\[^\x00]+", value)
            or ntpath.normpath(value) != value or any(part in {"", ".", ".."} for part in value[3:].split("\\"))):
        raise ValueError("prepared manifest absolute path is malformed")
    return value


def _manifest_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("prepared manifest has duplicate JSON keys")
        result[key] = value
    return result


def load_protected_manifest(raw: bytes, *, path: str, profile_sha256: str,
                            server_image_sha256: str, worker_image_sha256: str) -> PreparedLaunchManifest:
    """Parse data only; callers must obtain ``raw`` from the protected path reader."""
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_MANIFEST_BYTES:
        raise ValueError("prepared manifest exceeds its protected reader bound")
    expected_profile = _manifest_hex(profile_sha256)
    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_manifest_pairs,
                              parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("prepared manifest is not bounded UTF-8 JSON") from exc
    fields = {"schema", "prepared", "plan_sha256", "installation_prefix", "service_profile_sha256",
              "vendor_source_root", "vendor_files", "server_image", "server_config", "version_file",
              "ordered_classpath", "worker_source", "worker_source_sha256", "worker_source_pin",
              "jdk_files", "compiler", "generated_classes", "role_cwds", "runtime_temporaries",
              "role_cwd_facts", "configuration_files"}
    _manifest_fields(document, fields, "root")
    if document["schema"] != MANIFEST_SCHEMA or document["prepared"] is not True:
        raise ValueError("only a successfully retired prepared launch manifest can load")
    plan_sha = _manifest_hex(document["plan_sha256"])
    prefix = _manifest_absolute(document["installation_prefix"])
    source_root = _manifest_absolute(document["vendor_source_root"])
    expected_path = ntpath.join(prefix, *MANIFEST_PATH.split("/"))
    if (ntpath.normcase(_manifest_absolute(path)) != ntpath.normcase(expected_path)
            or document["service_profile_sha256"] != expected_profile):
        raise ValueError("prepared manifest path/profile source binding differs")

    rows = document["vendor_files"]
    if type(rows) is not list or not 2 <= len(rows) <= MAX_VENDOR_FILES:
        raise ValueError("prepared manifest vendor inventory is outside the finite bound")
    normalized_rows = []
    seen_source, seen_target, total = set(), set(), 0
    allowed_roles = {"server_image", "server_config", "version", "classpath", "runtime"}
    for row in rows:
        _manifest_fields(row, {"source", "target", "source_size", "source_sha256", "role", "target_size",
            "target_sha256", "target_file_id", "source_file_id", "source_security_sha256",
            "target_security_sha256", "source_path", "copy_equal", "fixed_transform"}, "vendor row")
        source, target = _manifest_relative(row["source"]), _manifest_relative(row["target"])
        source_path = _manifest_absolute(row["source_path"])
        expected_source = ntpath.join(source_root, *source.split("/"))
        if (ntpath.normcase(source_path) != ntpath.normcase(expected_source)
                or not target.startswith(VENDOR_PREFIX) or target[len(VENDOR_PREFIX):] != source
                or row["role"] not in allowed_roles or source.casefold() in seen_source
                or target.casefold() in seen_target or type(row["source_size"]) is not int
                or type(row["target_size"]) is not int or not 0 < row["source_size"] <= b.MAX_FILE
                or not 0 < row["target_size"] <= b.MAX_FILE or type(row["copy_equal"]) is not bool
                or row["role"] != "server_config" and row["copy_equal"] is not True
                or row["fixed_transform"] != ("webbridge-loopback-v1" if row["role"] == "server_config" else None)):
            raise ValueError("prepared manifest vendor lineage is malformed")
        _manifest_hex(row["source_sha256"]); _manifest_hex(row["target_sha256"])
        _manifest_hex(row["source_security_sha256"]); _manifest_hex(row["target_security_sha256"])
        if (type(row["source_file_id"]) is not list or len(row["source_file_id"]) != 2
                or type(row["target_file_id"]) is not list or len(row["target_file_id"]) != 2
                or any(type(part) is not str or not part for part in (*row["source_file_id"], *row["target_file_id"]))
                or row["copy_equal"] and (row["source_size"] != row["target_size"]
                                           or row["source_sha256"] != row["target_sha256"])):
            raise ValueError("prepared manifest vendor file identities are malformed")
        total += row["source_size"] + row["target_size"]
        if total > 2 * b.MAX_TOTAL:
            raise ValueError("prepared manifest vendor inventory exceeds its aggregate bound")
        normalized_rows.append(MappingProxyType(dict(row)))
        seen_source.add(source.casefold()); seen_target.add(target.casefold())
    by_role = {role: [row for row in normalized_rows if row["role"] == role]
               for role in ("server_image", "server_config", "version")}
    if any(len(by_role[role]) != 1 for role in by_role):
        raise ValueError("prepared manifest requires one exact Server/config/version source")
    critical_runtime = "ext/graphicsmagick/win64/CORE_RL_magick_.dll"
    if sum(row["source"].casefold() == critical_runtime.casefold() and row["role"] == "runtime"
           for row in normalized_rows) != 1:
        raise ValueError("prepared manifest omits the existing fixed Server image-library dependency")
    server_image, server_config, version_file = (document[key] for key in ("server_image", "server_config", "version_file"))
    if any(by_role[role][0]["target"] != value for role, value in (
            ("server_image", server_image), ("server_config", server_config), ("version", version_file))):
        raise ValueError("prepared manifest named vendor targets differ from their source rows")
    if by_role["server_image"][0]["source_sha256"] != _manifest_hex(server_image_sha256):
        raise ValueError("prepared Server source differs from the trusted profile image")

    classpath = document["ordered_classpath"]
    classpath_rows = [row for row in normalized_rows if row["role"] == "classpath"]
    if (type(classpath) is not list or not 1 <= len(classpath) <= MAX_VENDOR_FILES
            or classpath != [row["target"] for row in classpath_rows]
            or len({item.casefold() for item in classpath}) != len(classpath)):
        raise ValueError("prepared manifest ordered classpath differs from its pinned files")

    _manifest_fields(document["jdk_files"], {"java", "javac"}, "JDK files")
    normalized_jdk = {}
    for name, rel in (("java", "jdk11/bin/java.exe"), ("javac", "jdk11/bin/javac.exe")):
        row = document["jdk_files"][name]
        _manifest_fields(row, {"source_path", "target", "size", "sha256", "source_file_id",
                              "source_security_sha256", "target_file_id", "target_security_sha256"}, "JDK pin")
        target = _manifest_relative(row["target"])
        source_path = _manifest_absolute(row["source_path"])
        if target != rel or type(row["size"]) is not int or not 0 < row["size"] <= b.MAX_FILE:
            raise ValueError("prepared manifest JDK target is not fixed")
        _manifest_hex(row["sha256"]); _manifest_hex(row["source_security_sha256"])
        _manifest_hex(row["target_security_sha256"])
        for key in ("source_file_id", "target_file_id"):
            if type(row[key]) is not list or len(row[key]) != 2 or any(type(value) is not str or not value for value in row[key]):
                raise ValueError("prepared manifest JDK identity is malformed")
        normalized_jdk[name] = MappingProxyType(dict(row))
    jdk_source_root = ntpath.dirname(ntpath.dirname(normalized_jdk["java"]["source_path"]))
    if ntpath.normcase(ntpath.dirname(ntpath.dirname(normalized_jdk["javac"]["source_path"]))) != ntpath.normcase(jdk_source_root):
        raise ValueError("prepared JDK java/javac source roots differ")
    if normalized_jdk["java"]["sha256"] != _manifest_hex(worker_image_sha256):
        raise ValueError("prepared Java launcher differs from the trusted Worker image")

    source = document["worker_source"]
    source_pin = document["worker_source_pin"]
    if source != WORKER_SOURCE:
        raise ValueError("prepared Worker source is not the published fixed source")
    _manifest_fields(source_pin, {"source_path", "target", "size", "sha256", "source_file_id",
                                  "source_security_sha256", "target_file_id", "target_security_sha256"}, "Worker source pin")
    if (_manifest_relative(source_pin["target"]) != WORKER_SOURCE or type(source_pin["size"]) is not int
            or source_pin["size"] != 150507 or source_pin["sha256"] != WORKER_SOURCE_SHA256
            or document["worker_source_sha256"] != WORKER_SOURCE_SHA256):
        raise ValueError("prepared Worker source pin differs from its compiler input")
    _manifest_absolute(source_pin["source_path"])
    for key in ("source_security_sha256", "target_security_sha256"):
        _manifest_hex(source_pin[key])
    for key in ("source_file_id", "target_file_id"):
        if type(source_pin[key]) is not list or len(source_pin[key]) != 2 or any(type(value) is not str or not value for value in source_pin[key]):
            raise ValueError("prepared Worker source identity is malformed")
    _manifest_hex(document["worker_source_sha256"])

    compiler = document["compiler"]
    compiler_fields = {"executable", "argv", "argv_sha256", "cwd", "environment", "environment_sha256",
        "worker_source_sha256", "ordered_classpath_sha256", "resume_previous_suspend_count",
        "birth_filetime_100ns", "exit_code", "job_total_processes", "job_active_processes", "generated_files"}
    _manifest_fields(compiler, compiler_fields, "compiler receipt")
    cp_rows = [{"path": row["target"], "size": row["target_size"], "sha256": row["target_sha256"]}
               for row in classpath_rows]
    expected_argv = [ntpath.join(prefix, *COMPILER.split("/")), *JAVA_OPTIONS, "-classpath",
        ";".join(ntpath.join(prefix, *item.split("/")) for item in classpath),
        "-d", ntpath.join(prefix, *OUTPUT_DIR.split("/")), ntpath.join(prefix, *source.split("/"))]
    env = compiler["environment"]
    if type(env) is not dict or set(env) != {"SystemRoot", "WINDIR", "SystemDrive", "PATH", "JAVA_HOME", "TEMP", "TMP"}:
        raise ValueError("prepared compiler environment is not closed")
    windows_root = _manifest_absolute(env["SystemRoot"])
    system_root = ntpath.join(windows_root, "System32")
    expected_env = {"SystemRoot": windows_root, "WINDIR": windows_root,
        "SystemDrive": ntpath.splitdrive(windows_root)[0],
        "PATH": ";".join((ntpath.join(prefix, "jdk11", "bin"),
            ntpath.join(prefix, "prepared", "vendor", *ntpath.dirname(server_image).split("/")[-2:]),
            system_root, windows_root)),
        "JAVA_HOME": ntpath.join(prefix, "jdk11"),
        "TEMP": ntpath.join(prefix, *COMPILER_TEMP.split("/")),
        "TMP": ntpath.join(prefix, *COMPILER_TEMP.split("/"))}
    # The Server executable's directory is the exact ordered vendor PATH source.
    expected_env["PATH"] = ";".join((ntpath.join(prefix, "jdk11", "bin"),
        ntpath.dirname(ntpath.join(prefix, *server_image.split("/"))), system_root, windows_root))
    if (compiler["executable"] != expected_argv[0] or compiler["argv"] != expected_argv
            or compiler["cwd"] != ntpath.join(prefix, *INSTALLER_CWD.split("/"))
            or env != expected_env or compiler["argv_sha256"] != hashlib.sha256(b.canonical(expected_argv)).hexdigest()
            or compiler["environment_sha256"] != hashlib.sha256(b.canonical(env)).hexdigest()
            or compiler["worker_source_sha256"] != WORKER_SOURCE_SHA256
            or compiler["ordered_classpath_sha256"] != hashlib.sha256(b.canonical(cp_rows)).hexdigest()
            or compiler["resume_previous_suspend_count"] != 1
            or type(compiler["birth_filetime_100ns"]) is not list or len(compiler["birth_filetime_100ns"]) != 2
            or any(type(value) is not int or value < 0 for value in compiler["birth_filetime_100ns"])
            or type(compiler["exit_code"]) is not int or compiler["exit_code"] != 0
            or compiler["job_total_processes"] != 1 or compiler["job_active_processes"] != 0):
        raise ValueError("prepared compiler receipt does not bind its fixed process/input contract")

    classes = document["generated_classes"]
    if type(classes) is not list or not 1 <= len(classes) <= MAX_CLASS_OUTPUTS:
        raise ValueError("prepared Worker generated class list is outside its bound")
    normalized_classes = []
    class_paths = set()
    for row in classes:
        _manifest_fields(row, {"path", "size", "sha256", "file_id", "security_sha256"}, "generated class")
        class_path = _manifest_relative(row["path"])
        if (not class_path.casefold().startswith((OUTPUT_DIR + "/comsol_mcp/worker_java/").casefold())
                or not re.fullmatch(r"PersistentComsolWorker(?:\$[A-Za-z0-9_$]+)?\.class", class_path.rsplit("/", 1)[-1])
                or class_path.casefold() in class_paths or type(row["size"]) is not int
                or not 0 < row["size"] <= b.MAX_FILE
                or type(row["file_id"]) is not list or len(row["file_id"]) != 2
                or any(type(value) is not str or not value for value in row["file_id"])):
            raise ValueError("prepared generated class inventory is malformed")
        _manifest_hex(row["sha256"]); _manifest_hex(row["security_sha256"])
        class_paths.add(class_path.casefold())
        normalized_classes.append(MappingProxyType(dict(row)))
    if (classes != compiler["generated_files"]
            or "prepared/bootstrap/classes/comsol_mcp/worker_java/PersistentComsolWorker.class".casefold() not in class_paths):
        raise ValueError("prepared generated classes differ from the retired compiler receipt")

    _manifest_fields(document["role_cwds"], {"server", "worker"}, "role cwd")
    _manifest_fields(document["role_cwd_facts"], {"server", "worker"}, "role cwd facts")
    _manifest_fields(document["runtime_temporaries"], {"server", "worker"}, "runtime temp")
    if document["role_cwds"] != {"server": SERVER_CWD, "worker": WORKER_CWD}:
        raise ValueError("prepared role working directories are not fixed")
    if document["runtime_temporaries"] != {"server": SERVER_TEMP, "worker": WORKER_TEMP}:
        raise ValueError("prepared runtime temporary paths are not fixed")
    normalized_cwd_facts = {}
    for role in ("server", "worker"):
        row = document["role_cwd_facts"][role]
        _manifest_fields(row, {"file_id", "security_sha256"}, "role cwd fact")
        if (type(row["file_id"]) is not list or len(row["file_id"]) != 2
                or any(type(value) is not str or not value for value in row["file_id"])):
            raise ValueError("prepared role cwd identity is malformed")
        _manifest_hex(row["security_sha256"])
        normalized_cwd_facts[role] = MappingProxyType(dict(row))
    config_rows = document["configuration_files"]
    if type(config_rows) is not list or [row.get("path") for row in config_rows if type(row) is dict] != [
            "profiles/trusted-profile.json", "profiles/host-provider.json",
            "profiles/memory-resource.json", "profiles/disk-resource.json"]:
        raise ValueError("prepared four-config source record is incomplete or reordered")
    normalized_configs = []
    for row in config_rows:
        _manifest_fields(row, {"path", "sha256", "size"}, "configuration file")
        _manifest_relative(row["path"]); _manifest_hex(row["sha256"])
        if type(row["size"]) is not int or not 1 <= row["size"] <= b.MAX_PLAN:
            raise ValueError("prepared configuration file size is malformed")
        normalized_configs.append(MappingProxyType(dict(row)))
    profile_row = normalized_configs[0]
    if profile_row["sha256"] != expected_profile:
        raise ValueError("prepared manifest does not bind its exact trusted profile bytes")

    return PreparedLaunchManifest(_manifest_absolute(path), prefix, expected_profile, plan_sha, source_root,
        tuple(normalized_rows), server_image, server_config, version_file, tuple(classpath), source,
        document["worker_source_sha256"], MappingProxyType(dict(source_pin)), MappingProxyType(normalized_jdk),
        tuple(normalized_classes), MappingProxyType(dict(document["role_cwds"])),
        MappingProxyType(normalized_cwd_facts), MappingProxyType(dict(document["runtime_temporaries"])),
        tuple(normalized_configs), _MANIFEST_SEAL)


def loopback_server_config(raw: bytes) -> bytes:
    """Apply the fixed Main-selected private WebBridge loopback transformation."""
    if type(raw) is not bytes or not 0 < len(raw) <= b.MAX_ARCHIVE:
        b.fail("PREPARED_VENDOR_CONFIG", "bounded original WebBridge configuration bytes required")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise b.BootstrapError("PREPARED_VENDOR_CONFIG", "original WebBridge XML is malformed") from exc
    connectors = root.findall(".//Connector")
    if len(connectors) != 1:
        b.fail("PREPARED_VENDOR_CONFIG", "exactly one original WebBridge Connector is required")
    connectors[0].set("address", "127.0.0.1")
    output = ET.tostring(root, encoding="UTF-8", xml_declaration=True)
    try:
        verified = ET.fromstring(output).findall(".//Connector")
    except ET.ParseError as exc:
        raise b.BootstrapError("PREPARED_VENDOR_CONFIG", "fixed WebBridge XML failed readback") from exc
    if len(verified) != 1 or verified[0].get("address") != "127.0.0.1":
        b.fail("PREPARED_VENDOR_CONFIG", "fixed WebBridge loopback readback differs")
    return output


def capture_compiler_outputs(original, configuration, record):
    """Read back the complete fixed class namespace under the retained owner."""
    recipe = original.intent.prepared_launch
    if (recipe is None or original._compiler_record is not record
            or original.installer_compiler_state != "EXIT_KNOWN"):
        b.fail("PREPARED_OUTPUT", "original exited compiler and V3 namespace are required")
    base = recipe.output_dir
    original._compare_node(original.nodes[base])
    if record.capture_complete or record.capture_nodes is not None:
        b.fail("PREPARED_OUTPUT", "one same-owner compiler output capture is allowed")
    expected_files, outputs, captured_nodes, total = [], [], [], 0
    primary = False
    top = original.intent.destination(base)
    top_names = original.backend.names(top)
    if not top_names:
        if record.exit_code == 0:
            b.fail("PREPARED_OUTPUT", "successful compiler produced no package directory")
        record.capture_nodes = ()
        record.capture_file_paths = ()
        record.capture_bindings = ()
        record.capture_complete = True
        record.output_capture = ()
        configuration.seal_launch_outputs(record, allow_empty=True)
        return ()
    if top_names != {"comsol_mcp"}:
        b.fail("PREPARED_OUTPUT", "compiler emitted an unexpected root output")
    package_relative = base + "/comsol_mcp"
    worker_relative = package_relative + "/worker_java"

    def capture_directory(relative):
        if relative in original.nodes:
            b.fail("PREPARED_OUTPUT", "compiler-created directory collides with an original owned node")
        path = original.intent.destination(relative)
        node = b._Node(path, relative, None)
        original.nodes[relative] = node
        node.handle = original.backend.open_node(path, True)
        image = original.backend.image(node.handle, path, True)
        if not image.directory or image.attributes & 0x400 or image.links != 1:
            b.fail("PREPARED_OUTPUT", "compiler-created package directory must be regular and single-link")
        node.image = image
        configuration.finish_descriptor(node)
        configuration.finish_node(node, True)
        captured_nodes.append((relative, node))
        return node

    capture_directory(package_relative)
    if original.backend.names(original.intent.destination(package_relative)) != {"worker_java"}:
        b.fail("PREPARED_OUTPUT", "compiler emitted an unexpected package directory")
    capture_directory(worker_relative)
    package = original.intent.destination(worker_relative)
    names = original.backend.names(package)
    if len(names) > MAX_CLASS_OUTPUTS:
        b.fail("PREPARED_OUTPUT", "compiler output count exceeds the fixed bound")
    for name in sorted(names, key=str.casefold):
        if not re.fullmatch(r"PersistentComsolWorker(?:\$[A-Za-z0-9_$]+)?\.class", name):
            b.fail("PREPARED_OUTPUT", "only classes from the fixed published Worker source are accepted")
        relative = worker_relative + "/" + name
        if relative in original.nodes:
            b.fail("PREPARED_OUTPUT", "compiler output collides with an original owned node")
        path = original.intent.destination(relative)
        node = b._Node(path, relative, None)
        original.nodes[relative] = node
        node.handle = original.backend.open_node(path, False)
        image = original.backend.image(node.handle, path, False)
        if image.directory or image.attributes & 0x400 or image.links != 1 or not image.size or image.size > b.MAX_FILE:
            b.fail("PREPARED_OUTPUT", "generated class must be a bounded regular single-link file")
        node.image = image
        total += image.size
        if total > b.MAX_ARCHIVE:
            b.fail("PREPARED_OUTPUT", "complete compiler output exceeds the existing material bound")
        configuration.finish_descriptor(node)
        configuration.finish_node(node, False, (image.size, image.sha256))
        expected_files.append(relative)
        outputs.append((relative, node))
        captured_nodes.append((relative, node))
        primary |= name == "PersistentComsolWorker.class"
    if record.exit_code == 0 and not primary:
        b.fail("PREPARED_OUTPUT", "successful javac did not produce the fixed Worker main class")
    record.capture_nodes = tuple(captured_nodes)
    record.capture_file_paths = tuple(expected_files)
    record.capture_bindings = tuple((path, node, node.handle, node.image) for path, node in captured_nodes)
    record.capture_complete = True
    record.output_capture = tuple({"path": path, "size": node.image.size,
                                   "sha256": node.image.sha256,
                                   "file_id": [str(node.image.identity[0]), str(node.image.identity[1])],
                                   "security_sha256": b.digest(node.image.raw_sd)}
                                  for path, node in outputs)
    configuration.seal_launch_outputs(record, allow_empty=record.exit_code != 0)
    return tuple(outputs)


def validate_manifest(raw: bytes):
    if type(raw) is not bytes or not 1 <= len(raw) <= b.MAX_PLAN:
        b.fail("PREPARED_MANIFEST", "bounded original manifest bytes required")
    try:
        doc = json.loads(raw, object_pairs_hook=_unique_pairs,
                         parse_constant=lambda _: b.fail("PREPARED_MANIFEST", "nonfinite JSON is forbidden"))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise b.BootstrapError("PREPARED_MANIFEST", "invalid protected manifest JSON") from exc
    required = {"schema", "prepared", "plan_sha256", "installation_prefix", "service_profile_sha256",
                "vendor_source_root", "vendor_files", "server_image", "server_config", "version_file",
                "ordered_classpath", "worker_source", "worker_source_sha256", "worker_source_pin", "jdk_files",
                "compiler", "generated_classes", "role_cwds", "role_cwd_facts", "runtime_temporaries",
                "configuration_files"}
    b._fields(doc, required)
    if doc["schema"] != MANIFEST_SCHEMA:
        b.fail("PREPARED_MANIFEST", "unsupported prepared launch manifest schema")
    if type(doc["prepared"]) is not bool:
        b.fail("PREPARED_MANIFEST", "prepared status must be a strict boolean")
    b._hex(doc["plan_sha256"])
    b._hex(doc["service_profile_sha256"])
    b._hex(doc["worker_source_sha256"])
    if type(doc["vendor_files"]) is not list or not doc["vendor_files"]:
        b.fail("PREPARED_MANIFEST", "finite vendor source lineage required")
    if type(doc["ordered_classpath"]) is not list or not doc["ordered_classpath"]:
        b.fail("PREPARED_MANIFEST", "ordered classpath lineage required")
    return doc


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            b.fail("PREPARED_MANIFEST", "duplicate manifest field")
        result[key] = value
    return result
