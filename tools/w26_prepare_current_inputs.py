"""Prepare the exact historical 43 wheel bytes in a new private namespace.

No wheel imports, installations, target Python or native launches. Old inode,
once and PID observations are never reused. Existing strict R1 file helpers are
read-only reused; its historical controller is never imported or executed.
"""
from __future__ import annotations
import argparse
import ast
from email import policy
from email.parser import BytesParser
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import time
import tomllib
import urllib.request
import zipfile

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name, parse_wheel_filename

REPO = Path(__file__).resolve().parents[1]
PACKET = REPO / "continuation/packets/w26-offline-input-closure-r1"
CLOSURE_SHA = "df76796141d043c4fd3c37c6a9b53819596bf4fbf57a64b635ca4588227505a6"
COMMON = REPO / "continuation/packets/w26-offline-venv-unit-r1/frozen-work/common.py"
COMMON_SHA = "94a5b742551ee9ef21224ee1abd3d415f73a1fff1447559d600242f6a7bf0c8f"
_COMMON_MODULE = None
LOCK_SHAS = {"backend": "6f8b652ba4e1d8a0e040b2352e211f2d226acc9336a58c6229c874085643548a",
             "runtime": "8e959dfb421c3c22352e768f7a63d12e3459aac93d8e6479e8eb5c5a01603b42"}

def lock_bytes(label):
    raw = (PACKET / ("requirements-" + label + ".lock")).read_bytes()
    if hashlib.sha256(raw).hexdigest() != LOCK_SHAS[label]:
        raise RuntimeError("original fixed lock bytes changed")
    return raw

def _helpers():
    global _COMMON_MODULE
    if hashlib.sha256(COMMON.read_bytes()).hexdigest() != COMMON_SHA:
        raise RuntimeError("frozen strict helper source changed")
    if _COMMON_MODULE is None:
        spec = importlib.util.spec_from_file_location("w26_frozen_regular_helpers", COMMON)
        _COMMON_MODULE = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_COMMON_MODULE)
    return _COMMON_MODULE

def load_closure():
    raw = (PACKET / "ACTUAL43_METADATA_CLOSURE.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != CLOSURE_SHA:
        raise RuntimeError("actual43 source metadata changed")
    value = json.loads(raw)
    rows = value["actual_inputs"]
    if len(rows) != 43 or len({r["name"] for r in rows}) != 43:
        raise RuntimeError("actual43 names/cardinality changed")
    return value

def validate_wheel(raw, row):
    expected = row["actual_record"]
    if len(raw) != expected["bytes"] or hashlib.sha256(raw).hexdigest() != expected["sha256"]:
        raise RuntimeError("wheel pinned bytes/hash mismatch")
    name, version, _build, tags = parse_wheel_filename(row["filename"])
    if name != canonicalize_name(row["name"]) or str(version) != row["version"]:
        raise RuntimeError("wheel filename identity mismatch")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        names = [x.filename for x in infos]
        if len(names) != len(set(names)) or any(n.startswith("/") or "\\" in n or
                any(c in ("", ".", "..") for c in n.rstrip("/").split("/")) for n in names):
            raise RuntimeError("unsafe wheel member paths")
        if sum(x.file_size for x in infos) > 536870912 or any(x.file_size > 134217728 or
                (x.external_attr >> 16) & 0o170000 == 0o120000 for x in infos):
            raise RuntimeError("wheel expanded/symlink envelope refused")
        paths = {}
        parsed = {}
        for label in ("METADATA", "WHEEL"):
            members = [n for n in names if n.count("/") == 1 and n.endswith(".dist-info/" + label)]
            if len(members) != 1:
                raise RuntimeError("wheel metadata identity is not unique")
            paths[label] = members[0].rsplit("/", 1)[0]
            data = archive.read(members[0])
            if hashlib.sha256(data).hexdigest() != row[label + "_sha256"]:
                raise RuntimeError("internal metadata hash mismatch")
            parsed[label] = BytesParser(policy=policy.default).parsebytes(data)
        metadata, wheel = parsed["METADATA"], parsed["WHEEL"]
        if paths["METADATA"] != paths["WHEEL"] or metadata.defects or wheel.defects or \
                len(metadata.get_all("Name", [])) != 1 or len(metadata.get_all("Version", [])) != 1:
            raise RuntimeError("malformed internal metadata")
        if canonicalize_name(metadata["Name"]) != name or metadata["Version"] != row["version"] or \
                metadata.get("Requires-Python", "") != row["Requires-Python"] or \
                "3.12.13" not in SpecifierSet(row["Requires-Python"]) or \
                metadata.get_all("Requires-Dist", []) != row["Requires-Dist"] or \
                metadata.get_all("Provides-Extra", []) != row["Provides-Extra"] or \
                set(wheel.get_all("Tag", [])) != {str(t) for t in tags} or \
                sorted(str(t) for t in tags) != row["filename_tags"] or \
                wheel.get_all("Tag", []) != row["internal_tags"]:
            raise RuntimeError("internal metadata/tag/dependency identity mismatch")
    return {"name": row["name"], "version": row["version"], "METADATA_sha256": row["METADATA_sha256"],
            "WHEEL_sha256": row["WHEEL_sha256"], "wheel_code_imported": False}

def validate_graph(closure):
    by = {canonicalize_name(r["name"]): r for r in closure["actual_inputs"]}
    counts = closure["counts"]
    if len(closure["actual_dependency_checks"]) != 199 or \
            sum(c["applicable"] for c in closure["actual_dependency_checks"]) != 61:
        raise RuntimeError("finite graph cardinality differs")
    project = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]
    for text in project["dependencies"] + project["optional-dependencies"]["desktop"]:
        requirement = Requirement(text)
        if not requirement.marker or requirement.marker.evaluate({**closure["marker_environment"], "extra": "desktop"}):
            dep = canonicalize_name(requirement.name)
            if dep not in by or by[dep]["version"] not in requirement.specifier:
                raise RuntimeError("current project dependency differs from fixed wheel closure")
    for check in closure["actual_dependency_checks"]:
        requirement = Requirement(check["requirement"])
        applicable = not requirement.marker or any(requirement.marker.evaluate(
            {**closure["marker_environment"], "extra": e}) for e in ["", *check["extras"]])
        if applicable != check["applicable"]:
            raise RuntimeError("dependency marker graph differs")
        if applicable:
            dep = canonicalize_name(requirement.name)
            if dep not in by or by[dep]["version"] not in requirement.specifier or \
                    check["selected"] != dep or check["selected_version"] != by[dep]["version"]:
                raise RuntimeError("dependency graph version/closure differs")
    for label in ("backend", "runtime"):
        lines = lock_bytes(label).decode().splitlines()
        parsed = [re.fullmatch(r"([A-Za-z0-9_.-]+)==([^ ]+) --hash=sha256:([0-9a-f]{64})", x)
                  for x in lines if x and not x.startswith("#")]
        if any(x is None for x in parsed):
            raise RuntimeError("lock line is not exact name/version/hash")
        expected = {canonicalize_name(x[1]) for x in parsed}
        if len(parsed) != (3 if label == "backend" else 41):
            raise RuntimeError("lock cardinality differs")
        for match in parsed:
            selected = by[canonicalize_name(match[1])]
            if selected["version"] != match[2] or selected["actual_record"]["sha256"] != match[3]:
                raise RuntimeError("lock version/hash differs")
        if label == "backend" and expected != {"setuptools", "wheel", "packaging"}:
            raise RuntimeError("backend lock names differ")
    return closure["counts"]

def retained_source_function(filename, expected_sha, function):
    """Reuse one exact pure definition; never import/launch the old controller."""
    source = COMMON.parent / filename
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        raise RuntimeError("original stage/consumer function source changed")
    tree = ast.parse(raw)
    definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function]
    if len(definitions) != 1:
        raise RuntimeError("original helper definition is not unique")
    namespace = {"json": json, "Path": Path, "Refusal": _helpers().Refusal,
                 "INFO": (3, 5, 0, 9, 0), "NUMBER": 0x30500090}
    # network_denial's default connector is never called by preparation.
    if function == "network_denial":
        import errno
        import socket
        namespace.update(errno=errno, socket=socket)
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(source), "exec"), namespace)
    return namespace[function]

def draft_eight_commands(root):
    """Non-executable producer/consumer blueprint, awaiting real runtime binding."""
    raw = (COMMON.parent / "METADATA.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != "d127ca09640f4f4d8af7927bead65e1e44a9a7ca8d9070d21d80c12570000b8e":
        raise RuntimeError("original eight-command source plan changed")
    metadata = json.loads(raw)
    old_root = metadata["context"]["unit_root"]
    old_python = metadata["commands"][0]["argv"][0]
    commands = []
    for command in metadata["commands"]:
        argv = []
        for part in command["argv"]:
            if part == old_python:
                argv.append("<FRESH_HASH_VERIFIED_CPYTHON_31213_SSL359>")
            elif part.startswith(old_root):
                argv.append(str(root) + part[len(old_root):])
            elif part.startswith(str(COMMON.parent)) or part.endswith("consumers.py") or part.endswith("CONTEXT.json"):
                argv.append("<FRESH_REVIEWED_PRODUCER_CONTEXT>" if part.endswith("CONTEXT.json") else "<FRESH_REVIEWED_PROFILE_PRODUCER>")
            else:
                argv.append(part)
        commands.append({**command, "argv_blueprint": argv})
        commands[-1].pop("argv")
    return {"status": "DRAFT_NOT_EXECUTABLE_RUNTIME_AND_MAIN_REVIEW_REQUIRED", "commands": commands,
            "budget": metadata["budget"], "original_source_metadata_sha256": hashlib.sha256(raw).hexdigest(),
            "once": "NEW_SOURCE_AND_RUNTIME_BOUND_FIRST_RECORD_REQUIRED_OLD_ONCE_NOT_REUSED",
            "provider": {"INFO": [3, 5, 0, 9, 0], "NUMBER": 0x30500090, "TEXT_PREFIX": "OpenSSL 3.5.9 "},
            "ownership": "REUSE_ORIGINAL_POPEN_FIRSTCAUSE_OBSERVE_UNKNOWN_NO_REPLAY",
            "writes": "ONLY_NEW_BUILDER_RUNTIME_TMP_EVIDENCE_LOGS;INPUTS_READONLY",
            "offline": "ORIGINAL_STAGE_NETWORK_DENIAL_NUMERIC_EPERM_OR_EACCES_THEN_SAME_PID_EXECVE",
            "native_authorized": False}

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError("pinned acquisition refuses redirected URL")

def fetch(row, timeout):
    url = row["source_url"]
    if not url.startswith("https://files.pythonhosted.org/packages/") or \
            url.rsplit("/", 1)[-1] != row["filename"]:
        raise RuntimeError("unapproved official wheel URL")
    with urllib.request.build_opener(_NoRedirect).open(url, timeout=timeout) as response:
        if response.geturl() != url:
            raise RuntimeError("official URL changed")
        return response.read(row["actual_record"]["bytes"] + 1)

def prepare(root, *, download=False, reuse_manifest=None):
    root = Path(root)
    if not root.is_absolute() or root != root.resolve() or root.parent != Path("/private/tmp") or \
            not root.name.startswith("cvw26-"):
        raise RuntimeError("fresh task namespace must be canonical /private/tmp/cvw26-*")
    helper = _helpers()
    closure = load_closure()
    graph = validate_graph(closure)
    reusable = {}
    reuse_record = None
    if reuse_manifest is not None:
        raw, reuse_record = helper.raw_file(Path(reuse_manifest), maximum=4194304)
        prior = json.loads(raw)
        if prior.get("all43_present") is not True or len(prior.get("rows", [])) != 43:
            raise RuntimeError("reuse requires complete actual43 manifest")
        reusable = {r["filename"]: r["current_record"] for r in prior["rows"]}
        if set(reusable) != {r["filename"] for r in closure["actual_inputs"]}:
            raise RuntimeError("reusable wheel membership differs")
        for filename, record in reusable.items():
            if record["path"] != str(Path(reuse_manifest).parent / "wheelhouse" / filename):
                raise RuntimeError("reuse blob escaped original manifest wheelhouse")
    root.mkdir(mode=0o700, exist_ok=False)
    root_identity = helper.secure_dir(root)
    wheelhouse = root / "wheelhouse"
    wheelhouse.mkdir(mode=0o700)
    helper.publish(root / "FIRST_RECORD.json", {"root": str(root), "root_identity": root_identity,
                   "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   "closure_sha256": CLOSURE_SHA, "budget_bytes": sum(x["actual_record"]["bytes"] for x in closure["actual_inputs"]),
                   "budget_prepare_seconds": 600, "native_authorized": False})
    if reuse_record is not None:
        helper.publish(root / "REUSED_MANIFEST_FIRST_RECORD.json", reuse_record)
    results = []
    began = time.monotonic()
    try:
        for row in closure["actual_inputs"]:
            helper.secure_dir(root, root_identity)
            remaining = 600 - (time.monotonic() - began)
            if remaining <= 1:
                raise TimeoutError("shared prepare budget exhausted")
            original = Path(row["actual_record"]["path"])
            result = {"name": row["name"], "filename": row["filename"], "original_path": str(original)}
            if row["filename"] in reusable:
                current = reusable[row["filename"]]
                raw, _ = helper.raw_file(Path(current["path"]), expected=current)
                validate_wheel(raw, row)
                result.update(acquisition="READ_ONLY_CURRENT_MANIFEST_PINNED_COPY", original_current_record=current)
            elif original.exists() or original.is_symlink():
                raw, current = helper.raw_file(original)
                validate_wheel(raw, row)
                result.update(acquisition="READ_ONLY_LOCAL_HASH_MATCH_COPY", original_current_record=current)
            elif download:
                raw = fetch(row, min(30, remaining))
                result["acquisition"] = "OFFICIAL_EXACT_URL_TLS_DOWNLOAD"
            else:
                result.update(status="MISSING_OFFICIAL_REDOWNLOADABLE", expected=row["actual_record"])
                results.append(result)
                continue
            target = wheelhouse / row["filename"]
            # Preserve bytes even when a later metadata validation fails.
            helper.copy_blob(target, raw)
            validate_wheel(raw, row)
            if time.monotonic() - began >= 600:
                raise TimeoutError("shared prepare budget exhausted after acquisition")
            result.update(status="PASS_PINNED_WHEEL_BYTES_AND_METADATA", current_record=helper.raw_file(target)[1])
            results.append(result)
        for label in ("backend", "runtime"):
            helper.copy_blob(root / ("requirements-" + label + ".lock"),
                             lock_bytes(label))
        # Whole producer-consumer first records are checked again before
        # publication. A later substitution of an earlier source or copy cannot
        # be hidden by successfully checking only the most recent wheel.
        for result in results:
            for key in ("original_current_record", "current_record"):
                if key in result:
                    record = result[key]
                    helper.raw_file(Path(record["path"]), expected=record)
        helper.publish(root / "INPUTS_MANIFEST.json", {"rows": results, "graph": graph,
                       "all43_present": all(r["status"].startswith("PASS_") for r in results),
                       "status": "INPUT_PREPARATION_ONLY_NOT_INSTALL_NATIVE_ACCEPTANCE"})
        helper.publish(root / "EIGHT_COMMAND_BLUEPRINT.json", draft_eight_commands(root))
        if reuse_record is not None:
            helper.raw_file(Path(reuse_manifest), expected=reuse_record)
        return root / "INPUTS_MANIFEST.json"
    except BaseException as exc:
        helper.publish(root / "FIRST_FAILURE.json", {"error_type": type(exc).__name__, "error": str(exc), "completed_rows": results})
        raise

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--reuse-manifest", type=Path)
    args = parser.parse_args()
    print(prepare(args.root, download=args.download, reuse_manifest=args.reuse_manifest))
