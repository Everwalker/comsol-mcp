"""R19 controls for the actual R12 descriptor-to-reader wrapper call."""
from __future__ import annotations

import shutil
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

CANDIDATE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE / "source"))

from tools import w26_application_lifecycle_current as lifecycle  # noqa: E402
from tools import w26_sealed_data_current as data  # noqa: E402
from tools.w26_prepare_current_inputs import _helpers  # noqa: E402


class R19LifecycleReaderKeywordTests(unittest.TestCase):
    def setUp(self):
        self.roots: list[Path] = []

    def tearDown(self):
        for root in self.roots:
            shutil.rmtree(root, ignore_errors=True)

    def descriptor_fixture(self):
        root = Path("/private/tmp") / ("cvw26-lifecycle-r15-" + uuid.uuid4().hex[:8])
        root.mkdir(mode=0o700)
        self.roots.append(root)
        helper = _helpers()
        root_stat = root.lstat()
        identity = {"device": root_stat.st_dev, "inode": root_stat.st_ino}
        helper.secure_dir(root)
        helper.publish(root / "FIRST_RECORD.json", {"root": str(root), "root_identity": identity})
        profile_spec = {"schema": "test-profile-spec", "source_profile": data.R12_PROFILE}
        data_input_spec = {"path": str(root / "input-spec.json"), "sha256": "a" * 64}
        selector = {
            "seal_record": {"path": "/synthetic/seal.json", "sha256": "b" * 64},
            "input_spec_record": data_input_spec,
        }
        value = {
            "schema": "w26-sealed-r6-lifecycle-descriptor/1",
            "unit_root": str(root),
            "root_identity": identity,
            "seal": selector["seal_record"]["path"],
            "seal_sha256": selector["seal_record"]["sha256"],
            "synthetic_data": False,
            "old_historical_dependencies": "UNVERIFIED_CURRENT43_NEW_TEST_BASELINE",
            "current_application_profile": {"source_profile": data.R12_PROFILE},
            "current_profile_spec": profile_spec,
            "data_profile": data.R13_DATA_PROFILE,
            "data_selector": selector,
            "data_input_spec": data_input_spec,
        }
        descriptor = root / "descriptor.json"
        record = helper.publish(descriptor, value)
        return root, descriptor, record, profile_spec, selector, value

    def test_actual_descriptor_consumer_passes_bound_profile_spec_keyword(self):
        root, descriptor, record, profile_spec, selector, value = self.descriptor_fixture()
        observed = []

        def current_profile(profile, *, profile_spec):
            observed.append((profile, profile_spec))
            return {"verified": True}

        with patch.object(data, "_r13_selector", return_value=selector), \
             patch.object(data, "_verify_profile_spec_envelope"), \
             patch.object(data, "verify_r13_data_baseline", return_value={"consumer_closure": {"ok": True}}), \
             patch.object(lifecycle, "current_application_profile", side_effect=current_profile):
            checked, _ = lifecycle._with_current_reader(
                lambda: data.descriptor(descriptor, record["sha256"]),
                profile_spec=profile_spec, data_selector=selector)

        self.assertEqual(checked, value)
        self.assertEqual(observed, [(value["current_application_profile"], profile_spec)])

    def test_actual_descriptor_consumer_rejects_wrong_bound_profile_spec(self):
        root, descriptor, record, profile_spec, selector, _ = self.descriptor_fixture()
        wrong_spec = dict(profile_spec, source_profile="current175-r11")
        with patch.object(data, "_r13_selector", return_value=selector), \
             patch.object(data, "_verify_profile_spec_envelope"), \
             patch.object(data, "verify_r13_data_baseline", return_value={"consumer_closure": {"ok": True}}), \
             patch.object(lifecycle, "current_application_profile", return_value={"verified": True}):
            with self.assertRaisesRegex(RuntimeError, "R12 or R13 App producer"):
                lifecycle._with_current_reader(
                    lambda: data.descriptor(descriptor, record["sha256"]),
                    profile_spec=wrong_spec, data_selector=selector)

    def test_wrapper_rejects_unbound_profile_spec_argument(self):
        _, _, _, profile_spec, selector, _ = self.descriptor_fixture()
        profile = {"source_profile": data.R12_PROFILE}
        with patch.object(lifecycle, "current_application_profile", return_value={"verified": True}):
            with self.assertRaisesRegex(RuntimeError, "unbound profile spec"):
                lifecycle._with_current_reader(
                    lambda: data.current_application_profile(profile, profile_spec=dict(profile_spec, extra=True)),
                    profile_spec=profile_spec, data_selector=selector)


if __name__ == "__main__":
    unittest.main()
