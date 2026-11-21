"""Pure job/operation project-binding check (T35, unintegrated helper).

This module decides whether a durable job's metadata and its linked
operation's metadata independently and unambiguously name the SAME nonempty
project ID. Inputs are ordinary Python mappings already decoded from the
durable rows; this helper decodes nothing itself.

Trusted project-ID sources per side are exactly two generic fields:

* the metadata top-level ``project_id`` (used by session.start / session.connect
  admissions), and
* ``project_id`` inside an ``execution`` mapping (used by ordinary/stage
  admissions; compaction can project it into a top-level claim).

If both representations are present on either side, every present value must
be a nonempty string and all present values on both sides must agree exactly
(no normalization). Each side must contribute at least one independently
present project ID; a compacted job with a top-level ID paired with an
operation that keeps a nested ID is accepted when they match.

Any of the following yields no binding (``None``): non-mapping metadata, a
present-but-non-mapping ``execution`` value, a present-but-malformed project
ID claim (non-string or empty), a side with no
independently present ID, or any disagreement between present IDs.
``arguments.project_id`` and ``project_root`` are never read: they can
neither create nor override a binding.

A consistent binding is a metadata agreement check only. It is not
authority: it does not identify a principal, authorize anything, and does
not prove immutability. The function is pure and side-effect-free — no
store, DB, daemon, network, or other external access, and no mutation of
its inputs. No project names, operations, or test sentinels are
special-cased; there is no field allowlist beyond the two generic fields
above.
"""

from __future__ import annotations

from collections.abc import Mapping

__all__ = ["consistent_project_id"]


def _usable_project_id(value: object) -> bool:
    """A usable project ID claim is a nonempty string; it is never normalized."""
    return isinstance(value, str) and bool(value)


def _project_id_claims(metadata: object) -> list[str] | None:
    """Collect the independently present project IDs named by one side.

    Returns ``None`` when the side is malformed (non-mapping metadata, a
    present-but-non-mapping ``execution`` value, or a present-but-malformed
    project ID claim). An empty list means the side names no project
    binding at all.
    """
    if not isinstance(metadata, Mapping):
        return None
    claims: list[str] = []
    if "project_id" in metadata:
        value = metadata["project_id"]
        if not _usable_project_id(value):
            return None
        claims.append(value)
    if "execution" in metadata:
        execution = metadata["execution"]
        if not isinstance(execution, Mapping):
            return None
        if "project_id" in execution:
            value = execution["project_id"]
            if not _usable_project_id(value):
                return None
            claims.append(value)
    return claims


def consistent_project_id(job_metadata: object, operation_metadata: object) -> str | None:
    """Return the shared project ID, or ``None`` when no unambiguous binding.

    A binding is returned only when BOTH sides independently name at least
    one project ID from the trusted fields and ALL present IDs on both sides
    are nonempty strings that agree exactly.
    """
    job_claims = _project_id_claims(job_metadata)
    operation_claims = _project_id_claims(operation_metadata)
    if job_claims is None or operation_claims is None:
        return None
    if not job_claims or not operation_claims:
        return None
    if len(set(job_claims) | set(operation_claims)) != 1:
        return None
    return job_claims[0]
