import pytest

from app.platform import result
from app.services.chat import run_chat


def test_deepseek_continuation_gets_authorized_completed_analysis(flow):
    settings = flow.app.state.settings.model_copy(update={"agent_mode": "deepseek"})

    class Start:
        def invoke(self, inp, gateway):
            ctx = gateway.call("get_delivery_context", {"order_no": "O001"})
            inc = gateway.call(
                "upsert_incident",
                {
                    "mode": "CREATE",
                    "event_type": "ROAD_DELAY",
                    "vehicle_id": "V01",
                    "description": "traffic",
                    "delay_minutes": 20,
                },
            )
            job = gateway.call(
                "analyze_impact",
                {
                    "incident_id": inc["incident_id"],
                    "expected_incident_version": inc["incident_version"],
                    "snapshot_id": ctx["snapshot_id"],
                },
            )
            return result(incident_id=inc["incident_id"], job_ids=[job["job_id"]])

    first = flow.post("/chat", {"message": "堵车20分钟", "selected_order_no": "O001"}).json()[
        "data"
    ]
    run_chat(first["job_id"], session_factory=flow.factory, settings=settings, platform=Start())
    jid = flow.get("/jobs/" + first["job_id"]).json()["data"]["result"]["job_ids"][0]
    flow.app.state.business.run_job(jid)
    seen = []

    class Continue:
        def invoke(self, inp, gateway):
            seen.extend(inp.business_context["recent_jobs"])
            return result(incident_id=inp.business_context["incident_id"])

    second = flow.post(
        "/chat", {"message": "继续", "conversation_id": first["conversation_id"]}
    ).json()["data"]
    run_chat(second["job_id"], session_factory=flow.factory, settings=settings, platform=Continue())
    assert len(seen) == 1
    assert seen[0]["job_id"] == jid
    assert seen[0]["result"]["analysis_status"] == "VALID"
    assert seen[0]["result"]["analysis_id"]
    assert "task_token" not in str(seen)


def test_premature_receipt_does_not_expire_unapproved_candidate(flow):
    plan = flow.generate()
    rejected = flow.post(
        f"/plans/{plan['plan_id']}/receipts",
        {
            "expected_plan_version": plan["plan_version"],
            "decision": "ACCEPTED",
        },
    )
    assert rejected.status_code == 409
    current = flow.get(f"/plans/{plan['plan_id']}").json()["data"]
    assert current["status"] == "PENDING_APPROVAL"
    assert flow.approve(plan).status_code == 200


def test_chat_state_follows_new_computation_and_rejects_expired_analysis(flow):
    from datetime import timedelta

    from sqlalchemy import select

    from app import models as m
    from app.services.chat import reconcile
    from app.services.common import Actor

    context = flow.context()
    incident = flow.incident()
    queued = flow.post(
        "/impact-analyses",
        {
            "incident_id": incident["incident_id"],
            "expected_incident_version": incident["incident_version"],
            "snapshot_id": context["snapshot_id"],
        },
    ).json()["data"]
    flow.app.state.business.run_job(queued["job_id"])
    with flow.factory() as session:
        user = session.scalar(select(m.User).where(m.User.username == "driver01"))
        actor = Actor(user.id, user.tenant_id, user.role, user.vehicle_id)
    original = result(incident_id=incident["incident_id"])
    checked = reconcile(original, actor, flow.factory)
    assert checked.next_action == "GENERATE_PLANS"
    assert queued["job_id"] in checked.job_ids
    with flow.factory() as session, session.begin():
        session.get(m.Snapshot, context["snapshot_id"]).valid_until = m.utcnow() - timedelta(
            seconds=1
        )
    assert reconcile(original, actor, flow.factory).next_action == "ANALYZE_IMPACT"


@pytest.mark.parametrize("old_status", ["FAILED", "QUEUED"])
def test_current_analysis_supersedes_old_job_state(flow, old_status):
    from sqlalchemy import select

    from app import models as m
    from app.services.chat import reconcile
    from app.services.common import Actor

    context, incident = flow.context(), flow.incident()
    body = {
        "incident_id": incident["incident_id"],
        "expected_incident_version": incident["incident_version"],
        "snapshot_id": context["snapshot_id"],
    }
    old, current = [
        flow.post("/impact-analyses", body).json()["data"]["job_id"] for _ in range(2)
    ]
    with flow.factory() as session, session.begin():
        session.get(m.Job, old).status = old_status
        user = session.scalar(select(m.User).where(m.User.username == "driver01"))
        actor = Actor(user.id, user.tenant_id, user.role, user.vehicle_id)
    flow.app.state.business.run_job(current)
    checked = reconcile(
        result(incident_id=incident["incident_id"], job_ids=[old]), actor, flow.factory
    )
    assert checked.next_action == "GENERATE_PLANS"
    assert current in checked.job_ids
