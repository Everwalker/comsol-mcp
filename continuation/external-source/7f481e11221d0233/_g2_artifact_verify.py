"""Read-only verifier for the supported ``comsol-mcp-full-release/1`` bundle.

The verifier parses ZIP, wheel, JSON, lock, and metadata bytes only. It does not
extract members, import bundled code, install wheels, or call a builder/runtime.
Unknown marker syntax and resource-limit hits remain incomplete instead of
being treated as successful validation.
"""
from __future__ import annotations

from email.parser import BytesParser
from email.policy import compat32
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from contextvars import ContextVar
from typing import Any, Mapping
import zipfile
import zlib

from ._execution_contract import ExecutionContractError


FORMAT_SCHEMA = "comsol-mcp-full-release/1"
FORMAT_KIND = "OFFLINE_INSTALL_BUNDLE"
SUPPORTED_TARGETS = {"win_amd64", "macos_arm64", "macos_x86_64"}
SUPPORTED_PYTHON = "3.12"
MANIFEST_NAME = "OFFLINE_BUNDLE_MANIFEST.json"

# Hard ceilings apply before payload parsing. The three accepted bundles are
# below these bounds; a hit is an incomplete verification, never a pass.
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_OUTER_ENTRIES = 512
MAX_OUTER_MEMBER_BYTES = 32 * 1024 * 1024
MAX_OUTER_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_METADATA_BYTES = 4 * 1024 * 1024
MAX_WHEEL_BYTES = 24 * 1024 * 1024
MAX_TOTAL_WHEEL_BYTES = 64 * 1024 * 1024
MAX_WHEEL_ENTRIES = 20_000
MAX_WHEEL_MEMBER_BYTES = 64 * 1024 * 1024
MAX_WHEEL_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_BUNDLE_NESTED_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_FINDINGS = 100
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
_VERSION_RE = re.compile(
    r"^v?(?P<release>[0-9]+(?:\.[0-9]+)*)(?:(?P<pre>a|b|rc)(?P<pre_n>[0-9]+)?)?"
    r"(?:(?P<post>\.post|post)(?P<post_n>[0-9]+)?)?(?:(?P<dev>\.dev|dev)(?P<dev_n>[0-9]+)?)?$",
    re.IGNORECASE,
)
_MARKER_TOKEN = re.compile(
    r"\s*(?:(===|==|!=|<=|>=|~=|<|>)|(\band\b|\bor\b|\bnot\s+in\b|\bin\b)"
    r"|(\()|(\))|('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")"
    r"|([A-Za-z_][A-Za-z0-9_]*))",
    re.IGNORECASE,
)


class _MarkerUnsupported(ValueError):
    pass


class _Report:
    def __init__(self, *, project_id: str, artifact_id: str, package_format: str = "UNKNOWN",
                 format_schema: str | None = None, target: str | None = None,
                 python: str | None = None):
        self.project_id = project_id
        self.artifact_id = artifact_id
        self.package_format = package_format
        self.format_schema = format_schema
        self.target = target
        self.python = python
        self.member_integrity = "PASS"
        self.dependency_closure = "PASS_DECLARED_TARGET"
        self.target_format = "PASS_STATIC_TAGS"
        self.invalid = False
        self.incomplete = False
        self.unsupported = False
        self.findings: list[dict[str, str]] = []
        self.native_components: list[dict[str, str]] = []

    def finding(self, code: str, severity: str, detail: str, member: str | None = None) -> None:
        item = {"code": code, "severity": severity, "detail": detail[:240]}
        if member is not None:
            try:
                safe_member = _safe_name(member)
            except ValueError:
                safe_member = None
            if safe_member is not None:
                item["member"] = safe_member[:240]
        if len(self.findings) < MAX_FINDINGS:
            self.findings.append(item)
        elif len(self.findings) == MAX_FINDINGS:
            self.findings.append({"code": "FINDINGS_TRUNCATED", "severity": "WARNING", "detail": "additional findings were omitted"})

    def member_failure(self, code: str, detail: str, member: str | None = None) -> None:
        self.invalid = True
        self.member_integrity = "FAIL"
        self.finding(code, "ERROR", detail, member)

    def dependency_failure(self, code: str, detail: str, member: str | None = None) -> None:
        self.invalid = True
        self.dependency_closure = "FAIL"
        self.finding(code, "ERROR", detail, member)

    def target_failure(self, code: str, detail: str, member: str | None = None) -> None:
        self.invalid = True
        self.target_format = "FAIL"
        self.finding(code, "ERROR", detail, member)

    def mark_incomplete(self, code: str, detail: str, *, category: str = "member", member: str | None = None) -> None:
        self.incomplete = True
        if category == "member":
            self.member_integrity = "INCOMPLETE"
        elif category == "dependency":
            self.dependency_closure = "INCOMPLETE"
        elif category == "target":
            self.target_format = "INCOMPLETE"
        self.finding(code, "WARNING", detail, member)

    def mark_unsupported(self, code: str, detail: str, *, category: str = "target") -> None:
        self.unsupported = True
        if category == "member":
            self.member_integrity = "INCOMPLETE"
        elif category == "dependency":
            self.dependency_closure = "INCOMPLETE"
        else:
            self.target_format = "INCOMPLETE"
        self.finding(code, "WARNING", detail)

    def data(self) -> dict[str, Any]:
        verdict = ("INVALID" if self.invalid else "INCOMPLETE" if self.incomplete
                   else "UNSUPPORTED" if self.unsupported else "VERIFIED_DECLARED_PACKAGE_CONTENT")
        return {
            "data_schema_version": 1,
            "project_id": self.project_id,
            "artifact_id": self.artifact_id,
            "format": self.package_format,
            "format_schema": self.format_schema,
            "format_verdict": verdict,
            "member_integrity": self.member_integrity,
            "dependency_closure": self.dependency_closure,
            "target_format": self.target_format,
            "declared_target": self.target,
            "declared_python": self.python,
            "producer_trust": "UNVERIFIED_UNLESS_EXISTING_AUTHORITATIVE_PRODUCER_PROOF",
            "native_compatibility": "NOT_RUN",
            "native_components": self.native_components,
            "findings": self.findings,
            "source_note": "Bundled frozen source snapshot is not compared with the current HEAD.",
            "resource_budget_note": (
                "Archive 128 MiB; 512 outer entries; 128 MiB expanded; 32 MiB/member; "
                "64 MiB aggregate wheel bytes; 20,000 entries/wheel; 256 MiB expanded/wheel; "
                "512 MiB aggregate expanded wheel members. "
                "Any limit hit yields INCOMPLETE."
            ),
        }


def _sha256_handle(handle: Any) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    handle.seek(0)
    while True:
        block = handle.read(1024 * 1024)
        if not block:
            break
        total += len(block)
        digest.update(block)
    return total, digest.hexdigest()


def _safe_name(raw: str, *, is_directory: bool = False) -> str:
    if (not isinstance(raw, str) or not raw or "\x00" in raw or "\\" in raw
            or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw)):
        raise ValueError("unsafe member name")
    name = raw[:-1] if is_directory and raw.endswith("/") else raw
    parts = name.split("/")
    if not name or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("unsafe member name")
    if PurePosixPath(name).as_posix() != name:
        raise ValueError("noncanonical member name")
    return name


def _read_zip_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, *, limit: int,
                     keep: bool = False) -> tuple[int, str, int, bytes | None]:
    digest = hashlib.sha256()
    crc = 0
    size = 0
    payload = bytearray() if keep else None
    with archive.open(info, "r") as stream:
        while True:
            block = stream.read(64 * 1024)
            if not block:
                break
            size += len(block)
            if size > limit:
                raise OverflowError("member size limit")
            digest.update(block)
            crc = zlib.crc32(block, crc)
            if payload is not None:
                payload.extend(block)
    if size != info.file_size or (crc & 0xFFFFFFFF) != info.CRC:
        raise ValueError("member size or CRC mismatch")
    return size, digest.hexdigest(), crc & 0xFFFFFFFF, bytes(payload) if payload is not None else None


def _regular_member(info: zipfile.ZipInfo) -> bool:
    mode = stat.S_IFMT((info.external_attr >> 16) & 0xFFFF)
    if info.is_dir():
        return mode in (0, stat.S_IFDIR)
    return mode in (0, stat.S_IFREG)


class _NestedExpandedBudget:
    def __init__(self, limit: int):
        self.limit = limit
        self.used = 0

    @property
    def remaining(self) -> int:
        return self.limit - self.used

    def consume(self, size: int) -> None:
        if size < 0 or size > self.remaining:
            raise OverflowError("bundle nested expanded-byte budget")
        self.used += size


_ACTIVE_NESTED_EXPANDED_BUDGET: ContextVar[_NestedExpandedBudget | None] = ContextVar(
    "artifact_verify_nested_expanded_budget", default=None,
)


def _json_object(payload: bytes) -> dict[str, Any] | None:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON object key")
            value[key] = item
        return value

    def reject_nonfinite(value: str) -> None:
        raise ValueError("non-finite JSON number")

    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_nonfinite,
        )
    except (UnicodeDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _version_key(value: str) -> tuple[tuple[int, ...], tuple[int, int], tuple[int, int], tuple[int, int]]:
    match = _VERSION_RE.fullmatch(value)
    if match is None or "+" in value or "!" in value:
        raise _MarkerUnsupported("unsupported version syntax")
    release = tuple(int(part) for part in match.group("release").split("."))
    pre = match.group("pre")
    post = match.group("post")
    dev = match.group("dev")
    if pre:
        pre_key = ({"a": 0, "b": 1, "rc": 2}[pre.lower()], int(match.group("pre_n") or 0))
    elif dev and not post:
        # A pure development release sorts before every prerelease of this
        # release. A development release after a post release does not.
        pre_key = (-1, 0)
    else:
        pre_key = (3, 0)
    post_key = (1, int(match.group("post_n") or 0)) if post else (0, 0)
    dev_key = (0, int(match.group("dev_n") or 0)) if dev else (1, 0)
    return release, pre_key, post_key, dev_key


def _version_order_key(value: str, width: int = 0) -> tuple[Any, ...]:
    release, pre, post, dev = _version_key(value)
    return release + (0,) * max(0, width - len(release)), pre, post, dev


def _compare_version(left: str, operator: str, right: str) -> bool:
    if right.endswith(".*") and operator in {"==", "!="}:
        prefix = right[:-2]
        if not prefix or any(not part.isdigit() for part in prefix.split(".")):
            raise _MarkerUnsupported("unsupported wildcard version")
        actual = _version_key(left)[0]
        expected = tuple(int(part) for part in prefix.split("."))
        matched = actual[:len(expected)] == expected
        return matched if operator == "==" else not matched
    left_release, right_release = _version_key(left)[0], _version_key(right)[0]
    width = max(len(left_release), len(right_release))
    a = _version_order_key(left, width)
    b = _version_order_key(right, width)
    if operator == "==": return a == b
    if operator == "!=": return a != b
    if operator == "<": return a < b
    if operator == "<=": return a <= b
    if operator == ">": return a > b
    if operator == ">=": return a >= b
    if operator == "~=":
        prefix = right_release[:-1] if len(right_release) > 1 else right_release
        return a >= b and left_release[:len(prefix)] == prefix
    if operator == "===":
        return left.casefold() == right.casefold()
    raise _MarkerUnsupported("unsupported version comparator")


def _version_compare(left: str, right: str) -> int:
    left_release, right_release = _version_key(left)[0], _version_key(right)[0]
    width = max(3, len(left_release), len(right_release))
    left_order = _version_order_key(left, width)
    right_order = _version_order_key(right, width)
    return (left_order > right_order) - (left_order < right_order)


def _partial_python_full_version_result(python: str, operator: str, right: str) -> bool:
    """Evaluate only full-version predicates that hold for every 3.12.x target.

    The frozen target declares major.minor, not a fabricated patch release. A
    predicate that could change within that release line is unsupported and
    must make the verification incomplete.
    """
    if right.endswith(".*") and operator in {"==", "!="}:
        prefix = right[:-2]
        if not prefix or any(not part.isdigit() for part in prefix.split(".")):
            raise _MarkerUnsupported("unsupported wildcard Python version")
        target_release = tuple(int(part) for part in python.split("."))
        expected = tuple(int(part) for part in prefix.split("."))
        matched = target_release[:len(expected)] == expected if len(expected) <= len(target_release) else False
        if len(expected) > len(target_release) and expected[:len(target_release)] == target_release:
            raise _MarkerUnsupported("patch-sensitive wildcard Python version")
        return matched if operator == "==" else not matched
    if operator not in {"<", "<=", ">", ">=", "==", "!="}:
        raise _MarkerUnsupported("unsupported full Python version comparator")
    major, minor = (int(part) for part in python.split("."))
    low, high = f"{major}.{minor}.0", f"{major}.{minor + 1}.0"
    try:
        low_cmp = _version_compare(low, right)
        high_cmp = _version_compare(high, right)
    except _MarkerUnsupported as exc:
        raise _MarkerUnsupported("unsupported full Python version syntax") from exc
    if operator == "==":
        if low_cmp <= 0 and high_cmp > 0:
            raise _MarkerUnsupported("patch-sensitive full Python equality")
        return False
    if operator == "!=":
        if low_cmp <= 0 and high_cmp > 0:
            raise _MarkerUnsupported("patch-sensitive full Python inequality")
        return True
    if operator == ">=":
        if low_cmp >= 0:
            return True
        if high_cmp <= 0:
            return False
    elif operator == ">":
        if low_cmp > 0:
            return True
        if high_cmp <= 0:
            return False
    elif operator == "<":
        if high_cmp <= 0:
            return True
        if low_cmp >= 0:
            return False
    elif operator == "<=":
        if high_cmp <= 0:
            return True
        if low_cmp > 0:
            return False
    raise _MarkerUnsupported("patch-sensitive full Python version comparison")


def _satisfies_python_target(specifier: str, python: str) -> bool:
    """Evaluate a bounded Requires-Python set without inventing a patch level."""
    value = specifier.strip()
    if not value:
        return True
    results: list[bool] = []
    unsupported = False
    for clause in value.split(","):
        match = re.fullmatch(r"\s*(===|==|!=|~=|<=|>=|<|>)\s*([A-Za-z0-9.*+!_-]+)\s*", clause)
        if match is None:
            raise _MarkerUnsupported("unsupported Requires-Python specifier")
        try:
            results.append(_partial_python_full_version_result(python, match.group(1), match.group(2)))
        except _MarkerUnsupported:
            unsupported = True
    if False in results:
        return False
    if unsupported:
        raise _MarkerUnsupported("patch-sensitive Requires-Python semantics")
    return True


def _satisfies_version(version: str, specifier: str) -> bool:
    value = specifier.strip()
    if not value:
        return True
    for clause in value.split(","):
        match = re.fullmatch(r"\s*(===|==|!=|~=|<=|>=|<|>)\s*([A-Za-z0-9.*+!_-]+)\s*", clause)
        if match is None:
            raise _MarkerUnsupported("unsupported version specifier")
        if not _compare_version(version, match.group(1), match.group(2)):
            return False
    return True


class _MarkerParser:
    def __init__(self, value: str, environment: Mapping[str, str]):
        self.environment = environment
        self.tokens: list[str] = []
        position = 0
        while position < len(value):
            match = _MARKER_TOKEN.match(value, position)
            if match is None:
                raise _MarkerUnsupported("unsupported marker token")
            token = next(group for group in match.groups() if group is not None)
            self.tokens.append(token.strip())
            position = match.end()
        self.index = 0

    def parse(self) -> bool:
        result = self._parse_or()
        if self.index != len(self.tokens):
            raise _MarkerUnsupported("trailing marker syntax")
        return result

    def _parse_or(self) -> bool:
        result = self._parse_and()
        while self._peek("or"):
            self.index += 1
            right = self._parse_and()
            result = result or right
        return result

    def _parse_and(self) -> bool:
        result = self._parse_comparison()
        while self._peek("and"):
            self.index += 1
            right = self._parse_comparison()
            result = result and right
        return result

    def _parse_comparison(self) -> bool:
        if self._peek("("):
            self.index += 1
            value = self._parse_or()
            self._consume(")")
            return value
        left = self._operand()
        if self.index >= len(self.tokens):
            if isinstance(left, bool):
                return left
            raise _MarkerUnsupported("marker comparison is incomplete")
        operator = self.tokens[self.index].lower()
        if operator not in {"==", "!=", "<", "<=", ">", ">=", "~=", "===", "in", "not in"}:
            raise _MarkerUnsupported("unsupported marker operator")
        self.index += 1
        right = self._operand()
        left_value, left_variable = self._resolve(left)
        right_value, right_variable = self._resolve(right)
        if operator in {"in", "not in"}:
            if left_variable or right_variable:
                raise _MarkerUnsupported("membership marker variables are unsupported")
            matched = str(left_value) in str(right_value)
            return not matched if operator == "not in" else matched
        if left_variable == "extra" or right_variable == "extra":
            left_value = _normalize_name(str(left_value)) if left_variable == "extra" else left_value
            right_value = _normalize_name(str(right_value)) if right_variable == "extra" else right_value
        if left_variable == "python_full_version":
            return _partial_python_full_version_result(str(self.environment["python_version"]), operator, str(right_value))
        if right_variable == "python_full_version":
            inverse = {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "==": "==", "!=": "!="}.get(operator)
            if inverse is None:
                raise _MarkerUnsupported("unsupported full Python version comparator")
            return _partial_python_full_version_result(str(self.environment["python_version"]), inverse, str(left_value))
        version_variables = {"python_version", "implementation_version"}
        if left_variable in version_variables or right_variable in version_variables:
            return _compare_version(str(left_value), operator, str(right_value))
        if operator in {"~=", "==="}:
            raise _MarkerUnsupported("marker operator requires a version variable")
        if operator == "==": return str(left_value).casefold() == str(right_value).casefold()
        if operator == "!=": return str(left_value).casefold() != str(right_value).casefold()
        if operator == "<": return str(left_value) < str(right_value)
        if operator == "<=": return str(left_value) <= str(right_value)
        if operator == ">": return str(left_value) > str(right_value)
        if operator == ">=": return str(left_value) >= str(right_value)
        raise _MarkerUnsupported("unsupported marker comparison")

    def _operand(self) -> tuple[str, bool]:
        if self.index >= len(self.tokens):
            raise _MarkerUnsupported("marker operand is missing")
        token = self.tokens[self.index]
        if token in {"(", ")"} or token.lower() in {"and", "or", "==", "!=", "<", "<=", ">", ">=", "~=", "===", "in", "not in"}:
            raise _MarkerUnsupported("marker operand is malformed")
        self.index += 1
        if token[:1] in {"'", '"'}:
            if "\\" in token:
                raise _MarkerUnsupported("escaped marker literals are unsupported")
            return token[1:-1], False
        return token, True

    def _resolve(self, value: tuple[str, bool]) -> tuple[str, str | None]:
        item, is_variable = value
        if not is_variable:
            return item, None
        key = item.lower()
        resolved = self.environment.get(key)
        if resolved is None:
            raise _MarkerUnsupported("target marker variable is unavailable")
        return resolved, key

    def _peek(self, token: str) -> bool:
        return self.index < len(self.tokens) and self.tokens[self.index].lower() == token

    def _consume(self, token: str) -> None:
        if not self._peek(token):
            raise _MarkerUnsupported("marker parentheses are unbalanced")
        self.index += 1


def _target_environment(target: str, python: str) -> dict[str, str]:
    environments = {
        "win_amd64": {"os_name": "nt", "sys_platform": "win32", "platform_system": "Windows", "platform_machine": "AMD64"},
        "macos_arm64": {"os_name": "posix", "sys_platform": "darwin", "platform_system": "Darwin", "platform_machine": "arm64"},
        "macos_x86_64": {"os_name": "posix", "sys_platform": "darwin", "platform_system": "Darwin", "platform_machine": "x86_64"},
    }
    env = dict(environments[target])
    env.update({"implementation_name": "cpython", "platform_python_implementation": "CPython",
                "python_version": python, "python_full_version": python, "extra": ""})
    return env


def _marker_matches(marker: str | None, *, target: str, python: str, extra: str = "") -> bool:
    if not marker:
        return True
    env = _target_environment(target, python)
    env["extra"] = extra
    return _MarkerParser(marker, env).parse()


def _parse_requirement(value: str) -> dict[str, Any]:
    base, separator, marker = value.partition(";")
    match = re.fullmatch(
        r"\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?:\[(?P<extras>[A-Za-z0-9._,-]+)\])?\s*(?P<specifier>.*?)\s*",
        base,
    )
    if match is None:
        raise _MarkerUnsupported("unsupported requirement syntax")
    name = match.group("name")
    if not _PACKAGE_NAME_RE.fullmatch(name):
        raise _MarkerUnsupported("invalid requirement distribution name")
    extras = {_normalize_name(part) for part in (match.group("extras") or "").split(",") if part}
    specifier = match.group("specifier")
    if specifier:
        _satisfies_version("1.0", specifier)
    return {"name": _normalize_name(name), "extras": extras, "specifier": specifier,
            "marker": marker.strip() if separator else ""}


def _logical_requirement_lines(text: str) -> list[str]:
    rows: list[str] = []
    pending = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pending += " " + line.rstrip("\\").strip()
        if not line.endswith("\\"):
            rows.append(pending.strip())
            pending = ""
    if pending:
        raise _MarkerUnsupported("requirements lock ends with an incomplete continuation")
    return rows


def _parse_requirements_lock(text: str, *, target: str, python: str) -> dict[str, dict[str, Any]]:
    active: dict[str, dict[str, Any]] = {}
    for line in _logical_requirement_lines(text):
        if line.startswith(("-", "--")):
            raise _MarkerUnsupported("requirements install directives are unsupported")
        hashes = {value.lower() for value in re.findall(r"--hash=sha256:([0-9a-fA-F]{64})", line)}
        without_hashes = re.sub(r"--hash=sha256:[0-9a-fA-F]{64}", "", line).strip()
        if not hashes:
            raise _MarkerUnsupported("requirements pin has no SHA-256 hashes")
        requirement = _parse_requirement(without_hashes)
        specifier = requirement["specifier"]
        exact = re.fullmatch(r"==\s*([A-Za-z0-9][A-Za-z0-9.+!_-]*)", specifier)
        if exact is None:
            raise _MarkerUnsupported("requirements lock must use exact version pins")
        version = exact.group(1)
        if requirement["marker"] and not _marker_matches(requirement["marker"], target=target, python=python):
            continue
        key = requirement["name"]
        prior = active.get(key)
        if prior and prior["version"] != version:
            raise ValueError("conflicting active package pins")
        if prior:
            prior["hashes"].update(hashes)
            prior["extras"].update(requirement["extras"])
        else:
            active[key] = {"name": key, "version": version, "hashes": hashes,
                           "extras": set(requirement["extras"])}
    return active


def _parse_wheel_filename(filename: str) -> dict[str, Any]:
    if not filename.endswith(".whl") or "/" in filename or "\\" in filename:
        raise ValueError("wheel filename is invalid")
    fields = filename[:-4].split("-")
    if len(fields) == 5:
        name, version, py_tag, abi_tag, platform_tag = fields
    elif len(fields) == 6:
        name, version, _build, py_tag, abi_tag, platform_tag = fields
    else:
        raise ValueError("wheel filename is invalid")
    if not _PACKAGE_NAME_RE.fullmatch(name) or not re.fullmatch(r"[A-Za-z0-9.+!_-]+", version):
        raise ValueError("wheel filename is invalid")
    tags = {
        (py, abi, platform)
        for py in py_tag.lower().split(".")
        for abi in abi_tag.lower().split(".")
        for platform in platform_tag.lower().split(".")
    }
    return {"name": _normalize_name(name), "version": version, "tags": tags}


def _parse_wheel(content: bytes, filename: str) -> dict[str, Any]:
    wheel_file = _parse_wheel_filename(filename)
    try:
        archive = zipfile.ZipFile(io.BytesIO(content), "r")
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise ValueError("wheel archive is malformed") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_WHEEL_ENTRIES:
            raise OverflowError("wheel entry budget")
        names: dict[str, zipfile.ZipInfo] = {}
        inner_total = 0
        for info in infos:
            try:
                name = _safe_name(info.orig_filename, is_directory=info.is_dir())
            except ValueError as exc:
                raise ValueError("wheel member name is unsafe") from exc
            if name in names or not _regular_member(info):
                raise ValueError("wheel has duplicate or special members")
            names[name] = info
            inner_total += info.file_size
            if info.file_size > MAX_WHEEL_MEMBER_BYTES or inner_total > MAX_WHEEL_EXPANDED_BYTES:
                raise OverflowError("wheel expanded size budget")
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        wheel_names = [name for name in names if name.endswith(".dist-info/WHEEL")]
        if len(metadata_names) != 1 or len(wheel_names) != 1:
            raise ValueError("wheel must have one METADATA and one WHEEL file")
        metadata_payload: bytes | None = None
        wheel_payload: bytes | None = None
        license_files: list[dict[str, Any]] = []
        for name, info in names.items():
            keep = (name in metadata_names or name in wheel_names or ".dist-info/licenses/" in name.lower()
                    or name.rsplit("/", 1)[-1].lower() in {"license", "license.txt", "copying", "notice", "notice.txt"})
            limit = MAX_METADATA_BYTES if keep else min(info.file_size, MAX_WHEEL_MEMBER_BYTES)
            payload_buf = bytearray() if keep else None
            digest = hashlib.sha256()
            crc = 0
            size = 0
            nested_budget = _ACTIVE_NESTED_EXPANDED_BUDGET.get()
            with archive.open(info, "r") as stream:
                while True:
                    if nested_budget is not None:
                        remaining = nested_budget.remaining
                        if remaining == 0:
                            if size < info.file_size:
                                raise OverflowError("bundle nested expanded-byte budget")
                            break
                        block = stream.read(min(64 * 1024, remaining))
                    else:
                        block = stream.read(64 * 1024)
                    if not block:
                        break
                    size += len(block)
                    if size > limit:
                        raise OverflowError("wheel member budget")
                    if nested_budget is not None:
                        nested_budget.consume(len(block))
                    digest.update(block)
                    crc = zlib.crc32(block, crc)
                    if payload_buf is not None:
                        payload_buf.extend(block)
            if size != info.file_size or (crc & 0xFFFFFFFF) != info.CRC:
                raise ValueError("wheel member CRC or size mismatch")
            payload = bytes(payload_buf) if payload_buf is not None else None
            if name in metadata_names:
                metadata_payload = payload
            elif name in wheel_names:
                wheel_payload = payload
            elif payload is not None and not info.is_dir():
                license_files.append({"path": name, "bytes": size, "sha256": digest.hexdigest()})
        if metadata_payload is None or wheel_payload is None:
            raise ValueError("wheel metadata could not be read")
        try:
            metadata = BytesParser(policy=compat32).parsebytes(metadata_payload)
            wheel_text = wheel_payload.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("wheel metadata is malformed") from exc
        name = str(metadata.get("Name", "")).strip()
        version = str(metadata.get("Version", "")).strip()
        if not name or not version or _normalize_name(name) != wheel_file["name"]:
            raise ValueError("wheel filename and METADATA identity disagree")
        try:
            versions_match = _compare_version(version, "==", wheel_file["version"])
        except _MarkerUnsupported:
            raise
        if not versions_match:
            raise ValueError("wheel filename and METADATA version disagree")
        requires_python_values = metadata.get_all("Requires-Python", [])
        if len(requires_python_values) > 1:
            raise ValueError("wheel metadata repeats Requires-Python")
        requires_python = str(requires_python_values[0]).strip() if requires_python_values else None
        internal_tags: set[tuple[str, str, str]] = set()
        for line in wheel_text.splitlines():
            if line.startswith("Tag: "):
                tag = line[5:].strip().lower().split("-")
                if len(tag) != 3 or not all(tag):
                    raise ValueError("wheel internal tag is malformed")
                internal_tags.add(tuple(tag))
        if not internal_tags or internal_tags != wheel_file["tags"]:
            raise ValueError("wheel filename and internal WHEEL tags disagree")
        return {
            "name": _normalize_name(name), "display_name": name, "version": version,
            "tags": internal_tags, "requires_dist": [str(value) for value in metadata.get_all("Requires-Dist", [])],
            "requires_python": requires_python,
            "provides_extra": {_normalize_name(str(value).strip()) for value in metadata.get_all("Provides-Extra", [])},
            "license": str(metadata.get("License-Expression") or metadata.get("License") or "UNKNOWN").strip() or "UNKNOWN",
            "license_classifiers": [str(value).strip() for value in metadata.get_all("Classifier", [])
                                    if str(value).strip().lower().startswith("license ::")],
            "license_files": sorted(license_files, key=lambda item: item["path"]),
        }


def _parse_wheel_with_budget(content: bytes, filename: str,
                             budget: _NestedExpandedBudget) -> dict[str, Any]:
    token = _ACTIVE_NESTED_EXPANDED_BUDGET.set(budget)
    try:
        return _parse_wheel(content, filename)
    finally:
        _ACTIVE_NESTED_EXPANDED_BUDGET.reset(token)


def _tag_compatible(tag: tuple[str, str, str], *, target: str, python: str) -> bool | None:
    py_tag, abi_tag, platform = tag
    major, minor = (int(value) for value in python.split("."))
    if py_tag == "py3":
        py_ok = abi_tag == "none"
    elif py_tag.startswith("cp"):
        match = re.fullmatch(r"cp([0-9])([0-9]{1,2})", py_tag)
        if match is None or int(match.group(1)) != major:
            return False
        wheel_minor = int(match.group(2))
        py_ok = ((abi_tag == "abi3" and wheel_minor <= minor)
                 or (wheel_minor == minor and abi_tag in {"none", f"cp{major}{minor}"}))
    else:
        return None
    if not py_ok:
        return False
    if platform == "any":
        return abi_tag == "none"
    if target == "win_amd64":
        return platform == "win_amd64"
    target_arch = "arm64" if target == "macos_arm64" else "x86_64"
    if platform in {target_arch, "universal2"}:
        return True
    match = re.fullmatch(r"macosx_([0-9]+)_([0-9]+)_(arm64|x86_64|universal2)", platform)
    if match is None:
        return False
    arch = match.group(3)
    # The target contract fixes architecture but deliberately does not assert
    # a minimum macOS deployment version. Check valid platform-tag syntax and
    # architecture here; native OS compatibility remains NOT_RUN.
    int(match.group(1)); int(match.group(2))
    return arch in {target_arch, "universal2"}


def _check_wheel_closure(report: _Report, *, target: str, python: str,
                         requirements_text: str, wheels: Mapping[str, dict[str, Any]]) -> None:
    try:
        pins = _parse_requirements_lock(requirements_text, target=target, python=python)
    except _MarkerUnsupported:
        report.mark_incomplete("UNSUPPORTED_REQUIREMENT_SYNTAX", "requirements lock uses unsupported marker or pin syntax", category="dependency")
        return
    except ValueError as exc:
        report.dependency_failure("CONFLICTING_ACTIVE_PINS", "requirements lock contains conflicting active pins")
        return
    if not pins:
        report.dependency_failure("EMPTY_ACTIVE_REQUIREMENTS", "requirements lock has no active target pins")
        return
    for name, pin in pins.items():
        wheel = wheels.get(name)
        if wheel is None:
            report.dependency_failure("MISSING_REQUIRED_WHEEL", f"required wheel {name} is missing")
            continue
        if wheel["version"] != pin["version"]:
            report.dependency_failure("PINNED_VERSION_MISMATCH", f"wheel version does not match the active pin for {name}", wheel["file"])
        if wheel["sha256"] not in pin["hashes"]:
            report.dependency_failure("PINNED_HASH_MISMATCH", f"wheel hash is not allowed by the active pin for {name}", wheel["file"])
        if pin["extras"] - wheel["provides_extra"]:
            report.mark_incomplete("UNSUPPORTED_REQUESTED_EXTRA", "requirements lock requests an extra not declared by its wheel", category="dependency", member=wheel["file"])
    for name, wheel in wheels.items():
        if name not in pins:
            report.dependency_failure("UNPINNED_WHEEL", f"wheel {name} has no active target pin", wheel["file"])
    if report.dependency_closure == "FAIL":
        return

    active_extras = {name: set(pin["extras"]) for name, pin in pins.items()}
    visited: set[tuple[str, tuple[str, ...]]] = set()
    pending = list(sorted(wheels))
    while pending:
        name = pending.pop(0)
        wheel = wheels[name]
        extras = active_extras.setdefault(name, set())
        state = (name, tuple(sorted(extras)))
        if state in visited:
            continue
        visited.add(state)
        marker_extras = [""] + sorted(extras & wheel["provides_extra"])
        for raw_requirement in wheel["requires_dist"]:
            try:
                requirement = _parse_requirement(raw_requirement)
                active = False
                for extra in marker_extras:
                    if _marker_matches(requirement["marker"], target=target, python=python, extra=extra):
                        active = True
                        break
                if not active:
                    continue
                dependency = wheels.get(requirement["name"])
                if dependency is None:
                    report.dependency_failure("MISSING_DECLARED_DEPENDENCY", f"active dependency {requirement['name']} is not included", wheel["file"])
                    continue
                if not _satisfies_version(dependency["version"], requirement["specifier"]):
                    report.dependency_failure("DEPENDENCY_VERSION_CONFLICT", f"included {requirement['name']} does not satisfy a declared dependency", wheel["file"])
                    continue
                if requirement["extras"] - dependency["provides_extra"]:
                    report.mark_incomplete(
                        "UNSUPPORTED_REQUESTED_EXTRA",
                        "dependency requests an extra not declared by its wheel",
                        category="dependency", member=dependency["file"],
                    )
                if requirement["extras"] - active_extras.setdefault(requirement["name"], set()):
                    active_extras[requirement["name"]].update(requirement["extras"])
                    pending.append(requirement["name"])
            except _MarkerUnsupported:
                report.mark_incomplete("UNSUPPORTED_MARKER_OR_REQUIREMENT", "wheel dependency uses unsupported PEP 508 semantics", category="dependency", member=wheel["file"])


def _bundle_manifest(report: _Report, archive: zipfile.ZipFile, infos: list[zipfile.ZipInfo]) -> tuple[dict[str, Any] | None, dict[str, zipfile.ZipInfo]]:
    files: dict[str, zipfile.ZipInfo] = {}
    directories: set[str] = set()
    for info in infos:
        try:
            name = _safe_name(info.orig_filename, is_directory=info.is_dir())
        except ValueError:
            report.member_failure("UNSAFE_MEMBER_NAME", "archive contains an unsafe member name")
            continue
        if name in files or name in directories:
            report.member_failure("DUPLICATE_MEMBER", "archive contains duplicate member names", name)
            continue
        if not _regular_member(info):
            report.member_failure("SPECIAL_MEMBER", "archive contains a symlink or special file", name)
            continue
        if info.is_dir():
            directories.add(name)
            report.member_failure("UNDECLARED_DIRECTORY_MEMBER", "archive contains an unlisted directory entry", name)
        else:
            files[name] = info
    manifest_info = files.get(MANIFEST_NAME)
    if manifest_info is None:
        report.member_failure("MISSING_BUNDLE_MANIFEST", "OFFLINE_BUNDLE_MANIFEST.json is missing")
        return None, files
    try:
        size, _digest, _crc, manifest_bytes = _read_zip_member(
            archive, manifest_info, limit=MAX_METADATA_BYTES, keep=True,
        )
    except OverflowError:
        report.mark_incomplete("BUNDLE_RESOURCE_LIMIT", "bundle manifest exceeds the metadata read limit")
        return None, files
    except (zipfile.BadZipFile, RuntimeError, OSError, ValueError, zlib.error):
        report.member_failure("BUNDLE_MANIFEST_CRC_OR_READ_FAILURE", "bundle manifest could not be read with valid size and CRC", MANIFEST_NAME)
        return None, files
    if size != manifest_info.file_size or manifest_bytes is None:
        report.member_failure("BUNDLE_MANIFEST_SIZE_MISMATCH", "bundle manifest size is inconsistent", MANIFEST_NAME)
        return None, files
    manifest = _json_object(manifest_bytes)
    if manifest is None:
        report.member_failure("INVALID_BUNDLE_MANIFEST", "bundle manifest is not a UTF-8 JSON object", MANIFEST_NAME)
        return None, files
    report.format_schema = FORMAT_SCHEMA if manifest.get("schema") == FORMAT_SCHEMA else None
    if manifest.get("kind") == FORMAT_KIND:
        report.package_format = FORMAT_KIND
    if manifest.get("schema") != FORMAT_SCHEMA or manifest.get("kind") != FORMAT_KIND:
        report.mark_unsupported("UNSUPPORTED_BUNDLE_FORMAT", "bundle format kind or schema is unsupported", category="member")
        return manifest, files
    target = manifest.get("target")
    python = manifest.get("python")
    report.python = python if isinstance(python, str) and python == SUPPORTED_PYTHON else None
    if not isinstance(target, str):
        report.target_failure("INVALID_TARGET_TYPE", "bundle target must be a string", MANIFEST_NAME)
        report.target = None
    else:
        report.target = target if target in SUPPORTED_TARGETS else None
    if (isinstance(target, str) and target not in SUPPORTED_TARGETS) or python != SUPPORTED_PYTHON:
        report.mark_unsupported("UNSUPPORTED_TARGET_ENVIRONMENT", "bundle target or Python version is outside the supported initial set")
    rows = manifest.get("files_excluding_this_manifest")
    if not isinstance(rows, list):
        report.member_failure("INVALID_MEMBER_MANIFEST", "bundle member list is missing or malformed", MANIFEST_NAME)
        return manifest, files
    expected: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "bytes", "sha256"}:
            report.member_failure("INVALID_MEMBER_RECORD", "bundle member record has an invalid shape", MANIFEST_NAME)
            continue
        name = row.get("path")
        if not isinstance(name, str):
            report.member_failure("INVALID_MEMBER_NAME", "bundle member path is missing", MANIFEST_NAME)
            continue
        try:
            canonical_name = _safe_name(name)
        except ValueError:
            report.member_failure("UNSAFE_MANIFEST_MEMBER", "bundle manifest contains an unsafe member path", "OFFLINE_BUNDLE_MANIFEST.json")
            continue
        size_value = row.get("bytes")
        digest = row.get("sha256")
        if (canonical_name == MANIFEST_NAME or isinstance(size_value, bool) or not isinstance(size_value, int)
                or size_value < 0 or not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest)):
            report.member_failure("INVALID_MEMBER_RECORD", "bundle member size, digest, or self-exclusion is invalid", canonical_name)
            continue
        if canonical_name in expected:
            report.member_failure("DUPLICATE_MANIFEST_MEMBER", "bundle manifest lists a member more than once", canonical_name)
        expected[canonical_name] = {"bytes": size_value, "sha256": digest}
    expected_set = set(expected) | {MANIFEST_NAME}
    if set(files) != expected_set:
        missing = sorted(expected_set - set(files))
        extra = sorted(set(files) - expected_set)
        for name in missing[:MAX_FINDINGS]:
            report.member_failure("MISSING_DECLARED_MEMBER", "a declared package member is missing", name)
        for name in extra[:MAX_FINDINGS]:
            report.member_failure("UNDECLARED_MEMBER", "archive contains a member not listed in the manifest", name)
    return manifest, files


def _verify_outer_members(report: _Report, archive: zipfile.ZipFile, files: Mapping[str, zipfile.ZipInfo],
                          manifest: Mapping[str, Any]) -> dict[str, bytes]:
    rows = manifest.get("files_excluding_this_manifest")
    if not isinstance(rows, list):
        return {}
    expected = {row["path"]: row for row in rows if isinstance(row, dict) and isinstance(row.get("path"), str)}
    total_expanded = sum(info.file_size for info in files.values())
    if len(files) > MAX_OUTER_ENTRIES or total_expanded > MAX_OUTER_EXPANDED_BYTES:
        report.mark_incomplete("BUNDLE_RESOURCE_LIMIT", "outer archive exceeds the entry or expanded-size budget")
        return {}
    selected: dict[str, bytes] = {}
    total_wheel_bytes = 0
    for name in sorted(set(files) - {MANIFEST_NAME}):
        info = files[name]
        row = expected.get(name)
        if row is None:
            continue
        is_wheel = name.startswith("wheelhouse/") and name.endswith(".whl")
        limit = MAX_WHEEL_BYTES if is_wheel else MAX_OUTER_MEMBER_BYTES
        if info.file_size > limit:
            report.mark_incomplete("BUNDLE_RESOURCE_LIMIT", "archive member exceeds its individual size budget", member=name)
            continue
        if is_wheel:
            total_wheel_bytes += info.file_size
            if total_wheel_bytes > MAX_TOTAL_WHEEL_BYTES:
                report.mark_incomplete("BUNDLE_RESOURCE_LIMIT", "nested wheel bytes exceed their aggregate budget", member=name)
                continue
        keep = (not is_wheel and (name in {
            "PACKAGE_MANIFEST.json", "SBOM.cdx.json", "LICENSES.json",
            "locks/requirements.lock", "locks/uv.lock",
        } or name.startswith(("evidence/", "licenses/")))) or is_wheel
        try:
            size, digest, _crc, payload = _read_zip_member(
                archive, info, limit=limit,
                keep=keep,
            )
        except OverflowError:
            report.mark_incomplete("BUNDLE_RESOURCE_LIMIT", "archive member exceeded its streamed size budget", member=name)
            continue
        except (zipfile.BadZipFile, RuntimeError, OSError, ValueError, zlib.error):
            report.member_failure("MEMBER_CRC_OR_READ_FAILURE", "archive member failed size or CRC validation", name)
            continue
        if size != row.get("bytes") or digest != row.get("sha256"):
            report.member_failure("MEMBER_HASH_MISMATCH", "archive member does not match its declared size and SHA-256", name)
            continue
        if payload is not None:
            selected[name] = payload
    return selected


def _verify_package_manifest(report: _Report, manifest: Mapping[str, Any], selected: Mapping[str, bytes],
                             actual_members: Mapping[str, zipfile.ZipInfo]) -> dict[str, Any] | None:
    payload = selected.get("PACKAGE_MANIFEST.json")
    package = _json_object(payload) if payload is not None else None
    if package is None:
        report.member_failure("INVALID_PACKAGE_MANIFEST", "PACKAGE_MANIFEST.json is missing or malformed", "PACKAGE_MANIFEST.json")
        return None
    if package.get("schema") != FORMAT_SCHEMA:
        report.mark_unsupported("UNSUPPORTED_PACKAGE_MANIFEST", "package manifest schema is unsupported", category="dependency")
    target, python = manifest.get("target"), manifest.get("python")
    report_entries = package.get("wheels")
    if (package.get("target") != target or package.get("python") != python
            ):
        report.target_failure("PACKAGE_TARGET_BINDING_MISMATCH", "package manifest target binding is inconsistent", "PACKAGE_MANIFEST.json")
    # target_tags_verified is descriptive metadata only; wheel tags are parsed below.
    if not isinstance(report_entries, list) or package.get("wheel_count") != len(report_entries):
        report.dependency_failure("INVALID_WHEEL_MANIFEST", "package manifest wheel list or count is invalid", "PACKAGE_MANIFEST.json")
        return package
    expected_wheels: dict[str, dict[str, Any]] = {}
    for row in report_entries:
        if not isinstance(row, dict) or not isinstance(row.get("file"), str):
            report.dependency_failure("INVALID_WHEEL_RECORD", "package manifest contains an invalid wheel record", "PACKAGE_MANIFEST.json")
            continue
        filename = row["file"]
        try:
            wheel_name = _parse_wheel_filename(filename)["name"]
        except ValueError:
            report.dependency_failure("INVALID_WHEEL_FILENAME", "package manifest wheel filename is invalid", filename[:240])
            continue
        if filename in expected_wheels or wheel_name in expected_wheels:
            report.dependency_failure("DUPLICATE_WHEEL_RECORD", "package manifest contains duplicate wheel identity", filename[:240])
        expected_wheels[wheel_name] = row
    archive_wheels = {name for name in actual_members if name.startswith("wheelhouse/") and name.endswith(".whl")}
    expected_archive_names = {"wheelhouse/" + row["file"] for row in report_entries if isinstance(row, dict) and isinstance(row.get("file"), str)}
    if archive_wheels != expected_archive_names:
        report.dependency_failure("WHEELHOUSE_BINDING_MISMATCH", "package manifest wheels do not match the archived wheelhouse")
    required_metadata = {
        "requirements": ("requirements", "locks/requirements.lock", "requirements_included", "requirements_sha256"),
        "source_lock": ("source_lock", "locks/uv.lock", "source_lock_included", "source_lock_sha256"),
    }
    for key, (manifest_key, included_path, included_key, digest_key) in required_metadata.items():
        outer_ref = manifest.get(manifest_key)
        package_ref = package.get(manifest_key)
        payload_bytes = selected.get(included_path)
        digest = hashlib.sha256(payload_bytes).hexdigest() if payload_bytes is not None else None
        outer_filename = outer_ref.get("filename") if isinstance(outer_ref, dict) else None
        package_filename = package_ref.get("filename") if isinstance(package_ref, dict) else None
        filename_is_safe = (
            isinstance(outer_filename, str) and bool(outer_filename.strip())
            and outer_filename == Path(outer_filename).name
            and "/" not in outer_filename and "\\" not in outer_filename
            and outer_filename not in {".", ".."}
        )
        if (not filename_is_safe or package_filename != outer_filename
                or not isinstance(digest, str)
                or outer_ref.get("sha256") != digest
                or manifest.get(included_key) != included_path
                or manifest.get(digest_key) != digest
                or not isinstance(package_ref, dict)
                or package_ref.get("sha256") != digest):
            report.member_failure("LOCK_BINDING_MISMATCH", f"included {key} filename or digest bindings disagree", included_path)
    for evidence_name, path in (("sbom", "SBOM.cdx.json"), ("licenses", "LICENSES.json")):
        evidence = package.get("evidence_files", {}).get(evidence_name) if isinstance(package.get("evidence_files"), dict) else None
        expected_digest = hashlib.sha256(selected[path]).hexdigest() if path in selected else None
        if (not isinstance(evidence, dict) or evidence.get("filename") != path
                or not isinstance(expected_digest, str) or evidence.get("sha256") != expected_digest):
            report.member_failure("EVIDENCE_BINDING_MISMATCH", f"package manifest {evidence_name} binding disagrees", path)
    return package


def _verify_derived_wheel_binding(report: _Report, package: Mapping[str, Any], selected: Mapping[str, bytes],
                                 wheels: Mapping[str, dict[str, Any]]) -> Mapping[str, Any] | None:
    derived = package.get("derived_wheel")
    if derived is None:
        return None
    if not isinstance(derived, Mapping):
        report.member_failure("INVALID_DERIVED_WHEEL_RECORD", "derived wheel binding is malformed")
        return None
    native = derived.get("native_component")
    wheel_ref = derived.get("wheel")
    package_ref = derived.get("package")
    provenance = derived.get("provenance")
    source_lock_sha256 = derived.get("source_lock_sha256")
    package_match = re.fullmatch(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*([A-Za-z0-9.+!_-]+)\s*", package_ref) if isinstance(package_ref, str) else None
    wheel_name = _normalize_name(package_match.group(1)) if package_match else None
    wheel = wheels.get(wheel_name) if wheel_name else None
    package_rows = package.get("wheels")
    package_by_name = {
        _normalize_name(row.get("name", "")): row
        for row in package_rows
        if isinstance(row, Mapping) and isinstance(row.get("name"), str)
    } if isinstance(package_rows, list) else {}
    package_row = package_by_name.get(wheel_name) if wheel_name else None
    wheel_file = wheel_ref.get("filename") if isinstance(wheel_ref, Mapping) else None
    wheel_tag = wheel_ref.get("tag") if isinstance(wheel_ref, Mapping) else None
    wheel_tags = package_row.get("wheel_tags") if isinstance(package_row, Mapping) else None
    version_matches = False
    version_unsupported = False
    if package_match and wheel is not None:
        try:
            version_matches = _compare_version(package_match.group(2), "==", wheel["version"])
        except _MarkerUnsupported:
            version_unsupported = True
            report.mark_incomplete("UNSUPPORTED_DERIVED_WHEEL_VERSION", "derived wheel version syntax is unsupported", category="member")
    derived_wheel_ok = (
        isinstance(wheel_ref, Mapping) and isinstance(package_row, Mapping) and wheel is not None
        and isinstance(wheel_file, str) and wheel_file == Path(wheel_file).name and "/" not in wheel_file and "\\" not in wheel_file
        and wheel_file == Path(wheel["file"]).name
        and wheel_ref.get("sha256") == wheel["sha256"]
        and wheel_ref.get("bytes") == wheel["size"]
        and isinstance(wheel_tag, str) and isinstance(wheel_tags, list)
        and wheel_tag in wheel_tags
        and wheel_tag in ["-".join(tag) for tag in wheel["tags"]]
        and source_lock_sha256 == (package.get("source_lock", {}).get("sha256") if isinstance(package.get("source_lock"), Mapping) else None)
    )
    provenance_filename = provenance.get("filename") if isinstance(provenance, Mapping) else None
    provenance_sha256 = provenance.get("sha256") if isinstance(provenance, Mapping) else None
    provenance_payload = selected.get("evidence/" + provenance_filename) if isinstance(provenance_filename, str) else None
    provenance_ok = (
        isinstance(provenance_filename, str) and provenance_filename == Path(provenance_filename).name
        and "/" not in provenance_filename and "\\" not in provenance_filename
        and isinstance(provenance_sha256, str) and _SHA256_RE.fullmatch(provenance_sha256)
        and provenance_payload is not None and hashlib.sha256(provenance_payload).hexdigest() == provenance_sha256
    )
    if not derived_wheel_ok or not provenance_ok or (not version_matches and not version_unsupported):
        report.member_failure("DERIVED_WHEEL_BINDING_MISMATCH", "derived wheel, source lock, or evidence does not bind the packaged wheel")
    if not isinstance(native, Mapping):
        report.mark_unsupported("UNSUPPORTED_DERIVED_WHEEL_COMPONENT", "derived wheel has no supported native component declaration", category="member")
        return None
    name = native.get("name")
    version = native.get("version")
    linkage = native.get("linkage")
    license_name = native.get("license")
    source_sha256 = native.get("source_sha256")
    license_file = native.get("license_file")
    safe_native = (
        isinstance(name, str) and _PACKAGE_NAME_RE.fullmatch(name) is not None
        and isinstance(version, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}", version) is not None
        and linkage == "static"
        and isinstance(license_name, str) and 0 < len(license_name) <= 96
        and not any(ord(char) < 32 for char in license_name)
        and isinstance(source_sha256, str) and _SHA256_RE.fullmatch(source_sha256) is not None
        and isinstance(license_file, Mapping)
        and isinstance(license_file.get("path"), str)
        and isinstance(license_file.get("bytes"), int) and not isinstance(license_file.get("bytes"), bool)
        and license_file.get("bytes") >= 0
        and isinstance(license_file.get("sha256"), str) and _SHA256_RE.fullmatch(license_file.get("sha256")) is not None
    )
    if safe_native:
        try:
            _safe_name(license_file["path"])
        except ValueError:
            safe_native = False
    if not safe_native:
        report.member_failure("INVALID_NATIVE_COMPONENT_RECORD", "native component declaration is malformed")
        return None
    return native


def _canonical_license_file_records(value: Any) -> list[tuple[str, int, str]] | None:
    """Normalize a license-file inventory without making list order significant."""
    if not isinstance(value, list):
        return None
    records: list[tuple[str, int, str]] = []
    for row in value:
        if not isinstance(row, Mapping) or set(row) != {"path", "bytes", "sha256"}:
            return None
        path, size, digest = row.get("path"), row.get("bytes"), row.get("sha256")
        if (not isinstance(path, str) or isinstance(size, bool) or not isinstance(size, int) or size < 0
                or not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest)):
            return None
        try:
            if _safe_name(path) != path:
                return None
        except ValueError:
            return None
        records.append((path, size, digest))
    return sorted(records)


def _verify_components(report: _Report, package: Mapping[str, Any], selected: Mapping[str, bytes],
                       wheels: Mapping[str, dict[str, Any]]) -> None:
    sbom = _json_object(selected.get("SBOM.cdx.json", b""))
    licenses = _json_object(selected.get("LICENSES.json", b""))
    if sbom is None or licenses is None:
        report.member_failure("INVALID_COMPONENT_INVENTORY", "SBOM or LICENSES inventory is not a JSON object")
        return
    expected_target = package.get("target")
    if sbom.get("bomFormat") != "CycloneDX" or licenses.get("schema") != FORMAT_SCHEMA:
        report.member_failure("INVALID_COMPONENT_INVENTORY", "SBOM or LICENSES schema is unsupported")
    if sbom.get("specVersion") not in {"1.4", "1.5", "1.6"}:
        report.member_failure("INVALID_SBOM_VERSION", "SBOM specification version is malformed", "SBOM.cdx.json")
    if licenses.get("target") != expected_target:
        report.member_failure("LICENSE_TARGET_BINDING_MISMATCH", "license inventory target differs from package manifest", "LICENSES.json")
    package_rows = package.get("wheels")
    package_by_name = {_normalize_name(row.get("name", "")): row for row in package_rows
                       if isinstance(row, dict) and isinstance(row.get("name"), str)} if isinstance(package_rows, list) else {}
    native_component = _verify_derived_wheel_binding(report, package, selected, wheels)
    native_name = _normalize_name(native_component.get("name", "")) if isinstance(native_component, dict) else None
    if native_name is not None and native_name in wheels:
        report.member_failure("NATIVE_COMPONENT_IDENTITY_COLLISION", "native component must remain separate from the packaged wheel inventory")
        native_name = None

    sbom_rows = sbom.get("components")
    if not isinstance(sbom_rows, list):
        report.member_failure("INVALID_SBOM_COMPONENTS", "SBOM components are missing", "SBOM.cdx.json")
        sbom_rows = []
    sbom_by_name: dict[str, Mapping[str, Any]] = {}
    for row in sbom_rows:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            report.member_failure("INVALID_SBOM_COMPONENT", "SBOM component identity is malformed", "SBOM.cdx.json")
            continue
        key = _normalize_name(row["name"])
        if key in sbom_by_name:
            report.member_failure("DUPLICATE_SBOM_COMPONENT", "SBOM repeats a component", "SBOM.cdx.json")
        sbom_by_name[key] = row
    if set(sbom_by_name) != set(wheels) | ({native_name} if native_name else set()):
        report.member_failure("SBOM_COMPONENT_SET_MISMATCH", "SBOM components do not match wheels and declared native components", "SBOM.cdx.json")
    for name, wheel in wheels.items():
        row = sbom_by_name.get(name)
        if not isinstance(row, Mapping):
            report.member_failure("SBOM_WHEEL_MISSING", f"SBOM is missing wheel component {name}", "SBOM.cdx.json")
            continue
        hashes = row.get("hashes")
        sha_values = [item.get("content") for item in hashes if isinstance(item, dict) and item.get("alg") == "SHA-256"] if isinstance(hashes, list) else []
        properties = row.get("properties")
        tag_values = [item.get("value") for item in properties if isinstance(item, dict) and item.get("name") == "wheel.tags"] if isinstance(properties, list) else []
        if (row.get("version") != wheel["version"] or sha_values != [wheel["sha256"]]
                or tag_values != [",".join(sorted("-".join(tag) for tag in wheel["tags"]))]):
            report.member_failure("SBOM_WHEEL_BINDING_MISMATCH", f"SBOM identity or hash does not bind wheel {name}", "SBOM.cdx.json")
    license_rows = licenses.get("components")
    if not isinstance(license_rows, list):
        report.member_failure("INVALID_LICENSE_COMPONENTS", "license component list is missing", "LICENSES.json")
        license_rows = []
    license_by_name: dict[str, Mapping[str, Any]] = {}
    for row in license_rows:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            report.member_failure("INVALID_LICENSE_COMPONENT", "license component identity is malformed", "LICENSES.json")
            continue
        key = _normalize_name(row["name"])
        if key in license_by_name:
            report.member_failure("DUPLICATE_LICENSE_COMPONENT", "license inventory repeats a component", "LICENSES.json")
        license_by_name[key] = row
    expected_components = set(wheels) | ({native_name} if native_name else set())
    if set(license_by_name) != expected_components:
        report.member_failure("LICENSE_COMPONENT_SET_MISMATCH", "license components do not match wheels and declared native components", "LICENSES.json")
    package_index = package_by_name
    for name, wheel in wheels.items():
        row = license_by_name.get(name)
        package_row = package_index.get(name)
        if not isinstance(row, Mapping) or not isinstance(package_row, Mapping):
            report.member_failure("LICENSE_WHEEL_MISSING", f"license inventory is missing wheel {name}", "LICENSES.json")
            continue
        row_license_files = _canonical_license_file_records(row.get("license_files"))
        package_license_files = _canonical_license_file_records(package_row.get("license_files"))
        wheel_license_files = _canonical_license_file_records(wheel.get("license_files"))
        if (row.get("version") != wheel["version"] or row.get("license") != wheel["license"]
                or row.get("license_classifiers") != wheel["license_classifiers"]
                or row_license_files is None or row_license_files != wheel_license_files
                or package_row.get("version") != wheel["version"]
                or package_row.get("sha256") != wheel["sha256"]
                or package_row.get("bytes") != wheel["size"]
                or package_row.get("wheel_tags") != sorted("-".join(tag) for tag in wheel["tags"])
                or package_row.get("license") != wheel["license"]
                or package_row.get("license_classifiers") != wheel["license_classifiers"]
                or package_license_files is None or package_license_files != wheel_license_files):
            report.member_failure("LICENSE_WHEEL_BINDING_MISMATCH", f"package/license metadata does not bind wheel {name}", "LICENSES.json")
    if native_name:
        _verify_native_component(report, native_component, package.get("derived_wheel", {}), sbom_by_name.get(native_name), license_by_name.get(native_name), selected)
        report.native_components.append({
            "name": str(native_component.get("name", "")),
            "version": str(native_component.get("version", "")),
            "linkage": str(native_component.get("linkage", "DECLARED")),
            "status": "DECLARED_CONTENT_BINDING_ONLY",
        })


def _verify_native_component(report: _Report, component: Any, derived: Mapping[str, Any],
                              sbom_row: Any, license_row: Any, selected: Mapping[str, bytes]) -> None:
    if not isinstance(component, Mapping) or not isinstance(sbom_row, Mapping) or not isinstance(license_row, Mapping):
        report.member_failure("NATIVE_COMPONENT_BINDING_MISSING", "separately declared native component is not bound in SBOM and license inventory")
        return
    license_file = component.get("license_file")
    file_path = license_file.get("path") if isinstance(license_file, dict) else None
    payload = selected.get(file_path) if isinstance(file_path, str) else None
    if (not isinstance(file_path, str) or payload is None
            or hashlib.sha256(payload).hexdigest() != license_file.get("sha256")
            or len(payload) != license_file.get("bytes")
            or license_row.get("version") != component.get("version")
            or license_row.get("license") != component.get("license")
            or license_row.get("license_files") != [license_file]
            or sbom_row.get("version") != component.get("version")
            or not isinstance(sbom_row.get("hashes"), list)
            or not any(isinstance(row, dict) and row.get("alg") == "SHA-256" and row.get("content") == component.get("source_sha256")
                       for row in sbom_row["hashes"])):
        report.member_failure("NATIVE_COMPONENT_BINDING_MISMATCH", "declared native component does not match its source/license bindings")
    properties = sbom_row.get("properties")
    required_properties = {
        "release.component-kind": "statically-linked-native-dependency",
        "release.linkage": "static",
        "release.source.sha256": component.get("source_sha256"),
        "release.linked-into": str(derived.get("package", "")),
    }
    observed = {row.get("name"): row.get("value") for row in properties if isinstance(row, dict)} if isinstance(properties, list) else {}
    if any(observed.get(key) != value for key, value in required_properties.items()):
        report.member_failure("NATIVE_COMPONENT_SBOM_BINDING_MISMATCH", "declared native component SBOM properties disagree")


def _verify_supported_bundle(report: _Report, archive: zipfile.ZipFile, infos: list[zipfile.ZipInfo]) -> None:
    manifest, files = _bundle_manifest(report, archive, infos)
    if not isinstance(manifest, Mapping):
        report.dependency_closure = "INCOMPLETE"
        report.target_format = "INCOMPLETE"
        return
    if manifest.get("schema") != FORMAT_SCHEMA or manifest.get("kind") != FORMAT_KIND:
        report.dependency_closure = "INCOMPLETE"
        report.target_format = "INCOMPLETE"
        return
    if len(files) > MAX_OUTER_ENTRIES or sum(info.file_size for info in files.values()) > MAX_OUTER_EXPANDED_BYTES:
        report.mark_incomplete("BUNDLE_RESOURCE_LIMIT", "outer archive exceeds the entry or expanded-size budget")
        report.dependency_closure = "INCOMPLETE"
        report.target_format = "INCOMPLETE"
        return
    selected = _verify_outer_members(report, archive, files, manifest)
    if report.incomplete:
        report.dependency_closure = "INCOMPLETE"
        report.target_format = "INCOMPLETE"
        return
    package = _verify_package_manifest(report, manifest, selected, files)
    if package is None:
        return
    target = manifest.get("target")
    python = manifest.get("python")
    if not isinstance(target, str):
        report.dependency_closure = "INCOMPLETE"
        return
    if target not in SUPPORTED_TARGETS or python != SUPPORTED_PYTHON:
        report.dependency_closure = "INCOMPLETE"
        report.target_format = "INCOMPLETE"
        return
    wheel_rows = package.get("wheels")
    if not isinstance(wheel_rows, list):
        return
    parsed_wheels: dict[str, dict[str, Any]] = {}
    nested_budget = _NestedExpandedBudget(MAX_BUNDLE_NESTED_EXPANDED_BYTES)
    for row in wheel_rows:
        if not isinstance(row, dict) or not isinstance(row.get("file"), str):
            continue
        filename = row["file"]
        path = "wheelhouse/" + filename
        content = selected.get(path)
        if content is None:
            report.dependency_failure("MISSING_WHEEL_CONTENT", "wheelhouse package bytes are missing", path)
            continue
        try:
            metadata = _parse_wheel_with_budget(content, filename, nested_budget)
        except OverflowError:
            report.mark_incomplete("NESTED_WHEEL_RESOURCE_LIMIT", "nested wheels exceed a member or shared expanded-size budget", category="target", member=path)
            break
        except _MarkerUnsupported:
            report.mark_incomplete("UNSUPPORTED_WHEEL_VERSION_SYNTAX", "wheel version syntax is outside the supported PEP 440 subset", category="target", member=path)
            report.mark_incomplete("UNSUPPORTED_WHEEL_VERSION_SYNTAX", "wheel component bindings could not be completed", category="member", member=path)
            continue
        except (ValueError, zipfile.BadZipFile, RuntimeError, OSError, zlib.error) as exc:
            if isinstance(exc, ValueError) and str(exc) == "wheel filename and METADATA version disagree":
                report.target_failure("WHEEL_VERSION_MISMATCH", "wheel filename and metadata version disagree", path)
            else:
                report.target_failure("INVALID_WHEEL_METADATA", "wheel archive or internal metadata is invalid", path)
            continue
        if metadata["requires_python"]:
            try:
                python_compatible = _satisfies_python_target(metadata["requires_python"], python)
            except _MarkerUnsupported:
                report.mark_incomplete("UNSUPPORTED_REQUIRES_PYTHON", "wheel Requires-Python uses unsupported or patch-sensitive semantics", category="target", member=path)
            else:
                if not python_compatible:
                    report.target_failure("WHEEL_REQUIRES_PYTHON_MISMATCH", "wheel Requires-Python excludes the declared Python target", path)
        compatible = [_tag_compatible(tag, target=target, python=python) for tag in metadata["tags"]]
        if any(value is None for value in compatible):
            report.mark_incomplete("UNSUPPORTED_WHEEL_TAG", "wheel contains a tag syntax outside the supported target parser", category="target", member=path)
        elif not any(compatible):
            report.target_failure("INCOMPATIBLE_WHEEL_TAG", "wheel tags are incompatible with the declared target", path)
        info = files.get(path)
        if info is None:
            continue
        digest = hashlib.sha256(content).hexdigest()
        size = len(content)
        parsed_wheels[metadata["name"]] = {**metadata, "file": path, "sha256": digest, "size": size}
    if report.incomplete and report.dependency_closure == "PASS_DECLARED_TARGET":
        report.dependency_closure = "INCOMPLETE"
    if not report.invalid and not report.incomplete and report.dependency_closure != "FAIL":
        requirements = selected.get("locks/requirements.lock")
        if requirements is None:
            report.dependency_failure("MISSING_REQUIREMENTS_LOCK", "requirements lock was not verified", "locks/requirements.lock")
        else:
            try:
                _check_wheel_closure(
                    report, target=target, python=python,
                    requirements_text=requirements.decode("utf-8"), wheels=parsed_wheels,
                )
            except UnicodeDecodeError:
                report.dependency_failure("INVALID_REQUIREMENTS_LOCK", "requirements lock is not UTF-8", "locks/requirements.lock")
    if parsed_wheels and len(parsed_wheels) == len(wheel_rows):
        _verify_components(report, package, selected, parsed_wheels)


def verify_registered_bundle(project_root: str | Path, record: Mapping[str, Any], *,
                             project_id: str, artifact_id: str,
                             current_host_identity: str, current_engine_host_identity: str) -> dict[str, Any]:
    """Verify one schema-v2 registered file while retaining its pinned handle."""
    from ._artifact_store import (
        _assert_no_symlink_components,
        _assert_pinned_artifact_stable,
        _open_pinned_artifact,
    )
    from ._g2_artifact_reads import _validate_inspect_record

    _validate_inspect_record(record, artifact_id)
    if (record.get("project_id") != project_id
            or record.get("host_identity") != current_host_identity
            or record.get("engine_host_identity") != current_engine_host_identity):
        raise ExecutionContractError("ARTIFACT_NOT_FOUND", "artifact_id is not registered in the current managed project")
    root = Path(project_root)
    path = root / PurePosixPath(str(record["path"]))
    _assert_no_symlink_components(path, field="registered artifact")
    handle, before = _open_pinned_artifact(path)
    try:
        before_identity = {
            "device": int(before.st_dev), "inode": int(before.st_ino),
            "size": int(before.st_size),
            "mtime_ns": int(getattr(before, "st_mtime_ns", int(before.st_mtime * 1_000_000_000))),
        }
        if (before_identity != record["file_identity"] or before_identity["size"] != record["size"]):
            raise ExecutionContractError("ARTIFACT_IDENTITY_MISMATCH", "registered artifact file identity changed")
        initial_size, initial_digest = _sha256_handle(handle)
        _assert_pinned_artifact_stable(path, before, os.fstat(handle.fileno()))
        if initial_size != before.st_size or initial_digest != artifact_id:
            raise ExecutionContractError("ARTIFACT_HASH_MISMATCH", "registered artifact bytes no longer match artifact_id")
        report = _Report(project_id=project_id, artifact_id=artifact_id)
        if before.st_size > MAX_ARCHIVE_BYTES:
            report.mark_incomplete("BUNDLE_RESOURCE_LIMIT", "registered archive exceeds the compressed-size budget")
            report.dependency_closure = "INCOMPLETE"
            report.target_format = "INCOMPLETE"
        else:
            handle.seek(0)
            prefix = handle.read(4)
            handle.seek(0)
            try:
                with zipfile.ZipFile(handle, "r") as archive:
                    infos = archive.infolist()
                    if len(infos) > MAX_OUTER_ENTRIES:
                        report.mark_incomplete("BUNDLE_RESOURCE_LIMIT", "outer archive exceeds the entry budget")
                        report.dependency_closure = "INCOMPLETE"
                        report.target_format = "INCOMPLETE"
                    else:
                        _verify_supported_bundle(report, archive, infos)
            except zipfile.BadZipFile:
                if prefix[:2] == b"PK":
                    report.member_failure("INVALID_ZIP_ARCHIVE", "registered ZIP archive is malformed")
                    report.dependency_closure = "INCOMPLETE"
                    report.target_format = "INCOMPLETE"
                else:
                    report.mark_unsupported("UNSUPPORTED_ARTIFACT_FORMAT", "registered content is not a supported offline install bundle", category="member")
                    report.dependency_closure = "INCOMPLETE"
                    report.target_format = "INCOMPLETE"
                    report.package_format = "UNKNOWN"
            except (OSError, RuntimeError, ValueError, zlib.error):
                report.member_failure("ZIP_READ_FAILURE", "registered ZIP archive could not be parsed safely")
                report.dependency_closure = "INCOMPLETE"
                report.target_format = "INCOMPLETE"
        # Keep the descriptor pinned through parsing and budget refusal. A
        # second full hash catches concurrent in-place writes before reporting.
        final_size, final_digest = _sha256_handle(handle)
        _assert_pinned_artifact_stable(path, before, os.fstat(handle.fileno()))
        if final_size != initial_size or final_digest != initial_digest or final_digest != artifact_id:
            raise ExecutionContractError("ARTIFACT_CHANGED", "registered artifact changed during verification")
        result = report.data()
        return result
    finally:
        handle.close()
