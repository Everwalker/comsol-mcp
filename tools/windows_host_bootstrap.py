"""Read-only operator preview/inspect for the separate local bootstrap TCB.

There is no apply/restore CLI and no service/process/ACL mutation in this tool.
Approved apply/restore require the original in-process administrator owner.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from comsol_mcp import _windows_host_bootstrap as bootstrap


def preview(raw):
    plan = bootstrap.parse_plan(raw)
    return {"schema": bootstrap.SCHEMA, "state": "INTENT_ONLY_NO_NATIVE_AUTHORITY",
            "plan_sha256": plan.sha256, "prefix": plan.prefix, "service_name": plan.service_name,
            "files": len(plan.files), "bytes": sum(f.size for f in plan.files),
            "service_argv": list(plan.argv), "quoted_command_line": plan.command_line,
            "service_start": "BLOCKED_UNMATERIALIZED_TRUSTED_PROFILE",
            "apply_ready": False, "runtime": "PREMATERIALIZED_NORMAL_CPYTHON_NO_VENV"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--sha256", required=True, help="exact input file SHA-256 (intent pin only)")
    parser.add_argument("--inspect-native", action="store_true", help="Windows SDK read-only inspection")
    args = parser.parse_args(argv)
    with args.plan.open("rb") as stream:
        raw = stream.read(bootstrap.MAX_PLAN + 1)
    if bootstrap.digest(raw) != args.sha256:
        parser.error("input bytes differ from --sha256")
    result = bootstrap.inspect(raw) if args.inspect_native else preview(raw)
    if args.inspect_native:
        result["state"] = "READONLY_INSPECTED_ORIGINAL_HANDLES_CLOSED"
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
