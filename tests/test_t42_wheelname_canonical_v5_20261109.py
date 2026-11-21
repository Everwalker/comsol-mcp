"""T42 v5 controls: canonical-name wheel identity for the source-lock notice route.

Contract basis (parent adjudication of the v4 parent counterexamples):
the distribution field taken from the structured wheel-filename decomposition
and the locked distribution name are compared only through the packaging
library's existing canonicalize_name, combined with the release entry point's
existing _wheel_dist_version decomposition semantics. No enumerated spelling
allowlist: current normalized, legacy literal ('.'/uppercase) and mixed
separator/case spellings all match because they canonicalize identically,
while malformed shapes (extra '-'-separated segments, missing or empty tag
fields, bad suffix, bad build tag, '__' in the distribution field) stay
refused through packaging's own wheel-filename grammar. Version identity
against the lock/receipt version string stays exact, as in the release route's
pin/metadata comparisons.

Synthetic fixtures only: no acquisition, no install, no bundle build, no
native or legal certification.
"""
from __future__ import annotations

import pathlib

import pytest
from packaging.utils import canonicalize_name

from tests.test_t42_wheelname_compat_sub_20261109 import (
    CompatFixture,
    make_compat_wheel,
    one_package_fixture,
)
from tools import full_project_release as release
from tools import source_lock_notices as sln

TARGET = "macos_arm64"

# --- Helper level: canonical equivalence, generalized beyond the parent cases ---

HELPER_ACCEPT = [
    # The three parent counterexamples (T42-source-lock-v4-parent-canonical-
    # counterexamples-20261109.txt) as positive controls.
    ("syn-theta", "7.0.3", "syn.theta-7.0.3-py3-none-any.whl"),
    ("syn.alpha-beta", "7.0.3", "Syn.Alpha_Beta-7.0.3-py3-none-any.whl"),
    ("syn_theta", "7.0.3", "syn..theta-7.0.3-py3-none-any.whl"),
    # v4 already accepted these; they must stay accepted.
    ("Syn.theta", "7.0.3", "syn_theta-7.0.3-py3-none-any.whl"),
    ("syn.theta", "7.0.3", "Syn_Theta-7.0.3-py3-none-any.whl"),
    # Further mixed spellings not pinned by any earlier version.
    ("Syn--Theta", "5.0.0", "syn_theta-5.0.0-py3-none-any.whl"),
    ("syn-_-_theta", "7.0.3", "syn_theta-7.0.3-py3-none-any.whl"),
    ("syn-theta", "7.0.3", "SYN.Theta-7.0.3-py3-none-any.whl"),
    ("Zope.Interface", "5.0.0", "zope.interface-5.0.0-cp312-cp312-win_amd64.whl"),
    # Build-tag form (6 fields) per the release decomposition semantics.
    ("syn.theta", "7.0.3", "syn.theta-7.0.3-1-py3-none-any.whl"),
]

HELPER_REFUSE = [
    # Different canonical name.
    ("syn-theta", "7.0.3", "syn_thta-7.0.3-py3-none-any.whl"),
    ("syn-theta", "7.0.3", "syn.theta-extra-7.0.3-py3-none-any.whl"),
    # Wrong version in the filename even though the name matches.
    ("syn.theta", "7.0.3", "syn.theta-9.9.9-py3-none-any.whl"),
    ("syn.theta", "7.0.3", "Syn.Theta-7.0.4-py3-none-any.whl"),
    # Extra '-'-separated segments beyond the grammar.
    ("syn.theta", "7.0.3", "syn.theta-7.0.3-py3-none-any-extra.whl"),
    ("syn.theta", "7.0.3", "syn.theta-7.0.3-1-py3-none-any-extra.whl"),
    # Missing or partial tag fields.
    ("syn.theta", "7.0.3", "syn.theta-7.0.3.whl"),
    ("syn.theta", "7.0.3", "syn.theta-7.0.3-py3.whl"),
    ("syn.theta", "7.0.3", "syn.theta-7.0.3-py3-none.whl"),
    # Suffix anomalies.
    ("syn.theta", "7.0.3", "syn.theta-7.0.3-py3-none-any.whlz"),
    ("syn.theta", "7.0.3", "syn.theta-7.0.3-py3-none-any.WHLX"),
    # Malformed distribution field.
    ("syn.theta", "7.0.3", "-7.0.3-py3-none-any.whl"),
    ("syn-theta", "7.0.3", "syn__theta-7.0.3-py3-none-any.whl"),
    # Empty or non-identifier tag components.
    ("syn.theta", "7.0.3", "syn.theta-7.0.3--none-any.whl"),
    ("syn.theta", "7.0.3", "syn.theta-7.0.3-!!!-???-@@@.whl"),
    # Invalid build tag in the optional 6-field position.
    ("syn.theta", "7.0.3", "syn.theta-7.0.3-py3x-py3-none-any.whl"),
]


@pytest.mark.parametrize(("name", "version", "filename"), HELPER_ACCEPT)
def test_canonically_equivalent_wheel_names_match_identity(name, version, filename):
    assert sln._wheel_filename_matches_identity(filename, name, version) is True


@pytest.mark.parametrize(("name", "version", "filename"), HELPER_REFUSE)
def test_non_equivalent_or_malformed_wheel_names_refused(name, version, filename):
    assert sln._wheel_filename_matches_identity(filename, name, version) is False


@pytest.mark.parametrize(("name", "version", "filename"), HELPER_ACCEPT)
def test_helper_acceptance_consistent_with_release_decomposition(name, version, filename):
    """Every accepted spelling parses identically at the release entry point."""
    dist, parsed_version, _tags = release._wheel_dist_version(pathlib.Path(filename))
    assert dist == canonicalize_name(name)
    assert parsed_version == version


@pytest.mark.parametrize(("name", "version", "filename"), [
    ("syn.theta", "7.0.3", "syn.theta-7.0.3--none-any.whl"),
    ("syn.theta", "7.0.3", "syn.theta-7.0.3-py3-none-any-extra.whl"),
])
def test_helper_refusals_map_to_release_side_rejections(name, version, filename):
    """Resolve the helper/release boundary: shapes the helper refuses are also
    refused by the release route (parse error, or decomposition tolerates them
    but the tag semantics layer refuses), so nothing is accepted anywhere."""
    assert sln._wheel_filename_matches_identity(filename, name, version) is False
    try:
        release._wheel_dist_version(pathlib.Path(filename))
        parsed_ok = True
    except release.ReleaseError:
        parsed_ok = False
    if parsed_ok:
        assert release.wheel_target_check(filename, TARGET) is False


# --- Route level: lock_bound_notice_payloads consumes the generalized forms ---

ROUTE_ACCEPT = [
    ("syn-theta", "7.0.3", "syn.theta"),          # parent counterexample 1
    ("syn.alpha-beta", "7.0.3", "Syn.Alpha_Beta"),  # parent counterexample 2
    ("syn_theta", "7.0.3", "syn..theta"),         # parent counterexample 3
    ("syn-theta", "7.0.3", "SYN.Theta"),          # all-uppercase legacy form
]


@pytest.mark.parametrize(("name", "version", "dist_component"), ROUTE_ACCEPT)
def test_route_accepts_canonically_equivalent_legacy_wheels(tmp_path, name, version, dist_component):
    fixture, paths, filename, wheel_sha = one_package_fixture(tmp_path, name, version, dist_component)
    payloads, route = sln.lock_bound_notice_payloads(paths["receipt"], source_lock=paths["lock"],
                                                     target=TARGET)
    key = canonicalize_name(name)
    record = payloads[key][0]
    assert record["provenance"]["wheel_filename"] == filename
    assert record["provenance"]["wheel_sha256"] == wheel_sha
    assert wheel_sha == (lambda raw: __import__("hashlib").sha256(raw).hexdigest())(
        (fixture.wheelhouse / filename).read_bytes())
    assert route["kind"] == "SOURCE_LOCK_BOUND_PRIMARY_NOTICES"
    dist, parsed_version, _tags = release._wheel_dist_version(pathlib.Path(filename))
    assert dist == key and parsed_version == version


def _reforge_wheel_claim(fixture, paths, name, new_filename, new_version=None):
    """Rewrite lock + receipt to pin a different wheel filename for the package."""
    component = next(row for row in fixture.receipt_components if row["name"] == name)
    wheel_sha = make_compat_wheel(fixture.wheelhouse / new_filename, name,
                                  new_version or component["version"],
                                  new_filename.rsplit("-", 4)[0])
    entry = fixture.packages[name]["wheels"][0]
    entry["url"] = f"https://files.pythonhosted.org/synthetic/{new_filename}"
    entry["hash"] = "sha256:" + wheel_sha
    component["wheels"][TARGET] = {"filename": new_filename, "sha256": wheel_sha}
    fixture.save_lock()
    fixture.save_receipt()
    return wheel_sha


def test_route_refuses_forged_lock_with_filename_version_mismatch(tmp_path):
    fixture, paths, _filename, _sha = one_package_fixture(tmp_path, "syn.theta", "7.0.3", "syn.theta")
    _reforge_wheel_claim(fixture, paths, "syn.theta", "syn.theta-9.9.9-py3-none-any.whl", "9.9.9")
    with pytest.raises(sln.SourceLockNoticeError,
                       match=r"receipt version differs from the source lock version|"
                             r"disagrees with the component identity"):
        sln.lock_bound_notice_payloads(paths["receipt"], source_lock=paths["lock"], target=TARGET)


def test_route_refuses_forged_lock_with_extra_segment_filename(tmp_path):
    fixture, paths, _filename, _sha = one_package_fixture(tmp_path, "syn.theta", "7.0.3", "syn.theta")
    _reforge_wheel_claim(fixture, paths, "syn.theta", "syn.theta-7.0.3-py3-none-any-extra.whl")
    with pytest.raises(sln.SourceLockNoticeError,
                       match=r"disagrees with the component identity"):
        sln.lock_bound_notice_payloads(paths["receipt"], source_lock=paths["lock"], target=TARGET)


def test_route_refuses_hash_tamper_on_legacy_canonically_equivalent_wheel(tmp_path):
    fixture, paths, _filename, _sha = one_package_fixture(tmp_path, "syn.alpha-beta", "7.0.3",
                                                          "Syn.Alpha_Beta")
    component = next(row for row in fixture.receipt_components if row["name"] == "syn.alpha-beta")
    component["wheels"][TARGET]["sha256"] = "0" * 64
    fixture.save_receipt()
    with pytest.raises(sln.SourceLockNoticeError, match=r"not the lock-pinned wheel"):
        sln.lock_bound_notice_payloads(paths["receipt"], source_lock=paths["lock"], target=TARGET)


def test_route_refuses_ambiguous_duplicate_lock_rows_across_spellings(tmp_path):
    fixture = CompatFixture(tmp_path)
    fixture.add_compat_package("syn-theta", "7.0.3", "syn.theta")
    fixture.add_compat_package("syn.theta", "8.0.0", "syn_theta", receipt=False)
    paths = {"lock": fixture.save_lock(), "receipt": fixture.save_receipt(),
             "requirements": fixture.save_requirements_for([("syn-theta", "7.0.3")])}
    with pytest.raises(sln.SourceLockNoticeError, match=r"ambiguous duplicate package entry"):
        sln.lock_bound_notice_payloads(paths["receipt"], source_lock=paths["lock"], target=TARGET)


# --- End-to-end: create_bundle_manifest consumes a mixed legacy spelling ---

def test_mixed_legacy_wheel_consumed_end_to_end_by_release_manifest(tmp_path):
    fixture, paths, filename, wheel_sha = one_package_fixture(tmp_path, "syn-theta", "7.0.3",
                                                              "SYN.Theta")
    metadata = tmp_path / "metadata"
    manifest = release.create_bundle_manifest(fixture.wheelhouse, TARGET, paths["requirements"],
                                              paths["lock"], metadata,
                                              primary_notice_receipt=paths["receipt"])
    wheel_row = next(row for row in manifest["wheels"] if canonicalize_name(row["name"]) == "syn-theta")
    assert wheel_row["sha256"] == wheel_sha
    notice_rows = [row for row in manifest["primary_notices"] if "syn-theta" in row["path"]]
    assert notice_rows, manifest["primary_notices"]
    provenance = notice_rows[0]["provenance"]
    assert provenance["wheel_filename"] == filename
    assert provenance["wheel_sha256"] == wheel_row["sha256"] == wheel_sha
    assert manifest["primary_notice_route"]["kind"] == "SOURCE_LOCK_BOUND_PRIMARY_NOTICES"


def test_malformed_tag_wheel_refused_end_to_end(tmp_path):
    """A canonically-named wheel with a tag-anomalous filename is refused on the
    joint route (helper grammar and/or release tag semantics), never accepted."""
    fixture, paths, _filename, _sha = one_package_fixture(tmp_path, "syn.theta", "7.0.3", "syn.theta")
    bad = "syn.theta-7.0.3-!!!-???-@@@.whl"
    _reforge_wheel_claim(fixture, paths, "syn.theta", bad)
    requirements = fixture.save_requirements_for([("syn.theta", "7.0.3")])
    with pytest.raises((sln.SourceLockNoticeError, release.ReleaseError)):
        release.create_bundle_manifest(fixture.wheelhouse, TARGET, requirements,
                                       paths["lock"], tmp_path / "metadata",
                                       primary_notice_receipt=paths["receipt"])
