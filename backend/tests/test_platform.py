import pytest


def test_reconcile_rejects_unauthorized_ids_and_unverified_claims(flow):
    from app.errors import DomainError
    from app.platform import RunResult
    from app.services.chat import reconcile
    from app.services.common import Actor

    with flow.factory() as session:
        user = session.scalar(
            __import__("sqlalchemy")
            .select(__import__("app.models", fromlist=["User"]).User)
            .where(__import__("app.models", fromlist=["User"]).User.username == "driver01")
        )
    actor = Actor(user.id, user.tenant_id, user.role, user.vehicle_id)
    raw = RunResult(
        reply="全部送达 ACTIVE DONE 已经完成",
        business_state="DONE",
        incident_id=None,
        job_ids=[],
        plan_ids=[],
        next_action="NONE",
        evidence_refs=[],
    )
    checked = reconcile(raw, actor, flow.factory)
    assert checked.business_state == "NEEDS_INFO"
    assert "ACTIVE" not in checked.reply and "已经完成" not in checked.reply
    with pytest.raises(DomainError):
        reconcile(raw.model_copy(update={"plan_ids": ["unknown"]}), actor, flow.factory)


def test_reconcile_tracks_actual_activation_and_completion_states(flow):
    from datetime import timedelta

    from sqlalchemy import select

    from app import models as m
    from app.platform import result
    from app.services.chat import reconcile
    from app.services.common import Actor

    plan = flow.generate()
    with flow.factory() as session:
        user = session.scalar(select(m.User).where(m.User.username == "driver01"))
    actor = Actor(user.id, user.tenant_id, user.role, user.vehicle_id)
    raw = result(incident_id=plan["incident_id"], plan_ids=[plan["plan_id"]])
    assert reconcile(raw, actor, flow.factory).business_state == "WAITING_APPROVAL"
    approval = flow.approve(plan).json()["data"]
    with flow.factory() as session, session.begin():
        alternative = session.scalar(
            select(m.Plan).where(
                m.Plan.incident_id == plan["incident_id"], m.Plan.id != plan["plan_id"]
            )
        )
        alternative.expires_at = m.utcnow() - timedelta(seconds=1)
        raw = result(incident_id=plan["incident_id"], plan_ids=[alternative.id, plan["plan_id"]])
    assert reconcile(raw, actor, flow.factory).business_state == "WAITING_ACK"
    flow.post(
        "/plans/" + plan["plan_id"] + "/receipts",
        {"expected_plan_version": approval["plan_version"], "decision": "ACCEPTED"},
    )
    assert reconcile(raw, actor, flow.factory).business_state == "ACTIVE"


def test_protected_tool_limit_and_replay_survive_restart(flow):
    from app.errors import DomainError
    from app.platform import result
    from app.services.chat import run_chat

    class BoundedPlatform:
        def invoke(self, run_input, gateway):
            payload = {
                "mode": "CREATE",
                "event_type": "ROAD_DELAY",
                "vehicle_id": "V01",
                "description": "same traffic",
                "delay_minutes": 20,
            }
            first = gateway.call("upsert_incident", payload)
            second = gateway.call("upsert_incident", payload)
            assert first == second
            for _ in range(6):
                gateway.call(
                    "get_operation_status",
                    {"object_type": "INCIDENT", "object_id": first["incident_id"]},
                )
            with pytest.raises(DomainError, match="Eight tool"):
                gateway.call("upsert_incident", payload)
            return result(incident_id=first["incident_id"])

    data = flow.post("/chat", {"message": "测试", "selected_order_no": "O001"}).json()["data"]
    run_chat(
        data["job_id"],
        session_factory=flow.factory,
        settings=flow.app.state.settings,
        platform=BoundedPlatform(),
    )
    assert flow.get("/jobs/" + data["job_id"]).json()["data"]["job_status"] == "SUCCEEDED"
