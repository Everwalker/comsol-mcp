"""Scoped report publisher tests; all stores, paths and authorities are new."""
import pytest  # pyright: ignore[reportMissingImports]

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._g3_report_publish import publish_project_report
from tests.test_t30_managed_report_20261123 import project_lane  # noqa: F401


def test_project_report_publisher_writes_both_formats(project_lane):
    backend, authority, project, root, _ = project_lane
    with backend.project_root_scope(authority.bind_project_workspace(project)):
        result = publish_project_report(backend.project_root_binding, authority.authorize_operation,
                                        project["project_id"], str(root / "report.json"), False,
                                        '{"report": true}', '# Report\n')
    assert (root / 'report.json').read_text() == '{"report": true}'
    assert (root / 'report.md').read_text() == '# Report\n'
    assert len(result['published']) == 2


def test_project_report_publisher_rejects_outside_and_denied(project_lane):
    backend, authority, project, root, grants = project_lane
    with backend.project_root_scope(authority.bind_project_workspace(project)):
        with pytest.raises(ExecutionContractError):
            publish_project_report(backend.project_root_binding, authority.authorize_operation,
                                   project["project_id"], str(root.parent / "outside.json"), False,
                                   '{}', '# Report\n')
        assert not (root.parent / 'outside.json').exists()
        grants.remove('project_write')
        with pytest.raises(ExecutionContractError) as exc:
            publish_project_report(backend.project_root_binding, authority.authorize_operation,
                                   project["project_id"], str(root / "denied.json"), False,
                                   '{}', '# Report\n')
        assert exc.value.code == 'PERMISSION_DENIED'
        assert not (root / 'denied.json').exists()


def test_second_file_revocation_keeps_first_and_reports_partial(project_lane, monkeypatch):
    from comsol_mcp import _g3_report_publish
    backend, authority, project, root, grants = project_lane
    real_publish = _g3_report_publish.publish_parameter_table_bytes_no_clobber
    published = []

    def publish_then_revoke(*args, **kwargs):
        receipt = real_publish(*args, **kwargs)
        published.append(args[1])
        grants.remove('project_write')
        return receipt

    monkeypatch.setattr(_g3_report_publish, 'publish_parameter_table_bytes_no_clobber', publish_then_revoke)
    with backend.project_root_scope(authority.bind_project_workspace(project)):
        with pytest.raises(ExecutionContractError) as exc:
            publish_project_report(backend.project_root_binding, authority.authorize_operation,
                                   project['project_id'], str(root / 'partial.json'), False,
                                   '{}', '# Report\n')
    assert exc.value.code == 'ARTIFACT_PUBLISH_UNKNOWN'
    assert exc.value.details['cause_code'] == 'PERMISSION_DENIED'
    assert [item['path'] for item in exc.value.details['published']] == ['partial.json']
    assert published == ['partial.json']
    assert (root / 'partial.json').read_text() == '{}'
    assert not (root / 'partial.md').exists()


def test_project_report_publisher_never_overwrites_existing_file(project_lane):
    backend, authority, project, root, _ = project_lane
    (root / 'existing.json').write_text('preserve')
    with backend.project_root_scope(authority.bind_project_workspace(project)):
        with pytest.raises(ExecutionContractError):
            publish_project_report(backend.project_root_binding, authority.authorize_operation,
                                   project['project_id'], str(root / 'existing.json'), False,
                                   '{}', '# Report\n')
    assert (root / 'existing.json').read_text() == 'preserve'
    assert not (root / 'existing.md').exists()


def test_project_root_swap_between_authority_and_publish_is_refused(project_lane, monkeypatch):
    from comsol_mcp import _g3_report_publish
    backend, authority, project, root, _ = project_lane
    real_publish = _g3_report_publish.publish_parameter_table_bytes_no_clobber
    displaced = root.parent / 'displaced-original-root'

    def replace_root_then_publish(*args, **kwargs):
        root.rename(displaced)
        root.mkdir()
        return real_publish(*args, **kwargs)

    monkeypatch.setattr(_g3_report_publish, 'publish_parameter_table_bytes_no_clobber', replace_root_then_publish)
    with backend.project_root_scope(authority.bind_project_workspace(project)):
        with pytest.raises(ExecutionContractError) as exc:
            publish_project_report(backend.project_root_binding, authority.authorize_operation,
                                   project['project_id'], str(root / 'swap.json'), False,
                                   '{}', '# Report\n')
    assert exc.value.code == 'PROJECT_WORKSPACE_IDENTITY_MISMATCH'
    assert displaced.is_dir()
    assert not (root / 'swap.json').exists()
    assert not (root / 'swap.md').exists()
