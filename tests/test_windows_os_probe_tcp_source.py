"""Source-order + actual codec controls, NOT a JVM/socket implementation test.

Fault frames below are inert leaf inputs projected from the production source's
phase/capture statement positions. No Python socket, Java interpreter/compiler
or execution/denial authority is supplied by this fixture.
"""
import hashlib
import ipaddress
from pathlib import Path
import re

import pytest
from comsol_mcp import _windows_os_probe_protocol as wire
from comsol_mcp import _windows_worker_bootstrap as prepared

NONCE='a'*32
RECIPE='b'*64


def source_flow():
    source=Path(prepared.__file__).parent/'worker_java/PersistentComsolWorker.java'
    raw=source.read_bytes()
    assert hashlib.sha256(raw).hexdigest()==prepared.WORKER_SOURCE_SHA256
    body=raw.decode().split('          o.socket.acquisition=2; o.socketObject=new java.net.Socket();',1)[1]
    body=body.split('          o.input.acquisition=2; o.inputObject=o.socketObject.getInputStream();',1)[0]
    body=re.sub(r'//[^\n]*','',body)
    sites=dict(bind=body.index('o.socketObject.bind('),bound_read=body.index('o.socketObject.getLocalSocketAddress()'),
        connect=body.index('o.socketObject.connect('),setup=body.index('o.socketObject.setSoTimeout('),
        local_read=body.rindex('o.socketObject.getLocalSocketAddress()'),remote_read=body.index('o.socketObject.getRemoteSocketAddress()'))
    capture=body.index('o.localAddress=bound.getAddress().getAddress(); o.localPort=bound.getPort();')
    def phase(site):
        assignments=re.findall(r'o\.phase=(\d+);',body[:sites[site]])
        return wire.PHASES[int(assignments[-1])] if assignments else 'OPEN'
    return body,sites,capture,phase


def test_actual_java_connect_phase_has_one_call_and_immediate_return_boundary():
    body,sites,capture,phase=source_flow()
    assert sites['bind']<sites['bound_read']<capture<sites['connect']<sites['setup']<sites['remote_read']
    assert body.count('o.socketObject.connect(')==1
    assert phase('bind')==phase('bound_read')=='OPEN'
    assert phase('connect')=='CONNECT'
    assert phase('setup')==phase('local_read')==phase('remote_read')=='ECHO'
    tail=body[body.index('TIMEOUT_MS);',sites['connect'])+len('TIMEOUT_MS);'):].lstrip()
    assert tail.startswith('o.phase=7;')  # before any throwable connected-socket setup
    assert 'boolean loopback=address.isLoopbackAddress();' in body
    assert 'new byte[address.getAddress().length]' in body and 'loopbackBytes[loopbackBytes.length-1]=1;' in body
    assert 'if(loopbackBytes.length==4) loopbackBytes[0]=127;' in body
    assert 'new java.net.InetSocketAddress(bindAddress,0)' in body
    assert 'local.getPort()!=o.localPort' in body
    assert '!java.util.Arrays.equals(local.getAddress().getAddress(),o.localAddress)' in body
    assert 'o.remoteAddress=remote.getAddress().getAddress();' in body  # no intent-as-observed-peer


@pytest.mark.parametrize('address',['127.0.0.1','::1'])
@pytest.mark.parametrize('leaf',['bind','bound_read','connect','setup','local_read','remote_read'])
def test_source_bound_leaf_failure_frames_preserve_stage_and_preconnect_tuple(address,leaf):
    body,sites,capture,phase=source_flow()
    packed=ipaddress.ip_address(address).packed
    request=dict(version=1,nonce=NONCE,recipe_sha256=RECIPE,records=[dict(
        index=0,operation='TCP_ECHO',address=packed,port=32000)])
    row=wire.empty_observation(0,'TCP_ECHO')
    row.update(entered=True,phase=phase(leaf),exception_class='java.io.IOException')
    row['socket'].update(acquisition='RETURNED',close='RETURNED')
    if capture<sites[leaf]:row['local_endpoint']=dict(address=packed,port=45000)
    response=dict(version=1,nonce=NONCE,recipe_sha256=RECIPE,frame_status='PARSED',exception_class='',
        stdin=dict(acquisition='RETURNED',close='RETURNED',close_exception_class=''),
        output_observation='UNVERIFIED_SELF_OUTPUT',records=[row])
    actual=wire.decode_response(wire.encode_response(response),expected_nonce=NONCE,
        expected_recipe_sha256=RECIPE,expected_request=request)['records'][0]
    assert actual['remote_endpoint'] is None and actual['body_returned'] is False
    if leaf in ('bind','bound_read'):
        assert actual['phase']=='OPEN' and actual['local_endpoint'] is None
    else:
        assert actual['local_endpoint']==dict(address=packed,port=45000)
        assert actual['phase']==('CONNECT' if leaf=='connect' else 'ECHO')
    assert response['output_observation']=='UNVERIFIED_SELF_OUTPUT'
    assert not any(key in response for key in ('C','qualified','permit','resume'))


@pytest.mark.parametrize('address',['192.0.2.1','2001:db8::1'])
def test_nonloopback_retains_original_failed_connect_missing_tuple_limitation(address):
    body,sites,capture,phase=source_flow()
    assert not ipaddress.ip_address(address).is_loopback
    # Binding and first tuple writes are in the conditional loopback block.
    branch=body[body.index('if(loopback) {'):sites['connect']]
    assert branch.rfind('}')>branch.index('o.localAddress=bound')
    assert phase('connect')=='CONNECT'
    row=wire.empty_observation(0,'TCP_ECHO')
    row.update(entered=True,phase='CONNECT',exception_class='java.net.ConnectException')
    assert row['local_endpoint'] is None and row['remote_endpoint'] is None


def test_codec_remains_data_only_for_connect_close_failure_and_source_tuple():
    source_flow()
    row=wire.empty_observation(0,'TCP_ECHO')
    row.update(entered=True,phase='CONNECT',exception_class='java.net.ConnectException',
        local_endpoint=dict(address=bytes((127,0,0,1)),port=45000))
    row['socket'].update(acquisition='RETURNED',close='THREW',close_exception_class='java.io.IOException')
    value=dict(version=1,nonce=NONCE,recipe_sha256=RECIPE,frame_status='PARSED',exception_class='',
        stdin=dict(acquisition='RETURNED',close='RETURNED',close_exception_class=''),
        output_observation='UNVERIFIED_SELF_OUTPUT',records=[row])
    decoded=wire.decode_response(wire.encode_response(value),expected_nonce=NONCE,expected_recipe_sha256=RECIPE)
    assert decoded['records'][0]['socket']['close']=='THREW'
    assert decoded['records'][0]['local_endpoint']==row['local_endpoint']
    assert decoded['output_observation']=='UNVERIFIED_SELF_OUTPUT'
