"""Token budget tests."""

import os

from src.loopie.config import get_settings
from src.loopie.reliability.budget import BudgetTracker


def test_max_transitions_triggers_guard():
    os.environ["LOOPIE_MAX_AGENT_TRANSITIONS"] = "2"
    get_settings.cache_clear()
    budget = BudgetTracker()
    budget.record_transition()
    budget.record_transition()
    budget.record_transition()
    assert budget.budget_guard_triggered is True
    assert budget.stop_reason == "max_agent_transitions"
    get_settings.cache_clear()
