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


class _FullIdInfo(C.Structure):
    _fields_ = [("volume", C.c_uint64), ("file_id", C.c_ubyte * 16)]


class _BasicInfo(C.Structure):
    _fields_ = [("created", C.c_int64), ("accessed", C.c_int64),
                ("written", C.c_int64), ("changed", C.c_int64), ("attributes", DWORD)]


class _StandardInfo(C.Structure):
    _fields_ = [("allocation", C.c_int64), ("size", C.c_int64), ("links", DWORD),
                ("delete_pending", C.c_ubyte), ("directory", C.c_ubyte)]


class _DirectoryInfo(C.Structure):
    _fields_ = [("next_offset", DWORD), ("index", DWORD), ("created", C.c_int64),
                ("accessed", C.c_int64), ("written", C.c_int64), ("changed", C.c_int64),
                ("size", C.c_int64), ("allocation", C.c_int64), ("attributes", DWORD),
                ("name_length", DWORD), ("ea_size", DWORD), ("reparse_tag", DWORD),
                ("file_id", C.c_ubyte * 16), ("name", C.c_uint16 * 1)]


def parse_directory_records(raw, *, _include_dot_records=False):
    """Decode bounded FILE_ID_EXTD_DIR_INFO records, never a path glob."""
    offset = 0; rows = []; seen = set(); header = _DirectoryInfo.name.offset
    while True:
        if offset + header > len(raw):raise PublishError("ACCESS_VIOLATION", "directory record is truncated")
        padded = raw[offset:offset + C.sizeof(_DirectoryInfo)].ljust(C.sizeof(_DirectoryInfo), b'\0')
        item = _DirectoryInfo.from_buffer_copy(padded)
        length = int(item.name_length); step = int(item.next_offset)
        if not length or length % 2 or offset + header + length > len(raw):
            raise PublishError("ACCESS_VIOLATION", "directory name extent is invalid")
        if step and (step % 8 or step < header + length or offset + step >= len(raw)):
            raise PublishError("ACCESS_VIOLATION", "directory record offset is invalid")
        try:name = raw[offset + header:offset + header + length].decode('utf-16-le', 'strict')
        except UnicodeError as exc:raise PublishError("ACCESS_VIOLATION", "directory name encoding is invalid") from exc
        if name in {'.','..'}:
            if (name in seen or not item.attributes & 0x10 or item.attributes & 0x400
                    or item.reparse_tag or not int.from_bytes(bytes(item.file_id), 'little')):
                raise PublishError("ACCESS_VIOLATION", "directory dot record is invalid")
            seen.add(name)
            if _include_dot_records:
                rows.append({'name': name, 'directory': True,
                             'file_id': int.from_bytes(bytes(item.file_id), 'little')})
        else:
            _component(name)
            if '/' in name or '\\' in name or name.casefold() in seen or item.attributes & 0x400 or item.reparse_tag:
                raise PublishError("ACCESS_VIOLATION", "directory has reparse or duplicate entries")
            seen.add(name.casefold())
            rows.append({'name':name,'directory':bool(item.attributes & 0x10),
                         'file_id':int.from_bytes(bytes(item.file_id),'little')})
        if not step:return rows
        offset += step


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


_UNKNOWN_RENAME_OWNERS = []


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
            'GetFileInformationByHandleEx': ([HANDLE, C.c_int32, C.c_void_p, DWORD], BOOL),
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
        self.nt.NtSetInformationFile.argtypes = [HANDLE, C.POINTER(_IoStatusBlock), C.c_void_p, DWORD, C.c_int32]
        self.nt.NtSetInformationFile.restype = NTSTATUS
        self.nt.RtlNtStatusToDosError.argtypes = [NTSTATUS]
        self.nt.RtlNtStatusToDosError.restype = DWORD

    def check(self, ok):
        if not ok:
            owner = getattr(self, "_readonly_preflight_owner", None)
            if owner is not None and getattr(owner, "_retained_service_sid_read", False):
                raise owner._original_win_error(owner._original_get_last_error())
            raise C.WinError(C.get_last_error())

    def _assert_effect_ready(self, handle=None):
        record = getattr(self, '_rename_unknown_record', None)
        if record is not None:
            if handle is not None:record['original_handles'].append(handle)
            raise record['error'] from record['first_cause']

    def _ownership_registry(self):
        self._assert_effect_ready()
        # The worker also loads this helper as one standalone file. Only package
        # calls or an explicitly attached private owner participate in launch
        # ownership; a failed explicit binding must never silently downgrade.
        if __package__:
            from ._windows_owned_resources import registry_for
        elif getattr(self, '_native_owned_registry', None) is not None:
            from comsol_mcp._windows_owned_resources import registry_for
        else:
            return None
        return registry_for(self)

    def open(self, path, *, directory=False, create=False, metadata=False, add_file=False,
             add_directory=False, read_control=False, list_directory=False):
        registry = self._ownership_registry()
        original_closer = self._resource_closer() if registry is not None else None
        access = 0 if metadata else (0x001000A0 if directory else 0xC0010000)
        if read_control:
            access |= 0x00020000  # READ_CONTROL for a read-only security descriptor query
        if directory and list_directory:
            access |= 0x1  # FILE_LIST_DIRECTORY for exact handle enumeration
        if directory and add_file:
            access |= 0x2  # FILE_ADD_FILE on the final pinned parent only
        if directory and add_directory:
            access |= 0x4  # FILE_ADD_SUBDIRECTORY on a pinned directory parent
        share = 7 if metadata and not read_control else (3 if directory or read_control else 1)
        flags = 0x00200000 | (0x02000000 if directory else 0x80)
        preflight = getattr(self, '_readonly_preflight_owner', None)
        if preflight is not None:
            closer = preflight.function('CloseHandle', self.k)
            create_fn = preflight.function('CreateFileW', self.k)
            handle = create_fn(path, access, share, None, 1 if create else 3, flags, None)
            if getattr(handle, 'value', handle) != C.c_void_p(-1).value:
                preflight.capture(handle, family='handle', closer=closer, keepalive=(path,))
        else:
            handle = self.k.CreateFileW(path, access, share, None, 1 if create else 3, flags, None)
        if handle == C.c_void_p(-1).value:
            raise C.WinError(C.get_last_error())
        if registry is not None:
            registry.acquire(self, handle, original_closer, label="security_path_root")
        return handle

    def _open_relative(self, parent_handle, leaf, *, directory=False, create=False, add_file=False,
                       add_directory=False, read_data=False, read_control=False, delete=False, metadata=False, deny_write=False, list_directory=False):
        """Open exactly one child of a pinned directory object.

        A full path assembled from checked ancestor names can start traversing
        a new junction after a pre-existing FILE_WRITE_ATTRIBUTES handle sets
        a reparse point.  NtCreateFile binds this operation to RootDirectory.
        OBJ_DONT_REPARSE fails closed if name parsing encounters a reparse
        point; FILE_OPEN_REPARSE_POINT also asks for the leaf object itself.
        """
        registry = self._ownership_registry()
        original_closer = self._resource_closer() if registry is not None else None
        encoded = leaf.encode('utf-16-le')
        name_buffer = C.create_unicode_buffer(leaf)
        name = _UnicodeString(len(encoded), len(encoded) + 2,
                              C.cast(name_buffer, C.POINTER(C.c_wchar)))
        attributes = _ObjectAttributes(C.sizeof(_ObjectAttributes), parent_handle,
                                       C.pointer(name), 0x40 | 0x1000, None, None)
        output = HANDLE()
        io_status = _IoStatusBlock()
        access = 0x001000A0 if directory else (0x00100081 if read_data else 0x00100080)
        if read_control:
            access |= 0x00020000  # READ_CONTROL for a read-only security descriptor query
        if delete:
            access |= 0x00010000  # DELETE on this exact opened object
        # Directory handles are traversable; file identity probes need only
        # READ_ATTRIBUTES so execution permission is never requested.
        if directory and list_directory:
            access |= 0x1  # directory enumeration is an explicit requested right
        if directory and add_file:
            access |= 0x2  # FILE_ADD_FILE on the final publication parent
        if directory and add_directory:
            access |= 0x4  # FILE_ADD_SUBDIRECTORY on a pinned parent
        if create:
            access = (0x00110080 if directory else 0x00110083)  # add/delete and read identity on created object
            if read_control:access |= 0x00020000
            if directory and add_directory:access |= 0x25  # traverse/list/add-subdirectory
            if directory and add_file:access |= 0x2
        if metadata:access = 0x00100080  # SYNCHRONIZE + READ_ATTRIBUTES for synchronous full-ID queries
        share = (0x7 if metadata else 0x1 if deny_write else
                 0x3 if directory or read_control or delete else (0x1 if create or read_data else 0x7))
        disposition = 2 if create else 1  # FILE_CREATE / FILE_OPEN
        options = (0x1 if directory else 0x40) | 0x20 | 0x00200000
        preflight = getattr(self, '_readonly_preflight_owner', None)
        if preflight is not None:
            from ._windows_host_preflight_owner import scalar
            status = scalar(preflight.allocation_call('NtCreateFile', self.nt, output,
                       C.byref(output), access, C.byref(attributes), C.byref(io_status),
                       None, 0x80, share, disposition, options, None, 0, family='handle',
                       keepalive=(name_buffer, name, attributes, io_status)), 'NTSTATUS')
        else:
            try:
                status = int(self.nt.NtCreateFile(C.byref(output), access, C.byref(attributes),
                                                  C.byref(io_status), None, 0x80, share,
                                                  disposition, options, None, 0))
            except BaseException as primary:
                if output.value not in {None, 0, C.c_void_p(-1).value}:
                    self._retain_partial_relative(output, registry, original_closer, primary,
                                                  (name_buffer, name, attributes, io_status))
                raise
        if status < 0:
            if preflight is None and output.value not in {None, 0, C.c_void_p(-1).value}:
                primary = PublishError("NATIVE_STATUS_FAILED", "failed native status has an ambiguous output handle")
                self._retain_partial_relative(output, registry, original_closer, primary,
                                              (name_buffer, name, attributes, io_status))
            unsigned_status = status & 0xFFFFFFFF
            if unsigned_status == 0xC000050B:  # STATUS_REPARSE_POINT_ENCOUNTERED
                raise PublishError("ACCESS_VIOLATION", "export path encountered a reparse point")
            if unsigned_status in {0xC0000034, 0xC000003A}:  # NAME_NOT_FOUND / PATH_NOT_FOUND
                raise FileNotFoundError(2, "relative entry does not exist", leaf)
            if unsigned_status == 0xC0000035:  # STATUS_OBJECT_NAME_COLLISION
                raise FileExistsError(183, "relative entry already exists", leaf)
            dos_error = int(self.nt.RtlNtStatusToDosError(status))
            raise C.WinError(dos_error)
        if registry is not None:
            registry.acquire(self, output, original_closer, label="relative_file")
        expected_information = 2 if create else 1  # FILE_CREATED / FILE_OPENED
        if int(io_status.Information) != expected_information:
            self.close(output)
            raise PublishError("ACCESS_VIOLATION", "relative file open did not produce the requested object")
        return output

    def _retain_partial_relative(self, output, registry, original_closer, primary, keepalive):
        # Provenance is this actual native out storage and original callback,
        # never a caller-supplied integer or journal/PID reconstruction.
        if registry is not None:
            entry = registry.acquire(self, output, original_closer,
                                     label="partial_relative_output", keepalive=keepalive)
            entry.state = "UNKNOWN"
            entry.error = primary
        if __package__:
            from ._windows_state_restore import retain_unknown
            retain_unknown(self, primary, output=output, closer=original_closer)
        failure = PublishError("ACQUISITION_STATE_UNKNOWN", "original native output exists but acquisition is not terminal")
        failure.original_api = self
        failure.original_native_output = output
        failure.original_cause = primary
        failure.safe_retry = False
        raise failure from primary

    def open_child_directory(self, parent_handle, leaf, *, add_file=False, add_directory=False):
        return self._open_relative(parent_handle, leaf, directory=True, add_file=add_file,
                                   add_directory=add_directory)

    def open_child_directory_for_delete(self, parent_handle, leaf):
        return self._open_relative(parent_handle, leaf, directory=True, delete=True, read_control=True)

    def open_child_file_for_delete(self, parent_handle, leaf):
        return self._open_relative(parent_handle, leaf, delete=True, read_control=True)

    def create_child_directory(self, parent_handle, leaf):
        """Exclusively create one no-reparse directory relative to a pinned parent."""
        return self._open_relative(parent_handle, leaf, directory=True, create=True)

    def create_owned(self, parent_handle, leaf):
        return self._open_relative(parent_handle, leaf, create=True)

    def open_source(self, parent_handle, leaf):
        return self._open_relative(parent_handle, leaf, read_data=True)

    def open_security_path(self, path, *, directory, read_data=False):
        """Open a canonical drive path component-by-component for READ_CONTROL.

        Every returned handle remains open so callers can inspect security on
        the exact traversed objects without releasing the parent chain. UNC,
        extended paths, and reparse components are deliberately unsupported.
        """
        raw = os.fspath(path).replace('/', '\\')
        drive, tail = ntpath.splitdrive(raw)
        if (not re.match(r'^[A-Za-z]:$', drive) or not tail.startswith('\\')
                or raw.startswith(('\\\\', '\\\\?\\'))):
            raise PublishError("ACCESS_VIOLATION", "security path requires an ordinary absolute drive path")
        if len(raw) >= 240:
            raise PublishError("ACCESS_VIOLATION", "security path exceeds the supported ordinary Windows path bound")
        parts = [part for part in tail[1:].split('\\') if part]
        if len(parts) > 128:
            raise PublishError("ACCESS_VIOLATION", "security path exceeds the bounded component count")
        for part in parts:
            _component(part)
        registry = self._ownership_registry()
        scope = registry.scope() if registry is not None else None
        handles = [self.open(drive + '\\', directory=True, metadata=True, read_control=True)]
        if registry is not None:
            registry.acquire(self, handles[0], self._resource_closer(), label="security_path_root")
        try:
            if not parts:
                if not directory:
                    raise PublishError("ACCESS_VIOLATION", "drive root cannot be a provider file")
                return handles
            for index, part in enumerate(parts):
                is_directory = index < len(parts) - 1 or directory
                child = self._open_relative(handles[-1], part, directory=is_directory, read_control=True,
                                            read_data=read_data and not is_directory)
                if registry is not None:
                    registry.acquire(self, child, self._resource_closer(), label="security_path_child")
                handles.append(child)
                self.info(child, directory=is_directory)
            return handles
        except BaseException as original_error:
            preflight = getattr(self, '_readonly_preflight_owner', None)
            if preflight is not None:
                preflight.remember(original_error)
                # The original outer preflight owner unwinds each acquired
                # entry exactly once; a failed entry must never be retried here.
            elif registry is not None:
                registry.drain(scope)  # Failed releases stay with the launch owner.
            else:
                for handle in reversed(handles):
                    try:
                        self.close(handle)
                    except Exception:
                        pass  # Standalone publication retains its original contract.
            raise

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

    def _resource_closer(self):
        preflight = getattr(self, '_readonly_preflight_owner', None)
        if preflight is not None:
            return preflight.path_closer
        # A software OS-boundary override keeps its original callback. The
        # production close wrapper captures the checked native closer.
        if getattr(self.close, "__func__", None) is not getattr(type(self), "close", None):
            return self.close
        native_close = self.k.CloseHandle
        last_error = C.get_last_error
        win_error = C.WinError
        def close_original(handle):
            if not native_close(handle):
                raise win_error(last_error())
        return close_original

    def _close_native(self, handle):
        self.check(self.k.CloseHandle(handle))

    def close(self, handle):
        self._assert_effect_ready(handle)
        preflight = getattr(self, '_readonly_preflight_owner', None)
        if preflight is not None:
            return preflight.release(handle, family='handle')
        registry = self._ownership_registry()
        entry = registry.find(self, handle) if registry is not None else None
        if entry is not None:
            primary = sys.exc_info()[1]
            try:
                return entry.release_owned(registry.owner)
            except BaseException as failure:
                if __package__ and entry.state == "UNKNOWN":
                    from ._windows_state_restore import retain_unknown
                    retain_unknown(self, primary if primary is not None else failure,
                                   output=entry.handle, closer=entry.closer)
                raise
        return self._close_native(handle)

    def info(self, handle, *, directory=False):
        info = _FileInfo()
        preflight = getattr(self, '_readonly_preflight_owner', None)
        if preflight is not None:
            from ._windows_host_preflight_owner import scalar
            if getattr(preflight, '_retained_service_sid_read', False):
                preflight.hold(handle, info)
            self.check(scalar(preflight.function('GetFileInformationByHandle', self.k)(handle, C.byref(info)), 'BOOL'))
        else:
            self.check(self.k.GetFileInformationByHandle(handle, C.byref(info)))
        if info.attributes & 0x400 or bool(info.attributes & 0x10) != directory:
            raise PublishError("ACCESS_VIOLATION", "export path must use ordinary files and directories without reparse points")
        return (int(info.volume), (int(info.index_high) << 32) | int(info.index_low)), (int(info.size_high) << 32) | int(info.size_low)

    def full_fingerprint(self, handle, *, directory=False):
        full = _FullIdInfo(); basic = _BasicInfo(); standard = _StandardInfo()
        try:
            self.check(self.k.GetFileInformationByHandleEx(handle, 18, C.byref(full), C.sizeof(full)))
            self.check(self.k.GetFileInformationByHandleEx(handle, 0, C.byref(basic), C.sizeof(basic)))
            self.check(self.k.GetFileInformationByHandleEx(handle, 1, C.byref(standard), C.sizeof(standard)))
        except OSError as exc:
            raise PublishError("IDENTITY_UNAVAILABLE", "full Windows identity is unavailable; no legacy-ID fallback") from exc
        if (basic.attributes & 0x400 or bool(basic.attributes & 0x10) != directory
                or bool(standard.directory) != directory or standard.delete_pending or standard.size < 0
                or int.from_bytes(bytes(full.file_id),'little')==0 or standard.links<1):
            raise PublishError("ACCESS_VIOLATION", "full identity requires an ordinary non-pending object")
        return ((int(full.volume), int.from_bytes(bytes(full.file_id),'little')), int(standard.size),
                int(basic.created), int(basic.changed), int(basic.written), int(standard.links))

    def full_identity(self, handle, *, directory=False):
        return self.full_fingerprint(handle, directory=directory)[0]

    def named_full_identity(self, path, *, directory=False):
        handle = self.open(path, directory=directory, metadata=True)
        try:return self.full_identity(handle, directory=directory)
        finally:self.close(handle)

    def relative_full_identity(self, parent, leaf, *, directory=False):
        handle = self._open_relative(parent, leaf, directory=directory, metadata=True)
        try:return self.full_identity(handle, directory=directory)
        finally:self.close(handle)

    def create_restore_directory(self, parent, leaf):
        return self._open_relative(parent, leaf, directory=True, create=True, add_file=True,
                                   add_directory=True, read_control=True)

    def create_restore_file(self, parent, leaf):
        return self._open_relative(parent, leaf, create=True, read_control=True)

    def cleanup_open(self, parent, leaf, *, directory=False):
        return self._open_relative(parent, leaf, directory=directory, read_data=not directory,
                                   read_control=True, delete=True, deny_write=not directory, list_directory=directory)

    def enumerate_directory(self, handle, *, max_entries=40000):
        rows=[]; seen=set(); restart=True; empty_batch_seen=False
        while True:
            buffer=C.create_string_buffer(65536)
            ok=self.k.GetFileInformationByHandleEx(handle, 20 if restart else 19, buffer, len(buffer))
            if not ok:
                error=C.get_last_error()
                if error == 18:return rows  # ERROR_NO_MORE_FILES only
                raise C.WinError(error)
            restart=False; records=parse_directory_records(buffer.raw, _include_dot_records=True)
            batch=[row for row in records if row['name'] not in {'.', '..'}]
            for row in records:
                folded=row['name'].casefold()
                if folded in seen:raise PublishError("ACCESS_VIOLATION", "directory enumeration repeated an entry")
                seen.add(folded)
            if not batch:
                # A validated dot-only page precedes ERROR_NO_MORE_FILES on Windows.
                if empty_batch_seen:
                    raise PublishError("ACCESS_VIOLATION", "directory enumeration made no progress")
                empty_batch_seen=True
                continue
            for row in batch:
                rows.append(row)
                if len(rows)>max_entries:raise PublishError("ACCESS_VIOLATION", "directory entry cap exceeded")

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
        self._assert_effect_ready()
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
        self._assert_effect_ready()
        self.check(self.k.FlushFileBuffers(handle))

    def rename(self, handle, parent_handle, leaf):
        self._assert_effect_ready()
        if type(leaf) is not str or '/' in leaf or '\\' in leaf:
            raise PublishError('ACCESS_VIOLATION', 'rename requires one original-parent relative leaf')
        _component(leaf)
        # Original synchronous NtCreateFile handles; never a path fallback.
        native = self.nt.NtSetInformationFile
        translate = self.nt.RtlNtStatusToDosError
        original_close = self.k.CloseHandle
        registry = self._ownership_registry()
        entries = tuple(registry.unresolved()) if registry is not None else ()
        encoded = leaf.encode('utf-16-le')
        offset = _RenameInfo.FileName.offset
        buffer = C.create_string_buffer(max(C.sizeof(_RenameInfo), offset + len(encoded) + 2))
        info = C.cast(buffer, C.POINTER(_RenameInfo)).contents
        info.ReplaceIfExists = 0; info.RootDirectory = parent_handle; info.FileNameLength = len(encoded)
        C.memmove(C.addressof(buffer) + offset, encoded, len(encoded))
        io_status = _IoStatusBlock()
        io_status.Status = 0x7fffffff
        io_status.Information = C.c_size_t(-1).value
        record = {'api':self, 'native':native, 'translate':translate, 'io_status':io_status,
                  'buffer':buffer, 'name':leaf, 'original_handles':[handle,parent_handle],
                  'registry':registry, 'entries':entries, 'original_close':original_close, 'first_cause':None}
        try:
            result = native(handle, C.byref(io_status), buffer, len(buffer), 10)
            status = getattr(result, 'value', result)
            if type(status) is not int or not -(1<<31) <= status < (1<<31):
                raise ValueError('native rename returned a malformed NTSTATUS')
            # Definite negative status means no accepted rename, not UNKNOWN.
            if status < 0:
                failure_status = status
            elif status == 0 and io_status.Status == 0 and io_status.Information <= len(buffer):
                return
            else:
                raise ValueError('native rename completion is not synchronous and confirmed')
        except BaseException as first:
            record['first_cause'] = first
            error = PublishError('EXECUTION_STATE_UNKNOWN', 'original native rename completion is UNKNOWN',
                                 details={'safe_retry':False}, stage='execution_unknown')
            record['error'] = error
            self._rename_unknown_record = record
            _UNKNOWN_RENAME_OWNERS.append(record)
            # Preserve original acquired objects/closers. finish/drain must not
            # turn an ambiguous effect into automatic CloseHandle or rollback.
            if registry is not None:
                for entry in entries:
                    if entry.state != 'RELEASED':entry.state='UNKNOWN';entry.error=error
            if __package__:
                from ._windows_state_restore import retain_unknown
                retain_unknown(self, first, output=(io_status,buffer,record), owner=record)
            raise error from first
        # Preserve the existing WinError/no-clobber consumer path on a definite
        # NT rejection. No second rename API, retry, or absolute target name.
        mapped = translate(failure_status)
        code = getattr(mapped, 'value', mapped)
        raise C.WinError(code)

    def delete_owned(self, handle):
        self._assert_effect_ready()
        disposition = _DispositionInfo(1)
        self.check(self.k.SetFileInformationByHandle(handle, 4, C.byref(disposition), C.sizeof(disposition)))


@contextmanager
def pinned_parent(project_root, destination, *, add_file=True, add_directory=False, _api=None):
    """Hold every lexical directory ancestor without delete sharing."""
    root, parts = validate_paths(project_root, destination)
    api = _api or _API(); handles = []; names = []
    current = root[:3]
    try:
        components = [*(root[3:].split('\\') if len(root) > 3 else []), *parts[:-1]]
        root_handle = api.open(current, directory=True, add_file=add_file and not components,
                               add_directory=add_directory)
        handles.append(root_handle)
        identity = api.info(root_handle, directory=True)[0]
        if api.named_identity(current, directory=True) != identity:
            raise PublishError("ACCESS_VIOLATION", "export volume root changed during admission")
        names.append((current, identity))
        for index, part in enumerate(components):
            handle = api.open_child_directory(handles[-1], part,
                                              add_file=add_file and index == len(components) - 1,
                                              add_directory=add_directory)
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


def delete_exact_private_stage(stage_root, expected_root_identity, expected_file_identity,
                               *, source_name="model.mph", _api=None, _verify_security=None):
    """Delete a verified private stage using only pinned Windows handles.

    The source file and stage directory are each opened relative to a pinned
    parent with DELETE access and without FILE_SHARE_DELETE. Identity and ACL
    evidence are checked before marking either handle for deletion. A nonempty
    directory or substituted leaf remains in place and raises a typed error.
    """
    if os.name != "nt":
        raise PublishError("NOT_IMPLEMENTED", "Windows private-stage cleanup requires Windows")
    owned_acl = _verify_security is None
    if owned_acl:
        from ._host_scoped_provider import _verify_windows_owned_handle_security

    path = os.fspath(stage_root)
    if (not isinstance(path, str) or not ntpath.isabs(path) or not ntpath.basename(path) or source_name != "model.mph"
            or not isinstance(expected_root_identity, dict)
            or not isinstance(expected_file_identity, dict)):
        raise PublishError("INVALID_REQUEST", "private-stage cleanup requires exact pinned directory/file identities")
    if owned_acl:
        from pathlib import Path
        from ._windows_state_restore import delete_private_stage
        return delete_private_stage(Path(path), expected_root_identity, expected_file_identity, api=_api)
    api = _api or _API()
    directory_handle = file_handle = None
    def check_security(handle, name, directory, expected):
        if owned_acl:
            _verify_windows_owned_handle_security(api, handle, directory=directory)
            return True
        # Explicit SDK test seam; production always queries the original handle.
        return _verify_security(name, directory=directory, expected_identity=expected).get("identity") == expected
    with pinned_parent(ntpath.dirname(path), ntpath.basename(path), add_file=False,
                       _api=api) as (_api_value, parent, parts, binding):
        if len(parts) != 1:
            raise PublishError("ACCESS_VIOLATION", "private-stage path must be a direct child of its pinned parent")
        try:
            directory_handle = api.open_child_directory_for_delete(parent, parts[0])
            directory_identity = api.info(directory_handle, directory=True)[0]
            if (directory_identity != (expected_root_identity.get("device"), expected_root_identity.get("inode"))
                    or not check_security(directory_handle, path, True, expected_root_identity)):
                raise PublishError("ACCESS_VIOLATION", "private-stage directory identity or ACL changed before cleanup")
            file_handle = api.open_child_file_for_delete(directory_handle, source_name)
            file_fingerprint = api.source_fingerprint(file_handle)
            if (file_fingerprint[0] != (expected_file_identity.get("device"), expected_file_identity.get("inode"))
                    or file_fingerprint[1] <= 0 or file_fingerprint[4] != 1
                    or not check_security(file_handle, ntpath.join(path, source_name), False, expected_file_identity)):
                raise PublishError("ACCESS_VIOLATION", "private-stage file identity, link count, or ACL changed before cleanup")
            api.delete_owned(file_handle)
            api.close(file_handle)
            file_handle = None
            if api.exists_child(directory_handle, source_name):
                raise PublishError("ACCESS_VIOLATION", "private-stage source file remained after handle deletion")
            api.verify_named_binding(binding)
            if not check_security(directory_handle, path, True, expected_root_identity):
                raise PublishError("ACCESS_VIOLATION", "private-stage directory changed before handle deletion")
            api.delete_owned(directory_handle)
            api.close(directory_handle)
            directory_handle = None
            if api.exists(path):
                raise PublishError("ACCESS_VIOLATION", "private-stage directory name was recreated during cleanup")
        finally:
            if file_handle is not None:
                api.close(file_handle)
            if directory_handle is not None:
                api.close(directory_handle)


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
