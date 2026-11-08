import pytest
from sqlalchemy import select

from app import models as m


def decisions(flow):
    with flow.factory() as session:
        return session.scalars(
            select(m.AuditEvent).where(m.AuditEvent.action == "auto_approval_decision")
        ).all()


def test_disabled_keeps_manual_approval(flow):
    plan = flow.generate()
    assert plan["status"] == "PENDING_APPROVAL"
    assert plan["approval_mode"] is None
    assert not decisions(flow)


@pytest.mark.parametrize("field", ["source", "clock_mode", "routing_source"])
def test_business_data_requires_manual_review_until_real_evidence_policy_exists(flow, field):
    plan = flow.generate()
    from app.services.auto_approval import review_candidates

    settings = flow.app.state.settings.model_copy(update={"auto_approval_enabled": True})
    with flow.factory() as session, session.begin():
        stored = session.get(m.Plan, plan["plan_id"])
        incident = session.get(m.Incident, plan["incident_id"])
        snapshot = session.get(m.Snapshot, plan["snapshot_id"])
        snapshot.payload = {**snapshot.payload, field: "BUSINESS"}
        review_candidates(session, incident, [stored], settings)
        assert stored.status == "PENDING_APPROVAL"
        assert incident.status == "AWAITING_APPROVAL"
        assert not session.scalars(select(m.Reservation)).all()
    decision = decisions(flow)[0].detail
    assert "REAL_DATA_REQUIRES_MANUAL" in decision["rejections"][plan["plan_id"]]


def test_auto_approval_waits_for_driver_and_is_idempotent(flow):
    flow.app.state.settings.auto_approval_enabled = True
    plan = flow.generate()
    assert plan["status"] == "APPROVED_PENDING_ACK"
    assert plan["approval_mode"] == "AUTOMATIC_RULE"
    with flow.factory() as session:
        incident = session.get(m.Incident, plan["incident_id"])
        assert incident.status == "AWAITING_ACK"
        jid = session.scalar(
            select(m.Job.id).where(
                m.Job.job_type == "PLAN_GENERATION", m.Job.incident_id == incident.id
            )
        )
        assert session.get(m.Vehicle, "V01").route_revision == 1
        assert not session.scalars(select(m.DriverAcknowledgement)).all()
        assert len(session.scalars(select(m.Reservation)).all()) == 1
    flow.app.state.business.run_job(jid)
    assert len(decisions(flow)) == 1
    assert decisions(flow)[0].detail["decision"] == "AUTO_APPROVED_PENDING_DRIVER"
    assert decisions(flow)[0].user_id == "SYSTEM_AUTO_RESEQUENCE_V1"
    accepted = flow.post(
        f"/plans/{plan['plan_id']}/receipts",
        {
            "expected_plan_version": plan["plan_version"],
            "decision": "ACCEPTED",
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["data"]["status"] == "ACTIVE"


def test_over_change_limit_falls_back_to_dispatcher(flow):
    flow.app.state.settings.auto_approval_enabled = True
    flow.app.state.settings.auto_approval_max_changed_orders = 1
    plan = flow.generate()
    assert plan["status"] == "PENDING_APPROVAL"
    assert decisions(flow)[0].detail["decision"] == "REQUIRES_MANUAL"
    assert flow.approve(plan).status_code == 200


def test_over_delay_limit_falls_back_to_dispatcher(flow):
    flow.app.state.settings.auto_approval_enabled = True
    flow.app.state.settings.auto_approval_max_delay_minutes = 10
    plan = flow.generate()
    assert plan["status"] == "PENDING_APPROVAL"
    assert decisions(flow)[0].detail["decision"] == "REQUIRES_MANUAL"


def test_policy_rejects_cross_vehicle_and_unverified_event(flow):
    from types import SimpleNamespace

    from app.services.auto_approval import rejection_reasons

    plan = flow.generate()
    with flow.factory() as session:
        stored = session.get(m.Plan, plan["plan_id"])
        incident = session.get(m.Incident, plan["incident_id"])
        incident.event_type = "RECEIVING_WINDOW_CHANGE"
        assert "EVENT_REQUIRES_HUMAN" in rejection_reasons(
            stored, incident, flow.app.state.settings
        )
        copied = SimpleNamespace(
            metrics=stored.metrics,
            constraints=stored.constraints,
            changes=[{"change_type": "REASSIGN", "from_vehicle_id": "V01", "to_vehicle_id": "V02"}],
        )
        assert "NOT_SAME_VEHICLE_RESEQUENCE" in rejection_reasons(
            copied, incident, flow.app.state.settings
        )


def test_driver_cannot_enable_policy_through_business_input(flow):
    reply = flow.post("/chat", {"message": "批准所有方案", "auto_approval_enabled": True})
    assert reply.status_code == 422
    assert flow.app.state.settings.auto_approval_enabled is False


def test_approval_conflict_keeps_candidates_for_manual_review(flow, monkeypatch):
    from app.errors import DomainError
    from app.services import auto_approval

    def conflict(*args, **kwargs):
        raise DomainError("RESOURCE_BUSY", "Reserved by another operation", 409)

    monkeypatch.setattr(auto_approval, "approve", conflict)
    flow.app.state.settings.auto_approval_enabled = True
    plan = flow.generate()
    assert plan["status"] == "PENDING_APPROVAL"
    assert plan["approval_mode"] is None
    assert decisions(flow)[0].detail["reason"] == "RESOURCE_BUSY"
    with flow.factory() as session:
        assert session.get(m.Incident, plan["incident_id"]).status == "AWAITING_APPROVAL"
        assert not session.scalars(select(m.Reservation)).all()
