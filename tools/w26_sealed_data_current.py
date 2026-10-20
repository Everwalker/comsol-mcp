"""Versioned current-profile versioned reader for source-external install.

This keeps the historical sealed-data reader unchanged while accepting the
actual, Main-bound application candidate filename recorded in the producer's
once binding.  It is also the isolated helper entrypoint for sealed phases.
"""
from __future__ import annotations

import argparse
import contextvars
import hashlib
import importlib
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import runpy
import stat
import sys
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any


BASE_PATH = Path(__file__).with_name("w26_sealed_data_base_current.py")
BASE = runpy.run_path(str(BASE_PATH), run_name="_w26_sealed_data_current_base")
# Functions executed by runpy retain the namespace dict used during execution.
# `BASE` is the returned mapping and is a separate dict, so keep an explicit
# handle to the function namespace used by run_phase/descriptor/backup helpers.
BASE_NAMESPACE = BASE["run_phase"].__globals__
# R9 is additive. Historical R4-R8 readers and their exact loader closures remain unchanged.
R9_LOCK = Path(__file__).resolve().with_name("w26_application_sources_175_r9_prepared.lock.json")
R9_LOCK_SHA256 = "a524e533f01ed5e5ea0f06cfa5caf49ab0ddf50474258b672265c13f62f987a2"
R9_MEMBER_FINGERPRINT_SHA256 = "401d720192bba5456c8c576f9ed845a74e0555b9c8d4a5e9fa04dcb28d89ef26"
R10_PROFILE = "current175-r10"
R10_LOCK = Path(__file__).resolve().with_name("w26_application_sources_175_r10_prepared.lock.json")
R11_PROFILE = "current175-r11"
R11_LOCK = Path(__file__).resolve().with_name("w26_application_sources_175_r11_prepared.lock.json")
R11_LOCK_SHA256 = "7be4e8bf7c58611c65bbc8d6833acd0ed884de77c7620be2bd987e94b5e72abe"
R11_MEMBER_FINGERPRINT_SHA256 = "4433034853ee5b6aea878178887be328fde44c2783d06ce10ca9464945200738"
R12_PROFILE = "current175-r12"
R12_LOCK = Path(__file__).resolve().with_name("w26_application_sources_175_r12_prepared.lock.json")
R12_LOCK_SHA256 = "1f6adc82daa12c720509fbd58f86e381f834f4dd215bc5716302096f2b6c5a25"
R12_MEMBER_FINGERPRINT_SHA256 = "27da0545a17a4071ee3c21708b1f2d8f00535380b780525c31bbcedf9b799fd1"
R13_PRODUCT_PROFILE = "current175-r13"
R13_PRODUCT_LOCK = Path(__file__).resolve().with_name("w26_application_sources_175_r13_prepared.lock.json")
R13_PRODUCT_LOCK_SHA256 = "1dd9ba4db2264e44a6c778eff8d9d18d34709f7b954bff6252bce6cbede9b826"
R13_PRODUCT_MEMBER_FINGERPRINT_SHA256 = "66ba2d86fa2ff81027e0f3fc293d24ee98921fe0696acfd18fb2bce8c8426daf"
R13_DATA_APP_PROFILES = frozenset({R12_PROFILE, R13_PRODUCT_PROFILE})
R13_DATA_PROFILE = "t044-r13-actual-native-baseline"
R13_DATA_SOURCE_R14_SEAL_SHA256 = "af96e1ca2acdb8cfdbf3060d6b6d44a30d826a46ea38cef52eb8699e43d4878b"
R13_DATA_SEAL_SHA256 = "cd3b7677604a45febf1c3e5be99b8d6800cf6ebc7f0896a4008994e5a124470d"
R13_DATA_MAIN_REVIEW_SHA256 = "efcb35a3588e3f06c7dd15d547a7fb12a45f829762e412df41b93dd20937d0a0"
R13_DATA_SCOPE = "T044 R13 actual 1D fixed-slab COMSOL 6.4 macOS ARM baseline only; not W24 gel, UV/stress/material calibration, or whole-project acceptance"
R13_DATA_CLOSURE_RELATIVE = "analysis-control-store/consumer-seed/T044_R13_CONSUMER_CLOSURE_R15.json"
R13_DATA_INPUT_SPEC_SCHEMA = "w26-r13-explicit-data-input-spec/1"
R13_DATA_SELECTOR_SCHEMA = "w26-r16-explicit-data-baseline-selector/1"
R13_PREFLIGHT_PACKAGING_NAME = "packaging-26.3-py3-none-any.whl"
R13_PREFLIGHT_PACKAGING_SHA256 = "d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c"
R13_PREFLIGHT_PACKAGING_BYTES = 129956
R13_DATA_INPUT_SPEC_KEYS = frozenset({
    "schema", "profile_name", "seal", "source_seal", "payload_root", "payload_root_identity",
    "main_review", "consumer_closure_relative_path", "scope", "preflight_packaging_wheel",
})
_ACTIVE_R13_DATA_SELECTOR = contextvars.ContextVar("w26_active_r13_data_selector", default=None)
R13_DATA_SUPPLEMENTAL_RELATIVE_PATHS = frozenset({
    "T044ThermalFixture.java", "t044-coarse.mph", "t044-fine.mph",
})
R7_LOCK = Path(__file__).resolve().with_name("w26_application_sources_175_r7_final.lock.json")
R8_LOCK = Path(__file__).resolve().with_name("w26_application_sources_175_r8_prepared.lock.json")
PROFILE_MAP_PATH = Path(__file__).resolve().with_name("w26_profile_map_current.json")
PROFILE_MAP_SHA256 = "db7b12e458e9b2b966f6f17e35c75e1a1d4f05242d96a9330a3868c1dd019640"
CURRENT_TOOLS_ROOT = Path(__file__).resolve().parent
CURRENT_SCHEMA = "w26-current-versioned-loader-source-closure/1"
CURRENT_ROUTES = {
    "actor": "tools.w26_runtime_actor_current",
    "base": "tools.w26_sealed_data_base_current",
    "bundle": "tools.w26_application_bundle_current",
    "dependency_unit": "tools.w26_dependency_unit_current",
    "lifecycle": "tools.w26_application_lifecycle_current",
    "lifecycle_base": "tools.w26_application_lifecycle_base_current",
    "reader": "tools.w26_sealed_data_current",
}
LEGACY_ROLE_MODULES = frozenset({
    "tools.w26_application_bundle", "tools.w26_runtime_actor", "tools.w26_sealed_data_r9",
})
_PROFILE_MAP_RAW, PROFILE_MAP_RECORD = BASE["raw_file"](PROFILE_MAP_PATH)
if hashlib.sha256(_PROFILE_MAP_RAW).hexdigest() != PROFILE_MAP_SHA256:
    raise RuntimeError("current profile map pinned hash differs")
PROFILE_MAP = json.loads(_PROFILE_MAP_RAW)
_LEGACY_CLOSURE = PROFILE_MAP.get("legacy_profile", {}).get("loader_source_closure")
_LEGACY_NAMES = frozenset(_LEGACY_CLOSURE.get("members", {})) if isinstance(_LEGACY_CLOSURE, dict) else frozenset()
LEGACY_TOOLS_ROOT = Path(_LEGACY_CLOSURE["root"]) if isinstance(_LEGACY_CLOSURE, dict) else Path("/")
_CURRENT_NEW_NAMES = frozenset({
    "w26_application_bundle_current.py", "w26_application_lifecycle_base_current.py",
    "w26_application_lifecycle_current.py",
    "w26_dependency_unit_current.py", "w26_runtime_actor_current.py", "w26_sealed_data_base_current.py",
    "w26_sealed_data_current.py", "w26_profile_map_current.json", R10_LOCK.name, R11_LOCK.name, R12_LOCK.name,
    R13_PRODUCT_LOCK.name,
})
CURRENT_NAMES = frozenset(_LEGACY_NAMES | _CURRENT_NEW_NAMES)
_map_loader = PROFILE_MAP.get("current_loader", {})
_profile_rows = PROFILE_MAP.get("current_profiles", {})
if (PROFILE_MAP.get("legacy_profile", {}).get("source_profile") != "current175-r9"
        or PROFILE_MAP.get("legacy_profile", {}).get("profile_spec") is not None
        or set(_profile_rows) != {"current175-r9", R10_PROFILE, R11_PROFILE, R12_PROFILE, R13_PRODUCT_PROFILE}
        or _profile_rows.get("current175-r9", {}).get("source_lock_sha256") != R9_LOCK_SHA256
        or _profile_rows.get(R10_PROFILE, {}).get("source_lock_name") != R10_LOCK.name
        or _profile_rows.get(R11_PROFILE, {}).get("source_lock_name") != R11_LOCK.name
        or _profile_rows.get(R11_PROFILE, {}).get("source_lock_sha256") != R11_LOCK_SHA256
        or _profile_rows.get(R11_PROFILE, {}).get("member_fingerprint_sha256") != R11_MEMBER_FINGERPRINT_SHA256
        or _profile_rows.get(R12_PROFILE, {}).get("source_lock_name") != R12_LOCK.name
        or _profile_rows.get(R12_PROFILE, {}).get("source_lock_sha256") != R12_LOCK_SHA256
        or _profile_rows.get(R12_PROFILE, {}).get("member_fingerprint_sha256") != R12_MEMBER_FINGERPRINT_SHA256
        or _profile_rows.get(R13_PRODUCT_PROFILE, {}).get("source_lock_name") != R13_PRODUCT_LOCK.name
        or _profile_rows.get(R13_PRODUCT_PROFILE, {}).get("source_lock_sha256") != R13_PRODUCT_LOCK_SHA256
        or _profile_rows.get(R13_PRODUCT_PROFILE, {}).get("member_fingerprint_sha256") != R13_PRODUCT_MEMBER_FINGERPRINT_SHA256
        or _map_loader.get("schema") != CURRENT_SCHEMA
        or _map_loader.get("root") != "module-parent"
        or _map_loader.get("module_routes") != CURRENT_ROUTES
        or frozenset(_map_loader.get("member_names", ())) != CURRENT_NAMES
        or CURRENT_TOOLS_ROOT != CURRENT_TOOLS_ROOT.resolve(strict=True)):
    raise RuntimeError("current profile map routes or exact member set differ")
BASE.update({
    "CURRENT_APPLICATION_R9_LOCK": R9_LOCK,
    "CURRENT_APPLICATION_VERSIONED_LOCKS": {
        "current175-r9": R9_LOCK,
        R10_PROFILE: R10_LOCK,
        R11_PROFILE: R11_LOCK,
        R12_PROFILE: R12_LOCK,
        R13_PRODUCT_PROFILE: R13_PRODUCT_LOCK,
    },
    "CURRENT_APPLICATION_TOOLS_ROOT": CURRENT_TOOLS_ROOT,
    "CURRENT_APPLICATION_LOADER_SCHEMA": CURRENT_SCHEMA,
    "CURRENT_APPLICATION_LOADER_NAMES": CURRENT_NAMES,
    "CURRENT_APPLICATION_LOADER_MODULE_PATHS": {
        "tools." + Path(name).stem: str(CURRENT_TOOLS_ROOT / name)
        for name in CURRENT_NAMES if name.endswith(".py")
    },
})
BASE_NAMESPACE.update({
    key: BASE[key] for key in (
        "CURRENT_APPLICATION_R9_LOCK", "CURRENT_APPLICATION_VERSIONED_LOCKS", "CURRENT_APPLICATION_TOOLS_ROOT",
        "CURRENT_APPLICATION_LOADER_SCHEMA", "CURRENT_APPLICATION_LOADER_NAMES",
        "CURRENT_APPLICATION_LOADER_MODULE_PATHS",
    )
})
APP_CANDIDATE_RE = re.compile(r"ACTOR_CANDIDATE_[a-z0-9-]{1,48}\.json\Z")


def _get(base: Any, name: str) -> Any:
    return base[name] if isinstance(base, dict) else getattr(base, name)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _source_member_map_fingerprint(members: dict[str, Any]) -> str:
    return _sha(_canonical_bytes(dict(members)))


def _r9_current_application_loader_closure(source_records: dict[str, Any]) -> dict[str, Any]:
    """Close the loader with R9's exact reader, lock, member set, and records."""
    root = Path(BASE["CURRENT_APPLICATION_TOOLS_ROOT"])
    expected_names = BASE["CURRENT_APPLICATION_LOADER_NAMES"]
    if root != root.resolve(strict=True):
        raise RuntimeError("current application tools root is not canonical")
    members = {Path(path).name: record for path, record in source_records.items()
               if Path(path).parent == root}
    if set(members) != expected_names:
        raise RuntimeError("current application loader source closure membership differs")
    normalized = {}
    for name, record in members.items():
        path = root / name
        if record.get("path") != str(path):
            raise RuntimeError("current application loader source path differs")
        observed = BASE["raw_file"](path)[1]
        if (record.get("bytes") != observed["bytes"] or record.get("sha256") != observed["sha256"]
                or record.get("dev") != observed["dev"] or record.get("inode") != observed["inode"]
                or record.get("uid") != observed["uid"] or record.get("nlink") != observed["nlink"]
                or record.get("mode") != (observed["mode"] & 0o777)):
            raise RuntimeError("current application loader source fingerprint differs")
        normalized[name] = observed
    return {"schema": BASE["CURRENT_APPLICATION_LOADER_SCHEMA"], "root": str(root), "members": normalized}


def _r9_verify_current_application_loader_closure(closure: dict[str, Any]) -> Path:
    """Rehash the exact R9 closure before its isolated namespace import."""
    root = Path(BASE["CURRENT_APPLICATION_TOOLS_ROOT"])
    expected_names = BASE["CURRENT_APPLICATION_LOADER_NAMES"]
    if (set(closure) != {"schema", "root", "members"}
            or closure["schema"] != BASE["CURRENT_APPLICATION_LOADER_SCHEMA"]
            or closure["root"] != str(root) or root != root.resolve(strict=True)
            or set(closure["members"]) != expected_names):
        raise RuntimeError("current application loader source closure differs")
    for name in sorted(expected_names):
        path = root / name
        record = closure["members"][name]
        if record.get("path") != str(path):
            raise RuntimeError("current application loader member path differs")
        BASE["raw_file"](path, record)
    return root


def _install_current_application_tools_namespace(root: Path, closure: dict[str, Any]):
    """Load only the five versioned W26 roles through the sealed route map."""
    root = Path(root)
    if root != CURRENT_TOOLS_ROOT or root != root.resolve(strict=True):
        raise RuntimeError("current application loader root differs from the current module parent")
    existing = sys.modules.get("tools")
    if existing is not None:
        paths = {str(Path(value).resolve(strict=True)) for value in getattr(existing, "__path__", ())}
        if paths != {str(root)}:
            raise RuntimeError("preloaded tools package path differs from current profile closure")
    else:
        spec = importlib.machinery.ModuleSpec("tools", loader=None, is_package=True)
        spec.submodule_search_locations = [str(root)]
        sys.modules["tools"] = importlib.util.module_from_spec(spec)
    if LEGACY_ROLE_MODULES.intersection(sys.modules):
        raise RuntimeError("cached legacy W26 role module blocks current-profile import")
    BASE["_verify_current_application_cached_modules"](root, closure)
    before = {name for name in sys.modules if name.startswith("tools.")}
    app_module = importlib.import_module(CURRENT_ROUTES["bundle"])
    actor_module = importlib.import_module(CURRENT_ROUTES["actor"])
    importlib.import_module(CURRENT_ROUTES["dependency_unit"])
    importlib.import_module(CURRENT_ROUTES["lifecycle_base"])
    importlib.import_module(CURRENT_ROUTES["lifecycle"])
    after = {name for name in sys.modules if name.startswith("tools.")}
    if after - before - set(BASE["CURRENT_APPLICATION_LOADER_MODULE_PATHS"]):
        raise RuntimeError("current application import escaped its exact versioned loader closure")
    if LEGACY_ROLE_MODULES.intersection(sys.modules):
        raise RuntimeError("current-profile import loaded a legacy W26 role module")
    BASE["_verify_current_application_cached_modules"](root, closure)
    return app_module, actor_module


def _consume_legacy_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Delegate a no-spec profile only to its original R9 reader and root."""
    if (set(profile) != {"schema", "source_profile", "root", "result", "candidate", "once", "loader_source_closure"}
            or profile.get("schema") != "w26-current-installed-application/1"
            or profile.get("source_profile") != PROFILE_MAP["legacy_profile"]["source_profile"]):
        raise RuntimeError("legacy profile selector or tuple differs from the frozen R9 map")
    closure = profile.get("loader_source_closure")
    if closure != _LEGACY_CLOSURE:
        raise RuntimeError("legacy profile closure differs from the frozen original R9 tuple")
    legacy_root = Path(_LEGACY_CLOSURE["root"])
    if legacy_root != Path(BASE["CURRENT_APPLICATION_TOOLS_ROOT"]).resolve(strict=True):
        raise RuntimeError("legacy profile original tools root differs from the current reader module parent")
    for name, record in _LEGACY_CLOSURE["members"].items():
        BASE["raw_file"](legacy_root / name, record)
    existing = sys.modules.get("tools")
    if existing is None:
        spec = importlib.machinery.ModuleSpec("tools", loader=None, is_package=True)
        spec.submodule_search_locations = [str(legacy_root)]
        sys.modules["tools"] = importlib.util.module_from_spec(spec)
    else:
        paths = {str(Path(value).resolve(strict=True)) for value in getattr(existing, "__path__", ())}
        if paths != {str(legacy_root)}:
            raise RuntimeError("legacy profile actual consumer tools root differs from original R9 root")
    reader_path = legacy_root / "w26_sealed_data_r9.py"
    module = importlib.import_module("tools.w26_sealed_data_r9")
    if Path(module.__file__).resolve(strict=True) != reader_path:
        raise RuntimeError("legacy profile consumer is not the original R9 reader module")
    reader = getattr(module, "current_application_profile", None)
    if not callable(reader):
        raise RuntimeError("legacy R9 profile consumer is unavailable")
    return reader(profile)


def _versioned_profile_entry(source_profile: str) -> dict[str, Any]:
    row = _profile_rows.get(source_profile)
    expected = {"source_profile", "source_lock_name", "source_lock_sha256",
                "member_fingerprint_sha256", "member_count", "package_count",
                "root_count", "allowed_lock_roots"}
    if (not isinstance(row, dict) or set(row) != expected
            or row.get("source_profile") != source_profile
            or row.get("member_count") != 175 or row.get("package_count") != 172
            or row.get("root_count") != 3
            or not isinstance(row.get("source_lock_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", row["source_lock_sha256"])
            or not isinstance(row.get("member_fingerprint_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", row["member_fingerprint_sha256"])):
        raise RuntimeError("current reader source-profile selector is unknown")
    return row


def _allowed_source_lock_paths(source_profile: str) -> set[Path]:
    entry = _versioned_profile_entry(source_profile)
    name = entry.get("source_lock_name")
    if not isinstance(name, str) or Path(name).name != name:
        raise RuntimeError("current reader source-lock filename is malformed")
    roots = entry.get("allowed_lock_roots")
    if not isinstance(roots, list) or not roots or len(roots) != len(set(roots)):
        raise RuntimeError("current reader allowed source-lock roots are malformed")
    root_map = {"legacy_tools": LEGACY_TOOLS_ROOT, "current_tools": CURRENT_TOOLS_ROOT}
    if any(root not in root_map for root in roots):
        raise RuntimeError("current reader source-lock root selector is unknown")
    return {(root_map[root] / name).resolve(strict=True) for root in roots}


def _verify_profile_spec_envelope(profile: dict[str, Any], profile_spec: dict[str, Any]) -> dict[str, Any]:
    expected = {"schema", "source_profile", "profile_map_record", "selected_lock_record", "loader_source_closure"}
    if type(profile_spec) is not dict or set(profile_spec) != expected:
        raise RuntimeError("current reader profile-spec fields differ")
    source_profile = profile_spec.get("source_profile")
    if profile_spec.get("schema") != "w26-current-reader-profile-spec/1":
        raise RuntimeError("current reader profile-spec selector differs")
    entry = _versioned_profile_entry(source_profile)
    if profile_spec.get("profile_map_record") != PROFILE_MAP_RECORD:
        raise RuntimeError("current reader profile-map record differs from the pinned map")
    BASE["raw_file"](PROFILE_MAP_PATH, PROFILE_MAP_RECORD)
    lock_row = profile_spec.get("selected_lock_record")
    if not isinstance(lock_row, dict):
        raise RuntimeError("current reader selected source-lock record differs")
    lock_path = Path(lock_row.get("path", ""))
    if (not lock_path.is_absolute() or lock_path.resolve(strict=True) != lock_path
            or lock_path not in _allowed_source_lock_paths(source_profile)
            or lock_row.get("sha256") != entry.get("source_lock_sha256")):
        raise RuntimeError("current reader selected source-lock path/hash differs from its closed selector")
    from tools.w26_prepare_current_inputs import _helpers
    raw, observed_lock_row = _helpers().raw_file(lock_path, expected=lock_row)
    closure = profile_spec.get("loader_source_closure")
    if closure != profile.get("loader_source_closure"):
        raise RuntimeError("current profile and selected profile-spec loader closures differ")
    _r9_verify_current_application_loader_closure(closure)
    if closure["members"].get(PROFILE_MAP_PATH.name) != PROFILE_MAP_RECORD:
        raise RuntimeError("current profile closure omits or changes its selected profile map")
    current_lock_record = closure["members"].get(lock_path.name)
    if not isinstance(current_lock_record, dict) or current_lock_record.get("sha256") != lock_row.get("sha256") \
            or current_lock_record.get("bytes") != lock_row.get("bytes"):
        raise RuntimeError("current profile closure omits or changes its selected source-lock bytes")
    lock = json.loads(raw)
    if (lock.get("schema") != "w26-application-source-closure/1"
            or lock.get("profile") != source_profile or lock.get("member_count") != 175
            or lock.get("package_count") != 172 or lock.get("root_count") != 3
            or _source_member_map_fingerprint(lock.get("members", {})) != entry.get("member_fingerprint_sha256")):
        raise RuntimeError("current reader selected source-lock contents differ from the profile map")
    return {"lock": lock, "lock_record": observed_lock_row, "source_profile": source_profile}


def build_current_profile_spec(source_profile: str, selected_lock_record: dict[str, Any],
                               source_records: dict[str, Any]) -> dict[str, Any]:
    """Bind an explicit current selector, its new/existing lock, map, and loader."""
    entry = _versioned_profile_entry(source_profile)
    if not isinstance(selected_lock_record, dict):
        raise RuntimeError("current producer omitted its selected source-lock record")
    lock_path = Path(selected_lock_record.get("path", ""))
    if (not lock_path.is_absolute() or lock_path.resolve(strict=True) != lock_path
            or lock_path not in _allowed_source_lock_paths(source_profile)
            or selected_lock_record.get("sha256") != entry.get("source_lock_sha256")):
        raise RuntimeError("current producer selected another source-lock path/hash")
    from tools.w26_prepare_current_inputs import _helpers
    raw, lock_row = _helpers().raw_file(lock_path, expected=selected_lock_record)
    lock = json.loads(raw)
    if (lock.get("schema") != "w26-application-source-closure/1"
            or lock.get("profile") != source_profile or lock.get("member_count") != 175
            or lock.get("package_count") != 172 or lock.get("root_count") != 3
            or _source_member_map_fingerprint(lock.get("members", {})) != entry.get("member_fingerprint_sha256")):
        raise RuntimeError("current producer selected source-lock contents differ from the closed profile map")
    _, map_row = BASE["raw_file"](PROFILE_MAP_PATH, PROFILE_MAP_RECORD)
    closure = _r9_current_application_loader_closure(source_records)
    current_lock_row = closure["members"].get(lock_path.name)
    if (not isinstance(current_lock_row, dict) or current_lock_row.get("sha256") != lock_row.get("sha256")
            or current_lock_row.get("bytes") != lock_row.get("bytes")
            or closure["members"].get(PROFILE_MAP_PATH.name) != map_row):
        raise RuntimeError("current loader closure does not contain the exact map and selected lock bytes")
    return {
        "schema": "w26-current-reader-profile-spec/1",
        "source_profile": source_profile,
        "profile_map_record": map_row,
        "selected_lock_record": lock_row,
        "loader_source_closure": closure,
    }


def verify_current_profile_spec_source(source: dict[str, Any],
                                       profile_spec: dict[str, Any]) -> dict[str, Any]:
    """Validate the producer-selected profile and its closed lock/loader tuple."""
    if (not isinstance(source, dict) or not isinstance(profile_spec, dict)
            or source.get("source_profile") != profile_spec.get("source_profile")
            or source.get("source_lock") != profile_spec.get("selected_lock_record")):
        raise RuntimeError("current source input differs from its explicit profile selector")
    return _verify_profile_spec_envelope(
        {"loader_source_closure": profile_spec.get("loader_source_closure")}, profile_spec)


def build_current_profile_spec_for_app(root: Path, source_records: dict[str, Any]) -> dict[str, Any]:
    """Derive the explicit selector from the Main-bound prepared App artifact."""
    authoritative = _producer_records(Path(root))
    candidate = authoritative["candidate"][0]
    prepared_row = candidate.get("prepared")
    if not isinstance(prepared_row, dict) or Path(prepared_row.get("path", "")).parent != Path(root):
        raise RuntimeError("current profile producer prepared record is not exact-root bound")
    from tools.w26_prepare_current_inputs import _helpers
    prepared = json.loads(_helpers().raw_file(Path(prepared_row["path"]), expected=prepared_row)[0])
    source = prepared.get("source", {})
    spec = build_current_profile_spec(source.get("source_profile"), source.get("source_lock"), source_records)
    if prepared.get("current_profile_spec") is not None and prepared["current_profile_spec"] != spec:
        raise RuntimeError("current App prepared profile spec differs from the actual source producer")
    return spec


# Keep the returned mapping and the existing runpy function namespace in sync.
# This is confined to this fresh, hash-verified reader execution. In
# particular, it does not modify sys.modules or weaken the cached-module gate.
def _sync_r9_reader_namespace() -> None:
    values = {
        "CURRENT_APPLICATION_R9_LOCK": R9_LOCK,
        "CURRENT_APPLICATION_VERSIONED_LOCKS": {
            "current175-r9": R9_LOCK,
            R10_PROFILE: R10_LOCK,
        R11_PROFILE: R11_LOCK,
        R12_PROFILE: R12_LOCK,
        R13_PRODUCT_PROFILE: R13_PRODUCT_LOCK,
        },
        "CURRENT_APPLICATION_LOADER_NAMES": BASE["CURRENT_APPLICATION_LOADER_NAMES"],
        "CURRENT_APPLICATION_LOADER_MODULE_PATHS": BASE["CURRENT_APPLICATION_LOADER_MODULE_PATHS"],
        "current_application_loader_closure": _r9_current_application_loader_closure,
        "verify_current_application_loader_closure": _r9_verify_current_application_loader_closure,
        "_install_current_application_tools_namespace": _install_current_application_tools_namespace,
        "current_application_profile": lambda profile, profile_spec=None: current_application_profile(
            profile, BASE, profile_spec=profile_spec),
    }
    BASE.update(values)
    BASE_NAMESPACE.update(values)
    if any(BASE.get(name) is not value or BASE_NAMESPACE.get(name) is not value
           for name, value in values.items()):
        raise RuntimeError("current profile reader function namespace is not synchronized")


SEALED_PHASES = ("old-read", "candidate-backup", "restore", "old-restored-read")


def _r13_selector(selector: dict[str, Any] | None = None) -> dict[str, Any]:
    selected = selector if selector is not None else _ACTIVE_R13_DATA_SELECTOR.get()
    if (type(selected) is not dict or selected.get("schema") != R13_DATA_SELECTOR_SCHEMA
            or selected.get("profile_name") != R13_DATA_PROFILE):
        raise RuntimeError("T044 R13 consumer requires an explicitly selected finite data-input spec")
    return selected


def _r13_input(selector: dict[str, Any] | None = None) -> dict[str, Any]:
    selected = _r13_selector(selector)
    value = selected.get("input_spec")
    if type(value) is not dict or set(value) != R13_DATA_INPUT_SPEC_KEYS:
        raise RuntimeError("T044 R13 selected data-input spec fields differ")
    return value


def _r13_path(name: str, selector: dict[str, Any] | None = None) -> Path:
    spec = _r13_input(selector)
    aliases = {
        "seal": spec["seal"]["path"],
        "source_seal": spec["source_seal"]["path"],
        "payload_root": spec["payload_root"],
        "main_review": spec["main_review"]["path"],
        "packaging_wheel": spec["preflight_packaging_wheel"]["path"],
    }
    if name not in aliases:
        raise RuntimeError("T044 R13 data-input path name is outside the finite selector")
    return Path(aliases[name])


@contextmanager
def use_r13_data_selector(selector: dict[str, Any]):
    """Bind an already verified explicit input selector for one product call."""
    selected = _r13_selector(selector)
    token = _ACTIVE_R13_DATA_SELECTOR.set(selected)
    try:
        yield selected
    finally:
        _ACTIVE_R13_DATA_SELECTOR.reset(token)


def raw_file(path: str | Path, expected: dict[str, Any] | None = None,
             maximum: int = 268435456):
    """Expose the named sealed-file API consumed by the lifecycle actor."""
    return BASE["raw_file"](path, expected, maximum)


def signature(value: Any) -> dict[str, Any]:
    return BASE["signature"](value)


def verify_seal(path: str | Path, sha: str, *, data_selector: dict[str, Any] | None = None) -> dict[str, Any]:
    path = Path(path)
    selected = data_selector if data_selector is not None else _ACTIVE_R13_DATA_SELECTOR.get()
    if selected is not None:
        selector = _r13_selector(selected)
        if path != _r13_path("seal", selector):
            raise RuntimeError("T044 R13 seal path differs from the explicitly selected input spec")
        if sha != R13_DATA_SEAL_SHA256:
            raise RuntimeError("T044 R15 consumer seal digest is not the selected baseline")
        return verify_r13_data_baseline(selector)["consumer_closure"]
    return BASE["verify_seal"](path, sha)


def data_paths(value: dict[str, Any], *, data_selector: dict[str, Any] | None = None) -> dict[str, Path]:
    if value.get("data_profile") == R13_DATA_PROFILE:
        return _r13_transition_paths(_verify_r13_lifecycle_descriptor_value(
            value, value.get("current_profile_spec"), data_selector))
    return BASE["data_paths"](value)


def _r13_transition_paths(value: dict[str, Any]) -> dict[str, Path]:
    root = Path(value["unit_root"])
    work = root / "transition-work"
    return {"home": work / "sealed-control-home",
            "evidence": work / "transition-evidence",
            "restored_home": work / "full-restored-control-home",
            "workspace": work / "full-restored-workspaces"}


def _verify_r13_lifecycle_descriptor_value(value: dict[str, Any],
                                            profile_spec: dict[str, Any] | None,
                                            data_selector: dict[str, Any] | None = None) -> dict[str, Any]:
    """Bind the T044 selector to the normal current-application lifecycle descriptor."""
    expected_keys = {"schema", "unit_root", "root_identity", "seal", "seal_sha256",
                     "synthetic_data", "old_historical_dependencies", "current_application_profile",
                     "current_profile_spec", "data_profile", "data_selector", "data_input_spec"}
    if (type(value) is not dict or set(value) != expected_keys
            or value.get("schema") != "w26-sealed-r6-lifecycle-descriptor/1"
            or value.get("synthetic_data") is not False
            or value.get("old_historical_dependencies") != "UNVERIFIED_CURRENT43_NEW_TEST_BASELINE"
            or value.get("data_profile") != R13_DATA_PROFILE):
        raise RuntimeError("T044 R13 product lifecycle descriptor schema or profile differs")
    root = Path(value["unit_root"])
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise RuntimeError("T044 R13 product lifecycle root is unavailable") from exc
    first_raw, first_record = raw_file(root / "FIRST_RECORD.json", maximum=1048576)
    first = json.loads(first_raw)
    root_identity = {"device": root_stat.st_dev, "inode": root_stat.st_ino}
    if (not root.is_absolute() or root.parent != Path("/private/tmp")
            or root != root.resolve(strict=True) or root.is_symlink()
            or not re.fullmatch(r"cvw26-lifecycle-r15-[0-9a-f]{8}", root.name)
            or not stat.S_ISDIR(root_stat.st_mode) or root_stat.st_uid != os.getuid()
            or root_stat.st_mode & 0o077 or root_identity != value["root_identity"]
            or first.get("root_identity") != root_identity
            or first_record.get("path") != str(root / "FIRST_RECORD.json")):
        raise RuntimeError("T044 R13 product lifecycle private root identity differs")
    selector = _r13_selector(data_selector)
    if value.get("data_selector") != selector:
        raise RuntimeError("T044 R13 product lifecycle descriptor selected another data baseline")
    if (value.get("seal") != selector["seal_record"]["path"]
            or value.get("seal_sha256") != selector["seal_record"]["sha256"]):
        raise RuntimeError("T044 R13 product lifecycle descriptor seal differs from its named selector")
    if selector.get("input_spec_record") != value.get("data_input_spec"):
        raise RuntimeError("T044 R13 lifecycle descriptor data-input spec record differs")
    profile = value.get("current_application_profile")
    if (not isinstance(profile, dict) or not isinstance(profile_spec, dict)
            or value.get("current_profile_spec") != profile_spec
            or profile.get("source_profile") not in R13_DATA_APP_PROFILES
            or profile_spec.get("source_profile") not in R13_DATA_APP_PROFILES
            or profile.get("source_profile") != profile_spec.get("source_profile")):
        raise RuntimeError("T044 R13 data selector requires an explicit current175 R12 or R13 App producer")
    _verify_profile_spec_envelope({"loader_source_closure": profile_spec.get("loader_source_closure")},
                                  profile_spec)
    current_application_profile(profile, profile_spec=profile_spec)
    verify_r13_data_baseline(selector)
    return value


@contextmanager
def _r13_base_consumer_scope(profile_spec: dict[str, Any], data_selector: dict[str, Any]):
    """Bind only the seven named BASE consumer seams for one existing run_phase call."""
    _sync_r9_reader_namespace()
    if BASE_NAMESPACE is not BASE["run_phase"].__globals__:
        raise RuntimeError("BASE run_phase namespace identity differs")
    if (not isinstance(profile_spec, dict)
            or profile_spec.get("source_profile") not in R13_DATA_APP_PROFILES):
        raise RuntimeError("T044 R13 BASE consumers require a finite current175 R12 or R13 profile")
    source_profile = profile_spec["source_profile"]
    profile_entry = _versioned_profile_entry(source_profile)
    lock_record = profile_spec.get("selected_lock_record")
    if (not isinstance(lock_record, dict)
            or lock_record.get("sha256") != profile_entry.get("source_lock_sha256")):
        raise RuntimeError("T044 R13 BASE consumers require the exact selected current175 source lock")
    selector = _r13_selector(data_selector)

    def selected_descriptor(path, sha, selected_profile_spec=None):
        if selected_profile_spec not in (None, profile_spec):
            raise RuntimeError("T044 R13 BASE descriptor received another source-profile spec")
        return descriptor(path, sha, profile_spec, data_selector=selector)

    def selected_data_paths(value):
        return data_paths(value, data_selector=selector)

    def selected_verify_seal(path, sha):
        return verify_seal(path, sha, data_selector=selector)

    def selected_migration_bundle(archive, sidecar, closure, sealpath, destination, *,
                                  producer_archive_binding=None):
        return migration_bundle(archive, sidecar, closure, sealpath, destination,
                                producer_archive_binding=producer_archive_binding,
                                data_selector=selector)

    def selected_verify_migration(path, closure):
        return verify_migration(path, closure, data_selector=selector)

    def selected_supplemental_copy(closure, project_map, workspace):
        return supplemental_copy(closure, project_map, workspace, data_selector=selector)

    def selected_verify_restored(home, workspace, closure, *, restore_binding=None):
        return verify_restored(home, workspace, closure, restore_binding=restore_binding,
                               data_selector=selector)

    wrappers = {
        "descriptor": selected_descriptor,
        "data_paths": selected_data_paths,
        "verify_seal": selected_verify_seal,
        "migration_bundle": selected_migration_bundle,
        "verify_migration": selected_verify_migration,
        "supplemental_copy": selected_supplemental_copy,
        "verify_restored": selected_verify_restored,
    }
    originals = {}
    for name, wrapper in wrappers.items():
        current = BASE_NAMESPACE.get(name)
        if current is not BASE.get(name):
            raise RuntimeError("BASE named lifecycle seam is not its frozen reader function: " + name)
        originals[name] = current
    try:
        BASE_NAMESPACE.update(wrappers)
        yield
    finally:
        drifted = [name for name, wrapper in wrappers.items() if BASE_NAMESPACE.get(name) is not wrapper]
        BASE_NAMESPACE.update(originals)
        if drifted:
            raise RuntimeError("BASE named lifecycle seam changed before exact restoration: " + ",".join(drifted))


def migration_bundle(archive: str | Path, sidecar: str | Path, closure: dict[str, Any],
                     sealpath: str | Path, destination: str | Path, *,
                     producer_archive_binding: Any = None,
                     data_selector: dict[str, Any] | None = None) -> dict[str, Any]:
    selected = data_selector if data_selector is not None else _ACTIVE_R13_DATA_SELECTOR.get()
    if selected is None or Path(sealpath) != _r13_path("seal", selected):
        return BASE["migration_bundle"](archive, sidecar, closure, sealpath, destination,
                                         producer_archive_binding=producer_archive_binding)
    selector = _r13_selector(selected)
    if (closure.get("source_r14_seal_sha256") != R13_DATA_SOURCE_R14_SEAL_SHA256
            or closure.get("source_main_review_sha256") != R13_DATA_MAIN_REVIEW_SHA256
            or producer_archive_binding is None):
        raise RuntimeError("T044 R13 migration bundle requires the sealed source closure and actual backup observer")
    archive_bytes = BASE["_actual_backup_archive_raw"](archive, producer_archive_binding)
    closure_record = closure.get("consumer_closure_record")
    if not isinstance(closure_record, dict):
        raise RuntimeError("T044 R13 consumer closure record is missing")
    components = {
        "registered-full-state.tar": archive_bytes,
        "registered-full-state.tar.manifest.json": raw_file(sidecar)[0],
        "INPUT_CLOSURE.json": raw_file(Path(closure_record["path"]), closure_record)[0],
        "SEAL_FINAL.json": raw_file(_r13_path("seal", selector), {"sha256": R13_DATA_SEAL_SHA256})[0],
    }
    for row in closure["project_files"]:
        if row["registered_blob"] is False:
            name = "supplemental/" + row["relative_path"]
            if name in components:
                raise RuntimeError("T044 R13 migration supplemental member is duplicated")
            components[name] = raw_file(Path(row["copy"]["path"]), row["copy"])[0]
    expected_supplemental = {"supplemental/" + name for name in R13_DATA_SUPPLEMENTAL_RELATIVE_PATHS}
    if set(components) != {"registered-full-state.tar", "registered-full-state.tar.manifest.json",
                           "INPUT_CLOSURE.json", "SEAL_FINAL.json"} | expected_supplemental:
        raise RuntimeError("T044 R13 migration component closure differs from the six sealed project files")
    inventory = {name: {"bytes": len(blob), "sha256": _sha(blob)}
                 for name, blob in sorted(components.items())}
    metadata = {"schema": "w26-single-closed-migration-components/1", "registered_count": 3,
                "supplemental_count": 3, "total_project_files": 6, "components": inventory,
                "production_backup_format": "UNCHANGED",
                "history": closure["durable_history_sha256_same_algorithm_as_production"]}
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, blob in sorted(components.items()):
            bundle.writestr(name, blob)
        bundle.writestr("MIGRATION_MANIFEST.json", _canonical_bytes(metadata))
    return raw_file(destination)[1]


def verify_migration(path: str | Path, closure: dict[str, Any], *,
                     data_selector: dict[str, Any] | None = None):
    selected = data_selector if data_selector is not None else _ACTIVE_R13_DATA_SELECTOR.get()
    if selected is None:
        return BASE["verify_migration"](path, closure)
    selector = _r13_selector(selected)
    if closure.get("source_r14_seal_sha256") != R13_DATA_SOURCE_R14_SEAL_SHA256:
        return BASE["verify_migration"](path, closure)
    raw, record = raw_file(path)
    with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
        names = bundle.namelist()
        if bundle.testzip() is not None or len(names) != len(set(names)):
            raise RuntimeError("T044 R13 migration ZIP integrity or uniqueness differs")
        metadata = json.loads(bundle.read("MIGRATION_MANIFEST.json"))
        expected_components = {"registered-full-state.tar", "registered-full-state.tar.manifest.json",
                               "INPUT_CLOSURE.json", "SEAL_FINAL.json"}
        expected_components.update("supplemental/" + name for name in R13_DATA_SUPPLEMENTAL_RELATIVE_PATHS)
        if (metadata.get("schema") != "w26-single-closed-migration-components/1"
                or metadata.get("registered_count") != 3 or metadata.get("supplemental_count") != 3
                or metadata.get("total_project_files") != 6
                or metadata.get("history") != closure["durable_history_sha256_same_algorithm_as_production"]
                or set(metadata.get("components", {})) != expected_components
                or set(names) != expected_components | {"MIGRATION_MANIFEST.json"}):
            raise RuntimeError("T044 R13 migration manifest/member set differs")
        for name, expected in metadata["components"].items():
            blob = bundle.read(name)
            if len(blob) != expected.get("bytes") or _sha(blob) != expected.get("sha256"):
                raise RuntimeError("T044 R13 migration component bytes differ: " + name)
        for row in closure["project_files"]:
            if row["registered_blob"] is False:
                name = "supplemental/" + row["relative_path"]
                blob = bundle.read(name)
                if blob != raw_file(Path(row["copy"]["path"]), row["copy"])[0]:
                    raise RuntimeError("T044 R13 migration supplemental source differs: " + name)
        if (bundle.read("SEAL_FINAL.json") != raw_file(_r13_path("seal", selector), {"sha256": R13_DATA_SEAL_SHA256})[0]
                or json.loads(bundle.read("INPUT_CLOSURE.json")) != closure["consumer_closure"]):
            raise RuntimeError("T044 R13 migration closure or new baseline seal differs")
        parts = {"archive": bundle.read("registered-full-state.tar"),
                 "manifest": bundle.read("registered-full-state.tar.manifest.json")}
    return metadata, parts, record


def supplemental_copy(closure: dict[str, Any], project_map: dict[str, str],
                      workspace: str | Path, *,
                      data_selector: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    selected = data_selector if data_selector is not None else _ACTIVE_R13_DATA_SELECTOR.get()
    if selected is None:
        return BASE["supplemental_copy"](closure, project_map, workspace)
    _r13_selector(selected)
    if closure.get("source_r14_seal_sha256") != R13_DATA_SOURCE_R14_SEAL_SHA256:
        return BASE["supplemental_copy"](closure, project_map, workspace)
    workspace = Path(workspace)
    if (set(project_map) != {row["project_id"] for row in closure["projects"]}
            or workspace != workspace.resolve(strict=True) or workspace.is_symlink()
            or workspace.stat().st_uid != os.getuid() or workspace.stat().st_mode & 0o077):
        raise RuntimeError("T044 R13 restore workspace or project map is not the exact owned tree")
    copied = []
    for row in closure["project_files"]:
        if row["registered_blob"] is True:
            continue
        relative = Path(row["relative_path"])
        if (relative.is_absolute() or ".." in relative.parts
                or relative.as_posix() not in R13_DATA_SUPPLEMENTAL_RELATIVE_PATHS):
            raise RuntimeError("T044 R13 supplemental relative path is outside the exact three-file set")
        project = Path(project_map[row["project_id"]])
        first_stat = project.lstat()
        if (project.is_symlink() or project != project.resolve(strict=True)
                or not project.is_relative_to(workspace) or first_stat.st_uid != os.getuid()
                or first_stat.st_mode & 0o077):
            raise RuntimeError("T044 R13 restored project mapping owner/mode/path differs")
        source_bytes, source_record = raw_file(Path(row["copy"]["path"]), row["copy"])
        if (len(source_bytes) != row["bytes"] or source_record["sha256"] != row["sha256"]):
            raise RuntimeError("T044 R13 supplemental source differs from the sealed project closure")
        parent_fd = os.open(project, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            pinned = os.fstat(parent_fd)
            if signature(pinned) != signature(first_stat):
                raise RuntimeError("T044 R13 restore project changed before no-clobber supplemental copy")
            fd = os.open(relative.as_posix(), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=parent_fd)
            try:
                view = memoryview(source_bytes)
                while view:
                    view = view[os.write(fd, view):]
                os.fsync(fd)
            finally:
                os.close(fd)
            identity = lambda item: (item.st_dev, item.st_ino, item.st_uid, item.st_mode)
            if identity(pinned) != identity(os.fstat(parent_fd)) or identity(pinned) != identity(project.lstat()):
                raise RuntimeError("T044 R13 restore project identity changed during supplemental copy")
            copied.append(raw_file(project / relative)[1])
        finally:
            os.close(parent_fd)
    if len(copied) != 3:
        raise RuntimeError("T044 R13 requires exactly three sealed supplemental project files")
    return copied


def verify_restored(home: str | Path, workspace: str | Path, closure: dict[str, Any], *,
                    restore_binding: dict[str, Any] | None = None,
                    data_selector: dict[str, Any] | None = None) -> dict[str, Any]:
    selected = data_selector if data_selector is not None else _ACTIVE_R13_DATA_SELECTOR.get()
    if selected is None:
        return BASE["verify_restored"](home, workspace, closure, restore_binding=restore_binding)
    _r13_selector(selected)
    if closure.get("source_r14_seal_sha256") != R13_DATA_SOURCE_R14_SEAL_SHA256:
        return BASE["verify_restored"](home, workspace, closure, restore_binding=restore_binding)
    result = BASE["verify_restored"](home, workspace, closure, restore_binding=restore_binding)
    if (result.get("success") is not True or result.get("schema_table_count") != 14
            or result.get("table_counts") != closure.get("table_counts")
            or result.get("durable_history_sha256") != closure.get("durable_history_sha256_same_algorithm_as_production")
            or len(result.get("registered_readbacks", [])) != 3):
        raise RuntimeError("T044 R13 existing full-restore readback lacks the exact six-file/three-artifact closure")
    return {**result, "registered_files": 3, "supplemental_files": 3, "total_files": 6}


def backup_producer_sha(value: dict[str, Any],
                        profile_spec: dict[str, Any] | None = None) -> str:
    return BASE["backup_producer_sha"](value, profile_spec)


def r13_data_profile_selector(input_spec_path: str | Path,
                              input_spec_sha256: str) -> dict[str, Any]:
    """Load the finite T044 baseline only from the caller's exact path/hash spec."""
    spec_path = Path(input_spec_path)
    if (not spec_path.is_absolute() or spec_path.is_symlink()
            or spec_path.resolve(strict=True) != spec_path):
        raise RuntimeError("T044 R13 explicit data-input spec path is not canonical")
    spec_raw, spec_observation = raw_file(spec_path, maximum=65536)
    if spec_observation.get("sha256") != input_spec_sha256:
        raise RuntimeError("exact T044 R13 data-input spec SHA-256 differs")
    # Lifecycle artifacts use the frozen regular-file helper's stable record
    # shape (permission bits only, without timestamps); bind those exact bytes
    # using the reader's same-FD observation before publishing that record.
    spec_record = {key: spec_observation[key] for key in
                   ("path", "bytes", "sha256", "dev", "inode", "uid", "nlink")}
    spec_record["mode"] = spec_observation["mode"] & 0o777
    spec = json.loads(spec_raw)
    if (type(spec) is not dict or set(spec) != R13_DATA_INPUT_SPEC_KEYS
            or spec.get("schema") != R13_DATA_INPUT_SPEC_SCHEMA
            or spec.get("profile_name") != R13_DATA_PROFILE
            or spec.get("scope") != R13_DATA_SCOPE
            or spec.get("consumer_closure_relative_path") != R13_DATA_CLOSURE_RELATIVE):
        raise RuntimeError("T044 R13 explicit data-input spec schema/profile/scope differs")
    seal_spec, source_spec = spec.get("seal"), spec.get("source_seal")
    review_spec, wheel_spec = spec.get("main_review"), spec.get("preflight_packaging_wheel")
    if (type(seal_spec) is not dict or set(seal_spec) != {"path", "sha256"}
            or type(source_spec) is not dict or set(source_spec) != {"path", "sha256"}
            or type(review_spec) is not dict or set(review_spec) != {"path", "sha256"}
            or type(wheel_spec) is not dict or set(wheel_spec) != {"path", "bytes", "sha256"}
            or seal_spec.get("sha256") != R13_DATA_SEAL_SHA256
            or source_spec.get("sha256") != R13_DATA_SOURCE_R14_SEAL_SHA256
            or review_spec.get("sha256") != R13_DATA_MAIN_REVIEW_SHA256
            or Path(wheel_spec.get("path", "")).name != R13_PREFLIGHT_PACKAGING_NAME
            or wheel_spec.get("bytes") != R13_PREFLIGHT_PACKAGING_BYTES
            or wheel_spec.get("sha256") != R13_PREFLIGHT_PACKAGING_SHA256):
        raise RuntimeError("T044 R13 explicit data-input spec is outside the finite reviewed lineage")
    for row in (seal_spec, source_spec, review_spec, wheel_spec):
        path = Path(row.get("path", ""))
        if not path.is_absolute() or path.is_symlink() or path.resolve(strict=True) != path:
            raise RuntimeError("T044 R13 input spec contains a missing, linked, or noncanonical path")
    payload_root = Path(spec.get("payload_root", ""))
    if (not payload_root.is_absolute() or payload_root.is_symlink()
            or payload_root.resolve(strict=True) != payload_root
            or type(spec.get("payload_root_identity")) is not dict):
        raise RuntimeError("T044 R13 input spec payload root is missing or noncanonical")
    seal_raw, seal_record = raw_file(Path(seal_spec["path"]), maximum=4194304)
    source_raw, source_record = raw_file(Path(source_spec["path"]), maximum=4194304)
    wheel_record = raw_file(Path(wheel_spec["path"]), maximum=4194304)[1]
    seal = json.loads(seal_raw)
    source_seal = json.loads(source_raw)
    root_identity = spec["payload_root_identity"]
    if (seal_record.get("sha256") != R13_DATA_SEAL_SHA256
            or source_record.get("sha256") != R13_DATA_SOURCE_R14_SEAL_SHA256
            or wheel_record.get("bytes") != wheel_spec["bytes"]
            or wheel_record.get("sha256") != wheel_spec["sha256"]
            or seal.get("schema") != "w26-r15-t044-lifecycle-consumer-baseline-seal/1"
            or seal.get("persistent_input_root") != str(payload_root)
            or seal.get("root_identity") != root_identity
            or seal.get("source_main_review", {}).get("sha256") != R13_DATA_MAIN_REVIEW_SHA256
            or seal.get("source_main_review", {}).get("path") != review_spec["path"]
            or seal.get("status") != "PERSISTENT_ACTUAL_T044_INPUT_CONSUMER_SEALED_NOT_W26_FULLRESTORE_ACCEPTANCE"
            or seal.get("source_r14_seal") != source_spec
            or seal.get("project_file_count") != 6 or seal.get("registered_artifact_count") != 3
            or len(seal.get("files", [])) != 51
            or source_seal.get("schema") != "w26-r13-t044-actual-lifecycle-input-baseline-seal/1"
            or source_seal.get("source_main_review", {}).get("sha256") != R13_DATA_MAIN_REVIEW_SHA256
            or source_seal.get("raw_original_store", {}).get("sqlite_opened") is not False):
        raise RuntimeError("T044 R13 persistent seal, payload identity, or source lineage differs")
    root_stat = payload_root.lstat()
    if BASE["signature"](root_stat) != root_identity:
        raise RuntimeError("T044 R13 payload root identity differs from the exact seal")
    main_review = raw_file(Path(review_spec["path"]), maximum=4194304)[1]
    if main_review.get("sha256") != R13_DATA_MAIN_REVIEW_SHA256:
        raise RuntimeError("T044 R13 Main review path does not contain its frozen bytes")
    return {
        "schema": R13_DATA_SELECTOR_SCHEMA,
        "profile_name": R13_DATA_PROFILE,
        "input_spec_record": spec_record,
        "input_spec_sha256": input_spec_sha256,
        "input_spec": spec,
        "seal_record": seal_record,
        "source_seal_record": source_record,
        "payload_root": str(payload_root),
        "payload_root_identity": root_identity,
        "source_main_review_sha256": R13_DATA_MAIN_REVIEW_SHA256,
        "source_r14_seal_sha256": R13_DATA_SOURCE_R14_SEAL_SHA256,
        "scope": R13_DATA_SCOPE,
    }


def _r13_validate_tree_identity(root: Path, seal: dict[str, Any],
                                selector: dict[str, Any]) -> dict[str, Any]:
    """Hash and identity-check the exact persistent tree without opening SQLite."""
    if root != _r13_path("payload_root", selector) or root.is_symlink() or root.resolve(strict=True) != root:
        raise RuntimeError("T044 R13 payload root is not the explicit persistent selector")
    if BASE["signature"](root.lstat()) != seal.get("root_identity"):
        raise RuntimeError("T044 R13 payload root identity differs from its new seal")
    if sorted(path.name for path in root.iterdir()) != seal.get("root_exact_entry_names"):
        raise RuntimeError("T044 R13 payload top-level membership differs")
    expected_dirs = {"."}
    expected_files: set[str] = set()
    for row in seal.get("directories", []):
        rel = row.get("relative_path")
        if (not isinstance(rel, str) or not rel or Path(rel).is_absolute()
                or ".." in Path(rel).parts):
            raise RuntimeError("T044 R13 directory selector is malformed")
        path = root if rel == "." else root / rel
        if path.is_symlink() or not path.is_relative_to(root) or not path.is_dir():
            raise RuntimeError("T044 R13 directory member is linked or outside its root")
        if BASE["signature"](path.lstat()) != row.get("identity"):
            raise RuntimeError("T044 R13 sealed directory identity differs")
        expected_dirs.add(rel)
    for row in seal.get("files", []):
        rel = row.get("relative_path")
        if (not isinstance(rel, str) or not rel or Path(rel).is_absolute()
                or ".." in Path(rel).parts or rel in expected_files):
            raise RuntimeError("T044 R13 file selector is malformed or duplicated")
        path = root / rel
        if path.is_symlink() or not path.is_relative_to(root):
            raise RuntimeError("T044 R13 file member is linked or outside its root")
        raw, record = raw_file(path, maximum=268435456)
        if (len(raw) != row.get("bytes") or record.get("sha256") != row.get("sha256")
                or BASE["signature"](path.lstat()) != row.get("identity")):
            raise RuntimeError("T044 R13 sealed file bytes or identity differ: " + rel)
        expected_files.add(rel)
    observed_dirs: set[str] = set()
    observed_files: set[str] = set()
    for directory, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current = Path(directory)
        observed_dirs.add("." if current == root else current.relative_to(root).as_posix())
        for name in dirnames:
            path = current / name
            if path.is_symlink() or not path.is_dir():
                raise RuntimeError("T044 R13 payload contains a linked/non-directory member")
        for name in filenames:
            path = current / name
            if path.is_symlink() or not path.is_file():
                raise RuntimeError("T044 R13 payload contains a linked/non-file member")
            observed_files.add(path.relative_to(root).as_posix())
    if observed_dirs != expected_dirs or observed_files != expected_files:
        raise RuntimeError("T044 R13 complete persistent tree membership differs")
    return {"files_verified": len(expected_files), "directories_verified": len(expected_dirs),
            "raw_sqlite_originals_opened": False, "sqlite_opened": False}


def verify_r13_data_baseline(selector: dict[str, Any] | None = None) -> dict[str, Any]:
    """Verify the R15 consumer baseline and return its sealed production closure.

    Only the byte-preserved stable analysis copy is opened with SQLite in
    immutable read-only mode. The original DB/WAL/SHM payload remains opaque.
    """
    if selector is None:
        selector = _ACTIVE_R13_DATA_SELECTOR.get()
    selector = _r13_selector(selector)
    spec_record = selector.get("input_spec_record")
    if not isinstance(spec_record, dict):
        raise RuntimeError("T044 R13 selected baseline omits its exact input-spec record")
    expected = r13_data_profile_selector(spec_record.get("path"), selector.get("input_spec_sha256"))
    if selector != expected:
        raise RuntimeError("T044 R13 baseline requires its exact named persistent selector")
    seal_path = _r13_path("seal", selector)
    source_path = _r13_path("source_seal", selector)
    payload_root = _r13_path("payload_root", selector)
    raw, seal_record = raw_file(seal_path, expected=selector["seal_record"], maximum=4194304)
    seal = json.loads(raw)
    if (seal.get("schema") != "w26-r15-t044-lifecycle-consumer-baseline-seal/1"
            or seal.get("status") != "PERSISTENT_ACTUAL_T044_INPUT_CONSUMER_SEALED_NOT_W26_FULLRESTORE_ACCEPTANCE"
            or seal.get("scope") != expected["scope"]
            or seal.get("persistent_input_root") != expected["payload_root"]
            or seal.get("source_main_review", {}).get("sha256") != R13_DATA_MAIN_REVIEW_SHA256
            or seal.get("raw_original_store", {}).get("sqlite_opened") is not False
            or seal.get("project_file_count") != 6 or seal.get("registered_artifact_count") != 3
            or len(seal.get("files", [])) != 51
            or seal.get("source_r14_seal") != {"path": str(source_path),
                                                "sha256": R13_DATA_SOURCE_R14_SEAL_SHA256}):
        raise RuntimeError("T044 R13 sealed baseline counts, scope, or provenance differ")
    tree = _r13_validate_tree_identity(payload_root, seal, selector)
    source_raw, source_seal_record = raw_file(source_path, expected=selector["source_seal_record"], maximum=4194304)
    source_seal = json.loads(source_raw)
    if (source_seal_record["sha256"] != R13_DATA_SOURCE_R14_SEAL_SHA256
            or source_seal.get("schema") != "w26-r13-t044-actual-lifecycle-input-baseline-seal/1"
            or source_seal.get("source_main_review", {}).get("sha256") != R13_DATA_MAIN_REVIEW_SHA256
            or source_seal.get("raw_original_store", {}).get("sqlite_opened") is not False
            or source_seal.get("project_file_count") != 6
            or source_seal.get("registered_artifact_count") != 3):
        raise RuntimeError("T044 R14 source input seal or lineage differs")
    review_row = next((row for row in seal["files"] if row.get("relative_path") == "MAIN_ACTUAL_NATIVE_REVIEW_R1.json"), None)
    if not isinstance(review_row, dict):
        raise RuntimeError("T044 R13 Main actual review is not a sealed input")
    review_raw, review_record = raw_file(payload_root / review_row["relative_path"], maximum=4194304)
    review = json.loads(review_raw)
    if (review_record["sha256"] != R13_DATA_MAIN_REVIEW_SHA256
            or review.get("schema") != "T044_R13_MAIN_ACTUAL_NATIVE_REVIEW_V1"
            or review.get("status") != "PASS_SCOPED_T044_FIXED_1D_20_40_MESH_ANALYTIC_AND_PUBLIC_LIFECYCLE"
            or review.get("source_SQLite_opened") is not False
            or review.get("final_independent_review") != "NOT_RUN"):
        raise RuntimeError("T044 R13 source Main review bytes or scoped status differ")

    source_analysis = seal.get("source_analysis_database")
    source_analysis_old = source_seal.get("analysis_database", {}).get("analysis_copy_record", {})
    if (not isinstance(source_analysis, dict)
            or source_analysis.get("bytes") != source_analysis_old.get("bytes")
            or source_analysis.get("sha256") != source_analysis_old.get("sha256")
            or source_analysis.get("sha256") != "cb947d1bffc09297e63fd8c308f925cc1805f3f7a8839ae29d40066df2c3a61d"):
        raise RuntimeError("T044 byte-exact stable source analysis-copy lineage differs")
    consumer_record = seal.get("consumer_seed_database")
    if (not isinstance(consumer_record, dict)
            or consumer_record.get("path") != str(payload_root / "analysis-control-store/consumer-seed/operations.sqlite3")
            or seal.get("source_analysis_table_row_digests") != source_seal.get("analysis_database", {}).get("table_row_digests")):
        raise RuntimeError("T044 consumer DB seed path or source table-hash lineage differs")

    closure_record = seal.get("consumer_closure_record")
    if (not isinstance(closure_record, dict)
            or closure_record.get("path") != str(payload_root / R13_DATA_CLOSURE_RELATIVE)):
        raise RuntimeError("T044 consumer closure record path differs")
    closure_raw, observed_closure_record = raw_file(Path(closure_record["path"]),
                                                    expected=closure_record, maximum=4194304)
    consumer = json.loads(closure_raw)
    if (consumer.get("schema") != "w26-r15-t044-lifecycle-consumer-closure/1"
            or consumer.get("source_r14_seal_sha256") != R13_DATA_SOURCE_R14_SEAL_SHA256
            or consumer.get("main_review_sha256") != R13_DATA_MAIN_REVIEW_SHA256
            or consumer.get("analysis_database") != consumer_record
            or consumer.get("changed_tables_due_only_to_persistent_path_identity_rebind") != ["artifacts", "projects"]
            or consumer.get("synthetic_rows_created") != 0
            or consumer.get("operation_replay_performed") is not False
            or consumer.get("worker_or_model_bindings_revived") is not False):
        raise RuntimeError("T044 R15 consumer closure lineage or no-replay boundary differs")
    counts = consumer.get("table_counts", {})
    if (counts.get("operations") != 29 or counts.get("jobs") != 29
            or counts.get("projects") != 1 or counts.get("artifacts") != 3
            or len(counts) != 14
            or consumer.get("durable_history_sha256_same_algorithm_as_production")
            != source_seal.get("analysis_database", {}).get("durable_history_sha256")):
        raise RuntimeError("T044 consumer database counts or durable history differ from actual source")
    if (consumer.get("schema_columns") != source_seal.get("analysis_database", {}).get("schema_columns")
            or consumer.get("registered_blob_count") != 3
            or len(consumer.get("project_files", [])) != 6
            or len(consumer.get("projects", [])) != 1
            or len(consumer.get("artifacts", [])) != 3):
        raise RuntimeError("T044 consumer closure project, artifact, or schema cardinality differs")

    source_row = next((row for row in seal["files"]
                       if row.get("relative_path") == "analysis-control-store/operations.sqlite3"), None)
    if not isinstance(source_row, dict) or source_row.get("sha256") != source_analysis.get("sha256"):
        raise RuntimeError("T044 source analysis-copy file is absent from the new sealed tree")
    seed_row = next((row for row in seal["files"]
                     if row.get("relative_path") == "analysis-control-store/consumer-seed/operations.sqlite3"), None)
    if (not isinstance(seed_row, dict) or seed_row.get("sha256") != consumer_record.get("sha256")
            or seed_row.get("bytes") != consumer_record.get("bytes")):
        raise RuntimeError("T044 consumer seed DB is absent from the sealed file inventory")
    stable_observation = BASE["database_observation"](Path(consumer_record["path"]))
    for actual, expected_value in (
        (stable_observation["schema_columns"], consumer["schema_columns"]),
        (stable_observation["table_counts"], consumer["table_counts"]),
        (stable_observation["table_row_digests"], consumer["table_row_digests"]),
        (stable_observation["durable_history_sha256"], consumer["durable_history_sha256_same_algorithm_as_production"]),
    ):
        if actual != expected_value:
            raise RuntimeError("T044 immutable consumer analysis-copy DB differs from sealed row closure")
    if stable_observation["first_database_record"]["sha256"] != consumer_record["sha256"]:
        raise RuntimeError("T044 immutable consumer DB bytes differ from its new seal")

    files = {}
    project_root = Path(consumer.get("project_root", ""))
    if (project_root != payload_root / "preserved-runtime/workspaces/native-smoke"
            or consumer.get("project_root_identity") != hashlib.sha256(
                os.path.normcase(os.path.normpath(str(project_root))).encode("utf-8")).hexdigest()):
        raise RuntimeError("T044 consumer project rehome identity differs")
    for row in consumer["project_files"]:
        rel = row.get("relative_path")
        if (row.get("project_id") != consumer.get("project_id") or not isinstance(rel, str)
                or Path(rel).is_absolute() or ".." in Path(rel).parts or rel in files):
            raise RuntimeError("T044 consumer project closure has an invalid path or owner")
        sealed_file = next((item for item in seal["files"] if item.get("relative_path") ==
                            "preserved-runtime/workspaces/native-smoke/" + rel), None)
        copy_record = row.get("copy")
        if (not isinstance(sealed_file, dict) or not isinstance(copy_record, dict)
                or sealed_file.get("bytes") != row.get("bytes")
                or sealed_file.get("sha256") != row.get("sha256")
                or copy_record.get("path") != str(project_root / rel)
                or copy_record.get("sha256") != row.get("sha256")
                or copy_record.get("bytes") != row.get("bytes")):
            raise RuntimeError("T044 consumer project file is not bound to the six-byte closure")
        files[rel] = row
    registered_paths = {row["relative_path"] for row in files.values() if row.get("registered_blob") is True}
    supplemental_paths = {row["relative_path"] for row in files.values() if row.get("registered_blob") is False}
    if (len(registered_paths) != 3 or supplemental_paths != R13_DATA_SUPPLEMENTAL_RELATIVE_PATHS
            or set(files) != registered_paths | supplemental_paths):
        raise RuntimeError("T044 consumer artifact/supplemental file partition differs")
    if (consumer.get("table_row_digests") != seal.get("consumer_table_row_digests")
            or consumer.get("durable_history_sha256_same_algorithm_as_production")
            != seal.get("consumer_durable_history_sha256")):
        raise RuntimeError("T044 consumer table row digests differ between closure and seal")

    prior = seal.get("prior_release_wheel", {})
    prior_relative = "prior-release-wheel/comsol_mcp-0.1.9-py3-none-any.whl"
    prior_row = next((row for row in seal["files"]
                      if row.get("relative_path") == prior_relative), None)
    if (not isinstance(prior_row, dict)
            or prior_row.get("sha256") != "c1d82034e19e1f0fd897f3ec76911a3ccad229ed35f613148633bfb56f923ae6"
            or prior_row.get("bytes") != 615450
            or prior.get("path") != str(payload_root / prior_relative)
            or prior.get("sha256") != prior_row.get("sha256")
            or prior.get("bytes") != prior_row.get("bytes")
            or prior.get("source_classification") != "genuine persistent W22 prior release; exact old lifecycle SHA"):
        raise RuntimeError("T044 R13 baseline genuine prior wheel identity differs")
    raw_originals = seal.get("raw_original_store", {}).get("files", [])
    expected_raw = source_seal.get("raw_original_store", {}).get("files", [])
    if len(raw_originals) != 3 or [row.get("sha256") for row in raw_originals] != \
            [row.get("sha256") for row in expected_raw]:
        raise RuntimeError("T044 raw original DB/WAL/SHM byte lineage differs")
    closure_for_base = {
        "schema_columns": consumer["schema_columns"],
        "table_counts": consumer["table_counts"],
        "table_row_digests": consumer["table_row_digests"],
        "durable_history_sha256_same_algorithm_as_production": consumer["durable_history_sha256_same_algorithm_as_production"],
        "projects": consumer["projects"],
        "artifacts": consumer["artifacts"],
        "project_files": [{"project_id": row["project_id"], "relative_path": row["relative_path"],
                            "registered_blob": row["registered_blob"], "copy": row["copy"],
                            "bytes": row["bytes"], "sha256": row["sha256"]}
                           for row in consumer["project_files"]],
        "registered_blob_count": consumer["registered_blob_count"],
        "whole_database": {"copy": consumer_record},
        "genuine_prior_wheel": {"copy": prior,
                                 "source_classification": prior["source_classification"]},
        "consumer_closure_record": observed_closure_record,
        "consumer_closure": consumer,
        "source_r14_seal_sha256": R13_DATA_SOURCE_R14_SEAL_SHA256,
        "source_main_review_sha256": R13_DATA_MAIN_REVIEW_SHA256,
    }
    return {"status": "PASS_T044_R15_PERSISTENT_CONSUMER_BASELINE_NOT_FULLRESTORE",
            "profile_name": R13_DATA_PROFILE, "seal": seal_record,
            "source_main_review_sha256": R13_DATA_MAIN_REVIEW_SHA256,
            "source_r14_seal_sha256": R13_DATA_SOURCE_R14_SEAL_SHA256,
            "input_tree": tree, "consumer_analysis_database_opened_immutable_readonly": True,
            "consumer_closure": closure_for_base,
            "raw_original_store_opened": False, "fullrestore": "NOT_RUN"}


def _archive_fd_record(fd: int, path: str | Path, links: int):
    return BASE["_archive_fd_record"](fd, path, links)


def _restored_record(path: str | Path, binding: dict[str, Any]):
    return BASE["_restored_record"](path, binding)


def _close_archive_observation(observation: Any, original_failure: BaseException | None = None):
    return BASE["_close_archive_observation"](observation, original_failure)


def _validate_descriptor_profile_selector(
        value: dict[str, Any], profile_spec: dict[str, Any] | None) -> None:
    """Bind the selected lifecycle profile before the base descriptor reader runs."""
    profile = value.get("current_application_profile")
    embedded_spec = value.get("current_profile_spec")
    if profile is None:
        if embedded_spec is not None or profile_spec is not None:
            raise RuntimeError("current profile spec supplied without a current application profile")
        return
    if not isinstance(profile, dict) or profile.get("schema") != "w26-current-installed-application/1":
        raise RuntimeError("sealed descriptor current application profile is malformed")
    if profile_spec is None:
        if (embedded_spec is not None
                or profile.get("source_profile") != "current175-r9"
                or set(profile) != {"schema", "source_profile", "root", "result", "candidate", "once",
                                    "loader_source_closure"}
                or profile.get("loader_source_closure") != _LEGACY_CLOSURE):
            raise RuntimeError("no-spec sealed descriptor is restricted to the frozen R9 tuple")
        return
    expected_spec_keys = {"schema", "source_profile", "profile_map_record", "selected_lock_record",
                          "loader_source_closure"}
    if (type(profile_spec) is not dict or set(profile_spec) != expected_spec_keys
            or profile_spec.get("schema") != "w26-current-reader-profile-spec/1"
            or profile_spec.get("source_profile") not in {R11_PROFILE, *R13_DATA_APP_PROFILES}
            or profile.get("source_profile") != profile_spec.get("source_profile")
            or embedded_spec != profile_spec
            or profile_spec.get("profile_map_record") != PROFILE_MAP_RECORD
            or profile_spec.get("loader_source_closure") != profile.get("loader_source_closure")):
        raise RuntimeError("sealed descriptor does not match the explicitly selected R11 profile spec")
    lock_record = profile_spec.get("selected_lock_record")
    selected_profile = profile_spec["source_profile"]
    selected_lock = {R11_PROFILE: R11_LOCK, R12_PROFILE: R12_LOCK,
                     R13_PRODUCT_PROFILE: R13_PRODUCT_LOCK}[selected_profile]
    selected_sha = {R11_PROFILE: R11_LOCK_SHA256, R12_PROFILE: R12_LOCK_SHA256,
                    R13_PRODUCT_PROFILE: R13_PRODUCT_LOCK_SHA256}[selected_profile]
    if (not isinstance(lock_record, dict)
            or Path(str(lock_record.get("path", ""))).name != selected_lock.name
            or lock_record.get("sha256") != selected_sha):
        raise RuntimeError("sealed descriptor selected lock is not the pinned finite current source lock")


def _verify_r13_preflight_descriptor(value: dict[str, Any],
                                     profile_spec: dict[str, Any] | None,
                                     data_selector: dict[str, Any] | None) -> dict[str, Any]:
    """Validate the bounded data-only descriptor; it cannot authorize restore effects."""
    expected_keys = {"schema", "unit_root", "root_identity", "data_profile", "data_selector",
                     "profile_spec_record", "application_producer", "effect_policy", "synthetic_data"}
    if (type(value) is not dict or set(value) != expected_keys
            or value.get("schema") != "w26-r13-data-preflight-descriptor/1"
            or value.get("data_profile") != R13_DATA_PROFILE
            or value.get("application_producer") != "NOT_BOUND_NO_MAIN_REVIEW_NO_ONCE"
            or value.get("effect_policy") != "INPUT_PREFLIGHT_ONLY_NO_RESTORE"
            or value.get("synthetic_data") is not False):
        raise RuntimeError("T044 R13 preflight descriptor schema or effect policy differs")
    root = Path(value["unit_root"])
    root_stat = root.lstat()
    root_identity = {"device": root_stat.st_dev, "inode": root_stat.st_ino}
    if (not root.is_absolute() or root.parent != Path("/private/tmp")
            or root != root.resolve(strict=True)
            or not re.fullmatch(r"cvw26-r13-preflight-[0-9a-f]{8}", root.name)
            or root.is_symlink() or not stat.S_ISDIR(root_stat.st_mode)
            or root_stat.st_uid != os.getuid() or root_stat.st_mode & 0o077
            or root_identity != value["root_identity"]):
        raise RuntimeError("T044 R13 preflight namespace/root identity refused")
    first_raw, first_record = raw_file(root / "FIRST_RECORD.json", maximum=1048576)
    first = json.loads(first_raw)
    if (first_record.get("path") != str(root / "FIRST_RECORD.json")
            or first.get("schema") != "w26-r13-inert-preflight-root/1"
            or first.get("root_identity") != value["root_identity"]):
        raise RuntimeError("T044 R13 preflight root first record differs")
    selector = value.get("data_selector")
    selected_data = _r13_selector(data_selector)
    if selector != selected_data:
        raise RuntimeError("T044 R13 preflight descriptor selected another persistent baseline")
    spec_record = value.get("profile_spec_record")
    if (not isinstance(profile_spec, dict)
            or profile_spec.get("source_profile") not in {R11_PROFILE, *R13_DATA_APP_PROFILES}
            or not isinstance(spec_record, dict)
            or Path(spec_record.get("path", "")).parent != root):
        raise RuntimeError("T044 R13 preflight descriptor is not bound to a finite explicit R11/R12/R13 profile")
    from tools.w26_prepare_current_inputs import _helpers
    spec_raw, spec_observed = _helpers().raw_file(Path(spec_record["path"]), expected=spec_record,
                                                   maximum=4194304)
    if json.loads(spec_raw) != profile_spec or spec_observed != spec_record:
        raise RuntimeError("T044 R13 preflight profile-spec sidecar differs from its descriptor record")
    _verify_profile_spec_envelope({"loader_source_closure": profile_spec.get("loader_source_closure")}, profile_spec)
    return verify_r13_data_baseline(selected_data)


def preflight_sealed_phase(descriptor_path: str | Path, descriptor_sha256: str, phase: str,
                           profile_spec: dict[str, Any] | None,
                           data_selector: dict[str, Any] | None) -> dict[str, Any]:
    """Run actual product descriptor, baseline DB, and profile checks without phase effects."""
    if type(phase) is not str or phase not in SEALED_PHASES:
        raise RuntimeError("T044 R13 preflight phase is outside the fixed four sealed phases")
    raw, record = raw_file(descriptor_path, maximum=4194304)
    if record.get("sha256") != descriptor_sha256:
        raise RuntimeError("exact T044 R13 product lifecycle descriptor digest differs")
    value = json.loads(raw)
    selected, closure = descriptor(descriptor_path, descriptor_sha256, profile_spec,
                                   data_selector=data_selector)
    if selected.get("data_profile") != R13_DATA_PROFILE:
        raise RuntimeError("T044 R13 phase preflight requires the product data selector")
    paths = data_paths(selected)
    baseline = verify_r13_data_baseline(selected["data_selector"])
    database = BASE["verify_database"](Path(closure["whole_database"]["copy"]["path"]),
                                       closure, restored=False)
    profile = current_application_profile(selected["current_application_profile"],
                                          profile_spec=profile_spec)
    producer_sha = backup_producer_sha(selected, profile_spec)
    if producer_sha != profile["backup_source_sha256"]:
        raise RuntimeError("T044 R13 selected backup producer differs from the App source closure")
    for name, path in paths.items():
        if not path.is_relative_to(Path(selected["unit_root"]) / "transition-work"):
            raise RuntimeError("T044 R13 product phase output path escaped the lifecycle work root")
    prerequisites = {
        "old-read": "SEALED_SEED_AND_PRIVATE_TARGET_VERIFIED",
        "candidate-backup": "SEED_AND_BACKUP_PRODUCER_VERIFIED; OLD_READ_EFFECT_NOT_RUN",
        "restore": "SEED_AND_RESTORE_CONSUMER_VERIFIED; BACKUP_EFFECT_NOT_RUN",
        "old-restored-read": "SEED_AND_OLD_READER_VERIFIED; RESTORE_EFFECT_NOT_RUN",
    }
    return {"status": "PASS_T044_R13_PRODUCT_PHASE_INPUT_PREFLIGHT_ONLY",
            "phase": phase, "descriptor": record, "profile_name": R13_DATA_PROFILE,
             "source_profile": profile_spec["source_profile"], "baseline_seal_sha256": R13_DATA_SEAL_SHA256,
            "verified_input_files": baseline["input_tree"]["files_verified"],
            "verified_table_count": len(database["table_counts"]),
            "verified_operations": database["table_counts"].get("operations"),
            "verified_jobs": database["table_counts"].get("jobs"),
            "backup_producer_sha256": producer_sha, "phase_prerequisites": prerequisites[phase],
            "raw_sqlite_originals_opened": False, "analysis_database_opened_immutable_readonly": True,
            "restore_effects": "NOT_RUN", "backup_effects": "NOT_RUN",
            "phase_effects": "NOT_RUN", "subprocesses": 0}


def descriptor(path: str | Path, sha: str,
               profile_spec: dict[str, Any] | None = None, *,
               data_selector: dict[str, Any] | None = None):
    raw, observed = raw_file(path)
    if observed.get("sha256") != sha:
        raise RuntimeError("exact sealed descriptor changed")
    value = json.loads(raw)
    if value.get("schema") == "w26-r13-data-preflight-descriptor/1":
        return value, _verify_r13_preflight_descriptor(value, profile_spec, data_selector)
    if value.get("data_profile") == R13_DATA_PROFILE:
        if not Path(path).is_relative_to(Path(value.get("unit_root", "/"))):
            raise RuntimeError("T044 R13 lifecycle descriptor leaves its exact private root")
        checked = _verify_r13_lifecycle_descriptor_value(value, profile_spec, data_selector)
        return checked, verify_r13_data_baseline(checked["data_selector"])["consumer_closure"]
    _validate_descriptor_profile_selector(value, profile_spec)
    _sync_r9_reader_namespace()
    return BASE["descriptor"](path, sha, profile_spec)


def _producer_records(root: Path, base: Any = BASE) -> dict[str, Any]:
    """Bind the producer's candidate path through its exact once/review chain."""
    root = Path(root)
    from tools.w26_prepare_current_inputs import _helpers
    helper = _helpers()
    if root.parent != Path("/private/tmp") or root != root.resolve() or \
            not re.fullmatch(r"cvw26-application-[0-9a-f]{8}", root.name):
        raise RuntimeError("current profile application producer root refused")
    first = json.loads(helper.raw_file(root / "FIRST_RECORD.json")[0])
    helper.secure_dir(root, first["root_identity"])

    result_row = helper.raw_file(root / "APPLICATION_BUNDLE_RESULT.json")[1]
    once_row = helper.raw_file(root / "UNIT_ONCE_GUARD.json")[1]
    once = json.loads(helper.raw_file(root / "UNIT_ONCE_GUARD.json", expected=once_row)[0])
    binding_row = once.get("binding_first_record")
    if not isinstance(binding_row, dict) or binding_row.get("path") != str(root / "MAIN_ONCE_BINDING.json"):
        raise RuntimeError("current profile App once does not bind its original Main once file")
    binding_raw = helper.raw_file(root / "MAIN_ONCE_BINDING.json", expected=binding_row)[0]
    binding = json.loads(binding_raw)
    candidate_name = binding.get("candidate_filename")
    if not isinstance(candidate_name, str) or not APP_CANDIDATE_RE.fullmatch(candidate_name):
        raise RuntimeError("current profile App candidate filename is not Main-bound in the allowed namespace")
    candidate_row = helper.raw_file(root / candidate_name)[1]
    candidate = json.loads(helper.raw_file(root / candidate_name, expected=candidate_row)[0])
    review_row = helper.raw_file(root / "MAIN_SOURCE_REVIEW.json")[1]
    review = json.loads(helper.raw_file(root / "MAIN_SOURCE_REVIEW.json", expected=review_row)[0])

    if once.get("activity_kind") != "CURRENT_APPLICATION_BUNDLE" or \
            once.get("nonce") != binding.get("nonce") or \
            once.get("binding_first_record") != binding_row:
        raise RuntimeError("current profile App once/binding activity or nonce differs")
    if binding.get("status") != "MAIN_APPROVED_ONCE_W26_APPLICATION_BUNDLE" or \
            binding.get("root") != str(root) or \
            binding.get("candidate_filename") != candidate_name or \
            binding.get("candidate_sha256") != candidate_row["sha256"] or \
            binding.get("root_identity") != first["root_identity"] or \
            binding.get("review_sha256") != review_row["sha256"]:
        raise RuntimeError("current profile App exact once/candidate/root/review binding differs")
    if review.get("status") != "PASS_W26_APPLICATION_BUNDLE_SOURCE_FOR_EXACT_CANDIDATE" or \
            review.get("candidate_sha256") != candidate_row["sha256"]:
        raise RuntimeError("current profile App exact-candidate Main source review is missing")
    if candidate.get("status") != "READY_APPLICATION_BUNDLE_MAIN_REVIEW_NOT_EXECUTION_APPROVED" or \
            candidate.get("root_identity") != first["root_identity"] or candidate.get("native_Popen") != 0:
        raise RuntimeError("current profile App candidate root/prepare-only state differs")
    return {
        "root_identity": first["root_identity"],
        "result": (json.loads(helper.raw_file(root / "APPLICATION_BUNDLE_RESULT.json", expected=result_row)[0]), result_row),
        "candidate": (candidate, candidate_row),
        "once": (once, once_row),
        "binding": (binding, binding_row),
        "review": (review, review_row),
    }


def build_current_application_profile(root: Path, loader_sources: dict[str, Any], base: Any = BASE,
                                      *, profile_spec: dict[str, Any] | None = None) -> dict[str, Any]:
    """Construct a current profile only with its explicit selected-profile tuple."""
    if profile_spec is None:
        raise RuntimeError("current profile build requires an explicit profile spec")
    records = _producer_records(Path(root), base)
    source = records["candidate"][0].get("prepared", {})
    if not isinstance(source, dict):
        raise RuntimeError("current profile candidate prepared record is malformed")
    from tools.w26_prepare_current_inputs import _helpers
    prepared = json.loads(_helpers().raw_file(Path(source["path"]), expected=source)[0])
    selected = prepared.get("source", {})
    if selected.get("source_profile") != profile_spec.get("source_profile") or \
            selected.get("source_lock") != profile_spec.get("selected_lock_record"):
        raise RuntimeError("current App candidate source selection differs from its explicit profile spec")
    if prepared.get("current_profile_spec") is not None and prepared["current_profile_spec"] != profile_spec:
        raise RuntimeError("current App prepared source profile spec differs from its producer")
    loader = _get(base, "current_application_loader_closure")(loader_sources)
    if loader != profile_spec.get("loader_source_closure"):
        raise RuntimeError("current App candidate loader source closure differs from its explicit profile spec")
    return {
        "schema": "w26-current-installed-application/1",
        "source_profile": profile_spec["source_profile"],
        "root": str(Path(root)),
        "result": records["result"][1],
        "candidate": records["candidate"][1],
        "once": records["once"][1],
        "loader_source_closure": loader,
    }


def current_application_profile(profile: dict[str, Any], base: Any = BASE,
                                *, profile_spec: dict[str, Any] | None = None) -> dict[str, Any]:
    """Consume an exact legacy tuple or explicitly selected versioned current tuple."""
    if profile_spec is None:
        return _consume_legacy_profile(profile)
    spec_info = _verify_profile_spec_envelope(profile, profile_spec)
    if set(profile) != {"schema", "source_profile", "root", "result", "candidate", "once", "loader_source_closure"}:
        raise RuntimeError("closed current profile installed application profile fields differ")
    if profile.get("schema") != "w26-current-installed-application/1" or \
            profile.get("source_profile") != profile_spec.get("source_profile"):
        raise RuntimeError("current profile installed application profile selector differs")

    tools_root = _get(base, "verify_current_application_loader_closure")(profile["loader_source_closure"])
    app_module, actor_module = _get(base, "_install_current_application_tools_namespace")(
        tools_root, profile["loader_source_closure"])
    from tools.w26_prepare_current_inputs import _helpers
    helper = _helpers()

    root = Path(profile["root"])
    authoritative = _producer_records(root, base)
    result, result_row = authoritative["result"]
    candidate, candidate_row = authoritative["candidate"]
    once, once_row = authoritative["once"]
    if profile["result"] != result_row or profile["candidate"] != candidate_row or profile["once"] != once_row:
        raise RuntimeError("current profile profile producer records do not match the authoritative App artifacts")
    if profile["result"]["path"] != str(root / "APPLICATION_BUNDLE_RESULT.json") or \
            profile["once"]["path"] != str(root / "UNIT_ONCE_GUARD.json") or \
            profile["candidate"]["path"] != str(root / authoritative["binding"][0]["candidate_filename"]):
        raise RuntimeError("current profile profile App producer paths differ from exact result/once binding")

    if result.get("status") != "PASS_CURRENT_APPLICATION_BUNDLE_SCOPED_NOT_RELEASE" or \
            result.get("version") != "0.1.10" or result.get("target") != "macos_arm64" or \
            result.get("build", {}).get("source_file_count") != 175 or \
            result.get("COMSOL_JVM_GUI") != "NOT_RUN":
        raise RuntimeError("current profile application producer is not an exact completed175 App build")

    candidate_prepared = candidate.get("prepared")
    if not isinstance(candidate_prepared, dict) or Path(candidate_prepared.get("path", "")).parent != root:
        raise RuntimeError("current profile App prepared record leaves exact producer root")
    prepared_match = re.fullmatch(r"APP_INPUT_BINDING_([a-z0-9-]{1,48})\.json", Path(candidate_prepared["path"]).name)
    plan_record = candidate.get("plan")
    if (prepared_match is None or not isinstance(plan_record, dict)
            or Path(plan_record.get("path", "")).parent != root):
        raise RuntimeError("current profile App prepared/plan record namespace differs")
    plan_match = re.fullmatch(r"CURRENT_BUILD_PLAN_([a-z0-9-]{1,48})\.json", Path(plan_record["path"]).name)
    if plan_match is None or plan_match.group(1) != prepared_match.group(1):
        raise RuntimeError("current profile App plan/prepared tags differ")
    plan = json.loads(helper.raw_file(Path(plan_record["path"]), expected=plan_record)[0])
    prepared = json.loads(helper.raw_file(Path(candidate_prepared["path"]), expected=candidate_prepared)[0])
    expected_commands = app_module.commands(root, prepared["builder"]["path"])
    if (plan.get("activity_kind") != "CURRENT_APPLICATION_BUNDLE" or plan.get("version") != "0.1.10"
            or plan.get("target") != "macos_arm64" or plan.get("commands") != expected_commands
            or plan.get("delivery") != prepared.get("delivery")):
        raise RuntimeError("current profile App build plan differs from reviewed producer/source profile")
    context_record = plan.get("context")
    if (not isinstance(context_record, dict) or Path(context_record.get("path", "")).parent != root
            or Path(context_record["path"]).name != "APP_CONTEXT_" + prepared_match.group(1) + ".json"):
        raise RuntimeError("current profile App context record namespace differs")
    context = json.loads(helper.raw_file(Path(context_record["path"]), expected=context_record)[0])
    if (context.get("activity_kind") != "CURRENT_APPLICATION_BUNDLE" or context.get("unit_root") != str(root)
            or context.get("root_identity") != candidate.get("root_identity")
            or context.get("outside") != str(root / "outside") or context.get("commands") != expected_commands
            or context.get("legacy_sources") != prepared.get("legacy_sources")):
        raise RuntimeError("current profile App build context differs from its source-bound plan")
    source = prepared.get("source", {})
    snapshot = Path(source.get("snapshot_source", ""))
    if source.get("source_profile") != profile_spec.get("source_profile") or snapshot != root / "source-input":
        raise RuntimeError("current profile App source profile/snapshot path differs")
    lock_path = Path(profile_spec["selected_lock_record"]["path"])
    lock_path = lock_path.resolve(strict=True)
    lock_row = source.get("source_lock", {})
    if lock_row.get("path") != str(lock_path) or lock_row.get("sha256") != _versioned_profile_entry(
            profile_spec["source_profile"]).get("source_lock_sha256"):
        raise RuntimeError("current profile App exact reviewed source lock binding differs")
    lock = spec_info["lock"]
    if lock_row != profile_spec["selected_lock_record"]:
        raise RuntimeError("current profile App source lock differs from selected profile spec")
    if lock.get("schema") != "w26-application-source-closure/1" or \
            lock.get("profile") != profile_spec["source_profile"] or lock.get("member_count") != 175 or \
            lock.get("package_count") != 172 or lock.get("root_count") != 3:
        raise RuntimeError("current profile App source lock schema/member counts differ")
    fingerprint = _source_member_map_fingerprint(lock["members"])
    if fingerprint != _versioned_profile_entry(profile_spec["source_profile"]).get("member_fingerprint_sha256"):
        raise RuntimeError("current profile App source member fingerprint differs from selected generation")
    snapshot_summary = app_module.verify_source_input(
        source, require_live_origin=False, profile_spec=profile_spec)
    origin = source["origin_provenance_snapshot"]
    if not isinstance(snapshot_summary, dict) or snapshot_summary != {
            "source_snapshot_member_count": 175,
            "source_snapshot_fingerprint_sha256": fingerprint,
            "origin_snapshot_member_count": 175,
            "origin_snapshot_fingerprint_sha256": fingerprint,
    }:
        raise RuntimeError("current profile frozen source snapshot readback differs")

    extras = prepared.get("extras")
    copied_extras = prepared.get("copied_extras")
    if (not isinstance(extras, dict) or set(extras) != {"uv_lock", "operations"}
            or not isinstance(copied_extras, dict) or set(copied_extras) != set(extras)):
        raise RuntimeError("current profile frozen extra-input closure differs")
    delivery = prepared.get("delivery")
    for name, copied_record in copied_extras.items():
        basename = "uv.lock" if name == "uv_lock" and delivery is not None else "INPUT_" + name
        path = root / basename
        if copied_record.get("path") != str(path):
            raise RuntimeError("current profile frozen extra input path differs")
        helper.raw_file(path, expected=copied_record)
        if {key: copied_record.get(key) for key in ("bytes", "sha256")} != {
                key: extras[name].get(key) for key in ("bytes", "sha256")}:
            raise RuntimeError("current profile frozen extra input differs from its captured provenance")

    commands = result.get("commands", [])
    if len(commands) != 2:
        raise RuntimeError("current profile App requires exactly two terminal original captures")
    for row, label in zip(commands, ("builder-check", "application-build")):
        if row.get("status") != "PASS" or row.get("rc") != 0 or \
                row.get("owned_group_absent") is not True or row.get("owned_process_terminal") is not True or \
                row.get("birth", {}).get("status") != "OBSERVED" or row.get("birth", {}).get("pid") != row.get("pid") or \
                not str(row.get("result_name", "")).startswith(label + "-") or \
                Path(str(row.get("result_name", ""))).name != row.get("result_name"):
            raise RuntimeError("current profile App original capture terminal proof differs")
        terminal_path = root / "logs" / row["result_name"]
        terminal = json.loads(helper.raw_file(terminal_path)[0])
        if terminal != {key: value for key, value in row.items() if key != "result_name"}:
            raise RuntimeError("current profile App original terminal capture record differs")
        execution = json.loads(helper.raw_file(root / "evidence" / ("EXEC_" + label + ".json"))[0])
        probe_record = execution["network_probe_first_record"]
        if execution.get("same_original_pid") != row["pid"] or execution.get("inherited_group") != row["pid"] or \
                execution.get("extra_processes") != 0 or probe_record.get("path") != str(root / "evidence" / ("PROBE_" + label + ".json")):
            raise RuntimeError("current profile App original PID/network evidence binding differs")
        probe = json.loads(helper.raw_file(Path(probe_record["path"]), expected=probe_record)[0])
        if probe.get("status") != "PASS_ACTUAL_EPERM" or probe.get("errno") not in (1, 13):
            raise RuntimeError("current profile App original OS network denial is unproven")

    wheel = root / "appwheel" / "comsol_mcp-0.1.10-py3-none-any.whl"
    wheel_raw, wheel_record = helper.raw_file(wheel)
    build = result["build"]
    if wheel_record.get("sha256") != build.get("sha256") or wheel_record.get("bytes") != build.get("bytes") or \
            wheel_record not in result.get("outputs", []):
        raise RuntimeError("current profile App wheel identity differs from actual build result")
    from tools.w26_target_inputs import wheel_record as verify_wheel
    metadata = verify_wheel(wheel_raw, wheel.name, "macos_arm64")
    if metadata.get("name") != "comsol-mcp" or metadata.get("version") != "0.1.10":
        raise RuntimeError("current profile App wheel metadata differs")
    with zipfile.ZipFile(io.BytesIO(wheel_raw)) as archive:
        members = app_module.source_members(snapshot)
        package = {name for name in archive.namelist() if name.startswith("comsol_mcp/") and not name.endswith("/")}
        if package != {rel for rel in members if rel.startswith("comsol_mcp/")} or len(package) != 172:
            raise RuntimeError("current profile App wheel exact172 payload differs")
        for rel in package:
            if archive.read(rel) != helper.raw_file(members[rel], expected=source["snapshot_members"][rel])[0]:
                raise RuntimeError("current profile App wheel/source bytes differ")
    source_provenance = {
        "schema": "w26-source-provenance-snapshot-binding/1",
        "source_profile": profile_spec["source_profile"],
        "source_lock_path": str(lock_path),
        "source_lock_sha256": lock_row["sha256"],
        "member_count": 175,
        "package_count": 172,
        "root_count": 3,
        "member_fingerprint_sha256": fingerprint,
        "source_snapshot_path": str(snapshot),
        "source_snapshot_member_count": snapshot_summary["source_snapshot_member_count"],
        "source_snapshot_fingerprint_sha256": snapshot_summary["source_snapshot_fingerprint_sha256"],
        "origin_snapshot_path": origin["snapshot_source"],
        "origin_snapshot_member_count": snapshot_summary["origin_snapshot_member_count"],
        "origin_snapshot_fingerprint_sha256": snapshot_summary["origin_snapshot_fingerprint_sha256"],
        "source_snapshot_record_path": candidate_prepared["path"],
        "source_snapshot_record_sha256": candidate_prepared["sha256"],
    }
    return {"wheel": wheel_record, "result": result_row,
            "candidate": candidate_row, "once": once_row,
            "review": authoritative["review"][1],
            "backup_source_sha256": source["snapshot_members"]["comsol_mcp/_state_backup.py"]["sha256"],
            "source_provenance": source_provenance}


_sync_r9_reader_namespace()


def run_phase(descriptor: str, sha: str, phase: str,
              profile_spec: dict[str, Any] | None = None, *,
              input_preflight_only: bool = False,
              data_input_spec_path: str | Path | None = None,
              data_input_spec_sha256: str | None = None,
              preflight_packaging_wheel: str | Path | None = None,
              preflight_packaging_wheel_sha256: str | None = None) -> dict[str, Any] | None:
    """Run one sealed phase through its exact legacy or selected current tuple."""
    if phase not in SEALED_PHASES:
        raise RuntimeError("unknown sealed actual data phase")
    if bool(data_input_spec_path) != bool(data_input_spec_sha256):
        raise RuntimeError("data-input-spec path and digest must be supplied together")
    selector = (r13_data_profile_selector(data_input_spec_path, data_input_spec_sha256)
                if data_input_spec_path else None)
    if bool(preflight_packaging_wheel) != bool(preflight_packaging_wheel_sha256):
        raise RuntimeError("preflight packaging wheel path and digest must be supplied together")
    if input_preflight_only:
        if selector is None:
            raise RuntimeError("T044 R13 phase input preflight requires its explicit data-input spec")
        wheel = _r13_input(selector)["preflight_packaging_wheel"]
        if (Path(preflight_packaging_wheel or "") != Path(wheel["path"])
                or preflight_packaging_wheel_sha256 != wheel["sha256"]):
            raise RuntimeError("exact packaging preflight wheel differs from the selected data-input spec")
        with use_r13_data_selector(selector):
            return preflight_sealed_phase(descriptor, sha, phase, profile_spec, selector)
    # Read and bind the descriptor selector at this public helper boundary. The
    # base reader repeats its own seal and profile verification before effects.
    raw, observed = raw_file(descriptor)
    if observed.get("sha256") != sha:
        raise RuntimeError("exact sealed descriptor changed")
    value = json.loads(raw)
    if value.get("data_profile") == R13_DATA_PROFILE:
        if selector is None:
            raise RuntimeError("T044 R13 effectful product phase requires its explicit data-input spec")
        with use_r13_data_selector(selector):
            checked = _verify_r13_lifecycle_descriptor_value(value, profile_spec, selector)
            if value.get("data_selector") != selector:
                raise RuntimeError("T044 R13 phase descriptor differs from the exact caller data-input spec")
            if not Path(descriptor).is_relative_to(Path(checked["unit_root"])):
                raise RuntimeError("T044 R13 phase descriptor leaves its selected lifecycle root")
            _sync_r9_reader_namespace()
            with _r13_base_consumer_scope(profile_spec, selector):
                BASE["run_phase"](descriptor, sha, phase, profile_spec)
        return None
    _validate_descriptor_profile_selector(value, profile_spec)
    _sync_r9_reader_namespace()
    BASE["run_phase"](descriptor, sha, phase, profile_spec)
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--descriptor", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--profile-spec")
    parser.add_argument("--profile-spec-sha")
    parser.add_argument("--data-input-spec")
    parser.add_argument("--data-input-spec-sha")
    parser.add_argument("--preflight-packaging-wheel")
    parser.add_argument("--preflight-packaging-wheel-sha")
    parser.add_argument("--input-preflight-only", action="store_true")
    parser.add_argument("--phase", choices=("old-read", "candidate-backup", "restore", "old-restored-read"), required=True)
    args = parser.parse_args(argv)
    if bool(args.profile_spec) != bool(args.profile_spec_sha):
        raise RuntimeError("profile-spec path and digest must be provided together")
    if bool(args.data_input_spec) != bool(args.data_input_spec_sha):
        raise RuntimeError("data-input-spec path and digest must be provided together")
    profile_spec = None
    if args.profile_spec:
        raw, record = BASE["raw_file"](Path(args.profile_spec))
        if record["sha256"] != args.profile_spec_sha:
            raise RuntimeError("exact profile-spec sidecar changed")
        profile_spec = json.loads(raw)
    result = run_phase(args.descriptor, args.sha, args.phase, profile_spec,
                       input_preflight_only=args.input_preflight_only,
                       data_input_spec_path=args.data_input_spec,
                       data_input_spec_sha256=args.data_input_spec_sha,
                       preflight_packaging_wheel=args.preflight_packaging_wheel,
                       preflight_packaging_wheel_sha256=args.preflight_packaging_wheel_sha)
    if result is not None:
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
