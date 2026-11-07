import importlib
from datetime import UTC, datetime, timedelta

import pytest


def security():
    try:
        return importlib.import_module("app.security")
    except ModuleNotFoundError:
        pytest.fail("Security module not implemented")


def test_password_and_login_tokens():
    s = security()
    encoded = s.hash_password("correct-demo-password")
    assert encoded != "correct-demo-password"
    assert s.verify_password("correct-demo-password", encoded)
    assert not s.verify_password("incorrect", encoded)
    now = datetime.now(UTC)
    token = s.create_access_token(
        "U001", "TENANT1", "login-secret-32-characters-long-test", now=now
    )
    assert s.decode_access_token(token, "login-secret-32-characters-long-test")["sub"] == "U001"
    expired = s.create_access_token(
        "U001", "TENANT1", "login-secret-32-characters-long-test", now=now - timedelta(days=2)
    )
    with pytest.raises(s.SecurityError):
        s.decode_access_token(expired, "login-secret-32-characters-long-test")


def test_task_expiry_scope_objects_and_separate_token_kind():
    s = security()
    key = "task-secret-32-characters-long-test"
    now = datetime.now(UTC)
    token = s.create_task_token(
        task_id="TASK1",
        user_id="U001",
        tenant_id="TENANT1",
        scopes=["get_delivery_context"],
        object_ids=["V01"],
        key=key,
        now=now,
    )
    claims = s.decode_task_token(token, key, required_scope="get_delivery_context", object_id="V01")
    assert claims["tenant_id"] == "TENANT1"
    with pytest.raises(s.SecurityError):
        s.decode_task_token(token, key, required_scope="upsert_incident")
    with pytest.raises(s.SecurityError):
        s.decode_task_token(token, key, object_id="V02")
    with pytest.raises(s.SecurityError):
        s.decode_access_token(token, key)
    expired = s.create_task_token(
        task_id="TASK1",
        user_id="U001",
        tenant_id="TENANT1",
        scopes=["get_delivery_context"],
        object_ids=["V01"],
        key=key,
        now=now - timedelta(minutes=6),
    )
    with pytest.raises(s.SecurityError):
        s.decode_task_token(expired, key)


@pytest.mark.parametrize("boundary", ["issue", "decode"])
@pytest.mark.parametrize("field", ["scopes", "object_ids"])
@pytest.mark.parametrize(
    "value", [None, "not_upsert_incident_V010", 1, [], [1], [""], ["ok", None], {}]
)
def test_task_authorization_claims_are_nonempty_string_arrays(boundary, field, value):
    s = security()
    claims = {
        "task_id": "T1",
        "sub": "U1",
        "tenant_id": "TENANT1",
        "scopes": ["upsert_incident"],
        "object_ids": ["V01"],
    }
    claims[field] = value
    with pytest.raises(s.SecurityError):
        if boundary == "issue":
            s.create_task_token(
                task_id=claims["task_id"],
                user_id=claims["sub"],
                tenant_id=claims["tenant_id"],
                scopes=claims["scopes"],
                object_ids=claims["object_ids"],
                key=TASK_KEY,
            )
        else:
            s.decode_task_token(
                signed_task_claims(claims),
                TASK_KEY,
                required_scope="upsert_incident",
                object_id="V01",
            )


TASK_KEY = "task-secret-32-characters-long-test"


def signed_task_claims(claims):
    import jwt

    now = datetime.now(UTC)
    return jwt.encode(
        {
            **claims,
            "iss": "fleet-recovery",
            "aud": "task",
            "token_kind": "task",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        TASK_KEY,
        algorithm="HS256",
    )


@pytest.mark.parametrize("boundary", ["issue", "decode"])
@pytest.mark.parametrize("field", ["task_id", "sub", "tenant_id"])
@pytest.mark.parametrize("value", [None, 12, True, "", "bad identifier", []])
def test_task_identity_claims_are_strict_identifiers(boundary, field, value):
    s = security()
    claims = {
        "task_id": "T1",
        "sub": "U1",
        "tenant_id": "TENANT1",
        "scopes": ["upsert_incident"],
        "object_ids": ["V01"],
    }
    claims[field] = value
    with pytest.raises(s.SecurityError):
        if boundary == "issue":
            s.create_task_token(
                task_id=claims["task_id"],
                user_id=claims["sub"],
                tenant_id=claims["tenant_id"],
                scopes=claims["scopes"],
                object_ids=claims["object_ids"],
                key=TASK_KEY,
            )
        else:
            s.decode_task_token(signed_task_claims(claims), TASK_KEY)


def test_valid_task_claims_require_exact_membership():
    s = security()
    token = s.create_task_token(
        task_id="T1",
        user_id="U1",
        tenant_id="TENANT1",
        scopes=["not_upsert_incident"],
        object_ids=["V010"],
        key=TASK_KEY,
    )
    for kwargs in ({"required_scope": "upsert_incident"}, {"object_id": "V01"}):
        with pytest.raises(s.SecurityError):
            s.decode_task_token(token, TASK_KEY, **kwargs)
