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


_SECURITY_GUARD_RULE = "security_flag_blocks_refund"


def _refund_window_days(artifacts: dict[str, Any] | None) -> int | None:
    memory = (artifacts or {}).get("memory") or {}
    raw = memory.get("policy:refund_window")
    if not raw:
        return None
    for token in str(raw).replace(",", " ").split():
        if token.isdigit():
            return int(token)
    return None


def _has_security_guard(artifacts: dict[str, Any] | None) -> bool:
    rules = (artifacts or {}).get("routing_rules") or []
    return any(r.get("rule") == _SECURITY_GUARD_RULE for r in rules)


def mock_narration(
    node: str,
    ticket: dict[str, Any] | None = None,
    artifacts: dict[str, Any] | None = None,
) -> str:
    """Deterministic, case-specific narration grounded in the ticket + live artifacts.

    Mock mode only: the text reflects the actual retrieved artifact and the oracle's
    decision so the causality trace explains *why* a case passes or fails, instead of
    emitting the same generic line for every case.
    """
    ticket = ticket or {}
    case_id = ticket.get("case_id", "unknown")
    security_flag = bool(ticket.get("security_flag"))
    days = ticket.get("days_since_purchase")
    tier = ticket.get("customer_tier", "standard")
    failure_seed = ticket.get("failure_seed")
    must_check = bool(ticket.get("must_check_policy_version"))

    if node == "triage":
        if security_flag:
            return f"triage [{case_id}]: payment/refund requested with security_flag RAISED — routing to policy + resolution."
        if failure_seed == "planner_loop":
            return f"triage [{case_id}]: refund missing policy-version metadata — routing to policy lookup."
        return f"triage [{case_id}]: refund request · {days}d since purchase · {tier} tier — routing to policy + resolution."

    if node == "memory_lookup":
        window = _refund_window_days(artifacts)
        if window is not None:
            return f"memory_lookup [{case_id}]: refund policy window = {window} days (from Redis memory)."
        return f"memory_lookup [{case_id}]: no refund-window policy found in Redis memory."

    if node == "policy_check":
        if security_flag:
            state = "ACTIVE" if _has_security_guard(artifacts) else "MISSING"
            return f"policy_check [{case_id}]: security guard '{_SECURITY_GUARD_RULE}' = {state}."
        if must_check:
            return f"policy_check [{case_id}]: ticket requires a fresh policy version — verifying provenance."
        return f"policy_check [{case_id}]: standard refund guards evaluated, none blocking."

    if node == "resolution":
        try:
            from src.loopie.decide import decide_action, decide_tool_calls

            action = decide_action(ticket, artifacts or {})
            tools = ", ".join(c["name"] for c in decide_tool_calls(action)) or "no tool"
            return f"resolution [{case_id}]: decision → {action} (calls: {tools})."
        except Exception:
            return f"resolution [{case_id}]: selected resolution action based on policy state."

    if node == "evaluator":
        return f"evaluator [{case_id}]: grading against deterministic scorers (action_match, unauthorized_tool_call, …)."

    return f"Mock narration for {node} [{case_id}]."


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
        artifacts: dict[str, Any] | None = None,
    ) -> LLMResult:
        if self.settings.is_mock:
            text = mock_narration(node, ticket, artifacts)
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
