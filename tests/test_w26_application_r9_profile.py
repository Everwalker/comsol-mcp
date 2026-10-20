from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

from tools import w26_application_bundle as app
from tools import w26_sealed_data_r9 as reader
from tools.w26_prepare_current_inputs import _helpers
from tools import w26_target_inputs
from tests.w26_r9_source_fixture import saved_r9_source_fixture


REPOSITORY = Path(__file__).resolve().parents[1]
EXPECTED_LOCK_SHA = "a524e533f01ed5e5ea0f06cfa5caf49ab0ddf50474258b672265c13f62f987a2"
EXPECTED_MEMBER_FINGERPRINT = "401d720192bba5456c8c576f9ed845a74e0555b9c8d4a5e9fa04dcb28d89ef26"


def _copy_locked_source(destination: Path) -> None:
    source_snapshot, extras = saved_r9_source_fixture()
    for relative, source_path in app.source_members(source_snapshot).items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, target)
    for name, relative in {
        "uv_lock": "uv.lock",
        "operations": "docs/full_project_execution/release/RELEASE_OPERATIONS.md",
    }.items():
        source_path = extras[name]
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, target)


def _write_valid_wheel(snapshot: Path, wheel_path: Path) -> None:
    files = {
        relative: path.read_bytes()
        for relative, path in app.source_members(snapshot).items()
        if relative.startswith("comsol_mcp/")
    }
    assert len(files) == 172
    dist_info = "comsol_mcp-0.1.10.dist-info"
    files[f"{dist_info}/METADATA"] = (
        b"Metadata-Version: 2.1\nName: comsol-mcp\nVersion: 0.1.10\nRequires-Python: >=3.12\n\n"
    )
    files[f"{dist_info}/WHEEL"] = (
        b"Wheel-Version: 1.0\nGenerator: r9-source-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n"
    )
    record_path = f"{dist_info}/RECORD"
    record = io.StringIO(newline="")
    writer = csv.writer(record, lineterminator="\n")
    for name, raw in sorted(files.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode("ascii")
        writer.writerow((name, "sha256=" + digest, str(len(raw))))
    writer.writerow((record_path, "", ""))
    files[record_path] = record.getvalue().encode()
    wheel_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(wheel_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)


def _artifact_fixture(tmp_path: Path, monkeypatch, root_path: Path | None = None):
    helper = _helpers()
    root = Path(root_path) if root_path is not None else tmp_path / "app-root"
    root.parent.mkdir(parents=True, exist_ok=True)
    root.mkdir(mode=0o700)
    original = tmp_path / "mutable-origin"
    _copy_locked_source(original)
    captured = app.source_capture(original, root / "source-input", profile="current175-r9")
    assert set(captured["snapshot_members"]) == set(captured["origin_provenance_snapshot"]["snapshot_members"])
    assert app.verify_source_input_r9(captured, require_live_origin=True)["source_snapshot_member_count"] == 175

    extras = {
        "uv_lock": helper.raw_file(original / "uv.lock")[1],
        "operations": helper.raw_file(original / "docs/full_project_execution/release/RELEASE_OPERATIONS.md")[1],
    }
    copied_extras = app.copy_application_extras(root, extras, delivery=None)
    builder = {"path": str(tmp_path / "builder" / "bin" / "python")}
    prepared = {
        "source": captured,
        "builder": builder,
        "extras": extras,
        "copied_extras": copied_extras,
        "delivery": None,
        "legacy_sources": {},
    }
    tag = "r9test"
    prepared_record = helper.publish(root / f"APP_INPUT_BINDING_{tag}.json", prepared)
    commands = app.commands(root, builder["path"])
    context = {
        "activity_kind": "CURRENT_APPLICATION_BUNDLE",
        "unit_root": str(root),
        "root_identity": {"device": root.stat().st_dev, "inode": root.stat().st_ino},
        "outside": str(root / "outside"),
        "environment": {},
        "legacy_sources": {},
        "commands": commands,
    }
    context_record = helper.publish(root / f"APP_CONTEXT_{tag}.json", context)
    plan = {
        "activity_kind": "CURRENT_APPLICATION_BUNDLE",
        "version": "0.1.10",
        "target": "macos_arm64",
        "commands": commands,
        "delivery": None,
        "context": context_record,
    }
    plan_record = helper.publish(root / f"CURRENT_BUILD_PLAN_{tag}.json", plan)
    root_identity = context["root_identity"]

    wheel_path = root / "appwheel" / "comsol_mcp-0.1.10-py3-none-any.whl"
    _write_valid_wheel(root / "source-input", wheel_path)
    wheel_record = helper.raw_file(wheel_path)[1]
    result = {
        "status": "PASS_CURRENT_APPLICATION_BUNDLE_SCOPED_NOT_RELEASE",
        "version": "0.1.10",
        "target": "macos_arm64",
        "build": {"source_file_count": 175, "sha256": wheel_record["sha256"], "bytes": wheel_record["bytes"]},
        "COMSOL_JVM_GUI": "NOT_RUN",
        "outputs": [wheel_record],
        "commands": [],
    }
    (root / "logs").mkdir()
    (root / "evidence").mkdir()
    for index, label in enumerate(("builder-check", "application-build"), start=63000):
        terminal = {
            "status": "PASS", "rc": 0, "pid": index,
            "owned_group_absent": True, "owned_process_terminal": True,
            "birth": {"status": "OBSERVED", "pid": index},
        }
        result_name = label + "-terminal.json"
        helper.publish(root / "logs" / result_name, terminal)
        probe_path = root / "evidence" / ("PROBE_" + label + ".json")
        helper.publish(probe_path, {"status": "PASS_ACTUAL_EPERM", "errno": 1})
        probe_record = helper.raw_file(probe_path)[1]
        helper.publish(root / "evidence" / ("EXEC_" + label + ".json"), {
            "same_original_pid": index,
            "inherited_group": index,
            "extra_processes": 0,
            "network_probe_first_record": probe_record,
        })
        result["commands"].append({**terminal, "result_name": result_name})

    result_record = helper.publish(root / "APPLICATION_BUNDLE_RESULT.json", result)
    once = {"activity_kind": "CURRENT_APPLICATION_BUNDLE", "nonce": "r9-test-nonce"}
    once_record = helper.publish(root / "UNIT_ONCE_GUARD.json", once)
    review = {
        "status": "PASS_W26_APPLICATION_BUNDLE_SOURCE_FOR_EXACT_CANDIDATE",
        "candidate_sha256": "f" * 64,
    }
    review_record = helper.publish(root / "MAIN_SOURCE_REVIEW.json", review)
    candidate = {
        "status": "READY_APPLICATION_BUNDLE_MAIN_REVIEW_NOT_EXECUTION_APPROVED",
        "native_Popen": 0,
        "root_identity": root_identity,
        "prepared": prepared_record,
        "plan": plan_record,
    }
    candidate_record = helper.publish(root / "ACTOR_CANDIDATE_r9test.json", candidate)
    binding_record = helper.publish(root / "MAIN_ONCE_BINDING.json", {
        "candidate_filename": "ACTOR_CANDIDATE_r9test.json",
    })
    records = {
        "root_identity": root_identity,
        "result": (result, result_record),
        "candidate": (candidate, candidate_record),
        "once": (once, once_record),
        "binding": ({"candidate_filename": "ACTOR_CANDIDATE_r9test.json"}, binding_record),
        "review": (review, review_record),
    }
    monkeypatch.setattr(reader, "_producer_records", lambda _root, _base=reader.BASE: records)
    profile = {
        "schema": "w26-current-installed-application/1",
        "source_profile": "current175-r9",
        "root": str(root),
        "result": result_record,
        "candidate": candidate_record,
        "once": once_record,
        "loader_source_closure": {},
    }
    base = {
        "verify_current_application_loader_closure": lambda _closure: reader.R9_LOCK.parent,
        "_install_current_application_tools_namespace": lambda _path, _closure: (app, object()),
        "CURRENT_APPLICATION_R9_LOCK": reader.R9_LOCK,
    }
    return root, original, captured, profile, records, base, helper


def test_r9_reader_loader_pins_exact_reader_and_lock_without_changing_r7_r8():
    names = reader.BASE["CURRENT_APPLICATION_LOADER_NAMES"]
    assert "w26_sealed_data_r9.py" in names
    assert reader.R9_LOCK.name in names
    assert "w26_application_sources_175_r7_final.lock.json" in names
    assert "w26_application_sources_175_r8_prepared.lock.json" in names
    lock_record = _helpers().raw_file(reader.R9_LOCK)[1]
    lock = json.loads(_helpers().raw_file(reader.R9_LOCK)[0])
    assert lock_record["sha256"] == EXPECTED_LOCK_SHA
    assert lock["profile"] == "current175-r9"
    assert reader._source_member_map_fingerprint(lock["members"]) == EXPECTED_MEMBER_FINGERPRINT
    sources = {str(reader.BASE["CURRENT_APPLICATION_TOOLS_ROOT"] / name):
               _helpers().raw_file(reader.BASE["CURRENT_APPLICATION_TOOLS_ROOT"] / name)[1]
               for name in names}
    closure = reader.BASE["current_application_loader_closure"](sources)
    assert set(closure["members"]) == names
    without_reader = {path: row for path, row in sources.items() if Path(path).name != "w26_sealed_data_r9.py"}
    with pytest.raises(RuntimeError, match="membership differs"):
        reader.BASE["current_application_loader_closure"](without_reader)


def test_r9_reader_accepts_mutated_original_repo_after_capture_and_emits_snapshot_receipt(tmp_path, monkeypatch):
    root, original, captured, profile, records, base, _helper = _artifact_fixture(tmp_path, monkeypatch)
    changed = original / "comsol_mcp/data/science_recipes/SOURCE_ORIGINS.json"
    changed.write_bytes(changed.read_bytes() + b"\n# ordinary post-build edit\n")
    # The pre-dispatch path still audits the reviewed live origin; later artifact reads
    # use the two immutable root-local snapshots and do not open that mutable path.
    with pytest.raises(RuntimeError):
        app.verify_source_input_r9(captured, require_live_origin=True)
    result = reader.current_application_profile(profile, base)
    receipt = result["source_provenance"]
    assert receipt["schema"] == "w26-source-provenance-snapshot-binding/1"
    assert receipt["source_profile"] == "current175-r9"
    assert receipt["source_lock_sha256"] == EXPECTED_LOCK_SHA
    assert receipt["member_fingerprint_sha256"] == EXPECTED_MEMBER_FINGERPRINT
    assert receipt["source_snapshot_path"] == str(root / "source-input")
    assert receipt["origin_snapshot_path"] == str(root / "source-origin")
    assert receipt["source_snapshot_member_count"] == receipt["origin_snapshot_member_count"] == 175
    assert receipt["source_snapshot_fingerprint_sha256"] == receipt["origin_snapshot_fingerprint_sha256"] == EXPECTED_MEMBER_FINGERPRINT
    assert receipt["source_snapshot_record_path"] == str(root / "APP_INPUT_BINDING_r9test.json")
    assert receipt["source_snapshot_record_sha256"] == records["candidate"][0]["prepared"]["sha256"]


@pytest.mark.parametrize("target", ["source-input", "source-origin"])
def test_r9_reader_rejects_tampered_frozen_snapshot_after_original_tree_changes(tmp_path, monkeypatch, target):
    root, original, _captured, profile, _records, base, _helper = _artifact_fixture(tmp_path, monkeypatch)
    (original / "comsol_mcp/data/science_recipes/SOURCE_ORIGINS.json").write_bytes(b"post-build original change")
    tampered = root / target / "comsol_mcp/data/science_recipes/SOURCE_ORIGINS.json"
    tampered.write_bytes(tampered.read_bytes() + b"tamper")
    with pytest.raises(RuntimeError):
        reader.current_application_profile(profile, base)


def test_r9_reader_rejects_post_build_wheel_or_plan_tampering(tmp_path, monkeypatch):
    root, original, _captured, profile, _records, base, _helper = _artifact_fixture(tmp_path, monkeypatch)
    (original / "comsol_mcp/data/science_recipes/SOURCE_ORIGINS.json").write_bytes(b"post-build original change")
    wheel = root / "appwheel/comsol_mcp-0.1.10-py3-none-any.whl"
    wheel.write_bytes(wheel.read_bytes() + b"tamper")
    with pytest.raises(RuntimeError, match="wheel identity"):
        reader.current_application_profile(profile, base)

    root2, original2, _captured2, profile2, records2, base2, helper2 = _artifact_fixture(tmp_path / "plan-case", monkeypatch)
    (original2 / "comsol_mcp/data/science_recipes/SOURCE_ORIGINS.json").write_bytes(b"post-build original change")
    candidate = records2["candidate"][0]
    plan_path = Path(candidate["plan"]["path"])
    plan_path.write_bytes(plan_path.read_bytes() + b"tamper")
    with pytest.raises(RuntimeError):
        reader.current_application_profile(profile2, base2)


def test_r9_namespace_loader_imports_exact_closed_source_without_reusing_cached_modules(monkeypatch):
    helper = _helpers()
    tools_root = reader.BASE["CURRENT_APPLICATION_TOOLS_ROOT"]
    names = reader.BASE["CURRENT_APPLICATION_LOADER_NAMES"]
    source_records = {str(tools_root / name): helper.raw_file(tools_root / name)[1] for name in names}
    closure = reader.BASE["current_application_loader_closure"](source_records)
    assert reader.BASE["verify_current_application_loader_closure"](closure) == tools_root
    assert reader.BASE_NAMESPACE is reader.BASE["run_phase"].__globals__
    assert reader.BASE_NAMESPACE is not reader.BASE
    assert reader.BASE_NAMESPACE["CURRENT_APPLICATION_LOADER_NAMES"] == names
    assert reader.BASE_NAMESPACE["CURRENT_APPLICATION_LOADER_MODULE_PATHS"] == reader.BASE["CURRENT_APPLICATION_LOADER_MODULE_PATHS"]

    previous = {name: module for name, module in sys.modules.items()
                if name == "tools" or name.startswith("tools.")}
    for name in previous:
        del sys.modules[name]
    try:
        app_module, actor_module = reader.BASE["_install_current_application_tools_namespace"](tools_root, closure)
        assert Path(app_module.__file__).resolve() == tools_root / "w26_application_bundle.py"
        assert Path(actor_module.__file__).resolve() == tools_root / "w26_runtime_actor.py"
        reader.BASE["_verify_current_application_cached_modules"](tools_root, closure)
        package_paths = {str(Path(item).resolve()) for item in sys.modules["tools"].__path__}
        assert package_paths == {str(tools_root)}
    finally:
        for name in [key for key in sys.modules if key == "tools" or key.startswith("tools.")]:
            del sys.modules[name]
        sys.modules.update(previous)


def test_r9_reader_rejects_r7_r8_profile_lock_mix(tmp_path, monkeypatch):
    root, _original, _captured, profile, _records, base, _helper = _artifact_fixture(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="profile selector"):
        reader.current_application_profile({**profile, "source_profile": "current175-r7"}, base)
    for wrong_lock in (reader.R7_LOCK, reader.R8_LOCK):
        mixed = {**base, "CURRENT_APPLICATION_R9_LOCK": wrong_lock}
        with pytest.raises(RuntimeError, match="exact reviewed source lock binding differs"):
            reader.current_application_profile(profile, mixed)


def test_r9_reader_opens_metadata_record_and_payload_from_one_verified_wheel_read(tmp_path, monkeypatch):
    root, _original, _captured, profile, _records, base, helper = _artifact_fixture(tmp_path, monkeypatch)
    wheel = root / "appwheel" / "comsol_mcp-0.1.10-py3-none-any.whl"
    original_raw_file = helper.raw_file
    wheel_reads = []
    def count_wheel_reads(path, *args, **kwargs):
        if Path(path) == wheel:
            wheel_reads.append(Path(path))
        return original_raw_file(path, *args, **kwargs)
    monkeypatch.setattr(helper, "raw_file", count_wheel_reads)

    original_zipfile = zipfile.ZipFile
    zip_inputs = []
    def record_zip_input(source, *args, **kwargs):
        if isinstance(source, (str, Path)) and Path(source) == wheel:
            raise AssertionError("wheel ZIP reopened through its mutable path")
        if isinstance(source, io.BytesIO):
            zip_inputs.append(source.getvalue())
        return original_zipfile(source, *args, **kwargs)
    monkeypatch.setattr(zipfile, "ZipFile", record_zip_input)

    result = reader.current_application_profile(profile, base)
    raw = original_raw_file(wheel)[0]
    assert result["wheel"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert wheel_reads == [wheel]
    assert len(zip_inputs) >= 2
    assert all(value == raw for value in zip_inputs)
