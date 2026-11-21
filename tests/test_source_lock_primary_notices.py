"""Verifier-level synthetic controls for tools.source_lock_notices (T42/U10)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools import source_lock_notices as sln
from tests._t42_lock_notices_support import TARGET, build_fixture


@pytest.fixture
def fx(tmp_path):
    return build_fixture(tmp_path)


def call(fixture, paths, target=TARGET):
    return sln.lock_bound_notice_payloads(paths["receipt"], source_lock=paths["lock"], target=target)


def test_generic_unknown_packages_verified_against_lock(fx):
    fixture, paths = fx
    payloads, route = call(fixture, paths)
    assert set(payloads) == {"synth-alpha", "synth-beta"}
    alpha = payloads["synth-alpha"][0]
    beta = payloads["synth-beta"][0]
    # legitimate per-component licence differences, not one hard-coded example
    assert alpha["provenance"]["archive_member"].endswith("License.txt")
    assert beta["provenance"]["archive_member"].endswith("COPYING")
    assert alpha["license_bytes"] != beta["license_bytes"]
    for name, payload in (("synth-alpha", alpha), ("synth-beta", beta)):
        lock_row = fixture.packages[name]
        assert payload["version"] == lock_row["version"] != "12.2.2"
        assert payload["provenance"]["sdist_sha256"] == lock_row["sdist"]["hash"][7:]
        assert payload["provenance"]["sdist_bytes"] == lock_row["sdist"]["size"]
        assert payload["provenance"]["source_lock_sha256"] == hashlib.sha256(paths["lock"].read_bytes()).hexdigest()
        wheel_file = fixture.wheelhouse / payload["provenance"]["wheel_filename"]
        assert payload["wheel_sha256"] == hashlib.sha256(wheel_file.read_bytes()).hexdigest()
        assert payload["relative_path"].startswith("notices/" + name + "/")
        assert "materials" not in json.dumps(payload["provenance"])
    assert route["applied"] == ["synth-alpha", "synth-beta"]
    assert route["not_applicable_to_target"] == ["synth-delta"]
    assert route["declared_without_notices"] == ["synth-epsilon"]
    assert "synth-gamma" not in json.dumps(route)


def test_target_without_applicable_wheels_discloses_instead_of_fabricating(fx):
    fixture, paths = fx
    payloads, route = call(fixture, paths, target="win_amd64")
    assert payloads == {}
    assert route["not_applicable_to_target"] == ["synth-alpha", "synth-beta", "synth-delta", "synth-epsilon"]


@pytest.mark.parametrize("tamper", ["sdist", "notice-copy", "metadata-claim"])
def test_tampered_source_material_refused(fx, tamper):
    fixture, paths = fx
    if tamper == "sdist":
        fixture.sdist_path("synth-alpha").write_bytes(fixture.sdists["synth-alpha"][:-4] + b"XXXX")
    elif tamper == "notice-copy":
        (fixture.materials / "synth-beta" / "notice-0.txt").write_bytes(b"tampered notice copy\n")
    else:
        row = next(row for row in fixture.receipt_components if row["name"] == "synth-alpha")
        row["metadata_sha256"] = "0" * 64
        fixture.save_receipt()
    with pytest.raises(sln.SourceLockNoticeError):
        call(fixture, paths)


def test_forged_receipt_component_absent_from_lock_refused(fx):
    fixture, paths = fx
    forged = json.loads(json.dumps(fixture.receipt_components[0]))
    forged["name"], forged["version"] = "synth-ghost", "9.9.9"
    forged["notices"] = []
    fixture.receipt_components.append(forged)
    fixture.save_receipt()
    with pytest.raises(sln.SourceLockNoticeError, match="absent from the source lock"):
        call(fixture, paths)


@pytest.mark.parametrize("field", ["version", "wheel-sha", "wheel-name", "lock"])
def test_wrong_version_wrong_wheel_or_wrong_lock_refused(fx, field):
    fixture, paths = fx
    row = next(row for row in fixture.receipt_components if row["name"] == "synth-alpha")
    if field == "version":
        row["version"] = "7.1.1"
    elif field == "wheel-sha":
        row["wheels"][TARGET]["sha256"] = "0" * 64
    elif field == "wheel-name":
        row["wheels"][TARGET]["filename"] = "synth_gamma-3.3.3-py3-none-any.whl"
    else:
        del fixture.packages["synth-alpha"]
        fixture.save_lock()
    fixture.save_receipt()
    with pytest.raises(sln.SourceLockNoticeError):
        call(fixture, paths)


def test_stale_lock_claim_and_swapped_lock_refused(fx):
    fixture, paths = fx
    other = fixture.root / "other.lock"
    del fixture.packages["synth-beta"]
    fixture.save_lock(other)
    with pytest.raises(sln.SourceLockNoticeError, match="lock_sha256 claim is stale|absent from the source lock"):
        sln.lock_bound_notice_payloads(paths["receipt"], source_lock=other, target=TARGET)


def test_symlinked_source_archive_refused(fx):
    fixture, paths = fx
    path = fixture.sdist_path("synth-alpha")
    path.rename(fixture.root / "retained.tar.gz")
    path.symlink_to(fixture.root / "retained.tar.gz")
    with pytest.raises(sln.SourceLockNoticeError, match="left the source root through a link|linked|regular file"):
        call(fixture, paths)


@pytest.mark.parametrize("kind", ["escape", "abs", "dotdot-dir"])
def test_notice_or_component_path_escape_refused(fx, kind):
    fixture, paths = fx
    row = next(row for row in fixture.receipt_components if row["name"] == "synth-beta")
    outside = fixture.root / "outside.txt"
    outside.write_bytes(fixture.licenses["synth-beta"])
    if kind == "escape":
        row["notices"][0]["file"] = "../outside.txt"
    elif kind == "abs":
        row["notices"][0]["file"] = str(outside)
    else:
        row["source_archive"] = "../materials/synth-beta/source.tar.gz"
    fixture.save_receipt()
    with pytest.raises(sln.SourceLockNoticeError, match="safe relative path|left the source root"):
        call(fixture, paths)


@pytest.mark.parametrize("kind", ["traversal", "duplicate", "symlink-member", "hardlink-member"])
def test_lock_bound_archive_traversal_duplicate_and_links_refused(fx, kind):
    fixture, paths = fx
    raw, member = None, None
    from tests._t42_lock_notices_support import sdist_bytes
    raw, member = sdist_bytes("synth-beta", "2.0.0", "COPYING", fixture.licenses["synth-beta"], [kind])
    # lock, metadata and receipt are all moved to the crafted archive, so only
    # the structural membership rule can stop it
    fixture.modify_sdist("synth-beta", raw, sync_receipt_and_lock=True)
    with pytest.raises(sln.SourceLockNoticeError, match="traversal/link/membership"):
        call(fixture, paths)


def test_notice_copy_matching_archive_but_wrong_claim_refused(fx):
    fixture, paths = fx
    row = next(row for row in fixture.receipt_components if row["name"] == "synth-alpha")
    row["notices"][0]["bytes"] = 999
    fixture.save_receipt()
    with pytest.raises(sln.SourceLockNoticeError, match="extracted notice bytes differ"):
        call(fixture, paths)
