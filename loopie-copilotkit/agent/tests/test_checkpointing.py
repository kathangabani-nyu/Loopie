"""Checkpoint runtime lifecycle and local safety tests."""

from langgraph.checkpoint.memory import InMemorySaver

from src.loopie.checkpointing import get_checkpoint_runtime
from src.loopie.config import get_settings


def test_local_checkpoint_runtime_is_memory_and_lifecycle_is_idempotent(monkeypatch):
    monkeypatch.setenv("LOOPIE_HOSTED", "0")
    monkeypatch.setenv("LOOPIE_PERSISTENCE_MODE", "memory")
    get_settings.cache_clear()
    get_checkpoint_runtime.cache_clear()

    runtime = get_checkpoint_runtime()
    assert isinstance(runtime.checkpointer, InMemorySaver)
    assert runtime.pool is None

    import asyncio

    asyncio.run(runtime.start())
    asyncio.run(runtime.start())
    assert runtime.started is True
    asyncio.run(runtime.close())
    assert runtime.started is False

    get_checkpoint_runtime.cache_clear()
    get_settings.cache_clear()
