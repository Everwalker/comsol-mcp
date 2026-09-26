"""Export auditable tables from real W22 runner results; never synthesize cases."""
import argparse,csv,hashlib,json
from pathlib import Path

def export(run):
    run=Path(run);summary=json.loads((run/'summary.json').read_text())
    spec=json.loads((run/'project/benchmark_spec.json').read_text());manifest=json.loads((run/'project/source/manifest.json').read_text())
    groups=spec['fixture']['rings'];counts=[g['count']-len(set(g.get('disabled_indices',[]))) for g in groups]
    if len(groups)!=3:raise ValueError('this table describes the explicit three-group frozen benchmark')
    records=[]
    for case in summary.get('cases',[]):
        if case.get('status')!='COMPLETED':continue
        p=case['parameters'];p2=(spec['search']['total_W']-counts[0]*p['p0']-counts[1]*p['p1'])/counts[2]
        row={'case':case.get('search_ordinal'),'producer':case['producer'],'L_m':p['L'],
             'p_center_W_each':p['p0'],'p_ring1_W_each':p['p1'],'p_ring2_W_each':p2,
             'emitted_W':counts[0]*p['p0']+counts[1]*p['p1']+counts[2]*p2,
             'incident_W':case['Pinc'],'absorbed_W':case['Pabs'],'ROI_absorbed_W':case['Proi'],
             'optical_not_intercepted_W':spec['search']['total_W']-case['Pinc'],
             'unabsorbed_incident_W':case['Pinc']-case['Pabs'],'bottom_heat_out_W':case['Pout'],
             'storage_rate_W':case['Pstore'],'balance_residual_W':case['Pabs']-case['Pout']-case['Pstore'],
             'ROI_mean_T_K':spec['thermal']['Tamb_K']+case['roi_mean'],'ROI_mean_rise_K':case['roi_mean'],
             'ROI_std_K':case['roi_std'],'ROI_min_rise_K':case['roi_min'],'ROI_max_rise_K':case['roi_max'],
             'ROI_CV':case.get('roi_cv'),'ROI_CV_status':case.get('roi_cv_status'),'ROI_area_m2':case['roi_area'],
             'dataset':case['sample']['dataset'],'solution':case['sample']['solution'],'time_s':spec['times_s'][-1],
             'native_version':case['engine']['comsol_version'],'physical_validation':'UNVERIFIED'}
        records.append(row)
    if not records:raise ValueError('no completed native cases')
    with (run/'case_table.csv').open('w',newline='') as out:
        writer=csv.DictWriter(out,fieldnames=list(records[0]));writer.writeheader();writer.writerows(records)
    best=summary.get('best_candidate');comparison={'status':'NO_VERIFIED_BEST_REPORTED' if not best else 'REPORTED_BEST_PENDING_INDEPENDENT_ACCEPTANCE'}
    if best:
        base=next((c for c in summary['cases'] if c['parameters']=={'L':.02,'p0':.8,'p1':.75}),None)
        same=next((c for c in summary['cases'] if c['parameters']=={'L':best['parameters']['L'],'p0':.8,'p1':.75}),None)
        comparison.update(parameters=best['parameters'],best_CV=best['roi_cv'],
            relative_CV_improvement_vs_L20_baseline=None if base is None else 1-best['roi_cv']/base['roi_cv'],
            relative_CV_improvement_vs_same_L_baseline=None if same is None else 1-best['roi_cv']/same['roi_cv'],
            optimality='BEST_VERIFIED_FEASIBLE_SO_FAR_NOT_GLOBAL',physical_validation='UNVERIFIED')
    comparison['raw_summary_sha256']=hashlib.sha256((run/'summary.json').read_bytes()).hexdigest()
    (run/'comparison.json').write_text(json.dumps(comparison,indent=2)+'\n')
    return {'rows':len(records),'comparison':comparison}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('run',type=Path);args=p.parse_args();print(json.dumps(export(args.run),indent=2))
