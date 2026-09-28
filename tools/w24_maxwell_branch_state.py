"""Conservative V3 full-Xmesh state checks for W24.

These helpers authenticate layout completeness and compare saved/reopened
solution vectors. They do not infer which native field is Maxwell memory, nor
do they define a continuous-versus-staged physical tolerance.
"""
from __future__ import annotations

import hashlib
import json
import math
import struct
from collections import defaultdict
from typing import Any, Mapping, Sequence


class MaxwellStateError(ValueError):
    """A full-Xmesh capture or comparison violated its explicit contract."""


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MaxwellStateError(f"{label} must be a mapping")
    return value


def _sha256_text(value: Any, label: str) -> str:
    if (not isinstance(value, str) or len(value) != 64 or
            any(ch not in "0123456789abcdef" for ch in value)):
        raise MaxwellStateError(f"{label} must be a lowercase SHA-256")
    return value


def _finite_vector(value: Any, label: str) -> list[float]:
    if not isinstance(value, list) or not value:
        raise MaxwellStateError(f"{label} must be a nonempty native solution vector")
    out: list[float] = []
    for index, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise MaxwellStateError(f"{label}[{index}] must be numeric")
        number = float(item)
        if not math.isfinite(number):
            raise MaxwellStateError(f"{label}[{index}] must be finite")
        out.append(number)
    return out


def _v3_frame(frame: Any, label: str) -> tuple[Mapping[str, Any], list[float]]:
    row = _mapping(frame, label)
    metadata = _mapping(row.get("dofs"), f"{label}.dofs")
    if (metadata.get("snapshot_schema") != "W24-DOF-SNAPSHOT-3" or
            metadata.get("complete_xmesh_internal_dof_capture") is not True or
            metadata.get("complete_xmesh_dofs") is not True):
        raise MaxwellStateError(f"{label} is not a complete V3 full-Xmesh internal-DOF capture")
    digest = metadata.get("layout_sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise MaxwellStateError(f"{label} lacks its exact parsed Xmesh layout SHA-256")
    vector = _finite_vector(row.get("u_real"), f"{label}.u_real")
    axes = metadata.get("coordinate_axes")
    if isinstance(axes, bool) or axes not in (2, 3):
        raise MaxwellStateError(f"{label} has an unsupported coordinate-axis count")
    coordinates = metadata.get("coords")
    geom_nums = metadata.get("geomNums")
    nodes = metadata.get("nodes")
    name_inds = metadata.get("nameInds")
    vector_inds = metadata.get("solVectorInds")
    names = metadata.get("dofNames")
    field_names = metadata.get("fieldNames")
    field_ndofs = metadata.get("fieldNDofs")
    dof_count = metadata.get("xmesh_n_dofs")
    if (not isinstance(coordinates, list) or len(coordinates) != axes or
            not isinstance(dof_count, int) or isinstance(dof_count, bool) or dof_count <= 0 or
            any(not isinstance(axis, list) or len(axis) != dof_count for axis in coordinates) or
            any(not isinstance(values, list) or len(values) != dof_count
                for values in (geom_nums, nodes, name_inds, vector_inds)) or
            not isinstance(names, list) or not names or
            not all(isinstance(name, str) and name for name in names) or
            not isinstance(field_names, list) or not field_names or
            not all(isinstance(name, str) and name for name in field_names) or
            not isinstance(field_ndofs, list) or len(field_ndofs) != len(field_names) or
            any(isinstance(count, bool) or not isinstance(count, int) or count < 0
                for count in field_ndofs) or sum(field_ndofs) != dof_count):
        raise MaxwellStateError(f"{label} Xmesh axes, field counts, or DOF mapping arrays are inconsistent")
    for index in range(dof_count):
        for values, field in ((geom_nums, "geomNums"), (nodes, "nodes"),
                              (name_inds, "nameInds"), (vector_inds, "solVectorInds")):
            if isinstance(values[index], bool) or not isinstance(values[index], int):
                raise MaxwellStateError(f"{label}.{field}[{index}] must be an integer")
        if not 0 <= name_inds[index] < len(names):
            raise MaxwellStateError(f"{label}.nameInds[{index}] is outside the exact dofNames array")
    for axis_index, axis in enumerate(coordinates):
        for dof_index, coordinate in enumerate(axis):
            if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)) or not math.isfinite(float(coordinate)):
                raise MaxwellStateError(f"{label}.coords[{axis_index}][{dof_index}] must be finite")
    vector_summary = _mapping(metadata.get("mapping_summary"), f"{label}.mapping_summary")
    if vector_summary.get("vector_length") != len(vector):
        raise MaxwellStateError(f"{label} solution-vector length differs from its Xmesh mapping summary")
    valid_vector_indices = [index for index in vector_inds
                            if 0 <= index < len(vector)]
    unique_vector_indices = set(valid_vector_indices)
    duplicates = len(valid_vector_indices) - len(unique_vector_indices)
    if (any(index < 0 or index >= len(vector) for index in vector_inds) or
            vector_summary.get("mapped_dof_rows") != len(valid_vector_indices) or
            vector_summary.get("duplicate_solution_vector_index_rows") != duplicates or
            vector_summary.get("unique_mapped_vector_indices") != len(unique_vector_indices) or
            vector_summary.get("unrepresented_solution_vector_indices") != len(vector) - len(unique_vector_indices) or
            vector_summary.get("unmapped_dof_rows") != 0 or
            vector_summary.get("invalid_solution_indices") != 0 or
            vector_summary.get("out_of_range_solution_indices") != 0):
        raise MaxwellStateError(f"{label} solution-vector mapping summary differs from its original Xmesh indices")
    if (vector_summary.get("full_vector_and_internal_dof_map_covered") is not True or
            vector_summary.get("unmapped_dof_rows") != 0 or
            vector_summary.get("invalid_solution_indices") != 0 or
            vector_summary.get("out_of_range_solution_indices") != 0 or
            vector_summary.get("unrepresented_solution_vector_indices") != 0 or
            metadata.get("invalid_element_dof_references") != 0 or
            metadata.get("xmesh_n_dofs") != len(metadata.get("geomNums", [])) or
            metadata.get("element_local_map_group_count", 0) <= 0 or
            metadata.get("element_local_map_entries", 0) <= 0):
        raise MaxwellStateError(f"{label} has unmapped or invalid Xmesh/solution-vector entries")
    return metadata, vector


def v2_physics_configuration_sha256(readback: Any) -> str:
    """Bind full-state comparisons to the validated cure/Activation/Maxwell settings."""
    row = _mapping(readback, "W24 v2 configuration readback")
    keys = ("relative_exposure_dose", "spatial_uv_readback", "activation_readback",
            "viscoelastic_readback")
    selected = {key: dict(_mapping(row.get(key), key)) for key in keys}
    canonical = json.dumps(selected, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _frames(frames: Any, label: str) -> list[tuple[Mapping[str, Any], Mapping[str, Any], list[float]]]:
    if not isinstance(frames, Sequence) or isinstance(frames, (str, bytes)) or not frames:
        raise MaxwellStateError(f"{label} must contain at least one authenticated native frame")
    result: list[tuple[Mapping[str, Any], Mapping[str, Any], list[float]]] = []
    prior_time: float | None = None
    for index, frame in enumerate(frames):
        metadata, vector = _v3_frame(frame, f"{label}[{index}]")
        raw_time = frame.get("time_s")
        if isinstance(raw_time, bool) or not isinstance(raw_time, (int, float)):
            raise MaxwellStateError(f"{label}[{index}].time_s must be numeric")
        time_s = float(raw_time)
        if not math.isfinite(time_s) or (prior_time is not None and time_s <= prior_time):
            raise MaxwellStateError(f"{label} stored times must be finite and strictly increasing")
        if prior_time is not None and metadata.get("layout_sha256") != result[0][1].get("layout_sha256"):
            raise MaxwellStateError(f"{label} Xmesh layout changed between stored times")
        result.append((frame, metadata, vector))
        prior_time = time_s
    return result


def compare_persisted_full_xmesh_state(
    source_frames: Any,
    reopened_frames: Any,
    *,
    source_configuration_sha256: str,
    reopened_configuration_sha256: str,
) -> dict[str, Any]:
    """Require exact saved-MPH versus unchanged reopen state for every Sol double."""
    source = _frames(source_frames, "source_frames")
    reopened = _frames(reopened_frames, "reopened_frames")
    if len(source) != len(reopened):
        raise MaxwellStateError("saved and reopened captures have different stored-time counts")
    source_configuration_sha256 = _sha256_text(source_configuration_sha256, "source configuration fingerprint")
    reopened_configuration_sha256 = _sha256_text(reopened_configuration_sha256, "reopened configuration fingerprint")
    if source_configuration_sha256 != reopened_configuration_sha256:
        raise MaxwellStateError("saved and reopened cure/Activation/Maxwell configuration fingerprints differ")
    if source[0][1].get("layout_sha256") != reopened[0][1].get("layout_sha256"):
        raise MaxwellStateError("saved and reopened CompileEquations Xmesh layout hashes differ")
    exact_values = 0
    for index, ((left_frame, left_meta, left), (right_frame, right_meta, right)) in enumerate(zip(source, reopened)):
        if struct.pack(">d", float(left_frame["time_s"])) != struct.pack(">d", float(right_frame["time_s"])):
            raise MaxwellStateError(f"saved and reopened stored time differs at frame {index}")
        if left_meta.get("layout_sha256") != right_meta.get("layout_sha256") or len(left) != len(right):
            raise MaxwellStateError(f"saved and reopened Xmesh/vector layout differs at frame {index}")
        for dof_index, (left_value, right_value) in enumerate(zip(left, right)):
            if struct.pack(">d", left_value) != struct.pack(">d", right_value):
                raise MaxwellStateError(
                    f"saved and reopened solution double differs at time {left_frame['time_s']!r}, index {dof_index}"
                )
            exact_values += 1
    return {
        "status": "PERSISTED_FULL_XMESH_STATE_EXACT_MATCH",
        "stored_time_count": len(source),
        "solution_double_count": exact_values,
        "layout_sha256": source[0][1]["layout_sha256"],
        "configuration_sha256": source_configuration_sha256,
        "comparison": "bit-exact finite Sol vector values at identical stored times",
        "maxwell_branch_field_identity": "UNVERIFIED_NOT_INFERRED_FROM_FIELD_NAME",
        "maxwell_reference_state_semantics": "UNVERIFIED_NATIVE_OBSERVATION_REQUIRED",
        "native_acceptance": "NOT_RUN",
    }


def compare_full_xmesh_diagnostics(
    source_frames: Any,
    target_frames: Any,
    *,
    source_configuration_sha256: str,
    target_configuration_sha256: str,
) -> dict[str, Any]:
    """Report continuous/staged all-vector differences without applying a tolerance."""
    source = _frames(source_frames, "source_frames")
    target = _frames(target_frames, "target_frames")
    if len(source) != len(target):
        raise MaxwellStateError("full-Xmesh diagnostic schedules have different frame counts")
    source_configuration_sha256 = _sha256_text(source_configuration_sha256, "source configuration fingerprint")
    target_configuration_sha256 = _sha256_text(target_configuration_sha256, "target configuration fingerprint")
    if source_configuration_sha256 != target_configuration_sha256:
        raise MaxwellStateError("full-Xmesh diagnostic cure/Activation/Maxwell configurations differ")
    if source[0][1].get("layout_sha256") != target[0][1].get("layout_sha256"):
        raise MaxwellStateError("full-Xmesh diagnostic CompileEquations layouts differ")
    maximum = 0.0
    nonzero = 0
    by_name: dict[str, float] = defaultdict(float)
    compared_doubles = 0
    first_meta = source[0][1]
    indices = first_meta.get("nameInds")
    names = first_meta.get("dofNames")
    vector_indices = first_meta.get("solVectorInds")
    names_by_vector_index: dict[int, set[str]] = defaultdict(set)
    if isinstance(indices, list) and isinstance(names, list) and isinstance(vector_indices, list):
        for row, vector_index in enumerate(vector_indices):
            if (row < len(indices) and isinstance(vector_index, int) and
                    isinstance(indices[row], int) and 0 <= indices[row] < len(names) and
                    isinstance(names[indices[row]], str)):
                names_by_vector_index[vector_index].add(names[indices[row]])
    for frame_index, ((left_frame, left_meta, left), (right_frame, right_meta, right)) in enumerate(zip(source, target)):
        if struct.pack(">d", float(left_frame["time_s"])) != struct.pack(">d", float(right_frame["time_s"])):
            raise MaxwellStateError(f"full-Xmesh diagnostic frames do not share exact time at index {frame_index}")
        if (left_meta.get("layout_sha256") != right_meta.get("layout_sha256") or len(left) != len(right)):
            raise MaxwellStateError(f"full-Xmesh diagnostic layout changed at frame {frame_index}")
        for dof_index, (a, b) in enumerate(zip(left, right)):
            delta = abs(a - b)
            maximum = max(maximum, delta)
            if delta != 0.0:
                nonzero += 1
            compared_doubles += 1
            row_names = names_by_vector_index.get(dof_index, set())
            name = (next(iter(row_names)) if len(row_names) == 1 else
                    "ALIASED_FIELD_NAMES:" + "|".join(sorted(row_names)) if row_names else
                    "UNMAPPED_FIELD_NAME")
            by_name[name] = max(by_name[name], delta)
    return {
        "status": "FULL_XMESH_DIAGNOSTICS_ONLY_TOLERANCE_NOT_FROZEN",
        "stored_time_count": len(source),
        "solution_double_count": compared_doubles,
        "nonzero_difference_count": nonzero,
        "max_abs_difference": maximum,
        "max_abs_difference_by_native_dof_name": dict(sorted(by_name.items())),
        "layout_sha256": source[0][1]["layout_sha256"],
        "configuration_sha256": source_configuration_sha256,
        "configuration_sha256_matches": source_configuration_sha256 == target_configuration_sha256,
        "maxwell_branch_state": "UNVERIFIED_NO_APPROVED_BRANCH_FIELD_TOLERANCE",
        "native_acceptance": "NOT_RUN",
    }
