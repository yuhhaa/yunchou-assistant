from dataclasses import dataclass

from app import models as m
from app.errors import DomainError
from app.services import context, incidents, jobs, lifecycle, status, tool_receipts
from app.services.common import (
    PersistedConflict,
    audit,
    aware,
    canonical_hash,
    check_execution,
    expires,
    fail,
    lock_fleet,
    refresh_locked,
)


@dataclass
class Result:
    data: dict | None
    status: int = 200
    operation_id: str | None = None
    replayed: bool = False
    error: dict | None = None


class Business:
    def __init__(self, settings, session_factory):
        self.settings, self.session_factory = settings, session_factory

    def run_job(self, job_id, *, worker_id=None):
        return jobs.run_job(
            job_id,
            session_factory=self.session_factory,
            settings=self.settings,
            worker_id=worker_id,
        )

    def expire_reservations(self):
        return lifecycle.expire_reservations(session_factory=self.session_factory)

    def write(self, actor, action, body, key, *, target_id=None, request_id=None, turn_id=None):
        body_hash = canonical_hash(action, {"target_id": target_id, **body.model_dump(mode="json")})
        with self.session_factory() as session, session.begin():
            # Serializing a user's idempotency namespace makes simultaneous same-key
            # requests wait for the committed original, without a partial write.
            user = refresh_locked(session, m.User, actor.user_id)
            if not user or not user.active or user.tenant_id != actor.tenant_id:
                fail("UNAUTHENTICATED", status=401)
            lock_fleet(session, actor.tenant_id)
            check_execution(session, actor)
            turn = tool_receipts.reserve(session, actor, turn_id) if turn_id else None
            # Keep the budget reservation even when a validated write conflicts.
            # The savepoint rolls back all business/receipt/alias effects together.
            try:
                with session.begin_nested():
                    return self._accept_write(
                        session,
                        actor,
                        action,
                        body,
                        key,
                        body_hash,
                        target_id=target_id,
                        request_id=request_id,
                        turn=turn,
                    )
            except DomainError as exc:
                failure = exc
        raise failure

    def _accept_write(
        self, session, actor, action, body, key, body_hash, *, target_id, request_id, turn
    ):
        logical = (
            tool_receipts.logical_action(action, body.model_dump(mode="json")) if turn else None
        )
        public = status.request_operation(session, actor, key)
        accepted = tool_receipts.accepted(session, actor, turn, logical) if turn else None
        # The public key's action/body contract applies before turn reconciliation.
        # A different key cannot bypass an already accepted semantic CREATE either.
        for previous in (public, accepted):
            if previous and (previous.action != action or previous.body_hash != body_hash):
                fail("IDEMPOTENCY_CONFLICT", next_action="ASK_USER")
        if public and accepted and public.id != accepted.id:
            fail("IDEMPOTENCY_CONFLICT", next_action="ASK_USER")
        previous = public or accepted
        if previous:
            if aware(previous.expires_at) <= m.utcnow():
                fail(
                    "IDEMPOTENCY_CONFLICT",
                    "Idempotency retention expired; manual verification required",
                )
            if previous.action != action or previous.body_hash != body_hash:
                fail("IDEMPOTENCY_CONFLICT")
            # Original authority is still checked for the stored output roots.
            status.operation_data(session, actor, previous, tool=actor.roots is not None)
            if not public:
                # Each newly accepted alias needs its own >=24h replay window.
                # Extend the canonical receipt only after rejecting expired keys.
                previous.expires_at = max(
                    aware(previous.expires_at),
                    expires(max(self.settings.idempotency_ttl_seconds, 86400)),
                )
                session.add(
                    m.OperationAlias(
                        tenant_id=actor.tenant_id,
                        user_id=actor.user_id,
                        idempotency_key=key,
                        operation_id=previous.id,
                    )
                )
            if turn:
                tool_receipts.record(session, actor, turn, logical, action, previous, request_id)
            return Result(
                previous.response.get("data"),
                previous.response_status,
                previous.id,
                True,
                previous.response.get("error"),
            )
        operation = m.Operation(
            id=m.new_id(),
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            idempotency_key=key,
            action=action,
            target_type={
                "approve_plan": "PLAN",
                "plan_receipt": "PLAN",
                "reject_plan": "PLAN",
                "stop_completion": "STOP",
                "verify_window": "INCIDENT",
                "manual_handoff": "INCIDENT",
            }.get(action),
            target_id=target_id,
            body_hash=body_hash,
            expires_at=expires(max(self.settings.idempotency_ttl_seconds, 86400)),
        )
        session.add(operation)
        error = None
        try:
            args = (session, actor)
            suffix = (body, self.settings, operation.id)
            if action == "upsert_incident":
                data, code = incidents.upsert(*args, *suffix)
            elif action in {"update_map_node", "refresh_map_routing"}:
                from app.services import maps

                if action == "update_map_node":
                    data, code = maps.update_node(*args, target_id, *suffix)
                else:
                    data, code = maps.submit_refresh(*args, *suffix)
            elif action in {"analyze_impact", "generate_recovery_plans"}:
                data, code = jobs.submit(
                    *args,
                    *suffix,
                    "IMPACT_ANALYSIS" if action == "analyze_impact" else "PLAN_GENERATION",
                )
            elif action == "chat":
                from app.services.chat import submit

                data, code = submit(
                    session, actor, body, self.settings, operation.id, request_id=request_id
                )
            else:
                method = {
                    "approve_plan": lifecycle.approve,
                    "plan_receipt": lifecycle.receipts,
                    "reject_plan": lifecycle.reject,
                    "stop_completion": lifecycle.completion,
                    "verify_window": incidents.verify_window,
                    "manual_handoff": incidents.manual_handoff,
                }[action]
                data, code = method(*args, target_id, *suffix)
        except PersistedConflict as exc:
            data, code, error = None, exc.status_code, exc.as_dict()
        if data:
            operation.target_type, operation.target_id = status.operation_target(data)
        operation.response = {"data": data, "error": error}
        operation.response_status = code
        operation.status = "FAILED" if error else "SUCCEEDED"
        if turn:
            tool_receipts.record(session, actor, turn, logical, action, operation, request_id)
        audit(
            session,
            actor,
            action,
            "OPERATION",
            operation.id,
            {
                "target_id": target_id,
                "code": error["code"] if error else "OK",
                "task_id": actor.task_id,
            },
            operation.id,
            request_id=request_id,
        )
        return Result(data, code, operation.id, error=error)

    def read(self, actor, action, **kwargs):
        with self.session_factory() as session, session.begin():
            lock_fleet(session, actor.tenant_id)
            check_execution(session, actor)
            if action == "context":
                data = context.make_context(session, actor, self.settings, **kwargs)
            elif action == "map":
                from app.services.maps import map_data

                data = map_data(session, actor, self.settings, **kwargs)
            elif action == "demo_map":
                from app.services.demo_map import demo_map

                data = demo_map(session, actor, self.settings, **kwargs)
            elif action == "status":
                data = status.status(session, actor, **kwargs)
            elif action == "job":
                data = status.job_data(session, actor, **kwargs)
            elif action == "incidents":
                data = status.incident_list(session, actor)
            elif action == "incident_workspace":
                data = status.incident_workspace(session, actor, **kwargs)
            elif action == "notifications":
                data = status.notifications(session, actor)
            elif action == "fleet":
                from app.services.common import dispatcher, rows

                dispatcher(actor)
                data = {
                    "vehicles": [
                        {
                            "vehicle_id": v.id,
                            "vehicle_no": v.vehicle_no,
                            "driver_id": v.driver_id,
                            "status": v.status,
                        }
                        for v in rows(session, m.Vehicle, actor.tenant_id)
                    ]
                }
            return Result(data)
