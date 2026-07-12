"""Per-run dependency injection for LangGraph swarm nodes."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from src.loopie.manifests import ManifestReader, RunManifest
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
    manifest: RunManifest
    manifest_reader: ManifestReader
    eval_scope: bool = False
    artifact_overrides: dict[str, Any] | None = None

    def read_artifact(self, key: str) -> Any:
        return self.manifest_reader.read(key)

    def artifacts(self) -> dict[str, Any]:
        values = self.manifest_reader.legacy_artifacts()
        values.update(self.artifact_overrides or {})
        return values

    def read_set(self) -> list[dict[str, str]]:
        return self.manifest_reader.read_set()


run_ctx: ContextVar[RunContext | None] = ContextVar("loopie_run_ctx", default=None)


def get_run_context() -> RunContext:
    ctx = run_ctx.get()
    if ctx is None:
        raise RuntimeError("Loopie run context is not set — invoke graph via run_ticket()")
    return ctx
