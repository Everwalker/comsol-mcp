from __future__ import annotations

import copy
import math
from pathlib import Path

import pytest

from tools import run_native_w24_cure_preflight as runner


def _raw_mesh() -> dict:
    expected = runner.EXPECTED_DOMAIN_VOLUMES_M3
    vertices = [[], []]
    triangles = [[], [], []]
    entities = []
    for domain_index, (label, volume) in enumerate(expected.items()):
        # A right triangle with h=1 m has V=pi*r_max^2*h/3.
        r_max = math.sqrt(3.0 * volume / math.pi)
        base = 3 * domain_index
        vertices[0].extend([0.0, r_max, 0.0])
        vertices[1].extend([0.0, 0.0, 1.0])
        for row, index in enumerate((base, base + 1, base + 2)):
            triangles[row].append(index)
        entities.append(domain_index + 1)
    vertex_count = len(vertices[0])
    triangle_count = len(entities)
    raw = {
        "geometry": "geom1",
        "geometry_dimension": 2,
        "geometry_axisymmetric": True,
        "geometry_length_unit": "m",
        "mesh_tag": "mesh1",
        "mesh_geometry": "geom1",
        "mesh_dimension": 2,
        "mesh_empty": False,
        "mesh_num_elem_noarg": 7,
        "vertex_count": vertex_count,
        "element_types": ["vtx", "edg", "tri"],
        "element_counts": {"vtx": 12, "edg": 0, "tri": 4},
        "triangle_count": triangle_count,
        "triangle_index_base": 0,
        "vertex_coordinate_axes": ["r_m", "z_m"],
        "vertex_coordinates": vertices,
        "triangle_connectivity": triangles,
        "triangle_geometric_entities": entities,
        "capture_methods": [
            "getTypes", "getNumElem", "getNumVertex", "getVertex(position,count)",
            "getElem(tri,position,count)", "getElemEntity(tri,position,count)",
        ],
        "capture_block_size_max": 1024,
        "capture_numeric_scalar_count": 2 * vertex_count + 4 * triangle_count,
        "capture_atomic": False,
    }
    domains = {}
    for domain_index, (label, volume) in enumerate(expected.items()):
        domains[label] = {
            "selection": f"sel_{label}",
            "entities": [domain_index + 1],
            "expected_volume_m3": volume,
            "cross_section_area_m2": 1.25e-7,
            "area_source": "NATIVE_GEOMETRY_MEASURE_GETVOLUME_2D",
        }
    return {"native_mesh_raw": raw, "domain_readbacks": domains}


def _rejected(receipt: dict, code: str) -> None:
    with pytest.raises(ValueError, match=code):
        runner._mesh_volume_rows(receipt)


def test_actual_mesh_triangle_integral_accepts_exact_four_domain_fixture_and_binds_subsets():
    receipt = _raw_mesh()
    raw, proof, raw_sha = runner._mesh_volume_rows(receipt)
    assert raw is receipt["native_mesh_raw"]
    assert len(raw_sha) == 64
    assert proof["relative_tolerance"] == 1e-6
    assert proof["scope"].startswith("initial undeformed straight coupon")
    assert set(proof["domain_readbacks"]) == set(runner.EXPECTED_DOMAIN_VOLUMES_M3)
    for label, row in proof["domain_readbacks"].items():
        assert row["volume_source"] == "NATIVE_MESH_TRIANGLE_AXISYMMETRIC_INTEGRAL"
        assert row["triangle_count"] == 1
        assert row["triangle_columns"] == [list(runner.EXPECTED_DOMAIN_VOLUMES_M3).index(label)]
        assert row["relative_volume_error"] <= 1e-6
        assert len(row["triangle_subset_sha256"]) == 64


def test_noarg_mesh_count_is_independent_of_per_type_sum():
    receipt = _raw_mesh()
    raw = receipt["native_mesh_raw"]
    assert raw["mesh_num_elem_noarg"] == 7
    assert sum(raw["element_counts"].values()) == 16
    accepted_raw, _, _ = runner._mesh_volume_rows(receipt)
    assert accepted_raw["mesh_num_elem_noarg"] == 7


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda r: r["native_mesh_raw"].update(mesh_num_elem_noarg=0), "MESH_METADATA_INVALID"),
        (lambda r: (r["native_mesh_raw"]["element_types"].remove("tri"), r["native_mesh_raw"]["element_counts"].pop("tri")), "MESH_ELEMENT_COUNTS_MISMATCH"),
        (lambda r: r["native_mesh_raw"]["element_counts"].update(tri=0), "MESH_ELEMENT_COUNTS_MISMATCH"),
        (lambda r: r["native_mesh_raw"].update(triangle_count=0), "MESH_METADATA_INVALID"),
        (lambda r: r["native_mesh_raw"]["triangle_connectivity"][2].pop(), "MESH_ARRAY_SHAPE_INVALID"),
        (lambda r: r["native_mesh_raw"]["triangle_geometric_entities"].pop(), "MESH_ARRAY_SHAPE_INVALID"),
    ],
)
def test_mesh_count_and_triangle_payload_fail_closed(mutation, code):
    receipt = copy.deepcopy(_raw_mesh())
    mutation(receipt)
    _rejected(receipt, code)


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda r: r["native_mesh_raw"].update(geometry_length_unit="mm"), "MESH_BINDING_OR_UNIT_INVALID"),
        (lambda r: r["native_mesh_raw"].update(mesh_dimension=3), "MESH_BINDING_OR_UNIT_INVALID"),
        (lambda r: r["native_mesh_raw"].update(capture_methods=["getVertex"]), "MESH_GETTER_PROVENANCE_INVALID"),
        (lambda r: r["native_mesh_raw"].update(element_types=["vtx", "edg", "tri", "quad"],
                                                 element_counts={"vtx": 12, "edg": 0, "tri": 4, "quad": 1},
                                                 mesh_num_elem_noarg=17), "MESH_ELEMENT_TYPE_UNSUPPORTED"),
        (lambda r: r["native_mesh_raw"]["vertex_coordinates"][0].pop(), "MESH_ARRAY_SHAPE_INVALID"),
        (lambda r: r["native_mesh_raw"]["triangle_connectivity"][0].__setitem__(0, 99), "MESH_CONNECTIVITY_INVALID"),
        (lambda r: r["native_mesh_raw"]["vertex_coordinates"][0].__setitem__(0, float("nan")), "MESH_NUMERIC_INVALID"),
        (lambda r: r["native_mesh_raw"]["vertex_coordinates"][0].__setitem__(0, -1e-9), "MESH_NEGATIVE_RADIUS"),
        (lambda r: r["native_mesh_raw"]["triangle_connectivity"][1].__setitem__(0, 0), "MESH_DEGENERATE_TRIANGLE"),
        (lambda r: r["native_mesh_raw"]["triangle_geometric_entities"].__setitem__(0, 99),
         "MESH_DOMAIN_ENTITY_COVERAGE_INVALID"),
        (lambda r: r["domain_readbacks"].pop("fiber"), "MESH_DOMAIN_READBACKS_INVALID"),
        (lambda r: r["domain_readbacks"]["gold"].update(entities=[1]), "MESH_DOMAIN_SELECTION_DUPLICATE"),
        (lambda r: r["domain_readbacks"]["adhesive"].update(area_source="analytic"), "MESH_DOMAIN_AREA_OR_COMPARATOR_INVALID"),
        (lambda r: r["domain_readbacks"]["alumina"].update(expected_volume_m3=1.0),
         "MESH_DOMAIN_AREA_OR_COMPARATOR_INVALID"),
    ],
)
def test_actual_mesh_volume_fails_closed_on_invalid_native_shapes_domains_and_units(mutation, code):
    receipt = copy.deepcopy(_raw_mesh())
    mutation(receipt)
    _rejected(receipt, code)


def test_actual_mesh_volume_rejects_mesh_content_that_misses_frozen_physical_tolerance():
    receipt = _raw_mesh()
    # Stretch every radial coordinate in one selected material; it changes the
    # raw native content and the integrated physical volume.
    receipt["native_mesh_raw"]["vertex_coordinates"][0][1] *= 1.01
    _rejected(receipt, "MESH_VOLUME_THRESHOLD_FAILED")


def _source_response(receipt, model_ref, revision):
    nested = copy.deepcopy(receipt)
    worker_result = {"readback": copy.deepcopy(nested)}
    audit = {"model_tag": model_ref["model_tag"], "instance_id": "worker1", "worker_instance_id": "worker1",
             "generation": 2, "server_instance_id": "127.0.0.1:1234"}
    return {"success": True, "execution": {"project_id": "project-1", "model_ref": model_ref, "revision": revision},
            "data": {"worker": {"ok": True, "status": "SUCCEEDED", "result": worker_result},
                     "before": audit, "after": copy.deepcopy(audit),
                     "readback": {"executed": True, "readback": nested}}}


def test_mesh_artifacts_are_exact_no_clobber_project_and_evidence_copies(tmp_path: Path):
    receipt = _raw_mesh()
    model_ref = {"schema_version": 1, "generation": 1, "model_tag": "model1", "session_id": "session1", "server_instance_id": "server1"}
    project_workspace = tmp_path / "registered-project"
    evidence = tmp_path / "evidence"
    project_workspace.mkdir()
    evidence.mkdir()
    build = runner._attach_mesh_volume_evidence(
        copy.deepcopy(receipt), project_id="project-1", model_ref=model_ref, revision=7,
        stage="build", project_workspace=project_workspace, evidence=evidence,
        worker_epoch={"instance_id": "worker1", "generation": 2, "server": "127.0.0.1:1234"},
        source_response=_source_response(receipt, model_ref, 7),
    )
    reopened = runner._attach_mesh_volume_evidence(
        copy.deepcopy(receipt), project_id="project-1", model_ref={**model_ref, "generation": 2}, revision=9,
        stage="reopen", project_workspace=project_workspace, evidence=evidence,
        worker_epoch={"instance_id": "worker1", "generation": 2, "server": "127.0.0.1:1234"},
        source_response=_source_response(receipt, {**model_ref, "generation": 2}, 9),
    )
    comparison = runner._compare_mesh_volume_reopen(build, reopened)
    assert comparison["matches"] is True
    assert build["mesh_volume_observation"]["raw_mesh_sha256"] == reopened["mesh_volume_observation"]["raw_mesh_sha256"]
    for stage in ("build", "reopen"):
        project_path = project_workspace / f"native_mesh_volume_{stage}.json"
        evidence_path = evidence / f"native_mesh_volume_{stage}.json"
        assert project_path.read_bytes() == evidence_path.read_bytes()
        assert len(build["mesh_volume_observation"]["artifact_sha256"]) == 64
    with pytest.raises(FileExistsError):
        runner._attach_mesh_volume_evidence(
            copy.deepcopy(receipt), project_id="project-1", model_ref=model_ref, revision=10,
            stage="build", project_workspace=project_workspace, evidence=evidence,
        worker_epoch={"instance_id": "worker1", "generation": 2, "server": "127.0.0.1:1234"},
        source_response=_source_response(receipt, model_ref, 10),
        )


def test_mesh_reopen_comparison_rejects_changed_native_mesh_or_binding_domain_projection(tmp_path: Path):
    receipt = _raw_mesh()
    project = tmp_path / "project"
    evidence = tmp_path / "evidence"
    project.mkdir()
    evidence.mkdir()
    build = runner._attach_mesh_volume_evidence(
        copy.deepcopy(receipt), project_id="project-1", model_ref={"schema_version": 1, "generation": 1, "model_tag": "model1", "session_id": "session1", "server_instance_id": "server1"}, revision=1,
        stage="build", project_workspace=project, evidence=evidence,
        worker_epoch={"instance_id": "worker1", "generation": 2, "server": "127.0.0.1:1234"},
        source_response=_source_response(receipt, {"schema_version": 1, "generation": 1, "model_tag": "model1", "session_id": "session1", "server_instance_id": "server1"}, 1),
    )
    changed = copy.deepcopy(receipt)
    changed["native_mesh_raw"]["element_types"].reverse()
    reopen = runner._attach_mesh_volume_evidence(
        changed, project_id="project-1", model_ref={"schema_version": 1, "generation": 2, "model_tag": "model2", "session_id": "session1", "server_instance_id": "server1"}, revision=2,
        stage="reopen", project_workspace=project, evidence=evidence,
        worker_epoch={"instance_id": "worker1", "generation": 2, "server": "127.0.0.1:1234"},
        source_response=_source_response(changed, {"schema_version": 1, "generation": 2, "model_tag": "model2", "session_id": "session1", "server_instance_id": "server1"}, 2),
    )
    assert runner._compare_mesh_volume_reopen(build, reopen)["matches"] is False
