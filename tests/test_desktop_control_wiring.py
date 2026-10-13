from __future__ import annotations

import asyncio

import pytest
from mcp.server.fastmcp import FastMCP

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._desktop_service import (
    AVAILABLE,
    UNSUPPORTED_CONTROL,
    MetadataOnlyDesktopAdapter,
    WindowIdentity,
    WindowObservation,
)
from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._mcp_gateway import GatewayRegistry
from comsol_mcp._tools_desktop import register as register_desktop


class AvailableWindowAdapter(MetadataOnlyDesktopAdapter):
    def __init__(self):
        self.window = WindowObservation(
            WindowIdentity(
                platform="fixture",
                native_window_id="WINDOW:1",
                process_id=4321,
                process_birth="birth:4321",
                login_id="uid:test",
                desktop_session_id="session:1",
                comsol_version="6.4.0.293",
            ),
            title="COMSOL fixture window",
            control_status=AVAILABLE,
        )

    def status(self, runtime_id=None):
        return {"platform": "fixture", "permission_status": AVAILABLE,
                "control_status": AVAILABLE, "metadata_status": AVAILABLE}

    def enumerate_windows(self, runtime_id=None):
        return [self.window]

    def revalidate_window(self, identity):
        return self.window if self.window.identity == identity else None


def _service(root):
    class SnapshotAdapter:
        def model_snapshot(self, tag):
            return {"model_tag": tag, "server_instance_id": "server", "fingerprint": "f" * 64,
                    "external_event_counter": 0}

    return ExecutionService(SessionLedger("session-a", "server"), SnapshotAdapter(), project_root=root)


def _operation_count(daemon):
    return daemon.store.db.execute("SELECT count(*) FROM operations").fetchone()[0]


def _synthetic_png_1x1():
    import struct
    import zlib

    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00\xff")) + chunk(b"IEND", b""))


def test_all_public_desktop_tool_names_route_to_canonical_catalog_ids():
    calls = []
    server = FastMCP("desktop-gateway-test")
    registry = GatewayRegistry(server, lambda op, args, execution: (
        calls.append((op, args, execution)) or {"success": True, "data": {}}
    ))
    register_desktop(registry)

    names = {
        "desktop_status": "desktop.status",
        "desktop_bind": "desktop.bind",
        "desktop_show_model": "desktop.show_model",
        "desktop_select_node": "desktop.select_node",
        "desktop_capture": "desktop.capture",
        "desktop_action": "desktop.action",
        "desktop_shell_execute": "desktop.shell_execute",
        "desktop_migrate_standalone": "desktop.migrate_standalone",
    }
    assert set(names).issubset(server._tool_manager._tools)
    assert set(names.values()).issubset(registry.functions)

    tool = server._tool_manager._tools["desktop_status"]
    assert tool.parameters["required"] == ["project_id"]
    response = asyncio.run(tool.fn(project_id="project-a"))
    assert response.structuredContent["success"] is True
    assert calls == [("desktop.status", {"project_id": "project-a", "request_id": "", "runtime_id": ""}, {})]

    bind = server._tool_manager._tools["desktop_bind"]
    asyncio.run(bind.fn(project_id="p", idempotency_key="k", window_ref="w", model_ref="m"))
    assert calls[-1][0] == "desktop.bind"
    assert calls[-1][1]["verification"] == {}
    asyncio.run(bind.fn(project_id="p", idempotency_key="k2", window_ref="w", model_ref="m", verification=None))
    assert "verification" in calls[-1][1] and calls[-1][1]["verification"] is None


def test_real_server_registration_exposes_catalog_desktop_tool_names():
    import comsol_mcp.mcp_server as server_module

    expected = {
        "desktop_status", "desktop_bind", "desktop_show_model", "desktop_select_node",
        "desktop_capture", "desktop_action", "desktop_shell_execute", "desktop_migrate_standalone",
    }
    tools = server_module.mcp._tool_manager._tools
    assert expected.issubset(tools)
    assert server_module._gateway.functions["desktop.status"].__name__ == "desktop_status"
    assert tools["desktop_status"].parameters["required"] == ["project_id"]


def test_status_and_nested_status_use_read_only_control_route(tmp_path):
    for outer_operation, arguments in (
        ("desktop.status", {"project_id": "project-a"}),
        ("registry_call", {"operation_id": "desktop.status", "arguments": {}}),
        ("operation_call", {"operation_id": "desktop.status", "arguments": {}}),
    ):
        daemon = ControlDaemon(tmp_path / outer_operation, desktop_adapter=AvailableWindowAdapter())
        try:
            execution = {"project_id": "project-a"} if outer_operation != "desktop.status" else {}
            result = daemon.dispatch({"operation": outer_operation, "arguments": arguments, "execution": execution})
            assert result["success"] is True
            assert result["data"]["adapter"]["control_status"] == AVAILABLE
            assert result["data"]["windows"][0]["window_ref"] is not None
            assert _operation_count(daemon) == 0
        finally:
            daemon.close()


@pytest.mark.parametrize(("operation", "arguments"), [
    ("desktop.bind", {"window_ref": "forged", "model_ref": "model-ref"}),
    ("desktop.show_model", {"window_ref": "forged", "model_ref": "model-ref"}),
    ("desktop.select_node", {"window_ref": "forged", "path": {"segments": [{"accessor": "geometry"}]}}),
    ("desktop.capture", {"window_ref": "forged", "region": "graphics"}),
    ("desktop.action", {"window_ref": "forged", "action": {"action_id": "unknown"}}),
    ("desktop.shell_execute", {"window_ref": "forged", "source_artifact": "artifact", "expected_model_ref": "model-ref"}),
    ("desktop.migrate_standalone", {"window_ref": "forged", "target_session_id": "target", "save_policy": {
        "mode": "save_copy", "destination_path": "/tmp/copy.mph", "overwrite": False,
    }}),
])
def test_seven_host_control_actions_require_exact_host_grant_without_claims(tmp_path, operation, arguments):
    daemon = ControlDaemon(tmp_path, desktop_adapter=AvailableWindowAdapter())
    try:
        response = daemon.dispatch({
            "operation": operation,
            "arguments": {"project_id": "project-a", "idempotency_key": "must-not-start", **arguments},
            "execution": {},
        })
        assert response["success"] is False
        assert response["error"]["code"] == "PERMISSION_REQUIRED"
        assert "HOST_CONTROL" in response["error"]["message"]
        assert _operation_count(daemon) == 0
    finally:
        daemon.close()


def test_durable_bind_refusal_is_a_replayed_failure_not_unknown(tmp_path):
    service = _service(tmp_path)
    service.ledger.permissions.add("host_control")
    daemon = ControlDaemon(tmp_path / "daemon", service=service, desktop_adapter=AvailableWindowAdapter())
    try:
        status = daemon.dispatch({"operation": "desktop.status", "arguments": {"project_id": "p"}, "execution": {}})
        token = status["data"]["windows"][0]["window_ref"]
        request = {
            "operation": "desktop.bind",
            "arguments": {"project_id": "p", "idempotency_key": "bind-1", "window_ref": token,
                          "model_ref": "catalog-model-ref"},
            "execution": {},
        }
        first = daemon.dispatch(request)
        replay = daemon.dispatch(request)
        assert first["success"] is False
        assert first["error"]["code"] == "UNSUPPORTED_CONTROL"
        assert first["execution"]["status"] == "FAILED"
        assert first == replay
        assert _operation_count(daemon) == 1

        conflict = {**request, "arguments": {**request["arguments"], "model_ref": "different-ref"}}
        rejected = daemon.dispatch(conflict)
        assert rejected["success"] is False
        assert rejected["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert _operation_count(daemon) == 1
    finally:
        daemon.close()


def test_desktop_registry_metadata_does_not_advertise_unimplemented_native_controls():
    from comsol_mcp import _g2_registry

    status = _g2_registry.registry_describe("desktop.status")
    assert status["executable"] is True
    assert status["runtime_dispatch_contract"]["verification_scope"].find("NOT_RUN") >= 0
    for operation in (
        "desktop.bind", "desktop.show_model", "desktop.select_node", "desktop.capture",
        "desktop.action", "desktop.shell_execute", "desktop.migrate_standalone",
    ):
        descriptor = _g2_registry.registry_describe(operation)
        assert descriptor["executable"] is False
        assert descriptor["implementation_status"] == "PROPOSED_NOT_IMPLEMENTED"
        entry = _g2_registry.validate_call(operation, {
            "project_id": "p", "idempotency_key": "key", "window_ref": "w",
            **({"path": {"segments": [{"accessor": "geometry"}]}} if operation == "desktop.select_node" else {}),
            **({"region": "graphics"} if operation == "desktop.capture" else {}),
            **({"action": {"id": "candidate"}} if operation == "desktop.action" else {}),
            **({"source_artifact": "a", "expected_model_ref": "m"} if operation == "desktop.shell_execute" else {}),
            **({"target_session_id": "target", "save_policy": {"mode": "save_copy", "destination_path": "/tmp/a", "overwrite": False}}
               if operation == "desktop.migrate_standalone" else {}),
            **({"model_ref": "m"} if operation in {"desktop.bind", "desktop.show_model"} else {}),
        }, allow_coordinator_only=True)
        assert entry.operation_id == operation

    with pytest.raises(Exception):
        _g2_registry.validate_call("desktop.bind", {
            "project_id": "p", "idempotency_key": "key", "window_ref": "w", "model_ref": "m",
        })


def _desktop_claim(daemon, project='p'):
    from comsol_mcp._desktop_service import DesktopDispatchContext
    record, reused=daemon.store.begin(request_id='desktop-r',idempotency_key='desktop-k',request_hash='desktop-h',
                                     operation='desktop.bind',metadata={'project_id':project})
    daemon.store.update_job(record['job_id'],'RUNNING')
    return DesktopDispatchContext('desktop.bind',record['request_id'],record['operation_id'],record['job_id'],
                                  record['idempotency_key'],record['request_hash'],project)


def _desktop_read_fixture(tmp_path):
    service=_service(tmp_path)
    service.ledger.permissions.add('host_control')
    ref=service.bind_model('m')['execution']['model_ref']
    daemon=ControlDaemon(tmp_path/'daemon',service=service)
    daemon.backend.endpoint_key='127.0.0.1:2036'
    daemon.backend._bind_model_project(ref,'p')
    daemon.store.put_metadata('revisions',daemon.backend._model_project_key(ref),{'model_ref':ref,'project_id':'p','revision':0})
    # Project authorization is host-injected software fixture; no GUI/process exists.
    daemon.project_authority.authorize_operation=lambda project,permission: {'workspace':str(tmp_path)} if project=='p' else (_ for _ in ()).throw(RuntimeError('foreign project'))
    return daemon,service,ref


def test_desktop_queued_identity_uses_fresh_scoped_witness_without_second_claim(tmp_path, monkeypatch):
    import json
    daemon,service,ref=_desktop_read_fixture(tmp_path)
    queued = []
    submit = daemon.session_scheduler.submit
    def scoped_submit(context, callback, *args, **kwargs):
        queued.append(context)
        return submit(context, callback, *args, **kwargs)
    monkeypatch.setattr(daemon.session_scheduler, 'submit', scoped_submit)
    monkeypatch.setattr(daemon.queue, 'submit', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('legacy Desktop READ must use explicit None session scheduler')))
    try:
        context=_desktop_claim(daemon)
        before=_operation_count(daemon)
        result=daemon._desktop_engine_queue('desktop.binding.resolve',{'project_id':'p','model_ref_id':json.dumps(ref)},None,context)
        assert result['success'] is True and result['data']['model_ref']==ref
        assert result['data']['fingerprint_coverage']=='parameters+shallow_tree_identity'
        assert result['data']['cas_guarantee']=='NONE'
        assert result['data']['Desktop_model_binding']=='UNVERIFIED_BY_MANAGED_INSPECTION_ALONE'
        assert _operation_count(daemon)==before==1
        assert daemon.store.get_operation(context.operation_id)['status']=='RUNNING'
        assert queued == [None]
    finally:daemon.close()


@pytest.mark.parametrize('case',['no_claim','wrong_project','wrong_epoch','unattributed','dirty','active','external_change'])
def test_desktop_identity_refuses_unproven_or_unsafe_model_before_binding(tmp_path,case):
    import json
    from comsol_mcp._execution_contract import model_ref_from_mapping
    daemon,service,ref=_desktop_read_fixture(tmp_path)
    try:
        context=_desktop_claim(daemon)
        if case=='no_claim':daemon.store.update_job(context.job_id,'FAILED')
        if case=='wrong_epoch':ref['generation']+=1
        if case=='unattributed':daemon.backend.model_project_binding=lambda _: {'attribution':'UNATTRIBUTED'}
        if case=='dirty':service.ledger._state_for(model_ref_from_mapping(ref)).dirty=True
        if case=='active':service.ledger._state_for(model_ref_from_mapping(ref)).active_operation_id='other'
        if case=='external_change':service.adapter.model_snapshot=lambda tag:{'model_tag':tag,'server_instance_id':'server','fingerprint':'changed','external_event_counter':1}
        args={'project_id':'foreign' if case=='wrong_project' else 'p','model_ref_id':json.dumps(ref)}
        expected={'no_claim':'OWNER_UNVERIFIED','wrong_project':'PROJECT_IDENTITY_MISMATCH','wrong_epoch':'MODEL_IDENTITY_MISMATCH','unattributed':'MODEL_IDENTITY_MISMATCH','dirty':'EXECUTION_STATE_UNKNOWN','active':'EXECUTION_STATE_UNKNOWN','external_change':'EXECUTION_STATE_UNKNOWN'}[case]
        with pytest.raises(Exception) as caught:daemon._desktop_engine_queue('desktop.binding.resolve',args,None,context)
        assert caught.value.code==expected
        assert _operation_count(daemon)==1
    finally:daemon.close()


@pytest.mark.parametrize('operation',['desktop.shell_execute','model.load'])
def test_native_shell_load_still_refuse_without_single_claim_provider(tmp_path,operation):
    daemon=ControlDaemon(tmp_path/'daemon')
    try:
        result=daemon._desktop_engine_queue(operation,{},None,None)
        assert result['success'] is False and result['data']['engine_dispatched'] is False
        assert _operation_count(daemon)==0
    finally:daemon.close()


def test_save_copy_authorizer_reuses_project_path_policy_no_overwrite(tmp_path):
    from pathlib import Path
    daemon=ControlDaemon(tmp_path/'daemon')
    daemon.project_authority.authorize_operation=lambda p,e: {'workspace':str(tmp_path)}
    try:
        new=tmp_path/'copy.mph'
        assert daemon._authorize_desktop_save_copy('p',new) is True
        new.write_bytes(b'protected')
        assert daemon._authorize_desktop_save_copy('p',new) is False
        assert daemon._authorize_desktop_save_copy('p',tmp_path.parent/'escape.mph') is False
        assert daemon._authorize_desktop_save_copy('p',tmp_path/'.git'/'copy.mph') is False
        link=tmp_path/'linked';link.symlink_to(tmp_path)
        assert daemon._authorize_desktop_save_copy('p',link/'new.mph') is False
        assert new.read_bytes()==b'protected'
    finally:daemon.close()


def test_control_daemon_save_copy_publishes_only_under_running_migration_claim(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    from comsol_mcp._desktop_service import DesktopDispatchContext, _create_private_save_copy_stage, _verified_private_save_copy_file

    daemon, _service_value, _ref = _desktop_read_fixture(tmp_path)
    daemon.project_authority.authorize_operation = lambda project, permission: (
        {"workspace": str(tmp_path)} if project == "p" and permission == "project_write" else
        (_ for _ in ()).throw(RuntimeError("scope denied")))

    def begin_claim(key, destination):
        arguments = {"project_id": "p", "idempotency_key": key, "window_ref": "software-only",
                     "target_session_id": "target", "save_policy": {
                         "mode": "save_copy", "destination_path": str(destination), "overwrite": False,
                     }}
        record, _reused = daemon.store.begin(
            request_id=f"{key}-request", idempotency_key=key, request_hash=f"{key}-hash",
            operation="desktop.migrate_standalone", metadata={"project_id": "p"},
        )
        daemon.store.update_job(record["job_id"], "RUNNING")
        context = DesktopDispatchContext(
            "desktop.migrate_standalone", record["request_id"], record["operation_id"], record["job_id"],
            record["idempotency_key"], record["request_hash"], "p",
        )
        daemon.desktop._active_operations.add(context.operation_id)
        scope = {"operation": context.operation, "arguments_json": json.dumps(arguments, sort_keys=True),
                 "hash": context.request_hash}
        return context, scope

    try:
        destination = tmp_path / "published" / "saved.mph"
        destination.parent.mkdir()
        context, scope = begin_claim("migration-publish-positive", destination)
        stage_root, _root_identity = _create_private_save_copy_stage(context)
        stage_path = stage_root / "model.mph"
        payload = b"synthetic native Save Copy bytes"
        stage_path.write_bytes(payload)
        stage_receipt = _verified_private_save_copy_file(
            stage_root, stage_path, _root_identity, completion_verified=True,
        )
        token = daemon._desktop_request_scope.set(scope)
        try:
            result = daemon._publish_desktop_save_copy(
                "p", context, stage_root, stage_path, destination,
                {**stage_receipt, "stage_root_identity": _root_identity},
            )
        finally:
            daemon._desktop_request_scope.reset(token)
        assert result["operation_id"] == context.operation_id
        assert result["publication"] == "ARTIFACTSTORE_NO_CLOBBER"
        assert result["sha256"] == stage_receipt["sha256"]
        assert result["size_bytes"] == len(payload)
        assert destination.read_bytes() == payload

        raced_destination = tmp_path / "published" / "foreign-winner.mph"
        raced_context, raced_scope = begin_claim("migration-publish-race", raced_destination)
        raced_root, raced_root_identity = _create_private_save_copy_stage(raced_context)
        raced_source = raced_root / "model.mph"
        raced_source.write_bytes(b"second staged model")
        raced_receipt = _verified_private_save_copy_file(
            raced_root, raced_source, raced_root_identity, completion_verified=True,
        )
        import comsol_mcp._artifact_store as artifact_store
        actual_publisher = artifact_store._publish_project_chunks_no_clobber

        def install_foreign_leaf(root, relative, chunks, *, expected_sha256, expected_size):
            raced_destination.write_bytes(b"foreign winner")
            return actual_publisher(root, relative, chunks,
                                    expected_sha256=expected_sha256, expected_size=expected_size)

        monkeypatch.setattr(artifact_store, "_publish_project_chunks_no_clobber", install_foreign_leaf)
        token = daemon._desktop_request_scope.set(raced_scope)
        try:
            with pytest.raises(Exception) as caught:
                daemon._publish_desktop_save_copy(
                    "p", raced_context, raced_root, raced_source, raced_destination,
                    {**raced_receipt, "stage_root_identity": raced_root_identity},
                )
        finally:
            daemon._desktop_request_scope.reset(token)
        assert getattr(caught.value, "code", None) == "SAVE_COPY_PUBLISH_UNKNOWN"
        assert raced_destination.read_bytes() == b"foreign winner"
    finally:
        daemon.close()


def test_registered_desktop_source_reuses_actual_artifact_integrity_and_project_scope(tmp_path):
    from comsol_mcp._artifact_store import register_project_artifact,local_artifact_host_identity,local_engine_host_identity
    from comsol_mcp._desktop_service import TrustedSourceArtifact
    daemon,service,ref=_desktop_read_fixture(tmp_path)
    try:
        original=tmp_path/'trusted.java';original.write_bytes(b'// synthetic trusted source')
        record=register_project_artifact(tmp_path,daemon.store,'trusted.java',project_id='p',role='java-source',
                classification='synthetic',host_identity=local_artifact_host_identity(),
                engine_host_identity=local_engine_host_identity(daemon.backend.endpoint_key),request_id='register-r',registering_operation_id='register-o',
                artifact_type='text/x-java-source')
        artifact=daemon._resolve_desktop_artifact('p',record['artifact_id'])
        assert isinstance(artifact,TrustedSourceArtifact) and artifact.sha256==record['artifact_id']
        assert artifact.size==len(b'// synthetic trusted source')
        with pytest.raises(Exception):daemon._resolve_desktop_artifact('foreign',record['artifact_id'])
        # Alter the immutable registered copy rather than the retained input.
        from pathlib import Path
        (tmp_path/record['path']).write_bytes(b'changed')
        with pytest.raises(Exception):daemon._resolve_desktop_artifact('p',record['artifact_id'])
        assert _operation_count(daemon)==0
    finally:daemon.close()


def test_coordinator_bind_uses_single_claim_and_queue_inspect_with_fake_window_only(tmp_path):
    import json
    from comsol_mcp._desktop_service import DesktopModelObservation
    daemon,service,ref=_desktop_read_fixture(tmp_path)
    adapter=AvailableWindowAdapter()
    adapter.observe_model=lambda _: DesktopModelObservation('SERVER','127.0.0.1:2036','m',fingerprint='f'*64)
    daemon.desktop.adapter=adapter
    try:
        status=daemon.dispatch({'operation':'desktop.status','arguments':{'project_id':'p'},'execution':{}})
        token=status['data']['windows'][0]['window_ref']
        request={'operation':'desktop.bind','arguments':{'project_id':'p','idempotency_key':'real-queue-fixture',
                 'window_ref':token,'model_ref':json.dumps(ref)},'execution':{}}
        result=daemon.dispatch(request)
        assert result['success'] is True and result['execution']['status']=='SUCCEEDED'
        assert _operation_count(daemon)==1
        assert daemon.dispatch(request)==result
        assert _operation_count(daemon)==1
    finally:daemon.close()


def test_control_daemon_label_challenge_uses_durable_worker_lane_and_restores_exact_label(tmp_path):
    from contextlib import contextmanager
    import json
    from comsol_mcp._desktop_service import DesktopModelObservation
    from comsol_mcp._execution_contract import model_ref_from_mapping

    daemon, service, ref = _desktop_read_fixture(tmp_path)
    service.ledger.permissions.update({"inspect", "host_control"})
    service.ledger.model_ownership[ref["model_tag"]] = "mcp_owned"
    label = {"value": "owned-copy"}
    worker_calls = []

    class Model:
        def label(self, value=None):
            if value is None:
                worker_calls.append(("get", label["value"]))
                return label["value"]
            worker_calls.append(("set", value))
            label["value"] = value
            return None

    class Worker:
        def __init__(self):
            self._model = Model()

        def client(self):
            return self

        def model(self, tag):
            assert tag == ref["model_tag"]
            return self._model

        @contextmanager
        def operation_context(self, _operation_id, on_request_event=None):
            yield

    daemon.backend.worker = Worker()
    adapter = AvailableWindowAdapter()
    adapter.observe_model = lambda _identity: DesktopModelObservation("UNVERIFIED", active_model_label=label["value"])
    daemon.desktop.adapter = adapter
    try:
        status = daemon.dispatch({"operation": "desktop.status", "arguments": {"project_id": "p"}, "execution": {}})
        window_ref = status["data"]["windows"][0]["window_ref"]
        bound = daemon.dispatch({"operation": "desktop.bind", "arguments": {
            "project_id": "p", "idempotency_key": "label-challenge-bind", "window_ref": window_ref,
            "model_ref": json.dumps(ref),
        }, "execution": {}})
        assert bound["success"] is True
        assert bound["data"]["binding_evidence"]["binding_method"] == "WORKER_LABEL_NONCE_CHALLENGE"
        assert bound["data"]["binding_evidence"]["revision"] == 2
        assert label["value"] == "owned-copy"
        assert [kind for kind, _value in worker_calls].count("set") == 2
        nonce = next(value for kind, value in worker_calls if kind == "set")
        assert nonce.startswith("MCPBIND:") and len(nonce) == 40
        state = service.ledger._state_for(model_ref_from_mapping(ref))
        assert state.revision == 2 and state.dirty is False and state.active_operation_id is None
        operation = daemon.store.get_operation(bound["execution"]["operation_id"])
        assert operation["status"] == "SUCCEEDED"
    finally:
        daemon.close()


@pytest.mark.parametrize("case", ["user_owned", "dirty"])
def test_label_challenge_refuses_nonowned_or_uncertain_model_before_worker_mutation(tmp_path, case):
    from contextlib import contextmanager
    import json
    from comsol_mcp._desktop_service import DesktopModelObservation
    from comsol_mcp._execution_contract import model_ref_from_mapping

    daemon, service, ref = _desktop_read_fixture(tmp_path)
    service.ledger.permissions.update({"inspect", "host_control"})
    if case == "dirty":
        service.ledger._state_for(model_ref_from_mapping(ref)).dirty = True
        service.ledger.model_ownership[ref["model_tag"]] = "mcp_owned"
    label = {"value": "owned-copy"}
    worker_calls = []

    class Model:
        def label(self, value=None):
            worker_calls.append(("get" if value is None else "set", value))
            if value is not None:
                label["value"] = value
            return label["value"]

    class Worker:
        def client(self):
            return self

        def model(self, _tag):
            return Model()

        @contextmanager
        def operation_context(self, _operation_id, on_request_event=None):
            yield

    daemon.backend.worker = Worker()
    adapter = AvailableWindowAdapter()
    adapter.observe_model = lambda _identity: DesktopModelObservation("UNVERIFIED", active_model_label=label["value"])
    daemon.desktop.adapter = adapter
    try:
        status = daemon.dispatch({"operation": "desktop.status", "arguments": {"project_id": "p"}, "execution": {}})
        window_ref = status["data"]["windows"][0]["window_ref"]
        result = daemon.dispatch({"operation": "desktop.bind", "arguments": {
            "project_id": "p", "idempotency_key": "label-challenge-deny-" + case, "window_ref": window_ref,
            "model_ref": json.dumps(ref),
        }, "execution": {}})
        assert result["success"] is False
        assert result["execution"]["status"] == "FAILED"
        assert worker_calls == []
        assert label["value"] == "owned-copy"
        assert daemon.store.get_operation(result["execution"]["operation_id"])["status"] == "FAILED"
    finally:
        daemon.close()


def test_label_challenge_readback_loss_is_unknown_and_idempotency_prevents_retry(tmp_path):
    from contextlib import contextmanager
    import json
    from comsol_mcp._desktop_service import DesktopModelObservation
    from comsol_mcp._execution_contract import model_ref_from_mapping

    daemon, service, ref = _desktop_read_fixture(tmp_path)
    service.ledger.permissions.update({"inspect", "host_control"})
    service.ledger.model_ownership[ref["model_tag"]] = "mcp_owned"
    label = {"value": "owned-copy"}
    worker_calls = []

    class Model:
        def label(self, value=None):
            if value is None:
                worker_calls.append(("get", label["value"]))
                return label["value"]
            worker_calls.append(("set", value))
            label["value"] = value
            return None

    class Worker:
        def client(self):
            return self

        def model(self, _tag):
            return Model()

        @contextmanager
        def operation_context(self, _operation_id, on_request_event=None):
            yield

    daemon.backend.worker = Worker()
    adapter = AvailableWindowAdapter()
    # The UI never reflects the nonce, which makes the challenge fail closed;
    # the Worker still restores the original label through the same queue.
    adapter.observe_model = lambda _identity: DesktopModelObservation("UNVERIFIED", active_model_label="owned-copy")
    daemon.desktop.adapter = adapter
    try:
        status = daemon.dispatch({"operation": "desktop.status", "arguments": {"project_id": "p"}, "execution": {}})
        window_ref = status["data"]["windows"][0]["window_ref"]
        request = {"operation": "desktop.bind", "arguments": {
            "project_id": "p", "idempotency_key": "label-challenge-readback-loss", "window_ref": window_ref,
            "model_ref": json.dumps(ref),
        }, "execution": {}}
        result = daemon.dispatch(request)
        assert result["success"] is False and result["execution"]["status"] == "UNKNOWN"
        assert result["error"]["safe_retry"] is False
        assert label["value"] == "owned-copy"
        assert [kind for kind, _value in worker_calls].count("set") == 2
        assert daemon.desktop._bindings.get(window_ref) is None
        state = service.ledger._state_for(model_ref_from_mapping(ref))
        assert state.revision == 2 and state.dirty is False and state.active_operation_id is None
        assert daemon.dispatch(request) == result
        assert [kind for kind, _value in worker_calls].count("set") == 2
    finally:
        daemon.close()


def test_control_daemon_action_uses_profile_adapter_resolver_and_durable_outer_operation(tmp_path):
    from comsol_mcp._desktop_service import DesktopActionPlan, DesktopModelObservation

    daemon, service, ref = _desktop_read_fixture(tmp_path)
    service.ledger.permissions.update({"inspect", "host_control"})
    service.ledger.model_ownership[ref["model_tag"]] = "mcp_owned"
    calls = []
    adapter = AvailableWindowAdapter()
    adapter.observe_model = lambda _identity: DesktopModelObservation(
        "SERVER", "127.0.0.1:2036", ref["model_tag"], fingerprint="f" * 64,
    )
    adapter.resolve_action = lambda _identity, action: (
        calls.append(("resolve", dict(action))) or DesktopActionPlan("fixture.action", False, {"profile": "observed"})
    )
    adapter.perform_action = lambda _identity, plan: (
        calls.append(("invoke", plan.action_id)) or {"verified": True, "action_id": plan.action_id}
    )
    daemon.desktop.adapter = adapter
    try:
        status = daemon.dispatch({"operation": "desktop.status", "arguments": {"project_id": "p"}, "execution": {}})
        window_ref = status["data"]["windows"][0]["window_ref"]
        bound = daemon.dispatch({"operation": "desktop.bind", "arguments": {
            "project_id": "p", "idempotency_key": "profile-action-bind", "window_ref": window_ref,
            "model_ref": __import__("json").dumps(ref),
        }, "execution": {}})
        assert bound["success"] is True
        result = daemon.dispatch({"operation": "desktop.action", "arguments": {
            "project_id": "p", "idempotency_key": "profile-action-run", "window_ref": window_ref,
            "action": {"action_id": "fixture.action", "target": "window"},
        }, "execution": {}})
        assert result["success"] is True
        assert result["data"]["action_id"] == "fixture.action"
        assert result["data"]["declared_model_mutation"] is False
        assert calls == [("resolve", {"action_id": "fixture.action", "target": "window"}),
                         ("invoke", "fixture.action")]
        assert daemon.store.get_operation(result["execution"]["operation_id"])["status"] == "SUCCEEDED"
    finally:
        daemon.close()


def test_control_daemon_capture_registers_exact_window_bytes_in_project_artifact_store(tmp_path):
    import hashlib
    import json
    from comsol_mcp._desktop_service import DesktopModelObservation

    daemon, service, ref = _desktop_read_fixture(tmp_path)
    service.ledger.permissions.update({"inspect", "host_control", "project_write"})
    service.ledger.model_ownership[ref["model_tag"]] = "mcp_owned"
    image = _synthetic_png_1x1()
    adapter = AvailableWindowAdapter()
    adapter.observe_model = lambda _identity: DesktopModelObservation(
        "SERVER", "127.0.0.1:2036", ref["model_tag"], fingerprint="f" * 64,
    )
    adapter.capture = lambda identity, region: {
        "verified": True, "region": region, "scope": "target_window", "format": "PNG",
        "width": 1, "height": 1, "size": len(image), "sha256": hashlib.sha256(image).hexdigest(),
        "image_bytes": image, "target_identity_fingerprint": identity.fingerprint(),
        "profile_id": "software-fixture-only",
    }
    daemon.desktop.adapter = adapter
    try:
        status = daemon.dispatch({"operation": "desktop.status", "arguments": {"project_id": "p"}, "execution": {}})
        window_ref = status["data"]["windows"][0]["window_ref"]
        bound = daemon.dispatch({"operation": "desktop.bind", "arguments": {
            "project_id": "p", "idempotency_key": "capture-bind", "window_ref": window_ref,
            "model_ref": json.dumps(ref),
        }, "execution": {}})
        assert bound["success"] is True
        captured = daemon.dispatch({"operation": "desktop.capture", "arguments": {
            "project_id": "p", "idempotency_key": "capture-register", "window_ref": window_ref,
            "region": "window",
        }, "execution": {}})
        assert captured["success"] is True, captured
        assert "image_bytes" not in captured["data"]
        assert captured["data"]["artifact_id"] == hashlib.sha256(image).hexdigest()
        record = daemon.store.get_metadata("artifacts", captured["data"]["artifact_id"])
        assert record["role"] == "desktop_capture" and record["artifact_type"] == "image/png"
        assert record["classification"] == "project_private"
        published = tmp_path / captured["data"]["path"]
        assert published.read_bytes() == image
        assert record["provenance"]["capture_scope"] == "target_window"
        assert record["provenance"]["target_model_ref"] == ref
    finally:
        daemon.close()


@pytest.mark.parametrize("case", ["wrong_window", "bad_png"])
def test_control_daemon_capture_unknown_result_never_registers_mismatched_image(tmp_path, case):
    import hashlib
    import json
    from comsol_mcp._desktop_service import DesktopModelObservation

    daemon, service, ref = _desktop_read_fixture(tmp_path)
    service.ledger.permissions.update({"inspect", "host_control", "project_write"})
    service.ledger.model_ownership[ref["model_tag"]] = "mcp_owned"
    image = _synthetic_png_1x1() if case == "wrong_window" else b"not a png"
    adapter = AvailableWindowAdapter()
    adapter.observe_model = lambda _identity: DesktopModelObservation(
        "SERVER", "127.0.0.1:2036", ref["model_tag"], fingerprint="f" * 64,
    )
    adapter.capture = lambda identity, region: {
        "verified": True, "region": region, "scope": "target_window", "format": "PNG",
        "width": 1, "height": 1, "size": len(image), "sha256": hashlib.sha256(image).hexdigest(),
        "image_bytes": image, "target_identity_fingerprint": "0" * 64 if case == "wrong_window" else identity.fingerprint(),
        "profile_id": "software-fixture-only",
    }
    daemon.desktop.adapter = adapter
    try:
        status = daemon.dispatch({"operation": "desktop.status", "arguments": {"project_id": "p"}, "execution": {}})
        window_ref = status["data"]["windows"][0]["window_ref"]
        bound = daemon.dispatch({"operation": "desktop.bind", "arguments": {
            "project_id": "p", "idempotency_key": "capture-deny-bind-" + case, "window_ref": window_ref,
            "model_ref": json.dumps(ref),
        }, "execution": {}})
        assert bound["success"] is True
        denied = daemon.dispatch({"operation": "desktop.capture", "arguments": {
            "project_id": "p", "idempotency_key": "capture-deny-" + case, "window_ref": window_ref,
            "region": "window",
        }, "execution": {}})
        assert denied["success"] is False and denied["execution"]["status"] == "UNKNOWN"
        assert denied["error"]["safe_retry"] is False
        digest = hashlib.sha256(image).hexdigest()
        assert daemon.store.get_metadata("artifacts", digest) is None
        assert not list(tmp_path.glob("desktop-capture-*"))
    finally:
        daemon.close()
