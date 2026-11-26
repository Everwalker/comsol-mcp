"""T20 generic typed-property path-shape screen — mitigation slice tests (2026-11-08).

Execution model: omlx/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S (local route, whole task).

These tests pin the CONTRACT of the explicitly PARTIAL path-shape mitigation
wired at the single ``_run_g2_action`` point plus the nested preflight, as
authorized by ``T20-generic-property-parent-adjudication-20261108.md`` and
designed as S1 in ``T20-generic-property-route-review-20261108.md``.  Like
the parent tests, owner isolation is stubbed to model an already-owned
server while project authority, registry admission, the managed backend,
the write service and the property adapter are real; the fake Worker proves
control-side ordering, never native COMSOL behavior.

Pinned here (must be GREEN):
* refusal happens BEFORE any engine setter (zero mutation), with the proven
  REFUSED envelope (validation stage + witness mutation_issued=false) on the
  direct routes;
* all-or-nothing: a multi-property write whose first item is legal and
  second item crosses stores NOTHING; a nested transaction whose first
  action is legal and second crosses is refused in preflight with zero
  applied rows and no checkpoint/trial copy;
* an explicit ``java_signature`` (EXPLICIT metadata route) cannot bypass the
  screen;
* fail-closed when the host/path relationship cannot be established: a
  non-loopback engine endpoint refuses path-shaped values even when the
  value is inside the project, while plain strings keep applying;
* caller-forged roots/providers inside the body are never consulted;
* the screen accepts proven in-project plain absolutes (covered by the
  parent matrix) and ordinary text (units, expressions, wire payloads), but
  refuses NUL bytes, Windows drive/UNC shapes on this POSIX host, and any
  real ``..`` segment — even a lexically in-project relative traversal —
  because the engine resolves relative values against its own (unverified)
  working directory;
* in-project absolutes whose existing components include a symlink, or
  whose existing target has a filesystem link count other than one, are
  refused by the no-follow lstat scan (never opening content, never
  touching anything outside the root).

Deliberately NOT pinned closed (recorded open, must stay visible):
* the bare relative filename ``f1`` — see
  ``test_bare_relative_name_stays_undecided_documented_open`` below, which
  asserts the documented current behavior (the screen does not touch plain
  relative names) without claiming that this is safe; the parent
  ``test_unbound_engine_cwd_cannot_turn_bare_filename_into_external_authority``
  stays RED by design;
* TOCTOU after the scan, engine-side reparse/re-resolution before use,
  native Windows behavior, and any complete file/non-file classification or
  UNKNOWN fail-closed claim.
"""
from __future__ import annotations

import os
from pathlib import Path

from comsol_mcp._g2_property_path_guard import (SHAPE_DOTDOT_TRAVERSAL,
                                                SHAPE_POSIX_ABSOLUTE,
                                                adjudicate_path_leaf,
                                                classify_string_leaf)
from tests.test_t20_artifact_boundary import (_call, _envelope, _error_code,
                                              _make_project, _node_path)
from tests.test_t20_generic_property_routes_20261108 import (_entry_body,
                                                             _fake_mph_save,
                                                             _index_row_body,
                                                             _keyed_node,
                                                             _matrix_contains,
                                                             _outside_file,
                                                             _RevisionTracked,
                                                             TrialWorker)


def _props(name: str, value: str) -> dict:
    return {"path": _node_path(("func", "interpA")),
            "properties": [{"name": name,
                            "value": {"kind": "string", "shape": [], "data": value}}]}


def _refusal_shape(result) -> None:
    """A direct-route refusal must be proven, not merely not-success."""
    assert not result["success"], result
    assert _error_code(result) == "ACCESS_VIOLATION", result
    assert result["data"]["status"] == "REFUSED", result
    assert result["data"]["refused"] is True, result
    assert result["data"]["witness"]["mutation_issued"] is False, result
    assert result["error"]["stage"] == "validation", result


# ---------------------------------------------------------------------------
# no mutation before the refusal; all-or-nothing semantics
# ---------------------------------------------------------------------------


def test_multi_property_first_legal_second_crossing_stores_nothing(tmp_path, monkeypatch):
    """A crossing item in the same call must not let the earlier legal item
    mutate the engine first: the whole property_set is refused pre-dispatch."""
    daemon, project, root, _model, interp, exe = _make_project(tmp_path, "pg-multi", monkeypatch)
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation",
                            lambda: {"synthetic_owned_server": True})
        outside = _outside_file(tmp_path, root)
        inside = root / "inputs" / "pglegit.dat"
        inside.parent.mkdir(parents=True, exist_ok=True)
        inside.write_text("0.0 1.0\n")
        result = _call(daemon, "node.property_set", {
            "path": _node_path(("func", "interpA")),
            "properties": [
                {"name": "interp", "value": {"kind": "string", "shape": [], "data": "linear"}},
                {"name": "filename", "value": {"kind": "string", "shape": [], "data": str(inside)}},
                {"name": "source", "value": {"kind": "string", "shape": [], "data": str(outside)}},
            ]}, _envelope(project, exe, "pg-multi-1"))
        _refusal_shape(result)
        assert interp.values.get("interp") is None, "first legal property must NOT have applied"
        assert interp.values.get("filename") is None
        assert interp.values.get("source") is None
    finally:
        daemon.close()


def test_direct_entry_set_route_refuses_external_shape_before_set_entry(tmp_path, monkeypatch):
    """The direct (single-action) ``node.property_entry_set`` route is wired
    through the same screen: an external absolute entry value is refused with
    the proven pre-dispatch envelope and the keyed node stores nothing, while
    a plain relative entry value keeps applying on the same route."""
    daemon, project, root, model, _interp, exe = _make_project(tmp_path, "pg-entd", monkeypatch)
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation",
                            lambda: {"synthetic_owned_server": True})
        keyed = _keyed_node(model)
        outside = _outside_file(tmp_path, root)
        client = _RevisionTracked(daemon, project, exe)
        blocked = client.call("node.property_entry_set",
                              _entry_body("keyedA", str(outside), key="crosskey"), "pg-entd-1")
        _refusal_shape(blocked)
        assert "crosskey" not in keyed.entries, keyed.entries
        ok = client.call("node.property_entry_set",
                         _entry_body("keyedA", "f1", key="plainkey"), "pg-entd-2")
        assert ok["success"], ok
        assert keyed.entries.get("plainkey") == "f1", keyed.entries
    finally:
        daemon.close()


def test_nested_transaction_first_legal_second_crossing_zero_mutation_no_checkpoint(tmp_path, monkeypatch):
    """transaction.apply must refuse in preflight: no applied row, no engine
    mutation for either action (the legal first one included), and no
    before-checkpoint file created."""
    daemon, project, root, model, _interp, exe = _make_project(tmp_path, "pg-txn", monkeypatch)
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation",
                            lambda: {"synthetic_owned_server": True})
        _fake_mph_save(model)  # capability present; must NOT be used
        keyed = _keyed_node(model)
        outside = _outside_file(tmp_path, root)
        client = _RevisionTracked(daemon, project, exe)
        txn = client.call("transaction.apply", {
            "checkpoint_policy": "on_failure",
            "actions": [
                {"operation_id": "node.property_entry_set",
                 "arguments": _entry_body("keyedA", "f1", key="legalkey")},
                {"operation_id": "node.property_entry_set",
                 "arguments": _entry_body("keyedA", str(outside), key="crosskey")},
            ],
        }, "pg-txn-apply")
        assert not txn["success"], txn
        assert _error_code(txn) == "ACCESS_VIOLATION", txn
        data = txn.get("data") or {}
        assert not (data.get("applied") or []), data
        # neither the legal nor the crossing action was dispatched
        assert keyed.entries.get("legalkey") is None
        assert keyed.entries.get("crosskey") is None
        checkpoint_dir = daemon.backend.project_root / "g2_artifacts" / "checkpoints"
        assert not checkpoint_dir.exists() or not list(checkpoint_dir.glob("*.mph"))
    finally:
        daemon.close()


def test_trial_preflight_refuses_before_copy_with_access_violation(tmp_path, monkeypatch):
    """The trial route must stop at the preflight (ACCESS_VIOLATION), not at
    the later scoped readback UNKNOWN: no copy is loaded, nothing is removed
    afterwards, and the trial artifact is never produced."""
    daemon, project, root, model, _interp, exe = _make_project(tmp_path, "pg-trial", monkeypatch)
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation",
                            lambda: {"synthetic_owned_server": True})
        _fake_mph_save(model)
        worker = TrialWorker(daemon.backend.worker, model)
        monkeypatch.setattr(daemon.backend, "worker", worker)
        keyed = _keyed_node(model, seed_funcs=True)
        outside = _outside_file(tmp_path, root)
        client = _RevisionTracked(daemon, project, exe)
        ck = client.call("checkpoint.create", {"label": "trial-source"}, "pg-trial-ck")
        assert ck["success"], ck
        tr = client.call("transaction.trial", {
            "checkpoint_id": ck["data"]["checkpoint_id"],
            "actions": [
                {"operation_id": "node.property_index_set",
                 "arguments": _index_row_body("keyedA", ["m/s^-1", "x"])},
                {"operation_id": "node.property_entry_set",
                 "arguments": _entry_body("keyedA", str(outside), key="trialkey")},
            ],
        }, "pg-trial-1")
        assert not tr["success"]
        assert _error_code(tr) == "ACCESS_VIOLATION", tr
        assert keyed.entries.get("trialkey") is None
        assert keyed.matrices.get("funcs") == [["placeholder"]]
        assert worker.removed_state == {}  # the trial model was never loaded
        trials_dir = daemon.backend.project_root / "g2_artifacts" / "trials"
        assert not trials_dir.exists() or not list(trials_dir.glob("*.mph"))
    finally:
        daemon.close()


# ---------------------------------------------------------------------------
# bypass and fail-closed boundaries
# ---------------------------------------------------------------------------


def test_explicit_java_signature_route_cannot_bypass_path_screen(tmp_path, monkeypatch):
    """An explicit Java signature selects a type; it grants no path authority.

    Two layers are pinned: (1) today's adapter already refuses an
    unknown-metadata property admitted via an explicit signature before any
    setter, because no authoritative readback getter exists; (2) the path
    screen itself does not let a signature bypass adjudication on a KNOWN
    property, so a future schema that ever trusts explicit getters cannot
    silently re-open the crossing write.
    """
    daemon, project, root, _model, interp, exe = _make_project(tmp_path, "pg-sig", monkeypatch)
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation",
                            lambda: {"synthetic_owned_server": True})
        outside = _outside_file(tmp_path, root)
        unknown_metadata = _call(daemon, "node.property_set", {
            "path": _node_path(("func", "interpA")),
            "properties": [{"name": "importedname",
                            "value": {"kind": "string", "shape": [], "data": str(outside),
                                      "java_signature": "java.lang.String"}}]},
            _envelope(project, exe, "pg-sig-1"))
        assert not unknown_metadata["success"], unknown_metadata
        assert _error_code(unknown_metadata) in {"API_UNSUPPORTED", "ACCESS_VIOLATION"}, unknown_metadata
        assert interp.values.get("importedname") is None

        known_with_signature = _call(daemon, "node.property_set", {
            "path": _node_path(("func", "interpA")),
            "properties": [{"name": "filename",
                            "value": {"kind": "string", "shape": [], "data": str(outside),
                                      "java_signature": "java.lang.String"}}]},
            _envelope(project, exe, "pg-sig-2"))
        _refusal_shape(known_with_signature)
        assert interp.values.get("filename") is None
    finally:
        daemon.close()


def test_nonloopback_engine_host_refuses_path_shapes_even_inside_project(tmp_path, monkeypatch):
    """Host/path relationship unprovable ⇒ fail-closed for path-shaped
    values (even an in-project absolute), while plain strings keep applying
    on the same route — no blanket string ban."""
    daemon, project, root, _model, interp, exe = _make_project(tmp_path, "pg-host", monkeypatch)
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation",
                            lambda: {"synthetic_owned_server": True})
        inside = root / "inputs" / "pginside.dat"
        inside.parent.mkdir(parents=True, exist_ok=True)
        inside.write_text("0.0 1.0\n")
        daemon.backend.endpoint_key = "192.0.2.9:2036"
        blocked = _call(daemon, "node.property_set", _props("filename", str(inside)),
                        _envelope(project, exe, "pg-host-blocked"))
        _refusal_shape(blocked)
        assert blocked["error"]["details"]["findings"][0]["reason"] == "adjudication_unavailable"
        assert interp.values.get("filename") is None
        allowed = _call(daemon, "node.property_set", _props("interp", "linear"),
                        _envelope(project, exe, "pg-host-plain"))
        assert allowed["success"], allowed
        assert interp.values["interp"] == "linear"
    finally:
        daemon.close()


def test_caller_supplied_root_or_provider_fields_are_never_consulted(tmp_path, monkeypatch):
    """Smuggling a wider 'root' (or a provider/screen field) in the request
    body must not move the boundary: the external value stays refused and
    nothing is stored, whatever the admission layer reports."""
    daemon, project, root, _model, interp, exe = _make_project(tmp_path, "pg-forge", monkeypatch)
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation",
                            lambda: {"synthetic_owned_server": True})
        outside = _outside_file(tmp_path, root)
        body = _props("filename", str(outside))
        body["project_root"] = str(tmp_path)      # forged wider "root"
        body["path_guard"] = "always-allow"        # forged capability name
        result = _call(daemon, "node.property_set", body, _envelope(project, exe, "pg-forge-1"))
        assert not result["success"], result
        assert _error_code(result) in {"INVALID_REQUEST", "ACCESS_VIOLATION"}, result
        assert interp.values.get("filename") is None
    finally:
        daemon.close()


# ---------------------------------------------------------------------------
# positive controls: no over-blocking, plus the no-follow component rules
# ---------------------------------------------------------------------------


def test_ordinary_text_and_relative_without_traversal_still_apply(tmp_path, monkeypatch):
    """Units, expression-ish text, wire payloads, bare names and plain
    relative values (undecided by design) all apply verbatim — the screen is
    shape-based, not a blanket string ban and not a name whitelist."""
    daemon, project, root, _model, interp, exe = _make_project(tmp_path, "pg-plain", monkeypatch)
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation",
                            lambda: {"synthetic_owned_server": True})
        result = _call(daemon, "node.property_set", {
            "path": _node_path(("func", "interpA")),
            "properties": [
                {"name": "dseparator", "value": {"kind": "string", "shape": [], "data": "m/s^-1"}},
                {"name": "funcname", "value": {"kind": "string", "shape": [], "data": "f1"}},
                {"name": "source", "value": {"kind": "string", "shape": [], "data": "file"}},
                {"name": "interp", "value": {"kind": "string", "shape": [], "data": "if(cond,1,2)"}},
                {"name": "struct", "value": {"kind": "string", "shape": [], "data": "0 1\n2 3"}},
                {"name": "argunit", "value": {"kind": "string", "shape": [], "data": "inputs/data.dat"}},
            ]}, _envelope(project, exe, "pg-plain-1"))
        assert result["success"], result
        assert interp.values["dseparator"] == "m/s^-1"
        assert interp.values["funcname"] == "f1"
        assert interp.values["argunit"] == "inputs/data.dat"
        assert interp.values["interp"] == "if(cond,1,2)"
    finally:
        daemon.close()


def test_relative_traversal_refused_even_if_lexically_in_project(tmp_path, monkeypatch):
    """A real ``..`` segment escapes whatever directory the engine resolves
    relative values against (that base is unverified), so it is refused even
    when the collapsed lexical result would sit inside the project."""
    daemon, project, root, _model, interp, exe = _make_project(tmp_path, "pg-dotdot", monkeypatch)
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation",
                            lambda: {"synthetic_owned_server": True})
        # ``Interpolation.filename`` relative values are now refused earlier by
        # the T19.a known-file gate (pinned by
        # tests/test_t19a_import_filename_semantics_20261123.py); this pin of
        # the shape screen therefore uses the non-known-file ``source``
        # property, which reaches the same traversal refusal unchanged.
        result = _call(daemon, "node.property_set",
                       _props("source", "inputs/../inputs/x.dat"),
                       _envelope(project, exe, "pg-dotdot-1"))
        _refusal_shape(result)
        assert result["error"]["details"]["findings"][0]["reason"] == "relative_traversal_undecidable"
        assert interp.values.get("source") is None
    finally:
        daemon.close()


def test_windows_drive_unc_and_nul_shapes_refused_on_this_host(tmp_path, monkeypatch):
    daemon, project, root, _model, interp, exe = _make_project(tmp_path, "pg-shapes", monkeypatch)
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation",
                            lambda: {"synthetic_owned_server": True})
        for index, value in enumerate(["C:\\synthetic\\input.dat",
                                       "\\\\synthetic-server\\share\\input.dat",
                                       "evil\x00.dat"]):
            result = _call(daemon, "node.property_set", _props("filename", value),
                           _envelope(project, exe, f"pg-shapes-{index}"))
            _refusal_shape(result)
        assert interp.values.get("filename") is None
    finally:
        daemon.close()


def test_in_project_symlink_component_and_multi_link_target_refused(tmp_path, monkeypatch):
    """The scan is no-follow lstat over components strictly below the
    approved root (content is never opened, nothing outside the root is
    touched): a symlinked component and an existing multiple-link target are
    refused; the accepted T20 hardlink/digest single-link rule is reused."""
    daemon, project, root, _model, interp, exe = _make_project(tmp_path, "pg-link", monkeypatch)
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation",
                            lambda: {"synthetic_owned_server": True})
        private = tmp_path / "pg-link-private.dat"
        private.write_text("SENTINEL-PG-LINK\n")
        link = root / "link.dat"
        link.symlink_to(private)
        result = _call(daemon, "node.property_set", _props("filename", str(link)),
                       _envelope(project, exe, "pg-link-sym"))
        _refusal_shape(result)
        assert result["error"]["details"]["findings"][0]["reason"] == "symlink_component"
        assert interp.values.get("filename") is None

        a = root / "inputs" / "a.dat"
        a.parent.mkdir(parents=True, exist_ok=True)
        a.write_text("0.0 1.0\n")
        b = root / "inputs" / "b.dat"
        os.link(a, b)
        result = _call(daemon, "node.property_set", _props("filename", str(a)),
                       _envelope(project, exe, "pg-link-nlink"))
        _refusal_shape(result)
        assert result["error"]["details"]["findings"][0]["reason"] == "multiple_link_target"
        assert interp.values.get("filename") is None
    finally:
        daemon.close()


def test_matrix_entry_string_leaves_screened_in_arrays(tmp_path, monkeypatch):
    """Array/matrix string leaves are screened leaf-wise: a legal row stays
    applicable afterwards, a matrix row smuggling the outside cell is
    refused with the target setter never reached."""
    daemon, project, root, model, _interp, exe = _make_project(tmp_path, "pg-matrix", monkeypatch)
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation",
                            lambda: {"synthetic_owned_server": True})
        keyed = _keyed_node(model, seed_funcs=True)
        outside = _outside_file(tmp_path, root)
        blocked = _call(daemon, "node.property_index_set",
                        _index_row_body("keyedA", ["x", str(outside)]),
                        _envelope(project, exe, "pg-matrix-blocked"))
        assert not blocked["success"]
        assert _error_code(blocked) == "ACCESS_VIOLATION", blocked
        assert not _matrix_contains(keyed.matrices.get("funcs"), str(outside))
        assert keyed.matrices.get("funcs") == [["placeholder"]]
        ok = _call(daemon, "node.property_index_set",
                   _index_row_body("keyedA", ["m/s^-1", "x"]),
                   _envelope(project, exe, "pg-matrix-ok"))
        assert ok["success"], ok
        assert keyed.matrices["funcs"] == [["m/s^-1", "x"]]
    finally:
        daemon.close()


# ---------------------------------------------------------------------------
# documented-open: the mitigation is partial, and this file pins that fact
# ---------------------------------------------------------------------------


def test_bare_relative_name_open_scope_and_known_file_gate(tmp_path, monkeypatch):
    """Documented open scope for generic string properties stays visible: the
    screen does NOT decide bare relative names (the file/non-file
    classification problem), so a plain ``f1`` on a non-known-file property
    still reaches the fake setter.  For the finite known file-backed pairs
    (Interpolation.filename) the T19.a gate now refuses any relative value
    before the screen and setter, closing the old documented-open case there
    (the gate itself is pinned by tests/test_t19a_import_filename_semantics_20261123.py)."""
    daemon, project, root, _model, interp, exe = _make_project(tmp_path, "pg-open", monkeypatch)
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation",
                            lambda: {"synthetic_owned_server": True})
        gated = _call(daemon, "node.property_set", _props("filename", "f1"),
                      _envelope(project, exe, "pg-open-gated"))
        _refusal_shape(gated)
        assert gated["error"]["details"]["mitigation"] == "t19a-known-file-semantics"
        assert interp.values.get("filename") is None

        # ordered after the refusal so the revision is still the envelope's
        kept = _call(daemon, "node.property_set", _props("argunit", "f1"),
                     _envelope(project, exe, "pg-open-kept"))
        assert kept["success"], kept       # undecided for generic properties
        assert interp.values["argunit"] == "f1"
    finally:
        daemon.close()


# ---------------------------------------------------------------------------
# pure helper unit controls (no routes)
# ---------------------------------------------------------------------------


def test_pure_classifier_and_adjudicator_semantics(tmp_path):
    assert classify_string_leaf("linear") is None
    assert classify_string_leaf("m/s^-1") is None
    assert classify_string_leaf("f1") is None
    assert classify_string_leaf("a..b") is None  # not a ".." segment
    assert classify_string_leaf("../x") == SHAPE_DOTDOT_TRAVERSAL
    assert classify_string_leaf("a/../b") == SHAPE_DOTDOT_TRAVERSAL
    assert classify_string_leaf("/x/y") == SHAPE_POSIX_ABSOLUTE
    # A POSIX-absolute adjudication walks components under a pinned no-follow
    # binding; when the caller passes none the walk self-opens the approved
    # root (the production route pins the binding itself), so the fixture
    # root must be a real openable directory.
    root = str(Path(tmp_path, "approved", "project").resolve())
    os.makedirs(os.path.join(root, "inputs"), exist_ok=True)
    inside = os.path.join(root, "inputs", "data.dat")
    allowed, reason = adjudicate_path_leaf(inside, SHAPE_POSIX_ABSOLUTE,
                                           project_root=root, engine_host_proven=True)
    # missing components stop the scan: a not-yet-existing write target is
    # still adjudicated from its plain existing prefix (here: the components
    # down to ``inputs`` exist, the file itself does not).
    assert allowed and reason == "inside_project_boundary"
    allowed, reason = adjudicate_path_leaf(inside, SHAPE_POSIX_ABSOLUTE,
                                           project_root=root, engine_host_proven=False)
    assert not allowed and reason == "adjudication_unavailable"  # host unproven → fail closed
    allowed, reason = adjudicate_path_leaf("/other/project/x.dat", SHAPE_POSIX_ABSOLUTE,
                                           project_root=root, engine_host_proven=True)
    assert not allowed and reason == "outside_project_boundary"
    allowed, reason = adjudicate_path_leaf("/x", SHAPE_POSIX_ABSOLUTE,
                                           project_root=None, engine_host_proven=True)
    assert not allowed and reason == "adjudication_unavailable"
