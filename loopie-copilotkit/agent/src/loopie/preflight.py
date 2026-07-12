"""Hosted readiness checks for Redis, Postgres, Weave, and provider mode."""

from __future__ import annotations

import os
from typing import Any

from src.loopie.config import get_settings
from src.loopie.providers import provider_registry
from src.loopie.stores.ledger import Ledger
from src.loopie.stores.redis_store import RedisStore


def _weave_enabled() -> bool:
    from src.loopie.observability import weave_tracing_enabled

    return weave_tracing_enabled()


def _weave_traces_url() -> str | None:
    from src.loopie.observability import weave_traces_url

    return weave_traces_url()


def _provider_mode() -> str:
    settings = get_settings()
    registry = provider_registry()
    enabled = [name for name, cfg in registry.items() if cfg.enabled]
    if settings.is_test:
        return "test"
    if enabled:
        return f"live:{','.join(enabled)}"
    return "live:unconfigured"


def run_preflight(
    *,
    redis: RedisStore | None = None,
    ledger: Ledger | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    redis = redis or RedisStore()
    ledger = ledger or Ledger.connect(strict=settings.hosted)

    redis_caps = redis.preflight_capabilities()
    redis_reachable = bool(redis_caps.get("ping"))
    redis_json = bool(redis_caps.get("json"))
    postgres_reachable = ledger.ping()
    persistence_mode = ledger.persistence_mode
    weave_enabled = _weave_enabled()
    weave_project_url = _weave_traces_url() if os.getenv("WANDB_API_KEY") else None
    weave_required = settings.weave_enabled
    weave_ready = (not weave_required) or (weave_enabled and bool(weave_project_url))
    provider_mode = _provider_mode()
    provider_ready = settings.is_test or provider_mode != "live:unconfigured"
    service_auth_ready = bool(settings.api_token)

    hosted_requirements_met = (
        redis_reachable
        and postgres_reachable
        and persistence_mode == "postgres"
        and weave_ready
        and provider_ready
        and service_auth_ready
    )
    ok = hosted_requirements_met if settings.hosted else True

    cursor_cfg = provider_registry().get("cursor")
    return {
        "ok": ok,
        "hosted": settings.hosted,
        "redis_reachable": redis_reachable,
        "redis_json": redis_json,
        "redis_capabilities": redis_caps,
        "postgres_reachable": postgres_reachable,
        "persistence_mode": persistence_mode,
        "weave_enabled": weave_enabled,
        "weave_configured": bool(os.getenv("WANDB_API_KEY")),
        "weave_flag": get_settings().weave_enabled,
        "weave_project_url": weave_project_url,
        "weave_dashboard_ready": bool(weave_project_url),
        "provider_mode": provider_mode,
        "provider_ready": provider_ready,
        "service_auth_ready": service_auth_ready,
        "llm_mode": settings.llm_mode,
        "full_agentic": settings.full_agentic,
        "cursor_provider_enabled": bool(cursor_cfg and cursor_cfg.enabled),
    }


def assert_hosted_ready(*, redis: RedisStore | None = None, ledger: Ledger | None = None) -> dict[str, Any]:
    """Hard-fail when hosted mode dependencies are missing."""
    report = run_preflight(redis=redis, ledger=ledger)
    if get_settings().hosted and not report["ok"]:
        missing = []
        if not report["redis_reachable"]:
            missing.append("redis")
        if not report["postgres_reachable"] or report["persistence_mode"] != "postgres":
            missing.append("postgres")
        if report["weave_flag"] and not report["weave_dashboard_ready"]:
            missing.append("wandb/weave dashboard")
        if not report["provider_ready"]:
            missing.append("live LLM provider")
        if not report["service_auth_ready"]:
            missing.append("service API token")
        raise RuntimeError(
            "Hosted Loopie preflight failed — audit persistence requires "
            f"{', '.join(missing)}. Set REDIS_URL, POSTGRES_URL, WANDB_API_KEY, "
            "and WANDB_ENTITY or disable LOOPIE_HOSTED."
        )
    return report
