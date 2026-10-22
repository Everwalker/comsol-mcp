"""Actual Java Worker RequestState JSON snapshots and strict recovery admission."""
import json
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import shutil

import pytest

from comsol_mcp._control_daemon import ControlDaemon


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'comsol_mcp/worker_java/PersistentComsolWorker.java'
DEFAULT_COMSOL = (Path(r'C:\Program Files\COMSOL\COMSOL64\Multiphysics') if sys.platform == 'win32'
                  else Path('/Applications/COMSOL64/Multiphysics'))
COMSOL_ROOT = Path(os.environ.get('COMSOL_ROOT', str(DEFAULT_COMSOL))).expanduser()
DEFAULT_JDK11 = (Path(r'C:\Users\Everwalker\jdk11') if sys.platform == 'win32'
                 else Path('/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home'))


HARNESS = r'''package comsol_mcp.worker_java;

import java.lang.reflect.Constructor;
import java.lang.reflect.Method;
import java.util.LinkedHashMap;
import java.util.Map;

/** Calls only RequestState/Json; never constructs a Worker or connects to COMSOL. */
public final class RequestStateSnapshotHarness {
  @SuppressWarnings("unchecked")
  private static Map<String,Object> roundTrip(Object state, Method snapshot) throws Exception {
    Object raw = snapshot.invoke(state);
    String json = PersistentComsolWorker.Json.write(raw);
    Object parsed = PersistentComsolWorker.Json.parse(json);
    if (!(parsed instanceof Map)) throw new AssertionError("snapshot JSON is not an object");
    return (Map<String,Object>) parsed;
  }

  private static Object state(Constructor<?> constructor, String id) throws Exception {
    return constructor.newInstance(id, "run", "request-hash-" + id);
  }

  public static void main(String[] args) throws Exception {
    Class<?> type = Class.forName("comsol_mcp.worker_java.PersistentComsolWorker$RequestState");
    Constructor<?> constructor = type.getDeclaredConstructor(String.class, String.class, String.class);
    constructor.setAccessible(true);
    Method succeed = type.getDeclaredMethod("succeed", Object.class);
    Method fail = type.getDeclaredMethod("fail", Map.class);
    Method start = type.getDeclaredMethod("start");
    Method snapshot = type.getDeclaredMethod("snapshot");
    succeed.setAccessible(true); fail.setAccessible(true); start.setAccessible(true); snapshot.setAccessible(true);

    Map<String,Object> replies = new LinkedHashMap<>();
    Object nullSuccess = state(constructor, "worker-request");
    succeed.invoke(nullSuccess, new Object[] { null });
    replies.put("null_success", roundTrip(nullSuccess, snapshot));

    Object valueSuccess = state(constructor, "value-success");
    Map<String,Object> value = new LinkedHashMap<>(); value.put("answer", 7);
    succeed.invoke(valueSuccess, value);
    replies.put("value_success", roundTrip(valueSuccess, snapshot));

    Object queued = state(constructor, "queued");
    replies.put("queued", roundTrip(queued, snapshot));

    Object running = state(constructor, "running"); start.invoke(running);
    replies.put("running", roundTrip(running, snapshot));

    Object failed = state(constructor, "worker-request");
    Map<String,Object> failure = new LinkedHashMap<>(); failure.put("code", "TEST_FAILURE");
    fail.invoke(failed, failure);
    replies.put("failed", roundTrip(failed, snapshot));

    System.out.println(PersistentComsolWorker.Json.write(replies));
  }
}
'''


@pytest.fixture(scope='module')
def java_snapshots(tmp_path_factory):
    jdk = Path(os.environ.get('COMSOL_TEST_JDK11', str(DEFAULT_JDK11))).expanduser()
    release = jdk / 'release'
    if not release.is_file():
        pytest.skip('SKIP_ENV/NOT_RUN: exact JDK 11 compiler unavailable')
    version = re.search(r'^JAVA_VERSION="(\d+)', release.read_text(encoding='utf-8'), re.MULTILINE)
    if not version or int(version.group(1)) != 11:
        pytest.skip('SKIP_ENV/NOT_RUN: configured compiler is not JDK 11')
    jars = sorted(p for p in (COMSOL_ROOT / 'apiplugins').glob('*.jar') if not p.name.startswith('._'))
    if not jars:
        pytest.skip('SKIP_ENV/NOT_RUN: COMSOL 6.4 Java API compile classpath unavailable')

    work = tmp_path_factory.mktemp('worker-void-reply')
    classes = work / 'classes'
    classes.mkdir()
    harness = work / 'RequestStateSnapshotHarness.java'
    harness.write_text(HARNESS, encoding='utf-8')
    classpath = os.pathsep.join(map(str, jars))
    source_hash_before = __import__('hashlib').sha256(SOURCE.read_bytes()).hexdigest()
    compile_result = subprocess.run(
        [str(jdk / 'bin' / 'javac'), '-proc:none', '-encoding', 'UTF-8', '-classpath', classpath,
         '-d', str(classes), str(SOURCE), str(harness)],
        capture_output=True, text=True, timeout=120,
    )
    (work / 'javac.log').write_text(compile_result.stdout + compile_result.stderr, encoding='utf-8')
    assert compile_result.returncode == 0, compile_result.stdout + compile_result.stderr
    run_result = subprocess.run(
        [str(jdk / 'bin' / 'java'), '-cp', str(classes) + os.pathsep + classpath,
         'comsol_mcp.worker_java.RequestStateSnapshotHarness'],
        capture_output=True, text=True, timeout=30,
    )
    (work / 'java.log').write_text(run_result.stdout + run_result.stderr, encoding='utf-8')
    assert run_result.returncode == 0, run_result.stdout + run_result.stderr
    source_hash_after = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    assert source_hash_after == source_hash_before
    evidence_dir = os.environ.get('COMSOL_TEST_VOID_REPLY_EVIDENCE_DIR')
    if evidence_dir:
        retained = Path(evidence_dir)
        retained.mkdir(parents=True, exist_ok=True)
        shutil.copy2(work / 'javac.log', retained / 'javac.log')
        shutil.copy2(work / 'java.log', retained / 'java.log')
        (retained / 'request-state-snapshots.json').write_text(run_result.stdout, encoding='utf-8')
        (retained / 'harness-receipt.json').write_text(json.dumps({
            'source': str(SOURCE), 'source_sha256_before': source_hash_before,
            'source_sha256_after': source_hash_after, 'jdk_home': str(jdk),
            'comsol_java_api_root': str(COMSOL_ROOT), 'classpath_jar_count': len(jars),
            'javac_options': ['-proc:none', '-encoding', 'UTF-8'],
            'java_exit_code': run_result.returncode,
            'scope': 'RequestState.snapshot and Json serialization only; no Worker construction, COMSOL connection, or engine start',
        }, indent=2) + '\n', encoding='utf-8')
    return json.loads(run_result.stdout), work


def _recovery_observation(reply):
    operation_id = 'source-operation'
    job_id = 'source-job'
    request_id = 'worker-request'
    if 'request_id' not in reply:
        reply = {**reply, 'request_id': request_id}
    event = {'event': 'worker_request', 'metadata': {
        'phase': 'submitted', 'operation_id': operation_id, 'request_id': request_id,
        'kind': 'run', 'request_hash': 'a' * 64,
    }}

    class RecoveryProbe(ControlDaemon):
        def __init__(self):
            self.events = [event]

        def _session_job_events(self, observed_job_id):
            assert observed_job_id == job_id
            return self.events

    class Worker:
        def status(self, observed_request_id, *, timeout_s):
            assert observed_request_id == request_id and timeout_s == 1
            return dict(reply)

    daemon = RecoveryProbe.__new__(RecoveryProbe)
    daemon.__init__()
    return daemon._read_session_job_requests(
        Worker(), {'job_id': job_id, 'operation_id': operation_id},
        {'operation_id': 'recovery-operation'}, timeout=1,
    )


def test_java_snapshot_preserves_successful_null_and_nonnull_results(java_snapshots):
    replies, _work = java_snapshots
    null_reply = replies['null_success']
    assert null_reply['status'] == 'SUCCEEDED'
    assert null_reply['request_id'] == 'worker-request' and null_reply['type'] == 'run'
    assert 'result' in null_reply and null_reply['result'] is None
    value_reply = replies['value_success']
    assert value_reply['status'] == 'SUCCEEDED' and value_reply['result'] == {'answer': 7}


def test_java_snapshot_does_not_invent_terminal_results(java_snapshots):
    replies, _work = java_snapshots
    assert replies['queued']['status'] == 'QUEUED' and 'result' not in replies['queued']
    assert replies['running']['status'] == 'RUNNING' and 'result' not in replies['running']
    failed = replies['failed']
    assert failed['status'] == 'FAILED' and 'result' not in failed
    assert failed['failure'] == {'code': 'TEST_FAILURE'}


def test_strict_recovery_consumer_accepts_actual_java_null_success(java_snapshots):
    replies, _work = java_snapshots
    observed = _recovery_observation(replies['null_success'])
    assert observed['terminal'] is True
    assert observed['_terminal_replies']['worker-request']['result'] is None


def test_strict_recovery_consumer_accepts_actual_java_failure_with_failure_object(java_snapshots):
    replies, _work = java_snapshots
    observed = _recovery_observation(replies['failed'])
    assert observed['terminal'] is True
    assert observed['_terminal_replies']['worker-request']['failure'] == {'code': 'TEST_FAILURE'}


@pytest.mark.parametrize('mutation', [
    'missing_result', 'wrong_request_id', 'wrong_type', 'queued', 'running', 'failed_without_failure',
])
def test_strict_recovery_consumer_refuses_incomplete_or_mismatched_replies(java_snapshots, mutation):
    replies, _work = java_snapshots
    reply = dict(replies['null_success'])
    if mutation == 'missing_result':
        reply.pop('result', None)
    elif mutation == 'wrong_request_id':
        reply['request_id'] = 'different-request'
    elif mutation == 'wrong_type':
        reply['type'] = 'connect'
    elif mutation == 'queued':
        reply = dict(replies['queued'])
        reply['request_id'] = 'worker-request'
    elif mutation == 'running':
        reply = dict(replies['running'])
        reply['request_id'] = 'worker-request'
    else:
        reply = {'request_id': 'worker-request', 'type': 'run', 'status': 'FAILED'}
    observed = _recovery_observation(reply)
    assert observed['terminal'] is False
    assert observed['requests'][0]['terminal'] is False
