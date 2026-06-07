"""Per-run dependency injection for LangGraph swarm nodes."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from src.loopie.reliability.budget import BudgetTracker
from src.loopie.stores.ledger import Ledger
from src.loopie.stores.redis_store import RedisStore


@dataclass
class RunContext:
    redis: RedisStore
    ledger: Ledger
    mode: str | None
    artifact_version: str
    budget: BudgetTracker
    eval_scope: bool = False
    artifacts: dict[str, Any] | None = None


run_ctx: ContextVar[RunContext | None] = ContextVar("loopie_run_ctx", default=None)


def get_run_context() -> RunContext:
    ctx = run_ctx.get()
    if ctx is None:
        raise RuntimeError("Loopie run context is not set — invoke graph via run_ticket()")
    return ctx
