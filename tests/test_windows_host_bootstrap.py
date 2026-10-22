"""Inline synthetic fixtures only. No host plan, SDK mutation or native acceptance."""
from __future__ import annotations

import copy
import ctypes as C
import io
import json
import ntpath
import struct
import zipfile
from types import SimpleNamespace

import pytest

from comsol_mcp import _windows_host_bootstrap as b


SERVICE_SID = "S-1-5-80-1-2-3-4-5"  # SOFTWARE_ONLY synthetic SID, not an observed service
PREFIX = r"C:\Protected\ComsolMCP_fixture"
SOURCE = r"C:\Staging\approved"


def test_token_elevation_leaf_uses_exact_dword_request_and_result():
    """SOFTWARE_ONLY actual production leaf; oversized requests emulate Win32 24."""
    calls, error = [], [0]

    def token_info(handle, level, buffer, size, needed):
        calls.append((handle.value, level, size, C.sizeof(buffer)))
        C.cast(needed, C.POINTER(b.DWORD))[0] = C.sizeof(b.DWORD)
        if size != C.sizeof(b.DWORD):
            error[0] = 24
            return 0
        C.cast(buffer, C.POINTER(b.DWORD))[0] = 2
        return 1

    native = b._Windows._test({"GetTokenInformation": token_info}, lambda: error[0], seal=b._TEST_SEAL)
    buffer, used = native._token_buffer(SimpleNamespace(arg=lambda: b.HANDLE(777)), 20)
    assert used == C.sizeof(buffer) == C.sizeof(b.DWORD) == 4
    assert b.DWORD.from_buffer(buffer).value == 2
    assert calls == [(777, 20, 4, 4)]


@pytest.mark.parametrize("returned_extent", [0, 3, 8])
def test_token_elevation_leaf_rejects_non_dword_returned_extent(returned_extent):
    calls = []

    def token_info(handle, level, buffer, size, needed):
        calls.append((level, size))
        C.cast(needed, C.POINTER(b.DWORD))[0] = returned_extent
        return 1

    native = b._Windows._test({"GetTokenInformation": token_info}, lambda: 0, seal=b._TEST_SEAL)
    with pytest.raises(b.BootstrapError) as exc:
        native._token_buffer(SimpleNamespace(arg=lambda: b.HANDLE(777)), 20)
    assert exc.value.code == "TOKEN_BOUNDS"
    assert len(calls) == 1  # no SDK retry for a malformed returned extent


@pytest.mark.parametrize("level", [1, 3])
def test_variable_token_leaf_retains_original_bounded_buffer(level):
    calls = []

    def token_info(handle, information_class, buffer, size, needed):
        calls.append((information_class, size))
        C.cast(needed, C.POINTER(b.DWORD))[0] = 4
        C.cast(buffer, C.POINTER(b.DWORD))[0] = 0
        return 1

    native = b._Windows._test({"GetTokenInformation": token_info}, lambda: 0, seal=b._TEST_SEAL)
    buffer, used = native._token_buffer(SimpleNamespace(arg=lambda: b.HANDLE(777)), level)
    assert used == 4 and C.sizeof(buffer) == 65536
    assert calls == [(level, 65536)]


def raw_sd(directory=False, *, service=False, service_sid=None, owner=b.SY, broad=False):
    """Real self-relative binary layout, synthetic content, never an OS readback."""
    flags, mask = (3 if directory and not service else 0), (0xF01FF if service else 0x1F01FF)
    rows = [(b.SY, mask), (b.BA, mask)]
    if service_sid:
        rows.append((service_sid, 0x1200A9))
    if broad:
        rows.append(("S-1-1-0", 0x1F01FF))
    aces = []
    for sid, rights in rows:
        binary = b._sid_bytes(sid)
        aces.append(struct.pack("<BBHI", 0, flags, 8 + len(binary), rights) + binary)
    acl = struct.pack("<BBHHH", 2, 0, 8 + sum(map(len, aces)), len(aces), 0) + b"".join(aces)
    owner_raw, group_raw = b._sid_bytes(owner), b._sid_bytes(b.BA)
    return (struct.pack("<BBHIIII", 1, 0, 0x9004, 20, 20 + len(owner_raw), 0,
                        20 + len(owner_raw) + len(group_raw)) + owner_raw + group_raw + acl)


def python_pe(import_name=b"python312.dll"):
    data = bytearray(1024)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 128)
    data[128:132] = b"PE\0\0"
    struct.pack_into("<HH", data, 132, 0x8664, 1)
    struct.pack_into("<H", data, 148, 240)
    struct.pack_into("<H", data, 152, 0x20B)
    struct.pack_into("<II", data, 272, 0x1000, 40)
    struct.pack_into("<IIII", data, 400, 512, 0x1000, 512, 512)
    struct.pack_into("<IIIII", data, 512, 0, 0, 0, 0x1050, 0)
    data[592:592 + len(import_name) + 1] = import_name + b"\0"
    return bytes(data)


def archive(rows):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as z:
        for name, data in rows:
            z.writestr(name, data)
    return stream.getvalue()


def intent_doc():
    py, dll = python_pe(), b"SOFTWARE_ONLY Python DLL fixture"
    source = {
        "python312/tools/python.exe": py,
        "python312/tools/python312.dll": dll,
        "python312/tools/Lib/site-packages/comsol_mcp/_control_daemon.py": b"# inline software fixture\n",
        "jdk11/bin/java.exe": b"SOFTWARE_ONLY JDK bytes",
        "materials/python.nupkg": archive([("tools/python.exe", py), ("tools/python312.dll", dll)]),
        "materials/offline.zip": archive([("locks/requirements.lock", b"# software-only lock\n")]),
    }
    role = {"python312/tools/python.exe": "python", "jdk11/bin/java.exe": "jdk",
            "materials/python.nupkg": "python_archive", "materials/offline.zip": "bundle_archive"}
    rows = [dict(path=p, size=len(v), sha256=b.digest(v), role=role.get(p, "runtime"))
            for p, v in source.items()]
    jdk = [dict(path="bin/java.exe", size=len(source["jdk11/bin/java.exe"]),
                sha256=b.digest(source["jdk11/bin/java.exe"]))]
    argv = b.service_argv(PREFIX, "ComsolMCP_fixture")
    doc = dict(schema=b.SCHEMA, prefix=PREFIX, source_root=SOURCE,
               service_name="ComsolMCP_fixture", files=rows,
               materials=dict(python_nupkg_sha256=b.digest(source["materials/python.nupkg"]),
                              bundle_sha256=b.digest(source["materials/offline.zip"]),
                              jdk_inventory_sha256=b.digest(b.canonical(jdk))),
               runtime_layout="CPYTHON312_NORMAL_OFFLINE_MATERIALIZED",
               service_argv=list(argv), quoted_command_line=b.subprocess.list2cmdline(argv))
    return doc, source


def encode(doc):
    return b.canonical(doc)


class Once:
    def __init__(self, first):
        self.first, self.reads = first, 0

    @property
    def value(self):
        self.reads += 1
        if self.reads > 1:
            raise ValueError("SOFTWARE_ONLY result consumed twice")
        return self.first


def sharing_conflict(access, share, old_access, old_share):
    """CreateFile bidirectional sharing model; SOFTWARE_ONLY, never OS evidence."""
    for mask, shared in ((0x80000001, 1), (0x40000006, 2), (0x10000, 4)):
        if (access & mask and not old_share & shared) or (old_access & mask and not share & shared):
            return True
    return False


class FakeWindows:
    """Private-seal software model, not native API evidence."""
    def __init__(self, source):
        self.tree, self.handles, self.calls, self.file_opens = {}, {}, [], {}
        self.next_id, self.next_handle, self.error = 10, 100, 0
        self.service, self.pending_service_delete = None, False
        self.faults, self.close_values = {}, {}
        self.admin, self.system_owner = True, True
        self.sdk = {
            "OpenSCManagerW": self._manager, "OpenServiceW": self._open_service,
            "CreateServiceW": self._create_service, "CloseServiceHandle": self._close,
            "CreateFileW": self._open_node, "CloseHandle": self._close,
            "CreateDirectoryW": self._mkdir, "WriteFile": self._write,
            "ChangeServiceConfig2W": self._configure, "SetServiceObjectSecurity": self._service_sd,
            "SetSecurityInfo": self._file_sd, "DeleteService": self._delete_service,
            "SetFileInformationByHandle": self._delete_node,
        }
        self.owner = b._Owner(self)
        for path in (r"C:\Protected", SOURCE):
            parts = path.split("\\")
            for n in range(1, len(parts) + 1):
                directory = "\\".join(parts[:n])
                if n == 1:
                    directory += "\\"
                self.add(directory, True)
        for p, data in source.items():
            path = ntpath.join(SOURCE, *p.split("/"))
            parent = ntpath.dirname(path)
            while parent != SOURCE:
                self.add(parent, True)
                parent = ntpath.dirname(parent)
            self.add(path, False, data)
        self.add(r"C:\Protected\user-existing.txt", False, b"unrelated user data")
        self.original = copy.deepcopy(self.tree)

    def function(self, name):
        return self.sdk[name]

    def last_error(self):
        return self.error

    def add(self, path, directory, data=b"", sd=None):
        if path not in self.tree:
            self.next_id += 1
            self.tree[path] = dict(identity=((1 << 48) | 7, (1 << 96) | self.next_id), directory=directory,
                                   data=data, sd=sd or raw_sd(directory), reparse=False, links=1)
        return self.tree[path]

    def handle(self, kind, value):
        self.next_handle += 1
        self.handles[self.next_handle] = [kind, value, False]
        return self.next_handle

    def target(self, arg):
        return self.handles[arg.value][1]

    def fault(self, name, result):
        mode = self.faults.pop(name, None)
        if mode == "raise":
            raise OSError("SOFTWARE_ONLY SDK wrote then raised: " + name)
        if mode == "invalid":
            return "invalid SDK result"
        return result

    def _manager(self, machine, database, access):
        self.calls.append(("OpenSCManagerW", access))
        return self.fault("OpenSCManagerW", self.handle("manager", access))

    def _open_service(self, manager, name, access):
        self.calls.append(("OpenServiceW", name, access))
        if self.service is None:
            self.error = 1060
            return 0
        return self.handle("service", self.service)

    def _create_service(self, manager, name, display, access, kind, start, error, binary,
                        group, tag, deps, account, password):
        self.calls.append(("CreateServiceW", name, access, kind, start, binary, account))
        if self.faults.pop("CreateServiceW_known_before_write", False):
            self.error = 5
            return 0
        assert self.service is None and group is tag is deps is password is None
        self.service = dict(service_type=kind, start_type=start, error_control=error,
                            binary=binary, group="", tag=0, dependencies=(), account=account,
                            display=display, state=1, pid=0, status=(kind, 1, 0, 0, 0, 0, 0, 0, 0),
                            raw_sd=raw_sd(service=True), sid_type=0, privileges=(),
                            failure_actions=(0, "", "", ()), failure_flag=False, dependents=())
        return self.fault("CreateServiceW", self.handle("service", self.service))

    def _open_node(self, path, access, share, security, creation, flags, template):
        self.calls.append(("CreateFileW", path, access, share, creation, flags))
        assert creation == 3 and template is None  # Fake copy creates exclusively before this open.
        if path not in self.tree:
            self.error = 2
            return C.c_void_p(-1).value
        for old_path, old_access, old_share in self.file_opens.values():
            if old_path == path and sharing_conflict(access, share, old_access, old_share):
                self.error = 32
                return C.c_void_p(-1).value
        handle = self.handle("file", path)
        self.file_opens[handle] = (path, access, share)
        self.error = 0
        return self.fault("CreateFileW", handle)

    def _mkdir(self, path, sd):
        self.calls.append(("CreateDirectoryW", path))
        if path in self.tree:
            self.error = 183
            return 0
        self.add(path, True, sd=sd)
        return self.fault("CreateDirectoryW", 1)

    def _write(self, arg, data):
        self.calls.append(("WriteFile", self.target(arg)))
        self.tree[self.target(arg)]["data"] = data
        return self.fault("WriteFile", 1)

    def _configure(self, arg, field, value):
        self.calls.append(("ChangeServiceConfig2W", field))
        if self.faults.pop("config_known_before_write", False):
            self.error = 5
            return 0
        self.target(arg)[field] = value
        return self.fault("ChangeServiceConfig2W", 1)

    def _service_sd(self, arg, sd):
        self.calls.append(("SetServiceObjectSecurity",))
        self.target(arg)["raw_sd"] = sd
        return self.fault("SetServiceObjectSecurity", 1)

    def _file_sd(self, arg, sd):
        self.calls.append(("SetSecurityInfo", self.target(arg)))
        self.tree[self.target(arg)]["sd"] = sd
        return self.fault("SetSecurityInfo", 0)

    def _delete_service(self, arg):
        self.calls.append(("DeleteService",))
        assert self.target(arg) is self.service
        self.pending_service_delete = True
        return self.fault("DeleteService", 1)

    def _delete_node(self, arg):
        self.calls.append(("SetFileInformationByHandle", self.target(arg)))
        assert self.file_opens[arg.value][1] & 0x10000  # no disposition through a runtime read pin
        self.handles[arg.value].append("delete_pending")
        return self.fault("SetFileInformationByHandle", 1)

    def _close(self, arg):
        self.calls.append(("close", arg.value))
        if self.faults.pop("close_known_before_write", False):
            self.error = 5
            return 0
        kind, value, closed, *tail = self.handles[arg.value]
        assert not closed
        self.handles[arg.value][2] = True
        self.file_opens.pop(arg.value, None)
        if kind == "file" and tail:
            del self.tree[value]
        if kind == "service" and self.pending_service_delete:
            if not any(k == "service" and not c for k, _, c, *_ in self.handles.values()):
                self.service = None
        return self.fault("close", self.close_values.get(arg.value, 1))

    manager = b._Windows.manager
    service_absent = b._Windows.service_absent
    create_service = b._Windows.create_service
    compare_service_name = b._Windows.compare_service_name
    check_initial_service = b._Windows.check_initial_service
    _open = b._Windows._open
    open_node = b._Windows.open_node
    fresh_image = b._Windows.fresh_image
    bridge_node = b._Windows.bridge_node
    runtime_node = b._Windows.runtime_node
    restore_node = b._Windows.restore_node

    def image(self, handle, path, directory):
        if self.target(handle.arg()) != path:
            b.fail("FILE_ESCAPE", "fake final path differs")
        row = self.tree[path]
        if row["reparse"] or row["directory"] != directory or (not directory and row["links"] != 1):
            b.fail("FILE_KIND", "fake reparse/link/kind drift")
        return b.NodeImage(row["identity"], directory, 0 if directory else len(row["data"]),
                           None if directory else b.digest(row["data"]), row["sd"],
                           0x10 if directory else 0x80, row["links"], () if directory else (10, 20))

    def absent(self, path):
        return path not in self.tree

    def read(self, handle, size):
        raw = self.tree[self.target(handle.arg())]["data"]
        assert len(raw) == size
        return raw

    def sd(self, directory, **kwargs):
        return raw_sd(directory, **kwargs)

    def create_directory(self, path, sd, parent):
        self.owner.call("CreateDirectoryW", (path, sd))
        return self._open(path, True, write=True)

    def create_file(self, path, sd, source, size):
        assert path not in self.tree
        self.add(path, False, sd=sd)
        handle = self._open(path, False, write=True)
        self.owner.call("WriteFile", (handle.arg(), self.read(source, size)))
        return handle

    def service_image(self, handle):
        return copy.deepcopy(self.target(handle.arg()))

    def configure(self, handle, field, value):
        self.owner.call("ChangeServiceConfig2W", (handle.arg(), field, value))

    def service_security(self, handle, raw):
        self.owner.call("SetServiceObjectSecurity", (handle.arg(), raw))

    def file_security(self, handle, raw):
        self.owner.call("SetSecurityInfo", (handle.arg(), raw), "DWORD")

    def lookup_service_sid(self, name):
        return SERVICE_SID

    def names(self, path):
        return {ntpath.basename(p) for p in self.tree if p != path and ntpath.dirname(p) == path}

    def context(self):
        return {"elevated_admin": self.admin, "can_assign_SYSTEM_owner": self.system_owner,
                "enables_privileges": False, "token_user_sid": b.SY}

    def delete_service(self, handle):
        self.owner.call("DeleteService", (handle.arg(),))

    def delete_node(self, handle):
        self.owner.call("SetFileInformationByHandle", (handle.arg(),))


@pytest.fixture(autouse=True)
def private_software_retention(monkeypatch):
    # Only synthetic owners are discarded between tests, never native owners.
    monkeypatch.setattr(b, "_retained", [])


def fixture_bootstrap(*, prepared=True):
    doc, source = intent_doc()
    backend = FakeWindows(source)
    original = b.Bootstrap(b.parse_plan(encode(doc)), backend, seal=b._TEST_SEAL)
    if prepared:
        original.prepare()
    return original, backend, doc


def install():
    original, backend, doc = fixture_bootstrap()
    original.apply(approved_plan_sha256=original.intent.sha256)
    return original, backend, doc


def test_plan_is_finite_intent_and_binds_actual_argv_without_shell():
    doc, _ = intent_doc()
    plan = b.parse_plan(encode(doc))
    assert plan.argv == b.service_argv(PREFIX, "ComsolMCP_fixture")
    assert plan.command_line == b.subprocess.list2cmdline(plan.argv)
    assert plan.directories[0] == ""
    assert len(plan.files) == 6
    with pytest.raises(b.BootstrapError):
        b.Bootstrap(plan, object(), seal=object())


@pytest.mark.parametrize("mutation", [
    "extra", "duplicate_file", "escape", "drive", "unc", "prefix_overlap",
    "argv", "quoted", "venv", "collision", "size_bool", "bad_hash", "service_name",
    "fake_profile", "unknown_role", "jdk_hash",
])
def test_closed_plan_rejects_noncanonical_or_unbound_intent(mutation):
    doc, _ = intent_doc()
    if mutation == "extra": doc["apply_ready"] = True
    elif mutation == "duplicate_file": doc["files"].append(doc["files"][0])
    elif mutation == "escape": doc["files"][0]["path"] = "../outside.exe"
    elif mutation == "drive": doc["files"][0]["path"] = "D:/outside.exe"
    elif mutation == "unc": doc["prefix"] = r"\\server\share\ComsolMCP_fixture"
    elif mutation == "prefix_overlap": doc["source_root"] = PREFIX + r"\source"
    elif mutation == "argv": doc["service_argv"].append("--windows-scm-bootstrap-request")
    elif mutation == "quoted": doc["quoted_command_line"] += " & cmd.exe"
    elif mutation == "venv": doc["files"][0]["path"] = "python312/pyvenv.cfg"
    elif mutation == "collision": doc["files"][0]["path"] = "python312/tools"
    elif mutation == "size_bool": doc["files"][0]["size"] = True
    elif mutation == "bad_hash": doc["files"][0]["sha256"] = "0" * 63
    elif mutation == "service_name": doc["service_name"] = "ExistingSystemService"
    elif mutation == "fake_profile": doc["project_id"] = "fake-guid"
    elif mutation == "unknown_role": doc["files"][0]["role"] = "issuer"
    elif mutation == "jdk_hash": doc["materials"]["jdk_inventory_sha256"] = "0" * 64
    with pytest.raises(b.BootstrapError):
        b.parse_plan(encode(doc))


@pytest.mark.parametrize("raw", [b"", b"[]", b'{"schema":1,"schema":2}', b'{"x":NaN}', b"\xff", {}])
def test_strict_json_bounds_duplicates_and_bytes(raw):
    with pytest.raises(b.BootstrapError):
        b.parse_plan(raw)


def test_prepare_and_preview_are_readonly_and_never_profile_authority():
    original, backend, _ = fixture_bootstrap()
    preview = original.preview()
    assert preview["authority"] == "LOCAL_INSTALLER_ONLY_NOT_T037"
    assert preview["native_start"].startswith("BLOCKED_")
    assert preview["apply_ready_package_claim"] is False
    assert backend.tree == backend.original and backend.service is None
    assert not any(c[0] in {"CreateDirectoryW", "WriteFile", "CreateServiceW"} for c in backend.calls)
    original.abandon_readonly()
    assert original.state == "CLOSED_READONLY" and b.retained_bootstrap_owners() == ()


@pytest.mark.parametrize("existing", ["prefix", "service", "reparse_source", "hardlink_source", "source_bytes", "parent_acl"])
def test_prepare_refuses_existing_or_untrusted_namespaces(existing):
    original, backend, _ = fixture_bootstrap(prepared=False)
    if existing == "prefix": backend.add(PREFIX, True)
    elif existing == "service": backend.service = {"unrelated": True}
    elif existing in {"reparse_source", "hardlink_source", "source_bytes"}:
        row = backend.tree[ntpath.join(SOURCE, "python312", "tools", "python.exe")]
        if existing == "reparse_source": row["reparse"] = True
        elif existing == "hardlink_source": row["links"] = 2
        else: row["data"] += b"changed"
    else: backend.tree[r"C:\Protected"]["sd"] = raw_sd(True, broad=True)
    with pytest.raises(b.BootstrapError):
        original.prepare()
    assert not any(c[0] in {"CreateDirectoryW", "CreateServiceW", "DeleteService"} for c in backend.calls)


@pytest.mark.parametrize("condition", ["no_approval", "non_admin", "cannot_assign_system", "source_changed", "namespace_race"])
def test_apply_preconditions_have_no_write_fallback(condition):
    original, backend, _ = fixture_bootstrap()
    approved = original.intent.sha256
    if condition == "no_approval": approved = "0" * 64
    elif condition == "non_admin": backend.admin = False
    elif condition == "cannot_assign_system": backend.system_owner = False
    elif condition == "source_changed":
        backend.tree[ntpath.join(SOURCE, "jdk11", "bin", "java.exe")]["data"] += b"drift"
    elif condition == "namespace_race": backend.add(PREFIX, True)
    before = list(backend.calls)
    with pytest.raises(b.BootstrapError):
        original.apply(approved_plan_sha256=approved)
    assert not any(c[0] in {"CreateDirectoryW", "CreateServiceW"} for c in backend.calls[len(before):])


def test_full_original_owner_install_compare_restore_preserves_unrelated_and_archives():
    original, backend, _ = install()
    assert original.state == "INSTALLED_STOPPED_START_BLOCKED"
    assert backend.service["privileges"] == tuple(sorted(b.PRIVILEGES))
    assert backend.service["sid_type"] == 3
    assert backend.service["failure_actions"] == (0, "", "", ())
    assert backend.service["failure_flag"] is False
    assert backend.service["account"] == "LocalSystem" and backend.service["start_type"] == 3
    assert original.service.opened.name == "CreateServiceW"
    for node in original.nodes.values():
        assert node.image.identity[1] > 0 and isinstance(node.image.raw_sd, bytes)
        b.check_security(node.image.raw_sd, directory=node.image.directory, service_sid=SERVICE_SID)
    assert not any("StartService" in c[0] or "ControlService" in c[0] for c in backend.calls)
    assert original.restore()["state"] == "RESTORED"
    assert backend.tree == backend.original and backend.service is None


@pytest.mark.parametrize("drift", ["extra", "bytes", "identity", "raw_sd", "config", "service_raw_sd", "live", "unknown_children", "dependents", "source_archive"])
def test_restore_compares_fresh_original_postimages_before_any_delete(drift):
    original, backend, _ = install()
    file_path = original.intent.destination("jdk11/bin/java.exe")
    if drift == "extra": backend.add(PREFIX + r"\unexpected-user.txt", False, b"preserve me")
    elif drift == "bytes": backend.tree[file_path]["data"] += b"drift"
    elif drift == "identity": backend.tree[file_path]["identity"] = (7, 999999)
    elif drift == "raw_sd": backend.tree[file_path]["sd"] = raw_sd(broad=True)
    elif drift == "config": backend.service["binary"] += " unexpected"
    elif drift == "service_raw_sd": backend.service["raw_sd"] = raw_sd(service=True, broad=True)
    elif drift == "live": backend.service["state"], backend.service["pid"] = 4, 54321
    elif drift == "dependents": backend.service["dependents"] = (("unrelated-dependent", "preserve", ()),)
    elif drift == "source_archive": backend.tree[ntpath.join(SOURCE, "materials", "offline.zip")]["data"] += b"changed"
    else: original.children_state = "UNKNOWN"
    before = len(backend.calls)
    with pytest.raises(b.BootstrapError):
        original.restore()
    assert original.state == "UNKNOWN"
    assert not any(c[0] in {"DeleteService", "SetFileInformationByHandle"} for c in backend.calls[before:])
    assert PREFIX in backend.tree


@pytest.mark.parametrize("stage", ["CreateServiceW_known_before_write", "config_known_before_write"])
def test_explicit_known_failure_allows_original_compare_and_rollback(stage):
    original, backend, _ = fixture_bootstrap()
    backend.faults[stage] = True
    with pytest.raises(b.BootstrapError):
        original.apply(approved_plan_sha256=original.intent.sha256)
    assert original.state == "PARTIAL_KNOWN"
    assert PREFIX in backend.tree  # no automatic rollback
    original.restore()
    assert backend.tree == backend.original and backend.service is None


@pytest.mark.parametrize("stage", ["CreateDirectoryW", "CreateFileW", "WriteFile", "OpenSCManagerW", "CreateServiceW", "ChangeServiceConfig2W", "SetServiceObjectSecurity", "SetSecurityInfo"])
@pytest.mark.parametrize("fault", ["raise", "invalid"])
def test_sdk_write_then_exception_or_invalid_result_retains_unknown_without_replay(stage, fault):
    original, backend, _ = fixture_bootstrap()
    backend.faults[stage] = fault
    with pytest.raises(b.BootstrapError) as raised:
        original.apply(approved_plan_sha256=original.intent.sha256)
    assert raised.value.owner is original and original.state == "UNKNOWN"
    before = list(backend.calls)
    with pytest.raises(b.BootstrapError):
        original.apply(approved_plan_sha256=original.intent.sha256)
    with pytest.raises(b.BootstrapError):
        original.restore()
    with pytest.raises(b.BootstrapError):
        original.owner.close_all()
    assert backend.calls == before
    assert original in b.retained_bootstrap_owners()
    assert any(c.state == "UNKNOWN" for c in original.owner.calls)
    if stage == "CreateServiceW":
        pending = next(h for h in original.owner.handles if h.opened.name == stage)
        assert pending.state == "UNKNOWN" and pending.parent is not None
        assert not pending.parent.closed


@pytest.mark.parametrize("target", ["closer", "value", "argument"])
def test_original_closer_handle_and_storage_drift_never_dispatches_alternatives(target):
    original, backend, _ = fixture_bootstrap()
    handle = original.manager
    before = list(backend.calls)
    if target == "closer":
        backend.sdk["CloseServiceHandle"] = lambda arg: pytest.fail("replacement closer called")
    elif target == "value": handle.value += 1000
    else: handle.argument.value += 1000
    with pytest.raises(b.BootstrapError):
        handle.close()
    assert backend.calls == before


def test_confirmed_close_result_is_read_once_and_cached_across_bookkeeping_error(monkeypatch):
    original, backend, _ = fixture_bootstrap()
    handle = original.manager
    result = Once(1)
    backend.close_values[handle.value] = result
    checkpoint = original.owner.checkpoint
    raised = []
    def fault(event, resource):
        if resource is handle and not raised:
            raised.append(True)
            raise RuntimeError("SOFTWARE_ONLY bookkeeping failure after SDK success")
        return checkpoint(event, resource)
    monkeypatch.setattr(original.owner, "checkpoint", fault)
    with pytest.raises(RuntimeError):
        handle.close()
    assert handle.close_call.state == "SUCCEEDED" and not handle.closed
    handle.close()
    assert handle.closed and result.reads == 1
    assert len([c for c in backend.calls if c == ("close", handle.value)]) == 1


def test_uncertain_close_never_explicitly_repeats_or_closes_parent():
    original, backend, _ = fixture_bootstrap()
    manager = backend.manager(True)
    child = backend.owner.acquire("CreateServiceW",
        (manager.arg(), "ComsolMCP_test", "ComsolMCP_test", 0xF0007, 16, 3, 1, "exact",
         None, None, None, "LocalSystem", None), "CloseServiceHandle", manager)
    backend.faults["close"] = "raise"
    with pytest.raises(OSError):
        child.close()
    calls = list(backend.calls)
    with pytest.raises(b.BootstrapError):
        child.close(retry_known=True)
    with pytest.raises(b.BootstrapError):
        manager.close()
    assert backend.calls == calls


def test_scm_and_file_ctypes_layouts_are_windows_fixed_width():
    assert C.sizeof(b._QueryConfig) == 64
    assert b._QueryConfig.binary.offset == 16 and b._QueryConfig.dependencies.offset == 40
    assert C.sizeof(b._FailureActions) == 40 and b._FailureActions.actions.offset == 32
    assert C.sizeof(b._ServiceStatus) == 36
    assert C.sizeof(b._FileInfo) == 52 and C.sizeof(b._FindData) == 592
    assert C.sizeof(b._SecurityAttributes) == 24
    assert C.sizeof(b._PrivilegesInfo) == 8 and C.sizeof(b._SidInfo) == 4
    assert C.sizeof(b._Disposition) == 1 and C.sizeof(b._Dependent) == 48


def native_file_fixture(*, directory=False, drift=None, output_fault=None):
    """Real _Windows image/open code, synthetic SDK storage and sharing only."""
    model = FakeWindows({})
    path = ntpath.join(SOURCE, "image-fixture" if directory else "image-fixture.bin")
    model.add(path, directory, b"SOFTWARE_ONLY actual read-buffer bytes")
    identities, counts, seen, positions = {}, {}, [], {}
    original_open = model.sdk["CreateFileW"]

    def opening(*args):
        result = original_open(*args)
        if result in model.handles:
            identities[result] = model.tree[args[0]]["identity"]
        return result

    def information(arg, output):
        seen.append(("legacy", arg.value))
        row = model.tree[model.target(arg)]
        value = C.cast(output, C.POINTER(b._FileInfo)).contents
        value.attributes, value.links = (0x10 if directory else 0x80), 1
        value.size_low = 0 if directory else len(row["data"])
        value.volume = identities[arg.value][0] & 0xFFFFFFFF
        value.index_high = (identities[arg.value][1] >> 32) & 0xFFFFFFFF
        value.index_low = identities[arg.value][1] & 0xFFFFFFFF
        value.creation.low, value.write.low = 10, 20
        return 1

    def full_information(arg, level, output, size):
        assert level == 18 and size == 24
        counts[arg.value] = counts.get(arg.value, 0) + 1
        seen.append(("full", arg.value, level, size, C.cast(output, C.c_void_p).value))
        value = C.cast(output, C.POINTER(b._FileIdInfo)).contents
        volume, file_id = identities[arg.value]
        if counts[arg.value] == 2:
            if drift == "volume": volume ^= 1 << 55
            elif drift == "file_id": file_id ^= 1 << 120
        value.volume = volume
        value.file_id[:] = file_id.to_bytes(16, "little")
        if output_fault and counts[arg.value] == output_fault[0]:
            if output_fault[1] == "raise":
                raise OSError("SOFTWARE_ONLY FileIdInfo buffer written then raised")
            if output_fault[1] == "invalid": return "invalid SDK result"
            model.error = output_fault[1]
            return 0
        return 1

    def final_path(arg, buffer, capacity, flags):
        assert flags == 0 and capacity > len(path) + 4
        buffer.value = "\\\\?\\" + model.target(arg)
        return len(buffer.value)

    def seek(arg, distance, new_position, method):
        assert distance == 0 and new_position is None and method == 0
        positions[arg.value] = 0
        return 1

    def read(arg, buffer, capacity, output, overlapped):
        data = model.tree[model.target(arg)]["data"]
        block = data[positions[arg.value]:positions[arg.value] + capacity]
        C.memmove(buffer, block, len(block))
        C.cast(output, C.POINTER(b.DWORD))[0] = b.DWORD(len(block))
        positions[arg.value] += len(block)
        assert overlapped is None
        return 1

    def security(arg, flags, buffer, capacity, output):
        raw = model.tree[model.target(arg)]["sd"]
        assert flags == 7 and capacity >= len(raw)
        C.memmove(buffer, raw, len(raw))
        C.cast(output, C.POINTER(b.DWORD))[0] = b.DWORD(len(raw))
        return 1

    native = b._Windows._test({
        "CreateFileW": opening, "CloseHandle": model._close,
        "GetFileInformationByHandle": information, "GetFileInformationByHandleEx": full_information,
        "GetFinalPathNameByHandleW": final_path, "SetFilePointerEx": seek,
        "ReadFile": read, "GetKernelObjectSecurity": security,
    }, model.last_error, seal=b._TEST_SEAL)
    return native, model, path, seen


@pytest.mark.parametrize("directory", [False, True])
def test_native_fresh_compare_shares_read_write_delete_and_keeps_original_owner(directory):
    native, model, path, seen = native_file_fixture(directory=directory)
    original = native._open(path, directory, write=True)
    original_value, original_arg = original.value, original.argument
    assert model.file_opens[original_value][1] & 0x10000
    with pytest.raises(b.KnownSdkFailure) as raised:
        native._open(path, directory, share=3)
    assert raised.value.winerror == 32  # negative sharing control; no real Windows calls
    control = native._open(path, directory, share=7)
    control.close()
    expected = native.image(original, path, directory)
    actual = native.fresh_image(path, directory)
    assert actual == expected and actual.identity == model.tree[path]["identity"]
    assert original.value == original_value and original.argument is original_arg and not original.closed
    assert model.handles[original_value][2] is False
    assert model.calls[-2][0] == "CreateFileW" and model.calls[-2][3] == 7
    full_calls = [c for c in native.owner.calls if c.name == "GetFileInformationByHandleEx"]
    assert len(full_calls) == 4 and all(len(c.outputs) == 1 for c in full_calls)
    for call in full_calls:
        assert call.outputs[0] is call.args[2]._obj and C.sizeof(call.outputs[0]) == 24
    assert [s[1] for s in seen if s[0] == "full"][:2] == [original_value, original_value]
    native.owner.close_all()
    assert not model.file_opens


def test_file_id_info_abi_preserves_all_64_volume_and_128_id_bits():
    assert C.sizeof(b._FileIdInfo) == 24 and C.alignment(b._FileIdInfo) == 8
    assert b._FileIdInfo.volume.offset == 0 and b._FileIdInfo.file_id.offset == 8
    row = b._FileIdInfo()
    row.volume = 0xFEDCBA9876543210
    row.file_id[:] = bytes.fromhex("ffeeddccbbaa99887766554433221100")
    assert bytes(row) == struct.pack("<Q", 0xFEDCBA9876543210) + bytes(row.file_id)


@pytest.mark.parametrize("field", ["volume", "file_id"])
def test_native_image_rejects_high_bits_drift_on_original_handle(field):
    native, model, path, seen = native_file_fixture(drift=field)
    handle = native.open_node(path, False)
    with pytest.raises(b.BootstrapError) as raised:
        native.image(handle, path, False)
    assert raised.value.code == "FILE_DRIFT"
    assert [s[1] for s in seen if s[0] == "full"] == [handle.value, handle.value]
    assert not handle.closed
    native.owner.close_all()


@pytest.mark.parametrize("field", ["volume", "file_id"])
def test_native_fresh_mapping_cannot_alias_original_using_only_legacy_low_bits(field):
    native, model, path, _ = native_file_fixture()
    handle = native._open(path, False, write=True)
    original = native.image(handle, path, False)
    volume, file_id = model.tree[path]["identity"]
    model.tree[path]["identity"] = (volume ^ (1 << 55), file_id) if field == "volume" else (volume, file_id ^ (1 << 120))
    fresh = native.fresh_image(path, False)
    assert fresh != original and fresh.identity != original.identity
    assert not handle.closed and handle.value in model.file_opens
    assert native.image(handle, path, False) == original
    native.owner.close_all()


@pytest.mark.parametrize("when,error", [(1, 50), (2, 87)])
def test_native_file_id_unsupported_explicitly_refuses_without_legacy_fallback(when, error):
    native, model, path, seen = native_file_fixture(output_fault=(when, error))
    handle = native.open_node(path, False)
    with pytest.raises(b.BootstrapError) as raised:
        native.image(handle, path, False)
    assert raised.value.code == "FILE_ID_CAPABILITY"
    assert isinstance(raised.value.__cause__, b.KnownSdkFailure)
    assert raised.value.__cause__.winerror == error
    assert len([s for s in seen if s[0] == "full"]) == when
    assert not handle.closed
    native.owner.close_all()


@pytest.mark.parametrize("fault", ["raise", "invalid"])
def test_native_file_id_output_uncertainty_retains_storage_and_blocks_query_or_close(fault):
    native, model, path, seen = native_file_fixture(output_fault=(1, fault))
    handle = native.open_node(path, False)
    with pytest.raises((OSError, b.BootstrapError)):
        native.image(handle, path, False)
    call = native.owner.calls[-1]
    assert call.name == "GetFileInformationByHandleEx" and call.state == "UNKNOWN"
    assert call.outputs[0] is call.args[2]._obj
    assert call.outputs[0].volume == model.tree[path]["identity"][0]
    previous_calls = list(model.calls), list(seen)
    with pytest.raises(b.BootstrapError): native.fresh_image(path, False)
    with pytest.raises(b.BootstrapError): handle.close(retry_known=True)
    assert (model.calls, seen) == previous_calls and not handle.closed


def test_fake_full_install_compare_restore_checks_real_open_sharing_contract():
    original, backend, _ = fixture_bootstrap()
    original.apply(approved_plan_sha256=original.intent.sha256)
    installed = tuple(original.nodes.values())
    for node in installed:
        assert backend.file_opens[node.handle.value][1] & 0x10000
        assert node.image.identity[0] > 0xFFFFFFFF and node.image.identity[1] > 0xFFFFFFFFFFFFFFFF
        assert backend.fresh_image(node.path, node.image.directory) == node.image
        assert not node.handle.closed
    original.restore()
    assert backend.tree == backend.original and backend.service is None and not backend.file_opens


@pytest.mark.parametrize("field", ["volume", "file_id"])
def test_full_restore_refuses_high_bits_only_identity_drift_before_any_delete(field):
    original, backend, _ = fixture_bootstrap()
    original.apply(approved_plan_sha256=original.intent.sha256)
    node = next(n for n in original.nodes.values() if not n.image.directory)
    volume, file_id = node.image.identity
    backend.tree[node.path]["identity"] = (volume ^ (1 << 55), file_id) if field == "volume" else (volume, file_id ^ (1 << 120))
    before = list(backend.calls)
    with pytest.raises(b.BootstrapError): original.restore()
    assert original.state == "UNKNOWN" and not node.handle.closed
    assert not any(c[0] in {"DeleteService", "SetFileInformationByHandle"} for c in backend.calls[len(before):])


@pytest.mark.parametrize("directory", [False, True])
def test_native_bridge_runtime_restore_acquisitions_obey_real_bidirectional_sharing(directory):
    native, model, path, _ = native_file_fixture(directory=directory)
    writer = native._open(path, directory, write=True)
    expected = native.image(writer, path, directory)
    bridge = native.bridge_node(path, directory)
    with pytest.raises(b.KnownSdkFailure) as raised:
        native.runtime_node(path, directory)
    assert raised.value.winerror == 32  # runtime no-WRITE sharing must reject the still-open writer
    writer.close()
    assert native.image(bridge, path, directory) == expected
    runtime = native.runtime_node(path, directory)
    assert runtime.opened.args[1] == (0x20080 if directory else 0x80020000)
    assert runtime.opened.args[2] == 5 and not runtime.opened.args[1] & 0x400D0006
    bridge.close()
    assert native.image(runtime, path, directory) == expected
    with pytest.raises(b.KnownSdkFailure) as raised:
        native._open(path, directory, write=True, share=7)
    assert raised.value.winerror == 32
    delete = native.restore_node(path, directory)
    assert delete is not writer and delete.opened is not writer.opened
    assert delete.opened.args[1] == runtime.opened.args[1] | 0x10000
    assert not delete.opened.args[1] & 0x400C0006  # no write-data/DAC/owner on the new DELETE pin
    assert native.image(delete, path, directory) == expected
    runtime.close()
    assert native.image(delete, path, directory) == expected
    native.owner.close_all()
    assert not model.file_opens


def test_full_original_install_handoff_and_restore_preserves_each_original_handle_and_archives():
    original, backend, _ = install()
    writers = {name: node.handle for name, node in original.nodes.items()}
    images = {name: node.image for name, node in original.nodes.items()}
    before = len(backend.calls)
    result = original.handoff_runtime()
    assert result["static_runtime_read_pins"] and result["native_start"].startswith("BLOCKED_")
    assert result["apply_ready_package_claim"] is False and result["authority"] == "LOCAL_INSTALLER_ONLY_NOT_T037"
    assert original.state == "RUNTIME_READ_PINNED_START_BLOCKED"
    for name, node in original.nodes.items():
        assert node.handle is writers[name] and node.image == images[name]
        assert node.handle.closed and node.bridge_handle.closed and not node.runtime_handle.closed
        assert node.runtime_handle.opened is not node.handle.opened
        assert node.runtime_handle.opened.args[2] == 5
    for handle, (path, access, share) in backend.file_opens.items():
        if path == PREFIX or path.startswith(PREFIX + "\\"):
            assert not access & 0x400D0006 and share == 5
    assert not any(c[0] in {"WriteFile", "SetSecurityInfo", "ChangeServiceConfig2W", "DeleteService"} for c in backend.calls[before:])
    pins = [node.runtime_handle for node in original.nodes.values()]
    assert original.restore()["state"] == "RESTORED"
    assert backend.tree == backend.original and backend.service is None and not backend.file_opens
    assert all(h.closed for h in pins)
    for node in original.nodes.values():
        assert node.handle is writers[node.relative]
        assert node.delete_handle is not node.handle and node.delete_handle is not node.runtime_handle
        assert node.delete_handle.closed and node.delete_handle.opened.args[1] & 0x10000
        assert node.delete_handle.opened.args[4] == 3


def test_handoff_is_once_original_state_only_and_children_cannot_be_reset_by_flag():
    original, backend, _ = fixture_bootstrap()
    with pytest.raises(b.BootstrapError): original.handoff_runtime()
    original.apply(approved_plan_sha256=original.intent.sha256)
    original.handoff_runtime()
    before = list(backend.calls)
    with pytest.raises(b.BootstrapError): original.handoff_runtime()
    original.children_state = "UNKNOWN"
    with pytest.raises(b.BootstrapError) as raised:
        original.children_state = "NEVER_STARTED"
    assert raised.value.code == "CHILDREN_UNPROVEN" and original.children_state == "UNKNOWN"
    with pytest.raises(b.BootstrapError): original.restore()
    assert backend.calls == before and original.state == "UNKNOWN"


def test_child_state_diagnostic_rejects_caller_wrapper_without_quiescence_authority():
    original, backend, _ = install()
    class PretendStopped:
        def __eq__(self, other): return True
        def __ne__(self, other): return False
    before = list(backend.calls)
    with pytest.raises(b.BootstrapError): original.children_state = PretendStopped()
    original.children_state = "LIVE"
    with pytest.raises(b.BootstrapError): original.children_state = "NEVER_STARTED"
    with pytest.raises(b.BootstrapError): original.handoff_runtime()
    assert original.children_state == "LIVE" and original.state == "UNKNOWN" and backend.calls == before


@pytest.mark.parametrize("drift", ["identity", "bytes", "sd", "extra", "live", "children"])
@pytest.mark.parametrize("after_handoff", [False, True])
def test_handoff_and_restore_refuse_current_static_namespace_or_quiescence_drift(drift, after_handoff):
    original, backend, _ = install()
    if after_handoff: original.handoff_runtime()
    node = next(n for n in original.nodes.values() if not n.image.directory)
    if drift == "identity":
        volume, fid = backend.tree[node.path]["identity"]
        backend.tree[node.path]["identity"] = (volume, fid ^ (1 << 120))
    elif drift == "bytes": backend.tree[node.path]["data"] += b"changed"
    elif drift == "sd": backend.tree[node.path]["sd"] = raw_sd(broad=True)
    elif drift == "extra": backend.add(PREFIX + r"\unexpected-user-data", False, b"preserve")
    elif drift == "live": backend.service.update(state=4, pid=12345)
    else: original.children_state = "UNKNOWN"
    before = list(backend.calls)
    with pytest.raises(b.BootstrapError):
        original.restore() if after_handoff else original.handoff_runtime()
    assert original.state == "UNKNOWN" and original in b.retained_bootstrap_owners()
    assert not any(c[0] in {"DeleteService", "SetFileInformationByHandle", "WriteFile"} for c in backend.calls[len(before):])
    if not after_handoff: assert not any(n.handle.closed for n in original.nodes.values())


def handoff_with_close_hook():
    original, backend, _ = fixture_bootstrap(prepared=False)
    hook, sdk_close = {}, backend.sdk["CloseHandle"]
    def closing(arg):
        result = sdk_close(arg)
        if hook.get("close"):
            hook["close"](arg)
        return result
    backend.sdk["CloseHandle"] = closing  # fixed before any original writer acquisition
    original.prepare()
    original.apply(approved_plan_sha256=original.intent.sha256)
    return original, backend, hook


@pytest.mark.parametrize("point", ["bridge_acquired", "writer_closed", "runtime_acquired", "bridge_closed"])
def test_identity_drift_at_each_transition_boundary_retains_original_without_advancing(point, monkeypatch):
    original, backend, hook = handoff_with_close_hook()
    node = next(iter(original.nodes.values()))
    def drift():
        volume, fid = backend.tree[node.path]["identity"]
        backend.tree[node.path]["identity"] = (volume ^ (1 << 55), fid)
    if point in {"bridge_acquired", "runtime_acquired"}:
        name = "bridge_node" if point == "bridge_acquired" else "runtime_node"
        opening = getattr(backend, name)
        def changed(path, directory):
            handle = opening(path, directory)
            if path == node.path: drift()
            return handle
        monkeypatch.setattr(backend, name, changed)
    else:
        def after_close(arg):
            target = node.handle if point == "writer_closed" else node.bridge_handle
            if target is not None and arg.value == target.original_value: drift()
        hook["close"] = after_close
    with pytest.raises(b.BootstrapError): original.handoff_runtime()
    assert original.state == "UNKNOWN"
    if point == "bridge_acquired": assert not node.handle.closed
    before = list(backend.calls)
    with pytest.raises(b.BootstrapError): original.handoff_runtime()
    with pytest.raises(b.BootstrapError): original.restore()
    with pytest.raises(b.BootstrapError): original.owner.close_all()
    assert backend.calls == before
    if point in {"writer_closed", "bridge_acquired"}: assert node.runtime_handle is None


@pytest.mark.parametrize("phase", ["bridge", "runtime"])
@pytest.mark.parametrize("fault", ["known", "raise", "invalid"])
def test_new_pin_native_output_failures_are_never_adopted_or_retried_and_known_can_restore(phase, fault, monkeypatch):
    original, backend, _ = install()
    node = next(iter(original.nodes.values()))
    name = phase + "_node"
    opening, sdk_open = getattr(backend, name), backend.sdk["CreateFileW"]
    active = [False]
    def sdk(*args):
        if active[0]:
            active[0] = False
            if fault == "known":
                backend.error = 5
                return C.c_void_p(-1).value
            value = sdk_open(*args)
            if fault == "raise": raise OSError("SOFTWARE_ONLY native pin acquired then result lost")
            return "invalid returned HANDLE"
        return sdk_open(*args)
    backend.sdk["CreateFileW"] = sdk
    def chosen(path, directory):
        if path == node.path: active[0] = True
        return opening(path, directory)
    monkeypatch.setattr(backend, name, chosen)
    with pytest.raises(b.BootstrapError): original.handoff_runtime()
    before = list(backend.calls)
    with pytest.raises(b.BootstrapError): original.handoff_runtime()
    assert backend.calls == before
    if fault == "known":
        assert original.state == "PARTIAL_KNOWN"
        original.restore()
        assert backend.tree == backend.original and backend.service is None
    else:
        assert original.state == "UNKNOWN"
        pending = [h for h in original.owner.handles if h.state == "UNKNOWN"]
        assert len(pending) == 1 and pending[0].value is None
        with pytest.raises(b.BootstrapError): original.restore()
        assert backend.calls == before


@pytest.mark.parametrize("fault", ["raise", "invalid", "known", "confirmed_bookkeeping"])
def test_original_writer_close_outcomes_cannot_reclose_or_advance_handoff(fault, monkeypatch):
    original, backend, hook = handoff_with_close_hook()
    node = next(iter(original.nodes.values()))
    # Arm only after bridge comparison has closed its temporary fresh read HANDLE.
    image, armed = backend.fresh_image, [False]
    def compared(path, directory):
        result = image(path, directory)
        if path == node.path and node.bridge_handle is not None and not armed[0]:
            armed[0] = True
            if fault == "known": backend.faults["close_known_before_write"] = True
            elif fault in {"raise", "invalid"}: backend.faults["close"] = fault
        return result
    monkeypatch.setattr(backend, "fresh_image", compared)
    if fault == "confirmed_bookkeeping":
        checkpoint = original.owner.checkpoint
        def failed(event, handle):
            if event == "close_confirmed" and handle is node.handle:
                raise RuntimeError("SOFTWARE_ONLY bookkeeping after confirmed original close")
            return checkpoint(event, handle)
        monkeypatch.setattr(original.owner, "checkpoint", failed)
        backend.close_values[node.handle.value] = Once(1)
    with pytest.raises(b.BootstrapError): original.handoff_runtime()
    assert original.state == ("HANDOFF_CLOSE_ACK_PENDING" if fault == "confirmed_bookkeeping" else "UNKNOWN")
    assert node.runtime_handle is None
    assert len([c for c in backend.calls if c == ("close", node.handle.original_value)]) == 1
    before = list(backend.calls)
    with pytest.raises(RuntimeError if fault == "confirmed_bookkeeping" else b.BootstrapError):
        node.handle.close(retry_known=True)
    with pytest.raises(b.BootstrapError): original.handoff_runtime()
    with pytest.raises(b.BootstrapError): original.restore()
    assert backend.calls == before
    if fault == "confirmed_bookkeeping":
        assert node.handle.close_call.state == "SUCCEEDED" and node.handle.close_call.scalar == 1
        assert backend.close_values[node.handle.value].reads == 1
        assert original.owner.uncertain is False
        monkeypatch.setattr(original.owner, "checkpoint", checkpoint)
        node.handle.close(retry_known=True)
        assert backend.calls == before
        assert original.restore()["state"] == "RESTORED"
        assert backend.tree == backend.original and backend.service is None


@pytest.mark.parametrize("mutation", ["wrapper", "argument", "access"])
def test_restore_cannot_promote_rewrapped_or_drifted_runtime_pin(mutation):
    original, backend, _ = install()
    original.handoff_runtime()
    node = next(iter(original.nodes.values()))
    runtime = node.runtime_handle
    if mutation == "wrapper": node.runtime_handle = copy.copy(runtime)
    elif mutation == "argument": runtime.argument.value += 999
    else:
        args = list(runtime.opened.args);args[1] |= 0x10000;runtime.opened.args = tuple(args)
    before = len(backend.calls)
    with pytest.raises(b.BootstrapError): original.restore()
    assert original.state == "UNKNOWN" and not runtime.closed
    assert not any(c[0] in {"DeleteService", "SetFileInformationByHandle"} for c in backend.calls[before:])


@pytest.mark.parametrize("fault", ["raise", "invalid", "known", "drift"])
def test_new_restore_delete_pin_failure_preserves_service_and_all_original_read_pins(fault, monkeypatch):
    original, backend, _ = install()
    original.handoff_runtime()
    node = next(iter(original.nodes.values()))
    opening = backend.restore_node
    def changed(path, directory):
        if path == node.path and fault in {"raise", "invalid"}:
            backend.faults["CreateFileW"] = fault
        if path == node.path and fault == "known":
            backend.error = 5
            # A definitive denied acquisition, no output or retry.
            backend.sdk["CreateFileW"] = lambda *args: C.c_void_p(-1).value
        handle = opening(path, directory)
        if path == node.path and fault == "drift": backend.tree[path]["sd"] = raw_sd(directory, broad=True)
        return handle
    monkeypatch.setattr(backend, "restore_node", changed)
    before = len(backend.calls)
    with pytest.raises(b.BootstrapError): original.restore()
    assert original.state == "UNKNOWN" and backend.service is not None
    assert all(not n.runtime_handle.closed for n in original.nodes.values())
    assert not any(c[0] in {"DeleteService", "SetFileInformationByHandle"} for c in backend.calls[before:])
    count = len(backend.calls)
    with pytest.raises(b.BootstrapError): original.restore()
    assert len(backend.calls) == count


def test_new_native_acquisition_may_reuse_known_closed_integer_but_never_old_owner():
    native, model, path, _ = native_file_fixture()
    writer = native._open(path, False, write=True)
    bridge = native.bridge_node(path, False)
    expected = native.image(writer, path, False)
    writer.close()
    model.next_handle = writer.original_value - 1  # simulate normal OS scalar reuse
    runtime = native.runtime_node(path, False)
    assert runtime.original_value == writer.original_value and runtime is not writer
    assert runtime.opened is not writer.opened and runtime.opened.state == "SUCCEEDED"
    assert writer.closed and native.image(runtime, path, False) == expected
    native.owner.close_all()
    assert runtime.closed and bridge.closed and not model.file_opens


@pytest.mark.parametrize("fault", ["raise", "invalid", "known", "confirmed_bookkeeping"])
def test_restore_read_pin_close_uncertainty_never_deletes_or_repeats_original_closer(fault, monkeypatch):
    original, backend, hook = handoff_with_close_hook()
    original.handoff_runtime()
    node = list(original.nodes.values())[-1]
    target = node.runtime_handle
    if fault == "raise":
        def failed(arg):
            if arg.value == target.original_value:
                raise OSError("SOFTWARE_ONLY original read pin closed but native return lost")
        hook["close"] = failed
    elif fault in {"invalid", "known"}:
        backend.close_values[target.original_value] = "invalid BOOL" if fault == "invalid" else 0
    else:
        backend.close_values[target.original_value] = Once(1)
        checkpoint = original.owner.checkpoint
        def failed(event, handle):
            if handle is target: raise RuntimeError("SOFTWARE_ONLY close result cached before bookkeeping loss")
            return checkpoint(event, handle)
        monkeypatch.setattr(original.owner, "checkpoint", failed)
    before = len(backend.calls)
    with pytest.raises(b.BootstrapError): original.restore()
    assert original.state == ("RESTORE_CLOSE_ACK_PENDING" if fault == "confirmed_bookkeeping" else "UNKNOWN")
    assert backend.service is not None and node.delete_handle is not None
    assert not any(c[0] in {"DeleteService", "SetFileInformationByHandle"} for c in backend.calls[before:])
    assert len([c for c in backend.calls if c == ("close", target.original_value)]) == 1
    calls = list(backend.calls)
    with pytest.raises(RuntimeError if fault == "confirmed_bookkeeping" else b.BootstrapError):
        target.close(retry_known=True)
    with pytest.raises(b.BootstrapError): original.restore()
    assert backend.calls == calls
    if fault == "confirmed_bookkeeping":
        assert target.close_call.state == "SUCCEEDED" and backend.close_values[target.original_value].reads == 1
        assert original.owner.uncertain is False
        deletes = tuple(n.delete_handle for n in original.nodes.values())
        monkeypatch.setattr(original.owner, "checkpoint", checkpoint)
        target.close(retry_known=True)
        assert backend.calls == calls
        assert original.restore()["state"] == "RESTORED"
        assert tuple(n.delete_handle for n in original.nodes.values()) == deletes
        assert backend.tree == backend.original and backend.service is None


def one_pin_close_metadata_fault(original, backend, target, monkeypatch):
    checkpoint, fired = original.owner.checkpoint, []

    def one_fault(event, handle):
        if event == "close_confirmed" and handle is target and not fired:
            fired.append(handle)
            raise OSError("SOFTWARE_ONLY one acknowledgement fault after confirmed SDK close")
        return checkpoint(event, handle)

    result = backend.close_values[target.original_value] = Once(1)
    monkeypatch.setattr(original.owner, "checkpoint", one_fault)
    return fired, result


@pytest.mark.parametrize("phase,role,index", [
    ("handoff", "handle", 0), ("handoff", "bridge_handle", 0), ("handoff", "handle", -1),
    ("restore", "runtime_handle", -1), ("restore", "runtime_handle", 0), ("restore", "runtime_handle", 2),
])
def test_known_pin_close_ack_is_metadata_only_and_same_original_restore_continues(phase, role, index, monkeypatch):
    original, backend, _ = handoff_with_close_hook()
    if phase == "restore": original.handoff_runtime()
    node = tuple(original.nodes.values())[index]
    if role == "bridge_handle":
        opening = backend.bridge_node
        def bridge(path, directory):
            handle = opening(path, directory)
            if path == node.path:
                one_pin_close_metadata_fault(original, backend, handle, monkeypatch)
            return handle
        monkeypatch.setattr(backend, "bridge_node", bridge)
    else:
        one_pin_close_metadata_fault(original, backend, getattr(node, role), monkeypatch)
    with pytest.raises(b.BootstrapError):
        original.restore() if phase == "restore" else original.handoff_runtime()
    target = getattr(node, role)
    assert target.close_call.state == "SUCCEEDED" and target.close_call.scalar == 1
    assert not target.closed and not original.owner.uncertain
    calls, deletes = list(backend.calls), tuple(n.delete_handle for n in original.nodes.values())
    # Refuse all advancement until explicit original cached acknowledgement, without a query.
    with pytest.raises(b.BootstrapError): original.restore()
    with pytest.raises(b.BootstrapError): original.handoff_runtime()
    assert backend.calls == calls
    target.close(retry_known=True)
    assert target.closed and backend.calls == calls
    assert backend.close_values[target.original_value].reads == 1
    assert original.restore()["state"] == "RESTORED"
    if phase == "restore":
        assert all(n.delete_handle is h for n, h in zip(original.nodes.values(), deletes))
        old_delete_opens = [c for c in calls if c[0] == "CreateFileW" and c[2] & 0x10000]
        assert [c for c in backend.calls if c[0] == "CreateFileW" and c[2] & 0x10000] == old_delete_opens
    assert backend.calls.count(("close", target.original_value)) == 1
    assert backend.tree == backend.original and backend.service is None


@pytest.mark.parametrize("drift", ["scalar", "call", "argument", "closer", "phase", "owner_uncertain",
                                  "new_unknown_call", "new_pending_handle", "node_pin"])
def test_known_pin_ack_never_upgrades_cache_phase_owner_or_original_pin_drift(drift, monkeypatch):
    original, backend, _ = handoff_with_close_hook()
    node = next(iter(original.nodes.values()))
    target = node.handle
    one_pin_close_metadata_fault(original, backend, target, monkeypatch)
    with pytest.raises(b.BootstrapError): original.handoff_runtime()
    target.close(retry_known=True)
    if drift == "scalar": target.close_call.scalar = 2
    elif drift == "call": target.close_call = copy.copy(target.close_call)
    elif drift == "argument": target.argument.value += 1000
    elif drift == "closer": backend.sdk["CloseHandle"] = lambda handle: 1
    elif drift == "phase": original._restore_phase = "DESTRUCTIVE"
    elif drift == "owner_uncertain": original.owner.uncertain = True
    elif drift == "new_unknown_call":
        call = b._Call(original.owner, "unreturned_fixture_call", lambda: 1, (), "BOOL")
        call.state = "UNKNOWN"
    elif drift == "new_pending_handle":
        b._Handle(original.owner, target.opened, "CloseHandle", target.closer)
    else: node.bridge_handle = copy.copy(node.bridge_handle)
    calls = list(backend.calls)
    with pytest.raises(b.BootstrapError): original.restore()
    assert backend.calls == calls and backend.service is not None
    if drift == "owner_uncertain": assert original.owner.uncertain is True


@pytest.mark.parametrize("point", ["service", "file"])
def test_confirmed_close_metadata_fault_after_destructive_start_keeps_unknown(point, monkeypatch):
    original, backend, _ = handoff_with_close_hook()
    original.handoff_runtime()
    checkpoint, fired = original.owner.checkpoint, []

    def one_fault(event, handle):
        target = handle is original.service if point == "service" else any(
            handle is node.delete_handle for node in original.nodes.values())
        if event == "close_confirmed" and target and not fired:
            fired.append(handle)
            raise OSError("SOFTWARE_ONLY acknowledgement fault after destructive phase")
        return checkpoint(event, handle)

    monkeypatch.setattr(original.owner, "checkpoint", one_fault)
    with pytest.raises(b.BootstrapError): original.restore()
    assert original.state == "UNKNOWN" and original.owner.uncertain is True
    assert len(fired) == 1 and fired[0].close_call.state == "SUCCEEDED"
    assert original._restore_phase == "DESTRUCTIVE"
    calls = list(backend.calls)
    with pytest.raises(b.BootstrapError): fired[0].close(retry_known=True)
    with pytest.raises(b.BootstrapError): original.restore()
    assert backend.calls == calls


def test_bounded_scm_pointer_decoder_requires_original_buffer_and_termination():
    raw = "a\0b\0\0".encode("utf-16-le")
    buffer = C.create_string_buffer(raw, len(raw))
    assert b._bounded_string(buffer, len(raw), C.addressof(buffer), multi=True) == ("a", "b")
    for pointer, used in ((C.addressof(buffer)-2, len(raw)), (C.addressof(buffer)+1, len(raw)),
                          (C.addressof(buffer), 2), (C.addressof(buffer), len(raw)+2)):
        with pytest.raises(b.BootstrapError):
            b._bounded_string(buffer, used, pointer, multi=True)


@pytest.mark.parametrize("mutation", ["owner", "broad", "unprotected", "offset", "ace_size", "sid_count", "not_relative"])
def test_binary_sd_not_sddl_text_and_closed_acl_semantics(mutation):
    raw = bytearray(raw_sd(True))
    if mutation == "owner": raw = bytearray(raw_sd(True, owner=b.BA))
    elif mutation == "broad": raw = bytearray(raw_sd(True, broad=True))
    elif mutation == "unprotected": raw[3] &= ~0x10
    elif mutation == "offset": struct.pack_into("<I", raw, 4, len(raw)+1)
    elif mutation == "ace_size":
        acl = struct.unpack_from("<I", raw, 16)[0]
        struct.pack_into("<H", raw, acl+10, 0xFFFF)
    elif mutation == "sid_count": raw[21] = 16
    else: raw[3] &= ~0x80
    with pytest.raises(b.BootstrapError):
        b.check_security(bytes(raw), directory=True)


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/drive", "same", "symlink"])
def test_archive_members_are_finite_closed_and_never_extracted(name):
    if name == "same":
        raw = archive([("same", b"a"), ("SAME", b"b")])
    elif name == "symlink":
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as z:
            info = zipfile.ZipInfo("link")
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            z.writestr(info, "target")
        raw = stream.getvalue()
    else:
        raw = archive([(name, b"fixture")])
    with pytest.raises(b.BootstrapError):
        b._zip_inventory(raw)


def test_python_normal_image_rejects_venv_or_indirect_launcher():
    b._normal_python_pe(python_pe())
    with pytest.raises(b.BootstrapError):
        b._normal_python_pe(python_pe(b"kernel32.dll"))


@pytest.mark.parametrize("outcome", ["raise", "invalid_size"])
def test_sdk_sd_output_is_retained_before_call_and_never_freed_after_ambiguous_transfer(outcome):
    raw = raw_sd(True)
    storage = C.create_string_buffer(raw, len(raw))
    freed = []
    def convert(text, revision, out, size):
        C.cast(out, C.POINTER(C.c_void_p))[0] = C.c_void_p(C.addressof(storage))
        C.cast(size, C.POINTER(b.DWORD))[0] = b.DWORD(len(raw) if outcome == "raise" else b.MAX_SD+1)
        if outcome == "raise":
            raise OSError("SOFTWARE_ONLY wrote real output slot then raised")
        return 1
    native = b._Windows._test({
        "ConvertStringSecurityDescriptorToSecurityDescriptorW": convert,
        "LocalFree": lambda value: freed.append(value) or None,
    }, lambda: 5, seal=b._TEST_SEAL)
    with pytest.raises((b.BootstrapError, OSError)):
        native.sd(True)
    call = native.owner.calls[0]
    assert call.state == "UNKNOWN"
    assert call.outputs[0].value == C.addressof(storage) and freed == []
    with pytest.raises(b.BootstrapError):
        native.owner.close_all()
    assert freed == []


def test_native_disposition_uses_one_byte_sdk_BOOLEAN_not_BOOL():
    calls = []
    def disposition(handle, level, storage, size):
        calls.append((level, size, C.string_at(storage, size)))
        return 1
    native = b._Windows._test({"open": lambda: 123, "CloseHandle": lambda h: 1,
                              "SetFileInformationByHandle": disposition},
                             lambda: 0, seal=b._TEST_SEAL)
    handle = native.owner.acquire("open", (), "CloseHandle")
    native.delete_node(handle)
    assert calls == [(4, 1, b"\x01")]


def test_native_no_restart_really_deletes_actions_and_commands_not_NULL_no_change():
    calls = []
    def config(handle, level, pointer):
        actions = C.cast(pointer, C.POINTER(b._FailureActions)).contents
        calls.append((level, actions.count, bool(actions.actions),
                      bool(actions.reboot), bool(actions.command)))
        return 1
    native = b._Windows._test({"open": lambda: 123, "CloseServiceHandle": lambda h: 1,
                              "ChangeServiceConfig2W": config},
                             lambda: 0, seal=b._TEST_SEAL)
    handle = native.owner.acquire("open", (), "CloseServiceHandle")
    native.configure(handle, "failure_actions", (0, "", "", ()))
    assert calls == [(2, 0, True, True, True)]


def test_prepared_plan_object_cannot_be_replaced_by_equal_caller_dataclass():
    original, backend, _ = fixture_bootstrap()
    original.intent = copy.deepcopy(original.intent)
    calls = list(backend.calls)
    with pytest.raises(b.BootstrapError):
        original.apply(approved_plan_sha256=original.intent.sha256)
    assert backend.calls == calls


def test_failed_bool_close_does_not_prove_original_handle_still_open_for_retry():
    original, backend, _ = fixture_bootstrap()
    handle = original.manager
    backend.faults["close_known_before_write"] = True
    with pytest.raises(b.KnownSdkFailure):
        handle.close()
    assert handle.close_call.state == "KNOWN_FAILURE" and not handle.closed
    before = len(backend.calls)
    with pytest.raises(b.BootstrapError):
        handle.close()
    assert len(backend.calls) == before
    with pytest.raises(b.BootstrapError):
        handle.close(retry_known=True)
    assert not handle.closed and len(backend.calls) == before


def test_localfree_documented_original_allocation_failure_allows_explicit_original_retry():
    result, freed = [77], []
    def free(arg):
        freed.append(arg.value)
        return result[0]
    native = b._Windows._test({"open": lambda: 77, "LocalFree": free}, lambda: 5, seal=b._TEST_SEAL)
    handle = native.owner.acquire("open", (), "LocalFree")
    with pytest.raises(b.KnownSdkFailure):
        handle.close()
    result[0] = 0
    with pytest.raises(b.BootstrapError):
        handle.close()
    assert freed == [77]
    handle.close(retry_known=True)
    assert freed == [77, 77] and handle.closed


def test_closer_is_captured_before_sdk_acquisition_even_if_sdk_changes_binding():
    backend = FakeWindows(intent_doc()[1])
    original_close = backend.sdk["CloseServiceHandle"]
    def opening():
        backend.sdk["CloseServiceHandle"] = lambda arg: pytest.fail("replacement closer used")
        return backend.handle("manager", 1)
    backend.sdk["open"] = opening
    handle = backend.owner.acquire("open", (), "CloseServiceHandle")
    assert b._binding(handle.closer) == b._binding(original_close)
    with pytest.raises(b.BootstrapError):
        handle.close()
    backend.sdk["CloseServiceHandle"] = original_close
    handle.close()
    assert handle.closed


def test_unknown_original_output_blocks_even_a_new_public_readonly_query():
    original, backend, doc = fixture_bootstrap()
    backend.faults["CreateServiceW"] = "raise"
    with pytest.raises(b.BootstrapError):
        original.apply(approved_plan_sha256=original.intent.sha256)
    calls = list(backend.calls)
    with pytest.raises(b.BootstrapError) as raised:
        b.prepare(encode(doc))
    assert raised.value.code == "ORIGINAL_OWNER_RETAINED" and backend.calls == calls


def native_scm_fixture(*, bad_pointer=None):
    """Exercise real backend copy-out/parser with actual ctypes buffers."""
    seen = []
    def needed(out, value):
        C.cast(out, C.POINTER(b.DWORD))[0] = b.DWORD(value)
    def put(buffer, offset, text):
        raw = text.encode("utf-16-le")
        C.memmove(C.addressof(buffer) + offset, raw, len(raw))
        return C.addressof(buffer) + offset, offset + len(raw)
    def query(handle, buffer, limit, out):
        seen.append(("QueryServiceConfigW", limit))
        row = b._QueryConfig.from_buffer(buffer)
        row.service_type, row.start_type, row.error_control = 16, 3, 1
        pos = C.sizeof(row)
        for field, text in (("binary", "exact command\0"), ("account", "LocalSystem\0"), ("display", "ComsolMCP_fixture\0")):
            ptr, pos = put(buffer, pos, text)
            setattr(row, field, ptr)
        if bad_pointer == "binary": row.binary = C.addressof(buffer) + limit + 2
        needed(out, pos)
        return 1
    def query2(handle, level, buffer, limit, out):
        if level in (4, 5):
            C.cast(buffer, C.POINTER(b.DWORD))[0] = b.DWORD(3 if level == 5 else 0)
            used = 4
        elif level == 6:
            row = b._PrivilegesInfo.from_buffer(buffer)
            row.names, used = put(buffer, C.sizeof(row), "\0".join(b.PRIVILEGES) + "\0\0")
            if bad_pointer == "privileges": row.names = C.addressof(buffer) - 2
        elif level == 2:
            used = C.sizeof(b._FailureActions)
        else: pytest.fail("unbounded SDK config level")
        needed(out, used)
        return 1
    def status(handle, level, buffer, size, out):
        row = C.cast(buffer, C.POINTER(b._ServiceStatus)).contents
        row.service_type, row.state = 16, 1
        needed(out, C.sizeof(row))
        return 1
    def security(handle, flags, buffer, size, out):
        raw = raw_sd(service=True)
        C.memmove(buffer, raw, len(raw)); needed(out, len(raw))
        return 1
    def dependents(handle, states, buffer, size, out, count):
        seen.append(("EnumDependentServicesW", states))
        needed(out, 0); needed(count, 0)
        if bad_pointer == "dependent":
            row = b._Dependent.from_buffer(buffer)
            row.name = C.addressof(buffer) + size + 2
            needed(count, 1)
        elif bad_pointer == "dependent_count": needed(count, 65)
        return 1
    sdk = {"open": lambda: 123, "CloseServiceHandle": lambda h: 1,
           "QueryServiceConfigW": query, "QueryServiceConfig2W": query2,
           "QueryServiceStatusEx": status, "QueryServiceObjectSecurity": security,
           "EnumDependentServicesW": dependents}
    native = b._Windows._test(sdk, lambda: 5, seal=b._TEST_SEAL)
    handle = native.owner.acquire("open", (), "CloseServiceHandle")
    return native, handle, seen


def test_native_scm_actual_output_copy_reads_config_raw_sd_and_all_dependent_states():
    native, handle, seen = native_scm_fixture()
    image = native.service_image(handle)
    assert image["binary"] == "exact command" and image["account"] == "LocalSystem"
    assert image["sid_type"] == 3 and image["privileges"] == tuple(sorted(b.PRIVILEGES))
    assert image["raw_sd"] == raw_sd(service=True) and image["dependents"] == ()
    assert ("EnumDependentServicesW", 3) in seen
    assert image["state"] == 1 and image["pid"] == 0


@pytest.mark.parametrize("pointer", ["binary", "privileges", "dependent", "dependent_count"])
def test_native_scm_never_dereferences_out_of_buffer_pointers_or_counts(pointer):
    native, handle, _ = native_scm_fixture(bad_pointer=pointer)
    with pytest.raises(b.BootstrapError) as raised:
        native.service_image(handle)
    assert raised.value.code in {"SDK_POINTER", "SDK_BOUNDS"}


def test_operator_cli_default_is_pure_preview_with_no_apply_switch(capsys):
    from tools import windows_host_bootstrap as operator
    doc, _ = intent_doc()
    result = operator.preview(encode(doc))
    assert result["state"] == "INTENT_ONLY_NO_NATIVE_AUTHORITY" and result["apply_ready"] is False
    assert b.retained_bootstrap_owners() == ()
    with pytest.raises(SystemExit) as raised:
        operator.main(["--plan", "not-opened.json", "--sha256", "0" * 64, "--apply"])
    assert raised.value.code == 2
    assert "unrecognized arguments: --apply" in capsys.readouterr().err


def test_full_apply_confirmed_config_success_then_bookkeeping_loss_never_reconfigures(monkeypatch):
    original, backend, _ = fixture_bootstrap()
    configure = backend.configure
    def config_then_lost_bookkeeping(handle, field, value):
        configure(handle, field, value)
        raise RuntimeError("SOFTWARE_ONLY bookkeeping loss after confirmed SDK config")
    monkeypatch.setattr(backend, "configure", config_then_lost_bookkeeping)
    with pytest.raises(b.BootstrapError):
        original.apply(approved_plan_sha256=original.intent.sha256)
    assert original.state == "UNKNOWN" and original.owner.uncertain
    actual_call = next(c for c in original.owner.calls if c.name == "ChangeServiceConfig2W")
    assert actual_call.state == "SUCCEEDED" and actual_call.scalar == 1
    before = list(backend.calls)
    for action in (lambda: original.apply(approved_plan_sha256=original.intent.sha256),
                   original.restore, original.owner.close_all):
        with pytest.raises(b.BootstrapError):
            action()
    assert backend.calls == before


@pytest.mark.parametrize("stage", ["DeleteService", "SetFileInformationByHandle", "close_after_delete"])
def test_partial_restore_unknown_never_retries_or_releases_parents(stage, monkeypatch):
    original, backend, _ = install()
    if stage == "close_after_delete":
        delete = backend.delete_service
        def delete_then_arm_close(handle):
            delete(handle)
            backend.faults["close"] = "raise"
        monkeypatch.setattr(backend, "delete_service", delete_then_arm_close)
    else:
        backend.faults[stage] = "raise"
    with pytest.raises(b.BootstrapError):
        original.restore()
    assert original.state == "UNKNOWN" and original.owner.uncertain
    calls = list(backend.calls)
    with pytest.raises(b.BootstrapError):
        original.restore()
    with pytest.raises(b.BootstrapError):
        original.owner.close_all()
    assert backend.calls == calls and PREFIX in backend.tree
    for path, row in backend.original.items():
        assert backend.tree[path] == row  # original archives/user files untouched


def test_native_config_sets_only_four_privileges_restricted_sid_and_false_noncrash_flag():
    captured = []
    multi = "\0".join(sorted(b.PRIVILEGES)) + "\0\0"
    def config(handle, level, pointer):
        if level == 6:
            row = C.cast(pointer, C.POINTER(b._PrivilegesInfo)).contents
            captured.append((6, C.wstring_at(row.names, len(multi))))
        elif level == 5:
            captured.append((5, C.cast(pointer, C.POINTER(b._SidInfo)).contents.value))
        elif level == 4:
            captured.append((4, C.cast(pointer, C.POINTER(b.BOOL)).contents.value))
        else:
            pytest.fail("unexpected config API")
        return 1
    native = b._Windows._test({"open": lambda: 123, "CloseServiceHandle": lambda h: 1,
                              "ChangeServiceConfig2W": config}, lambda: 5, seal=b._TEST_SEAL)
    handle = native.owner.acquire("open", (), "CloseServiceHandle")
    native.configure(handle, "privileges", tuple(sorted(b.PRIVILEGES)))
    native.configure(handle, "sid_type", 3)
    native.configure(handle, "failure_flag", False)
    assert captured == [(6, multi), (5, 3), (4, 0)]
