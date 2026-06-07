"""Minimal HTTP API for Loopie cockpit actions."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.loopie.pipeline import LoopiePipeline
from src.loopie.preflight import run_preflight

_pipeline: LoopiePipeline | None = None


def get_pipeline() -> LoopiePipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = LoopiePipeline()
    return _pipeline


@asynccontextmanager
async def lifespan(_app: FastAPI):
    pipeline = get_pipeline()
    _app.state.preflight = pipeline.preflight
    yield


app = FastAPI(title="Loopie API", lifespan=lifespan)


class ActionRequest(BaseModel):
    case_id: str = "security_001"
    correction_id: str | None = None
    hero_case_id: str = "security_001"
    key: str = "routing:rules"


@app.get("/health")
def health():
    pipeline = get_pipeline()
    preflight = run_preflight(redis=pipeline.redis, ledger=pipeline.ledger)
    return {
        "status": "ok" if preflight["ok"] else "degraded",
        "ok": preflight["ok"],
        "hosted": preflight["hosted"],
        "persistence_mode": preflight["persistence_mode"],
        "provider_mode": preflight["provider_mode"],
        "llm_mode": preflight["llm_mode"],
    }


@app.get("/preflight")
def preflight():
    pipeline = get_pipeline()
    report = run_preflight(redis=pipeline.redis, ledger=pipeline.ledger)
    if report["hosted"] and not report["ok"]:
        raise HTTPException(status_code=503, detail=report)
    return report


@app.post("/reset")
def reset():
    return get_pipeline().reset()


@app.post("/seed")
def seed():
    return get_pipeline().seed()


@app.post("/run/baseline")
def run_baseline(body: ActionRequest):
    return get_pipeline().run_baseline(case_id=body.case_id)


@app.post("/corrections/propose")
def propose():
    return get_pipeline().propose_corrections()


@app.post("/corrections/approve")
def approve(body: ActionRequest):
    if not body.correction_id:
        return {"error": "correction_id required"}
    return get_pipeline().approve_correction(body.correction_id)


@app.post("/run/patched")
def run_patched(body: ActionRequest):
    return get_pipeline().run_patched(case_id=body.case_id)


@app.post("/counterfactual")
def counterfactual(body: ActionRequest):
    return get_pipeline().counterfactual_replay_suite(hero_case_id=body.hero_case_id)


@app.get("/state")
def state():
    return get_pipeline().export_state()


@app.get("/artifacts/{key:path}")
def artifacts(key: str):
    return get_pipeline().get_artifact_history(key)


@app.get("/budget")
def budget():
    return get_pipeline().get_budget_status()
