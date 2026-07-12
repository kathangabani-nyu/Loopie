"""Provider registry + routing unit tests."""

from src.loopie.providers import is_gpt5_model, openai_client_kwargs, provider_registry, resolve_provider, role_provider_chain
from src.loopie.providers import ProviderConfig


def test_openai_enabled_when_key_present(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("LOOPIE_CURSOR_SMOKE_OK", raising=False)
    registry = provider_registry()
    assert registry["openai"].enabled is True
    assert registry["cursor"].enabled is False


def test_cursor_gated_behind_smoke_flag(monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", "cursor-key")
    monkeypatch.setenv("LOOPIE_PROVIDER_CURSOR_ENABLED", "true")
    monkeypatch.delenv("LOOPIE_CURSOR_SMOKE_OK", raising=False)
    registry = provider_registry()
    assert registry["cursor"].enabled is False

    monkeypatch.setenv("LOOPIE_CURSOR_SMOKE_OK", "1")
    registry = provider_registry()
    assert registry["cursor"].enabled is True


def test_gpt5_omits_temperature_and_seed_params():
    cfg = ProviderConfig(name="openai", base_url=None, api_key="k", model="gpt-5.5", enabled=True)
    kwargs = openai_client_kwargs(cfg)
    assert "temperature" not in kwargs
    assert kwargs["reasoning_effort"] == "none"
    assert is_gpt5_model("gpt-5.5-2026-04-23") is True


def test_resolve_provider_honors_chain(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("LOOPIE_PROVIDER_CHAIN", "cursor,openai")
    monkeypatch.delenv("LOOPIE_CURSOR_SMOKE_OK", raising=False)
    assert resolve_provider("decision").name == "openai"
    assert "openai" in role_provider_chain("decision")
