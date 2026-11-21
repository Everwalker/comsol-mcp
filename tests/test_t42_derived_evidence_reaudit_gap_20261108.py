"""T42 follow-up (2026-11-08): derived build evidence must stay bound after the re-audit.

`package_wheelhouse_bundle` re-audits the wheelhouse through a fresh
`create_bundle_manifest` before packaging; that re-audit verifies the
caller-supplied derived build evidence file against the provenance claim.
But after the re-audit returns, the function rebuilds the bundle records by
re-hashing the file paths and then writes the ZIP from those same paths. A
byte swap landing in the window between the re-audit's verification and the
record hashing made the records, the ZIP member, and the post-write recursive
scan all agree with each other on the new bytes, while nothing ever compared
the records row or the final ZIP member against
`verified["derived_wheel"]["build_evidence"]["sha256"]` that had just been
attested. The success receipt was written for a bundle whose embedded
`evidence/derived-wheel-build-evidence.json` contradicted the provenance and
PACKAGE_MANIFEST claims inside the same archive.

The tests below are synthetic (pytest tmp dirs, fake wheels) through the real
package API with a real monkeypatch of the in-function re-audit call: the swap
happens immediately after the re-audit `create_bundle_manifest` returns — not
before the manifest and not inside the already-covered `ZipFile.write` window.

Also pinned here: an explicit `"build_evidence": null` key in the provenance
is a declared-but-malformed claim (declared claims must be objects), not a
silent downgrade to the legacy NOT_RECORDED path.

No network, COMSOL, Worker, SSH, or Intel execution.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import zipfile

import pytest

from tests.test_full_project_release import (
    TOOL, make_derived_crypto_inputs, release,
)
from tests.test_t42_derived_build_evidence_20261108 import (
    EVIDENCE_MEMBER, call_package, make_declared_build_evidence, prepare_packaging,
)


def _sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_evidence_swap_between_reaudit_and_records_rejected(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Swap the evidence file right after the pre-packaging re-audit returns.

    The re-audit itself still sees the honest bytes, so every manifest-level
    comparison passes; the records loop and the ZIP then consume the swapped
    bytes. Packaging must fail instead of certifying an internally consistent
    but provenance-contradicting archive.
    """
    wheelhouse, crypto_wheel, requirements, source_lock, provenance, _sha_ = make_derived_crypto_inputs(tmp_path)
    openssl_license = tmp_path / "OpenSSL-4.0.2-LICENSE.txt"
    evidence = make_declared_build_evidence(tmp_path, provenance)
    declared_sha = _sha(evidence)
    flow = prepare_packaging(tmp_path, wheelhouse, crypto_wheel, requirements, source_lock,
                             provenance, openssl_license, evidence=evidence)
    real_create = release.create_bundle_manifest
    swap_events = []

    def racy_create(*args, **kwargs):
        result = real_create(*args, **kwargs)
        # package_wheelhouse_bundle calls create_bundle_manifest exactly once
        # (the re-audit). Swap immediately after that verification completes,
        # before the caller builds the records list from the same paths.
        if not swap_events:
            evidence.write_bytes(b'{"kind": "swapped-after-reaudit-before-records"}\n')
            swap_events.append(_sha(evidence))
        return result

    monkeypatch.setattr(release, "create_bundle_manifest", racy_create)
    with pytest.raises(release.ReleaseError, match="build evidence|evidence"):
        call_package(tmp_path, flow, wheelhouse, provenance, openssl_license,
                     evidence=evidence, name="reaudit-gap")
    assert len(swap_events) == 1, "the re-audit interception must have fired exactly once"
    assert swap_events[0] != declared_sha
    receipt = tmp_path / "reaudit-gap-evidence" / "offline-bundle-macos_x86_64.json"
    assert not receipt.exists(), "no success receipt may be written for swapped evidence bytes"
    assert not (tmp_path / "reaudit-gap.zip").exists(), \
        "the rejected bundle must not even be written"


def test_positive_bundle_after_clean_reaudit_still_embeds_verified_evidence(
    tmp_path: pathlib.Path,
) -> None:
    """Real positive path stays intact: honest bytes package and tie three-way."""
    wheelhouse, crypto_wheel, requirements, source_lock, provenance, _sha_ = make_derived_crypto_inputs(tmp_path)
    openssl_license = tmp_path / "OpenSSL-4.0.2-LICENSE.txt"
    evidence = make_declared_build_evidence(tmp_path, provenance)
    flow = prepare_packaging(tmp_path, wheelhouse, crypto_wheel, requirements, source_lock,
                             provenance, openssl_license, evidence=evidence)
    result, archive, _evidence_dir = call_package(tmp_path, flow, wheelhouse, provenance,
                                                  openssl_license, evidence=evidence,
                                                  name="clean-positive")
    assert result["status"] == "OFFLINE_BUNDLE_PACKAGED_NATIVE_UNVERIFIED"
    with zipfile.ZipFile(archive) as zf:
        embedded = zf.read(EVIDENCE_MEMBER)
        indexed = json.loads(zf.read("OFFLINE_BUNDLE_MANIFEST.json"))
        rows = {row["path"]: row for row in indexed["files_excluding_this_manifest"]}
        package_manifest = json.loads(zf.read("PACKAGE_MANIFEST.json"))
    claim = package_manifest["derived_wheel"]["build_evidence"]
    assert claim["verification"] == "VERIFIED"
    assert hashlib.sha256(embedded).hexdigest() == _sha(evidence) == \
        claim["sha256"] == claim["declared_sha256"] == rows[EVIDENCE_MEMBER]["sha256"]


@pytest.mark.parametrize("call_site", ["verify", "manifest", "package"])
def test_explicit_null_build_evidence_claim_rejected(
    tmp_path: pathlib.Path, call_site: str
) -> None:
    """A declared `build_evidence` key must be an object; null is malformed."""
    wheelhouse, crypto_wheel, requirements, source_lock, provenance, _sha_ = make_derived_crypto_inputs(tmp_path)
    openssl_license = tmp_path / "OpenSSL-4.0.2-LICENSE.txt"
    evidence = make_declared_build_evidence(tmp_path, provenance)
    payload = json.loads(provenance.read_text(encoding="utf-8"))
    payload["build_evidence"] = None
    provenance.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    if call_site == "verify":
        with pytest.raises(release.ReleaseError, match="must be an object"):
            release.verify_derived_wheel(provenance, crypto_wheel, source_lock, openssl_license,
                                         "macos_x86_64")
    elif call_site == "manifest":
        with pytest.raises(release.ReleaseError, match="must be an object"):
            release.create_bundle_manifest(wheelhouse, "macos_x86_64", requirements, source_lock,
                                           tmp_path / "metadata", provenance, openssl_license)
        assert not (tmp_path / "metadata" / "PACKAGE_MANIFEST.json").exists()
    else:
        # Packaging re-audits through the same helper, so the null claim must not
        # degrade silently into the legacy NOT_RECORDED path there either.
        with pytest.raises(release.ReleaseError, match="must be an object"):
            flow = prepare_packaging(tmp_path, wheelhouse, crypto_wheel, requirements,
                                     source_lock, provenance, openssl_license)
        out = tmp_path / "null-claim-metadata"
        with pytest.raises(release.ReleaseError, match="must be an object"):
            release.create_bundle_manifest(wheelhouse, "macos_x86_64", requirements, source_lock,
                                           out, provenance, openssl_license)
        assert not (out / "PACKAGE_MANIFEST.json").exists()
