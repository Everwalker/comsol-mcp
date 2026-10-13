"""Actual runtime producer main, host identity/provider boundary fixtures only."""
import json,sys,hashlib,ssl,_hashlib
from pathlib import Path
from types import SimpleNamespace
import pytest
import openssl359_runtime_profile_r2 as producer
import openssl359_runtime_verify_r1 as consumer
from test_openssl359_runtime_r1 import sample
OUT=Path(__file__).parent
def save(name,value):(OUT/name).write_text(json.dumps(value,indent=2)+'\n')
def test_actual_producer_main_required_imports_unbound_before_certificates(monkeypatch,tmp_path,capsys):
 installed=tmp_path/'PUBLIC_BOUNDARY_INSTALLED';output_root=tmp_path/'PUBLIC_BOUNDARY_OUTPUT';output_root.mkdir(mode=0o700)
 prefix=installed/'cpython';filename=output_root/'ARM_RUNTIME_PROFILE.json'
 monkeypatch.setattr(producer,'INSTALLED',installed);monkeypatch.setattr(producer,'OUTPUT_ROOT',output_root)
 monkeypatch.setattr(producer,'platform',SimpleNamespace(machine=lambda:'arm64'))
 monkeypatch.setattr(producer,'sys',SimpleNamespace(prefix=str(prefix),executable=str(prefix/'bin/python3.12'),version_info=(3,12,13),version='PUBLIC_BOUNDARY_IDENTITY_ONLY'))
 # Exact desired fields are explicit synthetic boundary data, never native proof.
 monkeypatch.setattr(producer,'ssl',SimpleNamespace(__file__=str(prefix/'lib/python3.12/ssl.py'),OPENSSL_VERSION_INFO=(3,5,0,9,0),OPENSSL_VERSION_NUMBER=0x30500090,OPENSSL_VERSION='OpenSSL 3.5.9 29 Sep 2026',SSLContext=ssl.SSLContext,PROTOCOL_TLS_CLIENT=ssl.PROTOCOL_TLS_CLIENT,TLSVersion=ssl.TLSVersion))
 monkeypatch.setattr(producer,'hashlib',SimpleNamespace(__file__=str(prefix/'lib/python3.12/hashlib.py'),sha256=hashlib.sha256))
 monkeypatch.setattr(producer,'_hashlib',SimpleNamespace(__file__=str(prefix/'lib/python3.12/lib-dynload/_hashlib.cpython-312-darwin.so'),openssl_sha256=_hashlib.openssl_sha256))
 monkeypatch.setattr(producer,'_ssl',SimpleNamespace(__file__=str(prefix/'lib/python3.12/lib-dynload/_ssl.cpython-312-darwin.so')))
 observed={};exceptions=[];previous=sys.gettrace()
 def capture(frame,event,arg):
  if frame.f_code is producer.main.__code__:
   if event=='exception':
    kind,error,_=arg
    if isinstance(error,UnboundLocalError):exceptions.append({'type':kind.__name__,'cause':str(error),'line':frame.f_lineno})
    if isinstance(error,RuntimeError) and 'existing product runtime capability regressed' in str(error):
     observed['module_diagnostics']={k:dict(v) for k,v in frame.f_locals['modules'].items()};observed['terminal_error']=str(error)
   return capture
  return None
 try:
  sys.settrace(capture)
  with pytest.raises(RuntimeError,match='existing product runtime capability regressed: zlib'):producer.main(filename)
 finally:sys.settrace(previous)
 stdout=capsys.readouterr().out
 assert 'ACTUAL_PROVIDER_FIRST_READ' in stdout and not filename.exists()
 assert len(exceptions)==7 and all(row['type']=='UnboundLocalError' for row in observed['module_diagnostics'].values())
 assert set(observed['module_diagnostics'])=={'zlib','sqlite3','ctypes','venv','_lzma','_gdbm','_tkinter'}
 assert 'importlib' in producer.main.__code__.co_varnames
 # Actual host imports work in this interpreter; the producer never reaches them.
 import importlib
 host={name:bool(importlib.import_module(name)) for name in ('zlib','sqlite3','ctypes','venv')};assert all(host.values())
 save('ACTUAL_PRODUCER_MAIN_IMPORTLIB_LOCAL_RESIDUAL.json',{'status':'REPRODUCED_P2_SOURCE_SOFTWARE_ONLY','producer':str(Path(producer.__file__)),'function':'main',
  'frozen_main_importlib_is_function_local':True,'actual_caught_import_errors':exceptions,**observed,'stdout':stdout,
  'source_qualifiers':'Identity/version/provider/module file locations are public SDK/host boundary fixture values; actual host hashlib/_hashlib knownvector and SSLContext floor run, not fixed target359 runtime. No profile minted.',
  'actual_host_required_imports_work':host,'certificate_helpers':'NOT_REACHED','MemoryBIO_helpers':'NOT_REACHED','runtime_profile_JSON':'NOT_CREATED',
  'new_targetPython_newnamespace_COMSOL_native_effects':0})
def test_actual_profile_first_pin_consumer_positive_provider_drift_and_late_swap_refuses(monkeypatch,tmp_path):
 good=sample();assert consumer.validate_profile(good) is good
 changes=[]
 for index in range(5):
  row=sample();row['provider_info'][index]+=1
  with pytest.raises(RuntimeError,match='provider_info'):consumer.validate_profile(row)
  changes.append({'provider_tuple_index':index,'refused':True})
 for name,value in [('provider_number',0x30500091),('provider','OpenSSL 3.5.8 7 Apr 2026')]:
  row=sample();row[name]=value
  with pytest.raises(RuntimeError):consumer.validate_profile(row)
  changes.append({'provider_field':name,'refused':True})
 root=tmp_path/'public-output';root.mkdir(mode=0o700);monkeypatch.setattr(consumer,'ROOT',root)
 p=root/'ARM_RUNTIME_PROFILE.json';p.write_text(json.dumps(sample()));p.chmod(0o600)
 first,parsed=consumer.consume_profile(root);assert parsed['provider_info']==[3,5,0,9,0]
 # Actual first pinned profile record does not adopt identical bytes at a new inode.
 replacement=root/'public-replacement';replacement.write_bytes(p.read_bytes());replacement.chmod(0o600);replacement.replace(p)
 from intel_crypto_successor_r1 import checkpoint_bytes
 with pytest.raises(Exception):checkpoint_bytes(p,first)
 save('ACTUAL_PROVIDER_AND_FIRST_RECORD_CONSUMER_SCOPED_CONTROLS.json',{'status':'PASS_SCOPED_SOURCE_SOFTWARE_ONLY','provider_gate':changes,
  'consumer':'openssl359_runtime_verify_r1.consume_profile + original checkpoint_bytes','positive':'Explicit public synthetic profile; not native runtime','original_profile_record':first,'same_bytes_new_inode':'REFUSED_FIRST_IDENTITY_REPLACEMENT','target_runtime_native':0})
