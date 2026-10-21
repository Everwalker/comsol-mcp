"""Real COMSOL safety regression through stdio MCP; no mock acceptance."""
import argparse,asyncio,json,os,sys,traceback,subprocess
from datetime import datetime,timezone,timedelta
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
ROOT=Path(__file__).resolve().parents[1]
async def main(args):
 identity=subprocess.check_output(["ps","-p",str(args.pid),"-o","pid=,lstart=,command="],text=True)
 listeners=subprocess.check_output(["lsof","-nP","-a","-p",str(args.pid),"-iTCP","-sTCP:LISTEN"],text=True)
 if "comsol" not in identity.lower() or f":{args.port} " not in listeners:raise RuntimeError("Registered server PID/port mismatch")
 run=ROOT/'evidence/w04/runs'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');run.mkdir(parents=True)
 env=dict(os.environ,COMSOL_ROOT='/Applications/COMSOL64/Multiphysics',COMSOL_SERVER_MCP_HOME=str(ROOT/'.phase1-private'/run.name))
 transcript=[];checks={}
 def save(name,obj):(run/name).write_text(json.dumps(obj,indent=2,ensure_ascii=False))
 save('environment.json',{'os':sys.platform,'python':sys.version,'server':f'127.0.0.1:{args.port}','server_identity':identity,'listeners':listeners,'engine':'real COMSOL 6.4 via JPype JDK11/MPh and production MCP tools'})
 save('request.json',{'cases':['T003','T005','T006','T007','T033'],'threshold':1e-8})
 with (run/'engine.log').open('w') as log:
  async with stdio_client(StdioServerParameters(command=sys.executable,args=[str(ROOT/'tools/phase1_engine_test_server.py')],env=env,cwd=str(ROOT)),errlog=log) as (r,w):
   async with ClientSession(r,w,read_timeout_seconds=timedelta(minutes=3)) as session:
    await session.initialize()
    async def call(name,args={}):
     response=await session.call_tool(name,args);transcript.append({'name':name,'arguments':args,'response':response.model_dump(mode='json')});save('transcript.json',transcript)
     if response.isError:raise RuntimeError(str(response.content))
     obj=json.loads(response.content[0].text)
     return obj
    def data(obj):
     if 'success' in obj:
      if not obj['success']:raise RuntimeError(obj.get('error',str(obj)))
      return obj['data']
     return obj
    try:
     init=await call('fixture_init',{'mph_path':str(ROOT/'evidence/w02/runs/20260918T110411819403Z/model.mph'),'port':args.port,'prefs':args.prefs,'run_directory':str(run)})
     before=init['snapshot'];save('before.json',before)
     adopted=data(await call('load_visible_main_model',{'path':str(run/'other.mph')}));after=await call('fixture_snapshot')
     checks['T003']=adopted['load_mode']=='visible-main-adopted' and before==after
     data(await call('load_visible_main_model',{'path':str(run/'before.mph')}))
     expr=[{'name':x,'expression':'u','aggregate':x,'domains':[1]} for x in ['max','min','avg','integral']]+[{'name':'invalid','expression':'nonexistent_w04_symbol','aggregate':'max','domains':[1]}]
     evaluated=data(await call('evaluate_expressions',{'expressions_json':json.dumps(expr)}));save('aggregates.json',evaluated)
     after=await call('fixture_snapshot');checks['T005']=before==after and all(x['ok'] and abs(x['value']-2)<1e-8 for x in evaluated['results'][:4]) and not evaluated['results'][4]['ok']
     for comp in ('','comp1'):
      for name,expression in [('aa','3'),('bb','aa+4'),('aa','5')]:
       data(await call('manage_variables',{'action':'set','component':comp,'tag':'user_vars_'+('component' if comp else 'global'),'name':name,'expression':expression}))
     data(await call('run_study',{'study_tag':'std1'}))
     var=data(await call('evaluate_expressions',{'expressions_json':json.dumps([{'name':'global','expression':'bb'},{'name':'component','expression':'comp1.bb'}])}));snap=await call('fixture_snapshot');save('variables.json',{'snapshot':snap,'evaluated':var})
     def flatten(v):
      if isinstance(v,list):return [x for row in v for x in flatten(row)]
      return [v]
     checks['T006']=all(x['ok'] and all(abs(v-9)<1e-8 for v in flatten(x['value'])) for x in var['results']) and all({v['name'] for v in rows[0]['variables']}=={'aa','bb'} for rows in snap['variables'].values())
     selections=[]
     for feature,selection in [('',[1]),('',{'kind':'named','name':'user_sel'}),('',{'kind':'all'}),('test_flux',[1]),('cfeq1',[1]),('test_flux',{'kind':'inherited'}),('test_flux',[1])]:
      selections.append(await call('set_physics_selection',{'component':'comp1','physics_tag':'c','feature_tag':feature,'entities_json':json.dumps(selection)}))
     save('selections.json',selections);checks['T007']=all(x['success'] for x in selections[:4]) and not selections[4]['success'] and ('inherit' in str(selections[4]).lower() or '不可编辑选择' in str(selections[4])) and not selections[5]['success'] and '不允许继承' in selections[5]['error'] and selections[6]['success']
     before_read=await call('fixture_snapshot')
     pure=await call('evaluate_expressions',{'expressions_json':'[{"name":"u","expression":"u"}]','evaluation_policy':'pure_read'})
     ephem=data(await call('evaluate_expressions',{'expressions_json':'[{"name":"field","expression":"u"}]'}));after_read=await call('fixture_snapshot')
     invalid_metrics=await call('run_visible_main_iteration',{'label':'invalid','parameters_json':'[{"name":"guard_probe","expression":"2"}]','metrics_json':'[{}]'})
     checks['T033']=not invalid_metrics['success'] and not pure['success'] and before_read==after_read and ephem['results'][0]['ok']
     await call('fixture_save',{'path':str(run/'after.mph')});save('after.json',after_read)
    except Exception:
     save('failure.json',{'traceback':traceback.format_exc()})
    finally:
     try:await call('fixture_disconnect')
     except Exception:pass
     save('assertions.json',checks);save('result.json',{'checks':checks,'pass':len(checks)==5 and all(checks.values())})
 if len(checks)==5 and all(checks.values()):
  with (run/'reopen_engine.log').open('w') as log:
   async with stdio_client(StdioServerParameters(command=sys.executable,args=[str(ROOT/'tools/phase1_engine_test_server.py')],env=env,cwd=str(ROOT)),errlog=log) as (r,w):
    async with ClientSession(r,w,read_timeout_seconds=timedelta(minutes=3)) as session:
     await session.initialize()
     reopened=await session.call_tool('fixture_reopen',{'path':str(run/'after.mph'),'port':args.port,'prefs':args.prefs,'other_tag':init['other']})
     save('fresh_reopen.json',reopened.model_dump(mode='json'))
     rs=json.loads(reopened.content[0].text)
     preserved=['numerical','plots','tables','user_expr','user_table','table_data','physics_features']
     user_variables=lambda snapshot:{scope:[row for row in groups if row['tag'] in ('user_vars_global','user_vars_component')] for scope,groups in snapshot['variables'].items()}
     checks['fresh_reopen']=not reopened.isError and all(rs[k]==after_read[k] for k in preserved) and user_variables(rs)==user_variables(after_read)
     numeric=await session.call_tool('evaluate_expressions',{'expressions_json':json.dumps([{'name':'variable','expression':'comp1.bb'},{'name':'error','expression':'abs(u-2)','aggregate':'max','domains':[1]}])})
     save('fresh_reopen_numeric.json',numeric.model_dump(mode='json'))
     nr=json.loads(numeric.content[0].text)
     checks['fresh_reopen']=checks['fresh_reopen'] and nr.get('success',False) and all(row['ok'] for row in nr.get('data',{}).get('results',[])) and abs(nr['data']['results'][0]['last_value']-9)<1e-8 and nr['data']['results'][1]['value']<1e-8
     await session.call_tool('fixture_disconnect')
     save('assertions.json',checks);save('result.json',{'checks':checks,'pass':all(checks.values())})
 print(json.dumps({'run':str(run),'checks':checks}));return 0 if len(checks)==6 and all(checks.values()) else 1
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--pid',type=int,required=True);parser.add_argument('--port',type=int,required=True);parser.add_argument('--prefs',required=True)
 raise SystemExit(asyncio.run(main(parser.parse_args())))
