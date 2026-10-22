"""Real local Java/software startup controls; no Windows/native admission proof."""
from dataclasses import replace
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import textwrap

import pytest

from comsol_mcp import _java_worker as worker_module, _session_server as server, _windows_t037 as t037
from tests.test_java_worker import COMSOL_ROOT, JDK11
from tests.test_session_server import _startup_runtime
from tests.test_windows_fixed_listeners import factory_for
from tests.test_windows_network_intent import network_profile_and_plan

ROOT=Path(__file__).resolve().parents[1]
JAVA_SOURCE=ROOT/'comsol_mcp/worker_java/PersistentComsolWorker.java'
HARNESS=ROOT/'tests/java/PersistentComsolWorkerFixedListenerHarness.java'


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope='module')
def java_setup(tmp_path_factory):
    output=os.environ.get('COMSOL_MCP_FIXED_LISTENER_TEST_EVIDENCE')
    base=Path(output) if output else tmp_path_factory.mktemp('fixed-listener-java-software')
    base.mkdir(parents=True,exist_ok=True)
    if (base/'java-compile.json').exists():raise AssertionError('REFUSE_TEST_EVIDENCE_OVERWRITE')
    javac=JDK11/'bin'/('javac.exe' if sys.platform=='win32' else 'javac')
    java=JDK11/'bin'/('java.exe' if sys.platform=='win32' else 'java')
    if not javac.is_file() or not java.is_file():pytest.skip('NOT_RUN: existing JDK11 compiler/runtime unavailable')
    jars=sorted((COMSOL_ROOT/'apiplugins').glob('*.jar'))
    if not jars:pytest.skip('NOT_RUN: existing API classpath unavailable for production Worker compile')
    classes=base/'classes';classes.mkdir();classpath=os.pathsep.join(map(str,jars))
    command=[str(javac),'-encoding','UTF-8','-cp',classpath,'-d',str(classes),str(JAVA_SOURCE),str(HARNESS)]
    completed=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=120)
    (base/'java-compile.stdout').write_text(completed.stdout);(base/'java-compile.stderr').write_text(completed.stderr)
    receipt={'command':command,'exit':completed.returncode,'production_source_sha256':digest(JAVA_SOURCE),
        'harness_source_sha256':digest(HARNESS),'scope':'Compile only; no COMSOL Server/GUI or ModelUtil call'}
    (base/'java-compile.json').write_text(json.dumps(receipt,indent=2)+'\n')
    assert completed.returncode==0,completed.stdout+completed.stderr
    return base,java,classes,classpath


@pytest.mark.parametrize('prefer_ipv6',['true','false'])
def test_real_constructor_and_ready_bind_ipv4_even_when_default_prefers_ipv6(java_setup,prefer_ipv6):
    base,java,classes,classpath=java_setup
    tmp=base/('jvm-tmp-'+prefer_ipv6);tmp.mkdir()
    command=[str(java),'-Djava.net.preferIPv6Addresses='+prefer_ipv6,'-Djava.io.tmpdir='+str(tmp),
        '-cp',str(classes)+os.pathsep+classpath,'comsol_mcp.worker_java.PersistentComsolWorkerFixedListenerHarness',
        str(base/('probe-'+prefer_ipv6))]
    completed=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=30)
    (base/('probe-'+prefer_ipv6+'.stdout')).write_text(completed.stdout)
    (base/('probe-'+prefer_ipv6+'.stderr')).write_text(completed.stderr)
    receipt={'command':command,'exit':completed.returncode,'production_source_sha256':digest(JAVA_SOURCE),
        'harness_source_sha256':digest(HARNESS),'actual_main_class_sha256':digest(classes/'comsol_mcp/worker_java/PersistentComsolWorker.class')}
    (base/('probe-'+prefer_ipv6+'.json')).write_text(json.dumps(receipt,indent=2)+'\n')
    assert completed.returncode==0,completed.stdout+completed.stderr
    line=next(line for line in completed.stdout.splitlines() if line.startswith('FIXED_LISTENER_SOFTWARE_PASS '))
    result=json.loads(line.split(' ',1)[1]);assert result['occupied_fixed_port_refused']
    assert result['cases'][0]['requested_port']==0 and result['cases'][1]['requested_port']>0
    assert all(r['actual_host']==r['ready_host']=='127.0.0.1' and r['actual_port']==r['ready_port']
        and r['actual_tcp_accept'] for r in result['cases'])
    assert result['COMSOL_calls']==0 and result['native_admission']=='NOT_RUN'


def test_full_python_fixed_startup_rejects_ordinary_popen_as_original_t037_owner(java_setup,monkeypatch,tmp_path):
    """Ordinary local children cannot supply the private Windows HANDLE owner.

    The earlier successful software-factory probe is historical. This stronger
    consumer must refuse its missing native owner, even with valid ready/PID
    observations. The Java-only constructor probes retain their original scope.
    """
    base,java,classes,classpath=java_setup
    # Reserve both candidate numbers simultaneously, then try each exact number
    # once. A collision is a test failure, not a request to choose another port.
    with ExitStack() as stack:
        reservations=[stack.enter_context(socket.socket(socket.AF_INET,socket.SOCK_STREAM)) for _ in range(2)]
        for reservation in reservations:reservation.bind(('127.0.0.1',0))
        server_port,worker_port=[s.getsockname()[1] for s in reservations]
    host,_=network_profile_and_plan();grants=list(host.windows_isolation.wfp_grants)
    grants[0]=replace(grants[0],remote_ports=(server_port,));grants[1]=replace(grants[1],local_ports=(server_port,))
    grants[2]=replace(grants[2],local_ports=(worker_port,))
    factory=factory_for(replace(host,windows_isolation=replace(host.windows_isolation,wfp_grants=tuple(grants))))
    runtime=_startup_runtime(tmp_path/'server-fixture')
    original_popen=subprocess.Popen
    original_children=[];launches=[]
    # This does not authorize Windows or emulate native isolation. Both real
    # test children are local, ordinary, unconnected software harnesses.
    monkeypatch.setattr(t037.T037SessionProcessFactory,'preflight',lambda self:None)
    def software_factory(self,command,**options):
        assert self is factory
        if '-portfile' in command:
            code=textwrap.dedent('''
                import pathlib,socket,sys
                with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as listener:
                    listener.bind(('127.0.0.1',int(sys.argv[1])));listener.listen(1)
                    pathlib.Path(sys.argv[2]).write_text(str(listener.getsockname()[1]))
                    while True:
                        connection,address=listener.accept();connection.close()
            ''')
            actual=[sys.executable,'-c',code,command[command.index('-port')+1],command[command.index('-portfile')+1]]
            role='software-server'
        else:
            actual=list(command);role='software-unconnected-java-worker'
        child=original_popen(actual,**options)
        original_children.append(child)
        launches.append({'role':role,'requested_command':list(command),'actual_command':actual,'pid':child.pid})
        return child
    monkeypatch.setattr(t037.T037SessionProcessFactory,'__call__',software_factory)
    monkeypatch.setenv('JAVA_TOOL_OPTIONS','-Djava.net.preferIPv6Addresses=true')  # this test's ordinary JVM only
    launcher=server.OwnedServerLauncher(process_factory=factory,platform_name='darwin',ready_timeout_s=15,poll_interval_s=0.02)
    paths=worker_module.JavaWorkerPaths(COMSOL_ROOT,JDK11,project_root=tmp_path)
    worker=worker_module.PersistentJavaWorker(paths,state_dir=tmp_path/'worker',process_factory=factory)
    record={'scope':'FULL_PYTHON_STARTUP_WITH_LOCAL_SOFTWARE_FACTORY_NOT_T037_NATIVE',
        'production_source_sha256':digest(JAVA_SOURCE),'profile_sha256':factory.profile.sha256,
        'source_profile_sha256':factory.profile.source_sha256,'native_preflight_replaced_in_test_only':True,
        'expected_server_port':server_port,'expected_worker_port':worker_port,
        'COMSOL_Server_GUI_starts':0,'Windows_host_changes':0,'native_admission':'NOT_RUN'}
    try:
        handle=launcher.start(runtime,'project','session')
        monkeypatch.setattr(worker_module.socket,'create_connection',lambda *a,**kw:pytest.fail('RPC without original T037 owner'))
        with pytest.raises(worker_module.JavaWorkerError) as caught:worker.start(startup_timeout_s=30)
        ready=json.loads((worker.state_dir/'worker_endpoint.json').read_text())
        assert caught.value.execution_state_unknown and worker._startup_unknown is caught.value
        assert handle.endpoint==handle.expected_endpoint==factory.network_endpoint('server')
        assert worker._expected_endpoint==('127.0.0.1',worker_port)
        assert handle.network_intent==worker._network_intent==factory.network_intent
        assert ready['host']=='127.0.0.1' and ready['port']==worker_port and ready['pid']==worker._process.pid
        assert ready['process_start_epoch_ms']==worker_module.process_identity(worker._process.pid)['start_epoch_ms']
        assert worker._fixed_process is worker._process and worker._fixed_launch_owner is None
        assert t037._native_g2_session_launch_owner(worker._process) is None
        assert worker._listener_identity is None and worker._port is None and worker._generation is None
        with pytest.raises(worker_module.JavaWorkerError):worker.endpoint
        with pytest.raises(worker_module.JavaWorkerError):worker.close()
        cache=json.loads((worker._classes_dir/'cache_receipt.json').read_text())
        assert cache['source_sha256']==digest(JAVA_SOURCE)
        record.update(state='SOFTWARE_STARTUP_REFUSED_NO_ORIGINAL_T037_OWNER',server_endpoint={'host':handle.endpoint.address,'port':handle.endpoint.port},
            worker_expected_endpoint=worker._expected_endpoint,
            ready={k:v for k,v in ready.items() if k!='token'},ready_token_matches_launch=ready['token']==worker._token,
            ordinary_popen_is_not_native_owner=True,worker_identity='UNKNOWN_ORIGINAL_PROCESS_RETAINED',RPC_calls=0,
            worker_compile_receipt=cache,actual_worker_class_sha256=digest(worker._classes_dir/'comsol_mcp/worker_java/PersistentComsolWorker.class'))
        stopped=launcher.stop(handle,timeout_s=5)
        record['production_cleanup']={'worker_original_retained':worker._process is not None,'server_stop':stopped}
    except BaseException as exc:
        record.update(state='FAILED_SOFTWARE_STARTUP',error_type=type(exc).__name__,message=str(exc),
            worker_unknown=worker._startup_unknown is not None,original_worker_retained=worker._process is not None)
        raise
    finally:
        # Test-fixture owner finalization of only the exact harmless Popen
        # objects created above; never PID lookup or production unknown cleanup.
        cleanup=[]
        for child in original_children:
            if child.poll() is None:child.terminate()
            code=child.wait(timeout=10);cleanup.append({'pid':child.pid,'returncode':code,'exact_original_popen_reaped':True})
        record['launches']=launches;record['test_fixture_original_children_cleanup']=cleanup
        (base/'full-python-startup.json').write_text(json.dumps(record,indent=2,default=lambda x:x.__dict__)+'\n')
