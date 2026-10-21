"""Local-only controls for the R6E PREP generator, proof comparison, and wrapper."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import ntpath
from pathlib import Path
import sys
import types

import pytest

from tools import prepare_primary_owner_r6e_local as builder
from tools import windows_primary_token_owner_outer_gate_r5j as r5j
from tools import windows_primary_owner_r6e_strict_ssh_prep as ssh_wrapper


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
        "fail", "sha", "canonical", "audit_raw_proof", "_raw_value_deltas",
        "proof_recheck_diagnostic", "check_same_raw_proof", "collect_source_readback",
        "check_same_source_readback", "directory_entry_snapshot",
        "verify_exact_directory_entries", "verify_authorized_root_entry_delta",
        "root_entry_effect_record",
    }
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    assert {node.name for node in selected} == wanted
    namespace = {
        "hashlib": hashlib, "json": __import__("json"), "Path": Path,
        "sys": sys, "TASK_ROOT": r5j.TASK_ROOT,
        "sha": lambda raw: hashlib.sha256(raw).hexdigest(),
        "canonical": lambda value: (json.dumps(value, sort_keys=True, indent=2,
                                                ensure_ascii=False, allow_nan=False) + "\n").encode(),
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
    # The legacy prewrite snapshot still fails because private TaskRoot time
    # changes are strict under the exact R5J identity projection.
    assert gate._r5j_identity_projection(prewrite) != gate._r5j_identity_projection(final_readback)
    with pytest.raises(RuntimeError, match="canonical source path-proof identity changed"):
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


def _representative_raw_path_proof():
    drive, tail = ntpath.splitdrive(r5j.TASK_ROOT)
    paths = [drive + "\\"]
    current = drive + "\\"
    for part in tail.strip("\\").split("\\"):
        current = ntpath.join(current, part)
        paths.append(current)
    paths.extend([
        ntpath.join(r5j.TASK_ROOT, "source-r14"),
        ntpath.join(r5j.TASK_ROOT, "source-r14", "comsol_mcp"),
        ntpath.join(r5j.TASK_ROOT, "source-r14", "comsol_mcp", "_host_scoped_provider.py"),
    ])
    chain = []
    for index, path in enumerate(paths):
        directory = index != len(paths) - 1
        fingerprint = [[1, 1000 + index], 4096 if directory else 128,
                       100 + index, 200 + index, 300 + index, 1]
        chain.append(FakeGate._row(path, directory, fingerprint))
    leaf = paths[-1]
    return {"path": leaf, "directory": False,
            "identity": {"device": 1, "inode": 1000 + len(paths) - 1},
            "sha256": "a" * 64, "bytes": 128, "path_chain": chain}


def _change_fingerprint(row, field_index, delta=1):
    for key in ("native_full_fingerprint", "native_full_fingerprint_second"):
        value = row[key]
        if field_index == 0:
            value[0][1] += delta
        else:
            value[field_index] += delta


def test_r6d_gate_projection_function_is_exactly_the_unchanged_r5j_source():
    r6d_raw = builder.R6D_GATE.read_bytes()
    r5j_raw = builder.R5J_GATE.read_bytes()
    assert builder._function_source(r6d_raw, "_r5j_identity_projection") == \
        builder._function_source(r5j_raw, "_r5j_identity_projection")


def test_outside_taskroot_ancestor_change_and_write_drift_is_accepted_and_fully_recorded():
    functions = _extract_payload_functions()
    before = _representative_raw_path_proof()
    after = copy.deepcopy(before)
    for path in ("C:\\", r"C:\Users"):
        row = next(item for item in after["path_chain"] if item["path"].casefold() == path.casefold())
        for key in ("native_full_fingerprint", "native_full_fingerprint_second"):
            row[key][3] += 1000
            row[key][4] += 2000
    diagnostic = functions["check_same_raw_proof"](FakeGate(), before, after, "outside ancestors")
    assert diagnostic["status"] == "PASS_UNCHANGED_R5J_IDENTITY_PROJECTION"
    assert diagnostic["canonical_identity_equal"] is True
    assert diagnostic["raw_path_proof_sha256_before"] != diagnostic["raw_path_proof_sha256_after"]
    assert diagnostic["raw_delta_count"] == 8
    assert diagnostic["raw_delta_sha256"] == hashlib.sha256(
        functions["canonical"](diagnostic["raw_deltas"])
    ).hexdigest()
    assert all("before" in row and "after" in row for row in diagnostic["raw_deltas"])


def test_private_taskroot_time_drift_is_rejected_after_both_raw_proofs_are_audited():
    functions = _extract_payload_functions()
    before = _representative_raw_path_proof()
    after = copy.deepcopy(before)
    row = next(item for item in after["path_chain"]
               if item["path"].casefold() == r5j.TASK_ROOT.casefold())
    _change_fingerprint(row, 3, 1)
    with pytest.raises(RuntimeError, match="canonical path-proof identity changed"):
        functions["check_same_raw_proof"](FakeGate(), before, after, "private root")


@pytest.mark.parametrize("mutation", ["file_identity", "creation_time", "size", "security_descriptor"])
def test_outside_ancestor_non_time_identity_or_security_drift_is_rejected(mutation):
    functions = _extract_payload_functions()
    before = _representative_raw_path_proof()
    after = copy.deepcopy(before)
    row = next(item for item in after["path_chain"] if item["path"].casefold() == r"c:\users".casefold())
    if mutation == "file_identity":
        _change_fingerprint(row, 0)
    elif mutation == "creation_time":
        _change_fingerprint(row, 2)
    elif mutation == "size":
        _change_fingerprint(row, 1)
        row["info_size"] += 1
    else:
        row["descriptor_sha256"] = "e" * 64
    with pytest.raises(RuntimeError, match="canonical path-proof identity changed"):
        functions["check_same_raw_proof"](FakeGate(), before, after, mutation)


def test_raw_sentinel_text_is_rejected_before_projection_can_mask_it():
    functions = _extract_payload_functions()
    before = _representative_raw_path_proof()
    after = copy.deepcopy(before)
    row = next(item for item in before["path_chain"] if item["path"].casefold() == r"c:\users".casefold())
    row["native_full_fingerprint"][3] = "<outside-task-root-ancestor-change-time>"
    with pytest.raises(RuntimeError, match="exact integer"):
        functions["check_same_raw_proof"](FakeGate(), before, after, "raw sentinel")


def _wrapper_fixture(tmp_path, payload=b"synthetic R6E payload bytes"):
    payload_path = tmp_path / "payload.py"
    contract_path = tmp_path / "contract.json"
    manifest_path = tmp_path / "manifest.json"
    request_path = tmp_path / "request.json"
    review_path = tmp_path / "review.json"
    authorization_path = tmp_path / "authorization.json"
    payload_sha = hashlib.sha256(payload).hexdigest()
    payload_path.write_bytes(payload)
    manifest = {
        "schema": "cv3-windows-primary-owner-r6e-local-prep-manifest/1",
        "status": "LOCAL_SOURCE_PACKAGE_READY_FOR_MAIN_REVIEW_NO_REMOTE_OR_NATIVE_ACTION",
        "candidate": "synthetic review fixture only",
        "package_files": {ssh_wrapper.PAYLOAD_FILENAME: {"sha256": payload_sha, "bytes": len(payload)}},
    }
    manifest_raw = ssh_wrapper.canonical(manifest)
    manifest_path.write_bytes(manifest_raw)
    invocation = ssh_wrapper.invocation_binding(payload_sha, len(payload))
    contract = {
        "schema": "cv3-windows-primary-owner-r6e-prep-contract/1",
        "status": "LOCAL_PACKAGE_ONLY_PENDING_MAIN_SOURCE_REVIEW_AND_DIRECT_HUMAN_PREP_AUTHORIZATION",
        "local_payload": {"path": ssh_wrapper.PAYLOAD_REPO_PATH,
                          "sha256": payload_sha, "bytes": len(payload)},
        "remote_invocation": {"invocation": invocation,
                              "strict_ssh_wrapper_sha256": hashlib.sha256(
                                  ssh_wrapper.read_regular(ssh_wrapper.Path(__file__).resolve().parents[1]
                                  / "tools/windows_primary_owner_r6e_strict_ssh_prep.py")).hexdigest()},
        "remote_write_targets": [{"role": "target", "path": target}
                                 for target in ssh_wrapper.EXPECTED_TARGETS],
    }
    contract_raw = ssh_wrapper.canonical(contract)
    contract_path.write_bytes(contract_raw)
    files = ssh_wrapper._source_hashes()
    request = {
        "status": "PENDING_MAIN_R6E_PREP_GENERATOR_SOURCE_REVIEW_NO_REMOTE_OR_NATIVE_AUTHORITY",
        "source_hashes": files,
        "main_source_review_scope_files": [{"path": path, "sha256": digest}
                                           for path, digest in sorted(files.items())],
        "artifacts": {"remote_payload": {"path": ssh_wrapper.PAYLOAD_REPO_PATH,
                                          "bytes": len(payload), "sha256": payload_sha}},
    }
    request_raw = ssh_wrapper.canonical(request)
    request_path.write_bytes(request_raw)
    review = {
        "schema": "cv3-windows-primary-owner-r6e-prep-source-review/1",
        "status": "PASS_SOURCE_REVIEW_NO_REMOTE_OR_NATIVE_AUTHORITY",
        "source_review_request_sha256": hashlib.sha256(request_raw).hexdigest(),
        "candidate_manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "hashes": files,
        "synthetic_test_fixture": True,
    }
    review_raw = ssh_wrapper.canonical(review)
    review_path.write_bytes(review_raw)
    authorization = ssh_wrapper.expected_authorization(
        payload_sha256=payload_sha, payload_bytes=len(payload),
        manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(),
        request_sha256=hashlib.sha256(request_raw).hexdigest(),
        source_review_sha256=hashlib.sha256(review_raw).hexdigest(),
        invocation_sha256=hashlib.sha256(ssh_wrapper.canonical(invocation)).hexdigest(),
    )
    authorization_path.write_bytes(ssh_wrapper.canonical(authorization))
    return {
        "payload_path": payload_path, "contract_path": contract_path,
        "manifest_path": manifest_path, "request_path": request_path,
        "source_review_path": review_path, "authorization_path": authorization_path,
        "payload": payload,
    }


def test_strict_wrapper_is_exact_r6e_payload_and_r6d_target_bound_without_real_ssh():
    source = ssh_wrapper.Path(__file__).resolve().parents[1] / "tools/windows_primary_owner_r6e_strict_ssh_prep.py"
    text = source.read_text(encoding="utf-8")
    tree = ast.parse(text)
    popen_calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                   and isinstance(node.func, ast.Name) and node.func.id == "popen_factory"]
    assert len(popen_calls) == 1
    popen = popen_calls[0]
    shell = next(keyword.value for keyword in popen.keywords if keyword.arg == "shell")
    assert isinstance(shell, ast.Constant) and shell.value is False
    called = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute)}
    assert not called & {"kill", "terminate", "send_signal"}
    assert ssh_wrapper.PAYLOAD_FILENAME == "PRIMARY_OWNER_R6E_REMOTE_PREP_SCRIPT.py"
    assert len(ssh_wrapper.EXPECTED_TARGETS) == 5
    assert all("primary-owner-r6d" in item for item in ssh_wrapper.EXPECTED_TARGETS)
    assert "bb8d490ac965c830e60599f2865894a6bd0289d5dab59b92b7d1a227ac35676a" not in text
    assert "r5k3c" not in text.lower()
    invocation = ssh_wrapper.invocation_binding("a" * 64, 4096)
    assert invocation["remote_write_targets"] == ssh_wrapper.EXPECTED_TARGETS
    assert invocation["payload_sha256"] == "a" * 64 and invocation["payload_bytes"] == 4096


def test_wrapper_fake_popen_sends_one_payload_and_retains_same_object_until_natural_terminal(tmp_path):
    fixture = _wrapper_fixture(tmp_path)
    remote = {
        "status": ssh_wrapper.EXPECTED_REMOTE_STATUS,
        "r14": {"source_helper_count": 179, "stdlib_hash_count": 179},
        "native_child": {"CreateProcessWithTokenW": "NOT_CALLED", "child_processes": 0},
        "remote_objects": {"once_absent": True, "output_absent": True, "cases_absent": True},
    }
    remote_bytes = ssh_wrapper.canonical(remote)
    made = []

    class FakePopen:
        def __init__(self, argv, *, stdin, stdout, stderr, shell, cwd):
            assert argv == ssh_wrapper.ssh_argv()
            assert stdin is ssh_wrapper.subprocess.PIPE and shell is False and cwd == str(ssh_wrapper.ROOT)
            self.pid, self.returncode, self.calls = 887741, None, []
            stdout.write(remote_bytes)
            stderr.write(b"synthetic fake-ssh stderr\x00\xff\n")
            made.append(self)

        def communicate(self, input=None, timeout=None):
            self.calls.append((input, timeout))
            if input is not None:
                assert input == fixture["payload"]
                assert timeout == ssh_wrapper.ACTIVE_CUTOFF_SECONDS
                raise ssh_wrapper.subprocess.TimeoutExpired(ssh_wrapper.ssh_argv(), timeout)
            self.returncode = 0
            return None, None

        def wait(self):
            self.returncode = 0
            return 0

    run_root = tmp_path / "strict-ssh-evidence"
    rc = ssh_wrapper.main(
        popen_factory=FakePopen, run_root=run_root,
        payload_path=fixture["payload_path"], contract_path=fixture["contract_path"],
        manifest_path=fixture["manifest_path"], request_path=fixture["request_path"],
        source_review_path=fixture["source_review_path"],
        authorization_path=fixture["authorization_path"],
    )
    assert rc == 0 and len(made) == 1
    assert made[0].calls == [(fixture["payload"], ssh_wrapper.ACTIVE_CUTOFF_SECONDS), (None, None)]
    states = [json.loads(line) for line in (run_root / "SSH_STATE.jsonl").read_text().splitlines()]
    assert [row["status"] for row in states] == [
        "ORIGINAL_POPEN_BIRTH_OBSERVED", "UNKNOWN_ACTIVE_CUTOFF_SAME_POPEN_RETAINED",
        "NATURAL_TERMINAL_OBSERVED",
    ]
    terminal = json.loads((run_root / "SSH_TERMINAL.json").read_text())
    assert terminal["original_local_ssh_pid"] == 887741
    assert terminal["same_original_popen_retained_through_terminal"] is True
    assert terminal["payload_send_attempted_once"] is True
    assert terminal["signals_kills_retries_resends"] == 0


def test_wrapper_rejects_payload_drift_before_popen_or_output_creation(tmp_path):
    fixture = _wrapper_fixture(tmp_path)
    fixture["payload_path"].write_bytes(b"changed after exact manifest")
    calls = []
    run_root = tmp_path / "must-not-exist"
    with pytest.raises(RuntimeError, match="exact R6E manifest payload digest"):
        ssh_wrapper.main(
            popen_factory=lambda *args, **kwargs: calls.append((args, kwargs)),
            run_root=run_root, payload_path=fixture["payload_path"],
            contract_path=fixture["contract_path"], manifest_path=fixture["manifest_path"],
            request_path=fixture["request_path"], source_review_path=fixture["source_review_path"],
            authorization_path=fixture["authorization_path"],
        )
    assert calls == [] and not run_root.exists()
