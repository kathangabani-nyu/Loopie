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


def test_reset_preserves_chat_ledger_rows():
    ledger = MemoryLedger()
    ledger.record_cost(
        run_id="chat_persist",
        provider="openai",
        model="gpt-5.5",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        estimated_cost=0.02,
        stop_reason="chat",
        mode="chat",
    )
    ledger.record_cost(
        run_id="pipeline_run",
        provider="openai",
        model="gpt-4o-mini",
        prompt_tokens=1,
        completion_tokens=1,
        total_tokens=2,
        estimated_cost=0.001,
        stop_reason="completed",
        mode="live",
    )
    assert ledger.total_cost(mode="chat") == pytest.approx(0.02)
    assert ledger.total_cost(mode="live") == pytest.approx(0.001)

    ledger.reset()

    assert ledger.total_cost(mode="chat") == pytest.approx(0.02)
    assert ledger.total_cost(mode="live") == 0.0
