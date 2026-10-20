"""R20 regression for the R13 seal record shape crossing the product boundary."""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

CANDIDATE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE / "source"))

from tools import w26_application_lifecycle_current as lifecycle  # noqa: E402
from tools import w26_sealed_data_current as data  # noqa: E402
from tools.w26_prepare_current_inputs import _helpers  # noqa: E402

INPUT_SPEC = (CANDIDATE.parent / "w26-r17-current175-r12-runtime-input-bridge" / "inputs"
              / "w26_r13_explicit_data_input_spec_R16.json")
INPUT_SPEC_SHA256 = "6d0cfbab06da20ac79fa8f7dbe4d8226e868b7307bc376fe145641c06a2f2efe"


class R20LifecycleSealRecordShapeTests(unittest.TestCase):
    def test_actual_t044_selector_maps_to_product_helpers_exact_record(self):
        selector = data.r13_data_profile_selector(INPUT_SPEC, INPUT_SPEC_SHA256)
        baseline = data.verify_r13_data_baseline(selector)
        selected = selector["seal_record"]
        actual_product_record = _helpers().raw_file(Path(selected["path"]))[1]

        self.assertEqual(baseline["status"], "PASS_T044_R15_PERSISTENT_CONSUMER_BASELINE_NOT_FULLRESTORE")
        self.assertEqual(lifecycle._product_seal_first_record(selected), actual_product_record)
        self.assertEqual(actual_product_record["sha256"], data.R13_DATA_SEAL_SHA256)
        self.assertFalse(baseline["raw_original_store_opened"])
        self.assertEqual(baseline["fullrestore"], "NOT_RUN")

    def test_product_record_retains_exact_stable_identity_fields(self):
        selector = data.r13_data_profile_selector(INPUT_SPEC, INPUT_SPEC_SHA256)
        actual = _helpers().raw_file(Path(selector["seal_record"]["path"]))[1]
        for field in ("dev", "inode", "uid", "nlink", "bytes"):
            changed = copy.deepcopy(selector["seal_record"])
            changed[field] += 1
            self.assertNotEqual(lifecycle._product_seal_first_record(changed), actual, field)
        for field, change in (("sha256", "0" * 64), ("mode", 0o600),
                              ("path", "/wrong/seal.json")):
            changed = copy.deepcopy(selector["seal_record"])
            changed[field] = change if field != "mode" else (changed[field] & ~0o777) | change
            self.assertNotEqual(lifecycle._product_seal_first_record(changed), actual, field)

    def test_product_record_refuses_an_unbound_observation_shape(self):
        selector = data.r13_data_profile_selector(INPUT_SPEC, INPUT_SPEC_SHA256)
        changed = dict(selector["seal_record"])
        changed.pop("ctime_ns")
        with self.assertRaisesRegex(RuntimeError, "unexpected first-record shape"):
            lifecycle._product_seal_first_record(changed)


if __name__ == "__main__":
    unittest.main()
