"""Legitimate make verification step: all actual installed bytes and fat modules."""
import argparse,json
from pathlib import Path
from intel_deps_prepare import exclusive_json
from intel_deps_stages import tree_record
from openssl359_cpython_successor_r4 import actual_binaries,layout
p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args()
x=layout()
if x['root']!=a.root:raise RuntimeError('fixed TLS namespace differs')
profile=json.loads((a.root/'ARM_RUNTIME_PROFILE.json').read_bytes())
if profile['actual_machine']!='arm64' or profile['provider_info'][:3]!=[3,5,9]:raise RuntimeError('actual selected runtime profile differs')
if profile.get('software_variant')!='CPython3.12.13-official+OpenSSL3.5.9-official' or not profile.get('actual_certificate_cases',{}).get('malformed_negative'):raise RuntimeError('actual official runtime/cert cases not accepted')
if not profile.get('official_unmodified_CPython') or len(profile.get('actual_MemoryBIO',{}).get('actual_cases',[]))!=2:raise RuntimeError('actual official CP/TLS1.2/TLS1.3 evidence absent')
exclusive_json(a.root/'ACTUAL_BINARIES_AND_INSTALL.json',{'binary':actual_binaries(x),'installed_tree':tree_record(x['install']),'actual_arm_profile':profile,'x86_runtime':'NOT_RUN'})
