"""Fresh independent per-call propagation controls on actual production objects.

All OS/native/prereadiness outputs are SDK boundaries. No COMSOL/real process,
signals, public isolation gate bypass, verified native proof or science claim.
"""
import sys,json,inspect
from pathlib import Path
import pytest
from comsol_mcp import _g2_isolation as isolation
OUT=Path(__file__).resolve().parent
CANDIDATE=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/local-preservation/w24-coupled-science-20261013-candidate05/repository')

def record(name,data):(OUT/name).write_text(json.dumps(data,indent=2)+'\n')

def run_fixture_case(controls,function,monkeypatch,folder,extra=None):
    folder.mkdir()
    with monkeypatch.context() as scoped:
        fixture=controls.owner4.__wrapped__(scoped,folder);owned=next(fixture)
        try:
            kwargs={'owner4':owned,'monkeypatch':scoped,'tmp_path':folder,**(extra or {})}
            function(**{name:kwargs[name] for name in inspect.signature(function).parameters})
        finally:
            try:next(fixture)
            except StopIteration:pass

def test_fresh_each_SDK_call_reclips_and_late_actual_consumers_stop(monkeypatch,tmp_path):
    sys.path.insert(0,str(CANDIDATE/'tests'))
    import test_w24_coupled_probe_propagation as controls
    try:
        # Actual process/socket parsers with owner. Positive parse control never
        # calls verify_owned_server and never produces verified proof metadata.
        folder=tmp_path/'positive-parsers';folder.mkdir()
        with monkeypatch.context() as scoped:
            fixture=controls.owner4.__wrapped__(scoped,folder);owner,clock,ps=next(fixture)
            calls=[]
            try:
                clock.mono=1295.
                def sdk(command,**kwargs):
                    from types import SimpleNamespace
                    calls.append({'command':command,'timeout':kwargs.get('timeout')})
                    clock.mono=1297.
                    return SimpleNamespace(returncode=0,stderr='',stdout=controls._sdk_rows(command))
                scoped.setattr(isolation.subprocess,'run',sdk)
                process=isolation._process_snapshot(101,admission_owner=owner)
                sockets=isolation._socket_rows(56388,admission_owner=owner)
                assert process['pid']==101 and len(sockets)==3
                assert [row['timeout'] for row in calls]==[5.,3.]
                assert owner.deadline_monotonic==1900. and owner.stop_reason is None
                record('ACTUAL_EACH_CALL_RECLIP_CONTROL.json',{'status':'PASS_SCOPED_SOURCE_SOFTWARE',
                    'actual_methods':['_process_snapshot','_mac_process_snapshot','_socket_rows','_mac_socket_rows','_run_mac_probe','originalOwner.before_effect'],
                    'original_deadline_monotonic':1900.,'work_cleanup_reserve':600.,'calls':calls,
                    'actual_parsed_process':process,'actual_socket_rows':sockets,
                    'verified_native_proof_minted':False,'native_run':'NOT_RUN'})
            finally:
                try:next(fixture)
                except StopIteration:pass
        cases=[(controls.test_actual_listener_identity_nested_PS_is_capped_and_late_return_mints_no_receipt,{}),
            (controls.test_actual_start_worker_nested_G2_PS_and_socket_probes_same_owner,{'late':True}),
            (controls.test_actual_ManagedBackend_G2_verifier_propagates_original_owner_and_refuses_receiver_changes,{'drift':'late_ps'}),
            (controls.test_actual_ManagedBackend_G2_verifier_propagates_original_owner_and_refuses_receiver_changes,{'drift':'late_socket'}),
            (controls.test_actual_ManagedBackend_G2_verifier_propagates_original_owner_and_refuses_receiver_changes,{'drift':'foreign_worker'}),
            (controls.test_actual_ManagedBackend_G2_verifier_propagates_original_owner_and_refuses_receiver_changes,{'drift':'bad_owner_json'})]
        executed=[]
        for index,(function,extra) in enumerate(cases):
            run_fixture_case(controls,function,monkeypatch,tmp_path/('negative-%02d'%index),extra)
            executed.append({'actual_control':function.__name__,'negative':extra})
        record('ACTUAL_CALLER_NEGATIVE_CONTROLS.json',{'status':'PASS_SCOPED_SOURCE_SOFTWARE',
            'controls':executed,'late_PS_then_socket_is_not_dispatched':True,
            'late_socket_no_next_route_and_no_verified_proof':True,'wrong_Worker_or_JSON_owner_no_SDK_calls':True,
            'actual_objects':['NativeLoopbackServer','ManagedBackend','OperationStore','typedOriginalOwner','SampledRssMonitor','actualG2Verifier'],
            'native_run':'NOT_RUN','public_native_admission':'NOT_ATTEMPTED'})
    finally:sys.path.remove(str(CANDIDATE/'tests'))

def test_fresh_actual_main_Controller_Store_and_postcap_original_observation(monkeypatch,tmp_path):
    sys.path.insert(0,str(CANDIDATE/'tests'))
    import test_w24_coupled_probe_propagation as controls
    try:
        run_fixture_case(controls,controls.test_actual_run_main_Worker_snapshot_reaches_bounded_callee_and_late_probe_retains_originals,
            monkeypatch,tmp_path/'actual-main')
        run_fixture_case(controls,controls.test_postcap_actual_inventory_uses_short_readonly_observation_without_work_budget,
            monkeypatch,tmp_path/'postcap-observation')
        record('ACTUAL_MAIN_POSTCAP_CONTROL.json',{'status':'PASS_SCOPED_SOURCE_SOFTWARE',
            'actual_objects':['run','ControlDaemon','projectAuthority','OperationStore','_process_snapshot','_run_mac_probe','sameOriginalOwnerRetainer','_process_inventory'],
            'main_Worker_identity_cap_seconds':5.,'late_return_original_handles_retained':True,
            'postcap_same_original_PS_observe_seconds':1.,'postcap_inventory_each_call_seconds':[5.,5.],
            'no_budget_reopening_or_signal':True,'original_deadline_unchanged':True,
            'sameHandle_terminalReap_and_complete_originalGroupAbsence_beforeReturn':True,
            'original_firstCause_TimeoutError_and_permanent_nonPASS':True,
            'scope':'Actual main/project Store after prereadiness/native/compiler boundary fixtures; source admission and native public gate not tested or bypassed',
            'native_run':'NOT_RUN','verified_native_proof_minted':False})
    finally:sys.path.remove(str(CANDIDATE/'tests'))
