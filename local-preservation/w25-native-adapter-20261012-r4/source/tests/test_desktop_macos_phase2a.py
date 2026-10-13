"""Injected software controls; no actual permission APIs, inventory or install."""
import builtins
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace
import pytest
from packaging.requirements import Requirement
from comsol_mcp._desktop_macos import MacOSMetadataAdapter
from comsol_mcp._desktop_service import BLOCKED_PERMISSION, UNSUPPORTED_CONTROL

@pytest.fixture
def providers(monkeypatch):
    calls=[]
    def no_inventory(*args):raise AssertionError('status must not enumerate or inspect UI')
    ax=SimpleNamespace(AXIsProcessTrusted=lambda: calls.append('AX-nonprompt') or True)
    q=SimpleNamespace(CGWindowListCopyWindowInfo=no_inventory,
        CGRequestScreenCaptureAccess=no_inventory, CGPreflightScreenCaptureAccess=no_inventory)
    for name in ('kCGWindowListOptionOnScreenOnly','kCGWindowListExcludeDesktopElements',
                 'kCGNullWindowID','kCGWindowOwnerPID','kCGWindowOwnerName','kCGWindowNumber','kCGWindowName'):
        setattr(q,name,1)
    p=SimpleNamespace(Process=no_inventory)
    f=SimpleNamespace(NSBundle=SimpleNamespace(bundleWithPath_=no_inventory))
    modules={'ApplicationServices':ax,'Quartz':q,'psutil':p,'Foundation':f}
    for name,module in modules.items():monkeypatch.setitem(sys.modules,name,module)
    monkeypatch.setattr(sys,'platform','darwin')
    return modules,calls

def assert_unavailable(value):
    assert value['permission_status']=='UNKNOWN'
    assert value['metadata_status']=='UNAVAILABLE'
    assert value['accessibility_trusted'] is None
    assert value['control_status']==UNSUPPORTED_CONTROL
    assert value['native_evidence']=='NOT_RUN'

@pytest.mark.parametrize('trusted',[False,True])
def test_successful_nonprompt_bit_only_caller_and_no_inventory(providers,trusted):
    modules,calls=providers
    modules['ApplicationServices'].AXIsProcessTrusted=lambda: calls.append('AX-nonprompt') or trusted
    value=MacOSMetadataAdapter().status()
    assert value['permission_status']==('AVAILABLE' if trusted else BLOCKED_PERMISSION)
    assert value['metadata_status']=='AVAILABLE' and value['accessibility_trusted'] is trusted
    assert value['permission_scope']=='ACTUAL_PRODUCT_CALLER_ONLY_NOT_CUA_CERTIFICATION'
    assert value['control_status']==UNSUPPORTED_CONTROL and value['native_evidence']=='NOT_RUN'
    assert value['operation_gaps']['desktop.capture'].startswith('UNSUPPORTED_CONTROL')
    assert calls==['AX-nonprompt'] and 'metadata_error' not in value

@pytest.mark.parametrize('name',['ApplicationServices','Quartz','psutil','Foundation'])
def test_missing_dependency_is_not_permission_denied(monkeypatch,providers,name):
    modules,calls=providers;monkeypatch.setitem(sys.modules,name,None)
    value=MacOSMetadataAdapter().status();assert_unavailable(value)
    assert value['metadata_error']['stage']=='dependency_import'
    assert value['metadata_error']['type'] in {'ModuleNotFoundError','ImportError'}
    assert calls==[]

@pytest.mark.parametrize('provider,symbol',[
    ('ApplicationServices','AXIsProcessTrusted'),('Quartz','CGWindowListCopyWindowInfo'),
    ('psutil','Process'),('Foundation','NSBundle'),('Quartz','kCGWindowNumber')])
def test_missing_api_or_constant_is_unknown_before_permission(monkeypatch,providers,provider,symbol):
    modules,calls=providers;monkeypatch.delattr(modules[provider],symbol)
    value=MacOSMetadataAdapter().status();assert_unavailable(value)
    assert value['metadata_error']['stage'] in {'api_availability','dependency_import'}
    assert calls==[]

@pytest.mark.parametrize('provider,symbol',[
    ('ApplicationServices','AXIsProcessTrusted'),('Quartz','CGWindowListCopyWindowInfo'),
    ('psutil','Process'),('Foundation','bundleWithPath_')])
def test_noncallable_api_never_reports_available(monkeypatch,providers,provider,symbol):
    modules,calls=providers
    target=modules[provider].NSBundle if provider=='Foundation' else modules[provider]
    monkeypatch.setattr(target,symbol,None)
    value=MacOSMetadataAdapter().status();assert_unavailable(value)
    assert value['metadata_error']['stage']=='api_availability' and calls==[]

@pytest.mark.parametrize('error',[RuntimeError('fixture API error'),OSError('fixture OS error')])
def test_permission_api_exception_retains_unknown_and_cause(providers,error):
    modules,calls=providers
    def fail():calls.append('AX-nonprompt');raise error
    modules['ApplicationServices'].AXIsProcessTrusted=fail
    value=MacOSMetadataAdapter().status();assert_unavailable(value)
    assert value['metadata_error']=={'stage':'nonprompt_accessibility_trust','type':type(error).__name__,'message':str(error)}
    assert calls==['AX-nonprompt']

def test_construct_and_wrong_platform_status_do_not_load_or_query(monkeypatch):
    calls=[];original=builtins.__import__
    def guard(name,*args,**kwargs):
        if name in {'ApplicationServices','Quartz','psutil','Foundation'}:
            calls.append(name);raise AssertionError('unexpected lazy import')
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',guard)
    adapter=MacOSMetadataAdapter();assert calls==[]
    monkeypatch.setattr(sys,'platform','linux')
    result=adapter.status();assert result['metadata_status']=='WRONG_PLATFORM' and calls==[]

def test_packaging_extra_only_darwin_and_core_unchanged():
    root=Path(__file__).parents[1]
    current=tomllib.loads((root/'pyproject.toml').read_text())
    assert current['project']['dependencies']==['mcp>=1.30,<2','MPh','JPype1']
    assert current['project']['optional-dependencies']['dev']==['pytest']
    extra=[Requirement(x) for x in current['project']['optional-dependencies']['desktop']]
    assert {x.name for x in extra}=={'psutil','pyobjc-framework-ApplicationServices','pyobjc-framework-Quartz','pyobjc-framework-Cocoa'}
    for item in extra:
        assert item.marker.evaluate({'sys_platform':'darwin'})
        assert not item.marker.evaluate({'sys_platform':'linux'})
        assert not item.marker.evaluate({'sys_platform':'win32'})
    # Complete pyproject field preservation belongs to the frozen diff/manifest
    # audit; this deliverable test must not depend on a sibling scratch baseline.
