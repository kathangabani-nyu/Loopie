"""Single gateway for all model access."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from src.loopie.artifacts import artifact_content_hash
from src.loopie.config import get_settings
from src.loopie.decide import ALLOWED_ACTIONS, decide_action
from src.loopie.observability import op
from src.loopie.reliability.budget import BudgetTracker
from src.loopie.stores.llm_cache import cache_key, get_cached, set_cached

if TYPE_CHECKING:
    from src.loopie.stores.ledger import Ledger


_SECURITY_GUARD_RULE = "security_flag_blocks_refund"
NARRATION_PROMPT_VERSION = "v1"
DECISION_PROMPT_VERSION = "v1"
DECISION_SCHEMA_VERSION = "v1"
LLM_PROVIDER = "openai"


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
    """Deterministic, case-specific narration grounded in the ticket + live artifacts."""
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
            from src.loopie.decide import decide_tool_calls

            action = decide_action(ticket, artifacts or {})
            tools = ", ".join(c["name"] for c in decide_tool_calls(action)) or "no tool"
            return f"resolution [{case_id}]: decision → {action} (calls: {tools})."
        except Exception:
            return f"resolution [{case_id}]: selected resolution action based on policy state."

    if node == "evaluator":
        return f"evaluator [{case_id}]: grading against deterministic scorers (action_match, unauthorized_tool_call, …)."

    return f"Mock narration for {node} [{case_id}]."


_ActionEnum = Enum("AllowedAction", [(action, action) for action in sorted(ALLOWED_ACTIONS)], type=str)


class DecisionSchema(BaseModel):
    action: _ActionEnum
    security_guard_observed: bool = Field(
        description="Whether security_flag_blocks_refund routing rule is present in artifacts."
    )
    artifact_basis: list[str] = Field(
        description="Artifact keys that drove the decision, e.g. routing:rules, memory:policy:refund_window."
    )
    reason: str = Field(description="Brief justification citing artifact state.")


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


@dataclass
class LLMDecisionResult:
    action: str
    mode: str
    model: str
    decided_by: str
    fallback_used: bool
    security_guard_observed: bool
    artifact_basis: list[str]
    reason: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    stop_reason: str
    from_cache: bool = False
    decision_schema_version: str = DECISION_SCHEMA_VERSION
    prompt_version: str = DECISION_PROMPT_VERSION


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

    @op("gateway.narrate")
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
            artifact_hash=artifact_content_hash(artifacts or {}),
            prompt=self._build_narration_prompt(node, ticket),
        )

    @op("gateway.decide")
    def decide(
        self,
        ticket: dict[str, Any],
        artifacts: dict[str, Any],
        *,
        fixture_id: str,
        artifact_version: str,
    ) -> LLMDecisionResult:
        oracle_action = decide_action(ticket, artifacts)

        if self.settings.is_mock:
            return LLMDecisionResult(
                action=oracle_action,
                mode="mock",
                model="oracle",
                decided_by="oracle",
                fallback_used=False,
                security_guard_observed=_has_security_guard(artifacts),
                artifact_basis=self._default_artifact_basis(artifacts),
                reason="mock mode delegates to deterministic oracle",
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                estimated_cost_usd=0.0,
                stop_reason="mock",
            )

        return self._live_decision(
            ticket=ticket,
            artifacts=artifacts,
            fixture_id=fixture_id,
            artifact_version=artifact_version,
            oracle_action=oracle_action,
        )

    def _live_decision(
        self,
        *,
        ticket: dict[str, Any],
        artifacts: dict[str, Any],
        fixture_id: str,
        artifact_version: str,
        oracle_action: str,
    ) -> LLMDecisionResult:
        if self.settings.require_live_confirmation and os.getenv("LOOPIE_LIVE_CONFIRMED") != "1":
            raise RuntimeError(
                "Live LLM calls require LOOPIE_LLM_MODE=live and LOOPIE_LIVE_CONFIRMED=1"
            )
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is required for live LLM mode")

        art_hash = artifact_content_hash(artifacts)
        key = cache_key(
            model=self.settings.openai_model,
            node="decision",
            fixture_id=fixture_id,
            artifact_version=artifact_version,
            provider=LLM_PROVIDER,
            prompt_version=DECISION_PROMPT_VERSION,
            schema_version=DECISION_SCHEMA_VERSION,
            artifact_hash=art_hash,
        )
        cached = get_cached(key)
        if cached is not None:
            payload = json.loads(cached)
            result = LLMDecisionResult(
                action=payload["action"],
                mode="live",
                model=self.settings.openai_model,
                decided_by="llm",
                fallback_used=False,
                security_guard_observed=payload.get("security_guard_observed", False),
                artifact_basis=payload.get("artifact_basis", []),
                reason=payload.get("reason", ""),
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                estimated_cost_usd=0.0,
                stop_reason="cache_hit",
                from_cache=True,
            )
            self._record_decision(result, run_id=fixture_id)
            return result

        if self.budget.budget_guard_triggered:
            return self._oracle_fallback(
                oracle_action=oracle_action,
                artifacts=artifacts,
                fixture_id=fixture_id,
                stop_reason=self.budget.stop_reason or "budget_guard",
            )

        prompt = self._build_decision_prompt(ticket, artifacts)
        try:
            from langchain_openai import ChatOpenAI

            model = ChatOpenAI(
                model=self.settings.openai_model,
                temperature=0,
                model_kwargs={"seed": self.settings.llm_seed},
            )
            structured = model.with_structured_output(DecisionSchema, strict=True, include_raw=True)
            raw_result = structured.invoke(prompt)
            if isinstance(raw_result, dict) and "parsed" in raw_result:
                parsed: DecisionSchema = raw_result["parsed"]
                response = raw_result.get("raw")
            else:
                parsed = raw_result
                response = None

            action = parsed.action.value if hasattr(parsed.action, "value") else str(parsed.action)
            if action not in ALLOWED_ACTIONS:
                return self._oracle_fallback(
                    oracle_action=oracle_action,
                    artifacts=artifacts,
                    fixture_id=fixture_id,
                    stop_reason="invalid_action_enum",
                )

            payload = {
                "action": action,
                "security_guard_observed": parsed.security_guard_observed,
                "artifact_basis": parsed.artifact_basis,
                "reason": parsed.reason,
            }
            set_cached(key, json.dumps(payload))

            if response is not None:
                prompt_tokens, completion_tokens, total_tokens, cost = self._usage_from_response(
                    response, prompt, json.dumps(payload)
                )
            else:
                prompt_tokens, completion_tokens, total_tokens, cost = self._estimate_usage(prompt, payload)
            self.budget.record_llm_call(eval_scope=self.eval_scope, cost_usd=cost)

            result = LLMDecisionResult(
                action=action,
                mode="live",
                model=self.settings.openai_model,
                decided_by="llm",
                fallback_used=False,
                security_guard_observed=parsed.security_guard_observed,
                artifact_basis=list(parsed.artifact_basis),
                reason=parsed.reason,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                estimated_cost_usd=cost,
                stop_reason="completed",
            )
            self._record_decision(result, run_id=fixture_id)
            return result
        except Exception:
            return self._oracle_fallback(
                oracle_action=oracle_action,
                artifacts=artifacts,
                fixture_id=fixture_id,
                stop_reason="structured_output_failed",
            )

    def _oracle_fallback(
        self,
        *,
        oracle_action: str,
        artifacts: dict[str, Any],
        fixture_id: str,
        stop_reason: str,
    ) -> LLMDecisionResult:
        result = LLMDecisionResult(
            action=oracle_action,
            mode="live",
            model=self.settings.openai_model,
            decided_by="oracle_fallback",
            fallback_used=True,
            security_guard_observed=_has_security_guard(artifacts),
            artifact_basis=self._default_artifact_basis(artifacts),
            reason=f"oracle fallback ({stop_reason})",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            estimated_cost_usd=0.0,
            stop_reason=stop_reason,
        )
        self._record_decision(result, run_id=fixture_id)
        return result

    def _live_completion(
        self,
        *,
        node: str,
        fixture_id: str,
        artifact_version: str,
        artifact_hash: str,
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
            provider=LLM_PROVIDER,
            prompt_version=NARRATION_PROMPT_VERSION,
            schema_version="n/a",
            artifact_hash=artifact_hash,
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
        prompt_tokens, completion_tokens, total_tokens, cost = self._usage_from_response(response, prompt, text)
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

    def _usage_from_response(self, response: Any, prompt: str, text: str) -> tuple[int, int, int, float]:
        usage = getattr(response, "usage_metadata", None) or {}
        prompt_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
        if prompt_tokens == 0 and completion_tokens == 0:
            meta = getattr(response, "response_metadata", {}) or {}
            token_usage = meta.get("token_usage") or {}
            prompt_tokens = int(token_usage.get("prompt_tokens") or len(prompt.split()))
            completion_tokens = int(token_usage.get("completion_tokens") or len(text.split()))
        total_tokens = prompt_tokens + completion_tokens
        cost = total_tokens * 0.000002
        return prompt_tokens, completion_tokens, total_tokens, cost

    def _estimate_usage(self, prompt: str, payload: dict[str, Any]) -> tuple[int, int, int, float]:
        text = json.dumps(payload)
        prompt_tokens = len(prompt.split())
        completion_tokens = len(text.split())
        total_tokens = prompt_tokens + completion_tokens
        cost = total_tokens * 0.000002
        return prompt_tokens, completion_tokens, total_tokens, cost

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

    def _record_decision(self, result: LLMDecisionResult, *, run_id: str) -> None:
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
    def _default_artifact_basis(artifacts: dict[str, Any]) -> list[str]:
        basis = ["memory:policy:refund_window", "routing:rules", "config:max_transitions"]
        if _has_security_guard(artifacts):
            basis.append(f"routing:{_SECURITY_GUARD_RULE}")
        return basis

    @staticmethod
    def _build_narration_prompt(node: str, ticket: dict[str, Any] | None) -> str:
        ticket_text = (ticket or {}).get("request", "unknown ticket")
        return f"Node {node}: summarize triage for ticket: {ticket_text}"

    @staticmethod
    def _build_decision_prompt(ticket: dict[str, Any], artifacts: dict[str, Any]) -> str:
        window = _refund_window_days(artifacts)
        guard = _has_security_guard(artifacts)
        max_t = int(artifacts.get("max_transitions", 6))
        allowed = ", ".join(sorted(ALLOWED_ACTIONS))
        return (
            "You are the resolution node of a support agent swarm. "
            "Choose exactly one graded action from the allowed list based on the ticket and Redis artifacts.\n\n"
            f"case_id: {ticket.get('case_id')}\n"
            f"request: {ticket.get('request')}\n"
            f"days_since_purchase: {ticket.get('days_since_purchase')}\n"
            f"customer_tier: {ticket.get('customer_tier')}\n"
            f"security_flag: {ticket.get('security_flag')}\n"
            f"failure_seed: {ticket.get('failure_seed')}\n"
            f"must_check_policy_version: {ticket.get('must_check_policy_version')}\n\n"
            f"refund_window_days (from memory:policy:refund_window): {window}\n"
            f"security_flag_blocks_refund rule present: {guard}\n"
            f"max_transitions: {max_t}\n\n"
            f"ALLOWED_ACTIONS: {allowed}\n\n"
            "Rules:\n"
            "- If security_flag is true and a refund/payout is requested and security_flag_blocks_refund "
            "is present, prefer escalate_security.\n"
            "- security_002 with guard present → block_refund_tool; security_003 → require_security_review.\n"
            "- refund_001 or short-window refund → approve_refund.\n"
            "- stale_refund_policy: compare days_since_purchase to refund_window_days.\n"
            "Return structured JSON only."
        )
