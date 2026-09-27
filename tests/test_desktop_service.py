"""W25 Desktop coordinator software tests; fixtures are never native evidence."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import threading
import time

import pytest

from comsol_mcp._desktop_service import (
    AVAILABLE,
    BLOCKED_PERMISSION,
    MODEL_BINDING_UNKNOWN,
    UNSUPPORTED_CONTROL,
    DesktopActionPlan,
    DesktopCoordinator,
    DesktopDispatchContext,
    DesktopModelObservation,
    DesktopOperationError,
    ManagedModelSnapshot,
    MetadataOnlyDesktopAdapter,
    SavedCopy,
    SourcePreservation,
    TrustedSourceArtifact,
    WindowIdentity,
    WindowObservation,
)
from comsol_mcp._desktop_platforms import create_native_metadata_adapter
from comsol_mcp._execution_contract import ModelRef
from comsol_mcp._operation_store import OperationStore


ENDPOINT = "127.0.0.1:2036"
MODEL = ManagedModelSnapshot(ModelRef("session-a", "server-a", "model-a", 1), ENDPOINT, "model-a", "a" * 64)
MODEL_B = ManagedModelSnapshot(ModelRef("session-b", "server-b", "model-b", 1), "127.0.0.1:2037", "model-b", "b" * 64)
NODE_PATH_A = {"segments": [
    {"collection": "components", "tag": "comp1"},
    {"collection": "geometry", "tag": "geom1"},
    {"collection": "features", "tag": "blk1"},
]}


def _window(index: int = 1, *, session: str = "desktop-1", version: str = "6.4.0.293") -> WindowObservation:
    return WindowObservation(
        WindowIdentity(
            platform="fixture",
            native_window_id=f"WINDOW:{index}",
            process_id=1000 + index,
            process_birth=f"birth-{index}",
            login_id="sid:test-user",
            desktop_session_id=session,
            comsol_version=version,
        ),
        title="COMSOL Model A",
        control_status=AVAILABLE,
    )


class FixtureDesktopAdapter:
    """Injected software fixture. It does not talk to native GUI APIs."""

    def __init__(self, windows: list[WindowObservation] | None = None):
        self.windows = {row.identity.native_window_id: row for row in (windows or [_window()])}
        self.models = {
            key: DesktopModelObservation("SERVER", row.server_endpoint, row.model_tag, fingerprint=row.fingerprint)
            for key, row in (("WINDOW:1", MODEL), ("WINDOW:2", MODEL))
        }
        self.store: OperationStore | None = None
        self.callback_counts: dict[str, int] = {}
        self.callback_entered: threading.Event | None = None
        self.callback_release: threading.Event | None = None
        self.active_callbacks = 0
        self.max_active_callbacks = 0
        self.preserve_source = True
        self.source_window_open = True
        self.show_model_error: Exception | None = None
        self.show_model_calls: list[str] = []
        self.action_error: Exception | None = None

    def status(self, runtime_id: str | None = None):
        return {"platform": "fixture", "permission_status": "AVAILABLE", "control_status": AVAILABLE,
                "metadata_status": "AVAILABLE", "runtime_id": runtime_id}

    def enumerate_windows(self, runtime_id: str | None = None):
        return list(self.windows.values())

    def revalidate_window(self, identity: WindowIdentity):
        return self.windows.get(identity.native_window_id)

    def observe_model(self, identity: WindowIdentity):
        return self.models[identity.native_window_id]

    def show_model(self, identity: WindowIdentity, snapshot: ManagedModelSnapshot):
        self.show_model_calls.append(identity.native_window_id)
        if self.show_model_error:
            error, self.show_model_error = self.show_model_error, None
            raise error
        self.models[identity.native_window_id] = DesktopModelObservation(
            "SERVER", snapshot.server_endpoint, snapshot.model_tag, fingerprint=snapshot.fingerprint,
        )
        return {"verified": True}

    def select_node(self, identity: WindowIdentity, path):
        self._claim_observed("desktop.select_node")
        self.callback_counts["desktop.select_node"] = self.callback_counts.get("desktop.select_node", 0) + 1
        return {"verified": True, "selected_path": path}

    def capture(self, identity: WindowIdentity, region: str):
        self._claim_observed("desktop.capture")
        return {"verified": True, "region": region, "scope": "target_window", "artifact_id": "capture-1", "sha256": "c" * 64}

    def perform_action(self, identity: WindowIdentity, plan: DesktopActionPlan):
        self._claim_observed("desktop.action")
        self.callback_counts["desktop.action"] = self.callback_counts.get("desktop.action", 0) + 1
        if self.action_error:
            error, self.action_error = self.action_error, None
            raise error
        if self.callback_entered:
            self.active_callbacks += 1
            self.max_active_callbacks = max(self.max_active_callbacks, self.active_callbacks)
            self.callback_entered.set()
            if self.callback_release:
                assert self.callback_release.wait(3)
            self.active_callbacks -= 1
        return {"verified": True, "action_id": plan.action_id}

    def save_copy(self, identity: WindowIdentity, destination: Path):
        self._claim_observed("desktop.migrate_standalone")
        destination.write_bytes(b"fixture save-copy bytes")
        return SavedCopy(str(destination), sha256(destination.read_bytes()).hexdigest(), True, True, "source-fingerprint")

    def verify_source_preserved(self, identity: WindowIdentity, source: DesktopModelObservation):
        return SourcePreservation(
            self.source_window_open and self.preserve_source,
            source.standalone_model_id or "",
            source.fingerprint or "",
            bool(source.dirty),
        )

    def _claim_observed(self, operation: str):
        if self.store is None:
            return
        row = self.store.db.execute(
            "SELECT operation_id FROM operations WHERE operation=? AND status='RUNNING' ORDER BY rowid DESC LIMIT 1", (operation,),
        ).fetchone()
        assert row is not None, f"{operation} callback preceded durable RUNNING claim"
        job = self.store.operation_job(row[0])
        assert job is not None and job["status"] == "RUNNING"
        assert self.store.events(job["job_id"])[-1]["event"] == "DesktopOperationClaimed"


class FixtureEngineQueue:
    def __init__(self):
        self.models = {MODEL.model_ref: MODEL, MODEL_B.model_ref: MODEL_B}
        self.model_ids = {"model-a-id": MODEL, "model-b-id": MODEL_B}
        self.calls: list[tuple[str, dict, ModelRef | None]] = []
        self.store: OperationStore | None = None
        self.mutate_on_shell_execute = False

    def __call__(self, operation: str, arguments, model_ref: ModelRef | None, context: DesktopDispatchContext):
        args = dict(arguments)
        self.calls.append((operation, args, model_ref, context))
        if self.store is not None:
            claimed = self.store.get_operation(context.operation_id)
            assert claimed is not None and claimed["status"] == "RUNNING"
            assert claimed["idempotency_key"] == context.idempotency_key
            assert claimed["request_hash"] == context.request_hash
        if operation == "desktop.binding.resolve":
            return self._response(self.model_ids[args["model_ref_id"]])
        if operation == "desktop.binding.inspect":
            return self._response(self.models[model_ref])
        if operation == "desktop.shell_execute":
            if self.mutate_on_shell_execute:
                current = self.models[model_ref]
                self.models[model_ref] = ManagedModelSnapshot(
                    current.model_ref, current.server_endpoint, current.model_tag, "9" * 64,
                )
            return {"success": True, "data": {"source_sha256": args["source_sha256"], "model_ref": model_ref.as_dict(), "executed": True}}
        if operation == "model.load":
            target = args["target_session_id"]
            snapshot = ManagedModelSnapshot(ModelRef(target, "server-" + target, "migrated-model", 1), "127.0.0.1:2040", "migrated-model", "d" * 64)
            self.models[snapshot.model_ref] = snapshot
            return self._response(snapshot)
        raise AssertionError(f"unexpected engine queue operation {operation}")

    @staticmethod
    def _response(snapshot: ManagedModelSnapshot):
        return {"success": True, "data": {
            "model_ref": snapshot.model_ref.as_dict(),
            "server_endpoint": snapshot.server_endpoint,
            "model_tag": snapshot.model_tag,
            "fingerprint": snapshot.fingerprint,
        }}


class Harness:
    def __init__(self, tmp_path: Path, *, windows: list[WindowObservation] | None = None, permissions=None, clock=None):
        self.store = OperationStore(tmp_path / "operations.sqlite3")
        self.adapter = FixtureDesktopAdapter(windows)
        self.adapter.store = self.store
        self.engine = FixtureEngineQueue()
        self.engine.store = self.store
        self.permissions = permissions if permissions is not None else {"READ", "HOST_CONTROL", "TRUSTED_CODE"}
        self.clock_value = [0.0]
        self.save_root = tmp_path.resolve()

        def resolve_action(project_id, action):
            if action.get("action_id") != "safe.command" or action.get("target") != "window":
                raise DesktopOperationError("ACTION_NOT_AUTHORIZED", "action is not in the trusted catalog", category=BLOCKED_PERMISSION, safe_retry=True)
            return DesktopActionPlan("safe.command", bool(action.get("mutates_model", False)), {"target": "window"})

        def resolve_artifact(project_id, artifact_id):
            return TrustedSourceArtifact(artifact_id, "e" * 64, 40, "project-artifact-store")

        def authorize_save_copy(project_id, destination):
            return destination.is_relative_to(self.save_root)

        def resolve_permission(project_id, permission):
            return permission in self.permissions

        self.coordinator = DesktopCoordinator(
            store=self.store,
            adapter=self.adapter,
            authorize=resolve_permission,
            engine_queue=self.engine,
            resolve_action=resolve_action,
            resolve_artifact=resolve_artifact,
            authorize_save_copy=authorize_save_copy,
            handle_ttl_s=10,
            clock=clock or (lambda: self.clock_value[0]),
        )

    def close(self):
        self.store.close()

    def windows(self):
        result = self.coordinator.dispatch("desktop.status", {"project_id": "proj-1"})
        assert result["data"]["status"] == AVAILABLE
        return [item["window_ref"] for item in result["data"]["windows"]]

    def bind(self, window_ref: str, model_ref: str = "model-a-id", key: str = "bind-1"):
        return self.coordinator.dispatch("desktop.bind", {
            "project_id": "proj-1", "idempotency_key": key, "window_ref": window_ref, "model_ref": model_ref,
        })


def _action_args(window_ref: str, key: str, *, action=None):
    return {"project_id": "proj-1", "idempotency_key": key, "window_ref": window_ref,
            "action": action or {"action_id": "safe.command", "target": "window"}}


def _ops(harness: Harness, key: str):
    return harness.store.db.execute("SELECT operation_id, status, result FROM operations WHERE idempotency_key=?", (key,)).fetchone()


def test_coordinator_requires_real_store_and_all_trusted_production_dependencies(tmp_path):
    with pytest.raises(TypeError, match="real OperationStore"):
        DesktopCoordinator(store=object(), adapter=object(), authorize=lambda *_: True,
                          engine_queue=lambda *_: {},
                          resolve_action=lambda *_: None, resolve_artifact=lambda *_: None,
                          authorize_save_copy=lambda *_: True)

    store = OperationStore(tmp_path / "empty.sqlite")
    try:
        with pytest.raises(TypeError, match="trusted dependencies"):
            DesktopCoordinator(store=store, adapter=None, authorize=lambda *_: True,
                              engine_queue=lambda *_: {},
                              resolve_action=lambda *_: None, resolve_artifact=lambda *_: None,
                              authorize_save_copy=lambda *_: True)
    finally:
        store.close()


def test_status_mints_short_lived_ref_only_from_live_complete_observation(tmp_path):
    harness = Harness(tmp_path)
    try:
        result = harness.coordinator.dispatch("desktop.status", {"project_id": "proj-1", "runtime_id": "runtime-a"})
        row = result["data"]["windows"][0]
        assert result["data"]["status"] == AVAILABLE
        assert row["window_ref"].startswith("dw_")
        assert row["model_binding"] == "UNKNOWN"
        assert row["title"] == "COMSOL Model A"  # display hint only; not identity proof
        assert not harness.store.db.execute("SELECT count(*) FROM operations").fetchone()[0]

        blocked = Harness(tmp_path / "blocked", permissions={"HOST_CONTROL"})
        try:
            denied = blocked.coordinator.dispatch("desktop.status", {"project_id": "proj-1"})
            assert denied["success"] is False
            assert denied["error"]["code"] == "PERMISSION_REQUIRED"
            assert denied["data"]["status"] == BLOCKED_PERMISSION
        finally:
            blocked.close()

        incomplete_identity = WindowObservation(
            WindowIdentity("fixture", "WINDOW:9", 1009, "birth-9", "sid:test-user", "desktop-1", "UNVERIFIED"),
            title="COMSOL Model A", control_status=AVAILABLE,
        )
        incomplete = Harness(tmp_path / "incomplete", windows=[incomplete_identity])
        try:
            result = incomplete.coordinator.dispatch("desktop.status", {"project_id": "proj-1"})
            assert result["data"]["status"] == MODEL_BINDING_UNKNOWN
            assert result["data"]["windows"][0]["window_ref"] is None
        finally:
            incomplete.close()
    finally:
        harness.close()


def test_optional_catalog_string_fields_reject_explicit_null_or_wrong_types(tmp_path):
    harness = Harness(tmp_path)
    try:
        for field, value in (("runtime_id", None), ("runtime_id", 17), ("request_id", None), ("request_id", [])):
            result = harness.coordinator.dispatch("desktop.status", {"project_id": "proj-1", field: value})
            assert result["success"] is False
            assert result["error"]["code"] == "INVALID_REQUEST"
        rejected_write = harness.coordinator.dispatch("desktop.bind", {
            "project_id": "proj-1", "idempotency_key": "null-request-id", "request_id": None,
            "window_ref": "dw_unavailable", "model_ref": "model-a-id",
        })
        assert rejected_write["success"] is False
        assert rejected_write["error"]["code"] == "INVALID_REQUEST"
        assert _ops(harness, "null-request-id") is None
    finally:
        harness.close()


def test_bind_requires_live_endpoint_and_model_tag_crosscheck_not_title(tmp_path):
    harness = Harness(tmp_path)
    try:
        token = harness.windows()[0]
        harness.adapter.models["WINDOW:1"] = DesktopModelObservation("SERVER", "127.0.0.1:9999", "model-a", fingerprint="a" * 64)
        failed = harness.bind(token)
        assert failed["success"] is False
        assert failed["data"]["status"] == MODEL_BINDING_UNKNOWN
        assert _ops(harness, "bind-1")[1] == "FAILED"

        harness.adapter.models["WINDOW:1"] = DesktopModelObservation("SERVER", ENDPOINT, "model-a", fingerprint="a" * 64)
        verified = harness.bind(token, key="bind-2")
        assert verified["success"] is True
        assert verified["data"]["desktop_binding_verified"] is True
        assert verified["data"]["binding_evidence"]["model_ref"] == MODEL.model_ref.as_dict()
    finally:
        harness.close()


def test_model_fingerprint_is_rechecked_before_ui_side_effect_and_after_shell_execute(tmp_path):
    harness = Harness(tmp_path)
    try:
        token = harness.windows()[0]
        assert harness.bind(token)["success"]
        current = harness.engine.models[MODEL.model_ref]
        harness.engine.models[MODEL.model_ref] = ManagedModelSnapshot(
            current.model_ref, current.server_endpoint, current.model_tag, "f" * 64,
        )
        denied = harness.coordinator.dispatch("desktop.select_node", {
            "project_id": "proj-1", "idempotency_key": "stale-fingerprint", "window_ref": token,
            "path": NODE_PATH_A,
        })
        assert denied["success"] is False
        assert denied["error"]["code"] == "MODEL_IDENTITY_MISMATCH"
        assert not harness.adapter.callback_counts.get("desktop.select_node")

        # A newly bound model may execute trusted code, but an unanticipated
        # post-execution fingerprint change makes the outcome unknown.
        token = harness.windows()[0]
        assert harness.bind(token, key="rebind-new-fingerprint")["success"]
        harness.engine.mutate_on_shell_execute = True
        result = harness.coordinator.dispatch("desktop.shell_execute", {
            "project_id": "proj-1", "idempotency_key": "shell-fingerprint-change", "window_ref": token,
            "source_artifact": "artifact-1", "expected_model_ref": "model-a-id",
        })
        assert result["success"] is False
        assert result["error"]["code"] == "MODEL_IDENTITY_MISMATCH"
        assert result["execution"]["status"] == "UNKNOWN"
        assert _ops(harness, "shell-fingerprint-change")[1] == "UNKNOWN"
    finally:
        harness.close()


def test_all_catalog_handlers_are_durable_and_success_requires_verified_adapter_evidence(tmp_path):
    harness = Harness(tmp_path, windows=[_window(1), _window(2)])
    try:
        token = harness.windows()[0]
        assert harness.bind(token)["success"] is True

        shown = harness.coordinator.dispatch("desktop.show_model", {
            "project_id": "proj-1", "idempotency_key": "show-1", "window_ref": token, "model_ref": "model-a-id",
        })
        assert shown["success"] is True

        selected = harness.coordinator.dispatch("desktop.select_node", {
            "project_id": "proj-1", "idempotency_key": "select-1", "window_ref": token, "path": NODE_PATH_A,
        })
        assert selected["success"] is True
        assert selected["data"]["selected_path"] == NODE_PATH_A

        captured = harness.coordinator.dispatch("desktop.capture", {
            "project_id": "proj-1", "idempotency_key": "capture-1", "window_ref": token, "region": "graphics",
        })
        assert captured["success"] is True
        assert captured["data"]["scope"] == "target_window"

        acted = harness.coordinator.dispatch("desktop.action", _action_args(token, "action-1", action={
            "action_id": "safe.command", "target": "window", "mutates_model": True,
        }))
        assert acted["success"] is True
        assert any(call[0] == "desktop.binding.inspect" for call in harness.engine.calls)

        shell = harness.coordinator.dispatch("desktop.shell_execute", {
            "project_id": "proj-1", "idempotency_key": "shell-1", "window_ref": token,
            "source_artifact": "artifact-1", "expected_model_ref": "model-a-id",
        })
        assert shell["success"] is True
        assert shell["data"]["source_sha256"] == "e" * 64
        shell_calls = [call for call in harness.engine.calls if call[0] == "desktop.shell_execute"]
        assert len(shell_calls) == 1
        assert shell_calls[0][3].idempotency_key == "shell-1"
        assert harness.engine.calls[-1][0] == "desktop.binding.inspect"  # post-execution identity/fingerprint recheck

        standalone_token = harness.windows()[1]
        harness.adapter.models["WINDOW:2"] = DesktopModelObservation(
            "STANDALONE", standalone_model_id="standalone-source-1", fingerprint="source-fingerprint", dirty=True,
        )
        destination = tmp_path / "authorized save copy.mph"
        migrated = harness.coordinator.dispatch("desktop.migrate_standalone", {
            "project_id": "proj-1", "idempotency_key": "migrate-1", "window_ref": standalone_token,
            "target_session_id": "session-target", "save_policy": {
                "mode": "save_copy", "destination_path": str(destination), "overwrite": False,
            },
        })
        assert migrated["success"] is True
        assert migrated["data"]["migration_mode"] == "explicit_save_copy"
        assert migrated["data"]["source_dirty_preserved"] is True
        assert migrated["data"]["target_model_ref"]["session_id"] == "session-target"
        assert migrated["data"]["managed_model_loaded"] is True
        assert migrated["data"]["desktop_binding_verified"] is False
        assert migrated["data"]["source_window_identity"] == _window(2).identity.fingerprint()
        assert "WINDOW:2" not in harness.adapter.show_model_calls
        assert destination.is_file()
    finally:
        harness.close()


def test_operation_store_replays_same_key_and_rejects_body_conflict(tmp_path):
    harness = Harness(tmp_path)
    try:
        token = harness.windows()[0]
        assert harness.bind(token)["success"] is True
        args = {"project_id": "proj-1", "idempotency_key": "once", "window_ref": token, "path": NODE_PATH_A}
        first = harness.coordinator.dispatch("desktop.select_node", args)
        count_after_first = len([call for call in harness.engine.calls if call[0] == "desktop.binding.inspect"])
        replay = harness.coordinator.dispatch("desktop.select_node", {**args, "request_id": "new-trace-only"})
        assert replay == first
        assert len([call for call in harness.engine.calls if call[0] == "desktop.binding.inspect"]) == count_after_first
        conflict = harness.coordinator.dispatch("desktop.select_node", {**args, "path": {"segments": [
            {"collection": "components", "tag": "comp1"},
            {"collection": "geometry", "tag": "geom1"},
            {"collection": "features", "tag": "different"},
        ]}})
        assert conflict["success"] is False
        assert conflict["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    finally:
        harness.close()


def test_select_node_accepts_only_the_catalog_typed_nodepath_before_claim(tmp_path):
    harness = Harness(tmp_path)
    try:
        token = harness.windows()[0]
        assert harness.bind(token)["success"]
        invalid_paths = [
            "component/geom1/blk1",
            ["components", "comp1"],
            {"path": "component/geom1/blk1"},
            {"segments": [{"collection": "components", "tag": "comp1", "accessor": "bad"}]},
            {"segments": [{"collection": "components", "tag": "comp1", "extra": True}]},
        ]
        for index, path in enumerate(invalid_paths):
            key = f"invalid-nodepath-{index}"
            result = harness.coordinator.dispatch("desktop.select_node", {
                "project_id": "proj-1", "idempotency_key": key, "window_ref": token, "path": path,
            })
            assert result["success"] is False
            assert result["error"]["code"] == "INVALID_REQUEST"
            assert _ops(harness, key) is None

        accessor = {"segments": [{"accessor": "study"}]}
        accepted = harness.coordinator.dispatch("desktop.select_node", {
            "project_id": "proj-1", "idempotency_key": "accessor-nodepath", "window_ref": token, "path": accessor,
        })
        assert accepted["success"] is True
        assert accepted["data"]["selected_path"] == accessor
    finally:
        harness.close()


def test_idempotency_duplicate_while_running_never_replays_callback(tmp_path):
    harness = Harness(tmp_path)
    try:
        token = harness.windows()[0]
        assert harness.bind(token)["success"]
        entered, release = threading.Event(), threading.Event()
        harness.adapter.callback_entered, harness.adapter.callback_release = entered, release
        args = _action_args(token, "running-1")
        result_box = []
        thread = threading.Thread(target=lambda: result_box.append(harness.coordinator.dispatch("desktop.action", args)))
        thread.start()
        assert entered.wait(2)
        duplicate = harness.coordinator.dispatch("desktop.action", args)
        assert duplicate["success"] is False
        assert duplicate["error"]["code"] == "DESKTOP_OPERATION_IN_PROGRESS"
        assert harness.adapter.callback_counts["desktop.action"] == 1
        release.set()
        thread.join(3)
        assert not thread.is_alive()
        assert result_box[0]["success"] is True
    finally:
        if 'release' in locals():
            release.set()
        harness.close()


def test_same_window_and_server_session_callbacks_are_serialized(tmp_path):
    windows = [_window(1, session="shared-session"), _window(2, session="shared-session")]
    harness = Harness(tmp_path, windows=windows)
    try:
        refs = harness.windows()
        assert harness.bind(refs[0], key="bind-a")["success"]
        assert harness.bind(refs[1], key="bind-b")["success"]
        entered, release = threading.Event(), threading.Event()
        harness.adapter.callback_entered, harness.adapter.callback_release = entered, release
        results = []
        first = threading.Thread(target=lambda: results.append(harness.coordinator.dispatch("desktop.action", _action_args(refs[0], "serial-a"))))
        second = threading.Thread(target=lambda: results.append(harness.coordinator.dispatch("desktop.action", _action_args(refs[1], "serial-b"))))
        first.start()
        assert entered.wait(2)
        second.start()
        time.sleep(0.05)
        assert harness.adapter.max_active_callbacks == 1
        release.set()
        first.join(3)
        second.join(3)
        assert not first.is_alive() and not second.is_alive()
        assert all(row["success"] for row in results)
        assert harness.adapter.max_active_callbacks == 1
    finally:
        if 'release' in locals():
            release.set()
        harness.close()


def test_unknown_callback_result_is_durable_and_never_reexecuted(tmp_path):
    harness = Harness(tmp_path)
    try:
        token = harness.windows()[0]
        assert harness.bind(token)["success"]
        harness.adapter.action_error = RuntimeError("platform response lost after dispatch")
        args = _action_args(token, "unknown-1")
        first = harness.coordinator.dispatch("desktop.action", args)
        row = _ops(harness, "unknown-1")
        assert first["success"] is False
        assert first["execution"]["status"] == "UNKNOWN"
        assert row[1] == "UNKNOWN"
        callback_count = harness.adapter.callback_counts["desktop.action"]
        replay = harness.coordinator.dispatch("desktop.action", args)
        assert replay == first
        assert harness.adapter.callback_counts["desktop.action"] == callback_count == 1
    finally:
        harness.close()


def test_window_process_birth_and_lease_expiry_fail_closed(tmp_path):
    harness = Harness(tmp_path)
    try:
        token = harness.windows()[0]
        old = harness.adapter.windows["WINDOW:1"]
        new_identity = WindowIdentity("fixture", "WINDOW:1", 1001, "different-birth", "sid:test-user", "desktop-1", "6.4.0.293")
        harness.adapter.windows["WINDOW:1"] = WindowObservation(new_identity, title=old.title, control_status=AVAILABLE)
        changed = harness.coordinator.dispatch("desktop.bind", {
            "project_id": "proj-1", "idempotency_key": "stale-window", "window_ref": token, "model_ref": "model-a-id",
        })
        assert changed["success"] is False
        assert changed["error"]["code"] == "WINDOW_IDENTITY_CHANGED"
        assert not [call for call in harness.engine.calls if call[0] == "desktop.binding.resolve"]

        expiring = Harness(tmp_path / "expiring")
        try:
            ref = expiring.windows()[0]
            expiring.clock_value[0] = 11.0
            expired = expiring.bind(ref)
            assert expired["success"] is False
            assert expired["error"]["code"] == "WINDOW_HANDLE_EXPIRED"
        finally:
            expiring.close()
    finally:
        harness.close()


def test_shell_execute_requires_both_host_control_and_trusted_code(tmp_path):
    harness = Harness(tmp_path, permissions={"READ", "HOST_CONTROL"})
    try:
        token = harness.windows()[0]
        assert harness.bind(token)["success"]
        result = harness.coordinator.dispatch("desktop.shell_execute", {
            "project_id": "proj-1", "idempotency_key": "shell-denied", "window_ref": token,
            "source_artifact": "artifact-1", "expected_model_ref": "model-a-id",
        })
        assert result["success"] is False
        assert result["data"]["status"] == BLOCKED_PERMISSION
        assert result["error"]["code"] == "PERMISSION_REQUIRED"
        assert not [call for call in harness.engine.calls if call[0] == "desktop.shell_execute"]
    finally:
        harness.close()


def test_capture_never_expands_to_full_desktop_and_adapter_cannot_fake_success(tmp_path):
    harness = Harness(tmp_path)
    try:
        token = harness.windows()[0]
        assert harness.bind(token)["success"]
        rejected = harness.coordinator.dispatch("desktop.capture", {
            "project_id": "proj-1", "idempotency_key": "capture-desktop", "window_ref": token, "region": "entire_desktop",
        })
        assert rejected["success"] is False
        assert rejected["error"]["code"] == "CAPTURE_SCOPE_REJECTED"
        assert not _ops(harness, "capture-desktop")

        class BadCapture(FixtureDesktopAdapter):
            def capture(self, identity, region):
                return {"verified": True, "region": region, "scope": "entire_desktop", "artifact_id": "fake", "sha256": "f" * 64}

        bad = Harness(tmp_path / "bad")
        try:
            bad.adapter = BadCapture()
            bad.adapter.store = bad.store
            bad.coordinator.adapter = bad.adapter
            ref = bad.windows()[0]
            assert bad.bind(ref)["success"]
            result = bad.coordinator.dispatch("desktop.capture", {
                "project_id": "proj-1", "idempotency_key": "capture-bad", "window_ref": ref, "region": "window",
            })
            assert result["success"] is False
            assert result["error"]["code"] == "CAPTURE_SCOPE_REJECTED"
            assert _ops(bad, "capture-bad")[1] == "UNKNOWN"
        finally:
            bad.close()
    finally:
        harness.close()


def test_standalone_migration_requires_save_copy_and_keeps_source_on_failure(tmp_path):
    harness = Harness(tmp_path)
    try:
        token = harness.windows()[0]
        harness.adapter.models["WINDOW:1"] = DesktopModelObservation(
            "STANDALONE", standalone_model_id="standalone-source-1", fingerprint="source-fingerprint", dirty=True,
        )
        no_copy = harness.coordinator.dispatch("desktop.migrate_standalone", {
            "project_id": "proj-1", "idempotency_key": "bad-save", "window_ref": token,
            "target_session_id": "session-target", "save_policy": {"mode": "save", "destination_path": str(tmp_path / "x.mph"), "overwrite": False},
        })
        assert no_copy["success"] is False
        assert no_copy["error"]["code"] == "SAVE_COPY_REQUIRED"

        harness.adapter.preserve_source = False
        destination = tmp_path / "partial-copy.mph"
        failed = harness.coordinator.dispatch("desktop.migrate_standalone", {
            "project_id": "proj-1", "idempotency_key": "migration-source-lost", "window_ref": token,
            "target_session_id": "session-target", "save_policy": {
                "mode": "save_copy", "destination_path": str(destination), "overwrite": False,
            },
        })
        assert failed["success"] is False
        assert failed["error"]["code"] == "SOURCE_MODEL_NOT_PRESERVED"
        assert failed["execution"]["status"] == "UNKNOWN"
        assert destination.is_file()
        assert harness.adapter.source_window_open is True
        calls = len([call for call in harness.engine.calls if call[0] == "model.load"])
        replay = harness.coordinator.dispatch("desktop.migrate_standalone", {
            "project_id": "proj-1", "idempotency_key": "migration-source-lost", "window_ref": token,
            "target_session_id": "session-target", "save_policy": {
                "mode": "save_copy", "destination_path": str(destination), "overwrite": False,
            },
        })
        assert replay == failed
        assert len([call for call in harness.engine.calls if call[0] == "model.load"]) == calls
    finally:
        harness.close()


def test_migration_returns_loaded_managed_model_without_switching_source_window(tmp_path):
    harness = Harness(tmp_path)
    try:
        token = harness.windows()[0]
        source = DesktopModelObservation(
            "STANDALONE", standalone_model_id="standalone-source-1", fingerprint="source-fingerprint", dirty=True,
        )
        harness.adapter.models["WINDOW:1"] = source
        harness.adapter.show_model_error = RuntimeError("migration must not ask the source window to show a server model")
        destination = tmp_path / "saved but not migrated.mph"
        migrated = harness.coordinator.dispatch("desktop.migrate_standalone", {
            "project_id": "proj-1", "idempotency_key": "migration-no-ui-switch", "window_ref": token,
            "target_session_id": "session-target", "save_policy": {
                "mode": "save_copy", "destination_path": str(destination), "overwrite": False,
            },
        })
        assert migrated["success"] is True
        assert migrated["data"]["managed_model_loaded"] is True
        assert migrated["data"]["desktop_binding_verified"] is False
        assert migrated["data"]["target_model_ref"]["session_id"] == "session-target"
        assert harness.adapter.show_model_calls == []
        assert destination.is_file()
        assert harness.adapter.models["WINDOW:1"] == source
        assert harness.adapter.source_window_open is True
    finally:
        harness.close()


def test_second_live_coordinator_does_not_reconcile_another_owners_running_claim(tmp_path):
    harness = Harness(tmp_path)
    try:
        token = harness.windows()[0]
        assert harness.bind(token)["success"]
        entered, release = threading.Event(), threading.Event()
        harness.adapter.callback_entered, harness.adapter.callback_release = entered, release
        args = _action_args(token, "shared-running-operation")
        owner_result = []
        owner_thread = threading.Thread(target=lambda: owner_result.append(harness.coordinator.dispatch("desktop.action", args)))
        owner_thread.start()
        assert entered.wait(2)

        other = DesktopCoordinator(
            store=harness.store,
            adapter=harness.adapter,
            authorize=harness.coordinator.authorize,
            engine_queue=harness.engine,
            resolve_action=harness.coordinator.resolve_action,
            resolve_artifact=harness.coordinator.resolve_artifact,
            authorize_save_copy=harness.coordinator.authorize_save_copy,
            handle_ttl_s=10,
            clock=lambda: harness.clock_value[0],
        )
        waiting = other.dispatch("desktop.action", args)
        assert waiting["success"] is False
        assert waiting["error"]["code"] == "DESKTOP_OPERATION_OWNER_UNVERIFIED"
        operation = harness.store.get_operation(_ops(harness, "shared-running-operation")[0])
        job = harness.store.operation_job(operation["operation_id"])
        assert operation["status"] == job["status"] == "RUNNING"
        assert operation["result"] is None and job["result"] is None
        assert harness.adapter.callback_counts["desktop.action"] == 1

        release.set()
        owner_thread.join(3)
        assert not owner_thread.is_alive()
        assert owner_result[0]["success"] is True
        operation = harness.store.get_operation(operation["operation_id"])
        job = harness.store.operation_job(operation["operation_id"])
        assert operation["status"] == job["status"] == "SUCCEEDED"
        assert operation["result"] == owner_result[0]
    finally:
        if "release" in locals():
            release.set()
        harness.close()


def test_native_factory_is_not_a_fixture_and_keeps_control_capability_unsupported():
    adapter = create_native_metadata_adapter()
    assert not isinstance(adapter, FixtureDesktopAdapter)
    assert isinstance(adapter, MetadataOnlyDesktopAdapter)
    status = adapter.status()
    assert status.get("control_status") == UNSUPPORTED_CONTROL
    assert status.get("native_evidence") == "NOT_RUN"
    assert status.get("source_capabilities") == {"desktop.status": "PROCESS_WINDOW_IDENTITY_SOURCE_IMPLEMENTED"}
    assert status.get("operation_gaps", {}).get("desktop.capture", "").startswith("UNSUPPORTED_CONTROL")
    with pytest.raises(DesktopOperationError, match="does not implement desktop.show_model"):
        adapter.show_model(_window().identity, MODEL)
    with pytest.raises(DesktopOperationError, match="does not implement desktop.capture"):
        adapter.capture(_window().identity, "window")
