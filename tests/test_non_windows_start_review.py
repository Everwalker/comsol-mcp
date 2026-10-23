"""Main regression counter: scoped grants do not opt a console into SCM."""
from pathlib import Path

from tests.test_managed_stop_origin import _original_scoped_chain, _start, _SoftwareChild


def test_non_windows_start_with_existing_scoped_provider_keeps_original_launch_lane(monkeypatch):
    with _original_scoped_chain(monkeypatch) as ctx:
        daemon = ctx['daemon']
        # The real provider also serves unrelated model/artifact grants on Mac.
        # Remove the explicitly injected Windows SCM/profile test adapter.
        daemon._windows_t037_profile = None
        daemon._windows_t037_profile_path = None
        daemon._windows_t037_scm_binding = None
        daemon._windows_t037_scm_runtime = None
        launcher = daemon.session_server_launcher
        launcher.platform_name = 'darwin'
        calls = []

        def create_child(command, **options):
            calls.append(tuple(command))
            child = _SoftwareChild(48199, ctx['children'])
            ctx['children'][child.pid] = child
            Path(command[command.index('-portfile') + 1]).write_text(
                command[command.index('-port') + 1], encoding='ascii')
            return child

        launcher.process_factory = create_child
        result = _start(daemon, ctx['project_id'], 'main-non-windows-start')
        assert result.get('success') is True, result
        assert len(calls) == 1
        assert daemon._managed_stop_origins == {}
