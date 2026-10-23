"""Canonical regression for foreign-thread finite-reader finish ownership.

Main's original counter function and every assertion are preserved. Only its
external packet import/source-origin harness is adapted to this repository.
"""
from pathlib import Path
import threading

from comsol_mcp import _windows_t037_targets as targets
from comsol_mcp._execution_contract import ExecutionContractError
from tests.test_windows_t037_targets import full_chain, software_only

assert Path(targets.__file__).resolve() == (
    Path(__file__).resolve().parents[1] / 'comsol_mcp' / '_windows_t037_targets.py'
).resolve()

def test_foreign_reader_finish_refuses_before_any_original_release(monkeypatch, tmp_path):
    with full_chain(monkeypatch, tmp_path) as (daemon, factory, sdk, snapshot, events):
        with __import__("comsol_mcp._windows_t037_admission", fromlist=["_factory_birth_scope"])._factory_birth_scope(factory):
            with targets.finite_target_scope(factory.profile, factory, daemon):
                reader, = [r for r in targets._READS.values() if r.owner is factory]
                before = list(sdk.calls)
                errors = []
                def foreign():
                    try: reader.finish()
                    except BaseException as error: errors.append(error)
                thread = threading.Thread(target=foreign, name='Main-foreign-reader-finish')
                thread.start(); thread.join(2)
                assert not thread.is_alive()
                assert len(errors) == 1 and isinstance(errors[0], ExecutionContractError)
                assert errors[0].details['reasons'] == ['FINITE_READER_LIFETIME_MISMATCH']
                assert sdk.calls == before, 'foreign finish must refuse before any original SDK release'
                assert not reader.retired and targets._READS.get(id(reader)) is reader
                assert getattr(reader, '_finish_error', None) is None, 'foreign caller must not poison original closer'
            assert reader.retired
        factory.close()
