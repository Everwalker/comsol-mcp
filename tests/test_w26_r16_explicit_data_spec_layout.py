"""Exercise the real T044 data selector from the ordinary repository/tools layout."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


CANDIDATE = Path(__file__).resolve().parents[1]
SOURCE_TOOLS = CANDIDATE / "source" / "tools"
PERSISTENT_INPUT_PACKET = CANDIDATE.parent / "w26-r17-current175-r12-runtime-input-bridge"
INPUT_SPEC = PERSISTENT_INPUT_PACKET / "inputs" / "w26_r13_explicit_data_input_spec_R16.json"
INPUT_SPEC_SHA256 = "6d0cfbab06da20ac79fa8f7dbe4d8226e868b7307bc376fe145641c06a2f2efe"


class R16ExplicitDataSpecLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="w26-r16-normal-layout-", dir="/private/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repository = self.root / "repository"
        self.repository.mkdir()
        shutil.copytree(SOURCE_TOOLS, self.repository / "tools",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        common_source = (CANDIDATE.parents[1] / "continuation/packets/w26-offline-venv-unit-r1"
                         / "frozen-work/common.py")
        common_dest = (self.repository / "continuation/packets/w26-offline-venv-unit-r1"
                       / "frozen-work/common.py")
        common_dest.parent.mkdir(parents=True)
        shutil.copy2(common_source, common_dest)
        self.assertEqual(hashlib.sha256(common_dest.read_bytes()).hexdigest(),
                         "94a5b742551ee9ef21224ee1abd3d415f73a1fff1447559d600242f6a7bf0c8f")

    def run_isolated(self, expression: str):
        code = (
            "import json,sys; from pathlib import Path; "
            f"sys.path.insert(0,{str(self.repository)!r}); "
            "from tools import w26_sealed_data_current as data; "
            + expression
        )
        return subprocess.run([sys.executable, "-I", "-B", "-c", code],
                              check=False, capture_output=True, text=True, timeout=90)

    def test_normal_repository_tools_layout_reads_actual_frozen_baseline(self):
        result = self.run_isolated(
            f"s=data.r13_data_profile_selector({str(INPUT_SPEC)!r},{INPUT_SPEC_SHA256!r}); "
            "v=data.verify_r13_data_baseline(s); "
            "print(json.dumps({'selector_schema':s['schema'],'files':v['input_tree']['files_verified'],"
            "'directories':v['input_tree']['directories_verified'],'tables':len(v['consumer_closure']['table_counts']),"
            "'operations':v['consumer_closure']['table_counts']['operations'],"
            "'jobs':v['consumer_closure']['table_counts']['jobs'],"
            "'raw_originals_opened':v['raw_original_store_opened'],"
            "'analysis_copy_readonly':v['consumer_analysis_database_opened_immutable_readonly']}))"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value, {
            "selector_schema": "w26-r16-explicit-data-baseline-selector/1",
            "files": 51, "directories": 11, "tables": 14,
            "operations": 29, "jobs": 29,
            "raw_originals_opened": False, "analysis_copy_readonly": True,
        })

    def test_wrong_spec_hash_is_rejected_in_normal_layout(self):
        result = self.run_isolated(
            f"data.r13_data_profile_selector({str(INPUT_SPEC)!r},{'0'*64!r})"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exact T044 R13 data-input spec SHA-256 differs", result.stderr)

    def test_wrong_payload_root_is_rejected_against_sealed_root_and_identity(self):
        spec = json.loads(INPUT_SPEC.read_bytes())
        original = Path(spec["payload_root"])
        spec["payload_root"] = str(original.parent)
        changed = self.root / "wrong-root-spec.json"
        raw = json.dumps(spec, sort_keys=True, indent=2, ensure_ascii=False).encode() + b"\n"
        changed.write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        result = self.run_isolated(
            f"data.r13_data_profile_selector({str(changed)!r},{digest!r})"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("persistent seal, payload identity, or source lineage differs", result.stderr)

    def test_wrong_seal_sha_is_rejected_before_baseline_reads(self):
        spec = json.loads(INPUT_SPEC.read_bytes())
        spec["seal"]["sha256"] = "0" * 64
        changed = self.root / "wrong-seal-spec.json"
        raw = json.dumps(spec, sort_keys=True, indent=2, ensure_ascii=False).encode() + b"\n"
        changed.write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        result = self.run_isolated(
            f"data.r13_data_profile_selector({str(changed)!r},{digest!r})"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside the finite reviewed lineage", result.stderr)

    def test_product_phase_route_carries_explicit_data_and_packaging_inputs(self):
        packaging = json.loads(INPUT_SPEC.read_bytes())["preflight_packaging_wheel"]["path"]
        result = self.run_isolated(
            f"sys.path.insert(0,{packaging!r}); "
            "from tools import w26_application_lifecycle_current as lifecycle; "
            f"s=data.r13_data_profile_selector({str(INPUT_SPEC)!r},{INPUT_SPEC_SHA256!r}); "
            f"p=Path({str(self.root / 'profile-spec.json')!r}); p.write_text('{{}}'); "
            "pr=lifecycle._helpers().raw_file(p)[1]; "
            "rows=[{'label':'data-'+phase,'request_argv':['/usr/bin/sandbox-exec','-f','PROFILE',"
            "'/private/tmp/python','-I','-c','SCRIPT',str(data.__file__),'/private/tmp/descriptor.json',"
            "'a'*64,phase]} for phase in data.SEALED_PHASES]; "
            "out=lifecycle._route_sealed_phase_rows(rows,str(data.__file__),pr,"
            "data_input_spec_record=s['input_spec_record'],input_preflight_only=True); "
            "expected=['--profile-spec',pr['path'],'--profile-spec-sha',pr['sha256'],"
            "'--data-input-spec',s['input_spec_record']['path'],'--data-input-spec-sha',s['input_spec_sha256'],"
            "'--preflight-packaging-wheel',s['input_spec']['preflight_packaging_wheel']['path'],"
            "'--preflight-packaging-wheel-sha',s['input_spec']['preflight_packaging_wheel']['sha256'],"
            "'--input-preflight-only']; "
            "assert all(row['request_argv'][-len(expected):]==expected for row in out); "
            "assert 'parents[2]' not in lifecycle.CURRENT_SEALED_PHASE_SCRIPT; "
            "print(json.dumps({'routes':len(out),'tail':expected,'helper_relative_wheel_lookup':False}))"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["routes"], 4)
        self.assertFalse(value["helper_relative_wheel_lookup"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
