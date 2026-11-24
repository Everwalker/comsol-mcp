"""In-memory material input and tensor behavior retained by the V2 transplant."""

from __future__ import annotations

import sys
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from comsol_mcp import _g3_w15  # noqa: E402
from comsol_mcp._execution_contract import ExecutionContractError  # noqa: E402
from tests.test_t22_reviewfix import FakeMaterial, MATERIAL_PATH  # noqa: E402


class T22MaterialBehaviorTests(unittest.TestCase):
    @staticmethod
    def _update(material, definition):
        with patch.object(_g3_w15, "_material_lookup", return_value=(material, MATERIAL_PATH)):
            return _g3_w15.material_group_manage(
                object(),
                "memory-model",
                {"path": MATERIAL_PATH, "action": "update", "group": "def", "definition": definition},
            )

    def test_add_remove_and_combined_input_changes_keep_readback(self):
        cases = (
            (("Cp",), {"add": ["T"]}, ["Cp", "T"]),
            (("Cp", "T"), {"remove": ["T"]}, ["Cp"]),
            (("Cp", "T"), {"add": ["V"], "remove": ["T"]}, ["Cp", "V"]),
        )
        for initial, changes, expected in cases:
            with self.subTest(initial=initial, changes=changes):
                material = FakeMaterial(inputs=initial)
                result = self._update(material, {"inputs": changes})
                self.assertEqual(result["status"], "APPLIED")
                self.assertEqual(result["applied"][0]["inputs"]["readback"], expected)
                self.assertEqual(material.group.inputs, expected)

    def test_partial_input_failure_is_not_misreported_as_unknown(self):
        material = FakeMaterial(inputs=["Cp"])
        original_add = material.group.addInput

        def add_input(name):
            if name == "T2":
                raise ExecutionContractError("METHOD_REJECTED", "memory fake rejects second addition")
            original_add(name)

        material.group.addInput = add_input
        result = self._update(material, {"inputs": {"add": ["T1", "T2"]}})
        self.assertEqual(result["status"], "PARTIAL_FAILURE")
        self.assertTrue(result["partial_change"])
        self.assertFalse(result["execution_state_unknown"])
        self.assertEqual(material.group.inputs, ["Cp", "T1"])

    def test_tensor_rank_one_storage_and_undocumented_six_entry_readback(self):
        matrix, form = _g3_w15._tensor_matrix_from_value(
            [["2", "0", "0"], ["0", "3", "0"], ["0", "0", "4"]],
            label="thermalconductivity",
        )
        self.assertEqual(form, "matrix")
        typed, record = _g3_w15._tensor_typed_value(
            "thermalconductivity", matrix, kind="string", engine_rank=1,
            label="thermalconductivity",
        )
        self.assertEqual(typed["data"], ["2", "3", "4"])
        verified = _g3_w15.material_tensor_readback(
            "thermalconductivity", record, typed["data"],
            schema={"shape_rank": 1}, label="thermalconductivity",
        )
        self.assertTrue(verified["equivalent"])
        compact = _g3_w15.material_tensor_readback(
            "thermalconductivity", record, ["2", "3", "4", "0", "0", "0"],
            schema={"shape_rank": 1}, label="thermalconductivity",
        )
        self.assertFalse(compact["equivalent"])
        self.assertEqual(compact["rule"], "readback_form_not_documented")


if __name__ == "__main__":
    unittest.main()
