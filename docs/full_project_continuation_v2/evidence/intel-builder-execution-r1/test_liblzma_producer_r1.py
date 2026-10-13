"""Actual producer main/consumer; public identity and cert/BIO boundary spies.

Host known vectors, SSLContext floor and required imports run genuinely.
This does not execute the installed target interpreter or accept target TLS.
"""
import json
from pathlib import Path
import pytest
import liblzma_runtime_profile_r1 as producer
import liblzma_verify_r1 as verifier
import liblzma_successor_r1 as actor
from openssl359_runtime_successor_r2 import INSTALLED as ORIGINAL

def public_main_boundary(monkeypatch,tmp_path):
    installed=tmp_path/'public-installed';output=tmp_path/'public-runtime-r2'
    output.mkdir();prefix=installed/'cpython';prefix.mkdir(parents=True)
    source=installed/'cpython-input/Python-3.12.13/Modules/_ssl.c';source.parent.mkdir(parents=True)
    source.write_bytes((ORIGINAL/'cpython-input/Python-3.12.13/Modules/_ssl.c').read_bytes())
    helper=tmp_path/'public-helper';helper.mkdir();trace=tmp_path/'boundary-spy.jsonl'
    cert={'actual_backend':'PUBLIC_BOUNDARY_SPY_NON_TARGET','cases':[{}, {}, {}, {}],'malformed_negative':True,'IPv4_IPv6_SAN_URI_AIA_CRL':True,'socket_calls':0}
    bio={'actual_cases':[{'required_version':v,'client_version':v,'server_version':v,'actual_handshake_complete':True,'encrypted_payload_roundtrip':True} for v in ('TLSv1.2','TLSv1.3')],'default_TLS_minimum':'TLSv1_2','default_security_level':2,'sockets_created':0,'network_connections':0}
    for filename,function,value in [('liblzma_cert_checks_r1.py','runtime_cert_cases',cert),('openssl359_memorybio_r1.py','actual_memorybio_cases',bio)]:
        text='from pathlib import Path\nimport json\ndef '+function+'(ssl,argument):\n with Path('+repr(str(trace))+').open("a") as f:f.write(json.dumps({"boundary":'+repr(function)+',"argument":str(argument)})+"\\n")\n return '+repr(value)+'\n'
        (helper/filename).write_text(text)
    monkeypatch.setattr(producer,'INSTALLED',installed);monkeypatch.setattr(producer,'OUTPUT_ROOT',output);monkeypatch.setattr(producer,'__file__',str(helper/'profile.py'))
    monkeypatch.setattr(producer.platform,'machine',lambda:'arm64')
    monkeypatch.setattr(producer.sys,'prefix',str(prefix));monkeypatch.setattr(producer.sys,'executable',str(prefix/'bin/python3.12'));monkeypatch.setattr(producer.sys,'version_info',(3,12,13,'final',0))
    for module,name in ((producer._ssl,'_ssl'),(producer._hashlib,'_hashlib')):monkeypatch.setattr(module,'__file__',str(prefix/'lib/python3.12/lib-dynload'/(name+'.cpython-312-darwin.so')))
    for module,name in ((producer.ssl,'ssl'),(producer.hashlib,'hashlib')):monkeypatch.setattr(module,'__file__',str(prefix/'lib/python3.12'/(name+'.py')))
    monkeypatch.setattr(producer.ssl,'OPENSSL_VERSION_INFO',(3,5,0,9,0));monkeypatch.setattr(producer.ssl,'OPENSSL_VERSION_NUMBER',0x30500090);monkeypatch.setattr(producer.ssl,'OPENSSL_VERSION','OpenSSL 3.5.9 29 Sep 2026')
    import _lzma
    monkeypatch.setattr(_lzma,'__file__',str(prefix/'lib/python3.12/lib-dynload/_lzma.cpython-312-darwin.so'))
    monkeypatch.setattr(verifier,'ROOT',output);monkeypatch.setattr(verifier,'INSTALLED',installed)
    return installed,output,trace

def test_actual_producer_main_happy_path_reaches_certs_bio_and_firstrecord_consumer(monkeypatch,tmp_path,capsys):
    installed,output,trace=public_main_boundary(monkeypatch,tmp_path)
    producer.main(str(output/'ARM_RUNTIME_PROFILE.json'))
    rows=[json.loads(line) for line in trace.read_text().splitlines()]
    assert rows==[{'boundary':'runtime_cert_cases','argument':str(installed)},{'boundary':'actual_memorybio_cases','argument':str(installed/'runtime-certfixtures/keycert.pem')}]
    first,result=verifier.consume_profile(output)
    assert result['actual_lzma_roundtrip'] and result['actual_tar_xz_product_roundtrip']
    assert result['sha256_known_vector'] and result['ssl_context_created']
    assert all(result['modules'][n]['status']=='IMPORTED' for n in ('zlib','sqlite3','ctypes','venv'))
    assert result['provider_info']==[3,5,0,9,0] and result['provider_number']==0x30500090
    assert first['sha256']==producer.hashlib.sha256((output/'ARM_RUNTIME_PROFILE.json').read_bytes()).hexdigest()
    assert 'ACTUAL_PROVIDER_FIRST_READ' in capsys.readouterr().out

def test_actual_producer_required_import_failure_stops_before_cert_bio_and_output(monkeypatch,tmp_path):
    installed,output,trace=public_main_boundary(monkeypatch,tmp_path)
    original=producer.importlib.import_module;attempts=[]
    def fail_required(name,*args,**kwargs):
        attempts.append(name)
        if name=='_lzma':raise ImportError('public controlled required import failure')
        return original(name,*args,**kwargs)
    monkeypatch.setattr(producer.importlib,'import_module',fail_required)
    with pytest.raises(RuntimeError,match='regressed: _lzma'):producer.main(str(output/'ARM_RUNTIME_PROFILE.json'))
    assert attempts==['zlib','sqlite3','ctypes','venv','_lzma','_gdbm','_tkinter']
    assert not trace.exists() and not (output/'ARM_RUNTIME_PROFILE.json').exists()

