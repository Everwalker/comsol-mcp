"""Source controls for the same-process Windows primary-owner prebirth gate."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import windows_primary_token_owner_outer_gate_r5i as gate


HELPER_BYTES = b"reviewed-helper-bytes"
GATE_BYTES = b"reviewed-gate-bytes"
INNER_BINDING = {
    "schema": "cv3-windows-primary-token-owner-binding/1",
    "task_root": gate.TASK_ROOT,
    "python_executable": gate.PYTHON_EXE,
    "python_version": gate.PYTHON_VERSION,
    "helper_path": gate.HELPER_PATH,
    "helper_sha256": hashlib.sha256(HELPER_BYTES).hexdigest(),
    "authority_root": gate.AUTHORITY_ROOT,
    "binding_path": gate.INNER_BINDING_PATH,
    "approval_path": gate.APPROVAL_PATH,
    "once_path": gate.ONCE_PATH,
    "output_root": gate.OUTPUT_ROOT,
    "output_file": gate.OUTPUT_FILE,
    "expected_user_sid": gate.EXPECTED_USER_SID,
    "case_processes": 1,
    "max_active_seconds": 60,
    "COMSOL": 0,
    "GUI": 0,
    "JVM": 0,
    "Study": 0,
    "solver": 0,
    "no_privilege_enablement": True,
    "no_account_or_acl_mutation": True,
    "no_process_signal_or_pid_reopen": True,
}


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _raw(value) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def _candidate():
    source = {
        "source_plan_sha256": "4" * 64,
        "source_review_sha256": "5" * 64,
        "helper_sha256": _hash(HELPER_BYTES),
        "helper_controls_sha256": "b" * 64,
        "gate_sha256": _hash(GATE_BYTES),
        "gate_controls_sha256": "c" * 64,
        "inner_binding_sha256": _hash(_raw(INNER_BINDING)),
        "python_executable_sha256": "1" * 64,
        "python_executable_path_proof_sha256": "a" * 64,
        "protected13_manifest_sha256": "2" * 64,
        "current_resume_sha256": "3" * 64,
        "r14_binding_sha256": "6" * 64,
        "r14_source_closure_sha256": "7" * 64,
        "source_path_proofs_sha256": "8" * 64,
    }
    outer = {
        "schema": gate.OUTER_SCHEMA,
        "status": "REMOTE_PREPARED_CLOSED",
        "candidate": gate.OUTER_CANDIDATE,
        "identity_domains": gate.ROOT_IDENTITY_DOMAINS,
        "source_fingerprints": source,
        "paths": gate.EXPECTED_PATHS,
        "path_proofs": {name: {} for name in gate.PATH_PROOF_FIELDS},
        "r14_source_path_readback": {f"member-{index:03d}": {} for index in range(179)},
        "r14_source_path_readback_count": 179,
        "limits": gate.OUTER_LIMITS,
        "native_execution": gate.OUTER_NATIVE_EXECUTION,
        "closed_authority": gate.OUTER_CLOSED_AUTHORITY,
    }
    outer_raw = _raw(outer)
    approval = {
        "schema": gate.APPROVAL_SCHEMA,
        "status": "APPROVED_EXECUTE_ONCE",
        "binding_sha256": source["inner_binding_sha256"],
        "outer_binding_sha256": _hash(outer_raw),
        "helper_sha256": source["helper_sha256"],
        "helper_controls_sha256": source["helper_controls_sha256"],
        "gate_sha256": source["gate_sha256"],
        "gate_controls_sha256": source["gate_controls_sha256"],
        "python_executable_sha256": source["python_executable_sha256"],
        "python_executable_path_proof_sha256": source["python_executable_path_proof_sha256"],
        "protected13_manifest_sha256": source["protected13_manifest_sha256"],
        "current_resume_sha256": source["current_resume_sha256"],
        "source_plan_sha256": source["source_plan_sha256"],
        "source_review_sha256": source["source_review_sha256"],
        "r14_binding_sha256": source["r14_binding_sha256"],
        "r14_source_closure_sha256": source["r14_source_closure_sha256"],
        "source_path_proofs_sha256": source["source_path_proofs_sha256"],
        "outer_binding_file_proof_sha256": "9" * 64,
        "budget": dict(gate.APPROVAL_BUDGET),
        "native_nonce": "a" * 32,
    }
    return outer, outer_raw, approval


def test_closed_authority_mismatch_rejects_before_live_snapshot_or_helper():
    outer, outer_raw, approval = _candidate()
    approval["python_executable_sha256"] = "f" * 64
    calls = []
    with pytest.raises(gate.GateError, match="python_executable_sha256"):
        gate._dispatch_after_prebirth(
            outer=outer, approval=approval, outer_raw=outer_raw,
            inner_raw=_raw(INNER_BINDING), approval_raw=_raw(approval),
            helper_raw=HELPER_BYTES, gate_raw=GATE_BYTES,
            verify_snapshot=lambda: calls.append("snapshot"),
            invoke_helper=lambda: calls.append("helper"),
        )
    assert calls == []


def test_outer_r5g_schema_is_closed_and_rejects_missing_or_extra_fingerprints():
    outer, _outer_raw, _approval = _candidate()
    gate._validate_outer_envelope(outer)

    missing = dict(outer)
    missing["source_fingerprints"] = dict(outer["source_fingerprints"])
    del missing["source_fingerprints"]["python_executable_path_proof_sha256"]
    with pytest.raises(gate.GateError, match="exact R5-G projection"):
        gate._validate_outer_envelope(missing)

    extra = dict(outer)
    extra["source_fingerprints"] = {**outer["source_fingerprints"], "unreviewed_sha256": "d" * 64}
    with pytest.raises(gate.GateError, match="exact R5-G projection"):
        gate._validate_outer_envelope(extra)


def test_main_authority_binds_runtime_full_path_proof_sha_before_snapshot():
    outer, outer_raw, approval = _candidate()
    approval["python_executable_path_proof_sha256"] = "e" * 64
    calls = []
    with pytest.raises(gate.GateError, match="python_executable_path_proof_sha256"):
        gate._dispatch_after_prebirth(
            outer=outer, approval=approval, outer_raw=outer_raw,
            inner_raw=_raw(INNER_BINDING), approval_raw=_raw(approval),
            helper_raw=HELPER_BYTES, gate_raw=GATE_BYTES,
            verify_snapshot=lambda: calls.append("snapshot"),
            invoke_helper=lambda: calls.append("helper"),
        )
    assert calls == []


def test_main_authority_rejects_unreviewed_extra_approval_fields():
    outer, outer_raw, approval = _candidate()
    approval["note"] = "not part of the closed authority contract"
    with pytest.raises(gate.GateError, match="closed approval schema"):
        gate._dispatch_after_prebirth(
            outer=outer, approval=approval, outer_raw=outer_raw,
            inner_raw=_raw(INNER_BINDING), approval_raw=_raw(approval),
            helper_raw=HELPER_BYTES, gate_raw=GATE_BYTES,
            verify_snapshot=lambda: None,
            invoke_helper=lambda: None,
        )


def test_every_prebirth_check_finishes_before_same_process_helper_dispatch():
    outer, outer_raw, approval = _candidate()
    proof = {"identity": {"device": 1, "inode": 2}, "security_sha256": "d" * 64}
    approval["outer_binding_file_proof_sha256"] = gate._sha256(gate._canonical_json(proof))
    approval_raw = _raw(approval)
    calls = []

    result = gate._dispatch_after_prebirth(
        outer=outer, approval=approval, outer_raw=outer_raw,
        inner_raw=_raw(INNER_BINDING), approval_raw=approval_raw,
        helper_raw=HELPER_BYTES, gate_raw=GATE_BYTES,
        verify_snapshot=lambda: calls.append("snapshot") or {
            "outer_binding": {"private_path": proof, "sha256": _hash(outer_raw)},
            "approval": {"sha256": _hash(approval_raw)},
        },
        invoke_helper=lambda: calls.append("helper") or 73,
    )

    assert result == 73
    assert calls == ["snapshot", "helper"]


def test_helper_is_invoked_with_exact_reviewed_path_and_argv_in_same_process(monkeypatch):
    previous = ["outer-gate", "sentinel"]
    monkeypatch.setattr(gate.sys, "argv", list(previous))
    observed = {}

    def run_path(path, *, run_name):
        observed["path"] = path
        observed["run_name"] = run_name
        observed["argv"] = list(gate.sys.argv)
        observed["pid"] = __import__("os").getpid()
        return "same-process-result"

    assert gate._invoke_reviewed_helper(run_path=run_path) == "same-process-result"
    assert observed == {
        "path": gate.HELPER_PATH,
        "run_name": "__main__",
        "argv": [gate.HELPER_PATH, "--binding", gate.INNER_BINDING_PATH,
                 "--approval", gate.APPROVAL_PATH, "--once", gate.ONCE_PATH],
        "pid": __import__("os").getpid(),
    }
    assert gate.sys.argv == previous


def test_crt_path_and_fd_ctime_domains_are_compared_only_within_domain():
    name_before = SimpleNamespace(st_dev=4, st_ino=8, st_size=12, st_mtime_ns=16,
                                  st_ctime_ns=20, st_birthtime_ns=24)
    fd_before = SimpleNamespace(st_dev=4, st_ino=8, st_size=12, st_mtime_ns=16,
                                st_ctime_ns=21, st_birthtime_ns=24)
    fd_after = SimpleNamespace(st_dev=4, st_ino=8, st_size=12, st_mtime_ns=16,
                               st_ctime_ns=21, st_birthtime_ns=24)
    name_after = SimpleNamespace(st_dev=4, st_ino=8, st_size=12, st_mtime_ns=16,
                                 st_ctime_ns=20, st_birthtime_ns=24)
    assert gate._metadata_stable(name_before, fd_before, fd_after, name_after)
    changed = SimpleNamespace(**{**vars(fd_after), "st_mtime_ns": 17})
    assert not gate._metadata_stable(name_before, fd_before, changed, name_after)
    changed_name_ctime = SimpleNamespace(**{**vars(name_after), "st_ctime_ns": 22})
    assert not gate._metadata_stable(name_before, fd_before, fd_after, changed_name_ctime)
    changed_fd_ctime = SimpleNamespace(**{**vars(fd_after), "st_ctime_ns": 22})
    assert not gate._metadata_stable(name_before, fd_before, changed_fd_ctime, name_after)


def test_r14_source_groups_use_their_exact_remote_roots_and_root_id_domain():
    assert gate._r14_source_path("source175", "LICENSE") == gate.R14_SOURCE_ROOT + r"\LICENSE"
    assert gate._r14_source_path("helpers", "helpers-r14/windows_task_thread_owner.py") == (
        gate.TASK_ROOT + r"\helpers-r14\windows_task_thread_owner.py"
    )
    r14_identity = {"device": 5197258733370948702, "inode": 13792273858991511}
    gate._validate_restore_chain_identity(r14_identity, dict(r14_identity), dict(r14_identity))
    with pytest.raises(gate.GateError):
        gate._validate_restore_chain_identity(
            r14_identity, {"device": 543112286, "inode": r14_identity["inode"]}, dict(r14_identity),
        )


def test_r14_root_acl_digest_matches_private_path_leaf_not_chain_aggregate():
    proof = {
        "identity": {"device": 543112286, "inode": 13792273858991511, "security_sha256": "a" * 64},
        "security_sha256": "b" * 64,
        "path_chain": [
            {"identity": {"device": 543112286, "inode": 13792273858991511},
             "descriptor_sha256": "c" * 64, "owner": "daemon"},
            {"identity": {"device": 543112286, "inode": 13792273858991511},
             "descriptor_sha256": "d" * 64, "owner": "daemon"},
        ],
    }
    gate._validate_root_acl_descriptor("d" * 64, proof)
    with pytest.raises(gate.GateError):
        gate._validate_root_acl_descriptor("b" * 64, proof)


def _fake_runtime_image(monkeypatch, *, change_second_chain=False, reject_index=None,
                        native_fingerprint=None, provider_leaf_fingerprint=None):
    monkeypatch.setattr(gate, "_assert_path_kind", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(gate, "_win32_attributes", lambda _path: 32)
    components = gate._runtime_component_paths(gate.PYTHON_EXE)
    content = b"python"
    leaf_id = 10_000 + len(components) - 1
    # Exercise the full 64-bit volume and 128-bit file-ID domains. The private
    # path volume/file-index identity below remains its separate Win32 domain.
    leaf_native = [[(1 << 63) + 7, (1 << 127) + leaf_id], len(content), 11, 12, 13, 1]
    if provider_leaf_fingerprint is None:
        provider_leaf_fingerprint = (tuple(leaf_native[0]), *leaf_native[1:])
    owner_sid = "S-1-5-21-11-22-33-1001"
    calls = {"descriptors": [], "owner_sids": [], "finish": []}

    class API:
        def __init__(self):
            self.open_count = 0

        def open_security_path(self, _path, *, directory):
            assert directory is False
            self.open_count += 1
            offset = 0 if self.open_count == 1 else 1000
            return [offset + index + 1 for index in range(len(components))]

        def info(self, handle, *, directory):
            index = (handle - 1) % 1000
            assert directory is (index < len(components) - 1)
            return (7, 10_000 + index), 64

    api = API()
    owner_root = SimpleNamespace(remember=lambda error: None)
    monkeypatch.setattr(gate, "_runtime_path_binding_api", lambda: (
        api, lambda _api: owner_root,
        lambda _api, primary=None: calls["finish"].append(primary),
    ))

    class Provider:
        WINDOWS_PRIVATE_PATH_SECURITY_SEMANTICS = "windows-read-control-owner-dacl-persistent-acls"

        @staticmethod
        def _windows_volume_has_persistent_acls(_api, _path):
            return True, 7

        @staticmethod
        def _windows_security_context(_api):
            return "daemon-sid-pointer", ["trusted-sid-pointer"], ("token", [], "buffer")

        @staticmethod
        def _windows_original_full_getter(_api):
            def full_fingerprint(handle, *, directory):
                index = (handle - 1) % 1000
                assert directory is (index < len(components) - 1)
                if index == len(components) - 1:
                    return provider_leaf_fingerprint
                return ((7, 10_000 + index), 64, 11, 12, 13, 1)
            return full_fingerprint

        @staticmethod
        def _win32_descriptor_digest(_api, handle, *, directory, require_daemon_owner,
                                     daemon_sid, trusted_sids, pinned_ancestor_traversal=False):
            index = (handle - 1) % 1000
            assert require_daemon_owner is False
            assert daemon_sid == "daemon-sid-pointer" and trusted_sids == ["trusted-sid-pointer"]
            assert pinned_ancestor_traversal is (index < len(components) - 1)
            calls["descriptors"].append((index, require_daemon_owner, pinned_ancestor_traversal))
            if reject_index == index:
                raise RuntimeError("qualified descriptor policy refusal")
            sha = hashlib.sha256(f"descriptor-{index}".encode()).hexdigest()
            if change_second_chain and api.open_count > 1 and index == len(components) - 1:
                sha = "f" * 64
            return sha, "trusted-host-admin"

    monkeypatch.setattr(gate, "_runtime_owner_sid_text",
                        lambda _api, handle: calls["owner_sids"].append(handle) or owner_sid)
    native = {"full_native_fingerprint": native_fingerprint or (tuple(leaf_native[0]), *leaf_native[1:])}
    read_native = lambda _path: (content, hashlib.sha256(content).hexdigest(), native)
    return Provider(), read_native, calls, components, owner_sid, leaf_native


def test_runtime_image_uses_r14_descriptor_policy_and_binds_exact_owner_and_full_ids(monkeypatch):
    provider, read_native, calls, components, owner_sid, leaf_native = _fake_runtime_image(monkeypatch)

    observation = gate._runtime_image_proof(provider, read_native)

    assert observation["sha256"] == _hash(b"python")
    assert observation["native_fingerprint"] == leaf_native
    assert leaf_native[0][0] > (1 << 63) and leaf_native[0][1] > (1 << 127)
    assert observation["reparse_free_leaf"] is True
    chain = observation["private_path"]["path_chain"]
    assert [row["path"] for row in chain] == components
    assert [row["owner_sid"] for row in chain] == [owner_sid] * len(components)
    assert [row["native_full_fingerprint"] for row in chain][-1] == leaf_native
    assert len(calls["descriptors"]) == 2 * len(components)
    assert calls["finish"] == [None]


@pytest.mark.parametrize(
    ("field", "mutate"),
    [
        ("volume_64", lambda value: value[0].__setitem__(0, value[0][0] + 1)),
        ("file_id_128_high_bit", lambda value: value[0].__setitem__(1, value[0][1] ^ (1 << 127))),
        ("size", lambda value: value.__setitem__(1, value[1] + 1)),
        ("creation_time", lambda value: value.__setitem__(2, value[2] + 1)),
        ("change_time", lambda value: value.__setitem__(3, value[3] + 1)),
        ("write_time", lambda value: value.__setitem__(4, value[4] + 1)),
        ("link_count", lambda value: value.__setitem__(5, value[5] + 1)),
    ],
)
def test_runtime_image_rejects_each_changed_native_fingerprint_field(monkeypatch, field, mutate):
    provider, _read_native, _calls, _components, _owner_sid, leaf_native = _fake_runtime_image(monkeypatch)
    changed = [list(leaf_native[0]), *leaf_native[1:]]
    mutate(changed)
    bad_native = (tuple(changed[0]), *changed[1:])
    read_native = lambda _path: (
        b"python", _hash(b"python"), {"full_native_fingerprint": bad_native},
    )

    with pytest.raises(gate.GateError, match="native full identity differs") as caught:
        gate._runtime_image_proof(provider, read_native)

    assert json.dumps(gate.PYTHON_EXE) in str(caught.value)
    assert field in {
        "volume_64", "file_id_128_high_bit", "size", "creation_time",
        "change_time", "write_time", "link_count",
    }


@pytest.mark.parametrize("side", ["provider_path", "original_byte_handle"])
@pytest.mark.parametrize("bad_link_count", [True, 1.0])
def test_runtime_image_rejects_bool_or_float_fingerprint_integer(monkeypatch, side, bad_link_count):
    if side == "provider_path":
        # bool and float compare equal to integer 1 in Python; exact type checks
        # must reject them before a list/tuple equality comparison can pass.
        components = gate._runtime_component_paths(gate.PYTHON_EXE)
        leaf = [[(1 << 63) + 7, (1 << 127) + 10_000 + len(components) - 1], 6, 11, 12, 13, bad_link_count]
        provider_leaf = (tuple(leaf[0]), *leaf[1:])
        provider, read_native, _calls, _components, _owner_sid, _leaf_native = _fake_runtime_image(
            monkeypatch, provider_leaf_fingerprint=provider_leaf,
        )
    else:
        provider, _default_read_native, _calls, _components, _owner_sid, leaf_native = _fake_runtime_image(monkeypatch)
        changed = [list(leaf_native[0]), *leaf_native[1:]]
        changed[5] = bad_link_count
        native_fingerprint = (tuple(changed[0]), *changed[1:])
        read_native = lambda _path: (
            b"python", _hash(b"python"), {"full_native_fingerprint": native_fingerprint},
        )

    with pytest.raises(gate.GateError, match="exact integer") as caught:
        gate._runtime_image_proof(provider, read_native)

    assert "link_count" in str(caught.value)


def test_runtime_image_rejects_malformed_full_native_fingerprint_shape(monkeypatch):
    provider, _read_native, _calls, _components, _owner_sid, leaf_native = _fake_runtime_image(monkeypatch)
    malformed = (tuple(leaf_native[0]), *leaf_native[1:-1])
    read_native = lambda _path: (
        b"python", _hash(b"python"), {"full_native_fingerprint": malformed},
    )

    with pytest.raises(gate.GateError, match="exactly six fields"):
        gate._runtime_image_proof(provider, read_native)


def test_runtime_image_descriptor_policy_refusal_fails_with_exact_component_and_cleanup(monkeypatch):
    provider, read_native, calls, components, _owner_sid, _leaf_native = _fake_runtime_image(
        monkeypatch, reject_index=2,
    )

    with pytest.raises(gate.GateError, match='"index": 2') as caught:
        gate._runtime_image_proof(provider, read_native)

    assert calls["finish"] and isinstance(calls["finish"][0], gate.GateError)
    assert json.dumps(components[2]) in str(caught.value)


def test_runtime_image_second_chain_acl_difference_reports_the_changed_leaf(monkeypatch):
    provider, read_native, _calls, components, _owner_sid, _leaf_native = _fake_runtime_image(
        monkeypatch, change_second_chain=True,
    )

    with pytest.raises(gate.GateError, match='"index": 8') as caught:
        gate._runtime_image_proof(provider, read_native)
    assert json.dumps(components[-1]) in str(caught.value)


def test_gate_has_no_process_creation_path_and_uses_runpy_only_after_dispatch():
    source = Path(gate.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "subprocess" not in names | attrs
    assert not ({"Popen", "CreateProcess", "CreateProcessWithTokenW", "system"} & attrs)

    dispatch = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "_dispatch_after_prebirth")
    calls = [node.func.id for node in ast.walk(dispatch) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name)]
    assert calls.index("verify_snapshot") < calls.index("invoke_helper")

