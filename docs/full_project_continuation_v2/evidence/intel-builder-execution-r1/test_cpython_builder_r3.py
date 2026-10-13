"""Production builder boundary tests; no CPython configure/build/install."""
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import cpython_universal2_stages as b

class BuilderBoundary(unittest.TestCase):
    def macho(self, arch="arm64", minimum=0x000B0000, target_platform=1):
        cpu = {"arm64": 0x0100000C, "x86_64": 0x01000007}[arch]
        return struct.pack("<IiiIIIII", 0xFEEDFACF, cpu, 0, 2, 1, 24, 0, 0) + struct.pack("<IIIIII", 0x32, 24, target_platform, minimum, 0x001B0000, 0)

    def test_parser_accepts_two_declared_thin_slices(self):
        for arch in ("arm64", "x86_64"):
            d = b.parse_macho_probe(self.macho(arch), arch)
            self.assertEqual(d["minimum_os"], [11, 0, 0]);self.assertFalse(d["executed"])

    def test_parser_refuses_arch_floor_platform_fat_and_truncation(self):
        cases = [(self.macho("arm64"), "x86_64"), (self.macho(minimum=0x000C0000), "arm64"),
                 (self.macho(target_platform=2), "arm64"), (b"\xca\xfe\xba\xbe" + bytes(60), "arm64"),
                 (self.macho()[:-1], "arm64")]
        for raw, arch in cases:
            with self.subTest(arch=arch), self.assertRaises(b.Refusal): b.parse_macho_probe(raw, arch)

    def test_environment_inherits_home_without_repurposing(self):
        before = os.environ.get("HOME")
        env = b.task_environment(Path("/private/tmp/owned-fixture"))
        self.assertEqual(env.get("HOME"), before);self.assertEqual(os.environ.get("HOME"), before)
        self.assertEqual(env["CONFIG_SITE"], "/dev/null")
        self.assertIn("COMSOL_TASK_BUILD_PREFIX", env)

    def command_case(self, code, *, expected=None, timeout=2, birth_reader=None):
        with tempfile.TemporaryDirectory(prefix="comsol-builder-process-fixture-") as temporary:
            p = Path(temporary).resolve();fd = os.open(p, os.O_RDONLY | os.O_DIRECTORY)
            argv = [sys.executable, "-B", "-c", code]
            try:
                if expected:
                    with self.assertRaises(expected) as caught: b.run(fd, "fixture", argv, p, b.task_environment(p), timeout=timeout, birth_reader=birth_reader)
                else: b.run(fd, "fixture", argv, p, b.task_environment(p), timeout=timeout, birth_reader=birth_reader)
                results = [json.loads(f.read_text()) for f in p.glob("*.result.json")]
                self.assertEqual(len(results), 1)
                result = results[0]
                spawns = [json.loads(f.read_text()) for f in p.glob("*.spawn.json")]
                self.assertEqual(len(spawns), 1);self.assertEqual(spawns[0]["pid"], result["pid"])
                self.assertEqual(spawns[0]["argv"], argv)
                if not result["owned_process_terminal"]:
                    # The test owns this exact unreaped child. Cleanup is fixture
                    # cleanup only and never changes the original UNKNOWN result.
                    self.assertIsNotNone(caught.exception.process)
                    caught.exception.process.wait(timeout=2)
                return result, [json.loads(f.read_text()) for f in p.glob("*.birth.json")]
            finally: os.close(fd)

    def test_actual_owned_child_terminal_success_has_birth_and_logs(self):
        result, births = self.command_case("pass")
        self.assertEqual(result["status"], "PASS");self.assertEqual(result["rc"], 0)
        self.assertTrue(result["owned_process_terminal"]);self.assertTrue(result["owned_group_absent"])
        self.assertEqual(births[0]["status"], "OBSERVED");self.assertEqual(births[0]["uid"], os.getuid())

    def test_fast_owned_inspection_command_group_is_observed_before_wait(self):
        with tempfile.TemporaryDirectory() as temporary:
            p = Path(temporary).resolve();fd = os.open(p, os.O_RDONLY | os.O_DIRECTORY)
            try:
                result = b.run(fd, "fast-inspection-fixture", ["/usr/bin/true"], p, b.task_environment(p))
                self.assertEqual(result["group"]["status"], "OBSERVED")
                self.assertEqual(result["group"]["actual_pgid"], result["pid"])
                self.assertEqual(result["group"]["actual_sid"], result["pid"])
                self.assertTrue(result["owned_group_absent"])
            finally: os.close(fd)

    def test_actual_nonzero_child_is_terminal_failure(self):
        result, births = self.command_case("import sys;sys.exit(3)", expected=b.TerminalCommandFailure)
        self.assertEqual(result["status"], "TERMINAL_FAIL");self.assertEqual(result["rc"], 3)
        self.assertTrue(result["owned_process_terminal"])

    def test_unknown_birth_preserves_spawn_and_denial_before_wait(self):
        def denied(process, argv):
            return {"status": "UNKNOWN", "raw": {"rc": 1, "stdout": "", "stderr": "synthetic operation not permitted"}}
        result, births = self.command_case("import time;time.sleep(0.1)", expected=b.StageUnknown, birth_reader=denied)
        self.assertEqual(result["status"], "UNKNOWN_OWNERSHIP_OR_INTERRUPTION")
        self.assertIsNone(result["rc"]);self.assertIn("operation not permitted", births[0]["raw"]["stderr"])

    def test_actual_wait_timeout_preserves_original_unknown(self):
        result, births = self.command_case("import time;time.sleep(0.2)", expected=b.StageUnknown, timeout=0.01)
        self.assertEqual(result["status"], "UNKNOWN_TIMEOUT_ACTIVE_PROCESS")
        self.assertIsNone(result["rc"]);self.assertFalse(result["owned_process_terminal"])

    def test_same_handle_observer_preserves_original_unknown_and_stage_fence(self):
        with tempfile.TemporaryDirectory() as temporary:
            p = Path(temporary).resolve();fd = os.open(p, os.O_RDONLY | os.O_DIRECTORY)
            argv = [sys.executable, "-B", "-c", "import time;time.sleep(0.15)"]
            try:
                with self.assertRaises(b.StageUnknown) as caught: b.run(fd, "observer-fixture", argv, p, b.task_environment(p), timeout=0.01)
                original = p / (caught.exception.token + ".result.json")
                before = original.read_bytes()
                observed = b.observe_unknown(fd, caught.exception, interval=0.02)
                self.assertEqual(original.read_bytes(), before)
                self.assertEqual(observed["rc"], 0);self.assertTrue(observed["owned_group_absent"])
                self.assertEqual(observed["stage_acceptance"], "NOT_RUN")
                self.assertTrue(observed["active_stage_guard_retained"])
            finally: os.close(fd)

    def test_direct_child_exit_is_not_accepted_while_real_owned_group_remains(self):
        with tempfile.TemporaryDirectory() as temporary:
            p = Path(temporary).resolve();fd = os.open(p, os.O_RDONLY | os.O_DIRECTORY)
            code = "import subprocess,sys,time;child=subprocess.Popen([sys.executable,'-B','-c','import time;time.sleep(0.2)']);print(child.pid,flush=True);time.sleep(0.04)"
            argv = [sys.executable, "-B", "-c", code]
            try:
                with self.assertRaises(b.StageUnknown) as caught: b.run(fd, "group-fixture", argv, p, b.task_environment(p))
                original = p / (caught.exception.token + ".result.json")
                before = original.read_bytes();result = json.loads(before)
                self.assertTrue(result["owned_process_terminal"]);self.assertEqual(result["rc"], 0)
                self.assertFalse(result["owned_group_absent"])
                observed = b.observe_unknown(fd, caught.exception, interval=0.02)
                self.assertTrue(observed["owned_group_absent"]);self.assertEqual(original.read_bytes(), before)
                self.assertEqual(observed["stage_acceptance"], "NOT_RUN")
            finally: os.close(fd)

    def test_probe_symlink_is_never_followed(self):
        with tempfile.TemporaryDirectory() as temporary:
            p = Path(temporary).resolve();target = p / "owned-empty-target";target.write_bytes(self.macho())
            link = p / "probe";link.symlink_to(target.name)
            with self.assertRaises(OSError): b.read_probe(link, "arm64")

    def test_stage_receipt_without_terminal_commands_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            p = Path(temporary).resolve();f = p / "headers.PASS.json"
            f.write_text(json.dumps({"stage": "headers", "status": "PASS", "manifest_sha256": b.MANIFEST_SHA, "commands": []}));f.chmod(0o600)
            with self.assertRaises(b.Refusal): b.require_stage(p, "headers")

    def test_actual_main_build_rejects_missing_headers_without_spawn(self):
        with tempfile.TemporaryDirectory(prefix="comsol-builder-main-fixture-") as temporary:
            root = Path(temporary).resolve();p = root / "comsol-intel-inputs-r1-fixture";p.mkdir(mode=0o700)
            source = p / "sources/cpython-source-3.12.13/Python-3.12.13";source.mkdir(parents=True, mode=0o700)
            build = p / "cpython-universal2-build";build.mkdir(mode=0o700);(p / "build-logs").mkdir(mode=0o700)
            owner = {"uid": os.getuid(), "security": {"identity": b.identity(p.stat())}, "manifest_sha256": b.MANIFEST_SHA}
            data = {"OWNER.json": owner, "ACQUISITION_COMPLETE.json": {"status": "ACQUIRED_EXACT", "count": 43, "bytes": 220243712, "manifest_sha256": b.MANIFEST_SHA},
                    "OFFLINE_SOURCES_PREPARED.json": {"status": "VERIFIED_UNPACKED_ONLY", "manifest_sha256": b.MANIFEST_SHA}}
            for name, obj in data.items():
                f = p / name;f.write_text(json.dumps(obj));f.chmod(0o600)
            checked = []
            def prior(stage_build, stage):
                checked.append(stage)
                if stage == "headers": raise FileNotFoundError("actual fixture has no header PASS receipt")
                return {"stage": "configure", "status": "synthetic fixture only"}
            with patch.object(b.sys, "argv", ["builder", "--prefix", str(p), "--stage", "build", "--execute-stage"]), \
                 patch.object(b, "require_stage", side_effect=prior), patch.object(b, "run") as run:
                with self.assertRaises(FileNotFoundError): b.main()
                run.assert_not_called()
            self.assertEqual(checked, ["configure", "headers"])
            self.assertFalse((build / "ACTIVE_STAGE.json").exists())

if __name__ == "__main__": unittest.main()
