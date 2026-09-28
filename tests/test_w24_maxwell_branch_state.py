from __future__ import annotations

import copy

import pytest

from tools.w24_maxwell_branch_state import (
    MaxwellStateError, compare_full_xmesh_diagnostics,
    compare_persisted_full_xmesh_state,
)


CONFIG = "a" * 64


def _frame(time_s: float, values=(10.0, 20.0)):
    return {
        "time_s": time_s,
        "u_real": list(values),
        "u_imag": [0.0, 0.0],
        "dofs": {
            "snapshot_schema": "W24-DOF-SNAPSHOT-3",
            "complete_xmesh_internal_dof_capture": True,
            "complete_xmesh_dofs": True,
            "layout_sha256": "b" * 64,
            "coordinate_axes": 3,
            "xmesh_n_dofs": 2,
            "fieldNames": ["fieldA", "fieldB"],
            "fieldNDofs": [1, 1],
            "dofNames": ["fieldA", "fieldB"],
            "geomNums": [1, 1],
            "nodes": [1, 2],
            "nameInds": [0, 1],
            "solVectorInds": [0, 1],
            "coords": [[0.0, 1.0], [0.0, 0.0], [0.0, 0.0]],
            "mapping_summary": {
                "vector_length": 2,
                "mapped_dof_rows": 2,
                "unmapped_dof_rows": 0,
                "invalid_solution_indices": 0,
                "out_of_range_solution_indices": 0,
                "duplicate_solution_vector_index_rows": 0,
                "unique_mapped_vector_indices": 2,
                "unrepresented_solution_vector_indices": 0,
                "full_vector_and_internal_dof_map_covered": True,
            },
            "invalid_element_dof_references": 0,
            "element_local_map_group_count": 1,
            "element_local_map_entries": 2,
        },
    }


def _frames(values_by_time=((10.0, 20.0), (11.0, 21.0))):
    return [_frame(float(index), values) for index, values in enumerate(values_by_time)]


def test_persisted_source_and_reopen_require_exact_finite_full_solution_vectors():
    source = _frames()
    reopened = copy.deepcopy(source)
    comparison = compare_persisted_full_xmesh_state(
        source, reopened,
        source_configuration_sha256=CONFIG,
        reopened_configuration_sha256=CONFIG,
    )
    assert comparison["status"] == "PERSISTED_FULL_XMESH_STATE_EXACT_MATCH"
    assert comparison["solution_double_count"] == 4
    assert comparison["maxwell_branch_field_identity"] == "UNVERIFIED_NOT_INFERRED_FROM_FIELD_NAME"
    assert comparison["maxwell_reference_state_semantics"] == "UNVERIFIED_NATIVE_OBSERVATION_REQUIRED"
    assert comparison["native_acceptance"] == "NOT_RUN"

    # Resetting one otherwise-hidden vector value is a persistence failure.
    reset = copy.deepcopy(source)
    reset[1]["u_real"][1] = 0.0
    with pytest.raises(MaxwellStateError, match="solution double differs"):
        compare_persisted_full_xmesh_state(
            source, reset,
            source_configuration_sha256=CONFIG,
            reopened_configuration_sha256=CONFIG,
        )


@pytest.mark.parametrize("mutation, message", [
    (lambda rows: rows[0].update(time_s=0.25), "stored time differs"),
    (lambda rows: [row["dofs"].update(layout_sha256="c" * 64) for row in rows], "layout hashes differ"),
    (lambda rows: [row["dofs"].update(coordinate_axes=2) for row in rows], "Xmesh axes"),
    (lambda rows: [row["dofs"]["coords"].pop() for row in rows], "Xmesh axes"),
    (lambda rows: rows[0]["dofs"].update(complete_xmesh_internal_dof_capture=False), "not a complete V3"),
    (lambda rows: rows[0]["u_real"].__setitem__(0, float("nan")), "must be finite"),
])
def test_persistence_rejects_time_layout_axes_incomplete_mapping_and_nonfinite(mutation, message):
    source = _frames()
    target = copy.deepcopy(source)
    mutation(target)
    with pytest.raises(MaxwellStateError, match=message):
        compare_persisted_full_xmesh_state(
            source, target,
            source_configuration_sha256=CONFIG,
            reopened_configuration_sha256=CONFIG,
        )


def test_persistence_rejects_configuration_readback_change():
    with pytest.raises(MaxwellStateError, match="configuration fingerprints differ"):
        compare_persisted_full_xmesh_state(
            _frames(), _frames(),
            source_configuration_sha256=CONFIG,
            reopened_configuration_sha256="d" * 64,
        )


def test_continuous_staged_comparison_is_diagnostic_only_and_never_passes_maxwell_state():
    source = _frames()
    staged = copy.deepcopy(source)
    staged[1]["u_real"][0] += 1e-8
    result = compare_full_xmesh_diagnostics(
        source, staged,
        source_configuration_sha256=CONFIG,
        target_configuration_sha256=CONFIG,
    )
    assert result["status"] == "FULL_XMESH_DIAGNOSTICS_ONLY_TOLERANCE_NOT_FROZEN"
    assert result["solution_double_count"] == 4
    assert result["nonzero_difference_count"] == 1
    assert result["max_abs_difference"] == pytest.approx(1e-8)
    assert "PASS" not in result["status"]
    assert result["maxwell_branch_state"] == "UNVERIFIED_NO_APPROVED_BRANCH_FIELD_TOLERANCE"
    assert result["native_acceptance"] == "NOT_RUN"


def test_continuous_staged_diagnostic_rejects_different_layout_or_physics_configuration():
    source = _frames()
    different_layout = copy.deepcopy(source)
    for row in different_layout:
        row["dofs"]["layout_sha256"] = "c" * 64
    with pytest.raises(MaxwellStateError, match="CompileEquations layouts differ"):
        compare_full_xmesh_diagnostics(
            source, different_layout,
            source_configuration_sha256=CONFIG,
            target_configuration_sha256=CONFIG,
        )
    with pytest.raises(MaxwellStateError, match="configurations differ"):
        compare_full_xmesh_diagnostics(
            source, copy.deepcopy(source),
            source_configuration_sha256=CONFIG,
            target_configuration_sha256="d" * 64,
        )
