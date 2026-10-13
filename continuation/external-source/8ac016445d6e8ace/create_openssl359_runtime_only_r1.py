from pathlib import Path
E=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository/docs/full_project_continuation_v2/evidence/intel-builder-execution-r1')
old=(E/'openssl359_runtime_profile_r1.py').read_text();lines=old.splitlines(True)
imports=''.join(lines[:3])
body=''.join(lines[3:]);body=body.replace("expected_prefix=Path(sys.argv[1]).parent/'cpython'","expected_prefix=INSTALLED/'cpython'").replace("if ssl.OPENSSL_VERSION_INFO[:3]!=(3,5,9):raise RuntimeError('actual SSL provider is not fixed OpenSSL3.5.9')","validate_provider(ssl.OPENSSL_VERSION_INFO,ssl.OPENSSL_VERSION_NUMBER,ssl.OPENSSL_VERSION)")
body=body.replace("Path(sys.argv[1]).parent/'cpython-input", "INSTALLED/'cpython-input").replace("runtime_cert_cases(ssl,Path(sys.argv[1]).parent)","runtime_cert_cases(ssl,INSTALLED)").replace("Path(sys.argv[1]).parent/'runtime-certfixtures", "INSTALLED/'runtime-certfixtures").replace("'provider_info':ssl.OPENSSL_VERSION_INFO,","'provider_info':ssl.OPENSSL_VERSION_INFO,'provider_number':ssl.OPENSSL_VERSION_NUMBER,")
body=body.replace("fd=os.open(sys.argv[1]", "fd=os.open(output").replace("expected_prefix=INSTALLED/'cpython'", "expected_prefix=INSTALLED/'cpython'\nvalidate_identity(sys.prefix,sys.executable)")
header="""
INSTALLED=Path('/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T/comsol-intel-inputs-r1-obneuyy4/intel-tls-openssl359-cpython-r4')
OUTPUT_ROOT=INSTALLED.parent/'intel-tls-openssl359-runtime-r1'
def validate_provider(info,number,text):
 if tuple(info)!=(3,5,0,9,0) or number!=0x30500090 or text!='OpenSSL 3.5.9 29 Sep 2026':raise RuntimeError('exact actual OpenSSL3.5.9 full tuple/number/string differs')
def validate_identity(prefix,executable):
 if Path(prefix)!=INSTALLED/'cpython' or Path(executable)!=INSTALLED/'cpython/bin/python3.12':raise RuntimeError('fixed installed R4 interpreter differs')
def main(output):
 if Path(output)!=OUTPUT_ROOT/'ARM_RUNTIME_PROFILE.json':raise RuntimeError('fixed fresh runtime output differs')
"""
text=imports+header+''.join(' '+line for line in body.splitlines(True))+"\nif __name__=='__main__':main(sys.argv[1])\n"
with (E/'openssl359_runtime_profile_r2.py').open('x') as f:f.write(text)
