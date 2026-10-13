"""Whole actual main positive and all required failures; public boundary only."""
import hashlib,json
from pathlib import Path
import pytest
import openssl359_runtime_profile_r3 as producer
import openssl359_runtime_verify_r2 as verifier
from test_openssl359_runtime_producer_r2 import public_main_boundary
from intel_crypto_successor_r1 import checkpoint_bytes
OUT=Path(__file__).parent
def save(name,value):(OUT/name).write_text(json.dumps(value,indent=2)+'\n')
def test_actual_main_success_after_global_import_repair_first_output_pin(monkeypatch,tmp_path,capsys):
 installed,output,trace=public_main_boundary(monkeypatch,tmp_path)
 original=producer.importlib.import_module;attempts=[]
 def actual_host_import(name,*args,**kwargs):
  attempts.append(name);return original(name,*args,**kwargs)
 monkeypatch.setattr(producer.importlib,'import_module',actual_host_import)
 assert 'importlib' not in producer.main.__code__.co_varnames
 path=output/'ARM_RUNTIME_PROFILE.json';producer.main(path)
 assert attempts==['zlib','sqlite3','ctypes','venv','_lzma','_gdbm','_tkinter']
 boundaries=[json.loads(line) for line in trace.read_text().splitlines()]
 assert boundaries==[{'boundary':'runtime_cert_cases','argument':str(installed)},{'boundary':'actual_memorybio_cases','argument':str(installed/'runtime-certfixtures/keycert.pem')}]
 first,profile=verifier.consume_profile(output)
 assert profile['provider_info']==[3,5,0,9,0] and profile['provider_number']==0x30500090
 assert all(profile['modules'][name]['status']=='IMPORTED' for name in ('zlib','sqlite3','ctypes','venv'))
 assert profile['sha256_known_vector'] and profile['ssl_context_created'] and path.stat().st_mode&0o777==0o600
 assert first['sha256']==hashlib.sha256(path.read_bytes()).hexdigest()
 stdout=capsys.readouterr().out;assert 'ACTUAL_PROVIDER_FIRST_READ' in stdout
 raw=path.read_bytes();replacement=output/'public-same-byte-replacement';replacement.write_bytes(raw);replacement.chmod(0o600);replacement.replace(path)
 with pytest.raises(Exception):checkpoint_bytes(path,first)
 save('ACTUAL_PRODUCER_MAIN_AND_FIRST_PROFILE_PIN.json',{'status':'PASS_SCOPED_SOURCE_SOFTWARE','producer':producer.__file__,'actual_host_import_attempts':attempts,
  'global_importlib_binding':'NO_UNBOUND_LOCAL','actual_public_cert_BIO_boundaries':boundaries,'original_profile_first_record':first,
  'scope':'Actual main and firstsameFD consumer; identity/provider/cert/BIO public boundary spies, actual host knownvectors/TLSContext/required imports. This produced only synthetic private profile, no selected target runtime.',
  'profile_before_public_swap':profile,'same_byte_new_inode_after_acceptance':'REFUSED_ORIGINAL_FIRST_RECORD','stdout':stdout,'new_targetPython_compile_COMSOL_OSproof':0})
def test_each_genuine_required_import_failure_refuses_before_certs_BIO_output(monkeypatch,tmp_path):
 original=producer.importlib.import_module;rows=[]
 for target in ('zlib','sqlite3','ctypes','venv'):
  folder=tmp_path/target;folder.mkdir()
  with monkeypatch.context() as patch:
   installed,output,trace=public_main_boundary(patch,folder);attempts=[]
   def fail_one(name,*args,**kwargs):
    attempts.append(name)
    if name==target:raise ImportError('public genuine required import failure:'+name)
    return original(name,*args,**kwargs)
   patch.setattr(producer.importlib,'import_module',fail_one)
   with pytest.raises(RuntimeError,match='regressed: '+target) as exc:producer.main(output/'ARM_RUNTIME_PROFILE.json')
   assert attempts==['zlib','sqlite3','ctypes','venv','_lzma','_gdbm','_tkinter']
   assert not trace.exists() and not (output/'ARM_RUNTIME_PROFILE.json').exists()
   rows.append({'required':target,'actual_terminal':str(exc.value),'all7_imports_attempted':attempts,'certificates':'NOT_REACHED','MemoryBIO':'NOT_REACHED','profile':'NOT_CREATED'})
 save('ACTUAL_MAIN_GENUINE_REQUIRED_IMPORT_FAILURES.json',{'status':'PASS_SCOPED_SOURCE_SOFTWARE','cases':rows,'native_runtime':False,'no_false_runtime_profile_success':True})
