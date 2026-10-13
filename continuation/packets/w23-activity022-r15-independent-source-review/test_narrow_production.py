"""Fresh independent targeted successor controls. SDK fixtures only, never native."""
from pathlib import Path
import ast,copy,inspect,json,os,re,shlex
import pytest
from comsol_mcp import _g3_results as results
from comsol_mcp._execution_contract import ExecutionContractError
from tests.test_g3_w17 import FWiredTree,FSelection
from tests.test_g3_results import _requested_unit_point_tree
from tests.test_native_w23_te_science_protocol import _raw,_native_from_quadrature
from tools import run_native_w23_saved_mph_diagnostic as runner
from tools import run_native_w23_te_science as full
from tools import w23_te_science_protocol as proto
C=Path(runner.REPO);O=Path(__file__).parent;R14=C.parent/'w23-saved-mph-science-readback-20261013-candidate20-r14'
def save(name,obj):
 fd=os.open(O/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as f:f.write(json.dumps(obj,indent=2,default=str)+'\n')

def test_real_strict_evaluate_all_returns_verified_and_points_reject_prechild(monkeypatch):
 monkeypatch.setattr(FSelection,'all',lambda self:setattr(self,'_entities',[1,2]))
 class MeasureSelection:
  def __init__(self):self.calls=[]
  def geom(self,dim):self.calls.append(['geom',dim])
  def all(self):self.calls.append(['all'])
  def entities(self):self.calls.append(['entities']);return [1,2]
 tree=FWiredTree(numerical_real=42.0);measure=MeasureSelection()
 class M:
  def selection(self):return measure
 tree.component.measure=lambda:M()
 selection={'kind':'all','component':'comp1','geometry':'geom1','entity_dimension':3}
 args={'spec':{'expressions':['T'],'solution':{'dataset':'dset1','solution':'sol1'},'aggregate':'integral','complex_mode':'real','selection':selection,'storage':'inline'}}
 response=results.result_evaluate(tree.worker,'Model',args,strict_metric_evidence=True);e=response['strict_metric_evidence'];assert e['status']=='VERIFIED' and all(row['entities']==[1,2] for row in e['selection_features']);assert tree.numerical_list.tags()==[]
 point,nodes,args2=_requested_unit_point_tree(monkeypatch);args2['spec']['selection']={'kind':'all','component':'comp1','geometry':'geom1','entity_dimension':1};args2['spec']['entity_dim']=1
 with pytest.raises(ExecutionContractError) as refusal:results.result_at_points(point.worker,'Model',args2)
 assert nodes==[]
 old=json.loads((O.parent/'w23-activity022-r14-independent-source-review/ACTUAL_STRICT_MEASURE_ALL_R13_R14.json').read_bytes());assert old['proofs']['r13']['status']=='RETURNED' and old['proofs']['r14']['status']=='REFUSED'
 save('ACTUAL_R15_STRICT_MEASURE_ALL_AND_POINT_REFUSAL.json',{'source_only':True,'SDK_fixture':True,'r14_original_refusal_inherited':True,'current_actual_strict_metric_evidence':e,'real_measure_calls':measure.calls,'point_prechild_refusal':{'code':refusal.value.code,'message':str(refusal.value),'created_children':len(nodes)},'native':'NOT_RUN'})

def test_actual_final3_literal_script_all_tokens_guards_original_validator(tmp_path):
 d=C/'root-review-packet/final3';binding=json.loads((d/'FINAL_SOURCE_SOFTWARE_AND_FIXED_ENTRY_BINDING_ACTIVITY022_R15.json').read_bytes());f=json.loads(Path(binding['freeze']['path']).read_bytes());m=json.loads(Path(binding['metadata']['path']).read_bytes());a=json.loads(Path(m['approval_blueprint']['path']).read_bytes())['expected_approval_fields'];p=Path(m['script']['path']);text=p.read_text();tokens=shlex.split(next(x for x in text.splitlines() if x.startswith('exec ')))[1:];argv=tokens[:tokens.index('>')]
 assert argv==m['exact_native_invocation']['argv'] and argv[argv.index('--approval')+1]==f['approval_path_expected']
 real=runner._validate_literal_capture_entry(p,m,a,f,binding['freeze']['sha256']);runner._validate_approval(a,f,binding['freeze']['sha256'],argv)
 wrong=tmp_path/'wrong.zsh';wrong.write_text(text.replace('ROOT_APPROVAL_ACTIVITY022.json','ROOT_APPROVAL_ACTIVITY021.json'))
 with pytest.raises(RuntimeError,match='literal script argv differs'):runner._validate_literal_capture_entry(wrong,m,a,f,binding['freeze']['sha256'])
 wrong.write_text(text.replace('test -f '+f['approval_path_expected'],'test -f /private/tmp/foreign.json'))
 with pytest.raises(RuntimeError,match='approval guards'):runner._validate_literal_capture_entry(wrong,m,a,f,binding['freeze']['sha256'])
 save('ACTUAL_R15_LITERAL_SCRIPT_AND_FOUR_ARG_GUARD.json',{'source_only':True,'source_script_executed':False,'original_actual25tokens_equal_metadata':True,'actual_validator':real,'wrong_original021_path_refused':True,'matchingexec_foreign_test_f_refused':True,'Rootapproval':'REFERENCE_ONLY_NOT_CREATED','native':'NOT_RUN'})

def legacy_raw(plane,x,nx,n=641,phase=1.,modephase=1.):
 java=(C/'tools/java/NativeW23TEScienceFixture.java').read_text();body=java.split('private static final String[] RAW_FIELDS = {',1)[1].split('};',1)[0];names=re.findall(r'"([^"]+)"',body);assert len(names)==22
 values={name:[0j]*n for name in names}
 if plane=='output_x8':
  values['ewfd.Ez']=[phase*(2+1j)]*n;values['ewfd.Hy']=[phase*(-1+.5j)]*n;values['ewfd.Emodez_2']=[modephase*(1-.25j)]*n;values['ewfd.Hmodey_2']=[modephase*(-.5-.75j)]*n
 else:values['ewfd.Emodez_1']=[phase*(1+.5j)]*n;values['ewfd.Hmodey_1']=[phase*(-.75+.25j)]*n
 values['ewfd.neff_1']=[full.EXPECTED_NEFF_TE0]*n;values['ewfd.neff_2']=[full.EXPECTED_NEFF_TE0]*n;values['nx']=[complex(nx)]*n
 return {'status':'RAW_NATIVE_COMPLEX_FIELDS_READ','sample_count':n,'plane':plane,'complex_readback':True,'normal_sign':nx,'coordinates_m':[[x]*n,[-8e-6+16e-6*i/(n-1) for i in range(n)]],'expressions':[{'expression':name,'real':[z.real for z in values[name]],'imag':[z.imag for z in values[name]]} for name in names]}

def test_original_java_legacy_and_explicit_saved_schemas_all_pure_consumers_and_callers():
 proofs=[]
 for scheme in ['legacy','emw_down']:
  if scheme=='legacy':out=legacy_raw('output_x8',8e-6,1);inc=legacy_raw('input_xminus10',-10e-6,-1);rot=legacy_raw('output_x8',8e-6,1,phase=1j)
  else:out=_raw('output_x8',8e-6,1,sample_count=641);inc=_raw('input_xminus10',-10e-6,-1,sample_count=641);rot=_raw('output_x8',8e-6,1,sample_count=641,phase=1j)
  kw={} if scheme=='legacy' else {'field_scheme':'emw_down'}
  base=proto.independent_integrals(out,inc,**kw);q90=proto.independent_integrals(rot,inc,**kw);capture=proto.independent_capture_aperture_flux(out,**kw);phase=proto.software_global_phase_control(out,inc,**kw)
  native0=_native_from_quadrature(base);native90=_native_from_quadrature(q90);native0.update(overlap_amplitude={'real':1.,'imag':0.},reference_coupling_efficiency=.75);native90.update(overlap_amplitude={'real':0.,'imag':1.},reference_coupling_efficiency=.75)
  c0={'phase_value':'0[deg]','output_port_phase':'0[deg]','output_fields':out,'native_overlap':native0};c90={'phase_value':'90[deg]','output_port_phase':'0[deg]','output_fields':rot,'native_overlap':native90}
  phasepair=proto.verify_native_phase_pair(c0,c90,**kw)
  other='emw_down' if scheme=='legacy' else 'legacy'
  with pytest.raises(proto.ScienceProtocolError):proto.independent_integrals(out,inc,field_scheme=other)
  missing=copy.deepcopy(out);missing['expressions'].pop(0)
  with pytest.raises(proto.ScienceProtocolError):proto.independent_integrals(missing,inc,**kw)
  proofs.append({'scheme':scheme,'matrix_count':len(out['expressions']),'math':base,'capture':capture,'software_phase':phase,'synthetic_native_phase_pair_algorithm':phasepair,'opposite_schema_and_missing_row_refused':True})
 sources={'sources':{r:{'dataset_id':'d'+r,'solution_id':'s'+r,'outer_index':1,'inner_index':1} for r in ['signal','reference_mode','incident_reference']}}
 legacy=full.build_mode_overlap_definition(sources,1,-1);saved=full.build_mode_overlap_definition(sources,1,-1,field_scheme='emw_down');assert legacy['signal']['fields']['electric']['x']=='ewfd.Ex' and saved['signal']['fields']['electric']['x']=='down(emw.Ex)'
 calls=[]
 for n in ast.walk(ast.parse((C/'tools/run_native_w23_te_science.py').read_bytes())):
  if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='build_mode_overlap_definition':
   assert all(k.arg is not None for k in n.keywords);bound=inspect.signature(full.build_mode_overlap_definition).bind(*[object() for _ in n.args],**{k.arg:object() for k in n.keywords});bound.apply_defaults();assert bound.arguments['field_scheme']=='legacy';calls.append({'line':n.lineno,'field_scheme':'default_legacy','signature_bound':True})
 assert len(calls)==2
 callsrc=(C/'tools/run_native_w23_saved_mph_diagnostic.py').read_text();assert 'decoded_fields["input_xminus10"][sample_count], field_scheme="emw_down")' in callsrc
 assert (C/'tools/java/NativeW23TEScienceFixture.java').read_bytes()==(R14/'tools/java/NativeW23TEScienceFixture.java').read_bytes()
 save('ACTUAL_R15_LEGACY_SAVED_MATH_AND_DEFINITION_CALLERS.json',{'source_only':True,'synthetic_math_inputs_not_native':True,'Java_original22_byteexact':True,'proofs':proofs,'two_actual_fullcampaign_definition_callers':calls,'legacy_definition':legacy,'explicit_saved_definition':saved,'native_scientific_acceptance':'NOT_RUN'})
