"""Pre-warm provider-keyed replay cache for deterministic live demos."""

from __future__ import annotations

import json
from typing import Any

from src.loopie.artifacts import artifact_content_hash
from src.loopie.config import get_settings
from src.loopie.llm import DECISION_PROMPT_VERSION, DECISION_SCHEMA_VERSION, DEFAULT_PROVIDER
from src.loopie.runner import run_ticket, tickets_by_id
from src.loopie.stores.ledger import Ledger
from src.loopie.stores.llm_cache import cache_key, get_cached, set_cached
from src.loopie.stores.redis_store import RedisStore


def prewarm_decision_cache(
    case_id: str,
    *,
    redis: RedisStore | None = None,
    ledger: Ledger | None = None,
    artifact_version: str = "v1",
    action: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Seed replay cache with a known-good LLM decision payload (demo rehearsal)."""
    settings = get_settings()
    redis = redis or RedisStore()
    ticket = tickets_by_id()[case_id]
    artifacts = redis.get_live_artifacts()
    art_hash = artifact_content_hash(artifacts)
    key = cache_key(
        model=settings.openai_model,
        node="decision",
        fixture_id=case_id,
        artifact_version=artifact_version,
        provider=DEFAULT_PROVIDER,
        prompt_version=DECISION_PROMPT_VERSION,
        schema_version=DECISION_SCHEMA_VERSION,
        artifact_hash=art_hash,
    )
    if get_cached(key) is not None:
        return {"case_id": case_id, "cache_key": key, "prewarmed": False, "from_cache": True}

    resolved_action = action or ticket.get("expected_action", "escalate_manual_review")
    payload = {
        "action": resolved_action,
        "security_guard_observed": any(
            r.get("rule") == "security_flag_blocks_refund" for r in artifacts.get("routing_rules", [])
        ),
        "artifact_basis": ["memory:policy:refund_window", "routing:rules"],
        "reason": reason or f"prewarmed decision for {case_id}",
    }
    set_cached(key, json.dumps(payload))

    if settings.llm_mode == "live":
        run_ticket(
            ticket,
            redis=redis,
            ledger=ledger or Ledger.connect(),
            mode="live",
            artifact_version=artifact_version,
        )

    return {"case_id": case_id, "cache_key": key, "prewarmed": True, "action": resolved_action}
