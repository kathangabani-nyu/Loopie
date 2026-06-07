"""Agentic diagnosis (narrative only) — artifact writes stay on the deterministic path."""

from __future__ import annotations

from typing import Any

from src.loopie.config import get_settings
from src.loopie.observability import op
from src.loopie.reliability.corrections import propose


@op("diagnosis.agentic")
def agentic_diagnosis(failure: dict[str, Any]) -> dict[str, Any]:
    """LLM-driven diagnosis summary when LOOPIE_FULL_AGENTIC=1; never mutates artifacts."""
    settings = get_settings()
    category = failure.get("category", "unknown_failure")
    case_id = failure.get("case_id", "unknown")
    scores = failure.get("scores", {})
    run = failure.get("run", {})
    ticket_hint = run.get("narration", {}).get("triage", "")

    correction = propose(category, case_id=case_id)

    if not settings.full_agentic or settings.is_test:
        return {
            **correction,
            "diagnosis_mode": "deterministic",
            "diagnosis": f"Classifier mapped {category} to structured correction for {case_id}.",
            "failing_scorers": [k for k, v in scores.items() if not v],
        }

    try:
        from langchain_openai import ChatOpenAI

        model = ChatOpenAI(model=settings.openai_model, temperature=0)
        prompt = (
            "You are Loopie's supervisory diagnosis layer. Explain the failure root cause "
            "and proposed fix in 2-3 sentences. Do NOT invent artifact mutations.\n\n"
            f"case_id: {case_id}\n"
            f"category: {category}\n"
            f"failing_scorers: {[k for k, v in scores.items() if not v]}\n"
            f"triage: {ticket_hint}\n"
            f"proposed_correction_type: {correction.get('type')}\n"
            f"proposal_summary: {correction.get('summary')}\n"
        )
        response = model.invoke(prompt)
        narrative = str(response.content)
    except Exception as exc:
        narrative = f"Agentic diagnosis unavailable ({exc}); using deterministic proposal."

    return {
        **correction,
        "diagnosis_mode": "agentic",
        "diagnosis": narrative,
        "failing_scorers": [k for k, v in scores.items() if not v],
    }
