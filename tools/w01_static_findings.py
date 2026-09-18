#!/usr/bin/env python3
"""Record the baseline source locations behind F01--F12 for W01."""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = os.environ.get("W01_RUN_ID", "manual")
OUT = ROOT / "evidence" / "w01" / "runs" / RUN_ID

FINDINGS = {
    "F01": ("comsol_mcp/_model_ops.py", ["def _clear_numerical", "_clear_numerical(model)"], "STATIC_CONFIRMED_NOT_ENGINE_REPRODUCED", "Aggregate evaluation clears every numerical tag; W04 must replace it."),
    "F02": ("comsol_mcp/_physics_ops.py", ['var_node.set("name", name)', 'var_node.set("expr", expression)'], "STATIC_CONFIRMED_NOT_ENGINE_REPRODUCED", "Variable creation writes pseudo-properties instead of the documented variable-name setter."),
    "F03": ("comsol_mcp/_physics_ops.py", ["feat = phys.feature(feature_tag)", "def _set_physics_selection"], "STATIC_CONFIRMED_NOT_ENGINE_REPRODUCED", "Selection helper always resolves a feature, so a physics-level target is unreachable."),
    "F04": ("comsol_mcp/_connection.py", ["future.result(timeout=timeout)", "executor.shutdown(wait=False)"], "STATIC_CONFIRMED_NOT_ENGINE_REPRODUCED", "Timeout stops waiting only; no engine cancellation evidence exists."),
    "F05": ("comsol_mcp/_state.py", ["def _run_tool_readonly", "_runtime_lock.acquire(timeout=120.0)"], "STATIC_CONFIRMED_NOT_ENGINE_REPRODUCED", "Read-only calls skip the lock while model-facing tools are classified read-only; write lock has a hardcoded 120 seconds."),
    "F06": ("comsol_mcp/_tools_workflow.py", ["_prune_loaded_models_locked"], "STATIC_CONFIRMED_NOT_ENGINE_REPRODUCED", "Visible-main workflow calls pruning; W04 must remove implicit pruning."),
    "F07": ("comsol_mcp/_model_ops.py", ["def _coerce_eval_value", "def _last_scalar"], "STATIC_CONFIRMED_NOT_ENGINE_REPRODUCED", "Evaluation coercion can stringify types and collapses values to the last scalar."),
    "F08": ("comsol_mcp/_model_ops.py", ['feature.selection().geom("geom1"', "dim = 2  # Default to 2D"], "STATIC_CONFIRMED_NOT_ENGINE_REPRODUCED", "Aggregate helpers hardcode geom1 in several paths and default unknown geometry to 2D; they do not establish complete dataset/time semantics."),
    "F09": ("comsol_mcp/_tools_params.py", ["cover_domains = [1, 2]", "steel_boundaries = [5, 6, 7, 8]", '"wAccAvg"'], "STATIC_CONFIRMED_NOT_ENGINE_REPRODUCED", "Legacy metrics use fixed corrosion-model variables, domains, and boundaries rather than an explicit task metric definition."),
    "F10": ("comsol_mcp/_tools_workflow.py", ["timeout=300.0"], "STATIC_CONFIRMED_NOT_ENGINE_REPRODUCED", "Visible-main model loading has a hardcoded 300-second timeout; the separate 120-second lock is recorded by F05."),
    "F11": ("comsol_mcp/_tools_workflow.py", ["def verify_visible_main_session", "desktop"], "STATIC_CONFIRMED_NOT_ENGINE_REPRODUCED", "Workflow verification is model/session metadata, not Desktop-current-tab proof."),
    "F12": ("comsol_mcp/_physics_ops.py", ["if feature_tag in existing:", '"Physics already exists."', '"Feature already exists."'], "STATIC_CONFIRMED_NOT_ENGINE_REPRODUCED", "Existing-tag creates return success-like payloads without checking the requested type; W09 must also generate compatibility documentation/schema."),
}


def lines_with(path: Path, needles: list[str]) -> dict[str, list[int]]:
    rows = path.read_text(encoding="utf-8").splitlines()
    return {needle: [index for index, row in enumerate(rows, 1) if needle in row] for needle in needles}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    records = []
    for key, (relative, needles, status, conclusion) in FINDINGS.items():
        path = ROOT / relative
        matches = lines_with(path, needles)
        records.append({"finding": key, "source": relative, "matches": matches, "status": status, "conclusion": conclusion, "engine_test": "NOT_RUN: W01 is an inventory/protocol gate; runtime reproduction belongs to G0/G1 tests."})
    payload = {"baseline_commit": "ccca65aa8277d1205c5de5fb6221e460aca5997a", "findings": records}
    (OUT / "F01_F12_static_evidence.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    report = ["# W01 static F01-F12 evidence", "", "All findings are source-level evidence against the frozen baseline. No COMSOL engine was started.", ""]
    for item in records:
        refs = "; ".join(f"{needle}: {','.join(map(str, found)) or 'NOT_FOUND'}" for needle, found in item["matches"].items())
        report.extend([f"## {item['finding']} — {item['status']}", "", f"- Source: `{item['source']}`", f"- Lines: {refs}", f"- Finding: {item['conclusion']}", f"- Engine: {item['engine_test']}", ""])
    (OUT / "F01_F12_static_evidence.md").write_text("\n".join(report), encoding="utf-8")


if __name__ == "__main__":
    main()
