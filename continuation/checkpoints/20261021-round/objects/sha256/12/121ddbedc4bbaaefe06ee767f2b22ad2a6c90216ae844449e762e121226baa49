"""G3 R02: complete node discovery, pagination, errors and budgets.

The G2 review found that ``children_node`` probed a fixed ten-collection list
(no material/variable/func/selection/...), swallowed every exception and that
``find_nodes`` explored only one ``limit=500`` page per parent while reporting
nothing about incompleteness.  These tests pin the replacement contract:

* a verified collection allow-list covering the W13-W16 collections,
* one worker batch per node (``probe_children``/``walk_nodes``), with a fixture
  fallback path for plain Python workers,
* explicit ``complete``/``truncated``/``next_cursor``/``errors`` fields,
* cursors bound to the model epoch (worker generation + managed revision) and
  to the query, rejected explicitly when stale - never restarted from zero,
* traversal budgets (node/time) with resumable cursors.
"""
from __future__ import annotations

import json

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._g2_engine import COLLECTION_CANDIDATES, _solver_feature_order_witness, children_node, find_nodes


class _Container:
    def __init__(self, node, collection):
        self.node, self.collection = node, collection

    def tags(self):
        return list(self.node.children[self.collection].keys())

    def get(self, tag):
        return self.node.children[self.collection][tag]


class _FakeNode:
    """A fixture stand-in for a RemoteJava node with declared collections."""

    def __init__(self, children=None, *, tag="fake", type_id="FakeNode", label="fake", probe_error=None):
        object.__setattr__(self, "children", {name: dict(items) for name, items in (children or {}).items()})
        object.__setattr__(self, "_tag", tag)
        object.__setattr__(self, "_type_id", type_id)
        object.__setattr__(self, "_label", label)
        object.__setattr__(self, "_probe_error", probe_error)

    def __getattr__(self, name):
        children = object.__getattribute__(self, "children")
        if name in children:
            def accessor(*args):
                if args:
                    try:
                        return children[name][args[0]]
                    except KeyError as exc:
                        raise AttributeError(name) from exc
                return _Container(self, name)
            return accessor
        raise AttributeError(name)

    def tag(self):
        return self._tag

    def getType(self):
        return self._type_id

    def label(self):
        return self._label


def _probe(node, candidates):
    """Shared fixture probe mirroring the worker `children` command contract."""
    error = object.__getattribute__(node, "_probe_error")
    if error is not None:
        raise error
    rows, errors = [], []
    for spec in candidates:
        collection = spec["collection"]
        children = object.__getattribute__(node, "children")
        if collection not in children:
            continue
        container = getattr(node, collection)()
        rows.extend({"collection": collection, "tag": str(tag)} for tag in container.tags())
    return {"children": rows, "errors": errors}


class _Client:
    def __init__(self, node):
        self.node = node

    def model(self, _tag):
        return self.node


class _FixtureWorker:
    """Plain fixture worker: only the node-proxy fallback path is available."""

    def __init__(self, node, generation=7):
        self.node = node
        self._generation = generation

    @property
    def generation(self):
        return self._generation

    def client(self):
        return _Client(self.node)


class _BatchWorker(_FixtureWorker):
    def __init__(self, node, generation=7):
        super().__init__(node, generation)
        self.children_calls = 0

    def probe_children(self, node, candidates, *, request_id=None, rpc_timeout_s=None):
        self.children_calls += 1
        return _probe(node, candidates)


class _WalkWorker(_BatchWorker):
    """Fixture worker implementing the batch walk contract (one call per page)."""

    def __init__(self, node, generation=7, walk_errors=None, walk_limit_override=None):
        super().__init__(node, generation)
        self.walk_calls = 0
        self.walk_errors = list(walk_errors or [])
        self.walk_limit_override = walk_limit_override

    def walk_nodes(self, node, *, query, candidates, max_nodes, max_seconds, skip_visited, limit, request_id=None, rpc_timeout_s=None):
        import time as _time
        from collections import deque

        self.walk_calls += 1
        has_tag, wanted_tag = "tag" in query, query.get("tag")
        has_type = "type_id" in query or "type" in query
        wanted_type = query.get("type_id", query.get("type"))
        has_label, wanted_label = "label" in query, query.get("label")
        queue = deque([(node, [])])
        visited = 0
        matches = []
        errors = list(self.walk_errors)
        truncated = None
        complete = False
        deadline = _time.monotonic() + max_seconds
        effective_limit = self.walk_limit_override or limit
        while queue:
            if visited - skip_visited >= max_nodes:
                truncated = "node_budget"
                break
            if _time.monotonic() > deadline:
                truncated = "time_budget"
                break
            current, segments = queue.popleft()
            visited += 1
            if visited > skip_visited:
                tag, type_id, label = current.tag(), current.getType(), current.label()
                if ((not has_tag or wanted_tag == tag) and (not has_type or wanted_type == type_id)
                        and (not has_label or wanted_label == label)):
                    matches.append({"segments": list(segments), "tag": tag, "type_id": type_id, "label": label})
                    if len(matches) >= effective_limit:
                        truncated = "match_limit"
                        break
            for row in _probe(current, candidates)["children"]:
                child = current.children[row["collection"]][row["tag"]]
                queue.append((child, segments + [{"collection": row["collection"], "tag": row["tag"]}]))
        else:
            complete = True
        return {"matches": matches, "visited": visited, "complete": complete,
                "truncated_reason": truncated, "errors": errors, "generation": self.generation}


K_VALID_BUDGET = {"max_nodes": 2000, "max_seconds": 20.0}


def test_children_lists_new_collections_and_reports_rpc_efficiency():
    comp = _FakeNode(children={
        "material": {"mat1": _FakeNode(tag="mat1")},
        "variable": {"var1": _FakeNode(tag="var1")},
        "func": {"int1": _FakeNode(tag="int1")},
        "selection": {"sel1": _FakeNode(tag="sel1")},
        "physics": {"ht": _FakeNode(tag="ht")},
    })
    worker = _BatchWorker(comp)
    result = children_node(worker, "m", {"segments": []}, limit=100)
    rows = {(row["collection"], row["tag"]) for row in result["children"]}
    assert {("material", "mat1"), ("variable", "var1"), ("func", "int1"),
            ("selection", "sel1"), ("physics", "ht")} <= rows
    assert result["errors"] == []
    assert result["rpc_calls"] == 1
    assert result["complete"] is True and result["next_cursor"] is None
    assert worker.children_calls == 1


def test_children_fallback_path_skips_unsupported_collections_silently():
    comp = _FakeNode(children={"material": {"mat1": _FakeNode(tag="mat1")}})
    worker = _FixtureWorker(comp)  # no probe_children -> node-proxy fallback
    result = children_node(worker, "m", {"segments": []}, limit=100)
    assert [row["collection"] for row in result["children"]] == ["material"]
    assert result["errors"] == []
    assert result["rpc_calls"] > 1  # fixture probing is per-collection


def test_requested_collection_filters_batch_and_fallback_before_probing():
    root = _FakeNode(children={
        "component": {"comp1": _FakeNode(tag="comp1")},
        "physics": {"ewfd": _FakeNode(tag="ewfd")},
        "material": {"mat1": _FakeNode(tag="mat1")},
    })

    class _RecordingBatchWorker(_BatchWorker):
        def __init__(self, node):
            super().__init__(node)
            self.requested = []

        def probe_children(self, node, candidates, *, request_id=None, rpc_timeout_s=None):
            self.requested.append(tuple(row["collection"] for row in candidates))
            return super().probe_children(node, candidates, request_id=request_id,
                                          rpc_timeout_s=rpc_timeout_s)

    worker = _RecordingBatchWorker(root)
    filtered = children_node(worker, "m", {"segments": []}, limit=100,
                             collection="physics")
    assert filtered["children"] == [{"collection": "physics", "tag": "ewfd"}]
    assert filtered["collection_filter"] == "physics"
    assert filtered["complete"] is True and filtered["errors"] == []
    assert worker.requested[0] == ("physics",)

    unfiltered = children_node(worker, "m", {"segments": []}, limit=100)
    assert {row["collection"] for row in unfiltered["children"]} == {
        "component", "physics", "material"}
    assert "collection_filter" not in unfiltered
    assert worker.requested[1] == tuple(row["collection"] for row in COLLECTION_CANDIDATES)

    fallback = children_node(_FixtureWorker(root), "m", {"segments": []}, limit=100,
                             collection="material")
    assert fallback["children"] == [{"collection": "material", "tag": "mat1"}]
    assert fallback["collection_filter"] == "material"
    assert fallback["rpc_calls"] == 2  # exactly one accessor and one tags call


def test_filtered_children_cursor_is_bound_to_the_requested_collection():
    node = _FakeNode(children={
        "feature": {f"f{i}": _FakeNode(tag=f"f{i}") for i in range(5)},
        "physics": {f"p{i}": _FakeNode(tag=f"p{i}") for i in range(5)},
    })
    worker = _BatchWorker(node)
    first = children_node(worker, "m", {"segments": []}, limit=2,
                          model_revision=4, collection="feature")
    assert first["collection_filter"] == "feature"
    assert first["truncated"] is True and first["next_cursor"]
    second = children_node(worker, "m", {"segments": []}, limit=2,
                           model_revision=4, collection="feature", cursor=first["next_cursor"])
    assert second["collection_filter"] == "feature"
    assert {row["tag"] for row in first["children"]}.isdisjoint(
        row["tag"] for row in second["children"])
    assert second["total"] == 5
    for changed_collection in ("physics", None):
        with pytest.raises(ExecutionContractError) as exc:
            children_node(worker, "m", {"segments": []}, limit=2,
                          model_revision=4, collection=changed_collection,
                          cursor=first["next_cursor"])
        assert exc.value.code == "CURSOR_STALE"


class _IndexFeatureList:
    def __init__(self, node, tags, indices, *, change_after_first=False, after_tags=None):
        self.node = node
        self.native_tags = list(tags)
        self.indices = dict(indices)
        self.change_after_first = change_after_first
        self.after_tags = after_tags
        self.index_error = None
        self.tags_calls = 0
        self.index_calls = 0

    def tags(self):
        self.tags_calls += 1
        if self.after_tags is not None and self.tags_calls >= 3:
            return list(self.after_tags)
        return list(self.native_tags)

    def get(self, tag):
        return self.node.children["feature"][tag]

    def index(self, tag):
        self.index_calls += 1
        if self.index_error is not None:
            raise RuntimeError(self.index_error)
        if self.change_after_first and self.index_calls > len(self.native_tags):
            changed = dict(self.indices)
            first, second = self.native_tags[:2]
            changed[first], changed[second] = changed[second], changed[first]
            return changed[tag]
        return self.indices[tag]


class _IndexedSolverNode(_FakeNode):
    def __init__(self, children=None, **kwargs):
        super().__init__(children=children, **kwargs)
        self.index_list = _IndexFeatureList(self, ["zeta", "alpha", "mid"],
                                            {"zeta": 1, "alpha": 2, "mid": 0})

    def feature(self, *args):
        if args:
            return self.children["feature"][args[0]]
        return self.index_list


class _OrderWorker(_BatchWorker):
    def __init__(self, node, *, descriptor=None, error_rows=None):
        super().__init__(node)
        self.descriptor_calls = 0
        self.descriptor = descriptor
        self.error_rows = list(error_rows or [])

    def probe_children(self, node, candidates, *, request_id=None, rpc_timeout_s=None):
        self.children_calls += 1
        reply = _probe(node, candidates)
        if self.error_rows:
            reply["errors"] = list(self.error_rows)
        return reply

    def describe_public(self, node):
        self.descriptor_calls += 1
        if self.descriptor is not None:
            return self.descriptor
        return {"runtime_version": "6.4.0.293",
                "interfaces": ["com.comsol.model.ModelEntityList"],
                "methods": [{"interface": "com.comsol.model.ModelEntityList",
                             "method": "index", "parameters": ["java.lang.String"],
                             "returns": "int"}]}


def test_solver_feature_order_witness_uses_typed_native_indices_twice_and_keeps_display_sort():
    solver = _IndexedSolverNode(
        children={"feature": {tag: _FakeNode(tag=tag) for tag in ["zeta", "alpha", "mid"]}},
        tag="sol1", type_id="SolverSequence")
    root = _FakeNode(children={"sol": {"sol1": solver}})
    worker = _OrderWorker(root)
    result = children_node(worker, "m", {"segments": [{"collection": "sol", "tag": "sol1"}]},
                          limit=20, collection="feature")
    assert [row["tag"] for row in result["children"]] == ["alpha", "mid", "zeta"]
    witness = result["collection_order"]
    assert witness["order_status"] == "VERIFIED_STABLE_TWO_SAMPLES"
    assert witness["ordered_tags"] == ["mid", "zeta", "alpha"]
    assert witness["sample_atomicity"] == "TWO_STABLE_SAMPLES_NOT_ATOMIC_TO_OTHER_CLIENTS"
    assert witness["receiver_reused_for_all_reads"] is True
    assert witness["index_samples"][0] == witness["index_samples"][1]
    assert solver.index_list.index_calls == 6
    assert worker.descriptor_calls == 1
    assert result["complete"] is True
    assert result["rpc_calls"] == 11  # child batch plus 4 typed setup/read calls and 2 passes over 3 tags


@pytest.mark.parametrize("case", ["changed", "duplicate", "missing", "boolean", "float", "tags_changed", "proxy_error"])
def test_solver_feature_order_bad_samples_fail_closed_without_changing_child_listing(case):
    solver = _IndexedSolverNode(
        children={"feature": {tag: _FakeNode(tag=tag) for tag in ["zeta", "alpha", "mid"]}},
        tag="sol1", type_id="SolverSequence")
    if case == "changed":
        solver.index_list.change_after_first = True
    elif case == "duplicate":
        solver.index_list.indices = {"zeta": 1, "alpha": 1, "mid": 0}
    elif case == "missing":
        solver.index_list.indices = {"zeta": -1, "alpha": 1, "mid": 0}
    elif case == "boolean":
        solver.index_list.indices = {"zeta": True, "alpha": 1, "mid": 0}
    elif case == "float":
        solver.index_list.indices = {"zeta": 1.0, "alpha": 2, "mid": 0}
    elif case == "tags_changed":
        solver.index_list.after_tags = ["zeta", "mid", "alpha"]
    elif case == "proxy_error":
        solver.index_list.index_error = "controlled proxy index failure"
    root = _FakeNode(children={"sol": {"sol1": solver}})
    worker = _OrderWorker(root)
    result = children_node(worker, "m", {"segments": [{"collection": "sol", "tag": "sol1"}]},
                          limit=20, collection="feature")
    assert [row["tag"] for row in result["children"]] == ["alpha", "mid", "zeta"]
    assert result["complete"] is True and result["errors"] == []
    assert result["collection_order"]["order_status"] == "UNVERIFIED"
    assert result["collection_order"]["provenance_failure"]
    if case == "proxy_error":
        failure = result["collection_order"]["provenance_failure"]
        assert failure["stage"] == "index_sample_1"
        assert "controlled proxy index failure" in failure["message"]


@pytest.mark.parametrize("descriptor", [
    {"runtime_version": "6.4.0.293", "interfaces": ["com.comsol.model.ModelEntityList"],
     "methods": [{"interface": "com.comsol.model.ModelEntityList", "method": "index",
                  "parameters": ["java.lang.String"], "returns": "java.lang.Integer"}]},
    {"runtime_version": "6.4.0.293", "interfaces": ["foreign.List"],
     "methods": [{"interface": "foreign.List", "method": "index",
                  "parameters": ["java.lang.String"], "returns": "int"}]},
])
def test_solver_feature_order_wrong_descriptor_fails_closed(descriptor):
    solver = _IndexedSolverNode(
        children={"feature": {tag: _FakeNode(tag=tag) for tag in ["zeta", "alpha", "mid"]}},
        tag="sol1", type_id="SolverSequence")
    root = _FakeNode(children={"sol": {"sol1": solver}})
    worker = _OrderWorker(root, descriptor=descriptor)
    result = children_node(worker, "m", {"segments": [{"collection": "sol", "tag": "sol1"}]},
                          limit=20, collection="feature")
    assert result["complete"] is True
    assert result["collection_order"]["order_status"] == "UNVERIFIED"
    assert result["collection_order"]["provenance_failure"]["stage"] == "public_descriptor"
    assert solver.index_list.index_calls == 0


def test_order_witness_is_not_attempted_for_non_solver_path_or_incomplete_listing():
    solver = _IndexedSolverNode(
        children={"feature": {tag: _FakeNode(tag=tag) for tag in ["zeta", "alpha", "mid"]}},
        tag="sol1", type_id="SolverSequence")
    root = _FakeNode(children={"sol": {"sol1": solver},
                               "study": {"std1": _FakeNode(children={"feature": {"f1": _FakeNode(tag="f1")}},
                                                           tag="std1", type_id="Study")}})
    worker = _OrderWorker(root)
    study_result = children_node(worker, "m", {"segments": [{"collection": "study", "tag": "std1"}]},
                                 collection="feature")
    assert "collection_order" not in study_result
    assert worker.descriptor_calls == 0

    failing_worker = _OrderWorker(root, error_rows=[{"collection": "feature", "code": "TAGS_PROBE_FAILED",
                                                      "message": "source listing getter failed"}])
    incomplete = children_node(failing_worker, "m",
                               {"segments": [{"collection": "sol", "tag": "sol1"}]},
                               collection="feature")
    assert incomplete["complete"] is False
    assert incomplete["errors"][0]["message"] == "source listing getter failed"
    assert incomplete["collection_order"]["order_status"] == "UNVERIFIED"
    assert incomplete["collection_order"]["provenance_failure"]["stage"] == "children_listing"
    assert failing_worker.descriptor_calls == 0


def test_solver_feature_order_refuses_more_than_500_before_any_index_call():
    tags = [f"f{index:04d}" for index in range(501)]
    solver = _IndexedSolverNode(
        children={"feature": {tag: _FakeNode(tag=tag) for tag in tags}},
        tag="sol1", type_id="SolverSequence")
    solver.index_list.native_tags = list(tags)
    solver.index_list.indices = {tag: index for index, tag in enumerate(tags)}
    worker = _OrderWorker(_FakeNode())
    rows = [{"collection": "feature", "tag": tag} for tag in tags]
    witness, calls = _solver_feature_order_witness(
        worker, solver, {"segments": [{"collection": "sol", "tag": "sol1"}]},
        rows, listing_complete=True)
    assert witness["order_status"] == "UNVERIFIED"
    assert witness["provenance_failure"]["code"] == "ORDER_WITNESS_MEMBER_LIMIT"
    assert solver.index_list.index_calls == 0
    assert calls == 3  # actual feature receiver, typed descriptor, tags inventory


def test_filtered_children_rejects_unknown_collections_and_cross_filter_rows():
    node = _FakeNode(children={"physics": {"ewfd": _FakeNode(tag="ewfd")}})
    worker = _BatchWorker(node)
    for invalid in ("unsupported", True, 3):
        with pytest.raises(ExecutionContractError) as exc:
            children_node(worker, "m", {"segments": []}, collection=invalid)
        assert exc.value.code == "INVALID_REQUEST"
    assert worker.children_calls == 0

    class _LeakyBatchWorker(_BatchWorker):
        def probe_children(self, node, candidates, *, request_id=None, rpc_timeout_s=None):
            return {"children": [{"collection": "material", "tag": "mat1"}], "errors": []}

    with pytest.raises(ExecutionContractError) as exc:
        children_node(_LeakyBatchWorker(node), "m", {"segments": []},
                      collection="physics")
    assert exc.value.code == "EXECUTION_STATE_UNKNOWN"

    class _LeakyErrorWorker(_BatchWorker):
        def probe_children(self, node, candidates, *, request_id=None, rpc_timeout_s=None):
            return {"children": [], "errors": [{"collection": "material",
                                                  "code": "PROBE_FAILED"}]}

    with pytest.raises(ExecutionContractError) as exc:
        children_node(_LeakyErrorWorker(node), "m", {"segments": []},
                      collection="physics")
    assert exc.value.code == "EXECUTION_STATE_UNKNOWN"


def test_children_probe_error_is_reported_not_swallowed():
    node = _FakeNode(children={"physics": {"ht": _FakeNode(tag="ht")}})

    class _ErrorWorker(_BatchWorker):
        def probe_children(self, node, candidates, *, request_id=None, rpc_timeout_s=None):
            return {"children": [{"collection": "physics", "tag": "ht"}],
                    "errors": [{"collection": "material", "code": "COLLECTION_PROBE_FAILED", "message": "engine error"}]}

    result = children_node(_ErrorWorker(node), "m", {"segments": []}, limit=100)
    assert result["children"] == [{"collection": "physics", "tag": "ht"}]
    assert result["errors"][0]["code"] == "COLLECTION_PROBE_FAILED"
    assert result["complete"] is False and result["incomplete"] is True


def test_children_wp_inner_geometry_is_an_accessor_child():
    inner = _FakeNode(children={"feature": {"r1": _FakeNode(tag="r1")}}, tag="wp3geom")
    wp = _FakeNode(children={"geom": {"": inner}}, tag="wp3")

    class _WpWorker(_BatchWorker):
        def probe_children(self, node, candidates, *, request_id=None, rpc_timeout_s=None):
            if node.tag() == "wp3":
                return {"children": [{"collection": "geom", "accessor": True}], "errors": []}
            return _probe(node, candidates)

    result = children_node(_WpWorker(wp), "m", {"segments": []}, limit=100)
    assert result["children"] == [{"collection": "geom", "accessor": True, "tag": None}]

    # The inner geometry is reachable through the accessor path segment; the
    # accessor resolves from the model root just like any other path step.
    class _AccessorRoot(_FakeNode):
        def __getattr__(self, name):
            if name == "geom":
                return lambda *args: inner
            return super().__getattr__(name)

    root = _AccessorRoot(children={}, tag="root")
    inner_result = children_node(_BatchWorker(root), "m", {"segments": [{"accessor": "geom"}]}, limit=100)
    assert [row["tag"] for row in inner_result["children"]] == ["r1"]


def test_children_pagination_beyond_500_children():
    features = {f"f{index:04d}": _FakeNode(tag=f"f{index:04d}") for index in range(700)}
    geom = _FakeNode(children={"feature": features})
    worker = _BatchWorker(geom)
    seen = []
    cursor = None
    pages = 0
    while True:
        page = children_node(worker, "m", {"segments": []}, cursor=cursor, limit=500)
        seen.extend(row["tag"] for row in page["children"])
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            assert page["complete"] is True
            break
        assert len(page["children"]) <= 500
    assert pages == 2 and len(seen) == 700 and seen == sorted(seen)


def test_children_cursor_is_rejected_when_revision_changes():
    geom = _FakeNode(children={"feature": {f"f{i}": _FakeNode(tag=f"f{i}") for i in range(10)}})
    worker = _BatchWorker(geom)
    first = children_node(worker, "m", {"segments": []}, limit=3, model_revision=4)
    cursor = first["next_cursor"]
    assert cursor and first["truncated"] is True
    with pytest.raises(ExecutionContractError) as exc:
        children_node(worker, "m", {"segments": []}, cursor=cursor, limit=3, model_revision=5)
    assert exc.value.code == "CURSOR_STALE"


def test_children_cursor_is_rejected_when_children_change():
    geom = _FakeNode(children={"feature": {f"f{i}": _FakeNode(tag=f"f{i}") for i in range(10)}})
    worker = _BatchWorker(geom)
    cursor = children_node(worker, "m", {"segments": []}, limit=3)["next_cursor"]
    geom.children["feature"]["f-new"] = _FakeNode(tag="f-new")
    with pytest.raises(ExecutionContractError) as exc:
        children_node(worker, "m", {"segments": []}, cursor=cursor, limit=3)
    assert exc.value.code == "CURSOR_STALE"


def test_children_invalid_cursor_is_rejected_not_restarted():
    geom = _FakeNode(children={"feature": {"f1": _FakeNode(tag="f1")}})
    with pytest.raises(ExecutionContractError) as exc:
        children_node(_BatchWorker(geom), "m", {"segments": []}, cursor="not-a-cursor", limit=3)
    assert exc.value.code == "INVALID_CURSOR"
    with pytest.raises(ExecutionContractError) as exc2:
        children_node(_BatchWorker(geom), "m", {"segments": []}, cursor="", limit=3)
    assert exc2.value.code == "INVALID_CURSOR"


def _workplane_tree():
    rect = _FakeNode(children={"feature": {"r2": _FakeNode(tag="r2")}}, tag="r1", type_id="Rectangle")
    inner_geom = _FakeNode(children={"feature": {"r1": rect}}, tag="wpgeom", type_id="Geometry")

    class _AccessorNode(_FakeNode):
        def __getattr__(self, name):
            if name == "geom":
                return lambda *args: self.children["geom"][""]
            return super().__getattr__(name)

    wp3 = _AccessorNode(children={"geom": {"": inner_geom}}, tag="wp3", type_id="WorkPlane")
    geom1 = _FakeNode(children={"feature": {"wp3": wp3}}, tag="geom1", type_id="Geometry")
    comp = _FakeNode(children={"geom": {"geom1": geom1}}, tag="comp1", type_id="Component")
    model = _FakeNode(children={"component": {"comp1": comp}}, tag="model", type_id="Model")
    return _FixtureWorker(model)


def test_find_nodes_recurses_into_nested_workplane_geometry():
    """Fixture path: covers the Python BFS used when the worker has no batch walk."""
    worker = _workplane_tree()
    result = find_nodes(worker, "m", {"tag": "r1"}, limit=10)
    assert [row["tag"] for row in result["results"]] == ["r1"]
    segments = result["results"][0]["path"]["segments"]
    assert {"collection": "component", "tag": "comp1"} in segments
    assert {"collection": "feature", "tag": "wp3"} in segments
    assert {"accessor": "geom"} in segments
    assert {"collection": "feature", "tag": "r1"} in segments
    assert result["complete"] is True and result["errors"] == []


def test_find_nodes_budget_cutoff_returns_resumable_cursor():
    features = {f"f{index:03d}": _FakeNode(tag=f"f{index:03d}", type_id="Size") for index in range(40)}
    geom = _FakeNode(children={"feature": features})
    model = _FakeNode(children={"component": {"comp1": _FakeNode(children={"geom": {"geom1": geom}}, tag="comp1")}}, tag="model")

    class _TreeWorker(_WalkWorker):
        def client(self):
            return _Client(model)

    worker = _TreeWorker(model)
    collected = []
    cursor = None
    pages = 0
    while True:
        page = find_nodes(worker, "m", {"type_id": "Size"}, limit=10, cursor=cursor,
                          budget={"max_nodes": 12, "max_seconds": 20.0})
        collected.extend(row["tag"] for row in page["results"])
        pages += 1
        cursor = page["next_cursor"]
        assert page["status"] in {"SUCCEEDED", "INCOMPLETE"}
        if cursor is None:
            assert page["complete"] is True
            break
        assert page["truncated"] is True
    assert sorted(collected) == sorted(features.keys())
    assert len(collected) == len(set(collected))  # no duplicates across pages
    assert pages >= 2
    assert worker.walk_calls == pages


def test_find_nodes_probe_errors_never_look_like_not_found():
    model = _FakeNode(children={"component": {"comp1": _FakeNode(tag="comp1")}}, tag="model")

    class _TreeWorker(_WalkWorker):
        def client(self):
            return _Client(model)

    worker = _TreeWorker(model, walk_errors=[{"collection": "physics", "code": "COLLECTION_PROBE_FAILED", "message": "engine error"}])
    result = find_nodes(worker, "m", {"tag": "nothing"}, limit=10)
    assert result["results"] == []
    assert result["complete"] is False
    assert result["errors"], "probe errors must be surfaced"
    assert result["status"] == "INCOMPLETE"
    # The walk itself finished; there is nothing more to resume, and the
    # coverage gap is reported as an error instead of a resumable position.
    assert result["truncated"] is False and result["next_cursor"] is None


def test_find_nodes_truthful_empty_result_is_complete():
    model = _FakeNode(children={"component": {"comp1": _FakeNode(tag="comp1")}}, tag="model")

    class _TreeWorker(_WalkWorker):
        def client(self):
            return _Client(model)

    result = find_nodes(_TreeWorker(model), "m", {"tag": "nothing"}, limit=10)
    assert result["results"] == []
    assert result["complete"] is True and result["truncated"] is False
    assert result["errors"] == [] and result["status"] == "SUCCEEDED"


def test_find_nodes_cursor_bound_to_query_and_epoch():
    model = _FakeNode(children={"component": {"comp1": _FakeNode(tag="comp1")}}, tag="model")

    class _TreeWorker(_WalkWorker):
        def client(self):
            return _Client(model)

    worker = _TreeWorker(model)
    cursor = find_nodes(worker, "m", {"tag": "comp1"}, limit=1, budget={"max_nodes": 1, "max_seconds": 20.0})["next_cursor"]
    assert cursor is not None
    with pytest.raises(ExecutionContractError) as exc:
        find_nodes(worker, "m", {"tag": "other"}, limit=1, cursor=cursor)
    assert exc.value.code == "CURSOR_STALE"
    worker._generation = 8
    with pytest.raises(ExecutionContractError) as exc2:
        find_nodes(worker, "m", {"tag": "comp1"}, limit=1, cursor=cursor)
    assert exc2.value.code == "CURSOR_STALE"
    with pytest.raises(ExecutionContractError) as exc3:
        find_nodes(worker, "m", {"tag": "comp1"}, limit=1, cursor=json.dumps({"v": 1}))
    assert exc3.value.code == "INVALID_CURSOR"
