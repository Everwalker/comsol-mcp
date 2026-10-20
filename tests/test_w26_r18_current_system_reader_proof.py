"""Focused R18 product-consumer controls; synthetic positives are software-only."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import stat
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

CANDIDATE = Path(__file__).resolve().parents[1]
SOURCE = CANDIDATE / "source"
sys.path.insert(0, str(SOURCE))

from tools import (  # noqa: E402
    w26_application_lifecycle_base_current as base,
    w26_application_lifecycle_current as current,
    w26_runtime_actor_current as actor,
)
from tools.w26_prepare_current_inputs import _helpers  # noqa: E402
proof = base


def _records():
    ps_bytes = b"synthetic ps bytes"
    codesign_bytes = b"synthetic codesign bytes"
    return {
        "/bin/ps": {"path": "/bin/ps", "bytes": len(ps_bytes),
                     "sha256": hashlib.sha256(ps_bytes).hexdigest(),
                     "identity": [101, 202, 0, stat.S_IFREG | stat.S_ISUID | 0o755, 1,
                                  len(ps_bytes), 303, 404]},
        "/usr/bin/codesign": {"path": "/usr/bin/codesign", "bytes": len(codesign_bytes),
                               "sha256": hashlib.sha256(codesign_bytes).hexdigest(),
                               "identity": [101, 203, 0, stat.S_IFREG | 0o755, 1,
                                            len(codesign_bytes), 303, 404]},
    }


def _outputs():
    return (
        {"argv": list(proof.DISPLAY_ARGV), "returncode": 0, "stdout": "",
         "stderr": "Identifier=com.apple.ps\nAuthority=macOS Software Signing\n"
                   "Authority=Apple Code Signing Certification Authority\nAuthority=Apple Root CA\n"},
        {"argv": list(proof.VERIFY_ARGV), "returncode": 0, "stdout": "",
         "stderr": "/bin/ps: valid on disk\n/bin/ps: satisfies its Designated Requirement\n"},
    )


def _proof_value(root, records=None, display=None, verify=None):
    records = records or _records()
    actual_display, actual_verify = _outputs()
    root_identity = _helpers().secure_dir(root)
    return {"schema": proof.SCHEMA, "profile": proof.PROFILE,
            "status": "PASS_CURRENT_CODESIGN_STRICT", "read_only": True, "network": "NOT_USED",
            "system_ps": records["/bin/ps"], "codesign_system": records["/usr/bin/codesign"],
            "display": actual_display if display is None else display,
            "verify": actual_verify if verify is None else verify, "failure_reasons": [],
            "producer_source": _helpers().raw_file(Path(proof.__file__).resolve(strict=True))[1],
            "lifecycle_root": str(root), "lifecycle_root_identity": root_identity}


class R18CurrentSystemReaderProofTests(unittest.TestCase):
    def setUp(self):
        self.roots = []

    def tearDown(self):
        for root in self.roots:
            shutil.rmtree(root)

    def fresh_root(self):
        for _ in range(20):
            root = Path("/private/tmp") / ("cvw26-lifecycle-r15-" + uuid.uuid4().hex[:8])
            try:
                root.mkdir(mode=0o700)
                break
            except FileExistsError:
                continue
        else:
            raise RuntimeError("unable to allocate fresh synthetic lifecycle root")
        identity = _helpers().secure_dir(root)
        _helpers().publish(root / "FIRST_RECORD.json", {"root": str(root), "root_identity": identity})
        self.roots.append(root)
        return root

    def patch_base(self, module, prepared, profile):
        self.enterContext(patch.object(module.deps, "verify_inputs", lambda *_args: None))
        self.enterContext(patch.object(module.app, "verify_prior_profiles", lambda *_args: None))
        self.enterContext(patch.object(module.data, "verify_seal", lambda *_args, **_kwargs: {"synthetic": True}))
        self.enterContext(patch.object(module.data, "current_application_profile", lambda _profile: {
            "result": prepared["app_result"], "wheel": prepared["app_wheel"]}))
        self.enterContext(patch.object(module.release, "wheel_metadata", lambda path: {
            "version": "0.1.9" if Path(path).name.endswith("0.1.9-py3-none-any.whl") else "0.1.10"}))
        self.enterContext(patch.object(module.data, "descriptor", lambda _path, _sha: (
            {"current_application_profile": profile}, {"synthetic": True})))

    def base_prepared(self, root, binding):
        helper = _helpers()
        (root / "old-wheelhouse").mkdir()
        (root / "new-wheelhouse").mkdir()
        repository = Path(__file__).resolve().parents[3]
        app_root = repository / "_state" / "w26-r17-main-actual-app2-0d04cc1a-20261019" / "TERMINAL_RAW_R1"
        app_result = app_root / "APPLICATION_BUNDLE_RESULT.json"
        app_wheel = app_root / "comsol_mcp-0.1.10-py3-none-any.whl"
        old_wheel = repository / "evidence" / "w22_vcsel" / "delivery" / "comsol_mcp-0.1.9-py3-none-any.whl"
        profile = {"schema": "synthetic-current-profile-for-reader-test"}
        local = {}
        for name, raw in (("python", b"synthetic python"), ("descriptor", b"synthetic descriptor"),
                          ("requirements-old", b"synthetic old lock"),
                          ("requirements-new", b"synthetic new lock")):
            path = root / (name + ".bin")
            helper.copy_blob(path, raw)
            local[name] = helper.raw_file(path)[1]
        prepared = {"system_reader_profile": proof.PROFILE,
                    "current_system_reader_proof": binding,
                    "manifest": {}, "runtime": {}, "profiles": {},
                    "seal": {"path": base.SEAL, "sha256": base.SEAL_SHA},
                    "app_result": helper.raw_file(app_result)[1],
                    "app_wheel": helper.raw_file(app_wheel)[1],
                    "old_wheel": helper.raw_file(old_wheel)[1],
                    "python": local["python"], "descriptor": local["descriptor"],
                    "requirements_old": local["requirements-old"],
                    "requirements_new": local["requirements-new"],
                    "wheelhouses": {"old": {}, "new": {}},
                    "current_application_profile": profile}
        return prepared, profile

    def write_binding(self, root, payload, live_records, *, use_producer=False):
        self.enterContext(patch.object(
            actor, "system_record", side_effect=lambda path: copy.deepcopy(live_records[str(path)])))
        proof_path = root / proof.PROOF_FILENAME
        proof_record = _helpers().publish(proof_path, payload)
        if use_producer:
            return proof.bind_current_system_reader_proof(root, proof_path, proof_record["sha256"])
        first = json.loads(_helpers().raw_file(root / "FIRST_RECORD.json")[0])
        return {"schema": proof.BINDING_SCHEMA, "profile": proof.PROFILE,
                "root": str(root), "root_identity": first["root_identity"],
                "expected_sha256": proof_record["sha256"], "first_record": proof_record}

    def test_product_verify_inputs_consumes_explicit_proof(self):
        root = self.fresh_root()
        records = _records()
        binding = self.write_binding(root, _proof_value(root, records), records, use_producer=True)
        prepared, profile = self.base_prepared(root, binding)
        self.patch_base(base, prepared, profile)
        self.assertEqual(base.verify_inputs(root, prepared), {"synthetic": True})

    def test_product_verify_inputs_rejects_device_hash_authority_argv_and_rc_drift(self):
        mutations = ("wrong-device", "wrong-ps-hash", "wrong-codesign-hash", "wrong-authority",
                     "wrong-display-argv", "wrong-verify-argv", "wrong-verify-return",
                     "wrong-verify-output")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                root = self.fresh_root()
                payload_records, live_records = _records(), _records()
                display, verify = _outputs()
                if mutation == "wrong-device":
                    payload_records["/bin/ps"]["identity"][0] += 1
                elif mutation == "wrong-ps-hash":
                    payload_records["/bin/ps"]["sha256"] = "0" * 64
                elif mutation == "wrong-codesign-hash":
                    payload_records["/usr/bin/codesign"]["sha256"] = "0" * 64
                elif mutation == "wrong-authority":
                    display["stderr"] = "Identifier=com.apple.ps\nAuthority=(unavailable)\n"
                elif mutation == "wrong-display-argv":
                    display["argv"] = ["/usr/bin/codesign", "-d", "/bin/ps"]
                elif mutation == "wrong-verify-argv":
                    verify["argv"] = ["/usr/bin/codesign", "--verify", "/bin/ps"]
                elif mutation == "wrong-verify-return":
                    verify["returncode"] = 1
                elif mutation == "wrong-verify-output":
                    verify["stderr"] = "/bin/ps: CSSMERR_TP_NOT_TRUSTED\n"
                binding = self.write_binding(
                    root, _proof_value(root, payload_records, display, verify), live_records)
                prepared, profile = self.base_prepared(root, binding)
                self.patch_base(base, prepared, profile)
                with self.assertRaisesRegex(RuntimeError, "R18 current system-reader proof"):
                    base.verify_inputs(root, prepared)

    def test_product_verify_inputs_rejects_missing_proof_bad_hash_and_changed_bytes(self):
        root = self.fresh_root()
        records = _records()
        binding = self.write_binding(root, _proof_value(root, records), records, use_producer=True)
        prepared, profile = self.base_prepared(root, binding)
        self.patch_base(base, prepared, profile)
        missing = dict(prepared)
        missing.pop("current_system_reader_proof")
        with self.assertRaisesRegex(RuntimeError, "explicit R18 proof binding"):
            base.verify_inputs(root, missing)
        wrong_hash = dict(prepared)
        wrong_hash["current_system_reader_proof"] = dict(binding, expected_sha256="0" * 64)
        with self.assertRaisesRegex(RuntimeError, "first-record path/digest"):
            base.verify_inputs(root, wrong_hash)
        Path(binding["first_record"]["path"]).write_bytes(b"changed after first record")
        with self.assertRaises(RuntimeError):
            base.verify_inputs(root, prepared)

    def test_product_verify_inputs_rejects_cross_root_binding(self):
        root, other_root = self.fresh_root(), self.fresh_root()
        records = _records()
        binding = self.write_binding(root, _proof_value(root, records), records, use_producer=True)
        prepared, profile = self.base_prepared(root, binding)
        self.patch_base(base, prepared, profile)
        with self.assertRaisesRegex(RuntimeError, "lifecycle root identity differs"):
            base.verify_prepared_system_reader(prepared, other_root)

    def test_r12_wrapper_requires_proof_and_r11_default_remains_legacy(self):
        root = self.fresh_root()
        profile = {"schema": "w26-current-reader-profile-spec/1", "source_profile": "current175-r12"}
        record = _helpers().publish(root / "profile.json", profile)
        with self.assertRaisesRegex(RuntimeError, "explicit R18 system-reader proof"):
            current.verify_inputs(root, {"current_profile_spec": record, "data_input_spec": None})
        expected = {"legacy": "unchanged"}
        with patch.object(base, "verify_system_reader_signature", lambda _record: expected):
            self.assertIs(base.verify_prepared_system_reader({"system_reader_signature": {"sha256": "old"}}),
                          expected)
        with self.assertRaisesRegex(RuntimeError, "R11 fixed reader rejects"):
            base.verify_prepared_system_reader({"system_reader_signature": {},
                                                "current_system_reader_proof": {}})

    def test_r18_current_reader_exact_lane_argv_and_cap(self):
        row = {"label": "uninstall-process-guard", "lane": "SYSTEM_READER", "cap": 15,
               "max_calls": 2, "request_argv": ["ps", "-axo", "pid=,ppid=,command="]}
        base.system_reader_lane(row, list(proof.SYSTEM_READER_ARGV))
        for bad_row, bad_argv in ((dict(row, cap=16), proof.SYSTEM_READER_ARGV),
                                  (dict(row, max_calls=3), proof.SYSTEM_READER_ARGV),
                                  (row, ["/bin/ps", "-ef"])):
            with self.assertRaisesRegex(RuntimeError, "exact readonly system reader lane"):
                base.system_reader_lane(bad_row, bad_argv)

    def test_r18_producer_synthetic_pass_and_live_fail_closed_diagnostic(self):
        records = _records()
        with patch.object(actor, "system_record", side_effect=lambda path: copy.deepcopy(records[str(path)])), \
                patch.object(proof.platform, "system", return_value="Darwin"), \
                patch.object(proof.platform, "machine", return_value="arm64"):
            display, verify = _outputs()
            calls = []

            def successful_run(argv):
                calls.append(list(argv))
                return display if argv == proof.DISPLAY_ARGV else verify

            with patch.object(proof, "_r18_codesign_run", side_effect=successful_run):
                root = self.fresh_root()
                result = proof.capture_current_system_reader_proof(root)
                self.assertEqual(result["status"], "PASS_CURRENT_CODESIGN_STRICT")
                self.assertEqual(calls, [proof.DISPLAY_ARGV, proof.VERIFY_ARGV])
                binding = proof.bind_current_system_reader_proof(
                    root, root / proof.PROOF_FILENAME, result["proof"]["sha256"])
                proof.verify_current_system_reader_binding(binding, expected_root=root)

            other_root = self.fresh_root()
            bad_display = dict(display, stderr="Identifier=com.apple.ps\nAuthority=(unavailable)\n")
            with patch.object(proof, "_r18_codesign_run", side_effect=lambda argv: bad_display if argv == proof.DISPLAY_ARGV else
                              dict(verify, returncode=1, stderr="/bin/ps: CSSMERR_TP_NOT_TRUSTED\n")):
                refused = proof.capture_current_system_reader_proof(other_root)
            self.assertEqual(refused["status"], "NOT_READY_CURRENT_CODESIGN_STRICT")
            self.assertEqual(refused["proof"], "NOT_CREATED")
            self.assertFalse((other_root / proof.PROOF_FILENAME).exists())
            self.assertTrue(_helpers().raw_file(other_root / proof.DIAGNOSTIC_FILENAME)[0])

    def test_normal_repository_tools_layout_product_consumer_uses_explicit_root(self):
        temp = tempfile.TemporaryDirectory(prefix="w26-r18-normal-tools-", dir="/private/tmp")
        self.addCleanup(temp.cleanup)
        tools_dir = Path(temp.name) / "repository" / "tools"
        tools_dir.mkdir(parents=True, mode=0o700)
        helper_path = tools_dir / "w26_application_lifecycle_base_current.py"
        shutil.copyfile(Path(base.__file__), helper_path)
        spec = importlib.util.spec_from_file_location("tools._w26_base_normal_repository_layout", helper_path)
        self.assertIsNotNone(spec)
        layout_base = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(layout_base)
        self.assertEqual(helper_path.parent.name, "tools")
        self.assertEqual(helper_path.parent.parent.name, "repository")

        root = self.fresh_root()
        records = _records()
        display, verify = _outputs()
        with patch.object(actor, "system_record", side_effect=lambda path: copy.deepcopy(records[str(path)])), \
                patch.object(layout_base.platform, "system", return_value="Darwin"), \
                patch.object(layout_base.platform, "machine", return_value="arm64"), \
                patch.object(layout_base, "_r18_codesign_run", side_effect=lambda argv: display if argv == layout_base.R18_DISPLAY_ARGV else verify):
            result = layout_base.capture_current_system_reader_proof(root)
            binding = layout_base.bind_current_system_reader_proof(
                root, root / layout_base.PROOF_FILENAME, result["proof"]["sha256"])
            prepared, profile = self.base_prepared(root, binding)
            self.patch_base(layout_base, prepared, profile)
            self.assertEqual(layout_base.verify_inputs(root, prepared), {"synthetic": True})
        self.assertEqual(result["proof"]["path"], str(root / proof.PROOF_FILENAME))
        self.assertNotEqual(result["proof"]["path"], str(helper_path.parent / proof.PROOF_FILENAME))


if __name__ == "__main__":
    unittest.main()
