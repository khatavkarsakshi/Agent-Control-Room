"""Evaluation API tests: catalog, sync evaluation, summary, batch, delete."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.api.v1.endpoints import evaluations as evaluations_endpoint
from app.models import Evaluation, EvaluatorType, Run, RunStatus

ANSWER = (
    "Water evaporates from lakes and oceans, condenses into clouds, "
    "and falls back as precipitation."
)
CONTEXT = (
    "Water evaporates from lakes and oceans when the sun heats the surface. "
    "The vapour condenses into clouds and falls back as rain or snow."
)
REFERENCE = "Water evaporates, condenses into clouds and falls as precipitation."
PROMPT = "Explain the water cycle and how clouds form."


def _agent(client: TestClient, name: str = "eval-agent") -> dict:
    response = client.post(
        "/api/v1/agents",
        json={"name": name, "model_name": "gpt-4o-mini", "tools_config": ["calculator"]},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _run(client: TestClient, agent: dict, *, prompt: str = PROMPT) -> dict:
    response = client.post(
        "/api/v1/runs", json={"agent_id": agent["id"], "input_prompt": prompt}
    )
    assert response.status_code == 202, response.text
    return response.json()


def _complete(db, run_id: str, **overrides) -> None:
    db.expire_all()
    run = db.get(Run, UUID(run_id))
    assert run is not None
    run.status = RunStatus.COMPLETED
    run.output_response = overrides.get("output_response", ANSWER)
    run.input_prompt = overrides.get("input_prompt", PROMPT)
    run.total_tokens_used = overrides.get("total_tokens_used", 90)
    run.total_cost_usd = Decimal("0.000090")
    run.latency_ms = 180
    db.commit()


def test_catalog_endpoint(client: TestClient):
    response = client.get("/api/v1/evaluations/metrics")

    assert response.status_code == 200
    body = response.json()
    custom = next(item for item in body["evaluators"] if item["evaluator_type"] == "custom")
    assert custom["available"] is True
    assert custom["metrics"]


def test_evaluate_run_and_read_it_back(client: TestClient, db):
    agent = _agent(client)
    run = _run(client, agent)
    _complete(db, run["id"])

    response = client.post(
        f"/api/v1/runs/{run['id']}/evaluations",
        json={
            "evaluator_type": "custom",
            "metrics": ["answer_relevancy", "faithfulness", "answer_correctness"],
            "contexts": [CONTEXT],
            "reference": REFERENCE,
        },
    )

    assert response.status_code == 200, response.text
    report = response.json()
    assert report["run_id"] == run["id"]
    assert report["evaluator_type"] == "custom"
    assert {item["metric_name"] for item in report["evaluations"]} == {
        "answer_relevancy",
        "faithfulness",
        "answer_correctness",
    }
    assert all(0 <= item["score"] <= 1 for item in report["evaluations"])
    assert report["skipped"] == []
    assert report["average_score"] is not None

    summary = client.get(f"/api/v1/runs/{run['id']}/evaluations")
    assert summary.status_code == 200
    assert len(summary.json()["evaluations"]) == 3
    assert summary.json()["by_metric"]["faithfulness"]["count"] == 1

    # Run detail embeds the persisted scores for the dashboard.
    detail = client.get(f"/api/v1/runs/{run['id']}")
    assert detail.status_code == 200
    assert len(detail.json()["evaluations"]) == 3


def test_evaluate_without_output_is_400(client: TestClient, db):
    agent = _agent(client)
    run = _run(client, agent)

    response = client.post(
        f"/api/v1/runs/{run['id']}/evaluations", json={"evaluator_type": "custom"}
    )

    assert response.status_code == 400
    assert "no output" in response.json()["error"]["message"]


def test_evaluate_unknown_metric_is_400(client: TestClient, db):
    agent = _agent(client)
    run = _run(client, agent)
    _complete(db, run["id"])

    response = client.post(
        f"/api/v1/runs/{run['id']}/evaluations",
        json={"evaluator_type": "custom", "metrics": ["nope"]},
    )

    assert response.status_code == 400
    assert "Unknown metric" in response.json()["error"]["message"]


def test_evaluate_optional_evaluator_unavailable_is_503(client: TestClient, db):
    catalog = client.get("/api/v1/evaluations/metrics").json()
    ragas = next(item for item in catalog["evaluators"] if item["evaluator_type"] == "ragas")
    if ragas["available"]:
        import pytest

        pytest.skip("ragas is installed")

    agent = _agent(client)
    run = _run(client, agent)
    _complete(db, run["id"])

    response = client.post(
        f"/api/v1/runs/{run['id']}/evaluations", json={"evaluator_type": "ragas"}
    )

    assert response.status_code == 503


def test_delete_evaluations(client: TestClient, db):
    agent = _agent(client)
    run = _run(client, agent)
    _complete(db, run["id"])
    client.post(
        f"/api/v1/runs/{run['id']}/evaluations",
        json={"evaluator_type": "custom", "metrics": ["answer_relevancy"]},
    )
    assert db.query(Evaluation).count() == 1

    response = client.delete(f"/api/v1/runs/{run['id']}/evaluations")

    assert response.status_code == 200
    assert response.json() == {"run_id": run["id"], "deleted": 1}
    assert db.query(Evaluation).count() == 0

    again = client.delete(f"/api/v1/runs/{run['id']}/evaluations")
    assert again.json()["deleted"] == 0


def test_batch_evaluation_enqueues_task(client: TestClient, db):
    agent = _agent(client)
    first = _run(client, agent, prompt="first prompt")
    second = _run(client, agent, prompt="second prompt")
    _complete(db, first["id"])
    _complete(db, second["id"])

    response = client.post(
        "/api/v1/evaluations/batch",
        json={"run_ids": [first["id"], second["id"]], "evaluator_type": "custom"},
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["requested"] == 2
    assert body["run_ids"] == [first["id"], second["id"]]
    assert body["task_id"] == "eval-task-2"


def test_batch_evaluation_selects_completed_runs(client: TestClient, db):
    agent = _agent(client)
    completed = _run(client, agent)
    _complete(db, completed["id"])
    _run(client, agent, prompt="still pending")

    response = client.post(
        "/api/v1/evaluations/batch",
        json={"agent_id": agent["id"], "status": "completed", "limit": 10},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["run_ids"] == [completed["id"]]
    assert body["task_id"] == "eval-task-1"


def test_batch_evaluation_unknown_run_is_400(client: TestClient):
    response = client.post(
        "/api/v1/evaluations/batch",
        json={"run_ids": [str(uuid4())]},
    )

    assert response.status_code == 400
    assert "Unknown run" in response.json()["error"]["message"]


def test_batch_evaluation_with_no_matches_returns_empty(client: TestClient, db):
    agent = _agent(client)
    _run(client, agent)

    response = client.post(
        "/api/v1/evaluations/batch",
        json={"agent_id": agent["id"], "status": "completed"},
    )

    assert response.status_code == 202
    assert response.json() == {"task_id": None, "requested": 0, "run_ids": []}


def test_batch_evaluation_broker_failure_is_503(client: TestClient, monkeypatch, db):
    agent = _agent(client)
    run = _run(client, agent)
    _complete(db, run["id"])

    def _explode(*_args, **_kwargs):
        raise RuntimeError("broker down")

    monkeypatch.setattr(evaluations_endpoint.dispatch, "dispatch_evaluation", _explode)

    response = client.post(
        "/api/v1/evaluations/batch", json={"run_ids": [run["id"]]}
    )

    assert response.status_code == 503


def test_evaluation_of_unknown_run_is_404(client: TestClient):
    response = client.post(
        f"/api/v1/runs/{uuid4()}/evaluations", json={"evaluator_type": "custom"}
    )
    assert response.status_code == 404


def test_aggregate_endpoint_filters(client: TestClient, db):
    agent = _agent(client)
    run = _run(client, agent)
    _complete(db, run["id"])
    client.post(
        f"/api/v1/runs/{run['id']}/evaluations",
        json={"evaluator_type": "custom", "metrics": ["answer_relevancy"]},
    )

    response = client.get("/api/v1/evaluations/summary", params={"agent_id": agent["id"]})

    assert response.status_code == 200
    body = response.json()
    assert body["total_evaluations"] == 1
    item = body["items"][0]
    assert item["evaluator_type"] == "custom"
    assert item["metric_name"] == "answer_relevancy"
    assert item["run_count"] == 1
    assert item["average"] == item["minimum"] == item["maximum"]

    empty = client.get(
        "/api/v1/evaluations/summary",
        params={"agent_id": agent["id"], "evaluator_type": "deepeval"},
    )
    assert empty.json()["items"] == []
