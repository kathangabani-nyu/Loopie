"""Token budget tests."""

from src.loopie.config import get_settings
from src.loopie.reliability.budget import BudgetTracker


def test_max_transitions_triggers_guard(monkeypatch):
    # Use monkeypatch so the lowered limit is restored after the test and does not
    # leak into later tests' settings cache.
    monkeypatch.setenv("LOOPIE_MAX_AGENT_TRANSITIONS", "2")
    get_settings.cache_clear()
    try:
        budget = BudgetTracker()
        budget.record_transition()
        budget.record_transition()
        budget.record_transition()
        assert budget.budget_guard_triggered is True
        assert budget.stop_reason == "max_agent_transitions"
    finally:
        get_settings.cache_clear()
