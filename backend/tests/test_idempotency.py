def test_same_key_replays_original_and_conflicting_payload_rejected(flow):
    body = {
        "mode": "CREATE",
        "event_type": "ROAD_DELAY",
        "vehicle_id": "V01",
        "description": "SIMULATION",
        "delay_minutes": 20,
    }
    first = flow.post("/incidents", body, key="stable").json()
    replay = flow.post("/incidents", dict(reversed(list(body.items()))), key="stable").json()
    assert replay["data"] == first["data"]
    assert replay["operation_id"] == first["operation_id"]
    assert replay["meta"]["replayed"] is True
    assert flow.post("/incidents", {**body, "delay_minutes": 40}, key="stable").status_code == 409


def test_long_request_key_is_queryable_and_headers_bounded(flow):
    key = "request-" + "a" * 200
    body = {
        "mode": "CREATE",
        "event_type": "ROAD_DELAY",
        "vehicle_id": "V01",
        "description": "SIMULATION",
        "delay_minutes": 20,
    }
    first = flow.post("/incidents", body, key=key)
    assert first.status_code == 201
    status = flow.get("/status", params={"object_type": "REQUEST", "object_id": key})
    assert status.status_code == 200, status.text
    assert status.json()["data"]["result"]["operation_id"] == first.json()["operation_id"]
    assert flow.post("/incidents", body, key="a" * 257).status_code == 422


def test_same_second_logins_have_distinct_tokens(flow):
    body = {"username": "driver01", "password": "driver-test"}
    first = flow.client.post("/api/v1/auth/login", json=body)
    second = flow.client.post("/api/v1/auth/login", json=body)
    assert first.status_code == second.status_code == 200
    assert first.json()["data"]["access_token"] != second.json()["data"]["access_token"]


def test_postgresql_concurrent_same_key_is_one_atomic_result(flow):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    import pytest

    if flow.factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("PostgreSQL row-lock integration test; set TEST_DATABASE_URL")
    flow.headers()
    barrier = Barrier(2)
    body = {
        "mode": "CREATE",
        "event_type": "ROAD_DELAY",
        "vehicle_id": "V01",
        "description": "SIMULATION concurrent",
        "delay_minutes": 20,
    }

    def submit():
        barrier.wait(timeout=5)
        return flow.post("/incidents", body, key="concurrent-key")

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(submit) for _ in range(2)]
        responses = [future.result(timeout=15) for future in futures]
    assert [response.status_code for response in responses] == [201, 201]
    assert responses[0].json()["data"] == responses[1].json()["data"]
    assert {response.json()["meta"]["replayed"] for response in responses} == {True, False}


def test_expired_key_is_not_automatically_reexecuted_or_replayed(flow):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.models import Incident, Operation

    body = {
        "mode": "CREATE",
        "event_type": "ROAD_DELAY",
        "vehicle_id": "V01",
        "description": "SIMULATION",
        "delay_minutes": 20,
    }
    assert flow.post("/incidents", body, key="expired-key").status_code == 201
    with flow.factory() as session:
        session.scalar(select(Operation)).expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    response = flow.post("/incidents", body, key="expired-key")
    assert response.status_code == 409
    assert response.json()["code"] == "IDEMPOTENCY_CONFLICT"
    with flow.factory() as session:
        assert len(session.scalars(select(Incident)).all()) == 1


def test_audit_operation_keeps_http_request_identity(flow):
    from sqlalchemy import select

    from app.models import AuditEvent

    body = {
        "mode": "CREATE",
        "event_type": "ROAD_DELAY",
        "vehicle_id": "V01",
        "description": "SIMULATION",
        "delay_minutes": 20,
    }
    headers = {**flow.headers(), "X-Request-ID": "REQUEST_TRACE_1"}
    response = flow.client.post("/api/v1/incidents", json=body, headers=headers)
    assert response.status_code == 201
    with flow.factory() as session:
        audit = session.scalar(
            select(AuditEvent).where(
                AuditEvent.entity_type == "OPERATION",
                AuditEvent.operation_id == response.json()["operation_id"],
            )
        )
        assert audit.request_id == "REQUEST_TRACE_1"


def test_tool_recovers_failed_human_operation_with_current_root_authorization(flow):
    from datetime import UTC, datetime, timedelta

    from app.models import Plan

    plan = flow.generate()
    with flow.factory() as session:
        session.get(Plan, plan["plan_id"]).expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    body = {
        "expected_plan_version": plan["plan_version"],
        "expected_incident_version": plan["incident_version"],
        "snapshot_id": plan["snapshot_id"],
    }
    path = f"/plans/{plan['plan_id']}/approve"
    failed = flow.post(path, body, "dispatcher", key="failed-approval")
    assert failed.status_code == 409
    assert failed.json()["code"] == "STALE_PLAN"
    operation_id = failed.json()["operation_id"]
    token = flow.post("/auth/task-token", {"vehicle_id": "V01"}, "dispatcher").json()["data"][
        "task_token"
    ]
    wrong_root_token = flow.post("/auth/task-token", {"vehicle_id": "V02"}, "dispatcher").json()[
        "data"
    ]["task_token"]
    other_user_token = flow.post("/auth/task-token", {}, "driver01").json()["data"]["task_token"]
    for object_type, object_id in [("REQUEST", "failed-approval"), ("OPERATION", operation_id)]:
        params = {"object_type": object_type, "object_id": object_id}
        browser = flow.get("/status", "dispatcher", params=params)
        assert browser.status_code == 200
        assert browser.json()["data"]["result"]["operation_status"] == "FAILED"
        for credential, expected in [
            (token, 200),
            (wrong_root_token, 404),
            (other_user_token, 404),
        ]:
            response = flow.client.get(
                "/api/v1/agent-tools/status",
                params=params,
                headers={"Authorization": "Bearer service-test", "X-Task-Token": credential},
            )
            assert response.status_code == expected, response.text
            if expected == 200:
                result = response.json()["data"]["result"]
                assert result["operation_status"] == "FAILED"
                assert result["resource_type"] == "PLAN"
                assert result["resource_id"] == plan["plan_id"]
    replay = flow.post(path, body, "dispatcher", key="failed-approval")
    assert replay.status_code == 409
    assert replay.json()["operation_id"] == operation_id
    assert replay.json()["meta"]["replayed"] is True
