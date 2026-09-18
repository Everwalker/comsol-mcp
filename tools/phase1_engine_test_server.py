"""Test-only real JPype/MPh bridge; production tools are imported unchanged.
Only fixed fixtures and snapshots live here. Never register this in production.
"""
import os, sys, json, uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import comsol_mcp.mcp_server as entry
import comsol_mcp._server as srv
from comsol_mcp._phase1_runtime import client_classpath,DEFAULT_COMSOL_ROOT,DEFAULT_JDK11
from comsol_mcp._model import _set_current_model
from comsol_mcp._state import _write_workflow_state
from comsol_mcp._model_ops import _coerce_eval_value
from comsol_mcp._physics_ops import _list_variables
fixture={}
def connect_real(port,prefs):
 import jpype, mph
 cp,_,_=client_classpath(DEFAULT_COMSOL_ROOT)
 jpype.startJVM(str(DEFAULT_JDK11/'lib/server/libjvm.dylib'),f'-Dcs.prefsdir={prefs}',classpath=cp.split(os.pathsep),convertStrings=True)
 java=jpype.JClass('com.comsol.model.util.ModelUtil');java.connect('127.0.0.1',port)
 client=object.__new__(mph.Client);client.java=java;client.version='6.4';client.standalone=False;client.host='127.0.0.1';client.port=port
 srv._client=client;srv._client_connected=True;srv._connected_host='127.0.0.1';srv._connected_port=port
 return java,mph

@entry.mcp.tool()
def fixture_init(mph_path:str,port:int,prefs:str,run_directory:str)->dict:
 java,mph=connect_real(port,prefs)
 tag='w04_'+uuid.uuid4().hex[:12];jm=java.load(tag,mph_path);m=mph.Model(jm)
 jm.result().table().create('user_table','Table')
 jm.result().numerical().create('user_max','MaxSurface');n=jm.result().numerical('user_max');n.selection().set([1]);n.set('expr',['u']);n.set('table','user_table');n.setResult()
 jm.component('comp1').physics('c').create('test_flux','FluxBoundary',1)
 jm.component('comp1').selection().create('user_sel','Explicit');jm.component('comp1').selection('user_sel').geom('geom1',2);jm.component('comp1').selection('user_sel').set([1])
 other=java.create('w04_other_'+uuid.uuid4().hex[:12]);other.param().set('untouched','17')
 jm.save(str(Path(run_directory)/'before.mph'));other.save(str(Path(run_directory)/'other.mph'))
 fixture.update(model=m,other=str(other.tag()))
 _set_current_model(m,origin='test-fixture');_write_workflow_state({'model_dimension':2,'visible_main_locked':False})
 return {'tag':tag,'other':fixture['other'],'build':str(java.getComsolVersion()),'snapshot':fixture_snapshot()}
@entry.mcp.tool()
def fixture_snapshot()->dict:
 m=fixture['model'];j=m.java
 return {'models':sorted(map(str,srv._client.java.tags())), 'numerical':sorted(map(str,j.result().numerical().tags())), 'plots':sorted(map(str,j.result().tags())), 'tables':sorted(map(str,j.result().table().tags())), 'user_expr':list(j.result().numerical('user_max').getStringArray('expr')), 'user_table':str(j.result().numerical('user_max').getString('table')), 'table_data':_coerce_eval_value(j.result().table('user_table').getReal()),'variables':{'global':_list_variables(m),'component':_list_variables(m,'comp1')},'physics_features':{str(t):{'inheriting':bool(j.component('comp1').physics('c').feature(str(t)).selection().isInheriting())} for t in j.component('comp1').physics('c').feature().tags()},'other_value':str(srv._client.java.model(fixture['other']).param().get('untouched'))}
@entry.mcp.tool()
def fixture_save(path:str)->dict:
 fixture['model'].java.save(path);return {'path':path,'snapshot':fixture_snapshot()}
@entry.mcp.tool()
def fixture_reopen(path:str,port:int,prefs:str,other_tag:str)->dict:
 java,mph=connect_real(port,prefs)
 model=mph.Model(java.load(java.uniquetag('w04reopen'),path))
 fixture.update(model=model,other=other_tag)
 _set_current_model(model,origin='fresh-process-reopen');_write_workflow_state({'model_dimension':2,'visible_main_locked':False})
 return fixture_snapshot()

@entry.mcp.tool()
def fixture_disconnect()->dict:
 srv._client.java.disconnect();return {'disconnected':True}
if __name__=='__main__':entry.main()
