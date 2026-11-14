"""Replay API tests: start, list, detail, filters and cancellation."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.api.v1.endpoints import replays as replays_endpoint
from app.models import ReplaySession, ReplayStatus, Run, RunStatus


def _agent(client: TestClient, name: str = "replay-agent") -> dict:
    response = client.post(
        "/api/v1/agents",
        json={"name": name, "model_name": "gpt-4o-mini", "tools_config": ["calculator"]},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _run(client: TestClient, agent: dict, *, prompt: str = "What is 2 + 2?") -> dict:
    response = client.post(
        "/api/v1/runs", json={"agent_id": agent["id"], "input_prompt": prompt}
    )
    assert response.status_code == 202, response.text
    return response.json()


def _complete(db, run_id: str) -> None:
    db.expire_all()
    run = db.get(Run, UUID(run_id))
    assert run is not None
    run.status = RunStatus.COMPLETED
    run.output_response = "The answer is 4."
    run.total_tokens_used = 50
    run.total_cost_usd = Decimal("0.000050")
    run.latency_ms = 100
    db.commit()


def test_start_replay_creates_session(client: TestClient, db):
    agent = _agent(client)
    run = _run(client, agent)
    _complete(db, run["id"])

    response = client.post(
        f"/api/v1/runs/{run['id']}/replay",
        json={
            "mode": "live",
            "overrides": {
                "input_prompt": "What is 3 + 3?",
                "system_prompt": "Be brief.",
                "temperature": 0.1,
            },
        },
    )

    assert response.status_code == 202, response.text
    body = response.json()
    session = body["session"]
    assert session["status"] == "pending"
    assert session["original_run_id"] == run["id"]
    assert session["replay_run_id"] is not None
    assert session["overrides"]["mode"] == "live"
    assert session["overrides"]["system_prompt"] == "Be brief."
    assert session["original_run"]["id"] == run["id"]
    assert session["replay_run"]["replay_of_run_id"] == run["id"]
    assert session["replay_run"]["input_prompt"] == "What is 3 + 3?"
    assert body["task_id"].startswith("replay-task-")

    db.expire_all()
    stored = db.get(ReplaySession, UUID(session["id"]))
    assert stored is not None
    replay_run = db.get(Run, stored.replay_run_id)
    assert stored.status == ReplayStatus.PENDING
    assert replay_run.metadata_json["task_id"] == body["task_id"]
    assert replay_run.metadata_json["replay"]["overrides"] == {
        "system_prompt": "Be brief."
    }
    # The original run keeps its own result and status.
    original = db.get(Run, UUID(run["id"]))
    assert original.status == RunStatus.COMPLETED


def test_start_replay_for_unfinished_run_is_409(client: TestClient):
    agent = _agent(client)
    run = _run(client, agent)

    response = client.post(f"/api/v1/runs/{run['id']}/replay", json={"mode": "live"})

    assert response.status_code == 409


def test_start_offline_replay_without_recording_is_400(client: TestClient, db):
    agent = _agent(client)
    run = _run(client, agent)
    _complete(db, run["id"])

    response = client.post(f"/api/v1/runs/{run['id']}/replay", json={"mode": "offline"})

    assert response.status_code == 400
    assert "offline" in response.json()["error"]["message"]


def test_start_replay_with_unknown_tool_is_400(client: TestClient, db):
    agent = _agent(client)
    run = _run(client, agent)
    _complete(db, run["id"])

    response = client.post(
        f"/api/v1/runs/{run['id']}/replay",
        json={"mode": "live", "overrides": {"tools": ["not_a_tool"]}},
    )

    assert response.status_code == 400


def test_start_replay_for_unknown_run_is_404(client: TestClient):
    response = client.post(f"/api/v1/runs/{uuid4()}/replay", json={"mode": "live"})
    assert response.status_code == 404


def test_replay_broker_failure_marks_session_failed(
    client: TestClient, monkeypatch, db
):
    agent = _agent(client)
    run = _run(client, agent)
    _complete(db, run["id"])

    def _explode(_session_id):
        raise RuntimeError("broker down")

    monkeypatch.setattr(replays_endpoint.dispatch, "dispatch_replay", _explode)

    response = client.post(f"/api/v1/runs/{run['id']}/replay", json={"mode": "live"})

    assert response.status_code == 503
    db.expire_all()
    session = db.query(ReplaySession).one()
    assert session.status == ReplayStatus.FAILED
    assert session.comparison_result == {"error": "failed to enqueue replay"}


def test_list_and_get_replay_sessions(client: TestClient, db):
    agent = _agent(client)
    first = _run(client, agent, prompt="first")
    second = _run(client, agent, prompt="second")
    _complete(db, first["id"])
    _complete(db, second["id"])

    first_session = client.post(
        f"/api/v1/runs/{first['id']}/replay", json={"mode": "live"}
    ).json()["session"]
    second_session = client.post(
        f"/api/v1/runs/{second['id']}/replay", json={"mode": "live"}
    ).json()["session"]

    listing = client.get("/api/v1/replay-sessions")
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 2
    assert {item["id"] for item in body["items"]} == {
        first_session["id"],
        second_session["id"],
    }

    filtered = client.get(
        "/api/v1/replay-sessions", params={"original_run_id": first["id"]}
    )
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["id"] == first_session["id"]

    pending = client.get("/api/v1/replay-sessions", params=[("status", "pending")])
    assert pending.json()["total"] == 2
    completed = client.get("/api/v1/replay-sessions", params=[("status", "completed")])
    assert completed.json()["total"] == 0

    detail = client.get(f"/api/v1/replay-sessions/{first_session['id']}")
    assert detail.status_code == 200
    assert detail.json()["original_run"]["id"] == first["id"]
    assert detail.json()["replay_run"]["replay_of_run_id"] == first["id"]

    missing = client.get(f"/api/v1/replay-sessions/{uuid4()}")
    assert missing.status_code == 404


def test_cancel_replay_session(client: TestClient, db):
    agent = _agent(client)
    run = _run(client, agent)
    _complete(db, run["id"])
    session = client.post(
        f"/api/v1/runs/{run['id']}/replay", json={"mode": "live"}
    ).json()["session"]

    response = client.post(f"/api/v1/replay-sessions/{session['id']}/cancel")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "failed"
    assert body["replay_run"]["status"] == "failed"
    assert body["replay_run"]["error_message"] == "Cancelled by user"

    again = client.post(f"/api/v1/replay-sessions/{session['id']}/cancel")
    assert again.status_code == 409

    missing = client.post(f"/api/v1/replay-sessions/{uuid4()}/cancel")
    assert missing.status_code == 404


def test_replay_overrides_validation(client: TestClient, db):
    agent = _agent(client)
    run = _run(client, agent)
    _complete(db, run["id"])

    response = client.post(
        f"/api/v1/runs/{run['id']}/replay",
        json={"mode": "live", "overrides": {"temperature": 9}},
    )

    assert response.status_code == 422
