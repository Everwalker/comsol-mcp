from pathlib import Path
E=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository/docs/full_project_continuation_v2/evidence/intel-builder-execution-r1')
names=['liblzma_successor','liblzma_prepare','liblzma_cpython_checkpoint','liblzma_merge','liblzma_runtime_profile','liblzma_verify','liblzma_acceptance']
for stem in names:
 text=(E/(stem+'_r1.py')).read_text().replace('intel-liblzma-r1','intel-liblzma-r2').replace('LIBLZMA_SOURCE_FREEZE_R1.json','LIBLZMA_SOURCE_FREEZE_R2.json').replace('ROOT_LIBLZMA_SOURCE_REVIEW_R1.json','ROOT_LIBLZMA_SOURCE_REVIEW_R2.json').replace('LIBLZMA_R1_ONCE_GUARD.json','LIBLZMA_R2_ONCE_GUARD.json')
 for dependency in names:text=text.replace(dependency+'_r1',dependency+'_r2')
 (E/(stem+'_r2.py')).open('x').write(text)
