"""Task-specific external Main admission; exec the unchanged frozen P0 harness."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import uuid

BASE = Path('/private/tmp/cv3-t044-finalize-r2-inputs-aqzc2efj')
BINDINGS = {
    't044-thermal': 'f9846078cc26d8a7ac1ff03c35c5964c0776c09484aa9d777173b5dfa5805301',
}
JAVA = '/Applications/COMSOL64/Multiphysics/java/macarm64/jre/Contents/Home'
KEYS = {'schema', 'phase', 'nonce', 'launcher_sha256', 'binding_sha256', 'argv_sha256', 'protected_sha256', 'java_sha256', 'python_sha256', 'quiescence_required'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def signature(s):
    return (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def read_exact(path, *, private=False):
    path = Path(path)
    for parent in (path.parent, *path.parent.parents):
        if parent.is_symlink():
            raise ValueError('symlink ancestry')
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError('ordinary single-link file required')
    if private and (before.st_uid != os.getuid() or stat.S_IMODE(before.st_mode) != 0o600 or not 0 < before.st_size <= 32768):
        raise ValueError('private approval mode/owner/size')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(fd)
        if signature(opened) != signature(before):
            raise ValueError('file replaced before read')
        chunks = []
        while True:
            part = os.read(fd, 65536)
            if not part:
                break
            chunks.append(part)
        data = b''.join(chunks)
        if len(data) != before.st_size or signature(os.fstat(fd)) != signature(before) or signature(path.lstat()) != signature(before):
            raise ValueError('file changed during read')
        return data
    finally:
        os.close(fd)


def closed_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs)


def fixed_plan(phase):
    if phase not in BINDINGS:
        raise ValueError('unknown fixed phase')
    raw = read_exact(BASE / phase / 'SOURCE_BINDING.json')
    if sha(raw) != BINDINGS[phase]:
        raise ValueError('binding drift')
    binding = closed_json(raw)
    argv = list(binding['exact_harness_argv'])
    argv[argv.index('--java-home') + 1] = JAVA  # Explicit reviewed delta, never rewrite the old plan.
    return binding, argv


def quiescent():
    # Read-only kernel inventory; any native name or unreadable inventory rejects.
    result = subprocess.run(['/bin/ps', '-axo', 'comm='], capture_output=True, text=True, timeout=10, check=True)
    if not result.stdout.strip():
        raise ValueError('empty kernel inventory')
    for line in result.stdout.splitlines():
        name = Path(line.strip()).name.lower()
        if 'comsol' in name or name in ('java', 'javac', 'javap'):
            raise ValueError('native process still present')


def admit(approval_path, approval_sha, *, execute=True):
    approval_path = Path(approval_path).absolute()
    parent = approval_path.parent
    ps = parent.lstat()
    if parent.parent != Path('/private/tmp') or parent.is_symlink() or ps.st_uid != os.getuid() or stat.S_IMODE(ps.st_mode) != 0o700:
        raise ValueError('fresh private admission namespace required')
    raw = read_exact(approval_path, private=True)
    if sha(raw) != approval_sha:
        raise ValueError('Main approval SHA mismatch')
    a = closed_json(raw)
    if not isinstance(a, dict) or set(a) != KEYS or a['schema'] != 'CV3_P0_EXTERNAL_MAIN_ONCE_V1' or a['quiescence_required'] is not True:
        raise ValueError('closed approval schema')
    if str(uuid.UUID(a['nonce'])) != a['nonce']:
        raise ValueError('canonical Main nonce required')
    if sha(read_exact(__file__)) != a['launcher_sha256']:
        raise ValueError('launcher drift')
    # Consume once before later gates: a failed attempt cannot reuse authorization.
    fd = os.open(parent / 'CONSUMED.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, canonical({'approval_sha256': approval_sha, 'nonce': a['nonce'], 'owner_pid': os.getpid()}))
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        binding, argv = fixed_plan(a['phase'])
        if a['binding_sha256'] != BINDINGS[a['phase']] or sha(canonical(argv)) != a['argv_sha256']:
            raise ValueError('fixed argv/binding mismatch')
        if {'resolved_path': str(Path(argv[0]).resolve(strict=True)), 'sha256': sha(read_exact(Path(argv[0]).resolve(strict=True)))} != a['python_sha256']:
            raise ValueError('Python executable drift')
        source = Path(binding['source'])
        actual = {str(p.relative_to(source)) for p in source.rglob('*') if p.is_file()}
        if actual != set(binding['source_files']):
            raise ValueError('source membership drift')
        for name, row in binding['source_files'].items():
            path = source / name
            if path.lstat().st_mode & 0o222 or sha(read_exact(path)) != row['sha256']:
                raise ValueError('source byte/mode drift')
        for name in ('SOURCE_FREEZE', 'CALL_PLAN'):
            row = binding[name]
            if sha(read_exact(row['path'])) != row['sha256']:
                raise ValueError(name + ' drift')
        protected = [argv[i + 1] for i, value in enumerate(argv) if value == '--protected-state']
        if set(a['protected_sha256']) != set(protected) or any(sha(read_exact(path)) != a['protected_sha256'][path] for path in protected):
            raise ValueError('protected state drift')
        java_files = [str(Path(JAVA) / name) for name in ('bin/java', 'bin/javac', 'release')]
        if set(a['java_sha256']) != set(java_files) or any(sha(read_exact(path)) != a['java_sha256'][path] for path in java_files):
            raise ValueError('actual Java toolchain drift')
        root = Path(binding['runtime_path_expected_absent'])
        if root.exists() or root.is_symlink():
            raise ValueError('runtime must remain absent')
        quiescent()
        env = {k: v for k, v in os.environ.items() if not k.startswith('COMSOL_')}
        env.update(COMSOL_SERVER_MCP_HOME=str(root / 'home'), PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(source))
        if signature(parent.lstat()) != signature(ps):
            # Creating CONSUMED changes directory mtime/ctime, so compare ownership/inode/mode only.
            current = parent.lstat()
            if (current.st_dev, current.st_ino, current.st_mode, current.st_uid) != (ps.st_dev, ps.st_ino, ps.st_mode, ps.st_uid):
                raise ValueError('admission directory replaced')
        if execute:
            os.chdir(binding['cwd'])
            os.execve(argv[0], argv, env)
        return argv, env
    except BaseException as exc:
        evidence = canonical({'firstcause': type(exc).__name__ + ': ' + str(exc)[:512], 'native': 'NOT_DISPATCHED_BY_WRAPPER', 'nonce': a['nonce']})
        fd = os.open(parent / 'FIRST_FAILURE.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, evidence)
            os.fsync(fd)
        finally:
            os.close(fd)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--main-approval', required=True)
    parser.add_argument('--main-approval-sha256', required=True)
    args = parser.parse_args()
    admit(args.main_approval, args.main_approval_sha256)


if __name__ == '__main__':
    main()
