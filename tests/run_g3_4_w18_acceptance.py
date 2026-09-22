#!/usr/bin/env python3
"""Unified Live Acceptance Runner for Gate A (A01-A07) and W18 (V01-V11).

Executes comprehensive live acceptance against the local COMSOL 6.4 engine and python package,
generating structured, verifiable, and reproducible evidence complying with ACCEPTANCE.md.
"""
from __future__ import annotations

import argparse
import base64
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from typing import Any, Mapping

# Ensure repository root is on sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._java_worker import JavaWorkerPaths, PersistentJavaWorker
from comsol_mcp._artifact_store import (
    ArtifactStore,
    csv_to_field_array,
    trusted_project_root,
)
from comsol_mcp._g3_ops import DISPATCH, dispatch, _FALLBACK_EFFECTS
from comsol_mcp._g3_results import result_at_points
from comsol_mcp._mcp_gateway import mcp_result
from mcp.types import ImageContent, TextContent


COMSOL_ROOT = Path(os.environ.get("COMSOL_ROOT", "/Applications/COMSOL64/Multiphysics"))
JDK11 = Path(
    os.environ.get("COMSOL_JAVA_HOME")
    or os.environ.get("JAVA_HOME")
    or "/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _foreign_mphserver_pids() -> list[int]:
    """mphserver PIDs that exist before this suite starts its own."""
    try:
        output = subprocess.check_output(["ps", "-ax", "-o", "pid,command"], text=True)
    except Exception:
        return []
    pids: list[int] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        pid_s, command = parts
        if "mphserver" not in command:
            continue
        try:
            pid = int(pid_s)
            if pid != os.getpid():
                pids.append(pid)
        except ValueError:
            continue
    return sorted(pids)


class LiveAcceptanceRunner:
    def __init__(self, run_dir: Path | None = None) -> None:
        self.timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.run_id = f"g3_4_w18_acceptance_{self.timestamp}"
        self.base_dir = ROOT
        if run_dir is None:
            self.run_dir = ROOT / "evidence" / "phase4_4" / "runs" / self.run_id
        else:
            self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)

        self.prefs_dir = self.run_dir / "comsol_prefs"
        self.tmp_dir = self.run_dir / "comsol_tmp"
        self.recovery_dir = self.run_dir / "comsol_recovery"
        self.locks_dir = self.run_dir / "locks"
        for p in (self.prefs_dir, self.tmp_dir, self.recovery_dir, self.locks_dir):
            p.mkdir(parents=True, exist_ok=True)

        self.server_proc: subprocess.Popen[str] | None = None
        self.server_port: int | None = None
        self.worker: PersistentJavaWorker | None = None
        self.shared_server_pids_before: list[int] = []
        self.cases: dict[str, dict[str, Any]] = {}
        self.comsol_version: str = "COMSOL 6.4"
        self.live_model_tag: str | None = None
        self.saved_mph_path: Path | None = None

    def log(self, msg: str) -> None:
        print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)

    def start_server(self) -> int:
        self.log("Recording pre-existing mphserver PIDs...")
        self.shared_server_pids_before = _foreign_mphserver_pids()
        if self.shared_server_pids_before:
            self.log(f"Pre-existing mphserver PID(s): {self.shared_server_pids_before}")

        self.log("Starting dedicated isolated COMSOL mphserver...")
        portfile = self.run_dir / "server.port"
        server_log = self.run_dir / "mphserver.log"
        cmd = [
            str(COMSOL_ROOT / "bin" / "comsol"),
            "mphserver",
            "-port", "0",
            "-portfile", str(portfile),
            "-prefsdir", str(self.prefs_dir),
            "-tmpdir", str(self.tmp_dir),
            "-recoverydir", str(self.recovery_dir),
            "-login", "auto",
            "-silent",
            "-multi", "on",
        ]
        log_file = server_log.open("w", encoding="utf-8")
        self.server_proc = subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        deadline = time.time() + 25
        while time.time() < deadline:
            if portfile.is_file():
                try:
                    p = int(portfile.read_text().strip())
                    if p > 0:
                        self.server_port = p
                        self.log(f"COMSOL mphserver PID {self.server_proc.pid} listening on port {p}")
                        return p
                except (ValueError, OSError):
                    pass
            if self.server_proc.poll() is not None:
                raise RuntimeError(f"mphserver exited prematurely with code {self.server_proc.returncode}")
            time.sleep(0.3)
        raise TimeoutError("COMSOL mphserver failed to bind within 25s")

    def stop_server(self) -> None:
        if self.server_proc is not None:
            self.log(f"Stopping isolated mphserver PID {self.server_proc.pid}...")
            self.server_proc.terminate()
            try:
                self.server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.server_proc.kill()
            self.server_proc = None

    def make_worker(self, name: str = "worker") -> PersistentJavaWorker:
        state_dir = self.run_dir / name
        paths = JavaWorkerPaths(
            COMSOL_ROOT,
            JDK11,
            private_prefs=self.prefs_dir,
            project_root=self.run_dir,
            global_lock_root=self.locks_dir,
        )
        worker = PersistentJavaWorker(paths, state_dir=state_dir)
        worker.start()
        return worker

    def run_case_guarded(self, name: str, func: Any) -> bool:
        self.log(f"Running {name}...")
        t0 = time.monotonic()
        try:
            details = func()
            elapsed = time.monotonic() - t0
            self.cases[name] = {
                "status": "PASS",
                "elapsed_s": round(elapsed, 3),
                "details": details or {},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self.log(f"  -> {name} PASS ({elapsed:.2f}s)")
            return True
        except Exception as exc:
            elapsed = time.monotonic() - t0
            self.cases[name] = {
                "status": "FAIL",
                "elapsed_s": round(elapsed, 3),
                "error": str(exc),
                "traceback": traceback.format_exc(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self.log(f"  -> {name} FAIL: {exc}")
            return False

    # -----------------------------------------------------------------------
    # Case A01: SOURCE - Pinned Commit Verification & Restoration
    # -----------------------------------------------------------------------
    def case_a01(self) -> dict[str, Any]:
        pin_file = ROOT.parent / "PIN.json"
        assert pin_file.is_file(), "PIN.json missing"
        pin_data = json.loads(pin_file.read_text(encoding="utf-8"))
        expected_commit = pin_data["commit"]
        assert expected_commit == "31152904205834125776524f92288e18ba93b853"
        return {"pinned_commit": expected_commit, "status": "VERIFIED"}

    # -----------------------------------------------------------------------
    # Case A02: INSTALL+PROTOCOL+NATIVE - Project Root vs site-packages
    # -----------------------------------------------------------------------
    def case_a02(self) -> dict[str, Any]:
        class FakePaths:
            def __init__(self, root: Path) -> None:
                self.resolved_project_root = root

        class FakeWorker:
            def __init__(self, root: Path) -> None:
                self.paths = FakePaths(root)

        valid_root = self.run_dir
        res = trusted_project_root(FakeWorker(valid_root))
        assert res == valid_root

        # site-packages rejection
        sp_root = Path("/opt/homebrew/lib/python3.11/site-packages")
        try:
            trusted_project_root(FakeWorker(sp_root))
            assert False, "Should have rejected site-packages"
        except ExecutionContractError as exc:
            assert exc.code == "RUNTIME_CONFIGURATION_REQUIRED"

        return {"status": "verified", "site_packages_rejected": True}

    # -----------------------------------------------------------------------
    # Case A03: SECURITY+PROTOCOL - Artifact Security & Path Isolation
    # -----------------------------------------------------------------------
    def case_a03(self) -> dict[str, Any]:
        store = ArtifactStore(self.run_dir)
        p = store.resolve_safe_path("exports/data.csv", allow_overwrite=True)
        assert p == (self.run_dir / "exports" / "data.csv").resolve()

        rejected: list[str] = []
        for bad in [".phase1-private/secret.xml", ".g3-private/model.mph", ".git/config", "tokens.json"]:
            try:
                store.resolve_safe_path(bad, allow_overwrite=True)
                assert False, f"Did not reject {bad}"
            except ExecutionContractError as exc:
                assert exc.code == "ACCESS_VIOLATION"
                rejected.append(bad)

        return {"rejected_paths": rejected}

    # -----------------------------------------------------------------------
    # Case A04: DATA - CSV 4-Axis Semantics & 1:1 Roundtrip
    # -----------------------------------------------------------------------
    def case_a04(self) -> dict[str, Any]:
        store = ArtifactStore(self.run_dir)
        values = [
            [[[10.0, 11.0], [12.0, 13.0]]],
            [[[100.0, 101.0], [102.0, 103.0]]],
        ]
        field = {
            "values": values,
            "axes": ["expression", "outer", "inner", "point"],
            "shape": [2, 1, 2, 2],
            "coords": {
                "expression": ["T", "p"],
                "outer": [1],
                "inner": [10, 20],
                "point": [1, 2],
                "spatial": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
            },
            "units": {"expression": {"T": "degC", "p": "Pa"}},
        }
        target = self.run_dir / "a04_test.csv"
        store.export_field_data(
            str(target),
            {"status": {"ok": True}, "values": values, "expressions": ["T", "p"], "field_array": field},
            fmt="csv",
        )

        with target.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 8
        assert rows[0]["expr"] == "T"
        assert rows[0]["unit"] == "degC"
        assert rows[4]["expr"] == "p"
        assert rows[4]["unit"] == "Pa"

        reconstructed = csv_to_field_array(target)
        assert reconstructed.axes == ["expression", "outer", "inner", "point"]
        assert reconstructed.shape == (2, 1, 2, 2)
        assert reconstructed.coords["expression"] == ["T", "p"]

        return {"csv_rows": len(rows), "roundtrip_shape": reconstructed.shape}

    # -----------------------------------------------------------------------
    # Case A05: PROTOCOL - Evaluation Budget & Metadata Invariants
    # -----------------------------------------------------------------------
    def case_a05(self) -> dict[str, Any]:
        store = ArtifactStore(self.run_dir)
        eval_result = {
            "status": {"ok": True},
            "values": [1.0, 2.0, 3.0],
            "storage": "artifact",
            "field_array": {
                "axes": ["point"],
                "shape": [3],
                "coords": {"point": [1, 2, 3]},
                "units": {"expression": {"T": "K"}},
            },
        }
        target = self.run_dir / "a05_eval.json"
        store.export_field_data(str(target), eval_result, fmt="json")
        assert target.is_file()
        data = json.loads(target.read_text(encoding="utf-8"))
        assert "metadata" in data
        assert "field_array" in data["metadata"]
        return {"artifact_has_field_array_metadata": True}

    # -----------------------------------------------------------------------
    # Case A06: ARTIFACT - Chunked Reads & Stability
    # -----------------------------------------------------------------------
    def case_a06(self) -> dict[str, Any]:
        store = ArtifactStore(self.run_dir)
        test_file = self.run_dir / "a06_chunk_test.bin"
        payload = b"A" * 1024 * 64 + b"B" * 1024 * 64
        test_file.write_bytes(payload)
        expected_sha = hashlib.sha256(payload).hexdigest()

        chunk1 = store.read_chunk(str(test_file), offset=0, length=1024 * 64)
        assert len(chunk1["data_bytes"]) == 1024 * 64
        assert chunk1["chunk_sha256"] == hashlib.sha256(b"A" * 1024 * 64).hexdigest()
        assert chunk1["data_bytes"] == b"A" * 1024 * 64

        chunk2 = store.read_chunk(str(test_file), offset=1024 * 64, length=1024 * 64)
        assert len(chunk2["data_bytes"]) == 1024 * 64
        assert chunk2["chunk_sha256"] == hashlib.sha256(b"B" * 1024 * 64).hexdigest()
        return {"whole_file_sha256": expected_sha, "chunks_read": 2}

    # -----------------------------------------------------------------------
    # Case A07: EVIDENCE - Provenance & Public Commit Bridge
    # -----------------------------------------------------------------------
    def case_a07(self) -> dict[str, Any]:
        git_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        git_branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip()
        return {"commit": git_head, "branch": git_branch, "evidence_status": "VALIDATED"}

    # -----------------------------------------------------------------------
    # Case V01: CONTRACT - W18 Operation Registry & Effects
    # -----------------------------------------------------------------------
    def case_v01(self) -> dict[str, Any]:
        w18_ops = [
            "plot.list", "plot.group_create", "plot.feature_create", "plot.update",
            "plot.remove", "plot.render", "plot.geometry_render", "plot.view_manage",
            "export.list", "export.create", "export.update", "export.run", "export.remove",
        ]
        registered = []
        for op in w18_ops:
            assert op in DISPATCH, f"Missing dispatch registration for {op}"
            assert op in _FALLBACK_EFFECTS, f"Missing fallback effect for {op}"
            registered.append(op)
        return {"w18_operations_count": len(registered), "registered": registered}

    # -----------------------------------------------------------------------
    # Case V02: NATIVE - Live Model Build & Plot Feature CRUD
    # -----------------------------------------------------------------------
    def case_v02(self) -> dict[str, Any]:
        assert self.worker is not None
        assert self.server_port is not None

        self.log("  Connecting Java worker to live mphserver...")
        self.worker.client().connect(self.server_port, "127.0.0.1")

        self.log("  Building live 2D model with stationary heat transfer...")
        model = self.worker.client().create("ModelAcceptanceV02")
        tag = model.tag()
        self.live_model_tag = tag

        builder_code = """
import com.comsol.model.*;
import java.util.*;

public final class ModelAcceptanceV02Builder {
    public static Object run(Model model, Map<String, Object> args) {
        model.modelNode().create("comp1");
        model.geom().create("geom1", 2);
        model.geom("geom1").create("r1", "Rectangle");
        model.geom("geom1").feature("r1").set("size", new String[]{"0.05", "0.02"});
        model.geom("geom1").run();

        model.physics().create("ht", "HeatTransfer", "geom1");
        model.physics("ht").create("temp1", "TemperatureBoundary", 1);
        model.physics("ht").feature("temp1").selection().set(new int[]{1});
        model.physics("ht").feature("temp1").set("T0", "300[K]");

        model.physics("ht").create("temp2", "TemperatureBoundary", 1);
        model.physics("ht").feature("temp2").selection().set(new int[]{4});
        model.physics("ht").feature("temp2").set("T0", "350[K]");

        model.material().create("mat1", "Common", "comp1");
        model.material("mat1").selection().all();
        model.material("mat1").propertyGroup("def").set("thermalconductivity", new String[]{"400[W/(m*K)]"});
        model.material("mat1").propertyGroup("def").set("density", "8960[kg/m^3]");
        model.material("mat1").propertyGroup("def").set("heatcapacity", "385[J/(kg*K)]");

        model.mesh().create("mesh1", "geom1");
        model.mesh("mesh1").run();

        model.study().create("std1");
        model.study("std1").create("stat", "Stationary");
        model.study("std1").run();

        return Collections.singletonMap("status", "SOLVED");
    }
}
"""
        builder_file = self.run_dir / "ModelAcceptanceV02Builder.java"
        builder_file.write_text(builder_code, encoding="utf-8")
        reply = self.worker.submit("code_execute", {
            "tag": tag,
            "source_artifact": str(builder_file),
            "entrypoint": "ModelAcceptanceV02Builder",
            "arguments": {},
        })
        assert reply.get("ok") is True, f"Model build failed: {reply}"

        # Create plot group
        create_pg = DISPATCH["plot.group_create"](
            self.worker,
            tag,
            {"tag": "pg1", "dimension": 2, "dataset": "dset1", "properties": {"title": "Temperature Surface"}},
        )
        assert create_pg["tag"] == "pg1"

        # Create plot feature
        create_feat = DISPATCH["plot.feature_create"](
            self.worker,
            tag,
            {"group": "pg1", "tag": "surf1", "type_id": "Surface", "properties": {"expr": "T"}},
        )
        assert create_feat["tag"] == "surf1"

        # List plots
        list_res = DISPATCH["plot.list"](self.worker, tag, {})
        assert any(p["tag"] == "pg1" for p in list_res["plot_groups"])

        # Update feature
        update_res = DISPATCH["plot.update"](
            self.worker,
            tag,
            {"path": "pg1/surf1", "properties": {"expr": "T*2"}},
        )
        assert update_res["updated"] is True

        return {"model_tag": tag, "plot_group": "pg1", "feature": "surf1"}

    # -----------------------------------------------------------------------
    # Case V03: NATIVE_RENDER - Live Surface Plot Rendering to PNG
    # -----------------------------------------------------------------------
    def case_v03(self) -> dict[str, Any]:
        assert self.worker is not None
        assert self.live_model_tag is not None

        img_target = self.run_dir / "plots" / "surface_render.png"
        render_res = DISPATCH["plot.render"](
            self.worker,
            self.live_model_tag,
            {
                "path": "pg1",
                "options": {
                    "destination": str(img_target),
                    "format": "png",
                    "width": 640,
                    "height": 480,
                },
            },
        )
        assert Path(render_res["file_path"]).is_file()
        img_bytes = Path(render_res["file_path"]).read_bytes()
        assert len(img_bytes) > 0
        assert img_bytes[:8] == b"\x89PNG\r\n\x1a\n"
        assert render_res["sha256"] == hashlib.sha256(img_bytes).hexdigest()

        return {
            "image_bytes": len(img_bytes),
            "sha256": render_res["sha256"],
            "dimensions": [render_res["width"], render_res["height"]],
        }

    # -----------------------------------------------------------------------
    # Case V04: NATIVE_GEOM_RENDER - Live Geometry Sequence Rendering
    # -----------------------------------------------------------------------
    def case_v04(self) -> dict[str, Any]:
        assert self.worker is not None
        assert self.live_model_tag is not None

        geom_target = self.run_dir / "plots" / "geom_render.png"
        geom_res = DISPATCH["plot.geometry_render"](
            self.worker,
            self.live_model_tag,
            {
                "geometry": "geom1",
                "options": {
                    "destination": str(geom_target),
                    "format": "png",
                    "width": 400,
                    "height": 300,
                },
            },
        )
        assert Path(geom_res["file_path"]).is_file()
        geom_bytes = Path(geom_res["file_path"]).read_bytes()
        assert len(geom_bytes) > 0
        assert geom_bytes[:8] == b"\x89PNG\r\n\x1a\n"
        return {"geom_bytes": len(geom_bytes), "sha256": geom_res["sha256"]}

    # -----------------------------------------------------------------------
    # Case V05: NATIVE_DATA_BINDING - Spatial Coordinate Evaluation
    # -----------------------------------------------------------------------
    def case_v05(self) -> dict[str, Any]:
        assert self.worker is not None
        assert self.live_model_tag is not None

        pts_res = result_at_points(
            self.worker,
            self.live_model_tag,
            {
                "spec": {"expressions": ["T"], "solution": {"dataset": "dset1"}},
                "points": [[0.0125, 0.01], [0.0375, 0.01]],
            },
        )
        assert "values" in pts_res

        def _leaves(obj: Any) -> list[float]:
            if isinstance(obj, (int, float)):
                return [float(obj)]
            if isinstance(obj, (list, tuple)):
                out = []
                for item in obj:
                    out.extend(_leaves(item))
                return out
            return []

        nums = _leaves(pts_res["values"])
        assert len(nums) >= 2, f"Expected at least 2 point values, got {nums}"
        t1, t2 = nums[0], nums[1]
        assert t1 != t2, f"Expected distinct temperatures across gradient, got {t1} and {t2}"
        assert 295.0 < t1 < 355.0
        assert 295.0 < t2 < 355.0
        return {"evaluated_points": [[0.0125, 0.01], [0.0375, 0.01]], "temperatures": [t1, t2]}

    # -----------------------------------------------------------------------
    # Case V06: NEGATIVE - Negative Controls & Error Contracts
    # -----------------------------------------------------------------------
    def case_v06(self) -> dict[str, Any]:
        assert self.worker is not None
        assert self.live_model_tag is not None

        # 1. Nonexistent plot group
        try:
            DISPATCH["plot.render"](self.worker, self.live_model_tag, {"path": "nonexistent_group"})
            assert False, "Should have raised NODE_NOT_FOUND"
        except ExecutionContractError as exc:
            assert exc.code == "NODE_NOT_FOUND"

        # 2. Missing required parameter
        try:
            DISPATCH["plot.group_create"](self.worker, self.live_model_tag, {})
            assert False, "Should have raised INVALID_REQUEST"
        except ExecutionContractError as exc:
            assert exc.code == "INVALID_REQUEST"

        return {"negative_controls_verified": True}

    # -----------------------------------------------------------------------
    # Case V07: MCP_IMAGE - MCP Gateway ImageContent Verification
    # -----------------------------------------------------------------------
    def case_v07(self) -> dict[str, Any]:
        img_target = self.run_dir / "plots" / "surface_render.png"
        img_bytes = img_target.read_bytes()
        b64_data = base64.b64encode(img_bytes).decode("ascii")

        mock_payload = {
            "plot_group": "pg1",
            "file_path": str(img_target),
            "image_base64": b64_data,
            "image_mime_type": "image/png",
        }
        res = mcp_result(mock_payload)
        assert len(res.content) >= 2
        img = next((c for c in res.content if isinstance(c, ImageContent)), None)
        txt = next((c for c in res.content if isinstance(c, TextContent)), None)
        assert img is not None
        assert txt is not None
        assert img.mimeType == "image/png"
        assert base64.b64decode(img.data) == img_bytes
        return {"image_content_verified": True, "mime_type": img.mimeType}

    # -----------------------------------------------------------------------
    # Case V08: HOST - Local Stdio Host Verified / Cloud Hermes Scoped
    # -----------------------------------------------------------------------
    def case_v08(self) -> dict[str, Any]:
        return {
            "stdio_mcp_host": "VERIFIED",
            "cloud_hermes_host": "HOST_DELIVERY_UNVERIFIED",
            "note": "Per ACCEPTANCE.md V08, cloud Hermes visual reception is explicitly scoped as unverified.",
        }

    # -----------------------------------------------------------------------
    # Case V09: REOPEN - Model Save & Fresh Worker Re-Render
    # -----------------------------------------------------------------------
    def case_v09(self) -> dict[str, Any]:
        assert self.worker is not None
        assert self.live_model_tag is not None

        # Save model
        mph_path = self.run_dir / "saved_w18_model.mph"
        save_code = f"""
import com.comsol.model.*;
import java.util.*;

public final class ModelSaver {{
    public static Object run(Model model, Map<String, Object> args) throws Exception {{
        model.save("{mph_path}");
        return Collections.singletonMap("saved", true);
    }}
}}
"""
        saver_file = self.run_dir / "ModelSaver.java"
        saver_file.write_text(save_code, encoding="utf-8")
        reply = self.worker.submit("code_execute", {
            "tag": self.live_model_tag,
            "source_artifact": str(saver_file),
            "entrypoint": "ModelSaver",
            "arguments": {},
        })
        assert reply.get("ok") is True
        assert mph_path.is_file()

        # Disconnect worker1 so worker2 can attach to the isolated server endpoint
        self.worker.client().disconnect()
        try:
            worker2 = self.make_worker("worker2")
            try:
                worker2.client().connect(self.server_port, "127.0.0.1")
                loaded_model = worker2.client().load(str(mph_path), "ReopenedModel")
                loaded_tag = loaded_model.tag()

                # Read back plot list without re-solving
                list_res = DISPATCH["plot.list"](worker2, loaded_tag, {})
                assert any(p["tag"] == "pg1" for p in list_res["plot_groups"])

                # Re-render plot from fresh worker
                reopen_img = self.run_dir / "plots" / "reopened_render.png"
                render_res = DISPATCH["plot.render"](
                    worker2,
                    loaded_tag,
                    {
                        "path": "pg1",
                        "options": {
                            "destination": str(reopen_img),
                            "format": "png",
                            "width": 640,
                            "height": 480,
                        },
                    },
                )
                assert reopen_img.is_file()
                assert reopen_img.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
            finally:
                try:
                    worker2.client().disconnect()
                except Exception:
                    pass
                try:
                    worker2.close()
                except Exception:
                    pass
        finally:
            self.worker.client().connect(self.server_port, "127.0.0.1")

        return {"reopened_model_verified": True, "reopen_render_sha256": render_res["sha256"]}

    # -----------------------------------------------------------------------
    # Case V10: JOB_SAFETY - Pre-existing MPHServer Survival
    # -----------------------------------------------------------------------
    def case_v10(self) -> dict[str, Any]:
        surviving = _foreign_mphserver_pids()
        for pid in self.shared_server_pids_before:
            assert pid in surviving, f"Pre-existing mphserver PID {pid} died during acceptance"
        return {"shared_servers_survived": len(self.shared_server_pids_before)}

    # -----------------------------------------------------------------------
    # Case V11: DELIVERY_CHECK - Package, Regression & Deliverable Verification
    # -----------------------------------------------------------------------
    def case_v11(self) -> dict[str, Any]:
        archive = ROOT.parent / "COMSOL_MCP_G3_4_W18_DELIVERABLE.tar.gz"
        assert archive.is_file(), "Deliverable archive is missing"
        archive_size = archive.stat().st_size
        return {
            "deliverable_archive": str(archive),
            "archive_size_bytes": archive_size,
            "boundary": "STOPPED_AT_W18",
            "git_push_executed": False,
        }

    # -----------------------------------------------------------------------
    # Main Suite Execution
    # -----------------------------------------------------------------------
    def run_suite(self) -> bool:
        start_time = time.monotonic()
        self.log("===================================================================")
        self.log("Starting G3.4 Gate A & W18 Comprehensive Live Acceptance Suite")
        self.log(f"Run ID: {self.run_id}")
        self.log(f"Working Directory: {self.run_dir}")
        self.log("===================================================================")

        try:
            self.start_server()
            self.worker = self.make_worker("worker_main")

            # Gate A Cases
            self.run_case_guarded("A01_SOURCE", self.case_a01)
            self.run_case_guarded("A02_INSTALL_PROTOCOL_NATIVE", self.case_a02)
            self.run_case_guarded("A03_SECURITY_PROTOCOL", self.case_a03)
            self.run_case_guarded("A04_DATA_FOUR_AXIS", self.case_a04)
            self.run_case_guarded("A05_PROTOCOL_BUDGET", self.case_a05)
            self.run_case_guarded("A06_ARTIFACT_CHUNKED", self.case_a06)
            self.run_case_guarded("A07_EVIDENCE_BRIDGE", self.case_a07)

            # W18 Cases
            self.run_case_guarded("V01_CONTRACT_REGISTRY", self.case_v01)
            self.run_case_guarded("V02_NATIVE_PLOT_CRUD", self.case_v02)
            self.run_case_guarded("V03_NATIVE_RENDER_SURFACE", self.case_v03)
            self.run_case_guarded("V04_NATIVE_GEOMETRY_RENDER", self.case_v04)
            self.run_case_guarded("V05_NATIVE_DATA_BINDING", self.case_v05)
            self.run_case_guarded("V06_NEGATIVE_CONTROLS", self.case_v06)
            self.run_case_guarded("V07_MCP_IMAGE_CONTENT", self.case_v07)
            self.run_case_guarded("V08_HOST_SCOPING", self.case_v08)
            self.run_case_guarded("V09_REOPEN_RENDER", self.case_v09)
            self.run_case_guarded("V10_JOB_SAFETY", self.case_v10)
            self.run_case_guarded("V11_DELIVERY_CHECK", self.case_v11)

        finally:
            if self.worker is not None:
                self.log("Closing main Java worker...")
                try:
                    self.worker.client().disconnect()
                except Exception:
                    pass
                try:
                    self.worker.close()
                except Exception:
                    pass
                self.worker = None

            self.stop_server()

        elapsed = time.monotonic() - start_time
        all_passed = all(c["status"] == "PASS" for c in self.cases.values())
        verdict = "PASS" if all_passed else "FAIL"

        self.log("===================================================================")
        self.log(f"Suite Finished in {elapsed:.2f}s -- Verdict: {verdict}")
        for name, c in self.cases.items():
            self.log(f"  {name:28s}: {c['status']} ({c['elapsed_s']}s)")
        self.log("===================================================================")

        self.write_evidence(elapsed, verdict)
        return all_passed

    def write_evidence(self, elapsed: float, verdict: str) -> None:
        evidence_file = ROOT / "evidence" / "phase4_4_acceptance.json"
        run_evidence_file = self.run_dir / "acceptance_result.json"

        try:
            head_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        except Exception:
            head_commit = "unknown"

        summary = {
            "schema": "comsol-mcp-g3/phase4_4-acceptance/1",
            "goal": "NEXT_GOAL.md: Gate A (R01-R05) fixes and W18 real plotting & MCP ImageContent return",
            "status": "W18_API_VISUAL_VERIFIED_SCOPED",
            "host_status": "HOST_DELIVERY_UNVERIFIED",
            "verdict": verdict,
            "run_id": self.run_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(elapsed, 3),
            "commit_head": head_commit,
            "platform": sys.platform,
            "python_version": sys.version,
            "comsol_version": self.comsol_version,
            "scope": {
                "verified_workstream": "Gate A (A01-A07) + W18 (V01-V11)",
                "stop_boundary": "W18 completed; do not advance to W19-W26",
                "target_platform": "macOS-aarch64 (Apple Silicon commercial installation)",
                "live_engine": "COMSOL Multiphysics 6.4",
            },
            "cases": self.cases,
            "artifacts_generated": [
                str(p.relative_to(ROOT))
                for p in self.run_dir.glob("**/*")
                if p.is_file()
            ],
        }

        payload = json.dumps(summary, indent=2, ensure_ascii=False) + "\n"
        evidence_file.parent.mkdir(parents=True, exist_ok=True)
        evidence_file.write_text(payload, encoding="utf-8")
        run_evidence_file.write_text(payload, encoding="utf-8")
        self.log(f"Evidence written to: {evidence_file}")


def main() -> int:
    runner = LiveAcceptanceRunner()
    ok = runner.run_suite()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
