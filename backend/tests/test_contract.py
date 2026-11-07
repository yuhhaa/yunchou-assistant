import pytest
from sqlalchemy import func, select

from app.models import Incident, Job, OutboxEvent


def test_context_is_scoped_and_selection_is_exact(flow):
    first = flow.context(order_no="O001")
    second = flow.context(order_no="O002")
    assert first["selected_order_no"] == "O001"
    assert second["selected_order_no"] == "O002"
    assert len(first["orders"]) == 3
    assert first["clock_mode"] == "SIMULATION"
    assert "routing" not in first


@pytest.mark.parametrize(
    "patch",
    [
        {"delay_minutes": "20"},
        {"delay_minutes": True},
        {"delay_minutes": None},
        {"role": "DISPATCHER"},
        {"occurred_at": "2026-10-26T10:00:00"},
    ],
)
def test_strict_incident_rejects_without_write(flow, patch):
    body = {
        "mode": "CREATE",
        "event_type": "ROAD_DELAY",
        "vehicle_id": "V01",
        "description": "SIMULATION",
        "delay_minutes": 20,
        **patch,
    }
    response = flow.post("/incidents", body)
    assert response.status_code == 422
    assert response.json()["code"] == (
        "MISSING_FIELDS" if patch.get("delay_minutes", 20) is None else "INVALID_INPUT"
    )
    with flow.factory() as session:
        assert session.scalar(select(func.count()).select_from(Incident)) == 0


def test_zero_delay_and_durable_async_acceptance(flow):
    context, incident = flow.context(), flow.incident(0)
    response = flow.post(
        "/impact-analyses",
        {
            "incident_id": incident["incident_id"],
            "expected_incident_version": 1,
            "snapshot_id": context["snapshot_id"],
        },
    )
    assert response.status_code == 202
    assert response.json()["code"] == "ACCEPTED"
    job_id = response.json()["data"]["job_id"]
    with flow.factory() as session:
        assert session.get(Job, job_id).status == "QUEUED"
        assert session.scalar(select(OutboxEvent)).payload["job_id"] == job_id


def test_openapi_exact_tool_names(flow):
    schema = flow.client.get("/openapi.json").json()
    names = {
        operation["operationId"]
        for path, ops in schema["paths"].items()
        if path.startswith("/api/v1/agent-tools")
        for operation in ops.values()
    }
    assert names == {
        "get_delivery_context",
        "upsert_incident",
        "analyze_impact",
        "generate_recovery_plans",
        "get_operation_status",
    }
    workspace = schema["paths"]["/api/v1/incidents/{incident_id}/workspace"]["get"]
    assert workspace["security"] == [{"BrowserLogin": []}]
    assert (
        "IncidentWorkspaceOutput"
        in workspace["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    )


def test_openapi_declares_separate_tool_service_and_task_security(flow):
    schema = flow.client.get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/agent-tools/incidents"]["post"]
    assert operation["security"] == [{"ToolServiceKey": [], "TaskContext": []}]
    headers = {p["name"]: p for p in operation["parameters"] if p["in"] == "header"}
    assert headers["Idempotency-Key"]["required"] is True


@pytest.mark.parametrize("object_type", ["REQUEST", "OPERATION"])
def test_tool_cannot_recover_chat_operation_from_persisted_target(flow, object_type):
    from datetime import UTC, datetime, timedelta

    from app.models import Operation

    with flow.factory() as session:
        session.add(
            Job(
                id="CHAT1",
                tenant_id="DEMO",
                user_id="DRIVER01",
                operation_id="CHATOP1",
                job_type="CHAT",
                input={},
                status="FAILED",
            )
        )
        session.add(
            Operation(
                id="CHATOP1",
                tenant_id="DEMO",
                user_id="DRIVER01",
                action="chat",
                idempotency_key="chat-request",
                body_hash="0" * 64,
                target_type="JOB",
                target_id="CHAT1",
                response={"data": None, "error": {"code": "INTERNAL_ERROR"}},
                response_status=500,
                status="FAILED",
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        session.commit()
    params = {
        "object_type": object_type,
        "object_id": "chat-request" if object_type == "REQUEST" else "CHATOP1",
    }
    assert flow.get("/status", params=params).status_code == 200
    token = flow.post("/auth/task-token", {}).json()["data"]["task_token"]
    response = flow.client.get(
        "/api/v1/agent-tools/status",
        params=params,
        headers={"Authorization": "Bearer service-test", "X-Task-Token": token},
    )
    assert response.status_code == 404


def test_missing_routing_input_rejected_before_job_submission(flow):
    from app.models import RoutingGraph

    with flow.factory() as session:
        session.get(RoutingGraph, "DEMO_GRAPH").matrix = {}
        session.commit()
    snapshot, incident = flow.context(), flow.incident()
    response = flow.post(
        "/impact-analyses",
        {
            "incident_id": incident["incident_id"],
            "expected_incident_version": 1,
            "snapshot_id": snapshot["snapshot_id"],
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "DATA_INCOMPLETE"
    with flow.factory() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 0


def test_missing_road_estimate_returns_named_field_and_ask_user(flow):
    response = flow.post(
        "/incidents",
        {
            "mode": "CREATE",
            "event_type": "ROAD_DELAY",
            "vehicle_id": "V01",
            "description": "Traffic delay; estimate unknown",
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "MISSING_FIELDS"
    assert response.json()["error"]["missing_fields"] == ["delay_minutes"]
    assert response.json()["error"]["next_action"] == "ASK_USER"
