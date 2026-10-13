"""Narrow model-root NodeGroup structural ungroup evidence for G2 D2.

This adapter uses only the private typed Worker methods added for the D2
contract. It does not widen NodePath, generic reflection, or public
capabilities. Structural ungroup is reported as incomplete because incoming
consumer completeness and native placement semantics are not observable here.
"""
from __future__ import annotations

from typing import Any, Mapping

from ._execution_contract import ExecutionContractError, PreWriteRefusal


COMSOL_VERSION = "6.4.0.293"
MODEL_INTERFACE = "com.comsol.model.Model"
MODEL_ENTITY_INTERFACE = "com.comsol.model.ModelEntity"
PRIMITIVE_INTERFACE = "com.comsol.model.PrimitiveModelEntity"
NODEGROUP_INTERFACE = "com.comsol.model.NodeGroup"
NODEGROUP_LIST_INTERFACE = "com.comsol.model.NodeGroupList"
MAX_GROUP_MEMBERS = 512
MAX_NODEGROUP_TAGS = 4096
MAX_CONTAINER_HOPS = 32


def is_model_root_nodegroup_path(value: Any) -> bool:
    """Return true only for the exact one-segment model-root route."""
    if not isinstance(value, Mapping) or set(value) != {"segments"}:
        return False
    segments = value.get("segments")
    if type(segments) is not list or len(segments) != 1:
        return False
    segment = segments[0]
    if not isinstance(segment, Mapping) or set(segment) != {"collection", "tag"}:
        return False
    collection, tag = segment.get("collection"), segment.get("tag")
    return (
        type(collection) is str and collection == "nodeGroup"
        and type(tag) is str and bool(tag)
        and not any(character in tag for character in "/\\\x00")
    )


def _group_tag(path: Mapping[str, Any]) -> str:
    if not is_model_root_nodegroup_path(path):
        raise PreWriteRefusal("INVALID_NODE_PATH", "D2 accepts exactly one model-root nodeGroup/tag segment")
    return path["segments"][0]["tag"]


def _cause_record(exc: BaseException) -> dict[str, Any]:
    record: dict[str, Any] = {"cause_type": type(exc).__name__, "cause_message": str(exc)[:600]}
    reply = getattr(exc, "reply", None)
    if isinstance(reply, Mapping):
        record["worker_reply"] = dict(reply)
    return record


def _prewrite(exc: BaseException, message: str) -> PreWriteRefusal:
    code = getattr(exc, "code", None)
    if type(code) is not str or not code:
        reply = getattr(exc, "reply", None)
        failure = reply.get("failure") if isinstance(reply, Mapping) else None
        code = failure.get("code") if isinstance(failure, Mapping) else None
    if type(code) is not str or not code:
        code = "API_UNSUPPORTED"
    return PreWriteRefusal(code, message, details=_cause_record(exc)).with_cause(exc)


def _remote_type() -> type:
    from ._java_worker import RemoteJava
    return RemoteJava


def _require_remote(worker: Any, value: Any, label: str) -> Any:
    RemoteJava = _remote_type()
    if not isinstance(value, RemoteJava):
        raise PreWriteRefusal("API_UNSUPPORTED", f"{label} is not a typed Worker handle")
    generation = getattr(worker, "generation", None)
    if (value._worker is not worker or type(generation) is not int or generation < 1
            or type(value._generation) is not int or value._generation != generation
            or type(value._handle) is not str or not value._handle):
        raise PreWriteRefusal("STALE_WORKER_HANDLE", f"{label} belongs to another Worker or generation")
    return value


def _descriptor(worker: Any, value: Any, required: set[str], label: str) -> dict[str, Any]:
    value = _require_remote(worker, value, label)
    try:
        descriptor = worker.describe_public(value)
    except Exception as exc:
        raise _prewrite(exc, f"could not describe the actual {label} public receiver") from exc
    if not isinstance(descriptor, Mapping):
        raise PreWriteRefusal("API_UNSUPPORTED", f"{label} public descriptor is not an object")
    interfaces = descriptor.get("interfaces")
    if (type(descriptor.get("runtime_version")) is not str
            or descriptor["runtime_version"] != COMSOL_VERSION
            or type(interfaces) is not list
            or any(type(item) is not str for item in interfaces)
            or not required.issubset(set(interfaces))):
        raise PreWriteRefusal("API_UNSUPPORTED", f"{label} lacks exact COMSOL {COMSOL_VERSION} public interfaces",
                              details={"required_interfaces": sorted(required),
                                       "actual_interfaces": interfaces,
                                       "runtime_version": descriptor.get("runtime_version")})
    return dict(descriptor)


def _read(worker: Any, value: Any, method: str, *args: Any) -> Any:
    value = _require_remote(worker, value, f"{method} receiver")
    try:
        return worker.read_nodegroup(value, method, *args)
    except Exception as exc:
        raise _prewrite(exc, f"typed {method} read failed before NodeGroup ungroup") from exc


def _same_reference(worker: Any, left: Any, right: Any, label: str) -> bool:
    left = _require_remote(worker, left, f"{label} left operand")
    right = _require_remote(worker, right, f"{label} right operand")
    try:
        same = worker.entity_identity(left, right)
    except Exception as exc:
        raise _prewrite(exc, f"Java reference identity is unavailable for {label}") from exc
    if type(same) is not bool:
        raise PreWriteRefusal("API_UNSUPPORTED", f"Java reference identity returned a non-boolean for {label}")
    return same


def _typed_tags(worker: Any, value: Any, label: str) -> list[str]:
    tags = _read(worker, value, "tags")
    if type(tags) is not list or any(type(tag) is not str or not tag for tag in tags):
        raise PreWriteRefusal("EXECUTION_STATE_UNKNOWN", f"{label} returned malformed or truncated tags")
    if len(tags) > MAX_NODEGROUP_TAGS or len(set(tags)) != len(tags):
        raise PreWriteRefusal("EXECUTION_STATE_UNKNOWN", f"{label} returned duplicate or over-budget tags")
    return list(tags)


def _path(worker: Any, value: Any, label: str) -> str:
    actual = _read(worker, value, "resolveModelPath")
    if type(actual) is not str or not actual:
        raise PreWriteRefusal("EXECUTION_STATE_UNKNOWN", f"{label} has no exact nonempty resolved model path")
    return actual


def _owned_chain(worker: Any, entity: Any, model: Any, label: str) -> list[dict[str, Any]]:
    current = _require_remote(worker, entity, label)
    chain: list[dict[str, Any]] = []
    seen: list[Any] = []
    for hop in range(MAX_CONTAINER_HOPS + 1):
        descriptor = _descriptor(worker, current, {PRIMITIVE_INTERFACE}, f"{label} container at hop {hop}")
        chain.append({"hop": hop, "interfaces": list(descriptor["interfaces"]),
                      "is_bound_model": False, "_reference": current})
        if _same_reference(worker, current, model, f"{label} to bound Model"):
            chain[-1]["is_bound_model"] = True
            return chain
        for index, prior in enumerate(seen):
            if _same_reference(worker, current, prior, f"{label} container cycle check"):
                raise PreWriteRefusal("API_UNSUPPORTED", f"{label} container chain contains a cycle",
                                      details={"cycle_start_hop": index, "cycle_hop": hop})
        if hop == MAX_CONTAINER_HOPS:
            break
        seen.append(current)
        parent = _read(worker, current, "getContainer")
        if parent is None:
            raise PreWriteRefusal("API_UNSUPPORTED", f"{label} container chain ended before the bound Model",
                                  details={"hop": hop, "ownership_proven": False})
        current = _require_remote(worker, parent, f"{label} parent at hop {hop + 1}")
    raise PreWriteRefusal("API_UNSUPPORTED", f"{label} container chain exceeded its finite bound",
                          details={"max_hops": MAX_CONTAINER_HOPS, "ownership_proven": False})


def _capture(worker: Any, model: Any, tag: str, *, include_members: bool = True) -> dict[str, Any]:
    model = _require_remote(worker, model, "bound Model")
    model_descriptor = _descriptor(worker, model, {MODEL_INTERFACE, PRIMITIVE_INTERFACE}, "bound Model")
    group_list = _read(worker, model, "nodeGroup")
    group_list = _require_remote(worker, group_list, "NodeGroupList")
    group_list_descriptor = _descriptor(worker, group_list, {NODEGROUP_LIST_INTERFACE, PRIMITIVE_INTERFACE}, "NodeGroupList")
    list_tags = _typed_tags(worker, group_list, "NodeGroupList.tags")
    if tag not in list_tags:
        raise PreWriteRefusal("NODE_NOT_FOUND", "target NodeGroup tag is absent from actual model-root tags",
                              details={"target_tag": tag, "actual_tags": list_tags})
    if not _owned_chain(worker, group_list, model, "NodeGroupList"):
        raise PreWriteRefusal("API_UNSUPPORTED", "NodeGroupList ownership was not proven")
    group = _read(worker, model, "nodeGroup", tag)
    group = _require_remote(worker, group, "target NodeGroup")
    group_descriptor = _descriptor(worker, group, {NODEGROUP_INTERFACE, MODEL_ENTITY_INTERFACE, PRIMITIVE_INTERFACE}, "target NodeGroup")
    group_path = _path(worker, group, "target NodeGroup")
    group_chain = _owned_chain(worker, group, model, "target NodeGroup")
    size = _read(worker, group, "size")
    if type(size) is not int or size < 0 or size > MAX_GROUP_MEMBERS:
        raise PreWriteRefusal("EXECUTION_STATE_UNKNOWN", "NodeGroup.size is malformed or exceeds its finite bound",
                              details={"actual_size": size, "maximum": MAX_GROUP_MEMBERS})
    nested = _read(worker, group, "feature")
    nested = _require_remote(worker, nested, "nested NodeGroupList")
    nested_descriptor = _descriptor(worker, nested, {NODEGROUP_LIST_INTERFACE, PRIMITIVE_INTERFACE}, "nested NodeGroupList")
    nested_tags = _typed_tags(worker, nested, "NodeGroup.feature().tags")
    if nested_tags:
        raise PreWriteRefusal("API_UNSUPPORTED", "nested NodeGroup members require a separate reviewed adapter",
                              details={"nested_tags": nested_tags, "next_adapter": "nested NodeGroup structural removal"})
    after = _read(worker, group, "getAfter")
    after_path: str | None = None
    after_chain: list[dict[str, Any]] | None = None
    if after is not None:
        after = _require_remote(worker, after, "NodeGroup.getAfter result")
        _descriptor(worker, after, {MODEL_ENTITY_INTERFACE, PRIMITIVE_INTERFACE}, "NodeGroup.getAfter result")
        after_path = _path(worker, after, "NodeGroup.getAfter result")
        after_chain = _owned_chain(worker, after, model, "NodeGroup.getAfter result")
    members: list[dict[str, Any]] = []
    if include_members:
        for index in range(size):
            member = _read(worker, group, "get", index)
            member = _require_remote(worker, member, f"NodeGroup member {index}")
            descriptor = _descriptor(worker, member, {MODEL_ENTITY_INTERFACE, PRIMITIVE_INTERFACE}, f"NodeGroup member {index}")
            if NODEGROUP_INTERFACE in descriptor["interfaces"]:
                raise PreWriteRefusal("API_UNSUPPORTED", "a NodeGroup member is nested and cannot be safely ungrouped",
                                      details={"member_index": index, "member_path": _path(worker, member, f"nested member {index}")})
            member_path = _path(worker, member, f"NodeGroup member {index}")
            chain = _owned_chain(worker, member, model, f"NodeGroup member {index}")
            for prior_index, prior in enumerate(members):
                if member_path == prior["path"]:
                    raise PreWriteRefusal("API_UNSUPPORTED", "two NodeGroup members have the same observed model path",
                                          details={"member_indices": [prior_index, index], "path": member_path,
                                                   "identity_scope": "path collision refused as ambiguous; path is not identity proof"})
                if _same_reference(worker, member, prior["_reference"], "duplicate member reference check"):
                    raise PreWriteRefusal("API_UNSUPPORTED", "NodeGroup contains a duplicate Java reference",
                                          details={"member_indices": [prior_index, index], "path": member_path})
            members.append({"index": index, "_reference": member, "path": member_path,
                            "interfaces": list(descriptor["interfaces"]), "container_chain": chain})
        if len(members) != size:
            raise PreWriteRefusal("EXECUTION_STATE_UNKNOWN", "NodeGroup member enumeration was partial",
                                  details={"expected_size": size, "actual_members": len(members)})
    return {
        "model": model, "model_descriptor": model_descriptor,
        "group_list": group_list, "group_list_descriptor": group_list_descriptor,
        "group_list_tags": list_tags, "group": group, "group_descriptor": group_descriptor,
        "group_path": group_path, "group_container_chain": group_chain,
        "group_size": size, "nested_list": nested, "nested_descriptor": nested_descriptor,
        "nested_tags": nested_tags, "after": after, "after_path": after_path,
        "after_container_chain": after_chain, "members": members,
    }


def _same_chain(worker: Any, left: list[dict[str, Any]], right: list[dict[str, Any]], label: str) -> bool:
    if len(left) != len(right):
        return False
    for index, (left_item, right_item) in enumerate(zip(left, right)):
        if not _same_reference(worker, left_item["_reference"], right_item["_reference"], f"{label} hop {index}"):
            return False
    return True


def _require_same_projection(worker: Any, prepared: Mapping[str, Any], current: Mapping[str, Any]) -> None:
    if prepared["group_list_tags"] != current["group_list_tags"]:
        raise PreWriteRefusal("API_UNSUPPORTED", "model-root NodeGroup tag projection changed before dispatch",
                              details={"prepared_tags": prepared["group_list_tags"], "current_tags": current["group_list_tags"]})
    for key, label in (("model", "bound Model"), ("group_list", "NodeGroupList"), ("group", "target NodeGroup")):
        if not _same_reference(worker, prepared[key], current[key], f"prepared/current {label}"):
            raise PreWriteRefusal("API_UNSUPPORTED", f"prepared {label} identity changed before dispatch")
    if (prepared["group_path"] != current["group_path"]
            or prepared["group_size"] != current["group_size"]
            or prepared["nested_tags"] != current["nested_tags"]
            or not _same_chain(worker, prepared["group_container_chain"], current["group_container_chain"], "target group ownership")):
        raise PreWriteRefusal("API_UNSUPPORTED", "prepared NodeGroup structural projection changed before dispatch")
    if (prepared["after"] is None) != (current["after"] is None):
        raise PreWriteRefusal("API_UNSUPPORTED", "NodeGroup getAfter anchor changed before dispatch")
    if prepared["after"] is not None:
        if (not _same_reference(worker, prepared["after"], current["after"], "NodeGroup getAfter anchor")
                or prepared["after_path"] != current["after_path"]
                or not _same_chain(worker, prepared["after_container_chain"], current["after_container_chain"], "anchor ownership")):
            raise PreWriteRefusal("API_UNSUPPORTED", "NodeGroup getAfter anchor projection changed before dispatch")
    if len(prepared["members"]) != len(current["members"]):
        raise PreWriteRefusal("API_UNSUPPORTED", "NodeGroup member count changed before dispatch")
    for index, (before, now) in enumerate(zip(prepared["members"], current["members"])):
        if (not _same_reference(worker, before["_reference"], now["_reference"], f"member {index} identity")
                or before["path"] != now["path"]
                or before["interfaces"] != now["interfaces"]
                or not _same_chain(worker, before["container_chain"], now["container_chain"], f"member {index} ownership")):
            raise PreWriteRefusal("API_UNSUPPORTED", f"NodeGroup member {index} projection changed before dispatch")


def _chain_public(chain: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Project ownership evidence to fields safe for durable operation results."""
    return [{"hop": row["hop"], "interfaces": list(row["interfaces"]),
             "is_bound_model": row["is_bound_model"]}
            for row in chain]


def _member_public(member: Mapping[str, Any]) -> dict[str, Any]:
    return {"index": member["index"], "path": member["path"],
            "public_interfaces": list(member["interfaces"]),
            "container_chain": _chain_public(member["container_chain"])}


def prepare(worker: Any, model: Any, path: Mapping[str, Any], *, model_tag: str,
            model_revision: int | None) -> dict[str, Any]:
    tag = _group_tag(path)
    try:
        snapshot = _capture(worker, model, tag)
    except PreWriteRefusal:
        raise
    except Exception as exc:
        raise _prewrite(exc, "D2 NodeGroup preflight evidence is incomplete") from exc
    return {"path": {"segments": [{"collection": "nodeGroup", "tag": tag}]},
            "tag": tag, "model": model, "model_tag": model_tag, "model_revision": model_revision,
            "snapshot": snapshot, "model_descriptor": snapshot["model_descriptor"]}


def execute(worker: Any, prepared: Mapping[str, Any], current_model: Any,
            model_ref: Mapping[str, Any], data: dict[str, Any], *, before_dispatch: Any) -> None:
    tag = prepared["tag"]
    current_model = _require_remote(worker, current_model, "fresh bound Model")
    prepared_model = _require_remote(worker, prepared.get("model"), "prepared Model")
    if not isinstance(model_ref, Mapping):
        raise PreWriteRefusal("MODEL_IDENTITY_MISMATCH", "bound ModelRef is missing")
    model_generation = model_ref.get("generation")
    model_tag = model_ref.get("model_tag")
    if (type(model_generation) is not int or model_generation < 1
            or type(model_tag) is not str or not model_tag or model_tag != prepared.get("model_tag")):
        raise PreWriteRefusal("MODEL_IDENTITY_MISMATCH", "bound ModelRef generation/tag is malformed or changed")
    # ModelRef.generation is the ledger's binding epoch, while RemoteJava's
    # generation is the private Worker's restart fence. They are independent
    # positive integers and must never be compared to one another.
    if prepared_model._generation != current_model._generation:
        raise PreWriteRefusal("STALE_WORKER_HANDLE", "Worker generation changed after NodeGroup preflight")
    generation = current_model._generation
    try:
        current = _capture(worker, current_model, tag)
        _require_same_projection(worker, prepared["snapshot"], current)
    except PreWriteRefusal:
        raise
    except Exception as exc:
        raise _prewrite(exc, "fresh D2 NodeGroup projection is incomplete before dispatch") from exc
    snapshot = prepared["snapshot"]
    data.update({
        "semantic_operation": "NodeGroupList.ungroup(String)",
        "removed_path": prepared["path"],
        "cascade": False,
        "model_revision_at_prepare": prepared.get("model_revision"),
        "readback": {
            "preflight": {"group_path": snapshot["group_path"], "group_tags": snapshot["group_list_tags"],
                          "group_size": snapshot["group_size"], "getAfter_path": snapshot["after_path"],
                          "member_order": [_member_public(row) for row in snapshot["members"]]},
            "pre_dispatch": {"group_path": current["group_path"], "group_tags": current["group_list_tags"],
                             "group_size": current["group_size"], "getAfter_path": current["after_path"],
                             "projection_match": True},
        },
        "retained_members": [_member_public(row) for row in snapshot["members"]],
        "dependency_check": {
            "complete": False, "incoming_complete": False, "status": "UNVERIFIED",
            "scope": "observed NodeGroup members and model-root NodeGroup tags only",
            "unknown_obligations": ["incoming expression/selection consumer graph", "native placement and layout semantics",
                                    "independent member path re-resolution", "full model equality", "native six-target acceptance"],
        },
        "coverage": list(data.get("coverage", [])) + [
            {"path": prepared["path"], "field": "incoming dependencies", "status": "UNVERIFIED",
             "reason": "no reviewed public incoming/reference graph getter"},
            {"path": prepared["path"], "field": "native placement/layout", "status": "UNVERIFIED",
             "reason": "NodeGroupList.ungroup retention does not prove UI/native placement semantics"},
        ],
    })
    data["status"] = "INCOMPLETE"
    data["complete"] = False
    data["evidence"]["group_remove"] = {
        "runtime_version": COMSOL_VERSION,
        "public_interfaces": list(snapshot["group_descriptor"]["interfaces"]),
        "member_identity_scope": "Java reference identity for ownership and duplicate detection; false is not logical identity proof",
        "member_paths_are_identity": False,
        "post_operation_identity_claim": "held-reference usability with fresh typed container-chain evidence only",
    }
    # From this point the executor must treat any exception as an issued
    # mutation with unknown partial effects. The private Worker performs one
    # NodeGroupList.ungroup(String) invocation and never retries it.
    before_dispatch()
    data["mutation_dispatch"] = {"request_issued": True,
                                 "semantic_operation": "NodeGroupList.ungroup(String)",
                                 "generation": generation, "tag": tag}
    try:
        dispatch = worker.ungroup_nodegroup(current["group_list"], tag)
        if (not isinstance(dispatch, Mapping) or set(dispatch) != {
                "ungroup_dispatched", "generation", "tag", "semantic_operation", "runtime_version"}
                or dispatch.get("ungroup_dispatched") is not True
                or type(dispatch.get("generation")) is not int or dispatch["generation"] != generation
                or type(dispatch.get("tag")) is not str or dispatch["tag"] != tag
                or dispatch.get("semantic_operation") != "NodeGroupList.ungroup(String)"
                or dispatch.get("runtime_version") != COMSOL_VERSION):
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "private ungroup dispatch reply is malformed",
                                         stage="post_dispatch", details={"dispatch_reply": dict(dispatch) if isinstance(dispatch, Mapping) else repr(dispatch)})
        data["mutation_dispatch"] = dict(dispatch)
        root_list = _read(worker, current_model, "nodeGroup")
        root_list = _require_remote(worker, root_list, "post-dispatch NodeGroupList")
        if not _same_reference(worker, current["group_list"], root_list, "pre/post NodeGroupList"):
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "model-root NodeGroupList identity changed after ungroup",
                                         stage="post_dispatch")
        tags_after = _typed_tags(worker, root_list, "post-dispatch NodeGroupList.tags")
        if tag in tags_after:
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "target NodeGroup remains after ungroup",
                                         stage="post_dispatch", details={"target_tag": tag, "tags_after": tags_after})
        if tags_after != [item for item in snapshot["group_list_tags"] if item != tag]:
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "remaining NodeGroup tag order differs from observed expected retention",
                                         stage="post_dispatch", details={"tags_before": snapshot["group_list_tags"], "tags_after": tags_after})
        post_members = []
        for before in snapshot["members"]:
            member = _require_remote(worker, before["_reference"], f"held member {before['index']}")
            member_descriptor = _descriptor(worker, member, {MODEL_ENTITY_INTERFACE, PRIMITIVE_INTERFACE},
                                             f"post-dispatch held member {before['index']}")
            member_path = _path(worker, member, f"post-dispatch held member {before['index']}")
            chain = _owned_chain(worker, member, current_model, f"post-dispatch held member {before['index']}")
            post_members.append({"index": before["index"], "path": member_path,
                                 "path_changed": member_path != before["path"],
                                 "public_interfaces": list(member_descriptor["interfaces"]),
                                 "container_chain": _chain_public(chain),
                                 "identity_scope": "held reference only; no independent path re-resolution"})
        data["readback"].update({"post_dispatch": {"target_group_absent": True, "group_tags": tags_after,
                                                     "held_members_reported_in_preflight_order": [row["index"] for row in post_members],
                                                     "members": post_members,
                                                     "group_list_order_status": "OBSERVED_TAG_ORDER_ONLY",
                                                     "native_layout": "UNVERIFIED"}})
        data["target_group_absent"] = True
        data["retained_member_count"] = len(post_members)
        data["retained_member_paths"] = [row["path"] for row in post_members]
    except Exception as exc:
        if isinstance(exc, ExecutionContractError) and exc.stage == "post_dispatch":
            data["evidence"]["group_remove_failure"] = exc.as_dict()
            raise
        failure = _cause_record(exc)
        if isinstance(exc, ExecutionContractError):
            failure["contract_error"] = exc.as_dict()
        data["evidence"]["group_remove_failure"] = failure
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "NodeGroup ungroup was issued but required readback failed",
                                     stage="post_dispatch", details=failure).with_cause(exc) from exc
