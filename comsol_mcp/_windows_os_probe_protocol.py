"""Closed v1 fixed-JDK OS-observation wire data. No permission or C issuer.

Paths/recipe hashes are caller data, not authorization or filesystem identity.
Output is evidence to be checked by a future original parent/coordinator.
"""
from __future__ import annotations

import re

VERSION = 1
MAIN_CLASS = 'comsol_mcp.worker_java.PersistentComsolWorker$OsQualificationProbe'
MAX_REQUEST = 32 * 1024
MAX_RESPONSE = 64 * 1024
MAX_RECORDS = 16
MAX_PATH = 4096
MAX_EXCEPTION = 128
DEFAULT_READ_BYTES = 1024 * 1024
MAX_READ_BYTES = 16 * 1024 * 1024
CONNECT_TIMEOUT_MS = READ_TIMEOUT_MS = 1000
OPERATIONS = ('READ_HASH', 'CREATE_NEW', 'TCP_ECHO')
PHASES = ('NOT_ENTERED', 'VALIDATE', 'OPEN', 'READ', 'WRITE', 'FLUSH', 'CONNECT', 'ECHO', 'CLOSE', 'DONE')
ACQUISITIONS = ('NOT_ENTERED', 'RETURNED', 'THREW', 'BORROWED')
CLOSES = ('NOT_ENTERED', 'RETURNED', 'THREW', 'NOT_OWNED')
_HEX32 = re.compile(r'[0-9a-f]{32}\Z')
_HEX64 = re.compile(r'[0-9a-f]{64}\Z')
_EXCEPTION = re.compile(r'[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*\Z')


class ProtocolError(ValueError):
    pass


def _fail(message):
    raise ProtocolError(message)


def _keys(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        _fail('closed record fields required')


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        _fail('integer outside fixed bound')
    return value


def _boolean(value):
    if type(value) is not bool:
        _fail('strict boolean required')
    return value


def _identity(nonce, recipe):
    if type(nonce) is not str or not _HEX32.fullmatch(nonce):
        _fail('nonce must be32 lower hex')
    if type(recipe) is not str or not _HEX64.fullmatch(recipe):
        _fail('recipe SHA must be64 lower hex')


def _path(value):
    if type(value) is not str or not value or '\0' in value:
        _fail('absolute local path text required')
    try:
        raw = value.encode('utf-8', 'strict')
    except UnicodeError as exc:
        raise ProtocolError('strict UTF8 required') from exc
    if (len(raw) > MAX_PATH or value.startswith(('\\', '//'))
            or not (value.startswith('/') or re.match(r'[A-Za-z]:[\\/]', value))
            or value.endswith(('/', '\\')) or any(p in {'.', '..'} for p in re.split(r'[\\/]', value))):
        _fail('bounded absolute non-UNC path required')
    return raw


def create_basename(nonce, index):
    _identity(nonce, '0' * 64)
    _integer(index, 0, MAX_RECORDS - 1)
    return 'comsol-os-probe-' + nonce + '-' + str(index) + '.dat'


def create_payload(nonce, index):
    create_basename(nonce, index)
    return ('COMSOL-MCP-OS-PROBE/1\n' + nonce + '\n' + str(index) + '\n').encode('ascii')


def read_hash_record(index, path, maxbytes=DEFAULT_READ_BYTES):
    """Data-only convenience; the wire always includes the explicit limit."""
    _integer(index, 0, MAX_RECORDS - 1); _path(path); _integer(maxbytes, 1, MAX_READ_BYTES)
    return {'index': index, 'operation': 'READ_HASH', 'path': path, 'maxbytes': maxbytes}


def echo_payload(nonce, index):
    create_basename(nonce, index)
    return ('OSQP1:' + nonce + ':' + str(index) + '\n').encode('ascii')


class _Writer:
    def __init__(self):
        self.raw = bytearray()

    def number(self, size, value):
        _integer(value, 0, (1 << (8 * size)) - 1)
        self.raw.extend(value.to_bytes(size, 'big'))

    def text(self, value, limit, *, exception=False):
        if type(value) is not str or '\0' in value:
            _fail('strict text required')
        try:
            raw = value.encode('utf-8', 'strict')
        except UnicodeError as exc:
            raise ProtocolError('strict UTF8 required') from exc
        if len(raw) > limit or exception and value and not _EXCEPTION.fullmatch(value):
            _fail('bounded exception class/text required')
        self.number(2, len(raw)); self.raw.extend(raw)

    def flag(self, value):
        self.number(1, int(_boolean(value)))

    def optional(self, value, size):
        self.flag(value is not None)
        if value is not None:
            if size == 32:
                if type(value) is not str or not _HEX64.fullmatch(value):
                    _fail('digest required')
                self.raw.extend(bytes.fromhex(value))
            else:
                self.number(size, value)


class _Reader:
    def __init__(self, raw):
        self.raw, self.offset = raw, 0

    def take(self, count):
        if not 0 <= count <= len(self.raw) - self.offset:
            _fail('truncated frame')
        start = self.offset; self.offset += count
        return self.raw[start:self.offset]

    def number(self, size):
        return int.from_bytes(self.take(size), 'big')

    def text(self, limit):
        count = self.number(2)
        if count > limit:
            _fail('text bound')
        try:
            value = self.take(count).decode('utf-8', 'strict')
        except UnicodeError as exc:
            raise ProtocolError('strict UTF8 required') from exc
        if '\0' in value:
            _fail('NUL text')
        return value

    def flag(self):
        value = self.number(1)
        if value not in (0, 1):
            _fail('boolean wire value')
        return bool(value)

    def optional(self, size):
        if not self.flag():
            return None
        return self.take(size).hex() if size == 32 else self.number(size)

    def end(self):
        if self.offset != len(self.raw):
            _fail('trailing bytes')


def _frame(body, bound):
    if len(body) + 4 > bound:
        _fail('frame bound')
    return len(body).to_bytes(4, 'big') + bytes(body)


def _unframe(raw, bound):
    if type(raw) is not bytes or len(raw) < 4 or len(raw) > bound:
        _fail('bounded bytes frame required')
    size = int.from_bytes(raw[:4], 'big')
    if size > bound - 4 or size != len(raw) - 4:
        _fail('frame length/trailing bytes')
    return _Reader(raw[4:])


def _header(writer, magic, value):
    _identity(value['nonce'], value['recipe_sha256'])
    if type(value['version']) is not int or value['version'] != VERSION:
        _fail('version')
    writer.raw.extend(magic); writer.number(2, VERSION)
    writer.raw.extend(value['nonce'].encode('ascii') + value['recipe_sha256'].encode('ascii'))


def _read_header(reader, magic, expected_nonce, expected_recipe_sha256):
    if reader.take(4) != magic or reader.number(2) != VERSION:
        _fail('magic/version')
    try:
        nonce = reader.take(32).decode('ascii'); recipe = reader.take(64).decode('ascii')
    except UnicodeError as exc:
        raise ProtocolError('identity ASCII') from exc
    _identity(nonce, recipe)
    if expected_nonce is not None or expected_recipe_sha256 is not None:
        _identity(expected_nonce, expected_recipe_sha256)
        if nonce != expected_nonce or recipe != expected_recipe_sha256:
            _fail('identity binding mismatch')
    return {'version': VERSION, 'nonce': nonce, 'recipe_sha256': recipe}


def encode_request(value):
    _keys(value, ('version', 'nonce', 'recipe_sha256', 'records'))
    out = _Writer(); _header(out, b'CQ01', value)
    records = value['records']
    if type(records) is not list or not 1 <= len(records) <= MAX_RECORDS:
        _fail('record bound')
    out.number(2, len(records)); seen = set()
    for row in records:
        if type(row) is not dict or row.get('operation') not in OPERATIONS:
            _fail('operation')
        operation = row['operation']; index = _integer(row.get('index'), 0, MAX_RECORDS - 1)
        if index in seen:
            _fail('duplicate index')
        seen.add(index); body = _Writer()
        if operation == 'READ_HASH':
            _keys(row, ('index', 'operation', 'path', 'maxbytes'))
            _path(row['path']); body.text(row['path'], MAX_PATH)
            body.number(4, _integer(row['maxbytes'], 1, MAX_READ_BYTES))
        elif operation == 'CREATE_NEW':
            _keys(row, ('index', 'operation', 'path'))
            _path(row['path'])
            if re.split(r'[\\/]', row['path'])[-1] != create_basename(value['nonce'], index):
                _fail('CREATE_NEW basename must bind nonce/index')
            body.text(row['path'], MAX_PATH)
        else:
            _keys(row, ('index', 'operation', 'address', 'port'))
            if type(row['address']) is not bytes or len(row['address']) not in (4, 16):
                _fail('literal address bytes only; no DNS')
            body.number(1, len(row['address'])); body.raw.extend(row['address'])
            body.number(2, _integer(row['port'], 1, 65535))
        out.number(1, index); out.number(1, OPERATIONS.index(operation) + 1)
        out.number(4, len(body.raw)); out.raw.extend(body.raw)
    return _frame(out.raw, MAX_REQUEST)


def decode_request(raw, *, expected_nonce=None, expected_recipe_sha256=None):
    reader = _unframe(raw, MAX_REQUEST)
    value = _read_header(reader, b'CQ01', expected_nonce, expected_recipe_sha256)
    count = reader.number(2)
    if not 1 <= count <= MAX_RECORDS:
        _fail('record bound')
    rows = []
    for _ in range(count):
        index, code, length = reader.number(1), reader.number(1), reader.number(4)
        if not 1 <= code <= len(OPERATIONS) or length > MAX_REQUEST:
            _fail('operation/record length')
        body = _Reader(reader.take(length)); operation = OPERATIONS[code - 1]
        row = {'index': index, 'operation': operation}
        if operation in ('READ_HASH', 'CREATE_NEW'):
            row['path'] = body.text(MAX_PATH)
            if operation == 'READ_HASH':
                row['maxbytes'] = body.number(4)
        else:
            row['address'] = body.take(body.number(1)); row['port'] = body.number(2)
        body.end(); rows.append(row)
    reader.end(); value['records'] = rows
    if encode_request(value) != raw:
        _fail('noncanonical request')
    return value


def empty_resource():
    return {'acquisition': 'NOT_ENTERED', 'close': 'NOT_ENTERED', 'close_exception_class': ''}


def empty_observation(index, operation):
    return {'index': index, 'operation': operation, 'entered': False, 'phase': 'NOT_ENTERED',
        'body_returned': False, 'exception_class': '', 'input': empty_resource(),
        'output': empty_resource(), 'socket': empty_resource(), 'read_count': None, 'write_count': None,
        'read_sha256': None, 'write_sha256': None, 'eof_observed': False, 'limit_reached': False,
        'echo_matches': None, 'local_endpoint': None, 'remote_endpoint': None}


def _enum(value, choices):
    if type(value) is not str or value not in choices:
        _fail('unknown enum')
    return choices.index(value)


def _resource(out, value):
    _keys(value, ('acquisition', 'close', 'close_exception_class'))
    out.number(1, _enum(value['acquisition'], ACQUISITIONS)); out.number(1, _enum(value['close'], CLOSES))
    out.text(value['close_exception_class'], MAX_EXCEPTION, exception=True)
    if (bool(value['close_exception_class']) != (value['close'] == 'THREW')
            or value['acquisition'] in ('NOT_ENTERED', 'THREW') and value['close'] != 'NOT_ENTERED'):
        _fail('resource facts inconsistent')


def _endpoint(out, value):
    out.flag(value is not None)
    if value is not None:
        _keys(value, ('address', 'port'))
        if type(value['address']) is not bytes or len(value['address']) not in (4, 16):
            _fail('endpoint literal bytes')
        out.number(1, len(value['address'])); out.raw.extend(value['address'])
        out.number(2, _integer(value['port'], 1, 65535))


def _get_resource(reader):
    a, c = reader.number(1), reader.number(1)
    if a >= len(ACQUISITIONS) or c >= len(CLOSES):
        _fail('resource enum')
    return {'acquisition': ACQUISITIONS[a], 'close': CLOSES[c], 'close_exception_class': reader.text(MAX_EXCEPTION)}


def _get_endpoint(reader):
    if not reader.flag():
        return None
    return {'address': reader.take(reader.number(1)), 'port': reader.number(2)}


def encode_response(value):
    _keys(value, ('version', 'nonce', 'recipe_sha256', 'frame_status', 'exception_class',
                  'stdin', 'output_observation', 'records'))
    out = _Writer(); _header(out, b'CR01', value)
    out.number(1, _enum(value['frame_status'], ('PARSED', 'REJECTED')))
    out.text(value['exception_class'], MAX_EXCEPTION, exception=True)
    _resource(out, value['stdin'])
    if value['output_observation'] != 'UNVERIFIED_SELF_OUTPUT':
        _fail('output cannot self-attest its final write/close')
    out.number(1, 0)
    records = value['records']
    if type(records) is not list or not 0 <= len(records) <= MAX_RECORDS:
        _fail('response record bound')
    if ((value['frame_status'] == 'REJECTED') != bool(value['exception_class'])
            or value['frame_status'] == 'REJECTED' and records
            or value['frame_status'] == 'PARSED' and not records):
        _fail('frame status facts')
    out.number(2, len(records)); seen = set()
    for row in records:
        if type(row) is not dict:
            _fail('closed observation required')
        index = _integer(row.get('index'), 0, MAX_RECORDS - 1)
        operation = row.get('operation')
        if operation not in OPERATIONS or index in seen:
            _fail('response operation/index')
        seen.add(index); _keys(row, empty_observation(index, operation))
        body = _Writer(); body.flag(row['entered']); body.number(1, _enum(row['phase'], PHASES))
        body.flag(row['body_returned']); body.text(row['exception_class'], MAX_EXCEPTION, exception=True)
        for name in ('input', 'output', 'socket'):
            _resource(body, row[name])
        for name in ('read_count', 'write_count'):
            if row[name] is not None:
                _integer(row[name], 0, MAX_READ_BYTES if name == 'read_count' else 128)
            body.optional(row[name], 8)
        for name in ('read_sha256', 'write_sha256'):
            body.optional(row[name], 32)
        body.flag(row['eof_observed']); body.flag(row['limit_reached'])
        echo = row['echo_matches']
        if echo is not None:
            _boolean(echo)
        body.number(1, 0 if echo is None else 2 if echo else 1)
        _endpoint(body, row['local_endpoint']); _endpoint(body, row['remote_endpoint'])
        if not row['entered'] and row != empty_observation(index, operation):
            _fail('not-entered observation must contain no attempted facts')
        out.number(1, index); out.number(1, OPERATIONS.index(operation) + 1)
        out.number(4, len(body.raw)); out.raw.extend(body.raw)
    return _frame(out.raw, MAX_RESPONSE)


def decode_response(raw, *, expected_nonce, expected_recipe_sha256, expected_request=None):
    _identity(expected_nonce, expected_recipe_sha256)
    reader = _unframe(raw, MAX_RESPONSE)
    value = _read_header(reader, b'CR01', expected_nonce, expected_recipe_sha256)
    status = reader.number(1)
    if status not in (0, 1):
        _fail('frame status')
    value.update(frame_status=('PARSED', 'REJECTED')[status], exception_class=reader.text(MAX_EXCEPTION),
                 stdin=_get_resource(reader))
    if reader.number(1) != 0:
        _fail('self output observation')
    value['output_observation'] = 'UNVERIFIED_SELF_OUTPUT'
    count = reader.number(2)
    if count > MAX_RECORDS:
        _fail('record bound')
    rows = []
    for _ in range(count):
        index, code, length = reader.number(1), reader.number(1), reader.number(4)
        if not 1 <= code <= len(OPERATIONS) or length > MAX_RESPONSE:
            _fail('operation/record length')
        body = _Reader(reader.take(length)); row = empty_observation(index, OPERATIONS[code - 1])
        row['entered'] = body.flag(); phase = body.number(1)
        if phase >= len(PHASES):
            _fail('phase')
        row['phase'] = PHASES[phase]; row['body_returned'] = body.flag(); row['exception_class'] = body.text(MAX_EXCEPTION)
        for name in ('input', 'output', 'socket'):
            row[name] = _get_resource(body)
        for name in ('read_count', 'write_count'):
            row[name] = body.optional(8)
        for name in ('read_sha256', 'write_sha256'):
            row[name] = body.optional(32)
        row['eof_observed'] = body.flag(); row['limit_reached'] = body.flag()
        echo = body.number(1)
        if echo not in (0, 1, 2):
            _fail('echo tristate')
        row['echo_matches'] = None if echo == 0 else echo == 2
        row['local_endpoint'] = _get_endpoint(body); row['remote_endpoint'] = _get_endpoint(body)
        body.end(); rows.append(row)
    reader.end(); value['records'] = rows
    if encode_response(value) != raw:
        _fail('noncanonical response')
    if expected_request is not None and value['frame_status'] == 'PARSED':
        request = decode_request(encode_request(expected_request), expected_nonce=expected_nonce,
                                 expected_recipe_sha256=expected_recipe_sha256)
        if [(r['index'], r['operation']) for r in rows] != [(r['index'], r['operation']) for r in request['records']]:
            _fail('response record binding')
    return value
