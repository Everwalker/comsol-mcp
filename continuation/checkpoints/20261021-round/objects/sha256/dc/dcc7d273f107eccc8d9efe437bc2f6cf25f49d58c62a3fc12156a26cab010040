#!/usr/bin/env python3
"""Generate the bounded G2 registry reference from the action catalog."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from comsol_mcp._g2_registry import ENTRIES, IMPLEMENTED_OPERATIONS

OUTPUT = ROOT / "docs" / "comsol_mcp_design_v1" / "G2_API.md"


def render() -> str:
    lines = [
        "# COMSOL MCP G2 API",
        "",
        "This file is generated from `02_ACTION_CATALOG.json` by `tools/generate_g2_api.py`.",
        "It describes the W08-W12 control surface; `SUPPORTED_UNVERIFIED` means the software route exists and still requires the recorded COMSOL runtime evidence.",
        "Published `input_schema` and `output_schema` describe the effective production wire. `catalog_input_schema` is retained for traceability; it is not an assertion that the historical catalog model_ref string or ActionResult(ok=...) shape is accepted unchanged.",
        "",
        "## Wire envelope",
        "",
        "Model calls use `registry_call(operation_id, arguments, execution)` (or `operation_call`). The nested operation is validated again at the control daemon. `execution` carries the session, model reference, expected revision, idempotency key, and wait budgets. Responses retain a structured `success` boolean, `data`, `error`, partial/unknown signals, and execution metadata; MCP transport sets `isError` from `success`.",
        "",
        "## Executable catalog",
        "",
        "| Operation | MCP fallback | Domain | Effect | Scope | Gate | Status |",
        "|---|---|---|---|---|---|---|",
    ]
    for entry in sorted(ENTRIES, key=lambda item: item.operation_id):
        if entry.operation_id in IMPLEMENTED_OPERATIONS:
            lines.append(f"| `{entry.operation_id}` | `{entry.mcp_tool_name}` | {entry.domain} | `{entry.effect}` | `{entry.scope}` | `{entry.gate}` | `{entry.implementation_status}` |")
    lines += ["", "## Operation schemas", "", "The following JSON object is generated from the catalog and includes every executable W08-W12 operation.", "", "```json"]
    schemas = {
        entry.operation_id: {
            "mcp_tool_name": entry.as_dict()["mcp_tool_name"],
            "effect": entry.as_dict()["effect"],
            "scope": entry.as_dict()["scope"],
            "gate": entry.as_dict()["gate"],
            "implementation_status": entry.as_dict()["implementation_status"],
            "input_schema": entry.as_dict()["input_schema"],
            "catalog_input_schema": entry.as_dict()["catalog_input_schema"],
            "output_schema": entry.as_dict()["output_schema"],
            "wire_compatibility": entry.as_dict()["wire_compatibility"],
            "output_contract": entry.as_dict()["output_contract"],
            "required_tests": entry.as_dict()["required_tests"],
        }
        for entry in sorted(ENTRIES, key=lambda item: item.operation_id)
        if entry.operation_id in IMPLEMENTED_OPERATIONS
    }
    lines.append(json.dumps(schemas, ensure_ascii=False, indent=2, sort_keys=True))
    lines += ["```", "", "## Explicit limitations", "", "- Registry publication does not establish COMSOL engine acceptance.", "- Model writes, trial, restore, and trusted Java execution require the current control service revision gate and a live task-owned isolation receipt with an exact IPv4 loopback listener and connected-client observation.", "- Java trusted code is a reviewed source path with SHA-256 and diagnostics; it is not an OS sandbox.", "- Offline help is local, version-separated, source-hashed data. Missing versions return `UNAVAILABLE` and unknown search hits return `NOT_FOUND`.", ""]
    return "\n".join(lines)


def main() -> None:
    OUTPUT.write_text(render(), encoding="utf-8")


if __name__ == "__main__":
    main()
