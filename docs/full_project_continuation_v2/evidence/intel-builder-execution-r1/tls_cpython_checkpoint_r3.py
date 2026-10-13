"""Read-only r2 completed TLS checkpoint, fresh verified copies and CP source."""
import json,os,stat
from pathlib import Path
from acquire_exact_archives import Refusal
from intel_crypto_successor_r1 import PREFIX,checkpoint_bytes
import intel_deps_stages as deps
import tls_sources_r2 as sources
import tls_successor_r2 as prior
from tls_binary_r1 import fat_archive
from intel_deps_prepare import exclusive_json

R2=PREFIX/'intel-tls-r2'
SNAPSHOT=PREFIX.parent/'comsol-tls-execution-r2-9rlmhh5r/FAILED_TLS_R2_COMPLETE_TREE.json'
SNAPSHOT_PIN={'identity':{'device':16777231,'inode':18705979},'bytes':4164251,'sha256':'035652b74c1b33dbe131aad0924568a3d7b6b1220dda9908ee3ebff5a4078d53'}

def snapshot_rows():return deps.read_dep_stage_record(SNAPSHOT,SNAPSHOT_PIN)

def r2_bytes(relative,rows):
    row=[r for r in rows if r['relative_path']==relative]
    if len(row)!=1 or row[0]['kind']!='regular':raise Refusal('fixed r2 checkpoint member absent/ambiguous')
    return checkpoint_bytes(R2/relative,dict(row[0],path=str(R2/relative)))[0]

def verify_failed_r2(rows):
    from security_snapshot._host_scoped_provider import verify_private_path_security
    verify_private_path_security(R2,directory=True);st=R2.lstat()
    if (st.st_dev,st.st_ino,st.st_uid,stat.S_IMODE(st.st_mode))!=(16777231,18690492,os.getuid(),0o700):raise Refusal('original r2 root changed')
    if deps.tree_record(R2)!=rows:raise Refusal('complete failed r2 artifacts/raw/fences tree changed')
    final=R2.lstat()
    if (final.st_dev,final.st_ino,final.st_mode)!=(st.st_dev,st.st_ino,st.st_mode):raise Refusal('r2 root swapped during verification')
    return {'failed_r2_unchanged':True,'tree_records':len(rows),'snapshot_sha256':SNAPSHOT_PIN['sha256']}

def native_closure(rows):
    result=json.loads(r2_bytes('RESULT.json',rows));closed=json.loads(r2_bytes('CLOSED.json',rows));failure=json.loads(r2_bytes('FAIL.json',rows))
    if result['status']!='VALIDATION_FAIL' or closed['status']!='VALIDATION_FAIL' or failure['type']!='FileExistsError' or failure['cause']!="[Errno 17] File exists: 'merge.PASS.json'" or len(result['commands'])!=4 or result['diagnostic_errors'] or closed['errors']:raise Refusal('r2 expected post-merge first failure/terminal facts differ')
    plan,_=prior.command_plan(prior.layout());checks=[]
    for c,(label,argv,cwd) in zip(result['commands'],plan[:4]):
        if c['argv']!=argv or c['rc']!=0 or c['status']!='PASS' or not c['owned_process_terminal'] or not c['owned_group_absent'] or c['diagnostic_errors']:raise Refusal('original successful r2 command closure differs')
        name=c['result_name'];base=name.removesuffix('.result.json')
        request=json.loads(r2_bytes('logs/'+base+'.request.json',rows));spawn=json.loads(r2_bytes('logs/'+base+'.spawn.json',rows));birth=json.loads(r2_bytes('logs/'+base+'.birth.json',rows));terminal=json.loads(r2_bytes('logs/'+name,rows))
        group=json.loads(r2_bytes('logs/'+base+'.group.json',rows))
        # run() attaches result_name only to its returned value, after persisting
        # the actual terminal payload; compare every persisted field exactly.
        if terminal!={k:v for k,v in c.items() if k!='result_name'} or group!=c['group'] or birth!=c['birth'] or request['argv']!=argv or request['cwd']!=str(cwd) or request['stdout']!=c['stdout'] or request['stderr']!=c['stderr'] or spawn['pid']!=c['pid'] or spawn['argv']!=argv or spawn['uid_at_spawn']!=os.getuid() or birth['pid']!=c['pid'] or birth['uid']!=os.getuid() or birth['invocation_argv']!=argv:raise Refusal('r2 raw request/spawn/birth/terminal linkage differs')
        g=c['group']
        if g['status']!='OBSERVED' or g['actual_pgid']!=c['pid'] or g['actual_sid']!=c['pid'] or not g['independent_session_observed']:raise Refusal('r2 actual owned group identity differs')
        r2_bytes('logs/'+c['stdout'],rows);r2_bytes('logs/'+c['stderr'],rows)
        checks.append({'label':label,'actual_pid':c['pid'],'owned_process_terminal':True,'owned_group_absent':True,'raw_linkage_verified':True})
    return checks

def verify_tls_checkpoint():
    old=sources.verify_prior_tls_and_source();rows=snapshot_rows();r2=verify_failed_r2(rows);commands=native_closure(rows)
    merge=json.loads(r2_bytes('MERGE.PASS.json',rows));inputs=json.loads(r2_bytes('MERGE_INPUTS.json',rows));header=json.loads(r2_bytes('HEADERS_EXACT.json',rows))
    actual_headers=prior.compare_headers(R2/'openssl-arm64/include',PREFIX/'intel-deps-r2/openssl-x86_64/include')
    if actual_headers!=header or not header['exact_bytes_equal']:raise Refusal('actual original generated headers differ from r2 receipt')
    fat_headers=deps.tree_record(R2/'fat/include')
    for other in (header['arm'],header['x86_64']):
        norm=lambda rows:[{k:v for k,v in r.items() if k not in ('identity','mode')} for r in rows]
        if norm(fat_headers)!=norm(other):raise Refusal('r2 fat prefix header closure differs')
    if len(merge['archives'])!=2 or {a['name'] for a in merge['archives']}!={'libssl.a','libcrypto.a'}:raise Refusal('two exact r2 fat archives required')
    artifacts=[]
    for item in merge['archives']:
        name=item['name'];expected=item['output']
        if expected['path']!=str(R2/'fat/lib'/name):raise Refusal('r2 fat artifact path not fixed')
        raw,_=checkpoint_bytes(Path(expected['path']),expected);thin={}
        for arch,path in (('arm64',R2/'openssl-arm64/lib'/name),('x86_64',PREFIX/'intel-deps-r2/openssl-x86_64/lib'/name)):
            record=inputs['archives'][name][arch]
            if record['path']!=str(path):raise Refusal('original thin archive pointer differs')
            thin[arch],_=checkpoint_bytes(path,record)
        parsed=fat_archive(raw,thin)
        if parsed!=item['binary']:raise Refusal('actual fat slice/full object/minOS evidence differs')
        child=item['actual_command'];expected_argv=['/usr/bin/lipo','-create',inputs['archives'][name]['arm64']['path'],inputs['archives'][name]['x86_64']['path'],'-output',str(R2/'fat/lib'/name)]
        if child['actual_wait_rc']!=0 or not child['terminal_wait_observed'] or child['diagnostic_errors'] or child['request']['argv']!=expected_argv or child['request']['inherited_group']!=commands[3]['actual_pid']:raise Refusal('actual official lipo/outer make group closure differs')
        artifacts.append({'name':name,'file':expected,'binary':parsed,'raw':raw})
    return {'old':old,'r2':r2,'commands':commands,'headers':fat_headers,'artifacts':artifacts}

def copy_blob(path,raw):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        view=memoryview(raw)
        while view:view=view[os.write(fd,view):]
        os.fsync(fd)
    finally:os.close(fd)

def prepare_successor(x):
    if x['root']!=PREFIX/'intel-tls-cpython-r3':raise Refusal('only fresh fixed CP successor namespace allowed')
    checked=verify_tls_checkpoint();prior.copy_headers(R2/'fat/include',x['fat']/'include',checked['headers']);copies=[]
    for artifact in checked['artifacts']:
        destination=x['fat']/'lib'/artifact['name'];copy_blob(destination,artifact['raw']);record=prior.record(destination);raw,_=checkpoint_bytes(destination,record)
        if raw!=artifact['raw']:raise Refusal('new fat archive bytes differ from verified checkpoint')
        copies.append({'name':artifact['name'],'original':artifact['file'],'new':record,'slices_verified':True})
    rows=sources.inventory(PREFIX);cp_rows=[dict(r,relative_path=r['relative_path'][len(sources.CP_RELATIVE)+1:]) for r in rows if r['relative_path'].startswith(sources.CP_RELATIVE+'/')]
    archives=os.open(PREFIX/'archives',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);fd=None
    try:
        fd=os.open(sources.CP_ARCHIVE['filename'],os.O_RDONLY|os.O_NOFOLLOW,dir_fd=archives);before=sources.verify_archive_fd(fd,sources.CP_ARCHIVE)
        cp=sources.extract_pristine(fd,x['root']/'cpython-input',cp_rows,4928,101560866)
        if sources.verify_archive_fd(fd,sources.CP_ARCHIVE)!=before:raise Refusal('original CP archive changed during successor extraction')
    finally:
        if fd is not None:os.close(fd)
        os.close(archives)
    report={'old_preservation':checked['old'],'r2_preservation':checked['r2'],'native_prerequisite_commands':checked['commands'],'fat_copies':copies,'headers':deps.tree_record(x['fat']/'include'),'CP_pristine':cp,'CParchive':before}
    exclusive_json(x['root']/'CHECKPOINT_INPUT_COPIED_VERIFIED.json',report)
    report['accepted_checkpoint_record']=prior.record(x['root']/'CHECKPOINT_INPUT_COPIED_VERIFIED.json')
    return report

def verify_copied_inputs(x,accepted):
    path=x['root']/'CHECKPOINT_INPUT_COPIED_VERIFIED.json'
    if accepted['accepted_checkpoint_record']['path']!=str(path):raise Refusal('accepted copy receipt path differs')
    checkpoint=json.loads(checkpoint_bytes(path,accepted['accepted_checkpoint_record'])[0])
    if deps.tree_record(x['fat']/'include')!=checkpoint['headers']:raise Refusal('fresh copied header tree changed')
    if len(checkpoint['fat_copies'])!=2 or {r['name'] for r in checkpoint['fat_copies']}!={'libssl.a','libcrypto.a'}:raise Refusal('exact two copied fat libraries required')
    for item in checkpoint['fat_copies']:
        path=x['fat']/'lib'/item['name']
        if item['new']['path']!=str(path):raise Refusal('copied fat library pointer differs')
        if item['new']['sha256']!=item['original']['sha256']:raise Refusal('copied fat library SHA differs from actual source')
        checkpoint_bytes(path,item['new'])
    return {'copied_two_fat_libraries_and_header_tree_unchanged':True}

def preservation_verified_summary():
    result=verify_tls_checkpoint()
    return {'old':result['old'],'r2':result['r2'],'original_native_commands':result['commands'],'same145_generated_headers_and_fatprefix_headers':True,'fat_libraries':[{'name':a['name'],'file':a['file'],'slices':{arch:{'objects':s['object_count'],'archive_sha256':s['archive_sha256']} for arch,s in a['binary']['slices'].items()}} for a in result['artifacts']]}
