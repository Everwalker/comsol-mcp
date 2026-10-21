#!/usr/bin/env python3
"""Capture the fixed-baseline FastMCP registry without changing it."""
from __future__ import annotations

import inspect
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import comsol_mcp.mcp_server  # Registers the baseline's tools.
from comsol_mcp._server import mcp

RUN_ID = os.environ.get("W01_RUN_ID", "manual")
OUT = ROOT / "evidence" / "w01" / "runs" / RUN_ID

# Proposed logical routes are selected only from the supplied action catalog.
# They are a W01 migration inventory, not claims that W09 implements them yet.
# An empty tuple means the old behaviour is explicitly LEGACY_ONLY.
ROUTES = {
    "server_info": ("runtime_inspect",), "check_server_port": ("runtime_doctor",),
    "workflow_info": (), "configure_single_main_workflow": ("project_contract_set", "project_policy_set"),
    "load_visible_main_model": ("session_connect", "model_load", "desktop_bind"), "start_visible_main_workflow": ("session_start", "session_connect", "model_load"),
    "start_visible_main_workflow_async": ("session_start", "session_connect", "model_load", "job_status"), "visible_main_workflow_status": ("job_status",),
    "verify_visible_main_session": ("model_inspect", "desktop_status"), "unlock_visible_main": (),
    "mcp_tool_audit": ("registry_manifest",), "run_visible_main_iteration": ("transaction_apply", "study_run", "metric_evaluate"),
    "load_current_main_model": ("model_load",), "save_main_model_snapshot": ("checkpoint_create",),
    "commit_current_main_model": ("transaction_apply", "model_save"), "prune_loaded_models": ("model_close",),
    "server_start": ("session_start",), "server_connect": ("session_connect",), "server_disconnect": ("session_disconnect",),
    "model_create": ("model_create",), "model_load": ("model_load",), "model_tree": ("model_tree",),
    "get_parameters": ("parameter_list",), "evaluate_expressions": ("result_evaluate",),
    "get_core_metrics": ("metric_evaluate",), "set_parameters": ("parameter_set",),
    "ensure_component": ("node_create",), "ensure_geometry": ("geometry_sequence_create",),
    "ensure_mesh": ("mesh_create",), "create_feature": ("geometry_feature_create",),
    "update_feature": ("geometry_feature_update",), "delete_feature": ("geometry_feature_remove",),
    "run_feature": ("geometry_build",), "run_study": ("study_run",),
    "save_model": ("model_save",), "list_physics": ("physics_list",),
    "create_physics": ("physics_create",), "remove_physics": ("physics_remove",),
    "list_physics_features": ("physics_inspect",), "create_physics_feature": ("physics_feature_create",),
    "update_physics_feature": ("physics_feature_update",), "remove_physics_feature": ("physics_feature_remove",),
    "set_physics_selection": ("physics_selection_set",), "manage_variables": ("variable_list", "variable_group_create", "variable_set", "variable_remove"),
    "list_solver_config": ("solver_list",), "create_solver_config": ("solver_create",),
    "list_solver_features": ("solver_inspect",), "configure_solver": ("solver_feature_update",),
    "run_study_async": ("study_run", "job_status"), "run_study_status": ("job_status",),
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    catalog = json.loads((ROOT / "docs/comsol_mcp_design_v1/02_ACTION_CATALOG.json").read_text(encoding="utf-8"))
    catalog_by_tool = {operation["mcp_tool_name"]: operation for operation in catalog["operations"]}
    registry = []
    for name, tool in sorted(mcp._tool_manager._tools.items()):
        targets = ROUTES[name]
        assert all(target in catalog_by_tool for target in targets), f"Unknown catalog target for {name}: {targets}"
        legacy_props = set(tool.parameters.get("properties", {}))
        target_records = []
        for target in targets:
            operation = catalog_by_tool[target]
            target_props = set(operation["input_schema"].get("properties", {}))
            target_records.append({
                "mcp_tool_name": target,
                "operation_id": operation["operation_id"],
                "input_schema": operation["input_schema"],
                "output_contract": operation["output_contract"],
                "same_name_parameters": sorted(legacy_props & target_props),
                "legacy_only_parameters": sorted(legacy_props - target_props),
                "new_required_parameters": sorted(set(operation["input_schema"].get("required", [])) - legacy_props),
                "context_not_in_legacy_contract": [key for key in ("project_id", "session_id", "model_ref", "expected_revision", "idempotency_key") if key in target_props and key not in legacy_props],
            })
        registry.append({
            "legacy_tool": name,
            "callable": f"{tool.fn.__module__}.{tool.fn.__qualname__}",
            "description": tool.description,
            "input_schema": tool.parameters,
            "python_signature": str(inspect.signature(tool.fn)),
            "mapping_status": "LEGACY_ONLY_NO_DIRECT_EQUIVALENT" if not targets else "CATALOG_TARGETS_PROPOSED_NOT_IMPLEMENTED",
            "catalog_targets": target_records,
            "legacy_output_schema": tool.fn_metadata.output_schema,
            "legacy_output_note": "FastMCP wraps the baseline return as object.result:string; the string contains legacy JSON and is not a typed ActionResult.",
            "migration_status": "W01_AUDITED_NOT_IMPLEMENTED",
        })
    assert len(registry) == 50, f"Expected 50 legacy tools, found {len(registry)}"
    missing = set(ROUTES) - {row["legacy_tool"] for row in registry}
    assert not missing, f"Routes missing tools: {sorted(missing)}"
    payload = {
        "baseline_commit": "ccca65aa8277d1205c5de5fb6221e460aca5997a",
        "tool_count": len(registry),
        "source": "FastMCP in-process registry after comsol_mcp.mcp_server import",
        "compatibility_boundary": "Mapping is a W01 inventory; no new registry/protocol layer is installed.",
        "tools": registry,
    }
    (OUT / "legacy_tool_registry_and_mapping.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
