"""Portable inline protocol controls. No _state/host plan input is required."""
import copy
import hashlib
import struct

import pytest

from comsol_mcp import _windows_os_probe_protocol as p

NONCE = '0123456789abcdef0123456789abcdef'
RECIPE = 'a' * 64


def request():
    return {'version': 1, 'nonce': NONCE, 'recipe_sha256': RECIPE, 'records': [
        {'index': 0, 'operation': 'READ_HASH', 'path': '/owned/input.bin', 'maxbytes': p.DEFAULT_READ_BYTES},
        {'index': 1, 'operation': 'CREATE_NEW', 'path': '/owned/' + p.create_basename(NONCE, 1)},
        {'index': 2, 'operation': 'TCP_ECHO', 'address': b'\x7f\0\0\1', 'port': 49123},
    ]}


def response():
    row = p.empty_observation(0, 'READ_HASH')
    row.update(entered=True, phase='DONE', body_returned=True,
               read_count=3, read_sha256=hashlib.sha256(b'abc').hexdigest(), eof_observed=True)
    row['input'].update(acquisition='RETURNED', close='RETURNED')
    return {'version': 1, 'nonce': NONCE, 'recipe_sha256': RECIPE, 'frame_status': 'PARSED',
            'exception_class': '', 'stdin': {'acquisition': 'RETURNED', 'close': 'RETURNED', 'close_exception_class': ''},
            'output_observation': 'UNVERIFIED_SELF_OUTPUT', 'records': [row]}


def test_request_response_symmetric_closed_wire_and_data_only():
    assert p.read_hash_record(0, '/owned/input.bin')['maxbytes'] == 1024 * 1024
    req = request()
    assert p.decode_request(p.encode_request(req), expected_nonce=NONCE, expected_recipe_sha256=RECIPE) == req
    value = response()
    assert p.decode_response(p.encode_response(value), expected_nonce=NONCE,
                             expected_recipe_sha256=RECIPE, expected_request={**req, 'records': req['records'][:1]}) == value
    assert not any(name in value for name in ('permit', 'PASS', 'C', 'qualified', 'resume'))
    assert p.MAX_REQUEST == 32768 and p.MAX_RESPONSE == 65536 and p.MAX_RECORDS == 16


@pytest.mark.parametrize('damage', ['top_extra', 'record_extra', 'bad_op', 'bool_index', 'dup_index',
    'too_many', 'wrong_nonce', 'bad_recipe', 'bad_version', 'relative', 'unc', 'dotdot', 'nul',
    'surrogate', 'path_too_long', 'dns', 'zero_port', 'oversize_read', 'bool_read', 'wrong_create'])
def test_request_rejects_closed_shape_type_and_finite_bound_damage(damage):
    value = request()
    if damage == 'top_extra': value['permit'] = True
    elif damage == 'record_extra': value['records'][0]['override'] = True
    elif damage == 'bad_op': value['records'][0]['operation'] = 'EXEC'
    elif damage == 'bool_index': value['records'][0]['index'] = True
    elif damage == 'dup_index': value['records'][1]['index'] = 0
    elif damage == 'too_many': value['records'] = [value['records'][0]] * 17
    elif damage == 'wrong_nonce': value['nonce'] = NONCE.upper()
    elif damage == 'bad_recipe': value['recipe_sha256'] = 'g' * 64
    elif damage == 'bad_version': value['version'] = True
    elif damage == 'relative': value['records'][0]['path'] = 'relative/file'
    elif damage == 'unc': value['records'][0]['path'] = r'\\host\share\file'
    elif damage == 'dotdot': value['records'][0]['path'] = '/owned/../escape'
    elif damage == 'nul': value['records'][0]['path'] = '/owned/a\0b'
    elif damage == 'surrogate': value['records'][0]['path'] = '/owned/\ud800'
    elif damage == 'path_too_long': value['records'][0]['path'] = '/' + 'a' * 4096
    elif damage == 'dns': value['records'][2]['address'] = 'localhost'
    elif damage == 'zero_port': value['records'][2]['port'] = 0
    elif damage == 'oversize_read': value['records'][0]['maxbytes'] = p.MAX_READ_BYTES + 1
    elif damage == 'bool_read': value['records'][0]['maxbytes'] = True
    else: value['records'][1]['path'] = '/owned/other-file'
    with pytest.raises(p.ProtocolError): p.encode_request(value)


@pytest.mark.parametrize('damage', ['trailing', 'truncated', 'bad_length', 'version', 'utf8', 'unknown_record', 'record_trailing'])
def test_decoder_rejects_actual_wire_damage(damage):
    raw = bytearray(p.encode_request(request()))
    # prefix4 + magic4 + version2 + identity96 + count2 => first record index at108.
    if damage == 'trailing': raw.extend(b'x')
    elif damage == 'truncated': raw.pop()
    elif damage == 'bad_length': raw[:4] = (p.MAX_REQUEST + 1).to_bytes(4, 'big')
    elif damage == 'version': raw[9] = 2
    elif damage == 'utf8': raw[116] = 255  # first path byte, after record header + u16 text size.
    elif damage == 'unknown_record': raw[109] = 99
    else:
        # A record with an otherwise valid request plus extra field is rejected.
        length = int.from_bytes(raw[110:114], 'big')
        raw[110:114] = (length + 1).to_bytes(4, 'big')
        raw.insert(114 + length, 0)
        raw[:4] = (len(raw) - 4).to_bytes(4, 'big')
    with pytest.raises(p.ProtocolError): p.decode_request(bytes(raw))


def test_frame_bound_is_total_bytes_and_sixteen_records_do_not_raise_limits():
    value = request()
    value['records'] = [{'index': i, 'operation': 'READ_HASH', 'path': '/owned/' + str(i),
                         'maxbytes': p.MAX_READ_BYTES} for i in range(16)]
    assert p.decode_request(p.encode_request(value)) == value
    for row in value['records']: row['path'] = '/' + 'a' * 4090
    with pytest.raises(p.ProtocolError): p.encode_request(value)
    ipv6 = request(); ipv6['records'] = [{'index': 0, 'operation': 'TCP_ECHO', 'address': b'\0' * 15 + b'\1', 'port': 1}]
    assert p.decode_request(p.encode_request(ipv6)) == ipv6


@pytest.mark.parametrize('damage', ['wrong_nonce', 'wrong_recipe', 'partial', 'trailing', 'extra_field',
    'invented_permit', 'not_entered_facts', 'wrong_record', 'close_claim', 'unbounded_count', 'self_output'])
def test_response_unknown_partial_or_mismatched_bytes_are_never_authenticated(damage):
    value = response()
    if damage == 'extra_field': value['records'][0]['new_field'] = 1
    elif damage == 'invented_permit': value['permit'] = True
    elif damage == 'not_entered_facts': value['records'][0]['entered'] = False
    elif damage == 'close_claim': value['records'][0]['input']['close_exception_class'] = 'java.io.IOException'
    elif damage == 'unbounded_count': value['records'][0]['read_count'] = 2**40
    elif damage == 'self_output': value['output_observation'] = 'CLOSED'
    if damage in ('extra_field', 'invented_permit', 'not_entered_facts', 'close_claim', 'unbounded_count', 'self_output'):
        with pytest.raises(p.ProtocolError): p.encode_response(value)
        return
    raw = p.encode_response(value)
    if damage == 'partial': raw = raw[:-1]
    elif damage == 'trailing': raw += b'x'
    nonce = 'b' * 32 if damage == 'wrong_nonce' else NONCE
    recipe = 'b' * 64 if damage == 'wrong_recipe' else RECIPE
    req = request(); req['records'] = req['records'][:1]
    if damage == 'wrong_record': req['records'][0]['index'] = 1
    with pytest.raises(p.ProtocolError):
        p.decode_response(raw, expected_nonce=nonce, expected_recipe_sha256=recipe, expected_request=req)


def test_explicit_unknown_resource_and_exception_remain_observations():
    value = response(); row = value['records'][0]
    row.update(phase='CLOSE', body_returned=True)
    row['input'].update(close='THREW', close_exception_class='java.io.IOException')
    decoded = p.decode_response(p.encode_response(value), expected_nonce=NONCE, expected_recipe_sha256=RECIPE)
    assert decoded == value and decoded['output_observation'] == 'UNVERIFIED_SELF_OUTPUT'


# R3 response-only expected-identity controls; the original 41 nodes above are byte-exact.
@pytest.mark.parametrize('nonce,recipe', [
    pytest.param(None, None, id='none_none'),
    pytest.param(None, RECIPE, id='nonce_none'),
    pytest.param(NONCE, None, id='recipe_none'),
    pytest.param(NONCE.encode('ascii'), RECIPE, id='nonce_bytes'),
    pytest.param(NONCE, RECIPE.encode('ascii'), id='recipe_bytes'),
    pytest.param(False, RECIPE, id='nonce_bool'),
    pytest.param(NONCE, 64, id='recipe_int'),
    pytest.param(NONCE[:-1], RECIPE, id='nonce_short'),
    pytest.param(NONCE.upper(), RECIPE, id='nonce_upper'),
    pytest.param(NONCE, RECIPE[:-1], id='recipe_short'),
    pytest.param(NONCE, 'G' * 64, id='recipe_nonhex'),
])
def test_response_requires_valid_expected_identity_even_without_request(nonce, recipe):
    raw = p.encode_response(response())
    with pytest.raises(p.ProtocolError):
        p.decode_response(raw, expected_nonce=nonce, expected_recipe_sha256=recipe)


@pytest.mark.parametrize('changed', ['nonce', 'recipe', 'both'])
def test_response_absent_expectation_cannot_bind_foreign_identity_same_roster(changed):
    value = response()
    foreign = request(); foreign['records'] = foreign['records'][:1]
    if changed in ('nonce', 'both'): foreign['nonce'] = 'b' * 32
    if changed in ('recipe', 'both'): foreign['recipe_sha256'] = 'c' * 64
    assert [(r['index'], r['operation']) for r in value['records']] == [
        (r['index'], r['operation']) for r in foreign['records']]
    assert (foreign['nonce'], foreign['recipe_sha256']) != (value['nonce'], value['recipe_sha256'])
    assert p.decode_request(p.encode_request(foreign)) == foreign  # Intentional request data-only mode.
    with pytest.raises(p.ProtocolError):
        p.decode_response(p.encode_response(value), expected_nonce=None,
                          expected_recipe_sha256=None, expected_request=foreign)


def test_response_valid_expected_binding_and_request_unbound_mode_remain_usable():
    value = response(); req = request(); req['records'] = req['records'][:1]
    assert p.decode_request(p.encode_request(req)) == req
    raw = p.encode_response(value)
    assert p.decode_response(raw, expected_nonce=NONCE, expected_recipe_sha256=RECIPE) == value
    assert p.decode_response(raw, expected_nonce=NONCE, expected_recipe_sha256=RECIPE,
                             expected_request=req) == value
    foreign = copy.deepcopy(req); foreign['nonce'] = 'b' * 32; foreign['recipe_sha256'] = 'c' * 64
    with pytest.raises(p.ProtocolError):
        p.decode_response(raw, expected_nonce=NONCE, expected_recipe_sha256=RECIPE, expected_request=foreign)
