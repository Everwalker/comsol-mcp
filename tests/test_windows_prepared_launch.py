"""SOFTWARE_ONLY closed prepared-launch plan and normal-producer fixtures."""
import copy
import ctypes as C
import hashlib
import json
import ntpath
import shutil
import subprocess
from pathlib import Path, PureWindowsPath
from types import MappingProxyType, MethodType, SimpleNamespace as NS

import pytest

from comsol_mcp import _windows_host_bootstrap as b
from comsol_mcp import _windows_scm_config_materialization as config
from comsol_mcp import _session_server as server_runtime
from comsol_mcp import _windows_t037_launch_inputs as launch
from comsol_mcp import _windows_t037 as t037
from comsol_mcp import _windows_worker_bootstrap as prepared
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._session_server import OwnedServerError, ServerDirectories, prepare_prepared_t037_installation
from tests.test_windows_host_bootstrap import intent_doc
from tests.test_windows_scm_config_materialization import (
    ConfigWindows, HOST as SCM_HOST, SERVICE_SID, fixture as v2_config_fixture, recipe_doc,
)


SERVER_SOURCE = b"SOFTWARE_ONLY external COMSOL Server image"
CONFIG_SOURCE = b'<Server><Connector address="0.0.0.0"/></Server>'
VERSION_SOURCE = b"6.4 fixture"
CLASSPATH_SOURCE = b"SOFTWARE_ONLY COMSOL JAR fixture"
SOURCE_ROOT = r"C:\COMSOL"
PREFIX = r"C:\Protected\ComsolMCP_fixture"
WINROOT = r"C:\Windows"
SYSTEMROOT = r"C:\Windows\System32"


def v3_plan():
    document, sources, _server = recipe_doc()
    sources[prepared.WORKER_SOURCE] = Path(
        "comsol_mcp/worker_java/PersistentComsolWorker.java").read_bytes()
    sources[prepared.COMPILER] = b"SOFTWARE_ONLY javac source inventory"
    rows = {row["path"]: row for row in document["files"]}
    for path, role in ((prepared.WORKER_SOURCE, "runtime"), (prepared.COMPILER, "jdk")):
        raw = sources[path]
        rows[path] = {"path": path, "size": len(raw), "sha256": b.digest(raw), "role": role}
    document["files"] = list(rows.values())
    jdk = [{"path": row["path"][len("jdk11/"):], "size": row["size"], "sha256": row["sha256"]}
           for row in document["files"] if row["role"] == "jdk"]
    document["materials"]["jdk_inventory_sha256"] = b.digest(
        b.canonical(sorted(jdk, key=lambda row: row["path"])))
    document["schema"] = b.SCHEMA_V3
    argv = b.service_argv(document["prefix"], document["service_name"], version=3)
    document["service_argv"] = list(argv)
    document["quoted_command_line"] = subprocess.list2cmdline(argv)
    document["provisioning"]["schema"] = config.SCHEMA_V4
    vendor = [
        ("bin/win64/comsol.exe", "prepared/vendor/bin/win64/comsol.exe", SERVER_SOURCE, "server_image"),
        ("bin/win64/server.xml", "prepared/vendor/bin/win64/server.xml", CONFIG_SOURCE, "server_config"),
        ("bin/win64/version.txt", "prepared/vendor/bin/win64/version.txt", VERSION_SOURCE, "version"),
        ("plugins/COMSOL.jar", "prepared/vendor/plugins/COMSOL.jar", CLASSPATH_SOURCE, "classpath"),
        ("ext/graphicsmagick/win64/CORE_RL_magick_.dll",
         "prepared/vendor/ext/graphicsmagick/win64/CORE_RL_magick_.dll", b"IMAGE DLL FIXTURE", "runtime"),
    ]
    document["prepared_launch"] = {
        "schema": prepared.RECIPE_SCHEMA,
        "vendor_source_root": SOURCE_ROOT,
        "vendor_files": [{"source": source, "target": target, "size": len(raw),
                          "sha256": b.digest(raw), "role": role}
                         for source, target, raw, role in vendor],
        "classpath": [row[1] for row in vendor if row[3] == "classpath"],
        "server_image": vendor[0][1],
        "server_config": vendor[1][1],
        "version_file": vendor[2][1],
    }
    return document, sources


def test_v1_v2_remain_closed_and_v3_pins_published_worker_source():
    v1, _ = intent_doc()
    b.parse_plan(b.canonical(v1))
    v2, _sources, _server = recipe_doc()
    b.parse_plan(b.canonical(v2))
    for document in (v1, v2):
        changed = copy.deepcopy(document)
        changed["prepared_launch"] = {}
        with pytest.raises(b.BootstrapError):
            b.parse_plan(b.canonical(changed))

    document, _sources = v3_plan()
    intent = b.parse_plan(b.canonical(document))
    assert intent.prepared_launch.worker_source == prepared.WORKER_SOURCE
    tampered = copy.deepcopy(document)
    source_row = next(row for row in tampered["files"] if row["path"] == prepared.WORKER_SOURCE)
    source_row["sha256"] = "0" * 64 if source_row["sha256"] != "0" * 64 else "1" * 64
    with pytest.raises(b.BootstrapError, match="published source bytes"):
        b.parse_plan(b.canonical(tampered))


def _protected_manifest_document():
    prefix = PREFIX
    rows = []
    for row in _launch_manifest().vendor_files:
        rows.append({"source": row["source"], "target": row["target"],
            "source_size": row["source_size"], "source_sha256": row["source_sha256"],
            "role": row["role"], "target_size": row["target_size"],
            "target_sha256": row["target_sha256"], "target_file_id": list(row["target_file_id"]),
            "source_file_id": list(row["source_file_id"]),
            "source_security_sha256": row["source_security_sha256"],
            "target_security_sha256": row["target_security_sha256"],
            "source_path": row["source_path"], "copy_equal": row["copy_equal"],
            "fixed_transform": row["fixed_transform"]})
    cp = next(row for row in rows if row["role"] == "classpath")
    java_sha, javac_sha = "f" * 64, "e" * 64
    jdk = {}
    for name, target, source, sha in (
            ("java", "jdk11/bin/java.exe", r"C:\Staging\jdk11\bin\java.exe", java_sha),
            ("javac", "jdk11/bin/javac.exe", r"C:\Staging\jdk11\bin\javac.exe", javac_sha)):
        jdk[name] = {"source_path": source, "target": target, "size": 12, "sha256": sha,
            "source_file_id": ["1", name], "source_security_sha256": "a" * 64,
            "target_file_id": ["2", name], "target_security_sha256": "b" * 64}
    source_pin = {"source_path": r"C:\Staging\python312\tools\Lib\site-packages\comsol_mcp\worker_java\PersistentComsolWorker.java",
        "target": prepared.WORKER_SOURCE, "size": 166825,
        "sha256": prepared.WORKER_SOURCE_SHA256, "source_file_id": ["1", "worker"],
        "source_security_sha256": "a" * 64, "target_file_id": ["2", "worker"],
        "target_security_sha256": "b" * 64}
    argv = [ntpath.join(prefix, *prepared.COMPILER.split("/")), *prepared.JAVA_OPTIONS,
        "-classpath", ntpath.join(prefix, *cp["target"].split("/")), "-d",
        ntpath.join(prefix, *prepared.OUTPUT_DIR.split("/")),
        ntpath.join(prefix, *prepared.WORKER_SOURCE.split("/"))]
    windows = WINROOT
    env = {"SystemRoot": windows, "WINDIR": windows, "SystemDrive": "C:",
        "PATH": ";".join((ntpath.join(prefix, "jdk11", "bin"),
            ntpath.dirname(ntpath.join(prefix, *rows[0]["target"].split("/"))),
            SYSTEMROOT, windows)),
        "JAVA_HOME": ntpath.join(prefix, "jdk11"),
        "TEMP": ntpath.join(prefix, *prepared.COMPILER_TEMP.split("/")),
        "TMP": ntpath.join(prefix, *prepared.COMPILER_TEMP.split("/"))}
    class_path = prepared.OUTPUT_DIR + "/comsol_mcp/worker_java/PersistentComsolWorker.class"
    classes = [{"path": class_path, "size": 16, "sha256": "9" * 64,
        "file_id": ["3", "class"], "security_sha256": "a" * 64}]
    cp_rows = [{"path": cp["target"], "size": cp["target_size"], "sha256": cp["target_sha256"]}]
    compiler = {"executable": argv[0], "argv": argv,
        "argv_sha256": b.digest(b.canonical(argv)),
        "cwd": ntpath.join(prefix, *prepared.INSTALLER_CWD.split("/")),
        "environment": env, "environment_sha256": b.digest(b.canonical(env)),
        "worker_source_sha256": prepared.WORKER_SOURCE_SHA256,
        "ordered_classpath_sha256": b.digest(b.canonical(cp_rows)),
        "resume_previous_suspend_count": 1, "birth_filetime_100ns": [7, 8],
        "exit_code": 0, "job_total_processes": 1, "job_active_processes": 0,
        "generated_files": classes}
    configurations = [{"path": path, "sha256": "c" * 64 if index == 0 else "d" * 64,
        "size": 32} for index, path in enumerate(config.CONFIG_PATHS)]
    configurations[0]["sha256"] = "c" * 64
    return {"schema": prepared.MANIFEST_SCHEMA, "prepared": True, "plan_sha256": "7" * 64,
        "installation_prefix": prefix, "service_profile_sha256": "c" * 64,
        "vendor_source_root": SOURCE_ROOT, "vendor_files": rows,
        "server_image": rows[0]["target"], "server_config": rows[1]["target"],
        "version_file": rows[2]["target"], "ordered_classpath": [cp["target"]],
        "worker_source": prepared.WORKER_SOURCE,
        "worker_source_sha256": prepared.WORKER_SOURCE_SHA256, "worker_source_pin": source_pin,
        "jdk_files": jdk, "compiler": compiler, "generated_classes": classes,
        "role_cwds": {"server": prepared.SERVER_CWD, "worker": prepared.WORKER_CWD},
        "role_cwd_facts": {role: {"file_id": ["1", role], "security_sha256": "a" * 64}
            for role in ("server", "worker")},
        "runtime_temporaries": {"server": prepared.SERVER_TEMP, "worker": prepared.WORKER_TEMP},
        "configuration_files": configurations}


def test_protected_manifest_accepts_only_retired_fixed_compiler_material():
    document = _protected_manifest_document()
    loaded = prepared.load_protected_manifest(b.canonical(document),
        path=ntpath.join(PREFIX, *prepared.MANIFEST_PATH.split("/")), profile_sha256="c" * 64,
        server_image_sha256=b.digest(SERVER_SOURCE), worker_image_sha256="f" * 64)
    assert loaded.role_cwd_facts["server"]["security_sha256"] == "a" * 64
    assert sum(row["role"] == "runtime" for row in loaded.vendor_files) == 1

    tampered = copy.deepcopy(document)
    tampered["compiler"]["argv"][1] = "-proc:full"
    with pytest.raises(ValueError, match="fixed process/input contract"):
        prepared.load_protected_manifest(b.canonical(tampered),
            path=ntpath.join(PREFIX, *prepared.MANIFEST_PATH.split("/")), profile_sha256="c" * 64,
            server_image_sha256=b.digest(SERVER_SOURCE), worker_image_sha256="f" * 64)

    tampered = copy.deepcopy(document)
    tampered["role_cwd_facts"]["server"]["file_id"] = ["1"]
    with pytest.raises(ValueError, match="role cwd identity"):
        prepared.load_protected_manifest(b.canonical(tampered),
            path=ntpath.join(PREFIX, *prepared.MANIFEST_PATH.split("/")), profile_sha256="c" * 64,
            server_image_sha256=b.digest(SERVER_SOURCE), worker_image_sha256="f" * 64)


def test_original_manifest_writer_registers_fixed_sources_outputs_and_role_directories():
    document, _sources = v3_plan()
    intent = b.parse_plan(b.canonical(document))

    class Image:
        def __init__(self, size, sha256, identity, raw_sd, directory=False):
            self.size, self.sha256, self.identity, self.raw_sd = size, sha256, identity, raw_sd
            self.directory, self.attributes, self.links = directory, 0, 1

    source_sd, target_sd = b"SOURCE ACL FIXTURE", b"TARGET ACL FIXTURE"
    original_sources, original_nodes = {}, {}
    for index, item in enumerate(intent.files, 10):
        image = Image(item.size, item.sha256, (1, index), source_sd)
        original_sources[item.path] = (object(), image)
        original_nodes[item.path] = NS(image=Image(item.size, item.sha256, (2, index), target_sd))
    for index, relative in enumerate(intent.prepared_launch.directories, 100):
        if relative in {prepared.SERVER_CWD, prepared.WORKER_CWD}:
            original_nodes[relative] = NS(image=Image(0, "", (3, index), target_sd, directory=True))

    target_facts = {}
    vendor_sources = {"bin/win64/comsol.exe": SERVER_SOURCE,
        "bin/win64/server.xml": CONFIG_SOURCE, "bin/win64/version.txt": VERSION_SOURCE,
        "plugins/COMSOL.jar": CLASSPATH_SOURCE,
        "ext/graphicsmagick/win64/CORE_RL_magick_.dll": b"IMAGE DLL FIXTURE"}
    for index, row in enumerate(intent.prepared_launch.vendor_files, 200):
        target_raw = (prepared.loopback_server_config(CONFIG_SOURCE)
                      if row.role == "server_config" else
                      {"bin/win64/comsol.exe": SERVER_SOURCE,
                       "bin/win64/version.txt": VERSION_SOURCE,
                       "plugins/COMSOL.jar": CLASSPATH_SOURCE,
                       "ext/graphicsmagick/win64/CORE_RL_magick_.dll": b"IMAGE DLL FIXTURE"}[row.source])
        target_facts[row.target] = {
            "target_size": len(target_raw), "target_sha256": b.digest(target_raw),
            "target_file_id": ["4", str(index)], "source_file_id": ["1", str(index)],
            "source_security_sha256": b.digest(source_sd), "target_security_sha256": b.digest(target_sd),
            "source_path": ntpath.join(intent.prepared_launch.vendor_source_root, *row.source.split("/")),
            "copy_equal": target_raw == vendor_sources[row.source],
            "fixed_transform": "webbridge-loopback-v1" if row.role == "server_config" else None,
        }

    class_output = prepared.OUTPUT_DIR + "/comsol_mcp/worker_java/PersistentComsolWorker.class"
    class_image = Image(16, "9" * 64, (5, 6), target_sd)
    output_row = {"path": class_output, "size": class_image.size, "sha256": class_image.sha256,
        "file_id": [str(value) for value in class_image.identity],
        "security_sha256": b.digest(class_image.raw_sd)}
    original_nodes[class_output] = NS(image=class_image)
    compiler = copy.deepcopy(_protected_manifest_document()["compiler"])
    compiler["generated_files"] = [output_row]
    config_rows = [(path, b"PROFILE SOURCE FIXTURE" if index == 0 else f"CONFIG {index}".encode())
                   for index, path in enumerate(config.CONFIG_PATHS)]
    profile_hash = b.digest(config_rows[0][1])
    doc = prepared.manifest_document(plan_sha256=intent.sha256, recipe=intent.prepared_launch,
        prefix=intent.prefix, profile_sha256=profile_hash, config_files=config_rows,
        compiler_record=compiler, outputs=[(class_output, NS(image=class_image))],
        vendor_target_facts=target_facts,
        original=NS(intent=intent, sources=original_sources, nodes=original_nodes))
    loaded = prepared.load_protected_manifest(b.canonical(doc),
        path=ntpath.join(PREFIX, *prepared.MANIFEST_PATH.split("/")), profile_sha256=profile_hash,
        server_image_sha256=next(row.sha256 for row in intent.prepared_launch.vendor_files
                                 if row.role == "server_image"),
        worker_image_sha256=next(item.sha256 for item in intent.files if item.path == prepared.JAVA))
    assert loaded.role_cwd_facts["worker"]["file_id"] == ["3", str(
        100 + list(intent.prepared_launch.directories).index(prepared.WORKER_CWD))]
    assert loaded.generated_classes[0]["path"] == class_output

def _test_bootstrap(intent):
    owner = NS(handles=[], check=lambda: None, checkpoint=lambda *_args: None)
    backend = NS(owner=owner)
    owner.api = backend
    return b.Bootstrap(intent, backend, seal=b._TEST_SEAL), owner


def test_compiler_state_is_monotonic_and_requires_actual_retirement_proof():
    document, _ = v3_plan()
    bootstrap, owner = _test_bootstrap(b.parse_plan(b.canonical(document)))
    record = NS(owner=owner, failed_no_child_verified=lambda _bootstrap: False,
                retirement_verified=lambda _bootstrap: False)
    bootstrap._set_installer_compiler_state("BIRTH_PENDING", record)
    assert bootstrap.installer_compiler_state == "BIRTH_PENDING"
    with pytest.raises(b.BootstrapError, match="monotonic"):
        bootstrap._set_installer_compiler_state("NEVER_STARTED", record)
    with pytest.raises(b.BootstrapError, match="no-child state requires"):
        bootstrap._set_installer_compiler_state("FAILED_NO_CHILD", record)
    with pytest.raises(b.BootstrapError, match="not actually retired"):
        bootstrap._compare()
    assert bootstrap.children_state == "NEVER_STARTED"

    retired, retired_owner = _test_bootstrap(b.parse_plan(b.canonical(document)))
    false_record = NS(owner=retired_owner, failed_no_child_verified=lambda _bootstrap: False,
                      retirement_verified=lambda _bootstrap: False)
    retired._set_installer_compiler_state("BIRTH_PENDING", false_record)
    retired._installer_compiler_state = "EXIT_KNOWN"  # isolate the retirement-proof guard
    with pytest.raises(b.BootstrapError, match="retired state requires"):
        retired._set_installer_compiler_state("RETIRED", false_record)


def _launch_manifest():
    def vendor(source, target, source_bytes, target_bytes, role):
        return MappingProxyType({
            "source": source, "target": target,
            "source_size": len(source_bytes), "source_sha256": b.digest(source_bytes),
            "role": role, "target_size": len(target_bytes), "target_sha256": b.digest(target_bytes),
            "target_file_id": ["2", str(len(target_bytes))],
            "source_file_id": ["1", str(len(source_bytes))],
            "source_security_sha256": "a" * 64, "target_security_sha256": "b" * 64,
            "source_path": ntpath.join(SOURCE_ROOT, *source.split("/")),
            "copy_equal": source_bytes == target_bytes,
            "fixed_transform": "webbridge-loopback-v1" if role == "server_config" else None,
        })

    transformed = prepared.loopback_server_config(CONFIG_SOURCE)
    rows = (
        vendor("bin/win64/comsol.exe", "prepared/vendor/bin/win64/comsol.exe", SERVER_SOURCE, SERVER_SOURCE, "server_image"),
        vendor("bin/win64/server.xml", "prepared/vendor/bin/win64/server.xml", CONFIG_SOURCE, transformed, "server_config"),
        vendor("bin/win64/version.txt", "prepared/vendor/bin/win64/version.txt", VERSION_SOURCE, VERSION_SOURCE, "version"),
        vendor("plugins/COMSOL.jar", "prepared/vendor/plugins/COMSOL.jar", CLASSPATH_SOURCE, CLASSPATH_SOURCE, "classpath"),
        vendor("ext/graphicsmagick/win64/CORE_RL_magick_.dll",
            "prepared/vendor/ext/graphicsmagick/win64/CORE_RL_magick_.dll",
            b"IMAGE DLL FIXTURE", b"IMAGE DLL FIXTURE", "runtime"),
    )
    return prepared.PreparedLaunchManifest(
        path=ntpath.join(PREFIX, *prepared.MANIFEST_PATH.split("/")),
        installation_prefix=PREFIX, service_profile_sha256="c" * 64, plan_sha256="d" * 64,
        vendor_source_root=SOURCE_ROOT, vendor_files=rows,
        server_image=rows[0]["target"], server_config=rows[1]["target"], version_file=rows[2]["target"],
        ordered_classpath=(rows[3]["target"],), worker_source=prepared.WORKER_SOURCE,
        worker_source_sha256=prepared.WORKER_SOURCE_SHA256,
        worker_source_pin=MappingProxyType({}), jdk_files=MappingProxyType({}),
        generated_classes=(), role_cwds=MappingProxyType({"server": prepared.SERVER_CWD,
            "worker": prepared.WORKER_CWD}),
        role_cwd_facts=MappingProxyType({role: MappingProxyType({"file_id": ["1", "2"],
            "security_sha256": "a" * 64}) for role in ("server", "worker")}),
        runtime_temporaries=MappingProxyType({"server": prepared.SERVER_TEMP, "worker": prepared.WORKER_TEMP}),
        configuration_files=(), _seal=prepared._MANIFEST_SEAL)


def test_prepared_server_view_copies_exact_finite_regular_inventory(tmp_path):
    source = tmp_path / "original-comsol"
    install = tmp_path / "protected-install"
    private = tmp_path / "session-view"
    source.mkdir(); install.mkdir(); private.mkdir()
    transformed = prepared.loopback_server_config(CONFIG_SOURCE)
    contents = {
        "bin/win64/comsol.exe": SERVER_SOURCE,
        "bin/win64/server.xml": CONFIG_SOURCE,
        "bin/win64/version.txt": VERSION_SOURCE,
        "plugins/COMSOL.jar": CLASSPATH_SOURCE,
        "ext/graphicsmagick/win64/CORE_RL_magick_.dll": b"IMAGE DLL FIXTURE",
    }
    for relative, raw in contents.items():
        source_path = source / relative
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(raw)
    rows = []
    for relative, raw in contents.items():
        role = {"bin/win64/comsol.exe": "server_image", "bin/win64/server.xml": "server_config",
                "bin/win64/version.txt": "version", "plugins/COMSOL.jar": "classpath",
                "ext/graphicsmagick/win64/CORE_RL_magick_.dll": "runtime"}[relative]
        target = "prepared/vendor/" + relative
        target_bytes = transformed if role == "server_config" else raw
        rows.append(MappingProxyType({"source": relative, "target": target,
            "source_size": len(raw), "source_sha256": b.digest(raw), "role": role,
            "target_size": len(target_bytes), "target_sha256": b.digest(target_bytes),
            "target_file_id": ["2", str(len(target_bytes))], "source_file_id": ["1", str(len(raw))],
            "source_security_sha256": "a" * 64, "target_security_sha256": "b" * 64,
            "source_path": str(source / relative), "copy_equal": raw == target_bytes,
            "fixed_transform": "webbridge-loopback-v1" if role == "server_config" else None}))
    manifest = prepared.PreparedLaunchManifest(
        path=str(install / "prepared/launch-manifest.json"), installation_prefix=str(install),
        service_profile_sha256="c" * 64, plan_sha256="d" * 64, vendor_source_root=str(source),
        vendor_files=tuple(rows), server_image=rows[0]["target"], server_config=rows[1]["target"],
        version_file=rows[2]["target"], ordered_classpath=(rows[3]["target"],),
        worker_source=prepared.WORKER_SOURCE, worker_source_sha256=prepared.WORKER_SOURCE_SHA256,
        worker_source_pin=MappingProxyType({}), jdk_files=MappingProxyType({}), generated_classes=(),
        role_cwds=MappingProxyType({"server": prepared.SERVER_CWD, "worker": prepared.WORKER_CWD}),
        role_cwd_facts=MappingProxyType({role: MappingProxyType({"file_id": ["1", "2"],
            "security_sha256": "a" * 64}) for role in ("server", "worker")}),
        runtime_temporaries=MappingProxyType({"server": prepared.SERVER_TEMP, "worker": prepared.WORKER_TEMP}),
        configuration_files=(), _seal=prepared._MANIFEST_SEAL)
    for row in manifest.vendor_files:
        target = install.joinpath(*row["target"].split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(transformed if row["role"] == "server_config" else contents[row["source"]])

    result = prepare_prepared_t037_installation(source, private, manifest)
    assert result["view_kind"] == "FINITE_REGULAR_COPIES"
    assert result["vendor_file_count"] == 5
    assert (private / "bin/win64/server.xml").read_bytes() == transformed
    assert {path.relative_to(private).as_posix() for path in private.rglob("*") if path.is_file()} == {
        "bin/win64/comsol.exe", "bin/win64/server.xml", "bin/win64/version.txt", "plugins/COMSOL.jar",
        "ext/graphicsmagick/win64/CORE_RL_magick_.dll"}

    shutil.rmtree(private)
    private.mkdir()
    (source / "bin/win64/version.txt").write_bytes(b"drifted")
    with pytest.raises(ExecutionContractError, match="exact regular single-link"):
        prepare_prepared_t037_installation(source, private, manifest)


def _factory_and_manifest():
    descriptor = b"SOFTWARE_ONLY protected descriptor fixture"
    descriptor_sha = hashlib.sha256(descriptor).hexdigest()
    rows = tuple(MappingProxyType({**dict(row), "source_security_sha256": descriptor_sha,
        "target_security_sha256": descriptor_sha}) for row in _launch_manifest().vendor_files)
    java_source = r"C:\Staging\jdk11\bin\java.exe"
    javac_source = r"C:\Staging\jdk11\bin\javac.exe"
    def jdk(source, target, raw_hash):
        return MappingProxyType({"source_path": source, "target": target, "size": 12,
            "sha256": raw_hash, "source_file_id": ["1", "2"], "target_file_id": ["3", "4"],
            "source_security_sha256": descriptor_sha, "target_security_sha256": descriptor_sha})
    worker_pin = MappingProxyType({"source_path": r"C:\Staging\python312\tools\Lib\site-packages\comsol_mcp\worker_java\PersistentComsolWorker.java",
        "target": prepared.WORKER_SOURCE, "size": 166825, "sha256": prepared.WORKER_SOURCE_SHA256,
        "source_file_id": ["1", "9"], "source_security_sha256": descriptor_sha,
        "target_file_id": ["2", "10"], "target_security_sha256": descriptor_sha})
    generated = (MappingProxyType({"path": prepared.OUTPUT_DIR + "/comsol_mcp/worker_java/PersistentComsolWorker.class",
        "size": 16, "sha256": "9" * 64, "file_id": ["3", "11"],
        "security_sha256": descriptor_sha}),)
    manifest = prepared.PreparedLaunchManifest(
        path=ntpath.join(PREFIX, *prepared.MANIFEST_PATH.split("/")),
        installation_prefix=PREFIX, service_profile_sha256="c" * 64, plan_sha256="d" * 64,
        vendor_source_root=SOURCE_ROOT, vendor_files=rows,
        server_image=rows[0]["target"], server_config=rows[1]["target"], version_file=rows[2]["target"],
        ordered_classpath=(rows[3]["target"],), worker_source=prepared.WORKER_SOURCE,
        worker_source_sha256=prepared.WORKER_SOURCE_SHA256,
        worker_source_pin=worker_pin,
        jdk_files=MappingProxyType({"java": jdk(java_source, "jdk11/bin/java.exe", "f" * 64),
                                    "javac": jdk(javac_source, "jdk11/bin/javac.exe", "e" * 64)}),
        generated_classes=generated, role_cwds=MappingProxyType({"server": prepared.SERVER_CWD,
            "worker": prepared.WORKER_CWD}),
        role_cwd_facts=MappingProxyType({role: MappingProxyType({"file_id": ["1", "2"],
            "security_sha256": descriptor_sha}) for role in ("server", "worker")}),
        runtime_temporaries=MappingProxyType({"server": prepared.SERVER_TEMP, "worker": prepared.WORKER_TEMP}),
        configuration_files=(), _seal=prepared._MANIFEST_SEAL)
    factory = object.__new__(t037.T037SessionProcessFactory)
    factory.profile = NS(prepared_launch=manifest, server_image_sha256=b.digest(SERVER_SOURCE))
    factory._launch_os_roots = (WINROOT, SYSTEMROOT)
    return factory, manifest


def test_normal_server_and_worker_inputs_are_role_closed_and_consumed(monkeypatch):
    factory, manifest = _factory_and_manifest()
    verified = []
    monkeypatch.setattr(launch, "verify_regular_file", lambda path, **pins: verified.append((str(path), pins)))
    monkeypatch.setattr(launch, "verify_regular_directory", lambda path: None)
    private_root = r"C:\Sessions\s1\private"
    server_exe = ntpath.join(manifest.installation_prefix, *manifest.server_image.split("/"))
    server = launch.build_server_inputs(factory,
        NS(installation_root=SOURCE_ROOT),
        NS(private_installation=PureWindowsPath(private_root), temporary=PureWindowsPath(r"C:\Sessions\s1\tmp")),
        [server_exe, "-port", "2036"], stdio=("null", "log", "merge"))
    assert set(server.environment) == {"SystemRoot", "WINDIR", "SystemDrive", "PATH", "TEMP", "TMP"}
    assert "JAVA_HOME" not in server.environment and "COMSOL_MCP_WORKER_TOKEN" not in server.environment
    assert server.environment["PATH"].startswith(ntpath.join(manifest.prepared_vendor_root, "bin", "win64") + ";")
    server_options = {"cwd": server.cwd, "env": dict(server.environment), "executable": server.executable,
        "stdin": "null", "stdout": "log", "stderr": "merge", "creationflags": 0,
        "start_new_session": False, "text": False}
    with launch.activate_launch_inputs(factory, server):
        assert launch.consume_launch_inputs(factory, server.argv, server_options, "server") is server

    class Paths:
        comsol_root = PureWindowsPath(SOURCE_ROOT)
        jdk_home = PureWindowsPath(r"C:\Staging\jdk11")
        def executable(self, name):
            return PureWindowsPath(rf"C:\Staging\jdk11\bin\{name}.exe")

    worker = launch.build_worker_inputs(factory, Paths(),
        state_dir=PureWindowsPath(r"C:\Sessions\s1\worker-state"),
        temp_dir=PureWindowsPath(r"C:\Sessions\s1\worker-tmp"),
        preferences=None, endpoint=("127.0.0.1", 2037), server_lock_root=PureWindowsPath(r"C:\Sessions\locks"),
        generation=1, token="fixture-token", classpath=(rows_path := next(
            row["source_path"] for row in manifest.vendor_files if row["role"] == "classpath"),),
        stdio=("null", "null", "log"))
    assert worker.environment["JAVA_HOME"] == r"C:\Staging\jdk11"
    assert worker.environment["COMSOL_MCP_WORKER_TOKEN"] == "fixture-token"
    assert worker.classpath == (rows_path,)
    worker_options = {"cwd": worker.cwd, "env": dict(worker.environment), "executable": worker.executable,
        "stdin": "null", "stdout": "null", "stderr": "log", "creationflags": 0,
        "start_new_session": True, "text": True}
    with launch.activate_launch_inputs(factory, worker):
        assert launch.consume_launch_inputs(factory, worker.argv, worker_options, "worker") is worker
    assert len(verified) == 2  # Worker JDK executable plus the finite ordered source classpath.


def test_worker_launch_inputs_bind_original_createprocess_storage(monkeypatch):
    factory, manifest = _factory_and_manifest()
    monkeypatch.setattr(launch, "verify_regular_file", lambda *_args, **_kwargs: None)

    class Paths:
        comsol_root = PureWindowsPath(SOURCE_ROOT)
        jdk_home = PureWindowsPath(r"C:\Staging\jdk11")
        def executable(self, name):
            return PureWindowsPath(rf"C:\Staging\jdk11\bin\{name}.exe")

    inputs = launch.build_worker_inputs(factory, Paths(), state_dir=PureWindowsPath(r"C:\Sessions\s1\state"),
        temp_dir=PureWindowsPath(r"C:\Sessions\s1\temp"), preferences=None,
        endpoint=("127.0.0.1", 2037), server_lock_root=PureWindowsPath(r"C:\Sessions\locks"),
        generation=1, token="fixture-token", classpath=(next(
            row["source_path"] for row in manifest.vendor_files if row["role"] == "classpath"),),
        stdio=("null", "null", "log"))
    command = C.create_unicode_buffer(subprocess.list2cmdline(list(inputs.argv)))
    environment = (C.c_wchar * len(inputs.environment_block))(*inputs.environment_block)
    create_arguments = [None] * 10
    create_arguments[1] = inputs.executable
    create_arguments[7] = C.cast(environment, C.c_void_p)
    create_arguments[8] = inputs.cwd
    child = NS(_original_factory=factory, _original_profile=factory.profile, _original_role="worker",
        args=inputs.argv, _creation_parameters=(inputs.executable,),
        _call_storage=(None, None, command, environment), _create_arguments=create_arguments)
    inputs.validate_native_storage(child, NS(C=C))
    create_arguments[8] = r"C:\wrong\cwd"
    with pytest.raises(ExecutionContractError, match="CreateProcess storage"):
        inputs.validate_native_storage(child, NS(C=C))


def test_prepared_material_uses_current_held_reader_and_rejects_identity_drift(monkeypatch):
    factory, manifest = _factory_and_manifest()
    descriptor = b"SOFTWARE_ONLY protected descriptor fixture"
    security_sha = hashlib.sha256(descriptor).hexdigest()
    facts = {}

    def add(path, file_id, size, content_sha256):
        facts[path] = {"fingerprint": ((int(file_id[0]), int(file_id[1])), size, 1, 2, 3, 1),
            "descriptor": descriptor, "label": NS(ace=NS(rid=0x2000, mask=1, flags=0)),
            "content_sha256": content_sha256}

    for row in manifest.vendor_files:
        add(row["source_path"], row["source_file_id"], row["source_size"], row["source_sha256"])
        add(ntpath.join(manifest.installation_prefix, *row["target"].split("/")),
            row["target_file_id"], row["target_size"], row["target_sha256"])
    for row in manifest.jdk_files.values():
        add(ntpath.join(manifest.installation_prefix, *row["target"].split("/")),
            row["target_file_id"], row["size"], row["sha256"])
    add(ntpath.join(manifest.installation_prefix, *manifest.worker_source_pin["target"].split("/")),
        manifest.worker_source_pin["target_file_id"], manifest.worker_source_pin["size"],
        manifest.worker_source_pin["sha256"])
    for row in manifest.generated_classes:
        add(ntpath.join(manifest.installation_prefix, *row["path"].split("/")),
            row["file_id"], row["size"], row["sha256"])
    for role, relative in manifest.role_cwds.items():
        add(ntpath.join(manifest.installation_prefix, *relative.split("/")),
            manifest.role_cwd_facts[role]["file_id"], 0, None)

    class Reader:
        def __init__(self):
            self.paths = []
        def read(self, path, *, directory, content):
            self.paths.append((path, directory, content))
            return facts[path]

    reader = Reader()
    targets = __import__("comsol_mcp._windows_t037_targets", fromlist=["_descriptor"])
    monkeypatch.setattr(targets, "_require_held_finite_a", lambda _factory: NS(reader=reader))
    monkeypatch.setattr(targets, "_descriptor", lambda _raw: None)
    monkeypatch.setattr(launch, "verify_regular_directory", lambda _path: None)
    monkeypatch.setattr(Path, "iterdir", lambda _path: iter(()))
    monkeypatch.setattr(launch, "verify_regular_file", lambda *_args, **_kwargs: None)

    class Paths:
        comsol_root = PureWindowsPath(SOURCE_ROOT)
        jdk_home = PureWindowsPath(r"C:\Staging\jdk11")
        def executable(self, name):
            return PureWindowsPath(rf"C:\Staging\jdk11\bin\{name}.exe")

    inputs = launch.build_worker_inputs(factory, Paths(), state_dir=PureWindowsPath(r"C:\Sessions\s1\state"),
        temp_dir=PureWindowsPath(r"C:\Sessions\s1\temp"), preferences=None,
        endpoint=("127.0.0.1", 2037), server_lock_root=PureWindowsPath(r"C:\Sessions\locks"),
        generation=1, token="fixture-token", classpath=(next(
            row["source_path"] for row in manifest.vendor_files if row["role"] == "classpath"),),
        stdio=("null", "null", "log"))
    assert inputs.verify_protected_material(factory) is not None
    assert len(reader.paths) == 2 * len(manifest.vendor_files) + 2 + 1 + len(manifest.generated_classes) + 2

    target = ntpath.join(manifest.installation_prefix, *manifest.server_image.split("/"))
    old = facts[target]
    facts[target] = {**old, "fingerprint": ((999, 999), old["fingerprint"][1], *old["fingerprint"][2:])}
    with pytest.raises(ExecutionContractError, match="identity/extent"):
        inputs.verify_protected_material(factory)


def _wire_compiler_sdk(backend, *, create_mode="success", output_mode="main", exit_code=0):
    """Only SDK/filesystem leaves are synthetic; original lifecycle methods remain executable."""
    state = {"error": 0, "handles": 0, "create_mode": create_mode, "output_mode": output_mode,
             "exit_code": exit_code, "output_written": False, "create_args": None,
             "before_birth_names": None, "calls": []}

    def handle(kind, value):
        state["handles"] += 1
        return backend.handle(kind, value)

    def get_windows_root(buffer, capacity):
        assert capacity == 32768
        buffer.value = WINROOT
        state["calls"].append("GetSystemWindowsDirectoryW")
        return len(WINROOT)

    def get_system_root(buffer, capacity):
        assert capacity == 32768
        buffer.value = SYSTEMROOT
        state["calls"].append("GetSystemDirectoryW")
        return len(SYSTEMROOT)

    def create_job(_attributes, _name):
        state["calls"].append("CreateJobObjectW")
        return handle("job", {})

    def set_job(_job, level, pointer, size):
        limits = C.cast(pointer, C.POINTER(b._JobExtendedLimit)).contents
        assert level == 9 and size == C.sizeof(b._JobExtendedLimit)
        assert limits.basic.limit_flags == 0x2008 and limits.basic.active_process_limit == 1
        state["calls"].append("SetInformationJobObject")
        return 1

    def create_file(path, *args):
        if path == "NUL":
            assert args[0] == 0xC0000000 and args[1] == 3
            state["calls"].append("CreateFileW(NUL)")
            return handle("stdio", "NUL")
        return backend._create_or_open(path, *args)

    def initialize_attributes(pointer, count, flags, size_pointer):
        size = C.cast(size_pointer, C.POINTER(C.c_size_t)).contents
        if pointer is None:
            assert count == 2 and flags == 0
            size.value = 64
            state["error"] = 122
            backend.error = 122
            state["calls"].append("InitializeProcThreadAttributeList(size)")
            return 0
        assert count == 2 and flags == 0 and size.value == 64
        state["calls"].append("InitializeProcThreadAttributeList(init)")
        return 1

    def update_attribute(pointer, flags, attribute, value, size, _previous, _return_size):
        assert pointer is not None and flags == 0 and value is not None and size > 0
        state["calls"].append(("UpdateProcThreadAttribute", attribute))
        return 1

    def create_process(application, command_line, _process_attributes, _thread_attributes,
                       inherit, flags, environment, cwd, startup_pointer, process_info_pointer):
        assert application.startswith(PREFIX) and cwd.startswith(PREFIX)
        assert inherit is True and flags & 0x00000004 and flags & 0x00080000 and flags & 0x00000400
        assert C.cast(environment, C.c_void_p).value
        startup = C.cast(startup_pointer, C.POINTER(b._StartupInfoExW)).contents
        assert startup.startup.cb == C.sizeof(b._StartupInfoExW)
        assert startup.startup.flags == 0x100 and startup.startup.stdin == startup.startup.stdout == startup.startup.stderr
        root = ntpath.join(PREFIX, *prepared.OUTPUT_DIR.split("/"))
        state["before_birth_names"] = backend.names(root)
        state["create_args"] = (application, command_line, environment, cwd, startup_pointer,
                                 process_info_pointer)
        state["calls"].append("CreateProcessW")
        info = C.cast(process_info_pointer, C.POINTER(b._ProcessInformation)).contents
        if state["create_mode"] == "known-null":
            state["error"] = 5
            backend.error = 5
            return 0
        process = handle("process", {})
        info.process = process
        info.pid = 404
        if state["create_mode"] == "partial-known":
            state["error"] = 5
            backend.error = 5
            return 0
        if state["create_mode"] == "partial-success":
            return 1
        thread = handle("thread", {})
        info.thread = thread
        info.tid = 405
        if state["create_mode"] == "raise-after-output":
            raise OSError("SOFTWARE_ONLY CreateProcessW output then exception")
        return 1

    def emit_compiler_output():
        if state["output_written"] or state["output_mode"] == "empty":
            state["output_written"] = True
            return
        output = ntpath.join(PREFIX, *prepared.OUTPUT_DIR.split("/"))
        package = ntpath.join(output, "comsol_mcp")
        worker_package = ntpath.join(package, "worker_java")
        backend.add(package, True, sd=backend.tree[output]["sd"])
        backend.add(worker_package, True, sd=backend.tree[package]["sd"])
        if state["output_mode"] in {"main", "extra", "reparse"} or state["exit_code"] != 0:
            class_path = ntpath.join(worker_package, "PersistentComsolWorker.class")
            backend.add(class_path, False, b"SOFTWARE_ONLY compiled generic Worker class",
                        sd=backend.tree[worker_package]["sd"])
            if state["output_mode"] == "reparse":
                backend.tree[class_path]["reparse"] = True
        if state["output_mode"] == "extra":
            backend.add(ntpath.join(worker_package, "Unexpected.class"), False, b"extra",
                        sd=backend.tree[worker_package]["sd"])
        state["output_written"] = True

    def wait(_process, timeout):
        assert timeout == 600000
        state["calls"].append("WaitForSingleObject")
        emit_compiler_output()
        return 0

    def get_exit(_process, output):
        C.cast(output, C.POINTER(b.DWORD)).contents.value = state["exit_code"]
        state["calls"].append("GetExitCodeProcess")
        return 1

    def get_process_times(_process, creation, exit_time, kernel, user):
        for pointer, value in ((creation, 19), (exit_time, 0), (kernel, 1), (user, 2)):
            row = C.cast(pointer, C.POINTER(b._FileTime)).contents
            row.low, row.high = value, 0
        state["calls"].append("GetProcessTimes")
        return 1

    def query_job(_job, level, output, capacity, returned):
        row = C.cast(output, C.POINTER(b._JobAccounting)).contents
        assert level == 1 and capacity == C.sizeof(b._JobAccounting)
        row.total_processes, row.active_processes = 1, 0
        C.cast(returned, C.POINTER(b.DWORD)).contents.value = C.sizeof(b._JobAccounting)
        state["calls"].append("QueryInformationJobObject")
        return 1

    def assign_job(_job, _process):
        state["calls"].append("AssignProcessToJobObject")
        return 1

    def resume(_thread):
        state["calls"].append("ResumeThread")
        return 1

    sdk = {
        "GetSystemWindowsDirectoryW": get_windows_root,
        "GetSystemDirectoryW": get_system_root,
        "CreateJobObjectW": create_job,
        "SetInformationJobObject": set_job,
        "AssignProcessToJobObject": assign_job,
        "ResumeThread": resume,
        "WaitForSingleObject": wait,
        "GetExitCodeProcess": get_exit,
        "GetProcessTimes": get_process_times,
        "QueryInformationJobObject": query_job,
        "CreateFileW": create_file,
        "CloseHandle": backend._close,
        "CreateProcessW": create_process,
        "InitializeProcThreadAttributeList": initialize_attributes,
        "UpdateProcThreadAttribute": update_attribute,
        "DeleteProcThreadAttributeList": lambda _pointer: state["calls"].append("DeleteProcThreadAttributeList"),
    }
    backend.sdk.update(sdk)
    backend.system_roots = MethodType(b._Windows.system_roots, backend)
    backend.start_fixed_compiler = MethodType(b._Windows.start_fixed_compiler, backend)
    backend.close_fixed_compiler = MethodType(b._Windows.close_fixed_compiler, backend)
    backend.complete_fixed_compiler = MethodType(b._Windows.complete_fixed_compiler, backend)
    backend.last_compiler_state = state
    return state


def _prepared_installer(monkeypatch, *, create_mode="success", output_mode="main", exit_code=0):
    monkeypatch.setattr(config.native, "host_domains", lambda: dict(SCM_HOST))
    document, sources = v3_plan()
    _v2, _v2_sources, server = recipe_doc()
    backend = ConfigWindows(sources, server)
    vendor_bytes = {
        "bin/win64/comsol.exe": SERVER_SOURCE,
        "bin/win64/server.xml": CONFIG_SOURCE,
        "bin/win64/version.txt": VERSION_SOURCE,
        "plugins/COMSOL.jar": CLASSPATH_SOURCE,
        "ext/graphicsmagick/win64/CORE_RL_magick_.dll": b"IMAGE DLL FIXTURE",
    }
    for relative, raw in vendor_bytes.items():
        path = ntpath.join(SOURCE_ROOT, *relative.split("/"))
        parent = ntpath.dirname(path)
        pending = []
        while parent != SOURCE_ROOT and parent not in backend.tree:
            pending.append(parent)
            parent = ntpath.dirname(parent)
        for directory in reversed(pending):
            backend.add(directory, True)
        backend.add(path, False, raw)
    backend.original = copy.deepcopy(backend.tree)
    _wire_compiler_sdk(backend, create_mode=create_mode, output_mode=output_mode, exit_code=exit_code)
    original = b.Bootstrap(b.parse_plan(b.canonical(document)), backend, seal=b._TEST_SEAL)
    original.prepare()
    return original, backend


def test_v3_actual_bootstrap_compiler_capture_manifest_handoff_and_restore(monkeypatch):
    original, backend = _prepared_installer(monkeypatch)
    recipe = original.intent.prepared_launch
    assert original.intent.schema == b.SCHEMA_V3
    assert recipe.output_dir in original.intent.directories
    assert recipe.output_dir + "/comsol_mcp" not in original.intent.directories
    assert recipe.output_dir + "/comsol_mcp/worker_java" not in original.intent.directories

    result = original.apply(approved_plan_sha256=original.intent.sha256)
    assert result["native_start"] == "NOT_RUN"
    assert original.installer_compiler_state == "RETIRED"
    record = original._compiler_record
    assert type(record.process_info.process) is int and type(record.process_info.thread) is int
    assert record.resume_call.scalar == 1 and record.wait_call.scalar == 0
    assert record.exit_code == 0 and record.job_total == 1 and record.job_active == 0
    assert all(handle.closed and handle.close_call is not None for handle in
               (record.job, record.stdio, record.process, record.thread))
    assert backend.last_compiler_state["before_birth_names"] == set()
    assert set(record.capture_file_paths) == {
        prepared.OUTPUT_DIR + "/comsol_mcp/worker_java/PersistentComsolWorker.class"}
    assert {path for path, _node in record.capture_nodes} == {
        prepared.OUTPUT_DIR + "/comsol_mcp",
        prepared.OUTPUT_DIR + "/comsol_mcp/worker_java",
        prepared.OUTPUT_DIR + "/comsol_mcp/worker_java/PersistentComsolWorker.class"}
    assert "CreateProcessW" in backend.last_compiler_state["calls"]
    assert "AssignProcessToJobObject" in backend.last_compiler_state["calls"]
    assert "ResumeThread" in backend.last_compiler_state["calls"]
    assert "WaitForSingleObject" in backend.last_compiler_state["calls"]

    environment = record.environment_buffer
    environment_block = "\0".join(
        key + "=" + record.environment[key] for key in sorted(record.environment, key=str.casefold)) + "\0\0"
    assert "".join(environment[:len(environment_block)]) == environment_block
    assert environment[-2] == environment[-1] == "\0"
    assert C.cast(backend.last_compiler_state["create_args"][2], C.c_void_p).value == C.addressof(environment)
    assert record.argv[0] == ntpath.join(PREFIX, *prepared.COMPILER.split("/"))
    assert record.argv[record.argv.index("-classpath") + 1] == ntpath.join(
        PREFIX, *recipe.classpath[0].split("/"))
    assert record.argv[-1] == ntpath.join(PREFIX, *recipe.worker_source.split("/"))
    assert record.cwd == ntpath.join(PREFIX, *prepared.INSTALLER_CWD.split("/"))
    create_args = backend.last_compiler_state["create_args"]
    assert create_args[0] == record.argv[0] and C.wstring_at(create_args[1]) == subprocess.list2cmdline(list(record.argv))
    assert create_args[3] == record.cwd
    assert set(record.environment) == {"SystemRoot", "WINDIR", "SystemDrive", "PATH", "JAVA_HOME", "TEMP", "TMP"}

    manifest_node = original.nodes[prepared.MANIFEST_PATH]
    manifest = prepared.validate_manifest(backend.read(manifest_node.handle, manifest_node.image.size))
    assert manifest["compiler"]["exit_code"] == 0
    assert manifest["prepared"] is True
    for row in manifest["vendor_files"]:
        target_node = original.nodes[row["target"]]
        assert row["target_security_sha256"] == b.digest(target_node.image.raw_sd)
        assert row["target_security_sha256"] != row["source_security_sha256"]
    original._configuration.compare_generated_manifest()
    original._compare()
    original.handoff_runtime()
    assert original.state == "RUNTIME_READ_PINNED_START_BLOCKED"
    original.restore()
    assert original.state == "RESTORED" and backend.tree == backend.original
    assert original.installer_compiler_state == "RETIRED"


@pytest.mark.parametrize(("create_mode", "expected_state"), [
    ("known-null", "FAILED_NO_CHILD"),
    ("partial-known", "UNKNOWN"),
    ("partial-success", "UNKNOWN"),
    ("raise-after-output", "UNKNOWN"),
])
def test_v3_actual_createprocess_null_partial_and_throw_outputs_remain_original(
        monkeypatch, create_mode, expected_state):
    original, backend = _prepared_installer(monkeypatch, create_mode=create_mode)
    with pytest.raises(b.BootstrapError):
        original.apply(approved_plan_sha256=original.intent.sha256)
    assert original.installer_compiler_state == expected_state
    record = original._compiler_record
    assert record is not None and record.owner is original.owner and record.process_info is not None
    if create_mode == "known-null":
        assert prepared.b._raw_process_information_handle(record.process_info, "process") == 0
        assert prepared.b._raw_process_information_handle(record.process_info, "thread") == 0
        assert record.failed_no_child_verified(original)
        assert not original.owner.uncertain
    else:
        assert original.owner.uncertain
        assert record.create_call.state == "UNKNOWN" if create_mode == "raise-after-output" else True
        assert any(handle.state == "UNKNOWN" for handle in (record.process, record.thread))


def test_v3_nonzero_compiler_captures_partial_output_and_stays_not_prepared(monkeypatch):
    original, backend = _prepared_installer(monkeypatch, exit_code=1)
    with pytest.raises(b.BootstrapError, match="nonzero exit code"):
        original.apply(approved_plan_sha256=original.intent.sha256)
    assert original.installer_compiler_state == "RETIRED"
    assert original._compiler_record.output_capture
    manifest_node = original.nodes[prepared.MANIFEST_PATH]
    manifest = prepared.validate_manifest(backend.read(manifest_node.handle, manifest_node.image.size))
    assert manifest["prepared"] is False and manifest["compiler"]["exit_code"] == 1
    original._compare()


@pytest.mark.parametrize("output_mode", ["extra", "reparse"])
def test_v3_compiler_capture_rejects_extra_or_reparse_output(monkeypatch, output_mode):
    original, _backend = _prepared_installer(monkeypatch, output_mode=output_mode)
    with pytest.raises(b.BootstrapError):
        original.apply(approved_plan_sha256=original.intent.sha256)
    assert original.installer_compiler_state == "EXIT_KNOWN"
    assert original.state == "UNKNOWN" and original.owner.uncertain
    assert original._compiler_record.output_capture is None


def test_v2_original_four_config_path_never_constructs_compiler(monkeypatch):
    monkeypatch.setattr(config.native, "host_domains", lambda: dict(SCM_HOST))
    original, backend, _document = v2_config_fixture(prepared=True)
    assert original.intent.schema == b.SCHEMA_V2
    original.apply(approved_plan_sha256=original.intent.sha256)
    assert original.installer_compiler_state == "NEVER_STARTED"
    assert original._compiler_record is None
    assert all(call.name not in {"CreateProcessW", "CreateJobObjectW"} for call in original.owner.calls)


def test_win32_launch_api_restores_constructor_abi_and_reads_only_two_shared_roots(monkeypatch):
    calls = []

    def api_function(name, implementation=None):
        def invoke(*args):
            calls.append((name, args))
            return implementation(*args) if implementation is not None else 1
        invoke.__name__ = name
        return invoke

    windows_types = NS(HANDLE=C.c_void_p, LPCWSTR=C.c_wchar_p, LPWSTR=C.c_wchar_p,
                       DWORD=C.c_uint32, UINT=C.c_uint32, BOOL=C.c_int)
    kernel_names = (
        "GetCurrentProcess", "CreateJobObjectW", "SetInformationJobObject", "AssignProcessToJobObject",
        "ResumeThread", "WaitForSingleObject", "GetExitCodeProcess", "TerminateProcess", "CreateFileW",
        "DuplicateHandle", "InitializeProcThreadAttributeList", "UpdateProcThreadAttribute",
        "DeleteProcThreadAttributeList", "GetSystemWindowsDirectoryW", "GetSystemDirectoryW",
    )
    kernel = NS(**{name: api_function(name) for name in kernel_names})
    advapi = NS(**{name: api_function(name) for name in (
        "DuplicateTokenEx", "ConvertStringSidToSidW", "CreateProcessAsUserW", "CreateRestrictedToken", "LookupPrivilegeNameW")})

    def bind_readonly_fixture(api):
        api.C, api.W, api.k, api.a = C, windows_types, kernel, advapi

    monkeypatch.setattr(t037._Win32ReadOnlyApi, "__init__", bind_readonly_fixture)
    api = object.__new__(t037._Win32LaunchApi)
    api.__init__()

    expected = {
        "GetCurrentProcess": ([], windows_types.HANDLE),
        "CreateJobObjectW": ([C.c_void_p, windows_types.LPCWSTR], windows_types.HANDLE),
        "SetInformationJobObject": ([windows_types.HANDLE, C.c_int, C.c_void_p, windows_types.DWORD], windows_types.BOOL),
        "AssignProcessToJobObject": ([windows_types.HANDLE, windows_types.HANDLE], windows_types.BOOL),
        "ResumeThread": ([windows_types.HANDLE], windows_types.DWORD),
        "WaitForSingleObject": ([windows_types.HANDLE, windows_types.DWORD], windows_types.DWORD),
        "GetExitCodeProcess": ([windows_types.HANDLE, C.POINTER(windows_types.DWORD)], windows_types.BOOL),
        "TerminateProcess": ([windows_types.HANDLE, windows_types.UINT], windows_types.BOOL),
        "CreateFileW": ([windows_types.LPCWSTR, windows_types.DWORD, windows_types.DWORD, C.c_void_p,
                         windows_types.DWORD, windows_types.DWORD, windows_types.HANDLE], windows_types.HANDLE),
        "DuplicateHandle": ([windows_types.HANDLE, windows_types.HANDLE, windows_types.HANDLE,
                            C.POINTER(windows_types.HANDLE), windows_types.DWORD, windows_types.BOOL,
                            windows_types.DWORD], windows_types.BOOL),
        "InitializeProcThreadAttributeList": ([C.c_void_p, windows_types.DWORD, windows_types.DWORD,
                                                C.POINTER(C.c_size_t)], windows_types.BOOL),
        "UpdateProcThreadAttribute": ([C.c_void_p, windows_types.DWORD, C.c_size_t, C.c_void_p,
                                       C.c_size_t, C.c_void_p, C.c_void_p], windows_types.BOOL),
        "DeleteProcThreadAttributeList": ([C.c_void_p], None),
    }
    for name, (arguments, result) in expected.items():
        function = getattr(api.k, name)
        assert function.argtypes == arguments and function.restype is result
    assert api.a.DuplicateTokenEx.argtypes == [windows_types.HANDLE, windows_types.DWORD, C.c_void_p,
        C.c_int, C.c_int, C.POINTER(windows_types.HANDLE)]
    assert api.a.DuplicateTokenEx.restype is windows_types.BOOL
    assert api.a.ConvertStringSidToSidW.argtypes == [windows_types.LPCWSTR, C.POINTER(C.c_void_p)]
    assert api.a.ConvertStringSidToSidW.restype is windows_types.BOOL
    assert api.a.CreateProcessAsUserW.argtypes == [windows_types.HANDLE, windows_types.LPCWSTR,
        windows_types.LPWSTR, C.c_void_p, C.c_void_p, windows_types.BOOL, windows_types.DWORD,
        C.c_void_p, windows_types.LPCWSTR, C.c_void_p, C.c_void_p]
    assert api.a.CreateProcessAsUserW.restype is windows_types.BOOL

    def windows_root(buffer, capacity):
        assert capacity == 32768
        buffer.value = WINROOT
        return len(WINROOT)

    def system_root(buffer, capacity):
        assert capacity == 32768
        buffer.value = SYSTEMROOT
        return len(SYSTEMROOT)

    api.k.GetSystemWindowsDirectoryW = api_function("GetSystemWindowsDirectoryW", windows_root)
    api.k.GetSystemDirectoryW = api_function("GetSystemDirectoryW", system_root)
    assert api.system_roots() == (WINROOT, SYSTEMROOT)
    assert [name for name, _args in calls[-2:]] == ["GetSystemWindowsDirectoryW", "GetSystemDirectoryW"]
    assert all(len(args) == 2 for _name, args in calls[-2:])


def test_normal_t037_server_command_uses_protected_image_and_separate_mutable_directories(monkeypatch):
    factory, manifest = _factory_and_manifest()
    monkeypatch.setattr(server_runtime, "_t037_network_intent", lambda _factory: NS(server_listen_port=2036))
    private = PureWindowsPath(r"C:\Sessions\s1\private")
    directories = ServerDirectories(
        root=PureWindowsPath(r"C:\Sessions\s1"), private_installation=private,
        runtime=PureWindowsPath(r"C:\Sessions\s1\runtime"),
        preferences=PureWindowsPath(r"C:\Sessions\s1\preferences"),
        temporary=PureWindowsPath(r"C:\Sessions\s1\tmp"),
        recovery=PureWindowsPath(r"C:\Sessions\s1\recovery"),
        logs=PureWindowsPath(r"C:\Sessions\s1\logs"),
        port_file=PureWindowsPath(r"C:\Sessions\s1\runtime\server.port"),
        log_file=PureWindowsPath(r"C:\Sessions\s1\logs\mphserver.log"),
    )
    command = server_runtime.build_server_command(directories, platform_name="windows",
        validate_launcher=False, process_factory=factory)
    protected_image = ntpath.join(manifest.installation_prefix, *manifest.server_image.split("/"))
    assert command[0] == protected_image
    assert ntpath.commonpath((ntpath.normcase(command[0]), ntpath.normcase(manifest.prepared_vendor_root))) == ntpath.normcase(
        manifest.prepared_vendor_root)
    assert str(private).casefold() not in command[0].casefold()
    fixed_values = {
        "-port": "2036", "-portfile": str(directories.port_file), "-prefsdir": str(directories.preferences),
        "-tmpdir": str(directories.temporary), "-recoverydir": str(directories.recovery),
    }
    for option, value in fixed_values.items():
        assert command[command.index(option) + 1] == value
    assert len({fixed_values[key] for key in ("-portfile", "-prefsdir", "-tmpdir", "-recoverydir")}) == 4


@pytest.mark.parametrize("damage", ["foreign-phase-name", "replaced-output-handle"])
def test_v3_real_capture_seal_rejects_phase_mismatch_or_replaced_original_binding(monkeypatch, damage):
    seal_original = config._Configuration.seal_launch_outputs

    def damage_before_real_seal(configuration, record, *, allow_empty=False):
        if damage == "foreign-phase-name":
            record.namespace_before_capture = frozenset((*record.namespace_before_capture, "foreign/not-owned"))
        else:
            rows = list(record.capture_bindings)
            path, node, _handle, image = rows[0]
            rows[0] = (path, node, object(), image)
            record.capture_bindings = tuple(rows)
        return seal_original(configuration, record, allow_empty=allow_empty)

    monkeypatch.setattr(config._Configuration, "seal_launch_outputs", damage_before_real_seal)
    original, _backend = _prepared_installer(monkeypatch)
    with pytest.raises(b.BootstrapError) as failure:
        original.apply(approved_plan_sha256=original.intent.sha256)
    assert failure.value.code == "CONFIG_MANIFEST"
    assert original.installer_compiler_state == "EXIT_KNOWN"
    assert original.state == "UNKNOWN" and original.owner.uncertain


@pytest.mark.parametrize("transition", ["handoff", "restore"])
def test_v3_sealed_output_binding_replacement_blocks_original_transition(monkeypatch, transition):
    original, _backend = _prepared_installer(monkeypatch)
    original.apply(approved_plan_sha256=original.intent.sha256)
    record = original._compiler_record
    path, node, _handle, image = record.capture_bindings[0]
    record.capture_bindings = ((path, node, object(), image), *record.capture_bindings[1:])
    with pytest.raises(b.BootstrapError) as failure:
        if transition == "handoff":
            original.handoff_runtime()
        else:
            original.restore()
    assert failure.value.code == "CONFIG_ORIGIN"
    assert original.owner.uncertain and original.state == "UNKNOWN"
    assert original.handoff_started is False and original.restore_started is False
