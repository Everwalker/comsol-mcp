"""Data/guard software checks only; no Rust setup, compiler or package import."""
import base64, csv, hashlib, io, json, os, struct, tarfile, time, zipfile
from pathlib import Path
import pytest
from tools import w26_intel_derived as d, w26_intel_derived_stage as stage, w26_runtime_actor as actor

INPUTS=Path('/private/tmp/cvw26-intel-derived-inputs-83a530b595d0')

def tar_blob(files):
    out=io.BytesIO()
    with tarfile.open(fileobj=out,mode='w:gz') as tar:
        for name,raw in files.items():
            item=tarfile.TarInfo(name);item.size=len(raw);item.mode=0o600;tar.addfile(item,io.BytesIO(raw))
    return out.getvalue()

def macho(minimum=0xc0000,cpu=0x1000007,lib='/usr/lib/libSystem.B.dylib',rpath=False):
    name=lib.encode()+b'\0';pad=(-len(name))%4;command=struct.pack('<IIIIII',0xc,24+len(name)+pad,24,0,0,0)+name+b'\0'*pad
    commands=struct.pack('<IIIIII',0x32,24,1,minimum,0,0)+command
    if rpath:commands+=struct.pack('<II',0x8000001c,8)
    return struct.pack('<IiiIIIII',0xfeedfacf,cpu,3,8,3 if rpath else 2,len(commands),0,0)+commands

@pytest.mark.parametrize('minimum,cpu,lib,rpath',[(0xb0000,0x1000007,'/usr/lib/libSystem.B.dylib',False),(0xc0000,0x100000c,'/usr/lib/libSystem.B.dylib',False),(0xc0000,0x1000007,'/tmp/libssl.dylib',False),(0xc0000,0x1000007,'/usr/lib/libSystem.B.dylib',True)])
def test_actual_parser_rejects_wrong_macho(minimum,cpu,lib,rpath):
    with pytest.raises(RuntimeError):d.macho12(macho(minimum,cpu,lib,rpath))

def test_macho12_data_positive():
    assert d.macho12(macho())['minimum_os']=='12.0'

@pytest.mark.parametrize('key,value',[('budget_seconds',3601),('cleanup_reserve_seconds',120),('prepare_cap_seconds',601),('max_output_bytes',12*1024**3),('sampled_rss_stop_bytes',7*1024**3),('max_make_parallelism',3),('minimum_free_bytes',7*1024**3),('budget_seconds',True)])
def test_closed_scope_rejects_budget_widening(key,value):
    with pytest.raises(RuntimeError):d.scope({**d.SCOPE,key:value})

def test_scope_literal8_and_private_maturin(tmp_path):
    rows=d.stages(tmp_path);assert len(rows)==8
    assert rows[-1]['argv'][0]==str(tmp_path/'work/build-site/bin/maturin')
    assert rows[-1]['argv'][rows[-1]['argv'].index('--features')+1]=='pyo3/abi3-py312'
    assert rows[-1]['environment']['MACOSX_DEPLOYMENT_TARGET']=='12.0'
    assert rows[-1]['environment']['CARGO_NET_OFFLINE']=='true'
    assert rows[-1]['environment']['HOME']==str(tmp_path/'work/empty-home')
    assert rows[5]['argv']==['/usr/bin/make','-j2','build_libs']

def test_closed_json_duplicate():
    with pytest.raises(RuntimeError):d.closed_json(b'{"x":1,"x":2}')

@pytest.mark.parametrize('name',['../x','/tmp/x','a/../b','a//b','a\\b'])
def test_unsafe_archive_member(name,tmp_path):
    with pytest.raises(RuntimeError):d.unpack_tar(tar_blob({name:b'x'}),tmp_path/'copy')

def test_archive_copy_vendor_checks_actual_hash(tmp_path):
    blob=tar_blob({'thing-1.0/Cargo.toml':b'[package]\nname="thing"\nversion="1.0"\n','thing-1.0/file.rs':b'x'})
    checksum=hashlib.sha256(blob).hexdigest();d.unpack_tar(blob,tmp_path/'copy',cargo_checksum=checksum)
    doc=json.loads((tmp_path/'copy/thing-1.0/.cargo-checksum.json').read_text())
    assert doc['package']==checksum and doc['files']['file.rs']==hashlib.sha256(b'x').hexdigest()
    assert (tmp_path/'copy/thing-1.0/file.rs').read_bytes()==b'x'

@pytest.mark.parametrize('kind',[tarfile.SYMTYPE,tarfile.LNKTYPE,tarfile.CHRTYPE])
def test_special_archive_no_effect(kind,tmp_path):
    out=io.BytesIO()
    with tarfile.open(fileobj=out,mode='w:gz') as tar:
        item=tarfile.TarInfo('root/file');item.type=kind;item.linkname='/tmp/else';tar.addfile(item)
    with pytest.raises(RuntimeError):d.unpack_tar(out.getvalue(),tmp_path/'copy')
    assert not (tmp_path/'copy/root/file').exists()

@pytest.mark.parametrize('args',[(None,),({},),(object(),)])
def test_execute_no_json_minted_owner(args,tmp_path):
    with pytest.raises(RuntimeError,match='internal source gate'):
        d.execute_intel(tmp_path,{}, {},{},began=time.monotonic(),admission_token=args[0])

@pytest.mark.skipif(not INPUTS.is_dir(),reason='exact downloaded inputs not present')
def test_actual_locked_primary_source_and_maturin_no_import():
    h=d._helpers();record=h.raw_file(INPUTS/'source.json',maximum=16777216)[1];m=d.verify_source(INPUTS,record)
    assert m['source_count']==6606
    deps=json.loads((INPUTS/'DEPENDENCIES_RECEIPT.json').read_text());row=next(x for x in deps['rows'] if x['name']=='maturin')
    meta,actual=d.wheel_check(row['current_path'],expected_name='maturin',expected_version='1.14.1')
    assert actual['sha256']==row['sha256']
    assert d.release.wheel_target_check(Path(row['current_path']).name,'macos_x86_64')

@pytest.mark.skipif(not INPUTS.is_dir(),reason='exact current product proof absent')
def test_actual_product_header_sysconfig_records_no_interpreter():
    records=d.host_records(INPUTS);assert records['python']['sha256']==d.PYTHON_SHA;d.check_host(records)

@pytest.mark.parametrize('errno_value,status',[(1,'PASS_ACTUAL_EPERM'),(13,'PASS_ACTUAL_EPERM'),(61,'UNKNOWN_NETWORK_RULE_NOT_PROVEN')])
def test_original_os_denial_classifier(errno_value,status):
    def blocked(*a,**k):raise OSError(errno_value,'bounded test')
    assert stage.network_denial(12345,blocked)['status']==status

def test_same_pid_stage_does_not_exec_unproved_network(monkeypatch,tmp_path):
    class Helper:
        def secure_dir(self,*a):pass
    # Test actual productionstage semantic fence, with no exec/Popen path.
    seen=[];root=tmp_path;ctx={'schema':'w26-intel-derived-stage/1','unit_root':str(root),'root_identity':{},'legacy_sources':{},'commands':d.stages(root)}
    helper={'secure_dir':lambda *a:None,'publish':lambda *a:seen.append(a) or {}}
    monkeypatch.setattr(stage,'read_context',lambda *a:(ctx,helper))
    def refused(*a,**kw):raise OSError(61,'connection refused is not OSdeny')
    with pytest.raises(RuntimeError,match='unproven'):stage.execute('unused','unused',0,12345,connector=refused,execve=lambda *a:pytest.fail('next effect dispatched'))
    assert len(seen)==1

@pytest.mark.parametrize('index',[True,-1,8])
def test_stage_index_fence(monkeypatch,tmp_path,index):
    ctx={'schema':'w26-intel-derived-stage/1','unit_root':str(tmp_path),'root_identity':{},'legacy_sources':{},'commands':d.stages(tmp_path)}
    monkeypatch.setattr(stage,'read_context',lambda *a:(ctx,{}))
    with pytest.raises(RuntimeError,match='stage index'):stage.execute('x','x',index,12345)

@pytest.mark.skipif(not INPUTS.is_dir(),reason='exact official inputs absent')
def test_actual32_vendor_package_and_file_hash_fence(tmp_path):
    import tomllib
    rows=json.loads((INPUTS/'DEPENDENCIES_RECEIPT.json').read_text())['rows'];vendor=tmp_path/'vendor';vendor.mkdir(mode=0o700)
    for row in rows:
        if row['kind']!='CURRENT_CARGO_LOCK_CRATE':continue
        part=tmp_path/row['filename'];d.unpack_tar(Path(row['current_path']).read_bytes(),part,cargo_checksum=row['sha256'])
        (part/(row['name']+'-'+row['version'])).rename(vendor/(row['name']+'-'+row['version']))
    locked=tomllib.loads((INPUTS/'upstream/cryptography/cryptography-50.0.1/Cargo.lock').read_text())
    assert len(d.verify_vendor(vendor,locked))==32
    p=vendor/'pyo3-0.29.0/Cargo.toml';original=p.read_bytes();p.write_bytes(original+b'\n# altered\n')
    with pytest.raises(RuntimeError,match='hash differs'):d.verify_vendor(vendor,locked)
    p.write_bytes(original);assert len(d.verify_vendor(vendor,locked))==32
    doc=vendor/'pyo3-0.29.0/.cargo-checksum.json';value=json.loads(doc.read_text());value['package']='0'*64;doc.write_text(json.dumps(value))
    with pytest.raises(RuntimeError,match='package checksum'):d.verify_vendor(vendor,locked)

@pytest.mark.skipif(not INPUTS.is_dir(),reason='exact official wheel absent')
def test_actual_maturin_RECORD_payload_tamper_is_rejected(tmp_path):
    rows=json.loads((INPUTS/'DEPENDENCIES_RECEIPT.json').read_text())['rows'];row=next(x for x in rows if x['name']=='maturin');original=Path(row['current_path']);target=tmp_path/original.name
    with zipfile.ZipFile(original) as z,zipfile.ZipFile(target,'w') as out:
        for name in z.namelist():
            raw=z.read(name)
            if name=='maturin/__main__.py':raw+=b'\n# tampered\n'
            out.writestr(name,raw)
    with pytest.raises(RuntimeError,match='RECORD hash'):d.wheel_check(target)

@pytest.mark.skipif(not INPUTS.is_dir(),reason='official immutable source proof absent')
def test_actual_materialize_private_data_only_original_source_unchanged(tmp_path):
    h=d._helpers();root=tmp_path/'owned';root.mkdir(mode=0o700);record=h.raw_file(INPUTS/'source.json',maximum=16777216)[1]
    prepared={'input_root':str(INPUTS),'manifest':record,'host':d.host_records(INPUTS),'metadata':{n:h.raw_file(INPUTS/n,maximum=16777216)[1] for n in ('ARCHIVES_RECEIPT.json','DEPENDENCIES_RECEIPT.json','BUILD_DRAFT.json','ALL32_CRATE_SOURCE_AUDIT.json')}}
    # Actual production owner semantics for readonly software materialization;
    # never call Intel execution or mint a native/closedIntel approval here.
    owner=actor.RuntimeOwner(root,{'activity_kind':actor.CURRENT43},token=actor._OWNER_TOKEN)
    locked=d.materialize(root,prepared,owner)
    assert len(d.verify_vendor(root/'work/vendor',locked))==32
    assert (root/'work/build-site/bin/maturin').is_file()
    assert os.access(root/'work/build-site/bin/maturin',os.X_OK)
    assert not (root/'work/rust').exists() # No componentinstaller executed.
    d.verify_work_sources(root,prepared);d.verify_source(INPUTS,record)
    source=root/'work/sources/cryptography-50.0.1/Cargo.lock';source.write_bytes(source.read_bytes()+b'\n# altered\n')
    with pytest.raises(RuntimeError,match='pinned source bytes'):d.verify_work_sources(root,prepared)

@pytest.mark.skipif(os.environ.get('CVW26_INTEL_REAL_OS')!='1',reason='opt-in harmless originalPopen OS sandbox probe')
def test_original_owner_samePID_real_OS_denial_held_listener(tmp_path):
    import socket,sys
    from types import SimpleNamespace
    h=d._helpers();root=tmp_path/'owned-os';root.mkdir(mode=0o700)
    for name in ('work','evidence','logs'):(root/name).mkdir(mode=0o700)
    identity=h.secure_dir(root);py=str(Path(sys.executable).resolve());proof=root/'evidence/HARMLESS.json'
    effect=[py,'-I','-S','-B','-c',f'import json,os;open({str(proof)!r},"w").write(json.dumps({{"pid":os.getpid(),"only_harmless_python":True}}))']
    rows=[{'label':'harmless-'+str(i),'argv':effect,'cwd':str(root/'work'),'environment':{'PATH':'/usr/bin:/bin'},'cap':10} for i in range(8)]
    context=h.publish(root/'context.json',{'schema':'w26-intel-derived-stage/1','unit_root':str(root),'root_identity':identity,'legacy_sources':{'common':h.raw_file(d.COMMON)[1]},'commands':rows})
    owner=actor.RuntimeOwner(root,{'activity_kind':actor.CURRENT43},token=actor._OWNER_TOKEN)
    plan={'administrative_python':{'path':py}};binding={'nonce':'harmless-software-only-fixednonce'}
    sandbox=root/'sandbox.sb';h.copy_blob(sandbox,('(version 1)\n(allow default)\n(deny network*)\n(deny file-write*)\n(allow file-write* (subpath '+json.dumps(str(root))+'))\n').encode())
    fd=os.open(root/'logs',os.O_RDONLY|os.O_DIRECTORY);old=actor.original.subprocess
    actor.original.subprocess=SimpleNamespace(Popen=owner.popen,run=owner.diagnostic_run,TimeoutExpired=actor.subprocess.TimeoutExpired)
    try:
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(.1);port=listener.getsockname()[1]
            with socket.create_connection(('127.0.0.1',port),timeout=1):conn,_=listener.accept();conn.close()
            argv=[py,'-S','-B',str(Path(stage.__file__)),'--context',context['path'],'--sha',context['sha256'],'--index','0','--port',str(port)]
            result=actor.run_owned_stage(owner,fd,0,'harmless',argv,root,{'PATH':'/usr/bin:/bin','PYTHONPATH':str(Path(d.__file__).parents[1]),'PYTHONDONTWRITEBYTECODE':'1'},10,binding,plan,sandbox_path=sandbox)
            assert result['rc']==0 and owner.process.poll()==0
            assert json.loads(proof.read_text())['pid']==owner.process.pid
            assert json.loads((root/'evidence/PROBE_0.json').read_text())['status']=='PASS_ACTUAL_EPERM'
            with pytest.raises(socket.timeout):listener.accept()
    finally:
        actor.original.subprocess=old;os.close(fd)
        # Keep the actual object and reap even if software proof fails.
        if owner.process is not None:owner.original_wait(timeout=40)

@pytest.mark.skipif(os.environ.get('CVW26_INTEL_REAL_OS')!='1',reason='opt-in harmless originalowner unknown retention')
def test_actual_original_child_UNKNOWN_fences_no_next_effect_firstcause(tmp_path):
    import sys
    h=d._helpers();root=tmp_path/'retention';root.mkdir(mode=0o700);(root/'logs').mkdir(mode=0o700)
    unknown=root/'UNKNOWN.json';h.publish(unknown,{'status':'UNKNOWN','safe_retry':False});before=unknown.read_bytes()
    owner=actor.RuntimeOwner(root,{'activity_kind':actor.CURRENT43},token=actor._OWNER_TOKEN)
    argv=[str(Path(sys.executable).resolve()),'-I','-S','-B','-c','import time;time.sleep(0.1)'];owner.expected=argv
    child=owner.popen(argv,start_new_session=True)
    first=ValueError('first original cause');exc=actor.original.StageUnknown('softwareUNKNOWN',process=child,token='software-only');exc.original_cause=first
    birth=actor.original.actual_birth(child,argv);exc.original_result={'birth':birth,'group':{'status':'OBSERVED','actual_pgid':child.pid}}
    fd=os.open(root/'logs',os.O_RDONLY|os.O_DIRECTORY)
    try:
        actor.retain_first_failure(owner,fd,exc,[],stem='HARMLESS')
        assert owner.process is child and owner.first_cause is first and owner.observe_only
        with pytest.raises(TimeoutError):owner.remaining(1)
        assert unknown.read_bytes()==before and child.poll()==0
    finally:os.close(fd);owner.original_wait(timeout=10)

def ar_object(raw):
    name=b'file.o/';header=name.ljust(16)+b'0'.ljust(12)+b'0'.ljust(6)+b'0'.ljust(6)+b'600'.ljust(8)+str(len(raw)).encode().ljust(10)+b'`\n'
    return b'!<arch>\n'+header+raw+(b'\n' if len(raw)%2 else b'')

@pytest.mark.parametrize('minimum,cpu',[(0xb0000,0x1000007),(0xc0000,0x100000c)])
def test_static_OpenSSL_archive_actual_arch_and_minimum_rejected(minimum,cpu):
    raw=bytearray(macho(minimum=minimum,cpu=cpu));struct.pack_into('<I',raw,12,1)
    with pytest.raises(RuntimeError):d.static_archive12(ar_object(bytes(raw)))

def test_static_OpenSSL_actual_object12_data_positive():
    raw=bytearray(macho());struct.pack_into('<I',raw,12,1)
    assert d.static_archive12(ar_object(bytes(raw)))['architecture']=='x86_64'
