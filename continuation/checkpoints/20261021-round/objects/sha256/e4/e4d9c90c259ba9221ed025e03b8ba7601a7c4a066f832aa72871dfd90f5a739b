from __future__ import annotations

from contextlib import nullcontext
import json
import struct
import zlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._java_worker import JavaWorkerError
from comsol_mcp._runtime_installation import runtime_id_for_root
from comsol_mcp import _runtime_control
from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._operation_store import OperationStore


class _CheckoutWorker:
    def __init__(self, install: Path, reply: Any = None, error: Exception | None = None) -> None:
        self.paths = SimpleNamespace(comsol_root=install)
        self.reply = reply
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def runtime_metadata(self):
        return {"instance_id": "worker-1", "generation": 3, "server": "127.0.0.1:2036", "connected": True}

    def checkout_license(self, products, **kwargs):
        self.calls.append({"products": list(products), **kwargs})
        if self.error:
            raise self.error
        return self.reply


def _checkout_execution(**updates: Any) -> dict[str, Any]:
    return {
        "request_id": "outer-request-1",
        "queue_timeout_s": 4.0,
        "execution_timeout_s": 20.0,
        "rpc_timeout_s": 25.0,
        **updates,
    }


@pytest.mark.parametrize("granted, expected", [(True, "GRANTED"), (False, "NOT_GRANTED")])
def test_license_checkout_is_one_typed_request_on_the_current_session(
    tmp_path: Path, granted: bool, expected: str,
) -> None:
    worker = _CheckoutWorker(tmp_path, {
        "ok": True,
        "status": "SUCCEEDED",
        "result": {"granted": granted, "checkout_scope": "current_client_session",
                   "method": "ModelUtil.checkoutLicense(String...)", "product_count": 1},
    })
    execution = _checkout_execution()

    result = _runtime_control.license_checkout(
        worker, SimpleNamespace(endpoint_key="127.0.0.1:2036"),
        runtime_id=runtime_id_for_root(tmp_path), products=["ACDC"],
        authorization_ref="approved-ref-42", execution=execution,
    )

    assert result["checkout"]["status"] == expected
    assert result["checkout"]["granted"] is granted
    assert result["checkout_scope"] == "current_client_session"
    assert result["seat_release"] == "NOT_ATTEMPTED_UNVERIFIED"
    assert result["server_lifecycle_changed"] is False
    assert len(worker.calls) == 1
    assert worker.calls[0] == {
        "products": ["ACDC"], "request_id": "outer-request-1",
        "queue_timeout_s": 4.0, "rpc_timeout_s": 25.0,
    }


def test_license_checkout_unknown_is_not_retried(tmp_path: Path) -> None:
    worker = _CheckoutWorker(tmp_path, {
        "ok": True, "status": "RUNNING", "request_id": "outer-request-1",
    })
    with pytest.raises(ExecutionContractError) as caught:
        _runtime_control.license_checkout(
            worker, SimpleNamespace(endpoint_key="127.0.0.1:2036"),
            runtime_id=runtime_id_for_root(tmp_path), products=["ACDC"],
            authorization_ref="approved-ref-42", execution=_checkout_execution(),
        )
    assert caught.value.code == "EXECUTION_STATE_UNKNOWN"
    assert len(worker.calls) == 1


def test_license_checkout_transport_loss_is_unknown_and_never_retried(tmp_path: Path) -> None:
    worker = _CheckoutWorker(tmp_path, error=JavaWorkerError("timeout after dispatch"))
    with pytest.raises(ExecutionContractError) as caught:
        _runtime_control.license_checkout(
            worker, SimpleNamespace(endpoint_key="127.0.0.1:2036"),
            runtime_id=runtime_id_for_root(tmp_path), products=["ACDC"],
            authorization_ref="approved-ref-42", execution=_checkout_execution(),
        )
    assert caught.value.code == "EXECUTION_STATE_UNKNOWN"
    assert caught.value.details["seat_release"] == "NOT_ATTEMPTED_UNVERIFIED"
    assert len(worker.calls) == 1


def test_license_checkout_rejects_runtime_mismatch_before_dispatch(tmp_path: Path) -> None:
    worker = _CheckoutWorker(tmp_path, {"ok": True, "status": "SUCCEEDED",
                                        "result": {"granted": True, "checkout_scope": "current_client_session"}})
    with pytest.raises(ExecutionContractError) as caught:
        _runtime_control.license_checkout(
            worker, SimpleNamespace(endpoint_key="127.0.0.1:2036"),
            runtime_id="comsol-install:/another/path", products=["ACDC"],
            authorization_ref="approved-ref-42", execution=_checkout_execution(),
        )
    assert caught.value.code == "MODEL_IDENTITY_MISMATCH"
    assert worker.calls == []


def test_java_worker_wrapper_uses_only_the_dedicated_checkout_command() -> None:
    from comsol_mcp._java_worker import PersistentJavaWorker

    class Capture:
        calls = []

        def submit(self, kind, payload, **kwargs):
            self.calls.append((kind, payload, kwargs))
            return {"ok": True, "status": "SUCCEEDED"}

    capture = Capture()
    PersistentJavaWorker.checkout_license(
        capture, ["ACDC"], request_id="checkout-1", queue_timeout_s=3.0, rpc_timeout_s=25.0,
    )
    assert capture.calls == [("license_checkout", {"products": ["ACDC"]}, {
        "request_id": "checkout-1", "queue_timeout_s": 3.0, "rpc_timeout_s": 25.0,
    })]

    with pytest.raises(JavaWorkerError):
        PersistentJavaWorker.checkout_license(
            capture, [], request_id="checkout-invalid", queue_timeout_s=3.0, rpc_timeout_s=25.0,
        )
    assert len(capture.calls) == 1


class _ManagedCheckoutWorker(_CheckoutWorker):
    def __init__(self, install: Path, reply: Any = None, error: Exception | None = None):
        super().__init__(install, reply, error)
        self.generation = 1

    def operation_context(self, *_args, **_kwargs):
        return nullcontext()

    def start(self):
        return None

    def health(self):
        return {"connected": True, "server": "127.0.0.1:2036"}

    def client(self):
        return self


class _SnapshotAdapter:
    def model_snapshot(self, tag):
        return {"model_tag": tag, "fingerprint": "unchanged", "external_event_counter": 0}


def _managed_checkout_backend(tmp_path: Path, worker: _ManagedCheckoutWorker, *, permissions: set[str]):
    ledger = SessionLedger("session-checkout", "server-checkout")
    ledger.permissions = set(permissions)
    service = ExecutionService(ledger, _SnapshotAdapter(), project_root=tmp_path)
    store = OperationStore(tmp_path / "operations.sqlite3")
    backend = ManagedBackend(tmp_path / "home", store, service=service, worker=worker,
                             registry={}, project_root=tmp_path)
    backend.endpoint_key = "127.0.0.1:2036"
    return backend, store


def _checkout_arguments(install: Path, *, authorization_ref: str = "authorization-marker") -> dict[str, Any]:
    return {"runtime_id": runtime_id_for_root(install), "products": ["ACDC"],
            "authorization_ref": authorization_ref, "idempotency_key": "checkout-key"}


def _managed_execution(**updates: Any) -> dict[str, Any]:
    return {"request_id": "outer-checkout", "session_id": "session-checkout",
            "idempotency_key": "checkout-key", "queue_timeout_s": 2.0,
            "execution_timeout_s": 20.0, "rpc_timeout_s": 23.0, **updates}


def test_managed_backend_checkout_requires_host_permission_and_matching_envelope(tmp_path: Path) -> None:
    install = tmp_path / "COMSOL64" / "Multiphysics"
    install.mkdir(parents=True)
    worker = _ManagedCheckoutWorker(install, {
        "ok": True, "status": "SUCCEEDED",
        "result": {"granted": True, "checkout_scope": "current_client_session"},
    })
    backend, store = _managed_checkout_backend(tmp_path, worker, permissions={"inspect", "compute"})
    arguments = _checkout_arguments(install)
    try:
        with pytest.raises(ExecutionContractError) as denied:
            backend.invoke("runtime.license_checkout", arguments, _managed_execution(), "op-denied", lambda *_: None)
        assert denied.value.code == "PERMISSION_DENIED"
        assert worker.calls == []

        backend.service.ledger.permissions.add("host_control")
        with pytest.raises(ExecutionContractError) as mismatch:
            backend.invoke("runtime.license_checkout", arguments,
                           _managed_execution(idempotency_key="other-key"), "op-mismatch", lambda *_: None)
        assert mismatch.value.code == "IDEMPOTENCY_CONFLICT"
        assert worker.calls == []

        result = backend.invoke("runtime.license_checkout", arguments, _managed_execution(),
                                "op-checkout", lambda *_: None)
        assert result["success"] is True
        assert result["data"]["checkout"]["granted"] is True
        assert len(worker.calls) == 1
    finally:
        backend.close()
        store.close()


def test_host_control_grant_is_bounded_by_backend_startup_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from comsol_mcp import _server as server

    install = tmp_path / "COMSOL64" / "Multiphysics"
    install.mkdir(parents=True)
    store_path = tmp_path / "operations.sqlite3"
    home = tmp_path / "home"
    for name in ("_remote_client_factory", "_client", "_client_connected", "_connected_host",
                 "_connected_port", "_server_started_by_mcp"):
        monkeypatch.setattr(server, name, getattr(server, name))

    def make_backend() -> tuple[ManagedBackend, OperationStore]:
        store = OperationStore(store_path)
        backend = ManagedBackend(home, store, worker=_ManagedCheckoutWorker(install),
                                 registry={}, project_root=tmp_path)
        return backend, store

    # Deployment capabilities are captured when a backend is constructed.
    # Mutating the environment later cannot expand that live daemon's ceiling.
    monkeypatch.delenv("COMSOL_MCP_HOST_CONTROL", raising=False)
    backend, store = make_backend()
    try:
        first = backend.connect({"host": "127.0.0.1", "port": 2036}, "connect-1", lambda *_: None)
        assert first["data"]["host_control"]["enabled"] is False
        assert "host_control" not in backend.service.ledger.permissions

        monkeypatch.setenv("COMSOL_MCP_HOST_CONTROL", "true")
        second = backend.connect({"host": "127.0.0.1", "port": 2036}, "connect-2", lambda *_: None)
        assert second["data"]["host_control"]["enabled"] is False
        assert "host_control" not in backend.service.ledger.permissions
    finally:
        backend.close()
        store.close()

    # A fresh backend reads the explicit deployment opt-in and can grant it.
    monkeypatch.setenv("COMSOL_MCP_HOST_CONTROL", "true")
    backend, store = make_backend()
    try:
        enabled = backend.connect({"host": "127.0.0.1", "port": 2036}, "connect-3", lambda *_: None)
        assert enabled["data"]["host_control"]["enabled"] is True
        assert "host_control" in backend.service.ledger.permissions

        # Removing the environment value cannot mutate a running daemon's
        # startup ceiling. A new backend below is the revocation boundary.
        monkeypatch.delenv("COMSOL_MCP_HOST_CONTROL")
        still_enabled = backend.connect({"host": "127.0.0.1", "port": 2036}, "connect-4", lambda *_: None)
        assert still_enabled["data"]["host_control"]["enabled"] is True
        assert "host_control" in backend.service.ledger.permissions
    finally:
        backend.close()
        store.close()

    # The old persisted grant cannot exceed a newly constructed backend's
    # disabled startup ceiling.
    backend, store = make_backend()
    try:
        revoked = backend.connect({"host": "127.0.0.1", "port": 2036}, "connect-5", lambda *_: None)
        assert revoked["data"]["host_control"]["enabled"] is False
        assert "host_control" not in backend.service.ledger.permissions
    finally:
        backend.close()
        store.close()


def test_managed_render_probe_requires_compute_permission_and_owned_server_proof(tmp_path: Path, monkeypatch) -> None:
    install = tmp_path / "COMSOL64" / "Multiphysics"
    install.mkdir(parents=True)
    worker = _ManagedCheckoutWorker(install, {"ok": True, "status": "SUCCEEDED", "result": {}})
    backend, store = _managed_checkout_backend(tmp_path, worker, permissions={"inspect"})
    arguments = {"runtime_id": runtime_id_for_root(install), "mode": "geometry",
                 "idempotency_key": "render-key"}
    execution = _managed_execution(request_id="outer-render", idempotency_key="render-key",
                                   authorization_ref="render-authorization")
    try:
        with pytest.raises(ExecutionContractError) as denied:
            backend.invoke("runtime.render_probe", arguments, execution, "op-render-denied", lambda *_: None)
        assert denied.value.code == "PERMISSION_DENIED"
        assert worker.calls == []

        backend.service.ledger.permissions.add("compute")
        monkeypatch.setattr(backend, "_require_g2_isolation", lambda: (_ for _ in ()).throw(
            ExecutionContractError("ISOLATION_PROOF_REQUIRED", "owned server not proven")))
        with pytest.raises(ExecutionContractError) as unisolated:
            backend.invoke("runtime.render_probe", arguments, execution, "op-render-unisolated", lambda *_: None)
        assert unisolated.value.code == "ISOLATION_PROOF_REQUIRED"
        assert worker.calls == []
    finally:
        backend.close()
        store.close()


@pytest.mark.parametrize("operation, auth_location", [
    ("runtime.license_checkout", "arguments"),
    ("runtime.render_probe", "execution"),
])
def test_control_daemon_persists_only_authorization_hash_and_binds_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, auth_location: str,
) -> None:
    from comsol_mcp._control_daemon import ControlDaemon

    daemon = ControlDaemon(tmp_path / "daemon")
    calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    marker = f"private-{operation}-authorization-marker"
    digest = __import__("hashlib").sha256(marker.encode("utf-8")).hexdigest()

    def invoke(op, arguments, execution, operation_id, callback):
        calls.append((op, dict(arguments), dict(execution)))
        return {"success": True, "data": {"status": "OBSERVED", "checked": True},
                "execution": {"operation_id": operation_id}}

    monkeypatch.setattr(daemon.backend, "invoke", invoke)
    arguments = {"runtime_id": "fixture-runtime", "idempotency_key": "same-key"}
    execution = {"idempotency_key": "same-key", "request_id": "first-request",
                 "queue_timeout_s": 1.0, "execution_timeout_s": 10.0, "rpc_timeout_s": 12.0,
                 "authorization_ref_sha256": "caller-supplied-fake-hash"}
    arguments["authorization_ref_sha256"] = "caller-supplied-fake-hash"
    if auth_location == "arguments":
        arguments.update({"products": ["ACDC"], "authorization_ref": marker})
    else:
        execution["authorization_ref"] = marker
    try:
        first = daemon.dispatch({"operation": operation, "arguments": arguments, "execution": execution})
        assert first["success"] is True
        replay = daemon.dispatch({"operation": operation, "arguments": arguments, "execution": execution})
        assert replay == first
        assert len(calls) == 1

        job_id = first["execution"]["job_id"]
        row = daemon.store.job(job_id)
        events = daemon.store.events(job_id)
        saved = json.dumps({"metadata": row["metadata"], "events": events,
                            "result": row.get("result")}, sort_keys=True)
        assert marker not in saved
        assert digest in saved
        assert "caller-supplied-fake-hash" not in saved

        changed_args = dict(arguments)
        changed_execution = dict(execution)
        if auth_location == "arguments":
            changed_args["authorization_ref"] = marker + "-changed"
        else:
            changed_execution["authorization_ref"] = marker + "-changed"
        changed_execution["request_id"] = "second-request"
        changed = daemon.dispatch({"operation": operation, "arguments": changed_args,
                                   "execution": changed_execution})
        assert changed["success"] is False
        assert changed["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert len(calls) == 1
    finally:
        daemon.close()


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))


def _valid_png() -> bytes:
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + _png_chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff\xff"))
            + _png_chunk(b"IEND", b""))


class _FakeJavaWorker:
    generation = 1

    def __init__(self, pending_method: str | None = None, *, image_bytes: bytes | None = None) -> None:
        self.paths = SimpleNamespace(comsol_root=None)
        self.pending_method = pending_method
        self.image_bytes = image_bytes
        self.staging_path: Path | None = None
        self.requests: list[dict[str, Any]] = []
        self.model_tags = ["existing"]

    def runtime_metadata(self):
        return {"instance_id": "worker-render", "generation": 1,
                "server": "127.0.0.1:2036", "connected": True}

    def client(self):
        from comsol_mcp._java_worker import RemoteClient
        return RemoteClient(self)

    def submit(self, kind: str, payload: dict[str, Any], *, request_id=None,
               queue_timeout_s=None, rpc_timeout_s=None):
        self.requests.append({"type": kind, **payload, "request_id": request_id})
        if kind == "modelutil":
            method = payload["method"]
            if method == "tags":
                value = list(self.model_tags)
            elif method == "uniquetag":
                value = "mcpRuntimeRenderProbe_1"
            else:
                value = None
        elif kind == "model":
            value = {"$worker_handle": "model", "generation": 1, "java_type": "Model"}
        elif kind == "call":
            method = payload["method"]
            if method == self.pending_method:
                return {"ok": True, "status": "RUNNING", "request_id": request_id,
                        "type": kind, "queued_at_ms": 1, "started_at_ms": 2}
            if method == "set" and payload.get("handle", "").endswith(".image"):
                args = payload.get("args", [])
                if len(args) == 2 and args[0] == "pngfilename":
                    self.staging_path = Path(args[1])
            if method == "export" and self.image_bytes is not None:
                assert self.staging_path is not None
                self.staging_path.write_bytes(self.image_bytes)
            if method in {"component", "geom", "feature", "image"}:
                value = {"$worker_handle": f"{payload['handle']}.{method}",
                         "generation": 1, "java_type": "RemoteNode"}
            else:
                value = None
        else:
            raise AssertionError(f"unexpected Worker command {kind}")
        return {"ok": True, "status": "SUCCEEDED", "type": kind,
                "request_id": request_id, "result": value}


@pytest.mark.parametrize("pending_method, later_forward_methods", [
    ("run", {"image", "export"}),
    ("export", set()),
])
def test_render_stops_on_pending_build_or_export_and_only_runs_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    pending_method: str, later_forward_methods: set[str],
) -> None:
    install = tmp_path / "COMSOL64" / "Multiphysics"
    install.mkdir(parents=True)
    worker = _FakeJavaWorker(pending_method)
    worker.paths.comsol_root = install
    worker.paths.resolved_project_root = tmp_path.resolve()
    backend = SimpleNamespace(endpoint_key="127.0.0.1:2036")
    execution = {"request_id": "render-request", "queue_timeout_s": 2.0,
                 "execution_timeout_s": 30.0}

    with pytest.raises(ExecutionContractError) as caught:
        _runtime_control.render_probe(
            worker, backend, runtime_id=runtime_id_for_root(install), mode="geometry",
            execution=execution, authorization_ref="approved-render-ref",
        )

    assert caught.value.code == "EXECUTION_STATE_UNKNOWN"
    assert caught.value.details["worker_request_id"]
    forward_methods = [row["method"] for row in worker.requests if row["type"] == "call"]
    assert pending_method in forward_methods
    assert not later_forward_methods.intersection(forward_methods)
    # Removing the exact temporary model and reading tags are the only allowed
    # follow-up commands after an unresolved geometry/export call.
    commands_after_pending = worker.requests[
        next(i for i, row in enumerate(worker.requests)
             if row["type"] == "call" and row["method"] == pending_method) + 1:
    ]
    assert [row.get("method") for row in commands_after_pending if row["type"] == "call"] == []
    assert [row.get("method") for row in commands_after_pending if row["type"] == "modelutil"] == ["remove", "tags"]


@pytest.mark.parametrize("image_bytes, expected_code", [
    (_valid_png(), None),
    (b"not a png", "IMAGE_DECODE_ERROR"),
])
def test_render_probe_requires_a_valid_png_and_restores_the_model_inventory(
    tmp_path: Path, image_bytes: bytes, expected_code: str | None,
) -> None:
    install = tmp_path / "COMSOL64" / "Multiphysics"
    install.mkdir(parents=True)
    worker = _FakeJavaWorker(image_bytes=image_bytes)
    worker.paths.comsol_root = install
    worker.paths.resolved_project_root = tmp_path.resolve()
    backend = SimpleNamespace(endpoint_key="127.0.0.1:2036")

    if expected_code:
        with pytest.raises(ExecutionContractError) as caught:
            _runtime_control.render_probe(
                worker, backend, runtime_id=runtime_id_for_root(install), mode="geometry",
                execution={"request_id": "render-bad-image", "queue_timeout_s": 2.0,
                           "execution_timeout_s": 30.0}, authorization_ref="approved-render-ref",
            )
        assert caught.value.code == expected_code
    else:
        result = _runtime_control.render_probe(
            worker, backend, runtime_id=runtime_id_for_root(install), mode="geometry",
            execution={"request_id": "render-valid-image", "queue_timeout_s": 2.0,
                       "execution_timeout_s": 30.0}, authorization_ref="approved-render-ref",
        )
        assert result["status"] == "OBSERVED"
        assert result["render"]["png_validated"] is True
        assert result["render"]["width"] == 1 and result["render"]["height"] == 1
        assert result["model_inventory_restored"] is True

    assert worker.staging_path is not None
    assert not worker.staging_path.parent.exists()
    assert [row["method"] for row in worker.requests if row["type"] == "modelutil"][-2:] == ["remove", "tags"]
