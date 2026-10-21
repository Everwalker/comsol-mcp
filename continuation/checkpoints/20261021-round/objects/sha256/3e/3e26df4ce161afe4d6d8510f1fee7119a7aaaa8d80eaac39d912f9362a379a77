import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from types import SimpleNamespace

import pytest
from tools import w26_rebuild_current_runtime as sources
from tools import w26_runtime_actor as actor
from tools import _w26_original_process as original
from tools import w26_current_runtime_profile as profile
from tools import _w26_original_tls as tls
from tools.w26_prepare_current_inputs import _helpers


def archive(tmp_path, kind='file'):
    target=tmp_path/'source.tar';data=io.BytesIO()
    with tarfile.open(fileobj=data,mode='w') as tar:
        member=tarfile.TarInfo('fixture/file')
        if kind=='traversal':member.name='fixture/../escape'
        elif kind=='absolute':member.name='/fixture/escape'
        elif kind=='symlink':member.type=tarfile.SYMTYPE;member.linkname='../escape'
        elif kind=='hardlink':member.type=tarfile.LNKTYPE;member.linkname='fixture/file'
        elif kind=='special':member.type=tarfile.CHRTYPE
        if member.isfile():member.size=3;tar.addfile(member,io.BytesIO(b'abc'))
        else:tar.addfile(member)
        if kind=='duplicate':tar.addfile(member,io.BytesIO(b'abc'))
    target.write_bytes(data.getvalue());target.chmod(0o600);return target


def test_actual_exclusive_no_link_source_unpack(tmp_path):
    target=archive(tmp_path);out=tmp_path/'out';result=sources.safe_unpack(target,out,'fixture')
    assert result['member_count']==1 and result['expanded_bytes']==3
    assert (out/'fixture/file').read_bytes()==b'abc'
    with pytest.raises(FileExistsError):sources.safe_unpack(target,out,'fixture')


@pytest.mark.parametrize('change',['extra-file','extra-directory','symlink','changed-bytes','missing'])
def test_whole_source_tree_rejects_membership_and_content_drift(tmp_path,change):
    out=tmp_path/'out';result=sources.safe_unpack(archive(tmp_path),out,'fixture')
    actor.verify_source_tree(out,result['whole_rows'])
    if change=='extra-file':(out/'fixture/injected').write_bytes(b'code')
    elif change=='extra-directory':(out/'fixture/extra').mkdir()
    elif change=='symlink':(out/'fixture/link').symlink_to('file')
    elif change=='changed-bytes':(out/'fixture/file').write_bytes(b'def')
    else:(out/'fixture/file').unlink()
    with pytest.raises((RuntimeError,OSError)):actor.verify_source_tree(out,result['whole_rows'])


@pytest.mark.parametrize('link',['internal','absolute','escape'])
def test_relocation_copy_closed_relative_links(tmp_path,link):
    source=tmp_path/'installed';source.mkdir(mode=0o700)
    (source/'file').write_bytes(b'fixture');(source/'file').chmod(0o700)
    (tmp_path/'external').write_bytes(b'outside')
    value='file' if link=='internal' else str(source/'file') if link=='absolute' else '../external'
    (source/'alias').symlink_to(value)
    destination=tmp_path/'copy'
    if link=='internal':
        actor.copy_owned_tree(source,destination)
        assert (destination/'alias').read_bytes()==b'fixture'
        assert (destination/'file').stat().st_mode&0o111
        with pytest.raises(FileExistsError):actor.copy_owned_tree(source,destination)
    else:
        with pytest.raises(RuntimeError,match='escapes'):actor.copy_owned_tree(source,destination)


@pytest.mark.parametrize('kind',['traversal','absolute','symlink','hardlink','special','duplicate'])
def test_tar_bad_members_refused_before_extraction(tmp_path,kind):
    out=tmp_path/'out'
    with pytest.raises(RuntimeError,match='tar'):sources.safe_unpack(archive(tmp_path,kind),out,'fixture')
    assert not out.exists()


def test_same_source_archive_nofollow_refuses_alias(tmp_path):
    target=archive(tmp_path);alias=tmp_path/'alias';alias.symlink_to(target)
    with pytest.raises(OSError):sources.safe_unpack(alias,tmp_path/'out','fixture')


@pytest.mark.parametrize('url',['http://github.com/data','https://evil.invalid/data','https://github.com@evil.invalid/data'])
def test_source_redirect_stays_inside_explicit_publisher_origins(url):
    redirect=sources.SourceRedirect('https://github.com/official/source')
    with pytest.raises(RuntimeError,match='redirect'):redirect.redirect_request(None,None,302,'',{},url)


def fake_gate_case(tmp_path):
    helper=_helpers();root=tmp_path/'root';root.mkdir(mode=0o700)
    source=root/'source.py';source.write_text('# synthetic source fixture only\n');source.chmod(0o600)
    records={}
    for key in ('prepared','toolchain','plan'):
        records[key]=helper.publish(root/(key+'.json'),{'synthetic_fixture_not_native_authority':True})
    records['source']=helper.publish(root/'source.json',{str(source):helper.raw_file(source)[1]})
    candidate={'root_identity':helper.secure_dir(root),**records}
    candidate_record=helper.publish(root/'ACTOR_CANDIDATE.json',candidate)
    review=helper.publish(root/'MAIN_SOURCE_REVIEW.json',{'status':'PASS_W26_RUNTIME_REBUILD_SOURCE_FOR_EXACT_CANDIDATE',
                          'candidate_sha256':candidate_record['sha256'],'scope':'SYNTHETIC_GATE_TEST_NO_REAL_TOOLCHAIN'})
    binding={'status':'MAIN_APPROVED_ONCE_W26_RUNTIME_REBUILD','candidate_sha256':candidate_record['sha256'],
        'root':str(root),'root_identity':candidate['root_identity'],'budget_seconds':7200,'cleanup_reserve_seconds':600,
        'max_make_parallelism':2,'sampled_rss_stop_bytes':6*1024**3,'max_output_bytes':12*1024**3,
        'GUI':False,'COMSOL':False,'nonce':'synthetic-unit-fixture-not-a-real-native-once','review_sha256':review['sha256']}
    return root,binding


def test_missing_fresh_main_binding_has_zero_native_popen(tmp_path,monkeypatch):
    monkeypatch.setattr(actor.subprocess,'Popen',lambda *a,**kw:pytest.fail('no native Popen before gate'))
    with pytest.raises(FileNotFoundError):actor.execute(tmp_path,tmp_path/'MAIN_ONCE_BINDING.json','0'*64)


@pytest.mark.parametrize('change',['status','scope','candidate','nonce','GUI_bool','Root_review','candidate_escape','old_once','source_drift'])
def test_actual_fresh_gate_refusals_have_zero_native_popen(tmp_path,monkeypatch,change):
    root,binding=fake_gate_case(tmp_path);helper=_helpers()
    if change=='status':binding['status']='OLD_HISTORICAL_APPROVAL'
    elif change=='scope':binding['budget_seconds']=7201
    elif change=='candidate':binding['candidate_sha256']='0'*64
    elif change=='nonce':binding['nonce']=''
    elif change=='GUI_bool':binding['GUI']=0
    elif change=='Root_review':binding['review_sha256']='0'*64
    elif change=='candidate_escape':binding['candidate_filename']='../ACTOR_CANDIDATE.json'
    elif change=='old_once':helper.publish(root/'UNIT_ONCE_GUARD.json',{'already_consumed':True})
    else:(root/'source.py').write_text('# changed\n')
    record=helper.publish(root/'MAIN_ONCE_BINDING.json',binding)
    monkeypatch.setattr(actor.subprocess,'Popen',lambda *a,**kw:pytest.fail('native Popen after gate refusal'))
    with pytest.raises((RuntimeError,helper.Refusal)):actor.execute(root,root/'MAIN_ONCE_BINDING.json',record['sha256'])


def test_internal_runtime_owner_cannot_be_constructed_from_json(tmp_path):
    with pytest.raises(RuntimeError,match='internal owner'):actor.RuntimeOwner(tmp_path,{},token={'nonce':'pretend'})


def test_same_shared_epoch_reserve_stops_next_native_effect(tmp_path,monkeypatch):
    clock=SimpleNamespace(now=100.)
    monkeypatch.setattr(actor.time,'monotonic',lambda:clock.now)
    owner=actor.RuntimeOwner(tmp_path,{},token=actor._OWNER_TOKEN,began=50.)
    assert owner.deadline==7250.
    clock.now=6651.
    with pytest.raises(TimeoutError):owner.remaining(1800)
    assert owner.deadline==7250.


def test_original_run_unknown_retains_same_popen_and_original_cause(tmp_path,monkeypatch):
    child=SimpleNamespace(pid=222,wait=lambda timeout:0)
    first=OSError('FIRST_BIRTH_OBSERVATION_FAILURE')
    monkeypatch.setattr(original,'subprocess',SimpleNamespace(Popen=lambda *a,**kw:child,TimeoutExpired=subprocess.TimeoutExpired))
    monkeypatch.setattr(original,'observe_created_group',lambda process:{'status':'OBSERVED','actual_pgid':222})
    def failed_birth(process,argv):raise first
    fd=os.open(tmp_path,os.O_RDONLY|os.O_DIRECTORY)
    try:
        with pytest.raises(original.StageUnknown) as caught:original.run(fd,'fixture',['fixture'],tmp_path,{},birth_reader=failed_birth)
        assert caught.value.process is child and caught.value.original_cause is first
        assert list(tmp_path.glob('*.spawn.json')) and list(tmp_path.glob('*.result.json'))
    finally:os.close(fd)


def test_original_postbirth_diagnostic_failure_retains_original_process(tmp_path,monkeypatch):
    child=SimpleNamespace(pid=222,wait=lambda timeout:0)
    monkeypatch.setattr(original,'subprocess',SimpleNamespace(Popen=lambda *a,**kw:child,TimeoutExpired=subprocess.TimeoutExpired))
    monkeypatch.setattr(original,'observe_created_group',lambda process:{'status':'OBSERVED','actual_pgid':222})
    monkeypatch.setattr(original,'group_absent',lambda _:True)
    real=original.write_json
    def broken(fd,name,data):
        if name.endswith('.spawn.json'):raise OSError('ORIGINAL_SPAWN_LOG_FAILURE')
        return real(fd,name,data)
    monkeypatch.setattr(original,'write_json',broken)
    fd=os.open(tmp_path,os.O_RDONLY|os.O_DIRECTORY)
    try:
        with pytest.raises(original.StageUnknown) as caught:original.run(fd,'fixture',['fixture'],tmp_path,{})
        assert caught.value.process is child and 'SPAWN_LOG_FAILURE' in str(caught.value.original_cause)
    finally:os.close(fd)


@pytest.mark.parametrize('report_failure',[False,True])
def test_unknown_bad_exception_text_still_observes_real_original_child(tmp_path,monkeypatch,report_failure):
    class BrokenText(Exception):
        def __str__(self):raise RuntimeError('secondary exception text failure')
    class BrokenUnknown(original.StageUnknown):
        def __str__(self):raise RuntimeError('secondary unknown text failure')
    child=subprocess.Popen([sys.executable,'-I','-S','-c','import time; time.sleep(0.15)'],
                           stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    owner=actor.RuntimeOwner(tmp_path,{},token=actor._OWNER_TOKEN);owner.process=child
    first=BrokenText();unknown=BrokenUnknown('unknown',process=child,token='real-child');unknown.original_cause=first
    observe=original.observe_unknown;calls=[]
    def observed(fd,error,interval):
        assert error is unknown and error.process is child and owner.first_cause is first
        calls.append(child)
        return observe(fd,error,interval=0.01)
    monkeypatch.setattr(original,'observe_unknown',observed)
    if report_failure:
        def fail(*a,**kw):raise BrokenText()
        monkeypatch.setattr(actor,'_helpers',lambda:SimpleNamespace(publish=fail))
        monkeypatch.setattr(original,'fallback',fail)
    fd=os.open(tmp_path,os.O_RDONLY|os.O_DIRECTORY)
    try:
        actor.retain_first_failure(owner,fd,unknown,[])
        assert calls==[child] and child.poll()==0
        assert owner.first_cause is first and owner.process is child and owner.observe_only
        assert len(owner.diagnostic_failures)==(4 if report_failure else 2)
        terminal=json.loads((tmp_path/'real-child.terminal_observation.json').read_bytes())
        assert terminal['stage_acceptance']=='NOT_RUN' and terminal['original_unknown_unchanged']
    finally:
        os.close(fd)
        child.wait(timeout=3)


def test_plan_real_interfaces_scoped_arches_parallelism_and_no_user_home():
    lock=json.loads(sources.SOURCE_SPEC.read_bytes())
    prepared={'sources':{name:{'locked':row} for name,row in lock.items()}}
    plan=actor.build_plan(Path('/private/tmp/cvw26-runtime-fixture'),prepared,'/SDK-fixture')
    assert plan['budget_seconds']==7200 and plan['cleanup_reserve_seconds']==600
    assert len([s for s in plan['stages'] if s['label'].endswith('-build')])==11
    assert all('HOME' not in s['environment'] for s in plan['stages'])
    assert all('-j3' not in s['argv'] for s in plan['stages'])
    assert all('-j2' in s['argv'] for s in plan['stages'] if s['label'].endswith('-build'))
    cpp=next(s for s in plan['stages'] if s['label']=='cpython-configure')
    assert {'TCLTK_CFLAGS','TCLTK_LIBS','GDBM_CFLAGS','GDBM_LIBS','LIBLZMA_CFLAGS','LIBLZMA_LIBS'}<=set(cpp['environment'])
    assert any(s['label']=='runtime-relocation-profile' for s in plan['stages'])


def test_new_selected_source_locks_are_fixed_and_old_pins_preserved():
    lock=json.loads(sources.SOURCE_SPEC.read_bytes())
    assert lock['gdbm']['sha256']=='6a24504a14de4a744103dcb936be976df6fbe88ccff26065e54c1c47946f4a5e'
    assert lock['tcl']['sha256']=='14f9af32b1767ff718477a8f974ad03c34341097e6b43f4ce54644ee974e268e'
    assert lock['tk']['sha256']=='95cd528a80f5e4bdb557af9b14a7197d6860793a3894e25e7c9fad2ed05d4c3c'
    assert lock['python']['sha256']=='c08bc65a81971c1dd5783182826503369466c7e67374d1646519adf05207b684'


def test_actual_wrong_arch_refused_independently_of_fat_bytes():
    first={'platform':'x86_64','provider':{'INFO':[3,5,0,9,0]}}
    with pytest.raises(RuntimeError,match='actual required ARM'):profile.admit_arm_observation(first)
    assert first['platform']=='x86_64' # Observation is retained; never rewritten to expected.
    profile.admit_arm_observation({'platform':'arm64'})


@pytest.mark.parametrize('name',['UniformTypeIdentifiers','MissingFramework','../Escape'])
def test_actual_tk_weak_framework_interface_is_sdk_bound(tmp_path,monkeypatch,name):
    sdk=tmp_path/'SDK';framework=sdk/'System/Library/Frameworks/UniformTypeIdentifiers.framework'
    framework.mkdir(parents=True);monkeypatch.setattr(actor,'SDK_LINK',sdk)
    for arch in ('arm64','x86_64'):
        for component in ('tcl','tk'):
            folder=tmp_path/'install'/arch/component/'lib';folder.mkdir(parents=True)
            content=" -lpthread" if component=='tcl' else ' -weak_framework '+name+' -lpthread'
            p=folder/(component+'Config.sh');p.write_text(component.upper()+"_LIBS='"+content+"'\n");p.chmod(0o600)
    if name=='UniformTypeIdentifiers':
        flags,records=actor.prepared_link_flags(tmp_path)
        assert '-weak_framework UniformTypeIdentifiers' in flags and len(records)==4
    else:
        with pytest.raises(RuntimeError,match='framework'):actor.prepared_link_flags(tmp_path)


@pytest.mark.parametrize('tamper',[False,True])
def test_local_archive_reuse_fresh_identity_no_GET(tmp_path,monkeypatch,tamper):
    old=tmp_path/'old.tar';old.write_bytes(b'locked');old.chmod(0o600)
    row={'sha256':hashlib.sha256(b'locked').hexdigest(),'bytes':6,'url':'https://official.invalid/source'}
    monkeypatch.setattr(sources,'acquire',lambda *a,**kw:pytest.fail('local reuse must not GET'))
    if tamper:old.write_bytes(b'tamper')
    target=tmp_path/'new.tar'
    if tamper:
        with pytest.raises(RuntimeError,match='locked'):sources.reuse_archive(row,old,target)
        assert not target.exists()
    else:
        result=sources.reuse_archive(row,old,target)
        assert result['network_GET']==0 and result['actual_record']['inode']!=result['reused_from_first_record']['inode']
        with pytest.raises(FileExistsError):sources.reuse_archive(row,old,target)


@pytest.mark.parametrize('suffix',['actual',' -unknown',' -weak_framework'])
def test_full_actual_tk_interface_and_closed_flag_grammar(tmp_path,monkeypatch,suffix):
    actual='   -lpthread -framework CoreFoundation -framework Cocoa -framework Carbon -framework IOKit -framework QuartzCore -weak_framework UniformTypeIdentifiers  -lz  -lpthread -framework CoreFoundation '
    sdk=tmp_path/'SDK'
    for name in ('CoreFoundation','Cocoa','Carbon','IOKit','QuartzCore','UniformTypeIdentifiers'):
        (sdk/'System/Library/Frameworks'/(name+'.framework')).mkdir(parents=True)
    monkeypatch.setattr(actor,'SDK_LINK',sdk)
    for arch in ('arm64','x86_64'):
        for component,flags in [('tcl',' -lz  -lpthread -framework CoreFoundation '),('tk',actual if suffix=='actual' else actual+suffix)]:
            folder=tmp_path/'install'/arch/component/'lib';folder.mkdir(parents=True)
            path=folder/(component+'Config.sh');path.write_text(component.upper()+"_LIBS='"+flags+"'\n");path.chmod(0o600)
    if suffix=='actual':
        flags,_=actor.prepared_link_flags(tmp_path)
        assert flags.count('-weak_framework UniformTypeIdentifiers')==1
        assert '-framework UniformTypeIdentifiers' not in flags
    else:
        with pytest.raises(RuntimeError):actor.prepared_link_flags(tmp_path)


@pytest.mark.parametrize('bad',[None,'hardlink','outside-link','world-write','setid','special'])
def test_owned_install_modes_tightened_without_weakening_input_gate(tmp_path,bad,monkeypatch):
    root=tmp_path/'install';root.mkdir(mode=0o700);folder=root/'scripts';folder.mkdir(mode=0o755)
    data=folder/'data';data.write_bytes(b'fixture');data.chmod(0o644)
    executable=folder/'exec';executable.write_bytes(b'fixture');executable.chmod(0o755)
    if bad=='hardlink':os.link(data,folder/'alias')
    elif bad=='outside-link':(folder/'alias').symlink_to('../../outside')
    elif bad=='world-write':data.chmod(0o666)
    elif bad=='setid':
        # macOS may clear unprivileged setid attempts; inject the metadata boundary.
        original_lstat=Path.lstat
        def privileged_lstat(path):
            actual=original_lstat(path)
            return SimpleNamespace(st_mode=actual.st_mode|0o4000,st_uid=actual.st_uid,st_nlink=actual.st_nlink) if path==data else actual
        monkeypatch.setattr(Path,'lstat',privileged_lstat)
    elif bad=='special':os.mkfifo(folder/'fifo',0o600)
    if bad:
        with pytest.raises((RuntimeError,OSError)):actor.privatize_installed_tree(root)
        assert folder.stat().st_mode&0o077 # Whole preflight failed before changing modes.
    else:
        with pytest.raises(Exception):_helpers().secure_dir(folder)
        actor.privatize_installed_tree(root)
        _helpers().secure_dir(folder)
        assert data.stat().st_mode&0o777==0o600 and executable.stat().st_mode&0o777==0o700
        assert data.read_bytes()==b'fixture'


def test_install_mode_member_injection_refused(tmp_path,monkeypatch):
    root=tmp_path/'install';root.mkdir(mode=0o700);data=root/'data';data.write_bytes(b'fixture');data.chmod(0o644)
    real=os.fchmod
    def inject(fd,mode):
        real(fd,mode);(root/'injected').write_bytes(b'not original output')
    monkeypatch.setattr(actor.os,'fchmod',inject)
    with pytest.raises(RuntimeError,match='membership'):actor.privatize_installed_tree(root)


def public_cert_fixture(tmp_path):
    from tools.w26_prepare_current_inputs import _helpers
    helper=_helpers();folder=tmp_path/'runtime-certfixtures';folder.mkdir(mode=0o700)
    source=Path(__file__).parent/'fixtures/w26_public_tls'
    for name in (*tls.FIXTURE_BYTES,'keycert.pem'):helper.copy_blob(folder/name,(source/name).read_bytes())
    helper.publish(tmp_path/'CERT_FIXTURE_INPUTS.json',tls.FIXTURE_BYTES)
    return folder


def test_original_public_cert_cases_actual_host_software_boundary(tmp_path):
    import ssl
    public_cert_fixture(tmp_path);result=tls.runtime_cert_cases(ssl,tmp_path)
    assert len(result['cases'])==4 and result['malformed_negative'] and result['socket_calls']==0


def test_original_public_cert_tamper_refused(tmp_path):
    import ssl
    folder=public_cert_fixture(tmp_path);(folder/'allsans.pem').write_bytes(b'tamper')
    with pytest.raises(RuntimeError,match='fixture'):tls.runtime_cert_cases(ssl,tmp_path)


def test_actual_host_memorybio_no_socket_software_control(tmp_path,monkeypatch):
    import ssl,socket
    folder=public_cert_fixture(tmp_path)
    monkeypatch.setattr(socket,'socket',lambda *a,**kw:pytest.fail('MemoryBIO must not create sockets'))
    result=tls.actual_memorybio_cases(ssl,folder/'keycert.pem')
    assert [x['required_version'] for x in result['actual_cases']]==['TLSv1.2','TLSv1.3']
    assert all(x['encrypted_payload_roundtrip'] for x in result['actual_cases'])


def test_script_producer_membership_content_and_scope_refusal(tmp_path):
    folder=tmp_path/'install/tcl';folder.mkdir(parents=True,mode=0o700);p=folder/'init.tcl';p.write_bytes(b'fixture');p.chmod(0o600)
    manifest=[actor.script_closure(tmp_path,folder)]
    profile.verified_scripts(manifest,folder,tmp_path/'install')
    (folder/'injected').write_bytes(b'extra')
    with pytest.raises(RuntimeError,match='membership'):profile.verified_scripts(manifest,folder,tmp_path/'install')
    with pytest.raises(RuntimeError,match='escaped'):profile.verified_scripts(manifest,folder,tmp_path/'different-install')


def test_original_provider_full_text_not_just_version_prefix():
    actual={'INFO':[3,5,0,9,0],'NUMBER':0x30500090,'TEXT':'OpenSSL 3.5.9 29 Sep 2026'}
    profile.admit_provider(actual)
    for key,value in [('TEXT','OpenSSL 3.5.9 forged'),('INFO',[3,5,0,9,1]),('NUMBER',0x30500080)]:
        changed={**actual,key:value}
        with pytest.raises(RuntimeError,match='exact original SSL359'):profile.admit_provider(changed)


def test_core_tcl_install_preserves_standard_modules_messages_timezones():
    lock=json.loads(sources.SOURCE_SPEC.read_bytes());prepared={'sources':{k:{'locked':v} for k,v in lock.items()}}
    plan=actor.build_plan(Path('/private/tmp/cvw26-runtime-fixture'),prepared,'/SDK-fixture')
    for stage in plan['stages']:
        if stage['label'].startswith('tcl-') and stage['label'].endswith('-install'):
            assert stage['argv'][1:]==['install-binaries','install-libraries','install-headers','install-msgs','install-tzdata']
        if stage['label'].startswith('tk-') and stage['label'].endswith('-install'):
            assert stage['argv'][1:]==['install-binaries','install-libraries','install-headers']
