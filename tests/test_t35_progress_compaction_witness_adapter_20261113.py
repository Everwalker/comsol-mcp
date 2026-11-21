"""Collection adapter for the unmodified Qwen compaction-witness tests.

The authored fresh-read function takes ``terminal_status`` but lacks its
parametrize decorator. This adapter supplies that missing parameter and calls
all original bodies without changing their assertions; its result is an adapter
run, not a pass of the unmodified file's own pytest collection.
"""
from __future__ import annotations

import pytest

from tests.test_t35_progress_compaction_witness_20261113 import (
    test_t35_wit_forged_job_cache_compacted_witness_blocks_captured_claim as _forged,
    test_t35_wit_genuine_real_compaction_fresh_read_stays_captured as _fresh,
    test_t35_wit_pre_compaction_cursor_continuation_stays_captured_outside_frozen_prefix as _cursor,
)


@pytest.mark.parametrize('terminal_status', ('FAILED', 'SUCCEEDED'))
def test_witness_fresh_read_adapter(terminal_status: str) -> None:
    _fresh(terminal_status)


@pytest.mark.parametrize('tamper', ('operation-id', 'cleanup-time'))
def test_witness_tamper_adapter(tamper: str) -> None:
    _forged(tamper)


def test_witness_cursor_adapter() -> None:
    _cursor()
