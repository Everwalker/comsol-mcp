"""Actual R4 whole gate/first acceptance/consumer, public synthetic bytes only."""
from pathlib import Path
import sys,hashlib,json,os
import pytest
import openssl359_build_gate_r4 as gate
import openssl359_cpython_successor_r4 as unit
import openssl359_successor_r2 as r2
from acquire_exact_archives import Refusal
PRIOR=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/local-preservation/openssl359-cpython-r3-independent-source-review')
sys.path.insert(0,str(PRIOR))
from test_independent_r3 import fixture,NAMES,blob
OUT=Path(__file__).parent
def save(name,value):(OUT/name).write_text(json.dumps(value,indent=2)+'\n')
def replace_member(path,fixed,kind):
 changed=fixed.replace(b'PUBLIC ORIGINAL A',b'PUBLIC ORIGINAL B');assert len(changed)==len(fixed) and changed!=fixed
 first=path.stat()
 if kind=='new_inode':
  incoming=path.parent/(path.name+'.public-replacement');blob(incoming,changed);os.replace(incoming,path)
 else:path.write_bytes(changed);os.utime(path,ns=(first.st_atime_ns,first.st_mtime_ns))
 return {'original_inode':first.st_ino,'current_inode':path.stat().st_ino,'same_size':path.stat().st_size==first.st_size,'same_mtime':path.stat().st_mtime_ns==first.st_mtime_ns,'current_SHA':hashlib.sha256(path.read_bytes()).hexdigest()}
def accepted_fixture(folder):
 folder.mkdir();x,c,paths,values,view=fixture(folder);x['root']=folder
 report=gate.required_build_outputs(x,c)
 accepted=gate.capture_build_checkpoint(folder/'REQUIRED_BUILD_OUTPUTS_VERIFIED.json',report)
 return x,c,paths,values,view,report,accepted
def test_actual_whole_gate_first_regulars_fixed_and_original_R2_readonly_positive(monkeypatch,tmp_path):
 records=[]
 original=r2.layout();result=json.loads((original['root']/'RESULT.json').read_bytes());command=next(row for row in result['commands'] if row['argv']==['/usr/bin/make','-j2'])
 positive=gate.required_build_outputs(original,command)
 assert gate.recheck_original_acceptance(original,positive)['all_four_original_first_records_unchanged']
 own={'root':tmp_path,'build':original['build']};accepted=gate.capture_build_checkpoint(tmp_path/'REQUIRED_BUILD_OUTPUTS_VERIFIED.json',positive)
 assert gate.verify_preinstall_checkpoint(own,accepted)['all_four_original_first_records_unchanged']
 for member in NAMES:
  for kind in ('new_inode','same_inode_same_size_bytes'):
   folder=tmp_path/(member+'-'+kind);folder.mkdir();x,c,paths,values,view=fixture(folder)
   read=gate.regular_leaf;mutations=[]
   def mutate_after_all_first_reads(parent,name,path,*args,**kwargs):
    value=read(parent,name,path,*args,**kwargs)
    if name==NAMES['_hashlib'] and not mutations:mutations.append(replace_member(paths[member],values[member],kind))
    return value
   with monkeypatch.context() as patch:
    patch.setattr(gate,'regular_leaf',mutate_after_all_first_reads)
    with pytest.raises(Refusal,match='first accepted regular'):gate.required_build_outputs(x,c)
   assert len(mutations)==1
   records.append({'member':member,'mutation':kind,'whole_gate':'REFUSED_STALE_FIRST_RECORD',**mutations[0]})
 save('ACTUAL_WHOLE_GATE_FIRST_REGULAR_FIX_VERIFICATION.json',{'status':'PASS_SCOPED_SOURCE_SOFTWARE','actual_producer':'openssl359_build_gate_r4.required_build_outputs','actual_R2_readonly_positive':positive,'actual_R2_original_acceptance_recheck':'PASS_READONLY_BYTES','public_synthetic_cases':records,'real_fat_binary_parser':True,'target_compile_install_runtime':0})
def test_actual_install_consumer_first_tuple_refuses_changed_current_without_rebaseline(monkeypatch,tmp_path):
 records=[];dispatched=[]
 def sdk_boundary(*args,**kwargs):
  dispatched.append({'label':args[1],'argv':args[2],'cwd':str(args[3]),'timeout':kwargs['timeout']});return {'explicit_public_boundary':'NOT_NATIVE'}
 monkeypatch.setattr(unit.cp,'run',sdk_boundary)
 x,c,paths,values,view,report,accepted=accepted_fixture(tmp_path/'positive')
 assert all(isinstance(value,bytes) for value in accepted)
 actual=unit.run_install_with_original_acceptance(x,accepted,None,['/usr/bin/make','altinstall'],x['build'],{},unit.Budget(),unit.time.monotonic())
 assert actual['explicit_public_boundary']=='NOT_NATIVE' and len(dispatched)==1;positive=dispatched.pop()
 for member in NAMES:
  for kind in ('new_inode','same_inode_same_size_bytes'):
   x,c,paths,values,view,report,accepted=accepted_fixture(tmp_path/(member+'-'+kind))
   first_bytes=tuple(accepted);mutation=replace_member(paths[member],values[member],kind)
   with pytest.raises(Refusal,match='first accepted regular'):unit.run_install_with_original_acceptance(x,accepted,None,['/usr/bin/make','altinstall'],x['build'],{},unit.Budget(),unit.time.monotonic())
   assert not dispatched and accepted==first_bytes
   records.append({'member':member,'mutation':kind,'actual_consumer':'REFUSED_BEFORE_cp.run','install_dispatches':0,'immutable_actor_first_bytes_preserved':True,**mutation})
 # A same-inode same-size checkpoint content edit may not be readopted.
 x,c,paths,values,view,report,accepted=accepted_fixture(tmp_path/'checkpoint-bytes')
 p=x['root']/'REQUIRED_BUILD_OUTPUTS_VERIFIED.json';raw=p.read_bytes();changed=raw.replace(report['first_regular_records']['Makefile']['sha256'].encode(),b'0'*64);assert len(raw)==len(changed) and raw!=changed;p.write_bytes(changed)
 with pytest.raises(Refusal):unit.run_install_with_original_acceptance(x,accepted,None,['/usr/bin/make','altinstall'],x['build'],{},unit.Budget(),unit.time.monotonic())
 assert not dispatched
 records.append({'checkpoint':'same_inode_same_size_new_first_record_SHA','install_dispatches':0,'readopted':False})
 # Same exact checkpoint bytes moved to a new inode still cannot replace first identity.
 x,c,paths,values,view,report,accepted=accepted_fixture(tmp_path/'checkpoint-inode');p=x['root']/'REQUIRED_BUILD_OUTPUTS_VERIFIED.json'
 incoming=p.parent/'public-metadata-replacement';blob(incoming,p.read_bytes());os.replace(incoming,p)
 with pytest.raises(Refusal):unit.run_install_with_original_acceptance(x,accepted,None,['/usr/bin/make','altinstall'],x['build'],{},unit.Budget(),unit.time.monotonic())
 assert not dispatched;records.append({'checkpoint':'same_bytes_new_inode','install_dispatches':0,'readopted':False})
 # Immutable actor acceptance missing: no resampling the current report.
 x,c,paths,values,view,report,accepted=accepted_fixture(tmp_path/'missing-original')
 with pytest.raises(Refusal,match='immutable actor'):unit.run_install_with_original_acceptance(x,None,None,['/usr/bin/make','altinstall'],x['build'],{},unit.Budget(),unit.time.monotonic())
 assert not dispatched;records.append({'actor_first_tuple':'missing','install_dispatches':0,'new_baseline':False})
 assert len(records)==11
 save('ACTUAL_PREINSTALL_ORIGINAL_ACCEPTANCE_CONSUMER.json',{'status':'PASS_SCOPED_SOURCE_SOFTWARE','actual_consumer':'openssl359_cpython_successor_r4.run_install_with_original_acceptance','positive_supported_boundary_dispatch':positive,'negative_cases':records,'native_sdk_cp.run_only_boundary_spy':True,'target_compile_install_runtime':0})
