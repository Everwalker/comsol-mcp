from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.resources as resources
import json
from pathlib import Path

import jsonschema

import comsol_mcp
import comsol_mcp.mcp_server as mcp_server


def main() -> int:
    errors: list[str] = []
    package_file = Path(comsol_mcp.__file__).resolve()
    package_root = package_file.parent
    root_text = str(package_root)
    if "site-packages" not in root_text:
        errors.append(f"package imported outside site-packages: {package_root}")
    if "source_stage" in root_text or "/repository/comsol_mcp" in root_text:
        errors.append(f"package import unexpectedly resolves to source: {package_root}")

    required = [
        "data/g2/02_ACTION_CATALOG.json",
        "data/g2/common.schema.json",
        "phase1_java/BoundPhase1Poc.java",
        "phase1_java/BoundPhase1ProductProbe.java",
        "phase1_java/BoundPhase1Reopen.java",
        "worker_java/PersistentComsolWorker.java",
    ]
    resource_rows = []
    for relative in required:
        item = resources.files("comsol_mcp").joinpath(*relative.split("/"))
        exists = item.is_file()
        size = len(item.read_bytes()) if exists else 0
        resource_rows.append({"path": relative, "exists": exists, "size": size,
                              "sha256": hashlib.sha256(item.read_bytes()).hexdigest() if exists else None})
        if not exists or size == 0:
            errors.append(f"missing/empty package resource: {relative}")

    catalog_item = resources.files("comsol_mcp").joinpath("data", "g2", "02_ACTION_CATALOG.json")
    common_item = resources.files("comsol_mcp").joinpath("data", "g2", "common.schema.json")
    catalog = json.loads(catalog_item.read_text(encoding="utf-8"))
    common = json.loads(common_item.read_text(encoding="utf-8"))
    try:
        jsonschema.Draft202012Validator.check_schema(common)
    except Exception as exc:
        errors.append(f"common schema invalid: {exc}")
    operations = catalog.get("operations")
    if not isinstance(operations, list) or len(operations) != 272:
        errors.append(f"catalog operations count={len(operations) if isinstance(operations, list) else None}, expected 272")
    operation_ids = [row.get("operation_id") for row in operations or []]
    if len(operation_ids) != len(set(operation_ids)):
        errors.append("catalog operation ids are not unique")
    schema_count = 0
    for row in operations or []:
        for key in ("input_schema",):
            schema = row.get(key)
            try:
                jsonschema.Draft202012Validator.check_schema(schema)
                schema_count += 1
            except Exception as exc:
                errors.append(f"{row.get('operation_id')} {key} invalid: {exc}")

    tool_names = sorted(mcp_server.mcp._tool_manager._tools)
    if len(tool_names) != 65:
        errors.append(f"registered MCP tools={len(tool_names)}, expected 65")
    version = importlib.metadata.version("comsol-mcp")
    result = {
        "status": "PASS" if not errors else "FAIL",
        "import": {"module": str(package_file), "package_root": str(package_root), "version": version},
        "resources": resource_rows,
        "schema": {"common_defs": sorted(common.get("$defs", {})),
                   "catalog_operation_count": len(operations or []),
                   "catalog_input_schemas_checked": schema_count},
        "mcp_registration": {"tool_count": len(tool_names), "tool_names_sha256": hashlib.sha256("\n".join(tool_names).encode()).hexdigest()},
        "errors": errors,
        "live_comsol": False,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
