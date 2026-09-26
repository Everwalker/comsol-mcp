"""Source preparation software checks, not native/physical acceptance."""
import copy,json,math
from pathlib import Path
import pytest
from comsol_mcp._vcsel_source import prepare_basis, emitters, impossible_power_bound, irradiance, sigma

def fixture():
    return json.loads((Path(__file__).parents[1]/'docs/handoff_w22_vcsel/reference/fixture.json').read_text())

def test_manifest_binds_each_distance_and_group(tmp_path):
    s=fixture();s['grid']['points_per_axis']=5
    m=prepare_basis(s,tmp_path/'source')
    assert len(m['sources'])==9
    assert len({r['sha256'] for r in m['sources']})==9
    assert [r['active_count'] for r in m['sources'][:3]]==[1,6,11]
    assert impossible_power_bound(s,1,20)['status']=='PROVEN_INFEASIBLE'
    assert impossible_power_bound(s,1,5)['status']=='NOT_PROVEN_INFEASIBLE'
    with pytest.raises(ValueError):prepare_basis(s,tmp_path/'source')

def test_generalized_counts_masks_and_angular_profile(tmp_path):
    s=fixture();s['grid']['points_per_axis']=3
    s['rings']=[{'id':'custom','radius_m':.004,'count':4,'power_W_each':2,'disabled_indices':[0]}]
    s['L_candidates_m']=[.009,.022]
    m=prepare_basis(s,tmp_path/'source');assert len(m['sources'])==2
    rows=emitters(s);assert sum(r['power_W'] for r in rows)==6
    assert irradiance(rows,sigma(s,.009),-.004,0)>irradiance(rows,sigma(s,.009),.004,0)

@pytest.mark.parametrize('alpha',[-1,1.1,float('nan')])
def test_invalid_absorption_never_creates_source(tmp_path,alpha):
    s=fixture();s['absorption_fraction']=alpha
    with pytest.raises(ValueError):prepare_basis(s,tmp_path/'source')
    assert not (tmp_path/'source').exists()


def test_off_power_uniformity_is_undefined_not_perfect():
    from comsol_mcp._vcsel_source import temperature_uniformity
    assert temperature_uniformity(0,0,3)=={'roi_cv':None,'roi_cv_status':'UNDEFINED_NEAR_ZERO_MEAN','heating_feasible':False}
    assert not temperature_uniformity(2,0,3)['heating_feasible']
    assert temperature_uniformity(4,1,3)['roi_cv']==.25
    with pytest.raises(ValueError):temperature_uniformity(4,-1,3)


def test_unknown_kernel_is_refused_before_writing(tmp_path):
    s=fixture();s['kernel']['name']='unimplemented_optical_model'
    with pytest.raises(ValueError,match='unsupported kernel'):prepare_basis(s,tmp_path/'source')
    assert not (tmp_path/'source').exists()
