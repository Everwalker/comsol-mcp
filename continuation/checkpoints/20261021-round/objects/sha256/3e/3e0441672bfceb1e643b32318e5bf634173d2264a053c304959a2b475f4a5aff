from __future__ import annotations

import ast
import hashlib
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[1]
SOURCE = ROOT / "source"
PERSISTENT_INPUT_PACKET = ROOT.parent / "w26-r17-current175-r12-runtime-input-bridge"
DATA_INPUT_SPEC = PERSISTENT_INPUT_PACKET / "inputs" / "w26_r13_explicit_data_input_spec_R16.json"
DATA_INPUT_SPEC_SHA256 = "6d0cfbab06da20ac79fa8f7dbe4d8226e868b7307bc376fe145641c06a2f2efe"
sys.path.insert(0, str(SOURCE))

from tools import w26_sealed_data_current as data  # noqa: E402
from tools.w26_prepare_current_inputs import _helpers  # noqa: E402


class R15LifecycleAdapterTests(unittest.TestCase):
    def test_t044_r15_baseline_is_real_persistent_input_only(self):
        selector = data.r13_data_profile_selector(DATA_INPUT_SPEC, DATA_INPUT_SPEC_SHA256)
        summary = data.verify_r13_data_baseline(selector)
        closure = summary["consumer_closure"]
        self.assertEqual(summary["status"], "PASS_T044_R15_PERSISTENT_CONSUMER_BASELINE_NOT_FULLRESTORE")
        self.assertEqual(selector["seal_record"]["sha256"], data.R13_DATA_SEAL_SHA256)
        self.assertEqual(summary["input_tree"]["files_verified"], 51)
        self.assertEqual(closure["table_counts"]["operations"], 29)
        self.assertEqual(closure["table_counts"]["jobs"], 29)
        self.assertEqual(len(closure["project_files"]), 6)
        self.assertEqual(closure["registered_blob_count"], 3)
        self.assertEqual(summary["raw_original_store_opened"], False)
        self.assertEqual(summary["fullrestore"], "NOT_RUN")

    def test_t044_selector_rejects_cross_profile_and_changed_seal(self):
        selector = data.r13_data_profile_selector(DATA_INPUT_SPEC, DATA_INPUT_SPEC_SHA256)
        with self.assertRaisesRegex(RuntimeError, "explicitly selected finite data-input spec"):
            data.verify_r13_data_baseline({**selector, "profile_name": data.R11_PROFILE})
        with self.assertRaisesRegex(RuntimeError, "not the selected baseline"):
            data.verify_seal(selector["seal_record"]["path"], "0" * 64,
                             data_selector=selector)

    def test_r12_profile_spec_uses_exact_lock_map_and_current_source_closure(self):
        sources = {}
        for name in data.CURRENT_NAMES:
            path = data.CURRENT_TOOLS_ROOT / name
            sources[str(path)] = _helpers().raw_file(path)[1]
        lock_record = _helpers().raw_file(data.R12_LOCK)[1]
        spec = data.build_current_profile_spec(data.R12_PROFILE, lock_record, sources)
        self.assertEqual(spec["source_profile"], data.R12_PROFILE)
        self.assertEqual(spec["selected_lock_record"]["sha256"], data.R12_LOCK_SHA256)
        self.assertEqual(set(spec["loader_source_closure"]["members"]), set(data.CURRENT_NAMES))
        profile = {
            "schema": "w26-current-installed-application/1",
            "source_profile": data.R12_PROFILE,
            "root": "/private/tmp/cvw26-application-00000000",
            "result": {}, "candidate": {}, "once": {},
            "loader_source_closure": spec["loader_source_closure"],
        }
        data._validate_descriptor_profile_selector({
            "current_application_profile": profile,
            "current_profile_spec": spec,
        }, spec)
        wrong = {**spec, "selected_lock_record": {"path": str(data.R12_LOCK), "sha256": "f" * 64}}
        with self.assertRaisesRegex(RuntimeError, "pinned finite current source lock"):
            data._validate_descriptor_profile_selector({
                "current_application_profile": profile,
                "current_profile_spec": wrong,
            }, wrong)

    def test_r13_base_scope_patches_only_named_consumers_and_restores_them(self):
        names = ("descriptor", "data_paths", "verify_seal", "migration_bundle",
                 "verify_migration", "supplemental_copy", "verify_restored")
        before = {name: data.BASE_NAMESPACE[name] for name in names}
        spec = {"source_profile": data.R12_PROFILE,
                "selected_lock_record": {"sha256": data.R12_LOCK_SHA256}}
        selector = data.r13_data_profile_selector(DATA_INPUT_SPEC, DATA_INPUT_SPEC_SHA256)
        with data._r13_base_consumer_scope(spec, selector):
            for name in names:
                self.assertTrue(callable(data.BASE_NAMESPACE[name]))
                self.assertNotIn(data.BASE_NAMESPACE[name], before.values())
        for name in names:
            self.assertIs(data.BASE_NAMESPACE[name], before[name])
        with self.assertRaisesRegex(RuntimeError, "require a finite current175 R12 or R13 profile"):
            with data._r13_base_consumer_scope({
                "source_profile": data.R11_PROFILE,
                "selected_lock_record": {"sha256": data.R11_LOCK_SHA256},
            }, selector):
                pass

    def test_r13_product_descriptor_refuses_preflight_only_schema(self):
        with self.assertRaisesRegex(RuntimeError, "product lifecycle descriptor schema"):
            data._verify_r13_lifecycle_descriptor_value({
                "schema": "w26-r13-data-preflight-descriptor/1",
                "data_profile": data.R13_DATA_PROFILE,
            }, None)

    def test_r9_no_spec_legacy_tuple_is_still_restricted_to_original_map(self):
        legacy = {
            "schema": "w26-current-installed-application/1",
            "source_profile": "current175-r9",
            "root": data._LEGACY_CLOSURE["root"],
            "result": {}, "candidate": {}, "once": {},
            "loader_source_closure": data._LEGACY_CLOSURE,
        }
        data._validate_descriptor_profile_selector({
            "current_application_profile": legacy,
            "current_profile_spec": None,
        }, None)
        changed = {**legacy, "loader_source_closure": {"root": legacy["root"], "members": {}}}
        with self.assertRaisesRegex(RuntimeError, "frozen R9 tuple"):
            data._validate_descriptor_profile_selector({
                "current_application_profile": changed,
                "current_profile_spec": None,
            }, None)

    def test_reader_has_no_dynamic_getattr_or_globals_dispatch(self):
        for path in (SOURCE / "tools/w26_sealed_data_current.py",
                     SOURCE / "tools/w26_application_lifecycle_current.py"):
            tree = ast.parse(path.read_text())
            names = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
            calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Name) and node.func.id in {"globals", "locals"}]
            self.assertNotIn("__getattr__", names)
            self.assertEqual(calls, [])

    def test_historical_r9_source_fixture_revalidates_frozen_175_bytes(self):
        from tests.w26_r9_source_fixture import saved_r9_source_fixture
        from tools import w26_application_bundle as app

        source_root, extras = saved_r9_source_fixture()
        lock = json.loads(data.BASE["raw_file"](data.R9_LOCK)[0])
        self.assertEqual(len(app.source_members(source_root)), 175)
        self.assertEqual(data._source_member_map_fingerprint(lock["members"]),
                         data.R9_MEMBER_FINGERPRINT_SHA256)
        self.assertEqual(set(extras), {"uv_lock", "operations"})
        for path in extras.values():
            self.assertIn(_helpers().raw_file(path)[1]["sha256"], {
                "2058044231f3ace0a36b537a12b041579af94b9042add9acfd18ca0c0d7505c1",
                "03e2ca02a88e2c58cc496020e8966798b5dac3ba46fa35e5af13e5d56cd61345",
            })

    def test_only_fixed_sealed_phase_names_are_admitted(self):
        with self.assertRaisesRegex(RuntimeError, "unknown sealed actual data phase"):
            data.run_phase("unused", "unused", "not-a-phase")

    def test_r13_product_phase_preflight_refuses_implicit_data_selector(self):
        with self.assertRaisesRegex(RuntimeError, "requires its explicit data-input spec"):
            data.run_phase("unused", "unused", "old-read", input_preflight_only=True)


if __name__ == "__main__":
    unittest.main()
