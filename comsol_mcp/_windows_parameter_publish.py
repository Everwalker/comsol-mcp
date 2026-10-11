"""Win32 handle-pinned parameter publication (stdlib only).

Each directory child and the temporary file are opened relative to a pinned
directory handle with NtCreateFile, OBJ_DONT_REPARSE and FILE_OPEN_REPARSE_POINT.
Directory handles omit FILE_SHARE_DELETE. The owned file is renamed and
deleted by handle. API layout follows Microsoft's OBJECT_ATTRIBUTES,
NtCreateFile, FILE_RENAME_INFO and SetFileInformationByHandle documentation.
"""
from __future__ import annotations

import ctypes as C
import hashlib
import ntpath
import os
import re
import secrets
import sys
from contextlib import contextmanager

DWORD = C.c_uint32
HANDLE = C.c_void_p
BOOL = C.c_int32
NTSTATUS = C.c_int32


class _UnicodeString(C.Structure):
    _fields_ = [("Length", C.c_uint16), ("MaximumLength", C.c_uint16),
                ("Buffer", C.POINTER(C.c_wchar))]


class _ObjectAttributes(C.Structure):
    _fields_ = [("Length", DWORD), ("RootDirectory", HANDLE),
                ("ObjectName", C.POINTER(_UnicodeString)), ("Attributes", DWORD),
                ("SecurityDescriptor", C.c_void_p), ("SecurityQualityOfService", C.c_void_p)]


class _IoStatusUnion(C.Union):
    _fields_ = [("Status", NTSTATUS), ("Pointer", C.c_void_p)]


class _IoStatusBlock(C.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("value", _IoStatusUnion), ("Information", C.c_size_t)]


class PublishError(Exception):
    def __init__(self, code, message, *, details=None, stage="pre_dispatch"):
        super().__init__(message)
        self.code, self.details, self.stage = code, details or {}, stage


class _FileTime(C.Structure):
    _fields_ = [("low", DWORD), ("high", DWORD)]


class _FileInfo(C.Structure):
    _fields_ = [("attributes", DWORD), ("creation", _FileTime), ("access", _FileTime),
                ("write", _FileTime), ("volume", DWORD), ("size_high", DWORD),
                ("size_low", DWORD), ("links", DWORD), ("index_high", DWORD), ("index_low", DWORD)]


class _RenameFlags(C.Union):
    _fields_ = [("ReplaceIfExists", C.c_ubyte), ("Flags", DWORD)]


class _RenameInfo(C.Structure):
    _anonymous_ = ("flags",)
    _fields_ = [("flags", _RenameFlags), ("RootDirectory", HANDLE),
                ("FileNameLength", DWORD), ("FileName", C.c_uint16 * 1)]


class _DispositionInfo(C.Structure):
    _fields_ = [("DeleteFile", C.c_ubyte)]


def _component(value):
    stem = value.split(".", 1)[0].upper()
    reserved = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    reserved.update(f"{p}{n}" for p in ("COM", "LPT") for n in (*'123456789', '¹', '²', '³'))
    if (not value or value in {".", ".."} or value[-1] in " ." or stem in reserved
            or any(ord(c) < 32 or c in '<>:"|?*~' for c in value)):
        raise PublishError("ACCESS_VIOLATION", "export path contains a forbidden Windows name")


def validate_paths(project_root, destination):
    root = os.fspath(project_root)
    if not isinstance(destination, str) or not destination:
        raise PublishError("INVALID_DESTINATION", "destination must be a project-relative file path")
    # Keep the original lexical path; normpath/resolve could erase reparse hops.
    root = root.replace("/", "\\")
    if not re.match(r"^[A-Za-z]:\\", root) or root.startswith(('\\\\', '\\?')):
        raise PublishError("ACCESS_VIOLATION", "Windows project root requires an ordinary absolute drive path")
    if root.endswith('\\') and len(root) > 3:
        root = root[:-1]
    root_parts = root[3:].split('\\') if len(root) > 3 else []
    relative = destination.replace('\\', '/')
    parts = relative.split('/')
    for part in [*root_parts, *parts]:
        _component(part)
    if len(ntpath.join(root, *parts)) >= 240:
        raise PublishError("INVALID_DESTINATION", "export path exceeds the supported ordinary Windows path bound")
    return root, tuple(parts)


class _API:
    def __init__(self):
        if os.name != "nt":
            raise PublishError("NOT_IMPLEMENTED", "Win32 parameter publication requires Windows")
        self.k = C.WinDLL('kernel32', use_last_error=True)
        self.nt = C.WinDLL('ntdll')
        signatures = {
            'CreateFileW': ([C.c_wchar_p, DWORD, DWORD, C.c_void_p, DWORD, DWORD, HANDLE], HANDLE),
            'CloseHandle': ([HANDLE], BOOL),
            'GetFileInformationByHandle': ([HANDLE, C.POINTER(_FileInfo)], BOOL),
            'WriteFile': ([HANDLE, C.c_void_p, DWORD, C.POINTER(DWORD), C.c_void_p], BOOL),
            'ReadFile': ([HANDLE, C.c_void_p, DWORD, C.POINTER(DWORD), C.c_void_p], BOOL),
            'FlushFileBuffers': ([HANDLE], BOOL),
            'SetFilePointerEx': ([HANDLE, C.c_int64, C.POINTER(C.c_int64), DWORD], BOOL),
            'SetFileInformationByHandle': ([HANDLE, C.c_int32, C.c_void_p, DWORD], BOOL),
            'GetFileAttributesW': ([C.c_wchar_p], DWORD),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.k, name); function.argtypes = args; function.restype = result
        self.k.DeviceIoControl.argtypes = [HANDLE, DWORD, C.c_void_p, DWORD, C.c_void_p, DWORD,
                                           C.POINTER(DWORD), C.c_void_p]
        self.k.DeviceIoControl.restype = BOOL
        self.nt.NtCreateFile.argtypes = [C.POINTER(HANDLE), DWORD, C.POINTER(_ObjectAttributes),
                                         C.POINTER(_IoStatusBlock), C.c_void_p, DWORD, DWORD,
                                         DWORD, DWORD, C.c_void_p, DWORD]
        self.nt.NtCreateFile.restype = NTSTATUS
        self.nt.RtlNtStatusToDosError.argtypes = [NTSTATUS]
        self.nt.RtlNtStatusToDosError.restype = DWORD

    def check(self, ok):
        if not ok:
            raise C.WinError(C.get_last_error())

    def open(self, path, *, directory=False, create=False, metadata=False, add_file=False):
        access = 0 if metadata else (0x001000A0 if directory else 0xC0010000)
        if directory and add_file:
            access |= 0x2  # FILE_ADD_FILE on the final pinned parent only
        share = 7 if metadata else (3 if directory else 1)
        flags = 0x00200000 | (0x02000000 if directory else 0x80)
        handle = self.k.CreateFileW(path, access, share, None, 1 if create else 3, flags, None)
        if handle == C.c_void_p(-1).value:
            raise C.WinError(C.get_last_error())
        return handle

    def _open_relative(self, parent_handle, leaf, *, directory=False, create=False, add_file=False,
                       read_data=False):
        """Open exactly one child of a pinned directory object.

        A full path assembled from checked ancestor names can start traversing
        a new junction after a pre-existing FILE_WRITE_ATTRIBUTES handle sets
        a reparse point.  NtCreateFile binds this operation to RootDirectory.
        OBJ_DONT_REPARSE fails closed if name parsing encounters a reparse
        point; FILE_OPEN_REPARSE_POINT also asks for the leaf object itself.
        """
        encoded = leaf.encode('utf-16-le')
        name_buffer = C.create_unicode_buffer(leaf)
        name = _UnicodeString(len(encoded), len(encoded) + 2,
                              C.cast(name_buffer, C.POINTER(C.c_wchar)))
        attributes = _ObjectAttributes(C.sizeof(_ObjectAttributes), parent_handle,
                                       C.pointer(name), 0x40 | 0x1000, None, None)
        output = HANDLE()
        io_status = _IoStatusBlock()
        access = 0x001000A0 if directory else (0x00100081 if read_data else 0x00100080)
        # Directory handles are traversable; file identity probes need only
        # READ_ATTRIBUTES so execution permission is never requested.
        if directory and add_file:
            access |= 0x2  # FILE_ADD_FILE on the final publication parent
        if create:
            access = 0x00110083  # SYNCHRONIZE | DELETE | READ_ATTRIBUTES | WRITE_DATA | READ_DATA
        share = 0x3 if directory else (0x1 if create or read_data else 0x7)
        disposition = 1 if directory else (2 if create else 1)  # FILE_OPEN / FILE_CREATE
        options = (0x1 if directory else 0x40) | 0x20 | 0x00200000
        status = int(self.nt.NtCreateFile(C.byref(output), access, C.byref(attributes),
                                          C.byref(io_status), None, 0x80, share,
                                          disposition, options, None, 0))
        if status < 0:
            unsigned_status = status & 0xFFFFFFFF
            if unsigned_status == 0xC000050B:  # STATUS_REPARSE_POINT_ENCOUNTERED
                raise PublishError("ACCESS_VIOLATION", "export path encountered a reparse point")
            if unsigned_status in {0xC0000034, 0xC000003A}:  # NAME_NOT_FOUND / PATH_NOT_FOUND
                raise FileNotFoundError(2, "relative entry does not exist", leaf)
            if unsigned_status == 0xC0000035:  # STATUS_OBJECT_NAME_COLLISION
                raise FileExistsError(183, "relative entry already exists", leaf)
            dos_error = int(self.nt.RtlNtStatusToDosError(status))
            raise C.WinError(dos_error)
        expected_information = 2 if create else 1  # FILE_CREATED / FILE_OPENED
        if int(io_status.Information) != expected_information:
            self.check(self.k.CloseHandle(output))
            raise PublishError("ACCESS_VIOLATION", "relative file open did not produce the requested object")
        return output

    def open_child_directory(self, parent_handle, leaf, *, add_file=False):
        return self._open_relative(parent_handle, leaf, directory=True, add_file=add_file)

    def create_owned(self, parent_handle, leaf):
        return self._open_relative(parent_handle, leaf, create=True)

    def open_source(self, parent_handle, leaf):
        return self._open_relative(parent_handle, leaf, read_data=True)

    def source_fingerprint(self, handle):
        info = _FileInfo(); self.check(self.k.GetFileInformationByHandle(handle, C.byref(info)))
        if info.attributes & 0x400 or info.attributes & 0x10:
            raise PublishError("ACCESS_VIOLATION", "package source must be an ordinary file without reparse points")
        identity = (int(info.volume), (int(info.index_high) << 32) | int(info.index_low))
        creation = (int(info.creation.high) << 32) | int(info.creation.low)
        written = (int(info.write.high) << 32) | int(info.write.low)
        size = (int(info.size_high) << 32) | int(info.size_low)
        return identity, size, creation, written, int(info.links)

    def read_chunk(self, handle, size):
        buffer = C.create_string_buffer(size); count = DWORD()
        self.check(self.k.ReadFile(handle, buffer, size, C.byref(count), None))
        return buffer.raw[:count.value]

    def verify_named_binding(self, binding):
        for path, expected in binding:
            if self.named_identity(path, directory=True) != expected:
                raise PublishError("ACCESS_VIOLATION", "owned source directory identity changed during streaming")

    def child_identity(self, parent_handle, leaf):
        handle = self._open_relative(parent_handle, leaf)
        try:
            return self.info(handle)[0]
        finally:
            self.close(handle)

    def exists_child(self, parent_handle, leaf):
        try:
            self.child_identity(parent_handle, leaf)
            return True
        except FileNotFoundError:
            return False

    def close(self, handle):
        self.check(self.k.CloseHandle(handle))

    def info(self, handle, *, directory=False):
        info = _FileInfo(); self.check(self.k.GetFileInformationByHandle(handle, C.byref(info)))
        if info.attributes & 0x400 or bool(info.attributes & 0x10) != directory:
            raise PublishError("ACCESS_VIOLATION", "export path must use ordinary files and directories without reparse points")
        return (int(info.volume), (int(info.index_high) << 32) | int(info.index_low)), (int(info.size_high) << 32) | int(info.size_low)

    def named_identity(self, path, *, directory=False):
        handle = self.open(path, directory=directory, metadata=True)
        try:
            return self.info(handle, directory=directory)[0]
        finally:
            self.close(handle)

    def exists(self, path):
        attributes = self.k.GetFileAttributesW(path)
        if attributes != 0xFFFFFFFF:
            return True
        error = C.get_last_error()
        if error in {2, 3}:
            return False
        raise C.WinError(error)

    def write(self, handle, payload):
        offset = 0
        while offset < len(payload):
            block = payload[offset:offset + 1024 * 1024]; count = DWORD()
            self.check(self.k.WriteFile(handle, C.create_string_buffer(block), len(block), C.byref(count), None))
            if count.value == 0:
                raise OSError("zero-byte write")
            offset += count.value

    def readback(self, handle):
        self.check(self.k.SetFilePointerEx(handle, 0, None, 0))
        digest = hashlib.sha256(); size = 0; buffer = C.create_string_buffer(1024 * 1024)
        while True:
            count = DWORD(); self.check(self.k.ReadFile(handle, buffer, len(buffer), C.byref(count), None))
            if not count.value:
                return digest.hexdigest(), size
            digest.update(buffer.raw[:count.value]); size += count.value

    def flush(self, handle):
        self.check(self.k.FlushFileBuffers(handle))

    def rename(self, handle, parent_handle, leaf):
        encoded = leaf.encode('utf-16-le')
        offset = _RenameInfo.FileName.offset
        buffer = C.create_string_buffer(max(C.sizeof(_RenameInfo), offset + len(encoded) + 2))
        info = C.cast(buffer, C.POINTER(_RenameInfo)).contents
        info.ReplaceIfExists = 0; info.RootDirectory = parent_handle; info.FileNameLength = len(encoded)
        C.memmove(C.addressof(buffer) + offset, encoded, len(encoded))
        self.check(self.k.SetFileInformationByHandle(handle, 3, buffer, len(buffer)))

    def delete_owned(self, handle):
        disposition = _DispositionInfo(1)
        self.check(self.k.SetFileInformationByHandle(handle, 4, C.byref(disposition), C.sizeof(disposition)))


@contextmanager
def pinned_parent(project_root, destination, *, add_file=True, _api=None):
    """Hold every lexical directory ancestor without delete sharing."""
    root, parts = validate_paths(project_root, destination)
    api = _api or _API(); handles = []; names = []
    current = root[:3]
    try:
        components = [*(root[3:].split('\\') if len(root) > 3 else []), *parts[:-1]]
        root_handle = api.open(current, directory=True, add_file=add_file and not components)
        handles.append(root_handle)
        identity = api.info(root_handle, directory=True)[0]
        if api.named_identity(current, directory=True) != identity:
            raise PublishError("ACCESS_VIOLATION", "export volume root changed during admission")
        names.append((current, identity))
        for index, part in enumerate(components):
            handle = api.open_child_directory(handles[-1], part,
                                              add_file=add_file and index == len(components) - 1)
            handles.append(handle)
            current = ntpath.join(current, part)
            identity = api.info(handle, directory=True)[0]
            if api.named_identity(current, directory=True) != identity:
                raise PublishError("ACCESS_VIOLATION", "export directory identity changed during admission")
            names.append((current, identity))
        yield api, handles[-1], parts, names
    finally:
        pending = sys.exc_info()[0] is not None
        for handle in reversed(handles):
            try:
                api.close(handle)
            except OSError:
                if not pending:
                    raise


def publish(project_root, destination, payload, *, _api=None):
    if not isinstance(payload, bytes) or not payload:
        raise PublishError("INVALID_REQUEST", "published artifact bytes must be non-empty")
    digest = hashlib.sha256(payload).hexdigest()
    return publish_chunks(project_root, destination, (payload,), expected_sha256=digest,
                          expected_size=len(payload), _api=_api)


def publish_chunks(project_root, destination, chunks, *, expected_sha256, expected_size, _api=None):
    """Publish a stream supplied by an internal, already-pinned source reader.

    The caller owns and verifies the source handle. This function owns only the
    destination temp handle and consumes the iterable once, checking its exact
    expected digest and size before the no-clobber rename.
    """
    if (not isinstance(expected_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
            or isinstance(expected_size, bool) or not isinstance(expected_size, int)
            or expected_size <= 0):
        raise PublishError("INVALID_REQUEST", "stream publication requires a SHA-256 and positive byte size")
    try:
        chunks = iter(chunks)
    except TypeError as error:
        raise PublishError("INVALID_REQUEST", "stream publication requires an iterable of byte chunks") from error
    digest = expected_sha256; size = expected_size; observed = hashlib.sha256(); observed_size = 0
    handle = None; committed = False
    owned_created = False; identity = None
    try:
        with pinned_parent(project_root, destination, _api=_api) as (api, parent, parts, binding):
            temp_leaf = '.parameter-export.' + secrets.token_hex(16) + '.partial'
            if api.exists_child(parent, parts[-1]):
                raise PublishError("DESTINATION_EXISTS", "export destination already exists")
            try:
                handle = api.create_owned(parent, temp_leaf); owned_created = True
                identity = api.info(handle)[0]
                for block in chunks:
                    if not isinstance(block, bytes):
                        raise PublishError("INVALID_REQUEST", "stream source yielded a non-byte chunk")
                    if not block:
                        continue
                    observed_size += len(block)
                    if observed_size > size:
                        raise PublishError("ARTIFACT_SIZE_MISMATCH", "stream source exceeded its expected byte size")
                    observed.update(block)
                    api.write(handle, block)
                observed_digest = observed.hexdigest()
                if observed_size != size:
                    raise PublishError("ARTIFACT_SIZE_MISMATCH", "stream source did not match its expected byte size")
                if observed_digest != digest:
                    raise PublishError("ARTIFACT_HASH_MISMATCH", "stream source did not match its expected SHA-256")
                api.flush(handle)
                for path, expected in binding:
                    if api.named_identity(path, directory=True) != expected:
                        raise PublishError("ACCESS_VIOLATION", "export directory identity changed before publication")
                try:
                    api.rename(handle, parent, parts[-1]); committed = True
                except OSError as error:
                    if getattr(error, 'winerror', None) in {80, 183} or api.exists_child(parent, parts[-1]):
                        raise PublishError("DESTINATION_EXISTS", "export destination appeared before publication") from error
                    raise
                observed_digest, observed_size = api.readback(handle)
                if api.info(handle)[0] != identity or api.child_identity(parent, parts[-1]) != identity:
                    raise PublishError("ARTIFACT_IDENTITY_MISMATCH", "published file identity failed readback")
                if observed_digest != digest or observed_size != size:
                    raise PublishError("ARTIFACT_HASH_MISMATCH", "published bytes failed digest and size readback")
                for path, expected in binding:
                    if api.named_identity(path, directory=True) != expected:
                        raise PublishError("ACCESS_VIOLATION", "export directory identity changed during publication")
                api.flush(handle)
                api.close(handle); handle = None
                return {'relative_path': '/'.join(parts), 'sha256': digest, 'size_bytes': observed_size,
                        'file_identity': {'device': identity[0], 'inode': identity[1]}}
            except Exception as cause:
                # The chunk iterable owns source reads and can fail with any
                # ordinary exception (including contract/runtime errors). Once
                # this publisher has created its destination handle, every such
                # failure must take the same-handle cleanup and typed-receipt
                # path. BaseException interruptions still pass through while the
                # finally block closes the handle.
                # No temp was created when CREATE_NEW failed; the publisher
                # therefore has no owned residual to remove or report unknown.
                cleanup = not owned_created and not committed
                if handle is not None:
                    try:
                        api.delete_owned(handle); api.close(handle); handle = None
                        cleanup = not api.exists_child(parent, parts[-1] if committed else temp_leaf)
                    except Exception:
                        cleanup = False
                supplied_code = getattr(cause, 'code', None)
                code = (cause.code if isinstance(cause, PublishError) else
                        supplied_code if isinstance(supplied_code, str) and re.fullmatch(r'[A-Z][A-Z0-9_]{1,63}', supplied_code)
                        else 'ARTIFACT_PUBLISH_FAILED')
                details = {'partial_change': committed, 'publication_committed': committed,
                           'cleanup_verified': cleanup, 'residual_status': 'ABSENT' if cleanup else 'UNKNOWN',
                           'final_state': 'ROLLED_BACK' if cleanup else 'UNKNOWN', 'cause_code': code,
                           'cause_type': type(cause).__name__,
                           'relative_path': '/'.join(parts), 'sha256': digest, 'size_bytes': size,
                           'source_sha256_observed': observed.hexdigest(),
                           'source_size_observed': observed_size,
                           'file_identity': {'device': identity[0], 'inode': identity[1]} if identity else None}
                if committed or (owned_created and not cleanup):
                    raise PublishError('ARTIFACT_PUBLISH_UNKNOWN', 'owned export publication or cleanup could not be verified',
                                       details=details, stage='post_dispatch') from cause
                raise PublishError(code, 'export was refused or failed before publication', details=details) from cause
            finally:
                if handle is not None:
                    pending = sys.exc_info()[0] is not None
                    try:
                        api.close(handle)
                    except OSError:
                        if not pending:
                            raise
    except OSError as cause:
        raise PublishError('ARTIFACT_PUBLISH_UNKNOWN' if committed else 'ARTIFACT_PUBLISH_FAILED',
                           'Windows export filesystem operation failed', stage='post_dispatch' if committed else 'pre_dispatch',
                           details={'publication_committed': committed, 'cleanup_verified': False}) from cause
