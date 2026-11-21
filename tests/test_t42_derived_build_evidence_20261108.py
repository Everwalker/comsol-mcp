"""T42 (2026-11-08): declared derived-wheel build evidence must be enforced and embedded.

The corrected attestation `_state/t42-intel-correction-20261108/cryptography-derived-provenance-corrected.json`
declares a `build_evidence` block with a SHA256, but the release helper previously
ignored that claim entirely: a provenance claiming a wrong (or missing) evidence
hash still passed `verify_derived_wheel`, the evidence file was never required,
verified, or embedded in the offline bundle, and the bundle manifest gave no
signal about the evidence at all.

These tests are synthetic (pytest tmp dirs, fake wheels); no network, COMSOL,
Worker, SSH, or Intel execution. They pin the corrected contract:

* a provenance that declares `build_evidence` forces the caller to explicitly
  supply a readable regular file whose SHA256 matches exactly; the provenance's
  own `path` string is never opened;
* forged/missing/malformed claims, symlink or non-file inputs, evidence given
  without a declaration, tampering after the manifest, and byte swaps inside the
  packaging read window all fail before any success receipt is written;
* a verified record is embedded at the fixed member
  `evidence/derived-wheel-build-evidence.json` and hash-indexed, with
  provenance claim == manifest hash == actual embedded bytes;
* legacy schema v1 provenance without any block keeps working but must be
  honestly recorded as NOT_RECORDED/UNVERIFIED, never presented as verified.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import zipfile

import pytest

from tests.test_full_project_release import (
    TOOL, make_build_evidence, make_derived_crypto_inputs, make_wheel, release,
)

EVIDENCE_MEMBER = "evidence/derived-wheel-build-evidence.json"


def _sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_declared_build_evidence(tmp_path: pathlib.Path, provenance_path: pathlib.Path,
                                 *, sha_override: str | None = None,
                                 claim_shape: str = "normal") -> pathlib.Path:
    """Attach a build_evidence block to the synthetic provenance fixture."""
    evidence = tmp_path / "dependency-intel-build.json"
    evidence.write_text(json.dumps({"kind": "synthetic-historical-build-evidence",
                                    "note": "fixture, not a real build record"}) + "\n",
                        encoding="utf-8")
    payload = json.loads(provenance_path.read_text(encoding="utf-8"))
    if claim_shape == "normal":
        payload["build_evidence"] = {
            "path": "untrusted/tool-chosen/dependency-intel-build.json",
            "sha256": sha_override or _sha(evidence),
        }
    elif claim_shape == "malformed-sha":
        payload["build_evidence"] = {"path": "x.json", "sha256": "not-a-sha256"}
    elif claim_shape == "not-a-dict":
        payload["build_evidence"] = "just a string"
    else:
        raise AssertionError(f"unknown claim shape {claim_shape}")
    provenance_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return evidence


def prepare_packaging(tmp_path: pathlib.Path, wheelhouse: pathlib.Path, crypto_wheel: pathlib.Path,
                      requirements: pathlib.Path, source_lock: pathlib.Path,
                      provenance: pathlib.Path, openssl_license: pathlib.Path,
                      *, evidence: pathlib.Path | None = None):
    """Compose the lock chain and metadata manifest used by package-bundle."""
    app = wheelhouse / "comsol_mcp-0.1.9-py3-none-any.whl"
    make_wheel(app, name="comsol-mcp", version="0.1.9")
    derived_lock = tmp_path / "requirements-derived.lock"
    release.compose_derived_wheel_lock(requirements, crypto_wheel, source_lock, provenance,
                                       openssl_license, "macos_x86_64", derived_lock,
                                       derived_build_evidence=evidence)
    final_lock = tmp_path / "requirements.lock"
    release.compose_install_lock(derived_lock, app, "macos_x86_64", final_lock)
    metadata = tmp_path / "metadata"
    manifest = release.create_bundle_manifest(wheelhouse, "macos_x86_64", final_lock,
                                              source_lock, metadata, provenance,
                                              openssl_license,
                                              derived_build_evidence=evidence)
    build = tmp_path / "build-evidence"
    make_build_evidence(build, app)
    operations = tmp_path / "RELEASE_OPERATIONS.md"
    operations.write_text("Offline installation operations.\n", encoding="utf-8")
    return {"app": app, "final_lock": final_lock, "metadata": metadata,
            "build": build, "operations": operations, "manifest": manifest}


def call_package(tmp_path: pathlib.Path, flow: dict, wheelhouse: pathlib.Path,
                 provenance: pathlib.Path, openssl_license: pathlib.Path,
                 *, evidence: pathlib.Path | None = None, name: str = "bundle"):
    archive = tmp_path / f"{name}.zip"
    evidence_dir = tmp_path / f"{name}-evidence"
    result = release.package_wheelhouse_bundle(
        wheelhouse, flow["final_lock"], tmp_path / "uv.lock",
        flow["metadata"], flow["operations"], "macos_x86_64", archive, evidence_dir,
        flow["build"], TOOL, provenance, openssl_license,
        derived_build_evidence=evidence,
    )
    return result, archive, evidence_dir


def run_cli(*argv: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(TOOL), *argv], cwd=cwd,
                          capture_output=True, text=True, timeout=180,
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


def derived_lock_argv(requirements, wheel, source_lock, provenance, openssl_license, out,
                      evidence: pathlib.Path | None = None) -> list[str]:
    argv = ["compose-derived-lock", "--requirements", str(requirements), "--wheel", str(wheel),
            "--source-lock", str(source_lock), "--derived-provenance", str(provenance),
            "--openssl-license", str(openssl_license), "--target", "macos_x86_64",
            "--out", str(out)]
    if evidence is not None:
        argv += ["--derived-build-evidence", str(evidence)]
    return argv


# ---------------------------------------------------------------- positives

def test_positive_compose_lock_verifies_declared_evidence(tmp_path: pathlib.Path) -> None:
    wheelhouse, wheel, requirements, source_lock, provenance, _sha_ = make_derived_crypto_inputs(tmp_path)
    openssl_license = tmp_path / "OpenSSL-4.0.2-LICENSE.txt"
    evidence = make_declared_build_evidence(tmp_path, provenance)
    out = tmp_path / "requirements-derived.lock"
    receipt = release.compose_derived_wheel_lock(requirements, wheel, source_lock, provenance,
                                                 openssl_license, "macos_x86_64", out,
                                                 derived_build_evidence=evidence)
    assert receipt["status"] == "DERIVED_TARGET_LOCK_HASHED"
    block = receipt["derived_build_evidence"]
    assert block["verification"] == "VERIFIED"
    assert block["sha256"] == block["declared_sha256"] == _sha(evidence)


def test_positive_manifest_and_bundle_embed_evidence_with_three_way_match(tmp_path: pathlib.Path) -> None:
    wheelhouse, crypto_wheel, requirements, source_lock, provenance, _sha_ = make_derived_crypto_inputs(tmp_path)
    openssl_license = tmp_path / "OpenSSL-4.0.2-LICENSE.txt"
    evidence = make_declared_build_evidence(tmp_path, provenance)
    flow = prepare_packaging(tmp_path, wheelhouse, crypto_wheel, requirements, source_lock,
                             provenance, openssl_license, evidence=evidence)
    block = flow["manifest"]["derived_wheel"]["build_evidence"]
    assert block["verification"] == "VERIFIED"
    assert block["sha256"] == block["declared_sha256"] == _sha(evidence)
    result, archive, evidence_dir = call_package(tmp_path, flow, wheelhouse, provenance,
                                                 openssl_license, evidence=evidence)
    assert result["status"] == "OFFLINE_BUNDLE_PACKAGED_NATIVE_UNVERIFIED"
    assert (evidence_dir / "offline-bundle-macos_x86_64.json").is_file()
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        assert EVIDENCE_MEMBER in names
        # the caller's own filename must not leak into the member set
        assert not any(name.startswith("dependency-intel-build") or
                       "untrusted" in name for name in names)
        embedded = zf.read(EVIDENCE_MEMBER)
        assert embedded == evidence.read_bytes()
        indexed = json.loads(zf.read("OFFLINE_BUNDLE_MANIFEST.json"))
        rows = {row["path"]: row for row in indexed["files_excluding_this_manifest"]}
        # provenance claim == manifest hash index == actual embedded member bytes
        assert rows[EVIDENCE_MEMBER]["sha256"] == _sha(evidence) == \
            flow["manifest"]["derived_wheel"]["build_evidence"]["declared_sha256"]
        assert hashlib.sha256(embedded).hexdigest() == rows[EVIDENCE_MEMBER]["sha256"]


def test_cli_compose_derived_lock_accepts_explicit_evidence(tmp_path: pathlib.Path) -> None:
    isolated = tmp_path / "isolated-run"
    isolated.mkdir()
    (_house, wheel, requirements, source_lock, provenance, _sha_) = make_derived_crypto_inputs(tmp_path)
    openssl_license = tmp_path / "OpenSSL-4.0.2-LICENSE.txt"
    evidence = make_declared_build_evidence(tmp_path, provenance)
    out = isolated / "requirements-derived.lock"
    proc = run_cli(*derived_lock_argv(requirements, wheel, source_lock, provenance,
                                      openssl_license, out, evidence), cwd=isolated)
    assert proc.returncode == 0, f"exit={proc.returncode} stderr={proc.stderr!r}"
    receipt = json.loads(proc.stdout)
    assert receipt["derived_build_evidence"]["verification"] == "VERIFIED"
    assert receipt["derived_build_evidence"]["sha256"] == _sha(evidence)


# ---------------------------------------------------------------- rejections

def test_bad_hash_claim_rejected_across_compose_manifest_package(tmp_path: pathlib.Path) -> None:
    wheelhouse, crypto_wheel, requirements, source_lock, provenance, _sha_ = make_derived_crypto_inputs(tmp_path)
    openssl_license = tmp_path / "OpenSSL-4.0.2-LICENSE.txt"
    # like the original b749... bug: the provenance claim differs from the real file
    evidence = make_declared_build_evidence(tmp_path, provenance, sha_override="b7" + "0" * 62)
    out = tmp_path / "requirements-derived.lock"
    with pytest.raises(release.ReleaseError, match="build evidence|evidence"):
        release.compose_derived_wheel_lock(requirements, crypto_wheel, source_lock, provenance,
                                           openssl_license, "macos_x86_64", out,
                                           derived_build_evidence=evidence)
    assert not out.exists(), "bad evidence must not produce a derived lock artifact"
    with pytest.raises(release.ReleaseError):
        release.create_bundle_manifest(wheelhouse, "macos_x86_64", requirements, source_lock,
                                       tmp_path / "metadata", provenance, openssl_license,
                                       derived_build_evidence=evidence)
    assert not (tmp_path / "metadata" / "PACKAGE_MANIFEST.json").exists()
    with pytest.raises(release.ReleaseError):
        release.verify_derived_wheel(provenance, crypto_wheel, source_lock, openssl_license,
                                     "macos_x86_64", derived_build_evidence=evidence)


def test_missing_explicit_evidence_for_declared_provenance_rejected(tmp_path: pathlib.Path) -> None:
    wheelhouse, crypto_wheel, requirements, source_lock, provenance, _sha_ = make_derived_crypto_inputs(tmp_path)
    openssl_license = tmp_path / "OpenSSL-4.0.2-LICENSE.txt"
    make_declared_build_evidence(tmp_path, provenance)
    with pytest.raises(release.ReleaseError, match="build evidence|evidence"):
        release.verify_derived_wheel(provenance, crypto_wheel, source_lock, openssl_license,
                                     "macos_x86_64")
    out = tmp_path / "requirements-derived.lock"
    with pytest.raises(release.ReleaseError):
        release.compose_derived_wheel_lock(requirements, crypto_wheel, source_lock, provenance,
                                           openssl_license, "macos_x86_64", out)
    assert not out.exists(), "missing evidence must not produce a derived lock artifact"
    with pytest.raises(release.ReleaseError):
        release.create_bundle_manifest(wheelhouse, "macos_x86_64", requirements, source_lock,
                                       tmp_path / "metadata", provenance, openssl_license)


def test_cli_compose_derived_lock_declared_without_evidence_fails(tmp_path: pathlib.Path) -> None:
    """RED (T42): today the CLI exits 0 while ignoring a declared-but-unchecked claim."""
    isolated = tmp_path / "isolated-run"
    isolated.mkdir()
    (_house, wheel, requirements, source_lock, provenance, _sha_) = make_derived_crypto_inputs(tmp_path)
    openssl_license = tmp_path / "OpenSSL-4.0.2-LICENSE.txt"
    make_declared_build_evidence(tmp_path, provenance)
    out = isolated / "requirements-derived.lock"
    proc = run_cli(*derived_lock_argv(requirements, wheel, source_lock, provenance,
                                      openssl_license, out), cwd=isolated)
    assert proc.returncode != 0, f"declared claim without explicit input must fail: stdout={proc.stdout!r}"
    assert not out.exists(), "declared claim without explicit input must not produce an artifact"
    assert "FAIL" in proc.stderr


def test_evidence_input_without_declaration_rejected(tmp_path: pathlib.Path) -> None:
    wheelhouse, crypto_wheel, requirements, source_lock, provenance, _sha_ = make_derived_crypto_inputs(tmp_path)
    openssl_license = tmp_path / "OpenSSL-4.0.2-LICENSE.txt"
    evidence = tmp_path / "dependency-intel-build.json"
    evidence.write_text('{"kind": "undeclared input"}\n', encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="no build_evidence|undeclared|not declare"):
        release.verify_derived_wheel(provenance, crypto_wheel, source_lock, openssl_license,
                                     "macos_x86_64", derived_build_evidence=evidence)


def test_symlink_or_directory_evidence_rejected(tmp_path: pathlib.Path) -> None:
    wheelhouse, crypto_wheel, requirements, source_lock, provenance, _sha_ = make_derived_crypto_inputs(tmp_path)
    openssl_license = tmp_path / "OpenSSL-4.0.2-LICENSE.txt"
    evidence = make_declared_build_evidence(tmp_path, provenance)
    link = tmp_path / "linked-build-evidence.json"
    link.symlink_to(evidence)
    with pytest.raises(release.ReleaseError, match="regular file|symlink"):
        release.verify_derived_wheel(provenance, crypto_wheel, source_lock, openssl_license,
                                     "macos_x86_64", derived_build_evidence=link)
    directory = tmp_path / "evidence-dir"
    directory.mkdir()
    with pytest.raises(release.ReleaseError):
        release.verify_derived_wheel(provenance, crypto_wheel, source_lock, openssl_license,
                                     "macos_x86_64", derived_build_evidence=directory)


@pytest.mark.parametrize("claim_shape", ["malformed-sha", "not-a-dict"])
def test_malformed_evidence_claim_rejected(tmp_path: pathlib.Path, claim_shape: str) -> None:
    wheelhouse, crypto_wheel, requirements, source_lock, provenance, _sha_ = make_derived_crypto_inputs(tmp_path)
    openssl_license = tmp_path / "OpenSSL-4.0.2-LICENSE.txt"
    evidence = make_declared_build_evidence(tmp_path, provenance, claim_shape=claim_shape)
    with pytest.raises(release.ReleaseError):
        release.verify_derived_wheel(provenance, crypto_wheel, source_lock, openssl_license,
                                     "macos_x86_64", derived_build_evidence=evidence)


# ---------------------------------------------------------------- legacy honesty

def test_legacy_provenance_without_block_records_unverified(tmp_path: pathlib.Path) -> None:
    wheelhouse, crypto_wheel, requirements, source_lock, provenance, _sha_ = make_derived_crypto_inputs(tmp_path)
    openssl_license = tmp_path / "OpenSSL-4.0.2-LICENSE.txt"
    out = tmp_path / "requirements-compose-check.lock"
    receipt = release.compose_derived_wheel_lock(requirements, crypto_wheel, source_lock, provenance,
                                                 openssl_license, "macos_x86_64", out)
    assert receipt["derived_build_evidence"]["status"] == "NOT_RECORDED"
    assert receipt["derived_build_evidence"]["verification"] == "UNVERIFIED"
    flow = prepare_packaging(tmp_path, wheelhouse, crypto_wheel, requirements, source_lock,
                             provenance, openssl_license)
    block = flow["manifest"]["derived_wheel"]["build_evidence"]
    assert block["status"] == "NOT_RECORDED"
    assert block["verification"] == "UNVERIFIED"
    assert block.get("sha256") is None
    result, archive, evidence_dir = call_package(tmp_path, flow, wheelhouse, provenance, openssl_license)
    assert result["status"] == "OFFLINE_BUNDLE_PACKAGED_NATIVE_UNVERIFIED"
    with zipfile.ZipFile(archive) as zf:
        assert EVIDENCE_MEMBER not in zf.namelist()


def test_removed_claim_degrades_to_honest_legacy_not_verified(tmp_path: pathlib.Path) -> None:
    """Deleting the claim cannot fake verification: the marker must say UNVERIFIED,
    and supplying the evidence file afterwards is rejected as undeclared."""
    wheelhouse, crypto_wheel, requirements, source_lock, provenance, _sha_ = make_derived_crypto_inputs(tmp_path)
    openssl_license = tmp_path / "OpenSSL-4.0.2-LICENSE.txt"
    evidence = make_declared_build_evidence(tmp_path, provenance)
    declared_provenance_sha = _sha(provenance)
    payload = json.loads(provenance.read_text(encoding="utf-8"))
    del payload["build_evidence"]
    provenance.write_text(json.dumps(payload), encoding="utf-8")
    assert _sha(provenance) != declared_provenance_sha
    with pytest.raises(release.ReleaseError, match="no build_evidence|undeclared|not declare"):
        release.verify_derived_wheel(provenance, crypto_wheel, source_lock, openssl_license,
                                     "macos_x86_64", derived_build_evidence=evidence)
    flow = prepare_packaging(tmp_path, wheelhouse, crypto_wheel, requirements, source_lock,
                             provenance, openssl_license)
    block = flow["manifest"]["derived_wheel"]["build_evidence"]
    assert block["status"] == "NOT_RECORDED"
    assert block["verification"] == "UNVERIFIED"


# ---------------------------------------------------------------- tampering

def test_evidence_tamper_after_manifest_rejected_at_packaging(tmp_path: pathlib.Path) -> None:
    wheelhouse, crypto_wheel, requirements, source_lock, provenance, _sha_ = make_derived_crypto_inputs(tmp_path)
    openssl_license = tmp_path / "OpenSSL-4.0.2-LICENSE.txt"
    evidence = make_declared_build_evidence(tmp_path, provenance)
    flow = prepare_packaging(tmp_path, wheelhouse, crypto_wheel, requirements, source_lock,
                             provenance, openssl_license, evidence=evidence)
    # controlled replacement between manifest creation and packaging
    evidence.write_text('{"kind": "swapped-after-manifest"}\n', encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="changed since|build evidence|evidence"):
        call_package(tmp_path, flow, wheelhouse, provenance, openssl_license, evidence=evidence)
    assert not (tmp_path / "tamper-evidence" / "offline-bundle-macos_x86_64.json").exists(), \
        "no success receipt may be written for a swapped evidence file"


def test_packaging_read_window_byte_swap_rejected(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The hash index is computed before the zip write; swapping the evidence
    file inside that read window must be caught by the post-write member scan."""
    wheelhouse, crypto_wheel, requirements, source_lock, provenance, _sha_ = make_derived_crypto_inputs(tmp_path)
    openssl_license = tmp_path / "OpenSSL-4.0.2-LICENSE.txt"
    evidence = make_declared_build_evidence(tmp_path, provenance)
    flow = prepare_packaging(tmp_path, wheelhouse, crypto_wheel, requirements, source_lock,
                             provenance, openssl_license, evidence=evidence)
    original_write = zipfile.ZipFile.write

    def swapping_write(self, path, arcname=None, **kwargs):
        if arcname == EVIDENCE_MEMBER:
            evidence.write_bytes(b'{"kind": "swapped-inside-read-window"}\n')
        return original_write(self, path, arcname=arcname, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "write", swapping_write)
    with pytest.raises(release.ReleaseError, match="recursive scan failed|HASH_MISMATCH"):
        call_package(tmp_path, flow, wheelhouse, provenance, openssl_license,
                     evidence=evidence, name="read-window")
    assert not (tmp_path / "read-window-evidence" / "offline-bundle-macos_x86_64.json").exists(), \
        "no success receipt may be written when the packaged bytes differ from the hash index"
