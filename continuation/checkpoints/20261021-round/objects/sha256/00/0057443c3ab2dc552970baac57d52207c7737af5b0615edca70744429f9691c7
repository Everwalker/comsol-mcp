"""Replay the captured R11 public responses offline; synthetic saves are non-scientific fixtures."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from tools.t044_thermal_benchmark import run_cycle

CANDIDATE = Path(__file__).resolve().parents[1]
ACTUAL = CANDIDATE / "_state/t044-thermal-r13-freeze-consumer-0b8171f9-20261019/evidence" / "actual_r11"
RAW_BY_SUMMARY_INDEX = {
    7: "8-artifact.register.json",
    8: "9-code.execute_java.json",
    9: "10-parameter.get.json",
    10: "11-geometry.inspect.json",
    11: "12-study.inspect.json",
    12: "13-study.run.json",
    13: "14-dataset.list.json",
    14: "15-dataset.inspect.json",
    15: "16-result.at_points.json",
    17: "18-model.create.json",
    18: "19-code.execute_java.json",
    19: "20-parameter.get.json",
    20: "21-geometry.inspect.json",
    21: "22-study.inspect.json",
    22: "23-study.run.json",
    23: "24-dataset.list.json",
    24: "25-dataset.inspect.json",
    25: "26-result.at_points.json",
}
SYNTHETIC_CLASSIFICATION = "SYNTHETIC_TEST_ONLY_NOT_COMSOL_MPH_NOT_SCIENTIFIC_ACCEPTANCE"


def _actual():
    summary = json.loads((ACTUAL / "SUMMARY.json").read_text(encoding="utf-8"))
    responses = {
        index: json.loads((ACTUAL / filename).read_text(encoding="utf-8"))["structuredContent"]
        for index, filename in RAW_BY_SUMMARY_INDEX.items()
    }
    return summary, responses


def _synthetic_execution(*, summary, model_ref, revision, label):
    identity = lambda suffix: str(uuid5(NAMESPACE_URL, f"t044-r12-{label}-{suffix}"))
    request_id = identity("request")
    return {
        "project_id": summary["calls"][6]["execution"]["project_id"],
        "session_id": summary["calls"][6]["execution"]["session_id"],
        "model_ref": deepcopy(model_ref),
        "revision": revision,
        "operation_id": identity("operation"),
        "job_id": identity("job"),
        "request_id": request_id,
        "idempotency_key": request_id,
        "request_hash": hashlib.sha256(label.encode("utf-8")).hexdigest(),
    }


def test_captured_r11_model_inspect_failure_is_terminal_and_not_replayed():
    failure = json.loads((ACTUAL / "27-model.inspect.json").read_text(encoding="utf-8"))["structuredContent"]
    assert failure["success"] is False
    assert failure["error"]["code"] == "INVALID_REQUEST"
    assert failure["error"]["safe_retry"] is False
    assert "identity belongs in the outer execution envelope" in failure["error"]["message"]
    # This control uses the preserved capture only as data; no API call is issued.


def test_actual_r11_two_mesh_raw_replay_reaches_final_save_with_explicit_synthetic_save():
    source_summary, raw = _actual()
    project_id = source_summary["calls"][6]["execution"]["project_id"]
    session_id = source_summary["calls"][6]["execution"]["session_id"]
    model_ref = deepcopy(source_summary["calls"][6]["execution"]["model_ref"])
    attached = source_summary["actual_attach"]
    runtime_id = raw[8]["execution"]["runtime_observation"]["installation_runtime_id"]
    fixture = (ACTUAL / "T044ThermalFixture.java").read_bytes()
    summary = {"calls": deepcopy(source_summary["calls"][:7]),
               "test_fixture_scope": SYNTHETIC_CLASSIFICATION}
    received = []
    synthetic = []
    inspect_envelopes = []

    def digest(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    async def call(operation, body, *, ref=None, revision=None, cleanup=False):
        index = len(summary["calls"])
        received.append((index, operation))
        if index in RAW_BY_SUMMARY_INDEX:
            expected = source_summary["calls"][index]
            assert expected["operation"] == operation
            summary["calls"].append(deepcopy(expected))
            return deepcopy(raw[index])
        if operation == "model.save":
            assert index in {16, 27}
            assert isinstance(body, dict) and body.get("destination") in {"t044-coarse.mph", "t044-fine.mph"}
            assert body.get("include_solution") is True
            destination = Path(summary["test_workspace"]) / body["destination"]
            destination.write_bytes(
                (SYNTHETIC_CLASSIFICATION + ":" + body["destination"]).encode("ascii")
            )
            model_for_save = ref
            label = "coarse-save" if index == 16 else "fine-final-save"
            execution = _synthetic_execution(
                summary=source_summary, model_ref=model_for_save,
                revision=(revision + 1) if type(revision) is int else 3, label=label,
            )
            data = {
                "destination": destination.name,
                "sha256": digest(destination),
                "size_bytes": destination.stat().st_size,
                "fixture_classification": SYNTHETIC_CLASSIFICATION,
            }
            summary["calls"].append({
                "operation": operation, "request_id": execution["request_id"],
                "state": "SYNTHETIC_TEST_ONLY", "success": True, "isError": False,
                "execution": execution, "error": None,
                "fixture_classification": SYNTHETIC_CLASSIFICATION,
            })
            synthetic.append({"operation": operation, "index": index, "destination": destination.name,
                              "classification": SYNTHETIC_CLASSIFICATION})
            return {"success": True, "error": None, "execution": execution, "data": data}
        if operation == "model.inspect":
            assert index == 26
            inspect_envelopes.append({"body": deepcopy(body), "outer_ref": deepcopy(ref),
                                      "revision_argument": revision})
            assert body == {}
            assert ref == source_summary["calls"][17]["execution"]["model_ref"]
            execution = _synthetic_execution(
                summary=source_summary, model_ref=ref, revision=2, label="final-model-inspect-readback",
            )
            summary["calls"].append({
                "operation": operation, "request_id": execution["request_id"],
                "state": "SYNTHETIC_TEST_ONLY", "success": True, "isError": False,
                "execution": execution, "error": None,
                "fixture_classification": SYNTHETIC_CLASSIFICATION,
            })
            synthetic.append({"operation": operation, "index": index,
                              "classification": SYNTHETIC_CLASSIFICATION})
            return {"success": True, "error": None, "execution": execution,
                    "data": {"model_ref": deepcopy(ref),
                             "fixture_classification": SYNTHETIC_CLASSIFICATION}}
        raise AssertionError(f"unexpected or unmapped replay call {index}: {operation}")

    def run(workspace):
        summary["test_workspace"] = str(workspace)
        return asyncio.run(run_cycle(
            call, workspace=workspace, project_id=project_id, session_id=session_id,
            model_ref=model_ref, revision=source_summary["calls"][6]["execution"]["revision"],
            fixture=fixture, summary=summary, verify_artifact=lambda *_: None,
            digest=digest, runtime_id=runtime_id, attached=attached,
        ))

    # Imported by pytest's tmp_path fixture; keep the temporary directory scoped to this test.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="t044-r12-replay-") as work:
        final_ref, final_revision = run(Path(work))

    assert [op for _, op in received].count("study.run") == 2
    assert [op for _, op in received].count("result.at_points") == 2
    assert inspect_envelopes == [{
        "body": {},
        "outer_ref": source_summary["calls"][17]["execution"]["model_ref"],
        "revision_argument": None,
    }]
    assert synthetic == [
        {"operation": "model.save", "index": 16, "destination": "t044-coarse.mph",
         "classification": SYNTHETIC_CLASSIFICATION},
        {"operation": "model.inspect", "index": 26, "classification": SYNTHETIC_CLASSIFICATION},
        {"operation": "model.save", "index": 27, "destination": "t044-fine.mph",
         "classification": SYNTHETIC_CLASSIFICATION},
    ]
    assert final_ref == source_summary["calls"][17]["execution"]["model_ref"]
    assert final_revision == 3
    assert [sample["elements"] for sample in summary["t044_thermal"]["samples"]] == [20, 40]
    assert summary["t044_thermal"]["study_run_calls"] == 2
    assert summary["model_readback"]["fixture_classification"] == SYNTHETIC_CLASSIFICATION
    assert summary["saved_model"]["fixture_classification"] == SYNTHETIC_CLASSIFICATION
    assert summary["saved_artifact_hash_verified"] is True
    assert summary["test_fixture_scope"] == SYNTHETIC_CLASSIFICATION
    assert summary["calls"][-3]["operation"] == "result.at_points"
    assert summary["calls"][-2]["operation"] == "model.inspect"
    assert summary["calls"][-1]["operation"] == "model.save"
