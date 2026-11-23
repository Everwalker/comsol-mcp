"""Additional synthetic T10 cases using the real SQLite project authority."""
import pytest  # pyright: ignore[reportMissingImports]

from comsol_mcp._project_authority import ProjectAuthority
from tests.test_t10_legacy_binding_store_20261123 import scenario


@pytest.mark.parametrize("old_present,new_project,expected", [
    (False, None, None), (False, "project-a", "project-a"),
    (True, "project-a", "project-a"),
])
def test_other_legacy_binding_cases(tmp_path, old_present, new_project, expected):
    store, backend, old_key, new_key, calls = scenario(tmp_path, new_project)
    try:
        if not old_present:
            store.db.execute("DELETE FROM revisions WHERE model_key=?", (old_key,))
        backend.persist()
        result = store.get_metadata("revisions", new_key)
        assert result.get("project_id") == expected
        assert calls == ([] if expected is None else [(expected, "project_write")])
    finally:
        backend.close()
        store.close()


@pytest.mark.parametrize("allowed", [True, False])
def test_legacy_binding_uses_real_project_authority(tmp_path, allowed):
    store, backend, old_key, new_key, _ = scenario(tmp_path)
    try:
        grants = {"inspect", "project_write"}
        authority = ProjectAuthority(store, workspace_root=tmp_path,
                                     permission_provider=lambda: grants,
                                     grant_ceiling={"inspect", "project_write"})
        result = authority.dispatch("project.create", {"label": "synthetic", "workspace": "project",
                                    "policy": {"permissions": ["inspect", "project_write"]}})
        project_id = result["data"]["project"]["project_id"]
        legacy = store.get_metadata("revisions", old_key)
        legacy["project_id"] = project_id
        store.put_metadata("revisions", old_key, legacy)
        backend.project_authorizer = authority.authorize_operation
        rows = (("sessions", "legacy-session"), ("runtimes", "legacy-runtime"),
                ("revisions", old_key), ("revisions", new_key))
        before = [store.get_metadata(table, ident) for table, ident in rows]
        if allowed:
            backend.persist()
            assert store.get_metadata("revisions", new_key)["project_id"] == project_id
        else:
            grants.remove("project_write")
            with pytest.raises(Exception) as exc:
                backend.persist()
            assert getattr(exc.value, "code", None) == "PERMISSION_DENIED"
            assert [store.get_metadata(table, ident) for table, ident in rows] == before
            assert not store.db.in_transaction
    finally:
        backend.close()
        store.close()
