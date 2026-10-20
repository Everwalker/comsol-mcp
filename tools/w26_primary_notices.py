"""Exact same-version public notice inputs; no wheel/source execution or acquisition."""
import hashlib,io,json,tarfile
from pathlib import Path,PurePosixPath
from tools.w26_prepare_current_inputs import _helpers

MANIFEST_SHA='3f2904d8c4c698d841adf6be72cd37f4143f018a85977602679d89ba8b5050b0'
LICENSE_SHA='0ca04b07928d4872b9d9bb22187ca0426dd8bfab08f26eada0999a71dc81aaff'
NAMES={'pyobjc-core','pyobjc-framework-applicationservices','pyobjc-framework-coremedia','pyobjc-framework-screencapturekit'}

def read_record(record,maximum):
    raw,observed=_helpers().raw_file(Path(record['path']),maximum=maximum)
    if len(raw)!=record['bytes'] or observed['sha256']!=record['sha256']:
        raise RuntimeError('primary notice exact input bytes/hash differs')
    return raw

def notice_payloads(manifest_path,*,expected_sha=None):
    """Return public text and sanitized provenance; caller must bind each wheel SHA."""
    raw,record=_helpers().raw_file(Path(manifest_path),maximum=262144)
    allowed_manifest_hashes={
        'historical-r22-exact':MANIFEST_SHA,
        'official-pypi-12.2.2-r23':'1608d9b92736d35473f832fef642fcca078c1fc537f4aa511d1fce7624a79dbc',
    }
    if expected_sha is None:
        expected_sha=record['sha256'] if record['sha256'] in allowed_manifest_hashes.values() else MANIFEST_SHA
    if expected_sha not in allowed_manifest_hashes.values() or record['sha256']!=expected_sha:
        raise RuntimeError('unknown primary notice manifest hash')
    value=json.loads(raw)
    if value.get('schema')!='CVW26_EXACT_PYOBJC_PRIMARY_NOTICE_INPUTS_V1' or {row['name'].lower() for row in value['components']}!=NAMES or len(value['components'])!=4:
        raise RuntimeError('closed primary notice component membership differs')
    result={}
    for row in value['components']:
        name=row['name'].lower()
        if row['version']!='12.2.2' or row['exact_metadata_url']!='https://pypi.org/pypi/'+row['name']+'/12.2.2/json' or not row['sdist_url'].startswith('https://files.pythonhosted.org/'):
            raise RuntimeError('same-version primary notice source differs')
        metadata=json.loads(read_record(row['metadata'],262144));sdist=read_record(row['verified_sdist'],4194304)
        selected=[item for item in metadata['urls'] if item['url']==row['sdist_url']]
        if len(selected)!=1 or selected[0]['size']!=len(sdist) or selected[0]['digests']['sha256']!=row['verified_sdist']['sha256'] or metadata['info']['version']!='12.2.2' or metadata['info']['name'].lower()!=name:
            raise RuntimeError('official notice metadata/sdist identity differs')
        member=row['license_file']['archive_member'];expected=name.replace('-','_')+'-12.2.2/License.txt'
        if member!=expected:raise RuntimeError('primary notice archive member differs')
        with tarfile.open(fileobj=io.BytesIO(sdist),mode='r:gz') as archive:
            names=set();license_bytes=None
            for item in archive:
                path=PurePosixPath(item.name)
                if path.is_absolute() or '..' in path.parts or '\\' in item.name or item.name in names or not (item.isfile() or item.isdir()):
                    raise RuntimeError('primary source tar traversal/link/membership refused')
                names.add(item.name)
                if item.name==member:
                    if not item.isfile() or item.size!=1249:raise RuntimeError('primary notice is not exact regular member')
                    license_bytes=archive.extractfile(item).read()
        copied=read_record(row['license_file'],4096)
        if license_bytes!=copied or hashlib.sha256(copied).hexdigest()!=LICENSE_SHA or row['license_file']['sha256']!=LICENSE_SHA:
            raise RuntimeError('primary extracted license bytes differ')
        result[name]={'wheel_sha256':row['wheel_sha256'],'license_bytes':copied,'relative_path':'notices/'+name+'/License.txt',
                      'provenance':{'name':row['name'],'version':'12.2.2','wheel_sha256':row['wheel_sha256'],'primary_metadata_url':row['exact_metadata_url'],
                                    'metadata_sha256':row['metadata']['sha256'],'sdist_url':row['sdist_url'],'sdist_sha256':row['verified_sdist']['sha256'],
                                    'sdist_bytes':len(sdist),'archive_member':member,'license_sha256':LICENSE_SHA,'license_bytes':len(copied),
                                    'separate_product_notice_retained':True,'acceptance':'SOURCE_NOTICE_ASSOCIATION_NOT_LEGAL_OR_RUNTIME_CERTIFICATION'}}
    return result
