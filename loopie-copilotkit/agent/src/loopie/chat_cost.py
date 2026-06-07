"""LangChain cost callback for live chat with a USD cap."""

from __future__ import annotations

import os
import uuid
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from src.loopie.stores.ledger import Ledger


def max_chat_cost_usd() -> float:
    return float(os.getenv("LOOPIE_MAX_CHAT_COST_USD", "40"))


def budget_degraded_message(session_cost: float, max_cost: float) -> str:
    return (
        f"Live chat budget reached (${session_cost:.2f} / ${max_cost:.2f}). "
        "Cockpit buttons remain deterministic ($0 pipeline)."
    )


class ChatBudgetExceeded(Exception):
    """Raised when live chat spend is at or above LOOPIE_MAX_CHAT_COST_USD."""

    def __init__(self, session_cost: float, max_cost: float) -> None:
        self.session_cost = session_cost
        self.max_cost = max_cost
        super().__init__(budget_degraded_message(session_cost, max_cost))


def handle_chat_budget_error(exc: ChatBudgetExceeded) -> str:
    """User-facing degrade copy for CopilotKit / chat surfaces."""
    return str(exc)


def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    per_1k = 0.005 if model.startswith("gpt-5") else 0.0003
    return ((prompt_tokens + completion_tokens) / 1000.0) * per_1k


class LedgerCostCallback(BaseCallbackHandler):
    """Records chat LLM usage to Postgres and enforces LOOPIE_MAX_CHAT_COST_USD."""

    def __init__(self, ledger: Ledger | None = None) -> None:
        super().__init__()
        self.ledger = ledger or Ledger.connect(strict=False)
        self.max_cost = max_chat_cost_usd()
        self.run_id = f"chat_{uuid.uuid4().hex[:12]}"
        self._refresh_session_cost()

    def _refresh_session_cost(self) -> float:
        self.session_cost = self.ledger.total_cost(mode="chat")
        return self.session_cost

    def _enforce_budget_before_call(self) -> None:
        cost = self._refresh_session_cost()
        if cost >= self.max_cost:
            raise ChatBudgetExceeded(cost, self.max_cost)

    def on_llm_start(self, serialized: dict[str, Any], prompts: list[str], **kwargs: Any) -> None:
        self._enforce_budget_before_call()

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        **kwargs: Any,
    ) -> None:
        self._enforce_budget_before_call()

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        llm_output = response.llm_output or {}
        model = str(
            llm_output.get("model_name")
            or llm_output.get("model")
            or os.getenv("LOOPIE_OPENAI_MODEL", "gpt-4o-mini")
        )
        usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
        if not usage and response.generations:
            for gen_list in response.generations:
                for gen in gen_list:
                    meta = getattr(gen, "message", None)
                    usage_meta = getattr(meta, "usage_metadata", None) if meta else None
                    if usage_meta:
                        usage = {
                            "prompt_tokens": usage_meta.get("input_tokens", 0),
                            "completion_tokens": usage_meta.get("output_tokens", 0),
                            "total_tokens": usage_meta.get("total_tokens", 0),
                        }
                        break

        prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or prompt_tokens + completion_tokens)
        cost = _estimate_cost(model, prompt_tokens, completion_tokens)

        self.ledger.record_cost(
            run_id=self.run_id,
            provider="openai",
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            estimated_cost=cost,
            stop_reason="chat",
            mode="chat",
        )
        self.session_cost = self._refresh_session_cost()
        if self.session_cost > self.max_cost:
            raise ChatBudgetExceeded(self.session_cost, self.max_cost)
