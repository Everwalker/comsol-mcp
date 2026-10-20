"""Versioned current175-r9 producer verification for source-external install.

This keeps the historical sealed-data reader unchanged while accepting the
actual, Main-bound application candidate filename recorded in the producer's
once binding.  It is also the isolated helper entrypoint for sealed phases.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import runpy
import sys
import zipfile
from pathlib import Path
from typing import Any


BASE_PATH = Path(__file__).with_name("w26_sealed_data.py")
BASE = runpy.run_path(str(BASE_PATH), run_name="_w26_sealed_data_r9_base")
# Functions executed by runpy retain the namespace dict used during execution.
# `BASE` is the returned mapping and is a separate dict, so keep an explicit
# handle to the function namespace used by run_phase/descriptor/backup helpers.
BASE_NAMESPACE = BASE["run_phase"].__globals__
# R9 is additive. Historical R4-R8 readers and their exact loader closures remain unchanged.
R9_LOCK = Path(__file__).resolve().with_name("w26_application_sources_175_r9_prepared.lock.json")
R9_LOCK_SHA256 = "a524e533f01ed5e5ea0f06cfa5caf49ab0ddf50474258b672265c13f62f987a2"
R9_MEMBER_FINGERPRINT_SHA256 = "401d720192bba5456c8c576f9ed845a74e0555b9c8d4a5e9fa04dcb28d89ef26"
R7_LOCK = Path(__file__).resolve().with_name("w26_application_sources_175_r7_final.lock.json")
R8_LOCK = Path(__file__).resolve().with_name("w26_application_sources_175_r8_prepared.lock.json")
BASE["CURRENT_APPLICATION_R9_LOCK"] = R9_LOCK
BASE["CURRENT_APPLICATION_LOADER_NAMES"] = frozenset(
    set(BASE["CURRENT_APPLICATION_LOADER_NAMES"]) |
    {R7_LOCK.name, R8_LOCK.name, R9_LOCK.name, Path(__file__).name}
)
BASE["CURRENT_APPLICATION_LOADER_MODULE_PATHS"] = {
    "tools." + Path(name).stem: str(Path(BASE["CURRENT_APPLICATION_TOOLS_ROOT"]) / name)
    for name in BASE["CURRENT_APPLICATION_LOADER_NAMES"] if name.endswith(".py")
}
APP_CANDIDATE_RE = re.compile(r"ACTOR_CANDIDATE_[a-z0-9-]{1,48}\.json\Z")


def _get(base: Any, name: str) -> Any:
    return base[name] if isinstance(base, dict) else getattr(base, name)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _source_member_map_fingerprint(members: dict[str, Any]) -> str:
    return _sha(_canonical_bytes(dict(members)))


def _r9_current_application_loader_closure(source_records: dict[str, Any]) -> dict[str, Any]:
    """Close the loader with R9's exact reader, lock, member set, and records."""
    root = Path(BASE["CURRENT_APPLICATION_TOOLS_ROOT"])
    expected_names = BASE["CURRENT_APPLICATION_LOADER_NAMES"]
    if root != root.resolve(strict=True):
        raise RuntimeError("current application tools root is not canonical")
    members = {Path(path).name: record for path, record in source_records.items()
               if Path(path).parent == root}
    if set(members) != expected_names:
        raise RuntimeError("current application loader source closure membership differs")
    normalized = {}
    for name, record in members.items():
        path = root / name
        if record.get("path") != str(path):
            raise RuntimeError("current application loader source path differs")
        observed = BASE["raw_file"](path)[1]
        if (record.get("bytes") != observed["bytes"] or record.get("sha256") != observed["sha256"]
                or record.get("dev") != observed["dev"] or record.get("inode") != observed["inode"]
                or record.get("uid") != observed["uid"] or record.get("nlink") != observed["nlink"]
                or record.get("mode") != (observed["mode"] & 0o777)):
            raise RuntimeError("current application loader source fingerprint differs")
        normalized[name] = observed
    return {"schema": BASE["CURRENT_APPLICATION_LOADER_SCHEMA"], "root": str(root), "members": normalized}


def _r9_verify_current_application_loader_closure(closure: dict[str, Any]) -> Path:
    """Rehash the exact R9 closure before its isolated namespace import."""
    root = Path(BASE["CURRENT_APPLICATION_TOOLS_ROOT"])
    expected_names = BASE["CURRENT_APPLICATION_LOADER_NAMES"]
    if (set(closure) != {"schema", "root", "members"}
            or closure["schema"] != BASE["CURRENT_APPLICATION_LOADER_SCHEMA"]
            or closure["root"] != str(root) or root != root.resolve(strict=True)
            or set(closure["members"]) != expected_names):
        raise RuntimeError("current application loader source closure differs")
    for name in sorted(expected_names):
        path = root / name
        record = closure["members"][name]
        if record.get("path") != str(path):
            raise RuntimeError("current application loader member path differs")
        BASE["raw_file"](path, record)
    return root


# Keep the returned mapping and the existing runpy function namespace in sync.
# This is confined to this fresh, hash-verified reader execution. In
# particular, it does not modify sys.modules or weaken the cached-module gate.
def _sync_r9_reader_namespace() -> None:
    values = {
        "CURRENT_APPLICATION_R9_LOCK": R9_LOCK,
        "CURRENT_APPLICATION_LOADER_NAMES": BASE["CURRENT_APPLICATION_LOADER_NAMES"],
        "CURRENT_APPLICATION_LOADER_MODULE_PATHS": BASE["CURRENT_APPLICATION_LOADER_MODULE_PATHS"],
        "current_application_loader_closure": _r9_current_application_loader_closure,
        "verify_current_application_loader_closure": _r9_verify_current_application_loader_closure,
        "current_application_profile": lambda profile: current_application_profile(profile, BASE),
    }
    BASE.update(values)
    BASE_NAMESPACE.update(values)
    if any(BASE.get(name) is not value or BASE_NAMESPACE.get(name) is not value
           for name, value in values.items()):
        raise RuntimeError("current r9 reader function namespace is not synchronized")


def _producer_records(root: Path, base: Any = BASE) -> dict[str, Any]:
    """Bind the producer's candidate path through its exact once/review chain."""
    root = Path(root)
    from tools.w26_prepare_current_inputs import _helpers
    helper = _helpers()
    if root.parent != Path("/private/tmp") or root != root.resolve() or \
            not re.fullmatch(r"cvw26-application-[0-9a-f]{8}", root.name):
        raise RuntimeError("current r9 application producer root refused")
    first = json.loads(helper.raw_file(root / "FIRST_RECORD.json")[0])
    helper.secure_dir(root, first["root_identity"])

    result_row = helper.raw_file(root / "APPLICATION_BUNDLE_RESULT.json")[1]
    once_row = helper.raw_file(root / "UNIT_ONCE_GUARD.json")[1]
    once = json.loads(helper.raw_file(root / "UNIT_ONCE_GUARD.json", expected=once_row)[0])
    binding_row = once.get("binding_first_record")
    if not isinstance(binding_row, dict) or binding_row.get("path") != str(root / "MAIN_ONCE_BINDING.json"):
        raise RuntimeError("current r9 App once does not bind its original Main once file")
    binding_raw = helper.raw_file(root / "MAIN_ONCE_BINDING.json", expected=binding_row)[0]
    binding = json.loads(binding_raw)
    candidate_name = binding.get("candidate_filename")
    if not isinstance(candidate_name, str) or not APP_CANDIDATE_RE.fullmatch(candidate_name):
        raise RuntimeError("current r9 App candidate filename is not Main-bound in the allowed namespace")
    candidate_row = helper.raw_file(root / candidate_name)[1]
    candidate = json.loads(helper.raw_file(root / candidate_name, expected=candidate_row)[0])
    review_row = helper.raw_file(root / "MAIN_SOURCE_REVIEW.json")[1]
    review = json.loads(helper.raw_file(root / "MAIN_SOURCE_REVIEW.json", expected=review_row)[0])

    if once.get("activity_kind") != "CURRENT_APPLICATION_BUNDLE" or \
            once.get("nonce") != binding.get("nonce") or \
            once.get("binding_first_record") != binding_row:
        raise RuntimeError("current r9 App once/binding activity or nonce differs")
    if binding.get("status") != "MAIN_APPROVED_ONCE_W26_APPLICATION_BUNDLE" or \
            binding.get("root") != str(root) or \
            binding.get("candidate_filename") != candidate_name or \
            binding.get("candidate_sha256") != candidate_row["sha256"] or \
            binding.get("root_identity") != first["root_identity"] or \
            binding.get("review_sha256") != review_row["sha256"]:
        raise RuntimeError("current r9 App exact once/candidate/root/review binding differs")
    if review.get("status") != "PASS_W26_APPLICATION_BUNDLE_SOURCE_FOR_EXACT_CANDIDATE" or \
            review.get("candidate_sha256") != candidate_row["sha256"]:
        raise RuntimeError("current r9 App exact-candidate Main source review is missing")
    if candidate.get("status") != "READY_APPLICATION_BUNDLE_MAIN_REVIEW_NOT_EXECUTION_APPROVED" or \
            candidate.get("root_identity") != first["root_identity"] or candidate.get("native_Popen") != 0:
        raise RuntimeError("current r9 App candidate root/prepare-only state differs")
    return {
        "root_identity": first["root_identity"],
        "result": (json.loads(helper.raw_file(root / "APPLICATION_BUNDLE_RESULT.json", expected=result_row)[0]), result_row),
        "candidate": (candidate, candidate_row),
        "once": (once, once_row),
        "binding": (binding, binding_row),
        "review": (review, review_row),
    }


def build_current_application_profile(root: Path, loader_sources: dict[str, Any], base: Any = BASE) -> dict[str, Any]:
    """Construct an r9 profile from the actual result/once-selected candidate."""
    records = _producer_records(Path(root), base)
    loader = _get(base, "current_application_loader_closure")(loader_sources)
    return {
        "schema": "w26-current-installed-application/1",
        "source_profile": "current175-r9",
        "root": str(Path(root)),
        "result": records["result"][1],
        "candidate": records["candidate"][1],
        "once": records["once"][1],
        "loader_source_closure": loader,
    }


def current_application_profile(profile: dict[str, Any], base: Any = BASE) -> dict[str, Any]:
    """Verify the exact completed current175-r9 App producer and return its wheel."""
    if set(profile) != {"schema", "source_profile", "root", "result", "candidate", "once", "loader_source_closure"}:
        raise RuntimeError("closed current r9 installed application profile fields differ")
    if profile.get("schema") != "w26-current-installed-application/1" or \
            profile.get("source_profile") != "current175-r9":
        raise RuntimeError("current r9 installed application profile selector differs")

    tools_root = _get(base, "verify_current_application_loader_closure")(profile["loader_source_closure"])
    app_module, actor_module = _get(base, "_install_current_application_tools_namespace")(
        tools_root, profile["loader_source_closure"])
    from tools.w26_prepare_current_inputs import _helpers
    helper = _helpers()

    root = Path(profile["root"])
    authoritative = _producer_records(root, base)
    result, result_row = authoritative["result"]
    candidate, candidate_row = authoritative["candidate"]
    once, once_row = authoritative["once"]
    if profile["result"] != result_row or profile["candidate"] != candidate_row or profile["once"] != once_row:
        raise RuntimeError("current r9 profile producer records do not match the authoritative App artifacts")
    if profile["result"]["path"] != str(root / "APPLICATION_BUNDLE_RESULT.json") or \
            profile["once"]["path"] != str(root / "UNIT_ONCE_GUARD.json") or \
            profile["candidate"]["path"] != str(root / authoritative["binding"][0]["candidate_filename"]):
        raise RuntimeError("current r9 profile App producer paths differ from exact result/once binding")

    if result.get("status") != "PASS_CURRENT_APPLICATION_BUNDLE_SCOPED_NOT_RELEASE" or \
            result.get("version") != "0.1.10" or result.get("target") != "macos_arm64" or \
            result.get("build", {}).get("source_file_count") != 175 or \
            result.get("COMSOL_JVM_GUI") != "NOT_RUN":
        raise RuntimeError("current r9 application producer is not an exact completed175 App build")

    candidate_prepared = candidate.get("prepared")
    if not isinstance(candidate_prepared, dict) or Path(candidate_prepared.get("path", "")).parent != root:
        raise RuntimeError("current r9 App prepared record leaves exact producer root")
    prepared_match = re.fullmatch(r"APP_INPUT_BINDING_([a-z0-9-]{1,48})\.json", Path(candidate_prepared["path"]).name)
    plan_record = candidate.get("plan")
    if (prepared_match is None or not isinstance(plan_record, dict)
            or Path(plan_record.get("path", "")).parent != root):
        raise RuntimeError("current r9 App prepared/plan record namespace differs")
    plan_match = re.fullmatch(r"CURRENT_BUILD_PLAN_([a-z0-9-]{1,48})\.json", Path(plan_record["path"]).name)
    if plan_match is None or plan_match.group(1) != prepared_match.group(1):
        raise RuntimeError("current r9 App plan/prepared tags differ")
    plan = json.loads(helper.raw_file(Path(plan_record["path"]), expected=plan_record)[0])
    prepared = json.loads(helper.raw_file(Path(candidate_prepared["path"]), expected=candidate_prepared)[0])
    expected_commands = app_module.commands(root, prepared["builder"]["path"])
    if (plan.get("activity_kind") != "CURRENT_APPLICATION_BUNDLE" or plan.get("version") != "0.1.10"
            or plan.get("target") != "macos_arm64" or plan.get("commands") != expected_commands
            or plan.get("delivery") != prepared.get("delivery")):
        raise RuntimeError("current r9 App build plan differs from reviewed producer/source profile")
    context_record = plan.get("context")
    if (not isinstance(context_record, dict) or Path(context_record.get("path", "")).parent != root
            or Path(context_record["path"]).name != "APP_CONTEXT_" + prepared_match.group(1) + ".json"):
        raise RuntimeError("current r9 App context record namespace differs")
    context = json.loads(helper.raw_file(Path(context_record["path"]), expected=context_record)[0])
    if (context.get("activity_kind") != "CURRENT_APPLICATION_BUNDLE" or context.get("unit_root") != str(root)
            or context.get("root_identity") != candidate.get("root_identity")
            or context.get("outside") != str(root / "outside") or context.get("commands") != expected_commands
            or context.get("legacy_sources") != prepared.get("legacy_sources")):
        raise RuntimeError("current r9 App build context differs from its source-bound plan")
    source = prepared.get("source", {})
    snapshot = Path(source.get("snapshot_source", ""))
    if source.get("source_profile") != "current175-r9" or snapshot != root / "source-input":
        raise RuntimeError("current r9 App source profile/snapshot path differs")
    lock_path = Path(_get(base, "CURRENT_APPLICATION_R9_LOCK"))
    lock_path = lock_path.resolve(strict=True)
    lock_row = source.get("source_lock", {})
    if lock_row.get("path") != str(lock_path) or lock_row.get("sha256") != R9_LOCK_SHA256:
        raise RuntimeError("current r9 App exact reviewed source lock binding differs")
    lock = json.loads(helper.raw_file(lock_path, expected=lock_row)[0])
    if lock.get("schema") != "w26-application-source-closure/1" or \
            lock.get("profile") != "current175-r9" or lock.get("member_count") != 175 or \
            lock.get("package_count") != 172 or lock.get("root_count") != 3:
        raise RuntimeError("current r9 App source lock schema/member counts differ")
    fingerprint = _source_member_map_fingerprint(lock["members"])
    if fingerprint != R9_MEMBER_FINGERPRINT_SHA256:
        raise RuntimeError("current r9 App source member fingerprint differs from reviewed generation")
    snapshot_summary = app_module.verify_source_input_r9(source, require_live_origin=False)
    origin = source["origin_provenance_snapshot"]
    if not isinstance(snapshot_summary, dict) or snapshot_summary != {
            "source_snapshot_member_count": 175,
            "source_snapshot_fingerprint_sha256": fingerprint,
            "origin_snapshot_member_count": 175,
            "origin_snapshot_fingerprint_sha256": fingerprint,
    }:
        raise RuntimeError("current r9 frozen source snapshot readback differs")

    extras = prepared.get("extras")
    copied_extras = prepared.get("copied_extras")
    if (not isinstance(extras, dict) or set(extras) != {"uv_lock", "operations"}
            or not isinstance(copied_extras, dict) or set(copied_extras) != set(extras)):
        raise RuntimeError("current r9 frozen extra-input closure differs")
    delivery = prepared.get("delivery")
    for name, copied_record in copied_extras.items():
        basename = "uv.lock" if name == "uv_lock" and delivery is not None else "INPUT_" + name
        path = root / basename
        if copied_record.get("path") != str(path):
            raise RuntimeError("current r9 frozen extra input path differs")
        helper.raw_file(path, expected=copied_record)
        if {key: copied_record.get(key) for key in ("bytes", "sha256")} != {
                key: extras[name].get(key) for key in ("bytes", "sha256")}:
            raise RuntimeError("current r9 frozen extra input differs from its captured provenance")

    commands = result.get("commands", [])
    if len(commands) != 2:
        raise RuntimeError("current r9 App requires exactly two terminal original captures")
    for row, label in zip(commands, ("builder-check", "application-build")):
        if row.get("status") != "PASS" or row.get("rc") != 0 or \
                row.get("owned_group_absent") is not True or row.get("owned_process_terminal") is not True or \
                row.get("birth", {}).get("status") != "OBSERVED" or row.get("birth", {}).get("pid") != row.get("pid") or \
                not str(row.get("result_name", "")).startswith(label + "-") or \
                Path(str(row.get("result_name", ""))).name != row.get("result_name"):
            raise RuntimeError("current r9 App original capture terminal proof differs")
        terminal_path = root / "logs" / row["result_name"]
        terminal = json.loads(helper.raw_file(terminal_path)[0])
        if terminal != {key: value for key, value in row.items() if key != "result_name"}:
            raise RuntimeError("current r9 App original terminal capture record differs")
        execution = json.loads(helper.raw_file(root / "evidence" / ("EXEC_" + label + ".json"))[0])
        probe_record = execution["network_probe_first_record"]
        if execution.get("same_original_pid") != row["pid"] or execution.get("inherited_group") != row["pid"] or \
                execution.get("extra_processes") != 0 or probe_record.get("path") != str(root / "evidence" / ("PROBE_" + label + ".json")):
            raise RuntimeError("current r9 App original PID/network evidence binding differs")
        probe = json.loads(helper.raw_file(Path(probe_record["path"]), expected=probe_record)[0])
        if probe.get("status") != "PASS_ACTUAL_EPERM" or probe.get("errno") not in (1, 13):
            raise RuntimeError("current r9 App original OS network denial is unproven")

    wheel = root / "appwheel" / "comsol_mcp-0.1.10-py3-none-any.whl"
    wheel_raw, wheel_record = helper.raw_file(wheel)
    build = result["build"]
    if wheel_record.get("sha256") != build.get("sha256") or wheel_record.get("bytes") != build.get("bytes") or \
            wheel_record not in result.get("outputs", []):
        raise RuntimeError("current r9 App wheel identity differs from actual build result")
    from tools.w26_target_inputs import wheel_record as verify_wheel
    metadata = verify_wheel(wheel_raw, wheel.name, "macos_arm64")
    if metadata.get("name") != "comsol-mcp" or metadata.get("version") != "0.1.10":
        raise RuntimeError("current r9 App wheel metadata differs")
    with zipfile.ZipFile(io.BytesIO(wheel_raw)) as archive:
        members = app_module.source_members(snapshot)
        package = {name for name in archive.namelist() if name.startswith("comsol_mcp/") and not name.endswith("/")}
        if package != {rel for rel in members if rel.startswith("comsol_mcp/")} or len(package) != 172:
            raise RuntimeError("current r9 App wheel exact172 payload differs")
        for rel in package:
            if archive.read(rel) != helper.raw_file(members[rel], expected=source["snapshot_members"][rel])[0]:
                raise RuntimeError("current r9 App wheel/source bytes differ")
    source_provenance = {
        "schema": "w26-source-provenance-snapshot-binding/1",
        "source_profile": "current175-r9",
        "source_lock_path": str(lock_path),
        "source_lock_sha256": lock_row["sha256"],
        "member_count": 175,
        "package_count": 172,
        "root_count": 3,
        "member_fingerprint_sha256": fingerprint,
        "source_snapshot_path": str(snapshot),
        "source_snapshot_member_count": snapshot_summary["source_snapshot_member_count"],
        "source_snapshot_fingerprint_sha256": snapshot_summary["source_snapshot_fingerprint_sha256"],
        "origin_snapshot_path": origin["snapshot_source"],
        "origin_snapshot_member_count": snapshot_summary["origin_snapshot_member_count"],
        "origin_snapshot_fingerprint_sha256": snapshot_summary["origin_snapshot_fingerprint_sha256"],
        "source_snapshot_record_path": candidate_prepared["path"],
        "source_snapshot_record_sha256": candidate_prepared["sha256"],
    }
    return {"wheel": wheel_record, "result": result_row,
            "candidate": candidate_row, "once": once_row,
            "review": authoritative["review"][1],
            "backup_source_sha256": source["snapshot_members"]["comsol_mcp/_state_backup.py"]["sha256"],
            "source_provenance": source_provenance}


_sync_r9_reader_namespace()


def run_phase(descriptor: str, sha: str, phase: str) -> None:
    """Run the existing sealed phase through this versioned r9 reader."""
    _sync_r9_reader_namespace()
    BASE["run_phase"](descriptor, sha, phase)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--descriptor", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--phase", choices=("old-read", "candidate-backup", "restore", "old-restored-read"), required=True)
    args = parser.parse_args(argv)
    run_phase(args.descriptor, args.sha, args.phase)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
