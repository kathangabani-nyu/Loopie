"""Minimal HTTP API for Loopie cockpit actions."""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from src.loopie.pipeline import LoopiePipeline

app = FastAPI(title="Loopie API")
_pipeline = LoopiePipeline()


class ActionRequest(BaseModel):
    case_id: str = "security_001"
    correction_id: str | None = None
    hero_case_id: str = "security_001"
    key: str = "routing:rules"


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/reset")
def reset():
    return _pipeline.reset()


@app.post("/seed")
def seed():
    return _pipeline.seed()


@app.post("/run/baseline")
def run_baseline(body: ActionRequest):
    return _pipeline.run_baseline(case_id=body.case_id)


@app.post("/corrections/propose")
def propose():
    return _pipeline.propose_corrections()


@app.post("/corrections/approve")
def approve(body: ActionRequest):
    if not body.correction_id:
        return {"error": "correction_id required"}
    return _pipeline.approve_correction(body.correction_id)


@app.post("/run/patched")
def run_patched(body: ActionRequest):
    return _pipeline.run_patched(case_id=body.case_id)


@app.post("/counterfactual")
def counterfactual(body: ActionRequest):
    return _pipeline.counterfactual_replay_suite(hero_case_id=body.hero_case_id)


@app.get("/state")
def state():
    return _pipeline.export_state()


@app.get("/artifacts/{key:path}")
def artifacts(key: str):
    return _pipeline.get_artifact_history(key)


@app.get("/budget")
def budget():
    return _pipeline.get_budget_status()
