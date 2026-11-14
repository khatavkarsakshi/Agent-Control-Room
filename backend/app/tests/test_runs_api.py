"""Run creation, query, export and cancellation API tests."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.api.v1.endpoints import runs as runs_endpoint
from app.models import Run, RunStatus, SpanStatus, SpanType, Trace


def _create_agent(client: TestClient, *, name: str = "run-agent", active: bool = True) -> dict:
    response = client.post(
        "/api/v1/agents",
        json={
            "name": name,
            "model_name": "gpt-4o-mini",
            "tools_config": ["calculator"],
            "is_active": active,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_run_enqueues_task(client: TestClient):
    agent = _create_agent(client)

    response = client.post(
        "/api/v1/runs",
        json={
            "agent_id": agent["id"],
            "input_prompt": "Summarise the latest release notes",
            "metadata": {"source": "pytest"},
            "execution_options": {"max_iterations": 3, "timeout_seconds": 60},
        },
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "pending"
    assert body["agent_id"] == agent["id"]
    assert body["metadata"]["source"] == "pytest"
    assert body["metadata"]["requested_by"] == "api"
    assert body["metadata"]["task_id"] == f"task-{body['id']}"
    assert body["metadata"]["execution"]["max_iterations"] == 3


def test_create_run_for_unknown_agent_is_404(client: TestClient):
    response = client.post(
        "/api/v1/runs",
        json={
            "agent_id": "00000000-0000-0000-0000-000000000000",
            "input_prompt": "hello",
        },
    )

    assert response.status_code == 404


def test_create_run_for_inactive_agent_is_409(client: TestClient):
    agent = _create_agent(client, active=False)

    response = client.post(
        "/api/v1/runs", json={"agent_id": agent["id"], "input_prompt": "hello"}
    )

    assert response.status_code == 409
    assert "inactive" in response.json()["error"]["message"]


def test_broker_failure_marks_run_failed(
    client: TestClient, monkeypatch, db
):
    agent = _create_agent(client)

    def _explode(_run_id):
        raise RuntimeError("broker down")

    monkeypatch.setattr(runs_endpoint.dispatch, "dispatch_run", _explode)

    response = client.post(
        "/api/v1/runs", json={"agent_id": agent["id"], "input_prompt": "hello"}
    )

    assert response.status_code == 503
    db.expire_all()
    run = db.query(Run).one()
    assert run.status == RunStatus.FAILED
    assert "enqueue" in (run.error_message or "")


def test_list_runs_filters_and_sorts(client: TestClient, db):
    agent = _create_agent(client)
    for prompt in ("alpha task", "beta task", "gamma"):
        client.post("/api/v1/runs", json={"agent_id": agent["id"], "input_prompt": prompt})

    db.expire_all()
    runs = db.query(Run).order_by(Run.created_at).all()
    runs[0].status = RunStatus.COMPLETED
    runs[0].total_cost_usd = Decimal("0.500000")
    runs[0].total_tokens_used = 5_000
    runs[1].status = RunStatus.FAILED
    runs[1].total_cost_usd = Decimal("0.010000")
    runs[1].total_tokens_used = 100
    db.commit()

    completed = client.get("/api/v1/runs", params={"status": "completed"})
    assert completed.json()["total"] == 1

    searched = client.get("/api/v1/runs", params={"search": "beta"})
    assert searched.json()["total"] == 1

    costly = client.get(
        "/api/v1/runs", params={"min_cost": 0.1, "sort": "-total_cost_usd"}
    )
    assert costly.json()["total"] == 1
    assert costly.json()["items"][0]["total_cost_usd"] == 0.5


def test_get_run_includes_traces(client: TestClient, db):
    agent = _create_agent(client)
    run_id = client.post(
        "/api/v1/runs", json={"agent_id": agent["id"], "input_prompt": "trace me"}
    ).json()["id"]

    trace = Trace(
        run_id=UUID(run_id),
        span_name="llm.gpt-4o-mini",
        span_type=SpanType.LLM_CALL,
        status=SpanStatus.OK,
        trace_id=uuid4().hex,
        span_id=uuid4().hex[:16],
        tokens_used=30,
        tokens_input=20,
        tokens_output=10,
        cost_usd=Decimal("0.000030"),
        model_name="gpt-4o-mini",
        input_data={"messages": []},
        output_data={"content": "hi"},
    )
    db.add(trace)
    db.commit()

    response = client.get(f"/api/v1/runs/{run_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == run_id
    assert len(body["traces"]) == 1
    assert body["traces"][0]["span_type"] == "llm_call"
    assert body["traces"][0]["tokens_input"] == 20


def test_export_run_returns_attachment(client: TestClient, db):
    agent = _create_agent(client)
    run_id = client.post(
        "/api/v1/runs", json={"agent_id": agent["id"], "input_prompt": "export me"}
    ).json()["id"]
    db.add(
        Trace(
            run_id=UUID(run_id),
            span_name="agent.run",
            span_type=SpanType.DECISION,
            trace_id=uuid4().hex,
            span_id=uuid4().hex[:16],
        )
    )
    db.commit()

    response = client.get(f"/api/v1/runs/{run_id}/export")

    assert response.status_code == 200
    assert response.headers["content-disposition"] == f'attachment; filename="run-{run_id}.json"'
    body = response.json()
    assert body["run"]["id"] == run_id
    assert body["agent"]["name"] == "run-agent"
    assert len(body["traces"]) == 1


def test_cancel_run_is_idempotent_for_terminal_runs(client: TestClient):
    agent = _create_agent(client)
    run_id = client.post(
        "/api/v1/runs", json={"agent_id": agent["id"], "input_prompt": "cancel me"}
    ).json()["id"]

    cancelled = client.post(f"/api/v1/runs/{run_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "failed"
    assert cancelled.json()["error_message"] == "Cancelled by user"

    again = client.post(f"/api/v1/runs/{run_id}/cancel")
    assert again.status_code == 409


def test_stale_cancel_does_not_overwrite_completed_run(client: TestClient, db):
    agent = _create_agent(client)
    run_id = client.post(
        "/api/v1/runs", json={"agent_id": agent["id"], "input_prompt": "done"}
    ).json()["id"]
    db.query(Run).filter(Run.id == UUID(run_id)).update({Run.status: RunStatus.COMPLETED})
    db.commit()

    response = client.post(f"/api/v1/runs/{run_id}/cancel")

    assert response.status_code == 409
    db.expire_all()
    assert db.get(Run, UUID(run_id)).status == RunStatus.COMPLETED


def test_streaming_returns_snapshot_and_disabled_notice(client: TestClient):
    agent = _create_agent(client)
    run_id = client.post(
        "/api/v1/runs", json={"agent_id": agent["id"], "input_prompt": "stream me"}
    ).json()["id"]

    with client.stream("GET", f"/api/v1/runs/{run_id}/stream") as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: run.snapshot" in body
    assert "event: stream.unavailable" in body


def test_websocket_snapshot_for_terminal_run(client: TestClient, db):
    agent = _create_agent(client)
    run_id = client.post(
        "/api/v1/runs", json={"agent_id": agent["id"], "input_prompt": "ws me"}
    ).json()["id"]
    db.query(Run).filter(Run.id == UUID(run_id)).update({Run.status: RunStatus.COMPLETED})
    db.commit()

    with client.websocket_connect(f"/api/v1/runs/{run_id}/ws") as websocket:
        message = websocket.receive_json()
        assert message["type"] == "run.snapshot"
        assert message["data"]["run"]["status"] == "completed"
        close = websocket.receive()
        assert close["type"] == "websocket.close"


def test_trace_endpoints(client: TestClient, db):
    agent = _create_agent(client)
    run_id = client.post(
        "/api/v1/runs", json={"agent_id": agent["id"], "input_prompt": "tree"}
    ).json()["id"]

    trace_id = uuid4().hex
    root = Trace(
        run_id=UUID(run_id),
        span_name="agent.run",
        span_type=SpanType.DECISION,
        trace_id=trace_id,
        span_id=uuid4().hex[:16],
        duration_ms=120,
    )
    db.add(root)
    db.commit()
    db.refresh(root)
    child = Trace(
        run_id=UUID(run_id),
        parent_trace_id=root.id,
        span_name="llm.gpt-4o-mini",
        span_type=SpanType.LLM_CALL,
        trace_id=trace_id,
        span_id=uuid4().hex[:16],
        duration_ms=80,
        tokens_used=30,
    )
    db.add(child)
    db.commit()

    tree = client.get("/api/v1/traces/tree", params={"run_id": run_id})
    assert tree.status_code == 200
    assert tree.json()["span_count"] == 2
    assert tree.json()["roots"][0]["span_name"] == "agent.run"
    assert tree.json()["roots"][0]["children"][0]["span_name"] == "llm.gpt-4o-mini"

    listed = client.get("/api/v1/traces", params={"run_id": run_id})
    assert listed.json()["total"] == 2

    by_trace = client.get(f"/api/v1/traces/by-trace-id/{trace_id}")
    assert len(by_trace.json()) == 2

    single = client.get(f"/api/v1/traces/{child.id}")
    assert single.status_code == 200
    assert single.json()["tokens_used"] == 30

    missing = client.get(f"/api/v1/traces/{uuid4()}")
    assert missing.status_code == 404
