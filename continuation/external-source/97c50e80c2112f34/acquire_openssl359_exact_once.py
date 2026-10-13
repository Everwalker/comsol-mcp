import os,sys,json,hashlib
from pathlib import Path
D=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository/docs/full_project_continuation_v2/evidence/intel-builder-execution-r1');sys.path.insert(0,str(D))
from acquire_exact_archives import fetch,write_json,Refusal
from security_snapshot._host_scoped_provider import verify_private_path_security
root=Path(__file__).parent;verify_private_path_security(root,directory=True)
checks=[(root/'official-source.html','6e7cc333f10217752a20639963da6eb34d370d5222b00831feaab9730f40e742'),(root/'metadata-r2/openssl-3.5.9.tar.gz.sha256','f482705825eb43b47ce08d5cf4287ae062cdbada1a142d728a8d89c387300bf0')]
for path,sha in checks:
 if hashlib.sha256(path.read_bytes()).hexdigest()!=sha:raise Refusal('fixed official source metadata changed')
expected={'artifact_id':'openssl-source-3.5.9','filename':'openssl-3.5.9.tar.gz','url':'https://github.com/openssl/openssl/releases/download/openssl-3.5.9/openssl-3.5.9.tar.gz','origin_host':'github.com','expected_bytes':53279637,'expected_sha256':'603f5602e2eef00d77fbd429d34dcd5822bb301757a1bc9cdb24c670f1eb859a'}
manifest=json.loads((root/'EXACT_SOURCE_INPUT_MANIFEST.json').read_bytes())
if manifest['artifact']!=expected:raise Refusal('fixed 3.5.9 exact manifest differs')
if json.loads((root/'SOURCE_HEAD_RESULT.json').read_bytes())['bytes']!=expected['expected_bytes']:raise Refusal('fixed official HEAD differs')
rootfd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
write_json(rootfd,'EXACT_DOWNLOAD_ONCE_GUARD.json',{'actual_pid':os.getpid(),'argv':sys.argv,'manifest_sha':hashlib.sha256((root/'EXACT_SOURCE_INPUT_MANIFEST.json').read_bytes()).hexdigest(),'native_build':0})
for name in ('archives','archive-evidence'):os.mkdir(name,0o700,dir_fd=rootfd)
af=os.open('archives',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=rootfd);ef=os.open('archive-evidence',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=rootfd)
try:
 fetch(expected,af,ef)
 print('ACQUIRED_EXACT_3_5_9_SOURCE_ONLY',flush=True)
finally:
 for fd in (ef,af,rootfd):os.close(fd)
