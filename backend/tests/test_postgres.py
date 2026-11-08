"""Maintained PostgreSQL concurrency and optional actual Redis/Celery acceptance."""

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import select

from app import models as m


def pg_only(flow):
    if flow.factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Requires TEST_DATABASE_URL PostgreSQL")


def test_postgres_upgrade_0001_preserves_seed_and_checks_frozen_head(flow, monkeypatch):
    pg_only(flow)
    from alembic.config import Config
    from sqlalchemy import func
    from sqlalchemy.schema import CreateSchema, DropSchema

    from alembic import command
    from app.db import make_engine, make_session_factory
    from tests.test_deployment import (
        assert_legacy_workflows_preserved,
        legacy_seed_records,
        legacy_workflow_records,
    )

    engine = make_engine(os.environ["TEST_DATABASE_URL"])
    schema = "migration_" + uuid4().hex
    with engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    isolated = make_engine(
        engine.url.update_query_dict({"options": f"-csearch_path={schema}"}).render_as_string(
            hide_password=False
        )
    )
    try:
        monkeypatch.setenv("DATABASE_URL", isolated.url.render_as_string(hide_password=False))
        config = Config("alembic.ini")
        command.upgrade(config, "0001")
        factory = make_session_factory(isolated)
        legacy_seed_records(isolated)
        command.upgrade(config, "0002")
        legacy_workflow_records(isolated)
        command.upgrade(config, "head")
        command.check(config)
        assert_legacy_workflows_preserved(isolated)
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(m.Vehicle)) == 5
            assert session.scalar(select(func.count()).select_from(m.Order)) == 20
            assert session.scalar(select(func.count()).select_from(m.OperationAlias)) == 0
            assert all(
                n.longitude is None and n.latitude is None and n.coordinate_source is None
                for n in session.scalars(select(m.Node))
            )
            graph = session.scalar(select(m.RoutingGraph))
            assert graph.matrix == graph.simulation_matrix
            assert graph.routing_version == graph.simulation_routing_version == "R1"
    finally:
        isolated.dispose()
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        engine.dispose()


@pytest.mark.parametrize(
    "same_key,changed_body", [(True, False), (False, False), (True, True), (False, True)]
)
def test_postgres_chat_create_race_has_one_receipt_and_operation(flow, same_key, changed_body):
    pg_only(flow)
    from sqlalchemy import func

    from app.platform import result
    from app.services.chat import run_chat

    observed = []

    class Platform:
        def invoke(self, run_input, gateway):
            token = run_input.trusted_task_context["task_token"].get_secret_value()

            def submit(index):
                return flow.client.post(
                    "/api/v1/agent-tools/incidents",
                    json={
                        "mode": "CREATE",
                        "event_type": "ROAD_DELAY",
                        "vehicle_id": "V01",
                        "description": "traffic" + (str(index) if changed_body else ""),
                        "delay_minutes": 20,
                    },
                    headers={
                        "Authorization": "Bearer service-test",
                        "X-Task-Token": token,
                        "Idempotency-Key": "race" if same_key else f"race-{index}",
                    },
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                observed.extend(executor.map(submit, range(2)))
            return result()

    data = flow.post("/chat", {"message": "堵车20分钟", "selected_order_no": "O001"}).json()["data"]
    run_chat(
        data["job_id"],
        session_factory=flow.factory,
        settings=flow.app.state.settings,
        platform=Platform(),
    )
    assert sorted(r.status_code for r in observed) == ([201, 409] if changed_body else [201, 201])
    with flow.factory() as session:
        assert session.scalar(select(func.count()).select_from(m.Incident)) == 1
        operations = session.scalars(
            select(m.Operation).where(m.Operation.action == "upsert_incident")
        ).all()
        assert len(operations) == 1
        turn = session.get(m.ChatTurn, data["turn_id"])
        assert turn.tool_state["incident:create"]["operation_id"] == operations[0].id
        assert turn.tool_call_count == 2


def test_postgres_simultaneous_chat_is_one_active_turn(flow):
    pg_only(flow)
    from app.services.chat import run_chat

    data = flow.post("/chat", {"message": "查询", "selected_order_no": "O001"}).json()["data"]
    run_chat(data["job_id"], session_factory=flow.factory, settings=flow.app.state.settings)
    headers = flow.headers()

    def submit(index):
        return flow.client.post(
            "/api/v1/chat",
            json={"message": "继续", "conversation_id": data["conversation_id"]},
            headers={**headers, "Idempotency-Key": f"chat-race-{index}"},
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        codes = list(executor.map(lambda index: submit(index).status_code, range(2)))
    assert sorted(codes) == [202, 409]


def test_postgres_outbox_concurrent_dispatch_is_once(flow):
    pg_only(flow)
    from app.maintenance import dispatch_outbox

    data = flow.post("/chat", {"message": "查询", "selected_order_no": "O001"}).json()["data"]
    published = []

    def dispatch(_):
        return dispatch_outbox(flow.factory, lambda kind, jid: published.append(jid))

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(dispatch, range(2)))
    assert published == [data["job_id"]]


@pytest.mark.parametrize("auto_approval_enabled", [False, True])
def test_actual_redis_celery_three_turn_pipeline(flow, auto_approval_enabled):
    pg_only(flow)
    redis_url = os.environ.get("TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("Requires isolated TEST_REDIS_URL")
    from celery import Celery

    from app.maintenance import maintenance_once

    flow.app.state.settings.auto_approval_enabled = auto_approval_enabled
    bound = flow.factory.kw["bind"]
    schema = bound.get_execution_options()["schema_translate_map"][None]
    database_url = bound.url.update_query_dict(
        {"options": f"-csearch_path={schema}"}
    ).render_as_string(hide_password=False)
    env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "REDIS_URL": redis_url,
        "LOGIN_SIGNING_KEY": "l" * 40,
        "TASK_TOKEN_SIGNING_KEY": "t" * 40,
        "TOOL_SERVICE_KEY": "service-test",
        "AGENT_MODE": "local",
        "AUTO_APPROVAL_ENABLED": str(auto_approval_enabled).lower(),
    }
    suffix = uuid4().hex
    queues = {kind: f"test-{kind}-{suffix}" for kind in ("compute", "chat")}
    broker = Celery("runtime-test", broker=redis_url)
    processes = []
    try:
        for kind, queue_name in queues.items():
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "celery",
                        "-A",
                        "app.worker:celery",
                        "worker",
                        "--pool=solo",
                        "--concurrency=1",
                        "-Q",
                        queue_name,
                        "--hostname",
                        f"{kind}-{suffix}",
                        "--loglevel=WARNING",
                    ],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            )

        def publish(kind, jid):
            channel = "chat" if kind == "CHAT" else "compute"
            broker.send_task("fleet." + channel, args=[jid], queue=queues[channel])

        def wait_job(jid):
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline:
                assert all(p.poll() is None for p in processes), "Celery worker stopped"
                maintenance_once(flow.factory, publish)
                data = flow.get("/jobs/" + jid).json()["data"]
                if data["job_status"] in {"SUCCEEDED", "FAILED"}:
                    assert data["job_status"] == "SUCCEEDED", data["error"]
                    return data["result"]
                time.sleep(0.2)
            pytest.fail("Actual Redis/Celery job did not finish within 35 seconds")

        first = flow.post(
            "/chat", {"message": "预计延误20分钟", "selected_order_no": "O001"}
        ).json()["data"]
        result = wait_job(first["job_id"])
        assert result["business_state"] == "PROCESSING"
        wait_job(result["job_ids"][0])
        second = flow.post(
            "/chat", {"message": "继续", "conversation_id": first["conversation_id"]}
        ).json()["data"]
        result = wait_job(second["job_id"])
        generated = wait_job(result["job_ids"][-1])
        assert any(
            p["metrics"]["changed_order_count"] == 2 and p["metrics"]["total_delay_minutes"] == 0
            for p in generated["plans"]
        )
        third = flow.post(
            "/chat", {"message": "查看结果", "conversation_id": first["conversation_id"]}
        ).json()["data"]
        expected_state = "WAITING_ACK" if auto_approval_enabled else "WAITING_APPROVAL"
        assert wait_job(third["job_id"])["business_state"] == expected_state
        with flow.factory() as session:
            assert len(session.scalars(select(m.Incident)).all()) == 1
            assert session.get(m.Vehicle, "V01").route_revision == 1
        if auto_approval_enabled:
            approved = next(
                p for p in generated["plans"] if p["status"] == "APPROVED_PENDING_ACK"
            )
            assert approved["approval_mode"] == "AUTOMATIC_RULE"
            response = flow.post(
                f"/plans/{approved['plan_id']}/receipts",
                {"expected_plan_version": approved["plan_version"], "decision": "ACCEPTED"},
            )
            assert response.status_code == 200, response.text
            assert response.json()["data"]["status"] == "ACTIVE"
            assert flow.route_revision() == 2
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            process.wait(timeout=15)
        with broker.connection() as connection:
            for queue in queues.values():
                connection.default_channel.queue_delete(queue)
