"""Focused production read-only input/membership controls; no native build."""
import hashlib
import io
import os
import tarfile
import pytest
from acquire_exact_archives import Refusal
import liblzma_inputs_r1 as actual

def archive(entries):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as tar:
        for name, kind, content in entries:
            member = tarfile.TarInfo(name); member.mode = 0o700 if kind == 'dir' else 0o600
            if kind == 'dir': member.type = tarfile.DIRTYPE
            elif kind == 'symlink': member.type = tarfile.SYMTYPE; member.linkname = content
            elif kind == 'hardlink': member.type = tarfile.LNKTYPE; member.linkname = content
            else: member.size = len(content)
            tar.addfile(member, io.BytesIO(content) if kind == 'file' else None)
    stream.seek(0); return stream

def test_actual_official_inputs_no_unpack():
    result = actual.verify_inputs_and_scan()
    assert result['inventory']['members'] == 704
    assert result['inventory']['expanded_bytes'] == 11947792
    assert result['inventory']['links'] == 0
    assert result['actual_unpack'] == result['native_commands'] == result['network_requests'] == 0
    assert len(result['inventory']['rows']) == 704

def test_full_membership_and_bytes_positive():
    raw = b'public fixture'
    result = actual.scan_archive(archive([('xz-5.8.4', 'dir', b''), ('xz-5.8.4/a', 'file', raw)]), 2, len(raw))
    assert result['rows'][1]['sha256'] == hashlib.sha256(raw).hexdigest()
    assert result['rows'][1]['mode'] == 0o600
    assert not result['filesystem_extraction']

@pytest.mark.parametrize('name', ['../escape', '/xz-5.8.4/a', 'xz-5.8.4/./a', 'xz-5.8.4//a', 'other/a'])
def test_noncanonical_escape_refused(name):
    with pytest.raises(Refusal): actual.scan_archive(archive([('xz-5.8.4', 'dir', b''), (name, 'file', b'x')]), 2, 1)

@pytest.mark.parametrize('kind', ['symlink', 'hardlink'])
def test_link_refused(kind):
    with pytest.raises(Refusal): actual.scan_archive(archive([('xz-5.8.4', 'dir', b''), ('xz-5.8.4/link', kind, '../escape')]), 2, 0)

def test_duplicate_missing_parent_and_bounds_refused():
    for entries, count, size in [
        ([('xz-5.8.4', 'dir', b''), ('xz-5.8.4/a', 'file', b'x'), ('xz-5.8.4/a', 'file', b'x')], 3, 2),
        ([('xz-5.8.4', 'dir', b''), ('xz-5.8.4/sub/a', 'file', b'x')], 2, 1),
        ([('xz-5.8.4', 'dir', b''), ('xz-5.8.4/a', 'file', b'x')], 2, 0),
        ([('xz-5.8.4', 'dir', b''), ('xz-5.8.4/a', 'file', b'x')], 3, 1),
    ]:
        with pytest.raises(Refusal): actual.scan_archive(archive(entries), count, size)

def test_fixed_inode_hash_and_mode_refused(tmp_path):
    path = tmp_path/'input'; path.write_bytes(b'x'); path.chmod(0o600)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with pytest.raises(Refusal): actual.checked_input(fd, actual.INPUTS['xz-5.8.4.tar.gz'])
        path.chmod(0o622)
        with pytest.raises(Refusal): actual.checked_input(fd, actual.INPUTS['xz-5.8.4.tar.gz'])
    finally: os.close(fd)
