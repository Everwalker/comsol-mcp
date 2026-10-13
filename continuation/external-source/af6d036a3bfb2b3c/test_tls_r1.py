import os,struct,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import tls_binary_r1 as binary
import tls_successor_r1 as unit
import tls_merge_r1 as merge
from acquire_exact_archives import Refusal

def thin(arch,kind=1,minimum=0xb0000):
 command=struct.pack('<IIIIII',0x32,24,1,minimum,0,0)
 return struct.pack('<IiiIIIII',0xfeedfacf,binary.CPUS[arch],0,kind,1,len(command),0,0)+command
def archive(raw):
 name=b'one.o/';h=name.ljust(16)+b'0'.ljust(12)+b'0'.ljust(6)+b'0'.ljust(6)+b'100600'.ljust(8)+str(len(raw)).encode().ljust(10)+b'`\n'
 return b'!<arch>\n'+h+raw+(b'\n' if len(raw)%2 else b'')
def fat(a,b):
 return struct.pack('>II',0xcafebabe,2)+struct.pack('>iiIII',binary.CPUS['arm64'],0,48,len(a),0)+struct.pack('>iiIII',binary.CPUS['x86_64'],0,48+len(a),len(b),0)+a+b

class TLSControls(unittest.TestCase):
 def test_actual_plan_exact_private_flags_and_ten_outer_commands(self):
  x=unit.layout(Path('/private/tmp/fixed-test'));plan,env=unit.command_plan(x)
  self.assertEqual(len(plan),10);self.assertIn('darwin64-arm64-cc',plan[0][1]);self.assertIn('no-shared',plan[0][1]);self.assertEqual(plan[1][1],['/usr/bin/make','-j2','build_libs'])
  cfg=next(argv for label,argv,cwd in plan if label=='cp-configure');self.assertIn('--with-openssl='+str(x['fat']),cfg);self.assertIn('--with-openssl-rpath=no',cfg);self.assertNotIn('PY_UNSUPPORTED_OPENSSL_BUILD',env);self.assertEqual(env.get('HOME'),os.environ.get('HOME'))
 def test_budget_no_dispatch_after_cap_and_stage_cap(self):
  now=[100.];b=unit.Budget(lambda:now[0]);self.assertEqual(b.remaining('arm-build'),600)
  now[0]+=1501
  with self.assertRaises(Refusal):b.remaining('arm-build')
  now[0]=701
  with self.assertRaises(Refusal):b.completed(100,'arm-configure')
 def test_generated_headers_equal_actual_bytes(self):
  with tempfile.TemporaryDirectory() as t:
   a,b=Path(t)/'a',Path(t)/'b';a.mkdir();b.mkdir();(a/'h').write_bytes(b'exact');(b/'h').write_bytes(b'exact')
   self.assertTrue(unit.compare_headers(a,b)['exact_bytes_equal']);(b/'h').write_bytes(b'other')
   with self.assertRaises(Refusal):unit.compare_headers(a,b)
 def test_header_symlink_rejected(self):
  with tempfile.TemporaryDirectory() as t:
   a,b=Path(t)/'a',Path(t)/'b';a.mkdir();b.mkdir();(a/'h').write_bytes(b'x');(b/'h').symlink_to(a/'h')
   with self.assertRaises(Refusal):unit.compare_headers(a,b)
 def test_fat_archive_actual_slices_and_object_minimum(self):
  inputs={arch:archive(thin(arch)) for arch in ('arm64','x86_64')};result=binary.fat_archive(fat(*inputs.values()),inputs)
  self.assertEqual([r['object_count'] for r in result['slices'].values()],[1,1])
  with self.assertRaises(Refusal):binary.fat_archive(fat(*inputs.values()),dict(inputs,arm64=inputs['arm64']+b'x'))
  with self.assertRaises(Refusal):binary.static_archive(archive(thin('arm64',minimum=0xa0f00)),'arm64')
 def test_overlap_and_wrong_cpu_rejected(self):
  a,b=thin('arm64',2),thin('x86_64',2);raw=bytearray(fat(a,b));struct.pack_into('>I',raw,36,48)
  with self.assertRaises(Refusal):binary.fat_binary(bytes(raw),(2,))
  with self.assertRaises(Refusal):binary.thin(a,'x86_64',(2,))
 def test_actual_child_wait_even_spawn_receipt_failure(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);calls=[]
   def writer(path,payload):
    calls.append((path.name,payload))
    if path.name.endswith('.spawn.json'):raise OSError('synthetic durable write fault')
   with self.assertRaises(Refusal):merge.child([sys.executable,'-B','-c','print("owned synthetic child")'],root,root,'child',time.monotonic()+10,writer)
   result=next(payload for name,payload in calls if name.endswith('.result.json'));self.assertEqual(result['actual_wait_rc'],0);self.assertTrue(result['terminal_wait_observed']);self.assertTrue(result['actual_pid']);self.assertIn('owned synthetic child',(root/'child.stdout').read_text())
 def test_actual_child_terminal_failure_no_retry(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)
   with self.assertRaises(Refusal):merge.child([sys.executable,'-B','-c','raise SystemExit(7)'],root,root,'child',time.monotonic()+10)
   import json
   self.assertEqual(json.loads((root/'child.result.json').read_text())['actual_wait_rc'],7)
 def test_child_expired_budget_does_not_spawn(self):
  with tempfile.TemporaryDirectory() as t,patch.object(merge.subprocess,'Popen') as spawn:
   with self.assertRaises(Refusal):merge.child(['unexecuted'],Path(t),Path(t),'expired',time.monotonic()-1)
   spawn.assert_not_called();self.assertFalse(list(Path(t).iterdir()))
 def test_actual_same_handle_wait_fault_is_retained(self):
  original=merge.subprocess.Popen;processes=[]
  def spawn(*args,**kwargs):
   process=original(*args,**kwargs);processes.append(process);wait=process.wait;calls=[0]
   def injected(*args,**kwargs):
    calls[0]+=1
    if calls[0]==1:raise OSError('synthetic observation failure')
    return wait(*args,**kwargs)
   process.wait=injected;return process
  with tempfile.TemporaryDirectory() as t,patch.object(merge.subprocess,'Popen',side_effect=spawn):
   root=Path(t)
   with self.assertRaises(Refusal):merge.child([sys.executable,'-B','-c','print("same handle")'],root,root,'child',time.monotonic()+10)
   self.assertEqual(len(processes),1);self.assertEqual(processes[0].returncode,0)
   import json
   self.assertEqual(json.loads((root/'child.result.json').read_text())['actual_pid'],processes[0].pid)
 def test_actual_fsync_fault_after_wait_does_not_lose_owner(self):
  fsync=merge.os.fsync;calls=[0]
  def injected(fd):
   calls[0]+=1
   if calls[0]==1:raise OSError('synthetic fsync failure')
   return fsync(fd)
  with tempfile.TemporaryDirectory() as t,patch.object(merge.os,'fsync',side_effect=injected):
   root=Path(t)
   # request durable writer uses fsync; exclude it from this injection.
   def writer(path,payload):
    import json
    path.write_text(json.dumps(payload))
   with self.assertRaises(Refusal):merge.child([sys.executable,'-B','-c','print("wait before release")'],root,root,'child',time.monotonic()+10,writer)
   import json
   self.assertEqual(json.loads((root/'child.result.json').read_text())['actual_wait_rc'],0)

if __name__=='__main__':unittest.main(verbosity=2)
