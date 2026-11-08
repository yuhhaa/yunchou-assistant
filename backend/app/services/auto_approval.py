"""Opt-in, deterministic approval. No model input controls policy or actor identity."""

from app import models as m
from app.errors import DomainError
from app.schemas import ApprovalInput
from app.services.common import Actor, audit
from app.services.lifecycle import approve

POLICY = "AUTO_RESEQUENCE_V1"
PRINCIPAL = "SYSTEM_AUTO_RESEQUENCE_V1"  # non-login policy principal, not a human dispatcher


def rejection_reasons(plan, incident, settings):
    reasons = []
    if incident.event_type != "ROAD_DELAY":
        reasons.append("EVENT_REQUIRES_HUMAN")
    if (
        incident.delay_minutes is None
        or incident.delay_minutes > settings.auto_approval_max_delay_minutes
    ):
        reasons.append("DELAY_OUTSIDE_POLICY")
    metrics = plan.metrics
    if not plan.constraints.get("passed"):
        reasons.append("CONSTRAINTS_FAILED")
    for field in (
        "unresolved_order_count",
        "late_order_count",
        "high_priority_delay_minutes",
        "reassigned_order_count",
    ):
        if metrics.get(field) != 0:
            reasons.append("NONZERO_" + field.upper())
    changed = metrics.get("changed_order_count")
    if type(changed) is not int or not 1 <= changed <= settings.auto_approval_max_changed_orders:
        reasons.append("CHANGE_LIMIT")
    extra = metrics.get("extra_distance_m")
    if type(extra) is not int or extra > 0:
        reasons.append("EXTRA_DISTANCE")
    if not plan.changes or any(
        c.get("change_type") != "RESEQUENCE"
        or c.get("from_vehicle_id") != incident.vehicle_id
        or c.get("to_vehicle_id") != incident.vehicle_id
        for c in plan.changes
    ):
        reasons.append("NOT_SAME_VEHICLE_RESEQUENCE")
    return reasons


def review_candidates(session, incident, plans, settings, operation_id=None):
    if not settings.auto_approval_enabled or incident.status != "AWAITING_APPROVAL":
        return
    candidates = [
        p
        for p in plans
        if p.status == "PENDING_APPROVAL" and p.plan_group_id == incident.current_plan_group_id
    ]
    if not candidates:
        return
    actor = Actor(PRINCIPAL, incident.tenant_id, "DISPATCHER", None)
    rejections = {}
    for plan in candidates:
        reasons = rejection_reasons(plan, incident, settings)
        snapshot = session.get(m.Snapshot, plan.snapshot_id)
        payload = snapshot.payload if snapshot else {}
        # V1 has no verified GPS/traffic evidence policy. Fail closed outside
        # the explicitly simulated dataset, regardless of the enable switch.
        if any(
            payload.get(field) != "SIMULATION"
            for field in ("source", "clock_mode", "routing_source")
        ):
            reasons.append("REAL_DATA_REQUIRES_MANUAL")
        rejections[plan.id] = reasons
    eligible = [p for p in candidates if not rejections[p.id]]
    detail = {
        "policy": POLICY,
        "decision": "REQUIRES_MANUAL",
        "evaluated": len(candidates),
        "limits": {
            "changed_orders": settings.auto_approval_max_changed_orders,
            "delay_minutes": settings.auto_approval_max_delay_minutes,
        },
        "rejections": rejections,
    }
    if eligible:
        chosen = min(
            eligible,
            key=lambda p: (
                p.metrics["total_delay_minutes"],
                p.metrics["changed_order_count"],
                p.metrics["extra_distance_m"],
                p.id,
            ),
        )
        try:
            # Reuse all existing version, snapshot, constraint and reservation checks.
            # Savepoint ensures failed validation cannot corrupt generated candidates.
            with session.begin_nested():
                approve(
                    session,
                    actor,
                    chosen.id,
                    ApprovalInput(
                        expected_plan_version=chosen.version,
                        expected_incident_version=incident.version,
                        snapshot_id=chosen.snapshot_id,
                    ),
                    settings,
                    operation_id,
                )
                session.flush()
            detail.update(decision="AUTO_APPROVED_PENDING_DRIVER", plan_id=chosen.id)
        except DomainError as exc:
            detail.update(reason=exc.code)
    audit(session, actor, "auto_approval_decision", "INCIDENT", incident.id, detail, operation_id)
