"""Offline guardrails for bounded supplemental Phase-2 evidence drivers."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_followup_uses_production_stdio_and_verified_recovery_route():
    source = (ROOT / "tools" / "phase2_followup_mcp.py").read_text(encoding="utf-8")
    assert "stdio_client" in source
    assert "ClientSession" in source
    assert "phase2_recovery_mcp.py" in source
    assert '"worker-replace"' in source
    assert '"fresh-worker-reopen"' in source


def test_external_mutation_is_bounded_to_explicit_loopback_test_target():
    source = (ROOT / "tools" / "java" / "Phase2ExternalMutation.java").read_text(encoding="utf-8")
    assert '"127.0.0.1".equals(host)' in source
    assert "model.param().set(parameter, expression)" in source
    assert "ModelUtil.remove" not in source
    assert ".save(" not in source


def test_external_api_evidence_requires_event_readback_and_stale_write_rejection():
    source = (ROOT / "tools" / "phase2_followup_mcp.py").read_text(encoding="utf-8")
    for assertion in ("ordinary_expression_outer_error", "handler_event_counter_advanced", "readback_external_value_is_2", "stale_write_denied"):
        assert assertion in source
    assert '"gui_t011":"BLOCKED_NO_GUI_EVIDENCE"' in source
