"""One original-profile CREATE intent; no OS-generation or launch authority."""
from __future__ import annotations
import os
import re
from . import _w26_os_resources as resources
from ._windows_service_sid import check_subject, fail

SEQUENCE_SCHEMA = 'comsol-mcp.host-control-mutation-sequence/5'


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


def apply(executor, plan, approval):
    from ._windows_host_change import _normalize_supported_actions, _current_windows_binding, _receipt_payload_v2
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
    try:
        executor._start(admission)
        intent = {'step_index': 0, 'action_id': action['action_id'], 'resource_kind': 'appcontainer_profile',
                  'resource_key': action['resource_key'], 'desired_identity_sha256': action['desired_identity_sha256'],
                  'dependency_sha256': dep}
        executor.store.append_host_control_mutation_step_event(admission_id=admission.admission_id,
            event='APPLY_STEP_INTENT', payload=intent)
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
                  'restore_state': 'RESTORE_NOT_IMPLEMENTED', 'generation_state': 'UNAVAILABLE',
                  'capabilities_state': 'NOT_READ_BACK', 'safe_retry': False,
                  'evidence_classification': 'SOFTWARE_ONLY' if executor.software_only else 'WINDOWS_NATIVE_CREATE_RETURN_ONLY'}
        accepted, state = executor.store.finish(admission.operation_id, status='SUCCEEDED', result=result)
        if not accepted or state != 'SUCCEEDED':
            fail('HOST_CHANGE_RESULT_PERSISTENCE_UNKNOWN', 'AppContainer CREATE result was not durably recorded')
        return receipt
    except BaseException as error:
        executor._finish_unknown_sequence(admission, phase='APPLY', action=action, step_index=0, cause=error)
        raise


def restore(executor, plan, receipt, **kwargs):
    from ._windows_host_change import _normalize_supported_actions
    _normalize_supported_actions(plan, executor.profile)
    fail('RESTORE_NOT_IMPLEMENTED', 'AppContainer DELETE lacks genuine profile generation/exclusive ownership; no delete attempted')
