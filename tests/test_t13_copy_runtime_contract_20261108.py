"""T13/U02/U03 node.copy runtime contract: bind the actual observed full COMSOL
build consistently; never require the 6.4.0.293 literal. D1 software fake
Worker only; no native COMSOL proof and no native support is asserted.
"""
import copy
import json
import pytest
from comsol_mcp._execution_contract import ExecutionContractError, model_ref_from_mapping
from comsol_mcp import _g2_copy_subtree as subtree
from tests.test_unit_d_same_list_copy import (Collection, env, evidence, invoke, node_path)  # noqa: F401 (env is the reused fixture)

SUPPORTED_BUILDS = ['6.4.0.293', '6.3.0.290', '6.4.0.294']


def uniform_build_worker(w, build, *, only=None, after_copy=None, attribute=None):
    def override(node, r):
        if only is not None and node is not only:
            return r
        r['runtime_version'] = getattr(w, attribute, build) if attribute else build
        return r
    w.override = override


@pytest.mark.parametrize('build', SUPPORTED_BUILDS)
def test_all_receivers_same_supported_full_build_copy_allowed(env, build):
    b, w, ref, tickets = env
    uniform_build_worker(w, build)
    out = invoke(env); evidence(env, out, 't13-legal-build-' + build)
    assert out['success'], out
    d = out['data']
    assert d['status'] == 'INCOMPLETE' and d['complete'] is False
    assert d['readback']['source_prepare']['runtime_version'] == build
    assert len(tickets) == 1 and w.collection.calls == [('copy', 'copy', 'a')]
    assert w.collection.tags() == ['a', 'b', 'copy']
    assert out['execution']['revision'] == 1


def test_receiver_build_mismatch_within_one_projection_refused_before_write(env):
    b, w, ref, tickets = env
    root = w.collection.items['a']

    def override(node, r):
        r['runtime_version'] = '6.4.0.294' if node is root else '6.4.0.293'
        return r
    w.override = override
    with pytest.raises(ExecutionContractError) as caught:
        invoke(env)
    assert 'inconsistent COMSOL builds' in json.dumps(caught.value.as_dict())
    evidence(env, caught.value.as_dict(), 't13-cross-receiver-refusal')
    assert not tickets and w.collection.calls == [] and w.collection.tags() == ['a', 'b']
    assert not b.service.ledger._state_for(model_ref_from_mapping(ref)).dirty


def test_build_changes_between_prepare_and_fresh_refuses_before_copy(env, monkeypatch):
    b, w, ref, tickets = env
    w.observed_build = '6.4.0.293'
    uniform_build_worker(w, None, attribute='observed_build')
    original = b.service.execute_legacy

    def flip(*a, **kw):
        w.observed_build = '6.4.0.294'
        return original(*a, **kw)
    monkeypatch.setattr(b.service, 'execute_legacy', flip)
    out = invoke(env); evidence(env, out, 't13-prepare-fresh-build-refusal')
    assert not out['success'], out
    assert w.collection.calls == [] and w.collection.tags() == ['a', 'b'] and len(tickets) == 1
    assert out['execution']['dirty'] is False and out['execution']['revision'] == 0
    payload = json.dumps(out)
    assert 'before mutation' in payload and 'observed_builds' in payload


def test_build_changes_after_copy_readback_unknown_retains_target(env):
    b, w, ref, tickets = env
    w.observed_build = '6.4.0.293'
    uniform_build_worker(w, None, attribute='observed_build')
    w.collection.corrupt = lambda target: setattr(w, 'observed_build', '6.4.0.294')
    out = invoke(env); evidence(env, out, 't13-post-copy-build-unknown')
    assert not out['success'], out
    assert out['data']['status'] == 'UNKNOWN' and out['execution']['dirty'] is True
    assert out['error']['safe_retry'] is False
    assert len(tickets) == 1 and w.collection.calls == [('copy', 'copy', 'a')] and 'copy' in w.collection.items
    assert 'observed_builds' in json.dumps(out)


@pytest.mark.parametrize('build', ['6.2.0.287', '6.5.0.300'])
def test_unsupported_release_refused_before_write_zero_mutation(env, build):
    b, w, ref, tickets = env
    uniform_build_worker(w, build)
    with pytest.raises(ExecutionContractError) as caught:
        invoke(env)
    evidence(env, caught.value.as_dict(), 't13-unsupported-release-' + build)
    assert not tickets and w.collection.calls == [] and w.collection.tags() == ['a', 'b']
    assert not b.service.ledger._state_for(model_ref_from_mapping(ref)).dirty


def test_missing_copy_signature_refused_before_write_zero_mutation(env):
    b, w, ref, tickets = env

    def override(node, r):
        if isinstance(node, Collection):
            r['methods'] = [row for row in r['methods'] if row['method'] != 'copy']
        return r
    w.override = override
    with pytest.raises(ExecutionContractError) as caught:
        invoke(env)
    evidence(env, caught.value.as_dict(), 't13-missing-copy-signature')
    assert not tickets and w.collection.calls == [] and w.collection.tags() == ['a', 'b']


def test_match_observed_requires_one_full_build_across_projections(env):
    b, w, ref, tickets = env
    budget = subtree.new_budget(node_path('a'))
    source = subtree.observe(w, 'm', node_path('a'), budget, 'source')
    assert subtree.match_observed(source, copy.deepcopy(source), node_path('a'), node_path('a'))['observed_structural_status'] == 'OBSERVED_MATCH'
    shifted = json.loads(json.dumps(source).replace('6.4.0.293', '6.4.0.294'))
    with pytest.raises(ExecutionContractError) as caught:
        subtree.match_observed(source, shifted, node_path('a'), node_path('a'))
    assert caught.value.code == 'EXECUTION_STATE_UNKNOWN'
    assert 'COMSOL build' in str(caught.value) and caught.value.details['observed_builds'] == ['6.4.0.293', '6.4.0.294']


@pytest.mark.parametrize('build', ['6.3.0.290', '6.4.0.294'])
def test_require_structure_binds_supported_builds_not_literal(env, build):
    b, w, ref, tickets = env
    uniform_build_worker(w, build)
    projection = subtree.observe(w, 'm', node_path('a'), subtree.new_budget(node_path('a')), 'source')
    assert projection['runtime_version'] == build
    broken = copy.deepcopy(projection)
    broken['receiver_receipts'][0]['descriptor']['runtime_version'] = '6.4.0.293'
    with pytest.raises(ExecutionContractError) as caught:
        subtree.require_structure(broken, node_path('a'))
    assert 'consistently' in json.dumps(caught.value.details['required_failures'])
    incomplete = copy.deepcopy(projection)
    incomplete['runtime_version'] = '6.4'
    for receipt in incomplete['receiver_receipts']:
        receipt['descriptor']['runtime_version'] = '6.4'
    with pytest.raises(ExecutionContractError) as caught:
        subtree.require_structure(incomplete, node_path('a'))
    assert 'complete reviewed-release build' in json.dumps(caught.value.details['required_failures'])
