"""Internally authorized, journalled Windows host-change executor.

The implemented WFP subset includes exact provider→sublayer→optional-filter
CREATE and receipt-bound reverse restore. A provider/sublayer plan uses the V2
per-resource OperationStore journal; the original filter-only V1 path remains
compatible. Explicit complete four-layer groups use sequence V3 with the same
per-resource journal and exact reverse restore. Public MCP routing is unchanged. Plans containing
any other action/resource kind are refused before authorization consumption or
native mutation. A committed intent is never automatically replayed.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import socket
import uuid
from typing import Any, Mapping

from ._execution_contract import ExecutionContractError
from ._operation_store import OperationStore
from ._project_authority import ProjectAuthority
from ._host_scoped_provider import HostScopedProvider
from . import _w26_os_resources as resources
from . import _windows_t037_native as wfp_native
from ._windows_t037_wfp import (
    CtypesWindowsWfpMutationApi, WindowsWfpMutationError, guid_key,
    normalize_endpoint_grant, sha256,
)

_SOFTWARE_TEST_TOKEN = object()
_NATIVE_PROFILE_LOADER_TOKEN = object()
_MAX_SNAPSHOT_ROWS = 100_000
_SHA = frozenset("0123456789abcdef")


class _TrustedProfileLoadOrigin:
    """Unforgeable in-process receipt for one normal trusted-profile load."""

    __slots__ = ("profile_wrapper", "profile_path", "source_sha256",
                 "source_identity_sha256", "wrapper_sha256",
                 "host_resource_sha256", "host_policy_sha256",
                 "security_semantics", "wrapper_schema", "_seal")
    _IMMUTABLE = frozenset(__slots__)

    def __setattr__(self, name, value):
        if name in self._IMMUTABLE and hasattr(self, name):
            _error("HOST_CHANGE_PROFILE_LOAD_ORIGIN_UNKNOWN",
                   "trusted profile loader origin is immutable")
        object.__setattr__(self, name, value)

    def __init__(self, *, wrapper: Any, profile_path: str | os.PathLike[str], seal: object):
        from ._windows_t037 import WindowsT037ProfileV2
        if (seal is not _NATIVE_PROFILE_LOADER_TOKEN
                or type(wrapper) is not WindowsT037ProfileV2):
            _error("HOST_CHANGE_TRUSTED_PROFILE_LOADER_REQUIRED",
                   "a native profile origin can only be issued by the normal trusted loader")
        profile = wrapper.host_resource_profile
        self.profile_wrapper = wrapper
        self.profile_path = str(profile_path)
        self.source_sha256 = wrapper.source_sha256
        self.source_identity_sha256 = hashlib.sha256(
            _canonical(dict(wrapper.source_identity)).encode("utf-8")).hexdigest()
        self.wrapper_sha256 = wrapper.sha256
        self.host_resource_sha256 = profile.sha256
        self.host_policy_sha256 = resources._sha256(profile.windows_isolation.as_dict())
        self.security_semantics = wrapper.security_semantics
        self.wrapper_schema = wrapper.wrapper_schema
        self._seal = seal

    def matches(self, wrapper: Any, profile_path: str | os.PathLike[str]) -> bool:
        from ._windows_t037 import WindowsT037ProfileV2
        if (self._seal is not _NATIVE_PROFILE_LOADER_TOKEN
                or type(wrapper) is not WindowsT037ProfileV2
                or wrapper is not self.profile_wrapper
                or str(profile_path) != self.profile_path):
            return False
        profile = wrapper.host_resource_profile
        return (
            wrapper.source_sha256 == self.source_sha256
            and hashlib.sha256(_canonical(dict(wrapper.source_identity)).encode("utf-8")).hexdigest()
                == self.source_identity_sha256
            and wrapper.sha256 == self.wrapper_sha256
            and profile.sha256 == self.host_resource_sha256
            and resources._sha256(profile.windows_isolation.as_dict()) == self.host_policy_sha256
            and wrapper.security_semantics == self.security_semantics
            and wrapper.wrapper_schema == self.wrapper_schema
        )


class WindowsHostChangeError(ExecutionContractError):
    """Typed pre-effect refusal or durable unknown from host mutation."""


def _error(code: str, message: str, *, safe_retry: bool = False,
           stage: str = "validation", details: Mapping[str, Any] | None = None) -> None:
    raise WindowsHostChangeError(code, message, safe_retry=safe_retry,
                                 stage=stage, details=details)


def _is_sha(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in _SHA for char in value)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def _current_windows_binding(profile: Any) -> dict[str, Any]:
    """Verify current Windows host, service token and project-root identity."""
    if os.name != "nt":
        _error("HOST_CHANGE_WINDOWS_REQUIRED", "native host mutation is available only on Windows")
    policy = profile.windows_isolation
    from ._windows_t037 import windows_host_fingerprint, WindowsT037Error
    from ._host_scoped_provider import verify_private_path_security
    from ._windows_t037 import _Win32ReadOnlyApi
    try:
        fingerprint = windows_host_fingerprint()
        host_id = socket.gethostname()
        if fingerprint != policy.host_fingerprint_sha256 or host_id.casefold() != profile.host_id.casefold():
            _error("HOST_CHANGE_HOST_MISMATCH", "trusted Windows profile names a different host or machine identity")
        root = verify_private_path_security(profile.root.path, directory=True)
        identity = root.get("identity")
        if (root.get("security_semantics") != "windows-read-control-owner-dacl-persistent-acls"
                or not isinstance(identity, Mapping)
                or str(identity.get("device")) != profile.root.volume_id
                or str(identity.get("inode")) != profile.root.file_id):
            _error("HOST_CHANGE_ROOT_IDENTITY_MISMATCH", "trusted project root identity or ACL differs from startup profile")
        api = _Win32ReadOnlyApi()
        process = api.k.GetCurrentProcess()
        token = api.open_process_token(process)
        try:
            token_observation = api.read_token(token)
        finally:
            api.close_handle(token)
        if (token_observation.get("state") != "OBSERVED"
                or token_observation.get("user_sid", "").casefold() != policy.service_logon_sid.casefold()
                or profile.root.owner_id.casefold() != policy.service_logon_sid.casefold()):
            _error("HOST_CHANGE_SERVICE_IDENTITY_MISMATCH",
                   "host changes require the exact configured service logon identity and root owner")
        return {"host_id": host_id, "host_fingerprint_sha256": fingerprint,
                "root_identity": dict(identity),
                "root_security_sha256": root.get("security_sha256"),
                "service_token_sid": token_observation["user_sid"]}
    except WindowsHostChangeError:
        raise
    except ExecutionContractError as exc:
        _error("HOST_CHANGE_NATIVE_BINDING_UNKNOWN", "Windows host/root identity could not be verified",
               details={"cause_code": exc.code})
    except WindowsT037Error as exc:
        _error("HOST_CHANGE_NATIVE_BINDING_UNKNOWN", "Windows host identity could not be verified",
               details={"cause_code": exc.code})
    except Exception as exc:
        _error("HOST_CHANGE_NATIVE_BINDING_UNKNOWN", "Windows service/root binding failed closed",
               details={"cause_type": type(exc).__name__})


def _normalize_supported_actions(plan: Any, trusted_profile: Any) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    try:
        payload = resources.validate_host_change_plan(plan)
    except resources.ResourcePolicyError:
        raise
    except Exception as exc:
        _error("HOST_CHANGE_PLAN_INVALID", "typed host-change plan validation failed",
               details={"cause_type": type(exc).__name__})
    if (payload["project_id"] != trusted_profile.project_id
            or payload["profile_sha256"] != trusted_profile.sha256
            or payload["host"] != {"platform": "windows", "host_id": trusted_profile.host_id}
            or payload["profile"] != trusted_profile.as_dict()):
        _error("HOST_CHANGE_PROFILE_SCOPE_MISMATCH", "plan does not bind the exact trusted startup profile/root/host")
    from . import _windows_appcontainer as appcontainer
    if appcontainer.is_leaf(payload['change_manifest']):
        return payload, (appcontainer.normalize(payload, trusted_profile),)
    from ._windows_service_sid import is_leaf, normalize
    if is_leaf(payload["change_manifest"]):
        return payload, (normalize(payload, trusted_profile),)
    from ._windows_filter_group import is_group, normalize_actions
    if is_group(payload["change_manifest"]):
        actions = normalize_actions(payload, trusted_profile, plan.sha256)
        for action in actions: action["group_plan_payload"] = payload
        return payload, actions
    source_actions = payload["change_manifest"]
    if not 1 <= len(source_actions) <= 3:
        _error("HOST_CHANGE_ACTION_SET_UNSUPPORTED",
               "bounded WFP mutation plan must contain one to three actions")
    normalized: list[dict[str, Any]] = []
    policy = trusted_profile.windows_isolation
    seen_ids: set[str] = set()
    seen_keys: set[tuple[str, str]] = set()
    for source in source_actions:
        kind, desired = source.get("resource_kind"), source.get("desired")
        if source.get("operation") != "CREATE" or kind not in {"wfp_provider", "wfp_sublayer", "wfp_filter"}:
            _error("HOST_CHANGE_ACTION_UNSUPPORTED",
                   "the whole plan was refused before effects because a resource kind/operation is unsupported")
        if not isinstance(desired, Mapping):
            _error("HOST_CHANGE_ACTION_UNSUPPORTED", "WFP action desired state is malformed")
        if kind == "wfp_provider":
            key = desired.get("provider_key")
        elif kind == "wfp_sublayer":
            key = desired.get("sublayer_key")
        else:
            key = desired.get("filter_key")
        try:
            key = "{" + str(uuid.UUID(guid_key(key))).upper() + "}"
        except (TypeError, ValueError, WindowsWfpMutationError):
            _error("HOST_CHANGE_WFP_SCOPE_MISMATCH", "WFP action resource key is invalid")
        scope_key = (kind, guid_key(key))
        if source["action_id"] in seen_ids or scope_key in seen_keys:
            _error("HOST_CHANGE_ACTION_SET_INVALID", "WFP plan contains duplicate action IDs or resource keys")
        seen_ids.add(source["action_id"])
        seen_keys.add(scope_key)
        action: dict[str, Any] = {
            "action_id": source["action_id"],
            "resource_name": source["resource_name"],
            "resource_kind": kind,
            "resource_key": key,
            "desired": dict(desired),
            "desired_identity_sha256": source["desired_identity_sha256"],
            "ownership_guid": source["ownership_guid"],
            "plan_sha256": plan.sha256,
        }
        if kind == "wfp_provider":
            if guid_key(key) != guid_key(policy.wfp_provider_key):
                _error("HOST_CHANGE_WFP_SCOPE_MISMATCH", "provider key differs from the trusted profile")
            action["expected_resource"] = CtypesWindowsWfpMutationApi._expected_provider_row(desired)
        elif kind == "wfp_sublayer":
            if (guid_key(key) != guid_key(policy.wfp_sublayer_key)
                    or guid_key(desired.get("provider_key")) != guid_key(policy.wfp_provider_key)):
                _error("HOST_CHANGE_WFP_SCOPE_MISMATCH", "sublayer/provider keys differ from the trusted profile")
            action["expected_resource"] = CtypesWindowsWfpMutationApi._expected_sublayer_row(desired)
        else:
            if (guid_key(desired.get("provider_key")) != guid_key(policy.wfp_provider_key)
                    or guid_key(desired.get("sublayer_key")) != guid_key(policy.wfp_sublayer_key)
                    or desired.get("action") != "PERMIT"):
                _error("HOST_CHANGE_WFP_SCOPE_MISMATCH",
                       "filter does not bind the exact configured provider/sublayer/action")
            matches = [grant for grant in policy.wfp_grants
                       if hashlib.sha256(_canonical(grant.as_dict()).encode("utf-8")).hexdigest()
                       == desired.get("grant_sha256")]
            if len(matches) != 1:
                _error("HOST_CHANGE_WFP_GRANT_MISMATCH", "filter does not bind one unique configured network grant")
            grant = normalize_endpoint_grant(matches[0], policy=policy,
                                             launch_images=trusted_profile.launch_images)
            action.update({"filter_key": key,
                           "provider_key": desired["provider_key"],
                           "sublayer_key": desired["sublayer_key"],
                           "grant": grant,
                           "grant_sha256": desired["grant_sha256"]})
        normalized.append(action)

    kinds = [item["resource_kind"] for item in normalized]
    if len(set(kinds)) != len(kinds):
        _error("HOST_CHANGE_ACTION_SET_INVALID", "a plan may contain at most one action of each WFP resource kind")
    if "wfp_provider" in kinds or "wfp_sublayer" in kinds:
        if not {"wfp_provider", "wfp_sublayer"}.issubset(kinds):
            _error("HOST_CHANGE_ACTION_SET_UNSUPPORTED",
                   "provider and sublayer must be created/restored as one dependency-bound plan")
    order = {"wfp_provider": 0, "wfp_sublayer": 1, "wfp_filter": 2}
    normalized.sort(key=lambda item: order[item["resource_kind"]])
    if "wfp_filter" in kinds:
        filter_action = next(item for item in normalized if item["resource_kind"] == "wfp_filter")
        if (guid_key(filter_action["provider_key"]) != guid_key(policy.wfp_provider_key)
                or guid_key(filter_action["sublayer_key"]) != guid_key(policy.wfp_sublayer_key)):
            _error("HOST_CHANGE_WFP_SCOPE_MISMATCH", "filter dependency keys differ from the exact WFP policy")
    return payload, tuple(normalized)


def _normalize_supported_plan(plan: Any, trusted_profile: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Compatibility accessor for the earlier single-filter scoped tests."""
    payload, actions = _normalize_supported_actions(plan, trusted_profile)
    if len(actions) != 1 or actions[0]["resource_kind"] != "wfp_filter":
        _error("HOST_CHANGE_ACTION_SET_UNSUPPORTED",
               "this helper expects exactly one configured WFP filter action")
    return payload, dict(actions[0])


def _validated_wfp_snapshot(snapshot: Any) -> dict[str, Any]:
    if not isinstance(snapshot, Mapping) or snapshot.get("state") != "COMPLETE_READ_ONLY":
        _error("HOST_CHANGE_WFP_SNAPSHOT_INCOMPLETE", "complete bounded WFP read-only snapshot is required before mutation")
    limits, counts = snapshot.get("limits"), snapshot.get("counts")
    expected_limits = {"max_page_size": wfp_native.MAX_ENUM_PAGE,
                       "max_pages_per_kind": wfp_native.MAX_ENUM_PAGES,
                       "max_total_pages": wfp_native.MAX_ENUM_PAGES * 4,
                       "max_total_records": wfp_native.MAX_ENUM_ROWS,
                       "max_total_bytes": wfp_native.MAX_ENUM_TOTAL_BYTES,
                       "max_elapsed_ms": wfp_native.MAX_ENUM_SECONDS * 1000}
    count_fields = {"providers", "sublayers", "filters", "provider_contexts", "conditions", "pages",
                    "pages_by_kind", "total_records", "total_bytes", "elapsed_ms"}
    if (not isinstance(limits, Mapping) or dict(limits) != expected_limits
            or not isinstance(counts, Mapping) or set(counts) != count_fields
            or not isinstance(counts.get("pages_by_kind"), Mapping)
            or set(counts["pages_by_kind"]) != {"Provider", "SubLayer", "Filter", "ProviderContext"}
            or any(type(counts[key]) is not int or counts[key] < 0
                   for key in count_fields - {"pages_by_kind"})
            or any(type(value) is not int or value < 1 or value > wfp_native.MAX_ENUM_PAGES
                   for value in counts["pages_by_kind"].values())
            or counts["total_records"] > min(_MAX_SNAPSHOT_ROWS, expected_limits["max_total_records"])
            or counts["total_bytes"] > expected_limits["max_total_bytes"]
            or counts["elapsed_ms"] > expected_limits["max_elapsed_ms"]
            or counts["pages"] > expected_limits["max_total_pages"]
            or sum(counts["pages_by_kind"].values()) != counts["pages"]
            or not _is_sha(snapshot.get("snapshot_sha256"))):
        _error("HOST_CHANGE_WFP_SNAPSHOT_INVALID", "WFP snapshot lacks valid completeness bounds or digest")
    if any(counts["pages_by_kind"][kind] <= 0
           for kind in ("Provider", "SubLayer", "Filter", "ProviderContext")):
        _error("HOST_CHANGE_WFP_SNAPSHOT_INVALID", "complete WFP snapshot must include every enumeration")
    arrays = {key: snapshot.get(key) for key in
              ("providers", "sublayers", "filters", "provider_contexts")}
    if (any(not isinstance(rows, list) for rows in arrays.values())
            or sum(len(rows) for rows in arrays.values()) != counts["total_records"]
            or any(not isinstance(item, Mapping) for rows in arrays.values() for item in rows)):
        _error("HOST_CHANGE_WFP_SNAPSHOT_INVALID", "WFP snapshot row sets do not match the bounded total")
    try:
        canonical_snapshot = {
            "providers": sorted(arrays["providers"], key=lambda row: row.get("provider_key", "")),
            "sublayers": sorted(arrays["sublayers"], key=lambda row: row.get("sublayer_key", "")),
            "filters": sorted(arrays["filters"], key=lambda row: (row.get("layer_key", ""),
                                                                   row.get("filter_id", -1))),
            "provider_contexts": arrays["provider_contexts"],
        }
        canonical_bytes = _canonical(canonical_snapshot).encode("utf-8")
        observed_snapshot_sha = hashlib.sha256(canonical_bytes).hexdigest()
    except (TypeError, ValueError, KeyError) as exc:
        _error("HOST_CHANGE_WFP_SNAPSHOT_INVALID",
               "WFP snapshot rows cannot be canonically fingerprinted",
               details={"cause_type": type(exc).__name__})
    if (observed_snapshot_sha != snapshot["snapshot_sha256"]
            or len(canonical_bytes) != counts["total_bytes"]):
        _error("HOST_CHANGE_WFP_SNAPSHOT_TAMPERED",
               "WFP snapshot fingerprint or byte count does not bind its exact returned rows")
    if any(not isinstance(row, Mapping) or set(row) != {"state"}
           or row.get("state") != "PRESENT_UNCLASSIFIED" for row in arrays["provider_contexts"]):
        _error("HOST_CHANGE_WFP_SNAPSHOT_INVALID", "provider-context rows are not opaque/unclassified records")
    return {**arrays, "snapshot_sha256": snapshot["snapshot_sha256"],
            "reasons": list(snapshot.get("reasons", []))[:256],
            "counts": dict(counts), "limits": dict(limits)}


def _snapshot_dependencies(snapshot: Any, profile: Any, action: Mapping[str, Any], *,
                           require_filter_absent: bool) -> dict[str, Any]:
    rows = _validated_wfp_snapshot(snapshot)
    providers, sublayers, filters = rows["providers"], rows["sublayers"], rows["filters"]
    policy = profile.windows_isolation
    for resource_rows, key_name, label in ((providers, "provider_key", "provider"),
                                           (sublayers, "sublayer_key", "sublayer"),
                                           (filters, "filter_key", "filter")):
        try:
            keys = [guid_key(row.get(key_name)) for row in resource_rows]
        except WindowsWfpMutationError as exc:
            _error("HOST_CHANGE_WFP_SNAPSHOT_INVALID",
                   f"WFP snapshot contains an invalid {label} key",
                   details={"cause_code": exc.code})
        if len(keys) != len(set(keys)):
            _error("HOST_CHANGE_WFP_SNAPSHOT_INVALID",
                   f"WFP snapshot contains duplicate {label} keys")
    provider_matches = [row for row in providers
                        if guid_key(row.get("provider_key")) == guid_key(policy.wfp_provider_key)]
    sublayer_matches = [row for row in sublayers
                        if guid_key(row.get("sublayer_key")) == guid_key(policy.wfp_sublayer_key)]
    if len(provider_matches) != 1 or len(sublayer_matches) != 1:
        _error("HOST_CHANGE_WFP_DEPENDENCY_MISSING", "the configured provider and sublayer must each exist exactly once")
    provider, sublayer = provider_matches[0], sublayer_matches[0]
    if (provider.get("flags") != 0 or sublayer.get("flags") != 0
            or guid_key(sublayer.get("provider_key")) != guid_key(policy.wfp_provider_key)
            or not _is_sha(provider.get("security_sha256"))
            or not _is_sha(sublayer.get("security_sha256"))
            or not _is_sha(provider.get("provider_data_sha256"))
            or not _is_sha(sublayer.get("provider_data_sha256"))):
        _error("HOST_CHANGE_WFP_DEPENDENCY_UNSUPPORTED",
               "provider or sublayer carries unknown flags, owner, or identity fields")
    dependency = {"provider": dict(provider), "sublayer": dict(sublayer)}
    target_rows = [row for row in filters
                   if guid_key(row.get("filter_key")) == guid_key(action["filter_key"])]
    if len(target_rows) > 1:
        _error("HOST_CHANGE_WFP_FILTER_DUPLICATE", "WFP snapshot contains duplicate exact filter keys")
    if require_filter_absent and target_rows:
        _error("HOST_CHANGE_WFP_FILTER_EXISTS", "configured filter key already exists; no overwrite is permitted")
    return {
        "schema": "comsol-mcp.windows-wfp-dependency-baseline/1",
        "snapshot_sha256": rows["snapshot_sha256"],
        "dependency_sha256": sha256(dependency),
        "provider": dict(provider), "sublayer": dict(sublayer),
        "target_filter_sha256": sha256(dict(target_rows[0])) if target_rows else None,
        "target_filter_present": bool(target_rows),
        "effective_policy_state": "UNVERIFIED",
        "wfp_snapshot_reasons": rows["reasons"],
    }


def _snapshot_sequence_dependencies(snapshot: Any, profile: Any,
                                    actions: tuple[Mapping[str, Any], ...], *,
                                    phase: str,
                                    expected_resources: Mapping[str, str] | None = None,
                                    admitted_plan_sha256: str | None = None) -> dict[str, Any]:
    rows = _validated_wfp_snapshot(snapshot)
    providers, sublayers, filters = rows["providers"], rows["sublayers"], rows["filters"]
    policy = profile.windows_isolation
    for values, key_name, label in ((providers, "provider_key", "provider"),
                                    (sublayers, "sublayer_key", "sublayer"),
                                    (filters, "filter_key", "filter")):
        try:
            keys = [guid_key(item.get(key_name)) for item in values]
        except WindowsWfpMutationError as exc:
            _error("HOST_CHANGE_WFP_SNAPSHOT_INVALID", f"WFP {label} key is malformed",
                   details={"cause_code": exc.code})
        if len(set(keys)) != len(keys):
            _error("HOST_CHANGE_WFP_SNAPSHOT_INVALID", f"WFP snapshot contains duplicate {label} keys")
    provider_by_key = {guid_key(row["provider_key"]): dict(row) for row in providers}
    sublayer_by_key = {guid_key(row["sublayer_key"]): dict(row) for row in sublayers}
    filter_by_key = {guid_key(row["filter_key"]): dict(row) for row in filters}
    by_kind = {item["resource_kind"]: item for item in actions}
    provider_key = guid_key(policy.wfp_provider_key)
    sublayer_key = guid_key(policy.wfp_sublayer_key)
    provider_action = by_kind.get("wfp_provider")
    sublayer_action = by_kind.get("wfp_sublayer")
    filter_action = by_kind.get("wfp_filter")
    if phase == "APPLY":
        for action in actions:
            key = guid_key(action["resource_key"])
            existing = {"wfp_provider": provider_by_key, "wfp_sublayer": sublayer_by_key,
                        "wfp_filter": filter_by_key}[action["resource_kind"]].get(key)
            if existing is not None:
                _error("HOST_CHANGE_WFP_RESOURCE_EXISTS",
                       "configured WFP key already exists; CREATE never overwrites a resource",
                       details={"resource_kind": action["resource_kind"], "resource_key": action["resource_key"]})
    else:
        if not isinstance(expected_resources, Mapping) or set(expected_resources) != {
                action["action_id"] for action in actions}:
            _error("HOST_CHANGE_RECEIPT_MISMATCH", "restore resource hashes do not match the exact plan actions")
        for action in actions:
            key = guid_key(action["resource_key"])
            existing = {"wfp_provider": provider_by_key, "wfp_sublayer": sublayer_by_key,
                        "wfp_filter": filter_by_key}[action["resource_kind"]].get(key)
            if existing is None or sha256(existing) != expected_resources[action["action_id"]]:
                _error("HOST_CHANGE_RESTORE_DRIFT",
                       "one receipt-bound WFP resource is absent or changed; no restore effects were started",
                       details={"resource_kind": action["resource_kind"], "resource_key": action["resource_key"]})
            expected = action.get("expected_resource")
            if expected is not None and sha256(existing) != sha256(expected):
                _error("HOST_CHANGE_RESTORE_DRIFT", "WFP resource differs from its exact planned desired state")

    if provider_action:
        provider_expected = provider_action["expected_resource"]
        if phase == "APPLY" and provider_key in provider_by_key:
            _error("HOST_CHANGE_WFP_RESOURCE_EXISTS", "configured provider key already exists")
    else:
        provider_expected = provider_by_key.get(provider_key)
        if provider_expected is None:
            _error("HOST_CHANGE_WFP_DEPENDENCY_MISSING", "configured WFP provider must exist before filter mutation")
        if provider_expected.get("flags") not in {0, 1}:
            _error("HOST_CHANGE_WFP_DEPENDENCY_UNSUPPORTED", "WFP provider has unsupported flag bits")
    if sublayer_action:
        sublayer_expected = sublayer_action["expected_resource"]
        if phase == "APPLY" and sublayer_key in sublayer_by_key:
            _error("HOST_CHANGE_WFP_RESOURCE_EXISTS", "configured sublayer key already exists")
    else:
        sublayer_expected = sublayer_by_key.get(sublayer_key)
        if sublayer_expected is None:
            _error("HOST_CHANGE_WFP_DEPENDENCY_MISSING", "configured WFP sublayer must exist before filter mutation")
        if sublayer_expected.get("flags") not in {0, 1}:
            _error("HOST_CHANGE_WFP_DEPENDENCY_UNSUPPORTED", "WFP sublayer has unsupported flag bits")
    if (guid_key(sublayer_expected.get("provider_key")) != provider_key
            or not _is_sha(provider_expected.get("security_sha256"))
            or not _is_sha(sublayer_expected.get("security_sha256"))
            or not _is_sha(provider_expected.get("provider_data_sha256"))
            or not _is_sha(sublayer_expected.get("provider_data_sha256"))):
        _error("HOST_CHANGE_WFP_DEPENDENCY_UNSUPPORTED", "configured provider/sublayer identity is incomplete")
    if filter_action:
        filter_key = guid_key(filter_action["resource_key"])
        existing_filter = filter_by_key.get(filter_key)
        if phase == "APPLY" and existing_filter is not None:
            _error("HOST_CHANGE_WFP_RESOURCE_EXISTS", "configured filter key already exists")
        if phase == "RESTORE":
            planned_filter_keys = {guid_key(a["resource_key"]) for a in actions if a["resource_kind"] == "wfp_filter"}
            if provider_action and any(guid_key(row.get("provider_key")) == provider_key
                                       and guid_key(row.get("filter_key")) not in planned_filter_keys
                                       for row in filters):
                _error("HOST_CHANGE_WFP_PROVIDER_CHILDREN_PRESENT", "foreign filter references the provider")
            if sublayer_action and any(guid_key(row.get("sublayer_key")) == sublayer_key
                                       and guid_key(row.get("filter_key")) not in planned_filter_keys
                                       for row in filters):
                _error("HOST_CHANGE_WFP_SUBLAYER_CHILDREN_PRESENT", "foreign filter references the sublayer")
    if phase == "RESTORE" and (provider_action or sublayer_action):
        if rows["provider_contexts"]:
            _error("HOST_CHANGE_WFP_CHILDREN_UNCLASSIFIED",
                   "provider contexts are present; parent-resource ownership cannot be classified")
        planned_filter_keys = {guid_key(a["resource_key"]) for a in actions if a["resource_kind"] == "wfp_filter"}
        planned_sublayer_keys = {guid_key(a["resource_key"]) for a in actions
                                 if a["resource_kind"] == "wfp_sublayer"}
        if provider_action and any(row.get("provider_key") is not None
                                   and guid_key(row["provider_key"]) == provider_key
                                   and guid_key(row["sublayer_key"]) not in planned_sublayer_keys
                                   for row in sublayers):
            _error("HOST_CHANGE_WFP_PROVIDER_CHILDREN_PRESENT", "a foreign sublayer still references the provider")
        if sublayer_action and any(guid_key(row["sublayer_key"]) == sublayer_key
                                   and guid_key(row["filter_key"]) not in planned_filter_keys for row in filters):
            _error("HOST_CHANGE_WFP_SUBLAYER_CHILDREN_PRESENT", "a foreign filter references the sublayer")
        if provider_action and any(row.get("provider_key") is not None
                                   and guid_key(row["provider_key"]) == provider_key
                                   and guid_key(row["filter_key"]) not in planned_filter_keys
                                   for row in filters):
            _error("HOST_CHANGE_WFP_PROVIDER_CHILDREN_PRESENT", "a foreign filter references the provider")
        if sublayer_action and any(row.get("provider_key") is not None
                                   and guid_key(row["provider_key"]) == provider_key
                                   and guid_key(row["sublayer_key"]) != sublayer_key for row in sublayers):
            _error("HOST_CHANGE_WFP_PROVIDER_CHILDREN_PRESENT", "a foreign sublayer references the provider")
    dependency = {"provider": dict(provider_expected), "sublayer": dict(sublayer_expected)}
    dependency_sha = sha256(dependency)
    step_rows = []
    ordered = actions if phase == "APPLY" else tuple(reversed(actions))
    for index, action in enumerate(ordered):
        step_rows.append({"step_index": index, "action_id": action["action_id"],
                          "resource_kind": action["resource_kind"],
                          "resource_key": action["resource_key"],
                          "identity_sha256": action["desired_identity_sha256"],
                          "dependency_sha256": dependency_sha,
                          "expected_resource_sha256": (expected_resources[action["action_id"]]
                                                        if phase == "RESTORE" else None)})
    from ._windows_filter_group import SCHEMA
    group = any(item.get("desired", {}).get("schema") == SCHEMA for item in actions)
    sequence = {"schema": "comsol-mcp.host-control-mutation-sequence/3" if group else "comsol-mcp.host-control-mutation-sequence/2",
                "phase": phase,
                "plan_sha256": admitted_plan_sha256 or actions[0]["plan_sha256"],
                "steps": step_rows}
    return {**({"filter_group_plan": actions[0]["group_plan_payload"]} if group else {}),
            "schema": "comsol-mcp.windows-wfp-plan-baseline/2",
            "snapshot_sha256": rows["snapshot_sha256"],
            "dependency_sha256": dependency_sha,
            "provider": dict(provider_expected), "sublayer": dict(sublayer_expected),
            "target_filter_sha256": (sha256(filter_by_key[guid_key(filter_action["resource_key"])])
                                     if filter_action and guid_key(filter_action["resource_key"]) in filter_by_key
                                     else None),
            "effective_policy_state": "UNVERIFIED",
            "wfp_snapshot_reasons": rows["reasons"],
            "mutation_sequence": sequence}


def _receipt_payload(plan: Any, profile: Any, actions: tuple[Mapping[str, Any], ...]) -> dict[str, Any]:
    return {"schema": "comsol-mcp.applied-host-change-receipt/1",
            "plan_sha256": plan.sha256, "host_id": profile.host_id,
            "platform": profile.platform, "actions": [dict(item) for item in actions]}


def _receipt_payload_v2(plan: Any, profile: Any,
                        actions: tuple[Mapping[str, Any], ...]) -> dict[str, Any]:
    from ._windows_filter_group import is_group
    schema = "comsol-mcp.applied-host-change-receipt/3" if is_group(plan.as_dict()["change_manifest"]) else "comsol-mcp.applied-host-change-receipt/2"
    return {"schema": schema,
            "plan_sha256": plan.sha256, "host_id": profile.host_id,
            "platform": profile.platform, "actions": [dict(item) for item in actions]}


def _verify_receipt(plan: Any, profile: Any, receipt: Any) -> tuple[dict[str, Any], dict[str, Any], str]:
    payload, source_action = _normalize_supported_plan(plan, profile)
    if not isinstance(receipt, resources.AppliedHostChangeReceipt):
        _error("HOST_CHANGE_RECEIPT_INVALID", "restore requires a typed exact applied receipt")
    body = _receipt_payload(plan, profile, tuple(receipt.actions))
    if (receipt.plan_sha256 != plan.sha256 or receipt.host_id != profile.host_id
            or receipt.platform != "windows" or not _is_sha(receipt.receipt_sha256)
            or hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest() != receipt.receipt_sha256
            or len(receipt.actions) != 1):
        _error("HOST_CHANGE_RECEIPT_MISMATCH", "applied receipt does not bind this exact source plan/host")
    item = receipt.actions[0]
    if (not isinstance(item, Mapping) or set(item) != {
            "action_id", "created_resource_sha256", "prior_resource_sha256", "prior_backup_path"}
            or item.get("action_id") != source_action["action_id"]
            or not _is_sha(item.get("created_resource_sha256"))
            or item.get("prior_resource_sha256") is not None
            or item.get("prior_backup_path") is not None):
        _error("HOST_CHANGE_RECEIPT_MISMATCH", "restore receipt does not describe this exact CREATE action")
    return payload, source_action, item["created_resource_sha256"]


def _verify_sequence_receipt(plan: Any, profile: Any, receipt: Any
                             ) -> tuple[dict[str, Any], tuple[dict[str, Any], ...], dict[str, str]]:
    _payload, actions = _normalize_supported_actions(plan, profile)
    if not isinstance(receipt, resources.AppliedHostChangeReceipt):
        _error("HOST_CHANGE_RECEIPT_INVALID", "restore requires a typed exact applied receipt")
    items = tuple(receipt.actions)
    if len(items) != len(actions):
        _error("HOST_CHANGE_RECEIPT_MISMATCH", "receipt action count differs from its source plan")
    expected_items = []
    hashes: dict[str, str] = {}
    for action, item in zip(actions, items, strict=True):
        if (not isinstance(item, Mapping) or set(item) != {
                "action_id", "resource_kind", "resource_key", "created_resource_sha256",
                "prior_resource_sha256", "prior_backup_path"}
                or item.get("action_id") != action["action_id"]
                or item.get("resource_kind") != action["resource_kind"]
                or guid_key(item.get("resource_key")) != guid_key(action["resource_key"])
                or not _is_sha(item.get("created_resource_sha256"))
                or item.get("prior_resource_sha256") is not None
                or item.get("prior_backup_path") is not None):
            _error("HOST_CHANGE_RECEIPT_MISMATCH", "receipt does not bind the exact ordered CREATE resources")
        expected_items.append(dict(item))
        hashes[action["action_id"]] = item["created_resource_sha256"]
    body = _receipt_payload_v2(plan, profile, tuple(expected_items))
    if (receipt.plan_sha256 != plan.sha256 or receipt.host_id != profile.host_id
            or receipt.platform != "windows" or not _is_sha(receipt.receipt_sha256)
            or hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest() != receipt.receipt_sha256):
        _error("HOST_CHANGE_RECEIPT_MISMATCH", "receipt digest differs from its exact source plan, host and rows")
    return _payload, actions, hashes


@dataclass(frozen=True)
class _ExecutionAdmission:
    operation_id: str
    job_id: str
    admission_id: str


class WindowsHostChangeExecutor:
    """Internal apply/restore lifecycle bound to trusted profile and provider."""

    def __init__(self, *, store: OperationStore, authority: ProjectAuthority,
                 profile: Any, api: Any, software_only: bool,
                 _construction_token: object | None = None,
                 _trusted_profile_load_origin: _TrustedProfileLoadOrigin | None = None):
        if (type(store) is not OperationStore or type(authority) is not ProjectAuthority
                or authority.store is not store or type(profile) is not resources.HostResourceProfile
                or type(authority.authorization_verifier) is not HostScopedProvider):
            _error("HOST_CHANGE_TRUSTED_COMPONENT_REQUIRED",
                   "host mutation requires the existing OperationStore, ProjectAuthority, startup profile and pinned provider")
        resources.validate_profile(profile)
        if profile.platform != "windows" or profile.windows_isolation is None:
            _error("HOST_CHANGE_WINDOWS_PROFILE_REQUIRED", "host mutation requires exact Windows isolation profile v2")
        if software_only and (_construction_token is not _SOFTWARE_TEST_TOKEN or api is None):
            _error("HOST_CHANGE_TEST_API_REQUIRED",
                   "synthetic protocol execution requires the private software-test entry point")
        if not software_only and _construction_token is not _NATIVE_PROFILE_LOADER_TOKEN:
            _error("HOST_CHANGE_TRUSTED_PROFILE_LOADER_REQUIRED",
                   "native host mutation construction must come from the trusted startup profile loader")
        if not software_only and type(api) is not CtypesWindowsWfpMutationApi:
            _error("HOST_CHANGE_NATIVE_API_REQUIRED", "production execution requires the fixed native WFP adapter")
        if software_only:
            if _trusted_profile_load_origin is not None:
                _error("HOST_CHANGE_PROFILE_LOAD_ORIGIN_MISMATCH",
                       "software-only executors cannot carry a native trusted-loader origin")
        elif (type(_trusted_profile_load_origin) is not _TrustedProfileLoadOrigin
              or _trusted_profile_load_origin._seal is not _NATIVE_PROFILE_LOADER_TOKEN
              or _trusted_profile_load_origin.profile_wrapper.host_resource_profile is not profile):
            _error("HOST_CHANGE_PROFILE_LOAD_ORIGIN_REQUIRED",
                   "native execution must retain the exact profile returned by its trusted loader")
        self.store, self.authority, self.profile = store, authority, profile
        self.provider = authority.authorization_verifier
        self.api = api
        self.software_only = software_only
        self._trusted_profile_load_origin = _trusted_profile_load_origin
        self._software_test_seal = (_SOFTWARE_TEST_TOKEN if software_only else None)

    @classmethod
    def from_trusted_profile_path(cls, *, store: OperationStore, authority: ProjectAuthority,
                                  trusted_profile_path: str | os.PathLike[str]) -> "WindowsHostChangeExecutor":
        if os.name != "nt":
            _error("HOST_CHANGE_WINDOWS_REQUIRED", "native host mutation is available only on Windows")
        from ._windows_t037 import load_trusted_profile, WindowsT037ProfileV2
        loaded = load_trusted_profile(trusted_profile_path)
        if type(loaded) is not WindowsT037ProfileV2:
            _error("HOST_CHANGE_PROFILE_V2_REQUIRED", "v1 intent-only profile cannot authorize a Windows host mutation")
        profile = loaded.host_resource_profile
        _current_windows_binding(profile)
        origin = _TrustedProfileLoadOrigin(
            wrapper=loaded, profile_path=trusted_profile_path,
            seal=_NATIVE_PROFILE_LOADER_TOKEN,
        )
        return cls(store=store, authority=authority, profile=profile,
                   api=CtypesWindowsWfpMutationApi(), software_only=False,
                   _construction_token=_NATIVE_PROFILE_LOADER_TOKEN,
                   _trusted_profile_load_origin=origin)

    @classmethod
    def _for_software_test(cls, *, store: OperationStore, authority: ProjectAuthority,
                           profile: Any, api: Any, token: object) -> "WindowsHostChangeExecutor":
        if token is not _SOFTWARE_TEST_TOKEN:
            _error("HOST_CHANGE_TEST_TOKEN_REQUIRED", "synthetic WFP API is accepted only by the private test entry point")
        return cls(store=store, authority=authority, profile=profile,
                   api=api, software_only=True,
                   _construction_token=_SOFTWARE_TEST_TOKEN)

    def _preflight(self, plan: Any) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        payload, action = _normalize_supported_plan(plan, self.profile)
        if not self.software_only:
            current = _current_windows_binding(self.profile)
        else:
            current = {"host_id": self.profile.host_id,
                       "host_fingerprint_sha256": self.profile.windows_isolation.host_fingerprint_sha256,
                       "root_identity": {"device": self.profile.root.volume_id,
                                         "inode": self.profile.root.file_id},
                       "root_security_sha256": None,
                       "service_token_sid": self.profile.windows_isolation.service_logon_sid,
                       "evidence_classification": "SOFTWARE_ONLY"}
        snapshot = self.api.observe_snapshot()
        baseline = _snapshot_dependencies(snapshot, self.profile, action, require_filter_absent=True)
        baseline["current_binding"] = current
        return payload, action, baseline

    def _preflight_sequence(self, plan: Any
                            ) -> tuple[dict[str, Any], tuple[dict[str, Any], ...], dict[str, Any], bool]:
        payload, actions = _normalize_supported_actions(plan, self.profile)
        use_v2 = len(actions) > 1 or actions[0]["resource_kind"] != "wfp_filter"
        if not use_v2:
            old_payload, action, baseline = self._preflight(plan)
            return old_payload, (action,), baseline, False
        if not self.software_only:
            current = _current_windows_binding(self.profile)
            validate_security = getattr(self.api, "preflight_security_descriptors", None)
            if not callable(validate_security):
                _error("HOST_CHANGE_WFP_SECURITY_PREFLIGHT_UNAVAILABLE",
                       "native WFP adapter cannot validate exact planned security descriptors before admission")
            validate_security(actions)
        else:
            current = {"host_id": self.profile.host_id,
                       "host_fingerprint_sha256": self.profile.windows_isolation.host_fingerprint_sha256,
                       "root_identity": {"device": self.profile.root.volume_id,
                                         "inode": self.profile.root.file_id},
                       "root_security_sha256": None,
                       "service_token_sid": self.profile.windows_isolation.service_logon_sid,
                       "evidence_classification": "SOFTWARE_ONLY"}
        snapshot = self.api.observe_snapshot()
        baseline = _snapshot_sequence_dependencies(snapshot, self.profile, actions,
                                                   phase="APPLY", admitted_plan_sha256=plan.sha256)
        baseline["current_binding"] = current
        if len(_canonical(baseline).encode("utf-8")) > resources.MAX_HOST_PLAN_BYTES:
            _error("HOST_CHANGE_GROUP_BYTES_UNSUPPORTED", "whole baseline exceeds the original bounded plan budget")
        return payload, actions, baseline, True

    def _admit(self, *, phase: str, plan_sha256: str, approval: resources.HostChangeApproval,
               baseline: dict[str, Any], source_plan_sha256: str | None = None,
               source_receipt_sha256: str | None = None) -> _ExecutionAdmission:
        if (not isinstance(approval, resources.HostChangeApproval)
                or approval.plan_sha256 != plan_sha256
                or not isinstance(approval.authorization_ref, str)
                or not approval.authorization_ref):
            _error("HOST_CHANGE_APPROVAL_MISMATCH", "authorization must bind the exact apply/restore plan fingerprint")
        try:
            operation, admission = self.store.begin_host_control_operation(
                authority=self.authority, provider=self.provider, profile=self.profile,
                phase=phase, plan_sha256=plan_sha256,
                authorization_ref=approval.authorization_ref, baseline=baseline,
                source_plan_sha256=source_plan_sha256,
                source_receipt_sha256=source_receipt_sha256,
            )
        except resources.ResourcePolicyError as exc:
            if exc.code in {"HOST_CONTROL_ADMISSION_READBACK_UNKNOWN",
                            "HOST_CONTROL_ADMISSION_UNKNOWN"}:
                _error("HOST_CHANGE_ADMISSION_UNKNOWN",
                       "host operation admission outcome is unknown; do not replay this authorization reference",
                       safe_retry=False, stage="execution",
                       details={"cause_code": exc.code})
            raise
        job_id = operation.get("job_id")
        if (not isinstance(job_id, str) or admission.get("state") != "ADMITTED"
                or operation.get("status") != "QUEUED"
                or operation.get("operation") != f"host.change.{phase.lower()}"
                or operation.get("operation_id") != admission.get("operation_id")
                or operation.get("job_id") != admission.get("job_id")):
            _error("HOST_CHANGE_ADMISSION_UNKNOWN", "committed host admission did not read back its exact job/state",
                   safe_retry=False, stage="execution")
        accepted_admission = _ExecutionAdmission(operation["operation_id"], job_id, admission["admission_id"])
        callback = getattr(self, "_on_original_host_admission", None)
        if callback is not None: callback(self, accepted_admission)
        return accepted_admission

    def _start(self, admission: _ExecutionAdmission) -> None:
        if self.store.host_control_admission_state(admission.admission_id) != "ADMITTED":
            _error("HOST_CHANGE_ADMISSION_STATE_UNKNOWN",
                   "durable host admission is not in the initial state before dispatch",
                   safe_retry=False, stage="execution")
        self.store.update_job(admission.job_id, "RUNNING")
        operation = self.store.get_operation(admission.operation_id)
        job = self.store.job(admission.job_id)
        if (operation is None or job is None or operation.get("status") != "RUNNING"
                or job.get("status") != "RUNNING"
                or self.store.host_control_admission_state(admission.admission_id) != "ADMITTED"):
            _error("HOST_CHANGE_START_UNKNOWN",
                   "host operation/job did not durably enter RUNNING before its mutation intent",
                   safe_retry=False, stage="execution")

    def _finish_unknown(self, admission: _ExecutionAdmission, *, phase: str,
                        action: Mapping[str, Any], cause: BaseException) -> None:
        event = "APPLY_INTENT" if phase == "APPLY" else "RESTORE_INTENT"
        try:
            payload = {"reason_code": getattr(cause, "code", type(cause).__name__)[:200],
                       "failed_event": event, "action_id": action["action_id"],
                       "filter_key": action["filter_key"]}
            self.store.append_host_control_mutation_event(
                admission_id=admission.admission_id, event="RECOVERY_REQUIRED", payload=payload)
        except BaseException:
            # The original durable intent already prevents replay. If SQLite
            # cannot record the recovery marker, retain the UNKNOWN operation.
            pass
        try:
            self.store.finish(admission.operation_id, status="UNKNOWN", result={
                "success": False, "operation": f"host.change.{phase.lower()}",
                "execution_state": "UNKNOWN", "host_change_state": "RECOVERY_REQUIRED",
                "effective_policy_state": "UNVERIFIED", "safe_retry": False,
                "cause_code": getattr(cause, "code", type(cause).__name__),
                "evidence_classification": "SOFTWARE_ONLY" if self.software_only else "WINDOWS_NATIVE_READBACK_ONLY",
            })
        except BaseException:
            pass

    def _finish_unknown_sequence(self, admission: _ExecutionAdmission, *, phase: str,
                                 action: Mapping[str, Any], step_index: int | None,
                                 cause: BaseException,
                                 result: dict[str, Any] | None = None) -> None:
        if step_index is not None:
            event = f"{phase}_STEP_INTENT"
            try:
                self.store.append_host_control_mutation_step_event(
                    admission_id=admission.admission_id,
                    event="RECOVERY_REQUIRED",
                    payload={"reason_code": getattr(cause, "code", type(cause).__name__)[:200],
                             "failed_event": event, "step_index": step_index,
                             "action_id": action["action_id"],
                             "resource_kind": action["resource_kind"],
                             "resource_key": action["resource_key"]})
            except BaseException:
                # A committed V2 intent already fences replay even when the
                # recovery annotation itself cannot be persisted.
                pass
        try:
            unknown_result = result if result is not None else {
                "success": False, "operation": f"host.change.{phase.lower()}",
                "execution_state": "UNKNOWN", "host_change_state": "RECOVERY_REQUIRED",
                "effective_policy_state": "UNVERIFIED", "safe_retry": False,
                "cause_code": getattr(cause, "code", type(cause).__name__),
                "evidence_classification": "SOFTWARE_ONLY" if self.software_only
                    else "WINDOWS_NATIVE_READBACK_ONLY",
            }
            if (not isinstance(unknown_result, dict)
                    or unknown_result.get("success") is not False
                    or unknown_result.get("execution_state") != "UNKNOWN"
                    or unknown_result.get("host_change_state") != "RECOVERY_REQUIRED"
                    or unknown_result.get("safe_retry") is not False):
                raise ValueError("custom recovery result must remain UNKNOWN and non-retryable")
            self.store.finish(admission.operation_id, status="UNKNOWN",
                              result=dict(unknown_result))
        except BaseException:
            pass

    def _apply_sequence(self, plan: Any, actions: tuple[Mapping[str, Any], ...],
                        baseline: Mapping[str, Any], *,
                        approval: resources.HostChangeApproval) -> resources.AppliedHostChangeReceipt:
        admission = self._admit(phase="APPLY", plan_sha256=plan.sha256,
                                approval=approval, baseline=dict(baseline))
        created: dict[str, dict[str, Any]] = {}
        active_index: int | None = None
        active_action: Mapping[str, Any] = actions[0]
        try:
            self._start(admission)
            for index, action in enumerate(actions):
                active_index, active_action = index, action
                intent = {"step_index": index, "action_id": action["action_id"],
                          "resource_kind": action["resource_kind"],
                          "resource_key": action["resource_key"],
                          "desired_identity_sha256": action["desired_identity_sha256"],
                          "dependency_sha256": baseline["dependency_sha256"]}
                self.store.append_host_control_mutation_step_event(
                    admission_id=admission.admission_id, event="APPLY_STEP_INTENT", payload=intent)
                kind = action["resource_kind"]
                if kind == "wfp_provider":
                    row = self.api.create_provider(action=action)
                elif kind == "wfp_sublayer":
                    provider_action = next(item for item in actions if item["resource_kind"] == "wfp_provider")
                    provider_row = created[provider_action["action_id"]]
                    row = self.api.create_sublayer(
                        action=action, expected_provider_sha256=sha256(provider_row))
                elif kind == "wfp_filter":
                    provider_action = next(item for item in actions if item["resource_kind"] == "wfp_provider")
                    sublayer_action = next(item for item in actions if item["resource_kind"] == "wfp_sublayer")
                    dependency_sha = sha256({
                        "provider": created[provider_action["action_id"]],
                        "sublayer": created[sublayer_action["action_id"]],
                    })
                    if dependency_sha != baseline["dependency_sha256"]:
                        _error("HOST_CHANGE_WFP_DEPENDENCY_MISMATCH",
                               "created provider/sublayer do not match the full preflight dependency binding",
                               stage="execution")
                    row = self.api.create_filter(
                        profile=self.profile, action=action,
                        expected_dependency_sha256=dependency_sha)
                else:
                    _error("HOST_CHANGE_ACTION_UNSUPPORTED", "unsupported action escaped whole-plan preflight",
                           stage="execution")
                resource_sha = sha256(row)
                verified = {**intent, "created_resource_sha256": resource_sha}
                self.store.append_host_control_mutation_step_event(
                    admission_id=admission.admission_id,
                    event="APPLY_STEP_READBACK_VERIFIED", payload=verified)
                created[action["action_id"]] = dict(row)
                active_index = None

            if any(action.get("desired", {}).get("schema") == "comsol-mcp.windows-wfp-filter-group-member/1" for action in actions):
                _snapshot_sequence_dependencies(self.api.observe_snapshot(), self.profile, actions, phase="RESTORE",
                    expected_resources={action["action_id"]: sha256(created[action["action_id"]]) for action in actions})
            receipt_actions = tuple({
                "action_id": action["action_id"],
                "resource_kind": action["resource_kind"],
                "resource_key": action["resource_key"],
                "created_resource_sha256": sha256(created[action["action_id"]]),
                "prior_resource_sha256": None, "prior_backup_path": None,
            } for action in actions)
            receipt_body = _receipt_payload_v2(plan, self.profile, receipt_actions)
            receipt_sha = hashlib.sha256(_canonical(receipt_body).encode("utf-8")).hexdigest()
            receipt = resources.AppliedHostChangeReceipt(
                plan.sha256, self.profile.host_id, "windows", receipt_actions, receipt_sha)
            result = {"success": True, "operation": "host.change.apply",
                      "execution_state": "SUCCEEDED", "host_change_state": "APPLIED_UNVERIFIED",
                      "plan_sha256": plan.sha256, "profile_sha256": self.profile.sha256,
                      "applied_receipt": {**receipt_body, "receipt_sha256": receipt_sha},
                      "receipt_sha256": receipt_sha,
                      "effective_policy_state": "UNVERIFIED", "t037_state": "NOT_VERIFIED",
                      "safe_retry": False,
                      "evidence_classification": "SOFTWARE_ONLY" if self.software_only
                          else "WINDOWS_NATIVE_READBACK_ONLY"}
            accepted, authoritative = self.store.finish(
                admission.operation_id, status="SUCCEEDED", result=result)
            if not accepted or authoritative != "SUCCEEDED":
                _error("HOST_CHANGE_RESULT_PERSISTENCE_UNKNOWN",
                       "all WFP resources were read back but the durable terminal receipt is unknown",
                       safe_retry=False, stage="execution")
            return receipt
        except Exception as exc:
            self._finish_unknown_sequence(admission, phase="APPLY", action=active_action,
                                          step_index=active_index, cause=exc)
            _error("HOST_CHANGE_RECOVERY_REQUIRED",
                   "ordered WFP apply may have changed Windows state; automatic replay is forbidden",
                   safe_retry=False, stage="execution",
                   details={"cause_code": getattr(exc, "code", None) or type(exc).__name__,
                            "admission_id": admission.admission_id, "job_id": admission.job_id,
                            "step_index": active_index})

    def _restore_sequence(self, plan: Any, receipt: resources.AppliedHostChangeReceipt, *,
                          approval: resources.HostChangeApproval | None,
                          authorization_ref: str | None = None
                          ) -> dict[str, Any]:
        _payload, actions, expected_resources = _verify_sequence_receipt(plan, self.profile, receipt)
        if not self.software_only:
            current = _current_windows_binding(self.profile)
        else:
            current = {"host_id": self.profile.host_id,
                       "host_fingerprint_sha256": self.profile.windows_isolation.host_fingerprint_sha256,
                       "root_identity": {"device": self.profile.root.volume_id,
                                         "inode": self.profile.root.file_id},
                       "root_security_sha256": None,
                       "service_token_sid": self.profile.windows_isolation.service_logon_sid,
                       "evidence_classification": "SOFTWARE_ONLY"}
        snapshot = self.api.observe_snapshot()
        # Build the restore authorization fingerprint from the exact fresh
        # dependency snapshot, then bind that fingerprint into the admitted
        # reverse sequence.
        preliminary = _snapshot_sequence_dependencies(
            snapshot, self.profile, actions, phase="RESTORE",
            expected_resources=expected_resources, admitted_plan_sha256=plan.sha256)
        restore_body = {"schema": "comsol-mcp.host-change-restore/2",
                        "profile_sha256": self.profile.sha256,
                        "source_plan_sha256": plan.sha256,
                        "source_receipt_sha256": receipt.receipt_sha256,
                        "resource_hashes": [{"action_id": action["action_id"],
                                             "resource_kind": action["resource_kind"],
                                             "resource_key": action["resource_key"],
                                             "created_resource_sha256": expected_resources[action["action_id"]]}
                                            for action in actions],
                        "dependency_sha256": preliminary["dependency_sha256"],
                        "snapshot_sha256": preliminary["snapshot_sha256"]}
        restore_sha = hashlib.sha256(_canonical(restore_body).encode("utf-8")).hexdigest()
        if authorization_ref is not None:
            approval = resources.HostChangeApproval(restore_sha, authorization_ref)
        if not isinstance(approval, resources.HostChangeApproval) or approval.plan_sha256 != restore_sha:
            _error("HOST_CHANGE_APPROVAL_MISMATCH",
                   "authorization must bind the exact fresh restore plan and dependency snapshot")
        baseline = _snapshot_sequence_dependencies(
            snapshot, self.profile, actions, phase="RESTORE",
            expected_resources=expected_resources, admitted_plan_sha256=restore_sha)
        baseline["current_binding"] = current
        admission = self._admit(
            phase="RESTORE", plan_sha256=restore_sha, approval=approval, baseline=baseline,
            source_plan_sha256=plan.sha256, source_receipt_sha256=receipt.receipt_sha256)
        ordered = tuple(reversed(actions))
        active_index: int | None = None
        active_action: Mapping[str, Any] = ordered[0]
        try:
            self._start(admission)
            for index, action in enumerate(ordered):
                active_index, active_action = index, action
                resource_sha = expected_resources[action["action_id"]]
                intent = {"step_index": index, "action_id": action["action_id"],
                          "resource_kind": action["resource_kind"],
                          "resource_key": action["resource_key"],
                          "expected_resource_sha256": resource_sha,
                          "dependency_sha256": baseline["dependency_sha256"]}
                self.store.append_host_control_mutation_step_event(
                    admission_id=admission.admission_id, event="RESTORE_STEP_INTENT", payload=intent)
                kind = action["resource_kind"]
                if kind == "wfp_filter":
                    self.api.delete_filter(profile=self.profile, action=action,
                                           expected_resource_sha256=resource_sha,
                                           expected_dependency_sha256=baseline["dependency_sha256"])
                elif kind == "wfp_sublayer":
                    self.api.delete_sublayer(action=action, expected_resource_sha256=resource_sha)
                elif kind == "wfp_provider":
                    self.api.delete_provider(action=action, expected_resource_sha256=resource_sha)
                else:
                    _error("HOST_CHANGE_ACTION_UNSUPPORTED", "unsupported action escaped whole-plan preflight",
                           stage="execution")
                verified = {**intent, "removed_resource_sha256": resource_sha}
                self.store.append_host_control_mutation_step_event(
                    admission_id=admission.admission_id,
                    event="RESTORE_STEP_READBACK_VERIFIED", payload=verified)
                active_index = None
            result = {"success": True, "operation": "host.change.restore",
                      "execution_state": "SUCCEEDED", "host_change_state": "RESTORED_UNVERIFIED",
                      "restore_plan_sha256": restore_sha,
                      "source_plan_sha256": plan.sha256,
                      "source_receipt_sha256": receipt.receipt_sha256,
                      "effective_policy_state": "UNVERIFIED", "t037_state": "NOT_VERIFIED",
                      "safe_retry": False,
                      "evidence_classification": "SOFTWARE_ONLY" if self.software_only
                          else "WINDOWS_NATIVE_READBACK_ONLY"}
            accepted, authoritative = self.store.finish(
                admission.operation_id, status="SUCCEEDED", result=result)
            if not accepted or authoritative != "SUCCEEDED":
                _error("HOST_CHANGE_RESULT_PERSISTENCE_UNKNOWN",
                       "all WFP resources were absent on readback but terminal persistence is unknown",
                       safe_retry=False, stage="execution")
            return result
        except Exception as exc:
            self._finish_unknown_sequence(admission, phase="RESTORE", action=active_action,
                                          step_index=active_index, cause=exc)
            _error("HOST_CHANGE_RECOVERY_REQUIRED",
                   "reverse WFP restore may have changed Windows state; automatic replay is forbidden",
                   safe_retry=False, stage="execution",
                   details={"cause_code": getattr(exc, "code", None) or type(exc).__name__,
                            "admission_id": admission.admission_id, "job_id": admission.job_id,
                            "step_index": active_index})

    def apply(self, plan: Any, *, approval: resources.HostChangeApproval) -> resources.AppliedHostChangeReceipt:
        from . import _windows_appcontainer as appcontainer
        if appcontainer.is_leaf(plan.as_dict()["change_manifest"]):
            return appcontainer.apply(self, plan, approval)
        from ._windows_service_sid import is_leaf, apply
        if is_leaf(plan.as_dict()["change_manifest"]):
            return apply(self, plan, approval)
        payload, actions, baseline, use_v2 = self._preflight_sequence(plan)
        if use_v2:
            return self._apply_sequence(plan, actions, baseline, approval=approval)
        action = actions[0]
        admission = self._admit(phase="APPLY", plan_sha256=plan.sha256,
                                approval=approval, baseline=baseline)
        try:
            self._start(admission)
            intent = {"action_id": action["action_id"], "filter_key": action["filter_key"],
                      "desired_identity_sha256": action["desired_identity_sha256"],
                      "dependency_sha256": baseline["dependency_sha256"]}
            self.store.append_host_control_mutation_event(
                admission_id=admission.admission_id, event="APPLY_INTENT", payload=intent)
            row = self.api.create_filter(profile=self.profile, action=action,
                                         expected_dependency_sha256=baseline["dependency_sha256"])
            resource_sha256 = sha256(row)
            verified = {**intent, "created_resource_sha256": resource_sha256}
            self.store.append_host_control_mutation_event(
                admission_id=admission.admission_id,
                event="APPLY_READBACK_VERIFIED", payload=verified)
            receipt_actions = ({"action_id": action["action_id"],
                               "created_resource_sha256": resource_sha256,
                               "prior_resource_sha256": None, "prior_backup_path": None},)
            receipt_body = _receipt_payload(plan, self.profile, receipt_actions)
            receipt_sha = hashlib.sha256(_canonical(receipt_body).encode("utf-8")).hexdigest()
            receipt = resources.AppliedHostChangeReceipt(
                plan.sha256, self.profile.host_id, "windows", receipt_actions, receipt_sha)
            receipt_document = {**receipt_body, "receipt_sha256": receipt_sha}
            result = {"success": True, "operation": "host.change.apply",
                      "execution_state": "SUCCEEDED", "host_change_state": "APPLIED_UNVERIFIED",
                      "plan_sha256": plan.sha256, "profile_sha256": self.profile.sha256,
                      # Persist the complete typed receipt in the durable result
                      # so an acknowledged terminal apply remains recoverable
                      # if the caller disconnects before this method returns.
                      "applied_receipt": receipt_document,
                      "receipt_sha256": receipt_sha, "created_resource_sha256": resource_sha256,
                      "effective_policy_state": "UNVERIFIED",
                      "t037_state": "NOT_VERIFIED", "safe_retry": False,
                      "evidence_classification": ("SOFTWARE_ONLY" if self.software_only
                                                   else "WINDOWS_NATIVE_READBACK_ONLY")}
            accepted, authoritative = self.store.finish(admission.operation_id,
                                                        status="SUCCEEDED", result=result)
            if not accepted or authoritative != "SUCCEEDED":
                _error("HOST_CHANGE_RESULT_PERSISTENCE_UNKNOWN",
                       "filter was read back but its durable terminal receipt could not be committed",
                       safe_retry=False, stage="execution")
            return receipt
        except Exception as exc:
            self._finish_unknown(admission, phase="APPLY", action=action, cause=exc)
            _error("HOST_CHANGE_RECOVERY_REQUIRED", "host filter mutation may have changed Windows state; automatic replay is forbidden",
                   safe_retry=False, stage="execution",
                   details={"cause_code": getattr(exc, "code", None) or type(exc).__name__,
                            "admission_id": admission.admission_id, "job_id": admission.job_id})

    def restore(self, plan: Any, receipt: resources.AppliedHostChangeReceipt, *,
                approval: resources.HostChangeApproval | None = None,
                authorization_ref: str | None = None) -> dict[str, Any]:
        if approval is not None and authorization_ref is not None:
            _error("HOST_CHANGE_APPROVAL_MISMATCH",
                   "restore accepts either an internal authorization reference or an explicit exact approval")
        if authorization_ref is not None and (
                not isinstance(authorization_ref, str) or not authorization_ref.strip()
                or len(authorization_ref) > 512 or any(ord(char) < 32 for char in authorization_ref)):
            _error("HOST_CHANGE_APPROVAL_MISMATCH", "restore authorization reference is malformed")
        from . import _windows_appcontainer as appcontainer
        if appcontainer.is_leaf(plan.as_dict()['change_manifest']):
            return appcontainer.restore(self, plan, receipt, approval=approval, authorization_ref=authorization_ref)
        from ._windows_service_sid import is_leaf, restore
        if is_leaf(plan.as_dict()["change_manifest"]):
            return restore(self, plan, receipt, approval=approval, authorization_ref=authorization_ref)
        _payload, actions = _normalize_supported_actions(plan, self.profile)
        use_v2 = len(actions) > 1 or actions[0]["resource_kind"] != "wfp_filter"
        if use_v2:
            return self._restore_sequence(plan, receipt, approval=approval,
                                          authorization_ref=authorization_ref)
        payload, action, created_sha = _verify_receipt(plan, self.profile, receipt)
        if not self.software_only:
            current = _current_windows_binding(self.profile)
        else:
            current = {"host_id": self.profile.host_id,
                       "host_fingerprint_sha256": self.profile.windows_isolation.host_fingerprint_sha256,
                       "root_identity": {"device": self.profile.root.volume_id,
                                         "inode": self.profile.root.file_id},
                       "root_security_sha256": None,
                       "service_token_sid": self.profile.windows_isolation.service_logon_sid,
                       "evidence_classification": "SOFTWARE_ONLY"}
        snapshot = self.api.observe_snapshot()
        baseline = _snapshot_dependencies(snapshot, self.profile, action, require_filter_absent=False)
        if (baseline["target_filter_sha256"] != created_sha
                or baseline["target_filter_present"] is not True):
            _error("HOST_CHANGE_RESTORE_DRIFT", "receipt-bound filter is absent or changed; foreign/current state was preserved")
        baseline["current_binding"] = current
        restore_plan_payload = {
            "schema": "comsol-mcp.host-change-restore/1",
            "profile_sha256": self.profile.sha256,
            "source_plan_sha256": plan.sha256,
            "source_receipt_sha256": receipt.receipt_sha256,
            "filter_key": action["filter_key"],
            "expected_created_resource_sha256": created_sha,
            "dependency_sha256": baseline["dependency_sha256"],
        }
        restore_plan_sha = hashlib.sha256(_canonical(restore_plan_payload).encode("utf-8")).hexdigest()
        if authorization_ref is not None:
            approval = resources.HostChangeApproval(restore_plan_sha, authorization_ref)
        if not isinstance(approval, resources.HostChangeApproval) or approval.plan_sha256 != restore_plan_sha:
            _error("HOST_CHANGE_APPROVAL_MISMATCH",
                   "authorization must bind the exact fresh restore plan and dependency snapshot")
        admission = self._admit(phase="RESTORE", plan_sha256=restore_plan_sha,
                                approval=approval, baseline=baseline,
                                source_plan_sha256=plan.sha256,
                                source_receipt_sha256=receipt.receipt_sha256)
        try:
            self._start(admission)
            intent = {"action_id": action["action_id"], "filter_key": action["filter_key"],
                      "expected_resource_sha256": created_sha,
                      "dependency_sha256": baseline["dependency_sha256"]}
            self.store.append_host_control_mutation_event(
                admission_id=admission.admission_id, event="RESTORE_INTENT", payload=intent)
            self.api.delete_filter(profile=self.profile, action=action,
                                   expected_resource_sha256=created_sha,
                                   expected_dependency_sha256=baseline["dependency_sha256"])
            self.store.append_host_control_mutation_event(
                admission_id=admission.admission_id, event="RESTORE_READBACK_VERIFIED",
                payload={**intent, "removed_resource_sha256": created_sha})
            result = {"success": True, "operation": "host.change.restore",
                      "execution_state": "SUCCEEDED", "host_change_state": "RESTORED_UNVERIFIED",
                      "restore_plan_sha256": restore_plan_sha,
                      "source_plan_sha256": plan.sha256,
                      "source_receipt_sha256": receipt.receipt_sha256,
                      "effective_policy_state": "UNVERIFIED", "t037_state": "NOT_VERIFIED",
                      "safe_retry": False,
                      "evidence_classification": ("SOFTWARE_ONLY" if self.software_only
                                                   else "WINDOWS_NATIVE_READBACK_ONLY")}
            accepted, authoritative = self.store.finish(admission.operation_id,
                                                        status="SUCCEEDED", result=result)
            if not accepted or authoritative != "SUCCEEDED":
                _error("HOST_CHANGE_RESULT_PERSISTENCE_UNKNOWN",
                       "filter deletion was read back but its durable terminal receipt could not be committed",
                       safe_retry=False, stage="execution")
            return result
        except Exception as exc:
            self._finish_unknown(admission, phase="RESTORE", action=action, cause=exc)
            _error("HOST_CHANGE_RECOVERY_REQUIRED", "host filter restore may have changed Windows state; automatic replay is forbidden",
                   safe_retry=False, stage="execution",
                   details={"cause_code": getattr(exc, "code", None) or type(exc).__name__,
                            "admission_id": admission.admission_id, "job_id": admission.job_id})


__all__ = ["WindowsHostChangeExecutor", "WindowsHostChangeError"]
