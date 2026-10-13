import copy
import hashlib
import io
import json
import os
from pathlib import Path
import uuid
import zipfile

import pytest
from tools import w26_prepare_current_inputs as inputs


def wheel(extra=None):
    metadata = b"Metadata-Version: 2.3\nName: fixture-pkg\nVersion: 1.0\nRequires-Python: >=3.10\n\n"
    declaration = b"Wheel-Version: 1.0\nTag: py3-none-any\n\n"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("fixture_pkg-1.0.dist-info/METADATA", metadata)
        archive.writestr("fixture_pkg-1.0.dist-info/WHEEL", declaration)
        if extra:
            archive.writestr(extra, "fixture")
    raw = output.getvalue()
    row = {"filename": "fixture_pkg-1.0-py3-none-any.whl", "name": "fixture-pkg", "version": "1.0",
           "actual_record": {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()},
           "METADATA_sha256": hashlib.sha256(metadata).hexdigest(),
           "WHEEL_sha256": hashlib.sha256(declaration).hexdigest(), "Requires-Python": ">=3.10",
           "Requires-Dist": [], "Provides-Extra": [], "filename_tags": ["py3-none-any"],
           "internal_tags": ["py3-none-any"]}
    return raw, row


def test_pinned_actual43_graph_and_correct_packaging_pin():
    closure = inputs.load_closure()
    assert inputs.validate_graph(closure)["active_dependency_checks"] == 61
    assert sum(r["actual_record"]["bytes"] for r in closure["actual_inputs"]) == 29913882
    assert next(r for r in closure["actual_inputs"] if r["name"] == "packaging")["version"] == "26.3"


@pytest.mark.parametrize("change", ["version", "marker", "missing"])
def test_graph_drift_rejected(change):
    closure = copy.deepcopy(inputs.load_closure())
    if change == "version":
        next(r for r in closure["actual_inputs"] if r["name"] == "packaging")["version"] = "26.2"
    elif change == "marker":
        closure["actual_dependency_checks"][0]["applicable"] = False
    else:
        closure["actual_inputs"] = [r for r in closure["actual_inputs"] if r["name"] != "packaging"]
    with pytest.raises((RuntimeError, KeyError)):
        inputs.validate_graph(closure)


def test_fixture_zip_only_never_imports_code():
    raw, row = wheel()
    assert inputs.validate_wheel(raw, row)["wheel_code_imported"] is False


@pytest.mark.parametrize("member", ["../escape", "/absolute", "a\\b", "a/./b", "a//b"])
def test_zip_member_traversal_is_rejected_even_with_fixture_rehashed_pin(member):
    raw, row = wheel(member)
    with pytest.raises(RuntimeError, match="unsafe wheel"):
        inputs.validate_wheel(raw, row)


@pytest.mark.parametrize("change", ["bytes", "sha256", "metadata", "name", "tags", "requires"])
def test_pinned_content_and_internal_identity_rejected(change):
    raw, row = wheel()
    if change in ["bytes", "sha256"]:
        row["actual_record"][change] = len(raw) + 1 if change == "bytes" else "0" * 64
    elif change == "metadata": row["METADATA_sha256"] = "0" * 64
    elif change == "name": row["name"] = "other"
    elif change == "tags": row["internal_tags"] = ["cp312-none-any"]
    else: row["Requires-Dist"] = ["unavailable>=1"]
    with pytest.raises(RuntimeError): inputs.validate_wheel(raw, row)


@pytest.mark.parametrize("kind", ["symlink", "worldwrite", "hardlink"])
def test_reused_strict_nofollow_first_record_reader_refuses_untrusted_input(tmp_path, kind):
    helper = inputs._helpers()
    source = tmp_path / "source"
    source.write_bytes(b"original")
    source.chmod(0o600)
    if kind == "symlink":
        target = tmp_path / "link"; target.symlink_to(source); source = target
    elif kind == "worldwrite": source.chmod(0o666)
    else: os.link(source, tmp_path / "link")
    with pytest.raises((OSError, helper.Refusal)): helper.raw_file(source)


def test_strict_reader_refuses_changed_original_firstrecord(tmp_path):
    helper = inputs._helpers()
    source = tmp_path / "source"; source.write_bytes(b"original"); source.chmod(0o600)
    record = helper.raw_file(source)[1]
    source.write_bytes(b"modified")
    with pytest.raises(helper.Refusal, match="checkpoint changed"):
        helper.raw_file(source, expected=record)


def test_fresh_namespace_no_clobber_preserves_original_first_record():
    root = Path("/private/tmp") / ("cvw26-test-" + uuid.uuid4().hex)
    manifest = inputs.prepare(root)
    original = (root / "FIRST_RECORD.json").read_bytes()
    assert len(json.loads(manifest.read_bytes())["rows"]) == 43
    with pytest.raises(FileExistsError): inputs.prepare(root)
    assert (root / "FIRST_RECORD.json").read_bytes() == original


@pytest.mark.parametrize("root", ["relative", "/tmp/cvw26-alias", "/private/tmp/wrong-prefix"])
def test_unsafe_namespace_rejected_before_creation(root):
    with pytest.raises(RuntimeError, match="namespace"): inputs.prepare(root)


def test_network_requires_exact_official_url_before_open():
    _, row = wheel(); row["source_url"] = "http://files.pythonhosted.org/packages/fixture.whl"
    with pytest.raises(RuntimeError, match="official wheel URL"): inputs.fetch(row, 1)


def test_partial_failure_keeps_first_record_and_original_failure(monkeypatch):
    root = Path("/private/tmp") / ("cvw26-test-" + uuid.uuid4().hex)
    monkeypatch.setattr(inputs, "fetch", lambda *_: (_ for _ in ()).throw(OSError("ORIGINAL_TLS_FAILURE")))
    with pytest.raises(OSError, match="ORIGINAL_TLS_FAILURE"): inputs.prepare(root, download=True)
    failure = json.loads((root / "FIRST_FAILURE.json").read_bytes())
    assert failure["error"] == "ORIGINAL_TLS_FAILURE" and (root / "FIRST_RECORD.json").exists()


@pytest.mark.parametrize("number", [1, 13, 61, 111])
def test_original_numeric_offline_denial_rejects_misleading_connection_failure(number):
    import errno
    denial = inputs.retained_source_function("stage_gate.py", "a3b3003be938f3d7719ce865768a4df72976504c4f01636b67492b00c30df18c", "network_denial")
    def refused(*a, **kw): raise OSError(number, "synthetic kernel boundary")
    result = denial(1234, connector=refused)
    assert (result["status"] == "PASS_ACTUAL_EPERM") == (number in [errno.EPERM, errno.EACCES])


@pytest.mark.parametrize("change", [None, "info", "number", "text"])
def test_original_strict359_provider_requirement_is_preserved(change):
    validate = inputs.retained_source_function("consumers.py", "f566435bc56140c922ca50e23d0834f1f56f9f8eab545c2799951568fe67ec0d", "validate_provider")
    row = {"INFO": [3, 5, 0, 9, 0], "NUMBER": 0x30500090, "TEXT": "OpenSSL 3.5.9 fixture"}
    if change == "info": row["INFO"] = [3, 5, 0, 8, 0]
    if change == "number": row["NUMBER"] += 1
    if change == "text": row["TEXT"] = "OpenSSL 3.5.10 fixture"
    if change:
        with pytest.raises(inputs._helpers().Refusal): validate(row)
    else: validate(row)


def test_exact_original_sandbox_profile_preserves_process_scoped_writes_and_denies_network():
    profile = inputs.retained_source_function("controller.py", "2dce314e31b9c5d12333edf02f6ef94b71e484f08bef832aabc39b6990dc3056", "sandbox_profile")
    result = profile(Path("/private/tmp/cvw26-fixture/unit"), Path("/private/tmp/cvw26-fixture/source"))
    assert "(deny network*)" in result and "(deny file-write*)" in result
    assert '/wheelhouse' not in result and '/inputs' not in result
    assert result.count('(allow file-write* (subpath ') == 6


def test_eight_command_blueprint_is_non_executable_and_never_replays_old_paths():
    plan = inputs.draft_eight_commands(Path("/private/tmp/cvw26-fixture"))
    assert len(plan["commands"]) == 8 and plan["native_authorized"] is False
    assert plan["budget"]["whole"] == 1800 and plan["budget"]["cleanup"] == 300
    assert all("argv" not in row for row in plan["commands"])
    assert "comsol-intel-inputs-r1" not in json.dumps(plan["commands"])
    assert "<FRESH_HASH_VERIFIED_CPYTHON_31213_SSL359>" in plan["commands"][0]["argv_blueprint"]


def test_incomplete_reuse_manifest_refused_before_new_namespace(tmp_path):
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'all43_present': False, 'rows': []})); manifest.chmod(0o600)
    root = Path('/private/tmp') / ('cvw26-test-' + uuid.uuid4().hex)
    with pytest.raises(RuntimeError, match='complete actual43'): inputs.prepare(root, reuse_manifest=manifest)
    assert not root.exists()
