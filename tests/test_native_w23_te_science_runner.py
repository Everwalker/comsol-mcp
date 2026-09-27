from __future__ import annotations

import json
import copy

import pytest

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
                "metadata": {"type": "call", "method": "run"}}
    assert runner._is_study_run_job({"operation": {"operation": "study.run"}})
    assert not runner._is_study_run_job({"operation": {"operation": "result.mode_overlap"}})

    class Store:
        def list_jobs(self, offset=0, limit=1000, project_id=None):
            rows = [
                {"job_id": "study-job", "status": "SUCCEEDED",
                 "operation": {"operation": "study.run"}},
                {"job_id": "overlap-job", "status": "SUCCEEDED",
                 "operation": {"operation": "result.mode_overlap"}},
            ]
            return rows[offset:offset + limit]

        def events(self, job_id, offset=0, limit=1000):
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
    assert rows[0]["job_id"] == "study-job"


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
