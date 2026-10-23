"""SOFTWARE_ONLY synthetic installation, not a host plan or native authority."""
import copy
import ctypes as C
from dataclasses import replace
import json
import ntpath
import os
from pathlib import PureWindowsPath
import socket
import struct
import subprocess
from types import SimpleNamespace as NS

import pytest

from comsol_mcp import _windows_host_bootstrap as b
from comsol_mcp import _windows_scm_config_materialization as m
from comsol_mcp import _windows_scm_config_native as n
from comsol_mcp import _windows_t037_targets as targets
from comsol_mcp._execution_contract import ExecutionContractError
from tests.test_windows_host_bootstrap import FakeWindows, intent_doc, raw_sd, PREFIX, SERVICE_SID
from tests.test_windows_network_intent import network_profile_and_plan
from tests.test_windows_t037_targets import wfp_sd

HOST = {'hostname': 'SOFTWARE-fixture', 'machine_fingerprint_sha256': 'e' * 64}


@pytest.fixture(autouse=True)
def no_native(monkeypatch):
    def refused(*args, **kwargs):
        pytest.fail('SOFTWARE_ONLY: actual SDK/process/network prohibited')
    monkeypatch.setattr(C, 'WinDLL', refused, raising=False)
    monkeypatch.setattr(subprocess, 'Popen', refused)
    monkeypatch.setattr(subprocess, 'run', refused)
    monkeypatch.setattr(socket, 'create_connection', refused)
    monkeypatch.setattr(b, '_retained', [])
    monkeypatch.setattr(n, 'host_domains', lambda: dict(HOST))


def recipe_doc():
    doc, source = intent_doc()
    doc['schema'] = b.SCHEMA_V2
    argv = b.service_argv(PREFIX, doc['service_name'], version=2)
    doc['service_argv'], doc['quoted_command_line'] = list(argv), subprocess.list2cmdline(argv)
    host, _ = network_profile_and_plan()
    images = {i.role: i for i in host.launch_images}
    paths = {'launcher': argv[0], 'worker': PREFIX+r'\jdk11\bin\java.exe', 'server': r'C:\COMSOL\bin\comsol.exe'}
    server_bytes = b'EXPLICIT SOFTWARE_ONLY external COMSOL image fixture'
    hashes = {'launcher': b.digest(source['python312/tools/python.exe']),
              'worker': b.digest(source['jdk11/bin/java.exe']), 'server': b.digest(server_bytes)}
    project = PREFIX + r'\project'
    policy = replace(host.windows_isolation, service_name=doc['service_name'], service_start_name='LocalSystem',
        service_logon_sid=b.SY, host_fingerprint_sha256=HOST['machine_fingerprint_sha256'],
        acl_grants=(replace(host.windows_isolation.acl_grants[0], path=project,
                           principal_sid=host.windows_isolation.appcontainer_sid),),
        wfp_grants=tuple(replace(g, application_path=paths[g.role]) for g in host.windows_isolation.wfp_grants))
    host = replace(host, host_id=HOST['hostname'], launcher_account_id=b.SY, service_account_id=b.SY,
        root=replace(host.root, path=project, volume_id='1', file_id='2', owner_id=b.SY),
        allowed_operations=('model.save',),
        launch_images=tuple(replace(images[r], path=paths[r], image_sha256=hashes[r],
            argv_sha256=b.digest(b.canonical(list(argv))) if r == 'launcher' else images[r].argv_sha256)
            for r in ('launcher', 'server', 'worker')),
        file_grants=(replace(host.file_grants[0], path=project, rights=('READ',)),), windows_isolation=policy)
    label = dict(rid=0x2000, mask=1, flags=0)
    files = []
    for path, role in ((r'C:\COMSOL', 'installation_rx'), (PREFIX+r'\python312', 'code_rx'),
            (PREFIX+r'\profiles', 'config_rx'), (PREFIX+r'\jdk11', 'runtime_rx'),
            (project, 'project_root'), (project+r'\data', 'data_leaf'), (project+r'\temp', 'temp_leaf')):
        files.append(dict(path=path, kind='directory', roles=[role],
            descriptor_hex=m._slot({'config_rx':'private_directory_descriptor_hex',
                'project_root':'root_holder_descriptor_hex'}.get(role,'directory_descriptor_hex')) if path.startswith(PREFIX) else raw_sd(True).hex(),
            mandatory_label=dict(label), content_sha256=None))
    for image in host.launch_images:
        files.append(dict(path=image.path, kind='file', roles=[image.role+'_image'],
            descriptor_hex=m._slot('file_descriptor_hex') if image.path.startswith(PREFIX) else raw_sd().hex(),
            mandatory_label=dict(label), content_sha256=image.image_sha256))
    own_policy = [('Provider', policy.wfp_provider_key), ('SubLayer', policy.wfp_sublayer_key)]
    own_policy += [('Filter', key) for key in policy.wfp_filter_keys]
    trusted = dict(schema='comsol-mcp.windows-t037-profile/4', host_resource_profile=host.as_dict(),
        job_limits=dict(schema='comsol-mcp.windows-job-limits/1', process_memory_limit_bytes=4096,
                        job_memory_limit_bytes=16384, active_process_limit=4),
        finite_targets=dict(schema=targets.SCHEMA, filesystem_security=targets.FS_SECURITY,
            policy_security=targets.WFP_SECURITY, files=files,
            policy=[dict(kind=k, key=v, descriptor_hex=wfp_sd(policy.service_sid).hex()) for k,v in own_policy]))
    trusted['host_resource_profile']['windows_isolation']['service_sid'] = m._slot('service_sid')
    trusted['host_resource_profile']['root'] = m._slot('project_root')
    memory = dict(schema='comsol-mcp.memory-resource-profile/v1', profile_id='memory-fixture',
        host_id=HOST['machine_fingerprint_sha256'], platform='windows',
        budget=dict(max_total_reserved_bytes=4096, reserve_available_bytes=1024, max_task_reservation_bytes=2048),
        allowed_operations=['model.save'], trusted_estimates=[dict(operation_id='model.save', estimate_bytes=1024)])
    disk = dict(schema='comsol-mcp.disk-resource-config/v1', host_id=HOST['hostname'], platform='windows',
        profiles=[dict(profile_id='disk-fixture', project_id=host.project_id, root=m._slot('project_root'),
            storage_id=m._slot('storage_id'), disk_budget=host.disk_budget.as_dict(),
            allowed_operations=['model.save'], trusted_estimates=[dict(operation_id='model.save', estimate_bytes=100)])])
    provider = dict(schema='comsol-mcp.host-scoped-provider-config/1', destinations=[], grants=[])
    doc['provisioning'] = dict(schema=m.SCHEMA, host=dict(HOST), project_root=dict(kind='NEW_DIRECTORY',relative='project'),
        owned_directories=['project', 'project/data', 'project/temp'], labels=dict(file=dict(label), directory=dict(label)),
        state_policy=dict(service_sid=m._slot('service_sid'),service_access_mask=0x1301BF,client=None),
        templates=dict(zip(m.CONFIG_PATHS, (trusted, provider, memory, disk))))
    return doc, source, server_bytes


class ConfigWindows(FakeWindows):
    """Original Bootstrap/_Call/_Handle with only the actual SDK edges replaced."""
    def __init__(self, source, server_bytes):
        super().__init__(source)
        self.sdk.update(GetKernelObjectSecurity=self._label_read, SetSecurityInfo=self._security,
            WriteFile=self._write_config, FlushFileBuffers=self._flush, CreateFileW=self._create_or_open)
        self.token_user = b.SY
        self.write_counts = []
        self.add(r'C:\COMSOL', True)
        self.add(r'C:\COMSOL\bin', True)
        self.add(r'C:\COMSOL\bin\comsol.exe', False, server_bytes)
        for p in (r'C:\COMSOL', r'C:\COMSOL\bin\comsol.exe'):
            self.tree[p]['label'] = n.label_bytes(dict(rid=0x2000,mask=1,flags=0))
        self.original = copy.deepcopy(self.tree)

    def context(self):
        return {**super().context(), 'token_user_sid': self.token_user}

    def _create_or_open(self, path, access, share, security, creation, flags, template):
        if creation == 1:
            self.calls.append(('CREATE_NEW', path))
            if path in self.tree:
                self.error = 183
                return C.c_void_p(-1).value
            sa = C.cast(security, C.POINTER(b._SecurityAttributes)).contents
            head = C.string_at(sa.descriptor, 20)
            *_, dacl = struct.unpack('<BBHIIII', head)
            extent = dacl + struct.unpack_from('<H', C.string_at(sa.descriptor+dacl, 8), 2)[0]
            self.add(path, False, sd=C.string_at(sa.descriptor, extent))
        return super()._open_node(path, access, share, security, 3, flags, template)

    def _label_read(self, arg, flags, buffer, size, needed):
        assert flags == 0x10
        path = self.target(arg)
        self.calls.append(('LABEL_READ', path))
        raw = self.tree[path].get('label', struct.pack('<BBHIIII',1,0,0x8000,0,0,0,0))
        C.memmove(buffer, raw, len(raw))
        C.cast(needed, C.POINTER(b.DWORD))[0] = len(raw)
        return self.fault('LABEL_READ', 1)

    def _security(self, *args):
        if len(args) != 7:
            return super()._file_sd(*args)
        arg, kind, flags, owner, group, dacl, sacl = args
        path = self.target(arg)
        if flags==0x80000007:
            assert kind==1 and sacl is None
            base=owner.value-20
            header=struct.unpack('<BBHIIII',C.string_at(base,20))
            assert tuple(p.value for p in (owner,group,dacl))==tuple(base+v for v in (header[3],header[4],header[6]))
            size=struct.unpack_from('<H',C.string_at(dacl,8),2)[0]
            self.tree[path]['sd']=C.string_at(base,dacl.value-base+size)
            self.calls.append(('DACL_SET',path))
            return self.fault('DACL_SET',0)
        assert (kind, flags, owner, group, dacl) == (1, 0x10, None, None, None)
        self.calls.append(('LABEL_SET', path))
        size = struct.unpack_from('<H', C.string_at(sacl,8),2)[0]
        self.tree[path]['label'] = struct.pack('<BBHIIII',1,0,0x8010,0,0,20,0)+C.string_at(sacl,size)
        return self.fault('LABEL_SET', 0)

    def _write_config(self, *args):
        if len(args) == 2:
            return super()._write(*args)
        arg, pointer, requested, output, overlapped = args
        self.calls.append(('CONFIG_WRITE', self.target(arg)))
        count = self.write_counts.pop(0) if self.write_counts else requested
        C.cast(output, C.POINTER(b.DWORD))[0] = count
        if 0 < count <= requested:
            self.tree[self.target(arg)]['data'] += C.string_at(pointer, count)
        return self.fault('CONFIG_WRITE', 1)

    def _flush(self, arg):
        self.calls.append(('CONFIG_FLUSH', self.target(arg)))
        return self.fault('CONFIG_FLUSH', 1)


def fixture(*, actor=b.SY, prepared=True, existing_root=False):
    doc, source, server = recipe_doc()
    backend = ConfigWindows(source, server)
    backend.token_user = actor
    if existing_root:
        root=r'C:\Protected\existing-project'
        for path in (root,root+r'\data',root+r'\temp'):
            backend.add(path,True)['label']=n.label_bytes(dict(rid=0x2000,mask=1,flags=0))
        backend.original=copy.deepcopy(backend.tree)
        def relocate(value):
            if type(value) is str and value.startswith(PREFIX+r'\project'):
                return root+value[len(PREFIX+r'\project'):]
            if type(value) is dict:return {k:relocate(v) for k,v in value.items()}
            if type(value) is list:return [relocate(v) for v in value]
            return value
        doc=relocate(doc);identity=backend.tree[root]['identity']
        recipe=doc['provisioning']
        recipe['project_root']=dict(kind='EXISTING_DIRECTORY',identity=dict(path=root,
            volume_id=str(identity[0]),file_id=str(identity[1]),owner_id=b.SY))
        recipe['owned_directories']=[]
        for target in recipe['templates'][m.CONFIG_PATHS[0]]['finite_targets']['files']:
            if target['path'].startswith(root):target['descriptor_hex']=raw_sd(True).hex()
    original = b.Bootstrap(b.parse_plan(b.canonical(doc)), backend, seal=b._TEST_SEAL)
    if prepared:
        original.prepare()
    return original, backend, doc


def installed(*, actor=b.SY, existing_root=False):
    original, backend, doc = fixture(actor=actor, existing_root=existing_root)
    result = original.apply(approved_plan_sha256=original.intent.sha256)
    assert result['runtime_qualification'] == 'NOT_QUALIFIED'
    return original, backend, doc


def test_original_full_static_path_four_bytes_argv_pins_and_compared_restore():
    original, backend, doc = installed()
    final = original._configuration.resolved
    root = original.nodes['project'].image.identity
    assert final.service_sid == SERVICE_SID and final.root_identity == root
    assert set(original.nodes) == original._configuration.expected_namespace
    for path, raw in final.files:
        assert backend.tree[original.intent.destination(path)]['data'] == raw
        assert original.nodes[path].image.sha256 == b.digest(raw)
        assert '$bootstrap' not in raw.decode()
    trusted = json.loads(final.files[0][1])
    assert trusted['host_resource_profile']['root']['file_id'] == str(root[1])
    assert trusted['host_resource_profile']['windows_isolation']['service_sid'] == SERVICE_SID
    assert backend.service['binary'] == original.intent.command_line
    assert all(original.intent.argv.count(flag) == 1 for flag in ('--windows-t037-profile', '--host-provider-config',
                                                                '--memory-resource-config', '--disk-resource-config'))
    original.handoff_runtime()
    assert all(n.handle.closed and n.bridge_handle.closed and not n.runtime_handle.closed for n in original.nodes.values())
    result = original.restore()
    assert result['state'] == 'RESTORED'
    assert backend.tree == backend.original and backend.service is None


def test_v2_raw_approval_and_existing_installer_context_roundtrip_are_exact():
    from comsol_mcp import _windows_installer_context as context
    doc,_,_=recipe_doc()
    raw=b.canonical(doc)
    intent=b.parse_plan(raw)
    assert intent.sha256==b.digest(raw)
    context._intent(intent,raw)  # unchanged original exact-type/reparse gate
    pretty=json.dumps(doc,indent=2).encode()
    other=b.parse_plan(pretty)
    assert other.sha256!=intent.sha256 and other.provisioning==intent.provisioning
    with pytest.raises(context.ContextError):context._intent(intent,pretty)


def test_all_four_flags_quote_spaces_and_cannot_accept_a_shell_style_command():
    doc,_,_=recipe_doc();prefix=r'C:\Protected Area\ComsolMCP_fixture'
    def rep(value):
        if type(value) is str:return value.replace(PREFIX,prefix)
        if type(value) is dict:return {k:rep(v) for k,v in value.items()}
        if type(value) is list:return [rep(v) for v in value]
        return value
    doc=rep(doc);argv=b.service_argv(prefix,doc['service_name'],version=2)
    doc['service_argv']=list(argv);doc['quoted_command_line']=subprocess.list2cmdline(argv)
    images=doc['provisioning']['templates'][m.CONFIG_PATHS[0]]['host_resource_profile']['launch_images']
    next(row for row in images if row['role']=='launcher')['argv_sha256']=b.digest(b.canonical(list(argv)))
    intent=b.parse_plan(b.canonical(doc))
    assert intent.command_line.startswith('"C:\\Protected Area\\')
    assert intent.argv==argv
    doc['quoted_command_line']=' '.join(argv)
    with pytest.raises(b.BootstrapError):b.parse_plan(b.canonical(doc))


@pytest.mark.parametrize('bad',['port_zero','conflicting_flow','destination_range'])
def test_network_template_failure_is_before_any_compile_or_mutation(bad):
    doc,_,_=recipe_doc()
    grants=doc['provisioning']['templates'][m.CONFIG_PATHS[0]]['host_resource_profile']['windows_isolation']['wfp_grants']
    if bad=='port_zero':grants[0]['remote_ports']=[0]
    elif bad=='conflicting_flow':grants[1]['local_ports']=[32005]  # synthetic conflict, not a product port
    else:
        grants[0]['remote_ports']=[];grants[0]['remote_port_range']=[10000,11000]
    with pytest.raises(b.BootstrapError):b.parse_plan(b.canonical(doc))


@pytest.mark.parametrize('bad',[b'{"schema":1,"schema":2}',b'{"schema":NaN}',b'[]'])
def test_duplicate_nonfinite_and_nonobject_input_never_reaches_sdk(bad):
    with pytest.raises(b.BootstrapError):b.parse_plan(bad)


def test_capable_admin_static_writer_does_not_become_system_loader():
    original, backend, _ = installed(actor='S-1-5-21-100-200-300-1001')
    assert original._configuration.actor[0] == 'CAPABLE_ELEVATED_ADMIN'
    assert all(b.decode_sd(n.image.raw_sd)['owner'] == b.SY for n in original.nodes.values())
    original.handoff_runtime()
    before = list(backend.calls)
    with pytest.raises(b.BootstrapError, match='Admin static writing') as exc:
        original.load_configurations()
    assert exc.value.code == 'SYSTEM_LOADER_CONTEXT_REQUIRED'
    assert backend.calls == before and not original._configuration.loaded
    original.restore()


def test_approved_state_service_modify_and_actual_admin_client_are_finite_not_inherited():
    doc,source,server=recipe_doc()
    client='S-1-5-21-100-200-300-1001'
    doc['provisioning']['state_policy']['client']=dict(sid=client,directory_access_mask=0x1200A0)
    backend=ConfigWindows(source,server);backend.token_user=client
    original=b.Bootstrap(b.parse_plan(b.canonical(doc)),backend,seal=b._TEST_SEAL).prepare()
    result=original.apply(approved_plan_sha256=original.intent.sha256)
    assert result['service_state_write_acl']=='STATIC_BOUND_SERVICE_DATA_POLICY_NOT_NATIVE_VERIFIED'
    state=b.decode_sd(original.nodes['service-state'].image.raw_sd)
    assert state['owner']==b.SY and state['control']&0x1000
    assert state['aces']==((0,3,0x1F01FF,b.SY),(0,3,0x1F01FF,b.BA),
                           (0,3,0x1301BF,SERVICE_SID),(0,0,0x1200A0,client))
    assert not 0x1301BF & (0x40000|0x80000|0x1000000|0x10000000)
    for relative,node in original.nodes.items():
        if relative=='service-state':continue
        private_source = relative in ('profiles','project',*m.CONFIG_PATHS)
        b.check_security(node.image.raw_sd,directory=node.image.directory,service_sid=None if private_source else SERVICE_SID)
        assert all(row[3]!=client for row in b.decode_sd(node.image.raw_sd)['aces'])
    assert all(not key.startswith('service-state/') for key in original.nodes)  # no endpoint/token/publisher
    original.handoff_runtime();original.restore()


@pytest.mark.parametrize('wrong',['foreign_user','world','appcontainer','service','client_write','service_dac','service_owner','service_all','service_system_security','missing'])
def test_state_policy_never_mints_client_or_broad_service_permission(wrong):
    doc,source,server=recipe_doc();policy=doc['provisioning']['state_policy']
    if wrong=='foreign_user':policy['client']=dict(sid='S-1-5-21-1-2-3-1001',directory_access_mask=0x1200A0)
    elif wrong in ('world','appcontainer','service'):
        sid={'world':'S-1-1-0','appcontainer':'S-1-15-2-1','service':SERVICE_SID}[wrong]
        policy['client']=dict(sid=sid,directory_access_mask=0x1200A0)
    elif wrong=='client_write':policy['client']=dict(sid='S-1-5-21-100-200-300-1001',directory_access_mask=0x1200A2)
    elif wrong=='missing':del policy['service_access_mask']
    else:policy['service_access_mask']|={'service_dac':0x40000,'service_owner':0x80000,'service_all':0x10000000,'service_system_security':0x1000000}[wrong]
    backend=ConfigWindows(source,server)
    with pytest.raises(b.BootstrapError):
        original=b.Bootstrap(b.parse_plan(b.canonical(doc)),backend,seal=b._TEST_SEAL)
        original.prepare()
    assert backend.tree==backend.original and backend.service is None
    assert not any(c[0] in ('CreateServiceW','CreateDirectoryW','LABEL_SET','DACL_SET') for c in backend.calls)


@pytest.mark.parametrize('wrong_identity',[False,True])
def test_existing_project_root_is_only_observed_and_full_identity_mismatch_blocks(wrong_identity):
    doc,source,server=recipe_doc();backend=ConfigWindows(source,server)
    external=r'C:\Protected\existing-project'
    for path in (external,external+r'\data',external+r'\temp'):
        row=backend.add(path,True);row['label']=n.label_bytes(dict(rid=0x2000,mask=1,flags=0))
    backend.original=copy.deepcopy(backend.tree)
    def change(value):
        if type(value) is str and value.startswith(PREFIX+r'\project'):
            return external+value[len(PREFIX+r'\project'):]
        if type(value) is dict:return {k:change(v) for k,v in value.items()}
        if type(value) is list:return [change(v) for v in value]
        return value
    doc=change(doc);recipe=doc['provisioning'];root=backend.tree[external]['identity']
    recipe['project_root']=dict(kind='EXISTING_DIRECTORY',identity=dict(path=external,volume_id=str(root[0]),
        file_id=str(root[1]+(1 if wrong_identity else 0)),owner_id=b.SY))
    recipe['owned_directories']=[]
    for target in recipe['templates'][m.CONFIG_PATHS[0]]['finite_targets']['files']:
        if target['path'].startswith(external):target['descriptor_hex']=raw_sd(True).hex()
    original=b.Bootstrap(b.parse_plan(b.canonical(doc)),backend,seal=b._TEST_SEAL)
    if wrong_identity:
        with pytest.raises(b.BootstrapError) as exc:original.prepare()
        assert exc.value.code=='CONFIG_ROOT' and backend.tree==backend.original
        original.abandon_readonly()
        return
    original.prepare();original.apply(approved_plan_sha256=original.intent.sha256)
    assert original._configuration.resolved.root_identity==root
    assert all(not p.startswith('project') for p in original.nodes)
    assert all(not c[1].startswith(external) for c in backend.calls if c[0] in ('LABEL_SET','DACL_SET'))
    original.handoff_runtime();original.restore()
    assert backend.tree==backend.original


def test_copied_owner_config_or_dict_cannot_dispatch_a_new_effect():
    original,backend,_=fixture()
    before=list(backend.calls)
    clone=copy.copy(original)
    with pytest.raises(b.BootstrapError):clone.apply(approved_plan_sha256=original.intent.sha256)
    original._configuration=copy.copy(original._configuration)
    with pytest.raises(b.BootstrapError):original.apply(approved_plan_sha256=original.intent.sha256)
    callback=[]
    fake=NS(_guard=lambda *a:callback.append('unsafe'))
    for function in (m.apply_configuration,m.load_configurations,m.acknowledge_configuration):
        with pytest.raises(b.BootstrapError):function(fake)
    assert not callback and backend.calls==before


@pytest.mark.parametrize('change',['actual_actor','cached_context'])
def test_original_actor_facts_cannot_be_changed_or_replaced_between_prepare_and_effect(change):
    original,backend,_=fixture(actor='S-1-5-21-100-200-300-1001')
    before=copy.deepcopy(backend.tree)
    if change=='actual_actor':backend.token_user='S-1-5-21-100-200-300-1002'
    else:original._configuration.initial_context['token_user_sid']='S-1-5-21-100-200-300-1002'
    with pytest.raises(b.BootstrapError):original.apply(approved_plan_sha256=original.intent.sha256)
    assert backend.tree==before and backend.service is None
    assert not any(c[0] in ('CreateDirectoryW','CreateServiceW','LABEL_SET','DACL_SET') for c in backend.calls)


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'host_domain', 'root_literal', 'unknown_slot', 'slot_callback',
    'budget', 'estimate_missing', 'estimate_bool', 'argv', 'duplicate_flag', 'image', 'label_absent', 'label_flags',
    'label_rid', 'provider_action', 'provider_extra', 'disk_storage', 'directory_alias', 'directory_missing', 'source_hash'])
def test_invalid_approved_inputs_reject_before_any_sdk(mutation):
    doc, _, _ = recipe_doc()
    r = doc['provisioning']; t = r['templates']; h=t[m.CONFIG_PATHS[0]]['host_resource_profile']
    if mutation == 'missing': del t[m.CONFIG_PATHS[2]]
    elif mutation == 'extra': r['arbitrary'] = True
    elif mutation == 'host_domain': t[m.CONFIG_PATHS[2]]['host_id'] = HOST['hostname']
    elif mutation == 'root_literal': h['root'] = dict(path=PREFIX+r'\project', volume_id='55',file_id='22',owner_id=b.SY)
    elif mutation == 'unknown_slot': h['root'] = m._slot('caller_root')
    elif mutation == 'slot_callback': h['root'] = {m.SLOT:'project_root', 'callback':'unsafe'}
    elif mutation == 'budget': del t[m.CONFIG_PATHS[2]]['budget']['reserve_available_bytes']
    elif mutation == 'estimate_missing': t[m.CONFIG_PATHS[2]]['trusted_estimates'] = []
    elif mutation == 'estimate_bool': t[m.CONFIG_PATHS[2]]['trusted_estimates'][0]['estimate_bytes'] = True
    elif mutation == 'argv': doc['service_argv'][0] = 'python.exe'
    elif mutation == 'duplicate_flag': doc['service_argv'] += ['--home','C:\\unapproved']
    elif mutation == 'image': h['launch_images'][0]['image_sha256'] = '0'*64
    elif mutation == 'label_absent': del r['labels']['file']['rid']
    elif mutation == 'label_flags': r['labels']['directory']['flags'] = 8
    elif mutation == 'label_rid': r['labels']['directory']['rid'] = 0x1000
    elif mutation == 'provider_action': t[m.CONFIG_PATHS[1]]['grants'] = [dict(project_id='x',action='session.stop', authorization_ref_sha256='1'*64,scope_sha256='2'*64,expires_at_utc='2026-01-01T00:00:00Z')]
    elif mutation == 'provider_extra': t[m.CONFIG_PATHS[1]]['trusted'] = True
    elif mutation == 'disk_storage': t[m.CONFIG_PATHS[3]]['profiles'][0]['storage_id'] = 'windows-volume:55'
    elif mutation == 'directory_alias': r['owned_directories'].append('project/DATA')
    elif mutation == 'directory_missing': r['owned_directories'].remove('project')
    elif mutation == 'source_hash': t[m.CONFIG_PATHS[0]]['source_sha256'] = 'a'*64
    with pytest.raises(b.BootstrapError):
        b.parse_plan(b.canonical(doc))


@pytest.mark.parametrize('drift', ['extra', 'label', 'content', 'identity', 'reparse', 'link', 'dacl'])
def test_restore_preserves_owned_and_extra_nodes_on_drift(drift):
    original, backend, _ = installed()
    path = original.intent.destination(m.CONFIG_PATHS[0]); row=backend.tree[path]
    if drift == 'extra': backend.add(PREFIX+r'\profiles\unapproved.json',False,b'foreign')
    elif drift == 'label': row['label'] = n.label_bytes(dict(rid=0x3000,mask=1,flags=0))
    elif drift == 'content': row['data'] += b' '
    elif drift == 'identity': row['identity'] = (55,999)
    elif drift == 'reparse': row['reparse'] = True
    elif drift == 'link': row['links'] = 2
    elif drift == 'dacl': row['sd'] = raw_sd(broad=True)
    before = copy.deepcopy(backend.tree)
    with pytest.raises(b.BootstrapError): original.restore()
    assert backend.tree == before and backend.service is not None
    assert not any(c[0] in ('DeleteService','SetFileInformationByHandle') for c in backend.calls)


@pytest.mark.parametrize('fault', ['raise', 'invalid'])
def test_setter_opaque_is_once_retained_no_apply_restore_replay(fault):
    original, backend, _ = fixture()
    backend.faults['LABEL_SET'] = fault
    with pytest.raises(b.BootstrapError) as exc: original.apply(approved_plan_sha256=original.intent.sha256)
    assert exc.value.owner is original and original.state == 'UNKNOWN'
    calls = list(backend.calls)
    for operation in (lambda: original.apply(approved_plan_sha256=original.intent.sha256), original.restore,
                      original.acknowledge_configuration):
        with pytest.raises(b.BootstrapError): operation()
    assert backend.calls == calls and sum(c[0]=='LABEL_SET' for c in calls)==1
    assert original._configuration.native.label_calls[0].call.state == 'UNKNOWN'


@pytest.mark.parametrize('counts', [[1,2,3], [0], [2**32-1]])
def test_bounded_write_progress_and_invalid_progress_retention(counts):
    original, backend, _ = fixture()
    backend.write_counts = list(counts)
    if counts[0] == 1:
        original.apply(approved_plan_sha256=original.intent.sha256)
        assert original.nodes[m.CONFIG_PATHS[0]].image.sha256 == b.digest(original._configuration.resolved.files[0][1])
        original.restore()
    else:
        with pytest.raises(b.BootstrapError): original.apply(approved_plan_sha256=original.intent.sha256)
        assert original.state == 'UNKNOWN' and original.nodes[m.CONFIG_PATHS[0]].handle is not None
        assert len([c for c in backend.calls if c[0]=='CONFIG_WRITE']) == 1


def test_original_cached_success_ack_is_metadata_only_then_compared_restore(monkeypatch):
    original, backend, _ = fixture()
    checkpoint = original.owner.checkpoint
    def lose_ack(event, record):
        if event == 'CONFIG_NODE_POSTIMAGE': raise RuntimeError('synthetic lost metadata ack')
        checkpoint(event,record)
    monkeypatch.setattr(original.owner,'checkpoint',lose_ack)
    with pytest.raises(b.BootstrapError): original.apply(approved_plan_sha256=original.intent.sha256)
    assert original.state == 'CONFIGURATION_ACK_PENDING'
    before = list(backend.calls)
    original.acknowledge_configuration()
    assert backend.calls == before and original.state == 'PARTIAL_KNOWN'
    original.restore()
    assert backend.tree == backend.original


@pytest.mark.parametrize('mutation', ['raw', 'typed_recipe', 'owner', 'hash'])
def test_original_plan_and_approval_cannot_be_substituted(mutation):
    original, backend, _ = fixture()
    before=list(backend.calls)
    if mutation == 'raw': object.__setattr__(original.intent,'original_raw',original.intent.original_raw+b' ')
    elif mutation == 'typed_recipe': object.__setattr__(original.intent,'provisioning',b'{}')
    elif mutation == 'owner': original.owner=b._Owner(backend)
    approval = 'a'*64 if mutation=='hash' else original.intent.sha256
    with pytest.raises(b.BootstrapError): original.apply(approved_plan_sha256=approval)
    assert backend.calls==before


def _normal_loader_environment(monkeypatch, tmp_path, *, existing_root=True):
    """Real normal loaders/guards/owners/parsers; synthetic SDK exports only.

    Provider stdio uses a real disposable fd, with explicit path/borrowed-HANDLE
    translation at the OS seam. No raw-reader, attestation, source-chain, trust
    predicate, typed loader result or owner is manufactured by this helper.
    """
    from comsol_mcp import _windows_t037 as win, _windows_parameter_publish as publish
    from comsol_mcp import _windows_t037_mic as mic, _host_scoped_provider as provider
    from comsol_mcp import _w26_memory_resources as memory, _w26_os_resources as resources
    from comsol_mcp import _windows_disk_resources as disk
    from tests.test_windows_t037_targets import PathSdk
    from tests.test_windows_host_preflight_owner import Function
    from ctypes import wintypes
    import sys
    original,backend,_=installed(existing_root=existing_root)
    original.handoff_runtime()
    document=json.loads(original._configuration.resolved.files[0][1])
    by_key={ntpath.normcase(p):(p,row) for p,row in backend.tree.items()}
    class LoaderSdk(PathSdk):
        def __init__(self):
            super().__init__(document,{p:r['data'] for p,r in backend.tree.items() if not r['directory']})
            self.keep=[];self.access=[];self.serial=90000;self.private_reads=[];self.borrowed={}
            self.k.GetCurrentProcess=Function(lambda:-1)
            self.k.GetVolumePathNameW=Function(self.volume_path)
            self.k.GetVolumeInformationW=Function(self.volume_info)
            self.k.GetDiskFreeSpaceExW=Function(self.capacity)
            for name,fn in (('OpenProcessToken',self.token),('GetTokenInformation',self.token_info),
                    ('ConvertStringSidToSidW',self.convert),('IsValidSid',lambda p:1),
                    ('GetLengthSid',lambda p:len(self.sid_bytes(p))),
                    ('EqualSid',lambda p,q:int(self.sid_bytes(p)==self.sid_bytes(q))),
                    ('GetSecurityDescriptorDacl',self.dacl),('GetAclInformation',self.acl_info),('GetAce',self.ace)):
                setattr(self.a,name,Function(fn))

        def row(self,handle):
            path=self.handles[getattr(handle,'value',handle)];row=by_key[path][1]
            return path,dict(kind='directory' if row['directory'] else 'file',descriptor_hex=row['sd'].hex()),16 if row['directory'] else 128

        def identity(self,path):return by_key[path][1]['identity'][1]

        def root(self,path,access,*args):
            self.access.append((str(path),access))
            return super().root(path,access,*args)

        def relative(self,output,access,attributes,io_status,allocation,attrs,share,disposition,options,ea,size):
            value=C.cast(attributes,C.POINTER(publish._ObjectAttributes)).contents
            name=value.ObjectName.contents
            path=ntpath.join(self.handles[value.RootDirectory],C.wstring_at(name.Buffer,name.Length//2))
            self.access.append((path,access))
            assert disposition==1 and not allocation and not ea and size==0 and options&0x200000
            assert not access & (0x40000000|0x10000000|0x1000000|0xD0156)  # no generic write/all, security, DELETE or file mutation
            C.cast(output,C.POINTER(C.c_void_p))[0]=self.alloc_handle(path)
            C.cast(io_status,C.POINTER(publish._IoStatusBlock)).contents.Information=1
            return 0

        def file(self,handle,output):
            super().file(handle,output)
            row=by_key[self.handles[getattr(handle,'value',handle)]][1]
            info=C.cast(output,C.POINTER(mic._FileInfo)).contents
            info.volume=row['identity'][0]&0xffffffff
            info.id_low=row['identity'][1]&0xffffffff;info.id_high=row['identity'][1]>>32
            return 1

        def full(self,handle,kind,output,size):
            super().full(handle,kind,output,size)
            if kind==18:
                row=by_key[self.handles[getattr(handle,'value',handle)]][1]
                info=C.cast(output,C.POINTER(publish._FullIdInfo)).contents
                info.volume=row['identity'][0];info.file_id[:]=row['identity'][1].to_bytes(16,'little')
            return 1

        def security(self,handle,kind,flags,owner,group,dacl,sacl,output):
            path=self.handles[getattr(handle,'value',handle)];row=by_key[path][1]
            assert kind==1 and flags in (1,5,7,0x10)  # no audit SACL or setter
            raw=row.get('label',struct.pack('<BBHIIII',1,0,0x8000,0,0,0,0)) if flags==0x10 else row['sd']
            if flags in (1,5):
                raw=bytearray(raw);struct.pack_into('<I',raw,8,0);raw=bytes(raw)
            buffer=C.create_string_buffer(raw);pointer=C.addressof(buffer);self.buffers[pointer]=(buffer,raw)
            C.cast(output,C.POINTER(C.c_void_p))[0]=pointer
            for out,offset in ((owner,4),(group,8),(dacl,16),(sacl,12)):
                if out is not None:
                    relative=struct.unpack_from('<I',raw,offset)[0]
                    C.cast(out,C.POINTER(C.c_void_p))[0]=pointer+relative if relative else None
            self.private_reads.append((path,flags,b.digest(raw)))
            return 0

        @staticmethod
        def sid_bytes(pointer):
            header=C.string_at(pointer,8)
            assert header[0]==1 and header[1]<=15
            return C.string_at(pointer,8+4*header[1])

        def token(self,process,access,out):
            assert access==8
            self.serial+=1;C.cast(out,C.POINTER(C.c_void_p))[0]=self.serial
            return 1

        def token_info(self,token,kind,buffer,size,needed):
            assert kind==1
            C.cast(needed,C.POINTER(C.c_uint32))[0]=32
            if buffer is None:return 0
            C.cast(buffer,C.POINTER(C.c_void_p))[0]=C.addressof(buffer)+16
            value=b._sid_bytes(backend.token_user);C.memmove(C.addressof(buffer)+16,value,len(value))
            return 1

        def convert(self,text,out):
            raw=b._sid_bytes(text);buffer=C.create_string_buffer(raw);self.keep.append(buffer)
            C.cast(out,C.POINTER(C.c_void_p))[0]=C.addressof(buffer)
            return 1

        def volume_path(self,path,out,size):out.value='C:\\';return 1

        def volume_info(self,path,name,count,serial,maximum,flags,filesystem,size):
            C.cast(serial,C.POINTER(C.c_uint32))[0]=by_key[ntpath.normcase('C:\\')][1]['identity'][0]
            C.cast(flags,C.POINTER(C.c_uint32))[0]=8;filesystem.value='NTFS'
            return 1

        def capacity(self,path,available,total,free):
            assert path==json.loads(original._configuration.resolved.files[3][1])['profiles'][0]['root']['path']
            for out,value in ((available,1024**3),(total,4*1024**3),(free,2*1024**3)):
                C.cast(out,C.POINTER(C.c_uint64))[0]=value
            return 1

        def dacl(self,pointer,present,out,defaulted):
            raw=self.buffers[getattr(pointer,'value',pointer)][1]
            C.cast(present,C.POINTER(C.c_int32))[0]=1
            C.cast(defaulted,C.POINTER(C.c_int32))[0]=0
            C.cast(out,C.POINTER(C.c_void_p))[0]=getattr(pointer,'value',pointer)+struct.unpack_from('<I',raw,16)[0]
            return 1

        def sacl(self,pointer,present,out,defaulted):
            base=getattr(pointer,'value',pointer);raw=self.buffers[base][1]
            control=struct.unpack_from('<H',raw,2)[0];offset=struct.unpack_from('<I',raw,12)[0]
            C.cast(present,C.POINTER(C.c_int32))[0]=int(bool(control&0x10))
            C.cast(defaulted,C.POINTER(C.c_int32))[0]=int(bool(control&0x20))
            C.cast(out,C.POINTER(C.c_void_p))[0]=base+offset if offset else None
            return 1

        def acl_info(self,pointer,out,size,kind):
            raw=C.string_at(pointer,8);values=C.cast(out,C.POINTER(C.c_uint32))
            values[0]=struct.unpack_from('<H',raw,4)[0];values[1]=struct.unpack_from('<H',raw,2)[0];values[2]=0
            return 1

        def ace(self,pointer,index,out):
            cursor=getattr(pointer,'value',pointer)+8
            for _ in range(index):cursor+=struct.unpack_from('<H',C.string_at(cursor,4),2)[0]
            C.cast(out,C.POINTER(C.c_void_p))[0]=cursor
            return 1

    sdk=LoaderSdk()
    monkeypatch.setattr(win,'os',NS(**dict(vars(os),name='nt')))
    monkeypatch.setattr(win,'Path',PureWindowsPath)
    monkeypatch.setattr(win,'windows_host_fingerprint',lambda:HOST['machine_fingerprint_sha256'])
    monkeypatch.setattr(publish,'os',NS(**dict(vars(os),name='nt')))
    monkeypatch.setattr(mic,'os',NS(name='nt'))
    monkeypatch.setattr(wintypes,'DWORD',C.c_uint32)
    monkeypatch.setattr(wintypes,'BOOL',C.c_int32)
    monkeypatch.setattr(C,'WinDLL',lambda name,**kw:sdk.a if name=='advapi32' else sdk.nt if name=='ntdll' else sdk.k)
    monkeypatch.setattr(C,'get_last_error',lambda:0,raising=False)
    monkeypatch.setattr(C,'WinError',lambda code=0:OSError(code,'SOFTWARE SDK error'),raising=False)
    monkeypatch.setattr(targets,'_ORIGINS',{})
    monkeypatch.setattr(targets,'_READS',{})
    monkeypatch.setattr(targets,'_LOADER_APIS',{})
    monkeypatch.setattr(disk,'_pending',{})
    # Real Python stdio on disposable local files; Windows source names and
    # fds are translated only at the filesystem/export seams, never as proof.
    local=tmp_path/'profiles';local.mkdir(mode=0o700)
    mapping={original.intent.destination(p):local/ntpath.basename(p) for p in m.CONFIG_PATHS}
    mapping[original.intent.destination('profiles')]=local
    for path,raw in original._configuration.resolved.files:
        output=mapping[original.intent.destination(path)];output.write_bytes(raw);output.chmod(0o600)
    inverse={str(v):k for k,v in mapping.items()}
    safe=provider._safe_absolute;verify=provider._verify_windows_private_path
    def safe_local(value,field):
        return safe(os.fspath(mapping.get(str(value),value)),field)
    def private_path(value,**options):
        return verify(PureWindowsPath(inverse.get(str(value),str(value))),**options)
    def borrowed(fd):
        stat=os.fstat(fd)
        matches=[key for key,value in mapping.items() if (value.stat().st_dev,value.stat().st_ino)==(stat.st_dev,stat.st_ino)]
        assert len(matches)==1
        key=matches[0]
        if key not in sdk.borrowed:sdk.borrowed[key]=sdk.alloc_handle(key)
        return sdk.borrowed[key]
    monkeypatch.setitem(sys.modules,'msvcrt',NS(get_osfhandle=borrowed))
    monkeypatch.setattr(provider,'_safe_absolute',safe_local)
    monkeypatch.setattr(provider,'_verify_windows_private_path',private_path)
    monkeypatch.setattr(provider,'os',NS(**dict(vars(os),name='nt')))
    monkeypatch.setattr(memory,'os',NS(**dict(vars(os),name='nt',path=ntpath)))
    monkeypatch.setattr(memory,'Path',PureWindowsPath)
    monkeypatch.setattr(memory,'_current_platform',lambda:'windows')
    monkeypatch.setattr(memory,'_current_host_fingerprint',lambda platform:HOST['machine_fingerprint_sha256'])
    monkeypatch.setattr(disk,'os',NS(**dict(vars(os),name='nt')))
    monkeypatch.setattr(resources,'os',NS(**dict(vars(os),name='nt')))
    monkeypatch.setattr(disk,'_windows_host',lambda:HOST['hostname'])
    return original,backend,sdk,(win.load_trusted_profile,provider.HostScopedProvider.load,
        memory.TrustedMemoryResourceConfig.load,resources.TrustedDiskResourceConfig.load)


@pytest.mark.parametrize('existing_root',[False,True],ids=['new-root','existing-root'])
def test_full_four_normal_loaders_with_real_source_chain_and_readonly_rights(existing_root,monkeypatch,tmp_path,record_testsuite_property):
    from comsol_mcp import _windows_t037 as win, _host_scoped_provider as provider
    from comsol_mcp import _w26_memory_resources as memory, _w26_os_resources as resources
    from comsol_mcp import _windows_disk_resources as disk
    original,backend,sdk,_=_normal_loader_environment(monkeypatch,tmp_path,existing_root=existing_root)
    loaded=original.load_configurations()
    assert type(loaded[0]) is win.WindowsT037ProfileV2 and targets._ORIGINS[id(loaded[0])][0]() is loaded[0]
    assert type(loaded[1]) is provider.HostScopedProvider and loaded[1]._fd>=0
    assert type(loaded[2]) is memory.TrustedMemoryResourceConfig and loaded[2].trusted_startup_source
    assert type(loaded[3]) is resources.TrustedDiskResourceConfig and disk.is_windows_disk_config(loaded[3])
    assert len(original._configuration.loader_calls)==4 and all(c[3]=='RETURNED' for c in original._configuration.loader_calls)
    assert not targets._READS and not targets._LOADER_APIS and not disk._pending
    for relative in ('profiles',*m.CONFIG_PATHS):
        raw=original.nodes[relative].image.raw_sd
        b.check_security(raw,directory=relative=='profiles')
        assert {a[3] for a in b.decode_sd(raw)['aces']}=={b.SY,b.BA}
    readonly={0x20000,0x1200A0,0x120080,0x120081,0x100081}
    assert sdk.access and {mask for _,mask in sdk.access}<=readonly
    assert all(not mask & (0x40000000|0x10000000|0x1000000|0xD0156) for _,mask in sdk.access)
    assert all(any(p==ntpath.normcase(original.intent.destination(relative)) for p,flags,_ in sdk.private_reads)
               for relative in m.CONFIG_PATHS)
    source=provider.verify_private_path_security(original.intent.destination(m.CONFIG_PATHS[0]),directory=False,
        expected_identity=loaded[0].source_identity)
    assert source['identity']==loaded[0].source_identity and source['path_chain']
    if not existing_root:
        root=original.nodes['project']
        b.check_security(root.image.raw_sd,directory=True)
        target=next(t for t in loaded[0].finite_targets.files if t.roles==('project_root',))
        assert bytes.fromhex(target.descriptor_hex)==root.image.raw_sd
        assert all(original.nodes[leaf].image.raw_sd==backend.sd(True,service_sid=SERVICE_SID)
            for leaf in ('project/data','project/temp'))  # unchanged leaf template, no root-wide grant rewrite
    suffix='existing_root' if existing_root else 'new_root'
    record_testsuite_property('software_four_loader_readback_'+suffix,json.dumps(dict(
        root_mode=original._configuration.recipe['project_root']['kind'],
        kinds=[type(obj).__module__+'.'+type(obj).__qualname__ for obj in loaded],
        files=[dict(path=path,sha256=b.digest(raw)) for path,raw in original._configuration.resolved.files],
        source_identities=[getattr(loaded[0],'source_identity'),loaded[1]._config_identity,
                           loaded[2].source_identity],
        requested_access=sorted({mask for _,mask in sdk.access}),
        source_readback_count=len(sdk.private_reads),scope='SOFTWARE_ONLY_NOT_NATIVE'),sort_keys=True))
    record_testsuite_property('software_original_source_chain_'+suffix,json.dumps(source,sort_keys=True))
    loaded[1].close()
    original.restore()
    assert backend.tree==backend.original


def test_normal_system_loader_preserves_ed09_service_rx_guard_refusal(monkeypatch,tmp_path):
    """Old RX source remains a real normal-loader negative, never accepted intent."""
    original,backend,sdk,loaders=_normal_loader_environment(monkeypatch,tmp_path)
    target=original.intent.destination(m.CONFIG_PATHS[0])
    backend.tree[target]['sd']=backend.sd(False,service_sid=SERVICE_SID)
    before=copy.deepcopy(backend.tree)
    with pytest.raises(ExecutionContractError,match='untrusted SID'):
        loaders[0](target)
    assert not targets._LOADER_APIS and not targets._ORIGINS
    assert backend.tree==before


@pytest.mark.parametrize('index',range(4))
@pytest.mark.parametrize('fault',['service_rx','inherited_rx','wrong_owner',
    'foreign_reader','world_reader','appcontainer_reader','state_modify'])
def test_all_four_normal_loaders_preserve_private_owner_and_sid_guards(index,fault,monkeypatch,tmp_path):
    original,backend,sdk,loaders=_normal_loader_environment(monkeypatch,tmp_path)
    directory=False
    relative=m.CONFIG_PATHS[index]
    row=backend.tree[original.intent.destination(relative)]
    if fault=='wrong_owner':
        row['sd']=raw_sd(directory,owner=b.BA)
    elif fault=='state_modify':
        row['sd']=original._configuration.state_descriptor
    else:
        sid={'foreign_reader':'S-1-5-21-7-8-9-1001','world_reader':'S-1-1-0',
             'appcontainer_reader':'S-1-15-2-77'}.get(fault,SERVICE_SID)
        binary=b._sid_bytes(sid);flags=16 if fault=='inherited_rx' else 3 if directory else 0
        ace=struct.pack('<BBHI',0,flags,8+len(binary),0x1200A9)+binary
        offset=b.decode_sd(row['sd'])['offsets'][2];raw=bytearray(row['sd'])
        struct.pack_into('<H',raw,offset+2,struct.unpack_from('<H',raw,offset+2)[0]+len(ace))
        struct.pack_into('<H',raw,offset+4,struct.unpack_from('<H',raw,offset+4)[0]+1)
        row['sd']=bytes(raw)+ace
    before=copy.deepcopy(backend.tree)
    with pytest.raises(ExecutionContractError) as error:
        loaders[index](original.intent.destination(m.CONFIG_PATHS[index]))
    assert error.value.code in {'HOST_PROVIDER_CONFIG_INVALID','MEMORY_PROFILE_SECURITY_UNVERIFIED'}
    assert backend.tree==before and not original._configuration.loaded
    assert not targets._ORIGINS and not targets._LOADER_APIS and not targets._READS


@pytest.mark.parametrize('fault',['service_rx','wrong_owner'])
def test_final_private_directory_guard_and_original_pin_refuse_directory_drift(fault,monkeypatch,tmp_path):
    from comsol_mcp import _host_scoped_provider as provider
    original,backend,sdk,_=_normal_loader_environment(monkeypatch,tmp_path)
    target=original.intent.destination('profiles')
    pin=original.nodes['profiles'].runtime_handle
    backend.tree[target]['sd']=raw_sd(True,**({'service_sid':SERVICE_SID} if fault=='service_rx' else {'owner':b.BA}))
    with pytest.raises(ExecutionContractError):provider.verify_private_path_security(target,directory=True)
    with pytest.raises(b.BootstrapError) as error:original.load_configurations()
    assert error.value.code=='TREE_DRIFT' and original.state=='RUNTIME_READ_PINNED_START_BLOCKED'
    assert original.nodes['profiles'].runtime_handle is pin and not pin.closed
    assert not original._configuration.loader_calls and not original._configuration.loaded


@pytest.mark.parametrize('change',['absent','low','policy','inherit_only'])
def test_real_t037_source_origin_requires_exact_explicit_mic(change,monkeypatch,tmp_path):
    original,backend,sdk,loaders=_normal_loader_environment(monkeypatch,tmp_path)
    row=backend.tree[original.intent.destination(m.CONFIG_PATHS[0])]
    if change=='absent':row['label']=struct.pack('<BBHIIII',1,0,0x8000,0,0,0,0)
    else:
        raw=bytearray(row['label'])
        if change=='low':struct.pack_into('<I',raw,len(raw)-4,0x1000)
        elif change=='policy':struct.pack_into('<I',raw,32,2)
        else:raw[29]=8
        row['label']=bytes(raw)
    with pytest.raises(ExecutionContractError):loaders[0](original.intent.destination(m.CONFIG_PATHS[0]))
    assert not targets._ORIGINS and not targets._LOADER_APIS


@pytest.mark.parametrize('swap',['legacy_recipe','source_code_slot','source_state_path','source_root_overlap'])
def test_private_source_classification_is_explicit_in_new_approved_raw_recipe(swap):
    doc,_,_=recipe_doc();recipe=doc['provisioning']
    rows=recipe['templates'][m.CONFIG_PATHS[0]]['finite_targets']['files']
    config=next(row for row in rows if row['roles']==['config_rx'])
    if swap=='legacy_recipe':recipe['schema']='comsol-mcp.windows-scm-config-recipe/1'
    elif swap=='source_code_slot':config['descriptor_hex']=m._slot('directory_descriptor_hex')
    elif swap=='source_state_path':
        config['path']=PREFIX+r'\service-state';config['descriptor_hex']=m._slot('directory_descriptor_hex')
    else:recipe['project_root']['relative']='profiles'
    with pytest.raises(b.BootstrapError):b.parse_plan(b.canonical(doc))


def test_unchanged_new_root_service_rx_remains_a_concrete_disk_loader_conflict(monkeypatch,tmp_path):
    """Inject the prior RX descriptor; the real disk guard still refuses it."""
    original,backend,sdk,loaders=_normal_loader_environment(monkeypatch,tmp_path,existing_root=False)
    root=original.nodes['project']
    backend.tree[root.path]['sd']=backend.sd(True,service_sid=SERVICE_SID)
    before=copy.deepcopy(backend.tree)
    with pytest.raises(ExecutionContractError,match='untrusted SID'):loaders[3](original.intent.destination(m.CONFIG_PATHS[3]))
    assert not original._configuration.loaded and not root.runtime_handle.closed
    assert backend.tree==before  # readonly conflict, no replacement profile or host action


@pytest.mark.parametrize('conflict',['legacy_recipe2','generic_root_slot','root_data_role','root_code_role',
    'root_write_acl','root_write_file','private_source_slot'])
def test_new_root_holder_requires_explicit_slot_and_distinct_mutable_leaves(conflict):
    doc,source,server=recipe_doc();recipe=doc['provisioning']
    trusted=recipe['templates'][m.CONFIG_PATHS[0]]
    root=next(row for row in trusted['finite_targets']['files'] if row['roles']==['project_root'])
    if conflict=='legacy_recipe2':recipe['schema']='comsol-mcp.windows-scm-config-recipe/2'
    elif conflict=='generic_root_slot':root['descriptor_hex']=m._slot('directory_descriptor_hex')
    elif conflict=='private_source_slot':root['descriptor_hex']=m._slot('private_directory_descriptor_hex')
    elif conflict=='root_data_role':root['roles'].append('data_leaf')
    elif conflict=='root_code_role':root['roles']=['code_rx']
    elif conflict=='root_write_acl':trusted['host_resource_profile']['windows_isolation']['acl_grants'][0]['rights']=['READ','WRITE']
    else:trusted['host_resource_profile']['file_grants'][0]['rights']=['READ','WRITE']
    backend=ConfigWindows(source,server)
    with pytest.raises(b.BootstrapError):
        b.Bootstrap(b.parse_plan(b.canonical(doc)),backend,seal=b._TEST_SEAL).prepare()
    assert not backend.calls and backend.tree==backend.original and backend.service is None


def test_new_root_holder_is_the_exact_approved_relative_not_a_named_global_policy():
    doc,source,server=recipe_doc();old=PREFIX+r'\project';new=PREFIX+r'\workspace\holder'
    def move(value):
        if type(value) is str and value.startswith(old):return new+value[len(old):]
        if type(value) is dict:return {k:move(v) for k,v in value.items()}
        if type(value) is list:return [move(v) for v in value]
        return value
    doc=move(doc);recipe=doc['provisioning']
    recipe['project_root']['relative']='workspace/holder'
    recipe['owned_directories']=['workspace','workspace/holder','workspace/holder/data','workspace/holder/temp']
    backend=ConfigWindows(source,server)
    original=b.Bootstrap(b.parse_plan(b.canonical(doc)),backend,seal=b._TEST_SEAL).prepare()
    original.apply(approved_plan_sha256=original.intent.sha256)
    b.check_security(original.nodes['workspace/holder'].image.raw_sd,directory=True)
    for relative in ('workspace','workspace/holder/data','workspace/holder/temp','python312'):
        b.check_security(original.nodes[relative].image.raw_sd,directory=True,service_sid=SERVICE_SID)
    state=b.decode_sd(original.nodes['service-state'].image.raw_sd)
    assert (0,3,0x1301BF,SERVICE_SID) in state['aces']
    target=next(row for row in json.loads(original._configuration.resolved.files[0][1])['finite_targets']['files'] if row['roles']==['project_root'])
    assert target['path']==new and bytes.fromhex(target['descriptor_hex'])==original.nodes['workspace/holder'].image.raw_sd
    original.handoff_runtime();original.restore()
    assert backend.tree==backend.original


def test_new_root_wrong_owner_cannot_be_rebound_by_the_real_disk_loader(monkeypatch,tmp_path):
    original,backend,sdk,loaders=_normal_loader_environment(monkeypatch,tmp_path,existing_root=False)
    root=original.nodes['project'];backend.tree[root.path]['sd']=raw_sd(True,owner=b.BA)
    with pytest.raises(ExecutionContractError) as error:loaders[3](original.intent.destination(m.CONFIG_PATHS[3]))
    assert error.value.code=='DISK_ROOT_IDENTITY_MISMATCH'
    assert not original._configuration.loaded and not root.runtime_handle.closed
