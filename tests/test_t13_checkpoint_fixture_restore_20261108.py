"""T13 checkpoint env-fixture pointer lifecycle contract (20261108).

Fixture-lifecycle regression only: the env fixture in
test_unit_c_checkpoint_diff patches the dynamic _SessionServerProxy pointer
triple (_current_model/_current_model_origin/_current_model_path).  pytest
9.2's MonkeyPatch.setattr records old values from the instance __dict__ and
undoes missing entries with delattr, which cannot round-trip through the
proxy's routing __setattr__/__getattr__; the fixture now saves and restores
the exact triple itself.  These tests assert, without weakening anything:

- the pointer holder is one and the same session_server object across tests,
- each fixture call leaves the process pointer exactly at the pre-collection
  baseline before the next test's fixture installs fresh values,
- a normal invoke does not persistently change the installed pointer,
- the generator exception path (GeneratorExit at yield) restores the same
  triple, even after a mid-call pointer mutation.

Synthetic PureWorker/tmp_path fixtures only; no COMSOL, Worker process, SSH,
or network.
"""
import pytest
from _pytest.monkeypatch import MonkeyPatch
from comsol_mcp import _g2_checkpoint_ops as ops
from comsol_mcp._server import session_server
from tests.test_unit_c_checkpoint_diff import env, invoke

# Captured at collection, before any test in this session has run a fixture.
BASELINE_POINTER = ops.pointer()
_baseline_seen = []
_holders = []


@pytest.fixture(autouse=True)
def capture_pointer_before_env():
    # autouse fixtures instantiate before the directly-requested env fixture,
    # so this records exactly what the just-finished previous env fixture left
    # behind (and what the current env fixture is about to save).
    holder, values = ops.pointer()
    assert holder is session_server
    _holders.append(holder)
    _baseline_seen.append(values)


def installed(env, tmp_path):
    b, w, ref, row, tickets = env
    return (w.model_node, 'synthetic', str(tmp_path / 'g2_artifacts/checkpoints/old.mph'))


def test_env_installs_pointer_visible_through_same_proxy(env, tmp_path):
    holder, values = ops.pointer()
    assert holder is BASELINE_POINTER[0] is session_server
    assert _baseline_seen[-1] == BASELINE_POINTER[1]
    assert values == installed(env, tmp_path)
    out = invoke(env)
    assert out['success'], out
    # read-only dispatch observed the pointer but did not persistently change it
    assert ops.pointer()[1] == installed(env, tmp_path)


def test_exact_triple_restored_between_env_tests(env, tmp_path):
    # this test's env fixture just saved what the previous env fixture left:
    # exact restoration of the pre-collection triple between tests
    assert _baseline_seen[-1] == BASELINE_POINTER[1]
    assert all(h is session_server for h in _holders) and len(_holders) >= 2
    holder, values = ops.pointer()
    assert values == installed(env, tmp_path) != BASELINE_POINTER[1]
    out = invoke(env, 'exact-old', 'current')
    assert out['success'], out
    assert ops.pointer()[1] == installed(env, tmp_path)


def test_generator_exception_path_restores_same_triple(tmp_path):
    holder, baseline = ops.pointer()
    assert holder is session_server and baseline == BASELINE_POINTER[1]
    mp = MonkeyPatch()
    try:
        gen = env._get_wrapped_function()(tmp_path, mp)
        fixture_env = next(gen)
        assert ops.pointer()[1] == installed(fixture_env, tmp_path)
        out = invoke(fixture_env)
        assert out['success'], out
        # mid-call mutation like the 'pointer' UNKNOWN case, then the test is
        # interrupted: closing the generator raises GeneratorExit at the yield
        # and the fixture's finally must still restore the exact same triple
        setattr(ops.pointer()[0], '_current_model', object())
        gen.close()
    finally:
        mp.undo()
    restored = ops.pointer()
    assert restored[0] is holder
    assert restored[1] == baseline
