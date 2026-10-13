"""Read-only actual359R2 fat checkpoint and fresh original CP-only sources."""
import json,os,stat
from pathlib import Path
from acquire_exact_archives import Refusal
from intel_crypto_successor_r1 import PREFIX,HERE,checkpoint_bytes
from intel_deps_prepare import exclusive_json
import intel_deps_stages as deps
import tls_sources_r2 as sources
import openssl359_sources_r2 as prior_sources
import openssl359_successor_r2 as prior
from tls_cpython_checkpoint_r4 import copy_blob

R2=PREFIX/'intel-tls-openssl359-r2'
SNAPSHOT=PREFIX.parent/'comsol-openssl359-r2-execution-yrv5ddg2/FAILED_OPENSSL359_R2_COMPLETE_TREE.json'
SNAPSHOT_PIN={'identity':{'device':16777231,'inode':22796733},'bytes':8672763,'kind':'regular','mode':0o600,'sha256':'54dd019e70b951fbfde2e119ec28fb8f1102ab30a237960890d727d32ac85a30'}
ROOT_ACTUAL_SHA='d72abed9a82beb8ffac7950813bec997cf677c4141e1504a28480279fc304cec'
def rows():return json.loads(checkpoint_bytes(SNAPSHOT,SNAPSHOT_PIN)[0])
def r2_bytes(relative,records):
    selected=[r for r in records if r['relative_path']==relative]
    if len(selected)!=1 or selected[0]['kind']!='regular':raise Refusal('fixed actual359R2 prerequisite regular member absent')
    return checkpoint_bytes(R2/relative,dict(selected[0],path=str(R2/relative)))[0]
def verify_failed_r2():
    from security_snapshot._host_scoped_provider import verify_private_path_security
    verify_private_path_security(R2,directory=True);st=R2.lstat()
    if (st.st_dev,st.st_ino,st.st_uid,stat.S_IMODE(st.st_mode))!=(16777231,22740834,os.getuid(),0o700):raise Refusal('actual359R2 failed root changed')
    actual=rows()
    if deps.tree_record(R2)!=actual:raise Refusal('entire actual359R2 failedsource/fences/artifact/raw tree changed')
    if (R2.lstat().st_dev,R2.lstat().st_ino,R2.lstat().st_mode)!=(st.st_dev,st.st_ino,st.st_mode):raise Refusal('actual359R2 root swapped')
    root_record=prior.record(HERE/'ROOT_OPENSSL359_R2_ACTUAL_FAILURE_PRESERVATION_CHECK.json')
    if root_record['sha256']!=ROOT_ACTUAL_SHA:raise Refusal('Root actual359R2 check changed')
    checkpoint_bytes(Path(root_record['path']),root_record)
    return {'actual359R2_complete_unchanged':True,'records':len(actual),'snapshot_sha256':SNAPSHOT_PIN['sha256'],'Root_actual_check_sha256':ROOT_ACTUAL_SHA}
def native_closure(records):
    result=json.loads(r2_bytes('RESULT.json',records));closed=json.loads(r2_bytes('CLOSED.json',records));failure=json.loads(r2_bytes('FAIL.json',records))
    path=str(R2/'cpython-build/build/lib.macosx-11.0-universal2-3.12/_ssl.cpython-312-darwin.so')
    if result['status']!='VALIDATION_FAIL' or closed['status']!='VALIDATION_FAIL' or failure['type']!='OSError' or failure['cause']!="[Errno 62] Too many levels of symbolic links: '"+path+"'" or len(result['commands'])!=11 or result['diagnostic_errors'] or closed['errors']:raise Refusal('actual359R2 first conservatively rejectedlink/11terminal facts differ')
    plan,env=prior.command_plan(prior.layout());checks=[]
    for command,(label,argv,cwd) in zip(result['commands'],plan[:11]):
        name=command['result_name'];base=name.removesuffix('.result.json')
        request=json.loads(r2_bytes('logs/'+base+'.request.json',records));spawn=json.loads(r2_bytes('logs/'+base+'.spawn.json',records));birth=json.loads(r2_bytes('logs/'+base+'.birth.json',records));terminal=json.loads(r2_bytes('logs/'+name,records));group=json.loads(r2_bytes('logs/'+base+'.group.json',records))
        if command['argv']!=argv or command['rc']!=0 or command['status']!='PASS' or not command['owned_process_terminal'] or not command['owned_group_absent'] or command['diagnostic_errors']:raise Refusal('actual359R2 prerequisite command not truly PASS')
        if terminal!={k:v for k,v in command.items() if k!='result_name'} or birth!=command['birth'] or group!=command['group'] or request['argv']!=argv or request['cwd']!=str(cwd) or request['environment']!=env or request['stdout']!=command['stdout'] or request['stderr']!=command['stderr'] or spawn['pid']!=command['pid'] or spawn['argv']!=argv or birth['pid']!=command['pid'] or birth['uid']!=os.getuid():raise Refusal('actual359R2 raw/request/env/spawn/birth/group/wait linkage differs')
        if group['status']!='OBSERVED' or group['actual_pgid']!=command['pid'] or group['actual_sid']!=command['pid'] or not group['independent_session_observed']:raise Refusal('actual359R2 independent originalgroup differs')
        r2_bytes('logs/'+command['stdout'],records);r2_bytes('logs/'+command['stderr'],records)
        checks.append({'label':label,'actual_pid':command['pid'],'owned_process_terminal':True,'owned_group_absent':True,'original_raw_linkage_verified':True})
    return checks

def verify_checkpoint():
    old=prior_sources.preservation_verified_summary();failure=verify_failed_r2();records=rows();commands=native_closure(records)
    def member_pin(relative):
        matches=[r for r in records if r['relative_path']==relative]
        if len(matches)!=1 or matches[0]['kind']!='regular':raise Refusal('fixed actualmerge prerequisite absent')
        return dict(matches[0],path=str(R2/relative))
    output_pin=member_pin('MERGE_OUTPUT_VERIFIED.json');input_pin=member_pin('MERGE_INPUTS.json');prior.verify_merged_inputs(prior.layout(),output_pin,input_pin)
    merge=json.loads(checkpoint_bytes(R2/'MERGE_OUTPUT_VERIFIED.json',output_pin)[0]);inputs=json.loads(checkpoint_bytes(R2/'MERGE_INPUTS.json',input_pin)[0]);artifacts=[]
    for item in merge['archives']:
        name=item['name'];child=item['actual_command'];expected=['/usr/bin/lipo','-create',inputs['archives'][name]['arm64']['path'],inputs['archives'][name]['x86_64']['path'],'-output',str(R2/'fat/lib'/name)]
        if child['actual_wait_rc']!=0 or not child['terminal_wait_observed'] or child['diagnostic_errors'] or child['request']['argv']!=expected or child['request']['inherited_group']!=commands[6]['actual_pid']:raise Refusal('actual359R2 lipo originalwait/makegroup differs')
        raw,_=checkpoint_bytes(Path(item['output']['path']),item['output']);artifacts.append({'name':name,'original':item['output'],'raw':raw,'slices':item['binary']})
    headers=deps.tree_record(R2/'fat/include')
    return {'old':old,'r2':failure,'commands':commands,'headers':headers,'artifacts':artifacts}

def prepare_successor(x):
    if x['root']!=PREFIX/'intel-liblzma-r1':raise Refusal('fixed fresh359CP-onlyR3 namespace required')
    checked=verify_checkpoint();prior.copy_headers(R2/'fat/include',x['fat']/'include',checked['headers']);copies=[]
    for item in checked['artifacts']:
        destination=x['fat']/'lib'/item['name'];copy_blob(destination,item['raw']);row=prior.record(destination)
        if checkpoint_bytes(destination,row)[0]!=item['raw']:raise Refusal('fresh actual359fat copiedbytes differ')
        copies.append({'name':item['name'],'original':item['original'],'new':row,'original_slices_verified':True})
    inventory=sources.inventory(PREFIX);cp_rows=[dict(r,relative_path=r['relative_path'][len(sources.CP_RELATIVE)+1:]) for r in inventory if r['relative_path'].startswith(sources.CP_RELATIVE+'/')]
    fd=os.open(PREFIX/'archives'/sources.CP_ARCHIVE['filename'],os.O_RDONLY|os.O_NOFOLLOW)
    try:
        before=sources.verify_archive_fd(fd,sources.CP_ARCHIVE);cp=sources.extract_pristine(fd,x['root']/'cpython-input',cp_rows,4928,101560866)
        if sources.verify_archive_fd(fd,sources.CP_ARCHIVE)!=before:raise Refusal('fixed originalCParchive changed during R3preparation')
    finally:os.close(fd)
    if deps.file_record(x['cp_source']/'Modules/_ssl.c')['sha256']!=prior_sources.CP_SSL_SHA:raise Refusal('original officialCPssl bytes changed')
    folder=x['root']/'runtime-certfixtures';folder.mkdir(mode=0o700)
    from tls_cert_checks_r4 import FIXTURE_BYTES
    from openssl359_memorybio_r1 import CERT_PIN
    fixture_copies=[]
    for name,fixed in dict(FIXTURE_BYTES,**{'keycert.pem':CERT_PIN}).items():fixture_copies.append(prior_sources.copy_pinned_fixture(x['cp_source']/'Lib/test/certdata'/name,folder/name,fixed))
    exclusive_json(x['root']/'CERT_FIXTURE_INPUTS.json',FIXTURE_BYTES)
    report={'original359R2_conservation':checked['r2'],'old_conservation':checked['old'],'original_native_commands':checked['commands'],'fat_copies':copies,'fresh_headers':deps.tree_record(x['fat']/'include'),'official_original_CPsource':cp,'CParchive':before,'official_unmodified_CPython':True,'fixed_fixture_copies':fixture_copies}
    exclusive_json(x['root']/'CHECKPOINT_COPIED_INPUTS_VERIFIED.json',report);report['accepted_checkpoint_record']=prior.record(x['root']/'CHECKPOINT_COPIED_INPUTS_VERIFIED.json');return report

def verify_copied_inputs(x,accepted):
    path=x['root']/'CHECKPOINT_COPIED_INPUTS_VERIFIED.json'
    if accepted['accepted_checkpoint_record']['path']!=str(path):raise Refusal('accepted R3copied checkpoint escapednamespace')
    report=json.loads(checkpoint_bytes(path,accepted['accepted_checkpoint_record'])[0])
    if not report['official_unmodified_CPython'] or deps.file_record(x['cp_source']/'Modules/_ssl.c')['sha256']!=prior_sources.CP_SSL_SHA:raise Refusal('originalCP_SSL source changed')
    if [r for r in deps.tree_record(x['fat']/'include') if r['relative_path']=='openssl' or r['relative_path'].startswith('openssl/')]!=report['fresh_headers']:raise Refusal('copied actual359header tree changed')
    if len(report['fat_copies'])!=2 or {r['name'] for r in report['fat_copies']}!={'libssl.a','libcrypto.a'}:raise Refusal('two original359copiedlibs required')
    for item in report['fat_copies']:
        destination=x['fat']/'lib'/item['name']
        if item['new']['path']!=str(destination) or item['new']['sha256']!=item['original']['sha256']:raise Refusal('actual359copiedlib path/hash differs')
        checkpoint_bytes(destination,item['new'])
    for item in report['fixed_fixture_copies']:checkpoint_bytes(Path(item['destination_fixed_verified']['path']),item['destination_fixed_verified'])
    verify_checkpoint()
    return {'actual359R2_full_old_and_copied_inputs_unchanged':True}

def preservation_verified_summary():
    result=verify_checkpoint()
    return {'old':result['old'],'actual359R2':result['r2'],'original_native_commands':result['commands'],'actual359fat_libraries':[{'name':a['name'],'file':a['original'],'slices':a['slices']} for a in result['artifacts']]}
