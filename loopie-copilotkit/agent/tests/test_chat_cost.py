"""Chat budget callback tests."""

import pytest

from memory_stores import MemoryLedger
from src.loopie.chat_cost import ChatBudgetExceeded, LedgerCostCallback, max_chat_cost_usd


def test_budget_blocks_before_llm_start(monkeypatch):
    monkeypatch.setenv("LOOPIE_MAX_CHAT_COST_USD", "1.0")
    ledger = MemoryLedger()
    ledger.record_cost(
        run_id="chat_test",
        provider="openai",
        model="gpt-5.5",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        estimated_cost=1.5,
        stop_reason="chat",
        mode="chat",
    )
    callback = LedgerCostCallback(ledger=ledger)
    with pytest.raises(ChatBudgetExceeded):
        callback.on_llm_start({}, ["hello"])


def test_budget_allows_under_cap(monkeypatch):
    monkeypatch.setenv("LOOPIE_MAX_CHAT_COST_USD", "40")
    callback = LedgerCostCallback(ledger=MemoryLedger())
    callback.on_llm_start({}, ["hello"])


def test_max_chat_cost_default():
    assert max_chat_cost_usd() == 40.0
