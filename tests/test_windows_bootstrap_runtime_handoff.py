"""V2 original static pin/namespace controls. No host or runtime start."""
import copy

import pytest

from comsol_mcp import _windows_host_bootstrap as b
from comsol_mcp import _windows_scm_config_materialization as m
from tests.test_windows_scm_config_materialization import fixture, installed, no_native


def test_generated_file_create_new_collision_keeps_foreign_file_and_original_nodes():
    original,backend,_=fixture(prepared=False)
    create=backend.sdk['CreateFileW']
    foreign=original.intent.destination(m.CONFIG_PATHS[0])
    def collide(path,*args):
        if path==foreign and args[3]==1:
            backend.add(path,False,b'foreign race winner')
        return create(path,*args)
    backend.sdk['CreateFileW']=collide
    original.prepare()
    with pytest.raises(b.BootstrapError):original.apply(approved_plan_sha256=original.intent.sha256)
    assert original.state=='UNKNOWN' and backend.tree[foreign]['data']==b'foreign race winner'
    assert m.CONFIG_PATHS[0] in original.nodes and original.nodes[m.CONFIG_PATHS[0]].handle is None
    before=list(backend.calls)
    with pytest.raises(b.BootstrapError):original.restore()
    assert backend.calls==before


@pytest.mark.parametrize('fault',['raise','invalid'])
def test_pending_original_acquisition_never_adopts_name_or_reopens(fault):
    original,backend,_=fixture()
    backend.faults['CreateFileW']=fault
    with pytest.raises(b.BootstrapError):original.apply(approved_plan_sha256=original.intent.sha256)
    assert original.state=='UNKNOWN' and '' in original.nodes
    assert original.nodes[''].handle is None
    before=list(backend.calls)
    assert any(h.state=='UNKNOWN' for h in original.owner.handles)
    for call in (original.handoff_runtime,original.restore,original.acknowledge_configuration):
        with pytest.raises(b.BootstrapError):call()
    assert backend.calls==before


@pytest.mark.parametrize('phase',['generated_ack','node_assignment'])
def test_generated_known_success_ack_retains_frozen_four_bytes_and_original_restore(phase,monkeypatch):
    original,backend,_=fixture()
    if phase=='generated_ack':
        def checkpoint(event,ack):
            if event=='CONFIG_NODE_POSTIMAGE' and ack.node.relative==m.CONFIG_PATHS[0]:
                raise RuntimeError('lost generated metadata ack')
        monkeypatch.setattr(original.owner,'checkpoint',checkpoint)
    else:
        setattr_original=b._Node.__setattr__
        def assignment(self,key,value):
            if key=='label' and value is not None and self.relative==m.CONFIG_PATHS[0]:
                raise RuntimeError('native succeeded; second metadata assignment failed')
            setattr_original(self,key,value)
        monkeypatch.setattr(b._Node,'__setattr__',assignment)
    with pytest.raises(b.BootstrapError):original.apply(approved_plan_sha256=original.intent.sha256)
    assert original.state=='CONFIGURATION_ACK_PENDING'
    frozen=original._configuration.resolved
    assert frozen is not None and len(frozen.files)==4
    before=list(backend.calls)
    if phase=='node_assignment':monkeypatch.setattr(b._Node,'__setattr__',setattr_original)
    original.acknowledge_configuration()
    assert backend.calls==before and original._configuration.resolved is frozen
    original.restore()
    assert backend.tree==backend.original


def test_original_state_dacl_success_lost_ack_never_repeats_setter(monkeypatch):
    original,backend,_=fixture()
    def lost(event,ack):
        if event=='CONFIG_NODE_POSTIMAGE' and ack.node.relative=='service-state' and ack.label_call.kind=='DACL':
            raise RuntimeError('state DWORD success; metadata ack lost')
    monkeypatch.setattr(original.owner,'checkpoint',lost)
    with pytest.raises(b.BootstrapError):original.apply(approved_plan_sha256=original.intent.sha256)
    assert original.state=='CONFIGURATION_ACK_PENDING'
    before=list(backend.calls)
    original.acknowledge_configuration()
    assert backend.calls==before
    original.restore()
    assert backend.tree==backend.original


@pytest.mark.parametrize('fault',['raise','invalid'])
def test_dacl_effect_unknown_cannot_repeat_or_restore(fault):
    original,backend,_=fixture();backend.faults['DACL_SET']=fault
    with pytest.raises(b.BootstrapError):original.apply(approved_plan_sha256=original.intent.sha256)
    assert original.state=='UNKNOWN'
    before=list(backend.calls)
    for f in (original.restore,original.acknowledge_configuration,original.handoff_runtime):
        with pytest.raises(b.BootstrapError):f()
    assert backend.calls==before


@pytest.mark.parametrize('outcome',['raise','invalid','known_zero'])
def test_v2_original_writer_close_unknown_blocks_every_new_query(outcome):
    original,backend,_=installed()
    if outcome=='known_zero':backend.faults['close_known_before_write']=True
    else:backend.faults['close']=outcome
    with pytest.raises(b.BootstrapError):original.handoff_runtime()
    assert original.state=='UNKNOWN'
    before=list(backend.calls)
    for call in (original.handoff_runtime,original.restore,original.load_configurations):
        with pytest.raises(b.BootstrapError):call()
    assert backend.calls==before
    assert all(node.handle is not None for node in original.nodes.values())


def test_other_config_drift_between_bridge_and_writer_close_retains_original_writer():
    original,backend,_=installed()
    bridge=backend.bridge_node
    def drift(path,directory):
        handle=bridge(path,directory)
        backend.tree[original.intent.destination(m.CONFIG_PATHS[1])]['data']+=b'changed after initial whole-compare'
        return handle
    backend.bridge_node=drift
    with pytest.raises(b.BootstrapError):original.handoff_runtime()
    assert original.state=='UNKNOWN' and not original.nodes[''].handle.closed
    assert original.nodes[''].bridge_handle is not None and not original.nodes[''].bridge_handle.closed


@pytest.mark.parametrize('change',['recipe','source','hash','directory_manifest','resolved','version'])
def test_post_parse_and_post_freeze_drift_blocks_before_handoff_or_destroy(change):
    original,backend,_=installed()
    config=original._configuration
    if change=='recipe':config.recipe['labels']['file']['mask']=3
    elif change=='source':object.__setattr__(original.intent,'original_raw',original.intent.original_raw+b' ')
    elif change=='hash':object.__setattr__(config.resolved,'recipe_sha256','0'*64)
    elif change=='directory_manifest':config.expected_namespace=frozenset()
    elif change=='resolved':config.resolved=copy.copy(config.resolved)
    elif change=='version':object.__setattr__(original.intent,'schema',b.SCHEMA)
    before=list(backend.calls)
    with pytest.raises(b.BootstrapError):original.handoff_runtime()
    # The first guard/config check prevents any destructive call; some readonly
    # original-source comparisons may precede a pure config-drift observation.
    assert not any(c[0]=='close' for c in backend.calls[len(before):])
    assert not any(c[0] in ('DeleteService','SetFileInformationByHandle') for c in backend.calls)
