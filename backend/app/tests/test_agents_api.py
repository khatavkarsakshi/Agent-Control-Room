"""Agent CRUD API tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.models import Agent


def _payload(**overrides):
    payload = {
        "name": "Research Assistant",
        "description": "Finds and summarises sources",
        "model_name": "gpt-4o-mini",
        "system_prompt": "You are a careful research assistant.",
        "tools_config": [{"name": "calculator"}],
        "is_active": True,
    }
    payload.update(overrides)
    return payload


def test_create_agent(client: TestClient):
    response = client.post("/api/v1/agents", json=_payload())

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "Research Assistant"
    assert body["tools_config"] == [{"name": "calculator", "config": {}}]
    assert body["is_active"] is True
    assert body["id"]


def test_create_agent_rejects_unknown_tool(client: TestClient):
    response = client.post("/api/v1/agents", json=_payload(tools_config=[{"name": "rm_rf"}]))

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "bad_request"
    assert "rm_rf" in error["message"]
    assert error["details"]["unknown_tools"] == ["rm_rf"]


def test_duplicate_name_is_case_insensitive(client: TestClient):
    assert client.post("/api/v1/agents", json=_payload()).status_code == 201

    response = client.post("/api/v1/agents", json=_payload(name="research assistant"))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


def test_validation_errors_use_standard_envelope(client: TestClient):
    response = client.post("/api/v1/agents", json={"name": "x"})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert {detail["loc"][-1] for detail in error["details"]} >= {"model_name"}


def test_get_update_and_deactivate_agent(client: TestClient):
    created = client.post("/api/v1/agents", json=_payload()).json()
    agent_id = created["id"]

    fetched = client.get(f"/api/v1/agents/{agent_id}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == agent_id

    updated = client.patch(
        f"/api/v1/agents/{agent_id}",
        json={"description": None, "tools_config": ["current_time"]},
    )
    assert updated.status_code == 200
    assert updated.json()["description"] is None
    assert updated.json()["tools_config"] == [{"name": "current_time", "config": {}}]

    deleted = client.delete(f"/api/v1/agents/{agent_id}")
    assert deleted.status_code == 204

    assert client.get(f"/api/v1/agents/{agent_id}").json()["is_active"] is False


def test_update_rejects_null_required_field(client: TestClient):
    agent_id = client.post("/api/v1/agents", json=_payload()).json()["id"]

    response = client.patch(f"/api/v1/agents/{agent_id}", json={"model_name": None})

    assert response.status_code == 400
    assert "cannot be null" in response.json()["error"]["message"]


def test_missing_agent_returns_404(client: TestClient):
    response = client.get("/api/v1/agents/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_list_agents_filters_and_paginates(client: TestClient):
    for index in range(3):
        client.post(
            "/api/v1/agents",
            json=_payload(name=f"Agent {index}", description=f"number {index}"),
        )
    client.post("/api/v1/agents", json=_payload(name="Archived", is_active=False))

    active = client.get("/api/v1/agents", params={"is_active": True})
    assert active.status_code == 200
    assert active.json()["total"] == 3

    search = client.get("/api/v1/agents", params={"search": "Agent 1"})
    assert search.json()["total"] == 1
    assert search.json()["items"][0]["name"] == "Agent 1"

    page = client.get("/api/v1/agents", params={"limit": 1, "offset": 1})
    assert len(page.json()["items"]) == 1
    assert page.json()["total"] == 4


def test_search_treats_sql_wildcards_literally(client: TestClient):
    # Underscore is an allowed name character and also a LIKE wildcard.
    assert client.post("/api/v1/agents", json=_payload(name="Discount_100")).status_code == 201
    assert client.post("/api/v1/agents", json=_payload(name="Discount 200")).status_code == 201

    response = client.get("/api/v1/agents", params={"search": "_"})

    assert response.status_code == 200
    # "_" is matched as a literal character, not as a single-character wildcard.
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["name"] == "Discount_100"


def test_clone_agent_generates_unique_name(client: TestClient):
    source = client.post("/api/v1/agents", json=_payload()).json()

    first = client.post(f"/api/v1/agents/{source['id']}/clone", json={})
    second = client.post(f"/api/v1/agents/{source['id']}/clone", json={})
    named = client.post(
        f"/api/v1/agents/{source['id']}/clone", json={"name": "Copy of research"}
    )

    assert first.status_code == 201
    assert first.json()["name"] == "Research Assistant (copy)"
    assert second.json()["name"] == "Research Assistant (copy) 2"
    assert named.json()["name"] == "Copy of research"


def test_hard_delete_rejects_agent_with_runs(client: TestClient):
    agent = client.post("/api/v1/agents", json=_payload()).json()
    client.post(
        "/api/v1/runs",
        json={"agent_id": agent["id"], "input_prompt": "hello"},
    )

    response = client.delete(f"/api/v1/agents/{agent['id']}", params={"hard": True})

    assert response.status_code == 409
    assert response.json()["error"]["details"]["run_count"] == 1


def test_tools_endpoint_lists_builtin_tools(client: TestClient):
    response = client.get("/api/v1/tools")

    assert response.status_code == 200
    names = {tool["name"] for tool in response.json()}
    assert {"calculator", "current_time", "http_get"} <= names


def test_health_and_root(client: TestClient):
    health = client.get("/health")
    root = client.get("/")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert root.status_code == 200
    assert root.json()["api"] == "/api/v1"


def test_agent_runs_endpoint(client: TestClient, db):
    agent = client.post("/api/v1/agents", json=_payload()).json()
    client.post("/api/v1/runs", json={"agent_id": agent["id"], "input_prompt": "hello"})

    response = client.get(f"/api/v1/agents/{agent['id']}/runs")

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert db.query(Agent).count() == 1
