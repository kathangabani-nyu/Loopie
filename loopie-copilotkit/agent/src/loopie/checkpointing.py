"""Lifecycle-owned LangGraph checkpoint runtime.

Hosted execution uses an async Postgres saver. Local/test execution uses memory
unless a durable store is explicitly requested. The same checkpointer is shared
by the in-process AG-UI control graph; job scheduling remains a separate concern.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from src.loopie.config import get_settings


@dataclass
class CheckpointRuntime:
    checkpointer: Any
    pool: AsyncConnectionPool | None = None
    started: bool = False

    async def start(self) -> None:
        if self.started:
            return
        if self.pool is not None:
            await self.pool.open(wait=True, timeout=15)
            await self.checkpointer.setup()
        self.started = True

    async def close(self) -> None:
        if self.pool is not None and self.started:
            await self.pool.close(timeout=10)
        self.started = False


@lru_cache(maxsize=1)
def get_checkpoint_runtime() -> CheckpointRuntime:
    settings = get_settings()
    if not settings.requires_durable_stores:
        return CheckpointRuntime(checkpointer=InMemorySaver())

    pool = AsyncConnectionPool(
        conninfo=settings.postgres_url,
        min_size=1,
        max_size=5,
        open=False,
        timeout=15,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
        name="loopie-checkpoints",
    )
    return CheckpointRuntime(
        checkpointer=AsyncPostgresSaver(pool),
        pool=pool,
    )
