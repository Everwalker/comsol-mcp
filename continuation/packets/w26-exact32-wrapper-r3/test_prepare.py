"""Production prelaunch controls; no Popen, network, native inventory or guards."""
import importlib.util,sys,unittest,subprocess,os,signal,time
from pathlib import Path
D=Path(__file__).parent;W=Path((D/'W_PATH.txt').read_text().strip())
def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
wrapper=load('wrapper_prepared',W/'wrapper.py');actor=load('actor_prepared',W/'actor.py')
class Tests(unittest.TestCase):
 def test_actual_prepared_source_gate(self):
  m,cp,deps=wrapper.load_sources();self.assertEqual(len(m['network_URLs']),32);self.assertEqual(m['work_directory'],str(W));self.assertFalse((W/'WRAPPER_ONCE_GUARD.json').exists())
 def test_missing_binding_actual_main_no_spawn(self):
  old=sys.argv;oldPopen=subprocess.Popen;calls=[]
  def forbid(*a,**k):calls.append(a);raise AssertionError('Popen reached without actual Root binding')
  subprocess.Popen=forbid;sys.argv=['wrapper.py','--binding-sha','0'*64]
  try:
   with self.assertRaises(FileNotFoundError):wrapper.main()
   self.assertEqual(calls,[]);self.assertFalse((W/'WRAPPER_ONCE_GUARD.json').exists())
  finally:subprocess.Popen=oldPopen;sys.argv=old
 def test_actor_inherited_expired_budget_stops_before_source(self):
  old=sys.argv;oldenv=os.environ.get('COMSOL_W26_BATCH_START_MONOTONIC');oldfn=actor.source_and_rows;calls=[]
  actor.source_and_rows=lambda: calls.append(1);sys.argv=['actor.py','--execute-download','--approved-manifest-sha256',actor.MANIFEST_SHA];os.environ['COMSOL_W26_BATCH_START_MONOTONIC']=str(time.monotonic()-600)
  try:
   with self.assertRaisesRegex(RuntimeError,'exhausted inherited'):actor.main()
   self.assertEqual(calls,[]);self.assertFalse((W/'EXACT32_ONCE_GUARD.json').exists())
  finally:
   actor.source_and_rows=oldfn;sys.argv=old
   if oldenv is None:os.environ.pop('COMSOL_W26_BATCH_START_MONOTONIC',None)
   else:os.environ['COMSOL_W26_BATCH_START_MONOTONIC']=oldenv
 def test_actor_approved_sha_without_wrapper_binding_rejected(self):
  old=sys.argv;oldenv=os.environ.get('COMSOL_W26_BATCH_START_MONOTONIC');handler=signal.getsignal(signal.SIGALRM);sys.argv=['actor.py','--execute-download','--approved-manifest-sha256',actor.MANIFEST_SHA];os.environ['COMSOL_W26_BATCH_START_MONOTONIC']=str(time.monotonic())
  try:
   with self.assertRaises(FileNotFoundError):actor.main()
   self.assertFalse((W/'EXACT32_ONCE_GUARD.json').exists())
  finally:
   signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,handler);sys.argv=old
   if oldenv is None:os.environ.pop('COMSOL_W26_BATCH_START_MONOTONIC',None)
   else:os.environ['COMSOL_W26_BATCH_START_MONOTONIC']=oldenv
 def test_actor_metadata_no_fetch(self):
  old=sys.argv;sys.argv=['actor.py']
  try:actor.main();self.assertFalse((W/'EXACT32_ONCE_GUARD.json').exists())
  finally:sys.argv=old
if __name__=='__main__':unittest.main(verbosity=2)
