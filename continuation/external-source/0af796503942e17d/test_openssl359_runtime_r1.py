"""Production provider/profile consumers: public software controls only."""
import copy,json
from pathlib import Path
import pytest
import openssl359_runtime_profile_r2 as profile
import openssl359_runtime_verify_r1 as verify
import openssl359_runtime_successor_r1 as actor
def sample():
    return {'provider_info':[3,5,0,9,0],'provider_number':0x30500090,'provider':'OpenSSL 3.5.9 29 Sep 2026','executable':str(actor.INSTALLED/'cpython/bin/python3.12'),'actual_machine':'arm64','official_unmodified_CPython':True,'source_ssl_sha256':'19beed620c8b9eaec82e21235cf83f505e279ad8ede63652616d3fedea4a1710','software_variant':'CPython3.12.13-official+OpenSSL3.5.9-official','_ssl':str(actor.INSTALLED/'cpython/lib/python3.12/lib-dynload/_ssl.cpython-312-darwin.so'),'_hashlib':str(actor.INSTALLED/'cpython/lib/python3.12/lib-dynload/_hashlib.cpython-312-darwin.so'),'sha256_known_vector':True,'ssl_context_created':True,'network_connections':0,'x86_runtime':'NOT_RUN','actual_certificate_cases':{'malformed_negative':True,'IPv4_IPv6_SAN_URI_AIA_CRL':True,'cases':[{}, {}, {}, {}],'socket_calls':0},'actual_MemoryBIO':{'actual_cases':[{'required_version':v,'client_version':v,'server_version':v,'actual_handshake_complete':True,'encrypted_payload_roundtrip':True} for v in ('TLSv1.2','TLSv1.3')],'sockets_created':0,'network_connections':0,'default_TLS_minimum':'TLSv1_2','default_security_level':2},'modules':{n:{'status':'IMPORTED'} for n in ('zlib','sqlite3','ctypes','venv')},'_lzma_requirement':'REQUIRED_PRODUCT_TAR_XZ_CAPABILITY'}
def test_correct_provider_and_actual_profile_consumer_positive():
    r=sample();profile.validate_provider(r['provider_info'],r['provider_number'],r['provider']);assert verify.validate_profile(r) is r
@pytest.mark.parametrize('index',range(5))
def test_each_provider_tuple_component_refuses_with_actual_values(index):
    r=sample();r['provider_info'][index]+=1
    with pytest.raises(RuntimeError,match='provider_info') as exc:verify.validate_profile(r)
    assert str(r['provider_number']) in str(exc.value) and r['provider'] in str(exc.value)
@pytest.mark.parametrize('field,value',[('provider_number',0x30500091),('provider','OpenSSL 3.5.8 7 Apr 2026'),('executable','/outside/public/python3.12'),('_ssl','/outside/public/_ssl.so'),('_hashlib','/outside/public/_hashlib.so'),('source_ssl_sha256','0'*64),('actual_machine','x86_64'),('sha256_known_vector',False)])
def test_wrong_provider_or_profile_identity_refuses(field,value):
    r=sample();r[field]=value
    with pytest.raises(RuntimeError):verify.validate_profile(r)
def test_outside_prefix_refuses():
    with pytest.raises(RuntimeError):profile.validate_identity('/outside/public',actor.INSTALLED/'cpython/bin/python3.12')
def test_profile_wrong_namespace_and_symlink_refuses(monkeypatch,tmp_path):
    with pytest.raises(RuntimeError):verify.consume_profile(tmp_path)
    monkeypatch.setattr(verify,'ROOT',tmp_path);outside=tmp_path/'public-profile';outside.write_text(json.dumps(sample()));outside.chmod(0o600)
    (tmp_path/'ARM_RUNTIME_PROFILE.json').symlink_to(outside)
    with pytest.raises(Exception):verify.consume_profile(tmp_path)
def test_original_record_replacement_during_consume_not_adopted(monkeypatch,tmp_path):
    monkeypatch.setattr(verify,'ROOT',tmp_path);p=tmp_path/'ARM_RUNTIME_PROFILE.json';p.write_text(json.dumps(sample()));p.chmod(0o600)
    first=verify.prior.record
    def after_record(path,maximum):
        row=first(path,maximum);replacement=tmp_path/'public-replacement';replacement.write_text(json.dumps(sample()));replacement.chmod(0o600);replacement.replace(path);return row
    monkeypatch.setattr(verify.prior,'record',after_record)
    with pytest.raises(Exception):verify.consume_profile(tmp_path)
def test_original_runtime_inputs_fulltree_readonly_and_one_recipe():
    r=actor.verify_inputs();assert r['R4_records']==14215 and r['all_old_trees_unchanged']
    text=actor.recipe();assert text.count('\n\t')==2 and str(actor.INSTALLED/'cpython/bin/python3.12') in text
    assert ' -I -B ' in text and actor.prior.CAPS['runtime']==180 and actor.prior.UNIT_CAP==1800 and actor.prior.RESERVE==300
def test_replaced_original_root_rejected_before_snapshot_adoption(monkeypatch,tmp_path):
    tmp_path.chmod(0o700);monkeypatch.setattr(actor,'INSTALLED',tmp_path)
    with pytest.raises(Exception,match='fixed original R4 root identity'):actor.verify_inputs()
