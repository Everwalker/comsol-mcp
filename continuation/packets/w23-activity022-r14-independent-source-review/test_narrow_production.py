"""Independent SOURCE ONLY controls; actual algorithms, SDK-boundary fixtures."""
from pathlib import Path
import copy,importlib.util,json,os,re,shlex,sys
import pytest
from comsol_mcp import _g3_results as current
from comsol_mcp._execution_contract import ExecutionContractError
from tests.test_g3_w17 import FWiredTree,FSelection
from tests.test_activity022_boundary_selection_ticket import setup
from tests.test_ephemeral_result_ticket import _call
from tools import run_native_w23_saved_mph_diagnostic as runner
from tools import run_native_w23_te_science as campaign
C=Path(runner.REPO);R13=C.parent/'w23-saved-mph-science-readback-20261013-candidate20-r13';O=Path(__file__).parent
def load(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
def save(name,data):
 fd=os.open(O/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as f:f.write(json.dumps(data,indent=2,default=str)+'\n')

def test_actual_strict_measure_all_parent_success_current_refusal(monkeypatch):
 old=load('comsol_mcp._review_r13_results',R13/'comsol_mcp/_g3_results.py')
 monkeypatch.setattr(FSelection,'all',lambda self:setattr(self,'_entities',[1,2]))
 class MeasureSelection:
  def __init__(self):self.calls=[]
  def geom(self,dim):self.calls.append(['geom',dim])
  def all(self):self.calls.append(['all'])
  def entities(self):self.calls.append(['entities']);return [1,2]
 selection={'kind':'all','component':'comp1','geometry':'geom1','entity_dimension':3}
 args={'spec':{'expressions':['T'],'solution':{'dataset':'dset1','solution':'sol1'},'aggregate':'integral','complex_mode':'real','selection':selection,'storage':'inline'}}
 proofs={}
 for label,module in [('r13',old),('r14',current)]:
  tree=FWiredTree(numerical_real=42.0);measure=MeasureSelection()
  class M:
   def selection(self):return measure
  tree.component.measure=lambda:M()
  try:
   response=module.result_evaluate(tree.worker,'Model',copy.deepcopy(args),strict_metric_evidence=True)
   proofs[label]={'status':'RETURNED','strict_evidence':response['strict_metric_evidence'],'measure_calls':measure.calls,'remaining_numerical_tags':tree.numerical_list.tags()}
  except ExecutionContractError as exc:
   proofs[label]={'status':'REFUSED','code':exc.code,'message':str(exc),'measure_calls':measure.calls,'remaining_numerical_tags':tree.numerical_list.tags()}
 save('ACTUAL_STRICT_MEASURE_ALL_R13_R14.json',{'source_only':True,'SDK_fixture':True,'native':'NOT_RUN','arguments':args,'proofs':proofs})
 assert proofs['r13']['status']=='RETURNED' and proofs['r13']['strict_evidence']['status']=='VERIFIED'
 assert proofs['r14']['status']=='REFUSED' and proofs['r14']['code']=='INVALID_SELECTION' and 'named or explicit' in proofs['r14']['message']
 assert proofs['r13']['remaining_numerical_tags']==proofs['r14']['remaining_numerical_tags']==[]

def test_real_script_tokens_rejected_by_actual_four_arg_validator():
 d=C/'root-review-packet/final6';binding=json.loads((d/'FINAL_SOURCE_SOFTWARE_AND_FIXED_ENTRY_BINDING_ACTIVITY022_R14.json').read_bytes());freeze=json.loads(Path(binding['freeze']['path']).read_bytes());meta=json.loads(Path(binding['metadata']['path']).read_bytes());blue=json.loads(Path(meta['approval_blueprint']['path']).read_bytes())
 source=Path(binding['script']['path']).read_text();exec_line=next(x for x in source.splitlines() if x.startswith('exec '));tokens=shlex.split(exec_line);scriptargv=tokens[1:tokens.index('>')];expected=meta['exact_native_invocation']['argv'];differences=[{'index':i,'script':a,'metadata':b} for i,(a,b) in enumerate(zip(scriptargv,expected)) if a!=b]
 assert len(scriptargv)==len(expected)==25 and len(differences)==1 and differences[0]['index']==18
 runner._validate_approval(blue['expected_approval_fields'],freeze,binding['freeze']['sha256'],expected)
 with pytest.raises(RuntimeError) as e:runner._validate_approval(blue['expected_approval_fields'],freeze,binding['freeze']['sha256'],scriptargv)
 assert 'native_argv_sha256' in str(e.value) and 'exact_native_invocation.argv' in str(e.value)
 assert 'test -f '+scriptargv[18] in source and 'ACTIVITY021' in scriptargv[18] and 'ACTIVITY022' in expected[18]
 save('ACTUAL_SCRIPT_ARGV_APPROVAL_REFUSAL.json',{'source_only':True,'script_executed':False,'differences':differences,'metadata_reference_validator':'PASS_REFERENCE_ONLY','actual_script_validator':{'exception_type':type(e.value).__name__,'message':str(e.value)},'native_handoffs':0})

def test_full_science_original_java_output_contract_current_consumers_refuse():
 old=load('review_r13_science_protocol',R13/'tools/w23_te_science_protocol.py')
 java=(C/'tools/java/NativeW23TEScienceFixture.java').read_text();body=java.split('private static final String[] RAW_FIELDS = {',1)[1].split('};',1)[0];names=re.findall(r'"([^"]+)"',body);assert len(names)==22 and names[-4:]==['ewfd.neff_1','ewfd.neff_2','nx','ny']
 count=321;y=[-8e-6+16e-6*i/(count-1) for i in range(count)]
 raw={'sample_count':count,'normal_sign':1,'coordinates_m':[[8e-6]*count,y],'expressions':[{'expression':n,'real':[1.0 if n=='nx' else campaign.EXPECTED_NEFF_TE0 if 'neff' in n else 0.0]*count,'imag':[0.0]*count} for n in names]}
 prior=old._plane_coordinates(raw,expected_x_m=8e-6);assert prior[1:]==(1,1)
 with pytest.raises(runner.protocol.ScienceProtocolError,match='normal nx/ny') as normal:runner.protocol._plane_coordinates(raw,expected_x_m=8e-6)
 old_neff=campaign._validate_mode_index(raw,'ewfd.neff_2',2e-3);assert old_neff['status']=='PASS'
 with pytest.raises(ValueError,match='emw.neff_2') as neff:campaign._validate_mode_index(raw,'emw.neff_2',2e-3)
 assert 'interp.set("expr", RAW_FIELDS)' in java and '"expression", RAW_FIELDS[expressionIndex]' in java
 source=(C/'tools/run_native_w23_te_science.py').read_text();assert '"emw.neff_2", 2e-3' in source and '"phase": "raw_fields"' in source
 save('ACTUAL_FULL_CAMPAIGN_PRODUCER_CONSUMER_CONTRACT.json',{'source_only':True,'native':'NOT_RUN','synthetic_values_from_original_java_emission_contract':True,'Java_immutable_RAW_FIELDS':names,'actual_producer_source':{'sets_RAW_FIELDS':True,'returns_exact_RAW_FIELDS_labels':True},'actual_campaign_raw_fields_phase':True,'r13_protocol_same_raw_plane':'PASS','r14_protocol_same_raw_plane':{'exception_type':type(normal.value).__name__,'message':str(normal.value)},'r13_accepted_neff_argument':'ewfd.neff_2','r14_actual_neff_argument':'emw.neff_2','old_argument_result':old_neff,'current_argument_refusal':str(neff.value),'COMSOL_numerical_acceptance':'NOT_RUN'})

def test_original_public_selection_both_planes_consume_same_child(tmp_path,monkeypatch):
 proofs=[]
 for port in ['1','2']:
  out,g=setup(tmp_path/port,monkeypatch);daemon,service,ref,project,sdk,tree,nodes,args=out
  args['spec']['selection']=runner.plane_selection(port);g.n=args['spec']['selection']['tag'];g.ids=[1,3,5] if port=='1' else [8,10,12]
  try:
   response=_call(daemon,ref,project,args,'ind-plane-'+port,0);assert response['success'],response
   assert service.ledger._models['Model'].revision==0 and sdk.scope.consumed and sdk.scope.binding.used
   runner.protocol._require_point_selection_application(response['data'],args['spec']['selection'])
   assert len([r for r in sdk.scope.rows if r['method']=='selection' and r['receiver']==sdk.scope.feature])==1
   changed=copy.deepcopy(response['data']);changed['feature_readback']['selection_application']['after']['native_selection_readback']['entities']=[99]
   with pytest.raises(runner.protocol.ScienceProtocolError):runner.protocol._require_point_selection_application(changed,args['spec']['selection'])
   proofs.append({'port':port,'same_original_child':sdk.scope.selection_handle,'private_consume':sdk.scope.consumed,'revision':service.ledger._models['Model'].revision,'selection_records':sdk.scope.selection_records,'postread_changed_membership_consumer_refused':True,'actual_sdk_calls':sdk.calls})
  finally:daemon.close()
 save('ACTUAL_PUBLIC_BOTH_PLANES_SHARED_CHILD_AND_CONSUMER.json',{'source_only':True,'SDK_boundary_fixtures':True,'native':'NOT_RUN','proofs':proofs})
