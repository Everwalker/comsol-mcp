import copy,json
from pathlib import Path
import pytest
from tools import w24_v2_setup_readback as v2
from tests.test_w24_cure_capture_link import _contract_readback

from tests.test_w24_current11_config_projection import _actual_pair

def fixture_pair(tmp_path):
    # Actual v1 bytes protect all original physical/mesh gates. V2 values below
    # are synthetic interface fixtures; comparison explicitly cannot mint origin.
    before,after=_actual_pair(tmp_path)
    contract=_contract_readback()
    for row in (before,after):
        row.update(copy.deepcopy(contract))
        row.update(native_semantics='UNVERIFIED_FAIL_CLOSED',module_license='UNVERIFIED_FAIL_CLOSED',
            geometric_nonlinearity_readbacks={stage:{'receiver_type':'Time','sequence_tag':'sol'+str(i),'feature_tag':'t1','property':'geometricNonlinearity','getter':'getString','value':'on','frame_mapping':'REQUIRES_FRESH_EQUATION_VIEW_NOT_INHERITED_FROM_V1'} for i,stage in enumerate(['stdUV','stdBake','stdCool'])})
    return before,after

def test_full_versioned_compare_is_not_native_authority(tmp_path):
    before,after=fixture_pair(tmp_path);result=v2.compare_v2_save_reopen(before,after)
    assert result['matches']
    assert result['mesh_volume']==result['physical_and_mesh']['mesh_volume']
    assert len(result['physical_and_mesh']['compared_keys'])==21
    assert len(result['v2_configuration']['compared_keys'])==9
    assert result['scientific_acceptance']=='NOT_ACCEPTED' and result['Study_solver_authorized']==0

@pytest.mark.parametrize('kind',['extra_physical','missing_physical','changed_physical','extra_marker','missing_marker','dose','maxwell','activation','nonlinear_off','nonlinear_unknown','duplicate_sequence','claim_semantics'])
def test_no_added_missing_or_changed_configuration_is_hidden(kind,tmp_path):
    before,after=fixture_pair(tmp_path)
    if kind=='extra_physical':after['invented_physical_property']=123
    elif kind=='missing_physical':after.pop('heat_readbacks')
    elif kind=='changed_physical':after['solid_quasistatic_readback']='Transient'
    elif kind=='extra_marker':after['geometric_nonlinearity_readbacks']['stdUV']['invented']=True
    elif kind=='missing_marker':after.pop('spatial_uv_readback')
    elif kind=='dose':after['relative_exposure_dose']['unit']='1'
    elif kind=='maxwell':after['viscoelastic_readback']['tauvm']=['anotherBranch']
    elif kind=='activation':after['activation_readback']['expression']='alpha>0.1'
    elif kind=='nonlinear_off':after['geometric_nonlinearity_readbacks']['stdUV']['value']='off'
    elif kind=='nonlinear_unknown':after['geometric_nonlinearity_readbacks']['stdUV']['value']=None
    elif kind=='duplicate_sequence':after['geometric_nonlinearity_readbacks']['stdBake']['sequence_tag']='sol0'
    elif kind=='claim_semantics':after['native_semantics']='PASS'
    try: result=v2.compare_v2_save_reopen(before,after)
    except (ValueError,TypeError): return
    assert not result['matches']

def test_actual_v1_missing_v2_is_refused(tmp_path):
    before,after=_actual_pair(tmp_path)
    with pytest.raises(ValueError):v2.compare_v2_save_reopen(before,after)
