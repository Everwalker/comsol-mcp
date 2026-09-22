"""Fresh G3.3 stdio acceptance. Never modifies COMSOL installation settings.

All model construction and numerical assertions travel through public MCP tools.
The Java API isolation probes only authenticate/disconnect; they do not touch models.
"""
from __future__ import annotations

import argparse
import ast
import copy
from collections.abc import Mapping
from datetime import datetime, timezone
import asyncio
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tools"), str(ROOT / "tests")]
import phase4_run_mcp as protocol
import phase3_remote_addr_valve_runtime as isolation
COMSOL_ROOT = Path(os.environ.get("COMSOL_ROOT", "/Applications/COMSOL64/Multiphysics"))
JDK_HOME = Path(os.environ.get("COMSOL_JAVA_HOME", "/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home"))


class AcceptanceRunner:
    """Own exactly one new server; never attach to or stop an existing server."""
    def __init__(self, run):
        self.run = run
        self.server_proc = None
        for name in ("prefs", "tmp", "recovery", "artifacts"):
            (run / name).mkdir(mode=0o700, exist_ok=True)
    def start_isolated_server(self):
        self.log = (self.run / "mphserver.log").open("w")
        portfile = self.run / "server.port"
        command = [str(COMSOL_ROOT / "bin/comsol"), "mphserver", "-port", "0", "-portfile", str(portfile),
                   "-prefsdir", str(self.run / "prefs"), "-tmpdir", str(self.run / "tmp"),
                   "-recoverydir", str(self.run / "recovery"), "-login", "auto", "-silent", "-multi", "on"]
        self.server_proc = subprocess.Popen(command, stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(100):
            if portfile.is_file():
                return int(portfile.read_text().strip())
            if self.server_proc.poll() is not None:
                raise RuntimeError("New COMSOL server exited; see mphserver.log")
            time.sleep(.3)
        raise TimeoutError("New COMSOL server did not publish a port")
    def stop_server(self):
        if self.server_proc and self.server_proc.poll() is None:
            self.server_proc.terminate()
            self.server_proc.wait(timeout=15)
        if hasattr(self, "log"):
            self.log.close()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")


class OwnedHost(protocol.ProductionHost):
    """Close our stdio session and only freshly identified private children."""
    async def _confirm_quiescence(self):
        pending = set()
        active = {"QUEUED", "RUNNING", "STARTING", "ACTIVE", "IN_PROGRESS", "UNKNOWN"}
        terminal = {"SUCCEEDED", "FAILED", "CANCELLED", "CANCELED", "REJECTED", "EXPIRED"}
        for row in list(self.transcript):
            reply = row.get("structuredContent") or {}
            data = reply.get("data") or {}
            job = data.get("job_id") or (reply.get("execution") or {}).get("job_id")
            if not job:
                continue
            status = data.get("status")
            if ((isinstance(status, str) and status in active) or data.get("rpc_wait_expired")
                    or (reply.get("error") or {}).get("code") == "EXECUTION_STATE_UNKNOWN"):
                pending.add(job)
            if ((isinstance(status, str) and status in terminal)
                    or (data.get("metadata") or {}).get("reconciled_quiescent") is True):
                pending.discard(job)
        deadline, observations, sequence = time.monotonic() + 60, [], 0
        while pending and time.monotonic() < deadline:
            for job in list(pending):
                for operation in ("job_status", "job_reconcile"):
                    sequence += 1
                    reply = await self.call(operation, {"job_id": job,
                        "execution": protocol._execution(key=f"teardown-observe-{self.label}-{sequence}")}, reconcile=False)
                    observations.append({"job_id": job, "operation": operation, "response": reply})
                    data = reply.get("data") or {}
                    status = data.get("status")
                    if ((isinstance(status, str) and status in terminal)
                            or (data.get("metadata") or {}).get("reconciled_quiescent") is True):
                        pending.discard(job)
            if pending:
                await asyncio.sleep(.25)
        write_json(self.run_dir / f"{self.label}.quiescence.json", {"pending_jobs": sorted(pending), "observations": observations})
        if pending:
            self.retain_runtime = self.args.retain_runtime = True

    async def __aexit__(self, *exc):
        try:
            await self._confirm_quiescence()
        except BaseException as error:
            self.retain_runtime = self.args.retain_runtime = True
            write_json(self.run_dir / f"{self.label}.quiescence_error.json", {"error": str(error)})
        try:
            if self.session is not None:
                await self.session.__aexit__(*exc)
        finally:
            if self.transport is not None:
                try:
                    await self.transport.__aexit__(*exc)
                except BaseException as close_error:
                    write_json(self.run_dir / f"{self.label}.transport_close.json", {"error_type": type(close_error).__name__})
            if self.log_stream is not None:
                self.log_stream.close()
            if getattr(self, "retain_runtime", False):
                write_json(self.run_dir / f"{self.label}.cleanup.json", {"status": "RETAINED_FOR_ACTIVE_JOB", "private_home": str(self.private_home)})
            else:
                results = []
                for endpoint in (self.private_home / "control-private/worker/worker_endpoint.json",
                                 self.private_home / "control-private/control.json"):
                    if not endpoint.is_file():
                        continue
                    pid = json.loads(endpoint.read_text()).get("pid")
                    if type(pid) is not int or pid <= 1:
                        continue
                    before = isolation.base._process_snapshot(pid)
                    if not before:
                        continue
                    command = before.get("command", "")
                    if str(self.private_home) not in command:
                        results.append({"pid": pid, "status": "UNVERIFIED_OWNERSHIP"})
                        continue
                    again = isolation.base._process_snapshot(pid)
                    if not again or (again["birth"], again["command_sha256"]) != (before["birth"], before["command_sha256"]):
                        continue
                    os.kill(pid, signal.SIGTERM)
                    deadline = time.monotonic() + 5
                    while isolation.base._process_snapshot(pid) and time.monotonic() < deadline:
                        await asyncio.sleep(.1)
                    remaining = isolation.base._process_snapshot(pid)
                    forced = False
                    # Match the worker close policy, only for this fresh private child
                    # after every synchronous request completed and no UNKNOWN job is pending.
                    if remaining and not self.unresolved_jobs and "PersistentComsolWorker" in command:
                        if (remaining["birth"], remaining["command_sha256"]) == (before["birth"], before["command_sha256"]):
                            os.kill(pid, signal.SIGKILL)
                            forced = True
                            await asyncio.sleep(.3)
                    results.append({"pid": pid, "birth": before["birth"], "forced_owned_worker": forced,
                                    "status": "EXITED" if not isolation.base._process_snapshot(pid) else "STILL_RUNNING"})
                for record in results:
                    if isolation.base._process_snapshot(record["pid"]) is None:
                        record["status"] = "EXITED"
                write_json(self.run_dir / f"{self.label}.cleanup.json", results)
                assert all(record["status"] == "EXITED" for record in results), results


def builder_source(variable: str) -> str:
    """Reuse versioned fixture code, not an old artifact or receipt."""
    tree = ast.parse((ROOT / "tests/run_g3_3_live_acceptance.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == variable for t in node.targets):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                return node.value.value
    raise ValueError(variable)


INSPECTOR = """
import com.comsol.model.*;
import java.util.*;
public final class SavedStateInspector {
 public static Object run(Model model, Map<String,Object> args) {
  Map<String,Object> out = new LinkedHashMap<>();
  out.put("datasets", model.result().dataset().tags());
  out.put("solutions", model.sol().tags());
  out.put("derived_values", model.result().numerical().tags());
  out.put("material_k", model.material("mat1").propertyGroup("def").getStringArray("thermalconductivity"));
  out.put("left_T", model.physics("ht").feature("temp1").getString("T0"));
  out.put("right_T", model.physics("ht").feature("temp2").getString("T0"));
  out.put("solver_features", model.sol("sol1").feature().tags());
  if (Boolean.TRUE.equals(args.get("transient"))) {
   out.put("solution_parameters", model.sol("sol1").getPNames());
   out.put("solution_values", model.sol("sol1").getPVals());
   out.put("study_tlist", model.study("std1").feature("time").getString("tlist"));
   out.put("solver_tlist", model.sol("sol1").feature("t1").getString("tlist"));
   out.put("solver_rtol", model.sol("sol1").feature("t1").getString("rtol"));
  }
  if (Boolean.TRUE.equals(args.get("continuation"))) {
   out.put("table", model.result().numerical("user_derived_probe").getString("table"));
   out.put("tables", model.result().table().tags());
  }
  return out;
 }
}
"""

CONTINUE_C = """
import com.comsol.model.*;
import java.util.*;
public final class ContinueC {
 public static Object run(Model model, Map<String,Object> args) {
  model.study("std1").feature("time").set("tlist", "range(0, 1.0, 8.0)");
  model.sol("sol1").feature("t1").set("tlist", "range(0, 1.0, 8.0)");
  model.sol("sol1").feature("t1").set("rtol", "0.00001");
  model.result().table().create("user_table", "Table");
  model.result().numerical("user_derived_probe").set("table", "user_table");
  model.result().numerical("user_derived_probe").set("data", "dset1");
  model.result().numerical("user_derived_probe").selection().geom(2);
  model.result().numerical("user_derived_probe").selection().all();
  model.sol("sol1").runAll();
  model.result().numerical("user_derived_probe").setResult();
  return Collections.singletonMap("status", "CONTINUED_MANUAL_SOLVER");
 }
}
"""


class EngineReadback:
    """Adapter over a recorded public MCP engine reply, never invented field data."""
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.derived_values = snapshot["derived_values"]
        self.solver_settings = snapshot
    class Tags:
        def __init__(self, rows): self.rows = rows
        def tags(self): return self.rows
    def result(self): return self
    def dataset(self): return self.Tags(self.snapshot["datasets"])
    def sol(self): return self.Tags(self.snapshot["solutions"])


def scalars(value):
    if isinstance(value, list):
        return [number for child in value for number in scalars(child)]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [float(value)]
    raise AssertionError(f"Unexpected real result: {value!r}")


async def execute_java(client, run, name, source, arguments=None):
    source_sha = hashlib.sha256(source.encode()).hexdigest()
    path = run / "java_sources" / source_sha / (name + ".java")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        assert path.read_text() == source
    else:
        path.write_text(source)
    reply = await client.action("code.execute_java", {"mode": "trusted", "source_artifact": str(path.relative_to(ROOT)),
                                "entrypoint": name, "arguments": arguments or {}}, reconcile=False)
    if not protocol._success(reply):
        job = reply.get("execution", {}).get("job_id")
        if job:
            for operation in ("job_status", "job_result", "job_reconcile"):
                observed = await client.host.call(operation, {"job_id": job,
                    "execution": protocol._execution(key=operation + "-" + job)}, reconcile=False)
                write_json(run / (operation + "-" + job + ".json"), observed)
            if reply.get("error", {}).get("code") == "EXECUTION_STATE_UNKNOWN":
                key = reply["execution"]["idempotency_key"]
                sent = next(row for row in reversed(client.host.transcript)
                            if row.get("arguments", {}).get("execution", {}).get("idempotency_key") == key)
                before = await client.host.call("job_log", {"job_id": job, "limit": 1000}, reconcile=False)
                replay = await client.host.call(sent["operation"], copy.deepcopy(sent["arguments"]), reconcile=False)
                after = await client.host.call("job_log", {"job_id": job, "limit": 1000}, reconcile=False)
                assert replay["execution"]["job_id"] == job and not protocol._success(replay), replay
                assert before["data"]["events"] == after["data"]["events"]
                assert len(before["data"]["events"]) < 1000
                write_json(run / ("unknown_no_replay-" + job + ".json"), {
                    "status": "PASS", "original": reply, "retry": replay,
                    "before_events": before["data"]["events"], "after_events": after["data"]["events"]})
        raise AssertionError(reply)
    worker = reply["data"]["worker"]
    assert worker.get("ok") is True and worker.get("status") == "SUCCEEDED", worker
    return worker["result"]["readback"]


async def inspect(client, run, chain):
    return await execute_java(client, run, "SavedStateInspector", INSPECTOR,
                              {"transient": chain != "a", "continuation": chain == "c"})


async def points(client, chain, *, validate=True):
    reply = await client.action("result.at_points", {
        "spec": {"expressions": ["T", "ht.tfluxx"] if chain == "a" else ["T"],
                 "complex_mode": "real", "solution": {"dataset": "dset1"}},
        "points": [{"x": x, "y": .005} for x in [.0125, .025, .0375]],
        "coordinate_unit": "m", "frame": "spatial"}, reconcile=False)
    if not validate:
        return reply
    assert protocol._success(reply), reply
    data = reply["data"]
    assert data["status"]["ok"] is True and not data["status"]["cleanup_failed"], data
    values = scalars(data["values"])
    assert len(values) >= (6 if chain == "a" else 9), values
    if chain == "a":
        for actual, expected in zip(values[:3], [308.15, 323.15, 338.15]):
            assert abs(actual - expected) <= .001, (actual, expected)
        assert all(abs(v + 480000.) <= .1 for v in values[3:]), values
    return data


async def idempotency_check(client, run, original_data):
    """Resend identical wire arguments, then test a conflicting body under that key."""
    key, sent = next(reversed(client.dispatched.items()))
    first_row = next(row for row in reversed(client.host.transcript)
                     if row.get("arguments", {}).get("execution", {}).get("idempotency_key") == key)
    original = first_row["structuredContent"]
    job = original["execution"]["job_id"]
    async def log(suffix):
        reply = await client.host.call("job_log", {"job_id": job, "limit": 1000,
            "execution": protocol._execution(key="observe-log-" + suffix)}, reconcile=False)
        assert protocol._success(reply), reply
        return reply
    before = await log("before")
    replay, retry_record = await client.retry(key)
    after = await log("after")
    assert protocol._success(replay) and retry_record["same_body"], replay
    assert replay["execution"]["job_id"] == job
    assert replay["execution"]["operation_id"] == original["execution"]["operation_id"]
    # The transcript is already redacted; compare the two actual wire payloads
    # before redaction (including the private isolation receipt).
    assert replay["data"] == original_data
    assert len(before["data"]["events"]) < 1000, "job log must not be truncated"
    assert before["data"]["events"] == after["data"]["events"], "same-key retry dispatched new work"
    conflict_body = copy.deepcopy(first_row["arguments"])
    conflict_body["arguments"]["spec"]["expressions"] = ["T+1[K]"]
    conflict = await client.host.call(first_row["operation"], conflict_body, reconcile=False)
    assert not protocol._success(conflict) and conflict["error"]["code"] == "IDEMPOTENCY_CONFLICT", conflict
    write_json(run / "idempotency.json", {"original_execution": original["execution"], "retry": retry_record,
        "replayed_execution": replay["execution"], "before_log": before, "after_log": after, "conflict": conflict,
        "scope": "real numerical operation over public MCP", "status": "PASS"})


async def connect(host, key):
    reply = await host.call("server_connect", {"host": host.args.host, "port": host.args.port,
                            "execution": protocol._execution(key=key)}, reconcile=False)
    assert protocol._success(reply), reply


async def study_fault_case(client, run):
    """Native failing study, with its original UNKNOWN and no replay preserved."""
    source = '''import com.comsol.model.*; import java.util.*;
public final class UnboundProbeNegative {
 public static Object run(Model model, Map<String,Object> args) {
  model.probe().create("unbound_negative", "GlobalVariable");
  model.probe("unbound_negative").set("expr", "2");
  model.probe("unbound_negative").genResult("sol1");
  return Collections.singletonMap("negative_fixture", "unbound native probe");
 }
}'''
    # Deliberately bypass the public probe validator only to construct a known
    # invalid native fixture. The operation being accepted is public study.run.
    await execute_java(client, run, "UnboundProbeNegative", source)
    original = await client.action("study.run", {
        "study": {"segments": [{"collection": "study", "tag": "std1"}]}}, reconcile=False)
    write_json(run / "study_fault_original.json", original)
    assert not protocol._success(original), original
    # An unobservable engine state is reported as the contract field
    # `execution_state_unknown` (G3_OPERATIONS: 引擎状态无法观测时记
    # execution_state_unknown，绝不推断为"未发生"), not as a success-shaped envelope:
    # the error vector names the cause, forbids a safe retry and asks for reconciliation.
    fault_data = original.get("data") if isinstance(original.get("data"), Mapping) else {}
    unknown_flag = (original.get("execution_state_unknown")
                    if "execution_state_unknown" in original
                    else fault_data.get("execution_state_unknown"))
    error = original.get("error") or {}
    assert unknown_flag is True, original
    assert fault_data.get("ok") is False, original
    assert fault_data.get("applied_count") == 1 and fault_data.get("failed_count") == 1, original
    assert fault_data.get("not_executed_count") == 0, original
    assert error.get("code") in {"ENGINE_CALL_FAILED", "EXECUTION_STATE_UNKNOWN"}, error
    assert error.get("safe_retry") is False, error
    # The reconciliation demand is published by the control daemon on the data mapping and,
    # for an engine-side failure, by the worker's isolation proof; either one is the same
    # demand, so the case records which source carried it.
    reconciliation = fault_data.get("requires_model_reconciliation")
    reconciliation_source = "data.requires_model_reconciliation"
    if reconciliation is None:
        reconciliation = (fault_data.get("isolation_proof") or {}).get("requires_model_reconciliation")
        reconciliation_source = "data.isolation_proof.requires_model_reconciliation"
    assert reconciliation is True, (reconciliation_source, original)
    execution = original["execution"]
    job, key = execution["job_id"], execution["idempotency_key"]
    observations, quiescent = {}, False
    for operation in ("job_status", "job_result", "job_reconcile"):
        reply = await client.host.call(operation, {"job_id": job,
            "execution": protocol._execution(key="study-fault-" + operation)}, reconcile=False)
        observations[operation] = reply
        data = reply.get("data") or {}
        quiescent = quiescent or data.get("status") in ("SUCCEEDED", "FAILED", "CANCELLED", "REJECTED") or (data.get("metadata") or {}).get("reconciled_quiescent") is True
    write_json(run / "study_fault_observations.json", observations)
    assert quiescent, "Original failing study is not proven quiescent"
    before = await client.host.call("job_log", {"job_id": job, "limit": 1000,
        "execution": protocol._execution(key="study-fault-log-before")}, reconcile=False)
    replay, retry_record = await client.retry(key)
    after = await client.host.call("job_log", {"job_id": job, "limit": 1000,
        "execution": protocol._execution(key="study-fault-log-after")}, reconcile=False)
    assert protocol._success(before) and protocol._success(after)
    assert retry_record["same_body"] and not protocol._success(replay)
    assert replay["execution"]["job_id"] == job
    assert replay["execution"]["operation_id"] == execution["operation_id"]
    assert before["data"]["events"] == after["data"]["events"]
    assert len(before["data"]["events"]) < 1000
    refreshed = await client.host.call("model_inspect", {"refresh": True,
        "execution": protocol._execution(key="study-fault-refresh", request="model_inspect",
            ref=client.state.get("ref"), revision=client.state.get("revision"))}, reconcile=False)
    refreshed_execution = refreshed.get("execution") or (refreshed.get("data") or {}).get("execution") or {}
    assert protocol._success(refreshed) and refreshed_execution.get("dirty") is False, refreshed
    client._adopt_readback(refreshed)
    write_json(run / "study_fault_assertions.json", {"status": "PASS",
        "evidence_level": "LIVE_PUBLIC_MCP_NATIVE_STUDY_FAILURE", "original_unknown_preserved": True,
        "unknown_flag_source": "execution_state_unknown", "reconciliation_source": reconciliation_source,
        "quiescent": quiescent, "retry_record": retry_record, "replay": replay,
        "before_log": before, "after_log": after, "refresh": refreshed})


async def exercise(args, run):
    from comsol_mcp._gate_a_reopen import verify_reopen
    # Receipts are fixed from builder reads before closing its host/worker.
    transcript, state, receipts, reports = [], {}, {}, {}
    try:
        async with OwnedHost(args, run, transcript, label="builder", private_home=run / "private/builder", run_state=state) as host:
            client = protocol.ActionClient(host, args, state)
            await connect(host, "builder-connect")
            for chain in args.chains:
                state.pop("ref", None)
                created = await protocol._create_empty_model(client, state, "G33FreshProtocol" + chain, host)
                assert protocol._success(created), created
                await execute_java(client, run, "Chain" + chain.upper() + "Builder", builder_source("chain_" + chain + "_code"))
                initial = await inspect(client, run, chain) if chain != "c" else None
                if chain == "a":
                    # This legacy W16 route shares the changed Interp lifecycle;
                    # exercise it with a new native model and independent oracle.
                    path_reply = await client.action("result.sample_path", {
                        "spec": {"expressions": ["T", "ht.tfluxx"], "dataset": "dset1", "solution": "sol1"},
                        "path_definition": {"kind": "line", "start": [.0125, .005],
                                            "end": [.0375, .005], "samples": 3}}, reconcile=False)
                    write_json(run / "sample_path_regression_reply.json", path_reply)
                    assert protocol._success(path_reply), path_reply
                    sample_rows = protocol._extract_samples(path_reply)
                    sampled_t = protocol._sample_series(sample_rows, ("T", "T [K]"))
                    sampled_flux = protocol._sample_series(sample_rows, ("ht.tfluxx",))
                    assert sampled_t is not None and len(sampled_t) == 3, sample_rows
                    assert sampled_flux is not None and len(sampled_flux) == 3, sample_rows
                    assert all(abs(actual - expected) <= .001 for actual, expected in
                               zip(sampled_t, [308.15, 323.15, 338.15])), sampled_t
                    assert all(abs(actual + 480000.) <= .1 for actual in sampled_flux), sampled_flux
                    write_json(run / "sample_path_regression.json", {
                        "status": "PASS", "scope": "LIVE_PUBLIC_MCP_AFFECTED_W16_REGRESSION",
                        "expected_temperature": [308.15, 323.15, 338.15], "expected_heat_flux": -480000.,
                        "temperature_abs_tolerance": .001, "heat_flux_abs_tolerance": .1,
                        "sampled_temperature": sampled_t, "sampled_heat_flux": sampled_flux,
                        "oracle": "one-dimensional constant-conductivity temperature gradient in Chain A"})
                if chain == "c":
                    # C is a separately modified artifact with a manually executed solver.
                    initial = await execute_java(client, run, "SavedStateInspector", INSPECTOR,
                                                 {"transient": True, "continuation": False})
                    await execute_java(client, run, "ContinueC", CONTINUE_C)
                saved_state = await inspect(client, run, chain)
                values = await points(client, chain)
                if chain == "a":
                    await idempotency_check(client, run, values)
                if chain != "a":
                    times = saved_state["solution_values"]
                    assert len(set(times)) >= 3 and len([t for t in set(times) if t > 0]) >= 2, times
                    assert len(scalars(values["values"])) == 3 * len(times), (values, times)
                if chain == "c":
                    assert initial["study_tlist"] != saved_state["study_tlist"]
                    assert initial["solver_tlist"] != saved_state["solver_tlist"]
                    assert saved_state["table"] == "user_table"
                    assert float(saved_state["solver_rtol"]) == 1e-5
                mph = run / "artifacts" / ("chain_" + chain + ".mph")
                saved = await client.action("save_model", {"path": str(mph)}, reconcile=False)
                assert protocol._success(saved) and mph.is_file(), saved
                flat = scalars(values["values"])
                receipts[chain] = {"model_sha256": hashlib.sha256(mph.read_bytes()).hexdigest(),
                    "byte_size": mph.stat().st_size, "dataset": "dset1", "solution": "sol1",
                    "initial_state": initial, "solver_settings": saved_state,
                    "derived_values": saved_state["derived_values"], "builder_model_ref": dict(state["ref"]),
                    "point_readback": values,
                    "expectations": {f"sample_{i}": {"expected": value, "tolerance": .001 if i < 3 or chain != "a" else .1}
                                     for i, value in enumerate(flat)}}
                write_json(run / ("receipt_" + chain + ".json"), receipts[chain])
        state = {}
        async with OwnedHost(args, run, transcript, label="reopen", private_home=run / "private/reopen", run_state=state) as host:
            client = protocol.ActionClient(host, args, state)
            await connect(host, "reopen-connect")
            for chain in args.chains:
                state.pop("ref", None)
                mph = run / "artifacts" / ("chain_" + chain + ".mph")
                receipt = receipts[chain]
                assert hashlib.sha256(mph.read_bytes()).hexdigest() == receipt["model_sha256"]
                loaded, _ = await protocol._load_model(host, client, mph, "chain-" + chain, key="load-" + chain)
                assert protocol._success(loaded), loaded
                ref, revision = protocol._payload_execution(loaded)
                state.update(ref=ref, revision=revision)
                assert ref["server_instance_id"] != receipt["builder_model_ref"]["server_instance_id"], ref
                observed = await inspect(client, run, chain)
                read = await points(client, chain)
                if chain == "a":
                    key, _ = next(reversed(client.dispatched.items()))
                    sent = next(row for row in reversed(host.transcript)
                        if row.get("arguments", {}).get("execution", {}).get("idempotency_key") == key)
                    stale_body = copy.deepcopy(sent["arguments"])
                    stale_body["execution"]["model_ref"] = receipt["builder_model_ref"]
                    stale_body["execution"]["idempotency_key"] = "stale-builder-ref-after-reopen"
                    stale_body["execution"]["request_id"] = "stale-builder-ref-after-reopen"
                    stale = await host.call(sent["operation"], stale_body, reconcile=False)
                    assert not protocol._success(stale) and (stale.get("error") or {}).get("code") == "MODEL_IDENTITY_MISMATCH", stale
                    write_json(run / "stale_reference_after_reopen.json", {"status": "PASS",
                        "old_ref": receipt["builder_model_ref"], "new_ref": ref, "response": stale})
                flat = scalars(read["values"])
                assert len(flat) == len(receipt["expectations"])
                report = verify_reopen(EngineReadback(observed), receipt, mph_path=mph,
                                       evaluator=lambda model, expr: flat[int(expr.split("_")[1])])
                report.update(fresh_model_ref=ref, observed_state=observed, point_readback=read,
                              evidence_level="LIVE_PUBLIC_MCP_STORED_SOLUTION", rebuilt_or_resolved=False)
                reports[chain] = report
                write_json(run / ("reopen_" + chain + ".json"), report)
        write_json(run / "gate_a_positive.json", reports)
        if set(args.chains) == {"a", "b", "c"}:
            await negatives(args, run, transcript, receipts, reports)
    finally:
        write_json(run / "transcript.json", transcript)


async def negatives(args, run, transcript, receipts, reports):
    from comsol_mcp._gate_a_reopen import verify_reopen, ReopenVerificationError
    checks = {}
    def expected_failure(name, model, receipt, mph, evaluator, expected):
        try:
            verify_reopen(model, receipt, mph_path=mph, evaluator=evaluator)
        except ReopenVerificationError as failure:
            checks[name] = {**failure.as_dict(), "expected_code": expected, "negative_control_pass": failure.code == expected}
            assert failure.code == expected, checks[name]
        else:
            raise AssertionError("Negative control unexpectedly passed: " + name)
    a = receipts["a"]
    model = EngineReadback(reports["a"]["observed_state"])
    flat = scalars(reports["a"]["point_readback"]["values"])
    evaluate = lambda m, expression: flat[int(expression.split("_")[1])]
    mph_a = run / "artifacts/chain_a.mph"
    expected_failure("wrong_hash", model, {**a, "model_sha256": "0" * 64}, mph_a, evaluate, "ARTIFACT_HASH_MISMATCH")
    expected_failure("wrong_artifact", model, a, run / "artifacts/chain_b.mph", evaluate, "ARTIFACT_HASH_MISMATCH")
    expected_failure("wrong_dataset", model, {**a, "dataset": "absent_dataset"}, mph_a, evaluate, "DATASET_NOT_FOUND")
    expected_failure("wrong_solution", model, {**a, "solution": "absent_solution"}, mph_a, evaluate, "SOLUTION_NOT_FOUND")
    mutations = {
        "cleared": ("a", 'model.sol("sol1").clearSolutionData();'),
        "modified_field": ("a", 'model.physics("ht").feature("temp1").set("T0", "303.15[K]"); model.sol("sol1").runAll();'),
        "missing_derived": ("c", 'model.result().numerical().remove("user_derived_probe");'),
    }
    changed = {}
    state = {}
    async with OwnedHost(args, run, transcript, label="negative-builder", private_home=run / "private/negative-builder", run_state=state) as host:
        client = protocol.ActionClient(host, args, state)
        await connect(host, "negative-builder-connect")
        for name, (chain, statement) in mutations.items():
            state.pop("ref", None)
            loaded, _ = await protocol._load_model(host, client, run / "artifacts" / ("chain_" + chain + ".mph"),
                                                  "negative-source-" + name, key="negative-load-" + name)
            assert protocol._success(loaded), loaded
            ref, revision = protocol._payload_execution(loaded)
            state.update(ref=ref, revision=revision)
            source = ('import com.comsol.model.*; import java.util.*; public final class NegativeMutation {'
                      'public static Object run(Model model, Map<String,Object> args) {' + statement +
                      'return Collections.singletonMap("mutation", args.get("name")); }}')
            class_name = "NegativeMutation" + "".join(part.title() for part in name.split("_"))
            await execute_java(client, run, class_name, source.replace("NegativeMutation", class_name), {"name": name})
            path = run / "artifacts" / (name + ".mph")
            saved = await client.action("save_model", {"path": str(path)}, reconcile=False)
            assert protocol._success(saved) and path.is_file(), saved
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            assert digest != receipts[chain]["model_sha256"]
            changed[name] = {"chain": chain, "path": str(path), "sha256": digest, "builder_ref": dict(ref)}
        write_json(run / "negative_artifacts.json", changed)
    state = {}
    async with OwnedHost(args, run, transcript, label="negative-reopen", private_home=run / "private/negative-reopen", run_state=state) as host:
        client = protocol.ActionClient(host, args, state)
        await connect(host, "negative-reopen-connect")
        for name, artifact in changed.items():
            state.pop("ref", None)
            path = Path(artifact["path"])
            loaded, _ = await protocol._load_model(host, client, path, "negative-" + name, key="negative-reopen-" + name)
            assert protocol._success(loaded), loaded
            ref, revision = protocol._payload_execution(loaded)
            state.update(ref=ref, revision=revision)
            assert ref["server_instance_id"] != artifact["builder_ref"]["server_instance_id"]
            observed = await execute_java(client, run, "SavedStateInspector", INSPECTOR,
                                          {"transient": artifact["chain"] != "a", "continuation": False})
            expected = {key: val for key, val in receipts[artifact["chain"]].items() if key != "solver_settings"}
            expected["model_sha256"] = artifact["sha256"]
            if name == "missing_derived":
                expected_failure(name, EngineReadback(observed), expected, path, None, "DERIVED_VALUES_MISSING")
            else:
                raw = await points(client, "a", validate=False)
                write_json(run / (name + "_read.json"), raw)
                if protocol._success(raw):
                    values = scalars(raw["data"]["values"])
                    evaluator = lambda m, expression: values[int(expression.split("_")[1])] if values else []
                else:
                    def evaluator(m, expression):
                        raise RuntimeError(str(raw.get("error")))
                    job = raw.get("execution", {}).get("job_id")
                    if job:
                        for operation in ("job_status", "job_result", "job_reconcile"):
                            reply = await host.call(operation, {"job_id": job, "execution": protocol._execution(key=name + operation)}, reconcile=False)
                            write_json(run / (name + "_" + operation + ".json"), reply)
                expected_failure(name, EngineReadback(observed), expected, path, evaluator,
                                 "SOLUTION_CLEARED_OR_EMPTY" if name == "cleared" else "STORED_VALUE_MISMATCH")
            checks[name].update(artifact_sha256=artifact["sha256"], reopened_model_ref=ref,
                                evidence_level="LIVE_PUBLIC_MCP_STORED_SOLUTION", readback_re_solved=False)
            write_json(run / "gate_a_negatives.json", checks)
        fault_source = ('import com.comsol.model.*; import java.util.*; public final class ControlledFault {'
                        'public static Object run(Model model, Map<String,Object> args) {'
                        'throw new RuntimeException("G33 intentional completed API fault"); }}')
        try:
            await execute_java(client, run, "ControlledFault", fault_source)
        except AssertionError as failure:
            payload = failure.args[0] if failure.args else None
            assert isinstance(payload, dict) and payload.get("error", {}).get("code") == "EXECUTION_STATE_UNKNOWN", payload
            write_json(run / "controlled_fault.json", {"status": "EXPECTED_UNKNOWN", "original": payload,
                       "scope": "real dispatched Java failure, no timeout or cancellation claim"})
        else:
            raise AssertionError("Controlled API fault unexpectedly succeeded")


async def numerical_suite(args, run):
    from g33_numeric_cases import run_numeric_cases
    transcript, state = [], {}
    try:
        async with OwnedHost(args, run, transcript, label="numerics", private_home=run / "private/numerics", run_state=state) as host:
            client = protocol.ActionClient(host, args, state)
            await connect(host, "numerics-connect")
            async def build(variable, class_name, label):
                state.pop("ref", None)
                reply = await protocol._create_empty_model(client, state, label, host)
                assert protocol._success(reply), reply
                source = builder_source("chain_b_code" if variable == "budget_transient" else
                                        "chain_a_code" if variable in {"chain_a_mm", "c05_1d"} else variable)
                if variable == "budget_transient":
                    assert 'range(0, 0.5, 2.0)' in source
                    source = source.replace('range(0, 0.5, 2.0)', 'range(0, 0.1, 1.4)')
                if variable == "chain_a_mm":
                    source = source.replace('model.geom("geom1").create("r1", "Rectangle");',
                        'model.geom("geom1").lengthUnit("mm"); model.geom("geom1").create("r1", "Rectangle");').replace(
                        'new String[]{"0.05", "0.01"}', 'new String[]{"50", "10"}')
                if variable == "c05_1d":
                    source = source.replace('"comp1"', '"body"').replace('"geom1"', '"lineg"')
                    source = source.replace('create("lineg", 2)', 'create("lineg", 1)').replace('"Rectangle"', '"Interval"')
                    source = source.replace('set("size", new String[]{"0.05", "0.01"})', 'set("p", "0 2")')
                    source = source.replace('"TemperatureBoundary", 1', '"TemperatureBoundary", 0').replace('new int[]{4}', 'new int[]{2}')
                return await execute_java(client, run, class_name, source)
            if args.study_fault_only:
                await build("chain_b_code", "ChainBBuilder", "native-study-fault-fixture")
                await study_fault_case(client, run)
            elif args.cutplane_only:
                from g33_node_probe_cases import run_cutplane_cases
                await build("c04_code", "C04Builder", "cutplane-fixture")
                client.cutplane_fixture = {"fixture_ready": True, "dataset": "dset1", "component": "comp1", "geometry": "geom1"}
                report = await run_cutplane_cases(client, run / "cutplane")
                assert report["overall"] == "PASS", report["summary"]
            elif args.control_only:
                from g33_control_cases import run_control_cases
                await build("chain_b_code", "ChainBBuilder", "control-plane-fixture")
                report = await run_control_cases(client, run / "control")
                if report.get("needs_retained_runtime"):
                    host.retain_runtime = args.retain_runtime = True
                    write_json(run / "runtime_retained.json", {"reason": "original solve not proven quiescent", "report": report})
                    raise RuntimeError("Active original solve retained for follow-up; no owned process terminated")
                assert report["overall"] == "PASS", report
            elif args.probe_transient_only:
                from g33_node_probe_cases import run_transient_probe_case
                await build("chain_b_code", "ChainBBuilder", "transient-probe-fixture")
                client.transient_probe_fixture = {"dataset": "dset1", "study": "std1"}
                report = await run_transient_probe_case(client, run / "transient_probe")
                assert report["overall"] == "PASS", report["summary"]
            elif args.nodes_only or args.probe_only:
                from g33_node_probe_cases import run_node_probe_cases
                await build("chain_a_code", "ChainABuilder", "nodes-probes-fixture")
                report = await run_node_probe_cases(client, run / "nodes", execute_java=None, include_c11=not args.probe_only)
                assert report["overall"] == "PASS", report["summary"]
            elif args.export_only:
                from g33_export_cases import run_export_cases
                await build("budget_transient" if args.export_transient_budget else "chain_a_code",
                            "ChainBBuilder" if args.export_transient_budget else "ChainABuilder", "export-fixture")
                await run_export_cases(client, run)
                export_report = json.loads((run / "g33_export_cases.json").read_text())
                assert not any(export_report["summary"].get(key, 0) for key in ("FAIL", "BLOCKED")), export_report["summary"]
                assert export_report["summary"].get("PASS", 0) > 0, export_report["summary"]
            else:
                await run_numeric_cases(client, run / "numerics", build=build,
                                        execute_java=lambda name, source, arguments=None: execute_java(client, run, name, source, arguments), m1_only=args.m1_only)
    finally:
        write_json(run / "transcript.json", transcript)


async def axis_probe(args, run):
    """Exploratory native API observations, not a passing numerical gate."""
    transcript, state = [], {}
    try:
        async with OwnedHost(args, run, transcript, label="axis-probe", private_home=run / "private/axis-probe", run_state=state) as host:
            client = protocol.ActionClient(host, args, state)
            await connect(host, "axis-probe-connect")
            for chain in args.chains:
                state.pop("ref", None)
                created = await protocol._create_empty_model(client, state, "AxisProbe" + chain, host)
                assert protocol._success(created), created
                fixture = builder_source("chain_" + chain + "_code")
                if args.outer_probe:
                    fixture = fixture.replace('model.modelNode().create("comp1");', 'model.param().set("p1", "1"); model.param().set("p2", "3"); model.modelNode().create("comp1");')
                    fixture = fixture.replace('"353.15[K]"', '"293.15[K]+p1*10[K]+p2*1[K]"')
                    fixture = fixture.replace('model.study("std1").run();',
                        'model.study("std1").create("param", "Parametric");' +
                        'model.study("std1").feature("param").set("pname", new String[]{"p1","p2"});' +
                        'model.study("std1").feature("param").set("plistarr", new String[]{"1 2","3 4"});' +
                        'model.study("std1").feature("param").set("punit", new String[]{"",""});' +
                        'model.study("std1").feature("param").set("sweeptype", "filled");' +
                        'model.study("std1").run();')
                await execute_java(client, run, "Chain" + chain.upper() + "Builder", fixture)
                expressions = {
                    "outer": "info.getOuterSolnum()", "pairs_empty_filter": "info.getSolnums(new String[]{})",
                    "pairs_t_filter": 'info.getSolnums(new String[]{"t"})',
                    "inner_1_strict": "info.getSolnum(1, true)", "inner_1_nonstrict": "info.getSolnum(1, false)",
                    "inner_0_strict": "info.getSolnum(0, true)", "levels": "info.getLevelNames()",
                    "pnames_11": "info.getPNames(new int[][]{{1,1}})", "pvals_11": "info.getPvals(new int[][]{{1,1}})",
                    "units_11": "info.getUnits(new int[][]{{1,1}})", "solver_pnames": 'model.sol("sol1").getPNames()',
                    "solver_pvals": 'sequence.getPVals()', "solver_pvals_1": 'sequence.getPVals(1)',
                    "solver_type": "sequence.getType()", "info_unit_t": 'info.getUnit("t")',
                    "info_sol_1": "info.getSol(1)", "info_solver_1": "info.getSolverSequence(1)",
                }
                statements = []
                for name, expression in expressions.items():
                    expression = expression.replace('model.sol("sol1")', "sequence")
                    statements.append('try { out.put("' + name + '", ' + expression + '); } catch(Exception e) { out.put("' + name + '_ERROR", e.toString()); }')
                source = ('import com.comsol.model.*; import java.util.*; public final class AxisProbe {'
                    'public static Object run(Model model, Map<String,Object> args) {'
                    'Map<String,Object> result=new LinkedHashMap<>();' +
                    'Map<String,Object> datasets=new LinkedHashMap<>(); for(String dt:model.result().dataset().tags()) {' +
                    'Map<String,Object> d=new LinkedHashMap<>(); d.put("type",model.result().dataset(dt).getType());' +
                    'try { d.put("solution",model.result().dataset(dt).getString("solution")); } catch(Exception e) {} datasets.put(dt,d); }' +
                    'result.put("datasets",datasets); for(String tag:model.sol().tags()) {' +
                    'Map<String,Object> out=new LinkedHashMap<>(); SolverSequence sequence=model.sol(tag); SolutionInfo info=sequence.getSolutioninfo();' +
                    ''.join(statements) + 'result.put(tag,out); } return result; }}')
                result = await execute_java(client, run, "AxisProbe", source)
                write_json(run / ("native_axis_" + chain + ".json"), result)
                if args.outer_probe:
                    aggregate_source = """
import com.comsol.model.*; import java.util.*;
public final class OuterAggregateProbe {
 private static double[][] copy(double[][] a) { if(a==null)return null; double[][] b=new double[a.length][]; for(int k=0;k<a.length;k++)b[k]=a[k]==null?null:a[k].clone(); return b; }
 public static Object run(Model model, Map<String,Object> args) {
  Map<String,Object> out=new LinkedHashMap<>();
  for(String kind:new String[]{"AvSurface","IntSurface"}) {
   String tag="outeragg"+kind; NumericalFeature n=model.result().numerical().create(tag,kind);
   Map<String,Object> row=new LinkedHashMap<>();
   try {
    n.set("data","dset2"); n.selection().all(); n.set("expr",new String[]{"100*p1+10*p2+t/1[s]+x/1[m]","(100*p1+10*p2+t/1[s]+x/1[m])*i"});
    for(int outer=1;outer<=4;outer++) {
     Map<String,Object> vals=new LinkedHashMap<>();
     n.set("outersolnum",outer); n.run();
     vals.put("explicit_is_complex",n.isComplex(outer)); vals.put("default_real",copy(n.getReal())); vals.put("default_imag",copy(n.getImag()));
     vals.put("explicit_real",copy(n.getReal(false,outer))); vals.put("explicit_imag",copy(n.getImag(outer)));
     row.put(Integer.toString(outer),vals);
    }
   } catch(Exception e) { row.put("error",e.toString()); }
   finally {model.result().numerical().remove(tag);}
   out.put(kind,row);
  }
  return out;
 }
}
"""
                    result = await execute_java(client, run, "OuterAggregateProbe", aggregate_source)
                    write_json(run / ("native_outer_aggregate_" + chain + ".json"), result)

    finally:
        write_json(run / "transcript.json", transcript)


async def coordinate_probe(args, run):
    """Native coordinate convention probe on a millimeter geometry."""
    transcript, state = [], {}
    try:
        async with OwnedHost(args, run, transcript, label="coordinate-probe", private_home=run / "private/coordinate-probe", run_state=state) as host:
            client = protocol.ActionClient(host, args, state)
            await connect(host, "coordinate-probe-connect")
            created = await protocol._create_empty_model(client, state, "CoordinateUnits", host)
            assert protocol._success(created), created
            fixture = builder_source("chain_a_code").replace('model.geom("geom1").create("r1", "Rectangle");',
                'model.geom("geom1").lengthUnit("mm"); model.geom("geom1").create("r1", "Rectangle");').replace(
                'new String[]{"0.05", "0.01"}', 'new String[]{"50", "10"}')
            await execute_java(client, run, "ChainABuilder", fixture)
            source = """
import com.comsol.model.*; import java.util.*;
public final class CoordinateProbe {
 public static Object run(Model model, Map<String,Object> args) {
  Map<String,Object> out=new LinkedHashMap<>();
  out.put("geometry_unit",model.geom("geom1").lengthUnit());
  for(int mode=0;mode<2;mode++) {
   String tag="coordprobe"+mode;
   NumericalFeature n=model.result().numerical().create(tag,"Interp");
   Map<String,Object> row=new LinkedHashMap<>();
   try {
    n.set("data","dset1"); n.set("expr",new String[]{"x","y","T"});
    double scale=mode==0?1:1000;
    n.set("coord",new double[][]{{.0125*scale,.025*scale,.0375*scale},{.005*scale,.005*scale,.005*scale}});
    row.put("data",n.getData()); row.put("coordinates",n.getCoordinates());
    row.put("units",n.getStringArray("unit")); row.put("expressions",n.getStringArray("expr"));
   } catch(Exception e) { row.put("error",e.toString()); }
   finally { model.result().numerical().remove(tag); }
   out.put(mode==0?"SI_input":"geometry_input",row);
  }
  return out;
 }
}
"""
            result = await execute_java(client, run, "CoordinateProbe", source)
            write_json(run / "native_coordinates.json", result)
    finally:
        write_json(run / "transcript.json", transcript)


async def measure_probe(args, run):
    transcript, state = [], {}
    try:
        async with OwnedHost(args, run, transcript, label="measure-probe", private_home=run / "private/measure-probe", run_state=state) as host:
            client = protocol.ActionClient(host, args, state)
            await connect(host, "measure-probe-connect")
            created = await protocol._create_empty_model(client, state, "MeasureTypes", host)
            assert protocol._success(created), created
            await execute_java(client, run, "C04Builder", builder_source("c04_code"))
            source = """
import com.comsol.model.*; import java.util.*;
public final class MeasureProbe {
 public static Object run(Model model, Map<String,Object> args) {
  Map<String,Object> out=new LinkedHashMap<>();
  String[] types={"IntPoint","AvPoint","EvalPoint","MaxPoint","MinPoint","IntLine","IntSurface","IntVolume"};
  int[] dims={0,0,0,0,0,1,2,3};
  for(int k=0;k<types.length;k++) {
   String tag="measureprobe"+k; Map<String,Object> row=new LinkedHashMap<>();
   try {
    NumericalFeature n=model.result().numerical().create(tag,types[k]);
    row.put("created_type",n.getType()); n.set("data","dset1"); n.set("expr",new String[]{"2","1"});
    n.selection().geom(dims[k]); n.selection().all();
    row.put("real",n.getReal()); row.put("data",n.getData());
    row.put("coordinates",n.getCoordinates()); row.put("units",n.getStringArray("unit"));
   } catch(Exception e) { row.put("error",e.toString()); }
   finally { if(Arrays.asList(model.result().numerical().tags()).contains(tag)) model.result().numerical().remove(tag); }
   out.put(types[k],row);
  }
  return out;
 }
}
"""
            result = await execute_java(client, run, "MeasureProbe", source)
            write_json(run / "native_measure_types.json", result)
    finally:
        write_json(run / "transcript.json", transcript)


async def join_probe(args, run):
    transcript, state = [], {}
    try:
        async with OwnedHost(args, run, transcript, label="join-probe", private_home=run / "private/join-probe", run_state=state) as host:
            client = protocol.ActionClient(host, args, state)
            await connect(host, "join-probe-connect")
            created = await protocol._create_empty_model(client, state, "JoinFeatureTypes", host)
            assert protocol._success(created), created
            await execute_java(client, run, "ChainABuilder", builder_source("chain_a_code"))
            source = """
import com.comsol.model.*; import java.util.*;
public final class JoinProbe {
 public static Object run(Model model, Map<String,Object> args) {
  Map<String,Object> out=new LinkedHashMap<>();
  DatasetFeature join=model.result().dataset().create("joinprobe","Join");
  join.set("data","dset1"); join.set("data2","dset1"); join.set("method","difference");
  join.set("solutions","all"); join.set("solutions2","all");
  String[] types={"Eval","AvSurface","IntSurface","EvalPoint","Interp"};
  for(int k=0;k<types.length;k++) {
   String tag="joinnum"+k; Map<String,Object> row=new LinkedHashMap<>(); String step="create";
   try {
    NumericalFeature n=model.result().numerical().create(tag,types[k]);
    step="setData"; n.set("data","joinprobe"); step="setExpr"; n.set("expr",new String[]{"T"});
    step="selection";
    if(types[k].equals("Interp")) n.set("coord",new double[][]{{.0125,.025,.0375},{.005,.005,.005}});
    else { n.selection().geom(types[k].equals("EvalPoint")?0:2); n.selection().all(); }
    step="read";
    if(types[k].equals("Interp")||types[k].equals("Eval")) row.put("values",n.getData());
    else row.put("values",n.getReal());
    row.put("units",n.getStringArray("unit")); row.put("status","READ");
   } catch(Exception e) { row.put("error",e.toString()); row.put("failed_step",step); }
   finally { if(Arrays.asList(model.result().numerical().tags()).contains(tag)) model.result().numerical().remove(tag); }
   out.put(types[k],row);
  }
  model.result().dataset().remove("joinprobe"); return out;
 }
}
"""
            result = await execute_java(client, run, "JoinProbe", source)
            write_json(run / "native_join_features.json", result)
    finally:
        write_json(run / "transcript.json", transcript)


async def shape_probe(args, run):
    transcript, state = [], {}
    try:
        async with OwnedHost(args, run, transcript, label="shape-probe", private_home=run / "private/shape-probe", run_state=state) as host:
            client = protocol.ActionClient(host, args, state)
            await connect(host, "shape-probe-connect")
            created = await protocol._create_empty_model(client, state, "PreDataShape", host)
            assert protocol._success(created), created
            await execute_java(client, run, "ChainABuilder", builder_source("chain_a_code"))
            source = """
import com.comsol.model.*; import java.util.*;
public final class ShapeProbe {
 public static Object run(Model model, Map<String,Object> args) {
  Map<String,Object> out=new LinkedHashMap<>();
  NumericalFeature n=model.result().numerical().create("shapeprobe","Eval");
  try {
   n.set("data","dset1"); n.set("expr",new String[]{"T","ht.tfluxx"});
   double[][] coords=n.getCoordinates();
   int[] lengths=new int[coords.length]; for(int j=0;j<coords.length;j++) lengths[j]=coords[j].length;
   out.put("before_getData_coordinate_rows",coords.length); out.put("before_getData_coordinate_row_lengths",lengths);
   out.put("nData",n.getNData());
   double[][][] data=n.getData();
   out.put("after_getData_shape",new int[]{data.length,data[0].length,data[0][0].length});
   out.put("shape_matches",coords.length==2 && coords[0].length==data[0][0].length && coords[1].length==coords[0].length);
   out.put("ordering","getCoordinates then getNData then getData; no run/getData before coordinate shape");
   return out;
  } finally { model.result().numerical().remove("shapeprobe"); }
 }
}
"""
            result = await execute_java(client, run, "ShapeProbe", source)
            write_json(run / "native_predata_shape.json", result)
            assert result["shape_matches"] is True, result
    finally:
        write_json(run / "transcript.json", transcript)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--axis-probe-only", action="store_true")
    parser.add_argument("--coordinate-probe-only", action="store_true")
    parser.add_argument("--measure-probe-only", action="store_true")
    parser.add_argument("--join-probe-only", action="store_true")
    parser.add_argument("--shape-probe-only", action="store_true")
    parser.add_argument("--outer-probe", action="store_true")
    parser.add_argument("--numeric-only", action="store_true")
    parser.add_argument("--m1-only", action="store_true")
    parser.add_argument("--export-only", action="store_true")
    parser.add_argument("--export-transient-budget", action="store_true",
                        help="With --export-only, use 15 stored time steps on the same bounded mesh")
    parser.add_argument("--nodes-only", action="store_true")
    parser.add_argument("--probe-only", action="store_true")
    parser.add_argument("--probe-transient-only", action="store_true")
    parser.add_argument("--control-only", action="store_true")
    parser.add_argument("--study-fault-only", action="store_true")
    parser.add_argument("--cutplane-only", action="store_true")
    parser.add_argument("--chains", nargs="+", choices=["a", "b", "c"], default=["a", "b", "c"])
    args = parser.parse_args()
    if args.export_transient_budget and not args.export_only:
        parser.error("--export-transient-budget requires --export-only")
    run = args.run_dir.resolve()
    run.relative_to(ROOT)
    run.mkdir(parents=True, exist_ok=False)
    paths = sorted(path for directory in ("comsol_mcp", "tools", "tests") for path in (ROOT / directory).rglob("*")
                   if path.is_file() and "__pycache__" not in path.parts and path.suffix in {".py", ".java", ".json"})
    write_json(run / "source_manifest.json", {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "tree": subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT, text=True).strip(),
        "dirty": subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True),
        "interpreter": sys.executable, "cwd": str(Path.cwd()), "command": sys.argv,
        "files": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}})
    runner = AcceptanceRunner(run)
    started = time.monotonic()
    status = {"status": "FAIL", "started_at": datetime.now(timezone.utc).isoformat(),
              "scope": "API_PROBE" if args.axis_probe_only or args.coordinate_probe_only or args.measure_probe_only or args.join_probe_only or args.shape_probe_only else "LIVE_PUBLIC_MCP_DEVELOPMENT_ACCEPTANCE",
              "cases_requested": (["C04"] if args.m1_only else ["C04", "C05", "C06", "C07", "C08", "C09", "C10"] if args.numeric_only else ["C12", "C13"] if args.export_only else ["C14-transient"] if args.probe_transient_only else ["C14"] if args.probe_only else ["C11", "C14"] if args.nodes_only else ["C02-native-study-fault"] if args.study_fault_only else ["C15"] if args.control_only else ["C11-CutPlane"] if args.cutplane_only else args.chains), "exit_code": 1}
    try:
        args.port = runner.start_isolated_server()
        isolation.PORT = args.port
        isolation.COMSOL_ROOT = COMSOL_ROOT
        isolation.JDK_HOME = JDK_HOME
        try:
            proof = isolation._probe_api(run, runner.server_proc.pid)
        except isolation.ProbeFailure as failure:
            write_json(run / "isolation_probe_failure.json", failure.proof)
            raise
        write_json(run / "isolation_probe.json", proof)
        process = isolation.base._process_snapshot(runner.server_proc.pid)
        process.pop("command", None)
        process["port"] = args.port
        config = isolation.SERVER_XML
        receipt = {"schema_version": 2, "status": "RUNNING", "isolation_mode": "remote_addr_valve",
                   "process": process, "config": {"target": str(config), "target_inode": config.stat().st_ino,
                   "content_sha256_after_apply": hashlib.sha256(config.read_bytes()).hexdigest(),
                   "action_this_run": "READ_ONLY_EXISTING_CONFIGURATION"}, "probe": proof,
                   "paths": {"run_dir": str(run), "prefs": str(run / "prefs")}}
        receipt_path = run / "isolation_receipt.json"
        write_json(receipt_path, receipt)
        os.environ["COMSOL_MCP_ISOLATION_RECEIPT"] = str(receipt_path)
        os.environ["COMSOL_MCP_TRUSTED_CODE"] = "1"
        args.host, args.python, args.project_id = "127.0.0.1", sys.executable, "g33-" + run.name
        args.comsol_root, args.jdk11, args.prefs = COMSOL_ROOT, JDK_HOME, run / "prefs"
        args.private_home, args.tool_profile = None, "full"
        asyncio.run(shape_probe(args, run) if args.shape_probe_only else
                    join_probe(args, run) if args.join_probe_only else
                    measure_probe(args, run) if args.measure_probe_only else
                    coordinate_probe(args, run) if args.coordinate_probe_only else
                    axis_probe(args, run) if args.axis_probe_only else
                    numerical_suite(args, run) if args.numeric_only or args.export_only or args.m1_only or args.nodes_only or args.probe_only or args.probe_transient_only or args.control_only or args.study_fault_only or args.cutplane_only else exercise(args, run))
        status.update(status="PASS", exit_code=0)
    except BaseException as error:
        status.update(error_type=type(error).__name__, error=str(error)[:4000])
        raise
    finally:
        try:
            if not getattr(args, "retain_runtime", False):
                runner.stop_server()
            else:
                write_json(run / "server_retained.json", {"pid": runner.server_proc.pid, "port": args.port, "reason": "active original job"})
        finally:
            initial = json.loads((run / "source_manifest.json").read_text())["files"]
            final_paths = {str(path.relative_to(ROOT)): path for directory in ("comsol_mcp", "tools", "tests")
                           for path in (ROOT / directory).rglob("*") if path.is_file()
                           and "__pycache__" not in path.parts and path.suffix in {".py", ".java", ".json"}}
            final_hashes = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in final_paths.items()}
            changed = sorted(name for name in set(initial) | set(final_hashes)
                             if initial.get(name) != final_hashes.get(name))
            write_json(run / "final_source_files.json", final_hashes)
            status.update(finished_at=datetime.now(timezone.utc).isoformat(), elapsed_seconds=time.monotonic() - started,
                          changed_source_files=changed, final_source_acceptance_eligible=not changed)
            if changed and status["status"] == "PASS":
                status["status"] = "OBSERVATIONS_ONLY_SOURCE_CHANGED"
            write_json(run / "run_status.json", status)


if __name__ == "__main__":
    main()
