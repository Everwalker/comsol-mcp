import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import uuid
import pytest

spec = importlib.util.spec_from_file_location('once', Path(__file__).with_name('p0_fresh_once_exec.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

@pytest.fixture
def admission():
    with tempfile.TemporaryDirectory(prefix='cv3-p0-once-software-', dir='/private/tmp') as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        def create(phase=None, **changes):
            phase = next(iter(m.BINDINGS)) if phase is None else phase
            b, argv = m.fixed_plan(phase)
            protected = [argv[i+1] for i,v in enumerate(argv) if v=='--protected-state']
            a = dict(schema='CV3_P0_EXTERNAL_MAIN_ONCE_V1', phase=phase, nonce=str(uuid.uuid4()),
                     launcher_sha256=m.sha(m.read_exact(m.__file__)), binding_sha256=m.BINDINGS[phase],
                     argv_sha256=m.sha(m.canonical(argv)), protected_sha256={p:m.sha(m.read_exact(p)) for p in protected},
                     java_sha256={str(Path(m.JAVA)/p):m.sha(m.read_exact(Path(m.JAVA)/p)) for p in ('bin/java','bin/javac','release')},
                     python_sha256={'resolved_path':str(Path(argv[0]).resolve(strict=True)), 'sha256':m.sha(m.read_exact(Path(argv[0]).resolve(strict=True)))}, quiescence_required=True)
            a.update(changes)
            data=m.canonical(a); p=root/'SOFTWARE_ONLY_APPROVAL.json'; p.write_bytes(data); p.chmod(0o600)
            return p,m.sha(data)
        yield root,create

@pytest.mark.parametrize('phase',m.BINDINGS)
def test_actual_source_fixed_plan_once(admission,monkeypatch,phase):
    root,create=admission; p,h=create(phase)
    monkeypatch.setattr(m,'quiescent',lambda:None) # kernel predicate exercised separately; no SDK/backend mock.
    argv,env=m.admit(p,h,execute=False)
    assert argv[argv.index('--java-home')+1]==m.JAVA
    assert env['COMSOL_SERVER_MCP_HOME']==str(Path(argv[argv.index('--runtime')+1])/'home')
    assert not Path(argv[argv.index('--runtime')+1]).exists()
    with pytest.raises(FileExistsError): m.admit(p,h,execute=False)
    assert (root/'CONSUMED.json').is_file()

@pytest.mark.parametrize('changes',[
    {'argv_sha256':'0'*64}, {'binding_sha256':'0'*64}, {'python_sha256':'0'*64},
    {'protected_sha256':{}}, {'java_sha256':{}}, {'extra':'forbidden'},
    {'quiescence_required':False}, {'nonce':'bad'}, {'launcher_sha256':'0'*64},
])
def test_bad_binding_never_exec(admission,monkeypatch,changes):
    root,create=admission;p,h=create(**changes)
    monkeypatch.setattr(m.os,'execve',lambda *a:pytest.fail('unexpected dispatch'))
    with pytest.raises(ValueError):m.admit(p,h)

@pytest.mark.parametrize('damage',['symlink','hardlink','mode','duplicate','wrongsha'])
def test_approval_file_controls(admission,damage):
    root,create=admission;p,h=create()
    if damage=='symlink':
        p.rename(root/'original');p.symlink_to(root/'original')
    elif damage=='hardlink':os.link(p,root/'other')
    elif damage=='mode':p.chmod(0o644)
    elif damage=='duplicate':
        p.write_bytes(b'{"phase":"a","phase":"b"}');h=m.sha(p.read_bytes())
    else:h='0'*64
    with pytest.raises(ValueError):m.admit(p,h,execute=False)
    assert not (root/'CONSUMED.json').exists()

def test_quiescence_failure_preserves_consumption(admission,monkeypatch):
    root,create=admission;p,h=create()
    def refuse():raise ValueError('native still present')
    monkeypatch.setattr(m,'quiescent',refuse)
    with pytest.raises(ValueError,match='native still present'):m.admit(p,h,execute=False)
    assert 'native still present' in (root/'FIRST_FAILURE.json').read_text()
    with pytest.raises(FileExistsError):m.admit(p,h,execute=False)

def test_kernel_predicate_refuses_native(monkeypatch):
    class Result:stdout='/usr/bin/python3\n/java/bin/java\n'
    monkeypatch.setattr(m.subprocess,'run',lambda *a,**k:Result())
    with pytest.raises(ValueError,match='native process'):m.quiescent()

def test_read_exact_detects_midread_mutation(tmp_path,monkeypatch):
    p=tmp_path/'config';p.write_bytes(b'abc'); original=m.os.read; once=True
    def mutate(fd,n):
        nonlocal once
        data=original(fd,n)
        if once: once=False;p.chmod(0o640)
        return data
    monkeypatch.setattr(m.os,'read',mutate)
    with pytest.raises(ValueError,match='changed during read'):m.read_exact(p)

def test_execve_preserves_owner_pid_and_fixed_argv(admission,monkeypatch):
    root,create=admission;p,h=create(); pid=os.getpid(); captured=[]
    monkeypatch.setattr(m,'quiescent',lambda:None)
    monkeypatch.setattr(m.os,'chdir',lambda p:None)
    def execve(binary,argv,env):
        captured.append((os.getpid(),binary,argv,env))
        raise RuntimeError('software intercepted exec boundary')
    monkeypatch.setattr(m.os,'execve',execve)
    with pytest.raises(RuntimeError,match='intercepted'):m.admit(p,h)
    assert captured[0][0]==pid
    assert captured[0][1]==captured[0][2][0]
    assert (root/'CONSUMED.json').exists()
