"""Control agent startup tests."""

from src.loopie.control_agent import build_graph, chat_api_key_configured
from memory_stores import MemoryLedger


def test_build_graph_without_openai_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    graph = build_graph()
    assert graph is not None
    assert chat_api_key_configured() is False


def test_build_graph_with_openai_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    graph = build_graph(ledger=MemoryLedger())
    assert graph is not None
