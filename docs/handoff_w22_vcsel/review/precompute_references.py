"""Precompute frozen candidate references independently of native results.

Optional --source-root must be an actual delivered source/ directory. It is
read only. Each group's 1/m² data is multiplied by candidate W and alpha once.
"""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import numpy as np
import spectral_reference

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
ap=argparse.ArgumentParser()
ap.add_argument('--spec',type=Path,default=Path(__file__).parent.parent/'benchmark_spec.json')
ap.add_argument('--source-root',type=Path)
ap.add_argument('--out',type=Path,required=True)
a=ap.parse_args()
spec=json.loads(a.spec.read_text());fixture=spec['fixture']
manifest=json.loads((a.source_root/'manifest.json').read_text()) if a.source_root else None
if manifest:
    assert manifest['spec']==fixture,'actual source spec differs from frozen fixture'
a.out.parent.mkdir(parents=True,exist_ok=True)
result={'kind':'INDEPENDENT_REFERENCE_CACHE_NOT_NATIVE',
        'benchmark_sha256':digest(a.spec),'spectral_script_sha256':digest(spectral_reference.__file__),
        'precompute_script_sha256':digest(__file__),'source_manifest_sha256':digest(a.source_root/'manifest.json') if manifest else None,
        'numpy_version':np.__version__,'candidates':[],'physical_validation':'UNVERIFIED'}
for i,(L,p0,p1) in enumerate(itertools.product(spec['search']['L_m'],spec['search']['p_center_W'],spec['search']['p_ring1_W']),1):
    combined=None;input_hashes={};p2=(12.45-p0-6*p1)/11
    if manifest:
        sources=sorted([s for s in manifest['sources'] if s['L_m']==L],key=lambda s:s['group_index'])
        assert len(sources)==3
        xy=None;incident=None
        for s,p in zip(sources,[p0,p1,p2]):
            path=a.source_root/s['file'];sha=digest(path)
            assert sha==s['sha256'],'source hash mismatch'
            input_hashes[s['file']]=sha
            data=np.loadtxt(path,comments='%')
            if xy is None:xy=data[:,:2];incident=np.zeros(len(data))
            else:assert np.array_equal(xy,data[:,:2]),'basis coordinates differ'
            incident+=p*data[:,2]
        combined=a.out.parent/f'candidate_{i:02d}_combined.csv'
        np.savetxt(combined,np.column_stack([xy,incident,.6*incident]),delimiter=',',header='x_m,y_m,incident_W_m2,absorbed_W_m2',comments='',fmt='%.17g')
    common=dict(p_center=p0,p_ring1=p1,source=combined)
    coarse=spectral_reference.calculate(fixture,L,**common)
    fine=spectral_reference.calculate(fixture,L,**common,modes=64,depth_modes=96,quadrature=256,roi_radial=64,roi_angular=256)
    keys=['roi_mean_deltaT_K','roi_std_deltaT_K','roi_absorbed_W','workpiece_absorbed_W']
    errors={k:abs(fine[k]-coarse[k])/max(abs(fine[k]),1e-9) for k in keys}
    result['candidates'].append({'parameters':{'L':L,'p0':p0,'p1':p1},'input_hashes':input_hashes,
        'coarse':coarse,'fine':fine,'convergence_relative':errors,
        'converged':all(v<=spec['tolerances']['reference_convergence_relative'] for v in errors.values())})
    a.out.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(i,'converged',result['candidates'][-1]['converged'],flush=True)
assert all(c['converged'] for c in result['candidates'])
