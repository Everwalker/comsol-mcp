"""T20 generic typed-property file boundary — public route matrix (2026-11-08).

Synthetic public-route regression for the counterexamples found by the
read-only route review (docs/universal_mcp_execution/evidence/full/
T20-generic-property-route-review-20261108.md) and its scratch probes
(v3/v4 reproduced here, v5 = transaction nesting probe).  Like the parent
test ``test_t20_generic_filename_boundary_20261108.py``, the owner-isolation
verifier is stubbed to model an already-owned server, while the project
authority, registry admission, managed backend, write service and property
adapter are real.  The fake Worker does not establish native behavior.

What this file pins:

* DESIRED-BUT-NOT-YET-TRUE (expected RED today): a path-shaped string leaf
  (external absolute path or ``..`` traversal) must be refused before the
  fake engine setter runs on every public write route —
  ``node.property_set`` (parent test, absolute case), ``node.property_set``
  relative traversal, ``node.property_entry_set`` (keyed entry),
  ``node.property_index_set`` (StringMatrix row cell), and the nested
  ``transaction.apply`` / ``transaction.trial`` routes, which share the one
  ``_run_g2_action`` runner (``_managed_backend.py:4093``/``:4178``).
  These assertions currently fail because the routes store the outside
  value — that is the point: the RED failure mode is "an external path was
  actually accepted", not a test/harness error.
* DESIGNED-AND-TRUE (expected GREEN): unrelated plain strings
  (``linear``, ``f1``, ``m/s^-1``, ``file``) and in-project absolute values
  must keep applying; the isolation gate still refuses before any engine
  call when unstubbed; unknown-metadata properties stay pre-refused
  (``API_UNSUPPORTED``); and this fake's runtime value type alone
  (``getValueType("filename") == String`` — indistinguishable from ``interp``)
  does not establish file semantics, so a path-shape filter is only a partial
  mitigation and NO test here claims a generic UNKNOWN fail-closed.

Honesty boundaries: the refusal expectation is based on the *path shape of
the value*, never on whitelisting the property name ``filename``/``filenames``
— a future fix must classify by (versioned doc table | value path-shape), not
by sample names.  ``transaction.trial`` today honestly returns
EXECUTION_STATE_UNKNOWN (its scoped readback cannot cover keyed entries), but
the nested action is still dispatched to the trial copy first — the missing
piece is a pre-write/pre-checkpoint refusal (review S1), and the trial-copy
capture below proves today's dispatch really happened. A future preflight can
report ACCESS_VIOLATION instead of this later UNKNOWN. Nothing in this file
observes a real COMSOL engine; no native leak is claimed or implied.
"""
from __future__ import annotations

import copy
import zipfile
from pathlib import Path
from typing import Any, Mapping

from tests.test_t20_artifact_boundary import (
    INTERPOLATION_META,
    FNode,
    _call,
    _envelope,
    _error_code,
    _make_project,
    _node_path,
)

OUTSIDE_SENTINEL = "SENTINEL-OUTSIDE-T20-ROUTES"


class KeyedNode(FNode):
    """Fake engine node with setIndex/setEntry/entry readback (fake, not native).

    Mirrors the scratch probe v3/v4 fixture exactly so the matrix/entry
    write semantics exercised here match what the review recorded.
    """

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.entries: dict[str, Any] = {}

    def setIndex(self, name: str, typed: Any, *idx: int) -> None:
        data = typed.get("data") if isinstance(typed, dict) else typed
        matrix = self.matrices.setdefault(name, [])
        i = idx[0]
        while len(matrix) <= i:
            matrix.append([])
        if isinstance(data, list) and len(idx) == 1:
            matrix[i] = list(data)
        else:
            row = matrix[i]
            j = idx[1] if len(idx) > 1 else 0
            while len(row) <= j:
                row.append(None)
            row[j] = data

    def getEntryKeys(self, name: str) -> list[str]:
        return sorted(self.entries)

    def getEntryKeyIndex(self, name: str, key: str) -> int:
        return sorted(self.entries).index(key) if key in self.entries else -1

    def setEntry(self, name: str, key: str, typed: Any) -> None:
        self.entries[key] = typed.get("data") if isinstance(typed, dict) else typed

    def getString(self, name: str, index: int | None = None) -> Any:
        if index is None:
            return super().getString(name)
        keys = sorted(self.entries)
        return self.entries[keys[index]] if index < len(keys) else None


def _fake_mph_save(model: FNode) -> None:
    """Give the fake model the only checkpoint capability the transaction and
    trial routes touch: a save() writing a valid (tiny) MPH-zip candidate."""

    def save(path: str) -> None:
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("model/model.mphbin", b"fake-checkpoint-t20")

    model.__dict__["save"] = save


class TrialWorker:
    """The T20 harness FakeWorker plus ONLY the surface the trial route uses:
    backend_snapshot, client().tags()/load()/remove().  load() deep-copies the
    main model so a trial write can be distinguished from a main-model write,
    and remove() captures the clone's final state as the observation of what
    the trial engine held before cleanup deleted it."""

    def __init__(self, inner: Any, model: FNode, main_tag: str = "m1") -> None:
        self._inner = inner
        self.main_tag = main_tag
        self.models = {main_tag: model}
        self.snapshots = 0
        self.removed_state: dict[str, dict[str, Any]] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def backend_snapshot(self, model_tag: str) -> dict[str, Any]:
        self.snapshots += 1
        return {"model_tag": model_tag, "kind": "fake-snapshot"}

    def client(self):
        outer = self

        class _C:
            def model(self, tag: str):
                return outer.models[tag]

            def tags(self) -> list[str]:
                return list(outer.models)

            def load(self, path: Path, tag: str | None = None):
                clone = copy.deepcopy(outer.models[outer.main_tag])
                outer.models[tag] = clone
                return clone

            def remove(self, tag: str) -> None:
                model_node = outer.models.pop(tag, None)
                captured: dict[str, Any] = {}
                for collection, listing in getattr(model_node, "collections", {}).items():
                    items = getattr(listing, "items", {})
                    captured[collection] = {
                        item_tag: {
                            "entries": dict(getattr(node, "entries", {}) or {}),
                            "values": dict(getattr(node, "values", {}) or {}),
                            "matrices": {k: [list(r) for r in v]
                                         for k, v in (getattr(node, "matrices", {}) or {}).items()},
                        }
                        for item_tag, node in items.items()
                    }
                outer.removed_state[str(tag)] = captured

        return _C()


def _keyed_node(model: FNode, tag: str = "keyedA", *, seed_funcs: bool = False) -> KeyedNode:
    node = KeyedNode(tag=tag, type_id="Interpolation",
                     value_types={**INTERPOLATION_META, "filenames": "String"})
    if seed_funcs:
        node.matrices["funcs"] = [["placeholder"]]
    model.collections["func"].items[tag] = node
    return node


def _outside_file(tmp_path: Path, root: Path) -> Path:
    outside = tmp_path / "synthetic-other-project" / "input.dat"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text(OUTSIDE_SENTINEL + "\n")
    assert not outside.is_relative_to(root)
    return outside


def _entry_body(tag: str, value: str, key: str = "sol1") -> dict:
    return {"path": _node_path(("func", tag)), "name": "filenames", "key": key,
            "value": {"kind": "string", "shape": [], "data": value}}


def _index_row_body(tag: str, cells: list[str]) -> dict:
    return {"path": _node_path(("func", tag)), "name": "funcs", "indices": [0],
            "value": {"kind": "string", "shape": [len(cells)], "data": list(cells)}}


def _matrix_contains(matrix: Any, needle: str) -> bool:
    if not matrix:
        return False
    return any(cell == needle for row in matrix for cell in row)


class _RevisionTracked:
    """Public dispatcher that carries the caller's revision forward exactly as
    a real client must (the probe ``call()`` helper, as a reusable object)."""

    def __init__(self, daemon, project: Mapping, exe: Mapping) -> None:
        self.daemon, self.project, self.exe = daemon, project, dict(exe)

    def call(self, operation: str, body: Mapping, key: str) -> dict:
        result = _call(self.daemon, operation, body, _envelope(self.project, self.exe, key))
        execution = result.get("execution")
        if isinstance(execution, dict) and "session_id" in execution and "model_ref" in execution:
            self.exe = execution
        return result


# ---------------------------------------------------------------------------
# expected RED today: external path-shaped values accepted on public routes
# (desired contract = refused before the fake engine setter is reached)
# ---------------------------------------------------------------------------


def test_property_entry_set_keyed_external_absolute_must_refuse_before_set_entry(tmp_path, monkeypatch):
    """v3 counterexample: node.property_entry_set stores an external absolute
    path into a keyed String entry (StringMatrix-vs-keyed distinction: the
    value is a keyed entry, not an index row)."""
    daemon, project, root, model, _interp, exe = _make_project(tmp_path, "routes-entry", monkeypatch)
    try:
        isolation_calls: list[bool] = []

        def assumed_owned_server():
            isolation_calls.append(True)
            return {"synthetic_owned_server": True}

        monkeypatch.setattr(daemon.backend, "_require_g2_isolation", assumed_owned_server)
        keyed = _keyed_node(model)
        outside = _outside_file(tmp_path, root)
        result = _call(daemon, "node.property_entry_set", _entry_body("keyedA", str(outside)),
                       _envelope(project, exe, "routes-entry-1"))
        assert isolation_calls == [True]
        assert not result["success"] and keyed.entries.get("sol1") is None, {
            "public_result_success": result["success"],
            "fake_worker_setentry_received_external_path": keyed.entries.get("sol1") == str(outside),
            "readback_match": [row.get("readback_match") for row in result["data"].get("applied", [])],
            "isolation_gate_calls": len(isolation_calls),
        }
    finally:
        daemon.close()


def test_property_index_set_matrix_row_external_absolute_must_refuse_before_set_index(tmp_path, monkeypatch):
    """v4 counterexample: a StringMatrix row assignment smuggles the external
    absolute path into one cell; the sibling cell ("x") stays unrelated, which
    is why asserting the whole status is insufficient — the target setter and
    the concrete matrix content are pinned."""
    daemon, project, root, model, _interp, exe = _make_project(tmp_path, "routes-index", monkeypatch)
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation",
                            lambda: {"synthetic_owned_server": True})
        keyed = _keyed_node(model)
        outside = _outside_file(tmp_path, root)
        result = _call(daemon, "node.property_index_set",
                       _index_row_body("keyedA", [str(outside), "x"]),
                       _envelope(project, exe, "routes-index-1"))
        stored = keyed.matrices.get("funcs")
        assert not result["success"] and not _matrix_contains(stored, str(outside)), {
            "public_result_success": result["success"],
            "fake_worker_setindex_received_external_path": _matrix_contains(stored, str(outside)),
            "stored_matrix_row": stored,
        }
    finally:
        daemon.close()


def test_property_set_relative_traversal_must_refuse_before_set(tmp_path, monkeypatch):
    """v3 counterexample (new vs the parent test): a ``..``-shaped relative
    value crosses the project boundary by shape; same no-mutation contract as
    the parent's absolute case."""
    daemon, project, root, model, interp, exe = _make_project(tmp_path, "routes-rel", monkeypatch)
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation",
                            lambda: {"synthetic_owned_server": True})
        outside = _outside_file(tmp_path, root)
        traversal = "../synthetic-other-project/input.dat"
        assert Path(traversal).parts[0] == ".."
        result = _call(daemon, "node.property_set", {
            "path": _node_path(("func", "interpA")),
            "properties": [{"name": "filename",
                            "value": {"kind": "string", "shape": [], "data": traversal}}],
        }, _envelope(project, exe, "routes-rel-1"))
        assert not result["success"] and interp.values.get("filename") is None, {
            "public_result_success": result["success"],
            "fake_worker_set_received_traversal_path": interp.values.get("filename") == traversal,
        }
    finally:
        daemon.close()


def test_transaction_apply_nested_keyed_and_matrix_writes_must_refuse_before_runner(tmp_path, monkeypatch):
    """Nested transaction route: both outside-carrying actions go through the
    same _run_g2_action runner, so today the transaction SUCCEEDS with applied
    rows and the fake node stores the path in the keyed entry AND the matrix
    cell.  A fix must refuse at the single wiring point (or in
    _preflight_nested_actions) with no applied row and no engine mutation."""
    daemon, project, root, model, _interp, exe = _make_project(tmp_path, "routes-txn", monkeypatch)
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation",
                            lambda: {"synthetic_owned_server": True})
        _fake_mph_save(model)
        keyed = _keyed_node(model)
        outside = _outside_file(tmp_path, root)
        client = _RevisionTracked(daemon, project, exe)
        txn = client.call("transaction.apply", {
            "checkpoint_policy": "on_failure",
            "actions": [
                {"operation_id": "node.property_entry_set",
                 "arguments": _entry_body("keyedA", str(outside), key="txnkey")},
                {"operation_id": "node.property_index_set", "arguments": _index_row_body("keyedA", [str(outside), "x"])},
            ],
        }, "routes-txn-apply")
        data = txn.get("data") or {}
        assert not txn["success"], {
            "public_result_success": txn["success"],
            "transaction_status": data.get("status"),
            "applied_action_rows": len(data.get("applied") or []),
            "fake_worker_setentry_received_external_path": keyed.entries.get("txnkey") == str(outside),
            "stored_entry": keyed.entries.get("txnkey"),
            "fake_worker_setindex_received_external_path": _matrix_contains(keyed.matrices.get("funcs"), str(outside)),
            "stored_matrix_row": keyed.matrices.get("funcs"),
        }
        assert keyed.entries.get("txnkey") is None
        assert not _matrix_contains(keyed.matrices.get("funcs"), str(outside))
    finally:
        daemon.close()


def test_transaction_trial_nested_writes_must_not_reach_the_trial_engine_setter(tmp_path, monkeypatch):
    """Trial nesting is traceable with an extended fake (backend_snapshot,
    client tags/load/remove; load clones the model).  Today the envelope is
    honest about uncertainty (EXECUTION_STATE_UNKNOWN because keyed readback
    scope is not covered), but the nested actions ARE dispatched to the trial
    copy before that post-check: the captured trial state proves the fake
    setEntry/setIndex received the outside path.  The main model staying
    untouched is pinned as already-true; the trial-setter no-mutation part is
    the expected RED.  This test deliberately does NOT claim the trial route
    reported success — it pins that the outside path reached an engine setter
    at all, which a pre-write refusal (review S1) must stop."""
    daemon, project, root, model, _interp, exe = _make_project(tmp_path, "routes-trial", monkeypatch)
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation",
                            lambda: {"synthetic_owned_server": True})
        _fake_mph_save(model)
        # replace ONLY the worker surface the trial route touches
        worker = TrialWorker(daemon.backend.worker, model)
        monkeypatch.setattr(daemon.backend, "worker", worker)
        keyed = _keyed_node(model, seed_funcs=True)
        outside = _outside_file(tmp_path, root)
        client = _RevisionTracked(daemon, project, exe)
        ck = client.call("checkpoint.create", {"label": "trial-source"}, "routes-trial-ck")
        assert ck["success"], ck
        tr = client.call("transaction.trial", {
            "checkpoint_id": ck["data"]["checkpoint_id"],
            "actions": [
                {"operation_id": "node.property_entry_set", "arguments": _entry_body("keyedA", str(outside), key="trialkey")},
                {"operation_id": "node.property_index_set", "arguments": _index_row_body("keyedA", [str(outside), "x"])},
            ],
        }, "routes-trial-1")
        # already-true today and must stay true: the main model received nothing
        assert keyed.entries.get("trialkey") is None
        assert keyed.matrices.get("funcs") == [["placeholder"]]
        # Today the scoped readback is honestly UNKNOWN. A boundary preflight
        # can instead refuse with ACCESS_VIOLATION before creating a copy.
        assert not tr["success"]
        assert _error_code(tr) in {"EXECUTION_STATE_UNKNOWN", "ACCESS_VIOLATION"}, tr
        # expected RED today: what the trial engine held before cleanup
        captured = [state["func"]["keyedA"] for state in worker.removed_state.values()]
        trial_held_entry = any(row["entries"].get("trialkey") == str(outside) for row in captured)
        trial_held_matrix = any(_matrix_contains(row["matrices"].get("funcs"), str(outside)) for row in captured)
        assert not trial_held_entry and not trial_held_matrix, {
            "trial_envelope_code": _error_code(tr),
            "trial_scope_limitations": ((tr.get("data") or {}).get("main_model_scope") or {}).get("limitations"),
            "trial_copy_received_entry_path": trial_held_entry,
            "trial_copy_received_matrix_cell_path": trial_held_matrix,
        }
    finally:
        daemon.close()


# ---------------------------------------------------------------------------
# expected GREEN: capability that must keep working (no over-blocking, no
# name-whitelisting); these also document that today's refusal story is the
# isolation gate and unknown-metadata fail-closed only
# ---------------------------------------------------------------------------


def test_plain_unrelated_strings_still_apply_on_all_three_typed_routes(tmp_path, monkeypatch):
    """Positive control from probe v4: 'linear', 'f1', 'm/s^-1', 'file' carry
    no path shape and must apply verbatim on property_set, entry_set and
    index_set — before and after any future fix (no blanket string ban)."""
    daemon, project, root, model, interp, exe = _make_project(tmp_path, "routes-ok", monkeypatch)
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation",
                            lambda: {"synthetic_owned_server": True})
        keyed = _keyed_node(model)
        client = _RevisionTracked(daemon, project, exe)
        r1 = client.call("node.property_set", {
            "path": _node_path(("func", "interpA")),
            "properties": [
                {"name": "interp", "value": {"kind": "string", "shape": [], "data": "linear"}},
                {"name": "funcname", "value": {"kind": "string", "shape": [], "data": "f1"}},
                {"name": "dseparator", "value": {"kind": "string", "shape": [], "data": "m/s^-1"}},
                {"name": "source", "value": {"kind": "string", "shape": [], "data": "file"}},
            ]}, "routes-ok-strings")
        assert r1["success"], r1
        assert interp.values["interp"] == "linear"
        assert interp.values["funcname"] == "f1"
        assert interp.values["dseparator"] == "m/s^-1"
        assert interp.values["source"] == "file"
        r2 = client.call("node.property_entry_set", _entry_body("keyedA", "f1"), "routes-ok-entry")
        assert r2["success"], r2
        assert keyed.entries["sol1"] == "f1"
        r3 = client.call("node.property_index_set", _index_row_body("keyedA", ["m/s^-1", "x"]),
                         "routes-ok-index")
        assert r3["success"], r3
        assert keyed.matrices["funcs"] == [["m/s^-1", "x"]]
    finally:
        daemon.close()


def test_in_project_absolute_filename_remains_usable_today(tmp_path, monkeypatch):
    """Probe v4 fact (today AND under the proposed S2 policy): a FILE-shaped
    absolute value inside the approved project root currently applies through
    node.property_set with readback match; the single-point gate must not
    regress legitimate in-project writes."""
    daemon, project, root, _model, interp, exe = _make_project(tmp_path, "routes-inside", monkeypatch)
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation",
                            lambda: {"synthetic_owned_server": True})
        inside = root / "inputs" / "legit_routes.dat"
        inside.parent.mkdir(parents=True, exist_ok=True)
        inside.write_text("0.0 1.0\n")
        result = _call(daemon, "node.property_set", {
            "path": _node_path(("func", "interpA")),
            "properties": [{"name": "filename",
                            "value": {"kind": "string", "shape": [], "data": str(inside)}}],
        }, _envelope(project, exe, "routes-inside-1"))
        assert result["success"], result
        assert interp.values["filename"] == str(inside)
    finally:
        daemon.close()


def test_entry_and_index_routes_without_isolation_proof_refuse_before_any_setter(tmp_path, monkeypatch):
    """Without the stubbed owned-server proof the ordinary project_write
    isolation gate refuses both newer routes BEFORE any engine call — today's
    only real protection; the single-point file gate must not replace or
    weaken it."""
    daemon, project, root, model, _interp, exe = _make_project(tmp_path, "routes-gate", monkeypatch)
    try:
        keyed = _keyed_node(model)
        outside = _outside_file(tmp_path, root)
        r1 = _call(daemon, "node.property_entry_set", _entry_body("keyedA", str(outside)),
                   _envelope(project, exe, "routes-gate-entry"))
        assert not r1["success"]
        assert _error_code(r1) == "ISOLATION_PROOF_REQUIRED"
        r2 = _call(daemon, "node.property_index_set", _index_row_body("keyedA", [str(outside), "x"]),
                   _envelope(project, exe, "routes-gate-index"))
        assert not r2["success"]
        assert _error_code(r2) == "ISOLATION_PROOF_REQUIRED"
        assert keyed.entries.get("sol1") is None
        assert keyed.matrices.get("funcs") is None
    finally:
        daemon.close()


def test_unknown_metadata_property_stays_pre_refused_api_unsupported(tmp_path, monkeypatch):
    """v3 fact (fail-closed precedent to preserve): a property with no
    authoritative engine metadata is refused API_UNSUPPORTED at validation —
    the future path-shape gate must keep this, and must not let an explicit
    java_signature route bypass file adjudication (contract pinned in S4)."""
    daemon, project, root, _model, interp, exe = _make_project(tmp_path, "routes-unknown", monkeypatch)
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation",
                            lambda: {"synthetic_owned_server": True})
        outside = _outside_file(tmp_path, root)
        result = _call(daemon, "node.property_set", {
            "path": _node_path(("func", "interpA")),
            "properties": [{"name": "importedname",
                            "value": {"kind": "string", "shape": [], "data": str(outside)}}],
        }, _envelope(project, exe, "routes-unknown-1"))
        assert not result["success"]
        assert _error_code(result) == "API_UNSUPPORTED"
        assert interp.values.get("importedname") is None
    finally:
        daemon.close()


def test_runtime_value_type_alone_does_not_distinguish_file_semantics(tmp_path, monkeypatch):
    """In this fake, both properties expose the same String runtime type.

    This does not forbid a future schema from providing additional justified
    file semantics, nor assert that a documentation table is exhaustive.
    """
    daemon, project, _root, _model, _interp, exe = _make_project(tmp_path, "routes-meta", monkeypatch)
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation",
                            lambda: {"synthetic_owned_server": True})
        schema = _call(daemon, "node.property_schema",
                       {"path": _node_path(("func", "interpA"))}, _envelope(project, exe, "routes-meta-1"))
        assert schema["success"], schema
        rows = {row["name"]: row for row in schema["data"]["properties"]}
        filename_row, interp_row = rows["filename"], rows["interp"]
        assert filename_row["kind"] == interp_row["kind"] == "string"
        assert filename_row.get("value_type") == interp_row.get("value_type") == "String"
        assert filename_row.get("metadata_status") == interp_row.get("metadata_status") == "KNOWN"
    finally:
        daemon.close()
