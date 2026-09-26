"""Independent rectangular slab reference; never a native COMSOL result.

Cosine modes in x,y (insulated sides), cos(mu*z/d) depth modes with
mu*tan(mu)=h*d/k (top homogeneous Neumann, bottom Robin). Boundary forcing
q(x,y) enters weak form at z=0. Initial temperature rise is zero.
Requires numpy/scipy. No production package imports.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import numpy as np
from numpy.polynomial.legendre import leggauss
from scipy.optimize import brentq
from scipy.interpolate import LinearNDInterpolator


def gauss(n, lo, hi):
    x, w = leggauss(n)
    return lo+(x+1)*(hi-lo)/2, w*(hi-lo)/2


def calculate(spec, L, *, p_center=None, p_ring1=None, source=None,
              modes=48, depth_modes=64, quadrature=192, roi_radial=48,
              roi_angular=192, time_s=60.):
    width, d, k, rho, cp, h, radius = .04, .001, 20., 3000., 700., 500., .015
    alpha = float(spec['absorption_fraction'])
    sigma = spec['kernel']['sigma0_m']+spec['kernel']['slope']*L
    emitters=[]
    pc=spec['rings'][0]['power_W_each'] if p_center is None else p_center
    pr=spec['rings'][1]['power_W_each'] if p_ring1 is None else p_ring1
    po=(12.45-pc-6*pr)/11
    for g,p in zip(spec['rings'],(pc,pr,po)):
        for i in range(g['count']):
            if i in g.get('disabled_indices',[]): continue
            angle=2*np.pi*i/g['count']+g.get('phase_rad',0.)
            emitters.append((g['radius_m']*np.cos(angle),g['radius_m']*np.sin(angle),p))
    axis,w=gauss(quadrature,-width/2,width/2)
    n=np.arange(modes)
    basis=np.cos(np.pi*np.outer(n,(axis+width/2)/width))
    norm=np.full(modes,width/2); norm[0]=width
    if source:
        rows=np.genfromtxt(source,delimiter=',',names=True)
        # Delaunay diagonal choices are implementation dependent on a square
        # lattice; retain analytic comparison rather than assert bit equivalence.
        interp=LinearNDInterpolator(np.column_stack([rows['x_m'],rows['y_m']]),rows['absorbed_W_m2'],fill_value=0.)
        xx,yy=np.meshgrid(axis,axis,indexing='ij')
        q=interp(np.stack([xx,yy],axis=-1))
        coeff=(basis*w)@q@(basis*w).T/np.outer(norm,norm)
    else:
        coeff=np.zeros((modes,modes))
        for x,y,p in emitters:
            bx=(basis*w)@np.exp(-.5*((axis-x)/sigma)**2)
            by=(basis*w)@np.exp(-.5*((axis-y)/sigma)**2)
            coeff+=alpha*p/(2*np.pi*sigma*sigma)*np.outer(bx,by)/np.outer(norm,norm)
    bi=h*d/k
    mu=np.array([brentq(lambda u:u*np.tan(u)-bi,j*np.pi+1e-12,j*np.pi+np.pi/2-1e-10) for j in range(depth_modes)])
    znorm=d*(.5+np.sin(2*mu)/(4*mu))
    lamxy=(np.pi/width)**2*(n[:,None]**2+n[None,:]**2)
    rates=k/(rho*cp)*(lamxy[:,:,None]+(mu/d)**2)
    response=-np.expm1(-rates*time_s)/(rho*cp*znorm*rates)
    top_coeff=coeff*response.sum(axis=2)
    bottom_coeff=coeff*(response*np.cos(mu)).sum(axis=2)
    rr,rw=gauss(roi_radial,0,radius)
    angles=np.arange(roi_angular)*2*np.pi/roi_angular
    px=(rr[:,None]*np.cos(angles)).ravel()
    py=(rr[:,None]*np.sin(angles)).ravel()
    weights=np.repeat(rw*rr*2*np.pi/roi_angular,roi_angular)
    bx=np.cos(np.pi*np.outer(n,(px+width/2)/width))
    by=np.cos(np.pi*np.outer(n,(py+width/2)/width))
    temp=np.sum(bx*(top_coeff@by),axis=0)
    area=np.pi*radius**2
    mean=np.dot(weights,temp)/area
    std=np.sqrt(np.dot(weights,(temp-mean)**2)/area)
    qroi=np.sum(bx*(coeff@by),axis=0)
    pin=coeff[0,0]*width**2
    pout=h*bottom_coeff[0,0]*width**2
    storage=coeff[0,0]*width**2*np.sum(np.exp(-rates[0,0]*time_s)*d*np.sin(mu)/mu/znorm)
    # Integral of each nonconstant x/y mode is exactly zero over the slab.
    energy=rho*cp*width**2*coeff[0,0]*np.sum(response[0,0]*d*np.sin(mu)/mu)
    return {'origin':'INDEPENDENT_SPECTRAL_REFERENCE_NOT_COMSOL',
        'time_s':time_s,'L_m':L,'per_emitter_W':[pc,pr,po],
        'source_kind':'csv_delaunay_piecewise_linear' if source else 'analytic_gaussian',
        'source_sha256':hashlib.sha256(Path(source).read_bytes()).hexdigest() if source else None,
        'resolution':dict(modes=modes,depth_modes=depth_modes,quadrature=quadrature,roi_radial=roi_radial,roi_angular=roi_angular),
        'roi_mean_deltaT_K':float(mean),'roi_std_deltaT_K':float(std),
        'roi_cv_deltaT':float(std/mean),'roi_mean_T_K':float(300+mean),
        'roi_sampled_min_deltaT_K':float(temp.min()),'roi_sampled_max_deltaT_K':float(temp.max()),
        'roi_absorbed_W':float(np.dot(weights,qroi)),
        'workpiece_absorbed_W':float(pin),'bottom_out_W':float(pout),
        'stored_energy_J':float(energy),'storage_rate_W':float(storage),
        'balance_residual_W':float(pin-pout-storage),
        'physical_calibration':'UNVERIFIED'}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--fixture',type=Path,required=True)
    ap.add_argument('--L-m',type=float,default=.02)
    ap.add_argument('--p-center',type=float);ap.add_argument('--p-ring1',type=float)
    ap.add_argument('--source',type=Path)
    ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args()
    spec=json.loads(args.fixture.read_text())
    common=dict(p_center=args.p_center,p_ring1=args.p_ring1,source=args.source)
    base=calculate(spec,args.L_m,**common)
    fine=calculate(spec,args.L_m,**common,modes=64,depth_modes=96,quadrature=256,roi_radial=64,roi_angular=256)
    fields=['roi_mean_deltaT_K','roi_std_deltaT_K','roi_absorbed_W','workpiece_absorbed_W']
    differences={key:abs(fine[key]-base[key])/max(abs(fine[key]),1e-9) for key in fields}
    payload={'base':base,'fine':fine,'convergence_relative':differences,
        'reference_converged':all(v<=.002 for v in differences.values()),
        'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    args.out.write_text(json.dumps(payload,indent=2,allow_nan=False)+'\n')
    print(json.dumps(payload,indent=2))


if __name__=='__main__': main()
