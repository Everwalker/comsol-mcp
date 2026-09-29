from __future__ import annotations

from copy import deepcopy

import pytest

from comsol_mcp._w21_mesh_evidence import (
    MAX_BLOCK_COLUMNS,
    MAX_NUMERIC_SCALARS,
    MeshEvidenceError,
    capture_current_mesh,
    compare_current_mesh_snapshots,
)


def _binding(**updates):
    value = {
        "project_id": "project-a",
        "model_ref": {
            "schema_version": 1,
            "session_id": "session-a",
            "server_instance_id": "server-a",
            "model_tag": "model1",
            "generation": 1,
        },
        "revision": 4,
        "attempt_id": "attempt-a",
        "phase": "pre-stage",
        "component": "comp1",
        "mesh": "mesh1",
        "geometry": "geom1",
    }
    value.update(updates)
    return value


class FakeMeshSequence:
    """Offline MeshSequence-shaped fixture; it exposes read methods only."""

    def __init__(self, *, vertices=None, elems=None, entities=None, types=None, drift=None):
        self.vertices = deepcopy(vertices if vertices is not None else [
            [0.0, 1.0, 1.0, 0.0],
            [0.0, 0.0, 1.0, 1.0],
        ])
        self.elems = deepcopy(elems if elems is not None else {
            "tri": [[0], [1], [2]],
            "edg": [[0, 1], [1, 2]],
        })
        self.entities = deepcopy(entities if entities is not None else {
            "tri": [3],
            "edg": [1, 2],
        })
        self.types = list(types if types is not None else ["tri", "edg"])
        self.drift = drift or {}
        self.calls = []
        self.count_calls = {}
        self.types_calls = 0

    def _call(self, name, *args):
        self.calls.append((name, *args))

    def getSDim(self):
        self._call("getSDim")
        return 2

    def getNumVertex(self):
        self._call("getNumVertex")
        values = self.drift.get("vertices")
        if values:
            index = self.count_calls.get("vertices", 0)
            self.count_calls["vertices"] = index + 1
            return values[min(index, len(values) - 1)]
        return len(self.vertices[0])

    def getTypes(self):
        self._call("getTypes")
        self.types_calls += 1
        values = self.drift.get("types")
        return list(values[min(self.types_calls - 1, len(values) - 1)]) if values else list(self.types)

    def getNumElem(self, element_type):
        self._call("getNumElem", element_type)
        key = f"count:{element_type}"
        values = self.drift.get(key)
        if values:
            index = self.count_calls.get(key, 0)
            self.count_calls[key] = index + 1
            return values[min(index, len(values) - 1)]
        return len(self.entities.get(element_type, []))

    def getVertex(self, position, number):
        self._call("getVertex", position, number)
        return [row[position:position + number] for row in self.vertices]

    def getElem(self, element_type, position, number):
        self._call("getElem", element_type, position, number)
        return [row[position:position + number] for row in self.elems[element_type]]

    def getElemEntity(self, element_type, position, number):
        self._call("getElemEntity", element_type, position, number)
        return self.entities[element_type][position:position + number]


def _snapshot(mesh=None, **kwargs):
    binding = kwargs.pop("binding", _binding())
    return capture_current_mesh(mesh or FakeMeshSequence(), binding=binding, **kwargs)


def test_snapshot_streams_all_values_and_is_blocksize_independent():
    one = _snapshot(block_size=1)
    two = _snapshot(block_size=2)
    assert one["status"] == "CURRENT_MESH_CAPTURE_ONLY"
    assert one["capture"]["atomic_snapshot"] is False
    assert one["capture"]["historical_mesh"] == "UNVERIFIED"
    assert one["content"]["numeric_scalar_count"] == 18
    assert one["content_sha256"] == two["content_sha256"]
    assert one["evidence_sha256"] == two["evidence_sha256"]
    assert one["capture"]["block_size"] != two["capture"]["block_size"]


def test_only_documented_read_methods_are_called():
    mesh = FakeMeshSequence()
    _snapshot(mesh, block_size=2)
    allowed = {"getSDim", "getNumVertex", "getTypes", "getNumElem", "getVertex", "getElem", "getElemEntity"}
    assert {call[0] for call in mesh.calls} <= allowed
    assert not any(call[0] in {"run", "create", "set", "transfer"} for call in mesh.calls)
    assert all(call[0] not in {"getVertex", "getElem", "getElemEntity"} or call[-1] <= 2 for call in mesh.calls)


def test_exact_scalar_cap_boundary_passes_and_overflow_refuses_without_snapshot():
    mesh = FakeMeshSequence()
    receipt = capture_current_mesh(mesh, binding=_binding(), block_size=2, max_numeric_scalars=18)
    assert receipt["capture"]["numeric_scalars_read"] == 18

    over = FakeMeshSequence()
    with pytest.raises(MeshEvidenceError) as exc:
        capture_current_mesh(over, binding=_binding(), block_size=2, max_numeric_scalars=17)
    assert exc.value.code == "MESH_SCALAR_CAP_EXCEEDED"
    assert not any(call[0] == "getVertex" for call in over.calls)


def test_global_scalar_cap_and_block_width_cannot_be_raised():
    mesh = FakeMeshSequence()
    with pytest.raises(MeshEvidenceError) as cap_exc:
        capture_current_mesh(mesh, binding=_binding(), max_numeric_scalars=MAX_NUMERIC_SCALARS + 1)
    assert cap_exc.value.code == "MESH_SCALAR_CAP_INVALID"
    assert mesh.calls == []

    with pytest.raises(MeshEvidenceError) as block_exc:
        capture_current_mesh(mesh, binding=_binding(), block_size=MAX_BLOCK_COLUMNS + 1)
    assert block_exc.value.code == "MESH_BLOCK_SIZE_INVALID"
    assert mesh.calls == []


def test_scalar_cap_preflight_refuses_before_array_reads():
    mesh = FakeMeshSequence(vertices=[[0.0] * 40_000, [0.0] * 40_000], elems={}, entities={}, types=[])
    with pytest.raises(MeshEvidenceError) as exc:
        capture_current_mesh(mesh, binding=_binding(), max_numeric_scalars=MAX_NUMERIC_SCALARS)
    assert exc.value.code == "MESH_SCALAR_CAP_EXCEEDED"
    assert not any(call[0] == "getVertex" for call in mesh.calls)
    assert not any(call[0] == "getElem" for call in mesh.calls)


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_coordinates_fail_closed(bad_value):
    mesh = FakeMeshSequence(vertices=[[0.0, bad_value, 1.0, 0.0], [0.0, 0.0, 1.0, 1.0]])
    with pytest.raises(MeshEvidenceError) as exc:
        _snapshot(mesh)
    assert exc.value.code == "MESH_NUMERIC_INVALID"


def test_bad_coordinate_shape_and_connectivity_shape_fail_closed():
    mesh = FakeMeshSequence(vertices=[[0.0, 1.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    with pytest.raises(MeshEvidenceError) as exc:
        _snapshot(mesh)
    assert exc.value.code == "MESH_ARRAY_SHAPE_INVALID"

    mesh = FakeMeshSequence(elems={"tri": [[0, 1], [1, 2]], "edg": [[0, 1], [1, 2]]})
    with pytest.raises(MeshEvidenceError) as exc:
        _snapshot(mesh)
    assert exc.value.code == "MESH_ARRAY_SHAPE_INVALID"


@pytest.mark.parametrize("bad_index", [-1, 4, True, 1.5])
def test_connectivity_indices_must_be_exact_in_range_zero_based_integers(bad_index):
    mesh = FakeMeshSequence(elems={"tri": [[bad_index], [1], [2]], "edg": [[0, 1], [1, 2]]})
    with pytest.raises(MeshEvidenceError) as exc:
        _snapshot(mesh)
    assert exc.value.code == "MESH_CONNECTIVITY_INVALID"


def test_entity_assignments_must_be_integer_and_nonnegative():
    for invalid in (-1, True, 1.5):
        mesh = FakeMeshSequence(entities={"tri": [invalid], "edg": [1, 2]})
        with pytest.raises(MeshEvidenceError) as exc:
            _snapshot(mesh)
        assert exc.value.code == "MESH_ENTITY_ASSIGNMENT_INVALID"


def test_duplicate_or_undocumented_element_types_are_rejected_before_array_reads():
    duplicate = FakeMeshSequence(types=["tri", "tri"])
    with pytest.raises(MeshEvidenceError) as exc:
        _snapshot(duplicate)
    assert exc.value.code == "MESH_TYPES_INVALID"
    assert not any(call[0] in {"getVertex", "getElem", "getElemEntity"} for call in duplicate.calls)

    unsupported = FakeMeshSequence(types=["tet", "tet10"], elems={"tet": [[0], [1], [2], [3]], "tet10": []},
                                   entities={"tet": [1], "tet10": []})
    with pytest.raises(MeshEvidenceError) as exc:
        _snapshot(unsupported)
    assert exc.value.code == "MESH_ELEMENT_TYPE_UNSUPPORTED"
    assert not any(call[0] in {"getVertex", "getElem", "getElemEntity"} for call in unsupported.calls)


def test_documented_element_types_use_fixed_corner_arities():
    arities = {"vtx": 1, "edg": 2, "tri": 3, "quad": 4, "tet": 4, "pyr": 5, "prism": 6, "hex": 8}
    elems = {name: [[index % 8] for index in range(arity)] for name, arity in arities.items()}
    entities = {name: [1] for name in arities}
    vertices = [list(map(float, range(8))), [0.0] * 8]
    mesh = FakeMeshSequence(vertices=vertices, elems=elems, entities=entities, types=list(arities))
    snapshot = _snapshot(mesh)
    assert {item["type"]: item["nodes_per_element"] for item in snapshot["content"]["types"]} == arities
    assert snapshot["content"]["element_count"] == len(arities)


@pytest.mark.parametrize("drift", [
    {"vertices": [4, 5]},
    {"count:tri": [1, 2]},
    {"types": [["tri", "edg"], ["tri", "edg", "vtx"]]},
])
def test_pre_post_mesh_metadata_drift_fails_closed(drift):
    mesh = FakeMeshSequence(drift=drift)
    with pytest.raises(MeshEvidenceError) as exc:
        _snapshot(mesh)
    assert exc.value.code == "MESH_CHANGED_DURING_CAPTURE"


def test_unknown_worker_reply_is_preserved_and_stops_all_later_reads():
    class Unknown(RuntimeError):
        code = "EXECUTION_STATE_UNKNOWN"
        execution_state_unknown = True

    failure = Unknown("ambiguous worker reply")

    class FailingMesh(FakeMeshSequence):
        def getVertex(self, position, number):
            self._call("getVertex", position, number)
            raise failure

    mesh = FailingMesh()
    with pytest.raises(Unknown) as exc:
        _snapshot(mesh)
    assert exc.value is failure
    assert [call[0] for call in mesh.calls].count("getVertex") == 1
    assert not any(call[0] in {"getElem", "getElemEntity"} for call in mesh.calls)


def test_binding_requires_complete_modelref_and_exact_observation_identity():
    incomplete = _binding(model_ref={"session_id": "s", "model_tag": "m", "generation": 1})
    with pytest.raises(MeshEvidenceError) as exc:
        _snapshot(binding=incomplete)
    assert exc.value.code == "MESH_BINDING_INVALID"

    with pytest.raises(MeshEvidenceError) as exc:
        _snapshot(binding=_binding(revision=True))
    assert exc.value.code == "MESH_BINDING_INVALID"


@pytest.mark.parametrize("mutation,expected_difference", [
    ("coordinates", "vertex_coordinates_or_order"),
    ("connectivity", "connectivity_or_element_order:tri"),
    ("entities", "entity_assignments_or_order:tri"),
])
def test_same_mesh_tag_with_changed_content_is_not_equal(mutation, expected_difference):
    left = _snapshot()
    if mutation == "coordinates":
        right_mesh = FakeMeshSequence(vertices=[[0.0, 1.0, 1.1, 0.0], [0.0, 0.0, 1.0, 1.0]])
    elif mutation == "connectivity":
        right_mesh = FakeMeshSequence(elems={"tri": [[0], [2], [1]], "edg": [[0, 1], [1, 2]]})
    else:
        right_mesh = FakeMeshSequence(entities={"tri": [4], "edg": [1, 2]})
    right = _snapshot(right_mesh, binding=_binding(revision=5, attempt_id="attempt-b", phase="post-stage"))
    comparison = compare_current_mesh_snapshots(left, right)
    assert comparison["same_mesh_tag"] is True
    assert comparison["same_content"] is False
    assert expected_difference in comparison["differences"]
    assert comparison["historical_mesh"] == "UNVERIFIED"
    assert comparison["source_target_mapping"] == "UNVERIFIED"


def test_equal_content_allows_different_attempt_revision_and_mesh_tag_without_history_claim():
    left = _snapshot()
    right = _snapshot(binding=_binding(revision=8, attempt_id="attempt-b", phase="post-stage", mesh="mesh2"))
    comparison = compare_current_mesh_snapshots(left, right)
    assert comparison["status"] == "MATCH_CONTENT_ONLY"
    assert comparison["same_content"] is True
    assert comparison["same_mesh_tag"] is False
    assert "UNVERIFIED" in comparison["historical_mesh"]


@pytest.mark.parametrize("tamper", [
    "coordinate_hash", "arity", "capture_semantics", "extra_claim", "envelope_hash",
])
def test_malformed_or_upgraded_snapshot_is_rejected(tamper):
    snapshot = _snapshot()
    if tamper == "coordinate_hash":
        snapshot["content"]["vertex_coordinates_sha256"] = "0" * 64
    elif tamper == "arity":
        snapshot["content"]["types"][0]["nodes_per_element"] = 99
    elif tamper == "capture_semantics":
        snapshot["capture"]["atomic_snapshot"] = True
    elif tamper == "extra_claim":
        snapshot["capture"]["historical_mesh"] = "VERIFIED"
    else:
        snapshot["evidence_sha256"] = "f" * 64
    with pytest.raises(MeshEvidenceError):
        compare_current_mesh_snapshots(snapshot, _snapshot())


def test_comparison_requires_same_exact_project_model_component_and_geometry():
    left, right = _snapshot(), _snapshot(binding=_binding(component="comp2"))
    with pytest.raises(MeshEvidenceError) as exc:
        compare_current_mesh_snapshots(left, right)
    assert exc.value.code == "MESH_COMPARISON_BINDING_MISMATCH"
