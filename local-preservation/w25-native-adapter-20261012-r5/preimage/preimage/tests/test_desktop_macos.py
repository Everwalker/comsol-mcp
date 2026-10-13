"""Injected product-framework protocol tests only; no macOS APIs are invoked."""
from types import SimpleNamespace
import pytest
from comsol_mcp._desktop_macos import MacOSFrameworkGateway, MacOSAccessibilityObserver
from comsol_mcp._desktop_platforms import create_native_metadata_adapter
from comsol_mcp._desktop_service import DesktopOperationError, WindowIdentity, WindowObservation, UNSUPPORTED_CONTROL

ID = WindowIdentity('macos', 'CGWindow:7', 123, 'birth:123', 'uid:test', 'session:test', '6.4.0.293')

class AX:
    kAXValueCGPointType = 'point'
    kAXValueCGSizeType = 'size'
    def __init__(self):
        self.trust = True
        self.calls = []
        self.window = {'AXPosition': SimpleNamespace(x=10,y=20), 'AXSize': SimpleNamespace(width=100,height=90),
                       'AXTitle':'COMSOL fixture', 'AXRole':'AXWindow', 'AXChildren':[]}
        self.windows = [self.window]
    def AXIsProcessTrusted(self):
        self.calls.append('nonprompt-trust')
        return self.trust
    def AXUIElementCreateApplication(self, pid):
        self.calls.append(('application',pid))
        return {'AXWindows':self.windows}
    def AXUIElementCopyAttributeValue(self, element, name, _):
        self.calls.append(('attribute',name))
        return (0,element[name]) if name in element else (1,None)
    def AXValueGetValue(self, value, kind, _):
        return True,value

class Q:
    kCGWindowListOptionIncludingWindow=8
    kCGWindowNumber='number';kCGWindowOwnerPID='pid';kCGWindowBounds='bounds';kCGWindowName='title'
    def __init__(self):
        self.screen=True;self.calls=[]
        self.info={'number':7,'pid':123,'bounds':{'X':10,'Y':20,'Width':100,'Height':90},'title':'COMSOL fixture'}
    def CGPreflightScreenCaptureAccess(self):
        self.calls.append('nonprompt-screen')
        return self.screen
    def CGWindowListCopyWindowInfo(self, option, number):
        self.calls.append(('single-window',option,number))
        return [self.info]

@pytest.fixture
def gateway():
    ax,q=AX(),Q()
    return MacOSFrameworkGateway(lambda:(ax,q)),ax,q

def test_gateway_lazy_until_explicit_probe():
    calls=[]
    ax,q=AX(),Q()
    g=MacOSFrameworkGateway(lambda: calls.append('load') or (ax,q))
    assert calls==[] and ax.calls==[] and q.calls==[]
    assert g.permissions()['permission_scope']=='ACTUAL_PRODUCT_CALLER_ONLY_NOT_CUA_CERTIFICATION'
    g.permissions()
    assert calls==['load']

def test_missing_framework_does_not_prompt_or_enable_control():
    def missing():raise ImportError('fixture missing')
    a=MacOSAccessibilityObserver(gateway=MacOSFrameworkGateway(missing))
    value=a.status()
    assert value['permission_status']=='UNKNOWN' and value['control_status']==UNSUPPORTED_CONTROL
    assert value['product_observer_error']=='WINDOW_METADATA_UNAVAILABLE'

def test_permission_bits_separate_and_do_not_enable_controls(gateway):
    g,ax,q=gateway;q.screen=False
    a=MacOSAccessibilityObserver(gateway=g)
    value=a.status()
    assert value['accessibility_trusted'] is True and value['screen_capture_allowed'] is False
    assert value['control_status']==UNSUPPORTED_CONTROL and value['selector_profile']=='UNOBSERVED'
    assert value['model_binding']=='UNVERIFIED'

def test_tree_reads_exact_associated_window_not_other_app_or_model(gateway):
    g,ax,q=gateway
    value=g.window_tree(ID)
    assert value['scope']=='TARGET_WINDOW_AX_METADATA_ONLY' and value['model_binding']=='UNVERIFIED'
    assert len(value['nodes'])==1
    assert q.calls[-1]==('single-window',8,7)
    assert ('application',123) in ax.calls
    assert not any('Value' in str(call) for call in ax.calls)

@pytest.mark.parametrize('case',['permission','wrong_pid','wrong_window','bounds','title','ambiguous','missing_windows','missing_geometry'])
def test_mapping_failure_is_no_focused_window_fallback(gateway,case):
    g,ax,q=gateway
    if case=='permission':ax.trust=False
    if case=='wrong_pid':q.info['pid']=124
    if case=='wrong_window':q.info['number']=8
    if case=='bounds':q.info['bounds']['X']=11
    if case=='title':q.info['title']='another'
    if case=='ambiguous':ax.windows.append(dict(ax.window))
    if case=='missing_windows':ax.windows=[]
    if case=='missing_geometry':ax.window.pop('AXSize')
    with pytest.raises(DesktopOperationError):g.window_tree(ID)
    assert not any('Focused' in str(c) for c in ax.calls)

@pytest.mark.parametrize('depth,nodes',[(-1,1),(9,1),(True,1),(1,0),(1,513),(1,True)])
def test_invalid_inventory_limits_before_native_calls(gateway,depth,nodes):
    g,ax,q=gateway
    with pytest.raises(DesktopOperationError):g.window_tree(ID,max_depth=depth,max_nodes=nodes)
    assert ax.calls==[] and q.calls==[]

def test_tree_node_bound_and_truncation(gateway):
    g,ax,q=gateway
    ax.window['AXChildren']=[{'AXRole':'AXButton','AXTitle':'child'} for _ in range(10)]
    value=g.window_tree(ID,max_nodes=3)
    assert len(value['nodes'])==3
    # At least one child was omitted; truncation must be truthful.
    assert value['truncated'] is True

def test_observer_revalidates_before_and_after(gateway):
    g,ax,q=gateway
    class Observer:
        calls=0
        def enumerate_windows(self,_):
            self.calls+=1
            return [WindowObservation(ID)] if self.calls==1 else []
    observer=Observer();a=MacOSAccessibilityObserver(gateway=g,identity_observer=observer)
    with pytest.raises(DesktopOperationError,match='during'):a.inspect_accessibility(ID)
    assert observer.calls==2

def test_stale_identity_never_enters_ax(gateway):
    g,ax,q=gateway
    observer=SimpleNamespace(enumerate_windows=lambda _ : [])
    a=MacOSAccessibilityObserver(gateway=g,identity_observer=observer)
    with pytest.raises(DesktopOperationError,match='before'):a.inspect_accessibility(ID)
    assert ax.calls==[] and q.calls==[]

@pytest.mark.parametrize('method,args',[('observe_model',(ID,)),('show_model',(ID,None)),('select_node',(ID,{})),('capture',(ID,'graphics')),('perform_action',(ID,None)),('save_copy',(ID,None)),('verify_source_preserved',(ID,None))])
def test_no_unobserved_selector_route_becomes_available(gateway,method,args):
    g,ax,q=gateway;a=MacOSAccessibilityObserver(gateway=g)
    with pytest.raises(DesktopOperationError):getattr(a,method)(*args)
    assert ax.calls==[] and q.calls==[]

def test_factory_observer_choice_is_explicit_and_lazy(monkeypatch):
    import comsol_mcp._desktop_platforms as platforms
    monkeypatch.setattr(platforms.sys,'platform','darwin')
    a=create_native_metadata_adapter(macos_accessibility_observer=True)
    assert isinstance(a,MacOSAccessibilityObserver) and a.gateway._frameworks is None


def test_missing_nonprompt_framework_api_reports_unknown_not_available():
    gateway=MacOSFrameworkGateway(lambda:(SimpleNamespace(),SimpleNamespace()))
    result=MacOSAccessibilityObserver(gateway=gateway).status()
    assert result['permission_status']=='UNKNOWN' and result['metadata_status']=='UNAVAILABLE'
    assert result['control_status']==UNSUPPORTED_CONTROL
