from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

CANDIDATE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE / "source"))

from tools import w26_application_lifecycle_current as current  # noqa: E402
from tools import w26_sealed_data as legacy_data  # noqa: E402
from tools import w26_sealed_data_current as data  # noqa: E402
from tools.w26_prepare_current_inputs import _helpers  # noqa: E402


WORKSPACE = Path(__file__).resolve().parents[4]
DATA_INPUT_SPEC = (
    WORKSPACE
    / "repository/_state/w26-r17-current175-r12-runtime-input-bridge/inputs/"
    "w26_r13_explicit_data_input_spec_R16.json"
)
DATA_INPUT_SPEC_SHA256 = "6d0cfbab06da20ac79fa8f7dbe4d8226e868b7307bc376fe145641c06a2f2efe"


class StopBeforePopen(Exception):
    """Test sentinel proving inventory admission completed before any child launch."""


class _Owner:
    def __init__(self):
        self.remaining_calls = []
        self.samples = []
        self.process = None
        self.observe_only = False
        self.stop_reason = None
        self.first_cause = None

    def remaining(self, cap):
        self.remaining_calls.append(cap)


class R27DynamicSealedScriptTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="cvw26-r27-script-test-", dir="/private/tmp"))
        (self.root / "logs").mkdir(mode=0o700)
        (self.root / "evidence").mkdir(mode=0o700)
        root_stat = self.root.lstat()
        self.root_identity = {"device": root_stat.st_dev, "inode": root_stat.st_ino}
        self.helper = _helpers()
        self.profile_spec = {
            "schema": "w26-current-reader-profile-spec/1",
            "source_profile": data.R12_PROFILE,
        }
        self.profile_record = self.helper.publish(
            self.root / "CURRENT_PROFILE_SPEC_r27test.json", self.profile_spec
        )
        _, self.data_input_record = self.helper.raw_file(DATA_INPUT_SPEC)
        if self.data_input_record["sha256"] != DATA_INPUT_SPEC_SHA256:
            raise RuntimeError("R27 test data-input spec differs from its frozen source bytes")
        self.selector = data.r13_data_profile_selector(
            str(DATA_INPUT_SPEC), DATA_INPUT_SPEC_SHA256
        )
        self.descriptor = {
            "schema": "w26-sealed-r6-lifecycle-descriptor/1",
            "unit_root": str(self.root),
            "root_identity": self.root_identity,
            "seal": self.selector["seal_record"]["path"],
            "seal_sha256": self.selector["seal_record"]["sha256"],
            "synthetic_data": False,
            "old_historical_dependencies": "UNVERIFIED_CURRENT43_NEW_TEST_BASELINE",
            "current_application_profile": {"source_profile": data.R12_PROFILE},
            "current_profile_spec": self.profile_spec,
            "data_profile": data.R13_DATA_PROFILE,
            "data_selector": self.selector,
            "data_input_spec": self.selector["input_spec_record"],
        }
        self.descriptor_record = self.helper.publish(
            self.root / "EXTERNAL_DATA_r27test.json", self.descriptor
        )
        python_record = self.helper.raw_file(Path(sys.executable).resolve())[1]
        self.runtime_record = self.helper.publish(
            self.root / "CURRENT_RUNTIME_INPUT_r27test.json",
            {"base_python_record": python_record, "test_only": True},
        )
        self.prepared = {"runtime": self.runtime_record, "legacy_sources": {}}
        self.py = self.root / "transition-work/transition-venv/bin/python"
        self.module = None

    def tearDown(self):
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)

    def test_dynamic_old_read_uses_current_inventory_script_and_restores(self):
        base = current.lifecycle
        transition = current.transition_lifecycle
        original_loader = importlib.util.spec_from_file_location
        original_legacy_descriptor = legacy_data.descriptor
        original_script = transition.SEALED_PHASE_SCRIPT
        runtime_source = Path(transition.__file__).resolve(strict=True)
        launch_rows = []
        stage_attempts = []

        with (
            patch.object(data, "_verify_profile_spec_envelope"),
            patch.object(data, "verify_r13_data_baseline", return_value={"consumer_closure": {"verified": True}}),
            patch.object(current, "current_application_profile", return_value={"profile": self.descriptor["current_application_profile"]}),
        ):
            try:
                with current.r9_execution_context(self.profile_record, self.data_input_record):
                    # This is the same inventory producer and dynamic import
                    # call used by full_project_release.transition_check.
                    rows = base.inventory(
                        self.root,
                        str(self.py),
                        self.descriptor_record,
                        {"members": []},
                        {"members": []},
                    )
                    phase_row = next(row for row in rows if row["label"] == "data-old-read")
                    spec = importlib.util.spec_from_file_location(
                        "release_transition_lifecycle", str(runtime_source)
                    )
                    self.assertIsNotNone(spec)
                    self.assertIsNotNone(spec.loader)
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    self.module = module
                    self.assertEqual(module.SEALED_PHASE_SCRIPT, current.CURRENT_SEALED_PHASE_SCRIPT)
                    self.assertNotEqual(module.SEALED_PHASE_SCRIPT, original_script)

                    instance = object.__new__(module.Lifecycle)
                    instance.external_descriptor = self.descriptor_record["path"]
                    instance.external_descriptor_sha = self.descriptor_record["sha256"]
                    instance.release = base.release
                    instance.evidence = self.root / "evidence"
                    instance.profile = self.root / "transition-work/transition-evidence/network-deny-transition.sb"
                    instance.sandbox = "/usr/bin/sandbox-exec"
                    instance.steps = []

                    owner = _Owner()
                    candidate = {"root_identity": self.root_identity, "source": {}}
                    binding = {"nonce": "test-only-no-approval"}
                    logsfd = os.open(self.root / "logs", os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        launch = base.LifecycleLaunch(
                            self.root,
                            owner,
                            candidate,
                            binding,
                            {"inventory": rows},
                            self.prepared,
                            logsfd,
                            0,
                        )
                        original_admit = launch.admit

                        def record_admit(command, kwargs):
                            admitted = original_admit(command, kwargs)
                            launch_rows.append({"label": admitted["label"], "argv": list(command), "kwargs": dict(kwargs)})
                            return admitted

                        launch.admit = record_admit

                        def stop_before_child(*args, **kwargs):
                            stage_attempts.append({"args": list(args), "kwargs": dict(kwargs)})
                            raise StopBeforePopen("test stopped at the admitted pre-Popen boundary")

                        with (
                            patch.object(base, "verify_inputs", return_value={"test_only": True}),
                            patch.object(base.actor, "verify_actor", return_value=None),
                            patch.object(base.actor, "run_owned_stage", side_effect=stop_before_child),
                            base.release._controlled_launch_scope(launch),
                        ):
                            # Simulate R26's exact dynamic-module bug: the
                            # method supplied the current 12-argument tail,
                            # while this isolated module still held the legacy
                            # four-argument code string. Admission must fail
                            # closed and must stop before run_owned_stage/Popen.
                            module.SEALED_PHASE_SCRIPT = original_script
                            with self.assertRaisesRegex(
                                RuntimeError, "outside exact closed inventory"
                            ):
                                module.Lifecycle.seed_old(instance, self.py)
                            self.assertEqual(stage_attempts, [])
                            self.assertEqual(launch_rows, [])

                            module.SEALED_PHASE_SCRIPT = current.CURRENT_SEALED_PHASE_SCRIPT
                            with self.assertRaisesRegex(StopBeforePopen, "pre-Popen"):
                                module.Lifecycle.seed_old(instance, self.py)
                    finally:
                        os.close(logsfd)

                    self.assertEqual(len(launch_rows), 1)
                    self.assertEqual(launch_rows[0]["label"], "data-old-read")
                    self.assertEqual(launch_rows[0]["argv"], phase_row["request_argv"])
                    self.assertEqual(
                        launch_rows[0]["argv"][7], current.CURRENT_SEALED_PHASE_SCRIPT
                    )
                    self.assertEqual(launch_rows[0]["kwargs"], {
                        "capture_output": True,
                        "text": True,
                        "cwd": str(self.root / "transition-work/transition-venv"),
                    })
                    self.assertEqual(len(stage_attempts), 1)
                    self.assertEqual(owner.remaining_calls, [phase_row["cap"]])
                    self.assertFalse((self.root / "UNIT_ONCE_GUARD.json").exists())
                    self.assertFalse((self.root / "logs/TERMINAL_0.json").exists())
            finally:
                self.assertIs(importlib.util.spec_from_file_location, original_loader)
                self.assertIs(legacy_data.descriptor, original_legacy_descriptor)
                self.assertEqual(transition.SEALED_PHASE_SCRIPT, original_script)
                if self.module is not None:
                    self.assertEqual(self.module.SEALED_PHASE_SCRIPT, original_script)
                    restored_method = self.module.Lifecycle.external_arguments
                    self.assertEqual(restored_method.__name__, "external_arguments")
                    self.assertEqual(restored_method.__code__.co_filename, str(runtime_source))


if __name__ == "__main__":
    unittest.main()
