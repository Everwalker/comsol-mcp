"""SOFTWARE_ONLY typed authoring. No grant installation or native authority."""
import copy
from datetime import datetime, timedelta, timezone
import json
import ntpath
from types import SimpleNamespace

import pytest

from comsol_mcp import _windows_host_bootstrap as b
from comsol_mcp import _windows_scm_config_materialization as m
from comsol_mcp import _host_scoped_provider as p
from comsol_mcp._windows_t037 import parse_profile_v4_document
from tests import test_windows_scm_config_materialization as h
from tests.test_windows_scm_config_materialization import no_native


_literal_recipe_doc = h.recipe_doc
RECIPE4 = 'comsol-mcp.windows-scm-config-recipe/4'
MARKER = 'comsol-mcp.windows-original-managed-stop-grant/1'
RUNTIME_CLASS = 'comsol_mcp._session_context.SessionRuntimeConfig'
_SOFTWARE_REF = 'SOFTWARE_ONLY_not_an_issued_authorization'


def typed_recipe_doc():
    doc, source, server = _literal_recipe_doc()
    recipe = doc['provisioning']
    recipe['schema'] = RECIPE4
    project = recipe['templates'][m.CONFIG_PATHS[0]]['host_resource_profile']['project_id']
    marker = dict(schema=MARKER, platform='windows', ownership_mode='originalManagedLifetime',
                  source='FINAL_TRUSTED_PROFILE', runtime_id=dict(id='software-runtime', **{'class': RUNTIME_CLASS}))
    recipe['templates'][m.CONFIG_PATHS[1]]['grants'] = [dict(project_id=project, action='session.stop',
        authorization_ref_sha256=b.digest(_SOFTWARE_REF.encode()), scope_sha256=marker,
        expires_at_utc=(datetime.now(timezone.utc)+timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ'))]
    return doc, source, server


def expected_scope(raw, runtime_id='software-runtime'):
    profile = parse_profile_v4_document(json.loads(raw), source_sha256=b.digest(raw))
    host = profile.host_resource_profile
    return host.project_id, dict(schema=p.MANAGED_STOP_SCOPE_SCHEMA,
        runtime_id=dict(id=runtime_id, **{'class': RUNTIME_CLASS}), platform='windows',
        ownership_mode='originalManagedLifetime', host_source=dict(t037_source_sha256=b.digest(raw),
        host_resource_profile_sha256=host.sha256, host_id=host.host_id))


@pytest.mark.parametrize('existing_root', [False, True], ids=['new-root', 'existing-root'])
def test_typed_final_scope_matches_published_provider_and_four_frozen_bytes(existing_root, monkeypatch):
    monkeypatch.setattr(h, 'recipe_doc', typed_recipe_doc)
    original, backend, doc = h.installed(existing_root=existing_root)
    files = original._configuration.resolved.files
    assert tuple(path for path, raw in files) == m.CONFIG_PATHS
    project, scope = expected_scope(files[0][1])
    row = json.loads(files[1][1])['grants'][0]
    approved = doc['provisioning']['templates'][m.CONFIG_PATHS[1]]['grants'][0]
    assert row['scope_sha256'] == p.scope_sha256(project, 'session.stop', scope)
    assert {k:v for k,v in row.items() if k != 'scope_sha256'} == {
        k:v for k,v in approved.items() if k != 'scope_sha256'}
    assert MARKER not in files[1][1].decode()
    original.restore()
    assert backend.tree == backend.original


def _rows(doc):
    return doc['provisioning']['templates'][m.CONFIG_PATHS[1]]['grants']


def _stdio_identity_seam(monkeypatch, tmp_path, original, backend):
    """Extend the frozen SDK fixture's stdio seam, never its guards/results.

    Real disposable fd bytes/times/size/mode remain real. Windows stat dev/ino
    use the same synthetic legacy FileInfo domain as the fixture SDK (whose
    separate full IDs remain full-width). Unmapped/new identities stay real
    and fail comparison. No provider verifier/source/owner is substituted.
    """
    safe, fstat = p._safe_absolute, p.os.fstat
    directory = tmp_path/'profiles'
    paths = [('profiles', directory), *[(name, directory/ntpath.basename(name)) for name in m.CONFIG_PATHS]]
    identities = {}
    for relative, local in paths:
        info = local.lstat()
        volume, inode = backend.tree[original.intent.destination(relative)]['identity']
        identities[(info.st_dev, info.st_ino)] = (volume & 0xffffffff, inode & 0xffffffffffffffff)
    def converted(info):
        pair = identities.get((info.st_dev, info.st_ino))
        if pair is None: return info
        fields = {name:getattr(info,name) for name in dir(info) if name.startswith('st_')}
        fields.update(st_dev=pair[0], st_ino=pair[1])
        return SimpleNamespace(**fields)
    class StdioPath(type(directory)):
        def lstat(self): return converted(super().lstat())
    monkeypatch.setattr(p, '_safe_absolute', lambda value, field: StdioPath(str(safe(value, field))))
    monkeypatch.setattr(p, 'os', SimpleNamespace(**dict(vars(p.os), fstat=lambda fd:converted(fstat(fd)))))


@pytest.mark.parametrize('fault', [
    'recipe3', 'recipe5', 'version', 'domain', 'mode', 'source', 'source_path',
    'runtime_class', 'runtime_class_missing', 'runtime_id_missing', 'runtime_extra', 'runtime_object',
    'id_empty', 'id_bool', 'id_wildcard', 'id_path', 'id_space', 'id_long',
    'project', 'action', 'ref_missing', 'ref_bool', 'ref_upper', 'expiry_missing',
    'expiry_noncanonical', 'expiry_bool', 'expiry_far', 'row_extra', 'list_marker',
    'duplicate_typed', 'second_runtime', 'conflicting_literal', 'provider_reverse',
    'trusted_reverse', 'finite_reverse', 'callback', 'unresolved',
    'missing_schema', 'missing_platform', 'missing_ownership_mode', 'missing_source', 'missing_runtime_id',
    'extra_host', 'extra_provider', 'extra_source_sha256', 'extra_path',
    'place_trusted', 'place_memory', 'place_recipe', 'place_destination', 'place_ref',
])
def test_closed_marker_errors_are_before_bootstrap_or_any_sdk(fault):
    doc, source, server = typed_recipe_doc()
    recipe = doc['provisioning']; row = _rows(doc)[0]; marker = row['scope_sha256']
    if fault == 'recipe3': recipe['schema'] = m.SCHEMA
    elif fault == 'recipe5': recipe['schema'] = RECIPE4[:-1]+'5'
    elif fault == 'version': marker['schema'] = MARKER[:-1]+'2'
    elif fault == 'domain': marker['platform'] = 'darwin'
    elif fault == 'mode': marker['ownership_mode'] = 'adopted'
    elif fault == 'source': marker['source'] = 'caller-profile'
    elif fault == 'source_path': marker['source'] = m.CONFIG_PATHS[0]
    elif fault == 'runtime_class': marker['runtime_id']['class'] = 'caller.Runtime'
    elif fault == 'runtime_class_missing': del marker['runtime_id']['class']
    elif fault == 'runtime_id_missing': del marker['runtime_id']['id']
    elif fault == 'runtime_extra': marker['runtime_id']['callback'] = 'caller'
    elif fault == 'runtime_object': marker['runtime_id'] = 'software-runtime'
    elif fault.startswith('id_'):
        marker['runtime_id']['id'] = dict(empty='', bool=True, wildcard='*', path='C:\\runtime',
                                         space=' runtime', long='x'*129)[fault[3:]]
    elif fault == 'project': row['project_id'] = 'another-project'
    elif fault == 'action': row['action'] = 'model.close'
    elif fault == 'ref_missing': del row['authorization_ref_sha256']
    elif fault == 'ref_bool': row['authorization_ref_sha256'] = True
    elif fault == 'ref_upper': row['authorization_ref_sha256'] = 'A'*64
    elif fault == 'expiry_missing': del row['expires_at_utc']
    elif fault == 'expiry_noncanonical': row['expires_at_utc'] = '2026-01-01T00:00:00+00:00'
    elif fault == 'expiry_bool': row['expires_at_utc'] = True
    elif fault == 'expiry_far': row['expires_at_utc'] = '9999-01-01T00:00:00Z'
    elif fault == 'row_extra': row['renew'] = True
    elif fault == 'list_marker': row['scope_sha256'] = [marker]
    elif fault in ('duplicate_typed', 'second_runtime', 'conflicting_literal'):
        second = copy.deepcopy(row)
        if fault == 'second_runtime': second['scope_sha256']['runtime_id']['id'] = 'another-runtime'
        if fault == 'conflicting_literal': second['scope_sha256'] = 'f'*64
        _rows(doc).append(second)
    elif fault == 'provider_reverse': marker['provider_sha256'] = 'f'*64
    elif fault == 'trusted_reverse': recipe['templates'][m.CONFIG_PATHS[0]]['provider_sha256'] = 'f'*64
    elif fault == 'finite_reverse':
        target = next(t for t in recipe['templates'][m.CONFIG_PATHS[0]]['finite_targets']['files']
                      if t['roles'] == ['config_rx'])
        target['content_sha256'] = 'f'*64
    elif fault == 'callback': row['scope_sha256'] = {'callback': 'calculate-scope'}
    elif fault == 'unresolved': marker['runtime_id']['id'] = m._slot('service_sid')
    elif fault.startswith('missing_'): del marker[fault[8:]]
    elif fault.startswith('extra_'): marker[fault[6:]] = 'f'*64
    elif fault.startswith('place_'):
        row['scope_sha256'] = 'f'*64
        if fault == 'place_trusted': recipe['templates'][m.CONFIG_PATHS[0]]['stop'] = marker
        elif fault == 'place_memory': recipe['templates'][m.CONFIG_PATHS[2]]['stop'] = marker
        elif fault == 'place_recipe': recipe['stop'] = marker
        elif fault == 'place_destination':
            recipe['templates'][m.CONFIG_PATHS[1]]['destinations'] = [marker]
        elif fault == 'place_ref': row['authorization_ref_sha256'] = marker
    backend = h.ConfigWindows(source, server)
    before = copy.deepcopy(backend.tree)
    with pytest.raises(b.BootstrapError):
        intent = b.parse_plan(b.canonical(doc))
        b.Bootstrap(intent, backend, seal=b._TEST_SEAL).prepare()
    assert backend.tree == before and backend.service is None and backend.calls == []


def test_intent_projection_never_computes_a_provisional_scope(monkeypatch):
    calls = []
    def forbidden(*args):
        calls.append(args)
        pytest.fail('projection cannot mint a scope digest')
    monkeypatch.setattr(p, 'scope_sha256', forbidden)
    doc, _, _ = typed_recipe_doc()
    intent = b.parse_plan(b.canonical(doc))
    assert json.loads(intent.provisioning) == doc['provisioning']
    assert calls == []


@pytest.mark.parametrize('change', ['root_identity', 'trusted_bytes'])
@pytest.mark.parametrize('existing_root', [False, True], ids=['new-root', 'existing-root'])
def test_final_original_inputs_change_digest_without_a_projection_source(change, existing_root, monkeypatch):
    monkeypatch.setattr(h, 'recipe_doc', typed_recipe_doc)
    first, _, _ = h.installed(existing_root=existing_root)
    first_files = first._configuration.resolved.files
    first.restore()
    if change == 'root_identity':
        initial = h.ConfigWindows.__init__
        def shifted(self, *args):
            initial(self, *args)
            self.next_id += 77  # Synthetic original SDK allocator; no output/owner replacement.
        monkeypatch.setattr(h.ConfigWindows, '__init__', shifted)
    else:
        def changed_recipe():
            doc, source, server = typed_recipe_doc()
            doc['provisioning']['templates'][m.CONFIG_PATHS[0]]['job_limits']['process_memory_limit_bytes'] = 8192
            return doc, source, server
        monkeypatch.setattr(h, 'recipe_doc', changed_recipe)
    observed = []
    scope_digest = p.scope_sha256
    def record(project, action, scope):
        observed.append(copy.deepcopy(scope))
        return scope_digest(project, action, scope)
    monkeypatch.setattr(p, 'scope_sha256', record)
    second, _, _ = h.installed(existing_root=existing_root)
    files = second._configuration.resolved.files
    project, expected = expected_scope(files[0][1])
    assert observed == [expected]  # No validation/projection call, only final B/R/H.
    assert files[0][1] != first_files[0][1]
    assert json.loads(files[1][1])['grants'][0]['scope_sha256'] != json.loads(first_files[1][1])['grants'][0]['scope_sha256']
    if change == 'trusted_bytes':
        assert expected['host_source']['host_resource_profile_sha256'] == expected_scope(first_files[0][1])[1]['host_source']['host_resource_profile_sha256']
    assert expected['host_source']['host_id'] == h.HOST['hostname'] != h.HOST['machine_fingerprint_sha256']
    second.restore()


def test_all_four_bytes_frozen_before_first_generated_config_write_and_trusted_canonicalized_once(monkeypatch):
    monkeypatch.setattr(h, 'recipe_doc', typed_recipe_doc)
    original, backend, _ = h.fixture()
    canonical = b.canonical; actual_trusted = []; writes = []
    def encode(value):
        raw = canonical(value)
        if (type(value) is dict and value.get('schema') == 'comsol-mcp.windows-t037-profile/4'
                and value['host_resource_profile']['windows_isolation']['service_sid'] == h.SERVICE_SID):
            actual_trusted.append(raw)
        return raw
    monkeypatch.setattr(b, 'canonical', encode)
    create = original._configuration.native.create_bytes
    def write(node, raw, descriptor):
        resolved = original._configuration.resolved
        assert type(resolved) is m.ResolvedConfiguration and len(resolved.files) == 4
        assert all(type(payload) is bytes for _, payload in resolved.files)
        assert raw == dict(resolved.files)[node.relative]
        if not writes:
            assert not any(path in original.nodes for path in m.CONFIG_PATHS[1:])
            assert actual_trusted == [resolved.files[0][1]]
            assert type(json.loads(resolved.files[1][1])['grants'][0]['scope_sha256']) is str
        writes.append(node.relative)
        return create(node, raw, descriptor)
    monkeypatch.setattr(original._configuration.native, 'create_bytes', write)
    original.apply(approved_plan_sha256=original.intent.sha256)
    assert writes == list(m.CONFIG_PATHS) and len(actual_trusted) == 1
    original.restore()
    assert backend.tree == backend.original


@pytest.mark.parametrize('fault', ['normalizer_raise', 'normalizer_bad_hash', 'root_id', 'root_sd', 'service_sid', 'source_parse'])
def test_late_derivation_failure_retains_original_nodes_without_partial_config_write(fault, monkeypatch):
    monkeypatch.setattr(h, 'recipe_doc', typed_recipe_doc)
    original, backend, _ = h.fixture()
    if fault.startswith('normalizer_'):
        def fail(*args):
            if fault == 'normalizer_raise': raise ValueError('SOFTWARE_ONLY late normalizer refusal')
            return True
        monkeypatch.setattr(p, 'scope_sha256', fail)
    elif fault.startswith('root_') or fault == 'service_sid':
        compile_original = m._Configuration.compile
        def changed(config, sid):
            row = backend.tree[original.nodes['project'].path]
            if fault == 'root_id': row['identity'] = (row['identity'][0], row['identity'][1]+1)
            elif fault == 'root_sd': row['sd'] = h.raw_sd(True, broad=True)
            else: config.service_sid = 'S-1-5-80-6-7-8-9-10'
            return compile_original(config, sid)
        monkeypatch.setattr(m._Configuration, 'compile', changed)
    else:
        from comsol_mcp import _windows_t037 as win
        parse = win.parse_profile_v4_document
        def strict(document, **kwargs):
            if document['host_resource_profile']['windows_isolation']['service_sid'] == h.SERVICE_SID:
                raise ValueError('SOFTWARE_ONLY final strict parser refusal')
            return parse(document, **kwargs)
        monkeypatch.setattr(win, 'parse_profile_v4_document', strict)
    with pytest.raises(b.BootstrapError): original.apply(approved_plan_sha256=original.intent.sha256)
    assert original.state == 'UNKNOWN' and original.owner.uncertain
    assert original in b.retained_bootstrap_owners() and original.nodes
    assert all(node.handle is not None for node in original.nodes.values())
    assert not any(path in original.nodes or original.intent.destination(path) in backend.tree for path in m.CONFIG_PATHS)
    calls = list(backend.calls)
    for attempt in (original.restore, original.handoff_runtime):
        with pytest.raises(b.BootstrapError): attempt()
    assert backend.calls == calls


@pytest.mark.parametrize('existing_root', [False, True], ids=['new-root', 'existing-root'])
@pytest.mark.parametrize('expired', [False, True], ids=['current', 'expired'])
def test_four_normal_loaders_nonempty_grant_and_real_provider_verifier(existing_root, expired, monkeypatch, tmp_path, record_testsuite_property):
    from comsol_mcp import _windows_t037 as win, _windows_disk_resources as disk
    from comsol_mcp import _w26_memory_resources as memory, _w26_os_resources as resources
    def recipe():
        doc, source, server = typed_recipe_doc()
        if expired: _rows(doc)[0]['expires_at_utc'] = '2000-01-01T00:00:00Z'
        return doc, source, server
    monkeypatch.setattr(h, 'recipe_doc', recipe)
    original, backend, sdk, _ = h._normal_loader_environment(monkeypatch, tmp_path, existing_root=existing_root)
    _stdio_identity_seam(monkeypatch, tmp_path, original, backend)
    loaded = original.load_configurations()
    assert tuple(type(obj) for obj in loaded) == (win.WindowsT037ProfileV2, p.HostScopedProvider,
        memory.TrustedMemoryResourceConfig, resources.TrustedDiskResourceConfig)
    assert h.targets._ORIGINS[id(loaded[0])][0]() is loaded[0]
    assert disk.is_windows_disk_config(loaded[3]) and loaded[2].trusted_startup_source
    files = original._configuration.resolved.files
    project, scope = expected_scope(files[0][1])
    row = json.loads(files[1][1])['grants'][0]
    approved = json.loads(original.intent.provisioning)['templates'][m.CONFIG_PATHS[1]]['grants'][0]
    assert row['authorization_ref_sha256'] == approved['authorization_ref_sha256']
    assert row['expires_at_utc'] == approved['expires_at_utc']
    assert loaded[1].verify_scope(project, 'session.stop', scope, _SOFTWARE_REF) is (not expired)
    assert not loaded[1].verify_scope(project, 'session.stop', scope, 'SOFTWARE_ONLY_wrong_reference')
    assert not loaded[1].verify_scope('wrong-project', 'session.stop', scope, _SOFTWARE_REF)
    changed = copy.deepcopy(scope); changed['host_source']['t037_source_sha256'] = 'f'*64
    assert not loaded[1].verify_scope(project, 'session.stop', changed, _SOFTWARE_REF)
    assert not loaded[1](project, _SOFTWARE_REF)  # No broader policy authority.
    assert loaded[1].config_sha256 == b.digest(files[1][1])
    assert b.parse_plan(original.intent.original_raw) == original.intent  # No current-expiry cleanup gate.
    readonly = {0x20000,0x1200A0,0x120080,0x120081,0x100081}
    assert sdk.access and {mask for _, mask in sdk.access} <= readonly
    assert len(original._configuration.loader_calls) == 4
    record_testsuite_property('typed_stop_four_loader_'+str(existing_root)+'_'+str(expired), json.dumps(dict(
        scope='SOFTWARE_ONLY_NOT_NATIVE', root_mode=original._configuration.recipe['project_root']['kind'],
        files=[dict(path=path, sha256=b.digest(raw)) for path, raw in files],
        expected_scope_sha256=p.scope_sha256(project, 'session.stop', scope),
        expiry=row['expires_at_utc'], expired=expired, source_hash=loaded[0].source_sha256,
        kinds=[type(obj).__module__+'.'+type(obj).__qualname__ for obj in loaded],
        requested_access=sorted({mask for _,mask in sdk.access}), actual_authority='NOT_ISSUED'), sort_keys=True))
    # The ordinary provider remains pinned even through the software stdio seam.
    local = tmp_path/'profiles'/'host-provider.json'
    local.write_bytes(local.read_bytes()+b' ')
    with pytest.raises(h.ExecutionContractError) as error:
        loaded[1].verify_scope(project, 'session.stop', scope, _SOFTWARE_REF)
    assert error.value.code == 'AUTHORIZATION_STATE_UNKNOWN'
    loaded[1].close(); original.restore()
    assert backend.tree == backend.original


def test_recipe3_literal_grants_and_expired_cleanup_remain_literal(monkeypatch):
    def literal():
        doc, source, server = _literal_recipe_doc()
        recipe = doc['provisioning']
        project = recipe['templates'][m.CONFIG_PATHS[0]]['host_resource_profile']['project_id']
        recipe['templates'][m.CONFIG_PATHS[1]]['grants'] = [dict(project_id=project, action='session.stop',
            authorization_ref_sha256='1'*64, scope_sha256='2'*64, expires_at_utc='2000-01-01T00:00:00Z')]
        return doc, source, server
    monkeypatch.setattr(h, 'recipe_doc', literal)
    original, backend, doc = h.installed()
    assert m.SCHEMA.endswith('/3') and m.SCHEMA_V4.endswith('/4')
    assert json.loads(original._configuration.resolved.files[1][1]) == doc['provisioning']['templates'][m.CONFIG_PATHS[1]]
    assert b.parse_plan(original.intent.original_raw) == original.intent
    original.restore()
    assert backend.tree == backend.original


@pytest.mark.parametrize('typed', [False, True], ids=['literal-only', 'typed-with-other-literal'])
def test_recipe4_retains_approved_unrelated_literal_grants_without_expansion(typed, monkeypatch):
    literal = dict(project_id='other-approved-project', action='model.close',
        authorization_ref_sha256='1'*64, scope_sha256='2'*64, expires_at_utc='2000-01-01T00:00:00Z')
    def recipe():
        doc, source, server = typed_recipe_doc()
        if not typed: _rows(doc).clear()
        _rows(doc).append(copy.deepcopy(literal))
        return doc, source, server
    monkeypatch.setattr(h, 'recipe_doc', recipe)
    original, backend, _ = h.installed()
    grants = json.loads(original._configuration.resolved.files[1][1])['grants']
    assert grants[-1] == literal and len(grants) == (2 if typed else 1)
    original.restore()
    assert backend.tree == backend.original


def test_resolved_diagnostic_or_mutated_approved_marker_cannot_become_an_owner(monkeypatch):
    monkeypatch.setattr(h, 'recipe_doc', typed_recipe_doc)
    original, backend, _ = h.fixture()
    config = original._configuration
    forged = m.ResolvedConfiguration(b.digest(config.recipe_raw), h.SERVICE_SID, (1,2), ())
    with pytest.raises(b.BootstrapError): m.prepare_configuration(forged)
    config.recipe['templates'][m.CONFIG_PATHS[1]]['grants'][0]['scope_sha256']['runtime_id']['id'] = 'replacement'
    before = copy.deepcopy(backend.tree); calls = list(backend.calls)
    with pytest.raises(b.BootstrapError): original.apply(approved_plan_sha256=original.intent.sha256)
    assert backend.tree == before and backend.calls == calls and backend.service is None
