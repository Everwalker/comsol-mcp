"""Pure accepted-codec/event interface counter. No Java, socket, SDK or C proof."""
import json
from dataclasses import fields
from comsol_mcp import _windows_os_probe_protocol as wire
from comsol_mcp import _windows_t037_events as events


def test_connect_failure_frame_does_not_supply_original_local_tuple_for_event_correlator(tmp_path):
    nonce='a'*32;recipe='b'*64
    request=dict(version=1,nonce=nonce,recipe_sha256=recipe,records=[dict(
        index=0,operation='TCP_ECHO',address=bytes((127,0,0,1)),port=32000)])
    row=wire.empty_observation(0,'TCP_ECHO')
    row.update(entered=True,phase='CONNECT',exception_class='java.net.SocketTimeoutException',
               socket=dict(acquisition='RETURNED',close='RETURNED',close_exception_class=''))
    response=dict(version=1,nonce=nonce,recipe_sha256=recipe,frame_status='PARSED',exception_class='',
        stdin=dict(acquisition='RETURNED',close='RETURNED',close_exception_class=''),
        output_observation='UNVERIFIED_SELF_OUTPUT',records=[row])
    decoded=wire.decode_response(wire.encode_response(response),expected_nonce=nonce,
        expected_recipe_sha256=recipe,expected_request=request)
    actual=decoded['records'][0]
    assert actual['entered'] and actual['socket']['close']=='RETURNED'
    assert actual['local_endpoint'] is None and actual['remote_endpoint'] is None
    # A matching-looking event has a port, but using that event to invent the
    # original operation's missing source-port expectation would be circular.
    values={f.name:None for f in fields(events.WfpDropEvent)}
    values.update(event_type=events.FWPM_NET_EVENT_TYPE_CLASSIFY_DROP,flags=0,
        timestamp_filetime_100ns=110,ip_version=0,ip_protocol=6,
        local_address='127.0.0.1',remote_address='127.0.0.1',local_port=45000,
        remote_port=32000,app_id_bytes_hex='61',package_sid='S-1-15-2-123',filter_id=10,
        layer_id=20,direction='OUT',is_loopback=True,event_reasons=())
    observed=events.WfpNetEventObservation(state='OBSERVED',diagnostic_state='DIAGNOSTIC_ONLY',
        image_path=r'C:\JDK\java.exe',app_id_bytes_hex='61',app_id_sha256=None,
        collection_enabled=True,window_start_filetime_100ns=100,window_end_filetime_100ns=120,
        enum_create_started_filetime_100ns=100,enum_create_returned_filetime_100ns=120,pages=1,
        records_returned=1,events=(events.WfpDropEvent(**values),),exhausted=True,truncated=False,
        reason_codes=(),sdk_statuses=())
    expected=events.EventCorrelationExpectation(filter_id=10,layer_id=20,ip_version=0,protocol=6,
        local_address='127.0.0.1',local_port=None,remote_address='127.0.0.1',remote_port=32000,
        direction='OUT',start_filetime_100ns=100,end_filetime_100ns=120,app_id_bytes=b'a',
        package_sid='S-1-15-2-123')
    result=events.diagnose_event_correlation(observed,expected)
    assert result.state=='NO_MATCH' and 'EXPECTED_FIELDS_OUT_OF_BOUNDS' in result.reason_codes
    assert not result.blocking_eligible
    (tmp_path/'actual-codec-correlation.json').write_text(json.dumps(dict(
        scope='PURE_SOFTWARE_INTERFACE_COUNTER_NOT_OBSERVED_OS_DENIAL',
        local_endpoint=actual['local_endpoint'],remote_endpoint=actual['remote_endpoint'],
        correlation=result.as_dict(),native=False),indent=1)+'\n')
