"""Authenticated XZ extraction, exact copies and immutable actual TLS input."""
import json,os,stat
from pathlib import Path
import intel_deps_stages as deps
import openssl359_cpython_successor_r4 as prior
import openssl359_runtime_successor_r2 as tls
import liblzma_inputs_r1 as fixed
import liblzma_cpython_checkpoint_r2 as cp_input
from acquire_exact_archives import Refusal
from intel_crypto_successor_r1 import PREFIX,checkpoint_bytes
from intel_deps_prepare import copy_exact_tree,exclusive_json
RUNTIME=tls.ROOT
RUNTIME_SNAPSHOT=PREFIX.parent/'comsol-openssl359-runtime-r2-execution-vg00m5pi/COMPLETE_RUNTIME_R2_TREE.json'
RUNTIME_PIN={'kind':'regular','mode':0o600,'identity':{'device':16777231,'inode':24051482},'bytes':4677,'sha256':'106504c3452c2e796f6653e0412ba5308b72823088cf28b42cc7b76d252d0024'}
RESULT_SHA='2eb2ec56467611b7346630e4cc44bfda6eb6152ba1707580d902ce80c74e6e9c'
FAILED_R1=PREFIX/'intel-liblzma-r1'
FAILED_SNAPSHOT=PREFIX.parent/'comsol-liblzma-r1-failure-capture-u3nxv3ir/COMPLETE_FAILED_LIBLZMA_R1_TREE.json'
FAILED_PIN={'kind':'regular','mode':0o600,'identity':{'device':16777231,'inode':25864723},'bytes':1725367,'sha256':'3a0dafc2b188cfa9970ac4d8c4bd6ab1583e1340299cd846d9adf604e673fdba'}
FAILED_RECEIPT_SHA='7721fb0c0ce144107a461465c7839583bacc4812b497c78a368367a9a93704da'
def verify_failed_r1():
    from security_snapshot._host_scoped_provider import verify_private_path_security
    verify_private_path_security(PREFIX,directory=True);verify_private_path_security(FAILED_R1,directory=True)
    identity=lambda s:(s.st_dev,s.st_ino,s.st_uid,stat.S_IMODE(s.st_mode))
    expected=(16777231,25625234,os.getuid(),0o700)
    if identity(FAILED_R1.lstat())!=expected:raise Refusal('original failed R1 root identity changed')
    rows=json.loads(checkpoint_bytes(FAILED_SNAPSHOT,FAILED_PIN)[0])
    if deps.tree_record(FAILED_R1)!=rows or identity(FAILED_R1.lstat())!=expected:raise Refusal('original failed R1 entire5799 tree changed')
    rp=prior.HERE/'LIBLZMA_EXECUTION_RESULT_R1.json';record=prior.record(rp)
    if record['sha256']!=FAILED_RECEIPT_SHA:raise Refusal('original failed R1 actual receipt changed')
    result=json.loads(checkpoint_bytes(rp,record)[0])
    if result['native_outer_commands']!=0 or result['commands']!=[] or result['wrapper_terminal']['actual_wait_rc']!=1 or result['wrapper_terminal']['diagnostic_errors']:raise Refusal('original R1 first failure/zero native facts changed')
    return rows
def verify_tls_runtime():
    identity=lambda s:(s.st_dev,s.st_ino,s.st_uid,stat.S_IMODE(s.st_mode))
    expected=(16777231,24051041,os.getuid(),0o700)
    if identity(RUNTIME.lstat())!=expected:raise Refusal('accepted TLS runtime input root changed')
    rows=json.loads(checkpoint_bytes(RUNTIME_SNAPSHOT,RUNTIME_PIN)[0])
    if deps.tree_record(RUNTIME)!=rows or identity(RUNTIME.lstat())!=expected:raise Refusal('entire original TLS runtime tree changed')
    result_path=prior.HERE/'OPENSSL359_RUNTIME_EXECUTION_RESULT_R2.json';pin=prior.record(result_path)
    if pin['sha256']!=RESULT_SHA:raise Refusal('actual TLS runtime receipt changed')
    result=json.loads(checkpoint_bytes(result_path,pin)[0])
    if result['status']!='TLS_HASHLIB_SCOPED_ACTUAL_PASS' or result['command']['rc']!=0 or not result['command']['owned_process_terminal'] or not result['command']['owned_group_absent'] or not result['closed']['within_cap']:raise Refusal('actual accepted TLS command/closure differs')
    artifact_pin=next(r for r in rows if r['relative_path']=='ACTUAL_RUNTIME_AND_INSTALL.json')
    artifact=json.loads(checkpoint_bytes(RUNTIME/'ACTUAL_RUNTIME_AND_INSTALL.json',dict(artifact_pin,path=str(RUNTIME/'ACTUAL_RUNTIME_AND_INSTALL.json')))[0])
    profile=json.loads(checkpoint_bytes(RUNTIME/'ARM_RUNTIME_PROFILE.json',artifact['actual_profile_first_record'])[0])
    if profile!=result['actual_profile'] or profile!=artifact['actual_arm_profile']:raise Refusal('accepted actual TLS profile differs')
    from openssl359_runtime_verify_r2 import validate_profile
    validate_profile(profile);tls.verify_inputs()
    return {'entire_TLS_runtime17_unchanged':True,'actual_TLS_runtime_receipt_sha256':RESULT_SHA,'actual_R4_14215_and_all_old_unchanged':True}
def extracted_content_matches(folder,inventory):
    actual=deps.tree_record(folder)
    projected=[{k:r[k] for k in ('relative_path','kind','mode','bytes','sha256') if k in r} for r in actual]
    # Preserve every member, duplicate and field. Path ordering and whole
    # relative-path string ordering differ for directories and same-prefix .h.
    if sorted(projected,key=lambda row:row['relative_path'])!=sorted(inventory['rows'],key=lambda row:row['relative_path']):raise Refusal('actual extracted XZ complete content/mode differs from sameFD official listing')
    return actual
def copy_verified_pristine_xz(destination,inputs):
    original=verify_failed_r1();source=FAILED_R1/'xz-input'
    extracted_content_matches(source,inputs['inventory'])
    first=next(r for r in original if r['relative_path']=='xz-input')
    root=dict(relative_path='',kind='directory',uid=os.getuid(),device=first['identity']['device'],inode=first['identity']['inode'],mode=oct(first['mode']))
    rows=[]
    for r in original:
        if not r['relative_path'].startswith('xz-input/'):continue
        entry=dict(relative_path=r['relative_path'][len('xz-input/'):],kind=r['kind'],uid=os.getuid(),device=r['identity']['device'],inode=r['identity']['inode'],mode=oct(r['mode']))
        if r['kind']=='regular':entry.update(bytes=r['bytes'],sha256=r['sha256'],nlink=1)
        rows.append(entry)
    if len(rows)!=704:raise Refusal('original full XZ704 source copy closure differs')
    copy_exact_tree(source,rows,destination,root_record=root)
    extracted_content_matches(destination,inputs['inventory']);verify_failed_r1()
    return {'original_failed5799_unchanged':True,'original_complete_XZ704_exact_copy':True,'new_XZ_official_unpack':0}
def prepare(x):
    if x['root']!=PREFIX/'intel-liblzma-r2':raise Refusal('fixed fresh LZMA unit required')
    verify_tls_runtime();verify_failed_r1();inputs=fixed.verify_inputs_and_scan();cp=cp_input.prepare_successor(x)
    destination=x['root']/'xz-input';reuse=copy_verified_pristine_xz(destination,inputs)
    source=destination/'xz-5.8.4';tree=deps.tree_record(source);s=source.lstat();root=dict(relative_path='',kind='directory',uid=s.st_uid,device=s.st_dev,inode=s.st_ino,mode=oct(stat.S_IMODE(s.st_mode)))
    rows=[]
    for r in tree:
        item=dict(relative_path=r['relative_path'],kind=r['kind'],uid=os.getuid(),device=r['identity']['device'],inode=r['identity']['inode'],mode=oct(r['mode']))
        if r['kind']=='regular':item.update(bytes=r['bytes'],sha256=r['sha256'],nlink=1)
        rows.append(item)
    for arch in ('arm64','x86_64'):copy_exact_tree(source,rows,x['xz_source_'+arch],root_record=root)
    report={'cp_copied':cp,'authenticated_XZ_inputs':inputs['inputs'],'official_XZ_pristine_tree':deps.tree_record(destination),'arch_sources':{a:deps.tree_record(x['xz_source_'+a]) for a in ('arm64','x86_64')},'accepted_TLS':verify_tls_runtime(),'original_failed_R1_XZ_reuse':reuse}
    exclusive_json(x['root']/'LZMA_PREPARED_INPUTS.json',report);return {'accepted_record':prior.record(x['root']/'LZMA_PREPARED_INPUTS.json')}
def verify_prepared(x,accepted):
    path=x['root']/'LZMA_PREPARED_INPUTS.json'
    if accepted['accepted_record']['path']!=str(path):raise Refusal('original accepted prepare record path differs')
    report=json.loads(checkpoint_bytes(path,accepted['accepted_record'])[0]);cp_input.verify_copied_inputs(x,report['cp_copied'])
    fixed.verify_inputs_and_scan();verify_tls_runtime();verify_failed_r1()
    if deps.tree_record(x['root']/'xz-input')!=report['official_XZ_pristine_tree']:raise Refusal('official pristine XZ input tree changed')
    for arch in ('arm64','x86_64'):
        if deps.tree_record(x['xz_source_'+arch])!=report['arch_sources'][arch]:raise Refusal('fixed XZ arch source changed')
    return {'unchanged_original_TLS_and_XZ_inputs':True}
