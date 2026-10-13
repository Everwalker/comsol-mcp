"""Two concentrated actual-production source controls, host/SDK boundaries only."""
import json,os,io,lzma
from pathlib import Path
import pytest
from acquire_exact_archives import Refusal
import liblzma_runtime_profile_r1 as producer
import liblzma_verify_r1 as consumer
import liblzma_successor_r1 as actor
import liblzma_build_gate_r1 as gate
from test_liblzma_producer_r1 import public_main_boundary
from test_liblzma_unit_r1 import build_fixture

OUT=Path(__file__).parent
def saved(name,data):
 fd=os.open(OUT/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 try:os.write(fd,(json.dumps(data,indent=2)+'\n').encode());os.fsync(fd)
 finally:os.close(fd)

def test_actual_main_host_lzma_tarxz_and_failclosed_first_profile(monkeypatch,tmp_path,capsys):
 records=[]
 for mode in ('success','required_import','lzma_wrong_data','tarxz_wrong_data'):
  folder=tmp_path/mode;folder.mkdir()
  with monkeypatch.context() as m:
   installed,output,trace=public_main_boundary(m,folder)
   path=output/'ARM_RUNTIME_PROFILE.json'
   if mode=='required_import':
    original=producer.importlib.import_module
    def missing(name,*a,**kw):
     if name=='_lzma':raise ImportError('public synthetic genuine required import error')
     return original(name,*a,**kw)
    m.setattr(producer.importlib,'import_module',missing)
   if mode=='lzma_wrong_data':m.setattr(lzma,'decompress',lambda *a,**kw:b'wrong public payload')
   if mode=='tarxz_wrong_data':
    import tarfile
    original_extract=tarfile.TarFile.extractfile
    def wrong_extract(self,name):
     if name=='public-product-fixture.txt':return io.BytesIO(b'wrong public tar.xz payload')
     return original_extract(self,name)
    m.setattr(tarfile.TarFile,'extractfile',wrong_extract)
   if mode!='success':
    match={'required_import':'regressed: _lzma','lzma_wrong_data':'LZMA roundtrip','tarxz_wrong_data':'tar.xz roundtrip'}[mode]
    with pytest.raises(RuntimeError,match=match):producer.main(str(path))
    assert not path.exists()
    if mode=='required_import':assert not trace.exists()
    records.append({'mode':mode,'production_main_refused':True,'profile_created':False,'cert_BIO_boundary_called':trace.exists()})
    continue
   producer.main(str(path));first,profile=consumer.consume_profile(output)
   assert profile['actual_lzma_roundtrip'] and profile['actual_tar_xz_product_roundtrip']
   assert profile['modules']['_lzma']['status']=='IMPORTED'
   assert profile['actual_certificate_cases']['actual_backend']=='PUBLIC_BOUNDARY_SPY_NON_TARGET'
   assert 'ACTUAL_PROVIDER_FIRST_READ' in capsys.readouterr().out
   raw=path.read_bytes();path.unlink();path.write_bytes(raw);path.chmod(0o600)
   with pytest.raises(Refusal):consumer.checkpoint_bytes(path,first)
   changed=dict(profile,actual_tar_xz_product_roundtrip=False)
   with pytest.raises(RuntimeError,match='consumer evidence absent'):consumer.validate_profile(changed)
   records.append({'mode':mode,'actual_host_lzma_and_tarxz_roundtrips':True,'actual_main_and_firstrecord_consumer':True,'same_bytes_new_inode_refused':True,'missing_roundtrip_refused':True,'target_runtime':False,'provider_identity_cert_BIO':'PUBLIC_BOUNDARY_SPIES_ONLY'})
 saved('ACTUAL_PRODUCER_MAIN_HOST_AND_NEGATIVES.json',records)

def test_actual_five_record_preinstall_and_four_binary_decoder_controls(monkeypatch,tmp_path):
 records=[]
 for mode in ('success','_lzma_bytes','_lzma_inode','helperERROR','expired_budget'):
  folder=tmp_path/mode;folder.mkdir();x,command,leaf=build_fixture(folder)
  calls=[]
  with monkeypatch.context() as m:
   m.setattr(actor.cp,'run',lambda *a,**kw:(calls.append({'args':str(a),'timeout':kw['timeout']}) or {'public_sdk_spy':'INSTALL_NOT_RUN'}))
   if mode=='helperERROR':
    (x['logs']/command['stderr']).write_bytes(b'[ERROR] _lzma (ImportError: public fixture)\n')
    with pytest.raises(Refusal,match='helper required module ERROR'):gate.required_build_outputs(x,command)
    assert calls==[];records.append({'mode':mode,'install_calls':0});continue
   report=gate.required_build_outputs(x,command)
   assert set(report['first_regular_records'])=={'Makefile','python','_ssl','_hashlib','_lzma'}
   accepted=gate.capture_build_checkpoint(folder/'REQUIRED_BUILD_OUTPUTS_VERIFIED.json',report)
   if mode=='_lzma_bytes':
    raw=leaf.read_bytes();st=leaf.stat();leaf.write_bytes(raw[:-1]+bytes([raw[-1]^1]));os.utime(leaf,ns=(st.st_atime_ns,st.st_mtime_ns))
   if mode=='_lzma_inode':
    raw=leaf.read_bytes();modebits=leaf.stat().st_mode&0o777;leaf.unlink();leaf.write_bytes(raw);leaf.chmod(modebits)
   if mode=='expired_budget':
    now=[0.];budget=actor.Budget(clock=lambda:now[0]);now[0]=1501.
   else:budget=actor.Budget()
   if mode=='success':
    value=actor.run_install_with_original_acceptance(x,accepted,None,['/usr/bin/make','altinstall'],x['build'],{},budget,actor.time.monotonic())
    assert value['public_sdk_spy']=='INSTALL_NOT_RUN' and len(calls)==1
   else:
    with pytest.raises(Refusal):actor.run_install_with_original_acceptance(x,accepted,None,['/usr/bin/make','altinstall'],x['build'],{},budget,actor.time.monotonic())
    assert calls==[]
   records.append({'mode':mode,'install_calls':len(calls),'install_effect':'SDK_BOUNDARY_SPY_ONLY','all_five_first_records':True})
 # Actual Mach-O bytes decoded, never executed; public copied fixture uses the
 # already-read _ssl binary as a structural _lzma stand-in, not real liblzma.
 (tmp_path/'binaries').mkdir();x,command,leaf=build_fixture(tmp_path/'binaries')
 install=tmp_path/'public-install';(install/'bin').mkdir(parents=True);dyn=install/'lib/python3.12/lib-dynload';dyn.mkdir(parents=True)
 (install/'bin/python3.12').write_bytes((x['build']/'python.exe').read_bytes())
 raw=leaf.read_bytes()
 for name in ('_ssl','_hashlib','_lzma'):(dyn/(name+'.cpython-312-darwin.so')).write_bytes(raw)
 parsed=actor.actual_binaries({'install':install});assert set(parsed)=={'python','_ssl','_hashlib','_lzma'}
 old=b'/usr/lib/libSystem.B.dylib\0';new=b'/usr/lib/liblzma.5.dylib\0';assert old in raw
 (dyn/'_lzma.cpython-312-darwin.so').write_bytes(raw.replace(old,new+b'\0'*(len(old)-len(new))))
 with pytest.raises(Refusal,match='dynamic system/foreign fallback'):actor.actual_binaries({'install':install})
 records.append({'mode':'four_binary_actual_decoder','four_dual_CPU_min11':True,'_lzma_system_dylib_refused':True,'real_liblzma_target':False,'hardware_execution':False})
 saved('ACTUAL_PREINSTALL_FIRST_RECORD_AND_BINARY_NEGATIVES.json',records)
