from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

CANDIDATE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE / "source"))

from tools import w26_application_lifecycle_current as lifecycle  # noqa: E402
from tools import w26_application_lifecycle_base_current as base_lifecycle  # noqa: E402
from tools import w26_sealed_data_current as data  # noqa: E402
from tools.w26_prepare_current_inputs import _helpers  # noqa: E402


WORKSPACE = Path(__file__).resolve().parents[4]
DATA_INPUT_SPEC = (
    WORKSPACE
    / "repository/_state/w26-r17-current175-r12-runtime-input-bridge/inputs/"
    "w26_r13_explicit_data_input_spec_R16.json"
)
DATA_INPUT_SPEC_SHA256 = "6d0cfbab06da20ac79fa8f7dbe4d8226e868b7307bc376fe145641c06a2f2efe"
BACKUP_SOURCE_SHA256 = "a" * 64


class R28BackupProducerProfileSpecTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="cvw26-r28-backup-spec-", dir="/private/tmp"))
        self.helper = _helpers()
        self.profile_spec = {
            "schema": "w26-current-reader-profile-spec/1",
            "source_profile": data.R12_PROFILE,
        }
        self.profile_spec_record = self.helper.publish(
            self.root / "CURRENT_PROFILE_SPEC_r28test.json", self.profile_spec
        )
        raw, self.data_input_record = self.helper.raw_file(DATA_INPUT_SPEC)
        if (self.data_input_record["sha256"] != DATA_INPUT_SPEC_SHA256
                or json.loads(raw).get("schema") != "w26-r13-explicit-data-input-spec/1"):
            raise RuntimeError("R28 explicit T044 input spec differs from its frozen source")
        self.selector = data.r13_data_profile_selector(
            str(DATA_INPUT_SPEC), DATA_INPUT_SPEC_SHA256
        )
        self.profile = {"source_profile": data.R12_PROFILE}
        self.evidence = self.root / "evidence"

    def tearDown(self):
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)

    def _launch(self, profile=None):
        selected = self.profile if profile is None else profile
        return SimpleNamespace(
            root=self.root,
            prepared={"current_application_profile": selected},
        )

    def _fake_protection_inputs(self, _launch, phase):
        receipt = {"operation": "full_backup", "success": True}
        archive = {
            "schema": "w26-observed-original-fullbackup-archive/1",
            "producer_source_sha256": BACKUP_SOURCE_SHA256,
            # Deliberate safe stop after the real profile/producer call. The
            # constructor must reject this receipt before opening any file.
            "producer_receipt": {"test_only": "different receipt"},
            "scratch": {"path": str(self.evidence / ".comsol-full-backup-a1b2c3d4")},
        }
        if phase == "data-candidate-backup":
            return {"actual_archive_binding": archive, "backup": receipt}
        return {"restore": {"test_only": True}, "readback": {"test_only": True}}

    def _exercise_actual_protection_pairs(self, launch, profile_spec_record,
                                          data_input_record=None):
        calls = []

        def checked_profile(profile, base_namespace, *, profile_spec=None):
            calls.append((profile, base_namespace, profile_spec))
            self.assertEqual(profile, launch.prepared["current_application_profile"])
            self.assertIs(base_namespace, data.BASE)
            self.assertEqual(profile_spec, self.profile_spec)
            return {"backup_source_sha256": BACKUP_SOURCE_SHA256}

        with (
            patch.object(lifecycle, "current_application_profile", side_effect=checked_profile),
            patch.object(base_lifecycle, "_original_protection_capture",
                         side_effect=self._fake_protection_inputs),
            patch.object(data, "data_paths", return_value={"evidence": self.evidence}),
            patch.object(data, "backup_producer_sha", wraps=data.backup_producer_sha) as producer_call,
        ):
            with lifecycle.r9_execution_context(profile_spec_record, data_input_record):
                with self.assertRaisesRegex(
                    RuntimeError, "original archive read-only protection proof differs"
                ):
                    base_lifecycle._ProtectionPairs(launch)
                producer_call.assert_called_once_with(launch.prepared)
                self.assertEqual(len(calls), 1)
        return calls

    def test_actual_protection_pairs_routes_r12_backup_producer_with_exact_base_and_spec(self):
        calls = self._exercise_actual_protection_pairs(
            self._launch(), self.profile_spec_record, self.data_input_record
        )
        self.assertEqual(calls[0][0], self.profile)
        self.assertIs(calls[0][1], data.BASE)
        self.assertEqual(calls[0][2], self.profile_spec)

    def test_r12_consumer_rejects_wrong_base_namespace_and_wrong_spec(self):
        original_reader = data.current_application_profile
        with lifecycle.r9_execution_context(self.profile_spec_record, self.data_input_record):
            selected = data.current_application_profile
            with self.assertRaisesRegex(RuntimeError, "unbound base namespace"):
                selected(self.profile, dict(data.BASE), profile_spec=self.profile_spec)
            with self.assertRaisesRegex(RuntimeError, "unbound profile spec"):
                selected(self.profile, data.BASE,
                         profile_spec={**self.profile_spec, "source_profile": data.R11_PROFILE})
        self.assertIs(data.current_application_profile, original_reader)

    def test_actual_protection_pairs_rejects_wrong_selected_profile(self):
        launch = self._launch({"source_profile": data.R11_PROFILE})
        with (
            patch.object(lifecycle, "current_application_profile") as profile_reader,
            patch.object(base_lifecycle, "_original_protection_capture",
                         side_effect=self._fake_protection_inputs),
            patch.object(data, "data_paths", return_value={"evidence": self.evidence}),
        ):
            with lifecycle.r9_execution_context(self.profile_spec_record, self.data_input_record):
                with self.assertRaisesRegex(RuntimeError, "differs from its selected profile spec"):
                    base_lifecycle._ProtectionPairs(launch)
            profile_reader.assert_not_called()

    def test_r11_protection_route_retains_explicit_legacy_profile_selection(self):
        helper = _helpers()
        r11_spec = {"schema": "w26-current-reader-profile-spec/1",
                    "source_profile": data.R11_PROFILE}
        r11_record = helper.publish(self.root / "CURRENT_PROFILE_SPEC_r11.json", r11_spec)
        launch = self._launch({"source_profile": data.R11_PROFILE})
        calls = []

        def checked_profile(profile, base_namespace, *, profile_spec=None):
            calls.append((profile, base_namespace, profile_spec))
            self.assertIs(base_namespace, data.BASE)
            self.assertEqual(profile_spec, r11_spec)
            return {"backup_source_sha256": BACKUP_SOURCE_SHA256}

        with (
            patch.object(lifecycle, "current_application_profile", side_effect=checked_profile),
            patch.object(base_lifecycle, "_original_protection_capture",
                         side_effect=self._fake_protection_inputs),
            patch.object(data, "data_paths", return_value={"evidence": self.evidence}),
        ):
            with lifecycle.r9_execution_context(r11_record):
                with self.assertRaisesRegex(
                    RuntimeError, "original archive read-only protection proof differs"
                ):
                    base_lifecycle._ProtectionPairs(launch)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][2], r11_spec)


if __name__ == "__main__":
    unittest.main()
