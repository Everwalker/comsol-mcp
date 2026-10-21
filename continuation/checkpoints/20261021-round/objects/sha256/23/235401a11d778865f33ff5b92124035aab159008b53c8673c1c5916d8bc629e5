"""Fresh versioned current175 lifecycle preparer with the exact App producer binding.

This versioned preparer leaves the historical W26 lifecycle actor untouched and passes the explicitly selected profile through its producer and sealed reader.
It binds the App candidate filename from MAIN_ONCE_BINDING.json and pins this
module plus the r9 sealed-data helper in the nested lifecycle source closure.
"""
from __future__ import annotations

import json
import hashlib
import importlib
import importlib.util
import re
import sys
import os
import stat
import subprocess
import uuid
from contextlib import contextmanager
from pathlib import Path
from packaging.markers import default_environment

from tools import release_transition_lifecycle as transition_lifecycle
from tools import w26_application_lifecycle_base_current as lifecycle
from tools import w26_application_bundle_current as app
from tools import w26_runtime_actor_current as actor
from tools import w26_sealed_data_current as data
from tools.w26_application_bundle_current import _helpers
from tools.w26_sealed_data_current import (
    build_current_application_profile, build_current_profile_spec, build_current_profile_spec_for_app,
    current_application_profile,
)
system_reader_proof = lifecycle


SEALED_R9_HELPER = Path(data.__file__).resolve(strict=True)
LEGACY_R9_HELPER = Path(__file__).with_name("w26_sealed_data_r9.py").resolve(strict=True)
R9_SEALED_PHASES = ("old-read", "candidate-backup", "restore", "old-restored-read")
BASE_TRANSITION_CONSUMER_SHA256 = "08591b2a0adcceef0516a9c448e1def6b3c4f14fac0fd471a6f8276e61b87c41"
LEGACY_R6_DESCRIPTOR_SHA256 = "f9c77a286b755747e69d7ee8a67a5849128751f899d970073698e5a0c08ba4c8"
CURRENT_SEALED_PHASE_SCRIPT = (
    "import hashlib,pathlib,runpy,sys; helper,descriptor,sha,phase,*extra=sys.argv[1:]\n"
    "if '--input-preflight-only' in extra:\n"
    " wi=extra.index('--preflight-packaging-wheel'); wheel=pathlib.Path(extra[wi+1]); wh=extra[wi+3]\n"
    " if wheel.is_symlink() or wheel.resolve()!=wheel or hashlib.sha256(wheel.read_bytes()).hexdigest()!=wh: raise RuntimeError('explicit input-preflight packaging wheel differs')\n"
    " sys.path.insert(0,str(wheel))\n"
    "sys.path.insert(0,str(pathlib.Path(helper).resolve().parent.parent)); "
    "sys.argv=[helper,'--descriptor',descriptor,'--sha',sha,'--phase',phase,*extra]; "
    "runpy.run_path(helper,run_name='__main__')"
)
R9_LOCKS = (
    Path(data.__file__).with_name("w26_application_sources_175_r4.lock.json"),
    Path(data.__file__).with_name("w26_application_sources_175_r5.lock.json"),
    Path(data.__file__).with_name("w26_application_sources_175_r6.lock.json"),
    Path(data.__file__).with_name("w26_application_sources_175_r7_final.lock.json"),
    Path(data.__file__).with_name("w26_application_sources_175_r8_prepared.lock.json"),
    Path(data.__file__).with_name("w26_application_sources_175_r9_prepared.lock.json"),
)
TARGET_INPUTS = Path(data.__file__).with_name("w26_target_inputs.py")
def _loader_records() -> dict[str, dict]:
    sources = actor.source_fingerprints(actor.APP_LIFECYCLE)
    for path in (TARGET_INPUTS, *R9_LOCKS, SEALED_R9_HELPER):
        row = _helpers().raw_file(path)[1]
        sources[row["path"]] = row
    for name in data.CURRENT_NAMES:
        path = Path(data.__file__).with_name(name)
        row = _helpers().raw_file(path)[1]
        sources[row["path"]] = row
    return sources


def _records() -> dict[str, dict]:
    sources = _loader_records()
    for path in (Path(__file__).resolve(), Path(lifecycle.__file__).resolve(), SEALED_R9_HELPER):
        row = _helpers().raw_file(path)[1]
        sources[row["path"]] = row
    return sources


def _with_current_reader(function, *args, profile_spec, data_selector=None, **kwargs):
    """Use the exact selected current reader and descriptor selector for one call."""
    bound_profile_spec = profile_spec
    old_reader = data.current_application_profile
    old_descriptor = data.descriptor

    def selected_profile(profile, *, profile_spec=None):
        # The R12 descriptor consumer passes the selected spec explicitly as a
        # keyword.  Accept that one bound keyword while rejecting any attempt
        # to switch the reader to a different profile during this call.
        if profile_spec not in (None, bound_profile_spec):
            raise RuntimeError("lifecycle current profile received an unbound profile spec")
        if (not isinstance(profile, dict)
                or profile.get("source_profile") != bound_profile_spec.get("source_profile")):
            raise RuntimeError("lifecycle current profile differs from its explicit profile spec")
        return current_application_profile(profile, profile_spec=bound_profile_spec)

    def selected_descriptor(path, sha, *args, **kwargs):
        supplied = kwargs.pop("profile_spec", None)
        if args or kwargs or supplied not in (None, profile_spec):
            raise RuntimeError("lifecycle descriptor received an unbound profile selector")
        return old_descriptor(path, sha, profile_spec=profile_spec, data_selector=data_selector)

    data.current_application_profile = selected_profile
    data.descriptor = selected_descriptor
    try:
        return function(*args, **kwargs)
    finally:
        data.descriptor = old_descriptor
        data.current_application_profile = old_reader


def _inventory_with_r9_helper(root, python, descriptor, old_input, new_input, profile_spec_record,
                              data_input_spec_record=None):
    """Build the base inventory and route four sealed phases through current reader."""
    prior_script = transition_lifecycle.SEALED_PHASE_SCRIPT
    transition_lifecycle.SEALED_PHASE_SCRIPT = CURRENT_SEALED_PHASE_SCRIPT
    try:
        rows = lifecycle.inventory(root, python, descriptor, old_input, new_input)
    finally:
        transition_lifecycle.SEALED_PHASE_SCRIPT = prior_script
    return _route_sealed_phase_rows(rows, SEALED_R9_HELPER, profile_spec_record,
                                    data_input_spec_record=data_input_spec_record)


def _route_sealed_phase_rows(rows, helper_path, profile_spec_record, *,
                             data_input_spec_record=None, input_preflight_only=False):
    """Route the fixed four sealed rows with exact profile and selected data-input specs."""
    helper = str(Path(helper_path).resolve(strict=True))
    base_helper = helper
    spec_path = profile_spec_record.get("path") if isinstance(profile_spec_record, dict) else None
    spec_sha = profile_spec_record.get("sha256") if isinstance(profile_spec_record, dict) else None
    if not isinstance(spec_path, str) or not isinstance(spec_sha, str) or len(spec_sha) != 64:
        raise RuntimeError("current profile spec sidecar record is malformed")
    _helpers().raw_file(Path(spec_path), expected=profile_spec_record)
    data_args = []
    if data_input_spec_record is not None:
        if (not isinstance(data_input_spec_record, dict)
                or not isinstance(data_input_spec_record.get("path"), str)
                or not isinstance(data_input_spec_record.get("sha256"), str)
                or len(data_input_spec_record["sha256"]) != 64):
            raise RuntimeError("current lifecycle data-input spec sidecar record is malformed")
        _helpers().raw_file(Path(data_input_spec_record["path"]), expected=data_input_spec_record)
        data_args = ["--data-input-spec", data_input_spec_record["path"],
                     "--data-input-spec-sha", data_input_spec_record["sha256"]]
    if input_preflight_only and data_input_spec_record is None:
        raise RuntimeError("R13 read-only phase preflights require the explicit data-input spec")
    output = []
    found = set()
    for row in rows:
        label = row.get("label")
        if label not in {"data-" + phase for phase in R9_SEALED_PHASES}:
            output.append(row)
            continue
        phase = label.removeprefix("data-")
        if phase in found:
            raise RuntimeError("duplicate sealed phase in closed inventory")
        argv = row.get("request_argv")
        if not isinstance(argv, list) or len(argv) < 4:
            raise RuntimeError("sealed-phase inventory argv is malformed before R9 routing")
        found.add(phase)
        helper_positions = [index for index, value in enumerate(argv) if value == helper]
        already_product_routed = False
        if len(helper_positions) == 1:
            helper_index = helper_positions[0]
            if helper_index + 4 <= len(argv):
                selected_tail = [helper, argv[helper_index + 1], argv[helper_index + 2], phase,
                                 "--profile-spec", spec_path, "--profile-spec-sha", spec_sha,
                                 *data_args]
                already_product_routed = argv[helper_index:] == selected_tail
        if already_product_routed:
            if any(arg in argv for arg in ("--input-preflight-only", "--preflight-packaging-wheel",
                                           "--preflight-packaging-wheel-sha")):
                raise RuntimeError("sealed-phase product inventory already contains preflight-only arguments")
            routed = list(argv)
        elif argv[-4] == base_helper and argv[-1] == phase:
            routed = [*argv[:-4], helper, *argv[-3:], "--profile-spec", spec_path,
                      "--profile-spec-sha", spec_sha, *data_args]
        else:
            raise RuntimeError("base or exact product sealed-phase inventory argv differs before routing")
        if input_preflight_only:
            selector = data.r13_data_profile_selector(
                data_input_spec_record["path"], data_input_spec_record["sha256"])
            wheel = selector["input_spec"]["preflight_packaging_wheel"]
            routed.extend(["--preflight-packaging-wheel", wheel["path"],
                           "--preflight-packaging-wheel-sha", wheel["sha256"]])
            routed.append("--input-preflight-only")
        output.append({**row, "request_argv": routed})
    if found != set(R9_SEALED_PHASES):
        raise RuntimeError("R9 lifecycle requires exactly four fixed sealed phases")
    return output


def _run_r13_phase_input_preflights(root: Path, descriptor_record: dict,
                                     profile_spec_record: dict,
                                     data_input_spec_record: dict,
                                     selector: dict, rows: list[dict]) -> list[dict]:
    """Run each existing sealed-phase CLI in its explicit read-only input mode."""
    selected = [row for row in rows if row.get("label") in {
        "data-" + phase for phase in R9_SEALED_PHASES
    }]
    if {row["label"] for row in selected} != {"data-" + phase for phase in R9_SEALED_PHASES}:
        raise RuntimeError("current lifecycle requires exactly the four fixed data-phase rows")
    routed = _route_sealed_phase_rows(rows, SEALED_R9_HELPER, profile_spec_record,
                                      data_input_spec_record=data_input_spec_record,
                                      input_preflight_only=True)
    routed_by_label = {row["label"]: row for row in routed}
    helper = _helpers()
    profile_spec_raw, _ = helper.raw_file(
        Path(profile_spec_record["path"]), expected=profile_spec_record, maximum=65536)
    profile_spec = json.loads(profile_spec_raw)
    root = Path(root)
    def root_snapshot() -> dict[str, dict]:
        observed = {}
        for path in sorted(root.iterdir()):
            item = path.lstat()
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise RuntimeError("current lifecycle preflight root contains a linked/non-file member")
            if path.is_file():
                observed[path.name] = helper.raw_file(path)[1]
            else:
                observed[path.name] = {"dev": item.st_dev, "inode": item.st_ino,
                                       "uid": item.st_uid, "mode": item.st_mode,
                                       "nlink": item.st_nlink}
        return observed

    before_root = root_snapshot()
    before_baseline = data.verify_r13_data_baseline(selector)
    results = []
    for phase in R9_SEALED_PHASES:
        row = routed_by_label["data-" + phase]
        request = row.get("request_argv")
        wheel = selector["input_spec"]["preflight_packaging_wheel"]
        expected_tail = [str(SEALED_R9_HELPER), descriptor_record["path"],
                         descriptor_record["sha256"], phase, "--profile-spec",
                         profile_spec_record["path"], "--profile-spec-sha",
                         profile_spec_record["sha256"], "--data-input-spec",
                         data_input_spec_record["path"], "--data-input-spec-sha",
                         data_input_spec_record["sha256"], "--preflight-packaging-wheel",
                         wheel["path"], "--preflight-packaging-wheel-sha",
                         wheel["sha256"], "--input-preflight-only"]
        if (not isinstance(request, list) or len(request) < 24
                or request[:2] != ["/usr/bin/sandbox-exec", "-f"]
                or request[2] != str(root / "transition-work/transition-evidence/network-deny-transition.sb")
                or request[-17:] != expected_tail):
            raise RuntimeError("current lifecycle no-effect phase route differs from its exact descriptor/profile")
        inner = list(request[3:])
        if inner[0] != str(Path(root / "transition-work/transition-venv/bin/python")):
            raise RuntimeError("current lifecycle preflight must substitute only the exact product Python slot")
        inner[0] = str(Path(sys.executable).resolve(strict=True))
        completed = subprocess.run(
            inner, cwd=str(root), capture_output=True, text=True, timeout=90,
            env={"PATH": "/usr/bin:/bin", "HOME": str(root), "TMPDIR": str(root),
                 "LC_ALL": "C", "LANG": "C", "PYTHONNOUSERSITE": "1",
                 "PYTHONDONTWRITEBYTECODE": "1"}, check=False)
        if completed.returncode != 0 or completed.stderr.strip():
            raise RuntimeError("current lifecycle product phase input-only CLI failed: " + phase)
        value = json.loads(completed.stdout)
        if (value.get("status") != "PASS_T044_R13_PRODUCT_PHASE_INPUT_PREFLIGHT_ONLY"
                or value.get("phase") != phase
                or value.get("source_profile") != profile_spec.get("source_profile")
                or profile_spec.get("source_profile") not in data.R13_DATA_APP_PROFILES
                or value.get("baseline_seal_sha256") != data.R13_DATA_SEAL_SHA256
                or value.get("verified_input_files") != 51
                or value.get("verified_table_count") != 14
                or value.get("verified_operations") != 29
                or value.get("verified_jobs") != 29
                or value.get("raw_sqlite_originals_opened") is not False
                or value.get("restore_effects") != "NOT_RUN"
                or value.get("backup_effects") != "NOT_RUN"):
            raise RuntimeError("current lifecycle product phase input-only result scope differs: " + phase)
        results.append({"phase": phase, "request_argv": request, "exit_code": completed.returncode,
                        "result": value, "product_runtime": "NOT_RUN",
                        "sandbox_exec_wrapper": "NOT_RUN_FOR_READONLY_INPUT_PREFLIGHT"})
    after_root = root_snapshot()
    after_baseline = data.verify_r13_data_baseline(selector)
    if before_root != after_root or before_baseline != after_baseline:
        raise RuntimeError("current lifecycle input-only phase consumers changed root or sealed baseline")
    return results


@contextmanager
def _selected_lifecycle_seal(profile_spec: dict, data_input_spec_record=None):
    """Temporarily bind the existing lifecycle consumer to one finite seal profile."""
    if profile_spec.get("source_profile") in data.R13_DATA_APP_PROFILES:
        if not isinstance(data_input_spec_record, dict):
            raise RuntimeError("R12 lifecycle seal requires an explicit data-input spec record")
        selector = data.r13_data_profile_selector(
            data_input_spec_record.get("path"), data_input_spec_record.get("sha256"))
        selected_path = selector["seal_record"]["path"]
        selected_sha = selector["seal_record"]["sha256"]
    elif profile_spec.get("source_profile") == data.R11_PROFILE:
        if data_input_spec_record is not None:
            raise RuntimeError("legacy R11 lifecycle cannot consume a new R13 data-input spec")
        selector = None
        selected_path, selected_sha = lifecycle.SEAL, lifecycle.SEAL_SHA
    else:
        raise RuntimeError("lifecycle seal selector is outside the finite R11/R12 profiles")
    old_path, old_sha = lifecycle.SEAL, lifecycle.SEAL_SHA
    try:
        lifecycle.SEAL, lifecycle.SEAL_SHA = selected_path, selected_sha
        if selector is None:
            yield selected_path, selected_sha
        else:
            with data.use_r13_data_selector(selector):
                yield selected_path, selected_sha
    finally:
        changed = lifecycle.SEAL != selected_path or lifecycle.SEAL_SHA != selected_sha
        lifecycle.SEAL, lifecycle.SEAL_SHA = old_path, old_sha
        if changed:
            raise RuntimeError("selected lifecycle seal changed before exact restoration")


def _product_seal_first_record(record: dict) -> dict:
    """Project a strictly re-read R13 selector record at the helper boundary."""
    expected = {"path", "bytes", "sha256", "dev", "inode", "uid", "mode", "nlink",
                "mtime_ns", "ctime_ns"}
    if type(record) is not dict or set(record) != expected:
        raise RuntimeError("R13 selector seal observation has an unexpected first-record shape")
    if (type(record["path"]) is not str or type(record["sha256"]) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])
            or any(type(record[key]) is not int for key in
                   ("bytes", "dev", "inode", "uid", "mode", "nlink", "mtime_ns", "ctime_ns"))):
        raise RuntimeError("R13 selector seal observation has invalid first-record fields")
    return {"path": record["path"], "bytes": record["bytes"], "sha256": record["sha256"],
            "dev": record["dev"], "inode": record["inode"], "uid": record["uid"],
            "mode": stat.S_IMODE(record["mode"]), "nlink": record["nlink"]}


def _verify_product_seal_records(selector: dict, prepared_product_record: dict) -> dict:
    """Recheck the full R13 reader record, then compare only at the helper boundary.

    The R13 reader's same-FD observation includes full st_mode, mtime and ctime.
    The existing lifecycle helper exposes its established eight-field record,
    with permission bits only.  Both readers must observe the same actual seal
    bytes and stable inode identity before this exact projection is compared to
    the product-prepared record.
    """
    if type(selector) is not dict or type(selector.get("input_spec_record")) is not dict:
        raise RuntimeError("R13 seal selector omits its exact input-spec record")
    fresh_selector = data.r13_data_profile_selector(
        selector["input_spec_record"].get("path"), selector.get("input_spec_sha256"))
    if selector != fresh_selector:
        raise RuntimeError("R13 full seal selector changed from the strict reader result")
    seal_record = selector.get("seal_record")
    if type(seal_record) is not dict:
        raise RuntimeError("R13 full seal selector omits its first record")
    seal_path = Path(seal_record.get("path", ""))
    raw, full_observed = data.raw_file(seal_path, expected=seal_record, maximum=4194304)
    if full_observed != seal_record or hashlib.sha256(raw).hexdigest() != seal_record.get("sha256"):
        raise RuntimeError("R13 strict reader full seal first record changed")
    expected_product_record = _product_seal_first_record(full_observed)
    product_raw, product_observed = _helpers().raw_file(
        seal_path, expected=expected_product_record, maximum=4194304)
    if (hashlib.sha256(product_raw).hexdigest() != full_observed["sha256"]
            or product_observed != expected_product_record):
        raise RuntimeError("existing product helper seal first record differs at the named boundary")
    if prepared_product_record != product_observed:
        raise RuntimeError("product-prepared seal record differs from the actual helper observation")
    return {"selector_reader_record": full_observed,
            "product_helper_record": product_observed,
            "product_projection": expected_product_record}


@contextmanager
def r9_execution_context(profile_spec_record, data_input_spec_record=None):
    """Bind the existing W26 lifecycle's selected reader and consumer seams.

    The existing actor owns candidate admission and all execution policy. This
    context routes the sealed profile reader and the four exact sealed helper
    arguments. It restores every patched method and module function on exit.
    """
    helper_path = Path(SEALED_R9_HELPER).resolve(strict=True)
    reader_path = Path(data.__file__).resolve(strict=True)
    if helper_path != reader_path:
        raise RuntimeError("R9 lifecycle execution helper is not the imported exact reader")
    spec_raw, spec_observed = _helpers().raw_file(Path(profile_spec_record["path"]),
                                                  expected=profile_spec_record)
    if spec_observed["sha256"] != profile_spec_record["sha256"]:
        raise RuntimeError("current reader profile-spec sidecar changed")
    profile_spec = json.loads(spec_raw)
    if profile_spec.get("source_profile") in data.R13_DATA_APP_PROFILES:
        if not isinstance(data_input_spec_record, dict):
            raise RuntimeError("R12 lifecycle entry omits its exact data-input spec record")
        data_spec_raw, data_spec_observed = _helpers().raw_file(
            Path(data_input_spec_record["path"]), expected=data_input_spec_record, maximum=65536)
        selector = data.r13_data_profile_selector(data_input_spec_record["path"],
                                                  data_input_spec_record["sha256"])
        if data_spec_observed != selector["input_spec_record"] or json.loads(data_spec_raw) != selector["input_spec"]:
            raise RuntimeError("current lifecycle exact data-input spec bytes changed")
    elif profile_spec.get("source_profile") == data.R11_PROFILE:
        if data_input_spec_record is not None:
            raise RuntimeError("legacy R11 lifecycle cannot consume a new R13 data-input spec")
        selector = None
    else:
        raise RuntimeError("lifecycle execution source profile is outside finite R11/R12 support")
    old_reader = data.current_application_profile
    old_descriptor = data.descriptor
    legacy_data_module = None
    old_legacy_descriptor = None
    legacy_descriptor_wrapper = None
    old_inventory = lifecycle.inventory
    release = lifecycle.release
    old_sealed_phase_script = transition_lifecycle.SEALED_PHASE_SCRIPT
    spec_factory = importlib.util.spec_from_file_location
    base_module_path = Path(transition_lifecycle.__file__).resolve(strict=True)
    base_module_record = _helpers().raw_file(base_module_path)[1]
    if base_module_record["sha256"] != BASE_TRANSITION_CONSUMER_SHA256:
        raise RuntimeError("canonical transition consumer file hash differs")
    base_method = transition_lifecycle.Lifecycle.external_arguments
    base_method_code = base_method.__code__
    expected_inventory = {"rows": None, "by_phase": None}
    patched_methods = []
    patched_scripts = []

    def same_original_method(actual):
        code = getattr(actual, "__code__", None)
        return (
            getattr(actual, "__name__", None) == base_method.__name__
            and getattr(actual, "__qualname__", None) == base_method.__qualname__
            and getattr(actual, "__annotations__", None) == base_method.__annotations__
            and code is not None
            and code.co_filename == base_method_code.co_filename
            and code.co_firstlineno == base_method_code.co_firstlineno
            and code.co_code == base_method_code.co_code
            and code.co_consts == base_method_code.co_consts
            and code.co_names == base_method_code.co_names
            and code.co_varnames == base_method_code.co_varnames
            and code.co_argcount == base_method_code.co_argcount
            and code.co_kwonlyargcount == base_method_code.co_kwonlyargcount
        )

    def bind_actual_lifecycle_module(module):
        if Path(module.__file__).resolve(strict=True) != base_module_path:
            raise RuntimeError("actual transition consumer module path differs")
        original_script = getattr(module, "SEALED_PHASE_SCRIPT", None)
        if (type(original_script) is not str
                or original_script != old_sealed_phase_script):
            raise RuntimeError("actual transition sealed-phase script differs from the frozen base module")
        # full_project_release.transition_check imports a fresh module object
        # from the hash-pinned transition source.  The already-imported module
        # above is not that object, so route this exact dynamic consumer too.
        # Keep the closed script literal identical to the inventory producer.
        module.SEALED_PHASE_SCRIPT = CURRENT_SEALED_PHASE_SCRIPT
        patched_scripts.append((module, original_script, CURRENT_SEALED_PHASE_SCRIPT))
        cls = getattr(module, "Lifecycle", None)
        original = getattr(cls, "external_arguments", None) if cls is not None else None
        if not same_original_method(original):
            raise RuntimeError("actual Lifecycle.external_arguments is not the exact base method")

        def r9_external_arguments(instance, phase):
            if type(phase) is not str or phase not in R9_SEALED_PHASES:
                raise RuntimeError("R9 sealed phase is outside the fixed four-phase inventory")
            rows = expected_inventory["by_phase"]
            if rows is None or phase not in rows:
                raise RuntimeError("approved R9 sealed-phase inventory is not active")
            row = rows[phase]
            argv = row.get("request_argv")
            if not isinstance(argv, list) or len(argv) < 8:
                raise RuntimeError("approved current sealed-phase argv is malformed")
            expected_count = 12 if data_input_spec_record is not None else 8
            expected = argv[-expected_count:]
            base_arguments = original(instance, phase)
            expected_base_helper = str(base_module_path.with_name("w26_sealed_data.py"))
            if (len(expected) != expected_count or expected[0] != str(helper_path)
                    or expected[1] != getattr(instance, "external_descriptor", None)
                    or expected[2] != getattr(instance, "external_descriptor_sha", None)
                    or expected[3] != phase or expected[4] != "--profile-spec"
                    or expected[5] != profile_spec_record["path"]
                    or expected[6] != "--profile-spec-sha"
                    or expected[7] != profile_spec_record["sha256"]
                    or base_arguments != [expected_base_helper, expected[1], expected[2], phase]):
                raise RuntimeError("actual sealed consumer arguments differ from exact current inventory")
            if data_input_spec_record is not None and expected[8:] != [
                    "--data-input-spec", data_input_spec_record["path"],
                    "--data-input-spec-sha", data_input_spec_record["sha256"]]:
                raise RuntimeError("actual sealed consumer data-input arguments differ from its exact spec")
            return list(expected)

        cls.external_arguments = r9_external_arguments
        patched_methods.append((cls, original, r9_external_arguments))

    def wrapped_spec_from_file_location(name, location=None, *args, **kwargs):
        if location is None:
            spec = spec_factory(name, *args, **kwargs)
        else:
            spec = spec_factory(name, location, *args, **kwargs)
        if name != "release_transition_lifecycle":
            return spec
        if location is None:
            return spec
        try:
            resolved_location = Path(location).resolve(strict=True)
        except (OSError, RuntimeError):
            return spec
        if resolved_location != base_module_path:
            return spec
        _helpers().raw_file(base_module_path, expected=base_module_record)
        if expected_inventory["by_phase"] is None or spec is None or spec.loader is None:
            raise RuntimeError("R9 actual consumer import preceded its admitted inventory")
        if spec.origin is None or Path(spec.origin).resolve(strict=True) != base_module_path:
            raise RuntimeError("exact transition consumer spec origin differs")
        inner = spec.loader

        class ExactConsumerLoader:
            def create_module(self, target_spec):
                create = getattr(inner, "create_module", None)
                return create(target_spec) if create is not None else None

            def exec_module(self, target_module):
                inner.exec_module(target_module)
                bind_actual_lifecycle_module(target_module)

            def __getattr__(self, name):
                return getattr(inner, name)

        spec.loader = ExactConsumerLoader()
        return spec

    def r9_inventory(*args, **kwargs):
        base_rows = old_inventory(*args, **kwargs)
        routed_rows = _route_sealed_phase_rows(
            base_rows, helper_path, profile_spec_record,
            data_input_spec_record=data_input_spec_record)
        by_phase = {row["label"].removeprefix("data-"): row for row in routed_rows
                    if row.get("label") in {"data-" + phase for phase in R9_SEALED_PHASES}}
        if expected_inventory["rows"] is not None and expected_inventory["rows"] != routed_rows:
            raise RuntimeError("R9 lifecycle inventory changed during the actor scope")
        expected_inventory["rows"] = routed_rows
        expected_inventory["by_phase"] = by_phase
        return routed_rows

    missing_base_namespace = object()

    def selected_profile(profile, selected_base=missing_base_namespace, *, profile_spec=None):
        if selected_base is not missing_base_namespace and selected_base is not data.BASE:
            raise RuntimeError("lifecycle current profile received an unbound base namespace")
        if profile_spec not in (None, profile_spec_record_value):
            raise RuntimeError("lifecycle current profile received an unbound profile spec")
        if (not isinstance(profile, dict)
                or profile.get("source_profile") != profile_spec_record_value.get("source_profile")):
            raise RuntimeError("lifecycle current profile differs from its selected profile spec")
        return current_application_profile(
            profile, data.BASE, profile_spec=profile_spec_record_value)

    def selected_descriptor(path, sha, selected_profile_spec=None, *, data_selector=None):
        if selected_profile_spec not in (None, profile_spec_record_value):
            raise RuntimeError("lifecycle descriptor received an unbound profile spec")
        if data_selector not in (None, selector):
            raise RuntimeError("lifecycle descriptor received an unbound data selector")
        return old_descriptor(path, sha, profile_spec_record_value, data_selector=selector)

    profile_spec_record_value = profile_spec

    def selected_legacy_descriptor(path, sha):
        if (profile_spec_record_value.get("source_profile") not in data.R13_DATA_APP_PROFILES
                or data_input_spec_record is None or selector is None):
            raise RuntimeError("legacy lifecycle descriptor bridge requires the explicit R12 data selector")
        return selected_descriptor(path, sha, profile_spec_record_value,
                                   data_selector=selector)

    try:
        if profile_spec_record_value.get("source_profile") in data.R13_DATA_APP_PROFILES:
            legacy_descriptor_path = Path(transition_lifecycle.__file__).resolve(strict=True).with_name(
                "w26_sealed_data.py")
            legacy_descriptor_record = _helpers().raw_file(legacy_descriptor_path)[1]
            legacy_data_module = importlib.import_module("tools.w26_sealed_data")
            observed_module_path = Path(legacy_data_module.__file__).resolve(strict=True)
            original_legacy_descriptor = getattr(legacy_data_module, "descriptor", None)
            descriptor_code = getattr(original_legacy_descriptor, "__code__", None)
            if (observed_module_path != legacy_descriptor_path
                    or legacy_descriptor_record.get("sha256") != LEGACY_R6_DESCRIPTOR_SHA256
                    or getattr(original_legacy_descriptor, "__name__", None) != "descriptor"
                    or descriptor_code is None
                    or Path(descriptor_code.co_filename).resolve(strict=True) != legacy_descriptor_path
                    or descriptor_code.co_argcount != 2
                    or descriptor_code.co_kwonlyargcount != 0):
                raise RuntimeError("legacy transition descriptor module or frozen R6 source differs")
            old_legacy_descriptor = original_legacy_descriptor
            legacy_descriptor_wrapper = selected_legacy_descriptor
            legacy_data_module.descriptor = legacy_descriptor_wrapper
        elif profile_spec_record_value.get("source_profile") != data.R11_PROFILE:
            raise RuntimeError("legacy transition descriptor bridge is outside finite R11/R12 profiles")
        # The frozen base execute_lifecycle calls data.current_application_profile
        # and data.descriptor directly from verify_inputs. Bind both normal
        # product seams to this exact R11/R12 context for the whole base call.
        # The actual release_transition_lifecycle constructor imports the
        # historical tools.w26_sealed_data module independently; only the
        # explicit R12 context routes that exact frozen descriptor seam to
        # the already-bound current reader. R11 retains the original function.
        data.current_application_profile = selected_profile
        data.descriptor = selected_descriptor
        lifecycle.inventory = r9_inventory
        transition_lifecycle.SEALED_PHASE_SCRIPT = CURRENT_SEALED_PHASE_SCRIPT
        importlib.util.spec_from_file_location = wrapped_spec_from_file_location
        with _selected_lifecycle_seal(profile_spec, data_input_spec_record):
            yield helper_path
    finally:
        restore_error = False
        for module, original_script, routed_script in reversed(patched_scripts):
            if getattr(module, "SEALED_PHASE_SCRIPT", None) is not routed_script:
                restore_error = True
            module.SEALED_PHASE_SCRIPT = original_script
        if legacy_data_module is not None and old_legacy_descriptor is not None:
            if legacy_data_module.descriptor is not legacy_descriptor_wrapper:
                restore_error = True
            legacy_data_module.descriptor = old_legacy_descriptor
        for cls, original, wrapper in reversed(patched_methods):
            if cls.external_arguments is wrapper:
                cls.external_arguments = original
            else:
                cls.external_arguments = original
                restore_error = True
        try:
            importlib.util.spec_from_file_location = spec_factory
        finally:
            transition_lifecycle.SEALED_PHASE_SCRIPT = old_sealed_phase_script
            data.descriptor = old_descriptor
            data.current_application_profile = old_reader
            lifecycle.inventory = old_inventory
        if restore_error:
            raise RuntimeError("R9 transition consumer method changed before restoration")


def execute_entry(root: Path, binding_path: Path, binding_sha: str, *, candidate: dict,
                  binding: dict, binding_record: dict, began: float, admission_token=None):
    """Enter existing actor.execute with the exact selected current reader tuple.

    Invoke this once from a fresh Python process. It does not create a new
    execution framework and never changes the historical base lifecycle file.
    """
    helper = _helpers()
    binding_raw, binding_row = helper.raw_file(Path(binding_path))
    if binding_row["sha256"] != binding_sha:
        raise RuntimeError("lifecycle Main binding changed before current-reader setup")
    binding = json.loads(binding_raw)
    root = Path(root)
    if binding.get("root") != str(root) or binding.get("status") != "MAIN_APPROVED_ONCE_W26_APPLICATION_LIFECYCLE":
        raise RuntimeError("lifecycle Main binding root or approval state differs")
    candidate_name = binding.get("candidate_filename")
    if not isinstance(candidate_name, str) or not re.fullmatch(r"ACTOR_CANDIDATE_[a-z0-9-]{1,48}\.json", candidate_name):
        raise RuntimeError("lifecycle Main binding candidate name is outside the closed namespace")
    candidate_path = root / candidate_name
    candidate_raw, candidate_row = helper.raw_file(candidate_path)
    if candidate_row["sha256"] != binding.get("candidate_sha256"):
        raise RuntimeError("lifecycle Main binding candidate digest differs")
    candidate = json.loads(candidate_raw)
    prepared_record = candidate.get("prepared")
    if not isinstance(prepared_record, dict) or Path(prepared_record.get("path", "")).parent != root:
        raise RuntimeError("lifecycle candidate prepared record leaves its exact root")
    prepared = json.loads(helper.raw_file(Path(prepared_record["path"]), expected=prepared_record)[0])
    profile_spec_record = prepared.get("current_profile_spec")
    if not isinstance(profile_spec_record, dict) or Path(profile_spec_record.get("path", "")).parent != root:
        raise RuntimeError("lifecycle candidate omits its exact current profile-spec sidecar")
    data_input_spec_record = prepared.get("data_input_spec")
    with r9_execution_context(profile_spec_record, data_input_spec_record):
        return lifecycle.execute_lifecycle(root, candidate, binding, binding_record,
                                           began=began, admission_token=admission_token)


def prepare(root: Path, *, tag: str = "life15", current_application_root: Path,
            dependency_root: Path, data_input_spec_path: Path | None = None,
            data_input_spec_sha256: str | None = None,
            current_system_reader_proof_path: Path | None = None,
            current_system_reader_proof_sha256: str | None = None) -> dict:
    """Prepare the normal current App lifecycle against its selected sealed data profile."""
    root = Path(root)
    dependency_root = Path(dependency_root)
    if (root.parent != Path("/private/tmp") or root != root.resolve()
            or not re.fullmatch(r"cvw26-lifecycle-r15-[0-9a-f]{8}", root.name)
            or not re.fullmatch(r"[a-z0-9-]{1,48}", tag)):
        raise RuntimeError("fresh R15 product lifecycle namespace refused")
    if (dependency_root.parent != Path("/private/tmp") or dependency_root != dependency_root.resolve()
            or dependency_root == root or not dependency_root.name.startswith("cvw26-")):
        raise RuntimeError("R15 lifecycle requires a separately rooted current dependency producer")
    helper = _helpers()
    first = json.loads(helper.raw_file(root / "FIRST_RECORD.json")[0])
    helper.secure_dir(root, first["root_identity"])
    dep_first = json.loads(helper.raw_file(dependency_root / "FIRST_RECORD.json")[0])
    helper.secure_dir(dependency_root, dep_first["root_identity"])
    runtime_record = helper.raw_file(root / "CURRENT_RUNTIME_INPUT.json")[1]
    manifest = helper.raw_file(root / "INPUTS_MANIFEST.json")[1]
    from tools import w26_dependency_unit_current as deps
    runtime = deps.verify_inputs(root, manifest, runtime_record)
    dependency_profiles = {role: helper.raw_file(dependency_root / "evidence" / ("PROFILE_" + role + ".json"))[1]
                           for role in ("builder", "runtime")}
    app.verify_prior_profiles(dependency_profiles)
    dependency_result_record = helper.raw_file(dependency_root / "DEPENDENCY_RESULT.json")[1]
    dependency_result = json.loads(helper.raw_file(
        Path(dependency_result_record["path"]), expected=dependency_result_record)[0])
    if (dependency_result.get("status") != "PASS_ACTUAL_CURRENT43_ENVIRONMENTS_PROFILES_OFFLINE_SCOPED"
            or dependency_result.get("profile_records") != dependency_profiles):
        raise RuntimeError("R15 lifecycle dependency producer/profile closure differs")
    dependency_python = helper.raw_file(dependency_root / "runtime/bin/python", maximum=268435456)[1]
    for name in ("old-wheelhouse", "new-wheelhouse", "transition-work", "current-venv",
                 "current-install-evidence", "uninstall-evidence", "logs", "tmp", "evidence",
                 "outside", "empty-home", "import-home"):
        if (root / name).exists() or (root / name).is_symlink():
            raise RuntimeError("R15 lifecycle fresh effect namespace occupied: " + name)

    current_loader_sources = _loader_records()
    profile_spec = build_current_profile_spec_for_app(Path(current_application_root), current_loader_sources)
    if profile_spec.get("source_profile") not in {data.R11_PROFILE, *data.R13_DATA_APP_PROFILES}:
        raise RuntimeError("R15 lifecycle current application source profile is outside finite R11/R12/R13 support")
    if bool(data_input_spec_path) != bool(data_input_spec_sha256):
        raise RuntimeError("R13 data-input spec path and digest must be supplied together")
    if bool(current_system_reader_proof_path) != bool(current_system_reader_proof_sha256):
        raise RuntimeError("R18 system-reader proof path and digest must be supplied together")
    if profile_spec.get("source_profile") in data.R13_DATA_APP_PROFILES:
        if data_input_spec_path is None:
            raise RuntimeError("current175 R12 product prepare requires an explicit T044 R13 data-input spec")
        selector = data.r13_data_profile_selector(data_input_spec_path, data_input_spec_sha256)
        data_input_spec_record = selector["input_spec_record"]
        if current_system_reader_proof_path is None:
            raise RuntimeError("current175 R12 product prepare requires an explicit Main-bound R18 system-reader proof")
        current_system_reader_proof = system_reader_proof.bind_current_system_reader_proof(
            root, current_system_reader_proof_path, current_system_reader_proof_sha256)
    else:
        if data_input_spec_path is not None:
            raise RuntimeError("legacy R11 product prepare rejects an unrelated R13 data-input spec")
        if current_system_reader_proof_path is not None:
            raise RuntimeError("legacy R11 product prepare rejects an unrelated R18 system-reader proof")
        selector = None
        data_input_spec_record = None
        current_system_reader_proof = None
    profile_spec_record = helper.publish(root / ("CURRENT_PROFILE_SPEC_" + tag + ".json"), profile_spec)
    current_profile = build_current_application_profile(
        Path(current_application_root), current_loader_sources, profile_spec=profile_spec)
    current = current_application_profile(current_profile, profile_spec=profile_spec)
    app_result, new_app = current["result"], current["wheel"]
    if profile_spec["source_profile"] in data.R13_DATA_APP_PROFILES:
        seal_path, seal_sha = selector["seal_record"]["path"], selector["seal_record"]["sha256"]
        closure = data.verify_seal(seal_path, seal_sha, data_selector=selector)
    else:
        selector = None
        seal_path, seal_sha = lifecycle.SEAL, lifecycle.SEAL_SHA
        closure = data.verify_seal(seal_path, seal_sha)
    prior_copy_record = closure["genuine_prior_wheel"]["copy"]
    old_app = data.raw_file(Path(prior_copy_record["path"]), expected=prior_copy_record)[0]
    wheelhouses = {}
    runtime_pins = lifecycle.release.parse_hash_requirements(root / "requirements-runtime.lock", "macos_arm64")
    if {path.name for path in (root / "wheelhouse").glob("*.whl")} != {
            row["filename"] for row in json.loads(helper.raw_file(Path(manifest["path"]), expected=manifest)[0])["rows"]}:
        raise RuntimeError("R15 lifecycle exact43 input wheelhouse membership differs")
    for role, app_blob, appname in (
            ("old", old_app, "comsol_mcp-0.1.9-py3-none-any.whl"),
            ("new", helper.raw_file(Path(new_app["path"]), expected=new_app)[0],
             "comsol_mcp-0.1.10-py3-none-any.whl")):
        folder = root / (role + "-wheelhouse")
        folder.mkdir(mode=0o700, exist_ok=False)
        for path in sorted((root / "wheelhouse").glob("*.whl")):
            if lifecycle.release.wheel_metadata(path)["normalized_name"] in runtime_pins:
                helper.copy_blob(folder / path.name, helper.raw_file(path)[0])
        helper.copy_blob(folder / appname, app_blob)
        lifecycle.release.compose_install_lock(root / "requirements-runtime.lock", folder / appname,
                                               "macos_arm64", root / ("requirements-" + role + ".lock"))
        wheelhouses[role] = {path.name: helper.raw_file(path)[1] for path in sorted(folder.iterdir())}
        if len(wheelhouses[role]) != 42:
            raise RuntimeError("R15 lifecycle whole42 input closure differs")

    descriptor_value = {
        "schema": "w26-sealed-r6-lifecycle-descriptor/1", "unit_root": str(root),
        "root_identity": first["root_identity"], "seal": seal_path, "seal_sha256": seal_sha,
        "synthetic_data": False,
        "old_historical_dependencies": "UNVERIFIED_CURRENT43_NEW_TEST_BASELINE",
        "current_application_profile": current_profile, "current_profile_spec": profile_spec,
    }
    if selector is not None:
        descriptor_value["data_profile"] = data.R13_DATA_PROFILE
        descriptor_value["data_selector"] = selector
        descriptor_value["data_input_spec"] = data_input_spec_record
    descriptor_record = helper.publish(root / ("EXTERNAL_DATA_" + tag + ".json"), descriptor_value)
    marker_model = default_environment()
    old_input = transition_lifecycle.wheel_input(
        lifecycle.release, root / "requirements-old.lock", root / "old-wheelhouse", lifecycle.OLD_SHA, marker_model)
    new_input = transition_lifecycle.wheel_input(
        lifecycle.release, root / "requirements-new.lock", root / "new-wheelhouse", new_app["sha256"], marker_model)
    phase_rows = _inventory_with_r9_helper(root, dependency_python["path"], descriptor_record,
                                            old_input, new_input, profile_spec_record,
                                            data_input_spec_record=data_input_spec_record)
    seal_record = helper.raw_file(Path(seal_path))[1]
    prepared_value = {
        "current_application_profile": current_profile,
        "current_profile_spec": profile_spec_record,
        "data_input_spec": data_input_spec_record,
        "data_profile": data.R13_DATA_PROFILE if selector is not None else "legacy-r6-fixed-seal",
        "data_selector": selector,
        "dependency_root": str(dependency_root), "dependency_root_identity": dep_first["root_identity"],
        "dependency_result": dependency_result_record,
        "dependency_profiles": dependency_profiles, "profiles": dependency_profiles,
        "manifest": manifest, "runtime": runtime_record, "seal": seal_record,
        "app_result": app_result, "app_wheel": new_app,
        "old_wheel": helper.raw_file(root / "old-wheelhouse/comsol_mcp-0.1.9-py3-none-any.whl")[1],
        "python": dependency_python, "descriptor": descriptor_record,
        "requirements_old": helper.raw_file(root / "requirements-old.lock")[1],
        "requirements_new": helper.raw_file(root / "requirements-new.lock")[1],
        "wheelhouses": wheelhouses, "old_input": old_input, "new_input": new_input,
        "phase_rows": phase_rows, "marker_model_NOT_actual_evidence": marker_model,
        "legacy_sources": {name: helper.raw_file(lifecycle.COMMON.parent / file)[1]
                           for name, file in (("common", "common.py"), ("consumers", "consumers.py"),
                                              ("context", "CONTEXT.json"), ("config", "MPh.ini"))},
    }
    if selector is not None:
        prepared_value["system_reader_profile"] = system_reader_proof.PROFILE
        prepared_value["current_system_reader_proof"] = current_system_reader_proof
    else:
        prepared_value["system_reader_signature"] = helper.raw_file(lifecycle.SYSTEM_PS_SIGNATURE)[1]
    prepared = helper.publish(root / ("LIFECYCLE_INPUT_" + tag + ".json"), prepared_value)
    prepare_verification = None
    prepare_verification_record = None
    if selector is not None:
        prepare_verification = verify_inputs(root, prepared_value)
        prepare_verification_record = helper.publish(
            root / ("LIFECYCLE_INPUT_VERIFICATION_R15_" + tag + ".json"), prepare_verification)
    plan = helper.publish(root / ("CURRENT_BUILD_PLAN_" + tag + ".json"), {
        **actor.activity_scope({"activity_kind": actor.APP_LIFECYCLE}),
        "inventory": phase_rows,
        "data_input_spec": data_input_spec_record,
        "data_input_spec_sha256": (selector["input_spec_sha256"] if selector is not None else None),
        "administrative_python": {"path": str(Path(sys.executable).resolve()),
                                  "record": helper.raw_file(Path(sys.executable).resolve(), maximum=268435456)[1],
                                  "product_runtime": False},
        "native_process_umask": "0077",
        "workflow": "existing offline_install/doctor/transition_check with the selected external sealed data",
        "data_profile": prepared_value["data_profile"], "source_profile": profile_spec["source_profile"],
    })
    sources = _records()
    if data_input_spec_record is not None:
        sources[data_input_spec_record["path"]] = data_input_spec_record
    if current_system_reader_proof is not None:
        first_proof_record = current_system_reader_proof["first_record"]
        sources[first_proof_record["path"]] = first_proof_record
    for row in (*prepared_value["legacy_sources"].values(), *dependency_profiles.values(), dependency_python):
        sources[row["path"]] = row
    sources[dependency_result_record["path"]] = dependency_result_record
    if prepare_verification_record is not None:
        sources[prepare_verification_record["path"]] = prepare_verification_record
    source_record = helper.publish(root / ("CURRENT_ACTOR_SOURCE_" + tag + ".json"), sources)
    tools_record = helper.publish(root / ("LIFECYCLE_SYSTEM_" + tag + ".json"), {
        "sandbox": actor.system_record("/usr/bin/sandbox-exec"),
        "ps": actor.system_record("/bin/ps"),
    })
    candidate_record = helper.publish(root / ("ACTOR_CANDIDATE_" + tag + ".json"), {
        "status": "READY_APPLICATION_LIFECYCLE_MAIN_REVIEW_NOT_NATIVE_APPROVED",
        **actor.activity_scope({"activity_kind": actor.APP_LIFECYCLE}),
        "root_identity": first["root_identity"], "prepared": prepared,
        "toolchain": tools_record, "plan": plan, "source": source_record,
        **({"prepare_verification": prepare_verification_record} if prepare_verification_record else {}),
        "native_Popen": 0,
    })
    return candidate_record


def verify_inputs(root: Path, prepared: dict) -> dict:
    profile_spec_record = prepared.get("current_profile_spec")
    if not isinstance(profile_spec_record, dict):
        raise RuntimeError("current lifecycle prepared input omits the profile spec record")
    raw, observed = _helpers().raw_file(Path(profile_spec_record["path"]), expected=profile_spec_record)
    if observed["sha256"] != profile_spec_record["sha256"]:
        raise RuntimeError("current lifecycle profile spec sidecar changed")
    profile_spec = json.loads(raw)
    source_profile = profile_spec.get("source_profile")
    if source_profile not in {data.R11_PROFILE, *data.R13_DATA_APP_PROFILES}:
        raise RuntimeError("current lifecycle adapter only admits finite R11/R12/R13 source profiles")
    if source_profile in data.R13_DATA_APP_PROFILES:
        proof_binding = prepared.get("current_system_reader_proof")
        if (prepared.get("system_reader_profile") != system_reader_proof.PROFILE
                or not isinstance(proof_binding, dict) or "system_reader_signature" in prepared):
            raise RuntimeError("current175 R12 product verify_inputs requires the explicit R18 system-reader proof")
    elif ("current_system_reader_proof" in prepared or "system_reader_profile" in prepared):
        raise RuntimeError("legacy R11 product verify_inputs rejects an unrelated R18 system-reader proof")
    data_input_spec_record = prepared.get("data_input_spec")
    if source_profile in data.R13_DATA_APP_PROFILES:
        if not isinstance(data_input_spec_record, dict):
            raise RuntimeError("current175 R12 product verify_inputs requires an explicit data-input spec")
        data_spec_raw, data_spec_observed = _helpers().raw_file(
            Path(data_input_spec_record.get("path", "")), expected=data_input_spec_record, maximum=65536)
        selector = data.r13_data_profile_selector(data_input_spec_record["path"],
                                                  data_input_spec_record["sha256"])
        if data_spec_observed != selector["input_spec_record"] or json.loads(data_spec_raw) != selector["input_spec"]:
            raise RuntimeError("current lifecycle exact data-input spec bytes changed")
    else:
        if data_input_spec_record is not None:
            raise RuntimeError("legacy R11 product verify_inputs rejects a new R13 data-input spec")
        selector = None
    with _selected_lifecycle_seal(profile_spec, data_input_spec_record):
        result = _with_current_reader(lifecycle.verify_inputs, root, prepared,
                                      profile_spec=profile_spec, data_selector=selector)
    if source_profile not in data.R13_DATA_APP_PROFILES:
        return result
    seal_records = _verify_product_seal_records(selector, prepared.get("seal"))
    if (prepared.get("data_profile") != data.R13_DATA_PROFILE
            or prepared.get("data_selector") != selector
            or prepared.get("data_input_spec") != selector["input_spec_record"]
            or prepared.get("descriptor", {}).get("path") is None
            or prepared.get("phase_rows") is None):
        raise RuntimeError("current lifecycle prepared data selector/profile/phase inventory differs")
    dependency_root = Path(prepared.get("dependency_root", ""))
    if (dependency_root.parent != Path("/private/tmp") or dependency_root != dependency_root.resolve(strict=True)
            or dependency_root == Path(root) or dependency_root.is_symlink()):
        raise RuntimeError("current lifecycle dependency producer root is missing or noncanonical")
    first = json.loads(_helpers().raw_file(dependency_root / "FIRST_RECORD.json")[0])
    if first.get("root_identity") != prepared.get("dependency_root_identity"):
        raise RuntimeError("current lifecycle dependency producer identity changed")
    dependency_result_record = prepared.get("dependency_result")
    if not isinstance(dependency_result_record, dict):
        raise RuntimeError("current lifecycle prepared input omits the dependency producer result")
    dependency_result = json.loads(_helpers().raw_file(
        Path(dependency_result_record["path"]), expected=dependency_result_record)[0])
    if (dependency_result.get("status") != "PASS_ACTUAL_CURRENT43_ENVIRONMENTS_PROFILES_OFFLINE_SCOPED"
            or dependency_result.get("profile_records") != prepared.get("dependency_profiles")):
        raise RuntimeError("current lifecycle dependency producer/profile closure changed")
    dependency_python = _helpers().raw_file(dependency_root / "runtime/bin/python", maximum=268435456)[1]
    if dependency_python != prepared.get("python"):
        raise RuntimeError("current lifecycle dependency Python first record differs")
    app.verify_prior_profiles(prepared.get("dependency_profiles", {}))
    phase_results = _run_r13_phase_input_preflights(
        Path(root), prepared["descriptor"], profile_spec_record, data_input_spec_record,
        selector, prepared["phase_rows"])
    return {"status": "PASS_EXISTING_PRODUCT_VERIFY_INPUTS_AND_FOUR_PHASE_INPUT_ONLY_CONSUMERS",
            "base_lifecycle_verify_inputs": result,
            "data_selector": selector,
            "seal_record_cross_reader_verification": seal_records,
            "phase_input_preflights": phase_results,
            "phase_count": len(phase_results),
            "lifecycle_effects": "NOT_RUN", "restore": "NOT_RUN",
            "COMSOL_JVM_GUI": "NOT_RUN", "raw_sqlite_originals_opened": False}


if __name__ == "__main__":
    parser = __import__("argparse").ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--tag", default="life9")
    parser.add_argument("--capture-current-system-reader-proof", action="store_true")
    parser.add_argument("--current-application-root", type=Path)
    parser.add_argument("--dependency-root", type=Path)
    parser.add_argument("--data-input-spec", type=Path)
    parser.add_argument("--data-input-spec-sha")
    parser.add_argument("--current-system-reader-proof", type=Path)
    parser.add_argument("--current-system-reader-proof-sha")
    arguments = parser.parse_args()
    if bool(arguments.data_input_spec) != bool(arguments.data_input_spec_sha):
        raise RuntimeError("data-input-spec path and digest must be supplied together")
    if bool(arguments.current_system_reader_proof) != bool(arguments.current_system_reader_proof_sha):
        raise RuntimeError("R18 system-reader proof path and digest must be supplied together")
    if arguments.capture_current_system_reader_proof:
        if any(value is not None for value in (arguments.current_application_root, arguments.dependency_root,
                                               arguments.data_input_spec, arguments.data_input_spec_sha,
                                               arguments.current_system_reader_proof,
                                               arguments.current_system_reader_proof_sha)):
            parser.error("proof capture accepts only --root; product lifecycle inputs are not used")
        captured = lifecycle.capture_current_system_reader_proof(arguments.root)
        print(json.dumps(captured, sort_keys=True))
        raise SystemExit(0 if captured.get("status") == "PASS_CURRENT_CODESIGN_STRICT" else 2)
    if arguments.current_application_root is None or arguments.dependency_root is None:
        parser.error("normal lifecycle prepare requires --current-application-root and --dependency-root")
    print(json.dumps(prepare(arguments.root, tag=arguments.tag,
                              current_application_root=arguments.current_application_root,
                              dependency_root=arguments.dependency_root,
                              data_input_spec_path=arguments.data_input_spec,
                              data_input_spec_sha256=arguments.data_input_spec_sha,
                              current_system_reader_proof_path=arguments.current_system_reader_proof,
                              current_system_reader_proof_sha256=arguments.current_system_reader_proof_sha), sort_keys=True))
