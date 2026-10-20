"""Offline producer/reader controls for the additive current175-r11 profile."""
from __future__ import annotations

import inspect
import json
from pathlib import Path
import tempfile
import unittest

from tools import w26_application_bundle_current as bundle
from tools import w26_runtime_actor_current as actor
from tools import w26_sealed_data_current as reader


SOURCE_ROOT = Path(__file__).resolve().parents[4]


class Current175R11ProfileTests(unittest.TestCase):
    def _source_records(self):
        records = actor.source_fingerprints(actor.APP_BUNDLE)
        for name in ("common.py", "consumers.py", "CONTEXT.json", "MPh.ini"):
            row = bundle._helpers().raw_file(bundle.COMMON.parent / name)[1]
            records[row["path"]] = row
        bundle.add_current175_lock_sources(records, "current175-r11")
        return records

    def test_r11_lock_remains_frozen_with_finite_r12_r13_profiles_and_40_member_reader_closure(self):
        profile_map = reader.PROFILE_MAP
        self.assertEqual(set(profile_map["current_profiles"]), {
            "current175-r9", "current175-r10", "current175-r11", "current175-r12", "current175-r13"})
        self.assertEqual(len(reader.CURRENT_NAMES), 40)
        self.assertEqual(set(profile_map["current_loader"]["member_names"]), reader.CURRENT_NAMES)
        self.assertEqual(profile_map["current_profiles"]["current175-r11"]["source_lock_name"],
                         reader.R11_LOCK.name)
        self.assertEqual(profile_map["current_profiles"]["current175-r11"]["source_lock_sha256"],
                         reader.R11_LOCK_SHA256)
        self.assertEqual(profile_map["current_profiles"]["current175-r12"]["source_lock_sha256"],
                         "1f6adc82daa12c720509fbd58f86e381f834f4dd215bc5716302096f2b6c5a25")
        self.assertEqual(profile_map["current_profiles"]["current175-r13"]["source_lock_sha256"],
                         reader.R13_PRODUCT_LOCK_SHA256)
        self.assertEqual(len(bundle.CURRENT175_PROFILES), 10)
        self.assertEqual(inspect.signature(bundle.source_capture).parameters["profile"].default,
                         "app3-156")

    def test_real_r11_source_capture_binds_lock_map_and_reader(self):
        with tempfile.TemporaryDirectory(prefix="cvw26-r11-source-capture-") as temp:
            root = Path(temp)
            capture = bundle.source_capture(SOURCE_ROOT, root / "source-input",
                                            profile="current175-r11")
            records = self._source_records()
            spec = reader.build_current_profile_spec("current175-r11", capture["source_lock"], records)
            result = bundle.verify_source_input(capture, profile_spec=spec)
            envelope = reader._verify_profile_spec_envelope(
                {"loader_source_closure": spec["loader_source_closure"]}, spec)
            self.assertEqual(capture["source_profile"], "current175-r11")
            self.assertEqual(len(capture["snapshot_members"]), 175)
            self.assertEqual(len(spec["loader_source_closure"]["members"]), 40)
            self.assertEqual(envelope["source_profile"], "current175-r11")
            self.assertEqual(result["source_snapshot_member_count"], 175)
            self.assertEqual(result["origin_snapshot_member_count"], 175)
            live = bundle.verify_source_input(capture, require_live_origin=True, profile_spec=spec)
            self.assertEqual(live["source_snapshot_fingerprint_sha256"],
                             result["source_snapshot_fingerprint_sha256"])

    def test_r11_rejects_r9_r10_lock_mix_and_unknown_profile(self):
        records = self._source_records()
        r9 = bundle._helpers().raw_file(SOURCE_ROOT / "tools" /
                                        "w26_application_sources_175_r9_prepared.lock.json")[1]
        r10 = bundle._helpers().raw_file(SOURCE_ROOT / "tools" /
                                         "w26_application_sources_175_r10_prepared.lock.json")[1]
        with self.assertRaisesRegex(RuntimeError, "another source-lock path/hash"):
            reader.build_current_profile_spec("current175-r11", r9, records)
        with self.assertRaisesRegex(RuntimeError, "another source-lock path/hash"):
            reader.build_current_profile_spec("current175-r11", r10, records)
        with self.assertRaisesRegex(RuntimeError, "source-profile selector is unknown"):
            reader.build_current_profile_spec("current175-r14", r10, records)

    def test_profile_spec_selector_drift_and_snapshot_tamper_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="cvw26-r11-source-tamper-") as temp:
            root = Path(temp)
            capture = bundle.source_capture(SOURCE_ROOT, root / "source-input",
                                            profile="current175-r11")
            records = self._source_records()
            spec = reader.build_current_profile_spec("current175-r11", capture["source_lock"], records)
            wrong = {**capture, "source_profile": "current175-r10"}
            with self.assertRaisesRegex(RuntimeError, "explicit profile selector"):
                reader.verify_current_profile_spec_source(wrong, spec)
            member = Path(capture["snapshot_source"]) / "comsol_mcp" / "_state_backup.py"
            member.write_bytes(member.read_bytes() + b"\n# test-only drift\n")
            with self.assertRaisesRegex(Exception, "fixed regular checkpoint changed"):
                bundle.verify_source_input(capture, profile_spec=spec)


    def test_source_capture_r11_rejects_dangling_symlink_before_resolving_and_creating_target(self):
        with tempfile.TemporaryDirectory(prefix="cvw26-r11-dangling-link-") as temp:
            root = Path(temp)
            external_parent = root / "external"
            external_parent.mkdir()
            external_target = external_parent / "captured-source"
            alias = root / "dangling-source-link"
            alias.symlink_to(external_target, target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, "fresh R11 source/provenance snapshot paths"):
                bundle.source_capture(SOURCE_ROOT, alias, profile="current175-r11")
            self.assertTrue(alias.is_symlink())
            self.assertFalse(external_target.exists())
            self.assertFalse((external_parent / "source-origin").exists())

    def test_source_capture_r11_rejects_symlinked_parent_before_creating_external_target(self):
        with tempfile.TemporaryDirectory(prefix="cvw26-r11-parent-link-") as temp:
            root = Path(temp)
            canonical_parent = root / "canonical-parent"
            canonical_parent.mkdir()
            parent_alias = root / "parent-alias"
            parent_alias.symlink_to(canonical_parent, target_is_directory=True)
            external_target = parent_alias / "captured-source"
            with self.assertRaisesRegex(RuntimeError, "parent is not canonical"):
                bundle.source_capture(SOURCE_ROOT, external_target, profile="current175-r11")
            self.assertTrue(parent_alias.is_symlink())
            self.assertFalse((canonical_parent / "captured-source").exists())
            self.assertFalse((canonical_parent / "source-origin").exists())


if __name__ == "__main__":
    unittest.main()
