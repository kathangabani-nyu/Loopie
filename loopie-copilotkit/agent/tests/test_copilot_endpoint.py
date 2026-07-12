"""Proof that the control graph can be served from the FastAPI process."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from src.loopie.control_agent import build_unconfigured_chat_graph
from src.loopie.copilot_endpoint import mount_copilotkit


def test_in_process_copilotkit_health_endpoint_lists_control_agent():
    app = FastAPI()
    mount_copilotkit(
        app,
        graph=build_unconfigured_chat_graph("test", checkpointer=InMemorySaver()),
    )

    response = TestClient(app).get("/api/copilotkit/agent/loopie_control/health")

    assert response.status_code == 200
    assert response.json()["agent"]["name"] == "loopie_control"


def test_in_process_copilotkit_endpoint_streams_a_graph_run():
    app = FastAPI()
    mount_copilotkit(
        app,
        graph=build_unconfigured_chat_graph("test", checkpointer=InMemorySaver()),
    )

    response = TestClient(app).post(
        "/api/copilotkit/agent/loopie_control",
        json={
            "threadId": "thread-test",
            "runId": "run-test",
            "state": {},
            "messages": [{"id": "message-test", "role": "user", "content": "hello"}],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        },
        headers={"Accept": "text/event-stream"},
    )

    assert response.status_code == 200
    assert "RUN_STARTED" in response.text
    assert "RUN_FINISHED" in response.text
