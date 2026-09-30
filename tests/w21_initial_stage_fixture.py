"""Producer-generated offline fixture for the registered W21 initial stage.

This uses the real ControlDaemon, ExecutionService, OperationStore, ManagedBackend,
G3 producers, and PersistentJavaWorker protocol. The fake transport supplies only
COMSOL calls and never represents native or scientific acceptance.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any, Mapping

from comsol_mcp._execution_contract import model_ref_from_mapping
from comsol_mcp._java_worker import JavaWorkerTimeout

from tests.test_stage_definition import _persistent_worker_with_stub, _plan_v2, _setup


def build_initial_stage_fixture(
    tmp_path: Path, *, fault: str | None = None, runner_profile: bool = False,
    expected_revision: int | None = None,
    stage_define_request_id: str | None = None,
    stage_define_idempotency_key: str | None = None,
    stage_run_request_id: str | None = None,
    stage_run_idempotency_key: str | None = None,
    stage_define_execution_options: Mapping[str, Any] | None = None,
    stage_run_execution_options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a stored first-stage plan through the actual managed producer path."""
    import comsol_mcp._managed_backend as managed_backend

    managed_backend.configured_receipt = lambda: "TEST_ONLY_SYNTHETIC_OWNED_SERVER_RECEIPT"
    managed_backend.verify_owned_server = lambda receipt_path, *, endpoint, worker_pid: {
        "kind": "TEST_ONLY_SYNTHETIC_ISOLATION_PROOF", "receipt_path": receipt_path,
        "endpoint": endpoint, "worker_pid": worker_pid,
    }
    daemon, service, _old_worker, project_id, model_ref, execution, _host = _setup(tmp_path)
    revision = execution["expected_revision"] if expected_revision is None else expected_revision
    if type(revision) is not int or revision < 0:
        daemon.close()
        raise ValueError("expected_revision must be a non-negative integer")
    execution = {**execution, "expected_revision": revision}
    if revision != 0:
        # The runner may start from a frozen model revision established by its
        # earlier fixture/probe operations. Seed that exact pre-run revision in
        # the test-only ledger and its matching durable project attribution.
        service.ledger._state_for(model_ref_from_mapping(model_ref)).revision = revision
        daemon.store.put_metadata("revisions", daemon.backend._model_project_key(model_ref), {
            "model_ref": model_ref, "revision": revision, "dirty": False,
            "attribution": "PROJECT_BOUND", "project_id": project_id,
        })
    service.ledger.permissions.add("evaluate")
    daemon.backend.endpoint_key = "127.0.0.1:56000"
    workspace = Path(daemon.project_authority.get_project(project_id)["workspace"])
    workspace.joinpath("stage_outputs").mkdir(parents=True, exist_ok=True)
    worker_generation = model_ref["generation"] + 70
    state: dict[str, Any] = {"solved": False, "numerical": {}, "selection": {}, "submitted": []}
    transport_requests: list[dict[str, Any]] = []

    def handle(name: str, java_type: str = "Object") -> dict[str, Any]:
        return {"$worker_handle": name, "generation": worker_generation, "java_type": java_type}

    def request(body: dict[str, Any], *, timeout_s: float | None = None) -> dict[str, Any]:
        del timeout_s
        transport_requests.append(dict(body))
        kind = body["type"]
        if (fault == "unknown_after_eval" and kind == "call"
                and body.get("method") == "getStrictFieldReadback"):
            raise JavaWorkerTimeout("fixture lost the strict field response after dispatch")
        result: Any = None
        failure_reply: dict[str, Any] | None = None
        if kind == "health":
            result = {"generation": worker_generation, "instance_id": "initial-worker-fixture",
                      "connected": True, "server": "synthetic-test-fixture"}
        elif kind == "model_snapshot":
            tag = body["tag"]
            result = {"tag": tag, "model_tag": tag, "server_instance_id": "server-stage",
                      "instance_id": "initial-worker-fixture", "generation": worker_generation,
                      "external_event_counter": 0, "fingerprint": "stage-model-fingerprint"}
        elif kind == "model":
            result = handle("model-main", "Model")
        elif kind != "call":
            raise AssertionError(f"unexpected Worker command: {body}")
        else:
            receiver, method, args = body["handle"], body["method"], body.get("args", [])
            # Model collections and owned output persistence.
            if receiver == "model-main":
                if method == "study":
                    result = handle("study-list" if not args else "study-std1", "StudyList" if not args else "Study")
                elif method == "sol":
                    tags = ["sol1"] if state["solved"] else []
                    result = handle("solver-list", "SolverSequenceList") if not args else handle("solver-sol1", "SolverSequence")
                elif method == "result": result = handle("result", "Result")
                elif method == "modelNode": result = handle("model-node-list", "ModelNodeList")
                elif method == "component": result = handle("component-list" if not args else "component-comp1", "ComponentList" if not args else "Component")
                elif method == "geom": result = handle("geometry-list" if not args else "geometry-geom1", "GeomList" if not args else "GeomSequence")
                elif method == "save":
                    target = Path(args[0]); target.parent.mkdir(parents=True, exist_ok=True)
                    with zipfile.ZipFile(target, "w") as archive:
                        archive.writestr("fixture.txt", "offline fake Worker output")
                else: result = None
            elif receiver == "study-list":
                if method == "tags": result = ["std1"]
                elif method == "get" and args == ["std1"]: result = handle("study-std1", "Study")
            elif receiver == "study-std1":
                if method == "feature": result = handle("study-feature-list", "StudyFeatureList")
                elif method == "label": result = "Study 1"
                elif method == "getSolverSequences": result = ["sol1"] if state["solved"] else []
                elif method == "run": state["solved"] = True
                elif method == "properties": result = []
                else: result = None
            elif receiver == "study-feature-list":
                if method == "tags": result = ["time"]
                elif method == "get" and args == ["time"]: result = handle("study-time", "StudyFeature")
            elif receiver == "study-time":
                if method in {"type", "getType"}: result = "Transient"
                elif method == "label": result = "Time Dependent"
                elif method in {"properties", "getEntryKeys"}: result = []
                elif method == "solveFor": result = True
                elif method == "getDoubleArray" and args == ["tlist"]:
                    if fault in {"tlist_timeout", "tlist_unknown"}:
                        raise JavaWorkerTimeout("fixture lost the optional tlist property response after dispatch")
                    if fault == "tlist_success":
                        result = [0.0, 1.0]
                    else:
                        message = ("IllegalArgumentException: tlist is a range expression and cannot be read as a double array"
                                   if fault != "tlist_unrelated_failure"
                                   else "IllegalArgumentException: unrelated study property cannot be read as a double array")
                        failure_reply = {
                            "code": "ENGINE_CALL_FAILED", "message": message,
                            "execution_state_unknown": True,
                        }
                else: result = None
            elif receiver == "solver-list":
                if method == "tags": result = ["sol1"] if state["solved"] else []
                elif method == "get" and args == ["sol1"]: result = handle("solver-sol1", "SolverSequence")
            elif receiver == "solver-sol1":
                if method == "study": result = "std1"
                elif method == "isAttached": result = True
                elif method == "feature":
                    result = (handle("variables-v1", "SolverFeature") if args
                              else handle("solver-feature-list", "SolverFeatureList"))
                elif method == "getPVals": result = [1.0]
                elif method == "getSolutioninfo": result = handle("solution-info", "SolutionInfo")
                elif method == "getMesh": result = "mesh2" if fault == "wrong_mesh" else "mesh1"
                elif method == "label": result = "Solution 1"
                elif method == "properties": result = []
                else: result = None
            elif receiver == "solver-feature-list":
                if method == "tags": result = ["v1", "v2"] if fault == "ambiguous_variables" else ["v1"]
                elif method == "get" and args == ["v1"]: result = handle("variables-v1", "SolverFeature")
                elif method == "get" and args == ["v2"] and fault == "ambiguous_variables":
                    result = handle("variables-v2", "SolverFeature")
            elif receiver == "variables-v1":
                if method in {"type", "getType"}: result = "Variables"
                elif method == "isActive": result = fault != "inactive_variables"
                elif method == "getVariablesXmeshReadback":
                    if fault == "xmesh_timeout":
                        raise JavaWorkerTimeout("fixture lost Variables.xmeshInfo/clearXmesh response after dispatch")
                    result = {"status": "VERIFIED", "feature_tag": "v1", "feature_active": True,
                              "scope": "variables_solved_for_dofs", "mesh_cases": ["main"],
                              "n_dofs": 9, "field_names": ["T"], "field_n_dofs": [9],
                              "geometries": ["geom1"], "cleanup": "clearXmesh"}
                    if fault == "missing_dof":
                        result.update(field_names=["u"], field_n_dofs=[9])
                elif method == "feature": result = handle("variables-feature-list", "SolverFeatureList")
                elif method == "properties": result = []
                elif method == "problem": result = handle("empty-problem-list", "ProblemList")
                else: result = None
            elif receiver == "variables-v2":
                if method in {"type", "getType"}: result = "Variables"
                elif method == "isActive": result = True
                elif method == "getVariablesXmeshReadback":
                    if fault == "xmesh_timeout":
                        raise JavaWorkerTimeout("fixture lost Variables.xmeshInfo/clearXmesh response after dispatch")
                    result = {"status": "VERIFIED", "feature_tag": "v2", "feature_active": True,
                              "scope": "variables_solved_for_dofs", "mesh_cases": ["main"],
                              "n_dofs": 9, "field_names": ["T"], "field_n_dofs": [9],
                              "geometries": ["geom1"], "cleanup": "clearXmesh"}
                elif method == "feature": result = handle("variables-feature-list", "SolverFeatureList")
                elif method == "properties": result = []
                elif method == "problem": result = handle("empty-problem-list", "ProblemList")
                else: result = None
            elif receiver in {"empty-problem-list", "variables-feature-list"}:
                if method == "tags": result = []
                else: result = None
            # Result datasets and selected solution axes.
            elif receiver == "result":
                if method == "dataset": result = handle("dataset-list", "DatasetList")
                elif method == "numerical": result = handle("numerical-list", "NumericalFeatureList")
            elif receiver == "dataset-list":
                if method == "tags": result = ["dset1"]
                elif method == "get" and args == ["dset1"]: result = handle("dataset-dset1", "Dataset")
            elif receiver == "dataset-dset1":
                if method == "getType": result = "Solution"
                elif method == "properties": result = ["solution", "comp", "geom"]
                elif method == "getString":
                    result = {"solution": "sol1", "comp": "comp1", "geom": "geom1",
                              "frametype": "spatial"}.get(args[0])
                    if fault == "bad_frame" and args[0] == "frametype": result = "unsupported-frame"
            elif receiver == "solution-info":
                if method == "getOuterSolnum": result = [1]
                elif method == "getSolnum": result = [1]
                elif method == "getPNames": result = [["t"]]
                elif method == "getPvals": result = [[1.0]]
                elif method == "getUnits": result = [["s"]]
                elif method == "getLevelNames": result = ["t"]
                elif method == "getISol": result = [0, 0]
            # Model component, geometry, and all-selection membership.
            elif receiver == "model-node-list":
                if method == "tags": result = ["comp1"]
            elif receiver == "component-list":
                if method == "tags": result = ["comp1"]
                elif method == "get" and args == ["comp1"]: result = handle("component-comp1", "Component")
            elif receiver == "component-comp1":
                if method == "geom": result = handle("geometry-list" if not args else "geometry-geom1", "GeomList" if not args else "GeomSequence")
                elif method == "mesh": result = handle("mesh-list" if not args else "mesh-mesh1", "MeshList" if not args else "MeshSequence")
                elif method == "measure": result = handle("measure", "GeomMeasureFinal")
            elif receiver == "geometry-list":
                if method == "tags": result = ["geom1"]
                elif method == "get" and args == ["geom1"]: result = handle("geometry-geom1", "GeomSequence")
            elif receiver == "geometry-geom1":
                if method == "getSDim": result = 2
                elif method == "lengthUnit": result = "m"
                elif method == "isAxisymmetric": result = False
            elif receiver == "measure":
                if method == "selection": result = handle("measure-selection", "MeshSelection")
            elif receiver == "measure-selection":
                if method in {"geom", "set", "all"}: result = None
                elif method == "entities": result = [1]
            # Actual transient numerical Eval calls and automatically inferred units.
            elif receiver == "numerical-list":
                if method == "tags": result = list(state["numerical"])
                elif method == "create":
                    tag, feature_type = args; assert feature_type == "Eval"
                    state["numerical"][tag] = {}; state["selection"][tag] = {"geometry": None, "dimension": None, "entities": []}
                    result = handle(f"numerical-{tag}", "NumericalFeature")
                elif method == "remove": state["numerical"].pop(args[0], None)
            elif receiver.startswith("numerical-"):
                tag = receiver.removeprefix("numerical-"); props = state["numerical"][tag]
                if method == "set": props[args[0]] = args[1]
                elif method == "selection": result = handle(f"selection-{tag}", "Selection")
                elif method == "tag": result = tag
                elif method == "properties": result = []
                elif method == "getString": result = "manual" if args[0] in {"outerinput", "innerinput"} else props.get(args[0])
                elif method == "getInt":
                    result = props.get(args[0], 1)
                    if fault == "wrong_tuple" and args[0] == "solnum": result = int(result) + 1
                elif method == "getStringArray":
                    if args[0] == "expr": result = list(props.get("expr", []))
                    elif args[0] == "unit":
                        expressions = list(props.get("expr", []))
                        units = ["K", "1", "1"]
                        if fault == "wrong_unit": units = ["J", "1", "1"]
                        elif fault == "forced_unit": units = ["K", "K", "1"]
                        elif fault == "missing_unit": units = ["K", "", "1"]
                        result = units[:len(expressions)]
                elif method == "isComplex": result = False
                elif method == "getCoordinatesShape": result = {"kind": "coordinates_shape", "shape": [2, 3], "dimension": 2,
                    "point_count": 3, "source": "native NumericalFeature.getCoordinates()", "values_transmitted": False, "wire_payload": "shape-only"}
                elif method == "getStrictFieldReadback":
                    expressions = list(props.get("expr", [])); values = []
                    for expression in expressions:
                        if expression == "1": values.append([[1.0, 1.0, 1.0]])
                        elif fault == "wrong_control_numeric" and expression.startswith("(T)"):
                            values.append([[300.5, 301.5, 302.5]])
                        else: values.append([[300.0, 301.0, 302.0]])
                    result = {"layout": "expression,solnum,point", "shape": [len(expressions), 1, 3],
                              "is_complex": False, "real": values, "imag": None,
                              "coordinates": [[0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                              "numeric_scalar_count": len(expressions) * 3 + 6, "json_payload_bytes": 512}
                elif method == "run": result = None
            elif receiver.startswith("selection-"):
                tag = receiver.removeprefix("selection-"); selected = state["selection"][tag]
                if method == "geom" and len(args) == 2: selected["geometry"], selected["dimension"] = args; result = None
                elif method == "geom": result = selected["geometry"]
                elif method in {"set", "all"}: selected["entities"] = [1]; result = None
                elif method == "entities": result = [1]
                elif method == "dim": result = selected["dimension"]
                elif method == "named": result = None
                elif method == "isInheriting": result = False
            elif receiver == "mesh-list":
                if method == "tags": result = ["mesh1"]
                elif method == "get" and args == ["mesh1"]: result = handle("mesh-mesh1", "MeshSequence")
            elif receiver == "mesh-mesh1":
                if method == "geom": result = "geom1"
                elif method == "getSDim": result = 2
                elif method == "getNumVertex": result = 3
                elif method == "getTypes": result = ["tri"]
                elif method == "getNumElem": result = 1
                elif method == "getVertex": result = [[0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
                elif method == "getElem": result = [[0], [1], [2]]
                elif method == "getElemEntity": result = [1]
                elif method == "feature": result = handle("mesh-feature-list", "MeshFeatureList")
                elif method == "properties": result = []
                elif method == "label": result = "Mesh 1"
            elif receiver == "mesh-feature-list":
                if method == "tags": result = []
            elif receiver == "model-main" and method == "save":
                target = Path(args[0]); target.parent.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(target, "w") as archive: archive.writestr("fixture.txt", "offline fake Worker output")
            if result is None and failure_reply is None and method not in {
                "set", "remove", "run", "save", "geom", "all", "named", "label",
                "create", "getEntryKeys", "solveFor", "clearXmesh",
            }:
                # Java null is valid only for optional probe fields; distinguish
                # these from unimplemented calls to keep the fixture strict.
                if method not in {"getString", "getDouble", "getInt", "getStringArray", "getType", "type",
                                  "hasError", "hasWarning", "hasInformation", "hasProblem",
                                  "getLastComputationTime", "getLastComputationDate", "getLastComputationVersion",
                                  "isGenPlots", "isGenConv", "isGenIntermediatePlots", "isStoreSolution",
                                  "isPlotUndefVals", "isStoreCompleteHistory", "problem", "getM", "getN", "getNnz",
                                  "hasProblemOrInformation", "getDefaultSolnum", "getSequenceType", "hasProblems",
                                  "getErrorMessage", "getInformationMessage", "getWarningMessage",
                                  "getPNames", "getPVals", "getParamVals", "getParamNames", "getNStepsBack",
                                  "isEmpty", "isInitialized", "getCoordinatesShape", "isComplex", "tag",
                                  "properties", "getISol", "getMesh", "getNumElem", "getElemEntity", "getVertex",
                                  "getSDim", "getNumVertex", "getTypes", "lengthUnit", "isAxisymmetric",
                                  "current", "isAutomatic", "isComplete", "isInheriting", "entities", "dim",
                                  "objects", "object", "all", "getSolverSequences", "isAttached", "study",
                                  "getOuterSolnum", "getSolnum", "getUnits", "getLevelNames", "getPvals",
                                  "getPVals", "getSolutioninfo", "frametype", "expression", "getCoordinates",
                                  "getNumVertex", "getElem", "getElemEntity", "getSolverSequences",
                                  "getFilePath"}:
                    raise AssertionError(f"unhandled fake Worker call: {receiver}.{method}{args}")
        response = ({"ok": False, "status": "FAILED", "type": kind,
                     "generation": worker_generation, "failure": failure_reply}
                    if isinstance(failure_reply, Mapping)
                    else {"ok": True, "status": "SUCCEEDED", "type": kind,
                          "generation": worker_generation, "result": result})
        if isinstance(body.get("request_id"), str):
            response["request_id"] = (f"mismatch-{body['request_id']}"
                                      if ((fault == "dispatch_mismatch" and kind == "call"
                                           and body.get("method") == "getStrictFieldReadback")
                                          or (fault == "tlist_request_id_mismatch" and kind == "call"
                                              and body.get("method") == "getDoubleArray"
                                              and body.get("args") == ["tlist"]))
                                      else body["request_id"])
        return response

    worker = _persistent_worker_with_stub(workspace, request)
    # Keep the transport stub at the socket boundary while exercising the
    # production request-id guard used by PersistentJavaWorker._request.
    transport = worker._request

    def correlated_transport(body: dict[str, Any], *, timeout_s: float | None = None) -> dict[str, Any]:
        reply = transport(body, timeout_s=timeout_s)
        correlated = worker._validate_request_reply(body, reply)
        request_id = body.get("request_id")
        if request_id is not None and correlated:
            worker._known_requests[str(request_id)] = reply
        return reply

    worker._request = correlated_transport
    worker._generation = worker_generation
    daemon.backend.worker = worker

    class WorkerSnapshotAdapter:
        def model_snapshot(self, tag: str) -> dict[str, Any]:
            return worker.backend_snapshot(tag)

    service.adapter = WorkerSnapshotAdapter()

    def run_study(arguments: dict[str, Any]) -> str:
        model = worker.client().model(model_ref["model_tag"])
        model._call("study", arguments["study_tag"])._call("run")
        return json.dumps({"success": True, "data": {"study_tag": arguments["study_tag"]}})

    def save_model(arguments: dict[str, Any]) -> str:
        model = worker.client().model(model_ref["model_tag"])
        target = Path(arguments["path"])
        model.save(str(target))
        return json.dumps({"success": True, "data": {"saved_path": str(target)}})

    daemon.backend.registry.update({"run_study": run_study, "save_model": save_model})
    definition = _plan_v2()
    stage_id = "preheat"
    if runner_profile:
        # Match the runner's frozen, single-stage initial-state profile while
        # keeping the same public stage-definition and managed-run path.
        stage_id = "thermal_initial"
        definition = {
            "version": 2, "plan_id": "w21_initial_stage", "stages": [{
                "stage_id": stage_id, "ordinal": 1, "depends_on": [],
                "study_target": {"segments": [{"collection": "study", "tag": "std1"}]},
                "source_selection": {"kind": "initial_state", "strategy": "declared_initial"},
                "target_selection": {"dataset": "dset1", "outer": "last", "inner": "last"},
                "mapping_profile": "same_name_same_mesh_initialization",
                "variable_mappings": [{
                    "source_variable": "T", "target_variable": "T",
                    "source_unit": "K", "target_unit": "K", "mapping_method": "identity",
                }],
                "reference_state": {"strategy": "initial_state"}, "checks": [],
            }],
        }
    define_execution = {
        **execution, **dict(stage_define_execution_options or {}),
        "request_id": stage_define_request_id or execution["request_id"],
        "idempotency_key": stage_define_idempotency_key or execution["idempotency_key"],
    }
    define_dispatch = {"operation": "experiment.stage_define", "arguments": {"definition": definition},
                       "execution": define_execution}
    public_dispatches = [json.loads(json.dumps(define_dispatch, allow_nan=False))]
    defined = daemon.dispatch(define_dispatch)
    if defined.get("success") is not True:
        return {"daemon": daemon, "service": service, "worker": worker, "project_id": project_id,
                "model_ref": model_ref, "execution": execution, "transport_requests": transport_requests,
                "stage_id": stage_id, "definition": definition,
                "stage_define_execution": define_execution, "public_dispatches": public_dispatches,
                "defined": defined, "result": None, "attempt": None}
    request_id = stage_run_request_id or "real-initial-stage-request"
    run_execution = {
        **execution, **dict(stage_run_execution_options or {}),
        "request_id": request_id,
        "idempotency_key": stage_run_idempotency_key or request_id,
    }
    run_dispatch = {"operation": "experiment.stage_run", "arguments": {"stage_id": stage_id},
                    "execution": run_execution}
    public_dispatches.append(json.loads(json.dumps(run_dispatch, allow_nan=False)))
    hash_restore = None
    if fault == "artifact_mismatch":
        import comsol_mcp._w21_stage_backend as stage_backend
        hash_restore = stage_backend.hash_saved_artifact

        def hash_then_corrupt(path: Path) -> tuple[str, int]:
            digest, size = hash_restore(path)
            with path.open("ab") as stream:
                stream.write(b"tampered after the daemon's first hash")
            return digest, size

        stage_backend.hash_saved_artifact = hash_then_corrupt
    try:
        result = daemon.dispatch(run_dispatch)
    finally:
        if hash_restore is not None:
            stage_backend.hash_saved_artifact = hash_restore
    attempts = daemon.store.list_stage_attempts(project_id, model_ref, stage_id=stage_id)
    attempt = attempts[-1] if attempts else None
    return {"daemon": daemon, "service": service, "worker": worker, "project_id": project_id,
            "model_ref": model_ref, "execution": execution, "transport_requests": transport_requests,
            "stage_id": stage_id, "definition": definition,
            "stage_define_execution": define_execution, "stage_run_execution": run_execution,
            "public_dispatches": public_dispatches,
            "defined": defined, "result": result, "attempt": attempt, "fault": fault}
