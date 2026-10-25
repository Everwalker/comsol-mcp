"""Actual catalog/SD/App-ID reader on inert export buffers, not native proof."""
from types import SimpleNamespace as NS
from comsol_mcp import _windows_t037_targets as targets,_windows_t037_native as native
from comsol_mcp._windows_owned_resources import registry_for
from tests.test_windows_t037_qualification_original_owner import original_owner
from tests._controller_c_wfp_sdk import install


def test_original_five_catalog_sd_and_appid_readers_use_only_inert_exports(monkeypatch,tmp_path):
    with original_owner(monkeypatch,tmp_path,callback_hold_seconds=40) as value:
        driver=NS(env=value.env);install(driver)
        api=targets.FinitePolicyApi(value.env.profile)
        assert api.observe_wfp_policy_snapshot.__func__ is native.CtypesWindowsPolicyApi.observe_wfp_policy_snapshot
        assert api.observe_application_id.__func__ is native.CtypesWindowsPolicyApi.observe_application_id
        assert api.observe_service.__func__ is native.CtypesWindowsPolicyApi.observe_service
        service=api.observe_service(value.env.profile.service_name)
        assert service['service_start_sid']==value.env.profile.service_account_sid
        assert service['service_binary_argv_sha256']==value.env.profile._launch_image('launcher').argv_sha256
        snapshot=api.observe_wfp_policy_snapshot()
        selected=targets.require_owned_policy(value.env.profile,snapshot,api)
        app=api.observe_application_id(value.env.profile._launch_image('worker').path)
        assert app['size']==len(driver.catalog_sdk.app_id) and app['type']==12
        assert len(selected)==len(value.env.profile.finite_targets.policy)
        kinds={'Provider','SubLayer','Filter','ProviderContext','Layer'}
        assert {c[1] for c in driver.catalog_sdk.calls if c[0]=='enum'}==kinds
        assert {c[1] for c in driver.catalog_sdk.calls if c[0]=='enum_close'}==kinds
        assert {c[1] for c in driver.catalog_sdk.calls if c[0]=='security'}=={'Provider','SubLayer','Filter'}
        assert not driver.catalog_sdk.live and not registry_for(api).unresolved()
