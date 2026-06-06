"""Single gateway for all model access."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.loopie.config import get_settings
from src.loopie.reliability.budget import BudgetTracker
from src.loopie.stores.llm_cache import cache_key, get_cached, set_cached

if TYPE_CHECKING:
    from src.loopie.stores.ledger import Ledger


_MOCK_NARRATION: dict[str, str] = {
    "triage": "Routing ticket to policy and resolution workflow.",
    "memory_lookup": "Retrieved refund policy from Redis memory store.",
    "policy_check": "Evaluated routing guards against ticket context.",
    "resolution": "Selected resolution action based on policy state.",
    "evaluator": "Graded outcome against deterministic scorers.",
}


@dataclass
class LLMResult:
    text: str
    mode: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    stop_reason: str
    from_cache: bool = False


class LLMGateway:
    def __init__(
        self,
        *,
        budget: BudgetTracker | None = None,
        ledger: Ledger | None = None,
        eval_scope: bool = False,
    ) -> None:
        self.settings = get_settings()
        self.budget = budget or BudgetTracker()
        self.ledger = ledger
        self.eval_scope = eval_scope

    def narrate(
        self,
        *,
        node: str,
        fixture_id: str,
        artifact_version: str,
        ticket: dict[str, Any] | None = None,
    ) -> LLMResult:
        if self.settings.is_mock:
            text = _MOCK_NARRATION.get(node, f"Mock narration for {node}.")
            result = LLMResult(
                text=text,
                mode="mock",
                model="oracle",
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                estimated_cost_usd=0.0,
                stop_reason="mock",
            )
            self._record(result, run_id=fixture_id)
            return result

        return self._live_completion(
            node=node,
            fixture_id=fixture_id,
            artifact_version=artifact_version,
            prompt=self._build_prompt(node, ticket),
        )

    def _live_completion(
        self,
        *,
        node: str,
        fixture_id: str,
        artifact_version: str,
        prompt: str,
    ) -> LLMResult:
        if self.settings.require_live_confirmation and os.getenv("LOOPIE_LIVE_CONFIRMED") != "1":
            raise RuntimeError(
                "Live LLM calls require LOOPIE_LLM_MODE=live and LOOPIE_LIVE_CONFIRMED=1"
            )
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is required for live LLM mode")

        key = cache_key(
            model=self.settings.openai_model,
            node=node,
            fixture_id=fixture_id,
            artifact_version=artifact_version,
        )
        cached = get_cached(key)
        if cached is not None:
            result = LLMResult(
                text=cached,
                mode="live",
                model=self.settings.openai_model,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                estimated_cost_usd=0.0,
                stop_reason="cache_hit",
                from_cache=True,
            )
            self._record(result, run_id=fixture_id)
            return result

        from langchain_openai import ChatOpenAI

        model = ChatOpenAI(
            model=self.settings.openai_model,
            temperature=0,
            model_kwargs={"seed": self.settings.llm_seed},
        )
        if self.budget.budget_guard_triggered:
            return LLMResult(
                text="",
                mode="live",
                model=self.settings.openai_model,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                estimated_cost_usd=0.0,
                stop_reason=self.budget.stop_reason or "budget_guard",
            )

        response = model.invoke(prompt)
        text = str(response.content)
        prompt_tokens = len(prompt.split())
        completion_tokens = len(text.split())
        total_tokens = prompt_tokens + completion_tokens
        cost = total_tokens * 0.000002
        self.budget.record_llm_call(eval_scope=self.eval_scope, cost_usd=cost)
        set_cached(key, text)
        result = LLMResult(
            text=text,
            mode="live",
            model=self.settings.openai_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=cost,
            stop_reason="completed",
        )
        self._record(result, run_id=fixture_id)
        return result

    def _record(self, result: LLMResult, *, run_id: str) -> None:
        if self.ledger is None:
            return
        self.ledger.record_cost(
            run_id=run_id,
            model=result.model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.total_tokens,
            estimated_cost=result.estimated_cost_usd,
            stop_reason=result.stop_reason,
            mode=result.mode,
        )

    @staticmethod
    def _build_prompt(node: str, ticket: dict[str, Any] | None) -> str:
        ticket_text = (ticket or {}).get("request", "unknown ticket")
        return f"Node {node}: summarize triage for ticket: {ticket_text}"
