"""Regression tests for the bounded evidence-gathering resolution episode."""

from __future__ import annotations

from langchain_core.messages import AIMessage
import pytest

from src.loopie.config import get_settings
from src.loopie.llm import LLMGateway
from src.loopie.reliability.budget import BudgetTracker
from src.loopie.stores.llm_cache import clear_cache
from src.loopie.tools import authorize_and_execute


class FakeChatModel:
    def __init__(self, responses: list[AIMessage]):
        self.responses = list(responses)
        self.calls = 0
        self.tool_choices: list[str | None] = []

    def bind_tools(self, tools, *, tool_choice=None, strict=None):
        self.tool_choices.append(tool_choice)
        return self

    def invoke(self, messages):
        self.calls += 1
        if not self.responses:
            raise AssertionError("fake model received an unexpected invocation")
        return self.responses.pop(0)


def _evidence(name: str, call_id: str = "evidence-1") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": {}, "id": call_id, "type": "tool_call"}],
    )


def _submit(
    *,
    action: str = "approve_refund",
    proposed_tools: list[str] | None = None,
) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "submit_decision",
                "args": {
                    "action": action,
                    "security_guard_observed": False,
                    "artifact_basis": ["policy_version_read"],
                    "reason": "Pinned policy evidence supports this proposal.",
                    "proposed_tools": proposed_tools or [],
                },
                "id": "decision-1",
                "type": "tool_call",
            }
        ],
    )


def _artifacts() -> dict:
    return {
        "routing_rules": [],
        "policy_rules": [],
        "memory": {"policy:refund_window": "Refunds are allowed within 30 days."},
        "max_transitions": 6,
        "action_taxonomy": ["approve_refund", "deny_refund_offer_credit", "escalate_security"],
    }


@pytest.fixture(autouse=True)
def live_mode(monkeypatch):
    monkeypatch.setenv("LOOPIE_LLM_MODE", "live")
    monkeypatch.setenv("LOOPIE_LIVE_CONFIRMED", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("LOOPIE_ENABLE_REPLAY_CACHE", "true")
    get_settings.cache_clear()
    clear_cache()
    yield
    clear_cache()
    get_settings.cache_clear()


def _gateway_with(fake: FakeChatModel, *, budget: BudgetTracker | None = None) -> LLMGateway:
    gateway = LLMGateway(budget=budget or BudgetTracker(), ledger=None)
    gateway._model_for_provider = lambda cfg: fake  # type: ignore[method-assign]
    return gateway


def _run(gateway: LLMGateway, *, ticket_request: str = "refund"):
    return gateway.decide_episode(
        {"case_id": "episode-1", "request": ticket_request, "days_since_purchase": 10},
        _artifacts(),
        fixture_id="episode-1",
        artifact_version="manifest-1",
        policy_memory={"value": "Refunds are allowed within 30 days.", "version": 2},
        mode="live",
    )


def test_episode_forces_a_final_decision_after_bounded_iterations():
    fake = FakeChatModel([AIMessage(content="thinking") for _ in range(4)] + [_submit()])
    budget = BudgetTracker()
    result = _run(_gateway_with(fake, budget=budget))

    assert result.iterations == 5
    assert fake.calls == 5
    assert budget.llm_calls == 5
    assert fake.tool_choices[-1] == "submit_decision"


def test_cached_episode_replays_evidence_with_zero_new_model_cost():
    first_fake = FakeChatModel([_evidence("policy_version_read"), _submit(proposed_tools=["refund_tool"])])
    first = _run(_gateway_with(first_fake))

    second_fake = FakeChatModel([])
    second_budget = BudgetTracker()
    second = _run(_gateway_with(second_fake, budget=second_budget))

    assert first.evidence_calls == second.evidence_calls
    assert second.from_cache is True
    assert second_budget.llm_calls == 0
    assert second_fake.calls == 0


def test_ticket_content_change_busts_episode_cache():
    _run(_gateway_with(FakeChatModel([_submit()])), ticket_request="refund one")
    changed_fake = FakeChatModel([_submit()])

    changed = _run(_gateway_with(changed_fake), ticket_request="refund two")

    assert changed.from_cache is False
    assert changed_fake.calls == 1


def test_action_effect_contract_blocks_contradictory_refund():
    result = authorize_and_execute(
        {"case_id": "contract-1", "security_flag": False},
        {"policy_rules": []},
        "deny_refund_offer_credit",
        [{"name": "refund_tool", "args": {}}],
    )

    assert result["authorized_tools"] == []
    assert result["denied_proposals"] == ["refund_tool"]
    assert result["executed_tools"] == []
