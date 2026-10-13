"""Offline pytest launcher: deny all IP/DNS connections and fail on attempted use."""
from __future__ import annotations
import json
import os
import socket
import sys
from pathlib import Path

attempts = []
def deny(kind, detail):
    attempts.append({'kind': kind, 'detail': str(detail)[:256]})
    raise OSError('strict offline test guard denied network access')

_base_socket = socket.socket
class GuardedSocket(_base_socket):
    def connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            return deny('connect', address)
        return super().connect(address)
    def connect_ex(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            return deny('connect_ex', address)
        return super().connect_ex(address)

def guarded_create_connection(address, *args, **kwargs):
    return deny('create_connection', address)

def guarded_getaddrinfo(host, *args, **kwargs):
    return deny('getaddrinfo', host)

socket.socket = GuardedSocket
socket.create_connection = guarded_create_connection
socket.getaddrinfo = guarded_getaddrinfo
os.environ['W25_STRICT_NETWORK_GUARD'] = 'ACTIVE'
import pytest
args = sys.argv[1:]
result = int(pytest.main(args))
report = {
    'schema': 'W25_STRICT_OFFLINE_TEST_GUARD/1',
    'status': 'PASS' if result == 0 and not attempts else 'FAIL',
    'guard': 'all IPv4/IPv6 connect/connect_ex, socket.create_connection and DNS lookups denied',
    'attempts': attempts,
    'pytest_exit_code': result,
}
path = Path(os.environ.get('W25_NETWORK_GUARD_RECEIPT', 'NETWORK_GUARD_RECEIPT.json'))
path.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
print('STRICT_NETWORK_GUARD', report['status'], 'attempts=', len(attempts))
raise SystemExit(0 if report['status'] == 'PASS' else 1)
