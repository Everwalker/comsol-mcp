"""Prepared compiler/manifest + original factory consumer on inert SDK leaves.

Windows names are mapped only to this test's local scratch filesystem. No JDK,
COMSOL, Win32 DLL, subprocess, socket, live issuer or host policy is used.
"""
from contextlib import ExitStack
from dataclasses import replace
import copy
import ctypes as C
import hashlib
import json
import ntpath
import os
from pathlib import Path as HostPath, PureWindowsPath
import struct
import subprocess
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

from comsol_mcp import _java_worker as java, _session_server as server
from comsol_mcp import _windows_t037 as win, _windows_t037_targets as targets
from comsol_mcp import _windows_t037_native as native, _windows_t037_mic as mic
from comsol_mcp import _windows_parameter_publish as publish
from comsol_mcp import _windows_t037_launch_inputs as launch
from comsol_mcp import _windows_worker_bootstrap as prepared
from comsol_mcp import _windows_filter_group as group
from comsol_mcp import _windows_t037_child as _parent_query_source
_REAL_FINITE_POLICY_INIT=targets.FinitePolicyApi.__init__
_REAL_PARENT_QUERY_CLASS=_parent_query_source.NativeChildProcessApi
_REAL_HOST_FINGERPRINT=win.windows_host_fingerprint
_C_SOFTWARE_COMPUTER='SOFTWARE-fixture'
_C_SOFTWARE_MACHINE_GUID='public-source-fixture-machine-guid'
_C_SOFTWARE_MACHINE_SHA=hashlib.sha256(('COMSOL-MCP-WINDOWS-T037-V1\0'+_C_SOFTWARE_COMPUTER.casefold()+'\0'+_C_SOFTWARE_MACHINE_GUID).encode()).hexdigest()
from comsol_mcp import _control_daemon as control
from comsol_mcp._session_context import CanonicalSocket, SessionRuntimeConfig
from comsol_mcp._windows_owned_resources import registry_for
from tests.test_windows_prepared_launch import (v3_plan, _wire_compiler_sdk, WINROOT, SYSTEMROOT,
    SOURCE_ROOT, SERVER_SOURCE, CONFIG_SOURCE, VERSION_SOURCE, CLASSPATH_SOURCE)
from tests.test_windows_scm_config_materialization import ConfigWindows, recipe_doc, HOST
from comsol_mcp import _windows_host_bootstrap as bootstrap
from comsol_mcp import _windows_scm_config_materialization as configuration
from comsol_mcp._windows_scm_config_materialization import CONFIG_PATHS
from tests.test_windows_t037_targets import PathSdk, sd, label_descriptor, label_ace
from tests.test_windows_scm_prebirth import static_sdk, running_factory_chain
from tests import test_windows_scm_prebirth as scm_fixture
from tests.test_windows_network_intent import network_profile_and_plan, effective_group_fixture
from tests.test_windows_filter_group import GroupSDK
from tests.test_windows_suspended_ownership import _launch_api
from tests.test_windows_t037_connected_b import _QuerySDK
from comsol_mcp import _windows_t037_resume as _resume_source, _windows_t037_acl as _acl_source
_REAL_NATIVE_ISSUER = _resume_source.issue_native_resume
_REAL_NATIVE_RESUME = _resume_source.resume_native_child
_REAL_NATIVE_ACL = _acl_source.check_suspended_child_dacl
_REAL_WINDOWS_OBSERVER_INIT = win.WindowsT037Observer.__init__


def _normal_installer(monkeypatch, *, fixed_probe=False):
    """Bind the new finite fixture's declared Server image before plan freeze.

    The old parser tests intentionally inherit a different V2 Server source.
    A real loadable V3 lifecycle fixture must declare its actual V3 input image,
    not rewrite a completed manifest or bypass its profile/hash check.
    """
    monkeypatch.setattr(configuration.native,'host_domains',lambda:dict(HOST))
    document,sources=v3_plan()
    template=document['provisioning']['templates'][CONFIG_PATHS[0]]
    host=template['host_resource_profile'];images=host['launch_images']
    image=next(x for x in images if x['role']=='server');old=image['path']
    image['path']=ntpath.join(SOURCE_ROOT,'bin','win64','comsol.exe')
    image['image_sha256']=hashlib.sha256(SERVER_SOURCE).hexdigest()
    for grant in host['windows_isolation']['wfp_grants']:
        if grant['role']=='server':grant['application_path']=image['path']
    for grant in host['windows_isolation']['acl_grants']:
        grant['security_descriptor_sha256']=native.canonical_security_descriptor_sha256(configuration._planned_sd(True))
    for target in template['finite_targets']['files']:
        if target['path']==old:
            target['path']=image['path'];target['content_sha256']=image['image_sha256']
    _,_,legacy_server=recipe_doc();backend=ConfigWindows(sources,legacy_server)
    contents={'bin/win64/comsol.exe':SERVER_SOURCE,'bin/win64/server.xml':CONFIG_SOURCE,
        'bin/win64/version.txt':VERSION_SOURCE,'plugins/COMSOL.jar':CLASSPATH_SOURCE,
        'ext/graphicsmagick/win64/CORE_RL_magick_.dll':b'IMAGE DLL FIXTURE'}
    for relative,raw in contents.items():
        path=ntpath.join(SOURCE_ROOT,*relative.split('/'));parent=ntpath.dirname(path);pending=[]
        while parent!=SOURCE_ROOT and parent not in backend.tree:
            pending.append(parent);parent=ntpath.dirname(parent)
        for directory in reversed(pending):
            backend.add(directory,True)['label']=configuration.native.label_bytes(dict(rid=0x2000,mask=1,flags=0))
        backend.add(path,False,raw)['label']=configuration.native.label_bytes(dict(rid=0x2000,mask=1,flags=0))
    backend.original=copy.deepcopy(backend.tree)
    _wire_compiler_sdk(backend)
    if fixed_probe:
        wait=backend.sdk['WaitForSingleObject']
        def fixed_outputs(*args):
            result=wait(*args)
            package=ntpath.join(compiler_prefix,*prepared.OUTPUT_DIR.split('/'),'comsol_mcp','worker_java')
            path=ntpath.join(package,'PersistentComsolWorker$OsQualificationProbe.class')
            if path not in backend.tree:
                backend.add(path,False,b'SOFTWARE_ONLY inert fixed helper compiler output',sd=backend.tree[package]['sd'])
            return result
        compiler_prefix=document['prefix'] if 'prefix' in document else __import__('tests.test_windows_prepared_launch',fromlist=['PREFIX']).PREFIX
        backend.sdk['WaitForSingleObject']=fixed_outputs
    original=bootstrap.Bootstrap(bootstrap.parse_plan(bootstrap.canonical(document)),backend,seal=bootstrap._TEST_SEAL)
    original.prepare()
    return original,backend


def _windows_paths(root):
    """Actual pathlib checks run over ordinary private files under one root."""
    class WindowPath(HostPath, PureWindowsPath):
        _flavour = ntpath

        def __new__(cls,*args,**kwargs):
            if args and str(args[0]).startswith('/'):
                return HostPath(*args,**kwargs)
            return super().__new__(cls,*args,**kwargs)

        def _host(self):
            if not self.is_absolute() or self.drive.upper() != 'C:':
                raise AssertionError('only bounded fixture C: paths may reach scratch I/O')
            return root.joinpath(*self.parts[1:])

        def open(self, *args, **kwargs): return self._host().open(*args, **kwargs)
        def stat(self, *, follow_symlinks=True): return self._host().stat(follow_symlinks=follow_symlinks)
        def lstat(self): return self._host().lstat()
        def exists(self): return self._host().exists()
        def is_file(self): return self._host().is_file()
        def is_dir(self): return self._host().is_dir()
        def is_symlink(self): return self._host().is_symlink()
        def mkdir(self, mode=0o777, parents=False, exist_ok=False): return self._host().mkdir(mode=mode, parents=parents, exist_ok=exist_ok)
        def unlink(self, missing_ok=False): return self._host().unlink(missing_ok=missing_ok)
        def replace(self,target):self._host().replace(type(self)(target)._host());return type(self)(target)
        def rename(self,target):self._host().rename(type(self)(target)._host());return type(self)(target)
        def rmdir(self):return self._host().rmdir()
        def chmod(self,mode,*,follow_symlinks=True):return self._host().chmod(mode,follow_symlinks=follow_symlinks)
        def iterdir(self):
            for child in self._host().iterdir(): yield self / child.name
        def resolve(self, strict=False):
            host=self._host().resolve(strict=strict)
            if not host.is_relative_to(root.resolve()): raise AssertionError('fixture path escaped scratch')
            return type(self)('C:\\', *host.relative_to(root.resolve()).parts)
        def absolute(self): return self
        def expanduser(self): return self
    return WindowPath


class _InstalledPathSdk(PathSdk):
    """Filesystem/SD/MIC SDK values from the original installer-owned nodes."""
    def __init__(self, document, backend):
        self.nodes={ntpath.normcase(k):v for k,v in backend.tree.items()}
        super().__init__(document,{k:v['data'] for k,v in self.nodes.items() if not v['directory']})

    def row(self, handle):
        path=self.handles[getattr(handle,'value',handle)];node=self.nodes[path]
        return path,dict(kind='directory' if node['directory'] else 'file',descriptor_hex=node['sd'].hex()),0x10 if node['directory'] else 0x20

    def identity(self,path): return self.nodes[path]['identity'][1]

    def relative(self,output,access,attributes,io_status,allocation,attrs,share,disposition,options,ea,size):
        # The normal protected-manifest loader also performs a data-only open;
        # the old PathSdk fixture supports that form only for profile.json.
        value=C.cast(attributes,C.POINTER(publish._ObjectAttributes)).contents
        name=value.ObjectName.contents;leaf=C.wstring_at(name.Buffer,name.Length//2)
        path=ntpath.join(self.handles[value.RootDirectory],leaf)
        if leaf=='launch-manifest.json' and not access&0x20000:
            assert path in self.nodes and not self.nodes[path]['directory']
            assert (access,allocation,attrs,share,disposition,options,ea,size)==(0x100081,None,0x80,1,1,0x200060,None,0)
            C.cast(output,C.POINTER(C.c_void_p))[0]=self.alloc_handle(path)
            C.cast(io_status,C.POINTER(publish._IoStatusBlock)).contents.Information=1
            return 0
        return super().relative(output,access,attributes,io_status,allocation,attrs,share,disposition,options,ea,size)

    def file(self,handle,output):
        path,row,attrs=self.row(handle);node=self.nodes[path]
        out=C.cast(output,C.POINTER(mic._FileInfo)).contents
        volume,inode=node['identity'];out.attributes=attrs;out.volume=volume&0xffffffff
        out.id_low=inode&0xffffffff;out.id_high=(inode>>32)&0xffffffff
        out.creation=100;out.write=300;out.size_low=len(node['data']);out.links=node['links'];return 1

    def full(self,handle,kind,output,size):
        path,row,attrs=self.row(handle);node=self.nodes[path];volume,inode=node['identity']
        if kind==18:
            value=C.cast(output,C.POINTER(publish._FullIdInfo)).contents
            value.volume=volume;value.file_id[:]=inode.to_bytes(16,'little')
        elif kind==0:
            value=C.cast(output,C.POINTER(publish._BasicInfo)).contents
            value.created=100;value.changed=200;value.written=300;value.attributes=attrs
        elif kind==1:
            value=C.cast(output,C.POINTER(publish._StandardInfo)).contents
            value.size=len(node['data']);value.links=node['links'];value.directory=int(node['directory'])
        else: raise AssertionError(kind)
        return 1

    def security(self,handle,kind,flags,owner,group_,dacl,sacl,output):
        path,_,_=self.row(handle);assert kind==1 and flags in (5,7,0x10)
        raw=label_descriptor(label_ace(0x2000)) if flags==0x10 else self.nodes[path]['sd']
        holder=self.api.pending if flags==0x10 else self.api._finite_pending_sd
        assert holder is not None and C.addressof(holder.storage)==C.addressof(C.cast(output,C.POINTER(C.c_void_p)).contents)
        storage=C.create_string_buffer(raw);pointer=C.addressof(storage);self.buffers[pointer]=(storage,raw)
        C.cast(output,C.POINTER(C.c_void_p))[0]=pointer;self.sd_slots.append(pointer);return 0


class PreparedEnvironment:
    def __init__(self,monkeypatch,tmp_path,stack,*,controller=False,scm_callback_hold_seconds=None):
        self.monkeypatch=monkeypatch;self.tmp_path=tmp_path;self.stack=stack
        if controller:
            monkeypatch.setitem(HOST,'machine_fingerprint_sha256',_C_SOFTWARE_MACHINE_SHA)
        self.installer,self.backend=_normal_installer(monkeypatch,fixed_probe=controller)
        self.install_result=self.installer.apply(approved_plan_sha256=self.installer.intent.sha256)
        assert self.installer.installer_compiler_state=='RETIRED'
        self.configuration=dict(self.installer._configuration.resolved.files)
        self.profile_path=self.installer.intent.destination(CONFIG_PATHS[0])
        raw=self.configuration[CONFIG_PATHS[0]];document=json.loads(raw)
        manifest_node=self.installer.nodes[prepared.MANIFEST_PATH]
        raw_manifest=self.backend.read(manifest_node.handle,manifest_node.image.size)
        host=document['host_resource_profile'];images={x['role']:x for x in host['launch_images']}
        self.manifest=prepared.load_protected_manifest(raw_manifest,path=manifest_node.path,
            profile_sha256=hashlib.sha256(raw).hexdigest(),server_image_sha256=images['server']['image_sha256'],
            worker_image_sha256=images['worker']['image_sha256'])
        self.root=tmp_path/'windows-scratch';self.root.mkdir(parents=True)
        self.Path=_windows_paths(self.root)
        for name,node in self.backend.tree.items():
            target=self.Path(name)._host()
            if node['directory']:target.mkdir(parents=True,exist_ok=True)
            else:target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(node['data'])
        client=self.Path(self.manifest.vendor_source_root)/'bin/comsolclientpath.txt'
        client.parent.mkdir(parents=True,exist_ok=True);client.write_text('plugins/COMSOL.jar\n')
        self.sdk=_InstalledPathSdk(document,self.backend)
        monkeypatch.setattr(win,'os',NS(**dict(vars(os),name='nt')))
        monkeypatch.setattr(publish,'os',NS(**dict(vars(os),name='nt')))
        monkeypatch.setattr(mic,'os',NS(name='nt'))
        monkeypatch.setattr(C,'WinDLL',lambda name,**kw:self.sdk.a if name=='advapi32' else self.sdk.nt if name=='ntdll' else self.sdk.k,raising=False)
        original_init=targets.NativeTargetReader.__init__
        def initialize(reader,owner=None):
            original_init(reader,owner);self.sdk.api=reader.mic;self.sdk.path_api=reader.api
        monkeypatch.setattr(targets.NativeTargetReader,'__init__',initialize)
        # The normal original reader observes the source facts; no caller bool
        # or decorative JSON origin is installed. Same original fixture method
        # as the existing full-chain constructor, now fed installer bytes.
        source_reader=targets.NativeTargetReader()
        try:fact=source_reader.read(self.profile_path,directory=False,content=True,legacy_source=True,source_subject_sid=host['service_account_id'])
        finally:source_reader.finish()
        identity=dict(device=fact['legacy_identity'][0],inode=fact['legacy_identity'][1],security_sha256=fact['source_chain_sha256'])
        intent=win.parse_profile_v4_document(document,source_sha256=hashlib.sha256(raw).hexdigest(),source_identity=identity,security_semantics='SOFTWARE_SDK_OBSERVATIONS_ONLY')
        self.profile=replace(intent,prepared_launch=self.manifest)
        if controller:
            self._normal_loader_leaves(host['service_account_id'])
            self.profile=win.load_trusted_profile(self.profile_path,
                host_fingerprint_reader=lambda:self.profile.host_fingerprint_sha256)
        (tmp_path/'prepared-inputs.json').write_text(json.dumps(dict(scope='SOFTWARE_FIXTURE_INPUTS_NOT_NATIVE',profile=document,manifest=json.loads(raw_manifest)),indent=1)+'\n')
        targets._retain_loaded_origin(self.profile,self.profile_path)
        if controller:monkeypatch.setenv('COMSOL_MCP_HOST_CONTROL','1')
        self.daemon=control.ControlDaemon(tmp_path/'control',registry={});stack.callback(self.daemon.close)
        self.daemon._windows_t037_profile=self.profile
        self.factory=self.daemon._new_t037_session_factory(self.profile.host_resource_profile.project_id,'session')
        authority=self.daemon.project_authority;record=dict(schema_version=1,project_id=host['project_id'],workspace=self.profile.workspace_root,revision=1,
            policy=dict(permissions=['inspect','project_write','compute','host_control'] if controller else ['inspect','project_write','compute']))
        with authority.store.lock:
            authority.store.db.execute('INSERT INTO projects(project_id,workspace,schema_version,revision,record_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                (record['project_id'],record['workspace'],1,1,json.dumps(record),'software','software'))
        monkeypatch.setattr(authority,'_verify_workspace_binding',lambda path:None)  # filesystem-only path identity seam
        self._static_policy()
        expected_service=self.profile.service_name
        class ConfiguredScmSdk(scm_fixture._ScmApi):
            def start_service_ctrl_dispatcher(self,service_name,callback):
                assert service_name==expected_service
                self.dispatch_count+=1;callback(0,None)
            def register_service_ctrl_handler_ex(self,service_name,handler):
                assert service_name==expected_service
                self.handler=handler;return 19
        monkeypatch.setattr(scm_fixture,'_ScmApi',ConfiguredScmSdk)
        if controller:
            # Original prospective stop-grant input, in this inert fixture's
            # private provider file. The provider parser/verifier remain real.
            from comsol_mcp._host_scoped_provider import scope_sha256
            scope=dict(schema='comsol-mcp-managed-stop-scope/1',
                runtime_id=dict(id='fixture-runtime',**{'class':'comsol_mcp._session_context.SessionRuntimeConfig'}),
                platform='windows',ownership_mode='originalManagedLifetime',host_source=dict(
                    t037_source_sha256=self.profile.source_sha256,
                    host_resource_profile_sha256=self.profile.host_resource_profile.sha256,
                    host_id=self.profile.host_resource_profile.host_id))
            grant=dict(project_id=host['project_id'],action='session.stop',
                authorization_ref_sha256=hashlib.sha256(b'local-test-operator').hexdigest(),
                scope_sha256=scope_sha256(host['project_id'],'session.stop',scope),
                expires_at_utc=(datetime.now(timezone.utc)+timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ'))
            from tests.test_windows_t037_qualification_original_owner import make_plan
            from tests.test_windows_host_change import _grant
            self.qualification_plan=make_plan(self.profile)
            plan_body=self.qualification_plan.as_dict()
            from comsol_mcp import _w26_os_resources as resources
            inputs=plan_body['operator_inputs'];inputs['t037_qualification']['runtime_id']='fixture-runtime'
            self.qualification_plan=resources.build_host_change_plan(self.profile.host_resource_profile,
                change_manifest=[{key:action.get(key) for key in ('action_id','resource_kind','resource_name','operation','desired',
                    'baseline_identity','baseline_sha256','backup_path','backup_sha256')}
                    for action in plan_body['change_manifest']],operator_inputs=inputs)
            qualification_grant=_grant(self.profile.host_resource_profile,'host.change.apply','qualification-controller',
                self.qualification_plan.sha256,phase='APPLY')
            write=scm_fixture._write_provider
            monkeypatch.setattr(scm_fixture,'_write_provider',lambda directory,profile,grants:write(directory,profile,[grant,qualification_grant]))
        service_options={} if scm_callback_hold_seconds is None else dict(callback_hold_seconds=scm_callback_hold_seconds)
        stack.enter_context(running_factory_chain(self.daemon,self.factory,tmp_path/'scm',monkeypatch,profile_path=self.profile_path,**service_options))
        descriptor=bytes.fromhex(next(t.descriptor_hex for t in self.profile.finite_targets.files if t.path==self.profile.workspace_root))
        _,self.api,self.events,self.model=_launch_api(monkeypatch,profile_override=self.profile,acl_descriptor=descriptor)
        self.query=_QuerySDK(monkeypatch,self.api,self.profile,self.sdk)
        self.factory._api=self.api
        self.api.hash_image=lambda path: hashlib.sha256(self.Path(path).read_bytes()).hexdigest()
        open_image=self.api._open_image
        def pin_image(path):
            self._current_image=path
            return open_image(path)
        self.api._open_image=pin_image
        self.api._hash_image_handle=lambda *args:hashlib.sha256(self.Path(self._current_image).read_bytes()).hexdigest()
        self.api.k.GetSystemWindowsDirectoryW=lambda buffer,capacity:self._root(buffer,WINROOT)
        self.api.k.GetSystemDirectoryW=lambda buffer,capacity:self._root(buffer,SYSTEMROOT)
        self.api._environment_buffer=win._Win32LaunchApi._environment_buffer.__get__(self.api)
        self._patch_filesystem_modules()
        stack.callback(self._record_final_ownership)

    def _normal_loader_leaves(self,subject):
        """Real loader/manifest bodies; Windows path-security SDK observations are inert."""
        from comsol_mcp import _host_scoped_provider as provider, _windows_t037_bootstrap as boot
        self.monkeypatch.setattr(C,'get_last_error',lambda:0,raising=False)
        self.monkeypatch.setattr(C,'WinError',lambda code=0:OSError(code,'SOFTWARE Win32 error'),raising=False)
        original=provider.verify_private_path_security
        def protection(path,*,directory=False,expected_identity=None):
            if not ntpath.splitdrive(os.fspath(path))[0]:
                return original(path,directory=directory,expected_identity=expected_identity)
            reader=targets.NativeTargetReader()
            try:
                fact=reader.read(str(path),directory=directory,content=not directory,
                    legacy_source=True,source_subject_sid=subject)
            finally:reader.finish()
            identity=dict(device=fact['legacy_identity'][0],inode=fact['legacy_identity'][1],security_sha256=fact['source_chain_sha256'])
            if expected_identity is not None:assert identity==expected_identity
            return dict(identity=identity,security_sha256=identity['security_sha256'],
                security_semantics='windows-read-control-owner-dacl-persistent-acls')
        self.monkeypatch.setattr(provider,'verify_private_path_security',protection)
        self.monkeypatch.setattr(win,'Path',self.Path)
        self.monkeypatch.setattr(boot,'Path',self.Path)
        self.monkeypatch.setattr(boot,'_WINDOWS',True)

    def public_controller(self,launcher):
        """Same SCM daemon/project, normal public admission and a fresh original factory.

        The prebuilt inert SDK API is supplied only at its constructor boundary,
        after prospective origin creation. No factory/preflight/issuer/stop
        authorization body is substituted and no live grant is issued.
        """
        from tests._controller_c_full_software_fixture import prepare_controller
        return prepare_controller(self,launcher)

    def _record_final_ownership(self):
        held=getattr(self.factory,'_held_static_a',None)
        rows=lambda registry:[dict(label=e.label,state=e.state,value=e.value) for e in registry.unresolved()]
        value=dict(scope='SOFTWARE_ONLY_ORIGINAL_OBJECTS_NO_SDK_CALLS',
            factory_closed=self.factory._closed,held_a_state=getattr(held,'state',None),
            reader_retired=getattr(getattr(held,'reader',None),'retired',None),
            held_primary_error_type=type(getattr(held,'primary_error',None)).__name__,
            held_cleanup_error_type=type(getattr(held,'cleanup_error',None)).__name__,
            factory_entries=rows(registry_for(self.factory)),api_entries=rows(registry_for(self.api)),
            retained_children=[dict(closed=c._closed,returned=c._returned,never_resumed=c.never_resumed,
                process_state=getattr(c._process_entry,'state',None),thread_state=getattr(c._thread_entry,'state',None))
                for c in getattr(self.api,'_retained_children',{}).values()],
            retained_cleanup_count=len(getattr(self.api,'_retained_launch_cleanup',())),
            birth_preparation_states=[x.state for x in getattr(self.api,'_birth_preparations',{}).values()])
        (self.tmp_path/'final-ownership.json').write_text(json.dumps(value,indent=1)+'\n')

    def _root(self,buffer,text):buffer.value=text;return len(text)

    def _patch_filesystem_modules(self):
        from comsol_mcp import _process_diagnostics as diagnostics
        import tempfile
        def host_path(path):
            return self.Path(path)._host() if ntpath.isabs(os.fspath(path)) and ntpath.splitdrive(os.fspath(path))[0] else path
        def chmod(path,mode,**kw):return os.chmod(self.Path(path)._host(),mode,**kw)
        for module in (java,server,launch):
            self.monkeypatch.setattr(module,'Path',self.Path)
            if module in (java,server):self.monkeypatch.setattr(module,'os',NS(**dict(vars(os),chmod=chmod)))
        self.monkeypatch.setattr(diagnostics,'Path',self.Path)
        self.monkeypatch.setattr(diagnostics,'tempfile',NS(mkstemp=lambda **kw:tempfile.mkstemp(**{**kw,'dir':host_path(kw['dir'])})))
        self.monkeypatch.setattr(diagnostics,'os',NS(**dict(vars(os),
            replace=lambda src,dst:os.replace(host_path(src),host_path(dst)),
            unlink=lambda path:os.unlink(host_path(path)))))

    def _static_policy(self):
        profile=self.profile;host=profile.host_resource_profile
        Adapter=native.WindowsNativePolicyAdapter;Observer=win.WindowsT037Observer
        self.static_events,_=static_sdk(self.monkeypatch,profile)
        sdk=win.WindowsT037Observer().api
        sdk.k=NS(GetCurrentProcess=lambda:1)
        sdk.current_process_token=win._Win32ReadOnlyApi.current_process_token.__get__(sdk)
        sdk.volume_snapshot=lambda path:dict(volume_guid_path='SOFTWARE_VOLUME',filesystem='NTFS',
            serial_number=1,flags=8,total_bytes=1024,caller_available_bytes=512,free_bytes=512)
        init=Observer.__init__
        self.monkeypatch.setattr(win,'WindowsT037Observer',Observer)
        def observe_initialize(observer,*,platform_name=None,api=None):
            init(observer,platform_name=platform_name or 'windows',api=sdk if api is None else api)
        self.monkeypatch.setattr(Observer,'__init__',observe_initialize)
        actual=native.WindowsNativePolicyAdapter();service=actual.api.observe_service
        self.monkeypatch.setattr(native,'WindowsNativePolicyAdapter',Adapter)
        initialize=Adapter.__init__
        def policy_initialize(adapter,*,platform_name=None,api=None):
            initialize(adapter,api=actual.api if api is None else api,platform_name=platform_name or 'windows')
        self.monkeypatch.setattr(Adapter,'__init__',policy_initialize)
        def full_snapshot_unavailable():
            from comsol_mcp._execution_contract import ExecutionContractError
            raise ExecutionContractError('T037_NATIVE_WFP_UNAVAILABLE','SOFTWARE full native WFP snapshot seam is unavailable')
        actual.api.observe_wfp_snapshot=full_snapshot_unavailable
        _,plan=network_profile_and_plan();actions=copy.deepcopy(plan.as_dict()['change_manifest'])
        filters=[a['desired'] for a in actions if a['resource_kind']=='wfp_filter']
        permits=[x for x in filters if x['action']=='PERMIT']
        for row,grant in zip(permits,host.windows_isolation.wfp_grants):row['grant_sha256']=group.digest(grant.as_dict())
        for action in actions:
            if 'service_name' in action['desired']:action['desired']['service_name']=profile.service_name
        fingerprint=group.group_fingerprint(host,filters)
        for row in filters:row['group_sha256']=fingerprint
        normalized=group.normalize_actions(dict(change_manifest=actions),host,'f'*64)
        self._software_policy_actions=normalized
        _,wfp,snapshot=effective_group_fixture();builder=GroupSDK(host)
        snapshot['filters'][:]=[builder._row(host,a) for a in normalized if a['resource_kind']=='wfp_filter']
        protections={(t.kind,t.key):native.canonical_security_descriptor_sha256(bytes.fromhex(t.descriptor_hex)) for t in profile.finite_targets.policy}
        for row in snapshot['filters']:row['security_sha256']=protections['Filter',targets.guid_key(row['filter_key'])]
        snapshot['providers'][:]=[dict(provider_key=profile.windows_policy.wfp_provider_key,display_data=dict(name=None,description=None),flags=1,provider_data_sha256=hashlib.sha256(b'').hexdigest(),service_name=profile.service_name,security_sha256=protections['Provider',targets.guid_key(profile.windows_policy.wfp_provider_key)])]
        snapshot['sublayers'][:]=[dict(sublayer_key=profile.windows_policy.wfp_sublayer_key,provider_key=profile.windows_policy.wfp_provider_key,display_data=dict(name=None,description=None),flags=1,provider_data_sha256=hashlib.sha256(b'').hexdigest(),weight=100,security_sha256=protections['SubLayer',targets.guid_key(profile.windows_policy.wfp_sublayer_key)])]
        def policy_boundary(adapter,selected):
            adapter._finite_profile=selected;adapter._finite_expected_keys={(t.kind,t.key) for t in selected.finite_targets.policy}
            adapter._finite_security_reads=dict(protections)
            adapter.observe_wfp_policy_snapshot=lambda:copy.deepcopy(wfp.observe_wfp_policy_snapshot())
            adapter.observe_application_id=wfp.observe_application_id;adapter.observe_service=service
        self.monkeypatch.setattr(targets.FinitePolicyApi,'__init__',policy_boundary)

    def runtime(self):
        m=self.manifest;Path=self.Path
        state=Path(self.profile.workspace_root)/'sessions';prefs=Path(self.profile.workspace_root)/'prefs'
        state.mkdir(parents=True,exist_ok=True);prefs.mkdir(parents=True,exist_ok=True)
        return SessionRuntimeConfig('software-prepared','6.4',Path(m.vendor_source_root),
            Path(m.jdk_files['java']['source_path']),tuple(Path(r['source_path']) for r in m.vendor_files if r['role']=='classpath'),prefs,state)

    def worker(self,fault=None):
        Path=self.Path;m=self.manifest
        state=Path(self.profile.workspace_root)/'worker';locks=Path(self.profile.workspace_root)/'locks'
        state.mkdir(parents=True,exist_ok=True);locks.mkdir(parents=True,exist_ok=True)
        paths=java.JavaWorkerPaths(Path(m.vendor_source_root),Path(ntpath.dirname(ntpath.dirname(m.jdk_files['java']['source_path']))),project_root=Path(self.profile.workspace_root),global_lock_root=locks,platform_name='nt')
        worker=java.PersistentJavaWorker(paths,state_dir=state,process_factory=self.factory)
        api=self.api;model=self.model;events=self.events;factory=self.factory
        state_data=dict(birth=(11644473600000+123456)*10000+789,exited=None,reads=[],producer_finished=False)
        mutations=[];processes=[];commands=[];health=[];listeners=[]
        def times(handle):
            if state_data['producer_finished']:state_data['reads'].append(handle)
            return (10,None) if handle==777 else (state_data['birth'],state_data['exited'])
        api.process_times=times
        api.process_image_path=lambda handle:(self.profile._launch_image('launcher').path
            if handle==777 else state_data['image_path'])
        original_close=api.close_handle
        def close(handle):
            if state_data['producer_finished'] and handle==200:mutations.append(('close',handle))
            original_close(handle);return 1
        api.k.CloseHandle=close;api.close_handle=win._Win32ReadOnlyApi.close_handle.__get__(api)
        terminate=api.terminate_process
        def kill(handle,code):mutations.append(('terminate',handle,code));return terminate(handle,code)
        api.terminate_process=kill
        api._software_sdk=state_data;api._software_child_model=model;api._software_handle_events=events
        api._prepared_fixture=self
        api._prepared_processes=processes;api._prepared_commands=commands
        original_create=api.a.CreateProcessAsUserW
        def create(*args):
            result=original_create(*args)
            factory=self.factory;inputs=factory._active_launch_input['inputs']
            command=inputs.argv;commands.append(tuple(command));state_data['image_path']=command[0]
            child=next(reversed(api._retained_children.values()));child.software_sdk=state_data;child.mutation_calls=mutations;processes.append(child)
            if fault=='original_birth_differs':state_data['birth']-=10000
            if '--endpoint-file' in command:
                ready=dict(type='ready',host='127.0.0.1',port=factory.network_intent.worker_listen_port,pid=456,process_start_epoch_ms=123456,generation=1,instance_id='software-instance',token=inputs.environment['COMSOL_MCP_WORKER_TOKEN'])
                if fault=='wrong_port':ready['port']=32001
                Path(command[command.index('--endpoint-file')+1]).write_text(json.dumps(ready))
            elif '-portfile' in command:Path(command[command.index('-portfile')+1]).write_text(str(factory.network_intent.server_listen_port))
            state_data['producer_finished']=True
            return result
        api.a.CreateProcessAsUserW=create
        # Exact historical SOFTWARE_ONLY positive consumer continuation. The
        # separate real-issuer negative reinstates its original function.
        def software_resume(child,capability):child.note_resume_attempt();child._resume_epoch+=1;return 1
        self.monkeypatch.setattr('comsol_mcp._windows_t037_resume.issue_native_resume',lambda *a:object())
        self.monkeypatch.setattr('comsol_mcp._windows_t037_resume.resume_native_child',software_resume)
        self.monkeypatch.setattr(java,'process_identity',lambda pid:dict(alive=True,start_epoch_ms=123456))
        def listener(port,**kwargs):listeners.append(port);return [(456,CanonicalSocket('127.0.0.1',port))]
        self.monkeypatch.setattr(server,'listener_rows',listener)
        def healthy(**kwargs):health.append(worker.endpoint);return dict(status='HEALTHY')
        self.monkeypatch.setattr(worker,'health',healthy)
        return factory,worker,processes,commands,[],health,listeners


def worker_start(monkeypatch,tmp_path,fault=None,*,controller=False):
    stack=_ACTIVE_STACKS[-1]
    return PreparedEnvironment(monkeypatch,tmp_path,stack,controller=controller,scm_callback_hold_seconds=600 if controller else None).worker(fault)


_ACTIVE_STACKS=[]


def enter_test(stack):
    _ACTIVE_STACKS.append(stack)
    stack.callback(_ACTIVE_STACKS.pop)
