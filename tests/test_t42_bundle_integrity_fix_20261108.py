"""T42 (2026-11-08): package-integrity fix controls for the re-audit→records window.

The mother suite `tests/test_t42_bundle_reaudit_consistency_20261108.py` stays
the RED specification; the fix must bind every audited bundle input's exact
bytes/hash to the later records rows and, through the post-write member scan,
to the actual ZIP members — without turning legacy NOT_RECORDED provenance
into a false VERIFIED claim.

Controls here (all synthetic pytest fixtures through the real packaging API;
no network, COMSOL, Worker, SSH, or Intel execution):

* positive: a clean bundle ties audited claim == record row == embedded bytes
  across wheels, locks, provenance, license, manifest, receipts and evidence;
* swap-after-record (audited wheel and un-audited operations doc): a byte swap
  after the records hash must be refused by the records→member binding, and a
  success receipt must never be written;
* wheel-set drift after the re-audit (added/removed wheelhouse file) must be
  refused before the archive exists.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import zipfile

import pytest

from tests.test_full_project_release import (
    make_derived_crypto_inputs, make_wheel, release,
)
from tests.test_t42_derived_build_evidence_20261108 import (
    EVIDENCE_MEMBER, call_package, make_declared_build_evidence, prepare_packaging,
)


def _sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _derived_fixture(tmp_path: pathlib.Path):
    house, wheel, requirements, source_lock, provenance, _ = make_derived_crypto_inputs(tmp_path)
    license_path = tmp_path / "OpenSSL-4.0.2-LICENSE.txt"
    evidence = make_declared_build_evidence(tmp_path, provenance)
    flow = prepare_packaging(tmp_path, house, wheel, requirements, source_lock, provenance,
                             license_path, evidence=evidence)
    return house, wheel, source_lock, provenance, license_path, evidence, flow


def test_positive_records_and_members_equal_audited_claims(tmp_path: pathlib.Path) -> None:
    """Clean forward path: every audited member ties three ways end to end."""
    house, wheel, source_lock, provenance, license_path, evidence, flow = _derived_fixture(tmp_path)
    result, archive, evidence_dir = call_package(tmp_path, flow, house, provenance, license_path,
                                                 evidence=evidence, name="integrity-positive")
    assert result["status"] == "OFFLINE_BUNDLE_PACKAGED_NATIVE_UNVERIFIED"
    assert (evidence_dir / "offline-bundle-macos_x86_64.json").is_file()
    with zipfile.ZipFile(archive) as zf:
        indexed = json.loads(zf.read("OFFLINE_BUNDLE_MANIFEST.json"))
        rows = {row["path"]: row for row in indexed["files_excluding_this_manifest"]}
        package_manifest = json.loads(zf.read("PACKAGE_MANIFEST.json"))
        for member, claim_sha, claim_bytes in (
            (f"wheelhouse/{wheel.name}", package_manifest["derived_wheel"]["wheel"]["sha256"],
             package_manifest["derived_wheel"]["wheel"]["bytes"]),
            ("wheelhouse/comsol_mcp-0.1.9-py3-none-any.whl",
             next(r["sha256"] for r in package_manifest["wheels"] if r["file"] == "comsol_mcp-0.1.9-py3-none-any.whl"),
             None),
            ("locks/requirements.lock", package_manifest["requirements"]["sha256"], None),
            ("locks/uv.lock", package_manifest["source_lock"]["sha256"], None),
            ("evidence/cryptography-x86-derived-provenance.json",
             package_manifest["derived_wheel"]["provenance"]["sha256"], None),
            (package_manifest["derived_wheel"]["native_component"]["license_file"]["path"],
             package_manifest["derived_wheel"]["native_component"]["license_file"]["sha256"],
             package_manifest["derived_wheel"]["native_component"]["license_file"]["bytes"]),
            (EVIDENCE_MEMBER, package_manifest["derived_wheel"]["build_evidence"]["sha256"],
             package_manifest["derived_wheel"]["build_evidence"]["bytes"]),
        ):
            embedded = zf.read(member)
            assert hashlib.sha256(embedded).hexdigest() == rows[member]["sha256"] == claim_sha
            assert len(embedded) == rows[member]["bytes"]
            if claim_bytes is not None:
                assert claim_bytes == rows[member]["bytes"]
        # the audited manifest/receipt bytes themselves are embedded unchanged
        assert zf.read("PACKAGE_MANIFEST.json") == (flow["metadata"] / "PACKAGE_MANIFEST.json").read_bytes()
        assert zf.read("evidence/wheel-build-receipt.json") == \
            (flow["build"] / "wheel-build-receipt.json").read_bytes()
        assert _sha(provenance) == package_manifest["derived_wheel"]["provenance"]["sha256"]
        assert _sha(evidence) == package_manifest["derived_wheel"]["build_evidence"]["sha256"]


@pytest.mark.parametrize("swap_member,flow_key", [
    ("wheelhouse/comsol_mcp-0.1.9-py3-none-any.whl", "app"),
    ("docs/RELEASE_OPERATIONS.md", "operations"),
], ids=["audited-wheel", "un-audited-operations-doc"])
def test_swap_after_record_is_refused_without_success_receipt(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, swap_member: str, flow_key: str
) -> None:
    """Swap between the records hash and the ZIP write: the member must stay
    bound to the record and the run must end without any success receipt."""
    house, wheel, source_lock, provenance, license_path, evidence, flow = _derived_fixture(tmp_path)
    target_path = flow[flow_key]
    original_bytes = target_path.read_bytes()
    original_write = zipfile.ZipFile.write

    def swapping_write(self, path, arcname=None, **kwargs):
        if arcname == swap_member:
            target_path.write_bytes(original_bytes + b"\npost-record-swap")
        return original_write(self, path, arcname=arcname, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "write", swapping_write)
    with pytest.raises(release.ReleaseError, match="recursive scan failed|HASH_MISMATCH"):
        call_package(tmp_path, flow, house, provenance, license_path, evidence=evidence,
                     name="post-record")
    assert target_path.read_bytes() != original_bytes, "the injected post-record swap must have fired"
    assert not (tmp_path / "post-record-evidence" / "offline-bundle-macos_x86_64.json").exists(), \
        "no success receipt may be written for a swapped bundle member"


@pytest.mark.parametrize("drift", ["added", "removed"])
def test_wheelhouse_membership_drift_after_reaudit_rejected(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, drift: str
) -> None:
    """A wheelhouse file appearing or vanishing after the re-audit must fail
    before the archive exists: records may only contain the audited wheel set."""
    house, wheel, source_lock, provenance, license_path, evidence, flow = _derived_fixture(tmp_path)
    real_create = release.create_bundle_manifest
    events = []

    def drifting_create(*args, **kwargs):
        result = real_create(*args, **kwargs)
        if not events:
            if drift == "added":
                make_wheel(house / "extra_demo-1.0.0-py3-none-any.whl", name="extra-demo", version="1.0.0")
            else:
                (flow["app"]).unlink()
            events.append(True)
        return result

    monkeypatch.setattr(release, "create_bundle_manifest", drifting_create)
    with pytest.raises(release.ReleaseError, match="wheelhouse members changed"):
        call_package(tmp_path, flow, house, provenance, license_path, evidence=evidence,
                     name="wheel-drift")
    assert events == [True], "the drift injection must fire after the real re-audit"
    assert not (tmp_path / "wheel-drift.zip").exists(), "the rejected bundle must not be written"
    assert not (tmp_path / "wheel-drift-evidence" / "offline-bundle-macos_x86_64.json").exists()
