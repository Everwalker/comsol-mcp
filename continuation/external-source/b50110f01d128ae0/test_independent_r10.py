"""Actual reviewed validator and client-Jar reads, no native execution."""
import json,os,shutil
from pathlib import Path
import pytest
from tools import run_native_w23_saved_mph_diagnostic as runner

C=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/local-preservation/w23-saved-mph-science-readback-20261013-candidate20-r10')
F=C/'docs/full_project_execution/w23_overlap/evidence/native-run-20261013-018-saved-mph-zero-solve-candidate20-r10-final1/freeze.json'
O=Path(__file__).parent

def test_real_preflight_relative_persistent_classes_poison_and_restore(tmp_path):
 fixture=tmp_path/'public-frozen-material';fixture.mkdir();fp=fixture/'freeze.json'
 shutil.copyfile(F,fp);shutil.copyfile(F.parent/'source_manifest.json',fixture/'source_manifest.json')
 shutil.copytree(F.parent/'offline_gates/compile',fixture/'offline_gates/compile')
 digest=runner.sha256_file(fp)
 positive=runner._validate_freeze(fp,digest);material=runner.science._check_offline_receipts(fp,positive)
 assert sum(row['class_count'] for row in material['compiled_class_outputs'].values())==19
 assert all(Path(row['class_dir']).is_relative_to(fixture) for row in material['compiled_class_outputs'].values())
 leaf=fixture/'offline_gates/compile/persistent_worker/classes/comsol_mcp/worker_java/PersistentComsolWorker.class'
 first=leaf.read_bytes();assert first[:4]==b'\xca\xfe\xba\xbe'
 leaf.write_bytes(first[:-1]+bytes([first[-1]^1]))
 with pytest.raises(RuntimeError,match='frozen compiler output is missing or changed') as poisoned:runner._validate_freeze(fp,digest)
 leaf.write_bytes(first)
 assert runner._validate_freeze(fp,digest)==positive
 # A copied metadata-only successor must consume its actual parent, not the
 # successful original compile directory retained in receipt strings.
 metadata=tmp_path/'public-metadata-only';metadata.mkdir()
 shutil.copyfile(fp,metadata/'freeze.json');shutil.copyfile(fixture/'source_manifest.json',metadata/'source_manifest.json')
 with pytest.raises(RuntimeError,match='frozen compiler output is missing or changed') as missing:runner._validate_freeze(metadata/'freeze.json',digest)
 report={'status':'PASS_REAL_PRODUCTION_PREFLIGHT_POSITIVE_AND_CLASS_POISON_NEGATIVE','production_validate_freeze_mocked':False,'production_check_offline_receipts_mocked':False,'client_Jar_manifest_hashes_actual_readonly':True,'positive_class_count':19,'relative_parent':str(fixture),'poisoned_relative':'persistent_worker/classes/comsol_mcp/worker_java/PersistentComsolWorker.class','poison_reason':str(poisoned.value),'restored_original_bytes_accepted':True,'metadata_only_successor_reason':str(missing.value),'Server_Worker_targetPython_compiler_native_process':0,'approval_or_native_effect_handoff':0}
 fd=os.open(O/'ACTUAL_PREFLIGHT_CLASS_POISON_AND_RESTORATION.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 try:os.write(fd,(json.dumps(report,indent=2)+'\n').encode());os.fsync(fd)
 finally:os.close(fd)
