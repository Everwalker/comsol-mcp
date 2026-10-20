"""Software-only controls for the local R5K PREP packet builder."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
from contextlib import contextmanager
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "repository/tools/prepare_primary_owner_r5k_local.py"
PLAN_PATH = ROOT / "repository/docs/full_project_continuation_v3/state/WINDOWS_PRIMARY_TOKEN_OWNER_PREP_SOURCE_PLAN_R5K3C.json"
LEASE_PATH = ROOT / "repository/tools/windows_primary_owner_authority_root_lease_r6.py"
SSH_WRAPPER_PATH = ROOT / "repository/tools/windows_primary_owner_r5k_strict_ssh_prep.py"
SPEC = importlib.util.spec_from_file_location("r5k_builder_test", RUNNER_PATH)
assert SPEC and SPEC.loader
BUILDER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BUILDER
SPEC.loader.exec_module(BUILDER)


def rendered_payload() -> bytes:
    return BUILDER.render_payload({"fixture": "only"}, b"helper-fixture", b"gate-fixture", b"inner-fixture")


def test_local_runner_compiles_and_has_no_network_or_child_process_imports():
    tree = ast.parse(RUNNER_PATH.read_text(encoding="utf-8"))
    imported = set()
    called = set()
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
    assert not imported & {"subprocess", "socket", "urllib", "requests", "paramiko"}
    assert not called & {"Popen", "run", "check_call", "system", "CreateProcessWithTokenW"}
    compile(tree, str(RUNNER_PATH), "exec")


def test_rendered_payload_compiles_without_unfilled_markers():
    payload = rendered_payload()
    text = payload.decode("utf-8")
    compile(text, "r5k-fixture-payload.py", "exec")
    assert "__CONFIG_B64__" not in text
    assert "__HELPER_B64__" not in text
    assert "__GATE_B64__" not in text
    assert "__INNER_B64__" not in text


def test_payload_static_controls_prove_only_fixed_prep_surface():
    controls = BUILDER.payload_static_controls(rendered_payload())
    assert controls["compiled"] is True
    assert controls["exclusive_create_sites"] == 1
    assert controls["exclusive_remote_file_objects"] == 5
    assert controls["source_prehash_precedes_r5j_gate_import_and_owner_stage"] is True
    assert controls["raw_validation_precedes_projection"] is True


def test_payload_requires_stdin_only_exact_windows_runtime_and_has_no_cleanup_or_retry():
    source = rendered_payload().decode("utf-8")
    assert 'if sys.argv != ["-"]' in source
    assert "sys.flags.optimize != 0" in source
    assert "PREP_FAILED_NO_RETRY" in source
    assert "os.remove(" not in source
    assert "os.unlink(" not in source
    assert "os.rmdir(" not in source
    assert "CreateProcessWithTokenW(" not in source
    assert "taskkill" not in source


def test_raw_fingerprint_audit_validates_both_observations_before_projection():
    source = rendered_payload().decode("utf-8")
    validate = source.index("gate._normalize_native_full_fingerprint(value")
    project = source.index("source_identity = gate._r5j_identity_projection(source_readback)")
    assert validate < project
    assert '"native_full_fingerprint_second"' in source
    assert '"raw_source_path_proofs_sha256"' in source


def test_r5j_gate_installs_at_its_hard_coded_absolute_target():
    source = rendered_payload().decode("utf-8")
    assert 'GATE_PATH = HELPER_DIR + r"\\windows_primary_token_owner_outer_gate.py"' in source
    assert "windows_primary_token_owner_outer_gate_r5j.py" not in source
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    targets = plan["remote_prep_contract"]["remote_write_targets"]
    assert targets.count(BUILDER.REMOTE["helper_directory"] + r"\windows_primary_token_owner_outer_gate.py") == 1
    assert not any(path.endswith("windows_primary_token_owner_outer_gate_r5j.py") for path in targets)


def test_payload_creates_all_five_entry_names_before_directory_measurement_and_binding_finalize():
    source = rendered_payload().decode("utf-8")
    create_entries = source.index('open(path, "x+b", buffering=0)')
    first_directory_proof = source.index('("task_root", TASK_ROOT), ("helper_directory", HELPER_DIR)')
    finalize = source.index("outer_stream.write(outer_raw)")
    assert create_entries < first_directory_proof < finalize
    assert '"approval": (APPROVAL_PATH, False)' in source
    assert '"prebirth": (PREBIRTH_PATH, False)' in source
    assert "empty_approval_before" in source
    assert "ONCE_USED appeared during PREP" in source


def test_no_execution_authority_or_unknown_once_lease_is_hidden_in_payload_contract():
    source = rendered_payload().decode("utf-8")
    assert '"child_processes": 0' in source
    assert '"CreateProcessWithTokenW": "NOT_CALLED"' in source
    assert "task_thread_owner()" in source
    contract = json.loads((BUILDER.OUTPUT / "PRIMARY_OWNER_R5K_PREP_CONTRACT.json").read_text()) if BUILDER.OUTPUT.exists() else None
    if contract:
        assert contract["authority"]["native_child"] == "NOT_AUTHORIZED"
        assert contract["future_authority_root_lease_r6"]["status"] == "VERSIONED_LOCAL_SOURCE_CANDIDATE_NOT_STAGED_NOT_EXECUTED"
        assert contract["future_authority_root_lease_r6"]["frozen_helper_gate_modified"] is False


def test_real_contextmanager_rejects_early_owner_copy_and_accepts_after_finally():
    source = rendered_payload().decode("utf-8")
    assert "result_code = main()" in source
    assert "raise SystemExit(result_code)" in source
    assert "raise SystemExit(main())" not in source
    tree = ast.parse(source)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "_verified_owner_receipt_snapshot")
    namespace = {"fail": lambda message: (_ for _ in ()).throw(RuntimeError(message))}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "owner-receipt-control.py", "exec"), namespace)
    snapshot = namespace["_verified_owner_receipt_snapshot"]
    receipt = {"restored": False, "primary_unchanged": True,
               "UserSID": "S-1-5-21-2844656816-882443001-4055095909-1001"}

    @contextmanager
    def real_owner_context():
        try:
            yield receipt
        finally:
            receipt["restored"] = True

    with real_owner_context() as owner_receipt:
        try:
            snapshot(owner_receipt, receipt["UserSID"])
        except RuntimeError:
            early_copy_rejected = True
        else:
            early_copy_rejected = False
    assert early_copy_rejected is True
    final_copy = snapshot(receipt, receipt["UserSID"])
    assert final_copy["restored"] is True
    assert final_copy["primary_unchanged"] is True


def test_r6_lease_state_machine_and_generated_manifest_integrity():
    from repository.tools.windows_primary_owner_authority_root_lease_r6 import (
        AuthorityRootLeaseR6, LeaseError, ONCE_STATUS, WAIT_OBJECT_0, WAIT_TIMEOUT,
        _ACTIVE_LEASES,
    )

    class FakeOps:
        def __init__(self, *, pin_error=False, once_error=False, finish_error=False):
            self.finished = 0
            self.once_calls = 0
            self.cleanup_calls = 0
            self.pin_error = pin_error
            self.once_error = once_error
            self.finish_error = finish_error
            self.effect_started = False

        def pin_and_verify_prebirth(self):
            if self.pin_error:
                raise OSError("synthetic prebirth failure")
            return {"status": "PREBIRTH_ROOT_PINNED_STRICT"}

        def create_once_relative(self, raw, expected_sha256):
            self.once_calls += 1
            if self.once_error:
                self.effect_started = True
                raise OSError("synthetic ambiguous once-create failure")
            return {"status": "ONCE_CREATED_RELATIVE_FILE_CREATE_READBACK_PASS",
                    "create_disposition": "FILE_CREATE", "relative_to_original_root_handle": True,
                    "sha256": expected_sha256, "bytes": len(raw)}

        def finish_original_owner(self):
            if self.finish_error:
                raise OSError("synthetic original-owner close failure")
            self.finished += 1

        def cleanup_before_once(self, cause):
            self.cleanup_calls += 1
            self.finished += 1

    once_raw = json.dumps({"status": ONCE_STATUS}, sort_keys=True).encode()
    once_sha = hashlib.sha256(once_raw).hexdigest()
    ops = FakeOps()
    lease = AuthorityRootLeaseR6(ops)
    lease.pin_after_prebirth()
    lease.consume_once_relative(once_raw, once_sha)
    original_handle = __import__("ctypes").c_void_p(123)
    lease.bind_original_birth(original_handle, 321, 456, r"C:\python.exe")
    lease.observe_wait_timeout(original_handle, WAIT_TIMEOUT)
    assert lease.state == "UNKNOWN_RETAIN_ORIGINAL_HANDLES_NO_REPLAY"
    assert ops.finished == 0
    assert ops.once_calls == 1

    terminal_ops = FakeOps()
    terminal = AuthorityRootLeaseR6(terminal_ops)
    terminal.pin_after_prebirth()
    terminal.consume_once_relative(once_raw, once_sha)
    terminal_handle = __import__("ctypes").c_void_p(124)
    terminal.bind_original_birth(terminal_handle, 322, 457, r"C:\python.exe")
    result = terminal.finish_after_natural_terminal(terminal_handle, WAIT_OBJECT_0, 0)
    assert result["status"] == "TERMINAL_AND_ORIGINAL_OWNER_RELEASED"
    assert terminal_ops.finished == 1

    bad_ops = FakeOps()
    bad = AuthorityRootLeaseR6(bad_ops)
    bad.pin_after_prebirth()
    bad.consume_once_relative(once_raw, once_sha)
    bad_handle = __import__("ctypes").c_void_p(125)
    bad.bind_original_birth(bad_handle, 323, 458, r"C:\python.exe")
    try:
        bad.finish_after_natural_terminal(__import__("ctypes").c_void_p(125), WAIT_OBJECT_0, 0)
    except LeaseError:
        same_handle_object_required = True
    else:
        same_handle_object_required = False
    assert same_handle_object_required is True
    assert bad_ops.finished == 0

    before_once_ops = FakeOps(pin_error=True)
    before_once = AuthorityRootLeaseR6(before_once_ops)
    try:
        before_once.pin_after_prebirth()
    except OSError:
        prebirth_failure_propagated = True
    else:
        prebirth_failure_propagated = False
    assert prebirth_failure_propagated is True
    assert before_once.state == "ABORTED_BEFORE_ONCE_NO_BIRTH"
    assert before_once_ops.cleanup_calls == 1
    assert before_once_ops.once_calls == 0

    effect_error_ops = FakeOps(once_error=True)
    effect_error = AuthorityRootLeaseR6(effect_error_ops)
    effect_error.pin_after_prebirth()
    try:
        effect_error.consume_once_relative(once_raw, once_sha)
    except OSError:
        ambiguous_once_error_propagated = True
    else:
        ambiguous_once_error_propagated = False
    assert ambiguous_once_error_propagated is True
    assert effect_error.state == "UNKNOWN_RETAIN_ORIGINAL_HANDLES_NO_REPLAY"
    assert effect_error_ops.cleanup_calls == 0
    assert effect_error_ops.once_calls == 1
    try:
        effect_error.consume_once_relative(once_raw, once_sha)
    except LeaseError:
        once_retry_refused = True
    else:
        once_retry_refused = False
    assert once_retry_refused is True
    assert effect_error_ops.once_calls == 1

    close_error_ops = FakeOps(finish_error=True)
    close_error = AuthorityRootLeaseR6(close_error_ops)
    close_error.pin_after_prebirth()
    close_error.consume_once_relative(once_raw, once_sha)
    close_handle = __import__("ctypes").c_void_p(126)
    close_error.bind_original_birth(close_handle, 324, 459, r"C:\python.exe")
    try:
        close_error.finish_after_natural_terminal(close_handle, WAIT_OBJECT_0, 0)
    except OSError:
        close_failure_propagated = True
    else:
        close_failure_propagated = False
    assert close_failure_propagated is True
    assert close_error.state == "UNKNOWN_RETAIN_ORIGINAL_HANDLES_NO_REPLAY"
    assert any(item is close_error for item in _ACTIVE_LEASES)

    lease_source = LEASE_PATH.read_text(encoding="utf-8")
    assert "FILE_SHARE_NONE = 0" in lease_source
    assert "self.api.create_owned(self.root_handle, ONCE_LEAF)" in lease_source
    assert "self._finish_original_fn(self.api, None)" in lease_source
    assert "self._verify_parent_chain_after_once()" in lease_source
    assert "root_owner_dacl_rechecked_post_effect" in lease_source
    assert "directory_entry_exact_set_delta" in lease_source
    assert '"authority_root_prebirth_proof_replaced": False' in lease_source
    assert 'self.live_prebirth.get("root_proof")' not in lease_source
    assert "live_authority_root = self.gate._r5j_private_path_proof(" in lease_source
    assert "returns its `root_proof` for TASK_ROOT" in lease_source
    assert "self._mark_output_unknown(output, exc)" in lease_source

    if not BUILDER.OUTPUT.exists():
        return
    manifest_path = BUILDER.OUTPUT / "PRIMARY_OWNER_R5K_LOCAL_PREP_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name, record in manifest["files"].items():
        raw = (BUILDER.OUTPUT / name).read_bytes()
        assert len(raw) == record["bytes"]
        assert hashlib.sha256(raw).hexdigest() == record["sha256"]
    assert "prepare_primary_owner_r5k_local.py" in manifest["files"]
    assert "windows_primary_owner_authority_root_lease_r6.py" in manifest["files"]
    assert "windows_primary_owner_r5k_strict_ssh_prep.py" in manifest["files"]
    assert "test_windows_primary_token_owner_r5k_local_package.py" in manifest["files"]


def test_strict_ssh_wrapper_is_fixed_shell_false_single_popen_and_never_cancels():
    tree = ast.parse(SSH_WRAPPER_PATH.read_text(encoding="utf-8"))
    popen_calls = [node for node in ast.walk(tree)
                   if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id == "popen_factory"]
    assert len(popen_calls) == 1
    popen = popen_calls[0]
    shell = next(keyword.value for keyword in popen.keywords if keyword.arg == "shell")
    assert isinstance(shell, ast.Constant) and shell.value is False
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    factory_index = [arg.arg for arg in main.args.kwonlyargs].index("popen_factory")
    factory_default = main.args.kw_defaults[factory_index]
    assert ast.unparse(factory_default) == "subprocess.Popen"
    called = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute)}
    assert not called & {"kill", "terminate", "send_signal"}
    source = SSH_WRAPPER_PATH.read_text(encoding="utf-8")
    assert '"StrictHostKeyChecking=yes"' in source
    assert '"UpdateHostKeys=no"' in source
    assert '"ControlMaster=no"' in source
    assert '"ControlPath=none"' in source
    assert 'self._communicate(input=payload, timeout=self.active_cutoff_seconds)' in source
    assert "class OriginalPopenOwner" in source
    assert "owner.hold_until_terminal()" in source
    assert "child.communicate(input=payload)" not in source
    assert "EXPECTED_PAYLOAD_SHA256 = \"bb8d490ac965c830e60599f2865894a6bd0289d5dab59b92b7d1a227ac35676a\"" in source
    assert "raise SystemExit(cli_entrypoint())" in source
    assert "raise SystemExit(main())" not in source
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    assert plan["remote_prep_contract"]["remote_invocation"]["argv"] == [
        "/usr/bin/ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
        "-o", "UpdateHostKeys=no", "-o", "ControlMaster=no", "-o", "ControlPath=none",
        "everwalker@192.168.100.2",
        '"C:\\Users\\Everwalker\\AppData\\Local\\Programs\\Python\\Python312-comsol-mcp\\python.exe" -I -B -',
    ]


def test_strict_ssh_timeout_control_retains_same_fake_popen_and_sends_payload_once(tmp_path):
    if not BUILDER.OUTPUT.exists():
        return
    spec = importlib.util.spec_from_file_location("r5k3c_ssh_wrapper_test", SSH_WRAPPER_PATH)
    assert spec and spec.loader
    wrapper = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = wrapper
    spec.loader.exec_module(wrapper)

    manifest_path = BUILDER.OUTPUT / "PRIMARY_OWNER_R5K_LOCAL_PREP_MANIFEST.json"
    request_path = ROOT / "repository/docs/full_project_continuation_v3/state/WINDOWS_PRIMARY_TOKEN_OWNER_PREP_MAIN_REVIEW_REQUEST_R5K3C.json"
    manifest_raw = manifest_path.read_bytes()
    request_raw = request_path.read_bytes()
    request = json.loads(request_raw)
    exact_hashes = {row["path"]: row["sha256"] for row in request["main_source_review_scope_six_files"]}
    review = {
        "schema": "cv3-windows-primary-owner-prep-source-review-r5k3c/1",
        "status": "PASS_SOURCE_REVIEW_NO_NATIVE_AUTHORITY",
        "source_review_request_sha256": hashlib.sha256(request_raw).hexdigest(),
        "candidate_manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "hashes": exact_hashes,
        "synthetic_test_fixture": True,
    }
    review_path = tmp_path / "SOURCE_REVIEW_FIXTURE.json"
    review_raw = json.dumps(review, sort_keys=True, indent=2).encode() + b"\n"
    review_path.write_bytes(review_raw)
    invocation = wrapper.invocation_binding()
    authorization = wrapper.expected_authorization(
        payload_sha256=wrapper.EXPECTED_PAYLOAD_SHA256,
        payload_bytes=wrapper.EXPECTED_PAYLOAD_BYTES,
        manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(),
        request_sha256=hashlib.sha256(request_raw).hexdigest(),
        source_review_sha256=hashlib.sha256(review_raw).hexdigest(),
        invocation_sha256=hashlib.sha256(wrapper.canonical(invocation)).hexdigest(),
    )
    authorization_path = tmp_path / "AUTH_FIXTURE.json"
    authorization_path.write_bytes(json.dumps(authorization, sort_keys=True, indent=2).encode() + b"\n")

    remote = {
        "status": wrapper.EXPECTED_REMOTE_STATUS,
        "r14": {"source_helper_count": 179, "stdlib_hash_count": 179},
        "native_child": {"CreateProcessWithTokenW": "NOT_CALLED", "child_processes": 0},
        "remote_objects": {"once_absent": True, "output_absent": True, "cases_absent": True},
    }
    stdout_bytes = json.dumps(remote, sort_keys=True).encode() + b"\n"
    stderr_bytes = b"synthetic stderr raw\x00\xff\n"
    made = []

    class FakePopen:
        def __init__(self, argv, *, stdin, stdout, stderr, shell, cwd):
            self.pid = 887731
            self.returncode = None
            self.calls = []
            assert argv == wrapper.ssh_argv()
            assert stdin is wrapper.subprocess.PIPE
            assert shell is False
            assert cwd == str(ROOT)
            self.stdout_file, self.stderr_file = stdout, stderr
            self.stdout_file.write(stdout_bytes)
            self.stderr_file.write(stderr_bytes)
            made.append(self)

        def communicate(self, input=None, timeout=None):
            self.calls.append((input, timeout))
            if input is not None:
                assert input == wrapper.read_regular(BUILDER.OUTPUT / "PRIMARY_OWNER_R5K_REMOTE_PREP_SCRIPT.py")
                assert timeout == wrapper.ACTIVE_CUTOFF_SECONDS
                raise wrapper.subprocess.TimeoutExpired(wrapper.ssh_argv(), timeout)
            self.returncode = 0
            return None, None

        def wait(self):
            self.returncode = 0
            return 0

    run_root = tmp_path / "run-evidence"
    rc = wrapper.main(
        popen_factory=FakePopen, run_root=run_root,
        source_review_path=review_path, authorization_path=authorization_path,
    )
    assert rc == 0
    assert len(made) == 1
    assert made[0].calls == [
        (wrapper.read_regular(BUILDER.OUTPUT / "PRIMARY_OWNER_R5K_REMOTE_PREP_SCRIPT.py"),
         wrapper.ACTIVE_CUTOFF_SECONDS),
        (None, None),
    ]
    assert (run_root / "SSH.stdout").read_bytes() == stdout_bytes
    assert (run_root / "SSH.stderr").read_bytes() == stderr_bytes
    states = [json.loads(line) for line in (run_root / "SSH_STATE.jsonl").read_text().splitlines()]
    assert [row["status"] for row in states] == [
        "ORIGINAL_POPEN_BIRTH_OBSERVED", "UNKNOWN_ACTIVE_CUTOFF_SAME_POPEN_RETAINED",
        "NATURAL_TERMINAL_OBSERVED",
    ]
    terminal = json.loads((run_root / "SSH_TERMINAL.json").read_text())
    assert terminal["original_local_ssh_pid"] == 887731
    assert terminal["active_cutoff_exceeded"] is True
    assert terminal["same_original_popen_retained_through_terminal"] is True
    assert terminal["signals_kills_retries_resends"] == 0
    report = json.loads((run_root / "REMOTE_PREP_REPORT.json").read_text())
    assert report["status"] == "PASS_REMOTE_PREP_CLOSED_NO_NATIVE_AUTHORITY"
    assert report["replay"] is False


def _load_ssh_wrapper_for_owner_test():
    spec = importlib.util.spec_from_file_location("r5k3c_owner_guard_test", SSH_WRAPPER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_real_local_popen_birth_journal_failure_never_sends_payload_and_holds_to_terminal(tmp_path):
    wrapper = _load_ssh_wrapper_for_owner_test()
    stdout_path, stderr_path = tmp_path / "birth.out", tmp_path / "birth.err"
    state_path = tmp_path / "birth.state"
    state_path.touch()
    failures = {"ORIGINAL_POPEN_BIRTH_OBSERVED", "NATURAL_TERMINAL_OBSERVED"}

    def state_writer(path, status, **kwargs):
        if status in failures:
            failures.remove(status)
            raise OSError("synthetic journal failure at " + status)
        return wrapper._append_state(path, status, **kwargs)

    script = "import sys; data=sys.stdin.buffer.read(); sys.stdout.buffer.write(data or b'EMPTY'); sys.stdout.flush()"
    with stdout_path.open("wb", buffering=0) as stdout, stderr_path.open("wb", buffering=0) as stderr:
        child = subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.PIPE,
                                 stdout=stdout, stderr=stderr, shell=False)
        owner = wrapper.OriginalPopenOwner(child, state_path=state_path, started=time.monotonic(),
                                           state_writer=state_writer)
        birth_logged = owner.record_state("ORIGINAL_POPEN_BIRTH_OBSERVED")
        if birth_logged and owner.first_cause is None:
            owner.send_payload_once(b"must-not-be-sent")
        else:
            owner.hold_until_terminal()
        owner.hold_until_terminal()
    assert owner.child is child
    assert owner.terminal_observed is True
    assert child.returncode == 0
    assert owner.payload_send_attempted is False
    assert stdout_path.read_bytes() == b"EMPTY"
    assert owner.first_cause is not None and "ORIGINAL_POPEN_BIRTH_OBSERVED" in str(owner.first_cause)
    assert any(item["stage"] == "state_journal:NATURAL_TERMINAL_OBSERVED" for item in owner.diagnostic_errors)


def test_real_local_popen_cutoff_journal_failure_passively_holds_same_process_without_resend(tmp_path):
    wrapper = _load_ssh_wrapper_for_owner_test()
    stdout_path, stderr_path = tmp_path / "cutoff.out", tmp_path / "cutoff.err"
    state_path = tmp_path / "cutoff.state"
    state_path.touch()

    def state_writer(path, status, **kwargs):
        if status == "UNKNOWN_ACTIVE_CUTOFF_SAME_POPEN_RETAINED":
            raise OSError("synthetic cutoff journal failure")
        return wrapper._append_state(path, status, **kwargs)

    script = "import sys,time; data=sys.stdin.buffer.read(); time.sleep(.2); sys.stdout.buffer.write(data); sys.stdout.flush()"
    payload = b"one exact local-only payload\n"
    with stdout_path.open("wb", buffering=0) as stdout, stderr_path.open("wb", buffering=0) as stderr:
        child = subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.PIPE,
                                 stdout=stdout, stderr=stderr, shell=False)
        owner = wrapper.OriginalPopenOwner(child, state_path=state_path, started=time.monotonic(),
                                           active_cutoff_seconds=0.01, state_writer=state_writer)
        owner.record_state("ORIGINAL_POPEN_BIRTH_OBSERVED")
        owner.send_payload_once(payload)
        owner.hold_until_terminal()
    assert owner.child is child
    assert owner.terminal_observed is True
    assert owner.active_cutoff_exceeded is True
    assert owner.payload_send_attempted is True
    assert child.returncode == 0
    assert stdout_path.read_bytes() == payload
    assert owner.first_cause is not None and "cutoff journal failure" in str(owner.first_cause)


def test_real_local_popen_communicate_and_wait_observation_faults_still_hold_original_to_terminal(tmp_path):
    wrapper = _load_ssh_wrapper_for_owner_test()
    stdout_path, stderr_path = tmp_path / "observe.out", tmp_path / "observe.err"
    state_path = tmp_path / "observe.state"
    state_path.touch()
    faults = {"communicate": 2, "wait": 1}

    def fault_hook(operation):
        if faults.get(operation, 0):
            faults[operation] -= 1
            raise OSError("synthetic " + operation + " observation failure")

    script = "import sys; data=sys.stdin.buffer.read(); sys.stdout.buffer.write(data or b'EOF'); sys.stdout.flush()"
    with stdout_path.open("wb", buffering=0) as stdout, stderr_path.open("wb", buffering=0) as stderr:
        child = subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.PIPE,
                                 stdout=stdout, stderr=stderr, shell=False)
        owner = wrapper.OriginalPopenOwner(child, state_path=state_path, started=time.monotonic(),
                                           fault_hook=fault_hook)
        owner.record_state("ORIGINAL_POPEN_BIRTH_OBSERVED")
        owner.send_payload_once(b"must-not-be-sent-after-ambiguous-observation")
        owner.hold_until_terminal()
    assert owner.child is child
    assert owner.terminal_observed is True
    assert child.returncode == 0
    assert faults == {"communicate": 0, "wait": 0}
    assert stdout_path.read_bytes() == b"EOF"
    assert owner.first_cause is not None and "communicate observation failure" in str(owner.first_cause)
    assert any("wait observation failure" in row["error"] for row in owner.diagnostic_errors)


def test_cli_entrypoint_success_returns_zero_in_a_real_local_python_process():
    code = (
        "from repository.tools.windows_primary_owner_r5k_strict_ssh_prep import cli_entrypoint; "
        "raise SystemExit(cli_entrypoint(lambda: 0))"
    )
    child = subprocess.Popen([sys.executable, "-c", code], cwd=str(ROOT),
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, shell=False)
    stdout, stderr = child.communicate(timeout=10)
    assert child.returncode == 0, stderr.decode("utf-8", "replace")
    assert stdout == b""
