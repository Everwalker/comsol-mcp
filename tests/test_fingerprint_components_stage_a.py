"""Stage A software-only controls for the internal V1 fingerprint witness."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import re
import sys

import pytest

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT))

from comsol_mcp._execution_contract import ExecutionContractError, ModelRef, SessionLedger  # noqa: E402
from comsol_mcp._execution_service import ExecutionService  # noqa: E402


def _java_sort_key(value: str) -> bytes:
    return value.encode("utf-16-be", errors="strict")


def _digest(components: dict) -> str:
    parameters = components["parameters"]
    source = (
        components["tag"] + "\x00" + components["label"] + "\x00"
        + components["file_path"] + "\x00"
        + str(components["time_modified_epoch_ms"]) + "\x00"
        + "".join(f"{row['name']}={row['expression']}\x00" for row in parameters)
    )
    return hashlib.sha256(source.encode("utf-8", errors="strict")).hexdigest()


def _components(parameters=None):
    pairs = list(parameters if parameters is not None else [("alpha", "0.5"), ("zeta", "2[mm]")])
    pairs.sort(key=lambda item: _java_sort_key(item[0]))
    return {
        "schema_version": 1,
        "tag": "m",
        "label": "owned blank",
        "file_path": "/private/tmp/owned-blank.mph",
        "time_modified_epoch_ms": 1791969237281,
        "parameters": [{"name": name, "expression": expression} for name, expression in pairs],
    }


class Adapter:
    def __init__(self, *, components=None, include_components=True, fingerprint=None):
        self.components = copy.deepcopy(components if components is not None else _components())
        self.include_components = include_components
        self.fingerprint = fingerprint or _digest(self.components)
        self.counter = 0

    def model_snapshot(self, tag):
        result = {
            "model_tag": tag,
            "server_instance_id": "srv",
            "fingerprint": self.fingerprint,
            "external_event_counter": self.counter,
        }
        if self.include_components:
            result["fingerprint_components"] = copy.deepcopy(self.components)
        return result


def _service(tmp_path, adapter):
    service = ExecutionService(SessionLedger("session", "srv"), adapter, project_root=tmp_path)
    bound = service.bind_model("m")
    ref = ModelRef("session", "srv", "m", 1)
    return service, ref, bound


def test_v1_internal_evidence_validates_same_digest_and_public_inspect_drops_raw(tmp_path):
    adapter = Adapter()
    service, ref, _bound = _service(tmp_path, adapter)
    evidence = service.inspect_evidence(ref)
    snapshot = evidence["model_snapshot"]
    assert snapshot["fingerprint_components_status"] == "AVAILABLE_V1"
    assert snapshot["fingerprint_components"] == adapter.components
    assert snapshot["fingerprint"] == _digest(snapshot["fingerprint_components"])
    public = service.inspect(ref)
    assert "model_snapshot" not in public


def test_legacy_snapshot_remains_usable_and_internal_evidence_marks_unavailable(tmp_path):
    adapter = Adapter(include_components=False)
    service, ref, _bound = _service(tmp_path, adapter)
    evidence = service.inspect_evidence(ref)
    assert evidence["model_snapshot"]["fingerprint_components_status"] == "UNAVAILABLE_LEGACY"
    assert "fingerprint_components" not in evidence["model_snapshot"]
    assert "model_snapshot" not in service.inspect(ref)


def _invalid_cases():
    good = _components()
    cases = []

    value = copy.deepcopy(good)
    value.pop("label")
    cases.append(("missing-component-field", value, None))
    cases.append(("null-component-object", None, None))

    value = copy.deepcopy(good)
    value["schema_version"] = True
    cases.append(("bool-schema-version", value, None))
    value = copy.deepcopy(good)
    value["schema_version"] = 2
    cases.append(("unsupported-schema-version", value, None))
    value = copy.deepcopy(good)
    value["tag"] = "other"
    cases.append(("wrong-model-tag", value, None))
    value = copy.deepcopy(good)
    value["time_modified_epoch_ms"] = True
    cases.append(("bool-time", value, None))
    value = copy.deepcopy(good)
    value["time_modified_epoch_ms"] = float("nan")
    cases.append(("non-finite-time", value, None))
    value = copy.deepcopy(good)
    value["time_modified_epoch_ms"] = 12.0
    cases.append(("float-time", value, None))
    value = copy.deepcopy(good)
    value["time_modified_epoch_ms"] = -1
    cases.append(("negative-time", value, None))
    value = copy.deepcopy(good)
    value["time_modified_epoch_ms"] = 1 << 63
    cases.append(("time-overflow", value, None))
    value = copy.deepcopy(good)
    value["parameters"] = {"alpha": "0.5"}
    cases.append(("parameters-not-array", value, None))
    value = copy.deepcopy(good)
    value["parameters"].append({"name": "alpha", "expression": "0.7"})
    cases.append(("duplicate-parameter", value, None))
    value = copy.deepcopy(good)
    value["parameters"].reverse()
    cases.append(("unsorted-parameters", value, None))
    value = copy.deepcopy(good)
    value["parameters"][0]["extra"] = "not allowed"
    cases.append(("extra-row-field", value, None))
    value = copy.deepcopy(good)
    value["parameters"][0]["expression"] = 0.5
    cases.append(("non-string-expression", value, None))
    value = copy.deepcopy(good)
    value["label"] = "bad\x00label"
    cases.append(("nul-in-component", value, None))
    cases.append(("digest-mismatch", good, "0" * 64))
    return cases


@pytest.mark.parametrize("case,components,fingerprint_override", _invalid_cases())
def test_present_but_invalid_v1_fails_closed_and_preserves_unknown_state(
        tmp_path, case, components, fingerprint_override):
    adapter = Adapter()
    service, ref, _bound = _service(tmp_path, adapter)
    state = service.ledger._state_for(ref)
    state.dirty = True
    before = (state.fingerprint, state.revision, state.dirty)
    adapter.components = copy.deepcopy(components)
    if fingerprint_override is not None:
        adapter.fingerprint = fingerprint_override

    with pytest.raises(ExecutionContractError) as error:
        service.inspect_evidence(ref)
    assert error.value.code == "EXECUTION_STATE_UNKNOWN", case
    assert (state.fingerprint, state.revision, state.dirty) == before, case


def test_java_utf16_parameter_order_is_required(tmp_path):
    # Java String.compareTo puts U+10000 (D800 surrogate) before U+E000;
    # Python's default Unicode-codepoint sort orders them the other way.
    components = _components([("\U00010000p", "2"), ("\ue000p", "1")])
    assert components["parameters"][0]["name"] == "\U00010000p"
    adapter = Adapter(components=components)
    service, ref, _bound = _service(tmp_path, adapter)
    evidence = service.inspect_evidence(ref)
    assert evidence["model_snapshot"]["fingerprint_components"] == components


def test_worker_uses_one_sample_for_legacy_digest_and_raw_components():
    worker_source = (SOURCE_ROOT / "comsol_mcp/worker_java/PersistentComsolWorker.java").read_text()
    body = re.search(
        r"private Object modelSnapshot\(Map<String, Object> request\) throws Exception \{(.*?)\n  private Object compileJava",
        worker_source,
        flags=re.DOTALL,
    )
    assert body is not None
    body = body.group(1)
    assert body.count("model.label()") == 1
    assert body.count("model.getFilePath()") == 1
    assert body.count("model.timeModified()") == 1
    assert body.count("parameterSet.varnames()") == 1
    assert body.count("parameterSet.get(parameterNames[i])") == 1
    assert "modelFingerprintFromSample(tag, label, filePath," in body
    assert '"fingerprint", fingerprintValue.get("fingerprint")' in body
    assert '"fingerprint_components", fingerprintValue.get("fingerprint_components")' in body
    assert '"fingerprint_scope", Arrays.asList("tag", "label", "file_path", "time_modified", "parameters")' in body
