from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import signal
import sqlite3
import subprocess
import time


RUN = pathlib.Path(__file__).resolve().parent
REPO = RUN.parents[3]
TARGETS = (6072, 6098, 6105)
EXPECTED_ROOT = REPO.resolve()


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def run_cmd(argv: list[str]) -> tuple[int, str, str]:
    p = subprocess.run(argv, cwd=REPO, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def first_path(output: str) -> str | None:
    for line in output.splitlines():
        if line.startswith("n"):
            return line[1:]
    return None


def parse_ps(pid: int) -> dict:
    rc, out, err = run_cmd(
        ["ps", "-p", str(pid), "-o", "pid=,ppid=,user=,uid=,state=,etime=,lstart=,command="]
    )
    line = next((x.strip() for x in out.splitlines() if x.strip()), "")
    if not line:
        return {"exists": False, "returncode": rc, "stderr": err.strip()}
    fields = line.split(None, 6)
    command_start = line.find("/Users/")
    command = line[command_start:] if command_start >= 0 else ""
    birth = line[:command_start].strip() if command_start >= 0 else None
    return {
        "exists": True,
        "returncode": rc,
        "pid": fields[0] if len(fields) > 0 else None,
        "ppid": fields[1] if len(fields) > 1 else None,
        "user": fields[2] if len(fields) > 2 else None,
        "uid": fields[3] if len(fields) > 3 else None,
        "state": fields[4] if len(fields) > 4 else None,
        "etime": fields[5] if len(fields) > 5 else None,
        "birth": birth,
        "command": command,
        "command_sha256": sha256_text(command),
        "stderr": err.strip(),
    }


def children(pid: int) -> list[int]:
    rc, out, _ = run_cmd(["pgrep", "-P", str(pid)])
    if rc not in (0, 1):
        return [-1]
    return [int(x) for x in out.split() if x.isdigit()]


def cwd_for(pid: int) -> str | None:
    _, out, _ = run_cmd(["lsof", "-nP", "-a", "-p", str(pid), "-d", "cwd", "-Fn"])
    return first_path(out)


def listeners_for(pid: int) -> list[str]:
    _, out, _ = run_cmd(
        ["lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN", "-F", "pnPft"]
    )
    return [line[1:] for line in out.splitlines() if line.startswith("n")]


def home_from_command(command: str) -> str | None:
    parts = command.split()
    try:
        return parts[parts.index("--home") + 1]
    except (ValueError, IndexError):
        return None


def database_summary(home: str | None) -> dict:
    result = {"home": home, "exists": bool(home and pathlib.Path(home).exists()), "databases": []}
    if not home or not pathlib.Path(home).is_dir():
        return result
    for db_path in sorted(pathlib.Path(home).glob("*.sqlite3")):
        item = {"path": str(db_path.relative_to(REPO)) if db_path.is_relative_to(REPO) else "<temporary>", "tables": {}}
        try:
            uri = f"file:{db_path}?mode=ro"
            with sqlite3.connect(uri, uri=True) as db:
                tables = [row[0] for row in db.execute("select name from sqlite_master where type='table'")]
                for table in tables:
                    count = db.execute(f"select count(*) from {table}").fetchone()[0]
                    item["tables"][table] = {"rows": count}
                    if table == "jobs":
                        statuses = db.execute("select status, count(*) from jobs group by status order by status").fetchall()
                        item["tables"][table]["statuses"] = {str(k): int(v) for k, v in statuses}
        except Exception as exc:  # evidence records a read failure without exposing DB contents
            item["read_error"] = type(exc).__name__
        result["databases"].append(item)
    return result


def snapshot(label: str) -> dict:
    records = {}
    for pid in TARGETS:
        ps = parse_ps(pid)
        home = home_from_command(ps.get("command", "")) if ps.get("exists") else None
        records[str(pid)] = {
            "ps": ps,
            "children": children(pid) if ps.get("exists") else [],
            "cwd": cwd_for(pid) if ps.get("exists") else None,
            "listeners": listeners_for(pid) if ps.get("exists") else [],
            "job_database": database_summary(home),
        }
    result = {"schema": "comsol-mcp/process-cleanup-snapshot/1", "label": label, "captured_at_utc": now(), "targets": records}
    (RUN / f"{label}.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def quiescent_owned(record: dict) -> tuple[bool, list[str]]:
    ps = record["ps"]
    reasons: list[str] = []
    if not ps.get("exists"):
        reasons.append("missing")
        return False, reasons
    if ps.get("user") != "everwalker":
        reasons.append("owner_mismatch")
    command = ps.get("command", "")
    if "comsol_mcp._control_daemon" not in command or str(REPO / ".venv/bin/python") not in command:
        reasons.append("command_mismatch")
    if record.get("cwd") != str(REPO):
        reasons.append("cwd_mismatch")
    if record.get("children"):
        reasons.append("children_present")
    if not record.get("listeners"):
        reasons.append("listener_missing")
    home = record["job_database"].get("home") or ""
    if not (home.startswith(str(REPO / ".g3-private")) or "/pytest-of-everwalker/" in home):
        reasons.append("home_scope_mismatch")
    if home.startswith("/private/") and record["job_database"].get("exists"):
        reasons.append("unexpected_temp_home_exists")
    for db in record["job_database"].get("databases", []):
        jobs = db.get("tables", {}).get("jobs", {})
        statuses = jobs.get("statuses", {})
        if any(k.upper() in {"QUEUED", "RUNNING", "ACTIVE", "STARTED"} and v for k, v in statuses.items()):
            reasons.append("active_job_status")
        sessions = db.get("tables", {}).get("sessions", {}).get("rows")
        runtimes = db.get("tables", {}).get("runtimes", {}).get("rows")
        if sessions not in (None, 0):
            reasons.append("sessions_present")
        if runtimes not in (None, 0):
            reasons.append("runtimes_present")
    return not reasons, reasons


pre = snapshot("pre_cleanup")
checks = {}
for pid in TARGETS:
    checks[str(pid)] = quiescent_owned(pre["targets"][str(pid)])
decision = {"schema": "comsol-mcp/process-cleanup-decision/1", "generated_at_utc": now(), "checks": {k: {"quiescent_owned": v[0], "reasons": v[1]} for k, v in checks.items()}}
if all(v[0] for v in checks.values()):
    decision["action"] = "SIGTERM_targets_after_fresh_identity_recheck"
    decision["signals"] = {}
    for pid in TARGETS:
        try:
            os.kill(pid, signal.SIGTERM)
            decision["signals"][str(pid)] = "SIGTERM_sent"
        except ProcessLookupError:
            decision["signals"][str(pid)] = "already_exited"
        except OSError as exc:
            decision["signals"][str(pid)] = f"error:{type(exc).__name__}"
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if all(not parse_ps(pid).get("exists") for pid in TARGETS):
            break
        time.sleep(0.25)
    decision["post_termination_exists"] = {str(pid): parse_ps(pid).get("exists", False) for pid in TARGETS}
else:
    decision["action"] = "NO_SIGNAL"
    decision["signals"] = {}
post = snapshot("post_cleanup")
decision["post_snapshot"] = "post_cleanup.json"
(RUN / "decision.json").write_text(json.dumps(decision, indent=2, sort_keys=True) + "\n")

hashes = []
for path in sorted(RUN.rglob("*")):
    if path.is_file() and path.name != "SHA256SUMS.json":
        h = hashlib.sha256(path.read_bytes()).hexdigest()
        hashes.append({"path": str(path.relative_to(RUN)), "sha256": h, "size": path.stat().st_size})
(RUN / "SHA256SUMS.json").write_text(json.dumps({"schema": "comsol-mcp/evidence-sha256/v1", "generated_at_utc": now(), "self_excluded": True, "hashes": hashes}, indent=2, sort_keys=True) + "\n")
if decision["action"] == "NO_SIGNAL":
    raise SystemExit(2)
if any(decision.get("post_termination_exists", {}).values()):
    raise SystemExit(3)
