"""Build a local-only, source-reviewed R5K PREP packet.

This builder has no SSH, network, subprocess, approval, or native-child path.
It reads and hashes the frozen inputs and writes one fresh packet directory
plus its exact Main-review request. Existing outputs are a hard stop.
"""
from __future__ import annotations

import base64
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT / "repository"
STATE = REPO / "docs/full_project_continuation_v3/state"
OUTPUT = REPO / "_state/windows-primary-owner-r5k3c-local-prep-20261014"
REQUEST = STATE / "WINDOWS_PRIMARY_TOKEN_OWNER_PREP_MAIN_REVIEW_REQUEST_R5K3C.json"
PLAN = STATE / "WINDOWS_PRIMARY_TOKEN_OWNER_PREP_SOURCE_PLAN_R5K3C.json"
R5J_REVIEW = STATE / "WINDOWS_PRIMARY_TOKEN_OWNER_PREP_MAIN_SOURCE_REVIEW_R5J.json"
R5J_PLAN = STATE / "WINDOWS_PRIMARY_TOKEN_OWNER_OUTER_GATE_SOURCE_PLAN_R5J.json"
GATE = REPO / "tools/windows_primary_token_owner_outer_gate_r5j.py"
GATE_TESTS = REPO / "tests/test_windows_primary_token_owner_outer_gate_r5j.py"
HELPER = REPO / "tools/windows_primary_token_owner_probe.py"
HELPER_TESTS = REPO / "tests/test_windows_primary_token_owner_probe.py"
R5K3_TESTS = REPO / "tests/test_windows_primary_token_owner_r5k_local_package.py"
AUTHORITY_LEASE_R6 = REPO / "tools/windows_primary_owner_authority_root_lease_r6.py"
SSH_WRAPPER = REPO / "tools/windows_primary_owner_r5k_strict_ssh_prep.py"
R4_INNER_BINDING = ROOT / "_state/windows-restored-host-current-20261014/PRIMARY_OWNER_R5H_R4_INNER_BINDING_PROPOSAL.json"
R14_BINDING = ROOT / "_state/windows-restored-host-current-20261014/KERNEL_R14_ACTUAL_READBACK2/remote_files/authority-r14/BINDING.json"
PROTECTED13 = ROOT / "_state/windows-restored-host-current-20261014/PRIMARY_OWNER_R4_PROTECTED13_BEFORE.json"
PROTECTED13_COMPARISON = ROOT / "_state/windows-restored-host-current-20261014/PRIMARY_OWNER_R4_PROTECTED13_COMPARISON.json"
R14_PROTECTED13 = ROOT / "_state/windows-restored-host-current-20261014/KERNEL_R14_PROTECTED13_BEFORE.json"
RESUME = STATE / "RESUME.json"
CONTROL_CANDIDATE = STATE / "W25_CURRENT175_R6_APPLICATION_SOURCE_CANDIDATE_R1.json"
CONTROL_SOURCE_LOCK = REPO / "tools/w26_application_sources_175_r6.lock.json"
CONTROL_INSTALL_REVIEW = STATE / "W25_R6_INSTALL_R1_MAIN_ACTUAL_EVIDENCE_REVIEW_R1.json"

EXPECTED = {
    "r5j_main_source_review": "ec710a57b5376cd15da54275f6b232a4357cd855212b1d641d943a7e2379f5f9",
    "r5j_gate": "4425d5f6e4585648a4cabfe72e12bdafac7fba765747350947ad126e09ac97ca",
    "r5j_gate_controls": "3e7aa1b75c0f4e6455c5bac04e18c642cfb523e9d4a1ca70cc288f73d8670b8b",
    "r4_helper": "8ddae3d7e6390ec31dc47ece0ec279aa2ee662c0aac81e639e0bb2f4bf96cec6",
    "r4_helper_controls": "383fbab00bb5e8d68b18ee709d74d142d097d3c701a44bc001f4668c220bd6c0",
    "r4_inner_binding": "3c6206adff15778827a705729372ad55e16f6047e28759b7479ced5c83e8db76",
    "r14_binding": "075250aa28a0ae33eec9ac414ab0f143c046df090b00613669ffdf0fbf59dabb",
    "r14_closure": "5a486fa543ec9c550be48575ca96de866822c5688e8a52edc4c2e3743a3618b9",
    "protected13": "ea641286bec1332718129f0386f5dc12ac5b9658892b3f0c4079dde9e4f74077",
    "protected13_comparison": "f7518e5f1a55a63da47029cc47beace9432e53a9c4ad81b1140e97c3251511e8",
    "r14_protected13": "4ffc979fc3423c75178c4b8e788743152d0f035253664a4d318f0c48c5c7864d",
    "resume": "af6db45997b9a41c903358c91e57e7a769116ce6228b558fb756eb7c4e2238ab",
    "current175_candidate": "dbdd6244155da998f4a956969fe9080be471639c106edeec75495b55d87cf00b",
    "current175_source_lock": "016166b15329c83e8a0f1175f9cc44e5b0f3cd17a5873c1b6ad3dc77a3e843d1",
    "r6_install_review": "ff07a41477d0f48adab83ac91929ca72a14662b4172f721f0f56428b4201e282",
    "r5k2_manifest": "f3cedb01275101b731d1e60d424c04d01d01ec439486f92b411e4525afc0f352",
    "r5k2_review_request": "dd85918943ce5750775978e308010f271ce13387e66dc40b335fa4dc679795e1",
    "r5k3a_manifest": "ecd284ba9eb912455e3bc122282406e88fb33c23bb266e20d8a99358698a2f07",
    "r5k3a_review_request": "892d071137ee5673320ef151c4d35925d13c2f39b34ad73ba6d951dee47949b1",
    "r5k3b_manifest": "0603dfcf2e14317ac4200d09d0a74e4d83b37b081f1ce0841084b65f907bf384",
    "r5k3b_review_request": "6155374ee4105c5d5c3038aa1320130726dc56be8e2d81f57cbc4cea2ea5555b",
}

REMOTE = {
    "task_root": r"C:\Users\Everwalker\AppData\Local\cvw26-current-9f07a952265c",
    "python_executable": r"C:\Users\Everwalker\AppData\Local\Programs\Python\Python312-comsol-mcp\python.exe",
    "python_version": "3.12.10",
    "expected_user_sid": "S-1-5-21-2844656816-882443001-4055095909-1001",
    "helper_directory": r"C:\Users\Everwalker\AppData\Local\cvw26-current-9f07a952265c\helpers-primary-owner-r1",
    "authority_root": r"C:\Users\Everwalker\AppData\Local\cvw26-current-9f07a952265c\authority-primary-owner-r1",
}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def read_exact(path: Path, expected_sha: str | None = None) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"input is absent, not a regular file, or a symlink: {path}")
    raw = path.read_bytes()
    if expected_sha is not None and sha(raw) != expected_sha:
        raise RuntimeError(f"input hash changed: {path}")
    return raw


def b64_expression(raw: bytes) -> str:
    encoded = base64.b64encode(raw).decode("ascii")
    chunks = [encoded[offset:offset + 96] for offset in range(0, len(encoded), 96)]
    return "(" + ",\n        ".join(repr(chunk) for chunk in chunks) + ",)"


PAYLOAD_TEMPLATE = r'''# Generated R5K bounded Windows PREP payload; this is not an execution approval.
from __future__ import annotations
import base64, ctypes, hashlib, json, os, sys, time, types
from pathlib import Path

CONFIG = json.loads(base64.b64decode(__CONFIG_B64__).decode("utf-8", "strict"))
HELPER_BYTES = base64.b64decode("".join(__HELPER_B64__).encode("ascii"), validate=True)
GATE_BYTES = base64.b64decode("".join(__GATE_B64__).encode("ascii"), validate=True)
INNER_BYTES = base64.b64decode("".join(__INNER_B64__).encode("ascii"), validate=True)
TASK_ROOT = CONFIG["task_root"]
PYTHON_EXE = CONFIG["python_executable"]
PYTHON_VERSION = CONFIG["python_version"]
HELPER_DIR = CONFIG["helper_directory"]
AUTHORITY_ROOT = CONFIG["authority_root"]
HELPER_PATH = HELPER_DIR + r"\windows_primary_token_owner_probe.py"
GATE_PATH = HELPER_DIR + r"\windows_primary_token_owner_outer_gate.py"
INNER_PATH = AUTHORITY_ROOT + r"\BINDING.json"
PREBIRTH_PATH = AUTHORITY_ROOT + r"\PREBIRTH_BINDING.json"
APPROVAL_PATH = AUTHORITY_ROOT + r"\MAIN_APPROVAL.json"
ONCE_PATH = AUTHORITY_ROOT + r"\ONCE_USED.json"
OUTPUT_ROOT = TASK_ROOT + r"\primary-owner-probe-r1"
OUTPUT_FILE = OUTPUT_ROOT + r"\owner-probe.bin"
LEASE_EVENTS = OUTPUT_ROOT + r"\OWNER_LEASE_EVENTS.jsonl"
CASES_ROOT = TASK_ROOT + r"\cases-primary-owner-r1"
R14_BINDING_PATH = TASK_ROOT + r"\authority-r14\BINDING.json"
R14_SOURCE_ROOT = TASK_ROOT + r"\source-r14"
R14_HELPER_ROOT = TASK_ROOT + r"\helpers-r14"
MAX_SECONDS = 300
STARTED = time.monotonic()
FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF


def fail(message, **details):
    suffix = " " + json.dumps(details, sort_keys=True, ensure_ascii=False) if details else ""
    raise RuntimeError(message + suffix)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def budget():
    if time.monotonic() - STARTED > MAX_SECONDS:
        fail("bounded PREP payload exceeded its five-minute active budget; no retry")


def attrs(path):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetFileAttributesW.argtypes = [ctypes.c_wchar_p]
    kernel.GetFileAttributesW.restype = ctypes.c_uint32
    return int(kernel.GetFileAttributesW(path))


def stdlib_read(path, maximum=268435456):
    before_attrs = attrs(path)
    if (before_attrs == INVALID_FILE_ATTRIBUTES
            or before_attrs & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)):
        fail("standard-library prehash path is absent, a directory, or a reparse point", path=path)
    target = Path(path)
    if target.is_symlink():
        fail("standard-library prehash rejects a symlink name", path=path)
    try:
        with target.open("rb", buffering=0) as stream:
            before = os.fstat(stream.fileno())
            if getattr(before, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT:
                fail("opened standard-library prehash handle is a reparse point", path=path)
            if before.st_size < 0 or before.st_size > maximum:
                fail("standard-library prehash size exceeds bound", path=path)
            raw = stream.read(maximum + 1)
            after = os.fstat(stream.fileno())
        after_attrs = attrs(path)
    except OSError as exc:
        raise RuntimeError("standard-library prehash open/read failed: " + path) from exc
    sig = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    if (len(raw) > maximum or sig(before) != sig(after) or len(raw) != after.st_size
            or before_attrs != after_attrs or after_attrs & FILE_ATTRIBUTE_REPARSE_POINT):
        fail("standard-library prehash changed during read", path=path)
    return raw


def strict_json(raw, label):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=pairs,
                           parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)))
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(label + " is not strict duplicate-free UTF-8 JSON") from exc
    if not isinstance(value, dict):
        fail(label + " root is not an object")
    return value


def stdlib_closure(binding):
    result = {}
    for group in ("source175", "helpers"):
        records = binding.get(group)
        if not isinstance(records, dict) or not records:
            fail("R14 source closure group is missing", group=group)
        result[group] = {}
        for relative, row in records.items():
            if not isinstance(relative, str) or not isinstance(row, dict):
                fail("R14 source closure row is malformed", group=group)
            count, digest = row.get("bytes"), row.get("sha256")
            if type(count) is not int or count < 0 or not isinstance(digest, str) or len(digest) != 64:
                fail("R14 source closure hash/length is malformed", group=group, path=relative)
            result[group][relative] = {"bytes": count, "sha256": digest}
    return result


def source_path(group, relative):
    root = R14_SOURCE_ROOT if group == "source175" else TASK_ROOT
    return os.path.join(root, relative.replace("/", "\\"))


def load_r5j_gate():
    module = types.ModuleType("r5j_verified_prebirth_gate")
    module.__file__ = GATE_PATH
    module.__package__ = ""
    sys.modules[module.__name__] = module
    exec(compile(GATE_BYTES, GATE_PATH, "exec"), module.__dict__)
    return module


def audit_raw_proof(gate, proof, label):
    if not isinstance(proof, dict):
        fail("private path proof must be an object", label=label)
    chain = proof.get("path_chain")
    if not isinstance(chain, list) or not chain:
        fail("private path proof has no complete raw path chain", label=label)
    audited = 0
    for index, row in enumerate(chain):
        if not isinstance(row, dict):
            fail("private path proof component is malformed", label=label, index=index)
        for name in ("native_full_fingerprint", "native_full_fingerprint_second"):
            if name not in row:
                fail("raw full fingerprint observation is missing", label=label, index=index, field=name)
            value = row[name]
            normalized = gate._normalize_native_full_fingerprint(value, label=f"{label} {index} {name}")
            if type(value) is not list or normalized != value:
                fail("raw full fingerprint was not preserved as its exact validated list", label=label, index=index, field=name)
            audited += 1
    return audited


def add_proof(gate, table, name, proof):
    table[name] = proof
    return audit_raw_proof(gate, proof, name)


def raw_sig(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _verified_owner_receipt_snapshot(receipt, expected_user_sid):
    """Copy only after the original context manager has confirmed restoration."""
    if (not isinstance(receipt, dict) or receipt.get("restored") is not True
            or receipt.get("primary_unchanged") is not True
            or receipt.get("UserSID") != expected_user_sid):
        fail("task-thread owner receipt was read before confirmed context restoration")
    return dict(receipt)


def check_same_raw_proof(gate, before, after, label):
    audit_raw_proof(gate, after, label)
    if before != after:
        fail("strict raw path proof changed after PREBIRTH_BINDING finalization", label=label)
    if gate._r5j_identity_projection(before) != gate._r5j_identity_projection(after):
        fail("canonical path-proof identity changed after PREBIRTH_BINDING finalization", label=label)


def main():
    if os.name != "nt" or sys.flags.optimize != 0:
        fail("R5K requires the exact unoptimized Windows Python runtime")
    if os.path.normcase(os.path.abspath(sys.executable)) != os.path.normcase(PYTHON_EXE):
        fail("Python executable path differs from the exact R5K candidate", actual=sys.executable)
    if sys.version.split()[0] != CONFIG["python_version"]:
        fail("Python version differs from the exact R5K candidate", actual=sys.version)
    if sys.argv != ["-"]:
        fail("payload requires exact stdin-only `python -` invocation with no arguments", argv=sys.argv)
    if sha(HELPER_BYTES) != CONFIG["helper_sha256"] or sha(GATE_BYTES) != CONFIG["gate_sha256"]:
        fail("embedded helper or R5J gate bytes differ from the reviewed exact hashes")
    if sha(INNER_BYTES) != CONFIG["inner_binding_sha256"]:
        fail("embedded inner binding differs from its candidate hash")
    if sha(HELPER_BYTES) != CONFIG["helper_sha256"]:
        fail("embedded helper digest mismatch")

    # Bootstrap only with the standard library: verify the exact R14 binding,
    # canonical 179-member closure and every byte before importing any R14 code.
    r14_raw = stdlib_read(R14_BINDING_PATH, 16 * 1024 * 1024)
    if sha(r14_raw) != CONFIG["r14_binding_sha256"]:
        fail("remote R14 binding hash differs from the frozen exact source")
    r14 = strict_json(r14_raw, "remote R14 binding")
    if os.path.normcase(r14.get("taskroot", "")) != os.path.normcase(TASK_ROOT):
        fail("remote R14 binding targets another TaskRoot")
    closure = stdlib_closure(r14)
    closure_sha = sha(canonical(closure))
    if closure_sha != CONFIG["r14_source_closure_sha256"]:
        fail("remote R14 closure digest differs", actual=closure_sha)
    if sum(len(group) for group in closure.values()) != 179:
        fail("remote R14 source/helper closure is not exactly 179 members")
    stdlib_records = {}
    for group in ("source175", "helpers"):
        for relative, expected in closure[group].items():
            budget()
            full = source_path(group, relative)
            raw = stdlib_read(full)
            digest = sha(raw)
            if digest != expected["sha256"] or len(raw) != expected["bytes"]:
                fail("standard-library source prehash differs from exact R14 closure", group=group, path=full,
                     expected_sha256=expected["sha256"], actual_sha256=digest,
                     expected_bytes=expected["bytes"], actual_bytes=len(raw))
            stdlib_records[group + ":" + relative] = {"sha256": digest, "bytes": len(raw)}

    # Only after the independent 179-member byte pass may the exact source
    # modules be imported and the reviewed R5J proof implementation executed.
    sys.path[:0] = [R14_SOURCE_ROOT, R14_HELPER_ROOT]
    from comsol_mcp import _host_scoped_provider as provider
    from comsol_mcp import _windows_state_restore as state_restore
    from windows_task_thread_owner import stable_native_bytes, task_thread_owner
    expected_modules = {
        provider: os.path.join(R14_SOURCE_ROOT, "comsol_mcp", "_host_scoped_provider.py"),
        state_restore: os.path.join(R14_SOURCE_ROOT, "comsol_mcp", "_windows_state_restore.py"),
    }
    for module, expected_path in expected_modules.items():
        if os.path.normcase(os.path.abspath(module.__file__ or "")) != os.path.normcase(os.path.abspath(expected_path)):
            fail("qualified R14 production module imported from an unexpected location", module=module.__name__)
    owner_module = sys.modules.get("windows_task_thread_owner")
    expected_owner_module = os.path.join(R14_HELPER_ROOT, "windows_task_thread_owner.py")
    if (owner_module is None or os.path.normcase(os.path.abspath(owner_module.__file__ or ""))
            != os.path.normcase(os.path.abspath(expected_owner_module))):
        fail("qualified R14 task-thread owner imported from an unexpected location")
    gate = load_r5j_gate()
    if gate._r14_closure(r14) != closure:
        fail("embedded R5J closure parser differs from the standard-library closure")
    fixed_paths = {
        "TASK_ROOT": TASK_ROOT, "PYTHON_EXE": PYTHON_EXE, "HELPER_DIR": HELPER_DIR,
        "AUTHORITY_ROOT": AUTHORITY_ROOT, "HELPER_PATH": HELPER_PATH, "GATE_PATH": GATE_PATH,
        "INNER_BINDING_PATH": INNER_PATH, "OUTER_BINDING_PATH": PREBIRTH_PATH,
        "APPROVAL_PATH": APPROVAL_PATH, "ONCE_PATH": ONCE_PATH, "OUTPUT_ROOT": OUTPUT_ROOT,
        "OUTPUT_FILE": OUTPUT_FILE, "LEASE_EVENTS": LEASE_EVENTS,
        "R14_BINDING_PATH": R14_BINDING_PATH, "R14_SOURCE_ROOT": R14_SOURCE_ROOT,
        "R14_HELPER_ROOT": R14_HELPER_ROOT,
    }
    for name, expected in fixed_paths.items():
        if getattr(gate, name, None) != expected:
            fail("embedded R5J gate fixed path differs from payload contract", field=name)
    if gate.OUTER_SCHEMA != "cv3-windows-primary-owner-prebirth-binding/3":
        fail("embedded gate schema differs from reviewed R5J")

    # Candidate output, authority, and case paths are absent before any stage write.
    for path in (HELPER_DIR, AUTHORITY_ROOT, ONCE_PATH, OUTPUT_ROOT, OUTPUT_FILE, LEASE_EVENTS, CASES_ROOT):
        gate._assert_path_kind(path, directory=path in (HELPER_DIR, AUTHORITY_ROOT, OUTPUT_ROOT, CASES_ROOT),
                               must_exist=False)
    if attrs(TASK_ROOT) == INVALID_FILE_ATTRIBUTES or attrs(TASK_ROOT) & (
            FILE_ATTRIBUTE_REPARSE_POINT) or not attrs(TASK_ROOT) & FILE_ATTRIBUTE_DIRECTORY:
        fail("TaskRoot must be an existing non-reparse directory")

    raw_audit_count = 0
    source_readback = {}
    for group in ("source175", "helpers"):
        for relative, expected in closure[group].items():
            budget()
            full = source_path(group, relative)
            raw, digest, metadata = stable_native_bytes(Path(full))
            if digest != expected["sha256"] or len(raw) != expected["bytes"]:
                fail("R14 source bytes differ on original-handle read", group=group, path=full)
            proof = gate._private_file_proof(
                provider, full, expected_sha256=digest, expected_bytes=len(raw),
                expected_native_fingerprint=metadata.get("full_native_fingerprint"),
            )
            raw_audit_count += audit_raw_proof(gate, proof, "R14 source " + relative)
            source_readback[relative] = {"content_sha256": digest, "bytes": len(raw), "private_path": proof}
    if len(source_readback) != 179 or len(stdlib_records) != 179:
        fail("complete remote R14 source/helper proof count differs", actual=len(source_readback))
    source_identity = gate._r5j_identity_projection(source_readback)
    source_proofs_sha = sha(canonical(source_identity))
    source_raw_sha = sha(canonical(source_readback))

    python_proof = gate._runtime_image_proof(provider, stable_native_bytes)
    raw_audit_count += audit_raw_proof(gate, python_proof["private_path"], "Python image")
    gate._normalize_native_full_fingerprint(python_proof["native_fingerprint"], label="Python raw native image")
    python_identity_sha = sha(canonical(gate._r5j_identity_projection(python_proof)))
    if python_proof["path"] != PYTHON_EXE:
        fail("Python runtime image differs from the frozen exact runtime image")

    r14_binding_proof = gate._private_file_proof(
        provider, R14_BINDING_PATH, expected_sha256=CONFIG["r14_binding_sha256"], expected_bytes=len(r14_raw),
    )
    raw_audit_count += audit_raw_proof(gate, r14_binding_proof, "R14 binding")
    initial_root_proof = gate._r5j_private_path_proof(provider, TASK_ROOT, directory=True)
    raw_audit_count += audit_raw_proof(gate, initial_root_proof, "TaskRoot prewrite")
    with state_restore._chain(Path(TASK_ROOT)) as root_chain:
        root_chain.verify()
        initial_root_identity = dict(root_chain.root_id)
    gate._validate_restore_chain_identity(r14.get("root_identity", {}), initial_root_identity,
                                         r14.get("root_identity", {}))
    gate._validate_root_acl_descriptor(r14.get("root_security", ""), initial_root_proof)

    # All 179 source/helper bytes and private proofs, the runtime leaf, and the
    # TaskRoot restore-chain identity are frozen before the first candidate write.
    target_objects = {
        "helper": (HELPER_PATH, False), "gate": (GATE_PATH, False),
        "inner": (INNER_PATH, False), "prebirth": (PREBIRTH_PATH, False),
        "approval": (APPROVAL_PATH, False),
    }
    for label, (path, _directory) in target_objects.items():
        if attrs(path) != INVALID_FILE_ATTRIBUTES:
            fail("no-overwrite candidate file exists before PREP", target=label, path=path)
    if attrs(HELPER_DIR) != INVALID_FILE_ATTRIBUTES or attrs(AUTHORITY_ROOT) != INVALID_FILE_ATTRIBUTES:
        fail("no-overwrite candidate directory exists before PREP")

    files = {}
    write_started = False
    try:
        with task_thread_owner() as owner_receipt:
            write_started = True
            os.mkdir(HELPER_DIR)
            os.mkdir(AUTHORITY_ROOT)
            for label, (path, _directory) in target_objects.items():
                files[label] = open(path, "x+b", buffering=0)
            if set(files) != set(target_objects):
                fail("exclusive creation did not produce the exact five remote file objects")
            empty_approval_before = raw_sig(os.fstat(files["approval"].fileno()))
            empty_once_absent = attrs(ONCE_PATH) == INVALID_FILE_ATTRIBUTES
            if not empty_once_absent:
                fail("ONCE_USED appeared during PREP")

            # The directory entry set is now complete. Write only into the
            # three pre-created source objects; approval remains zero bytes.
            for label, raw in (("helper", HELPER_BYTES), ("gate", GATE_BYTES), ("inner", INNER_BYTES)):
                stream = files[label]
                if os.fstat(stream.fileno()).st_size != 0:
                    fail("exclusive PREP file is not empty before in-place population", target=label)
                stream.seek(0)
                if stream.write(raw) != len(raw):
                    fail("in-place PREP file write was short", target=label)
                stream.flush()
                os.fsync(stream.fileno())
                stream.seek(0)
                if stream.read(len(raw) + 1) != raw:
                    fail("in-place PREP file readback differs", target=label)

            directory_proofs = {}
            for label, path in (("task_root", TASK_ROOT), ("helper_directory", HELPER_DIR),
                                ("authority_root", AUTHORITY_ROOT)):
                proof = gate._r5j_private_path_proof(provider, path, directory=True)
                raw_audit_count += audit_raw_proof(gate, proof, label + " post-entry-set")
                directory_proofs[label] = proof

            candidate_file_proofs = {}
            file_map = (("helper_path", "helper"), ("gate_path", "gate"),
                        ("inner_binding_path", "inner"))
            for proof_name, handle_name in file_map:
                stream = files[handle_name]
                raw = {"helper": HELPER_BYTES, "gate": GATE_BYTES, "inner": INNER_BYTES}[handle_name]
                proof = gate._r5j_private_path_proof(
                    provider, stream.name, directory=False, borrowed_fd=stream.fileno(),
                    borrowed_info=os.fstat(stream.fileno()), expected_sha256=sha(raw), expected_bytes=len(raw),
                )
                raw_audit_count += audit_raw_proof(gate, proof, proof_name)
                candidate_file_proofs[proof_name] = proof

            r14_binding_final_proof = gate._private_file_proof(
                provider, R14_BINDING_PATH, expected_sha256=CONFIG["r14_binding_sha256"], expected_bytes=len(r14_raw),
            )
            raw_audit_count += audit_raw_proof(gate, r14_binding_final_proof, "R14 binding final")
            if gate._r5j_identity_projection(r14_binding_final_proof) != gate._r5j_identity_projection(r14_binding_proof):
                fail("R14 binding original private-path proof changed during PREP")

            path_proofs = {
                "task_root": directory_proofs["task_root"],
                "helper_directory": directory_proofs["helper_directory"],
                "authority_root": directory_proofs["authority_root"],
                "helper_path": candidate_file_proofs["helper_path"],
                "gate_path": candidate_file_proofs["gate_path"],
                "inner_binding_path": candidate_file_proofs["inner_binding_path"],
                "r14_binding_path": r14_binding_final_proof,
                "python_executable": python_proof,
                "task_root_windows_state_restore_identity": initial_root_identity,
            }
            source_fingerprints = {
                "source_plan_sha256": CONFIG["r5j_source_plan_sha256"],
                "source_review_sha256": CONFIG["r5j_source_review_sha256"],
                "helper_sha256": CONFIG["helper_sha256"],
                "helper_controls_sha256": CONFIG["helper_controls_sha256"],
                "gate_sha256": CONFIG["gate_sha256"],
                "gate_controls_sha256": CONFIG["gate_controls_sha256"],
                "inner_binding_sha256": CONFIG["inner_binding_sha256"],
                "protected13_manifest_sha256": CONFIG["protected13_manifest_sha256"],
                "current_resume_sha256": CONFIG["current_resume_sha256"],
                "r14_binding_sha256": CONFIG["r14_binding_sha256"],
                "r14_source_closure_sha256": CONFIG["r14_source_closure_sha256"],
                "source_path_proofs_sha256": source_proofs_sha,
                "python_executable_sha256": python_proof["sha256"],
                "python_executable_path_proof_sha256": python_identity_sha,
            }
            outer = {
                "schema": gate.OUTER_SCHEMA,
                "status": "REMOTE_PREPARED_CLOSED",
                "candidate": gate.OUTER_CANDIDATE,
                "identity_domains": gate.ROOT_IDENTITY_DOMAINS,
                "source_fingerprints": source_fingerprints,
                "paths": gate.EXPECTED_PATHS,
                "path_proofs": path_proofs,
                "r14_source_path_readback": source_readback,
                "r14_source_path_readback_count": 179,
                "limits": gate.OUTER_LIMITS,
                "native_execution": gate.OUTER_NATIVE_EXECUTION,
                "closed_authority": gate.OUTER_CLOSED_AUTHORITY,
            }
            gate._validate_outer_envelope(outer)
            outer_raw = gate._canonical_json(outer)
            outer_stream = files["prebirth"]
            if os.fstat(outer_stream.fileno()).st_size != 0:
                fail("PREBIRTH_BINDING placeholder is not empty before finalization")
            outer_stream.seek(0)
            if outer_stream.write(outer_raw) != len(outer_raw):
                fail("in-place PREBIRTH_BINDING finalization was short")
            outer_stream.flush()
            os.fsync(outer_stream.fileno())
            outer_stream.seek(0)
            if outer_stream.read(len(outer_raw) + 1) != outer_raw:
                fail("in-place PREBIRTH_BINDING final readback differs")
            parsed_outer = gate._strict_json(outer_raw, label="new PREBIRTH_BINDING")
            gate._validate_outer_envelope(parsed_outer)
            outer_path_proof = gate._r5j_private_path_proof(
                provider, PREBIRTH_PATH, directory=False, borrowed_fd=outer_stream.fileno(),
                borrowed_info=os.fstat(outer_stream.fileno()), expected_sha256=sha(outer_raw),
                expected_bytes=len(outer_raw),
            )
            raw_audit_count += audit_raw_proof(gate, outer_path_proof, "PREBIRTH_BINDING final")

            approval_stream = files["approval"]
            approval_after = raw_sig(os.fstat(approval_stream.fileno()))
            if (approval_after != empty_approval_before or os.fstat(approval_stream.fileno()).st_size != 0
                    or attrs(ONCE_PATH) != INVALID_FILE_ATTRIBUTES):
                fail("reserved approval object changed or ONCE_USED appeared")
            approval_proof = gate._r5j_private_path_proof(
                provider, APPROVAL_PATH, directory=False, borrowed_fd=approval_stream.fileno(),
                borrowed_info=os.fstat(approval_stream.fileno()), expected_sha256=sha(b""), expected_bytes=0,
            )
            raw_audit_count += audit_raw_proof(gate, approval_proof, "MAIN_APPROVAL empty placeholder")

            for label, path in (("task_root", TASK_ROOT), ("helper_directory", HELPER_DIR),
                                ("authority_root", AUTHORITY_ROOT)):
                proof = gate._r5j_private_path_proof(provider, path, directory=True)
                raw_audit_count += audit_raw_proof(gate, proof, label + " post-finalization")
                check_same_raw_proof(gate, directory_proofs[label], proof, label)
            for path in (ONCE_PATH, OUTPUT_ROOT, OUTPUT_FILE, LEASE_EVENTS, CASES_ROOT):
                if attrs(path) != INVALID_FILE_ATTRIBUTES:
                    fail("forbidden authority/output/cases object appeared during PREP", path=path)
            if any(os.fstat(stream.fileno()).st_size != len(raw) for stream, raw in (
                (files["helper"], HELPER_BYTES), (files["gate"], GATE_BYTES),
                (files["inner"], INNER_BYTES), (files["prebirth"], outer_raw),
            )):
                fail("one or more staged file objects have an unexpected final size")

            staged_records = {}
            for label, raw in (("helper", HELPER_BYTES), ("gate", GATE_BYTES),
                               ("inner", INNER_BYTES), ("prebirth", outer_raw)):
                stream = files[label] if label != "prebirth" else outer_stream
                stream.seek(0)
                readback = stream.read(len(raw) + 1)
                if readback != raw:
                    fail("final same-handle staged readback mismatch", target=label)
                staged_records[label] = {"sha256": sha(readback), "bytes": len(readback)}
        owner_copy = _verified_owner_receipt_snapshot(owner_receipt, CONFIG["expected_user_sid"])
        report = {
            "schema": "cv3-windows-primary-owner-r5k-remote-prep-receipt/1",
            "status": "REMOTE_PREPARED_CLOSED_NO_NATIVE_AUTHORITY",
            "python": {"path": PYTHON_EXE, "version": sys.version.split()[0],
                       "sha256": python_proof["sha256"], "path_proof_sha256": python_identity_sha},
            "r14": {"binding_sha256": sha(r14_raw), "closure_sha256": closure_sha,
                    "source_helper_count": 179, "stdlib_hash_count": len(stdlib_records),
                    "original_handle_proof_count": len(source_readback),
                    "source_path_proofs_sha256": source_proofs_sha,
                    "raw_source_path_proofs_sha256": source_raw_sha,
                    "raw_full_fingerprint_observations_validated": raw_audit_count},
            "remote_objects": {
                "candidate_files": staged_records,
                "gate_target": GATE_PATH,
                "gate_source_sha256": sha(GATE_BYTES),
                "authority_root_proof_identity_sha256": sha(canonical(gate._r5j_identity_projection(directory_proofs["authority_root"]))),
                "authority_root_raw_proof_sha256": sha(canonical(directory_proofs["authority_root"])),
                "empty_main_approval": {"bytes": 0, "sha256": sha(b""),
                                         "raw_proof_sha256": sha(canonical(approval_proof)),
                                         "identity": approval_proof["identity"]},
                "once_absent": True, "output_absent": True, "cases_absent": True,
                "prebirth_raw_proof_sha256": sha(canonical(outer_path_proof)),
            },
            "native_child": {"CreateProcessWithTokenW": "NOT_CALLED", "child_processes": 0},
            "account_privilege_acl_mutations": 0,
            "owner_receipt": owner_copy,
            "budget_seconds": int(time.monotonic() - STARTED),
            "replay": False,
        }
        sys.stdout.write(json.dumps(report, sort_keys=True, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return 0
    finally:
        # Closing the five original preparation file handles is ordinary local
        # resource release. No file/directory is removed or recreated.
        for stream in reversed(list(files.values())):
            try:
                stream.close()
            except BaseException:
                pass


if __name__ == "__main__":
    try:
        result_code = main()
    except BaseException as exc:
        sys.stderr.write(json.dumps({"status": "PREP_FAILED_NO_RETRY", "error_type": type(exc).__name__,
                                     "error": str(exc), "replay": False},
                                    sort_keys=True, ensure_ascii=False) + "\n")
        sys.stderr.flush()
        raise
    raise SystemExit(result_code)
'''


def protected13_check() -> tuple[dict[str, str], bytes, bytes]:
    manifest_raw = read_exact(PROTECTED13, EXPECTED["protected13"])
    comparison_raw = read_exact(PROTECTED13_COMPARISON, EXPECTED["protected13_comparison"])
    manifest = json.loads(manifest_raw)
    if not isinstance(manifest, dict) or len(manifest) != 13:
        raise RuntimeError("protected13 manifest must contain exactly 13 original members")
    for relative, digest in manifest.items():
        current = ROOT / relative
        if sha(read_exact(current)) != digest:
            raise RuntimeError(f"protected upstream member changed: {relative}")
    if sha(read_exact(R14_PROTECTED13)) != EXPECTED["r14_protected13"]:
        raise RuntimeError("frozen R14 protected13 manifest changed")
    if sha(read_exact(RESUME)) != EXPECTED["resume"]:
        raise RuntimeError("current RESUME changed")
    return manifest, manifest_raw, comparison_raw


def local_parity_check(r14_binding: dict) -> dict:
    differing = []
    matching = 0
    for relative, prior in r14_binding["source175"].items():
        raw = read_exact(REPO / relative)
        if len(raw) == prior["bytes"] and sha(raw) == prior["sha256"]:
            matching += 1
        else:
            differing.append(relative)
    for relative, prior in r14_binding["helpers"].items():
        original = Path(prior["original"])
        raw = read_exact(original)
        if len(raw) == prior["bytes"] and sha(raw) == prior["sha256"]:
            matching += 1
        else:
            differing.append(relative)
    expected_different = {
        "comsol_mcp/_control_daemon.py", "comsol_mcp/_g2_registry.py", "comsol_mcp/_g3_results.py",
        "comsol_mcp/_desktop_macos.py", "comsol_mcp/_desktop_service.py",
    }
    if matching != 174 or len(differing) != 5 or set(differing) != expected_different:
        raise RuntimeError(f"local R14 comparison scope changed: matching={matching}, differences={sorted(differing)}")
    return {"matching_members": matching, "different_members": len(differing),
            "different_paths": sorted(differing), "remote_full_179_prehash_still_required": True}


def render_payload(config: dict, helper_raw: bytes, gate_raw: bytes, inner_raw: bytes) -> bytes:
    result = PAYLOAD_TEMPLATE
    replacements = {
        "__CONFIG_B64__": repr(base64.b64encode(canonical(config)).decode("ascii")),
        "__HELPER_B64__": b64_expression(helper_raw),
        "__GATE_B64__": b64_expression(gate_raw),
        "__INNER_B64__": b64_expression(inner_raw),
    }
    for token, replacement in replacements.items():
        result = result.replace(token, replacement)
    if any(token in result for token in ("__CONFIG_B64__", "__HELPER_B64__", "__GATE_B64__", "__INNER_B64__")):
        raise RuntimeError("unfilled payload template marker")
    return result.encode("utf-8")


def payload_static_controls(payload: bytes) -> dict:
    source = payload.decode("utf-8", "strict")
    tree = ast.parse(source, filename="PRIMARY_OWNER_R5K_REMOTE_PREP_SCRIPT.py", mode="exec")
    compile(tree, "PRIMARY_OWNER_R5K_REMOTE_PREP_SCRIPT.py", "exec")
    forbidden_imports = {"subprocess", "socket", "urllib", "requests", "http", "ftplib", "paramiko"}
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
    if imported & forbidden_imports:
        raise RuntimeError(f"remote PREP payload imports network/process modules: {sorted(imported & forbidden_imports)}")
    forbidden_calls = {"Popen", "run", "call", "check_call", "check_output", "system", "CreateProcess",
                       "CreateProcessWithTokenW", "CreateProcessAsUserW", "LogonUserW", "execv", "spawnv",
                       "remove", "unlink", "rmdir", "rename"}
    if called & forbidden_calls:
        raise RuntimeError(f"remote PREP payload contains forbidden process/cleanup calls: {sorted(called & forbidden_calls)}")
    if any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
           and isinstance(node.func.value, ast.Name) and node.func.value.id == "os"
           and node.func.attr in {"remove", "unlink", "rmdir", "replace", "rename", "system"}
           for node in ast.walk(tree)):
        raise RuntimeError("remote PREP payload contains an OS process or filesystem cleanup call")
    prehash_at = source.index('for group in ("source175", "helpers"):')
    gate_import_at = source.index("gate = load_r5j_gate()")
    owner_gate_at = source.index("with task_thread_owner() as owner_receipt:")
    if not prehash_at < gate_import_at < owner_gate_at:
        raise RuntimeError("remote source prehash, exact gate load, and owner-gated writes are out of order")
    normalize_at = source.index("gate._normalize_native_full_fingerprint(value")
    projection_at = source.index("source_identity = gate._r5j_identity_projection(source_readback)")
    if normalize_at > projection_at:
        raise RuntimeError("raw full-fingerprint validation must precede canonical projection")
    if source.count('open(path, "x+b", buffering=0)') != 1 or 'os.mkdir(HELPER_DIR)' not in source:
        raise RuntimeError("remote write surface differs from the exact five-object exclusive PREP design")
    if 'GATE_PATH = HELPER_DIR + r"\\windows_primary_token_owner_outer_gate.py"' not in source:
        raise RuntimeError("remote gate target differs from the exact reviewed hard-coded GATE_PATH")
    owner_with = source.index("with task_thread_owner() as owner_receipt:")
    owner_snapshot = source.index("owner_copy = _verified_owner_receipt_snapshot(owner_receipt,")
    owner_with_line = source[:owner_with].count("\n") + 1
    owner_snapshot_line = source[:owner_snapshot].count("\n") + 1
    owner_with_node = next((node for node in ast.walk(tree)
                            if isinstance(node, ast.With) and node.lineno == owner_with_line), None)
    if (owner_snapshot <= owner_with or owner_with_node is None or owner_with_node.end_lineno is None
            or owner_snapshot_line <= owner_with_node.end_lineno):
        raise RuntimeError("task-thread owner receipt must be copied after the original context manager exits")
    helper_node = next((node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == "_verified_owner_receipt_snapshot"), None)
    if helper_node is None:
        raise RuntimeError("payload lacks the exact post-context restoration receipt gate")
    if "CreateProcessWithTokenW(" in source or "taskkill" in source or "TerminateProcess" in source:
        raise RuntimeError("remote PREP source contains native-process execution or termination")
    return {"compiled": True, "imported_modules": sorted(imported), "forbidden_imports": [],
            "forbidden_calls": [], "exclusive_create_sites": 1, "exclusive_remote_file_objects": 5,
            "source_prehash_precedes_r5j_gate_import_and_owner_stage": True,
            "raw_validation_precedes_projection": True}


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink() or REQUEST.exists() or REQUEST.is_symlink():
        raise RuntimeError("R5K local output or review request already exists; no overwrite/retry")
    source_plan_raw = read_exact(PLAN)
    previous_manifest_raw = read_exact(REPO / "_state/windows-primary-owner-r5k3-local-prep-20261014/PRIMARY_OWNER_R5K_LOCAL_PREP_MANIFEST.json",
                                       EXPECTED["r5k3a_manifest"])
    previous_request_raw = read_exact(STATE / "WINDOWS_PRIMARY_TOKEN_OWNER_PREP_MAIN_REVIEW_REQUEST_R5K3.json",
                                      EXPECTED["r5k3a_review_request"])
    previous_r5k3b_manifest_raw = read_exact(REPO / "_state/windows-primary-owner-r5k3b-local-prep-20261014/PRIMARY_OWNER_R5K_LOCAL_PREP_MANIFEST.json",
                                             EXPECTED["r5k3b_manifest"])
    previous_r5k3b_request_raw = read_exact(STATE / "WINDOWS_PRIMARY_TOKEN_OWNER_PREP_MAIN_REVIEW_REQUEST_R5K3B.json",
                                           EXPECTED["r5k3b_review_request"])
    r5j_review_raw = read_exact(R5J_REVIEW, EXPECTED["r5j_main_source_review"])
    r5j_plan_raw = read_exact(R5J_PLAN)
    helper_raw = read_exact(HELPER, EXPECTED["r4_helper"])
    gate_raw = read_exact(GATE, EXPECTED["r5j_gate"])
    gate_tests_raw = read_exact(GATE_TESTS, EXPECTED["r5j_gate_controls"])
    helper_tests_raw = read_exact(HELPER_TESTS, EXPECTED["r4_helper_controls"])
    r5k3_tests_raw = read_exact(R5K3_TESTS)
    authority_lease_raw = read_exact(AUTHORITY_LEASE_R6)
    ssh_wrapper_raw = read_exact(SSH_WRAPPER)
    compile(authority_lease_raw.decode("utf-8", "strict"), str(AUTHORITY_LEASE_R6), "exec")
    compile(ssh_wrapper_raw.decode("utf-8", "strict"), str(SSH_WRAPPER), "exec")
    r14_raw = read_exact(R14_BINDING, EXPECTED["r14_binding"])
    protected13, protected13_raw, comparison_raw = protected13_check()
    resume_raw = read_exact(RESUME, EXPECTED["resume"])
    r14_binding = json.loads(r14_raw)
    if r14_binding.get("taskroot") != REMOTE["task_root"]:
        raise RuntimeError("frozen R14 binding targets an unexpected TaskRoot")

    # Verify the five-file local R14 difference classification against source
    # bytes without changing or copying any upstream source.
    local_parity = local_parity_check(r14_binding)
    current175_candidate_raw = read_exact(CONTROL_CANDIDATE, EXPECTED["current175_candidate"])
    current175_source_lock_raw = read_exact(CONTROL_SOURCE_LOCK, EXPECTED["current175_source_lock"])
    r6_install_review_raw = read_exact(CONTROL_INSTALL_REVIEW, EXPECTED["r6_install_review"])

    closure = {}
    for group in ("source175", "helpers"):
        closure[group] = {
            name: {"bytes": record["bytes"], "sha256": record["sha256"]}
            for name, record in r14_binding[group].items()
        }
    closure_sha = sha(canonical(closure))
    if closure_sha != EXPECTED["r14_closure"] or sum(map(len, closure.values())) != 179:
        raise RuntimeError("frozen R14 closure does not match 175 source plus 4 helper inputs")

    inner = {
        "schema": "cv3-windows-primary-token-owner-binding/1",
        "task_root": REMOTE["task_root"],
        "python_executable": REMOTE["python_executable"],
        "python_version": REMOTE["python_version"],
        "helper_path": REMOTE["helper_directory"] + r"\windows_primary_token_owner_probe.py",
        "helper_sha256": sha(helper_raw),
        "authority_root": REMOTE["authority_root"],
        "binding_path": REMOTE["authority_root"] + r"\BINDING.json",
        "approval_path": REMOTE["authority_root"] + r"\MAIN_APPROVAL.json",
        "once_path": REMOTE["authority_root"] + r"\ONCE_USED.json",
        "output_root": REMOTE["task_root"] + r"\primary-owner-probe-r1",
        "output_file": REMOTE["task_root"] + r"\primary-owner-probe-r1\owner-probe.bin",
        "expected_user_sid": REMOTE["expected_user_sid"],
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
    inner_raw = canonical(inner)
    r4_inner_raw = read_exact(R4_INNER_BINDING, EXPECTED["r4_inner_binding"])
    if inner_raw != r4_inner_raw:
        raise RuntimeError("R5K inner binding proposal differs from the exact Main-reviewed R4 helper contract")

    source_plan = json.loads(source_plan_raw)
    if source_plan.get("schema") != "cv3-windows-primary-owner-prep-source-plan-r5k3c/1":
        raise RuntimeError("R5K3C source plan has an unexpected schema")
    config = {
        **REMOTE,
        "r5j_source_plan_sha256": sha(r5j_plan_raw),
        "r5j_source_review_sha256": sha(r5j_review_raw),
        "r14_binding_sha256": sha(r14_raw),
        "r14_source_closure_sha256": closure_sha,
        "protected13_manifest_sha256": sha(protected13_raw),
        "protected13_comparison_sha256": sha(comparison_raw),
        "r14_protected13_manifest_sha256": EXPECTED["r14_protected13"],
        "current_resume_sha256": sha(resume_raw),
        "helper_sha256": sha(helper_raw),
        "helper_controls_sha256": sha(helper_tests_raw),
        "gate_sha256": sha(gate_raw),
        "gate_controls_sha256": sha(gate_tests_raw),
        "inner_binding_sha256": sha(inner_raw),
        "local_r14_parity": local_parity,
        "local_package_only": True,
    }
    # The runtime executable digest is measured on the remote Windows host
    # and captured in the candidate binding; local generation cannot assert it.
    payload = render_payload(config, helper_raw, gate_raw, inner_raw)
    payload_controls = payload_static_controls(payload)
    if len(payload) != 219696 or sha(payload) != "bb8d490ac965c830e60599f2865894a6bd0289d5dab59b92b7d1a227ac35676a":
        raise RuntimeError("R5K3C must preserve the exact reviewed 219696-byte PREP payload")
    wrapper_spec = importlib.util.spec_from_file_location("r5k3c_ssh_wrapper_build", SSH_WRAPPER)
    if wrapper_spec is None or wrapper_spec.loader is None:
        raise RuntimeError("strict SSH wrapper cannot be loaded for side-effect-free contract generation")
    wrapper_module = importlib.util.module_from_spec(wrapper_spec)
    wrapper_spec.loader.exec_module(wrapper_module)
    invocation = wrapper_module.invocation_binding(sha(payload), len(payload))
    if (wrapper_module.PAYLOAD_PATH != OUTPUT / "PRIMARY_OWNER_R5K_REMOTE_PREP_SCRIPT.py"
            or wrapper_module.CONTRACT_PATH != OUTPUT / "PRIMARY_OWNER_R5K_PREP_CONTRACT.json"
            or wrapper_module.MANIFEST_PATH != OUTPUT / "PRIMARY_OWNER_R5K_LOCAL_PREP_MANIFEST.json"):
        raise RuntimeError("strict SSH wrapper paths do not bind the unique R5K3C package")
    planned_remote = source_plan.get("remote_prep_contract", {})
    planned_invocation = planned_remote.get("remote_invocation", {})
    if (planned_invocation.get("argv") != invocation["argv"]
            or planned_remote.get("payload_sha256") != sha(payload)
            or planned_remote.get("payload_bytes") != len(payload)
            or planned_invocation.get("current_execution") != "NOT_RUN_NO_SSH_CONNECTION_NO_PAYLOAD_SENT"):
        raise RuntimeError("R5K3C local plan SSH command/payload/scope differs from the strict wrapper")

    contract = {
        "schema": "cv3-windows-primary-owner-r5k3c-prep-contract/1",
        "status": "LOCAL_PREP_PACKET_ONLY_PENDING_MAIN_REVIEW",
        "authority": {"ssh": "NOT_RUN", "remote_write": "NOT_RUN", "native_child": "NOT_AUTHORIZED",
                      "CreateProcessWithTokenW": "NOT_CALLED", "approval": "NOT_CREATED",
                      "once": "ABSENT", "COMSOL_GUI_JVM": "NOT_RUN"},
        "source_plan_r5k3c": {"path": str(PLAN.relative_to(ROOT)), "sha256": sha(source_plan_raw)},
        "native_gate_reviewed_basis": {
            "r5j_source_plan_sha256": sha(r5j_plan_raw), "r5j_main_review_sha256": sha(r5j_review_raw),
            "r5j_gate_sha256": sha(gate_raw), "r5j_gate_controls_sha256": sha(gate_tests_raw),
            "r4_helper_sha256": sha(helper_raw), "r4_helper_controls_sha256": sha(helper_tests_raw),
        },
        "local_input_integrity": {
            "r14_binding_sha256": sha(r14_raw), "r14_source_helper_count": 179,
            "r14_source_helper_closure_sha256": closure_sha,
            "protected13_manifest_sha256": sha(protected13_raw),
            "protected13_comparison_sha256": sha(comparison_raw),
            "current_resume_sha256": sha(resume_raw),
            "r14_protected13_manifest_sha256": sha(read_exact(R14_PROTECTED13)),
            "local_r14_parity": local_parity,
            "current175_candidate_sha256": sha(current175_candidate_raw),
            "current175_source_lock_sha256": sha(current175_source_lock_raw),
            "r6_install_review_sha256": sha(r6_install_review_raw),
        },
        "remote_contract": {
            "target_count": 5,
            "gate_target": REMOTE["helper_directory"] + r"\windows_primary_token_owner_outer_gate.py",
            "gate_source_sha256": sha(gate_raw),
            "remote_helper_sha256": sha(helper_raw),
            "inner_binding_sha256": sha(inner_raw),
            "prebirth_placeholder": "created empty then finalized through the same exclusive file object",
            "approval_placeholder": "zero-byte same-object reservation; never authority content in R5K",
            "once": "must remain absent",
            "future_once_handle_lease": "VERSIONED_R6_SOURCE_CANDIDATE_INCLUDED_LOCALLY_NOT_STAGED_OR_EXECUTED",
            "remote_179_prehash": "required before any of the five remote objects is created",
            "raw_fingerprints": "preserve both native observations; independently normalize each raw six-field list before any canonical projection",
        },
        "remote_invocation": {
            "invocation": invocation,
            "invocation_sha256": sha(canonical(invocation)),
            "strict_ssh_wrapper_path": str(SSH_WRAPPER.relative_to(ROOT)),
            "strict_ssh_wrapper_sha256": sha(ssh_wrapper_raw),
            "authorization_path": str(wrapper_module.AUTHORIZATION_PATH.relative_to(ROOT)),
            "run_evidence_directory": str(wrapper_module.RUN_ROOT.relative_to(ROOT)),
            "required_source_review_schema": "cv3-windows-primary-owner-prep-source-review-r5k3c/1",
            "required_human_authorization_schema": "cv3-windows-primary-owner-r5k3c-human-prep-authorization/1",
            "required_human_authorization_fields": [
                "status", "payload_sha256", "payload_bytes", "manifest_sha256",
                "source_review_request_sha256", "main_source_review_sha256",
                "ssh_invocation_sha256", "remote_write_targets", "remote_write_count",
                "active_cutoff_seconds", "passive_wait_same_original_popen_after_cutoff",
                "resend_or_retry", "native_child_authority", "COMSOL_GUI_JVM_authority",
            ],
            "authorization_file_created_now": False,
            "wrapper_invoked_now": False,
            "stdout_stderr": "Exact raw binary bytes are redirected directly to exclusive local files and hashed after the same original Popen reaches natural terminal.",
            "timeout": "At 120 seconds persist UNKNOWN with the exact Popen/PID, then passively wait on the same object. No signal, kill, close, second payload send, or retry.",
        },
        "future_authority_root_lease_r6": {
            "source_path": str(AUTHORITY_LEASE_R6.relative_to(ROOT)),
            "source_sha256": sha(authority_lease_raw),
            "status": "VERSIONED_LOCAL_SOURCE_CANDIDATE_NOT_STAGED_NOT_EXECUTED",
            "frozen_helper_gate_modified": False,
            "prebirth_rule": "The unchanged R5J strict private-directory full-fingerprint proof must pass first while ONCE_USED is absent; its raw six-field authority-root tuple remains immutable evidence.",
            "handle_route": "On a separate R14 _API/original-owner registry, pin the authority-root parent chain, then NtCreateFile relative to the pinned parent with ShareAccess=0 and exact FILE_OPEN; compare every held component against R5J's original proof. Use that same exclusive root handle for listing and api.create_owned(root_handle, 'ONCE_USED.json') FILE_CREATE with OBJ_DONT_REPARSE.",
            "post_effect": "The one new name naturally changes NTFS authority-root change/write timestamps. Preserve raw pre/post tuples separately, require all identity/non-time fields exact and only the one expected ONCE file ID/name delta, and never substitute the post-effect tuple into the R5J prebirth proof.",
            "lifetime": "Retain the same R14 owner, pinned root and ONCE handles through CreateProcessWithTokenW and natural terminal observation on the exact original PROCESS_INFORMATION process handle. WAIT_TIMEOUT/UNKNOWN retains them for passive wait; no PID reopen/signal/close/retry/replay.",
            "main_review_gate": "Main must decide whether this separately versioned exact side-effect receipt is compatible with the no-relaxation requirement before any later native PREP.",
        },
        "r5k3c_software_controls": {"path": str(R5K3_TESTS.relative_to(ROOT)),
                                    "sha256": sha(r5k3_tests_raw), "affected_test_count": 16},
        "strict_ssh_wrapper": {"path": str(SSH_WRAPPER.relative_to(ROOT)), "sha256": sha(ssh_wrapper_raw),
                               "current_execution": "NOT_RUN"},
        "local_payload": {"file": "PRIMARY_OWNER_R5K_REMOTE_PREP_SCRIPT.py", "sha256": sha(payload), "bytes": len(payload),
                          "execution": "NOT_RUN", "static_controls": payload_controls},
        "local_inner_binding_proposal": {"file": "PRIMARY_OWNER_R5K_INNER_BINDING_PROPOSAL.json",
                                         "sha256": sha(inner_raw), "bytes": len(inner_raw)},
        "write_order": "fresh output directory and local packet files only; no remote operation, no SSH, no approval or once creation",
        "future_stop_conditions": [
            "Main must review the exact six-file R5K3C source scope: local builder, generated payload, contract, versioned R6 authority-root lease source, strict SSH wrapper, and the 16 software controls; verify the supporting binding/manifest/request hashes.",
            "A separate direct human authorization must name the exact final payload SHA before any strict SSH transfer/run.",
            "Any mismatch in exact R14 binding/closure/179 member bytes/proofs, runtime image, path owner/DACL, identity, reparse state, exclusive target absence, raw fingerprint normalization, or same-object readback is fail/no-retry.",
            "No CreateProcessWithTokenW or child is authorized by this PREP packet.",
            "R5K3C does not stage or execute the R6 source. Main must review it and decide any next PREP separately; a new gate/helper integration is required before any child authority.",
            "The strict SSH wrapper remains uninvoked. Review its exact host/argv, single binary stdin payload, direct stdout/stderr evidence files, 120-second UNKNOWN cutoff and passive wait on the same original Popen.",
        ],
    }
    contract_raw = json.dumps(contract, sort_keys=True, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
    inner_proposal_raw = json.dumps(inner, sort_keys=True, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"

    if OUTPUT.exists() or REQUEST.exists():
        raise RuntimeError("local output appeared during validation; no overwrite")
    OUTPUT.mkdir(parents=False, exist_ok=False)
    runner_raw = read_exact(Path(__file__))
    package_files = {
        Path(__file__).name: runner_raw,
        "PRIMARY_OWNER_R5K_REMOTE_PREP_SCRIPT.py": payload,
        "PRIMARY_OWNER_R5K_PREP_CONTRACT.json": contract_raw,
        "PRIMARY_OWNER_R5K_INNER_BINDING_PROPOSAL.json": inner_proposal_raw,
        AUTHORITY_LEASE_R6.name: authority_lease_raw,
        SSH_WRAPPER.name: ssh_wrapper_raw,
        R5K3_TESTS.name: r5k3_tests_raw,
    }
    for name, raw in package_files.items():
        target = OUTPUT / name
        with target.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())

    manifest = {
        "schema": "cv3-windows-primary-owner-r5k3c-local-prep-manifest/1",
        "status": "LOCAL_PACKET_CREATED_REMOTE_PREP_NOT_RUN",
        "candidate": "R5K3C original-Popen lifetime guard and CLI exit correction, preserving the R5K3B payload and source evidence",
        "source_plan_r5k3c": {"path": str(PLAN.relative_to(ROOT)), "sha256": sha(source_plan_raw)},
        "source_review_r5j": {"sha256": sha(r5j_review_raw), "status": "PASS_SOURCE_REVIEW_NO_NATIVE_AUTHORITY"},
        "authority_root_lease_r6": {"path": str(AUTHORITY_LEASE_R6.relative_to(ROOT)),
                                     "sha256": sha(authority_lease_raw),
                                     "status": "LOCAL_SOURCE_CANDIDATE_NOT_STAGED_NOT_EXECUTED"},
        "software_controls_r5k3c": {"path": str(R5K3_TESTS.relative_to(ROOT)),
                                   "sha256": sha(r5k3_tests_raw), "affected_test_count": 16},
        "strict_ssh_wrapper": {"path": str(SSH_WRAPPER.relative_to(ROOT)), "sha256": sha(ssh_wrapper_raw),
                               "status": "SOURCE_ONLY_NOT_INVOKED"},
        "files": {name: {"sha256": sha(raw), "bytes": len(raw)} for name, raw in sorted(package_files.items())},
        "inputs": {
            "r14_binding_sha256": sha(r14_raw), "r14_closure_sha256": closure_sha,
            "protected13_manifest_sha256": sha(protected13_raw),
            "protected13_comparison_sha256": sha(comparison_raw), "current_resume_sha256": sha(resume_raw),
            "r14_protected13_manifest_sha256": sha(read_exact(R14_PROTECTED13)),
            "local_r14_parity": local_parity,
        },
        "evidence_scope": {"runner_generation": "LOCAL_ONLY", "payload_execution": "NOT_RUN",
                           "remote_upload": "NOT_RUN", "remote_prepare": "NOT_RUN",
                           "native_child": "NOT_AUTHORIZED", "COMSOL_GUI_JVM": "NOT_RUN"},
        "self_hash": "Not embedded to avoid self-reference; exact hash is recorded by the review request.",
    }
    manifest_raw = json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
    with (OUTPUT / "PRIMARY_OWNER_R5K_LOCAL_PREP_MANIFEST.json").open("xb") as stream:
        stream.write(manifest_raw)
        stream.flush()
        os.fsync(stream.fileno())

    review_request = {
        "schema": "cv3-windows-primary-owner-prep-main-review-request-r5k3c/1",
        "status": "PENDING_MAIN_SOURCE_REVIEW_NO_REMOTE_AUTHORITY",
        "candidate": "R5K3C original-Popen lifetime guard and CLI exit correction, preserving the R5K3B payload and source evidence",
        "scope": {
            "authorized_now": ["local source/test inspection", "local package generation", "local Python compile/AST controls"],
            "not_authorized": ["SSH", "network", "remote writes", "approval or once creation", "CreateProcessWithTokenW", "any child", "COMSOL", "GUI", "JVM", "account/privilege/ACL mutation"],
        },
        "reviewed_native_basis": {
            "R5J_review_sha256": sha(r5j_review_raw), "R5J_gate_sha256": sha(gate_raw),
            "R5J_gate_controls_sha256": sha(gate_tests_raw), "R4_helper_sha256": sha(helper_raw),
            "R4_helper_controls_sha256": sha(helper_tests_raw),
        },
        "main_source_review_scope_six_files": [
            {"path": str(Path(__file__).relative_to(ROOT)), "sha256": sha(runner_raw)},
            {"path": str((OUTPUT / "PRIMARY_OWNER_R5K_REMOTE_PREP_SCRIPT.py").relative_to(ROOT)), "sha256": sha(payload)},
            {"path": str((OUTPUT / "PRIMARY_OWNER_R5K_PREP_CONTRACT.json").relative_to(ROOT)), "sha256": sha(contract_raw)},
            {"path": str(AUTHORITY_LEASE_R6.relative_to(ROOT)), "sha256": sha(authority_lease_raw)},
            {"path": str(SSH_WRAPPER.relative_to(ROOT)), "sha256": sha(ssh_wrapper_raw)},
            {"path": str(R5K3_TESTS.relative_to(ROOT)), "sha256": sha(r5k3_tests_raw)},
        ],
        "main_source_review_contract": {
            "schema": "cv3-windows-primary-owner-prep-source-review-r5k3c/1",
            "required_status": "PASS_SOURCE_REVIEW_NO_NATIVE_AUTHORITY",
            "source_review_request_sha256": "exact SHA-256 of this request file after generation",
            "candidate_manifest_sha256": sha(manifest_raw),
            "hashes": "exact path-to-SHA-256 mapping of the six entries in main_source_review_scope_six_files",
        },
        "package": {
            "directory": str(OUTPUT.relative_to(ROOT)),
            "manifest_sha256": sha(manifest_raw),
            "runner_sha256": sha(runner_raw),
            "payload_sha256": sha(payload), "payload_bytes": len(payload),
            "payload_static_controls": payload_controls,
            "contract_sha256": sha(contract_raw), "inner_binding_proposal_sha256": sha(inner_proposal_raw),
            "authority_root_lease_r6_sha256": sha(authority_lease_raw),
            "software_controls_sha256": sha(r5k3_tests_raw),
            "source_plan_r5k3c_sha256": sha(source_plan_raw),
        },
        "review_questions": [
            "Confirm the payload performs only the five bounded PREP file/object writes, after exact remote 179-member standard-library and R5J private-path proofs.",
            "Confirm the R5J gate source is staged at its exact hard-coded GATE_PATH (`windows_primary_token_owner_outer_gate.py`).",
            "Confirm raw native fingerprint lists are individually normalized before any R5J canonical identity projection and remain raw in PREBIRTH_BINDING.",
            "Confirm owner receipt is snapshotted only after task_thread_owner exits; the real contextmanager negative/positive control must reject early copy and pass after restoration.",
            "Confirm the empty approval placeholder has no authority, ONCE_USED remains absent, and no native child is executed.",
            "Review the R6 source's exclusive same-object root handle, FILE_CREATE-relative ONCE, strict immutable R5J prebirth proof, raw separate post-effect tuple, and retained-owner natural-terminal lifecycle. Confirm this does not silently relax or replace R5J evidence.",
            "Review the strict SSH wrapper's exact payload hash and argv; confirm it has no current authorization file, is not invoked by the local builder, preserves raw stdout/stderr, and after cutoff passively retains the exact Popen without kill or resend.",
        ],
        "source_plan_sha256": sha(source_plan_raw),
        "supersedes": {"local_r5k2_manifest_sha256": EXPECTED["r5k2_manifest"],
                       "local_r5k2_review_request_sha256": EXPECTED["r5k2_review_request"],
                       "local_r5k3a_manifest_sha256": EXPECTED["r5k3a_manifest"],
                       "local_r5k3a_review_request_sha256": EXPECTED["r5k3a_review_request"],
                       "local_r5k3b_manifest_sha256": EXPECTED["r5k3b_manifest"],
                       "local_r5k3b_review_request_sha256": EXPECTED["r5k3b_review_request"],
                       "reason": "R5K3B preserved payload bb8d490a but Main found CLI SystemExit success was caught as failure and post-Popen journaling/observation failures could unwind before natural terminal. R5K3C installs an unconditional original-Popen owner guard, passive no-resend hold and real local-process fault controls; all prior packets remain immutable and no SSH was run."},
        "main_action_requested": "Review this exact six-file R5K3C source scope and either PASS or provide corrective findings. Any later remote preparation requires separate direct human authorization naming payload SHA bb8d490ac965c830e60599f2865894a6bd0289d5dab59b92b7d1a227ac35676a; this request itself authorizes no SSH or remote writes.",
    }
    request_raw = json.dumps(review_request, sort_keys=True, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
    with REQUEST.open("xb") as stream:
        stream.write(request_raw)
        stream.flush()
        os.fsync(stream.fileno())

    print(json.dumps({"status": "LOCAL_R5K3C_PACKAGE_CREATED",
                      "output_directory": str(OUTPUT), "review_request": str(REQUEST),
                      "payload_sha256": sha(payload), "payload_bytes": len(payload),
                      "manifest_sha256": sha(manifest_raw), "review_request_sha256": sha(request_raw),
                      "remote_or_native_actions": "NOT_RUN"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
