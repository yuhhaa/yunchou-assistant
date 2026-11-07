import pytest

from app.models import Job


def test_browser_workspace_discovers_driver_candidates_and_rechecks_access_and_expiry(flow):
    from datetime import timedelta

    from sqlalchemy import select

    from app import models as m

    plan = flow.generate()
    incident_id = plan["incident_id"]
    path = f"/incidents/{incident_id}/workspace"
    assert incident_id in {
        i["incident_id"] for i in flow.get("/incidents", "dispatcher").json()["data"]["incidents"]
    }
    response = flow.get(path, "dispatcher")
    assert response.status_code == 200, response.text
    workspace = response.json()["data"]
    from app.outputs import Envelope, IncidentWorkspaceOutput

    Envelope[IncidentWorkspaceOutput].model_validate(response.json())
    assert set(workspace) == {"incident", "analyses", "plans"}
    assert workspace["incident"]["incident_id"] == incident_id
    assert plan["plan_id"] in {p["plan_id"] for p in workspace["plans"]}
    assert plan["analysis_id"] in {a["analysis_id"] for a in workspace["analyses"]}
    assert flow.get(path).status_code == 200
    assert flow.get(path, "driver02").status_code == 404
    assert flow.get("/incidents/unknown/workspace", "dispatcher").status_code == 404
    with flow.factory() as session:
        job = session.scalar(select(m.Job).where(m.Job.incident_id == incident_id))
        job_id = job.id
    assert flow.get("/jobs/" + job_id, "dispatcher").status_code == 404
    token = flow.post("/auth/task-token", {}).json()["data"]["task_token"]
    assert (
        flow.client.get(
            "/api/v1" + path,
            headers={"Authorization": "Bearer service-test", "X-Task-Token": token},
        ).status_code
        == 401
    )
    with flow.factory() as session, session.begin():
        session.get(m.Snapshot, plan["snapshot_id"]).valid_until = m.utcnow() - timedelta(seconds=1)
    expired = flow.get(path, "dispatcher").json()["data"]
    assert all(p["status"] == "STALE" for p in expired["plans"])
    assert all(a["analysis_status"] == "STALE" for a in expired["analyses"])
    with flow.factory() as session:
        assert (
            expired["incident"]["status"]
            == session.get(m.Incident, incident_id).status
            == "MANUAL_REVIEW"
        )
    assert expired["incident"]["next_action"] == "MANUAL_REVIEW"
    with flow.factory() as session, session.begin():
        session.get(m.Incident, incident_id).tenant_id = "OTHER"
    assert flow.get(path, "dispatcher").status_code == 404


def test_browser_workspace_projects_incident_after_approved_lease_expiry(flow):
    from datetime import timedelta

    from sqlalchemy import select

    from app import models as m

    plan = flow.generate()
    assert flow.approve(plan).status_code == 200
    with flow.factory() as session, session.begin():
        for reservation in session.scalars(
            select(m.Reservation).where(m.Reservation.plan_id == plan["plan_id"])
        ):
            reservation.expires_at = m.utcnow() - timedelta(seconds=1)
    response = flow.get(f"/incidents/{plan['incident_id']}/workspace", "dispatcher")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    projected_plan = next(p for p in data["plans"] if p["plan_id"] == plan["plan_id"])
    assert projected_plan["status"] == "STALE"
    with flow.factory() as session:
        assert (
            data["incident"]["status"]
            == session.get(m.Incident, plan["incident_id"]).status
            == "MANUAL_REVIEW"
        )
        assert (
            session.scalar(select(m.Reservation).where(m.Reservation.plan_id == plan["plan_id"]))
            is None
        )
    assert data["incident"]["next_action"] == "MANUAL_REVIEW"


def test_driver_cannot_read_or_change_another_vehicle(flow):
    assert flow.get("/delivery-context?order_no=O004").status_code == 404
    incident = flow.incident()
    assert flow.get("/incidents/" + incident["incident_id"], "driver02").status_code == 404
    assert (
        flow.post(
            "/incidents",
            {
                "mode": "CREATE",
                "event_type": "ROAD_DELAY",
                "vehicle_id": "V02",
                "description": "x",
                "delay_minutes": 20,
            },
        ).status_code
        == 404
    )


def test_tool_requires_service_key_and_task_context_independently(flow):
    browser = flow.headers()
    assert (
        flow.client.get("/api/v1/agent-tools/delivery-context", headers=browser).status_code == 401
    )
    token = flow.post("/auth/task-token", {}).json()["data"]["task_token"]
    headers = {"Authorization": "Bearer service-test", "X-Task-Token": token}
    response = flow.client.get("/api/v1/agent-tools/delivery-context", headers=headers)
    assert response.status_code == 200, response.text
    assert (
        flow.client.get(
            "/api/v1/agent-tools/delivery-context?order_no=O004", headers=headers
        ).status_code
        == 404
    )
    create = flow.client.post(
        "/api/v1/agent-tools/incidents",
        headers={**headers, "Idempotency-Key": "tool-create"},
        json={
            "mode": "CREATE",
            "event_type": "ROAD_DELAY",
            "vehicle_id": "V01",
            "description": "SIMULATION",
            "delay_minutes": 20,
        },
    )
    assert create.status_code == 201, create.text
    incident_id = create.json()["data"]["incident_id"]
    status = flow.client.get(
        "/api/v1/agent-tools/status",
        headers=headers,
        params={"object_type": "INCIDENT", "object_id": incident_id},
    )
    assert status.status_code == 200, status.text
    assert status.json()["data"]["result"]["incident_id"] == incident_id


def test_chat_job_is_browser_only(flow):
    with flow.factory() as session:
        session.add(
            Job(
                id="CHAT1",
                tenant_id="DEMO",
                user_id="DRIVER01",
                operation_id="OP1",
                job_type="CHAT",
                input={},
                status="QUEUED",
            )
        )
        session.commit()
    assert flow.get("/jobs/CHAT1").status_code == 200
    token = flow.post("/auth/task-token", {}).json()["data"]["task_token"]
    response = flow.client.get(
        "/api/v1/agent-tools/status",
        params={"object_type": "JOB", "object_id": "CHAT1"},
        headers={"Authorization": "Bearer service-test", "X-Task-Token": token},
    )
    assert response.status_code == 404


@pytest.mark.parametrize(
    "path",
    ["/delivery-context?role=DISPATCHER", "/status?object_type=JOB&object_id=J1&tenant_id=OTHER"],
)
def test_unknown_query_fields_rejected(flow, path):
    assert flow.get(path).status_code == 422


def test_expired_task_token_and_forged_role_do_not_expand_access(flow):
    from datetime import UTC, datetime, timedelta

    from app.security import create_task_token

    token = create_task_token(
        task_id="T1",
        user_id="DRIVER01",
        tenant_id="DEMO",
        scopes=["get_delivery_context"],
        object_ids=["V01"],
        key="t" * 40,
        now=datetime.now(UTC) - timedelta(seconds=301),
    )
    response = flow.client.get(
        "/api/v1/agent-tools/delivery-context",
        headers={"Authorization": "Bearer service-test", "X-Task-Token": token},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "TASK_CONTEXT_EXPIRED"
    response = flow.post("/auth/task-token", {"role": "DISPATCHER", "vehicle_id": "V02"})
    assert response.status_code == 422


def test_task_derived_jobs_and_plans_accessible_with_original_roots(flow):
    token = flow.post("/auth/task-token", {}).json()["data"]["task_token"]
    headers = {"Authorization": "Bearer service-test", "X-Task-Token": token}
    plan = flow.generate()
    for kind, object_id in [("PLAN", plan["plan_id"]), ("ANALYSIS", plan["analysis_id"])]:
        response = flow.client.get(
            "/api/v1/agent-tools/status",
            headers=headers,
            params={"object_type": kind, "object_id": object_id},
        )
        assert response.status_code == 200, response.text


def test_driver_without_current_tasks_can_get_scoped_token_and_empty_context(flow):
    from app.models import User, Vehicle

    with flow.factory() as session:
        session.get(User, "DRIVER01").vehicle_id = None
        session.get(Vehicle, "V01").driver_id = None
        session.commit()
    response = flow.post("/auth/task-token", {})
    assert response.status_code == 200, response.text
    token = response.json()["data"]["task_token"]
    assert "*" not in response.json()["data"]["object_ids"]
    context = flow.client.get(
        "/api/v1/agent-tools/delivery-context",
        headers={"Authorization": "Bearer service-test", "X-Task-Token": token},
    )
    assert context.status_code == 200
    assert context.json()["data"]["vehicles"] == []
    assert context.json()["data"]["orders"] == []
    assert context.json()["data"]["route_stops"] == []
