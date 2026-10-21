from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp import _windows_t037 as windows_t037
from tools import windows_current_installed_profile_runtime_binding_r3 as binding
from tests.test_windows_current_installed_profile_runtime_binding_r3 import _binding_inputs, _inputs
from tests.test_windows_host_change import _profile as windows_host_profile


@pytest.mark.parametrize("target", sorted(binding.TARGET_BUILDS))
def test_bound_installed_runtime_reaches_core_resolver_but_t037_blocks_birth(
        tmp_path, monkeypatch, target):
    """The current-source runtime can enter Core; unverified T037 stops before birth."""
    monkeypatch.setattr(
        binding, "_windows_product_version",
        lambda executable: Path(executable).parents[2].name.removeprefix("comsol-"),
    )
    inputs = _inputs(tmp_path, target)
    candidate = binding._build_runtime_binding_from_verified_outputs(**_binding_inputs(inputs))
    runtime = binding.make_session_runtime_config(candidate)
    workspace = tmp_path / "project-root"
    workspace.mkdir()

    resolved_calls = []

    def resolve_runtime(runtime_id, project_id, session_id, project_root, state_root):
        resolved_calls.append((runtime_id, project_id, session_id, project_root, state_root))
        return runtime

    daemon = ControlDaemon(
        tmp_path / "control-home",
        project_root=workspace,
        session_runtime_resolver=resolve_runtime,
    )
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda *args, **kwargs: pytest.fail("T037 admission refusal must precede process creation"),
    )
    daemon._windows_t037_profile = windows_t037.WindowsT037ProfileV2(
        windows_host_profile(), "d" * 64, {}, "SOFTWARE_ONLY",
    )
    monkeypatch.setattr(daemon, "_authorize_server_control", lambda *args: None)
    monkeypatch.setattr(
        daemon, "_require_t037_policy",
        lambda *args: (_ for _ in ()).throw(
            ExecutionContractError("T037_POLICY_UNVERIFIED", "task-specific isolation is unverified")
        ),
    )
    monkeypatch.setattr(
        windows_t037.T037SessionProcessFactory, "preflight",
        lambda *args: pytest.fail("T037 policy refusal must precede native preflight"),
    )
    try:
        observed = daemon._resolve_session_runtime(
            runtime.runtime_id, "project-a", "session-a", workspace,
        )
        assert observed is runtime
        assert resolved_calls and resolved_calls[0][:4] == (
            runtime.runtime_id, "project-a", "session-a", workspace,
        )
        with pytest.raises(ExecutionContractError) as captured:
            daemon._dispatch_session_start({
                "project_id": "project-a",
                "runtime_id": runtime.runtime_id,
                "idempotency_key": "no-birth-under-unverified-t037",
            }, {})
        assert captured.value.code == "T037_POLICY_UNVERIFIED"
        assert daemon._session_t037_process_factories == {}
        assert daemon._session_server_handles == {}
        assert candidate["gates"]["original_Popen_or_Windows_handle"] == "NOT_BORN"
    finally:
        daemon.close()
