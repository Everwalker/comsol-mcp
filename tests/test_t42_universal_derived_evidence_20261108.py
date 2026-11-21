"""T42 (2026-11-08): the ordinary universal release CLI must honour declared derived build evidence.

The T42 helper layer (tools/full_project_release.py) already refuses any
provenance that declares a ``build_evidence`` block unless the caller supplies
that evidence file explicitly and its SHA256 matches the claim. This file pins
the integration through ``tools/universal_release.py``: build_target and the CLI
must thread an explicit ``derived_build_evidence`` input end to end into the
offline ZIP, must reject missing/wrong/symlinked evidence, must reject evidence
that the provenance never declared, and must keep legacy undeclared provenance
at the original NOT_RECORDED/UNVERIFIED behaviour. The provenance's own
``build_evidence.path`` string is data only and is never opened.

All inputs are synthetic pytest tmp fixtures; no COMSOL, network, or git writes.
"""
import hashlib
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import universal_release as tool
from tests.test_full_project_release import make_build_evidence, make_derived_crypto_inputs, make_wheel

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_MEMBER = "evidence/derived-wheel-build-evidence.json"


def declared_build_evidence(tmp_path, provenance_path, *, decoy=True):
    """Rewrite the fixture provenance to declare a build_evidence block.

    Returns (evidence_file, evidence_sha256). The block's ``path`` field is a
    decoy pointing at a different regular file with different bytes so a test
    can prove the helper only ever opens the caller-supplied file.
    """
    payload = json.loads(provenance_path.read_text())
    evidence = tmp_path / "synthetic-derived-build-log.json"
    evidence.write_text(json.dumps({"status": "SYNTHETIC_BUILD_FIXTURE", "command": "cargo build --offline"}),
                        encoding="utf-8")
    sha = hashlib.sha256(evidence.read_bytes()).hexdigest()
    block = {"sha256": sha, "bytes": evidence.stat().st_size}
    if decoy:
        decoy_file = tmp_path / "provenance-claimed-path-build-log.json"
        decoy_file.write_text("attacker-controlled decoy; must never be opened", encoding="utf-8")
        block["path"] = decoy_file.as_posix()
    payload["build_evidence"] = block
    provenance_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return evidence, sha


def derived_fixture(tmp_path, *, provenance_hook=None):
    """Assemble universal.build_target inputs for the macos_x86_64 derived path."""
    _house, wheel, exported, lock, provenance, _sha = make_derived_crypto_inputs(tmp_path)
    if provenance_hook is not None:
        provenance_hook(provenance)
    app = tmp_path / "comsol_mcp-1.0-py3-none-any.whl"
    make_wheel(app, name="comsol-mcp")
    evidence = tmp_path / "build"
    make_build_evidence(evidence, app, version="1.0")
    ops = tmp_path / "operations.md"
    ops.write_text("Synthetic software packaging test.\n")
    p = {"name": "comsol-mcp", "version": "1.0", "requires-python": ">=3.10",
         "dependencies": ["cryptography>=3.4"], "optional-dependencies": {"desktop": []}}
    house = tmp_path / "available"
    house.mkdir()
    out = tmp_path / "target"
    return tmp_path, p, house, wheel, exported, lock, provenance, app, evidence, ops, out


def call_build_target(fixture, *, derived_build_evidence="__omitted__"):
    (tmp_path, p, house, wheel, exported, lock, provenance, app, evidence, ops,
     out) = fixture
    kwargs = {} if derived_build_evidence == "__omitted__" else {"derived_build_evidence": derived_build_evidence}
    return tool.build_target(p, "macos_x86_64", house, exported, lock, app, out, evidence, ops,
                             wheel, provenance, tmp_path / "OpenSSL-4.0.2-LICENSE.txt", **kwargs)


def test_declared_build_evidence_without_input_is_rejected(tmp_path):
    """Provenance that declares build_evidence must never package without the explicit file."""
    fixture = derived_fixture(tmp_path, provenance_hook=lambda prov: declared_build_evidence(tmp_path, prov))
    with pytest.raises(tool.release.ReleaseError, match="provenance declares build_evidence"):
        call_build_target(fixture)
    assert not (tmp_path / "target" / "offline.zip").exists()


def test_declared_build_evidence_flows_through_build_target_into_zip(tmp_path):
    """The caller-supplied evidence file is verified and embedded under a fixed safe member."""
    declared = {}

    def hook(prov):
        declared["evidence"], declared["sha"] = declared_build_evidence(tmp_path, prov)

    fixture = derived_fixture(tmp_path, provenance_hook=hook)
    row = call_build_target(fixture, derived_build_evidence=declared["evidence"])
    assert row["status"] == "PACKAGED_NATIVE_UNVERIFIED"
    assert row["derived_input"]["build_evidence"]["verification"] == "VERIFIED"
    assert row["derived_input"]["build_evidence"]["sha256"] == declared["sha"]
    assert row["derived_input"]["build_evidence"]["bundle_member"] == EVIDENCE_MEMBER
    with zipfile.ZipFile(tmp_path / "target" / "offline.zip") as z:
        names = set(z.namelist())
        assert EVIDENCE_MEMBER in names
        assert z.read(EVIDENCE_MEMBER) == declared["evidence"].read_bytes()
        manifest = json.loads(z.read("PACKAGE_MANIFEST.json"))
        assert manifest["derived_wheel"]["build_evidence"]["verification"] == "VERIFIED"
        assert manifest["derived_wheel"]["build_evidence"]["declared_sha256"] == declared["sha"]
        bundle = json.loads(z.read("OFFLINE_BUNDLE_MANIFEST.json"))
        member = next(r for r in bundle["files_excluding_this_manifest"] if r["path"] == EVIDENCE_MEMBER)
        assert member["sha256"] == declared["sha"]


def test_wrong_derived_build_evidence_sha256_is_rejected(tmp_path):
    declared = {}

    def hook(prov):
        declared["evidence"], _sha = declared_build_evidence(tmp_path, prov)

    fixture = derived_fixture(tmp_path, provenance_hook=hook)
    wrong = tmp_path / "wrong-build-log.json"
    wrong.write_text('{"status": "DIFFERENT FIXTURE CONTENT"}', encoding="utf-8")
    with pytest.raises(tool.release.ReleaseError, match="SHA256 does not match"):
        call_build_target(fixture, derived_build_evidence=wrong)
    assert not (tmp_path / "target" / "offline.zip").exists()


def test_symlink_derived_build_evidence_is_rejected(tmp_path):
    declared = {}

    def hook(prov):
        declared["evidence"], _sha = declared_build_evidence(tmp_path, prov)

    fixture = derived_fixture(tmp_path, provenance_hook=hook)
    real = tmp_path / "real-target.json"
    real.write_bytes(declared["evidence"].read_bytes())
    link = tmp_path / "linked-build-log.json"
    link.symlink_to(real)
    with pytest.raises(tool.release.ReleaseError, match="regular file"):
        call_build_target(fixture, derived_build_evidence=link)
    assert not (tmp_path / "target" / "offline.zip").exists()


def test_derived_build_evidence_without_declaration_is_rejected(tmp_path):
    """Passing evidence the legacy provenance never declared is an error, not silent trust."""
    fixture = derived_fixture(tmp_path)
    evidence = tmp_path / "undeclared-build-log.json"
    evidence.write_text('{"status": "SYNTHETIC"}', encoding="utf-8")
    with pytest.raises(tool.release.ReleaseError, match="declares no build_evidence block"):
        call_build_target(fixture, derived_build_evidence=evidence)


def test_legacy_undeclared_provenance_stays_unverified_without_input(tmp_path):
    """Original behaviour is preserved: no block, no input, NOT_RECORDED/UNVERIFIED, no member."""
    fixture = derived_fixture(tmp_path)
    row = call_build_target(fixture)
    build_evidence = row["derived_input"]["build_evidence"]
    assert build_evidence["status"] == "NOT_RECORDED" and build_evidence["verification"] == "UNVERIFIED"
    assert build_evidence["bundle_member"] is None
    with zipfile.ZipFile(tmp_path / "target" / "offline.zip") as z:
        assert EVIDENCE_MEMBER not in set(z.namelist())


def test_cli_parses_derived_build_evidence_flag(tmp_path, monkeypatch):
    captured = {}

    def fake_run(args):
        captured["args"] = args
        return {"targets": {}}

    monkeypatch.setattr(tool, "run", fake_run)
    house = tmp_path / "house"
    house.mkdir()
    ops = tmp_path / "operations.md"
    ops.write_text("fixture\n")
    evidence = tmp_path / "derived-build-evidence.json"
    evidence.write_text("{}\n")
    exit_code = tool.main([
        "--source", str(ROOT), "--lock", str(ROOT / "uv.lock"),
        "--builder-python", str(tmp_path / "missing-builder-python"),
        "--uv", str(tmp_path / "missing-uv"),
        "--operations", str(ops), "--output", str(tmp_path / "fresh-output"),
        "--wheelhouse", f"macos_x86_64={house}",
        "--derived-intel-wheel", str(tmp_path / "missing.whl"),
        "--derived-provenance", str(tmp_path / "missing-provenance.json"),
        "--openssl-license", str(tmp_path / "missing-license.txt"),
        "--derived-build-evidence", str(evidence),
    ])
    assert exit_code == 0
    assert captured["args"].derived_build_evidence == evidence


def test_run_rejects_derived_build_evidence_without_derived_inputs(tmp_path):
    """--derived-build-evidence alone must fail before any snapshot work starts."""
    evidence = tmp_path / "derived-build-evidence.json"
    evidence.write_text("{}\n")
    args = SimpleNamespace(source=ROOT, lock=ROOT / "uv.lock", output=(tmp_path / "fresh-output").absolute(),
                           builder_python=None, uv=None, operations=None, wheelhouse={},
                           derived_intel_wheel=None, derived_provenance=None, openssl_license=None,
                           derived_build_evidence=evidence, primary_notices=None)
    with pytest.raises(tool.release.ReleaseError, match="derived build evidence"):
        tool.run(args)
    assert not (tmp_path / "fresh-output").exists()
