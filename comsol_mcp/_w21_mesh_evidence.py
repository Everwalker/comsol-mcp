"""Bounded, read-only snapshots of a *current* COMSOL MeshSequence.

The snapshot records all current vertex coordinates, element connectivity,
and geometric-entity assignments through COMSOL's block getters.  It is not a
historical-solution mesh proof, a source/target admission, or an atomic engine
snapshot.  Callers must serialize access to the managed model; before/after
counts only detect visible count/type drift.

COMSOL 6.4 API evidence:
* MeshSequence, doc 7637/chunk 22946, SHA-256
  0f46188b7694be0f1b7d188790218075aa6c831b58080f1d27ae04f3a141df23.
* COMSOL Programming Reference Manual, p. 447, doc 3626/chunk 11202, SHA-256
  5b7f23ad2eae77f59d71935f4f6c9b6b9ede380dc34da065181841e512bbc105.
* Accessing Mesh Data, doc 4332/chunk 17080, SHA-256
  61381c7186fee5f54a11364f5717b2dd4869872bdede6b7f24f8802ad9ed8cfc.
* MeshData's connectivity example explicitly uses 0-based vertex indices:
  doc 11794/chunk 34341, SHA-256
  63375d474ee2eacf9bf1e574f8c9b601f043fde6e1e32632a1b9f4817bf7149a.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
from collections.abc import Mapping, Sequence
from typing import Any

from ._execution_contract import model_ref_from_mapping
from ._g3_results import W21_FIELD_READBACK_MAX_NUMERIC_SCALARS


SNAPSHOT_SCHEMA = "w21.current-mesh-snapshot/v1"
COMPARISON_SCHEMA = "w21.current-mesh-content-comparison/v1"
CURRENT_MESH_CAPTURE_ONLY = "CURRENT_MESH_CAPTURE_ONLY"
MAX_BLOCK_COLUMNS = 1024
MAX_NUMERIC_SCALARS = W21_FIELD_READBACK_MAX_NUMERIC_SCALARS
_READ_METHODS = (
    "getSDim",
    "getNumVertex",
    "getTypes",
    "getNumElem",
    "getVertex",
    "getElem",
    "getElemEntity",
)
_BINDING_FIELDS = {
    "project_id",
    "model_ref",
    "revision",
    "attempt_id",
    "phase",
    "component",
    "mesh",
    "geometry",
}
_ELEMENT_NODE_COUNTS = {
    "vtx": 1,
    "edg": 2,
    "tri": 3,
    "quad": 4,
    "tet": 4,
    "pyr": 5,
    "prism": 6,
    "hex": 8,
}


class MeshEvidenceError(ValueError):
    """A fail-closed error while capturing/comparing mesh evidence."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise MeshEvidenceError(code, message)


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise MeshEvidenceError("MESH_EVIDENCE_NOT_CANONICAL", "mesh evidence is not finite JSON data") from exc


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_sha256(value: Any) -> str:
    return _sha256(_canonical_bytes(value))


def _is_sha256(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(char in "0123456789abcdef" for char in value))


def _normalize_binding(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not _BINDING_FIELDS.issubset(value):
        _fail("MESH_BINDING_INCOMPLETE", "mesh observation requires the complete project/model/revision/attempt/component binding")
    project_id = value.get("project_id")
    if not isinstance(project_id, str) or not project_id:
        _fail("MESH_BINDING_INVALID", "project identity is malformed")
    raw_ref = value.get("model_ref")
    if not isinstance(raw_ref, Mapping):
        _fail("MESH_BINDING_INVALID", "full ModelRef is required")
    try:
        model_ref = model_ref_from_mapping(raw_ref).as_dict()
    except Exception as exc:
        raise MeshEvidenceError("MESH_BINDING_INVALID", "full ModelRef is invalid") from exc
    if dict(raw_ref) != model_ref:
        _fail("MESH_BINDING_INVALID", "ModelRef must include its complete canonical identity")
    revision = value.get("revision")
    if type(revision) is not int or revision < 0:
        _fail("MESH_BINDING_INVALID", "model revision must be a non-negative integer")
    for key in ("attempt_id", "phase", "component", "mesh", "geometry"):
        item = value.get(key)
        if not isinstance(item, str) or not item or not item.strip():
            _fail("MESH_BINDING_INVALID", f"{key} must be an explicit non-empty string")
    # Extra binding fields are retained only when they are finite JSON values.
    normalized = dict(value)
    normalized["model_ref"] = model_ref
    _canonical_bytes(normalized)
    return normalized


def _read(receiver: Any, method: str, *args: Any) -> Any:
    if method not in _READ_METHODS:
        _fail("MESH_READ_METHOD_REFUSED", "only documented read-only MeshSequence methods are allowed")
    try:
        function = getattr(receiver, method)
    except AttributeError as exc:
        raise MeshEvidenceError("MESH_API_UNAVAILABLE", f"read-only MeshSequence.{method} is unavailable") from exc
    if not callable(function):
        _fail("MESH_API_UNAVAILABLE", f"read-only MeshSequence.{method} is unavailable")
    # Let execution-state/timeout/transport exceptions escape unchanged. A
    # caller must not mistake an ambiguous Worker reply for a retryable shape
    # error or continue with another Worker read.
    return function(*args)


def _nonnegative_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        _fail("MESH_METADATA_INVALID", f"{label} must be a non-negative integer")
    return value


def _header(receiver: Any) -> dict[str, Any]:
    space_dimension = _read(receiver, "getSDim")
    if type(space_dimension) is not int or not 1 <= space_dimension <= 3:
        _fail("MESH_METADATA_INVALID", "getSDim must return an integer from 1 through 3")
    vertex_count = _nonnegative_int(_read(receiver, "getNumVertex"), "getNumVertex")
    raw_types = _read(receiver, "getTypes")
    if (not isinstance(raw_types, Sequence) or isinstance(raw_types, (str, bytes))
            or any(not isinstance(item, str) or not item for item in raw_types)):
        _fail("MESH_TYPES_INVALID", "getTypes must return a sequence of non-empty type names")
    if len(set(raw_types)) != len(raw_types):
        _fail("MESH_TYPES_INVALID", "getTypes returned a duplicate element type")
    types = sorted(raw_types)
    counts = {element_type: _nonnegative_int(
        _read(receiver, "getNumElem", element_type), f"getNumElem({element_type})"
    ) for element_type in types}
    return {"space_dimension": space_dimension, "vertex_count": vertex_count,
            "types": types, "element_counts": counts}


def _matrix(value: Any, *, rows: int | None, columns: int, label: str) -> list[list[Any]]:
    if (not isinstance(value, Sequence) or isinstance(value, (str, bytes))
            or (rows is not None and len(value) != rows)):
        _fail("MESH_ARRAY_SHAPE_INVALID", f"{label} returned an invalid row count")
    result: list[list[Any]] = []
    for row in value:
        if (not isinstance(row, Sequence) or isinstance(row, (str, bytes))
                or len(row) != columns):
            _fail("MESH_ARRAY_SHAPE_INVALID", f"{label} returned an invalid block width")
        result.append(list(row))
    if not result:
        _fail("MESH_ARRAY_SHAPE_INVALID", f"{label} returned an empty matrix for a non-empty block")
    return result


def _finite_number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("MESH_NUMERIC_INVALID", "mesh coordinates must be finite numbers")
    try:
        number = float(value)
    except (OverflowError, ValueError):
        _fail("MESH_NUMERIC_INVALID", "mesh coordinates must be finite numbers")
    if not math.isfinite(number):
        _fail("MESH_NUMERIC_INVALID", "mesh coordinates must be finite numbers")
    return number


def _vertex_block(receiver: Any, position: int, count: int, dimension: int) -> list[list[float]]:
    rows = _matrix(_read(receiver, "getVertex", position, count),
                   rows=dimension, columns=count, label="getVertex")
    return [[_finite_number(item) for item in row] for row in rows]


def _connectivity_block(
    receiver: Any, element_type: str, position: int, count: int,
    *, vertex_count: int, expected_arity: int | None,
) -> list[list[int]]:
    raw = _read(receiver, "getElem", element_type, position, count)
    rows = _matrix(raw, rows=expected_arity, columns=count, label=f"getElem({element_type})")
    normalized: list[list[int]] = []
    for row in rows:
        values: list[int] = []
        for value in row:
            if type(value) is not int or not 0 <= value < vertex_count:
                _fail("MESH_CONNECTIVITY_INVALID", "getElem returned a non-integer or out-of-range 0-based vertex index")
            values.append(value)
        normalized.append(values)
    return normalized


def _entity_block(receiver: Any, element_type: str, position: int, count: int) -> list[int]:
    raw = _read(receiver, "getElemEntity", element_type, position, count)
    if (not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or len(raw) != count
            or any(type(item) is not int or item < 0 for item in raw)):
        _fail("MESH_ENTITY_ASSIGNMENT_INVALID", f"getElemEntity({element_type}) returned invalid entity assignments")
    return list(raw)


def _begin_hash(domain: bytes, *values: int | str) -> Any:
    digest = hashlib.sha256()
    digest.update(domain + b"\x00")
    for value in values:
        if isinstance(value, str):
            raw = value.encode("utf-8")
            digest.update(struct.pack(">Q", len(raw)))
            digest.update(raw)
        else:
            digest.update(struct.pack(">Q", value))
    return digest


def _updated_element_hashes(
    connectivity_hash: Any,
    entity_hash: Any,
    connectivity: list[list[int]],
    entities: list[int],
) -> None:
    arity = len(connectivity)
    for column in range(len(entities)):
        for row in range(arity):
            connectivity_hash.update(struct.pack(">Q", connectivity[row][column]))
        entity_hash.update(struct.pack(">q", entities[column]))


def _content_digest(content: Mapping[str, Any]) -> str:
    return _json_sha256(dict(content))


def _evidence_digest(
    status: str,
    claim_scope: str,
    binding_sha256: str,
    content_sha256: str,
    numeric_scalar_cap: int,
    numeric_scalars_read: int,
) -> str:
    capture_semantics = {
        "read_methods": list(_READ_METHODS),
        "pre_post_counts_equal": True,
        "atomic_snapshot": False,
        "same_count_content_drift_can_escape": True,
        "historical_mesh": "UNVERIFIED",
        "source_target_mapping": "UNVERIFIED",
    }
    return _json_sha256({
        "schema": SNAPSHOT_SCHEMA,
        "status": status,
        "claim_scope": claim_scope,
        "binding_sha256": binding_sha256,
        "content_sha256": content_sha256,
        "capture_semantics": capture_semantics,
        "numeric_scalar_cap": numeric_scalar_cap,
        "numeric_scalars_read": numeric_scalars_read,
    })


def capture_current_mesh(
    mesh_sequence: Any,
    *,
    binding: Mapping[str, Any],
    block_size: int = MAX_BLOCK_COLUMNS,
    max_numeric_scalars: int = MAX_NUMERIC_SCALARS,
) -> dict[str, Any]:
    """Stream one bounded current mesh snapshot using only documented getters.

    ``max_numeric_scalars`` can lower, but never raise, the existing W21
    65,536-value limit. It counts coordinates, connectivity indices, and
    geometric-entity assignments together. Oversized meshes fail closed; no
    truncated snapshot is returned.
    """
    normalized_binding = _normalize_binding(binding)
    if type(block_size) is not int or not 1 <= block_size <= MAX_BLOCK_COLUMNS:
        _fail("MESH_BLOCK_SIZE_INVALID", f"block_size must be from 1 through {MAX_BLOCK_COLUMNS}")
    if (type(max_numeric_scalars) is not int or not 1 <= max_numeric_scalars
            <= MAX_NUMERIC_SCALARS):
        _fail("MESH_SCALAR_CAP_INVALID", f"max_numeric_scalars must be from 1 through {MAX_NUMERIC_SCALARS}")

    try:
        before = _header(mesh_sequence)
        dimension = before["space_dimension"]
        vertex_count = before["vertex_count"]
        counts = before["element_counts"]
        lower_bound = dimension * vertex_count + sum(count * 2 for count in counts.values())
        if lower_bound > max_numeric_scalars:
            _fail("MESH_SCALAR_CAP_EXCEEDED", "mesh scalar count exceeds the explicit cap")
        unknown_types = [element_type for element_type in before["types"]
                         if element_type not in _ELEMENT_NODE_COUNTS]
        if unknown_types:
            _fail("MESH_ELEMENT_TYPE_UNSUPPORTED", "mesh has an element type with no documented corner arity")
        arities = {element_type: _ELEMENT_NODE_COUNTS[element_type]
                   for element_type in before["types"]}
        exact_scalar_count = dimension * vertex_count + sum(
            count * (arities[element_type] + 1)
            for element_type, count in counts.items()
        )
        if exact_scalar_count > max_numeric_scalars:
            _fail("MESH_SCALAR_CAP_EXCEEDED", "mesh scalar count exceeds the explicit cap")

        coordinate_hash = _begin_hash(
            b"w21-mesh-vertex-coordinates-v1", dimension, vertex_count
        )
        read_scalars = 0
        vertex_offset = 0
        while vertex_offset < vertex_count:
            count = min(block_size, vertex_count - vertex_offset)
            block = _vertex_block(mesh_sequence, vertex_offset, count, dimension)
            scalars = dimension * count
            if read_scalars + scalars > max_numeric_scalars:
                _fail("MESH_SCALAR_CAP_EXCEEDED", "mesh scalar read would exceed the explicit cap")
            for column in range(count):
                for row in range(dimension):
                    coordinate_hash.update(struct.pack(">d", block[row][column]))
            read_scalars += scalars
            vertex_offset += count

        type_summaries: list[dict[str, Any]] = []
        for element_type in before["types"]:
            count = counts[element_type]
            arity = arities[element_type]
            connectivity_hash = _begin_hash(
                b"w21-mesh-connectivity-v1", element_type, count, arity
            )
            entity_hash = _begin_hash(
                b"w21-mesh-entity-assignments-v1", element_type, count
            )
            offset = 0
            while offset < count:
                block_count = min(block_size, count - offset)
                scalars = block_count * (arity + 1)
                if read_scalars + scalars > max_numeric_scalars:
                    _fail("MESH_SCALAR_CAP_EXCEEDED", "mesh scalar read would exceed the explicit cap")
                connectivity = _connectivity_block(
                    mesh_sequence, element_type, offset, block_count,
                    vertex_count=vertex_count, expected_arity=arity,
                )
                entities = _entity_block(mesh_sequence, element_type, offset, block_count)
                _updated_element_hashes(connectivity_hash, entity_hash, connectivity, entities)
                read_scalars += scalars
                offset += block_count
            type_summaries.append({
                "type": element_type,
                "element_count": count,
                "nodes_per_element": arity,
                "connectivity_sha256": connectivity_hash.hexdigest(),
                "entity_assignments_sha256": entity_hash.hexdigest(),
            })

        if read_scalars != exact_scalar_count:
            _fail("MESH_SCALAR_ACCOUNTING_INVALID", "mesh scalar accounting did not match the full read")
        after = _header(mesh_sequence)
        if before != after:
            _fail("MESH_CHANGED_DURING_CAPTURE", "mesh type or count metadata changed during capture")

        content = {
            "space_dimension": dimension,
            "vertex_count": vertex_count,
            "vertex_coordinates_sha256": coordinate_hash.hexdigest(),
            "types": type_summaries,
            "element_count": sum(counts.values()),
            "numeric_scalar_count": exact_scalar_count,
        }
        status = CURRENT_MESH_CAPTURE_ONLY
        claim_scope = "current mesh content only; historical/source-target association, frame, DOF and solver history are unverified"
        binding_sha256 = _json_sha256(normalized_binding)
        content_sha256 = _content_digest(content)
        return {
            "schema": SNAPSHOT_SCHEMA,
            "status": status,
            "claim_scope": claim_scope,
            "binding": normalized_binding,
            "binding_sha256": binding_sha256,
            "content": content,
            "content_sha256": content_sha256,
            "evidence_sha256": _evidence_digest(
                status, claim_scope, binding_sha256, content_sha256,
                max_numeric_scalars, read_scalars,
            ),
            "capture": {
                "block_size": block_size,
                "numeric_scalar_cap": max_numeric_scalars,
                "numeric_scalars_read": read_scalars,
                "read_methods": list(_READ_METHODS),
                "pre_post_counts_equal": True,
                "atomic_snapshot": False,
                "same_count_content_drift_can_escape": True,
                "historical_mesh": "UNVERIFIED",
                "source_target_mapping": "UNVERIFIED",
            },
        }
    except MeshEvidenceError:
        raise
    except Exception:
        # Worker transport/UNKNOWN/timeouts must retain their original type and
        # status.  In particular, never turn an ambiguous read into an ordinary
        # mesh validation error that a caller might retry.
        raise


def _validated_snapshot(value: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(value, Mapping) or value.get("schema") != SNAPSHOT_SCHEMA:
        _fail("MESH_SNAPSHOT_INVALID", "comparison requires a current-mesh snapshot from this schema")
    if set(value) != {
        "schema", "status", "claim_scope", "binding", "binding_sha256", "content",
        "content_sha256", "evidence_sha256", "capture",
    }:
        _fail("MESH_SNAPSHOT_INVALID", "snapshot fields are incomplete or unexpected")
    if value.get("status") != CURRENT_MESH_CAPTURE_ONLY:
        _fail("MESH_SNAPSHOT_INVALID", "snapshot status is not current-mesh capture only")
    binding = _normalize_binding(value.get("binding"))
    binding_sha256 = _json_sha256(binding)
    if value.get("binding_sha256") != binding_sha256:
        _fail("MESH_SNAPSHOT_INVALID", "snapshot binding digest does not match")
    content = value.get("content")
    if not isinstance(content, Mapping):
        _fail("MESH_SNAPSHOT_INVALID", "snapshot content is missing")
    content_dict = dict(content)
    if set(content_dict) != {
        "space_dimension", "vertex_count", "vertex_coordinates_sha256", "types",
        "element_count", "numeric_scalar_count",
    }:
        _fail("MESH_SNAPSHOT_INVALID", "snapshot content fields are incomplete or unexpected")
    dimension, vertex_count = content_dict.get("space_dimension"), content_dict.get("vertex_count")
    element_count, scalar_count = content_dict.get("element_count"), content_dict.get("numeric_scalar_count")
    if (type(dimension) is not int or not 1 <= dimension <= 3
            or type(vertex_count) is not int or vertex_count < 0
            or type(element_count) is not int or element_count < 0
            or type(scalar_count) is not int or scalar_count < 0
            or not _is_sha256(content_dict.get("vertex_coordinates_sha256"))):
        _fail("MESH_SNAPSHOT_INVALID", "snapshot counts or coordinate hash are malformed")
    types = content_dict.get("types")
    if not isinstance(types, list):
        _fail("MESH_SNAPSHOT_INVALID", "snapshot element type summaries are missing")
    names: list[str] = []
    expected_elements = 0
    expected_scalars = dimension * vertex_count
    for item in types:
        if not isinstance(item, Mapping) or set(item) != {
            "type", "element_count", "nodes_per_element", "connectivity_sha256",
            "entity_assignments_sha256",
        }:
            _fail("MESH_SNAPSHOT_INVALID", "snapshot element type summary is malformed")
        element_type = item.get("type")
        count = item.get("element_count")
        arity = item.get("nodes_per_element")
        if (not isinstance(element_type, str) or element_type not in _ELEMENT_NODE_COUNTS
                or type(count) is not int or count < 0
                or arity != _ELEMENT_NODE_COUNTS[element_type]
                or type(arity) is not int
                or not _is_sha256(item.get("connectivity_sha256"))
                or not _is_sha256(item.get("entity_assignments_sha256"))):
            _fail("MESH_SNAPSHOT_INVALID", "snapshot element type values are malformed")
        names.append(element_type)
        expected_elements += count
        expected_scalars += count * (arity + 1)
    if (names != sorted(names) or len(names) != len(set(names))
            or element_count != expected_elements or scalar_count != expected_scalars):
        _fail("MESH_SNAPSHOT_INVALID", "snapshot type order or total counts are inconsistent")
    content_sha256 = _content_digest(content_dict)
    if value.get("content_sha256") != content_sha256:
        _fail("MESH_SNAPSHOT_INVALID", "snapshot content digest does not match")
    if value.get("claim_scope") != (
        "current mesh content only; historical/source-target association, frame, DOF and solver history are unverified"
    ):
        _fail("MESH_SNAPSHOT_INVALID", "snapshot claim scope is not the required limited scope")
    capture = value.get("capture")
    expected_capture = {
        "read_methods": list(_READ_METHODS),
        "pre_post_counts_equal": True,
        "atomic_snapshot": False,
        "same_count_content_drift_can_escape": True,
        "historical_mesh": "UNVERIFIED",
        "source_target_mapping": "UNVERIFIED",
    }
    if not isinstance(capture, Mapping):
        _fail("MESH_SNAPSHOT_INVALID", "snapshot capture metadata is missing")
    if set(capture) != set(expected_capture) | {
        "block_size", "numeric_scalar_cap", "numeric_scalars_read",
    }:
        _fail("MESH_SNAPSHOT_INVALID", "snapshot capture metadata is incomplete or unexpected")
    for key, expected in expected_capture.items():
        if capture.get(key) != expected:
            _fail("MESH_SNAPSHOT_INVALID", "snapshot capture semantics were changed")
    cap = capture.get("numeric_scalar_cap")
    read = capture.get("numeric_scalars_read")
    block = capture.get("block_size")
    if (type(cap) is not int or not 1 <= cap <= MAX_NUMERIC_SCALARS
            or type(read) is not int or read != scalar_count or read > cap
            or type(block) is not int or not 1 <= block <= MAX_BLOCK_COLUMNS):
        _fail("MESH_SNAPSHOT_INVALID", "snapshot capture budget metadata is malformed")
    if value.get("evidence_sha256") != _evidence_digest(
        CURRENT_MESH_CAPTURE_ONLY, value["claim_scope"], binding_sha256, content_sha256,
        cap, read,
    ):
        _fail("MESH_SNAPSHOT_INVALID", "snapshot evidence digest does not match")
    return binding, content_dict


def compare_current_mesh_snapshots(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    """Compare two exact-bound current snapshots without promoting history.

    Attempt/revision/phase and mesh tag may differ; both observations must be
    bound to the same project, complete ModelRef, component, and geometry.
    """
    left_binding, left_content = _validated_snapshot(left)
    right_binding, right_content = _validated_snapshot(right)
    for key in ("project_id", "model_ref", "component", "geometry"):
        if left_binding.get(key) != right_binding.get(key):
            _fail("MESH_COMPARISON_BINDING_MISMATCH", f"snapshots have different {key} bindings")

    differences: list[str] = []
    for key in ("space_dimension", "vertex_count", "element_count"):
        if left_content.get(key) != right_content.get(key):
            differences.append(key)
    if left_content.get("vertex_coordinates_sha256") != right_content.get("vertex_coordinates_sha256"):
        differences.append("vertex_coordinates_or_order")
    left_types = left_content.get("types")
    right_types = right_content.get("types")
    if not isinstance(left_types, list) or not isinstance(right_types, list):
        _fail("MESH_SNAPSHOT_INVALID", "snapshot element-type summaries are malformed")
    left_by_type = {item.get("type"): item for item in left_types if isinstance(item, Mapping)}
    right_by_type = {item.get("type"): item for item in right_types if isinstance(item, Mapping)}
    if len(left_by_type) != len(left_types) or len(right_by_type) != len(right_types):
        _fail("MESH_SNAPSHOT_INVALID", "snapshot element-type summaries are malformed")
    if set(left_by_type) != set(right_by_type):
        differences.append("element_types")
    for element_type in sorted(set(left_by_type) & set(right_by_type)):
        left_item, right_item = left_by_type[element_type], right_by_type[element_type]
        if left_item.get("element_count") != right_item.get("element_count"):
            differences.append(f"element_count:{element_type}")
        if left_item.get("nodes_per_element") != right_item.get("nodes_per_element"):
            differences.append(f"connectivity_arity:{element_type}")
        if left_item.get("connectivity_sha256") != right_item.get("connectivity_sha256"):
            differences.append(f"connectivity_or_element_order:{element_type}")
        if left_item.get("entity_assignments_sha256") != right_item.get("entity_assignments_sha256"):
            differences.append(f"entity_assignments_or_order:{element_type}")
    same_content = left["content_sha256"] == right["content_sha256"]
    if same_content and differences:
        _fail("MESH_COMPARISON_INVALID", "equal content hashes conflict with detailed component comparison")
    if not same_content and not differences:
        differences.append("content_hash")
    return {
        "schema": COMPARISON_SCHEMA,
        "status": "MATCH_CONTENT_ONLY" if same_content else "CONTENT_DIFFERENT",
        "claim_scope": "current-mesh content comparison only; no historical association or stage admission is established",
        "same_content": same_content,
        "same_mesh_tag": left_binding.get("mesh") == right_binding.get("mesh"),
        "left_binding_sha256": left["binding_sha256"],
        "right_binding_sha256": right["binding_sha256"],
        "left_content_sha256": left["content_sha256"],
        "right_content_sha256": right["content_sha256"],
        "differences": differences,
        "historical_mesh": "UNVERIFIED",
        "source_target_mapping": "UNVERIFIED",
    }
