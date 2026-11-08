from sqlalchemy import select

from app import models as m
from app.errors import DomainError
from app.services.common import (
    PersistedConflict,
    audit,
    aware,
    dependencies_valid,
    dispatcher,
    expires,
    fail,
    get,
    invalidate,
    iso,
    plan_access,
    release,
    rows,
    snapshot_valid,
    vehicle_access,
)
from app.solver import simulate_route


def affected_vehicles(session, plan):
    incident = session.get(m.Incident, plan.incident_id)
    affected = {incident.vehicle_id}
    # Solver includes unchanged routes; those do not acquire reservations or versions.
    for change in plan.changes:
        affected.update((change["from_vehicle_id"], change["to_vehicle_id"]))
    current = {s.id: s for s in rows(session, m.RouteStop, plan.tenant_id)}
    for target in plan.target_routes:
        before = current.get(target["stop_id"])
        if not before or any(
            getattr(before, k) != target[k]
            for k in ("vehicle_id", "order_no", "sequence_no", "node_id", "status")
        ):
            affected.add(target["vehicle_id"])
            if before:
                affected.add(before.vehicle_id)
    return sorted(affected)


def coordinate(incident, state):
    """Fence calculations accepted before this human/reservation decision.

    The epoch is independent of calculation facts and must survive releasing the
    temporary reservation. Plain reads/expiration of unapproved old candidates do
    not establish a new human decision and must not advance this epoch.
    """
    incident.coordination_version += 1
    incident.status = state
    incident.current_calculation_job_id = None
    incident.current_plan_group_id = None


def owns_candidate_workflow(session, incident, plan):
    """An unselected alternative never owns a selected approval's coordination."""
    return incident.current_plan_group_id == plan.plan_group_id and not session.scalar(
        select(m.Plan.id).where(
            m.Plan.incident_id == incident.id,
            m.Plan.tenant_id == incident.tenant_id,
            m.Plan.id != plan.id,
            m.Plan.status == "APPROVED_PENDING_ACK",
        )
    )


def release_for_review(session, plan, state="STALE"):
    was_approved = plan.status == "APPROVED_PENDING_ACK"
    release(session, plan, state)
    incident = session.get(m.Incident, plan.incident_id)
    if was_approved:
        coordinate(incident, "MANUAL_REVIEW")
    elif incident.status == "AWAITING_APPROVAL" and owns_candidate_workflow(
        session, incident, plan
    ):
        incident.status = "MANUAL_REVIEW"


def expire_plan(session, plan, code="STALE_PLAN", reason="Candidate expired or input changed"):
    release_for_review(session, plan)
    raise PersistedConflict(code, reason, 409, next_action="REFRESH_CONTEXT")


def check_plan(session, plan, *, reservation_required=False):
    if plan.status not in {"PENDING_APPROVAL", "APPROVED_PENDING_ACK"}:
        fail("STALE_PLAN", "Candidate is no longer awaiting this action")
    if aware(plan.expires_at) <= m.utcnow():
        expire_plan(session, plan)
    incident = session.get(m.Incident, plan.incident_id)
    if incident.version != plan.incident_version:
        expire_plan(session, plan, "STALE_INCIDENT")
    try:
        snapshot = session.get(m.Snapshot, plan.snapshot_id)
        snapshot_valid(session, snapshot)
        dependencies_valid(session, plan.tenant_id, plan.dependencies)
    except DomainError as error:
        expire_plan(session, plan, error.code)
    if reservation_required:
        reservations = session.scalars(
            select(m.Reservation).where(m.Reservation.plan_id == plan.id)
        ).all()
        if {r.vehicle_id for r in reservations} != set(affected_vehicles(session, plan)) or any(
            aware(r.expires_at) <= m.utcnow() for r in reservations
        ):
            expire_plan(session, plan, reason="Reservation lease expired")
    return incident, snapshot


def validate_target(plan, snapshot, incident):
    original = {s["stop_id"]: s for s in snapshot.payload["route_stops"]}
    targets = {s["stop_id"]: s for s in plan.target_routes}
    if len(targets) != len(plan.target_routes) or set(targets) != set(original):
        fail("STALE_PLAN", "Stored target route has incomplete stop coverage")
    for sid, before in original.items():
        target = targets[sid]
        if target["order_no"] != before["order_no"] or target["node_id"] != before["node_id"]:
            fail("STALE_PLAN", "Stored route changed delivery identity")
        if target["status"] != before["status"]:
            fail("STALE_PLAN", "Planning cannot fabricate execution status")
        if before["status"] != "PENDING" and target != before:
            fail("STALE_PLAN", "Fixed stops cannot move")
    for vehicle in snapshot.payload["vehicles"]:
        vid = vehicle["vehicle_id"]
        route = sorted(
            [s for s in plan.target_routes if s["vehicle_id"] == vid],
            key=lambda s: s["sequence_no"],
        )
        if [s["sequence_no"] for s in route] != list(range(1, len(route) + 1)):
            fail("STALE_PLAN", "Stored sequence is not continuous")
        checked = simulate_route(
            snapshot.payload,
            vid,
            [s["order_no"] for s in route],
            incident.delay_minutes or 0 if vid == incident.vehicle_id else 0,
        )
        if not checked["constraints"]["passed"]:
            fail(
                "STALE_PLAN",
                "Stored route violates current constraints",
                details=checked["constraints"],
            )


def approve(session, actor, plan_id, body, settings, operation_id):
    dispatcher(actor)
    plan = plan_access(session, actor, plan_id)
    incident, snapshot = check_plan(session, plan)
    if (
        body.expected_plan_version != plan.version
        or body.expected_incident_version != incident.version
        or body.snapshot_id != plan.snapshot_id
    ):
        fail("STALE_PLAN")
    if plan.status != "PENDING_APPROVAL":
        fail("STALE_PLAN")
    validate_target(plan, snapshot, incident)
    affected = affected_vehicles(session, plan)
    drivers = []
    for vehicle_id in affected:
        vehicle = get(session, m.Vehicle, vehicle_id, actor)
        if not vehicle.driver_id:
            fail(
                "DATA_INCOMPLETE",
                "Affected vehicle has no driver",
                422,
                missing_fields=[f"vehicles.{vehicle_id}.driver_id"],
            )
        drivers.append(vehicle.driver_id)
        existing = session.scalar(
            select(m.Reservation).where(
                m.Reservation.tenant_id == actor.tenant_id, m.Reservation.vehicle_id == vehicle_id
            )
        )
        if existing:
            if aware(existing.expires_at) <= m.utcnow():
                owner = session.get(m.Plan, existing.plan_id)
                if owner:
                    release_for_review(session, owner)
                else:
                    session.delete(existing)
                session.flush()
            else:
                fail("RESOURCE_BUSY", "Vehicle is already reserved")
    deadline = min(aware(plan.expires_at), expires(min(settings.reservation_ttl_seconds, 60)))
    for vehicle_id in affected:
        session.add(
            m.Reservation(
                tenant_id=actor.tenant_id,
                plan_id=plan.id,
                vehicle_id=vehicle_id,
                expires_at=deadline,
            )
        )
    plan.required_driver_ids = sorted(set(drivers))
    plan.status, plan.approved_by, plan.approved_at = (
        "APPROVED_PENDING_ACK",
        actor.user_id,
        m.utcnow(),
    )
    plan.version += 1
    coordinate(incident, "AWAITING_ACK")
    incident.current_plan_group_id = plan.plan_group_id
    audit(session, actor, "approve_plan", "PLAN", plan.id, {"vehicle_ids": affected}, operation_id)
    return {
        "plan_id": plan.id,
        "plan_version": plan.version,
        "status": plan.status,
        "required_driver_ids": plan.required_driver_ids,
        "reservation_expires_at": iso(deadline),
    }, 200


def _receipt_data(session, plan):
    accepted = sorted(
        a.driver_id
        for a in rows(session, m.DriverAcknowledgement, plan.tenant_id)
        if a.plan_id == plan.id and a.accepted
    )
    revisions = {
        vid: session.get(m.Vehicle, vid).route_revision for vid in affected_vehicles(session, plan)
    }
    return {
        "plan_id": plan.id,
        "plan_version": plan.version,
        "status": plan.status,
        "accepted_driver_ids": accepted,
        "pending_driver_ids": sorted(set(plan.required_driver_ids) - set(accepted)),
        "active_route_revision": (
            next(iter(revisions.values())) if len(revisions) == 1 else revisions
        )
        if plan.status == "ACTIVE"
        else None,
    }


def receipts(session, actor, plan_id, body, settings, operation_id):
    plan = get(session, m.Plan, plan_id, actor)
    if actor.role != "DRIVER" or actor.user_id not in plan.required_driver_ids:
        fail("NOT_FOUND", status=404)
    vehicle_access(session, actor, actor.vehicle_id)
    if body.expected_plan_version != plan.version:
        fail("STALE_PLAN")
    previous = session.scalar(
        select(m.DriverAcknowledgement).where(
            m.DriverAcknowledgement.plan_id == plan.id,
            m.DriverAcknowledgement.driver_id == actor.user_id,
        )
    )
    accepted = body.decision == "ACCEPTED"
    if previous:
        if previous.accepted != accepted:
            fail("STALE_PLAN", "Driver already submitted a different receipt")
        if plan.status != "ACTIVE":
            check_plan(session, plan, reservation_required=True)
        return _receipt_data(session, plan), 200
    if plan.status != "APPROVED_PENDING_ACK":
        fail("STALE_PLAN")
    incident, snapshot = check_plan(session, plan, reservation_required=True)
    session.add(
        m.DriverAcknowledgement(
            tenant_id=actor.tenant_id, plan_id=plan.id, driver_id=actor.user_id, accepted=accepted
        )
    )
    session.flush()
    if not accepted:
        release_for_review(session, plan, "REJECTED")
    elif not _receipt_data(session, plan)["pending_driver_ids"]:
        try:
            validate_target(plan, snapshot, incident)
        except DomainError as error:
            expire_plan(session, plan, error.code, error.reason)
        _activate(session, actor, plan, incident)
    audit(
        session,
        actor,
        "driver_receipt",
        "PLAN",
        plan.id,
        {"decision": body.decision, "reason": body.reason},
        operation_id,
    )
    return _receipt_data(session, plan), 200


def _activate(session, actor, plan, incident):
    affected = affected_vehicles(session, plan)
    stops = {
        s.id: s for s in rows(session, m.RouteStop, plan.tenant_id) if s.vehicle_id in affected
    }
    for stop in stops.values():
        stop.sequence_no += 100000
    session.flush()
    for target in plan.target_routes:
        if target["vehicle_id"] in affected:
            stop = stops[target["stop_id"]]
            for key in ("vehicle_id", "order_no", "sequence_no", "node_id", "status"):
                setattr(stop, key, target[key])
            stop.version += 1
    for vid in affected:
        vehicle = session.get(m.Vehicle, vid)
        vehicle.version += 1
        vehicle.route_revision += 1
    for order in rows(session, m.Order, plan.tenant_id):
        if order.assigned_vehicle_id in affected:
            order.version += 1
    plan.status, plan.activated_at = "ACTIVE", m.utcnow()
    incident.status = "EXECUTING"
    for reservation in rows(session, m.Reservation, plan.tenant_id):
        if reservation.plan_id == plan.id:
            session.delete(reservation)
    for other in rows(session, m.Plan, plan.tenant_id):
        if (
            other.id != plan.id
            and other.status in {"PENDING_APPROVAL", "APPROVED_PENDING_ACK"}
            and (
                other.incident_id == plan.incident_id
                or set(affected_vehicles(session, other)) & set(affected)
            )
        ):
            release(session, other, "SUPERSEDED")
    for driver_id in plan.required_driver_ids:
        event_id = f"active-{plan.id}"
        payload = {"type": "PLAN_ACTIVE", "plan_id": plan.id, "status": "ACTIVE"}
        session.add(
            m.Notification(
                tenant_id=plan.tenant_id, user_id=driver_id, event_id=event_id, payload=payload
            )
        )
        session.add(
            m.OutboxEvent(
                tenant_id=plan.tenant_id,
                event_type="NOTIFICATION_CREATED",
                payload={**payload, "user_id": driver_id, "event_id": event_id},
            )
        )


def reject(session, actor, plan_id, body, settings, operation_id):
    dispatcher(actor)
    plan = plan_access(session, actor, plan_id)
    if plan.version != body.expected_plan_version:
        fail("STALE_PLAN")
    check_plan(session, plan)
    incident = session.get(m.Incident, plan.incident_id)
    owns_workflow = plan.status == "APPROVED_PENDING_ACK" or owns_candidate_workflow(
        session, incident, plan
    )
    release(session, plan, "REJECTED")
    if owns_workflow:
        coordinate(incident, "MANUAL_REVIEW")
    audit(session, actor, "reject_plan", "PLAN", plan.id, {"reason": body.reason}, operation_id)
    return {"plan_id": plan.id, "plan_version": plan.version, "status": plan.status}, 200


def completion(session, actor, stop_id, body, settings, operation_id):
    stop = get(session, m.RouteStop, stop_id, actor)
    vehicle = vehicle_access(session, actor, stop.vehicle_id)
    if actor.role != "DRIVER" or vehicle.driver_id != actor.user_id:
        fail("NOT_FOUND", status=404)
    if body.event_type == "ARRIVED" and vehicle.status == "UNAVAILABLE":
        fail(
            "INCIDENT_NOT_READY",
            "Unavailable vehicle requires manual rescue",
            next_action="MANUAL_REVIEW",
        )
    if vehicle.route_revision != body.expected_route_revision:
        fail("STALE_PLAN", "Route revision changed")
    if body.event_type == "ARRIVED" and stop.status != "PENDING":
        fail("STALE_PLAN", "Stop is not pending")
    if body.event_type == "COMPLETED" and stop.status != "ARRIVED":
        fail("STALE_PLAN", "Arrival must be acknowledged first")
    if any(
        s.vehicle_id == vehicle.id
        and s.sequence_no < stop.sequence_no
        and s.status not in {"COMPLETED", "CANCELLED"}
        for s in rows(session, m.RouteStop, actor.tenant_id)
    ):
        fail("STALE_PLAN", "An earlier stop is unfinished")
    if stop.arrived_at and aware(body.occurred_at) < aware(stop.arrived_at):
        fail("INVALID_INPUT", "Completion precedes arrival", 422)
    stop.status = body.event_type
    if body.event_type == "ARRIVED":
        stop.arrived_at = body.occurred_at
    else:
        stop.completed_at = body.occurred_at
        order = get(session, m.Order, stop.order_no, actor)
        order.status, order.cargo_status = "COMPLETED", "DELIVERED"
        order.version += 1
    stop.version += 1
    vehicle.location_node_id, vehicle.location_time = stop.node_id, body.occurred_at
    vehicle.version += 1
    vehicle.route_revision += 1
    invalidate(session, actor.tenant_id, vehicle_id=vehicle.id)
    session.flush()
    unfinished = any(
        s.vehicle_id == vehicle.id and s.status not in {"COMPLETED", "CANCELLED"}
        for s in rows(session, m.RouteStop, actor.tenant_id)
    )
    if not unfinished:
        for incident in rows(session, m.Incident, actor.tenant_id):
            if incident.vehicle_id == vehicle.id and incident.status == "EXECUTING":
                incident.status = "RESOLVED"
    audit(
        session,
        actor,
        "stop_completion",
        "STOP",
        stop.id,
        {"event_type": body.event_type, "route_revision": vehicle.route_revision},
        operation_id,
    )
    return {
        "stop_id": stop.id,
        "status": stop.status,
        "route_revision": vehicle.route_revision,
        "order_no": stop.order_no,
        "occurred_at": iso(body.occurred_at),
    }, 200


def expire_reservations(*, session_factory):
    """Timer hook for Task3; transitions and releases are committed per tenant."""
    with session_factory() as session:
        tenant_ids = sorted(set(session.scalars(select(m.Reservation.tenant_id)).all()))
    expired_ids = []
    for tenant_id in tenant_ids:
        with session_factory() as session, session.begin():
            from app.services.common import lock_fleet

            lock_fleet(session, tenant_id)
            for reservation in rows(session, m.Reservation, tenant_id):
                if aware(reservation.expires_at) <= m.utcnow():
                    plan = session.get(m.Plan, reservation.plan_id)
                    if plan and plan.status == "APPROVED_PENDING_ACK":
                        release_for_review(session, plan)
                        expired_ids.append(plan.id)
                    elif plan is None:
                        session.delete(reservation)
    return expired_ids
