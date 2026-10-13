"""Actual TLS1.2/1.3 MemoryBIO handshake with fixed public CPython fixture."""
from pathlib import Path
import time
import importlib.util
def checked_fixture(path,expected):
    spec=importlib.util.spec_from_file_location("fixed_cert_checks",Path(__file__).with_name("tls_cert_checks_r4.py"))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.checked_fixture(path,expected)

CERT_PIN={'bytes':4058,'sha256':'cfd723672e8205a30f93aa14e24ef5b0264c41db367b50cfa0b6fcb83d881947'}

def actual_memorybio_cases(ssl,certificate,clock=time.monotonic):
    certificate=Path(certificate);checked_fixture(certificate,CERT_PIN)
    default=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if default.minimum_version<ssl.TLSVersion.TLSv1_2 or default.security_level<2:raise RuntimeError('default TLS floor/security regressed')
    result=[]
    for version in (ssl.TLSVersion.TLSv1_2,ssl.TLSVersion.TLSv1_3):
        server_context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);server_context.load_cert_chain(str(certificate))
        client_context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        # This is the original public test certificate, not a trust-store test.
        # The transport is only in-memory, with no socket object or endpoint.
        client_context.check_hostname=False;client_context.verify_mode=ssl.CERT_NONE
        for context in (server_context,client_context):context.minimum_version=version;context.maximum_version=version
        incoming=[ssl.MemoryBIO(),ssl.MemoryBIO()];outgoing=[ssl.MemoryBIO(),ssl.MemoryBIO()]
        client=client_context.wrap_bio(incoming[0],outgoing[0],server_side=False,server_hostname='localhost')
        server=server_context.wrap_bio(incoming[1],outgoing[1],server_side=True)
        def pump():
            for source,target in ((outgoing[0],incoming[1]),(outgoing[1],incoming[0])):
                while source.pending:target.write(source.read())
        completed=[False,False];start=clock();iterations=0
        while not all(completed):
            iterations+=1
            if iterations>128 or clock()-start>15:raise RuntimeError('bounded actual MemoryBIO handshake did not complete')
            for i,actor in enumerate((client,server)):
                if completed[i]:continue
                try:actor.do_handshake();completed[i]=True
                except (ssl.SSLWantReadError,ssl.SSLWantWriteError):pass
                pump()
        expected='TLSv1.2' if version==ssl.TLSVersion.TLSv1_2 else 'TLSv1.3'
        if client.version()!=expected or server.version()!=expected:raise RuntimeError('actual TLS negotiation differs from required version')
        message=b'comsol-public-memorybio-fixture';client.write(message);pump()
        if server.read(len(message))!=message:raise RuntimeError('actual encrypted MemoryBIO payload differs')
        result.append({'required_version':expected,'client_version':client.version(),'server_version':server.version(),'client_cipher':client.cipher(),'server_cipher':server.cipher(),'actual_handshake_complete':True,'encrypted_payload_roundtrip':True,'iterations':iterations,'elapsed_s':clock()-start})
    return {'actual_cases':result,'default_TLS_minimum':default.minimum_version.name,'default_security_level':default.security_level,'fixture':CERT_PIN,'sockets_created':0,'network_connections':0}
