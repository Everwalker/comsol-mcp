from __future__ import annotations

import errno
import shutil
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

from tools.t037_isolation_acceptance import (
    _candidate_profile,
    _classify_role,
    _is_expected_denial,
    _ps_absence,
    _sbpl_string,
)


def _good_role_record() -> dict[str, object]:
    pid = 8123
    probe_binary = "/private/tmp/t037_probe_child"
    unrelated_pid = 8124
    return {
        "role": "worker",
        "command": ["/usr/bin/sandbox-exec", probe_binary],
        "probe_executable": probe_binary,
        "launcher_pid": pid,
        "returncode": 0,
        "stderr": "",
        "process_identity": {
            "status": "OBSERVED",
            "pid": pid,
            "uid": 501,
            "gid": 20,
            "command": probe_binary,
        },
        "unrelated_sentinel_process": {
            "pid": unrelated_pid,
            "alive_after_probe": True,
            "before": {"status": "OBSERVED", "pid": unrelated_pid},
            "after": {"status": "OBSERVED", "pid": unrelated_pid},
            "cleanup_inventory": {"status": "ABSENT", "returncode": 1},
        },
        "sentinel_unchanged": True,
        "allowed_endpoint_accepted": True,
        "denied_endpoint_accepted": False,
        "child_record": {
            "role": "worker",
            "pid": pid,
            "uid": 501,
            "gid": 20,
            "allowed_write_errno": 0,
            "allowed_read_errno": 0,
            "allowed_readback": "probe-ok",
            "protected_read_errno": errno.EPERM,
            "protected_write_errno": errno.EACCES,
            "allowed_network_phase": 3,
            "allowed_network_errno": 0,
            "denied_network_phase": 2,
            "denied_network_errno": errno.EPERM,
            "sentinel_query_errno": errno.EPERM,
            "sentinel_signal_errno": errno.EPERM,
            "spawn_error": errno.EACCES,
            "spawned_status": -1,
        },
    }


def test_candidate_profile_is_deny_default_with_exact_allowances(tmp_path: Path) -> None:
    binary = (tmp_path / "probe child").resolve()
    scratch = (tmp_path / "worker scratch").resolve()
    profile = _candidate_profile(
        probe_binary=binary,
        scratch=scratch,
        allowed_loopback_port=41521,
    )

    assert "(deny default)" in profile
    assert "(allow default)" not in profile
    assert f'(allow process-exec (literal "{binary}"))' in profile
    assert f'(allow file-read* file-write* (subpath "{scratch}"))' in profile
    assert '(allow network-outbound (remote ip "localhost:41521"))' in profile
    assert "(allow network-outbound)" not in profile
    assert "process-fork" not in profile
    assert "synthetic-home" not in profile


def test_sbpl_path_quoting_rejects_control_characters() -> None:
    assert _sbpl_string('/private/tmp/a"b') == '"/private/tmp/a\\"b"'
    with pytest.raises(ValueError, match="control character"):
        _sbpl_string("/private/tmp/bad\npath")


@pytest.mark.parametrize("value", [errno.EACCES, errno.EPERM])
def test_only_explicit_access_denial_errno_is_expected(value: int) -> None:
    assert _is_expected_denial(value)


@pytest.mark.parametrize("value", [None, errno.ECONNREFUSED, errno.ENOENT, 134, "EPERM"])
def test_unknown_or_unrelated_errors_are_not_denials(value: object) -> None:
    assert not _is_expected_denial(value)


def test_role_pass_requires_all_positive_and_negative_controls() -> None:
    assert _classify_role(_good_role_record()) == "PASS"


def test_unexpected_protected_access_or_network_success_is_fail() -> None:
    protected_read_succeeded = _good_role_record()
    protected_read_succeeded["child_record"]["protected_read_errno"] = 0  # type: ignore[index]
    assert _classify_role(protected_read_succeeded) == "FAIL"

    denied_endpoint_connected = _good_role_record()
    denied_endpoint_connected["denied_endpoint_accepted"] = True
    assert _classify_role(denied_endpoint_connected) == "FAIL"

    unrelated_process_signaled = _good_role_record()
    unrelated_process_signaled["unrelated_sentinel_process"]["alive_after_probe"] = False  # type: ignore[index]
    assert _classify_role(unrelated_process_signaled) == "FAIL"


def test_abort_or_non_denial_is_unknown_not_a_pass() -> None:
    aborted = _good_role_record()
    aborted["returncode"] = -6
    assert _classify_role(aborted) == "UNKNOWN"

    unexpected_errno = _good_role_record()
    unexpected_errno["child_record"]["denied_network_errno"] = errno.ECONNREFUSED  # type: ignore[index]
    assert _classify_role(unexpected_errno) == "UNKNOWN"

    signal_allowed = _good_role_record()
    signal_allowed["child_record"]["sentinel_signal_errno"] = 0  # type: ignore[index]
    assert _classify_role(signal_allowed) == "FAIL"


def test_missing_or_mismatched_process_identity_is_unknown() -> None:
    missing = _good_role_record()
    missing["process_identity"] = {"status": "UNKNOWN"}
    assert _classify_role(missing) == "UNKNOWN"

    mismatch = _good_role_record()
    mismatch["child_record"]["pid"] = 8124  # type: ignore[index]
    assert _classify_role(mismatch) == "UNKNOWN"

    changed_sentinel_birth = _good_role_record()
    changed_sentinel_birth["unrelated_sentinel_process"]["after"]["birth_text"] = "changed"  # type: ignore[index]
    assert _classify_role(changed_sentinel_birth) == "UNKNOWN"


def test_cleanup_ps_rc1_empty_is_absent_but_other_shapes_are_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tools.t037_isolation_acceptance.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )
    assert _ps_absence(8124)["status"] == "ABSENT"

    monkeypatch.setattr(
        "tools.t037_isolation_acceptance.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    assert _ps_absence(8124)["status"] == "UNKNOWN"

    monkeypatch.setattr(
        "tools.t037_isolation_acceptance.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="8124\n", stderr=""),
    )
    assert _ps_absence(8124)["status"] == "UNKNOWN"


@pytest.mark.skipif(sys.platform != "darwin", reason="C child probe is compiled with the macOS toolchain")
def test_synthetic_c_probe_compiles_with_strict_warnings(tmp_path: Path) -> None:
    compiler = shutil.which("cc") or shutil.which("clang")
    if compiler is None:
        pytest.skip("no local C compiler")
    source = Path(__file__).resolve().parents[1] / "tools" / "t037_probe_child.c"
    result = subprocess.run(
        [compiler, "-Wall", "-Wextra", "-Werror", "-O2", "-fsyntax-only", str(source)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_no_profile_acceptance_can_be_promoted_to_t037_pass() -> None:
    source = (Path(__file__).resolve().parents[1] / "tools" / "t037_isolation_acceptance.py").read_text()
    assert '"t037_acceptance": "UNVERIFIED"' in source
    assert "production_worker_or_server_integrated" in source
    assert "windows_target_verified" in source
