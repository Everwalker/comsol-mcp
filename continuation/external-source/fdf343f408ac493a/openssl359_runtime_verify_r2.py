"""Consume the new exact profile and immutable installed R4 bytes."""
import argparse,json
from pathlib import Path
from intel_crypto_successor_r1 import checkpoint_bytes
from intel_deps_prepare import exclusive_json
from intel_deps_stages import tree_record
import openssl359_cpython_successor_r4 as prior
from openssl359_runtime_successor_r2 import ROOT,INSTALLED,verify_inputs
from openssl359_runtime_profile_r3 import validate_provider,validate_identity
def validate_profile(profile):
    validate_provider(profile['provider_info'],profile['provider_number'],profile['provider']);validate_identity(INSTALLED/'cpython',profile['executable'])
    if profile['actual_machine']!='arm64' or not profile['official_unmodified_CPython'] or profile['source_ssl_sha256']!='19beed620c8b9eaec82e21235cf83f505e279ad8ede63652616d3fedea4a1710' or profile['software_variant']!='CPython3.12.13-official+OpenSSL3.5.9-official':raise RuntimeError('actual runtime/source identity differs')
    if profile['_ssl']!=str(INSTALLED/'cpython/lib/python3.12/lib-dynload/_ssl.cpython-312-darwin.so') or profile['_hashlib']!=str(INSTALLED/'cpython/lib/python3.12/lib-dynload/_hashlib.cpython-312-darwin.so'):raise RuntimeError('actual runtime module outside fixed installed prefix')
    if not profile['sha256_known_vector'] or not profile['ssl_context_created'] or profile['network_connections']!=0 or profile['x86_runtime']!='NOT_RUN':raise RuntimeError('actual runtime known vector/scope differs')
    cert=profile['actual_certificate_cases'];bio=profile['actual_MemoryBIO']
    if not cert['malformed_negative'] or not cert['IPv4_IPv6_SAN_URI_AIA_CRL'] or len(cert['cases'])!=4 or cert['socket_calls']!=0:raise RuntimeError('actual certificate evidence absent')
    cases=bio['actual_cases']
    if len(cases)!=2 or bio['sockets_created']!=0 or bio['network_connections']!=0 or bio['default_TLS_minimum']!='TLSv1_2' or bio['default_security_level']<2:raise RuntimeError('actual MemoryBIO/default TLS evidence absent')
    for case,expected in zip(cases,('TLSv1.2','TLSv1.3')):
        if case['required_version']!=expected or case['client_version']!=expected or case['server_version']!=expected or not case['actual_handshake_complete'] or not case['encrypted_payload_roundtrip']:raise RuntimeError('actual two negotiated MemoryBIO versions differ')
    for name in ('zlib','sqlite3','ctypes','venv'):
        if profile['modules'][name]['status']!='IMPORTED':raise RuntimeError('actual required module regression')
    if profile['_lzma_requirement']!='REQUIRED_PRODUCT_TAR_XZ_CAPABILITY':raise RuntimeError('required lzma scope changed')
    return profile
def consume_profile(root):
    if root!=ROOT:raise RuntimeError('fixed fresh runtime namespace differs')
    path=root/'ARM_RUNTIME_PROFILE.json';first=prior.record(path,maximum=1048576)
    raw,_=checkpoint_bytes(path,first);profile=validate_profile(json.loads(raw))
    return first,profile
def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args()
    first,profile=consume_profile(a.root);preservation=verify_inputs()
    bins=prior.actual_binaries(prior.layout());installed=tree_record(INSTALLED/'cpython')
    # Keep the first actual accepted profile record through all later reads.
    checkpoint_bytes(a.root/'ARM_RUNTIME_PROFILE.json',first)
    exclusive_json(a.root/'ACTUAL_RUNTIME_AND_INSTALL.json',{'actual_profile_first_record':first,'actual_arm_profile':profile,'binary':bins,'installed_tree':installed,'whole_inputs_preservation':preservation,'x86_runtime':'NOT_RUN'})
if __name__=='__main__':main()
