"""Software-only controls for the bounded geometry/material/PDE/mesh adapters.

The fake Java objects exercise the production action functions and their
prewrite/readback/UNKNOWN branches.  These tests do not start COMSOL and are
not native or scientific acceptance evidence.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from comsol_mcp import _g3_model_domains as domains
from comsol_mcp._execution_contract import PreWriteRefusal
from comsol_mcp._metric_contract import definition_sha256


MODEL_REF = {"schema_version": 1, "session_id": "session-1", "server_instance_id": "srv",
             "model_tag": "m", "generation": 1}
COMP_GEOM_PATH = {"segments": [
    {"collection": "component", "tag": "comp1"}, {"collection": "geom", "tag": "geom1"},
]}
COMP_MESH_PATH = {"segments": [
    {"collection": "component", "tag": "comp1"}, {"collection": "mesh", "tag": "mesh1"},
]}
PDE_PATH = {"segments": [
    {"collection": "component", "tag": "comp1"}, {"collection": "physics", "tag": "pde"},
    {"collection": "feature", "tag": "eq1"},
]}


def _context(tmp_path: Path, **extra):
    value = {"project_root": str(tmp_path), "project_id": "project-1", "operation_store": object(),
             "host_identity": "host-1", "engine_host_identity": "engine-1", "operation_id": "op-1",
             "request_id": "req-1", "model_ref": MODEL_REF, "source_revision": 3}
    value.update(extra)
    return value


@pytest.fixture(autouse=True)
def _managed_model(monkeypatch):
    monkeypatch.setattr(domains, "_managed_model", lambda _context, _tag: (object(), MODEL_REF, object()))


def _args(context, **body):
    return {"_managed_context": context, **body}


class _Tags:
    def __init__(self):
        self.items = {}

    def tags(self):
        return list(self.items)

    def create(self, tag, type_id):
        if tag in self.items:
            raise ValueError("tag exists")
        self.items[tag] = _Node(type_id)
        return self.items[tag]

    def get(self, tag):
        return self.items[tag]


class _Node:
    _TYPE_MAP = {"string": "String", "strings": "StringArray", "double": "Double"}

    def __init__(self, type_id="Node", values=None, value_types=None, allowed=None):
        self.type_id = type_id
        self.values = dict(values or {})
        self.value_types = dict(value_types or {})
        self.allowed = dict(allowed or {})
        self._selections = {}
        self.calls = []

    def getType(self):
        return self.type_id

    def properties(self):
        return list(self.value_types)

    def getValueType(self, name):
        return self.value_types.get(name)

    def getAllowedPropertyValues(self, name):
        return self.allowed.get(name)

    def getString(self, name):
        return self.values[name]

    def getStringArray(self, name):
        return list(self.values[name])

    def getDouble(self, name):
        return self.values[name]

    def set(self, name, value):
        self.calls.append(("set", name, value))
        self.values[name] = value

    def selection(self, name=None):
        if name is not None:
            return self._selections.setdefault(name, _Selection())
        return _Selection()


class _Selection:
    def __init__(self):
        self.values = []

    def set(self, values):
        self.values = list(values)

    def objects(self):
        return list(self.values)


class _Geometry(_Node):
    def __init__(self):
        super().__init__("GeomSequence")
        self.features = _Tags()
        self.run_count = 0

    def lengthUnit(self):
        return "mm"

    def objectNames(self):
        return ["blk1", "cyl1"]

    def feature(self, *args):
        if not args:
            return self.features
        return self.features.get(args[0])

    def runAll(self):
        self.run_count += 1

    def exportFinal(self, path):
        Path(path).write_bytes(b"synthetic geometry bytes")
        return path


class _MaterialList(_Tags):
    def insert(self, _path, tags):
        for tag in tags:
            self.items[tag] = _Node("Material")
        return (["imported"], [f"mat:{tag}" for tag in tags], [])


class _Component:
    def __init__(self, geom=None, mesh=None, physics=None):
        self._geom = geom or _Geometry()
        self._mesh = mesh
        self._physics = physics

    def geom(self, *_args):
        return self._geom

    def mesh(self, *_args):
        return self._mesh

    def physics(self, *_args):
        return self._physics


class _Model:
    def __init__(self, component=None, materials=None):
        self._component = component or _Component()
        self._materials = materials or _MaterialList()

    def component(self, _tag):
        return self._component

    def material(self, *_args):
        return self._materials


class _Worker:
    def __init__(self, model=None):
        self._model = model or _Model()

    def client(self):
        return self

    def model(self, _tag):
        return self._model


def _patch_export_helpers(monkeypatch, tmp_path):
    monkeypatch.setattr(domains, "_project_store", lambda _context: (tmp_path, _DestinationStore(tmp_path)))
    monkeypatch.setattr(domains, "_publish_export", lambda _ctx, _private, stage, dest, **kw:
                        {"path": dest, "sha256": hashlib.sha256(stage.read_bytes()).hexdigest(),
                         "size_bytes": stage.stat().st_size, "role": kw["role"]})


class _DestinationStore:
    def __init__(self, root):
        self.root = root

    def resolve_safe_path(self, relative, allow_overwrite=False):
        result = self.root / relative
        if result.exists() and not allow_overwrite:
            raise FileExistsError(relative)
        return result


def test_geometry_export_publishes_final_geometry_bytes_and_selection_refuses_before_write(monkeypatch, tmp_path):
    geom = _Geometry()
    worker = _Worker(_Model(_Component(geom=geom)))
    monkeypatch.setattr(domains, "resolve_path", lambda *_a, **_kw: (COMP_GEOM_PATH, geom))
    _patch_export_helpers(monkeypatch, tmp_path)
    context = _context(tmp_path)
    result = domains.geometry_export(worker, "m", _args(context, geometry=COMP_GEOM_PATH,
                                                           format="mphtxt", destination="exports/g.mphtxt"))
    assert result["status"] == "APPLIED", result.get("failure", result)
    assert result["file"]["size_bytes"] == len(b"synthetic geometry bytes")
    with pytest.raises(PreWriteRefusal, match="selected-object"):
        domains.geometry_export(worker, "m", _args(context, geometry=COMP_GEOM_PATH, format="mphtxt",
                                                     destination="exports/selected.mphtxt", selection={"objects": ["blk1"]}))


def test_geometry_repair_uses_owned_branch_and_native_property_readbacks(monkeypatch, tmp_path):
    geom = _Geometry()
    original_create = geom.features.create
    def create(tag, type_id):
        node = original_create(tag, type_id)
        if type_id == "Repair":
            node.values = {"check": "on", "repairtol": 0.01}
            node.value_types = {"check": "String", "repairtol": "Double"}
            node.allowed = {"check": ["on", "off"]}
        return node
    geom.features.create = create
    monkeypatch.setattr(domains, "resolve_path", lambda *_a, **_kw: (COMP_GEOM_PATH, geom))
    monkeypatch.setattr(domains, "_verify_owned_checkpoint_branch", lambda *_a: {"checkpoint_sha256": "a" * 64})
    result = domains.geometry_repair(_Worker(), "m", _args(_context(tmp_path), geometry=COMP_GEOM_PATH,
        plan={"tag": "repair2", "input_objects": ["blk1"], "properties": {"check": "off", "repairtol": 0.02}}))
    assert result["status"] == "APPLIED", result
    assert geom.run_count == 1
    values = {row["name"]: row for row in result["applied"] if row["step"] == "property"}
    assert values["check"]["getter"] == "getString" and values["check"]["readback"] == "off"
    assert values["repairtol"]["getter"] == "getDouble" and values["repairtol"]["readback"] == pytest.approx(0.02)


def test_geometry_repair_native_readback_mismatch_is_unknown_after_create(monkeypatch, tmp_path):
    geom = _Geometry()
    # This feature is added by create(); its getter intentionally returns the old value after set.
    original_create = geom.features.create
    def create(tag, type_id):
        node = original_create(tag, type_id)
        node.values = {"check": "on"}
        node.value_types = {"check": "String"}
        node.allowed = {"check": ["on", "off"]}
        node.getString = lambda _name: "on"
        return node
    geom.features.create = create
    monkeypatch.setattr(domains, "resolve_path", lambda *_a, **_kw: (COMP_GEOM_PATH, geom))
    monkeypatch.setattr(domains, "_verify_owned_checkpoint_branch", lambda *_a: {"checkpoint_sha256": "a" * 64})
    result = domains.geometry_repair(_Worker(), "m", _args(_context(tmp_path), geometry=COMP_GEOM_PATH,
        plan={"tag": "repair2", "input_objects": ["blk1"], "properties": {"check": "off"}}))
    assert result["status"] == "UNKNOWN" and result["execution_state_unknown"] is True
    assert result["partial_change"] is True and result["safe_retry"] is False


def test_owned_geometry_branch_revalidates_checkpoint_and_clone_file(monkeypatch, tmp_path):
    class Ref:
        def __init__(self, value): self.value = value
        def as_dict(self): return dict(self.value)
    source_ref = Ref({"schema_version": 1, "session_id": "session-1", "server_instance_id": "srv",
                      "model_tag": "source", "generation": 1})
    branch_ref = Ref({**source_ref.as_dict(), "model_tag": "branch"})
    payload = b"verified checkpoint bytes"
    digest = hashlib.sha256(payload).hexdigest()
    branch_path = tmp_path / "branches" / "branch.mph"
    branch_path.parent.mkdir()
    branch_path.write_bytes(payload)
    clone = {"verified": True, "sha256": digest, "source_sha256": digest, "bytes": len(payload)}
    source_binding = {"model_ref": source_ref.as_dict()}
    branch_row = {"kind": "checkpoint_branch", "schema_version": 1, "checkpoint_id": "cp-1",
        "model_ref": branch_ref.as_dict(), "revision": 5, "source_binding": source_binding,
        "admission_source_identity": {"model_ref": source_ref.as_dict()}, "clone_input": clone,
        "private_path": str(branch_path)}
    state = type("State", (), {"ref": branch_ref, "revision": 5, "dirty": False,
                                "fingerprint": "fp-branch", "active_operation_id": None})()
    ledger = type("Ledger", (), {"model_ownership": {"branch": "mcp_owned"},
        "_state_for": lambda _self, _ref: state})()
    service = type("Service", (), {"ledger": ledger})()
    backend = type("Backend", (), {"model_project_binding": lambda _self, _ref: {
        "attribution": "PROJECT_BOUND", "project_id": "project-1"},
        "_model_project_key": lambda _self, _ref: "branch-revision"})()
    class Store:
        def get_metadata(self, category, key):
            if category == "artifacts" and key == "g2_owned_branch_branch": return branch_row
            if category == "revisions" and key == "branch-revision":
                return {"model_ref": branch_ref.as_dict(), "revision": 5, "dirty": False,
                        "fingerprint": "fp-branch", "active_operation_id": None,
                        "project_id": "project-1", "attribution": "PROJECT_BOUND"}
            return None
    store = Store()
    from comsol_mcp import _g2_checkpoint_ops
    monkeypatch.setattr(_g2_checkpoint_ops, "checkpoint_input", lambda *_a: (
        {"source_binding": source_binding, "sha256": digest}, tmp_path / "source.mph"))
    context = _context(tmp_path, operation_store=store, execution_service=service, backend=backend,
        model_ref=branch_ref.as_dict(), source_revision=5)
    result = domains._verify_owned_checkpoint_branch(context, "branch", branch_ref)
    assert result["branch_file_sha256"] == digest and result["branch_file_size_bytes"] == len(payload)
    branch_path.write_bytes(b"changed checkpoint bytes")
    with pytest.raises(PreWriteRefusal, match="differ from the source checkpoint"):
        domains._verify_owned_checkpoint_branch(context, "branch", branch_ref)


def test_material_import_checks_registered_bytes_and_inserted_tags(monkeypatch, tmp_path):
    source = tmp_path / "material.xml"
    source.write_text("synthetic material source")
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    registered_provenance = {"source_sha256": sha, "source_units": "SI", "topology": {"domains": 2}}
    record = {"role": "material_source", "classification": "project_local", "provenance": registered_provenance}
    monkeypatch.setattr(domains, "_registered", lambda *_a: ({"path": str(source), "sha256": sha,
                        "size": source.stat().st_size, "artifact_id": sha}, record))
    materials = _MaterialList()
    worker = _Worker(_Model(materials=materials))
    result = domains.material_import(worker, "m", _args(_context(tmp_path), artifact_id=sha,
        provenance={"source_sha256": sha, "material_tags": ["mat1"], "source_units": "SI", "topology": {"domains": 2}}))
    assert result["status"] == "APPLIED" and result["material_tags_after"] == ["mat1"]
    assert result["provenance_status"].startswith("SOURCE_BYTES_AND_REGISTERED_UNITS_TOPOLOGY_VERIFIED")


def test_material_import_failed_insert_readback_is_unknown_and_selection_refuses(monkeypatch, tmp_path):
    source = tmp_path / "material.mph"
    source.write_bytes(b"fixture")
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    registered_provenance = {"source_sha256": sha, "source_units": "SI", "topology": {}}
    monkeypatch.setattr(domains, "_registered", lambda *_a: ({"path": str(source), "sha256": sha,
                        "size": source.stat().st_size, "artifact_id": sha}, {"role": "saved_model", "classification": "project_local",
                        "provenance": registered_provenance}))
    worker = _Worker(_Model(materials=_MaterialList()))
    context = _context(tmp_path)
    base = {"artifact_id": sha, "provenance": {"source_sha256": sha, "material_tags": ["x"], "source_units": "SI", "topology": {}}}
    with pytest.raises(PreWriteRefusal, match="selection"):
        domains.material_import(worker, "m", _args(context, **base, selection={"domains": [1]}))
    bad = _MaterialList()
    bad.insert = lambda *_a: (["no tags"], [], [])
    worker = _Worker(_Model(materials=bad))
    result = domains.material_import(worker, "m", _args(context, **base))
    assert result["status"] == "UNKNOWN" and result["execution_state_unknown"] is True


def test_pde_weak_form_property_is_set_with_readback_and_unknown_property_refuses(monkeypatch, tmp_path):
    node = _Node("WeakContribution", {"weak": "u"}, {"weak": "String"})
    interface = _Node("WeakFormPDE")
    component = _Component(physics=_Tags())
    model = _Model(component)
    monkeypatch.setattr(domains, "bound_model", lambda *_a: model)
    def resolve(_worker, _tag, path, **_kw):
        segments = path["segments"]
        return (path, interface if len(segments) == 2 else node)
    monkeypatch.setattr(domains, "resolve_path", resolve)
    def setter(_worker, _tag, _path, assignments):
        for row in assignments:
            node.set(row["name"], row["value"]["data"])
        return {"success": True, "data": {"applied": [{"readback": node.values["weak"]}]}}
    monkeypatch.setattr(domains, "property_set", setter)
    result = domains.physics_pde_manage(_Worker(model), "m", _args(_context(tmp_path), action="configure",
        path=PDE_PATH, definition={"properties": {"weak": "d(u,x)*test(u)"}}))
    assert result["status"] == "APPLIED" and node.values["weak"] == "d(u,x)*test(u)"
    with pytest.raises(PreWriteRefusal, match="allow-list"):
        domains.physics_pde_manage(_Worker(model), "m", _args(_context(tmp_path), action="configure",
            path=PDE_PATH, definition={"properties": {"secret": "x"}}))


def test_coefficient_pde_indexed_array_prevalidates_shape_before_setter(monkeypatch, tmp_path):
    node = _Node("CoefficientFormPDE", {"c": ["c11", "c22"]}, {"c": "StringArray"})
    interface = _Node("CoefficientFormPDE")
    model = _Model()
    monkeypatch.setattr(domains, "bound_model", lambda *_a: model)
    monkeypatch.setattr(domains, "resolve_path", lambda _w, _t, path, **_kw:
                        (path, interface if len(path["segments"]) == 2 else node))
    writes = []
    def index_set(_worker, _tag, _path, name, index, typed):
        writes.append((name, index, typed["data"]))
        node.values[name][index[0]] = typed["data"]
        return {"success": True, "data": {"readback": typed["data"], "verification": {"status": "PASS"}}}
    monkeypatch.setattr(domains, "property_index_set", index_set)
    worker = _Worker(model)
    good = domains.physics_pde_manage(worker, "m", _args(_context(tmp_path), action="configure", path=PDE_PATH,
        definition={"properties": {"c": ["2", "3"]}}))
    assert good["status"] == "APPLIED" and len(writes) == 2
    writes.clear()
    with pytest.raises(PreWriteRefusal, match="array length"):
        domains.physics_pde_manage(worker, "m", _args(_context(tmp_path), action="configure", path=PDE_PATH,
            definition={"properties": {"c": ["2", "3", "4"]}}))
    assert writes == []


def test_global_equations_create_uses_indexed_set_and_stops_on_readback_failure(monkeypatch, tmp_path):
    values = {name: ["", "", ""] for name in ("name", "equation", "initialValueU")}
    feature = _Node("GlobalEquation", values, {name: "StringArray" for name in values})
    interface = _Node("GlobalEquations")
    feature_list = _Tags()
    feature_list.items["ge1"] = feature
    interface.feature = lambda *args: feature_list if not args else feature_list.get(args[0])
    physics = _Tags()
    def create(tag, type_id):
        physics.items[tag] = interface
    physics.create = create
    component = _Component(physics=physics)
    model = _Model(component)
    monkeypatch.setattr(domains, "bound_model", lambda *_a: model)
    monkeypatch.setattr(domains, "_component_physics", lambda *_a: (component, physics))
    def index_set(_worker, _tag, _path, prop, indices, typed):
        feature.values[prop][indices[0]] = typed["data"]
        return {"success": True, "data": {"readback": typed["data"], "verification": {"status": "PASS"}}}
    monkeypatch.setattr(domains, "property_index_set", index_set)
    args = _args(_context(tmp_path), action="create_global_equations", definition={"component": "comp1", "tag": "geq1",
        "equations": [{"name": "x", "equation": "x-1", "initial_value_u": "0"}]})
    result = domains.physics_pde_manage(_Worker(model), "m", args)
    assert result["status"] == "APPLIED" and feature.values["equation"][0] == "x-1"
    physics.items.pop("geq2", None)
    monkeypatch.setattr(domains, "property_index_set", lambda *_a: {"success": False, "error": {"code": "DENIED"}})
    fail = domains.physics_pde_manage(_Worker(model), "m", {**args, "definition": {"component": "comp1", "tag": "geq2",
        "equations": [{"name": "x", "equation": "x-2", "initial_value_u": "1"}]}})
    assert fail["status"] == "UNKNOWN" and fail["safe_retry"] is False


class _Mesh(_Node):
    def __init__(self, complete=True, empty=False, count=12, sdim=3):
        super().__init__("MeshSequence")
        self.complete, self.empty, self.count, self.sdim = complete, empty, count, sdim
        self.features = _Tags()
        self.run_count = 0
        self.import_count = 0

    def getSDim(self): return self.sdim
    def getNumElem(self): return self.count
    def getNumVertex(self): return 8
    def isEmpty(self): return self.empty
    def isComplete(self): return self.complete
    def getTypes(self): return ["tet"]
    def lengthUnit(self): return "mm"
    def geom(self): return "geom1"
    def feature(self, *args): return self.features if not args else self.features.get(args[0])
    def export(self, path):
        Path(path).write_bytes(b"synthetic mesh export")
        return path
    def run(self):
        self.run_count += 1
        self.complete, self.empty = True, False
        if self.count <= 0:
            self.count = 12
    def create(self, tag, type_id):
        feature = self.features.create(tag, type_id)
        if type_id == "Import":
            feature.values = {"source": "native", "filename": ""}
            feature.value_types = {"source": "String", "filename": "String"}
            feature.importData = self.importData
    def importData(self):
        self.import_count += 1
        self.empty = False
        self.complete = False


def test_mesh_export_requires_complete_nonempty_and_publishes(monkeypatch, tmp_path):
    mesh = _Mesh()
    monkeypatch.setattr(domains, "_mesh_path_context", lambda *_a, **_kw: (COMP_MESH_PATH, mesh, "comp1", "mesh1"))
    _patch_export_helpers(monkeypatch, tmp_path)
    result = domains.mesh_export(_Worker(), "m", _args(_context(tmp_path), path=COMP_MESH_PATH,
        format="stl", destination="exports/mesh.stl"))
    assert result["status"] == "APPLIED" and result["file"]["size_bytes"] > 0
    incomplete = _Mesh(complete=False)
    monkeypatch.setattr(domains, "_mesh_path_context", lambda *_a, **_kw: (COMP_MESH_PATH, incomplete, "comp1", "mesh1"))
    with pytest.raises(PreWriteRefusal, match="isComplete"):
        domains.mesh_export(_Worker(), "m", _args(_context(tmp_path), path=COMP_MESH_PATH,
            format="mphtxt", destination="exports/not-ready.mphtxt"))


def test_mesh_import_importdata_then_run_preserves_registered_provenance(monkeypatch, tmp_path):
    source = tmp_path / "mesh.stl"
    source.write_bytes(b"synthetic registered mesh")
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    record = {"role": "mesh_source", "provenance": {"source_sha256": sha, "source_units": "mm", "topology": {"faces": 8}}}
    monkeypatch.setattr(domains, "_registered", lambda *_a: ({"path": str(source), "sha256": sha,
                        "size": source.stat().st_size, "artifact_id": sha}, record))
    mesh = _Mesh(count=0, empty=True)
    component = _Component(mesh=mesh)
    model = _Model(component)
    monkeypatch.setattr(domains, "bound_model", lambda *_a: model)
    monkeypatch.setattr(domains, "_mesh_path_context", lambda *_a, **_kw: (COMP_MESH_PATH, mesh, "comp1", "mesh1"))
    def prop_set(_worker, _tag, _path, payload):
        feat = mesh.features.items[next(iter(mesh.features.items))]
        for row in payload:
            feat.values[row["name"]] = row["value"]["data"]
        return {"success": True, "data": {"applied": [{"readback": row["value"]["data"]} for row in payload]}}
    monkeypatch.setattr(domains, "property_set", prop_set)
    result = domains.mesh_import(_Worker(model), "m", _args(_context(tmp_path), path=COMP_MESH_PATH, artifact_id=sha))
    assert result["status"] == "APPLIED", result.get("failure", result)
    assert mesh.import_count == 1 and mesh.run_count == 1
    assert result["source_sha256"] == sha and result["source_units"] == "mm"


class _MetricStore:
    def __init__(self, project_id, metric_records=()):
        self.project_id = project_id
        self.metric_records = list(metric_records)
        self.records = {}

    def read_metric_definitions(self, project_id):
        assert project_id == self.project_id
        return list(self.metric_records)

    def get_metadata(self, category, key):
        assert category == "artifacts"
        return self.records.get(key)

    def get_operation(self, operation_id):
        assert operation_id == "op-1"
        return {"operation_id": operation_id, "project_id": self.project_id, "status": "RUNNING"}


def _case(case_id, metric_id, source_ref, branch_suffix, value, *, context, store):
    branch = {**source_ref, "model_tag": f"branch-{branch_suffix}"}
    observation = "obs_" + f"{int(branch_suffix):032x}"
    payload = f"registered observation {branch_suffix}".encode()
    obs_sha = hashlib.sha256(payload).hexdigest()
    artifact_path = Path(context["project_root"]) / "observations" / f"{observation}.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)
    store.records[observation] = {"kind": "w17_observation", "observation_id": observation,
        "project_id": context["project_id"], "model_ref": branch, "revision": 4,
        "producer": context["operation_id"], "sha256": obs_sha,
        "artifact": {"file_path": str(artifact_path), "byte_size": len(payload), "sha256": obs_sha}}
    return {"status": "SUCCEEDED", "case_id": case_id, "checkpoint_id": "checkpoint-" + case_id,
            "branch_model_ref": branch, "branch_revision": 4, "source_model_ref": source_ref,
            "mesh_readback": {"is_complete": True, "is_empty": False, "element_count": 100 * (int(branch_suffix) - 1) + 10},
            "study_readback": {"status": "SUCCEEDED", "solution": "sol1"},
            "observations": {metric_id: {"metric_definition_sha256": metric_id,
                "observation_ref": {"observation_id": observation, "sha256": obs_sha},
                "model_ref": branch, "model_revision": 4, "producer_operation_id": "op-1",
                "value": {"value": float(value), "unit": "K"}}}}


def test_convergence_study_compares_bound_observations_only_and_stops_on_unknown(tmp_path):
    metric_definition = {"expression": "T"}
    metric_id = definition_sha256(metric_definition)
    store = _MetricStore("project-1", [{"metric_id": "temperature", "version": 2,
        "definition_sha256": metric_id, "definition": metric_definition, "removed": False}])
    calls = []
    events = []
    def executor(**kwargs):
        events.append("case:" + kwargs["case_id"])
        calls.append(kwargs["case_id"])
        index = len(calls)
        assert kwargs["metric_refs"] == [{"metric_id": "temperature", "version": 2,
            "definition_sha256": metric_id, "definition": metric_definition}]
        return _case(kwargs["case_id"], metric_id, MODEL_REF, str(index), float(index), context=ctx, store=store)
    ctx = _context(tmp_path, operation_store=store, _mesh_convergence_case_executor=executor,
                    _mesh_convergence_preflight=lambda **kwargs: events.append("preflight") or {"status": "PREFLIGHTED"},
                    _mesh_convergence_budget_status=lambda: {"cases_evaluated": len(calls)},
                    execution_service=object(), backend=object())
    definition = {"mesh_path": COMP_MESH_PATH,
        "study_path": {"segments": [{"collection": "study", "tag": "std1"}]},
        "source_checkpoint_id": "cp-source", "levels": [
            {"case_id": "coarse", "refinement": {"nrefine": 1}},
            {"case_id": "fine", "refinement": {"nrefine": 2}},
        ]}
    result = domains.mesh_convergence_study(_Worker(), "m", _args(ctx, definition=definition, metrics=[metric_id]))
    assert result["status"] == "APPLIED" and calls == ["coarse", "fine"]
    assert events[:3] == ["preflight", "case:coarse", "case:fine"]
    assert result["conclusion"].startswith("COMPARISON_ONLY")
    assert result["comparisons"][metric_id][0]["absolute_delta"] == 1.0

    stopped = []
    def failing_executor(**kwargs):
        stopped.append(kwargs["case_id"])
        return {"status": "FAILED"}
    ctx["_mesh_convergence_case_executor"] = failing_executor
    failed = domains.mesh_convergence_study(_Worker(), "m", _args(ctx, definition=definition, metrics=[metric_id]))
    assert failed["status"] == "UNKNOWN" and failed["next_case_not_executed"] is True
    assert stopped == ["coarse"]

    # Even a well-shaped ref fails closed if the ObservationStore row was
    # rebound to another revision; no later mesh level is dispatched.
    stale_calls = []
    def stale_executor(**kwargs):
        stale_calls.append(kwargs["case_id"])
        case = _case(kwargs["case_id"], metric_id, MODEL_REF, "7", 7.0, context=ctx, store=store)
        obs_id = case["observations"][metric_id]["observation_ref"]["observation_id"]
        store.records[obs_id]["revision"] = 99
        return case
    ctx["_mesh_convergence_case_executor"] = stale_executor
    stale = domains.mesh_convergence_study(_Worker(), "m", _args(ctx, definition=definition, metrics=[metric_id]))
    assert stale["status"] == "UNKNOWN" and stale["next_case_not_executed"] is True
    assert stale_calls == ["coarse"]


def test_convergence_refuses_unregistered_metric_before_executor(tmp_path):
    calls = []
    metric_id = "d" * 64
    context = _context(tmp_path, operation_store=_MetricStore("project-1"),
        _mesh_convergence_case_executor=lambda **kwargs: calls.append(kwargs),
        _mesh_convergence_preflight=lambda **kwargs: {"status": "PREFLIGHTED"})
    definition = {"mesh_path": COMP_MESH_PATH,
        "study_path": {"segments": [{"collection": "study", "tag": "std1"}]},
        "source_checkpoint_id": "cp", "levels": [
            {"case_id": "coarse", "refinement": {"nrefine": 1}},
            {"case_id": "fine", "refinement": {"nrefine": 2}},
        ]}
    with pytest.raises(PreWriteRefusal, match="active definition"):
        domains.mesh_convergence_study(_Worker(), "m", _args(context, definition=definition, metrics=[metric_id]))
    assert calls == []
