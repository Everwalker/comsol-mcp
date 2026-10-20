"""Sealed actual R6 data attachment for the existing disposable Lifecycle.

No synthetic jobs, grants, reconciliation, native engine or backup format changes.
"""
import argparse,hashlib,importlib,importlib.machinery,importlib.util,json,os,re,sqlite3,stat,sys,tarfile,io
from pathlib import Path

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def signature(s):return {'dev':s.st_dev,'inode':s.st_ino,'uid':s.st_uid,'mode':s.st_mode,'nlink':s.st_nlink,'bytes':s.st_size,'mtime_ns':s.st_mtime_ns,'ctime_ns':s.st_ctime_ns}
def _raw_regular(path,expected=None,maximum=268435456,*,link_count):
    path=Path(path)
    if not path.is_absolute() or path!=path.resolve():raise RuntimeError('sealed path must be canonical no-link')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        first=os.fstat(fd)
        if not stat.S_ISREG(first.st_mode) or first.st_uid!=os.getuid() or first.st_nlink!=link_count or first.st_mode&0o022 or first.st_size>maximum:raise RuntimeError('sealed input owner/mode/link/size refused')
        pieces=[]
        while part:=os.read(fd,1048576):pieces.append(part)
        raw=b''.join(pieces);record={'path':str(path),**signature(first),'sha256':hashlib.sha256(raw).hexdigest()}
        if signature(first)!=signature(os.fstat(fd)) or signature(first)!=signature(path.lstat()) or len(raw)!=first.st_size:raise RuntimeError('sealed sameFD source changed')
        if expected is not None and any(record.get(k)!=v for k,v in expected.items() if k in record):raise RuntimeError('sealed exact first record differs')
        return raw,record
    finally:os.close(fd)
def raw_file(path,expected=None,maximum=268435456):
    return _raw_regular(path,expected,maximum,link_count=1)

def restored_database_binding(receipt,home):
    home=Path(home);live=home/'operations.sqlite3';recovery=Path(receipt['recovery_directory'])
    if (receipt.get('success') is not True or receipt.get('operation')!='full_restore'
            or receipt.get('worker_or_model_bindings_revived') is not False or receipt.get('operation_replay_performed') is not False
            or recovery.parent!=home or not re.fullmatch('state-full-recovery-[0-9TZ]+-[0-9a-f]{10}',recovery.name)
            or home!=home.resolve() or recovery!=recovery.resolve()):raise RuntimeError('actual full restore producer binding refused')
    alias=recovery/'operations.sqlite3';one=_raw_regular(live,link_count=2)[1];two=_raw_regular(alias,link_count=2)[1]
    if any(one[k]!=two[k] for k in one if k!='path') or one['sha256']!=receipt['database_sha256']:raise RuntimeError('actual full restore two-name binding differs')
    return {'schema':'w26-actual-fullrestore-two-name-db/1','producer_receipt':receipt,'live':one,'recovery_alias':two}

def _restored_record(path,binding):
    if binding.get('schema')!='w26-actual-fullrestore-two-name-db/1' or str(Path(path))!=binding['live']['path']:raise RuntimeError('actual restored DB proof path differs')
    receipt=binding['producer_receipt'];live=Path(binding['live']['path']);alias=Path(binding['recovery_alias']['path'])
    if alias!=Path(receipt['recovery_directory'])/'operations.sqlite3' or alias.parent.parent!=live.parent:raise RuntimeError('actual restored DB recovery alias leaves producer home')
    one=_raw_regular(live,binding['live'],link_count=2)[1];two=_raw_regular(alias,binding['recovery_alias'],link_count=2)[1]
    if any(one[k]!=two[k] for k in one if k!='path'):raise RuntimeError('actual restored DB names are not same stable file')
    return one

def _after_typed_readback_binding(binding):
    # Called only after this process's actual OperationStore/ArtifactStore read.
    # Their SQLite journal normalization can change physical bytes, not rows.
    live=Path(binding['live']['path']);alias=Path(binding['recovery_alias']['path']);one=_raw_regular(live,link_count=2)[1];two=_raw_regular(alias,link_count=2)[1]
    if any(one[k]!=two[k] for k in one if k!='path') or any(one[k]!=binding['live'][k] for k in ['dev','inode','uid','mode','nlink']):raise RuntimeError('typed readback changed restored DB binding')
    result={**binding,'live':one,'recovery_alias':two,'normalization':'ACTUAL_TYPED_READBACK_SQLITE_JOURNAL_ONLY_ROWS_VERIFIED_SEPARATELY','prior_record':binding['live']}
    return result

def exclusive(path,raw):
    path=Path(path);fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        view=memoryview(raw)
        while view:view=view[os.write(fd,view):]
        os.fsync(fd)
    finally:os.close(fd)
    return raw_file(path)[1]

def verify_seal(path,sha):
    raw,record=raw_file(path)
    if record['sha256']!=sha:raise RuntimeError('exact external seal SHA differs')
    seal=json.loads(raw);root=Path(seal['root']);sig=signature(root.lstat())
    if root!=Path(path).parent or sig!=seal['root_identity'] or root.is_symlink() or sig['uid']!=os.getuid() or sig['mode']&0o777!=0o700:raise RuntimeError('sealed root identity differs')
    if sorted(p.name for p in root.iterdir())!=seal['root_exact_entry_names']:raise RuntimeError('sealed root membership differs')
    for row in seal['directories']:
        p=Path(row['path'])
        if not p.is_relative_to(root) or p.is_symlink() or signature(p.lstat())!=row['identity'] or not stat.S_ISDIR(p.lstat().st_mode):raise RuntimeError('sealed directory member differs')
    expected=set()
    for row in seal['input_records']:
        p=Path(row['path'])
        if not p.is_relative_to(root):raise RuntimeError('sealed file leaves root')
        raw_file(p,row);expected.add(str(p))
    raw_file(Path(seal['closure']['path']),seal['closure']);expected.add(seal['closure']['path']);expected.add(str(Path(path)))
    expected.add(str(root/'SEAL.json'))
    if {str(p) for p in root.rglob('*') if p.is_file()}!=expected:raise RuntimeError('sealed whole file membership differs')
    closure=json.loads(raw_file(Path(seal['closure']['path']),seal['closure'])[0])
    if closure['namespace']!=str(root) or closure['uid']!=os.getuid() or closure['registered_blob_count']!=2 or closure['all_project_file_count']!=4:raise RuntimeError('sealed actual closure counts differ')
    for value in [closure['genuine_prior_wheel'],closure['whole_database'],*closure['project_files']]:
        raw_file(Path(value['copy']['path']),value['copy']);raw_file(Path(value['original']['path']),value['original'])
    if closure['genuine_prior_wheel']['copy']['sha256']!='c1d82034e19e1f0fd897f3ec76911a3ccad229ed35f613148633bfb56f923ae6':raise RuntimeError('genuine W22 prior identity differs')
    verify_database(Path(closure['whole_database']['copy']['path']),closure,restored=False)
    return closure

def database_observation(path,*,restore_binding=None):
    path=Path(path)
    before=raw_file(path)[1] if restore_binding is None else _restored_record(path,restore_binding)
    wal=Path(str(path)+'-wal');wal_record=None
    if wal.exists() or wal.is_symlink():
        wal_record=raw_file(wal)[1]
        if wal_record['bytes']!=0:raise RuntimeError('quiescent row observation refuses uncheckpointed WAL')
    db=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True);db.row_factory=sqlite3.Row
    try:
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('actual ledger integrity failed')
        tables=sorted(r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'"));columns={};counts={};digests={};history=hashlib.sha256();rows_by_table={}
        for table in tables:
            if not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*',table):raise RuntimeError('unsafe ledger table name')
            cols=[dict(r) for r in db.execute('PRAGMA table_info("'+table+'")')];rows=db.execute('SELECT * FROM "'+table+'"').fetchall();columns[table]=cols;counts[table]=len(rows);rows_by_table[table]=[dict(r) for r in rows]
            encoded=[json.dumps([{'bytes_hex':x.hex()} if isinstance(x,bytes) else x for x in tuple(r)],sort_keys=True,separators=(',',':'),allow_nan=False) for r in rows];digests[table]=hashlib.sha256(json.dumps(sorted(encoded),separators=(',',':')).encode()).hexdigest()
            if not table.startswith('sqlite_') and table not in {'projects','artifacts'} and encoded:
                history.update(table.encode()+b'\0');history.update(json.dumps([c['name'] for c in cols],separators=(',',':')).encode()+b'\0')
                for text in sorted(encoded):history.update(text.encode()+b'\n')
        return {'schema_columns':columns,'table_counts':counts,'table_row_digests':digests,'durable_history_sha256':history.hexdigest(),'rows':rows_by_table,'first_database_record':before}
    finally:
        db.close()
        if wal_record is not None:raw_file(wal,wal_record)
        elif wal.exists() or wal.is_symlink():raise RuntimeError('WAL appeared during quiescent row observation')
        if restore_binding is None:raw_file(path,before)
        elif _restored_record(path,restore_binding)!=before:raise RuntimeError('actual restored database changed during observation')

def verify_database(path,closure,*,restored,restore_binding=None):
    observed=database_observation(path,restore_binding=restore_binding)
    if observed['schema_columns']!=closure['schema_columns'] or observed['table_counts']!=closure['table_counts'] or observed['durable_history_sha256']!=closure['durable_history_sha256_same_algorithm_as_production']:raise RuntimeError('actual complete schema/count/durable row invariant differs')
    for table,value in observed['table_row_digests'].items():
        if not restored or table not in {'projects','artifacts'}:
            if value!=closure['table_row_digests'][table]:raise RuntimeError('actual complete table rows differ: '+table)
    if restored:
        projects={r['project_id']:r for r in observed['rows']['projects']};artifacts={r['artifact_id']:r for r in observed['rows']['artifacts']}
        for expected in closure['projects']:
            row=projects[expected['project_id']];metadata=json.loads(row['record_json']);invariant={k:v for k,v in metadata.items() if k not in ('workspace','revision','updated_at')}
            if digest(invariant)!=expected['invariant_metadata_sha256'] or row['revision']!=expected['required_restore_revision'] or metadata['revision']!=row['revision'] or metadata['workspace']!=row['workspace'] or row['schema_version']!=expected['schema_version']:raise RuntimeError('restored project invariant/revision differs')
        for expected in closure['artifacts']:
            metadata=json.loads(artifacts[expected['artifact_id']]['metadata']);invariant={k:v for k,v in metadata.items() if k not in expected['metadata_rebind_allowed_keys']}
            if digest(invariant)!=expected['invariant_metadata_sha256']:raise RuntimeError('restored artifact invariant differs')
    return observed

def supplemental_copy(closure,project_map,workspace):
    workspace=Path(workspace);copied=[]
    for row in closure['project_files']:
        if row['registered_blob']:continue
        relative=Path(row['relative_path'])
        if relative.is_absolute() or '..' in relative.parts or str(relative) not in {'parameters.json','v3-native-smoke.mph'}:raise RuntimeError('sealed supplemental relative path refused')
        project=Path(project_map[row['project_id']]);st=project.lstat()
        if project.is_symlink() or project!=project.resolve() or not project.is_relative_to(workspace) or st.st_uid!=os.getuid() or st.st_mode&0o777!=0o700:raise RuntimeError('actual restored project mapping ownership differs')
        raw,_=raw_file(Path(row['copy']['path']),row['copy'])
        parent=os.open(project,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            first=os.fstat(parent)
            if signature(first)!=signature(st):raise RuntimeError('restored project changed before no-clobber copy')
            fd=os.open(str(relative),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
            try:
                view=memoryview(raw)
                while view:view=view[os.write(fd,view):]
                os.fsync(fd)
            finally:os.close(fd)
            # APFS also changes directory nlink when adding an ordinary file.
            # Pin the directory object; final whole membership verifies the two additions.
            key=lambda s:(s.st_dev,s.st_ino,s.st_uid,s.st_mode)
            if key(first)!=key(os.fstat(parent)) or key(first)!=key(project.lstat()):raise RuntimeError('restored project identity changed during copy')
            copied.append(raw_file(project/relative)[1])
        finally:os.close(parent)
    if len(copied)!=2:raise RuntimeError('exact two supplemental members required')
    return copied

def verify_restored(home,workspace,closure,*,restore_binding=None):
    from comsol_mcp._operation_store import OperationStore
    from comsol_mcp._project_authority import ProjectAuthority
    from comsol_mcp._artifact_store import local_artifact_host_identity,resolve_registered_artifact
    observed=verify_database(Path(home)/'operations.sqlite3',closure,restored=True,restore_binding=restore_binding);store=OperationStore(Path(home)/'operations.sqlite3');workspace=Path(workspace);host=local_artifact_host_identity();resolved=[];project_map={}
    try:
        authority=ProjectAuthority(store,workspace_root=workspace,permission_provider=lambda:set(),grant_ceiling=set())
        for project in closure['projects']:project_map[project['project_id']]=authority.get_project(project['project_id'])['workspace']
        for row in closure['artifacts']:
            value=resolve_registered_artifact(Path(project_map[row['project_id']]),store,row['artifact_id'],project_id=row['project_id'],current_host_identity=host,current_engine_host_identity=host,current_server_instance_id='')
            if value['sha256']!=row['sha256'] or value['size']!=row['size_bytes'] or value['role']!=row['role'] or value['relative_path']!=row['relative_path']:raise RuntimeError('actual registered artifact readback differs')
            resolved.append({**value,'path':str(value['path'])})
    finally:store.close()
    for project in closure['projects']:
        p=Path(project_map[project['project_id']]);files={str(x.relative_to(p)) for x in p.rglob('*') if x.is_file()};expected={r['relative_path'] for r in closure['project_files'] if r['project_id']==project['project_id']}
        if files!=expected:raise RuntimeError('whole restored four-file project closure differs')
        for row in closure['project_files']:
            raw,_=raw_file(p/row['relative_path'])
            if len(raw)!=row['copy']['bytes'] or hashlib.sha256(raw).hexdigest()!=row['copy']['sha256']:raise RuntimeError('restored actual full project bytes differ')
    if restore_binding is not None:restore_binding=_after_typed_readback_binding(restore_binding)
    verify_database(Path(home)/'operations.sqlite3',closure,restored=True,restore_binding=restore_binding)
    return {'success':True,'restored_database_binding':restore_binding,'project_map':project_map,'registered_readbacks':resolved,'schema_table_count':len(observed['table_counts']),'table_counts':observed['table_counts'],'durable_history_sha256':observed['durable_history_sha256'],'registered_files':2,'supplemental_files':2,'total_files':4,'native':'NOT_RUN','solve':'NOT_RUN'}

def descriptor(path,sha):
    raw,record=raw_file(path)
    if record['sha256']!=sha:raise RuntimeError('exact sealed descriptor changed')
    value=json.loads(raw);root=Path(value['unit_root'])
    if value.get('schema')!='w26-sealed-r6-lifecycle-descriptor/1' or value.get('synthetic_data') is not False or value.get('old_historical_dependencies')!='UNVERIFIED_CURRENT43_NEW_TEST_BASELINE':raise RuntimeError('closed external data descriptor mode differs')
    if root.parent!=Path('/private/tmp') or not root.name.startswith('cvw26-') or root!=root.resolve() or not Path(path).is_relative_to(root) or root.lstat().st_uid!=os.getuid() or root.lstat().st_mode&0o777!=0o700:raise RuntimeError('data descriptor task root refused')
    if value['seal_sha256']!='1b06bc4b8f681d61139a41307254b8e5b13759eaad2344922d06311b51ce7d76' or value['seal']!='/private/tmp/cvw26-lifecycle-inputs-c4563dee9656/SEAL_FINAL.json':raise RuntimeError('closed genuine R6 seal selection differs')
    if value.get('current_application_profile') is not None:current_application_profile(value['current_application_profile'])
    closure=verify_seal(value['seal'],value['seal_sha256']);return value,closure

def data_paths(value):
    work=Path(value['unit_root'])/'transition-work'
    return {'home':work/'sealed-control-home','evidence':work/'transition-evidence','restored_home':work/'full-restored-control-home','workspace':work/'full-restored-workspaces'}

BACKUP_PRODUCER_SHA='66a155dda59e5a4552165344515609ae42dc21f19e08fc303e29bef6c83a320b'
CURRENT_APPLICATION_LOCK=Path(__file__).with_name('w26_application_sources_175_r4.lock.json')
CURRENT_APPLICATION_R5_LOCK=Path(__file__).with_name('w26_application_sources_175_r5.lock.json')
CURRENT_APPLICATION_R6_LOCK=Path(__file__).with_name('w26_application_sources_175_r6.lock.json')
CURRENT_APPLICATION_TOOLS_ROOT=Path(__file__).resolve().parent
CURRENT_APPLICATION_LOADER_SCHEMA='w26-current-application-loader-source-closure/1'
CURRENT_APPLICATION_LOADER_NAMES=frozenset({
    '_w26_original_binary.py','_w26_original_process.py','_w26_original_tls.py',
    'full_project_release.py','release_transition_lifecycle.py',
    'w26_application_bundle.py','w26_application_lifecycle.py','w26_application_sources.lock.json',
    'w26_application_stage.py','w26_current_runtime_profile.py','w26_dependency_profile.py',
    'w26_dependency_stage.py','w26_dependency_unit.py','w26_hold_exec.py',
    'w26_lifecycle_stage.py','w26_prepare_current_inputs.py','w26_rebuild_current_runtime.py',
    'w26_runtime_actor.py','w26_runtime_sources.lock.json','w26_sealed_data.py',
    'w26_target_inputs.py','w26_application_sources_175_r4.lock.json','w26_application_sources_175_r5.lock.json','w26_application_sources_175_r6.lock.json',
})
CURRENT_APPLICATION_LOADER_MODULE_PATHS={
    'tools.'+Path(name).stem:str(CURRENT_APPLICATION_TOOLS_ROOT/name)
    for name in CURRENT_APPLICATION_LOADER_NAMES if name.endswith('.py')
}

def current_application_loader_closure(source_records):
    """Package the exact current26 project sources needed by the isolated reader."""
    root=CURRENT_APPLICATION_TOOLS_ROOT
    if root!=root.resolve(strict=True):raise RuntimeError('current application tools root is not canonical')
    members={Path(path).name:record for path,record in source_records.items() if Path(path).parent==root}
    if set(members)!=CURRENT_APPLICATION_LOADER_NAMES:raise RuntimeError('current application loader source closure membership differs')
    normalized={}
    for name,record in members.items():
        path=root/name
        if record.get('path')!=str(path):raise RuntimeError('current application loader source path differs')
        observed=raw_file(path)[1]
        if (record.get('bytes')!=observed['bytes'] or record.get('sha256')!=observed['sha256']
                or record.get('dev')!=observed['dev'] or record.get('inode')!=observed['inode']
                or record.get('uid')!=observed['uid'] or record.get('nlink')!=observed['nlink']
                or record.get('mode')!=(observed['mode']&0o777)):
            raise RuntimeError('current application loader source fingerprint differs')
        normalized[name]=observed
    return {'schema':CURRENT_APPLICATION_LOADER_SCHEMA,'root':str(root),'members':normalized}

def verify_current_application_loader_closure(closure):
    """Verify pinned sources before allowing an isolated namespace-package import."""
    root=CURRENT_APPLICATION_TOOLS_ROOT
    if (set(closure)!={'schema','root','members'} or closure['schema']!=CURRENT_APPLICATION_LOADER_SCHEMA
            or closure['root']!=str(root) or root!=root.resolve(strict=True)
            or set(closure['members'])!=CURRENT_APPLICATION_LOADER_NAMES):
        raise RuntimeError('current application loader source closure differs')
    for name in sorted(CURRENT_APPLICATION_LOADER_NAMES):
        path=root/name;record=closure['members'][name]
        if record.get('path')!=str(path):raise RuntimeError('current application loader member path differs')
        raw_file(path,record)
    return root

def _verify_current_application_cached_modules(root,closure):
    expected=CURRENT_APPLICATION_LOADER_MODULE_PATHS
    for name,module in list(sys.modules.items()):
        if name not in expected:continue
        path=expected[name]
        try:
            module_path=getattr(module,'__file__',None)
            spec=getattr(module,'__spec__',None);origin=getattr(spec,'origin',None);loader=getattr(spec,'loader',None)
            observed=str(Path(module_path).resolve(strict=True)) if isinstance(module_path,str) else None
            observed_origin=str(Path(origin).resolve(strict=True)) if isinstance(origin,str) else None
            loader_path=getattr(loader,'path',None)
            observed_loader=str(Path(loader_path).resolve(strict=True)) if isinstance(loader_path,str) else None
        except (OSError,RuntimeError,TypeError,ValueError):
            raise RuntimeError('cached current application tools module provenance is unreadable')
        if observed!=path or observed_origin!=path or observed_loader!=path:raise RuntimeError('cached current application tools module provenance differs: '+name)
        member=Path(path).name
        raw_file(Path(path),closure['members'][member])

def _install_current_application_tools_namespace(root,closure):
    """Resolve only the checked tools directory without adding cwd or sys.path."""
    root=Path(root)
    expected=str(root)
    existing=sys.modules.get('tools')
    if existing is not None:
        paths={str(Path(value).resolve(strict=True)) for value in getattr(existing,'__path__',())}
        if paths!={expected}:raise RuntimeError('preloaded tools package path differs from current26 source closure')
    else:
        spec=importlib.machinery.ModuleSpec('tools',loader=None,is_package=True)
        spec.submodule_search_locations=[expected]
        sys.modules['tools']=importlib.util.module_from_spec(spec)
    _verify_current_application_cached_modules(root,closure)
    before={name for name in sys.modules if name.startswith('tools.')}
    app=importlib.import_module('tools.w26_application_bundle');actor=importlib.import_module('tools.w26_runtime_actor')
    after={name for name in sys.modules if name.startswith('tools.')}
    if after-before-set(CURRENT_APPLICATION_LOADER_MODULE_PATHS):raise RuntimeError('current application loader imported an unknown tools module')
    _verify_current_application_cached_modules(root,closure)
    return app,actor

def current_application_profile(profile):
    """Read the exact completed current producer; grants no process or deletion rights."""
    import zipfile
    if set(profile)!={'schema','source_profile','root','result','candidate','once','loader_source_closure'}:raise RuntimeError('closed current installed application profile fields differ')
    tools_root=verify_current_application_loader_closure(profile['loader_source_closure'])
    app,actor=_install_current_application_tools_namespace(tools_root,profile['loader_source_closure'])
    from tools.w26_prepare_current_inputs import _helpers
    h=_helpers()
    source_profile=profile['source_profile']
    if profile['schema']!='w26-current-installed-application/1' or source_profile not in {'current175-r4','current175-r5','current175-r6'}:raise RuntimeError('closed current installed application profile differs')
    root=Path(profile['root'])
    if root.parent!=Path('/private/tmp') or root!=root.resolve() or not re.fullmatch('cvw26-application-[0-9a-f]{8}',root.name):raise RuntimeError('current application producer root refused')
    first=json.loads(h.raw_file(root/'FIRST_RECORD.json')[0]);h.secure_dir(root,first['root_identity'])
    expected={'result':'APPLICATION_BUNDLE_RESULT.json','candidate':'ACTOR_CANDIDATE_app6c.json','once':'UNIT_ONCE_GUARD.json'};values={}
    for key,name in expected.items():
        if profile[key]['path']!=str(root/name):raise RuntimeError('current application producer record leaves exact root')
        values[key]=json.loads(h.raw_file(root/name,expected=profile[key])[0])
    result,candidate,once=values['result'],values['candidate'],values['once']
    if result['status']!='PASS_CURRENT_APPLICATION_BUNDLE_SCOPED_NOT_RELEASE' or result['version']!='0.1.10' or result['target']!='macos_arm64' or result['build']['source_file_count']!=175:raise RuntimeError('current application producer not exact completed175')
    binding=json.loads(h.raw_file(Path(once['binding_first_record']['path']),expected=once['binding_first_record'])[0])
    if (once['activity_kind']!=actor.APP_BUNDLE or binding['status']!='MAIN_APPROVED_ONCE_W26_APPLICATION_BUNDLE' or binding['root']!=str(root) or binding['candidate_filename']!=expected['candidate'] or binding['candidate_sha256']!=profile['candidate']['sha256'] or binding['nonce']!=once['nonce']):raise RuntimeError('current application producer once/source binding differs')
    if once['binding_first_record']['path']!=str(root/'MAIN_ONCE_BINDING.json') or h.raw_file(root/'MAIN_SOURCE_REVIEW.json')[1]['sha256']!=binding['review_sha256']:raise RuntimeError('current application original source review differs')
    prepared_path=Path(candidate['prepared']['path'])
    if prepared_path.parent!=root:raise RuntimeError('current application producer prepared record leaves root')
    prepared=json.loads(h.raw_file(prepared_path,expected=candidate['prepared'])[0]);source=prepared['source'];snapshot=Path(source['snapshot_source'])
    if source['source_profile']!=source_profile or snapshot!=root/'source-input':raise RuntimeError('current installed profile source capture differs')
    h.secure_dir(snapshot);app.verify_version(snapshot)
    if source_profile=='current175-r4':lock_path=CURRENT_APPLICATION_LOCK
    elif source_profile=='current175-r5':lock_path=CURRENT_APPLICATION_R5_LOCK
    else:lock_path=CURRENT_APPLICATION_R6_LOCK
    if source['source_lock']['path']!=str(lock_path):raise RuntimeError('current application exact source lock path differs')
    lock=json.loads(h.raw_file(lock_path,expected=source['source_lock'])[0]);members=app.source_members(snapshot)
    if (lock.get('schema')!='w26-application-source-closure/1' or lock.get('profile')!=source_profile or lock.get('member_count')!=175 or lock.get('package_count')!=172 or lock.get('root_count')!=3 or len(members)!=175 or set(members)!=set(lock['members']) or set(members)!=set(source['snapshot_members'])):raise RuntimeError('current installed complete175 membership differs')
    for rel,path in members.items():
        record=source['snapshot_members'][rel]
        if record['path']!=str(path):raise RuntimeError('current installed snapshot member path differs')
        observed=h.raw_file(path,expected=record)[1]
        if {'bytes':observed['bytes'],'sha256':observed['sha256']}!=lock['members'][rel]:raise RuntimeError('current installed snapshot locked bytes differ')
    commands=result['commands']
    if len(commands)!=2:raise RuntimeError('current application exact two captures differ')
    for index,(row,label) in enumerate(zip(commands,['builder-check','application-build'])):
        if row['status']!='PASS' or row['rc']!=0 or row['owned_group_absent'] is not True or row['owned_process_terminal'] is not True or row['birth']['status']!='OBSERVED' or row['birth']['pid']!=row['pid'] or not row['result_name'].startswith(label+'-') or Path(row['result_name']).name!=row['result_name']:raise RuntimeError('current application original capture not terminal')
        if json.loads(h.raw_file(root/'logs'/row['result_name'])[0])!={k:v for k,v in row.items() if k!='result_name'}:raise RuntimeError('current application original capture record differs')
        execution=json.loads(h.raw_file(root/'evidence'/('EXEC_'+label+'.json'))[0]);probe_record=execution['network_probe_first_record']
        if execution['same_original_pid']!=row['pid'] or execution['inherited_group']!=row['pid'] or execution['extra_processes']!=0 or probe_record['path']!=str(root/'evidence'/('PROBE_'+label+'.json')):raise RuntimeError('current application same original PID proof differs')
        probe=json.loads(h.raw_file(Path(probe_record['path']),expected=probe_record)[0])
        if probe['status']!='PASS_ACTUAL_EPERM' or probe['errno'] not in [1,13]:raise RuntimeError('current application original OS network denial differs')
    wheel=root/'appwheel/comsol_mcp-0.1.10-py3-none-any.whl';wheel_record=h.raw_file(wheel)[1]
    if wheel_record['sha256']!=result['build']['sha256'] or wheel_record['bytes']!=result['build']['bytes'] or wheel_record not in result['outputs']:raise RuntimeError('current application completed wheel identity differs')
    from tools.w26_target_inputs import wheel_record as verify_wheel
    _verify_current_application_cached_modules(tools_root,profile['loader_source_closure'])
    metadata=verify_wheel(h.raw_file(wheel,expected=wheel_record)[0],wheel.name,'macos_arm64')
    if metadata['name']!='comsol-mcp' or metadata['version']!='0.1.10':raise RuntimeError('current installed wheel metadata differs')
    with zipfile.ZipFile(wheel) as archive:
        package={name for name in archive.namelist() if name.startswith('comsol_mcp/') and not name.endswith('/')}
        if package!={rel for rel in members if rel.startswith('comsol_mcp/')} or len(package)!=172:raise RuntimeError('current installed exact172 payload differs')
        for rel in package:
            if archive.read(rel)!=h.raw_file(members[rel],expected=source['snapshot_members'][rel])[0]:raise RuntimeError('current installed wheel/source bytes differ')
    return {'wheel':wheel_record,'result':profile['result'],'backup_source_sha256':source['snapshot_members']['comsol_mcp/_state_backup.py']['sha256']}

def backup_producer_sha(value):
    profile=value.get('current_application_profile')
    return BACKUP_PRODUCER_SHA if profile is None else current_application_profile(profile)['backup_source_sha256']
_ARCHIVE_OBSERVATION_TOKEN=object()

def _archive_fd_record(fd,path,links):
    path=Path(path);first=os.fstat(fd)
    if (path!=path.resolve() or not stat.S_ISREG(first.st_mode) or first.st_uid!=os.getuid()
            or first.st_nlink!=links or first.st_mode&0o022 or first.st_size>268435456):raise RuntimeError('actual archive FD owner/mode/link refused')
    os.lseek(fd,0,os.SEEK_SET);pieces=[]
    while part:=os.read(fd,1048576):pieces.append(part)
    raw=b''.join(pieces)
    if signature(first)!=signature(os.fstat(fd)) or signature(first)!=signature(path.lstat()) or len(raw)!=first.st_size:raise RuntimeError('actual archive original FD/name changed')
    return raw,{'path':str(path),**signature(first),'sha256':hashlib.sha256(raw).hexdigest()}

class _ObservedBackupArchive:
    """Read-only observer of one original producer publication; not a receipt factory."""
    def __init__(self,module,evidence,target,token,*,producer_sha=BACKUP_PRODUCER_SHA):
        if token is not _ARCHIVE_OBSERVATION_TOKEN:raise RuntimeError('JSON cannot mint original archive observer')
        self.token=token;self.module=module;self.evidence=Path(evidence);self.target=Path(target);self.fd=None;self.calls=0;self.record=None;self.close_attempted=False;self.close_failure=None
        self.original=module._publish_archive_no_clobber;self.producer_sha=producer_sha
        source=Path(module.__file__)
        if (raw_file(source)[1]['sha256']!=self.producer_sha or self.original.__module__!=module.__name__
                or Path(self.original.__code__.co_filename)!=source or module.create_full_backup.__globals__ is not module.__dict__):raise RuntimeError('actual installed backup original function/source differs')
        self.root_before=signature(self.evidence.lstat())
        if self.evidence!=self.evidence.resolve() or self.root_before['uid']!=os.getuid() or self.root_before['mode']&0o777!=0o700 or self.target.parent!=self.evidence:raise RuntimeError('actual archive observer task evidence refused')
        self.inventory_before={p.name:signature(p.lstat()) for p in self.evidence.iterdir()}
        if self.target.name in self.inventory_before:raise RuntimeError('actual archive destination was not unborn')
    def check_root(self):
        now=signature(self.evidence.lstat())
        if self.evidence!=self.evidence.resolve() or any(now[k]!=self.root_before[k] for k in ['dev','inode','uid','mode']):raise RuntimeError('actual archive task evidence directory replaced')
        for name,before in self.inventory_before.items():
            if signature((self.evidence/name).lstat())!=before:raise RuntimeError('actual archive pre-call evidence member changed')
    def publish(self,stage,target):
        self.check_root()
        original_stage_argument,original_target_argument=stage,target
        stage=Path(stage);target=Path(target);scratch=stage.parent
        if (self.calls!=0 or target!=self.target or stage.name!='full-state.tar' or scratch.parent!=self.evidence
                or not re.fullmatch(r'\.comsol-full-backup-[a-z0-9_]{8}',scratch.name) or scratch.name in self.inventory_before):raise RuntimeError('actual original publisher call/source root refused')
        self.calls+=1
        self.scratch=scratch;self.scratch_before=signature(scratch.lstat())
        if scratch!=scratch.resolve() or self.scratch_before['uid']!=os.getuid() or self.scratch_before['mode']&0o777!=0o700 or {p.name for p in scratch.iterdir()}!={'operations.sqlite3','full-state.tar'}:raise RuntimeError('actual original publisher directory refused')
        self.fd=os.open(stage,os.O_RDONLY|os.O_NOFOLLOW);_,self.before_publish=_archive_fd_record(self.fd,stage,1)
        # Actual original function, original arguments, original result/exception.
        result=self.original(original_stage_argument,original_target_argument)
        _,self.after_publish=_archive_fd_record(self.fd,stage,2);_,published=_raw_regular(target,link_count=2)
        if any(published[k]!=self.after_publish[k] for k in published if k!='path') or any(self.before_publish[k]!=self.after_publish[k] for k in ['dev','inode','uid','mode','bytes','sha256']):raise RuntimeError('actual publication changed original archive identity/bytes')
        return result
    def bind(self,receipt):
        self.check_root()
        if signature(self.scratch.lstat())!=self.scratch_before:raise RuntimeError('actual archive born publisher directory changed')
        if (self.calls!=1 or self.fd is None or receipt.get('success') is not True or receipt.get('operation')!='full_backup'
                or receipt.get('archive_path')!=str(self.target) or receipt.get('archive_sha256')!=self.after_publish['sha256']
                or receipt.get('archive_size_bytes')!=self.after_publish['bytes']):raise RuntimeError('actual fullbackup receipt differs from original publication')
        self.record={'schema':'w26-observed-original-fullbackup-archive/1','producer_source_sha256':self.producer_sha,'producer_receipt':receipt,'before_call_inventory':self.inventory_before,'before_publish':self.before_publish,'after_publish':self.after_publish,'scratch':{'path':str(self.scratch),'identity':signature(self.scratch.lstat())},'target':_raw_regular(self.target,link_count=2)[1],'original_publisher_calls':1}
        self.read();return self
    def read(self):
        if self.token is not _ARCHIVE_OBSERVATION_TOKEN or self.fd is None or self.record is None:raise RuntimeError('original archive observer/FD unavailable')
        self.check_root()
        if signature(self.scratch.lstat())!=self.record['scratch']['identity'] or {p.name for p in self.scratch.iterdir()}!={'operations.sqlite3','full-state.tar'}:raise RuntimeError('actual original publisher directory changed')
        raw,current=_archive_fd_record(self.fd,self.scratch/'full-state.tar',2);_,target=_raw_regular(self.target,self.record['target'],link_count=2)
        if current!=self.after_publish or any(current[k]!=target[k] for k in current if k!='path'):raise RuntimeError('actual archive post-publication FD/name/fullstat changed')
        return raw
    def close(self):
        if self.close_attempted:
            if self.close_failure is not None:raise self.close_failure
            return
        self.close_attempted=True
        if self.fd is not None:
            try:os.close(self.fd)
            except BaseException as failure:self.close_failure=failure;raise
            self.fd=None

_RETAINED_ARCHIVE_OBSERVERS=[]

def _close_archive_observation(observation,original_failure=None):
    # An uncertain close is never retried: preserve the original observer/FD.
    if any(item is observation for item in _RETAINED_ARCHIVE_OBSERVERS):
        raise RuntimeError('archive observer cleanup remains unknown; no retry')
    try:observation.close()
    except BaseException as cleanup_failure:
        _RETAINED_ARCHIVE_OBSERVERS.append(observation)
        error=RuntimeError('archive observer FD cleanup failure retained')
        error.original_cause=original_failure;error.cleanup_failure=cleanup_failure;error.archive_observation=observation
        raise error from (original_failure if original_failure is not None else cleanup_failure)

def observed_full_backup(module,home,target,evidence,*,current_profile=None):
    producer_sha=BACKUP_PRODUCER_SHA if current_profile is None else current_application_profile(current_profile)['backup_source_sha256']
    observation=_ObservedBackupArchive(module,evidence,target,_ARCHIVE_OBSERVATION_TOKEN,producer_sha=producer_sha)
    module._publish_archive_no_clobber=observation.publish
    try:
        receipt=module.create_full_backup(home,target)
        observation.bind(receipt)
        return receipt,observation
    except BaseException as original_failure:
        _close_archive_observation(observation,original_failure)
        raise
    finally:module._publish_archive_no_clobber=observation.original

def _actual_backup_archive_raw(path,observation):
    if not isinstance(observation,_ObservedBackupArchive) or observation.token is not _ARCHIVE_OBSERVATION_TOKEN or Path(path)!=observation.target:raise RuntimeError('JSON cannot mint original archive observer/FD')
    return observation.read()

def migration_bundle(archive,sidecar,closure,sealpath,destination,*,producer_archive_binding=None):
    import zipfile
    archive_bytes=raw_file(archive)[0] if producer_archive_binding is None else _actual_backup_archive_raw(archive,producer_archive_binding)
    components={'registered-full-state.tar':archive_bytes,'registered-full-state.tar.manifest.json':raw_file(sidecar)[0],'INPUT_CLOSURE.json':raw_file(Path(sealpath).parent/'metadata/INPUT_CLOSURE.json')[0],'SEAL_FINAL.json':raw_file(sealpath)[0]}
    for row in closure['project_files']:
        if not row['registered_blob']:components['supplemental/'+row['relative_path']]=raw_file(row['copy']['path'],row['copy'])[0]
    inventory={n:{'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()} for n,b in components.items()}
    metadata={'schema':'w26-single-closed-migration-components/1','registered_count':2,'supplemental_count':2,'total_project_files':4,'components':inventory,'production_backup_format':'UNCHANGED','history':closure['durable_history_sha256_same_algorithm_as_production']}
    with zipfile.ZipFile(destination,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for n,b in components.items():z.writestr(n,b)
        z.writestr('MIGRATION_MANIFEST.json',json.dumps(metadata,sort_keys=True).encode())
    return raw_file(destination)[1]

def verify_migration(path,closure):
    import zipfile
    raw,record=raw_file(path)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if z.testzip() is not None or len(z.namelist())!=len(set(z.namelist())):raise RuntimeError('migration zip integrity/membership differs')
        metadata=json.loads(z.read('MIGRATION_MANIFEST.json'));expected={'registered-full-state.tar','registered-full-state.tar.manifest.json','INPUT_CLOSURE.json','SEAL_FINAL.json','supplemental/parameters.json','supplemental/v3-native-smoke.mph'}
        if set(metadata['components'])!=expected or set(z.namelist())!=expected|{'MIGRATION_MANIFEST.json'} or metadata['schema']!='w26-single-closed-migration-components/1' or metadata['registered_count']!=2 or metadata['supplemental_count']!=2 or metadata['total_project_files']!=4 or metadata['history']!=closure['durable_history_sha256_same_algorithm_as_production']:raise RuntimeError('closed migration inventory differs')
        for n,row in metadata['components'].items():
            data=z.read(n)
            if len(data)!=row['bytes'] or hashlib.sha256(data).hexdigest()!=row['sha256']:raise RuntimeError('migration component tampered')
        for row in closure['project_files']:
            if not row['registered_blob']:
                data=z.read('supplemental/'+row['relative_path'])
                if len(data)!=row['copy']['bytes'] or hashlib.sha256(data).hexdigest()!=row['copy']['sha256']:raise RuntimeError('migration supplemental not same sealed input')
        seal=raw_file('/private/tmp/cvw26-lifecycle-inputs-c4563dee9656/SEAL_FINAL.json')[0]
        if z.read('SEAL_FINAL.json')!=seal or json.loads(z.read('INPUT_CLOSURE.json'))!=closure:raise RuntimeError('migration source seal closure differs')
        return metadata,{'archive':z.read('registered-full-state.tar'),'manifest':z.read('registered-full-state.tar.manifest.json')},record

def restore_archive_inputs(parts,inputs):
    # Retain the producer's exact filename: the unchanged manifest binds it.
    manifest_value=json.loads(parts['manifest'])
    name='before-candidate-open.full-state.tar'
    if manifest_value.get('archive_name')!=name:raise RuntimeError('closed original producer archive filename differs')
    inputs=Path(inputs);inputs.mkdir(mode=0o700,exist_ok=False)
    archive=inputs/name;manifest=inputs/(name+'.manifest.json')
    exclusive(archive,parts['archive']);exclusive(manifest,parts['manifest'])
    return archive,manifest

def run_phase(path,sha,phase):
    from importlib import metadata
    import jpype
    if jpype.isJVMStarted():raise RuntimeError('data phase forbids active JVM')
    value,closure=descriptor(path,sha);paths=data_paths(value);home=paths['home'];evidence=paths['evidence'];version=metadata.version('comsol-mcp');expected='0.1.9' if phase in {'old-read','old-restored-read'} else '0.1.10'
    if version!=expected:raise RuntimeError('installed data phase version differs')
    os.environ['COMSOL_SERVER_MCP_HOME']=str(home if phase!='restore' else paths['restored_home'])
    if phase=='old-read':
        home.mkdir(mode=0o700,exist_ok=False);copy=exclusive(home/'operations.sqlite3',raw_file(closure['whole_database']['copy']['path'],closure['whole_database']['copy'])[0]);observed=verify_database(home/'operations.sqlite3',closure,restored=False)
        result={'success':True,'phase':phase,'database':copy,'table_counts':observed['table_counts'],'durable_history_sha256':observed['durable_history_sha256'],'synthetic_rows_created':0,'history_replay':False}
    elif phase=='candidate-backup':
        from comsol_mcp import _state_backup as backup_module
        verify_database(home/'operations.sqlite3',closure,restored=False);backup=evidence/'before-candidate-open.full-state.tar';receipt,archive_observation=observed_full_backup(backup_module,home,backup,evidence,**({'current_profile':value['current_application_profile']} if value.get('current_application_profile') is not None else {}))
        original_failure=None
        try:
            if receipt['project_count']!=len(closure['projects']) or receipt['file_count']!=closure['registered_blob_count'] or receipt['durable_history_sha256']!=closure['durable_history_sha256_same_algorithm_as_production']:raise RuntimeError('actual standard registered backup counts/history differs')
            verify_database(home/'operations.sqlite3',closure,restored=False)
            bundle=migration_bundle(backup,Path(str(backup)+'.manifest.json'),closure,value['seal'],evidence/'single-migration-bundle.zip',producer_archive_binding=archive_observation);verify_migration(bundle['path'],closure)
            result={'success':True,'backup':receipt,'actual_archive_binding':archive_observation.record,'migration_bundle':bundle,'synthetic_rows_created':0}
            verify_seal(value['seal'],value['seal_sha256'])
            if jpype.isJVMStarted():raise RuntimeError('data phase unexpectedly started JVM')
            result['jpype_started_before_after']=[False,False];print(json.dumps(result,sort_keys=True),flush=True);return result
        except BaseException as failure:
            original_failure=failure;raise
        finally:_close_archive_observation(archive_observation,original_failure)
    elif phase=='restore':
        from comsol_mcp._state_backup import restore_full_backup
        meta,parts,bundle=verify_migration(evidence/'single-migration-bundle.zip',closure);archive,manifest=restore_archive_inputs(parts,evidence/'restore-inputs');paths['restored_home'].mkdir(mode=0o700,exist_ok=False);paths['workspace'].mkdir(mode=0o700,exist_ok=False)
        restored=restore_full_backup(paths['restored_home'],paths['workspace'],archive,manifest,confirm=True)
        if restored['worker_or_model_bindings_revived'] is not False or restored['operation_replay_performed'] is not False:raise RuntimeError('production restore no-replay contract differs')
        binding=restored_database_binding(restored,paths['restored_home']);observed=verify_database(paths['restored_home']/'operations.sqlite3',closure,restored=True,restore_binding=binding);project_map={r['project_id']:r['workspace'] for r in observed['rows']['projects']};copies=supplemental_copy(closure,project_map,paths['workspace']);readback=verify_restored(paths['restored_home'],paths['workspace'],closure,restore_binding=binding)
        exclusive(evidence/'ACTUAL_RESTORED_DB_BINDING.json',(json.dumps(readback['restored_database_binding'],sort_keys=True)+'\n').encode())
        result={'success':True,'restore':restored,'readback':readback,'supplemental_copies':copies,'migration_bundle':bundle}
    elif phase=='old-restored-read':
        binding=json.loads(raw_file(evidence/'ACTUAL_RESTORED_DB_BINDING.json')[0]);observed=verify_database(paths['restored_home']/'operations.sqlite3',closure,restored=True,restore_binding=binding);result={'success':True,'phase':phase,'table_counts':observed['table_counts'],'durable_history_sha256':observed['durable_history_sha256'],'readonly_old_code':True,'no_constructor_reconcile_replay':True}
    else:raise RuntimeError('unknown sealed actual data phase')
    verify_seal(value['seal'],value['seal_sha256'])
    if jpype.isJVMStarted():raise RuntimeError('data phase unexpectedly started JVM')
    result['jpype_started_before_after']=[False,False];print(json.dumps(result,sort_keys=True),flush=True);return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--descriptor',required=True);p.add_argument('--sha',required=True);p.add_argument('--phase',choices=['old-read','candidate-backup','restore','old-restored-read'],required=True);a=p.parse_args();run_phase(a.descriptor,a.sha,a.phase)
