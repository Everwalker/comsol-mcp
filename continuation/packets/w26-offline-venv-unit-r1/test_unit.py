"""Production source controls with synthetic public boundaries; no target Python/pip/socket/exec."""
import copy,errno,hashlib,importlib.util,json,os,sys,tempfile,types,unittest
from pathlib import Path
import common,consumers,stage_gate
D=Path(__file__).parent
class Dist:
 def __init__(self,name,version,site):
  self.metadata={'Name':name};self.version=version;self.site=site;self.files=[Path(name+'.dist-info/METADATA'),Path(name+'.dist-info/WHEEL')]
 def locate_file(self,f):return self.site/f
class ProfileControls(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='comsol-w26-profile-software-');self.root=Path(self.tmp.name);(self.root/'evidence').mkdir();(self.root/'outside').mkdir();(self.root/'outside/MPh.ini').write_text('[config]\n')
  self.context={'unit_root':str(self.root),'base_prefix':'/synthetic-public-base','pip':{'name':'pip','version':'25.0.1'},'roles':{},'mph_public_defaults':{'session':'client-server','caching':False,'classkit':False}}
  self.provider=types.SimpleNamespace(OPENSSL_VERSION_INFO=(3,5,0,9,0),OPENSSL_VERSION_NUMBER=0x30500090,OPENSSL_VERSION='OpenSSL 3.5.9 29 Sep 2026');self.boundary_calls=[]
  self.mods={};self.dists={}
  for role,names in [('builder',['setuptools','wheel','packaging']),('runtime',['jpype1','mph'])]:
   site=self.root/role/'lib/python3.12/site-packages';site.mkdir(parents=True);self.dists[role]=[];distrows=[];modules=[]
   for name in names+['pip']:
    ver={'setuptools':'80.9.0','wheel':'0.48.0','packaging':'26.3','jpype1':'1.7.1','mph':'1.4.0','pip':'25.0.1'}[name];dp=site/(name+'.dist-info');dp.mkdir();mr=b'synthetic public metadata\n';wr=b'synthetic public wheel header\n';(dp/'METADATA').write_bytes(mr);(dp/'WHEEL').write_bytes(wr);row={'name':name,'version':ver,'METADATA_sha256':hashlib.sha256(mr).hexdigest(),'WHEEL_sha256':hashlib.sha256(wr).hexdigest()}
    self.dists[role].append(Dist(name,ver,site))
    if name=='pip':self.context['pip']=row;continue
    distrows.append(row);modname='jpype' if name=='jpype1' else name;mp=site/(modname+'.py');data=b'# synthetic bound public module\n';mp.write_bytes(data);modules.append({'module':modname,'relative_path':mp.name,'sha256':hashlib.sha256(data).hexdigest(),'distribution':name});module=types.SimpleNamespace(__file__=str(mp),__version__=ver)
    if modname=='jpype':module.isJVMStarted=lambda:False
    if modname=='mph':module.config=types.SimpleNamespace(option=lambda:self.context['mph_public_defaults'])
    self.mods[modname]=module
   self.context['roles'][role]={'distributions':distrows,'modules':modules}
 def tearDown(self):self.tmp.cleanup()
 def identity(self,role):return {'prefix':str(self.root/role),'base_prefix':self.context['base_prefix'],'executable':str(self.root/role/'bin/python'),'cwd':str(self.root/'outside'),'purelib':str(self.root/role/'lib/python3.12/site-packages'),'python_version':'3.12.13','platform':'darwin'}
 def run_profile(self,role='builder',identity=None):
  def importer(n):self.boundary_calls.append(n);return self.mods[n]
  def reader(path):self.assertEqual(path,[self.identity(role)['purelib']]);return self.dists[role]
  return consumers.profile(role,self.context,importer,reader,identity or self.identity(role),self.provider)
 def test_producer_builder_main_flow_and_original_consumer(self):
  p,rec=self.run_profile();consumers.verify_profile(Path(rec['path']),rec,'builder',self.context);self.assertEqual(p['distributions']['packaging'],'26.3');self.assertIn('setuptools',self.boundary_calls)
 def test_producer_runtime_main_flow_all_public_boundaries(self):
  p,rec=self.run_profile('runtime');consumers.verify_profile(Path(rec['path']),rec,'runtime',self.context);self.assertEqual(p['jpype_started_before_after'],[False,False]);self.assertIn('mph',self.boundary_calls)
 def call_actual_main(self,role):
  context_file=self.root/'context.json';rec=common.publish(context_file,self.context);oldargs=sys.argv;original=consumers.profile
  def public_boundary(role,context):return original(role,context,lambda n:self.mods[n],lambda path:self.dists[role],self.identity(role),self.provider)
  consumers.profile=public_boundary;sys.argv=['consumers.py','--role',role,'--context',str(context_file),'--context-sha',rec['sha256']]
  try:consumers.main();self.assertTrue((self.root/'evidence'/('PROFILE_'+role+'.json')).exists())
  finally:consumers.profile=original;sys.argv=oldargs
 def test_actual_CLI_producer_main_builder(self):self.call_actual_main('builder')
 def test_actual_CLI_producer_main_runtime(self):self.call_actual_main('runtime')
 def test_wrong_metadata_version(self):self.dists['builder'][2].version='26.2';self.assertRaises(common.Refusal,self.run_profile)
 def test_wrong_builder_API_version(self):self.mods['packaging'].__version__='26.2';self.assertRaises(common.Refusal,self.run_profile)
 def test_outside_prefix(self):i=self.identity('builder');i['prefix']='/synthetic-foreign';self.assertRaises(common.Refusal,self.run_profile,'builder',i)
 def test_wrong_sourceoutside_cwd(self):i=self.identity('builder');i['cwd']=str(D);self.assertRaises(common.Refusal,self.run_profile,'builder',i)
 def test_wrong_module_whole_bytes(self):Path(self.mods['wheel'].__file__).write_bytes(b'changed');self.assertRaises(common.Refusal,self.run_profile)
 def test_JVM_started_refused(self):self.mods['jpype'].isJVMStarted=lambda:True;self.assertRaises(common.Refusal,self.run_profile,'runtime')
 def test_allfive_provider_info_wrong(self):
  for index in range(5):
   row={'INFO':[3,5,0,9,0],'NUMBER':0x30500090,'TEXT':'OpenSSL 3.5.9 29 Sep 2026'};row['INFO'][index]+=1
   with self.assertRaises(common.Refusal):consumers.validate_provider(row)
 def test_provider_number_wrong(self):self.provider.OPENSSL_VERSION_NUMBER+=1;self.assertRaises(common.Refusal,self.run_profile);self.assertTrue((self.root/'evidence/PROVIDER_builder.json').exists())
 def test_original_profile_json_changed_not_adopted(self):
  p,rec=self.run_profile();file=Path(rec['path']);obj=json.loads(file.read_text());obj['identity']['prefix']='/foreign';file.write_text(json.dumps(obj));self.assertRaises(common.Refusal,consumers.verify_profile,file,rec,'builder',self.context)
 def test_module_drift_after_original_profile_before_consumer(self):
  p,rec=self.run_profile();Path(self.mods['wheel'].__file__).write_bytes(b'changed');self.assertRaises(common.Refusal,consumers.verify_profile,Path(rec['path']),rec,'builder',self.context)
class GateControls(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory(prefix='comsol-w26-gate-software-');self.root=Path(self.tmp.name);(self.root/'evidence').mkdir();self.plan=self.root/'plan.json';self.rec=common.publish(self.plan,{'root':str(self.root),'commands':[{'label':'fixed','argv':['/synthetic-fixed-python','-I','-B','-m','pip','check']}]});self.calls=[]
 def tearDown(self):self.tmp.cleanup()
 def execspy(self,*args):self.calls.append(args)
 def deny(self,*args,**kwargs):raise PermissionError(errno.EPERM,'synthetic actual boundary errno')
 def test_actual_gate_EPERR_and_same_process_exec_branch(self):stage_gate.execute(self.plan,self.rec['sha256'],'fixed',9,self.execspy,self.deny);self.assertEqual(len(self.calls),1)
 def test_connection_refused_not_permission(self):
  def refused(*a,**k):raise OSError(errno.ECONNREFUSED,'operation not permitted misleading text')
  self.assertRaises(common.Refusal,stage_gate.execute,self.plan,self.rec['sha256'],'fixed',9,self.execspy,refused);self.assertEqual(self.calls,[])
 def test_plan_byte_drift(self):self.plan.write_bytes(self.plan.read_bytes()+b' ');self.assertRaises(common.Refusal,stage_gate.execute,self.plan,self.rec['sha256'],'fixed',9,self.execspy,self.deny);self.assertEqual(self.calls,[])
 def test_unlisted_stage(self):self.assertRaises(common.Refusal,stage_gate.execute,self.plan,self.rec['sha256'],'notfixed',9,self.execspy,self.deny);self.assertEqual(self.calls,[])
 def test_checked_source_copy_rejects_self_drift(self):p=self.root/'input';p.write_bytes(b'original');rec=common.raw_file(p)[1];p.write_bytes(b'modified');self.assertRaises(common.Refusal,common.exact_copy,p,self.root/'dest',rec);self.assertFalse((self.root/'dest').exists())
class ControllerControls(unittest.TestCase):
 def test_missing_Root_binding_actual_source_gate_no_Popen(self):
  import subprocess
  W=Path((D/'W_PATH.txt').read_text().strip());spec=importlib.util.spec_from_file_location('prepared_controller',W/'controller.py');ctrl=importlib.util.module_from_spec(spec);spec.loader.exec_module(ctrl)
  original=subprocess.Popen;calls=[]
  def denied(*a,**kw):calls.append(a);raise AssertionError('unexpected source preparation Popen')
  subprocess.Popen=denied
  try:
   with self.assertRaises(FileNotFoundError):ctrl.source_gate('0'*64)
   self.assertEqual(calls,[]);self.assertFalse((W/'UNIT_ONCE_GUARD.json').exists());self.assertFalse(Path(json.loads((W/'METADATA.json').read_text())['context']['unit_root']).exists())
  finally:subprocess.Popen=original
 def test_production_profile_taskonly_paths(self):
  W=Path((D/'W_PATH.txt').read_text().strip());spec=importlib.util.spec_from_file_location('controller_profile',W/'controller.py');ctrl=importlib.util.module_from_spec(spec);spec.loader.exec_module(ctrl)
  profile=ctrl.sandbox_profile(Path('/synthetic-owned-root'),Path('/synthetic-own-logs'))
  self.assertIn('(deny network*)',profile);self.assertIn('(deny file-write*)',profile);self.assertIn('(subpath "/synthetic-owned-root/builder")',profile);self.assertNotIn('(subpath "/Users',profile)
if __name__=='__main__':unittest.main(verbosity=2)
