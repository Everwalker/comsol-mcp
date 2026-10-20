"""Source controls for the same-process Windows primary-owner prebirth gate."""
from __future__ import annotations

import ast
import hashlib
import json
import ntpath
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from tools import windows_primary_token_owner_outer_gate_r5j as gate


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


def test_outer_r5j_schema_is_closed_and_rejects_missing_or_extra_fingerprints():
    outer, _outer_raw, _approval = _candidate()
    gate._validate_outer_envelope(outer)

    missing = dict(outer)
    missing["source_fingerprints"] = dict(outer["source_fingerprints"])
    del missing["source_fingerprints"]["python_executable_path_proof_sha256"]
    with pytest.raises(gate.GateError, match="exact R5-J projection"):
        gate._validate_outer_envelope(missing)

    extra = dict(outer)
    extra["source_fingerprints"] = {**outer["source_fingerprints"], "unreviewed_sha256": "d" * 64}
    with pytest.raises(gate.GateError, match="exact R5-J projection"):
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


def _fake_runtime_image(monkeypatch, *, path=gate.PYTHON_EXE, directory=False,
                        change_second_chain=False, reject_index=None,
                        native_fingerprint=None, provider_leaf_fingerprint=None,
                        second_full_changes=None, second_identity_changes=(),
                        second_volume_changes=(), second_descriptor_changes=(), second_owner_class_changes=(),
                        second_owner_sid_changes=(), attributes=32, info_reject_index=None):
    monkeypatch.setattr(gate, "_assert_path_kind", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(gate, "_win32_attributes", lambda _path: attributes)
    components = gate._runtime_component_paths(path)
    target_is_directory = directory
    content = b"python"
    leaf_id = 10_000 + len(components) - 1
    # Exercise the full 64-bit volume and 128-bit file-ID domains. The private
    # path volume/file-index identity below remains its separate Win32 domain.
    leaf_native = [[(1 << 63) + 7, (1 << 127) + leaf_id], len(content), 11, 12, 13, 1]
    if provider_leaf_fingerprint is None:
        provider_leaf_fingerprint = (tuple(leaf_native[0]), *leaf_native[1:])
    owner_sid = "S-1-5-21-11-22-33-1001"
    calls = {"descriptors": [], "owner_sids": [], "finish": [], "full": [], "security_context": []}
    second_full_changes = second_full_changes or {}
    second_identity_changes = set(second_identity_changes)
    second_volume_changes = set(second_volume_changes)
    second_descriptor_changes = set(second_descriptor_changes)
    second_owner_class_changes = set(second_owner_class_changes)
    second_owner_sid_changes = set(second_owner_sid_changes)

    class API:
        def __init__(self):
            self.open_count = 0

        def open_security_path(self, requested, *, directory):
            assert str(requested) == path
            assert directory is target_is_directory
            self.open_count += 1
            offset = 0 if self.open_count == 1 else 1000
            return [offset + index + 1 for index in range(len(components))]

        def info(self, handle, *, directory):
            index = (handle - 1) % 1000
            assert directory is (index < len(components) - 1 or target_is_directory)
            if index == info_reject_index:
                raise RuntimeError("synthetic pinned reparse-component refusal")
            file_index = 10_000 + index
            volume = 7
            if self.open_count > 1 and index in second_volume_changes:
                volume += 1
            if self.open_count > 1 and index in second_identity_changes:
                file_index += 1
            return (volume, file_index), 64

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
            calls["security_context"].append("acquired")
            return "daemon-sid-pointer", ["trusted-sid-pointer"], ("token", [], "buffer")

        @staticmethod
        def _windows_original_full_getter(_api):
            def full_fingerprint(handle, *, directory):
                borrowed_handle = getattr(handle, "value", None) is not None
                handle_value = getattr(handle, "value", handle)
                if borrowed_handle:
                    index = len(components) - 1 if handle_value == 9 else len(components) - 2
                    assert directory is False
                else:
                    index = (handle_value - 1) % 1000
                    assert directory is (index < len(components) - 1 or target_is_directory)
                value = (provider_leaf_fingerprint if index == len(components) - 1
                         else ((7, 10_000 + index), 64, 11, 12, 13, 1))
                value = list(value)
                if isinstance(value[0], tuple):
                    value[0] = list(value[0])
                if api.open_count > 1:
                    for field, replacement in second_full_changes.get(index, {}).items():
                        slot = {"volume_64": (0, 0), "file_id_128": (0, 1), "size": (1,),
                                "creation_time": (2,), "change_time": (3,),
                                "write_time": (4,), "link_count": (5,)}[field]
                        if field in ("volume_64", "file_id_128"):
                            value[0][slot[1]] = replacement
                        else:
                            value[slot[0]] = replacement
                calls["full"].append((api.open_count, index, directory))
                return (tuple(value[0]), *value[1:])
            return full_fingerprint

        @staticmethod
        def _win32_descriptor_digest(_api, handle, *, directory, require_daemon_owner,
                                     daemon_sid, trusted_sids, pinned_ancestor_traversal=False):
            index = (handle - 1) % 1000
            calls["descriptors"].append((api.open_count, index, require_daemon_owner, pinned_ancestor_traversal))
            assert daemon_sid == "daemon-sid-pointer" and trusted_sids == ["trusted-sid-pointer"]
            if reject_index == index:
                raise RuntimeError("qualified descriptor policy refusal")
            sha = hashlib.sha256(f"descriptor-{index}".encode()).hexdigest()
            if ((change_second_chain and api.open_count > 1 and index == len(components) - 1)
                    or (api.open_count > 1 and index in second_descriptor_changes)):
                sha = "f" * 64
            owner_class = "trusted-host-admin"
            if api.open_count > 1 and index in second_owner_class_changes:
                owner_class = "changed-owner-classification"
            return sha, owner_class

    def owner_sid_for_handle(_api, handle):
        index = (handle - 1) % 1000
        calls["owner_sids"].append((api.open_count, index))
        return owner_sid + ("-changed" if api.open_count > 1 and index in second_owner_sid_changes else "")

    monkeypatch.setattr(gate, "_runtime_owner_sid_text", owner_sid_for_handle)
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

    with pytest.raises(gate.GateError, match="pinned leaf differs") as caught:
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

    with pytest.raises(gate.GateError, match='"component_index": 2') as caught:
        gate._runtime_image_proof(provider, read_native)

    assert calls["finish"] and isinstance(calls["finish"][0], gate.GateError)
    assert json.dumps(components[2]) in str(caught.value)


def test_runtime_image_second_chain_acl_difference_reports_the_changed_leaf(monkeypatch):
    provider, read_native, _calls, components, _owner_sid, _leaf_native = _fake_runtime_image(
        monkeypatch, change_second_chain=True,
    )

    with pytest.raises(gate.GateError, match='"component_index": 8') as caught:
        gate._runtime_image_proof(provider, read_native)
    assert json.dumps(components[-1]) in str(caught.value)


def test_r5j_allows_only_outside_ancestor_change_write_times_and_keeps_raw_diagnostics(monkeypatch):
    component_count = len(gate._runtime_component_paths(gate.PYTHON_EXE))
    changed = {index: {"change_time": 112, "write_time": 113}
               for index in range(component_count - 1)}
    provider, read_native, calls, components, _owner_sid, leaf_native = _fake_runtime_image(
        monkeypatch, second_full_changes=changed,
    )

    proof = gate._runtime_image_proof(provider, read_native)

    chain = proof["private_path"]["path_chain"]
    for index, row in enumerate(chain[:-1]):
        assert row["outside_task_root_ancestor"] is True
        assert row["native_full_fingerprint"][3:5] == [12, 13]
        assert row["native_full_fingerprint_second"][3:5] == [112, 113]
        assert row["outside_task_root_ancestor_time_differences"] == {
            "change_time": {"first": 12, "second": 112},
            "write_time": {"first": 13, "second": 113},
        }
        assert row["path"] == components[index]
    assert chain[-1]["outside_task_root_ancestor"] is False
    assert chain[-1]["native_full_fingerprint"] == leaf_native
    assert chain[-1]["native_full_fingerprint_second"] == leaf_native

    # A changed raw diagnostic remains visible, while the canonical identity
    # contains identical values for this exact two-field ancestor exception.
    import copy
    changed_diagnostic = copy.deepcopy(proof)
    changed_row = changed_diagnostic["private_path"]["path_chain"][0]
    changed_row["native_full_fingerprint"][3] += 1
    changed_row["native_full_fingerprint_second"][4] += 1
    changed_row["outside_task_root_ancestor_time_differences"]["change_time"]["first"] += 1
    changed_row["outside_task_root_ancestor_time_differences"]["write_time"]["second"] += 1
    assert changed_diagnostic != proof
    assert gate._r5j_identity_projection(changed_diagnostic) == gate._r5j_identity_projection(proof)
    assert calls["finish"] == [None]


@pytest.mark.parametrize(
    ("field", "replacement"),
    [("volume_64", 8), ("file_id_128", 999_999), ("size", 65),
     ("creation_time", 12), ("link_count", 2)],
)
def test_r5j_outside_ancestor_rejects_every_non_time_full_identity_change(monkeypatch, field, replacement):
    provider, _read_native, _calls, components, _owner_sid, _leaf_native = _fake_runtime_image(
        monkeypatch, second_full_changes={0: {field: replacement}},
    )

    with pytest.raises(gate.GateError, match="pinned component full identity changed") as caught:
        gate._r5j_private_path_proof(provider, gate.PYTHON_EXE, directory=False)

    assert json.dumps(components[0]) in str(caught.value)


def test_r5j_outside_ancestor_path_identity_domain_mismatch_is_not_masked(monkeypatch):
    provider, _read_native, _calls, components, _owner_sid, _leaf_native = _fake_runtime_image(
        monkeypatch, second_identity_changes={0},
    )

    with pytest.raises(gate.GateError, match="identity-domain, descriptor, or owner changed") as caught:
        gate._r5j_private_path_proof(provider, gate.PYTHON_EXE, directory=False)

    assert json.dumps(components[0]) in str(caught.value)
    assert "identity" in str(caught.value)


def test_r5j_private_root_and_descendant_are_not_in_the_time_allowance(monkeypatch):
    components = gate._runtime_component_paths(gate.TASK_ROOT)
    root_index = next(i for i, value in enumerate(components)
                      if ntpath.normcase(value) == ntpath.normcase(gate.TASK_ROOT))
    provider, _read_native, _calls, _components, _owner_sid, _leaf_native = _fake_runtime_image(
        monkeypatch, path=gate.TASK_ROOT, directory=True, attributes=gate.FILE_ATTRIBUTE_DIRECTORY,
        second_full_changes={root_index: {"change_time": 99}},
    )

    with pytest.raises(gate.GateError, match="pinned component full identity changed") as caught:
        gate._r5j_private_path_proof(provider, gate.TASK_ROOT, directory=True)

    assert json.dumps(gate.TASK_ROOT) in str(caught.value)


@pytest.mark.parametrize("time_field", ["change_time", "write_time"])
@pytest.mark.parametrize("component_role", ["private_root", "private_descendant", "private_leaf"])
def test_r5j_private_root_descendant_and_leaf_times_remain_exact(monkeypatch, component_role, time_field):
    path = gate.TASK_ROOT + r"\source-r14\comsol_mcp\control.py"
    components = gate._runtime_component_paths(path)
    root_index = next(i for i, value in enumerate(components)
                      if ntpath.normcase(value) == ntpath.normcase(gate.TASK_ROOT))
    index = {
        "private_root": root_index,
        "private_descendant": root_index + 1,
        "private_leaf": len(components) - 1,
    }[component_role]
    provider, _read_native, _calls, _components, _owner_sid, _leaf_native = _fake_runtime_image(
        monkeypatch, path=path, second_full_changes={index: {time_field: 99}},
    )

    with pytest.raises(gate.GateError, match="pinned component full identity changed") as caught:
        gate._r5j_private_path_proof(provider, path, directory=False)

    assert json.dumps(components[index]) in str(caught.value)


@pytest.mark.parametrize("time_field", ["change_time", "write_time"])
def test_r5j_python_runtime_leaf_times_and_sha_full_identity_stay_strict(monkeypatch, time_field):
    component_index = len(gate._runtime_component_paths(gate.PYTHON_EXE)) - 1
    provider, read_native, _calls, components, _owner_sid, _leaf_native = _fake_runtime_image(
        monkeypatch, second_full_changes={component_index: {time_field: 88}},
    )

    with pytest.raises(gate.GateError, match="pinned component full identity changed") as caught:
        gate._runtime_image_proof(provider, read_native)

    assert json.dumps(components[-1]) in str(caught.value)


def test_r5j_only_outside_ordinary_directories_receive_traversal_policy(monkeypatch):
    components = gate._runtime_component_paths(gate.TASK_ROOT + r"\source-r14\module.py")
    root_index = next(i for i, value in enumerate(components)
                      if ntpath.normcase(value) == ntpath.normcase(gate.TASK_ROOT))
    provider, _read_native, calls, _components, _owner_sid, _leaf_native = _fake_runtime_image(
        monkeypatch, path=gate.TASK_ROOT + r"\source-r14\module.py",
    )

    gate._r5j_private_path_proof(provider, gate.TASK_ROOT + r"\source-r14\module.py", directory=False)

    first = [row for row in calls["descriptors"] if row[0] == 1]
    assert len(first) == len(components)
    for _chain, index, require_daemon_owner, pinned_ancestor in first:
        if index < root_index:
            assert require_daemon_owner is False and pinned_ancestor is True
        else:
            assert require_daemon_owner is True and pinned_ancestor is False


def test_r5j_runtime_leaf_uses_full_trusted_acl_policy_without_daemon_owner_requirement(monkeypatch):
    provider, read_native, calls, components, _owner_sid, _leaf_native = _fake_runtime_image(monkeypatch)

    gate._runtime_image_proof(provider, read_native)

    leaf = len(components) - 1
    assert all(call[2] is False for call in calls["descriptors"])
    assert all(call[3] is (call[1] < leaf) for call in calls["descriptors"])
    assert calls["security_context"] == ["acquired"]


@pytest.mark.parametrize(
    "mutation",
    ["second_descriptor_changes", "second_owner_class_changes", "second_owner_sid_changes"],
)
def test_r5j_second_chain_descriptor_owner_classification_and_owner_sid_are_exact(monkeypatch, mutation):
    path = gate.TASK_ROOT + r"\source-r14\module.py"
    components = gate._runtime_component_paths(path)
    provider, _read_native, _calls, _components, _owner_sid, _leaf_native = _fake_runtime_image(
        monkeypatch, path=path, **{mutation: {0}},
    )

    with pytest.raises(gate.GateError, match="path, identity-domain, descriptor, or owner changed") as caught:
        gate._r5j_private_path_proof(provider, path, directory=False)
    assert json.dumps(components[0]) in str(caught.value)


def test_r5j_second_chain_private_volume_serial_mismatch_fails_in_its_own_domain(monkeypatch):
    provider, _read_native, _calls, components, _owner_sid, _leaf_native = _fake_runtime_image(
        monkeypatch, second_volume_changes={0},
    )

    with pytest.raises(gate.GateError, match="ACL-verified volume") as caught:
        gate._r5j_private_path_proof(provider, gate.PYTHON_EXE, directory=False)

    assert json.dumps(components[0]) in str(caught.value)


@pytest.mark.parametrize("component_role", ["private_root", "private_descendant", "private_leaf"])
def test_r5j_private_root_descendant_and_leaf_legacy_file_identity_remain_exact(monkeypatch, component_role):
    path = {
        "private_root": gate.TASK_ROOT,
        "private_descendant": gate.TASK_ROOT + r"\source-r14",
        "private_leaf": gate.TASK_ROOT + r"\source-r14\module.py",
    }[component_role]
    directory = component_role != "private_leaf"
    components = gate._runtime_component_paths(path)
    index = len(components) - 1
    provider, _read_native, _calls, _components, _owner_sid, _leaf_native = _fake_runtime_image(
        monkeypatch, path=path, directory=directory,
        attributes=gate.FILE_ATTRIBUTE_DIRECTORY if directory else 0x20,
        second_identity_changes={index},
    )

    with pytest.raises(gate.GateError, match="identity-domain, descriptor, or owner changed") as caught:
        gate._r5j_private_path_proof(provider, path, directory=directory)
    assert json.dumps(components[index]) in str(caught.value)


@pytest.mark.parametrize("component_role", ["private_root", "private_descendant", "private_leaf"])
def test_r5j_private_root_descendant_and_leaf_security_descriptors_remain_exact(monkeypatch, component_role):
    path = {
        "private_root": gate.TASK_ROOT,
        "private_descendant": gate.TASK_ROOT + r"\source-r14",
        "private_leaf": gate.TASK_ROOT + r"\source-r14\module.py",
    }[component_role]
    directory = component_role != "private_leaf"
    components = gate._runtime_component_paths(path)
    index = len(components) - 1
    provider, _read_native, _calls, _components, _owner_sid, _leaf_native = _fake_runtime_image(
        monkeypatch, path=path, directory=directory,
        attributes=gate.FILE_ATTRIBUTE_DIRECTORY if directory else 0x20,
        second_descriptor_changes={index},
    )

    with pytest.raises(gate.GateError, match="identity-domain, descriptor, or owner changed") as caught:
        gate._r5j_private_path_proof(provider, path, directory=directory)
    assert json.dumps(components[index]) in str(caught.value)


@pytest.mark.parametrize("component_role", ["private_root", "private_descendant", "private_leaf"])
@pytest.mark.parametrize(
    ("field", "replacement"),
    [("volume_64", 8), ("file_id_128", 999_999), ("size", 65),
     ("creation_time", 12), ("link_count", 2)],
)
def test_r5j_private_root_descendant_and_leaf_preserve_each_non_time_full_field(
        monkeypatch, component_role, field, replacement):
    path = {
        "private_root": gate.TASK_ROOT,
        "private_descendant": gate.TASK_ROOT + r"\source-r14",
        "private_leaf": gate.TASK_ROOT + r"\source-r14\module.py",
    }[component_role]
    directory = component_role != "private_leaf"
    components = gate._runtime_component_paths(path)
    index = len(components) - 1
    provider, _read_native, _calls, _components, _owner_sid, _leaf_native = _fake_runtime_image(
        monkeypatch, path=path, directory=directory,
        attributes=gate.FILE_ATTRIBUTE_DIRECTORY if directory else 0x20,
        second_full_changes={index: {field: replacement}},
    )

    with pytest.raises(gate.GateError, match="pinned component full identity changed") as caught:
        gate._r5j_private_path_proof(provider, path, directory=directory)
    assert json.dumps(components[index]) in str(caught.value)


def test_r5j_pinned_reparse_component_refusal_is_preserved_as_failure(monkeypatch):
    provider, _read_native, calls, components, _owner_sid, _leaf_native = _fake_runtime_image(
        monkeypatch, info_reject_index=2,
    )

    with pytest.raises(gate.GateError, match="reparse-component refusal") as caught:
        gate._r5j_private_path_proof(provider, gate.PYTHON_EXE, directory=False)

    assert json.dumps(components[2]) in str(caught.value)
    assert calls["finish"] and isinstance(calls["finish"][0], gate.GateError)


@pytest.mark.parametrize("bad_time", [True, 1.0, "changed"])
def test_r5j_outside_ancestor_time_types_are_validated_before_they_can_be_masked(monkeypatch, bad_time):
    provider, _read_native, _calls, components, _owner_sid, _leaf_native = _fake_runtime_image(
        monkeypatch, second_full_changes={0: {"change_time": bad_time}},
    )

    with pytest.raises(gate.GateError, match="exact integer") as caught:
        gate._r5j_private_path_proof(provider, gate.PYTHON_EXE, directory=False)
    assert components[0] in str(caught.value)


def test_r5j_private_file_proof_binds_borrowed_original_handle_and_checks_exact_content(monkeypatch, tmp_path):
    path = gate.PYTHON_EXE
    provider, _read_native, calls, _components, _owner_sid, leaf_native = _fake_runtime_image(monkeypatch)
    source = tmp_path / "borrowed-control.bin"
    source.write_bytes(b"python")
    with source.open("rb", buffering=0) as stream:
        info = os.fstat(stream.fileno())
        monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(get_osfhandle=lambda _fd: 9))

        proof = gate._r5j_private_path_proof(
            provider, path, directory=False,
            borrowed_fd=stream.fileno(), borrowed_info=info,
            expected_native_fingerprint=(tuple(leaf_native[0]), *leaf_native[1:]),
            expected_sha256=_hash(b"python"), expected_bytes=6,
        )

    assert proof["source_content_sha256"] == _hash(b"python")
    assert proof["source_bytes"] == 6
    assert proof["path_chain"][-1]["native_full_fingerprint"] == leaf_native
    assert calls["finish"] == [None]


def test_r5j_private_file_proof_rejects_a_borrowed_handle_from_another_leaf(monkeypatch, tmp_path):
    path = gate.PYTHON_EXE
    provider, _read_native, _calls, _components, _owner_sid, _leaf_native = _fake_runtime_image(monkeypatch)
    source = tmp_path / "borrowed-control.bin"
    source.write_bytes(b"python")
    with source.open("rb", buffering=0) as stream:
        info = os.fstat(stream.fileno())
        monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(get_osfhandle=lambda _fd: 8))
        with pytest.raises(gate.GateError, match="original stdio file handle full identity"):
            gate._r5j_private_path_proof(
                provider, path, directory=False, borrowed_fd=stream.fileno(), borrowed_info=info,
                expected_sha256=_hash(b"python"), expected_bytes=6,
            )


def test_r5j_reparse_leaf_is_rejected_before_opening_the_original_api(monkeypatch):
    provider, _read_native, calls, _components, _owner_sid, _leaf_native = _fake_runtime_image(
        monkeypatch, attributes=gate.FILE_ATTRIBUTE_REPARSE_POINT | 0x20,
    )

    with pytest.raises(gate.GateError, match="ordinary object kind"):
        gate._r5j_private_path_proof(provider, gate.PYTHON_EXE, directory=False)
    assert calls["descriptors"] == [] and calls["finish"] == []


@pytest.mark.parametrize("path", [r"\\server\share\file.py", r"\\?\C:\\file.py", "relative.py"])
def test_r5j_rejects_unc_extended_and_relative_paths(path):
    with pytest.raises(gate.GateError, match="ordinary absolute drive path"):
        gate._runtime_component_paths(path)


def test_r5j_identity_projection_ignores_only_recomputed_outside_ancestor_times():
    base = [[7, 90], 100, 10, 11, 12, 1]
    ancestor = {
        "path": r"C:\Users", "directory": True,
        "identity": {"device": 7, "inode": 90},
        "native_full_fingerprint": base,
        "native_full_fingerprint_second": [[7, 90], 100, 10, 21, 22, 1],
        "descriptor_sha256": "a" * 64, "owner_classification": "trusted-host-admin",
        "owner_sid": "S-1-5-32-544", "outside_task_root_ancestor": True,
        "outside_task_root_ancestor_time_differences": {"change_time": {"first": 11, "second": 21}},
    }
    private_root = {
        **ancestor,
        "path": gate.TASK_ROOT, "outside_task_root_ancestor": True,
    }
    base_projection = gate._r5j_identity_projection(ancestor)
    assert base_projection["native_full_fingerprint"] == [
        [7, 90], 100, 10, "<outside-task-root-ancestor-change-time>",
        "<outside-task-root-ancestor-write-time>", 1,
    ]
    assert base_projection == gate._r5j_identity_projection({
        **ancestor,
        "native_full_fingerprint": [[7, 90], 100, 10, 111, 112, 1],
        "native_full_fingerprint_second": [[7, 90], 100, 10, 121, 122, 1],
        "outside_task_root_ancestor_time_differences": {"write_time": {"first": 112, "second": 122}},
    })
    assert base_projection != gate._r5j_identity_projection(private_root)


@pytest.mark.parametrize("field_index", [0, 1, 2, 5])
def test_r5j_identity_projection_preserves_every_nonmutable_ancestor_field(field_index):
    row = {
        "path": r"C:\Users", "directory": True,
        "identity": {"device": 7, "inode": 90},
        "native_full_fingerprint": [[7, 90], 100, 10, 11, 12, 1],
        "descriptor_sha256": "a" * 64,
    }
    changed = json.loads(json.dumps(row))
    if field_index == 0:
        changed["native_full_fingerprint"][0][1] += 1
    else:
        changed["native_full_fingerprint"][field_index] += 1
    assert gate._r5j_identity_projection(row) != gate._r5j_identity_projection(changed)


def test_r5j_sdk_owner_finish_failure_propagates_and_never_becomes_pass(monkeypatch):
    provider, _read_native, _calls, _components, _owner_sid, _leaf_native = _fake_runtime_image(monkeypatch)
    api, hold, _finish = gate._runtime_path_binding_api()

    def failing_finish(_api, primary=None):
        raise RuntimeError("original SDK owner cleanup remains unresolved")

    monkeypatch.setattr(gate, "_runtime_path_binding_api", lambda: (api, hold, failing_finish))
    with pytest.raises(RuntimeError, match="cleanup remains unresolved"):
        gate._r5j_private_path_proof(provider, gate.PYTHON_EXE, directory=False)


def test_r5j_path_proof_bootstraps_exact_r14_sdk_and_finishes_its_original_resource_registry():
    source = Path(gate.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    funcs = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    bootstrap = funcs["_runtime_path_binding_api"]
    imports = [node for node in ast.walk(bootstrap) if isinstance(node, ast.ImportFrom)]
    assert any(node.module == "comsol_mcp._windows_parameter_publish"
               and [alias.name for alias in node.names] == ["_API"] for node in imports)
    assert any(node.module == "comsol_mcp._windows_host_preflight_owner"
               and {alias.name for alias in node.names} == {"hold_original", "finish_original"}
               for node in imports)
    bootstrap_calls = [node for node in ast.walk(bootstrap) if isinstance(node, ast.Call)]
    assert any(isinstance(call.func, ast.Name) and call.func.id == "_API" for call in bootstrap_calls)

    proof = funcs["_r5j_private_path_proof"]
    proof_source = ast.get_source_segment(source, proof)
    assert proof_source is not None
    assert "hold_original(api)" in proof_source
    assert "finish_original(api, primary)" in proof_source
    assert "first_chain = inspect_complete_chain()" in proof_source
    assert "second_chain = inspect_complete_chain()" in proof_source
    assert "provider._windows_volume_has_persistent_acls" in proof_source
    assert "provider._windows_security_context" in proof_source
    assert "provider._windows_original_full_getter" in proof_source
    assert "provider._win32_descriptor_digest" in proof_source
    assert "provider._assert_opened_path_security" not in source


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

