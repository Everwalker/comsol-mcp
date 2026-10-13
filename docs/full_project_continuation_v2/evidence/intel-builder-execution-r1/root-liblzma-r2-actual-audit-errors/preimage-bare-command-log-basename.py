import datetime,hashlib,json,os,pathlib,stat,subprocess,sys
B=pathlib.Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');E=B/'repository/docs/full_project_continuation_v2/evidence/intel-builder-execution-r1';sys.path.insert(0,str(E))
import liblzma_successor_r2 as u
from liblzma_prepare_r2 import verify_tls_runtime
from tls_binary_r1 import fat_archive
v={}
def ck(row):
 p=pathlib.Path(row['path']);actual=u.frozen_digest(p);assert actual['sha256']==row['sha256'] and actual['bytes']==row.get('bytes',actual['bytes'])
 fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
 try:
  a=os.fstat(fd);parts=[]
  while chunk:=os.read(fd,1048576):parts.append(chunk)
  raw=b''.join(parts);z=os.fstat(fd);assert (a.st_dev,a.st_ino,a.st_size,a.st_mtime_ns,a.st_ctime_ns)==(z.st_dev,z.st_ino,z.st_size,z.st_mtime_ns,z.st_ctime_ns)
 finally:os.close(fd)
 assert hashlib.sha256(raw).hexdigest()==row['sha256']
 if 'identity'in row:assert row['identity']=={'device':a.st_dev,'inode':a.st_ino}
 if 'mode'in row:assert row['mode']==stat.S_IMODE(a.st_mode)
 v[str(p)]={'path':str(p),**actual};return raw
def refs(obj):
 if isinstance(obj,dict):
  if isinstance(obj.get('path'),str) and len(str(obj.get('sha256','')))==64:ck(obj)
  for value in obj.values():refs(value)
 elif isinstance(obj,list):
  for value in obj:refs(value)
p=E/'LIBLZMA_EXECUTION_RESULT_R2.json';a=json.loads(ck({'path':str(p),'sha256':'6f61767373090652bdd3070251bebb39141aa309a0638490107c9ecd0e1fd65b'}));refs(a)
assert a['status']=='LZMA_TLS_SCOPED_ACTUAL_PASS' and a['actual_tool_session']==24981 and a['wrapper_pid']==5908 and a['actor_pid']==5946
term=a['wrapper_terminal'];assert term['actual_wait_rc']==0 and term['diagnostic_errors']==[] and term['same_handle_observed'] and term['elapsed_including_wait_and_flush_s']==467.99602983301156
assert a['native_outer_commands']==len(a['commands'])==13 and all(c['rc']==0 and c['owned_process_terminal'] and c['owned_group_absent'] and not c['diagnostic_errors']for c in a['commands'])
assert a['closed']['within_cap'] and not a['closed']['errors'] and a['closed']['preservation_failure'] is None
assert all(x==0 for x in a['effects'].values()) and a['new_CP_official_safeextract']==1
x=u.layout();root=x['root'];s=root.lstat();assert (s.st_dev,s.st_ino,s.st_uid,stat.S_IMODE(s.st_mode))==(16777231,26652210,os.getuid(),0o700)
tree=json.loads(ck(a['complete_new_tree']));assert len(tree)==16870 and sum(r.get('bytes',0)for r in tree if r['kind']=='regular')==808153827 and u.deps.tree_record(root)==tree
W=pathlib.Path('/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T/comsol-liblzma-r2-execution-3wacz70a')
for name in ['wrapper.py','argv.json','spawn.json','birth.json','stdout','stderr','terminal.json','START_INPUT_PINS.json','PRIOR_OWNED_PID_ABSENCE.json','PRIOR_OWNED_GROUP_ABSENCE.json']:
 q=W/name;ck({'path':str(q),**u.frozen_digest(q)})
assert json.loads((W/'terminal.json').read_bytes())==term
binding=json.loads(ck({'path':str(E/'ROOT_LIBLZMA_R2_ONCE_DISPATCH_BINDING.json'),'sha256':'adbe26ea01cf31c682730f654efc96ed55e263531f17459e708d188b133439c4'}))
assert json.loads((W/'argv.json').read_bytes())['argv']==binding['exact_actor_argv']
plan,base_environment=u.command_plan(x);assert len(plan)==13 and [c['argv']for c in a['commands']]==[argv for label,argv,cwd in plan]
for c,(label,argv,cwd)in zip(a['commands'],plan):
 done=json.loads(ck({'path':str(root/('STAGE_'+label+'_DONE.json')),**u.frozen_digest(root/('STAGE_'+label+'_DONE.json'))}))
 assert set(done['command'])==set(c)|{'result_name'} and {k:done['command'][k] for k in c}==c
 original_result=pathlib.Path(c['stdout']).parent/done['command']['result_name']
 assert json.loads(ck({'path':str(original_result),**u.frozen_digest(original_result)}))==c
profile=json.loads(ck(a['actual_profile_first_record']));assert profile==a['actual_profile']
assert profile['actual_machine']=='arm64' and profile['provider_info']==[3,5,0,9,0] and profile['provider_number']==0x30500090 and profile['provider']=='OpenSSL 3.5.9 29 Sep 2026'
assert profile['actual_lzma_roundtrip'] and profile['actual_tar_xz_product_roundtrip'] and profile['sha256_known_vector'] and profile['ssl_context_created'] and profile['official_unmodified_CPython'] and profile['network_connections']==0
assert all(profile['modules'][name]['status']=='IMPORTED'for name in ['_lzma','zlib','sqlite3','ctypes','venv'])
assert all(profile['modules'][name]['status']=='IMPORT_FAILED'for name in ['_gdbm','_tkinter']) and profile['gdbm_tk_requirement']=='UNRESOLVED_NO_SCOPE_EXCLUSION' and profile['x86_runtime']=='NOT_RUN'
cert=profile['actual_certificate_cases'];assert len(cert['cases'])==4 and cert['malformed_negative'] and cert['socket_calls']==0
assert all(c.get('expected_fields_matched')or(c.get('actual_malformed_rejected')and c.get('error_type')=='SSLError')for c in cert['cases'])
bio=profile['actual_MemoryBIO'];assert bio['sockets_created']==bio['network_connections']==0 and [c['required_version']for c in bio['actual_cases']]==['TLSv1.2','TLSv1.3'] and all(c['actual_handshake_complete']and c['encrypted_payload_roundtrip']for c in bio['actual_cases'])
runtime_log=next(c['stdout']for c in a['commands']if c['argv']==plan[-1][1]);lines=ck({'path':runtime_log,**u.frozen_digest(pathlib.Path(runtime_log))}).decode().splitlines();reported=[json.loads(line[len('ARM_PROFILE '):])for line in lines if line.startswith('ARM_PROFILE ')];assert reported==[profile]
assert u.actual_binaries(x)==a['actual_final_binary']
entry=a['actual_library']['archives'][0];assert len(a['actual_library']['archives'])==1 and entry['name']=='liblzma.a'
thin={arch:u.checkpoint_bytes(x['lzma_'+arch]/'lib/liblzma.a',u.record(x['lzma_'+arch]/'lib/liblzma.a'))[0]for arch in ('arm64','x86_64')}
fat=ck(entry['output']);assert fat_archive(fat,thin)==entry['binary'] and len(fat)==540272
assert all(entry['binary']['slices'][arch]['object_count']==79 for arch in thin)
assert len(a['lipo_children'])==1 and a['lipo_children'][0]['terminal_wait_observed'] and a['lipo_children'][0]['actual_wait_rc']==0 and not a['lipo_children'][0]['diagnostic_errors']
u.compare_headers(x['lzma_arm64']/'include',x['lzma_x86_64']/'include')
f=json.loads(ck({'path':str(E/'LIBLZMA_SOURCE_FREEZE_R2.json'),'sha256':'3d6cf9b36dad862d4a125b337dee62405db1021d4aa14ca33fbd348550cdf9ea'}))
preservation=verify_tls_runtime();archives=u.verify_original_archives(f)
pids=a['fresh_owner_absence']['pids'];groups=[r['pgid']for r in a['fresh_owner_absence']['groups']];assert len(pids)==17 and len(groups)==14
ps=subprocess.run(['/bin/ps','-p',','.join(map(str,pids)),'-o','pid=,ppid=,uid=,lstart=,command='],capture_output=True,text=True,timeout=10);assert ps.returncode==1 and not ps.stdout.strip() and not ps.stderr.strip()
for group in groups:
 try:os.killpg(group,0)
 except ProcessLookupError:pass
 else:raise AssertionError('original group not absent')
payload={'schema':'ROOT_LIBLZMA_R2_ACTUAL_CONCENTRATED_CHECK/1','created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'PASS_SCOPED_ACTUAL_ARM_LZMA_TARXZ_TLS_AND_FAT_BYTES','actual_receipt':v[str(p)],'verified_sameFD_refs':list(v.values()),'same_handle_original_session':24981,'wrapper_actor':[5908,5946],'original_terminal':term,'outer_commands':13,'lipo_child':1,'full_current_new_tree':{'records':16870,'regular_bytes':808153827,'root':a['root_identity'],'snapshot':a['complete_new_tree']},'actual_first_profile':a['actual_profile_first_record'],'actual_ARM':{'lzma_and_tarxz':True,'provider_info':profile['provider_info'],'provider_number':profile['provider_number'],'certificate_cases':4,'MemoryBIO':['TLSv1.2','TLSv1.3'],'known_vectors_and_python_regressions':True,'network_sockets':0},'fat_binary_and_liblzma_objects_only':{'actual_bytes_two_slices_verified':True,'liblzma_bytes':540272,'objects_each_arch':79,'shared_headers_equal':True,'_lzma_dynamic_system_fallback':False,'hardware_Intel':'NOT_RUN'},'current_old_inputs_and_failed_R1_5799_R4_14215_Runtime17_preservation':preservation,'original43_archives':archives,'fresh_original_owner_absence':{'ps':{'argv':ps.args,'rc':ps.returncode,'stdout':ps.stdout,'stderr':ps.stderr},'17pids':pids,'14groups':groups,'zero_probe_only_no_signals_sent':True},'Root_new_target_runtime_or_compiler':0,'_gdbm__tkinter':'IMPORT_FAILED_PRESERVED_PRODUCT_SCOPE_UNRESOLVED','full_T037_hardquota_three_arch_delivery_Goal':'OPEN_NOT_ACCEPTED','Goal':'ACTIVE_INCOMPLETE'}
dest=E/'ROOT_LIBLZMA_R2_ACTUAL_CHECK.json';raw=(json.dumps(payload,indent=2,sort_keys=True)+'\n').encode();fd=os.open(dest,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
try:
 data=memoryview(raw)
 while data:data=data[os.write(fd,data):]
 os.fsync(fd)
finally:os.close(fd)
print(json.dumps({'path':str(dest),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'refs':len(v),'status':payload['status']}))
