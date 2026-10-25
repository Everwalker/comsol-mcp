"""Source/software intent tests; no SDK, process or C authority."""
from dataclasses import FrozenInstanceError
import copy
import pytest
from comsol_mcp import _windows_t037_qualification_recipe as recipe
from comsol_mcp import _w26_os_resources as resources
from comsol_mcp._execution_contract import ExecutionContractError
from tests.test_windows_host_change import _profile, _plan


def document():
    return dict(schema=recipe.SCHEMA, prepared_manifest_sha256='a'*64,
        runtime_id='normal-runtime', comsol_version='6.4.0.293',
        vendor_roles=['server','worker'], os_controls=list(recipe.CONTROLS))


def test_optional_absent_preserves_existing_plan_and_other_inputs():
    assert recipe.from_operator_inputs({'operator':'reviewed'}) is None
    assert recipe.from_plan(_plan(_profile())) is None


def test_exact_optional_recipe_is_immutable_and_changes_approved_plan_digest():
    old=_plan(_profile());old_doc=old.as_dict()
    plan=resources.build_host_change_plan(_profile(),change_manifest=[{
        'action_id':a['action_id'],'resource_kind':a['resource_kind'],'resource_name':a['resource_name'],
        'operation':a['operation'],'desired':a['desired'],'baseline_identity':None,
        'baseline_sha256':None,'backup_path':None,'backup_sha256':None}
        for a in old_doc['change_manifest']],operator_inputs={recipe.KEY:document()})
    parsed=recipe.from_plan(plan)
    assert parsed.as_dict()==document() and parsed.sha256==recipe.digest(document())
    assert plan.sha256!=old.sha256
    assert plan.as_dict()['operator_inputs_sha256']!=old_doc['operator_inputs_sha256']
    with pytest.raises(FrozenInstanceError):parsed.runtime_id='other'
    tampered=plan.as_dict();tampered['operator_inputs'][recipe.KEY]['runtime_id']='other'
    import json
    with pytest.raises(resources.ResourcePolicyError):recipe.from_plan(resources.HostChangePlan(json.dumps(tampered),plan.sha256))


@pytest.mark.parametrize('field,value',[
 ('schema','caller-purpose'),('prepared_manifest_sha256','A'*64),('prepared_manifest_sha256',None),
 ('runtime_id','..\\arbitrary.exe'),('runtime_id',True),('comsol_version','6.4.0.293 evil'),
 ('comsol_version','6.5.0.293'),('vendor_roles',[]),('vendor_roles',['worker','server']),
 ('vendor_roles',['server','server']),('vendor_roles',['compiler']),('vendor_roles','server'),
 ('os_controls',[]),('os_controls',['FILE_CREATE','FILE_READ','NETWORK_CONNECT']),
 ('os_controls',['FILE_READ','FILE_CREATE','NETWORK_CONNECT','OTHER']),('os_controls',None)])
def test_closed_recipe_refuses_invalid_input(field,value):
    raw=document();raw[field]=value
    with pytest.raises(ExecutionContractError,match='qualification'):recipe.from_operator_inputs({recipe.KEY:raw})


@pytest.mark.parametrize('extra',['command','code','java_main','authorization_ref','purpose','grant'])
def test_recipe_never_takes_generic_code_or_authority(extra):
    raw=document();raw[extra]='caller'
    with pytest.raises(ExecutionContractError):recipe.from_operator_inputs({recipe.KEY:raw})


def test_both_supported_release_builds_retained_and_role_subsets_bounded():
    for version in ['6.3.0.290','6.4.0.293','6.4.1.1']:
        raw=document();raw['comsol_version']=version;raw['vendor_roles']=['worker']
        result=recipe.from_operator_inputs({recipe.KEY:raw})
        assert result.comsol_version==version and result.vendor_roles==('worker',)
    with pytest.raises(ExecutionContractError):recipe.from_operator_inputs({recipe.KEY:None})


@pytest.mark.parametrize('malformed',[False,True])
def test_actual_executor_recipe_without_original_enrollment_refuses_before_sdk_or_admission(tmp_path,monkeypatch,malformed):
    from tests.test_windows_filter_group import profile_and_plan, executor
    profile,old=profile_and_plan();payload=old.as_dict();raw=document()
    if malformed:raw['code']='not allowed'
    plan=resources.build_host_change_plan(profile,change_manifest=[dict(
        **{k:a[k] for k in ['action_id','resource_kind','resource_name','operation','desired']},
        baseline_identity=None,baseline_sha256=None,backup_path=None,backup_sha256=None)
        for a in payload['change_manifest']],operator_inputs={recipe.KEY:raw})
    exe,api,store,provider,authority=executor(tmp_path,monkeypatch,profile,plan)
    before=list(api.calls)
    try:
        with pytest.raises(ExecutionContractError) as error:exe.apply(plan,approval=resources.HostChangeApproval(plan.sha256,'apply-once'))
        assert error.value.code==('T037_QUALIFICATION_RECIPE_INVALID' if malformed else 'T037_QUALIFICATION_OWNER_REQUIRED')
        assert api.calls==before
        assert store.db.execute('SELECT count(*) FROM host_control_admissions').fetchone()[0]==0
        assert store.db.execute('SELECT count(*) FROM operations').fetchone()[0]==0
    finally:provider.close();store.close()



def test_manifest_recipe_binds_complete_original_prepared_material_not_one_source_hash(tmp_path,monkeypatch):
    from contextlib import ExitStack
    from dataclasses import replace
    from types import MappingProxyType
    from tests._prepared_launch_software_fixture import PreparedEnvironment
    with ExitStack() as stack:
        env=PreparedEnvironment(monkeypatch,tmp_path,stack)
        manifest=env.profile.prepared_launch
        raw=document();raw['prepared_manifest_sha256']=recipe.manifest_digest(manifest)
        bound=recipe.from_operator_inputs({recipe.KEY:raw})
        assert bound.require_manifest(manifest) is manifest
        row=dict(manifest.generated_classes[0]);row['sha256']='f'*64
        changed=replace(manifest,generated_classes=(MappingProxyType(row),*manifest.generated_classes[1:]))
        assert recipe.manifest_digest(changed)!=recipe.manifest_digest(manifest)
        with pytest.raises(ExecutionContractError):bound.require_manifest(changed)
        changed=replace(manifest,role_cwds=MappingProxyType(dict(manifest.role_cwds,worker='foreign')))
        with pytest.raises(ExecutionContractError):bound.require_manifest(changed)
        with pytest.raises(ExecutionContractError):recipe.manifest_digest({'sha256':raw['prepared_manifest_sha256']})
        # Scope checking is pure data comparison, not a permission/owner producer.
        from comsol_mcp import _windows_t037_qualification as q, _windows_os_probe_protocol as wire
        from comsol_mcp._windows_t037_network import derive_session_network_intent
        import ipaddress,ntpath
        intent=derive_session_network_intent(env.profile);nonce='c'*32
        java=manifest.jdk_files['java'];parent=next(t.path for t in env.profile.finite_targets.files if 'temp_leaf' in t.roles)
        request=dict(version=1,nonce=nonce,recipe_sha256=bound.sha256,records=[
            wire.read_hash_record(0,java['source_path'],java['size']+1),
            dict(index=1,operation='CREATE_NEW',path=ntpath.join(parent,wire.create_basename(nonce,1))),
            dict(index=2,operation='TCP_ECHO',address=ipaddress.ip_address(intent.bind_address).packed,port=intent.server_listen_port)])
        request=wire.decode_request(wire.encode_request(request))
        assert q.validate_os_request_scope(env.profile,request) is request
        for index,key,value in [(0,'path',r'C:\secret.bin'),(0,'maxbytes',java['size']),
                                (1,'path',ntpath.join(r'C:\outside',wire.create_basename(nonce,1))),
                                (2,'address',bytes((8,8,8,8))),(2,'port',65535)]:
            changed=copy.deepcopy(request);changed['records'][index][key]=value
            with pytest.raises(ExecutionContractError):q.validate_os_request_scope(env.profile,changed)

