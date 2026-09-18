# W01 static F01-F12 evidence

All findings are source-level evidence against the frozen baseline. No COMSOL engine was started.

## F01 — STATIC_CONFIRMED_NOT_ENGINE_REPRODUCED

- Source: `comsol_mcp/_model_ops.py`
- Lines: def _clear_numerical: 75; _clear_numerical(model): 120,134,152,170,265
- Finding: Aggregate evaluation clears every numerical tag; W04 must replace it.
- Engine: NOT_RUN: W01 is an inventory/protocol gate; runtime reproduction belongs to G0/G1 tests.

## F02 — STATIC_CONFIRMED_NOT_ENGINE_REPRODUCED

- Source: `comsol_mcp/_physics_ops.py`
- Lines: var_node.set("name", name): 275; var_node.set("expr", expression): 276
- Finding: Variable creation writes pseudo-properties instead of the documented variable-name setter.
- Engine: NOT_RUN: W01 is an inventory/protocol gate; runtime reproduction belongs to G0/G1 tests.

## F03 — STATIC_CONFIRMED_NOT_ENGINE_REPRODUCED

- Source: `comsol_mcp/_physics_ops.py`
- Lines: feat = phys.feature(feature_tag): 202; def _set_physics_selection: 197
- Finding: Selection helper always resolves a feature, so a physics-level target is unreachable.
- Engine: NOT_RUN: W01 is an inventory/protocol gate; runtime reproduction belongs to G0/G1 tests.

## F04 — STATIC_CONFIRMED_NOT_ENGINE_REPRODUCED

- Source: `comsol_mcp/_connection.py`
- Lines: future.result(timeout=timeout): 25; executor.shutdown(wait=False): 32
- Finding: Timeout stops waiting only; no engine cancellation evidence exists.
- Engine: NOT_RUN: W01 is an inventory/protocol gate; runtime reproduction belongs to G0/G1 tests.

## F05 — STATIC_CONFIRMED_NOT_ENGINE_REPRODUCED

- Source: `comsol_mcp/_state.py`
- Lines: def _run_tool_readonly: 437; _runtime_lock.acquire(timeout=120.0): 456
- Finding: Read-only calls skip the lock while model-facing tools are classified read-only; write lock has a hardcoded 120 seconds.
- Engine: NOT_RUN: W01 is an inventory/protocol gate; runtime reproduction belongs to G0/G1 tests.

## F06 — STATIC_CONFIRMED_NOT_ENGINE_REPRODUCED

- Source: `comsol_mcp/_tools_workflow.py`
- Lines: _prune_loaded_models_locked: 26,110,488
- Finding: Visible-main workflow calls pruning; W04 must remove implicit pruning.
- Engine: NOT_RUN: W01 is an inventory/protocol gate; runtime reproduction belongs to G0/G1 tests.

## F07 — STATIC_CONFIRMED_NOT_ENGINE_REPRODUCED

- Source: `comsol_mcp/_model_ops.py`
- Lines: def _coerce_eval_value: 30; def _last_scalar: 43
- Finding: Evaluation coercion can stringify types and collapses values to the last scalar.
- Engine: NOT_RUN: W01 is an inventory/protocol gate; runtime reproduction belongs to G0/G1 tests.

## F08 — STATIC_CONFIRMED_NOT_ENGINE_REPRODUCED

- Source: `comsol_mcp/_model_ops.py`
- Lines: feature.selection().geom("geom1": 138,156,174,312,315; dim = 2  # Default to 2D: 271
- Finding: Aggregate helpers hardcode geom1 in several paths and default unknown geometry to 2D; they do not establish complete dataset/time semantics.
- Engine: NOT_RUN: W01 is an inventory/protocol gate; runtime reproduction belongs to G0/G1 tests.

## F09 — STATIC_CONFIRMED_NOT_ENGINE_REPRODUCED

- Source: `comsol_mcp/_tools_params.py`
- Lines: cover_domains = [1, 2]: 110; steel_boundaries = [5, 6, 7, 8]: 111; "wAccAvg": 115,130,137
- Finding: Legacy metrics use fixed corrosion-model variables, domains, and boundaries rather than an explicit task metric definition.
- Engine: NOT_RUN: W01 is an inventory/protocol gate; runtime reproduction belongs to G0/G1 tests.

## F10 — STATIC_CONFIRMED_NOT_ENGINE_REPRODUCED

- Source: `comsol_mcp/_tools_workflow.py`
- Lines: timeout=300.0: 105,483
- Finding: Visible-main model loading has a hardcoded 300-second timeout; the separate 120-second lock is recorded by F05.
- Engine: NOT_RUN: W01 is an inventory/protocol gate; runtime reproduction belongs to G0/G1 tests.

## F11 — STATIC_CONFIRMED_NOT_ENGINE_REPRODUCED

- Source: `comsol_mcp/_tools_workflow.py`
- Lines: def verify_visible_main_session: 155; desktop: 149,319
- Finding: Workflow verification is model/session metadata, not Desktop-current-tab proof.
- Engine: NOT_RUN: W01 is an inventory/protocol gate; runtime reproduction belongs to G0/G1 tests.

## F12 — STATIC_CONFIRMED_NOT_ENGINE_REPRODUCED

- Source: `comsol_mcp/_physics_ops.py`
- Lines: if feature_tag in existing:: 173; "Physics already exists.": 140; "Feature already exists.": 174
- Finding: Existing-tag creates return success-like payloads without checking the requested type; W09 must also generate compatibility documentation/schema.
- Engine: NOT_RUN: W01 is an inventory/protocol gate; runtime reproduction belongs to G0/G1 tests.
