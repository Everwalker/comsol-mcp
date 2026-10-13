import json,hashlib,stat,re,shutil
from pathlib import Path
B=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');O=B/'local-preservation/github-main-sync-20261013-r1';S=O/'send-tree';F=O/'final-r1';F.mkdir(mode=0o700);rows=[]
def copy(source,dest,expected=None):
 p=Path(source);st=p.lstat();assert stat.S_ISREG(st.st_mode);raw=p.read_bytes();sha=hashlib.sha256(raw).hexdigest()
 if expected:assert len(raw)==expected['bytes'] and sha==expected['sha256'],str(p)
 q=S/dest;q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(raw);q.chmod(0o755 if st.st_mode&0o111 else 0o644);assert q.read_bytes()==raw
 rows.append({'source':str(p),'source_relative':str(p.relative_to(B)) if p.is_relative_to(B) else None,'published_path':dest,'bytes':len(raw),'sha256':sha,'role':'FINAL_DELTA_SOURCE_AND_STATUS_BYTES_NOT_NATIVE_ADMISSION'})
C=B/'local-preservation/root-source-checkpoint-main-pause-20261013';current=json.loads((C/'CURRENT_STATE.json').read_text());receipt=json.loads((C/'RECEIPT.json').read_text());assert hashlib.sha256((C/'CURRENT_STATE.json').read_bytes()).hexdigest()=='d1598ddaaaf9c46f44d44496eb7ba6c6ed90b437bff1c981d55f9fb8e61c5525';assert hashlib.sha256((C/'RECEIPT.json').read_bytes()).hexdigest()=='40f3792b404a7830e75a3295e4e8649546fe1afd711936fc13c8e7fc1fbde0f4'
for change in receipt['changes']:
 r=change['after'];p=Path(r['path']);dest=str(p.relative_to(B/'repository')) if p.is_relative_to(B/'repository') else 'continuation/checkpoint/WORKSPACE_PROGRESS.md';copy(p,dest,r)
assert (B/'PROGRESS.md').read_bytes()[:3739]==(B/'repository/PROGRESS.md').read_bytes()[:3739]
for p in (C/'CURRENT_STATE.json',C/'RECEIPT.json'):copy(p,'continuation/checkpoint/'+p.name)
for r in current['reviewed_materials']:
 p=Path(r['path'])
 if p.is_relative_to(B/'repository'):dest=str(p.relative_to(B/'repository'))
 elif p.is_relative_to(B/'local-preservation/w23-saved-mph-science-readback-20261013-candidate20-r15'):dest='continuation/candidates/w23-r15/'+str(p.relative_to(B/'local-preservation/w23-saved-mph-science-readback-20261013-candidate20-r15'))
 else:dest='continuation/packets/'+str(p.relative_to(B/'local-preservation'))
 copy(p,dest,r)
for name in ['root-activity022-r15-source-review','w23-activity022-r15-independent-source-review']:
 for p in (B/'local-preservation'/name).iterdir():
  if p.is_file() and not p.is_symlink() and p.suffix in {'.py','.json','.md','.xml','.jsonl','.bin'} and p.stat().st_size<=2*1024*1024:copy(p,'continuation/packets/'+name+'/'+p.name)
# Final user state is separate from immutable historical freeze statuses.
status={'schema':'publication-current-scoped-state/1','base':'0.1.9','W23_R15':'ROOT_AND_INDEPENDENT_PASS_SCOPED_SOURCE_SOFTWARE_FIXED_ENTRY','Activity022':'NOT_RUN','W26_offline_venv_R1':'ROOT_AND_INDEPENDENT_PASS_SOURCE_SOFTWARE; EIGHT_COMMANDS_NOT_RUN','runtime_identity':'UNFIXED_UNVERIFIED','W24':'HOLD','integration':'INDEPENDENT_CANDIDATES_NOT_INTEGRATED','final_project':'OPEN_INCOMPLETE_NOT_ACCEPTED','publication':'PENDING_EXACT_TREE_REVIEW_AND_NORMAL_MAIN_SYNC','after_verified_sync':'PAUSE_NO_AUTOMATIC_NEXT_PHASE','reviewed_materials':current['reviewed_materials']}
(S/'continuation/STATES.json').write_text(json.dumps(status,indent=2)+'\n');copy(S/'continuation/STATES.json','continuation/STATES.json')
p=S/'continuation/README.md';t=p.read_text();t=t.replace('source review PENDING, native NOT_RUN.','Root and Independent scoped source/software/fixed-entry PASS; Activity022 NOT_RUN.').replace('Root source-only PASS; independent review pending; target venv/pip/runtime execution NOT_RUN.','Root and Independent scoped source/software PASS; all eight target venv/pip/profile commands NOT_RUN.');t+='\n## Current round stop boundary\n\nSee checkpoint/CURRENT_STATE.json and STATES.json. After ordinary verified main synchronization, pause; do not dispatch Activity022, W26 dependency environments or any new scientific/native phase automatically. Runtime identity mismatch remains UNFIXED/UNVERIFIED; full W01–W26 completion and unified final acceptance remain OPEN. All three progress histories retain their original tails with the same current header.\n';p.write_text(t);copy(p,'continuation/README.md')
(F/'FINAL_DELTA_MANIFEST.json').write_text(json.dumps(rows,indent=2)+'\n');(F/'STAGE_DELTA.nul').write_bytes(b'\0'.join(r['published_path'].encode() for r in rows)+b'\0')
violations=[]
for r in rows:
 raw=(S/r['published_path']).read_bytes()
 if any(re.search(p,raw) for p in [rb'gh[pousr]_[A-Za-z0-9]{30,}',rb'AKIA[0-9A-Z]{16}',rb'(?m)^-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----$']):violations.append(r['published_path'])
assert not violations
(F/'FINAL_DELTA_CHECK.json').write_text(json.dumps({'status':'PASS_EXACT_FINAL_DELTA','delta_records':len(rows),'reviewed_materials_logically_included':11,'canonical_pages':5,'shared_header_bytes':3739,'secrets_signature_findings':violations,'native':0,'commit':0,'push':0,'old_provisional_unchanged':True},indent=2)+'\n')
print('finaldelta',len(rows),'11 reviewed logical records','5 pages shared header')
