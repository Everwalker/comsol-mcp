"""T42-R1 fix: OFFLINE_BUNDLE_MANIFEST summary lock hashes are records-bound.

Complements tests/test_t42_bundle_summary_consistency_20261108.py with the two
sides of the post-record swap window that the fix must keep coherent:
1. a swap that is NOT restored before the ZIP member write is refused by the
   post-write scan (BUNDLE_MEMBER_HASH_MISMATCH) with no success receipt, and
   the failed archive's summary still shows the audited records hash;
2. the normal path keeps the summary JSON shape (filenames, status, nested and
   flat fields) identical while tying every summary hash to the audited
   records row and the embedded member.

Synthetic fixture through the real packaging API only; no native or build
provenance claim.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import pathlib
import zipfile

import pytest

from tests.test_full_project_release import make_derived_crypto_inputs, release
from tests.test_t42_derived_build_evidence_20261108 import (
    call_package, make_declared_build_evidence, prepare_packaging,
)


def test_unrestored_post_record_swap_is_refused_and_summary_stays_audited(
        tmp_path, monkeypatch):
    house, wheel, requirements, source_lock, provenance, _ = make_derived_crypto_inputs(tmp_path)
    license_path = tmp_path / "OpenSSL-4.0.2-LICENSE.txt"
    evidence = make_declared_build_evidence(tmp_path, provenance)
    flow = prepare_packaging(tmp_path, house, wheel, requirements, source_lock,
                             provenance, license_path, evidence=evidence)
    original = source_lock.read_bytes()
    original_sha = hashlib.sha256(original).hexdigest()
    swapped = original + b"\n# synthetic unrestored replacement\n"
    marker = {"injected": False}
    real_sha256 = release.sha256_file

    def swap_after_audited_record_read(path):
        digest = real_sha256(path)
        frame = inspect.currentframe()
        caller = frame.f_back if frame is not None else None
        if (pathlib.Path(path).resolve() == source_lock.resolve()
                and caller is not None
                and caller.f_code.co_name == "package_wheelhouse_bundle"
                and caller.f_locals.get("arcname") == "locks/uv.lock"
                and not marker["injected"]):
            source_lock.write_bytes(swapped)
            marker["injected"] = True
        return digest

    monkeypatch.setattr(release, "sha256_file", swap_after_audited_record_read)
    receipt = tmp_path / "swap-unrestored-evidence" / "offline-bundle-macos_x86_64.json"
    try:
        with pytest.raises(release.ReleaseError, match="BUNDLE_MEMBER_HASH_MISMATCH"):
            call_package(tmp_path, flow, house, provenance, license_path,
                         evidence=evidence, name="swap-unrestored")
        assert marker["injected"], "counterexample must reach the post-record window"
        assert not receipt.exists(), "a member contradicting the records cannot publish a success receipt"
        archive = tmp_path / "swap-unrestored.zip"
        if archive.is_file():
            with zipfile.ZipFile(archive) as bundle:
                summary = json.loads(bundle.read("OFFLINE_BUNDLE_MANIFEST.json"))
                member_sha = hashlib.sha256(bundle.read("locks/uv.lock")).hexdigest()
            record = next(row for row in summary["files_excluding_this_manifest"]
                          if row["path"] == "locks/uv.lock")
            # The summary describes the audited records row (original bytes),
            # never the raced member; that divergence is exactly what the
            # post-write scan refused above.
            assert summary["source_lock"]["sha256"] == summary["source_lock_sha256"] == record["sha256"] == original_sha
            assert member_sha == hashlib.sha256(swapped).hexdigest() != original_sha
    finally:
        source_lock.write_bytes(original)


def test_normal_path_summary_shape_and_binding_are_unchanged(tmp_path):
    house, wheel, requirements, source_lock, provenance, _ = make_derived_crypto_inputs(tmp_path)
    license_path = tmp_path / "OpenSSL-4.0.2-LICENSE.txt"
    evidence = make_declared_build_evidence(tmp_path, provenance)
    flow = prepare_packaging(tmp_path, house, wheel, requirements, source_lock,
                             provenance, license_path, evidence=evidence)
    result, archive, _ = call_package(tmp_path, flow, house, provenance, license_path,
                                      evidence=evidence, name="shape")
    assert result["status"] == "OFFLINE_BUNDLE_PACKAGED_NATIVE_UNVERIFIED"
    with zipfile.ZipFile(archive) as bundle:
        summary = json.loads(bundle.read("OFFLINE_BUNDLE_MANIFEST.json"))
        members = {"locks/uv.lock": bundle.read("locks/uv.lock"),
                   "locks/requirements.lock": bundle.read("locks/requirements.lock")}
    assert summary["kind"] == "OFFLINE_INSTALL_BUNDLE"
    assert summary["status"] == "PACKAGED_NATIVE_UNVERIFIED"
    assert summary["native_execution_verified"] is False
    for target, arcname, filename in (("source_lock", "locks/uv.lock", "uv.lock"),
                                      ("requirements", "locks/requirements.lock", "requirements.lock")):
        member_sha = hashlib.sha256(members[arcname]).hexdigest()
        record = next(row for row in summary["files_excluding_this_manifest"] if row["path"] == arcname)
        assert summary[target] == {"filename": filename, "sha256": member_sha}
        assert summary[f"{target}_sha256"] == member_sha == record["sha256"]
        assert summary[f"{target}_included"] == arcname
