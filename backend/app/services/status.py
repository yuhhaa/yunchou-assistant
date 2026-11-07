from copy import deepcopy

from sqlalchemy import select

from app import models as m
from app.errors import DomainError
from app.services.common import (
    PersistedConflict,
    dispatcher,
    fail,
    get,
    incident_access,
    incident_data,
    iso,
    plan_access,
    plan_data,
    rows,
    snapshot_valid,
    vehicle_access,
)
from app.services.lifecycle import check_plan


def analysis_data(session, analysis):
    incident = session.get(m.Incident, analysis.incident_id)
    snapshot = session.get(m.Snapshot, analysis.snapshot_id)
    try:
        snapshot_valid(session, snapshot)
        if incident.version != analysis.incident_version:
            fail("STALE_INCIDENT")
    except DomainError:
        analysis.status = "STALE"
    return {**analysis.result, "analysis_status": analysis.status}


def current_plan(session, plan):
    if plan.status in {"PENDING_APPROVAL", "APPROVED_PENDING_ACK"}:
        try:
            check_plan(session, plan, reservation_required=plan.status == "APPROVED_PENDING_ACK")
        except PersistedConflict:
            pass
    return plan_data(plan)


def job_data(session, actor, job_id, *, tool=False):
    job = get(session, m.Job, job_id, actor)
    if job.user_id != actor.user_id:
        fail("NOT_FOUND", status=404)
    if tool and job.job_type == "CHAT":
        fail("NOT_FOUND", status=404)
    if job.job_type == "MAP_ROUTING":
        dispatcher(actor)
    if job.incident_id:
        incident_access(session, actor, job.incident_id)
    elif tool:
        fail("NOT_FOUND", status=404)
    result = deepcopy(job.result)
    if result and job.job_type == "CHAT":
        from app.services.chat import reconcile

        result = reconcile(result, actor, None, existing_session=session).model_dump(mode="json")
        if job.result["reply"].startswith("SIMULATION"):
            result["reply"] = "SIMULATION 本地规则模拟器：" + result["reply"]
    if result and job.job_type == "IMPACT_ANALYSIS":
        result = analysis_data(session, session.get(m.Analysis, result["analysis_id"]))
    if result and job.job_type == "PLAN_GENERATION":
        result["plans"] = [
            current_plan(session, session.get(m.Plan, p["plan_id"])) for p in result["plans"]
        ]
    return {
        "job_id": job.id,
        "job_type": job.job_type,
        "job_status": job.status,
        "result_kind": job.job_type,
        "result": result,
        "error": job.error,
        "poll_after_seconds": 2 if job.status == "QUEUED" else 5,
    }


def operation_target(data):
    for kind, key in [
        ("JOB", "job_id"),
        ("PLAN", "plan_id"),
        ("INCIDENT", "incident_id"),
        ("STOP", "stop_id"),
        ("ORDER", "order_no"),
        ("NODE", "node_id"),
    ]:
        if data.get(key):
            return kind, data[key]
    return None, None


def operation_data(session, actor, operation, *, tool=False):
    if operation.user_id != actor.user_id:
        fail("NOT_FOUND", status=404)
    data = operation.response.get("data") or {}
    resource_type, resource_id = (
        (operation.target_type, operation.target_id)
        if operation.target_type and operation.target_id
        else operation_target(data)
    )
    if resource_type == "JOB":
        job_data(session, actor, resource_id, tool=tool)
    elif resource_type == "INCIDENT":
        incident_access(session, actor, resource_id)
    elif resource_type == "PLAN":
        plan_access(session, actor, resource_id)
    elif resource_type == "STOP":
        stop = get(session, m.RouteStop, resource_id, actor)
        vehicle_access(session, actor, stop.vehicle_id)
    elif resource_type == "ORDER":
        order = get(session, m.Order, resource_id, actor)
        vehicle_access(session, actor, order.assigned_vehicle_id)
    elif resource_type == "NODE":
        if tool:
            fail("NOT_FOUND", status=404)
        dispatcher(actor)
        get(session, m.Node, resource_id, actor)
    elif tool:
        fail("NOT_FOUND", status=404)
    return {
        "operation_id": operation.id,
        "operation_type": operation.action,
        "operation_status": operation.status,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "job_id": data.get("job_id"),
    }


def request_operation(session, actor, key):
    operation = session.scalar(
        select(m.Operation).where(
            m.Operation.tenant_id == actor.tenant_id,
            m.Operation.user_id == actor.user_id,
            m.Operation.idempotency_key == key,
        )
    )
    if operation:
        return operation
    alias = session.scalar(
        select(m.OperationAlias).where(
            m.OperationAlias.tenant_id == actor.tenant_id,
            m.OperationAlias.user_id == actor.user_id,
            m.OperationAlias.idempotency_key == key,
        )
    )
    return session.get(m.Operation, alias.operation_id) if alias else None


def status(session, actor, object_type, object_id):
    kind, state, result, error = None, None, None, None
    if object_type == "INCIDENT":
        result, kind = incident_data(incident_access(session, actor, object_id)), "INCIDENT"
    elif object_type == "PLAN":
        result, kind = (
            current_plan(session, plan_access(session, actor, object_id)),
            "RECOVERY_PLAN",
        )
    elif object_type == "ANALYSIS":
        analysis = get(session, m.Analysis, object_id, actor)
        incident_access(session, actor, analysis.incident_id)
        result, kind = analysis_data(session, analysis), "IMPACT_ANALYSIS"
    elif object_type == "JOB":
        job = job_data(session, actor, object_id, tool=actor.roots is not None)
        result, kind, state, error = (
            job["result"],
            job["result_kind"],
            job["job_status"],
            job["error"],
        )
    elif object_type in {"REQUEST", "OPERATION"}:
        if object_type == "REQUEST":
            operation = request_operation(session, actor, object_id)
            if not operation:
                fail("REQUEST_NOT_FOUND", status=404)
        else:
            operation = get(session, m.Operation, object_id, actor)
        result = operation_data(session, actor, operation, tool=actor.roots is not None)
        kind = "OPERATION"
    return {
        "object_type": object_type,
        "object_id": object_id,
        "job_status": state,
        "result_kind": kind,
        "result": result,
        "error": error,
    }


def incident_list(session, actor):
    visible = []
    for incident in rows(session, m.Incident, actor.tenant_id):
        try:
            incident_access(session, actor, incident.id)
            visible.append(incident_data(incident))
        except DomainError:
            continue
    return {"incidents": visible}


def incident_workspace(session, actor, incident_id):
    incident = incident_access(session, actor, incident_id)
    analyses = session.scalars(
        select(m.Analysis)
        .where(m.Analysis.tenant_id == actor.tenant_id, m.Analysis.incident_id == incident.id)
        .order_by(m.Analysis.created_at, m.Analysis.id)
    ).all()
    plans = session.scalars(
        select(m.Plan)
        .where(m.Plan.tenant_id == actor.tenant_id, m.Plan.incident_id == incident.id)
        .order_by(m.Plan.created_at, m.Plan.id)
    ).all()
    # Current result projections recheck dependency versions and expiration under
    # the caller's fleet lock. No creator's job or CHAT record is exposed.
    projected_analyses = [analysis_data(session, analysis) for analysis in analyses]
    projected_plans = [current_plan(session, plan_access(session, actor, p.id)) for p in plans]
    # Expiring a plan/reservation can change this incident's status. Serialize
    # the incident only after those transitions so all projections agree.
    return {
        "incident": incident_data(incident),
        "analyses": projected_analyses,
        "plans": projected_plans,
    }


def notifications(session, actor):
    return {
        "notifications": [
            {
                "notification_id": n.id,
                "event_id": n.event_id,
                "payload": n.payload,
                "created_at": iso(n.created_at),
                "read_at": iso(n.read_at),
            }
            for n in rows(session, m.Notification, actor.tenant_id)
            if n.user_id == actor.user_id
        ]
    }
