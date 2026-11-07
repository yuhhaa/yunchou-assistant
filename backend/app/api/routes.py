import hashlib

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from app import models as m
from app.api.dependencies import current_actor, idempotency_key, query_only
from app.schemas import (
    ApprovalInput,
    ChatInput,
    CompletionInput,
    HandoffInput,
    ImpactInput,
    IncidentInput,
    LoginInput,
    MapNodeInput,
    MapRefreshInput,
    PlanGenerationInput,
    ReceiptInput,
    RejectionInput,
    StatusInput,
    TaskTokenInput,
    WindowVerificationInput,
)
from app.security import create_access_token, create_task_token, verify_password
from app.services.business import Result
from app.services.common import SCOPES, Actor, expires, fail
from app.services.context import select_vehicle

router = APIRouter(prefix="/api/v1")
actor_dependency = Depends(current_actor)


@router.get("/health")
def health(request: Request):
    return respond(request, Result({"status": "ALIVE"}))


@router.get("/ready")
def ready(request: Request):
    from redis import Redis
    from sqlalchemy import text

    from app.platform import DeepSeekPlatform

    settings = request.app.state.settings
    try:
        if any(
            len(secret.get_secret_value()) < 32
            for secret in (
                settings.login_signing_key,
                settings.task_token_signing_key,
                settings.tool_service_key,
            )
        ):
            raise ValueError("Secrets unavailable")
        with request.app.state.session_factory() as session:
            if session.scalar(text("SELECT version_num FROM alembic_version")) != "0004":
                raise ValueError("Migration required")
        with Redis.from_url(
            settings.redis_url, socket_timeout=3, socket_connect_timeout=3
        ) as redis:
            redis.ping()
        if settings.agent_mode == "deepseek":
            DeepSeekPlatform(settings)
        elif settings.agent_mode != "local":
            raise ValueError("Invalid agent mode")
    except Exception:  # noqa: BLE001 - readiness returns no dependency/credential details
        return respond(
            request,
            Result(
                None,
                503,
                error={
                    "code": "DEPENDENCY_UNAVAILABLE",
                    "reason": "Service is not ready",
                    "missing_fields": [],
                    "retryable": True,
                    "next_action": "MANUAL_REVIEW",
                    "details": None,
                },
            ),
        )
    return respond(
        request,
        Result(
            {"status": "READY", "agent_mode": settings.agent_mode, "source": settings.clock_mode}
        ),
    )


def respond(request, result):
    from fastapi.responses import JSONResponse

    error = result.error
    return JSONResponse(
        status_code=result.status,
        content={
            "code": error["code"] if error else "ACCEPTED" if result.status == 202 else "OK",
            "message": error["reason"] if error else "Accepted" if result.status == 202 else "OK",
            "data": result.data,
            "error": error,
            "request_id": request.state.request_id,
            "operation_id": result.operation_id,
            "meta": {
                "contract_version": "1.0",
                "data_time": m.utcnow().isoformat(),
                "replayed": result.replayed,
            },
        },
    )


def write(request, actor, action, body, target_id=None):
    key = idempotency_key(request)
    if actor.task_id:
        from app.services.chat import tool_gateway

        gateway = tool_gateway(request.app, actor)
        if gateway:
            _data, result = gateway.call(
                action,
                body.model_dump(mode="json"),
                request_id=request.state.request_id,
                full_result=True,
                idempotency_key=key,
            )
            return respond(request, result)
    return respond(
        request,
        request.app.state.business.write(
            actor,
            action,
            body,
            key,
            target_id=target_id,
            request_id=request.state.request_id,
        ),
    )


@router.post("/chat", status_code=202)
def chat(request: Request, body: ChatInput, actor: Actor = actor_dependency):
    return write(request, actor, "chat", body)


@router.post("/auth/login")
def login(request: Request, body: LoginInput):
    settings = request.app.state.settings
    with request.app.state.session_factory() as session, session.begin():
        users = session.scalars(
            select(m.User).where(m.User.username == body.username, m.User.active.is_(True))
        ).all()
        if len(users) != 1 or not verify_password(body.password, users[0].password_hash):
            fail("UNAUTHENTICATED", "Invalid credentials", 401, next_action="LOGIN")
        user = users[0]
        token = create_access_token(
            user.id,
            user.tenant_id,
            settings.login_signing_key.get_secret_value(),
            lifetime_seconds=settings.access_token_seconds,
        )
        session.add(
            m.LoginSession(
                tenant_id=user.tenant_id,
                user_id=user.id,
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                expires_at=expires(settings.access_token_seconds),
            )
        )
        return respond(
            request,
            Result(
                {
                    "access_token": token,
                    "token_type": "Bearer",
                    "expires_in": settings.access_token_seconds,
                    "user": {
                        "user_id": user.id,
                        "username": user.username,
                        "role": user.role,
                        "vehicle_id": user.vehicle_id,
                    },
                }
            ),
        )


@router.post("/auth/task-token")
def task_token(request: Request, body: TaskTokenInput, actor: Actor = actor_dependency):
    settings = request.app.state.settings
    with request.app.state.session_factory() as session:
        if (
            actor.role == "DRIVER"
            and not actor.vehicle_id
            and not body.vehicle_id
            and not body.order_no
        ):
            roots = ["self_" + hashlib.sha256(actor.user_id.encode()).hexdigest()[:32]]
        else:
            vehicle = select_vehicle(
                session, actor, vehicle_id=body.vehicle_id, order_no=body.order_no
            )
            roots = [vehicle.id]
        task_id = m.new_id()
        token = create_task_token(
            task_id=task_id,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            scopes=SCOPES,
            object_ids=roots,
            key=settings.task_token_signing_key.get_secret_value(),
            lifetime_seconds=min(settings.task_token_seconds, 300),
        )
        return respond(
            request,
            Result(
                {
                    "task_id": task_id,
                    "task_token": token,
                    "expires_in": min(settings.task_token_seconds, 300),
                    "scopes": SCOPES,
                    "object_ids": roots,
                }
            ),
        )


@router.get("/delivery-context")
@router.get("/agent-tools/delivery-context", operation_id="get_delivery_context")
def delivery_context(
    request: Request,
    order_no: str | None = None,
    vehicle_id: str | None = None,
    actor: Actor = actor_dependency,
):
    query_only(request, {"order_no", "vehicle_id"})
    # Reuse strict identifier validation for both optional selector fields.
    TaskTokenInput(order_no=order_no, vehicle_id=vehicle_id)
    if actor.task_id:
        from app.services.chat import tool_gateway

        gateway = tool_gateway(request.app, actor)
        if gateway:
            return respond(
                request,
                Result(
                    gateway.call(
                        "get_delivery_context", {"order_no": order_no, "vehicle_id": vehicle_id}
                    )
                ),
            )
    return respond(
        request,
        request.app.state.business.read(actor, "context", order_no=order_no, vehicle_id=vehicle_id),
    )


@router.post("/incidents")
@router.post("/agent-tools/incidents", operation_id="upsert_incident")
def incident(request: Request, body: IncidentInput, actor: Actor = actor_dependency):
    return write(request, actor, "upsert_incident", body)


@router.post("/impact-analyses")
@router.post("/agent-tools/impact-analyses", operation_id="analyze_impact")
def impact(request: Request, body: ImpactInput, actor: Actor = actor_dependency):
    return write(request, actor, "analyze_impact", body)


@router.post("/recovery-plans")
@router.post("/agent-tools/recovery-plans", operation_id="generate_recovery_plans")
def plans(request: Request, body: PlanGenerationInput, actor: Actor = actor_dependency):
    return write(request, actor, "generate_recovery_plans", body)


@router.get("/status")
@router.get("/agent-tools/status", operation_id="get_operation_status")
def operation_status(
    request: Request, object_type: str, object_id: str, actor: Actor = actor_dependency
):
    query_only(request, {"object_type", "object_id"})
    body = StatusInput(object_type=object_type, object_id=object_id)
    if actor.task_id:
        from app.services.chat import tool_gateway

        gateway = tool_gateway(request.app, actor)
        if gateway:
            return respond(request, Result(gateway.call("get_operation_status", body.model_dump())))
    return respond(request, request.app.state.business.read(actor, "status", **body.model_dump()))


@router.get("/jobs/{job_id}")
def job(request: Request, job_id: str, actor: Actor = actor_dependency):
    return respond(request, request.app.state.business.read(actor, "job", job_id=job_id))


@router.get("/conversations")
def conversations(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
    actor: Actor = actor_dependency,
):
    query_only(request, {"limit", "offset"})
    if actor.task_id:
        fail("FORBIDDEN", status=403)
    with request.app.state.session_factory() as session:
        rows = session.scalars(
            select(m.Conversation)
            .where(
                m.Conversation.tenant_id == actor.tenant_id,
                m.Conversation.user_id == actor.user_id,
            )
            .order_by(m.Conversation.created_at.desc(), m.Conversation.id.desc())
            .offset(offset)
            .limit(limit + 1)
        ).all()
        return respond(
            request,
            Result(
                {
                    "items": [
                        {
                            "conversation_id": row.id,
                            "created_at": row.created_at.isoformat(),
                            "active_turn_id": row.active_turn_id,
                            "selected_order_no": row.context.get("selected_order_no"),
                            "incident_id": row.context.get("incident_id"),
                        }
                        for row in rows[:limit]
                    ],
                    "next_offset": offset + limit if len(rows) > limit else None,
                }
            ),
        )


@router.get("/conversations/{conversation_id}/messages")
def conversation_messages(
    request: Request,
    conversation_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
    actor: Actor = actor_dependency,
):
    query_only(request, {"limit", "offset"})
    if actor.task_id:
        fail("FORBIDDEN", status=403)
    with request.app.state.session_factory() as session:
        conversation = session.get(m.Conversation, conversation_id)
        if (
            not conversation
            or conversation.tenant_id != actor.tenant_id
            or conversation.user_id != actor.user_id
        ):
            fail("NOT_FOUND", status=404)
        rows = session.scalars(
            select(m.ChatTurn)
            .where(
                m.ChatTurn.conversation_id == conversation_id,
                m.ChatTurn.tenant_id == actor.tenant_id,
                m.ChatTurn.user_id == actor.user_id,
            )
            .order_by(m.ChatTurn.created_at, m.ChatTurn.id)
            .offset(offset)
            .limit(limit + 1)
        ).all()
        return respond(
            request,
            Result(
                {
                    "conversation_id": conversation_id,
                    "items": [
                        {
                            "turn_id": row.id,
                            "job_id": row.job_id,
                            "status": row.status,
                            "created_at": row.created_at.isoformat(),
                            "user_message": row.user_message,
                            "assistant_reply": row.result.get("reply") if row.result else None,
                        }
                        for row in rows[:limit]
                    ],
                    "next_offset": offset + limit if len(rows) > limit else None,
                }
            ),
        )


@router.get("/incidents")
def list_incidents(request: Request, actor: Actor = actor_dependency):
    return respond(request, request.app.state.business.read(actor, "incidents"))


@router.get("/incidents/{incident_id}")
def get_incident(request: Request, incident_id: str, actor: Actor = actor_dependency):
    result = request.app.state.business.read(
        actor, "status", object_type="INCIDENT", object_id=incident_id
    )
    return respond(request, Result(result.data["result"]))


@router.get("/plans/{plan_id}")
def get_plan(request: Request, plan_id: str, actor: Actor = actor_dependency):
    result = request.app.state.business.read(actor, "status", object_type="PLAN", object_id=plan_id)
    return respond(request, Result(result.data["result"]))


@router.get("/incidents/{incident_id}/workspace")
def incident_workspace(request: Request, incident_id: str, actor: Actor = actor_dependency):
    query_only(request, set())
    return respond(
        request,
        request.app.state.business.read(actor, "incident_workspace", incident_id=incident_id),
    )


@router.get("/notifications")
def notifications(request: Request, actor: Actor = actor_dependency):
    return respond(request, request.app.state.business.read(actor, "notifications"))


@router.get("/vehicles")
def vehicles(request: Request, actor: Actor = actor_dependency):
    return respond(request, request.app.state.business.read(actor, "fleet"))


@router.get("/map")
def map_view(request: Request, vehicle_id: str | None = None, actor: Actor = actor_dependency):
    query_only(request, {"vehicle_id"})
    TaskTokenInput(vehicle_id=vehicle_id)
    return respond(request, request.app.state.business.read(actor, "map", vehicle_id=vehicle_id))


@router.get("/map/demo")
def demo_map_view(
    request: Request,
    vehicle_id: str | None = None,
    plan_id: str | None = None,
    actor: Actor = actor_dependency,
):
    query_only(request, {"vehicle_id", "plan_id"})
    TaskTokenInput(vehicle_id=vehicle_id)
    if plan_id is not None:
        StatusInput(object_type="PLAN", object_id=plan_id)
        if vehicle_id is None:
            fail(
                "MISSING_FIELDS",
                "Candidate preview requires vehicle_id",
                422,
                missing_fields=["vehicle_id"],
            )
    return respond(
        request,
        request.app.state.business.read(actor, "demo_map", vehicle_id=vehicle_id, plan_id=plan_id),
    )


@router.put("/map/nodes/{node_id}")
def map_node(request: Request, node_id: str, body: MapNodeInput, actor: Actor = actor_dependency):
    return write(request, actor, "update_map_node", body, node_id)


@router.get("/map/geocode")
def map_geocode(
    request: Request,
    address: str = Query(min_length=1, max_length=200),
    city: str | None = Query(None, min_length=1, max_length=64),
    actor: Actor = actor_dependency,
):
    from app.services.maps import geocode

    query_only(request, {"address", "city"})
    if not address.strip() or (city is not None and not city.strip()):
        fail("INVALID_INPUT", "Address/city cannot be blank", 422)
    return respond(
        request,
        Result(
            geocode(
                actor,
                request.app.state.settings,
                request.app.state.session_factory,
                address.strip(),
                city,
            )
        ),
    )


@router.get("/map/route")
def map_route(
    request: Request, vehicle_id: str, plan_id: str | None = None, actor: Actor = actor_dependency
):
    from app.services.maps import route

    query_only(request, {"vehicle_id", "plan_id"})
    TaskTokenInput(vehicle_id=vehicle_id)
    if plan_id is not None:
        StatusInput(object_type="PLAN", object_id=plan_id)
    return respond(
        request,
        Result(
            route(
                actor,
                request.app.state.settings,
                request.app.state.session_factory,
                vehicle_id,
                plan_id,
            )
        ),
    )


@router.post("/map/routing/refresh", status_code=202)
def map_refresh(request: Request, body: MapRefreshInput, actor: Actor = actor_dependency):
    return write(request, actor, "refresh_map_routing", body)


@router.post("/plans/{plan_id}/approve")
def approve(request: Request, plan_id: str, body: ApprovalInput, actor: Actor = actor_dependency):
    return write(request, actor, "approve_plan", body, plan_id)


@router.post("/plans/{plan_id}/receipts")
def receipts(request: Request, plan_id: str, body: ReceiptInput, actor: Actor = actor_dependency):
    return write(request, actor, "plan_receipt", body, plan_id)


@router.post("/plans/{plan_id}/reject")
def reject(request: Request, plan_id: str, body: RejectionInput, actor: Actor = actor_dependency):
    return write(request, actor, "reject_plan", body, plan_id)


@router.post("/incidents/{incident_id}/verify-window")
def verify_window(
    request: Request,
    incident_id: str,
    body: WindowVerificationInput,
    actor: Actor = actor_dependency,
):
    return write(request, actor, "verify_window", body, incident_id)


@router.post("/incidents/{incident_id}/manual-handoff")
def handoff(
    request: Request, incident_id: str, body: HandoffInput, actor: Actor = actor_dependency
):
    return write(request, actor, "manual_handoff", body, incident_id)


@router.post("/stops/{stop_id}/completion")
def completion(
    request: Request, stop_id: str, body: CompletionInput, actor: Actor = actor_dependency
):
    return write(request, actor, "stop_completion", body, stop_id)
