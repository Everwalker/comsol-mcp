import json,ast,hashlib,difflib,os
from pathlib import Path
from strict_derived import read_regular
D=Path(__file__).parent;R=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2')
E=R/'repository/docs/full_project_continuation_v2/evidence';ROOT_DECISION=R/'local-preservation/w26-runtime-consumer-plan-r1/ROOT_PRIVATE_HELPER_AND_WHEEL_AVAILABILITY_DECISION_R1.json'
current=['full_project_release.py','strict_derived.py','native_parser.py','test_successor.py','availability.py','freeze_packet.py']
for n in current:ast.parse((D/n).read_bytes(),filename=n)
assert (D/'full_project_release_original.py').read_bytes()==(R/'repository/tools/full_project_release.py').read_bytes()
raw=(D/'TEST_OVERLAY.stderr').read_text();assert 'Ran 17 tests' in raw and raw.rstrip().endswith('OK')
(D/'FINAL_HELPER_DIFF.patch').write_text(''.join(difflib.unified_diff((D/'full_project_release_original.py').read_text().splitlines(True),(D/'full_project_release.py').read_text().splitlines(True),fromfile='immutable original',tofile='private strict successor')))
binding=json.loads((D/'ORIGIN_BINDING.json').read_bytes())
refs=binding['verified_inputs']+[read_regular(p)[1] for p in [ROOT_DECISION,R/'repository/tools/full_project_release.py',R/'repository/uv.lock',R/'repository/pyproject.toml',E/'w25-native-delivery-preparation-r2/ARM64_RUNTIME_INSTALL_RECEIPT_R2.json',E/'w25-native-delivery-preparation-r2/ISOLATED_ARM64_RUNTIME_PLAN_R2.json',E/'release_data_lifecycle/release-delivery-plan-r1/download-plan.json',R/'local-preservation/w26-runtime-consumer-plan-r1/FREEZE_R1.json',E/'intel-builder-execution-r1/ROOT_LIBLZMA_R2_ACTUAL_CHECK.json']]
for q in refs:
 _,now=read_regular(Path(q['path']))
 assert now==q
files=[read_regular(p)[1] for p in sorted(D.iterdir()) if p.is_file() and p.name!='FREEZE_R1.json']
result={'schema':'comsol-w26-private-helper-freeze/1','status':'SOURCE_SOFTWARE_PREPARED_NOT_TARGET_ADMISSION','current_source_AST':current,'files':files,'original_source_unchanged':True,'root_decision_sha256':read_regular(ROOT_DECISION)[1]['sha256'],'references':refs,'focused_production_controls':{'count':17,'raw':read_regular(D/'TEST_OVERLAY.stderr')[1],'status':'PASS_SOFTWARE_AND_READONLY_BYTES_NOT_NATIVE'},'availability':read_regular(D/'AVAILABILITY_FINAL_R1.json')[1],'scope':{'product_floor':'macOS12.0','actual_crypto_wheel_floor':'macOS11.0','native_install_or_runtime':0,'network':0,'COMSOL':0,'old_frozen_source_writes':0,'progress_git_CAS_writes':0,'transitive_stdlib_acceptance':'NOT_CLAIMED'}}
p=D/'FREEZE_R1.json'
with p.open('xb') as f:f.write((json.dumps(result,indent=2,sort_keys=True)+'\n').encode());f.flush();os.fsync(f.fileno())
print(read_regular(p)[1])
