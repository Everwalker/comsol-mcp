"""Read raw public MCP evidence and independently check the frozen baseline.
This is evidence review, not a fresh native solve or D1-D6 approval.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

ap=argparse.ArgumentParser()
ap.add_argument('--transcript',type=Path,required=True)
ap.add_argument('--review-dir',type=Path,default=Path(__file__).parent)
ap.add_argument('--out',type=Path,required=True)
a=ap.parse_args()
rows=[json.loads(line) for line in a.transcript.read_text().splitlines()]
responses=[]
for row in rows:
    if row['direction']=='response':
        data=json.loads(next(x['text'] for x in row['result']['content'] if x['type']=='text'))
        responses.append((row['tool'],data))
sweep=next(d['data'] for t,d in responses if t=='study.sweep_manage')
case=sweep['cases'][0]
spec=json.loads((a.review_dir.parent/'benchmark_spec.json').read_text())
tol=spec['tolerances']
checks=[]
def check(name,actual,expected,relative,absolute):
    error=abs(actual-expected);limit=max(absolute,abs(expected)*relative)
    checks.append(dict(name=name,actual=actual,expected=expected,error=error,limit=limit,pass_=math.isfinite(actual) and error<=limit))
mapping={'roi_mean':('roi_mean_deltaT_K','reference_mean_relative','reference_mean_absolute_K'),
         'roi_std':('roi_std_deltaT_K','reference_std_relative','reference_std_absolute_K'),
         'Pabs':('workpiece_absorbed_W','source_integral_relative','source_integral_absolute_W'),
         'Proi':('roi_absorbed_W','source_integral_relative','source_integral_absolute_W')}
for name in ['analytic','interpolated']:
    reference=json.loads((a.review_dir/f'reference_L20_base_{name}.json').read_text())
    assert reference['reference_converged']
    for key,(ref,rt,at) in mapping.items():
        check(name+':'+key,case[key],reference['fine'][ref],tol[rt],tol[at])
    # Spectral reference extrema remain finite sampled estimates, not exact extrema.
    for key,ref in [('roi_max','roi_sampled_max_deltaT_K'),('roi_min','roi_sampled_min_deltaT_K')]:
        check(name+':'+key+'_sampled_reference',case[key],reference['fine'][ref],tol['reference_extrema_relative'],tol['reference_extrema_absolute_K'])
check('roi_area',case['roi_area'],math.pi*.015**2,.0001,0)
check('absorption_once',case['Pabs'],.6*case['Pinc'],1e-10,1e-8)
check('energy_balance_relative',(case['Pabs']-case['Pout']-case['Pstore'])/case['Pabs'],0,0,tol['energy_balance_relative'])
probes=next(d['data'] for t,d in responses if t=='operation_call' and d.get('data',{}).get('expressions')==['incident','qabs'])
expected=[4568.244015010881,16062.767682758742,15432.407589064585,15432.407589064576]
for j,expression in enumerate(probes['expressions']):
    for p,value in enumerate(expected):
        check(f'source:{expression}:{p}',probes['values'][j][0][-1][p],value*(.6 if j else 1),tol['source_point_relative'],tol['source_point_absolute_W_m2'])
array=case['sample']['field_array']
for i,name in enumerate(case['sample']['expressions']):
    check('scalar_matches_raw_array:'+name,case[name],array['values'][i][0][-1][0],0,0)
metadata={
 'success_responses':all(d.get('success') is True for _,d in responses),
 'not_cache':case['cache_hit'] is False,
 'fresh_computation_timestamp':case['solve']['computation_timestamp_changed'] is True,
 'bound_solution':case['sample']['dataset']=='dset1' and case['sample']['solution']=='sol1' and case['sample']['dataset_binding']['binding_complete'],
 'actual_stored_times':case['solution_indices']['time_values']==spec['times_s'],
 'array_axes':array['axes']==['expression','outer','inner','point'],
 'nonzero_heating':case['roi_mean']>=spec['search']['mean_deltaT_min_K']}
result={'scope':'WINDOWS_BASELINE_SMOKE_EVIDENCE_REVIEW_ONLY',
 'native_reexecuted_by_reviewer':False,'w22_scoped_approval':False,
 'review_result':'PASS_LIMITED_SCOPE' if all(c['pass_'] for c in checks) and all(metadata.values()) else 'CHANGES_REQUIRED',
 'transcript_sha256':hashlib.sha256(a.transcript.read_bytes()).hexdigest(),
 'benchmark_sha256':hashlib.sha256((a.review_dir.parent/'benchmark_spec.json').read_bytes()).hexdigest(),
 'engine':case['engine'],'model_ref':case['model_ref'],'producer':case['producer'],
 'observation_ref':case['observation_ref'],'metadata':metadata,'checks':checks,
 'remaining':['independent fresh dual-version solve','L scan and optimization','source cache negative control','outside source support and m/mm control','native image and clean-path reopen','final candidate code review'],
 'notes':['Raw optional getPNames/getPvals/getUnits metadata calls failed for nonparametric solution; actual stored time and dataset binding are present. No broad old-stage re-audit implied.','At t=0 solver readback has small nonzero temperature rise; this review certifies only frozen t=60s comparison.','CV denominator guard must not expose zero-heating CV as a valid optimum.','Physical calibration remains UNVERIFIED.']}
a.out.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'review_result':result['review_result'],'checks':len(checks),'metadata':metadata,'failed':[c for c in checks if not c['pass_']]},indent=2))
