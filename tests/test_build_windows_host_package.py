"""Offline host-package controls; synthetic files are never native evidence."""
import copy
import hashlib
import io
import json
import ntpath
from pathlib import Path
import zipfile

import pytest

from tools import build_windows_host_package as tool
from comsol_mcp._execution_contract import ExecutionContractError


def synthetic_hostplan():
    """Minimal software data, not host observations, identities or authorization.

    Paths/names/GUIDs/hashes/budgets are invented. LocalSystem and the four
    privileges describe the input contract only; no service or right exists.
    No private host plan, license data, allocated SID or file ID is used.
    """
    root = r"C:\SoftwareFixture\HostPackage"
    targets = []
    for version in ("63", "64"):
        def guid(slot):
            return "{00000000-0000-0000-0000-%012d}" % (int(version) * 100 + slot)

        filters = [dict(filter_key=guid(index + 1), action="BLOCK", layer_key=layer,
                        direction=direction, address_family=family)
                   for index, (layer, direction, family, _) in enumerate(tool.LAYERS)]
        filters += [dict(filter_key=guid(index + 5), action="PERMIT", layer_key=layer[0],
                         direction=layer[1], address_family="IPV4")
                    for index, layer in enumerate((tool.LAYERS[0], tool.LAYERS[2], tool.LAYERS[2]))]
        workspace = ntpath.join(root, "project" + version)
        targets.append(dict(version=version, workspace=workspace,
                            proposed_ports=dict(server=16000 + int(version), worker_listener=17000 + int(version)),
                            filters=filters, appcontainer_name="software-only-" + version,
                            provider_key=guid(20), sublayer_key=guid(21), group_id=guid(22),
                            exact_rule_image_binding=dict(server_source_sha256="d" * 64),
                            installation_root=ntpath.join(root, "vendor" + version),
                            actual_version="SOFTWARE_ONLY_" + version,
                            control_home=ntpath.join(workspace, "control")))
    return dict(fixture_scope="SYNTHETIC_SOFTWARE_ONLY_NOT_AUTHORIZATION", apply_ready=False,
                authorized_to_apply=False, new_root=root, targets=targets,
                service=dict(name="SoftwareOnlyHostService", SCM_start_name="LocalSystem",
                             new_SAM_account_required=False, type="own", start="demand",
                             required_privileges_proposal=["SeTcbPrivilege", "SeAssignPrimaryTokenPrivilege",
                                                           "SeIncreaseQuotaPrivilege", "SeChangeNotifyPrivilege"]),
                normal_installation=dict(package=dict(sha256="e" * 64)),
                budget_proposal=dict(
                    job_limits=dict(schema="comsol-mcp.windows-job-limits/1",
                                    process_memory_limit_bytes=3 * 1024**3, job_memory_limit_bytes=4 * 1024**3,
                                    active_process_limit=6),
                    disk_admission_only=dict(reserve_free_bytes=1024**3, max_task_reservation_bytes=512 * 1024**2,
                                             quota_profile_id=None),
                    admission_only=dict(max_total_reserved_bytes=4 * 1024**3, reserve_available_bytes=256 * 1024**2,
                                        max_task_reservation_bytes=4 * 1024**3)),
                network=dict(license=dict(
                    observed_COMSOL_server_vendor_endpoints=[], external_license_allow_rules=[],
                    files=[dict(version=t["version"], path=ntpath.join(t["installation_root"], "license", "license.dat"),
                                sha256="f" * 64, allowlisted_directive_counts=dict(FEATURE=1, PACKAGE=0, INCREMENT=0),
                                count_kinds=dict(UNCOUNTED=1), vendor=["LMCOMSOL"]) for t in targets])),
                rollback_baselines=[], bounded_BFE_delegation_proposal=dict(
                    engine="SOFTWARE_ONLY_UNOBSERVED", containers=[], layers=[], ACE_flags=[],
                    new_object_DACLs={}, baseline_refs=[]))


def zip_bytes(members):
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return result.getvalue()


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    # Prevent accidental engine, host-query or shell execution in all builder tests.
    monkeypatch.setattr(tool.subprocess, "Popen", lambda *a, **k: pytest.fail("offline builder spawned a child"))
    monkeypatch.setattr(tool.subprocess, "run", lambda *a, **k: pytest.fail("offline builder ran a command"))
    base = tmp_path / "inputs"
    base.mkdir()
    host = synthetic_hostplan()
    source = base / "source"
    source.mkdir()
    names = ["_w26_os_resources.py", "_windows_host_change.py", "_windows_t037_bootstrap.py", "_windows_t037_broker.py",
             "_windows_service_sid.py", "_windows_appcontainer.py", "_windows_filter_group.py", "_windows_t037_network.py", "_windows_t037.py"]
    members = {"comsol_mcp/" + n: b"# SOFT_FILES_ONLY_NOT_NATIVE_OR_RUNTIME\n" for n in names}
    for name, data in members.items():
        p = source / name
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(data)
    snapshot = dict(commit="b" * 40, files=[dict(path=n, bytes=len(b), sha256=hashlib.sha256(b).hexdigest()) for n, b in members.items()])
    app = zip_bytes(members)
    dep = zip_bytes({"demo.py": b"# synthetic dependency"})
    wheel_rows = [dict(file=n, bytes=len(data), sha256=hashlib.sha256(data).hexdigest()) for n, data in [
        ("comsol_mcp-0.1.10-py3-none-any.whl", app), ("demo-1-py3-none-any.whl", dep)]]
    archive = zip_bytes({"PACKAGE_MANIFEST.json": tool.canonical(dict(target="win_amd64", wheels=wheel_rows)),
                        "locks/requirements.lock": "\n".join(r["file"] + " --hash=sha256:" + r["sha256"] for r in wheel_rows).encode(),
                        **{"wheelhouse/" + r["file"]: data for r, data in zip(wheel_rows, [app, dep])}})
    python = zip_bytes({"tools/python.exe": b"SOFTWARE_ONLY_NOT_A_PE_IMAGE"})
    host["normal_installation"]["package"]["sha256"] = hashlib.sha256(python).hexdigest()
    values = dict(source_manifest=snapshot, host_plan=host,
                  network_observation=dict(state="OBSERVED_READONLY_NOT_NATIVE_PROOF", host_settings_changes=0, netsh_query_count=1,
                                           observation={"source_port_range": [1024, 60000]}),
                  offline_bundle=archive, python_runtime=python,
                  windows_materials=dict(jdk_root=r"C:\SoftwareFixture\jdk11", host_changes=0,
                                         jdk_files=[dict(path="bin/java.exe", bytes=5, sha256="a" * 64)]))
    spec = dict(schema=tool.SPEC_SCHEMA, source_root=str(source))
    for key, value in values.items():
        p = base / (key + ".data")
        p.write_bytes(value if isinstance(value, bytes) else tool.canonical(value))
        spec[key] = tool.file_ref(p)
    return spec, base


def edit_input(spec, key, change):
    path = Path(spec[key]["path"])
    data = tool.json_bytes(path.read_bytes())
    change(data)
    path.write_bytes(tool.canonical(data))
    spec[key] = tool.file_ref(path)


def test_builder_creates_complete_checked_package_without_authority(inputs, tmp_path):
    spec, base = inputs
    result = tool.build(spec, tmp_path / "packet")
    assert result["apply_ready"] is False and result["host_apply"] == "NOT_AUTHORIZED"
    assert result["native_admission"] == "NOT_RUN" and result["new_local_SAM_accounts"] == 0
    assert result["bundle"]["wheel_count"] == 2  # Synthetic test fixture only.
    assert len(result["targets"]) == 2 and len(result["steps"]) == 8
    assert result["service_configuration"]["service_sid"] is None
    assert result["service_configuration"]["required_privileges_proposal"] == [
        "SeTcbPrivilege", "SeAssignPrimaryTokenPrivilege", "SeIncreaseQuotaPrivilege", "SeChangeNotifyPrivilege"]
    assert [a["action"] for a in result["install_commands"] if "action" in a] == ["ExtractOfficialPython", "ExtractOfflineBundle", "CopyJDK"]
    assert [r["argv"][1] for r in result["steps"] if r["resource"].startswith("SCM-")] == ["create", "config"]
    for version in ("63", "64"):
        profile = tool.json_bytes((tmp_path / "packet/profiles" / (version + ".template.json")).read_bytes())
        host = profile["host_resource_profile"]
        assert host["project_id"] is None and host["root"]["file_id"] is None
        policy = host["windows_isolation"]
        assert policy["appcontainer_sid"] is None and policy["service_sid"] is None
        assert len(policy["wfp_filter_keys"]) == 7 and len(policy["wfp_grants"]) == 3
        assert all(g["address_family"] == "IPV4" for g in policy["wfp_grants"])
        assert policy["wfp_grants"][0]["local_port_range"] == [1024, 60000]
        assert policy["wfp_grants"][1]["remote_port_range"] == [1024, 60000]
        assert policy["wfp_grants"][0]["remote_ports"] == policy["wfp_grants"][1]["local_ports"]
        provider = tool.json_bytes((tmp_path / "packet/profiles" / ("provider" + version + ".json")).read_bytes())
        assert provider["grants"] == [] and provider["destinations"] == []
        bootstrap = tool.json_bytes((tmp_path / "packet/profiles" / ("bootstrap" + version + ".template.json")).read_bytes())
        assert bootstrap["authorization_ref"] is bootstrap["plan"] is bootstrap["receipt"] is None
        disk = tool.json_bytes((tmp_path / "packet/profiles" / ("disk" + version + ".template.json")).read_bytes())
        assert disk["profiles"][0]["storage_id"] is None
        assert disk["profiles"][0]["disk_budget"]["mode"] == "ADMISSION_ONLY"
        assert "--disk-resource-config" in next(t for t in result["targets"] if t["version"] == version)["service_argv"]
    assert tool.check(tmp_path / "packet")["state"] == "FILES_MATCH_INTENT_ONLY"


def test_no_overwrite_and_no_unrelated_file_changes(inputs, tmp_path):
    spec, _ = inputs
    target = tmp_path / "packet"
    tool.build(spec, target)
    before = {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    with pytest.raises(FileExistsError):
        tool.build(spec, target)
    assert before == {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob("*") if p.is_file()}


def test_reparse_output_or_input_is_rejected(inputs, tmp_path):
    spec, base = inputs
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="reparse"):
        tool.build(spec, link / "packet")
    pointed = base / "pointed"
    pointed.symlink_to(Path(spec["python_runtime"]["path"]))
    spec["python_runtime"]["path"] = str(pointed)
    with pytest.raises(ValueError, match="reparse"):
        tool.build(spec, tmp_path / "packet")


@pytest.mark.parametrize("changed", ["source", "archive", "host-reference", "extra-spec", "fake-authorized", "no-range", "range-bool", "range-full", "ports-zero", "ports-conflict"])
def test_changed_or_unbounded_inputs_fail_before_output(inputs, tmp_path, changed):
    spec, _ = inputs
    if changed == "source":
        (Path(spec["source_root"]) / "comsol_mcp/_windows_t037.py").write_text("changed")
    elif changed == "archive":
        Path(spec["offline_bundle"]["path"]).write_bytes(b"changed")
    elif changed == "host-reference":
        spec["host_plan"]["sha256"] = "e" * 64
    elif changed == "extra-spec":
        spec["authorization_ref"] = "not-authorization"
    elif changed == "fake-authorized":
        edit_input(spec, "host_plan", lambda j: j.update(authorized_to_apply=True))
    elif changed == "no-range":
        edit_input(spec, "network_observation", lambda j: j["observation"].pop("source_port_range"))
    elif changed in ("range-bool", "range-full"):
        edit_input(spec, "network_observation", lambda j: j["observation"].update(source_port_range=[True, 60000] if changed == "range-bool" else [1, 65535]))
    else:
        edit_input(spec, "host_plan", lambda j: j["targets"][0]["proposed_ports"].update(worker_listener=0 if changed == "ports-zero" else j["targets"][0]["proposed_ports"]["server"]))
    with pytest.raises((ValueError, KeyError)):
        tool.build(spec, tmp_path / "packet")


@pytest.mark.parametrize("name", ["../outside", "/outside", "C:/outside", "a\\outside", "NUL", "a/CON.txt", "a/evil ", "a/evil."])
def test_archives_cannot_escape_or_use_windows_ambiguous_names(name):
    with pytest.raises(ValueError):
        tool.archive_members(zip_bytes({name: b"x"}))


def test_archive_case_collision_and_link_are_rejected():
    with pytest.raises(ValueError):
        tool.archive_members(zip_bytes({"a": b"x", "A": b"y"}))
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as z:
        info = zipfile.ZipInfo("link")
        info.external_attr = 0o120777 << 16
        z.writestr(info, "outside")
    with pytest.raises(ValueError):
        tool.archive_members(output.getvalue())


def test_whole_package_detects_missing_extra_or_changed_members(inputs, tmp_path):
    spec, _ = inputs
    target = tmp_path / "packet"
    tool.build(spec, target)
    p = target / "profiles/provider63.json"
    before = p.read_bytes()
    p.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        tool.check(target)
    p.write_bytes(before)
    extra = target / "extra"
    extra.write_bytes(b"x")
    with pytest.raises(ValueError, match="inventory"):
        tool.check(target)
    extra.unlink()
    p.unlink()
    with pytest.raises(ValueError, match="inventory"):
        tool.check(target)


def test_license_sign_and_other_private_fields_are_never_copied(inputs, tmp_path):
    spec, _ = inputs
    def add(j):
        license = j["network"]["license"]
        license["files"][0].update(SIGN="PRIVATE_MARKER", raw_line="PRIVATE_MARKER")
        license["files"][0]["allowlisted_directive_counts"]["SIGN"] = "PRIVATE_MARKER"
        license["scope"] = "PRIVATE_MARKER"
    edit_input(spec, "host_plan", add)
    result = tool.build(spec, tmp_path / "packet")
    assert "PRIVATE_MARKER" not in json.dumps(result)
    assert "SIGN" not in json.dumps(result["license_metadata"])


def complete_software_profile(template, session="software-session"):
    """Fill schema slots with named synthetic metadata; never trusted/native proof."""
    d = copy.deepcopy(template)
    host = d["host_resource_profile"]
    host.update(project_id="software-project", host_id="software-host", interactive_account_id="S-1-5-21-2000")
    host["root"].update(volume_id="software-volume", file_id="software-file")
    policy = host["windows_isolation"]
    policy.update(service_sid="S-1-5-80-100-101-102-103-104", appcontainer_sid="S-1-15-2-100-101-102-103-104-105-106", host_fingerprint_sha256="a" * 64)
    key = hashlib.sha256((host["project_id"] + "\0" + session).encode()).hexdigest()
    home = ntpath.join(host["root"]["path"], "state", "sessions", key)
    server = ntpath.join(home, "owned-server", "installation", "bin", "win64", "comsolmphserver.exe")
    host["launch_images"][1]["path"] = server
    for image in host["launch_images"]:
        image["argv_sha256"] = "b" * 64
    paths = [host["root"]["path"], ntpath.join(host["root"]["path"], "data"), ntpath.dirname(ntpath.dirname(host["launch_images"][2]["path"])),
             ntpath.dirname(server), ntpath.join(home, "worker"), ntpath.join(home, "owned-server", "runtime")]
    for acl, grant, path in zip(policy["acl_grants"], host["file_grants"], paths):
        acl.update(path=path, principal_sid=policy["appcontainer_sid"], security_descriptor_sha256="c" * 64)
        grant["path"] = path
    for grant in policy["wfp_grants"]:
        grant["principal_sid"] = policy["appcontainer_sid"]
        if grant["role"] == "server":
            grant["application_path"] = server
    return d


def template_from_packet(inputs, tmp_path):
    spec, _ = inputs
    tool.build(spec, tmp_path / "packet")
    return tool.json_bytes((tmp_path / "packet/profiles/63.template.json").read_bytes())


def test_unresolved_template_refuses_but_bound_schema_is_only_intent(inputs, tmp_path):
    template = template_from_packet(inputs, tmp_path)
    with pytest.raises(ValueError, match="unresolved"):
        tool.validate_profile(template, template, "software-session")
    document = complete_software_profile(template)
    result = tool.validate_profile(template, document, "software-session")
    assert result["state"] == "SCHEMA_VALID_INTENT_ONLY" and result["trusted_startup"] is False
    assert result["native_admission"] == "NOT_RUN" and result["network_intent"]["verification_state"] == "NOT_VERIFIED"


def test_cli_source_binding_hashes_actual_pretty_file_bytes_not_reserialization(inputs, tmp_path, capsys):
    template = template_from_packet(inputs, tmp_path)
    source = tmp_path / "actual-profile.json"
    source.write_text(json.dumps(complete_software_profile(template), indent=3) + "\n")
    tool.main(["validate-profile", "--template", str(tmp_path / "packet/profiles/63.template.json"),
               "--profile", str(source), "--session-id", "software-session"])
    result = json.loads(capsys.readouterr().out)
    assert result["network_intent"]["profile_source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result["source_binding"] == "EXACT_INPUT_FILE_BYTES_NOT_TRUSTED_PATH"
    assert result["trusted_startup"] is False


@pytest.mark.parametrize("fault", ["extra", "root", "service-account", "service-app-swap", "image", "port", "range", "broad", "wrong-session", "wrong-acl", "bad-sid", "quota"])
def test_profile_binding_refuses_scope_changes_and_role_confusion(inputs, tmp_path, fault):
    template = template_from_packet(inputs, tmp_path)
    d = complete_software_profile(template)
    host = d["host_resource_profile"]
    policy = host["windows_isolation"]
    if fault == "extra":
        d["verified"] = True
    elif fault == "root":
        host["root"]["path"] = r"C:\Windows"
    elif fault == "service-account":
        host["service_account_id"] = "S-1-5-19"
    elif fault == "service-app-swap":
        policy["service_sid"], policy["appcontainer_sid"] = policy["appcontainer_sid"], policy["service_sid"]
    elif fault == "image":
        host["launch_images"][2]["path"] = r"C:\untrusted\java.exe"
    elif fault == "port":
        policy["wfp_grants"][0]["remote_ports"] = [0]
    elif fault == "range":
        policy["wfp_grants"][0]["local_port_range"] = [1024, 60001]
    elif fault == "broad":
        policy["wfp_grants"][0]["remote_addresses"] = ["0.0.0.0/0"]
    elif fault == "wrong-session":
        host["launch_images"][1]["path"] = ntpath.join(host["root"]["path"], "unrelated.exe")
        policy["wfp_grants"][1]["application_path"] = host["launch_images"][1]["path"]
    elif fault == "wrong-acl":
        policy["acl_grants"][-1]["path"] = ntpath.join(host["root"]["path"], "another-project")
        host["file_grants"][-1]["path"] = policy["acl_grants"][-1]["path"]
    elif fault == "quota":
        host["disk_budget"]["quota_profile_id"] = "unrequested-quota"
    else:
        policy["service_sid"] = "S-1-5-4294967296"
    with pytest.raises((ValueError, ExecutionContractError)):
        tool.validate_profile(template, d, "software-session")


def test_existing_group_builder_and_normalizer_keep_four_defaults_three_permits(inputs, tmp_path):
    template = template_from_packet(inputs, tmp_path)
    d = complete_software_profile(template)
    security = {kind: dict(security_sddl="D:P(A;;GA;;;SY)", security_sha256=letter * 64)
                for kind, letter in [("provider", "a"), ("sublayer", "b")]}
    result = tool.wfp_plan(d, security, "{00000000-0000-0000-0000-000000009999}")
    payload = json.loads(result["payload_json"])
    assert payload["plan_status"] == "PLAN_ONLY_UNVERIFIED"
    actions = payload["change_manifest"]
    assert len(actions) == 9
    filters = [a["desired"] for a in actions if a["resource_kind"] == "wfp_filter"]
    assert sum(f["action"] == "BLOCK" for f in filters) == 4
    assert sum(f["action"] == "PERMIT" for f in filters) == 3
    assert {f["address_family"] for f in filters if f["action"] == "PERMIT"} == {"IPV4"}
    assert payload["native_baseline_status"] == "NOT_CAPTURED" and all(a["operation"] == "CREATE" for a in actions)


def test_plan_compiler_uses_original_leaf_contracts_and_leaves_authorization_empty(inputs, tmp_path):
    template = template_from_packet(inputs, tmp_path)
    d = complete_software_profile(template)
    security = {kind: dict(security_sddl="D:P(A;;GA;;;SY)", security_sha256=letter * 64)
                for kind, letter in [("provider", "a"), ("sublayer", "b")]}
    target = tmp_path / "plans"
    result = tool.prepare_plans(template, d, "software-session", security, "{00000000-0000-0000-0000-000000009999}", target)
    assert result["native_admission"] == "NOT_RUN"
    status = tool.json_bytes((target / "STATUS.json").read_bytes())
    assert status["authorization_ref"] is status["receipt"] is None and status["host_apply"] == "NOT_AUTHORIZED"
    app = tool.json_bytes((target / "appcontainer.plan.json").read_bytes())
    actions = json.loads(app["payload_json"])["change_manifest"]
    assert len(actions) == 1 and actions[0]["resource_kind"] == "appcontainer_profile"
    scm = tool.json_bytes((target / "service-sid-update.template.json").read_bytes())
    assert scm["operation"] == "UPDATE" and scm["baseline_identity"] is scm["backup_sha256"] is None
    with pytest.raises(FileExistsError):
        tool.prepare_plans(template, d, "software-session", security, "{00000000-0000-0000-0000-000000009999}", target)


def test_operator_has_no_install_apply_or_arbitrary_execution_mode(inputs, tmp_path):
    template_from_packet(inputs, tmp_path)
    text = (tmp_path / "packet/operator.ps1").read_text()
    assert "'Inspect','InstallPlan','ApplyPlan','RestorePlan','Verify'" in text
    assert all(name not in text for name in ["Invoke-Expression", "Start-Process", "Set-Acl", "Start-Service", "netsh set"])


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}'])
def test_closed_json_rejects_duplicate_and_nonfinite_inputs(raw):
    with pytest.raises(ValueError):
        tool.json_bytes(raw)
