"""One explicitly configured private SCM bootstrap request, without a queue.

Reading intent never establishes native policy proof. Only a live sealed SCM
runtime may submit it through the daemon's existing authority and serial lane.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
from types import MappingProxyType
from typing import Any

from ._windows_t037_broker import WindowsScmError

SCHEMA = 'COMSOL_MCP_WINDOWS_SCM_BOOTSTRAP_V1'
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_DEPTH = 32
MAX_NODES = 100_000
_WINDOWS = os.name == 'nt'


def _fail(code, message):
    raise WindowsScmError(code, message, safe_retry=False, stage='validation')


def _object(pairs):
    row = {}
    for key, value in pairs:
        if key in row:
            _fail('SCM_BOOTSTRAP_DUPLICATE_KEY', 'bootstrap JSON contains a duplicate object key')
        row[key] = value
    return row


def _decode(raw: bytes):
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_REQUEST_BYTES:
        _fail('SCM_BOOTSTRAP_SIZE_INVALID', 'bootstrap JSON size is outside its bound')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_object,
                           parse_constant=lambda _: _fail('SCM_BOOTSTRAP_JSON_INVALID', 'nonfinite JSON is forbidden'))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        _fail('SCM_BOOTSTRAP_JSON_INVALID', 'bootstrap must be bounded UTF-8 JSON')
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > MAX_DEPTH or count > MAX_NODES:
            _fail('SCM_BOOTSTRAP_JSON_LIMIT', 'bootstrap JSON exceeds structural bounds')
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, float) and not math.isfinite(item):
            _fail('SCM_BOOTSTRAP_JSON_INVALID', 'nonfinite JSON is forbidden')
    return value


def read_private_bootstrap(path) -> bytes:
    """Read the exact owner/DACL/path-chain protected file through one handle."""
    if not _WINDOWS:
        _fail('SCM_BOOTSTRAP_WINDOWS_REQUIRED', 'private bootstrap loading requires Windows handle/ACL APIs')
    target = Path(path)
    if not target.is_absolute() or '..' in target.parts or target.name in {'', '.', '..'}:
        _fail('SCM_BOOTSTRAP_PATH_INVALID', 'bootstrap requires an absolute canonical file path')
    from ._host_scoped_provider import verify_private_path_security
    from ._windows_parameter_publish import _API
    api = _API()
    before = verify_private_path_security(target, directory=False)
    chain = api.open_security_path(target.parent, directory=True)
    handle = None
    try:
        handle = api.open_source(chain[-1], target.name)
        fingerprint = api.source_fingerprint(handle)
        identity, size, _created, _written, links = fingerprint
        expected = {'device': identity[0], 'inode': identity[1],
                    'security_sha256': before['security_sha256']}
        if (before['identity'] != expected or links != 1
                or not 1 <= size <= MAX_REQUEST_BYTES
                or before['security_semantics'] != 'windows-read-control-owner-dacl-persistent-acls'):
            _fail('SCM_BOOTSTRAP_PINNING_INVALID', 'bootstrap identity, link count, ACL semantics or size is invalid')
        payload = bytearray()
        while len(payload) < size:
            block = api.read_chunk(handle, min(16384, size - len(payload)))
            if not block:
                _fail('SCM_BOOTSTRAP_FILE_CHANGED', 'bootstrap file ended before its pinned size')
            payload.extend(block)
        if api.read_chunk(handle, 1) or api.source_fingerprint(handle) != fingerprint:
            _fail('SCM_BOOTSTRAP_FILE_CHANGED', 'bootstrap file changed during pinned read')
        after = verify_private_path_security(target, directory=False, expected_identity=expected)
        if after['security_sha256'] != before['security_sha256']:
            _fail('SCM_BOOTSTRAP_FILE_CHANGED', 'bootstrap ACL changed during pinned read')
        return bytes(payload)
    finally:
        if handle is not None:
            api.close(handle)
        for item in reversed(chain):
            api.close(item)


@dataclass(frozen=True)
class BootstrapRequest:
    phase: str
    authorization_ref: str
    plan: Any
    receipt: Any
    source_sha256: str


def parse_bootstrap(raw: bytes, profile) -> BootstrapRequest:
    from ._windows_t037 import WindowsT037ProfileV2
    from . import _w26_os_resources as resources
    from ._windows_host_change import _normalize_supported_actions, _verify_receipt, _verify_sequence_receipt
    if type(profile) is not WindowsT037ProfileV2:
        _fail('SCM_BOOTSTRAP_PROFILE_REQUIRED', 'bootstrap requires the daemon trusted v2 profile')
    value = _decode(raw)
    fields = {'schema', 'service_name', 'profile_source_sha256', 'host_profile_sha256',
              'project_id', 'phase', 'authorization_ref', 'plan', 'receipt'}
    if not isinstance(value, dict) or set(value) != fields or value['schema'] != SCHEMA:
        _fail('SCM_BOOTSTRAP_SCHEMA_INVALID', 'bootstrap fields must match the exact closed schema')
    if (value['service_name'] != profile.service_name
            or value['profile_source_sha256'] != profile.source_sha256
            or value['host_profile_sha256'] != profile.sha256
            or value['project_id'] != profile.host_resource_profile.project_id):
        _fail('SCM_BOOTSTRAP_PROFILE_MISMATCH', 'bootstrap does not bind this exact service/profile/project')
    selected = value['plan']
    if not isinstance(selected, dict) or set(selected) != {'payload_json', 'sha256'}:
        _fail('SCM_BOOTSTRAP_PLAN_INVALID', 'bootstrap requires the exact immutable plan envelope')
    if not isinstance(selected['payload_json'], str):
        _fail('SCM_BOOTSTRAP_PLAN_INVALID', 'plan payload must be a JSON string')
    _decode(selected['payload_json'].encode('utf-8'))  # Nested JSON has the same duplicate/depth bounds.
    plan = resources.HostChangePlan(selected['payload_json'], selected['sha256'])
    resources.validate_host_change_plan(plan)
    _normalize_supported_actions(plan, profile.host_resource_profile)
    from ._windows_t037_broker import _authorization_ref
    authorization_ref = _authorization_ref(value['authorization_ref'])
    phase, receipt = value['phase'], None
    if phase == 'APPLY':
        if value['receipt'] is not None:
            _fail('SCM_BOOTSTRAP_RECEIPT_INVALID', 'APPLY must not carry a receipt')
    elif phase == 'RESTORE':
        r = value['receipt']
        if not isinstance(r, dict) or set(r) != {'plan_sha256', 'host_id', 'platform', 'actions', 'receipt_sha256'}:
            _fail('SCM_BOOTSTRAP_RECEIPT_INVALID', 'RESTORE requires the exact receipt envelope')
        if not isinstance(r['actions'], list) or not 1 <= len(r['actions']) <= 3:
            _fail('SCM_BOOTSTRAP_RECEIPT_INVALID', 'RESTORE receipt actions are outside the supported bound')
        receipt = resources.AppliedHostChangeReceipt(r['plan_sha256'], r['host_id'], r['platform'],
                                                     tuple(r['actions']), r['receipt_sha256'])
        # Both historical one-filter and V2 sequence receipts retain their
        # actual digest/schema. No translation may mint a replacement receipt.
        if len(receipt.actions) == 1 and isinstance(receipt.actions[0], dict) and 'resource_kind' not in receipt.actions[0]:
            _verify_receipt(plan, profile.host_resource_profile, receipt)
        else:
            _verify_sequence_receipt(plan, profile.host_resource_profile, receipt)
        receipt = resources.AppliedHostChangeReceipt(
            receipt.plan_sha256, receipt.host_id, receipt.platform,
            tuple(MappingProxyType(dict(item)) for item in receipt.actions), receipt.receipt_sha256)
    else:
        _fail('SCM_BOOTSTRAP_PHASE_INVALID', 'bootstrap phase must be APPLY or RESTORE')
    return BootstrapRequest(phase, authorization_ref, plan, receipt, hashlib.sha256(raw).hexdigest())


def submit_bootstrap(daemon, runtime, path):
    request = parse_bootstrap(read_private_bootstrap(path), daemon._windows_t037_profile)
    return daemon._submit_scm_host_control(runtime, phase=request.phase, plan=request.plan,
                                           authorization_ref=request.authorization_ref, receipt=request.receipt)
