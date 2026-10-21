"""R21 actual-file controls for the R13 and existing lifecycle seal readers."""
from __future__ import annotations

import copy
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
sys.path.insert(0, str(CANDIDATE / "source"))

from tools import w26_application_lifecycle_current as lifecycle  # noqa: E402
from tools import w26_sealed_data_current as data  # noqa: E402
from tools.w26_prepare_current_inputs import _helpers  # noqa: E402

INPUT_SPEC = (CANDIDATE.parent / "w26-r17-current175-r12-runtime-input-bridge" / "inputs"
              / "w26_r13_explicit_data_input_spec_R16.json")
INPUT_SPEC_SHA256 = "6d0cfbab06da20ac79fa8f7dbe4d8226e868b7307bc376fe145641c06a2f2efe"
FULL_READER_DRIFT_FIELDS = ("mtime_ns", "ctime_ns", "dev", "inode", "sha256", "path",
                            "uid", "nlink", "mode", "bytes")
PRODUCT_READER_DRIFT_FIELDS = ("dev", "inode", "sha256", "path", "uid", "nlink", "mode", "bytes")


class R21ActualSealCrossReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.selector = data.r13_data_profile_selector(INPUT_SPEC, INPUT_SPEC_SHA256)
        cls.baseline = data.verify_r13_data_baseline(cls.selector)
        cls.seal_path = Path(cls.selector["seal_record"]["path"])
        cls.helper_record = _helpers().raw_file(cls.seal_path)[1]

    def test_same_actual_seal_is_read_by_both_strict_and_product_readers(self):
        raw, full_record = data.raw_file(
            self.seal_path, expected=self.selector["seal_record"], maximum=4194304)
        product_raw, product_record = _helpers().raw_file(
            self.seal_path, expected=lifecycle._product_seal_first_record(full_record), maximum=4194304)

        self.assertEqual(raw, product_raw)
        self.assertEqual(full_record, self.selector["seal_record"])
        self.assertEqual(product_record, self.helper_record)
        self.assertEqual(self.baseline["status"],
                         "PASS_T044_R15_PERSISTENT_CONSUMER_BASELINE_NOT_FULLRESTORE")
        self.assertFalse(self.baseline["raw_original_store_opened"])
        self.assertEqual(self.baseline["fullrestore"], "NOT_RUN")
        checked = lifecycle._verify_product_seal_records(self.selector, self.helper_record)
        self.assertEqual(checked["selector_reader_record"], full_record)
        self.assertEqual(checked["product_helper_record"], product_record)

    def test_four_phase_product_rows_can_be_checked_twice_without_double_routing(self):
        with tempfile.TemporaryDirectory(prefix="w26-r21-route-control-") as temporary:
            folder = Path(temporary)
            profile_path = folder / "CURRENT_PROFILE_SPEC.json"
            profile_path.write_text(json.dumps({"control": "source-only route fixture"}) + "\n")
            profile_record = _helpers().raw_file(profile_path)[1]
            descriptor = {"path": str(folder / "EXTERNAL_DATA.json"), "sha256": "a" * 64}
            helper = str(Path(lifecycle.SEALED_R9_HELPER).resolve(strict=True))
            rows = []
            for phase in lifecycle.R9_SEALED_PHASES:
                argv = ["/usr/bin/sandbox-exec", "-f", str(folder / "profile.sb"),
                        str(folder / "python"), "-I", "-B", "-c",
                        lifecycle.CURRENT_SEALED_PHASE_SCRIPT, helper,
                        descriptor["path"], descriptor["sha256"], phase]
                rows.append({"label": "data-" + phase, "request_argv": argv})

            initially_routed = lifecycle._route_sealed_phase_rows(
                rows, helper, profile_record, data_input_spec_record=self.selector["input_spec_record"])
            repeated_route = lifecycle._route_sealed_phase_rows(
                initially_routed, helper, profile_record,
                data_input_spec_record=self.selector["input_spec_record"])
            preflight_rows = lifecycle._route_sealed_phase_rows(
                repeated_route, helper, profile_record,
                data_input_spec_record=self.selector["input_spec_record"], input_preflight_only=True)

            self.assertEqual(repeated_route, initially_routed)
            self.assertEqual({row["label"] for row in preflight_rows},
                             {"data-" + phase for phase in lifecycle.R9_SEALED_PHASES})
            for row, original in zip(preflight_rows, initially_routed):
                self.assertEqual(row["request_argv"][:-5], original["request_argv"])
                self.assertEqual(row["request_argv"][-5:], [
                    "--preflight-packaging-wheel",
                    self.selector["input_spec"]["preflight_packaging_wheel"]["path"],
                    "--preflight-packaging-wheel-sha",
                    self.selector["input_spec"]["preflight_packaging_wheel"]["sha256"],
                    "--input-preflight-only",
                ])
    def test_full_r13_selector_record_rejects_each_actual_seal_field_drift(self):
        for field in FULL_READER_DRIFT_FIELDS:
            with self.subTest(field=field):
                changed_selector = copy.deepcopy(self.selector)
                record = changed_selector["seal_record"]
                if field == "path":
                    record[field] = str(self.seal_path.with_name("wrong-seal.json"))
                elif field == "sha256":
                    record[field] = "0" * 64
                else:
                    record[field] += 1
                with self.assertRaises((RuntimeError, OSError)):
                    lifecycle._verify_product_seal_records(changed_selector, self.helper_record)
                with self.assertRaises((RuntimeError, OSError)):
                    data.raw_file(self.seal_path, expected=record, maximum=4194304)

    def test_actual_product_comparison_rejects_each_helper_record_field_drift(self):
        for field in PRODUCT_READER_DRIFT_FIELDS:
            with self.subTest(field=field):
                changed_product_record = copy.deepcopy(self.helper_record)
                if field == "path":
                    changed_product_record[field] = str(self.seal_path.with_name("wrong-seal.json"))
                elif field == "sha256":
                    changed_product_record[field] = "0" * 64
                elif field == "mode":
                    changed_product_record[field] ^= 0o100
                else:
                    changed_product_record[field] += 1
                with self.assertRaisesRegex(RuntimeError, "product-prepared seal record differs"):
                    lifecycle._verify_product_seal_records(self.selector, changed_product_record)

    def test_actual_r13_descriptor_consumer_rejects_timestamp_and_fullmode_drift(self):
        root = Path("/private/tmp") / ("cvw26-lifecycle-r15-" + uuid.uuid4().hex[:8])
        root.mkdir(mode=0o700)
        try:
            helper = _helpers()
            root_stat = root.lstat()
            root_identity = {"device": root_stat.st_dev, "inode": root_stat.st_ino}
            helper.secure_dir(root)
            helper.publish(root / "FIRST_RECORD.json", {
                "root": str(root), "root_identity": root_identity,
            })
            profile = {"source_profile": data.R12_PROFILE}
            profile_spec = {"source_profile": data.R12_PROFILE}
            descriptor = {
                "schema": "w26-sealed-r6-lifecycle-descriptor/1",
                "unit_root": str(root),
                "root_identity": root_identity,
                "seal": self.selector["seal_record"]["path"],
                "seal_sha256": self.selector["seal_record"]["sha256"],
                "synthetic_data": False,
                "old_historical_dependencies": "UNVERIFIED_CURRENT43_NEW_TEST_BASELINE",
                "current_application_profile": profile,
                "current_profile_spec": profile_spec,
                "data_profile": data.R13_DATA_PROFILE,
                "data_selector": self.selector,
                "data_input_spec": self.selector["input_spec_record"],
            }
            # Only the unrelated App-profile producer gate is isolated here. The
            # real descriptor consumer, exact full selector reader, seal bytes,
            # and T044 baseline verifier remain active for both positive and
            # negative controls.
            with patch.object(data, "_verify_profile_spec_envelope"), \
                 patch.object(data, "current_application_profile", return_value={"verified": True}):
                paths = data.data_paths(descriptor, data_selector=self.selector)
                self.assertEqual(paths["home"], root / "transition-work" / "sealed-control-home")
                self.assertFalse(paths["home"].exists())
                for field in ("mtime_ns", "ctime_ns", "mode"):
                    with self.subTest(field=field):
                        changed_selector = copy.deepcopy(self.selector)
                        changed_selector["seal_record"][field] += 1
                        changed_descriptor = copy.deepcopy(descriptor)
                        changed_descriptor["data_selector"] = changed_selector
                        with self.assertRaisesRegex(RuntimeError,
                                                     "exact named persistent selector"):
                            data.data_paths(changed_descriptor, data_selector=changed_selector)
        finally:
            shutil.rmtree(root)

    def test_projection_uses_the_frozen_helper_S_IMODE_domain(self):
        with tempfile.TemporaryDirectory(prefix="w26-r22-helper-mode-") as temporary:
            path = Path(temporary) / "mode-control.json"
            path.write_bytes(b"mode-domain control")
            path.chmod(0o4750)
            _raw, helper_record = _helpers().raw_file(path)
            full_mode = path.lstat().st_mode
            full_record = {
                **helper_record,
                "mode": full_mode,
                "mtime_ns": path.lstat().st_mtime_ns,
                "ctime_ns": path.lstat().st_ctime_ns,
            }
            self.assertEqual(helper_record["mode"], stat.S_IMODE(full_mode))
            self.assertEqual(lifecycle._product_seal_first_record(full_record)["mode"],
                             helper_record["mode"])
            self.assertEqual(lifecycle._product_seal_first_record(full_record)["mode"],
                             stat.S_IMODE(full_mode))


if __name__ == "__main__":
    unittest.main()
