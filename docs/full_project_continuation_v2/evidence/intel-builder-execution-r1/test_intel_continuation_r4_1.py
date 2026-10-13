"""Production continuation full-tree gates and owned observer interface fault."""
import os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import intel_deps_stages as d
import run_intel_deps_continuation_r4_1 as w
import cpython_universal2_stages as c
class ContinuationGates(unittest.TestCase):
 def test_actual_initial_checkpoint_all_four_trees(self):
  result=w.verify_initial_checkpoint(d.EXACT_PREFIX/'intel-deps-r2')
  self.assertEqual(result['stage'],'prepare');self.assertEqual(len(result['artifacts']['inputs']),7139)
 def test_only_openssl_mutation_allowed_after_successful_stage(self):
  with tempfile.TemporaryDirectory() as t:
   work=Path(t);inputs=work/'inputs';inputs.mkdir();openssl=inputs/'openssl-source-4.0.2';openssl.mkdir();(openssl/'original').write_bytes(b'original');crypto=inputs/'crypto';crypto.mkdir();(crypto/'original').write_bytes(b'original');prior={'artifacts':{'inputs':d.tree_record(inputs)}}
   (openssl/'generated').write_bytes(b'legitimate build artifact')
   with self.assertRaises(d.Refusal):w.verify_unchanged_inputs(work,prior)
   w.verify_unchanged_inputs(work,prior,after_openssl=True)
   (crypto/'original').write_bytes(b'changed')
   with self.assertRaises(d.Refusal):w.verify_unchanged_inputs(work,prior,after_openssl=True)
 def test_observer_interface_error_keeps_same_actual_child(self):
  with tempfile.TemporaryDirectory() as t:
   work=Path(t).resolve();fd=os.open(work,os.O_RDONLY|os.O_DIRECTORY)
   try:
    with self.assertRaises(c.StageUnknown) as caught:c.run(fd,'continuation-fixture',[sys.executable,'-B','-c','import time;time.sleep(0.2)'],work,c.task_environment(work),timeout=0.01)
    error=caught.exception;child=error.process;actual=c.observe_unknown;calls=[]
    def observing(logfd,received):
     calls.append(received.process)
     if len(calls)==1:raise OSError('injected observer interface failure')
     return actual(logfd,received,interval=0.01)
    with patch.object(c,'observe_unknown',observing),patch.object(w.time,'sleep',return_value=None):result=w.retain_unknown_owner(fd,error)
    self.assertEqual(child.returncode,0);self.assertTrue(result['owned_group_absent']);self.assertTrue(all(handle is child for handle in calls));self.assertTrue(error.diagnostic_errors);self.assertEqual(error.diagnostic_errors.causes[-1].args,("injected observer interface failure",))
   finally:os.close(fd)
if __name__=='__main__':unittest.main(verbosity=2)
