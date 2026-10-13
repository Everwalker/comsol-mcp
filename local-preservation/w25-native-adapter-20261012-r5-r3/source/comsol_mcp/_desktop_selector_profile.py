"""Strict host-installed profile for previously observed Desktop selectors.

Profiles are deliberately data, not executable code.  They are read from a
private, host-owned file and are bound to one exact platform, OS build and
COMSOL product version.  No profile is synthesized from a window title or a
test fixture.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
from typing import Any, Mapping

from ._desktop_service import DesktopOperationError, WindowIdentity


PROFILE_SCHEMA = "comsol-mcp.desktop-selector-profile/1"
MAX_PROFILE_BYTES = 256 * 1024
MAX_PROFILE_AGE_SECONDS = 30 * 24 * 60 * 60
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROFILE_KEYS = frozenset({
    "schema", "profile_id", "platform", "comsol_version", "os_build",
    "observed_at_utc", "observation_sha256", "selectors", "node_paths", "actions",
})
_COMMON_SELECTOR_KEYS = frozenset({"selector_id", "kind", "properties", "readback", "pattern"})
_ALLOWED_SELECTOR_NAMES = frozenset({
    "active_model_label", "shell_input", "shell_run", "shell_output",
    "shell_busy", "show_model", "save_copy", "save_path", "save_confirm",
    "save_complete", "active_server_endpoint", "standalone_model_id",
    "model_fingerprint", "model_dirty",
})


def _strict_json(raw: bytes) -> dict[str, Any]:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError(f"non-finite JSON constant {value}")

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise DesktopOperationError("DESKTOP_PROFILE_INVALID", "selector profile is not strict UTF-8 JSON", safe_retry=True) from exc
    if not isinstance(value, dict):
        raise DesktopOperationError("DESKTOP_PROFILE_INVALID", "selector profile root must be an object", safe_retry=True)
    return value


def _canonical_absolute(path_value: str | os.PathLike[str]) -> Path:
    path = Path(path_value).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise DesktopOperationError("DESKTOP_PROFILE_INVALID", "selector profile path must be absolute and traversal-free", safe_retry=True)
    cursor = Path(path.anchor)
    try:
        for part in path.parts[1:]:
            cursor /= part
            if stat.S_ISLNK(cursor.lstat().st_mode):
                raise DesktopOperationError("DESKTOP_PROFILE_INVALID", "selector profile path contains a symlink", safe_retry=True)
        if path.resolve(strict=True) != path:
            raise DesktopOperationError("DESKTOP_PROFILE_INVALID", "selector profile path must be canonical", safe_retry=True)
    except DesktopOperationError:
        raise
    except OSError as exc:
        raise DesktopOperationError("DESKTOP_PROFILE_INVALID", "selector profile path cannot be inspected", safe_retry=True) from exc
    return path


def _require_security_evidence(security: Any, *, host_platform: str | None = None) -> dict[str, Any]:
    """Validate the provider contract before its identity is used for a pinned read."""
    if not isinstance(security, Mapping):
        raise DesktopOperationError("DESKTOP_PROFILE_UNTRUSTED", "selector profile security evidence is malformed", safe_retry=True)
    identity = security.get("identity")
    if not isinstance(identity, Mapping) or type(identity.get("device")) is not int or type(identity.get("inode")) is not int:
        raise DesktopOperationError("DESKTOP_PROFILE_UNTRUSTED", "selector profile security identity is missing", safe_retry=True)
    if identity["device"] < 0 or identity["inode"] <= 0:
        raise DesktopOperationError("DESKTOP_PROFILE_UNTRUSTED", "selector profile security identity is invalid", safe_retry=True)
    semantics = security.get("security_semantics")
    host = host_platform or ("windows" if os.name == "nt" else "macos" if sys.platform == "darwin" else "posix")
    if host == "windows":
        # This is the exact semantic label emitted by the reviewed host
        # provider API. Keep the consumer bound to its returned contract
        # without requiring a convenience constant that older compatible
        # provider snapshots do not export.
        expected_semantics = "windows-read-control-owner-dacl-persistent-acls"
    elif host == "macos":
        expected_semantics = "posix-owner-mode+filesystem-ownership+no-extended-acl"
    else:
        expected_semantics = "posix-owner-mode"
    if semantics != expected_semantics:
        raise DesktopOperationError("DESKTOP_PROFILE_UNTRUSTED", "selector profile lacks the host provider's exact ownership/ACL contract", safe_retry=True)
    security_sha = security.get("security_sha256")
    if host == "windows":
        if (not isinstance(security_sha, str) or not _SHA256.fullmatch(security_sha)
                or identity.get("security_sha256") != security_sha):
            raise DesktopOperationError("DESKTOP_PROFILE_UNTRUSTED", "Windows selector profile ACL evidence is not hash-bound", safe_retry=True)
    elif security_sha is not None:
        raise DesktopOperationError("DESKTOP_PROFILE_UNTRUSTED", "POSIX selector profile returned unexpected ACL digest evidence", safe_retry=True)
    return {"identity": dict(identity), "security_semantics": semantics,
            "security_sha256": security_sha}


def _same_security_evidence(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return (dict(left["identity"]) == dict(right["identity"])
            and left["security_semantics"] == right["security_semantics"]
            and left["security_sha256"] == right["security_sha256"])


def _descriptor_identity(fd: int, info: os.stat_result, *, windows: bool | None = None) -> tuple[int, int]:
    """Return identity in the same native representation as the trust provider."""
    use_windows = os.name == "nt" if windows is None else windows
    if not use_windows:
        return int(info.st_dev), int(info.st_ino)
    try:
        import msvcrt
        from ._windows_parameter_publish import _API
        native_handle = msvcrt.get_osfhandle(fd)
        (device, inode), native_size = _API().info(native_handle, directory=False)
    except Exception as exc:
        raise DesktopOperationError("DESKTOP_PROFILE_UNTRUSTED", "Windows profile fd identity could not be read through its pinned native handle", safe_retry=True) from exc
    if int(native_size) != int(info.st_size):
        raise DesktopOperationError("DESKTOP_PROFILE_CHANGED", "Windows profile fd size differs between CRT and native handle views", safe_retry=True)
    return int(device), int(inode)


def _read_private_profile(path_value: str | os.PathLike[str]) -> tuple[dict[str, Any], str]:
    path = _canonical_absolute(path_value)
    try:
        from ._host_scoped_provider import verify_private_path_security
        initial_security = _require_security_evidence(
            verify_private_path_security(path, directory=False))
    except Exception as exc:
        if isinstance(exc, DesktopOperationError):
            raise
        code = getattr(exc, "code", "DESKTOP_PROFILE_UNTRUSTED")
        raise DesktopOperationError(str(code), "selector profile OS ownership/ACL trust could not be verified", safe_retry=True) from exc
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise DesktopOperationError("DESKTOP_PROFILE_UNTRUSTED", "selector profile could not be pinned", safe_retry=True) from exc
    try:
        before = os.fstat(fd)
        named = path.lstat()
        expected_identity = initial_security["identity"]
        descriptor_identity = _descriptor_identity(fd, before)
        named_identity_matches = (os.name == "nt" or
                                  (before.st_dev, before.st_ino) == (named.st_dev, named.st_ino))
        if (not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(named.st_mode)
                or not named_identity_matches
                or descriptor_identity != (expected_identity["device"], expected_identity["inode"])
                or before.st_size < 2 or before.st_size > MAX_PROFILE_BYTES):
            raise DesktopOperationError("DESKTOP_PROFILE_UNTRUSTED", "selector profile file identity or size is invalid", safe_retry=True)
        try:
            security_before = _require_security_evidence(
                verify_private_path_security(path, directory=False,
                                             expected_identity=expected_identity))
        except Exception as exc:
            code = getattr(exc, "code", "DESKTOP_PROFILE_UNTRUSTED")
            raise DesktopOperationError(str(code), "selector profile trust changed before the pinned read", safe_retry=True) from exc
        if not _same_security_evidence(initial_security, security_before):
            raise DesktopOperationError("DESKTOP_PROFILE_CHANGED", "selector profile security evidence changed before the pinned read", safe_retry=True)
        chunks = []
        remaining = MAX_PROFILE_BYTES + 1
        while remaining:
            block = os.read(fd, min(65536, remaining))
            if not block:
                break
            chunks.append(block)
            remaining -= len(block)
        raw = b"".join(chunks)
        after = os.fstat(fd)
        named_after = path.lstat()
        descriptor_identity_after = _descriptor_identity(fd, after)
        try:
            security_after = _require_security_evidence(
                verify_private_path_security(path, directory=False,
                                             expected_identity=expected_identity))
        except Exception as exc:
            code = getattr(exc, "code", "DESKTOP_PROFILE_CHANGED")
            raise DesktopOperationError(str(code), "selector profile trust changed during the pinned read", safe_retry=True) from exc
        identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        named_identity_matches_after = (os.name == "nt" or
                                        (after.st_dev, after.st_ino) == (named_after.st_dev, named_after.st_ino))
        if (identity_before != identity_after
                or not named_identity_matches_after
                or descriptor_identity_after != (expected_identity["device"], expected_identity["inode"])
                or not _same_security_evidence(initial_security, security_after)
                or len(raw) > MAX_PROFILE_BYTES):
            raise DesktopOperationError("DESKTOP_PROFILE_CHANGED", "selector profile changed while being read", safe_retry=True)
        profile = _strict_json(raw)
        return profile, hashlib.sha256(raw).hexdigest()
    finally:
        os.close(fd)


def _validate_selector(selector_id: str, value: Any, platform: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) - (_COMMON_SELECTOR_KEYS | {"states"}) or not _COMMON_SELECTOR_KEYS <= set(value):
        raise DesktopOperationError("DESKTOP_PROFILE_INVALID", f"selector {selector_id!r} has an invalid schema", safe_retry=True)
    if value.get("selector_id") != selector_id:
        raise DesktopOperationError("DESKTOP_PROFILE_INVALID", "selector IDs must match their profile keys", safe_retry=True)
    expected_kind = "uia" if platform == "windows" else "ax"
    if value.get("kind") != expected_kind:
        raise DesktopOperationError("DESKTOP_PROFILE_INVALID", f"selector {selector_id!r} has the wrong platform kind", safe_retry=True)
    properties = value.get("properties")
    if not isinstance(properties, Mapping):
        raise DesktopOperationError("DESKTOP_PROFILE_INVALID", f"selector {selector_id!r} properties must be an object", safe_retry=True)
    if platform == "windows":
        allowed = {"automation_id", "control_type_id", "framework_id", "name"}
        required = {"automation_id", "control_type_id", "framework_id"}
        if not required <= set(properties) or set(properties) - allowed:
            raise DesktopOperationError("DESKTOP_PROFILE_INVALID", f"UIA selector {selector_id!r} must use exact AutomationId/ControlType/FrameworkId properties", safe_retry=True)
        if (not isinstance(properties["automation_id"], str) or not properties["automation_id"]
                or type(properties["control_type_id"]) is not int or properties["control_type_id"] <= 0
                or not isinstance(properties["framework_id"], str) or not properties["framework_id"]
                or ("name" in properties and (not isinstance(properties["name"], str) or not properties["name"]))):
            raise DesktopOperationError("DESKTOP_PROFILE_INVALID", f"UIA selector {selector_id!r} has malformed exact properties", safe_retry=True)
    else:
        allowed = {"identifier", "role", "subrole", "title"}
        required = {"identifier", "role"}
        if not required <= set(properties) or set(properties) - allowed:
            raise DesktopOperationError("DESKTOP_PROFILE_INVALID", f"AX selector {selector_id!r} must use exact identifier and role", safe_retry=True)
        if any(not isinstance(properties[key], str) or not properties[key] for key in required):
            raise DesktopOperationError("DESKTOP_PROFILE_INVALID", f"AX selector {selector_id!r} has malformed exact properties", safe_retry=True)
        if any(key in properties and (not isinstance(properties[key], str) or not properties[key]) for key in {"subrole", "title"}):
            raise DesktopOperationError("DESKTOP_PROFILE_INVALID", f"AX selector {selector_id!r} has malformed optional properties", safe_retry=True)
    readback = value.get("readback")
    if not isinstance(readback, str) or readback not in {"name", "value", "text"}:
        raise DesktopOperationError("DESKTOP_PROFILE_INVALID", f"selector {selector_id!r} must declare a supported readback", safe_retry=True)
    pattern = value.get("pattern")
    if not isinstance(pattern, str) or pattern not in {"read", "value", "invoke", "selection"}:
        raise DesktopOperationError("DESKTOP_PROFILE_INVALID", f"selector {selector_id!r} must declare a supported interaction pattern", safe_retry=True)
    states = value.get("states")
    if selector_id in {"shell_busy", "model_dirty", "save_complete"}:
        if selector_id == "shell_busy":
            expected_states = {"busy", "idle"}
        elif selector_id == "model_dirty":
            expected_states = {"dirty", "clean"}
        else:
            expected_states = {"idle", "busy", "complete"}
        if (not isinstance(states, Mapping) or set(states) != expected_states
                or not all(isinstance(states[key], str) and states[key] for key in expected_states)
                or len({states[key] for key in expected_states}) != len(expected_states)):
            raise DesktopOperationError("DESKTOP_PROFILE_INVALID", f"{selector_id} requires exact distinct state readbacks", safe_retry=True)
    elif states is not None:
        raise DesktopOperationError("DESKTOP_PROFILE_INVALID", "states are only accepted for shell_busy, model_dirty or save_complete", safe_retry=True)
    if platform == "macos" and readback not in {"name", "value"}:
        raise DesktopOperationError("DESKTOP_PROFILE_INVALID", f"AX selector {selector_id!r} declares an unsupported readback", safe_retry=True)
    if selector_id in {"active_model_label", "active_server_endpoint", "standalone_model_id",
                       "model_fingerprint", "model_dirty"} and pattern != "read":
        raise DesktopOperationError("DESKTOP_PROFILE_INVALID", f"observation selector {selector_id!r} must be read-only", safe_retry=True)
    if selector_id in {"show_model", "save_copy", "save_confirm"} and pattern not in {"invoke", "selection"}:
        raise DesktopOperationError("DESKTOP_PROFILE_INVALID", f"action selector {selector_id!r} must declare invoke or selection", safe_retry=True)
    if selector_id == "save_path" and pattern != "value":
        raise DesktopOperationError("DESKTOP_PROFILE_INVALID", "Save Copy path selector must declare ValuePattern", safe_retry=True)
    if selector_id == "save_complete" and pattern != "read":
        raise DesktopOperationError("DESKTOP_PROFILE_INVALID", "Save Copy completion selector must be read-only", safe_retry=True)
    # Freeze the parsed values into plain, bounded JSON data.
    result = {"selector_id": selector_id, "kind": expected_kind,
              "properties": dict(properties), "readback": readback,
              "pattern": pattern}
    if states is not None:
        result["states"] = dict(states)
    return result


def validate_profile(profile: Mapping[str, Any], *, platform_name: str,
                     comsol_version: str, os_build: str,
                     now: float | None = None) -> dict[str, Any]:
    if not isinstance(profile, Mapping) or set(profile) != _PROFILE_KEYS:
        raise DesktopOperationError("DESKTOP_PROFILE_INVALID", "selector profile has unknown or missing top-level fields", safe_retry=True)
    if profile.get("schema") != PROFILE_SCHEMA or profile.get("platform") != platform_name:
        raise DesktopOperationError("DESKTOP_PROFILE_MISMATCH", "selector profile schema/platform does not match this host", safe_retry=True)
    if profile.get("comsol_version") != comsol_version or profile.get("os_build") != os_build:
        raise DesktopOperationError("DESKTOP_PROFILE_STALE", "selector profile exact COMSOL version or OS build differs from the observed host", safe_retry=True)
    if not isinstance(profile.get("profile_id"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", profile["profile_id"]):
        raise DesktopOperationError("DESKTOP_PROFILE_INVALID", "profile_id is malformed", safe_retry=True)
    observation_sha = profile.get("observation_sha256")
    if not isinstance(observation_sha, str) or not _SHA256.fullmatch(observation_sha):
        raise DesktopOperationError("DESKTOP_PROFILE_INVALID", "profile must bind a host-observation SHA-256", safe_retry=True)
    observed_at = profile.get("observed_at_utc")
    if not isinstance(observed_at, str):
        raise DesktopOperationError("DESKTOP_PROFILE_INVALID", "observed_at_utc must be an ISO-8601 timestamp", safe_retry=True)
    try:
        parsed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timezone required")
        observed_epoch = parsed.astimezone(timezone.utc).timestamp()
    except (TypeError, ValueError, OverflowError) as exc:
        raise DesktopOperationError("DESKTOP_PROFILE_INVALID", "observed_at_utc is invalid", safe_retry=True) from exc
    current = time.time() if now is None else now
    if observed_epoch > current + 300 or current - observed_epoch > MAX_PROFILE_AGE_SECONDS:
        raise DesktopOperationError("DESKTOP_PROFILE_STALE", "selector observation is future-dated or older than 30 days", safe_retry=True)
    selectors = profile.get("selectors")
    if not isinstance(selectors, Mapping) or not selectors:
        raise DesktopOperationError("DESKTOP_PROFILE_INVALID", "selector profile has unknown or empty selector inventory", safe_retry=True)
    validated = {}
    fingerprints = set()
    for selector_id, value in selectors.items():
        if (not isinstance(selector_id, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,95}", selector_id)
                or (selector_id not in _ALLOWED_SELECTOR_NAMES
                    and not re.fullmatch(r"(?:node|action)_[0-9a-f]{64}", selector_id))):
            raise DesktopOperationError("DESKTOP_PROFILE_INVALID", "selector key is malformed", safe_retry=True)
        item = _validate_selector(selector_id, value, platform_name)
        fingerprint = json.dumps(item["properties"], sort_keys=True, separators=(",", ":"))
        if fingerprint in fingerprints:
            raise DesktopOperationError("DESKTOP_PROFILE_DUPLICATE", "selector profile contains duplicate exact selectors", safe_retry=True)
        fingerprints.add(fingerprint)
        validated[selector_id] = item
    save_selector_names = {"save_copy", "save_path", "save_confirm", "save_complete"}
    if set(validated) & save_selector_names:
        if not save_selector_names <= set(validated):
            raise DesktopOperationError(
                "DESKTOP_PROFILE_INVALID",
                "Save Copy requires observed action, path, confirmation and completion selectors",
                safe_retry=True,
            )
        if validated["save_complete"]["pattern"] != "read":
            raise DesktopOperationError("DESKTOP_PROFILE_INVALID", "Save Copy completion must be a read-only observed selector", safe_retry=True)
    if "shell_input" in validated or "shell_run" in validated or "shell_output" in validated or "shell_busy" in validated:
        required_shell = {"shell_input", "shell_run", "shell_output", "shell_busy"}
        if not required_shell <= set(validated):
            raise DesktopOperationError("DESKTOP_PROFILE_INVALID", "Java Shell support requires input, run, output and busy selectors", safe_retry=True)
        if (validated["shell_input"]["pattern"] != "value"
                or validated["shell_input"]["readback"] != "value"
                or validated["shell_run"]["pattern"] != "invoke"
                or validated["shell_busy"]["pattern"] != "read"
                or validated["shell_output"]["pattern"] not in {"read", "value"}
                or validated["shell_output"]["pattern"] == "invoke"
                or validated["shell_busy"]["pattern"] == "invoke"):
            raise DesktopOperationError("DESKTOP_PROFILE_INVALID", "Java Shell selectors have incompatible declared interaction patterns", safe_retry=True)
    node_paths = profile.get("node_paths")
    actions = profile.get("actions")
    if not isinstance(node_paths, Mapping) or not isinstance(actions, Mapping):
        raise DesktopOperationError("DESKTOP_PROFILE_INVALID", "node_paths and actions must be objects, even when empty", safe_retry=True)
    for key, selector_id in node_paths.items():
        if not isinstance(key, str) or not _SHA256.fullmatch(key) or selector_id not in validated:
            raise DesktopOperationError("DESKTOP_PROFILE_INVALID", "node_paths must map canonical NodePath SHA-256 keys to selectors", safe_retry=True)
        if validated[selector_id]["pattern"] not in {"selection", "invoke"}:
            raise DesktopOperationError("DESKTOP_PROFILE_INVALID", "node path selector must declare selection or invoke", safe_retry=True)
    validated_actions = {}
    for action_id, descriptor in actions.items():
        if (not isinstance(action_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", action_id)
                or not isinstance(descriptor, Mapping)
                or set(descriptor) != {"selector_id", "mutates_model", "before", "after"}
                or type(descriptor.get("mutates_model")) is not bool):
            raise DesktopOperationError("DESKTOP_PROFILE_INVALID", "actions require a strict selector/mutation/before/after observation descriptor", safe_retry=True)
        selector_id = descriptor.get("selector_id")
        if selector_id not in validated or validated[selector_id]["pattern"] not in {"invoke", "selection"}:
            raise DesktopOperationError("DESKTOP_PROFILE_INVALID", "action selector must reference an exact invokable UI element", safe_retry=True)
        states = {}
        for condition_name in ("before", "after"):
            condition = descriptor.get(condition_name)
            if (not isinstance(condition, Mapping) or set(condition) != {"selector_id", "value"}
                    or condition.get("selector_id") not in validated
                    or validated[condition["selector_id"]]["pattern"] not in {"read", "value"}
                    or not isinstance(condition.get("value"), str) or len(condition["value"]) > 4096
                    or "\x00" in condition["value"]):
                raise DesktopOperationError("DESKTOP_PROFILE_INVALID", f"action {condition_name} condition must reference one bounded exact readback", safe_retry=True)
            states[condition_name] = {"selector_id": condition["selector_id"], "value": condition["value"]}
        validated_actions[action_id] = {"selector_id": selector_id,
                                        "mutates_model": descriptor["mutates_model"],
                                        "before": states["before"], "after": states["after"]}
    return {**dict(profile), "selectors": validated,
            "node_paths": dict(node_paths), "actions": validated_actions}


def load_selector_profile(path_value: str | os.PathLike[str] | None, identity: WindowIdentity,
                          *, os_build: str) -> dict[str, Any]:
    if not path_value:
        raise DesktopOperationError("DESKTOP_PROFILE_UNAVAILABLE", "no host-observed Desktop selector profile is installed", safe_retry=True)
    profile, digest = _read_private_profile(path_value)
    validated = validate_profile(profile, platform_name=identity.platform,
                                 comsol_version=identity.comsol_version,
                                 os_build=os_build)
    return {**validated, "profile_sha256": digest}
