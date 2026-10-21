from __future__ import annotations

import argparse
import os
import shutil
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

CANDIDATE = Path(os.environ.get("W26_R26_SOURCE", str(Path(__file__).resolve().parents[1] / "source"))).resolve()
sys.path.insert(0, str(CANDIDATE))

from tools import w26_application_lifecycle_current as lifecycle  # noqa: E402
from tools import w26_sealed_data_current as data  # noqa: E402
from tools import w26_sealed_data as legacy_data  # noqa: E402
from tools.w26_prepare_current_inputs import _helpers  # noqa: E402


REPOSITORY = Path(__file__).resolve().parents[4]
DATA_INPUT_SPEC = (REPOSITORY / "repository/_state/w26-r17-current175-r12-runtime-input-bridge/"
                   "inputs/w26_r13_explicit_data_input_spec_R16.json")
DATA_INPUT_SPEC_SHA256 = "6d0cfbab06da20ac79fa8f7dbe4d8226e868b7307bc376fe145641c06a2f2efe"
OLD_WHEEL_SHA256 = "c1d82034e19e1f0fd897f3ec76911a3ccad229ed35f613148633bfb56f923ae6"
CURRENT_WHEEL_SHA256 = "870eb6355772f393e36d50947ecc95b213116de5cbaaf60dbf44e30e94ae545b"


class R24LifecycleEntryContextTests(unittest.TestCase):
    def setUp(self):
        self.roots: list[Path] = []

    def tearDown(self):
        for root in self.roots:
            shutil.rmtree(root, ignore_errors=True)

    def descriptor_fixture(self):
        helper = _helpers()
        root = Path("/private/tmp") / ("cvw26-lifecycle-r15-" + uuid.uuid4().hex[:8])
        root.mkdir(mode=0o700)
        self.roots.append(root)
        root_stat = root.lstat()
        identity = {"device": root_stat.st_dev, "inode": root_stat.st_ino}
        helper.secure_dir(root, identity)
        helper.publish(root / "FIRST_RECORD.json", {"root_identity": identity})
        profile_spec = {"schema": "w26-current-reader-profile-spec/1",
                        "source_profile": data.R12_PROFILE}
        profile_spec_record = helper.publish(root / "CURRENT_PROFILE_SPEC_test.json", profile_spec)
        input_raw, input_record = helper.raw_file(DATA_INPUT_SPEC)
        if input_record["sha256"] != DATA_INPUT_SPEC_SHA256:
            raise RuntimeError("R24 test data-input spec differs from its fixed source bytes")
        selector = data.r13_data_profile_selector(str(DATA_INPUT_SPEC), DATA_INPUT_SPEC_SHA256)
        profile = {"schema": "w26-current-installed-application/1",
                   "source_profile": data.R12_PROFILE}
        descriptor = {
            "schema": "w26-sealed-r6-lifecycle-descriptor/1",
            "unit_root": str(root),
            "root_identity": identity,
            "seal": selector["seal_record"]["path"],
            "seal_sha256": selector["seal_record"]["sha256"],
            "synthetic_data": False,
            "old_historical_dependencies": "UNVERIFIED_CURRENT43_NEW_TEST_BASELINE",
            "current_application_profile": profile,
            "current_profile_spec": profile_spec,
            "data_profile": data.R13_DATA_PROFILE,
            "data_selector": selector,
            "data_input_spec": selector["input_spec_record"],
        }
        descriptor_path = root / "EXTERNAL_DATA_r24test.json"
        descriptor_record = helper.publish(descriptor_path, descriptor)
        return (root, descriptor_path, descriptor_record, profile_spec,
                profile_spec_record, selector, descriptor)

    def test_base_descriptor_call_receives_bound_r12_profile_and_selector(self):
        (root, descriptor_path, descriptor_record, profile_spec, profile_spec_record,
         selector, value) = self.descriptor_fixture()
        old_reader = data.current_application_profile
        old_descriptor = data.descriptor
        profile = value["current_application_profile"]
        with patch.object(data, "_verify_profile_spec_envelope"), \
             patch.object(data, "verify_r13_data_baseline",
                          return_value={"consumer_closure": {"verified": True}}), \
             patch.object(lifecycle, "current_application_profile",
                          return_value={"profile": profile}):
            with lifecycle.r9_execution_context(profile_spec_record, selector["input_spec_record"]):
                # This is the exact two-positional-argument call made by the
                # existing base lifecycle's verify_inputs implementation.
                checked, closure = data.descriptor(descriptor_path, descriptor_record["sha256"])
                self.assertEqual(checked, value)
                self.assertEqual(closure, {"verified": True})
                with self.assertRaisesRegex(RuntimeError, "unbound profile spec"):
                    data.descriptor(descriptor_path, descriptor_record["sha256"],
                                    dict(profile_spec, altered=True))
                with self.assertRaisesRegex(RuntimeError, "unbound data selector"):
                    data.descriptor(descriptor_path, descriptor_record["sha256"],
                                    data_selector=dict(selector, altered=True))
        self.assertIs(data.current_application_profile, old_reader)
        self.assertIs(data.descriptor, old_descriptor)

    def test_entry_context_rejects_wrong_data_spec_before_base_call(self):
        (_, _, _, _, profile_spec_record, selector, _) = self.descriptor_fixture()
        wrong_record = dict(selector["input_spec_record"], sha256="0" * 64)
        with self.assertRaisesRegex(RuntimeError, "changed"):
            with lifecycle.r9_execution_context(profile_spec_record, wrong_record):
                self.fail("an incorrect R13 data-input record entered the base lifecycle")

    def test_legacy_r11_descriptor_keeps_its_explicit_profile_without_r13_selector(self):
        helper = _helpers()
        root = Path("/private/tmp") / ("cvw26-lifecycle-r15-" + uuid.uuid4().hex[:8])
        root.mkdir(mode=0o700)
        self.roots.append(root)
        profile_spec = {"schema": "w26-current-reader-profile-spec/1",
                        "source_profile": data.R11_PROFILE}
        profile_spec_record = helper.publish(root / "CURRENT_PROFILE_SPEC_r11.json", profile_spec)
        original = data.descriptor
        original_legacy = legacy_data.descriptor
        descriptor_path = root / "legacy-descriptor.json"
        with patch.object(data, "descriptor", return_value=({"legacy": True}, {"ok": True})) as reader:
            with lifecycle.r9_execution_context(profile_spec_record):
                self.assertIs(legacy_data.descriptor, original_legacy)
                result = data.descriptor(descriptor_path, "a" * 64)
                self.assertEqual(result, ({"legacy": True}, {"ok": True}))
        reader.assert_called_once_with(descriptor_path, "a" * 64, profile_spec, data_selector=None)
        self.assertIs(data.descriptor, original)
        self.assertIs(legacy_data.descriptor, original_legacy)

    def _transition_args(self, root, descriptor_path, descriptor_sha, wrong_descriptor_sha=False):
        work = root / "wrong-transition-work"
        old_lock = root / "old-requirements.lock"
        new_lock = root / "new-requirements.lock"
        old_lock.write_text(
            f"comsol-mcp==0.1.9 --hash=sha256:{OLD_WHEEL_SHA256}\n", encoding="utf-8")
        new_lock.write_text(
            f"comsol-mcp==0.1.10 --hash=sha256:{CURRENT_WHEEL_SHA256}\n", encoding="utf-8")
        return argparse.Namespace(
            python=Path(sys.executable), work_root=work,
            old_requirements=old_lock, new_requirements=new_lock,
            old_wheelhouse=root / "old-wheelhouse", new_wheelhouse=root / "new-wheelhouse",
            old_wheel_sha256=OLD_WHEEL_SHA256, new_wheel_sha256=CURRENT_WHEEL_SHA256,
            with_data_lifecycle=True, external_sealed_descriptor=descriptor_path,
            external_sealed_descriptor_sha=("0" * 64 if wrong_descriptor_sha else descriptor_sha),
            synthetic_data=False)

    def test_actual_transition_check_constructor_uses_r12_current_descriptor(self):
        (root, descriptor_path, descriptor_record, profile_spec, profile_spec_record,
         selector, value) = self.descriptor_fixture()
        original_legacy = legacy_data.descriptor
        original_current = data.descriptor
        calls = []

        def observe_current_descriptor(path, sha, *args, **kwargs):
            calls.append((path, sha, args, kwargs))
            return original_current(path, sha, *args, **kwargs)

        with patch.object(data, "_verify_profile_spec_envelope"), \
             patch.object(data, "verify_r13_data_baseline",
                          return_value={"consumer_closure": {"verified": True}}), \
             patch.object(lifecycle, "current_application_profile",
                          return_value={"profile": value["current_application_profile"]}), \
             patch.object(data, "descriptor", observe_current_descriptor):
            args = self._transition_args(root, descriptor_path, descriptor_record["sha256"])
            with lifecycle.r9_execution_context(profile_spec_record,
                                                selector["input_spec_record"]):
                lifecycle.lifecycle.inventory(root, str(sys.executable), descriptor_record,
                                              {"members": []}, {"members": []})
                self.assertIsNot(legacy_data.descriptor, original_legacy)
                with patch.object(lifecycle.lifecycle.release.sys, "platform", "darwin"), \
                     patch.object(lifecycle.lifecycle.release.shutil, "which",
                                  return_value="/usr/bin/sandbox-exec"):
                    result = lifecycle.lifecycle.release.transition_check(
                        args, Path(lifecycle.lifecycle.release.__file__))
                self.assertEqual(result["status"], "BLOCKED_LIFECYCLE_INPUTS")
                self.assertIn("sealed lifecycle work root differs",
                              result["data_lifecycle"]["reason"])
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0][0:2],
                                 (descriptor_path, descriptor_record["sha256"]))
                self.assertEqual(calls[0][2], (profile_spec,))
                self.assertEqual(calls[0][3], {"data_selector": selector})
        self.assertIs(data.descriptor, original_current)
        self.assertIs(legacy_data.descriptor, original_legacy)

    def test_actual_transition_check_constructor_rejects_wrong_r12_descriptor_hash(self):
        (root, descriptor_path, descriptor_record, _, profile_spec_record,
         selector, value) = self.descriptor_fixture()
        original_legacy = legacy_data.descriptor
        with patch.object(data, "_verify_profile_spec_envelope"), \
             patch.object(data, "verify_r13_data_baseline",
                          return_value={"consumer_closure": {"verified": True}}), \
             patch.object(lifecycle, "current_application_profile",
                          return_value={"profile": value["current_application_profile"]}):
            args = self._transition_args(root, descriptor_path,
                                         descriptor_record["sha256"],
                                         wrong_descriptor_sha=True)
            with lifecycle.r9_execution_context(profile_spec_record,
                                                selector["input_spec_record"]):
                lifecycle.lifecycle.inventory(root, str(sys.executable), descriptor_record,
                                              {"members": []}, {"members": []})
                with self.assertRaisesRegex(RuntimeError, "exact sealed descriptor changed"):
                    with patch.object(lifecycle.lifecycle.release.sys, "platform", "darwin"), \
                         patch.object(lifecycle.lifecycle.release.shutil, "which",
                                      return_value="/usr/bin/sandbox-exec"):
                        lifecycle.lifecycle.release.transition_check(
                            args, Path(lifecycle.lifecycle.release.__file__))
        self.assertIs(legacy_data.descriptor, original_legacy)


if __name__ == "__main__":
    unittest.main()
