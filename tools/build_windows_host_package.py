"""Offline Windows host package: reviewable intent, never host authorization.

build/check/validate-profile are local file operations. The emitted PowerShell
operator previews commands and checks package bytes; it has no apply mode.
The existing SCM bootstrap, trusted profile loader and host-change executor
remain the only production routes. No SID, receipt, grant or seal is minted.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import ntpath
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import subprocess
import sys
import zipfile

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SCHEMA = "comsol-mcp.windows-host-package/1"
SPEC_SCHEMA = "comsol-mcp.windows-host-package-input/1"
SHA = re.compile(r"[0-9a-f]{64}\Z")
MAX_JSON = 8 * 1024**2
MAX_ARCHIVE = 128 * 1024**2
MAX_EXPANDED = 512 * 1024**2
LAYERS = (
    ("{C38D57D1-05A7-4C33-904F-7FBCEEE60E82}", "OUTBOUND", "IPV4", "ALE_AUTH_CONNECT"),
    ("{4A72393B-319F-44BC-84C3-BA54DCB3B6B4}", "OUTBOUND", "IPV6", "ALE_AUTH_CONNECT"),
    ("{E1CD9FE7-F4B5-4273-96C0-592E487B8650}", "INBOUND", "IPV4", "ALE_AUTH_RECV_ACCEPT"),
    ("{A3B42C97-9F04-4672-B87E-CEE9C483257F}", "INBOUND", "IPV6", "ALE_AUTH_RECV_ACCEPT"),
)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def no_links(path):
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if part.is_symlink() or (part.exists() and getattr(part.lstat(), "st_file_attributes", 0) & 0x400):
            raise ValueError("symlink/reparse input or output refused")
    return path


def read_file(path, limit=MAX_ARCHIVE):
    path = no_links(path)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
        raise ValueError("bounded ordinary file required")
    data = path.read_bytes()
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or len(data) != before.st_size:
        raise ValueError("input changed during read")
    return data


def json_bytes(data):
    def pairs(items):
        row = {}
        for key, value in items:
            if key in row:
                raise ValueError("duplicate JSON key")
            row[key] = value
        return row
    def nonfinite(_):
        raise ValueError("nonfinite JSON")
    if len(data) > MAX_JSON:
        raise ValueError("JSON size bound")
    return json.loads(data, object_pairs_hook=pairs, parse_constant=nonfinite)


def file_ref(path):
    data = read_file(path)
    return {"path": str(Path(path).absolute()), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def read_ref(row, *, as_json=True):
    if (type(row) is not dict or set(row) != {"path", "bytes", "sha256"}
            or type(row["bytes"]) is not int or row["bytes"] < 0
            or type(row["sha256"]) is not str or not SHA.fullmatch(row["sha256"])):
        raise ValueError("exact file reference required")
    data = read_file(row["path"])
    if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
        raise ValueError("file reference changed")
    return json_bytes(data) if as_json else data


def safe_member(name):
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name
            or any(p.endswith((" ", ".")) or PureWindowsPath(p).is_reserved() for p in path.parts)):
        raise ValueError("unsafe Windows archive member")
    return path


def archive_members(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        rows = archive.infolist()
        if len(rows) > 10000 or sum(r.file_size for r in rows) > MAX_EXPANDED:
            raise ValueError("archive extent bound")
        seen = set()
        for row in rows:
            safe_member(row.filename)
            if row.filename.casefold() in seen or stat.S_ISLNK(row.external_attr >> 16) or row.flag_bits & 1:
                raise ValueError("duplicate/link/encrypted archive member")
            seen.add(row.filename.casefold())
        return {r.filename: archive.read(r) for r in rows if not r.is_dir()}


def verify_bundle(data, source):
    members = archive_members(data)
    package = json_bytes(members["PACKAGE_MANIFEST.json"])
    if package["target"] != "win_amd64":
        raise ValueError("Windows AMD64 bundle required")
    expected = {"wheelhouse/" + r["file"]: r for r in package["wheels"]}
    actual = {n for n in members if n.startswith("wheelhouse/")}
    if actual != set(expected):
        raise ValueError("whole wheelhouse member set mismatch")
    for name, row in expected.items():
        if len(members[name]) != row["bytes"] or hashlib.sha256(members[name]).hexdigest() != row["sha256"]:
            raise ValueError("wheelhouse hash mismatch")
    lock = members["locks/requirements.lock"].decode("utf-8")
    if any(r["sha256"] not in lock for r in expected.values()):
        raise ValueError("wheel missing from require-hashes install lock")
    apps = [n for n in actual if PurePosixPath(n).name.startswith("comsol_mcp-")]
    if len(apps) != 1:
        raise ValueError("one application wheel required")
    wheel = archive_members(members[apps[0]])
    source_rows = {r["path"]: r for r in source["files"] if r["path"].startswith("comsol_mcp/")}
    wheel_rows = {n for n in wheel if n.startswith("comsol_mcp/")}
    if wheel_rows != set(source_rows) or any(hashlib.sha256(wheel[n]).hexdigest() != r["sha256"] for n, r in source_rows.items()):
        raise ValueError("application wheel does not match the frozen production source")
    return {"wheel_count": len(expected), "app_source_members": len(source_rows),
            "application_wheel_sha256": hashlib.sha256(members[apps[0]]).hexdigest(),
            "install_lock_sha256": hashlib.sha256(members["locks/requirements.lock"]).hexdigest()}


def source_functions(source_root):
    """Fingerprint only existing interfaces used by this package, without executing them."""
    names = ["_w26_os_resources.py", "_windows_host_change.py", "_windows_t037_bootstrap.py",
             "_windows_t037_broker.py", "_windows_service_sid.py", "_windows_appcontainer.py",
             "_windows_filter_group.py", "_windows_t037_network.py", "_windows_t037.py"]
    return {"comsol_mcp/" + n: file_ref(Path(source_root) / "comsol_mcp" / n)["sha256"] for n in names}


def target_template(host, target, ephemeral, python_hash, java_hash):
    root = host["new_root"]
    version = target["version"]
    workspace = target["workspace"]
    state = ntpath.join(workspace, "state")
    python = ntpath.join(root, "python312", "tools", "python.exe")
    java = ntpath.join(root, "jdk11", "bin", "java.exe")
    server_port, worker_port = target["proposed_ports"]["server"], target["proposed_ports"]["worker_listener"]
    from comsol_mcp._windows_t037_network import validate_port_range
    validate_port_range(tuple(ephemeral))
    if any(type(p) is not int or not 1 <= p <= 65535 for p in (server_port, worker_port)) or server_port == worker_port:
        raise ValueError("two distinct explicit listener ports required")
    grants = []
    for role, direction, port in [("worker", "OUTBOUND", server_port), ("server", "INBOUND", server_port), ("worker", "INBOUND", worker_port)]:
        layer = LAYERS[0] if direction == "OUTBOUND" else LAYERS[2]
        row = dict(role=role, direction=direction, layer=layer[3], layer_key=layer[0], address_family="IPV4", protocol=6,
                   application_path=java if role == "worker" else None, principal_sid=None,
                   local_addresses=["127.0.0.1/32"], remote_addresses=["127.0.0.1/32"],
                   local_ports=[] if direction == "OUTBOUND" else [port], remote_ports=[port] if direction == "OUTBOUND" else [])
        row["local_port_range" if direction == "OUTBOUND" else "remote_port_range"] = list(ephemeral)
        grants.append(row)
    # Retain four BLOCK GUIDs and exactly three IPv4 permits from the old plan.
    selected = [f for f in target["filters"] if f["action"] == "BLOCK" or f["address_family"] == "IPV4"]
    if len(selected) != 7 or len({f["filter_key"].casefold() for f in selected}) != 7:
        raise ValueError("four defaults plus three IPv4 permits required")
    defaults = [f for f in selected if f["action"] == "BLOCK"]
    if ({(f["layer_key"], f["direction"], f["address_family"]) for f in defaults}
            != {(l[0], l[1], l[2]) for l in LAYERS} or len(defaults) != 4):
        raise ValueError("all four exact package defaults must be retained")
    acl = [dict(path=p, principal_sid=None, rights=rights, security_descriptor_sha256=None) for p, rights in [
        (workspace, ["READ", "EXECUTE"]), (ntpath.join(workspace, "data"), ["READ", "WRITE"]),
        (ntpath.join(root, "jdk11"), ["READ", "EXECUTE"]),
        (None, ["READ", "EXECUTE"]), (None, ["READ", "WRITE"]), (None, ["READ", "WRITE"])]]
    profile = dict(schema="comsol-mcp.host-resource-profile/v1", profile_id="windows63" if version == "63" else "windows64",
        project_id=None, platform="windows", host_id=None,
        root=dict(path=workspace, volume_id=None, file_id=None, owner_id="S-1-5-18"),
        launcher_account_id="S-1-5-18", service_account_id="S-1-5-18", interactive_account_id=None,
        applied_policy_id=target["appcontainer_name"],
        launch_images=[dict(role=r, path=p, image_sha256=h, argv_sha256=None) for r, p, h in [
            ("launcher", python, python_hash), ("server", None, target["exact_rule_image_binding"]["server_source_sha256"]), ("worker", java, java_hash)]],
        file_grants=[dict(path=g["path"], rights=g["rights"]) for g in acl],
        network_grants=[dict(kind="LOOPBACK_ENDPOINT", endpoint="127.0.0.1", port=p) for p in (server_port, worker_port)],
        disk_budget={"mode": "ADMISSION_ONLY", **copy.deepcopy(host["budget_proposal"]["disk_admission_only"])},
        allowed_operations=["model.inspect"],
        windows_isolation=dict(schema="comsol-mcp.windows-isolation-policy/1", service_name=host["service"]["name"],
            service_start_name=host["service"]["SCM_start_name"], service_logon_sid="S-1-5-18", service_sid=None,
            service_sid_type="RESTRICTED", appcontainer_profile_name=target["appcontainer_name"], appcontainer_sid=None,
            capability_sids=[], acl_grants=acl, wfp_provider_key=target["provider_key"], wfp_sublayer_key=target["sublayer_key"],
            wfp_filter_keys=[f["filter_key"] for f in selected], wfp_grants=grants, host_fingerprint_sha256=None, default_network_action="BLOCK"))
    return dict(schema="comsol-mcp.windows-t037-profile/3", host_resource_profile=profile,
                job_limits=copy.deepcopy(host["budget_proposal"]["job_limits"])), state


def fixed_fields(template, candidate, prefix=""):
    """Nulls are unresolved SDK/metadata inputs, never example SIDs or evidence."""
    if template is None:
        if prefix == "/host_resource_profile/disk_budget/quota_profile_id":
            # ADMISSION_ONLY explicitly has no hard-quota object. This null
            # is fixed configuration, unlike unresolved native identity slots.
            if candidate is not None:
                raise ValueError("unrequested quota scope changed")
            return
        if candidate is None:
            raise ValueError("unresolved profile input")
        return
    if type(template) is dict:
        if type(candidate) is not dict or set(template) != set(candidate):
            raise ValueError("profile shape or extra field changed")
        for k in template:
            fixed_fields(template[k], candidate[k], prefix + "/" + k)
    elif type(template) is list:
        if type(candidate) is not list or len(template) != len(candidate):
            raise ValueError("profile list scope changed")
        for i, (left, right) in enumerate(zip(template, candidate)):
            fixed_fields(left, right, prefix + "/" + str(i))
    elif type(template) is not type(candidate) or template != candidate:
        raise ValueError("fixed profile scope changed")


def validate_profile(template, document, session_id, *, source_sha256=None):
    """Schema/intent validation only. The production pinned loader is still required."""
    fixed_fields(template, document)
    from comsol_mcp._windows_t037 import parse_profile_v3_document
    from comsol_mcp._windows_t037_network import derive_session_network_intent
    profile = parse_profile_v3_document(document, source_sha256=source_sha256 or digest(document))
    host = profile.host_resource_profile
    if (not host.windows_isolation.service_sid.startswith("S-1-5-80-")
            or not host.windows_isolation.appcontainer_sid.startswith("S-1-15-2-")):
        raise ValueError("new service/AppContainer roles require their actual native SID namespaces")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", session_id):
        raise ValueError("explicit fresh session metadata required")
    key = hashlib.sha256((host.project_id + "\0" + session_id).encode()).hexdigest()
    session = ntpath.join(host.root.path, "state", "sessions", key)
    expected_server = ntpath.join(session, "owned-server", "installation", "bin", "win64", "comsolmphserver.exe")
    if host.launch_images[1].path != expected_server:
        raise ValueError("server path is not the exact project/session materialization")
    principal = host.windows_isolation.appcontainer_sid
    if any(g.principal_sid != principal for g in host.windows_isolation.acl_grants):
        raise ValueError("child ACL role was swapped")
    expected_paths = [host.root.path, ntpath.join(host.root.path, "data"),
                      ntpath.dirname(ntpath.dirname(host.launch_images[2].path)),
                      ntpath.dirname(expected_server), ntpath.join(session, "worker"), ntpath.join(session, "owned-server", "runtime")]
    if [g.path for g in host.windows_isolation.acl_grants] != expected_paths:
        raise ValueError("ACL paths escaped the exact data/code layout")
    if [g.path for g in host.file_grants] != expected_paths:
        raise ValueError("file grants differ from reviewed layout")
    intent = derive_session_network_intent(profile)
    return dict(state="SCHEMA_VALID_INTENT_ONLY", trusted_startup=False, native_admission="NOT_RUN",
                profile_sha256=profile.sha256, network_intent=intent.as_dict(),
                source_binding="EXACT_INPUT_FILE_BYTES_NOT_TRUSTED_PATH" if source_sha256 else "CANONICAL_DOCUMENT_ONLY_NOT_FILE_IDENTITY")


def wfp_plan(document, security, group_id):
    """Use the existing pure plan builder and native action normalizer; never apply."""
    from comsol_mcp import _w26_os_resources as resources, _windows_filter_group as group
    from comsol_mcp._windows_host_change import _normalize_supported_actions
    host = resources.parse_host_resource_profile(document["host_resource_profile"])
    policy = host.windows_isolation
    if set(security) != {"provider", "sublayer"}:
        raise ValueError("actual serialized provider/sublayer descriptor material required")
    members = []
    for index, layer in enumerate(LAYERS):
        members.append(dict(schema=group.SCHEMA, group_id=group_id, group_sha256="0" * 64,
            filter_key=policy.wfp_filter_keys[index], provider_key=policy.wfp_provider_key,
            sublayer_key=policy.wfp_sublayer_key, action="BLOCK", weight=10, flags=1, layer_key=layer[0],
            direction=layer[1], address_family=layer[2], principal_sid=policy.appcontainer_sid, grant_sha256=None))
    for index, grant in enumerate(policy.wfp_grants):
        members.append({**members[0], "filter_key": policy.wfp_filter_keys[4 + index], "action": "PERMIT", "weight": 20,
                        "layer_key": grant.layer_key, "direction": grant.direction, "address_family": grant.address_family,
                        "grant_sha256": group.digest(grant.as_dict())})
    fingerprint = group.group_fingerprint(host, members)
    for m in members:
        m["group_sha256"] = fingerprint
    empty = hashlib.sha256(b"").hexdigest()
    desired = [dict(provider_key=policy.wfp_provider_key, display_name=policy.service_name, description="Bounded COMSOL host package",
                    flags=1, provider_data_sha256=empty, service_name=policy.service_name, **security["provider"]),
               dict(sublayer_key=policy.wfp_sublayer_key, display_name=policy.appcontainer_profile_name,
                    description="Bounded COMSOL host package", flags=1, provider_key=policy.wfp_provider_key,
                    provider_data_sha256=empty, weight=100, **security["sublayer"]), *members]
    kinds = ["wfp_provider", "wfp_sublayer", *(["wfp_filter"] * len(members))]
    actions = [dict(action_id="resource-" + str(i), resource_kind=kind, resource_name="resource-" + str(i), operation="CREATE",
                    desired=value, baseline_identity=None, baseline_sha256=None, backup_path=None, backup_sha256=None)
               for i, (kind, value) in enumerate(zip(kinds, desired))]
    plan = resources.build_host_change_plan(host, change_manifest=actions, operator_inputs={})
    _normalize_supported_actions(plan, host)
    return dict(payload_json=plan.payload_json, sha256=plan.sha256)


def prepare_plans(template, document, session_id, security, group_id, output, *, source_sha256=None):
    """Compile existing immutable plan envelopes after explicit material binding."""
    result = validate_profile(template, document, session_id, source_sha256=source_sha256)
    wfp = wfp_plan(document, security, group_id)
    from comsol_mcp import _w26_os_resources as resources
    from comsol_mcp._windows_host_change import _normalize_supported_actions
    host = resources.parse_host_resource_profile(document["host_resource_profile"])
    policy = host.windows_isolation
    action = dict(action_id="appcontainer-create", resource_kind="appcontainer_profile", resource_name=policy.appcontainer_profile_name,
                  operation="CREATE", desired=dict(profile_id=host.applied_policy_id, policy_sha256=resources._sha256(policy.as_dict())),
                  baseline_identity=None, baseline_sha256=None, backup_path=None, backup_sha256=None)
    app = resources.build_host_change_plan(host, change_manifest=[action], operator_inputs={})
    _normalize_supported_actions(app, host)
    output = no_links(output)
    if output.exists():
        raise FileExistsError("REFUSE_PLAN_OVERWRITE")
    output.mkdir()
    _write(output, "wfp.plan.json", wfp)
    _write(output, "appcontainer.plan.json", dict(payload_json=app.payload_json, sha256=app.sha256))
    _write(output, "service-sid-update.template.json", dict(action_id="service-sid-update", resource_kind="windows_service",
        resource_name=policy.service_name, operation="UPDATE", desired=dict(schema="comsol-mcp.windows-service-sid-config/1",
            **{k: getattr(policy, k) for k in ("service_name", "service_start_name", "service_logon_sid", "service_sid", "service_sid_type")}),
        baseline_identity=None, baseline_sha256=None, backup_path=None, backup_sha256=None))
    _write(output, "STATUS.json", {**result, "host_apply": "NOT_AUTHORIZED", "receipt": None, "authorization_ref": None,
        "service_sid_update": "UNBOUND: actual existing service identity/config and original canonical backup needed; use existing native leaf."})
    return result


def _write(root, name, value):
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as f:
        f.write(value if isinstance(value, bytes) else canonical(value) + b"\n")


def safe_license_metadata(host):
    """Never copy lines, feature names, SIGN/ISSUER or arbitrary nested fields."""
    source = host["network"]["license"]
    if source["observed_COMSOL_server_vendor_endpoints"] or source["external_license_allow_rules"]:
        raise ValueError("new license endpoint policy needs Main review; no wildcard addition")
    files = []
    for row, target in zip(source["files"], host["targets"]):
        expected = ntpath.join(target["installation_root"], "license", "license.dat")
        if row["version"] != target["version"] or row["path"] != expected or not SHA.fullmatch(row["sha256"]):
            raise ValueError("selected license metadata scope changed")
        counts = {k: v for k, v in row["allowlisted_directive_counts"].items() if k in {"FEATURE", "PACKAGE", "INCREMENT"}}
        kinds = {k: v for k, v in row["count_kinds"].items() if k == "UNCOUNTED"}
        if any(type(v) is not int or v < 0 for v in [*counts.values(), *kinds.values()]):
            raise ValueError("license count metadata malformed")
        if row["vendor"] != ["LMCOMSOL"]:
            raise ValueError("selected vendor metadata changed")
        files.append(dict(version=row["version"], path=expected, sha256=row["sha256"],
                          allowlisted_directive_counts=counts, count_kinds=kinds, vendor=["LMCOMSOL"]))
    if len(files) != 2 or len(source["files"]) != 2:
        raise ValueError("both selected license metadata rows required")
    return dict(scope="Historical selected license metadata only; no proof of no custom configuration.", files=files,
                observed_COMSOL_server_vendor_endpoints=[], external_license_allow_rules=[],
                license_legitimacy_checkout_and_product_rights="UNVERIFIED_NOT_RUN")


def unresolved(value, prefix=""):
    if prefix == "/host_resource_profile/disk_budget/quota_profile_id":
        return []
    if value is None:
        return [prefix]
    if type(value) is dict:
        return [p for k, v in value.items() for p in unresolved(v, prefix + "/" + k)]
    if type(value) is list:
        return [p for i, v in enumerate(value) for p in unresolved(v, prefix + "/" + str(i))]
    return []


OPERATOR = r'''param([ValidateSet('Inspect','InstallPlan','ApplyPlan','RestorePlan','Verify')][string]$Phase='Inspect')
$ErrorActionPreference='Stop'
# This script has no host apply/install mode. It previews the reviewed steps.
$packet=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'HOST_PACKAGE.json') -Raw | ConvertFrom-Json
if ($Phase -eq 'Verify') {
  $manifest=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'MANIFEST.json') -Raw | ConvertFrom-Json
  foreach ($entry in $manifest.files) {
    if ($entry.path -match '(^[\\/]|\.\.|:)' ) { throw 'UNSAFE_PACKAGE_PATH' }
    $path=Join-Path $PSScriptRoot $entry.path
    $item=Get-Item -LiteralPath $path -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'REPARSE_REFUSED' }
    if ($item.Length -ne $entry.bytes -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.sha256) { throw 'PACKAGE_CHANGED' }
  }
  'FILES_MATCH_ONLY_NOT_HOST_AUTHORIZATION'
} elseif ($Phase -eq 'Inspect') {
  $packet | ConvertTo-Json -Depth 32
} else {
  $selected=@($packet.steps | Where-Object { $_.phase -eq $Phase })
  if ($Phase -eq 'InstallPlan') { $selected=@($packet.install_commands)+$selected }
  $selected | ConvertTo-Json -Depth 24
}
'''


def build(spec, output):
    fields = {"schema", "source_manifest", "source_root", "host_plan", "network_observation", "offline_bundle", "windows_materials", "python_runtime"}
    if type(spec) is not dict or set(spec) != fields or spec["schema"] != SPEC_SCHEMA:
        raise ValueError("closed host package input required")
    source = read_ref(spec["source_manifest"])
    root = no_links(spec["source_root"])
    names = [r["path"] for r in source["files"]]
    actual = {p.relative_to(root).as_posix() for p in (root / "comsol_mcp").rglob("*") if p.is_file()
              and not any(s.startswith("._") or s == "__pycache__" for s in p.parts) and p.suffix not in {".pyc", ".pyo"}}
    if len(names) != len(set(names)) or actual != set(names):
        raise ValueError("whole frozen production source membership changed")
    for row in source["files"]:
        safe_member(row["path"])
        data = read_file(root / row["path"])
        if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError("frozen source drift")
    host = read_ref(spec["host_plan"])
    if host["apply_ready"] is not False or host["authorized_to_apply"] is not False or len(host["targets"]) != 2:
        raise ValueError("historical plan cannot confer apply/authorization")
    if host["service"]["SCM_start_name"] != "LocalSystem" or host["service"]["new_SAM_account_required"] is not False:
        raise ValueError("account policy decision requires Main review")
    network = read_ref(spec["network_observation"])
    if network["state"] != "OBSERVED_READONLY_NOT_NATIVE_PROOF" or network["host_settings_changes"] != 0 or network["netsh_query_count"] != 1:
        raise ValueError("accepted historical dynamic range readback required; no guessed default")
    ephemeral = network["observation"]["source_port_range"]
    materials = read_ref(spec["windows_materials"])
    jdk_rows = {r["path"]: r for r in materials["jdk_files"]}
    if len(jdk_rows) != len(materials["jdk_files"]) or materials["host_changes"] != 0:
        raise ValueError("bounded original JDK inventory required")
    for name in jdk_rows:
        safe_member(name)
    python_bytes = read_ref(spec["python_runtime"], as_json=False)
    if hashlib.sha256(python_bytes).hexdigest() != host["normal_installation"]["package"]["sha256"]:
        raise ValueError("official CPython artifact differs from reviewed material")
    python_hash = hashlib.sha256(archive_members(python_bytes)["tools/python.exe"]).hexdigest()
    bundle = read_ref(spec["offline_bundle"], as_json=False)
    bundle_result = verify_bundle(bundle, source)
    license_metadata = safe_license_metadata(host)
    prepared = [target_template(host, target, ephemeral, python_hash, jdk_rows["bin/java.exe"]["sha256"]) for target in host["targets"]]
    output = no_links(output)
    if output.exists():
        raise FileExistsError("REFUSE_PACKAGE_OVERWRITE")
    output.mkdir()
    _write(output, "materials/offline.zip", bundle)
    _write(output, "materials/python.nupkg", python_bytes)
    _write(output, "materials/jdk-source-files.json", materials)
    _write(output, "SOURCE.json", source)
    steps = []
    targets = []
    for target, (template, state) in zip(host["targets"], prepared):
        version = target["version"]
        config = ntpath.join(host["new_root"], "profiles", "comsol" + version + "-v3.json")
        provider = ntpath.join(host["new_root"], "profiles", "host-provider" + version + ".json")
        memory = ntpath.join(host["new_root"], "profiles", "memory" + version + ".json")
        disk = ntpath.join(host["new_root"], "profiles", "disk" + version + ".json")
        argv = [ntpath.join(host["new_root"], "python312", "tools", "python.exe"), "-B", "-m", "comsol_mcp._control_daemon",
                "--home", target["control_home"], "--windows-t037-profile", config, "--windows-scm-service-name", host["service"]["name"],
                "--host-provider-config", provider, "--memory-resource-config", memory, "--disk-resource-config", disk]
        item = dict(version=version, installation_root=target["installation_root"], version_expectation=target["actual_version"],
                    profile_root=target["workspace"], project_data=ntpath.join(target["workspace"], "data"), session_state_root=state,
                    profile_path=config, service_argv=argv, scm_image_path=subprocess.list2cmdline(argv),
                    project_id=None, session_id=None, session_home=None,
                    server_listener=target["proposed_ports"]["server"], worker_listener=target["proposed_ports"]["worker_listener"],
                    historical_source_port_range=ephemeral, group_id=target["group_id"],
                    launch_contracts={"server": {"producer": "_session_server.build_server_command", "fixed_port_argument": ["-port", str(target["proposed_ports"]["server"])],
                                                 "image_path": None, "argv": None, "argv_sha256": None},
                                      "worker": {"producer": "_java_worker.PersistentJavaWorker.start", "image_path": template["host_resource_profile"]["launch_images"][2]["path"],
                                                 "main_class": "comsol_mcp.worker_java.PersistentComsolWorker", "fixed_port_argument": ["--port", str(target["proposed_ports"]["worker_listener"])],
                                                 "argv": None, "argv_sha256": None}},
                    profile_state="UNRESOLVED_TEMPLATE_NOT_TRUSTED", native_admission="NOT_RUN")
        targets.append(item)
        _write(output, "profiles/" + version + ".template.json", template)
        _write(output, "profiles/" + version + ".unresolved.json", dict(fields=unresolved(template),
            bindings={"project/session": "Existing project_create/project registry and actual session metadata; do not reuse old IDs.",
                      "service_sid": "Actual SCM/LookupAccountNameW for exact NT SERVICE\\" + host["service"]["name"],
                      "appcontainer_sid": "Original SDK CreateAppContainerProfile/readback for " + target["appcontainer_name"],
                      "root.volume/fileID": "GetFileInformationByHandle on original retained protected root HANDLE",
                      "security hashes": "Actual serialized GetSecurityInfo SD bytes; no SDDL-text hash substituted for raw SD hash.",
                      "image/argv": "Existing launch/compile producers, verified private copies and actual image hashes; no port0/IPv6 fallback."},
            authority="NONE", source_port_range_status="HISTORICAL_READONLY_REOBSERVE_BEFORE_NATIVE_ADMISSION"))
        _write(output, "profiles/provider" + version + ".json", {"schema": "comsol-mcp.host-scoped-provider-config/1", "destinations": [], "grants": []})
        budget = host["budget_proposal"]
        _write(output, "profiles/memory" + version + ".template.json", dict(schema="comsol-mcp.memory-resource-profile/v1", profile_id="windows-memory-" + version,
               host_id=None, platform="windows", budget=budget["admission_only"], allowed_operations=["model.inspect"],
               trusted_estimates=[{"operation_id": "model.inspect", "estimate_bytes": budget["admission_only"]["max_task_reservation_bytes"]}]))
        _write(output, "profiles/disk" + version + ".template.json", dict(schema="comsol-mcp.disk-resource-config/v1", host_id=None,
            platform="windows", profiles=[dict(profile_id="windows-disk-" + version, project_id=None,
                root=copy.deepcopy(template["host_resource_profile"]["root"]), storage_id=None,
                disk_budget=copy.deepcopy(template["host_resource_profile"]["disk_budget"]), allowed_operations=["model.inspect"],
                trusted_estimates=[{"operation_id": "model.inspect", "estimate_bytes": budget["disk_admission_only"]["max_task_reservation_bytes"]}])]))
        _write(output, "profiles/bootstrap" + version + ".template.json", dict(schema="COMSOL_MCP_WINDOWS_SCM_BOOTSTRAP_V1",
            service_name=host["service"]["name"], profile_source_sha256=None, host_profile_sha256=None, project_id=None,
            phase="APPLY", authorization_ref=None, plan=None, receipt=None))
        steps.extend([
            dict(phase="InstallPlan", resource="SCM-" + version, state="NOT_AUTHORIZED", argv=(
                 [r"C:\Windows\System32\sc.exe", "create", host["service"]["name"], "type=", "own", "start=", "demand", "obj=", "LocalSystem", "binPath=", item["scm_image_path"]]
                 if version == "63" else [r"C:\Windows\System32\sc.exe", "config", host["service"]["name"], "binPath=", item["scm_image_path"]]),
                 condition="Register this single absent service once for63;64 is an exact configuration switch only after original owned children are terminal. RequiredPrivileges and service DACL use the reviewed exact new service scope; no global LSA rights."),
            dict(phase="ApplyPlan", resource="AppContainer-" + version, state="NOT_AUTHORIZED", entrypoint="_windows_appcontainer via existing SCM bootstrap",
                 name=target["appcontainer_name"], new_SID=None, condition="Original CreateAppContainerProfile SDK return/readback bound to this exact name. No synthetic SID; existing leaf enforces service subject."),
            dict(phase="ApplyPlan", resource="WFP-" + version, state="NOT_AUTHORIZED", entrypoint="WindowsHostChangeExecutor via _windows_t037_bootstrap",
                 provider=target["provider_key"], sublayer=target["sublayer_key"], filter_keys=template["host_resource_profile"]["windows_isolation"]["wfp_filter_keys"],
                 conditions="Four package BLOCK defaults; threeIPv4 exact role/image destination permits with source-range. Compare fresh native E before admission; noIPv6permit. BFE must already run; never start it as a fallback."),
            dict(phase="RestorePlan", resource="host-sequence-" + version, state="NOT_AUTHORIZED", entrypoint="Original SCM RESTORE bootstrap + exact original apply receipt",
                 compare="Fresh dependency/object/marker/security fingerprints must match original receipt; unknown/replaced/extra dependents refuse without delete. Reverse WFP filters->sublayer->provider, then AppContainer and service-SID original backups; no invented restore receipt.")])
    packet = dict(schema=SCHEMA, state="REVIEWABLE_INTENT_ONLY", apply_ready=False, host_apply="NOT_AUTHORIZED", native_admission="NOT_RUN", ISOLATION="UNVERIFIED",
        source_commit=source["commit"], source_manifest_sha256=spec["source_manifest"]["sha256"], source_interfaces=source_functions(root),
        input_bindings=spec, bundle=bundle_result, service=host["service"]["name"], service_account="LocalSystem", new_local_SAM_accounts=0,
        service_configuration=dict(service_type=host["service"]["type"], start=host["service"]["start"], automatic_restart=False,
            service_sid_type="RESTRICTED", service_sid=None, original_SCM_descriptor=None,
            required_privileges_proposal=host["service"]["required_privileges_proposal"],
            scope="Only this new own-process service. No global LSA right/account changes; actual SCM token/config/required-privilege readback pending.",
            restricted_SID_entrypoint="Existing _windows_service_sid UPDATE leaf with actual canonical backup; no sc.exe shortcut or fabricated receipt."),
        protected_root=host["new_root"], targets=targets, steps=steps, resource_budget=host["budget_proposal"],
        dynamic_object_binding="Fresh actual IDs may fill nulls only through original creation/SDK readback for these exact names. Schema/JSON/hash agreement is intent, not observation or authorization. Main must use existing trusted SCM/provider/SQLite admission.",
        known_gaps=[
            {"id": "SCM-ACL-BOOTSTRAP", "source": "_windows_host_change._normalize_supported_actions", "reason": "Initial SCM registration/root/ACL/privilege bootstrap is operator work outside current native executor; Main SCM-source integration pending."},
            {"id": "WINDOWS-DISK-STARTUP", "source": "_w26_os_resources.TrustedDiskResourceConfig.load", "reason": "Windows returns DISK_RESOURCE_CONFIG_PROVIDER_UNSUPPORTED; disk budget retained; service argv cannot silently claim it is enforced."},
            {"id": "PREBIRTH-ISSUER", "source": "T037SessionProcessFactory.preflight", "reason": "Existing native prebirth/child-proof issuer refuses; this package does not unlock it."},
            {"id": "FRESH-NATIVE-MATERIALS", "reason": "Real project/session allocation, new service/package SID, root fileID/volume, final SDs/hashes, protected JDK/vendor copy and fresh OS source range remain unobserved. Raw license checkout and native compatibility NOT_RUN."}],
        preserve=["Existing Desktop20592 and any other original jobs", "Original COMSOL63/64 installations", "OldPREP/R3/SHARED_SID/network/hostplan raw", "All unrelated services/security/license/network state"],
        license_metadata=license_metadata, rollback_baselines=host["rollback_baselines"],
        BFE_delegation={**{k: host["bounded_BFE_delegation_proposal"][k] for k in ("engine", "containers", "layers", "ACE_flags", "new_object_DACLs", "baseline_refs")},
            "state": "NOT_AUTHORIZED_NOT_APPLIED", "maximum_shared_descriptors": 8, "new_WFP_objects": 18,
            "condition": "Only if actual fresh restricted service access requires it; compare and preserve raw original SDs. No BFE startup, packet/global firewall relaxation or historical-object grants."},
        rollback_rules=["CREATE: freshly verify absent, retain actual post-create identity/hash/SD and original operation receipt. No name-only ownership.",
            "UPDATE: preserve exact original raw descriptor/config bytes, identity and hash before change. Restore only if current still equals the exact post-image.",
            "Third-party drift, extra children or unresolved original resources: keep UNKNOWN, do not reacquire/force-delete/retry opaque closers.",
            "Service stop/delete only for this new service after real job quiescence and original SCM configuration/process binding comparison; never DesktopPID or unrelated service.",
            "Owned tree removal only after hash/fileID/reparse/manifest and live-owner checks, external rollback archive retained; preserve user output and unexpected files.",
            "BFE shared descriptor restore only on exact current-postimage comparison to retained original bytes. No global firewall/security reset."],
        ACL_layout=["SYSTEM owner; SYSTEM/Administrators control on new code/config/profiles; exact restricted service SID only necessary control.",
            "No AppContainer write on Python/JDK/Workerclasses/profiles/provider/receipts or another target. RX/traverse on code, explicit data/temp/runtime/log/recovery leaves only.",
            "Worker endpoint parent needs file-create-only access without ADD_SUBDIRECTORY/DELETE_CHILD; precompiled classes get protected RX. Actual SDK SD serialization/hash and native access comparison remain pending.",
            "No ACL change to ProgramFiles/vendor/user historical directories. If existing launcher cannot provide required finite private vendor material, stop for Main; never grant broad user-tree access."],
        environment_proposal=dict(TEMP=ntpath.join(host["new_root"], "install-temp"), TMP=ntpath.join(host["new_root"], "install-temp"),
            PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1", PIP_CACHE_DIR=ntpath.join(host["new_root"], "install-temp", "pip-cache"),
            scope="Installer only; no global environment/PATH/registry edits. Main SCM integration must bind each service/child TEMP to its approved target leaf before launch; inherited environment is not proof."),
        install_commands=[dict(action="ExtractOfficialPython", archive="materials/python.nupkg", destination=ntpath.join(host["new_root"], "python312"),
                expected_sha256=spec["python_runtime"]["sha256"], state="NOT_EXECUTED_NOT_AUTHORIZED", compare="Fresh absent protected namespace only, exclusive ordinary files, no reparse/overwrite; exact archive inventory/hash before and after extraction."),
            dict(action="ExtractOfflineBundle", archive="materials/offline.zip", destination=ntpath.join(host["new_root"], "offline"),
                expected_sha256=spec["offline_bundle"]["sha256"], state="NOT_EXECUTED_NOT_AUTHORIZED", compare="Use hash-pinned existing release helper. Check all35wheels/lock/source manifests before any Python execution."),
            dict(argv=[ntpath.join(host["new_root"], "python312", "tools", "python.exe"), "-B", "-m", "pip", "install", "--no-index", "--require-hashes", "--disable-pip-version-check", "--find-links", ntpath.join(host["new_root"], "offline", "wheelhouse"), "-r", ntpath.join(host["new_root"], "offline", "locks", "requirements.lock")], state="NOT_EXECUTED_NOT_AUTHORIZED", reason="Direct normal CPython prefix; no venv redirector presented as same-PID SCM image."),
            dict(action="CopyJDK", source=materials["jdk_root"], destination=ntpath.join(host["new_root"], "jdk11"), manifest="materials/jdk-source-files.json", state="NOT_EXECUTED_NOT_AUTHORIZED", compare="No reparse/overwrite; all source and destination file hashes must match the finite inventory before execution.", file_count=len(jdk_rows))])
    _write(output, "HOST_PACKAGE.json", packet)
    acl_rows = []
    for leaf in ("python312", "jdk11", "profiles"):
        acl_rows.append(dict(path=ntpath.join(host["new_root"], leaf), owner="S-1-5-18", protected_DACL=True,
            full_control=["S-1-5-18", "S-1-5-32-544"], restricted_service_sid=None, service_mask="0x001200A9",
            appcontainer_write=False, before_raw_SD=None, before_sha256=None, after_raw_SD=None, after_sha256=None))
    for target, (template, _) in zip(targets, prepared):
        for index, grant in enumerate(template["host_resource_profile"]["windows_isolation"]["acl_grants"]):
            acl_rows.append(dict(path=grant["path"], version=target["version"], profile_grant_index=index,
                principal_sid=None, principal_name=next(t["appcontainer_name"] for t in host["targets"] if t["version"] == target["version"]),
                rights=grant["rights"], before_raw_SD=None, before_sha256=None, after_raw_SD=None, after_sha256=None,
                scope="Only new owned subtree; Worker classes protected separately from file-create-only endpoint parent."))
    _write(output, "ACL_CHANGE_TEMPLATES.json", dict(host_apply="NOT_AUTHORIZED", native_baseline="NOT_CAPTURED", changes=acl_rows,
        entrypoint="Operator bootstrap outside host-change executor; existing protected descriptor/retained HANDLE APIs, no new setter in this builder."))
    _write(output, "operator.ps1", OPERATOR.encode())
    _write(output, "README.txt", ("Run operator.ps1 -Phase Inspect or Verify. All modes are readonly/preview. No install/apply mode.\n"
        "Profiles/*.template.json contain null native inputs and are deliberately not loadable as trusted profiles.\n"
        "Use validate-profile for schema/intent checking only, with actual new metadata supplied outside this tool.\n"
        "Use existing SCM bootstrap with genuine scoped provider/receipt after Main review and authorization.\n"
        "Initial protected namespace/SCM/ACL bootstrap and Windows disk adapter remain explicit gaps.\n").encode())
    rows = []
    for p in sorted(output.rglob("*")):
        if p.is_file():
            row = file_ref(p);row["path"] = p.relative_to(output).as_posix();rows.append(row)
    _write(output, "MANIFEST.json", dict(schema=SCHEMA, files=rows, authority="NONE"))
    check(output)
    return packet


def check(root):
    root = no_links(root)
    manifest = json_bytes(read_file(root / "MANIFEST.json", MAX_JSON))
    expected = {r["path"]: r for r in manifest["files"]}
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    if actual != set(expected) | {"MANIFEST.json"}:
        raise ValueError("whole package inventory mismatch")
    for name, row in expected.items():
        safe_member(name)
        data = read_file(root / name)
        if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError("package member changed")
    packet = json_bytes(read_file(root / "HOST_PACKAGE.json", MAX_JSON))
    if packet["apply_ready"] is not False or packet["host_apply"] != "NOT_AUTHORIZED" or packet["native_admission"] != "NOT_RUN":
        raise ValueError("package cannot confer host authority")
    return dict(state="FILES_MATCH_INTENT_ONLY", files=len(expected), host_apply="NOT_AUTHORIZED")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build_parser = sub.add_parser("build")
    build_parser.add_argument("--spec", type=Path, required=True)
    build_parser.add_argument("--output", type=Path, required=True)
    verify = sub.add_parser("check")
    verify.add_argument("package", type=Path)
    profile = sub.add_parser("validate-profile")
    profile.add_argument("--template", type=Path, required=True)
    profile.add_argument("--profile", type=Path, required=True)
    profile.add_argument("--session-id", required=True)
    plans = sub.add_parser("prepare-plans", help="pure schema/intent only; no issuer, SDK, grant or receipt")
    plans.add_argument("--template", type=Path, required=True)
    plans.add_argument("--profile", type=Path, required=True)
    plans.add_argument("--session-id", required=True)
    plans.add_argument("--security", type=Path, required=True)
    plans.add_argument("--group-id", required=True)
    plans.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "build":
        result = build(json_bytes(read_file(args.spec, MAX_JSON)), args.output)
        print(json.dumps({k: result[k] for k in ("schema", "state", "apply_ready", "host_apply", "source_commit", "bundle")}, indent=2))
    elif args.command == "check":
        print(json.dumps(check(args.package), indent=2))
    elif args.command == "validate-profile":
        raw = read_file(args.profile, MAX_JSON)
        result = validate_profile(json_bytes(read_file(args.template, MAX_JSON)), json_bytes(raw), args.session_id,
                                  source_sha256=hashlib.sha256(raw).hexdigest())
        print(json.dumps(result, indent=2))
    else:
        raw = read_file(args.profile, MAX_JSON)
        result = prepare_plans(json_bytes(read_file(args.template, MAX_JSON)), json_bytes(raw),
                               args.session_id, json_bytes(read_file(args.security, MAX_JSON)), args.group_id, args.output,
                               source_sha256=hashlib.sha256(raw).hexdigest())
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
