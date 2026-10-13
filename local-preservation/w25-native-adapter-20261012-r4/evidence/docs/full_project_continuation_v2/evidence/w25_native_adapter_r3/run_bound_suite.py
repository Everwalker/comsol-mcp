from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
RUNTIME = Path("/private/tmp/comsol-v2-runtime-20261011-new/comsol-mcp-recovery-runtime-20261004-001/python312/bin/python")
GUARD = Path("/Volumes/SSD/Comsol-MCP/execution-scratch/temp-loss-runtime-recovery-20261004-001/builder-preparation022/network-guard")
TESTS = [
    "tests/test_desktop_capture.py",
    "tests/test_desktop_control_wiring.py",
    "tests/test_desktop_macos.py",
    "tests/test_desktop_macos_phase2a.py",
    "tests/test_desktop_managed_load_phase2b.py",
    "tests/test_desktop_native_adapters_w25.py",
    "tests/test_desktop_service.py",
    "tests/test_desktop_sticky_effects_phase2c.py",
    "tests/test_desktop_win32_identity.py",
    "tests/test_host_scoped_provider.py",
]
SOURCES = sorted({
    *[str(path.relative_to(ROOT)) for path in (ROOT / "comsol_mcp").glob("_desktop_*.py")],
    "comsol_mcp/_host_scoped_provider.py",
    "comsol_mcp/_windows_parameter_publish.py",
    "comsol_mcp/_control_daemon.py",
    "comsol_mcp/_managed_backend.py",
    "comsol_mcp/_artifact_store.py",
    "comsol_mcp/_project_authority.py",
    "comsol_mcp/_operation_store.py",
    *TESTS,
})


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_map() -> dict[str, dict[str, object]]:
    result = {}
    for relative in SOURCES:
        path = ROOT / relative
        result[relative] = {"sha256": sha256(path), "size_bytes": path.stat().st_size}
    return result


def main() -> int:
    if not RUNTIME.is_file() or not GUARD.is_dir():
        raise SystemExit("required recovered Python runtime or strict network guard is unavailable")
    log = OUT / "network-guard-bound.jsonl"
    junit = OUT / "junit-bound.xml"
    log.write_text("", encoding="utf-8")
    before = source_map()
    env = dict(os.environ)
    env.update({
        "PYTHONPATH": os.pathsep.join([str(GUARD), str(ROOT), str(ROOT / "tests")]),
        "CODEX_NETWORK_GUARD_LABEL": "w25-native-adapter-r3-bound",
        "CODEX_NETWORK_GUARD_RUN_ID": "w25-native-adapter-r3-bound-20261012",
        "CODEX_NETWORK_GUARD_LOG": str(log),
    })
    command = [str(RUNTIME), "-m", "pytest", "-q", *TESTS,
               f"--junitxml={junit.relative_to(ROOT)}"]
    result = subprocess.run(command, cwd=ROOT, env=env, text=True,
                            capture_output=True, check=False)
    stdout_path = OUT / "pytest-stdout.txt"
    stderr_path = OUT / "pytest-stderr.txt"
    stdout_path.write_text(result.stdout, encoding="utf-8")
    stderr_path.write_text(result.stderr, encoding="utf-8")
    after = source_map()
    guard_lines = log.read_text(encoding="utf-8").splitlines()
    guard_events = [json.loads(line) for line in guard_lines if line.strip()]
    unchanged = before == after
    receipt = {
        "schema": "w25-native-adapter-bound-suite/1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "classification": "SOFTWARE_GUARDED_ONLY",
        "native_gui_or_comsol": "NOT_RUN",
        "runtime": {"path": str(RUNTIME), "version": subprocess.check_output(
            [str(RUNTIME), "--version"], text=True).strip()},
        "command": command,
        "tests": TESTS,
        "source_hashes_before": before,
        "source_hashes_after": after,
        "source_hashes_unchanged": unchanged,
        "pytest_exit_code": result.returncode,
        "junit": {"path": str(junit.relative_to(ROOT)),
                  "sha256": sha256(junit) if junit.is_file() else None},
        "stdout": {"path": str(stdout_path.relative_to(ROOT)), "sha256": sha256(stdout_path)},
        "stderr": {"path": str(stderr_path.relative_to(ROOT)), "sha256": sha256(stderr_path)},
        "network_guard": {"path": str(log.relative_to(ROOT)), "sha256": sha256(log),
                          "active_before_child_entrypoint": sum(
                              event.get("event") == "GUARD_ACTIVE_BEFORE_CHILD_ENTRYPOINT"
                              for event in guard_events),
                          "blocked_attempts": sum(
                              event.get("event") == "BLOCKED_NETWORK_ATTEMPT"
                              for event in guard_events),
                          "events": len(guard_events)},
    }
    receipt_path = OUT / "BOUND_SUITE_RECEIPT.json"
    receipt_path.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    sys.stdout.write(f"\nsource_hashes_unchanged={unchanged} guard_events={len(guard_events)} blocked={receipt['network_guard']['blocked_attempts']}\n")
    return result.returncode if unchanged and guard_events and receipt["network_guard"]["blocked_attempts"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
