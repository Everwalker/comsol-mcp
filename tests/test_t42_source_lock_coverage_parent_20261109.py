"""T42 lock-notice v2 candidate: generic coverage and multi-notice contracts.

Fixtures are synthetic, offline, task-owned and not legal or native evidence.
"""
from __future__ import annotations

import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

from tools import full_project_release as release
from tools import source_lock_notices as notices
from tests._t42_lock_notices_support import TARGET, build_fixture, sha
from tests.test_t42_lock_notices_release_consumption_20261109 import make_manifest, packaging_args


@pytest.fixture
def fx(tmp_path):
    fixture, paths = build_fixture(tmp_path)
    return fixture, paths, tmp_path


def _add_second_notice(fixture):
    component = 'synth-alpha'
    first_member = fixture.members[component]
    second_member = str(Path(first_member).parent / 'NOTICE.txt')
    first = fixture.licenses[component]
    second = b'Another synthetic notice; independently different bytes.\n'
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w:gz') as archive:
        for member, payload in ((first_member, first), (second_member, second)):
            info = tarfile.TarInfo(member)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    fixture.modify_sdist(component, out.getvalue(), sync_receipt_and_lock=True)
    (fixture.materials / component / 'notice-1.txt').write_bytes(second)
    row = next(row for row in fixture.receipt_components if row['name'] == component)
    row['notices'].append({'archive_member': second_member, 'file': component + '/notice-1.txt',
                           'sha256': sha(second), 'bytes': len(second)})
    fixture.save_receipt()
    return first, second


def _strip_wheel_license_and_repin(fixture, component):
    row = next(row for row in fixture.receipt_components if row['name'] == component)
    filename = row['wheels'][TARGET]['filename']
    path = fixture.wheelhouse / filename
    original = path.read_bytes()
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as before, zipfile.ZipFile(output, 'w') as after:
        for item in before.infolist():
            if '.dist-info/licenses/' not in item.filename.lower():
                after.writestr(item, before.read(item.filename))
    data = output.getvalue()
    path.write_bytes(data)
    digest = sha(data)
    fixture.wheels[filename] = (digest, len(data))
    fixture.packages[component]['wheels'][0]['hash'] = 'sha256:' + digest
    fixture.packages[component]['wheels'][0]['size'] = len(data)
    row['wheels'][TARGET]['sha256'] = digest
    fixture.save_lock()
    fixture.save_receipt()
    fixture.save_requirements()


def test_two_distinct_notices_one_component_are_packaged_without_clobber(fx):
    fixture, paths, root = fx
    first, second = _add_second_notice(fixture)
    manifest, metadata = make_manifest(fixture, paths, root, primary_notice_receipt=paths['receipt'])
    alpha = [item for item in manifest['primary_notices'] if item['provenance']['name'] == 'synth-alpha']
    assert len(alpha) == 2
    by_name = {Path(item['path']).name: (metadata / item['path']).read_bytes() for item in alpha}
    assert by_name == {'License.txt': first, 'NOTICE.txt': second}
    licenses = json.loads((metadata / 'LICENSES.json').read_text())
    component = next(row for row in licenses['components'] if row['name'] == 'synth-alpha')
    assert len(component['primary_source_notices']) == 2


def test_receipt_absent_target_for_present_wheel_is_refused(fx):
    fixture, paths, root = fx
    row = next(row for row in fixture.receipt_components if row['name'] == 'synth-alpha')
    assert (fixture.wheelhouse / row['wheels'][TARGET]['filename']).is_file()
    del row['wheels'][TARGET]
    fixture.save_receipt()
    with pytest.raises(release.ReleaseError, match='(?i)(applicab|target.*claim|claim.*target)'):
        make_manifest(fixture, paths, root, primary_notice_receipt=paths['receipt'])
    assert not (root / 'metadata' / 'PACKAGE_MANIFEST.json').exists()


@pytest.mark.parametrize('omission', ['component-absent', 'declared-empty'])
def test_wheel_without_license_cannot_be_silently_uncovered_by_receipt(fx, omission):
    fixture, paths, root = fx
    _strip_wheel_license_and_repin(fixture, 'synth-alpha')
    if omission == 'component-absent':
        fixture.receipt_components = [row for row in fixture.receipt_components if row['name'] != 'synth-alpha']
    else:
        next(row for row in fixture.receipt_components if row['name'] == 'synth-alpha')['notices'] = []
    fixture.save_receipt()
    with pytest.raises(release.ReleaseError, match='(?i)(uncovered|without.*notice|missing.*license)'):
        make_manifest(fixture, paths, root, primary_notice_receipt=paths['receipt'])
    assert not (root / 'metadata' / 'PACKAGE_MANIFEST.json').exists()


def test_duplicate_component_claim_is_rejected_before_overwrite(fx):
    fixture, paths, _root = fx
    fixture.receipt_components.append(json.loads(json.dumps(fixture.receipt_components[0])))
    fixture.save_receipt()
    with pytest.raises(notices.SourceLockNoticeError, match='(?i)duplicate.*component'):
        notices.lock_bound_notice_payloads(paths['receipt'], source_lock=paths['lock'], target=TARGET)


def test_real_absent_target_and_wheel_licensed_without_receipt_notice_still_disclosed(fx):
    fixture, paths, root = fx
    manifest, _metadata = make_manifest(fixture, paths, root, primary_notice_receipt=paths['receipt'])
    route = manifest['primary_notice_route']
    assert 'synth-delta' in route['not_applicable_to_target']
    assert 'synth-epsilon' in route['declared_without_notices']
    assert any(item['name'] == 'synth-epsilon' and item['license_files'] for item in manifest['wheels'])


def test_preexisting_notice_root_remains_no_clobber(fx):
    fixture, paths, root = fx
    existing = root / 'metadata' / 'notices'
    existing.mkdir(parents=True)
    sentinel = existing / 'keep.txt'
    sentinel.write_text('do not change')
    with pytest.raises(release.ReleaseError, match='no-clobber'):
        make_manifest(fixture, paths, root, primary_notice_receipt=paths['receipt'])
    assert sentinel.read_text() == 'do not change'


def test_package_reaudit_rejects_post_manifest_target_claim_omission(fx):
    fixture, paths, root = fx
    make_manifest(fixture, paths, root, primary_notice_receipt=paths['receipt'])
    row = next(row for row in fixture.receipt_components if row['name'] == 'synth-alpha')
    del row['wheels'][TARGET]
    fixture.save_receipt()
    with pytest.raises(release.ReleaseError):
        release.package_wheelhouse_bundle(*packaging_args(fixture, paths, root),
                                          primary_notice_receipt=paths['receipt'])
    assert not (root / 'offline.zip').exists()
