"""Synthetic regression coverage for per-solution parameter coordinates."""
from __future__ import annotations

import unittest

from comsol_mcp._g3_results import _field_array_context


class ResultMetadataPairAlignmentTests(unittest.TestCase):
    def test_units_omit_time_and_frequency_when_no_pair_has_parameters(self) -> None:
        binding = {
            "binding_source": "synthetic SolutionInfo pair metadata",
            "pair_mapping_complete": True,
            "outer_indices": [1],
            "inner_indices": [1, 2],
            "solnum_pairs": [
                {"outer": 1, "inner": 1, "solnum": 1},
                {"outer": 1, "inner": 2, "solnum": 2},
            ],
            "parameter_names": [],
            "parameter_names_by_pair": {(1, 1): [], (1, 2): []},
            "parameter_values_by_pair": {(1, 1): [], (1, 2): []},
            "parameter_units_by_pair": {(1, 1): [], (1, 2): []},
        }

        coords, units, metadata = _field_array_context(
            binding,
            ["voltage"],
            expression_units={"voltage": "V"},
            point_count=1,
        )

        self.assertNotIn("time", coords)
        self.assertNotIn("frequency", coords)
        self.assertNotIn("time", units)
        self.assertNotIn("frequency", units)
        self.assertNotIn("time_unit", metadata)
        self.assertNotIn("frequency_unit", metadata)
        self.assertEqual(coords["parameters"], [{}, {}])
        self.assertEqual(len(metadata["solution_pairs"]), 2)

    def test_mixed_time_and_frequency_parameters_keep_pair_alignment(self) -> None:
        binding = {
            "binding_source": "synthetic SolutionInfo pair metadata",
            "pair_mapping_complete": True,
            "outer_indices": [1, 2],
            "inner_indices": [1, 2],
            "solnum_pairs": [
                {"outer": 1, "inner": 1, "solnum": 1},
                {"outer": 2, "inner": 1, "solnum": 2},
                {"outer": 2, "inner": 2, "solnum": 3},
            ],
            "parameter_names": ["lambda", "t", "freq"],
            "parameter_names_by_pair": {
                (1, 1): ["lambda"],
                (2, 1): ["t", "freq", "lambda"],
                (2, 2): ["t", "lambda"],
            },
            "parameter_values_by_pair": {
                (1, 1): [500.0],
                (2, 1): [2.5, 60.0, 700.0],
                (2, 2): [3.5, 800.0],
            },
            "parameter_units_by_pair": {
                (1, 1): ["nm"],
                (2, 1): ["s", "Hz", "nm"],
                (2, 2): ["s", "nm"],
            },
        }

        coords, units, metadata = _field_array_context(
            binding,
            ["voltage", "current"],
            expression_units={"voltage": "V", "current": "A"},
            point_count=1,
        )

        self.assertEqual(coords["expression"], ["voltage", "current"])
        self.assertEqual(units["expression"], {"voltage": "V", "current": "A"})
        self.assertEqual(
            coords["parameters"],
            [
                {"lambda": 500.0},
                {"t": 2.5, "freq": 60.0, "lambda": 700.0},
                {"t": 3.5, "lambda": 800.0},
            ],
        )
        self.assertEqual(len(metadata["solution_pairs"]), 3)
        self.assertEqual(
            [pair["parameter_units"] for pair in metadata["solution_pairs"]],
            [
                {"lambda": "nm"},
                {"t": "s", "freq": "Hz", "lambda": "nm"},
                {"t": "s", "lambda": "nm"},
            ],
        )
        self.assertEqual(
            coords["time"],
            [None, 2.5, 3.5],
            "time coordinate positions must remain aligned with solution_pairs",
        )
        self.assertEqual(
            coords["frequency"],
            [None, 60.0, None],
            "frequency coordinate positions must remain aligned with solution_pairs",
        )
        self.assertEqual(units["time"], "s")
        self.assertEqual(units["frequency"], "Hz")
        self.assertEqual(metadata["time_unit"], "s")
        self.assertEqual(metadata["frequency_unit"], "Hz")

        complete_binding = {
            **binding,
            "parameter_names": ["t", "freq"],
            "parameter_names_by_pair": {
                (1, 1): ["t", "freq"],
                (2, 1): ["t", "freq"],
                (2, 2): ["t", "freq"],
            },
            "parameter_values_by_pair": {
                (1, 1): [0.0, 50.0],
                (2, 1): [1.0, 60.0],
                (2, 2): [2.0, 70.0],
            },
            "parameter_units_by_pair": {
                (1, 1): ["s", "Hz"],
                (2, 1): ["s", "Hz"],
                (2, 2): ["s", "Hz"],
            },
        }
        complete_coords, complete_units, complete_metadata = _field_array_context(
            complete_binding,
            ["voltage", "current"],
            expression_units={"voltage": "V", "current": "A"},
        )
        self.assertEqual(complete_coords["time"], [0.0, 1.0, 2.0])
        self.assertEqual(complete_coords["frequency"], [50.0, 60.0, 70.0])
        self.assertEqual(complete_units["time"], "s")
        self.assertEqual(complete_units["frequency"], "Hz")
        self.assertEqual(complete_metadata["time_unit"], "s")
        self.assertEqual(complete_metadata["frequency_unit"], "Hz")


if __name__ == "__main__":
    unittest.main()
