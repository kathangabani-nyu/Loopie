"""Loopie product API, durable worker, SSE, and native AG-UI composition root."""

from __future__ import annotations

import hmac
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.loopie.api.v1 import router as v1_router
from src.loopie.checkpointing import get_checkpoint_runtime
from src.loopie.config import get_settings
from src.loopie.copilot_endpoint import mount_copilotkit
from src.loopie.observability import ensure_weave
from src.loopie.preflight import run_preflight
from src.loopie.runner import seed_baseline
from src.loopie.runtime import RuntimeServices, build_runtime
from src.loopie.winloop import ensure_selector_event_loop_policy

# Must run before any event loop is created (i.e. before uvicorn/asyncio
# start serving) — see winloop.py for why.
ensure_selector_event_loop_policy()


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_weave()
    checkpoints = get_checkpoint_runtime()
    await checkpoints.start()
    runtime = build_runtime(checkpoints)
    app.state.runtime = runtime
    await runtime.start()
    try:
        yield
    finally:
        await runtime.close()
        await checkpoints.close()


app = FastAPI(title="Loopie API", version="2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().ui_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Last-Event-ID", "Idempotency-Key"],
)
app.include_router(v1_router)


def _runtime(request: Request) -> RuntimeServices:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="application runtime is not ready")
    return runtime


@app.middleware("http")
async def service_auth(request: Request, call_next):
    if request.method == "OPTIONS" or request.url.path == "/health":
        return await call_next(request)
    settings = get_settings()
    if not settings.api_token:
        if not settings.hosted:
            return await call_next(request)
        return JSONResponse({"error": "service authentication is not configured"}, status_code=503)
    scheme, _, candidate = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(candidate, settings.api_token):
        return JSONResponse({"error": "invalid service credentials"}, status_code=401)
    return await call_next(request)


@app.get("/health")
def health(request: Request):
    runtime = _runtime(request)
    report = run_preflight(redis=runtime.stores.redis, ledger=runtime.stores.ledger)
    return {
        "status": "ok" if report["ok"] else "degraded",
        "ok": report["ok"],
        "hosted": report["hosted"],
        "persistence_mode": report["persistence_mode"],
        "provider_mode": report["provider_mode"],
        "llm_mode": report["llm_mode"],
    }


@app.get("/preflight")
def preflight(request: Request):
    runtime = _runtime(request)
    report = run_preflight(redis=runtime.stores.redis, ledger=runtime.stores.ledger)
    if report["hosted"] and not report["ok"]:
        raise HTTPException(status_code=503, detail=report)
    return report


@app.post("/admin/reset")
def reset(request: Request):
    if not get_settings().enable_admin_reset:
        raise HTTPException(status_code=404, detail="admin reset is disabled")
    runtime = _runtime(request)
    runtime.stores.redis.flush_loopie_keys()
    runtime.stores.ledger.reset()
    return {"reset": True, **seed_baseline(redis=runtime.stores.redis, ledger=runtime.stores.ledger)}


mount_copilotkit(app)
