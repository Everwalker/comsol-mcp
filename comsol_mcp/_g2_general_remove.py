"""T13 candidate: consent-gated removal of an ordinary or nested model entity.

This is NOT the model-root NodeGroup structural ungroup (that published route
stays in :mod:`comsol_mcp._g2_group_remove` and keeps its own contract). This
adapter performs exactly one actual
``com.comsol.model.ModelEntityList.remove(java.lang.String)`` call against the
real container receiver of a typed target path, on the same generic Worker
``call`` channel the reviewed G3 domain removal lanes already use.

Candidate contract (software-fake evidence only; no native acceptance):

* Explicit closed consent. ``node.remove`` on a non-model-root path requires an
  ``authorization`` object that repeats the exact target path, uses
  ``scope="target_subtree"``, and acknowledges every open proof obligation:
  external reference impact, descendant placement semantics, and the
  candidate-vocabulary enumeration bound. Missing or malformed consent keeps
  the historical pre-write refusal byte-for-byte.
* Pre-write retention baseline. A whole-tree snapshot runs through the same
  candidate-vocabulary walk used by ``node.find``. Any probe/getter error,
  duplicate path, or exhausted page budget refuses the operation before the
  first mutation. An incomplete baseline can never be replaced by "we'll
  compare afterwards".
* Post-dispatch non-target retention check. A second walk compares every path
  outside the authorized subtree on presence AND on the row fields the
  candidate vocabulary actually observes (``tag``, ``type_id``, ``label``).
  A vanished path, a same-path row whose observed fields changed, or a new
  path appearing outside the authorized subtree is reported as
  EXECUTION_STATE_UNKNOWN with the pre/post snapshots in evidence — success
  is never returned next to such an observation. This is detection after the
  fact, not prevention, and never an atomic rollback. The retention block
  names its compared fields explicitly and keeps object identity, hidden
  properties and external references UNVERIFIED; equal paths plus equal
  compared fields never claim "the same object survived".
* Honest disclosure. COMSOL 6.4 exposes no public incoming-consumer getter
  (the API index has no ``getDependencies``; ``ModelEntityList.remove`` is
  documented only as "Removes an entity."), and per-family descendant
  semantics of ``remove(String)`` are not specified publicly. Both stay
  UNSCANNED, ``complete`` stays false, and no dependency-closure claim is made.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from ._execution_contract import ExecutionContractError, PreWriteRefusal
from ._g2_contract import NodePath
from ._g2_engine import _actual_tags, _call, _entity_parent, _model, _worker_failure_unknown, find_nodes

MODEL_ENTITY_LIST = "com.comsol.model.ModelEntityList"
JAVA_STRING = "java.lang.String"
SEMANTIC_OPERATION = "com.comsol.model.ModelEntityList.remove(String)"
REQUIRED_ACKNOWLEDGEMENTS = frozenset({
    "external_reference_impact_unverified",
    "descendant_scope_follows_engine_semantics",
    "enumeration_scope_candidate_vocabulary",
})
SNAPSHOT_PAGE_LIMIT = 500
SNAPSHOT_MAX_PAGES = 200
# The per-row metadata fields the candidate whole-tree walk actually observes
# (see _g2_engine.find_nodes rows). The retention proof compares exactly these
# fields and nothing else; anything beyond them stays UNVERIFIED by declaration.
RETENTION_COMPARED_FIELDS = ("tag", "type_id", "label")


def validate_consent(path_value: Any, authorization: Any) -> dict[str, Any]:
    """Pure consent validation: no receiver, Worker, or model access."""
    if not isinstance(authorization, Mapping) or set(authorization) != {"scope", "target_path", "acknowledge"}:
        raise PreWriteRefusal(
            "INVALID_REQUEST",
            "authorization must be an object with exactly scope, target_path and acknowledge",
            details={"receiver_accessed": False, "mutation_dispatched": False})
    if authorization["scope"] != "target_subtree":
        raise PreWriteRefusal(
            "INVALID_REQUEST", "authorization.scope must be exactly target_subtree",
            details={"receiver_accessed": False, "mutation_dispatched": False})
    acknowledge = authorization["acknowledge"]
    if (not isinstance(acknowledge, list) or any(type(item) is not str for item in acknowledge)
            or len(set(acknowledge)) != len(acknowledge)
            or set(acknowledge) != REQUIRED_ACKNOWLEDGEMENTS):
        raise PreWriteRefusal(
            "INVALID_REQUEST",
            "authorization.acknowledge must name each open proof obligation exactly once",
            details={"required_acknowledgements": sorted(REQUIRED_ACKNOWLEDGEMENTS),
                     "receiver_accessed": False, "mutation_dispatched": False})
    requested = NodePath.from_wire(path_value, allow_empty=False)
    target = NodePath.from_wire(authorization["target_path"], allow_empty=False)
    if target.as_dict() != requested.as_dict():
        raise PreWriteRefusal(
            "INVALID_REQUEST", "authorization.target_path must equal the node.remove path exactly",
            details={"receiver_accessed": False, "mutation_dispatched": False})
    return {"scope": "target_subtree", "target_path": requested.as_dict(),
            "acknowledge": sorted(acknowledge)}


def _row_key(path: Mapping[str, Any]) -> str:
    return json.dumps(list(path["segments"]), sort_keys=True, separators=(",", ":"))


def _under_prefix(row_segments: list, prefix: list) -> bool:
    return (len(row_segments) >= len(prefix)
            and all(row_segments[index] == prefix[index] for index in range(len(prefix))))


def _snapshot_tree(worker: Any, model_tag: str, model_revision: int | None, stage: str,
                   *, pre_write: bool) -> tuple[dict[str, dict[str, Any]], int]:
    """Whole-tree candidate-vocabulary snapshot; resumable across pages.

    The walk is the read-only discovery route already published for
    ``node.find``: probe errors are explicit rows, never silently empty lists.
    ``pre_write=True`` refusals are PreWriteRefusal (no mutation has been
    possible yet); the post stage raises an ordinary post-dispatch
    ExecutionContractError so the caller marks the mutation UNKNOWN.
    """
    def refuse(message: str, details: dict[str, Any], cause: BaseException | None = None):
        if pre_write:
            refusal = PreWriteRefusal("REMOVAL_SNAPSHOT_INCOMPLETE", message, details=details)
            return refusal.with_cause(cause) if cause is not None else refusal
        return ExecutionContractError("REMOVAL_SNAPSHOT_INCOMPLETE", message,
                                      stage="post_dispatch", details=details)

    rows: dict[str, dict[str, Any]] = {}
    cursor: str | None = None
    pages = 0
    while True:
        try:
            out = find_nodes(worker, model_tag, {}, root={"segments": []},
                             limit=SNAPSHOT_PAGE_LIMIT, cursor=cursor,
                             model_revision=model_revision)
        except ExecutionContractError as exc:
            if pre_write:
                # A read can be unresolved even though no delete was issued.
                # Preserve codes the outer job already treats as UNKNOWN.
                if (exc.code == "EXECUTION_STATE_UNKNOWN"
                        or (exc.code == "ENGINE_UNRESPONSIVE" and not exc.safe_retry)):
                    raise
                # A non-UNKNOWN wrapper can carry a structured UNKNOWN in
                # its details or cause; the outer job checks only the code.
                if _worker_failure_unknown(exc):
                    raise ExecutionContractError(
                        "EXECUTION_STATE_UNKNOWN",
                        f"the whole-tree {stage} snapshot state is unknown",
                        details={"stage": stage, "cause_code": exc.code,
                                 "receiver_accessed": True, "mutation_dispatched": False},
                    ) from exc
            raise refuse(f"the whole-tree {stage} snapshot failed",
                         {"stage": stage, "cause_code": getattr(exc, "code", None),
                          "cause": str(exc)[:600], "receiver_accessed": True,
                          "mutation_dispatched": not pre_write}, exc) from exc
        pages += 1
        if out["errors"]:
            raise refuse(f"the whole-tree {stage} snapshot reported probe/getter errors; "
                         "retention of non-target nodes is not provable",
                         {"stage": stage, "errors": out["errors"][:50],
                          "receiver_accessed": True, "mutation_dispatched": not pre_write})
        for row in out["results"]:
            key = _row_key(row["path"])
            if key in rows:
                raise refuse("the whole-tree snapshot observed the same path twice",
                             {"stage": stage, "path": row["path"],
                              "receiver_accessed": True, "mutation_dispatched": not pre_write})
            rows[key] = row
        if not out["truncated"]:
            break
        if pages >= SNAPSHOT_MAX_PAGES:
            raise refuse("the whole-tree snapshot exceeded its page budget",
                         {"stage": stage, "pages": pages, "visited": out["visited"],
                          "receiver_accessed": True, "mutation_dispatched": not pre_write})
        cursor = out["next_cursor"]
    if not out["complete"]:
        raise refuse(f"the whole-tree {stage} snapshot ended incomplete",
                     {"stage": stage, "truncated_reason": out["truncated_reason"],
                      "receiver_accessed": True, "mutation_dispatched": not pre_write})
    return rows, pages


def prepare(worker: Any, model_tag: str, body: Mapping[str, Any], *,
            model_revision: int | None) -> dict[str, Any]:
    """Resolve the actual receiver and build the pre-write retention baseline."""
    authorization = validate_consent(body["path"], body.get("authorization"))
    parsed = NodePath.from_wire(body["path"], allow_empty=False)
    if parsed.segments[-1].collection is None:
        raise PreWriteRefusal("INVALID_NODE_PATH",
                              "the removal target must end in a tagged entity, not an accessor",
                              details={"receiver_accessed": False, "mutation_dispatched": False})
    model = _model(worker, model_tag)
    parent_path, container, leaf = _entity_parent(worker, model_tag, parsed.as_dict())
    from ._g2_public_api import describe_receiver, supports, supports_release
    descriptor = describe_receiver(worker, container)
    if (not supports_release(descriptor["runtime_version"])
            or not supports(descriptor, MODEL_ENTITY_LIST, "remove", (JAVA_STRING,), "void")):
        raise PreWriteRefusal(
            "API_UNSUPPORTED",
            f"the actual container receiver has no reviewed {MODEL_ENTITY_LIST}.remove({JAVA_STRING}) signature",
            details={"receiver_accessed": True, "mutation_dispatched": False,
                     "runtime_version": descriptor["runtime_version"],
                     "interfaces": list(descriptor["interfaces"])[:64]})
    before_tags = _actual_tags(container)
    if leaf.tag not in before_tags:
        raise PreWriteRefusal(
            "NODE_NOT_FOUND", "the removal target tag is absent from the actual container tags",
            details={"receiver_accessed": True, "mutation_dispatched": False,
                     "actual_tags": before_tags})
    target_path = parsed.as_dict()
    snapshot_rows, pages = _snapshot_tree(worker, model_tag, model_revision, "pre", pre_write=True)
    target_key = _row_key(target_path)
    if target_key not in snapshot_rows:
        raise PreWriteRefusal(
            "REMOVAL_SNAPSHOT_INCOMPLETE",
            "the pre-write snapshot did not observe the resolved target path; accessor resolution and the whole-tree walk disagree",
            details={"target_path": target_path, "receiver_accessed": True, "mutation_dispatched": False})
    target_segments = target_path["segments"]
    subtree_keys = {key for key, row in snapshot_rows.items()
                    if _under_prefix(row["path"]["segments"], target_segments)}
    # Defensive: under the current published vocabulary a nodeGroup segment is
    # only addressable as the single-segment model-root path, which stays on
    # the reviewed _g2_group_remove ungroup route; multi-segment nodeGroup
    # paths are rejected by NodePath.from_wire pre-write (no hidden group
    # cascade route). If nested group addressability is ever opened, this
    # pre-read makes member effects observable: COMSOL 6.4
    # NodeGroupList.remove(String) "Removes a group and its members from the
    # model. Any member that cannot be removed is kept" (NodeGroupList.html
    # SHA-256 39d28e1083da9a724501eb87b28f014ae7dbf1860f585a07eb80f36c2df47949).
    pre_members: list[str] | None = None
    if parsed.segments[-1].collection == "nodeGroup":
        try:
            pre_members = [str(item) for item in _call(_call(container, "get", leaf.tag), "members")]
        except Exception:
            pre_members = None
    return {"authorization": authorization, "model_tag": model_tag, "model_revision": model_revision,
            "container": container, "container_descriptor": descriptor, "before_tags": before_tags,
            "tag": leaf.tag, "target_path": target_path, "snapshot": snapshot_rows,
            "subtree_keys": subtree_keys, "snapshot_pages": pages, "pre_members": pre_members}


def execute(worker: Any, prepared: Mapping[str, Any], current_model: Any,
            model_ref: Mapping[str, Any], data: dict[str, Any], *, before_dispatch: Any) -> None:
    """Dispatch one authorized remove, then prove non-target retention."""
    model_tag = prepared["model_tag"]
    if not isinstance(model_ref, Mapping):
        raise PreWriteRefusal("MODEL_IDENTITY_MISMATCH", "bound ModelRef is missing")
    ref_tag = model_ref.get("model_tag")
    generation = model_ref.get("generation")
    if type(ref_tag) is not str or not ref_tag or ref_tag != model_tag:
        raise PreWriteRefusal("MODEL_IDENTITY_MISMATCH", "bound ModelRef model_tag is malformed or changed")
    if type(generation) is not int or generation < 1:
        raise PreWriteRefusal("MODEL_IDENTITY_MISMATCH", "bound ModelRef generation is malformed")
    container = prepared["container"]
    now_tags = _actual_tags(container)
    if now_tags != prepared["before_tags"]:
        raise PreWriteRefusal(
            "REMOVAL_SNAPSHOT_INCOMPLETE",
            "the actual container tag list changed after the pre-write snapshot",
            details={"before_tags": prepared["before_tags"], "current_tags": now_tags,
                     "receiver_accessed": True, "mutation_dispatched": False})
    pre_rows: Mapping[str, Any] = prepared["snapshot"]
    subtree_keys = prepared["subtree_keys"]
    data.update({
        "semantic_operation": SEMANTIC_OPERATION,
        "removed_path": prepared["target_path"],
        "cascade": False,
        "authorization": prepared["authorization"],
        "model_revision_at_prepare": prepared.get("model_revision"),
        "readback": {"pre_snapshot": {"node_count": len(pre_rows), "pages": prepared["snapshot_pages"]},
                     "pre_container_tags": list(prepared["before_tags"])},
        "reference_risk": {
            "level": "UNSCANNED",
            "scope": "target_subtree",
            "obligations": ["incoming expression/selection/consumer references (no public COMSOL 6.4 getter)",
                            "descendant placement under remove(String) (no public per-family contract)",
                            "child enumeration bounded to the candidate collection vocabulary"],
        },
        "dependency_check": {
            "complete": False, "incoming_complete": False, "status": "UNVERIFIED",
            "scope": "candidate-vocabulary whole-tree snapshots around the authorized target only",
            "unknown_obligations": ["complete incoming consumer graph", "native descendant placement semantics"],
        },
        "evidence": {**data.get("evidence", {}), "general_remove": {
            "runtime_version": prepared["container_descriptor"]["runtime_version"],
            "consent_scope": prepared["authorization"]["scope"],
            "path_keys_are_identity": "candidate-vocabulary path segments; not an external identity proof",
            "retention_comparison_fields": ["path", *RETENTION_COMPARED_FIELDS],
        }},
    })
    # From this point the executor treats any exception as an issued mutation
    # with unknown partial effects. This adapter issues one remove and never
    # retries it.
    before_dispatch()
    data["mutation_dispatch"] = {"request_issued": True, "semantic_operation": SEMANTIC_OPERATION,
                                 "tag": prepared["tag"], "target_path": prepared["target_path"]}
    _call(container, "remove", prepared["tag"])
    after_tags = _actual_tags(container)
    if prepared["tag"] in after_tags:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN", "the target tag remains in the actual container after remove",
            stage="post_dispatch", details={"tag": prepared["tag"], "tags_after": after_tags})
    data["readback"]["post_container_tags"] = after_tags
    post_rows, post_pages = _snapshot_tree(worker, model_tag, prepared.get("model_revision"),
                                           "post", pre_write=False)
    outside_before = {key for key in pre_rows if key not in subtree_keys}
    post_keys = set(post_rows)
    missing = sorted(outside_before - post_keys)
    new_paths = sorted(post_keys - set(pre_rows))
    # Every whole-tree path that is new relative to the pre-write snapshot is
    # disclosed in unexpected_new_paths and escalated.  The former "inside the
    # authorized subtree" split (new_paths_inside_authorized_subtree) was
    # removed 2026-11-09 as structurally vacuous: subtree_keys is derived from
    # the pre-snapshot rows, so no key can be both new relative to pre_rows
    # and a member of subtree_keys; the published field was always [] and read
    # as a checked "no new subtree-internal paths" guarantee it never
    # provided.  A genuinely new path under the target prefix would require the
    # removed target to reappear, which the earlier container-tag and
    # target_absent gates already escalate to EXECUTION_STATE_UNKNOWN; if a
    # future enumeration vocabulary diverged enough to bypass those gates, such
    # a path is classified here as unexpected (conservative UNKNOWN), never as
    # authorized.
    new_outside_subtree = sorted(key for key in new_paths if key not in subtree_keys)
    # Same-path non-target rows are compared field-by-field on exactly what
    # the candidate walk observes; a path-set-only diff cannot see an in-place
    # replacement of a non-target entity (reviewer CE-A).
    changed: list[dict[str, Any]] = []
    for key in sorted(outside_before & post_keys):
        before_row, after_row = pre_rows[key], post_rows[key]
        diffs = {field: {"before": before_row.get(field), "after": after_row.get(field)}
                 for field in RETENTION_COMPARED_FIELDS if before_row.get(field) != after_row.get(field)}
        if diffs:
            changed.append({"path": before_row["path"], "fields": diffs})
    observed_equal = outside_before & post_keys
    target_absent = _row_key(prepared["target_path"]) not in post_keys
    subtree_absent = sorted(key for key in subtree_keys if key not in post_keys)
    subtree_remaining = sorted(key for key in subtree_keys if key in post_keys)
    member_survivors: list[str] | None = None
    member_removed: list[str] | None = None
    if prepared.get("pre_members") is not None:
        post_tag_set = {row["path"]["segments"][-1]["tag"] for row in post_rows.values()
                        if row["path"]["segments"] and row["path"]["segments"][-1].get("tag")}
        member_survivors = sorted(t for t in prepared["pre_members"] if t in post_tag_set)
        member_removed = sorted(t for t in prepared["pre_members"] if t not in post_tag_set)
    data["retention"] = {
        # "Retained" below never means "the same object survived": it is a
        # whole-tree path/row observation limited to the compared fields.
        "observation_scope": "whole-tree candidate-vocabulary walk; non-target rows compared on path presence plus the compared_fields below",
        "compared_fields": ["path", *RETENTION_COMPARED_FIELDS],
        "unchanged_claim": "path presence plus equal tag/type_id/label only; not an object-identity or property claim",
        "unverified_dimensions": [
            "object identity behind an equal path with equal compared fields (no public COMSOL 6.4 identity getter)",
            "properties beyond tag/type_id/label (no per-node property diff was taken before or after)",
            "incoming/outgoing external references (no public COMSOL 6.4 incoming-reference getter)",
        ],
        "non_target_paths_before": len(outside_before),
        "non_target_paths_absent_after": len(missing),
        "missing_non_target_paths": missing[:50],
        "non_target_paths_metadata_changed": len(changed),
        "metadata_changes": changed[:20],
        "non_target_paths_unchanged_on_compared_fields": len(observed_equal) - len(changed),
        "unexpected_new_paths": new_outside_subtree[:50],
        "target_absent": target_absent,
        "subtree_rows_before": len(subtree_keys),
        "subtree_rows_absent_after": len(subtree_absent),
        "subtree_rows_still_observed": len(subtree_remaining),
        "snapshot_pages_pre": prepared["snapshot_pages"],
        "snapshot_pages_post": post_pages,
        "enumeration_scope": "candidate_collection_vocabulary",
    }
    data["coverage"] = list(data.get("coverage", [])) + [
        {"path": prepared["target_path"], "field": "incoming expression/selection/consumer references",
         "status": "UNSUPPORTED",
         "reason": "no public COMSOL 6.4 incoming-reference getter (the API index has no getDependencies); "
                   "the consent acknowledges external_reference_impact_unverified"},
        {"path": prepared["target_path"], "field": "child enumeration completeness", "status": "UNVERIFIED",
         "reason": "both snapshots only name the enumerated candidate collections; child lists reachable "
                   "under other accessor names stay invisible to the retention proof"},
        {"path": prepared["target_path"], "field": "descendant placement under remove(String)", "status": "UNVERIFIED",
         "reason": f"observed disposition only: {len(subtree_absent)} subtree rows absent, "
                   f"{len(subtree_remaining)} still observed; the 6.4 javadoc for ModelEntityList.remove "
                   "states only 'Removes an entity.'"},
        {"path": prepared["target_path"], "field": "non-target object identity and hidden properties", "status": "UNVERIFIED",
         "reason": "the retention proof compares path presence plus tag/type_id/label from the candidate walk; "
                   "a non-target replacement that keeps all compared fields is invisible to this vocabulary and "
                   "must not be read as 'the same object survived'"},
    ]
    data["status"] = "INCOMPLETE"
    data["complete"] = False
    if missing:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            "the issued remove removed entities outside the authorized target subtree",
            stage="post_dispatch",
            details={"missing_non_target_paths": missing[:50], "authorized_scope": "target_subtree",
                     "detection": "post-dispatch comparison against the pre-write whole-tree snapshot"})
    if changed:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            "entities observed outside the authorized target subtree changed on compared fields after the remove",
            stage="post_dispatch",
            details={"metadata_changes": changed[:20], "compared_fields": ["path", *RETENTION_COMPARED_FIELDS],
                     "authorized_scope": "target_subtree",
                     "detection": "pre/post whole-tree row comparison; identity beyond compared fields stays UNVERIFIED"})
    if new_outside_subtree:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            "new whole-tree paths appeared outside the authorized target subtree after the remove",
            stage="post_dispatch",
            details={"unexpected_new_paths": new_outside_subtree[:50], "authorized_scope": "target_subtree",
                     "detection": "post-dispatch comparison against the pre-write whole-tree snapshot"})
    if not target_absent:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            "the target path is still observed in the whole-tree snapshot after remove",
            stage="post_dispatch", details={"target_path": prepared["target_path"]})
