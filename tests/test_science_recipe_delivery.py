"""Fail-closed structural coverage checks for T060 science-delivery manifests."""

import hashlib
import importlib.util
import json
import pathlib
import sys

import pytest


TOOL = pathlib.Path(__file__).resolve().parents[1] / "tools" / "science_recipe_delivery.py"
spec = importlib.util.spec_from_file_location("science_recipe_delivery", TOOL)
delivery = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = delivery
spec.loader.exec_module(delivery)


def _ref(path: pathlib.Path) -> dict[str, str]:
    return {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _matrix() -> dict:
    target_ids = [
        "windows-x64/comsol6.3", "windows-x64/comsol6.4",
        "macos-arm64/comsol6.3", "macos-arm64/comsol6.4",
        "macos-x86_64/comsol6.3", "macos-x86_64/comsol6.4",
    ]
    targets = {target: {"status": "NOT_RUN"} for target in target_ids}
    for target in ("macos-arm64/comsol6.3", "macos-x86_64/comsol6.3"):
        targets[target].update({
            "effective_scope_disposition": "USER_REQUESTED_SKIP",
            "scope_waiver_id": "USER_SCOPE_WAIVER_MACOS_COMSOL63_20260926",
            "scope_waiver_basis": "跳过mac 6.3验证",
            "original_required_scope_preserved": True,
            "compatibility_claim": "NOT_VERIFIED",
        })
    return {"tests": [{"id": "T060", "targets": targets}]}


def _valid_fixture(tmp_path: pathlib.Path) -> tuple[dict, dict]:
    cases = []
    for case_id, domain in delivery.CASE_DOMAINS.items():
        artifacts = {}
        for category in delivery.REQUIRED_CATEGORIES:
            suffix = {
                "mph": ".mph", "dependency_hashes": ".json", "recipe": ".json",
                "raw_numerical": ".csv", "plots": ".png", "logs": ".log",
                "version": ".json", "validation": ".json",
            }[category]
            name = f"{case_id}-{category}{suffix}"
            payload = b"opaque fixture\x00" if category in {"mph", "plots"} else b"fixture payload\n"
            (tmp_path / name).write_bytes(payload)
            artifacts[category] = [_ref(tmp_path / name)]
        cases.append({"case_id": case_id, "domain": domain, "artifacts": artifacts})

    target_replays = []
    for target_id, baseline in _matrix()["tests"][0]["targets"].items():
        if baseline.get("effective_scope_disposition") == "USER_REQUESTED_SKIP":
            target_replays.append({
                "target_id": target_id,
                "status": "USER_REQUESTED_SKIP",
                "waiver_id": baseline["scope_waiver_id"],
                "waiver_basis": baseline["scope_waiver_basis"],
                "original_required_scope_preserved": True,
                "compatibility_claim": "NOT_VERIFIED",
            })
        else:
            log = tmp_path / f"replay-{target_id.replace('/', '-')}.log"
            log.write_text("clean project replay fixture\n", encoding="utf-8")
            target_replays.append({
                "target_id": target_id,
                "status": "PASS",
                "clean_project_directory": True,
                "engine_version": target_id.rsplit("/", 1)[-1].replace("comsol", "COMSOL ") + " fixture only",
                "log": _ref(log),
            })
    return {"schema": delivery.SCHEMA, "cases": cases, "target_replays": target_replays}, _matrix()


def test_complete_file_closure_never_promotes_t060_or_replay_claims(tmp_path):
    manifest, matrix = _valid_fixture(tmp_path)
    report = delivery.audit_bundle(tmp_path, manifest, matrix)
    assert report["artifact_hash_closure"] == "PASS"
    assert report["package_status"] == "ARTIFACT_HASH_CLOSURE_COMPLETE_PENDING_INDEPENDENT_REVIEW"
    assert report["target_replay_claim_status"] == "ALL_APPLICABLE_REPLAY_CLAIMS_PRESENT"
    assert report["t060_acceptance"] == "NOT_RUN"
    assert report["embedded_path_scan"]["status"] == "PARTIAL_TEXT_ONLY_OPAQUE_FILES_UNSCANNED"
    assert report["target_replays"]["windows-x64/comsol6.4"]["claim_review"] == "UNREVIEWED"
    assert report["target_replays"]["macos-arm64/comsol6.3"]["compatibility_claim"] == "NOT_VERIFIED"
    assert report["package_file_set"]["status"] == "PASS"


def test_hash_complete_bundle_with_unrun_target_stays_replay_incomplete(tmp_path):
    manifest, matrix = _valid_fixture(tmp_path)
    target = next(r for r in manifest["target_replays"] if r["target_id"] == "windows-x64/comsol6.4")
    (tmp_path / target["log"]["path"]).unlink()
    target.pop("log")
    target["status"] = "NOT_RUN"
    report = delivery.audit_bundle(tmp_path, manifest, matrix)
    assert report["artifact_hash_closure"] == "PASS"
    assert report["target_replay_claim_status"] == "NOT_RUN_OR_INCOMPLETE"
    assert report["package_status"] == "ARTIFACT_HASH_CLOSURE_COMPLETE_TARGET_REPLAY_INCOMPLETE"
    assert report["t060_acceptance"] == "NOT_RUN"


def test_unlisted_package_file_blocks_exact_member_closure(tmp_path):
    manifest, matrix = _valid_fixture(tmp_path)
    (tmp_path / "unlisted.txt").write_text("hidden member\n", encoding="utf-8")
    report = delivery.audit_bundle(tmp_path, manifest, matrix)
    assert report["package_file_set"]["status"] == "INCOMPLETE"
    assert report["package_file_set"]["unlisted_files"] == ["unlisted.txt"]
    assert report["artifact_hash_closure"] == "INCOMPLETE"


def test_manifest_is_the_only_excluded_nonartifact_member(tmp_path):
    manifest, matrix = _valid_fixture(tmp_path)
    manifest_path = tmp_path / "science-delivery.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    report = delivery.audit_bundle(tmp_path, manifest, matrix, manifest_file=manifest_path)
    assert report["package_file_set"]["status"] == "PASS"
    assert report["package_file_set"]["manifest_file_excluded_from_member_set"] == "science-delivery.json"


def test_unlisted_symlink_blocks_package_member_set(tmp_path):
    manifest, matrix = _valid_fixture(tmp_path)
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    (tmp_path / "surprise-link.txt").symlink_to(outside)
    report = delivery.audit_bundle(tmp_path, manifest, matrix)
    assert report["package_file_set"]["status"] == "INCOMPLETE"
    assert report["package_file_set"]["symlinks"] == ["surprise-link.txt"]


def test_missing_raw_values_is_an_explicit_incomplete_category(tmp_path):
    manifest, matrix = _valid_fixture(tmp_path)
    w23 = next(case for case in manifest["cases"] if case["case_id"] == "W23")
    del w23["artifacts"]["raw_numerical"]
    report = delivery.audit_bundle(tmp_path, manifest, matrix)
    w23_report = next(case for case in report["cases"] if case["case_id"] == "W23")
    assert w23_report["categories"]["raw_numerical"]["status"] == "MISSING"
    assert report["artifact_hash_closure"] == "INCOMPLETE"
    assert report["t060_acceptance"] == "NOT_RUN"


def test_unrecognized_category_cannot_hide_undeclared_evidence(tmp_path):
    manifest, matrix = _valid_fixture(tmp_path)
    w22 = next(case for case in manifest["cases"] if case["case_id"] == "W22")
    extra = tmp_path / "unreviewed.txt"
    extra.write_text("unreviewed\n", encoding="utf-8")
    w22["artifacts"]["extra"] = [_ref(extra)]
    report = delivery.audit_bundle(tmp_path, manifest, matrix)
    w22_report = next(case for case in report["cases"] if case["case_id"] == "W22")
    assert w22_report["status"] == "INVALID"
    assert "unexpected artifact categories" in w22_report["error"]
    assert report["artifact_hash_closure"] == "INCOMPLETE"


def test_hash_mismatch_and_absolute_path_in_text_are_not_silently_passed(tmp_path):
    manifest, matrix = _valid_fixture(tmp_path)
    w22 = next(case for case in manifest["cases"] if case["case_id"] == "W22")
    item = w22["artifacts"]["logs"][0]
    (tmp_path / item["path"]).write_text("log at C:\\Temp\\model.log\n", encoding="utf-8")
    report = delivery.audit_bundle(tmp_path, manifest, matrix)
    w22_report = next(case for case in report["cases"] if case["case_id"] == "W22")
    log = w22_report["categories"]["logs"]["files"][0]
    assert log["status"] == "HASH_MISMATCH"
    assert log["text_path_scan"] == "KNOWN_ABSOLUTE_PATH_FOUND"
    assert report["package_status"] == "KNOWN_ABSOLUTE_PATHS_FOUND"
    assert report["t060_acceptance"] == "NOT_RUN"


def test_absolute_path_in_manifest_metadata_is_reported(tmp_path):
    manifest, matrix = _valid_fixture(tmp_path)
    manifest["run_root"] = "/Volumes/SSD/private-run"
    report = delivery.audit_bundle(tmp_path, manifest, matrix)
    assert report["embedded_path_scan"]["status"] == "KNOWN_ABSOLUTE_PATHS_FOUND"
    assert report["embedded_path_scan"]["manifest_fields_with_known_absolute_paths"] == ["$/run_root"]
    assert report["package_status"] == "KNOWN_ABSOLUTE_PATHS_FOUND"


def test_replay_log_absolute_path_is_included_in_aggregate_scan(tmp_path):
    manifest, matrix = _valid_fixture(tmp_path)
    target = next(r for r in manifest["target_replays"] if r["target_id"] == "windows-x64/comsol6.4")
    log_path = tmp_path / target["log"]["path"]
    log_path.write_text("replay saved to C:\\Temp\\w24\\result.mph\n", encoding="utf-8")
    target["log"] = _ref(log_path)
    report = delivery.audit_bundle(tmp_path, manifest, matrix)
    target_report = report["target_replays"]["windows-x64/comsol6.4"]
    assert target_report["log"]["text_path_scan"] == "KNOWN_ABSOLUTE_PATH_FOUND"
    assert report["embedded_path_scan"]["status"] == "KNOWN_ABSOLUTE_PATHS_FOUND"
    assert report["embedded_path_scan"]["findings"] == [target["log"]["path"]]
    assert report["package_status"] == "KNOWN_ABSOLUTE_PATHS_FOUND"


@pytest.mark.parametrize("bad_path", ["../escape.txt", "/absolute/file", "C:\\Temp\\file", "a//b.txt", "a/./b.txt"])
def test_nonportable_or_traversing_paths_are_rejected(tmp_path, bad_path):
    manifest, matrix = _valid_fixture(tmp_path)
    w22 = next(case for case in manifest["cases"] if case["case_id"] == "W22")
    w22["artifacts"]["recipe"][0]["path"] = bad_path
    report = delivery.audit_bundle(tmp_path, manifest, matrix)
    w22_report = next(case for case in report["cases"] if case["case_id"] == "W22")
    assert w22_report["categories"]["recipe"]["status"] == "INVALID"
    assert report["artifact_hash_closure"] == "INCOMPLETE"


def test_symlink_payload_is_rejected_even_when_target_hash_matches(tmp_path):
    manifest, matrix = _valid_fixture(tmp_path)
    target = tmp_path / "real.json"
    target.write_text("payload\n", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    w22 = next(case for case in manifest["cases"] if case["case_id"] == "W22")
    w22["artifacts"]["validation"] = [{"path": link.name, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}]
    report = delivery.audit_bundle(tmp_path, manifest, matrix)
    w22_report = next(case for case in report["cases"] if case["case_id"] == "W22")
    assert w22_report["categories"]["validation"]["status"] == "INVALID"


def test_skip_claim_must_match_original_waiver_without_compatibility_claim(tmp_path):
    manifest, matrix = _valid_fixture(tmp_path)
    skip = next(r for r in manifest["target_replays"] if r["target_id"] == "macos-arm64/comsol6.3")
    skip["waiver_id"] = "invented"
    report = delivery.audit_bundle(tmp_path, manifest, matrix)
    assert report["target_replays"]["macos-arm64/comsol6.3"]["status"] == "INVALID_SKIP_CLAIM"
    assert report["target_replay_claim_status"] == "NOT_RUN_OR_INCOMPLETE"


def test_blocked_license_is_recorded_but_never_counts_as_replay_completion(tmp_path):
    manifest, matrix = _valid_fixture(tmp_path)
    target = next(r for r in manifest["target_replays"] if r["target_id"] == "windows-x64/comsol6.4")
    target.update({"status": "BLOCKED_LICENSE", "missing_modules": ["Heat Transfer Module"]})
    report = delivery.audit_bundle(tmp_path, manifest, matrix)
    assert report["target_replays"]["windows-x64/comsol6.4"]["status"] == "BLOCKED_LICENSE"
    assert report["target_replay_claim_status"] == "NOT_RUN_OR_INCOMPLETE"


def test_replay_claim_for_wrong_comsol_major_minor_is_rejected(tmp_path):
    manifest, matrix = _valid_fixture(tmp_path)
    target = next(r for r in manifest["target_replays"] if r["target_id"] == "windows-x64/comsol6.4")
    target["engine_version"] = "COMSOL Multiphysics 6.3.0 fixture"
    report = delivery.audit_bundle(tmp_path, manifest, matrix)
    assert report["target_replays"]["windows-x64/comsol6.4"]["status"] == "INVALID_PASS_CLAIM"
    assert report["target_replay_claim_status"] == "NOT_RUN_OR_INCOMPLETE"


@pytest.mark.parametrize(
    "payload",
    [
        '{"schema":"first","schema":"second"}',
        '{"outer":{"status":"first","status":"second"}}',
        '{"value":NaN}',
        '{"value":Infinity}',
        '{"value":-Infinity}',
        '{"value":1e999}',
    ],
)
def test_cli_json_reader_rejects_duplicate_keys_and_nonfinite_numbers(tmp_path, payload):
    source = tmp_path / "invalid.json"
    source.write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError):
        delivery._json_read(source)
