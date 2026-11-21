"""T13 synthetic batch-reply control for error propagation across find cursors.

This exercises real find_nodes cursor creation/consumption with a stand-in for
batch replies; it does not exercise the Java gateway or a COMSOL process.
"""
from __future__ import annotations

from comsol_mcp._g2_engine import find_nodes
from tests.test_t13_java_walk_getter_failure_20261109 import _FixedReplyWorker


class SequenceWorker(_FixedReplyWorker):
    def __init__(self, replies):
        super().__init__({})
        self.replies = list(replies)

    def walk_nodes(self, node, *, query, candidates, max_nodes, max_seconds, skip_visited, limit):
        self.calls.append({"query": dict(query), "skip_visited": skip_visited})
        assert self.replies, "unexpected worker call"
        return self.replies.pop(0)


def _reply(*, visited, truncated, errors):
    return {"matches": [], "visited": visited, "matched_in_call": 0,
            "complete": truncated is None, "truncated_reason": truncated,
            "errors": errors, "generation": 7}


def test_find_error_on_truncated_page_cannot_be_laundered_by_cursor():
    error = {"collection": "component", "code": "GETTER_FAILED", "message": "synthetic fault"}
    worker = SequenceWorker([
        _reply(visited=1, truncated="node_budget", errors=[error]),
        _reply(visited=2, truncated=None, errors=[]),
    ])
    first = find_nodes(worker, "m", {"type": "ComponentType"}, model_revision=4)
    assert first["status"] == "INCOMPLETE" and first["errors"]
    if first["next_cursor"] is None:
        return  # Safe alternative: require a fresh full search after the gap.
    final = find_nodes(worker, "m", {"type": "ComponentType"}, model_revision=4,
                       cursor=first["next_cursor"])
    assert final["status"] == "INCOMPLETE" and final["complete"] is False, (first, final)


def test_clean_truncated_search_remains_resumable():
    worker = SequenceWorker([
        _reply(visited=1, truncated="node_budget", errors=[]),
        _reply(visited=2, truncated=None, errors=[]),
    ])
    first = find_nodes(worker, "m", {"type": "ComponentType"}, model_revision=4)
    assert first["next_cursor"] is not None and first["truncated"] is True
    final = find_nodes(worker, "m", {"type": "ComponentType"}, model_revision=4,
                       cursor=first["next_cursor"])
    assert final["status"] == "SUCCEEDED" and final["complete"] is True
    assert [c["skip_visited"] for c in worker.calls] == [0, 1]
