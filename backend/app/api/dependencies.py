import hashlib

import jwt
from fastapi import Request

from app import models as m
from app.security import SecurityError, decode_access_token, decode_task_token, verify_service_key
from app.services.common import Actor, aware, fail


def current_actor(request: Request):
    settings = request.app.state.settings
    authorization = request.headers.get("authorization", "")
    if len(authorization) > 8192 or not authorization.startswith("Bearer "):
        fail("UNAUTHENTICATED", status=401, next_action="LOGIN")
    token = authorization.removeprefix("Bearer ")
    roots, task_id = None, None
    if request.url.path.startswith("/api/v1/agent-tools/"):
        if not verify_service_key(token, settings.tool_service_key.get_secret_value()):
            fail("UNAUTHENTICATED", status=401)
        task = request.headers.get("x-task-token", "")
        if len(task) > 8192:
            fail("UNAUTHENTICATED", status=401)
        try:
            claims = decode_task_token(
                task,
                settings.task_token_signing_key.get_secret_value(),
                required_scope=request.scope["route"].operation_id,
            )
        except SecurityError:
            # Only classify expiration after verifying signature/type/audience.
            try:
                checked = jwt.decode(
                    task,
                    settings.task_token_signing_key.get_secret_value(),
                    algorithms=["HS256"],
                    audience="task",
                    issuer="fleet-recovery",
                    options={
                        "verify_exp": False,
                        "require": ["exp", "sub", "tenant_id", "token_kind"],
                    },
                )
                if checked["token_kind"] == "task" and checked["exp"] <= m.utcnow().timestamp():
                    fail("TASK_CONTEXT_EXPIRED", status=401)
            except jwt.PyJWTError:
                pass
            fail("UNAUTHENTICATED", status=401)
        roots, task_id = tuple(claims["object_ids"]), claims["task_id"]
    else:
        try:
            claims = decode_access_token(token, settings.login_signing_key.get_secret_value())
        except SecurityError:
            fail("UNAUTHENTICATED", status=401, next_action="LOGIN")
    with request.app.state.session_factory() as session:
        user = session.get(m.User, claims["sub"])
        if not user or not user.active or user.tenant_id != claims["tenant_id"]:
            fail("UNAUTHENTICATED", status=401)
        if roots is None:
            from sqlalchemy import select

            login = session.scalar(
                select(m.LoginSession).where(
                    m.LoginSession.token_hash == hashlib.sha256(token.encode()).hexdigest()
                )
            )
            if not login or login.revoked_at or aware(login.expires_at) <= m.utcnow():
                fail("UNAUTHENTICATED", status=401, next_action="LOGIN")
        execution = claims.get("execution", {}) if roots is not None else {}
        return Actor(
            user.id,
            user.tenant_id,
            user.role,
            user.vehicle_id,
            roots,
            task_id,
            execution.get("job_id"),
            execution.get("attempt"),
            execution.get("owner"),
        )


def idempotency_key(request):
    import re

    key = request.headers.get("idempotency-key", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", key):
        fail("INVALID_INPUT", "Idempotency-Key must be 1-256 letters/digits/underscore/hyphen", 422)
    return key


def query_only(request, allowed):
    if set(request.query_params) - set(allowed) or any(
        len(request.query_params.getlist(k)) > 1 for k in request.query_params
    ):
        fail("INVALID_INPUT", "Unexpected or duplicate query fields", 422)
