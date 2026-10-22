"""Production attribute-list controls with synthetic SDK boundaries only.

The separate ordinary Windows probe observes policy flags; these tests neither
mint admission nor claim child-spawn enforcement or vendor compatibility.
"""
import ctypes as C
import gc
from types import SimpleNamespace
import weakref

import pytest

from comsol_mcp import _windows_t037 as win
from comsol_mcp._windows_owned_resources import NativeResourceError, own, registry_for


class SECURITY_CAPABILITIES(C.Structure):
    _fields_ = [("AppContainerSid", C.c_void_p), ("Capabilities", C.c_void_p),
                ("CapabilityCount", C.c_uint32), ("Reserved", C.c_uint32)]


def _address(value):
    return C.cast(value, C.c_void_p).value if value is not None else None


def _sdk(monkeypatch, api=None, *, fail_update=None, delete_failure=None, mode=None):
    if api is None:
        api = win._Win32LaunchApi.__new__(win._Win32LaunchApi)
        api.C = C
        api.W = SimpleNamespace(DWORD=C.c_uint32)
        api.k = SimpleNamespace()
    state = {"error": 122, "init": [], "updates": [], "delete": [], "policy_ref": None}
    monkeypatch.setattr(C, "get_last_error", lambda: state["error"], raising=False)

    def initialize(pointer, count, flags, output):
        state["init"].append((_address(pointer), count, flags))
        size = C.cast(output, C.POINTER(C.c_size_t)).contents
        if mode == "only_two_supported" and count != 2:
            size.value, state["error"] = 0, 87
            return 0
        if not pointer:
            size.value = 0 if mode == "zero_size" else 65537 if mode == "oversized" else 128
            state["error"] = 5 if mode == "probe_error" else 122
            return int(mode == "unexpected_probe_success")
        if mode == "init_error":
            state["error"] = 87
            return 0
        if mode == "initialized_size_overflow":
            size.value = 129
        if mode == "initialized_size_zero":
            size.value = 0
        return 1

    def update(pointer, flags, key, value, size, previous, returned):
        address = _address(value)
        # Store addresses/copies/weakrefs only. A CArgObject would artificially
        # extend the DWORD lifetime and invalidate the owner/GC control.
        state["updates"].append({"list": _address(pointer), "flags": flags, "key": key,
            "value": address, "size": size, "bytes": C.string_at(address, size),
            "previous": previous, "returned": returned})
        if key == 0x0002000E:
            state["policy_ref"] = weakref.ref(value._obj)
        gc.collect()
        if len(state["updates"]) == fail_update:
            state["error"] = 5
            return 0
        return 1

    def delete(pointer):
        state["delete"].append(_address(pointer))
        if state["policy_ref"] is not None:
            assert state["policy_ref"]() is not None
            assert state["policy_ref"]().value == 1
        if delete_failure == "raise":
            raise OSError("original DeleteProcThreadAttributeList failed")
        if delete_failure == "invalid_void_result":
            return False

    api.k.InitializeProcThreadAttributeList = initialize
    api.k.UpdateProcThreadAttribute = update
    api.k.DeleteProcThreadAttributeList = delete
    return api, state


def _values():
    return SECURITY_CAPABILITIES(0x500, None, 0, 0), (C.c_void_p * 2)(0x101, 0x102)


def test_three_exact_updates_use_fixed_real_dword_and_original_values(monkeypatch):
    api, state = _sdk(monkeypatch)
    caps, handles = _values()
    storage, pointer = api._attribute_list(caps, handles)
    assert [row[1:] for row in state["init"]] == [(3, 0), (3, 0)]
    assert [row["key"] for row in state["updates"]] == [0x20009, 0x20002, 0x2000E]
    assert [row["size"] for row in state["updates"]] == [C.sizeof(caps), C.sizeof(handles), 4]
    assert state["updates"][0]["value"] == C.addressof(caps)
    assert state["updates"][1]["value"] == C.addressof(handles)
    assert state["updates"][2]["bytes"] == b"\x01\0\0\0"
    assert all(row["list"] == pointer.value and row["flags"] == 0
               and row["previous"] is None and row["returned"] is None for row in state["updates"])
    entry = registry_for(api).find(api, pointer)
    assert entry.registry.owner is api and entry.keepalive[:3] == (storage, caps, handles)
    policy = entry.keepalive[3]
    assert type(policy) is C.c_uint32 and policy.value == 1
    assert win._Win32LaunchApi.PROC_THREAD_ATTRIBUTE_CHILD_PROCESS_POLICY == 0x2000E
    entry.release_owned(api)
    assert state["delete"] == [pointer.value] and not registry_for(api).unresolved()


def test_original_storage_survives_gc_and_launcher_duplicate_registration(monkeypatch):
    api, state = _sdk(monkeypatch)
    caps, handles = _values()
    storage, pointer = api._attribute_list(caps, handles)
    entry = registry_for(api).find(api, pointer)
    # The unchanged launcher repeats own() with its original three storages.
    again = own(api, pointer, api.k.DeleteProcThreadAttributeList, mode="VOID",
                label="attribute_list", keepalive=(storage, caps, handles))
    assert again is entry and len(entry.keepalive) == 4
    refs = tuple(weakref.ref(value) for value in entry.keepalive)
    del caps, handles, storage, pointer, again
    gc.collect()
    assert all(ref() is not None for ref in refs)
    entry.release_owned(api)
    assert state["delete"] == [entry.value] and entry.keepalive == ()
    gc.collect()
    assert refs[3]() is None  # Policy was retained by its actual owner until Delete.


@pytest.mark.parametrize("index,label", [(1, "SecurityCapabilities"), (2, "HandleList"), (3, "ChildProcessPolicy")])
def test_update_failure_refuses_and_releases_once_without_fallback(monkeypatch, index, label):
    api, state = _sdk(monkeypatch, fail_update=index)
    with pytest.raises(win.WindowsT037Error) as exc:
        api._attribute_list(*_values())
    assert label in str(exc.value) and "error 5" in str(exc.value)
    assert len(state["updates"]) == index and len(state["delete"]) == 1
    assert all(row[1] == 3 for row in state["init"])
    assert not registry_for(api).unresolved()


@pytest.mark.parametrize("delete_failure", ["raise", "invalid_void_result"])
def test_third_update_failure_keeps_unknown_originals_and_never_retries_delete(monkeypatch, delete_failure):
    api, state = _sdk(monkeypatch, fail_update=3, delete_failure=delete_failure)
    registry = registry_for(api)
    scope = registry.scope()
    with pytest.raises((OSError, NativeResourceError)) as exc:
        api._attribute_list(*_values())
    assert "ChildProcessPolicy" in str(exc.value.__context__)
    entries = registry.unresolved()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.state == "UNKNOWN" and entry.registry.owner is api and entry.adapter is api
    assert len(entry.keepalive) == 4 and C.sizeof(entry.keepalive[0]) == 128
    assert entry.keepalive[3].value == 1
    gc.collect()
    assert state["policy_ref"]() is entry.keepalive[3]
    assert len(registry.drain(scope)) == 1
    with pytest.raises((OSError, NativeResourceError)):
        entry.release_owned(api)
    assert state["delete"] == [entry.value] and entry.state == "UNKNOWN"


@pytest.mark.parametrize("mode", ["zero_size", "oversized", "probe_error", "unexpected_probe_success",
    "init_error", "initialized_size_overflow", "initialized_size_zero", "only_two_supported"])
def test_size_initialization_and_old_two_attribute_sdk_fail_closed(monkeypatch, mode):
    api, state = _sdk(monkeypatch, mode=mode)
    with pytest.raises(win.WindowsT037Error):
        api._attribute_list(*_values())
    assert not state["updates"] and all(row[1] == 3 for row in state["init"])
    assert len(state["delete"]) == (1 if mode.startswith("initialized_size_") else 0)
    assert not registry_for(api).unresolved()


@pytest.mark.parametrize("key", [0, 0x20002, 0x20009, 0x2000F, True, "0x2000e"])
def test_substituted_attribute_key_cannot_disable_or_redirect_policy(monkeypatch, key):
    api, state = _sdk(monkeypatch)
    api.PROC_THREAD_ATTRIBUTE_CHILD_PROCESS_POLICY = key
    with pytest.raises(win.WindowsT037Error):
        api._attribute_list(*_values())
    assert not state["init"] and not state["updates"] and not state["delete"]


@pytest.mark.parametrize("fault", ["wide_dword", "zero_policy", "override_policy"])
def test_dword_abi_and_nonrestricted_value_are_rejected_before_allocation(monkeypatch, fault):
    api, state = _sdk(monkeypatch)
    class ReplacedDWORD(C.c_uint32):
        def __init__(self, value):
            C.c_uint32.__init__(self, 0 if fault == "zero_policy" else 2)
    api.W.DWORD = C.c_uint64 if fault == "wide_dword" else ReplacedDWORD
    with pytest.raises(win.WindowsT037Error):
        api._attribute_list(*_values())
    assert not state["init"] and not registry_for(api).unresolved()


def test_no_attribute_list_caller_policy_parameter(monkeypatch):
    api, state = _sdk(monkeypatch)
    caps, handles = _values()
    with pytest.raises(TypeError):
        api._attribute_list(caps, handles, child_process_policy=0)
    with pytest.raises(TypeError):
        api._attribute_list(caps, handles, 0)
    assert not state["init"]


@pytest.mark.parametrize("option", ["child_process_policy", "attribute_list", "startupinfo"])
def test_existing_launcher_option_allowlist_refuses_caller_substitution(monkeypatch, option):
    from tests.test_windows_suspended_ownership import _launch_api
    profile, api, events, state = _launch_api(monkeypatch)
    with pytest.raises(win.WindowsT037Error):
        api.create_suspended_appcontainer_child([profile._launch_image("server").path],
            {option: 0}, profile=profile, job=300, role="server")
    assert "CREATE_SUSPENDED" not in events and "RESUME" not in events


@pytest.mark.parametrize("role", ["server", "worker"])
@pytest.mark.parametrize("fail_third", [False, True])
def test_both_launcher_roles_use_production_three_attributes_and_keep_original_guards(monkeypatch, role, fail_third):
    from tests.test_windows_suspended_ownership import _launch_api
    profile, api, events, state = _launch_api(monkeypatch)
    api, sdk = _sdk(monkeypatch, api, fail_update=3 if fail_third else None)
    api._attribute_list = win._Win32LaunchApi._attribute_list.__get__(api)
    api._hash_image_handle = lambda *_: profile.server_image_sha256 if role == "server" else profile.worker_image_sha256
    api.process_image_path = lambda _: profile._launch_image(role).path
    original_create = api.a.CreateProcessAsUserW
    def create(*args):
        entries = [entry for entry in registry_for(api).unresolved() if entry.label == "attribute_list"]
        assert len(entries) == 1 and len(entries[0].keepalive) == 4
        assert entries[0].keepalive[3].value == 1
        return original_create(*args)
    api.a.CreateProcessAsUserW = create
    with pytest.raises(win.WindowsT037Error) as exc:
        api.create_suspended_appcontainer_child([profile._launch_image(role).path], {},
            profile=profile, job=300, role=role)
    assert len(sdk["updates"]) == 3 and sdk["updates"][2]["key"] == 0x2000E
    assert len(sdk["delete"]) == 1 and "RESUME" not in events
    ancillary = [entry for entry in registry_for(api).unresolved() if entry.label != "owned_session_job"]
    assert not ancillary
    if fail_third:
        assert "ChildProcessPolicy" in str(exc.value) and "CREATE_SUSPENDED" not in events
    else:
        assert exc.value.code == "NATIVE_CHILD_PROOF_UNAVAILABLE"
        assert "CREATE_SUSPENDED" in events and not state["alive"]
        assert ("terminate", 200) in events and ("close", 200) in events and ("close", 201) in events
