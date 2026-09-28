# AGENTS.md — comsol-server-mcp

## Project Overview

COMSOL MCP Server (`comsol-mcp` v0.1.9). An attach-first MCP bridge that connects to a running COMSOL Multiphysics Server, loads a main .mph model, locks it, and drives the same server-side model that a COMSOL Desktop client visualizes in real time.

## Architecture

```
comsol_mcp/
  __init__.py              # from .mcp_server import main
  _server.py               # FastMCP instance, global state, constants, classification sets
  _state.py                # Workflow persistence, status/operations logging, path/port utils, _run_tool
  _connection.py           # Disconnect, client shell, _require_client
  _model.py                # Model adopt/prune, visible-main lock mechanism
  _model_ops.py            # Pure helpers: parameters, expressions, core metrics, tree, geometry
  _tools_connection.py     # server_info, check_server_port, server_start, server_connect, server_disconnect
  _tools_workflow.py       # Workflow tools, visible-main lifecycle, model_tree, run_study
  _tools_model.py          # model_create, model_load, prune_loaded_models
  _tools_params.py         # get_parameters, set_parameters, evaluate_expressions, get_core_metrics
  _tools_geometry.py       # ensure_component/geometry/mesh, create/update/delete/run_feature
  _tools_snapshot.py       # run_visible_main_iteration, save_main_model_snapshot, commit_current_main_model, save_model
  mcp_server.py            # Thin entrypoint: imports + register + main()
```

The default full profile currently registers **112 MCP tool names**; registration is runtime-discovered, and the exact
inventory assertion is maintained in `tests/test_registration.py::EXPECTED_TOOLS`. The 51 legacy names retain their
existing arguments. Domain/expert profiles narrow publication while keeping the managed fallback available. The gateway retains legacy arguments and adds an optional `execution`
object for model identity, expected revision, idempotency and timeout settings.
All production calls route through the control daemon. Registration alone is
not evidence that a capability passed real COMSOL acceptance.

The stdio host uses `_mcp_gateway.py` and `_control_client.py`; it does not own
the lifetime of a computation. `_control_daemon.py` serializes engine work,
`_managed_backend.py` binds legacy callbacks to `_execution_service.py`, and
`_java_worker.py` communicates with the persistent Java Worker. `_operation_store.py`
persists jobs and results; `_runtime_state.py` restores identity conservatively.
Cached health/status/log queries do not enter the engine queue.

## Build and Run

```bash
pip install -e .          # install in editable mode
pip install -e ".[dev]"   # with pytest
python -m comsol_mcp.mcp_server   # start MCP server
pytest                           # run the evolving non-COMSOL unit suite
```

## Module Dependency Graph

```
_server.py  (no deps)
  -> _state.py
    -> _connection.py
      -> _model.py
        -> _model_ops.py (no global state deps)

Tool modules depend on the above:
  _tools_connection -> _server, _state, _connection, _model
  _tools_workflow   -> _server, _state, _model, _tools_connection (cross-module call)
  _tools_model      -> _server, _state, _model
  _tools_params     -> _state, _model, _model_ops
  _tools_geometry   -> _state, _model, _model_ops
  _tools_snapshot   -> _server, _state, _model, _tools_params (cross-module call)
```

No circular imports.

## Key Constraints

- **Target runtime matrix:** macOS Apple Silicon (Darwin aarch64), Commercial COMSOL Multiphysics 6.4 (Build 293), Java 11 (Amazon Corretto 11.0.28).
  Other environments (Windows, Intel Mac, COMSOL 6.3) remain UNVERIFIED. Cloud Hermes delivery remains `HOST_DELIVERY_UNVERIFIED` until verified with live cloud vision model.
- **Python 3.10+** (tested on Python 3.14.7)
- **51 legacy tool names** retain existing arguments; the default full profile currently registers 112 MCP tool names
  (exact inventory: `tests/test_registration.py::EXPECTED_TOOLS`).
- **Entrypoint backward compat**: `python -m comsol_mcp.mcp_server`, `from comsol_mcp.mcp_server import main`
- **Visible-main lock**: After `load_visible_main_model()`, tools are guarded by identity check (tag/label/path)
- **State**: legacy globals in `_server.py` remain guarded by `_runtime_lock`;
  execution identity and durable job state belong to the control service.
- **Shared Server**: never terminate it to implement a request timeout. Worker
  endpoint locks and the single engine queue apply across models. An expired
  RPC wait or execution deadline does not prove that COMSOL stopped.

## Tool Classification

| Category | Tools | Behavior when locked |
|---|---|---|
| SAFE_READ | server_info, check_server_port, workflow_info, model_tree, get_parameters, evaluate_expressions, get_core_metrics | Always allowed |
| SAFE_WRITE | set_parameters, ensure_*, create/update/delete/run_feature, run_study, run_visible_main_iteration, save_main_model_snapshot, save_model | Allowed if identity matches |
| RESTRICTED | commit_current_main_model, model_create, model_load, prune_loaded_models | Blocked entirely when locked |

This table describes the legacy visible-main guard only. The production effect
registry in `_execution_contract.py` also applies: result evaluation is an
ephemeral mutation and requires `project_write` permission and a current
`expected_revision`. A legacy `SAFE_READ` label does not bypass these checks.

Managed calls use `execution.model_ref` returned by load/adopt and
`execution.expected_revision` from the latest response. Reuse the same
`idempotency_key` for an uncertain retry of the same request; a different body
with that key is rejected. `server_instance_id` is a conservative Worker
connection epoch, not an independently observed COMSOL process UUID. A Worker
replacement invalidates old refs even when the COMSOL Server survives.

`rpc_timeout_s` limits caller waiting, `queue_timeout_s` limits time before
dispatch, and `execution_timeout_s` records an exceeded running deadline
without killing the Server. A null execution deadline has no time limit.
`no_progress_warning_s` emits an observation warning, not a cancellation.
Inspect the original job after a timeout; do not submit a replacement solve.

## Cross-Module Tool Calls

Some tools call other tools via direct function call (not MCP dispatch):
- `_start_visible_main_workflow_payload` -> `server_connect`, `load_visible_main_model`, `verify_visible_main_session`
- `run_visible_main_iteration` -> `get_core_metrics`, `save_main_model_snapshot`
- `load_current_main_model` -> `load_visible_main_model`

These are resolved by importing the target function from the other module.

## How to Add a New Tool

1. Add the function to the appropriate `_tools_*.py` module
2. Add `mcp_instance.add_tool(function_name)` to that module's `register()` function
3. If the tool modifies model state, add it to the appropriate classification set in `_server.py`
4. Classify its effect in `_execution_contract.py`; unclassified production
   operations fail closed. Add execution-contract and actual engine evidence
   appropriate to the operation.

## How to Add a New Helper

- Pure function (no global state) -> `_model_ops.py`
- State-aware function -> `_state.py` or `_model.py`
- Connection lifecycle -> `_connection.py`

## Testing

- `pytest` runs the non-COMSOL unit suite; use its actual result/count in
  evidence rather than copying a stale count.
- Tests cover: sanitize_label, normalize_properties, coerce_eval, last_scalar, port_is_open, workflow_state IO, friendly_connection_error, numeric_result, resolve_path, normcase_path, tool registration, classification sets
- Integration tests requiring a live COMSOL Server are marked `@pytest.mark.comsol_server` and skipped by default


## Current execution and phase synchronization

Current user instructions (2026-09-27) supersede older phase-stop and progress-file instructions. Continue `docs/full_project_execution/MASTER_GOAL.md` through all original W01–W26 requirements without waiting for approval between phases. The main Agent plans, handles architecture and reviews results; delegate defined implementation, testing and debugging to the same GPT-6 Luna executor with Max reasoning when available. Final acceptance requires a fresh independent Reviewer against the complete original scope, followed by repair and re-review. Software checks alone never establish native or scientific acceptance.

- Read root `PROGRESS.md` first, then the existing `docs/full_project_execution/state/RESUME.json` and `TASKS.json`. Preserve active/UNKNOWN jobs and reconcile their original identities before any new solve. Do not bootstrap over this checkout or reinitialize existing state.
- Root `PROGRESS.md` is the sole concise progress entry: current stage/work packages, PASS/PARTIAL/BLOCKED/NOT_RUN, actual capabilities and tests, unresolved items, concrete next action and recovery/rebuild entry. Reuse existing state files; do not create parallel progress systems.
- Every completed clear phase must be committed and synchronized immediately to `origin` (`https://github.com/Everwalker/comsol-mcp.git`), then work continues. This is standing authorization for ordinary non-force pushes to `main`.
- Commit only necessary production source/configuration/scripts, tests, rebuildable fixtures, existing execution state and compact evidence summaries/manifests/key request-results. Preserve large raw evidence locally with identity/hash/rebuild pointers. Exclude credentials, token/preferences, venv/cache/runtime directories, commercial software/JARs, compiled output and duplicate large artifacts.
- Before each phase commit, run `git status`, `git fetch origin main` and `git log --oneline --decorate -5`. Inspect unexpected remote changes and merge/rebase safely without replacing unknown changes. Stage an explicit file allowlist and commit with a concise stage description.
- Push with `git push origin HEAD:main`; never force-push or rewrite remote history. If branch protection requires a PR, use it and report pending merge. Preserve local commits if authentication, network or protection blocks delivery.
- After pushing, run `git fetch origin main`, `git rev-parse HEAD` and `git rev-parse origin/main`. Only matching hashes justify reporting GitHub synchronization complete.
- Continue the next authorized work immediately after synchronization. Do not publish unexecuted checks as PASS or mark the full project complete while required work, true blockers or final review remain.
