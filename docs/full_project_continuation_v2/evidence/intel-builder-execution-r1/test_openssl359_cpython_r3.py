"""Affected official-build-view gate controls, no target runtime execution."""
import json,os,shutil
from pathlib import Path
import pytest
from acquire_exact_archives import Refusal
import openssl359_build_gate_r3 as gate
import openssl359_cpython_successor_r3 as unit
import openssl359_cpython_checkpoint_r3 as checkpoint
import openssl359_successor_r2 as original

def actual_command():
    result=json.loads((original.layout()['root']/'RESULT.json').read_bytes())
    return next(c for c in result['commands'] if c['argv']==['/usr/bin/make','-j2'])

def toy_view(tmp_path):
    build=tmp_path/'owned-build';modules=build/'Modules';view=build/'build/lib.macosx-11.0-universal2-3.12'
    modules.mkdir(parents=True,mode=0o700);view.mkdir(parents=True,mode=0o700)
    name='_ssl.cpython-312-darwin.so';(modules/name).write_bytes(b'PUBLIC INERT physical leaf; not a MachO acceptance');(modules/name).chmod(0o600)
    (view/name).symlink_to('../../Modules/'+name)
    return build,modules,view,name

def test_actual_r2_official_main_and_two_modules_physical_gate_readonly():
    result=gate.required_build_outputs(original.layout(),actual_command())
    assert set(result['required_build_binaries'])=={'python','_ssl','_hashlib'}
    assert all(set(row['slices'])=={'arm64','x86_64'} for row in result['required_build_binaries'].values())
    assert all(not row['followed_for_bytes'] for row in result['official_exact_symbolic_views'])
    assert result['runtime_acceptance']=='NOT_RUN' and not result['general_symlink_follow']

@pytest.mark.parametrize('target',['/outside/public','../../../outside','../../Modules/_ssl_failed.cpython-312-darwin.so','../../Modules/./_ssl.cpython-312-darwin.so'])
def test_wrong_literal_view_target_refused(tmp_path,target):
    build,modules,view,name=toy_view(tmp_path);(view/name).unlink();(view/name).symlink_to(target)
    a=os.open(modules,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);b=os.open(view,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        with pytest.raises(Refusal):gate.module_leaf(a,b,'_ssl',build)
    finally:os.close(a);os.close(b)

def test_physical_leaf_symlink_and_failed_name_refused(tmp_path):
    build,modules,view,name=toy_view(tmp_path);outside=tmp_path/'public-outside';outside.write_bytes(b'PUBLIC outside fixture')
    (modules/name).unlink();(modules/name).symlink_to(outside)
    a=os.open(modules,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);b=os.open(view,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        with pytest.raises(OSError):gate.module_leaf(a,b,'_ssl',build)
        with pytest.raises(Refusal):gate.module_leaf(a,b,'_ssl_failed',build)
    finally:os.close(a);os.close(b)
    assert outside.read_bytes()==b'PUBLIC outside fixture'

def test_link_identity_swap_after_physical_read_refused(monkeypatch,tmp_path):
    build,modules,view,name=toy_view(tmp_path);read=gate.regular_leaf
    def swap(*args,**kwargs):
        result=read(*args,**kwargs);(view/name).unlink();(view/name).symlink_to('../../Modules/'+name);return result
    monkeypatch.setattr(gate,'regular_leaf',swap)
    a=os.open(modules,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);b=os.open(view,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        with pytest.raises(Refusal,match='identity/text changed'):gate.module_leaf(a,b,'_ssl',build)
    finally:os.close(a);os.close(b)

def cloned_real_build(tmp_path):
    original_build=original.layout()['build'];build=tmp_path/'synthetic-owned-build';build.mkdir(mode=0o700);(build/'Modules').mkdir(mode=0o700);view=build/'build/lib.macosx-11.0-universal2-3.12';view.mkdir(parents=True,mode=0o700);logs=tmp_path/'logs';logs.mkdir(mode=0o700)
    main=original.build_python_basename((original_build/'Makefile').read_bytes())
    for source,dest in [(original_build/'Makefile',build/'Makefile'),(original_build/main,build/main)]+[(original_build/'Modules'/name,build/'Modules'/name) for name in ['_ssl.cpython-312-darwin.so','_hashlib.cpython-312-darwin.so']]:shutil.copyfile(source,dest);dest.chmod(0o600)
    for name in ['_ssl.cpython-312-darwin.so','_hashlib.cpython-312-darwin.so']:(view/name).symlink_to('../../Modules/'+name)
    for suffix in ('stdout','stderr'):(logs/suffix).write_bytes(b'');(logs/suffix).chmod(0o600)
    command=dict(actual_command(),stdout='stdout',stderr='stderr')
    return {'build':build,'logs':logs},command

def test_parent_directory_swap_anchored_fd_reads_then_refuses_current_tree(monkeypatch,tmp_path):
    x,command=cloned_real_build(tmp_path);read=gate.regular_leaf;changed=[]
    def swap(parent,name,path,*args,**kwargs):
        if name=='_ssl.cpython-312-darwin.so' and not changed:
            (x['build']/'Modules').rename(x['build']/'original-public-modules');(x['build']/'Modules').mkdir(mode=0o700);changed.append(True)
        return read(parent,name,path,*args,**kwargs)
    monkeypatch.setattr(gate,'regular_leaf',swap)
    with pytest.raises(Refusal,match='parent directory swapped'):gate.required_build_outputs(x,command)
    assert changed==[True]

def test_required_error_still_stops_before_physical_reads(monkeypatch,tmp_path):
    logs=tmp_path/'logs';logs.mkdir();(logs/'stdout').write_text('');(logs/'stderr').write_text('[ERROR] _ssl failed to import: PUBLIC synthetic failure\n')
    for p in logs.iterdir():p.chmod(0o600)
    command=dict(actual_command(),stdout='stdout',stderr='stderr')
    with pytest.raises(Refusal,match='required module ERROR'):gate.required_build_outputs({'build':tmp_path/'absent','logs':logs},command)

def test_actual_checkpoint_readonly_29154_whole_and_fat_headers():
    result=checkpoint.verify_checkpoint()
    assert result['r2']['records']==29154 and len(result['commands'])==11
    assert len(result['headers'])==143 and {a['name'] for a in result['artifacts']}=={'libssl.a','libcrypto.a'}

def test_new_six_cp_commands_only_no_openssl_or_lipo_replay():
    plan,env=unit.command_plan(unit.layout());oldplan,_=original.command_plan(original.layout())
    assert len(plan)==6 and unit.UNIT_CAP==1800 and unit.RESERVE==300
    for command,prior in zip(plan,oldplan[7:]):
        assert command[0]==prior[0]
        assert [a.replace(str(unit.layout()['root']),str(original.layout()['root'])) for a in command[1]]==prior[1]
    assert not unit.layout()['root'].exists()


def test_first_view_swapped_while_second_module_is_read_refused(monkeypatch,tmp_path):
    x,command=cloned_real_build(tmp_path);read=gate.regular_leaf;view=x['build']/'build/lib.macosx-11.0-universal2-3.12';changed=[]
    def swap(parent,name,path,*args,**kwargs):
        result=read(parent,name,path,*args,**kwargs)
        if name=='_hashlib.cpython-312-darwin.so':
            link=view/'_ssl.cpython-312-darwin.so';link.unlink();link.symlink_to('../../Modules/_ssl.cpython-312-darwin.so');changed.append(True)
        return result
    monkeypatch.setattr(gate,'regular_leaf',swap)
    with pytest.raises(Refusal,match='before whole gate completed'):gate.required_build_outputs(x,command)
    assert changed==[True]
