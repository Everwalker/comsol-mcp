"""Local-only controls for the R6D PREP generator and emitted payload."""
from __future__ import annotations

import ast
import hashlib
import ntpath
from pathlib import Path
import sys
import types

import pytest

from tools import prepare_primary_owner_r6d_local as builder
from tools import windows_primary_token_owner_outer_gate_r5j as r5j


def rendered_payload() -> bytes:
    return builder.render_payload({"fixture": True}, b"helper-fixture", b"gate-fixture", b"inner-fixture")


def test_local_generator_has_no_network_or_process_birth_capability():
    tree = ast.parse(builder.RUNNER.read_text(encoding="utf-8"))
    imported, called = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                called.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                called.add(node.func.attr)
    assert not imported & {"subprocess", "socket", "urllib", "requests", "paramiko", "http"}
    assert not called & {"Popen", "run", "check_call", "check_output", "CreateProcessWithTokenW"}
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                   and isinstance(node.func.value, ast.Name) and node.func.value.id == "os"
                   and node.func.attr == "system" for node in ast.walk(tree))
    compile(tree, str(builder.RUNNER), "exec")


def test_payload_compiles_and_targets_the_exact_r6d_gate_and_five_objects():
    source = rendered_payload().decode("utf-8")
    compile(source, "r6d-fixture-payload.py", "exec")
    assert "__CONFIG_B64__" not in source
    assert 'HELPER_PATH = HELPER_DIR + r"\\windows_primary_token_owner_probe_r6d.py"' in source
    assert 'GATE_PATH = HELPER_DIR + r"\\windows_primary_token_owner_outer_gate_r6d.py"' in source
    assert 'AUTHORITY_ROOT + r"\\PREBIRTH_BINDING.json"' in source
    assert 'AUTHORITY_ROOT + r"\\MAIN_APPROVAL.json"' in source
    assert 'cv3-windows-primary-owner-prebirth-binding-r6d/1' in source
    controls = builder.payload_static_controls(source.encode())
    assert controls["compiled"] is True
    assert controls["exclusive_remote_file_objects"] == 5
    assert controls["prewrite_source_audit_is_separate"] is True
    assert controls["final_source_proofs_follow_entry_set"] is True
    assert controls["final_source_proofs_rechecked_after_in_place_finalization"] is True


def _extract_payload_functions():
    source = rendered_payload().decode("utf-8")
    tree = ast.parse(source)
    wanted = {
        "fail", "sha", "canonical", "audit_raw_proof", "collect_source_readback",
        "check_same_source_readback", "directory_entry_snapshot",
        "verify_exact_directory_entries", "verify_authorized_root_entry_delta",
        "root_entry_effect_record",
    }
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    assert {node.name for node in selected} == wanted
    namespace = {
        "hashlib": hashlib, "json": __import__("json"), "Path": Path,
        "sys": sys, "TASK_ROOT": r5j.TASK_ROOT,
        "HELPER_DIR": builder.HELPER_DIR, "AUTHORITY_ROOT": builder.AUTHORITY_ROOT,
        "source_path": lambda group, relative: ntpath.join(r5j.TASK_ROOT, relative),
        "stable_native_bytes": None,
        "budget": lambda: None,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "r6d-payload-controls.py", "exec"), namespace)
    return namespace


class FakePrivateProvider:
    def __init__(self, files):
        self.files = files
        self.epoch = 1
        self.entries = {
            r5j.TASK_ROOT: {"authority-r14": 2001, "source-r14": 2002, "helpers-r14": 2003},
            builder.HELPER_DIR: {},
            builder.AUTHORITY_ROOT: {},
        }

    @staticmethod
    def fingerprint(file_id, *, size=0, change=1, write=1, creation=10):
        return [[1, file_id], size, creation, change, write, 1]


class FakeGate:
    TASK_ROOT = r5j.TASK_ROOT

    @staticmethod
    def _normalize_native_full_fingerprint(value, *, label):
        return r5j._normalize_native_full_fingerprint(value, label=label)

    @staticmethod
    def _r5j_identity_projection(value):
        return r5j._r5j_identity_projection(value)

    @staticmethod
    def _row(path, directory, fingerprint):
        return {
            "path": path, "directory": directory,
            "identity": {"device": 1, "inode": fingerprint[0][1]},
            "info_size": fingerprint[1], "native_full_fingerprint": fingerprint,
            "native_full_fingerprint_second": list(fingerprint),
            "descriptor_sha256": "d" * 64, "owner_classification": "EXPECTED",
            "owner_sid": "S-1-5-21-test",
        }

    def _private_file_proof(self, provider, path, *, expected_sha256, expected_bytes,
                            expected_native_fingerprint=None):
        root_fp = provider.fingerprint(9001, size=len(provider.entries[r5j.TASK_ROOT]) * 32,
                                       change=provider.epoch, write=provider.epoch)
        root = self._row(r5j.TASK_ROOT, True, root_fp)
        raw = provider.files[str(path)]
        file_id = int.from_bytes(hashlib.sha256(str(path).encode()).digest()[:8], "big") or 1
        leaf_fp = provider.fingerprint(file_id, size=len(raw), creation=20, change=20, write=20)
        leaf = self._row(str(path), False, leaf_fp)
        assert hashlib.sha256(raw).hexdigest() == expected_sha256
        assert len(raw) == expected_bytes
        return {"path": str(path), "directory": False, "sha256": expected_sha256,
                "bytes": expected_bytes, "path_chain": [root, leaf]}

    def _r5j_private_path_proof(self, provider, path, *, directory, **_kwargs):
        path = str(path)
        root_fp = provider.fingerprint(9001, size=len(provider.entries[r5j.TASK_ROOT]) * 32,
                                       change=provider.epoch, write=provider.epoch)
        root = self._row(r5j.TASK_ROOT, True, root_fp)
        if path.casefold() == r5j.TASK_ROOT.casefold():
            return {"path": path, "directory": True, "identity": root["identity"], "path_chain": [root]}
        leaf_id = 9002 if path.casefold() == builder.HELPER_DIR.casefold() else 9003
        leaf_fp = provider.fingerprint(leaf_id, size=64, creation=11, change=provider.epoch, write=provider.epoch)
        leaf = self._row(path, directory, leaf_fp)
        return {"path": path, "directory": directory, "identity": leaf["identity"],
                "path_chain": [root, leaf]}


class FakeStateApi:
    def __init__(self, provider):
        self.provider = provider

    def enumerate_directory(self, handle):
        return [{"name": name, "file_id": file_id}
                for name, file_id in self.provider.entries[str(handle)].items()]


class FakeStateChain:
    def __init__(self, api, path):
        self.api, self.root_handle, self.root_id = api, str(path), {"device": 1, "inode": 9001}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def verify(self):
        return None


class FakeStateRestore:
    def __init__(self, provider):
        self.api = FakeStateApi(provider)

    def _chain(self, path):
        return FakeStateChain(self.api, path)


def test_private_taskroot_entry_change_rejects_prewrite_source_snapshot_and_accepts_final_snapshot():
    functions = _extract_payload_functions()
    paths = {}
    closure = {"source175": {}, "helpers": {}}
    stdlib_records = {}
    for group, count, prefix in (("source175", 175, "src"), ("helpers", 4, "hlp")):
        for index in range(count):
            relative = f"{prefix}-{index:03d}.py"
            path = ntpath.join(r5j.TASK_ROOT, relative)
            raw = (group + ":" + relative).encode()
            digest = hashlib.sha256(raw).hexdigest()
            closure[group][relative] = {"bytes": len(raw), "sha256": digest}
            paths[path] = raw
            stdlib_records[group + ":" + relative] = {"sha256": digest, "bytes": len(raw)}
    provider = FakePrivateProvider(paths)
    gate = FakeGate()
    functions["stable_native_bytes"] = lambda path: (
        provider.files[str(path)], hashlib.sha256(provider.files[str(path)]).hexdigest(),
        {"full_native_fingerprint": None},
    )
    prewrite, prewrite_meta = functions["collect_source_readback"](
        gate, provider, closure, stdlib_records, "PREWRITE_AUDIT",
    )
    assert len(prewrite) == 179
    root_before = gate._r5j_private_path_proof(provider, r5j.TASK_ROOT, directory=True)
    state = FakeStateRestore(provider)
    entries_before = functions["directory_entry_snapshot"](state, r5j.TASK_ROOT)

    # Model the authorized creation of exactly the two namespace directories.
    provider.epoch = 2
    provider.entries[r5j.TASK_ROOT].update({"helpers-primary-owner-r6d": 3001,
                                            "authority-primary-owner-r6d": 3002})
    provider.entries[builder.HELPER_DIR] = {
        "windows_primary_token_owner_probe_r6d.py": 4001,
        "windows_primary_token_owner_outer_gate_r6d.py": 4002,
    }
    provider.entries[builder.AUTHORITY_ROOT] = {
        "BINDING.json": 5001, "PREBIRTH_BINDING.json": 5002, "MAIN_APPROVAL.json": 5003,
    }
    entries_after = functions["directory_entry_snapshot"](state, r5j.TASK_ROOT)
    delta = functions["verify_authorized_root_entry_delta"](
        entries_before, entries_after, ("helpers-primary-owner-r6d", "authority-primary-owner-r6d"),
    )
    helper_entries = functions["verify_exact_directory_entries"](
        functions["directory_entry_snapshot"](state, builder.HELPER_DIR),
        ("windows_primary_token_owner_probe_r6d.py", "windows_primary_token_owner_outer_gate_r6d.py"),
        "helper_directory",
    )
    authority_entries = functions["verify_exact_directory_entries"](
        functions["directory_entry_snapshot"](state, builder.AUTHORITY_ROOT),
        ("BINDING.json", "PREBIRTH_BINDING.json", "MAIN_APPROVAL.json"), "authority_root",
    )
    root_after = gate._r5j_private_path_proof(provider, r5j.TASK_ROOT, directory=True)
    effect = functions["root_entry_effect_record"](gate, root_before, root_after, delta)
    assert effect["native_full_fingerprint_before"] != effect["native_full_fingerprint_after"]
    assert effect["changed_fields_recorded_without_projection"]
    assert set(delta["added"]) == {"helpers-primary-owner-r6d", "authority-primary-owner-r6d"}
    assert len(helper_entries) == 2 and len(authority_entries) == 3

    final_readback, final_meta = functions["collect_source_readback"](
        gate, provider, closure, stdlib_records, "FINAL_AFTER_ENTRY_SET",
    )
    # This is the legacy failure: the same strict R5J projection still sees
    # private TaskRoot timestamp changes and rejects the prewrite snapshot.
    assert gate._r5j_identity_projection(prewrite) != gate._r5j_identity_projection(final_readback)
    with pytest.raises(RuntimeError, match="strict raw source/path proof changed"):
        functions["check_same_source_readback"](gate, prewrite, final_readback, "old order")

    # The new post-entry snapshot is itself the expected value and survives an
    # exact post-finalization re-read because no directory entry changes again.
    postfinal, _postfinal_meta = functions["collect_source_readback"](
        gate, provider, closure, stdlib_records, "POST_FINALIZATION_RECHECK",
    )
    equal = functions["check_same_source_readback"](gate, final_readback, postfinal, "final order")
    assert equal["source_path_proofs_sha256"] == final_meta["source_path_proofs_sha256"]
    assert prewrite_meta["source_path_proofs_sha256"] != final_meta["source_path_proofs_sha256"]


def test_exact_root_entry_delta_rejects_unapproved_addition_or_replacement():
    functions = _extract_payload_functions()
    before = {"known": 101}
    assert functions["verify_authorized_root_entry_delta"](
        before, {"known": 101, "helpers-primary-owner-r6d": 102,
                 "authority-primary-owner-r6d": 103},
        ("helpers-primary-owner-r6d", "authority-primary-owner-r6d"),
    )["changed_existing_ids"] == []
    with pytest.raises(RuntimeError, match="entry delta differs"):
        functions["verify_authorized_root_entry_delta"](
            before, {"known": 101, "helpers-primary-owner-r6d": 102, "unexpected": 104},
            ("helpers-primary-owner-r6d", "authority-primary-owner-r6d"),
        )
    with pytest.raises(RuntimeError, match="entry delta differs"):
        functions["verify_authorized_root_entry_delta"](
            before, {"known": 999, "helpers-primary-owner-r6d": 102,
                     "authority-primary-owner-r6d": 103},
            ("helpers-primary-owner-r6d", "authority-primary-owner-r6d"),
        )
