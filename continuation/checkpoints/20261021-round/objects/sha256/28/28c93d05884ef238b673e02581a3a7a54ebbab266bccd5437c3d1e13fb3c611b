"""Actual production durability route; software controls, not Win native PASS."""
import errno
import os
from pathlib import Path
import pytest
from comsol_mcp import _state_backup as backup


@pytest.mark.parametrize('platform,expected',[('nt','r+b'),('posix','rb')])
def test_existing_output_mode_no_truncation_and_real_descriptor_fsync(tmp_path,monkeypatch,platform,expected):
    path=tmp_path/'existing';path.write_bytes(b'original owned output');original=Path.open;modes=[];native=os.fsync;fds=[]
    def opened(self,mode,*a,**k):
        modes.append(mode);return original(self,mode,*a,**k)
    def flush(fd):fds.append(fd);assert os.fstat(fd).st_size==len(b'original owned output');native(fd)
    monkeypatch.setattr(backup.os,'name',platform);monkeypatch.setattr(Path,'open',opened);monkeypatch.setattr(backup.os,'fsync',flush)
    backup._fsync_file(path);assert modes==[expected] and len(fds)==1
    with original(path,'rb') as source:assert source.read()==b'original owned output'


def test_permission_and_commit_failure_keep_typed_durability_failure(tmp_path,monkeypatch):
    path=tmp_path/'existing';path.write_bytes(b'unchanged')
    monkeypatch.setattr(backup.os,'name','nt')
    def denied(*a,**k):raise PermissionError(errno.EACCES,'denied')
    with monkeypatch.context() as patch:
        patch.setattr(Path,'open',denied)
        with pytest.raises(backup.StateBackupError) as error:backup._fsync_file(path)
        assert error.value.code=='DURABILITY_FAILED' and isinstance(error.value.__cause__,PermissionError)
    def failed(fd):raise OSError(errno.EBADF,'commit failed')
    monkeypatch.setattr(backup.os,'fsync',failed)
    with pytest.raises(backup.StateBackupError) as error:backup._fsync_file(path)
    assert error.value.code=='DURABILITY_FAILED' and error.value.__cause__.errno==errno.EBADF


def test_nonexistent_output_never_created(tmp_path,monkeypatch):
    path=tmp_path/'absent';monkeypatch.setattr(backup.os,'name','nt')
    with pytest.raises(backup.StateBackupError) as error:backup._fsync_file(path)
    assert error.value.code=='DURABILITY_FAILED' and not path.exists()
