"""Windows state recovery using original relative handles, ACLs and full IDs.

No pathname delete, permission repair, resource re-adoption or UNKNOWN replay.
"""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import ntpath
from pathlib import Path
import sys
import threading
from uuid import uuid4
from ._windows_parameter_publish import _API, _component
from ._host_scoped_provider import _verify_windows_owned_handle_security, _windows_volume_has_persistent_acls


# Private strong roots retain actual acquired objects, never wire identities.
_UNKNOWN_OWNERS = []
_OWNER_LOCK = threading.RLock()
_SCOPE = threading.local()


def retained_unknown_owners():
    with _OWNER_LOCK:
        return tuple(_UNKNOWN_OWNERS)


def retain_unknown(api, primary, *, owner=None, output=None, closer=None):
    scope=getattr(_SCOPE,'current',None)
    home,workspace=scope if scope is not None else (None,None)
    with _OWNER_LOCK:
        existing=next((row for row in _UNKNOWN_OWNERS if row['api'] is api and row['owner'] is owner
                       and row['scope_home']==home and row['scope_workspace']==workspace),None)
        if existing is not None:return existing
        record={'api':api,'registry':getattr(api,'_native_owned_registry',None) if api is not None else None,
                'owner':owner,'output':output,'closer':closer,'first_cause':primary,
                'scope_home':home,'scope_workspace':workspace}
        _UNKNOWN_OWNERS.append(record)
        return record


def assert_no_unknown(home, workspace=None):
    home=ntpath.normcase(ntpath.normpath(str(home)));workspace=ntpath.normcase(ntpath.normpath(str(workspace))) if workspace is not None else None
    with _OWNER_LOCK:
        retained=next((row for row in _UNKNOWN_OWNERS
                       if row['scope_home'] is not None and ntpath.normcase(ntpath.normpath(row['scope_home']))==home
                       or workspace is not None and row['scope_workspace'] is not None and ntpath.normcase(ntpath.normpath(row['scope_workspace']))==workspace),None)
    if retained is not None:
        fail('original Windows restore owner remains UNKNOWN; no owner re-open or automatic retry',
             'RESTORE_ORIGINAL_OWNER_UNKNOWN',retained['first_cause'])


@contextmanager
def restore_scope(home,workspace=None):
    home=str(home);workspace=str(workspace) if workspace is not None else None
    assert_no_unknown(home,workspace)
    previous=getattr(_SCOPE,'current',None)
    _SCOPE.current=(home,workspace)
    try:yield
    finally:_SCOPE.current=previous


def fail(message, code='RESTORE_TARGET_CHANGED', cause=None):
    from ._state_backup import StateBackupError
    raise StateBackupError(code, message) from cause


def identity(fp):
    return {'device':fp[0][0], 'inode':fp[0][1]}


def security(api, handle, *, directory, owner=True, ancestor=False):
    try:return _verify_windows_owned_handle_security(api,handle,directory=directory,require_daemon_owner=owner,
                                                       **({"pinned_ancestor_traversal": True} if ancestor else {}))
    except BaseException as failure:
        original=getattr(failure,'_original_preflight_owner',None)
        if original is not None:
            cause=original.first_cause if original.first_cause is not None else failure
            retain_unknown(original.api,cause,owner=original)
        raise


class Chain:
    """Original API/handles are retained on an unconfirmed close."""
    def __init__(self,root,*,api=None,expected=None,writable=False):
        self.api=api or _API();self.root=str(root);self.handles=[];self.links=[]
        self.closed=set();self.directory_proofs=[]
        try:
            raw=self.root.replace('/','\\');drive,tail=ntpath.splitdrive(raw)
            if len(drive)!=2 or drive[1]!=':' or not tail.startswith('\\') or raw.startswith('\\\\') or len(raw)>=240:
                fail('ordinary absolute drive restore root required')
            parts=[p for p in tail.split('\\') if p]
            if len(parts)>128:fail('restore directory depth exceeds bound')
            for part in parts:_component(part)
            self.handles.append(self.api.open(drive+'\\',directory=True,metadata=False,read_control=True,
                                             add_directory=writable and not parts,add_file=writable and not parts,list_directory=not parts))
            root_acl=security(self.api,self.handles[-1],directory=True,owner=not parts,ancestor=bool(parts))
            self.directory_proofs.append((self.handles[-1],self.api.full_identity(self.handles[-1],directory=True),root_acl,not parts,bool(parts)))
            for index,part in enumerate(parts):
                parent=self.handles[-1]
                handle=self.api._open_relative(parent,part,directory=True,read_control=True,
                                               add_directory=writable and index==len(parts)-1,
                                               add_file=writable and index==len(parts)-1,list_directory=index==len(parts)-1)
                self.handles.append(handle)
                fp=self.api.full_fingerprint(handle,directory=True)
                acl=security(self.api,handle,directory=True,owner=index==len(parts)-1,ancestor=index<len(parts)-1)
                self.directory_proofs.append((handle,fp[0],acl,index==len(parts)-1,index<len(parts)-1))
                self.links.append((parent,part,fp[0]))
            self.root_handle=self.handles[-1]
            self.root_id=identity(self.api.full_fingerprint(self.root_handle,directory=True))
            if expected is not None and self.root_id!=dict(expected):fail('restore root differs from journal full identity')
            self.verify()
        except BaseException as exc:
            self.finish(exc);raise

    @property
    def leaf(self):return self.root_handle

    def capture(self,h):self.handles.append(h);return h

    def close(self,h):
        if id(h) in self.closed:return
        self.api.close(h);self.closed.add(id(h))

    def verify(self):
        for h,ident,acl,owner,ancestor in self.directory_proofs:
            if self.api.full_identity(h,directory=True)!=ident or security(self.api,h,directory=True,owner=owner,ancestor=ancestor)!=acl:
                fail('original pinned ancestor identity or ACL changed')
        for parent,name,expected in self.links:
            if self.api.relative_full_identity(parent,name,directory=True)!=expected:
                fail('pinned directory name differs from original handle')
        if identity(self.api.full_fingerprint(self.handles[0],directory=True))['device']!=self.root_id['device']:
            fail('restore chain changed volume')

    def finish(self,primary=None):
        errors=[]
        for handle in reversed(self.handles):
            if id(handle) in self.closed:continue
            try:self.close(handle)
            except BaseException as error:errors.append(error)
        if errors:
            retain_unknown(self.api,primary if primary is not None else errors[0],owner=self)
            error=errors[0];error._original_restore_chain=self;error._restore_first_cause=primary if primary is not None else error
            fail('original Windows handle release remains unknown','RESTORE_ROLLBACK_INCOMPLETE',error)

    def __enter__(self):return self
    def __exit__(self,t,error,tb):self.finish(error)


def _chain(root,**kw):
    # The volume/security query API has an independent owner and is never used
    # for path resource acquisition after its preflight has retired.
    api=kw.pop('api',None) or _API()
    probe=_API()
    from ._windows_host_preflight_owner import finish_original
    primary=None
    try:persistent,_serial=_windows_volume_has_persistent_acls(probe,Path(root))
    except BaseException as exc:primary=exc;raise
    finally:
        try:finish_original(probe,primary)
        except BaseException as failure:
            original=getattr(failure,'_original_preflight_owner',None)
            if original is not None:
                cause=primary if primary is not None else original.first_cause if original.first_cause is not None else failure
                retain_unknown(probe,cause,owner=original)
            raise
    if not persistent:fail('restore volume lacks persistent ACLs')
    # Chain constructor does not make any native preflight allocations on api.
    return Chain(root,api=api,**kw)


def directory_identity(root,relative=None,*,expected=None):
    with _chain(root,expected=expected) as c:
        current=c.leaf
        from ._state_backup import _safe_relative_parts
        for part in _safe_relative_parts(relative) if relative is not None else []:
            _component(part);h=c.capture(c.api._open_relative(current,part,directory=True,read_control=True))
            fp=c.api.full_fingerprint(h,directory=True)
            if c.api.relative_full_identity(current,part,directory=True)!=fp[0]:fail('directory leaf changed')
            security(c.api,h,directory=True);current=h
        c.verify();return identity(c.api.full_fingerprint(current,directory=True))


def create_tree(root,relative,*,expected=None,existing=0):
    with _chain(root,expected=expected,writable=True) as c:
        from ._state_backup import _safe_relative_parts
        parts=_safe_relative_parts(relative)
        if type(existing) is not int or not 0<=existing<=len(parts):fail('invalid existing directory prefix')
        current=c.leaf
        for index,part in enumerate(parts):
            _component(part)
            h=c.capture(c.api._open_relative(current,part,directory=True,read_control=True,add_directory=True,add_file=True)
                if index<existing else c.api.create_restore_directory(current,part))
            fp=c.api.full_fingerprint(h,directory=True);security(c.api,h,directory=True)
            if c.api.relative_full_identity(current,part,directory=True)!=fp[0]:fail('created directory leaf changed')
            current=h
        c.verify();return identity(c.api.full_fingerprint(current,directory=True))


def _parent(c,relative):
    from ._state_backup import _safe_relative_parts
    parts=_safe_relative_parts(relative);current=c.leaf
    for part in parts:
        _component(part)
    for part in parts[:-1]:
        h=c.capture(c.api._open_relative(current,part,directory=True,read_control=True,add_file=True,add_directory=True))
        acl=security(c.api,h,directory=True)
        c.directory_proofs.append((h,c.api.full_identity(h,directory=True),acl,True,False))
        c.links.append((current,part,c.api.full_identity(h,directory=True)));current=h
    return current,parts[-1]


def rename_directory(source_root,source_relative,dest_root,dest_relative,*,source_id,source_root_id,dest_root_id):
    with _chain(source_root,expected=source_root_id,writable=True) as c, _chain(dest_root,api=c.api,expected=dest_root_id,writable=True) as d:
        parent,leaf=_parent(c,source_relative);dest,name=_parent(d,dest_relative)
        h=c.capture(c.api.cleanup_open(parent,leaf,directory=True))
        if identity(c.api.full_fingerprint(h,directory=True))!=dict(source_id):fail('directory source full identity changed')
        security(c.api,h,directory=True);c.verify();d.verify()
        c.api.rename(h,dest,name)
        if c.api.relative_full_identity(dest,name,directory=True)!=c.api.full_identity(h,directory=True):fail('renamed directory identity changed')
        c.verify();d.verify()


def file_proof(api,h,*,single_link=True):
    fp=api.full_fingerprint(h)
    if single_link and fp[-1]!=1:fail('restore file has multiple links')
    acl=security(api,h,directory=False)
    sha,size=api.readback(h)
    if fp!=api.full_fingerprint(h) or acl!=security(api,h,directory=False) or size!=fp[1]:fail('restore file changed during pinned hash')
    return {'fingerprint':fp,'security':acl,'sha256':sha,'size':size}


def inventory(api,h,*,prefix='',proof=None,depth=0):
    from . import _state_backup as b
    if depth>128:fail('restore depth cap exceeded')
    proof={} if proof is None else proof;result={}
    for row in sorted(api.enumerate_directory(h),key=lambda r:r['name']):
        name=row['name'];relative=prefix+'/'+name if prefix else name
        child=api._open_relative(h,name,directory=row['directory'],read_data=not row['directory'],read_control=True,list_directory=row['directory'])
        try:
            fp=api.full_fingerprint(child,directory=row['directory'])
            if fp[0][1]!=row['file_id']:fail('enumerated child identity changed')
            if row['directory']:
                acl=security(api,child,directory=True);proof[relative]={'directory':True,'identity':fp[0],'security':acl}
                child_result,_=inventory(api,child,prefix=relative,proof=proof,depth=depth+1)
                result.update(child_result)
            else:
                p=file_proof(api,child)
                if p['size']>b.MAX_FULL_BACKUP_FILE_BYTES:fail('restore file size cap exceeded')
                proof[relative]={'directory':False,**p};result[relative]=(p['sha256'],p['size'])
            if len(proof)>40000 or len(result)>b.MAX_FULL_BACKUP_FILES or sum(n for _,n in result.values())>b.MAX_FULL_BACKUP_TOTAL_BYTES:
                fail('restore inventory exceeds bound')
        finally:
            primary=sys.exc_info()[1]
            try:api.close(child)
            except BaseException as secondary:
                secondary._restore_first_cause=primary if primary is not None else secondary
                secondary._original_restore_api=api
                retain_unknown(api,primary if primary is not None else secondary,output=child)
                fail('inventory original child release unknown','RESTORE_ROLLBACK_INCOMPLETE',secondary)
    return result,proof


def _json(value):
    import json
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def _exists(api,parent,name,*,directory):
    try:return api.relative_full_identity(parent,name,directory=directory)
    except FileNotFoundError:return None


def _check_manifest(files,expected,subset):
    if expected is None:return
    if subset:
        if any(name not in expected or expected[name]!=value for name,value in files.items()):fail('cleanup has unlisted or changed file')
    elif files!=dict(expected):fail('cleanup differs from exact manifest')


def _remove_contents(c,h,expected,proof,*,prefix='',depth=0):
    from . import _state_backup as b
    if depth>128:fail('cleanup directory depth exceeds bound')
    for row in sorted(c.api.enumerate_directory(h),key=lambda r:r['name']):
        name=row['name'];rel=prefix+'/'+name if prefix else name
        known=proof.get(rel)
        if not isinstance(known,dict) or known.get('directory')!=row['directory']:fail('unowned cleanup entry')
        child=c.capture(c.api.cleanup_open(h,name,directory=row['directory']))
        fp=c.api.full_fingerprint(child,directory=row['directory'])
        if fp[0][1]!=row['file_id']:fail('cleanup enumerated object changed')
        if row['directory']:
            if _json(fp[0])!=_json(known['identity']) or security(c.api,child,directory=True)!=known['security']:
                fail('cleanup directory identity/ACL changed')
            _remove_contents(c,child,expected,proof,prefix=rel,depth=depth+1)
            if c.api.enumerate_directory(child):fail('cleanup directory is not empty')
        else:
            now={'directory':False,**file_proof(c.api,child)}
            if _json(now)!=_json(known):fail('cleanup file identity/bytes/ACL changed')
            if expected is not None and expected.get(rel)!=(now['sha256'],now['size']):fail('cleanup file not in manifest')
        if c.api.relative_full_identity(h,name,directory=row['directory'])!=fp[0]:fail('cleanup name changed before delete')
        c.verify();c.api.delete_owned(child);c.close(child)
        if _exists(c.api,h,name,directory=row['directory']) is not None:fail('deleted entry name still present')
        b._after_restore_quarantine_child_removed(h,rel)


def _persist(home,journal):
    from . import _state_backup as b
    if len(_json(journal).encode('utf-8'))>1024*1024:
        fail('owned cleanup proof exceeds durable journal bound','RESTORE_JOURNAL_INVALID')
    b._full_journal_write(home,journal)


def _remove_directory(root,relative,*,expected_identity,expected_root_identity,expected_files=None,
                     allow_subset=False,journal_home,journal,journal_root_relative=''):
    from . import _state_backup as b
    items=journal.setdefault('directory_cleanup',[])
    if not isinstance(items,list):fail('cleanup journal field invalid','RESTORE_JOURNAL_INVALID')
    parts=b._safe_relative_parts(relative)
    item=next((i for i in items if isinstance(i,dict) and i.get('relative')==relative
        and i.get('root_relative')==journal_root_relative and i.get('identity')==dict(expected_identity)),None)
    resuming=item is not None
    if item is not None:
        if (item.get('phase') not in {'PREPARED','QUARANTINED','REMOVE_INTENT','DONE'}
            or item.get('root_identity')!=dict(expected_root_identity) or item.get('leaf')!=parts[-1]
            or item.get('parent_relative')!='/'.join(parts[:-1]) or item.get('allow_subset') is not bool(allow_subset)
            or b._journal_cleanup_files(item.get('expected_files'))!=expected_files
            or not isinstance(item.get('windows_proof'),dict)):
            fail('Windows cleanup journal proof invalid','RESTORE_JOURNAL_INVALID')
        proof=item['windows_proof']
        if set(proof)!={'root_created','root_security','entries'} or not isinstance(proof['entries'],dict):
            fail('cleanup proof fields differ','RESTORE_JOURNAL_INVALID')
        for name,row in proof['entries'].items():
            b._safe_relative_parts(name)
            keys={'directory','identity','security'} if isinstance(row,dict) and row.get('directory') is True else {'directory','fingerprint','security','sha256','size'}
            if not isinstance(row,dict) or type(row.get('directory')) is not bool or set(row)!=keys:
                fail('cleanup child proof fields differ','RESTORE_JOURNAL_INVALID')
        import re
        if not re.fullmatch(r'\.comsol-restore-cleanup-[0-9a-f]{32}',item.get('quarantine_leaf','')):
            fail('cleanup quarantine name invalid','RESTORE_JOURNAL_INVALID')
    with _chain(root,expected=expected_root_identity,writable=True) as c:
        parent,leaf=_parent(c,relative)
        if item and item['phase']=='DONE':
            if _exists(c.api,parent,leaf,directory=True) is not None or _exists(c.api,parent,item['quarantine_leaf'],directory=True) is not None:
                fail('completed cleanup name recreated')
            return
        if item is None:
            handle=c.capture(c.api.cleanup_open(parent,leaf,directory=True))
            fp=c.api.full_fingerprint(handle,directory=True)
            if identity(fp)!=dict(expected_identity):fail('cleanup target identity differs')
            root_acl=security(c.api,handle,directory=True)
            files,proof=inventory(c.api,handle);_check_manifest(files,expected_files,allow_subset)
            item={'root_relative':journal_root_relative,'root_identity':dict(expected_root_identity),'relative':relative,
                'leaf':leaf,'parent_relative':'/'.join(parts[:-1]),'quarantine_leaf':'.comsol-restore-cleanup-'+uuid4().hex,
                'identity':dict(expected_identity),'phase':'PREPARED','expected_files':None if expected_files is None else {p:list(v) for p,v in expected_files.items()},
                'allow_subset':bool(allow_subset),'windows_proof':{'root_created':fp[2],'root_security':root_acl,'entries':proof}}
            items.append(item);_persist(journal_home,journal)
        else:handle=None
        quarantine=item['quarantine_leaf']
        source_id=_exists(c.api,parent,leaf,directory=True);moved_id=_exists(c.api,parent,quarantine,directory=True)
        want=(expected_identity['device'],expected_identity['inode'])
        if item['phase']=='PREPARED':
            if moved_id is None:
                if source_id!=want:fail('prepared original cleanup target missing or changed')
                if handle is None:handle=c.capture(c.api.cleanup_open(parent,leaf,directory=True))
                if c.api.full_identity(handle,directory=True)!=want:fail('prepared original handle differs')
                c.api.rename(handle,parent,quarantine)
                b._after_restore_quarantine_move(root,relative,quarantine)
            elif moved_id!=want or source_id is not None:fail('prepared quarantine identity/name conflicts')
            item['phase']='QUARANTINED';_persist(journal_home,journal)
        source_id=_exists(c.api,parent,leaf,directory=True);moved_id=_exists(c.api,parent,quarantine,directory=True)
        if source_id is not None:fail('foreign leaf appeared at original path')
        if moved_id is None:
            if item['phase']!='REMOVE_INTENT':fail('quarantine missing before remove intent')
            c.verify();return
        if moved_id!=want:fail('quarantine identity changed')
        if handle is None:handle=c.capture(c.api.cleanup_open(parent,quarantine,directory=True))
        fp=c.api.full_fingerprint(handle,directory=True);stored=item['windows_proof']
        if fp[0]!=want or fp[2]!=stored['root_created'] or security(c.api,handle,directory=True)!=stored['root_security']:
            fail('quarantine original creation/security proof changed')
        files,proof=inventory(c.api,handle);_check_manifest(files,expected_files,allow_subset or resuming)
        if any(p not in stored['entries'] or _json(row)!=_json(stored['entries'][p]) for p,row in proof.items()):
            fail('quarantined contents differ from original owned proof')
        b._before_restore_quarantine_delete(root,relative,quarantine)
        files,proof=inventory(c.api,handle);_check_manifest(files,expected_files,allow_subset or resuming)
        if any(p not in stored['entries'] or _json(row)!=_json(stored['entries'][p]) for p,row in proof.items()):fail('cleanup raced after verification')
        _remove_contents(c,handle,expected_files,stored['entries'])
        b._before_restore_quarantine_rmdir(parent,quarantine);c.verify()
        if c.api.enumerate_directory(handle) or c.api.relative_full_identity(parent,quarantine,directory=True)!=want:
            fail('empty quarantine name changed')
        item['phase']='REMOVE_INTENT';_persist(journal_home,journal)
        c.api.delete_owned(handle);c.close(handle)
        b._after_restore_quarantine_rmdir(parent,quarantine)
        if _exists(c.api,parent,quarantine,directory=True) is not None or _exists(c.api,parent,leaf,directory=True) is not None:
            fail('deleted quarantine or original name remains')
        c.verify()
    # The wrapper journals DONE only after original handle releases confirm.


def remove_directory(root,relative,**kwargs):
    from . import _state_backup as b
    _remove_directory(root,relative,**kwargs)
    item=next(i for i in kwargs['journal']['directory_cleanup']
              if i.get('relative')==relative and i.get('root_relative')==kwargs.get('journal_root_relative','')
              and i.get('identity')==dict(kwargs['expected_identity']))
    if item['phase']!='DONE':
        item['phase']='DONE';_persist(kwargs['journal_home'],kwargs['journal'])



def write_member(source,root,relative,*,expected_root,sha,size,capture_birth=False):
    with _chain(root,expected=expected_root,writable=True) as c:
        parent,leaf=_parent(c,relative);h=c.capture(c.api.create_restore_file(parent,leaf))
        born=c.api.full_fingerprint(h);acl=security(c.api,h,directory=False);digest=hashlib.sha256();total=0
        try:
            while block:=source.read(1048576):
                total+=len(block)
                if total>size:fail('archive expanded past declared size','FULL_BACKUP_MEMBER_MISMATCH')
                digest.update(block);c.api.write(h,block)
            c.api.flush(h)
            if total!=size or digest.hexdigest()!=sha:fail('archive member hash differs','FULL_BACKUP_MEMBER_HASH_MISMATCH')
            read_sha,read_size=c.api.readback(h);fp=c.api.full_fingerprint(h)
            if read_sha!=sha or read_size!=size or fp[0]!=born[0] or fp[2]!=born[2] or fp[-1]!=1 or security(c.api,h,directory=False)!=acl:
                fail('staged member same handle proof differs')
            if c.api.relative_full_identity(parent,leaf)!=fp[0]:fail('created member name differs')
            c.verify()
            return {'identity':identity(fp),'created':fp[2],'security':acl} if capture_birth else None
        except BaseException as primary:
            # Only this original exclusive handle can be deleted. Retain the
            # first cause and original owner if any secondary cleanup fails.
            try:
                c.api.delete_owned(h);c.close(h)
                if _exists(c.api,parent,leaf,directory=False) is not None:
                    fail('failed member cleanup remains','RESTORE_STAGE_CLEANUP_UNKNOWN')
            except BaseException as secondary:
                secondary._restore_first_cause=primary
                secondary._original_restore_chain=c
                fail('exclusive member cleanup remains unknown','RESTORE_STAGE_CLEANUP_UNKNOWN',secondary)
            raise


def source_member(path):
    path=Path(path)
    with _chain(path.parent) as c:
        try:h=c.capture(c.api._open_relative(c.leaf,path.name,read_data=True,read_control=True))
        except FileNotFoundError:return None
        proof=file_proof(c.api,h,single_link=False)
        if c.api.relative_full_identity(c.leaf,path.name)!=proof['fingerprint'][0]:fail('SQLite source name changed','DATABASE_SOURCE_CHANGED')
        c.verify()
        return {'signature':proof['fingerprint'],'sha256':proof['sha256'],'security':proof['security']}


def copy_snapshot_member(source,target,expected):
    with _chain(source.parent) as c, _chain(target.parent,api=c.api,writable=True) as d:
        incoming=c.capture(c.api._open_relative(c.leaf,source.name,read_data=True,read_control=True))
        if _json({'signature':c.api.full_fingerprint(incoming),'sha256':c.api.readback(incoming)[0],
                  'security':security(c.api,incoming,directory=False)})!=_json(expected):fail('SQLite source changed before copy','DATABASE_SOURCE_CHANGED')
        output=d.capture(c.api.create_restore_file(d.leaf,target.name));security(c.api,output,directory=False)
        # readback positioned the source at EOF; rewind without reopening it.
        c.api.check(c.api.k.SetFilePointerEx(incoming,0,None,0))
        while block:=c.api.read_chunk(incoming,1048576):d.api.write(output,block)
        d.api.flush(output)
        if d.api.readback(output)[0]!=expected['sha256'] or c.api.full_fingerprint(incoming)!=expected['signature']:
            fail('SQLite source changed while copying','DATABASE_SOURCE_CHANGED')
        c.verify();d.verify()


def private_copy_root():
    import tempfile
    parent=Path(tempfile.gettempdir()).resolve(strict=True);name='.sqlite-readonly-copy-'+uuid4().hex
    ident=create_tree(parent,name)
    return parent/name,ident


def cleanup_snapshot(root,expected_identity):
    from . import _state_backup as b
    allowed={b.DATABASE_NAME+s for s in ('','-wal','-shm','-journal')}
    with _chain(root.parent,writable=True) as c:
        h=c.capture(c.api.cleanup_open(c.leaf,root.name,directory=True))
        fp=c.api.full_fingerprint(h,directory=True)
        if identity(fp)!=dict(expected_identity):fail('private SQLite directory identity changed','DATABASE_COPY_CLEANUP_UNKNOWN')
        security(c.api,h,directory=True);files,proof=inventory(c.api,h)
        if not set(files)<=allowed or any('/' in p or row['directory'] for p,row in proof.items()):fail('private SQLite has unexpected members','DATABASE_COPY_CLEANUP_UNKNOWN')
        b._before_sqlite_snapshot_cleanup(root)
        again,again_proof=inventory(c.api,h)
        if files!=again or _json(proof)!=_json(again_proof):fail('private SQLite copy changed','DATABASE_COPY_CLEANUP_UNKNOWN')
        if c.api.relative_full_identity(c.leaf,root.name,directory=True)!=fp[0]:fail('private SQLite root name replaced','DATABASE_COPY_CLEANUP_UNKNOWN')
        _remove_contents(c,h,files,proof)
        if c.api.enumerate_directory(h):fail('private SQLite copy remains nonempty','DATABASE_COPY_CLEANUP_UNKNOWN')
        c.api.delete_owned(h);c.close(h)
        if _exists(c.api,c.leaf,root.name,directory=True) is not None:fail('private SQLite root remained','DATABASE_COPY_CLEANUP_UNKNOWN')


def birth_file(path):
    with _chain(path.parent,writable=True) as c:
        h=c.capture(c.api.create_restore_file(c.leaf,path.name));fp=c.api.full_fingerprint(h)
        return {'identity':identity(fp),'created':fp[2],'security':security(c.api,h,directory=False)}


def finish_standalone(target,connection,birth):
    from . import _state_backup as b
    if not isinstance(birth,dict) or set(birth)!={'identity','created','security'}:
        fail('standalone target requires actual Windows exclusive birth proof','DATABASE_TARGET_CHANGED')
    with _chain(target.parent) as c:
        db=c.capture(c.api._open_relative(c.leaf,target.name,read_data=True,read_control=True))
        fp=c.api.full_fingerprint(db);acl=security(c.api,db,directory=False)
        if identity(fp)!=birth['identity'] or fp[2]!=birth['created'] or acl!=birth['security'] or fp[-1]!=1:
            fail('standalone target differs from original birth','DATABASE_TARGET_CHANGED')
        names={r['name'] for r in c.api.enumerate_directory(c.leaf)}
        if target.name+'-wal' in names or target.name+'-journal' in names:fail('generated target has WAL/journal','BACKUP_SIDECAR_REMAINS')
        if target.name+'-shm' not in names:return {'action':'NO_SHM','WAL_absent':True}
        shm=c.capture(c.api.cleanup_open(c.leaf,target.name+'-shm'))
        proof=file_proof(c.api,shm);original=file_proof(c.api,db);locked=False
        try:
            try:connection.execute('BEGIN EXCLUSIVE');locked=True
            except __import__('sqlite3').Error as exc:
                fail('standalone target exclusive transaction unavailable','STANDALONE_TARGET_BUSY',exc)
            if str(connection.execute('PRAGMA journal_mode').fetchone()[0]).lower()!='delete':fail('target not DELETE mode','BACKUP_JOURNAL_MODE_FAILED')
            c.api.check(c.api.k.SetFilePointerEx(db,0,None,0))
            if c.api.read_chunk(db,20)[18:20]!=b'\x01\x01':fail('target header not DELETE','BACKUP_JOURNAL_MODE_FAILED')
            b._integrity_check(connection);schema=b._inspect_schema(connection);history=b._history_fingerprint(connection)
            b._before_standalone_shm_unlink(target,Path(str(target)+'-shm'))
            names={r['name'] for r in c.api.enumerate_directory(c.leaf)}
            if target.name+'-wal' in names or target.name+'-journal' in names:fail('WAL/journal appeared','BACKUP_SIDECAR_REMAINS')
            if c.api.relative_full_identity(c.leaf,target.name+'-shm')!=proof['fingerprint'][0] or file_proof(c.api,shm)!=proof or file_proof(c.api,db)!=original:
                fail('standalone target/SHM changed','DATABASE_TARGET_CHANGED')
            c.verify();c.api.delete_owned(shm);c.close(shm)
            if _exists(c.api,c.leaf,target.name+'-shm',directory=False) is not None:fail('owned SHM remains','BACKUP_SIDECAR_REMAINS')
            b._integrity_check(connection)
            if b._inspect_schema(connection)!=schema or b._history_fingerprint(connection)!=history or file_proof(c.api,db)!=original:
                fail('database changed during SHM cleanup','DATABASE_TARGET_CHANGED')
            return {'action':'REMOVED_OWNED_ORPHAN_SHM','exclusive_SQLite_transaction':True,'schema_version':schema,'history_sha256':history}
        finally:
            if locked:
                primary=sys.exc_info()[1]
                try:connection.execute('ROLLBACK')
                except BaseException as secondary:
                    secondary._restore_first_cause=primary if primary is not None else secondary
                    secondary._original_restore_chain=c
                    fail('standalone transaction release unknown','RESTORE_ROLLBACK_INCOMPLETE',secondary)


def tree_inventory(root):
    with _chain(root) as c:
        files,_proof=inventory(c.api,c.leaf);c.verify();return files


def _check_birth(api,h,birth):
    if not isinstance(birth,dict) or set(birth)!={'identity','created','security'}:
        fail('Windows file requires durable exclusive birth proof','RESTORE_JOURNAL_INVALID')
    fp=api.full_fingerprint(h)
    if identity(fp)!=birth['identity'] or fp[2]!=birth['created'] or fp[-1]!=1 or security(api,h,directory=False)!=birth['security']:
        fail('exclusive Windows file birth identity/ACL changed')
    return fp


def remove_file(path,*,sha,birth):
    with _chain(path.parent,writable=True) as c:
        h=c.capture(c.api.cleanup_open(c.leaf,path.name));fp=_check_birth(c.api,h,birth)
        proof=file_proof(c.api,h)
        if proof['sha256']!=sha or c.api.relative_full_identity(c.leaf,path.name)!=fp[0]:
            fail('owned file bytes/name differ before cleanup')
        c.verify();c.api.delete_owned(h);c.close(h)
        if _exists(c.api,c.leaf,path.name,directory=False) is not None:fail('owned file cleanup not confirmed')


def install_file(source,target,*,sha,birth):
    with _chain(source.parent,writable=True) as c, _chain(target.parent,api=c.api,writable=True) as d:
        h=c.capture(c.api.cleanup_open(c.leaf,source.name));fp=_check_birth(c.api,h,birth)
        if file_proof(c.api,h)['sha256']!=sha or c.api.relative_full_identity(c.leaf,source.name)!=fp[0]:
            fail('owned database changed before installation')
        c.verify();d.verify();c.api.rename(h,d.leaf,target.name)
        if c.api.relative_full_identity(d.leaf,target.name)!=fp[0] or _exists(c.api,c.leaf,source.name,directory=False) is not None:
            fail('database installation exact name proof failed')
        _check_birth(c.api,h,birth);c.verify();d.verify()


def delete_private_stage(root,root_id,file_id,*,api=None):
    # This legacy stage contains one fixed leaf; ancestors and original DELETE
    # objects are verified without reopening a DELETE object for READ_CONTROL.
    with _chain(root.parent,api=api,writable=True) as c:
        h=c.capture(c.api.cleanup_open(c.leaf,root.name,directory=True))
        if identity(c.api.full_fingerprint(h,directory=True))!=root_id:fail('private stage root identity changed')
        security(c.api,h,directory=True);files,proof=inventory(c.api,h)
        if set(files)!={'model.mph'} or set(proof)!={'model.mph'} or files['model.mph'][1]<=0:
            fail('private model stage contains unexpected members')
        row=proof['model.mph']
        if identity(row['fingerprint'])!=file_id:fail('private stage file full identity differs')
        _remove_contents(c,h,files,proof)
        if c.api.enumerate_directory(h) or c.api.relative_full_identity(c.leaf,root.name,directory=True)!=c.api.full_identity(h,directory=True):
            fail('private stage changed before root deletion')
        c.verify();c.api.delete_owned(h);c.close(h)
        if _exists(c.api,c.leaf,root.name,directory=True) is not None:fail('private stage deletion not confirmed')
