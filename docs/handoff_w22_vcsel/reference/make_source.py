#!/usr/bin/env python3
"""Deterministic synthetic W22 test source, not measured VCSEL optics or COMSOL.
Analytic reference: isotropic normalized 2-D Gaussian per active emitter.
Only standard library; no network, COMSOL, model edits or user-file overwrites.
"""
from __future__ import annotations
import argparse,csv,hashlib,json,math
from pathlib import Path

def finite(x, name, *, nonnegative=False):
    if isinstance(x,bool) or not isinstance(x,(float,int)) or not math.isfinite(x):raise ValueError(name+' must be finite numeric')
    if nonnegative and x<0:raise ValueError(name+' must be nonnegative')
    return float(x)

def emitters(spec):
    result=[]
    for group in spec['rings']:
        radius=finite(group['radius_m'],'radius',nonnegative=True)
        count=group['count'];power=finite(group['power_W_each'],'power',nonnegative=True)
        if type(count) is not int or count<1:raise ValueError('positive integer emitter count required')
        disabled=group.get('disabled_indices',[])
        if any(type(i) is not int or i<0 or i>=count for i in disabled):raise ValueError('invalid disabled index')
        for i in range(count):
            angle=2*math.pi*i/count+float(group.get('phase_rad',0))
            result.append({'id':group['id']+'_'+str(i),'group':group['id'],'x_m':radius*math.cos(angle),
                           'y_m':radius*math.sin(angle),'power_W':0.0 if i in disabled else power,
                           'enabled':i not in disabled})
    return result

def sigma(spec,L):
    L=finite(L,'L',nonnegative=True)
    value=finite(spec['kernel']['sigma0_m'],'sigma0',nonnegative=True)+finite(spec['kernel']['slope'],'slope',nonnegative=True)*L
    if value<=0:raise ValueError('sigma must be positive')
    return value

def irradiance(rows,s,x,y):
    if s<=0 or not math.isfinite(s):raise ValueError('sigma must be positive finite')
    denom=2*math.pi*s*s
    return math.fsum(r['power_W']/denom*math.exp(-((x-r['x_m'])**2+(y-r['y_m'])**2)/(2*s*s)) for r in rows)

def rectangle_power(rows,s,xlo,xhi,ylo,yhi):
    if s<=0 or xhi<=xlo or yhi<=ylo:raise ValueError('invalid integration region or sigma')
    norm=math.sqrt(2)*s
    return math.fsum(r['power_W']*.25*(math.erf((xhi-r['x_m'])/norm)-math.erf((xlo-r['x_m'])/norm))*(math.erf((yhi-r['y_m'])/norm)-math.erf((ylo-r['y_m'])/norm)) for r in rows)

def build(spec_path,dest,L):
    spec=json.loads(spec_path.read_text(encoding='utf-8'));rows=emitters(spec);s=sigma(spec,L)
    alpha=finite(spec['absorption_fraction'],'alpha',nonnegative=True)
    if alpha>1:raise ValueError('alpha cannot exceed 1')
    n=spec['grid']['points_per_axis'];half=finite(spec['grid']['half_width_m'],'grid half width',nonnegative=True)
    if type(n) is not int or not 3<=n<=1001 or half<=0:raise ValueError('grid range invalid')
    if dest.exists():raise ValueError('refuse existing destination; choose a new directory')
    dest.mkdir(parents=True)
    axis=[-half+2*half*i/(n-1) for i in range(n)];delta=axis[1]-axis[0]
    integral=0.0
    with (dest/'source.csv').open('x',encoding='utf-8',newline='') as f:
        out=csv.writer(f);out.writerow(['x_m','y_m','incident_W_m2','absorbed_W_m2'])
        for j,y in enumerate(axis):
            for i,x in enumerate(axis):
                val=irradiance(rows,s,x,y);out.writerow([format(x,'.17g'),format(y,'.17g'),format(val,'.17g'),format(alpha*val,'.17g')])
                integral+=val*(.5 if i in (0,n-1) else 1)*(.5 if j in (0,n-1) else 1)*delta*delta
    emitted=math.fsum(r['power_W'] for r in rows)
    exact=rectangle_power(rows,s,-half,half,-half,half)
    angles={str(x)+','+str(y):{'incident_W_m2':irradiance(rows,s,x,y),'absorbed_W_m2':alpha*irradiance(rows,s,x,y)} for x,y in ((.012,0),(-.012,0),(0,.012),(0,-.012))}
    report={'origin':'SYNTHETIC_ANALYTICAL_INPUT_NOT_ENGINE_RESULT','kernel':'normalized_2D_Gaussian',
            'L_m':L,'sigma_m':s,'absorption_fraction':alpha,'nominal_emitters':len(rows),'active_emitters':sum(r['enabled'] for r in rows),
            'emitted_W':emitted,'full_plane_absorbed_W_upper_bound':alpha*emitted,
            'rectangle_incident_W_analytic':exact,'rectangle_incident_W_grid_trapezoid':integral,
            'relative_quadrature_error':abs(exact-integral)/max(exact,1e-100),
            'same_radius_reference_points':angles,'extrapolation_policy':'explicit_zero_outside_table; never renormalize_clipped_power',
            'fixture_sha256':hashlib.sha256(spec_path.read_bytes()).hexdigest(),
            'source_sha256':hashlib.sha256((dest/'source.csv').read_bytes()).hexdigest(),
            'generator_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'comsol_executed':False,'physical_calibration':'UNVERIFIED'}
    (dest/'emitters.json').write_text(json.dumps(rows,indent=2,allow_nan=False)+'\n')
    (dest/'reference_values.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    return report

if __name__=='__main__':
    a=argparse.ArgumentParser(description=__doc__);a.add_argument('--spec',type=Path,default=Path(__file__).with_name('fixture.json'));a.add_argument('--out',type=Path,required=True);a.add_argument('--L-m',type=float,default=.02);n=a.parse_args()
    print(json.dumps(build(n.spec,n.out,n.L_m),ensure_ascii=False,indent=2,allow_nan=False))
