"""Legitimate make verification step: all actual installed bytes and fat modules."""
import argparse,json
from pathlib import Path
from intel_deps_prepare import exclusive_json
from intel_deps_stages import tree_record
from tls_successor_r2 import actual_binaries,layout
p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args()
x=layout(a.root.parent)
if x['root']!=a.root:raise RuntimeError('fixed TLS namespace differs')
profile=json.loads((a.root/'ARM_RUNTIME_PROFILE.json').read_bytes())
if profile['actual_machine']!='arm64' or profile['provider_info'][:3]!=[4,0,2]:raise RuntimeError('actual selected runtime profile differs')
exclusive_json(a.root/'ACTUAL_BINARIES_AND_INSTALL.json',{'binary':actual_binaries(x),'installed_tree':tree_record(x['install']),'actual_arm_profile':profile,'x86_runtime':'NOT_RUN'})
