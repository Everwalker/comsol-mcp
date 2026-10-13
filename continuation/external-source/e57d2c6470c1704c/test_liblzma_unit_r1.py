"""Affected producer/gate/static-input controls, without target native commands."""
import json,os
from pathlib import Path
import pytest
from acquire_exact_archives import Refusal
import liblzma_build_gate_r1 as gate
import liblzma_successor_r1 as unit
from test_openssl359_cpython_r3 import cloned_real_build
from test_openssl359_cpython_pin_r4 import mutate
def build_fixture(tmp_path):
    x,c=cloned_real_build(tmp_path);x['root']=tmp_path
    leaf=x['build']/'Modules/_lzma.cpython-312-darwin.so';leaf.write_bytes((x['build']/'Modules/_ssl.cpython-312-darwin.so').read_bytes());leaf.chmod(0o755)
    (x['build']/'build/lib.macosx-11.0-universal2-3.12/_lzma.cpython-312-darwin.so').symlink_to('../../Modules/_lzma.cpython-312-darwin.so')
    return x,c,leaf
def test_five_actual_parser_members_positive(tmp_path):
    x,c,_=build_fixture(tmp_path);report=gate.required_build_outputs(x,c)
    assert set(report['first_regular_records'])=={'Makefile','python','_ssl','_hashlib','_lzma'}
    accepted=gate.capture_build_checkpoint(tmp_path/'REQUIRED_BUILD_OUTPUTS_VERIFIED.json',report)
    assert gate.verify_preinstall_checkpoint(x,accepted)['all_five_original_first_records_unchanged']
@pytest.mark.parametrize('kind',('inode','bytes'))
def test_new_lzma_firstread_drift_refused(monkeypatch,tmp_path,kind):
    x,c,leaf=build_fixture(tmp_path);original=gate.regular_leaf;changed=[]
    def after_read(*args,**kwargs):
        result=original(*args,**kwargs)
        if args[1]=='_lzma.cpython-312-darwin.so' and not changed:mutate(leaf,kind);changed.append(True)
        return result
    monkeypatch.setattr(gate,'regular_leaf',after_read)
    with pytest.raises(Refusal,match='first accepted regular'):gate.required_build_outputs(x,c)
@pytest.mark.parametrize('kind',('inode','bytes'))
def test_new_lzma_preinstall_drift_sdkinstall_zero(monkeypatch,tmp_path,kind):
    x,c,leaf=build_fixture(tmp_path);accepted=gate.capture_build_checkpoint(tmp_path/'REQUIRED_BUILD_OUTPUTS_VERIFIED.json',gate.required_build_outputs(x,c));mutate(leaf,kind);calls=[]
    monkeypatch.setattr(unit.cp,'run',lambda *a,**k:calls.append(a))
    with pytest.raises(Refusal,match='first accepted regular'):unit.run_install_with_original_acceptance(x,accepted,None,['/usr/bin/make','altinstall'],x['build'],{},unit.Budget(),unit.time.monotonic())
    assert calls==[]
def test_required_lzma_helper_error_blocks_install_even_make_zero(tmp_path):
    x,c,_=build_fixture(tmp_path);(x['logs']/c['stderr']).write_text('[ERROR] _lzma (ImportError: public synthetic loader refusal)\n')
    with pytest.raises(Refusal,match='helper required module ERROR'):gate.required_build_outputs(x,c)
def test_explicit_official_xz_configuration_arch_env_and_no_fallback():
    x=unit.layout();plan,base=unit.command_plan(x);assert len(plan)==13
    names=[unit.stage_done_record_name(label) for label,_,_ in plan]
    assert len(set(name.casefold() for name in names))==13
    for index,arch in ((0,'arm64'),(3,'x86_64')):
        label,argv,cwd=plan[index];host='aarch64-apple-darwin' if arch=='arm64' else 'x86_64-apple-darwin'
        assert '--build=aarch64-apple-darwin' in argv and '--host='+host in argv
        assert '--disable-shared' in argv and '--enable-static' in argv and '--enable-threads=posix' in argv
        assert plan[index+1][1]==['/usr/bin/make','-C','src/liblzma','-j2'] and plan[index+2][1]==['/usr/bin/make','-C','src/liblzma','install']
        env=unit.stage_environment(label,base);assert '-arch '+arch in env['CC'] and '-isysroot '+str(unit.cp.SDK) in env['CC'] and '-mmacosx-version-min=11.0' in env['CC']
    assert base['LIBLZMA_CFLAGS']=='-I'+str(x['fat']/'include') and base['LIBLZMA_LIBS']==str(x['fat']/'lib/liblzma.a')
    assert unit.UNIT_CAP==1800 and unit.RESERVE==300 and unit.CAPS['prepare']==600 and unit.CAPS['runtime']==180
    primary=unit.PREFIX.parent/'comsol-liblzma-primary-source-readonly-l91qhotl'
    configure=(primary/'configure.source.txt').read_text();make=(primary/'src__liblzma__Makefile.am.source.txt').read_text()
    assert 'cross_compiling=yes' in configure and 'SUBDIRS = api' in make and 'lib_LTLIBRARIES = liblzma.la' in make
    for option in plan[0][1][6:]:
        if option.startswith('--enable-') or option.startswith('--disable-'):
            name=option.split('=',1)[0].replace('--disable-','--enable-')
            assert name in configure or option in configure
def test_lzma_dynamic_system_fallback_rejected_with_actual_decoder(tmp_path):
    x,c,leaf=build_fixture(tmp_path);install=tmp_path/'installed';(install/'bin').mkdir(parents=True);folder=install/'lib/python3.12/lib-dynload';folder.mkdir(parents=True)
    (install/'bin/python3.12').write_bytes((x['build']/'python.exe').read_bytes())
    raw=leaf.read_bytes();old=b'/usr/lib/libSystem.B.dylib\0';new=b'/usr/lib/liblzma.5.dylib\0';assert old in raw
    for name in ('_ssl','_hashlib','_lzma'):
        data=raw.replace(old,new+b'\0'*(len(old)-len(new))) if name=='_lzma' else raw
        (folder/(name+'.cpython-312-darwin.so')).write_bytes(data)
    with pytest.raises(Refusal,match='dynamic system/foreign fallback'):unit.actual_binaries({'install':install})
def test_samefd_content_projection_rejects_wrong_extracted_bytes(tmp_path):
    from liblzma_prepare_r1 import extracted_content_matches
    (tmp_path/'public').write_bytes(b'right');rows=unit.deps.tree_record(tmp_path);projection=[{k:r[k] for k in ('relative_path','kind','mode','bytes','sha256') if k in r} for r in rows]
    extracted_content_matches(tmp_path,{'rows':projection});(tmp_path/'public').write_bytes(b'wrong')
    with pytest.raises(Refusal):extracted_content_matches(tmp_path,{'rows':projection})
def merge_fixture(tmp_path):
    # Public software boundary metadata only, never an actual lipo claim.
    import struct
    import liblzma_acceptance_r1 as merge
    x=unit.layout();x={k:tmp_path/p.relative_to(unit.layout()['root']) for k,p in x.items()}
    x['root']=tmp_path;(x['fat']/'lib').mkdir(parents=True);(x['fat']/'include').mkdir()
    raw={}
    for arch in ('arm64','x86_64'):
        target=x['lzma_'+arch];(target/'lib').mkdir(parents=True);(target/'include/lzma').mkdir(parents=True)
        (target/'include/lzma.h').write_bytes(b'public same header\n');(target/'include/lzma/version.h').write_bytes(b'public same version header\n')
        original=unit.PREFIX/'intel-tls-openssl359-r2'/('openssl-arm64' if arch=='arm64' else 'openssl-x86_64')/'lib/libssl.a'
        raw[arch]=original.read_bytes();(target/'lib/liblzma.a').write_bytes(raw[arch])
    first=merge.prepare_merge(x,unit.Budget(),unit.time.monotonic())
    a=48;b=(a+len(raw['arm64'])+3)&~3
    fat=struct.pack('>II',0xcafebabe,2)+struct.pack('>iiIII',0x100000c,0,a,len(raw['arm64']),2)+struct.pack('>iiIII',0x1000007,3,b,len(raw['x86_64']),2)
    fat+=raw['arm64']+b'\0'*(b-a-len(raw['arm64']))+raw['x86_64'];out=x['fat']/'lib/liblzma.a';out.write_bytes(fat)
    from tls_binary_r1 import fat_archive
    command={'request':{'argv':['/usr/bin/lipo','-create',str(x['lzma_arm64']/'lib/liblzma.a'),str(x['lzma_x86_64']/'lib/liblzma.a'),'-output',str(out)],'boundary':'PUBLIC_SYNTHETIC_NOT_ACTUAL_NATIVE'},'actual_wait_rc':0,'terminal_wait_observed':True,'diagnostic_errors':[]}
    unit.exclusive_json(x['root']/'MERGE_OUTPUT_VERIFIED.json',{'archives':[{'name':'liblzma.a','actual_command':command,'output':unit.record(out),'binary':fat_archive(fat,raw)}]})
    return x,merge,first
def test_exact_lzma_merge_firstaccepted_actual_decoder_positive(tmp_path):
    x,merge,first=merge_fixture(tmp_path);accepted=merge.accept_merged(x,first)
    assert isinstance(first,bytes) and isinstance(accepted,bytes)
    assert merge.verify_merged(x,accepted)['actual_library_two_slices_exact_and_all_objects_min11']
def test_exact_lzma_merge_metadata_firstrecord_replacement_not_readopted(tmp_path):
    x,merge,first=merge_fixture(tmp_path);accepted=merge.accept_merged(x,first)
    path=x['root']/'MERGE_INPUTS.json';data=json.loads(path.read_bytes());data['untrusted_extra']='cannot become new baseline';path.write_text(json.dumps(data))
    with pytest.raises(Refusal):merge.verify_merged(x,accepted)
