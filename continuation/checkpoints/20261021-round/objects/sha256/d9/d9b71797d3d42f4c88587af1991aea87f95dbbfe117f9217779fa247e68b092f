"""Fresh current175-r9 lifecycle candidate preparer with the exact App producer binding.

This versioned preparer leaves the historical W26 lifecycle actor untouched.
It binds the App candidate filename from MAIN_ONCE_BINDING.json and pins this
module plus the r9 sealed-data helper in the nested lifecycle source closure.
"""
from __future__ import annotations

import json
import importlib.util
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from packaging.markers import default_environment

from tools import release_transition_lifecycle as transition_lifecycle
from tools import w26_application_lifecycle as lifecycle
from tools import w26_application_bundle as app
from tools import w26_runtime_actor as actor
from tools import w26_sealed_data as data
from tools import w26_sealed_data_r9 as sealed_r9
from tools.w26_application_bundle import _helpers
from tools.w26_sealed_data_r9 import build_current_application_profile, current_application_profile


SEALED_R9_HELPER = Path(__file__).with_name("w26_sealed_data_r9.py").resolve(strict=True)
R9_SEALED_PHASES = ("old-read", "candidate-backup", "restore", "old-restored-read")
BASE_TRANSITION_CONSUMER_SHA256 = "08591b2a0adcceef0516a9c448e1def6b3c4f14fac0fd471a6f8276e61b87c41"
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
    return sources


def _records() -> dict[str, dict]:
    sources = _loader_records()
    for path in (Path(__file__).resolve(), SEALED_R9_HELPER):
        row = _helpers().raw_file(path)[1]
        sources[row["path"]] = row
    return sources


def _with_r9_reader(function, *args, **kwargs):
    """Use the versioned reader only for this candidate preparation call."""
    old_reader = data.current_application_profile
    data.current_application_profile = lambda profile: current_application_profile(profile, sealed_r9.BASE)
    try:
        return function(*args, **kwargs)
    finally:
        data.current_application_profile = old_reader


def _inventory_with_r9_helper(root, python, descriptor, old_input, new_input):
    """Build the base inventory and replace only its four sealed helper argv."""
    rows = lifecycle.inventory(root, python, descriptor, old_input, new_input)
    return _route_sealed_phase_rows(rows, SEALED_R9_HELPER)


def _route_sealed_phase_rows(rows, helper_path):
    """Copy the inventory, changing only the fixed R9 helper operand."""
    helper = str(Path(helper_path).resolve(strict=True))
    base_helper = str(Path(data.__file__).with_name("w26_sealed_data.py").resolve(strict=True))
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
        if not isinstance(argv, list) or len(argv) < 4 or argv[-4] != base_helper or argv[-1] != phase:
            raise RuntimeError("base sealed-phase inventory argv differs before R9 routing")
        found.add(phase)
        output.append({**row, "request_argv": [*argv[:-4], helper, *argv[-3:]]})
    if found != set(R9_SEALED_PHASES):
        raise RuntimeError("R9 lifecycle requires exactly four fixed sealed phases")
    return output


@contextmanager
def r9_execution_context():
    """Bind the existing W26 lifecycle's R9 reader and exact consumer seams.

    The existing actor owns candidate admission and all execution policy. This
    context routes the sealed profile reader and the four exact sealed helper
    arguments. It restores every patched method and module function on exit.
    """
    helper_path = Path(SEALED_R9_HELPER).resolve(strict=True)
    reader_path = Path(sealed_r9.__file__).resolve(strict=True)
    if helper_path != reader_path:
        raise RuntimeError("R9 lifecycle execution helper is not the imported exact reader")
    old_reader = data.current_application_profile
    old_inventory = lifecycle.inventory
    release = lifecycle.release
    spec_factory = importlib.util.spec_from_file_location
    base_module_path = Path(transition_lifecycle.__file__).resolve(strict=True)
    base_module_record = _helpers().raw_file(base_module_path)[1]
    if base_module_record["sha256"] != BASE_TRANSITION_CONSUMER_SHA256:
        raise RuntimeError("canonical transition consumer file hash differs")
    base_method = transition_lifecycle.Lifecycle.external_arguments
    base_method_code = base_method.__code__
    expected_inventory = {"rows": None, "by_phase": None}
    patched_methods = []

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
            if not isinstance(argv, list) or len(argv) < 4:
                raise RuntimeError("approved R9 sealed-phase argv is malformed")
            expected = argv[-4:]
            base_arguments = original(instance, phase)
            expected_base_helper = str(base_module_path.with_name("w26_sealed_data.py"))
            if (len(expected) != 4 or expected[0] != str(helper_path)
                    or expected[1] != getattr(instance, "external_descriptor", None)
                    or expected[2] != getattr(instance, "external_descriptor_sha", None)
                    or expected[3] != phase
                    or base_arguments != [expected_base_helper, expected[1], expected[2], phase]):
                raise RuntimeError("actual sealed consumer arguments differ from exact R9 inventory")
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
        routed_rows = _route_sealed_phase_rows(base_rows, helper_path)
        by_phase = {row["label"].removeprefix("data-"): row for row in routed_rows
                    if row.get("label") in {"data-" + phase for phase in R9_SEALED_PHASES}}
        if expected_inventory["rows"] is not None and expected_inventory["rows"] != routed_rows:
            raise RuntimeError("R9 lifecycle inventory changed during the actor scope")
        expected_inventory["rows"] = routed_rows
        expected_inventory["by_phase"] = by_phase
        return routed_rows

    try:
        data.current_application_profile = lambda profile: current_application_profile(profile, sealed_r9.BASE)
        lifecycle.inventory = r9_inventory
        importlib.util.spec_from_file_location = wrapped_spec_from_file_location
        yield helper_path
    finally:
        restore_error = False
        for cls, original, wrapper in reversed(patched_methods):
            if cls.external_arguments is wrapper:
                cls.external_arguments = original
            else:
                cls.external_arguments = original
                restore_error = True
        try:
            importlib.util.spec_from_file_location = spec_factory
        finally:
            data.current_application_profile = old_reader
            lifecycle.inventory = old_inventory
        if restore_error:
            raise RuntimeError("R9 transition consumer method changed before restoration")


def execute_entry(root: Path, binding_path: Path, binding_sha: str):
    """Enter the existing actor.execute path with the exact R9 lifecycle seams.

    Invoke this once from a fresh Python process. It does not create a new
    execution framework and never changes the historical base lifecycle file.
    """
    with r9_execution_context():
        return actor.execute(root, binding_path, binding_sha)


def prepare(root: Path, *, tag: str = "life9", current_application_root: Path) -> dict:
    """Prepare a source-only R9 lifecycle candidate from the exact R9 App bundle."""
    root = Path(root)
    if root.parent != Path("/private/tmp") or root != root.resolve() or \
            not root.name.startswith("cvw26-") or not re.fullmatch(r"[a-z0-9-]{1,48}", tag):
        raise RuntimeError("fresh lifecycle namespace refused")
    first = json.loads(_helpers().raw_file(root / "FIRST_RECORD.json")[0])
    helper = _helpers()
    helper.secure_dir(root, first["root_identity"])
    closure = data.verify_seal(lifecycle.SEAL, lifecycle.SEAL_SHA)
    runtime_record = helper.raw_file(root / "CURRENT_RUNTIME_INPUT.json")[1]
    manifest = helper.raw_file(root / "INPUTS_MANIFEST.json")[1]
    from tools import w26_dependency_unit as deps
    deps.verify_inputs(root, manifest, runtime_record)
    profiles = {role: helper.raw_file(lifecycle.DEP_ROOT / "evidence" / ("PROFILE_" + role + ".json"))[1]
                for role in ("builder", "runtime")}
    app.verify_prior_profiles(profiles)
    for name in ("old-wheelhouse", "new-wheelhouse", "transition-work", "current-venv",
                 "current-install-evidence", "uninstall-evidence", "logs", "tmp", "evidence",
                 "outside", "empty-home", "import-home"):
        if (root / name).exists() or (root / name).is_symlink():
            raise RuntimeError("lifecycle fresh effect namespace occupied")

    current_loader_sources = _loader_records()
    current_profile = build_current_application_profile(Path(current_application_root), current_loader_sources, sealed_r9.BASE)
    current = current_application_profile(current_profile, sealed_r9.BASE)
    app_result, new_app = current["result"], current["wheel"]
    old_app = data.raw_file(closure["genuine_prior_wheel"]["copy"]["path"], closure["genuine_prior_wheel"]["copy"])[0]
    wheelhouses = {}
    runtime_pins = lifecycle.release.parse_hash_requirements(root / "requirements-runtime.lock", "macos_arm64")
    for role, app_blob, appname in (
        ("old", old_app, "comsol_mcp-0.1.9-py3-none-any.whl"),
        ("new", helper.raw_file(Path(new_app["path"]), expected=new_app)[0], "comsol_mcp-0.1.10-py3-none-any.whl"),
    ):
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
            raise RuntimeError("whole lifecycle42 input closure differs")

    descriptor = helper.publish(root / ("EXTERNAL_DATA_" + tag + ".json"), {
        # The sealed descriptor protocol is still the R6-compatible format;
        # its embedded current_application_profile is strictly R9.
        "schema": "w26-sealed-r6-lifecycle-descriptor/1", "unit_root": str(root),
        "seal": lifecycle.SEAL, "seal_sha256": lifecycle.SEAL_SHA, "synthetic_data": False,
        "old_historical_dependencies": "UNVERIFIED_CURRENT43_NEW_TEST_BASELINE",
        "current_application_profile": current_profile,
    })
    python = helper.raw_file(lifecycle.DEP_ROOT / "runtime/bin/python", maximum=268435456)[1]
    marker_model = default_environment()
    old_input = transition_lifecycle.wheel_input(lifecycle.release, root / "requirements-old.lock",
                                                  root / "old-wheelhouse", lifecycle.OLD_SHA, marker_model)
    new_input = transition_lifecycle.wheel_input(lifecycle.release, root / "requirements-new.lock",
                                                  root / "new-wheelhouse", new_app["sha256"], marker_model)
    prepared_value = {
        "current_application_profile": current_profile,
        "system_reader_signature": helper.raw_file(lifecycle.SYSTEM_PS_SIGNATURE)[1],
        "manifest": manifest, "runtime": runtime_record,
        "seal": helper.raw_file(Path(lifecycle.SEAL))[1],
        "profiles": profiles, "app_result": app_result, "app_wheel": new_app,
        "old_wheel": helper.raw_file(root / "old-wheelhouse/comsol_mcp-0.1.9-py3-none-any.whl")[1],
        "python": python, "descriptor": descriptor,
        "requirements_old": helper.raw_file(root / "requirements-old.lock")[1],
        "requirements_new": helper.raw_file(root / "requirements-new.lock")[1],
        "wheelhouses": wheelhouses, "old_input": old_input, "new_input": new_input,
        "marker_model_NOT_actual_evidence": marker_model,
        "legacy_sources": {name: helper.raw_file(lifecycle.COMMON.parent / file)[1]
                           for name, file in (("common", "common.py"), ("consumers", "consumers.py"),
                                              ("context", "CONTEXT.json"), ("config", "MPh.ini"))},
    }
    prepared = helper.publish(root / ("LIFECYCLE_INPUT_" + tag + ".json"), prepared_value)
    plan = helper.publish(root / ("CURRENT_BUILD_PLAN_" + tag + ".json"), {
        **actor.activity_scope({"activity_kind": actor.APP_LIFECYCLE}),
        "inventory": _inventory_with_r9_helper(root, python["path"], descriptor, old_input, new_input),
        "administrative_python": {"path": str(Path(sys.executable).resolve()),
                                  "record": helper.raw_file(Path(sys.executable).resolve(), maximum=268435456)[1],
                                  "product_runtime": False},
        "native_process_umask": "0077",
        "workflow": "existing offline_install/doctor/transition_check with external sealed data/uninstall only",
    })
    sources = _records()
    for row in prepared_value["legacy_sources"].values():
        sources[row["path"]] = row
    source_record = helper.publish(root / ("CURRENT_ACTOR_SOURCE_" + tag + ".json"), sources)
    tools_record = helper.publish(root / ("LIFECYCLE_SYSTEM_" + tag + ".json"), {
        "sandbox": actor.system_record("/usr/bin/sandbox-exec"),
        "ps": actor.system_record("/bin/ps"),
    })
    return helper.publish(root / ("ACTOR_CANDIDATE_" + tag + ".json"), {
        "status": "READY_APPLICATION_LIFECYCLE_MAIN_REVIEW_NOT_NATIVE_APPROVED",
        **actor.activity_scope({"activity_kind": actor.APP_LIFECYCLE}),
        "root_identity": first["root_identity"], "prepared": prepared,
        "toolchain": tools_record, "plan": plan, "source": source_record, "native_Popen": 0,
    })


def verify_inputs(root: Path, prepared: dict) -> dict:
    return _with_r9_reader(lifecycle.verify_inputs, root, prepared)


if __name__ == "__main__":
    parser = __import__("argparse").ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--tag", default="life9")
    parser.add_argument("--current-application-root", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(prepare(arguments.root, tag=arguments.tag,
                              current_application_root=arguments.current_application_root), sort_keys=True))
