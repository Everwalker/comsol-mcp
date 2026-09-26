"""Independently check Reviewer native candidate plus new-worker reopened model."""
import argparse, hashlib, json, math
from pathlib import Path

ap=argparse.ArgumentParser()
ap.add_argument('--fresh',type=Path,required=True);ap.add_argument('--reopen',type=Path,required=True)
ap.add_argument('--full',type=Path,required=True);ap.add_argument('--out',type=Path,required=True)
ap.add_argument('--wheel-sha256',required=True,help='Explicit final candidate wheel identity supplied before independent run.')
a=ap.parse_args();review=Path(__file__).parent
fresh=json.loads((a.fresh/'summary.json').read_text());reopen=json.loads((a.reopen/'summary.json').read_text());full=json.loads((a.full/'summary.json').read_text())
spec=json.loads((review.parent/'benchmark_spec.json').read_text());tol=spec['tolerances'];checks=[]
def flag(name,value,detail=None):checks.append(dict(name=name,passed=bool(value),detail=detail))
def compare(name,x,y,rt,at):flag(name,math.isfinite(x) and abs(x-y)<=max(at,rt*abs(y)),dict(actual=x,reference=y,error=abs(x-y),limit=max(at,rt*abs(y))))
def flatten(x):
    if isinstance(x,list):
        for z in x:yield from flatten(z)
    else:yield x
flag('fresh_terminal',fresh['status']=='NATIVE_SMOKE_COMPLETED')
flag('reopen_terminal',reopen['status']=='FRESH_WORKER_REOPEN_COMPLETED' and reopen['solve_count']==0)
flag('one_fresh_solved_candidate',len(fresh['cases'])==1 and not fresh['cases'][0]['cache_hit'])
c=fresh['cases'][0]
best=min((r for r in full['cases'] if r['roi_mean']>=spec['search']['mean_deltaT_min_K']),key=lambda r:r['roi_std']/r['roi_mean'])
flag('returned_best_parameters',c['parameters']==best['parameters'])
for field in ['roi_mean','roi_std','roi_max','roi_min']:
    compare('independent_fresh:'+field,c[field],best[field],tol['fresh_relative'],tol['fresh_absolute_K'])
for kind in ['analytic','interpolated']:
    cache=json.loads((review/'reference_cache'/f'{kind}.json').read_text())
    ref=next(row['fine'] for row in cache['candidates'] if row['parameters']==c['parameters'])
    for f,r,rt,at in [('roi_mean','roi_mean_deltaT_K','reference_mean_relative','reference_mean_absolute_K'),('roi_std','roi_std_deltaT_K','reference_std_relative','reference_std_absolute_K'),('Pabs','workpiece_absorbed_W','source_integral_relative','source_integral_absolute_W'),('Proi','roi_absorbed_W','source_integral_relative','source_integral_absolute_W')]:
        compare(kind+':'+f,c[f],ref[r],tol[rt],tol[at])
compare('energy_balance',(c['Pabs']-c['Pout']-c['Pstore'])/c['Pabs'],0,0,tol['energy_balance_relative'])
flag('stored_mph_hash',fresh['saved_sha256']==reopen['saved_sha256'])
expected=fresh['final_sample']['data']
for which in ['stored_before_rebind','stored_after_rebind']:
    actual=reopen[which]['data'];flag(which+':binding',actual['dataset_binding']['binding_complete'] and actual['solution']=='sol1' and actual['dataset']=='dset1')
    flag(which+':expressions',actual['expressions']==expected['expressions'])
    for i,expr in enumerate(expected['expressions']):
        av=list(flatten(actual['values'][i]));ev=list(flatten(expected['values'][i]))
        flag(which+':'+expr,len(av)==len(ev) and all(math.isclose(x,y,rel_tol=1e-10,abs_tol=1e-12) for x,y in zip(av,ev)),{'maximum_difference':max(abs(x-y) for x,y in zip(av,ev))})
readback=reopen['settings']['data']['readback']['readback'];s=readback['settings']
expected_settings={'block_size_m':[.04,.04,.001],'block_position_m':[-.02,-.02,0.], 'roi_radius_m':.015,
 'thermalconductivity':['20[W/(m*K)]'],'density':['3000[kg/m^3]'],'heatcapacity':['700[J/(kg*K)]'],
 'initial_T':'Tamb','top_q0':'qabs','bottom_q0':'500[W/(m^2*K)]*(Tamb-T)','tlist':'0 1 5 20 60',
 'roi_mean_definition':'iroi(T-Tamb)/iroi(1)','roi_std_definition':'sqrt(iroi((T-Tamb-roi_mean)^2)/iroi(1))','dataset_solution':'sol1'}
for key,value in expected_settings.items():flag('readback_setting:'+key,s.get(key)==value,s.get(key))
flag('readback_roi_selection',s['roi_integration_selection']==readback['roi_box'] and len(readback['roi_box'])>0)
for key,value in dict(c['parameters'],p2=(12.45-c['parameters']['p0']-6*c['parameters']['p1'])/11,ptotal=12.45,alpha=.6,Tamb=300.).items():
    compare('readback_parameter:'+key,s['parameter_values'][key]['value'],value,0,1e-12)
for f in s['input_functions']:
    flag('function:'+f['tag'],f['argument_units']==['m','m'] and f['function_units']==['1/m^2'] and f['interpolation']=='linear' and f['extrapolation']=='value' and f['extrapolation_value']==0,f)
    flag('rebound_new_path:'+f['tag'],a.reopen.name in f['filename'])
flag('all_nine_functions',len(s['input_functions'])==9)
for folder in [a.fresh,a.reopen]:
    origin=json.loads((folder/'package_origin.json').read_text())
    flag(folder.name+':final_wheel',origin['wheel_sha256']==a.wheel_sha256)
    flag(folder.name+':frozen_benchmark',origin['benchmark_sha256']==hashlib.sha256((review.parent/'benchmark_spec.json').read_bytes()).hexdigest())
result={'result':'PASS' if all(c['passed'] for c in checks) else 'CHANGES_REQUIRED','scope':'REVIEWER_FRESH_CANDIDATE_AND_NEW_PATH_REOPEN','checks':checks,'engine':c['engine'],'parameters':c['parameters'],'physical_validation':'UNVERIFIED'}
a.out.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
print(json.dumps({'result':result['result'],'checks':len(checks),'failures':[c for c in checks if not c['passed']]},indent=2))
