"""Private control files: read-only Windows ACL admission and original fd use.

No ACL setter, service action or credential enters this module's public errors.
Windows paths use the existing provider verifier; POSIX callers keep their
existing policy at the control entrypoints.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import stat

_LIMIT = 64 * 1024
_SEAL = object()
_RETAINED = []
_FENCED = set()


class ControlPrivacyError(RuntimeError):
    def __init__(self):
        super().__init__("Private control path verification failed")


def _is_windows():
    return os.name == "nt"


def _hold(error, *roots):
    # Strong private roots, including an uncertain fd/API owner and original
    # traceback. Never serialize these, retry, or close them during unwinding.
    _RETAINED.append((error, error.__traceback__, roots))
    raise ControlPrivacyError() from None


def retain_operation(error, *roots):
    """Retain existing control resources without issuing a cleanup operation."""
    _RETAINED.append((error, error.__traceback__, roots))


def _absolute(value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ControlPrivacyError()
    return path


def _within_home(home, consume, *, expected_identity=None):
    from ._host_scoped_provider import _with_control_private_path
    key = os.fspath(_absolute(home)).casefold()
    if key in _FENCED:
        raise ControlPrivacyError()
    try:
        return _with_control_private_path(_absolute(home), directory=True,
            expected_identity=expected_identity,
            consume=lambda api, leaf, meta: consume(meta))
    except BaseException as error:
        _FENCED.add(key)
        _hold(error, home, consume)


def prepare_home(value, *, create):
    """Only exclusive new directory creation; never repair existing Windows ACLs."""
    home = _absolute(value)
    key = os.fspath(home).casefold()
    if key in _FENCED:
        raise ControlPrivacyError()
    if not _is_windows():
        raise ControlPrivacyError()  # POSIX entrypoints retain their old path.
    try:
        home.lstat()
    except FileNotFoundError:
        if not create:
            raise ControlPrivacyError() from None
        parent = home.parent
        try:
            parent.lstat()
        except FileNotFoundError:
            prepare_home(parent, create=True)
        # Parent pins survive mkdir and validation of the inherited child DACL.
        def make(_meta):
            home.mkdir(mode=0o700, exist_ok=False)
            return _within_home(home, lambda _child: home)
        return _within_home(parent, make)
    except BaseException as error:
        _FENCED.add(key)
        _hold(error, home)
    return _within_home(home, lambda _meta: home)


class _OriginalFile:
    def __init__(self, path):
        self.path = path
        self.fd = None
        self.state = "NEW"
        self.error = None

    def fileno(self):
        if self.state != "OPEN":
            raise ControlPrivacyError()
        return self.fd

    def open(self, flags):
        self.state = "OPEN_ENTERED"
        self.fd = os.open(self.path, flags | getattr(os, "O_BINARY", 0)
                          | getattr(os, "O_NOFOLLOW", 0), 0o600)
        self.state = "OPEN"

    def close(self):
        self.state = "CLOSE_ENTERED_UNKNOWN"
        os.close(self.fd)
        self.state = "CLOSED"


def _named_file(path):
    value = path.lstat()
    if (not stat.S_ISREG(value.st_mode) or stat.S_ISLNK(value.st_mode)
            or getattr(value, "st_file_attributes", 0) & 0x400
            or value.st_nlink != 1):
        raise ControlPrivacyError()
    return value


def _verify_fd(file, *, expected_identity=None):
    from ._host_scoped_provider import (_assert_opened_path_security,
                                       _CONTROL_PRIVACY_ACCESS)
    named = _named_file(file.path)
    opened = os.fstat(file.fd)
    if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)):
        raise ControlPrivacyError()
    return _assert_opened_path_security(file.path, file.fd, opened,
        directory=False, expected_identity=expected_identity,
        _control_access=_CONTROL_PRIVACY_ACCESS)


def _fingerprint(file):
    value = os.fstat(file.fd)
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
            value.st_ctime_ns, value.st_nlink)


def _read_bytes(file):
    before = _fingerprint(file)
    if before[2] <= 0 or before[2] > _LIMIT:
        raise ControlPrivacyError()
    chunks = []
    total = 0
    while True:
        block = os.read(file.fd, min(65536, _LIMIT + 1 - total))
        if not block:
            break
        total += len(block)
        if total > _LIMIT:
            raise ControlPrivacyError()
        chunks.append(block)
    if _fingerprint(file) != before:
        raise ControlPrivacyError()
    return b"".join(chunks)


def _file_read(path):
    file = _OriginalFile(path)
    try:
        file.open(os.O_RDONLY)
        admission = _verify_fd(file)
        raw = _read_bytes(file)
        _verify_fd(file, expected_identity=admission["identity"])
        file.close()
        return raw, admission
    except BaseException as error:
        file.error = error
        _hold(error, file)


class _Endpoint(dict):
    def __init__(self, seal, value, home, root_identity, file_identity, digest):
        if seal is not _SEAL:
            raise ControlPrivacyError()
        super().__init__(value)
        self._original_value = copy.deepcopy(value)
        self._home = home
        self._root_identity = copy.deepcopy(root_identity)
        self._file_identity = copy.deepcopy(file_identity)
        self._digest = digest


def _parse(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ControlPrivacyError()
            value[key] = item
        return value
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    if type(value) is not dict:
        raise ControlPrivacyError()
    return value


def read_endpoint(home):
    home = _absolute(home)
    def read(meta):
        path = home / "control.json"
        try:
            _named_file(path)
        except FileNotFoundError:
            return None
        raw, admission = _file_read(path)
        return _Endpoint(_SEAL, _parse(raw), home, meta["identity"],
                         admission["identity"], hashlib.sha256(raw).hexdigest())
    return _within_home(home, read)


def request_if_current(endpoint, submit):
    """Revalidate the exact confidential object before network submission."""
    if not isinstance(endpoint, _Endpoint):
        return submit(endpoint)  # Existing separately verified SCM route.
    def request(_meta):
        if dict(endpoint) != endpoint._original_value:
            raise ControlPrivacyError()
        raw, admission = _file_read(endpoint._home / "control.json")
        if (admission["identity"] != endpoint._file_identity
                or hashlib.sha256(raw).hexdigest() != endpoint._digest
                or _parse(raw) != endpoint._original_value):
            raise ControlPrivacyError()
        return copy.deepcopy(endpoint._original_value)
    admitted = _within_home(endpoint._home, request,
                            expected_identity=endpoint._root_identity)
    # Only immutable authenticated metadata is used after verification. Keep
    # transport exceptions intact; never relabel an entered RPC as not submitted.
    return submit(admitted)


def with_log(home, launch):
    home = _absolute(home)
    def open_log(_meta):
        path = home / "control.log"
        file = _OriginalFile(path)
        try:
            try:
                _named_file(path)
                flags = os.O_RDWR | os.O_APPEND
            except FileNotFoundError:
                flags = os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_EXCL
            file.open(flags)
            _verify_fd(file)  # Must precede the child inheriting this descriptor.
            return file
        except BaseException as error:
            file.error = error
            _hold(error, file)
    file = _within_home(home, open_log)
    try:
        result = launch(file)
    except BaseException as error:
        # The existing platform-specific spawn error and action remain exact.
        # No descriptor cleanup follows an uncertain original constructor.
        retain_operation(error, file)
        _FENCED.add(os.fspath(home).casefold())
        raise
    try:
        file.close()
    except BaseException as error:
        _FENCED.add(os.fspath(home).casefold())
        _hold(error, file)
    return result


def publish_endpoint(home, endpoint):
    """Validate an empty exclusive temp before writing any secret bytes."""
    home = _absolute(home)
    payload = json.dumps(endpoint).encode("utf-8")
    if not 0 < len(payload) <= _LIMIT:
        raise ControlPrivacyError()
    def publish(_meta):
        temporary = home / "control.json.tmp"
        target = home / "control.json"
        file = _OriginalFile(temporary)
        try:
            try:
                _named_file(target)
            except FileNotFoundError:
                previous = None
            else:
                # A restart may replace its old endpoint, but never blindly
                # overwrite a reparse, hardlink or independently unsafe file.
                previous_raw, previous_admission = _file_read(target)
                previous = (previous_raw, previous_admission["identity"])
            file.open(os.O_RDWR | os.O_CREAT | os.O_EXCL)
            admission = _verify_fd(file)
            if os.fstat(file.fd).st_size != 0:
                raise ControlPrivacyError()
            if os.write(file.fd, payload) != len(payload):
                raise ControlPrivacyError()
            os.fsync(file.fd)
            os.lseek(file.fd, 0, os.SEEK_SET)
            if _read_bytes(file) != payload:
                raise ControlPrivacyError()
            _verify_fd(file, expected_identity=admission["identity"])
            file.close()  # The original CRT fd must release its no-delete share.
            if previous is None:
                try:
                    target.lstat()
                except FileNotFoundError:
                    pass
                else:
                    raise ControlPrivacyError()
            else:
                current_raw, current_admission = _file_read(target)
                if (current_raw, current_admission["identity"]) != previous:
                    raise ControlPrivacyError()
            # Parents remain SDK-pinned; their verified DACL admits only trusted
            # writers. Singleton publication is not a hostile same-UID sandbox.
            temporary.replace(target)
            raw, final = _file_read(target)
            if raw != payload or final["identity"] != admission["identity"]:
                raise ControlPrivacyError()
        except BaseException as error:
            file.error = error
            _hold(error, file)
    return _within_home(home, publish)
