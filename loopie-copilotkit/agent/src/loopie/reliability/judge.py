"""Advisory semantic judge; never participates in authoritative pass/fail."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.loopie.config import get_settings
from src.loopie.providers import openai_client_kwargs, resolve_provider


class JudgeVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["clear", "flag"]
    suggested_action: str
    category: Literal[
        "incorrect_resolution",
        "insufficient_context",
        "policy_ambiguity",
        "unsafe_tool_intent",
        "other",
    ]
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1, max_length=1000)
    cited_policy_rules: list[str] = Field(max_length=20)


@dataclass
class AdvisoryJudge:
    async def review(
        self,
        *,
        ticket: dict[str, Any],
        run: dict[str, Any],
    ) -> dict[str, Any] | None:
        settings = get_settings()
        if not settings.judge_enabled:
            return None
        provider = resolve_provider("judge")
        if provider is None:
            return None
        provider = replace(provider, model=settings.judge_model)
        from langchain_openai import ChatOpenAI

        model = ChatOpenAI(**openai_client_kwargs(provider))
        structured = model.with_structured_output(JudgeVerdict, strict=True)
        prompt = (
            "Act as an advisory support-quality reviewer. Do not determine the authoritative "
            "run status. Review semantic fit against only the ticket, decision, and pinned approved "
            "policies. Flag only material issues. Return every response field; cited_policy_rules "
            "must be an empty list when no approved rule is relevant.\n\n"
            f"ticket: {json.dumps(ticket, sort_keys=True)}\n"
            f"decision: {json.dumps({'action': run.get('action'), 'tool_calls': run.get('tool_calls')}, sort_keys=True)}\n"
            f"approved_policies: {json.dumps((run.get('artifacts_snapshot') or {}).get('policy_rules', []), sort_keys=True)}"
        )
        verdict = await structured.ainvoke(prompt)
        return verdict.model_dump(mode="json")
