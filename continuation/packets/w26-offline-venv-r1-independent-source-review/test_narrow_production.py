"""Independent source-only controls; no target process, socket, installer, wheel code."""
from pathlib import Path
import importlib.util,json,os,sys,subprocess
import pytest
C=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/local-preservation/w26-offline-venv-unit-r1');O=Path(__file__).parent;sys.path.insert(0,str(C))
import common,consumers
def load(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
def save(name,value):
 fd=os.open(O/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as out:out.write(json.dumps(value,indent=2)+'\n')

def test_actual_controller_main_missing_once_binding_no_probe_or_Popen(monkeypatch):
 W=Path((C/'W_PATH.txt').read_text().strip());ctrl=load('review_original_w26_controller',W/'controller.py');calls=[]
 def forbidden(*args,**kwargs):calls.append(repr(args));raise AssertionError('unexpected process/probe before missing once gate')
 monkeypatch.setattr(subprocess,'Popen',forbidden);monkeypatch.setattr(subprocess,'run',forbidden);monkeypatch.setattr(sys,'argv',['controller.py','--binding-sha','0'*64])
 with pytest.raises(FileNotFoundError) as err:ctrl.main()
 assert err.value.filename==str(ctrl.BINDING) and calls==[]
 assert not (W/'UNIT_ONCE_GUARD.json').exists() and not (W/'CONTROLLER_BIRTH.json').exists()
 save('ACTUAL_CONTROLLER_MAIN_MISSING_BINDING_REFUSAL.json',{'source_only':True,'actual_entry':str(W/'controller.py'),'exception_type':type(err.value).__name__,'missing_file':err.value.filename,'Popen_or_probe_calls':calls,'new_once_guard':False,'actual8commands':'NOT_RUN','targetPython_pip_sandbox_wheelcode_COMSOL':0})

def test_actual_consumer_main_first_stdout_record_then_changed_source_refusal(monkeypatch,capsys):
 module=load('review_w26_original_test_public_fixture',C/'test_unit.py');case=module.ProfileControls();case.setUp()
 try:
  context_file=case.root/'context.json';context_rec=common.publish(context_file,case.context);original=consumers.profile;originalCalls=[]
  def boundary(role,ctx):
   def importer(name):originalCalls.append(name);return case.mods[name]
   return original(role,ctx,importer,lambda path:case.dists[role],case.identity(role),case.provider)
  monkeypatch.setattr(consumers,'profile',boundary);monkeypatch.setattr(sys,'argv',['consumers.py','--role','builder','--context',str(context_file),'--context-sha',context_rec['sha256']])
  consumers.main();stdout=capsys.readouterr().out;actual=json.loads(stdout.splitlines()[-1]);first=actual['first_profile_record'];p=Path(first['path']);verified=consumers.verify_profile(p,first,'builder',case.context)
  module_record=next(x['first_record'] for x in json.loads(p.read_bytes())['module_first_records'] if x['module']=='packaging');Path(module_record['path']).write_bytes(b'# replaced after original producer first record\n')
  with pytest.raises(common.Refusal,match='fixed regular checkpoint changed') as err:consumers.verify_profile(p,first,'builder',case.context)
  save('ACTUAL_PRODUCER_MAIN_FIRST_STDOUT_AND_CONSUMER_DRIFT.json',{'source_only':True,'public_boundary_fixtures':True,'producer_main_original_algorithm':True,'stdout_framing':actual,'original_first_profile':verified,'original_importer_calls':originalCalls,'changed_exact_original_module_record':module_record,'actual_consumer_refusal':str(err.value),'profile_not_re_adopted':True,'native_targetruntime_installer_wheelcode':'NOT_RUN'})
 finally:case.tearDown()
