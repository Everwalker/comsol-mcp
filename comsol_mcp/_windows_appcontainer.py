"""One original-profile CREATE intent; no OS-generation or launch authority."""
from __future__ import annotations
import os
import re
import hashlib
from . import _w26_os_resources as resources
from ._windows_service_sid import check_subject, fail

SEQUENCE_SCHEMA = 'comsol-mcp.host-control-mutation-sequence/5'
RESTORE_SEQUENCE_SCHEMA = 'comsol-mcp.host-control-mutation-sequence/6'


def is_leaf(actions):
    return any(isinstance(a, dict) and a.get('resource_kind') == 'appcontainer_profile' for a in actions)


def normalize(payload, profile):
    actions = payload['change_manifest']
    if len(actions) != 1 or actions[0]['resource_kind'] != 'appcontainer_profile' or actions[0]['operation'] != 'CREATE':
        fail('HOST_CHANGE_APPCONTAINER_UNSUPPORTED', 'only one original AppContainer CREATE is supported')
    action = actions[0]
    policy = profile.windows_isolation
    if not re.fullmatch(r'[-_. A-Za-z0-9]{1,64}', policy.appcontainer_profile_name):
        fail('HOST_CHANGE_APPCONTAINER_NAME_INVALID', 'profile name is outside the documented userenv contract')
    if (action['resource_name'] != policy.appcontainer_profile_name
            or action['desired'] != {'profile_id': profile.applied_policy_id,
                                     'policy_sha256': resources._sha256(policy.as_dict())}):
        fail('HOST_CHANGE_APPCONTAINER_PROFILE_MISMATCH', 'AppContainer action differs from the exact original policy')
    return {**action, 'resource_key': action['ownership_guid']}


def validate_journal_leaf(baseline, row):
    sequence = baseline.get('mutation_sequence', {})
    subject = baseline.get('appcontainer_service_subject')
    policy = baseline.get('appcontainer_policy')
    if (sequence.get('schema') != SEQUENCE_SCHEMA or sequence.get('phase') != 'APPLY'
            or len(sequence.get('steps', [])) != 1
            or not isinstance(subject, dict) or not isinstance(policy, dict)
            or resources._sha256({'subject': subject, 'policy': policy}) != baseline.get('dependency_sha256')):
        fail('HOST_CONTROL_JOURNAL_CORRUPT', 'AppContainer journal lacks the original bound subject/policy')


def validate_restore_journal_leaf(baseline, row):
    sequence = baseline.get('mutation_sequence')
    subject = baseline.get('appcontainer_service_subject')
    policy = baseline.get('appcontainer_policy')
    restore = baseline.get('appcontainer_restore')
    expected_baseline_keys = {
        'schema', 'source_operation_id', 'source_job_id', 'source_admission_id',
        'source_result_sha256', 'restore_body',
    }
    if (not isinstance(sequence, dict)
            or sequence.get('schema') != RESTORE_SEQUENCE_SCHEMA
            or sequence.get('phase') != 'RESTORE'
            or sequence.get('plan_sha256') != row.get('plan_sha256')
            or not isinstance(sequence.get('steps'), list)
            or len(sequence['steps']) != 1
            or not isinstance(subject, dict) or not isinstance(policy, dict)
            or not isinstance(restore, dict) or set(restore) != expected_baseline_keys
            or restore.get('schema') != 'comsol-mcp.appcontainer-profile-restore-binding/1'
            or not all(isinstance(restore.get(key), str) and restore[key]
                       for key in ('source_operation_id', 'source_job_id', 'source_admission_id'))
            or not isinstance(restore.get('source_result_sha256'), str)
            or re.fullmatch(r'[0-9a-f]{64}', restore['source_result_sha256']) is None
            or resources._sha256({'subject': subject, 'policy': policy})
                != baseline.get('dependency_sha256')):
        fail('HOST_CONTROL_JOURNAL_CORRUPT',
             'AppContainer RESTORE baseline lacks its exact source and policy binding')
    body = restore.get('restore_body')
    body_fields = {
        'schema', 'source_plan_sha256', 'source_receipt_sha256', 'profile_sha256',
        'resource_kind', 'resource_key', 'expected_created_resource_sha256',
        'operation', 'acceptance_target',
    }
    if (not isinstance(body, dict) or set(body) != body_fields
            or body.get('schema') != 'comsol-mcp.appcontainer-profile-restore/1'
            or body.get('source_plan_sha256') != row.get('source_plan_sha256')
            or body.get('source_receipt_sha256') != row.get('source_receipt_sha256')
            or body.get('profile_sha256') != row.get('profile_sha256')
            or body.get('resource_kind') != 'appcontainer_profile'
            or body.get('operation') != 'DELETE'
            or body.get('acceptance_target') != 'NATIVE_API_RETURN_ONLY'
            or re.fullmatch(r'[0-9a-f]{64}', str(body.get('source_plan_sha256', ''))) is None
            or re.fullmatch(r'[0-9a-f]{64}', str(body.get('source_receipt_sha256', ''))) is None
            or re.fullmatch(r'[0-9a-f]{64}', str(body.get('profile_sha256', ''))) is None
            or re.fullmatch(r'[0-9a-f]{64}', str(body.get('expected_created_resource_sha256', ''))) is None
            or resources._sha256(body) != row.get('plan_sha256')):
        fail('HOST_CONTROL_JOURNAL_CORRUPT',
             'AppContainer deterministic RESTORE digest differs from its admission')
    step = sequence['steps'][0]
    if (not isinstance(step, dict)
            or set(step) != {'step_index', 'action_id', 'resource_kind', 'resource_key',
                             'identity_sha256', 'dependency_sha256', 'expected_resource_sha256'}
            or step.get('step_index') != 0 or type(step.get('step_index')) is not int
            or not isinstance(step.get('action_id'), str) or not step['action_id']
            or step.get('resource_kind') != 'appcontainer_profile'
            or step.get('resource_key') != body.get('resource_key')
            or step.get('expected_resource_sha256') != body.get('expected_created_resource_sha256')
            or not all(isinstance(step.get(key), str)
                       and re.fullmatch(r'[0-9a-f]{64}', step[key]) is not None
                       for key in ('identity_sha256', 'dependency_sha256'))
            or step.get('dependency_sha256') != baseline.get('dependency_sha256')):
        fail('HOST_CONTROL_JOURNAL_CORRUPT',
             'AppContainer RESTORE step differs from its deterministic leaf binding')
    try:
        from uuid import UUID
        parsed_key = '{' + str(UUID(step['resource_key'].strip('{}'))).upper() + '}'
    except (AttributeError, TypeError, ValueError):
        fail('HOST_CONTROL_JOURNAL_CORRUPT', 'AppContainer RESTORE resource key is not a GUID')
    if parsed_key != step['resource_key']:
        fail('HOST_CONTROL_JOURNAL_CORRUPT', 'AppContainer RESTORE resource key is not canonical')
    expected_create = {
        'profile_name': policy.get('appcontainer_profile_name'),
        'appcontainer_sid': policy.get('appcontainer_sid'),
        'service_user_sid': policy.get('service_logon_sid'),
        'creation_result': 'S_OK',
        'capabilities_state': 'NOT_READ_BACK',
        'generation_state': 'UNAVAILABLE',
    }
    expected_resource_sha = resources._sha256(expected_create)
    if (expected_resource_sha != body['expected_created_resource_sha256']
            or expected_resource_sha != step['expected_resource_sha256']):
        fail('HOST_CONTROL_JOURNAL_CORRUPT',
             'AppContainer expected CREATE receipt is not derivable from the pinned policy')
    receipt_body = {
        'schema': 'comsol-mcp.applied-host-change-receipt/2',
        'plan_sha256': row.get('source_plan_sha256'),
        'host_id': row.get('host_id'),
        'platform': 'windows',
        'actions': [{
            'action_id': step['action_id'],
            'resource_kind': 'appcontainer_profile',
            'resource_key': step['resource_key'],
            'created_resource_sha256': expected_resource_sha,
            'prior_resource_sha256': None,
            'prior_backup_path': None,
        }],
    }
    if (hashlib.sha256(resources._canonical_json(receipt_body).encode('utf-8')).hexdigest()
            != row.get('source_receipt_sha256')
            or restore['source_operation_id'] == row.get('operation_id')
            or restore['source_job_id'] == row.get('job_id')
            or restore['source_admission_id'] == row.get('admission_id')):
        fail('HOST_CONTROL_JOURNAL_CORRUPT',
             'AppContainer RESTORE source receipt or original operation identity is inconsistent')


def apply(executor, plan, approval):
    from ._windows_host_change import _normalize_supported_actions, _current_windows_binding, _receipt_payload_v2
    from . import _windows_appcontainer_lifecycle as lifecycle
    _, actions = _normalize_supported_actions(plan, executor.profile)
    action = actions[0]
    profile = executor.profile
    policy = profile.windows_isolation
    current = ({'service_token_sid': policy.service_logon_sid, 'evidence_classification': 'SOFTWARE_ONLY'}
               if executor.software_only else _current_windows_binding(profile))
    subject = executor.api._service_sid_observe(policy.service_name)
    check_subject(subject, profile, os.getpid())
    executor.api._appcontainer_no_impersonation()
    dep = resources._sha256({'subject': subject, 'policy': policy.as_dict()})
    baseline = {'current_binding': current, 'appcontainer_service_subject': subject,
                'appcontainer_policy': policy.as_dict(), 'dependency_sha256': dep,
                'mutation_sequence': {'schema': SEQUENCE_SCHEMA, 'phase': 'APPLY', 'plan_sha256': plan.sha256,
                    'steps': [{'step_index': 0, 'action_id': action['action_id'], 'resource_kind': 'appcontainer_profile',
                               'resource_key': action['resource_key'], 'identity_sha256': action['desired_identity_sha256'],
                               'dependency_sha256': dep, 'expected_resource_sha256': None}]}}
    admission = executor._admit(phase='APPLY', plan_sha256=plan.sha256, approval=approval, baseline=baseline)
    profile_effect = None
    try:
        executor._start(admission)
        intent = {'step_index': 0, 'action_id': action['action_id'], 'resource_kind': 'appcontainer_profile',
                  'resource_key': action['resource_key'], 'desired_identity_sha256': action['desired_identity_sha256'],
                  'dependency_sha256': dep}
        executor.store.append_host_control_mutation_step_event(admission_id=admission.admission_id,
            event='APPLY_STEP_INTENT', payload=intent)
        effect_factory = getattr(executor, '_on_original_appcontainer_profile_effect', None)
        if not callable(effect_factory):
            fail('HOST_CHANGE_APPCONTAINER_OWNER_REQUIRED',
                 'the exact daemon-rooted profile effect must exist before native CREATE')
        profile_effect = effect_factory(executor, admission, action, subject)
        if (type(profile_effect) is not lifecycle.ProfileEffect
                or profile_effect.executor is not executor
                or profile_effect.admission is not admission
                or profile_effect.api is not executor.api):
            fail('HOST_CHANGE_APPCONTAINER_OWNER_UNKNOWN',
                 'daemon returned an unbound original profile effect')
        profile_effect.require_rooted(executor.api)
        if getattr(executor.api, '_appcontainer_profile_effect', None) is not profile_effect:
            fail('HOST_CHANGE_APPCONTAINER_OWNER_UNKNOWN',
                 'the original SDK API does not retain its daemon-rooted profile effect')
        returned = executor.api._appcontainer_create(policy, subject)
        expected = {'profile_name': policy.appcontainer_profile_name, 'appcontainer_sid': policy.appcontainer_sid,
                    'service_user_sid': policy.service_logon_sid, 'creation_result': 'S_OK',
                    'capabilities_state': 'NOT_READ_BACK', 'generation_state': 'UNAVAILABLE'}
        if returned != expected:
            fail('HOST_CHANGE_APPCONTAINER_RESULT_UNKNOWN', 'original CREATE output differs from the exact policy')
        resource_sha = resources._sha256(returned)
        executor.store.append_host_control_mutation_step_event(admission_id=admission.admission_id,
            event='APPLY_STEP_READBACK_VERIFIED', payload={**intent, 'created_resource_sha256': resource_sha})
        rows = ({'action_id': action['action_id'], 'resource_kind': 'appcontainer_profile', 'resource_key': action['resource_key'],
                 'created_resource_sha256': resource_sha, 'prior_resource_sha256': None, 'prior_backup_path': None},)
        body = _receipt_payload_v2(plan, profile, rows)
        receipt_sha = resources._sha256(body)
        receipt = resources.AppliedHostChangeReceipt(plan.sha256, profile.host_id, 'windows', rows, receipt_sha)
        result = {'success': True, 'operation': 'host.change.apply', 'execution_state': 'SUCCEEDED',
                  'host_change_state': 'CONFIGURED_UNVERIFIED', 'plan_sha256': plan.sha256,
                  'profile_sha256': profile.sha256, 'applied_receipt': {**body, 'receipt_sha256': receipt_sha},
                  'receipt_sha256': receipt_sha, 'effective_policy_state': 'UNVERIFIED', 't037_state': 'NOT_VERIFIED',
                  'restore_state': 'API_ONLY_DELETE_AVAILABLE_POSTIMAGE_UNVERIFIED', 'generation_state': 'UNAVAILABLE',
                  'capabilities_state': 'NOT_READ_BACK', 'safe_retry': False,
                  'evidence_classification': 'SOFTWARE_ONLY' if executor.software_only else 'WINDOWS_NATIVE_CREATE_RETURN_ONLY'}
        accepted, state = executor.store.finish(admission.operation_id, status='SUCCEEDED', result=result)
        if not accepted or state != 'SUCCEEDED':
            fail('HOST_CHANGE_RESULT_PERSISTENCE_UNKNOWN', 'AppContainer CREATE result was not durably recorded')
        profile_effect.record_store_success(admission, executor.store, accepted, state, receipt)
        return receipt
    except BaseException as error:
        executor._finish_unknown_sequence(admission, phase='APPLY', action=action, step_index=0, cause=error)
        raise


def _restore_result(executor, selection, attempt, *, native_state, hresult,
                    acknowledgment_state=None):
    return {
        'success': False,
        'operation': 'host.change.restore',
        'execution_state': 'UNKNOWN',
        'host_change_state': 'RECOVERY_REQUIRED',
        'restore_state': 'POSTIMAGE_UNVERIFIED',
        'postimage_state': 'UNVERIFIED',
        'native_delete_state': native_state,
        'api_name': 'DeleteAppContainerProfile',
        'api_return_hresult': hresult,
        'api_return_acknowledgment': acknowledgment_state or 'UNVERIFIED',
        'plan_sha256': selection.plan_sha256,
        'restore_plan_sha256': selection.restore_sha256,
        'source_receipt_sha256': selection.receipt_sha256,
        'profile_sha256': selection.effect.profile.sha256,
        'generation_state': 'UNAVAILABLE',
        'effective_policy_state': 'UNVERIFIED',
        'safe_retry': False,
        'evidence_scope': 'NATIVE_API_RETURN_ONLY',
        'evidence_classification': (
            'SOFTWARE_ONLY' if selection.effect.software_only
            else 'WINDOWS_NATIVE_API_RETURN_ONLY'
        ),
    }


def _mark_recovery(executor, admission, action, cause, result):
    executor._finish_unknown_sequence(
        admission, phase='RESTORE', action=action, step_index=0,
        cause=cause, result=result,
    )


def restore(executor, plan, receipt, *, approval=None, authorization_ref=None):
    from . import _windows_appcontainer_lifecycle as lifecycle
    from ._windows_appcontainer_native import (
        capture_delete_attempt, delete_appcontainer_profile,
    )
    from ._windows_host_change import _normalize_supported_actions
    from ._windows_appcontainer_gate import _pause_terminal_profile
    _, actions = _normalize_supported_actions(plan, executor.profile)
    action = actions[0]
    selector = getattr(executor, '_on_select_original_appcontainer_profile_effect', None)
    if not callable(selector):
        fail('HOST_CHANGE_APPCONTAINER_OWNER_REQUIRED',
             'RESTORE requires the private Controller selector for the original rooted CREATE effect')
    selection = selector(executor, plan, receipt, action)
    if (type(selection) is not lifecycle.ProfileRestoreSelection
            or selection._seal is not lifecycle._RESTORE_SELECTION_SEAL
            or selection.executor is not executor
            or selection.request.plan is not plan
            or selection.request.receipt is not receipt
            or selection.effect.executor.api is not selection.effect.api):
        fail('HOST_CHANGE_APPCONTAINER_OWNER_BINDING_MISMATCH',
             'RESTORE selection is not bound to this exact sealed request and executor')

    ref = authorization_ref
    if approval is not None:
        if (authorization_ref is not None
                or not isinstance(approval, resources.HostChangeApproval)
                or approval.plan_sha256 != selection.restore_sha256):
            fail('HOST_CHANGE_APPROVAL_MISMATCH',
                 'RESTORE approval must bind the deterministic AppContainer restore digest')
        ref = approval.authorization_ref
    elif not isinstance(ref, str) or not ref.strip() or len(ref) > 512 \
            or any(ord(char) < 32 for char in ref):
        fail('HOST_CHANGE_AUTHORIZATION_REQUIRED',
             'RESTORE requires its distinct original scoped authorization reference')
    ref_sha256 = hashlib.sha256(ref.encode('utf-8')).hexdigest()
    scope = {
        'root': executor.profile.root.as_dict(),
        'host_id': executor.profile.host_id,
        'profile_sha256': executor.profile.sha256,
        'plan_sha256': selection.restore_sha256,
        'phase': 'RESTORE',
        'source_plan_sha256': selection.plan_sha256,
        'source_receipt_sha256': selection.receipt_sha256,
    }
    project = executor.authority.authorize_operation(
        selection.request.project_id, 'host_control')
    from ._w26_os_resources import _path_key
    if (_path_key(project.get('workspace', ''), 'windows')
            != _path_key(executor.profile.root.path, 'windows')
            or executor.provider.verify_scope(
                executor.profile.project_id, 'host.change.restore',
                scope, ref) is not True):
        fail('HOST_CONTROL_AUTHORIZATION_REQUIRED',
             'the existing host provider rejected the exact AppContainer RESTORE scope')
    executor.provider._assert_stable()
    existing = selection.effect._restore_attempt
    if existing is not None:
        if (type(existing) is not lifecycle.ProfileRestoreAttempt
                or existing._seal is not lifecycle._RESTORE_ATTEMPT_SEAL
                or existing.selection.effect is not selection.effect
                or existing.selection.restore_sha256 != selection.restore_sha256
                or existing.selection.plan_sha256 != selection.plan_sha256
                or existing.selection.receipt_sha256 != selection.receipt_sha256
                or existing.authorization_ref_sha256 != ref_sha256
                or existing.native_attempt.state != 'API_CONFIRMED'
                or existing.native_attempt.hresult != 0
                or existing.pause.state != 'PAUSED'):
            fail('HOST_CHANGE_APPCONTAINER_DELETE_REPLAY_REFUSED',
                 'only exact cached S_OK metadata acknowledgment may continue the original restore attempt')
        existing.pause.check()
        try:
            ack = executor.store.record_appcontainer_delete_api_return(existing)
            result = _restore_result(
                executor, selection, existing, native_state='API_CONFIRMED',
                hresult=0, acknowledgment_state=ack['state'],
            )
            _mark_recovery(
                executor, existing.admission, selection.action,
                resources.ResourcePolicyError(
                    'HOST_CHANGE_APPCONTAINER_POSTIMAGE_UNVERIFIED',
                    'the cached Delete S_OK has no independent AppContainer postimage readback',
                ),
                result,
            )
            return result
        except BaseException:
            raise

    profile_effect = selection.effect
    current = ({'service_token_sid': executor.profile.windows_isolation.service_logon_sid,
                'evidence_classification': 'SOFTWARE_ONLY'}
               if executor.software_only else
               __import__('comsol_mcp._windows_host_change', fromlist=['_current_windows_binding'])
               ._current_windows_binding(executor.profile))
    subject = profile_effect.api._service_sid_observe(
        executor.profile.windows_isolation.service_name)
    check_subject(subject, executor.profile, os.getpid())
    if resources._canonical_json(subject) != resources._canonical_json(profile_effect.subject):
        fail('HOST_CHANGE_APPCONTAINER_SUBJECT_CHANGED',
             'the current configured service subject differs from the original CREATE subject')
    dep = resources._sha256({'subject': subject,
                             'policy': executor.profile.windows_isolation.as_dict()})
    source_binding = {
        'schema': 'comsol-mcp.appcontainer-profile-restore-binding/1',
        'source_operation_id': profile_effect.admission.operation_id,
        'source_job_id': profile_effect.admission.job_id,
        'source_admission_id': profile_effect.admission.admission_id,
        'source_result_sha256': profile_effect._apply_result_sha256,
        'restore_body': selection.restore_body,
    }
    baseline = {
        'current_binding': current,
        'appcontainer_service_subject': subject,
        'appcontainer_policy': executor.profile.windows_isolation.as_dict(),
        'dependency_sha256': dep,
        'appcontainer_restore': source_binding,
        'mutation_sequence': {
            'schema': RESTORE_SEQUENCE_SCHEMA,
            'phase': 'RESTORE',
            'plan_sha256': selection.restore_sha256,
            'steps': [{
                'step_index': 0,
                'action_id': action['action_id'],
                'resource_kind': 'appcontainer_profile',
                'resource_key': action['resource_key'],
                'identity_sha256': action['desired_identity_sha256'],
                'dependency_sha256': dep,
                'expected_resource_sha256': selection.expected_resource_sha256,
            }],
        },
    }
    validate_restore_journal_leaf(baseline, {
        'plan_sha256': selection.restore_sha256,
        'source_plan_sha256': selection.plan_sha256,
        'source_receipt_sha256': selection.receipt_sha256,
        'profile_sha256': executor.profile.sha256,
        'host_id': executor.profile.host_id,
        'operation_id': 'restore-candidate',
        'job_id': 'restore-candidate-job',
        'admission_id': 'restore-candidate-admission',
    })
    native_attempt = capture_delete_attempt(profile_effect.api, profile_effect)
    pause = _pause_terminal_profile(profile_effect.runtime)
    admission = None
    restore_attempt = None
    try:
        if approval is None:
            approval = resources.HostChangeApproval(selection.restore_sha256, ref)
        admission = executor._admit(
            phase='RESTORE', plan_sha256=selection.restore_sha256,
            approval=approval, baseline=baseline,
            source_plan_sha256=selection.plan_sha256,
            source_receipt_sha256=selection.receipt_sha256,
        )
        executor._start(admission)
        intent = {
            'step_index': 0,
            'action_id': action['action_id'],
            'resource_kind': 'appcontainer_profile',
            'resource_key': action['resource_key'],
            'expected_resource_sha256': selection.expected_resource_sha256,
            'dependency_sha256': dep,
        }
        executor.store.append_host_control_mutation_step_event(
            admission_id=admission.admission_id, event='RESTORE_STEP_INTENT',
            payload=intent,
        )
        restore_attempt = lifecycle.ProfileRestoreAttempt(
            selection=selection, admission=admission,
            authorization_ref_sha256=ref_sha256,
            dependency_sha256=dep,
            baseline_sha256=resources._sha256(baseline),
            native_attempt=native_attempt, pause=pause,
            seal=lifecycle._RESTORE_ATTEMPT_SEAL,
        )
        profile_effect.bind_restore_attempt(profile_effect.api, restore_attempt)

        # Re-read original source/current process facts after the durable
        # intent, outside runtime/SQLite locks. The terminal pause then fences
        # new factory work while the captured API enters exactly once.
        lifecycle._validate_original_effect_for_restore(
            profile_effect, daemon=selection.daemon,
            request=selection.request, executor=executor,
            action=action, receipt=receipt,
        )
        with pause.gate.runtime._lock:
            pause.check()
            profile_effect.require_rooted(profile_effect.api)
            if (profile_effect._restore_attempt is not restore_attempt
                    or native_attempt.original_call is not profile_effect._original_call
                    or native_attempt.userenv is not profile_effect._original_userenv
                    or native_attempt.function is not
                        native_attempt.userenv.DeleteAppContainerProfile
                    or native_attempt.name_buffer.value != profile_effect.profile_name):
                fail('HOST_CHANGE_APPCONTAINER_DELETE_OWNER_UNKNOWN',
                     'final original owner/function/name binding changed under the terminal pause')
            native_attempt.reserve_entry()

        # The pause and exact owner were fenced and the single entry reserved
        # under the runtime lock.  no_impersonation and Delete can block in
        # SDK/OS calls, so they must run after releasing that lock.
        delete_appcontainer_profile(profile_effect, native_attempt)

        try:
            ack = executor.store.record_appcontainer_delete_api_return(restore_attempt)
            acknowledgment = ack['state']
        except BaseException:
            acknowledgment = 'PENDING'
        result = _restore_result(
            executor, selection, restore_attempt,
            native_state='API_CONFIRMED', hresult=0,
            acknowledgment_state=acknowledgment,
        )
        _mark_recovery(
            executor, admission, action,
            resources.ResourcePolicyError(
                'HOST_CHANGE_APPCONTAINER_POSTIMAGE_UNVERIFIED',
                'Delete S_OK is API_CONFIRMED only; storage and registration postimage remain unverified',
            ),
            result,
        )
        return result
    except BaseException as error:
        if admission is None:
            raise
        if (native_attempt.state in {'RESERVED', 'PREPARING', 'INVOKING'}
                and not native_attempt.finished):
            native_attempt.mark_failed()
        if restore_attempt is not None and native_attempt.state == 'API_CONFIRMED':
            try:
                ack = executor.store.record_appcontainer_delete_api_return(restore_attempt)
                acknowledgment = ack['state']
            except BaseException:
                acknowledgment = 'PENDING'
            result = _restore_result(
                executor, selection, restore_attempt,
                native_state='API_CONFIRMED', hresult=0,
                acknowledgment_state=acknowledgment,
            )
        else:
            result = _restore_result(
                executor, selection, restore_attempt or native_attempt,
                native_state=native_attempt.state,
                hresult=native_attempt.hresult,
            )
        _mark_recovery(executor, admission, action, error, result)
        return result
