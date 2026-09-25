import json,math,hashlib
from pathlib import Path
root=Path(__file__).resolve().parents[3]
output={}
for version in ('63','64'):
    p=root/'evidence/w21_closure/windows'/('reviewer_t11_final_'+version)
    s=json.loads((p/'summary.json').read_text());assert s['status']=='SMOKE_COMPLETED'
    package=json.loads((p/'package_origin.json').read_text())
    assert package['wheel_sha256']=='8511dbc550792fbe538646897c951649a49de668030e66bd9306868801b94e5d'
    assert package['runner_sha256']==hashlib.sha256((p/'runner_source.py').read_bytes()).hexdigest()
    result={'engine':s['engine'],'modes':{}}
    for mode in ('mesh','time'):
        rows=[]
        for case,reg in zip(s['refinement'][mode],s['refinement'][mode+'_convergence']['data']['registered_cases']):
            field=case['sample']['data']['values'][0][0]
            errors=[abs(field[it][ix]-(300+10*math.sin(math.pi*x)*math.exp(-math.pi**2*t))) for it,t in enumerate([0,.01,.03,.1]) if it for ix,x in enumerate([.25,.5,.75])]
            measured=max(errors)
            assert abs(measured-case['error'])<1e-12 and abs(measured-reg['error'])<1e-12 and measured<=.1
            assert reg['producer'] and reg['observation_ref']
            rows.append({k:reg[k] for k in ('elements','dofs','tolerance','tlist') }|{'recomputed_max_error_K':measured})
        assert len(set((r['elements'],r['dofs'],r['tolerance']) for r in rows))==3
        negatives={}
        for name in ('forged_arrays','repeated_ref','error_override','settings_override','missing_id','tampered_hash','repeated_settings'):
            n=s['refinement'][mode+'_'+name]
            assert n['data']['numerical_verification_status']!='PASS' and n['execution']['dirty'] is False and n['data']['physical_validation_status']=='UNVERIFIED'
            negatives[name]=n['data']['scope']
        assert s['refinement'][mode+'_post_negative_recovery']['data']['numerical_verification_status']=='PASS'
        result['modes'][mode]={'levels':rows,'negative_scopes':negatives,'post_negative_recovery':'PASS'}
    output[version]=result
out=Path(__file__).with_name('REVIEWER_T11_FINAL_RECOMPUTE.json');out.write_text(json.dumps(output,indent=2));print(json.dumps(output,indent=2))
