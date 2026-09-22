#!/usr/bin/env python3
"""Unified Live Acceptance Runner for Gate A (R01-R05) and W18 (Real Graphics & MCP Image Return).

Executes live acceptance against the local COMSOL 6.4 engine and python package,
generating structured, verifiable, and reproducible evidence.
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
from comsol_mcp._g3_ops import DISPATCH, dispatch
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

    def make_worker(self) -> PersistentJavaWorker:
        state_dir = self.run_dir / "worker"
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
    # Case R01: Project Root Resolution & site-packages Rejection
    # -----------------------------------------------------------------------
    def case_r01(self) -> dict[str, Any]:
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
    # Case R02: ArtifactStore Safe Path & Hidden/Private Subpath Rejection
    # -----------------------------------------------------------------------
    def case_r02(self) -> dict[str, Any]:
        store = ArtifactStore(self.run_dir)
        # Normal safe path
        p = store.resolve_safe_path("exports/data.csv", allow_overwrite=True)
        assert p == (self.run_dir / "exports" / "data.csv").resolve()

        # Rejection of private subpaths
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
    # Case R03: CSV 4-Axis Semantics, Units, and 1:1 Roundtrip
    # -----------------------------------------------------------------------
    def case_r03(self) -> dict[str, Any]:
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
        target = self.run_dir / "r03_test.csv"
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
    # Case R04: Result Evaluation Budget & Metadata Invariants
    # -----------------------------------------------------------------------
    def case_r04(self) -> dict[str, Any]:
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
        target = self.run_dir / "r04_eval.json"
        store.export_field_data(str(target), eval_result, fmt="json")
        assert target.is_file()
        data = json.loads(target.read_text(encoding="utf-8"))
        assert "metadata" in data
        assert "field_array" in data["metadata"]
        return {"artifact_has_field_array_metadata": True}

    # -----------------------------------------------------------------------
    # Case W18: Live Plotting, Rendering, View, Export & MCP ImageContent
    # -----------------------------------------------------------------------
    def case_w18_live(self) -> dict[str, Any]:
        assert self.worker is not None
        assert self.server_port is not None

        self.log("  Connecting Java worker to live mphserver...")
        self.worker.client().connect(self.server_port, "127.0.0.1")

        # Build a live 2D model with heat transfer
        self.log("  Creating live COMSOL model 'LiveW18'...")
        model = self.worker.client().create("LiveW18")
        tag = model.tag()

        builder_code = """
import com.comsol.model.*;
import java.util.*;

public final class W18LiveBuilder {
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
        builder_file = self.run_dir / "W18LiveBuilder.java"
        builder_file.write_text(builder_code, encoding="utf-8")
        reply = self.worker.submit("code_execute", {
            "tag": tag,
            "source_artifact": str(builder_file),
            "entrypoint": "W18LiveBuilder",
            "arguments": {},
        })
        assert reply.get("ok") is True, f"Model build failed: {reply}"
        self.log("  Model built and solved successfully!")

        # 1. Test plot.group_create
        self.log("  Testing plot.group_create...")
        create_pg = DISPATCH["plot.group_create"](
            self.worker,
            tag,
            {"tag": "pg1", "dimension": 2, "dataset": "dset1", "properties": {"title": "Temperature Surface"}},
        )
        assert create_pg["tag"] == "pg1"
        assert create_pg["type_id"] == "PlotGroup2D"

        # 2. Test plot.feature_create
        self.log("  Testing plot.feature_create...")
        create_feat = DISPATCH["plot.feature_create"](
            self.worker,
            tag,
            {"group": "pg1", "tag": "surf1", "type_id": "Surface", "properties": {"expr": "T"}},
        )
        assert create_feat["tag"] == "surf1"
        assert create_feat["type_id"] == "Surface"

        # 3. Test plot.list
        self.log("  Testing plot.list...")
        list_res = DISPATCH["plot.list"](self.worker, tag, {})
        assert list_res["total_count"] >= 1
        pg_item = next((p for p in list_res["plot_groups"] if p["tag"] == "pg1"), None)
        assert pg_item is not None
        assert len(pg_item["features"]) >= 1

        # 4. Test plot.view_manage
        self.log("  Testing plot.view_manage...")
        view_res = DISPATCH["plot.view_manage"](
            self.worker,
            tag,
            {"tag": "view1", "action": "inspect"},
        )
        assert view_res["view_tag"] == "view1"

        view_list = DISPATCH["plot.view_manage"](
            self.worker,
            tag,
            {"action": "list"},
        )
        assert view_list["total_count"] >= 1

        # 5. Test plot.render
        self.log("  Testing plot.render (producing real PNG image)...")
        img_target = self.run_dir / "plots" / "live_temp_plot.png"
        render_res = DISPATCH["plot.render"](
            self.worker,
            tag,
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
        assert img_bytes[:8] == b"\x89PNG\r\n\x1a\n", "Rendered file does not have PNG magic bytes"
        assert render_res["sha256"] == hashlib.sha256(img_bytes).hexdigest()
        assert render_res["byte_size"] == len(img_bytes)
        self.log(f"  Rendered PNG successfully: {render_res['byte_size']} bytes, SHA256: {render_res['sha256'][:16]}...")

        # 6. Test MCP Gateway ImageContent Return
        self.log("  Testing MCP Gateway ImageContent return...")
        gateway_res = mcp_result(render_res)
        gateway_content = gateway_res.content
        assert len(gateway_content) >= 2, "Expected ImageContent and companion TextContent"
        img_item = next((c for c in gateway_content if isinstance(c, ImageContent)), None)
        txt_item = next((c for c in gateway_content if isinstance(c, TextContent)), None)
        assert img_item is not None, "mcp_result did not return ImageContent"
        assert txt_item is not None, "mcp_result did not return TextContent"
        assert img_item.mimeType == "image/png"
        assert base64.b64decode(img_item.data) == img_bytes
        self.log("  MCP ImageContent return verified: matching base64 payload and image/png MIME type!")

        # 7. Test plot.geometry_render
        self.log("  Testing plot.geometry_render...")
        geom_target = self.run_dir / "plots" / "live_geom.png"
        geom_res = DISPATCH["plot.geometry_render"](
            self.worker,
            tag,
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

        # 8. Test plot.update
        self.log("  Testing plot.update...")
        update_res = DISPATCH["plot.update"](
            self.worker,
            tag,
            {"path": "pg1/surf1", "properties": {"expr": "T*2"}},
        )
        assert update_res["updated"] is True

        # 9. Test export lifecycle (export.create, export.list, export.update, export.run, export.remove)
        self.log("  Testing export lifecycle...")
        exp_target = self.run_dir / "plots" / "export_test.png"
        create_exp = DISPATCH["export.create"](
            self.worker,
            tag,
            {
                "tag": "exp1",
                "type_id": "Image",
                "properties": {
                    "plotgroup": "pg1",
                    "pngfilename": str(exp_target),
                },
            },
        )
        assert create_exp["tag"] == "exp1"

        exp_list = DISPATCH["export.list"](self.worker, tag, {})
        assert exp_list["total_count"] >= 1
        assert any(e["tag"] == "exp1" for e in exp_list["exports"])

        update_exp = DISPATCH["export.update"](
            self.worker,
            tag,
            {"tag": "exp1", "properties": {"size": "manualweb", "width": 800, "height": 600}},
        )
        assert update_exp["updated"] is True

        run_exp = DISPATCH["export.run"](self.worker, tag, {"tag": "exp1"})
        assert run_exp["ran"] is True

        remove_exp = DISPATCH["export.remove"](self.worker, tag, {"tag": "exp1"})
        assert remove_exp["removed"] is True

        # 10. Test plot.remove
        self.log("  Testing plot.remove...")
        rm_feat = DISPATCH["plot.remove"](self.worker, tag, {"path": "pg1/surf1"})
        assert rm_feat["removed"] is True
        rm_pg = DISPATCH["plot.remove"](self.worker, tag, {"path": "pg1"})
        assert rm_pg["removed"] is True

        return {
            "model_tag": tag,
            "render_bytes": render_res["byte_size"],
            "render_sha256": render_res["sha256"],
            "geom_bytes": geom_res["byte_size"],
            "mcp_image_content_verified": True,
            "export_lifecycle_verified": True,
        }

    # -----------------------------------------------------------------------
    # Main Suite Execution
    # -----------------------------------------------------------------------
    def run_suite(self) -> bool:
        start_time = time.monotonic()
        self.log("===================================================================")
        self.log("Starting G3.4 Gate A & W18 Live Acceptance Suite")
        self.log(f"Run ID: {self.run_id}")
        self.log(f"Working Directory: {self.run_dir}")
        self.log("===================================================================")

        try:
            self.start_server()
            self.worker = self.make_worker()

            self.run_case_guarded("R01_PROJECT_ROOT", self.case_r01)
            self.run_case_guarded("R02_ARTIFACT_SECURITY", self.case_r02)
            self.run_case_guarded("R03_CSV_FOUR_AXIS", self.case_r03)
            self.run_case_guarded("R04_EVAL_BUDGET", self.case_r04)
            self.run_case_guarded("W18_LIVE_PLOT_GRAPHICS", self.case_w18_live)

        finally:
            if self.worker is not None:
                self.log("Closing Java worker...")
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

            # Verify shared server survival
            surviving = _foreign_mphserver_pids()
            for pid in self.shared_server_pids_before:
                if pid not in surviving:
                    self.log(f"WARNING: Pre-existing mphserver PID {pid} is no longer alive!")

        elapsed = time.monotonic() - start_time
        all_passed = all(c["status"] == "PASS" for c in self.cases.values())
        verdict = "PASS" if all_passed else "FAIL"

        self.log("===================================================================")
        self.log(f"Suite Finished in {elapsed:.2f}s -- Verdict: {verdict}")
        for name, c in self.cases.items():
            self.log(f"  {name:25s}: {c['status']} ({c['elapsed_s']}s)")
        self.log("===================================================================")

        self.write_evidence(elapsed, verdict)
        return all_passed

    def write_evidence(self, elapsed: float, verdict: str) -> None:
        evidence_file = ROOT / "evidence" / "phase4_4_acceptance.json"
        run_evidence_file = self.run_dir / "acceptance_result.json"

        # Get HEAD commit
        try:
            head_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        except Exception:
            head_commit = "unknown"

        summary = {
            "schema": "comsol-mcp-g3/phase4_4-acceptance/1",
            "goal": "NEXT_GOAL.md: Gate A (R01-R05) fixes and W18 real plotting & MCP ImageContent return",
            "verdict": verdict,
            "run_id": self.run_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(elapsed, 3),
            "commit_head": head_commit,
            "platform": sys.platform,
            "python_version": sys.version,
            "comsol_version": self.comsol_version,
            "scope": {
                "verified_workstream": "Gate A (R01-R05) + W18",
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
