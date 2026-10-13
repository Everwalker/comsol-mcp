"""Actual four-member whole-gate/preinstall drift controls; no new native."""
import json,os
from pathlib import Path
import pytest
from acquire_exact_archives import Refusal
import openssl359_build_gate_r4 as gate
import openssl359_cpython_successor_r4 as unit
from test_openssl359_cpython_r3 import cloned_real_build,actual_command
import openssl359_successor_r2 as original

MEMBERS=('Makefile','python.exe','_ssl.cpython-312-darwin.so','_hashlib.cpython-312-darwin.so')
def member_path(x,name):return x['build']/name if name in ('Makefile','python.exe') else x['build']/'Modules'/name
def mutate(path,kind):
    raw=path.read_bytes();changed=bytearray(raw);changed[-1]^=1
    if kind=='inode':
        replacement=path.parent/(path.name+'.public-replacement');replacement.write_bytes(changed);replacement.chmod(path.stat().st_mode&0o777);os.replace(replacement,path)
    else:
        before=path.stat();path.write_bytes(changed);os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))

def test_real_r2_positive_whole_gate_and_original_acceptance_recheck(tmp_path):
    x=original.layout();report=gate.required_build_outputs(x,actual_command())
    assert set(report['first_regular_records'])=={'Makefile','python','_ssl','_hashlib'}
    assert gate.recheck_original_acceptance(x,report)['all_four_original_first_records_unchanged']
    own={'root':tmp_path,'build':x['build']};accepted=gate.capture_build_checkpoint(tmp_path/'REQUIRED_BUILD_OUTPUTS_VERIFIED.json',report)
    assert all(isinstance(value,bytes) for value in accepted)
    assert gate.verify_preinstall_checkpoint(own,accepted)['all_four_original_first_records_unchanged']

@pytest.mark.parametrize('name',MEMBERS)
@pytest.mark.parametrize('kind',('inode','bytes'))
def test_actual_whole_gate_last_initial_member_then_earlier_change_refuses(monkeypatch,tmp_path,name,kind):
    x,command=cloned_real_build(tmp_path);read=gate.regular_leaf;changed=[]
    def after_final_firstread(*args,**kwargs):
        result=read(*args,**kwargs)
        if args[1]=='_hashlib.cpython-312-darwin.so' and not changed:mutate(member_path(x,name),kind);changed.append(True)
        return result
    monkeypatch.setattr(gate,'regular_leaf',after_final_firstread)
    with pytest.raises(Refusal,match='first accepted regular'):gate.required_build_outputs(x,command)
    assert changed==[True]

@pytest.mark.parametrize('name',MEMBERS)
@pytest.mark.parametrize('kind',('inode','bytes'))
def test_actual_after_accepted_preinstall_change_refuses_install_zero(monkeypatch,tmp_path,name,kind):
    x,command=cloned_real_build(tmp_path);x['root']=tmp_path
    report=gate.required_build_outputs(x,command);accepted=gate.capture_build_checkpoint(tmp_path/'REQUIRED_BUILD_OUTPUTS_VERIFIED.json',report)
    mutate(member_path(x,name),kind);dispatched=[]
    monkeypatch.setattr(unit.cp,'run',lambda *a,**k:dispatched.append(a))
    with pytest.raises(Refusal,match='first accepted regular'):
        unit.run_install_with_original_acceptance(x,accepted,None,['/usr/bin/make','altinstall'],x['build'],{},unit.Budget(),unit.time.monotonic())
    assert dispatched==[]

def test_metadata_and_firstrecord_replacement_not_readopted(tmp_path):
    x,command=cloned_real_build(tmp_path);x['root']=tmp_path;report=gate.required_build_outputs(x,command)
    path=tmp_path/'REQUIRED_BUILD_OUTPUTS_VERIFIED.json';accepted=gate.capture_build_checkpoint(path,report)
    altered=dict(report);altered['first_regular_records']=dict(report['first_regular_records']);altered['first_regular_records']['Makefile']=dict(altered['first_regular_records']['Makefile'],sha256='0'*64)
    replacement=tmp_path/'public-metadata-replacement';replacement.write_text(json.dumps(altered));replacement.chmod(0o600);os.replace(replacement,path)
    with pytest.raises(Refusal):gate.verify_preinstall_checkpoint(x,accepted)
    assert json.loads(accepted[1])['first_regular_records']['Makefile']['sha256']==report['first_regular_records']['Makefile']['sha256']

def test_new_six_commands_and_original_helper_error_and_budget_unchanged():
    import openssl359_cpython_successor_r3 as prior
    plan,env=unit.command_plan(unit.layout());old,_=prior.command_plan(prior.layout())
    for current,before in zip(plan,old):assert [arg.replace(str(unit.layout()['root']),str(prior.layout()['root'])) for arg in current[1]]==before[1]
    assert len(plan)==6 and unit.UNIT_CAP==1800 and unit.RESERVE==300 and unit.CAPS['prepare']==600
    text=Path(unit.__file__).read_text();assert 'run_install_with_original_acceptance(x,accepted_build' in text
    assert text.index('verify_preinstall_checkpoint(x,accepted)')<text.index("return cp.run(lfd,'cp-install'")
