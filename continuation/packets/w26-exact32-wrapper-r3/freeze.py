import json,ast,os,hashlib,difflib,sys
from pathlib import Path
D=Path(__file__).parent;R=D.parents[1];A=D.parent/'w26-helper-successor-r2';W=Path((D/'W_PATH.txt').read_text().strip());sys.path.insert(0,str(A));from strict_derived import read_regular
for p in list(D.glob('*.py'))+list(W.glob('*.py')):ast.parse(p.read_bytes(),filename=str(p))
for n in ['WRAPPER_ONCE_GUARD.json','EXACT32_ONCE_GUARD.json','PREFIX_CREATED.json','REQUEST.json','WRAPPER_BIRTH.json','TERMINAL.json']:assert not (W/n).exists()
assert list((W/'logs').iterdir())==[]
old=json.loads(read_regular(A/'FREEZE_R2.json')[0]);preserved=[]
for row in old['files']:
 assert read_regular(Path(row['path']))[1]==row;preserved.append(row)
metadata=read_regular(W/'METADATA.json')[1];m=json.loads(read_regular(W/'METADATA.json')[0]);assert m['actor_sha256']==read_regular(W/'actor.py')[1]['sha256']
for row in m['source_refs']:assert read_regular(Path(row['path']))[1]==row
assert 'Ran 5 tests' in (D/'TEST_FINAL5.stderr').read_text() and (D/'TEST_FINAL5.stderr').read_text().rstrip().endswith('OK')
(D/'ACTOR_R2_TO_R3.diff').write_text(''.join(difflib.unified_diff((A/'acquire_exact32.py').read_text().splitlines(True),(D/'actor_r3.py').read_text().splitlines(True),fromfile='immutable R2 actor',tofile='new R3 actor')))
review=R/'local-preservation/root-w26-helper-successor-r2-review/REVIEW_AND_NEXT_ADMISSION_DECISION.json'
result={'schema':'comsol-w26-exact32-wrapper-supplement/3','status':'SOURCE_ONLY_WAIT_EXACT_ROOT_ONCE_BINDING','work_directory':str(W),'metadata':metadata,'wrapper':read_regular(W/'wrapper.py')[1],'actor':read_regular(W/'actor.py')[1],'work_identity':m['work_identity'],'files':[read_regular(p)[1] for p in sorted(D.iterdir()) if p.is_file() and p.name!='FREEZE_R3.json'],'work_files':[read_regular(p)[1] for p in sorted(W.iterdir()) if p.is_file()],'references':m['source_refs']+[read_regular(review)[1],read_regular(A/'FREEZE_R2.json')[1],m['runtime_snapshot']],'original_R2_files_preserved':preserved,'controls':{'count':5,'status':'PASS_PREPARATION_NOT_NETWORK_NATIVE','raw':read_regular(D/'TEST_FINAL5.stderr')[1]},'effects':{'spawn':0,'new_guards':0,'new32GET':0,'targetPython':0,'pip':0,'COMSOL':0,'oldroot_write':0},'next_invocation_prefix':['/private/tmp/comsol-v2-runtime-20261011-new/comsol-mcp-recovery-runtime-20261004-001/python312/bin/python3','-B','-u',str(W/'wrapper.py'),'--binding-sha'],'last_argument':'actual fixed Root once receipt SHA, not yet supplied; no literal placeholder may be dispatched'}
p=D/'FREEZE_R3.json'
with p.open('xb') as f:f.write((json.dumps(result,indent=2,sort_keys=True)+'\n').encode());f.flush();os.fsync(f.fileno())
print(read_regular(p)[1]);print('wrapper',result['wrapper']);print('metadata',metadata);print('actor',result['actor'])
