import re
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()


class SecurityError(ValueError):
    pass


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise SecurityError("Claims require nonempty string identifiers")


def _task_claims(claims):
    for field in ("task_id", "sub", "tenant_id"):
        _identifier(claims.get(field))
    for field in ("scopes", "object_ids"):
        values = claims.get(field)
        if not isinstance(values, list) or not values:
            raise SecurityError("Task scopes and objects require nonempty string arrays")
        for value in values:
            _identifier(value)
    execution = claims.get("execution")
    if execution is not None:
        if (
            not isinstance(execution, dict)
            or set(execution) != {"job_id", "attempt", "owner"}
            or type(execution["attempt"]) is not int
            or execution["attempt"] < 1
            or not isinstance(execution["owner"], str)
            or not 1 <= len(execution["owner"]) <= 128
        ):
            raise SecurityError("Invalid execution fence")
        _identifier(execution["job_id"])


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError("Password must contain at least 8 characters")
    return _hasher.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    try:
        return _hasher.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def _encode(claims, key, kind, seconds, now):
    _identifier(claims.get("sub"))
    _identifier(claims.get("tenant_id"))
    if not key or len(key) < 32:
        raise SecurityError("Signing key must contain at least 32 characters")
    now = now or datetime.now(UTC)
    return jwt.encode(
        {
            **claims,
            "jti": uuid4().hex,
            "token_kind": kind,
            "iat": now,
            "exp": now + timedelta(seconds=seconds),
            "iss": "fleet-recovery",
            "aud": kind,
        },
        key,
        algorithm="HS256",
    )


def _decode(token, key, kind):
    if not key or len(key) < 32:
        raise SecurityError("Signing key must contain at least 32 characters")
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=["HS256"],
            audience=kind,
            issuer="fleet-recovery",
            options={"require": ["exp", "iat", "sub", "tenant_id", "token_kind"]},
        )
        if claims["token_kind"] != kind:
            raise SecurityError("Invalid token kind")
        _identifier(claims["sub"])
        _identifier(claims["tenant_id"])
        return claims
    except jwt.PyJWTError as exc:
        raise SecurityError("Invalid or expired token") from exc


def create_access_token(user_id, tenant_id, key, *, now=None, lifetime_seconds=3600):
    return _encode({"sub": user_id, "tenant_id": tenant_id}, key, "access", lifetime_seconds, now)


def decode_access_token(token, key):
    return _decode(token, key, "access")


def create_task_token(
    *,
    task_id,
    user_id,
    tenant_id,
    scopes,
    object_ids,
    key,
    now=None,
    lifetime_seconds=300,
    execution=None,
):
    if not 0 < lifetime_seconds <= 300:
        raise SecurityError("Task lifetime must be between 1 and 300 seconds")
    claims = {
        "task_id": task_id,
        "sub": user_id,
        "tenant_id": tenant_id,
        "scopes": scopes,
        "object_ids": object_ids,
    }
    if execution is not None:
        claims["execution"] = execution
    _task_claims(claims)
    return _encode(claims, key, "task", lifetime_seconds, now)


def decode_task_token(token, key, *, required_scope=None, object_id=None):
    claims = _decode(token, key, "task")
    _task_claims(claims)
    for value, field in ((required_scope, "scopes"), (object_id, "object_ids")):
        if value is not None:
            _identifier(value)
            if value not in claims[field]:
                raise SecurityError("Scope or object denied")
    return claims


def verify_service_key(supplied: str, expected: str) -> bool:
    return bool(expected) and secrets.compare_digest(supplied, expected)
