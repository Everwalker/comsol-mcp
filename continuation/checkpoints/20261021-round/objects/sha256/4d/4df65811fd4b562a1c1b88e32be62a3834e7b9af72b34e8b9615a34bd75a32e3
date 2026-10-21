"""Assemble the live acceptance matrix for the G3.3 clean-room round.

Reads every frozen_* run under evidence/phase4_3/runs/, extracts the group verdict,
the run status envelope and the source identity, and writes a single matrix file.
Read-only with respect to the runs themselves.
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUNS = ROOT / "evidence" / "phase4_3" / "runs"
OUT = ROOT / "evidence" / "phase4_3" / "LIVE_MATRIX_20260922.json"

GROUPS = {
    "frozen_19case": ("main_runner_C00_C17", None),
    "frozen_m1": ("M1_single_constant_nonunit_volume", None),
    "frozen_numeric": ("numeric_C04_C10", None),
    "frozen_nodes": ("nodes_C11_C14", None),
    "frozen_probe": ("probe_C14_C16", None),
    "frozen_export": ("export_C12_C13", None),
    "frozen_control": ("control_C15", None),
    "frozen_study-fault": ("study_fault_unknown_no_replay", None),
    "frozen_cutplane": ("cutplane_C11", None),
    "frozen_chains": ("gate_a_chains_and_protocol", None),
    "frozen_probe-transient": ("probe_transient_history_axis", None),
}


def load(path: pathlib.Path):
    try:
        return json.loads(path.read_text())
    except Exception as exc:  # evidence must be read verbatim; record what failed
        return {"__unreadable__": str(exc)}


def case_ledger(run: pathlib.Path):
    """Return (status, summary, requested) from whatever ledger the group wrote."""
    for path in sorted(run.glob("result.json")):
        data = load(path)
        if isinstance(data, dict) and data.get("status"):
            return (
                data.get("status"),
                {
                    "passed_cases": data.get("passed_cases"),
                    "failed_cases": data.get("failed_cases"),
                    "aborted_cases": data.get("aborted_cases"),
                    "total_cases": data.get("total_cases"),
                },
                path.name,
            )
    for pattern in ("**/*cases*.final.json", "**/*cases*.json", "run_status.json"):
        for path in sorted(run.glob(pattern)):
            data = load(path)
            if not isinstance(data, dict):
                continue
            if "run_status.json" in path.name:
                return (
                    data.get("status"),
                    {"cases_requested": data.get("cases_requested"), "exit_code": data.get("exit_code")},
                    path.relative_to(run).as_posix(),
                )
            got = False
            for key in ("overall", "status"):
                if data.get(key):
                    summary = data.get("summary") or {
                        k: v
                        for k, v in data.items()
                        if isinstance(v, (int, str)) and k not in ("scope",)
                    }
                    return data.get(key), summary, path.relative_to(run).as_posix()
            if "summary" in data:
                return data.get("overall") or "UNKNOWN", data["summary"], path.relative_to(run).as_posix()
            if got:
                break
    return None, None, None


def source_identity(run: pathlib.Path):
    for name in ("source_manifest.json", "final_source_files.json", "isolation_probe.json"):
        path = run / name
        if not path.exists():
            continue
        data = load(path)
        if not isinstance(data, dict):
            continue
        ident = {}
        for key in ("commit", "tree", "dirty", "source_commit", "source_tree", "config_sha256", "status"):
            if key in data:
                ident[key] = data[key]
        if ident:
            ident["file"] = name
            return ident
    return None


matrix = []
for prefix, (label, _) in GROUPS.items():
    for run in sorted(RUNS.glob(prefix + "_*")):
        status, summary, ledger = case_ledger(run)
        matrix.append(
            {
                "group": label,
                "run_dir": run.relative_to(ROOT).as_posix(),
                "verdict": status,
                "summary": summary,
                "ledger": ledger,
                "source_identity": source_identity(run),
            }
        )

# exploratory native API observations: the tool documents them as "not a passing
# numerical gate", so they are recorded separately instead of entering the matrix.
exploratory = []
for run in sorted(RUNS.glob("frozen_*-probe_*")):
    evidence = sorted(p.name for p in run.glob("*.json"))
    exploratory.append(
        {
            "run_dir": run.relative_to(ROOT).as_posix(),
            "kind": "EXPLORATORY_NATIVE_OBSERVATION",
            "evidence_files": evidence,
        }
    )

# Closing verdict per group: the latest run that carries a verdict, so a reader sees both
# the preserved failure sequence and which run closed the group.
group_final = {}
for entry in matrix:
    if entry["verdict"]:
        group_final[entry["group"]] = {
            "verdict": entry["verdict"],
            "run_dir": entry["run_dir"],
            "summary": entry["summary"],
        }

payload = {
    "schema": "comsol-mcp-g3/live-acceptance-matrix/1",
    "round": "2026-09-22 clean-room re-run on the recovered repository",
    "matrix": matrix,
    "group_final": group_final,
    "exploratory_observations": exploratory,
    "note": (
        "Each entry is a real live run on the frozen tree; verdict and summary are read from the run's own "
        "ledger, never restated by hand. Exploratory probes are separated because the protocol tool defines "
        "them as observations rather than gates."
    ),
}

OUT.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
print("wrote", OUT.relative_to(ROOT), "entries:", len(matrix), "exploratory:", len(exploratory))
for entry in matrix:
    print(f"  {entry['group']:<42} {str(entry['verdict']):<24} {entry['run_dir'].split('/')[-1]}")
