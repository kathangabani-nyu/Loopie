"""FastAPI service-token, CORS, and destructive-admin boundary tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

import loopie_server
from src.loopie.config import get_settings

APP_UI_ORIGIN = get_settings().ui_origin


class StubRepository:
    async def get_project(self):
        return {
            "id": "00000000-0000-0000-0000-000000000001",
            "slug": "default",
            "name": "Stub",
            "scope": "test",
            "action_taxonomy": ["escalate_manual_review"],
        }


class StubRuntime:
    repository = StubRepository()


def _configure(monkeypatch, *, hosted: bool = True, reset: bool = False):
    monkeypatch.setenv("LOOPIE_HOSTED", "1" if hosted else "0")
    monkeypatch.setenv("LOOPIE_API_TOKEN", "test-service-token")
    monkeypatch.setenv("LOOPIE_UI_ORIGIN", "https://loopie.example")
    monkeypatch.setenv("LOOPIE_ENABLE_ADMIN_RESET", "1" if reset else "0")
    get_settings.cache_clear()
    loopie_server.app.state.runtime = StubRuntime()


def test_api_rejects_missing_service_token(monkeypatch):
    _configure(monkeypatch)
    # No `with` block: the installed Starlette TestClient only drives the
    # app's real lifespan (real Postgres/Redis startup) as a context manager.
    # A plain instance skips it, leaving the manually-injected StubRuntime in
    # place — that IS this test suite's "lifespan off" mechanism.
    client = TestClient(loopie_server.app)
    try:
        response = client.get("/api/v1/meta")
        assert response.status_code == 401
    finally:
        client.close()


def test_api_accepts_valid_service_token(monkeypatch):
    _configure(monkeypatch)
    client = TestClient(loopie_server.app)
    try:
        response = client.get(
            "/api/v1/meta", headers={"Authorization": "Bearer test-service-token"}
        )
        assert response.status_code == 200
        assert response.json()["project"]["slug"] == "default"
    finally:
        client.close()


def test_admin_reset_is_disabled_by_default(monkeypatch):
    _configure(monkeypatch, reset=False)
    client = TestClient(loopie_server.app)
    try:
        response = client.post(
            "/admin/reset", headers={"Authorization": "Bearer test-service-token"}
        )
        assert response.status_code == 404
    finally:
        client.close()


def test_cors_preflight_allows_only_configured_ui(monkeypatch):
    _configure(monkeypatch)
    client = TestClient(loopie_server.app)
    try:
        response = client.options(
            "/api/v1/meta",
            headers={
                "Origin": APP_UI_ORIGIN,
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == APP_UI_ORIGIN

        rejected = client.options(
            "/api/v1/meta",
            headers={
                "Origin": "https://attacker.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert rejected.status_code == 400
    finally:
        client.close()
