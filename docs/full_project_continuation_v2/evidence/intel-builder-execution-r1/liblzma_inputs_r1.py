"""Read-only exact authenticated XZ input preflight; no extraction or execution."""
import hashlib
import os
import stat
import tarfile
from pathlib import Path, PurePosixPath
from acquire_exact_archives import Refusal

INPUT_ROOT = Path('/private/tmp/comsol-liblzma-source-20261012-r1')
INPUTS = {
    'xz-5.8.4.tar.gz': (2799797, '0014c7886930454fe8bd4228665b51af55eeae560ea135c9c4cd33f55b2591d9', 18597671),
    'xz-5.8.4.tar.gz.sig': (566, '486b23126c9b36e34ab98a9b4894b5ca00a0c6105bf25dd1e5660ae5b2e01cf9', 18597680),
    'lasse_collin_pubkey.txt': (3151, '7b44485541eefe7cecb8bcf30682904bb6c3d70aae21bbfffc5fca742cb6f56f', 18597698),
}
MEMBERS = 704
EXPANDED_BYTES = 11947792

def checked_input(fd, expected):
    size, digest, inode = expected
    st = os.fstat(fd)
    if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_nlink != 1
        or stat.S_IMODE(st.st_mode) != 0o600 or (st.st_dev, st.st_ino) != (16777231, inode)
        or st.st_size != size):
        raise Refusal('fixed liblzma input owner/mode/dev/inode/nlink/size differs')
    os.lseek(fd, 0, os.SEEK_SET)
    h = hashlib.sha256(); total = 0
    while raw := os.read(fd, 1048576):
        total += len(raw)
        if total > size: raise Refusal('fixed liblzma input grew')
        h.update(raw)
    end = os.fstat(fd)
    fields = lambda s: (s.st_dev, s.st_ino, s.st_uid, s.st_mode, s.st_nlink, s.st_size)
    if fields(st) != fields(end) or total != size or h.hexdigest() != digest:
        raise Refusal('fixed liblzma input same-FD bytes/identity differ')
    os.lseek(fd, 0, os.SEEK_SET)
    return dict(bytes=size, sha256=digest, identity=dict(device=st.st_dev, inode=st.st_ino), uid=st.st_uid, mode=0o600)

def scan_archive(stream, expected_members=MEMBERS, expected_bytes=EXPANDED_BYTES):
    """Production full membership preflight before reviewed safe extraction.

    This fixed official source has no links. Rejecting any link, special member,
    implicit parent, noncanonical path or unexpected content preserves that
    exact contract; the later extractor retains its stronger traversal guards.
    """
    rows = []; seen = {}; expanded = 0
    with tarfile.open(fileobj=stream, mode='r:*') as archive:
        for member in archive:
            name = member.name
            path = PurePosixPath(name)
            if (not name or '\x00' in name or path.is_absolute() or '..' in path.parts
                or '.' in name.split('/') or '//' in name
                or str(path) != name.rstrip('/') or path.parts[0] != 'xz-5.8.4'):
                raise Refusal('liblzma noncanonical/outside fixed source member')
            if not (member.isdir() or member.isfile()):
                raise Refusal('fixed liblzma source link/special forbidden')
            if name.endswith('/') and not member.isdir(): raise Refusal('regular member has directory suffix')
            key = str(path)
            if key in seen: raise Refusal('duplicate fixed liblzma member')
            if member.size < 0: raise Refusal('negative member size')
            expanded += member.size
            if expanded > expected_bytes or len(rows) >= expected_members:
                raise Refusal('fixed liblzma member/expanded bound exceeded')
            row = dict(relative_path=key, kind='directory' if member.isdir() else 'regular', mode=0o700 if member.isdir() or member.mode & 0o111 else 0o600)
            if member.isdir():
                if member.size: raise Refusal('directory contains unexpected body')
            else:
                source = archive.extractfile(member)
                if source is None: raise Refusal('liblzma regular body absent')
                h = hashlib.sha256(); count = 0
                with source:
                    while raw := source.read(1048576):
                        count += len(raw)
                        if count > member.size: raise Refusal('liblzma member body exceeds declared size')
                        h.update(raw)
                if count != member.size: raise Refusal('liblzma member body truncated')
                row.update(bytes=count, sha256=h.hexdigest())
            rows.append(row); seen[key] = row['kind']
    if len(rows) != expected_members or expanded != expected_bytes:
        raise Refusal('exact liblzma member/expanded totals differ')
    if seen.get('xz-5.8.4') != 'directory': raise Refusal('fixed liblzma source root missing')
    for key in seen:
        for parent in PurePosixPath(key).parents:
            if str(parent) == '.': continue
            if seen.get(str(parent)) != 'directory': raise Refusal('fixed liblzma traversal parent absent/non-directory')
    return dict(members=len(rows), expanded_bytes=expanded, rows=sorted(rows, key=lambda row:row['relative_path']), links=0, filesystem_extraction=False, archive_program_execution=False)

def verify_inputs_and_scan():
    from security_snapshot._host_scoped_provider import verify_private_path_security
    verify_private_path_security(INPUT_ROOT, directory=True)
    parent = os.open(INPUT_ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    opened = []
    try:
        initial = os.fstat(parent); records = {}; archive_fd = None
        for name, expected in INPUTS.items():
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent); opened.append(fd)
            records[name] = checked_input(fd, expected)
            if name == 'xz-5.8.4.tar.gz': archive_fd = fd
        with os.fdopen(os.dup(archive_fd), 'rb') as stream: inventory = scan_archive(stream)
        for name, fd in zip(INPUTS, opened):
            if checked_input(fd, INPUTS[name]) != records[name]: raise Refusal('liblzma input changed during scan')
        end = INPUT_ROOT.lstat()
        if (end.st_dev,end.st_ino,end.st_uid,end.st_mode) != (initial.st_dev,initial.st_ino,initial.st_uid,initial.st_mode):
            raise Refusal('liblzma fixed input root swapped during scan')
        return dict(inputs=records, inventory=inventory, actual_unpack=0, native_commands=0, network_requests=0)
    finally:
        for fd in opened: os.close(fd)
        os.close(parent)
