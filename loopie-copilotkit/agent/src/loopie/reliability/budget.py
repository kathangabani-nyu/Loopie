"""Budget guardrails for LLM calls and transitions."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.loopie.config import get_settings


@dataclass
class BudgetTracker:
    llm_calls: int = 0
    eval_llm_calls: int = 0
    transitions: int = 0
    estimated_cost_usd: float = 0.0
    budget_guard_triggered: bool = False
    stop_reason: str | None = None
    _settings: object = field(default_factory=get_settings)

    def record_llm_call(self, *, eval_scope: bool = False, cost_usd: float = 0.0) -> None:
        if eval_scope:
            self.eval_llm_calls += 1
        else:
            self.llm_calls += 1
        self.estimated_cost_usd += cost_usd
        self._check_limits()

    def record_transition(self) -> None:
        self.transitions += 1
        settings = get_settings()
        if self.transitions > settings.max_agent_transitions:
            self.budget_guard_triggered = True
            self.stop_reason = "max_agent_transitions"

    def _check_limits(self) -> None:
        settings = get_settings()
        if self.llm_calls > settings.max_llm_calls_per_run:
            self.budget_guard_triggered = True
            self.stop_reason = "max_llm_calls_per_run"
        if self.eval_llm_calls > settings.max_llm_calls_per_eval:
            self.budget_guard_triggered = True
            self.stop_reason = "max_llm_calls_per_eval"
        if self.estimated_cost_usd > settings.max_estimated_cost_usd:
            self.budget_guard_triggered = True
            self.stop_reason = "max_estimated_cost_usd"

    def to_dict(self) -> dict:
        return {
            "llm_calls": self.llm_calls,
            "eval_llm_calls": self.eval_llm_calls,
            "transitions": self.transitions,
            "estimated_cost_usd": round(self.estimated_cost_usd, 6),
            "budget_guard_triggered": self.budget_guard_triggered,
            "stop_reason": self.stop_reason,
        }
