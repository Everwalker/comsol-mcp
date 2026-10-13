"""Software controls for the optional public client; no native dispatch."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("p0_client", Path(__file__).with_name("run_v3_public_mcp_smoke.py"))
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


def test_actual_production_csv_parser_accepts_client_modified_export():
    from comsol_mcp._g3_parameter_io import _serialize, _parse_csv
    raw = _serialize("csv", ["default"], [{"group": "default", "name": "v3_marker", "expression": "2.5[mm]",
        "description": "V3 public MCP native smoke", "unit": "m", "unit_status": "VERIFIED"}])
    changed = client.changed_csv(raw.decode())
    groups, rows = _parse_csv(changed.encode())
    assert rows[0]["expression"] == "5.5[mm]"
    assert rows[0]["description"] == "V3 CSV modified, quoted roundtrip"
    assert rows[0]["unit"] == "m"


@pytest.mark.parametrize("raw", ["name,expression\nv3_marker,2.5[mm]\n", "group,name,expression,description,unit,unit_status\n"])
def test_modified_csv_rejects_wrong_or_empty_table(raw):
    with pytest.raises(RuntimeError):
        client.changed_csv(raw)


def test_artifact_bytes_are_bound_to_content_addressed_registration(tmp_path):
    path = tmp_path / "source.csv"
    path.write_bytes(b"test")
    record = {"path": path.name, "size": 4, "sha256": client.digest(path), "artifact_id": client.digest(path)}
    assert client.verify_artifact(tmp_path, record) == path
    path.write_bytes(b"evil")
    with pytest.raises(RuntimeError):
        client.verify_artifact(tmp_path, record)


@pytest.mark.parametrize("path", ["../other", "/private/tmp/other"])
def test_artifact_rejects_path_escape(tmp_path, path):
    with pytest.raises(RuntimeError):
        client.verify_artifact(tmp_path, {"path": path})


def producer():
    attach = {"worker_instance_id": "w1", "worker_epoch": 2, "server_instance_id": "s1", "endpoint": {"host": "127.0.0.1", "port": 12}}
    ref = {"session_id": "session1", "server_instance_id": "s1"}
    identity = {"runtime_id": "install1", "worker_instance_id": "w1", "connection_epoch": 2, "server_instance_id": "s1", "endpoint": "127.0.0.1:12"}
    observation = {"schema": "comsol-worker-runtime-observation/v1", "status": "OBSERVED", "comsol_version": "6.4.0.293", "runtime_identity": identity, "installation_runtime_id": "install1"}
    value = {"execution": {"model_ref": ref, "runtime_observation": observation}, "data": {"source_sha256": "abc", "readback": {"readback": {"name": "v3_marker", "expression": "2.5[mm]", "value": 0.0025, "unit": "m"}}}}
    return value, attach, ref


def test_client_checks_producer_and_java_getter():
    value, attach, ref = producer()
    assert client.verify_runtime_producer(value, attach, "install1", ref, "abc", "2.5[mm]", 0.0025)["status"] == "OBSERVED"


@pytest.mark.parametrize("field,wrong", [("worker_instance_id", "other"), ("connection_epoch", 3), ("connection_epoch", 2.0), ("endpoint", "127.0.0.1:13"), ("runtime_id", "worker1")])
def test_client_rejects_mismatched_owned_runtime(field, wrong):
    value, attach, ref = producer()
    value["execution"]["runtime_observation"]["runtime_identity"][field] = wrong
    with pytest.raises(RuntimeError):
        client.verify_runtime_producer(value, attach, "install1", ref, "abc", "2.5[mm]", 0.0025)


def test_client_never_substitutes_session_version_for_producer():
    value, attach, ref = producer()
    value["execution"]["runtime_observation"]["status"] = "UNVERIFIED"
    attach["remote_engine_version"] = "6.4.0.293"
    with pytest.raises(RuntimeError):
        client.verify_runtime_producer(value, attach, "install1", ref, "abc", "2.5[mm]", 0.0025)
