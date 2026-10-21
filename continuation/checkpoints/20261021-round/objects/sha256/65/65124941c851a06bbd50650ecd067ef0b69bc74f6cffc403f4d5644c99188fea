"""Real SQLite private publication reads preserve every live DB/WAL/SHM byte."""
import hashlib,json,sqlite3
from pathlib import Path
import pytest
from comsol_mcp import _state_backup as backup


def identity(path):
    stat=path.stat()
    return (stat.st_dev,stat.st_ino,stat.st_mode,stat.st_size,hashlib.sha256(path.read_bytes()).hexdigest())


def test_fresh_wal_header_without_sidecars_private_read(tmp_path):
    home=tmp_path/'home';home.mkdir();db=home/backup.DATABASE_NAME
    connection=sqlite3.connect(db)
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('CREATE TABLE revisions(model_key TEXT,metadata TEXT)')
    connection.commit();connection.close()
    assert not Path(str(db)+'-wal').exists() and not Path(str(db)+'-shm').exists()
    before=identity(db)
    assert backup._model_publication_journals(home)==[]
    backup.assert_no_pending_restore(home)
    assert identity(db)==before
    assert sorted(p.name for p in home.iterdir())==[backup.DATABASE_NAME]


@pytest.mark.parametrize('status,fenced',[('COMMITTED',False),('PREPARED',True)])
def test_real_committed_wal_rows_fence_and_live_files_unchanged(tmp_path,status,fenced):
    home=tmp_path/'home';home.mkdir();db=home/backup.DATABASE_NAME
    connection=sqlite3.connect(db)
    try:
        connection.execute('PRAGMA journal_mode=WAL');connection.execute('PRAGMA wal_autocheckpoint=0')
        connection.execute('CREATE TABLE revisions(model_key TEXT,metadata TEXT)');connection.commit()
        connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        key=backup.MODEL_PUBLICATION_KEY_PREFIX+'actual-wal-test'
        encoded=json.dumps({'schema':'comsol-mcp.model-publication-journal/1','status':status})
        connection.execute('INSERT INTO revisions VALUES(?,?)',(key,encoded));connection.commit()
        files=[db,Path(str(db)+'-wal'),Path(str(db)+'-shm')]
        before={p.name:identity(p) for p in files}
        assert backup._model_publication_journals(home)==[(key,encoded)]
        if fenced:
            with pytest.raises(backup.StateBackupError,match='interrupted model publication'):backup.assert_no_pending_restore(home)
        else:backup.assert_no_pending_restore(home)
        assert {p.name:identity(p) for p in files}==before
        assert sorted(p.name for p in home.iterdir())==sorted(before)
    finally:connection.close()


def test_private_readonly_query_only_and_close_cleanup(tmp_path,monkeypatch):
    db=tmp_path/'input.sqlite3';c=sqlite3.connect(db);c.execute('create table x(a)');c.execute('insert into x values(7)');c.commit();c.close()
    monkeypatch.setattr(backup.tempfile,'tempdir',str(tmp_path))
    before=identity(db);connection=backup._private_readonly_connection(db);root=connection._snapshot_root
    assert connection.execute('select * from x').fetchall()==[(7,)]
    with pytest.raises(sqlite3.OperationalError,match='readonly'):connection.execute('insert into x values(8)')
    assert identity(db)==before;connection.close();assert not root.exists();connection.close()


@pytest.mark.parametrize('mutation',['db','sidecar'])
def test_private_copy_rejects_cross_member_change(tmp_path,monkeypatch,mutation):
    db=tmp_path/'input.sqlite3';c=sqlite3.connect(db);c.execute('create table x(a)');c.commit();c.close()
    monkeypatch.setattr(backup.tempfile,'tempdir',str(tmp_path));original=backup._copy_sqlite_snapshot_member
    def copy(source,target,expected):
        original(source,target,expected)
        if source==db:
            if mutation=='db':db.write_bytes(db.read_bytes()+b'changed')
            else:Path(str(db)+'-wal').write_bytes(b'new sidecar')
    monkeypatch.setattr(backup,'_copy_sqlite_snapshot_member',copy)
    with pytest.raises(backup.StateBackupError,match='closure changed'):backup._private_readonly_connection(db)
    assert not list(tmp_path.glob('.sqlite-readonly-copy-*'))


def test_private_copy_rejects_unsafe_permission_and_corruption(tmp_path,monkeypatch):
    db=tmp_path/'input.sqlite3';db.write_bytes(b'not sqlite');monkeypatch.setattr(backup.tempfile,'tempdir',str(tmp_path))
    db.chmod(0o666)
    with pytest.raises(backup.StateBackupError,match='private regular'):backup._private_readonly_connection(db)
    db.chmod(0o600)
    with pytest.raises(sqlite3.DatabaseError):backup._private_readonly_connection(db)
    assert not list(tmp_path.glob('.sqlite-readonly-copy-*'))


def test_non_cantopen_error_does_not_fallback(tmp_path,monkeypatch):
    db=tmp_path/'input.sqlite3';db.write_bytes(b'not sqlite')
    monkeypatch.setattr(backup,'_private_readonly_connection',lambda path:pytest.fail('corruption must never fall back'))
    with pytest.raises(backup.StateBackupError,match='opened read-only'):backup._readonly_connection(db)


def test_cleanup_rejects_substituted_directory_without_deleting_foreign(tmp_path,monkeypatch):
    db=tmp_path/'input.sqlite3';c=sqlite3.connect(db);c.execute('create table x(a)');c.close();monkeypatch.setattr(backup.tempfile,'tempdir',str(tmp_path))
    c=backup._private_readonly_connection(db);root=c._snapshot_root;moved=tmp_path/'retained';root.rename(moved);root.mkdir();foreign=root/'keep';foreign.write_bytes(b'foreign')
    with pytest.raises(backup.StateBackupError,match='directory identity'):c.close()
    assert foreign.read_bytes()==b'foreign' and (moved/backup.DATABASE_NAME).exists()


@pytest.fixture
def owned_standalone(tmp_path):
    from comsol_mcp._operation_store import OperationStore
    source=tmp_path/'source';store=OperationStore(source);store.close()
    target=tmp_path/'target';birth=backup._exclusive_empty_file(target);target.write_bytes(source.read_bytes())
    c=sqlite3.connect(target,isolation_level=None,timeout=0.02);c.execute('pragma wal_checkpoint(TRUNCATE)');c.execute('pragma journal_mode=DELETE')
    shm=Path(str(target)+'-shm')
    if not shm.exists():shm.write_bytes(b'synthetic owned generated SHM; guard control only')
    yield target,shm,c,birth
    c.close()


def test_owned_standalone_shm_removal_preserves_database_and_records_exclusion(owned_standalone):
    target,shm,c,birth=owned_standalone;before=identity(target)
    record=backup._finish_owned_standalone_sqlite(target,c,birth)
    assert record['action']=='REMOVED_OWNED_ORPHAN_SHM' and record['exclusive_SQLite_transaction'] is True
    assert not shm.exists() and identity(target)==before


@pytest.mark.parametrize('mutation',['shm_replace','wal_appearance'])
def test_owned_standalone_refuses_changed_shm_or_new_wal_without_deletion(owned_standalone,monkeypatch,mutation):
    target,shm,c,birth=owned_standalone;wal=Path(str(target)+'-wal')
    def seam(*args):
        if mutation=='shm_replace':shm.rename(Path(str(shm)+'.retained'));shm.write_bytes(b'foreign replacement')
        else:wal.write_bytes(b'new WAL; not owned by helper')
    monkeypatch.setattr(backup,'_before_standalone_shm_unlink',seam)
    with pytest.raises(backup.StateBackupError):backup._finish_owned_standalone_sqlite(target,c,birth)
    assert shm.exists()
    if mutation=='shm_replace':assert shm.read_bytes()==b'foreign replacement'
    else:assert wal.read_bytes()==b'new WAL; not owned by helper'


def test_owned_standalone_busy_writer_refuses_shm_deletion(owned_standalone):
    target,shm,c,birth=owned_standalone;writer=sqlite3.connect(target,isolation_level=None)
    try:
        writer.execute('BEGIN IMMEDIATE');before=shm.read_bytes()
        with pytest.raises(backup.StateBackupError,match='exclusive'):backup._finish_owned_standalone_sqlite(target,c,birth)
        assert shm.read_bytes()==before
    finally:writer.execute('ROLLBACK');writer.close()


def test_readonly_fallback_legitimate_production_hardlink_is_stable_not_writable(tmp_path,monkeypatch):
    db=tmp_path/'db';c=sqlite3.connect(db);c.execute('create table x(a)');c.commit();c.close();alias=tmp_path/'recovery-alias';alias.hardlink_to(db);monkeypatch.setattr(backup.tempfile,'tempdir',str(tmp_path))
    before=identity(db);reader=backup._private_readonly_connection(db)
    try:
        assert reader.execute('select count(*) from x').fetchone()==(0,)
        with pytest.raises(sqlite3.OperationalError):reader.execute('insert into x values(1)')
    finally:reader.close()
    assert identity(db)==before and identity(alias)==before


def test_cleanup_same_name_replacement_retains_foreign_and_original(tmp_path,monkeypatch):
    db=tmp_path/'input';c=sqlite3.connect(db);c.execute('create table x(a)');c.close();monkeypatch.setattr(backup.tempfile,'tempdir',str(tmp_path));reader=backup._private_readonly_connection(db);root=reader._snapshot_root
    def seam(path):
        owned=path/backup.DATABASE_NAME;owned.rename(path/'retained-original');owned.write_bytes(b'foreign same-name regular file')
        # Keep exact allowed membership to test identity, not name count.
        (path/'retained-original').rename(tmp_path/'retained-original')
    monkeypatch.setattr(backup,'_before_sqlite_snapshot_cleanup',seam)
    with pytest.raises(backup.StateBackupError,match='named member changed'):reader.close()
    assert (root/backup.DATABASE_NAME).read_bytes()==b'foreign same-name regular file'
    assert (tmp_path/'retained-original').exists()


def test_original_failure_and_cleanup_unknown_are_both_retained(tmp_path,monkeypatch):
    db=tmp_path/'input';c=sqlite3.connect(db);c.execute('create table x(a)');c.close();monkeypatch.setattr(backup.tempfile,'tempdir',str(tmp_path));original=backup.StateBackupError('DATABASE_SOURCE_CHANGED','original first cause');unknown=backup.StateBackupError('DATABASE_COPY_CLEANUP_UNKNOWN','cleanup unknown')
    monkeypatch.setattr(backup,'_copy_sqlite_snapshot_member',lambda *a:(_ for _ in ()).throw(original));monkeypatch.setattr(backup,'_cleanup_sqlite_snapshot',lambda *a:(_ for _ in ()).throw(unknown))
    with pytest.raises(backup.StateBackupError) as raised:backup._private_readonly_connection(db)
    assert raised.value.original_cause is original and raised.value.cleanup_failure is unknown and raised.value.__cause__ is original
