import copy,json
from pathlib import Path
import pytest
from tools import w24_witnessed_stress as witness

from tests.w24_attachment_fixture import attachment_case
RAW=Path(__file__).parent/'fixtures/w24_current11/solid_equation_view_expression_inventory.json'

def actual(): return json.loads(RAW.read_bytes())

def test_actual_four_spatial_rows_preserve_all_cells():
    inventory=actual();result=witness.select_spatial_cauchy_rows(inventory)
    assert result['status']=='SOURCE_ONLY_REQUIRES_AUTHENTICATED_NATIVE_CONTEXT'
    assert result['Study_solver_authorized']==0
    assert result['dependent_dof_status']=='NOT_ESTABLISHED_FROM_EQUATION_VIEW'
    for role,row in result['roles'].items():
        assert row['raw_row']==inventory['feature_tables'][0]['raw_rows'][row['raw_row_index']]
        assert row['original_equation_view_unit']=='N/m^2'
        assert row['requested_evaluation_unit']=='Pa'
        assert row['tensor_measure']=='CAUCHY' and row['frame']=='SPATIAL'

@pytest.mark.parametrize('column,replacement',[(1,'solid.SRR'),(2,'solid.PRR'),(3,'Pa'),(3,'N'),(4,'Cauchy radial stress'),(5,'边界 1–15'),(8,'/solid/StressGroup/material/'),(9,'应力张量（材料坐标系）'),(10,'Other'),(11,'/PhysicsList/solid/PhysicsFeatureList/vis1'),(12,'comp2')])
def test_changed_raw_tensor_or_frame_or_alias_rejected(column,replacement):
    inventory=actual();inventory['feature_tables'][0]['raw_rows'][230][column]=replacement
    with pytest.raises(witness.StressWitnessError):witness.select_spatial_cauchy_rows(inventory)

@pytest.mark.parametrize('kind',['missing','duplicate','short_row','duplicate_feature','missing_frame','incomplete','count','bool_count'])
def test_missing_duplicate_or_incomplete_witness_rejected(kind):
    inventory=actual();table=inventory['feature_tables'][0]
    if kind=='missing':table['raw_rows'].pop(230);table['row_count']-=1;inventory['expression_row_count']-=1
    elif kind=='duplicate':table['raw_rows'].append(copy.deepcopy(table['raw_rows'][230]));table['row_count']+=1;inventory['expression_row_count']+=1
    elif kind=='short_row':table['raw_rows'][230].pop()
    elif kind=='duplicate_feature':inventory['feature_tables'].append(copy.deepcopy(table));inventory['feature_count']+=1;inventory['expression_row_count']+=table['row_count']
    elif kind=='missing_frame':table['raw_rows'][230][9]=None
    elif kind=='incomplete':inventory['complete']=False
    elif kind=='count':inventory['expression_row_count']+=1
    elif kind=='bool_count':inventory['model_mutations']=False
    with pytest.raises(witness.StressWitnessError):witness.select_spatial_cauchy_rows(inventory)

def test_authenticated_actual_history_is_not_new_science_authority(attachment_case):
    manifest=attachment_case['manifest']
    m=json.loads(manifest.read_bytes())
    result=witness.authenticate_current11_mapping(manifest,m['roles']['template_receipt'])
    assert result['status']=='AUTHENTICATED_HISTORICAL_CONFIGURATION_MAPPING_NOT_EVALUATED'
    assert result['input_attachment']['verified_file_count']==attachment_case['file_count']
    assert result['historical_refs']=='TERMINATED_PROVENANCE_ONLY'
    assert result['native_numeric_values']=='NOT_EVALUATED'
    assert result['Study_solver_authorized']==0
    assert 'FRESH_V2' in result['new_science_requirement']
