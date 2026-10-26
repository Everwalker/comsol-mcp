from __future__ import annotations

import contextlib
import hashlib
import importlib
import io
import json
import os
import pathlib
import sys
import types
import zipfile
from collections.abc import Mapping

import pytest

import test_full_project_release as existing_release_tests

release = existing_release_tests.release
REPOSITORY = next(
    parent for parent in pathlib.Path(existing_release_tests.__file__).resolve().parents
    if (parent / "pyproject.toml").is_file() and (parent / "tools" / "full_project_release.py").is_file()
)
PIP25_NUPKG_ENV = "COMSOL_MCP_PIP25_NUPKG"
PIP25_DEFAULT_NUPKG = REPOSITORY / "_state" / "universal-windows-host-package" / "python.3.12.10.nupkg"
PIP25_NUPKG_SHA256 = "0eb85c2dfccccf1b17352de4c397f69194035b7d37149eacc16f1147d93de3b8"
ENSUREPIP_WHEEL_MEMBER = "tools/Lib/ensurepip/_bundled/pip-25.0.1-py3-none-any.whl"
ENSUREPIP_WHEEL_SHA256 = "c46efd13b6aa8279f33f2864459c8ce587ea6a1a59ee20de055868d8f7688f7f"
PIP25_CONFIGURATION_SHA256 = "f8a3a893a8e1de11735cc3d014f275fc416306902c81ef914ab790b8b1cb9b3a"
R1_HELPER_ENV = "COMSOL_MCP_R1_HELPER"
R1_HELPER_SHA256 = "7d28e969a5090f915cd768543fee918e1bbe08080c2516960ea83ea486c09e73"


def _assert_same_environment(actual: object, expected: dict[str, str], message: str) -> None:
    if not isinstance(actual, Mapping) or dict(actual) != expected:
        raise AssertionError(message)


_RESERVED_ENVIRONMENT_KEYS = ("HOME", "home", "CODEX_HOME")


def _reserved_environment_values(environment: Mapping[str, str]) -> dict[str, tuple[bool, str | None]]:
    return {key: (key in environment, environment.get(key)) for key in _RESERVED_ENVIRONMENT_KEYS}


def _assert_reserved_environment_unchanged(
    left: Mapping[str, str], right: Mapping[str, str], message: str,
) -> None:
    if _reserved_environment_values(left) != _reserved_environment_values(right):
        raise AssertionError(message)


def _expected_pip_child_environment(parent: Mapping[str, str]) -> dict[str, str]:
    result = {key: value for key, value in parent.items() if not key.upper().startswith("PIP_")}
    result["PIP_CONFIG_FILE"] = release.os.devnull
    return result


def _pip25_nupkg_path() -> pathlib.Path:
    explicit = os.environ.get(PIP25_NUPKG_ENV)
    path = pathlib.Path(explicit) if explicit else PIP25_DEFAULT_NUPKG
    if not path.is_file():
        pytest.skip(
            "pinned pip25 external fixture is absent; set COMSOL_MCP_PIP25_NUPKG or provide "
            "the Python 3.12.10 NuGet package at the repository default"
        )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != PIP25_NUPKG_SHA256:
        raise AssertionError("pinned pip25 external fixture SHA-256 mismatch")
    return path


@contextlib.contextmanager
def _r1_helper_reference():
    explicit = os.environ.get(R1_HELPER_ENV)
    if not explicit:
        yield None
        return
    path = pathlib.Path(explicit)
    if not path.is_file():
        raise AssertionError("explicit frozen r1 helper path is missing")
    if hashlib.sha256(path.read_bytes()).hexdigest() != R1_HELPER_SHA256:
        raise AssertionError("frozen r1 helper SHA-256 mismatch")
    name = "full_project_release_r1_early_parser_reference"
    previous = sys.modules.get(name)
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    try:
        source = path.read_bytes()
        exec(compile(source, str(path), "exec"), module.__dict__)
        yield module
    finally:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous


@contextlib.contextmanager
def _embedded_pip25(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    with zipfile.ZipFile(_pip25_nupkg_path()) as runtime_archive:
        wheel_bytes = runtime_archive.read(ENSUREPIP_WHEEL_MEMBER)
    assert hashlib.sha256(wheel_bytes).hexdigest() == ENSUREPIP_WHEEL_SHA256
    pip_root = tmp_path / "pip-25.0.1"
    pip_root.mkdir()
    with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as wheel:
        configuration_source = wheel.read("pip/_internal/configuration.py")
        assert hashlib.sha256(configuration_source).hexdigest() == PIP25_CONFIGURATION_SHA256
        for name in wheel.namelist():
            if name.startswith("pip/") and not name.endswith("/"):
                destination = pip_root / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(wheel.read(name))

    previous_modules = {name: module for name, module in sys.modules.items()
                        if name == "pip" or name.startswith("pip.")}
    for name in previous_modules:
        sys.modules.pop(name, None)
    monkeypatch.syspath_prepend(str(pip_root))
    importlib.invalidate_caches()
    try:
        pip = importlib.import_module("pip")
        configuration = importlib.import_module("pip._internal.configuration")
        assert pip.__version__ == "25.0.1"
        yield pip, configuration
    finally:
        for name in tuple(sys.modules):
            if name == "pip" or name.startswith("pip."):
                sys.modules.pop(name, None)
        sys.modules.update(previous_modules)
        importlib.invalidate_caches()


@pytest.mark.parametrize("pip_destination", [
    "PIP_TARGET", "PIP_PREFIX", "PIP_ROOT", "PIP_CONFIG_FILE", "PIP_PYTHON", "pIp_PyThOn",
])
@pytest.mark.parametrize("force_reinstall", [False, True])
def test_pip_install_lock_isolated_child_oracle(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    pip_destination: str,
    force_reinstall: bool,
) -> None:
    venv_root = tmp_path / "venv"
    lexical_python = venv_root / "Scripts" / "python.exe"
    lexical_python.parent.mkdir(parents=True)
    base_python = tmp_path / "base" / "python.exe"
    base_python.parent.mkdir()
    base_python.write_text("synthetic interpreter target", encoding="utf-8")
    lexical_python.symlink_to(base_python)

    destinations = {
        "PIP_TARGET": tmp_path / "external-target",
        "PIP_PREFIX": tmp_path / "external-prefix",
        "PIP_ROOT": tmp_path / "external-root",
        "PIP_CONFIG_FILE": tmp_path / "hostile-pip.ini",
        "PIP_PYTHON": tmp_path / "alternate-python",
        "pIp_PyThOn": tmp_path / "mixed-case-alternate-python",
    }
    hostile_config = tmp_path / "hostile-pip.ini"
    hostile_config.write_text(
        "[global]\ntarget = " + str(tmp_path / "config-target") + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(pip_destination, str(destinations[pip_destination]))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "foreign-pythonpath"))
    monkeypatch.setenv("PYTHONUSERBASE", str(tmp_path / "foreign-userbase"))
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "foreign-pycache"))
    monkeypatch.setenv("PIP_CACHE_DIR", str(tmp_path / "foreign-pip-cache"))
    monkeypatch.setenv("PIP_LOG", str(tmp_path / "foreign-pip.log"))
    parent_environment = dict(release.os.environ)

    captured: list[tuple[list[str], dict[str, object]]] = []

    def inert_capture(command: list[str], **kwargs: object) -> types.SimpleNamespace:
        captured.append((list(command), dict(kwargs)))
        return types.SimpleNamespace(returncode=0, stdout="synthetic success", stderr="")

    owner = types.SimpleNamespace(capture=inert_capture)
    requirements = tmp_path / "requirements.lock"
    wheelhouse = tmp_path / "wheelhouse"
    requirements.write_text("synthetic lock only\n", encoding="utf-8")
    wheelhouse.mkdir()
    with release._controlled_launch_scope(owner):
        result = release.pip_install_lock(
            lexical_python, requirements, wheelhouse, force_reinstall=force_reinstall
        )

    assert len(captured) == 1
    argv, kwargs = captured[0]
    effective_env = kwargs.get("env")
    if effective_env is None:
        # subprocess.run(env=None) inherits the caller's complete environment.
        effective_env = dict(release.os.environ)
        pytest.fail(
            "OLD_SOURCE_FAIL: inert original capture inherited " + pip_destination
            + "; no exact isolated child environment reached _controlled_launch_scope"
        )

    assert argv[:7] == [str(lexical_python), "-I", "-B", "-m", "pip", "--isolated", "install"]
    assert argv[7] == ("--force-reinstall" if force_reinstall else "--no-index")
    if force_reinstall:
        assert argv[8] == "--no-index"
    assert argv[0] == str(lexical_python)
    assert "--no-index" in argv
    assert "--only-binary=:all:" in argv
    assert "--disable-pip-version-check" in argv
    assert "--no-input" in argv
    assert "--no-cache-dir" in argv
    assert "--find-links" in argv and str(wheelhouse) in argv
    assert "--require-hashes" in argv
    assert "--requirement" in argv and str(requirements) in argv
    assert ("--force-reinstall" in argv) is force_reinstall
    assert isinstance(effective_env, dict)
    assert effective_env is not release.os.environ
    expected_environment = _expected_pip_child_environment(parent_environment)
    _assert_same_environment(effective_env, expected_environment,
                             "pip child environment changed outside its explicit policy")
    _assert_reserved_environment_unchanged(
        parent_environment, effective_env,
        "pip child reserved environment differs from caller values",
    )
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert "env" not in result
    _assert_same_environment(release.os.environ, parent_environment, "pip builder changed the parent environment")
    _assert_reserved_environment_unchanged(
        parent_environment, release.os.environ, "pip builder changed reserved environment",
    )
    assert not any(key.upper().startswith("PIP_") and key != "PIP_CONFIG_FILE" for key in effective_env)


def test_run_record_default_remains_compatible_and_does_not_report_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[list[str], dict[str, object]]] = []

    def inert_capture(command: list[str], **kwargs: object) -> types.SimpleNamespace:
        captured.append((list(command), dict(kwargs)))
        return types.SimpleNamespace(returncode=0, stdout="synthetic", stderr="")

    monkeypatch.setattr(release, "_capture", inert_capture)
    result = release.run_record(["synthetic-command"])
    assert captured == [(["synthetic-command"], {"cwd": None, "capture_output": True, "text": True})]
    assert "env" not in result


def test_run_record_explicit_pip_environment_reaches_controlled_owner_bridge_without_reporting_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PIP_CACHE_DIR", "/synthetic-owner-bridge-cache")
    monkeypatch.setenv("PIP_PYTHON", "/synthetic-owner-bridge-python")
    monkeypatch.setenv("pIp_PyThOn", "/synthetic-owner-bridge-mixed-case-python")
    parent_environment = dict(release.os.environ)
    command, child_environment = release._isolated_pip_invocation(
        pathlib.Path("/synthetic/venv/python"), ["check", "--no-cache-dir"]
    )
    captured: list[tuple[list[str], dict[str, object]]] = []

    def inert_capture(command: list[str], **kwargs: object) -> types.SimpleNamespace:
        captured.append((list(command), dict(kwargs)))
        return types.SimpleNamespace(returncode=0, stdout="synthetic", stderr="")

    owner = types.SimpleNamespace(capture=inert_capture)
    with release._controlled_launch_scope(owner):
        result = release.run_record(command, env=child_environment)

    assert len(captured) == 1
    _argv, kwargs = captured[0]
    owner_environment = kwargs.get("env")
    _assert_same_environment(owner_environment, child_environment,
                             "owner bridge did not preserve the pip child environment")
    _assert_reserved_environment_unchanged(
        parent_environment, child_environment, "pip policy overlay changed reserved environment",
    )
    _assert_reserved_environment_unchanged(
        parent_environment, release.os.environ, "run_record changed reserved environment",
    )
    _assert_same_environment(release.os.environ, parent_environment, "run_record changed the parent environment")
    assert not any(key.upper().startswith("PIP_") and key != "PIP_CONFIG_FILE" for key in owner_environment)
    assert "env" not in result


def test_doctor_pip_check_uses_isolated_no_log_child_without_changing_home(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    venv = tmp_path / "doctor-venv"
    binary_dir = venv / "bin"
    binary_dir.mkdir(parents=True)
    venv_python = binary_dir / "python"
    venv_python.write_text("inert interpreter placeholder\n", encoding="utf-8")
    launcher = binary_dir / "comsol-mcp"
    identity = {
        "python": "3.12.0", "executable": str(venv_python), "prefix": str(venv),
        "version": "0.1.9", "module": str(venv / "lib/python3.12/site-packages/comsol_mcp"),
        "entry_points": [{"name": "comsol-mcp", "value": "comsol_mcp.mcp_server:main"}],
        "entry_point_path": str(launcher), "entry_point_exists": True, "package_file_count": 4,
        "java_sources": ["Worker.java"], "action_catalogs": ["data/02_ACTION_CATALOG.json"],
        "schemas": ["data/schema.json"],
    }
    log_target = tmp_path / "synthetic-pip.log"
    monkeypatch.setenv("PIP_LOG", str(log_target))
    monkeypatch.setenv("PIP_CONFIG_FILE", str(tmp_path / "hostile-pip.ini"))
    monkeypatch.setenv("PIP_CACHE_DIR", str(tmp_path / "synthetic-cache"))
    monkeypatch.setenv("PIP_PYTHON", str(tmp_path / "alternate-python"))
    monkeypatch.setenv("pIp_PyThOn", str(tmp_path / "mixed-case-alternate-python"))
    parent_environment = dict(release.os.environ)
    captured: list[tuple[list[str], dict[str, object]]] = []

    def inert_capture(command: list[str], **kwargs: object) -> types.SimpleNamespace:
        captured.append((list(command), dict(kwargs)))
        stdout = json.dumps(identity) if "-c" in command else "No broken requirements found.\n"
        return types.SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    owner = types.SimpleNamespace(capture=inert_capture)
    with release._controlled_launch_scope(owner):
        result = release.doctor(venv_python)

    assert result["status"] == "PASS"
    assert len(captured) == 2
    identity_argv, identity_kwargs = captured[0]
    assert identity_argv[:4] == [str(venv_python), "-I", "-B", "-c"]
    assert "env" not in identity_kwargs
    check_argv, check_kwargs = captured[1]
    assert check_argv[:7] == [str(venv_python), "-I", "-B", "-m", "pip", "--isolated", "check"]
    assert check_argv[7:] == ["--no-cache-dir"]
    assert "--log" not in check_argv
    assert isinstance(check_kwargs.get("env"), dict)
    expected_child_environment = _expected_pip_child_environment(parent_environment)
    _assert_same_environment(check_kwargs.get("env"), expected_child_environment,
                             "doctor pip-check environment changed outside its explicit policy")
    _assert_reserved_environment_unchanged(parent_environment, check_kwargs["env"],
                           "doctor pip-check child reserved environment differs from caller values")
    _assert_same_environment(release.os.environ, parent_environment,
                             "doctor changed the parent environment")
    _assert_reserved_environment_unchanged(
        parent_environment, release.os.environ, "doctor changed reserved environment",
    )
    assert not any(key.upper().startswith("PIP_") and key != "PIP_CONFIG_FILE" for key in check_kwargs["env"])
    assert "env" not in result["pip_check"]
    assert not log_target.exists()


def test_pip25_global_parser_early_pip_python_r1_fail_r2_pass(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alternate_python = "/synthetic/alternate-venv/bin/python"
    monkeypatch.setenv("PIP_PYTHON", alternate_python)
    monkeypatch.setenv("pIp_PyThOn", "/synthetic/mixed-case-python")
    monkeypatch.setenv("PIP_TARGET", "/synthetic/external-target")
    monkeypatch.setenv("PIP_LOG", "/synthetic/external-pip.log")
    monkeypatch.setenv("PIP_CONFIG_FILE", "/synthetic/hostile-pip.ini")
    monkeypatch.setenv("PIP_CACHE_DIR", "/synthetic/external-cache")
    parent_environment = dict(release.os.environ)
    venv_python = pathlib.Path("/synthetic/original-venv/bin/python")
    pip_args = ["check", "--no-cache-dir"]

    with _r1_helper_reference() as r1_release:
        if r1_release is None:
            old_command, _ = release._isolated_pip_invocation(venv_python, pip_args)
            old_child_environment = dict(parent_environment)
            old_child_environment["PIP_CONFIG_FILE"] = release.os.devnull
            old_builder_reference = "portable-r1-policy-reference"
        else:
            old_command, old_child_environment = r1_release._isolated_pip_invocation(venv_python, pip_args)
            old_builder_reference = "sha256:" + R1_HELPER_SHA256
        new_command, new_child_environment = release._isolated_pip_invocation(venv_python, pip_args)

    expected_old_environment = dict(parent_environment)
    expected_old_environment["PIP_CONFIG_FILE"] = release.os.devnull
    _assert_same_environment(old_child_environment, expected_old_environment,
                             "frozen r1 policy reference did not preserve the original PIP_PYTHON input")
    _assert_reserved_environment_unchanged(parent_environment, old_child_environment,
                           "r1 policy reference changed reserved environment")
    _assert_same_environment(new_child_environment, _expected_pip_child_environment(parent_environment),
                             "r2 child policy did not filter all case-insensitive PIP_* names")
    _assert_reserved_environment_unchanged(
        parent_environment, new_child_environment, "r2 child policy changed reserved environment",
    )
    assert any(key.upper() == "PIP_PYTHON" for key in old_child_environment)
    assert not any(key.upper() == "PIP_PYTHON" for key in new_child_environment)
    assert old_command[5:] == new_command[5:] == ["--isolated", "check", "--no-cache-dir"]

    with _embedded_pip25(tmp_path, monkeypatch) as (pip, _configuration):
        main_parser = importlib.import_module("pip._internal.cli.main_parser")
        synthetic_interpreter = "/synthetic/alternate-venv/bin/python"
        identification_calls: list[str] = []
        internal_spawns: list[list[str]] = []

        class InternalSpawnDenied(RuntimeError):
            pass

        def identify_synthetic_interpreter(value: str) -> str:
            identification_calls.append(value)
            return synthetic_interpreter

        def record_and_deny_spawn(command: list[str], *_args: object, **_kwargs: object) -> None:
            internal_spawns.append(list(command))
            raise InternalSpawnDenied("synthetic pip internal spawn denied before process creation")

        monkeypatch.setattr(main_parser, "identify_python_interpreter", identify_synthetic_interpreter)
        monkeypatch.setattr(main_parser.subprocess, "run", record_and_deny_spawn)

        old_parser_environment = dict(old_child_environment)
        with monkeypatch.context() as old_child:
            old_child.setattr(release.os, "environ", old_parser_environment)
            with pytest.raises(InternalSpawnDenied) as old_error:
                main_parser.parse_command(old_command[5:])
        if len(internal_spawns) != 1 or not identification_calls:
            raise AssertionError("r1 global parser did not reach the inert PIP_PYTHON spawn leaf")
        old_spawn_argv = internal_spawns[0]
        if not old_spawn_argv or old_spawn_argv[0] != synthetic_interpreter:
            raise AssertionError("r1 parser spawn leaf did not use the synthetic alternate interpreter")
        if str(old_error.value) != "synthetic pip internal spawn denied before process creation":
            raise AssertionError("r1 parser's first inert-leaf error changed")
        print("PIP25_R1_EXPECTED_FAIL=global parse_command attempted inner spawn")
        print("PIP25_R1_BUILDER=" + old_builder_reference)
        print("PIP25_R1_INNER_SPAWN_ARGV=" + json.dumps(old_spawn_argv, separators=(",", ":")))
        print("PIP25_R1_FIRST_ERROR=" + type(old_error.value).__name__ + ": " + str(old_error.value))

        internal_spawns.clear()
        identification_calls.clear()
        new_parser_environment = dict(new_child_environment)
        with monkeypatch.context() as new_child:
            new_child.setattr(release.os, "environ", new_parser_environment)
            command_name, command_args = main_parser.parse_command(new_command[5:])
        if command_name != "check" or command_args != ["--isolated", "--no-cache-dir"]:
            raise AssertionError("r2 global parse_command did not reach the intended isolated leaf")
        if internal_spawns or identification_calls:
            raise AssertionError("r2 global parser reached the PIP_PYTHON identification/spawn leaf")
        _assert_same_environment(release.os.environ, parent_environment,
                                 "pip25 parser oracle changed the parent environment")
        _assert_reserved_environment_unchanged(parent_environment, release.os.environ,
                               "pip25 parser oracle changed reserved environment")
        print("PIP25_R2_PASS=global parse_command selected check with zero inner spawn")
        print("PIP25_R2_REMOVED_MIXED_CASE_PIP_KEYS=true; parent_environment=unchanged")
        assert pip.__version__ == "25.0.1"


def test_transition_candidate_rollback_and_cleanup_use_isolated_pip_environment(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hostile = {
        "PIP_TARGET": str(tmp_path / "outside-target"),
        "PIP_PREFIX": str(tmp_path / "outside-prefix"),
        "PIP_ROOT": str(tmp_path / "outside-root"),
        "PIP_CONFIG_FILE": str(tmp_path / "outside-pip.ini"),
        "PIP_CACHE_DIR": str(tmp_path / "outside-cache"),
        "PIP_LOG": str(tmp_path / "outside-pip.log"),
        "PIP_PYTHON": str(tmp_path / "alternate-python"),
        "pIp_PyThOn": str(tmp_path / "mixed-case-alternate-python"),
        "PYTHONPATH": str(tmp_path / "outside-pythonpath"),
        "PYTHONUSERBASE": str(tmp_path / "outside-userbase"),
        "PYTHONPYCACHEPREFIX": str(tmp_path / "outside-pycache"),
    }
    for key, value in hostile.items():
        monkeypatch.setenv(key, value)
    parent_environment = dict(release.os.environ)

    result, _state, _events = existing_release_tests._transition_fixture(tmp_path, monkeypatch)
    assert result["status"] == "PASS_ARTIFACT_ROLLBACK_MIGRATION_UNVERIFIED"
    records = result["_fixture_pip_invocations"]
    assert len(records) == 3
    expected_environment = _expected_pip_child_environment(parent_environment)
    for argv, child_environment in records:
        _assert_same_environment(child_environment, expected_environment,
                                 "transition child environment changed outside pip policy")
        _assert_reserved_environment_unchanged(parent_environment, child_environment,
                               "transition pip child reserved environment differs from caller values")
        assert not any(key.upper().startswith("PIP_") and key != "PIP_CONFIG_FILE"
                       for key in child_environment)
        assert argv[:3] == ["/usr/bin/sandbox-exec", "-f", argv[2]]
        command = argv[3:]
        assert command[:6] == [str(tmp_path / "transition-work" / "transition-venv" / "bin" / "python"),
                               "-I", "-B", "-m", "pip", "--isolated"]
        if "install" in command:
            assert command[6] == "install"
            assert "--no-index" in command
            assert "--only-binary=:all:" in command
            assert "--disable-pip-version-check" in command
            assert "--no-input" in command
            assert "--no-cache-dir" in command
            assert "--force-reinstall" in command
            assert "--require-hashes" in command
            assert "--requirement" in command
        else:
            assert command[6] == "uninstall"
            assert "--yes" in command
    _assert_same_environment(release.os.environ, parent_environment, "transition changed the parent environment")
    _assert_reserved_environment_unchanged(
        parent_environment, release.os.environ, "transition changed reserved environment",
    )


def test_pinned_ensurepip_pip25_ignores_pip_environment_and_global_user_site_config(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _embedded_pip25(tmp_path, monkeypatch) as (_pip, configuration):
        kinds = configuration.kinds
        global_config = tmp_path / "global.ini"
        user_config = tmp_path / "user.ini"
        site_config = tmp_path / "site.ini"
        environment_config = tmp_path / "environment.ini"
        config_values = {
            global_config: "[global]\ntarget = " + str(tmp_path / "global-target") + "\n",
            user_config: "[global]\nprefix = " + str(tmp_path / "user-prefix") + "\n",
            site_config: "[global]\nroot = " + str(tmp_path / "site-root") + "\n",
            environment_config: "[global]\ntarget = " + str(tmp_path / "environment-target") + "\n",
        }
        for path, contents in config_values.items():
            path.write_text(contents, encoding="utf-8")
        synthetic_files = {
            kinds.GLOBAL: [str(global_config)],
            kinds.USER: [str(user_config)],
            kinds.SITE: [str(site_config)],
        }
        monkeypatch.setattr(configuration, "get_configuration_files", lambda: synthetic_files)
        destinations = {
            "PIP_TARGET": str(tmp_path / "env-target"),
            "PIP_PREFIX": str(tmp_path / "env-prefix"),
            "PIP_ROOT": str(tmp_path / "env-root"),
        }

        for key, value in destinations.items():
            monkeypatch.setenv(key, value)
        monkeypatch.delenv("PIP_CONFIG_FILE", raising=False)
        unisolated = configuration.Configuration(isolated=False)
        unisolated.load()
        unisolated_options = dict(unisolated.items())
        assert unisolated_options[":env:.target"] == destinations["PIP_TARGET"]
        assert unisolated_options[":env:.prefix"] == destinations["PIP_PREFIX"]
        assert unisolated_options[":env:.root"] == destinations["PIP_ROOT"]
        assert unisolated_options["global.target"] == str(tmp_path / "global-target")
        assert unisolated_options["global.prefix"] == str(tmp_path / "user-prefix")
        assert unisolated_options["global.root"] == str(tmp_path / "site-root")

        monkeypatch.setenv("PIP_CONFIG_FILE", str(environment_config))
        custom_file = configuration.Configuration(isolated=False)
        custom_file.load()
        custom_file_options = dict(custom_file.items())
        assert custom_file_options["global.target"] == str(tmp_path / "environment-target")

        isolated_without_devnull = configuration.Configuration(isolated=True)
        isolated_without_devnull.load()
        isolated_file_options = dict(isolated_without_devnull.items())
        assert isolated_file_options["global.target"] == str(tmp_path / "environment-target")
        assert isolated_file_options["global.root"] == str(tmp_path / "site-root")
        assert ":env:.target" not in isolated_file_options

        parent_environment = dict(release.os.environ)
        pip_command, child_environment = release._isolated_pip_invocation(
            tmp_path / "venv" / "Scripts" / "python.exe", ["install"]
        )
        assert pip_command[:7] == [str(tmp_path / "venv" / "Scripts" / "python.exe"),
                                   "-I", "-B", "-m", "pip", "--isolated", "install"]
        assert child_environment is not release.os.environ
        expected_child_environment = _expected_pip_child_environment(parent_environment)
        _assert_same_environment(child_environment, expected_child_environment,
                                 "isolated pip environment did not filter PIP_* keys")
        _assert_reserved_environment_unchanged(parent_environment, child_environment,
                               "isolated pip environment changed reserved environment")
        isolated_values: list[dict[str, object]] = []
        with monkeypatch.context() as child:
            for key, value in destinations.items():
                child.setenv(key, value)
            child.setenv("PIP_CONFIG_FILE", release.os.devnull)
            isolated = configuration.Configuration(isolated=True)
            isolated.load()
            isolated_values.extend(dict(isolated.items()).values())
        assert isolated_values == []
        _assert_same_environment(release.os.environ, parent_environment,
                                 "pip loader test changed the parent environment")
