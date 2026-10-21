"""Offline replay of the actual R31R2 public reads through producer and runner gates."""
from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from tools import run_native_w23_saved_mph_diagnostic as runner
from tools import w23_direction_discovery as direction


EVIDENCE = Path(os.environ.get(
    "W23_R31R2_REPLAY_EVIDENCE",
    str(Path(__file__).resolve().parent / "fixtures" / "w23_r31r2_actual_direction_replay.json"),
))
PHYSICS_PATH = {"segments": [
    {"collection": "component", "tag": "comp1"},
    {"collection": "physics", "tag": "ewfd"},
]}


def _key(operation_id, arguments):
    return operation_id, json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _actual_replay(*, mutate_identifier=None):
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    assert evidence["schema"] == "W23_R31R2_ACTUAL_PUBLIC_DIRECTION_READ_REPLAY_R1"
    records = evidence["records"]
    assert len(records) == 160
    queues = defaultdict(deque)
    for row in records:
        assert row["operation_id"] in direction.READONLY_OPERATION_IDS
        public_data = deepcopy(row["public_data"])
        if (mutate_identifier is not None and row["operation_id"] == "node.inspect"
                and row["arguments"].get("path") == PHYSICS_PATH):
            mutate_identifier(public_data)
        queues[_key(row["operation_id"], row["arguments"])].append(public_data)
    consumed = []

    def call(operation_id, arguments):
        key = _key(operation_id, arguments)
        if not queues[key]:
            raise AssertionError(f"no captured actual read remains for {operation_id} {arguments!r}")
        consumed.append(key)
        return queues[key].popleft()

    return direction.discover_direction_graph(call), consumed, queues, evidence


def test_actual_160_read_replay_resolves_documented_name_scope_and_runner_gate():
    graph, consumed, queues, evidence = _actual_replay()
    namespace = graph["electromagnetic_waves"]["namespace_correlation"]
    assert len(consumed) == 160
    assert not any(queues.values())
    assert evidence["source_graph_sha256"] == "851014faf91c97e46b5cb6411f9cfce651608ae64a1fad5c9b28d5fd48f89027"
    assert namespace["physics_tag"] == "ewfd"
    assert namespace["physics_identifier"] == "emw"
    assert namespace["tag_comparison_status"] == "TAG_NAMESPACE_MISMATCH_UNRESOLVED"
    assert namespace["status"] == "IDENTIFIER_SCOPE_MATCH"
    assert graph["native_scientific_acceptance"] == "NOT_ACCEPTED_DIAGNOSTIC_READBACK_ONLY"
    assert runner.direction_graph_is_resolved(graph) is True


@pytest.mark.parametrize("failure", ["wrong", "missing"])
def test_actual_replay_wrong_or_missing_identifier_is_rejected_by_runner(failure):
    def change(data):
        if failure == "wrong":
            data["identifier"] = "wrong"
            data["identifier_status"] = "OBSERVED"
            data["identifier_unavailable_reason"] = None
        else:
            data["identifier"] = None
            data["identifier_status"] = "UNAVAILABLE"
            data["identifier_unavailable_reason"] = "API_UNSUPPORTED"

    graph, consumed, queues, _ = _actual_replay(mutate_identifier=change)
    assert len(consumed) == 160
    assert not any(queues.values())
    assert runner.direction_graph_is_resolved(graph) is False


def test_actual_replay_ambiguous_identifier_fails_in_producer_before_runner():
    def mark_ambiguous(data):
        data["identifier"] = None
        data["identifier_status"] = "AMBIGUOUS"
        data["identifier_unavailable_reason"] = None

    with pytest.raises(direction.DirectionDiscoveryError, match="ambiguous Physics identifier"):
        _actual_replay(mutate_identifier=mark_ambiguous)


def test_old_tag_namespace_status_cannot_satisfy_runner_gate():
    assert runner.direction_graph_is_resolved({
        "electromagnetic_waves": {"namespace_correlation": {
            "status": "TAG_NAMESPACE_MATCH",
        }},
        "port_name_binding_status": {"1": "UNIQUE_EXACT_PORT_NAME"},
        "bma_solution_bindings": [{"status": "UNIQUE_EXACT_LINK"}],
    }) is False


def test_namespace_consumer_requires_bounded_emw_and_rejects_expected_prefix_drift():
    valid = direction.correlate_physics_identity("ewfd", "emw", "OBSERVED")
    assert direction.physics_namespace_is_resolved(valid) is True
    graph = {
        "electromagnetic_waves": {"namespace_correlation": valid},
        "port_name_binding_status": {"1": "UNIQUE_EXACT_PORT_NAME"},
        "bma_solution_bindings": [{"status": "UNIQUE_EXACT_LINK"}],
    }
    assert runner.direction_graph_is_resolved(graph) is True

    both_none = deepcopy(valid)
    both_none.update({"physics_identifier": None, "observed_field_variable_prefix": None,
                      "identifier_prefix_equal": True,
                      "physics_identifier_tag_alias_equal": False})
    both_empty = deepcopy(valid)
    both_empty.update({"physics_identifier": "", "observed_field_variable_prefix": "",
                       "physics_tag": "", "identifier_prefix_equal": True,
                       "physics_identifier_tag_alias_equal": True})
    both_wrong = deepcopy(valid)
    both_wrong.update({"physics_identifier": "wrong", "observed_field_variable_prefix": "wrong",
                       "identifier_prefix_equal": True,
                       "physics_identifier_tag_alias_equal": False})
    empty_tag = deepcopy(valid)
    empty_tag.update({"physics_tag": "", "physics_identifier_tag_alias_equal": False})

    for namespace in (both_none, both_empty, both_wrong, empty_tag):
        assert direction.physics_namespace_is_resolved(namespace) is False
        mutated_graph = deepcopy(graph)
        mutated_graph["electromagnetic_waves"]["namespace_correlation"] = namespace
        assert runner.direction_graph_is_resolved(mutated_graph) is False

    assert direction.physics_namespace_is_resolved(valid, expected_prefix="wrong") is False
    assert runner.direction_graph_is_resolved(graph, expected_prefix="wrong") is False
