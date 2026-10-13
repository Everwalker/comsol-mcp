"""Bound W02 client PoC. Attaches explicitly; never manages an existing server."""
from __future__ import annotations
import hashlib
import json
import math
import os
import platform
import re
import subprocess
import struct
import zlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMSOL_ROOT = Path('/Applications/COMSOL64/Multiphysics')
DEFAULT_JDK11 = Path('/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home')


def stamp():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')


def bounded_path(path, root):
    resolved, allowed = Path(path).resolve(), Path(root).resolve()
    if allowed not in resolved.parents:
        raise ValueError(f'Path must be strictly below {allowed}')
    return resolved


def client_classpath(root):
    manifest = root / 'bin/comsolclientpath.txt'
    jars = [root / 'apiplugins' / name.strip() for name in manifest.read_text().splitlines() if name.strip()]
    if not jars or any(not p.is_file() for p in jars):
        raise ValueError('Official client classpath contains missing JARs')
    return os.pathsep.join(map(str, jars)), hashlib.sha256(manifest.read_bytes()).hexdigest(), len(jars)


def run_command(command, run, name, timeout=180):
    log = run / (name + '.log')
    with log.open('w') as output:
        child = subprocess.Popen(command, cwd=run, stdout=output, stderr=subprocess.STDOUT)
        write_json(run / (name + '.process.json'), {'pid': child.pid, 'started_at': stamp(), 'command': command})
        try:
            code = child.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f'EXECUTION_STATE_UNKNOWN: client PID {child.pid} still running; do not retry; inspect {log}') from exc
    lines = log.read_text(errors='replace')
    if code:
        raise RuntimeError(f'{name} exited {code}; inspect {log}')
    return lines


def validate_png(path):
    """Validate the fixed recipe's RGB8 PNG, including CRCs and raster rows."""
    data = Path(path).read_bytes()
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('Not PNG')
    offset, compressed, size, ended = 8, bytearray(), None, False
    while offset < len(data):
        length = struct.unpack('>I', data[offset:offset+4])[0]
        kind = data[offset+4:offset+8]
        payload = data[offset+8:offset+8+length]
        crc = struct.unpack('>I', data[offset+8+length:offset+12+length])[0]
        if zlib.crc32(kind+payload) != crc:
            raise ValueError('PNG CRC mismatch')
        if kind == b'IHDR':
            w,h,depth,color,compression,filtering,interlace = struct.unpack('>IIBBBBB',payload)
            if (depth,color,compression,filtering,interlace) != (8,2,0,0,0):
                raise ValueError('PoC validator expects non-interlaced RGB8 PNG')
            size = [w,h]
        elif kind == b'IDAT': compressed.extend(payload)
        elif kind == b'IEND': ended = True
        offset += length+12
    if not size or not ended or min(size)<=0:
        raise ValueError('Incomplete PNG')
    raster=zlib.decompress(compressed); stride=1+size[0]*3
    if len(raster)!=stride*size[1] or any(raster[y*stride]>4 for y in range(size[1])):
        raise ValueError('Invalid PNG raster')
    return size


def run_v64_poc(run_dir: Path, *, known_server_pid: int, known_port: int,
                preferences_directory: str, comsol_root: Path = DEFAULT_COMSOL_ROOT,
                jdk11: Path = DEFAULT_JDK11) -> dict[str, Any]:
    """Run a fixed recipe on an explicitly selected local test server.

    No server start/stop or implicit model adoption occurs here. Caller must
    register the isolated server first. This is not the W06 managed worker.
    """
    run = bounded_path(run_dir, ROOT / 'evidence/w02')
    prefs = bounded_path(preferences_directory, ROOT / '.phase1-private')
    if not prefs.is_dir() or known_server_pid <= 1 or not 1024 <= known_port <= 65535:
        raise ValueError('Existing private preferences and valid server PID/port required')
    run.mkdir(parents=True, exist_ok=False)
    write_json(run / 'request.json', {'tool': 'runtime_poc_v64', 'pid': known_server_pid,
        'port': known_port, 'recipe': '-div(grad(u))+u=2; natural BC; unit square', 'threshold': 1e-8})
    (run / 'engine.log').write_text('Progress and API diagnostics are retained in recipe.log and reopen.log.\n')
    try:
        process = subprocess.check_output(['ps', '-p', str(known_server_pid), '-o', 'pid=,lstart=,command='], text=True).strip()
        listeners = subprocess.check_output(['lsof', '-nP', '-a', '-p', str(known_server_pid), '-iTCP', '-sTCP:LISTEN'], text=True)
        if 'comsol' not in process.lower() or f':{known_port} ' not in listeners:
            raise ValueError('PID/COMSOL identity/port did not match the selected test server')
        write_json(run / 'server_identity.json', {'observed_at': stamp(), 'process': process,
            'listeners': listeners, 'exit_policy': 'attach-only; never terminate this server'})
        cp, cp_hash, jar_count = client_classpath(comsol_root)
        java_version = subprocess.run([str(jdk11/'bin/java'), '-version'], capture_output=True, text=True, check=True)
        if not re.search(r'version "11\.', java_version.stderr):
            raise ValueError('This PoC requires external JDK 11')
        sources = sorted((ROOT/'comsol_mcp/phase1_java').glob('*.java'))
        write_json(run/'environment.json', {'timestamp': stamp(), 'os': platform.platform(),
            'architecture': platform.machine(), 'jdk': java_version.stderr, 'comsol_root': str(comsol_root),
            'classpath_sha256': cp_hash, 'jar_count': jar_count,
            'sources': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}})
        classes = run/'classes'; classes.mkdir()
        run_command([str(jdk11/'bin/javac'), '-cp', cp, '-d', str(classes), *map(str,sources)], run, 'compile')
        tmp = prefs.parent/'client-temp'; tmp.mkdir(exist_ok=True)
        base = [str(jdk11/'bin/java'), f'-Dcs.prefsdir={prefs}', f'-Djava.io.tmpdir={tmp}', '-cp', str(classes)+os.pathsep+cp]
        tag = 'poc_'+uuid.uuid4().hex[:16]
        mph, png = run/'model.mph', run/'result.png'
        recipe = run_command(base+['comsol_mcp.phase1_java.BoundPhase1Poc', '127.0.0.1', str(known_port), tag, str(mph), str(png)], run, 'recipe')
        reopened = run_command(base+['comsol_mcp.phase1_java.BoundPhase1Reopen', '127.0.0.1', str(known_port), str(mph)], run, 'reopen')
        products = run_command(base+['comsol_mcp.phase1_java.BoundPhase1ProductProbe', '127.0.0.1', str(known_port)], run, 'products')
        size = validate_png(png)
        errs = [float(x) for x in re.findall(r'(?:maxabs|u)=([0-9.Ee+-]+)', recipe+'\n'+reopened)]
        checks = {'recipe_returned': 'W02_RESULT' in recipe, 'fresh_process_reopened': 'W02_REOPEN' in reopened,
            'two_errors_below_threshold': len(errs)==2 and all(math.isfinite(x) and x<1e-8 for x in errs),
            'png_decoded': min(size)>0, 'mph_nonempty': mph.stat().st_size>0,
            'server_build_confirmed': bool(re.search(r'6\.4(?:\.|\s)', recipe))}
        result = {'ok': all(checks.values()), 'checks': checks, 'errors': errs, 'image_size': size,
            'model_tag': tag, 'server_pid': known_server_pid, 'port': known_port,
            'products': products, 'evidence': str(run),
            'artifacts': {p.name: {'path':str(p), 'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in (mph,png)}}
        write_json(run/'assertions.json', checks); write_json(run/'result.json', result)
        if not result['ok']:
            raise RuntimeError('PoC acceptance assertions failed; inspect result.json')
        return result
    except Exception as exc:
        if not (run/'environment.json').exists():
            write_json(run/'environment.json', {'timestamp':stamp(), 'os':platform.platform()})
        write_json(run/'failure.json', {'status':'FAIL', 'error':repr(exc), 'timestamp':stamp()})
        if not (run/'result.json').exists(): write_json(run/'result.json', {'ok':False,'error':repr(exc)})
        if not (run/'assertions.json').exists(): write_json(run/'assertions.json', {'completed':False})
        raise
