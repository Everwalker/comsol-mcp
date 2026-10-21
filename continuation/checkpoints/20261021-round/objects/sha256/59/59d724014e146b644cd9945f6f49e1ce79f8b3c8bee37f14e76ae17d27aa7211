"""Synthetic source controls for the R17 runtime-input producer boundary.

The runtime trees below are deliberately synthetic. These tests exercise the
existing 43-input consumer and the exact BUILD_RESULT/PASS receipt shapes; they
do not claim runtime build or native acceptance.
"""
from __future__ import annotations

import hashlib
import contextlib
import json
import io
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "source"
sys.path.insert(0, str(SOURCE))

from tools import w26_application_lifecycle_current as lifecycle  # noqa: E402
from tools import w26_sealed_data_current as data  # noqa: E402
from tools import w26_dependency_unit_current as deps  # noqa: E402
from tools.w26_prepare_current_inputs import _helpers  # noqa: E402


PASS_KEYS = {
    "argv", "birth", "diagnostic_errors", "finished_unix", "group",
    "owned_group_absent", "owned_process_terminal", "parent_pid", "pid",
    "rc", "requested_new_session", "result_name", "status", "stderr",
    "stdout", "uid_at_spawn",
}
PROFILE_MODULES = {
    "_ssl", "_hashlib", "ssl", "hashlib", "zlib", "sqlite3", "ctypes",
    "venv", "_lzma", "_gdbm", "_tkinter",
}
PROVIDER = {"INFO": [3, 5, 0, 9, 0], "NUMBER": 0x30500090,
            "TEXT": "OpenSSL 3.5.9 29 Sep 2026"}
PERSISTENT_INPUT_PACKET = ROOT.parent / "w26-r17-current175-r12-runtime-input-bridge"
PERSISTENT43_MANIFEST = PERSISTENT_INPUT_PACKET / "inputs/current43-reuse-r16-r2/INPUTS_MANIFEST_R17.json"
PERSISTENT43_MANIFEST_SHA256 = "651cfc79e5c8aed521e7bf1669fdb4b85eb5595857184d20b948c161adecf5c5"
INPUTS = PERSISTENT43_MANIFEST.parent / "wheelhouse"


def _new_root(prefix: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix, dir="/private/tmp")).resolve()


def _write_private(path: Path, raw: bytes) -> dict:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(raw)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
    finally:
        os.close(fd)
    return _helpers().raw_file(path)[1]


def _labels() -> list[str]:
    result = []
    for arch in ("arm64", "x86_64"):
        for component in ("openssl", "xz", "gdbm", "tcl", "tk"):
            result.extend((component + "-" + arch + "-configure",
                           component + "-" + arch + "-build",
                           component + "-" + arch + "-install"))
    result.extend("fat-" + name for name in (
        "libssl.a", "libcrypto.a", "liblzma.a", "libgdbm.a", "libtcl8.6.a", "libtk8.6.a"))
    result.extend(("cpython-configure", "cpython-build", "cpython-install",
                   "runtime-required-profile", "runtime-relocation-profile"))
    return result


def _synthetic_pass(label: str, root: Path, index: int) -> dict:
    pid = 1000 + index
    argv = ["/usr/bin/sandbox-exec", "-f", str(root / "BUILD_SANDBOX.sb"),
            "/usr/local/bin/python3", "-I", "-S", "-B",
            str(SOURCE / "tools" / "w26_hold_exec.py"), "--stage",
            str(root / "logs" / ("STAGE_" + str(index) + ".json")), "--sha", "a" * 64,
            "--go", str(root / "logs" / ("GO_" + str(index) + ".json"))]
    return {
        "argv": argv,
        "birth": {
            "actual_ps_command": "synthetic source-control record",
            "birth_observed": "synthetic-only",
            "invocation_argv": argv,
            "pid": pid,
            "ppid": 77,
            "raw": {"argv": ["/bin/ps", "-p", str(pid)], "rc": 0,
                    "stderr": "", "stdout": str(pid)},
            "status": "OBSERVED",
            "uid": os.getuid(),
        },
        "diagnostic_errors": [],
        "finished_unix": 1.0,
        "group": {"actual_pgid": pid, "actual_sid": pid,
                  "independent_session_observed": True, "observed_unix": 1.0,
                  "parent_sid": 77, "status": "OBSERVED"},
        "owned_group_absent": True,
        "owned_process_terminal": True,
        "parent_pid": 77,
        "pid": pid,
        "rc": 0,
        "requested_new_session": True,
        "result_name": label + "-" + uuid.uuid4().hex + ".result.json",
        "status": "PASS",
        "stderr": label + ".stderr",
        "stdout": label + ".stdout",
        "uid_at_spawn": os.getuid(),
    }


def _synthetic_profile(prefix: Path, mode: str, files: dict[str, dict]) -> dict:
    executable = prefix / "bin/python3.12"
    return {
        "provider": PROVIDER,
        "python_version": "3.12.13 (synthetic source-control fixture)",
        "executable": str(executable),
        "prefix": str(prefix),
        "platform": "arm64",
        "status": "PASS_ARM_SSL359_LZMA_GDBM_TCL_HEADLESS_REQUIRED_RUNTIME",
        "required_modules": files,
        "sha256_known_vector": True,
        "zlib_roundtrip": True,
        "sqlite_roundtrip": True,
        "ctypes_pointer64": True,
        "actual_certificate_cases": [],
        "actual_MemoryBIO": [],
        "SSLContext_min_TLS12": True,
        "gdbm_roundtrip": True,
        "Tcl_scripts": {},
        "mode": mode,
        "delivery_acceptance": "NOT_RUN",
        "optional_dbm_missing": False,
        "Tk_window_created": False,
        "GUI_acceptance": "NOT_RUN",
        "physical_Intel_acceptance": "NOT_RUN",
        "COMSOL": "NOT_RUN",
        "dependency43_install": "NOT_RUN",
    }


class RuntimeInputBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dep_root = _new_root("cvw26-r17-bridge-dependencies-")
        cls.addClassCleanup(shutil.rmtree, cls.dep_root)
        cls.runtime_roots: list[Path] = []
        cls.addClassCleanup(cls._cleanup_roots)
        cls._prepare_actual43_input_namespace()

    @classmethod
    def _cleanup_roots(cls):
        for root in reversed(cls.runtime_roots):
            shutil.rmtree(root, ignore_errors=True)

    @classmethod
    def _prepare_actual43_input_namespace(cls):
        h = _helpers()
        persistent_raw, persistent_record = h.raw_file(PERSISTENT43_MANIFEST)
        if persistent_record["sha256"] != PERSISTENT43_MANIFEST_SHA256:
            raise RuntimeError("R17 persistent exact43 input manifest digest differs")
        persistent = json.loads(persistent_raw)
        persistent_rows = {row["filename"]: row["current_record"] for row in persistent["rows"]}
        identity = h.secure_dir(cls.dep_root)
        h.publish(cls.dep_root / "FIRST_RECORD.json", {
            "root": str(cls.dep_root), "root_identity": identity,
            "source_sha256": hashlib.sha256(deps.__file__.encode()).hexdigest(),
            "closure_sha256": __import__("tools.w26_prepare_current_inputs", fromlist=["CLOSURE_SHA"]).CLOSURE_SHA,
            "native_authorized": False,
        })
        wheelhouse = cls.dep_root / "wheelhouse"
        wheelhouse.mkdir(mode=0o700)
        closure = deps.load_closure()
        rows = []
        for pin in closure["actual_inputs"]:
            source = INPUTS / pin["filename"]
            raw, _ = h.raw_file(source, expected=persistent_rows[pin["filename"]])
            deps.validate_wheel(raw, pin)
            target = wheelhouse / pin["filename"]
            record = _write_private(target, raw)
            rows.append({"filename": pin["filename"], "current_record": record})
        for role in ("backend", "runtime"):
            _write_private(cls.dep_root / ("requirements-" + role + ".lock"),
                           __import__("tools.w26_prepare_current_inputs", fromlist=["lock_bytes"]).lock_bytes(role))
        h.publish(cls.dep_root / "INPUTS_MANIFEST.json", {"all43_present": True, "rows": rows})

    def _runtime_fixture(self, *, bad_relocation=False, wrong_python_record=False) -> tuple[Path, str]:
        h = _helpers()
        root = _new_root("cvw26-r17-bridge-runtime-")
        self.runtime_roots.append(root)
        identity = h.secure_dir(root)
        h.publish(root / "PREPARE_FIRST_RECORD.json", {
            "root_identity": identity, "native_Popen": 0,
            "actor_sha256": hashlib.sha256(b"synthetic source-control actor").hexdigest(),
        })
        base = root / "install/cpython"
        relocated = root / "build/relocation-probe/install/cpython"
        modules = {
            "_ssl": "lib/python3.12/lib-dynload/_ssl.cpython-312-darwin.so",
            "_hashlib": "lib/python3.12/lib-dynload/_hashlib.cpython-312-darwin.so",
            "ssl": "lib/python3.12/ssl.py", "hashlib": "lib/python3.12/hashlib.py",
            "zlib": "lib/python3.12/lib-dynload/zlib.cpython-312-darwin.so",
            "sqlite3": "lib/python3.12/sqlite3/__init__.py", "ctypes": "lib/python3.12/ctypes/__init__.py",
            "venv": "lib/python3.12/venv/__init__.py",
            "_lzma": "lib/python3.12/lib-dynload/_lzma.cpython-312-darwin.so",
            "_gdbm": "lib/python3.12/lib-dynload/_gdbm.cpython-312-darwin.so",
            "_tkinter": "lib/python3.12/lib-dynload/_tkinter.cpython-312-darwin.so",
        }
        binary_names = {
            "python3.12": "bin/python3.12",
            "_ssl.cpython-312-darwin.so": modules["_ssl"],
            "_hashlib.cpython-312-darwin.so": modules["_hashlib"],
            "_lzma.cpython-312-darwin.so": modules["_lzma"],
            "_gdbm.cpython-312-darwin.so": modules["_gdbm"],
            "_tkinter.cpython-312-darwin.so": modules["_tkinter"],
            "_sqlite3.cpython-312-darwin.so": "lib/python3.12/lib-dynload/_sqlite3.cpython-312-darwin.so",
            "_ctypes.cpython-312-darwin.so": "lib/python3.12/lib-dynload/_ctypes.cpython-312-darwin.so",
            "zlib.cpython-312-darwin.so": modules["zlib"],
        }
        base.mkdir(mode=0o700, parents=True)
        relocated.mkdir(mode=0o700, parents=True)
        for directory in (root / "install", root / "build", root / "build/relocation-probe",
                          root / "build/relocation-probe/install"):
            directory.chmod(0o700)
        base_records = {}
        for index, (module, relative) in enumerate(modules.items()):
            target = base / relative
            record = _write_private(target, ("synthetic module " + module).encode())
            base_records[module] = record
        python_record = _write_private(base / "bin/python3.12", b"synthetic runtime executable")
        base_records["_python"] = python_record
        _write_private(base / "lib/python3.12/lib-dynload/_sqlite3.cpython-312-darwin.so",
                       b"synthetic module _sqlite3")
        _write_private(base / "lib/python3.12/lib-dynload/_ctypes.cpython-312-darwin.so",
                       b"synthetic module _ctypes")
        relocated_records = {}
        for module, relative in modules.items():
            relocated_records[module] = _write_private(relocated / relative,
                                                        ("synthetic relocated module " + module).encode())
        _write_private(relocated / "bin/python3.12", b"synthetic relocated runtime executable")
        if wrong_python_record:
            other = _write_private(base / "rogue/python3.12", b"not the selected executable")
            python_record = other
        base_profile = _synthetic_profile(base, "build-local", {k: v for k, v in base_records.items() if k != "_python"})
        relocation_profile = _synthetic_profile(relocated, "relocated", relocated_records)
        if bad_relocation:
            relocation_profile["status"] = "FAIL_SYNTHETIC_RELOCATION_PROFILE"
        logs = root / "logs"
        logs.mkdir(mode=0o700)
        relocation_logs = root / "build/relocation-probe/logs"
        relocation_logs.mkdir(mode=0o700)
        base_profile_record = h.publish(logs / "RUNTIME_PROFILE_RESULT.json", base_profile)
        relocation_profile_record = h.publish(relocation_logs / "RUNTIME_PROFILE_RESULT.json", relocation_profile)
        fat_rows = []
        for name, relative in binary_names.items():
            path = base / relative
            record = h.raw_file(path)[1]
            if wrong_python_record and name == "python3.12":
                record = python_record
            digest = record["sha256"]
            fat_rows.append({"RPATH": "ABSENT", "physical_Intel": "NOT_RUN", "record": record,
                "fat_MachO": {arch: {"architecture": arch, "bytes": record["bytes"], "executed": False,
                    "filetype": 2, "linked_dylibs": [], "minimum_os": [11, 0, 0], "sha256": digest}
                    for arch in ("arm64", "x86_64")}})
        result = {
            "status": "PASS_CURRENT_RUNTIME_BUILD_ARM_PROFILE_FAT_BYTES_SCOPED",
            "commands": [_synthetic_pass(label, root, index) for index, label in enumerate(_labels())],
            "runtime_profile": base_profile_record,
            "relocation_profile": relocation_profile_record,
            "runtime_fat_bytes": fat_rows,
            "samples": [], "physical_Intel": "NOT_RUN", "full_product_install": "NOT_RUN",
        }
        result_record = h.publish(root / "BUILD_RESULT.json", result)
        return root, result_record["sha256"]

    def test_actual_input_bridge_positive_and_negative_controls(self):
        h = _helpers()
        # Wrong producer digest and a replaced result are refused before any
        # consumer input is published.
        wrong_sha_root, actual_sha = self._runtime_fixture()
        with self.assertRaisesRegex(RuntimeError, "explicit expected SHA-256"):
            deps.prepare_runtime_input(self.dep_root, build_root=wrong_sha_root,
                                       expected_build_result_sha256="0" * 64)
        self.assertFalse((self.dep_root / "CURRENT_RUNTIME_INPUT.json").exists())
        result_path = wrong_sha_root / "BUILD_RESULT.json"
        result_path.write_bytes(result_path.read_bytes() + b" ")
        with self.assertRaisesRegex(RuntimeError, "explicit expected SHA-256"):
            deps.prepare_runtime_input(self.dep_root, build_root=wrong_sha_root,
                                       expected_build_result_sha256=actual_sha)
        self.assertFalse((self.dep_root / "CURRENT_RUNTIME_INPUT.json").exists())

        # A transplanted BUILD_RESULT cannot carry its original profile paths
        # into a different runtime root.
        crossed_root, crossed_sha = self._runtime_fixture()
        other_root = _new_root("cvw26-r17-bridge-runtime-crossed-")
        self.runtime_roots.append(other_root)
        h.publish(other_root / "PREPARE_FIRST_RECORD.json", {
            "root_identity": h.secure_dir(other_root), "native_Popen": 0,
            "actor_sha256": hashlib.sha256(b"synthetic source-control actor").hexdigest(),
        })
        shutil.copy2(crossed_root / "BUILD_RESULT.json", other_root / "BUILD_RESULT.json")
        with self.assertRaisesRegex(RuntimeError, "build root/runner"):
            deps.prepare_runtime_input(self.dep_root, build_root=other_root,
                                       expected_build_result_sha256=crossed_sha)

        nonproducer_root, nonproducer_sha = self._runtime_fixture(wrong_python_record=True)
        with self.assertRaisesRegex(RuntimeError, "base Python executable"):
            deps.prepare_runtime_input(self.dep_root, build_root=nonproducer_root,
                                       expected_build_result_sha256=nonproducer_sha)

        relocation_root, relocation_sha = self._runtime_fixture(bad_relocation=True)
        with self.assertRaisesRegex(RuntimeError, "runtime profile mode/platform"):
            deps.prepare_runtime_input(self.dep_root, build_root=relocation_root,
                                       expected_build_result_sha256=relocation_sha)

        good_root, good_sha = self._runtime_fixture()
        manifest_record = h.raw_file(self.dep_root / "INPUTS_MANIFEST.json")[1]
        output_record = deps.prepare_runtime_input(self.dep_root, build_root=good_root,
                                                   expected_build_result_sha256=good_sha)
        self.assertEqual(Path(output_record["path"]), self.dep_root / "CURRENT_RUNTIME_INPUT.json")
        runtime = json.loads(h.raw_file(Path(output_record["path"]), expected=output_record)[0])
        self.assertEqual(set(("base_python_record", "base_prefix", "actual_profile_first_record",
                              "actual_relocation_profile_first_record", "actual_BUILD_RESULT_first_record"))
                         - set(runtime), set())
        self.assertEqual(runtime["base_python_record"]["path"],
                         str(good_root / "install/cpython/bin/python3.12"))
        accepted = deps.verify_inputs(self.dep_root, manifest_record, output_record)
        self.assertEqual(accepted["base_python_record"], runtime["base_python_record"])
        candidate_record = deps.prepare(self.dep_root, tag="r17synthetic")
        candidate = json.loads(h.raw_file(Path(candidate_record["path"]), expected=candidate_record)[0])
        source = json.loads(h.raw_file(Path(candidate["source"]["path"]),
                                      expected=candidate["source"])[0])
        for path in (Path(deps.actor.__file__).resolve(strict=True), Path(deps.__file__).resolve(strict=True)):
            record = h.raw_file(path)[1]
            self.assertEqual(source[str(path)], record)
        self.assertEqual(candidate["status"], "READY_CURRENT43_FOR_MAIN_REVIEW_NOT_EXECUTION_APPROVED")
        # verify_actor is a fail-closed validator with no success return value.
        # Its successful completion (rather than a fabricated truthy result) is
        # the assertion; malformed source records raise from the call.
        deps.actor.verify_actor(candidate)
        before = h.raw_file(Path(output_record["path"]))[1]
        with self.assertRaisesRegex(RuntimeError, "already exists; O_EXCL"):
            deps.prepare_runtime_input(self.dep_root, build_root=good_root,
                                       expected_build_result_sha256=good_sha)
        self.assertEqual(h.raw_file(Path(output_record["path"]))[1], before)

    def test_cli_accepts_only_the_finite_runtime_input_invocation(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                deps.main(["--root", str(self.dep_root), "--prepare-runtime-input"])
            with self.assertRaises(SystemExit):
                deps.main(["--root", str(self.dep_root), "--build-root", str(self.dep_root),
                           "--expected-build-result-sha256", "a" * 64])
            with self.assertRaises(SystemExit):
                deps.main(["--prepare-runtime-input", "--root", str(self.dep_root),
                           "--build-root", str(self.dep_root), "--expected-build-result-sha256",
                           "a" * 64, "--tag=deps1"])


class PersistentCurrent43ManifestTests(unittest.TestCase):
    def test_r17_persistent_manifest_rehashes_all_exact_wheels(self):
        h = _helpers()
        raw, manifest_record = h.raw_file(PERSISTENT43_MANIFEST)
        self.assertEqual(manifest_record["sha256"], PERSISTENT43_MANIFEST_SHA256)
        manifest = json.loads(raw)
        closure = deps.load_closure()
        pins = {row["filename"]: row for row in closure["actual_inputs"]}
        self.assertEqual(len(manifest["rows"]), 43)
        self.assertEqual({row["filename"] for row in manifest["rows"]}, set(pins))
        wheelhouse = Path(manifest["wheelhouse"])
        self.assertEqual(wheelhouse, PERSISTENT43_MANIFEST.parent / "wheelhouse")
        for row in manifest["rows"]:
            self.assertEqual(row["current_record"]["path"], str(wheelhouse / row["filename"]))
            blob, _ = h.raw_file(wheelhouse / row["filename"], expected=row["current_record"])
            deps.validate_wheel(blob, pins[row["filename"]])
        coremedia = next(row for row in manifest["rows"] if row["name"] == "pyobjc-framework-coremedia")
        self.assertEqual(coremedia["current_record"]["sha256"],
                         "84f5deee8d366bf73069a0525509637aa578ccd0e6688923b09999a8bc3077a8")
        self.assertEqual(manifest["wheel_code_imported"], False)


class ActualLifecycleConsumerArgumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cvw26-r17-actual-argv-", dir="/private/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.h = _helpers()
        self.data_spec_record = self.h.raw_file(
            PERSISTENT_INPUT_PACKET / "inputs/w26_r13_explicit_data_input_spec_R16.json")[1]

    def _profile_spec_record(self, profile: str) -> dict:
        lock = data.R12_LOCK if profile == data.R12_PROFILE else data.R11_LOCK
        sources = {}
        for name in data.CURRENT_NAMES:
            path = data.CURRENT_TOOLS_ROOT / name
            sources[str(path)] = self.h.raw_file(path)[1]
        spec = data.build_current_profile_spec(profile, self.h.raw_file(lock)[1], sources)
        return self.h.publish(self.root / (profile.replace("/", "_") + "-" + uuid.uuid4().hex + ".json"), spec)

    def _exercise_wrapped_consumer(self, profile: str, data_record: dict | None,
                                   *, mutate_tail: bool = False) -> None:
        profile_record = self._profile_spec_record(profile)
        if profile == data.R12_PROFILE:
            context_data_record = data_record or self.data_spec_record
        else:
            context_data_record = None
        suffix = uuid.uuid4().hex
        descriptor = self.h.publish(self.root / (profile.replace("/", "_") + "-descriptor-" + suffix + ".json"),
                                    {"schema": "synthetic-external-descriptor"})
        lifecycle_root = self.root / (profile.replace("/", "_") + "-lifecycle-" + suffix)
        lifecycle_root.mkdir(mode=0o700)
        with lifecycle.r9_execution_context(profile_record, context_data_record):
            rows = lifecycle.lifecycle.inventory(
                lifecycle_root, "/usr/bin/python3", descriptor,
                {"members": []}, {"members": []})
            consumer_path = Path(lifecycle.transition_lifecycle.__file__).resolve(strict=True)
            spec = __import__("importlib.util", fromlist=["spec_from_file_location"]).spec_from_file_location(
                "release_transition_lifecycle", consumer_path)
            module = __import__("importlib.util", fromlist=["module_from_spec"]).module_from_spec(spec)
            spec.loader.exec_module(module)
            phase = "old-read"
            route = next(row for row in rows if row["label"] == "data-" + phase)
            instance = object.__new__(module.Lifecycle)
            instance.external_descriptor = descriptor["path"]
            instance.external_descriptor_sha = descriptor["sha256"]
            expected_count = 12 if context_data_record is not None else 8
            expected = route["request_argv"][-expected_count:]
            if mutate_tail:
                route["request_argv"][-1] = "0" * 64
                with self.assertRaisesRegex(RuntimeError, "actual sealed consumer"):
                    instance.external_arguments(phase)
            else:
                self.assertEqual(instance.external_arguments(phase), expected)
                self.assertEqual(len(expected), expected_count)

    def test_real_wrapped_external_arguments_r12_and_legacy_r11(self):
        self._exercise_wrapped_consumer(data.R12_PROFILE, self.data_spec_record)
        self._exercise_wrapped_consumer(data.R12_PROFILE, self.data_spec_record, mutate_tail=True)
        self._exercise_wrapped_consumer(data.R11_PROFILE, None)
        self._exercise_wrapped_consumer(data.R11_PROFILE, None, mutate_tail=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
