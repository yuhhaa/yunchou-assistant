"""Turn action receipts, committed in the transaction that accepts a business write."""

from app import models as m
from app.services.common import audit, canonical_hash, fail, refresh_locked
from app.services.status import request_operation


def logical_action(name, values):
    # One accepted user report per turn. Wording, facts, headers and attempts cannot
    # authorize another CREATE; changed content needs verification/a new user turn.
    if name == "upsert_incident" and values["mode"] == "CREATE":
        return "incident:create"
    return canonical_hash(name, values)


def reserve(session, actor, turn_id):
    # Caller holds fleet + execution job locks until the business transaction ends.
    turn = refresh_locked(session, m.ChatTurn, turn_id)
    if (
        not turn
        or turn.task_id != actor.task_id
        or turn.user_id != actor.user_id
        or turn.tenant_id != actor.tenant_id
    ):
        fail("NOT_FOUND", status=404)
    if turn.tool_call_count >= 8:
        fail("RATE_LIMITED", "Eight tool calls per turn reached", 429, next_action="MANUAL_REVIEW")
    turn.tool_call_count += 1
    return turn


def accepted(session, actor, turn, logical):
    call = turn.tool_state.get(logical)
    if call:
        operation = request_operation(session, actor, call["key"])
        if operation:
            return operation
    if logical == "incident:create":
        # Upgrade recovery: 0001 persisted a body-hash key before the business write.
        # A committed CREATE may therefore exist without data/operation_id in state.
        for call in turn.tool_state.values():
            operation = request_operation(session, actor, call["key"])
            if (
                operation
                and operation.action == "upsert_incident"
                and operation.response_status == 201
            ):
                return operation
    return None


def record(session, actor, turn, logical, name, operation, request_id):
    turn.tool_state = {
        **turn.tool_state,
        logical: {
            "name": name,
            "key": operation.idempotency_key,
            "data": operation.response.get("data"),
            "operation_id": operation.id,
        },
    }
    audit(
        session,
        actor,
        "tool_call",
        "TURN",
        turn.id,
        {
            "trace_id": turn.trace_id,
            "tool": name,
            "job_id": turn.job_id,
            "status": operation.response_status,
        },
        operation.id,
        request_id=request_id,
    )
