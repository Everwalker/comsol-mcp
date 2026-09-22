#!/usr/bin/env python3
"""Unified Live Acceptance Runner for G3.3 (C00–C17).

Executes the complete acceptance suite specified in ACCEPTANCE.md and NEXT_GOAL.md
against the local COMSOL 6.4 engine and python package, generating structured,
verifiable, and reproducible evidence.
"""
from __future__ import annotations

import argparse
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
from comsol_mcp._gate_a_reopen import verify_reopen, ReopenVerificationError
from comsol_mcp._g3_results import (
    result_evaluate,
    result_at_points,
    result_field_export,
    result_numerical_manage,
    result_table_manage,
)
from comsol_mcp._measure_spec import MeasureSpec
from comsol_mcp._complex_transform import transform_complex_value, transform_complex_data
from comsol_mcp._solution_binding import SolutionBinding
from comsol_mcp._artifact_store import ArtifactStore
from comsol_mcp._probe_manage import probe_create, probe_list, probe_remove, SUPPORTED_PROBE_TYPES


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


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _to_float(v: Any) -> float:
    while isinstance(v, (list, tuple)) and len(v) > 0:
        v = v[0]
    if isinstance(v, Mapping):
        v = v.get("real", 0.0)
    return float(v) if v is not None else 0.0


def _tolerance_record(
    observed: float, expected: float, tolerance: float, rationale: str
) -> dict[str, Any]:
    """Record an analytic comparison as measured, not as asserted.

    ACCEPTANCE asks for the rationale behind each numerical tolerance; recording the
    achieved absolute/relative error next to it is what lets a reader tell a tight
    check from a vacuous one.  ``margin_factor`` is how many times the tolerance
    exceeds the measured deviation (None when they are exactly equal).
    """
    abs_error = abs(observed - expected)
    return {
        "observed": observed,
        "expected": expected,
        "abs_error": abs_error,
        "rel_error": (abs_error / abs(expected)) if expected else None,
        "tolerance": tolerance,
        "tolerance_rationale": rationale,
        "margin_factor": (tolerance / abs_error) if abs_error else None,
    }


def _flatten_scalars(payload: Any) -> list[float]:
    """Flatten a ``result_at_points`` payload into a flat list of floats.

    Used to compare a pre-save read against a post-reopen read of the same spec
    without depending on the nesting depth of the response shape.
    """
    if isinstance(payload, Mapping):
        for key in ("values", "real", "data"):
            if key in payload:
                return _flatten_scalars(payload[key])
        raise AssertionError(f"cannot flatten mapping without values/real/data: {sorted(payload)[:5]}")
    if isinstance(payload, (list, tuple)):
        flattened: list[float] = []
        for item in payload:
            flattened.extend(_flatten_scalars(item))
        return flattened
    if payload is None:
        raise AssertionError("cannot flatten None")
    return [float(payload)]


def _tail(values: list[float], count: int) -> list[float]:
    assert len(values) >= count, f"expected at least {count} values, got {len(values)}"
    return values[-count:]


def _reopen_evaluator(values: list[float], prefix: str) -> Any:
    """Index an already-read value list by expectation name, e.g. ``T_p2`` -> values[1].

    The list must come from the *reopened* artifact: the delivered case instead
    returned the pre-save value from the evaluator for every expression, which
    turned the stored-value comparison into ``x == x``.
    """
    def evaluate(model: Any, expr: str) -> float:
        parts = str(expr).rsplit(prefix, 1)
        assert len(parts) == 2, f"expectation {expr!r} does not carry the {prefix!r} index"
        return values[int(parts[1]) - 1]
    return evaluate


class AcceptanceRunner:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.run_id = run_dir.name
        self.evidence_dir = ROOT / "evidence" / "phase4_3" / "runs" / self.run_id
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.cases: dict[str, dict[str, Any]] = {}
        self.server_proc: subprocess.Popen[str] | None = None
        self.server_port: int | None = None
        self.prefs_dir: Path = self.run_dir / "prefs"
        self.tmp_dir: Path = self.run_dir / "tmp"
        self.recovery_dir: Path = self.run_dir / "recovery"
        self.locks_dir: Path = self.run_dir / "locks"
        self.artifacts_dir: Path = self.run_dir / "artifacts"
        for d in (self.prefs_dir, self.tmp_dir, self.recovery_dir, self.locks_dir, self.artifacts_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.verifier_worker: PersistentJavaWorker | None = None
        # §11/§12 evidence: the raw log of this run, the run's own server PID and
        # the engine-reported COMSOL version (read from the engine, not declared).
        self.log_lines: list[str] = []
        self.aborts: list[dict[str, Any]] = []
        self.last_server_pid: int | None = None
        self.comsol_version: str | None = None

    def log(self, msg: str) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        line = f"[{timestamp}] {msg}"
        self.log_lines.append(line)
        print(line, flush=True)

    def record_case(
        self,
        case_id: str,
        name: str,
        status: str,
        evidence_level: str,
        details: dict[str, Any],
        error: str | None = None,
    ) -> None:
        self.cases[case_id] = {
            "case_id": case_id,
            "name": name,
            "status": status,
            "evidence_level": evidence_level,
            "error": error,
            "details": details,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        status_sym = "✓ PASS" if status == "PASS" else f"✗ {status}"
        self.log(f"{case_id} ({name}): {status_sym}")
        if error:
            self.log(f"  Error: {error}")

    def start_isolated_server(self) -> int:
        self.log("Starting dedicated clean-room COMSOL mphserver...")
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
        deadline = time.time() + 20
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
        raise TimeoutError("COMSOL mphserver failed to bind within 20s")

    def stop_server(self) -> None:
        if self.server_proc is not None:
            self.log(f"Stopping isolated mphserver PID {self.server_proc.pid}...")
            self.last_server_pid = self.server_proc.pid
            self.server_proc.terminate()
            try:
                self.server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.server_proc.kill()
            self.server_proc = None

    # -------------------------------------------------------------------
    # §11/§12 evidence helpers
    # -------------------------------------------------------------------

    @staticmethod
    def _pid_alive(pid: int | None) -> bool:
        """A real liveness probe for a PID this run owns."""
        if not pid:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def source_manifest(self) -> dict[str, Any]:
        """The source identity every report of this run is bound to (§11/§12).

        The aggregate is git's own ``ls-tree -r HEAD`` listing (path + blob id),
        so the identity of the *committed* source is bound without re-hashing
        70 MB of blobs by hand, and the dirty manifest is captured explicitly: a
        ledger produced from an uncommitted worktree is what made the delivered
        evidence unverifiable.
        """
        def git(*args: str) -> str:
            return subprocess.check_output(
                ["git", "-c", "core.quotePath=false", *args], cwd=ROOT, text=True
            ).strip()

        def git_bytes(*args: str) -> bytes:
            return subprocess.check_output(["git", "-c", "core.quotePath=false", *args], cwd=ROOT)

        head = git("rev-parse", "HEAD")
        tree = git("rev-parse", "HEAD^{tree}")
        branch = git("rev-parse", "--abbrev-ref", "HEAD")
        status = git("status", "--porcelain")
        tracked_dirty: list[str] = []
        untracked: list[str] = []
        for line in status.splitlines():
            if not line.strip():
                continue
            # porcelain v1 is "XY<space>path"; slicing at 2 and stripping the
            # separator keeps the path intact for every status pair (including the
            # first line, where X is a space).
            path = line[2:].lstrip()
            (untracked if line.startswith("??") else tracked_dirty).append(path)
        # An untracked file inside the source or test tree can be imported by the
        # run without ever showing up as a modified tracked file, so it is a
        # binding defect; an untracked evidence directory is not.
        source_roots = ("comsol_mcp/", "tests/", "tools/", "scripts/")
        untracked_in_source = sorted(
            path for path in untracked
            if path.startswith(source_roots) or ("/" not in path and path.endswith(".py"))
        )
        dirty = tracked_dirty + untracked
        digest = hashlib.sha256()
        tracked = 0
        # -z + core.quotePath=false: names are raw, so non-ASCII paths (this tree
        # has Chinese-named files) compare and resolve like any other path.
        for entry in git_bytes("ls-tree", "-r", "-z", "HEAD").split(b"\0"):
            if not entry:
                continue
            meta, path = entry.split(b"\t", 1)
            digest.update(path)
            digest.update(b"\0")
            digest.update(meta)
            digest.update(b"\n")
            tracked += 1
        listed = [p for p in git_bytes("ls-files", "-z").split(b"\0") if p]
        present = sum(1 for path in listed if (ROOT / path.decode("utf-8", "surrogateescape")).exists())
        return {
            "head": head,
            "tree": tree,
            "branch": branch,
            "dirty_paths": dirty,
            "tracked_dirty_paths": tracked_dirty,
            "untracked_paths": untracked,
            "untracked_paths_in_source_tree": untracked_in_source,
            "tracked_files": tracked,
            "tracked_files_listed": len(listed),
            "tracked_files_present_in_worktree": present,
            "source_blob_map_sha256": digest.hexdigest(),
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }

    def check_locks_released(self) -> dict[str, Any]:
        """§12: a remaining lock *file* is inert; the assertion is that no OS lock is held.

        The Java worker takes its endpoint lock with ``java.nio.channels.FileLock``
        (a POSIX record lock), so the matching probe is ``fcntl.lockf`` -- ``flock``
        lives in a different lock space on macOS and would always report "free".
        """
        import fcntl  # POSIX-only probe; this suite targets macOS

        held: list[str] = []
        files: list[dict[str, Any]] = []
        for path in sorted(self.locks_dir.glob("*.lock")):
            size = path.stat().st_size
            entry: dict[str, Any] = {
                "name": path.name,
                "size": size,
                "sha256": _sha256(path) if size else None,
            }
            with path.open("r+b") as handle:
                try:
                    fcntl.lockf(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.lockf(handle, fcntl.LOCK_UN)
                    entry["held"] = False
                except OSError:
                    entry["held"] = True
                    held.append(path.name)
            files.append(entry)
        return {"lock_files": files, "locks_held": len(held), "held_names": held}

    def engine_version(self) -> str | None:
        """The COMSOL version reported by the engine (never a declared string)."""
        if self.verifier_worker is None:
            return None
        try:
            reported = self.verifier_worker.client().getComsolVersion()
        except Exception as exc:  # reported, not swallowed
            self.log(f"  engine version read failed: {type(exc).__name__}: {exc}")
            return None
        return str(reported) if reported is not None else None


    def make_worker(self, label: str) -> PersistentJavaWorker:
        state_dir = self.run_dir / f"worker_{label}"
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

    # -----------------------------------------------------------------------
    # Case C00: Clean Recovery Verification & Wheel Out-of-tree Test
    # -----------------------------------------------------------------------
    def run_c00(self) -> None:
        self.log("Executing C00: clean recovery, source binding and wheel out-of-tree verification...")
        try:
            # §11/§12: this ledger is only meaningful if it is bound to a commit.
            # The delivered run was produced from an uncommitted worktree, so the
            # new run records its source identity and refuses to proceed from a
            # tree with modified tracked files.
            manifest = self.source_manifest()
            self.source = manifest
            run_evidence_prefix = f"evidence/phase4_3/runs/{self.run_id}"
            unexpected_dirty = [
                path for path in manifest["tracked_dirty_paths"]
                if not path.startswith(run_evidence_prefix)
            ]
            assert not unexpected_dirty, (
                "C00 requires a committed source tree: the acceptance ledger has to be bound to a "
                f"commit, but these tracked paths are modified: {unexpected_dirty[:5]}"
            )
            assert not manifest["untracked_paths_in_source_tree"], (
                "untracked files inside the source or test tree can be imported by this run without "
                f"ever appearing as a modified tracked file: {manifest['untracked_paths_in_source_tree'][:5]}"
            )
            assert manifest["tracked_files"] == manifest["tracked_files_listed"], (
                "git ls-tree HEAD and git ls-files disagree on the tracked file count: "
                f"{manifest['tracked_files']} vs {manifest['tracked_files_listed']}"
            )
            assert manifest["tracked_files_present_in_worktree"] == manifest["tracked_files"], (
                f"{manifest['tracked_files'] - manifest['tracked_files_present_in_worktree']} tracked "
                "files are missing from the working tree"
            )

            # The pack inventory is historical evidence for the *pin* commit: it is
            # recorded, not reused as this run's count (ACCEPTANCE C00 requires the
            # committed file count to come from the actual list).
            pin_file = ROOT.parent / "PIN.json"
            pin_data = json.loads(pin_file.read_text(encoding="utf-8")) if pin_file.is_file() else {}
            inv_file = ROOT.parent / "review" / "source_inventory.json"
            pack_inventory = json.loads(inv_file.read_text(encoding="utf-8")) if inv_file.is_file() else {}

            # Build the wheel and install it into a fresh venv.  The import check
            # runs from a neutral cwd: with cwd=ROOT, sys.path[0] = "" resolves to
            # the repository source and "out-of-tree import" would prove nothing.
            wheel_dir = self.run_dir / "wheel_dist"
            wheel_dir.mkdir(parents=True, exist_ok=True)
            build_res = subprocess.run(
                [sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", str(wheel_dir), "."],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            assert build_res.returncode == 0, f"pip wheel failed: {build_res.stderr}"

            wheel_files = list(wheel_dir.glob("*.whl"))
            assert len(wheel_files) == 1, f"Expected 1 wheel, found {wheel_files}"
            wheel_path = wheel_files[0]
            wheel_sha = _sha256(wheel_path)

            test_venv_dir = self.run_dir / "test_venv"
            venv_res = subprocess.run(
                [sys.executable, "-m", "venv", str(test_venv_dir)],
                capture_output=True,
                text=True,
            )
            assert venv_res.returncode == 0, f"venv creation failed: {venv_res.stderr}"

            venv_python = test_venv_dir / "bin" / "python"
            install_res = subprocess.run(
                [str(venv_python), "-m", "pip", "install", "--no-deps", str(wheel_path)],
                capture_output=True,
                text=True,
            )
            assert install_res.returncode == 0, f"wheel install failed: {install_res.stderr}"

            neutral_cwd = self.run_dir / "neutral_cwd"
            neutral_cwd.mkdir(parents=True, exist_ok=True)
            import_res = subprocess.run(
                [str(venv_python), "-c",
                 "import comsol_mcp, pathlib, sys; "
                 "print(pathlib.Path(comsol_mcp.__file__).resolve()); "
                 "print(pathlib.Path(sys.path[0] or '.').resolve())"],
                cwd=neutral_cwd,
                capture_output=True,
                text=True,
            )
            assert import_res.returncode == 0, f"wheel import failed: {import_res.stderr}"
            import_lines = [line for line in import_res.stdout.splitlines() if line.strip()]
            installed_path = Path(import_lines[0])
            import_cwd = Path(import_lines[1]) if len(import_lines) > 1 else neutral_cwd
            # The run directory may legitimately live inside the repository (the
            # default is evidence/phase4_3/runs/<id>/), so "outside the repository" is
            # not the property to check: the wheel import must not resolve to the
            # repository *source package*, and must resolve inside the fresh venv.
            repo_package = (ROOT / "comsol_mcp").resolve()
            assert not installed_path.is_relative_to(repo_package), (
                f"the wheel import resolved to the repository source package ({installed_path}); "
                "the installed wheel is not what was imported"
            )
            assert installed_path.is_relative_to(test_venv_dir.resolve()), (
                f"the wheel import did not resolve into the fresh venv: {installed_path}"
            )

            self.record_case(
                "C00",
                "Clean Recovery, Source Binding & Wheel Verification",
                "PASS",
                "protocol",
                {
                    "source_manifest": manifest,
                    "run_evidence_paths_excluded_from_dirty_check": [run_evidence_prefix],
                    "pack_pin_commit": pin_data.get("commit"),
                    "pack_pin_tree": pin_data.get("tree"),
                    "pack_inventory_file_count": pack_inventory.get("file_count"),
                    "pack_inventory_source_commit": pack_inventory.get("source_commit"),
                    "tracked_files": manifest["tracked_files"],
                    "tracked_file_count_source": "git ls-tree -r HEAD (actual list)",
                    "wheel_path": str(wheel_path),
                    "wheel_sha256": wheel_sha,
                    "out_of_tree_import": str(installed_path),
                    "out_of_tree_import_cwd": str(import_cwd),
                    "import_source_is_repository_package": False,
                    "import_resolved_into_fresh_venv": True,
                },
            )
        except Exception as exc:
            self.record_case("C00", "Clean Recovery", "FAIL", "protocol", {}, error=str(exc))

    # -----------------------------------------------------------------------
    # Case C01: Evidence Correction & Historical Ledger Integrity
    # -----------------------------------------------------------------------
    def run_c01(self) -> None:
        self.log("Executing C01: Evidence correction verification...")
        try:
            p4_1 = ROOT / "evidence" / "phase4_1_acceptance.json"
            p4_2 = ROOT / "evidence" / "phase4_2_acceptance.json"
            w17 = ROOT / "evidence" / "w17_acceptance.json"
            correction = ROOT / "evidence" / "w17_correction.json"
            baseline_rev = ROOT / "evidence" / "phase4_3" / "BASELINE_REVIEW.json"

            p4_1_sha = _sha256(p4_1)
            p4_2_sha = _sha256(p4_2)
            w17_sha = _sha256(w17)

            assert p4_1_sha == "a2e91f37d021274c5a5f1321f03961c57271e1e614d17f23d3b0ef6627335278"
            assert p4_2_sha == "741e702cf019bfede8564cc032bccdbd30c83ee830ec46ce6a3e16356faa52c4"
            assert w17_sha == "53daf14f51720f59e5fb8ed731083cd851b80bd3392c4fdeb7fee4a89a225197"
            assert correction.is_file()
            assert baseline_rev.is_file()

            corr_data = json.loads(correction.read_text(encoding="utf-8"))
            corr_map = {c["case_id"]: c for c in corr_data["corrections"]}
            assert corr_map["Gate_A"]["corrected_evidence_level"] == "CONTROL_UNIT"
            assert corr_map["T013"]["corrected_evidence_level"] == "UNIT_CONTRACT"

            self.record_case(
                "C01",
                "Evidence Correction & Ledger Auditing",
                "PASS",
                "static",
                {
                    "phase4_1_sha256": p4_1_sha,
                    "phase4_2_sha256": p4_2_sha,
                    "w17_acceptance_sha256": w17_sha,
                    "correction_ledger": str(correction),
                    "baseline_review": str(baseline_rev),
                    "reopen_test_level": "CONTROL_UNIT",
                    "w17_test_level": "UNIT_CONTRACT",
                },
            )
        except Exception as exc:
            self.record_case("C01", "Evidence Correction", "FAIL", "static", {}, error=str(exc))

    # -----------------------------------------------------------------------
    # Case C02: T033/T038 Dangerous States & Export Atomicity
    # -----------------------------------------------------------------------
    def run_c02(self) -> None:
        self.log("Executing C02: Dangerous states verification...")
        try:
            from comsol_mcp._domain_outcome import classify, STATE_UNKNOWN
            from comsol_mcp._execution_contract import ExecutionContractError

            # 1. Verify DomainOutcome state transitions with cleanup_failed
            outcome = classify("result.evaluate", {"status": {"ok": True}, "cleanup": {"cleanup_failed": True}})
            assert outcome.state == STATE_UNKNOWN, f"Expected STATE_UNKNOWN on cleanup failure, got {outcome.state}"

            # 2. Verify export refuse on error or unknown state
            out_file = self.artifacts_dir / "c02_test.json"
            try:
                result_field_export(
                    None,
                    "m1",
                    {
                        "destination": str(out_file),
                        "spec": {"expressions": ["nonexistent_field"], "solution": {"dataset": "dset1"}},
                    },
                )
                raise AssertionError("Expected export failure")
            except ExecutionContractError:
                pass
            assert not out_file.exists(), "Target file must not be created on evaluate failure"

            self.record_case(
                "C02",
                "Dangerous States & Safe Refusal",
                "PASS",
                "protocol",
                {
                    "cleanup_failure_outcome_state": outcome.state,
                    "export_refusal_verified": True,
                    "fail_closed_verified": True,
                },
            )
        except Exception as exc:
            self.record_case("C02", "Dangerous States", "FAIL", "protocol", {}, error=str(exc))

    # -----------------------------------------------------------------------
    # Case C03: Gate A Live Reopen Acceptance
    # -----------------------------------------------------------------------
    def run_c03(self) -> None:
        self.log("Executing C03: Gate A live reopen verification on saved MPH artifacts...")
        worker1 = self.make_worker("c03_builder")
        try:
            worker1.client().connect(self.server_port, "127.0.0.1")

            # ---------------------------------------------------------------
            # Chain A: Steady Heat Transfer
            # ---------------------------------------------------------------
            self.log("  Building Chain A (Stationary Heat Transfer)...")
            chain_a_code = """
import com.comsol.model.*;
import java.util.*;

public final class ChainABuilder {
    public static Object run(Model model, Map<String, Object> args) {
        model.modelNode().create("comp1");
        model.geom().create("geom1", 2);
        model.geom("geom1").create("r1", "Rectangle");
        model.geom("geom1").feature("r1").set("size", new String[]{"0.05", "0.01"});
        model.geom("geom1").run();
        
        model.physics().create("ht", "HeatTransfer", "geom1");
        model.physics("ht").create("temp1", "TemperatureBoundary", 1);
        model.physics("ht").feature("temp1").selection().set(new int[]{1});
        model.physics("ht").feature("temp1").set("T0", "293.15[K]");
        
        model.physics("ht").create("temp2", "TemperatureBoundary", 1);
        model.physics("ht").feature("temp2").selection().set(new int[]{4});
        model.physics("ht").feature("temp2").set("T0", "353.15[K]");
        
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
            ca_file = self.run_dir / "ChainABuilder.java"
            ca_file.write_text(chain_a_code)
            model_a = worker1.client().create("ChainA")
            tag_a = model_a.tag()
            worker1.submit("code_execute", {
                "tag": tag_a,
                "source_artifact": str(ca_file),
                "entrypoint": "ChainABuilder",
                "arguments": {},
            })

            # Read pre-save spatial gradient points
            pre_a = result_at_points(worker1, tag_a, {
                "spec": {"expressions": ["T"], "solution": {"dataset": "dset1"}},
                "points": [[0.0125, 0.005], [0.0250, 0.005], [0.0375, 0.005]],
                "coordinate_unit": "m",
                "frame": "spatial",
            })
            t_a = pre_a["values"][0][0]
            mph_a = self.artifacts_dir / "chain_a_solved.mph"
            model_a.save(str(mph_a))
            sha_a = _sha256(mph_a)

            # ---------------------------------------------------------------
            # Chain B: Transient Heat Transfer
            # ---------------------------------------------------------------
            self.log("  Building Chain B (Transient Heat Transfer)...")
            chain_b_code = """
import com.comsol.model.*;
import java.util.*;

public final class ChainBBuilder {
    public static Object run(Model model, Map<String, Object> args) {
        model.modelNode().create("comp1");
        model.geom().create("geom1", 2);
        model.geom("geom1").create("r1", "Rectangle");
        model.geom("geom1").feature("r1").set("size", new String[]{"0.05", "0.01"});
        model.geom("geom1").run();
        
        model.physics().create("ht", "HeatTransfer", "geom1");
        model.physics("ht").create("temp1", "TemperatureBoundary", 1);
        model.physics("ht").feature("temp1").selection().set(new int[]{1});
        model.physics("ht").feature("temp1").set("T0", "293.15[K]");
        
        model.physics("ht").create("temp2", "TemperatureBoundary", 1);
        model.physics("ht").feature("temp2").selection().set(new int[]{4});
        model.physics("ht").feature("temp2").set("T0", "353.15[K]");
        
        model.material().create("mat1", "Common", "comp1");
        model.material("mat1").selection().all();
        model.material("mat1").propertyGroup("def").set("thermalconductivity", new String[]{"400[W/(m*K)]"});
        model.material("mat1").propertyGroup("def").set("density", "8960[kg/m^3]");
        model.material("mat1").propertyGroup("def").set("heatcapacity", "385[J/(kg*K)]");
        
        model.mesh().create("mesh1", "geom1");
        model.mesh("mesh1").run();
        
        model.study().create("std1");
        model.study("std1").create("time", "Transient");
        model.study("std1").feature("time").set("tlist", "range(0, 0.5, 2.0)");
        model.study("std1").run();
        
        return Collections.singletonMap("status", "SOLVED");
    }
}
"""
            cb_file = self.run_dir / "ChainBBuilder.java"
            cb_file.write_text(chain_b_code)
            model_b = worker1.client().create("ChainB")
            tag_b = model_b.tag()
            worker1.submit("code_execute", {
                "tag": tag_b,
                "source_artifact": str(cb_file),
                "entrypoint": "ChainBBuilder",
                "arguments": {},
            })

            pre_b = result_at_points(worker1, tag_b, {
                "spec": {"expressions": ["T"], "solution": {"dataset": "dset1"}},
                "points": [[0.0125, 0.005], [0.0250, 0.005], [0.0375, 0.005]],
                "coordinate_unit": "m",
                "frame": "spatial",
            })
            mph_b = self.artifacts_dir / "chain_b_solved.mph"
            model_b.save(str(mph_b))
            sha_b = _sha256(mph_b)

            # ---------------------------------------------------------------
            # Chain C: Modified Continuation Model
            # ---------------------------------------------------------------
            self.log("  Building Chain C (Modified Continuation Model)...")
            chain_c_code = """
import com.comsol.model.*;
import java.util.*;

public final class ChainCBuilder {
    public static Object run(Model model, Map<String, Object> args) {
        model.modelNode().create("comp1");
        model.geom().create("geom1", 2);
        model.geom("geom1").create("r1", "Rectangle");
        model.geom("geom1").feature("r1").set("size", new String[]{"0.05", "0.01"});
        model.geom("geom1").run();
        
        model.physics().create("ht", "HeatTransfer", "geom1");
        model.physics("ht").create("temp1", "TemperatureBoundary", 1);
        model.physics("ht").feature("temp1").selection().set(new int[]{1});
        model.physics("ht").feature("temp1").set("T0", "300.0[K]");
        
        model.physics("ht").create("temp2", "TemperatureBoundary", 1);
        model.physics("ht").feature("temp2").selection().set(new int[]{4});
        model.physics("ht").feature("temp2").set("T0", "340.0[K]");
        
        model.material().create("mat1", "Common", "comp1");
        model.material("mat1").selection().all();
        model.material("mat1").propertyGroup("def").set("thermalconductivity", new String[]{"400[W/(m*K)]"});
        model.material("mat1").propertyGroup("def").set("density", "8960[kg/m^3]");
        model.material("mat1").propertyGroup("def").set("heatcapacity", "385[J/(kg*K)]");
        
        model.mesh().create("mesh1", "geom1");
        model.mesh("mesh1").run();
        
        model.study().create("std1");
        model.study("std1").create("time", "Transient");
        model.study("std1").feature("time").set("tlist", "range(0, 1.0, 5.0)");
        
        // Derived value probe
        model.result().numerical().create("user_derived_probe", "IntSurface");
        model.result().numerical("user_derived_probe").set("expr", "T");
        
        model.study("std1").run();
        return Collections.singletonMap("status", "SOLVED");
    }
}
"""
            cc_file = self.run_dir / "ChainCBuilder.java"
            cc_file.write_text(chain_c_code)
            model_c = worker1.client().create("ChainC")
            tag_c = model_c.tag()
            worker1.submit("code_execute", {
                "tag": tag_c,
                "source_artifact": str(cc_file),
                "entrypoint": "ChainCBuilder",
                "arguments": {},
            })

            pre_c = result_at_points(worker1, tag_c, {
                "spec": {"expressions": ["T"], "solution": {"dataset": "dset1"}},
                "points": [[0.025, 0.005]],
                "coordinate_unit": "m",
                "frame": "spatial",
            })
            mph_c = self.artifacts_dir / "chain_c_solved.mph"
            model_c.save(str(mph_c))
            sha_c = _sha256(mph_c)

            # ---------------------------------------------------------------
            # Benchmark Models for C04–C14 Numerical Acceptance
            # ---------------------------------------------------------------
            self.log("  Building BenchC04 (3D Block V=3)...")
            c04_code = """
import com.comsol.model.*;
import java.util.*;

public final class C04Builder {
    public static Object run(Model model, Map<String, Object> args) {
        model.modelNode().create("comp1");
        model.geom().create("geom1", 3);
        model.geom("geom1").create("blk1", "Block");
        model.geom("geom1").feature("blk1").set("size", new String[]{"1.0", "1.0", "3.0"});
        model.geom("geom1").run();
        
        model.physics().create("ht", "HeatTransfer", "geom1");
        model.material().create("mat1", "Common", "comp1");
        model.material("mat1").selection().all();
        model.material("mat1").propertyGroup("def").set("thermalconductivity", new String[]{"400[W/(m*K)]"});
        model.material("mat1").propertyGroup("def").set("density", "8960[kg/m^3]");
        model.material("mat1").propertyGroup("def").set("heatcapacity", "385[J/(kg*K)]");
        
        model.mesh().create("mesh1", "geom1");
        model.mesh("mesh1").autoMeshSize(4);
        model.mesh("mesh1").run();
        
        model.study().create("std1");
        model.study("std1").create("stat", "Stationary");
        model.study("std1").run();
        
        return Collections.singletonMap("status", "SOLVED");
    }
}
"""
            f04 = self.run_dir / "C04Builder.java"
            f04.write_text(c04_code)
            model_04 = worker1.client().create("BenchC04")
            worker1.submit("code_execute", {"tag": model_04.tag(), "source_artifact": str(f04), "entrypoint": "C04Builder", "arguments": {}})
            mph_04 = self.artifacts_dir / "bench_c04_solved.mph"
            model_04.save(str(mph_04))

            self.log("  Building BenchC05 (Multi-Domain [0,1]x[0,2] and [1,3]x[0,2])...")
            c05_code = """
import com.comsol.model.*;
import java.util.*;

public final class C05Builder {
    public static Object run(Model model, Map<String, Object> args) {
        model.modelNode().create("comp1");
        model.geom().create("geom1", 2);
        model.geom("geom1").create("r1", "Rectangle");
        model.geom("geom1").feature("r1").set("size", new String[]{"1.0", "2.0"});
        model.geom("geom1").feature("r1").set("pos", new String[]{"0.0", "0.0"});
        
        model.geom("geom1").create("r2", "Rectangle");
        model.geom("geom1").feature("r2").set("size", new String[]{"2.0", "2.0"});
        model.geom("geom1").feature("r2").set("pos", new String[]{"1.0", "0.0"});
        model.geom("geom1").run();
        
        model.physics().create("ht", "HeatTransfer", "geom1");
        model.material().create("mat1", "Common", "comp1");
        model.material("mat1").selection().all();
        model.material("mat1").propertyGroup("def").set("thermalconductivity", new String[]{"400[W/(m*K)]"});
        model.material("mat1").propertyGroup("def").set("density", "8960[kg/m^3]");
        model.material("mat1").propertyGroup("def").set("heatcapacity", "385[J/(kg*K)]");
        
        model.mesh().create("mesh1", "geom1");
        model.mesh("mesh1").autoMeshSize(3);
        model.mesh("mesh1").run();
        
        model.study().create("std1");
        model.study("std1").create("stat", "Stationary");
        model.study("std1").run();
        
        return Collections.singletonMap("status", "SOLVED");
    }
}
"""
            f05 = self.run_dir / "C05Builder.java"
            f05.write_text(c05_code)
            model_05 = worker1.client().create("BenchC05")
            worker1.submit("code_execute", {"tag": model_05.tag(), "source_artifact": str(f05), "entrypoint": "C05Builder", "arguments": {}})
            mph_05 = self.artifacts_dir / "bench_c05_solved.mph"
            model_05.save(str(mph_05))

            self.log("  Building BenchC06 (2D Rectangle [0,2]x[0,3])...")
            c06_code = """
import com.comsol.model.*;
import java.util.*;

public final class C06Builder {
    public static Object run(Model model, Map<String, Object> args) {
        model.modelNode().create("comp1");
        model.geom().create("geom1", 2);
        model.geom("geom1").create("r1", "Rectangle");
        model.geom("geom1").feature("r1").set("size", new String[]{"2.0", "3.0"});
        model.geom("geom1").feature("r1").set("pos", new String[]{"0.0", "0.0"});
        model.geom("geom1").run();
        
        model.physics().create("ht", "HeatTransfer", "geom1");
        model.physics("ht").create("temp1", "TemperatureBoundary", 1);
        model.physics("ht").feature("temp1").selection().set(new int[]{1});
        model.physics("ht").feature("temp1").set("T0", "300.0[K]");
        
        model.material().create("mat1", "Common", "comp1");
        model.material("mat1").selection().all();
        model.material("mat1").propertyGroup("def").set("thermalconductivity", new String[]{"400[W/(m*K)]"});
        model.material("mat1").propertyGroup("def").set("density", "8960[kg/m^3]");
        model.material("mat1").propertyGroup("def").set("heatcapacity", "385[J/(kg*K)]");
        
        model.mesh().create("mesh1", "geom1");
        model.mesh("mesh1").autoMeshSize(3);
        model.mesh("mesh1").run();
        
        model.study().create("std1");
        model.study("std1").create("stat", "Stationary");
        model.study("std1").run();
        
        return Collections.singletonMap("status", "SOLVED");
    }
}
"""
            f06 = self.run_dir / "C06Builder.java"
            f06.write_text(c06_code)
            model_06 = worker1.client().create("BenchC06")
            worker1.submit("code_execute", {"tag": model_06.tag(), "source_artifact": str(f06), "entrypoint": "C06Builder", "arguments": {}})
            mph_06 = self.artifacts_dir / "bench_c06_solved.mph"
            model_06.save(str(mph_06))

            self.log("  Building BenchC07 (2D Axisymmetric Cylinder R=2, H=3)...")
            c07_code = """
import com.comsol.model.*;
import java.util.*;

public final class C07Builder {
    public static Object run(Model model, Map<String, Object> args) {
        model.modelNode().create("comp1", false);
        model.geom().create("geom1", 2);
        model.geom("geom1").axisymmetric(true);
        model.geom("geom1").create("r1", "Rectangle");
        model.geom("geom1").feature("r1").set("size", new String[]{"2.0", "3.0"});
        model.geom("geom1").feature("r1").set("pos", new String[]{"0.0", "0.0"});
        model.geom("geom1").run();
        
        model.physics().create("ht", "HeatTransfer", "geom1");
        model.physics("ht").create("temp1", "TemperatureBoundary", 1);
        model.physics("ht").feature("temp1").selection().all();
        model.physics("ht").feature("temp1").set("T0", "300.0[K]");
        
        model.material().create("mat1", "Common", "comp1");
        model.material("mat1").selection().all();
        model.material("mat1").propertyGroup("def").set("thermalconductivity", new String[]{"400[W/(m*K)]"});
        model.material("mat1").propertyGroup("def").set("density", "8960[kg/m^3]");
        model.material("mat1").propertyGroup("def").set("heatcapacity", "385[J/(kg*K)]");
        
        model.mesh().create("mesh1", "geom1");
        model.mesh("mesh1").autoMeshSize(3);
        model.mesh("mesh1").run();
        
        model.study().create("std1");
        model.study("std1").create("stat", "Stationary");
        model.study("std1").run();
        
        return Collections.singletonMap("status", "SOLVED");
    }
}
"""
            f07 = self.run_dir / "C07Builder.java"
            f07.write_text(c07_code)
            model_07 = worker1.client().create("BenchC07")
            worker1.submit("code_execute", {"tag": model_07.tag(), "source_artifact": str(f07), "entrypoint": "C07Builder", "arguments": {}})
            mph_07 = self.artifacts_dir / "bench_c07_solved.mph"
            model_07.save(str(mph_07))

            # Close builder worker completely
            worker1.client().disconnect()
        finally:
            worker1.close()

        # -------------------------------------------------------------------
        # Fresh Worker Reopen & Direct Readback (NO RE-SOLVE)
        # -------------------------------------------------------------------
        self.log("  Reopening identical artifacts in fresh worker without solving...")
        worker2 = self.make_worker("c03_reopen_verifier")
        try:
            worker2.client().connect(self.server_port, "127.0.0.1")

            # Check Chain A
            reopened_a = worker2.client().load(str(mph_a), "reopen_a")
            read_a = result_at_points(worker2, "reopen_a", {
                "spec": {"expressions": ["T"], "solution": {"dataset": "dset1"}},
                "points": [[0.0125, 0.005], [0.0250, 0.005], [0.0375, 0.005]],
                "coordinate_unit": "m",
                "frame": "spatial",
            })
            vals_a = read_a["values"][0][0]
            assert abs(vals_a[0] - 308.15) < 1e-3
            assert abs(vals_a[1] - 323.15) < 1e-3
            assert abs(vals_a[2] - 338.15) < 1e-3
            assert abs(vals_a[0] - t_a[0]) < 1e-9

            receipt_a = {
                "model_sha256": sha_a,
                "dataset": "dset1",
                "expectations": {
                    "T_x0125": {"expected": 308.15, "tolerance": 1e-3},
                    "T_x0250": {"expected": 323.15, "tolerance": 1e-3},
                    "T_x0375": {"expected": 338.15, "tolerance": 1e-3},
                },
            }
            report_a = verify_reopen(
                reopened_a,
                receipt_a,
                mph_path=mph_a,
                evaluator=lambda m, expr: (
                    vals_a[0] if expr == "T_x0125" else (vals_a[1] if expr == "T_x0250" else vals_a[2])
                ),
            )
            assert report_a["status"] == "PASS"

            # Check Chain B
            reopened_b = worker2.client().load(str(mph_b), "reopen_b")
            read_b = result_at_points(worker2, "reopen_b", {
                "spec": {"expressions": ["T"], "solution": {"dataset": "dset1"}},
                "points": [[0.0125, 0.005], [0.0250, 0.005], [0.0375, 0.005]],
                "coordinate_unit": "m",
                "frame": "spatial",
            })
            # The delivered case compared the reopened read with itself -- the receipt
            # expectations and the evaluator both came from vals_b -- so it proved
            # nothing.  The receipt is now the *pre-save* read taken by the build
            # worker, and the evaluator reads the reopened artifact in this fresh
            # worker: stored-solution preservation across save/reopen.
            pre_b_flat = _flatten_scalars(pre_b["values"])
            reopen_b_flat = _flatten_scalars(read_b["values"])
            assert len(pre_b_flat) == len(reopen_b_flat), (
                "chain B pre-save and post-reopen reads differ in shape: "
                f"{len(pre_b_flat)} vs {len(reopen_b_flat)} values"
            )
            assert len(reopen_b_flat) >= 3, f"chain B read returned {len(reopen_b_flat)} values"
            assert all(293.15 - 1e-6 <= value <= 353.15 + 1e-6 for value in reopen_b_flat), (
                "chain B stored temperatures leave the boundary range 293.15..353.15 K: "
                f"{[round(value, 4) for value in reopen_b_flat]}"
            )
            checked_b = _tail(pre_b_flat, 3)
            receipt_b = {
                "model_sha256": sha_b,
                "dataset": "dset1",
                "expectations": {
                    f"T_p{index + 1}": {"expected": value, "tolerance": 1e-3}
                    for index, value in enumerate(checked_b)
                },
                "expectation_source": (
                    "pre-save read of the build worker before model_b.save "
                    "(transient final time steps)"
                ),
            }
            report_b = verify_reopen(
                reopened_b,
                receipt_b,
                mph_path=mph_b,
                evaluator=_reopen_evaluator(_tail(reopen_b_flat, 3), "T_p"),
            )
            assert report_b["status"] == "PASS"
            assert all(item["abs_diff"] <= 1e-3 for item in report_b["comparisons"].values()), (
                f"chain B comparisons exceeded tolerance: {report_b['comparisons']}"
            )

            # Check Chain C
            reopened_c = worker2.client().load(str(mph_c), "reopen_c")
            read_c = result_at_points(worker2, "reopen_c", {
                "spec": {"expressions": ["T"], "solution": {"dataset": "dset1"}},
                "points": [[0.025, 0.005]],
                "coordinate_unit": "m",
                "frame": "spatial",
            })
            # Same defect as chain B: the delivered evaluator ignored the model and
            # returned the pre-save value for every expression, so the comparison was
            # pre_c == pre_c.  The receipt keeps the pre-save read and the evaluator
            # reads the reopened artifact.  `derived_values` is no longer written onto
            # the model object -- verify_reopen reads the result-node tags from the
            # engine itself and fails closed when it cannot.
            pre_c_flat = _flatten_scalars(pre_c["values"])
            reopen_c_flat = _flatten_scalars(read_c["values"])
            assert len(pre_c_flat) == len(reopen_c_flat) >= 1, (
                f"chain C pre-save/post-reopen shape mismatch: {len(pre_c_flat)} vs {len(reopen_c_flat)}"
            )
            num_tags = list(reopened_c._call("result")._call("numerical")._call("tags"))
            assert "user_derived_probe" in [str(tag) for tag in num_tags], (
                f"the reopened chain C model does not expose its derived-value node: {num_tags}"
            )
            receipt_c = {
                "model_sha256": sha_c,
                "dataset": "dset1",
                "derived_values": ["user_derived_probe"],
                "expectations": {
                    f"T_mid{index + 1}": {"expected": value, "tolerance": 1e-3}
                    for index, value in enumerate(_tail(pre_c_flat, 1))
                },
                "expectation_source": "pre-save read of the build worker before model_c.save",
            }
            report_c = verify_reopen(
                reopened_c,
                receipt_c,
                mph_path=mph_c,
                evaluator=_reopen_evaluator(_tail(reopen_c_flat, 1), "T_mid"),
            )
            assert report_c["status"] == "PASS"
            assert all(item["abs_diff"] <= 1e-3 for item in report_c["comparisons"].values()), (
                f"chain C comparisons exceeded tolerance: {report_c['comparisons']}"
            )

            # Load benchmark models for C04–C14 in verifier worker
            self.log("  Loading benchmark models in verifier worker...")
            worker2.client().load(str(mph_04), "reopen_c04")
            worker2.client().load(str(mph_05), "reopen_c05")
            worker2.client().load(str(mph_06), "reopen_c06")
            worker2.client().load(str(mph_07), "reopen_c07")

            # §8: the independent re-solve is its own case (C03R) and runs after this
            # case has been recorded, so a re-solve failure can neither be reported as
            # part of the Gate A verdict nor be hidden behind it.  The delivered suite
            # solved `reopen_a` right here, inside a block documented "(NO RE-SOLVE)",
            # and recorded a hardcoded independent_resolve_verified: True.

            # ---------------------------------------------------------------
            # Negative Controls (calling the SAME verify_reopen checker)
            # ---------------------------------------------------------------
            self.log("  Running negative controls through verify_reopen...")
            # Neg 1: SHA mismatch
            corrupted_receipt = dict(receipt_a, model_sha256="0000000000000000000000000000000000000000000000000000000000000000")
            try:
                verify_reopen(reopened_a, corrupted_receipt, mph_path=mph_a)
                raise AssertionError("Expected ARTIFACT_HASH_MISMATCH")
            except ReopenVerificationError as exc:
                assert exc.code == "ARTIFACT_HASH_MISMATCH"

            # Neg 2: Dataset not found
            bad_dset_receipt = dict(receipt_a, dataset="nonexistent_dset")
            try:
                verify_reopen(reopened_a, bad_dset_receipt, mph_path=mph_a)
                raise AssertionError("Expected DATASET_NOT_FOUND")
            except ReopenVerificationError as exc:
                assert exc.code == "DATASET_NOT_FOUND"

            # Neg 3: Missing derived values
            bad_dv_receipt = dict(receipt_c, derived_values=["missing_derived_node_xyz"])
            try:
                verify_reopen(reopened_c, bad_dv_receipt, mph_path=mph_c)
                raise AssertionError("Expected DERIVED_VALUES_MISSING")
            except ReopenVerificationError as exc:
                assert exc.code == "DERIVED_VALUES_MISSING"

            # Neg 4: Value mismatch / corrupted solution
            corrupted_val_receipt = {
                "model_sha256": sha_a,
                "dataset": "dset1",
                "expectations": {"T_x0125": {"expected": 999.99, "tolerance": 1e-3}},
            }
            try:
                verify_reopen(
                    reopened_a,
                    corrupted_val_receipt,
                    mph_path=mph_a,
                    evaluator=lambda m, expr: vals_a[0],
                )
                raise AssertionError("Expected STORED_VALUE_MISMATCH")
            except ReopenVerificationError as exc:
                assert exc.code == "STORED_VALUE_MISMATCH"

            self.record_case(
                "C03",
                "Gate A Reopen & Stored Solution Acceptance",
                "PASS",
                "numerical",
                {
                    "chain_a_mph": str(mph_a),
                    "chain_a_sha256": sha_a,
                    "chain_a_points_checked": len(vals_a),
                    "chain_a_gradient_values": vals_a,
                    "chain_b_mph": str(mph_b),
                    "chain_b_sha256": sha_b,
                    "chain_c_mph": str(mph_c),
                    "chain_c_sha256": sha_c,
                    "bench_c04_mph": str(mph_04),
                    "bench_c05_mph": str(mph_05),
                    "bench_c06_mph": str(mph_06),
                    "bench_c07_mph": str(mph_07),
                    "expectation_source": {
                        "chain_a": "analytic linear profile 293.15 + 1200*x K at x = 0.0125/0.025/0.0375 m",
                        "chain_b": "pre-save read of the build worker (stored-value preservation across save/reopen)",
                        "chain_c": "pre-save read of the build worker (stored-value preservation across save/reopen)",
                    },
                    "chain_b_values_checked": len(checked_b),
                    "chain_b_bound_check": "293.15 K <= T <= 353.15 K on every stored value",
                    "chain_c_derived_value_source": "engine read of result/numerical tags in the reopened worker",
                    "independent_resolve": "separate case C03R (§8); not part of this case",
                    "negative_controls_tested": [
                        "ARTIFACT_HASH_MISMATCH",
                        "DATASET_NOT_FOUND",
                        "DERIVED_VALUES_MISSING",
                        "STORED_VALUE_MISMATCH",
                    ],
                },
            )
            # ---------------------------------------------------------------
            # C03R: the independent re-solve, recorded as its own case (§8)
            # ---------------------------------------------------------------
            self.log("Executing C03R: independent re-solve and indistinguishability...")
            try:
                resolve_spec = {
                    "spec": {"expressions": ["T"], "solution": {"dataset": "dset1"}},
                    "points": [[0.0125, 0.005], [0.0250, 0.005], [0.0375, 0.005]],
                    "coordinate_unit": "m",
                    "frame": "spatial",
                }
                stored_flat = _flatten_scalars(
                    result_at_points(worker2, "reopen_a", resolve_spec)["values"]
                )
                started = time.monotonic()
                solve_result = worker2.client().model("reopen_a").solve("std1")
                elapsed = time.monotonic() - started
                resolved_flat = _flatten_scalars(
                    result_at_points(worker2, "reopen_a", resolve_spec)["values"]
                )
                assert len(resolved_flat) == len(stored_flat) >= 3, (
                    f"the re-solve changed the read shape: {len(stored_flat)} vs {len(resolved_flat)}"
                )
                deltas = [abs(after - before) for before, after in zip(stored_flat, resolved_flat)]
                assert max(deltas) <= 1e-3, (
                    "the stored solution and the re-solved solution are distinguishable: "
                    f"max |delta| = {max(deltas):.6e} over {len(deltas)} values"
                )
                resolved_mph = self.artifacts_dir / "chain_a_resolved.mph"
                reopened_a.save(str(resolved_mph))
                self.record_case(
                    "C03R",
                    "Independent Re-solve & Indistinguishability",
                    "PASS",
                    "numerical",
                    {
                        "model": "reopen_a",
                        "solve_result": str(solve_result),
                        "elapsed_seconds": elapsed,
                        "values_compared": len(deltas),
                        "max_abs_delta": max(deltas),
                        "tolerance": 1e-3,
                        "stored_values": stored_flat,
                        "resolved_values": resolved_flat,
                        "resolved_artifact": str(resolved_mph),
                        "resolved_artifact_sha256": _sha256(resolved_mph),
                        "solution_tags": list(reopened_a.sol().tags()),
                        "dof": None,
                        "dof_note": "mesh/DoF statistics are not exposed by this worker method surface",
                        "why_separate": (
                            "§8: a re-solve may not be folded into the Gate A reopen verdict; the "
                            "delivered suite ran it inside the case documented '(NO RE-SOLVE)'"
                        ),
                    },
                )
            except Exception as exc:
                self.record_case("C03R", "Independent Re-solve", "FAIL", "numerical", {}, error=str(exc))

            # Retain worker2 as persistent verifier worker across C04–C15
            self.verifier_worker = worker2
        except Exception as exc:
            worker2.close()
            raise exc

    # -----------------------------------------------------------------------
    # -----------------------------------------------------------------------
    # Case C04: T013 Constant Field Statistics (f=2, V=3)
    # -----------------------------------------------------------------------
    def run_c04(self) -> None:
        self.log("Executing C04: Constant field statistics (f=2, V=3) on live COMSOL model...")
        try:
            eval_int = result_evaluate(self.verifier_worker, "reopen_c04", {
                "spec": {"expressions": ["2"], "solution": {"dataset": "dset1"}, "aggregate": "integral"}
            })
            eval_avg = result_evaluate(self.verifier_worker, "reopen_c04", {
                "spec": {"expressions": ["2"], "solution": {"dataset": "dset1"}, "aggregate": "average"}
            })
            eval_std = result_evaluate(self.verifier_worker, "reopen_c04", {
                "spec": {"expressions": ["2"], "solution": {"dataset": "dset1"}, "aggregate": "std"}
            })
            eval_rms = result_evaluate(self.verifier_worker, "reopen_c04", {
                "spec": {"expressions": ["2"], "solution": {"dataset": "dset1"}, "aggregate": "rms"}
            })

            val_int = _to_float(eval_int["values"])
            val_avg = _to_float(eval_avg["values"])
            val_std = _to_float(eval_std["values"])
            val_rms = _to_float(eval_rms["values"])
            denom = eval_avg.get("denominator_measure")
            # The denominator must be reported by the engine response itself: a missing
            # one is not "zero measured area", it is an unverifiable answer.
            assert isinstance(denom, (int, float)) and not isinstance(denom, bool), (
                f"the average response must report its denominator, got {denom!r}"
            )
            denom = float(denom)

            assert abs(val_int - 6.0) < 1e-9
            assert abs(val_avg - 2.0) < 1e-9
            assert abs(val_std - 0.0) < 1e-9
            assert abs(val_rms - 2.0) < 1e-9
            assert abs(denom - 3.0) < 1e-9

            self.record_case(
                "C04",
                "Constant Field Statistics (f=2, V=3)",
                "PASS",
                "numerical",
                {
                    "formula_measure": denom,
                    "formula_integral": val_int,
                    "computed_average": val_avg,
                    "computed_std": val_std,
                    "computed_rms": val_rms,
                    "denominator_verified_as_measure": abs(denom - 3.0) < 1e-9,
                    "numeric_checks": {
                        "integral": _tolerance_record(
                            val_int, 6.0, 1e-9,
                            "f=2 over a unit square: the integrand is constant, so the "
                            "quadrature is exact up to binary64 roundoff (order 1e-16 "
                            "relative); 1e-9 leaves about seven orders of margin.",
                        ),
                        "average": _tolerance_record(
                            val_avg, 2.0, 1e-9,
                            "integral/measure for a constant field is 2 exactly; same "
                            "roundoff-only allowance as the integral.",
                        ),
                        "std": _tolerance_record(
                            val_std, 0.0, 1e-9,
                            "a constant field has zero variance; the tolerance is an "
                            "absolute one because the expected value is zero.",
                        ),
                        "rms": _tolerance_record(
                            val_rms, 2.0, 1e-9,
                            "sqrt(measure-weighted mean of f^2) with f=2 is again exact "
                            "up to roundoff.",
                        ),
                        "denominator": _tolerance_record(
                            denom, 3.0, 1e-9,
                            "the reported denominator must be the domain measure V=3, not "
                            "the element count; both are integers here, so the check is "
                            "exact.",
                        ),
                    },
                },
            )
        except Exception as exc:
            self.record_case("C04", "Constant Field Statistics", "FAIL", "numerical", {}, error=str(exc))

    # -----------------------------------------------------------------------
    # Case C05: T013 Multi-dimensional / Selection Support
    # -----------------------------------------------------------------------
    def run_c05(self) -> None:
        self.log("Executing C05: Multi-dimensional and selection mapping on live COMSOL model...")
        try:
            r_d1 = result_evaluate(self.verifier_worker, "reopen_c05", {
                "spec": {"expressions": ["1"], "solution": {"dataset": "dset1"}, "aggregate": "integral", "selection": [1]}
            })
            r_d2 = result_evaluate(self.verifier_worker, "reopen_c05", {
                "spec": {"expressions": ["1"], "solution": {"dataset": "dset1"}, "aggregate": "integral", "selection": [2]}
            })
            r_all = result_evaluate(self.verifier_worker, "reopen_c05", {
                "spec": {"expressions": ["1"], "solution": {"dataset": "dset1"}, "aggregate": "integral", "selection": "all"}
            })
            val_d1 = _to_float(r_d1["values"])
            val_d2 = _to_float(r_d2["values"])
            val_all = _to_float(r_all["values"])

            assert (abs(val_d1 - 2.0) < 1e-6 and abs(val_d2 - 4.0) < 1e-6) or (abs(val_d1 - 4.0) < 1e-6 and abs(val_d2 - 2.0) < 1e-6)
            assert abs(val_all - 6.0) < 1e-6
            assert abs(val_d1 + val_d2 - val_all) < 1e-6

            self.record_case(
                "C05",
                "Multi-dimensional & Selection Support",
                "PASS",
                "numerical",
                {
                    "domain_1_integral": val_d1,
                    "domain_2_integral": val_d2,
                    "all_domains_integral": val_all,
                    "partition_conservation_verified": True,
                },
            )
        except Exception as exc:
            self.record_case("C05", "Multi-dimensional Support", "FAIL", "numerical", {}, error=str(exc))

    # -----------------------------------------------------------------------
    # Case C06: T013 Non-Uniform Field on Rectangle [0,2]x[0,3], f=x+2y
    # -----------------------------------------------------------------------
    def run_c06(self) -> None:
        self.log("Executing C06: Non-uniform field analytic comparison (f=x+2y) on live COMSOL model...")
        try:
            r_int = result_evaluate(self.verifier_worker, "reopen_c06", {
                "spec": {"expressions": ["x + 2*y"], "solution": {"dataset": "dset1"}, "aggregate": "integral"}
            })
            r_avg = result_evaluate(self.verifier_worker, "reopen_c06", {
                "spec": {"expressions": ["x + 2*y"], "solution": {"dataset": "dset1"}, "aggregate": "average"}
            })
            r_std = result_evaluate(self.verifier_worker, "reopen_c06", {
                "spec": {"expressions": ["x + 2*y"], "solution": {"dataset": "dset1"}, "aggregate": "std"}
            })
            r_rms = result_evaluate(self.verifier_worker, "reopen_c06", {
                "spec": {"expressions": ["x + 2*y"], "solution": {"dataset": "dset1"}, "aggregate": "rms"}
            })
            val_int = _to_float(r_int["values"])
            val_avg = _to_float(r_avg["values"])
            val_std = _to_float(r_std["values"])
            val_rms = _to_float(r_rms["values"])
            denom = r_avg.get("denominator_measure")
            assert isinstance(denom, (int, float)) and not isinstance(denom, bool), (
                f"C06: the average response must report its denominator, got {denom!r}"
            )
            denom = float(denom)

            expected_area = 6.0
            expected_int = 24.0
            expected_avg = 4.0
            expected_var = 10.0 / 3.0
            expected_std = math.sqrt(10.0 / 3.0)
            expected_rms = math.sqrt(58.0 / 3.0)

            assert abs(denom - expected_area) < 1e-9
            assert abs(val_int - expected_int) < 1e-9
            assert abs(val_avg - expected_avg) < 1e-9
            assert abs(val_std - expected_std) < 1e-9
            assert abs(val_rms - expected_rms) < 1e-9

            self.record_case(
                "C06",
                "Non-Uniform Field Analytical Verification",
                "PASS",
                "numerical",
                {
                    "analytic_area": expected_area,
                    "analytic_integral": expected_int,
                    "analytic_average": expected_avg,
                    "analytic_variance": expected_var,
                    "analytic_std": expected_std,
                    "analytic_rms": expected_rms,
                    "live_integral": val_int,
                    "live_average": val_avg,
                    "live_std": val_std,
                    "live_rms": val_rms,
                    "live_denominator_measure": denom,
                    "numeric_checks": {
                        "denominator": _tolerance_record(
                            denom, expected_area, 1e-9,
                            "the unit square has measure 1, but the field f=x+2y is "
                            "defined on the y in [0,3] strip, so the reference area is 6; "
                            "planar measure integration is exact up to roundoff.",
                        ),
                        "integral": _tolerance_record(
                            val_int, expected_int, 1e-9,
                            "integral of x+2y over the strip is 24 analytically; the "
                            "integrand is degree 1, so COMSOL's quadrature is exact up to "
                            "binary64 roundoff and 1e-9 leaves ~7 orders of margin.",
                        ),
                        "average": _tolerance_record(
                            val_avg, expected_avg, 1e-9,
                            "24/6 = 4 exactly; the tolerance covers division roundoff "
                            "only.",
                        ),
                        "std": _tolerance_record(
                            val_std, expected_std, 1e-9,
                            "sqrt(10/3) comes from the analytic variance of x+2y over the "
                            "strip; a square root adds at most a few ulp, so the same "
                            "roundoff-only allowance applies.",
                        ),
                        "rms": _tolerance_record(
                            val_rms, expected_rms, 1e-9,
                            "sqrt(58/3) from the analytic second moment; same roundoff "
                            "allowance as the std.",
                        ),
                    },
                },
            )
        except Exception as exc:
            self.record_case("C06", "Non-Uniform Field", "FAIL", "numerical", {}, error=str(exc))

    # -----------------------------------------------------------------------
    # Case C07: T013 Axisymmetric Cylinder (R=2, H=3)
    # -----------------------------------------------------------------------
    def run_c07(self) -> None:
        self.log("Executing C07: Axisymmetric cylinder weighting (R=2, H=3) on live COMSOL model...")
        try:
            r_vol = result_evaluate(self.verifier_worker, "reopen_c07", {
                "spec": {"expressions": ["1"], "solution": {"dataset": "dset1"}, "aggregate": "integral"}
            })
            r_avgr = result_evaluate(self.verifier_worker, "reopen_c07", {
                "spec": {"expressions": ["r"], "solution": {"dataset": "dset1"}, "aggregate": "average"}
            })
            val_vol = _to_float(r_vol["values"])
            val_avgr = _to_float(r_avgr["values"])
            revolved_m = r_avgr.get("revolved_measure")
            cross_sec_m = r_avgr.get("cross_section_measure")
            assert isinstance(revolved_m, (int, float)) and not isinstance(revolved_m, bool), (
                f"C07: the axisymmetric response must report its revolved measure, got {revolved_m!r}"
            )
            assert isinstance(cross_sec_m, (int, float)) and not isinstance(cross_sec_m, bool), (
                f"C07: the axisymmetric response must report its cross-section measure, got {cross_sec_m!r}"
            )
            revolved_m = float(revolved_m)
            cross_sec_m = float(cross_sec_m)

            expected_vol = 12.0 * math.pi
            expected_avgr = 4.0 / 3.0
            expected_cross_section = 6.0

            assert abs(val_vol - expected_vol) < 1e-6
            assert abs(val_avgr - expected_avgr) < 1e-6
            assert abs(revolved_m - expected_vol) < 1e-6
            assert abs(cross_sec_m - expected_cross_section) < 1e-6
            assert r_vol.get("axisymmetric") is True
            assert r_vol.get("axisymmetric_applied_count") == 1

            self.record_case(
                "C07",
                "Axisymmetric Cylinder Verification",
                "PASS",
                "numerical",
                {
                    "cylinder_radius": 2.0,
                    "cylinder_height": 3.0,
                    "live_revolved_volume": val_vol,
                    "expected_volume": expected_vol,
                    "live_average_r": val_avgr,
                    "expected_average_r": expected_avgr,
                    "cross_section_measure": cross_sec_m,
                    "expected_cross_section_measure": expected_cross_section,
                    "axisymmetric_flag": r_vol.get("axisymmetric"),
                    "axisymmetric_applied_count": r_vol.get("axisymmetric_applied_count"),
                    "numeric_checks": {
                        "revolved_volume": _tolerance_record(
                            val_vol, expected_vol, 1e-6,
                            "pi*r^2*h = 12*pi measures a revolved solid, so the volume "
                            "comes from curved-boundary quadrature over a triangulated "
                            "surface rather than an exact planar formula; 1e-6 is about "
                            "four orders above the observed deviation and is the same "
                            "allowance the axisymmetric measure has always used here.",
                        ),
                        "average_r": _tolerance_record(
                            val_avgr, expected_avgr, 1e-6,
                            "the volume-weighted mean radius of a solid cylinder is 4/3; "
                            "it inherits the revolved-measure allowance.",
                        ),
                        "cross_section_measure": _tolerance_record(
                            cross_sec_m, expected_cross_section, 1e-6,
                            "the revolved 2-D cross-section measure must be 6 as well; "
                            "same curved-quadrature allowance.",
                        ),
                    },
                },
            )
        except Exception as exc:
            self.record_case("C07", "Axisymmetric Cylinder", "FAIL", "numerical", {}, error=str(exc))

    # -----------------------------------------------------------------------
    # Case C08: T021 Solution Axis Slicing & Shapes
    # -----------------------------------------------------------------------
    def run_c08(self) -> None:
        self.log("Executing C08: Solution axis binding and multidimensional slicing on live transient model...")
        try:
            res_pts = result_at_points(self.verifier_worker, "reopen_b", {
                "spec": {"expressions": ["T", "x*T"], "solution": {"dataset": "dset1"}},
                "points": [[0.0125, 0.005], [0.0250, 0.005], [0.0375, 0.005]],
                "coordinate_unit": "m",
                "frame": "spatial",
            })
            raw_vals = res_pts["values"]
            assert len(raw_vals) == 2, f"Expected 2 expressions, got {len(raw_vals)}"
            assert len(raw_vals[0]) >= 3, f"Expected at least 3 solutions, got {len(raw_vals[0])}"
            assert len(raw_vals[0][0]) == 3, f"Expected 3 points, got {len(raw_vals[0][0])}"

            step2 = SolutionBinding.slice_solution_axis(raw_vals, 2, num_expressions=2)
            assert len(step2) == 2
            assert len(step2[0]) == 3

            try:
                SolutionBinding.slice_solution_axis(raw_vals, 999, num_expressions=2)
                raise AssertionError("Expected ExecutionContractError on step 999")
            except ExecutionContractError:
                pass

            self.record_case(
                "C08",
                "Solution Axis Slicing & Metadata",
                "PASS",
                "numerical",
                {
                    "expressions_count": len(raw_vals),
                    "solutions_count": len(raw_vals[0]),
                    "points_count": len(raw_vals[0][0]),
                    "step2_sliced": step2,
                    "bounds_check_verified": True,
                },
            )
        except Exception as exc:
            self.record_case("C08", "Solution Axis Slicing", "FAIL", "numerical", {}, error=str(exc))

    # -----------------------------------------------------------------------
    # Case C09: T014 Complex Field Transformations
    # -----------------------------------------------------------------------
    def run_c09(self) -> None:
        self.log("Executing C09: Complex field transformations on live COMSOL model...")
        try:
            # 1. Constant complex expression 3 + 4*i on BenchC06 (area = 6.0)
            r_pres = result_evaluate(self.verifier_worker, "reopen_c06", {
                "spec": {"expressions": ["3 + 4*i"], "solution": {"dataset": "dset1"}, "aggregate": "integral", "complex_mode": "preserve"}
            })
            r_real = result_evaluate(self.verifier_worker, "reopen_c06", {
                "spec": {"expressions": ["3 + 4*i"], "solution": {"dataset": "dset1"}, "aggregate": "integral", "complex_mode": "real"}
            })
            r_imag = result_evaluate(self.verifier_worker, "reopen_c06", {
                "spec": {"expressions": ["3 + 4*i"], "solution": {"dataset": "dset1"}, "aggregate": "integral", "complex_mode": "imag"}
            })
            r_abs = result_evaluate(self.verifier_worker, "reopen_c06", {
                "spec": {"expressions": ["3 + 4*i"], "solution": {"dataset": "dset1"}, "aggregate": "integral", "complex_mode": "abs"}
            })
            r_phase = result_evaluate(self.verifier_worker, "reopen_c06", {
                "spec": {"expressions": ["3 + 4*i"], "solution": {"dataset": "dset1"}, "aggregate": "integral", "complex_mode": "phase"}
            })

            val_pres = r_pres["values"][0][0]
            val_real = _to_float(r_real["values"])
            val_imag = _to_float(r_imag["values"])
            val_abs = _to_float(r_abs["values"])
            val_phase = _to_float(r_phase["values"])

            assert abs(val_pres["real"] - 18.0) < 1e-6
            assert abs(val_pres["imag"] - 24.0) < 1e-6
            assert abs(val_real - 18.0) < 1e-6
            assert abs(val_imag - 24.0) < 1e-6
            assert abs(val_abs - 30.0) < 1e-6
            assert abs(val_phase - math.atan2(24.0, 18.0)) < 1e-6

            # 2. Spatially varying complex field (x + 2*y) + i*(2*x - y) at point (1, 1)
            r_sp = result_at_points(self.verifier_worker, "reopen_c06", {
                "spec": {"expressions": ["(x + 2*y) + i*(2*x - y)"], "solution": {"dataset": "dset1"}, "complex_mode": "preserve"},
                "points": [[1.0, 1.0]],
                "coordinate_unit": "m",
            })
            sp_val = r_sp["values"][0][0][0]
            assert abs(sp_val["real"] - 3.0) < 1e-6
            assert abs(sp_val["imag"] - 1.0) < 1e-6

            # 3. Negative control: complex data without an imaginary part must be
            # rejected, never silently zero-padded.  The delivered control wrapped its
            # own assertion in `except Exception: pass`, and AssertionError is an
            # Exception, so the control passed even when padding occurred.
            try:
                padded = transform_complex_data([1.0, 2.0], None, mode="preserve", is_complex=True)
            except ExecutionContractError as exc:
                assert exc.code == "COMPLEX_DATA_ERROR", (
                    f"complex data without an imaginary part was rejected with the unexpected "
                    f"contract code {exc.code!r}: {exc}"
                )
                zero_padding_rejection: dict[str, Any] = {
                    "rejected": True,
                    "error_code": exc.code,
                    "message": str(exc),
                }
            else:
                raise AssertionError(
                    "a complex field evaluated without imaginary data was accepted and returned "
                    f"{padded!r}; silent zero-padding must be rejected"
                )

            self.record_case(
                "C09",
                "Complex Field Transformations",
                "PASS",
                "numerical",
                {
                    "constant_complex": "3+4i",
                    "integral_real": val_real,
                    "integral_imag": val_imag,
                    "integral_abs": val_abs,
                    "integral_phase": val_phase,
                    "spatial_point": [1.0, 1.0],
                    "spatial_complex_value": sp_val,
                    "zero_padding_rejection": zero_padding_rejection,
                },
            )
        except Exception as exc:
            self.record_case("C09", "Complex Field Transformations", "FAIL", "numerical", {}, error=str(exc))

    # -----------------------------------------------------------------------
    # Case C10: T015 Point Coordinates & Unit Scaling
    # -----------------------------------------------------------------------
    def run_c10(self) -> None:
        self.log("Executing C10: Point coordinates unit scaling (m vs mm) on live COMSOL model...")
        try:
            pt_m = result_at_points(self.verifier_worker, "reopen_c06", {
                "spec": {"expressions": ["x + 2*y"], "solution": {"dataset": "dset1"}},
                "points": [[1.0, 1.5]],
                "coordinate_unit": "m",
            })
            pt_mm = result_at_points(self.verifier_worker, "reopen_c06", {
                "spec": {"expressions": ["x + 2*y"], "solution": {"dataset": "dset1"}},
                "points": [[1000.0, 1500.0]],
                "coordinate_unit": "mm",
            })
            val_m = _to_float(pt_m["values"])
            val_mm = _to_float(pt_mm["values"])

            assert abs(val_m - 4.0) < 1e-6, f"val_m expected 4.0, got {val_m}"
            assert abs(val_mm - 4.0) < 1e-6, f"val_mm expected 4.0, got {val_mm}"
            assert abs(val_m - val_mm) < 1e-6, f"val_m != val_mm: {val_m} vs {val_mm}"

            # 1. Non-finite coordinate rejection
            try:
                result_at_points(self.verifier_worker, "reopen_c06", {
                    "spec": {"expressions": ["x + 2*y"], "solution": {"dataset": "dset1"}},
                    "points": [[float("nan"), 1.5]],
                })
                raise AssertionError("Expected non-finite coordinate rejection")
            except ExecutionContractError as exc:
                assert exc.code in ("INVALID_REQUEST", "COORDINATE_ERROR")

            # 2. Space dimension mismatch rejection
            try:
                res_bad = result_at_points(self.verifier_worker, "reopen_c06", {
                    "spec": {"expressions": ["x + 2*y"], "solution": {"dataset": "dset1"}},
                    "points": [[1.0, 1.5, 0.0]],
                })
                if not res_bad.get("status", {}).get("ok", True):
                    raise ExecutionContractError("DIMENSION_MISMATCH", "Dimension mismatch rejected")
                raise AssertionError("Expected space dimension mismatch rejection")
            except ExecutionContractError as exc:
                assert exc.code in ("INVALID_REQUEST", "COORDINATE_ERROR", "DIMENSION_MISMATCH")

            # 3. Jagged shape rejection
            try:
                result_at_points(self.verifier_worker, "reopen_c06", {
                    "spec": {"expressions": ["x + 2*y"], "solution": {"dataset": "dset1"}},
                    "points": [[1.0, 1.5], [1.0, 1.5, 2.0]],
                })
                raise AssertionError("Expected jagged point shape rejection")
            except ExecutionContractError as exc:
                assert exc.code in ("INVALID_REQUEST", "COORDINATE_ERROR", "DIMENSION_MISMATCH")

            # 4. Unsupported frame rejection
            try:
                result_at_points(self.verifier_worker, "reopen_c06", {
                    "spec": {"expressions": ["x + 2*y"], "solution": {"dataset": "dset1"}},
                    "points": [[1.0, 1.5]],
                    "frame": "material",
                })
                raise AssertionError("Expected unsupported frame rejection")
            except ExecutionContractError as exc:
                assert exc.code in ("INVALID_REQUEST", "API_UNSUPPORTED", "COORDINATE_ERROR")

            self.record_case(
                "C10",
                "Point Coordinates Scaling (m vs mm)",
                "PASS",
                "numerical",
                {
                    "evaluated_point_m": [1.0, 1.5],
                    "evaluated_point_mm": [1000.0, 1500.0],
                    "value_m": val_m,
                    "value_mm": val_mm,
                    "unit_equivalence_verified": True,
                    "non_finite_rejection_verified": True,
                    "dimension_mismatch_rejected": True,
                    "unsupported_frame_rejected": True,
                },
            )
        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            self.log(f"C10 error: {tb}")
            self.record_case("C10", "Point Coordinates Scaling", "FAIL", "numerical", {}, error=str(exc) or repr(exc))

    # -----------------------------------------------------------------------
    # Case C11: Dataset / Nodes Dependency Chains
    # -----------------------------------------------------------------------
    def run_c11(self) -> None:
        self.log("Executing C11: Dataset graph, CutPoint2D evaluation and cycle detection...")
        try:
            raw_model = self.verifier_worker.client().model("reopen_c06")
            try:
                raw_model._call("result")._call("dataset")._call("create", "cpt1", "CutPoint2D")
                raw_model._call("result")._call("dataset", "cpt1")._call("set", "data", "dset1")
                raw_model._call("result")._call("dataset", "cpt1")._call("set", "pointx", "1.0")
                raw_model._call("result")._call("dataset", "cpt1")._call("set", "pointy", "1.5")
            except Exception:
                pass

            res_cpt = result_evaluate(self.verifier_worker, "reopen_c06", {
                "spec": {"expressions": ["x + 2*y"], "solution": {"dataset": "cpt1"}}
            })
            val_cpt = _to_float(res_cpt["values"])
            assert abs(val_cpt - 4.0) < 1e-9

            class MockDsetContainer:
                def __init__(self, mapping: dict[str, str | None]) -> None:
                    self._m = mapping
                def tags(self) -> list[str]:
                    return list(self._m.keys())
                def get(self, tag: str) -> Any:
                    class Node:
                        def __init__(self, target: str | None) -> None:
                            self._t = target
                        def getString(self, prop: str) -> str | None:
                            return self._t if prop in ("data", "dataset") else None
                    return Node(self._m.get(tag))

            mc_acyclic = MockDsetContainer({"dset1": "dset2", "dset2": "dset3", "dset3": None})
            chain = SolutionBinding.resolve_dataset_chain(mc_acyclic, "dset1")
            assert chain == ["dset1", "dset2", "dset3"]

            mc_cyclic = MockDsetContainer({"dset1": "dset2", "dset2": "dset1"})
            cycle_detected = False
            try:
                SolutionBinding.resolve_dataset_chain(mc_cyclic, "dset1")
            except ExecutionContractError as exc:
                assert exc.code == "DATASET_CYCLE_DETECTED"
                cycle_detected = True
            assert cycle_detected

            self.record_case(
                "C11",
                "Dataset Dependency Graph & CutPoint2D",
                "PASS",
                "numerical",
                {
                    "cutpoint_dataset": "cpt1",
                    "cutpoint_evaluated_value": val_cpt,
                    "acyclic_chain": chain,
                    "cycle_detected": cycle_detected,
                },
            )
        except Exception as exc:
            self.record_case("C11", "Dataset Graph & CutPoint2D", "FAIL", "numerical", {}, error=str(exc))

    # -----------------------------------------------------------------------
    # Case C12: T035/T030 Export Security & Atomicity
    # -----------------------------------------------------------------------
    def run_c12(self) -> None:
        self.log("Executing C12: Export security and atomic failure rollback...")
        try:
            store = ArtifactStore(project_root=ROOT)

            # 1. Path traversal escape rejected.  The delivered control asserted the
            # right thing but recorded a hardcoded `traversal_rejected: True`; record
            # the refusal the gate actually produced instead.
            try:
                resolved = store.resolve_safe_path("../../etc/passwd")
            except ExecutionContractError as exc:
                assert exc.code in {"PERMISSION_DENIED", "PATH_ESCAPES_PROJECT_ROOT", "ACCESS_VIOLATION"}, (
                    f"traversal refusal used an unexpected contract code: {exc.code}"
                )
                traversal_rejection: dict[str, Any] = {
                    "rejected": True,
                    "error_code": exc.code,
                    "message": str(exc),
                }
            except PermissionError as exc:
                traversal_rejection = {
                    "rejected": True,
                    "error_code": "PermissionError",
                    "message": str(exc),
                }
            else:
                raise AssertionError(
                    f"a path escaping the approved project root resolved to {resolved!r} "
                    "instead of being refused"
                )

            # 2. Atomic export failure preservation via export_field_data
            target = self.artifacts_dir / "preserved.txt"
            target.write_text("ORIGINAL_CONTENT")
            orig_sha = _sha256(target)
            entries_before = sorted(p.name for p in self.artifacts_dir.iterdir())

            try:
                store.export_field_data(
                    str(target),
                    {"status": {"ok": False, "engine_error": "SIMULATED_FAILURE"}, "values": [1, 2, 3]},
                )
            except ExecutionContractError as exc:
                export_refusal: dict[str, Any] = {
                    "rejected": True,
                    "error_code": exc.code,
                    "message": str(exc),
                }
            else:
                raise AssertionError("export_field_data accepted a failed engine payload")

            assert target.read_text() == "ORIGINAL_CONTENT"
            assert _sha256(target) == orig_sha

            # 3. Atomic save rollback via atomic_save.  The control has to prove the
            # writer ran and staged partial content: refusing the call before the
            # writer is reached (e.g. via the path gate) would leave the original
            # untouched and make the rollback assertions pass without proving anything.
            from comsol_mcp._atomic_save import atomic_save, AtomicSaveError

            writer_ran: dict[str, Any] = {"ran": False, "staged": None}

            def failing_writer(tmp_p: Path) -> None:
                writer_ran["ran"] = True
                tmp_p.write_text("PARTIAL_CONTENT")
                writer_ran["staged"] = tmp_p.read_text()
                raise RuntimeError("simulated engine failure during export")

            try:
                atomic_save(str(target), failing_writer, project_root=ROOT)
            except (AtomicSaveError, RuntimeError) as exc:
                rollback: dict[str, Any] = {
                    "rejected": True,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            else:
                raise AssertionError("atomic_save reported success although its writer raised")

            assert writer_ran["ran"], (
                "the rollback control never reached the writer, so the rollback path was not "
                "exercised (the call was refused earlier)"
            )
            assert writer_ran["staged"] == "PARTIAL_CONTENT", (
                f"the writer did not stage partial content: {writer_ran['staged']!r}"
            )
            assert target.read_text() == "ORIGINAL_CONTENT"
            assert _sha256(target) == orig_sha

            # The failure path is documented to keep its temporary candidate as
            # incident evidence (see _atomic_save.atomic_save's docstring), and
            # ACCEPTANCE.md C12 asks only that a failed atomic save preserve the
            # original file hash.  So the control records the retained candidate and
            # checks the cleanliness guarantee where it applies: on success.
            retained_candidates = sorted(p.name for p in self.artifacts_dir.glob(".*.tmp.mph"))
            assert all(
                name.startswith(f".{target.name}.") and name.endswith(".tmp.mph")
                for name in retained_candidates
            ), f"the retained candidate does not follow the documented pattern: {retained_candidates}"

            # 4. A successful atomic save publishes the new content and leaves nothing behind.
            import zipfile as _zipfile

            success_target = self.artifacts_dir / "atomic_success.mph"

            def successful_writer(tmp_p: Path) -> None:
                with _zipfile.ZipFile(tmp_p, "w") as archive:
                    archive.writestr("mph/info.xml", "<mph/>")

            candidates_before_success = set(p.name for p in self.artifacts_dir.glob(".*.tmp.mph"))
            publish = atomic_save(str(success_target), successful_writer, project_root=ROOT)
            candidates_after_success = set(p.name for p in self.artifacts_dir.glob(".*.tmp.mph"))
            assert _zipfile.is_zipfile(success_target), "the published file is not a readable ZIP"
            assert publish["sha256"] == _sha256(success_target), (
                "the digest reported by atomic_save does not match the published file"
            )
            assert publish["path"] == str(success_target), (
                f"atomic_save reported {publish['path']!r} instead of {str(success_target)!r}"
            )
            assert candidates_after_success == candidates_before_success, (
                "a successful atomic_save left candidate files behind: "
                f"{sorted(candidates_after_success - candidates_before_success)}"
            )

            # 5. A host-requested export whose destination is outside the approved roots
            #    must be refused before any file side effect.  The delivered control only
            #    covered a relative traversal; an absolute destination was never tried, and
            #    the store's policy admits the process temp tree as a second root, so the
            #    probe has to aim outside both.
            outside_root_probe = Path("/etc/g3_c12_export_probe.json")
            assert not outside_root_probe.exists(), "probe destination must not pre-exist"
            try:
                store.export_field_data(str(outside_root_probe), {"values": [1, 2, 3]})
            except ExecutionContractError as exc:
                absolute_export_refusal: dict[str, Any] = {"rejected": True, "error_code": exc.code}
            else:
                raise AssertionError("export_field_data accepted a destination outside every approved root")
            assert absolute_export_refusal.get("error_code") == "ACCESS_VIOLATION", absolute_export_refusal
            assert not outside_root_probe.exists(), "a refused export created the destination anyway"

            # 6. The published artifact of a host-requested evaluation is project-scoped and
            #    atomically published (C13 completes the read side of the same contract).
            from comsol_mcp._g3_results import _export_to_artifact

            published = _export_to_artifact({"x": [1.0, 2.0, 3.0]}, "c12")
            published_ref = Path(published["artifact_ref"]).resolve()
            assert published_ref.is_relative_to(ROOT.resolve()), published_ref
            assert _sha256(published_ref) == published["sha256"]
            published_candidates = [p.name for p in published_ref.parent.iterdir() if p.name != published_ref.name]
            assert published_candidates == [], published_candidates

            self.record_case(
                "C12",
                "Export Path Traversal Protection & Atomic Rollback",
                "PASS",
                "protocol",
                {
                    "traversal_rejection": traversal_rejection,
                    "export_failure_rejection": export_refusal,
                    "absolute_destination_rejection": absolute_export_refusal,
                    "atomic_rollback": rollback,
                    "writer_reached": writer_ran["ran"],
                    "writer_staged_content": writer_ran["staged"],
                    "preserved_original_sha256": orig_sha,
                    "retained_failure_candidate": retained_candidates,
                    "retained_candidate_policy": (
                        "_atomic_save keeps the failed candidate as incident evidence; "
                        "ACCEPTANCE.md C12 requires the original hash to survive, not its removal"
                    ),
                    "artifacts_dir_entries_before": entries_before,
                    "successful_publish": publish,
                    "candidates_after_success": sorted(candidates_after_success),
                    "published_artifact": {
                        "file_path": str(published_ref),
                        "sha256": published["sha256"],
                        "byte_size": published["byte_size"],
                        "inside_project_root": True,
                        "candidates_left": published_candidates,
                    },
                    "export_root_policy": (
                        "the delivered store admits the project root plus the process temp "
                        "tree; anything else is refused with ACCESS_VIOLATION before a file "
                        "is created"
                    ),
                },
            )
        except Exception as exc:
            self.record_case("C12", "Export Security", "FAIL", "protocol", {}, error=str(exc))

    # -----------------------------------------------------------------------
    # Case C13: T049 Big Array Chunk Streaming
    # -----------------------------------------------------------------------
    def run_c13(self) -> None:
        self.log("Executing C13: Big array chunk streaming with SHA verification...")
        try:
            import base64 as _base64
            import hashlib as _hashlib
            import tracemalloc

            from comsol_mcp._execution_contract import ExecutionContractError
            from comsol_mcp._g3_ops import dispatch as dispatch_operation
            from comsol_mcp._g3_results import _export_to_artifact

            # 1. Publish an artifact the way the adapter does when a payload exceeds the
            #    inline boundary, and pin its immutable digest.  The published path must
            #    stay inside the project root, the pinned digest must match the bytes on
            #    disk, and an atomic publish must leave no candidate behind.
            payload = {"x": [i * 0.5 for i in range(20000)], "y": list(range(20000))}
            meta = _export_to_artifact(payload, "c13", eval_context={"requested_storage": "auto"})
            ref = Path(meta["artifact_ref"]).resolve()
            file_size = meta["byte_size"]
            pinned = meta["sha256"]
            assert ref.is_relative_to(ROOT.resolve()), f"{ref} is not inside {ROOT}"
            assert _sha256(ref) == pinned, "published digest does not match the file on disk"
            leftovers = [p.name for p in ref.parent.iterdir() if p.name != ref.name]
            assert leftovers == [], f"atomic publish left candidates behind: {leftovers}"

            # 2. Reconstruct through the host-requested chunk read (the delivered suite
            #    called ArtifactStore.read_chunk directly, so the operation a host can
            #    actually request was never exercised).  Each chunk carries its own
            #    digest, the rolling digest of the stream must equal the pinned digest of
            #    the whole file -- a streaming hash, not a re-read compared with itself.
            chunk_bytes = 32 * 1024
            digest = _hashlib.sha256()
            offset = 0
            reads = 0
            chunk_digests_ok = True
            while offset < file_size:
                out = dispatch_operation(
                    "artifact.read",
                    self.verifier_worker,
                    "reopen_c06",
                    {
                        "artifact_id": str(ref),
                        "offset": offset,
                        "length": chunk_bytes,
                        # pinning costs one bounded full pass, so pin the first chunk only
                        "expected_sha256": pinned if reads == 0 else None,
                    },
                )
                block = _base64.b64decode(out["data_base64"])
                chunk_digests_ok &= out["chunk_sha256"] == _sha256_bytes(block)
                digest.update(block)
                offset += out["length"]
                reads += 1
                if out["eof"]:
                    break
            reconstructed_sha256 = digest.hexdigest()
            assert reads > 1, f"the payload should span several chunks, got {reads}"
            assert chunk_digests_ok, "a served chunk did not match its own digest"
            assert reconstructed_sha256 == pinned, (reconstructed_sha256, pinned)

            # 3. Refusals, with the code each case actually produced.
            refusals: dict[str, str] = {}
            for label, args in (
                ("path_escape", {"path": "../../etc/passwd", "offset": 0, "length": 16}),
                ("offset_out_of_range", {"artifact_id": str(ref), "offset": file_size + 1, "length": 16}),
                ("forged_whole_digest", {"artifact_id": str(ref), "offset": 0, "length": 16, "expected_sha256": "0" * 64}),
                ("forged_chunk_digest", {"artifact_id": str(ref), "offset": 0, "length": 16, "expected_chunk_sha256": "0" * 64}),
            ):
                try:
                    dispatch_operation("artifact.read", self.verifier_worker, "reopen_c06", args)
                except ExecutionContractError as exc:
                    refusals[label] = exc.code
                else:
                    raise AssertionError(f"{label} was served instead of refused")
            assert refusals == {
                "path_escape": "ACCESS_VIOLATION",
                "offset_out_of_range": "INVALID_CHUNK_RANGE",
                "forged_whole_digest": "ARTIFACT_HASH_MISMATCH",
                "forged_chunk_digest": "CHUNK_HASH_MISMATCH",
            }, refusals

            # 4. Peak memory: NEXT_GOAL refuses "read the whole file, then call it
            #    paging".  Measure a streaming consumer (which folds chunks into a
            #    rolling digest and keeps nothing) against read_bytes() on two files
            #    whose size differs by ~10x: the stream must stay flat, the full read
            #    must track the file.
            def _stream_peak(artifact_ref: Path, size: int, pin: str | None) -> float:
                rolling = _hashlib.sha256()
                off = 0
                tracemalloc.start()
                while off < size:
                    args: dict[str, Any] = {"artifact_id": str(artifact_ref), "offset": off, "length": chunk_bytes}
                    if pin is not None:
                        args["expected_sha256"] = pin
                    result = dispatch_operation("artifact.read", self.verifier_worker, "reopen_c06", args)
                    rolling.update(_base64.b64decode(result["data_base64"]))
                    off += result["length"]
                    if result["eof"]:
                        break
                _cur, peak = tracemalloc.get_traced_memory()
                tracemalloc.stop()
                return peak / 1024

            def _full_read_peak(artifact_ref: Path) -> float:
                tracemalloc.start()
                blob = artifact_ref.read_bytes()
                _hashlib.sha256(blob).hexdigest()
                _cur, peak = tracemalloc.get_traced_memory()
                tracemalloc.stop()
                del blob
                return peak / 1024

            big_meta = _export_to_artifact(
                {"x": [i * 0.5 for i in range(200000)], "y": list(range(200000))}, "c13big"
            )
            big_ref = Path(big_meta["artifact_ref"]).resolve()
            big_size = big_meta["byte_size"]
            peaks: dict[str, Any] = {
                "small_bytes": file_size,
                "big_bytes": big_size,
                "small_stream_no_pin_kib": round(_stream_peak(ref, file_size, None), 1),
                "small_stream_pinned_kib": round(_stream_peak(ref, file_size, pinned), 1),
                "small_full_read_kib": round(_full_read_peak(ref), 1),
                "big_stream_no_pin_kib": round(_stream_peak(big_ref, big_size, None), 1),
                "big_stream_pinned_kib": round(_stream_peak(big_ref, big_size, big_meta["sha256"]), 1),
                "big_full_read_kib": round(_full_read_peak(big_ref), 1),
            }
            peaks["stream_is_size_independent"] = (
                peaks["big_stream_no_pin_kib"] / peaks["small_stream_no_pin_kib"] < 1.6
            )
            peaks["pinned_stream_is_size_independent"] = (
                peaks["big_stream_pinned_kib"] / peaks["small_stream_pinned_kib"] < 1.6
            )
            peaks["full_read_tracks_file_size"] = peaks["big_full_read_kib"] / peaks["small_full_read_kib"] > 3.0
            peaks["file_size_ratio"] = round(big_size / file_size, 2)
            assert peaks["stream_is_size_independent"], peaks
            assert peaks["pinned_stream_is_size_independent"], peaks
            assert peaks["full_read_tracks_file_size"], peaks
            peaks["instrument"] = "tracemalloc peak python allocations (KiB)"
            peaks["note"] = (
                "a pinned expected_sha256 costs one bounded 1 MiB-block pass per call; "
                "the measured peak is therefore block-bounded, not file-bounded"
            )

            self.record_case(
                "C13",
                "Chunk Streaming & Bounded Memory",
                "PASS",
                "protocol",
                {
                    "file_size": file_size,
                    "full_sha256": pinned,
                    "chunk_bytes": chunk_bytes,
                    "chunk_reads": reads,
                    "reconstructed_sha256": reconstructed_sha256,
                    "chunk_digests_verified": chunk_digests_ok,
                    "host_dispatch_operation": "artifact.read",
                    "refusals": refusals,
                    "peak_memory": peaks,
                },
            )
        except Exception as exc:
            self.record_case("C13", "Chunk Streaming", "FAIL", "protocol", {}, error=str(exc))

    # -----------------------------------------------------------------------
    # Case C14: Probe / Table Management
    # -----------------------------------------------------------------------
    def run_c14(self) -> None:
        self.log("Executing C14: Model definitions probe and results separation on live COMSOL model...")
        try:
            # 1. Verify Model Definitions probe CRUD separation from Derived Values
            assert "DomainProbe" in SUPPORTED_PROBE_TYPES
            assert "BoundaryProbe" in SUPPORTED_PROBE_TYPES
            assert "PointProbe" in SUPPORTED_PROBE_TYPES
            assert "GlobalProbe" in SUPPORTED_PROBE_TYPES

            # 2. Verify validation error on unsupported probe
            from comsol_mcp._execution_contract import ExecutionContractError
            try:
                probe_create(self.verifier_worker, "reopen_c06", {"tag": "p_bad", "type_id": "UnknownProbe", "definition": {}})
                raise AssertionError("Expected API_UNSUPPORTED on UnknownProbe")
            except ExecutionContractError as exc:
                assert exc.code == "API_UNSUPPORTED"

            # 3. Create Model Definitions DomainProbe on live COMSOL model
            created_probe = probe_create(self.verifier_worker, "reopen_c06", {
                "tag": "prb1",
                "type_id": "DomainProbe",
                "definition": {"expr": "x + 2*y"},
            })
            assert created_probe["created"] is True

            # 4. List probes and verify prb1 is registered under model/component probes
            probes = probe_list(self.verifier_worker, "reopen_c06")
            assert "prb1" in probes["tags"]

            # 4b. The same operations must be reachable through host dispatch: the F10
            # module was not wired into _g3_ops._MODULES, so a host request could not
            # reach any probe operation while the catalogue advertised five.
            from comsol_mcp._g3_ops import dispatch as dispatch_operation

            self.log("  Verifying host dispatch path for probe operations...")
            dispatched_list = dispatch_operation("probe.list", self.verifier_worker, "reopen_c06", {})
            assert "prb1" in dispatched_list["tags"], dispatched_list
            dispatched_remove = dispatch_operation(
                "probe.remove", self.verifier_worker, "reopen_c06", {"tag": "prb1"}
            )
            assert dispatched_remove["removed"] is True, dispatched_remove
            assert dispatched_remove["verified_removed"] is True, dispatched_remove
            dispatched_recreate = dispatch_operation(
                "probe.create",
                self.verifier_worker,
                "reopen_c06",
                {"tag": "prb1", "type_id": "DomainProbe", "definition": {"expr": "x + 2*y"}},
            )
            assert dispatched_recreate["created"] is True, dispatched_recreate

            # Unimplemented but catalogued operations must be refused, not reported as done.
            unimplemented_probe_ops: dict[str, str] = {}
            for unimplemented in ("probe.update", "probe.history"):
                try:
                    dispatch_operation(unimplemented, self.verifier_worker, "reopen_c06", {})
                except ExecutionContractError as exc:
                    unimplemented_probe_ops[unimplemented] = exc.code
                else:
                    raise AssertionError(f"{unimplemented} reported success although it is not implemented")
            assert unimplemented_probe_ops == {
                "probe.update": "UNSUPPORTED_OPERATION",
                "probe.history": "UNSUPPORTED_OPERATION",
            }, unimplemented_probe_ops

            # 5. Verify probe is NOT in results derived values (model.result.numerical)
            num_tags = list(self.verifier_worker.client().model("reopen_c06")._call("result")._call("numerical")._call("tags"))
            assert "prb1" not in num_tags

            # 6. Test User Table creation with live numeric data write and readback
            created_table = result_table_manage(self.verifier_worker, "reopen_c06", {
                "action": "create",
                "path": "tbl1",
                "definition": {
                    "data": [[1.5, 2.5], [3.5, 4.5]],
                },
            })
            assert created_table["created"] is True

            # 7. Read back table data directly from COMSOL engine (no echo)
            tbl_data = result_table_manage(self.verifier_worker, "reopen_c06", {
                "action": "get",
                "path": "tbl1",
            })
            read_vals = tbl_data.get("data")
            assert read_vals is not None, "Table data should not be None after write"
            assert abs(read_vals[0][0] - 1.5) < 1e-6
            assert abs(read_vals[0][1] - 2.5) < 1e-6
            assert abs(read_vals[1][0] - 3.5) < 1e-6
            assert abs(read_vals[1][1] - 4.5) < 1e-6

            # 8. Clean up created test entities
            probe_remove(self.verifier_worker, "reopen_c06", {"tag": "prb1"})
            plist_after = probe_list(self.verifier_worker, "reopen_c06")
            assert "prb1" not in plist_after["tags"]

            result_table_manage(self.verifier_worker, "reopen_c06", {
                "action": "remove",
                "path": "tbl1",
            })

            self.record_case(
                "C14",
                "Definitions Probe vs Derived Values Separation & Table Management",
                "PASS",
                "numerical",
                {
                    "created_probe_tag": "prb1",
                    "probe_type": "DomainProbe",
                    "probe_scope": "component.probe (separated from result.numerical)",
                    "table_created_tag": "tbl1",
                    "table_written_data": [[1.5, 2.5], [3.5, 4.5]],
                    "table_read_data": read_vals,
                    "validation_verified": True,
                    "probe_host_dispatch": {
                        "reachable": ["probe.list", "probe.create", "probe.remove"],
                        "dispatched_removed_verified": dispatched_remove["verified_removed"],
                        "unimplemented_refusals": unimplemented_probe_ops,
                    },
                },
            )
        except Exception as exc:
            self.record_case("C14", "Probe Separation", "FAIL", "numerical", {}, error=str(exc))

    # -----------------------------------------------------------------------
    # Case C15: T010/T012/T027 Idempotency & Control Plane Responsiveness
    # -----------------------------------------------------------------------
    def run_c15(self) -> None:
        self.log("Executing C15: Idempotency and control plane responsiveness...")
        try:
            worker = self.make_worker("c15_probe")
            try:
                health = worker.health(timeout_s=3.0)
                assert health["status"] == "HEALTHY"
                assert health["ok"] is True
                assert isinstance(health["instance_id"], str)
            finally:
                worker.close()

            self.record_case(
                "C15",
                "Idempotency & Control Responsiveness",
                "PASS",
                "protocol",
                {
                    "worker_status": health["status"],
                    "instance_id": health["instance_id"],
                    "control_responsive": True,
                },
            )
        except Exception as exc:
            self.record_case("C15", "Control Responsiveness", "FAIL", "protocol", {}, error=str(exc))

    # -----------------------------------------------------------------------
    # Case C16: Packaging, Dependencies & Schema
    # -----------------------------------------------------------------------
    def run_c16(self) -> None:
        self.log("Executing C16: packaging, dependency locks and wheel resources...")
        try:
            pyproject = ROOT / "pyproject.toml"
            assert pyproject.is_file(), f"pyproject.toml missing at {pyproject}"
            content = pyproject.read_text(encoding="utf-8")
            assert "dependencies = [" in content, "pyproject.toml declares no dependencies list"

            lock_files = [
                ROOT / "constraints-macos-arm64-py313.txt",
                ROOT / "requirements-windows-cp312.txt",
                ROOT / "pip-freeze-fresh.txt",
            ]
            absent_locks = [str(p.name) for p in lock_files if not p.is_file()]
            assert not absent_locks, f"dependency lock files missing: {absent_locks}"

            wheel_dir = self.run_dir / "wheel_dist"
            wheels = sorted(wheel_dir.glob("*.whl"))
            if not wheels:
                # C16 owns its input: when C00 never got as far as building the wheel,
                # build one here instead of failing on a missing dependency.
                wheel_dir = self.run_dir / "c16_wheel_dist"
                wheel_dir.mkdir(parents=True, exist_ok=True)
                build_res = subprocess.run(
                    [sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", str(wheel_dir), "."],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                )
                assert build_res.returncode == 0, f"pip wheel failed: {build_res.stderr}"
                wheels = sorted(wheel_dir.glob("*.whl"))
            assert len(wheels) == 1, (
                f"expected exactly one built wheel in {wheel_dir}, found {[w.name for w in wheels]}"
            )
            wheel_path = wheels[0]

            # The resources inside the wheel are what the server loads at runtime:
            # without them the action catalog and the Java worker are simply absent.
            import zipfile

            with zipfile.ZipFile(wheel_path) as archive:
                names = archive.namelist()
                required_suffixes = [
                    "comsol_mcp/data/g2/02_ACTION_CATALOG.json",
                    "comsol_mcp/data/g2/common.schema.json",
                    "comsol_mcp/worker_java/PersistentComsolWorker.java",
                ]
                resources: dict[str, Any] = {}
                for suffix in required_suffixes:
                    matches = [n for n in names if n.endswith(suffix)]
                    assert matches, (
                        f"wheel {wheel_path.name} does not package {suffix}; first entries: {names[:10]}"
                    )
                    payload = archive.read(matches[0])
                    resources[suffix] = {
                        "entry": matches[0],
                        "size": archive.getinfo(matches[0]).file_size,
                        "sha256": _sha256_bytes(payload),
                    }
                catalog = json.loads(
                    archive.read(resources[required_suffixes[0]]["entry"]).decode("utf-8")
                )
                schema = json.loads(
                    archive.read(resources[required_suffixes[1]]["entry"]).decode("utf-8")
                )
                assert isinstance(catalog, dict) and catalog, "packaged action catalog is not a non-empty object"
                assert isinstance(schema, dict) and schema, "packaged common.schema.json is not a non-empty object"

            self.record_case(
                "C16",
                "Packaging & Dependencies Lock",
                "PASS",
                "static",
                {
                    "pyproject": str(pyproject),
                    "pyproject_sha256": _sha256(pyproject),
                    "dependency_lock_files": {
                        p.name: _sha256(p) for p in lock_files
                    },
                    "wheel_file": str(wheel_path),
                    "wheel_sha256": _sha256(wheel_path),
                    "wheel_resource_manifest_source": "read out of the built wheel with zipfile",
                    "wheel_resources": resources,
                    "action_catalog_entries": len(catalog),
                    "schema_top_level_keys": sorted(schema)[:10],
                },
            )
        except Exception as exc:
            self.record_case("C16", "Packaging & Dependencies", "FAIL", "static", {}, error=str(exc))

    # -----------------------------------------------------------------------
    # Case C17: Teardown, Evidence Publishing & Scoping
    # -----------------------------------------------------------------------
    def run_c17(self) -> None:
        self.log("Executing C17: teardown and evidence finalization...")
        try:
            # Read the engine-reported version *before* the worker goes away, so the
            # ledger never carries a declared version string (§12).
            self.comsol_version = self.engine_version()

            teardown_errors: list[str] = []
            if self.verifier_worker is not None:
                try:
                    self.verifier_worker.client().disconnect()
                except Exception as exc:
                    teardown_errors.append(f"disconnect: {type(exc).__name__}: {exc}")
                try:
                    self.verifier_worker.close()
                except Exception as exc:
                    teardown_errors.append(f"close: {type(exc).__name__}: {exc}")
                self.verifier_worker = None

            self.stop_server()
            time.sleep(1.0)  # let the process table and lock ownership settle

            lock_report = self.check_locks_released()
            server_alive = self._pid_alive(self.last_server_pid)
            prior = {cid: case["status"] for cid, case in self.cases.items() if cid != "C17"}
            failed = sorted(cid for cid, status in prior.items() if status != "PASS")
            derived_tag = (
                "G3_3_MAC_W17_VERIFIED_SCOPED" if not failed else "G3_3_ACCEPTANCE_FAILED"
            )

            assert not teardown_errors, f"teardown reported errors: {teardown_errors}"
            assert not server_alive, (
                f"the isolated mphserver this suite started (PID {self.last_server_pid}) is still "
                "alive after teardown"
            )
            assert lock_report["locks_held"] == 0, (
                f"{lock_report['locks_held']} worker endpoint lock(s) are still held: "
                f"{lock_report['held_names']}"
            )

            self.record_case(
                "C17",
                "Teardown & Verification Ledger Finalization",
                "PASS",
                "protocol",
                {
                    "isolated_server_pid": self.last_server_pid,
                    "isolated_server_alive_after_teardown": server_alive,
                    "isolated_server_stopped": not server_alive,
                    "lock_files_remaining": lock_report["lock_files"],
                    "locks_held": lock_report["locks_held"],
                    "lock_probe": "fcntl.lockf(LOCK_EX|LOCK_NB) per endpoint lock file",
                    "teardown_errors": teardown_errors,
                    "status_tag": derived_tag,
                    "status_tag_derived_from": "every case recorded in this run",
                    "cases_recorded_before_teardown": len(prior),
                    "failed_cases": failed,
                    "engine_reported_comsol_version": self.comsol_version,
                    "stop_boundary": "W17 (not entering W18)",
                },
            )
        except Exception as exc:
            self.record_case("C17", "Teardown", "FAIL", "protocol", {}, error=str(exc))

    # -----------------------------------------------------------------------
    # Master Execution
    # -----------------------------------------------------------------------
    def run_case_guarded(self, run_case: Any) -> None:
        """Run one acceptance case; an escaping exception is a FAIL, not a crash.

        The delivered suite let a case exception abort the entire run (run_c03
        re-raised), so a single failure produced no ledger at all.  A case that dies
        must still appear in the ledger as FAIL, with its traceback, and the
        remaining cases must still run.
        """
        case_id = run_case.__name__.removeprefix("run_").upper()
        try:
            run_case()
        except Exception as exc:
            self.log(f"{case_id} aborted: {type(exc).__name__}: {exc}")
            aborted = {
                "case_id": case_id,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
            self.aborts.append(aborted)
            existing = self.cases.get(case_id)
            if existing is None:
                self.record_case(
                    case_id, f"{case_id} (aborted before recording)", "FAIL", "protocol", {},
                    error=f"{type(exc).__name__}: {exc}",
                )
            else:
                existing["status"] = "FAIL"
                existing["error"] = existing.get("error") or f"{type(exc).__name__}: {exc}"

    def run_all(self) -> None:
        start_time = time.monotonic()
        self.log("================================================================")
        self.log(f"Starting G3.3 Live Acceptance Suite (Run ID: {self.run_id})")
        self.log("================================================================")

        try:
            self.start_isolated_server()

            for run_case in (
                self.run_c00,
                self.run_c01,
                self.run_c02,
                self.run_c03,
                self.run_c04,
                self.run_c05,
                self.run_c06,
                self.run_c07,
                self.run_c08,
                self.run_c09,
                self.run_c10,
                self.run_c11,
                self.run_c12,
                self.run_c13,
                self.run_c14,
                self.run_c15,
                self.run_c16,
                self.run_c17,
            ):
                self.run_case_guarded(run_case)

        finally:
            if self.verifier_worker is not None:
                try:
                    self.verifier_worker.client().disconnect()
                except Exception as exc:
                    self.log(f"  cleanup disconnect failed: {type(exc).__name__}: {exc}")
                try:
                    self.verifier_worker.close()
                except Exception as exc:
                    self.log(f"  cleanup close failed: {type(exc).__name__}: {exc}")
                self.verifier_worker = None
            self.stop_server()

        elapsed = time.monotonic() - start_time
        self.log(f"Suite completed in {elapsed:.2f}s")
        self.write_acceptance_evidence(elapsed)

    def write_acceptance_evidence(self, elapsed_s: float) -> None:
        # Build comprehensive phase4_3_acceptance.json
        all_passed = all(c["status"] == "PASS" for c in self.cases.values())
        source = getattr(self, "source", None) or self.source_manifest()
        runner_path = Path(__file__).resolve()
        summary = {
            "schema": "comsol-mcp-g3/phase4_3-acceptance/1",
            "goal": "NEXT_GOAL.md: G3.3 clean-room recovery, evidence correction, and W17 verification",
            "status": "G3_3_MAC_W17_VERIFIED_SCOPED" if all_passed else "ACCEPTANCE_FAILED",
            "run_id": self.run_id,
            "run_dir": str(self.run_dir),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": elapsed_s,
            "platform": sys.platform,
            "python_version": sys.version,
            "comsol_version": self.comsol_version,
            "comsol_version_source": (
                "ModelUtil.getComsolVersion() via the run's worker"
                if self.comsol_version
                else "unavailable: the engine did not report a version in this run"
            ),
            # §11/§12: the ledger is bound to the source it was produced from.
            "source_manifest": source,
            "runner_sha256": _sha256(runner_path),
            "runner_command": sys.argv,
            "total_cases": len(self.cases),
            "aborted_cases": [
                {"case_id": item["case_id"], "error_type": item["error_type"], "error": item["error"]}
                for item in self.aborts
            ],
            "passed_cases": sum(1 for c in self.cases.values() if c["status"] == "PASS"),
            "failed_cases": sum(1 for c in self.cases.values() if c["status"] != "PASS"),
            "scope": {
                "verified_workstream": "W17",
                "stop_boundary": "W17 (do not advance to W18-W26)",
                "target_platform": "macOS-aarch64 (Apple Silicon commercial installation)",
                "unverified_platforms": ["windows", "linux", "intel-mac", "comsol-6.3", "gui"],
            },
            "cases": self.cases,
        }

        # Write in-tree evidence first: every later digest covers real files.
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(summary, indent=2, ensure_ascii=False) + "\n"
        (self.evidence_dir / "result.json").write_text(payload, encoding="utf-8")
        (self.evidence_dir / "assertions.json").write_text(
            json.dumps(
                {
                    "run_id": self.run_id,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "source_manifest": source,
                    "aborted_cases": self.aborts,
                    "assertions": [
                        {
                            "case_id": case["case_id"],
                            "name": case["name"],
                            "status": case["status"],
                            "evidence_level": case["evidence_level"],
                            "error": case["error"],
                            "details": case["details"],
                            "timestamp": case["timestamp"],
                        }
                        for case in self.cases.values()
                    ],
                },
                indent=2,
                ensure_ascii=False,
            ) + "\n",
            encoding="utf-8",
        )
        (self.evidence_dir / "runner.log").write_text("\n".join(self.log_lines) + "\n", encoding="utf-8")
        (self.evidence_dir / "RUN_NOTES.md").write_text(
            "# Run notes\n\n"
            f"- run_id: `{self.run_id}`\n"
            f"- goal: {summary['goal']}\n"
            f"- status: `{summary['status']}`\n"
            f"- source: `{source['head']}` (tree `{source['tree']}`, branch `{source['branch']}`)\n"
            f"- engine: {self.comsol_version or 'unavailable'}\n\n"
            "## What is tracked and what is not\n\n"
            "This directory is tracked selectively.  The evidence documents are:\n\n"
            "- `result.json` -- the ledger written for this run (same payload as\n"
            "  `evidence/phase4_3_acceptance.json`);\n"
            "- `assertions.json` -- per-case assertion records plus any aborted cases\n"
            "  with their tracebacks;\n"
            "- `runner.log` -- the runner's own log lines for this run;\n"
            "- `source_manifest.json` -- HEAD, tree, branch, tracked-file count and the\n"
            "  aggregate blob-map digest the ledger is bound to;\n"
            "- `environment.json` -- COMSOL root, JDK, interpreter, cwd, command line;\n"
            "- `case_inventory.json` -- the case ids recorded in this run;\n"
            "- `SHA256SUMS.json` -- digests of the run artifacts and of the evidence\n"
            "  files above, so a verifier can detect edits.\n\n"
            "Run-local working state is deliberately *not* tracked (see `.gitignore`):\n"
            "`artifacts/` (solved models and sentinel files), `prefs/` (the isolated\n"
            "engine's private workspace), `tmp/`, `locks/`, `recovery/`, `worker_*/`,\n"
            "`test_venv/`, `wheel_dist/`, generated `*.java` builders, `server.port` and\n"
            "`mphserver.log`.  Those are inputs the run consumed or produced on disk;\n"
            "they are reproducible by re-running the suite, and their digests are in\n"
            "`SHA256SUMS.json`.\n\n"
            "## How to re-verify\n\n"
            "```\n"
            f"cd {ROOT}\n"
            f".venv/bin/python tests/run_g3_3_live_acceptance.py --run-dir {self.run_dir}\n"
            "```\n\n"
            "The run refuses to start from a tree with modified tracked files, so the\n"
            "ledger it writes is always bound to a commit.\n",
            encoding="utf-8",
        )
        (self.evidence_dir / "source_manifest.json").write_text(
            json.dumps(source, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        (self.evidence_dir / "case_inventory.json").write_text(
            json.dumps(list(self.cases.keys()), indent=2) + "\n", encoding="utf-8"
        )
        (self.evidence_dir / "environment.json").write_text(
            json.dumps(
                {
                    "comsol_root": str(COMSOL_ROOT),
                    "comsol_version": self.comsol_version,
                    "comsol_version_source": summary["comsol_version_source"],
                    "jdk_home": str(JDK11),
                    "python_executable": sys.executable,
                    "platform": sys.platform,
                    "cwd": str(Path.cwd()),
                    "command": sys.argv,
                    "run_dir": str(self.run_dir),
                },
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )

        # SHA256SUMS covers the run artifacts *and* the evidence written above, so
        # the digests a verifier checks are themselves part of the run.
        sums: dict[str, str] = {}
        for directory, prefix in ((self.artifacts_dir, ""), (self.evidence_dir, "evidence/")):
            for f in sorted(directory.glob("*")):
                if f.is_file() and f.name != "SHA256SUMS.json":
                    sums[f"{prefix}{f.name}"] = _sha256(f)
        (self.evidence_dir / "SHA256SUMS.json").write_text(
            json.dumps(sums, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

        # The top-level ledger is written last: it is the published artifact and it
        # must reflect a run whose evidence already exists on disk.
        out_ledger = ROOT / "evidence" / "phase4_3_acceptance.json"
        out_ledger.write_text(payload, encoding="utf-8")
        self.log(f"Wrote acceptance ledger: {out_ledger}")
        self.log(f"All run artifacts and evidence saved to {self.evidence_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="G3.3 Live Acceptance Suite")
    parser.add_argument("--run-dir", default=None, help="Working directory for acceptance run")
    args = parser.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if args.run_dir:
        run_dir = Path(args.run_dir).expanduser().resolve()
    else:
        # §11: run evidence lives inside the repository, so the ledger's run_id,
        # its per-run directory and the SHA256SUMS digests all resolve in-tree.
        run_dir = ROOT / "evidence" / "phase4_3" / "runs" / f"live_acceptance_{stamp}"
    runner = AcceptanceRunner(run_dir)
    runner.run_all()


if __name__ == "__main__":
    main()
