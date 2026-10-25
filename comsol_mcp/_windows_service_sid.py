"""Exact existing-service SID setup leaf; configuration never grants OS proof."""
from __future__ import annotations
import ctypes as C
import hashlib
import json
from pathlib import Path
from ._execution_contract import ExecutionContractError
from . import _w26_os_resources as resources

DESIRED_SCHEMA='comsol-mcp.windows-service-sid-config/1'
BACKUP_SCHEMA='comsol-mcp.windows-service-sid-backup/1'
SEQUENCE_SCHEMA='comsol-mcp.host-control-mutation-sequence/4'
RECEIPT_SCHEMA='comsol-mcp.applied-host-change-receipt/4'


def fail(code,message):
    raise ExecutionContractError(code,message,safe_retry=False,stage='pre_dispatch')


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')
def digest(value):return hashlib.sha256(canonical(value)).hexdigest()


def _read_original_backup(api,handle,metadata,*,_limit=None,_hash_only=False):
    from ._windows_parameter_publish import _FileInfo,DWORD
    from ._windows_host_preflight_owner import scalar
    owner=api._readonly_preflight_owner;owner.check_owner()
    limit=resources.MAX_HOST_PLAN_BYTES if _limit is None else _limit
    entry=owner.registry.find(api,handle)
    if entry is None or entry not in owner.entries():fail('HOST_CHANGE_BACKUP_UNKNOWN','backup requires original retained file entry')
    def info():
        storage=_FileInfo();owner.hold(handle,storage)
        result=owner.sdk['GetFileInformationByHandle'](handle,C.byref(storage))
        if scalar(result,'BOOL')==0:raise owner._original_win_error(owner._original_get_last_error())
        identity={'device':int(storage.volume),'inode':(int(storage.index_high)<<32)|int(storage.index_low)}
        if storage.attributes&0x410 or storage.links!=1:
            fail('HOST_CHANGE_BACKUP_INVALID','backup must be an ordinary singly linked file')
        if any(identity[k]!=metadata['identity'][k] for k in identity):fail('HOST_CHANGE_BACKUP_UNKNOWN','backup file identity changed')
        return (identity, (int(storage.size_high)<<32)|int(storage.size_low),
                int(storage.creation.high)<<32|int(storage.creation.low),
                int(storage.write.high)<<32|int(storage.write.low),int(storage.links),int(storage.attributes))
    first=info();size=first[1]
    if not 0<size<=limit:fail('HOST_CHANGE_BACKUP_INVALID','backup size outside original bounded budget')
    raw=bytearray();consumed=0;hashed=hashlib.sha256()
    buffer=C.create_string_buffer(65536);count=DWORD();owner.hold(handle,buffer,count)
    while True:
        requested=min(65536,limit+1-consumed)
        if requested<=0:fail('HOST_CHANGE_BACKUP_INVALID','backup bytes exceed original budget')
        count.value=0
        result=owner.sdk['ReadFile'](handle,buffer,requested,C.byref(count),None)
        if scalar(result,'BOOL')==0:raise owner._original_win_error(owner._original_get_last_error())
        got=scalar(count,'DWORD')
        if got>requested:fail('HOST_CHANGE_BACKUP_UNKNOWN','ReadFile returned invalid byte count')
        block=buffer.raw[:got];hashed.update(block);consumed+=got
        if not _hash_only:raw.extend(block)
        if consumed>size:fail('HOST_CHANGE_BACKUP_UNKNOWN','backup read exceeds pinned size')
        if got==0:break
    if consumed!=size or info()!=first:fail('HOST_CHANGE_BACKUP_UNKNOWN','backup changed or ended before pinned size')
    return {'raw':bytes(raw),'security':metadata,'file_fingerprint':first,'sha256':hashed.hexdigest()}


def read_backup(path,expected_sha256,*,_executor=None):
    from ._host_scoped_provider import _verify_windows_private_path
    actual=_verify_windows_private_path(Path(path),directory=False,expected_identity=None,
                                        _retained_callback=_read_original_backup,
                                        _original_owner_callback=(lambda owner: retain_preflight(_executor, owner)) if _executor is not None else None)
    raw=actual.pop('raw')
    if hashlib.sha256(raw).hexdigest()!=expected_sha256:fail('HOST_CHANGE_BACKUP_MISMATCH','actual pinned backup does not match approved bytes')
    def pairs(items):
        answer={}
        for key,value in items:
            if key in answer:fail('HOST_CHANGE_BACKUP_INVALID','duplicate backup field')
            answer[key]=value
        return answer
    try:document=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda value:fail('HOST_CHANGE_BACKUP_INVALID','nonfinite backup field'))
    except (ValueError,UnicodeError) as error:raise ExecutionContractError('HOST_CHANGE_BACKUP_INVALID','backup is not strict JSON') from error
    if canonical(document)!=raw:fail('HOST_CHANGE_BACKUP_INVALID','backup must be canonical JSON bytes')
    resources._reject_secrets(document,'service_sid_backup')
    actual['document']=document;actual['raw_sha256']=expected_sha256
    return actual


def is_leaf(actions):
    return any(isinstance(a,dict) and isinstance(a.get('desired'),dict) and a['desired'].get('schema')==DESIRED_SCHEMA for a in actions)


def validate_desired(desired,profile):
    fields={'schema','service_name','service_start_name','service_logon_sid','service_sid','service_sid_type'}
    policy=profile.windows_isolation
    if set(desired)!=fields or desired['schema']!=DESIRED_SCHEMA or profile.platform!='windows' or policy is None:
        fail('HOST_CHANGE_SCM_DESIRED_INVALID','closed Windows service SID desired fields required')
    for field in fields-{'schema'}:
        expected=getattr(policy,field)
        if type(desired[field]) is not str or desired[field]!=expected:fail('HOST_CHANGE_SCM_PROFILE_MISMATCH','service SID desired differs from exact trusted profile')
    if desired['service_sid_type']!='RESTRICTED':fail('HOST_CHANGE_SCM_DESIRED_INVALID','only explicit RESTRICTED setup supported')


def normalize(payload,profile):
    sources=payload['change_manifest']
    if len(sources)!=1:fail('HOST_CHANGE_SCM_MIXED_PLAN','service SID setup requires exactly one explicit UPDATE')
    source=sources[0];validate_desired(source['desired'],profile)
    if source['resource_kind']!='windows_service' or source['operation']!='UPDATE':fail('HOST_CHANGE_SCM_MIXED_PLAN','service SID setup requires windows_service UPDATE')
    return {**source,'resource_key':source['ownership_guid'],'plan_sha256':resources._sha256(payload)}


def check_subject(subject,profile,pid):
    policy=profile.windows_isolation;images=[i for i in profile.launch_images if i.role=='launcher']
    if len(images)!=1:fail('HOST_CHANGE_SCM_SUBJECT_INVALID','exactly one trusted launcher required')
    image=images[0]
    if (subject['service_name']!=policy.service_name or subject['service_start_name']!=policy.service_start_name
        or subject['service_start_sid']!=policy.service_logon_sid or subject['service_sid']!=policy.service_sid
        or subject['service_type']!=0x10 or subject['status']['service_type']!=0x10
        or subject['status']['state']!=4 or subject['status']['process_id']!=pid or pid<=0
        or subject['service_binary_image_path']!=image.path or subject['service_binary_argv_sha256']!=image.argv_sha256
        or subject.get('image_sha256')!=image.image_sha256):
        fail('HOST_CHANGE_SCM_SUBJECT_INVALID','current service is not exact active own-process pinned launcher subject')
    if subject['sid_type'] not in (0,1,3):fail('HOST_CHANGE_SCM_SUBJECT_INVALID','unknown SID configuration bits')
    if any(resources._is_secret_key(arg.lstrip('-/').split('=',1)[0]) for arg in subject['argv']):
        fail('HOST_CHANGE_SCM_SUBJECT_INVALID','service command line contains credential-bearing selectors')


def check_backup(actual,profile,source,subject):
    doc=actual['document']
    expected={'schema':BACKUP_SCHEMA,'profile_sha256':profile.sha256,'project_id':profile.project_id,
              'host_id':profile.host_id,'root':profile.root.as_dict(),'subject':subject}
    if (type(doc) is not dict or set(doc)!=set(expected) or canonical(doc)!=canonical(expected)
        or digest(subject)!=source['proposed_baseline_sha256']
        or canonical(source['proposed_baseline'])!=canonical(subject)):
        fail('HOST_CHANGE_BACKUP_MISMATCH','actual closed backup differs from original profile/current exact service baseline')
    return expected


def validate_journal_leaf(baseline,row):
    sequence=baseline.get('mutation_sequence',{});steps=sequence.get('steps',[])
    if (sequence.get('schema')!=SEQUENCE_SCHEMA or len(steps)!=1 or steps[0].get('resource_kind')!='windows_service'
        or baseline.get('service_sid_leaf_schema')!=DESIRED_SCHEMA or not isinstance(baseline.get('service_subject'),dict)
        or not isinstance(baseline.get('original_backup'),dict)):
        fail('HOST_CONTROL_JOURNAL_CORRUPT','service SID journal does not bind exact one-step subject/backup')
    if digest({'subject':baseline['service_subject'],'backup':baseline['original_backup']})!=baseline.get('dependency_sha256'):
        fail('HOST_CONTROL_JOURNAL_CORRUPT','service SID journal subject/backup digest mismatch')


def _preflight(executor,plan):
    import os
    from ._windows_host_change import _normalize_supported_actions,_current_windows_binding
    payload,actions=_normalize_supported_actions(plan,executor.profile);action=actions[0]
    if len(actions)!=1 or not is_leaf(payload['change_manifest']):fail('HOST_CHANGE_SCM_MIXED_PLAN','explicit single service SID leaf required')
    current=({'host_id':executor.profile.host_id,'host_fingerprint_sha256':executor.profile.windows_isolation.host_fingerprint_sha256,
              'root_identity':executor.profile.root.as_dict(),'service_token_sid':executor.profile.windows_isolation.service_logon_sid,
              'evidence_classification':'SOFTWARE_ONLY'} if executor.software_only else _current_windows_binding(executor.profile))
    subject=executor.api._service_sid_observe(executor.profile.windows_isolation.service_name)
    check_subject(subject,executor.profile,os.getpid())
    actual=read_backup(action['proposed_backup_path'],action['proposed_backup_sha256'],_executor=executor)
    return action,current,subject,actual


def _baseline(action,current,subject,actual,*,phase,plan_sha,expected=None):
    # Actual bytes are represented by bounded pinned file identity and exact
    # canonical document digest, never by a proposed path/hash alone.
    backup={'path':action['proposed_backup_path'],'raw_sha256':actual['raw_sha256'],
            'security':actual['security'],'file_fingerprint':json.loads(canonical(actual['file_fingerprint'])),
            'document_sha256':digest(actual['document'])}
    dep=digest({'subject':subject,'backup':backup})
    return {'service_sid_leaf_schema':DESIRED_SCHEMA,'service_subject':subject,'original_backup':backup,
            'current_binding':current,'dependency_sha256':dep,
            'mutation_sequence':{'schema':SEQUENCE_SCHEMA,'phase':phase,'plan_sha256':plan_sha,
              'steps':[{'step_index':0,'action_id':action['action_id'],'resource_kind':'windows_service',
                        'resource_key':action['resource_key'],'identity_sha256':action['desired_identity_sha256'],
                        'dependency_sha256':dep,'expected_resource_sha256':expected}]}}


def receipt_body(plan,profile,actions):
    return {'schema':RECEIPT_SCHEMA,'plan_sha256':plan.sha256,'host_id':profile.host_id,
            'platform':'windows','actions':[dict(a) for a in actions]}


def apply(executor,plan,approval):
    action,current,subject,actual=_preflight(executor,plan)
    if subject['sid_type']==3:fail('HOST_CHANGE_SCM_ALREADY_CONFIGURED','service SID already restricted; no new applied receipt')
    check_backup(actual,executor.profile,action,subject)
    baseline=_baseline(action,current,subject,actual,phase='APPLY',plan_sha=plan.sha256)
    admission=executor._admit(phase='APPLY',plan_sha256=plan.sha256,approval=approval,baseline=baseline)
    try:
        executor._start(admission)
        intent={'step_index':0,'action_id':action['action_id'],'resource_kind':'windows_service','resource_key':action['resource_key'],
                'desired_identity_sha256':action['desired_identity_sha256'],'dependency_sha256':baseline['dependency_sha256']}
        executor.store.append_host_control_mutation_step_event(admission_id=admission.admission_id,event='APPLY_STEP_INTENT',payload=intent)
        after=executor.api._service_sid_configure(subject['service_name'],subject,3)
        if after!={**subject,'sid_type':3}:fail('HOST_CHANGE_SCM_READBACK_UNKNOWN','complete configured subject differs')
        executor.store.append_host_control_mutation_step_event(admission_id=admission.admission_id,event='APPLY_STEP_READBACK_VERIFIED',payload={**intent,'created_resource_sha256':digest(after)})
        rows=({'action_id':action['action_id'],'resource_kind':'windows_service','resource_key':action['resource_key'],
               'created_resource_sha256':digest(after),'prior_resource_sha256':digest(subject),'prior_backup_path':action['proposed_backup_path']},)
        body=receipt_body(plan,executor.profile,rows);receipt_sha=digest(body)
        receipt=resources.AppliedHostChangeReceipt(plan.sha256,executor.profile.host_id,'windows',rows,receipt_sha)
        result={'success':True,'operation':'host.change.apply','execution_state':'SUCCEEDED',
                'host_change_state':'CONFIGURED_UNVERIFIED_SYSTEM_RESTART_REQUIRED','plan_sha256':plan.sha256,
                'profile_sha256':executor.profile.sha256,'applied_receipt':{**body,'receipt_sha256':receipt_sha},
                'receipt_sha256':receipt_sha,'effective_policy_state':'UNVERIFIED','t037_state':'NOT_VERIFIED','safe_retry':False,
                'evidence_classification':'SOFTWARE_ONLY' if executor.software_only else 'WINDOWS_NATIVE_READBACK_ONLY'}
        accepted,state=executor._finish_apply(admission,result)
        if not accepted or state!='SUCCEEDED':fail('HOST_CHANGE_RESULT_PERSISTENCE_UNKNOWN','service config readback terminal receipt not durable')
        return receipt
    except BaseException as error:
        executor._finish_unknown_sequence(admission,phase='APPLY',action=action,step_index=0,cause=error)
        raise


def restore(executor,plan,receipt,*,approval=None,authorization_ref=None):
    action,current,subject,actual=_preflight(executor,plan)
    if type(receipt) is not resources.AppliedHostChangeReceipt or receipt.plan_sha256!=plan.sha256 or receipt.host_id!=executor.profile.host_id or receipt.platform!='windows' or len(receipt.actions)!=1:
        fail('HOST_CHANGE_SCM_RECEIPT_MISMATCH','exact original service SID receipt required')
    original=actual['document'].get('subject')
    if not isinstance(original,dict) or type(original.get('sid_type')) is not int or original['sid_type'] not in (0,1):fail('HOST_CHANGE_BACKUP_MISMATCH','original baseline SID type absent')
    check_backup(actual,executor.profile,action,original)
    expected_row={'action_id':action['action_id'],'resource_kind':'windows_service','resource_key':action['resource_key'],
                  'created_resource_sha256':digest({**original,'sid_type':3}),
                  'prior_resource_sha256':digest(original),'prior_backup_path':action['proposed_backup_path']}
    if dict(receipt.actions[0])!=expected_row or digest(receipt_body(plan,executor.profile,receipt.actions))!=receipt.receipt_sha256:
        fail('HOST_CHANGE_SCM_RECEIPT_MISMATCH','receipt body/schema/original backup differs')
    with executor.store.lock:
        rows=executor.store.db.execute(
            "SELECT result,metadata FROM operations WHERE operation='host.change.apply' AND status='SUCCEEDED' AND json_extract(result,'$.receipt_sha256')=? LIMIT 2",
            (receipt.receipt_sha256,)).fetchall()
    if len(rows)!=1:
        fail('HOST_CHANGE_SCM_RECEIPT_MISMATCH','original successful durable apply receipt is absent or ambiguous')
    result=json.loads(rows[0]['result']);metadata=json.loads(rows[0]['metadata'])
    expected_document={**receipt_body(plan,executor.profile,receipt.actions),'receipt_sha256':receipt.receipt_sha256}
    binding=metadata.get('host_control',{})
    if (result.get('applied_receipt')!=expected_document or result.get('profile_sha256')!=executor.profile.sha256
        or result.get('host_change_state')!='CONFIGURED_UNVERIFIED_SYSTEM_RESTART_REQUIRED'
        or binding.get('plan_sha256')!=plan.sha256 or binding.get('profile_sha256')!=executor.profile.sha256
        or binding.get('root_identity')!=executor.profile.root.as_dict() or binding.get('phase')!='APPLY'):
        fail('HOST_CHANGE_SCM_RECEIPT_MISMATCH','original receipt does not match durable original profile/job/root')
    if subject!={**original,'sid_type':3}:fail('HOST_CHANGE_SCM_SUBJECT_CHANGED','foreign or changed service preserved for recovery')
    restore_body={'schema':'comsol-mcp.windows-service-sid-restore/1','source_plan_sha256':plan.sha256,
                  'source_receipt_sha256':receipt.receipt_sha256,'profile_sha256':executor.profile.sha256,
                  'subject_sha256':digest(subject),'backup_sha256':actual['raw_sha256'],'original_subject_sha256':digest(original),
                  'resource_key':action['resource_key']}
    restore_sha=digest(restore_body)
    if authorization_ref is not None:approval=resources.HostChangeApproval(restore_sha,authorization_ref)
    baseline=_baseline(action,current,subject,actual,phase='RESTORE',plan_sha=restore_sha,expected=digest(subject))
    admission=executor._admit(phase='RESTORE',plan_sha256=restore_sha,approval=approval,baseline=baseline,
                              source_plan_sha256=plan.sha256,source_receipt_sha256=receipt.receipt_sha256)
    try:
        executor._start(admission)
        intent={'step_index':0,'action_id':action['action_id'],'resource_kind':'windows_service','resource_key':action['resource_key'],
                'expected_resource_sha256':digest(subject),'dependency_sha256':baseline['dependency_sha256']}
        executor.store.append_host_control_mutation_step_event(admission_id=admission.admission_id,event='RESTORE_STEP_INTENT',payload=intent)
        after=executor.api._service_sid_configure(subject['service_name'],subject,original['sid_type'])
        if after!=original:fail('HOST_CHANGE_SCM_READBACK_UNKNOWN','complete restored service subject differs')
        executor.store.append_host_control_mutation_step_event(admission_id=admission.admission_id,event='RESTORE_STEP_READBACK_VERIFIED',payload={**intent,'removed_resource_sha256':digest(subject)})
        result={'success':True,'operation':'host.change.restore','execution_state':'SUCCEEDED','host_change_state':'RESTORED_UNVERIFIED',
                'plan_sha256':restore_sha,'source_plan_sha256':plan.sha256,'source_receipt_sha256':receipt.receipt_sha256,
                'effective_policy_state':'UNVERIFIED','t037_state':'NOT_VERIFIED','safe_retry':False,
                'evidence_classification':'SOFTWARE_ONLY' if executor.software_only else 'WINDOWS_NATIVE_READBACK_ONLY'}
        accepted,state=executor.store.finish(admission.operation_id,status='SUCCEEDED',result=result)
        if not accepted or state!='SUCCEEDED':fail('HOST_CHANGE_RESULT_PERSISTENCE_UNKNOWN','service restore readback terminal result not durable')
        return result
    except BaseException as error:
        executor._finish_unknown_sequence(admission,phase='RESTORE',action=action,step_index=0,cause=error)
        raise


def retain_preflight(executor,owner):
    owners=getattr(executor.api,'_original_service_sid_preflight_owners',None)
    if owners is None:owners=[];executor.api._original_service_sid_preflight_owners=owners
    if owner not in owners:owners.append(owner)


def original_preflights_terminal(api):
    from ._windows_host_preflight_owner import confirmed_original_cleanup
    original_call=getattr(api,'_appcontainer_original_call',None)
    if original_call is not None and not original_call[5]():return False
    context=getattr(api,'_original_scm_leaf_context',None)
    if context is not None and context.get('pending_return') is not None:return False
    return all(confirmed_original_cleanup(owner) for owner in getattr(api,'_original_service_sid_preflight_owners',()))
