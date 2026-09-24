"""Tests to verify all MCP tools are registered correctly."""

EXPECTED_TOOLS = [
    "server_info",
    "check_server_port",
    "workflow_info",
    "configure_single_main_workflow",
    "load_visible_main_model",
    "start_visible_main_workflow",
    "start_visible_main_workflow_async",
    "visible_main_workflow_status",
    "verify_visible_main_session",
    "unlock_visible_main",
    "mcp_tool_audit",
    "run_visible_main_iteration",
    "load_current_main_model",
    "save_main_model_snapshot",
    "commit_current_main_model",
    "prune_loaded_models",
    "server_start",
    "server_connect",
    "server_disconnect",
    "model_create",
    "model_load",
    "model_tree",
    "get_parameters",
    "evaluate_expressions",
    "get_core_metrics",
    "set_parameters",
    "ensure_component",
    "ensure_geometry",
    "ensure_mesh",
    "create_feature",
    "update_feature",
    "delete_feature",
    "run_feature",
    "run_study",
    "save_model",
    "list_physics",
    "create_physics",
    "remove_physics",
    "list_physics_features",
    "create_physics_feature",
    "update_physics_feature",
    "remove_physics_feature",
    "set_physics_selection",
    "manage_variables",
    "list_solver_config",
    "create_solver_config",
    "list_solver_features",
    "configure_solver",
    "run_study_async",
    "run_study_status",
    "runtime_poc_v64",
    "session_health",
    "model_inspect",
    "model_adopt",
    "job_status",
    "job_log",
    "job_result",
    "job_reconcile",
    "job_list",
    "job_cancel",
    "job_wait",
    "registry_list",
    "registry_describe",
    "registry_search",
    "registry_manifest",
    "registry_call",
    "operation_describe",
    "operation_call",
    "plot.render",
    "plot_render",
    "validate.preflight",
    "validate_preflight",
    "validate.structure",
    "validate_structure",
    "validate.boundary_conditions",
    "validate_boundary_conditions",
    "validate.expressions",
    "validate_expressions",
    "validate.solution",
    "validate_solution",
    "validate.conservation",
    "validate_conservation",
    "validate.convergence",
    "validate_convergence",
    "validate.report",
    "validate_report",
]


def test_all_tools_registered():
    import comsol_mcp.mcp_server
    from comsol_mcp._server import mcp

    registered = set(mcp._tool_manager._tools.keys())
    expected = set(EXPECTED_TOOLS)
    assert registered == expected, (
        f"Missing: {expected - registered}, "
        f"Extra: {registered - expected}"
    )


def test_tool_count():
    import comsol_mcp.mcp_server
    from comsol_mcp._server import mcp

    assert len(mcp._tool_manager._tools) == len(EXPECTED_TOOLS)


def test_every_public_tool_uses_the_execution_gateway():
    import comsol_mcp.mcp_server as entry
    import inspect
    for tool in entry.mcp._tool_manager._tools.values():
        assert inspect.iscoroutinefunction(tool.fn)
        assert "execution" in tool.parameters["properties"]
