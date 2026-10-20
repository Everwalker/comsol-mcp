"""Closed current app/bundle activity reusing release APIs and original W26 actor."""
import argparse,ast,contextlib,hashlib,json,os,re,socket,sys,time
from pathlib import Path
from types import SimpleNamespace
from tools import w26_runtime_actor_current as actor,full_project_release as release,w26_dependency_unit_current as deps
from tools.w26_prepare_current_inputs import _helpers,COMMON,load_closure

VERSION='0.1.10';TARGET='macos_arm64'
CURRENT_SOURCE_PROFILE='current174-r2'
SOURCE_LOCK=Path(__file__).with_name('w26_application_sources.lock.json')
CURRENT175_SOURCE_PROFILE='current175-r1'
CURRENT175_SOURCE_LOCK=Path(__file__).with_name('w26_application_sources_175.lock.json')
CURRENT175_R2_SOURCE_PROFILE='current175-r2'
CURRENT175_R2_SOURCE_LOCK=Path(__file__).with_name('w26_application_sources_175_r2.lock.json')
CURRENT175_R3_SOURCE_PROFILE='current175-r3'
CURRENT175_R3_SOURCE_LOCK=Path(__file__).with_name('w26_application_sources_175_r3.lock.json')
CURRENT175_R4_SOURCE_PROFILE='current175-r4'
CURRENT175_R4_SOURCE_LOCK=Path(__file__).with_name('w26_application_sources_175_r4.lock.json')
CURRENT175_R5_SOURCE_PROFILE='current175-r5'
CURRENT175_R5_SOURCE_LOCK=Path(__file__).with_name('w26_application_sources_175_r5.lock.json')
CURRENT175_R6_SOURCE_PROFILE='current175-r6'
CURRENT175_R6_SOURCE_LOCK=Path(__file__).with_name('w26_application_sources_175_r6.lock.json')
CURRENT175_R7_SOURCE_PROFILE='current175-r7'
CURRENT175_R7_SOURCE_LOCK=Path(__file__).with_name('w26_application_sources_175_r7_final.lock.json')
CURRENT175_R8_SOURCE_PROFILE='current175-r8'
CURRENT175_R8_SOURCE_LOCK=Path(__file__).with_name('w26_application_sources_175_r8_prepared.lock.json')
CURRENT175_R9_SOURCE_PROFILE='current175-r9'
CURRENT175_R9_SOURCE_LOCK=Path(__file__).with_name('w26_application_sources_175_r9_prepared.lock.json')
CURRENT175_R10_SOURCE_PROFILE='current175-r10'
CURRENT175_R10_SOURCE_LOCK=Path(__file__).with_name('w26_application_sources_175_r10_prepared.lock.json')
CURRENT175_R11_SOURCE_PROFILE='current175-r11'
CURRENT175_R11_SOURCE_LOCK=Path(__file__).with_name('w26_application_sources_175_r11_prepared.lock.json')
CURRENT175_R12_SOURCE_PROFILE='current175-r12'
CURRENT175_R12_SOURCE_LOCK=Path(__file__).with_name('w26_application_sources_175_r12_prepared.lock.json')
CURRENT175_R13_SOURCE_PROFILE='current175-r13'
CURRENT175_R13_SOURCE_LOCK=Path(__file__).with_name('w26_application_sources_175_r13_prepared.lock.json')
LATEST_SOURCE_PROFILE=CURRENT175_R13_SOURCE_PROFILE
LATEST_SOURCE_LOCK=CURRENT175_R13_SOURCE_LOCK
CURRENT175_PROFILES=frozenset({CURRENT175_R4_SOURCE_PROFILE,CURRENT175_R5_SOURCE_PROFILE,CURRENT175_R6_SOURCE_PROFILE,CURRENT175_R7_SOURCE_PROFILE,CURRENT175_R8_SOURCE_PROFILE,CURRENT175_R9_SOURCE_PROFILE,CURRENT175_R10_SOURCE_PROFILE,CURRENT175_R11_SOURCE_PROFILE,CURRENT175_R12_SOURCE_PROFILE,CURRENT175_R13_SOURCE_PROFILE})
VERSIONED_CURRENT_PROFILES=frozenset({CURRENT175_R9_SOURCE_PROFILE,CURRENT175_R10_SOURCE_PROFILE,CURRENT175_R11_SOURCE_PROFILE,CURRENT175_R12_SOURCE_PROFILE,CURRENT175_R13_SOURCE_PROFILE})

def current175_source_lock(profile):
    return {
        CURRENT175_R4_SOURCE_PROFILE:CURRENT175_R4_SOURCE_LOCK,
        CURRENT175_R5_SOURCE_PROFILE:CURRENT175_R5_SOURCE_LOCK,
        CURRENT175_R6_SOURCE_PROFILE:CURRENT175_R6_SOURCE_LOCK,
        CURRENT175_R7_SOURCE_PROFILE:CURRENT175_R7_SOURCE_LOCK,
        CURRENT175_R8_SOURCE_PROFILE:CURRENT175_R8_SOURCE_LOCK,
        CURRENT175_R9_SOURCE_PROFILE:CURRENT175_R9_SOURCE_LOCK,
        CURRENT175_R10_SOURCE_PROFILE:CURRENT175_R10_SOURCE_LOCK,
        CURRENT175_R11_SOURCE_PROFILE:CURRENT175_R11_SOURCE_LOCK,
        CURRENT175_R12_SOURCE_PROFILE:CURRENT175_R12_SOURCE_LOCK,
        CURRENT175_R13_SOURCE_PROFILE:CURRENT175_R13_SOURCE_LOCK,
    }[profile]

def add_current175_lock_sources(records,profile):
    if profile not in CURRENT175_PROFILES:return records
    h=_helpers()
    for closed_profile in sorted(CURRENT175_PROFILES):
        row=h.raw_file(current175_source_lock(closed_profile))[1]
        records[row['path']]=row
    # The versioned reader derives its own import closure from the exact
    # current-tools root. Bind every member of that closure into the producer
    # source record set before constructing the profile spec.
    from tools import w26_sealed_data_current as reader
    for name in sorted(reader.CURRENT_NAMES):
        row=h.raw_file(Path(reader.__file__).with_name(name))[1]
        records[row['path']]=row
    return records

def source_members(source):
    source=Path(source);members={}
    for p in sorted((source/'comsol_mcp').rglob('*')):
        if p.is_symlink():raise RuntimeError('package source link refused')
        rel=p.relative_to(source)
        if any(x in {'__pycache__','.pytest_cache'} or x.startswith('._') for x in rel.parts) or p.suffix in {'.pyc','.pyo'}:continue
        if p.is_file():members[rel.as_posix()]=p
        elif not p.is_dir():raise RuntimeError('package source special member refused')
    for name in release.SOURCE_ROOT_FILES:members[name]=source/name
    return members

def verify_version(source):
    import tomllib
    source=Path(source);h=_helpers();project=tomllib.loads(h.raw_file(source/'pyproject.toml')[0].decode())
    server=ast.parse(h.raw_file(source/'comsol_mcp/_server.py')[0]);versions=[]
    for node in server.body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='VERSION' for t in node.targets):versions.append(ast.literal_eval(node.value))
    if project['project']['version']!=VERSION or versions!=[VERSION]:raise RuntimeError('application declared/package version not exact current0.1.10')

def source_contract(members,profile):
    if profile=='app3-156':
        if len(members)!=156:raise RuntimeError('current Main package153 plus3 roots membership differs')
        return None
    if profile==CURRENT_SOURCE_PROFILE:count,lock_path=174,SOURCE_LOCK
    elif profile in CURRENT175_PROFILES:count,lock_path=175,current175_source_lock(profile)
    else:raise RuntimeError('unknown closed application source profile')
    h=_helpers();raw,record=h.raw_file(lock_path);lock=json.loads(raw)
    if lock.get('schema')!='w26-application-source-closure/1' or lock.get('profile')!=profile or lock.get('member_count')!=count or len(lock.get('members',{}))!=count or set(members)!=set(lock['members']):raise RuntimeError('exact current source closure membership differs')
    if profile in CURRENT175_PROFILES and (lock.get('package_count')!=172 or lock.get('root_count')!=3 or sum(rel.startswith('comsol_mcp/') for rel in members)!=172 or set(members)-{rel for rel in members if rel.startswith('comsol_mcp/')}!={'README.md','LICENSE','pyproject.toml'}):raise RuntimeError('exact current175 package172/root3 source closure differs')
    for rel,p in members.items():
        observed=h.raw_file(p)[1];expected=lock['members'][rel]
        if {'bytes':observed['bytes'],'sha256':observed['sha256']}!=expected:raise RuntimeError('exact current source locked bytes differ: '+rel)
    return record

def source_capture(source,destination,*,profile='app3-156'):
    if profile==CURRENT175_R9_SOURCE_PROFILE:
        return source_capture_r9(source,destination)
    if profile==CURRENT175_R10_SOURCE_PROFILE:
        return source_capture_r10(source,destination)
    if profile==CURRENT175_R11_SOURCE_PROFILE:
        return source_capture_r11(source,destination)
    if profile==CURRENT175_R12_SOURCE_PROFILE:
        return source_capture_r12(source,destination)
    if profile==CURRENT175_R13_SOURCE_PROFILE:
        return source_capture_r13(source,destination)
    h=_helpers();source=Path(source).resolve(strict=True);destination=Path(destination);members=source_members(source);verify_version(source)
    lock_record=source_contract(members,profile)
    rows={}
    for rel,p in members.items():rows[rel]=h.raw_file(p)[1]
    destination.mkdir(mode=0o700,exist_ok=False)
    for rel,row in rows.items():
        target=destination/rel;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700);raw,_=h.raw_file(Path(row['path']),expected=row);h.copy_blob(target,raw)
    if set(source_members(source))!=set(rows):raise RuntimeError('original source membership changed during capture')
    for row in rows.values():h.raw_file(Path(row['path']),expected=row)
    verify_version(destination)
    return {'source_profile':profile,'source_lock':lock_record,'original_source':str(source),'original_members':rows,'snapshot_source':str(destination),'snapshot_members':{rel:h.raw_file(destination/rel)[1] for rel in rows}}

def _canonical_source_member_map(members):
    raw=json.dumps(dict(members),sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()

def source_capture_r9(source,destination):
    """Capture the exact R9 build source and a separate immutable origin snapshot."""
    h=_helpers();source=Path(source).resolve(strict=True);destination=Path(destination)
    origin=destination.parent/'source-origin';members=source_members(source);verify_version(source)
    lock_record=source_contract(members,CURRENT175_R9_SOURCE_PROFILE)
    lock=json.loads(h.raw_file(Path(lock_record['path']),expected=lock_record)[0])
    if (lock.get('profile')!=CURRENT175_R9_SOURCE_PROFILE or lock.get('member_count')!=175
            or lock.get('package_count')!=172 or lock.get('root_count')!=3):
        raise RuntimeError('current R9 source lock schema/member counts differ')
    fingerprint=_canonical_source_member_map(lock['members'])
    if (destination.exists() or destination.is_symlink() or origin.exists() or origin.is_symlink()):
        raise RuntimeError('fresh R9 source/provenance snapshot paths already exist')
    rows={rel:h.raw_file(path)[1] for rel,path in members.items()}
    destination.mkdir(mode=0o700,exist_ok=False);origin.mkdir(mode=0o700,exist_ok=False)
    for rel,row in rows.items():
        raw=h.raw_file(Path(row['path']),expected=row)[0]
        for target_root in (destination,origin):
            target=target_root/rel;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            h.copy_blob(target,raw)
    if set(source_members(source))!=set(rows):raise RuntimeError('R9 original source membership changed during capture')
    for row in rows.values():h.raw_file(Path(row['path']),expected=row)
    verify_version(destination);verify_version(origin)
    snapshot_members={rel:h.raw_file(destination/rel)[1] for rel in rows}
    origin_members={rel:h.raw_file(origin/rel)[1] for rel in rows}
    for rel in rows:
        locked=lock['members'][rel]
        if ({key:snapshot_members[rel][key] for key in ('bytes','sha256')}!=locked
                or {key:origin_members[rel][key] for key in ('bytes','sha256')}!=locked):
            raise RuntimeError('R9 frozen source snapshot differs from exact source lock: '+rel)
    return {
        'source_profile':CURRENT175_R9_SOURCE_PROFILE,
        'source_lock':lock_record,
        'original_source':str(source),
        'snapshot_source':str(destination),
        'snapshot_members':snapshot_members,
        'origin_provenance_snapshot':{
            'schema':'w26-source-origin-provenance-snapshot/1',
            'original_source_path':str(source),
            'original_members':rows,
            'snapshot_source':str(origin),
            'snapshot_members':origin_members,
            'member_fingerprint_sha256':fingerprint,
        },
    }

def source_capture_r10(source,destination):
    """Capture assembly R3's exact 175 source bytes under the new R10 lock."""
    h=_helpers();source=Path(source).resolve(strict=True);destination=Path(destination)
    origin=destination.parent/'source-origin';members=source_members(source);verify_version(source)
    lock_record=source_contract(members,CURRENT175_R10_SOURCE_PROFILE)
    lock=json.loads(h.raw_file(CURRENT175_R10_SOURCE_LOCK,expected=lock_record)[0])
    if (lock.get('profile')!=CURRENT175_R10_SOURCE_PROFILE or lock.get('member_count')!=175
            or lock.get('package_count')!=172 or lock.get('root_count')!=3):
        raise RuntimeError('current R10 source lock schema/member counts differ')
    fingerprint=_canonical_source_member_map(lock['members'])
    if destination.exists() or destination.is_symlink() or origin.exists() or origin.is_symlink():
        raise RuntimeError('fresh R10 source/provenance snapshot paths already exist')
    rows={rel:h.raw_file(path)[1] for rel,path in members.items()}
    destination.mkdir(mode=0o700,exist_ok=False);origin.mkdir(mode=0o700,exist_ok=False)
    for rel,row in rows.items():
        raw=h.raw_file(Path(row['path']),expected=row)[0]
        for target_root in (destination,origin):
            target=target_root/rel;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            h.copy_blob(target,raw)
    if set(source_members(source))!=set(rows):raise RuntimeError('R10 original source membership changed during capture')
    for row in rows.values():h.raw_file(Path(row['path']),expected=row)
    verify_version(destination);verify_version(origin)
    snapshot_members={rel:h.raw_file(destination/rel)[1] for rel in rows}
    origin_members={rel:h.raw_file(origin/rel)[1] for rel in rows}
    for rel in rows:
        locked=lock['members'][rel]
        if ({key:snapshot_members[rel][key] for key in ('bytes','sha256')}!=locked
                or {key:origin_members[rel][key] for key in ('bytes','sha256')}!=locked):
            raise RuntimeError('R10 frozen source/provenance snapshot differs from exact new source lock: '+rel)
    return {
        'source_profile':CURRENT175_R10_SOURCE_PROFILE,
        'source_lock':lock_record,
        'original_source':str(source),
        'snapshot_source':str(destination),
        'snapshot_members':snapshot_members,
        'origin_provenance_snapshot':{
            'schema':'w26-source-origin-provenance-snapshot/1',
            'original_source_path':str(source),
            'original_members':rows,
            'snapshot_source':str(origin),
            'snapshot_members':origin_members,
            'member_fingerprint_sha256':fingerprint,
        },
    }


def source_capture_r11(source,destination):
    """Capture assembly R3's exact 175 source bytes under the new R11 lock."""
    h=_helpers();source=Path(source).resolve(strict=True);destination=Path(destination)
    if destination.exists() or destination.is_symlink():
        raise RuntimeError('fresh R11 source/provenance snapshot paths already exist')
    raw_parent=destination.parent
    canonical_parent=raw_parent.resolve(strict=True)
    if raw_parent!=canonical_parent:
        raise RuntimeError('R11 source/provenance snapshot parent is not canonical')
    destination=canonical_parent/destination.name
    origin=destination.parent/'source-origin';members=source_members(source);verify_version(source)
    lock_record=source_contract(members,CURRENT175_R11_SOURCE_PROFILE)
    lock=json.loads(h.raw_file(CURRENT175_R11_SOURCE_LOCK,expected=lock_record)[0])
    if (lock.get('profile')!=CURRENT175_R11_SOURCE_PROFILE or lock.get('member_count')!=175
            or lock.get('package_count')!=172 or lock.get('root_count')!=3):
        raise RuntimeError('current R11 source lock schema/member counts differ')
    fingerprint=_canonical_source_member_map(lock['members'])
    if destination.exists() or destination.is_symlink() or origin.exists() or origin.is_symlink():
        raise RuntimeError('fresh R11 source/provenance snapshot paths already exist')
    rows={rel:h.raw_file(path)[1] for rel,path in members.items()}
    destination.mkdir(mode=0o700,exist_ok=False);origin.mkdir(mode=0o700,exist_ok=False)
    for rel,row in rows.items():
        raw=h.raw_file(Path(row['path']),expected=row)[0]
        for target_root in (destination,origin):
            target=target_root/rel;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            h.copy_blob(target,raw)
    if set(source_members(source))!=set(rows):raise RuntimeError('R11 original source membership changed during capture')
    for row in rows.values():h.raw_file(Path(row['path']),expected=row)
    verify_version(destination);verify_version(origin)
    snapshot_members={rel:h.raw_file(destination/rel)[1] for rel in rows}
    origin_members={rel:h.raw_file(origin/rel)[1] for rel in rows}
    for rel in rows:
        locked=lock['members'][rel]
        if ({key:snapshot_members[rel][key] for key in ('bytes','sha256')}!=locked
                or {key:origin_members[rel][key] for key in ('bytes','sha256')}!=locked):
            raise RuntimeError('R11 frozen source/provenance snapshot differs from exact new source lock: '+rel)
    return {
        'source_profile':CURRENT175_R11_SOURCE_PROFILE,
        'source_lock':lock_record,
        'original_source':str(source),
        'snapshot_source':str(destination),
        'snapshot_members':snapshot_members,
        'origin_provenance_snapshot':{
            'schema':'w26-source-origin-provenance-snapshot/1',
            'original_source_path':str(source),
            'original_members':rows,
            'snapshot_source':str(origin),
            'snapshot_members':origin_members,
            'member_fingerprint_sha256':fingerprint,
        },
    }



def source_capture_r12(source,destination):
    """Capture assembly R3's exact 175 source bytes under the new R12 lock."""
    h=_helpers();source=Path(source).resolve(strict=True);destination=Path(destination)
    if destination.exists() or destination.is_symlink():
        raise RuntimeError('fresh R12 source/provenance snapshot paths already exist')
    raw_parent=destination.parent
    canonical_parent=raw_parent.resolve(strict=True)
    if raw_parent!=canonical_parent:
        raise RuntimeError('R12 source/provenance snapshot parent is not canonical')
    destination=canonical_parent/destination.name
    origin=destination.parent/'source-origin';members=source_members(source);verify_version(source)
    lock_record=source_contract(members,CURRENT175_R12_SOURCE_PROFILE)
    lock=json.loads(h.raw_file(CURRENT175_R12_SOURCE_LOCK,expected=lock_record)[0])
    if (lock.get('profile')!=CURRENT175_R12_SOURCE_PROFILE or lock.get('member_count')!=175
            or lock.get('package_count')!=172 or lock.get('root_count')!=3):
        raise RuntimeError('current R12 source lock schema/member counts differ')
    fingerprint=_canonical_source_member_map(lock['members'])
    if destination.exists() or destination.is_symlink() or origin.exists() or origin.is_symlink():
        raise RuntimeError('fresh R12 source/provenance snapshot paths already exist')
    rows={rel:h.raw_file(path)[1] for rel,path in members.items()}
    destination.mkdir(mode=0o700,exist_ok=False);origin.mkdir(mode=0o700,exist_ok=False)
    for rel,row in rows.items():
        raw=h.raw_file(Path(row['path']),expected=row)[0]
        for target_root in (destination,origin):
            target=target_root/rel;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            h.copy_blob(target,raw)
    if set(source_members(source))!=set(rows):raise RuntimeError('R12 original source membership changed during capture')
    for row in rows.values():h.raw_file(Path(row['path']),expected=row)
    verify_version(destination);verify_version(origin)
    snapshot_members={rel:h.raw_file(destination/rel)[1] for rel in rows}
    origin_members={rel:h.raw_file(origin/rel)[1] for rel in rows}
    for rel in rows:
        locked=lock['members'][rel]
        if ({key:snapshot_members[rel][key] for key in ('bytes','sha256')}!=locked
                or {key:origin_members[rel][key] for key in ('bytes','sha256')}!=locked):
            raise RuntimeError('R12 frozen source/provenance snapshot differs from exact new source lock: '+rel)
    return {
        'source_profile':CURRENT175_R12_SOURCE_PROFILE,
        'source_lock':lock_record,
        'original_source':str(source),
        'snapshot_source':str(destination),
        'snapshot_members':snapshot_members,
        'origin_provenance_snapshot':{
            'schema':'w26-source-origin-provenance-snapshot/1',
            'original_source_path':str(source),
            'original_members':rows,
            'snapshot_source':str(origin),
            'snapshot_members':origin_members,
            'member_fingerprint_sha256':fingerprint,
        },
    }


def source_capture_r13(source,destination):
    """Capture the coordinated R29 175-member source under its new R13 lock."""
    h=_helpers();source=Path(source).resolve(strict=True);destination=Path(destination)
    if destination.exists() or destination.is_symlink():
        raise RuntimeError('fresh R13 source/provenance snapshot paths already exist')
    raw_parent=destination.parent
    canonical_parent=raw_parent.resolve(strict=True)
    if raw_parent!=canonical_parent:
        raise RuntimeError('R13 source/provenance snapshot parent is not canonical')
    destination=canonical_parent/destination.name
    origin=destination.parent/'source-origin';members=source_members(source);verify_version(source)
    lock_record=source_contract(members,CURRENT175_R13_SOURCE_PROFILE)
    lock=json.loads(h.raw_file(CURRENT175_R13_SOURCE_LOCK,expected=lock_record)[0])
    if (lock.get('profile')!=CURRENT175_R13_SOURCE_PROFILE or lock.get('member_count')!=175
            or lock.get('package_count')!=172 or lock.get('root_count')!=3):
        raise RuntimeError('current R13 source lock schema/member counts differ')
    fingerprint=_canonical_source_member_map(lock['members'])
    if destination.exists() or destination.is_symlink() or origin.exists() or origin.is_symlink():
        raise RuntimeError('fresh R13 source/provenance snapshot paths already exist')
    rows={rel:h.raw_file(path)[1] for rel,path in members.items()}
    destination.mkdir(mode=0o700,exist_ok=False);origin.mkdir(mode=0o700,exist_ok=False)
    for rel,row in rows.items():
        raw=h.raw_file(Path(row['path']),expected=row)[0]
        for target_root in (destination,origin):
            target=target_root/rel;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            h.copy_blob(target,raw)
    if set(source_members(source))!=set(rows):raise RuntimeError('R13 original source membership changed during capture')
    for row in rows.values():h.raw_file(Path(row['path']),expected=row)
    verify_version(destination);verify_version(origin)
    snapshot_members={rel:h.raw_file(destination/rel)[1] for rel in rows}
    origin_members={rel:h.raw_file(origin/rel)[1] for rel in rows}
    for rel in rows:
        locked=lock['members'][rel]
        if ({key:snapshot_members[rel][key] for key in ('bytes','sha256')}!=locked
                or {key:origin_members[rel][key] for key in ('bytes','sha256')}!=locked):
            raise RuntimeError('R13 frozen source/provenance snapshot differs from exact new source lock: '+rel)
    return {
        'source_profile':CURRENT175_R13_SOURCE_PROFILE,
        'source_lock':lock_record,
        'original_source':str(source),
        'snapshot_source':str(destination),
        'snapshot_members':snapshot_members,
        'origin_provenance_snapshot':{
            'schema':'w26-source-origin-provenance-snapshot/1',
            'original_source_path':str(source),
            'original_members':rows,
            'snapshot_source':str(origin),
            'snapshot_members':origin_members,
            'member_fingerprint_sha256':fingerprint,
        },
    }
def verify_source_input_r9(source,*,require_live_origin=False,selected_lock_record=None):
    """Revalidate immutable R9 snapshots; optionally audit the live origin at dispatch."""
    h=_helpers();source_root=Path(source.get('snapshot_source',''));origin_record=source.get('origin_provenance_snapshot')
    if (source.get('source_profile')!=CURRENT175_R9_SOURCE_PROFILE or not isinstance(origin_record,dict)
            or set(source)!={'source_profile','source_lock','original_source','snapshot_source','snapshot_members','origin_provenance_snapshot'}
            or set(origin_record)!={'schema','original_source_path','original_members','snapshot_source','snapshot_members','member_fingerprint_sha256'}
            or origin_record.get('schema')!='w26-source-origin-provenance-snapshot/1'
            or origin_record.get('original_source_path')!=source.get('original_source')):
        raise RuntimeError('closed current R9 source/provenance capture fields differ')
    lock_row=source['source_lock']
    lock_path=(Path(selected_lock_record['path']).resolve(strict=True) if selected_lock_record is not None
               else CURRENT175_R9_SOURCE_LOCK.resolve(strict=True))
    if lock_row.get('path')!=str(lock_path) or (selected_lock_record is not None and lock_row!=selected_lock_record):
        raise RuntimeError('current R9 exact selected source-lock record differs')
    lock=json.loads(h.raw_file(lock_path,expected=lock_row)[0])
    if (lock.get('schema')!='w26-application-source-closure/1' or lock.get('profile')!=CURRENT175_R9_SOURCE_PROFILE
            or lock.get('member_count')!=175 or lock.get('package_count')!=172 or lock.get('root_count')!=3
            or set(lock.get('members',{}))!=set(source['snapshot_members'])
            or set(lock['members'])!=set(origin_record['snapshot_members'])
            or set(lock['members'])!=set(origin_record['original_members'])):
        raise RuntimeError('current R9 lock/snapshot membership differs')
    fingerprint=_canonical_source_member_map(lock['members'])
    if origin_record.get('member_fingerprint_sha256')!=fingerprint:
        raise RuntimeError('current R9 source provenance member fingerprint differs')
    if source_root!=source_root.resolve(strict=True):
        raise RuntimeError('current R9 immutable source snapshot path differs')
    if (source_root.parent.parent==Path('/private/tmp')
            and re.fullmatch(r'cvw26-application-[0-9a-f]{8}',source_root.parent.name)
            and source_root.name!='source-input'):
        raise RuntimeError('current R9 producer source snapshot is not at its exact root path')
    origin_root=Path(origin_record['snapshot_source'])
    if origin_root!=source_root.parent/'source-origin' or origin_root!=origin_root.resolve(strict=True):
        raise RuntimeError('current R9 immutable origin snapshot path differs')
    original_root=Path(source['original_source'])
    if not original_root.is_absolute() or origin_record['original_source_path']!=str(original_root):
        raise RuntimeError('current R9 original-source provenance path differs')
    verify_version(source_root);verify_version(origin_root);h.secure_dir(source_root);h.secure_dir(origin_root)
    if (set(source_members(source_root))!=set(lock['members']) or set(source_members(origin_root))!=set(lock['members'])):
        raise RuntimeError('current R9 source/provenance tree membership differs')
    if require_live_origin and (original_root!=original_root.resolve(strict=True)
                                or set(source_members(original_root))!=set(lock['members'])):
        raise RuntimeError('current R9 live original source membership differs at dispatch')
    for rel in sorted(lock['members']):
        expected=lock['members'][rel]
        for root,records in ((source_root,source['snapshot_members']),
                             (origin_root,origin_record['snapshot_members']),
                             (original_root,origin_record['original_members'])):
            row=records.get(rel);path=root/rel
            if not isinstance(row,dict) or row.get('path')!=str(path):
                raise RuntimeError('current R9 snapshot member path differs')
            if {key:row.get(key) for key in ('bytes','sha256')}!=expected:
                raise RuntimeError('current R9 saved source provenance differs from exact lock: '+rel)
            if root!=original_root or require_live_origin:
                observed=h.raw_file(path,expected=row)[1]
                if {key:observed[key] for key in ('bytes','sha256')}!=expected:
                    raise RuntimeError('current R9 source snapshot member differs from exact lock: '+rel)
    return {
        'source_snapshot_member_count':len(source['snapshot_members']),
        'source_snapshot_fingerprint_sha256':fingerprint,
        'origin_snapshot_member_count':len(origin_record['snapshot_members']),
        'origin_snapshot_fingerprint_sha256':fingerprint,
    }

def verify_source_input_r10(source,*,require_live_origin=False,selected_lock_record=None):
    """Revalidate the explicit R10 lock and both immutable R3-derived snapshots."""
    h=_helpers();source_root=Path(source.get('snapshot_source',''));origin_record=source.get('origin_provenance_snapshot')
    if (source.get('source_profile')!=CURRENT175_R10_SOURCE_PROFILE or not isinstance(origin_record,dict)
            or set(source)!={'source_profile','source_lock','original_source','snapshot_source','snapshot_members','origin_provenance_snapshot'}
            or set(origin_record)!={'schema','original_source_path','original_members','snapshot_source','snapshot_members','member_fingerprint_sha256'}
            or origin_record.get('schema')!='w26-source-origin-provenance-snapshot/1'
            or origin_record.get('original_source_path')!=source.get('original_source')):
        raise RuntimeError('closed current R10 source/provenance capture fields differ')
    lock_path=CURRENT175_R10_SOURCE_LOCK.resolve(strict=True)
    lock_row=source['source_lock']
    if (selected_lock_record is None or lock_row!=selected_lock_record
            or lock_row.get('path')!=str(lock_path)):
        raise RuntimeError('current R10 exact selected source-lock record differs')
    lock=json.loads(h.raw_file(lock_path,expected=lock_row)[0])
    if (lock.get('schema')!='w26-application-source-closure/1' or lock.get('profile')!=CURRENT175_R10_SOURCE_PROFILE
            or lock.get('member_count')!=175 or lock.get('package_count')!=172 or lock.get('root_count')!=3
            or set(lock.get('members',{}))!=set(source['snapshot_members'])
            or set(lock['members'])!=set(origin_record['snapshot_members'])
            or set(lock['members'])!=set(origin_record['original_members'])):
        raise RuntimeError('current R10 lock/snapshot membership differs')
    fingerprint=_canonical_source_member_map(lock['members'])
    if origin_record.get('member_fingerprint_sha256')!=fingerprint:
        raise RuntimeError('current R10 source provenance member fingerprint differs')
    if source_root!=source_root.resolve(strict=True):raise RuntimeError('current R10 immutable source snapshot path differs')
    if (source_root.parent.parent==Path('/private/tmp')
            and re.fullmatch(r'cvw26-application-[0-9a-f]{8}',source_root.parent.name)
            and source_root.name!='source-input'):
        raise RuntimeError('current R10 producer source snapshot is not at its exact root path')
    origin_root=Path(origin_record['snapshot_source'])
    if origin_root!=source_root.parent/'source-origin' or origin_root!=origin_root.resolve(strict=True):
        raise RuntimeError('current R10 immutable origin snapshot path differs')
    original_root=Path(source['original_source'])
    if not original_root.is_absolute() or origin_record['original_source_path']!=str(original_root):
        raise RuntimeError('current R10 original-source provenance path differs')
    verify_version(source_root);verify_version(origin_root);h.secure_dir(source_root);h.secure_dir(origin_root)
    if set(source_members(source_root))!=set(lock['members']) or set(source_members(origin_root))!=set(lock['members']):
        raise RuntimeError('current R10 source/provenance tree membership differs')
    if require_live_origin and (original_root!=original_root.resolve(strict=True)
                                or set(source_members(original_root))!=set(lock['members'])):
        raise RuntimeError('current R10 live original source membership differs at dispatch')
    for rel in sorted(lock['members']):
        expected=lock['members'][rel]
        for current_root,records in ((source_root,source['snapshot_members']),
                                     (origin_root,origin_record['snapshot_members']),
                                     (original_root,origin_record['original_members'])):
            row=records.get(rel);path=current_root/rel
            if not isinstance(row,dict) or row.get('path')!=str(path):
                raise RuntimeError('current R10 snapshot member path differs')
            if {key:row.get(key) for key in ('bytes','sha256')}!=expected:
                raise RuntimeError('current R10 saved source provenance differs from exact lock: '+rel)
            if current_root!=original_root or require_live_origin:
                observed=h.raw_file(path,expected=row)[1]
                if {key:observed[key] for key in ('bytes','sha256')}!=expected:
                    raise RuntimeError('current R10 source snapshot member differs from exact lock: '+rel)
    return {'source_snapshot_member_count':len(source['snapshot_members']),
            'source_snapshot_fingerprint_sha256':fingerprint,
            'origin_snapshot_member_count':len(origin_record['snapshot_members']),
            'origin_snapshot_fingerprint_sha256':fingerprint}


def verify_source_input_r11(source,*,require_live_origin=False,selected_lock_record=None):
    """Revalidate the explicit R11 lock and both immutable R3-derived snapshots."""
    h=_helpers();source_root=Path(source.get('snapshot_source',''));origin_record=source.get('origin_provenance_snapshot')
    if (source.get('source_profile')!=CURRENT175_R11_SOURCE_PROFILE or not isinstance(origin_record,dict)
            or set(source)!={'source_profile','source_lock','original_source','snapshot_source','snapshot_members','origin_provenance_snapshot'}
            or set(origin_record)!={'schema','original_source_path','original_members','snapshot_source','snapshot_members','member_fingerprint_sha256'}
            or origin_record.get('schema')!='w26-source-origin-provenance-snapshot/1'
            or origin_record.get('original_source_path')!=source.get('original_source')):
        raise RuntimeError('closed current R11 source/provenance capture fields differ')
    lock_path=CURRENT175_R11_SOURCE_LOCK.resolve(strict=True)
    lock_row=source['source_lock']
    if (selected_lock_record is None or lock_row!=selected_lock_record
            or lock_row.get('path')!=str(lock_path)):
        raise RuntimeError('current R11 exact selected source-lock record differs')
    lock=json.loads(h.raw_file(lock_path,expected=lock_row)[0])
    if (lock.get('schema')!='w26-application-source-closure/1' or lock.get('profile')!=CURRENT175_R11_SOURCE_PROFILE
            or lock.get('member_count')!=175 or lock.get('package_count')!=172 or lock.get('root_count')!=3
            or set(lock.get('members',{}))!=set(source['snapshot_members'])
            or set(lock['members'])!=set(origin_record['snapshot_members'])
            or set(lock['members'])!=set(origin_record['original_members'])):
        raise RuntimeError('current R11 lock/snapshot membership differs')
    fingerprint=_canonical_source_member_map(lock['members'])
    if origin_record.get('member_fingerprint_sha256')!=fingerprint:
        raise RuntimeError('current R11 source provenance member fingerprint differs')
    if source_root!=source_root.resolve(strict=True):raise RuntimeError('current R11 immutable source snapshot path differs')
    if (source_root.parent.parent==Path('/private/tmp')
            and re.fullmatch(r'cvw26-application-[0-9a-f]{8}',source_root.parent.name)
            and source_root.name!='source-input'):
        raise RuntimeError('current R11 producer source snapshot is not at its exact root path')
    origin_root=Path(origin_record['snapshot_source'])
    if origin_root!=source_root.parent/'source-origin' or origin_root!=origin_root.resolve(strict=True):
        raise RuntimeError('current R11 immutable origin snapshot path differs')
    original_root=Path(source['original_source'])
    if not original_root.is_absolute() or origin_record['original_source_path']!=str(original_root):
        raise RuntimeError('current R11 original-source provenance path differs')
    verify_version(source_root);verify_version(origin_root);h.secure_dir(source_root);h.secure_dir(origin_root)
    if set(source_members(source_root))!=set(lock['members']) or set(source_members(origin_root))!=set(lock['members']):
        raise RuntimeError('current R11 source/provenance tree membership differs')
    if require_live_origin and (original_root!=original_root.resolve(strict=True)
                                or set(source_members(original_root))!=set(lock['members'])):
        raise RuntimeError('current R11 live original source membership differs at dispatch')
    for rel in sorted(lock['members']):
        expected=lock['members'][rel]
        for current_root,records in ((source_root,source['snapshot_members']),
                                     (origin_root,origin_record['snapshot_members']),
                                     (original_root,origin_record['original_members'])):
            row=records.get(rel);path=current_root/rel
            if not isinstance(row,dict) or row.get('path')!=str(path):
                raise RuntimeError('current R11 snapshot member path differs')
            if {key:row.get(key) for key in ('bytes','sha256')}!=expected:
                raise RuntimeError('current R11 saved source provenance differs from exact lock: '+rel)
            if current_root!=original_root or require_live_origin:
                observed=h.raw_file(path,expected=row)[1]
                if {key:observed[key] for key in ('bytes','sha256')}!=expected:
                    raise RuntimeError('current R11 source snapshot member differs from exact lock: '+rel)
    return {'source_snapshot_member_count':len(source['snapshot_members']),
            'source_snapshot_fingerprint_sha256':fingerprint,
            'origin_snapshot_member_count':len(origin_record['snapshot_members']),
            'origin_snapshot_fingerprint_sha256':fingerprint}



def verify_source_input_r12(source,*,require_live_origin=False,selected_lock_record=None):
    """Revalidate the explicit R12 lock and both immutable R3-derived snapshots."""
    h=_helpers();source_root=Path(source.get('snapshot_source',''));origin_record=source.get('origin_provenance_snapshot')
    if (source.get('source_profile')!=CURRENT175_R12_SOURCE_PROFILE or not isinstance(origin_record,dict)
            or set(source)!={'source_profile','source_lock','original_source','snapshot_source','snapshot_members','origin_provenance_snapshot'}
            or set(origin_record)!={'schema','original_source_path','original_members','snapshot_source','snapshot_members','member_fingerprint_sha256'}
            or origin_record.get('schema')!='w26-source-origin-provenance-snapshot/1'
            or origin_record.get('original_source_path')!=source.get('original_source')):
        raise RuntimeError('closed current R12 source/provenance capture fields differ')
    lock_path=CURRENT175_R12_SOURCE_LOCK.resolve(strict=True)
    lock_row=source['source_lock']
    if (selected_lock_record is None or lock_row!=selected_lock_record
            or lock_row.get('path')!=str(lock_path)):
        raise RuntimeError('current R12 exact selected source-lock record differs')
    lock=json.loads(h.raw_file(lock_path,expected=lock_row)[0])
    if (lock.get('schema')!='w26-application-source-closure/1' or lock.get('profile')!=CURRENT175_R12_SOURCE_PROFILE
            or lock.get('member_count')!=175 or lock.get('package_count')!=172 or lock.get('root_count')!=3
            or set(lock.get('members',{}))!=set(source['snapshot_members'])
            or set(lock['members'])!=set(origin_record['snapshot_members'])
            or set(lock['members'])!=set(origin_record['original_members'])):
        raise RuntimeError('current R12 lock/snapshot membership differs')
    fingerprint=_canonical_source_member_map(lock['members'])
    if origin_record.get('member_fingerprint_sha256')!=fingerprint:
        raise RuntimeError('current R12 source provenance member fingerprint differs')
    if source_root!=source_root.resolve(strict=True):raise RuntimeError('current R12 immutable source snapshot path differs')
    if (source_root.parent.parent==Path('/private/tmp')
            and re.fullmatch(r'cvw26-application-[0-9a-f]{8}',source_root.parent.name)
            and source_root.name!='source-input'):
        raise RuntimeError('current R12 producer source snapshot is not at its exact root path')
    origin_root=Path(origin_record['snapshot_source'])
    if origin_root!=source_root.parent/'source-origin' or origin_root!=origin_root.resolve(strict=True):
        raise RuntimeError('current R12 immutable origin snapshot path differs')
    original_root=Path(source['original_source'])
    if not original_root.is_absolute() or origin_record['original_source_path']!=str(original_root):
        raise RuntimeError('current R12 original-source provenance path differs')
    verify_version(source_root);verify_version(origin_root);h.secure_dir(source_root);h.secure_dir(origin_root)
    if set(source_members(source_root))!=set(lock['members']) or set(source_members(origin_root))!=set(lock['members']):
        raise RuntimeError('current R12 source/provenance tree membership differs')
    if require_live_origin and (original_root!=original_root.resolve(strict=True)
                                or set(source_members(original_root))!=set(lock['members'])):
        raise RuntimeError('current R12 live original source membership differs at dispatch')
    for rel in sorted(lock['members']):
        expected=lock['members'][rel]
        for current_root,records in ((source_root,source['snapshot_members']),
                                     (origin_root,origin_record['snapshot_members']),
                                     (original_root,origin_record['original_members'])):
            row=records.get(rel);path=current_root/rel
            if not isinstance(row,dict) or row.get('path')!=str(path):
                raise RuntimeError('current R12 snapshot member path differs')
            if {key:row.get(key) for key in ('bytes','sha256')}!=expected:
                raise RuntimeError('current R12 saved source provenance differs from exact lock: '+rel)
            if current_root!=original_root or require_live_origin:
                observed=h.raw_file(path,expected=row)[1]
                if {key:observed[key] for key in ('bytes','sha256')}!=expected:
                    raise RuntimeError('current R12 source snapshot member differs from exact lock: '+rel)
    return {'source_snapshot_member_count':len(source['snapshot_members']),
            'source_snapshot_fingerprint_sha256':fingerprint,
            'origin_snapshot_member_count':len(origin_record['snapshot_members']),
            'origin_snapshot_fingerprint_sha256':fingerprint}


def verify_source_input_r13(source,*,require_live_origin=False,selected_lock_record=None):
    """Revalidate the exact R13 package lock and both isolated source snapshots."""
    h=_helpers();source_root=Path(source.get('snapshot_source',''));origin_record=source.get('origin_provenance_snapshot')
    if (source.get('source_profile')!=CURRENT175_R13_SOURCE_PROFILE or not isinstance(origin_record,dict)
            or set(source)!={'source_profile','source_lock','original_source','snapshot_source','snapshot_members','origin_provenance_snapshot'}
            or set(origin_record)!={'schema','original_source_path','original_members','snapshot_source','snapshot_members','member_fingerprint_sha256'}
            or origin_record.get('schema')!='w26-source-origin-provenance-snapshot/1'
            or origin_record.get('original_source_path')!=source.get('original_source')):
        raise RuntimeError('closed current R13 source/provenance capture fields differ')
    lock_path=CURRENT175_R13_SOURCE_LOCK.resolve(strict=True)
    lock_row=source['source_lock']
    if (selected_lock_record is None or lock_row!=selected_lock_record
            or lock_row.get('path')!=str(lock_path)):
        raise RuntimeError('current R13 exact selected source-lock record differs')
    lock=json.loads(h.raw_file(lock_path,expected=lock_row)[0])
    if (lock.get('schema')!='w26-application-source-closure/1' or lock.get('profile')!=CURRENT175_R13_SOURCE_PROFILE
            or lock.get('member_count')!=175 or lock.get('package_count')!=172 or lock.get('root_count')!=3
            or set(lock.get('members',{}))!=set(source['snapshot_members'])
            or set(lock['members'])!=set(origin_record['snapshot_members'])
            or set(lock['members'])!=set(origin_record['original_members'])):
        raise RuntimeError('current R13 lock/snapshot membership differs')
    fingerprint=_canonical_source_member_map(lock['members'])
    if origin_record.get('member_fingerprint_sha256')!=fingerprint:
        raise RuntimeError('current R13 source provenance member fingerprint differs')
    if source_root!=source_root.resolve(strict=True):raise RuntimeError('current R13 immutable source snapshot path differs')
    if (source_root.parent.parent==Path('/private/tmp')
            and re.fullmatch(r'cvw26-application-[0-9a-f]{8}',source_root.parent.name)
            and source_root.name!='source-input'):
        raise RuntimeError('current R13 producer source snapshot is not at its exact root path')
    origin_root=Path(origin_record['snapshot_source'])
    if origin_root!=source_root.parent/'source-origin' or origin_root!=origin_root.resolve(strict=True):
        raise RuntimeError('current R13 immutable origin snapshot path differs')
    original_root=Path(source['original_source'])
    if not original_root.is_absolute() or origin_record['original_source_path']!=str(original_root):
        raise RuntimeError('current R13 original-source provenance path differs')
    verify_version(source_root);verify_version(origin_root);h.secure_dir(source_root);h.secure_dir(origin_root)
    if set(source_members(source_root))!=set(lock['members']) or set(source_members(origin_root))!=set(lock['members']):
        raise RuntimeError('current R13 source/provenance tree membership differs')
    if require_live_origin and (original_root!=original_root.resolve(strict=True)
                                or set(source_members(original_root))!=set(lock['members'])):
        raise RuntimeError('current R13 live original source membership differs at dispatch')
    for rel in sorted(lock['members']):
        expected=lock['members'][rel]
        for current_root,records in ((source_root,source['snapshot_members']),
                                     (origin_root,origin_record['snapshot_members']),
                                     (original_root,origin_record['original_members'])):
            row=records.get(rel);path=current_root/rel
            if not isinstance(row,dict) or row.get('path')!=str(path):
                raise RuntimeError('current R13 snapshot member path differs')
            if {key:row.get(key) for key in ('bytes','sha256')}!=expected:
                raise RuntimeError('current R13 saved source provenance differs from exact lock: '+rel)
            if current_root!=original_root or require_live_origin:
                observed=h.raw_file(path,expected=row)[1]
                if {key:observed[key] for key in ('bytes','sha256')}!=expected:
                    raise RuntimeError('current R13 source snapshot member differs from exact lock: '+rel)
    return {'source_snapshot_member_count':len(source['snapshot_members']),
            'source_snapshot_fingerprint_sha256':fingerprint,
            'origin_snapshot_member_count':len(origin_record['snapshot_members']),
            'origin_snapshot_fingerprint_sha256':fingerprint}
def verify_source_input(source,*,require_live_origin=False,profile_spec=None):
    if source.get('source_profile')==CURRENT175_R9_SOURCE_PROFILE:
        selected=profile_spec.get('selected_lock_record') if isinstance(profile_spec,dict) else None
        if profile_spec is not None:
            from tools.w26_sealed_data_current import verify_current_profile_spec_source
            verify_current_profile_spec_source(source,profile_spec)
        return verify_source_input_r9(source,require_live_origin=require_live_origin,selected_lock_record=selected)
    if source.get('source_profile')==CURRENT175_R10_SOURCE_PROFILE:
        if not isinstance(profile_spec,dict):
            raise RuntimeError('current R10 source verification requires the exact versioned profile spec')
        from tools.w26_sealed_data_current import verify_current_profile_spec_source
        verify_current_profile_spec_source(source,profile_spec)
        return verify_source_input_r10(source,require_live_origin=require_live_origin,
                                       selected_lock_record=profile_spec['selected_lock_record'])
    if source.get('source_profile')==CURRENT175_R11_SOURCE_PROFILE:
        if not isinstance(profile_spec,dict):
            raise RuntimeError('current R11 source verification requires the exact versioned profile spec')
        from tools.w26_sealed_data_current import verify_current_profile_spec_source
        verify_current_profile_spec_source(source,profile_spec)
        return verify_source_input_r11(source,require_live_origin=require_live_origin,
                                       selected_lock_record=profile_spec['selected_lock_record'])
    if source.get('source_profile')==CURRENT175_R12_SOURCE_PROFILE:
        if not isinstance(profile_spec,dict):
            raise RuntimeError('current R12 source verification requires the exact versioned profile spec')
        from tools.w26_sealed_data_current import verify_current_profile_spec_source
        verify_current_profile_spec_source(source,profile_spec)
        return verify_source_input_r12(source,require_live_origin=require_live_origin,
                                       selected_lock_record=profile_spec['selected_lock_record'])
    if source.get('source_profile')==CURRENT175_R13_SOURCE_PROFILE:
        if not isinstance(profile_spec,dict):
            raise RuntimeError('current R13 source verification requires the exact versioned profile spec')
        from tools.w26_sealed_data_current import verify_current_profile_spec_source
        verify_current_profile_spec_source(source,profile_spec)
        return verify_source_input_r13(source,require_live_origin=require_live_origin,
                                       selected_lock_record=profile_spec['selected_lock_record'])
    h=_helpers();root=Path(source['snapshot_source']);h.secure_dir(root);verify_version(root)
    if set(source_members(root))!=set(source['snapshot_members']):raise RuntimeError('immutable app source membership drift')
    profile=source.get('source_profile','app3-156');source_contract(source_members(root),profile)
    if profile in {CURRENT_SOURCE_PROFILE,*CURRENT175_PROFILES}:h.raw_file(SOURCE_LOCK if profile==CURRENT_SOURCE_PROFILE else current175_source_lock(profile),expected=source['source_lock'])
    for rel,row in source['snapshot_members'].items():
        if row['path']!=str(root/rel):raise RuntimeError('app source record leaves snapshot')
        h.raw_file(root/rel,expected=row)
    original=Path(source['original_source'])
    if set(source_members(original))!=set(source['original_members']):raise RuntimeError('original Main source membership drift')
    for row in source['original_members'].values():h.raw_file(Path(row['path']),expected=row)

def verify_delivery(delivery,source):
    if delivery is None:return None
    expected={'targets':['macos_arm64','macos_x86_64','win_amd64'],'runtime_counts':{'macos_arm64':41,'macos_x86_64':41,'win_amd64':34},'package_counts':{'macos_arm64':42,'macos_x86_64':42,'win_amd64':35}}
    if set(delivery)!={'target_inputs','primary_notices',*expected} or any(delivery.get(k)!=v for k,v in expected.items()):raise RuntimeError('closed delivery target scope differs')
    if source['source_profile']!=LATEST_SOURCE_PROFILE:raise RuntimeError('closed delivery source scope differs')
    h=_helpers()
    from tools.w26_target_inputs import verify_targets
    from tools.w26_primary_notices import notice_payloads
    for key in ['target_inputs','primary_notices']:h.raw_file(Path(delivery[key]['path']),expected=delivery[key])
    original_source=source.get('original_source')
    if not isinstance(original_source,str) or not Path(original_source).is_absolute():raise RuntimeError('closed delivery requires the captured original source root')
    # The Intel historical target record pins the original uv.lock first record.
    # R12 verification above separately validates both original and captured 175-byte source trees.
    targets=verify_targets(delivery['target_inputs'],Path(original_source)/'pyproject.toml');notices=notice_payloads(Path(delivery['primary_notices']['path']))
    for target in ['macos_arm64','macos_x86_64']:
        by={row['name']:row for row in targets['targets'][target]['rows']}
        for name,row in notices.items():
            if by[name]['actual_record']['sha256']!=row['wheel_sha256']:raise RuntimeError('primary notice does not match target wheel input')
    return targets

def verify_prior_profiles(records):
    h=_helpers()
    def walk(value):
        if isinstance(value,dict):
            if {'path','bytes','sha256','dev','inode','uid','mode','nlink'}<=set(value):h.raw_file(Path(value['path']),expected=value,maximum=268435456)
            else:
                for x in value.values():walk(x)
        elif isinstance(value,list):
            for x in value:walk(x)
    for role,record in records.items():
        raw,_=h.raw_file(Path(record['path']),expected=record);v=json.loads(raw)
        if v['role']!=role or v['status']!='PASS_SCOPED_ACTUAL_DEPENDENCY_IMPORTS_NOT_COMSOL_OR_FULL_STDLIB' or v['provider']!={'INFO':[3,5,0,9,0],'NUMBER':0x30500090,'TEXT':'OpenSSL 3.5.9 29 Sep 2026'}:raise RuntimeError('actual prior current dependency profile differs')
        if role=='builder' and v['distributions']!={'pip':'25.0.1','packaging':'26.3','setuptools':'80.9.0','wheel':'0.48.0'}:raise RuntimeError('actual builder complete distribution set differs')
        if role=='runtime' and v['jpype_started_before_after']!=[False,False]:raise RuntimeError('prior dependency JVM state differs')
        walk(v)

def commands(root,python):
    root=Path(root);stage=str(root/'tmp/build-source/source')
    requests=[[python,'-c','import setuptools, wheel; print(setuptools.__version__, wheel.__version__)'],[python,'-m','pip','wheel','--no-deps','--no-build-isolation','--disable-pip-version-check','--wheel-dir',str(root/'appwheel'),stage]]
    actual=[[python,'-I','-B',*requests[0][1:]],[python,'-I','-B','-m','pip','wheel','--no-index','--no-cache-dir',*requests[1][4:]]]
    return [{'label':label,'request_argv':req,'argv':argv,'cap':cap} for label,req,argv,cap in zip(['builder-check','application-build'],requests,actual,[180,600])]

def copy_application_extras(root,extras,*,delivery):
    h=_helpers();root=Path(root)
    if set(extras)!={'uv_lock','operations'}:raise RuntimeError('closed application extra membership differs')
    copies={}
    for name,row in extras.items():
        # Genuine derived provenance binds uv.lock by basename as well as SHA.
        filename='uv.lock' if name=='uv_lock' and delivery is not None else 'INPUT_'+name
        target=root/filename;h.copy_blob(target,h.raw_file(Path(row['path']),expected=row)[0]);copies[name]=h.raw_file(target)[1]
    return copies

def prepare(root,prior_root,source,*,tag='app1',source_profile='app3-156',target_inputs=None,primary_notices=None):
    h=_helpers();root=Path(root);prior_root=Path(prior_root);source=Path(source).resolve(strict=True)
    if root.parent!=Path('/private/tmp') or root!=root.resolve() or not root.name.startswith('cvw26-') or not re.fullmatch('[a-z0-9-]{1,48}',tag):raise RuntimeError('fresh closed app root/tag refused')
    first=json.loads(h.raw_file(root/'FIRST_RECORD.json')[0]);h.secure_dir(root,first['root_identity'])
    manifest=h.raw_file(root/'INPUTS_MANIFEST.json')[1];runtime_record=h.raw_file(root/'CURRENT_RUNTIME_INPUT.json')[1];runtime=deps.verify_inputs(root,manifest,runtime_record)
    profiles={role:h.raw_file(prior_root/'evidence'/('PROFILE_'+role+'.json'))[1] for role in ['builder','runtime']};verify_prior_profiles(profiles)
    prior=h.raw_file(prior_root/'DEPENDENCY_RESULT.json')[1];priorvalue=json.loads(h.raw_file(Path(prior['path']),expected=prior)[0])
    if priorvalue['status']!='PASS_ACTUAL_CURRENT43_ENVIRONMENTS_PROFILES_OFFLINE_SCOPED' or priorvalue['profile_records']!=profiles:raise RuntimeError('original current43 producer-profile closure differs')
    python=prior_root/'builder/bin/python';builder=h.raw_file(python,maximum=268435456)[1]
    for name in ['source-input','source-origin','tmp','logs','evidence','outside','empty-home','appwheel','bundlewheelhouse','metadata','bundle']:
        if (root/name).exists() or (root/name).is_symlink():raise RuntimeError('fresh app effect path already exists')
    delivery=None
    if (target_inputs is None)!=(primary_notices is None):raise RuntimeError('closed delivery requires both three targets and primary notices')
    if target_inputs is not None:
        if source_profile!=LATEST_SOURCE_PROFILE:raise RuntimeError('three target delivery requires exact latest closed current175 source scope')
        from tools.w26_target_inputs import verify_targets
        from tools.w26_primary_notices import notice_payloads
        target_record=h.raw_file(Path(target_inputs))[1];notice_record=h.raw_file(Path(primary_notices))[1]
        verify_targets(target_record,source/'pyproject.toml');notice_payloads(Path(primary_notices))
        delivery={'target_inputs':target_record,'primary_notices':notice_record,'targets':['macos_arm64','macos_x86_64','win_amd64'],'runtime_counts':{'macos_arm64':41,'macos_x86_64':41,'win_amd64':34},'package_counts':{'macos_arm64':42,'macos_x86_64':42,'win_amd64':35}}
    capture=source_capture(source,root/'source-input',profile=source_profile)
    extras={name:h.raw_file(Path(source)/path)[1] for name,path in {'uv_lock':'uv.lock','operations':'docs/full_project_execution/release/RELEASE_OPERATIONS.md'}.items()}
    copied_extras=copy_application_extras(root,extras,delivery=delivery)
    sources=actor.source_fingerprints(actor.APP_BUNDLE)
    legacy_sources={name:h.raw_file(COMMON.parent/file)[1] for name,file in [('common','common.py'),('consumers','consumers.py'),('context','CONTEXT.json'),('config','MPh.ini')]}
    for row in legacy_sources.values():sources[row['path']]=row
    add_current175_lock_sources(sources,source_profile)
    current_profile_spec=None
    if source_profile in VERSIONED_CURRENT_PROFILES:
        from tools.w26_sealed_data_current import build_current_profile_spec
        current_profile_spec=build_current_profile_spec(source_profile,capture['source_lock'],sources)
    prepared_value={'delivery':delivery,'manifest':manifest,'runtime':runtime_record,'root_identity':first['root_identity'],
        'prior_result':prior,'profiles':profiles,'builder':builder,'source':capture,'extras':extras,
        'copied_extras':copied_extras,'legacy_sources':legacy_sources}
    if current_profile_spec is not None:prepared_value['current_profile_spec']=current_profile_spec
    prepared=h.publish(root/('APP_INPUT_BINDING_'+tag+'.json'),prepared_value)
    environment=deps.dependency_environment(root)
    context=h.publish(root/('APP_CONTEXT_'+tag+'.json'),{'activity_kind':actor.APP_BUNDLE,'unit_root':str(root),'root_identity':first['root_identity'],'outside':str(root/'outside'),'environment':environment,'legacy_sources':json.loads(h.raw_file(Path(prepared['path']),expected=prepared)[0])['legacy_sources'],'commands':commands(root,str(python))})
    plan=h.publish(root/('CURRENT_BUILD_PLAN_'+tag+'.json'),{**actor.activity_scope({'activity_kind':actor.APP_BUNDLE}),'context':context,'commands':commands(root,str(python)),'version':VERSION,'target':TARGET,'delivery':delivery,'administrative_python':{'path':str(Path(sys.executable).resolve()),'record':h.raw_file(Path(sys.executable).resolve(),maximum=268435456)[1],'product_runtime':False},'native_process_umask':'0077'})
    if delivery is not None:
        for name in ['w26_target_inputs.py','w26_primary_notices.py']:
            row=h.raw_file(Path(__file__).with_name(name))[1];sources[row['path']]=row
    source_record=h.publish(root/('CURRENT_ACTOR_SOURCE_'+tag+'.json'),sources)
    system=h.publish(root/('APP_SYSTEM_'+tag+'.json'),{'sandbox':actor.system_record('/usr/bin/sandbox-exec'),'ps':actor.system_record('/bin/ps')})
    return h.publish(root/('ACTOR_CANDIDATE_'+tag+'.json'),{'status':'READY_APPLICATION_BUNDLE_MAIN_REVIEW_NOT_EXECUTION_APPROVED',**actor.activity_scope({'activity_kind':actor.APP_BUNDLE}),'root_identity':first['root_identity'],'prepared':prepared,'toolchain':system,'plan':plan,'source':source_record,'native_Popen':0})

class ApplicationLaunch:
    def __init__(self,root,owner,candidate,binding,plan,prepared,logsfd,port):self.root=Path(root);self.owner=owner;self.candidate=candidate;self.binding=binding;self.plan=plan;self.prepared=prepared;self.logsfd=logsfd;self.port=port;self.index=0;self.completed=[]
    def verify_inputs(self,*,require_live_origin=False):
        p=self.prepared;deps.verify_inputs(self.root,p['manifest'],p['runtime']);verify_prior_profiles(p['profiles']);verify_source_input(p['source'],require_live_origin=require_live_origin,profile_spec=p.get('current_profile_spec'));h=_helpers();h.raw_file(Path(p['builder']['path']),expected=p['builder'],maximum=268435456)
        external_extras=[] if p['source'].get('source_profile')==CURRENT175_R9_SOURCE_PROFILE and not require_live_origin else list(p['extras'].values())
        for row in [p['prior_result'],*external_extras,*p['copied_extras'].values()]:h.raw_file(Path(row['path']),expected=row)
        if set(p['copied_extras'])!={'uv_lock','operations'}:raise RuntimeError('closed application copies differ')
        for name,row in p['copied_extras'].items():
            filename='uv.lock' if name=='uv_lock' and p.get('delivery') is not None else 'INPUT_'+name
            if row['path']!=str(self.root/filename):raise RuntimeError('application copied extra leaves exact basename/path')
            if p['source'].get('source_profile')==CURRENT175_R9_SOURCE_PROFILE and {key:row[key] for key in ('bytes','sha256')}!={key:p['extras'][name][key] for key in ('bytes','sha256')}:
                raise RuntimeError('current R9 frozen extra input differs from its captured provenance')
        verify_delivery(p.get('delivery'),p['source'])
    def before_build_wheel(self,source,out,python,evidence):
        if self.index!=0 or [str(source),str(out),str(python),str(evidence)]!=[self.prepared['source']['snapshot_source'],str(self.root/'appwheel'),self.prepared['builder']['path'],str(self.root/'evidence')]:raise RuntimeError('app exact build API paths differ before effects')
        self.owner.remaining(600);self.verify_inputs();actor.verify_actor(self.candidate)
    @contextlib.contextmanager
    def temporary_source_directory(self):
        self.owner.remaining(600);p=self.root/'tmp/build-source';p.mkdir(mode=0o700,exist_ok=False);yield str(p)
    def capture(self,command,**kwargs):
        if self.index>=2 or kwargs!={'capture_output':True,'text':True} or command!=self.plan['commands'][self.index]['request_argv']:raise RuntimeError('two exact application captures differ')
        stage=self.plan['commands'][self.index];self.owner.remaining(stage['cap']);self.verify_inputs();actor.verify_actor(self.candidate);h=_helpers();ctx=json.loads(h.raw_file(Path(self.plan['context']['path']),expected=self.plan['context'])[0])
        runtime=json.loads(h.raw_file(Path(self.prepared['runtime']['path']),expected=self.prepared['runtime'])[0])
        argv=[runtime['base_python_record']['path'],'-I','-S','-B',str(Path(__file__).with_name('w26_application_stage.py')),'--context',self.plan['context']['path'],'--sha',self.plan['context']['sha256'],'--index',str(self.index),'--port',str(self.port)]
        result=actor.run_owned_stage(self.owner,self.logsfd,self.index,stage['label'],argv,self.root/'outside',ctx['environment'],stage['cap'],self.binding,self.plan,sandbox_path=self.root/'APP_SANDBOX.sb')
        self.completed.append(result);h.publish(self.root/'logs'/('PASS_'+str(self.index)+'.json'),result);self.index+=1
        return SimpleNamespace(returncode=result['rc'],stdout=h.raw_file(self.root/'logs'/result['stdout'])[0].decode(),stderr=h.raw_file(self.root/'logs'/result['stderr'])[0].decode())

def execute_bundle(root,candidate,binding,binding_record,*,began,admission_token=None):
    if admission_token is not actor._OWNER_TOKEN:raise RuntimeError('app cannot mint original owner from JSON')
    h=_helpers();root=Path(root);plan=json.loads(h.raw_file(Path(candidate['plan']['path']),expected=candidate['plan'])[0]);scope=actor.activity_scope(plan)
    if scope['activity_kind']!=actor.APP_BUNDLE or plan['version']!=VERSION or plan['target']!=TARGET:raise RuntimeError('closed application version/target scope differs')
    prepared=json.loads(h.raw_file(Path(candidate['prepared']['path']),expected=candidate['prepared'])[0])
    if plan.get('delivery')!=prepared.get('delivery'):raise RuntimeError('closed delivery plan differs')
    owner=actor.RuntimeOwner(root,plan,token=actor._OWNER_TOKEN,began=began);actor._RETAINED[str(root)]=owner
    actor.verify_actor(candidate);owner.remaining(600)
    ctx=json.loads(h.raw_file(Path(plan['context']['path']),expected=plan['context'])[0])
    if plan['commands']!=commands(root,prepared['builder']['path']) or ctx!={'activity_kind':actor.APP_BUNDLE,'unit_root':str(root),'root_identity':candidate['root_identity'],'outside':str(root/'outside'),'environment':deps.dependency_environment(root),'legacy_sources':prepared['legacy_sources'],'commands':plan['commands']}:
        raise RuntimeError('closed app exact context/argv differs')
    tools=json.loads(h.raw_file(Path(candidate['toolchain']['path']),expected=candidate['toolchain'])[0])
    for row in tools.values():
        if actor.system_record(row['path'])!=row:raise RuntimeError('bound app system input changed')
    h.raw_file(Path(plan['administrative_python']['path']),expected=plan['administrative_python']['record'])
    # Read-only validation before once, directories, listener or original Popen.
    validator=ApplicationLaunch(root,owner,candidate,binding,plan,prepared,None,None);validator.verify_inputs(require_live_origin=True)
    if time.monotonic()-began>=600:raise TimeoutError('app prepare cap before effects exhausted')
    h.publish(root/'UNIT_ONCE_GUARD.json',{'binding_first_record':binding_record,'nonce':binding['nonce'],'activity_kind':actor.APP_BUNDLE,'epoch':began,'budget':1800,'cleanup_reserve':300})
    for name in ['tmp','logs','evidence','outside','empty-home','appwheel','bundlewheelhouse','metadata','bundle']:(root/name).mkdir(mode=0o700,exist_ok=False)
    sandbox='(version 1)\n(allow default)\n(deny network*)\n(deny file-write*)\n'+''.join('(allow file-write* (subpath '+json.dumps(str(root/name))+'))\n' for name in ['tmp','logs','evidence','appwheel'])+'(allow file-write* (literal "/dev/null"))\n'
    h.copy_blob(root/'APP_SANDBOX.sb',sandbox.encode());logsfd=os.open(root/'logs',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);old=actor.original.subprocess;launch=None
    actor.original.subprocess=SimpleNamespace(Popen=owner.popen,run=owner.diagnostic_run,TimeoutExpired=actor.subprocess.TimeoutExpired)
    try:
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(.25);port=listener.getsockname()[1]
            with socket.create_connection(('127.0.0.1',port),timeout=1):positive,_=listener.accept();positive.close()
            h.publish(root/'evidence/OWNED_LISTENER_POSITIVE.json',{'host':'127.0.0.1','port':port,'actual_owned_parent_listener':True,'positive_connected':True})
            launch=ApplicationLaunch(root,owner,candidate,binding,plan,prepared,logsfd,port)
            with release._controlled_launch_scope(launch):
                built=release.build_wheel(Path(prepared['source']['snapshot_source']),root/'appwheel',Path(prepared['builder']['path']),root/'evidence')
                if launch.index!=2 or built['wheel_metadata']['version']!=VERSION:raise RuntimeError('actual application build identity/captures differs')
                wheel=root/'appwheel'/built['wheel'];h.raw_file(wheel);owner.remaining(180)
                delivery=prepared.get('delivery');uv_lock=Path(prepared['copied_extras']['uv_lock']['path'])
                if delivery is not None:
                    from tools.w26_target_inputs import verify_targets
                    targets=verify_targets(delivery['target_inputs'],Path(prepared['source']['original_source'])/'pyproject.toml');bundles={};manifests={}
                    for target in delivery['targets']:
                        owner.remaining(180);item=targets['targets'][target];house=root/'bundlewheelhouse'/target;house.mkdir(mode=0o700,exist_ok=False)
                        for row in item['rows']:h.copy_blob(house/row['filename'],h.raw_file(Path(row['copy']['path']),expected=row['copy'])[0])
                        h.copy_blob(house/wheel.name,h.raw_file(wheel)[0]);lock=root/('requirements-'+target+'.lock')
                        release.compose_install_lock(Path(item['requirements']['path']),wheel,target,lock)
                        options={'primary_notice_manifest':Path(delivery['primary_notices']['path'])} if target!='win_amd64' else {}
                        if target=='macos_x86_64':
                            intel_profile=targets.get('intel_historical_prebuilt_profile')
                            if intel_profile is None:
                                options.update(derived_provenance=Path(targets['intel_derived_provenance']['path']),
                                               openssl_license=Path(targets['openssl_license']['path']))
                            else:
                                options.update(historical_prebuilt_profile=intel_profile,
                                               historical_provenance=Path(targets['intel_derived_provenance']['path']),
                                               historical_openssl_license=Path(targets['openssl_license']['path']))
                        metadata=root/'metadata'/target
                        manifests[target]=release.create_bundle_manifest(house,target,lock,uv_lock,metadata,**options)
                        bundles[target]=release.package_wheelhouse_bundle(house,lock,uv_lock,metadata,root/'INPUT_operations',target,root/'bundle'/('comsol-mcp-0.1.10-'+target+'-offline.zip'),root/'evidence',root/'evidence',**options)
                    manifest=manifests;bundle=bundles
                else:
                    release.compose_install_lock(root/'requirements-runtime.lock',wheel,TARGET,root/'requirements-application.lock')
                    runtime_pins=release.parse_hash_requirements(root/'requirements-runtime.lock',TARGET)
                    copied=[]
                    for p in sorted((root/'wheelhouse').glob('*.whl')):
                        if release.wheel_metadata(p)['normalized_name'] in runtime_pins:
                            raw,record=h.raw_file(p);h.copy_blob(root/'bundlewheelhouse'/p.name,raw);copied.append(record)
                    if len(copied)!=41:raise RuntimeError('runtime exactly41 wheel closure differs')
                    h.copy_blob(root/'bundlewheelhouse'/wheel.name,h.raw_file(wheel)[0])
                    manifest=release.create_bundle_manifest(root/'bundlewheelhouse',TARGET,root/'requirements-application.lock',uv_lock,root/'metadata')
                    bundle=release.package_wheelhouse_bundle(root/'bundlewheelhouse',root/'requirements-application.lock',uv_lock,root/'metadata',root/'INPUT_operations',TARGET,root/'bundle/comsol-mcp-0.1.10-macos-arm64-offline.zip',root/'evidence',root/'evidence')
            try:accepted,_=listener.accept();accepted.close();raise RuntimeError('sandboxed app reached held listener')
            except socket.timeout:pass
        owner.remaining(180);launch.verify_inputs();actor.verify_actor(candidate)
        h.publish(root/'APPLICATION_BUNDLE_RESULT.json',{'status':'PASS_CURRENT_APPLICATION_BUNDLE_SCOPED_NOT_RELEASE','version':VERSION,'target':TARGET,'delivery':prepared.get('delivery'),'commands':launch.completed,'samples':owner.samples,'build':built,'manifest':manifest,'bundle':bundle,'outputs':[h.raw_file(p)[1] for p in sorted(root.rglob('*')) if p.is_file() and (p.parent in [root/'appwheel',root/'bundle'] or p.is_relative_to(root/'metadata'))], 'application_install_transition_full_restore':'NOT_RUN','COMSOL_JVM_GUI':'NOT_RUN','physical_Intel_Windows':'NOT_RUN','independent_final_review':'NOT_RUN'})
        return root/'APPLICATION_BUNDLE_RESULT.json'
    except BaseException as exc:actor.retain_first_failure(owner,logsfd,exc,launch.completed if launch else [],stem='APPLICATION');raise
    finally:actor.original.subprocess=old;os.close(logsfd)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--prior-root',type=Path,required=True);p.add_argument('--source',type=Path,required=True);p.add_argument('--tag',default='app1');p.add_argument('--source-profile',choices=['app3-156',CURRENT_SOURCE_PROFILE,CURRENT175_R4_SOURCE_PROFILE,CURRENT175_R5_SOURCE_PROFILE,CURRENT175_R6_SOURCE_PROFILE,CURRENT175_R7_SOURCE_PROFILE,CURRENT175_R8_SOURCE_PROFILE,CURRENT175_R9_SOURCE_PROFILE,CURRENT175_R10_SOURCE_PROFILE,CURRENT175_R11_SOURCE_PROFILE,CURRENT175_R12_SOURCE_PROFILE,CURRENT175_R13_SOURCE_PROFILE],default='app3-156');p.add_argument('--target-inputs',type=Path);p.add_argument('--primary-notices',type=Path);a=p.parse_args();print(prepare(a.root,a.prior_root,a.source,tag=a.tag,source_profile=a.source_profile,target_inputs=a.target_inputs,primary_notices=a.primary_notices))
