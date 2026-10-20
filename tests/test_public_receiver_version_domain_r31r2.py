"""R31R2 public-receiver version-domain regression controls; software-only."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import pytest
from comsol_mcp import _g2_public_api as api
from comsol_mcp._execution_contract import ExecutionContractError, PreWriteRefusal
from comsol_mcp._g2_engine import children_node, execute_node_action, prepare_node_action
from tools import w23_direction_discovery as direction

ACTUAL_DESCRIPTOR_SHA256 = "877839f9eb8da3b8145babcd3357fd2982aab0691c7fa620e76bdaa35f48d03a"
ACTUAL_DESCRIPTOR_RELATIVE = Path("evidence/R31R2_ACTUAL_ROOT_LABEL_DESCRIPTORS_SOURCE_R1.json")
ROOT_PATH = {"segments": []}
MODEL_ENTITY_LIST = "com.comsol.model.ModelEntityList"
SOLVER_PATH = {"segments": [{"collection": "sol", "tag": "sol1"}]}


def actual_native_descriptor() -> dict:
    source_root = Path(__file__).resolve().parents[1]
    source_path = source_root.parent / ACTUAL_DESCRIPTOR_RELATIVE
    raw = source_path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == ACTUAL_DESCRIPTOR_SHA256
    rows = json.loads(raw)
    assert isinstance(rows, list) and len(rows) == 1
    return rows[0]


def worker_descriptor_from_native_capture(row: dict) -> dict:
    # The frozen capture names the signature array ``labels``; each row is the
    # exact public method descriptor consumed by Worker.describe_public.
    return {"runtime_version": row["runtime_version"],
            "interfaces": row["interfaces"], "methods": row["labels"]}


@pytest.mark.parametrize(("raw", "semantic", "kind"), [
    ("6.4.0.293", "6.4.0.293", "CANONICAL_FOUR_PART"),
    ("COMSOL Multiphysics 6.4 (开发版本: 293)", "6.4.0.293", "COMSOL_ZH_DEVELOPMENT_BUILD"),
    ("COMSOL Multiphysics 6.4.0 (开发版本: 293)", "6.4.0.293", "COMSOL_ZH_DEVELOPMENT_BUILD"),
    ("COMSOL Multiphysics 6.4 (Build: 293)", "6.4.0.293", "COMSOL_ENGLISH_BUILD"),
])
def test_closed_runtime_version_forms_normalize_from_the_observation(raw, semantic, kind):
    assert api.normalize_public_runtime_version(raw) == (semantic, kind)


def test_wrong_build_normalizes_to_its_own_value_instead_of_the_reviewed_constant():
    raw = "COMSOL Multiphysics 6.4 (开发版本: 294)"
    semantic, kind = api.normalize_public_runtime_version(raw)
    assert semantic == "6.4.0.294"
    assert semantic != api.VERSION
    assert kind == "COMSOL_ZH_DEVELOPMENT_BUILD"


@pytest.mark.parametrize("raw", [
    None, True, 6.4, float("nan"), "", " " * 257,
    "6.4.0.293 trailing", " 6.4.0.293", "6.4.0.293 ", "6.4.0.293\n",
    "v6.4.0.293", "6.4.0", "06.4.0.293", "６.４.０.２９３",
    "OtherProduct 6.4 (开发版本: 293)",
    "COMSOL Multiphysics 6.4", "COMSOL Multiphysics 6.4 (开发版本:)",
    "COMSOL Multiphysics 6.4 (Development version: 293)",
    "COMSOL Multiphysics 6.4 (Build 293)",
    "COMSOL Multiphysics 6.4 (开发版本： 293)",
    "COMSOL Multiphysics 6.4 (开发版本: 293) trailing",
    "COMSOL Multiphysics 6.4 (开发版本: 293) / COMSOL Multiphysics 6.5 (开发版本: 295)",
])
def test_incomplete_ambiguous_or_unrecognized_runtime_versions_reject(raw):
    with pytest.raises(ValueError):
        api.normalize_public_runtime_version(raw)


class PublicEntity:
    def __init__(self):
        self.value = "old"
        self.calls = []

    def comments(self, *args):
        if args:
            self.calls.append(("comments", args))
            self.value = args[0]["data"]
            return self
        self.calls.append(("comments_get", ()))
        return self.value

    def resolveModelPath(self):
        return "model-root/feature-a"


class RootModel:
    def __init__(self):
        self.display_label = "COMSOL"
        self.calls = []
        self.entity = PublicEntity()
        self.solver = None

    def label(self, *args):
        if args:
            self.calls.append(("label", args))
            self.display_label = args[0]
            return self
        self.calls.append(("label_get", ()))
        return self.display_label

    def resolveModelPath(self):
        return "model-root"

    def feature(self, tag=None):
        return self.entity

    def sol(self, tag=None):
        assert tag == "sol1"
        return self.solver


class DescriptorWorker:
    generation = 7

    def __init__(self, descriptor):
        self.descriptor = copy.deepcopy(descriptor)
        self.root = RootModel()

    def client(self):
        return self

    def model(self, tag):
        assert tag == "m"
        return self.root

    def describe_public(self, node):
        return copy.deepcopy(self.descriptor)


@pytest.mark.parametrize("wrong_raw", [
    "COMSOL Multiphysics 6.4 (开发版本: 294)",
    "COMSOL Multiphysics 6.3 (开发版本: 293)",
])
def test_actual_copied_native_root_label_descriptor_gates_public_label_action(wrong_raw):
    native = actual_native_descriptor()
    assert native["runtime_version"] == "COMSOL Multiphysics 6.4 (开发版本: 293)"
    assert "com.comsol.model.ModelEntity" in native["interfaces"]
    exact_label_setter = {"interface": "com.comsol.model.ModelEntity", "method": "label",
                          "parameters": ["java.lang.String"], "returns": "com.comsol.model.ModelEntity"}
    assert exact_label_setter in native["labels"]
    descriptor = worker_descriptor_from_native_capture(native)
    descriptor["runtime_version"] = wrong_raw
    worker = DescriptorWorker(descriptor)
    with pytest.raises(PreWriteRefusal) as error:
        prepare_node_action(worker, "m", "node.label_set", {"path": ROOT_PATH, "label": "new"})
    assert error.value.code == "API_UNSUPPORTED"
    assert worker.root.calls == []


def test_actual_copied_native_root_label_descriptor_runs_through_public_label_action():
    native = actual_native_descriptor()
    descriptor = worker_descriptor_from_native_capture(native)
    worker = DescriptorWorker(descriptor)
    prepared = prepare_node_action(worker, "m", "node.label_set",
                                   {"path": ROOT_PATH, "label": "R31R2-version-control"})
    observed = prepared["descriptor"]
    assert observed["runtime_version"] == api.VERSION
    assert observed["runtime_version_raw"] == native["runtime_version"]
    assert observed["runtime_version_format"] == "COMSOL_ZH_DEVELOPMENT_BUILD"
    result = execute_node_action(worker, "m", "node.label_set", prepared,
                                 model_ref={"model_tag": "m"})
    assert result["success"] is True
    assert result["data"]["after"] == "R31R2-version-control"
    assert worker.root.display_label == "R31R2-version-control"
    assert result["data"]["evidence"]["descriptor"]["runtime_version_raw"] == native["runtime_version"]


def test_generic_api_describe_prepare_invoke_keep_effect_and_raw_version_evidence():
    raw_version = actual_native_descriptor()["runtime_version"]
    descriptor = {"runtime_version": raw_version, "interfaces": [api.MODEL_ENTITY],
        "methods": [
            {"interface": api.MODEL_ENTITY, "method": "comments", "parameters": [], "returns": "java.lang.String"},
            {"interface": api.MODEL_ENTITY, "method": "comments", "parameters": ["java.lang.String"], "returns": api.MODEL_ENTITY},
        ]}
    worker = DescriptorWorker(descriptor)
    request_path = {"segments": [{"collection": "feature", "tag": "featureA"}]}
    described = api.describe_api(worker, "m", {"path": request_path})
    assert described["runtime_version"] == api.VERSION
    assert described["evidence"]["descriptor"]["runtime_version_raw"] == raw_version
    assert [row["effect"] for row in described["methods"]] == ["READ", "WRITE"]
    read_request = {"path": request_path, "method": "comments", "arguments": [], "declared_effect": "READ"}
    write_request = {"path": request_path, "method": "comments", "arguments": [
        {"kind": "string", "shape": [], "data": "changed"}], "declared_effect": "WRITE"}
    prepared_read = api.prepare_invoke(worker, "m", read_request)
    prepared_write = api.prepare_invoke(worker, "m", write_request)
    assert prepared_read["capability"].effect == "READ" and prepared_read["effect"] == "inspect"
    assert prepared_write["capability"].effect == "WRITE" and prepared_write["effect"] == "project_write"
    with pytest.raises(ExecutionContractError) as error:
        api.prepare_invoke(worker, "m", {**write_request, "declared_effect": "READ"})
    assert error.value.code == "PERMISSION_DENIED"
    wrong_version_worker = DescriptorWorker({**descriptor,
        "runtime_version": "COMSOL Multiphysics 6.4 (开发版本: 294)"})
    with pytest.raises(ExecutionContractError) as error:
        api.prepare_invoke(wrong_version_worker, "m", read_request)
    assert error.value.code == "API_UNSUPPORTED"
    with pytest.raises(ExecutionContractError) as error:
        api.describe_api(wrong_version_worker, "m", {"path": request_path})
    assert error.value.code == "API_UNSUPPORTED"


class FeatureList:
    java_type = MODEL_ENTITY_LIST
    def __init__(self):
        self._tags = ["storeM", "eigenA", "stepZ"]
        self._indices = {"stepZ": 0, "eigenA": 1, "storeM": 2}
        self.index_reads = []
    def tags(self):
        return list(self._tags)
    def index(self, tag):
        self.index_reads.append(tag)
        return self._indices[tag]


class SolverNode:
    def __init__(self):
        self.features = FeatureList()
    def feature(self, tag=None):
        assert tag is None or tag in self.features._tags
        return self.features


class OrderWorker(DescriptorWorker):
    def __init__(self, raw_version):
        super().__init__({"runtime_version": raw_version, "interfaces": [MODEL_ENTITY_LIST],
            "methods": [{"interface": MODEL_ENTITY_LIST, "method": "index",
                         "parameters": ["java.lang.String"], "returns": "int"}]})
        self.root.solver = SolverNode()


def test_localized_runtime_order_witness_flows_from_typed_index_producer_to_w23_consumer():
    raw_version = actual_native_descriptor()["runtime_version"]
    worker = OrderWorker(raw_version)
    produced = children_node(worker, "m", SOLVER_PATH, collection="feature", limit=10)
    witness = produced["collection_order"]
    assert witness["order_status"] == "VERIFIED_STABLE_TWO_SAMPLES"
    assert witness["receiver"]["runtime_version"] == api.VERSION
    assert witness["receiver"]["runtime_version_raw"] == raw_version
    assert witness["ordered_tags"] == ["stepZ", "eigenA", "storeM"]
    assert produced["children"] == [
        {"collection": "feature", "tag": "eigenA"},
        {"collection": "feature", "tag": "stepZ"},
        {"collection": "feature", "tag": "storeM"},
    ]
    evidence, index_by_tag = direction._solver_feature_order(produced, SOLVER_PATH, produced["children"])
    assert evidence["order_status"] == "VERIFIED_STABLE_TWO_SAMPLES"
    assert evidence["receiver"]["runtime_version_raw"] == raw_version
    assert index_by_tag == {"stepZ": 0, "eigenA": 1, "storeM": 2}
    assert len(worker.root.solver.features.index_reads) == 6
