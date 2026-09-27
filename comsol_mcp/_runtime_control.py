"""Narrow runtime control operations with explicit authorization and budgets.

The seat-consuming checkout command is separate from ``modelutil`` and is
serialized through the already-connected Worker.  The render probe constructs
and removes one tagged disposable model on an independently proven owned
server; it never touches a caller-selected model or starts/stops a server.
"""
from __future__ import annotations

import hashlib
import shutil
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence
from uuid import uuid4

from ._execution_contract import ExecutionContractError


def _validate_bound_runtime(worker: Any, backend: Any, runtime_id: str) -> dict[str, Any]:
    """Bind the request label to the exact configured install and endpoint."""
    paths = getattr(worker, "paths", None)
    root = getattr(paths, "comsol_root", None)
    endpoint = getattr(backend, "endpoint_key", None)
    if root is None or not isinstance(endpoint, str) or not endpoint:
        raise ExecutionContractError("RUNTIME_CONFIGURATION_REQUIRED", "runtime install and endpoint identity are unavailable")
    from ._runtime_installation import RuntimeInstallationError, runtime_id_for_root
    try:
        actual_runtime_id = runtime_id_for_root(root)
    except (RuntimeInstallationError, OSError) as exc:
        raise ExecutionContractError("RUNTIME_CONFIGURATION_REQUIRED", "configured COMSOL installation cannot be identified") from exc
    if runtime_id != actual_runtime_id:
        raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "runtime_id does not match the connected Worker installation")
    try:
        identity = worker.runtime_metadata()
    except Exception as exc:
        raise ExecutionContractError("ENGINE_UNRESPONSIVE", "connected Worker identity could not be verified", safe_retry=True) from exc
    if (not isinstance(identity, Mapping) or identity.get("connected") is not True
            or identity.get("server") != endpoint):
        raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "Worker health does not match the exact managed endpoint")
    return dict(identity)


def license_checkout(worker: Any, backend: Any, *, runtime_id: str, products: Sequence[str],
                     authorization_ref: str, execution: Mapping[str, Any]) -> dict[str, Any]:
    """Perform one authorized checkout attempt on the current Worker session."""
    identity = _validate_bound_runtime(worker, backend, runtime_id)
    request_id = execution.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise ExecutionContractError("INVALID_REQUEST", "license checkout requires the durable outer request_id")
    authorization_sha256 = hashlib.sha256(authorization_ref.encode("utf-8")).hexdigest()
    try:
        reply = worker.checkout_license(
            list(products), request_id=request_id,
            queue_timeout_s=float(execution["queue_timeout_s"]),
            # The transport deadline covers queue wait and execution. The
            # daemon has already validated rpc >= queue + execution.
            rpc_timeout_s=float(execution["rpc_timeout_s"]),
        )
    except Exception as exc:
        # The Worker may have received the command before transport loss. Keep
        # the original durable request unknown and never submit another checkout.
        from ._java_worker import JavaWorkerError
        if isinstance(exc, JavaWorkerError):
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN",
                "license checkout outcome is unknown; reconcile the original job before any further action",
                details={"worker_request_id": request_id, "authorization_ref_sha256": authorization_sha256,
                         "checkout_scope": "current_client_session", "seat_release": "NOT_ATTEMPTED_UNVERIFIED"},
            ) from exc
        raise

    if not isinstance(reply, Mapping):
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN", "Worker returned no checkout state; reconcile the original job",
            details={"worker_request_id": request_id, "authorization_ref_sha256": authorization_sha256},
        )
    status = reply.get("status")
    if status in {"QUEUED", "RUNNING"}:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN", "license checkout remains active; reconcile the original job instead of retrying",
            details={"worker_request_id": request_id, "worker_status": status,
                     "authorization_ref_sha256": authorization_sha256},
        )
    if status != "SUCCEEDED" or reply.get("ok") is not True:
        failure = reply.get("failure") if isinstance(reply.get("failure"), Mapping) else {}
        code = failure.get("code") or reply.get("code")
        if code == "MODEL_UTIL_METHOD_ABSENT":
            raise ExecutionContractError(
                "UNSUPPORTED_OPERATION", "the connected COMSOL runtime does not expose the documented checkout method",
                details={"checkout_status": "UNSUPPORTED_API", "checkout_dispatched": False,
                         "authorization_ref_sha256": authorization_sha256},
            )
        if failure.get("execution_state_unknown") is True or failure.get("post_dispatch") is True:
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN", "license checkout may have reached COMSOL; reconcile the original job",
                details={"worker_request_id": request_id, "authorization_ref_sha256": authorization_sha256,
                         "checkout_scope": "current_client_session", "seat_release": "NOT_ATTEMPTED_UNVERIFIED"},
            )
        raise ExecutionContractError(
            str(code or "LICENSE_CHECKOUT_REFUSED"),
            "license checkout was refused before a result could be observed",
            details={"checkout_status": "FAILED", "authorization_ref_sha256": authorization_sha256},
        )
    result = reply.get("result")
    if (not isinstance(result, Mapping) or type(result.get("granted")) is not bool
            or result.get("checkout_scope") != "current_client_session"):
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN", "checkout reply did not contain the documented boolean result",
            details={"worker_request_id": request_id, "authorization_ref_sha256": authorization_sha256},
        )
    return {
        "status": "OBSERVED",
        "runtime_id": runtime_id,
        "runtime_identity": {key: identity.get(key) for key in ("instance_id", "generation", "server", "connected")},
        "checkout": {"status": "GRANTED" if result["granted"] else "NOT_GRANTED",
                     "granted": result["granted"], "products": list(products),
                     "method": result.get("method"), "product_count": result.get("product_count"),
                     "request_id": request_id},
        "checkout_scope": "current_client_session",
        "seat_release": "NOT_ATTEMPTED_UNVERIFIED",
        "authorization_ref_sha256": authorization_sha256,
        "verified": ["the dedicated Worker checkout command returned a boolean for this request"],
        "unverified": ["seat retention after the current COMSOL client session ends"],
        "server_lifecycle_changed": False,
    }


class _RequestBudget:
    def __init__(self, execution: Mapping[str, Any], request_id: str) -> None:
        self.execution_seconds = float(execution["execution_timeout_s"])
        self.deadline = time.monotonic() + self.execution_seconds
        self.queue_timeout = float(execution["queue_timeout_s"])
        self.request_id = request_id
        self.index = 0

    def kwargs(self, *, cleanup: bool = False) -> dict[str, Any]:
        remaining = self.deadline - time.monotonic()
        reserve = 0.0 if cleanup else min(5.0, max(1.0, self.execution_seconds * 0.15))
        available = remaining - reserve
        if available <= 0.05:
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN", "render probe reached its operation deadline before cleanup completed",
                details={"worker_request_id": self.request_id},
            )
        self.index += 1
        suffix = f"cleanup-{self.index}" if cleanup else str(self.index)
        return {
            "request_id": f"{self.request_id}:render:{suffix}",
            "queue_timeout_s": min(self.queue_timeout, available),
            "rpc_timeout_s": available,
        }


class _TimedProxy:
    """Inject the operation deadline into every RemoteClient/RemoteJava call."""
    def __init__(self, value: Any, budget: _RequestBudget) -> None:
        self._value = value
        self._budget = budget

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._value, name)
        if not callable(value):
            return value

        def invoke(*args: Any, **kwargs: Any) -> Any:
            call_kwargs = self._budget.kwargs()
            call_kwargs.update(kwargs)
            try:
                result = value(*args, **call_kwargs)
            except Exception as exc:
                from ._java_worker import JavaWorkerError
                if isinstance(exc, JavaWorkerError):
                    raise ExecutionContractError(
                        "EXECUTION_STATE_UNKNOWN",
                        "render Worker request outcome is unknown; reconcile the original job",
                        details={"worker_request_id": call_kwargs["request_id"]},
                    ) from exc
                raise
            if (isinstance(result, Mapping)
                    and result.get("status") in {"QUEUED", "RUNNING"}
                    and isinstance(result.get("request_id"), str)
                    and isinstance(result.get("type"), str)
                    and "queued_at_ms" in result):
                # RemoteJava deliberately surfaces a pending reply as a plain
                # mapping. Treat it as an unresolved command and stop the
                # recipe before it can inspect output or dispatch more work.
                raise ExecutionContractError(
                    "EXECUTION_STATE_UNKNOWN",
                    "render Worker request remains active; reconcile the original job before continuing",
                    details={"worker_request_id": result.get("request_id") or call_kwargs["request_id"],
                             "worker_status": result["status"]},
                )
            from ._java_worker import RemoteJava
            return _TimedProxy(result, self._budget) if isinstance(result, RemoteJava) else result
        return invoke


class _TimedWorker:
    def __init__(self, worker: Any, project_root: Path, budget: _RequestBudget) -> None:
        self._worker = worker
        self._budget = budget
        self.paths = SimpleNamespace(resolved_project_root=project_root)

    def client(self) -> _TimedProxy:
        return _TimedProxy(self._worker.client(), self._budget)

    def submit(self, kind: str, payload: Mapping[str, Any], *, cleanup: bool = False) -> dict[str, Any]:
        kwargs = self._budget.kwargs(cleanup=cleanup)
        try:
            return self._worker.submit(kind, payload, **kwargs)
        except Exception as exc:
            from ._java_worker import JavaWorkerError
            if isinstance(exc, JavaWorkerError):
                raise ExecutionContractError(
                    "EXECUTION_STATE_UNKNOWN", "render Worker request outcome is unknown; reconcile the original job",
                    details={"worker_request_id": kwargs["request_id"]},
                ) from exc
            raise


def render_probe(worker: Any, backend: Any, *, runtime_id: str, mode: str,
                 execution: Mapping[str, Any], authorization_ref: str) -> dict[str, Any]:
    """Render and validate one disposable rectangle image on the owned server."""
    identity = _validate_bound_runtime(worker, backend, runtime_id)
    if mode != "geometry":
        raise ExecutionContractError("UNSUPPORTED_OPERATION", "the bounded runtime probe supports mode='geometry' only")
    request_id = execution.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise ExecutionContractError("INVALID_REQUEST", "render probe requires the durable outer request_id")
    authorization_sha256 = hashlib.sha256(authorization_ref.encode("utf-8")).hexdigest()
    from ._artifact_store import ArtifactStore
    project_root = getattr(getattr(worker, "paths", None), "resolved_project_root", None)
    if project_root is None:
        project_root = getattr(backend, "project_root", None)
    if project_root is None:
        raise ExecutionContractError("RUNTIME_CONFIGURATION_REQUIRED", "render probe requires the configured project root")
    artifact_store = ArtifactStore(project_root=Path(project_root))
    root = Path(tempfile.mkdtemp(prefix=".comsol-mcp-render-probe-", dir=str(artifact_store.project_root)))
    temp_root_removed = False
    try:
        root.chmod(0o700)
    except OSError:
        pass
    target = root / "runtime-render.png"
    staging = root / ".runtime-render.staging.png"
    budget = _RequestBudget(execution, request_id)
    timed_worker = _TimedWorker(worker, artifact_store.project_root, budget)
    client = timed_worker.client()
    before_tags: list[str] | None = None
    model_tag: str | None = None
    create_attempted = False
    rendered: dict[str, Any] | None = None
    operation_error: BaseException | None = None
    worker_cleanup_error: BaseException | None = None
    temp_cleanup_error: BaseException | None = None
    final_tags: list[str] | None = None
    try:
        before = client.tags()
        if not isinstance(before, list) or any(not isinstance(tag, str) for tag in before):
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "Worker did not return a complete model-tag inventory")
        before_tags = list(before)
        model_tag = str(client.uniquetag("mcpRuntimeRenderProbe"))
        if not model_tag or model_tag in before_tags:
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "Worker did not provide a unique disposable model tag")
        create_attempted = True
        from ._java_worker import _decode_reply
        create_reply = timed_worker.submit("modelutil", {"method": "create", "args": [model_tag]})
        if create_reply.get("status") in {"QUEUED", "RUNNING"}:
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN", "disposable model creation remains active; reconcile the original job",
                details={"worker_request_id": create_reply.get("request_id")},
            )
        _decode_reply(create_reply, worker)

        from ._g2_engine import _call
        model = client.model(model_tag)
        component_list = _call(model, "component")
        _call(component_list, "create", "comp1")
        component = _call(model, "component", "comp1")
        geometry_list = _call(component, "geom")
        _call(geometry_list, "create", "geom1", 2)
        geometry = _call(component, "geom", "geom1")
        feature_list = _call(geometry, "feature")
        _call(feature_list, "create", "rect1", "Rectangle")
        rectangle = _call(geometry, "feature", "rect1")
        _call(rectangle, "set", "size", [1.0, 1.0])
        _call(geometry, "run")
        image = _call(geometry, "image")
        # These are required calls. A failed filename/format/dimension setting
        # must never be swallowed into a render of an uncontrolled destination.
        _call(image, "set", "target", "file")
        _call(image, "set", "imagetype", "png")
        _call(image, "set", "pngfilename", str(staging))
        _call(image, "set", "size", "manualweb")
        _call(image, "set", "width", 320)
        _call(image, "set", "height", 240)
        _call(image, "set", "antialias", "on")
        _call(image, "export")

        from ._g3_w18 import _stage_and_publish_image
        raw, digest, width, height = _stage_and_publish_image(
            artifact_store, staging, target,
            allow_overwrite=False, expected_fmt="png",
        )
        rendered = {"sha256": digest, "byte_size": len(raw), "width": width, "height": height,
                    "png_validated": True, "artifact_ephemeral": True}
    except BaseException as exc:
        operation_error = exc
    finally:
        if create_attempted and model_tag:
            try:
                cleanup_kwargs = budget.kwargs(cleanup=True)
                worker.client().remove(model_tag, **cleanup_kwargs)
            except BaseException as exc:
                worker_cleanup_error = exc
            try:
                cleanup_kwargs = budget.kwargs(cleanup=True)
                observed = worker.client().tags(**cleanup_kwargs)
                if isinstance(observed, list) and all(isinstance(tag, str) for tag in observed):
                    final_tags = list(observed)
            except BaseException as exc:
                worker_cleanup_error = worker_cleanup_error or exc

    model_restored = (before_tags is not None and final_tags is not None and model_tag not in final_tags
                      and sorted(before_tags) == sorted(final_tags))
    cleanup_complete = not create_attempted or model_restored
    # A failed remove RPC can be resolved by the subsequent exact tag inventory,
    # which runs later on the same serial Worker queue. The readback, rather than
    # the earlier RPC error, is the final cleanup evidence.
    if model_restored:
        worker_cleanup_error = None
    if cleanup_complete:
        try:
            shutil.rmtree(root)
            temp_root_removed = True
            # The render artifact is deliberately ephemeral. Do not leave its
            # path in ArtifactStore's process-global registered-artifact set.
            ArtifactStore._REGISTERED_ARTIFACTS.discard(str(target))
            ArtifactStore._REGISTERED_ARTIFACTS.discard(str(target.resolve()))
        except OSError as exc:
            temp_cleanup_error = exc
    if worker_cleanup_error is not None or temp_cleanup_error is not None or not cleanup_complete or not temp_root_removed:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN", "render probe cleanup could not prove restoration of the Worker model inventory and temporary files",
            details={"worker_request_id": request_id, "temporary_model_removed": model_restored,
                     "model_inventory_restored": model_restored, "temporary_root_removed": temp_root_removed,
                     "temporary_root_retained": not temp_root_removed,
                     "temporary_root": str(root) if not temp_root_removed else None,
                     "authorization_ref_sha256": authorization_sha256},
        ) from (worker_cleanup_error or temp_cleanup_error)
    if operation_error is not None:
        if isinstance(operation_error, ExecutionContractError):
            raise operation_error
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN", "render probe failed after dispatch; the original job must be reconciled",
            details={"worker_request_id": request_id, "model_inventory_restored": True,
                     "authorization_ref_sha256": authorization_sha256},
        ) from operation_error
    if rendered is None:
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "render probe produced no validated image result")
    return {
        "status": "OBSERVED",
        "runtime_id": runtime_id,
        "runtime_identity": {key: identity.get(key) for key in ("instance_id", "generation", "server", "connected")},
        "mode": mode,
        "render": rendered,
        "temporary_model_removed": True,
        "model_inventory_restored": True,
        "server_lifecycle_changed": False,
        "authorization_ref_sha256": authorization_sha256,
        "verified": ["a disposable geometry was rendered to a structurally valid PNG and removed"],
        "unverified": ["GUI display behavior and any user-selected model render"],
    }


__all__ = ["license_checkout", "render_probe"]
