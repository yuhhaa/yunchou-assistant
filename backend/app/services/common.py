import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, timedelta

from sqlalchemy import select

from app import models as m
from app.errors import DomainError

SCOPES = [
    "get_delivery_context",
    "upsert_incident",
    "analyze_impact",
    "generate_recovery_plans",
    "get_operation_status",
]


def aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def iso(value):
    return aware(value).isoformat() if value else None


@dataclass(frozen=True)
class Actor:
    user_id: str
    tenant_id: str
    role: str
    vehicle_id: str | None
    roots: tuple[str, ...] | None = None
    task_id: str | None = None
    execution_job_id: str | None = None
    execution_attempt: int | None = None
    execution_owner: str | None = None


def check_execution(session, actor):
    """Hold the claim row through the same transaction as each tool mutation/read."""
    if actor.task_id is None:
        return
    turn = session.scalar(select(m.ChatTurn).where(m.ChatTurn.task_id == actor.task_id))
    if not turn:
        return
    job = refresh_locked(session, m.Job, turn.job_id)
    if (
        turn.user_id != actor.user_id
        or turn.tenant_id != actor.tenant_id
        or job.id != actor.execution_job_id
        or job.attempts != actor.execution_attempt
        or job.lease_owner != actor.execution_owner
        or job.status != "RUNNING"
        or aware(job.lease_expires_at) <= m.utcnow()
    ):
        fail("OPERATION_TIMEOUT", "Chat execution claim expired or replaced", 504)


class PersistedConflict(DomainError):
    """A rejected action whose stale/release transitions must still commit."""


def fail(code, reason=None, status=409, **kwargs):
    raise DomainError(code, reason or code, status, **kwargs)


def rows(session, model, tenant_id):
    return session.scalars(select(model).where(model.tenant_id == tenant_id)).all()


def get(session, model, object_id, actor):
    obj = session.get(model, object_id)
    if obj is None or obj.tenant_id != actor.tenant_id:
        fail("NOT_FOUND", status=404)
    return obj


def vehicle_access(session, actor, vehicle_id):
    vehicle = get(session, m.Vehicle, vehicle_id, actor)
    if actor.role != "DISPATCHER" and vehicle.driver_id != actor.user_id:
        fail("NOT_FOUND", status=404)
    if actor.roots is not None and vehicle.id not in actor.roots:
        fail("NOT_FOUND", status=404)
    return vehicle


def incident_access(session, actor, incident_id):
    incident = get(session, m.Incident, incident_id, actor)
    vehicle_access(session, actor, incident.vehicle_id)
    return incident


def plan_access(session, actor, plan_id):
    plan = get(session, m.Plan, plan_id, actor)
    if actor.role == "DRIVER" and actor.user_id in plan.required_driver_ids:
        vehicle_access(session, actor, actor.vehicle_id)
    else:
        incident_access(session, actor, plan.incident_id)
    return plan


def dispatcher(actor):
    if actor.role != "DISPATCHER":
        fail("FORBIDDEN", status=403)


def lock_fleet(session, tenant_id):
    """Aggregate lock order: sorted vehicles, then incidents/plans/jobs.

    All API fact writers and publication paths take this same fleet gate. The
    prototype has five vehicles; locking briefly across the fleet avoids mixed
    aggregate lock ordering while computations and human waits remain outside.
    Always refresh identity-map state after waiting for PostgreSQL row locks.
    """
    return session.scalars(
        select(m.Vehicle)
        .where(m.Vehicle.tenant_id == tenant_id)
        .order_by(m.Vehicle.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()


def refresh_locked(session, model, object_id):
    return session.scalar(
        select(model)
        .where(model.id == object_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def audit(
    session,
    actor,
    action,
    entity_type,
    entity_id,
    detail=None,
    operation_id=None,
    *,
    request_id=None,
):
    session.add(
        m.AuditEvent(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            operation_id=operation_id,
            request_id=request_id,
            detail=detail or {},
        )
    )


def release(session, plan, status="STALE"):
    plan.status = status
    plan.version += 1
    for reservation in session.scalars(
        select(m.Reservation).where(
            m.Reservation.tenant_id == plan.tenant_id, m.Reservation.plan_id == plan.id
        )
    ):
        session.delete(reservation)
    session.flush()


def invalidate(session, tenant_id, *, incident_id=None, vehicle_id=None):
    from app.services.lifecycle import release_for_review

    stale = []
    for plan in rows(session, m.Plan, tenant_id):
        related = plan.incident_id == incident_id or any(
            d["entity_type"] == "VEHICLE" and d["entity_id"] == vehicle_id
            for d in plan.dependencies
        )
        if related and plan.status in {"PENDING_APPROVAL", "APPROVED_PENDING_ACK"}:
            release_for_review(session, plan)
            stale.append(plan.id)
    for analysis in rows(session, m.Analysis, tenant_id):
        snapshot = session.get(m.Snapshot, analysis.snapshot_id)
        if analysis.incident_id == incident_id or (
            snapshot
            and any(
                d["entity_type"] == "VEHICLE" and d["entity_id"] == vehicle_id
                for d in snapshot.dependencies
            )
        ):
            analysis.status = "STALE"
    return stale


def dependencies_valid(session, tenant_id, dependencies):
    for dep in dependencies:
        kind, object_id, version = dep["entity_type"], dep["entity_id"], dep["version"]
        model = {
            "INCIDENT": m.Incident,
            "VEHICLE": m.Vehicle,
            "ORDER": m.Order,
            "ROUTING": m.RoutingGraph,
        }.get(kind)
        if model:
            obj = session.get(model, object_id)
            actual = (
                obj.routing_version
                if obj and kind == "ROUTING"
                else str(obj.version)
                if obj
                else None
            )
        else:
            graph = session.scalar(
                select(m.RoutingGraph).where(m.RoutingGraph.tenant_id == tenant_id)
            )
            obj = graph
            actual = getattr(
                graph, "ruleset_version" if kind == "RULESET" else "fleet_set_version", None
            )
        if not obj or obj.tenant_id != tenant_id or str(actual) != str(version):
            fail("STALE_INCIDENT" if kind == "INCIDENT" else "STALE_SNAPSHOT")


def snapshot_valid(session, snapshot):
    if aware(snapshot.valid_until) <= m.utcnow():
        fail("STALE_SNAPSHOT", "Snapshot expired", next_action="REFRESH_CONTEXT")
    dependencies_valid(session, snapshot.tenant_id, snapshot.dependencies)


def calculation_inputs(session, actor, body):
    incident = incident_access(session, actor, body["incident_id"])
    if incident.version != body["expected_incident_version"]:
        fail("STALE_INCIDENT")
    if incident.event_type == "VEHICLE_BREAKDOWN":
        fail("INCIDENT_NOT_READY", "Breakdown requires manual rescue", next_action="MANUAL_REVIEW")
    if incident.status == "MANUAL_REVIEW" and actor.role != "DISPATCHER":
        fail(
            "INCIDENT_NOT_READY",
            "Dispatcher must reopen this incident",
            next_action="WAIT_DISPATCHER",
        )
    if incident.status not in {
        "READY_FOR_ANALYSIS",
        "ANALYZING",
        "AWAITING_APPROVAL",
        "MANUAL_REVIEW",
    }:
        fail("INCIDENT_NOT_READY")
    if incident.event_type == "RECEIVING_WINDOW_CHANGE" and not incident.facts.get("verified"):
        fail("INCIDENT_NOT_READY", next_action="VERIFY_WINDOW")
    snapshot = get(session, m.Snapshot, body["snapshot_id"], actor)
    if not any(v["vehicle_id"] == incident.vehicle_id for v in snapshot.payload["vehicles"]):
        fail("NOT_FOUND", status=404)
    # Snapshot access derives from every included root, not just a guessed ID.
    for vehicle in snapshot.payload["vehicles"]:
        vehicle_access(session, actor, vehicle["vehicle_id"])
    snapshot_valid(session, snapshot)
    analysis = None
    if body.get("analysis_id"):
        analysis = get(session, m.Analysis, body["analysis_id"], actor)
        if (
            analysis.incident_id != incident.id
            or analysis.snapshot_id != snapshot.id
            or analysis.incident_version != incident.version
            or analysis.status != "VALID"
        ):
            fail("STALE_PLAN", "Analysis does not match current inputs")
    return incident, snapshot, analysis


def incident_data(incident, superseded=None):
    ready = incident.status in {"READY_FOR_ANALYSIS", "ANALYZING", "AWAITING_APPROVAL"}
    return {
        "incident_id": incident.id,
        "incident_version": incident.version,
        "event_type": incident.event_type,
        "vehicle_id": incident.vehicle_id,
        "order_no": incident.order_no,
        "status": incident.status,
        "description": incident.description,
        "delay_minutes": incident.delay_minutes,
        "reported_window_start": iso(incident.reported_window_start),
        "reported_window_end": iso(incident.reported_window_end),
        "reported_at": iso(incident.reported_at),
        "occurred_at": iso(incident.occurred_at),
        "can_analyze": ready,
        "superseded_plan_ids": superseded or [],
        "next_action": "ANALYZE_IMPACT"
        if ready
        else "VERIFY_WINDOW"
        if incident.status == "NEEDS_VERIFICATION"
        else "MANUAL_REVIEW",
    }


def plan_data(plan):
    return {
        "plan_id": plan.id,
        "plan_group_id": plan.plan_group_id,
        "plan_version": plan.version,
        "incident_id": plan.incident_id,
        "incident_version": plan.incident_version,
        "snapshot_id": plan.snapshot_id,
        "analysis_id": plan.analysis_id,
        "status": plan.status,
        "expires_at": iso(plan.expires_at),
        "metrics": plan.metrics,
        "changes": plan.changes,
        "constraints": plan.constraints,
        "dependencies": plan.dependencies,
        "required_driver_ids": plan.required_driver_ids,
        "target_routes": plan.target_routes,
    }


def canonical_hash(action, body):
    return hashlib.sha256(
        json.dumps(
            {"action": action, "body": body},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


def expires(seconds):
    return m.utcnow() + timedelta(seconds=seconds)
