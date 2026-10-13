"""Actual complete prepare copier with public synthetic upstream SDK seams."""
import hashlib,json,os
from pathlib import Path
import pytest
import openssl359_sources_r2 as source
import openssl359_successor_r2 as unit
import tls_cert_checks_r4 as certs
import openssl359_memorybio_r1 as bio

NAMES=('allsans.pem','nullbytecert.pem','nokia.pem','badcert.pem','keycert.pem')
def pin(raw):return {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
def blob(path,raw):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw);path.chmod(0o600)

def prepare_fixture(patch,tmp_path,target=None,mutation=None):
    prefix=tmp_path/'synthetic-owned-prefix';prefix.mkdir(mode=0o700)
    x=unit.layout(prefix);x['root'].mkdir(mode=0o700)
    originals={name:('PUBLIC SYNTHETIC FIXED '+name+'\n').encode() for name in NAMES}
    wanted={name:pin(raw) for name,raw in originals.items()};accepted=[]
    patch.setattr(source,'PREFIX',prefix);patch.setattr(source,'INPUT_ROOT',tmp_path/'synthetic-auth-input')
    blob(source.INPUT_ROOT/'archives/openssl-3.5.9.tar.gz',b'PUBLIC inert SDK placeholder; no source tar extraction')
    blob(prefix/'archives'/source.cp_sources.CP_ARCHIVE['filename'],b'PUBLIC inert CP archive placeholder; no tar extraction')
    patch.setattr(source,'authenticated_content',lambda:{'synthetic_archive_phase_only':True})
    patch.setattr(source.cp_sources,'verify_archive_fd',lambda *args:{'scope':'public synthetic SDK seam, no official auth/unpack proof'})
    def openssl(fd,destination,content):
        root=destination/'openssl-3.5.9';root.mkdir(parents=True,mode=0o700)
        blob(root/'public-source.txt',b'PUBLIC SYNTHETIC SOURCE; never executed')
        return {'source_root':str(root),'tree':source.deps.tree_record(root),'scope':'synthetic source seam; actual strict work-copy code executes'}
    patch.setattr(source,'extract_exact_source',openssl)
    patch.setattr(source.cp_sources,'inventory',lambda *args:[])
    ssl_source=b'PUBLIC SYNTHETIC CP _ssl.c; never compiled'
    patch.setattr(source,'CP_SSL_SHA',hashlib.sha256(ssl_source).hexdigest())
    def cp(*args):
        blob(x['cp_source']/'Modules/_ssl.c',ssl_source)
        for name,raw in originals.items():blob(x['cp_source']/'Lib/test/certdata'/name,raw)
        return {'scope':'synthetic source seam; no actual tar extraction'}
    patch.setattr(source.cp_sources,'extract_pristine',cp)
    patch.setattr(certs,'FIXTURE_BYTES',{name:wanted[name] for name in NAMES[:-1]});patch.setattr(bio,'CERT_PIN',wanted['keycert.pem'])
    if target:
        external=tmp_path/'outside-owned-prefix-public-fixture';replacement=b'X'*len(originals[target]);blob(external,replacement)
        original=certs.checked_fixture
        def first_then_change(path,expected):
            first=original(path,expected)
            if path==x['cp_source']/'Lib/test/certdata'/target:
                accepted.append(first)
                if mutation=='inode':
                    incoming=tmp_path/'public-replacement-inode';blob(incoming,replacement);os.replace(incoming,path)
                elif mutation=='symlink':path.unlink();path.symlink_to(external)
                elif mutation=='bytes':path.write_bytes(replacement)
                else:raise AssertionError('unknown synthetic mutation')
            return first
        patch.setattr(certs,'checked_fixture',first_then_change)
    return x,originals,wanted,accepted

def test_actual_prepare_complete_five_fixed_destinations_positive(monkeypatch,tmp_path):
    x,originals,wanted,accepted=prepare_fixture(monkeypatch,tmp_path)
    result=source.prepare_sources(x)
    assert len(result['accepted_fixture_copies'])==5
    for row in result['accepted_fixture_copies']:
        name=Path(row['source_first_accepted']['path']).name
        assert row['source_first_accepted']['sha256']==wanted[name]['sha256']
        assert row['destination_fixed_verified']['sha256']==wanted[name]['sha256']
        assert row['exact_accepted_raw_copied']
        assert (x['root']/'runtime-certfixtures'/name).read_bytes()==originals[name]

@pytest.mark.parametrize('target',NAMES)
@pytest.mark.parametrize('mutation',('inode','symlink','bytes'))
def test_actual_prepare_first_acceptance_change_refuses_no_wrong_destination(monkeypatch,tmp_path,target,mutation):
    x,originals,wanted,accepted=prepare_fixture(monkeypatch,tmp_path,target,mutation)
    with pytest.raises((RuntimeError,OSError)):source.prepare_sources(x)
    assert len(accepted)==1 and accepted[0]['sha256']==wanted[target]['sha256']
    assert not (x['root']/'runtime-certfixtures'/target).exists()
    assert not (x['root']/'OFFICIAL_INPUTS_PREPARED.json').exists()
    for name in NAMES:
        path=x['root']/'runtime-certfixtures'/name
        if path.exists():assert path.read_bytes()==originals[name]

def test_r2_thirteen_commands_only_fixed_namespace_and_helper_successors():
    import openssl359_successor_r1 as old
    plan,env=unit.command_plan(unit.layout());prior,_=old.command_plan(old.layout())
    assert unit.UNIT_CAP==1800 and unit.RESERVE==300 and len(plan)==13
    for new,before in zip(plan,prior):
        assert new[0]==before[0]
        assert [a.replace(str(unit.layout()['root']),str(old.layout()['root'])) for a in new[1]]==before[1]
    text=Path(unit.__file__).read_text()
    assert 'openssl359_sources_r2' in text and 'openssl359_merge_r2.py' in text and 'openssl359_verify_r2.py' in text
    assert 'openssl359_runtime_profile_r1.py' in text
