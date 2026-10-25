"""Closed optional qualification intent; never an owner or permission."""
from __future__ import annotations

from dataclasses import dataclass, fields
import hashlib
import json
import re
from typing import Mapping

from ._execution_contract import ExecutionContractError

KEY = 't037_qualification'
SCHEMA = 'comsol-mcp.t037-qualification-recipe/1'
from ._windows_os_probe_protocol import OPERATIONS as CONTROLS
ROLES = ('server', 'worker')
_SHA = re.compile(r'[0-9a-f]{64}')
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}')
_VERSION = re.compile(r'6\.(?:3|4)\.[0-9]+\.[0-9]+')


def fail(message):
    raise ExecutionContractError('T037_QUALIFICATION_RECIPE_INVALID', message,
                                 safe_retry=False, stage='pre_dispatch')


def _plain(value):
    if isinstance(value, Mapping):
        if any(type(k) is not str for k in value): fail('material keys must be strings')
        return {k: _plain(v) for k, v in value.items()}
    if type(value) in (list, tuple): return [_plain(v) for v in value]
    if value is None or type(value) in (str, bool, int): return value
    fail('unbounded or unsupported prepared material value')


def digest(value):
    return hashlib.sha256(json.dumps(_plain(value), sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def manifest_digest(manifest):
    from ._windows_worker_bootstrap import PreparedLaunchManifest, _MANIFEST_SEAL
    if type(manifest) is not PreparedLaunchManifest or manifest._seal is not _MANIFEST_SEAL:
        fail('normal protected prepared manifest is required')
    return digest({f.name: getattr(manifest, f.name) for f in fields(manifest) if f.name != '_seal'})


@dataclass(frozen=True)
class QualificationRecipe:
    prepared_manifest_sha256: str
    runtime_id: str
    comsol_version: str
    vendor_roles: tuple[str, ...]
    os_controls: tuple[str, ...] = CONTROLS
    schema: str = SCHEMA

    def __post_init__(self):
        if (self.schema != SCHEMA or type(self.prepared_manifest_sha256) is not str
                or _SHA.fullmatch(self.prepared_manifest_sha256) is None
                or type(self.runtime_id) is not str or _ID.fullmatch(self.runtime_id) is None
                or type(self.comsol_version) is not str or _VERSION.fullmatch(self.comsol_version) is None
                or type(self.os_controls) is not tuple or self.os_controls != CONTROLS
                or type(self.vendor_roles) is not tuple or not self.vendor_roles
                or self.vendor_roles != tuple(role for role in ROLES if role in self.vendor_roles)):
            fail('qualification intent differs from the closed material/runtime/role/control contract')

    def as_dict(self):
        return {'schema': self.schema, 'prepared_manifest_sha256': self.prepared_manifest_sha256,
                'runtime_id': self.runtime_id, 'comsol_version': self.comsol_version,
                'vendor_roles': list(self.vendor_roles), 'os_controls': list(self.os_controls)}

    @property
    def sha256(self): return digest(self.as_dict())

    def require_manifest(self, manifest):
        if manifest_digest(manifest) != self.prepared_manifest_sha256:
            fail('original protected manifest does not match approved recipe')
        return manifest


def from_operator_inputs(operator_inputs):
    """Other existing operator inputs stay intact; only this optional key is parsed."""
    if not isinstance(operator_inputs, Mapping): fail('operator_inputs must be a mapping')
    if KEY not in operator_inputs: return None
    raw = operator_inputs[KEY]
    expected = {'schema', 'prepared_manifest_sha256', 'runtime_id', 'comsol_version',
                'vendor_roles', 'os_controls'}
    if type(raw) is not dict or set(raw) != expected:
        fail('qualification recipe has missing/unknown fields')
    if type(raw['vendor_roles']) is not list or type(raw['os_controls']) is not list:
        fail('qualification roles/controls must be exact arrays')
    return QualificationRecipe(raw['prepared_manifest_sha256'], raw['runtime_id'], raw['comsol_version'],
                               tuple(raw['vendor_roles']), tuple(raw['os_controls']), raw['schema'])


def from_plan(plan):
    from ._w26_os_resources import validate_host_change_plan
    return from_operator_inputs(validate_host_change_plan(plan)['operator_inputs'])
