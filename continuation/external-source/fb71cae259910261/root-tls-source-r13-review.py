import ast,datetime,hashlib,importlib,json,os,pathlib,sys
R=pathlib.Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');B=R/'repository';E=B/'docs/full_project_continuation_v2/evidence/intel-builder-execution-r1';v={}
def ck(row):
    p=pathlib.Path(row['path'])
    if not p.is_absolute():p=(R if row['path'].startswith('repository/') else B)/row['path']
    assert not p.is_symlink(),str(p)
    raw=p.read_bytes();h=hashlib.sha256(raw).hexdigest();assert h==row['sha256'],str(p)
    if 'bytes' in row:assert len(raw)==row['bytes'],str(p)
    v[str(p)]={'path':str(p),'sha256':h,'bytes':len(raw)};return raw
def refs(d):
    if isinstance(d,dict):
        if isinstance(d.get('path'),str) and len(str(d.get('sha256','')))==64:ck(d)
        for x in d.values():refs(x)
    elif isinstance(d,list):
        for x in d:refs(x)

F=E/'TLS_SOURCE_FREEZE_R1_3.json';f=json.loads(ck({'path':str(F),'sha256':'6d6ea3c20ff1904046a5ed16404458ffb36c8e80505cd95b9a444ee5a96c9532'}));refs(f)
parent=json.loads(ck({'path':str(E/'TLS_SOURCE_FREEZE_R1_2.json'),'sha256':'7234dc0c70436760837a69c55e212d1d41799636e4a354e0fbcd9e40eaafc111'}))
crypto=json.loads(ck({'path':str(E/'INTEL_CRYPTO_SOURCE_FREEZE_R1.json'),'sha256':'55e3b459e554c5c4475b3d2cf041ae7a202b7677cb70996f5b216e9084b2544f'}));cur={x['path']:x for x in f['source_files']};old={x['path']:x for x in crypto['source_files']};prior={x['path']:x for x in parent['source_files']}
assert len(cur)==23 and len(old)==17 and all(cur[k]==r for k,r in old.items())
changed=[k for k in cur if cur[k]!=prior[k]];assert {pathlib.Path(k).name for k in changed}=={'tls_successor_r1.py','test_tls_r1.py'}
for row in cur.values():ast.parse(ck(row),filename=row['path'])
current_test=ast.parse((E/'test_tls_r1.py').read_bytes());previous_test=ast.parse((E/'tls_r1_2_preservation_preimage/test_tls_r1.py').read_bytes())
def tests(tree):return {n.name:ast.dump(n,include_attributes=False) for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name.startswith('test_')}
a,b=tests(previous_test),tests(current_test);assert len(a)==14 and len(b)==16 and all(b[k]==r for k,r in a.items())
sys.path.insert(0,str(E));unit=importlib.import_module('tls_successor_r1');deps=importlib.import_module('intel_deps_stages');crypto_unit=importlib.import_module('intel_crypto_successor_r1');binary=importlib.import_module('tls_binary_r1')
assert len(f['primary_refs'])==108
for row in f['source_files']+f['primary_refs']:
 p=pathlib.Path(row['path']);p=p if p.is_absolute() else B/p
 actual=unit.frozen_digest(p);assert actual['bytes']==row['bytes'] and actual['sha256']==row['sha256']
for key,count in [('test_raw_result',11),('test_raw_result_r1_1',2),('test_raw_result_r1_2',1),('test_raw_result_r1_3',2)]:
 r=f[key];report=json.loads(ck(r));assert report['actual_wait_rc']==0
 for name in ('stdout','stderr','argv.json','spawn.json'):
  p=pathlib.Path(r['path']).parent/name
  assert p.exists(),str(p)
 latest=json.loads(ck(f['test_raw_result_r1_3']));assert latest['junit_counts']=={'tests':2,'errors':0,'failures':0,'skipped':0} and latest['native_build_commands']==0 and latest['original_first_cause_retained'] is True and latest['preservation_false_survives_cap_and_cleanup'] is True
import xml.etree.ElementTree as ET
j=pathlib.Path(f['test_raw_result_r1_3']['path']).parent/'junit.xml';cases=ET.fromstring(j.read_bytes()).findall('.//testcase');assert len(cases)==2 and all(not any(c.find(t) is not None for t in ('failure','error','skipped')) for c in cases)
assert unit.UNIT_CAP==1800 and unit.RESERVE==300 and not unit.layout()['root'].exists()
oldroot=unit.PREFIX/'intel-deps-r2';history=unit.verify_old_unit_tree(oldroot);assert history['tree_records']==13588
prepare=deps.prior_stage(oldroot,'prepare');deps.prior_stage(oldroot,'rust');openssl=deps.prior_stage(oldroot,'openssl');unit.verify_unchanged_inputs(oldroot,prepare,after_openssl=True)
archives=[]
for item in openssl['target_binary_evidence']:
 p=pathlib.Path(item['path']);record=unit.record(p);raw,_=crypto_unit.checkpoint_bytes(p,record);parsed=binary.static_archive(raw,'x86_64')
 assert parsed['object_count']==item['object_count'] and parsed['archive_sha256']==item['archive_sha256'] and parsed['archive_bytes']==item['archive_bytes'];archives.append({'ref':record,'objects':parsed['object_count']})
assert sum(x['objects'] for x in archives)==1107
from security_snapshot._host_scoped_provider import verify_private_path_security
prereq=[]
for name in ('cpython-universal2','cpython-universal2-build','intel-crypto-r3'):
 p=unit.PREFIX/name;verify_private_path_security(p,directory=True);rows=deps.tree_record(p);assert rows
 prereq.append({'path':str(p),'records':len(rows),'regular_bytes':sum(r.get('bytes',0) for r in rows)})
actual=json.loads(ck({'path':str(E/'ROOT_INTEL_CRYPTO_ACTUAL_CHECK_R1.json'),'sha256':'f48e3a867ff693c3d637ffdae22e051f53a0074c04a95128514e173566c35ba0'}));assert actual['status']=='PASS_SCOPED_ACTUAL_TARGET_WHEEL_BUILD_BYTES_AND_OWNED_CLOSURE'
plan,env=unit.command_plan(unit.layout());assert len(plan)==10 and all(not any(x in argv for x in ('--host','PY_UNSUPPORTED_OPENSSL_BUILD=static')) for _,argv,_ in plan)
assert env['MACOSX_DEPLOYMENT_TARGET']=='11.0' and 'PY_UNSUPPORTED_OPENSSL_BUILD' not in env and 'DYLD_LIBRARY_PATH' not in env
requirements=json.loads(ck({'path':str(E/'TLS_PRODUCT_MODULE_REQUIREMENTS_R1.json'),'sha256':'8b773b9857ca1250adba4cff69d738fb2334050aae97ac43a83cb6d9ef18a2fc'}));assert requirements['missing_installed_metadata']==[]
payload={'schema':'ROOT_TLS_SOURCE_REVIEW/1.3','created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'PASS_SCOPED_WHOLE_TLS_SOURCE_AND_ACTUAL_PREREQUISITES','freeze':v[str(F)],'verified_references':list(v.values()),'source_closure':{'sources':23,'original_crypto17_byte_unchanged':True,'added_TLS':6,'R1_2_to_R1_3_changed':changed,'remaining21_unchanged':True,'AST':23},'actual_prerequisites':{'old_failed_namespace':history,'old_x86_static_archives':archives,'objects_x86_min11':1107,'existing_roots':prereq,'actual_previous_crypto_root_review':v[str(E/'ROOT_INTEL_CRYPTO_ACTUAL_CHECK_R1.json')]},'test_inheritance':{'existing14_method_AST_unchanged':True,'affected_production_terminal_controls':2,'latest_JUnit':'2_PASS_0_FAILURE_ERROR_SKIP','distinct_controls':16,'no_redundant_existing_group_rerun':True},'source_review':['One fresh owned TLS namespace; all original CPython/install/build/crypto/deps trees before and after must retain actual identities/mode/owner/nlink/bytes; original failures unchanged.','Official fresh ARM OpenSSL darwin64-arm64 no-shared/no-apps/no-tests -j2, exact macOS11; all installed header literal same-FD bytes must equal successful original x86 header tree.','Two actual official Apple lipo calls run under original observed make group; child argv/raw/wait retained and each complete fat slice must equal original thin archive raw bytes; every object exactCPU/minOS11. No fabricated child birth or fat writer.','Normal official CPython universal2 configure with verified fat static-only prefix and openssl-rpath=no; unsupported GNU static override and Homebrew/system/network fallback absent.','Real fresh ARM-selected Python3.12.13 SSL4.0.2/hashlib known-vector and original product imports plus actual dualCPU SSL/hashlib modules/systemdylibs and full install tree required; no x86 hardware/CP39/free-threaded acceptance.','One original reviewed Popen runner; first FAIL/UNKNOWN stops nextcommand, holds samehandle, no kill/relaunch; wholeunit1800 includes300 cleanup with stage wait recalculated after all preparation/permanentguard.','Root R1_2 substantive finding preservationFAIL fact was incorrectly recomputed after status rewrites; actual production terminal outcome now keeps independent verified bool/full originalcause in RESULT/CLOSED and separate failure receipt. Two real fixture-tree controls prove False cannot flip under later cap/diagnostic failure.'],'admission':{'status':'APPROVED_ONCE_FRESH_TLS_SUCCESSOR','runner':cur[str((E/'tls_successor_r1.py').relative_to(B))],'namespace':str(unit.layout()['root']),'outer_command_count':10,'explicit_lipo_child_commands':2,'unit_seconds':1800,'cleanup_reserve_seconds':300,'preparation_subcap_seconds':600,'network_planned':0,'system_install':0,'pure_x86_runtime':0,'COMSOL_GUI_Study':0,'required':'Rehash current23source/108primary/currentRootreview; original crypto exactPID absence; old whole tree/actual prerequisites; namespace absent; original permanent guard/raw/Popen/birth/group and first failure stops downstream; all actual acceptance/ownedcleanup incl time and original trees.'},'actual_TLS_native':'NOT_RUN','lzma':'REQUIRED_OPEN_WITH_INDEPENDENT_OFFICIAL_INPUT_PREPARED','complete_Intel_runtime':'NOT_ACCEPTED','hardware':'NOT_RUN','scientific':'NOT_RUN','final_delivery':'NOT_RUN','Goal':'ACTIVE'}
p=E/'ROOT_TLS_SOURCE_REVIEW_R1_3.json';raw=(json.dumps(payload,indent=2,sort_keys=True)+'\n').encode();fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
try:os.write(fd,raw);os.fsync(fd)
finally:os.close(fd)
print(json.dumps({'path':str(p),'sha256':hashlib.sha256(raw).hexdigest(),'refs':len(v),'sources':23,'existing_root_records':prereq,'status':payload['status']}))
