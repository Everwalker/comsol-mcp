"""Project-authorized publication of caller-supplied validation reports."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ._artifact_store import ArtifactStore, publish_parameter_table_bytes_no_clobber
from ._execution_contract import ExecutionContractError
from ._project_root import ProjectRootBinding, close_binding_releasing, open_project_root_binding


def publish_project_report(binding: ProjectRootBinding | None, authorizer: Callable[..., Any] | None, project_id: str,
                           destination: str, overwrite: bool, report_json: str, report_md: str) -> dict[str, Any]:
    """Publish to the admitted project; each format is an atomic no-clobber write.

    The two files are not one filesystem transaction. If a later publication
    fails, the earlier receipt is retained in the reported uncertain outcome.
    """
    if not isinstance(binding, ProjectRootBinding) or binding._closed or not callable(authorizer):
        raise ExecutionContractError("MANAGED_CONTEXT_REQUIRED", "report publication requires a live project binding",
                                     stage="validation")
    if not isinstance(project_id, str) or not project_id:
        raise ExecutionContractError("PROJECT_IDENTITY_REQUIRED", "report publication requires a project identity",
                                     stage="validation")
    if not isinstance(destination, str) or not destination or "\x00" in destination:
        raise ExecutionContractError("INVALID_DESTINATION", "report destination must be a nonempty path", stage="validation")
    if type(overwrite) is not bool:
        raise ExecutionContractError("INVALID_REQUEST", "report overwrite must be boolean", stage="validation")
    if overwrite:
        raise ExecutionContractError("UNSUPPORTED_OPERATION", "managed report overwrite is not yet supported",
                                     stage="validation")
    raw = Path(destination)
    if ".." in raw.parts:
        raise ExecutionContractError("ACCESS_VIOLATION", "report destination cannot contain parent traversal",
                                     stage="validation")
    root = binding.path

    def check_authority() -> None:
        try:
            record = authorizer(project_id, "project_write")
        except ExecutionContractError as exc:
            raise ExecutionContractError(exc.code, str(exc), stage="validation", details=exc.details) from exc
        if not isinstance(record, dict) or record.get("project_id") != project_id or record.get("workspace") != str(root):
            raise ExecutionContractError("PROJECT_IDENTITY_MISMATCH", "report project workspace differs from the admitted root",
                                         stage="validation")
        observed = open_project_root_binding(root)
        primary = None
        try:
            if observed.identity != binding.identity or binding._closed:
                raise ExecutionContractError("PROJECT_WORKSPACE_IDENTITY_MISMATCH", "report project root identity changed",
                                             stage="validation")
        except BaseException as exc:
            primary = exc
            raise
        finally:
            close_binding_releasing(observed, primary)

    check_authority()
    store = ArtifactStore(root)
    target = store.resolve_safe_path(destination, allow_overwrite=False)
    target_relative = target.relative_to(root).as_posix()
    suffix = target.suffix.lower()
    if suffix == ".json":
        files = [(target_relative, report_json.encode("utf-8")),
                 (target.with_suffix(".md").relative_to(root).as_posix(), report_md.encode("utf-8"))]
    elif suffix == ".md":
        files = [(target_relative, report_md.encode("utf-8")),
                 (target.with_suffix(".json").relative_to(root).as_posix(), report_json.encode("utf-8"))]
    else:
        files = [(target_relative, report_md.encode("utf-8"))]
    for relative, _ in files:
        store.resolve_safe_path(relative, allow_overwrite=False)
    published = []
    for relative, payload in files:
        try:
            check_authority()
            receipt = publish_parameter_table_bytes_no_clobber(
                root, relative, payload, expected_project_identity=binding.identity,
            )
            published.append({"path": relative, "receipt": receipt})
        except Exception as exc:
            if not published:
                raise
            raise ExecutionContractError("ARTIFACT_PUBLISH_UNKNOWN", "report publication stopped after a prior file was published",
                                         stage="post_dispatch", details={"published": published, "cause_type": type(exc).__name__,
                                                                          "cause_code": getattr(exc, "code", None)}) from exc
    return {"project_id": project_id, "published": published, "atomicity": "PER_FILE_NOT_PAIR"}
