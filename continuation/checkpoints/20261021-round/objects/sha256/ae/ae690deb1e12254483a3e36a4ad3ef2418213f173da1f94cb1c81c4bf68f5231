"""Actual registered-source/production prebirth guards, no native witnesses."""
from pathlib import Path
import hashlib
import json
import pytest
from tests.test_g3_managed_models import env, _args, _register_source, _forged_durable_test
from comsol_mcp import _g3_managed_models as models

ROOT=Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('name',['NativeW23TEScienceFixture','W24StaticShapeFixture','W24CureCouponFixture'])
def test_actual_multiclass_scientific_source_requires_explicit_identity(env,name):
    raw=(ROOT/'tools/java'/f'{name}.java').read_bytes();source=_register_source(env,raw)
    with pytest.raises(Exception) as implicit:models._resolve_recipe_source(env['context'],source['artifact_id'])
    assert getattr(implicit.value,'code',None)=='INVALID_REQUEST'
    resolved,record,description=models._resolve_recipe_source(env['context'],source['artifact_id'],name)
    assert description['entrypoint']==name and description['source_sha256']==source['sha256']
    assert not env['worker'].executions

@pytest.mark.parametrize('entrypoint',['doesNotExist','only','Missing#run','X;exit()','../Foo',''])
def test_explicit_nonexistent_comment_or_invalid_entrypoint_refused_before_worker(env,entrypoint):
    source=_register_source(env,b'// class only\npublic class Actual { static class Nested {} }\n')
    with pytest.raises(Exception) as failure:
        models.recipe_register(env['worker'],'m',_args(env['context'],source_artifact=source['artifact_id'],entrypoint=entrypoint,input_schema={'type':'object'},test_refs=['unverified']))
    assert getattr(failure.value,'code',None)=='INVALID_REQUEST'
    assert not env['worker'].executions

@pytest.mark.parametrize('value',[None,1,False,{},'X'*257])
def test_optional_entrypoint_type_and_bound_refusal(env,value):
    with pytest.raises(Exception) as failure:
        models.recipe_register(env['worker'],'m',_args(env['context'],source_artifact='not-an-artifact',entrypoint=value,input_schema={'type':'object'},test_refs=['unverified']))
    assert getattr(failure.value,'code',None)=='INVALID_REQUEST'
    assert not env['worker'].executions


def test_explicit_class_never_promotes_forged_test_to_native_witness(env):
    source=_register_source(env,b'public class Actual { static class Nested {} }\n')
    fake=_forged_durable_test(env,source['sha256'])
    with pytest.raises(Exception) as failure:
        models.recipe_register(env['worker'],'m',_args(env['context'],source_artifact=source['artifact_id'],entrypoint='Actual',input_schema={'type':'object'},test_refs=[fake]))
    assert getattr(failure.value,'code',None)=='RECIPE_TEST_UNVERIFIED'
    assert not env['worker'].executions


def test_w24_cure_recipe_source_origin_matches_both_mirrors_and_keeps_build_v2_default():
    origins_path = ROOT / 'comsol_mcp/data/science_recipes/SOURCE_ORIGINS.json'
    origins = json.loads(origins_path.read_text(encoding='utf-8'))
    matches = [row for row in origins['files'] if row['origin'] ==
               'tools/java/W24CureCouponFixture.java' and row['resource'] ==
               'comsol_mcp/data/science_recipes/java/W24CureCouponFixture.java']
    assert len(matches) == 1
    record = matches[0]
    origin = (ROOT / record['origin']).read_bytes()
    resource = (ROOT / record['resource']).read_bytes()
    assert origin == resource
    assert hashlib.sha256(origin).hexdigest() == record['sha256']

    parameters = json.loads((ROOT / 'comsol_mcp/data/science_recipes/parameters.json').read_text(encoding='utf-8'))
    cure = next(row for row in parameters['recipes'] if row['id'] == 'W24_CURE')
    assert cure['entrypoint'] == 'W24CureCouponFixture#runInPlace'
    assert cure['rebuild']['phase'] == 'build_v2'
    assert cure['witness']['phase'] == 'build_v2'
