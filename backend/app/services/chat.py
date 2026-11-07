"""Durable chat acceptance, protected tool execution and DB-authoritative replies."""

import hashlib
import logging
from contextlib import nullcontext

import httpx
from sqlalchemy import select

from app import models as m
from app.errors import DomainError
from app.platform import DeepSeekPlatform, LocalSimulator, RunInput, RunResult, result
from app.schemas import ImpactInput, IncidentInput, PlanGenerationInput, StatusInput, TaskTokenInput
from app.security import create_task_token
from app.services.common import (
    SCOPES,
    Actor,
    audit,
    aware,
    check_execution,
    expires,
    fail,
    incident_access,
    lock_fleet,
    plan_access,
    refresh_locked,
)
from app.services.context import select_vehicle
from app.services.status import job_data, status
from app.services.tool_receipts import logical_action

logger = logging.getLogger(__name__)


def submit(session, actor, body, settings, operation_id, *, request_id=None):
    conversation = None
    if body.conversation_id:
        conversation = refresh_locked(session, m.Conversation, body.conversation_id)
        if (
            not conversation
            or conversation.tenant_id != actor.tenant_id
            or conversation.user_id != actor.user_id
        ):
            fail("NOT_FOUND", status=404)
        if conversation.active_turn_id:
            active = session.get(m.ChatTurn, conversation.active_turn_id)
            if active and active.status in {"QUEUED", "RUNNING"}:
                fail("RESOURCE_BUSY", "A conversation turn is still running", next_action="POLL")
    selection = dict(conversation.context) if conversation else {}
    previous_selection = dict(selection)
    if body.selected_order_no:
        selection["selected_order_no"] = body.selected_order_no
        # A new explicit order must not retain a previous incident implicitly.
        if selection.get("selected_order_no") != (conversation.context if conversation else {}).get(
            "selected_order_no"
        ):
            selection.pop("incident_id", None)
    if body.incident_id:
        selection["incident_id"] = body.incident_id
    incident = (
        incident_access(session, actor, selection["incident_id"])
        if selection.get("incident_id")
        else None
    )
    vehicle = (
        select_vehicle(
            session,
            actor,
            order_no=selection.get("selected_order_no"),
            vehicle_id=incident.vehicle_id if incident else None,
        )
        if (actor.vehicle_id or selection.get("selected_order_no") or incident)
        else None
    )
    if actor.role == "DISPATCHER" and not vehicle:
        fail(
            "MISSING_FIELDS",
            "Select an order or incident",
            422,
            missing_fields=["selected_order_no or incident_id"],
            next_action="ASK_USER",
        )
    if incident and vehicle and incident.vehicle_id != vehicle.id:
        fail("NOT_FOUND", status=404)
    selection["vehicle_id"] = vehicle.id if vehicle else None
    if not selection.get("_history_scope") or any(
        selection.get(field) != previous_selection.get(field)
        for field in ("selected_order_no", "incident_id", "vehicle_id")
    ):
        selection["_history_scope"] = m.new_id()
    if not conversation:
        conversation = m.Conversation(
            id=m.new_id(), tenant_id=actor.tenant_id, user_id=actor.user_id, context=selection
        )
        session.add(conversation)
        session.flush()
    turn_id, job_id, trace_id = m.new_id(), m.new_id(), m.new_id()
    turn = m.ChatTurn(
        id=turn_id,
        tenant_id=actor.tenant_id,
        user_id=actor.user_id,
        conversation_id=conversation.id,
        job_id=job_id,
        user_message=body.message,
        trace_id=trace_id,
        task_id=turn_id,
        tool_call_count=0,
        tool_state={},
    )
    session.add(turn)
    roots = (
        [vehicle.id]
        if vehicle
        else ["self_" + hashlib.sha256(actor.user_id.encode()).hexdigest()[:32]]
    )
    session.add(
        m.Job(
            id=job_id,
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            operation_id=operation_id,
            job_type="CHAT",
            status="QUEUED",
            incident_id=incident.id if incident else None,
            input={
                "turn_id": turn_id,
                "trace_id": trace_id,
                "business_context": selection,
                "roots": roots,
            },
        )
    )
    conversation.active_turn_id, conversation.context = turn_id, selection
    session.add(
        m.OutboxEvent(
            tenant_id=actor.tenant_id,
            event_type="JOB_SUBMITTED",
            payload={"job_id": job_id, "job_type": "CHAT"},
        )
    )
    audit(
        session,
        actor,
        "submit_chat",
        "TURN",
        turn_id,
        {"trace_id": trace_id, "job_id": job_id},
        operation_id,
        request_id=request_id,
    )
    return {
        "conversation_id": conversation.id,
        "turn_id": turn_id,
        "job_id": job_id,
        "job_type": "CHAT",
        "job_status": "QUEUED",
        "poll_after_seconds": 2,
    }, 202


class ToolGateway:
    def __init__(self, actor, turn_id, session_factory, settings, owner=None, fence=None):
        from app.services.business import Business

        self.actor, self.turn_id, self.factory, self.settings = (
            actor,
            turn_id,
            session_factory,
            settings,
        )
        self.owner, self.fence = owner, fence
        self.business = Business(settings, session_factory)

    def call(self, name, values, *, request_id=None, full_result=False, idempotency_key=None):
        request_id = request_id or m.new_id()
        if name not in SCOPES:
            fail("FORBIDDEN", status=403)
        schema = {
            "upsert_incident": IncidentInput,
            "analyze_impact": ImpactInput,
            "generate_recovery_plans": PlanGenerationInput,
            "get_delivery_context": TaskTokenInput,
            "get_operation_status": StatusInput,
        }[name]
        body = schema.model_validate(values)
        values = body.model_dump(mode="json")
        logical = logical_action(name, values)
        key = idempotency_key or f"turn_{self.turn_id}_{logical}"
        if name not in {"get_delivery_context", "get_operation_status"}:
            # Budget, execution fence, semantic receipt, aliases and business result
            # share one transaction. There is no post-commit write-state gap.
            outcome = self.business.write(
                self.actor, name, body, key, request_id=request_id, turn_id=self.turn_id
            )
            if outcome.error and not full_result:
                raise DomainError(
                    outcome.error["code"],
                    outcome.error["reason"],
                    outcome.status,
                    missing_fields=outcome.error.get("missing_fields"),
                    next_action=outcome.error.get("next_action", "MANUAL_REVIEW"),
                )
            return (outcome.data, outcome) if full_result else outcome.data
        with self.factory() as session, session.begin():
            lock_fleet(session, self.actor.tenant_id)
            check_execution(session, self.actor)
            turn = refresh_locked(session, m.ChatTurn, self.turn_id)
            job = session.get(m.Job, turn.job_id)
            if (
                job.status != "RUNNING"
                or aware(job.lease_expires_at) <= m.utcnow()
                or (
                    self.owner is not None
                    and (job.lease_owner != self.owner or job.attempts != self.fence)
                )
            ):
                fail("OPERATION_TIMEOUT", "Turn lease no longer valid", 504)
            if turn.tool_call_count >= 8:
                fail(
                    "RATE_LIMITED",
                    "Eight tool calls per turn reached",
                    429,
                    next_action="MANUAL_REVIEW",
                )
            turn.tool_call_count += 1
            state = dict(turn.tool_state)
            state[logical] = {**state.get(logical, {}), "name": name, "key": key}
            turn.tool_state = state
            cached = state[logical].get("data") if name == "get_delivery_context" else None
            trace_id = turn.trace_id
        if cached is not None:
            from app.services.business import Result

            outcome = Result(cached)
        elif name in {"get_delivery_context", "get_operation_status"}:
            outcome = self.business.read(
                self.actor, "context" if name == "get_delivery_context" else "status", **values
            )
        else:
            outcome = self.business.write(
                self.actor, name, body, key, request_id=request_id or m.new_id()
            )
        with self.factory() as session, session.begin():
            lock_fleet(session, self.actor.tenant_id)
            check_execution(session, self.actor)
            turn = refresh_locked(session, m.ChatTurn, self.turn_id)
            state = dict(turn.tool_state)
            state[logical] = {
                **state[logical],
                "data": outcome.data,
                "operation_id": outcome.operation_id,
            }
            turn.tool_state = state
            audit(
                session,
                self.actor,
                "tool_call",
                "TURN",
                turn.id,
                {
                    "trace_id": trace_id,
                    "tool": name,
                    "job_id": turn.job_id,
                    "status": outcome.status,
                },
                outcome.operation_id,
                request_id=request_id,
            )
        logger.info(
            "trace_id=%s request_id=%s job_id=%s operation_id=%s status=%s",
            trace_id,
            request_id,
            turn.job_id,
            outcome.operation_id,
            outcome.status,
        )
        return (outcome.data, outcome) if full_result else outcome.data

    def existing_jobs(self, incident_id):
        with self.factory() as session, session.begin():
            incident_access(session, self.actor, incident_id)
            jobs = session.scalars(
                select(m.Job)
                .where(
                    m.Job.incident_id == incident_id,
                    m.Job.user_id == self.actor.user_id,
                    m.Job.job_type != "CHAT",
                )
                .order_by(m.Job.created_at)
            ).all()
            return [job_data(session, self.actor, j.id) for j in jobs]


def tool_gateway(app, actor):
    with app.state.session_factory() as session:
        turn = session.scalar(select(m.ChatTurn).where(m.ChatTurn.task_id == actor.task_id))
        if not turn:
            return None  # independently issued narrow local-test token
        if turn.user_id != actor.user_id or turn.tenant_id != actor.tenant_id:
            fail("NOT_FOUND", status=404)
        return ToolGateway(actor, turn.id, app.state.session_factory, app.state.settings)


def reconcile(raw, actor, session_factory, *, existing_session=None):
    """Every referenced ID and state is checked; reply is rendered from DB facts."""
    raw = RunResult.model_validate(raw)
    output = raw.model_copy(deep=True)
    with (
        nullcontext(existing_session)
        if existing_session is not None
        else session_factory.begin() as session
    ):
        lock_fleet(session, actor.tenant_id)
        incident = incident_access(session, actor, raw.incident_id) if raw.incident_id else None
        jobs = [job_data(session, actor, jid, tool=True) for jid in raw.job_ids]
        plans = [status(session, actor, "PLAN", pid)["result"] for pid in raw.plan_ids]
        for item in jobs:
            job = session.get(m.Job, item["job_id"])
            if incident and job.incident_id != incident.id:
                fail("NOT_FOUND", status=404)
        for p in plans:
            if incident and p["incident_id"] != incident.id:
                fail("NOT_FOUND", status=404)
        if not incident and (jobs or plans):
            ids = {session.get(m.Job, j["job_id"]).incident_id for j in jobs} | {
                p["incident_id"] for p in plans
            }
            if len(ids) != 1:
                fail("NOT_FOUND", status=404)
            incident = incident_access(session, actor, ids.pop())
            output.incident_id = incident.id
        # Evidence references are reconstructed, never taken from model text.
        output.evidence_refs = [f"{incident.id}:v{incident.version}"] if incident else []
        output.evidence_refs += [f"{p['plan_id']}:v{p['plan_version']}" for p in plans]
        state, action = "NEEDS_INFO", "ASK_USER"
        if incident:
            state, action = "NEEDS_INFO", "ANALYZE_IMPACT"
            if incident.status == "NEEDS_VERIFICATION":
                action = "VERIFY_WINDOW"
            elif incident.status == "MANUAL_REVIEW":
                state, action = "MANUAL_REVIEW", "MANUAL_REVIEW"
            elif incident.status == "RESOLVED":
                # Completion requires actual persisted deliveries too.
                stops = session.scalars(
                    select(m.RouteStop).where(
                        m.RouteStop.tenant_id == actor.tenant_id,
                        m.RouteStop.vehicle_id == incident.vehicle_id,
                    )
                ).all()
                if stops and all(s.status in {"COMPLETED", "CANCELLED"} for s in stops):
                    state, action = "DONE", "NONE"
            elif incident.status == "EXECUTING":
                active = session.scalar(
                    select(m.Plan).where(
                        m.Plan.incident_id == incident.id, m.Plan.status == "ACTIVE"
                    )
                )
                if active:
                    plan_access(session, actor, active.id)
                    state, action = "ACTIVE", "NONE"
            elif incident.status == "AWAITING_ACK" or any(
                p["status"] == "APPROVED_PENDING_ACK" for p in plans
            ):
                state, action = "WAITING_ACK", "WAIT_DRIVER"
            elif incident.status == "AWAITING_APPROVAL" or any(
                p["status"] == "PENDING_APPROVAL" for p in plans
            ):
                state, action = "WAITING_APPROVAL", "WAIT_DISPATCHER"
            elif any(j["job_status"] in {"QUEUED", "RUNNING"} for j in jobs):
                state, action = "PROCESSING", "POLL"
            elif any(j["job_status"] == "FAILED" for j in jobs):
                state, action = "MANUAL_REVIEW", "MANUAL_REVIEW"
            elif any(
                j["job_type"] == "IMPACT_ANALYSIS" and j["job_status"] == "SUCCEEDED" for j in jobs
            ):
                action = "GENERATE_PLANS"
        output.business_state, output.next_action = state, action
        messages = {
            "NEEDS_INFO": "请补充明确的异常信息或预计延误分钟数；已登记事件可继续分析。",
            "PROCESSING": "计算任务已受理，正在处理，请按任务编号查询。",
            "WAITING_APPROVAL": "候选已生成，等待调度员批准。",
            "WAITING_ACK": "方案已批准并预留资源，等待所有相关司机接收。",
            "ACTIVE": "数据库确认新计划已生效，仍需实际配送与完成回执。",
            "MANUAL_REVIEW": "当前需要人工处理，请联系调度员核实。",
            "DONE": "数据库确认相关配送回执完成且事件已关闭。",
        }
        output.reply = messages[state]
    return output


def _recorded_result(factory, actor, turn_id, *, existing_session=None):
    with nullcontext(existing_session) if existing_session is not None else factory() as session:
        turn = session.get(m.ChatTurn, turn_id)
        raw = result()
        for call in turn.tool_state.values():
            data = call.get("data") or {}
            if data.get("incident_id"):
                raw.incident_id = data["incident_id"]
            if data.get("job_id") and data["job_id"] not in raw.job_ids:
                raw.job_ids.append(data["job_id"])
        # Crash after the business commit but before tool-state update: stable keys
        # find the already accepted write; never resubmit the user's report.
        for call in turn.tool_state.values():
            op = session.scalar(
                select(m.Operation).where(
                    m.Operation.tenant_id == actor.tenant_id,
                    m.Operation.user_id == actor.user_id,
                    m.Operation.idempotency_key == call["key"],
                )
            )
            data = (op.response.get("data") or {}) if op else {}
            if data.get("incident_id"):
                raw.incident_id = data["incident_id"]
            if data.get("job_id") and data["job_id"] not in raw.job_ids:
                raw.job_ids.append(data["job_id"])
        return raw


def _history(session, actor, turn, scope):
    """Only a contiguous selection segment; never replay tool or internal messages."""
    rows = session.execute(
        select(m.ChatTurn, m.Job)
        .join(m.Job, m.ChatTurn.job_id == m.Job.id)
        .where(
            m.ChatTurn.conversation_id == turn.conversation_id,
            m.ChatTurn.tenant_id == actor.tenant_id,
            m.ChatTurn.user_id == actor.user_id,
            m.ChatTurn.id != turn.id,
            m.ChatTurn.created_at <= turn.created_at,
        )
        .order_by(m.ChatTurn.created_at.desc(), m.ChatTurn.id.desc())
        .limit(6)
    ).all()
    history, size = [], 0
    for previous, job in rows:
        if job.input.get("business_context", {}).get("_history_scope") != scope:
            break
        pair = [{"role": "user", "content": previous.user_message}]
        if previous.status == "SUCCEEDED" and previous.result:
            pair.append({"role": "assistant", "content": previous.result["reply"]})
        added = sum(len(message["content"]) for message in pair)
        if size + added > 12000:
            break
        size += added
        history[0:0] = pair
    return history


def run_chat(job_id, *, session_factory, settings, worker_id=None, platform=None):
    owner = f"{worker_id or 'chat'}-{m.new_id()}"[:128]
    with session_factory() as session, session.begin():
        initial = session.get(m.Job, job_id)
        if not initial:
            fail("NOT_FOUND", status=404)
        lock_fleet(session, initial.tenant_id)
        job = refresh_locked(session, m.Job, job_id)
        if job.job_type != "CHAT":
            fail("INVALID_INPUT", status=422)
        if job.status != "QUEUED":
            return {"job_id": job.id, "job_status": job.status}
        user = session.get(m.User, job.user_id)
        turn = session.get(m.ChatTurn, job.input["turn_id"])
        if not user or not user.active or user.tenant_id != job.tenant_id:
            job.status, turn.status = "FAILED", "FAILED"
            job.error = DomainError("UNAUTHENTICATED", "User unavailable", 401).as_dict()
            conversation = session.get(m.Conversation, turn.conversation_id)
            conversation.active_turn_id = None
            return {"job_id": job.id, "job_status": job.status}
        job.status, turn.status, job.lease_owner = "RUNNING", "RUNNING", owner
        job.attempts += 1
        actor = Actor(
            user.id,
            user.tenant_id,
            user.role,
            user.vehicle_id,
            tuple(job.input["roots"]),
            turn.task_id,
            job.id,
            job.attempts,
            owner,
        )
        job.started_at, job.lease_expires_at = m.utcnow(), expires(75)
        fence, turn_id = job.attempts, turn.id
        token = create_task_token(
            task_id=turn.task_id,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            scopes=SCOPES,
            object_ids=list(actor.roots),
            key=settings.task_token_signing_key.get_secret_value(),
            lifetime_seconds=300,
            execution={"job_id": job.id, "attempt": job.attempts, "owner": owner},
        )
        # Recover accepted effects before exposing any context to another platform
        # attempt, including 0001's business-commit/tool-state-completion gap.
        recovered = _recorded_result(session_factory, actor, turn_id, existing_session=session)
        try:
            recovered = reconcile(recovered, actor, session_factory, existing_session=session)
        except DomainError as exc:
            # Access can change while a worker is down. Do not expose recovered
            # IDs to the platform or leave an unrecoverable turn queued forever.
            job.status = turn.status = "FAILED"
            job.error, job.completed_at = exc.as_dict(), m.utcnow()
            job.lease_owner, job.lease_expires_at = None, None
            conversation = session.get(m.Conversation, turn.conversation_id)
            if conversation.active_turn_id == turn_id:
                conversation.active_turn_id = None
            return {"job_id": job.id, "job_status": job.status}
        selection = dict(job.input["business_context"])
        if recovered.incident_id:
            selection["incident_id"] = recovered.incident_id
            job.incident_id = recovered.incident_id
        if recovered.job_ids:
            selection["job_ids"] = recovered.job_ids
        job.input = {**job.input, "business_context": selection}
        run_input = RunInput(
            input_text=turn.user_message,
            conversation_id=turn.conversation_id,
            turn_id=turn.id,
            trace_id=turn.trace_id,
            current_time=settings.simulation_time
            if settings.clock_mode == "SIMULATION"
            else m.utcnow(),
            locale="zh-CN",
            business_context={
                **{key: value for key, value in selection.items() if not key.startswith("_")},
                "clock_mode": settings.clock_mode,
            },
            history=_history(session, actor, turn, selection.get("_history_scope")),
            trusted_task_context={
                "task_token": token,
                "service_authorization": "Bearer " + settings.tool_service_key.get_secret_value(),
                "trace_id": turn.trace_id,
                **{
                    name + "_key": f"turn_{turn.id}_{name}"
                    for name in ("upsert_incident", "analyze_impact", "generate_recovery_plans")
                },
            },
        )
    gateway = ToolGateway(actor, turn_id, session_factory, settings, owner, fence)
    failure = None
    try:
        if platform is None:
            if settings.agent_mode == "local":
                platform = LocalSimulator()
            elif settings.agent_mode == "deepseek":
                platform = DeepSeekPlatform(settings)
            else:
                fail("DEPENDENCY_UNAVAILABLE", "Unknown agent mode", 503)
        raw = platform.invoke(run_input, gateway)
    except (httpx.HTTPError, DomainError, ValueError, KeyError, TypeError, IndexError):
        raw = _recorded_result(session_factory, actor, turn_id)
        if not raw.incident_id and not raw.job_ids:
            failure = DomainError(
                "DEPENDENCY_UNAVAILABLE",
                "Platform unavailable or invalid output",
                503,
                next_action="RETRY_SAME_KEY",
            ).as_dict()
    except Exception:  # noqa: BLE001 - sanitize unexpected platform failures
        raw = _recorded_result(session_factory, actor, turn_id)
        failure = DomainError("DEPENDENCY_UNAVAILABLE", "Platform invocation failed", 503).as_dict()
    try:
        # Merge durable writes even when the model omits their accepted IDs.
        recorded = _recorded_result(session_factory, actor, turn_id)
        raw = RunResult.model_validate(raw)
        raw.incident_id = (
            raw.incident_id or recorded.incident_id or run_input.business_context.get("incident_id")
        )
        raw.job_ids = list(dict.fromkeys(raw.job_ids + recorded.job_ids))
        checked = reconcile(raw, actor, session_factory)
        if settings.agent_mode == "local":
            checked.reply = "SIMULATION 本地规则模拟器：" + checked.reply
    except (DomainError, ValueError):
        checked = None
        failure = DomainError(
            "DEPENDENCY_UNAVAILABLE", "Platform result could not be authorized", 503
        ).as_dict()
        # A fabricated reference must not hide a real accepted write. Discard all
        # model IDs, then authorize only durable receipts using current DB access.
        recorded = _recorded_result(session_factory, actor, turn_id)
        if recorded.incident_id or recorded.job_ids:
            try:
                checked = reconcile(recorded, actor, session_factory)
                failure = None
            except (DomainError, ValueError):
                pass
    with session_factory() as session, session.begin():
        lock_fleet(session, actor.tenant_id)
        job = refresh_locked(session, m.Job, job_id)
        if job.status != "RUNNING" or job.lease_owner != owner or job.attempts != fence:
            return {"job_id": job.id, "job_status": job.status}
        if aware(job.lease_expires_at) <= m.utcnow():
            return {"job_id": job.id, "job_status": "RUNNING"}  # scanner owns recovery
        if checked and not failure:
            try:
                checked = reconcile(checked, actor, session_factory, existing_session=session)
                if settings.agent_mode == "local":
                    checked.reply = "SIMULATION 本地规则模拟器：" + checked.reply
            except DomainError:
                failure = DomainError(
                    "NOT_FOUND", "Business result no longer authorized", 404
                ).as_dict()
        turn = session.get(m.ChatTurn, turn_id)
        conversation = session.get(m.Conversation, turn.conversation_id)
        job.status = turn.status = "FAILED" if failure else "SUCCEEDED"
        job.error, job.completed_at = failure, m.utcnow()
        job.result = turn.result = (
            checked.model_dump(mode="json") if checked and not failure else None
        )
        if checked and checked.incident_id:
            job.incident_id = checked.incident_id
            conversation.incident_id = checked.incident_id
            conversation.context = {**conversation.context, "incident_id": checked.incident_id}
        if conversation.active_turn_id == turn_id:
            conversation.active_turn_id = None
        job.lease_owner, job.lease_expires_at = None, None
        audit(
            session,
            actor,
            "publish_chat",
            "TURN",
            turn_id,
            {"trace_id": turn.trace_id, "job_id": job.id, "status": job.status},
            job.operation_id,
        )
        logger.info(
            "trace_id=%s job_id=%s operation_id=%s status=%s",
            turn.trace_id,
            job.id,
            job.operation_id,
            job.status,
        )
        return {"job_id": job.id, "job_status": job.status}
