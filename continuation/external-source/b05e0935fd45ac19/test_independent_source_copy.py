"""Actual prepare_sources copier; only earlier archive SDK phases use public synthetic fixtures."""
import hashlib,json,os,stat
from pathlib import Path
import pytest
import openssl359_sources_r1 as source
import openssl359_successor_r1 as unit
import tls_cert_checks_r4 as certs
import openssl359_memorybio_r1 as bio
from acquire_exact_archives import Refusal

OUT=Path(__file__).parent
NAMES=('allsans.pem','nullbytecert.pem','nokia.pem','badcert.pem','keycert.pem')
def pin(raw):return {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
def blob(path,raw):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw);path.chmod(0o600)


def test_actual_production_prepare_copy_reopens_validated_fixed_fixture_and_accepts_replacement(monkeypatch,tmp_path):
    records=[]
    for target_name in NAMES:
        fixture=tmp_path/target_name.replace('.pem','');fixture.mkdir()
        prefix=fixture/'synthetic-owned-prefix';prefix.mkdir(mode=0o700)
        x=unit.layout(prefix);x['root'].mkdir(mode=0o700)
        originals={name:('PUBLIC SYNTHETIC FIXED '+name+'\n').encode() for name in NAMES}
        wanted={name:pin(data) for name,data in originals.items()}
        external=fixture/'outside-synthetic-prefix-public-bytes'
        replacement=('PUBLIC NONFIXED COPIER REPLACEMENT '+target_name+'\n').encode()
        blob(external,replacement)
        accepted=[]
        with monkeypatch.context() as patch:
            patch.setattr(source,'PREFIX',prefix)
            patch.setattr(source,'INPUT_ROOT',fixture/'synthetic-auth-input')
            blob(source.INPUT_ROOT/'archives/openssl-3.5.9.tar.gz',b'PUBLIC inert SDK archive placeholder; no tar extraction')
            blob(prefix/'archives'/source.cp_sources.CP_ARCHIVE['filename'],b'PUBLIC inert CP archive placeholder; no tar extraction')
            patch.setattr(source,'authenticated_content',lambda:{'synthetic_archive_phase_only':True})
            def before_archive(fd,expected):
                return {'scope':'PUBLIC SYNTHETIC SDK PHASE ONLY; no official auth/unpack/runtime proof'}
            patch.setattr(source.cp_sources,'verify_archive_fd',before_archive)
            def synthetic_openssl(fd,destination,content):
                root=destination/'openssl-3.5.9';root.mkdir(parents=True,mode=0o700)
                blob(root/'public-source.txt',b'PUBLIC SYNTHETIC SOURCE; never executed')
                return {'source_root':str(root),'tree':source.deps.tree_record(root),
                    'scope':'Synthetic upstream extraction boundary; actual safe-copy still executes'}
            patch.setattr(source,'extract_exact_source',synthetic_openssl)
            patch.setattr(source.cp_sources,'inventory',lambda prefix:[])
            ssl_source=b'PUBLIC SYNTHETIC CP _ssl.c; never compiled'
            patch.setattr(source,'CP_SSL_SHA',hashlib.sha256(ssl_source).hexdigest())
            def synthetic_cp(fd,destination,rows,members,expanded):
                blob(x['cp_source']/'Modules/_ssl.c',ssl_source)
                for name,data in originals.items():blob(x['cp_source']/'Lib/test/certdata'/name,data)
                return {'scope':'Synthetic upstream extraction boundary; no actual tar unpack'}
            patch.setattr(source.cp_sources,'extract_pristine',synthetic_cp)
            patch.setattr(certs,'FIXTURE_BYTES',{name:wanted[name] for name in NAMES[:-1]})
            patch.setattr(bio,'CERT_PIN',wanted['keycert.pem'])
            original_check=certs.checked_fixture
            def check_then_replace(path,expected):
                first=original_check(path,expected)
                if path.name==target_name:
                    accepted.append(first)
                    if target_name=='keycert.pem':
                        path.unlink();path.symlink_to(external)
                    else:
                        incoming=fixture/'public-inode-replacement'
                        blob(incoming,replacement);os.replace(incoming,path)
                return first
            patch.setattr(certs,'checked_fixture',check_then_replace)
            # Actual production copier and original copy_blob/checkpoint/file_record
            # execute unchanged. No patch is made to their acceptance or reads.
            result=source.prepare_sources(x)
            destination=x['root']/'runtime-certfixtures'/target_name
            assert destination.read_bytes()==replacement and destination.read_bytes()!=originals[target_name]
            assert len(accepted)==1 and accepted[0]['sha256']==wanted[target_name]['sha256']
            assert result['fixed_public_MemoryBIO_certificate']==wanted['keycert.pem']
            records.append({'target':target_name,
                'first_real_same_FD_fixed_reader':accepted[0],
                'replacement':'post-validation symlink to external public synthetic file' if target_name=='keycert.pem'
                    else 'post-validation regular inode with nonfixed public synthetic bytes',
                'actual_dest_bytes_sha256':hashlib.sha256(destination.read_bytes()).hexdigest(),
                'original_fixed_bytes_sha256':wanted[target_name]['sha256'],
                'actual_production_copy_completed':True,'dest_fixed_binding_matches':False,
                'source_program_executed':False,'tar_extraction_native_compile':0})
    (OUT/'ACTUAL_PREPARE_SOURCE_PINNED_CERT_COPY_RESIDUAL.json').write_text(json.dumps({
        'status':'REPRODUCED_P2_SOURCE_SOFTWARE_ONLY','actual_production_consumer':'openssl359_sources_r1.prepare_sources',
        'real_boundary_methods':['tls_cert_checks_r4.checked_fixture','intel_deps_stages.file_record',
            'intel_crypto_successor_r1.checkpoint_bytes','tls_cpython_checkpoint_r4.copy_blob'],
        'cases':records,'fixture_data':'Only public synthetic bytes; no user data, official archives, old tree or fresh real unit touched',
        'earlier_archive_auth_and_extract_phases':'Disclosed synthetic SDK boundary seams; no official source proof claimed',
        'new_real_OpenSSL359_native_runtime':'NOT_RUN'},indent=2)+'\n')


def test_actual_rc0_build_log_ERROR_and_official_build_python_parser_stop_before_install():
    import tls_cpython_successor_r4 as old
    x=old.layout();old_result=json.loads((x['root']/'RESULT.json').read_bytes())
    command=next(row for row in old_result['commands'] if row['argv']==['/usr/bin/make','-j2'])
    assert command['rc']==0 and command['owned_process_terminal'] and command['owned_group_absent']
    with pytest.raises(Refusal,match='actual build helper reported required TLS module ERROR'):
        unit.required_build_outputs(x,command)
    makefile=(x['build']/'Makefile').read_bytes()
    assert unit.build_python_basename(makefile)=='python.exe'
    for bad in (makefile.replace(b'python$(BUILDEXE)',b'../../outside'),
                makefile+b'\nBUILDEXE=.exe\n',b'BUILDPYTHON=python\nBUILDEXE=\n'):
        with pytest.raises(Refusal):unit.build_python_basename(bad)
    (OUT/'ACTUAL_R4_RC0_REQUIRED_ERROR_AND_MAKEFILE_GATE.json').write_text(json.dumps({
        'status':'PASS_SCOPED_SOURCE_SOFTWARE','actual_original_R4_make_rc':0,
        'actual_original_helper_required_module_error_refused':True,'original_official_BUILDPYTHON':'python$(BUILDEXE)',
        'actual_BUILD_EXE_suffix':'.exe','path_guess_and_ambiguous_suffix_refused':True,
        'new_install_or_native_dispatch':0,'new_OpenSSL359_runtime':'NOT_RUN'},indent=2)+'\n')
