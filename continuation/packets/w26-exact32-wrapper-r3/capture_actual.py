import json,hashlib,os,sys,tempfile,time
from pathlib import Path
R=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');D=Path(__file__).parent;A=D.parent/'w26-helper-successor-r2';W=Path((D/'W_PATH.txt').read_text().strip());E=R/'repository/docs/full_project_continuation_v2/evidence/intel-builder-execution-r1';sys.path.insert(0,str(A));from strict_derived import read_regular
sys.path.insert(0,str(E));import intel_deps_stages as deps
C=Path(tempfile.mkdtemp(prefix='comsol-w26-exact32-actual-capture-r1-')).resolve()
term=json.loads(read_regular(W/'TERMINAL.json')[0]);tool=json.loads(read_regular(W/'ENTRY_TOOL_TERMINAL.json')[0]);fresh=json.loads(read_regular(W/'FRESH_OWNED_ABSENCE.json')[0]);last=json.loads((W/'ENTRY_STDOUT.raw').read_text().splitlines()[-1]);prefix=Path(term['prefix'])
assert tool['actual_exit_code']==0 and last['status']=='PASS_EXACT32_DOWNLOAD_ONLY' and last['elapsed']<600 and last['diagnostic_errors']==[]
assert term['status']=='PASS_EXACT32_PENDING_FINAL_WRAPPER_WAIT' and term['old_roots_unchanged'] and term['command']['owned_group_absent'] and term['command']['owned_process_terminal'] and term['command']['rc']==0
assert fresh['rc']==1 and not fresh['stdout'].strip() and not fresh['stderr'].strip() and fresh['owned_group_absent'] is True
m=json.loads(read_regular(A/'EXACT32_DOWNLOAD_MANIFEST.json')[0]);verified=[]
for row in m['artifacts']:
 p=prefix/'archives'/row['filename'];_,rec=read_regular(p)
 resultpath=prefix/'evidence'/(row['artifact_id']+'.result.json');requestpath=prefix/'evidence'/(row['artifact_id']+'.request.json');res=json.loads(read_regular(resultpath)[0]);req=json.loads(read_regular(requestpath)[0])
 assert rec['bytes']==row['expected_bytes'] and rec['sha256']==row['expected_sha256'] and rec['mode']==384 and rec['nlink']==1
 assert res['status']=='ACQUIRED_EXACT' and res['rc']==0 and res['http_status']==200 and res['actual_bytes']==rec['bytes'] and res['actual_sha256']==rec['sha256'] and res['url']==row['url'] and req['url']==row['url']
 assert res['identity']=={'device':rec['dev'],'inode':rec['inode']}
 verified.append({'artifact_id':row['artifact_id'],'record':rec,'http_result':read_regular(resultpath)[1],'http_request':read_regular(requestpath)[1],'http_status':200,'redirects':res['redirects']})
assert len(verified)==32 and sum(q['record']['bytes'] for q in verified)==21090946 and not list((prefix/'archives').glob('*.partial'))
current=deps.tree_record(prefix);accepted=json.loads(read_regular(W/'COMPLETE_ACQUISITION_TREE.json')[0]);assert current==accepted
with (C/'COMPLETE_NEW_TREE.json').open('x') as f:json.dump(current,f,indent=2,sort_keys=True);f.write('\n');f.flush();os.fsync(f.fileno())
refs=[read_regular(p)[1] for p in sorted(W.rglob('*')) if p.is_file()]+[read_regular(p)[1] for p in [E/'ROOT_W26_EXACT32_ONCE_DISPATCH_BINDING_R3.json',E/'ROOT_W26_EXACT32_SOURCE_REVIEW_R3.json',A/'EXACT32_DOWNLOAD_MANIFEST.json',D/'FREEZE_R3.json',prefix/'OWNER.json',prefix/'ACTOR_RESULT.json',C/'COMPLETE_NEW_TREE.json']]
result={'schema':'comsol-w26-exact32-actual/1','status':'PASS_SCOPED_EXACT32_PUBLIC_INPUT_ACQUISITION_AND_PRESERVATION','tool':tool,'wrapper_pid':30672,'actor_pid':30699,'actual_actor_group':30699,'actual_wrapper_elapsed_s':last['elapsed'],'original_tool_session':57650,'same_handle_closed_rc':0,'owned_fresh_absence':fresh,'prefix':str(prefix),'root_identity':{'device':prefix.stat().st_dev,'inode':prefix.stat().st_ino,'uid':prefix.stat().st_uid,'mode':prefix.stat().st_mode&0o777},'all32_count':32,'all32_bytes':21090946,'verified_archives':verified,'new_tree_records':len(current),'new_tree':read_regular(C/'COMPLETE_NEW_TREE.json')[1],'old_original43_and3failedpartials_and_runtime16870_unchanged':{'before':term['old_before'],'after':term['old_after'],'passed':term['old_roots_unchanged']},'sources_unchanged':True,'references':refs,'effects':{'public_exact_file_GETs':32,'pip_install':0,'target_python':0,'wheel_code':0,'unpack':0,'build':0,'COMSOL':0,'Windows':0,'CAS_Progress_Git':0},'product_install_fullruntime_threearch_delivery':'NOT_RUN','created_unix':time.time()}
for row in json.loads(read_regular(W/'METADATA.json')[0])['source_refs']:assert read_regular(Path(row['path']))[1]==row
p=D/'ACTUAL_EXECUTION_R3.json'
with p.open('x') as f:json.dump(result,f,indent=2,sort_keys=True);f.write('\n');f.flush();os.fsync(f.fileno())
print(read_regular(p)[1]);print('prefix',str(prefix),'tree',len(current),'capture',C)
