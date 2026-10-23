"""Same-process SCM lifetime and finite A diagnostics. No Resume authority.

V4 adds a closed finite filesystem/MIC/source/owned-policy contract. Legacy
profiles still refuse its missing producers. Static comparisons never grant
B/C qualification or Resume authority.
"""
from __future__ import annotations

from contextlib import contextmanager
import ntpath
import os
from types import SimpleNamespace

from ._execution_contract import ExecutionContractError


def _refuse(reason, message, **details):
    raise ExecutionContractError('T037_POLICY_UNVERIFIED', message, safe_retry=False,
        stage='pre_dispatch', details={'phase':'STATIC_HOST_BROKER_POLICY', 'reasons':[reason], **details})


def _bind_daemon_runtime(daemon, runtime):
    from ._control_daemon import ControlDaemon
    from ._host_scoped_provider import HostScopedProvider
    from ._windows_t037 import WindowsT037ProfileV2
    from ._windows_t037_broker import _require_original_runtime
    _require_original_runtime(runtime)
    profile,provider=daemon._windows_t037_profile,daemon._scoped_host_provider
    if (type(daemon) is not ControlDaemon or type(profile) is not WindowsT037ProfileV2
            or type(provider) is not HostScopedProvider or provider._closed
            or daemon.project_authority.authorization_verifier is not provider
            or runtime.service_name != profile.service_name
            or daemon._windows_t037_profile_path is None or getattr(daemon,'_closing',True)):
        _refuse('SCM_DAEMON_BINDING_MISMATCH','original configured SCM daemon/provider/profile required')
    with runtime._lock:
        _require_original_runtime(runtime)
        if runtime._daemon_binding is not None or getattr(daemon,'_windows_t037_scm_binding',None) is not None:
            _refuse('SCM_RUNTIME_REUSED','original RUNNING runtime may bind exactly one daemon once')
        binding=(runtime,daemon,profile,provider,daemon.project_authority,
                 daemon._windows_t037_profile_path,profile.source_sha256,dict(profile.source_identity),
                 profile.sha256,provider.config_sha256)
        runtime._daemon_binding=binding
        daemon._windows_t037_scm_runtime=runtime
        daemon._windows_t037_scm_binding=binding
        from ._windows_appcontainer_gate import _bind_original_profile_gate
        _bind_original_profile_gate(binding)


def _require_daemon_binding(binding):
    from ._windows_t037_broker import _require_original_runtime
    if type(binding) is not tuple or len(binding)!=10:
        _refuse('SCM_DAEMON_BINDING_REQUIRED','no original same-process SCM daemon binding')
    runtime,daemon,profile,provider,authority,path,source,identity,digest,provider_digest=binding
    _require_original_runtime(runtime)
    if (runtime._daemon_binding is not binding
            or getattr(daemon,'_windows_t037_scm_binding',None) is not binding
            or getattr(daemon,'_windows_t037_scm_runtime',None) is not runtime
            or daemon._windows_t037_profile is not profile or daemon._scoped_host_provider is not provider
            or daemon.project_authority is not authority or authority.authorization_verifier is not provider
            or daemon._windows_t037_profile_path != path or profile.source_sha256 != source
            or dict(profile.source_identity)!=identity or profile.sha256!=digest
            or runtime.service_name!=profile.service_name or provider.config_sha256!=provider_digest
            or provider._closed or getattr(daemon,'_closing',True)):
        _refuse('SCM_DAEMON_BINDING_MISMATCH','original daemon/profile/source/provider lifetime changed')
    return runtime,daemon,profile,provider


def _bind_session_factory(daemon, factory, key):
    from ._windows_t037 import T037SessionProcessFactory
    binding=getattr(daemon,'_windows_t037_scm_binding',None)
    runtime,_daemon,profile,_provider=_require_daemon_binding(binding)
    with runtime._lock:
        _require_daemon_binding(binding)
        if (_daemon is not daemon or type(factory) is not T037SessionProcessFactory
                or factory.profile is not profile or key[1]!=factory.session_id
                or daemon._session_t037_process_factories.get(key) is not factory or factory._closed
                or getattr(factory,'_scm_binding',None) is not None):
            _refuse('SCM_FACTORY_BINDING_MISMATCH','registered original factory is required before static SDK reads')
        from ._windows_appcontainer_gate import _register_factory
        _register_factory(factory,(binding,daemon,key,factory))


def _require_factory_runtime(factory):
    from ._windows_t037 import T037SessionProcessFactory
    binding=getattr(factory,'_scm_binding',None)
    if type(factory) is not T037SessionProcessFactory or type(binding) is not tuple or len(binding)!=4:
        _refuse('SCM_FACTORY_BINDING_REQUIRED','console/serialized/foreign factories cannot acquire native birth rights')
    original,daemon,key,exact=binding
    runtime,bound_daemon,profile,provider=_require_daemon_binding(original)
    if (exact is not factory or bound_daemon is not daemon or factory.profile is not profile
            or factory._closed or key[1]!=factory.session_id
            or daemon._session_t037_process_factories.get(key) is not factory):
        _refuse('SCM_FACTORY_BINDING_MISMATCH','original factory/daemon registration changed')
    return runtime,daemon,profile,provider


@contextmanager
def _factory_birth_scope(factory):
    from ._windows_appcontainer_gate import _factory_activity_scope
    with _factory_activity_scope(factory):yield


def _observe_static_inputs(profile, owner):
    """Read current SDK facts only. The returned diagnostics are not proof."""
    from . import _windows_t037 as win
    from ._windows_t037_native import WindowsNativePolicyAdapter
    from ._windows_t037_effective import observe_effective_wfp
    from ._windows_t037_wfp import guid_key
    from ._windows_owned_resources import registry_for,attach_registry
    registry=registry_for(owner)
    if registry.unresolved():
        from ._windows_t037_targets import _has_only_original_pins
        if not _has_only_original_pins(owner,registry):
            _refuse('STATIC_NATIVE_OWNER_UNRESOLVED','original static resources remain owned; no observation retry')
    scope=registry.scope()
    observer=win.WindowsT037Observer()
    owner._prebirth_observers.append(observer)
    attach_registry(observer.api,registry)
    try:
        if profile.wrapper_schema == win.PROFILE_SCHEMA_V4:
            from ._windows_t037_targets import FinitePolicyApi
            adapter=WindowsNativePolicyAdapter(api=FinitePolicyApi(profile))
        else:
            adapter=WindowsNativePolicyAdapter()
        observer._native_policy_adapters=[adapter]
        attach_registry(adapter.api,registry)
        if win.windows_host_fingerprint(resource_registry=registry)!=profile.host_fingerprint_sha256:
            _refuse('STATIC_HOST_MISMATCH','actual host differs from the exact startup profile')
        before=adapter.api.observe_service(profile.service_name)
        current=observer.observe_process(os.getpid())
        policy=profile.windows_policy;launcher=profile._launch_image('launcher')
        wanted={'service_name':policy.service_name,'service_start_name':policy.service_start_name,
                'service_start_sid':policy.service_logon_sid,'service_sid_type':policy.service_sid_type,
                'service_sid':policy.service_sid,'state':'RUNNING', 'process_id':os.getpid(),
                'service_binary_argv_sha256':launcher.argv_sha256,
                'service_security_semantics':'OWNER_DACL_CANONICAL_ACE_ORDER_READ_CONTROL'}
        if (type(before) is not dict or any(before.get(k)!=v for k,v in wanted.items())
                or ntpath.normcase(ntpath.normpath(before.get('service_binary_image_path','')))
                    !=ntpath.normcase(ntpath.normpath(launcher.path))):
            _refuse('STATIC_SCM_CONFIGURATION_MISMATCH','actual SCM configuration/PID does not match this process/profile')
        token=current.get('token',{})
        if (current.get('pid')!=os.getpid() or type(current.get('birth_filetime_100ns')) is not int
                or current['birth_filetime_100ns']<=0 or current.get('exit_filetime_100ns') is not None
                or ntpath.normcase(ntpath.normpath(current.get('image_path','')))
                    !=ntpath.normcase(ntpath.normpath(launcher.path))
                or token.get('state')!='OBSERVED' or token.get('user_sid')!=policy.service_logon_sid
                or policy.service_sid not in token.get('group_sids',())):
            _refuse('STATIC_SCM_PROCESS_MISMATCH','actual current broker process/birth/image/token does not match SCM')
        # Reuse the bounded five-catalog, four-layer and role App-ID parser.
        # Its unverified arbitration/visibility reasons remain diagnostic facts.
        snapshot=adapter.api.observe_wfp_policy_snapshot()
        # One current SDK snapshot, fed only into the existing bounded parser;
        # this transient adapter/value is not an admission/Resume capability.
        parser_input=SimpleNamespace(observe_wfp_policy_snapshot=lambda:snapshot,
                                    observe_application_id=adapter.api.observe_application_id)
        effective=observe_effective_wfp(profile,parser_input)
        configured={guid_key(key) for key in policy.wfp_filter_keys}
        if len(configured)!=4+len(policy.wfp_grants) or set(effective.matched_filter_keys)!=configured:
            _refuse('STATIC_OWNED_FOUR_LAYER_CONFIGURATION_MISMATCH',
                    'actual configured four-default/exact-permit set is incomplete or mismatched',
                    wfp_reasons=list(effective.reasons))
        from ._windows_filter_group import LAYERS
        rows=[r for r in snapshot['filters'] if guid_key(r['filter_key']) in configured]
        defaults=[r for r in rows if r['action_type']==0x1001]
        if (len(defaults)!=4 or {guid_key(r['layer_key']) for r in defaults}!={guid_key(k) for k in LAYERS}
                or any(r['weight']['value']<=next(b['weight']['value'] for b in defaults
                    if guid_key(b['layer_key'])==guid_key(r['layer_key']))
                    for r in rows if r['action_type']==0x1002)):
            _refuse('STATIC_OWNED_FOUR_LAYER_CONFIGURATION_MISMATCH','owned group defaults/permit precedence mismatch')
        if profile.wrapper_schema == win.PROFILE_SCHEMA_V4:
            from ._windows_t037_targets import require_owned_policy
            owned_before=require_owned_policy(profile,snapshot,adapter.api)
            # A second bounded read must retain the exact configured set and
            # protection. Global arbitration/visibility reasons remain facts.
            fresh=adapter.api.observe_wfp_policy_snapshot()
            owned_after=require_owned_policy(profile,fresh,adapter.api)
            layer_rows=lambda value: sorted([r for r in value['layers'] if guid_key(r['layer_key'])
                in {guid_key(k) for k in LAYERS}],key=lambda r:r['layer_key'])
            if owned_before!=owned_after or layer_rows(fresh)!=layer_rows(snapshot):
                _refuse('STATIC_POLICY_CHANGED','own policy/four-layer schema changed during finite static A')
        if adapter.api.observe_service(profile.service_name)!=before or observer.observe_process(os.getpid())!=current:
            _refuse('STATIC_SCM_CHANGED','original SCM/process facts changed during static read')
        return {'scope':'STATIC_SDK_INPUTS_ONLY_NOT_ADMISSION','service_pid':os.getpid(),
                'birth_filetime_100ns':current['birth_filetime_100ns'],
                'wfp_snapshot_sha256':effective.snapshot_sha256,
                'matched_filter_keys':list(effective.matched_filter_keys),'wfp_reasons':list(effective.reasons)}
    finally:
        failures=registry.drain(scope)
        if failures:raise failures[0][1]


def require_static_prebirth(profile, *, owner):
    runtime,daemon,original,provider=_require_factory_runtime(owner)
    if profile is not original:
        _refuse('STATIC_PROFILE_MISMATCH','static read requires the exact original profile object')
    provider._assert_stable()  # Actual pinned startup provider; never a JSON approval.
    from ._windows_t037 import PROFILE_SCHEMA_V4
    if profile.wrapper_schema == PROFILE_SCHEMA_V4:
        from ._windows_t037_targets import finite_target_scope
        with finite_target_scope(profile,owner,daemon):
            _observe_static_inputs(profile,owner)
            _require_factory_runtime(owner)
            provider._assert_stable()
        # Only static A completes here. The existing original Job/birth path
        # and unimplemented B/C issuer remain the next independent gates.
        return
    observation=_observe_static_inputs(profile,owner)
    _require_factory_runtime(owner)
    # No inferred file type, mandatory label, code/TCB tree or permission is
    # invented from an ACL grant. Main must settle that exact finite target.
    _refuse('FINITE_FILESYSTEM_MIC_TARGET_PRODUCER_UNAVAILABLE',
        'finite static file-kind/mandatory-label/code-TCB target cannot be expressed by the current profile',
        observed=observation,
        missing_target_fields=['file_or_directory','expected_mandatory_label','protected_code_config_and_runtime_TCB',
                               'live_pinned_profile_source_identity_hash_security','owned_policy_protection_readback'],
        deferred=['ORIGINAL_SUSPENDED_CHILD_B','PURPOSE_BOUND_QUALIFICATION_C','RESUME_ISSUER_UNAVAILABLE'])
