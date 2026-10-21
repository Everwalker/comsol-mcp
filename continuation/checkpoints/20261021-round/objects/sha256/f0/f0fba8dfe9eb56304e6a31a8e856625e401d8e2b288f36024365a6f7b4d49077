from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import (
    w25_current_r7_offline_install_actor as install_r7,
    w25_macos_owned_desktop_actor_r7 as desktop_r7,
    w26_application_bundle as app,
    w26_application_lifecycle_r7_actor as lifecycle_r7,
    w26_sealed_data_r6 as sealed_r6,
    w26_sealed_data_r7 as sealed_r7,
)
from tools.w26_prepare_current_inputs import _helpers


def test_r7_r8_locks_remain_historical_and_r9_successor_matches_live_package():
    assert app.LATEST_SOURCE_PROFILE == "current175-r9"
    assert app.CURRENT175_R7_SOURCE_PROFILE == "current175-r7"
    assert app.CURRENT175_R8_SOURCE_PROFILE == "current175-r8"
    assert app.CURRENT175_R9_SOURCE_PROFILE == "current175-r9"
    assert app.current175_source_lock("current175-r7") == app.CURRENT175_R7_SOURCE_LOCK
    assert app.current175_source_lock("current175-r8") == app.CURRENT175_R8_SOURCE_LOCK
    assert app.current175_source_lock("current175-r9") == app.CURRENT175_R9_SOURCE_LOCK
    assert app.CURRENT175_R7_SOURCE_LOCK.name == "w26_application_sources_175_r7_final.lock.json"
    source = Path(__file__).resolve().parents[1]
    members = app.source_members(source)
    lock_record = _helpers().raw_file(app.CURRENT175_R7_SOURCE_LOCK)[1]
    lock = json.loads(_helpers().raw_file(app.CURRENT175_R7_SOURCE_LOCK)[0])
    assert lock_record["sha256"] == "6062c06724846eeb294c10767bd25579ffe44d1035bdcc505d3f843a01c1ab8a"
    assert lock["profile"] == "current175-r7"
    assert (lock["member_count"], lock["package_count"], lock["root_count"]) == (175, 172, 3)
    assert len(members) == 175
    assert lock["members"]["comsol_mcp/_desktop_macos.py"]["sha256"] == desktop_r7.APP_SOURCE_HASHES["comsol_mcp._desktop_macos"]
    assert lock["members"]["comsol_mcp/_control_daemon.py"]["sha256"] == _helpers().raw_file(source / "comsol_mcp/_control_daemon.py")[1]["sha256"]
    r8_raw, r8_record = _helpers().raw_file(app.CURRENT175_R8_SOURCE_LOCK)
    r8_lock = json.loads(r8_raw)
    assert r8_record["sha256"] == "11ff744bc2bd7d860c75fced51575ab99f8c9fa13f959b1c38b0d79c13823a12"
    assert r8_lock["profile"] == "current175-r8"
    assert (r8_lock["member_count"], r8_lock["package_count"], r8_lock["root_count"]) == (175, 172, 3)
    r9_raw, r9_record = _helpers().raw_file(app.CURRENT175_R9_SOURCE_LOCK)
    r9_lock = json.loads(r9_raw)
    assert r9_lock["profile"] == "current175-r9"
    assert (r9_lock["member_count"], r9_lock["package_count"], r9_lock["root_count"]) == (175, 172, 3)
    assert app.source_contract(members, app.CURRENT175_R9_SOURCE_PROFILE) == r9_record
    changed_r7_to_r8 = {
        name for name, path in members.items()
        if r8_lock["members"][name] != lock["members"][name]
    }
    assert changed_r7_to_r8 == {
        "comsol_mcp/data/science_recipes/SOURCE_ORIGINS.json",
        "comsol_mcp/data/science_recipes/java/W24CureCouponFixture.java",
    }
    changed_r8_to_r9 = {
        name for name in members
        if r9_lock["members"][name] != r8_lock["members"][name]
    }
    assert changed_r8_to_r9 == {
        "comsol_mcp/_desktop_macos.py",
        "comsol_mcp/_desktop_service.py",
    }


def test_r7_reader_closes_its_own_lock_without_changing_the_r6_reader():
    r7_names = sealed_r7.BASE["CURRENT_APPLICATION_LOADER_NAMES"]
    r6_names = sealed_r6.BASE["CURRENT_APPLICATION_LOADER_NAMES"]
    assert sealed_r7.R7_LOCK == app.CURRENT175_R7_SOURCE_LOCK
    assert sealed_r7.R7_LOCK.name in r7_names
    assert sealed_r7.R7_LOCK.name not in r6_names
    assert r6_names == frozenset(set(r7_names) - {sealed_r7.R7_LOCK.name})
    module_paths = sealed_r7.BASE["CURRENT_APPLICATION_LOADER_MODULE_PATHS"]
    assert {Path(path).name for path in module_paths.values()} == {
        name for name in r7_names if name.endswith(".py")
    }


def test_r7_lifecycle_loader_uses_exact_extended_r7_closure_and_r6_rejects_it():
    sources = lifecycle_r7._loader_records()
    r7_closure = sealed_r7.BASE["current_application_loader_closure"](sources)
    r7_names = sealed_r7.BASE["CURRENT_APPLICATION_LOADER_NAMES"]
    assert set(r7_closure["members"]) == r7_names
    assert sealed_r7.R7_LOCK.name in r7_closure["members"]
    with pytest.raises(RuntimeError, match="membership differs"):
        sealed_r6.BASE["current_application_loader_closure"](sources)
    without_r7_lock = {path: record for path, record in sources.items()
                       if Path(path).name != sealed_r7.R7_LOCK.name}
    with pytest.raises(RuntimeError, match="membership differs"):
        sealed_r7.BASE["current_application_loader_closure"](without_r7_lock)
    tampered = {path: dict(record) for path, record in sources.items()}
    lock_path = str(sealed_r7.R7_LOCK.resolve())
    tampered[lock_path]["sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="fingerprint differs"):
        sealed_r7.BASE["current_application_loader_closure"](tampered)


def test_r7_lifecycle_and_install_reader_shims_pass_only_the_r7_base(monkeypatch):
    profile = {"source_profile": "current175-r7"}
    lifecycle_bases = []
    install_bases = []
    monkeypatch.setattr(
        lifecycle_r7,
        "current_application_profile",
        lambda received, base: lifecycle_bases.append(base) or "r7-lifecycle-reader",
    )
    monkeypatch.setattr(
        install_r7.sealed_data_r7,
        "current_application_profile",
        lambda received, base: install_bases.append(base) or "r7-install-reader",
    )

    assert lifecycle_r7._with_r7_reader(
        lambda: lifecycle_r7.data.current_application_profile(profile)
    ) == "r7-lifecycle-reader"
    assert install_r7._r7_reader_call(
        lambda: install_r7.sealed_data.current_application_profile(profile)
    ) == "r7-install-reader"
    assert lifecycle_bases == [sealed_r7.BASE]
    assert install_bases == [sealed_r7.BASE]


def test_r7_consumer_rejects_r6_selector_before_opening_any_producer_file():
    profile = {
        "schema": "w26-current-installed-application/1",
        "source_profile": "current175-r6",
        "root": "/path/not-used",
        "result": {}, "candidate": {}, "once": {}, "loader_source_closure": {},
    }
    with pytest.raises(RuntimeError, match="selector differs"):
        sealed_r7.current_application_profile(profile)


def test_r7_lifecycle_and_install_sources_pin_the_r7_reader_and_lock():
    records = lifecycle_r7._records()
    assert str(Path(lifecycle_r7.__file__).resolve()) in records
    assert str(lifecycle_r7.SEALED_R7_HELPER) in records
    assert str(app.CURRENT175_R7_SOURCE_LOCK) in records
    assert lifecycle_r7.R7_LOCKS[-1] == app.CURRENT175_R7_SOURCE_LOCK
    assert install_r7.APP_LOCK == app.CURRENT175_R7_SOURCE_LOCK
    assert install_r7.CALLER_SOURCE_HASHES == desktop_r7.APP_SOURCE_HASHES
    assert desktop_r7.CURRENT_R7 == "current175-r7"


def test_r7_reader_rejects_untrusted_producer_root_before_artifact_reads(tmp_path):
    with pytest.raises(RuntimeError, match="producer root refused"):
        sealed_r7._producer_records(tmp_path)
