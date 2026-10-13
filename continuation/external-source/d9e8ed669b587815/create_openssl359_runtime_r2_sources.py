from pathlib import Path
E=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository/docs/full_project_continuation_v2/evidence/intel-builder-execution-r1')
pairs=[('openssl359_runtime_profile_r2.py','openssl359_runtime_profile_r3.py'),('openssl359_runtime_successor_r1.py','openssl359_runtime_successor_r2.py'),('openssl359_runtime_verify_r1.py','openssl359_runtime_verify_r2.py')]
for old,new in pairs:
 text=(E/old).read_text()
 text=text.replace('intel-tls-openssl359-runtime-r1','intel-tls-openssl359-runtime-r2').replace('openssl359_runtime_profile_r2.py','openssl359_runtime_profile_r3.py').replace('openssl359_runtime_verify_r1.py','openssl359_runtime_verify_r2.py').replace('openssl359_runtime_successor_r1 import','openssl359_runtime_successor_r2 import').replace('openssl359_runtime_profile_r2 import','openssl359_runtime_profile_r3 import').replace('OPENSSL359_RUNTIME_SOURCE_FREEZE_R1.json','OPENSSL359_RUNTIME_SOURCE_FREEZE_R2.json').replace('ROOT_OPENSSL359_RUNTIME_SOURCE_REVIEW_R1.json','ROOT_OPENSSL359_RUNTIME_SOURCE_REVIEW_R2.json')
 if old.endswith('profile_r2.py'):
  assert text.count(' import importlib.util\n')==1
  text=text.replace(' import importlib.util\n','').replace('from pathlib import Path\n','from pathlib import Path\nimport importlib.util\n')
 with (E/new).open('x') as f:f.write(text)
