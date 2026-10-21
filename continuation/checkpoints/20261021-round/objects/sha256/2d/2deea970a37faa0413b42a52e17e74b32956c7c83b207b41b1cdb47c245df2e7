"""Real production API/ACL/registry/journal algorithms with OS-boundary SDK standins.

The standin uses owned local files/FDs and models Windows sharing. These controls
are software evidence, not an actual Windows kernel or remote identity proof.
"""
import ctypes as C
import hashlib
import io
import os
from pathlib import Path
import shutil
import sqlite3
from types import SimpleNamespace
import pytest
from tests.test_windows_host_preflight_owner import SDK,Function,install
from comsol_mcp import _windows_parameter_publish as api_module,_windows_state_restore as win,_state_backup as backup


class TreeSDK(SDK):
    def __init__(self,drive):
        super().__init__();self.drive=drive;self.nodes={};self.error=5;self.deleted=[];self.sharing_reopens=0;self.enum_cursor={};self.null_acl=False
        self.k.CreateFileW=Function(self.root);self.nt.NtCreateFile=Function(self.relative)
        self.k.GetFileInformationByHandleEx=Function(self.info_ex);self.k.GetFileInformationByHandle=Function(self.info)
        self.k.SetFileInformationByHandle=Function(self.set_info);self.k.CloseHandle=Function(self.close)
        self.nt.NtSetInformationFile=Function(self.set_nt_info)
        self.nt.RtlNtStatusToDosError=Function(lambda status:183 if status==-1073741771 else 5)
        self.k.ReadFile=Function(self.read);self.k.WriteFile=Function(self.write);self.k.SetFilePointerEx=Function(self.seek)
        self.k.FlushFileBuffers=Function(lambda h:os.fsync(self.node(h)['fd']) or 1)
        self.a.GetSecurityDescriptorDacl=Function(self.dacl)

    def node(self,h):return self.nodes[self.address(h)]
    def path(self,p):return self.drive.joinpath(*str(p).replace('\\','/')[3:].split('/'))
    def open_node(self,p,access,share,directory,create=False):
        if create:
            if p.exists():raise FileExistsError
            if directory:p.mkdir()
            else:p.write_bytes(b'')
        if not p.exists():raise FileNotFoundError
        info=p.stat(follow_symlinks=False)
        desired=lambda a: ((1 if a&(0x80000001|0x20) else 0)|(2 if a&(0x40000002|4) else 0)|(4 if a&0x10000 else 0))
        for row in self.nodes.values():
            st=os.fstat(row['fd'])
            if (st.st_dev,st.st_ino)==(info.st_dev,info.st_ino):
                if desired(access)&~row['share'] or desired(row['access'])&~share:
                    self.sharing_reopens+=1;raise PermissionError('actual SDK sharing matrix refusal')
                if row.get('pending'):raise PermissionError('delete pending')
        flags=os.O_RDONLY if directory or not access&(0x40000000|2) else os.O_RDWR
        fd=os.open(p,flags|os.O_NOFOLLOW);self.seq+=1
        self.nodes[self.seq]={'path':p,'fd':fd,'access':access,'share':share,'directory':directory,'pending':False}
        return self.seq

    def root(self,path,access,share,security,disposition,flags,template):
        self.calls.append(('root',str(path),access,share));return self.open_node(self.path(path),access,share,bool(flags&0x2000000),disposition==1)

    def relative(self,out,access,attributes,io_status,*args):
        attrs=C.cast(attributes,C.POINTER(api_module._ObjectAttributes)).contents
        parent=self.node(attrs.RootDirectory);name=C.wstring_at(attrs.ObjectName.contents.Buffer)
        assert attrs.Attributes==0x1040
        assert not args[4]&0x20 or access&0x00100000, "synchronous NtCreateFile needs SYNCHRONIZE"
        try:h=self.open_node(parent['path']/name,access,args[2],bool(args[4]&1),args[3]==2)
        except PermissionError:return C.c_int32(0xC0000043).value
        except FileNotFoundError:return C.c_int32(0xC0000034).value
        except FileExistsError:return C.c_int32(0xC0000035).value
        C.cast(out,C.POINTER(C.c_void_p))[0]=h;C.cast(io_status,C.POINTER(api_module._IoStatusBlock)).contents.Information=2 if args[3]==2 else 1
        self.calls.append(('relative',h,name,access,args[2]));return 0

    def info(self,h,out):
        row=self.node(h);st=os.fstat(row['fd']);value=C.cast(out,C.POINTER(api_module._FileInfo)).contents
        value.attributes=16 if row['directory'] else 128;value.volume=st.st_dev&0xffffffff;value.index_low=st.st_ino&0xffffffff;value.index_high=st.st_ino>>32;value.links=st.st_nlink;value.size_low=st.st_size
        return 1

    def info_ex(self,h,kind,buffer,size):
        row=self.node(h);st=os.fstat(row['fd'])
        if kind==18:
            v=C.cast(buffer,C.POINTER(api_module._FullIdInfo)).contents;v.volume=st.st_dev
            for i,b in enumerate(st.st_ino.to_bytes(16,'little')):v.file_id[i]=b
        elif kind==0:
            v=C.cast(buffer,C.POINTER(api_module._BasicInfo)).contents;v.attributes=16 if row['directory'] else 128;v.created=st.st_ino;v.changed=st.st_ctime_ns;v.written=st.st_mtime_ns
        elif kind==1:
            v=C.cast(buffer,C.POINTER(api_module._StandardInfo)).contents;v.size=st.st_size;v.links=st.st_nlink;v.directory=row['directory'];v.delete_pending=row['pending']
        elif kind in (19,20):
            assert row['access']&1, 'exact directory enumeration needs FILE_LIST_DIRECTORY'
            if kind==20:self.enum_cursor[self.address(h)]=0
            names=sorted(row['path'].iterdir())
            if self.enum_cursor.get(self.address(h),0):self.error=18;return 0
            self.enum_cursor[self.address(h)]=1
            if not names:self.error=18;return 0
            raw=bytearray()
            for i,p in enumerate(names):
                stp=p.stat(follow_symlinks=False);name=p.name.encode('utf-16-le');extent=(api_module._DirectoryInfo.name.offset+len(name)+7)//8*8
                v=api_module._DirectoryInfo();v.next_offset=extent if i<len(names)-1 else 0;v.name_length=len(name);v.attributes=0x400 if p.is_symlink() else 16 if p.is_dir() else 128
                for j,b in enumerate(stp.st_ino.to_bytes(16,'little')):v.file_id[j]=b
                record=bytearray(bytes(v)[:api_module._DirectoryInfo.name.offset]+name);record.extend(b'\0'*(extent-len(record)));raw.extend(record)
            assert len(raw)<=size;C.memmove(buffer,bytes(raw),len(raw))
        else:raise AssertionError(kind)
        return 1

    def set_nt_info(self,h,iosb,buffer,size,kind):
        # Explicit SDK boundary adapter for native class10/IO_STATUS_BLOCK,
        # using the same owned-local-FD model, never Windows native evidence.
        assert kind==10
        try:self.set_info(h,3,buffer,size)
        except FileExistsError:return -1073741771
        status=iosb._obj;status.Status=0;status.Information=0
        return 0

    def set_info(self,h,kind,buffer,size):
        row=self.node(h)
        assert row['access']&0x10000
        if kind==3:
            v=C.cast(buffer,C.POINTER(api_module._RenameInfo)).contents;assert not v.ReplaceIfExists
            name=C.string_at(C.addressof(buffer)+api_module._RenameInfo.FileName.offset,v.FileNameLength).decode('utf-16-le');dest=self.node(v.RootDirectory)['path']/name
            if dest.exists():raise FileExistsError
            old=row['path'];os.rename(old,dest)
            for item in self.nodes.values():
                if item['path']==old or item['path'].is_relative_to(old):item['path']=dest/item['path'].relative_to(old)
        elif kind==4:row['pending']=True;self.deleted.append(row['path'])
        else:raise AssertionError(kind)
        return 1

    def close(self,h):
        key=self.address(h)
        if key not in self.nodes:return super().close(h)
        row=self.nodes[key]
        if self.close_result==0:return 0
        if row['pending']:
            # True handle disposition, represented by our owned local file only.
            st=os.fstat(row['fd']);others=[r for k,r in self.nodes.items() if k!=key and (os.fstat(r['fd']).st_dev,os.fstat(r['fd']).st_ino)==(st.st_dev,st.st_ino)]
            if not others:
                if row['directory']:row['path'].rmdir()
                else:row['path'].unlink()
        os.close(row['fd']);self.nodes.pop(key);self.calls.append(('close',key));return 1

    def read(self,h,buffer,size,count,overlapped):
        data=os.read(self.node(h)['fd'],size);C.memmove(buffer,data,len(data));C.cast(count,C.POINTER(C.c_uint32))[0]=len(data);return 1
    def write(self,h,buffer,size,count,overlapped):
        n=os.write(self.node(h)['fd'],C.string_at(buffer,size));C.cast(count,C.POINTER(C.c_uint32))[0]=n;return 1
    def seek(self,h,offset,new,whence):os.lseek(self.node(h)['fd'],getattr(offset,'value',offset),whence);return 1
    def dacl(self,descriptor,present,dacl,defaulted):
        result=super().dacl(descriptor,present,dacl,defaulted)
        if self.null_acl:C.cast(present,C.POINTER(C.c_int32))[0]=0
        return result

@pytest.fixture
def sdk(tmp_path,monkeypatch):
    drive=tmp_path/'drive';drive.mkdir();(drive/'owned').mkdir();sdk=TreeSDK(drive);install(monkeypatch,sdk)
    monkeypatch.setattr(C,'get_last_error',lambda:sdk.error,raising=False)
    def win_error(code=5):
        # CPython's actual Windows mapping uses FileExistsError for183.
        error=FileExistsError(17,'SDK native destination collision') if code==183 else OSError(code,'SDK native rejection')
        error.winerror=code
        return error
    monkeypatch.setattr(C,'WinError',win_error,raising=False)
    yield sdk
    # Test finalizer only closes actual standin local descriptors, no registry
    # recovery/SDK retry. It is not production release authority or PASS proof.
    for row in sdk.nodes.values():os.close(row['fd'])


def root_id(sdk):
    st=(sdk.drive/'owned').stat();return {'device':st.st_dev,'inode':st.st_ino}


def prepare_tree(sdk):
    tree=sdk.drive/'owned/projects';tree.mkdir();(tree/'nested').mkdir();(tree/'nested/file').write_bytes(b'actual owned bytes')
    st=tree.stat();ident={'device':st.st_dev,'inode':st.st_ino};files={'nested/file':(hashlib.sha256(b'actual owned bytes').hexdigest(),18)}
    return ident,files


def remove(sdk,ident,files,journal):
    return win.remove_directory(Path('C:/owned'),'projects',expected_identity=ident,expected_root_identity=root_id(sdk),expected_files=files,journal_home=Path('C:/journal'),journal=journal)


def test_real_production_handle_acl_tree_journal_and_sharing(sdk,monkeypatch):
    ident,files=prepare_tree(sdk);journal={};writes=[]
    monkeypatch.setattr(backup,'_full_journal_write',lambda h,j:writes.append(j['directory_cleanup'][0]['phase']))
    remove(sdk,ident,files,journal)
    assert journal['directory_cleanup'][0]['phase']=='DONE'
    assert writes==['PREPARED','QUARANTINED','REMOVE_INTENT','DONE']
    assert not (sdk.drive/'owned/projects').exists() and not sdk.nodes
    assert sdk.sharing_reopens==0
    assert any(c[0]=='security' for c in sdk.calls if isinstance(c,tuple))

@pytest.mark.parametrize('seam',['_after_restore_quarantine_move','_after_restore_quarantine_child_removed','_after_restore_quarantine_rmdir'])
def test_four_phase_recovery_after_real_owned_partial_effects(sdk,monkeypatch,seam):
    ident,files=prepare_tree(sdk);journal={};monkeypatch.setattr(backup,'_full_journal_write',lambda *a:None)
    def interrupt(*a):raise OSError('durable fault seam')
    monkeypatch.setattr(backup,seam,interrupt)
    with pytest.raises(OSError):remove(sdk,ident,files,journal)
    monkeypatch.setattr(backup,seam,lambda *a:None)
    remove(sdk,ident,files,journal)
    assert journal['directory_cleanup'][0]['phase']=='DONE'
    assert not (sdk.drive/'owned/projects').exists()

@pytest.mark.parametrize('mutation',['bytes','extra','symlink','samehash','acl'])
def test_foreign_changes_rejected_preserved_before_delete(sdk,monkeypatch,mutation):
    ident,files=prepare_tree(sdk);journal={};monkeypatch.setattr(backup,'_full_journal_write',lambda *a:None)
    def change(root,relative,q):
        target=sdk.drive/'owned'/q
        if mutation=='bytes':(target/'nested/file').write_bytes(b'foreign change')
        elif mutation=='extra':(target/'foreign').write_bytes(b'never owned')
        elif mutation=='symlink':(target/'foreign').symlink_to(sdk.drive)
        elif mutation=='samehash':
            p=target/'nested/file';p.rename(target/'nested/original');p.write_bytes(b'actual owned bytes')
        else:sdk.null_acl=True
    monkeypatch.setattr(backup,'_before_restore_quarantine_delete',change)
    with pytest.raises(Exception):remove(sdk,ident,files,journal)
    assert not sdk.deleted and journal['directory_cleanup'][0]['phase']=='QUARANTINED'


def test_new_full_identity_matches_actual_stat_without_truncation(sdk):
    assert win.directory_identity(Path('C:/owned'))==root_id(sdk)
    result=win.create_tree(Path('C:/owned'),'new/deeper',expected=root_id(sdk))
    actual=(sdk.drive/'owned/new/deeper').stat();assert result=={'device':actual.st_dev,'inode':actual.st_ino}


def test_actual_original_acl_query_does_not_reopen_delete_target(sdk):
    with win._chain(Path('C:/owned')) as c:
        p=sdk.drive/'owned/file';p.write_bytes(b'owned');h=c.capture(c.api.cleanup_open(c.leaf,'file'))
        first=len([x for x in sdk.calls if isinstance(x,tuple) and x[0]=='relative'])
        win.security(c.api,h,directory=False)
        assert len([x for x in sdk.calls if isinstance(x,tuple) and x[0]=='relative'])==first
        with pytest.raises(OSError):c.api._open_relative(c.leaf,'file',read_control=True)
        assert sdk.sharing_reopens==1


def test_actual_journal_publication_restart_receipt(sdk):
    ident,files=prepare_tree(sdk);home=sdk.drive/'journal';home.mkdir()
    journal={'schema':backup.FULL_RESTORE_JOURNAL_SCHEMA,'recovery_directory':'state-full-recovery-software'}
    win.remove_directory(Path('C:/owned'),'projects',expected_identity=ident,expected_root_identity=root_id(sdk),
                         expected_files=files,journal_home=home,journal=journal)
    actual=backup._read_full_restore_journal(home)
    assert actual['directory_cleanup'][0]['phase']=='DONE'
    assert actual['directory_cleanup'][0]['windows_proof']['entries']['nested/file']['sha256']==files['nested/file'][0]


def test_default_private_stage_uses_original_acl_and_full_id(sdk):
    tree=sdk.drive/'owned/stage';tree.mkdir();p=tree/'model.mph';p.write_bytes(b'owned model')
    root=tree.stat();file=p.stat()
    api_module.delete_exact_private_stage('C:/owned/stage',{'device':root.st_dev,'inode':root.st_ino},
                                         {'device':file.st_dev,'inode':file.st_ino})
    assert not tree.exists() and not sdk.nodes and sdk.sharing_reopens==0


def test_real_sqlite_copy_closure_and_private_cleanup(sdk):
    from comsol_mcp._operation_store import OperationStore
    path=sdk.drive/'owned/source.sqlite3';store=OperationStore(path);store.close()
    original=path.read_bytes();source=Path('C:/owned/source.sqlite3')
    closure=win.source_member(source);assert closure['sha256']==hashlib.sha256(original).hexdigest()
    ident=win.create_tree(Path('C:/owned'),'copy')
    win.copy_snapshot_member(source,Path('C:/owned/copy')/backup.DATABASE_NAME,closure)
    local=sdk.drive/'owned/copy'/backup.DATABASE_NAME
    db=sqlite3.connect(local);assert db.execute('PRAGMA integrity_check').fetchone()==('ok',);db.close()
    assert local.read_bytes()==original
    win.cleanup_snapshot(Path('C:/owned/copy'),ident)
    assert not local.parent.exists() and path.read_bytes()==original and not sdk.nodes


@pytest.mark.parametrize('mutation',['foreign','samehash','link','sourcechange'])
def test_private_copy_and_source_substitution_refuses(sdk,monkeypatch,mutation):
    p=sdk.drive/'owned/source';p.write_bytes(b'original SQL bytes')
    source=Path('C:/owned/source');proof=win.source_member(source)
    if mutation=='sourcechange':
        p.write_bytes(b'changed SQL bytes')
        with pytest.raises(backup.StateBackupError):win.copy_snapshot_member(source,Path('C:/owned/out'),proof)
        assert not (sdk.drive/'owned/out').exists();return
    ident=win.create_tree(Path('C:/owned'),'copy');q=sdk.drive/'owned/copy'/backup.DATABASE_NAME;q.write_bytes(b'owned copy')
    def change(root):
        if mutation=='foreign':(q.parent/'foreign').write_bytes(b'not owned')
        elif mutation=='samehash':
            q.rename(q.parent/'original');q.write_bytes(b'owned copy')
        else:os.link(q,q.parent/'link')
    monkeypatch.setattr(backup,'_before_sqlite_snapshot_cleanup',change)
    with pytest.raises(Exception):win.cleanup_snapshot(Path('C:/owned/copy'),ident)
    assert q.parent.exists() and not sdk.deleted


@pytest.mark.parametrize('with_shm',[False,True])
def test_actual_sqlite_standalone_birth_and_owned_shm(sdk,with_shm):
    from comsol_mcp._operation_store import OperationStore
    target=Path('C:/owned/output.sqlite3');birth=win.birth_file(target);local=sdk.drive/'owned/output.sqlite3'
    store=OperationStore(local);store.close()
    db=sqlite3.connect(local,isolation_level=None);db.execute('PRAGMA journal_mode=DELETE');before=local.read_bytes()
    if with_shm:Path(str(local)+'-shm').write_bytes(b'generated orphan SHM')
    try:result=win.finish_standalone(target,db,birth)
    finally:db.close()
    assert result['action']==('REMOVED_OWNED_ORPHAN_SHM' if with_shm else 'NO_SHM')
    assert local.read_bytes()==before and not Path(str(local)+'-shm').exists() and not sdk.nodes


@pytest.mark.parametrize('mutation',['birth','wal','dbreplace','shmreplace'])
def test_sqlite_standalone_foreign_preserved(sdk,monkeypatch,mutation):
    from comsol_mcp._operation_store import OperationStore
    target=Path('C:/owned/output.sqlite3');birth=win.birth_file(target);local=sdk.drive/'owned/output.sqlite3'
    store=OperationStore(local);store.close()
    db=sqlite3.connect(local,isolation_level=None);db.execute('PRAGMA journal_mode=DELETE')
    shm=Path(str(local)+'-shm');shm.write_bytes(b'owned orphan')
    if mutation=='birth':birth['identity']['inode']+=1
    if mutation=='wal':Path(str(local)+'-wal').write_bytes(b'foreign WAL')
    def change(*a):
        p=local if mutation=='dbreplace' else shm
        p.rename(p.with_suffix('.foreign'));p.write_bytes(b'foreign replacement')
    if mutation in ('dbreplace','shmreplace'):monkeypatch.setattr(backup,'_before_standalone_shm_unlink',change)
    try:
        with pytest.raises(Exception):win.finish_standalone(target,db,birth)
    finally:db.close()
    assert shm.exists() and not sdk.deleted


def test_real_no_clobber_database_install_and_exact_rollback(sdk):
    source=Path('C:/owned/staged');target=Path('C:/owned/installed');birth=win.birth_file(source)
    p=sdk.drive/'owned/staged';p.write_bytes(b'actual generated database');sha=hashlib.sha256(p.read_bytes()).hexdigest()
    win.install_file(source,target,sha=sha,birth=birth)
    installed=sdk.drive/'owned/installed';assert installed.stat().st_nlink==1 and not p.exists()
    win.remove_file(target,sha=sha,birth=birth)
    assert not installed.exists() and not sdk.nodes and sdk.sharing_reopens==0


@pytest.mark.parametrize('mutation',['samehash','acl','wrongsha','collision'])
def test_database_publish_or_cleanup_foreign_refusal(sdk,mutation):
    source=Path('C:/owned/staged');target=Path('C:/owned/installed');birth=win.birth_file(source)
    p=sdk.drive/'owned/staged';p.write_bytes(b'owned');sha=hashlib.sha256(b'owned').hexdigest()
    if mutation=='collision':
        (sdk.drive/'owned/installed').write_bytes(b'foreign')
        with pytest.raises(FileExistsError):win.install_file(source,target,sha=sha,birth=birth)
        assert p.exists();return
    win.install_file(source,target,sha=sha,birth=birth);q=sdk.drive/'owned/installed'
    if mutation=='samehash':q.rename(q.with_suffix('.old'));q.write_bytes(b'owned')
    elif mutation=='acl':sdk.null_acl=True
    else:sha='0'*64
    with pytest.raises(Exception):win.remove_file(target,sha=sha,birth=birth)
    assert q.exists() and not sdk.deleted


def test_close_unknown_keeps_original_registry_and_first_cause(sdk):
    class BrokenText(OSError):
        def __str__(self):raise RuntimeError('secondary unsafe str')
    primary=BrokenText();chain=None
    with pytest.raises(backup.StateBackupError) as caught:
        with win._chain(Path('C:/owned')) as chain:
            sdk.close_result=0
            raise primary
    secondary=caught.value.__cause__
    assert secondary._original_restore_chain is chain and secondary._restore_first_cause is primary
    assert sdk.nodes
    from comsol_mcp._windows_owned_resources import registry_for
    assert any(e.state=='UNKNOWN' for e in registry_for(chain.api).unresolved())


def test_journal_done_waits_for_original_handle_release(sdk,monkeypatch):
    ident,files=prepare_tree(sdk);journal={};monkeypatch.setattr(backup,'_full_journal_write',lambda *a:None)
    def after_removed(*a):sdk.close_result=0
    monkeypatch.setattr(backup,'_after_restore_quarantine_rmdir',after_removed)
    with pytest.raises(backup.StateBackupError):remove(sdk,ident,files,journal)
    assert journal['directory_cleanup'][0]['phase']=='REMOVE_INTENT' and sdk.nodes


@pytest.mark.parametrize('phase',['PREPARED','QUARANTINED','REMOVE_INTENT'])
def test_actual_json_roundtrip_preserves_recovery_proof(sdk,monkeypatch,phase):
    import json
    ident,files=prepare_tree(sdk);journal={};writes=[]
    def publish(home,j):
        writes.append(j['directory_cleanup'][0]['phase'])
        if writes[-1]==phase:raise OSError('durable caller interruption')
    monkeypatch.setattr(backup,'_full_journal_write',publish)
    with pytest.raises(OSError):remove(sdk,ident,files,journal)
    reloaded=json.loads(json.dumps(journal));monkeypatch.setattr(backup,'_full_journal_write',lambda *a:None)
    remove(sdk,ident,files,reloaded)
    assert reloaded['directory_cleanup'][0]['phase']=='DONE' and not sdk.nodes


def test_busy_original_sqlite_handle_never_widens_share_delete(sdk):
    target=Path('C:/owned/database');birth=win.birth_file(target);p=sdk.drive/'owned/database';p.write_bytes(b'owned')
    with win._chain(Path('C:/owned')) as c:
        original=c.capture(c.api._open_relative(c.leaf,'database',read_data=True,read_control=True))
        with pytest.raises(OSError):win.remove_file(target,sha=hashlib.sha256(b'owned').hexdigest(),birth=birth)
        assert sdk.sharing_reopens==1 and p.exists() and not sdk.deleted
        assert sdk.node(original)['share']==3
    win.remove_file(target,sha=hashlib.sha256(b'owned').hexdigest(),birth=birth)
    assert not p.exists()


def test_full_identity_no_64_or_32bit_truncation(sdk):
    original=sdk.k.GetFileInformationByHandleEx.callback
    def extended(h,kind,buffer,size):
        result=original(h,kind,buffer,size)
        if kind==18:
            v=C.cast(buffer,C.POINTER(api_module._FullIdInfo)).contents;v.volume=(1<<61)+17
            for i,b in enumerate(((1<<124)+99).to_bytes(16,'little')):v.file_id[i]=b
        return result
    sdk.k.GetFileInformationByHandleEx.callback=extended
    api=api_module._API();h=api.open('C:/owned',directory=True,read_control=True)
    try:assert api.full_identity(h,directory=True)==((1<<61)+17,(1<<124)+99)
    finally:api.close(h)


@pytest.mark.parametrize('mutation',['zeroid','reparse','pending','unsupported'])
def test_identity_api_unsupported_and_unsafe_refuse(sdk,mutation):
    original=sdk.k.GetFileInformationByHandleEx.callback
    def mutate(h,kind,buffer,size):
        result=original(h,kind,buffer,size)
        if mutation=='unsupported':sdk.error=87;return 0
        if mutation=='zeroid' and kind==18:
            C.memset(C.addressof(C.cast(buffer,C.POINTER(api_module._FullIdInfo)).contents.file_id),0,16)
        if mutation=='reparse' and kind==0:C.cast(buffer,C.POINTER(api_module._BasicInfo)).contents.attributes|=0x400
        if mutation=='pending' and kind==1:C.cast(buffer,C.POINTER(api_module._StandardInfo)).contents.delete_pending=1
        return result
    sdk.k.GetFileInformationByHandleEx.callback=mutate
    with pytest.raises(Exception):win.directory_identity(Path('C:/owned'))
    assert not sdk.nodes and not sdk.deleted


@pytest.mark.parametrize('mutation',['offset','oddname','reparse','colon','duplicate'])
def test_actual_directory_record_abi_rejects_invalid(mutation):
    def row(name,last=True):
        data=name.encode('utf-16-le');v=api_module._DirectoryInfo();v.name_length=len(data);v.file_id[0]=1
        size=(88+len(data)+7)//8*8;v.next_offset=0 if last else size
        raw=bytearray(bytes(v)[:88]+data);raw.extend(b'\0'*(size-len(raw)));return raw
    data=row('file');v=api_module._DirectoryInfo.from_buffer(data)
    if mutation=='offset':v.next_offset=8
    elif mutation=='oddname':v.name_length=3
    elif mutation=='reparse':v.attributes=0x400
    elif mutation=='colon':data=row('file:stream')
    else:data=row('file',False)+row('FILE')
    with pytest.raises(api_module.PublishError):api_module.parse_directory_records(bytes(data))


def test_original_acl_query_security_allocation_failure_preserves_target(sdk):
    from comsol_mcp._windows_owned_resources import registry_for
    with win._chain(Path('C:/owned')) as c:
        p=sdk.drive/'owned/file';p.write_bytes(b'owned');h=c.capture(c.api.cleanup_open(c.leaf,'file'))
        original=registry_for(c.api).find(c.api,h)
        sdk.fault='descriptor_io'
        with pytest.raises(OSError) as caught:win.security(c.api,h,directory=False)
        assert caught.value is sdk.primary and registry_for(c.api).find(c.api,h) is original and original.state=='ACQUIRED'
        assert p.exists() and not sdk.deleted
        sdk.fault=None


def test_production_nt_dispatch_and_actual_journal_writer(sdk,monkeypatch):
    facade={name:getattr(os,name) for name in dir(os)};facade['name']='nt'
    monkeypatch.setattr(backup,'os',SimpleNamespace(**facade))
    ident,files=prepare_tree(sdk);home=sdk.drive/'journal';home.mkdir()
    journal={'schema':backup.FULL_RESTORE_JOURNAL_SCHEMA,'recovery_directory':'state-full-recovery-software'}
    backup._remove_exact_directory(Path('C:/owned'),'projects',expected_identity=ident,
       expected_root_identity=root_id(sdk),expected_files=files,journal_home=home,journal=journal)
    receipt=backup._read_full_restore_journal(home)
    assert receipt['directory_cleanup'][0]['phase']=='DONE' and not sdk.nodes


def test_owned_standalone_requires_complete_original_birth_even_without_shm(sdk):
    local=sdk.drive/'owned/sql';local.write_bytes(b'foreign DB bytes')
    db=sqlite3.connect(':memory:')
    try:
        with pytest.raises(backup.StateBackupError) as error:win.finish_standalone(Path('C:/owned/sql'),db,None)
        assert error.value.code=='DATABASE_TARGET_CHANGED'
    finally:db.close()
    assert local.read_bytes()==b'foreign DB bytes' and not sdk.deleted


def test_existing_readonly_multilink_source_is_not_own_output_claim(sdk):
    source=sdk.drive/'owned/source';source.write_bytes(b'genuine retained bytes');os.link(source,source.with_suffix('.second'))
    proof=win.source_member(Path('C:/owned/source'))
    assert proof['signature'][-1]==2
    win.copy_snapshot_member(Path('C:/owned/source'),Path('C:/owned/copy'),proof)
    assert (sdk.drive/'owned/copy').stat().st_nlink==1 and source.stat().st_nlink==2


def windows_production_boundary(sdk,monkeypatch):
    """Only translate test-local OS paths at the SDK boundary, not algorithms."""
    original=win._chain
    def mapped(root,**kw):
        path=Path(root)
        if path.is_relative_to(sdk.drive):root=Path('C:/')/path.relative_to(sdk.drive)
        return original(root,**kw)
    monkeypatch.setattr(win,'_chain',mapped)
    facade={name:getattr(os,name) for name in dir(os)};facade['name']='nt'
    monkeypatch.setattr(backup,'os',SimpleNamespace(**facade))


def test_real_full_restore_win_dispatch_whole_ledger_registered_file_rebind(sdk,monkeypatch):
    from tests.test_state_backup import _full_backup_fixture
    archive,manifest=_full_backup_fixture(sdk.drive)
    home=sdk.drive/'restore-home';workspace=sdk.drive/'restore-workspace';home.mkdir();workspace.mkdir()
    windows_production_boundary(sdk,monkeypatch)
    result=backup.restore_full_backup(home,workspace,archive,manifest,confirm=True)
    assert result['success'] and result['file_count']==1 and result['project_count']==1
    assert (home/backup.DATABASE_NAME).stat().st_nlink==1 and not sdk.nodes
    import json
    receipt=json.loads((Path(result['recovery_directory'])/'full-restore-journal.json').read_text())
    assert receipt['phase']=='RESTORED_AND_REBOUND' and receipt['windows_database_birth']['identity']['inode']==(home/backup.DATABASE_NAME).stat().st_ino
    assert receipt['directory_cleanup'][0]['phase']=='DONE'


@pytest.mark.parametrize('seam',['_after_restore_quarantine_move','_after_restore_quarantine_child_removed','_after_restore_quarantine_rmdir'])
def test_real_full_restore_failed_rollback_and_public_recover_win_dispatch(sdk,monkeypatch,seam):
    from tests.test_state_backup import _full_backup_fixture
    archive,manifest=_full_backup_fixture(sdk.drive)
    home=sdk.drive/'restore-home';workspace=sdk.drive/'restore-workspace';home.mkdir();workspace.mkdir()
    windows_production_boundary(sdk,monkeypatch)
    def fail_validation(*a,**kw):raise RuntimeError('actual pre-publication validation seam')
    def interrupt(*a):raise OSError('owned four-phase cleanup interruption')
    monkeypatch.setattr(backup,'_validate_rebound_database',fail_validation)
    monkeypatch.setattr(backup,seam,interrupt)
    with pytest.raises(backup.StateBackupError) as caught:backup.restore_full_backup(home,workspace,archive,manifest,confirm=True)
    assert caught.value.code=='RESTORE_ROLLBACK_INCOMPLETE'
    with pytest.raises(backup.StateBackupError) as fenced:backup.assert_no_pending_restore(home)
    assert fenced.value.code=='RESTORE_RECOVERY_REQUIRED'
    monkeypatch.setattr(backup,seam,lambda *a:None)
    result=backup.recover_full_restore(home,confirm=True)
    assert result['success'] and list(workspace.iterdir())==[] and not (home/backup.DATABASE_NAME).exists()
    assert not (home/backup.ACTIVE_FULL_RESTORE_JOURNAL).exists() and not sdk.nodes


def test_real_installed_singlelink_database_rollback_recovers(sdk,monkeypatch):
    from tests.test_state_backup import _full_backup_fixture
    archive,manifest=_full_backup_fixture(sdk.drive)
    home=sdk.drive/'restore-home';workspace=sdk.drive/'restore-workspace';home.mkdir();workspace.mkdir()
    windows_production_boundary(sdk,monkeypatch)
    observed={}
    # Actual generated stage prefix is captured from the real journal, not guessed.
    def fail_after_database(root,relative,q):
        if (home/backup.DATABASE_NAME).exists():
            observed['links']=(home/backup.DATABASE_NAME).stat().st_nlink
            raise OSError('after database install, before stage terminal cleanup')
    monkeypatch.setattr(backup,'_after_restore_quarantine_move',fail_after_database)
    with pytest.raises(backup.StateBackupError) as caught:backup.restore_full_backup(home,workspace,archive,manifest,confirm=True)
    assert caught.value.code=='RESTORE_ROLLBACK_INCOMPLETE' and observed['links']==1
    monkeypatch.setattr(backup,'_after_restore_quarantine_move',lambda *a:None)
    assert backup.recover_full_restore(home,confirm=True)['success']
    assert not (home/backup.DATABASE_NAME).exists() and list(workspace.iterdir())==[] and not sdk.nodes


def test_real_same_sha_foreign_installed_database_preserved(sdk,monkeypatch):
    from tests.test_state_backup import _full_backup_fixture
    archive,manifest=_full_backup_fixture(sdk.drive)
    home=sdk.drive/'restore-home';workspace=sdk.drive/'restore-workspace';home.mkdir();workspace.mkdir()
    windows_production_boundary(sdk,monkeypatch)
    mutated={}
    def substitute(root,relative,q):
        db=home/backup.DATABASE_NAME
        if db.exists() and not mutated:
            original=db.read_bytes();db.rename(home/'retained-original');db.write_bytes(original);mutated['identity']=db.stat().st_ino
            raise OSError('same SHA foreign database replacement')
    monkeypatch.setattr(backup,'_after_restore_quarantine_move',substitute)
    with pytest.raises(backup.StateBackupError) as caught:backup.restore_full_backup(home,workspace,archive,manifest,confirm=True)
    assert caught.value.code=='RESTORE_ROLLBACK_INCOMPLETE'
    monkeypatch.setattr(backup,'_after_restore_quarantine_move',lambda *a:None)
    with pytest.raises(backup.StateBackupError):backup.recover_full_restore(home,confirm=True)
    assert (home/backup.DATABASE_NAME).stat().st_ino==mutated['identity'] and (home/'retained-original').exists()
    assert (home/backup.ACTIVE_FULL_RESTORE_JOURNAL).exists()


def test_real_validator_close_failure_keeps_original_store_and_fence(sdk,monkeypatch):
    from tests.test_state_backup import _full_backup_fixture
    from comsol_mcp._operation_store import OperationStore
    archive,manifest=_full_backup_fixture(sdk.drive)
    home=sdk.drive/'restore-home';workspace=sdk.drive/'restore-workspace';home.mkdir();workspace.mkdir()
    windows_production_boundary(sdk,monkeypatch)
    validator=backup._validate_rebound_database;original_close=OperationStore.close
    class BrokenText(OSError):
        def __str__(self):raise RuntimeError('do not format this first cause')
    primary=BrokenText()
    def close_fail(self):raise primary
    def invoke(*a,**kw):
        with monkeypatch.context() as m:
            m.setattr(OperationStore,'close',close_fail)
            return validator(*a,**kw)
    monkeypatch.setattr(backup,'_validate_rebound_database',invoke)
    with pytest.raises(backup.StateBackupError) as caught:backup.restore_full_backup(home,workspace,archive,manifest,confirm=True)
    failure=caught.value
    try:
        assert failure.code=='RESTORE_ROLLBACK_INCOMPLETE' and failure.original_cause is primary
        assert isinstance(failure.original_database_owner,OperationStore)
        assert (workspace/'projects').exists() and not (home/backup.DATABASE_NAME).exists()
        assert not sdk.deleted and (home/backup.ACTIVE_FULL_RESTORE_JOURNAL).exists()
        with pytest.raises(backup.StateBackupError):backup.assert_no_pending_restore(home)
    finally:
        # Owned harmless test connection finalizer, not recovery qualification.
        original_close(failure.original_database_owner)


@pytest.mark.parametrize('fault',['finalize','install_pending'])
def test_real_validated_stage_failure_returns_empty_targets(sdk,monkeypatch,fault):
    from tests.test_state_backup import _full_backup_fixture
    archive,manifest=_full_backup_fixture(sdk.drive)
    home=sdk.drive/'restore-home';workspace=sdk.drive/'restore-workspace';home.mkdir();workspace.mkdir()
    windows_production_boundary(sdk,monkeypatch)
    if fault=='finalize':
        original=backup._finalize_database
        def finalize(*a,**kw):
            if kw.get('migrate') is False:raise OSError('second finalize pre-effect failure')
            return original(*a,**kw)
        monkeypatch.setattr(backup,'_finalize_database',finalize)
    else:
        original=backup._full_journal_write;fired=False
        def publish(home,journal):
            nonlocal fired
            original(home,journal)
            if journal['phase']=='DATABASE_INSTALL_PENDING' and not fired:
                fired=True;raise OSError('durable install intent, zero publication')
        monkeypatch.setattr(backup,'_full_journal_write',publish)
    with pytest.raises(backup.StateBackupError) as caught:backup.restore_full_backup(home,workspace,archive,manifest,confirm=True)
    assert caught.value.code=='FULL_RESTORE_FAILED'
    assert list(workspace.iterdir())==[] and not (home/backup.DATABASE_NAME).exists()
    assert not (home/backup.ACTIVE_FULL_RESTORE_JOURNAL).exists() and not sdk.nodes


def test_real_validator_full_data_change_is_not_history_exemption(sdk,monkeypatch):
    from tests.test_state_backup import _full_backup_fixture
    archive,manifest=_full_backup_fixture(sdk.drive)
    home=sdk.drive/'restore-home';workspace=sdk.drive/'restore-workspace';home.mkdir();workspace.mkdir()
    windows_production_boundary(sdk,monkeypatch)
    original=backup._validate_rebound_database
    def mutate(path,*a,**kw):
        original(path,*a,**kw)
        db=sqlite3.connect(path);db.execute('UPDATE projects SET revision=revision+1');db.commit();db.close()
    monkeypatch.setattr(backup,'_validate_rebound_database',mutate)
    with pytest.raises(backup.StateBackupError) as caught:backup.restore_full_backup(home,workspace,archive,manifest,confirm=True)
    assert caught.value.code=='RESTORE_ROLLBACK_INCOMPLETE'
    assert caught.value.__cause__.code=='HISTORY_HASH_MISMATCH'
    assert not (home/backup.DATABASE_NAME).exists() and (home/backup.ACTIVE_FULL_RESTORE_JOURNAL).exists()


def test_owner_close_uses_original_sdk_callback_not_mutable_alias(sdk):
    with win._chain(Path('C:/owned')) as c:
        old=sdk.k.CloseHandle;sdk.k.CloseHandle=Function(lambda h: (_ for _ in ()).throw(AssertionError('replacement must not close original')))
        # Entries already captured their actual original CloseHandle callback.
        for h in reversed(c.handles):c.close(h)
        assert not sdk.nodes
        sdk.k.CloseHandle=old


@pytest.mark.parametrize('relative',['../foreign','a\\foreign','/foreign','a//foreign','a/../foreign'])
def test_restore_relative_inputs_cannot_cross_pinned_parent(sdk,relative):
    with pytest.raises(Exception):win.create_tree(Path('C:/owned'),relative,expected=root_id(sdk))
    assert list((sdk.drive/'owned').iterdir())==[] and not sdk.deleted


def test_old_or_partial_windows_journal_proof_never_grants_cleanup(sdk,monkeypatch):
    import json
    ident,files=prepare_tree(sdk);journal={}
    def stop(*a):raise OSError('after original capture')
    monkeypatch.setattr(backup,'_full_journal_write',stop)
    with pytest.raises(OSError):remove(sdk,ident,files,journal)
    for key in ('root_created','root_security','entries'):
        copy=json.loads(json.dumps(journal));copy['directory_cleanup'][0]['windows_proof'].pop(key)
        with pytest.raises(backup.StateBackupError) as caught:remove(sdk,ident,files,copy)
        assert caught.value.code=='RESTORE_JOURNAL_INVALID'
    assert (sdk.drive/'owned/projects').exists() and not sdk.deleted


def test_partial_native_output_exception_keeps_original_api_and_no_close_retry(sdk):
    original=sdk.nt.NtCreateFile.callback
    primary=OSError('original NtCreateFile output written then SDK exception')
    def partial(out,access,attrs,io,*args):
        result=original(out,access,attrs,io,*args)
        if args[3]==2:raise primary
        return result
    sdk.nt.NtCreateFile.callback=partial
    with pytest.raises(Exception) as caught:win.write_member(io.BytesIO(b'new'),Path('C:/owned'),'partial',expected_root=root_id(sdk),sha=hashlib.sha256(b'new').hexdigest(),size=3)
    failure=caught.value
    assert failure.__cause__ is primary and failure.code=='ACQUISITION_STATE_UNKNOWN'
    from comsol_mcp._windows_owned_resources import registry_for
    api=failure.original_api;entries=registry_for(api).unresolved()
    assert len(entries)==1 and entries[0].state=='UNKNOWN' and entries[0].error is primary
    assert (sdk.drive/'owned/partial').exists() and len(sdk.nodes)==1 and not sdk.deleted
    with pytest.raises(OSError) as no_retry:api.close(entries[0].handle)
    assert no_retry.value is primary and len(sdk.nodes)==1


def test_partial_native_error_status_is_not_metadata_owned_or_replayed(sdk):
    original=sdk.nt.NtCreateFile.callback
    def partial(out,access,attrs,io,*args):
        result=original(out,access,attrs,io,*args)
        return C.c_int32(0xC0000035).value if args[3]==2 else result
    sdk.nt.NtCreateFile.callback=partial
    with pytest.raises(api_module.PublishError) as caught:win.birth_file(Path('C:/owned/partial-status'))
    failure=caught.value
    assert failure.code=='ACQUISITION_STATE_UNKNOWN' and failure.safe_retry is False
    from comsol_mcp._windows_owned_resources import registry_for
    assert len(registry_for(failure.original_api).unresolved())==1
    assert len(sdk.nodes)==1 and not sdk.deleted


@pytest.mark.parametrize('fault',['partial','close','metadata'])
def test_whole_restore_original_native_unknown_fences_rollback_and_gc(sdk,monkeypatch,fault):
    import gc,weakref
    from tests.test_state_backup import _full_backup_fixture
    archive,manifest=_full_backup_fixture(sdk.drive)
    home=sdk.drive/'restore-home';workspace=sdk.drive/'restore-workspace';home.mkdir();workspace.mkdir()
    windows_production_boundary(sdk,monkeypatch)
    original_nt=sdk.nt.NtCreateFile.callback;original_close=sdk.k.CloseHandle.callback
    original_rollback=backup._rollback_full_restore;rollbacks=[];trigger=[]
    def rollback(*a,**kw):rollbacks.append(True);return original_rollback(*a,**kw)
    monkeypatch.setattr(backup,'_rollback_full_restore',rollback)
    def native(out,access,attrs,io_status,*args):
        result=original_nt(out,access,attrs,io_status,*args)
        row=sdk.nodes.get(C.cast(out,C.POINTER(C.c_void_p)).contents.value)
        if fault=='partial' and row and row['path'].name=='operations.sqlite3' and args[3]==2:
            trigger.append(len(sdk.calls));raise OSError('original out handle written, acquisition uncertain')
        return result
    def close(h):
        row=sdk.nodes.get(sdk.address(h))
        if row and row['path'].name=='operations.sqlite3' and (fault=='close' or fault=='metadata' and row['access']==0x00100080):
            trigger.append(len(sdk.calls));return 0
        return original_close(h)
    sdk.nt.NtCreateFile.callback=native;sdk.k.CloseHandle.callback=close
    caught=None
    try:backup.restore_full_backup(home,workspace,archive,manifest,confirm=True)
    except backup.StateBackupError as error:caught=error
    assert trigger and caught is not None
    assert not rollbacks, 'UNKNOWN must fence before any automatic rollback opens'
    owners=win.retained_unknown_owners()
    assert owners and any(row['scope_home']==str(home) for row in owners)
    unknown_api=next(row['api'] for row in owners if row.get('api') is not None and row['scope_home']==str(home))
    reference=weakref.ref(unknown_api);del unknown_api,owners,caught;gc.collect()
    assert reference() is not None and any(row.get('api') is reference() for row in win.retained_unknown_owners())
    before=list(sdk.calls);deleted=list(sdk.deleted)
    with pytest.raises(backup.StateBackupError) as refused:backup.recover_full_restore(home,confirm=True)
    assert refused.value.code=='RESTORE_ORIGINAL_OWNER_UNKNOWN'
    assert sdk.calls==before and sdk.deleted==deleted and not rollbacks


def test_same_scope_case_alias_cannot_reopen_original_unknown_owner(sdk):
    import gc
    home=Path('C:/owned/private-scope');workspace=Path('C:/owned/workspace-scope')
    with win.restore_scope(home,workspace):
        try:
            with win._chain(Path('C:/owned')) as c:
                sdk.close_result=0
        except backup.StateBackupError:pass
    gc.collect();before=list(sdk.calls)
    for alias in ('c:/OWNED/PRIVATE-SCOPE','C:\\owned\\private-scope'):
        with pytest.raises(backup.StateBackupError) as refused:
            with win.restore_scope(alias):pytest.fail('unknown scope reopened')
        assert refused.value.code=='RESTORE_ORIGINAL_OWNER_UNKNOWN'
    with pytest.raises(backup.StateBackupError):
        with win.restore_scope('C:/different-home','c:/OWNED/WORKSPACE-SCOPE'):pytest.fail('unknown workspace reused')
    assert sdk.calls==before


def test_different_scope_not_granted_or_blocked_by_unknown_metadata(sdk):
    marker=object();first=OSError('actual original cause')
    with win.restore_scope('C:/scope-A','C:/workspace-A'):
        record=win.retain_unknown(None,first,owner=marker)
    with win.restore_scope('C:/scope-B','C:/workspace-B'):
        assert getattr(win._SCOPE,'current')==('C:/scope-B','C:/workspace-B')
    assert record['owner'] is marker and record['first_cause'] is first
    # A caller-shaped dictionary is neither release authority nor an owner
    # replacement; no public cleanup/retry API is added by this scope fence.
    assert not hasattr(win,'cleanup_retained_unknown')
