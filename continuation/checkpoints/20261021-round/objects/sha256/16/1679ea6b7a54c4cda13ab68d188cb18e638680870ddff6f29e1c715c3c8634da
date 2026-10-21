from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT))

from tools import w26_application_bundle_current as bundle  # noqa: E402
from tools import w26_application_lifecycle_base_current as base_lifecycle  # noqa: E402
from tools import w26_application_lifecycle_current as lifecycle  # noqa: E402
from tools import w26_runtime_actor_current as actor  # noqa: E402
from tools import w26_sealed_data_current as reader  # noqa: E402
from tools.w26_prepare_current_inputs import _helpers  # noqa: E402


R13 = "current175-r13"
DATA_INPUT_SPEC = (Path(__file__).resolve().parents[5]
                   / "repository/_state/w26-r17-current175-r12-runtime-input-bridge/inputs/"
                   "w26_r13_explicit_data_input_spec_R16.json")
DATA_INPUT_SPEC_SHA256 = "6d0cfbab06da20ac79fa8f7dbe4d8226e868b7307bc376fe145641c06a2f2efe"
BACKUP_SOURCE_SHA256 = "a" * 64


class IntegratedCurrent175R13ProfileTests(unittest.TestCase):
    def _loader_sources(self):
        records = actor.source_fingerprints(actor.APP_BUNDLE)
        for name in ("common.py", "consumers.py", "CONTEXT.json", "MPh.ini"):
            row = bundle._helpers().raw_file(bundle.COMMON.parent / name)[1]
            records[row["path"]] = row
        bundle.add_current175_lock_sources(records, R13)
        return records

    def test_r13_lock_source_capture_and_existing_consumer_verify_real_175_bytes(self):
        with tempfile.TemporaryDirectory(prefix="cvw26-r29-r13-source-", dir="/private/tmp") as temp:
            capture = bundle.source_capture(SOURCE_ROOT, Path(temp) / "source-input", profile=R13)
            records = self._loader_sources()
            spec = reader.build_current_profile_spec(R13, capture["source_lock"], records)
            verified = bundle.verify_source_input(capture, profile_spec=spec)

        self.assertEqual(capture["source_profile"], R13)
        self.assertEqual(len(capture["snapshot_members"]), 175)
        self.assertEqual(spec["source_profile"], R13)
        self.assertEqual(spec["selected_lock_record"]["sha256"], reader.R13_PRODUCT_LOCK_SHA256)
        self.assertEqual(verified["source_snapshot_member_count"], 175)
        self.assertEqual(verified["origin_snapshot_member_count"], 175)

    def test_r13_profile_is_finite_and_keeps_r11_r12_lock_records_unchanged(self):
        profiles = reader.PROFILE_MAP["current_profiles"]
        self.assertEqual(set(profiles), {
            "current175-r9", "current175-r10", "current175-r11", "current175-r12", R13,
        })
        self.assertEqual(profiles["current175-r11"]["source_lock_sha256"], reader.R11_LOCK_SHA256)
        self.assertEqual(profiles["current175-r11"]["member_fingerprint_sha256"],
                         reader.R11_MEMBER_FINGERPRINT_SHA256)
        self.assertEqual(profiles["current175-r12"]["source_lock_sha256"], reader.R12_LOCK_SHA256)
        self.assertEqual(profiles["current175-r12"]["member_fingerprint_sha256"],
                         reader.R12_MEMBER_FINGERPRINT_SHA256)
        self.assertEqual(profiles[R13]["source_lock_sha256"], reader.R13_PRODUCT_LOCK_SHA256)
        self.assertEqual(profiles[R13]["member_fingerprint_sha256"],
                         reader.R13_PRODUCT_MEMBER_FINGERPRINT_SHA256)
        self.assertIn(reader.R13_PRODUCT_LOCK.name, reader.CURRENT_NAMES)
        self.assertEqual(set(reader.PROFILE_MAP["current_loader"]["member_names"]), reader.CURRENT_NAMES)

    def test_r13_rejects_r12_lock_wrong_profile_and_snapshot_tamper(self):
        records = self._loader_sources()
        r12_record = bundle._helpers().raw_file(bundle.CURRENT175_R12_SOURCE_LOCK)[1]
        with self.assertRaisesRegex(RuntimeError, "another source-lock path/hash"):
            reader.build_current_profile_spec(R13, r12_record, records)
        with self.assertRaisesRegex(RuntimeError, "source-profile selector is unknown"):
            reader.build_current_profile_spec("current175-r14", r12_record, records)

        with tempfile.TemporaryDirectory(prefix="cvw26-r29-r13-tamper-", dir="/private/tmp") as temp:
            capture = bundle.source_capture(SOURCE_ROOT, Path(temp) / "source-input", profile=R13)
            spec = reader.build_current_profile_spec(R13, capture["source_lock"], records)
            wrong_profile = {**capture, "source_profile": "current175-r12"}
            with self.assertRaisesRegex(RuntimeError, "explicit profile selector"):
                bundle.verify_source_input(wrong_profile, profile_spec=spec)
            member = Path(capture["snapshot_source"]) / "comsol_mcp" / "_java_worker.py"
            member.write_bytes(member.read_bytes() + b"\n# controlled R13 snapshot drift\n")
            with self.assertRaisesRegex(Exception, "fixed regular checkpoint changed"):
                bundle.verify_source_input(capture, profile_spec=spec)

    def test_r13_source_capture_does_not_alias_the_immutable_r12_product_lock(self):
        with tempfile.TemporaryDirectory(prefix="cvw26-r29-r12-alias-", dir="/private/tmp") as temp:
            with self.assertRaisesRegex(RuntimeError, "exact current source locked bytes differ"):
                bundle.source_capture(SOURCE_ROOT, Path(temp) / "source-input",
                                      profile="current175-r12")
            self.assertFalse((Path(temp) / "source-input").exists())
            self.assertFalse((Path(temp) / "source-origin").exists())

    def test_real_protection_pairs_routes_the_explicit_r13_profile_through_backup_producer(self):
        root = Path(tempfile.mkdtemp(prefix="cvw26-r29-r13-protection-", dir="/private/tmp"))
        helper = _helpers()
        try:
            records = self._loader_sources()
            lock_record = helper.raw_file(bundle.CURRENT175_R13_SOURCE_LOCK)[1]
            profile_spec = reader.build_current_profile_spec(R13, lock_record, records)
            profile_spec_record = helper.publish(root / "CURRENT_PROFILE_SPEC_r29r13.json", profile_spec)
            raw, input_record = helper.raw_file(DATA_INPUT_SPEC)
            if (input_record["sha256"] != DATA_INPUT_SPEC_SHA256
                    or json.loads(raw).get("schema") != "w26-r13-explicit-data-input-spec/1"):
                raise RuntimeError("R29 T044 input spec differs from its frozen source")
            selector = reader.r13_data_profile_selector(str(DATA_INPUT_SPEC), DATA_INPUT_SPEC_SHA256)
            profile = {"source_profile": R13}
            expected_profile_spec = profile_spec
            launch = SimpleNamespace(root=root, prepared={"current_application_profile": profile})
            evidence = root / "evidence"
            calls = []

            def selected_profile(selected, base_namespace, *, profile_spec=None):
                calls.append((selected, base_namespace, profile_spec))
                self.assertEqual(selected, profile)
                self.assertIs(base_namespace, reader.BASE)
                self.assertEqual(profile_spec, expected_profile_spec)
                return {"backup_source_sha256": BACKUP_SOURCE_SHA256}

            def protection_inputs(_launch, phase):
                receipt = {"operation": "full_backup", "success": True}
                archive = {
                    "schema": "w26-observed-original-fullbackup-archive/1",
                    "producer_source_sha256": BACKUP_SOURCE_SHA256,
                    "producer_receipt": {"test_only": "different receipt"},
                    "scratch": {"path": str(evidence / ".comsol-full-backup-a1b2c3d4")},
                }
                if phase == "data-candidate-backup":
                    return {"actual_archive_binding": archive, "backup": receipt}
                return {"restore": {"test_only": True}, "readback": {"test_only": True}}

            with (
                patch.object(lifecycle, "current_application_profile", side_effect=selected_profile),
                patch.object(base_lifecycle, "_original_protection_capture", side_effect=protection_inputs),
                patch.object(reader, "data_paths", return_value={"evidence": evidence}),
                patch.object(reader, "backup_producer_sha", wraps=reader.backup_producer_sha) as producer_call,
            ):
                with lifecycle.r9_execution_context(profile_spec_record, input_record):
                    with self.assertRaisesRegex(
                        RuntimeError, "original archive read-only protection proof differs"
                    ):
                        base_lifecycle._ProtectionPairs(launch)
                    producer_call.assert_called_once_with(launch.prepared)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][2], profile_spec)
            self.assertEqual(selector["input_spec_record"], input_record)
        finally:
            import shutil

            shutil.rmtree(root)


if __name__ == "__main__":
    unittest.main()
