from __future__ import annotations

from email.parser import BytesParser
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import zipfile

import pytest

from comsol_mcp._g2_artifact_verify import (
    FORMAT_SCHEMA,
    _MarkerUnsupported,
    _json_object,
    _marker_matches,
    _satisfies_python_target,
    _version_compare,
)
from comsol_mcp._g2_registry import registry_describe
from test_artifact_registration_import import (
    _error_code,
    _make_daemon,
    _read_action,
    _register,
    _store_rows_for_read_assertion,
)


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _wheel_bytes(spec: dict) -> tuple[str, bytes, dict]:
    name = spec["name"]
    version = spec.get("version", "1.0.0")
    tag = spec.get("tag", "py3-none-any")
    filename = spec.get("filename", f"{name.replace('-', '_')}-{version}-{tag}.whl")
    dist_info = f"{name.replace('-', '_')}-{version}.dist-info"
    license_payload = spec.get("license_payload", b"MIT license fixture\n")
    license_path = f"{dist_info}/licenses/LICENSE"
    metadata = [
        "Metadata-Version: 2.1",
        f"Name: {name}",
        f"Version: {version}",
        f"License: {spec.get('license', 'MIT')}",
        "Classifier: License :: OSI Approved :: MIT License",
    ]
    if spec.get("requires_python"):
        metadata.append(f"Requires-Python: {spec['requires_python']}")
    for requirement in spec.get("requires_dist", []):
        metadata.append(f"Requires-Dist: {requirement}")
    for extra in spec.get("provides_extra", []):
        metadata.append(f"Provides-Extra: {extra}")
    wheel_tags = spec.get("internal_tags", [tag])
    wheel_text = ["Wheel-Version: 1.0", "Root-Is-Purelib: true"]
    wheel_text.extend(f"Tag: {value}" for value in wheel_tags)
    members = {
        f"{dist_info}/METADATA": ("\n".join(metadata) + "\n\n").encode(),
        f"{dist_info}/WHEEL": ("\n".join(wheel_text) + "\n").encode(),
        license_path: license_payload,
        f"{name.replace('-', '_')}/payload.py": b"# Data only; never imported by the verifier.\n",
    }
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, payload in members.items():
            archive.writestr(path, payload)
    payload = output.getvalue()
    metadata_message = BytesParser().parsebytes(members[f"{dist_info}/METADATA"])
    info = {
        "file": filename,
        "name": name,
        "version": version,
        "bytes": len(payload),
        "sha256": _hash(payload),
        "wheel_tags": sorted(wheel_tags),
        "license": str(metadata_message.get("License", "UNKNOWN")),
        "license_classifiers": [
            str(value) for value in metadata_message.get_all("Classifier", [])
            if str(value).strip().lower().startswith("license ::")
        ],
        "license_files": [{"path": license_path, "bytes": len(license_payload), "sha256": _hash(license_payload)}],
        "requires_python": spec.get("requires_python"),
        "requires_dist": spec.get("requires_dist", []),
        "provides_extra": spec.get("provides_extra", []),
    }
    return filename, payload, info


def _bundle_bytes(
    wheel_specs: list[dict] | None = None,
    *,
    target: str = "macos_arm64",
    package_target: str | None = None,
    requirements: str | None = None,
    bundle_schema: str = FORMAT_SCHEMA,
    bundle_kind: str = "OFFLINE_INSTALL_BUNDLE",
    include_self_in_manifest: bool = False,
    corrupt_member: str | None = None,
    traversal_member: bool = False,
    symlink_member: bool = False,
    wrong_sbom_wheel_hash: bool = False,
) -> bytes:
    wheel_specs = wheel_specs or [{"name": "demo-pkg"}]
    files: dict[str, bytes] = {"tools/release-check.txt": b"bounded static fixture\n", "locks/uv.lock": b"version = 1\n"}
    wheel_records = []
    component_rows = []
    license_rows = []
    requirement_lines = []
    for spec in wheel_specs:
        filename, wheel_payload, info = _wheel_bytes(spec)
        files["wheelhouse/" + filename] = wheel_payload
        wheel_records.append({key: info[key] for key in (
            "file", "name", "version", "bytes", "sha256", "wheel_tags", "license",
            "license_classifiers", "license_files",
        )})
        digest = "0" * 64 if wrong_sbom_wheel_hash else info["sha256"]
        component_rows.append({
            "type": "library", "name": info["name"], "version": info["version"],
            "hashes": [{"alg": "SHA-256", "content": digest}],
            "properties": [{"name": "wheel.tags", "value": ",".join(info["wheel_tags"])}],
        })
        license_rows.append({key: info[key] for key in (
            "name", "version", "license", "license_classifiers", "license_files",
        )})
        requirement_lines.append(
            f"{info['name']}=={info['version']} --hash=sha256:{info['sha256']}"
        )
    requirement_payload = (requirements or "\n".join(requirement_lines) + "\n").encode()
    files["locks/requirements.lock"] = requirement_payload
    sbom_payload = json.dumps({
        "bomFormat": "CycloneDX", "specVersion": "1.5", "version": 1,
        "components": component_rows,
    }, sort_keys=True).encode()
    license_payload = json.dumps({
        "schema": FORMAT_SCHEMA, "target": target, "components": license_rows,
    }, sort_keys=True).encode()
    files["SBOM.cdx.json"] = sbom_payload
    files["LICENSES.json"] = license_payload
    package = {
        "schema": FORMAT_SCHEMA, "target": package_target or target, "python": "3.12",
        "wheel_count": len(wheel_records), "wheels": wheel_records,
        "requirements": {"filename": "requirements.lock", "sha256": _hash(requirement_payload)},
        "source_lock": {"filename": "uv.lock", "sha256": _hash(files["locks/uv.lock"])},
        "evidence_files": {
            "sbom": {"filename": "SBOM.cdx.json", "sha256": _hash(sbom_payload)},
            "licenses": {"filename": "LICENSES.json", "sha256": _hash(license_payload)},
        },
        "derived_wheel": None,
    }
    files["PACKAGE_MANIFEST.json"] = json.dumps(package, sort_keys=True).encode()
    rows = [{"path": name, "bytes": len(payload), "sha256": _hash(payload)}
            for name, payload in sorted(files.items())]
    if include_self_in_manifest:
        rows.append({"path": "OFFLINE_BUNDLE_MANIFEST.json", "bytes": 1, "sha256": "0" * 64})
    manifest = {
        "schema": bundle_schema, "kind": bundle_kind, "target": target, "python": "3.12",
        "source_lock": {"filename": "uv.lock", "sha256": _hash(files["locks/uv.lock"])},
        "source_lock_included": "locks/uv.lock", "source_lock_sha256": _hash(files["locks/uv.lock"]),
        "requirements": {"filename": "requirements.lock", "sha256": _hash(requirement_payload)},
        "requirements_included": "locks/requirements.lock", "requirements_sha256": _hash(requirement_payload),
        "files_excluding_this_manifest": rows,
    }
    files["OFFLINE_BUNDLE_MANIFEST.json"] = json.dumps(manifest, sort_keys=True).encode()
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, original in sorted(files.items()):
            payload = original
            if corrupt_member == name:
                payload = original + b"post-manifest corruption\n"
            archive.writestr(name, payload)
        if traversal_member:
            archive.writestr("../outside.txt", b"not in the declared member list")
        if symlink_member:
            info = zipfile.ZipInfo("untrusted-link")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, b"../target")
    return output.getvalue()


def _registered_response(daemon, project_root: Path, payload: bytes, *, name="bundle.zip", request_key="verify-register"):
    source = project_root / "inputs" / name
    source.write_bytes(payload)
    registered = _register(daemon, path=f"inputs/{name}", key=request_key, request_id=f"{request_key}-request")
    assert registered["success"] is True
    return registered["data"]["artifact_id"]


def _verify(daemon, project_id: str, artifact_id: str, *, entrypoint="direct", request_id=None):
    return _read_action(
        daemon, "artifact.verify", project_id, {"artifact_id": artifact_id},
        entrypoint=entrypoint, request_id=request_id,
    )


def test_artifact_verify_dispatches_real_registered_bundle_and_leaves_store_rows_unchanged(tmp_path):
    from jsonschema import validate

    daemon, project_root, _ = _make_daemon(tmp_path)
    try:
        artifact_id = _registered_response(daemon, project_root, _bundle_bytes())
        before = _store_rows_for_read_assertion(daemon.store)
        for entrypoint in ("direct", "registry_call", "operation_call"):
            response = _verify(
                daemon, daemon._test_project_id, artifact_id,
                entrypoint=entrypoint, request_id="verify-dispatch-read-only",
            )
            assert response["success"] is True
            data = response["data"]
            validate(instance=data, schema=registry_describe("artifact.verify")["data_schema"])
            assert data["format_verdict"] == "VERIFIED_DECLARED_PACKAGE_CONTENT"
            assert data["member_integrity"] == "PASS"
            assert data["dependency_closure"] == "PASS_DECLARED_TARGET"
            assert data["target_format"] == "PASS_STATIC_TAGS"
            assert data["artifact_id"] == artifact_id
            assert data["request_id"] == "verify-dispatch-read-only"
            assert data["producer_trust"] == "UNVERIFIED_UNLESS_EXISTING_AUTHORITATIVE_PRODUCER_PROOF"
            assert data["native_compatibility"] == "NOT_RUN"
        assert _store_rows_for_read_assertion(daemon.store) == before
    finally:
        daemon.close()


@pytest.mark.parametrize("entrypoint", ["direct", "registry_call", "operation_call"])
def test_artifact_verify_returns_typed_member_dependency_and_target_findings(tmp_path, entrypoint):
    daemon, project_root, _ = _make_daemon(tmp_path)
    payload = _bundle_bytes(corrupt_member="tools/release-check.txt")
    try:
        artifact_id = _registered_response(daemon, project_root, payload)
        response = _verify(daemon, daemon._test_project_id, artifact_id, entrypoint=entrypoint)
        assert response["success"] is True
        data = response["data"]
        assert data["format_verdict"] == "INVALID"
        assert data["member_integrity"] == "FAIL"
        assert any(row["code"] == "MEMBER_HASH_MISMATCH" for row in data["findings"])
        assert str(project_root) not in json.dumps(data, sort_keys=True)
    finally:
        daemon.close()


def test_artifact_verify_rejects_manifest_self_inclusion_traversal_and_symlinks(tmp_path):
    cases = [
        (_bundle_bytes(include_self_in_manifest=True), "INVALID"),
        (_bundle_bytes(traversal_member=True), "INVALID"),
        (_bundle_bytes(symlink_member=True), "INVALID"),
    ]
    for index, (payload, verdict) in enumerate(cases):
        daemon, project_root, _ = _make_daemon(tmp_path / str(index))
        try:
            artifact_id = _registered_response(daemon, project_root, payload, request_key=f"unsafe-{index}")
            response = _verify(daemon, daemon._test_project_id, artifact_id)
            assert response["success"] is True
            assert response["data"]["format_verdict"] == verdict
            assert response["data"]["member_integrity"] == "FAIL"
        finally:
            daemon.close()


def test_artifact_verify_unknown_format_is_unsupported_and_does_not_claim_unchecked_categories(tmp_path):
    daemon, project_root, _ = _make_daemon(tmp_path)
    try:
        artifact_id = _registered_response(
            daemon, project_root, _bundle_bytes(bundle_schema="comsol-mcp-full-release/99"),
        )
        response = _verify(daemon, daemon._test_project_id, artifact_id)
        assert response["success"] is True
        data = response["data"]
        assert data["format_verdict"] == "UNSUPPORTED"
        assert data["member_integrity"] == "INCOMPLETE"
        assert data["dependency_closure"] == "INCOMPLETE"
        assert data["target_format"] == "INCOMPLETE"
        assert data["format_schema"] is None
    finally:
        daemon.close()


def test_artifact_verify_evaluates_target_markers_and_reports_missing_active_dependency(tmp_path):
    inactive = _bundle_bytes(wheel_specs=[{
        "name": "demo-pkg", "requires_dist": ["win-helper>=1; sys_platform == 'win32'"],
    }])
    active = _bundle_bytes(
        wheel_specs=[{"name": "demo-pkg", "requires_dist": ["win-helper>=1; sys_platform == 'win32'"]}],
        target="win_amd64",
    )
    for index, (payload, expected) in enumerate(((inactive, "VERIFIED_DECLARED_PACKAGE_CONTENT"), (active, "INVALID"))):
        daemon, project_root, _ = _make_daemon(tmp_path / str(index))
        try:
            artifact_id = _registered_response(daemon, project_root, payload, request_key=f"marker-{index}")
            data = _verify(daemon, daemon._test_project_id, artifact_id)["data"]
            assert data["format_verdict"] == expected
            if expected == "INVALID":
                assert data["dependency_closure"] == "FAIL"
                assert any(row["code"] == "MISSING_DECLARED_DEPENDENCY" for row in data["findings"])
        finally:
            daemon.close()


def test_artifact_verify_marks_unknown_patch_sensitive_python_semantics_incomplete():
    assert _marker_matches("python_full_version < '3.11'", target="macos_arm64", python="3.12") is False
    with pytest.raises(_MarkerUnsupported):
        _marker_matches("python_full_version >= '3.12.1'", target="macos_arm64", python="3.12")
    assert _satisfies_python_target(">=3.12", "3.12") is True
    with pytest.raises(_MarkerUnsupported):
        _satisfies_python_target(">=3.12.1", "3.12")


def test_artifact_verify_patch_sensitive_marker_is_incomplete_through_real_dispatch(tmp_path):
    daemon, project_root, _ = _make_daemon(tmp_path)
    try:
        payload = _bundle_bytes(wheel_specs=[{
            "name": "demo-pkg", "requires_dist": ["missing>=1; python_full_version >= '3.12.1'"],
        }])
        artifact_id = _registered_response(daemon, project_root, payload)
        data = _verify(daemon, daemon._test_project_id, artifact_id)["data"]
        assert data["format_verdict"] == "INCOMPLETE"
        assert data["dependency_closure"] == "INCOMPLETE"
        assert any(row["code"] == "UNSUPPORTED_MARKER_OR_REQUIREMENT" for row in data["findings"])
    finally:
        daemon.close()


def test_artifact_verify_applies_requirements_lock_target_markers_and_detects_missing_wheel(tmp_path):
    demo_filename, demo_payload, demo_info = _wheel_bytes({"name": "demo-pkg"})
    requirement = (
        f"demo-pkg==1.0.0 --hash=sha256:{demo_info['sha256']}\n"
        + "win-helper==1.0.0 ; sys_platform == 'win32' --hash=sha256:" + "a" * 64 + "\n"
    )
    payload = _bundle_bytes(requirements=requirement)
    for index, target in enumerate(("macos_arm64", "win_amd64")):
        daemon, project_root, _ = _make_daemon(tmp_path / str(index))
        try:
            if target == "win_amd64":
                payload = _bundle_bytes(target=target, requirements=requirement)
            else:
                payload = _bundle_bytes(target=target, requirements=requirement)
            artifact_id = _registered_response(daemon, project_root, payload, request_key=f"lock-marker-{index}")
            data = _verify(daemon, daemon._test_project_id, artifact_id)["data"]
            if target == "macos_arm64":
                assert data["format_verdict"] == "VERIFIED_DECLARED_PACKAGE_CONTENT"
            else:
                assert data["format_verdict"] == "INVALID"
                assert any(row["code"] == "MISSING_REQUIRED_WHEEL" for row in data["findings"])
        finally:
            daemon.close()


def test_artifact_verify_activates_only_requested_extra_dependencies(tmp_path):
    wheel_spec = {
        "name": "demo-pkg", "provides_extra": ["security"],
        "requires_dist": ["missing-secure>=1; extra == 'security'"],
    }
    _filename, _payload, info = _wheel_bytes(wheel_spec)
    base_pin = f"demo-pkg=={info['version']} --hash=sha256:{info['sha256']}\n"
    extra_pin = f"demo-pkg[security]=={info['version']} --hash=sha256:{info['sha256']}\n"
    cases = ((base_pin, "VERIFIED_DECLARED_PACKAGE_CONTENT"), (extra_pin, "INVALID"))
    for index, (requirements, expected) in enumerate(cases):
        daemon, project_root, _ = _make_daemon(tmp_path / str(index))
        try:
            payload = _bundle_bytes([wheel_spec], requirements=requirements)
            artifact_id = _registered_response(daemon, project_root, payload, request_key=f"extra-{index}")
            data = _verify(daemon, daemon._test_project_id, artifact_id)["data"]
            assert data["format_verdict"] == expected
            if expected == "INVALID":
                assert data["dependency_closure"] == "FAIL"
                assert any(row["code"] == "MISSING_DECLARED_DEPENDENCY" for row in data["findings"])
        finally:
            daemon.close()


def test_artifact_verify_checks_wheel_platform_tag_and_manifest_sbom_bindings(tmp_path):
    wrong_target = _bundle_bytes(wheel_specs=[{
        "name": "demo-pkg", "tag": "cp312-cp312-win_amd64",
    }])
    wrong_sbom = _bundle_bytes(wrong_sbom_wheel_hash=True)
    for index, payload in enumerate((wrong_target, wrong_sbom)):
        daemon, project_root, _ = _make_daemon(tmp_path / str(index))
        try:
            artifact_id = _registered_response(daemon, project_root, payload, request_key=f"wheel-check-{index}")
            data = _verify(daemon, daemon._test_project_id, artifact_id)["data"]
            assert data["format_verdict"] == "INVALID"
            if index == 0:
                assert any(row["code"] == "INCOMPATIBLE_WHEEL_TAG" for row in data["findings"])
            else:
                assert any(row["code"] == "SBOM_WHEEL_BINDING_MISMATCH" for row in data["findings"])
        finally:
            daemon.close()


def test_artifact_verify_detects_package_target_mismatch_and_unknown_requires_python_patch(tmp_path):
    cases = [
        (_bundle_bytes(package_target="win_amd64"), "INVALID", "PACKAGE_TARGET_BINDING_MISMATCH", "FAIL"),
        (_bundle_bytes(wheel_specs=[{"name": "demo-pkg", "requires_python": ">=3.12.1"}]),
         "INCOMPLETE", "UNSUPPORTED_REQUIRES_PYTHON", "INCOMPLETE"),
    ]
    for index, (payload, verdict, finding, target_status) in enumerate(cases):
        daemon, project_root, _ = _make_daemon(tmp_path / str(index))
        try:
            artifact_id = _registered_response(daemon, project_root, payload, request_key=f"target-contract-{index}")
            data = _verify(daemon, daemon._test_project_id, artifact_id)["data"]
            assert data["format_verdict"] == verdict
            assert data["target_format"] == target_status
            assert any(row["code"] == finding for row in data["findings"])
        finally:
            daemon.close()


def test_artifact_verify_budget_hit_is_incomplete_instead_of_a_false_pass(tmp_path, monkeypatch):
    import comsol_mcp._g2_artifact_verify as verifier

    daemon, project_root, _ = _make_daemon(tmp_path)
    try:
        artifact_id = _registered_response(daemon, project_root, _bundle_bytes())
        monkeypatch.setattr(verifier, "MAX_OUTER_MEMBER_BYTES", 8)
        data = _verify(daemon, daemon._test_project_id, artifact_id)["data"]
        assert data["format_verdict"] == "INCOMPLETE"
        assert data["member_integrity"] == "INCOMPLETE"
        assert data["dependency_closure"] == "INCOMPLETE"
        assert data["target_format"] == "INCOMPLETE"
    finally:
        daemon.close()


def test_artifact_verify_permission_is_checked_before_metadata_access(tmp_path, monkeypatch):
    daemon, project_root, _ = _make_daemon(tmp_path)
    try:
        artifact_id = _registered_response(daemon, project_root, _bundle_bytes())
        daemon.project_authority.permission_provider = lambda: set()
        monkeypatch.setattr(
            daemon.store, "get_project_artifact_metadata",
            lambda *_args, **_kwargs: pytest.fail("artifact metadata lookup occurred before permission denial"),
        )
        response = _verify(daemon, daemon._test_project_id, artifact_id)
        assert response["success"] is False
        assert _error_code(response) == "PERMISSION_DENIED"
    finally:
        daemon.close()


def test_artifact_verify_foreign_and_forged_hashes_are_non_disclosing(tmp_path):
    daemon, project_root, _ = _make_daemon(tmp_path)
    try:
        artifact_id = _registered_response(daemon, project_root, _bundle_bytes())
        other = daemon.dispatch({
            "operation": "project.create",
            "arguments": {"label": "other", "workspace": "other", "policy": {"permissions": ["inspect", "project_write"]}},
            "execution": {"request_id": "create-other", "idempotency_key": "create-other"},
        })["data"]["project"]
        (Path(other["workspace"]) / "inputs").mkdir(parents=True)
        foreign = _verify(daemon, other["project_id"], artifact_id)
        forged = _verify(daemon, other["project_id"], "a" * 64)
        assert foreign["success"] is False and forged["success"] is False
        assert _error_code(foreign) == _error_code(forged) == "ARTIFACT_NOT_FOUND"
        assert foreign["error"]["message"] == forged["error"]["message"]
    finally:
        daemon.close()


def test_artifact_verify_sanitizes_symlink_resolver_failures(tmp_path):
    daemon, project_root, _ = _make_daemon(tmp_path)
    try:
        artifact_id = _registered_response(daemon, project_root, _bundle_bytes())
        record = daemon.store.get_metadata("artifacts", artifact_id)
        registered_path = project_root / record["path"]
        registered_path.unlink()
        registered_path.symlink_to(project_root / "inputs" / "bundle.zip")
        response = _verify(daemon, daemon._test_project_id, artifact_id)
        assert response["success"] is False
        assert _error_code(response) == "ACCESS_VIOLATION"
        assert str(project_root) not in json.dumps(response, sort_keys=True)
    finally:
        daemon.close()


def test_artifact_verify_real_accepted_dev1_bundles_through_registration(tmp_path):
    from jsonschema import validate

    raw_paths = os.environ.get("COMSOL_ARTIFACT_VERIFY_BUNDLE_PATHS", "")
    if not raw_paths:
        pytest.skip("accepted dev1 archive paths were not supplied for this environment")
    paths = [Path(value) for value in raw_paths.split(os.pathsep) if value]
    expected = {
        "comsol-mcp-win_amd64-0.2.0.dev1.zip": "win_amd64",
        "comsol-mcp-macos_arm64-0.2.0.dev1.zip": "macos_arm64",
        "comsol-mcp-macos_x86_64-0.2.0.dev1.zip": "macos_x86_64",
    }
    assert {path.name for path in paths} == set(expected)
    daemon, project_root, _ = _make_daemon(tmp_path)
    try:
        for index, path in enumerate(sorted(paths, key=lambda item: item.name)):
            assert path.is_file()
            source_bytes = path.read_bytes()
            artifact_id = _registered_response(
                daemon, project_root, source_bytes, name=path.name, request_key=f"real-dev1-{index}",
            )
            before = _store_rows_for_read_assertion(daemon.store)
            response = _verify(daemon, daemon._test_project_id, artifact_id)
            assert response["success"] is True
            data = response["data"]
            validate(instance=data, schema=registry_describe("artifact.verify")["data_schema"])
            assert data["format_verdict"] == "VERIFIED_DECLARED_PACKAGE_CONTENT", data["findings"]
            assert data["member_integrity"] == "PASS", data["findings"]
            assert data["dependency_closure"] == "PASS_DECLARED_TARGET", data["findings"]
            assert data["target_format"] == "PASS_STATIC_TAGS", data["findings"]
            assert data["declared_target"] == expected[path.name]
            assert data["declared_python"] == "3.12"
            assert data["native_compatibility"] == "NOT_RUN"
            assert data["producer_trust"] == "UNVERIFIED_UNLESS_EXISTING_AUTHORITATIVE_PRODUCER_PROOF"
            if expected[path.name] == "macos_x86_64":
                assert [item["name"] for item in data["native_components"]] == ["OpenSSL"]
                assert data["native_components"][0]["status"] == "DECLARED_CONTENT_BINDING_ONLY"
            else:
                assert data["native_components"] == []
            assert _store_rows_for_read_assertion(daemon.store) == before
    finally:
        daemon.close()


@pytest.mark.parametrize("left,right", [
    ("1.0.dev1", "1.0a1.dev1"),
    ("1.0a1.dev1", "1.0a1"),
    ("1.0rc1.dev1", "1.0rc1"),
    ("1.0rc1", "1.0"),
    ("1.0", "1.0.post1.dev1"),
    ("1.0.post1.dev1", "1.0.post1"),
])
def test_supported_pep440_pre_post_dev_order_matches_packaging(left, right):
    packaging = pytest.importorskip("packaging.version")
    expected = (packaging.Version(left) > packaging.Version(right)) - (packaging.Version(left) < packaging.Version(right))
    assert _version_compare(left, right) == expected


def test_verifier_json_rejects_duplicate_nested_keys_and_nonfinite_constants():
    assert _json_object(b'{"outer":{"value":1,"value":2}}') is None
    assert _json_object(b'{"value":NaN}') is None
    assert _json_object(b'{"value":Infinity}') is None


def test_shared_nested_wheel_expansion_budget_counts_streamed_bytes_and_stops(tmp_path, monkeypatch):
    import zipfile
    from io import BytesIO

    import comsol_mcp._g2_artifact_verify as verifier

    specs = [
        {"name": "demo-pkg", "license_payload": b"D" * 32},
        {"name": "helper-pkg", "license_payload": b"H" * 48},
        {"name": "third-pkg", "license_payload": b"T" * 64},
        {"name": "fourth-pkg", "license_payload": b"F" * 80},
        {"name": "fifth-pkg", "license_payload": b"Z" * 96},
    ]
    expanded_sizes = []
    filenames = []
    for spec in specs:
        filename, payload, _info = _wheel_bytes(spec)
        filenames.append(filename)
        with zipfile.ZipFile(BytesIO(payload), "r") as wheel:
            expanded_sizes.append(sum(info.file_size for info in wheel.infolist()))
    total = sum(expanded_sizes)
    four_wheel_total = sum(expanded_sizes[:4])

    daemon, project_root, _ = _make_daemon(tmp_path)
    try:
        artifact_id = _registered_response(
            daemon, project_root, _bundle_bytes(specs), request_key="shared-expanded-budget",
        )
        parsed: list[str] = []
        charged = [0]
        original_parse = verifier._parse_wheel
        original_consume = verifier._NestedExpandedBudget.consume

        def recording_parse(content, filename):
            parsed.append(filename)
            return original_parse(content, filename)

        def recording_consume(budget, size):
            charged[0] += size
            return original_consume(budget, size)

        monkeypatch.setattr(verifier, "_parse_wheel", recording_parse)
        monkeypatch.setattr(verifier._NestedExpandedBudget, "consume", recording_consume)
        cases = (
            (total + 1, "VERIFIED_DECLARED_PACKAGE_CONTENT", filenames, total),
            (total, "VERIFIED_DECLARED_PACKAGE_CONTENT", filenames, total),
            (four_wheel_total - 1, "INCOMPLETE", filenames[:4], four_wheel_total - 1),
        )
        for index, (limit, verdict, expected_parsed, expected_charged) in enumerate(cases):
            monkeypatch.setattr(verifier, "MAX_BUNDLE_NESTED_EXPANDED_BYTES", limit)
            parsed.clear()
            charged[0] = 0
            response = _verify(daemon, daemon._test_project_id, artifact_id, request_id=f"shared-budget-{index}")
            assert response["success"] is True, response
            data = response["data"]
            assert data["format_verdict"] == verdict, data
            assert parsed == expected_parsed
            assert charged[0] == expected_charged
            if verdict == "INCOMPLETE":
                assert any(row["code"] == "NESTED_WHEEL_RESOURCE_LIMIT" for row in data["findings"])
    finally:
        daemon.close()
