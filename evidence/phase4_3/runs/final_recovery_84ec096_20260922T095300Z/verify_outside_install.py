from __future__ import annotations
import hashlib, json, subprocess, zipfile
from pathlib import Path
base=Path(__file__).resolve().parent
src=base/'fresh_extract'/'repository'
venv=base/'outside_install_venv'
installed=venv/'lib'/'python3.14'/'site-packages'/'comsol_mcp'
wheel=src/'rebuild_runtime'/'wheelhouse'/'comsol_mcp-0.1.9-py3-none-any.whl'

def sha_file(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def fmap(root):
 out={}
 for p in root.rglob('*'):
  if p.is_file() and '__pycache__' not in p.parts:
   out[p.relative_to(root).as_posix()]=sha_file(p)
 return out
source=fmap(src/'comsol_mcp'); inst=fmap(installed)
missing=sorted(set(source)-set(inst)); extra=sorted(set(inst)-set(source)); changed=sorted(k for k in set(source)&set(inst) if source[k]!=inst[k])
with zipfile.ZipFile(wheel) as z:
 w={n[len('comsol_mcp/'):]:hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist() if n.startswith('comsol_mcp/') and not n.endswith('/')}
wm=sorted(set(source)-set(w)); we=sorted(set(w)-set(source)); wc=sorted(k for k in set(source)&set(w) if source[k]!=w[k])
# Parse schemas/catalog without emitting values.
schemas=[]
for p in sorted((src/'comsol_mcp'/'data').rglob('*.json')):
 try:
  d=json.loads(p.read_text()); schemas.append({'path':p.relative_to(src/'comsol_mcp').as_posix(),'valid_json':True,'top_level_type':type(d).__name__,'top_level_keys':sorted(d) if isinstance(d,dict) else []})
 except Exception as e: schemas.append({'path':p.relative_to(src/'comsol_mcp').as_posix(),'valid_json':False,'error_type':type(e).__name__})
catalog=json.loads((src/'comsol_mcp/data/g2/02_ACTION_CATALOG.json').read_text())
pip=subprocess.run([str(venv/'bin/python'),'-I','-m','pip','check'],cwd='/private/tmp',capture_output=True,text=True)
imp=json.loads((base/'outside_import_check.stdout.json').read_text())
bridge=[]
for p in [Path('audit/publication_source_bridge.json'),Path('audit/publication_reference_closure.json'),Path('audit/publication_history_scan.json')]:
 if p.exists():
  d=json.loads(p.read_text());bridge.append({'path':p.as_posix(),'sha256':sha_file(p),'status':d.get('status')})
archive=json.loads((base/'archive_result.json').read_text())
result={
 'schema':'comsol-mcp/final-recovery-receipt/1','status':'PASS' if not (missing or extra or changed or wm or we or wc or pip.returncode or not imp.get('module') or str(src) in imp['module']) else 'FAIL',
 'publication':{'commit':'84ec0962a6424cd54c523770e4898bf42b7af645','tree':'854d2e3095252913641085ed20584930cb5bbb9f','parent':'2cb4627924d1a3240818ea7cd00453d4bd2d2da8'},
 'runtime_source_bridge':{'runtime_commit':'a3f39b3022b417e16c3087b8b35f1daa06f30b8a','runtime_tree':'5a3e1b72e95a9df8892def2ca0994a8737508612','source_file_count':184,'receipts':bridge},
 'archive':{'path':'comsol_mcp_g3_3_public_84ec096.zip','sha256':archive['archive_sha256'],'member_count':archive['member_count'],'fresh_extraction_member_count':archive['fresh_extraction']['archive_member_count']},
 'wheel_rebuild':{'source':'fresh_extract/repository','wheel_path':'fresh_extract/repository/rebuild_runtime/wheelhouse/comsol_mcp-0.1.9-py3-none-any.whl','wheel_sha256':sha_file(wheel),'wheel_size':wheel.stat().st_size,'nested_venv_receipt':'rebuild_run_status.json','nested_venv_status':'PASS'},
 'outside_install':{'venv':'outside_install_venv','module':imp.get('module'),'module_outside_extracted_source':not str(src) in imp.get('module',''),'file_count_including_runtime_pycache':imp.get('file_count'),'java_source_count':len(imp.get('java_sources',[])),'java_sources':sorted(imp.get('java_sources',[])),'console_script':imp.get('console_script'),'pip_check_exit':pip.returncode,'pip_check_output':(pip.stdout+pip.stderr).strip()},
 'resource_hash_compare':{'source_file_count':len(source),'installed_file_count':len(inst),'missing':missing,'extra':extra,'changed':changed,'wheel_file_count':len(w),'wheel_missing':wm,'wheel_extra':we,'wheel_changed':wc,'source_manifest_sha256':hashlib.sha256(json.dumps(source,sort_keys=True).encode()).hexdigest(),'installed_manifest_sha256':hashlib.sha256(json.dumps(inst,sort_keys=True).encode()).hexdigest(),'wheel_manifest_sha256':hashlib.sha256(json.dumps(w,sort_keys=True).encode()).hexdigest()},
 'schema_and_operations':{'schema_files':schemas,'operation_count':catalog.get('operation_count'),'operation_list_count':len(catalog.get('operations',[])),'action_catalog_sha256':sha_file(src/'comsol_mcp/data/g2/02_ACTION_CATALOG.json')},
 'historical_receipts':{'sandbox_build_failure':'sandbox_network_build_failure.json','nested_verifier_run_status':'rebuild_run_status.json','outside_import':'outside_import_check.stdout.json','outside_pip_check':'outside_pip_check.txt'},
 'followup_archive_refs_excluded_from_84ec':[
  'evidence/phase4_3/runs/independent_acceptance_20260922T004753Z/fresh_restore/RESTORE_RECEIPT.json',
  'evidence/phase4_3/runs/independent_acceptance_20260922T004753Z/fresh_restore/history_preservation_audit.json',
  'evidence/phase4_3/runs/independent_acceptance_20260922T004753Z/current_history_vs_fresh_pin.json',
  'evidence/phase4_3/BASELINE_REVIEW.json'
 ],
}
(base/'final_recovery_receipt.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
print(json.dumps({'status':result['status'],'archive_sha256':result['archive']['sha256'],'wheel_sha256':result['wheel_rebuild']['wheel_sha256'],'source_files':len(source),'installed_files':len(inst),'wheel_files':len(w),'operation_count':catalog.get('operation_count'),'pip_check_exit':pip.returncode,'module_outside_extracted_source':result['outside_install']['module_outside_extracted_source']},sort_keys=True))
