"""Real full R2 prepare copier, public synthetic upstream SDK boundary seams only."""
from pathlib import Path
import hashlib,json,os
import pytest
import openssl359_sources_r2 as source
import tls_cert_checks_r4 as certs
import tls_cpython_checkpoint_r4 as copier
from test_openssl359_cert_copy_r2 import prepare_fixture,NAMES,blob

OUT=Path(__file__).parent

def save(name,value):(OUT/name).write_text(json.dumps(value,indent=2)+'\n')

def test_actual_complete_R2_first_source_pin_to_checkpoint_copy_and_fixed_destinations(monkeypatch,tmp_path):
    x,originals,wanted,_=prepare_fixture(monkeypatch,tmp_path)
    result=source.prepare_sources(x);copies=result['accepted_fixture_copies']
    assert len(copies)==5 and {Path(row['source_first_accepted']['path']).name for row in copies}==set(NAMES)
    records=[]
    for row in copies:
        first=row['source_first_accepted'];dest=row['destination_fixed_verified'];name=Path(first['path']).name
        origin=x['cp_source']/'Lib/test/certdata'/name;target=x['root']/'runtime-certfixtures'/name
        assert first['path']==str(origin) and first['identity']=={'device':origin.stat().st_dev,'inode':origin.stat().st_ino}
        assert first['bytes']==wanted[name]['bytes'] and first['sha256']==wanted[name]['sha256']
        assert first['kind']=='regular' and first['mode']==0o600
        assert dest['path']==str(target) and dest['sha256']==wanted[name]['sha256']
        assert target.read_bytes()==originals[name] and row['exact_accepted_raw_copied']
        assert row['fixed']==wanted[name]
        records.append({'name':name,'actual_source_first_pin':first,'actual_destination_fixed_pin':dest,
            'raw_matches_original_fixed_bytes':True})
    assert (x['root']/'OFFICIAL_INPUTS_PREPARED.json').is_file()
    save('ACTUAL_COMPLETE_PREPARE_FIRST_PIN_AND_DESTINATION_POSITIVE.json',{
        'status':'PASS_SCOPED_SOURCE_SOFTWARE','producer':'openssl359_sources_r2.prepare_sources',
        'five_actual_source_checkpoint_and_copy_destinations':records,
        'archive_auth_and_extract_phases':'Disclosed public synthetic SDK seams; no real tar extraction/auth/runtime proof',
        'native_unpack_compile_runtime':0})


def test_actual_complete_prepare_rejects_all_first_reader_and_destination_replacements(monkeypatch,tmp_path):
    records=[]
    for phase in ('source_inode','source_symlink','source_bytes','destination_bytes'):
        for name in NAMES:
            folder=tmp_path/(phase+'-'+name);folder.mkdir()
            with monkeypatch.context() as patch:
                x,originals,wanted,_=prepare_fixture(patch,folder)
                target_source=x['cp_source']/'Lib/test/certdata'/name
                target_destination=x['root']/'runtime-certfixtures'/name
                nonfixed=b'Q'*len(originals[name]);accepted=[];mutated=[]
                original_reader=certs.checked_fixture
                if phase.startswith('source_'):
                    external=folder/'outside-owned-prefix-public-synthetic';blob(external,nonfixed)
                    def first_then_change(path,expected):
                        first=original_reader(path,expected)
                        if path==target_source:
                            accepted.append(first)
                            if phase=='source_inode':
                                replacement=folder/'public-new-inode';blob(replacement,nonfixed);os.replace(replacement,path)
                            elif phase=='source_symlink':path.unlink();path.symlink_to(external)
                            else:path.write_bytes(nonfixed)
                            mutated.append(str(path))
                        return first
                    patch.setattr(certs,'checked_fixture',first_then_change)
                else:
                    original_writer=copier.copy_blob
                    def write_then_corrupt(path,raw):
                        original_writer(path,raw)
                        if path==target_destination:
                            path.write_bytes(nonfixed);mutated.append(str(path))
                    patch.setattr(copier,'copy_blob',write_then_corrupt)
                with pytest.raises((RuntimeError,OSError)) as failure:source.prepare_sources(x)
                assert mutated==[str(target_source if phase.startswith('source_') else target_destination)]
                assert not (x['root']/'OFFICIAL_INPUTS_PREPARED.json').exists()
                if phase.startswith('source_'):
                    assert len(accepted)==1 and accepted[0]['sha256']==wanted[name]['sha256']
                    assert not target_destination.exists()
                else:
                    assert target_destination.read_bytes()==nonfixed
                for other in NAMES:
                    path=x['root']/'runtime-certfixtures'/other
                    if path.exists() and not (phase=='destination_bytes' and other==name):
                        assert path.read_bytes()==originals[other]
                records.append({'phase':phase,'fixture':name,'actual_exception_type':type(failure.value).__name__,
                    'actual_exception':str(failure.value),'first_source_pin':accepted,
                    'nonfixed_source_copy_prevented':phase.startswith('source_'),
                    'destination_tamper_refused':phase=='destination_bytes',
                    'prepared_success_not_published':True})
    assert len(records)==20
    save('ACTUAL_COMPLETE_PREPARE_SOURCE_AND_DESTINATION_ADVERSARIAL.json',{
        'status':'PASS_SCOPED_SOURCE_SOFTWARE','actual_producer':'openssl359_sources_r2.prepare_sources',
        'source_mutation_cases':15,'destination_after_original_copy_mutation_cases':5,'cases':records,
        'actual_unmodified_methods':['checked_fixture','checkpoint_bytes','copy_blob (except disclosed post-write mutation seam)'],
        'only_public_synthetic_data':True,'actual_user_data_native_archive_unpack_compile':0})
