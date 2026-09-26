"""Independent numerical review of raw W22 MCP transcript and finite search.

Imports independent spectral_reference only, never production domain helpers.
Does not start COMSOL and cannot substitute for Reviewer fresh native runs.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import spectral_reference
from spectral_reference import calculate

ap=argparse.ArgumentParser()
ap.add_argument('--run',type=Path,required=True)
ap.add_argument('--spec',type=Path,default=Path(__file__).parent.parent/'benchmark_spec.json')
ap.add_argument('--out',type=Path,required=True)
ap.add_argument('--cases-only',action='store_true',help='Review completed search cases while explicitly retaining downstream failure.')
ap.add_argument('--controls-run',type=Path,help='Append separately recorded corrected controls; never rewrite original run status.')
args=ap.parse_args()
summary=json.loads((args.run/'summary.json').read_text())
original_run_status=summary['status']
control_baseline=summary['cases'][-1]
if args.controls_run:
    controls=json.loads((args.controls_run/'summary.json').read_text())
    control_baseline=controls['cases'][-1]
    summary.update({k:v for k,v in controls.items() if k!='cases'})
spec=json.loads(args.spec.read_text());tol=spec['tolerances'];checks=[]
cached={}
for kind in ['analytic','interpolated']:
    path=Path(__file__).parent/'reference_cache'/f'{kind}.json'
    if path.exists():
        cache=json.loads(path.read_text())
        assert cache['benchmark_sha256']==hashlib.sha256(args.spec.read_bytes()).hexdigest(),'reference cache spec mismatch'
        assert cache['spectral_script_sha256']==hashlib.sha256(Path(spectral_reference.__file__).read_bytes()).hexdigest(),'reference cache algorithm mismatch'
        assert all(c['converged'] for c in cache['candidates']),'reference cache unconverged'
        cached[kind]={tuple(c['parameters'][k] for k in ['L','p0','p1']):c for c in cache['candidates']}
def flag(name,passed,detail=None):
    checks.append({'name':name,'passed':bool(passed),'detail':detail})
def compare(name,a,b,rel,absolute):
    error=abs(a-b);limit=max(absolute,abs(b)*rel)
    flag(name,math.isfinite(a) and error<=limit,{'actual':a,'reference':b,'error':error,'limit':limit})
responses=[]
transcript_paths=[args.run/'transcript.jsonl']+([args.controls_run/'transcript.jsonl'] if args.controls_run else [])
for line in '\n'.join(p.read_text() for p in transcript_paths).splitlines():
    row=json.loads(line)
    if row['direction']=='response':
        payload=row['result'].get('structuredContent') or json.loads(next(c['text'] for c in row['result']['content'] if c['type']=='text'))
        responses.append((row['tool'],payload))
rawcases=[case for tool,p in responses if tool=='study.sweep_manage' for case in p.get('data',{}).get('cases',[])]
cases=summary['cases'];expected={(L,p0,p1) for L in spec['search']['L_m'] for p0 in spec['search']['p_center_W'] for p1 in spec['search']['p_ring1_W']}
actual={(c['parameters']['L'],c['parameters']['p0'],c['parameters']['p1']) for c in cases}
flag('all_27_frozen_candidates',len(cases)==27 and actual==expected)
expected_units={'roi_mean':'K','roi_std':'K','roi_max':'K','roi_min':'K','roi_area':'m^2','Pinc':'W','Pabs':'W','Proi':'W','Pout':'W','Pstore':'W','U':'J'}
reference_results=[]
for i,c in enumerate(cases):
    label=f'case_{i+1}'
    p=c['parameters'];pc,pr=p['p0'],p['p1'];po=(12.45-pc-6*pr)/11
    matching=[r for r in rawcases if r['producer']==c['producer']]
    flag(label+':raw_producer_present',bool(matching))
    if matching:
        raw=matching[0]
        for key in expected_units:
            compare(label+':raw_summary:'+key,c[key],raw[key],0,0)
    flag(label+':power_bounds',all(0<=v<=1 for v in [pc,pr,po]))
    compare(label+':total_power',pc+6*pr+11*po,12.45,0,1e-12)
    flag(label+':native_complete',c['status']=='COMPLETED' and c['cache_hit'] is False)
    flag(label+':binding',c['sample']['dataset_binding']['binding_complete'] and c['sample']['solution']=='sol1' and c['sample']['dataset']=='dset1')
    flag(label+':times',c['solution_indices']['time_values']==spec['times_s'])
    units=c['sample']['field_array']['units']['expression']
    flag(label+':units',all(units.get(k)==v for k,v in expected_units.items()),units)
    compare(label+':area',c['roi_area'],math.pi*.015**2,0,1e-7)
    compare(label+':absorption_once',c['Pabs'],.6*c['Pinc'],0,1e-8)
    compare(label+':balance',(c['Pabs']-c['Pout']-c['Pstore'])/c['Pabs'],0,0,tol['energy_balance_relative'])
    cachekey=(p['L'],pc,pr)
    ref=cached['analytic'][cachekey]['fine'] if 'analytic' in cached else calculate(spec['fixture'],p['L'],p_center=pc,p_ring1=pr,modes=64,depth_modes=96,quadrature=256,roi_radial=64,roi_angular=256)
    reference_results.append(ref)
    for key,rkey,relative,absolute in [('Pabs','workpiece_absorbed_W','source_integral_relative','source_integral_absolute_W'),('Proi','roi_absorbed_W','source_integral_relative','source_integral_absolute_W'),('roi_mean','roi_mean_deltaT_K','reference_mean_relative','reference_mean_absolute_K'),('roi_std','roi_std_deltaT_K','reference_std_relative','reference_std_absolute_K')]:
        compare(label+':independent:'+key,c[key],ref[rkey],tol[relative],tol[absolute])
        if 'interpolated' in cached:
            compare(label+':independent_interpolated:'+key,c[key],cached['interpolated'][cachekey]['fine'][rkey],tol[relative],tol[absolute])
    # Feasibility and CV are independently recomputed, not copied from labels.
    valid=c['roi_mean']>=spec['search']['mean_deltaT_min_K'] and c['roi_mean']>1e-9
    flag(label+':honest_heating_constraint',c.get('heating_feasible') is valid)
    compare(label+':cv',c['roi_cv'],c['roi_std']/c['roi_mean'],0,1e-12)
feasible=[c for c in cases if c['roi_mean']>=spec['search']['mean_deltaT_min_K'] and c['roi_mean']>1e-9]
if not feasible:
    raise RuntimeError('No feasible case: assess NO_FEASIBLE_FOUND branch separately; do not fabricate a best reference.')
best=min(feasible,key=lambda c:c['roi_std']/c['roi_mean'])
if not args.cases_only:
    flag('returned_best_is_discrete_best',summary['best_candidate']['parameters']==best['parameters'])
    flag('controls_terminal',summary['status'] in ['NATIVE_SMOKE_COMPLETED','SEARCH_COMPLETED'])
bp=best['parameters'];bi=next(i for i,c in enumerate(cases) if c['producer']==best['producer'])
coarse=cached['analytic'][(bp['L'],bp['p0'],bp['p1'])]['coarse'] if 'analytic' in cached else calculate(spec['fixture'],bp['L'],p_center=bp['p0'],p_ring1=bp['p1'])
for key in ['roi_mean_deltaT_K','roi_std_deltaT_K','roi_absorbed_W','workpiece_absorbed_W']:
    compare('best_reference_convergence:'+key,coarse[key],reference_results[bi][key],tol['reference_convergence_relative'],0)
for key,ref in [('roi_min','roi_sampled_min_deltaT_K'),('roi_max','roi_sampled_max_deltaT_K')]:
    compare('best_sampled_extrema:'+key,best[key],reference_results[bi][ref],tol['reference_extrema_relative'],tol['reference_extrema_absolute_K'])
if args.cases_only:
    result={'scope':'COMPLETED_27_CASES_NUMERICAL_REVIEW_ONLY','review_result':'PASS_CASES_ONLY' if all(c['passed'] for c in checks) else 'CHANGES_REQUIRED',
      'overall_native_run_status':summary['status'],'w22_scoped_approval':False,'native_reexecuted_by_reviewer':False,
      'benchmark_sha256':hashlib.sha256(args.spec.read_bytes()).hexdigest(),'transcript_sha256':hashlib.sha256((args.run/'transcript.jsonl').read_bytes()).hexdigest(),
      'checks':checks,'references':reference_results,'independently_selected_best_parameters':best['parameters'],
      'remaining':['cache repeat FAILURE remains unresolved','source mutation native negative control NOT_RUN','best native output/save/reopen NOT_RUN in this run'],
      'physical_validation':'UNVERIFIED'}
    args.out.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'result':result['review_result'],'checks':len(checks),'best':best['parameters'],'failures':[c for c in checks if not c['passed']]},indent=2))
    raise SystemExit(0 if all(c['passed'] for c in checks) else 1)
final=summary['final_sample']['data'];expressions=final['expressions']
flag('final_parameters',summary['final_parameters']==best['parameters'])
for name in expected_units:
    compare('final_fresh_matches_best:'+name,final['values'][expressions.index(name)][0][-1][0],best[name],tol['fresh_relative'],tol['fresh_absolute_K'] if expected_units[name]=='K' else 1e-8)
for key in ['source_probes','source_probes_mm']:
    probe=summary[key]['data'];p=best['parameters'];s=.002+.05*p['L'];pgroup=[p['p0'],p['p1'],(12.45-p['p0']-6*p['p1'])/11]
    for point,(x,y) in enumerate(spec['source_probes_m']):
        incident=0.
        for group,power in zip(spec['fixture']['rings'],pgroup):
            for j in range(group['count']):
                if j in group.get('disabled_indices',[]):continue
                angle=2*math.pi*j/group['count']+group.get('phase_rad',0.)
                dx=x-group['radius_m']*math.cos(angle);dy=y-group['radius_m']*math.sin(angle)
                incident+=power/(2*math.pi*s*s)*math.exp(-(dx*dx+dy*dy)/(2*s*s))
        for e,ref in enumerate([incident,.6*incident]):
            compare(key+f':angular_point_{point}_expr_{e}',probe['values'][e][0][-1][point],ref,tol['source_point_relative'],tol['source_point_absolute_W_m2'])
outside=summary['outside_probes']['data']['values']
def scalars(value):
    if isinstance(value,list):
        for child in value:yield from scalars(child)
    else:yield value
flag('zero_extrapolation',all(abs(x)<=1 for x in scalars(outside)))
repeat=summary['cache_repeat']['data'];changed=summary['changed_source_run']['data']
flag('same_input_cache',repeat['cache_hits']==1 and repeat['budget']['cases_evaluated']==0)
flag('changed_input_recomputed',changed['cache_hits']==0 and changed['budget']['cases_evaluated']==1)
flag('source_bytes_changed',summary['source_change']['before']!=summary['source_change']['after'])
flag('changed_input_physical_effect',abs(changed['cases'][0]['Pabs']-control_baseline['Pabs'])>1e-8)
control=summary['impossible_control'];bound=.6*18*1
flag('impossible_bound',control['status']=='PROVEN_INFEASIBLE' and abs(control['upper_bound_W']-bound)<1e-12 and control['target_W']>bound)
result={'scope':'INDEPENDENT_FULL_SEARCH_NUMERICAL_REVIEW','review_result':'PASS' if all(c['passed'] for c in checks) else 'CHANGES_REQUIRED','native_reexecuted_by_reviewer':False,'w22_scoped_approval':False,
        'original_run_status':original_run_status,'appended_controls_run':str(args.controls_run) if args.controls_run else None,
        'appended_controls_transcript_sha256':hashlib.sha256((args.controls_run/'transcript.jsonl').read_bytes()).hexdigest() if args.controls_run else None,
        'benchmark_sha256':hashlib.sha256(args.spec.read_bytes()).hexdigest(),'transcript_sha256':hashlib.sha256((args.run/'transcript.jsonl').read_bytes()).hexdigest(),
        'checks':checks,'references':reference_results,'best_parameters':best['parameters'],'physical_validation':'UNVERIFIED'}
args.out.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
print(json.dumps({'result':result['review_result'],'checks':len(checks),'failures':[c for c in checks if not c['passed']]},indent=2))
