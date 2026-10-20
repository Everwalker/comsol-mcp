from __future__ import annotations

import json
import os
import importlib.util
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

REPOSITORY = Path(os.environ.get("W26_R9_REPO_ROOT") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(REPOSITORY))
_CANDIDATE_ACTOR = os.environ.get("W26_R9_CANDIDATE_ACTOR")
if _CANDIDATE_ACTOR:
    _spec = importlib.util.spec_from_file_location("_w26_r9_lifecycle_test_candidate", _CANDIDATE_ACTOR)
    if _spec is None or _spec.loader is None:
        raise RuntimeError("R9 lifecycle scratch candidate cannot be loaded")
    actor = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(actor)
else:
    from tools import w26_application_lifecycle_r9_actor as actor
actor.SEALED_R9_HELPER = Path(actor.sealed_r9.__file__).resolve(strict=True)


def test_r9_lifecycle_loader_closure_is_exact_28_current_sources():
    records = actor._loader_records()
    closure = actor.sealed_r9._r9_current_application_loader_closure(records)
    expected = set(actor.sealed_r9.BASE["CURRENT_APPLICATION_LOADER_NAMES"])
    assert len(expected) == 28
    assert set(closure["members"]) == expected
    assert closure["root"] == str(REPOSITORY / "tools")
    assert closure["members"]["w26_sealed_data_r9.py"]["sha256"] == (
        actor._helpers().raw_file(REPOSITORY / "tools/w26_sealed_data_r9.py")[1]["sha256"]
    )
    assert closure["members"]["w26_application_sources_175_r9_prepared.lock.json"]["sha256"] == (
        actor.sealed_r9.R9_LOCK_SHA256
    )


@pytest.mark.skipif(
    not os.environ.get("W26_R9_APP_ROOT"),
    reason="NOT_RUN: local R9 application artifact unavailable; set W26_R9_APP_ROOT to opt in",
)
def test_r9_lifecycle_reader_accepts_exact_local_current175_r9_app():
    app_root = Path(os.environ["W26_R9_APP_ROOT"])
    if not app_root.is_dir():
        pytest.skip("NOT_RUN: configured local R9 application artifact path is unavailable")
    loader = actor._loader_records()
    descriptor = actor.build_current_application_profile(app_root, loader, actor.sealed_r9.BASE)
    profile = actor.current_application_profile(descriptor, actor.sealed_r9.BASE)
    assert descriptor["source_profile"] == "current175-r9"
    assert profile["source_provenance"]["source_profile"] == "current175-r9"
    assert profile["source_provenance"]["member_count"] == 175
    assert profile["wheel"]["sha256"] == json.loads(
        (app_root / "APPLICATION_BUNDLE_RESULT.json").read_text(encoding="utf-8")
    )["build"]["sha256"]
    assert profile["result"]["sha256"] != profile["wheel"]["sha256"]


def test_verify_inputs_temporarily_routes_through_r9_and_restores_legacy_reader(tmp_path, monkeypatch):
    sentinel = object()
    calls = []
    original_reader = actor.data.current_application_profile

    def r9_reader(profile, base):
        calls.append((profile, base))
        return sentinel

    monkeypatch.setattr(actor, "current_application_profile", r9_reader)
    monkeypatch.setattr(
        actor.lifecycle,
        "verify_inputs",
        lambda _root, _prepared: actor.data.current_application_profile({"source_profile": "current175-r9"}),
    )
    assert actor.verify_inputs(tmp_path / "fresh-lifecycle-fixture", {}) is sentinel
    assert len(calls) == 1
    assert calls[0][0] == {"source_profile": "current175-r9"}
    assert calls[0][1] is actor.sealed_r9.BASE
    assert actor.data.current_application_profile is original_reader


def test_inventory_routes_only_four_helper_argvs_without_changing_module_path(tmp_path, monkeypatch):
    original = actor.data.__file__
    base_inventory = actor.lifecycle.inventory
    descriptor = {"path": str(tmp_path / "EXTERNAL_DATA.json"), "sha256": "a" * 64}
    rows = base_inventory(tmp_path / "root", "/python", descriptor, {"members": []}, {"members": []})
    observed = []

    def inventory(*args):
        observed.append(actor.data.__file__)
        return rows

    monkeypatch.setattr(actor.lifecycle, "inventory", inventory)
    routed = actor._inventory_with_r9_helper(tmp_path / "root", "/python", descriptor, {}, {})
    phases = [row for row in routed if row["label"].startswith("data-")]
    assert [row["label"] for row in phases] == ["data-" + phase for phase in actor.R9_SEALED_PHASES]
    assert all(row["request_argv"][-4] == str(actor.SEALED_R9_HELPER) for row in phases)
    assert all(rows_item["request_argv"][-4] == str(Path(actor.data.__file__).with_name("w26_sealed_data.py"))
               for rows_item in rows if rows_item["label"].startswith("data-"))
    assert observed == [original]
    assert actor.data.__file__ == original


def test_r9_execution_context_restores_reader_inventory_and_import_loader_after_error(monkeypatch):
    original_reader = actor.data.current_application_profile
    original_path = actor.data.__file__
    original_inventory = actor.lifecycle.inventory
    original_spec_factory = importlib.util.spec_from_file_location
    observed = []
    sentinel = RuntimeError("synthetic inventory stop")

    def failing_inventory(*args, **kwargs):
        observed.append(actor.data.__file__)
        assert actor.data.__file__ == original_path
        raise sentinel

    monkeypatch.setattr(actor.lifecycle, "inventory", failing_inventory)
    with actor.r9_execution_context():
        assert actor.data.__file__ == original_path
        assert actor.data.current_application_profile is not original_reader
        assert importlib.util.spec_from_file_location is not original_spec_factory
        assert actor.lifecycle.inventory is not failing_inventory
        with pytest.raises(RuntimeError, match="synthetic inventory stop"):
            actor.lifecycle.inventory({}, "python", {}, {}, {})
        assert actor.data.__file__ == original_path
    assert observed == [original_path]
    assert actor.data.current_application_profile is original_reader
    assert actor.lifecycle.inventory is failing_inventory
    assert importlib.util.spec_from_file_location is original_spec_factory
    assert actor.data.__file__ == original_path


def test_r9_consumer_spec_hook_delegates_noncanonical_loads_unchanged(tmp_path, monkeypatch):
    original_factory = importlib.util.spec_from_file_location
    delegated = []

    def factory_spy(*args, **kwargs):
        spec = original_factory(*args, **kwargs)
        delegated.append(spec)
        return spec

    monkeypatch.setattr(importlib.util, "spec_from_file_location", factory_spy)
    unrelated = tmp_path / "unrelated_transition_copy.py"
    unrelated.write_text("VALUE = True\n", encoding="utf-8")
    with actor.r9_execution_context():
        for name in ("some_other_module", "release_transition_lifecycle"):
            result = importlib.util.spec_from_file_location(name, unrelated)
            assert result is delegated[-1]
            assert result.origin == str(unrelated)
    assert importlib.util.spec_from_file_location is factory_spy


def test_r9_execute_entry_restores_both_seams_even_when_actor_stops(tmp_path, monkeypatch):
    original_reader = actor.data.current_application_profile
    original_file = actor.data.__file__
    original_inventory = actor.lifecycle.inventory
    original_spec_factory = importlib.util.spec_from_file_location
    stop = RuntimeError("test stop inside existing actor.execute")

    def actor_entry(root, binding_path, binding_sha):
        assert root == tmp_path / "root"
        assert binding_path == tmp_path / "binding.json"
        assert binding_sha == "0" * 64
        assert actor.data.__file__ == original_file
        assert actor.data.current_application_profile is not original_reader
        assert actor.lifecycle.inventory is not original_inventory
        assert importlib.util.spec_from_file_location is not original_spec_factory
        raise stop

    monkeypatch.setattr(actor.actor, "execute", actor_entry)
    with pytest.raises(RuntimeError, match="test stop inside existing actor.execute"):
        actor.execute_entry(tmp_path / "root", tmp_path / "binding.json", "0" * 64)
    assert actor.data.current_application_profile is original_reader
    assert actor.lifecycle.inventory is original_inventory
    assert importlib.util.spec_from_file_location is original_spec_factory
    assert actor.data.__file__ == original_file


def test_actual_base_sealed_consumer_reaches_closed_admit_for_fixed_phases_only(tmp_path, monkeypatch):
    root = tmp_path / "fresh-lifecycle-fixture"
    descriptor = {"path": str(root / "EXTERNAL_DATA.json"), "sha256": "b" * 64}
    python = root / "transition-work" / "transition-venv" / "bin" / "python"
    profile = root / "transition-work" / "transition-evidence" / "network-deny-transition.sb"
    evidence = root / "transition-work" / "transition-evidence"
    evidence.mkdir(parents=True)
    original_data_path = actor.data.__file__
    original_reader = actor.data.current_application_profile
    original_inventory = actor.lifecycle.inventory
    original_spec_factory = importlib.util.spec_from_file_location
    original_capture = actor.lifecycle.release._capture
    popen_calls = []
    admissions = []

    def forbidden_popen(*args, **kwargs):
        popen_calls.append((args, kwargs))
        raise AssertionError("R9 software control must stop before Popen")

    monkeypatch.setattr(actor.lifecycle.release.subprocess, "Popen", forbidden_popen)
    with actor.r9_execution_context():
        inventory = actor.lifecycle.inventory(
            root, str(python), descriptor, {"members": []}, {"members": []}
        )
        data_rows = [row for row in inventory if row["label"].startswith("data-")]
        assert len(data_rows) == 4
        assert all(row["request_argv"][-4] == str(actor.SEALED_R9_HELPER) for row in data_rows)
        assert actor.data.__file__ == original_data_path

        module_path = Path(actor.transition_lifecycle.__file__).resolve(strict=True)
        spec = importlib.util.spec_from_file_location(
            "release_transition_lifecycle", module_path
        )
        assert spec is not None and spec.loader is not None
        actual_consumer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(actual_consumer)
        instance = object.__new__(actual_consumer.Lifecycle)
        instance.release = actor.lifecycle.release
        instance.external_descriptor = descriptor["path"]
        instance.external_descriptor_sha = descriptor["sha256"]
        instance.sandbox = "/usr/bin/sandbox-exec"
        instance.profile = profile
        instance.evidence = evidence
        instance.steps = []
        instance.failures = []

        launch = object.__new__(actor.lifecycle.LifecycleLaunch)
        launch.plan = {"inventory": inventory}
        launch.offline = None
        launch.pending_unknown = None
        launch.counts = {}

        def actual_admit_without_popen(command, **kwargs):
            row = launch.admit(command, kwargs)
            admissions.append({"label": row["label"], "argv": list(command), "cwd": kwargs.get("cwd")})
            return SimpleNamespace(returncode=0, stdout='{"success":true}', stderr="")

        with actor.lifecycle.release._controlled_launch_scope(
            SimpleNamespace(capture=actual_admit_without_popen)
        ):
            actual_consumer.Lifecycle.seed_old(instance, python)
            for phase in actor.R9_SEALED_PHASES[1:]:
                actual_consumer.Lifecycle.process(
                    instance, "direct-" + phase, python,
                    actual_consumer.SEALED_PHASE_SCRIPT,
                    actual_consumer.Lifecycle.external_arguments(instance, phase),
                )

            with pytest.raises(RuntimeError, match="fixed four-phase inventory"):
                actual_consumer.Lifecycle.external_arguments(instance, "unlisted")
            instance.external_descriptor_sha = "c" * 64
            with pytest.raises(RuntimeError, match="exact R9 inventory"):
                actual_consumer.Lifecycle.external_arguments(instance, "old-read")
            instance.external_descriptor_sha = descriptor["sha256"]

            original_admit = launch.admit

            def tamper_helper(command, kwargs):
                changed = list(command)
                changed[-4] = str(module_path.with_name("w26_sealed_data.py"))
                return original_admit(changed, kwargs)

            monkeypatch.setattr(launch, "admit", tamper_helper)
            with pytest.raises(RuntimeError, match="outside exact closed inventory"):
                actual_consumer.Lifecycle.process(
                    instance, "tampered-old-read", python,
                    actual_consumer.SEALED_PHASE_SCRIPT,
                    actual_consumer.Lifecycle.external_arguments(instance, "old-read"),
                )

    labels = [row["label"] for row in admissions]
    assert labels == ["data-old-read", "data-candidate-backup", "data-restore", "data-old-restored-read"]
    assert all(row["argv"][-4] == str(actor.SEALED_R9_HELPER) for row in admissions)
    assert len(popen_calls) == 0
    assert actor.data.__file__ == original_data_path
    assert actor.data.current_application_profile is original_reader
    assert actor.lifecycle.inventory is original_inventory
    assert importlib.util.spec_from_file_location is original_spec_factory
    assert actor.lifecycle.release._capture is original_capture
    assert actual_consumer.Lifecycle.external_arguments.__name__ == "external_arguments"
    assert actual_consumer.Lifecycle.external_arguments.__code__.co_code == (
        actor.transition_lifecycle.Lifecycle.external_arguments.__code__.co_code
    )
    assert actor.lifecycle.release.subprocess.Popen is forbidden_popen


def test_dynamic_consumer_method_restores_after_scoped_exception(tmp_path):
    root = tmp_path / "exception-fixture"
    descriptor = {"path": str(root / "EXTERNAL_DATA.json"), "sha256": "d" * 64}
    python = str(root / "transition-work" / "transition-venv" / "bin" / "python")
    original_reader = actor.data.current_application_profile
    original_file = actor.data.__file__
    original_inventory = actor.lifecycle.inventory
    original_factory = importlib.util.spec_from_file_location
    expected_code = actor.transition_lifecycle.Lifecycle.external_arguments.__code__
    actual_consumer = None

    with pytest.raises(RuntimeError, match="stop after dynamic consumer binding"):
        with actor.r9_execution_context():
            actor.lifecycle.inventory(root, python, descriptor, {"members": []}, {"members": []})
            path = Path(actor.transition_lifecycle.__file__).resolve(strict=True)
            spec = importlib.util.spec_from_file_location("release_transition_lifecycle", path)
            assert spec is not None and spec.loader is not None
            actual_consumer = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(actual_consumer)
            assert actual_consumer.Lifecycle.external_arguments.__name__ == "r9_external_arguments"
            raise RuntimeError("stop after dynamic consumer binding")

    assert actual_consumer is not None
    assert actual_consumer.Lifecycle.external_arguments.__name__ == "external_arguments"
    assert actual_consumer.Lifecycle.external_arguments.__code__.co_code == expected_code.co_code
    assert importlib.util.spec_from_file_location is original_factory
    assert actor.data.current_application_profile is original_reader
    assert actor.data.__file__ == original_file
    assert actor.lifecycle.inventory is original_inventory
