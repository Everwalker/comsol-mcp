"""SOURCE_ONLY regression of original production helpers, no native execution."""
import copy,json,os
from pathlib import Path
from types import SimpleNamespace
import pytest
from comsol_mcp import _g3_common as common
from comsol_mcp._measure_spec import MeasureSpec
from comsol_mcp._execution_contract import ExecutionContractError
from tools import run_native_w23_saved_mph_diagnostic as runner
from tests.test_activity022_boundary_selection_ticket import Selection
from tests.test_g3_results import _requested_unit_point_tree

class AllSelection(Selection):
    def geom(self,*args):
        self._guard('geom',args)
        if len(args)==1:self.d=args[0]
        elif args:self.g,self.d=args
        return self.g if not args else self
    def all(self):self._guard('all',());self.ids=[1,3,5];return self


def test_real_strict_measure_all_resolver_and_shared_binding_compatibility(monkeypatch):
    tree,nodes,args=_requested_unit_point_tree(monkeypatch)
    measure_selection=AllSelection();measure=SimpleNamespace(selection=lambda:measure_selection)
    tree.components.items['comp1'].measure=lambda:measure
    requested={'kind':'all','component':'comp1','geometry':'geom1','entity_dimension':1}
    expected=common.resolve_selection_entities(tree.worker,'Model',requested)
    assert expected['entities']==[1,3,5] and expected['source']=='measure_tool_all'
    child=AllSelection();feature=SimpleNamespace(selection=lambda:child)
    ms=MeasureSpec(aggregate='integral',space_dim=2,entity_dim=1,selection=requested)
    ms.apply_selection(feature)
    result=common.bind_strict_feature_selection(tree.worker,'Model',feature,requested,stage='before_numeric_read',expected=expected,apply=True)
    assert result['entities']==[1,3,5] and result['native_selection_readback']['is_inheriting'] is False
    assert sum(name=='all' for name,args in child.calls)==2
    # The actual public points producer still rejects all before creating an Interp.
    from comsol_mcp._g3_results import result_at_points
    args['spec']['selection']=requested;args['spec']['entity_dim']=1
    with pytest.raises(ExecutionContractError):result_at_points(tree.worker,'Model',args)
    assert not nodes

@pytest.mark.parametrize('fault',['ignored-all','wrong-dimension','unknown-inheritance'])
def test_measure_all_readback_stays_fail_closed(fault):
    requested={'kind':'all','component':'comp1','geometry':'geom1','entity_dimension':1}
    child=AllSelection()
    if fault=='ignored-all':child.all=lambda:child
    if fault=='wrong-dimension':child.dim=lambda:2
    if fault=='unknown-inheritance':child.isInheriting=lambda:None
    with pytest.raises(ExecutionContractError):common.bind_strict_feature_selection(None,'Model',SimpleNamespace(selection=lambda:child),requested,stage='before_numeric_read',expected={'entities':[1,3,5],'source':'SOURCE_ONLY_ENUMERATED_ALL'},apply=True)


def _actual_entry_reference(tmp_path):
    published=runner.REPO/'continuation/candidates/w23-r14'
    packet=published/'root-review-packet/final6/native-capture-supplement022-003'
    m=json.loads((packet/'NATIVE_CAPTURE_INVOCATION_ACTIVITY022_R14.json').read_text())
    old=Path(m['exact_native_invocation']['cwd'])
    frozen=published/Path(m['freeze']['path']).relative_to(old)
    assert runner.sha256_file(frozen)==m['freeze']['sha256']
    f=json.loads(frozen.read_text())
    a=json.loads((packet/'ROOT_APPROVAL_EXPECTED_FIELDS_REFERENCE_ONLY.json').read_text())['expected_approval_fields']
    approval_name=f"ROOT_APPROVAL_ACTIVITY{runner.ACTIVITY_ID.rsplit('-',1)[1]}.json"
    old_approval_names=('ROOT_APPROVAL_ACTIVITY021.json','ROOT_APPROVAL_ACTIVITY022.json')
    current=str(runner.REPO)
    old_root=str(old)
    for old_approval_name in old_approval_names:
        m=json.loads(json.dumps(m).replace(old_root,current).replace(old_approval_name,approval_name))
        f=json.loads(json.dumps(f).replace(old_root,current).replace(old_approval_name,approval_name))
    f['activity_id']=runner.ACTIVITY_ID;f['candidate_id']=runner.CANDIDATE_ID;f['candidate_revision']=runner.CANDIDATE_REVISION
    a=json.loads(json.dumps(a).replace(str(old),current))
    a['activity_id']=runner.ACTIVITY_ID;a['candidate_id']=runner.CANDIDATE_ID;a['candidate_revision']=runner.CANDIDATE_REVISION
    a['native_argv_sha256']=runner.sha256_json(m['exact_native_invocation']['argv'])
    a['exact_native_invocation']={
        'argv':m['exact_native_invocation']['argv'],
        'cwd':m['exact_native_invocation']['cwd'],
        'environment':runner._native_environment(f.get('fresh_runtime_binding')),
    }
    p=tmp_path/'literal.zsh';p.write_text((Path(__file__).parent/'fixtures/w23_activity022/ACTUAL_R14_WRONG_LITERAL_ENTRY.zsh').read_text().replace(old_root,current))
    return p,m,f,a


def test_actual_r14_literal_mismatch_reproduced_and_original_four_arg_guard_passes_only_exact_script(tmp_path):
    p,m,f,a=_actual_entry_reference(tmp_path)
    with pytest.raises(RuntimeError,match='literal script argv differs'):runner._validate_literal_capture_entry(p,m,a,f,m['freeze']['sha256'])
    p.write_text(p.read_text().replace('ROOT_APPROVAL_ACTIVITY021.json',f"ROOT_APPROVAL_ACTIVITY{runner.ACTIVITY_ID.rsplit('-',1)[1]}.json"))
    receipt=runner._validate_literal_capture_entry(p,m,a,f,m['freeze']['sha256'])
    assert receipt['actual_argv']==m['exact_native_invocation']['argv']
    assert receipt['actual_approval_path']==f['approval_path_expected']
    wrong=copy.deepcopy(a);wrong['native_argv_sha256']='0'*64
    with pytest.raises(RuntimeError):runner._validate_literal_capture_entry(p,m,wrong,f,m['freeze']['sha256'])

@pytest.mark.parametrize('fault',['approval-test','symlink-test','cwd','extraexec','expansion'])
def test_actual_fixed_script_guards_cannot_be_hidden_by_matching_metadata(tmp_path,fault):
    p,m,f,a=_actual_entry_reference(tmp_path);text=p.read_text().replace('ROOT_APPROVAL_ACTIVITY021.json',f"ROOT_APPROVAL_ACTIVITY{runner.ACTIVITY_ID.rsplit('-',1)[1]}.json")
    if fault=='approval-test':text=text.replace('test -f '+f['approval_path_expected'],'test -f /private/tmp/foreign.json')
    elif fault=='symlink-test':text=text.replace('test ! -L '+f['approval_path_expected'],'test ! -L /private/tmp/foreign.json')
    elif fault=='cwd':text=text.replace('cd '+m['exact_native_invocation']['cwd'],'cd /private/tmp')
    elif fault=='extraexec':text+='\nexec echo unsafe\n'
    else:text=text.replace('exec env ','exec env $UNTRUSTED ')
    p.write_text(text)
    with pytest.raises(RuntimeError):runner._validate_literal_capture_entry(p,m,a,f,m['freeze']['sha256'])


def test_actual_java_fullcampaign_literal_matrix_real_consumer_and_saved_two_planes():
    import re
    from tests.test_native_w23_te_science_protocol import _raw
    java=(runner.REPO/'tools/java/NativeW23TEScienceFixture.java').read_text()
    block=re.search(r'private static final String\[\] RAW_FIELDS = \{(.*?)\};',java,re.S).group(1)
    names=re.findall(r'"([^"]+)"',block);assert len(names)==22
    # The transformed fixture explicitly implements each known role contract;
    # original actual Java source determines names, no consumer-name fallback.
    def legacy(plane,x,nx):
        n=runner.protocol.SAMPLE_COUNT
        # Fully synthetic literalproducer rows, built directly in the Java22
        # contract. No conversion/relabeling of savedpoint numerical payloads.
        values={name:[0j]*n for name in names}
        if plane=='output_x8':
            values['ewfd.Ez']=[2+1j]*n;values['ewfd.Hy']=[-1+.5j]*n
            values['ewfd.Emodez_2']=[1-.25j]*n;values['ewfd.Hmodey_2']=[-.5-.75j]*n
        else:
            values['ewfd.Emodez_1']=[1+.5j]*n;values['ewfd.Hmodey_1']=[-.75+.25j]*n
        values['nx']=[complex(nx)]*n;values['ny']=[0j]*n
        values['ewfd.neff_1']=[1.540405036791]*n;values['ewfd.neff_2']=[1.540405036791]*n
        return {'status':'RAW_NATIVE_COMPLEX_FIELDS_READ','plane':plane,'sample_count':n,'complex_readback':True,'normal_sign':nx,
            'coordinates_m':[[x]*n,[-8e-6+16e-6*i/(n-1) for i in range(n)]],
            'expressions':[{'expression':name,'real':[z.real for z in values[name]],'imag':[z.imag for z in values[name]]} for name in names]}
    out=legacy('output_x8',8e-6,1);inc=legacy('input_xminus10',-10e-6,-1)
    result=runner.protocol.independent_integrals(out,inc)
    assert result['status']=='SOFTWARE_RECOMPUTED_FROM_NATIVE_RAW_FIELDS'
    from tools import run_native_w23_te_science as full
    # Original fullcampaign uses exact literal Java neffnames; labels here are
    # SDK fixture values only and never native/scientific evidence.
    assert full._validate_mode_index(out,'ewfd.neff_2',2e-3)['status']=='PASS'
    assert full._validate_mode_index(inc,'ewfd.neff_1',2e-3)['status']=='PASS'
    savedout=_raw('output_x8',8e-6,1);savedin=_raw('input_xminus10',-10e-6,-1)
    saved=runner.protocol.independent_integrals(savedout,savedin,field_scheme='emw_down')
    assert saved['reciprocal_overlap_numerator']==result['reciprocal_overlap_numerator']
    for scheme,left,right in [('legacy',savedout,savedin),('emw_down',out,inc),('unknown',out,inc)]:
        with pytest.raises(runner.protocol.ScienceProtocolError):runner.protocol.independent_integrals(left,right,field_scheme=scheme)


def test_full_and_saved_definition_scheme_is_explicit_default_legacy():
    from tools import run_native_w23_te_science as full
    sources={'sources':{role:{'dataset_id':'d'+role,'solution_id':'s'+role,'outer_index':1,'inner_index':1} for role in ['signal','reference_mode','incident_reference']}}
    legacy=full.build_mode_overlap_definition(sources,1,-1)
    saved=full.build_mode_overlap_definition(sources,1,-1,field_scheme='emw_down')
    assert legacy['signal']['fields']['electric']['x']=='ewfd.Ex'
    assert legacy['reference_mode']['fields']['magnetic']['z']=='ewfd.Hmodez_2'
    assert saved['signal']['fields']['electric']['x']=='down(emw.Ex)'
    assert saved['reference_mode']['fields']['magnetic']['z']=='emw.Hmodez_2'
    with pytest.raises(runner.protocol.ScienceProtocolError):full.build_mode_overlap_definition(sources,1,-1,field_scheme='unknown')
