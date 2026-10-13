"""Exact original archive -> fresh CP source; prior failed/contaminated trees retained."""
import hashlib,json,os,stat
from pathlib import Path
from acquire_exact_archives import Refusal,manifest_rows
from intel_deps_prepare import SOURCE_INVENTORY_SHA,copy_exact_tree
from intel_crypto_successor_r1 import PREFIX
import intel_deps_stages as deps
from prepare_offline_builder import extract

CP_RELATIVE='cpython-source-3.12.13/Python-3.12.13'
CP_ARCHIVE={'filename':'Python-3.12.13.tar.xz','identity':{'device':16777231,'inode':17304235},'bytes':20801708,'mode':0o600,'sha256':'c08bc65a81971c1dd5783182826503369466c7e67374d1646519adf05207b684'}
FAILED_TREE_PATH=PREFIX.parent/'comsol-tls-execution-r1-4-bduwyb45/FAILED_TLS_UNIT_TREE.json'
FAILED_TREE={'identity':{'device':16777231,'inode':18641569},'bytes':1845627,'sha256':'70f4bbb56479bde2477aec3774ae7ef8043527c1893be82fa477e1ac8882fb70'}
CACHE_FINDING_PATH=PREFIX.parent/'comsol-tls-execution-r1-4-bduwyb45/ORIGINAL_CPYTHON_SOURCE_MEMBERSHIP_FINDING.json'
CACHE_FINDING={'identity':{'device':16777231,'inode':18641560},'bytes':47599,'sha256':'7b1c34f39285fbd47187f8dabefa27f41989321ee727ef1965f111ecfefeaa75'}

def inventory(prefix):
    raw=(prefix/'OFFLINE_SOURCE_TREE_INVENTORY_R3.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=SOURCE_INVENTORY_SHA:raise Refusal('exact original unpack inventory changed')
    result=json.loads(raw)
    if result['source_root']!=str(prefix/'sources'):raise Refusal('original inventory root changed')
    return result['rows']

def verify_archive_fd(fd,expected):
    st=os.fstat(fd)
    if (not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or st.st_nlink!=1 or stat.S_IMODE(st.st_mode)!=expected['mode']
        or {'device':st.st_dev,'inode':st.st_ino}!=expected['identity'] or st.st_size!=expected['bytes']):raise Refusal('fixed CP archive owner/mode/dev/inode/nlink/size changed')
    os.lseek(fd,0,os.SEEK_SET);digest=hashlib.sha256();size=0
    while raw:=os.read(fd,1048576):
        size+=len(raw)
        if size>expected['bytes']:raise Refusal('fixed CP archive grew')
        digest.update(raw)
    final=os.fstat(fd)
    if (final.st_dev,final.st_ino,final.st_size,final.st_uid,final.st_nlink,final.st_mode)!=(st.st_dev,st.st_ino,st.st_size,st.st_uid,st.st_nlink,st.st_mode) or size!=expected['bytes'] or digest.hexdigest()!=expected['sha256']:raise Refusal('fixed CP archive same-FD SHA/identity changed')
    os.lseek(fd,0,os.SEEK_SET)
    return {'identity':expected['identity'],'bytes':size,'sha256':digest.hexdigest(),'uid':st.st_uid,'mode':expected['mode']}

def extract_pristine(fd,destination,content_rows,expected_members=None,expected_bytes=None):
    """Production extraction calls the unchanged reviewed FD API, then checks full content."""
    destination.mkdir(mode=0o700)
    rootfd=os.open(destination,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:unpack=extract(fd,rootfd)
    finally:os.close(rootfd)
    if expected_members is not None and unpack['members']!=expected_members:raise Refusal('exact official archive member count differs')
    if expected_bytes is not None and unpack['expanded_bytes']!=expected_bytes:raise Refusal('exact official archive expanded bytes differ')
    if {p.name for p in destination.iterdir()}!={'Python-3.12.13'}:raise Refusal('fresh CP archive top-level closure differs')
    root=destination/'Python-3.12.13';sourcefd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        st=os.fstat(sourcefd)
        if st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)!=0o700:raise Refusal('fresh CP source root owner/mode differs')
        actual=deps.tree_record(root)
        final=root.lstat()
        if (final.st_dev,final.st_ino,final.st_mode)!=(st.st_dev,st.st_ino,st.st_mode):raise Refusal('fresh CP source root swapped during content check')
    finally:os.close(sourcefd)
    expected=[]
    for row in content_rows:
        r={'relative_path':row['relative_path'],'kind':row['kind'],'mode':int(row['mode'],8)}
        if row['kind']=='regular':r.update(bytes=row['bytes'],sha256=row['sha256'])
        elif row['kind']!='directory':raise Refusal('official CP content inventory contains nonregular member')
        expected.append(r)
    normalized=[{k:v for k,v in row.items() if k!='identity'} for row in actual]
    if sorted(expected,key=lambda r:r['relative_path'])!=sorted(normalized,key=lambda r:r['relative_path']):raise Refusal('fresh CP full member/mode/byte/SHA closure differs')
    return {'unpack':unpack,'fresh_source_root':str(root),'fresh_source_identity':{'device':st.st_dev,'inode':st.st_ino,'uid':st.st_uid,'mode':stat.S_IMODE(st.st_mode)},'fresh_complete_content_verified':True,'fresh_tree':actual,'archive_programs_executed':0}

def prepare_sources_r2(x,prefix=PREFIX):
    if prefix!=PREFIX or x['root']!=prefix/'intel-tls-r2':raise Refusal('only fixed r2 source namespace permitted')
    rows=inventory(prefix);relative='openssl-source-4.0.2/openssl-4.0.2'
    roots=[r for r in rows if r['relative_path']==relative]
    if len(roots)!=1:raise Refusal('fixed original OpenSSL source root missing')
    selected=[dict(r,relative_path=r['relative_path'][len(relative)+1:]) for r in rows if r['relative_path'].startswith(relative+'/')]
    # The unchanged copier validates entire membership and every old inode/byte
    # before making its output. No added cache or other entry can be ignored.
    openssl=copy_exact_tree(prefix/'sources'/relative,selected,x['arm_source'],root_record=roots[0])
    manifests=[r for r in manifest_rows() if r['artifact_id']=='cpython-source-3.12.13']
    if len(manifests)!=1 or any(manifests[0][key]!=value for key,value in {'filename':CP_ARCHIVE['filename'],'expected_bytes':CP_ARCHIVE['bytes'],'expected_sha256':CP_ARCHIVE['sha256']}.items()):raise Refusal('fixed original43 CP archive manifest changed')
    archives=os.open(prefix/'archives',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    fd=None
    try:
        fd=os.open(CP_ARCHIVE['filename'],os.O_RDONLY|os.O_NOFOLLOW,dir_fd=archives)
        verified=verify_archive_fd(fd,CP_ARCHIVE)
        cp_rows=[dict(r,relative_path=r['relative_path'][len(CP_RELATIVE)+1:]) for r in rows if r['relative_path'].startswith(CP_RELATIVE+'/')]
        cp=extract_pristine(fd,x['root']/'cpython-input',cp_rows,4928,101560866)
        post=verify_archive_fd(fd,CP_ARCHIVE)
        if post!=verified:raise Refusal('original archive changed during extraction')
        if Path(cp['fresh_source_root'])!=x['cp_source']:raise Refusal('fresh CP source pointer differs from command plan')
        return {'openssl_exact_copy':openssl,'cpython_exact_archive':verified,'cpython_fresh_source':cp,'old_source_cache_ignored_or_deleted':False}
    finally:
        if fd is not None:os.close(fd)
        os.close(archives)

def verify_prior_tls_and_source():
    """Read-only conservation of failed r1 plus complete old CP source/cache bytes."""
    from security_snapshot._host_scoped_provider import verify_private_path_security
    old=PREFIX/'intel-tls-r1';verify_private_path_security(old,directory=True)
    st=old.lstat()
    if (st.st_dev,st.st_ino,st.st_uid,stat.S_IMODE(st.st_mode))!=(16777231,18634376,os.getuid(),0o700):raise Refusal('failed TLS r1 root identity/owner/mode changed')
    failed=deps.read_dep_stage_record(FAILED_TREE_PATH,FAILED_TREE)
    if deps.tree_record(old)!=failed:raise Refusal('failed TLS r1 complete partial/fences/raw tree changed')
    final=old.lstat()
    if (final.st_dev,final.st_ino,final.st_mode)!=(st.st_dev,st.st_ino,st.st_mode):raise Refusal('failed TLS r1 root swapped during read')
    finding=deps.read_dep_stage_record(CACHE_FINDING_PATH,CACHE_FINDING);rows=inventory(PREFIX);expected=[]
    for row in rows:
        if not row['relative_path'].startswith(CP_RELATIVE+'/'):continue
        r={'relative_path':row['relative_path'][len(CP_RELATIVE)+1:],'identity':{'device':row['device'],'inode':row['inode']},'kind':row['kind'],'mode':int(row['mode'],8)}
        if row['kind']=='regular':r.update(bytes=row['bytes'],sha256=row['sha256'])
        expected.append(r)
    expected.extend(finding['added_records']);expected.sort(key=lambda r:r['relative_path'])
    cp_root=PREFIX/'sources'/CP_RELATIVE
    root_rows=[r for r in rows if r['relative_path']==CP_RELATIVE]
    if len(root_rows)!=1:raise Refusal('old contaminated CP root missing from inventory')
    rootfd=os.open(cp_root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        actual=os.fstat(rootfd);row=root_rows[0]
        if (actual.st_dev,actual.st_ino,actual.st_uid,stat.S_IMODE(actual.st_mode))!=(row['device'],row['inode'],row['uid'],int(row['mode'],8)):raise Refusal('old CP source root identity/mode changed')
        if sorted(deps.tree_record(cp_root),key=lambda r:r['relative_path'])!=expected:raise Refusal('original contaminated CP expected+cache whole tree changed')
        current=cp_root.lstat()
        if (current.st_dev,current.st_ino,current.st_mode)!=(actual.st_dev,actual.st_ino,actual.st_mode):raise Refusal('old CP source root swapped during read')
    finally:os.close(rootfd)
    return {'failed_tls_r1_tree_records':len(failed),'failed_tls_r1_snapshot_sha256':FAILED_TREE['sha256'],'failed_tls_r1_unchanged':True,'old_CP_source_records':len(expected),'old_CP_source_including155cache_unchanged':True,'cache_finding_sha256':CACHE_FINDING['sha256'],'old_fences_retained':True}
