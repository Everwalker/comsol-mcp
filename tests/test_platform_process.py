"""Platform-only process probing tests; no real child is started or signalled."""
from comsol_mcp import _platform_process as processes


def test_windows_probe_never_uses_os_kill(monkeypatch):
    calls = []
    monkeypatch.setattr(processes, "_windows_process_identity", lambda pid: calls.append(pid) or {"alive": True, "start_epoch_ms": 7})
    monkeypatch.setattr(processes.os, "kill", lambda *_: (_ for _ in ()).throw(AssertionError("Windows probe must not signal")))
    assert processes.process_identity(99, platform_name="nt") == {"alive": True, "start_epoch_ms": 7}
    assert calls == [99]


def test_invalid_pid_is_dead_without_platform_probe(monkeypatch):
    monkeypatch.setattr(processes, "_windows_process_identity", lambda _: (_ for _ in ()).throw(AssertionError("invalid PID")))
    assert processes.process_identity(1, platform_name="nt") == {"alive": False, "start_epoch_ms": None}
