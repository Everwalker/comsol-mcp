"""Scoped, strictly read-only pytest collection adapter for the G3 W13 test.

Round v5 (2026-11-10, parent ruling; replaces the unexecuted v4 — bytes frozen at
_state/g3-w13-portable-test-parent-20261110/frozen-conftest-v4-invalid.txt, which
was never parseable — and the v3 design frozen at
_state/g3-w13-v3-parent-audit-20261110/frozen-tests-conftest-v3-defective.txt):

The v2/v3/v4 approach of injecting task identity into the *global process
environment* through collection/runtest window hooks is retired.  v2 leaked
adapter-owned G3L_* keys into unrelated module imports; v3 fixed the window but
its ownership cleanup deleted a value a later unrelated session fixture had
independently created under G3L_ROOT (saved parent RED: that fixture's own
assertion failed even though the adapter never intended to own the key); v4
could not even be parsed.  This adapter therefore **never sets, pops, restores
or otherwise modifies any process environment key**.  It only *reads* the
environment to detect a partial G3L identity configuration for the one target
file below, and in that case replaces pytest's default collector for that file
with a fail-closed collector that raises at collect() before any import.

Semantics:

* all-absent and complete modes: ordinary pytest collection — no duplicate
  default collector is added and no collector is suppressed.  The unchanged
  test resolves its own identity mode exactly once at import from an
  environment snapshot taken before it imports any product module, so values
  that unrelated fixtures set or clear later (even under these key names)
  cannot alter the selected mode and are never deleted here.
* partial mode (any strict subset of the eight gate keys — including a lone
  ``G3L_W15_SHA``): exactly one ``CollectError`` for the target file, raised
  before any import of the target, with no defaults and no completion, and
  with zero target bodies.  Only collectors of the exact built-in type
  ``_pytest.python.Module`` bound to this path are replaced (v3's preserved
  exact-type handling); any third-party collector registered for the same
  path is kept untouched, and no collectors are suppressed in the other modes.
* Disclosed limit (not global control): if a third-party collector
  independently imports the target while the configuration is partial, this
  adapter cannot intercept that import; the test module's own partial gate
  then fails before any product import or test body.  No claim is made that
  this design is universally compatible with every pytest plugin, and the
  test's synthetic fake-only assertions are not U02/T14/native acceptance.

No environment values are ever logged here — only key names, counts and fixed
marker text.  Nothing in this file is environment-owning, so no value created
by other code can be mistaken for an adapter residual.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from _pytest.nodes import Collector
from _pytest.python import Module as _BuiltinPythonModule

_TESTS_DIR = Path(__file__).resolve().parent
_TARGET = _TESTS_DIR / "test_g3_selection_measure_unknown.py"

# The eight keys the unchanged test's gate actually reads (names only).
_REQUIRED_KEYS = (
    "G3L_ROOT",
    "G3L_TEST",
    "G3L_TEST_SHA",
    "G3L_INIT_SHA",
    "G3L_W13_SHA",
    "G3L_COMMON_SHA",
    "G3L_ENGINE_SHA",
    "G3L_CONTRACT_SHA",
)
# Named in the test header but never read as a pin; its lone presence must
# count as a partial configuration (fail closed), never as all-absent.
_PARTIAL_MARKER_KEYS = ("G3L_W15_SHA",)


def _partial_identity():
    """Read-only detection of a partial configuration; never mutates env.

    Returns ``(present-key-names, missing-key-names)`` iff the configuration
    is partial; ``None`` for the complete and all-absent modes.
    """
    present_required = [key for key in _REQUIRED_KEYS if key in os.environ]
    present_marker = [key for key in _PARTIAL_MARKER_KEYS if key in os.environ]
    if len(present_required) == len(_REQUIRED_KEYS):
        return None
    if present_required or present_marker:
        present = sorted(set(present_required) | set(present_marker))
        missing = [key for key in _REQUIRED_KEYS if key not in os.environ]
        return present, missing
    return None


def _is_target_path(path) -> bool:
    try:
        return Path(path).resolve() == _TARGET
    except (OSError, TypeError):
        return False


class _G3LFailClosedCollector(pytest.Module):
    """Replaces pytest's default collector for the one target file in partial mode.

    Raises at collect() *before* importing the module, so the unchanged
    identity gate is never reached and exactly one collection error is
    reported (no second built-in collector, no target body).
    """

    def collect(self):
        detection = _partial_identity()
        present, missing = detection if detection else ([], [])
        raise Collector.CollectError(
            "g3l-discovery-adapter: partial G3L identity configuration — "
            f"present-key-names={present} missing-key-names={missing}; "
            "failing closed before import (no completion, no defaults)"
        )


@pytest.hookimpl(wrapper=True)
def pytest_collect_file(file_path: Path, parent):
    target = _is_target_path(file_path)
    aggregated = yield
    collectors = list(aggregated)
    if not target:
        return collectors
    defaults = [
        c
        for c in collectors
        if type(c) is _BuiltinPythonModule and _is_target_path(getattr(c, "path", ""))
    ]
    detection = _partial_identity()
    print(
        "g3l-discovery-adapter: target-collectors "
        f"total={len(collectors)} builtin-module={len(defaults)} "
        f"partial={'yes' if detection else 'no'} "
        "(read-only adapter; process environment never modified)",
        file=sys.stderr,
        flush=True,
    )
    if detection is None:
        # All-absent or complete: ordinary collection semantics retained.
        return collectors
    # Replace only pytest's default Module collector for this file — never
    # add a collector alongside it and never remove third-party collectors.
    kept = [c for c in collectors if c not in defaults]
    kept.append(_G3LFailClosedCollector.from_parent(parent, path=_TARGET))
    return kept
