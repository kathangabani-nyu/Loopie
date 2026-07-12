"""Lifecycle-owned consumer for the durable Postgres lease queue."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from src.loopie.jobs import Job, MemoryJobStore, PostgresJobStore
from src.loopie.services.runs import RunService

logger = logging.getLogger(__name__)
JobStore = MemoryJobStore | PostgresJobStore


class DurableWorker:
    def __init__(
        self,
        *,
        jobs: JobStore,
        runs: RunService,
        worker_id: str,
        lease_seconds: int = 30,
        poll_seconds: float = 0.25,
    ) -> None:
        self.jobs = jobs
        self.runs = runs
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.poll_seconds = poll_seconds
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name=f"loopie-worker:{self.worker_id}")

    async def close(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    async def run_once(self) -> bool:
        job = await self.jobs.claim(worker_id=self.worker_id, lease_seconds=self.lease_seconds)
        if job is None:
            return False
        await self._execute_claimed(job)
        return True

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                handled = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Loopie worker poll failed")
                handled = False
            if not handled:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
                except TimeoutError:
                    pass

    async def _execute_claimed(self, job: Job) -> None:
        assert job.lease_token
        heartbeat = asyncio.create_task(self._heartbeat(job), name=f"heartbeat:{job.id}")
        try:
            await self.runs.execute(job)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            failed_job = await self.jobs.fail(job_id=job.id, lease_token=job.lease_token, error=error)
            if failed_job is not None:
                run_id = str(job.payload.get("run_id", ""))
                if failed_job.status == "failed":
                    await self.runs.repository.fail_run(run_id, error)
                    self.runs.emit_event(
                        "run.failed",
                        {"run_id": run_id, "status": "failed", "error": error},
                    )
                else:
                    await self.runs.repository.mark_run_queued(run_id, error)
                    self.runs.emit_event(
                        "run.retrying",
                        {"run_id": run_id, "status": "queued", "error": error},
                    )
            logger.exception("Loopie job %s failed", job.id)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    async def _heartbeat(self, job: Job) -> None:
        assert job.lease_token
        interval = max(self.lease_seconds / 3, 1)
        while True:
            await asyncio.sleep(interval)
            renewed = await self.jobs.heartbeat(
                job_id=job.id,
                lease_token=job.lease_token,
                lease_seconds=self.lease_seconds,
            )
            if not renewed:
                raise RuntimeError("Job lease heartbeat was fenced")
