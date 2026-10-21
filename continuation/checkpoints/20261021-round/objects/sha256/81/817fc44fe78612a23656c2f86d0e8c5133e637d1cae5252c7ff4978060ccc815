"""Source/prebirth controls only: no fake native builder success or JVM."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4
import pytest
from tools import science_recipe_cycle as science

ROOT=Path(__file__).resolve().parents[1]
INSTALL=Path('/Applications/COMSOL64/Multiphysics')
JAVA=Path('/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home')

@pytest.fixture
def prepared(tmp_path):
    runtime=Path('/private/tmp')/('science-guard-'+uuid4().hex)
    value=science.plan(ROOT,runtime,INSTALL,JAVA)
    path=tmp_path/'plan.json';path.write_text(json.dumps(value));path.chmod(0o600)
    return path,value,runtime


def test_exact_plan_real_resources_and_literal_calls(prepared):
    path,value,runtime=prepared
    assert science.validate_plan(path,science.digest(path),source=ROOT,runtime=runtime,installation=INSTALL,java_home=JAVA)==value
    assert len(value['ordered_operations'])==76
    assert len(value['literal_cycle_calls'])==67
    assert value['budget']['task_models_max']==8 and value['budget']['study_submissions']==value['budget']['solver_calls']==0
    assert [c['entrypoint'] for c in value['cases']][-2:]==['W24StaticShapeFixture#runInPlace','W24CureCouponFixture#runInPlace']
    assert all('test_refs' not in c for c in value['cases'])
    science.check_closure(ROOT,value)

@pytest.mark.parametrize('field',['files','budget','cases','runtime_identity','ordered_operations','literal_cycle_calls'])
def test_rehashed_drop_field_still_refused(prepared,field):
    path,value,runtime=prepared;value.pop(field);path.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='drift'):
        science.validate_plan(path,science.digest(path),source=ROOT,runtime=runtime,installation=INSTALL,java_home=JAVA)


def test_namespace_reuse_refused_before_any_effect(prepared,monkeypatch):
    path,value,runtime=prepared
    other=runtime.parent/(runtime.name+'-other')
    with pytest.raises(ValueError,match='drift'):
        science.validate_plan(path,science.digest(path),source=ROOT,runtime=other,installation=INSTALL,java_home=JAVA)
    existing=path.parent/'exists';existing.mkdir()
    with pytest.raises(ValueError,match='namespace'):science.plan(ROOT,existing,INSTALL,JAVA)


def test_wrong_python_closure_and_plan_permissions_refused(prepared,monkeypatch):
    path,value,runtime=prepared
    monkeypatch.setattr(sys,'executable',sys._base_executable)
    with pytest.raises(ValueError,match='drift'):
        science.validate_plan(path,science.digest(path),source=ROOT,runtime=runtime,installation=INSTALL,java_home=JAVA)
    path.chmod(0o644)
    with pytest.raises(ValueError,match='owner-private'):science.validate_plan(path,'0'*64,source=ROOT,runtime=runtime,installation=INSTALL,java_home=JAVA)


def test_samefd_parent_symlink_and_source_tamper(tmp_path,prepared):
    file=tmp_path/'source';file.write_bytes(b'actual');link=tmp_path/'link';link.symlink_to(tmp_path,target_is_directory=True)
    with pytest.raises(ValueError,match='parent symlink'):science.digest(link/'source')
    _,value,_=prepared;value['files']['tools/science_recipe_cycle.py']='0'*64
    with pytest.raises(RuntimeError,match='closure changed'):science.check_closure(ROOT,value)


def test_cleanup_suffix_after_known_prebuild_failure_and_no_next_effect(prepared):
    _,value,_=prepared;history=[]
    science.check_call_order(value,20,'session.disconnect',cleanup=True,cleanup_history=history,connected=True);history.append('session.disconnect')
    science.check_call_order(value,21,'session.stop',cleanup=True,cleanup_history=history,connected=True);history.append('session.stop')
    for op,cleanup in [('code.execute_java',False),('session.stop',True)]:
        with pytest.raises(RuntimeError):science.check_call_order(value,22,op,cleanup=cleanup,cleanup_history=history,connected=True)
    with pytest.raises(RuntimeError):science.check_call_order(value,20,'session.stop',cleanup=True,cleanup_history=[],connected=True)


def test_no_return_only_or_missing_native_readback_accepted():
    for value in [{'success':True,'data':{'execution_success':True,'readback':{'readback':42}}},
                  {'success':True,'data':{'status':'EXECUTED','execution_state_unknown':True}}]:
        with pytest.raises(RuntimeError):science.payload(value)
    class Broken(Exception):
        def __str__(self):raise RuntimeError('secondary')
    assert science.safe_text(Broken())=='<unprintable Broken>'


def test_mode_fails_before_popen_on_rehashed_invalid_plan(prepared,monkeypatch):
    import mcp.client.stdio  # load type annotations before zero-Popen sentinel
    path,value,runtime=prepared;value['budget']['study_submissions']=1;path.write_text(json.dumps(value))
    spec=importlib.util.spec_from_file_location('science_client',ROOT/'tools/run_v3_public_mcp_smoke.py')
    client=importlib.util.module_from_spec(spec);spec.loader.exec_module(client)
    monkeypatch.syspath_prepend(str(ROOT/'tools'))
    monkeypatch.setattr(client,'source_identity',lambda *_: {})
    monkeypatch.setattr(client.subprocess,'Popen',lambda *a,**k:pytest.fail('Popen before source/literal gate'))
    args=SimpleNamespace(science_recipes=True,package_cycle=False,parameter_io=False,parameter_csv=False,runtime_observation=False,
         source=ROOT,runtime=runtime,installation=INSTALL,java_home=JAVA,freeze=path,science_plan=path,science_plan_sha256=science.digest(path))
    with pytest.raises(ValueError,match='drift'):asyncio.run(client.run(args))


def test_w24_inplace_reuses_physics_and_default_creation_remains():
    shape=(ROOT/'tools/java/W24StaticShapeFixture.java').read_text()
    assert 'return build(parent, caseId, configuration(configurationId), false);' in shape
    assert 'return build(parent, caseId, selected, true);' in shape
    assert 'ModelUtil.createUnique' in shape and '!complete && !inPlace' in shape
    cure=(ROOT/'tools/java/W24CureCouponFixture.java').read_text()
    for source in [shape,cure]:
        guard=source[source.index('public static Object runInPlace'):]
        for required in ['expected_model_tag','ModelUtil.tags()','component().tags()','geom().tags()','study().tags()','sol().tags()','param().varnames()']:
            assert required in guard
    assert 'return run(model, args);' in cure
    getter=(ROOT/'tools/java/ScientificBuildReadback.java').read_text()
    assert '.run(' not in getter and 'ModelUtil' not in getter and 'getBoundingBox()' in getter


def test_literal_cycle_bodies_match_actual_public_registry(prepared):
    from comsol_mcp._g2_registry import validate_call
    _,value,_=prepared
    replacements={'$SOURCE_PATH':'science/java/W22Fixture.java','$SOURCE_ID':'a'*64,'$REVISION':1,
                  '$EXACT_INPUT_SCHEMA':{'type':'object'},'$WITNESS_OPERATION_ID':'op-actual-placeholder',
                  '$RECIPE_ID':'b'*64,'$MODEL_TAG':'mcp1','$WORKSPACE':'/private/tmp/workspace'}
    def resolve(v):
        if isinstance(v,str):
            if v in replacements:return replacements[v]
            for key,text in replacements.items():
                if isinstance(text,str):v=v.replace(key,text)
            return v
        if isinstance(v,dict):return {k:resolve(x) for k,x in v.items()}
        if isinstance(v,list):return [resolve(x) for x in v]
        return v
    for slot in value['literal_cycle_calls']:
        body=resolve(slot['arguments']);op=slot['operation']
        if op!='model.inspect':body.update(project_id='project-software',session_id='session-software',request_id='request-software')
        validate_call(op,body,allow_unbound_identity=True)


def test_literal_body_guards_wrong_install_argv_permission_and_cleanup(prepared):
    _,value,_=prepared
    slot=value['literal_base_calls'][0]
    science.check_literal_body(slot,{'roots':[str(INSTALL)]},{})
    with pytest.raises(RuntimeError):science.check_literal_body(slot,{'roots':['/different/install']},{})
    project=value['literal_base_calls'][2];body=json.loads(json.dumps(project['arguments']));body['policy']['permissions'].append('invented')
    with pytest.raises(RuntimeError):science.check_literal_body(project,body,{})
    cleanup=value['literal_cleanup_calls'][1]
    bindings={'$PROJECT_ID':'p','$SESSION_ID':'s'}
    body={'project_id':'p','session_id':'s','authorization_ref':'oldonce','idempotency_key':'new'}
    with pytest.raises(RuntimeError):science.check_literal_body(cleanup,body,bindings)


def test_science_full_execution_window_keeps_cleanup_reserve():
    assert science.execution_timeouts(151)=={'rpc_timeout_s':135,'queue_timeout_s':15,'execution_timeout_s':120}
    for remaining in [150,120,0,-1]:
        with pytest.raises(TimeoutError):science.execution_timeouts(remaining)
