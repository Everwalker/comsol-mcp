"""Actual public Controller/Store/Service/ledger/Worker.submit; SDK transport only.

Synthetic numerical values test source plumbing and never attest native scaling.
The resolver remains real and returns actual015 nonNone incomplete metadata.
"""
import copy
import json
import os
from pathlib import Path
import pytest
from tests.test_ephemeral_result_ticket import _public, _call
from tests.test_g3_results import FInterp, FNode
from comsol_mcp import _g3_results as results
from tools import run_native_w23_saved_mph_diagnostic as runner


def _install_point_values(monkeypatch):
    def data(self):
        self._guard('getData', ())
        n=len(self.coords[0]); value=193414489032258.03
        return [[[value if unit=='Hz' else value/1e9 if unit=='GHz' else 1.0]*n]
                for unit in self.props['unit']]
    def imag(self):
        self._guard('getImagData', ())
        return [[[0.0]*len(self.coords[0])] for _ in self.props['expr']]
    monkeypatch.setattr(FInterp,'getData',data)
    monkeypatch.setattr(FInterp,'getImagData',imag,raising=False)


def test_activity017_exact_eight_slots_real_public_stack_keep_revision_and_partial_axes(tmp_path,monkeypatch):
    daemon,service,ref,project,sdk,tree,nodes,args=_public(tmp_path,monkeypatch)
    _install_point_values(monkeypatch)
    # SDK-native dataset and solver collection fixtures. No resolver/FieldArray fake.
    sol_list=tree.model.collections['sol']; original=tree.solver
    for dataset,solution in [('dset2','sol2'),('dset3','sol3')]:
        solver=copy.copy(original);solver.tag_=solution
        sol_list.items[solution]=solver
        tree.datasets.items[dataset]=FNode(dataset,'Solution',props=dict(tree.datasets.items['dset1'].props,solution=solution))
    plan=[('freq_Hz','dset1','sol1',('freq',),('Hz',),[[-10.,0.]]),
          ('freq_GHz','dset1','sol1',('freq',),('GHz',),[[-10.,0.]])]
    plan += [(f'fields_{plane}_{count}','dset1','sol1',runner.RAW_FIELD_EXPRESSIONS,runner.FIELD_REQUESTED_UNITS,runner._field_points(x,count))
             for plane,x in [('output_x8',8.),('input_xminus10',-10.)] for count in (321,641)]
    plan += [(f'mode_point_{step}',dataset,solution,runner.protocol.bma_point_expressions(port),tuple(runner.mode_units(port).values()),[[x,0.]])
             for step,dataset,solution,port,x in [('bmaInput','dset2','sol2','1',-10.),('bmaOutput','dset3','sol3','2',8.)]]
    captured=[]
    try:
        runner._set_dispatch_allowlist(daemon,tmp_path/'allowlist.jsonl')
        for label,dataset,solution,expressions,units,points in plan:
            partial=results._result_solution_binding(tree.model,solution)
            assert partial and partial['pair_mapping_complete'] is False
            spec={'expressions':list(expressions),'units':list(units),'solution':{'dataset':dataset,'solution':solution},'complex_mode':'preserve'}
            public={'spec':spec,'points':runner._public_points(points),'coordinate_unit':'um','frame':'spatial'}
            response=_call(daemon,ref,project,public,label,0)
            assert response['success'],response
            assert response['execution']['revision']==0 and response['execution']['model_ref']==ref
            data=response['data'];axes=data['solution_axes'];proof=data['feature_readback']['requested_unit_application']
            assert data['field_array'] is None and axes['pair_mapping_complete'] is False
            assert axes['parameters_complete'] is False and axes['axis_metadata_complete'] is False
            runner.protocol._require_requested_unit_application(proof,expressions,units)
            assert tree.numerical.items=={} and service.ledger._models['Model'].revision==0
            operation=daemon.store.db.execute('SELECT operation_id FROM operations WHERE request_id=?',(label,)).fetchone()
            op=daemon.store.get_operation(operation['operation_id']);assert op['result']==response and op['status']=='SUCCEEDED'
            captured.append({'label':label,'actual_public_request':public,'actual_terminal_response':response,'actual_scope_rows':copy.deepcopy(sdk.scope.rows)})
        assert len(captured)==8 and len(nodes)==8
        assert all(any(row['method']=='getData' for row in item['actual_scope_rows']) for item in captured)
        capture=Path(os.environ.get('W23_ACTIVITY017_SOFTWARE_CAPTURE',str(tmp_path)))
        capture.mkdir(parents=True,exist_ok=True)
        (capture/'actual-eight-slots.json').write_text(json.dumps(captured,indent=2)+'\n')
    finally:daemon.close()


@pytest.mark.parametrize('mutation',['missing','wrong','extra','short'])
def test_activity017_field_explicit_units_closed_allowlist_zero_submit(tmp_path,monkeypatch,mutation):
    daemon,service,ref,project,sdk,tree,nodes,args=_public(tmp_path,monkeypatch)
    try:
        runner._set_dispatch_allowlist(daemon,tmp_path/'allowlist.jsonl')
        body={'spec':{'expressions':list(runner.RAW_FIELD_EXPRESSIONS),'units':list(runner.FIELD_REQUESTED_UNITS),'solution':{'dataset':'dset1','solution':'sol1'},'complex_mode':'preserve'},'points':runner._public_points(runner._field_points(8.,321)),'coordinate_unit':'um','frame':'spatial'}
        if mutation=='missing':body['spec'].pop('units')
        elif mutation=='wrong':body['spec']['units'][0]='A/m'
        elif mutation=='extra':body['spec']['units'].append('1')
        else:body['spec']['units'].pop()
        before=len(sdk.calls)
        with pytest.raises(RuntimeError,match='allowlist'):_call(daemon,ref,project,body,'bad-'+mutation,0)
        assert len(sdk.calls)==before and not nodes and service.ledger._models['Model'].revision==0
    finally:daemon.close()
