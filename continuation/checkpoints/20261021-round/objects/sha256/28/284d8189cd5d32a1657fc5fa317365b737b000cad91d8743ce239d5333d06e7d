#!/usr/bin/env python3
"""T030: fill only a separately mounted <=20 MiB disposable test volume.

The volume must already contain last_complete.mph. All engine work uses the
production stdio MCP gateway. No service or volume is stopped by this driver.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import zipfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from phase2_run_mcp import ROOT, JDK11_DEFAULT, _write, _sha256, _server_identity, _execution


async def run(args):
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    evidence = ROOT / 'evidence/phase2/disk-full' / stamp
    evidence.mkdir(parents=True)
    volume = args.volume.resolve()
    target = volume / 'last_complete.mph'
    filler = volume / 'disk-full-test-filler.bin'
    transcript, assertions = [], {}
    filler_created = False
    result = {'status': 'NOT_RUN', 'case': 'T030'}
    _write(evidence / 'request.json', {'volume': str(volume), 'target': str(target), 'route': 'production stdio MCP'})
    try:
        identity = _server_identity(args.pid, args.port)
        volume.relative_to(ROOT)
        stat = os.statvfs(volume)
        size = stat.f_blocks * stat.f_frsize
        if not os.path.ismount(volume) or volume.stat().st_dev == ROOT.stat().st_dev or not 0 < size <= 20 * 1024**2:
            raise RuntimeError('Refuse filling anything except a separate <=20 MiB test mount')
        if not target.is_file() or filler.exists():
            raise RuntimeError('Require existing complete target and no prior filler')
        before = _sha256(target)
        with zipfile.ZipFile(target) as archive:
            assert archive.testzip() is None
        _write(evidence / 'environment.json', {'server_identity': identity, 'volume_bytes': size,
               'device': volume.stat().st_dev, 'target_before_sha256': before, 'target_bytes': target.stat().st_size})
        env = dict(os.environ, COMSOL_ROOT=args.comsol_root, JAVA_HOME=args.jdk11,
                   COMSOL_PREFS_DIR=args.prefs, COMSOL_SERVER_MCP_HOME=str(args.private_home.resolve()), PYTHONPATH=str(ROOT))
        params = StdioServerParameters(command=sys.executable, args=['-m', 'comsol_mcp.mcp_server'], env=env, cwd=str(ROOT))
        with (evidence / 'engine.log').open('w') as engine_log:
            async with stdio_client(params, errlog=engine_log) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    async def call(name, arguments):
                        response = await session.call_tool(name, arguments)
                        value = json.loads(response.content[0].text)
                        transcript.append({'operation': name, 'arguments': arguments, 'isError': response.isError, 'result': value})
                        _write(evidence / 'result.json', {'status': 'NOT_RUN', 'transcript': transcript})
                        return value, response.isError
                    connected, _ = await call('server_connect', {'host': '127.0.0.1', 'port': args.port})
                    assert connected['success'], connected
                    loaded, _ = await call('model_load', {'path': str(target)})
                    assert loaded['success'], loaded
                    ref, revision = loaded['execution']['model_ref'], loaded['execution']['revision']
                    # This small volume is disposable; the complete MPH stays
                    # untouched. Leave less free space than its actual size.
                    available = os.statvfs(volume).f_bavail * stat.f_frsize
                    remaining = max(0, available - 64 * 1024)
                    with filler.open('xb') as stream:
                        filler_created = True
                        while remaining:
                            block = min(64 * 1024, remaining)
                            stream.write(b'\0' * block)
                            remaining -= block
                        stream.flush(); os.fsync(stream.fileno())
                    saved, outer_error = await call('save_model', {'path': str(target),
                        'execution': _execution(ref, revision, key='disk-full-'+stamp, request='disk-full-'+stamp)})
                    after = _sha256(target)
                    error_text = json.dumps(saved, ensure_ascii=False).lower()
                    assertions.update(failure_propagated=not saved.get('success') and outer_error,
                                      disk_space_error=any(word in error_text for word in ('no space', 'disk full', 'disk space', 'enospc', '磁盘空间', '磁盘已满')),
                                      original_hash_preserved=after == before,
                                      original_nonempty=target.stat().st_size > 0,
                                      separate_limited_volume=True)
                    with zipfile.ZipFile(target) as archive:
                        assertions['original_zip_intact'] = archive.testzip() is None
                    result = {'status': 'PASS' if all(assertions.values()) else 'FAIL', 'case': 'T030',
                              'before_sha256': before, 'after_sha256': after,
                              'free_bytes_after': os.statvfs(volume).f_bavail * stat.f_frsize,
                              'temporary_files': [{'name': p.name, 'bytes': p.stat().st_size} for p in volume.iterdir() if p.is_file() and p != filler],
                              'transcript': transcript}
    except Exception as exc:
        result = {'status': 'FAIL', 'case': 'T030', 'error': f'{type(exc).__name__}: {exc}', 'transcript': transcript}
    finally:
        # Removing only our filler restores this disposable volume's capacity.
        if filler_created and filler.exists():
            filler.unlink()
        _write(evidence / 'assertions.json', assertions)
        _write(evidence / 'result.json', result)
        _write(evidence / 'SHA256SUMS.json', {p.name: _sha256(p) for p in evidence.iterdir() if p.is_file()})
    print(json.dumps({'evidence': str(evidence), 'status': result['status']}))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--private-home', type=Path, required=True)
    parser.add_argument('--prefs', required=True)
    parser.add_argument('--volume', type=Path, default=ROOT / 'output/phase2-disk-test-volume')
    parser.add_argument('--pid', type=int, default=84749)
    parser.add_argument('--port', type=int, default=56388)
    parser.add_argument('--comsol-root', default='/Applications/COMSOL64/Multiphysics')
    parser.add_argument('--jdk11', default=JDK11_DEFAULT)
    raise SystemExit(asyncio.run(run(parser.parse_args())))
