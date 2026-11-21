"""T13 Java-walk getter-failure expected-behaviour RED (2026-11-09).

Formalizes the confirmed T13 read-only audit
(``docs/universal_mcp_execution/evidence/full/T13-java-walk-readonly-audit-20261109.md``)
into a repeatable *expected-behaviour* test against the real Worker source.
It is RED by design on the current Worker build: the fix owner must make
``PersistentComsolWorker.walk(Map)`` retain completed getter exceptions in
the reply ``errors`` list so the Python consumer can never see a
complete/error-free empty search for a node whose metadata probe faulted.

Layers (boundaries stated honestly):

* ``test_java_walk_getter_failures_are_surfaced``  - compiles the real
  ``PersistentComsolWorker.java`` plus ``tests/java/PersistentComsolWorkerWalkFailureHarness.java``
  into pytest ``tmp_path`` and runs it on the local JVM against the
  installed COMSOL API *interfaces* only (JDK Proxy receivers).  No Worker
  constructor, no worker service, no socket, no COMSOL process, no engine:
  this is a software harness, NOT native COMSOL evidence.  All 8 scenarios
  run regardless of assertion failures; each prints one RESULT line and
  the harness keeps its own detailed per-scenario assertions, so the first
  failure never masks the remaining getter triggers.
* ``test_g2_engine_find_nodes_*``  - pinned against a FIXED synthetic batch
  reply through ``find_nodes`` -> ``_walk_from_reply``: shows the consumer
  gate ``complete = data["complete"] and not data["errors"]`` makes a
  non-empty errors list sufficient to report INCOMPLETE.  Synthetic reply
  only - it does NOT claim the real gateway/native path was exercised.

Toolchain honesty: missing ``javac``/``java`` or COMSOL API jars produce a
pytest skip with a ``NOT_RUN`` reason - never a silent pass.  A non-zero
javac or java exit is surfaced as a hard failure with the raw output;
only *behaviour* assertion failures are acceptable as the designed RED
(a compile error would be a different, loudly-labelled failure).
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from comsol_mcp._g2_engine import find_nodes

REPO = Path(__file__).resolve().parents[1]
WORKER_SOURCE = REPO / "comsol_mcp" / "worker_java" / "PersistentComsolWorker.java"
HARNESS = REPO / "tests" / "java" / "PersistentComsolWorkerWalkFailureHarness.java"
HARNESS_CLASS = "comsol_mcp.worker_java.PersistentComsolWorkerWalkFailureHarness"
COMSOL_ROOT = Path("/Applications/COMSOL64/Multiphysics")

RESULT_RE = re.compile(
    r"^RESULT id=(?P<id>\w+) visits=(?P<visits>-?\d+) matches=(?P<matches>-?\d+) "
    r"matched=(?P<matched>-?\d+) complete=(?P<complete>\S+) truncated=(?P<truncated>\S+) "
    r"errors=(?P<errors>-?\d+) getType_calls=(?P<getType_calls>\d+) label_calls=(?P<label_calls>\d+) "
    r"tag_calls=(?P<tag_calls>\d+) walk_threw=(?P<walk_threw>\S+) first_type_id=(?P<first_type_id>\S+)$",
    re.MULTILINE,
)
EXPECTED_SCENARIOS = ("P1", "P2", "P3", "C1", "E1", "E2", "E3", "E4")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _java_toolchain() -> tuple[str, str, str]:
    javac = shutil.which("javac")
    java = shutil.which("java")
    if javac is None or java is None:
        pytest.skip("NOT_RUN: Java compiler/runtime unavailable for the Worker walk getter-failure harness")
    api_dir = COMSOL_ROOT / "apiplugins"
    jars = sorted(api_dir.glob("*.jar")) if api_dir.is_dir() else []
    if not jars:
        pytest.skip("NOT_RUN: COMSOL Java API classpath unavailable for the Worker walk getter-failure harness")
    assert javac is not None and java is not None
    return javac, java, os.pathsep.join(str(path) for path in jars)


def _parse_results(stdout: str) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    for match in RESULT_RE.finditer(stdout):
        rows[match.group("id")] = match.groupdict()
    return rows


# ---------------------------------------------------------------------------
# RED-by-design: real JVM, real private walk(Map), expected behaviour.
# ---------------------------------------------------------------------------

def test_java_walk_getter_failures_are_surfaced(tmp_path: Path):
    javac, java, classpath = _java_toolchain()
    worker_sha_before = _sha256(WORKER_SOURCE)

    classes = tmp_path / "walk-failure-classes"
    classes.mkdir()
    compile_result = subprocess.run(
        [javac, "-encoding", "UTF-8", "-classpath", classpath, "-d", str(classes),
         str(WORKER_SOURCE), str(HARNESS)],
        cwd=REPO, capture_output=True, text=True, timeout=240,
    )
    # A compile failure is NOT the designed behaviour RED; label it loudly.
    assert compile_result.returncode == 0, (
        "COMPILE failure (harness/worker source or classpath problem, not the "
        "behaviour under test): " + compile_result.stdout + compile_result.stderr
    )

    execution = subprocess.run(
        [java, "-cp", str(classes) + os.pathsep + classpath, HARNESS_CLASS],
        cwd=REPO, capture_output=True, text=True, timeout=180,
    )
    # Verify the actual source identity even when the behavioural RED below
    # fails; checking this after the assertions only binds GREEN executions.
    worker_sha_after = _sha256(WORKER_SOURCE)
    assert worker_sha_after == worker_sha_before, (
        f"Worker source changed during the harness run: {worker_sha_before} -> {worker_sha_after}")

    results = _parse_results(execution.stdout)
    raw = (
        f"\n--- harness exit={execution.returncode} ---\n{execution.stdout}\n"
        f"--- stderr ---\n{execution.stderr}\n"
    )

    missing = [sid for sid in EXPECTED_SCENARIOS if sid not in results]
    assert not missing, f"harness did not execute every scenario; missing RESULT lines: {missing}{raw}"

    failures: list[str] = []

    def expect(row: dict[str, str], condition: bool, message: str) -> None:
        if not condition:
            failures.append(f"{row['id']}: {message} (RESULT {row})")

    for sid in ("P1", "P2", "P3"):
        row = results[sid]
        expect(row, row["matches"] == "1", "positive control must still match exactly 1 node")
        expect(row, row["errors"] == "0", "clean successful match must keep errors=[]")
        expect(row, row["complete"] == "true", "clean successful match must stay complete=true")
        expect(row, row["truncated"] == "null", "clean successful match must not truncate")
        expect(row, row["walk_threw"] == "-", "walk must not throw on a healthy node")
    expect(results["P1"], results["P1"]["getType_calls"] == "1"
           and results["P1"]["label_calls"] == "1" and results["P1"]["tag_calls"] == "1",
           "handler counter: all three metadata getters must be exercised on the healthy node")
    expect(results["P1"], results["P1"]["first_type_id"] == "HeatTransfer",
           "matched row must carry the real type_id")

    clean_absent = results["C1"]
    expect(clean_absent, clean_absent["matches"] == "0" and clean_absent["complete"] == "true"
           and clean_absent["errors"] == "0",
           "genuine non-match control must show the CLEAN complete-empty signature "
           "(matches=[] complete=true errors=[])")

    getter_by_scenario = {"E1": "getType_calls", "E2": "label_calls", "E3": "tag_calls",
                          "E4": "getType_calls"}
    for sid, counter in getter_by_scenario.items():
        row = results[sid]
        expect(row, row["walk_threw"] == "-",
               "a completed getter exception must not escape as a walk-level exception")
        expect(row, int(row[counter]) >= 1,
               f"injected getter must actually be invoked (counter {counter}={row[counter]})")
        # The consumer gate in _g2_engine.find_nodes is
        # ``complete = data['complete'] and not data['errors']``: a
        # non-empty worker errors list is what stops a dropped
        # would-be match from masquerading as a complete empty
        # search.  Message text and error codes are intentionally
        # NOT pinned here, and a field-level fault is not to be
        # dressed up as a transport EXECUTION_STATE_UNKNOWN.
        expect(row, int(row["errors"]) >= 1,
               "EXPECTED the completed getter failure to be retained in reply errors "
               f"(found errors={row['errors']} complete={row['complete']} matches={row['matches']})")
        masquerading = (row["matches"] == "0" and row["complete"] == "true" and row["errors"] == "0")
        expect(row, not masquerading,
               "reply must not be indistinguishable from a complete error-free empty search")

    assert not failures, "walk getter-failure expected-behaviour contract violated:\n- " \
        + "\n- ".join(failures) + raw

    # The harness exit code is produced by its own behavioural checks, not
    # by keyword matching; both layers must agree.
    assert execution.returncode == 0, f"harness reported behavioural failures (exit {execution.returncode}){raw}"

# ---------------------------------------------------------------------------
# GREEN contract pins for the Python consumer (synthetic FIXED batch replies;
# the real gateway/native path is NOT claimed to be exercised).
# ---------------------------------------------------------------------------

class _FixedReplyWorker:
    """Worker stand-in whose walk_nodes returns a fixed production-shape reply.

    Only used to pin how ``find_nodes`` consumes the reply; no socket, no
    JavaWorker, no engine.
    """

    def __init__(self, reply: dict[str, Any]):
        self._reply = reply
        self.calls: list[dict[str, Any]] = []

    def client(self):
        outer = self

        class _Client:
            def model(self, _tag):
                return "model-endpoint"

        return _Client()

    def walk_nodes(self, node, *, query, candidates, max_nodes, max_seconds, skip_visited, limit):
        self.calls.append({"query": dict(query)})
        return dict(self._reply)


def test_find_nodes_surfaces_batch_getter_errors_as_incomplete():
    # Shape a correctly-fixed Worker would emit once a getter failure is
    # retained: errors non-empty, and either complete=false or truncated -
    # the consumer only requires the non-empty errors list.
    reply = {"matches": [], "visited": 1, "matched_in_call": 0, "complete": True,
             "truncated_reason": None, "generation": 7,
             "errors": [{"collection": "", "code": "GETTER_FAILED",
                         "message": "RuntimeException: synthetic completed getType failure"}]}
    worker = _FixedReplyWorker(reply)
    result = find_nodes(worker, "m", {"type": "HeatTransfer"}, limit=50)
    assert worker.calls, "find_nodes must have used the batch walk_nodes path"
    assert result["errors"], "getter failure row must survive into the wire result"
    assert result["complete"] is False
    assert result["incomplete"] is True
    assert result["status"] == "INCOMPLETE"
    assert result["count"] == 0


def test_find_nodes_clean_empty_reply_stays_succeeded_not_incomplete():
    # Counter-control: with errors=[] complete=true (the current buggy walk
    # shape for a faulted getter, and the honest shape for a genuinely
    # absent node) the consumer has no way to distinguish them.  This pins
    # that the fix MUST live in the Worker errors list, not in _g2_engine.
    reply = {"matches": [], "visited": 1, "matched_in_call": 0, "complete": True,
             "truncated_reason": None, "generation": 7, "errors": []}
    result = find_nodes(_FixedReplyWorker(reply), "m", {"type": "HeatTransfer"}, limit=50)
    assert result["complete"] is True and result["status"] == "SUCCEEDED" and result["errors"] == []
