"""Actual dependency profile producer/consumer. Source controls inject only public boundaries."""
import argparse,hashlib,importlib,importlib.metadata as md,json,os,sys,sysconfig,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent));from common import raw_file,publish,Refusal
INFO=(3,5,0,9,0);NUMBER=0x30500090

def provider_observed(provider):
 return {'INFO':list(provider.OPENSSL_VERSION_INFO),'NUMBER':provider.OPENSSL_VERSION_NUMBER,'TEXT':provider.OPENSSL_VERSION}
def validate_provider(row):
 if tuple(row['INFO'])!=INFO or row['NUMBER']!=NUMBER or not row['TEXT'].startswith('OpenSSL 3.5.9 '):raise Refusal('actual CP SSL provider mismatch: '+repr(row))

def profile(role,context,importer=importlib.import_module,dist_reader=md.distributions,identity=None,provider=None):
 import ssl
 ident=identity or {'prefix':sys.prefix,'base_prefix':sys.base_prefix,'executable':sys.executable,'cwd':os.getcwd(),'purelib':sysconfig.get_path('purelib'),'python_version':sys.version.split()[0],'platform':sys.platform}
 observed=provider_observed(provider or ssl);root=Path(context['unit_root']);prefix=root/role;output=root/'evidence'/('PROFILE_'+role+'.json')
 # Preserve all first actual provider values even if the provider or identity gate rejects.
 first_provider=publish(root/'evidence'/('PROVIDER_'+role+'.json'),observed)
 validate_provider(observed)
 if ident['prefix']!=str(prefix) or ident['base_prefix']!=context['base_prefix'] or ident['executable']!=str(prefix/'bin/python') or ident['cwd']!=str(root/'outside') or ident['python_version']!='3.12.13' or ident['platform']!='darwin':raise Refusal('actual installed consumer identity/cwd differs')
 site=Path(ident['purelib'])
 if not site.is_relative_to(prefix) or not site.resolve().is_relative_to(prefix.resolve()):raise Refusal('site not inside fixed venv prefix')
 req=context['roles'][role];expected={r['name']:r for r in req['distributions']};expected['pip']=context['pip']
 distributions={};meta_records=[]
 # Fixed direct site only: nested setuptools vendor metadata is payload, not new installed inputs.
 for dist in dist_reader(path=[str(site)]):
  name=dist.metadata['Name'].lower().replace('_','-');version=dist.version
  if name in distributions or name not in expected or version!=expected[name]['version']:raise Refusal('actual installed distribution version/set differs: '+name)
  entry=expected[name]
  for leaf,sha in [('METADATA',entry['METADATA_sha256']),('WHEEL',entry['WHEEL_sha256'])]:
   files=[f for f in dist.files if str(f).count('/')==1 and str(f).endswith('.dist-info/'+leaf)]
   if len(files)!=1:raise Refusal('installed direct metadata leaf not unique')
   path=Path(dist.locate_file(files[0]))
   if not path.is_relative_to(site) or not path.resolve().is_relative_to(site.resolve()):raise Refusal('installed metadata path outside site')
   _,record=raw_file(path)
   if record['sha256']!=sha:raise Refusal('installed metadata bytes differ from accepted wheel')
   meta_records.append(record)
  distributions[name]=version
 if set(distributions)!=set(expected):raise Refusal('installed required distributions missing')
 modules=[];jvm=[]
 if role=='runtime':
  j=importer('jpype');jvm.append(bool(j.isJVMStarted()))
  if jvm[-1]:raise Refusal('JVM unexpectedly started before manifest')
 for mod in req['modules']:
  loaded=importer(mod['module']);path=Path(loaded.__file__)
  if not path.is_relative_to(site) or not path.resolve().is_relative_to(site.resolve()) or path.relative_to(site).as_posix()!=mod['relative_path']:raise Refusal('installed module file outside fixed venv/wheel path')
  _,record=raw_file(path)
  if record['sha256']!=mod['sha256']:raise Refusal('actual module bytes differ from accepted wheel')
  modules.append({'module':mod['module'],'first_record':record})
 if role=='builder':
  for mod in ['setuptools','wheel','packaging']:
   if getattr(importer(mod),'__version__',None)!=expected[mod]['version']:raise Refusal('builder API version differs')
 else:
  if importer('mph').config.option()!=context['mph_public_defaults']:raise Refusal('MPh synthetic current-dir config does not match defaults')
  jvm.append(bool(importer('jpype').isJVMStarted()))
  if jvm[-1]:raise Refusal('JVM started during required imports')
 for rec in meta_records+[m['first_record'] for m in modules]:raw_file(Path(rec['path']),expected=rec)
 result={'schema':'comsol-w26-dependency-firstprofile/1','status':'PASS_SCOPED_ACTUAL_DEPENDENCY_IMPORTS_NOT_COMSOL_OR_FULL_STDLIB','role':role,'identity':ident,'provider':observed,'provider_first_record':first_provider,'distributions':distributions,'metadata_first_records':meta_records,'module_first_records':modules,'jpype_started_before_after':jvm,'fixture_record':raw_file(root/'outside/MPh.ini')[1],'forbidden_calls':{'COMSOL_start':0,'JVM_start':0,'GUI':0,'mcp_server_main':0},'created_unix':time.time()}
 rec=publish(output,result)
 return result,rec

def verify_profile(path,first_record,role,context):
 raw,rec=raw_file(path,expected=first_record);p=json.loads(raw)
 if p['role']!=role or p['status']!='PASS_SCOPED_ACTUAL_DEPENDENCY_IMPORTS_NOT_COMSOL_OR_FULL_STDLIB':raise Refusal('wrong actual first profile')
 prefix=str(Path(context['unit_root'])/role)
 if p['identity']['prefix']!=prefix or p['identity']['cwd']!=str(Path(context['unit_root'])/'outside') or p['identity']['executable']!=prefix+'/bin/python':raise Refusal('profile path/identity wrong')
 validate_provider(p['provider']);raw_file(Path(p['provider_first_record']['path']),expected=p['provider_first_record'])
 expected={r['name']:r['version'] for r in context['roles'][role]['distributions']};expected['pip']=context['pip']['version']
 if p['distributions']!=expected or (role=='runtime' and p['jpype_started_before_after']!=[False,False]):raise Refusal('firstprofile required runtime state mismatch')
 if [r['module'] for r in p['module_first_records']]!=[r['module'] for r in context['roles'][role]['modules']]:raise Refusal('actual module manifest differs')
 for r in p['metadata_first_records']+[m['first_record'] for m in p['module_first_records']]+[p['fixture_record']]:raw_file(Path(r['path']),expected=r)
 return rec

def main():
 a=argparse.ArgumentParser();a.add_argument('--role',choices=['builder','runtime'],required=True);a.add_argument('--context',type=Path,required=True);a.add_argument('--context-sha',required=True);p=a.parse_args();raw,rec=raw_file(p.context)
 if rec['sha256']!=p.context_sha:raise Refusal('fixed consumer context changed')
 context=json.loads(raw);value,record=profile(p.role,context);verify_profile(Path(record['path']),record,p.role,context);print(json.dumps({'status':value['status'],'first_profile_record':record}),flush=True)
if __name__=='__main__':main()
