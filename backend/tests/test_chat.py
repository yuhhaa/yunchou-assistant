import pytest
from sqlalchemy import func, select

from app import models as m

REPORT = {
    "mode": "CREATE",
    "event_type": "ROAD_DELAY",
    "vehicle_id": "V01",
    "description": "traffic",
    "delay_minutes": 20,
}


@pytest.mark.parametrize("message", ["预计晚20分钟", "预计延时20分钟"])
def test_local_clarification_accepts_explicit_estimated_lateness(flow, message):
    from app.services.chat import run_chat

    first = flow.post("/chat", {"message": "堵车了"}).json()["data"]
    run_chat(first["job_id"], session_factory=flow.factory, settings=flow.app.state.settings)
    second = flow.post(
        "/chat", {"message": message, "conversation_id": first["conversation_id"]}
    ).json()["data"]
    run_chat(second["job_id"], session_factory=flow.factory, settings=flow.app.state.settings)
    job = flow.get("/jobs/" + second["job_id"]).json()["data"]
    assert job["result"]["business_state"] == "PROCESSING"
    with flow.factory() as session:
        incidents = session.scalars(select(m.Incident)).all()
        assert len(incidents) == 1 and incidents[0].delay_minutes == 20


def test_history_is_finite_owned_and_resets_on_selection_change(flow):
    from app.platform import result
    from app.services.chat import run_chat

    inputs = []

    class Capture:
        def invoke(self, run_input, gateway):
            inputs.append(run_input)
            return result()

    def turn(message, **selection):
        queued = flow.post("/chat", {"message": message, **selection}).json()["data"]
        run_chat(
            queued["job_id"],
            session_factory=flow.factory,
            settings=flow.app.state.settings,
            platform=Capture(),
        )
        return queued

    first = turn("道路堵车，时间待确认", selected_order_no="O001")
    turn("20分钟", conversation_id=first["conversation_id"])
    assert inputs[-1].history[0]["content"] == "道路堵车，时间待确认"
    turn("换一单", conversation_id=first["conversation_id"], selected_order_no="O002")
    assert inputs[-1].history == []
    turn("补充信息", conversation_id=first["conversation_id"], selected_order_no="O001")
    assert inputs[-1].history == []  # Switching back cannot revive an old event.
    listing = flow.get("/conversations?limit=1").json()["data"]
    assert listing["items"][0]["conversation_id"] == first["conversation_id"]
    messages = flow.get("/conversations/" + first["conversation_id"] + "/messages?limit=2")
    assert messages.status_code == 200
    assert len(messages.json()["data"]["items"]) == 2
    assert "tool_state" not in messages.text and "task_token" not in messages.text
    assert (
        flow.get(
            "/conversations/" + first["conversation_id"] + "/messages", actor="dispatcher"
        ).status_code
        == 404
    )
    assert flow.get("/conversations?limit=101").status_code == 422


def test_cors_uses_explicit_allowlist(flow):
    from fastapi.testclient import TestClient

    from app.main import create_app

    settings = flow.app.state.settings.model_copy(
        update={"cors_allow_origins": ["https://ui.test"]}
    )
    with TestClient(create_app(settings, flow.factory)) as client:
        headers = {
            "Origin": "https://ui.test",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,idempotency-key",
        }
        allowed = client.options("/api/v1/chat", headers=headers)
        assert allowed.headers["access-control-allow-origin"] == "https://ui.test"
        rejected = client.options("/api/v1/chat", headers={**headers, "Origin": "https://bad.test"})
        assert "access-control-allow-origin" not in rejected.headers


@pytest.mark.parametrize("invalid_output", [False, True, "unauthorized"])
def test_direct_deepseek_recovers_write_after_http_or_output_failure(flow, invalid_output):
    import json

    import httpx

    from app.platform import DeepSeekPlatform
    from app.services.chat import run_chat

    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "create1",
                                        "type": "function",
                                        "function": {
                                            "name": "upsert_incident",
                                            "arguments": json.dumps(REPORT),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        if invalid_output == "unauthorized":
            from app.platform import result

            value = result(plan_ids=["unknown-plan"]).model_dump_json()
            return httpx.Response(200, json={"choices": [{"message": {"content": value}}]})
        return (
            httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})
            if invalid_output
            else httpx.Response(503, json={"error": "unavailable"})
        )

    settings = flow.app.state.settings.model_copy(update={"agent_mode": "deepseek"})
    from pydantic import SecretStr

    settings.deepseek_api_key = SecretStr("private")
    queued = flow.post("/chat", {"message": "堵车20分钟", "selected_order_no": "O001"}).json()[
        "data"
    ]
    run_chat(
        queued["job_id"],
        session_factory=flow.factory,
        settings=settings,
        platform=DeepSeekPlatform(settings, transport=httpx.MockTransport(handle)),
    )
    job = flow.get("/jobs/" + queued["job_id"]).json()["data"]
    assert job["job_status"] == "SUCCEEDED" and job["result"]["incident_id"]
    assert job["result"]["business_state"] != "DONE"
    with flow.factory() as session:
        assert session.scalar(select(func.count()).select_from(m.Incident)) == 1


def test_internal_gateway_raises_persisted_business_error(flow, monkeypatch):
    from app.errors import DomainError
    from app.platform import result
    from app.services.business import Business, Result
    from app.services.chat import run_chat

    observed = []

    class Capture:
        def invoke(self, run_input, gateway):
            with monkeypatch.context() as scoped:
                scoped.setattr(
                    Business,
                    "write",
                    lambda *args, **kwargs: Result(
                        None, 409, error=DomainError("VERSION_CONFLICT", "Changed", 409).as_dict()
                    ),
                )
                with pytest.raises(DomainError) as caught:
                    gateway.call("upsert_incident", REPORT)
                observed.append(caught.value.code)
            return result()

    queued = flow.post("/chat", {"message": "堵车20分钟", "selected_order_no": "O001"}).json()[
        "data"
    ]
    run_chat(
        queued["job_id"],
        session_factory=flow.factory,
        settings=flow.app.state.settings,
        platform=Capture(),
    )
    assert observed == ["VERSION_CONFLICT"]


@pytest.mark.parametrize("crash_after_commit", [False, True])
def test_reclaimed_turn_recovers_create_before_rephrased_platform_retry(
    flow, monkeypatch, crash_after_commit
):
    from datetime import timedelta

    from app.errors import DomainError
    from app.maintenance import recover_jobs
    from app.platform import result
    from app.services.business import Business
    from app.services.chat import run_chat

    accepted, resumed, conflicts = [], [], []
    original_write = Business.write

    def lost_response(self, *args, **kwargs):
        outcome = original_write(self, *args, **kwargs)
        accepted.append(outcome.data)
        raise SystemExit("process lost after business commit")

    class CrashPlatform:
        def invoke(self, run_input, gateway):
            if crash_after_commit:
                monkeypatch.setattr(Business, "write", lost_response)
            accepted.append(gateway.call("upsert_incident", REPORT))
            raise SystemExit("process lost after gateway completion")

    class RetryPlatform:
        def invoke(self, run_input, gateway):
            resumed.append(run_input.business_context)
            try:
                gateway.call("upsert_incident", {**REPORT, "description": "traffic congestion"})
            except DomainError as exc:
                conflicts.append(exc.code)
            return result()

    data = flow.post("/chat", {"message": "堵车20分钟", "selected_order_no": "O001"}).json()["data"]
    with pytest.raises(SystemExit):
        run_chat(
            data["job_id"],
            session_factory=flow.factory,
            settings=flow.app.state.settings,
            platform=CrashPlatform(),
        )
    monkeypatch.setattr(Business, "write", original_write)
    with flow.factory() as session, session.begin():
        session.get(m.Job, data["job_id"]).lease_expires_at = m.utcnow() - timedelta(seconds=1)
    assert data["job_id"] in recover_jobs(flow.factory)
    run_chat(
        data["job_id"],
        session_factory=flow.factory,
        settings=flow.app.state.settings,
        platform=RetryPlatform(),
    )
    with flow.factory() as session:
        assert session.scalar(select(func.count()).select_from(m.Incident)) == 1
    assert resumed[0]["incident_id"] == accepted[0]["incident_id"]
    assert conflicts == ["IDEMPOTENCY_CONFLICT"]
    assert (
        flow.get("/jobs/" + data["job_id"]).json()["data"]["result"]["incident_id"]
        == accepted[0]["incident_id"]
    )


def test_chat_callback_preserves_public_keys_aliases_and_request_lookup(flow):
    from app.platform import result
    from app.services.chat import run_chat

    observed = []

    class CallbackPlatform:
        def invoke(self, run_input, gateway):
            headers = {
                "Authorization": "Bearer service-test",
                "X-Task-Token": run_input.trusted_task_context["task_token"].get_secret_value(),
                "Idempotency-Key": "same-callback-key",
            }
            url = "/api/v1/agent-tools/incidents"
            first = flow.client.post(url, json=REPORT, headers=headers)
            replay = flow.client.post(url, json=REPORT, headers=headers)
            changed = flow.client.post(url, json={**REPORT, "delay_minutes": 21}, headers=headers)
            alias = flow.client.post(
                url, json=REPORT, headers={**headers, "Idempotency-Key": "other-key"}
            )
            context = flow.context()
            analysis_body = {
                "incident_id": first.json()["data"]["incident_id"],
                "expected_incident_version": 1,
                "snapshot_id": context["snapshot_id"],
            }
            other_action = flow.client.post(
                "/api/v1/agent-tools/impact-analyses", json=analysis_body, headers=headers
            )
            lookup = flow.client.get(
                "/api/v1/agent-tools/status",
                params={"object_type": "REQUEST", "object_id": "same-callback-key"},
                headers=headers,
            )
            alias_lookup = flow.client.get(
                "/api/v1/agent-tools/status",
                params={"object_type": "REQUEST", "object_id": "other-key"},
                headers=headers,
            )
            operation = flow.client.get(
                "/api/v1/agent-tools/status",
                params={"object_type": "OPERATION", "object_id": first.json()["operation_id"]},
                headers=headers,
            )
            observed.extend(
                [first, replay, changed, alias, other_action, lookup, alias_lookup, operation]
            )
            ninth = flow.client.get(
                "/api/v1/agent-tools/status",
                params={"object_type": "REQUEST", "object_id": "same-callback-key"},
                headers=headers,
            )
            observed.append(ninth)
            return result()

    data = flow.post("/chat", {"message": "堵车20分钟", "selected_order_no": "O001"}).json()["data"]
    run_chat(
        data["job_id"],
        session_factory=flow.factory,
        settings=flow.app.state.settings,
        platform=CallbackPlatform(),
    )
    assert [r.status_code for r in observed] == [201, 201, 409, 201, 409, 200, 200, 200, 429]
    first, replay, changed, alias, other_action, *lookups = [r.json() for r in observed[:-1]]
    assert replay["meta"]["replayed"] and alias["meta"]["replayed"]
    assert first["operation_id"] == replay["operation_id"] == alias["operation_id"]
    assert changed["code"] == other_action["code"] == "IDEMPOTENCY_CONFLICT"
    assert all(x["data"]["result"]["operation_id"] == first["operation_id"] for x in lookups)
    # An alias reserves the same global tenant/user key namespace for ordinary APIs too.
    assert (
        flow.post("/incidents", {**REPORT, "delay_minutes": 22}, key="other-key").status_code == 409
    )
    assert flow.post("/incidents", REPORT, key="other-key").json()["meta"]["replayed"]


def test_distinct_protected_action_keys_allow_create_analysis_and_plans(flow):
    from app.platform import result
    from app.services.chat import run_chat

    observed = []

    class Pipeline:
        def invoke(self, run_input, gateway):
            context = gateway.call("get_delivery_context", {"vehicle_id": "V01"})
            headers = {
                "Authorization": "Bearer service-test",
                "X-Task-Token": run_input.trusted_task_context["task_token"].get_secret_value(),
            }

            def post(path, name, body):
                key = run_input.trusted_task_context[name + "_key"].get_secret_value()
                response = flow.client.post(
                    "/api/v1/agent-tools/" + path,
                    json=body,
                    headers={**headers, "Idempotency-Key": key},
                )
                observed.append((key, response.status_code))
                return response.json()["data"]

            incident = post("incidents", "upsert_incident", REPORT)
            body = {
                "incident_id": incident["incident_id"],
                "expected_incident_version": 1,
                "snapshot_id": context["snapshot_id"],
            }
            job = post("impact-analyses", "analyze_impact", body)
            flow.app.state.business.run_job(job["job_id"])
            analysis = flow.get("/jobs/" + job["job_id"]).json()["data"]["result"]
            plans = post(
                "recovery-plans",
                "generate_recovery_plans",
                {**body, "analysis_id": analysis["analysis_id"]},
            )
            return result(
                incident_id=incident["incident_id"], job_ids=[job["job_id"], plans["job_id"]]
            )

    data = flow.post("/chat", {"message": "堵车20分钟", "selected_order_no": "O001"}).json()["data"]
    run_chat(
        data["job_id"],
        session_factory=flow.factory,
        settings=flow.app.state.settings,
        platform=Pipeline(),
    )
    assert [code for _, code in observed] == [201, 202, 202]
    assert len({key for key, _ in observed}) == 3


def test_new_user_turn_allows_another_explicit_create(flow):
    from app.platform import result
    from app.services.chat import run_chat

    class ExplicitReport:
        def invoke(self, run_input, gateway):
            report = gateway.call("upsert_incident", REPORT)
            return result(incident_id=report["incident_id"])

    first = flow.post("/chat", {"message": "堵车20分钟", "selected_order_no": "O001"}).json()[
        "data"
    ]
    run_chat(
        first["job_id"],
        session_factory=flow.factory,
        settings=flow.app.state.settings,
        platform=ExplicitReport(),
    )
    second = flow.post(
        "/chat", {"message": "这是新的堵车事件20分钟", "conversation_id": first["conversation_id"]}
    ).json()["data"]
    run_chat(
        second["job_id"],
        session_factory=flow.factory,
        settings=flow.app.state.settings,
        platform=ExplicitReport(),
    )
    with flow.factory() as session:
        assert session.scalar(select(func.count()).select_from(m.Incident)) == 2


def test_reclaim_recovers_legacy_pending_keys_and_accepted_job_before_invoke(flow):
    from datetime import timedelta

    from app.errors import DomainError
    from app.maintenance import recover_jobs
    from app.platform import result
    from app.services.chat import run_chat

    accepted, contexts, conflicts = [], [], []

    class Crash:
        def invoke(self, run_input, gateway):
            context = gateway.call("get_delivery_context", {"vehicle_id": "V01"})
            incident = gateway.call("upsert_incident", REPORT)
            job = gateway.call(
                "analyze_impact",
                {
                    "incident_id": incident["incident_id"],
                    "expected_incident_version": 1,
                    "snapshot_id": context["snapshot_id"],
                },
            )
            accepted.extend([incident["incident_id"], job["job_id"]])
            # 0001 could commit the operation while only the hash/key reservation
            # existed. Simulate that durable pre-upgrade state for both writes.
            with flow.factory() as session, session.begin():
                turn = session.get(m.ChatTurn, run_input.turn_id)
                turn.tool_state = {
                    "legacy-" + str(index): {"name": call["name"], "key": call["key"]}
                    for index, call in enumerate(turn.tool_state.values())
                    if call["name"] != "get_delivery_context"
                }
            raise SystemExit("old worker lost after accepted computation")

    class Resume:
        def invoke(self, run_input, gateway):
            contexts.append(run_input.business_context)
            try:
                gateway.call("upsert_incident", {**REPORT, "description": "traffic congestion"})
            except DomainError as exc:
                conflicts.append(exc.code)
            return result()

    data = flow.post("/chat", {"message": "堵车20分钟", "selected_order_no": "O001"}).json()["data"]
    with pytest.raises(SystemExit):
        run_chat(
            data["job_id"],
            session_factory=flow.factory,
            settings=flow.app.state.settings,
            platform=Crash(),
        )
    with flow.factory() as session, session.begin():
        session.get(m.Job, data["job_id"]).lease_expires_at = m.utcnow() - timedelta(seconds=1)
    recover_jobs(flow.factory)
    run_chat(
        data["job_id"],
        session_factory=flow.factory,
        settings=flow.app.state.settings,
        platform=Resume(),
    )
    assert contexts[0]["incident_id"] == accepted[0]
    assert contexts[0]["job_ids"] == [accepted[1]]
    assert conflicts == ["IDEMPOTENCY_CONFLICT"]
    with flow.factory() as session:
        assert session.scalar(select(func.count()).select_from(m.Incident)) == 1
        assert (
            session.scalar(
                select(func.count()).select_from(m.Job).where(m.Job.job_type == "IMPACT_ANALYSIS")
            )
            == 1
        )


def test_new_callback_alias_retains_at_least_24_hours_from_its_acceptance(flow):
    from datetime import timedelta

    from app.platform import result
    from app.services.chat import run_chat

    expirations = []

    class Platform:
        def invoke(self, run_input, gateway):
            _, first = gateway.call(
                "upsert_incident", REPORT, idempotency_key="first", full_result=True
            )
            with flow.factory() as session, session.begin():
                session.get(m.Operation, first.operation_id).expires_at = m.utcnow() + timedelta(
                    hours=1
                )
            before = m.utcnow()
            gateway.call("upsert_incident", REPORT, idempotency_key="later-alias")
            with flow.factory() as session:
                expirations.extend(
                    [before, session.get(m.Operation, first.operation_id).expires_at]
                )
            return result()

    data = flow.post("/chat", {"message": "堵车20分钟", "selected_order_no": "O001"}).json()["data"]
    run_chat(
        data["job_id"],
        session_factory=flow.factory,
        settings=flow.app.state.settings,
        platform=Platform(),
    )
    assert expirations[1] >= expirations[0] + timedelta(hours=24)


def test_recovery_with_revoked_business_access_fails_without_platform_invocation(flow):
    from datetime import timedelta

    from app.maintenance import recover_jobs
    from app.services.chat import run_chat

    invoked = []

    class Crash:
        def invoke(self, run_input, gateway):
            gateway.call("upsert_incident", REPORT)
            raise SystemExit("process lost")

    class ShouldNotInvoke:
        def invoke(self, run_input, gateway):
            invoked.append(run_input.business_context)

    data = flow.post("/chat", {"message": "堵车20分钟", "selected_order_no": "O001"}).json()["data"]
    with pytest.raises(SystemExit):
        run_chat(
            data["job_id"],
            session_factory=flow.factory,
            settings=flow.app.state.settings,
            platform=Crash(),
        )
    with flow.factory() as session, session.begin():
        session.get(m.Job, data["job_id"]).lease_expires_at = m.utcnow() - timedelta(seconds=1)
        session.get(m.Vehicle, "V01").driver_id = "DRIVER02"
    recover_jobs(flow.factory)
    outcome = run_chat(
        data["job_id"],
        session_factory=flow.factory,
        settings=flow.app.state.settings,
        platform=ShouldNotInvoke(),
    )
    assert outcome["job_status"] == "FAILED"
    assert invoked == []


def test_chat_accepts_replays_and_enforces_conversation_owner_and_busy(flow):
    body = {"message": " 查任务 ", "selected_order_no": "O001"}
    first = flow.post("/chat", body, key="chat-one")
    assert first.status_code == 202, first.text
    data = first.json()["data"]
    assert flow.post("/chat", body, key="chat-one").json()["data"] == data
    assert (
        flow.post("/chat", {**body, "conversation_id": data["conversation_id"]}).status_code == 409
    )
    assert (
        flow.post(
            "/chat",
            {"message": "查任务", "conversation_id": data["conversation_id"]},
            actor="driver02",
        ).status_code
        == 404
    )
    assert flow.post("/chat", {**body, "message": "different"}, key="chat-one").status_code == 409


def test_chat_strict_inputs_and_selected_scope(flow):
    assert flow.post("/chat", {"message": "   "}).status_code == 422
    assert flow.post("/chat", {"message": "hello", "role": "DISPATCHER"}).status_code == 422
    assert flow.post("/chat", {"message": "hello", "selected_order_no": "O004"}).status_code == 404


def test_local_chat_uses_business_writes_and_survives_duplicate_execution(flow):
    from app.services.chat import run_chat

    data = flow.post(
        "/chat", {"message": "堵车预计延误20分钟", "selected_order_no": "O001"}
    ).json()["data"]
    run_chat(data["job_id"], session_factory=flow.factory, settings=flow.app.state.settings)
    run_chat(data["job_id"], session_factory=flow.factory, settings=flow.app.state.settings)
    job = flow.get("/jobs/" + data["job_id"]).json()["data"]
    assert job["result_kind"] == "CHAT" and job["job_status"] == "SUCCEEDED"
    assert job["result"]["business_state"] == "PROCESSING"
    assert "SIMULATION" in job["result"]["reply"]
    assert len(job["result"]["job_ids"]) == 1
    with flow.factory() as session:
        assert session.scalar(select(func.count()).select_from(m.Incident)) == 1
        turn = session.get(m.ChatTurn, data["turn_id"])
        assert turn.tool_call_count <= 8
        assert turn.trace_id and turn.task_id == turn.id
    token = flow.post("/auth/task-token", {"order_no": "O001"}).json()["data"]["task_token"]
    response = flow.client.get(
        "/api/v1/agent-tools/status",
        params={
            "object_type": "JOB",
            "object_id": data["job_id"],
        },
        headers={"Authorization": "Bearer service-test", "X-Task-Token": token},
    )
    assert response.status_code == 404


def test_platform_timeout_reconciles_existing_write_without_repeating_report(flow):
    import httpx

    from app.services.chat import run_chat

    class TimeoutPlatform:
        def invoke(self, run_input, gateway):
            gateway.call(
                "upsert_incident",
                {
                    "mode": "CREATE",
                    "event_type": "ROAD_DELAY",
                    "vehicle_id": "V01",
                    "description": "traffic",
                    "delay_minutes": 20,
                },
            )
            raise httpx.ReadTimeout("timeout")

    data = flow.post("/chat", {"message": "堵车20分钟", "selected_order_no": "O001"}).json()["data"]
    run_chat(
        data["job_id"],
        session_factory=flow.factory,
        settings=flow.app.state.settings,
        platform=TimeoutPlatform(),
    )
    result = flow.get("/jobs/" + data["job_id"]).json()["data"]
    assert result["job_status"] == "SUCCEEDED"
    assert result["result"]["incident_id"]
    assert result["result"]["business_state"] != "DONE"
    assert result["result"]["next_action"] == "ANALYZE_IMPACT"
    with flow.factory() as session:
        assert session.scalar(select(func.count()).select_from(m.Incident)) == 1


def test_old_platform_callback_cannot_write_after_new_attempt_claim(flow):
    from datetime import timedelta

    from app.platform import result
    from app.services.chat import run_chat

    observed = []

    class OldPlatform:
        def invoke(self, run_input, gateway):
            observed.append(run_input.current_time.isoformat())
            with flow.factory() as session, session.begin():
                turn = session.get(m.ChatTurn, run_input.turn_id)
                job = session.get(m.Job, turn.job_id)
                job.attempts += 2
                job.lease_owner = "replacement-worker"
                job.lease_expires_at = m.utcnow() + timedelta(seconds=60)
            response = flow.client.post(
                "/api/v1/agent-tools/incidents",
                json={
                    "mode": "CREATE",
                    "event_type": "ROAD_DELAY",
                    "vehicle_id": "V01",
                    "description": "stale changed report",
                    "delay_minutes": 99,
                },
                headers={
                    "Authorization": "Bearer service-test",
                    "Idempotency-Key": "stale-callback",
                    "X-Task-Token": run_input.trusted_task_context["task_token"].get_secret_value(),
                },
            )
            observed.append(response.status_code)
            return result()

    data = flow.post("/chat", {"message": "hello", "selected_order_no": "O001"}).json()["data"]
    run_chat(
        data["job_id"],
        session_factory=flow.factory,
        settings=flow.app.state.settings,
        platform=OldPlatform(),
    )
    assert observed == [flow.app.state.settings.simulation_time, 504]
    with flow.factory() as session:
        assert session.scalar(select(func.count()).select_from(m.Incident)) == 0
