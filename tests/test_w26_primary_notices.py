"""Portable synthetic controls; official notice validation is recorded separately."""
import hashlib,io,json,tarfile
from pathlib import Path
import pytest
from tools import w26_primary_notices as notices

@pytest.fixture
def notice_fixture(tmp_path,monkeypatch):
    text=b'Synthetic license fixture, not a legal or primary-source acceptance.\n';text=text+b' '*(1249-len(text));components=[]
    def save(name,raw):
        p=tmp_path/name;p.write_bytes(raw);return {'path':str(p),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
    for name in sorted(notices.NAMES):
        member=name.replace('-','_')+'-12.2.2/License.txt';buffer=io.BytesIO()
        with tarfile.open(fileobj=buffer,mode='w:gz') as archive:
            item=tarfile.TarInfo(member);item.size=len(text);archive.addfile(item,io.BytesIO(text))
        sdist=save(name+'.tgz',buffer.getvalue());url='https://files.pythonhosted.org/synthetic/'+name+'.tgz'
        metadata=save(name+'.json',json.dumps({'info':{'name':name,'version':'12.2.2'},'urls':[{'url':url,'size':sdist['bytes'],'digests':{'sha256':sdist['sha256']}}]}).encode())
        components.append({'name':name,'version':'12.2.2','exact_metadata_url':'https://pypi.org/pypi/'+name+'/12.2.2/json','sdist_url':url,'metadata':metadata,'verified_sdist':sdist,'license_file':{**save(name+'.txt',text),'archive_member':member},'wheel_sha256':'a'*64})
    value={'schema':'CVW26_EXACT_PYOBJC_PRIMARY_NOTICE_INPUTS_V1','fixture':'SYNTHETIC_PORTABLE_NOT_PRIMARY','components':components};manifest=tmp_path/'manifest.json';manifest.write_text(json.dumps(value));sha=hashlib.sha256(manifest.read_bytes()).hexdigest();monkeypatch.setattr(notices,'MANIFEST_SHA',sha);monkeypatch.setattr(notices,'LICENSE_SHA',hashlib.sha256(text).hexdigest())
    return manifest,sha,value

def test_notice_payload_public_text_and_sanitized_provenance_only(notice_fixture):
    manifest,sha,_=notice_fixture;rows=notices.notice_payloads(manifest,expected_sha=sha)
    assert set(rows)==notices.NAMES and all(len(r['license_bytes'])==1249 for r in rows.values())
    for row in rows.values():
        assert row['provenance']['separate_product_notice_retained'] is True
        assert not any(key=='path' for key in row['provenance'])

@pytest.mark.parametrize('tamper',['manifest','metadata','sdist','license','symlink'])
def test_primary_notice_exact_records_tamper_refused(notice_fixture,tamper,tmp_path):
    manifest,sha,value=notice_fixture
    if tamper=='manifest':manifest.write_bytes(b'{}')
    else:
        key={'metadata':'metadata','sdist':'verified_sdist','license':'license_file','symlink':'license_file'}[tamper];p=Path(value['components'][0][key]['path'])
        if tamper=='symlink':p.rename(tmp_path/'retained');p.symlink_to(tmp_path/'retained')
        else:p.write_bytes(b'tampered')
    with pytest.raises(Exception):notices.notice_payloads(manifest,expected_sha=sha)
    assert not (tmp_path/'notices').exists()

def test_caller_supplied_unknown_manifest_hash_cannot_mint_notice(notice_fixture):
    manifest,_,_=notice_fixture
    with pytest.raises(RuntimeError,match='unknown'):notices.notice_payloads(manifest,expected_sha='0'*64)

@pytest.mark.parametrize('kind',['traversal','symlink','hardlink','duplicate'])
def test_primary_source_container_links_traversal_and_duplicate_refused(notice_fixture,monkeypatch,kind):
    manifest,_,value=notice_fixture;row=value['components'][0];text=Path(row['license_file']['path']).read_bytes();member=row['license_file']['archive_member'];buffer=io.BytesIO()
    with tarfile.open(fileobj=buffer,mode='w:gz') as archive:
        first=tarfile.TarInfo(member);first.size=len(text);archive.addfile(first,io.BytesIO(text))
        item=tarfile.TarInfo(member if kind=='duplicate' else '../outside' if kind=='traversal' else 'unsafe')
        if kind in {'symlink','hardlink'}:item.type=tarfile.SYMTYPE if kind=='symlink' else tarfile.LNKTYPE;item.linkname=member;archive.addfile(item)
        else:item.size=1;archive.addfile(item,io.BytesIO(b'x'))
    raw=buffer.getvalue();sdist=Path(row['verified_sdist']['path']);sdist.write_bytes(raw);row['verified_sdist'].update(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest());m=Path(row['metadata']['path']);metadata=json.loads(m.read_bytes());metadata['urls'][0].update(size=len(raw),digests={'sha256':hashlib.sha256(raw).hexdigest()});m.write_text(json.dumps(metadata));row['metadata'].update(bytes=m.stat().st_size,sha256=hashlib.sha256(m.read_bytes()).hexdigest());manifest.write_text(json.dumps(value));sha=hashlib.sha256(manifest.read_bytes()).hexdigest();monkeypatch.setattr(notices,'MANIFEST_SHA',sha)
    with pytest.raises(RuntimeError,match='traversal/link/membership'):notices.notice_payloads(manifest,expected_sha=sha)

@pytest.mark.parametrize('tamper',[None,'wheel-binding','notice-bytes','metadata-provenance'])
def test_existing_release_bundle_rechecks_primary_notice_payloads(notice_fixture,monkeypatch,tmp_path,tamper):
    """Actual release manifest/package APIs; synthetic licenses do not qualify inputs."""
    import zipfile
    from tools import full_project_release as release
    from tests.test_full_project_release import make_wheel,make_build_evidence
    manifest,_,value=notice_fixture;house=tmp_path/'wheelhouse';pins=[]
    for row in value['components']:
        wheel=house/(row['name'].replace('-','_')+'-12.2.2-py3-none-any.whl')
        sha=make_wheel(wheel,name=row['name'],version='12.2.2');row['wheel_sha256']=sha
        pins.append(row['name']+'==12.2.2 --hash=sha256:'+sha)
    app=house/'comsol_mcp-0.1.10-py3-none-any.whl';appsha=make_wheel(app,name='comsol-mcp',version='0.1.10');pins.append('comsol-mcp==0.1.10 --hash=sha256:'+appsha)
    if tamper=='wheel-binding':value['components'][0]['wheel_sha256']='0'*64
    manifest.write_text(json.dumps(value));sha=hashlib.sha256(manifest.read_bytes()).hexdigest();monkeypatch.setattr(notices,'MANIFEST_SHA',sha)
    # Bind fixture-only default argument without granting caller supplied hash trust.
    original=notices.notice_payloads
    monkeypatch.setattr(notices,'notice_payloads',lambda path:original(path,expected_sha=sha))
    lock=tmp_path/'requirements.lock';lock.write_text('\n'.join(pins)+'\n');uv=tmp_path/'uv.lock';uv.write_text('version=1\n');metadata=tmp_path/'metadata'
    if tamper=='wheel-binding':
        with pytest.raises(release.ReleaseError,match='actual same-version wheel'):release.create_bundle_manifest(house,'macos_arm64',lock,uv,metadata,primary_notice_manifest=manifest)
        assert not metadata.exists();return
    result=release.create_bundle_manifest(house,'macos_arm64',lock,uv,metadata,primary_notice_manifest=manifest)
    assert len(result['primary_notices'])==4
    if tamper=='notice-bytes':(metadata/result['primary_notices'][0]['path']).write_bytes(b'wrong')
    if tamper=='metadata-provenance':
        doc=json.loads((metadata/'PACKAGE_MANIFEST.json').read_bytes());doc['primary_notices'][0]['provenance']['sdist_url']='https://wrong.invalid/'
        (metadata/'PACKAGE_MANIFEST.json').write_text(json.dumps(doc))
    build=tmp_path/'build';make_build_evidence(build,app,version='0.1.10');tool=tmp_path/'full_project_release.py';tool.write_text('# synthetic tool\n');ops=tmp_path/'RELEASE_OPERATIONS.md';ops.write_text('synthetic operations\n');archive=tmp_path/'bundle.zip'
    args=(house,lock,uv,metadata,ops,'macos_arm64',archive,tmp_path/'evidence',build,tool)
    if tamper:
        with pytest.raises(release.ReleaseError,match='primary notice'):release.package_wheelhouse_bundle(*args,primary_notice_manifest=manifest)
        assert not archive.exists();return
    release.package_wheelhouse_bundle(*args,primary_notice_manifest=manifest)
    with zipfile.ZipFile(archive) as z:
        for row in result['primary_notices']:assert hashlib.sha256(z.read(row['path'])).hexdigest()==row['sha256']
    licenses=json.loads((metadata/'LICENSES.json').read_bytes())
    assert sum(bool(row.get('primary_source_notices')) for row in licenses['components'])==4

def test_notice_output_foreign_symlink_directory_refused(notice_fixture,monkeypatch,tmp_path):
    from tools import full_project_release as release
    from tests.test_full_project_release import make_wheel
    manifest,_,value=notice_fixture;house=tmp_path/'house';pins=[]
    for row in value['components']:
        sha=make_wheel(house/(row['name'].replace('-','_')+'-12.2.2-py3-none-any.whl'),name=row['name'],version='12.2.2');row['wheel_sha256']=sha;pins.append(row['name']+'==12.2.2 --hash=sha256:'+sha)
    manifest.write_text(json.dumps(value));sha=hashlib.sha256(manifest.read_bytes()).hexdigest();monkeypatch.setattr(notices,'MANIFEST_SHA',sha);original=notices.notice_payloads;monkeypatch.setattr(notices,'notice_payloads',lambda path:original(path,expected_sha=sha))
    lock=tmp_path/'lock';lock.write_text('\n'.join(pins));metadata=tmp_path/'metadata';metadata.mkdir();outside=tmp_path/'outside';outside.mkdir();(metadata/'notices').symlink_to(outside,target_is_directory=True)
    with pytest.raises(release.ReleaseError,match='fresh no-clobber'):release.create_bundle_manifest(house,'macos_arm64',lock,None,metadata,primary_notice_manifest=manifest)
    assert list(outside.iterdir())==[] and not (metadata/'PACKAGE_MANIFEST.json').exists()
