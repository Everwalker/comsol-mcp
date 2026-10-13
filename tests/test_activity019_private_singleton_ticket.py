"""Source controls: original019 SDK replies, real production ticket algorithms.

Only the source transport correlation/handle generations/model identity are
rebased. Original failure flags stay verbatim. Non-Hz Interp replies have no
actual019 evidence and are explicitly synthetic, never native/science proof.
"""
import copy,json,os,importlib.util
from pathlib import Path
import pytest
from comsol_mcp import _g3_results as results
from comsol_mcp._solution_binding import SolutionBinding
from comsol_mcp._ephemeral_result_ticket import _Scope
from tests.test_ephemeral_result_ticket import _public,_call

F=Path(__file__).parent/'fixtures/w23_activity019'
HZ=json.loads((F/'ACTUAL_019_HZ_TYPED_TICKET_PROJECTION.json').read_text())
PORTS=json.loads((F/'ACTUAL018_019_PORT_DATASET_TYPED_PROJECTIONS.json').read_text())

def _save(label,obj):
    dest=os.environ.get('W23_ACTIVITY020_TICKET_CAPTURE')
    if dest:
        p=Path(dest);p.mkdir(parents=True,exist_ok=True)
        (p/(label+'.json')).write_text(json.dumps(obj,indent=2,sort_keys=True)+'\n')

def _setup(tmp_path,monkeypatch,*,dataset='dset1',unit='Hz',expressions=None,points=None,owned=True,fault=None):
    daemon,service,ref,project,sdk,tree,nodes,args=_public(tmp_path,monkeypatch)
    if owned:service.ledger.model_ownership['Model']='mcp_owned'
    service.ledger._models['Model'].revision=1
    service.ledger._models['Model'].fingerprint=HZ['snapshots'][0]['fingerprint']
    allpairs=HZ['all_typed_worker_pairs']
    selected=allpairs if dataset=='dset1' else next(p for p in PORTS if p['activity']=='019' and p['dataset']==dataset)['all_original_typed_worker_pairs']
    def pairs_by(rows):
        return [(p.get('observed_event') or p['observed_event'])['metadata'] for p in rows]
    records=pairs_by(selected);base=pairs_by(allpairs);requests=[];used=[];faults=[];consume=[]
    aliases={}
    oldrequest=sdk.request
    def rebase(reply,body):
        reply=copy.deepcopy(reply);reply['request_id']=body['request_id'];reply['type']=body['type']
        def handles(x):
            if isinstance(x,dict):
                if '$worker_handle' in x:x['generation']=1
                for v in x.values():handles(v)
            elif isinstance(x,list):
                for v in x:handles(v)
        handles(reply)
        if body['type']=='model_snapshot':
            reply['result']['model_tag']='Model';reply['result']['tag']='Model';reply['result']['server_instance_id']='public-test-epoch';reply['result']['generation']=1;reply['result']['instance_id']='synthetic-point-worker';reply['result']['worker_instance_id']='synthetic-point-worker'
        return reply
    def transport(body,timeout_s=None):
        requests.append(copy.deepcopy(body))
        from comsol_mcp._ephemeral_result_ticket import current_scope
        sdk.scope=current_scope() or sdk.scope
        method=body.get('method');a=body.get('args',[])
        member_receivers={r['metadata'].get('handle') for r in records if r['metadata'].get('method')=='tags' and r['reply'].get('result')==['std1']}
        member_target=not fault or fault['kind']!='member' or bool(aliases.get(body.get('handle'),{body.get('handle')}) & member_receivers)
        if fault and not faults and method==fault['method'] and member_target and (fault.get('args') is None or a==fault['args']):
            faults.append(copy.deepcopy(body));kind=fault['kind']
            if kind=='timeout':
                from comsol_mcp._java_worker import JavaWorkerTimeout
                raise JavaWorkerTimeout('SOURCE_CONTROL_REQUIRED_GETTER_TIMEOUT')
            if kind in {'unknown','running','incomplete','wrong-request'}:
                reply={'ok':False,'status':'FAILED','request_id':body['request_id'],'type':body['type'],'failure':{'code':'ENGINE_CALL_FAILED','execution_state_unknown':True,'message':'SOURCE_CONTROL_REQUIRED_GETTER'}}
                if kind=='running':reply['status']='RUNNING'
                if kind=='incomplete':reply={'ok':False,'request_id':body['request_id'],'type':body['type']}
                if kind=='wrong-request':reply['request_id']='SOURCE_WRONG_REQUEST'
                return reply
        # Exact recorded receiver-independent signatures. Handles/generation
        # are rebased transport identities; getter values/failures unchanged.
        candidates=[r for r in records if r['metadata'].get('type')==body['type'] and r['metadata'].get('method')==method and r['metadata'].get('args',[])==a]
        synthetic=False
        if not candidates:
            candidates=[r for r in base if r['metadata'].get('type')==body['type'] and r['metadata'].get('method')==method and r['metadata'].get('args',[])==a]
            synthetic=dataset!='dset1'
        if not candidates and method=='get' and a==[dataset]:
            candidates=[r for r in records if r['metadata'].get('method')=='get' and r['metadata'].get('args')==[dataset]]
        if not candidates and method=='setIndex' and a[:1]==['unit']:
            candidates=[r for r in base if r['metadata'].get('method')=='setIndex'];synthetic=True
        if not candidates and method=='set' and a[:1] in [['expr'],['data']]:
            candidates=[r for r in base if r['metadata'].get('method')=='set' and r['metadata'].get('args',[])[:1]==a[:1]];synthetic=True
        if not candidates and method=='setInterpolationCoordinates':
            candidates=[r for r in base if r['metadata'].get('method')==method];synthetic=True
        assert candidates,('NO_RECORDED_OR_DECLARED_SYNTHETIC_REPLY',body)
        receiver_aliases=aliases.get(body.get('handle'),{body.get('handle')})
        exact=[r for r in candidates if r['metadata'].get('handle') in receiver_aliases]
        if not exact and dataset!='dset1' and body['type']=='call':
            bridge=[r for r in base if r['metadata'].get('handle') in receiver_aliases and r['metadata'].get('method')==method and r['metadata'].get('args',[])==a]
            if bridge:exact=bridge;synthetic=True
        if body['type']=='call' and dataset=='dset1' and not synthetic:
            assert exact,('RECORDED_RECEIVER_MISMATCH',body)
        original=(exact or candidates)[0];reply=rebase(original['reply'],body)
        value=reply.get('result')
        if isinstance(value,dict) and '$worker_handle' in value:
            possible=exact or candidates
            equivalents={r['reply']['result']['$worker_handle'] for r in possible if isinstance(r['reply'].get('result'),dict) and '$worker_handle' in r['reply']['result']}
            aliases.setdefault(value['$worker_handle'],set()).update(equivalents)
        if fault and faults and faults[-1]==body and fault['kind'] in {'missing','multi','unit','member'}:
            reply['result']={'missing':None,'multi':[2,1],'unit':'m','member':['foreignStudy']}[fault['kind']]
        feature_handles={r['reply']['result']['$worker_handle'] for r in base if r['metadata'].get('method')=='create' and isinstance(r['reply'].get('result'),dict)}
        if dataset!='dset1' and method=='getString' and a==['data'] and body.get('handle') in feature_handles:reply['result']=dataset;synthetic=True
        if method=='getStringArray' and a==['expr'] and expressions is not None:reply['result']=expressions;synthetic=True
        if method=='getStringArray' and a==['unit'] and (unit!='Hz' or dataset!='dset1'):reply['result']=unit if isinstance(unit,list) else [unit];synthetic=True
        if method=='getData' and (dataset!='dset1' or expressions is not None or points is not None):
            count=len(points or args['points']);reply['result']=[[[float(i+1) for _ in range(count)]] for i in range(len(expressions or ['freq']))];synthetic=True
        if method=='getImagData' and (dataset!='dset1' or expressions is not None or points is not None):
            count=len(points or args['points']);reply['result']=[[[0.0 for _ in range(count)]] for _ in (expressions or ['freq'])];synthetic=True
        if method=='getCoordinates' and points is not None:reply['result']=[[p['x'] for p in points],[p['y'] for p in points]];synthetic=True
        used.append({'current_request':copy.deepcopy(body),'original_observed':copy.deepcopy(original),'rebased_reply':copy.deepcopy(reply),'synthetic_missing_native_reply':synthetic})
        return reply
    daemon.backend.worker._request=transport
    originalconsume=_Scope.consume
    def observe(scope,data,outcome,changed,callback):
        record={'outcome':outcome,'changed_engine':changed,'invalid_before':scope.invalid,'entered':scope.entered,'finished':scope.finished,'pending_count':len(scope.pending),'binding_intact':scope.binding.intact(),'before_tags':scope.before,'feature':scope.feature,'container':scope.container,'data_hash':scope.data_hash,'rows':copy.deepcopy(scope.rows)}
        result=originalconsume(scope,data,outcome,changed,callback);record.update({'actual_original_consume_result':result,'binding_used_after':scope.binding.used,'consumed_after':scope.consumed});consume.append(record);return result
    monkeypatch.setattr(_Scope,'consume',observe)
    args['spec']['solution']={'dataset':dataset,'solution':'sol'+dataset[-1]}
    args['spec']['units']=unit if isinstance(unit,list) else [unit]
    if expressions is not None:args['spec']['expressions']=expressions
    if points is not None:args['points']=points
    return daemon,service,ref,project,sdk,args,requests,used,faults,consume

def _run(tmp_path,monkeypatch,**options):
    d,s,r,p,sdk,a,requests,used,faults,consume=_setup(tmp_path,monkeypatch,**options)
    try:
        response=_call(d,r,p,a,'source019-new-key',1)
        _save('debug-last-response-'+options.get('dataset','dset1'),{'response':response,'requests':requests,'consume':consume})
        return response,s.ledger._models['Model'].revision,requests,used,faults,consume
    finally:d.close()

def test_real019_hz_r12_private_consume_pass_and_revision_stable(tmp_path,monkeypatch):
    response,revision,requests,used,faults,consume=_run(tmp_path,monkeypatch)
    assert response['success'] and revision==1,(response,revision,requests,consume)
    assert not any(r.get('method')=='getSolnums' for r in requests)
    assert consume[-1]['actual_original_consume_result'] is True and not consume[-1]['invalid_before']
    assert response['data']['solution_axes']['pair_mapping_complete'] is False
    assert response['data']['field_array'] is None
    assert not any(r['synthetic_missing_native_reply'] for r in used)
    _save('new-hz-complete-private-scope',{'source_only':True,'native':'NOT_RUN','response':response,'requests':requests,'replay':used,'consume':consume})

def test_old_r11_real019_hz_unknown_flag_preserved_and_revision_increment(tmp_path,monkeypatch):
    from tests._published_history import published_source
    old=published_source('w23-saved-mph-science-readback-20261013-candidate20-r11/comsol_mcp/_g3_results.py')
    spec=importlib.util.spec_from_file_location('comsol_mcp._source_old_r11_results',old);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(results,'_ORIGINAL_RESULT_AT_POINTS',module._ORIGINAL_RESULT_AT_POINTS)
    response,revision,requests,used,faults,consume=_run(tmp_path,monkeypatch)
    assert response['success'] and revision==2,(response,revision,requests,consume)
    failed=[r for r in used if r['current_request'].get('method')=='getSolnums']
    assert failed and failed[0]['rebased_reply']['failure']['execution_state_unknown'] is True
    assert consume[-1]['invalid_before'] is True and consume[-1]['actual_original_consume_result'] is False
    _save('old-r11-hz-danger-reproduction',{'source_only':True,'native':'NOT_RUN','response':response,'replay':used,'consume':consume})

@pytest.mark.parametrize('kind,method,args', [('unknown','getSize',None),('timeout','getSize',None),('running','getSize',None),('incomplete','getSize',None),('wrong-request','getSize',None),('missing','getSize',None),('multi','getSizeMulti',None),('unit','getUnit',['freq']),('member','tags',[])])
def test_attached_actual_dispatched_required_getter_fault_blocks_exemption(tmp_path,monkeypatch,kind,method,args):
    response,revision,requests,used,faults,consume=_run(tmp_path,monkeypatch,fault={'kind':kind,'method':method,'args':args})
    assert len(faults)==1
    assert revision>1 or not response['success']
    assert not consume or consume[-1]['actual_original_consume_result'] is False
    _save('attached-fault-'+kind,{'source_only':True,'native':'NOT_RUN','injected_request':faults,'response':response,'revision':revision,'consume':consume})

@pytest.mark.parametrize('slot', ['freq_GHz','fields_output_x8_321','fields_output_x8_641','fields_input_xminus10_321','fields_input_xminus10_641','mode_point_bmaInput','mode_point_bmaOutput'])
def test_remaining_seven_slots_recorded_metadata_synthetic_interp_scope(tmp_path,monkeypatch,slot):
    from tools import run_native_w23_saved_mph_diagnostic as runner
    options={}
    if slot=='freq_GHz':options={'unit':'GHz'}
    elif slot.startswith('fields_'):
        count=int(slot.rsplit('_',1)[1]);x=8.0 if 'output' in slot else -10.0
        options={'expressions':list(runner.RAW_FIELD_EXPRESSIONS),'unit':list(runner.FIELD_REQUESTED_UNITS),'points':[{'x':x,'y':-8+16*i/(count-1)} for i in range(count)]}
    else:
        port='1' if 'Input' in slot else '2'
        options={'dataset':'dset2' if port=='1' else 'dset3','unit':['1/m','1','1/m'],'expressions':list(runner.protocol.bma_point_expressions(port)),'points':[{'x':-10.0 if port=='1' else 8.0,'y':0.0}]}
    response,revision,requests,used,faults,consume=_run(tmp_path,monkeypatch,**options)
    assert response['success'] and revision==1,(response,revision,consume)
    assert consume[-1]['actual_original_consume_result'] is True
    assert response['data']['solution_axes']['pair_mapping_complete'] is False
    assert response['data']['field_array'] is None
    assert any(r['synthetic_missing_native_reply'] for r in used)
    optional=[r for r in used if r['current_request'].get('method')=='getSolnums']
    if 'mode_point' in slot:
        assert len(optional)==1 and optional[0]['rebased_reply']['ok'] is True and optional[0]['rebased_reply']['result']==[]
        assert response['data']['result_budget']['records'][0]['solution_count_observation']['witness_kind']=='stored_eigenfrequency_singleton'
    else:assert not optional
    _save(slot,{'source_only':True,'native':'NOT_RUN','science':'NOT_ACCEPTED','metadata_original_activity019':True,'Interp_missing_native_replies_synthetic':True,'response':response,'replay':used,'consume':consume})

def test_unowned_current_hz_preserves_generic_diagnostic_and_denies_exemption(tmp_path,monkeypatch):
    response,revision,requests,used,faults,consume=_run(tmp_path,monkeypatch,owned=False)
    assert response['success'] and revision==2
    assert any(r.get('method')=='getSolnums' for r in requests)
    assert consume[-1]['actual_original_consume_result'] is False

@pytest.mark.parametrize('private_skip', [False,True])
def test_generic_solutionbinding_successful_tuple_api_default_and_nonempty_preserved(private_skip):
    class Info:
        def __init__(self):self.calls=[]
        def getOuterSolnum(self):self.calls.append('outer');return [] if not private_skip else [2]
        def getSolnums(self,names):self.calls.append('getSolnums');return [[2,7]]
        def getSolnum(self,outer,strict):assert outer==2 and strict is True;return [7]
        def getPNames(self,pairs):return [['freq']]
        def getPvals(self,pairs):return [[10.0]]
        def getUnits(self,pairs):return [['Hz']]
        def getLevelNames(self):return ['freq']
    info=Info();binding=SolutionBinding.resolve_solution_info(info,_skip_empty_outer_tuple_diagnostic=private_skip)
    assert binding['pair_mapping_complete'] is True and binding['solnum_pairs']==[{'outer':2,'inner':7,'solnum':7}]
    assert ('getSolnums' in info.calls) is (not private_skip)
