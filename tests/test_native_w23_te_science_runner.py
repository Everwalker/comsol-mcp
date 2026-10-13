from __future__ import annotations

import json
import copy
from pathlib import Path

import pytest

from comsol_mcp._java_worker import PersistentJavaWorker
from tools import run_native_w23_te_science as runner


def _study_inventory(*, sequence_layout: tuple[tuple[str, ...], ...] = (("bmaInput", "bmaOutput", "freq"),)):
    steps = [
        {"tag": "bmaInput", "feature_type": "BoundaryModeAnalysis", "PortName": "1", "modeFreq": "f0"},
        {"tag": "bmaOutput", "feature_type": "BoundaryModeAnalysis", "PortName": "2", "modeFreq": "f0"},
        {"tag": "freq", "feature_type": "Frequency", "plist": "f0"},
    ]
    sequences = []
    for index, tags in enumerate(sequence_layout, 1):
        sequences.append({
            "tag": f"sol{index}",
            "study_step_bindings_in_solver_tree_order": [
                {"feature_type": "StudyStep", "study": "std1", "studystep": tag,
                 "path": f"st{index}/{tag}"}
                for tag in tags
            ],
        })
    return {"study_steps_in_configured_order": steps,
            "solver_sequences_for_study": sequences}


def _dataset(tag: str, solution: str, *, mode_axis: bool):
    names = ["freq", "modeIndex"] if mode_axis else ["freq"]
    values = [193.414489032258, 1] if mode_axis else [193.414489032258]
    units = ["THz", ""] if mode_axis else ["THz"]
    return (
        {"tag": tag, "type_id": "Solution", "component": "comp1",
         "geometry": "geom1", "solution": solution},
        {"binding_complete": True, "solution": solution,
         "parameters": {"by_pair": {"1:1": {
             "names": names, "values": values, "units": units, "solnum": 1}}}},
    )


def _mesh_readback(level: str = "coarse"):
    hmax = "0.16[um]" if level == "coarse" else "0.08[um]"
    return {
        "mesh_level": level,
        "hmax": hmax,
        "hmin": "0.04[um]",
        "elements": 1200,
        "tri_elements": 1200,
        "element_types": ["tri"],
        "mesh_is_empty": False,
        "mesh_is_complete": True,
        "free_tri_tag": "ftri1",
        "free_tri_built": True,
        "free_tri_selection_dimension": 2,
        "free_tri_selected_domains": [1, 2, 3, 4, 5, 6],
        "geometry_domain_count": 6,
        "study_or_solver_invoked": False,
    }


def test_native_mesh_readback_requires_full_domain_freetri_and_frozen_sizes():
    coarse = runner.validate_mesh_readback(_mesh_readback(), "coarse")
    fine = runner.validate_mesh_readback(_mesh_readback("fine"), "fine")
    assert coarse["status"] == "PASS_NATIVE_FULL_DOMAIN_FREETRI_MESH_READBACK"
    assert coarse["triangle_element_count"] > 0
    assert coarse["full_2d_domain_coverage_by_selection_and_count"] is True
    assert fine["actual_hmax"] == "0.08[um]"
    assert fine["actual_hmin"] == "0.04[um]"


def test_native_mesh_readback_rejects_activity005_actual_zero_element_readback():
    # Activity005 reported a real integer zero after a successful set_mesh Worker
    # operation. That remains a failure fixture, not evidence of an empty mesh
    # being scientifically acceptable.
    actual_activity005_readback = {
        "elements": 0,
        "hmax": "0.16[um]",
        "hmin": "0.04[um]",
        "mesh_level": "coarse",
        "study_or_solver_invoked": False,
    }
    with pytest.raises(ValueError, match="total element count must be a positive integer"):
        runner.validate_mesh_readback(actual_activity005_readback, "coarse")


@pytest.mark.parametrize("change, message", [
    (lambda row: row.update(elements=0), "total element count"),
    (lambda row: row.update(elements=True), "total element count"),
    (lambda row: row.update(tri_elements=0), "FreeTri element count"),
    (lambda row: row.update(tri_elements=1201), "FreeTri element count"),
    (lambda row: row.update(mesh_is_empty=True), "isEmpty"),
    (lambda row: row.update(mesh_is_complete=False), "isComplete"),
    (lambda row: row.update(free_tri_built=False), "FreeTri generator"),
    (lambda row: row.update(free_tri_selection_dimension=1), "dimension-2"),
    (lambda row: row.update(free_tri_selected_domains=[1, 2]), "full 2D geometry domain count"),
    (lambda row: row.update(free_tri_selected_domains=[1, 2, 3, 4, 5, 5]), "unique"),
    (lambda row: row.update(element_types=["quad"]), "include the FreeTri triangle type"),
    (lambda row: row.update(hmax="0.20[um]"), "hmax/hmin"),
    (lambda row: row.update(hmin="0.02[um]"), "hmax/hmin"),
    (lambda row: row.update(study_or_solver_invoked=True), "no-study/no-solver"),
])
def test_native_mesh_readback_fails_closed_on_incomplete_or_mismatched_evidence(change, message):
    readback = _mesh_readback()
    change(readback)
    with pytest.raises(ValueError, match=message):
        runner.validate_mesh_readback(readback, "coarse")


def test_science_fixture_uses_explicit_full_domain_freetri_and_native_mesh_statistics():
    assert runner.CANDIDATE_ID == "W23-TE-PLANAR-TE-SCIENCE-17"
    fixture_source = (runner.REPO / "tools/java/NativeW23TEScienceFixture.java").read_text()
    assert 'mesh.create("ftri1", "FreeTri")' in fixture_source
    assert "freeTri.selection().geom(2);" in fixture_source
    assert "freeTri.selection().all()" in fixture_source
    assert fixture_source.index("freeTri.selection().geom(2);") < fixture_source.index("freeTri.selection().all();")
    assert "freeTri.selection().entities(2)" in fixture_source
    assert 'mesh.getNumElem("tri")' in fixture_source
    assert "mesh.isEmpty()" in fixture_source
    assert "mesh.isComplete()" in fixture_source


def test_study_call_plan_separates_four_submissions_from_configured_steps():
    plan = runner.planned_study_calls()
    assert plan["study_run_submission_count_max"] == 4
    assert [row["case_id"] for row in plan["study_run_submissions"]] == [
        "coarse-phase0", "coarse-phase90", "fine-phase0", "fine-phase90"]
    assert all(row["method"] == "model.study('std1').run()" for row in plan["study_run_submissions"])
    assert all(row["separate_bma_or_frequency_study_run_submissions"] == 0
               for row in plan["study_run_submissions"])
    assert plan["configured_study_feature_count"] == 3
    assert plan["configured_study_step_instances_across_all_calls"] == 12
    assert "one model.study('std1').run() Worker submission" in plan[
        "bma_and_frequency_submission_relationship"]
    assert plan["solver_sequence_count_or_internal_solver_execution_count"].startswith("UNKNOWN_")


def test_solver_tree_binding_accepts_one_sequence_with_multiple_steps_or_split_sequences():
    combined = runner.validate_study_inventory(_study_inventory())
    split = runner.validate_study_inventory(_study_inventory(
        sequence_layout=(("bmaInput",), ("bmaOutput",), ("freq",))))
    assert [row["studystep"] for row in combined["study_step_bindings_in_solver_tree_order"]] == [
        "bmaInput", "bmaOutput", "freq"]
    assert len(split["solver_sequence_tags"]) == 3
    assert combined["actual_solver_execution_count"] == "NOT_DERIVED_FROM_CONFIGURATION"


def test_pre_solve_feature_gate_allows_empty_generated_solver_inventory():
    inventory = _study_inventory()
    inventory["solver_sequences_for_study"] = []
    configured = runner.validate_configured_study_features(inventory)
    assert configured["status"] == "PASS_CONFIGURED_STUDY_FEATURES_BEFORE_SOLVE"
    assert configured["configured_study_feature_count"] == 3


def test_solver_tree_binding_fails_closed_on_wrong_step_order_or_native_properties():
    wrong_order = _study_inventory(sequence_layout=(("bmaOutput", "bmaInput", "freq"),))
    with pytest.raises(ValueError, match="order/bindings"):
        runner.validate_study_inventory(wrong_order)

    wrong_port = _study_inventory()
    wrong_port["study_steps_in_configured_order"][0]["PortName"] = "2"
    with pytest.raises(ValueError, match="PortName"):
        runner.validate_study_inventory(wrong_port)

    wrong_mode_frequency = _study_inventory()
    wrong_mode_frequency["study_steps_in_configured_order"][1]["modeFreq"] = "f1"
    with pytest.raises(ValueError, match="modeFreq"):
        runner.validate_configured_study_features(wrong_mode_frequency)

    missing_mode_frequency = _study_inventory()
    del missing_mode_frequency["study_steps_in_configured_order"][0]["modeFreq"]
    with pytest.raises(ValueError, match="modeFreq"):
        runner.validate_configured_study_features(missing_mode_frequency)

    wrong_frequency = _study_inventory()
    wrong_frequency["study_steps_in_configured_order"][2]["plist"] = "f1"
    with pytest.raises(ValueError, match="frequency study step"):
        runner.validate_study_inventory(wrong_frequency)


def test_source_resolution_uses_native_dataset_solution_and_parameter_axes_only():
    datasets = []
    indices = {}
    for row in (
        _dataset("dSignal", "solFreq", mode_axis=False),
        _dataset("dOutputMode", "solOutput", mode_axis=True),
        _dataset("dInputMode", "solInput", mode_axis=True),
    ):
        datasets.append(row[0])
        indices[row[0]["tag"]] = row[1]
    resolved = runner.resolve_mode_overlap_sources(
        datasets, indices,
        {"freq": ["solFreq"], "bmaOutput": ["solOutput"], "bmaInput": ["solInput"]})
    assert resolved["caller_arrays_used"] is False
    assert resolved["sources"]["signal"]["dataset_id"] == "dSignal"
    assert resolved["sources"]["reference_mode"]["native_parameter_values"] == [
        193.414489032258, 1]
    assert resolved["sources"]["incident_reference"]["solution_id"] == "solInput"

    ambiguous = datasets + [dict(datasets[0], tag="dSignal2")]
    indices["dSignal2"] = dict(indices["dSignal"])
    with pytest.raises(ValueError, match="signal requires exactly one"):
        runner.resolve_mode_overlap_sources(
            ambiguous, indices,
            {"freq": ["solFreq"], "bmaOutput": ["solOutput"], "bmaInput": ["solInput"]})

    unknown_binding = datasets + [{"tag": "dUnresolved", "type_id": "Solution",
                                   "component": "comp1", "geometry": "geom1",
                                   "solution": "solFreq"}]
    with pytest.raises(ValueError, match="incomplete axes"):
        runner.resolve_mode_overlap_sources(
            unknown_binding, indices,
            {"freq": ["solFreq"], "bmaOutput": ["solOutput"], "bmaInput": ["solInput"]})


def test_overlap_definition_uses_exact_complex_port_mode_expression_suffixes():
    sources = {"sources": {
        role: {"dataset_id": dataset, "solution_id": solution,
               "outer_index": 1, "inner_index": 1}
        for role, dataset, solution in (
            ("signal", "dSignal", "solFreq"),
            ("reference_mode", "dOut", "solOut"),
            ("incident_reference", "dIn", "solIn"))}}
    definition = runner.build_mode_overlap_definition(sources, 1, -1)
    assert definition["reference_mode"]["fields"]["electric"]["x"] == "ewfd.Emodex_2"
    assert definition["reference_mode"]["fields"]["magnetic"]["z"] == "ewfd.Hmodez_2"
    assert definition["incident_reference"]["fields"]["electric"]["x"] == "ewfd.Emodex_1"
    assert definition["signal"]["fields"]["magnetic"]["y"] == "ewfd.Hy"
    assert definition["capture"] == {
        "aperture_id": "receiver_core_aperture", "plane_id": "output_x8",
        "selection": {"component": "comp1", "geometry": "geom1", "tag": "selCoreCaptureX8"},
        "normal_sign": 1, "incident_reference_id": "input_port_mode_1",
    }


def test_capture_aperture_readback_requires_distinct_native_core_entities():
    build = {
        "science_plane": {"x_um": 8.0},
        "geometry": {"capture_aperture": {
            "plane_x_um": 8.0, "y_um": [-0.5, 0.5], "selection_tag": "selCoreCaptureX8"}},
        "selections": [
            {"tag": "selReceiverX8", "entity_dimension": 1, "entity_ids": [2, 3, 4]},
            {"tag": "selCoreCaptureX8", "entity_dimension": 1, "entity_ids": [3]},
        ],
    }
    receipt = runner.validate_capture_aperture_readback(build)
    assert receipt["status"] == "PASS_NATIVE_CAPTURE_SELECTION_READBACK"
    assert receipt["entity_ids"] == [3]

    same_as_full_plane = copy.deepcopy(build)
    same_as_full_plane["selections"][1]["entity_ids"] = [2, 3, 4]
    with pytest.raises(ValueError, match="strict native-entity subset"):
        runner.validate_capture_aperture_readback(same_as_full_plane)
    wrong_dimension = copy.deepcopy(build)
    wrong_dimension["selections"][1]["entity_dimension"] = 2
    with pytest.raises(ValueError, match="both be boundary"):
        runner.validate_capture_aperture_readback(wrong_dimension)
    wrong_plane = copy.deepcopy(build)
    wrong_plane["geometry"]["capture_aperture"]["plane_x_um"] = 10.0
    with pytest.raises(ValueError, match="frozen core-only x=8"):
        runner.validate_capture_aperture_readback(wrong_plane)


def test_exact_approval_receipt_binds_limits_plan_and_frozen_source_manifest():
    source_files = {"example.py": {"path": "/tmp/example.py", "bytes": 1,
                                    "sha256": "a" * 64}}
    freeze = {"source_files": source_files,
              "study_or_solver_call_plan": runner.planned_study_calls()}
    approval = {
        "status": runner.APPROVAL_STATUS,
        "candidate_id": runner.CANDIDATE_ID,
        "freeze_sha256": "b" * 64,
        "source_manifest_sha256": runner._source_manifest_sha256(source_files),
        "max_owned_server_processes": 1,
        "max_worker_sessions": 1,
        "max_gui_processes": 0,
        "max_study_run_calls": 4,
        "max_seconds_from_server_birth": runner.MAX_BIRTH_BUDGET_S,
        "cleanup_reserve_seconds": runner.CLEANUP_RESERVE_S,
        "retry_unknown_or_nonterminal_worker_request": False,
        "study_or_solver_call_plan_sha256": runner.sha256_json(freeze["study_or_solver_call_plan"]),
    }
    runner.validate_approval_receipt(approval, freeze, "b" * 64)
    approval["max_study_run_calls"] = 5
    with pytest.raises(RuntimeError, match="does not match"):
        runner.validate_approval_receipt(approval, freeze, "b" * 64)
    approval["max_study_run_calls"] = 4
    approval["max_gui_processes"] = 1
    with pytest.raises(RuntimeError, match="does not match"):
        runner.validate_approval_receipt(approval, freeze, "b" * 64)


def test_freeze_binds_canonical_source_map_and_raw_manifest_file_hash(tmp_path, monkeypatch):
    source_files = {"example.py": {"path": "/tmp/example.py", "bytes": 1,
                                    "sha256": "a" * 64}}
    monkeypatch.setattr(runner, "_source_files", lambda: source_files)
    artifact = runner._write_source_manifest_artifact(tmp_path, source_files)
    manifest_path = tmp_path / artifact["path"]
    assert artifact["sha256"] == runner.sha256_file(manifest_path)
    assert artifact["sha256"] != runner._source_manifest_sha256(source_files)
    assert "exact raw UTF-8 bytes" in artifact["sha256_semantics"]

    freeze = {
        "status": "FROZEN_AWAITING_ROOT_APPROVAL",
        "candidate_id": runner.CANDIDATE_ID,
        "source_files": source_files,
        "source_manifest_sha256": runner._source_manifest_sha256(source_files),
        "source_manifest_artifact": artifact,
        "study_or_solver_call_plan": runner.planned_study_calls(),
    }
    freeze_path = tmp_path / "freeze.json"
    runner.write_json(freeze_path, freeze)
    runner._validate_frozen_candidate(freeze_path, runner.sha256_file(freeze_path))

    original_manifest = manifest_path.read_text(encoding="utf-8")
    manifest_path.write_text(" " + original_manifest[1:], encoding="utf-8")
    with pytest.raises(RuntimeError, match="file-byte SHA-256 mismatch"):
        runner._validate_frozen_candidate(freeze_path, runner.sha256_file(freeze_path))


def test_runtime_project_create_id_drives_the_real_cleanup_ledger_filter(tmp_path, monkeypatch):
    from comsol_mcp._operation_store import OperationStore
    from tools import run_native_w23_te_managed_preflight as preflight

    project_id = "authority-minted-w23-7b48"
    workspace = tmp_path / "science"
    workspace.mkdir()
    monkeypatch.setattr(runner, "PROJECT_ID", None)
    monkeypatch.setattr(preflight, "PROJECT_ID", "old-hardcoded-id")
    project_receipt = runner._bind_authoritative_project({
        "success": True,
        "data": {"project": {
            "project_id": project_id, "label": runner.PROJECT_LABEL,
            "workspace": str(workspace), "schema_version": 1, "revision": 1,
        }},
    }, {"preflight_module": preflight})
    assert project_receipt["project_id"] == project_id
    assert preflight.PROJECT_ID == project_id

    store = OperationStore(tmp_path / "operations.sqlite3")
    try:
        job, _ = store.begin(
            request_id="w23-live-request", idempotency_key="w23-live-idempotency",
            request_hash="w23-live-hash", operation="operation_call",
            metadata={"project_id": project_id},
        )
        store.update_job(job["job_id"], "RUNNING")
        daemon = type("Daemon", (), {"store": store})()
        current = preflight._ledger_reconciliation(daemon, page_size=100)
        assert current["project_id"] == project_id
        assert [row["job_id"] for row in current["complete_raw_job_and_event_pages"]] == [job["job_id"]]
        assert current["safe_for_owned_cleanup"] is False
        current_scope = runner._validate_cleanup_project_scope(
            project_receipt, project_id, preflight, current)
        assert current_scope["cleanup_allowed"] is True
        current_authorization = runner._cleanup_authorization(
            project_receipt, project_id, preflight, current,
            {"safe_for_owned_cleanup": False}, {"idle": False})
        assert current_authorization["safe_for_owned_cleanup"] is False

        # Demonstrate the exact failure mode if cleanup retained the old ID:
        # the active job disappears and an unsafe empty-ledger proof looks idle.
        preflight.PROJECT_ID = "old-hardcoded-id"
        stale = preflight._ledger_reconciliation(daemon, page_size=100)
        assert stale["pagination"]["job_count"] == 0
        assert stale["safe_for_owned_cleanup"] is True
        stale_idle = preflight._daemon_idle_proof(daemon)
        assert stale_idle["idle"] is True
        stale_authorization = runner._cleanup_authorization(
            project_receipt, project_id, preflight, stale,
            {"safe_for_owned_cleanup": stale["safe_for_owned_cleanup"]}, stale_idle)
        assert stale_authorization["safe_for_owned_cleanup"] is False
        assert stale_authorization["project_scope"]["status"] == "FAIL_PROJECT_LEDGER_SCOPE_MISMATCH"

        # Even if the mutable runner global still has the minted ID, it cannot
        # override a stale reconciliation or the independently captured ID.
        assert runner.PROJECT_ID == project_id
        mismatched_reconciliation = dict(current, project_id="other-project-id")
        mismatched_authorization = runner._cleanup_authorization(
            project_receipt, project_id, preflight, mismatched_reconciliation,
            {"safe_for_owned_cleanup": True}, {"idle": True})
        assert mismatched_authorization["safe_for_owned_cleanup"] is False

        mismatched_receipt = dict(project_receipt, project_id="other-project-id")
        receipt_authorization = runner._cleanup_authorization(
            mismatched_receipt, project_id, preflight, current,
            {"safe_for_owned_cleanup": True}, {"idle": True})
        assert receipt_authorization["safe_for_owned_cleanup"] is False
    finally:
        store.close()


def test_prebirth_project_create_uses_production_control_route_and_exact_science_workspace(tmp_path, monkeypatch):
    from comsol_mcp._control_daemon import ControlDaemon

    monkeypatch.setenv("COMSOL_MCP_TRUSTED_CODE", "1")
    container = tmp_path / "authorized-container"
    container.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()

    class Preflight:
        PROJECT_ID = "not-created"

    daemon = ControlDaemon(tmp_path / "control", worker=None, project_root=container)
    try:
        receipt = runner._create_science_project_prebirth(
            daemon, {"preflight_module": Preflight}, container, evidence)
        assert receipt["status"] == "PASS_RUNTIME_AUTHORITY_PROJECT_BOUND"
        assert receipt["workspace_created_before_native_server_birth"] is True
        assert receipt["workspace"] == str((container / "science").resolve(strict=True))
        assert receipt["project_record"]["project_id"] == receipt["project_id"]
        assert receipt["project_record"]["workspace"] == receipt["workspace"]
        assert receipt["project_record"]["label"] == runner.PROJECT_LABEL
        assert receipt["project_record"]["policy"]["permissions"] == [
            "compute", "inspect", "project_write", "trusted_code"]
        assert Preflight.PROJECT_ID == receipt["project_id"]
        assert json.loads((evidence / "project_create_request.json").read_text())[
            "operation"] == "project.create"
        assert json.loads((evidence / "project_create_response.json").read_text())[
            "data"]["project"]["project_id"] == receipt["project_id"]
    finally:
        daemon.close()


def test_project_create_refuses_trusted_code_without_host_grant(tmp_path, monkeypatch):
    from comsol_mcp._control_daemon import ControlDaemon

    monkeypatch.delenv("COMSOL_MCP_TRUSTED_CODE", raising=False)
    container = tmp_path / "authorized-container"
    container.mkdir()
    daemon = ControlDaemon(tmp_path / "control", worker=None, project_root=container)
    try:
        response = daemon.dispatch({
            "operation": "project.create",
            "arguments": {
                "label": runner.PROJECT_LABEL,
                "workspace": "science",
                "policy": {"permissions": [
                    "compute", "inspect", "project_write", "trusted_code"]},
            },
            "execution": {"request_id": "w23-host-grant-negative",
                          "idempotency_key": "w23-host-grant-negative"},
        })
        assert response["success"] is False
        assert response["error"]["code"] == "POLICY_ESCALATION_REFUSED"
        assert not (container / "science").exists()
    finally:
        daemon.close()


def test_managed_model_create_and_reload_keep_minted_project_identity_in_outer_envelopes(tmp_path, monkeypatch):
    project_id = "authority-minted-science-project"
    ref = {"model_tag": "w23model", "session_id": "session-1"}
    calls = []

    class Backend:
        @staticmethod
        def model_project_binding(model_ref):
            assert model_ref == ref
            return {"attribution": "PROJECT_BOUND", "project_id": project_id}

    class Daemon:
        backend = Backend()

        @staticmethod
        def dispatch(request):
            calls.append(request)
            operation = request["operation"]
            if operation == "model_create":
                return {"success": True, "data": {"model_tag": ref["model_tag"]},
                        "execution": {"model_ref": ref, "revision": 1}}
            if operation == "model_load":
                return {"success": True, "data": {"model_tag": ref["model_tag"]},
                        "execution": {"model_ref": ref, "revision": 2}}
            if operation == "model.inspect":
                return {"success": True, "data": {"status": "SUCCEEDED"}}
            raise AssertionError(f"unexpected managed operation: {operation}")

    monkeypatch.setattr(runner, "PROJECT_ID", project_id)
    create_ref, create_revision, create_receipt = runner._create_project_bound_model(
        Daemon(), "W23 managed planar TE science", tmp_path)
    assert create_ref == ref
    assert create_revision == 1
    assert create_receipt["project_id"] == project_id
    assert [row["operation"] for row in calls] == ["model_create", "model.inspect"]
    assert all(row["execution"]["project_id"] == project_id for row in calls)
    assert all(row["execution"].get("model_ref", ref) == ref for row in calls)

    calls.clear()
    saved_model = tmp_path / "saved.mph"
    saved_model.write_bytes(b"software route fixture only")
    reopened_ref, reopened_revision, reopened_tag = runner._load_and_bind_saved_model(
        Daemon(), saved_model, "software_reload", tmp_path)
    assert reopened_ref == ref
    assert reopened_revision == 2
    assert reopened_tag == ref["model_tag"]
    assert [row["operation"] for row in calls] == ["model_load", "model.inspect"]
    assert all(row["execution"]["project_id"] == project_id for row in calls)


@pytest.mark.parametrize("operation", ["model_create", "model_load"])
@pytest.mark.parametrize("binding", [
    {"attribution": "UNATTRIBUTED", "project_id": None},
    {"attribution": "PROJECT_BOUND", "project_id": "foreign-project-id"},
])
def test_model_route_refuses_missing_or_foreign_persisted_binding_even_if_response_echoes_expected(
        tmp_path, monkeypatch, operation, binding):
    project_id = "authority-minted-science-project"
    ref = {"model_tag": "w23model", "session_id": "session-1"}

    class Backend:
        @staticmethod
        def model_project_binding(_model_ref):
            return binding

    class Daemon:
        backend = Backend()

        @staticmethod
        def dispatch(request):
            return {"success": True,
                    "data": {"model_tag": ref["model_tag"]},
                    "execution": {"project_id": project_id,
                                  "model_ref": ref, "revision": 1}}

    monkeypatch.setattr(runner, "PROJECT_ID", project_id)
    if operation == "model_create":
        with pytest.raises(RuntimeError, match="did not persist the authoritative project binding"):
            runner._create_project_bound_model(Daemon(), "W23 model", tmp_path)
    else:
        saved_model = tmp_path / "saved.mph"
        saved_model.write_bytes(b"software route fixture only")
        with pytest.raises(RuntimeError, match="was not persisted under the authoritative project ID"):
            runner._load_and_bind_saved_model(Daemon(), saved_model, "software_reload", tmp_path)


def test_project_binding_rejects_missing_or_failed_authority_receipt(monkeypatch):
    from tools import run_native_w23_te_managed_preflight as preflight

    monkeypatch.setattr(runner, "PROJECT_ID", None)
    monkeypatch.setattr(preflight, "PROJECT_ID", "old-hardcoded-id")
    with pytest.raises(RuntimeError, match="did not succeed"):
        runner._bind_authoritative_project({"success": False, "data": {}},
                                           {"preflight_module": preflight})
    with pytest.raises(RuntimeError, match="project.create response lacks"):
        runner._bind_authoritative_project({"success": True, "data": {"project": {}}},
                                           {"preflight_module": preflight})
    assert runner.PROJECT_ID is None
    assert preflight.PROJECT_ID == "old-hardcoded-id"


def test_direct_rpc_journal_separates_managed_requests_and_keeps_unknown_terminal_blocker(tmp_path):
    journal_path = tmp_path / "direct.jsonl"
    journal = runner.DirectRpcJournal(journal_path)
    journal.capture({"phase": "submitted", "request_id": "r1", "kind": "call",
                    "metadata": {"method": "health"}})
    journal.capture({"phase": "observed", "request_id": "r1", "kind": "call",
                    "status": "SUCCEEDED", "metadata": {"method": "health"}})
    journal.capture({"operation_id": "managed-op", "phase": "observed",
                    "request_id": "managed-rpc", "status": "SUCCEEDED"})
    journal.capture({"phase": "submitted", "request_id": "r2", "kind": "call",
                    "metadata": {"method": "connect"}})
    journal.capture({"phase": "unknown", "request_id": "r2", "kind": "call",
                    "error": "transport timeout", "metadata": {"method": "connect"}})
    rows = runner.read_jsonl(journal_path)
    assert len(rows) == 4
    reconciliation = runner._load_helper_refs()["reconcile_direct_rpc_events"](rows)
    assert reconciliation["safe_for_owned_cleanup"] is False
    assert reconciliation["unknown_rpc_ids"] == ["r2"]


def test_study_run_budget_counts_only_run_calls_inside_study_run_jobs():
    metadata = {"phase": "submitted", "kind": "call",
                "metadata": {"type": "call", "method": "run",
                              "request_id": "activity007-worker-request-178"}}
    assert runner._is_study_run_job({"operation": {"operation": "study.run"}})
    assert runner._is_study_run_job({"operation": {
        "operation": "operation_call",
        "metadata": {"arguments": {"operation_id": "study.run"}}}})
    assert not runner._is_study_run_job({"operation": {"operation": "result.mode_overlap"}})

    class Store:
        def list_jobs(self, offset=0, limit=1000, project_id=None):
            rows = [
                {"job_id": "activity007-job", "operation_id": "activity007-operation",
                 "status": "UNKNOWN", "operation": {
                     "operation": "operation_call", "status": "UNKNOWN",
                     "request_id": "activity007-operation-request",
                     "metadata": {"arguments": {"operation_id": "study.run"},
                                  "execution": {"request_id": "activity007-operation-request",
                                                "expected_revision": 4}},
                     "result": {"data": {
                         "execution_state_unknown": True,
                         "domain_outcome": {"state": "unknown", "failure": {
                             "code": "ENGINE_CALL_FAILED", "safe_retry": False}}}}},
                 "result": {"data": {
                     "execution_state_unknown": True,
                     "domain_outcome": {"state": "unknown", "failure": {
                         "code": "ENGINE_CALL_FAILED", "safe_retry": False}}}}},
                {"job_id": "overlap-job", "status": "SUCCEEDED",
                 "operation": {"operation": "result.mode_overlap"}},
            ]
            return rows[offset:offset + limit]

        def events(self, job_id, offset=0, limit=1000):
            if job_id == "activity007-job":
                return [
                    {"id": 178, "event": "worker_request", "metadata": metadata},
                    {"id": 179, "event": "worker_request", "metadata": {
                        **metadata, "phase": "observed", "status": "FAILED"}},
                ]
            return [{"event": "worker_request", "metadata": metadata}]

    class Preflight:
        _page_project_jobs = staticmethod(lambda store, page_size: (store.list_jobs(limit=page_size), 1))
        _page_job_events = staticmethod(lambda store, job_id, page_size: (store.events(job_id, limit=page_size), 1))
        _is_native_study_run_submission = staticmethod(
            lambda event: event.get("phase") == "submitted"
            and event.get("kind") == "call"
            and event.get("metadata", {}).get("method") == "run")

    class Daemon:
        store = Store()

    rows = runner._actual_science_study_run_submissions(Daemon(), Preflight())
    assert len(rows) == 1
    row = rows[0]
    assert row["job_id"] == "activity007-job"
    assert row["request_id"] == "activity007-worker-request-178"
    assert row["status"] == "UNKNOWN"
    assert row["domain_state"] == "unknown"
    assert row["execution_state_unknown"] is True
    assert row["safe_retry"] is False
    assert row["operation_request_id"] == "activity007-operation-request"
    assert row["operation_execution_request_id"] == "activity007-operation-request"
    assert row["worker_event_id"] == 178
    assert row["worker_event_method"] == "run"
    projected = runner._runner_study_submission_records(rows)
    assert len(projected) == 1
    assert projected[0]["durable_job_status"] == "UNKNOWN"
    assert projected[0]["domain_state"] == "unknown"
    assert projected[0]["safe_retry"] is False
    assert projected[0]["worker_event"]["metadata"]["method"] == "run"


def _canonical_study_run_test_row(job_id, request_id, worker_event_id):
    return {
        "job_id": job_id, "request_id": request_id,
        "worker_event_id": worker_event_id,
        "operation": "study.run", "operation_id": f"operation-{job_id}",
        "operation_request_id": f"outer-{job_id}",
        "worker_event_phase": "submitted", "worker_event_method": "run",
        "worker_event": {"phase": "submitted", "metadata": {
            "request_id": request_id, "method": "run"}},
    }


def test_study_run_canonical_ordinal_projection_rejects_missing_duplicate_identity():
    missing_event_id = _canonical_study_run_test_row("job-a", "worker-a", None)
    with pytest.raises(ValueError, match="positive worker_event_id"):
        runner._runner_study_submission_records([missing_event_id])

    duplicate_event_id = [
        _canonical_study_run_test_row("job-a", "worker-a", 41),
        _canonical_study_run_test_row("job-b", "worker-b", 41),
    ]
    with pytest.raises(ValueError, match="repeats an event or job/request identity"):
        runner._runner_study_submission_records(duplicate_event_id)

    duplicate_job_request = [
        _canonical_study_run_test_row("job-a", "worker-a", 41),
        _canonical_study_run_test_row("job-a", "worker-a", 42),
    ]
    with pytest.raises(ValueError, match="repeats an event or job/request identity"):
        runner._runner_study_submission_records(duplicate_job_request)


def _real_pending_operation_store_study_run(tmp_path, monkeypatch, *, prior_submissions=0):
    from types import SimpleNamespace

    from comsol_mcp._operation_store import OperationStore
    from tools import run_native_w23_te_managed_preflight as preflight

    project_id = "project-test-activity008-r2"
    monkeypatch.setattr(preflight, "PROJECT_ID", project_id)
    store = OperationStore(tmp_path / "pending-study-run.sqlite3")
    prior_jobs = []
    for index in range(prior_submissions):
        operation, _ = store.begin(
            request_id=f"prior-outer-study-request-{index}",
            idempotency_key=f"prior-outer-study-idempotency-{index}",
            request_hash=f"prior-study-request-hash-{index}",
            operation="operation_call",
            metadata={
                "project_id": project_id,
                "arguments": {"operation_id": "study.run",
                              "arguments": {"study": "std1", "timeout_s": 5}},
                "execution": {"project_id": project_id,
                              "request_id": f"prior-outer-study-request-{index}"},
            },
        )
        prior_jobs.append((index, operation["job_id"]))
    # Event IDs deliberately follow creation order. The newest-first job page
    # will therefore return these rows in the opposite order once a later
    # pending dispatch is appended during wait.
    for index, prior_job_id in prior_jobs:
        worker_request_id = f"prior-worker-study-request-{index}"
        store.add_event(prior_job_id, "worker_request", {
            "phase": "submitted", "kind": "call", "status": "RUNNING",
            "request_id": worker_request_id,
            "metadata": {"type": "call", "method": "run",
                         "request_id": worker_request_id},
        })
        terminal_result = {
            "success": True,
            "data": {"job_id": prior_job_id, "execution_state_unknown": False,
                     "domain_outcome": {"state": "succeeded"}},
            "execution": {"job_id": prior_job_id},
        }
        store.update_job(prior_job_id, "SUCCEEDED", result=terminal_result)
    operation, created = store.begin(
        request_id="outer-study-request-r2",
        idempotency_key="outer-study-idempotency-r2",
        request_hash="activity008-r2-request-hash",
        operation="operation_call",
        metadata={
            "project_id": project_id,
            "arguments": {"operation_id": "study.run",
                          "arguments": {"study": "std1", "timeout_s": 5}},
            "execution": {"project_id": project_id,
                          "request_id": "outer-study-request-r2"},
        },
    )
    assert created is False
    job_id = operation["job_id"]
    store.update_job(job_id, "RUNNING")
    daemon = SimpleNamespace(store=store)
    response = {"success": True, "data": {"job_id": job_id, "status": "PENDING"},
                "execution": {"job_id": job_id}}
    return daemon, preflight, store, job_id, response


def test_pending_dispatch_reconciles_late_worker_event_after_same_job_wait(
        tmp_path, monkeypatch):
    daemon, preflight, store, job_id, pending_response = (
        _real_pending_operation_store_study_run(
            tmp_path, monkeypatch, prior_submissions=2))
    before_rows = runner._actual_science_study_run_submissions(daemon, preflight)
    assert len(before_rows) == 2
    before_ids = {(row["job_id"], row["request_id"]) for row in before_rows}
    before_wait = runner._reconcile_study_run_dispatch(
        daemon, preflight, response_job_id=job_id, before_identities=before_ids)
    assert before_wait["status"] == "UNVERIFIED_POST_WAIT_ACCOUNTING_STOP"
    assert before_wait["submission_count_for_dispatch"] == "UNKNOWN"

    terminal_response = {
        "success": True,
        "data": {"job_id": job_id, "domain_outcome": {"state": "succeeded"}},
        "execution": {"job_id": job_id},
    }
    worker_request_id = "worker-study-run-request-r2"
    worker_submitted = {
        "phase": "submitted", "kind": "call", "status": "RUNNING",
        "request_id": worker_request_id,
        "metadata": {"type": "call", "method": "run",
                     "request_id": worker_request_id},
    }
    sleep_calls = []

    def persist_submission_during_wait(_seconds):
        sleep_calls.append(1)
        store.add_event(job_id, "worker_request", worker_submitted)
        worker_observed = {
            "phase": "observed", "kind": "call", "status": "SUCCEEDED",
            "request_id": worker_request_id,
            "metadata": {"type": "call", "method": "run", "status": "SUCCEEDED",
                         "request_id": worker_request_id},
        }
        store.add_event(job_id, "worker_request", worker_observed)
        store.update_job(job_id, "SUCCEEDED", result=terminal_response)

    monkeypatch.setattr(runner.time, "sleep", persist_submission_during_wait)
    terminal = runner._wait_job(
        daemon, pending_response, deadline_epoch=runner.time.time() + 10,
        cleanup_reserve_s=0, label="pending_study_run_r2", evidence=tmp_path)
    assert terminal["success"] is True
    assert len(sleep_calls) == 1

    after_wait = runner._reconcile_study_run_dispatch(
        daemon, preflight, response_job_id=job_id, before_identities=before_ids)
    assert after_wait["status"] == "PASS_ONE_DURABLE_WORKER_SUBMISSION_BOUND_TO_DISPATCH_JOB"
    assert after_wait["response_job_id"] == job_id
    assert after_wait["response_job_status"] == "SUCCEEDED"
    assert after_wait["response_job_is_study_run"] is True
    assert after_wait["submission_count_for_dispatch"] == 1
    assert len(after_wait["response_job_worker_submissions"]) == 1
    assert after_wait["response_job_worker_submissions"][0]["request_id"] == worker_request_id
    assert "ordinal" not in after_wait["response_job_worker_submissions"][0]
    accounting = {"status": after_wait["status"], "post_wait_reconciliation": after_wait}
    runner._require_terminal_study_run_success_before_accounting(
        {"require_success": runner._operation_succeeded}, terminal_response,
        "pending Study.run positive control", accounting,
        expected_total_dispatch_count=3)
    with pytest.raises(RuntimeError, match="cumulative durable submission count is unverified"):
        runner._require_terminal_study_run_success_before_accounting(
            {"require_success": runner._operation_succeeded}, terminal_response,
            "cumulative submission mismatch negative control", accounting,
            expected_total_dispatch_count=4)

    canonical_records = runner._runner_study_submission_records(
        after_wait["all_durable_submissions"])
    # OperationStore's job page is newest-first. The canonical ordinal must
    # instead follow global Worker event IDs and stay attached to exact IDs.
    raw_event_ids = [row["worker_event_id"] for row in after_wait["all_durable_submissions"]]
    assert raw_event_ids == sorted(raw_event_ids, reverse=True)
    assert [row["worker_event_id"] for row in canonical_records] == sorted(raw_event_ids)
    assert [row["ordinal"] for row in canonical_records] == [1, 2, 3]

    phase_calls = []
    def fake_save(_daemon, ref, revision, fixture_name, case_id, _helpers, **kwargs):
        phase_calls.append(("save", case_id))
        path = kwargs["project_workspace"] / "provisional.mph"
        path.write_bytes(b"offline-test-placeholder")
        receipt = {"status": kwargs["receipt_status"], "path": str(path),
                   "bytes": path.stat().st_size, "sha256": runner.sha256_file(path),
                   "scientific_acceptance": kwargs["scientific_acceptance"]}
        return path, receipt, ref, revision + 1

    def fake_fixture_call(_daemon, ref, revision, fixture_name, args, _helpers, **kwargs):
        phase_calls.append((args["phase"], kwargs["label"]))
        assert args == {"phase": "solution_inventory"}
        return {"status": "SYNTHETIC_ZERO_SOLVE_INVENTORY_ENTRY"}, ref, revision + 1

    monkeypatch.setattr(runner, "_save_case_model", fake_save)
    monkeypatch.setattr(runner, "_fixture_call", fake_fixture_call)
    terminal_response["execution"]["model_ref"] = {
        "model_tag": "testModel", "session_id": "test-session"}
    terminal_response["execution"]["revision"] = 7
    provisional_saved_models = {}
    summary = {}
    completion = runner._complete_terminal_study_run_case(
        daemon, {"require_success": runner._operation_succeeded},
        terminal_response=terminal_response, accounting=accounting,
        expected_total_dispatch_count=3, post_wait_reconciliation=after_wait,
        study_submissions=canonical_records, response_job_id=job_id,
        case_id="coarse-phase0", completed_case_count=1,
        ref={"model_tag": "oldModel", "session_id": "test-session"}, revision=6,
            fixture_name="NativeW23TEScienceFixture.java",
            project_workspace=tmp_path, deadline_epoch=runner.time.time()+600,
        evidence=tmp_path, summary=summary,
        provisional_saved_models=provisional_saved_models)
    assert completion["submission"]["ordinal"] == 3
    assert completion["submission"]["worker_event_id"] == raw_event_ids[0]
    assert completion["response_job_worker_submission"]["job_id"] == job_id
    assert phase_calls == [
        ("save", "coarse-phase0_provisional"),
        ("solution_inventory", "solution_inventory_coarse-phase0"),
    ]
    assert completion["inventory"]["status"] == "SYNTHETIC_ZERO_SOLVE_INVENTORY_ENTRY"
    assert provisional_saved_models["coarse-phase0"]["scientific_acceptance"] == "NOT_ACCEPTED"
    assert summary["native_scientific_acceptance"] == "NOT_ACCEPTED"
    assert len(runner._actual_science_study_run_submissions(daemon, preflight)) == 3

    wrong_job = runner._reconcile_study_run_dispatch(
        daemon, preflight, response_job_id="not-the-dispatch-response-job",
        before_identities=before_ids)
    assert wrong_job["status"] == "UNVERIFIED_POST_WAIT_ACCOUNTING_STOP"


def test_pending_unknown_without_durable_submission_stays_unknown_not_zero(
        tmp_path, monkeypatch):
    daemon, preflight, store, job_id, pending_response = (
        _real_pending_operation_store_study_run(tmp_path, monkeypatch))
    before_ids = set()

    def become_unknown_during_wait(_seconds):
        store.update_job(job_id, "UNKNOWN", result={
            "success": False,
            "data": {"job_id": job_id, "execution_state_unknown": True,
                     "domain_outcome": {"state": "unknown", "failure": {
                         "code": "ENGINE_CALL_FAILED", "safe_retry": False}}},
            "error": {"message": "ORIGINAL_UNKNOWN_SENTINEL", "safe_retry": False},
            "execution": {"job_id": job_id},
        })

    monkeypatch.setattr(runner.time, "sleep", become_unknown_during_wait)
    terminal = runner._wait_job(
        daemon, pending_response, deadline_epoch=runner.time.time() + 10,
        cleanup_reserve_s=0, label="pending_unknown_r2", evidence=tmp_path)
    reconciliation = runner._reconcile_study_run_dispatch(
        daemon, preflight, response_job_id=job_id, before_identities=before_ids)
    assert terminal["success"] is False
    assert reconciliation["response_job_status"] == "UNKNOWN"
    assert reconciliation["status"] == "UNVERIFIED_POST_WAIT_ACCOUNTING_STOP"
    assert reconciliation["submission_count_for_dispatch"] == "UNKNOWN"
    with pytest.raises(RuntimeError, match="ORIGINAL_UNKNOWN_SENTINEL"):
        runner._require_terminal_study_run_success_before_accounting(
            {"require_success": runner._operation_succeeded}, terminal,
            "Study.run negative control",
            {"status": reconciliation["status"], "post_wait_reconciliation": reconciliation},
            expected_total_dispatch_count=1)


def test_pending_worker_dispatch_preserves_original_failure_before_accounting_mismatch(
        tmp_path, monkeypatch):
    daemon, preflight, store, job_id, pending_response = (
        _real_pending_operation_store_study_run(tmp_path, monkeypatch))
    before_ids = set()
    worker_request_id = "worker-failed-study-run-request-r2"
    failed_response = {
        "success": False,
        "data": {"job_id": job_id, "domain_outcome": {"state": "failed"}},
        "error": {"code": "ENGINE_CALL_FAILED", "message": "ORIGINAL_COMSOL_FAILURE_SENTINEL"},
        "execution": {"job_id": job_id},
    }
    def fail_during_wait(_seconds):
        store.add_event(job_id, "worker_request", {
            "phase": "submitted", "kind": "call", "status": "RUNNING",
            "request_id": worker_request_id,
            "metadata": {"type": "call", "method": "run",
                         "request_id": worker_request_id},
        })
        store.update_job(job_id, "FAILED", result=failed_response)

    monkeypatch.setattr(runner.time, "sleep", fail_during_wait)
    terminal = runner._wait_job(
        daemon, pending_response, deadline_epoch=runner.time.time() + 10,
        cleanup_reserve_s=0, label="pending_failure_r2", evidence=tmp_path)
    reconciliation = runner._reconcile_study_run_dispatch(
        daemon, preflight, response_job_id=job_id, before_identities=before_ids)
    assert reconciliation["status"] == "PASS_ONE_DURABLE_WORKER_SUBMISSION_BOUND_TO_DISPATCH_JOB"
    assert reconciliation["response_job_status"] == "FAILED"
    with pytest.raises(RuntimeError, match="ORIGINAL_COMSOL_FAILURE_SENTINEL"):
        runner._require_terminal_study_run_success_before_accounting(
            {"require_success": runner._operation_succeeded}, terminal,
            "Study.run native failure control", {"status": "UNVERIFIED_POST_WAIT_ACCOUNTING_STOP"},
            expected_total_dispatch_count=2)


def test_worker_events_for_job_reads_all_pages():
    class Store:
        def __init__(self):
            self.rows = [{"id": index, "event": "worker_request", "metadata": {}}
                         for index in range(1001)]

        def events(self, job_id, offset=0, limit=100):
            return self.rows[offset:offset + min(limit, 1000)]

    class Daemon:
        store = Store()

    rows = runner._worker_events_for_job(Daemon(), "job")
    assert len(rows) == 1001


def test_partial_start_identity_capture_comes_from_exact_worker_popen_child():
    class Proc:
        pid = 42117

        def poll(self):
            return None

    class Worker:
        _process = Proc()
        _port = 42118

    class Server:
        worker = Worker()

    observed = []

    def snapshot(pid):
        observed.append(pid)
        return {"pid": pid, "birth": "2026-09-27T00:00:00Z", "command": "java worker"}

    receipt = runner._capture_worker_process_identity(Server(), {"_process_snapshot": snapshot})
    assert receipt["status"] == "PASS_EXACT_WORKER_POPEN_IDENTITY_CAPTURED"
    assert receipt["pid"] == 42117 and receipt["port"] == 42118
    assert observed == [42117]

    class ReusedPidServer:
        worker = Worker()

    refused = runner._capture_worker_process_identity(
        ReusedPidServer(), {"_process_snapshot": lambda pid: {"pid": pid + 1}})
    assert refused["status"] == "WORKER_IDENTITY_UNAVAILABLE"
    assert refused["identity"]["pid"] == 42118


def test_execute_rejects_bad_freeze_before_any_process_or_evidence_directory(tmp_path, monkeypatch):
    freeze_path = tmp_path / "freeze.json"
    freeze_path.write_text(json.dumps({"candidate_id": "bad"}), encoding="utf-8")
    approval_path = tmp_path / "approval.json"
    approval_path.write_text("{}", encoding="utf-8")
    work = tmp_path / "work"
    evidence = tmp_path / "evidence"
    monkeypatch.setattr(runner, "_load_helper_refs", lambda: {"imports_complete": True})
    with pytest.raises(RuntimeError, match="freeze file is absent or its SHA-256 differs"):
        runner.execute(freeze_path, "0" * 64, approval_path, work, evidence)
    assert not work.exists()
    assert not evidence.exists()


def _material_getter(value_type: str, raw, *, stored_size=None):
    size_getter = "MaterialModel.size"
    if value_type == "StringArray":
        return {"value_type": value_type, "getter": "MaterialModel.getStringArray",
                "raw_value": raw, "raw_shape": [len(raw)], "size_getter": size_getter,
                "stored_size": stored_size if stored_size is not None else ([1, 1] if len(raw) == 1 else [3, 3]),
                "stored_size_status": "CAPTURED", "readback_status": "CAPTURED"}
    if value_type == "String":
        return {"value_type": value_type, "getter": "MaterialModel.getString",
                "raw_value": raw, "raw_shape": [], "size_getter": size_getter,
                "stored_size": stored_size if stored_size is not None else [1, 1],
                "stored_size_status": "CAPTURED", "readback_status": "CAPTURED"}
    return {"value_type": value_type, "getter": "MaterialModel.getStringMatrix",
            "raw_value": raw, "raw_shape": [len(raw), len(raw[0]) if raw else 0],
            "size_getter": size_getter, "stored_size": stored_size if stored_size is not None else [3, 3],
            "stored_size_status": "CAPTURED", "readback_status": "CAPTURED"}


def _valid_material_build():
    rows = []
    for tag, selection, epsilon in (
            ("matCore", "selCoreMaterial", "n_core^2"),
            ("matCladdingBottom", "selCladdingBottom", "n_clad^2"),
            ("matCladdingTop", "selCladdingTop", "n_clad^2")):
        rows.append({
            "tag": tag,
            "selection": selection,
            "expected_diagonal_relative_permittivity": epsilon,
            "relative_permittivity": _material_getter("StringArray", [
                epsilon, "0", "0", "0", epsilon, "0", "0", "0", epsilon]),
            "relative_permeability": _material_getter("StringMatrix", [
                ["1", "0", "0"], ["0", "1", "0"], ["0", "0", "1"]]),
            "electric_conductivity": _material_getter("StringArray", ["0[S/m]"]),
            "electric_lossless_assumption": (
                "real_relative_permittivity_with_electric_conductivity_set_to_zero_S_per_m"),
        })
    return {"materials": rows}


@pytest.mark.parametrize(("value_type", "raw", "stored_size"), [
    ("StringArray", ["0[S/m]"], [1, 1]),
    ("StringArray", ["0[S/m]"], [3, 3]),
    ("String", "0[S/m]", [1, 1]),
])
def test_lossless_material_gate_accepts_exact_finite_zero_conductivity_for_every_material(
        value_type, raw, stored_size):
    build = _valid_material_build()
    for row in build["materials"]:
        row["electric_conductivity"] = _material_getter(
            value_type, copy.deepcopy(raw), stored_size=stored_size)
    gate = runner.validate_material_tensor_readback(build)
    assert gate["electric_conductivity_is_finite_zero_S_per_m_for_every_material"] is True
    for material in gate["materials"]:
        assert material["electric_conductivity_raw_shape"] == (
            [1] if value_type == "StringArray" else [])
        assert material["electric_conductivity_stored_size"] == stored_size
        assert material["electric_conductivity_value_S_per_m"] == 0.0
        assert material["electric_conductivity_finite_zero_verified"] is True


@pytest.mark.parametrize("mutation, message", [
    (lambda payload: payload["raw_value"].__setitem__(0, "0.1[S/m]"), "must be finite zero"),
    (lambda payload: payload["raw_value"].__setitem__(0, "NaN[S/m]"), "finite numeric zero"),
    (lambda payload: payload["raw_value"].__setitem__(0, "0[1/s]"), "finite numeric zero"),
    (lambda payload: payload.update(value_type="Unknown"), "expected matching String/getString"),
    (lambda payload: payload.update(raw_shape=[1, 1]), "expected matching String/getString"),
    (lambda payload: payload.update(stored_size=[3, 1]), "MaterialModel.size must be captured"),
    (lambda payload: payload.update(readback_status="UNVERIFIED_NULL_VALUE"), "not captured"),
    (lambda payload: payload.update(stored_size_status="UNVERIFIED_SIZE_ERROR"), "MaterialModel.size must be captured"),
])
def test_lossless_material_gate_rejects_unverified_nonzero_or_malformed_conductivity(mutation, message):
    build = _valid_material_build()
    mutation(build["materials"][0]["electric_conductivity"])
    with pytest.raises(ValueError, match=message):
        runner.validate_material_tensor_readback(build)


@pytest.mark.parametrize("raw", ["1e-400[S/m]", "1e9999[S/m]", "-1e-400[S/m]"])
def test_lossless_material_gate_rejects_decimal_underflow_and_float_overflow(raw):
    build = _valid_material_build()
    build["materials"][0]["electric_conductivity"] = _material_getter(
        "StringArray", [raw])
    with pytest.raises(ValueError, match="conductivity must be finite zero S/m"):
        runner.validate_material_tensor_readback(build)


def test_material_tensor_gate_accepts_only_metadata_bound_documented_shapes():
    build = _valid_material_build()
    gate = runner.validate_material_tensor_readback(build)
    assert gate["status"] == "PASS_NATIVE_MATERIAL_TENSOR_AND_LOSSLESS_CONDUCTIVITY_READBACK"
    assert gate["physical_tensor_diagonal_and_zero_off_diagonal_verified"] is True
    assert gate["electric_conductivity_is_finite_zero_S_per_m_for_every_material"] is True
    core = next(row for row in gate["materials"] if row["tag"] == "matCore")
    assert core["relative_permittivity_value_type"] == "StringArray"
    assert core["relative_permittivity_raw_shape"] == [9]
    assert core["relative_permittivity_stored_size"] == [3, 3]
    assert core["relative_permeability_value_type"] == "StringMatrix"
    assert core["relative_permeability_raw_shape"] == [3, 3]
    assert core["relative_permeability_stored_size"] == [3, 3]
    assert core["electric_conductivity_value_type"] == "StringArray"
    assert core["electric_conductivity_raw_shape"] == [1]
    assert core["electric_conductivity_stored_size"] == [1, 1]
    assert core["electric_conductivity_value_S_per_m"] == 0.0
    assert core["electric_conductivity_finite_zero_verified"] is True
    assert gate["string_array_index_order"].startswith("COMSOL 6.4 documented column-wise")


@pytest.mark.parametrize("stored_size", ([1, 1], [3, 3]))
def test_material_tensor_gate_reconstructs_only_canonical_isotropic_one_entry_for_captured_property_sizes(stored_size):
    build = _valid_material_build()
    for row in build["materials"]:
        epsilon = row["expected_diagonal_relative_permittivity"]
        row["relative_permittivity"] = _material_getter("StringArray", [epsilon], stored_size=stored_size)
        row["relative_permeability"] = _material_getter("StringArray", ["1"], stored_size=stored_size)

    gate = runner.validate_material_tensor_readback(build)
    assert gate["status"] == "PASS_NATIVE_MATERIAL_TENSOR_AND_LOSSLESS_CONDUCTIVITY_READBACK"
    assert gate["physical_tensor_diagonal_and_zero_off_diagonal_verified"] is True
    for row in gate["materials"]:
        assert row["relative_permittivity_reconstruction"] == (
            "StringArray-isotropic-one-entry-property-size-1x1" if stored_size == [1, 1]
            else "StringArray-isotropic-one-entry-property-size-3x3")
        assert row["relative_permittivity_raw_shape"] == [1]
        assert row["relative_permittivity_stored_size"] == stored_size
        assert row["relative_permeability_reconstruction"] == (
            "StringArray-isotropic-one-entry-property-size-1x1" if stored_size == [1, 1]
            else "StringArray-isotropic-one-entry-property-size-3x3")
        assert row["relative_permeability_diagonal"] == "1"


@pytest.mark.parametrize("stored_size", ([1, 1], [3, 3]))
def test_material_tensor_gate_reconstructs_string_scalar_with_either_documented_property_size(stored_size):
    build = _valid_material_build()
    for row in build["materials"]:
        epsilon = row["expected_diagonal_relative_permittivity"]
        row["relative_permittivity"] = _material_getter("String", epsilon, stored_size=stored_size)
        row["relative_permeability"] = _material_getter("String", "1", stored_size=stored_size)

    gate = runner.validate_material_tensor_readback(build)
    assert gate["status"] == "PASS_NATIVE_MATERIAL_TENSOR_AND_LOSSLESS_CONDUCTIVITY_READBACK"
    assert gate["physical_tensor_diagonal_and_zero_off_diagonal_verified"] is True
    assert all(row["relative_permittivity_stored_size"] == stored_size for row in gate["materials"])
    assert all(row["relative_permeability_diagonal"] == "1" for row in gate["materials"])


def test_material_tensor_gate_reconstructs_documented_string_scalar_only_as_isotropic():
    build = _valid_material_build()
    for row in build["materials"]:
        epsilon = row["expected_diagonal_relative_permittivity"]
        row["relative_permittivity"] = _material_getter("String", epsilon)
        row["relative_permeability"] = _material_getter("String", "1")

    gate = runner.validate_material_tensor_readback(build)
    assert gate["status"] == "PASS_NATIVE_MATERIAL_TENSOR_AND_LOSSLESS_CONDUCTIVITY_READBACK"
    assert all(row["relative_permittivity_reconstruction"] == "String-isotropic-scalar-property-size-1x1"
               for row in gate["materials"])


@pytest.mark.parametrize("stored_size", ([1, 3], [3, 1], [2, 2], [1, 9]))
def test_material_tensor_gate_rejects_single_scalar_for_unapproved_property_size(stored_size):
    build = _valid_material_build()
    for row in build["materials"]:
        epsilon = row["expected_diagonal_relative_permittivity"]
        row["relative_permittivity"] = _material_getter("StringArray", [epsilon], stored_size=stored_size)
        row["relative_permeability"] = _material_getter("StringArray", ["1"], stored_size=stored_size)
    with pytest.raises(ValueError, match=r"StringArray requires exact isotropic raw_shape \[1\]"):
        runner.validate_material_tensor_readback(build)


@pytest.mark.parametrize("stored_size", ([1, 3], [3, 1], [2, 2], [1, 9]))
def test_material_tensor_gate_rejects_string_scalar_for_unapproved_property_size(stored_size):
    build = _valid_material_build()
    for row in build["materials"]:
        epsilon = row["expected_diagonal_relative_permittivity"]
        row["relative_permittivity"] = _material_getter("String", epsilon, stored_size=stored_size)
        row["relative_permeability"] = _material_getter("String", "1", stored_size=stored_size)
    with pytest.raises(ValueError, match=r"String scalar requires raw_shape \[\] and captured size \[1,1\] or \[3,3\]"):
        runner.validate_material_tensor_readback(build)


@pytest.mark.parametrize("mutation, message", [
    (lambda payload: payload.update(raw_shape=[]), r"StringArray requires exact isotropic raw_shape \[1\]"),
    (lambda payload: payload.update(stored_size=[1, 3]), r"StringArray requires exact isotropic raw_shape \[1\]"),
    (lambda payload: payload.update(stored_size=[3, 1]), r"StringArray requires exact isotropic raw_shape \[1\]"),
    (lambda payload: payload.update(stored_size=[2, 2]), r"StringArray requires exact isotropic raw_shape \[1\]"),
    (lambda payload: payload.update(stored_size=[1, 9]), r"StringArray requires exact isotropic raw_shape \[1\]"),
    (lambda payload: payload.update(readback_status="UNVERIFIED_NULL_VALUE"), "getter readback is not captured"),
])
def test_material_tensor_gate_rejects_one_entry_unless_raw_shape_size_and_getter_are_exact(
    mutation, message,
):
    build = _valid_material_build()
    for row in build["materials"]:
        epsilon = row["expected_diagonal_relative_permittivity"]
        row["relative_permittivity"] = _material_getter("StringArray", [epsilon])
        row["relative_permeability"] = _material_getter("StringArray", ["1"])
    mutation(build["materials"][0]["relative_permittivity"])
    with pytest.raises(ValueError, match=message):
        runner.validate_material_tensor_readback(build)


@pytest.mark.parametrize("mutate, message", [
    (lambda row: row["relative_permittivity"].update(raw_shape=[3, 3]), r"StringArray requires exact isotropic raw_shape \[1\]"),
    (lambda row: row["relative_permittivity"].update(value_type="Unknown"), "unsupported or mismatched"),
    (lambda row: row["relative_permeability"].update(raw_shape=[3, 2]), "StringMatrix requires actual 3x3"),
    (lambda row: row["relative_permeability"]["raw_value"][0].__setitem__(1, "0.01"), r"relpermeability\[0,1\]"),
    (lambda row: row["relative_permittivity"]["raw_value"].__setitem__(4, "n_core"), r"relpermittivity\[1,1\]"),
    (lambda row: row["relative_permittivity"].update(stored_size=[1, 1]), r"StringArray requires exact isotropic raw_shape \[1\]"),
    (lambda row: row["relative_permittivity"].update(stored_size=[3, 3, 1]), "two positive integer dimensions"),
    (lambda row: row["relative_permittivity"].pop("stored_size"), "two positive integer dimensions"),
])
def test_material_tensor_gate_rejects_wrong_metadata_shape_or_physical_entries(mutate, message):
    build = _valid_material_build()
    mutate(build["materials"][0])
    with pytest.raises(ValueError, match=message):
        runner.validate_material_tensor_readback(build)


def test_fixture_material_source_uses_nine_entry_vectors_and_typed_getter_dispatch():
    source = (Path(__file__).parents[1] / "tools/java/NativeW23TEScienceFixture.java").read_text(
        encoding="utf-8")
    assert 'String[] epsilon = {eps, "0", "0", "0", eps, "0", "0", "0", eps};' in source
    assert 'String[] mu = {"1", "0", "0", "0", "1", "0", "0", "0", "1"};' in source
    assert '.set("relpermittivity", epsilon)' in source
    assert '.set("relpermeability", mu)' in source
    assert '.set("electricconductivity", new String[] {"0[S/m]"})' in source
    assert 'materialPropertyReadback(group, "electricconductivity")' in source
    assert '"real_relative_permittivity_with_electric_conductivity_set_to_zero_S_per_m"' in source
    assert 'String valueType = group.getValueType(property);' in source
    assert 'group.getStringArray(property)' in source
    assert 'group.getStringMatrix(property)' in source
    assert 'group.getString(property)' in source
    assert 'int[] storedSize = group.size(property);' in source
    assert 'result.put("size_getter", "MaterialModel.size");' in source
    assert 'result.put("stored_size"' in source
    assert 'result.put("raw_shape"' in source
    assert "String[][] epsilon" not in source


def test_durable_unknown_cleanup_idle_requires_terminal_rpc_fresh_health_and_exact_endpoint():
    from tools import run_native_w23_te_managed_preflight as preflight

    job = {"job_id": "job-unknown", "status": "UNKNOWN",
           "result": {"domain_state": "unknown", "safe_retry": False}}
    events = [
        {"id": 1, "event": "worker_request", "metadata": {
            "request_id": "fixture-rpc", "phase": "submitted", "status": "RUNNING"}},
        {"id": 2, "event": "worker_request", "metadata": {
            "request_id": "fixture-rpc", "phase": "observed", "status": "FAILED"}},
    ]

    class Store:
        def list_jobs(self, offset=0, limit=1000, project_id=None):
            return [job][offset:offset + limit]

        def events(self, job_id, offset=0, limit=1000):
            return events[offset:offset + limit]

    class Daemon:
        store = Store()

    health = {"ok": True, "status": "HEALTHY", "connected": True,
              "server": "127.0.0.1:61926", "queued_or_running": 0}
    proof = preflight._daemon_idle_proof(
        Daemon(), worker_health=health, expected_endpoint="127.0.0.1:61926")
    assert proof["idle"] is True
    assert proof["durable_unknown_preserved"] is True
    assert proof["durable_unknown_job_ids"] == ["job-unknown"]
    assert job["status"] == "UNKNOWN" and job["result"]["safe_retry"] is False

    assert preflight._daemon_idle_proof(Daemon())["idle"] is False
    assert preflight._daemon_idle_proof(
        Daemon(), worker_health=health, expected_endpoint="127.0.0.1:61927")["idle"] is False
    assert preflight._daemon_idle_proof(
        Daemon(), worker_health=dict(health, queued_or_running=1),
        expected_endpoint="127.0.0.1:61926")["idle"] is False

    original_events = list(events)
    events[:] = original_events[:1]
    assert preflight._daemon_idle_proof(
        Daemon(), worker_health=health, expected_endpoint="127.0.0.1:61926")["idle"] is False
    events[:] = original_events
    events.extend(original_events)
    assert preflight._daemon_idle_proof(
        Daemon(), worker_health=health, expected_endpoint="127.0.0.1:61926")["idle"] is False
    events[:] = original_events
    job["status"] = "RUNNING"
    assert preflight._daemon_idle_proof(
        Daemon(), worker_health=health, expected_endpoint="127.0.0.1:61926")["idle"] is False


def test_socket_owner_gate_parses_both_numeric_loopback_endpoints(monkeypatch):
    from types import SimpleNamespace
    from tools import run_native_w23_te_managed_preflight as preflight

    output = "\n".join((
        "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME",
        "java 101 everwalker 59u IPv6 0x1 0t0 TCP 127.0.0.1:61926 (LISTEN)",
        "java 101 everwalker 60u IPv6 0x2 0t0 TCP 127.0.0.1:61926->127.0.0.10:61953 (ESTABLISHED)",
        "java 102 everwalker 61u IPv6 0x3 0t0 TCP [::ffff:127.0.0.1]:61953->[::1]:61926 (ESTABLISHED)",
    ))
    monkeypatch.setattr(preflight.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout=output, stderr=""))
    receipt = preflight._port_socket_owners(61926)
    assert receipt["safe"] is True
    assert receipt["pids"] == [101, 102]
    assert all(row["valid"] and row["active"] is True for row in receipt["rows"])
    assert receipt["rows"][1]["remote_endpoint"]["host"] == "127.0.0.10"
    assert receipt["rows"][1]["remote_endpoint"]["loopback"] is True
    assert receipt["rows"][2]["local_endpoint"]["ipv4_mapped_loopback"] is True


@pytest.mark.parametrize("socket_text, state", [
    ("127.0.0.1:61926->8.8.8.8:443", "ESTABLISHED"),
    ("127.0.0.1:61926->[::1:443", "ESTABLISHED"),
    ("127.0.0.1:61926->[not-an-ip]:443", "ESTABLISHED"),
    ("*:61926", "LISTEN"),
    ("127.0.0.1:61926->127.0.0.1:61953", "CLOSE_WAIT"),
])
def test_socket_owner_gate_blocks_external_malformed_wildcard_or_unknown_state(
        monkeypatch, socket_text, state):
    from types import SimpleNamespace
    from tools import run_native_w23_te_managed_preflight as preflight

    output = "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n"
    output += f"java 101 everwalker 59u IPv6 0x1 0t0 TCP {socket_text} ({state})\n"
    monkeypatch.setattr(preflight.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout=output, stderr=""))
    receipt = preflight._port_socket_owners(61926)
    assert receipt["safe"] is False
    assert receipt["pids"] == []
    assert receipt["rows"][0]["valid"] is False


def test_socket_owner_gate_keeps_actual_closed_ipv6_form_as_inactive_history(monkeypatch):
    from types import SimpleNamespace
    from tools import run_native_w23_te_managed_preflight as preflight

    output = "\n".join((
        "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME",
        "java 101 everwalker 59u IPv6 0x1 0t0 TCP 127.0.0.1:61926 (LISTEN)",
        "java 999 everwalker 174u IPv6 0x2 0t0 TCP [::127.0.0.1]:61926->[::127.0.0.1]:61953 (CLOSED)",
    ))
    monkeypatch.setattr(preflight.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout=output, stderr=""))
    receipt = preflight._port_socket_owners(61926)
    assert receipt["safe"] is True
    assert receipt["pids"] == [101]
    assert receipt["inactive_historical_pids"] == [999]
    closed = receipt["rows"][1]
    assert closed["state"] == "CLOSED" and closed["active"] is False
    assert closed["loopback_only"] is None
    assert closed["endpoint_semantics"] == (
        "inactive_closed_history_only_not_used_as_live_loopback_or_client_proof")
    assert closed["local_endpoint"]["host"] == "::7f00:1"
    assert closed["local_endpoint"]["loopback"] is False


def test_bma_uses_documented_shift_search_enum_without_relaxing_science_gates():
    fixture = Path(runner.FIXTURE).read_text(encoding="utf-8")
    assert 'bma.set("eigwhich", "lm")' in fixture
    assert 'bma.set("shiftactive", "on")' in fixture
    assert 'bma.set("shift", "1.54")' in fixture
    assert 'bma.set("neigs", 1)' in fixture
    assert runner.EXPECTED_NEFF_TE0 == 1.540405036791
    assert runner.MAX_STUDY_RUN_CALLS == 4
    assert runner.CANDIDATE_ID == "W23-TE-PLANAR-TE-SCIENCE-17"
    assert runner.PROJECT_LABEL == "W23 managed planar TE science candidate 17"


def test_remote_client_disconnect_decodes_worker_result_and_owned_close_api():
    worker = object.__new__(PersistentJavaWorker)
    sent = {}
    result = {"connected": False, "generation": 3, "instance_id": "worker-instance-test"}

    def submit(kind, payload, *, request_id=None, rpc_timeout_s=None):
        sent.update({"kind": kind, "payload": payload, "request_id": request_id,
                     "rpc_timeout_s": rpc_timeout_s})
        return {"ok": True, "status": "SUCCEEDED", "result": dict(result)}

    worker.submit = submit
    decoded = worker.client().disconnect(request_id="disconnect-test", rpc_timeout_s=2.0)
    assert sent == {"kind": "disconnect", "payload": {}, "request_id": "disconnect-test",
                    "rpc_timeout_s": 2.0}
    assert decoded == result  # production RemoteClient returns the decoded result, not its wire envelope

    class OwnedChild:
        pid = 43210
        def __init__(self):
            self.returncode = None
            self.terminated = False
        def poll(self):
            return self.returncode
        def terminate(self):
            self.terminated = True
            self.returncode = 0
        def wait(self, timeout=None):
            self.returncode = 0
            return self.returncode

    import threading
    child = OwnedChild()
    worker._lock = threading.RLock()
    worker._process = child
    worker._port = 23456
    worker._generation = 3
    worker._classes_dir = Path("classes")
    worker.close()
    assert child.terminated is True
    assert worker._process is None
    assert worker._port is None
    assert worker._generation is None
    assert worker._classes_dir is None


def test_worker_disconnect_verification_requires_same_identity_and_direct_result_shape():
    before = {"ok": True, "status": "HEALTHY", "instance_id": "worker-1",
              "generation": 2, "connected": True, "queued_or_running": 0}
    direct_result = {"connected": False, "instance_id": "worker-1", "generation": 3}
    after = {"ok": True, "status": "HEALTHY", "instance_id": "worker-1",
             "generation": 3, "connected": False, "queued_or_running": 0}
    assert runner._worker_disconnect_verified(direct_result, before, after) is True
    assert runner._worker_disconnect_verified(
        {"ok": True, "status": "SUCCEEDED", "result": direct_result}, before, after) is False
    assert runner._worker_disconnect_verified(
        dict(direct_result, instance_id="other-worker"), before, after) is False
    assert runner._worker_disconnect_verified(
        dict(direct_result, generation=4), before, after) is False
    assert runner._worker_disconnect_verified(
        direct_result, before, dict(after, connected=True)) is False
    assert runner._worker_disconnect_verified(
        direct_result, dict(before, queued_or_running=False), after) is False
