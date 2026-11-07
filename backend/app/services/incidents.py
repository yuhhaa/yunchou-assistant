from app import models as m
from app.services.common import (
    audit,
    dispatcher,
    fail,
    get,
    incident_access,
    incident_data,
    invalidate,
    vehicle_access,
)


def upsert(session, actor, body, settings, operation_id):
    superseded = []
    if body.mode == "CREATE":
        vehicle = vehicle_access(session, actor, body.vehicle_id)
        if body.order_no:
            order = get(session, m.Order, body.order_no, actor)
            if order.assigned_vehicle_id != vehicle.id:
                fail("NOT_FOUND", status=404)
        values = body.model_dump(exclude={"mode", "incident_id", "expected_incident_version"})
        incident = m.Incident(
            id=m.new_id(), tenant_id=actor.tenant_id, user_id=actor.user_id, **values, facts={}
        )
        incident.status = {
            "ROAD_DELAY": "READY_FOR_ANALYSIS",
            "RECEIVING_WINDOW_CHANGE": "NEEDS_VERIFICATION",
            "VEHICLE_BREAKDOWN": "MANUAL_REVIEW",
        }[body.event_type]
        session.add(incident)
        session.flush()
        if body.event_type == "VEHICLE_BREAKDOWN":
            vehicle.status = "UNAVAILABLE"
            vehicle.version += 1
        # A new event is an independent invalidation even when vehicle facts did not change.
        superseded = invalidate(session, actor.tenant_id, vehicle_id=vehicle.id)
    else:
        incident = incident_access(session, actor, body.incident_id)
        if actor.role != "DISPATCHER" and incident.user_id != actor.user_id:
            fail("NOT_FOUND", status=404)
        if incident.version != body.expected_incident_version:
            fail("STALE_INCIDENT")
        values = body.model_dump(
            exclude_unset=True, exclude={"mode", "incident_id", "expected_incident_version"}
        )
        if incident.event_type != "ROAD_DELAY" and "delay_minutes" in values:
            fail("INVALID_INPUT", "delay_minutes applies only to road delay", 422)
        if incident.event_type != "RECEIVING_WINDOW_CHANGE" and any(
            k.startswith("reported_window") for k in values
        ):
            fail("INVALID_INPUT", "Reported windows apply only to window changes", 422)
        if any(v is None for v in values.values()):
            fail("INVALID_INPUT", "Business fields cannot be cleared", 422)
        changed = any(getattr(incident, k) != v for k, v in values.items() if k != "description")
        for key, value in values.items():
            setattr(incident, key, value)
        if changed:
            incident.version += 1
            incident.current_calculation_job_id = None
            incident.current_plan_group_id = None
            superseded = invalidate(session, actor.tenant_id, incident_id=incident.id)
            incident.status = {
                "ROAD_DELAY": "READY_FOR_ANALYSIS",
                "RECEIVING_WINDOW_CHANGE": "NEEDS_VERIFICATION",
                "VEHICLE_BREAKDOWN": "MANUAL_REVIEW",
            }[incident.event_type]
            if incident.event_type == "RECEIVING_WINDOW_CHANGE":
                incident.facts = {**incident.facts, "verified": False}
    audit(
        session,
        actor,
        "upsert_incident",
        "INCIDENT",
        incident.id,
        {"incident_version": incident.version},
        operation_id,
    )
    return incident_data(incident, superseded), 201 if body.mode == "CREATE" else 200


def verify_window(session, actor, incident_id, body, settings, operation_id):
    dispatcher(actor)
    incident = incident_access(session, actor, incident_id)
    order = get(session, m.Order, body.order_no, actor)
    if incident.event_type != "RECEIVING_WINDOW_CHANGE" or incident.order_no != order.id:
        fail("INVALID_INPUT", "Verification must refer to reported order", 422)
    if incident.version != body.expected_incident_version:
        fail("STALE_INCIDENT")
    if order.version != body.expected_order_version:
        fail("STALE_SNAPSHOT")
    order.earliest_arrival, order.latest_arrival = body.window_start, body.window_end
    order.version += 1
    incident.version += 1
    incident.current_calculation_job_id = None
    incident.current_plan_group_id = None
    incident.facts = {**incident.facts, "verified": True, "verified_by": actor.user_id}
    stale = invalidate(session, actor.tenant_id, vehicle_id=incident.vehicle_id)
    incident.status = "READY_FOR_ANALYSIS"
    audit(
        session,
        actor,
        "verify_window",
        "INCIDENT",
        incident.id,
        {"order_no": order.id, "order_version": order.version},
        operation_id,
    )
    return incident_data(incident, stale), 200


def manual_handoff(session, actor, incident_id, body, settings, operation_id):
    dispatcher(actor)
    incident = incident_access(session, actor, incident_id)
    if incident.version != body.expected_incident_version:
        fail("STALE_INCIDENT")
    incident.status = "MANUAL_REVIEW"
    incident.coordination_version += 1
    incident.current_calculation_job_id = None
    incident.current_plan_group_id = None
    stale = invalidate(session, actor.tenant_id, incident_id=incident.id)
    audit(
        session,
        actor,
        "manual_handoff",
        "INCIDENT",
        incident.id,
        {"reason": body.reason},
        operation_id,
    )
    return incident_data(incident, stale), 200
