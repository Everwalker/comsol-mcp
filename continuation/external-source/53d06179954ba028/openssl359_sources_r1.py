"""Authenticated OpenSSL3.5.9 and original CPython fresh-source preparation."""
import hashlib,json,os,stat
from pathlib import Path
from acquire_exact_archives import Refusal
from intel_crypto_successor_r1 import PREFIX,checkpoint_bytes
from intel_deps_prepare import exclusive_json,copy_exact_tree
import intel_deps_stages as deps
import tls_sources_r2 as cp_sources
import tls_cpython_checkpoint_r4 as old
from prepare_offline_builder import extract

INPUT_ROOT=PREFIX.parent/'comsol-openssl359-input-r1-h1avrr0_'
SOURCE_PIN={'identity':{'device':16777231,'inode':22717259},'bytes':53279637,'mode':0o600,'sha256':'603f5602e2eef00d77fbd429d34dcd5822bb301757a1bc9cdb24c670f1eb859a'}
AUTH_PIN={'identity':{'device':16777231,'inode':22718075},'bytes':2164,'kind':'regular','mode':0o600,'sha256':'83e9d485501e4f04551c18a1685f7259e8cfff071a1457fdd47cda1846965a9e'}
CONTENT_PIN={'identity':{'device':16777231,'inode':22718278},'bytes':1383837,'kind':'regular','mode':0o644,'sha256':'d601984111be77c4c2af9e47cb17774e724918e2ba44968ada829245f974542f'}
R4=PREFIX/'intel-tls-cpython-r4'
R4_SNAPSHOT=PREFIX.parent/'comsol-tls-cpython-execution-r4-f74o2la9/FAILED_TLS_CP_R4_COMPLETE_TREE.json'
R4_PIN={'identity':{'device':16777231,'inode':22714476},'bytes':4357446,'kind':'regular','mode':0o600,'sha256':'408e4f0314aa15e1ef2e8ca7bb2cec78642dd8f4b7157509d78755ee684fd570'}
CP_SSL_SHA='19beed620c8b9eaec82e21235cf83f505e279ad8ede63652616d3fedea4a1710'

def verify_failed_r4():
    from security_snapshot._host_scoped_provider import verify_private_path_security
    verify_private_path_security(R4,directory=True);st=R4.lstat()
    if (st.st_dev,st.st_ino,st.st_uid,stat.S_IMODE(st.st_mode))!=(16777231,22674075,os.getuid(),0o700):raise Refusal('fixed failed R4 root changed')
    raw,_=checkpoint_bytes(R4_SNAPSHOT,R4_PIN);rows=json.loads(raw)
    if deps.tree_record(R4)!=rows:raise Refusal('complete old R4 failure/fence/source/install tree changed')
    final=R4.lstat()
    if (final.st_dev,final.st_ino,final.st_mode)!=(st.st_dev,st.st_ino,st.st_mode):raise Refusal('old R4 root swapped during check')
    return {'old_failed_r4_unchanged':True,'records':len(rows),'snapshot_sha256':R4_PIN['sha256']}

def preservation_verified_summary():
    return {'through_original_r3':old.preservation_verified_summary(),'r4':verify_failed_r4()}

def authenticated_content():
    from security_snapshot._host_scoped_provider import verify_private_path_security
    verify_private_path_security(INPUT_ROOT,directory=True,expected_identity={'device':16777231,'inode':22716034})
    raw,_=checkpoint_bytes(INPUT_ROOT/'AUTHENTICATED_SOURCE_RECEIPT.json',AUTH_PIN);auth=json.loads(raw)
    if (auth['status']!='PASS_ACTUAL_OFFICIAL_SHA256_AND_PUBLISHER_PGP' or auth['fixed_primary_fingerprint']!='B146647E45A7B33947AB226B2A2C87D161692D40'
        or auth['actual_signing_subkey_fingerprint']!='C46ED3F2CBEFDA1FDAADA44264ED7B1DCCE71CB2' or auth['actual_archive_unpack']!=0 or auth['actual_native_compile']!=0):raise Refusal('fixed original publisher authentication facts differ')
    for path,row in auth['inputs'].items():
        expected=dict(row,identity={'device':row['device'],'inode':row['inode']},kind='regular')
        checkpoint_bytes(Path(path),expected)
    raw,_=checkpoint_bytes(INPUT_ROOT/'OFFICIAL_ARCHIVE_CONTENT_INVENTORY_SOURCE_ONLY.json',CONTENT_PIN);content=json.loads(raw)
    if content['source_sha256']!=SOURCE_PIN['sha256'] or content['members']!=6078 or content['expanded_bytes']!=131230833 or content['links']!=0:raise Refusal('fixed official complete membership facts differ')
    return content

def extract_exact_source(fd,destination,content):
    destination.mkdir(mode=0o700);d=os.open(destination,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:result=extract(fd,d)
    finally:os.close(d)
    if result!={'members':content['members'],'expanded_bytes':content['expanded_bytes']}:raise Refusal('official source unpack totals differ')
    if {p.name for p in destination.iterdir()}!={'openssl-3.5.9'}:raise Refusal('official source top level differs')
    root=destination/'openssl-3.5.9';tree=deps.tree_record(root)
    expected=[dict(row,relative_path=row['relative_path'][len('openssl-3.5.9/'):]) for row in content['rows'] if row['relative_path'].startswith('openssl-3.5.9/')]
    normalized=[{k:v for k,v in row.items() if k!='identity'} for row in tree]
    if sorted(normalized,key=lambda r:r['relative_path'])!=sorted(expected,key=lambda r:r['relative_path']):raise Refusal('complete official source mode/member/byte/SHA closure differs')
    st=root.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)!=0o700:raise Refusal('fresh official source root security differs')
    return {'source_root':str(root),'root_identity':{'device':st.st_dev,'inode':st.st_ino,'uid':st.st_uid,'mode':0o700},'tree':tree,'unpack':result,'source_programs_executed':0}

def prepare_sources(x):
    if x['root']!=PREFIX/'intel-tls-openssl359-r1':raise Refusal('fixed fresh OpenSSL3.5.9 namespace required')
    content=authenticated_content()
    fd=os.open(INPUT_ROOT/'archives/openssl-3.5.9.tar.gz',os.O_RDONLY|os.O_NOFOLLOW)
    try:
        before=cp_sources.verify_archive_fd(fd,SOURCE_PIN)
        openssl=extract_exact_source(fd,x['root']/'openssl-input',content)
        if cp_sources.verify_archive_fd(fd,SOURCE_PIN)!=before:raise Refusal('authenticated archive changed during extraction')
    finally:os.close(fd)
    source=Path(openssl['source_root']);st=source.lstat();root_record={'uid':st.st_uid,'device':st.st_dev,'inode':st.st_ino,'mode':oct(stat.S_IMODE(st.st_mode)),'kind':'directory'}
    copy_rows=[]
    for row in openssl['tree']:
        converted=dict(row,uid=os.getuid(),device=row['identity']['device'],inode=row['identity']['inode'],mode=oct(row['mode']))
        if row['kind']=='regular':converted['nlink']=1
        copy_rows.append(converted)
    working=[]
    for arch in ('arm64','x86_64'):
        destination=x['arm_source'] if arch=='arm64' else x['x86_source']
        working.append({'arch':arch,'exact_copy':copy_exact_tree(source,copy_rows,destination,root_record=root_record)})
    inventory=cp_sources.inventory(PREFIX);cp_rows=[dict(row,relative_path=row['relative_path'][len(cp_sources.CP_RELATIVE)+1:]) for row in inventory if row['relative_path'].startswith(cp_sources.CP_RELATIVE+'/')]
    fd=os.open(PREFIX/'archives'/cp_sources.CP_ARCHIVE['filename'],os.O_RDONLY|os.O_NOFOLLOW)
    try:
        cp_before=cp_sources.verify_archive_fd(fd,cp_sources.CP_ARCHIVE)
        cp=cp_sources.extract_pristine(fd,x['root']/'cpython-input',cp_rows,4928,101560866)
        if cp_sources.verify_archive_fd(fd,cp_sources.CP_ARCHIVE)!=cp_before:raise Refusal('official CP archive changed during new extraction')
    finally:os.close(fd)
    if hashlib.sha256((x['cp_source']/'Modules/_ssl.c').read_bytes()).hexdigest()!=CP_SSL_SHA:raise Refusal('official CP SSL source differs; no backport permitted')
    from tls_cert_checks_r4 import FIXTURE_BYTES,checked_fixture
    from tls_cpython_checkpoint_r4 import copy_blob
    folder=x['root']/'runtime-certfixtures';folder.mkdir(mode=0o700)
    for name,expected in FIXTURE_BYTES.items():
        path=x['cp_source']/'Lib/test/certdata'/name;checked_fixture(path,expected);row=deps.file_record(path);row.update(kind='regular',mode=stat.S_IMODE(path.lstat().st_mode));raw,_=checkpoint_bytes(path,row);copy_blob(folder/name,raw)
    exclusive_json(x['root']/'CERT_FIXTURE_INPUTS.json',FIXTURE_BYTES)
    from openssl359_memorybio_r1 import CERT_PIN
    cert=x['cp_source']/'Lib/test/certdata/keycert.pem';checked_fixture(cert,CERT_PIN);copy_blob(folder/'keycert.pem',cert.read_bytes())
    result={'OpenSSL_official_source':openssl,'OpenSSL_arch_work_copies':working,'official_OpenSSL_archive':before,'official_CPython_source':cp,'official_CPython_archive':cp_before,'official_unmodified_CPython':True,'SSL_source_sha256':CP_SSL_SHA,'fixed_public_MemoryBIO_certificate':CERT_PIN}
    exclusive_json(x['root']/'OFFICIAL_INPUTS_PREPARED.json',result)
    row=deps.file_record(x['root']/'OFFICIAL_INPUTS_PREPARED.json');row.update(kind='regular',mode=0o600);result['accepted_checkpoint_record']=row
    return result

def verify_unchanged_source_inputs(x,accepted):
    path=x['root']/'OFFICIAL_INPUTS_PREPARED.json'
    if accepted['accepted_checkpoint_record']['path']!=str(path):raise Refusal('accepted prepared checkpoint path differs')
    raw,_=checkpoint_bytes(path,accepted['accepted_checkpoint_record']);record=json.loads(raw)
    source=record['OpenSSL_official_source']
    if source['source_root']!=str(x['root']/'openssl-input/openssl-3.5.9'):raise Refusal('official source pointer differs')
    current=Path(source['source_root']);st=current.lstat()
    if {'device':st.st_dev,'inode':st.st_ino,'uid':st.st_uid,'mode':stat.S_IMODE(st.st_mode)}!=source['root_identity'] or deps.tree_record(current)!=source['tree']:raise Refusal('authoritative official source tree changed')
    if hashlib.sha256((x['cp_source']/'Modules/_ssl.c').read_bytes()).hexdigest()!=CP_SSL_SHA or not record['official_unmodified_CPython']:raise Refusal('original CP SSL source changed')
    return {'official_source_input_unchanged':True,'original_CPython_SSL_source_unchanged':True}
