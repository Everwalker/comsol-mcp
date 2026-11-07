"""T20 artifact-identity boundary for function.data_import (public routes, fake Worker).

These tests pin the project-level artifact binding that the published
``function.data_import`` schema promises: the required ``artifact_id`` must be a
schema-v2 artifact registered in the caller's own managed project, and the file
the COMSOL engine reads is the digest-verified managed copy — never a caller
layout path.  Cross-project, fabricated, and forged-path requests must be
refused before any Worker dispatch, and the domain definition routes must not
offer an equivalent ``filename`` bypass.  A small self-contained fake engine is
used; nothing here is native COMSOL evidence.

The published readback is redaction only, never observation fabrication: an
engine readback that differs from the registered binding is published with an
explicit mismatch flag and its raw value redacted, not rewritten to the
expected path.  Pre-dispatch refusals carry the validation stage plus the
dispatch witness proof.  Two adjacent boundaries are pinned as they really are
and remain OPEN (recorded residual risks, not claims of safety): the generic
typed setter mirror (node.property_set) has no artifact concept once its
ordinary project_write isolation gate is satisfied by an owned server, and
function.data_reload follows model state — a loaded model whose filename points
outside the project still refreshes, with only the control-side digest withheld
at the project boundary.
"""
from __future__ import annotations

import json
import hashlib
import types
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Mapping

import pytest

from comsol_mcp import _g3_ops
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._execution_service import ExecutionService


# --------------------------------------------------------------------------
# minimal fake engine (independent of other suites so parallel edits stay safe)
# --------------------------------------------------------------------------


INTERPOLATION_META = {
    "filename": "String", "struct": "String", "nargs": "Int", "dseparator": "String",
    "source": "String", "argunit": "String", "fununit": "String", "funcname": "String",
    "funcs": "StringMatrix", "interp": "String", "extrap": "String",
}


class FNode:
    def __init__(self, *, tag: str, type_id: str = "Fake",
                 collections: Mapping[str, Any] | None = None,
                 value_types: Mapping[str, str] | None = None) -> None:
        self.tag_ = tag
        self.type_id = type_id
        self.collections = dict(collections or {})
        self.value_types = dict(value_types or {})
        self.values: dict[str, Any] = {}
        self.integers: dict[str, int] = {}
        self.arrays: dict[str, list[Any]] = {}
        self.matrices: dict[str, list[list[Any]]] = {}
        self.import_datasets = 0
        self.refreshes = 0

    def tag(self) -> str:
        return self.tag_

    def getType(self) -> str:
        return self.type_id

    def label(self, *args: Any) -> Any:
        return self.tag_

    def properties(self) -> list[str]:
        names = set(self.value_types) | set(self.values) | set(self.integers)
        names |= set(self.arrays) | set(self.matrices)
        return sorted(names)

    def getValueType(self, name: str) -> str | None:
        return self.value_types.get(name)

    def getAllowedPropertyValues(self, name: str) -> list[str] | None:
        return None

    def hasProperty(self, name: str) -> bool:
        return name in self.properties()

    def _read(self, name: str) -> Any:
        for store in (self.values, self.integers, self.arrays, self.matrices):
            if name in store:
                return store[name]
        return None

    def getString(self, name: str) -> Any:
        return self._read(name)

    def getStringArray(self, name: str) -> Any:
        return self._read(name)

    def getStringMatrix(self, name: str) -> Any:
        return self._read(name)

    def getDouble(self, name: str) -> Any:
        return self._read(name)

    def getInt(self, name: str) -> Any:
        return self._read(name)

    def getBoolean(self, name: str) -> Any:
        return self._read(name)

    def set(self, *args: Any) -> None:
        if len(args) == 2 and isinstance(args[1], Mapping):
            name, typed = args
            data = typed.get("data")
            if isinstance(data, str):
                self.values[name] = data
            elif isinstance(data, int):
                self.integers[name] = data
            elif isinstance(data, list):
                if data and isinstance(data[0], list):
                    self.matrices[name] = [list(row) for row in data]
                else:
                    self.arrays[name] = list(data)
            else:
                self.values[name] = data
            return
        raise TypeError("unsupported set form")

    def functionNames(self) -> list[str]:
        return ["f_" + self.tag_]

    def importData(self, *args: Any) -> None:
        self.import_datasets += 1

    def refresh(self) -> None:
        self.refreshes += 1

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        collections = self.__dict__.get("collections") or {}
        if name in collections:
            target = collections[name]

            def accessor(tag: Any = None, *rest: Any) -> Any:
                if tag is None:
                    return target
                return target.get(tag)
            return accessor
        raise AttributeError(name)


class FList:
    def __init__(self) -> None:
        self.items: dict[str, FNode] = {}

    def tags(self) -> list[str]:
        return list(self.items)

    def get(self, tag: str) -> FNode:
        return self.items[tag]

    def hasTag(self, tag: str) -> bool:
        return tag in self.items

    def create(self, tag: str, *type_id: Any) -> FNode:
        node = FNode(tag=tag, type_id=(str(type_id[0]) if type_id else "Fake"),
                     value_types=dict(INTERPOLATION_META)
                     if (type_id and str(type_id[0]) == "Interpolation") else {})
        self.items[tag] = node
        return node

    def remove(self, tag: str) -> None:
        del self.items[tag]


class FakeWorker:
    generation = 1

    def __init__(self, model: FNode) -> None:
        self.model_node = model

    def client(self):
        outer = self

        class _C:
            def model(self, tag: str) -> FNode:
                return outer.model_node
        return _C()

    def operation_context(self, *a: Any, **k: Any):
        return nullcontext()


class SnapshotAdapter:
    def __init__(self, server_id: str) -> None:
        self.server_id = server_id

    def model_snapshot(self, tag: str):
        return {"model_tag": tag, "server_instance_id": self.server_id,
                "external_event_counter": 0, "fingerprint": "fp-test"}


def _make_project(tmp_path: Path, name: str, monkeypatch):
    """One real ControlDaemon + project + adopted model + Interpolation node."""
    monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())
    container = tmp_path / name / "projects"
    container.mkdir(parents=True, exist_ok=True)
    service = ExecutionService(SessionLedger(f"session-{name}", f"server-{name}"),
                               SnapshotAdapter(f"server-{name}"), project_root=container)
    model = FNode(tag="Model", type_id="Model", collections={
        "func": FList(), "param": FList(), "variable": FList(),
        "component": FList(), "selection": FList(),
    })
    model.__dict__["java"] = types.SimpleNamespace(tag=lambda: "m1", getFilePath=lambda: "")
    worker = FakeWorker(model)
    daemon = ControlDaemon(tmp_path / name / "control", service=service, registry={},
                           worker=worker, project_root=container)
    daemon.backend.endpoint_key = "127.0.0.1:2036"
    daemon.backend.worker_identity = {
        "runtime_id": f"rt-{name}", "worker_instance_id": f"w-{name}", "connection_epoch": 1,
        "server_instance_id": f"server-{name}", "endpoint": "127.0.0.1:2036",
    }
    created = daemon.dispatch({
        "operation": "project.create",
        "arguments": {"label": f"proj-{name}", "workspace": f"proj-{name}",
                      "policy": {"permissions": ["inspect", "project_write", "compute"]}},
        "execution": {"request_id": f"pc-{name}", "idempotency_key": f"pc-{name}"},
    })
    assert created["success"], created
    project = created["data"]["project"]
    (Path(project["workspace"]) / "inputs").mkdir(parents=True, exist_ok=True)
    adopted = daemon.dispatch({
        "operation": "model.adopt",
        "arguments": {"server_model_tag": "m1"},
        "execution": {"project_id": project["project_id"], "session_id": f"session-{name}",
                      "request_id": f"adopt-{name}", "idempotency_key": f"adopt-{name}"},
    })
    assert adopted["success"], adopted
    interp = model.collections["func"].create("interpA", "Interpolation")
    return daemon, project, Path(project["workspace"]), model, interp, adopted["execution"]


def _envelope(project: Mapping, exe: Mapping, key: str) -> dict:
    return {"project_id": project["project_id"], "session_id": exe["session_id"],
            "model_ref": exe["model_ref"], "expected_revision": exe["revision"],
            "idempotency_key": key, "request_id": key}


def _call(daemon, operation: str, body: Mapping, exe_env: Mapping):
    return daemon.dispatch({"operation": "registry_call",
                            "arguments": {"operation_id": operation, "arguments": dict(body)},
                            "execution": dict(exe_env)})


def _node_path(*segments: tuple[str, str]) -> dict:
    return {"segments": [{"collection": c, "tag": t} for c, t in segments]}


def _register(daemon, project: Mapping, rel_path: str, *, role: str, key: str):
    return daemon.dispatch({
        "operation": "artifact.register",
        "arguments": {"project_id": project["project_id"], "idempotency_key": key,
                      "request_id": key, "path": rel_path, "role": role,
                      "classification": "internal"},
        "execution": {"project_id": project["project_id"], "idempotency_key": key, "request_id": key},
    })


def _error_code(result: Mapping) -> Any:
    return (result.get("error") or {}).get("code")


# --------------------------------------------------------------------------
# positive: the engine reads the registered, digest-verified managed copy
# --------------------------------------------------------------------------


def test_data_import_binds_registered_artifact_and_overrides_forged_layout_path(tmp_path, monkeypatch):
    daemon, project, root, _model, interp, exe = _make_project(tmp_path, "own", monkeypatch)
    try:
        (root / "inputs" / "grid.dat").write_text("0.0 1.0\n1.0 2.0\n")
        registered = _register(daemon, project, "inputs/grid.dat", role="interpolation_data", key="reg1")
        assert registered["success"], registered
        artifact_id = registered["data"]["artifact_id"]
        managed_rel = registered["data"]["path"]
        managed_path = root / managed_rel

        # a totally unrelated path the caller tries to smuggle in the layout
        forged = tmp_path / "forged" / "salary_grid.dat"
        forged.parent.mkdir(parents=True, exist_ok=True)
        forged.write_text("SENTINEL-PRIVATE 9.0 9.0\n")

        result = _call(daemon, "function.data_import", {
            "path": _node_path(("func", "interpA")), "artifact_id": artifact_id,
            "layout": {"file_path": str(forged), "resolved_path": str(forged),
                       "struct": "spreadsheet", "nargs": 1},
            "units": {"argunit": "m", "fununit": "K"},
        }, _envelope(project, exe, "import-bound"))

        assert result["success"], result
        assert result["data"]["status"] == "APPLIED"
        # the fake engine receives exactly the managed registered copy
        assert interp.values["filename"] == str(managed_path)
        assert interp.import_datasets == 1
        assert str(forged) not in json.dumps(result)
        # the published identity is the registration, not an absolute server path
        artifact = result["data"]["artifact"]
        assert artifact["artifact_id"] == artifact_id
        assert artifact["sha256"] == artifact_id
        assert artifact["path"] == managed_rel
        assert artifact["verify_status"] == "registered_artifact_bound"
        assert result["data"]["readback"]["filename"]["value"] == managed_rel
        # honest redaction: the observed engine value really was the managed copy, so
        # it is published under the project-relative registration path with a binding
        # flag — an observation the engine made, not an expected value pasted in.
        assert result["data"]["readback"]["filename"]["matches_registered_binding"] is True
        filename_rows = [row for row in result["data"]["applied"] if row.get("name") == "filename"]
        assert len(filename_rows) == 1
        filename_row = filename_rows[0]
        assert filename_row["value"]["data"] == managed_rel
        assert filename_row["readback"]["data"] == managed_rel
        assert filename_row["path_binding_to_registered_artifact"] == {"value": True, "readback": True}
        assert str(managed_path) not in json.dumps(result)
    finally:
        daemon.close()


def test_data_import_direct_canonical_action_binds_same_identity(tmp_path, monkeypatch):
    daemon, project, root, _model, interp, exe = _make_project(tmp_path, "direct", monkeypatch)
    try:
        (root / "inputs" / "grid2.dat").write_text("0.0 3.0\n1.0 4.0\n")
        registered = _register(daemon, project, "inputs/grid2.dat", role="data", key="reg2")
        assert registered["success"], registered
        artifact_id = registered["data"]["artifact_id"]
        envelope = _envelope(project, exe, "direct-import")
        arguments = {
            "project_id": project["project_id"], "session_id": envelope["session_id"],
            "model_ref": envelope["model_ref"], "expected_revision": envelope["expected_revision"],
            "idempotency_key": "direct-import", "request_id": "direct-import",
            "path": _node_path(("func", "interpA")), "artifact_id": artifact_id,
            "layout": {"struct": "spreadsheet", "nargs": 1}, "units": {},
        }
        result = daemon.dispatch({"operation": "function.data_import",
                                  "arguments": arguments, "execution": envelope})
        assert result["success"], result
        assert interp.values["filename"] == str(root / registered["data"]["path"])
        assert result["data"]["artifact"]["artifact_id"] == artifact_id
    finally:
        daemon.close()


# --------------------------------------------------------------------------
# negatives: refused before any Worker dispatch
# --------------------------------------------------------------------------


def test_data_import_refuses_unregistered_and_foreign_project_artifact(tmp_path, monkeypatch):
    daemon, project, root, _model, interp, exe = _make_project(tmp_path, "neg", monkeypatch)
    other_daemon, other_project, other_root, _other_model, _other_interp, _other_exe = \
        _make_project(tmp_path, "foreign", monkeypatch)
    try:
        (other_root / "inputs" / "foreign.dat").write_text("0.0 1.0\n")
        foreign = _register(other_daemon, other_project, "inputs/foreign.dat",
                            role="interpolation_data", key="foreign-reg")
        assert foreign["success"], foreign

        outside = tmp_path / "outside" / "private.dat"
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_text("SENTINEL-PRIVATE\n")

        unregistered = _call(daemon, "function.data_import", {
            "path": _node_path(("func", "interpA")), "artifact_id": "0" * 64,
            "layout": {"file_path": str(outside), "struct": "spreadsheet", "nargs": 1},
            "units": {},
        }, _envelope(project, exe, "neg-fake"))
        assert not unregistered["success"]
        assert _error_code(unregistered) == "ARTIFACT_NOT_FOUND"
        # the refusal is proven, not asserted: explicit validation stage plus a
        # dispatch witness that issued no mutation-class engine call
        assert unregistered["data"]["status"] == "REFUSED"
        assert unregistered["data"]["refused"] is True
        assert unregistered["data"]["witness"]["mutation_issued"] is False
        assert unregistered["error"]["stage"] == "validation"

        cross_project = _call(daemon, "function.data_import", {
            "path": _node_path(("func", "interpA")),
            "artifact_id": foreign["data"]["artifact_id"],
            "layout": {"file_path": str(outside), "struct": "spreadsheet", "nargs": 1},
            "units": {},
        }, _envelope(project, exe, "neg-cross"))
        assert not cross_project["success"]
        assert _error_code(cross_project) == "ARTIFACT_NOT_FOUND"
        assert cross_project["data"]["refused"] is True
        assert cross_project["data"]["witness"]["mutation_issued"] is False

        # nothing reached the fake engine in either refusal
        assert interp.values.get("filename") is None
        assert interp.import_datasets == 0
    finally:
        daemon.close()
        other_daemon.close()


def test_function_definition_filename_is_refused_on_create_and_update(tmp_path, monkeypatch):
    daemon, project, root, model, interp, exe = _make_project(tmp_path, "defgate", monkeypatch)
    try:
        outside = tmp_path / "outside2" / "salary.dat"
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_text("SENTINEL-PRIVATE\n")

        update_direct = _call(daemon, "function.update", {
            "path": _node_path(("func", "interpA")), "definition": {"filename": str(outside)},
        }, _envelope(project, exe, "gate-update"))
        assert not update_direct["success"]
        assert _error_code(update_direct) == "PERMISSION_DENIED"
        assert update_direct["data"]["refused"] is True
        assert update_direct["data"]["witness"]["mutation_issued"] is False
        assert interp.values.get("filename") is None

        create_wrapped = _call(daemon, "function.create", {
            "tag": "interpEvil", "type_id": "Interpolation",
            "definition": {"properties": {"filename": str(outside)}},
        }, _envelope(project, exe, "gate-create"))
        assert not create_wrapped["success"]
        assert _error_code(create_wrapped) == "PERMISSION_DENIED"
        assert create_wrapped["data"]["refused"] is True
        assert create_wrapped["data"]["witness"]["mutation_issued"] is False
        assert "interpEvil" not in model.collections["func"].items

        # data_reload on a node whose model state carries a non-bound filename is a
        # model-state read (the write routes above are closed); pin that refresh
        # does not create a new public writer of the property.
        interp.values["filename"] = str(outside)
        reload_result = _call(daemon, "function.data_reload", {"path": _node_path(("func", "interpA"))},
                              _envelope(project, exe, "gate-reload"))
        assert reload_result["success"], reload_result
        assert interp.refreshes == 1
        # residual risk pinned honestly: the engine refreshed a file outside the
        # project (model content); the control-side digest of that path is withheld
        # instead of published, and the digest value itself must not appear.
        assert reload_result["data"]["sha256"]["status"] == "not_reported_outside_project_boundary"
        assert hashlib.sha256(outside.read_bytes()).hexdigest() not in json.dumps(reload_result)
    finally:
        daemon.close()


def test_definition_updates_without_filename_still_apply(tmp_path, monkeypatch):
    daemon, project, _root, _model, interp, exe = _make_project(tmp_path, "narrow", monkeypatch)
    try:
        result = _call(daemon, "function.update", {
            "path": _node_path(("func", "interpA")),
            "definition": {"interp": "linear", "fununit": "K"},
        }, _envelope(project, exe, "narrow-update"))
        assert result["success"], result
        assert result["data"]["status"] == "APPLIED"
        assert interp.values["interp"] == "linear"
    finally:
        daemon.close()


def test_data_import_refuses_nonloopback_engine_before_worker(tmp_path, monkeypatch):
    """The function.data_import branch inherits the loopback engine-host proof:
    project-local managed files may only back a literal loopback COMSOL endpoint."""
    daemon, project, root, _model, interp, exe = _make_project(tmp_path, "remote", monkeypatch)
    try:
        (root / "inputs" / "gridr.dat").write_text("0.0 1.0\n")
        registered = _register(daemon, project, "inputs/gridr.dat", role="interpolation_data", key="regr")
        assert registered["success"], registered
        daemon.backend.endpoint_key = "192.0.2.9:2036"
        result = _call(daemon, "function.data_import", {
            "path": _node_path(("func", "interpA")),
            "artifact_id": registered["data"]["artifact_id"],
            "layout": {"struct": "spreadsheet", "nargs": 1}, "units": {},
        }, _envelope(project, exe, "remote-engine"))
        assert not result["success"]
        assert _error_code(result) == "ARTIFACT_HOST_UNVERIFIED"
        assert interp.values.get("filename") is None
        assert interp.import_datasets == 0
    finally:
        daemon.close()


# --------------------------------------------------------------------------
# permission distinction of the lower-level typed engine mirror (adapter level)
# --------------------------------------------------------------------------


def test_generic_typed_setter_route_has_no_artifact_concept(tmp_path, monkeypatch):
    """Documented boundary for T20: ``node.property_set`` is the generic typed
    engine mirror; its gates are engine property metadata plus the effect and
    isolation policy, not artifact registration.  This pins today's designed
    distinction at the adapter level so any future unification (T36) lands as a
    visible contract change instead of a silent difference between routes."""
    from comsol_mcp._g2_engine import property_set

    daemon, _project, root, model, interp, _exe = _make_project(tmp_path, "generic", monkeypatch)
    try:
        target = root / "inputs" / "generic.dat"
        target.write_text("0.0 1.0\n")
        data = property_set(model_worker(model), "m1", _node_path(("func", "interpA")),
                            [{"name": "filename", "value": {"kind": "string", "shape": [],
                                                            "data": str(target)}}])
        assert data["applied"][0]["readback_match"] is True
        assert interp.values["filename"] == str(target)
    finally:
        daemon.close()


def model_worker(model: FNode) -> FakeWorker:
    return FakeWorker(model)


# --------------------------------------------------------------------------
# negative control for the redaction itself: a real engine readback mismatch
# must stay visible as a mismatch and must never be rewritten to the expected
# registered path (the fabrication this test guards against is the failure mode
# where redaction silently becomes observation fabrication).
# --------------------------------------------------------------------------


def test_data_import_engine_mismatch_is_reported_not_fabricated(tmp_path, monkeypatch):
    daemon, project, root, model, _interp, exe = _make_project(tmp_path, "mismatch", monkeypatch)
    try:
        (root / "inputs" / "gridm.dat").write_text("0.0 1.0\n")
        registered = _register(daemon, project, "inputs/gridm.dat",
                               role="interpolation_data", key="regm")
        assert registered["success"], registered
        managed_rel = registered["data"]["path"]
        managed_abs = str(root / managed_rel)

        class MismatchNode(FNode):
            """The engine accepts the write but reads back a different value."""
            def set(self, *args: Any) -> None:
                if len(args) == 2 and args[0] == "filename":
                    self.values["filename"] = "/somewhere/engine-normalized.dat"
                    return
                super().set(*args)

        model.collections["func"].items["interpA"] = MismatchNode(
            tag="interpA", type_id="Interpolation", value_types=dict(INTERPOLATION_META))

        result = _call(daemon, "function.data_import", {
            "path": _node_path(("func", "interpA")),
            "artifact_id": registered["data"]["artifact_id"],
            "layout": {"struct": "spreadsheet", "nargs": 1}, "units": {},
        }, _envelope(project, exe, "mismatch-import"))

        # the failure is honestly reported, not smoothed into a success
        assert not result["success"], result
        assert result["data"]["status"] == "FAILED"
        assert result["data"]["import_data"]["skipped"] is True

        # the readback row keeps the mismatch visible with the raw value redacted;
        # it is NOT rewritten to the expected registered path
        row = result["data"]["readback"]["filename"]
        assert row["matches_registered_binding"] is False
        assert row["value_redacted"] is True
        assert row["value"] is None

        failed_rows = [row for row in result["data"]["failed"] if row.get("name") == "filename"]
        assert len(failed_rows) == 1
        failed = failed_rows[0]
        assert failed["readback"]["data"] is None
        assert failed["readback"]["data_redacted"] is True
        assert failed["path_binding_to_registered_artifact"] == {"readback": False}
        assert "value" not in failed  # never synthesize a requested-value row that was not emitted

        # no managed absolute path, no raw engine value, and no fabricated expected
        # value are published inside the failed observation
        payload = json.dumps(result)
        assert managed_abs not in payload
        assert "/somewhere/engine-normalized.dat" not in payload
        assert managed_rel not in json.dumps(failed)
        assert interp_import_state(model)["import_datasets"] == 0
    finally:
        daemon.close()


def interp_import_state(model: FNode) -> dict:
    node = model.collections["func"].items["interpA"]
    return {"import_datasets": node.import_datasets}


# --------------------------------------------------------------------------
# public-route boundary probes for the adjacent generic entry points
# --------------------------------------------------------------------------


def test_public_node_property_set_keeps_its_project_write_isolation_gate(tmp_path, monkeypatch):
    """node.property_set is the generic typed engine mirror under the ordinary
    project_write gate.  Its project boundary must stay intact: without a
    satisfied isolation proof the write is refused before any engine call.  The
    adapter-level test above already pins that once an owned server satisfies
    the gate, this route can still write 'filename' — that remains an OPEN
    cross-route gap tracked under T36, not something this fix claims closed."""
    daemon, project, root, _model, interp, exe = _make_project(tmp_path, "propset", monkeypatch)
    try:
        outside = tmp_path / "propset_private.dat"
        outside.write_text("SENTINEL-PRIVATE\n")
        result = _call(daemon, "node.property_set", {
            "path": _node_path(("func", "interpA")),
            "properties": [{"name": "filename",
                            "value": {"kind": "string", "shape": [], "data": str(outside)}}],
        }, _envelope(project, exe, "propset-gate"))
        assert not result["success"], result
        assert _error_code(result) == "ISOLATION_PROOF_REQUIRED"
        assert interp.values.get("filename") is None
    finally:
        daemon.close()


def test_public_api_invoke_cannot_reach_the_unreviewed_setter(tmp_path, monkeypatch):
    """api.invoke only exposes reviewed capabilities; PropFeature.set is not one
    (api.describe publishes it as UNREVIEWED with the controlled alternative
    node.property_set), so the raw mirror cannot write 'filename' either."""
    daemon, project, _root, _model, interp, exe = _make_project(tmp_path, "apiinv", monkeypatch)
    try:
        outside = tmp_path / "apiinv_private.dat"
        outside.write_text("SENTINEL-PRIVATE\n")
        result = _call(daemon, "api.invoke", {
            "path": _node_path(("func", "interpA")), "method": "set",
            "arguments": [{"kind": "string", "shape": [], "data": "filename"},
                          {"kind": "string", "shape": [], "data": str(outside)}],
            "declared_effect": "WRITE",
        }, _envelope(project, exe, "apiinv-set"))
        assert not result["success"], result
        assert _error_code(result) == "API_UNSUPPORTED"
        assert interp.values.get("filename") is None
    finally:
        daemon.close()


def test_data_reload_digest_published_inside_project_boundary(tmp_path, monkeypatch):
    """The data_reload digest withholding only applies outside the managed
    project boundary: a model referencing a file inside the project keeps its
    verified digest publication (no functionality regression)."""
    daemon, project, root, _model, interp, exe = _make_project(tmp_path, "reloadin", monkeypatch)
    try:
        inside = root / "inputs" / "inside_reload.dat"
        inside.parent.mkdir(parents=True, exist_ok=True)
        inside.write_text("0.0 1.0\n2.0 3.0\n")
        interp.values["filename"] = str(inside)
        result = _call(daemon, "function.data_reload", {"path": _node_path(("func", "interpA"))},
                       _envelope(project, exe, "reload-inside"))
        assert result["success"], result
        assert interp.refreshes == 1
        assert result["data"]["sha256"]["status"] == "verified"
        assert result["data"]["sha256"]["value"] == hashlib.sha256(inside.read_bytes()).hexdigest()
    finally:
        daemon.close()
