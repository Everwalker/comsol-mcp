"""Bounded SCM/listener test seams; no native identity or admission authority.

Reuse the publisher's original raw SDK/namespace/provisioned provider fixture.
ControlDaemon, its constructor, issuer checks and drain/close stay production.
"""
from contextlib import contextmanager
import threading

from tests.test_windows_scm_publisher import original_chain, SoftwareServer


def bootstrap_group_plan(profile):
    """Rebind the existing complete group intent to this SOFTWARE SYSTEM profile.

    This only constructs intent with production validators. No executor, issuer,
    grant, host observation or native mutation is performed.
    """
    from comsol_mcp import _w26_os_resources as resources
    from comsol_mcp import _windows_filter_group as group
    from tests.test_windows_network_intent import network_profile_and_plan
    _, seed = network_profile_and_plan()
    fields = ('action_id', 'resource_kind', 'resource_name', 'operation')
    actions = [dict({key: row[key] for key in fields}, desired=dict(row['desired']))
               for row in seed.as_dict()['change_manifest']]
    for action in actions:
        assert action['operation'] == 'CREATE'
        action.update(baseline_identity=None, baseline_sha256=None,
                      backup_path=None, backup_sha256=None)
        if action['resource_kind'] == 'wfp_provider':
            action['desired']['service_name'] = profile.service_name
    members = [row['desired'] for row in actions if row['resource_kind'] == 'wfp_filter']
    digest = group.group_fingerprint(profile.host_resource_profile, members)
    for member in members:
        member['group_sha256'] = digest
    return resources.build_host_change_plan(profile.host_resource_profile,
                                            change_manifest=actions, operator_inputs={})


@contextmanager
def scm_publisher_chain(monkeypatch, tmp_path):
    from comsol_mcp import _windows_t037_broker as broker
    from comsol_mcp import _windows_scm_publisher as publisher

    fixture = original_chain.__wrapped__(monkeypatch, tmp_path)
    state = next(fixture)
    state.events, state.daemons, state.runtimes, state.brokers = [], [], [], []
    state.status_rows, state.servers = [], []
    state.entered, state.stopped = threading.Event(), threading.Event()
    state.on_init = state.on_close = state.action = None
    state.constructor_calls = state.close_calls = 0
    original_prepare = publisher.prepare_home

    def prepare(home, runtime):
        owner = original_prepare(home, runtime)
        state.owners.append(owner)
        return owner

    monkeypatch.setattr(publisher, 'prepare_home', prepare)
    original_status = state.scm.set_service_status

    def status(handle, row):
        original_status(handle, row)
        state.status_rows.append(row)

    monkeypatch.setattr(state.scm, 'set_service_status', status)
    original_broker = broker.WindowsScmServiceBroker

    def software_broker(name, callback):
        assert name == state.profile.service_name == 'ComsolMCP_Test'

        def service_main(runtime):
            assert runtime._state == 'START_PENDING'
            state.runtimes.append(runtime)
            state.runtime = runtime
            return callback(runtime)

        service = original_broker._for_software_test(
            name, service_main, api=state.scm, token=broker._SCM_TEST_API_TOKEN)
        state.brokers.append(service)
        return service

    state.software_broker = software_broker
    monkeypatch.setattr(broker, 'WindowsScmServiceBroker', software_broker)
    daemon_type = state.cd.ControlDaemon  # keep the actual class/source binding
    original_init = daemon_type.__init__
    original_close = daemon_type.close
    original_issuer = daemon_type._windows_t037_host_control_issuer

    def init(daemon, *args, **options):
        original_init(daemon, *args, **options)
        assert type(daemon) is daemon_type
        assert daemon._windows_t037_profile is state.profile
        state.daemons.append(daemon)
        state.events.append('daemon-created')

    def issuer(daemon, runtime):
        assert runtime is state.runtime and runtime.is_active
        result = original_issuer(daemon, runtime)
        state.events.append('trusted-issuer')
        return result

    def close(daemon):
        if state.on_close is not None:
            state.on_close(daemon)
        original_close(daemon)
        assert daemon._close_complete and daemon.closed.is_set()
        state.close_calls += 1
        state.events.append('drained-close')

    monkeypatch.setattr(daemon_type, '__init__', init)
    monkeypatch.setattr(daemon_type, 'close', close)
    monkeypatch.setattr(daemon_type, '_windows_t037_host_control_issuer', issuer)

    class Listener(SoftwareServer):
        def __init__(self, address, handler):
            state.constructor_calls += 1
            state.servers.append(self)
            super().__init__(address, handler)
            state.events.append('listener-bound')
            if state.on_init is not None:
                state.on_init(self)

        def serve_forever(self):
            state.entered.set()
            if state.action is not None:
                state.action(self)
            else:
                assert state.stopped.wait(3)

        def shutdown(self):
            super().shutdown()
            state.events.append('listener-shutdown')
            state.stopped.set()

        def server_close(self):
            super().server_close()
            state.events.append('listener-close')

    state.listener_type = Listener
    monkeypatch.setattr(publisher, '_Server', Listener)
    monkeypatch.setattr(publisher, '_ServerInit', Listener.__init__)
    monkeypatch.setattr(publisher, '_ServerClose', Listener.server_close)

    def main_args(*extra):
        return ['--home', str(state.home), '--host-provider-config', str(state.provider_path),
                '--windows-t037-profile', str(state.profile_path),
                '--windows-scm-service-name', state.profile.service_name, *extra]

    state.main_args = main_args
    try:
        yield state
    finally:
        # SOFTWARE teardown only, after all owner/no-replay assertions. Delegate
        # the original drain if a failing assertion interrupted normal serve.
        state.stopped.set()
        for daemon in state.daemons:
            if not daemon._close_complete:
                original_close(daemon)
        try:
            next(fixture)
        except StopIteration:
            pass
