import copy
from tests.test_ephemeral_result_ticket import _public,_call
from comsol_mcp import _g3_results as results
from comsol_mcp._ephemeral_result_ticket import current_scope

def test_actual_public_no_explicit_units_keeps_conservative_revision(tmp_path,monkeypatch):
    daemon,service,ref,project,sdk,tree,nodes,args=_public(tmp_path,monkeypatch)
    try:
        partial=results._result_solution_binding(tree.model,'sol1');assert partial and partial['pair_mapping_complete'] is False
        generic=copy.deepcopy(args);generic['spec'].pop('units')
        response=_call(daemon,ref,project,generic,'no-explicit-units',0)
        assert response['success'] and response['execution']['revision']==1
        assert service.ledger._models['Model'].revision==1
        assert tree.numerical.items=={} and current_scope() is None
        before=len(sdk.calls);stale=_call(daemon,ref,project,args,'stale-after-generic',0)
        assert not stale['success'] and len(sdk.calls)==before
    finally:daemon.close()
