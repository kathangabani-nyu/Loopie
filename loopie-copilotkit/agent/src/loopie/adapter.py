"""Framework-agnostic adapter — supervise external sample_agent runs."""

from __future__ import annotations

from typing import Any

from src.loopie.decide import decide_action
from src.loopie.pipeline import LoopiePipeline
from src.loopie.runner import tickets_by_id


def supervise_external_run(
    *,
    case_id: str,
    external_action: str,
    pipeline: LoopiePipeline | None = None,
) -> dict[str, Any]:
    """Compare an external agent action against the Loopie oracle."""
    pipeline = pipeline or LoopiePipeline()
    ticket = tickets_by_id()[case_id]
    artifacts = pipeline.redis.get_live_artifacts()
    oracle_action = decide_action(ticket, artifacts)
    return {
        "case_id": case_id,
        "external_action": external_action,
        "oracle_action": oracle_action,
        "matches_oracle": external_action == oracle_action,
        "supervised_by": "loopie_adapter",
    }


def wrap_sample_agent_case(case_id: str = "security_001") -> dict[str, Any]:
    """Stretch: treat sample_agent output as external and diff against oracle."""
    pipeline = LoopiePipeline()
    pipeline.seed()
    baseline = pipeline.run_baseline(case_id=case_id)
    external_action = baseline["scores"]  # placeholder external surface
    return supervise_external_run(
        case_id=case_id,
        external_action=str(baseline.get("failure", {}).get("run", {}).get("action", "unknown")),
        pipeline=pipeline,
    )
