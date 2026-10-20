from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools import w25_macos_owned_desktop_actor_r7 as actor
from comsol_mcp._desktop_macos import MacOSFrameworkGateway
from comsol_mcp._desktop_service import DesktopOperationError, WindowIdentity


IDENTITY = WindowIdentity("macos", "CGWindow:7", 123, "birth:123", "uid:501", "session:123", "6.4.0")


class _Clock:
    def __init__(self):
        self.value = 0.0

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def _ax_error(attribute: str, scope: str, code: int, *, transient: bool = True) -> DesktopOperationError:
    name = MacOSFrameworkGateway._ax_error_name(code)
    return DesktopOperationError(
        "AX_ATTRIBUTE_TRANSIENT" if transient else "AX_ATTRIBUTE_UNAVAILABLE",
        f"Accessibility attribute read failed ({name}, {code}): {attribute} in {scope}",
        diagnostics={
            "kind": "AX_ERROR", "ax_error_code": code, "ax_error_name": name,
            "attribute": attribute, "scope": scope, "readiness_transient": transient,
        },
    )


def test_position_readiness_recovers_only_after_same_owned_identity_revalidation():
    clock = _Clock()

    class Observer:
        calls = 0

        def enumerate_windows(self):
            self.calls += 1
            return [SimpleNamespace(identity=IDENTITY)]

    class Adapter:
        calls = 0

        def inspect_accessibility(self, identity, **_limits):
            assert identity == IDENTITY
            self.calls += 1
            if self.calls == 1:
                raise _ax_error("AXPosition", "window_association", -25204)
            return {"nodes": [], "truncated": False}

    observer, adapter, attempts = Observer(), Adapter(), []
    tree = actor._inspect_owned_window_with_readiness(
        observer, adapter, IDENTITY, deadline=60, attempts=attempts,
        clock=clock.now, sleeper=clock.sleep,
    )
    assert tree["truncated"] is False
    assert adapter.calls == 2 and observer.calls == 1
    assert [row["status"] for row in attempts] == ["RETRY_AX_WINDOW_GEOMETRY_ASSOCIATION", "PASS"]
    assert attempts[0]["diagnostics"]["attribute"] == "AXPosition"
    assert attempts[0]["diagnostics"]["scope"] == "window_association"


@pytest.mark.parametrize("attribute,scope,code", [
    ("AXWindows", "owned_window_tree", -25204),
    ("AXPosition", "application_window_map", -25204),
    ("AXSize", "window_association", -25205),
    ("AXSize", "window_association", -25211),
])
def test_wrong_scope_unsupported_and_disabled_ax_errors_are_terminal(attribute, scope, code):
    clock = _Clock()
    class Observer:
        calls = 0

        def enumerate_windows(self):
            self.calls += 1
            return [SimpleNamespace(identity=IDENTITY)]

    class Adapter:
        calls = 0

        def inspect_accessibility(self, *_args, **_kwargs):
            self.calls += 1
            raise _ax_error(attribute, scope, code, transient=False)

    observer, adapter, attempts = Observer(), Adapter(), []
    with pytest.raises(DesktopOperationError) as caught:
        actor._inspect_owned_window_with_readiness(
            observer, adapter, IDENTITY, deadline=60, attempts=attempts,
            clock=clock.now, sleeper=clock.sleep,
        )
    assert caught.value.diagnostics["ax_error_code"] == code
    assert adapter.calls == 1 and observer.calls == 0
    assert attempts[0]["status"] == "FAIL_TERMINAL"


def test_no_value_size_retries_but_changed_window_identity_fails_closed():
    clock = _Clock()
    class Observer:
        calls = 0

        def enumerate_windows(self):
            self.calls += 1
            reused = WindowIdentity("macos", "CGWindow:8", 123, "birth:123", "uid:501", "session:123", "6.4.0")
            return [SimpleNamespace(identity=reused)]

    class Adapter:
        calls = 0

        def inspect_accessibility(self, *_args, **_kwargs):
            self.calls += 1
            raise _ax_error("AXSize", "window_association", -25212)

    observer, adapter = Observer(), Adapter()
    with pytest.raises(DesktopOperationError) as caught:
        actor._inspect_owned_window_with_readiness(
            observer, adapter, IDENTITY, deadline=60, clock=clock.now, sleeper=clock.sleep,
        )
    assert caught.value.code == "WINDOW_IDENTITY_CHANGED"
    assert caught.value.diagnostics["last_ax_error"]["ax_error_code"] == -25212
    assert adapter.calls == 1 and observer.calls == 1


def test_geometry_and_window_map_errors_share_one_nonresetting_deadline(monkeypatch):
    clock = _Clock()
    fixed_deadline = 60.0
    monkeypatch.setattr(actor, "AX_READINESS_SECONDS", 60)

    class Observer:
        calls = 0

        def enumerate_windows(self):
            self.calls += 1
            return [SimpleNamespace(identity=IDENTITY)]

    class Adapter:
        calls = 0

        def inspect_accessibility(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                clock.value += 40
                raise _ax_error("AXWindows", "application_window_map", -25204)
            clock.value += 20
            raise _ax_error("AXSize", "window_association", -25212)

    observer, adapter, attempts = Observer(), Adapter(), []
    with pytest.raises(DesktopOperationError) as caught:
        actor._inspect_owned_window_with_readiness(
            observer, adapter, IDENTITY, deadline=100, readiness_deadline=fixed_deadline,
            attempts=attempts, clock=clock.now, sleeper=clock.sleep,
        )
    assert caught.value.code == "AX_READINESS_TIMEOUT"
    assert caught.value.diagnostics["last_ax_error"]["attribute"] == "AXSize"
    assert caught.value.diagnostics["readiness_deadline_monotonic"] == fixed_deadline
    assert adapter.calls == 2 and observer.calls == 1
    assert [row["status"] for row in attempts] == [
        "RETRY_AX_WINDOWS_READINESS", "RETRY_AX_WINDOW_GEOMETRY_ASSOCIATION",
    ]


def test_late_success_does_not_extend_readiness_deadline():
    clock = _Clock()

    class Observer:
        def enumerate_windows(self):
            return [SimpleNamespace(identity=IDENTITY)]

    class Adapter:
        calls = 0

        def inspect_accessibility(self, *_args, **_kwargs):
            self.calls += 1
            clock.value = 61
            return {"nodes": [], "truncated": False}

    adapter, attempts = Adapter(), []
    with pytest.raises(DesktopOperationError) as caught:
        actor._inspect_owned_window_with_readiness(
            Observer(), adapter, IDENTITY, deadline=100, attempts=attempts,
            clock=clock.now, sleeper=clock.sleep,
        )
    assert caught.value.code == "AX_READINESS_TIMEOUT"
    assert adapter.calls == 1
    assert attempts[0]["status"] == "FAIL_READINESS_DEADLINE"


def _r7_app_actual_review():
    return {
        "actual_network_denial_probes": 2,
        "archives": {
            "macos_arm64": {"all_zip_members_hashed": 58, "wheel_count": 42},
            "macos_x86_64": {"all_zip_members_hashed": 60, "wheel_count": 42},
            "win_amd64": {"all_zip_members_hashed": 47, "wheel_count": 35},
        },
        "birth_terminal_group_absent": True,
        "current_R7_install_helpers": "newsourceclosure separatelyreviewed_required; noApp7a rebuild needed",
        "new_application_wheel_RECORD_members_verified": 178,
        "original_captures": 2,
        "package_members_exact_final_R7_lock": 172,
        "result_sha256": "a" * 64,
        "scope": (
            "Actual3architecture packaging only; sourceexternalinstall/GUI/science/Windows/Intelhardware/"
            "finalreview NOT_RUN. R7versionedreader laterchanged forinstall only; no historicalactorclosureretrofit asserted."
        ),
        "status": "PASS_SCOPED_R7_APP7A_ACTUAL_ARCHIVES_NOT_RELEASE",
        "unchanged_dependency_wheels_byte_equal_R6_and_inherited_RECORD_audit": 116,
        "wheel_sha256": "b" * 64,
    }


def test_r7_actual_app_archive_review_accepts_only_exact_bound_contract():
    assert actor._verify_r7_app_actual_review(
        _r7_app_actual_review(), result_sha256="a" * 64, wheel_sha256="b" * 64,
    ) is None


def test_r7_source_lock_record_accepts_only_exact_absolute_or_repo_relative_path(tmp_path, monkeypatch):
    absolute = actor.file_record(actor.R7_LOCK)
    relative = {**absolute, "path": actor.R7_LOCK.relative_to(actor.REPOSITORY).as_posix()}
    monkeypatch.chdir(tmp_path)
    assert actor._capture_r7_source_lock(absolute) == absolute
    assert actor._capture_r7_source_lock(relative) == absolute
    relative["path"] = "../tools/w26_application_sources_175_r7_final.lock.json"
    with pytest.raises(RuntimeError, match="exact locked file"):
        actor._capture_r7_source_lock(relative)


@pytest.mark.parametrize("damage", ["missing_count", "wrong_result_sha", "false_group", "wrong_wheel_sha"])
def test_r7_actual_app_archive_review_rejects_missing_or_mismatched_evidence(damage):
    review = _r7_app_actual_review()
    if damage == "missing_count":
        del review["package_members_exact_final_R7_lock"]
    elif damage == "wrong_result_sha":
        review["result_sha256"] = "c" * 64
    elif damage == "false_group":
        review["birth_terminal_group_absent"] = False
    else:
        review["wheel_sha256"] = "d" * 64
    with pytest.raises(RuntimeError, match="R7 contract|exact R7 counts"):
        actor._verify_r7_app_actual_review(review, result_sha256="a" * 64, wheel_sha256="b" * 64)
