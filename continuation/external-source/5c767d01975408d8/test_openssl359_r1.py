"""New production source-preparation and real host-planning controls only."""
import hashlib,io,json,os,ssl,tarfile
from pathlib import Path
import pytest
from acquire_exact_archives import Refusal
import openssl359_sources_r1 as source
import openssl359_successor_r1 as unit
import openssl359_merge_r1 as merge
import openssl359_memorybio_r1 as bio

def synthetic_archive(tmp_path,rows):
    path=tmp_path/'synthetic.tar';members=[]
    with tarfile.open(path,'w') as archive:
        for name,body,executable in rows:
            member=tarfile.TarInfo(name);member.mode=0o755 if executable else 0o644
            if body is None:member.type=tarfile.DIRTYPE;member.mode=0o755
            else:member.size=len(body)
            archive.addfile(member,None if body is None else io.BytesIO(body))
            record={'relative_path':name,'kind':'directory' if body is None else 'regular','mode':0o700 if body is None or executable else 0o600}
            if body is not None:record.update(bytes=len(body),sha256=hashlib.sha256(body).hexdigest())
            members.append(record)
    return path,{'members':len(rows),'expanded_bytes':sum(len(body) for name,body,executable in rows if body is not None),'rows':sorted(members,key=lambda row:row['relative_path'])}

def test_actual_authenticated_source_and_r4_preserved_readonly():
    content=source.authenticated_content();old=source.verify_failed_r4()
    assert content['members']==6078 and content['expanded_bytes']==131230833 and not content['links']
    assert old['old_failed_r4_unchanged'] and old['records']==14185

def test_actual_production_safeextract_complete_byte_mode_closure(tmp_path):
    rows=[('openssl-3.5.9',None,True),('openssl-3.5.9/a',None,True),('openssl-3.5.9/a/file',b'fixture',False),('openssl-3.5.9/a.md',b'public',False),('openssl-3.5.9/run',b'never executed',True)]
    path,content=synthetic_archive(tmp_path,rows);fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:result=source.extract_exact_source(fd,tmp_path/'destination',content)
    finally:os.close(fd)
    assert result['unpack']['members']==5 and result['source_programs_executed']==0
    assert (tmp_path/'destination/openssl-3.5.9/run').stat().st_mode&0o777==0o700

def test_production_extract_wrong_content_preserves_partial_and_refuses(tmp_path):
    path,content=synthetic_archive(tmp_path,[('openssl-3.5.9',None,True),('openssl-3.5.9/a',b'exact bytes',False)])
    content['rows'][1]['sha256']='0'*64;fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        with pytest.raises(Refusal):source.extract_exact_source(fd,tmp_path/'destination',content)
    finally:os.close(fd)
    assert (tmp_path/'destination/openssl-3.5.9/a').read_bytes()==b'exact bytes'

def test_escape_production_extract_no_external_effect(tmp_path):
    path,content=synthetic_archive(tmp_path,[('openssl-3.5.9',None,True),('../outside',b'public',False)])
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        with pytest.raises(Refusal):source.extract_exact_source(fd,tmp_path/'destination',content)
    finally:os.close(fd)
    assert not (tmp_path/'outside').exists()

def test_fixed_merge_namespace_refused_before_spawn(monkeypatch,tmp_path):
    called=[];monkeypatch.setattr(merge.subprocess,'Popen',lambda *a,**k:called.append(a))
    with pytest.raises(Refusal):merge.merge(tmp_path,tmp_path/'MERGE_INPUTS.json',9999999999,'0'*64)
    assert called==[]

def test_original_cp_commands_two_explicit_targets_no_macro_fallback():
    import tls_cpython_successor_r4 as old
    plan,env=unit.command_plan(unit.layout());previous,_=old.command_plan(old.layout())
    assert len(plan)==13 and unit.UNIT_CAP==1800 and unit.RESERVE==300
    for new,prior in zip(plan[7:],previous):
        assert new[0]==prior[0]
        assert [a.replace(str(unit.layout()['root']),str(old.layout()['root'])) for a in new[1]]==prior[1]
    for configuration in (plan[0],plan[3]):
        assert '--libdir=lib' in configuration[1]
        assert not any('no-deprecated' in a or 'OPENSSL_NO_' in a for a in configuration[1])
        assert unit.cp.CLANG in ' '.join(configuration[1]) and '-mmacosx-version-min=11.0' in ' '.join(configuration[1])
    assert len({unit.stage_done_record_name(label).casefold() for label,argv,cwd in plan})==13
    assert 'PY_UNSUPPORTED_OPENSSL_BUILD' not in env

def test_real_host_memorybio_planning_both_versions_no_socket():
    certificate=source.PREFIX/'sources'/source.cp_sources.CP_RELATIVE/'Lib/test/certdata/keycert.pem'
    result=bio.actual_memorybio_cases(ssl,certificate)
    assert [r['client_version'] for r in result['actual_cases']]==['TLSv1.2','TLSv1.3']
    assert all(r['encrypted_payload_roundtrip'] for r in result['actual_cases'])
    assert result['network_connections']==result['sockets_created']==0
    # Existing host OpenSSL3.6.4 is only planning evidence; new3.5.9 is NOT_RUN.

def test_actual_memorybio_wrong_fixed_fixture_and_deadline_refused(tmp_path):
    path=tmp_path/'wrong.pem';path.write_bytes(b'public tampered fixture');path.chmod(0o600)
    with pytest.raises(RuntimeError):bio.actual_memorybio_cases(ssl,path)
    certificate=source.PREFIX/'sources'/source.cp_sources.CP_RELATIVE/'Lib/test/certdata/keycert.pem'
    times=iter([0,16])
    with pytest.raises(RuntimeError):bio.actual_memorybio_cases(ssl,certificate,clock=lambda:next(times))

def test_production_preservation_r4_failure_stays_false_no_dispatch(monkeypatch):
    def fail():raise Refusal('synthetic original R4 changed')
    monkeypatch.setattr(source,'preservation_verified_summary',fail)
    called=[];monkeypatch.setattr(unit.cp,'run',lambda *a,**k:called.append(a))
    errors=unit.cp.DiagnosticErrors();status,facts=unit.preservation_outcome(source.PREFIX/'intel-deps-r2',[],{},'TLS_HASHLIB_SCOPED_ACTUAL_PASS',errors,unit.Budget())
    assert not facts['old_roots_unchanged'] and facts['preservation_failure']
    assert 'PASS' not in status and called==[] and errors


def test_production_merge_reader_parses_same_pinned_raw_despite_replacement(monkeypatch,tmp_path):
    path=tmp_path/'MERGE_INPUTS.json';path.write_text('{"fixed": "original pinned raw"}');path.chmod(0o600);pin=unit.record(path)
    actual_loads=json.loads
    def replace_between_read_and_parse(raw):
        replacement=tmp_path/'replacement';replacement.write_text('{"fixed": "wrong replacement"}');replacement.chmod(0o600);os.replace(replacement,path)
        return actual_loads(raw)
    monkeypatch.setattr(unit.json,'loads',replace_between_read_and_parse)
    result=unit.read_merge_inputs({'root':tmp_path},pin)
    assert result=={'fixed':'original pinned raw'}
    with pytest.raises(Refusal):unit.read_merge_inputs({'root':tmp_path},pin)
