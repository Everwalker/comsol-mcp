"""Pytest adapter for the accepted exact 38 manual C2 cases.

Original test module remains byte-exact. Its original historical SOFTWARE_ONLY
issuer continuation controls the consumer, not C qualification. No original
manual-runner effect permission is inherited; the coherent runner hard guard
is active before imports.
"""
import pytest
from tests import test_t037_resume_attempt as original

_SELECTED = [('actual_consumer_releases_global_issuer_lock_before_resume_sdk', ()), ('dispatch_reservation_rejects_concurrent_transfer_and_registry_close', ()), ('actual_factory_raw_one_first_ownership_failure_is_sticky_unknown', ('thread-close', 'preparation-close', 'ancillary-close', 'parent-close', 'connected-b-settlement', 'publication')), ('actual_consumer_refuses_wrong_original_api_owner_before_consuming', ()), ('actual_consumer_rechecks_original_api_owner_after_job_query', ()), ('copied_capability_is_rejected_by_actual_consumer_without_resume', ()), ('wrong_thread_consumer_reentry_is_rejected_before_sdk', ()), ('non_exact_resume_return_type_is_retained_unknown_without_cleanup', (True, original._IntLikeOne())), ('non_one_previous_count_stops_all_factory_finally_sdk_cleanup', (0, 2, 4294967295)), ('resume_call_exception_is_sticky_unknown_and_preserves_original_child', ()), ('exact_dword_previous_count_is_captured_without_coercion', (0, 1, 2, 4294967295)), ('raw_return_transfer_failure_retains_raw_and_blocks_followup', ()), ('raw_return_assignment_transfer_failure_keeps_preallocated_exact_root', ()), ('actual_consumer_rechecks_original_bindings_after_job_query', ('function', 'issuer-profile', 'epoch', 'job-profile', 'launch-inputs', 'connected-B', 'thread-entry')), ('no_resume_entry_keeps_existing_known_cleanup_path', ()), ('original_worker_late_exit_noeffect_witness', (True,)), ('real_issuer_still_refuses_without_registering_any_capability', ()), ('existing_connected_b_full_factory_refusal_regression', ()), ('existing_suspended_ownership_refusal_cleanup_regression', ()), ('existing_prepared_launch_manifest_regression', ()), ('existing_installer_abi_regression', ())]
_ACCEPTED_CASES=[(name, value) for name, parameters in _SELECTED for value in (parameters or (None,))]
assert len(_ACCEPTED_CASES)==38
# This accepted manual wrapper only delegates to the exact canonical prepared
# test selected in the same coherent union (and appends audit metadata). Run
# that original assertion body once, rather than reporting two independent
# controls. The original accepted module/wrapper stays byte-exact.
_CANONICAL_ALIAS={
    'existing_prepared_launch_manifest_regression':
        'tests/test_windows_prepared_launch.py::test_protected_manifest_accepts_only_retired_fixed_compiler_material'}
_CASES=[case for case in _ACCEPTED_CASES if case[0] not in _CANONICAL_ALIAS]
assert len(_CASES)==37

@pytest.mark.parametrize('name,value',_CASES,ids=[name+':'+str(i) for i,(name,_) in enumerate(_CASES)])
def test_exact_accepted_c2_consumer(monkeypatch,tmp_path,name,value):
    function=getattr(original,'test_'+name)
    if value is None:
        function(monkeypatch,tmp_path)
    else:
        function(monkeypatch,tmp_path,value)
