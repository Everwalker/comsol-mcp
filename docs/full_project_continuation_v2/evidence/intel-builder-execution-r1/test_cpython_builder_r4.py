"""Affected production paths only; tiny owned children, no native build."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import cpython_universal2_stages as b
class OwnershipFailures(unittest.TestCase):
 def exercise(self,fault):
  with tempfile.TemporaryDirectory(prefix='comsol-cpython-r4-') as tmp:
   path=Path(tmp).resolve();fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
   actual=b.subprocess.Popen;writer=b.write_json;sync=b.os.fsync;close=b.os.close;clock=b.time.time
   created=[];logs=[];failure=OSError('injected '+fault)
   def spawn(*args,**kw):
    child=actual(*args,**kw);created.append(child)
    if kw.get('start_new_session'): logs.extend([kw['stdout'],kw['stderr']])
    return child
   def write(directory,name,data):
    if name.endswith('.'+fault+'.json'): raise failure
    return writer(directory,name,data)
   def fsync(target):
    if fault=='fsync' and target in logs: raise failure
    return sync(target)
   def closing(target):
    close(target)
    if fault=='close' and target in logs: raise failure
   def timestamp():
    if fault=="finished_time" and created and created[0].returncode is not None: raise failure
    return clock()
   def birth(child,argv):
    if fault=='birth_reader': raise failure
    return b.actual_birth(child,argv)
   try:
    with patch.object(b.subprocess,'Popen',spawn),patch.object(b,'write_json',write),patch.object(b.os,'fsync',fsync),patch.object(b.os,'close',closing),patch.object(b.time,'time',timestamp):
     with self.assertRaises(b.StageUnknown) as caught:
      b.run(fd,'fault',[sys.executable,'-B','-c','import time;time.sleep(0.15)'],path,b.task_environment(path),birth_reader=birth)
    error=caught.exception
    self.assertIs(error.process,created[0]);self.assertIs(error.original_cause,failure);self.assertIs(error.__cause__,failure)
    result=b.observe_unknown(fd,error,interval=0.03)
    self.assertEqual(result['rc'],0);self.assertEqual(result['stage_acceptance'],'NOT_RUN')
    for target in logs:
     with self.assertRaises(OSError): os.fstat(target)
   finally:
    for child in created: child.wait(timeout=3)
    os.close(fd)
 def test_postspawn_json_writes(self):
  for fault in ('spawn','group','birth','result'):
   with self.subTest(fault=fault): self.exercise(fault)
 def test_finished_timestamp_exception(self): self.exercise('finished_time')
 def test_birth_exception(self): self.exercise('birth_reader')
 def test_log_fsync(self): self.exercise('fsync')
 def test_log_close(self): self.exercise('close')
 def test_observer_diagnostics_failure(self):
  with tempfile.TemporaryDirectory(prefix='comsol-cpython-r4-observer-') as tmp:
   path=Path(tmp).resolve();fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
   try:
    with self.assertRaises(b.StageUnknown) as caught:
     b.run(fd,'observer',[sys.executable,'-B','-c','import time;time.sleep(0.18)'],path,b.task_environment(path),timeout=0.01)
    error=caught.exception;child=error.process
    with patch.object(b.os,'fsync',side_effect=OSError('injected observation fsync')),patch('builtins.print',side_effect=OSError('injected stdout')),patch.object(b.os,'write',side_effect=OSError('injected fallback')):
     result=b.observe_unknown(fd,error,interval=0.02)
    self.assertIs(error.process,child);self.assertEqual(child.returncode,0);self.assertTrue(result['owned_group_absent']);self.assertTrue(error.diagnostic_errors)
   finally: os.close(fd)
 def test_headers_only_clang(self):
  commands=b.stage_commands('headers',Path('/src'),Path('/build'),Path('/install'))
  self.assertEqual(len(commands),2);self.assertTrue(all(c[0]==b.CLANG for c in commands))
  source=Path(b.__file__).read_text();self.assertNotIn('/usr/bin/lipo',source);self.assertNotIn('/usr/bin/otool',source)
if __name__=='__main__': unittest.main(verbosity=2)
