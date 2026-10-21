"""Real compiler admission for the production Worker, without engine or Worker birth."""
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMSOL = (Path(r'C:\Program Files\COMSOL\COMSOL64\Multiphysics') if sys.platform == 'win32'
                  else Path('/Applications/COMSOL64/Multiphysics'))
COMSOL_ROOT = Path(os.environ.get('COMSOL_ROOT', str(DEFAULT_COMSOL))).expanduser()
DEFAULT_JDK11 = (Path(r'C:\Users\Everwalker\jdk11') if sys.platform == 'win32'
                 else Path('/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home'))
FIXTURES = {
    'P0ParameterReadback.java': '7b2d3c928706557446eb879742cb431ef74296cfdd6cd46770db237f33b1019f',
    'P0ExportJava.java': '62c98af3a254f769adc7ff9cfd21c15087ac19d84fa5cd66ba206db282323cfe',
}


def _version(home):
    release = home / 'release'
    if not release.is_file():
        return None
    found = re.search(r'^JAVA_VERSION="(\d+)', release.read_text(encoding='utf-8'), re.MULTILINE)
    return int(found.group(1)) if found else None


def _jdk(kind):
    if kind == 'externalJDK11':
        configured = os.environ.get('COMSOL_TEST_JDK11') or os.environ.get('COMSOL_JAVA_HOME') or os.environ.get('JAVA_HOME')
        candidate = Path(configured).expanduser() if configured else DEFAULT_JDK11
        # A JAVA_HOME pointing at another major is not Java11 qualification.
        if _version(candidate) != 11 and not os.environ.get('COMSOL_TEST_JDK11'):
            candidate = DEFAULT_JDK11
        candidates = [candidate] if _version(candidate) == 11 else []
    else:
        configured = os.environ.get('COMSOL_TEST_BUNDLED_JAVA_HOME')
        candidates = ([Path(configured).expanduser()] if configured else
                      sorted(p.parent for p in (COMSOL_ROOT / 'java').rglob('release')))
        candidates = [p for p in candidates if _version(p) == 21]
    suffix = '.exe' if sys.platform == 'win32' else ''
    candidates = [p for p in candidates if all((p / 'bin' / (name + suffix)).is_file() for name in ('java', 'javac'))]
    if len(candidates) != 1:
        pytest.skip(f'SKIP_ENV/NOT_RUN: {kind} exact compiler environment unavailable or ambiguous; configure COMSOL_TEST_JDK11/COMSOL_TEST_BUNDLED_JAVA_HOME')
    return candidates[0], suffix


@pytest.mark.parametrize('kind', ['externalJDK11', 'bundledJDK21'])
def test_actual_production_source_snapshot_compiler(kind, tmp_path):
    jars = sorted(p for p in (COMSOL_ROOT / 'apiplugins').glob('*.jar') if not p.name.startswith('._'))
    if not jars:
        pytest.skip('SKIP_ENV/NOT_RUN: COMSOL SDK apiplugins unavailable; configure COMSOL_ROOT')
    jdk, suffix = _jdk(kind)
    for name, digest in FIXTURES.items():
        assert hashlib.sha256((ROOT / 'tools/java' / name).read_bytes()).hexdigest() == digest
    source = ROOT / 'comsol_mcp/worker_java/PersistentComsolWorker.java'
    harness = ROOT / 'tests/java/PersistentComsolWorkerSourceCompilerHarness.java'
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    classes = tmp_path / 'classes'
    classes.mkdir()
    classpath = os.pathsep.join(map(str, jars))
    javac = [str(jdk / 'bin' / ('javac' + suffix)), '-encoding', 'UTF-8', '-classpath', classpath,
             '-d', str(classes), str(source), str(harness)]
    java = [str(jdk / 'bin' / ('java' + suffix)), '-cp', str(classes) + os.pathsep + classpath,
            'comsol_mcp.worker_java.PersistentComsolWorkerSourceCompilerHarness', str(tmp_path / 'input'), str(ROOT / 'tools/java')]
    c = subprocess.run(javac, capture_output=True, text=True, timeout=120)
    (tmp_path / 'javac.log').write_text(c.stdout + c.stderr)
    assert c.returncode == 0, c.stdout + c.stderr
    r = subprocess.run(java, capture_output=True, text=True, timeout=45)
    (tmp_path / 'java.log').write_text(r.stdout + r.stderr)
    assert r.returncode == 0, r.stdout + r.stderr
    assert 'PASS_SOURCE_COMPILER' in r.stdout and 'Worker_constructor=NOT_RUN' in r.stdout
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
