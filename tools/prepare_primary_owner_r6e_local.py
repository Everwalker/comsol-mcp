"""Build an inert local R6E PREP package with narrow R5J proof comparison.

This generator performs only local reads and exclusive local package writes. It
has no SSH, network, approval-signing, ONCE, or process-birth path.
"""
from __future__ import annotations

import ast
import base64
import collections
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT / "repository"
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
STATE = REPO / "docs/full_project_continuation_v3/state"
OUTPUT = REPO / "_state/windows-primary-owner-r6e-local-prep-r1-20261015"
PLAN = STATE / "WINDOWS_PRIMARY_OWNER_R6E_PREP_SOURCE_PLAN_R1.json"
VALIDATION = STATE / "WINDOWS_PRIMARY_OWNER_R6E_PREP_SOFTWARE_VALIDATION_R1.json"
REQUEST = STATE / "WINDOWS_PRIMARY_OWNER_R6E_PREP_MAIN_REVIEW_REQUEST_R1.json"
JUNIT = STATE / "WINDOWS_PRIMARY_OWNER_R6D_INTEGRATION_PYTEST.xml"
LOCAL_JUNIT = STATE / "WINDOWS_PRIMARY_OWNER_R6E_PREP_LOCAL_CONTROLS_PYTEST_R1.xml"
TEMPLATE = REPO / "tools/windows_primary_owner_r6e_prep_payload_template.py.txt"
RUNNER = REPO / "tools/prepare_primary_owner_r6e_local.py"
CONTROLS = REPO / "tests/test_windows_primary_owner_r6e_local_package.py"
SSH_WRAPPER = REPO / "tools/windows_primary_owner_r6e_strict_ssh_prep.py"
R6D_GATE = REPO / "tools/windows_primary_token_owner_outer_gate_r6d.py"
R6D_HELPER = REPO / "tools/windows_primary_token_owner_probe_r6d.py"
R6D_GATE_CONTROLS = REPO / "tests/test_windows_primary_owner_r6d_integration.py"
R6D_HELPER_CONTROLS = REPO / "tests/test_windows_primary_token_owner_probe_r6d.py"
R6D_SOURCE_PLAN = STATE / "WINDOWS_PRIMARY_OWNER_R6D_INTEGRATION_SOURCE_PLAN.json"
R6D_SOURCE_MANIFEST = STATE / "WINDOWS_PRIMARY_OWNER_R6D_INTEGRATION_SOURCE_MANIFEST.json"
R6D_SOURCE_REVIEW_REQUEST = STATE / "WINDOWS_PRIMARY_OWNER_R6D_INTEGRATION_MAIN_REVIEW_REQUEST.json"
R6B_LEASE = REPO / "tools/windows_primary_owner_authority_root_lease_r6b.py"
R6B_LEASE_CONTROLS = REPO / "tests/test_windows_primary_owner_authority_root_lease_r6b.py"
R5J_REVIEW = STATE / "WINDOWS_PRIMARY_TOKEN_OWNER_PREP_MAIN_SOURCE_REVIEW_R5J.json"
R5J_PLAN = STATE / "WINDOWS_PRIMARY_TOKEN_OWNER_OUTER_GATE_SOURCE_PLAN_R5J.json"
R5J_GATE = REPO / "tools/windows_primary_token_owner_outer_gate_r5j.py"
R6D_GUARD_REVIEW = STATE / "WINDOWS_PRIMARY_OWNER_R6D_MAIN_SOURCE_REVIEW_R1.json"
R6D_PREP_REVIEW_FINDING = STATE / "WINDOWS_PRIMARY_OWNER_R6D_PREP_MAIN_SOURCE_REVIEW_R1.json"
R6D_PREP_PLAN = STATE / "WINDOWS_PRIMARY_OWNER_R6D_PREP_SOURCE_PLAN_R1.json"
R6D_PREP_VALIDATION = STATE / "WINDOWS_PRIMARY_OWNER_R6D_PREP_SOFTWARE_VALIDATION_R1.json"
R6D_PREP_REVIEW_REQUEST = STATE / "WINDOWS_PRIMARY_OWNER_R6D_PREP_MAIN_REVIEW_REQUEST_R1.json"
R6D_PREP_RUNNER = REPO / "tools/prepare_primary_owner_r6d_local.py"
R6D_PREP_TEMPLATE = REPO / "tools/windows_primary_owner_r6d_prep_payload_template.py.txt"
R6D_PREP_CONTROLS = REPO / "tests/test_windows_primary_owner_r6d_local_package.py"
R6D_PREP_MANIFEST = REPO / "_state/windows-primary-owner-r6d-local-prep-r1-20261015/PRIMARY_OWNER_R6D_LOCAL_PREP_MANIFEST.json"
R6D_PREP_PAYLOAD = REPO / "_state/windows-primary-owner-r6d-local-prep-r1-20261015/PRIMARY_OWNER_R6D_REMOTE_PREP_SCRIPT.py"
R6D_PREP_LOCAL_JUNIT = STATE / "WINDOWS_PRIMARY_OWNER_R6D_PREP_LOCAL_CONTROLS_PYTEST_R1.xml"
R14_BINDING = ROOT / "_state/windows-restored-host-current-20261014/KERNEL_R14_ACTUAL_READBACK2/remote_files/authority-r14/BINDING.json"
PROTECTED13 = ROOT / "_state/windows-restored-host-current-20261014/PRIMARY_OWNER_R4_PROTECTED13_BEFORE.json"
PROTECTED13_COMPARISON = ROOT / "_state/windows-restored-host-current-20261014/PRIMARY_OWNER_R4_PROTECTED13_COMPARISON.json"
R14_PROTECTED13 = ROOT / "_state/windows-restored-host-current-20261014/KERNEL_R14_PROTECTED13_BEFORE.json"
RESUME = STATE / "RESUME.json"

EXPECTED = {
    "r6d_plan": "6bc1216db0024c6eb4d61c465ac05eb13d4c9b3b624a5a0b3c9995da3147f7e8",
    "r6d_manifest": "eb8212809de9366278c586fddf607ce147911a8111e1c6325532c8743a117a4f",
    "r6d_review_request": "b30aab705af261e63e641fa7c277fb07b3a7fe06196b29de78db79a63d5442ed",
    "r6d_junit": "4807b786eacf1dc88bbba8a036fed75d036510da935eba4f3d1c9b19a464851b",
    "r6d_gate": "497665208412c7e1f08f6ace1f54ce3b9c4451cfac71e310d167b3d15ddd0318",
    "r6d_helper": "bc9f34ec4259acf49181b30b802903cc343561704ca7ce0f9069c774ea97904a",
    "r6d_gate_controls": "5a6d4af5b39e09b41857d8cb6cfa0871bf0f8d0b59858b717cd6547b8edd4211",
    "r6d_helper_controls": "8d31d4bfe6aa937f961ff27b68655621e5a20d30f8ed8ca7f9c0a5fc11817eeb",
    "r6b_lease": "68bb01164f6c2bb51ecc4924076252409c75d9d9406d654ba09ac76c52126259",
    "r6b_lease_controls": "160469c1279c4f8213cb7d19e69155110639b691e0b2fd7e78d40ede8b6afe61",
    "r5j_review": "ec710a57b5376cd15da54275f6b232a4357cd855212b1d641d943a7e2379f5f9",
    "r5j_plan": "57cda3615c46e5a7cd32ad64396b9ef51d73bfa77e711b709f641c2974036cd2",
    "r5j_gate": "4425d5f6e4585648a4cabfe72e12bdafac7fba765747350947ad126e09ac97ca",
    "r6d_guard_review": "c71c692e713902bda91c853c9e47dd058fb93cc3e18d590bb30a6b8b8b0edaad",
    "r6d_prep_review_finding": "a99e83f4b66c3f7f27fd4573c506a244919b2b1383eed92188a0b102cf319c8c",
    "r6d_prep_plan": "081841306369cc798f1fbca16b1fae7e921a86636c714e550e2f2c2a33149f47",
    "r6d_prep_validation": "d81bf65c7a6fdab85a20d1787b0371e36f17bf1e277b7dcba354efebe890e9c7",
    "r6d_prep_review_request": "1c88043226868048188d72e2ff610f36843cefb311837d03d4920c4a9ec8813d",
    "r6d_prep_manifest": "35da2a2fecb3ed1a05ed3568c6f20a1bfc04e55096a9e501bec780ea66705cc8",
    "r6d_prep_payload": "bce8380587bdc08be9455e204f0351263fb74b3c6847a43924e5e59ecfbad1d4",
    "r6d_prep_local_junit": "cda12fdb4859be1a7c3e6dd721a440d054d295af92454d005b0abdd87a7a8aa3",
    "r6d_prep_runner": "c32cf649ca1f929718406e16bb3cd6c81a40f857f8f71eb10b8894c87a3f4511",
    "r6d_prep_template": "463e60e9eda1138a4757eff9f572277b388f80b980d686e6484407b847a15e36",
    "r6d_prep_controls": "132ee2344cda88655042dc8e9febd762f7d564e69d49bc99ec10b7592d9b9091",
    "r14_binding": "075250aa28a0ae33eec9ac414ab0f143c046df090b00613669ffdf0fbf59dabb",
    "r14_closure": "5a486fa543ec9c550be48575ca96de866822c5688e8a52edc4c2e3743a3618b9",
    "protected13": "ea641286bec1332718129f0386f5dc12ac5b9658892b3f0c4079dde9e4f74077",
    "protected13_comparison": "f7518e5f1a55a63da47029cc47beace9432e53a9c4ad81b1140e97c3251511e8",
    "r14_protected13": "4ffc979fc3423c75178c4b8e788743152d0f035253664a4d318f0c48c5c7864d",
    "resume": "af6db45997b9a41c903358c91e57e7a769116ce6228b558fb756eb7c4e2238ab",
}
REMOTE = {
    "task_root": r"C:\Users\Everwalker\AppData\Local\cvw26-current-9f07a952265c",
    "python_executable": r"C:\Users\Everwalker\AppData\Local\Programs\Python\Python312-comsol-mcp\python.exe",
    "python_version": "3.12.10",
    "expected_user_sid": "S-1-5-21-2844656816-882443001-4055095909-1001",
}
HELPER_DIR = REMOTE["task_root"] + r"\helpers-primary-owner-r6d"
AUTHORITY_ROOT = REMOTE["task_root"] + r"\authority-primary-owner-r6d"
OUTPUT_ROOT = REMOTE["task_root"] + r"\primary-owner-probe-r6d"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def read_exact(path: Path, expected_sha256: str | None = None) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"input is absent, not a regular file, or a symlink: {path}")
    raw = path.read_bytes()
    if expected_sha256 is not None and sha(raw) != expected_sha256:
        raise RuntimeError(f"input hash changed: {path}")
    return raw


def metadata(path: Path) -> dict[str, object]:
    raw = read_exact(path)
    return {"path": str(path.relative_to(ROOT)), "bytes": len(raw), "sha256": sha(raw)}


def write_exclusive(path: Path, raw: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def b64_expression(raw: bytes) -> str:
    value = base64.b64encode(raw).decode("ascii")
    chunks = [value[index:index + 96] for index in range(0, len(value), 96)]
    return "(" + ",\n        ".join(repr(chunk) for chunk in chunks) + ",)"


def render_payload(config: dict, helper: bytes, gate: bytes, inner: bytes) -> bytes:
    text = read_exact(TEMPLATE).decode("utf-8", "strict")
    values = {
        "__CONFIG_B64__": repr(base64.b64encode(canonical(config)).decode("ascii")),
        "__HELPER_B64__": b64_expression(helper),
        "__GATE_B64__": b64_expression(gate),
        "__INNER_B64__": b64_expression(inner),
    }
    for marker, replacement in values.items():
        text = text.replace(marker, replacement)
    if any(marker in text for marker in values):
        raise RuntimeError("R6E payload has an unfilled template marker")
    return text.encode("utf-8")


def _function_source(raw: bytes, name: str) -> str:
    source = raw.decode("utf-8", "strict")
    tree = ast.parse(source)
    matches = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name]
    if len(matches) != 1:
        raise RuntimeError(f"expected one exact source function {name}")
    return ast.get_source_segment(source, matches[0]) or ""


def payload_static_controls(payload: bytes) -> dict:
    source = payload.decode("utf-8", "strict")
    tree = ast.parse(source, filename="PRIMARY_OWNER_R6D_REMOTE_PREP_SCRIPT.py", mode="exec")
    compile(tree, "PRIMARY_OWNER_R6D_REMOTE_PREP_SCRIPT.py", "exec")
    forbidden_imports = {"subprocess", "socket", "urllib", "requests", "http", "ftplib", "paramiko"}
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
    if imported & forbidden_imports:
        raise RuntimeError(f"R6E payload has a network/process import: {sorted(imported & forbidden_imports)}")
    forbidden_calls = {"Popen", "run", "call", "check_call", "check_output", "system", "CreateProcess",
                       "CreateProcessWithTokenW", "CreateProcessAsUserW", "LogonUserW", "execv", "spawnv",
                       "remove", "unlink", "rmdir", "rename"}
    if called & forbidden_calls:
        raise RuntimeError(f"R6E payload has a process/cleanup call: {sorted(called & forbidden_calls)}")
    main_source = source[source.index("def main():"):]
    prehash = main_source.index("stdlib_records = {}")
    gate_load = main_source.index("gate = load_r5j_gate()")
    owner = main_source.index("with task_thread_owner() as owner_receipt:")
    prewrite = main_source.index("prewrite_source_readback, prewrite_source_receipt = collect_source_readback(")
    create_dirs = main_source.index("os.mkdir(HELPER_DIR)")
    populate_inner = main_source.index('("inner", INNER_BYTES)')
    final_source = main_source.index("source_readback, final_source_receipt = collect_source_readback(")
    outer_bind = main_source.index("outer = {")
    final_write = main_source.index("outer_stream.write(outer_raw)")
    postfinal = main_source.index("postfinal_source_readback, postfinal_source_receipt = collect_source_readback(")
    if not (prehash < gate_load < prewrite < owner < create_dirs < populate_inner < final_source < outer_bind < final_write < postfinal):
        raise RuntimeError("R6E PREP ordering does not preserve prewrite audit then post-entry final proofs")
    normalize = source.index("gate._normalize_native_full_fingerprint(value")
    project = source.index("identity = gate._r5j_identity_projection(records)")
    if not normalize < project:
        raise RuntimeError("raw fingerprints must be validated before canonical projection")
    if source.count('open(path, "x+b", buffering=0)') != 1 or source.count('"prebirth": (PREBIRTH_PATH, False)') != 1:
        raise RuntimeError("R6E PREP must exclusively create its fixed five remote leaves")
    if 'GATE_PATH = HELPER_DIR + r"\\windows_primary_token_owner_outer_gate_r6d.py"' not in source:
        raise RuntimeError("R6E payload remote gate target does not match its unchanged R6D guard path")
    if "check_same_source_readback(" not in source or "directory_entry_snapshot(state_restore, TASK_ROOT)" not in source:
        raise RuntimeError("R6E payload lacks final-source and directory-entry rechecks")
    if "CreateProcessWithTokenW(" in source or "ONCE_USED.json\" contains" in source:
        raise RuntimeError("R6E PREP payload contains native child/approval authority")
    diagnostic_source = _function_source(payload, "proof_recheck_diagnostic")
    if (diagnostic_source.index("audit_raw_proof(gate, before")
            > diagnostic_source.index("gate._r5j_identity_projection(before)")
            or diagnostic_source.index("audit_raw_proof(gate, after")
            > diagnostic_source.index("gate._r5j_identity_projection(after)")):
        raise RuntimeError("R6E must audit both raw proofs before applying the unchanged R5J projection")
    if "before != after" in _function_source(payload, "check_same_raw_proof"):
        raise RuntimeError("R6E raw path comparator may not require byte-for-byte raw equality")
    if "before != after" in _function_source(payload, "check_same_source_readback"):
        raise RuntimeError("R6E source comparator may not require byte-for-byte raw equality")
    if "raw_deltas" not in _function_source(payload, "check_same_source_readback"):
        raise RuntimeError("R6E source recheck must retain raw path deltas diagnostically")
    return {
        "compiled": True, "forbidden_imports": [], "forbidden_calls": [],
        "exclusive_remote_file_objects": 5, "exclusive_create_sites": 1,
        "prewrite_source_audit_is_separate": True,
        "final_source_proofs_follow_entry_set": True,
        "final_source_proofs_rechecked_after_in_place_finalization": True,
        "exact_root_and_candidate_entry_sets_verified_through_r14_api": True,
        "raw_validation_precedes_projection": True,
        "both_raw_observations_audited_before_projection": True,
        "r5j_projection_is_only_path_identity_comparator": True,
        "raw_source_hashes_and_deltas_retained_diagnostically": True,
        "native_child": "NOT_AUTHORIZED_AND_NOT_RUN",
    }


def protected_inputs() -> tuple[dict, bytes, bytes, bytes]:
    manifest_raw = read_exact(PROTECTED13, EXPECTED["protected13"])
    comparison_raw = read_exact(PROTECTED13_COMPARISON, EXPECTED["protected13_comparison"])
    manifest = json.loads(manifest_raw)
    if not isinstance(manifest, dict) or len(manifest) != 13:
        raise RuntimeError("protected13 manifest must contain exactly 13 original members")
    for relative, digest in manifest.items():
        if sha(read_exact(ROOT / relative)) != digest:
            raise RuntimeError(f"protected original member changed: {relative}")
    r14_protected = read_exact(R14_PROTECTED13, EXPECTED["r14_protected13"])
    resume_raw = read_exact(RESUME, EXPECTED["resume"])
    return manifest, manifest_raw, comparison_raw, resume_raw


def local_r14_parity(r14: dict) -> dict:
    differing, matching = [], 0
    for relative, prior in r14["source175"].items():
        raw = read_exact(REPO / relative)
        if len(raw) == prior["bytes"] and sha(raw) == prior["sha256"]:
            matching += 1
        else:
            differing.append(relative)
    for relative, prior in r14["helpers"].items():
        raw = read_exact(Path(prior["original"]))
        if len(raw) == prior["bytes"] and sha(raw) == prior["sha256"]:
            matching += 1
        else:
            differing.append(relative)
    expected = {
        "comsol_mcp/_control_daemon.py", "comsol_mcp/_g2_registry.py", "comsol_mcp/_g3_results.py",
        "comsol_mcp/_desktop_macos.py", "comsol_mcp/_desktop_service.py",
    }
    if matching != 174 or len(differing) != 5 or set(differing) != expected:
        raise RuntimeError("local R14 parity differs from the previously classified 174/179 state")
    return {"matching_members": 174, "different_members": 5, "different_paths": sorted(differing),
            "remote_full_179_prehash_and_path_proofs_required": True}


def _junit_summary(raw: bytes, expected_tests: int, label: str) -> dict:
    from io import BytesIO
    root = ET.parse(BytesIO(raw)).getroot()
    if root.tag != "testsuites":
        raise RuntimeError(label + " JUnit root is not testsuites")
    suites = root.findall(".//testsuite")
    by_file = dict(collections.Counter(row.get("classname", "") for row in root.findall(".//testcase")))
    total = sum(int(row.get("tests", "0")) for row in suites)
    failed = sum(int(row.get("failures", "0")) for row in suites)
    errors = sum(int(row.get("errors", "0")) for row in suites)
    skipped = sum(int(row.get("skipped", "0")) for row in suites)
    if total != expected_tests or sum(by_file.values()) != expected_tests or failed or errors or skipped:
        raise RuntimeError(f"{label} JUnit must be {expected_tests} passed with no failures/errors/skips")
    return {"tests": expected_tests, "passed": expected_tests, "errors": errors,
            "failed": failed, "skipped": skipped, "by_file": by_file}


def main() -> int:
    for path in (OUTPUT, PLAN, VALIDATION, REQUEST):
        if path.exists() or path.is_symlink():
            raise RuntimeError(f"R6E local PREP output already exists; no overwrite/retry: {path}")
    from tools import windows_primary_owner_r6e_strict_ssh_prep as ssh_wrapper
    inputs = {
        R6D_SOURCE_PLAN: EXPECTED["r6d_plan"], R6D_SOURCE_MANIFEST: EXPECTED["r6d_manifest"],
        R6D_SOURCE_REVIEW_REQUEST: EXPECTED["r6d_review_request"], R6D_GATE: EXPECTED["r6d_gate"],
        R6D_HELPER: EXPECTED["r6d_helper"], R6D_GATE_CONTROLS: EXPECTED["r6d_gate_controls"],
        R6D_HELPER_CONTROLS: EXPECTED["r6d_helper_controls"], R6B_LEASE: EXPECTED["r6b_lease"],
        R6B_LEASE_CONTROLS: EXPECTED["r6b_lease_controls"], R5J_REVIEW: EXPECTED["r5j_review"],
        R5J_PLAN: EXPECTED["r5j_plan"], R5J_GATE: EXPECTED["r5j_gate"],
        R6D_GUARD_REVIEW: EXPECTED["r6d_guard_review"],
        R6D_PREP_REVIEW_FINDING: EXPECTED["r6d_prep_review_finding"],
        R6D_PREP_PLAN: EXPECTED["r6d_prep_plan"],
        R6D_PREP_VALIDATION: EXPECTED["r6d_prep_validation"],
        R6D_PREP_REVIEW_REQUEST: EXPECTED["r6d_prep_review_request"],
        R6D_PREP_RUNNER: EXPECTED["r6d_prep_runner"],
        R6D_PREP_TEMPLATE: EXPECTED["r6d_prep_template"],
        R6D_PREP_CONTROLS: EXPECTED["r6d_prep_controls"],
        R6D_PREP_MANIFEST: EXPECTED["r6d_prep_manifest"],
        R6D_PREP_PAYLOAD: EXPECTED["r6d_prep_payload"],
        R6D_PREP_LOCAL_JUNIT: EXPECTED["r6d_prep_local_junit"],
        R14_BINDING: EXPECTED["r14_binding"], SSH_WRAPPER: None,
        PROTECTED13: EXPECTED["protected13"],
        PROTECTED13_COMPARISON: EXPECTED["protected13_comparison"],
        R14_PROTECTED13: EXPECTED["r14_protected13"],
        RESUME: EXPECTED["resume"],
        JUNIT: EXPECTED["r6d_junit"], LOCAL_JUNIT: None,
        TEMPLATE: None, RUNNER: None, CONTROLS: None,
    }
    actual_source_paths = {str(path.relative_to(ROOT)) for path in inputs if path != LOCAL_JUNIT}
    if actual_source_paths != set(ssh_wrapper.SOURCE_PATHS):
        raise RuntimeError("R6E wrapper and local builder source-review inventories differ")
    raw_inputs = {path: read_exact(path, expected) for path, expected in inputs.items()}
    r6d_plan = json.loads(raw_inputs[R6D_SOURCE_PLAN])
    r6d_manifest = json.loads(raw_inputs[R6D_SOURCE_MANIFEST])
    r6d_review_request = json.loads(raw_inputs[R6D_SOURCE_REVIEW_REQUEST])
    r6d_guard_review = json.loads(raw_inputs[R6D_GUARD_REVIEW])
    r6d_prep_finding = json.loads(raw_inputs[R6D_PREP_REVIEW_FINDING])
    if (r6d_plan.get("schema") != "cv3-windows-primary-owner-r6d-integration-source-plan/1"
            or r6d_manifest.get("schema") != "cv3-windows-primary-owner-r6d-integration-source-manifest/1"
            or r6d_review_request.get("schema") != "cv3-windows-primary-owner-r6d-integration-main-review-request/1"):
        raise RuntimeError("R6D source candidate lineage schema changed")
    if r6d_review_request.get("status") != "PENDING_MAIN_R6D_EXACT_SOURCE_REVIEW_NO_REMOTE_OR_NATIVE_AUTHORITY":
        raise RuntimeError("R6D exact source review is no longer pending as expected")
    if (r6d_guard_review.get("status") != "PASS_SOURCE_REVIEW_R6D_NO_REMOTE_OR_NATIVE_AUTHORITY"
            or r6d_guard_review.get("junit_tests") != 49
            or r6d_guard_review.get("embedded_R6B") != "BYTE_EQUAL"):
        raise RuntimeError("R6D guard Main review does not bind the qualified 49-test source scope")
    if (r6d_prep_finding.get("status") != "CHANGES_REQUIRED_NO_REMOTE_AUTHORITY"
            or r6d_prep_finding.get("payload_sha256") != "bce8380587bdc08be9455e204f0351263fb74b3c6847a43924e5e59ecfbad1d4"):
        raise RuntimeError("R6D PREP finding no longer names the frozen superseded payload")
    r6d_hashes = {
        "repository/tools/windows_primary_token_owner_outer_gate_r6d.py": EXPECTED["r6d_gate"],
        "repository/tools/windows_primary_token_owner_probe_r6d.py": EXPECTED["r6d_helper"],
        "repository/tests/test_windows_primary_owner_r6d_integration.py": EXPECTED["r6d_gate_controls"],
        "repository/tests/test_windows_primary_token_owner_probe_r6d.py": EXPECTED["r6d_helper_controls"],
        "repository/tools/windows_primary_owner_authority_root_lease_r6b.py": EXPECTED["r6b_lease"],
        "repository/tests/test_windows_primary_owner_authority_root_lease_r6b.py": EXPECTED["r6b_lease_controls"],
    }
    if (_function_source(raw_inputs[R6D_GATE], "_r5j_identity_projection")
            != _function_source(raw_inputs[R5J_GATE], "_r5j_identity_projection")):
        raise RuntimeError("R6D embedded projection source drifted from the unchanged exact R5J implementation")
    guard_review_sources = r6d_guard_review.get("source_hashes")
    if not isinstance(guard_review_sources, dict) or set(guard_review_sources) != {
            "repository/tests/test_windows_primary_owner_authority_root_lease_r6b.py",
            "repository/tests/test_windows_primary_owner_r6d_integration.py",
            "repository/tests/test_windows_primary_token_owner_probe_r6d.py",
            "repository/tools/windows_primary_owner_authority_root_lease_r6b.py",
            "repository/tools/windows_primary_token_owner_outer_gate_r6d.py",
            "repository/tools/windows_primary_token_owner_probe_r6d.py",
    }:
        raise RuntimeError("R6D guard Main review does not contain its exact six-source scope")
    for relative, record in guard_review_sources.items():
        path = ROOT / relative
        if (not isinstance(record, dict) or record.get("sha256") != sha(read_exact(path))
                or record.get("sha256") != r6d_hashes.get(relative)):
            raise RuntimeError(f"R6D guard Main review source hash is not independently verified: {relative}")
    r6d_prep_plan = json.loads(raw_inputs[R6D_PREP_PLAN])
    r6d_prep_manifest = json.loads(raw_inputs[R6D_PREP_MANIFEST])
    r6d_prep_request = json.loads(raw_inputs[R6D_PREP_REVIEW_REQUEST])
    if (r6d_prep_plan.get("payload_sha256") != EXPECTED["r6d_prep_payload"]
            or r6d_prep_manifest.get("package_files", {}).get("PRIMARY_OWNER_R6D_REMOTE_PREP_SCRIPT.py", {}).get("sha256")
            != EXPECTED["r6d_prep_payload"]
            or r6d_prep_request.get("status") != "PENDING_MAIN_R6D_PREP_GENERATOR_SOURCE_REVIEW_NO_REMOTE_OR_NATIVE_AUTHORITY"
            or r6d_prep_finding.get("request_sha256") != sha(raw_inputs[R6D_PREP_REVIEW_REQUEST])):
        raise RuntimeError("R6D PREP source findings/manifest lineage differs from the frozen changes-required packet")

    for relative, expected_sha in r6d_hashes.items():
        candidate_rows = r6d_manifest["candidate_files"]
        if candidate_rows.get(relative, {}).get("sha256") != expected_sha:
            raise RuntimeError(f"R6D Main manifest is inconsistent with actual source: {relative}")
    protected, protected_raw, comparison_raw, resume_raw = protected_inputs()
    r14_raw = raw_inputs[R14_BINDING]
    r14 = json.loads(r14_raw)
    if r14.get("taskroot") != REMOTE["task_root"]:
        raise RuntimeError("frozen R14 binding targets another TaskRoot")
    closure = {
        group: {name: {"bytes": record["bytes"], "sha256": record["sha256"]}
                for name, record in r14[group].items()}
        for group in ("source175", "helpers")
    }
    closure_sha = sha(canonical(closure))
    if closure_sha != EXPECTED["r14_closure"] or sum(map(len, closure.values())) != 179:
        raise RuntimeError("R14 closure differs from the exact 175 source plus 4 helper members")
    parity = local_r14_parity(r14)

    helper_raw = raw_inputs[R6D_HELPER]
    gate_raw = raw_inputs[R6D_GATE]
    r6b_source_raw = raw_inputs[R6B_LEASE]
    r6b_controls_raw = raw_inputs[R6B_LEASE_CONTROLS]
    inner = {
        "schema": "cv3-windows-primary-token-owner-binding-r6d/1",
        "task_root": REMOTE["task_root"], "python_executable": REMOTE["python_executable"],
        "python_version": REMOTE["python_version"],
        "helper_path": HELPER_DIR + r"\windows_primary_token_owner_probe_r6d.py",
        "helper_sha256": sha(helper_raw),
        "r6b_lease_source_sha256": sha(r6b_source_raw),
        "r6b_lease_controls_sha256": sha(r6b_controls_raw),
        "authority_root": AUTHORITY_ROOT,
        "binding_path": AUTHORITY_ROOT + r"\BINDING.json",
        "approval_path": AUTHORITY_ROOT + r"\MAIN_APPROVAL.json",
        "once_path": AUTHORITY_ROOT + r"\ONCE_USED.json",
        "output_root": OUTPUT_ROOT, "output_file": OUTPUT_ROOT + r"\owner-probe.bin",
        "expected_user_sid": REMOTE["expected_user_sid"],
        "case_processes": 1, "max_active_seconds": 60,
        "COMSOL": 0, "GUI": 0, "JVM": 0, "Study": 0, "solver": 0,
        "no_privilege_enablement": True, "no_account_or_acl_mutation": True,
        "no_process_signal_or_pid_reopen": True,
    }
    inner_raw = canonical(inner)
    r5j_plan_raw = raw_inputs[R5J_PLAN]
    r5j_review_raw = raw_inputs[R5J_REVIEW]
    r14_meta = {"source175": len(closure["source175"]), "helpers": len(closure["helpers"]),
                "total": sum(map(len, closure.values()))}
    config = {
        **REMOTE, "helper_directory": HELPER_DIR, "authority_root": AUTHORITY_ROOT,
        "r5j_source_plan_sha256": sha(r5j_plan_raw), "r5j_source_review_sha256": sha(r5j_review_raw),
        "r5j_gate_sha256": sha(raw_inputs[R5J_GATE]),
        "r14_binding_sha256": sha(r14_raw), "r14_source_closure_sha256": closure_sha,
        "protected13_manifest_sha256": sha(protected_raw),
        "protected13_comparison_sha256": sha(comparison_raw),
        "r14_protected13_manifest_sha256": EXPECTED["r14_protected13"],
        "current_resume_sha256": sha(resume_raw),
        "helper_sha256": sha(helper_raw), "helper_controls_sha256": EXPECTED["r6d_helper_controls"],
        "gate_sha256": sha(gate_raw), "gate_controls_sha256": EXPECTED["r6d_gate_controls"],
        "inner_binding_sha256": sha(inner_raw), "local_r14_parity": parity,
        "r6d_candidate_manifest_sha256": sha(raw_inputs[R6D_SOURCE_MANIFEST]),
        "r6d_source_review_request_sha256": sha(raw_inputs[R6D_SOURCE_REVIEW_REQUEST]),
        "r6d_guard_main_review_sha256": sha(raw_inputs[R6D_GUARD_REVIEW]),
        "r6d_prep_review_finding_sha256": sha(raw_inputs[R6D_PREP_REVIEW_FINDING]),
        "local_package_only": True,
    }
    payload = render_payload(config, helper_raw, gate_raw, inner_raw)
    if (_function_source(gate_raw, "_r5j_identity_projection")
            != _function_source(raw_inputs[R5J_GATE], "_r5j_identity_projection")):
        raise RuntimeError("embedded R6D projection must remain byte-identical to R5J")
    payload_controls = payload_static_controls(payload)
    junit_raw = raw_inputs[JUNIT]
    junit_summary = _junit_summary(junit_raw, 49, "R6D gate/owner suite")
    local_junit_raw = raw_inputs[LOCAL_JUNIT]
    local_junit_summary = _junit_summary(local_junit_raw, 15, "R6E PREP generator and wrapper controls")

    ssh_invocation = ssh_wrapper.invocation_binding(sha(payload), len(payload))

    remote_files = {
        "PRIMARY_OWNER_R6E_REMOTE_PREP_SCRIPT.py": payload,
        "PRIMARY_OWNER_R6E_INNER_BINDING_PROPOSAL.json": inner_raw,
        "windows_primary_token_owner_probe_r6d.py": helper_raw,
        "windows_primary_token_owner_outer_gate_r6d.py": gate_raw,
        "windows_primary_owner_r6e_prep_payload_template.py.txt": raw_inputs[TEMPLATE],
    }
    remote_meta = {name: {"bytes": len(raw), "sha256": sha(raw)} for name, raw in remote_files.items()}
    contract = {
        "schema": "cv3-windows-primary-owner-r6e-prep-contract/1",
        "status": "LOCAL_PACKAGE_ONLY_PENDING_MAIN_SOURCE_REVIEW_AND_DIRECT_HUMAN_PREP_AUTHORIZATION",
        "local_payload": {
            "path": str((OUTPUT / "PRIMARY_OWNER_R6E_REMOTE_PREP_SCRIPT.py").relative_to(ROOT)),
            "sha256": sha(payload), "bytes": len(payload),
        },
        "remote_invocation": {
            "invocation": ssh_invocation,
            "strict_ssh_wrapper_sha256": sha(raw_inputs[SSH_WRAPPER]),
        },
        "authority": {
            "SSH": "NOT_RUN", "remote_write": "NOT_RUN", "approval_signing": "NOT_AUTHORIZED",
            "MAIN_APPROVAL": "zero-byte future placeholder only", "ONCE_USED": "ABSENT",
            "CreateProcessWithTokenW": "NOT_CALLED", "native_child": "NOT_AUTHORIZED",
            "COMSOL_GUI_JVM_Study_solver": "NOT_RUN", "account_privilege_acl_mutation": "PROHIBITED",
        },
        "remote_target": {**REMOTE, "helper_directory": HELPER_DIR, "authority_root": AUTHORITY_ROOT,
                          "output_root": OUTPUT_ROOT},
        "remote_write_targets": [
            {"role": "helper_source", "path": HELPER_DIR + r"\windows_primary_token_owner_probe_r6d.py",
             "initial_state": "ABSENT", "sha256": sha(helper_raw)},
            {"role": "gate_source", "path": HELPER_DIR + r"\windows_primary_token_owner_outer_gate_r6d.py",
             "initial_state": "ABSENT", "sha256": sha(gate_raw)},
            {"role": "inner_binding", "path": AUTHORITY_ROOT + r"\BINDING.json",
             "initial_state": "ABSENT", "sha256": sha(inner_raw)},
            {"role": "prebirth_binding", "path": AUTHORITY_ROOT + r"\PREBIRTH_BINDING.json",
             "initial_state": "ABSENT_then_zero_byte_placeholder_then_finalize_in_place", "sha256": "derived after exact remote proofs"},
            {"role": "main_approval_placeholder", "path": AUTHORITY_ROOT + r"\MAIN_APPROVAL.json",
             "initial_state": "ABSENT_then_zero_byte_placeholder_only", "sha256": hashlib.sha256(b"").hexdigest()},
        ],
        "forbidden_objects": [AUTHORITY_ROOT + r"\ONCE_USED.json", OUTPUT_ROOT,
                              OUTPUT_ROOT + r"\owner-probe.bin", OUTPUT_ROOT + r"\OWNER_LEASE_EVENTS.jsonl",
                              REMOTE["task_root"] + r"\cases-primary-owner-r6d"],
        "required_order": [
            "Standard-library hash all 179 remote R14 members and record exact raw prewrite path proofs before the first candidate write.",
            "Under the original task_thread_owner gate create only two namespace directories and exactly five leaf names; prove the TaskRoot entry delta adds only those two names and prove exact helper/authority leaf-name sets through R14 pinned handles.",
            "Populate helper, gate and inner BINDING in place, keeping PREBIRTH and MAIN_APPROVAL empty.",
            "After the closed entry set is fixed, freshly collect all 179 FINAL raw R14 source/helper path proofs and use only this post-entry snapshot/digest in PREBIRTH_BINDING.",
        "Finalize PREBIRTH through the same file object, then strictly audit both raw observations for TaskRoot/helper/authority and all 179 final source proofs; compare path identity only through unchanged R5J projection and preserve raw hashes/deltas.",
        "Preserve the prewrite snapshot separately as audit; R5J may project only change/write times for ordinary directories outside TASK_ROOT. Keep TASK_ROOT, descendants, leaves, runtime image, security, identity, size, creation and link-count fields exact.",
        ],
        "future_remote_prep_requires": ["Main exact source review of this package", "separate direct human authorization for the exact payload SHA and five targets"],
        "native_child_requires_separate_future_authorization_and_R6D_main_source_review": True,
    }
    contract_raw = canonical(contract)
    remote_files["PRIMARY_OWNER_R6E_PREP_CONTRACT.json"] = contract_raw
    remote_meta["PRIMARY_OWNER_R6E_PREP_CONTRACT.json"] = {"bytes": len(contract_raw), "sha256": sha(contract_raw)}

    source_rows = {
        str(path.relative_to(ROOT)): {
            "path": str(path.relative_to(ROOT)), "bytes": len(raw), "sha256": sha(raw),
        }
        for path, raw in raw_inputs.items()
        if str(path.relative_to(ROOT)) in set(ssh_wrapper.SOURCE_PATHS)
    }
    manifest = {
        "schema": "cv3-windows-primary-owner-r6e-local-prep-manifest/1",
        "status": "LOCAL_SOURCE_PACKAGE_READY_FOR_MAIN_REVIEW_NO_REMOTE_OR_NATIVE_ACTION",
        "self_hash": "not embedded; bound by the source plan and review request",
        "candidate": "R6E PREP generator preserves raw proof hashes/deltas, audits both observations, and compares only the unchanged R5J path-identity projection while retaining exact post-entry-set proofs.",
        "package_files": remote_meta,
        "candidate_sources": source_rows,
        "r6d_gate_source_manifest_sha256": sha(raw_inputs[R6D_SOURCE_MANIFEST]),
        "r6d_gate_source_review_request_sha256": sha(raw_inputs[R6D_SOURCE_REVIEW_REQUEST]),
        "r6d_guard_main_review_sha256": sha(raw_inputs[R6D_GUARD_REVIEW]),
        "r6d_prep_review_finding_sha256": sha(raw_inputs[R6D_PREP_REVIEW_FINDING]),
        "r14": {"binding_sha256": sha(r14_raw), "closure_sha256": closure_sha,
                "source_count": r14_meta["source175"], "helper_count": r14_meta["helpers"],
                "total_members": r14_meta["total"], "local_parity": parity},
        "protected_inputs": {"protected13_manifest_sha256": sha(protected_raw),
                             "protected13_comparison_sha256": sha(comparison_raw),
                             "r14_protected13_sha256": EXPECTED["r14_protected13"],
                             "current_resume_sha256": sha(resume_raw)},
        "payload_static_controls": payload_controls,
        "local_tests": {
            "r6d_gate_and_owner": {**junit_summary, "junit_sha256": sha(junit_raw), "junit_bytes": len(junit_raw)},
            "r6e_prep_generator_and_wrapper": {**local_junit_summary,
                "junit_sha256": sha(local_junit_raw), "junit_bytes": len(local_junit_raw)},
        },
        "remote_targets": contract["remote_write_targets"],
        "authority_boundary": contract["authority"],
        "not_run": ["SSH", "remote PREP", "Main execution approval", "ONCE", "CreateProcessWithTokenW", "child process", "COMSOL", "GUI", "JVM", "Study", "solver"],
    }
    manifest_raw = canonical(manifest)
    remote_files["PRIMARY_OWNER_R6E_LOCAL_PREP_MANIFEST.json"] = manifest_raw
    remote_meta["PRIMARY_OWNER_R6E_LOCAL_PREP_MANIFEST.json"] = {"bytes": len(manifest_raw), "sha256": sha(manifest_raw)}

    validation = {
        "schema": "cv3-windows-primary-owner-r6e-prep-software-validation/1",
        "status": "PASS_LOCAL_SOFTWARE_CONTROLS_NOT_REMOTE_OR_NATIVE_VALIDATION",
        "evidence_limit": "Local Python source/template controls with synthetic interfaces only; no Windows-native, remote, process, product, COMSOL, or scientific acceptance.",
        "python": sys.version.split()[0], "platform": platform.system(),
        "payload_static_controls": payload_controls,
        "upstream_r6d_tests": junit_summary,
        "upstream_r6d_junit": {"sha256": sha(junit_raw), "bytes": len(junit_raw), "path": str(JUNIT.relative_to(ROOT))},
        "prep_generator_and_wrapper_controls": local_junit_summary,
        "prep_generator_junit": {"sha256": sha(local_junit_raw), "bytes": len(local_junit_raw),
                                "path": str(LOCAL_JUNIT.relative_to(ROOT))},
        "source_manifest_sha256": sha(manifest_raw),
        "not_run": ["R5K3C tests", "SSH", "remote PREP", "approval", "ONCE", "CreateProcessWithTokenW", "Windows child", "account/privilege/ACL mutation", "COMSOL", "GUI", "JVM", "Study", "solver"],
    }
    validation_raw = canonical(validation)

    # The plan and validation bind one another in one direction only: the plan
    # records the test result; validation records the final plan digest.
    plan = {
        "schema": "cv3-windows-primary-owner-r6e-prep-source-plan/1",
        "candidate": manifest["candidate"],
        "status": "SOURCE_CANDIDATE_READY_FOR_MAIN_REVIEW_NO_REMOTE_AUTHORITY",
        "stop_boundary": "Main exact PREP generator/payload source review only; no SSH, remote stage, approval or native child.",
        "package_directory": str(OUTPUT.relative_to(ROOT)),
        "package_manifest_sha256": sha(manifest_raw),
        "source_inputs": source_rows,
        "r6d_gate_source_manifest_sha256": sha(raw_inputs[R6D_SOURCE_MANIFEST]),
        "r6d_gate_source_review_request_sha256": sha(raw_inputs[R6D_SOURCE_REVIEW_REQUEST]),
        "payload_sha256": sha(payload), "payload_bytes": len(payload),
        "source_sequence": contract["required_order"],
        "local_validation": {"status": validation["status"], "upstream_tests": 49,
                             "prep_generator_and_wrapper_controls": 15, "junit_sha256": sha(junit_raw),
                             "local_junit_sha256": sha(local_junit_raw),
                             "payload_static_controls": payload_controls},
        "remote_prep_contract": {
            "remote_write_targets": contract["remote_write_targets"],
            "invocation": ssh_invocation,
            "strict_ssh_wrapper_sha256": sha(raw_inputs[SSH_WRAPPER]),
        },
        "r6d_guard_main_review_sha256": sha(raw_inputs[R6D_GUARD_REVIEW]),
        "r6d_prep_review_finding_sha256": sha(raw_inputs[R6D_PREP_REVIEW_FINDING]),
        "authority_boundary": contract["authority"],
    }
    plan_raw = canonical(plan)
    validation["source_plan_sha256"] = sha(plan_raw)
    validation_raw = canonical(validation)
    plan_meta = {"path": str(PLAN.relative_to(ROOT)), "bytes": len(plan_raw), "sha256": sha(plan_raw)}
    validation_meta = {"path": str(VALIDATION.relative_to(ROOT)), "bytes": len(validation_raw),
                       "sha256": sha(validation_raw)}
    manifest_meta = {"path": str((OUTPUT / "PRIMARY_OWNER_R6E_LOCAL_PREP_MANIFEST.json").relative_to(ROOT)),
                     "bytes": len(manifest_raw), "sha256": sha(manifest_raw)}
    junit_meta = {"path": str(JUNIT.relative_to(ROOT)), "bytes": len(junit_raw), "sha256": sha(junit_raw)}
    local_junit_meta = {"path": str(LOCAL_JUNIT.relative_to(ROOT)), "bytes": len(local_junit_raw),
                        "sha256": sha(local_junit_raw)}
    request = {
        "schema": "cv3-windows-primary-owner-r6e-prep-main-review-request/1",
        "status": "PENDING_MAIN_R6E_PREP_GENERATOR_SOURCE_REVIEW_NO_REMOTE_OR_NATIVE_AUTHORITY",
        "candidate": manifest["candidate"],
        "main_action_requested": "Review this exact local-only R6E generator, payload, raw-proof diagnostics, narrow R5J projection comparator, owner wrapper source and five-target future PREP proposal. Return findings or a scoped source-review result; do not SSH or perform remote preparation.",
        "artifacts": {"source_plan": plan_meta, "software_validation": validation_meta,
                      "package_manifest": manifest_meta, "r6d_gate_junit": junit_meta,
                      "r6e_prep_generator_and_wrapper_junit": local_junit_meta,
                      "remote_payload": {"path": str((OUTPUT / "PRIMARY_OWNER_R6E_REMOTE_PREP_SCRIPT.py").relative_to(ROOT)),
                                        "bytes": len(payload), "sha256": sha(payload)}},
        "source_hashes": source_rows,
        "main_source_review_scope_files": [
            {"path": path, "sha256": row["sha256"]}
            for path, row in sorted(source_rows.items())
        ],
        "required_review_questions": [
            "Does the generator preserve the full 179-member standard-library prehash and raw prewrite path audit before its first remote write?",
            "Does it create only the two exact namespace directories and five leaves under the same task_thread_owner gate, then prove the exact TaskRoot entry delta and exact helper/authority entry sets through the R14 pinned enumeration API?",
            "Are raw final R14 source/helper path proofs collected only after all five entry names and helper/gate/inner bytes are fixed, and are these the only proofs/digests placed in PREBIRTH_BINDING?",
            "After same-object PREBIRTH finalization, does it audit both complete raw observations and compare all 179 source plus TaskRoot/helper/authority path proofs only through the byte-identical R5J projection?",
            "Are full raw proof hashes and raw leaf deltas retained as diagnostics while only ordinary directory ancestors outside TASK_ROOT may project change/write times?",
            "Are TASK_ROOT, all descendants, file leaves, runtime image, security descriptors, file IDs, size, creation time and link count still exact?",
            "Are prewrite proofs retained only as separate audit values, with private TaskRoot timestamps never masked or substituted as final expected values?",
            "Are all five future remote target paths exact and no approval/ONCE/output/cases/native child/SSH action created or authorized?",
        ],
        "authority_boundary": {"source_review_grants_remote_or_native_authority": False,
                              "direct_human_PREP_authorization": "PENDING", "SSH": "NOT_RUN",
                              "remote_PREP": "NOT_RUN", "native_child": "NOT_AUTHORIZED_AND_NOT_RUN"},
        "r6d_gate_source_lineage": {"plan_sha256": sha(raw_inputs[R6D_SOURCE_PLAN]),
                                    "manifest_sha256": sha(raw_inputs[R6D_SOURCE_MANIFEST]),
                                    "source_review_request_sha256": sha(raw_inputs[R6D_SOURCE_REVIEW_REQUEST]),
                                    "main_source_review_sha256": sha(raw_inputs[R6D_GUARD_REVIEW]),
                                    "r6d_prep_changes_required_finding_sha256": sha(raw_inputs[R6D_PREP_REVIEW_FINDING]),
                                    "native_or_remote_review_status": "NOT_AUTHORIZED"},
    }
    request_raw = canonical(request)

    # All bytes and cross-references are fixed. Create only this fresh local
    # package and three review artifacts; no remote command exists in this code.
    os.mkdir(OUTPUT, 0o700)
    for name, raw in remote_files.items():
        write_exclusive(OUTPUT / name, raw)
    write_exclusive(PLAN, plan_raw)
    write_exclusive(VALIDATION, validation_raw)
    write_exclusive(REQUEST, request_raw)
    print(json.dumps({
        "status": "LOCAL_R6E_PREP_PACKAGE_READY_FOR_MAIN_REVIEW_NO_REMOTE_OR_NATIVE_ACTION",
        "output": str(OUTPUT.relative_to(ROOT)), "manifest": manifest_meta,
        "source_plan": plan_meta, "software_validation": validation_meta,
        "main_review_request": {"path": str(REQUEST.relative_to(ROOT)),
                                "bytes": len(request_raw), "sha256": sha(request_raw)},
        "junit": junit_meta, "prep_generator_junit": local_junit_meta,
        "payload_sha256": sha(payload), "payload_bytes": len(payload),
        "tests": 49, "prep_generator_and_wrapper_controls": 15,
        "native_child": "NOT_AUTHORIZED_AND_NOT_RUN", "SSH": "NOT_RUN",
    }, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
