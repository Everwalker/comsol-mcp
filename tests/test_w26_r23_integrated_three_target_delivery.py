from __future__ import annotations
import hashlib
import importlib
import json
import os
import sys
import tempfile
from types import ModuleType
import unittest
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
SOURCE=Path(os.environ.get("W26_R26_SOURCE", str(BASE/"source"))).resolve()
TARGET_INPUTS=Path(os.environ.get("W26_R26_TARGET_INPUTS", "/private/tmp/cvw26-target-inputs-e9083088/target-inputs/TARGET_INPUTS.json"))
TARGET_SOURCE=Path(os.environ["W26_R26_TARGET_SOURCE"]).resolve()
NOTICE=Path("/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V3/repository/_state/w26-r23-primary-notice-inputs-3e41f37d48/NOTICE_INPUT_MANIFEST_R2.json")
PACKAGING=Path("/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V3/repository/_state/w26-r17-current175-r12-runtime-input-bridge/inputs/recovered-exact-wheelhouse/packaging-26.3-py3-none-any.whl")
PACKAGING_SHA="d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c"

def modules():
    if hashlib.sha256(PACKAGING.read_bytes()).hexdigest()!=PACKAGING_SHA:
        raise RuntimeError("pinned packaging 26.3 test dependency differs")
    source_root=SOURCE.resolve()
    for path in (PACKAGING.resolve(),source_root):
        value=str(path)
        if value in sys.path:
            sys.path.remove(value)
        sys.path.insert(0,value)
    for name,module in tuple(sys.modules.items()):
        if name=="tools" or name.startswith("tools."):
            origin=getattr(module,"__file__",None)
            if origin is None:
                locations=tuple(getattr(module,"__path__",()))
                if not locations or any(not Path(location).resolve().is_relative_to(source_root)
                                         for location in locations):
                    raise RuntimeError("unverifiable or foreign tools module origin is already loaded: "+name)
            elif not Path(origin).resolve().is_relative_to(source_root):
                raise RuntimeError("foreign tools module origin is already loaded: "+name)
    importlib.invalidate_caches()
    return (importlib.import_module("tools.w26_application_bundle_current"),
            importlib.import_module("tools.w26_primary_notices"))

def delivery(bundle,notices,manifest):
    h=notices._helpers()
    return ({"target_inputs":h.raw_file(TARGET_INPUTS)[1],"primary_notices":h.raw_file(manifest)[1],
             "targets":["macos_arm64","macos_x86_64","win_amd64"],
             "runtime_counts":{"macos_arm64":41,"macos_x86_64":41,"win_amd64":34},
             "package_counts":{"macos_arm64":42,"macos_x86_64":42,"win_amd64":35}},
            {"source_profile":bundle.LATEST_SOURCE_PROFILE,"snapshot_source":"/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V3/repository/_state/w26-r22-lifecycle-actual-full-selector-consumer/source","original_source":str(TARGET_SOURCE)})

class IntegratedR23DeliveryTests(unittest.TestCase):
    def test_module_loader_preserves_candidate_objects_and_rejects_foreign_origin(self):
        bundle,notices=modules()
        before={name:module for name,module in tuple(sys.modules.items())
                if name=="tools" or name.startswith("tools.")}
        self.assertTrue(before)
        self.assertIs(bundle,importlib.import_module("tools.w26_application_bundle_current"))
        self.assertIs(notices,importlib.import_module("tools.w26_primary_notices"))
        for name,module in before.items():
            self.assertIs(sys.modules.get(name),module)

        foreign=ModuleType("tools.foreign_test_origin")
        foreign.__file__="/private/tmp/w26-foreign-tools-origin.py"
        sys.modules[foreign.__name__]=foreign
        try:
            with self.assertRaisesRegex(RuntimeError,"foreign tools module origin"):
                modules()
        finally:
            sys.modules.pop(foreign.__name__,None)

        self.assertIs(bundle,importlib.import_module("tools.w26_application_bundle_current"))
        self.assertIs(notices,importlib.import_module("tools.w26_primary_notices"))

    def test_integrated_actual_consumer_accepts_finite_official_manifest(self):
        bundle,notices=modules();d,s=delivery(bundle,notices,NOTICE)
        result=bundle.verify_delivery(d,s)
        self.assertEqual(result["schema"],"W26_CURRENT_THREE_TARGET_INPUTS_INTEL_PREBUILT_V1")
        self.assertEqual(set(notices.notice_payloads(NOTICE)),{"pyobjc-core","pyobjc-framework-applicationservices","pyobjc-framework-coremedia","pyobjc-framework-screencapturekit"})

    def test_integrated_reader_binds_target_uv_lock_to_original_source_not_copy(self):
        bundle,notices=modules();d,s=delivery(bundle,notices,NOTICE)
        accepted=bundle.verify_delivery(d,s)
        self.assertEqual(accepted["schema"],"W26_CURRENT_THREE_TARGET_INPUTS_INTEL_PREBUILT_V1")
        wrong=dict(s);wrong["original_source"]="/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V3/repository/_state/w26-r22-lifecycle-actual-full-selector-consumer/source"
        with self.assertRaisesRegex(RuntimeError,"historical Intel profile archive/source/Mach-O metadata drift"):
            bundle.verify_delivery(d,wrong)

    def test_integrated_actual_consumer_rejects_tamper_and_old_cross_profile_sha(self):
        bundle,notices=modules()
        with tempfile.TemporaryDirectory() as tmp:
            changed=Path(tmp)/"NOTICE_INPUT_MANIFEST.json";raw=NOTICE.read_bytes();changed.write_bytes(raw[:-1]+(b" " if raw[-1:]!=b" " else b"\n"))
            with self.assertRaisesRegex(RuntimeError,"unknown primary notice manifest hash"):
                notices.notice_payloads(changed)
            with self.assertRaisesRegex(RuntimeError,"unknown primary notice manifest hash"):
                notices.notice_payloads(NOTICE,expected_sha=notices.MANIFEST_SHA)
            d,s=delivery(bundle,notices,changed)
            with self.assertRaisesRegex(RuntimeError,"unknown primary notice manifest hash"):
                bundle.verify_delivery(d,s)

if __name__=="__main__":
    unittest.main(verbosity=2)
