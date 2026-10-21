"""Current source-outside exact original producer/consumer; never executes old CLI."""
import argparse,ast,hashlib,importlib,importlib.metadata as md,json,os,ssl,sys,sysconfig,time
from pathlib import Path

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--role',choices=['builder','runtime'],required=True)
    parser.add_argument('--context',required=True);parser.add_argument('--sha',required=True);args=parser.parse_args()
    stage_path=Path(__file__).with_name('w26_dependency_stage.py');namespace={'__name__':'w26_stage_definitions','__file__':str(stage_path)}
    exec(compile(stage_path.read_bytes(),str(stage_path),'exec'),namespace)
    context,helper=namespace['read_context'](args.context,args.sha)
    record=context['legacy_sources']['consumers'];raw,_=helper['raw_file'](Path(record['path']),expected=record)
    tree=ast.parse(raw);wanted={'provider_observed','validate_provider','profile','verify_profile'}
    definitions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in wanted]
    if {n.name for n in definitions}!=wanted:raise RuntimeError('original pure producer definitions are not unique')
    original={'importlib':importlib,'md':md,'json':json,'os':os,'sys':sys,'sysconfig':sysconfig,'time':time,'Path':Path,
              'raw_file':helper['raw_file'],'publish':helper['publish'],'Refusal':helper['Refusal'],'INFO':(3,5,0,9,0),'NUMBER':0x30500090}
    exec(compile(ast.Module(body=definitions,type_ignores=[]),record['path'],'exec'),original)
    original_validate=original['validate_provider']
    def validate_full_provider(row):
        original_validate(row)
        if row['TEXT']!='OpenSSL 3.5.9 29 Sep 2026':raise RuntimeError('exact full SSL359 provider text differs')
    original['validate_provider']=validate_full_provider
    # Original first-provider capture happens before its identity/provider gates.
    value,first=original['profile'](args.role,context)
    original['verify_profile'](Path(first['path']),first,args.role,context)
    print(json.dumps({'status':value['status'],'first_profile_record':first}),flush=True)

if __name__=='__main__':main()
