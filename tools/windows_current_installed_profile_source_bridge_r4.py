"""Resolve the current W26 source-profile generation without relabeling artifacts.

This bridge keeps the R3/R6 consumer historical, verifies the exact R7 App7a
artifact only through its existing R7 reader, and refuses current-source
binding until that exact generation has a reviewed reader and completed App.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from tools import w26_application_bundle as application
from tools import w26_application_lifecycle_r7_actor as lifecycle_r7
from tools import w26_sealed_data_r7 as sealed_r7
from tools.w26_prepare_current_inputs import _helpers


R7_PROFILE = "current175-r7"
R8_PROFILE = "current175-r8"
R7_LOCK_SHA256 = "6062c06724846eeb294c10767bd25579ffe44d1035bdcc505d3f843a01c1ab8a"
R8_PREPARED_LOCK_SHA256 = "11ff744bc2bd7d860c75fced51575ab99f8c9fa13f959b1c38b0d79c13823a12"
R7_MEMBER_FINGERPRINT_SHA256 = "a7a6f4cbdc67224df624bf9b11a253193692f4ef79f811404337a490808fd1aa"
R8_MEMBER_FINGERPRINT_SHA256 = "93a90276bd88c57837f12be286be8d653026f7c0de030206dc5c61c448839200"
R7_APP7A_ROOT = Path("/private/tmp/cvw26-application-2854c1fc")
R7_APP7A_RESULT_SHA256 = "498800d4ee43c28cf17138140a47d1bb8aa3a65a886b9b6b48cf209e61d625a7"
R7_APP7A_WHEEL_SHA256 = "498a81fe9656f974986bfbeb1945d948f9c46914b9ba256327c63c221bb610c7"
R7_ARCHIVE_REVIEW_SHA256 = "d4125e76aaeddd599d2f1df567b229194af1fe19449562f9f55944d40394c63c"
R7_ARCHIVE_REVIEW_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs/full_project_continuation_v3/state/W26_R7_MAIN_ACTUAL_ARCHIVE_EVIDENCE_REVIEW_R1.json"
)


class CurrentSourceNotReady(RuntimeError):
    def __init__(self, readiness: Mapping[str, Any]):
        self.readiness = dict(readiness)
        super().__init__(str(self.readiness.get("status", "NOT_READY_CURRENT_SOURCE")))


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _expected_lock(profile: str) -> tuple[Path, str, str]:
    if profile == R7_PROFILE:
        return application.CURRENT175_R7_SOURCE_LOCK, R7_LOCK_SHA256, R7_MEMBER_FINGERPRINT_SHA256
    if profile == R8_PROFILE:
        return application.CURRENT175_R8_SOURCE_LOCK, R8_PREPARED_LOCK_SHA256, R8_MEMBER_FINGERPRINT_SHA256
    raise CurrentSourceNotReady({
        "status": "NOT_READY_UNSUPPORTED_SOURCE_PROFILE",
        "requested_source_profile": profile,
        "r6_is_preserved_by": "windows_current_installed_profile_runtime_binding_r3",
    })


def _validate_lock_value(value: Any, profile: str, member_fingerprint: str) -> dict[str, Any]:
    if (not isinstance(value, Mapping)
            or value.get("schema") != "w26-application-source-closure/1"
            or value.get("profile") != profile
            or value.get("member_count") != 175
            or value.get("package_count") != 172
            or value.get("root_count") != 3
            or not isinstance(value.get("members"), Mapping)
            or len(value["members"]) != 175):
        raise RuntimeError("W26 source lock profile/schema/counts do not match its exact generation")
    for name, record in value["members"].items():
        if (not isinstance(name, str) or not isinstance(record, Mapping)
                or set(record) != {"bytes", "sha256"}
                or type(record.get("bytes")) is not int or record["bytes"] < 0
                or not isinstance(record.get("sha256"), str) or len(record["sha256"]) != 64
                or any(ch not in "0123456789abcdef" for ch in record["sha256"])):
            raise RuntimeError("W26 source lock contains a malformed source-member record")
    observed_fingerprint = _sha(canonical_bytes(dict(value["members"])))
    if observed_fingerprint != member_fingerprint:
        raise RuntimeError("W26 source member fingerprint differs from the pinned generation")
    return {
        "source_profile": profile,
        "member_count": value["member_count"],
        "package_count": value["package_count"],
        "root_count": value["root_count"],
        "member_fingerprint_sha256": observed_fingerprint,
    }


def _r7_original_source_differences(root: Path) -> list[dict[str, Any]]:
    """Return current-vs-recorded source deltas for a failed historical R7 read."""
    helper = _helpers()
    candidate, _candidate_record = sealed_r7._producer_records(root, sealed_r7.BASE)["candidate"]
    prepared_record = candidate.get("prepared")
    if not isinstance(prepared_record, Mapping):
        return [{"diagnostic": "R7 App candidate lacks a prepared source record"}]
    prepared = json.loads(helper.raw_file(Path(prepared_record["path"]), expected=prepared_record)[0])
    source = prepared.get("source")
    original_members = source.get("original_members") if isinstance(source, Mapping) else None
    if not isinstance(original_members, Mapping):
        return [{"diagnostic": "R7 App prepared source lacks original member records"}]
    differences = []
    compared = ("path", "bytes", "sha256", "dev", "inode", "uid", "nlink", "mode")
    for relative, expected in sorted(original_members.items()):
        try:
            observed = helper.raw_file(Path(expected["path"]))[1]
        except Exception as exc:
            differences.append({"member": relative, "read_error": f"{type(exc).__name__}: {exc}"})
            continue
        delta = {
            key: {"recorded": expected.get(key), "observed": observed.get(key)}
            for key in compared if expected.get(key) != observed.get(key)
        }
        if delta:
            differences.append({"member": relative, "differences": delta})
    return differences


def source_lock_binding(profile: str) -> dict[str, Any]:
    """Bind a generation name to its exact lock path, bytes, and member map."""
    path, expected_lock_sha, expected_member_fingerprint = _expected_lock(profile)
    path = Path(path).resolve(strict=True)
    raw, record = _helpers().raw_file(path)
    if record.get("sha256") != expected_lock_sha:
        raise RuntimeError("W26 source-lock bytes differ from the reviewed generation")
    try:
        value = json.loads(raw.decode("utf-8", "strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("W26 source lock is not strict UTF-8 JSON") from exc
    member_binding = _validate_lock_value(value, profile, expected_member_fingerprint)
    return {
        "schema": "w26-source-lock-generation-binding/1",
        "path": str(path),
        "bytes": record["bytes"],
        "sha256": record["sha256"],
        **member_binding,
    }


def _live_source_differences(lock_members: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Compare current W26 members with the exact prepared/final generation lock."""
    repository_root = Path(__file__).resolve().parents[1]
    current = application.source_members(repository_root)
    differences: list[dict[str, Any]] = []
    for relative in sorted(set(current) | set(lock_members)):
        expected = lock_members.get(relative)
        if relative not in current:
            differences.append({"member": relative, "status": "LOCK_MEMBER_MISSING_FROM_CURRENT_SOURCE"})
            continue
        if expected is None:
            differences.append({"member": relative, "status": "CURRENT_MEMBER_ABSENT_FROM_LOCK"})
            continue
        observed = _helpers().raw_file(current[relative])[1]
        delta = {
            key: {"locked": expected.get(key), "current": observed.get(key)}
            for key in ("bytes", "sha256") if expected.get(key) != observed.get(key)
        }
        if delta:
            differences.append({"member": relative, "status": "CURRENT_MEMBER_DIFFERS_FROM_LOCK",
                                "differences": delta})
    return differences


def current_source_readiness() -> dict[str, Any]:
    """Report whether the repository's declared latest source has a qualified consumer."""
    latest = application.LATEST_SOURCE_PROFILE
    if latest not in (R7_PROFILE, R8_PROFILE):
        try:
            lock_path = Path(application.current175_source_lock(latest)).resolve()
        except Exception as exc:
            lock_path = None
            lookup_error = f"{type(exc).__name__}: {exc}"
        else:
            lookup_error = None
        lock_candidate: dict[str, Any] = {
            "source_profile": latest,
            "status": "UNPINNED_GENERATION_REQUIRES_OWN_SOURCE_REVIEW",
            "path": str(lock_path) if lock_path is not None else None,
            "exists": bool(lock_path is not None and lock_path.is_file()),
            "reviewed_sha256": None,
            "member_fingerprint_sha256": None,
        }
        if lock_path is not None and lock_path.is_file():
            raw, record = _helpers().raw_file(lock_path)
            lock_candidate["observed_sha256"] = record["sha256"]
            try:
                value = json.loads(raw.decode("utf-8", "strict"))
            except (UnicodeError, json.JSONDecodeError):
                lock_candidate["status"] = "UNPINNED_GENERATION_LOCK_MALFORMED"
            else:
                lock_candidate["declared_profile"] = value.get("profile")
                lock_candidate["declared_member_count"] = value.get("member_count")
                members = value.get("members")
                if isinstance(members, Mapping):
                    lock_candidate["observed_member_fingerprint_sha256"] = _sha(
                        canonical_bytes(dict(members))
                    )
                    lock_candidate["observed_member_record_count"] = len(members)
                    live_differences = _live_source_differences(members)
                    lock_candidate["live_source_differences"] = live_differences
                    lock_candidate["live_source_matches_observed_lock"] = not live_differences
        generation = latest.rsplit("-r", 1)[-1]
        reader_path = Path(sealed_r7.__file__).with_name(f"w26_sealed_data_r{generation}.py")
        return {
            "schema": "w26-current-source-readiness/1",
            "status": f"NOT_READY_CURRENT_R{generation.upper()}_READER_AND_APP_BUILD_PENDING",
            "latest_source_profile": latest,
            "source_lock_candidate": lock_candidate,
            "source_lock_lookup_error": lookup_error,
            "reader": {
                "path": str(reader_path),
                "exists": reader_path.is_file(),
                "status": "NO_PINNED_REVIEWED_READER_ROUTE",
            },
            "current_actual_app_artifact": "NOT_SUPPLIED_TO_THIS_BRIDGE",
            "historical_r7_app7a": "SOURCE_ARTIFACT_ONLY_NOT_CURRENT_PROFILE",
            "current_runtime_binding_created": False,
        }
    try:
        lock = source_lock_binding(latest)
    except Exception as exc:
        return {
            "schema": "w26-current-source-readiness/1",
            "status": "NOT_READY_LATEST_SOURCE_LOCK_UNVERIFIED",
            "latest_source_profile": latest,
            "reason": f"{type(exc).__name__}: {exc}",
            "current_runtime_binding_created": False,
        }
    if latest == R8_PROFILE:
        reader_path = Path(sealed_r7.__file__).with_name("w26_sealed_data_r8.py")
        lock_raw, _lock_record = _helpers().raw_file(application.CURRENT175_R8_SOURCE_LOCK)
        lock_value = json.loads(lock_raw.decode("utf-8", "strict"))
        live_differences = _live_source_differences(lock_value["members"])
        return {
            "schema": "w26-current-source-readiness/1",
            "status": ("NOT_READY_R8_PREPARED_LOCK_DOES_NOT_MATCH_LIVE_SOURCE"
                       if live_differences else
                       "NOT_READY_CURRENT_R8_READER_AND_APP_BUILD_PENDING"),
            "latest_source_profile": latest,
            "source_lock": lock,
            "live_source_membership": {
                "status": "DIFFERS_FROM_PREPARED_LOCK" if live_differences else "MATCHES_PREPARED_LOCK",
                "member_count": 175,
                "differences": live_differences,
            },
            "r8_reader": {
                "path": str(reader_path),
                "exists": reader_path.is_file(),
                "status": "NOT_IMPLEMENTED_OR_NOT_REVIEWED",
            },
            "r8_actual_app_artifact": "NOT_SUPPLIED_TO_THIS_BRIDGE",
            "historical_r7_app7a": "SOURCE_ARTIFACT_ONLY_NOT_CURRENT_R8",
            "current_runtime_binding_created": False,
        }
    return {
        "schema": "w26-current-source-readiness/1",
        "status": "NOT_READY_CURRENT_SOURCE_REQUIRES_NEW_EXACT_REVIEW",
        "latest_source_profile": latest,
        "source_lock": lock,
        "current_runtime_binding_created": False,
    }


def require_current_source_ready(profile: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed until the exact declared latest profile has a reviewed reader and App build."""
    readiness = current_source_readiness()
    requested = profile.get("source_profile") if isinstance(profile, Mapping) else None
    if (readiness["status"] != "PASS_CURRENT_SOURCE_READER_AND_APP_ARTIFACT"
            or requested != readiness.get("latest_source_profile")):
        raise CurrentSourceNotReady({
            **readiness,
            "requested_source_profile": requested,
            "current_runtime_binding_created": False,
        })
    # This generation intentionally has no current-ready path. A later reviewed
    # source bridge must implement the exact reader before returning PASS.
    raise CurrentSourceNotReady({
        **readiness,
        "status": "NOT_READY_READER_ROUTE_NOT_IMPLEMENTED",
        "requested_source_profile": requested,
        "current_runtime_binding_created": False,
    })


def verify_exact_r7_app7a_artifact() -> dict[str, Any]:
    """Run the unmodified R7 semantic reader on the pinned App7a artifacts.

    A successful result is historical source-artifact evidence only. It cannot
    satisfy the repository's latest R8 consumer or installed Windows gates.
    """
    lock = source_lock_binding(R7_PROFILE)
    root = R7_APP7A_ROOT.resolve(strict=True)
    if root != R7_APP7A_ROOT:
        raise RuntimeError("exact R7 App7a producer root is not canonical")
    helper = _helpers()
    archive_review_raw, archive_review_record = helper.raw_file(R7_ARCHIVE_REVIEW_PATH)
    archive_review = json.loads(archive_review_raw.decode("utf-8", "strict"))
    if (archive_review_record.get("sha256") != R7_ARCHIVE_REVIEW_SHA256
            or archive_review.get("status") != "PASS_SCOPED_R7_APP7A_ACTUAL_ARCHIVES_NOT_RELEASE"):
        raise RuntimeError("R7 App7a archive review differs from its scoped actual-evidence receipt")
    result_path = root / "APPLICATION_BUNDLE_RESULT.json"
    wheel_path = root / "appwheel" / "comsol_mcp-0.1.10-py3-none-any.whl"
    result_record = helper.raw_file(result_path)[1]
    wheel_record = helper.raw_file(wheel_path)[1]
    if (result_record.get("sha256") != R7_APP7A_RESULT_SHA256
            or wheel_record.get("sha256") != R7_APP7A_WHEEL_SHA256):
        raise RuntimeError("actual R7 App7a result or universal wheel differs from the reviewed artifact")

    profile = sealed_r7.build_current_application_profile(
        root, lifecycle_r7._loader_records(), sealed_r7.BASE,
    )
    if profile.get("source_profile") != R7_PROFILE:
        raise RuntimeError("R7 producer did not emit its exact historical source-profile name")
    try:
        verified = sealed_r7.current_application_profile(profile, sealed_r7.BASE)
    except Exception as exc:
        drift = _r7_original_source_differences(root)
        error = f"{type(exc).__name__}: {exc}"
        source_drift = "fixed regular checkpoint changed" in error and bool(drift)
        return {
            "schema": "w26-historical-r7-app7a-reader-audit/1",
            "status": ("REFUSED_R7_READER_DETECTED_SOURCE_DRIFT" if source_drift
                       else "REFUSED_R7_SEMANTIC_READER"),
            "source_lock": lock,
            "app7a_root": str(root),
            "result": result_record,
            "wheel": wheel_record,
            "archive_review": archive_review_record,
            "reader": "tools.w26_sealed_data_r7.current_application_profile",
            "reader_error": error,
            "original_source_differences": drift,
            "current_source_profile": application.LATEST_SOURCE_PROFILE,
            "current_binding_eligible": False,
            "current_runtime_binding_created": False,
        }
    if (verified.get("result", {}).get("sha256") != R7_APP7A_RESULT_SHA256
            or verified.get("wheel", {}).get("sha256") != R7_APP7A_WHEEL_SHA256):
        raise RuntimeError("R7 semantic reader output differs from the exact App7a result/wheel")
    return {
        "schema": "w26-historical-r7-app7a-reader-audit/1",
        "status": "PASS_R7_APP7A_ARTIFACT_ONLY_NOT_CURRENT_R8",
        "source_lock": lock,
        "app7a_root": str(root),
        "result": verified["result"],
        "wheel": verified["wheel"],
        "archive_review": archive_review_record,
        "reader": "tools.w26_sealed_data_r7.current_application_profile",
        "current_source_profile": application.LATEST_SOURCE_PROFILE,
        "current_binding_eligible": False,
        "current_runtime_binding_created": False,
    }
