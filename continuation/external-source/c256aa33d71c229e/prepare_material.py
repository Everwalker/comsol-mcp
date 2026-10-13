import json,hashlib,os,ast,difflib
from pathlib import Path
from strict_derived import read_regular,DECLARED,ACQUIRED,OPENSSL_SHA,MANIFEST_SHA
R=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');E=R/'repository/docs/full_project_continuation_v2/evidence/intel-builder-execution-r1';D=Path(__file__).parent
P=Path('/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T/comsol-intel-inputs-r1-obneuyy4')
manifest=R/'repository/docs/full_project_continuation_v2/evidence/w25-native-delivery-preparation-r2/INTEL_X86_ARCHIVE_ACQUISITION_MANIFEST_R1.json'
receipt=P/'evidence/resume-attempt-r3-a4753ec26eae4f9e82053e19ae30b97f/openssl-source-4.0.2.try-1/openssl-source-4.0.2.result.json'
api=R/'repository/docs/full_project_continuation_v2/evidence/release_data_lifecycle/desktop-builder-candidate-r4-w25/openssl-4.0.2-release-api.json'
inputs=[manifest,receipt,api,P/'archives/openssl-4.0.2.tar.gz',E/'ROOT_INTEL_CRYPTO_ABI_CHOICE_R1.json',E/'ROOT_INTEL_CRYPTO_ACTUAL_CHECK_R1.json']
records=[read_regular(p)[1] for p in inputs]
assert records[0]['sha256']==MANIFEST_SHA and records[3]['sha256']==OPENSSL_SHA
x=json.loads(read_regular(manifest)[0]);rows=next(v for v in x.values() if isinstance(v,list) and v and isinstance(v[0],dict) and 'artifact_id' in v[0]);row=next(q for q in rows if q['artifact_id']=='openssl-source-4.0.2');assert row['url']==ACQUIRED and row['github_asset_id']==529116792
acq=json.loads(read_regular(receipt)[0]);assert acq['actual_sha256']==OPENSSL_SHA and acq['actual_bytes']==55153883 and acq['rc']==0 and acq['http_status']==200
ap=json.loads(read_regular(api)[0]);asset=next(a for a in ap['assets'] if a['id']==529116792);assert asset['browser_download_url']==ACQUIRED and asset['digest']=='sha256:'+OPENSSL_SHA
binding={'origin_identity':{'declared_url':DECLARED,'acquired_official_url':ACQUIRED,'archive_sha256':OPENSSL_SHA,'archive_bytes':55153883,'github_asset_id':529116792,'manifest_sha256':MANIFEST_SHA,'acquisition_receipt_sha256':records[1]['sha256'],'official_asset_api_sha256':records[2]['sha256'],'authentication':'Original reviewed official GitHub asset digest plus exact HTTPS acquisition; no PGP4 evidence fabricated.'},'verified_inputs':records}
def write(n,x):
 raw=(json.dumps(x,indent=2,sort_keys=True)+'\n').encode()
 with (D/n).open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
 return hashlib.sha256(raw).hexdigest()
sha=write('ORIGIN_BINDING.json',binding)
s=D/'strict_derived.py';text=s.read_text();text=text.replace("binding=json.loads((Path(__file__).parent/'ORIGIN_BINDING.json').read_bytes())",f"binding_raw,binding_record=read_regular(Path(__file__).parent/'ORIGIN_BINDING.json')\n if binding_record['sha256']!='{sha}':raise Refusal('origin binding does not match source-frozen SHA')\n binding=json.loads(binding_raw)")
s.write_text(text)
result=json.loads(read_regular(E/'INTEL_CRYPTO_EXECUTION_RESULT_R1.json')[0]);art=result['unit_result']['artifact'];wheel=Path(art['wheel']);lock=R/'repository/uv.lock';lr,lid=read_regular(lock)
import tomllib
sdt=next(q['sdist'] for q in tomllib.loads(lr.decode())['package'] if q['name']=='cryptography' and q['version']=='50.0.1')
lic=P/'sources/openssl-source-4.0.2/openssl-4.0.2/LICENSE.txt';_,li=read_regular(lic)
provenance={'schema':'comsol-mcp-derived-wheel/2','status':'BUILD_CANDIDATE','target':'macos_x86_64','package':{'name':'cryptography','version':'50.0.1'},'wheel':{'filename':wheel.name,'sha256':art['sha256'],'bytes':art['bytes'],'tag':'cp39-abi3-macosx_11_0_x86_64'},'source':{'url':sdt['url'],'sha256':sdt['hash'][7:]},'source_lock':{'filename':lock.name,'sha256':lid['sha256'],'sdist_sha256':sdt['hash'][7:]},'source_origin_binding':binding['origin_identity'],'native_component':{'name':'OpenSSL','version':'4.0.2','license':'Apache-2.0','linkage':'static','source_url':ACQUIRED,'source_sha256':OPENSSL_SHA,'license_file':{'filename':lic.name,'sha256':li['sha256'],'bytes':li['bytes']}},'native_audit':{'architecture':'x86_64','minimum_os':'11.0','openssl_dynamic_dependency':False,'temporary_dylib_dependencies':[],'dynamic_libraries':art['extension']['linked_dylibs'],'extension_sha256':art['extension']['sha256']},'uv_lock_modified':False,'upstream_source_patched':False}
write('ACTUAL_CRYPTO_PROVENANCE_R1.json',provenance)
(D/'HELPER_DIFF.patch').write_text(''.join(difflib.unified_diff((D/'full_project_release_original.py').read_text().splitlines(True),(D/'full_project_release.py').read_text().splitlines(True),fromfile='original frozen helper',tofile='private successor helper')))
for p in D.glob('*.py'):ast.parse(p.read_bytes(),filename=str(p))
print('origin/provenance/diff prepared; AST PASS')
