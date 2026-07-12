"""LLM gateway provider/fallback regression tests."""

from __future__ import annotations

import pytest

from src.loopie.config import get_settings
from src.loopie.llm import LLMGateway, LiveDecisionUnavailable, _provider_error_summary


def test_provider_error_summary_exposes_safe_openai_metadata_only():
    exc = RuntimeError("raw exception must not leak")
    exc.body = {  # type: ignore[attr-defined]
        "error": {
            "message": "Unsupported parameter: temperature",
            "type": "invalid_request_error",
            "param": "temperature",
            "code": "unsupported_parameter",
        }
    }

    summary = _provider_error_summary(exc)

    assert "Unsupported parameter: temperature" in summary
    assert "param=temperature" in summary
    assert "raw exception must not leak" not in summary
from src.loopie.providers import cursor_smoke_verified, write_cursor_smoke_marker
from src.loopie.reliability.budget import BudgetTracker


@pytest.fixture(autouse=True)
def live_mode(monkeypatch):
    monkeypatch.setenv("LOOPIE_LLM_MODE", "live")
    monkeypatch.setenv("LOOPIE_LIVE_CONFIRMED", "1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_budget_exhaustion_fails_live_decision_instead_of_using_oracle(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    get_settings.cache_clear()

    budget = BudgetTracker(budget_guard_triggered=True, stop_reason="max_estimated_cost_usd")
    gateway = LLMGateway(budget=budget, ledger=None)
    with pytest.raises(LiveDecisionUnavailable, match="budget exhausted"):
        gateway.decide_episode(
            ticket={"case_id": "live-1", "request": "refund"},
            artifacts={"routing_rules": [], "memory": {}, "action_taxonomy": ["escalate_security"]},
            fixture_id="security_001",
            artifact_version="v1",
            policy_memory={"value": "Refunds within 30 days", "version": 1},
            mode="live",
        )


def test_live_decision_requires_any_enabled_provider_not_only_openai(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("CURSOR_API_KEY", "cursor-key")
    monkeypatch.setenv("LOOPIE_PROVIDER_CHAIN", "cursor")
    monkeypatch.setenv("LOOPIE_PROVIDER_CURSOR_ENABLED", "true")
    monkeypatch.setenv("LOOPIE_CURSOR_SMOKE_OK", "1")
    get_settings.cache_clear()

    gateway = LLMGateway(budget=BudgetTracker(), ledger=None)
    providers = gateway._require_live_providers("decision")
    assert providers[0][1] == "cursor"


def test_cursor_smoke_marker_persists_across_env(monkeypatch, tmp_path):
    monkeypatch.delenv("LOOPIE_CURSOR_SMOKE_OK", raising=False)
    marker = tmp_path / ".cursor_smoke_ok"
    monkeypatch.setattr("src.loopie.providers.CURSOR_SMOKE_MARKER", marker)
    assert cursor_smoke_verified() is False
    marker.write_text("ok\n", encoding="utf-8")
    assert cursor_smoke_verified() is True


def test_write_cursor_smoke_marker_creates_file(monkeypatch, tmp_path):
    marker = tmp_path / ".cursor_smoke_ok"
    monkeypatch.setattr("src.loopie.providers.CURSOR_SMOKE_MARKER", marker)
    path = write_cursor_smoke_marker()
    assert path == marker
    assert marker.read_text(encoding="utf-8") == "ok\n"
