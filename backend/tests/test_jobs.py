from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app import models as m


def test_reverse_publication_keeps_newest_submitted_candidate_family(flow):
    first = flow.generate()
    body = {
        "incident_id": first["incident_id"],
        "expected_incident_version": 1,
        "snapshot_id": first["snapshot_id"],
        "analysis_id": first["analysis_id"],
    }
    old, current = [
        flow.post("/recovery-plans", body, "dispatcher").json()["data"]["job_id"] for _ in range(2)
    ]
    assert flow.app.state.business.run_job(current)["job_status"] == "SUCCEEDED"
    current_result = flow.get("/jobs/" + current, "dispatcher").json()["data"]["result"]
    assert flow.app.state.business.run_job(old)["job_status"] == "SUCCEEDED"
    old_result = flow.get("/jobs/" + old, "dispatcher").json()["data"]["result"]
    with flow.factory() as session, session.begin():
        for plan in session.scalars(
            select(m.Plan).where(m.Plan.plan_group_id == old_result["plan_group_id"])
        ):
            plan.expires_at = m.utcnow() - timedelta(seconds=1)
    response = flow.get(f"/incidents/{first['incident_id']}/workspace", "dispatcher")
    assert response.status_code == 200
    assert response.json()["data"]["incident"]["status"] == "AWAITING_APPROVAL"
    with flow.factory() as session:
        incident = session.get(m.Incident, first["incident_id"])
        assert incident.current_calculation_job_id == current
        assert incident.current_plan_group_id == current_result["plan_group_id"]


def test_current_job_failure_returns_manual_despite_older_pending_job_and_late_result(
    flow, monkeypatch
):
    from app.errors import DomainError
    from app.services import jobs

    snapshot, incident = flow.context(), flow.incident()
    body = {
        "incident_id": incident["incident_id"],
        "expected_incident_version": 1,
        "snapshot_id": snapshot["snapshot_id"],
    }
    old, current = [
        flow.post("/impact-analyses", body, "dispatcher").json()["data"]["job_id"] for _ in range(2)
    ]
    original = jobs.analyze_snapshot

    def failed(*args):
        raise DomainError("DATA_INCOMPLETE", "Calculation input unavailable", 422)

    monkeypatch.setattr(jobs, "analyze_snapshot", failed)
    assert flow.app.state.business.run_job(current)["job_status"] == "FAILED"
    with flow.factory() as session:
        assert session.get(m.Incident, incident["incident_id"]).status == "MANUAL_REVIEW"
    monkeypatch.setattr(jobs, "analyze_snapshot", original)
    assert flow.app.state.business.run_job(old)["job_status"] == "SUCCEEDED"
    with flow.factory() as session:
        stored = session.get(m.Incident, incident["incident_id"])
        assert stored.status == "MANUAL_REVIEW"
        assert stored.coordination_version == 1 and stored.version == 1
        assert stored.current_calculation_job_id == current
        assert stored.current_plan_group_id is None


@pytest.mark.parametrize("current_finished", [False, True], ids=["queued", "published"])
def test_old_failure_cannot_change_new_owner_state(flow, monkeypatch, current_finished):
    from app.errors import DomainError
    from app.services import jobs

    first = flow.generate()
    body = {
        "incident_id": first["incident_id"],
        "expected_incident_version": 1,
        "snapshot_id": first["snapshot_id"],
        "analysis_id": first["analysis_id"],
    }
    old, current = [
        flow.post("/recovery-plans", body, "dispatcher").json()["data"]["job_id"] for _ in range(2)
    ]
    if current_finished:
        assert flow.app.state.business.run_job(current)["job_status"] == "SUCCEEDED"
    with flow.factory() as session:
        group_id = session.get(m.Incident, first["incident_id"]).current_plan_group_id

    def failed(*args):
        raise DomainError("DATA_INCOMPLETE", "Obsolete computation failed", 422)

    monkeypatch.setattr(jobs, "generate_candidates", failed)
    assert flow.app.state.business.run_job(old)["job_status"] == "FAILED"
    with flow.factory() as session:
        incident = session.get(m.Incident, first["incident_id"])
        assert incident.status == ("AWAITING_APPROVAL" if current_finished else "ANALYZING")
        assert incident.current_calculation_job_id == current
        assert incident.current_plan_group_id == group_id
        assert incident.version == 1 and incident.coordination_version == 1


def test_current_family_read_expiry_does_not_fence_same_snapshot_computation(flow):
    first = flow.generate()
    body = {
        "incident_id": first["incident_id"],
        "expected_incident_version": 1,
        "snapshot_id": first["snapshot_id"],
        "analysis_id": first["analysis_id"],
    }
    submitted = flow.post("/recovery-plans", body, "dispatcher")
    assert submitted.status_code == 202
    job_id = submitted.json()["data"]["job_id"]
    with flow.factory() as session, session.begin():
        session.get(m.Plan, first["plan_id"]).expires_at = m.utcnow() - timedelta(seconds=1)
    response = flow.get(f"/incidents/{first['incident_id']}/workspace", "dispatcher")
    assert response.status_code == 200
    assert response.json()["data"]["incident"]["status"] == "ANALYZING"
    with flow.factory() as session:
        assert session.get(m.Incident, first["incident_id"]).coordination_version == 1
    assert flow.app.state.business.run_job(job_id)["job_status"] == "SUCCEEDED"


def queued(flow):
    context, incident = flow.context(), flow.incident()
    return flow.post(
        "/impact-analyses",
        {
            "snapshot_id": context["snapshot_id"],
            "incident_id": incident["incident_id"],
            "expected_incident_version": 1,
        },
    ).json()["data"]["job_id"]


def test_outbox_broker_failure_retry_and_duplicate_delivery(flow):
    from app.maintenance import dispatch_outbox

    job_id = queued(flow)

    def broken(*args):
        raise ConnectionError("broker unavailable")

    assert dispatch_outbox(flow.factory, broken)["failed"] == 1
    with flow.factory() as session:
        assert session.get(m.Job, job_id).status == "QUEUED"
        event = session.scalar(select(m.OutboxEvent))
        event.available_at = m.utcnow() - timedelta(seconds=1)
        session.commit()
    delivered = []
    dispatch_outbox(flow.factory, lambda kind, identifier: delivered.append((kind, identifier)))
    assert delivered == [("IMPACT_ANALYSIS", job_id)]
    flow.app.state.business.run_job(job_id)
    flow.app.state.business.run_job(job_id)
    with flow.factory() as session:
        assert session.get(m.Job, job_id).status == "SUCCEEDED"
        assert session.scalar(select(func.count()).select_from(m.Analysis)) == 1


def test_c18_worker_restart_reclaims_and_fences_old_attempt(flow):
    from app.maintenance import recover_jobs

    job_id = queued(flow)
    with flow.factory() as session, session.begin():
        job = session.get(m.Job, job_id)
        original_input = dict(job.input)
        job.status, job.lease_owner, job.attempts = "RUNNING", "dead-worker", 1
        job.lease_expires_at = m.utcnow() - timedelta(seconds=1)
    assert job_id in recover_jobs(flow.factory)
    with flow.factory() as session:
        job = session.get(m.Job, job_id)
        assert job.status == "QUEUED" and job.attempts == 2 and job.lease_owner is None
        assert job.input == original_input
    flow.app.state.business.run_job(job_id)
    assert flow.get("/jobs/" + job_id).json()["data"]["job_status"] == "SUCCEEDED"


def test_c18_notification_redelivery_does_not_reinsert(flow):
    from app.maintenance import dispatch_outbox

    plan = flow.generate()
    approval = flow.approve(plan).json()["data"]
    flow.post(
        "/plans/" + plan["plan_id"] + "/receipts",
        {
            "expected_plan_version": approval["plan_version"],
            "decision": "ACCEPTED",
        },
    )
    with flow.factory() as session:
        before = session.scalar(select(func.count()).select_from(m.Notification))
    dispatch_outbox(flow.factory, lambda *args: None)
    with flow.factory() as session, session.begin():
        for event in session.scalars(
            select(m.OutboxEvent).where(m.OutboxEvent.event_type == "NOTIFICATION_CREATED")
        ):
            event.status = "PENDING"
    dispatch_outbox(flow.factory, lambda *args: None)
    with flow.factory() as session:
        assert before > 0
        assert session.scalar(select(func.count()).select_from(m.Notification)) == before


def test_maintenance_expires_reservations_without_read(flow):
    from app.maintenance import maintenance_once

    plan = flow.generate()
    flow.approve(plan)
    with flow.factory() as session, session.begin():
        for row in session.scalars(select(m.Reservation)):
            row.expires_at = m.utcnow() - timedelta(seconds=1)
    maintenance_once(flow.factory, lambda *args: None)
    with flow.factory() as session:
        assert session.get(m.Plan, plan["plan_id"]).status == "STALE"
        assert session.scalar(select(func.count()).select_from(m.Reservation)) == 0
