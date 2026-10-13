import json,ast,os,hashlib,difflib
from pathlib import Path
from strict_derived import read_regular
D=Path(__file__).parent;R=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');A=D.parent/'w26-helper-successor-r1';E=R/'repository/docs/full_project_continuation_v2/evidence/intel-builder-execution-r1';M=Path('/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T/comsol-w26-wheel048-metadata-r1-46ehevk6')
old=json.loads(read_regular(A/'FREEZE_R1.json')[0]);preserved=[]
for q in old['files']:
 _,now=read_regular(Path(q['path']));assert now==q;preserved.append(q)
assert (A/'full_project_release_original.py').read_bytes()==(R/'repository/tools/full_project_release.py').read_bytes()
assert 'Ran 22 tests' in (D/'TEST.stderr').read_text() and (D/'TEST.stderr').read_text().rstrip().endswith('OK')
assert 'Ran 3 tests' in (D/'DOWNLOADER_TEST.stderr').read_text() and (D/'DOWNLOADER_TEST.stderr').read_text().rstrip().endswith('OK')
assert not (D/'EXACT32_ONCE_GUARD.json').exists()
source=[p for p in sorted(D.glob('*.py'))]
for p in source:ast.parse(p.read_bytes(),filename=str(p))
for n in ['strict_derived.py','availability.py','test_successor.py']:
 (D/(n+'.diff')).write_text(''.join(difflib.unified_diff((A/n).read_text().splitlines(True),(D/n).read_text().splitlines(True),fromfile='immutable R1/'+n,tofile='R2/'+n)))
manifest=read_regular(D/'EXACT32_DOWNLOAD_MANIFEST.json')[1]
argv=['/private/tmp/comsol-v2-runtime-20261011-new/comsol-mcp-recovery-runtime-20261004-001/python312/bin/python3','-B','-u',str(D.resolve()/'acquire_exact32.py'),'--execute-download','--approved-manifest-sha256',manifest['sha256']]
plan={'status':'SOURCE_PREPARED_NOT_DOWNLOAD_ADMISSION','exact_actor_argv':argv,'aggregate_files':32,'aggregate_bytes':21090946,'batch_cap_seconds':600,'cleanup_reserve':15,'per_transport_timeout_seconds':90,'retries':0,'first_failure':'STOP preserve originalpartial and all durable receipts; no newprefix blindrefetch','wrapper':'Original normal-managed immutable source/root/source pins+realPID/birth/session/stdout/stderr/wait/elapsed/close capture; concrete W identity not prepared/launched by this packet. Must bind Root actual once receipt and exact wrapper before execute.','effects':{'runtimewheelGETs':0,'target_python':0,'pip':0,'native':0,'public_metadata_GETs':1}}
(D/'NEXT_ACQUISITION_PLAN.json').write_text(json.dumps(plan,indent=2,sort_keys=True)+'\n')
refs=[read_regular(p)[1] for p in [A/'FREEZE_R1.json',R/'local-preservation/root-w26-helper-successor-r1-control/RECEIPT.json',R/'repository/uv.lock',R/'repository/tools/full_project_release.py',E/'acquire_exact_archives.py']+list(sorted(M.iterdir()))]
accepted=json.loads((D/'ACCEPTED_CONTENT.json').read_bytes());refs+=accepted['basis_refs'];refs+=json.loads((D/'ORIGIN_BINDING.json').read_bytes())['verified_inputs']
for q in refs:
 assert read_regular(Path(q['path']))[1]==q
files=[read_regular(p)[1] for p in sorted(D.iterdir()) if p.is_file() and p.name!='FREEZE_R2.json']
result={'schema':'comsol-w26-private-helper-freeze/2','status':'SOURCE_SOFTWARE_PREPARED_NOT_DOWNLOAD_OR_INSTALL_ADMISSION','source_AST':[str(p) for p in source],'files':files,'references':refs,'preserved_original_R1_files':preserved,'helper_controls':22,'download_preparation_controls':3,'original_helper_lock_raw_unchanged':True,'scope':plan['effects'],'acquisition_plan':read_regular(D/'NEXT_ACQUISITION_PLAN.json')[1]}
p=D/'FREEZE_R2.json'
with p.open('xb') as f:f.write((json.dumps(result,indent=2,sort_keys=True)+'\n').encode());f.flush();os.fsync(f.fileno())
print(read_regular(p)[1]);print('manifest',manifest)
