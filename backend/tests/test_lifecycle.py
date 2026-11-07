from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import Plan, Reservation, RouteStop


def refresh_family(flow, previous):
    snapshot = flow.context()
    body = {
        "incident_id": previous["incident_id"],
        "expected_incident_version": previous["incident_version"],
        "snapshot_id": snapshot["snapshot_id"],
    }
    for endpoint in ("/impact-analyses", "/recovery-plans"):
        response = flow.post(endpoint, body, "dispatcher")
        assert response.status_code == 202, response.text
        job_id = response.json()["data"]["job_id"]
        assert flow.app.state.business.run_job(job_id)["job_status"] == "SUCCEEDED"
        result = flow.get("/jobs/" + job_id, "dispatcher").json()["data"]["result"]
        if endpoint == "/impact-analyses":
            body["analysis_id"] = result["analysis_id"]
    return result["plans"][0]


def test_route_fact_invalidates_selected_approval_and_returns_incident_to_manual(flow):
    from app import models as m

    selected = flow.generate()
    approved = flow.approve(selected)
    assert approved.status_code == 200
    with flow.factory() as session:
        baseline = session.get(m.Incident, selected["incident_id"]).coordination_version
    arrived = flow.post(
        "/stops/STOP001/completion",
        {
            "event_type": "ARRIVED",
            "expected_route_revision": 1,
            "occurred_at": "2026-10-26T10:00:00+08:00",
        },
    )
    assert arrived.status_code == 200, arrived.text
    with flow.factory() as session:
        incident = session.get(m.Incident, selected["incident_id"])
        assert incident.status == "MANUAL_REVIEW"
        assert incident.version == 1 and incident.coordination_version == baseline + 1
        assert session.get(Plan, selected["plan_id"]).status == "STALE"
        assert session.scalar(select(Reservation)) is None
    accepted = flow.post(
        f"/plans/{selected['plan_id']}/receipts",
        {"decision": "ACCEPTED", "expected_plan_version": approved.json()["data"]["plan_version"]},
    )
    assert accepted.status_code == 409
    assert flow.route_revision() == 2


@pytest.mark.parametrize("read", ["workspace", "plan", "status"])
def test_historical_candidate_expiry_preserves_new_approval_and_valid_ack(flow, read):
    from app import models as m

    old = flow.generate()
    selected = refresh_family(flow, old)
    approved = flow.approve(selected)
    assert approved.status_code == 200, approved.text
    with flow.factory() as session, session.begin():
        baseline = session.get(m.Incident, old["incident_id"]).coordination_version
        for plan in session.scalars(select(Plan).where(Plan.snapshot_id == old["snapshot_id"])):
            plan.expires_at = m.utcnow() - timedelta(seconds=1)
    path = {
        "workspace": f"/incidents/{old['incident_id']}/workspace",
        "plan": f"/plans/{old['plan_id']}",
        "status": f"/status?object_type=PLAN&object_id={old['plan_id']}",
    }[read]
    response = flow.get(path, "dispatcher")
    assert response.status_code == 200, response.text
    if read == "workspace":
        assert response.json()["data"]["incident"]["status"] == "AWAITING_ACK"
    with flow.factory() as session:
        incident = session.get(m.Incident, old["incident_id"])
        assert incident.status == "AWAITING_ACK"
        assert incident.version == 1 and incident.coordination_version == baseline
        assert session.get(Plan, old["plan_id"]).status == "STALE"
        assert session.get(Plan, selected["plan_id"]).status == "APPROVED_PENDING_ACK"
        assert [r.plan_id for r in session.scalars(select(Reservation))] == [selected["plan_id"]]
    accepted = flow.post(
        f"/plans/{selected['plan_id']}/receipts",
        {"decision": "ACCEPTED", "expected_plan_version": approved.json()["data"]["plan_version"]},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["data"]["status"] == "ACTIVE"


@pytest.mark.parametrize("same_family", [True, False], ids=["alternative", "historical"])
def test_reject_unselected_candidate_preserves_approval_reservation_and_ack(flow, same_family):
    from app import models as m
    from app.services.common import plan_data

    selected = flow.generate()
    if same_family:
        with flow.factory() as session:
            other = plan_data(session.scalar(select(Plan).where(Plan.id != selected["plan_id"])))
    else:
        other, selected = selected, refresh_family(flow, selected)
    approved = flow.approve(selected)
    assert approved.status_code == 200, approved.text
    with flow.factory() as session:
        baseline = session.get(m.Incident, selected["incident_id"]).coordination_version
    rejected = flow.post(
        f"/plans/{other['plan_id']}/reject",
        {"expected_plan_version": other["plan_version"], "reason": "Discard this alternative"},
        "dispatcher",
    )
    assert rejected.status_code == 200, rejected.text
    with flow.factory() as session:
        incident = session.get(m.Incident, selected["incident_id"])
        assert incident.status == "AWAITING_ACK"
        assert incident.version == 1 and incident.coordination_version == baseline
        assert session.get(Plan, other["plan_id"]).status == "REJECTED"
        assert session.get(Plan, selected["plan_id"]).status == "APPROVED_PENDING_ACK"
        assert [r.plan_id for r in session.scalars(select(Reservation))] == [selected["plan_id"]]
    accepted = flow.post(
        f"/plans/{selected['plan_id']}/receipts",
        {"decision": "ACCEPTED", "expected_plan_version": approved.json()["data"]["plan_version"]},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["data"]["status"] == "ACTIVE"


@pytest.mark.parametrize("action", ["expire", "reject"])
@pytest.mark.parametrize("phase", ["queued", "computing", "analysis-ready", "current-family"])
def test_historical_candidate_cannot_overwrite_new_workflow(flow, monkeypatch, action, phase):
    from app import models as m
    from app.services import jobs

    old = flow.generate()
    snapshot = flow.context()
    body = {
        "incident_id": old["incident_id"],
        "expected_incident_version": 1,
        "snapshot_id": snapshot["snapshot_id"],
    }
    submitted = flow.post("/impact-analyses", body, "dispatcher")
    assert submitted.status_code == 202, submitted.text
    job_id = submitted.json()["data"]["job_id"]
    expected = "ANALYZING"
    with flow.factory() as session:
        baseline = session.get(m.Incident, old["incident_id"]).coordination_version

    def discard_old():
        if action == "expire":
            with flow.factory() as session, session.begin():
                session.get(Plan, old["plan_id"]).expires_at = m.utcnow() - timedelta(seconds=1)
            response = flow.get(f"/incidents/{old['incident_id']}/workspace", "dispatcher")
        else:
            response = flow.post(
                f"/plans/{old['plan_id']}/reject",
                {"expected_plan_version": 1, "reason": "Discard obsolete candidate"},
                "dispatcher",
            )
        assert response.status_code == 200, response.text
        with flow.factory() as session:
            incident = session.get(m.Incident, old["incident_id"])
            assert incident.status == expected
            assert incident.version == 1 and incident.coordination_version == baseline
            assert session.scalar(select(Reservation)) is None

    if phase == "computing":
        original = jobs.analyze_snapshot

        def compute(*args):
            discard_old()
            return original(*args)

        monkeypatch.setattr(jobs, "analyze_snapshot", compute)
    elif phase == "queued":
        discard_old()
    else:
        assert flow.app.state.business.run_job(job_id)["job_status"] == "SUCCEEDED"
        expected = "READY_FOR_ANALYSIS"
        if phase == "current-family":
            analysis = flow.get("/jobs/" + job_id, "dispatcher").json()["data"]["result"]
            generated = flow.post(
                "/recovery-plans", {**body, "analysis_id": analysis["analysis_id"]}, "dispatcher"
            )
            assert generated.status_code == 202, generated.text
            job_id = generated.json()["data"]["job_id"]
            assert flow.app.state.business.run_job(job_id)["job_status"] == "SUCCEEDED"
            expected = "AWAITING_APPROVAL"
        discard_old()
    assert flow.app.state.business.run_job(job_id)["job_status"] == "SUCCEEDED"


@pytest.mark.parametrize("during_compute", [False, True], ids=["before-claim", "during-compute"])
@pytest.mark.parametrize("transition", ["driver-refusal", "dispatcher-reject", "lease-expiry"])
@pytest.mark.parametrize("job_type", ["analysis", "plans"])
@pytest.mark.parametrize("reopen", [False, True], ids=["manual", "reopened"])
def test_human_plan_coordination_fences_old_calculations_after_dispatcher_reopens(
    flow, monkeypatch, during_compute, transition, job_type, reopen
):
    from app import models as m
    from app.services import jobs

    plan = flow.generate()
    body = {
        "incident_id": plan["incident_id"],
        "expected_incident_version": 1,
        "snapshot_id": plan["snapshot_id"],
    }
    endpoint = "/impact-analyses" if job_type == "analysis" else "/recovery-plans"
    payload = body if job_type == "analysis" else {**body, "analysis_id": plan["analysis_id"]}
    submitted = flow.post(endpoint, payload, "dispatcher")
    assert submitted.status_code == 202, submitted.text
    old_id = submitted.json()["data"]["job_id"]
    method = "analyze_snapshot" if job_type == "analysis" else "generate_candidates"
    original = getattr(jobs, method)
    with flow.factory() as session:
        baseline = session.get(m.Incident, plan["incident_id"]).coordination_version

    def coordinate_and_reopen():
        if transition in {"driver-refusal", "lease-expiry"}:
            approved = flow.approve(plan)
            assert approved.status_code == 200, approved.text
            with flow.factory() as session:
                assert (
                    session.get(m.Incident, plan["incident_id"]).coordination_version
                    == baseline + 1
                )
            if transition == "driver-refusal":
                refused = flow.post(
                    f"/plans/{plan['plan_id']}/receipts",
                    {
                        "decision": "REJECTED",
                        "reason": "cannot execute",
                        "expected_plan_version": approved.json()["data"]["plan_version"],
                    },
                )
                assert refused.status_code == 200, refused.text
            else:
                with flow.factory() as session, session.begin():
                    for reservation in session.scalars(select(m.Reservation)):
                        reservation.expires_at = m.utcnow() - timedelta(seconds=1)
                assert flow.app.state.business.expire_reservations() == [plan["plan_id"]]
        else:
            rejected = flow.post(
                f"/plans/{plan['plan_id']}/reject",
                {
                    "expected_plan_version": plan["plan_version"],
                    "reason": "needs human review",
                },
                "dispatcher",
            )
            assert rejected.status_code == 200, rejected.text
        with flow.factory() as session:
            incident = session.get(m.Incident, plan["incident_id"])
            assert incident.status == "MANUAL_REVIEW"
            assert incident.version == 1
            assert incident.coordination_version == baseline + (
                2 if transition in {"driver-refusal", "lease-expiry"} else 1
            )
        if reopen:
            reopened = flow.post("/impact-analyses", body, "dispatcher")
            assert reopened.status_code == 202, reopened.text
            # Finish the newly authorized work before the old publication attempt.
            monkeypatch.setattr(jobs, method, original)
            assert (
                flow.app.state.business.run_job(reopened.json()["data"]["job_id"])["job_status"]
                == "SUCCEEDED"
            )

    if during_compute:

        def compute(*args, **kwargs):
            coordinate_and_reopen()
            return original(*args, **kwargs)

        monkeypatch.setattr(jobs, method, compute)
    else:
        coordinate_and_reopen()
    assert flow.app.state.business.run_job(old_id)["job_status"] == "FAILED"
    with flow.factory() as session:
        assert session.get(m.Job, old_id).result is None
        incident = session.get(m.Incident, plan["incident_id"])
        assert incident.status == ("READY_FOR_ANALYSIS" if reopen else "MANUAL_REVIEW")
        assert incident.version == 1


def test_full_benchmark_approve_wait_ack_then_activate(flow):
    plan = flow.generate()
    assert plan["metrics"]["total_distance_m"] == 20000
    approved = flow.approve(plan)
    assert approved.status_code == 200, approved.text
    data = approved.json()["data"]
    assert data["required_driver_ids"] == ["DRIVER01"]
    assert data["status"] == "APPROVED_PENDING_ACK"
    assert flow.route_revision() == 1
    receipt = {"decision": "ACCEPTED", "expected_plan_version": data["plan_version"]}
    response = flow.post(f"/plans/{plan['plan_id']}/receipts", receipt, key="accept")
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "ACTIVE"
    assert flow.route_revision() == 2
    assert flow.post(f"/plans/{plan['plan_id']}/receipts", receipt, key="accept").status_code == 200
    assert flow.route_revision() == 2
    with flow.factory() as session:
        stops = session.scalars(
            select(RouteStop).where(RouteStop.vehicle_id == "V01").order_by(RouteStop.sequence_no)
        ).all()
        assert [s.order_no for s in stops] == ["O002", "O001", "O003"]
        assert session.scalar(select(Reservation)) is None


def test_old_incident_cannot_approve(flow):
    plan = flow.generate()
    update = flow.post(
        "/incidents",
        {
            "mode": "UPDATE",
            "incident_id": plan["incident_id"],
            "expected_incident_version": 1,
            "delay_minutes": 40,
        },
    )
    assert update.status_code == 200
    assert update.json()["data"]["incident_version"] == 2
    response = flow.approve(plan)
    assert response.status_code == 409
    assert response.json()["code"] in {"STALE_INCIDENT", "STALE_PLAN"}
    assert flow.route_revision() == 1


def test_expired_reservation_durably_stales_and_releases(flow):
    plan = flow.generate()
    approved = flow.approve(plan).json()["data"]
    with flow.factory() as session:
        reservation = session.scalar(
            select(Reservation).where(Reservation.plan_id == plan["plan_id"])
        )
        reservation.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    response = flow.post(
        f"/plans/{plan['plan_id']}/receipts",
        {"decision": "ACCEPTED", "expected_plan_version": approved["plan_version"]},
    )
    assert response.status_code == 409
    with flow.factory() as session:
        assert session.get(Plan, plan["plan_id"]).status == "STALE"
        assert session.scalar(select(Reservation)) is None
    assert flow.route_revision() == 1


def test_driver_rejection_releases_reservations(flow):
    plan = flow.generate()
    approved = flow.approve(plan).json()["data"]
    response = flow.post(
        f"/plans/{plan['plan_id']}/receipts",
        {
            "decision": "REJECTED",
            "reason": "Cannot execute",
            "expected_plan_version": approved["plan_version"],
        },
    )
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "REJECTED"
    with flow.factory() as session:
        assert session.scalar(select(Reservation)) is None


def test_stop_completion_versions_and_idempotency(flow):
    body = {
        "event_type": "ARRIVED",
        "expected_route_revision": 1,
        "occurred_at": "2026-10-26T10:20:00+08:00",
    }
    response = flow.post("/stops/STOP001/completion", body, key="arrive")
    assert response.status_code == 200, response.text
    revision = response.json()["data"]["route_revision"]
    completed = {**body, "event_type": "COMPLETED", "expected_route_revision": revision}
    first = flow.post("/stops/STOP001/completion", completed, key="complete")
    assert first.status_code == 200, first.text
    second = flow.post("/stops/STOP001/completion", completed, key="complete")
    assert second.json()["data"] == first.json()["data"]
    assert flow.post("/stops/STOP002/completion", body).status_code == 409


def test_window_report_requires_dispatcher_verification_and_fresh_snapshot(flow):
    snapshot = flow.context()
    report = flow.post(
        "/incidents",
        {
            "mode": "CREATE",
            "event_type": "RECEIVING_WINDOW_CHANGE",
            "vehicle_id": "V01",
            "order_no": "O001",
            "description": "Reported window",
            "reported_window_start": "2026-10-26T10:10:00+08:00",
            "reported_window_end": "2026-10-26T10:55:00+08:00",
        },
    ).json()["data"]
    analysis = {
        "incident_id": report["incident_id"],
        "expected_incident_version": 1,
        "snapshot_id": snapshot["snapshot_id"],
    }
    assert flow.post("/impact-analyses", analysis).json()["code"] == "INCIDENT_NOT_READY"
    body = {
        "order_no": "O001",
        "expected_order_version": 1,
        "expected_incident_version": 1,
        "window_start": "2026-10-26T10:10:00+08:00",
        "window_end": "2026-10-26T10:55:00+08:00",
    }
    path = f"/incidents/{report['incident_id']}/verify-window"
    assert flow.post(path, body).status_code == 403
    verified = flow.post(path, body, "dispatcher")
    assert verified.status_code == 200, verified.text
    assert verified.json()["data"]["incident_version"] == 2
    assert (
        flow.post("/impact-analyses", {**analysis, "expected_incident_version": 2}).json()["code"]
        == "STALE_SNAPSHOT"
    )


def test_reverify_window_releases_approval_and_allows_fresh_driver_analysis(flow):
    from app import models as m

    report = flow.post(
        "/incidents",
        {
            "mode": "CREATE",
            "event_type": "RECEIVING_WINDOW_CHANGE",
            "vehicle_id": "V01",
            "order_no": "O001",
            "description": "SIMULATION revised receiving window",
            "reported_window_start": "2026-10-26T10:00:00+08:00",
            "reported_window_end": "2026-10-26T10:50:00+08:00",
        },
    ).json()["data"]
    path = f"/incidents/{report['incident_id']}/verify-window"
    verification = {
        "order_no": "O001",
        "expected_order_version": 1,
        "expected_incident_version": 1,
        "window_start": "2026-10-26T10:00:00+08:00",
        "window_end": "2026-10-26T10:50:00+08:00",
    }
    first = flow.post(path, verification, "dispatcher")
    assert first.status_code == 200, first.text
    plan = refresh_family(flow, first.json()["data"])
    old_job = flow.post(
        "/impact-analyses",
        {
            "incident_id": report["incident_id"],
            "expected_incident_version": 2,
            "snapshot_id": plan["snapshot_id"],
        },
    )
    assert old_job.status_code == 202, old_job.text
    approved = flow.approve(plan)
    assert approved.status_code == 200, approved.text
    with flow.factory() as session:
        epoch = session.get(m.Incident, report["incident_id"]).coordination_version
    verified = flow.post(
        path,
        {
            **verification,
            "expected_order_version": 2,
            "expected_incident_version": 2,
            "window_end": "2026-10-26T11:00:00+08:00",
        },
        "dispatcher",
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["data"]["status"] == "READY_FOR_ANALYSIS"
    assert verified.json()["data"]["can_analyze"] is True
    assert verified.json()["data"]["incident_version"] == 3
    with flow.factory() as session:
        incident = session.get(m.Incident, report["incident_id"])
        assert incident.status == "READY_FOR_ANALYSIS" and incident.version == 3
        assert incident.coordination_version == epoch + 1
        assert (
            incident.current_calculation_job_id is None and incident.current_plan_group_id is None
        )
        assert session.get(Plan, plan["plan_id"]).status == "STALE"
        assert session.scalar(select(Reservation)) is None
    old_ack = flow.post(
        f"/plans/{plan['plan_id']}/receipts",
        {"decision": "ACCEPTED", "expected_plan_version": approved.json()["data"]["plan_version"]},
    )
    assert old_ack.status_code == 409
    fresh = flow.post(
        "/impact-analyses",
        {
            "incident_id": report["incident_id"],
            "expected_incident_version": 3,
            "snapshot_id": flow.context()["snapshot_id"],
        },
    )
    assert fresh.status_code == 202, fresh.text
    assert (
        flow.app.state.business.run_job(old_job.json()["data"]["job_id"])["job_status"] == "FAILED"
    )
    assert flow.route_revision() == 1


def test_solver_publication_rechecks_incident_while_computation_has_no_transaction(
    flow, monkeypatch
):
    from app.services import jobs

    snapshot, incident = flow.context(), flow.incident()
    response = flow.post(
        "/impact-analyses",
        {
            "incident_id": incident["incident_id"],
            "expected_incident_version": 1,
            "snapshot_id": snapshot["snapshot_id"],
        },
    )
    job_id = response.json()["data"]["job_id"]
    original = jobs.analyze_snapshot

    def changed_during_compute(snapshot, incident):
        update = flow.post(
            "/incidents",
            {
                "mode": "UPDATE",
                "incident_id": incident["incident_id"],
                "expected_incident_version": 1,
                "delay_minutes": 40,
            },
        )
        assert update.status_code == 200
        return original(snapshot, incident)

    monkeypatch.setattr(jobs, "analyze_snapshot", changed_during_compute)
    flow.app.state.business.run_job(job_id)
    result = flow.get("/jobs/" + job_id).json()["data"]
    assert result["job_status"] == "FAILED"
    assert result["error"]["code"] == "STALE_INCIDENT"
    assert result["result"] is None


def test_description_only_update_preserves_calculation_version(flow):
    incident = flow.incident()
    response = flow.post(
        "/incidents",
        {
            "mode": "UPDATE",
            "incident_id": incident["incident_id"],
            "expected_incident_version": 1,
            "description": "Clarification only",
        },
    )
    assert response.status_code == 200
    assert response.json()["data"]["incident_version"] == 1


def test_second_new_key_receipt_does_not_reactivate(flow):
    plan = flow.generate()
    version = flow.approve(plan).json()["data"]["plan_version"]
    body = {"decision": "ACCEPTED", "expected_plan_version": version}
    assert flow.post(f"/plans/{plan['plan_id']}/receipts", body).status_code == 200
    assert flow.post(f"/plans/{plan['plan_id']}/receipts", body).status_code == 200
    assert flow.route_revision() == 2


def test_expired_snapshot_submission_and_old_analysis_are_rejected(flow):
    from app.models import Snapshot

    snapshot, incident = flow.context(), flow.incident()
    with flow.factory() as session:
        session.get(Snapshot, snapshot["snapshot_id"]).valid_until = datetime.now(UTC) - timedelta(
            seconds=1
        )
        session.commit()
    response = flow.post(
        "/impact-analyses",
        {
            "incident_id": incident["incident_id"],
            "expected_incident_version": 1,
            "snapshot_id": snapshot["snapshot_id"],
        },
    )
    assert response.status_code == 409
    assert response.json()["code"] == "STALE_SNAPSHOT"


def test_stored_target_cannot_move_loaded_order_to_another_vehicle(flow):
    plan = flow.generate()
    with flow.factory() as session:
        saved = session.get(Plan, plan["plan_id"])
        saved.target_routes = [
            {**target, "vehicle_id": "V02"} if target["order_no"] == "O001" else target
            for target in saved.target_routes
        ]
        session.commit()
    response = flow.approve(plan)
    assert response.status_code == 409
    assert flow.route_revision() == 1


def test_hard_window_no_solution_is_successful_bounded_result(flow):
    from app.models import Order

    with flow.factory() as session:
        order = session.get(Order, "O001")
        order.window_type = "HARD"
        order.latest_arrival = datetime.fromisoformat("2026-10-26T10:01:00+08:00")
        order.version += 1
        session.commit()
    report = flow.incident()
    snapshot = flow.context()
    body = {
        "incident_id": report["incident_id"],
        "expected_incident_version": 1,
        "snapshot_id": snapshot["snapshot_id"],
    }
    job_id = flow.post("/impact-analyses", body).json()["data"]["job_id"]
    flow.app.state.business.run_job(job_id)
    analysis = flow.get("/jobs/" + job_id).json()["data"]
    assert analysis["job_status"] == "SUCCEEDED"
    job_id = flow.post(
        "/recovery-plans", {**body, "analysis_id": analysis["result"]["analysis_id"]}
    ).json()["data"]["job_id"]
    flow.app.state.business.run_job(job_id)
    result = flow.get("/jobs/" + job_id).json()["data"]
    assert result["job_status"] == "SUCCEEDED"
    assert result["result"]["solution_status"] == "NO_FEASIBLE_PLAN_IN_SEARCH"
    assert result["result"]["plans"] == []


def test_reject_and_handoff_are_audited_without_route_change(flow):
    from app.models import AuditEvent

    plan = flow.generate()
    response = flow.post(
        f"/plans/{plan['plan_id']}/reject",
        {"expected_plan_version": 1, "reason": "Human review"},
        "dispatcher",
    )
    assert response.status_code == 200
    response = flow.post(
        f"/incidents/{plan['incident_id']}/manual-handoff",
        {"expected_incident_version": 1, "reason": "Human review"},
        "dispatcher",
    )
    assert response.status_code == 200
    assert response.json()["data"]["incident_version"] == 1
    assert response.json()["data"]["status"] == "MANUAL_REVIEW"
    assert flow.route_revision() == 1
    with flow.factory() as session:
        actions = {a.action for a in session.scalars(select(AuditEvent))}
        assert {"reject_plan", "manual_handoff"} <= actions


def test_worker_duplicate_delivery_does_not_duplicate_analysis(flow):
    from app.models import Analysis

    snapshot, incident = flow.context(), flow.incident()
    job_id = flow.post(
        "/impact-analyses",
        {
            "incident_id": incident["incident_id"],
            "expected_incident_version": 1,
            "snapshot_id": snapshot["snapshot_id"],
        },
    ).json()["data"]["job_id"]
    flow.app.state.business.run_job(job_id)
    flow.app.state.business.run_job(job_id)
    with flow.factory() as session:
        assert len(session.scalars(select(Analysis)).all()) == 1


def test_frontend_reads_need_only_login(flow):
    incident = flow.incident()
    assert (
        flow.get("/incidents", "dispatcher").json()["data"]["incidents"][0]["incident_id"]
        == incident["incident_id"]
    )
    assert len(flow.get("/vehicles", "dispatcher").json()["data"]["vehicles"]) == 5
    assert flow.get("/vehicles").status_code == 403
    assert flow.get("/notifications").json()["data"]["notifications"] == []


def test_breakdown_blocks_new_arrival_but_can_record_already_arrived_delivery(flow):
    arrived = flow.post(
        "/stops/STOP001/completion",
        {
            "event_type": "ARRIVED",
            "expected_route_revision": 1,
            "occurred_at": "2026-10-26T10:00:00+08:00",
        },
    )
    assert arrived.status_code == 200
    report = flow.post(
        "/incidents",
        {
            "mode": "CREATE",
            "event_type": "VEHICLE_BREAKDOWN",
            "vehicle_id": "V01",
            "description": "SIMULATION breakdown",
        },
    )
    assert report.status_code == 201
    assert flow.context()["vehicles"][0]["status"] == "UNAVAILABLE"
    completed = flow.post(
        "/stops/STOP001/completion",
        {
            "event_type": "COMPLETED",
            "expected_route_revision": 2,
            "occurred_at": "2026-10-26T10:00:00+08:00",
        },
    )
    assert completed.status_code == 200
    response = flow.post(
        "/stops/STOP002/completion",
        {
            "event_type": "ARRIVED",
            "expected_route_revision": 3,
            "occurred_at": "2026-10-26T10:00:00+08:00",
        },
    )
    assert response.status_code == 409
    assert flow.route_revision() == 3


def test_sweeper_releases_expired_leases_without_human_request(flow):
    plan = flow.generate()
    flow.approve(plan)
    with flow.factory() as session:
        session.scalar(select(Reservation)).expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    flow.app.state.business.expire_reservations()
    with flow.factory() as session:
        assert session.scalar(select(Reservation)) is None
        assert session.get(Plan, plan["plan_id"]).status == "STALE"


def multi_vehicle_candidate(flow):
    """Persist a full solver-style input for the lifecycle's multi-driver case.

    The bounded public solver currently changes one vehicle. This fixture also
    includes an unchanged third route to verify reservation/ack scope precisely.
    """
    from copy import deepcopy

    from app.models import Snapshot, new_id

    plan = flow.generate()
    extra = [flow.context("dispatcher", vehicle_id=vid) for vid in ("V02", "V03")]
    with flow.factory() as session:
        saved = session.get(Plan, plan["plan_id"])
        baseline = session.get(Snapshot, saved.snapshot_id)
        payload, deps = deepcopy(baseline.payload), deepcopy(baseline.dependencies)
        for context in extra:
            snapshot = session.get(Snapshot, context["snapshot_id"])
            for key in ("vehicles", "orders", "route_stops"):
                payload[key] += deepcopy(snapshot.payload[key])
            deps += [d for d in snapshot.dependencies if d["entity_type"] in {"VEHICLE", "ORDER"}]
        snapshot_id = new_id()
        payload["snapshot_id"] = snapshot_id
        session.add(
            Snapshot(
                id=snapshot_id,
                tenant_id="DEMO",
                user_id="DRIVER01",
                payload=payload,
                dependencies=deps,
                valid_until=baseline.valid_until,
            )
        )
        saved.snapshot_id = snapshot_id
        saved.dependencies = [
            d for d in saved.dependencies if d["entity_type"] == "INCIDENT"
        ] + deps
        saved.target_routes = saved.target_routes + [
            deepcopy(s) for s in payload["route_stops"] if s["vehicle_id"] != "V01"
        ]
        targets = deepcopy(saved.target_routes)
        for stop in targets:
            if stop["vehicle_id"] == "V02" and stop["sequence_no"] in {1, 2}:
                stop["sequence_no"] = 3 - stop["sequence_no"]
        saved.target_routes = targets
        saved.changes = saved.changes + [
            {
                "change_type": "RESEQUENCE",
                "order_no": "O004",
                "from_vehicle_id": "V02",
                "to_vehicle_id": "V02",
                "old_sequence_no": 1,
                "new_sequence_no": 2,
            },
            {
                "change_type": "RESEQUENCE",
                "order_no": "O005",
                "from_vehicle_id": "V02",
                "to_vehicle_id": "V02",
                "old_sequence_no": 2,
                "new_sequence_no": 1,
            },
        ]
        saved.required_driver_ids = ["DRIVER01", "DRIVER02"]
        session.commit()
    return {**plan, "snapshot_id": snapshot_id}


def test_all_related_drivers_accept_and_unaffected_vehicle_is_not_reserved(flow):
    from app.models import Vehicle

    plan = multi_vehicle_candidate(flow)
    approved = flow.approve(plan)
    assert approved.status_code == 200, approved.text
    version = approved.json()["data"]["plan_version"]
    assert approved.json()["data"]["required_driver_ids"] == ["DRIVER01", "DRIVER02"]
    with flow.factory() as session:
        assert {r.vehicle_id for r in session.scalars(select(Reservation))} == {"V01", "V02"}
    body = {"decision": "ACCEPTED", "expected_plan_version": version}
    first = flow.post(f"/plans/{plan['plan_id']}/receipts", body, "driver02")
    assert first.status_code == 200, first.text
    assert first.json()["data"]["status"] == "APPROVED_PENDING_ACK"
    assert flow.get(f"/plans/{plan['plan_id']}", "driver02").status_code == 200
    assert flow.route_revision() == 1
    second = flow.post(f"/plans/{plan['plan_id']}/receipts", body)
    assert second.status_code == 200, second.text
    assert second.json()["data"]["status"] == "ACTIVE"
    with flow.factory() as session:
        assert session.get(Vehicle, "V02").route_revision == 2
        assert session.get(Vehicle, "V03").route_revision == 1
        assert session.scalar(select(Reservation)) is None


def test_repeated_partial_acceptance_checks_expired_lease(flow):
    plan = multi_vehicle_candidate(flow)
    version = flow.approve(plan).json()["data"]["plan_version"]
    body = {"decision": "ACCEPTED", "expected_plan_version": version}
    path = f"/plans/{plan['plan_id']}/receipts"
    assert flow.post(path, body).status_code == 200
    with flow.factory() as session:
        for reservation in session.scalars(select(Reservation)):
            reservation.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    response = flow.post(path, body)
    assert response.status_code == 409
    with flow.factory() as session:
        assert session.get(Plan, plan["plan_id"]).status == "STALE"
        assert session.scalar(select(Reservation)) is None


def test_postgresql_competing_dispatchers_cannot_reserve_same_vehicle(flow):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    import pytest

    from app.models import User
    from app.services.common import plan_data

    if flow.factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("PostgreSQL row-lock integration test; set TEST_DATABASE_URL")
    first = flow.generate()
    with flow.factory() as session:
        other = session.scalar(
            select(Plan).where(
                Plan.incident_id == first["incident_id"], Plan.id != first["plan_id"]
            )
        )
        second = plan_data(other)
        original = session.get(User, "DISPATCHER01")
        session.add(
            User(
                id="DISPATCHER02",
                tenant_id="DEMO",
                username="dispatcher02",
                password_hash=original.password_hash,
                role="DISPATCHER",
            )
        )
        session.commit()
    flow.headers("dispatcher")
    response = flow.client.post(
        "/api/v1/auth/login", json={"username": "dispatcher02", "password": "dispatch-test"}
    )
    assert response.status_code == 200
    flow.tokens["dispatcher02"] = response.json()["data"]["access_token"]
    barrier = Barrier(2)

    def approve(plan, actor):
        barrier.wait(timeout=5)
        return flow.post(
            f"/plans/{plan['plan_id']}/approve",
            {
                "expected_plan_version": plan["plan_version"],
                "expected_incident_version": plan["incident_version"],
                "snapshot_id": plan["snapshot_id"],
            },
            actor,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(approve, plan, actor)
            for plan, actor in [(first, "dispatcher"), (second, "dispatcher02")]
        ]
        responses = [future.result(timeout=15) for future in futures]
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert (
        next(response for response in responses if response.status_code == 409).json()["code"]
        == "RESOURCE_BUSY"
    )
    with flow.factory() as session:
        assert len(session.scalars(select(Reservation)).all()) == 1
    assert flow.route_revision() == 1


def test_breakdown_cannot_enter_automatic_analysis(flow):
    report = flow.post(
        "/incidents",
        {
            "mode": "CREATE",
            "event_type": "VEHICLE_BREAKDOWN",
            "vehicle_id": "V01",
            "description": "SIMULATION breakdown",
        },
    ).json()["data"]
    snapshot = flow.context()
    body = {
        "incident_id": report["incident_id"],
        "expected_incident_version": 1,
        "snapshot_id": snapshot["snapshot_id"],
    }
    for actor in ("driver01", "dispatcher"):
        response = flow.post("/impact-analyses", body, actor)
        assert response.status_code == 409
        assert response.json()["code"] == "INCIDENT_NOT_READY"


def test_only_dispatcher_can_reopen_manual_road_incident_with_audit(flow):
    from app.models import AuditEvent

    plan = flow.generate()
    rejected = flow.post(
        f"/plans/{plan['plan_id']}/reject",
        {"expected_plan_version": 1, "reason": "Needs review"},
        "dispatcher",
    )
    assert rejected.status_code == 200
    body = {
        "incident_id": plan["incident_id"],
        "expected_incident_version": 1,
        "snapshot_id": plan["snapshot_id"],
    }
    assert flow.post("/impact-analyses", body).status_code == 409
    response = flow.post("/impact-analyses", body, "dispatcher")
    assert response.status_code == 202, response.text
    with flow.factory() as session:
        assert "reopen_manual_incident" in {a.action for a in session.scalars(select(AuditEvent))}


@pytest.mark.parametrize("during_compute", [False, True], ids=["before-claim", "during-compute"])
@pytest.mark.parametrize("reopen", [False, True], ids=["manual", "reopened"])
def test_manual_handoff_fences_older_dispatcher_job(flow, monkeypatch, during_compute, reopen):
    from app.models import Analysis, AuditEvent, Incident, Job
    from app.services import jobs

    snapshot, incident = flow.context(), flow.incident()
    body = {
        "incident_id": incident["incident_id"],
        "expected_incident_version": 1,
        "snapshot_id": snapshot["snapshot_id"],
    }
    response = flow.post("/impact-analyses", body, "dispatcher")
    assert response.status_code == 202
    old_job_id = response.json()["data"]["job_id"]
    original = jobs.analyze_snapshot

    def handoff_and_maybe_reopen():
        handoff = flow.post(
            f"/incidents/{incident['incident_id']}/manual-handoff",
            {"expected_incident_version": 1, "reason": "Dispatcher takes control"},
            "dispatcher",
        )
        assert handoff.status_code == 200
        assert handoff.json()["data"]["incident_version"] == 1
        if reopen:
            response = flow.post("/impact-analyses", body, "dispatcher")
            assert response.status_code == 202, response.text
            new_job_id = response.json()["data"]["job_id"]
            # The new job can finish before the old worker reaches publication.
            monkeypatch.setattr(jobs, "analyze_snapshot", original)
            assert flow.app.state.business.run_job(new_job_id)["job_status"] == "SUCCEEDED"

    if during_compute:

        def handoff_during_compute(payload, event):
            handoff_and_maybe_reopen()
            return original(payload, event)

        monkeypatch.setattr(jobs, "analyze_snapshot", handoff_during_compute)
    else:
        handoff_and_maybe_reopen()

    assert flow.app.state.business.run_job(old_job_id)["job_status"] == "FAILED"
    with flow.factory() as session:
        stored = session.get(Incident, incident["incident_id"])
        assert stored.version == 1
        assert stored.status == ("READY_FOR_ANALYSIS" if reopen else "MANUAL_REVIEW")
        assert session.get(Job, old_job_id).result is None
        assert len(session.scalars(select(Analysis)).all()) == (1 if reopen else 0)
        audits = session.scalars(
            select(AuditEvent).where(AuditEvent.action == "reopen_manual_incident")
        ).all()
        assert len(audits) == (1 if reopen else 0)
