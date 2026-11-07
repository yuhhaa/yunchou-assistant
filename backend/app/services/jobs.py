"""Durable claim / compute / publish boundary; CPU work owns no database session."""

from copy import deepcopy
from datetime import datetime
from itertools import pairwise

from app import models as m
from app.config import Settings
from app.errors import DomainError
from app.services.common import (
    Actor,
    audit,
    aware,
    calculation_inputs,
    expires,
    fail,
    iso,
    lock_fleet,
    plan_data,
    refresh_locked,
    rows,
)
from app.solver import analyze_snapshot, generate_candidates


def incident_fingerprint(session, tenant_id, vehicle_id):
    return sorted(
        [
            [i.id, i.version]
            for i in rows(session, m.Incident, tenant_id)
            if i.vehicle_id == vehicle_id
        ]
    )


def submit(session, actor, body, settings, operation_id, job_type):
    values = body.model_dump(mode="json")
    incident, _snapshot, _analysis = calculation_inputs(session, actor, values)
    validate_facts(_snapshot.payload, job_type)
    if incident.status == "MANUAL_REVIEW":
        audit(
            session,
            actor,
            "reopen_manual_incident",
            "INCIDENT",
            incident.id,
            {"job_type": job_type, "incident_version": incident.version},
            operation_id,
        )
    job = m.Job(
        id=m.new_id(),
        tenant_id=actor.tenant_id,
        user_id=actor.user_id,
        operation_id=operation_id,
        job_type=job_type,
        incident_id=incident.id,
        input={
            **values,
            "incident_coordination_version": incident.coordination_version,
            "resource_incident_versions": incident_fingerprint(
                session, actor.tenant_id, incident.vehicle_id
            ),
        },
        status="QUEUED",
    )
    session.add(job)
    # A new immutable snapshot starts a new workflow. Recomputing the same inputs
    # retains the current alternatives so an explicit rejection still fences it.
    current_family = next(
        (
            plan
            for plan in rows(session, m.Plan, actor.tenant_id)
            if plan.incident_id == incident.id
            and plan.plan_group_id == incident.current_plan_group_id
        ),
        None,
    )
    if current_family is None or current_family.snapshot_id != values["snapshot_id"]:
        incident.current_plan_group_id = None
    incident.current_calculation_job_id = job.id
    session.add(
        m.OutboxEvent(
            tenant_id=actor.tenant_id,
            event_type="JOB_SUBMITTED",
            payload={"job_id": job.id, "job_type": job_type},
        )
    )
    incident.status = "ANALYZING"
    return {
        "job_id": job.id,
        "job_type": job_type,
        "job_status": "QUEUED",
        "incident_id": incident.id,
        "poll_after_seconds": 2,
    }, 202


def validate_facts(payload, job_type):
    """Cheap input completeness check; no route simulation or search under locks."""
    missing = set(payload.get("missing_fields", []))
    matrix = payload.get("routing") or {}
    for vehicle in payload["vehicles"]:
        vid = vehicle["vehicle_id"]
        if not vehicle.get("location_node_id"):
            missing.add(f"vehicles.{vid}.location_node_id")
        if not vehicle.get("location_time") or datetime.fromisoformat(
            vehicle["location_time"]
        ) > datetime.fromisoformat(payload["clock_time"]):
            missing.add(f"vehicles.{vid}.location_time")
        route = sorted(
            [
                s
                for s in payload["route_stops"]
                if s["vehicle_id"] == vid and s["status"] not in {"COMPLETED", "CANCELLED"}
            ],
            key=lambda s: s["sequence_no"],
        )
        nodes = [vehicle["location_node_id"], *[s["node_id"] for s in route]]
        edges = list(pairwise(nodes))
        if job_type == "PLAN_GENERATION":
            edges = [
                (source, target) for source in nodes for target in nodes[1:] if source != target
            ]
        for source, target in edges:
            edge = matrix.get(source, {}).get(target)
            if not isinstance(edge, dict) or any(
                type(edge.get(k)) is not int or edge[k] < 0 for k in ("minutes", "distance_m")
            ):
                missing.add(f"routing.{source}.{target}")
    if missing:
        fail("DATA_INCOMPLETE", "Missing calculation inputs", 422, missing_fields=sorted(missing))


def _inputs(session, job):
    user = session.get(m.User, job.user_id)
    if not user or not user.active or user.tenant_id != job.tenant_id:
        fail("NOT_FOUND", status=404)
    actor = Actor(user.id, user.tenant_id, user.role, user.vehicle_id)
    incident, snapshot, analysis = calculation_inputs(session, actor, job.input)
    if job.input.get("incident_coordination_version") != incident.coordination_version:
        fail("INCIDENT_NOT_READY", "Calculation predates human or reservation coordination")
    if job.input.get("resource_incident_versions") != incident_fingerprint(
        session, job.tenant_id, incident.vehicle_id
    ):
        fail("STALE_INCIDENT", "Another incident changed this resource")
    return incident, snapshot, analysis


def _failed(session, job, error):
    job.status, job.error, job.result = "FAILED", error.as_dict(), None
    job.completed_at, job.lease_owner, job.lease_expires_at = m.utcnow(), None, None
    incident = session.get(m.Incident, job.incident_id)
    if (
        incident
        and incident.current_calculation_job_id == job.id
        and incident.status == "ANALYZING"
        and incident.version == job.input.get("expected_incident_version")
        and incident.coordination_version == job.input.get("incident_coordination_version")
    ):
        incident.status = "MANUAL_REVIEW"
        incident.current_plan_group_id = None


def run_job(job_id, *, session_factory, settings=None, worker_id=None):
    """Execute one calculation job, returning its durable status.

    Idempotent for terminal/running jobs. Claim commits RUNNING plus a unique
    lease_owner and attempts fence, compute runs without a session, publication
    reacquires fleet locks and validates inputs/fence. Task3 may reclaim expired
    leases by advancing attempts and assigning a new owner before dispatch.
    CHAT jobs are delegated to Task3 and never executed here.
    """
    settings = settings or Settings()
    with session_factory() as session:
        initial = session.get(m.Job, job_id)
        is_map = initial is not None and initial.job_type == "MAP_ROUTING"
    if is_map:
        from app.services.maps import run_refresh

        return run_refresh(
            job_id, session_factory=session_factory, settings=settings, worker_id=worker_id
        )
    owner = f"{worker_id or 'business'}-{m.new_id()}"[:128]
    with session_factory() as session, session.begin():
        initial = session.get(m.Job, job_id)
        if not initial:
            fail("NOT_FOUND", status=404)
        lock_fleet(session, initial.tenant_id)
        job = refresh_locked(session, m.Job, job_id)
        if job.job_type not in {"IMPACT_ANALYSIS", "PLAN_GENERATION"}:
            fail("INVALID_INPUT", "Unsupported calculation job", 422)
        if job.status != "QUEUED":
            return {"job_id": job.id, "job_status": job.status}
        try:
            incident, snapshot, analysis = _inputs(session, job)
        except DomainError as error:
            _failed(session, job, error)
            return {"job_id": job.id, "job_status": "FAILED"}
        job.status, job.lease_owner = "RUNNING", owner
        job.lease_expires_at = expires(settings.computation_budget_seconds + 30)
        job.attempts += 1
        job.started_at = m.utcnow()
        fence, kind = job.attempts, job.job_type
        payload, event = (
            deepcopy(snapshot.payload),
            {
                "incident_id": incident.id,
                "version": incident.version,
                "event_type": incident.event_type,
                "vehicle_id": incident.vehicle_id,
                "delay_minutes": incident.delay_minutes,
                "verified": incident.facts.get("verified", False),
            },
        )
    # No DB session, vehicle lock, or incident lock survives this boundary.
    error, result = None, None
    try:
        result = (
            analyze_snapshot(payload, event)
            if kind == "IMPACT_ANALYSIS"
            else generate_candidates(payload, event)
        )
    except DomainError as exc:
        error = exc
    except Exception:  # noqa: BLE001 - durable worker boundary sanitizes unexpected solver failures
        error = DomainError("INTERNAL_ERROR", "Calculation failed", 500)
    with session_factory() as session, session.begin():
        initial = session.get(m.Job, job_id)
        lock_fleet(session, initial.tenant_id)
        job = refresh_locked(session, m.Job, job_id)
        if job.status != "RUNNING" or job.lease_owner != owner or job.attempts != fence:
            return {"job_id": job.id, "job_status": job.status}
        try:
            incident, snapshot, analysis = _inputs(session, job)
            if aware(job.lease_expires_at) <= m.utcnow():
                fail("OPERATION_TIMEOUT", "Calculation lease expired", 504)
            if error:
                raise error
        except DomainError as exc:
            _failed(session, job, exc)
            return {"job_id": job.id, "job_status": "FAILED"}
        if kind == "IMPACT_ANALYSIS":
            analysis_id = m.new_id()
            result = {
                **result,
                "analysis_id": analysis_id,
                "incident_id": incident.id,
                "incident_version": incident.version,
                "snapshot_id": snapshot.id,
                "analysis_status": "VALID",
            }
            session.add(
                m.Analysis(
                    id=analysis_id,
                    tenant_id=job.tenant_id,
                    incident_id=incident.id,
                    incident_version=incident.version,
                    snapshot_id=snapshot.id,
                    status="VALID",
                    result=result,
                )
            )
            if incident.current_calculation_job_id == job.id:
                incident.status = "READY_FOR_ANALYSIS"
        else:
            group_id = m.new_id()
            output = []
            for item in result["plans"]:
                affected = (
                    {incident.vehicle_id}
                    | {c["from_vehicle_id"] for c in item["changes"]}
                    | {c["to_vehicle_id"] for c in item["changes"]}
                )
                drivers = sorted(
                    {
                        v.driver_id
                        for v in rows(session, m.Vehicle, job.tenant_id)
                        if v.id in affected and v.driver_id
                    }
                )
                plan = m.Plan(
                    id=m.new_id(),
                    tenant_id=job.tenant_id,
                    version=1,
                    plan_group_id=group_id,
                    analysis_id=analysis.id,
                    incident_id=incident.id,
                    incident_version=incident.version,
                    snapshot_id=snapshot.id,
                    status="PENDING_APPROVAL",
                    expires_at=min(
                        aware(snapshot.valid_until),
                        expires(min(settings.candidate_ttl_seconds, 300)),
                    ),
                    metrics=item["metrics"],
                    changes=item["changes"],
                    constraints=item["constraints"],
                    dependencies=item["dependencies"],
                    target_routes=item["target_routes"],
                    required_driver_ids=drivers,
                )
                session.add(plan)
                output.append(plan_data(plan))
            result = {
                **result,
                "plan_group_id": group_id,
                "analysis_id": analysis.id,
                "plans": output,
                "computed_at": iso(m.utcnow()),
            }
            if incident.current_calculation_job_id == job.id:
                incident.status = "AWAITING_APPROVAL" if output else "MANUAL_REVIEW"
                incident.current_plan_group_id = group_id if output else None
        job.status, job.result, job.error = "SUCCEEDED", result, None
        job.completed_at, job.lease_owner, job.lease_expires_at = m.utcnow(), None, None
        session.add(
            m.AuditEvent(
                tenant_id=job.tenant_id,
                user_id=job.user_id,
                action="publish_job",
                entity_type="JOB",
                entity_id=job.id,
                operation_id=job.operation_id,
                detail={"job_type": kind},
            )
        )
        return {"job_id": job.id, "job_status": job.status}
