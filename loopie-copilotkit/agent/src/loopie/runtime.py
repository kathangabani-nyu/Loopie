"""Application lifecycle composition root."""

from __future__ import annotations

import asyncio
import os
import socket
from dataclasses import dataclass
from typing import Any

from src.loopie.checkpointing import CheckpointRuntime
from src.loopie.config import get_settings
from src.loopie.jobs import MemoryJobStore, PostgresJobStore
from src.loopie.preflight import assert_hosted_ready, run_preflight
from src.loopie.product_repository import MemoryProductRepository, PostgresProductRepository, ProductRepository
from src.loopie.reliability.judge import AdvisoryJudge
from src.loopie.services.runs import RunService
from src.loopie.services.approvals import ApprovalService
from src.loopie.services.corrections import CorrectionService
from src.loopie.stores.ledger import Ledger
from src.loopie.stores.redis_store import RedisStore
from src.loopie.worker import DurableWorker


@dataclass(frozen=True)
class StoreBundle:
    redis: RedisStore
    ledger: Ledger
    preflight: dict[str, Any]


def build_stores() -> StoreBundle:
    settings = get_settings()
    redis = RedisStore()
    ledger = Ledger.connect(strict=settings.hosted)
    preflight = (
        assert_hosted_ready(redis=redis, ledger=ledger)
        if settings.hosted
        else run_preflight(redis=redis, ledger=ledger)
    )
    return StoreBundle(redis=redis, ledger=ledger, preflight=preflight)


@dataclass
class RuntimeServices:
    stores: StoreBundle
    repository: ProductRepository
    jobs: MemoryJobStore | PostgresJobStore
    runs: RunService
    approvals: ApprovalService
    corrections: CorrectionService
    worker: DurableWorker

    async def start(self) -> None:
        from src.loopie.reliability.corrections import project_pending_outbox

        project_pending_outbox(ledger=self.stores.ledger, redis=self.stores.redis)
        await self.worker.start()

    async def close(self) -> None:
        await self.worker.close()
        await asyncio.to_thread(self.stores.redis.close)


def build_runtime(
    checkpoints: CheckpointRuntime,
    *,
    stores: StoreBundle | None = None,
) -> RuntimeServices:
    stores = stores or build_stores()
    if checkpoints.pool is None:
        repository: ProductRepository = MemoryProductRepository()
        jobs: MemoryJobStore | PostgresJobStore = MemoryJobStore()
    else:
        repository = PostgresProductRepository(checkpoints.pool)
        jobs = PostgresJobStore(checkpoints.pool)
    runs = RunService(
        repository=repository,
        jobs=jobs,
        redis=stores.redis,
        ledger=stores.ledger,
        judge=AdvisoryJudge(),
    )
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    worker = DurableWorker(jobs=jobs, runs=runs, worker_id=worker_id)
    approvals = ApprovalService(ledger=stores.ledger, redis=stores.redis, runs=runs)
    corrections = CorrectionService(
        repository=repository,
        redis=stores.redis,
        ledger=stores.ledger,
    )
    return RuntimeServices(
        stores=stores,
        repository=repository,
        jobs=jobs,
        runs=runs,
        approvals=approvals,
        corrections=corrections,
        worker=worker,
    )
