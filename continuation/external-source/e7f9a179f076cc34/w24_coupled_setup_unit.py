"""Coupled zero-solve configuration unit using the existing managed setup runner."""
from pathlib import Path
import time
import json

SCHEMA='W24_COUPLED_ZERO_SOLVE_SETUP_CONTRACT_V1'
ROOT_DESIGN_SHA='7201fb134afa884c8de2217b892d361bfda6005ffa6124c1e432423975047911'
BIRTH_BUDGET_SECONDS=1800.0
CLEANUP_RESERVE_SECONDS=600.0

def setup_contract():
    return {'schema':SCHEMA,'root_design_sha256':ROOT_DESIGN_SHA,'cure_build_phase':'build_v2',
        'cure_reopen_phase':'readback_v2','mu_scale':1.0,'static_baselines':['flat','step'],
        'static_configuration':'baseline','original_sensitivity_slots_required':14,
        'server_birth_budget_seconds_including_cleanup':BIRTH_BUDGET_SECONDS,
        'cleanup_reserve_seconds':CLEANUP_RESERVE_SECONDS,'owned_servers':1,'sequential_workers':1,
        'Study_solver_PhaseInitialization_submissions':0,'GUI':0,
        'stable_native_shape':'REQUIRED_LATER_NOT_PROVIDED_BY_SETUP',
        'scientific_acceptance':'NOT_ACCEPTED'}

def validate_setup_contract(value):
    try:
        exact = type(value) is dict and json.dumps(value,sort_keys=True,allow_nan=False)==json.dumps(setup_contract(),sort_keys=True,allow_nan=False)
    except (TypeError,ValueError):
        exact = False
    if not exact:raise ValueError('coupled setup contract differs from the exact frozen typed zero-solve unit')
    return dict(value)

def build_static_baseline_setups(*, daemon, project_id, workspace, project_response,
        parent_binding, evidence, server_pid, worker_pid, server_birth_epoch, deadline_epoch,
        native_epoch_capability, admission_owner):
    """No birth/solve path here; reuse existing live Worker and managed slot fence."""
    from tools.w24_coupled_admission import require_admission_owner
    owner=require_admission_owner(admission_owner)
    if owner.deadline_epoch!=deadline_epoch or owner.birth_epoch!=server_birth_epoch:
        raise ValueError("static cases must retain the original whole-unit birth/deadline")
    from tools.w24_native_epoch_capability import require_native_epoch_capability
    capability=require_native_epoch_capability(native_epoch_capability)
    from comsol_mcp._platform_process import process_identity
    from tools.run_native_w24_static_shape_setup import StaticShapeManagedRunner,prepare_project_sources
    from tools.run_native_w24_static_shape_setup_campaign import (
        BudgetedSetupDaemon,SampledRssMonitor,_terminal_campaign_ledger)
    from tools import run_native_w24_cure_preflight as preflight
    identity=process_identity(server_pid)
    if identity.get("alive") is not True or type(identity.get("start_epoch_ms")) is not int or abs(identity["start_epoch_ms"]-int(server_birth_epoch*1000))>=1000:
        raise RuntimeError('existing owned Server actual birth identity unavailable')
    evidence=Path(evidence);evidence.mkdir(mode=0o700)
    sources=prepare_project_sources(Path(workspace))
    monitor=owner.monitor
    if (monitor is None or owner.handles.get('server').pid!=server_pid or owner.handles.get('worker').pid!=worker_pid):
        raise ValueError('static setup requires the original birth-bound Server/Worker/RSS owner')
    owner.before_effect('static_baseline_unit_enter',120.0)
    deadline_monotonic=owner.deadline_monotonic
    gate=BudgetedSetupDaemon(daemon,monitor=monitor,deadline_monotonic=deadline_monotonic,
        events_path=evidence/'route_events.jsonl',workspace=Path(workspace),project_id=project_id,
        native_epoch_capability=capability)
    runner_evidence=evidence/'managed';runner_evidence.mkdir(mode=0o700)
    runner=StaticShapeManagedRunner(daemon=gate,project_id=project_id,project_workspace=Path(workspace),
        project_create_response=project_response,parent_binding=parent_binding,source_manifest=sources,
        setup_runner=preflight,evidence_dir=runner_evidence,timeout_s=120.0,
        native_epoch_capability=capability)
    records=[]
    try:
        for case in ('flat','step'):
            if time.time()>=deadline_epoch-CLEANUP_RESERVE_SECONDS or monitor.stop_reason or gate.unknown:
                raise RuntimeError('coupled baseline setup reached budget/resource/UNKNOWN stop before next slot')
            gate.set_slot('baseline',case)
            result=runner.build_save_reopen(case,'baseline')
            if gate.slot_stage!='readback_complete' or result['study_run_submissions']!=[] or result['phase_initialization_executed'] is not False:
                raise RuntimeError('baseline setup did not preserve exact no-solve staged readback')
            ledger=_terminal_campaign_ledger(daemon,project_id)
            if ledger.get('safe_to_retire') is not True:
                raise RuntimeError('baseline setup ledger is not fully terminal; no next slot')
            preflight.write_json(evidence/(case+'_setup_receipt.json'),result)
            preflight.write_json(evidence/(case+'_terminal_ledger.json'),ledger)
            records.append(result);gate.clear_slot()
        return {'status':'ACTUAL_BASELINE_CONFIGURATIONS_SAVED_REOPENED_NOT_SOLVED',
            'cases':records,'sources':sources,'actual_cure_worker_epoch':capability.as_evidence(),
            'original_sensitivity_slots_required':14,
            'native_stable_shapes':'NOT_CAPTURED','scientific_acceptance':'NOT_ACCEPTED'}
    finally:
        # The whole-unit caller retains monitor and handles through cure+both cases.
        pass
