#!/usr/bin/env python3
"""MCP tools: solver configuration and async study execution."""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

import comsol_mcp._server as _srv
from comsol_mcp._state import (
    _run_tool, _run_tool_readonly, _json, _setup_logging,
    _create_background_job, _update_background_job, _read_background_job,
    _append_operation, _safe_model_label,
)
from comsol_mcp._model import _require_visible_main
from comsol_mcp._solver_ops import (
    _list_solver_configs, _list_solver_features,
    _create_solver_config, _configure_solver_feature,
    _list_segregated_steps, _add_segregated_step,
)


# ---------------------------------------------------------------------------
# Solver configuration tools
# ---------------------------------------------------------------------------
def list_solver_config() -> str:
    """List all solver configurations with their features.

    Returns solver tags, feature counts, and feature tags for each solver
    configuration in the current model.
    """
    """List all solver configurations with their features.

    Returns solver tags, feature counts, and feature tags for each solver
    configuration in the current model.
    """

    def _impl() -> dict[str, Any]:
        model = _require_visible_main("list_solver_config")
        configs = _list_solver_configs(model)
        return {
            "label": _safe_model_label(model),
            "solver_configs": configs,
            "count": len(configs),
        }

    return _run_tool_readonly("list_solver_config", _impl)


def create_solver_config(sol_tag: str, study_tag: str) -> str:
    """Create a solver configuration linked to a study.

    The solver tag identifies the new solver config. The study tag links it
    to an existing study for solving.
    """

    def _impl() -> dict[str, Any]:
        model = _require_visible_main("create_solver_config")
        stag = sol_tag.strip()
        sttag = study_tag.strip()
        if not stag:
            raise ValueError("sol_tag is required.")
        if not sttag:
            raise ValueError("study_tag is required.")
        result = _create_solver_config(model, stag, sttag)
        return result

    return _run_tool("create_solver_config", _impl)


def list_solver_features(sol_tag: str) -> str:
    """List features and sub-features under a solver configuration.

    Returns feature tags, labels, properties, and any sub-features (e.g.
    segregated solver steps).
    """

    def _impl() -> dict[str, Any]:
        model = _require_visible_main("list_solver_features")
        stag = sol_tag.strip()
        if not stag:
            raise ValueError("sol_tag is required.")
        features = _list_solver_features(model, stag)
        return {
            "label": _safe_model_label(model),
            "sol_tag": stag,
            "features": features,
            "count": len(features),
        }

    return _run_tool_readonly("list_solver_features", _impl)


def configure_solver(
    sol_tag: str,
    feature_tag: str,
    properties_json: str = "[]",
) -> str:
    """Configure a solver feature's properties.

    properties_json format: [{"name": "prop_name", "value": "value"}] or
    {"prop_name": "value", ...}. For array-valued properties use
    [{"name": "prop_name", "values": ["v1", "v2"]}].

    To add segregated solver steps, use properties_json with:
    [{"name": "add_segregated_step", "value": "{\"step_tag\": \"step1\", \"variables\": [\"u\", \"v\"]}"}]
    Multiple steps can be added in a single call.
    """

    def _impl() -> dict[str, Any]:
        model = _require_visible_main("configure_solver")
        stag = sol_tag.strip()
        ftag = feature_tag.strip()
        if not stag:
            raise ValueError("sol_tag is required.")
        if not ftag:
            raise ValueError("feature_tag is required.")

        from comsol_mcp._model_ops import _normalize_properties
        from comsol_mcp._state import ToolExecutionError
        parsed = json.loads(properties_json or "[]")
        if isinstance(parsed, dict):
            parsed = [{"name": k, "values" if isinstance(v, list) else "value": v} for k, v in parsed.items()]
        if not isinstance(parsed, list):
            raise ValueError("properties_json must be an object or array.")
        plan = []
        for item in parsed:
            if not isinstance(item, dict):
                raise ValueError("Every solver update must be an object.")
            if item.get("name") == "add_segregated_step":
                step = json.loads(item.get("value", "{}"))
                if not isinstance(step, dict) or not isinstance(step.get("step_tag"), str) or not step["step_tag"].strip():
                    raise ValueError("A segregated step requires a nonempty step_tag.")
                variables = step.get("variables", [])
                if not isinstance(variables, list) or any(not isinstance(v, str) or not v.strip() for v in variables):
                    raise ValueError("Segregated variables must be an array of nonempty strings.")
                plan.append({"action": "add_segregated_step", "step_tag": step["step_tag"].strip(), "variables": variables})
            else:
                _normalize_properties(json.dumps([item]))
                plan.append({"action": "set_property", "property": item})
        applied = []
        for index, update in enumerate(plan):
            try:
                if update["action"] == "add_segregated_step":
                    outcome = _add_segregated_step(model, stag, ftag, update["step_tag"], update["variables"])
                else:
                    outcome = _configure_solver_feature(model, stag, ftag, json.dumps([update["property"]]))
                applied.append({**update, "result": outcome})
            except Exception as exc:
                raise ToolExecutionError("Solver batch stopped after a runtime failure.", data={
                    "applied": applied, "failed": {**update, "error": str(exc)},
                    "not_executed": plan[index + 1:], "atomic": False,
                    "partial_change": bool(applied), "failed_item_may_have_changed": True,
                    "safe_retry": False,
                }) from exc
        return {
            "sol_tag": stag,
            "feature_tag": ftag,
            "applied": applied,
        }

    return _run_tool("configure_solver", _impl)


# ---------------------------------------------------------------------------
# Async study execution tools
# ---------------------------------------------------------------------------
def run_study_async(study_tag: str = "") -> str:
    """Run a COMSOL study in a background thread and return a job ID for polling.

    This is the async version of run_study. It starts the solve in a background
    thread and returns immediately with a job_id. Use run_study_status(job_id)
    to poll for completion.

    The MCP client will NOT disconnect during long solves because this tool
    returns immediately.
    """

    def _impl() -> dict[str, Any]:
        model = _require_visible_main("run_study_async")
        study = study_tag.strip()
        payload = {
            "study_tag": study,
            "model_label": _safe_model_label(model),
        }
        job_id = _create_background_job("run_study", payload)

        def _worker() -> None:
            _update_background_job(job_id, status="running", stage="solving")
            try:
                with _srv._runtime_lock:
                    from comsol_mcp._tools_workflow import _run_study_on_model
                    _run_study_on_model(model, study)
                _update_background_job(
                    job_id,
                    status="succeeded",
                    stage="completed",
                    result={"study_tag": study, "model_label": _safe_model_label(model)},
                )
                _append_operation({
                    "tool": "run_study_async.worker",
                    "success": True,
                    "error": "",
                    "data": {"job_id": job_id, "study_tag": study},
                })
            except Exception as exc:
                logging.exception("Background run_study %s failed", job_id)
                _update_background_job(job_id, status="failed", stage="failed", error=str(exc))
                _append_operation({
                    "tool": "run_study_async.worker",
                    "success": False,
                    "error": str(exc),
                    "data": {"job_id": job_id, "study_tag": study},
                })

        thread = threading.Thread(target=_worker, name=job_id, daemon=True)
        thread.start()
        return {
            "job_id": job_id,
            "status": "running",
            "study_tag": study or "(default)",
            "poll_tool": "run_study_status",
            "message": "Study started in background. Poll run_study_status(job_id) for progress.",
        }

    return _run_tool("run_study_async", _impl)


def run_study_status(job_id: str = "") -> str:
    """Return status for the latest or specified async run_study job.

    Pass the job_id returned by run_study_async, or leave empty to get the
    latest job status.
    """

    _setup_logging()
    job = _read_background_job(job_id)
    success = bool(job)
    return _json({
        "success": success,
        "job": job,
        "message": "" if success else "No background run_study job found.",
    })


def register(mcp_instance) -> None:
    mcp_instance.add_tool(list_solver_config)
    mcp_instance.add_tool(create_solver_config)
    mcp_instance.add_tool(list_solver_features)
    mcp_instance.add_tool(configure_solver)
    mcp_instance.add_tool(run_study_async)
    mcp_instance.add_tool(run_study_status)
