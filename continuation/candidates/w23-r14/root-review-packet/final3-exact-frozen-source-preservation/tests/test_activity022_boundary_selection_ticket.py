"""SOURCE_ONLY real production G3/Scope/Service with synthetic SDK transport.
Actual021 rows prove identities/domain scope, not actual Interp numerical results.
"""
import copy,json,os
from pathlib import Path
import pytest
from tests.test_ephemeral_result_ticket import _public,_call,Entity,_failed
from tools import run_native_w23_saved_mph_diagnostic as runner

class Selection(Entity):
    def __init__(self,*,global_=False):
        super().__init__();self.g='geom1';self.d=1;self.n='selInputPort' if global_ else '';self.ids=[1,3,5] if global_ else [];self.global_=global_
    def geom(self,*args):
        self._guard('geom',args)
        if args:self.g,self.d=args
        return self.g if not args else self
    def dim(self):self._guard('dim',());return self.d
    def named(self,*args):
        self._guard('named',args)
        if args:self.n=args[0];self.ids=[1,3,5] if self.n=='selInputPort' else [8,10,12]
        return self.n if not args else self
    def entities(self):self._guard('entities',());return list(self.ids)
    def isInheriting(self):self._guard('isInheriting',());return False
    def set(self,ids):self._guard('set',(ids,));self.ids=list(ids)

def setup(tmp_path,monkeypatch):
    out=_public(tmp_path,monkeypatch);daemon,service,ref,project,sdk,tree,nodes,args=out
    global_sel=Selection(global_=True);comp=tree.components.items['comp1'];comp.selection=lambda tag:global_sel
    factory=tree.numerical.factory
    def create(tag,kind):
        node=factory(tag,kind);child=Selection();node.selection=lambda:child;node.child=child;return node
    tree.numerical.factory=create
    args['spec']['selection']=runner.plane_selection('1');args['spec']['entity_dim']=1
    return out,global_sel

def capture(fault,sdk,response,revision):
    dest=os.environ.get('W23_ACTIVITY022_SELECTION_CAPTURE')
    if dest:
        p=Path(dest);p.mkdir(parents=True,exist_ok=True);(p/(fault+'.json')).write_text(json.dumps({'source_only':True,'native':'NOT_RUN','fault':fault,'response':response,'revision':revision,'actual_sdk_requests':sdk.calls,'private_scope_rows':sdk.scope.rows,'private_consume':sdk.scope.consumed,'scope_invalid':sdk.scope.invalid,'selection_records':sdk.scope.selection_records},indent=2)+'\n')

def test_original_public_scope_consume_accepts_only_actual_child_and_readbacks(tmp_path,monkeypatch):
    out,g=setup(tmp_path,monkeypatch);daemon,service,ref,project,sdk,tree,nodes,args=out
    try:
        response=_call(daemon,ref,project,args,'selection-positive',0)
        capture('positive',sdk,response,service.ledger._models['Model'].revision)
        assert response['success'],response
        assert service.ledger._models['Model'].revision==0,(sdk.scope.invalid,sdk.scope.rows)
        assert sdk.scope.consumed and sdk.scope.binding.used
        assert sdk.scope.selection_java_type=='com.comsol.model.FixtureSDKEntity' # concrete implementation intentionally not LocalSelection interface
        assert set(sdk.scope.selection_records)=={'before_numeric_read','after_numeric_read'}
        assert not [c for c in g.calls if c[0] in {'set','named','geom'} and c[1]]
    finally:daemon.close()

@pytest.mark.parametrize('fault',['global-child','wrong-parent','child-generation','ignored-geom','ignored-named','wrong-members','wrong-dim','missing-getter','unknown-getter','global-mutation','post-read-change','revision','units','incomplete'])
def test_actual_fault_fires_and_never_exempts_revision(tmp_path,monkeypatch,fault):
    out,g=setup(tmp_path,monkeypatch);daemon,service,ref,project,sdk,tree,nodes,args=out;fired=False
    def inject(body):
        nonlocal fired
        method=body.get('method');av=body.get('args',[]);node=sdk.objects.get(body.get('handle'));is_child=nodes and node is getattr(nodes[-1],'child',None)
        targets={'global-child':'selection','wrong-parent':'selection','child-generation':'selection','ignored-geom':'geom','ignored-named':'named','wrong-members':'entities','wrong-dim':'dim','missing-getter':'isInheriting','unknown-getter':'isInheriting','global-mutation':'getData','post-read-change':'getData','revision':'getData','units':'getStringArray','incomplete':'getData'}
        if fired or method!=targets[fault]:return None
        if fault in {'global-child','wrong-parent','child-generation'} and av:return None
        if fault in {'ignored-geom','ignored-named','wrong-members','wrong-dim','missing-getter','unknown-getter'} and not is_child:return None
        if fault in {'ignored-geom','ignored-named'} and not av:return None
        if fault=='units' and av!=['unit']:return None
        fired=True
        if fault in {'global-child','wrong-parent','child-generation'}:
            value=sdk.value(g if fault=='global-child' else nodes[-1].child)
            if fault=='child-generation':value={**value,'generation':2}
            if fault=='wrong-parent':sdk.scope.feature='foreign-parent'
            return {'ok':True,'status':'SUCCEEDED','type':body['type'],'request_id':body['request_id'],'result':value}
        if fault in {'ignored-geom','ignored-named'}:
            if fault=='ignored-geom':node.g='foreign-geom'
            return {'ok':True,'status':'SUCCEEDED','type':body['type'],'request_id':body['request_id'],'result':sdk.value(node)}
        if fault in {'wrong-members','wrong-dim'}:
            return {'ok':True,'status':'SUCCEEDED','type':body['type'],'request_id':body['request_id'],'result':[2] if fault=='wrong-members' else 2}
        if fault in {'missing-getter','unknown-getter'}:
            x=_failed(body)
            if fault=='unknown-getter':x['failure']['execution_state_unknown']=True
            return x
        if fault=='global-mutation':
            from comsol_mcp._java_worker import RemoteJava
            h=sdk.value(g);RemoteJava(daemon.backend.worker,h['$worker_handle'],1)._call('set',[2])
        if fault=='post-read-change':nodes[-1].child.ids=[2]
        if fault=='revision':sdk.counter+=1
        if fault=='units':return _failed(body)
        if fault=='incomplete':return {'ok':False,'type':body['type'],'request_id':body['request_id']}
        return None
    sdk.inject=inject
    try:
        response=_call(daemon,ref,project,args,'selection-'+fault,0);assert fired,fault
        capture(fault,sdk,response,service.ledger._models['Model'].revision)
        assert not response['success'] or service.ledger._models['Model'].revision>0,response
        if fault=='global-child':assert not [c for c in g.calls if c[0] in {'geom','named','set'} and c[1]]
    finally:daemon.close()

def test_exact_plane_guard_accepts14_and_rejects_cross_plane_no_native():
    for port,x in [('1',-10.),('2',8.)]:
        expr=runner.plane_field_expressions(port);assert len(expr)==14 and len(set(expr))==14
        a={'spec':{'expressions':list(expr),'units':list(runner.plane_field_units(port)),'solution':{'dataset':'dset1','solution':'sol1'},'complex_mode':'preserve','selection':runner.plane_selection(port),'entity_dim':1},'points':runner._public_points(runner._field_points(x,321)),'coordinate_unit':'um','frame':'spatial'}
        assert runner._allowed_saved_point_read(a)
        a['spec']['selection']=runner.plane_selection('2' if port=='1' else '1');assert not runner._allowed_saved_point_read(a)
        assert all(e.startswith('down(emw.') for e in expr[:6]);assert all(not e.startswith('down') for e in expr[6:])


def install_boundary_selection_fixture(tree,monkeypatch):
    globals_={tag:Selection(global_=True) for tag in ['selInputPort','selReceiverX8']}
    globals_['selReceiverX8'].n='selReceiverX8';globals_['selReceiverX8'].ids=[8,10,12]
    tree.components.items['comp1'].selection=lambda tag:globals_[tag]
    factory=tree.numerical.factory
    def create(tag,kind):
        node=factory(tag,kind);node.child=Selection();node.selection=lambda:node.child;return node
    tree.numerical.factory=create
    return globals_


def test_actual021_rows_prove_aliases_scopes_and_selected_domain_side_not_values():
    import hashlib,collections
    p=Path(__file__).parent/'fixtures/w23_activity021/ACTUAL021_DEFINITION_BOUNDARY_RAW.json'
    assert hashlib.sha256(p.read_bytes()).hexdigest()=='ab6bbea6828ae36fd592bd4476227c857b3549904f8bb717d8175fb4cbf45565'
    r=json.loads(p.read_text())['readback'];assert r['physics_tag']=='ewfd' and r['physics_scope_raw']=='root.comp1.emw'
    allrows=[row for table in r['definition_tables'] for row in table['raw_rows'] if row]
    for port,boundarytext in [('1','边界 1, 3, 5'),('2','边界 8, 10, 12')]:
        for e in runner.plane_field_expressions(port):
            alias=e[5:-1] if e.startswith('down(') else e
            hits=[row for row in allrows if row[1]==alias];assert hits,alias
            if 'mode' in alias:assert all(row[5]==boundarytext for row in hits)
    for name,expression in [('emw.Ex','0'),('emw.Ey','0'),('emw.Ez','Ez')]:
        assert all(row[2]==expression for row in allrows if row[1]==name)
    for p in ['1','2']:
        for name in runner.protocol.bma_point_expressions(p):assert any(row[1]==name and row[5]=='全局' for row in allrows)
    domains={}
    for selection in r['named_selections']:
        if selection['dimension_raw']==2:
            for row in selection['adjacency_and_edge_samples_raw']:
                edges=tuple(row['adjacent_entities_raw']);d=row['entity'];assert d not in domains or domains[d]==edges;domains[d]=edges
    assert set(domains)==set(range(1,7))
    inverse={b:tuple(sorted(d for d,edges in domains.items() if b in edges)) for b in range(1,18)}
    columns=list(zip(*r['boundary_up_down_raw']))
    assert collections.Counter(inverse.values())==collections.Counter(tuple(sorted(d for d in c if d)) for c in columns)
    for b,physical,pml in [(1,1,0),(3,2,0),(5,3,0),(8,1,4),(10,2,5),(12,3,6)]:
        compatible=[c for c in columns if tuple(sorted(d for d in c if d))==inverse[b]]
        assert compatible and all(c==(pml,physical) for c in compatible)
    assert len([c for c in columns if tuple(sorted(d for d in c if d))==inverse[1]])==2 # deliberately not fake all17unique index proof

@pytest.mark.parametrize('fault',['missing','wrong-parent-geometry','wrong-dimension','wrong-name','wrong-membership','unknown-inheriting','inheriting','after-change','wrong-stage','wrong-component'])
def test_original_selection_consumer_rejects_invalid_actual_shape_before_science(tmp_path,monkeypatch,fault):
    out,g=setup(tmp_path,monkeypatch);daemon,service,ref,project,sdk,tree,nodes,args=out
    try:
        response=_call(daemon,ref,project,args,'selection-consumer',0);assert response['success']
        result=copy.deepcopy(response['data']);sel=runner.plane_selection('1')
        runner.protocol._require_point_selection_application(result,sel)
        application=result['feature_readback']['selection_application']
        if fault=='missing':application.pop('after')
        elif fault=='wrong-stage':application['before']['stage']='after_numeric_read'
        elif fault=='wrong-component':application['before']['component']='compOther'
        else:
            ob=application['after' if fault=='after-change' else 'before']['native_selection_readback']
            field,value={'wrong-parent-geometry':('geometry','geom2'),'wrong-dimension':('dimension',True),'wrong-name':('named','selReceiverX8'),'wrong-membership':('entities',[8,10,12]),'unknown-inheriting':('is_inheriting',None),'inheriting':('is_inheriting',True),'after-change':('entities',[2])}[fault];ob[field]=value
        with pytest.raises(runner.protocol.ScienceProtocolError):runner.protocol._require_point_selection_application(result,sel)
    finally:daemon.close()
