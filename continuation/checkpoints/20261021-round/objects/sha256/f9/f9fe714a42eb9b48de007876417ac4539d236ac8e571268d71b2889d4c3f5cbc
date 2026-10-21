"""Fresh current175-r6 lifecycle candidate preparer with the exact App producer binding.

This versioned preparer leaves the historical W26 lifecycle actor untouched.
It binds the App candidate filename from MAIN_ONCE_BINDING.json and pins this
module plus the r6 sealed-data helper in the nested lifecycle source closure.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from packaging.markers import default_environment

from tools import release_transition_lifecycle as transition_lifecycle
from tools import w26_application_lifecycle as lifecycle
from tools import w26_application_bundle as app
from tools import w26_runtime_actor as actor
from tools import w26_sealed_data as data
from tools.w26_application_bundle import _helpers
from tools.w26_sealed_data_r6 import build_current_application_profile, current_application_profile


SEALED_R6_HELPER = Path(__file__).with_name("w26_sealed_data_r6.py").resolve(strict=True)
R6_LOCKS = (
    Path(data.__file__).with_name("w26_application_sources_175_r4.lock.json"),
    Path(data.__file__).with_name("w26_application_sources_175_r5.lock.json"),
    Path(data.__file__).with_name("w26_application_sources_175_r6.lock.json"),
)
TARGET_INPUTS = Path(data.__file__).with_name("w26_target_inputs.py")


def _loader_records() -> dict[str, dict]:
    sources = actor.source_fingerprints(actor.APP_LIFECYCLE)
    for path in (TARGET_INPUTS, *R6_LOCKS):
        row = _helpers().raw_file(path)[1]
        sources[row["path"]] = row
    return sources


def _records() -> dict[str, dict]:
    sources = _loader_records()
    for path in (Path(__file__).resolve(), SEALED_R6_HELPER):
        row = _helpers().raw_file(path)[1]
        sources[row["path"]] = row
    return sources


def _with_r6_reader(function, *args, **kwargs):
    """Use the versioned reader only for this candidate preparation call."""
    old_reader = data.current_application_profile
    data.current_application_profile = lambda profile: current_application_profile(profile, data)
    try:
        return function(*args, **kwargs)
    finally:
        data.current_application_profile = old_reader


def _inventory_with_r6_helper(root, python, descriptor, old_input, new_input):
    """Point only the sealed-phase helper argv at the versioned reader."""
    old_file = data.__file__
    data.__file__ = str(SEALED_R6_HELPER)
    try:
        return lifecycle.inventory(root, python, descriptor, old_input, new_input)
    finally:
        data.__file__ = old_file


def prepare(root: Path, *, tag: str = "life6r", current_application_root: Path) -> dict:
    """Prepare a source-only R6 lifecycle candidate from completed App6e evidence."""
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
    current_profile = build_current_application_profile(Path(current_application_root), current_loader_sources, data)
    current = current_application_profile(current_profile, data)
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
        "inventory": _inventory_with_r6_helper(root, python["path"], descriptor, old_input, new_input),
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
    return _with_r6_reader(lifecycle.verify_inputs, root, prepared)


if __name__ == "__main__":
    parser = __import__("argparse").ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--tag", default="life6r")
    parser.add_argument("--current-application-root", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(prepare(arguments.root, tag=arguments.tag,
                              current_application_root=arguments.current_application_root), sort_keys=True))
