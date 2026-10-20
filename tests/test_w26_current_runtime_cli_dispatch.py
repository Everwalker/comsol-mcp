from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run_child(code: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["TMPDIR"] = "/private/tmp"
    return subprocess.run(
        [sys.executable, "-c", code, *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def test_fresh_current_cli_entry_dispatches_app_bundle_with_same_owner_no_native():
    code = r'''
import importlib, os, runpy, sys, tempfile, tools
from pathlib import Path
from types import SimpleNamespace

legacy = importlib.import_module("tools.w26_runtime_actor")
assert "tools.w26_runtime_actor_current" not in sys.modules
sys.argv = ["tools.w26_runtime_actor_current", "--help"]
try:
    runpy.run_module("tools.w26_runtime_actor_current", run_name="__main__", alter_sys=True)
except SystemExit as result:
    assert result.code == 0
else:
    raise AssertionError("current module CLI help did not exit")

actor = sys.modules.get("tools.w26_runtime_actor_current")
assert actor is not None and tools.w26_runtime_actor_current is actor
assert sys.modules["tools.w26_runtime_actor"] is legacy
consumer = importlib.import_module("tools.w26_application_bundle_current")
assert consumer.actor is actor

candidate = actor.activity_scope({"activity_kind": actor.APP_BUNDLE})
candidate["plan"] = {"path": "/private/tmp/unread-current-app-plan.json", "sha256": "0" * 64}
actor.gate = lambda *args: (candidate, {}, {"path": "synthetic-binding", "sha256": "0" * 64})
class ReachedConsumer(Exception):
    pass
reads = []
def stop_at_first_consumer_read(*args, **kwargs):
    reads.append((args, kwargs))
    raise ReachedConsumer()
consumer._helpers = lambda: SimpleNamespace(raw_file=stop_at_first_consumer_read)
native_births = []
actor.subprocess.Popen = lambda *args, **kwargs: native_births.append((args, kwargs))
prior_umask = os.umask(0o077)
try:
    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        root = Path(directory)
        try:
            actor.execute(root, root / "binding.json", "0" * 64)
        except ReachedConsumer:
            pass
        else:
            raise AssertionError("current APP_BUNDLE dispatch did not enter its real consumer")
        assert list(root.iterdir()) == []
finally:
    os.umask(prior_umask)
assert len(reads) == 1 and native_births == []
print("PASS_CURRENT_CLI_TO_REAL_APP_BUNDLE_CONSUMER_SAME_OWNER_NO_NATIVE")
'''
    result = _run_child(code)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS_CURRENT_CLI_TO_REAL_APP_BUNDLE_CONSUMER_SAME_OWNER_NO_NATIVE" in result.stdout


def test_current_cli_refuses_preloaded_wrong_canonical_module_before_dispatch():
    code = r'''
import importlib.util, runpy, sys, tools, types
conflict = sys.argv[1]
spec = importlib.util.find_spec("tools.w26_runtime_actor_current")
wrong = types.ModuleType("tools.w26_runtime_actor_current")
wrong.__spec__ = spec
if conflict == "module":
    sys.modules["tools.w26_runtime_actor_current"] = wrong
else:
    tools.w26_runtime_actor_current = wrong
sys.argv = ["tools.w26_runtime_actor_current", "--help"]
try:
    runpy.run_module("tools.w26_runtime_actor_current", run_name="__main__", alter_sys=True)
except RuntimeError as failure:
    assert "canonical current actor module identity differs" in str(failure)
    print("PASS_PRELOADED_CURRENT_MODULE_REFUSED_BEFORE_DISPATCH")
else:
    raise AssertionError("preloaded current actor module was accepted")
if conflict == "module":
    assert sys.modules["tools.w26_runtime_actor_current"] is wrong
else:
    assert tools.w26_runtime_actor_current is wrong
'''
    for conflict in ("module", "package"):
        result = _run_child(code, conflict)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "PASS_PRELOADED_CURRENT_MODULE_REFUSED_BEFORE_DISPATCH" in result.stdout
