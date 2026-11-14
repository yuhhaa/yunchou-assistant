"""Validate DeepSeek -> ToolGateway -> isolated database, without Docker.

--mock checks protocol and real business writes, NOT real model understanding.
--live calls the configured model and can incur API charges. Never uses a shared DB.
"""

import argparse
import json
import secrets
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from app import models as m
from app.config import Settings
from app.db import Base, make_engine, make_session_factory
from app.main import create_app
from app.platform import DeepSeekPlatform, result
from app.seed import seed_demo
from app.services.chat import run_chat


class ScriptedModel:
    """Protocol fixture; only provider responses are substituted."""

    def __call__(self, request):
        payload = json.loads(request.content)
        messages = payload["messages"]
        user_index = max(i for i, msg in enumerate(messages) if msg["role"] == "user")
        message = messages[user_index]["content"]
        current_tools = [msg for msg in messages[user_index + 1 :] if msg["role"] == "tool"]
        outcomes = [json.loads(msg["content"]) for msg in current_tools]
        if any(not item["ok"] for item in outcomes):
            raise ValueError("Mock fixture encountered a real business error")
        data = [item["data"] for item in outcomes]
        call_name, args = None, None
        if not data:
            call_name, args = "get_delivery_context", {"order_no": "O001"}
        elif "20" not in message:
            response = result()
            response.reply = "请提供预计延误分钟数。"
        elif len(data) == 1:
            call_name, args = (
                "upsert_incident",
                {
                    "mode": "CREATE",
                    "event_type": "ROAD_DELAY",
                    "vehicle_id": "V01",
                    "order_no": "O001",
                    "description": "SIMULATION traffic delay",
                    "delay_minutes": 20,
                },
            )
        elif len(data) == 2:
            call_name, args = (
                "analyze_impact",
                {
                    "incident_id": data[1]["incident_id"],
                    "expected_incident_version": data[1]["incident_version"],
                    "snapshot_id": data[0]["snapshot_id"],
                },
            )
        else:
            response = result("PROCESSING", "POLL", data[1]["incident_id"], [data[2]["job_id"]])
        if call_name:
            output = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_" + uuid4().hex,
                        "type": "function",
                        "function": {
                            "name": call_name,
                            "arguments": json.dumps(args),
                        },
                    }
                ],
            }
        else:
            output = {"role": "assistant", "content": response.model_dump_json()}
        return httpx.Response(200, json={"choices": [{"message": output}]})


def validate(*, live=False, env_file=None, full=False, delay=20):
    settings = Settings(_env_file=env_file, database_url="sqlite://", agent_mode="deepseek")
    if live:
        if not settings.deepseek_api_key.get_secret_value():
            raise ValueError("DEEPSEEK_API_KEY is missing; no request was sent")
    else:
        settings.deepseek_api_key = SecretStr("mock-only-not-a-real-key")
    settings.login_signing_key = SecretStr(secrets.token_urlsafe(40))
    settings.task_token_signing_key = SecretStr(secrets.token_urlsafe(40))
    settings.tool_service_key = SecretStr(secrets.token_urlsafe(40))
    platform = DeepSeekPlatform(
        settings, transport=None if live else httpx.MockTransport(ScriptedModel())
    )
    engine = make_engine("sqlite://")
    factory = make_session_factory(engine)
    try:
        Base.metadata.create_all(engine)
        password = secrets.token_urlsafe(24)
        with factory() as session:
            seed_demo(session, dispatcher_password=password, driver_password=password)
        application = create_app(settings, factory)
        report = {
            "mode": "LIVE_MODEL" if live else "MOCK_MODEL",
            "database": "isolated in-memory SQLite",
            "business_source": "SIMULATION",
            "queue": "run_chat executed synchronously; Redis/Celery NOT tested",
            "model": settings.deepseek_model if live else "scripted protocol fixture",
            "turns": [],
            "tool_calls": [],
        }
        with TestClient(application) as client:
            login = client.post(
                "/api/v1/auth/login", json={"username": "driver01", "password": password}
            )
            login.raise_for_status()
            token = login.json()["data"]["access_token"]
            headers = {"Authorization": "Bearer " + token}
            conversation_id = None
            for text in ("堵车了", f"预计晚{delay}分钟，请登记异常并开始影响分析"):
                body = {"message": text, "selected_order_no": "O001"}
                if conversation_id:
                    body["conversation_id"] = conversation_id
                response = client.post(
                    "/api/v1/chat",
                    json=body,
                    headers={
                        **headers,
                        "Idempotency-Key": uuid4().hex,
                    },
                )
                response.raise_for_status()
                accepted = response.json()["data"]
                conversation_id = accepted["conversation_id"]
                run_chat(
                    accepted["job_id"],
                    session_factory=factory,
                    settings=settings,
                    platform=platform,
                )
                output = client.get("/api/v1/jobs/" + accepted["job_id"], headers=headers)
                output.raise_for_status()
                job = output.json()["data"]
                report["turns"].append(
                    {"input": text, "conversation_id": conversation_id, "job": job}
                )
                with factory() as session:
                    turn = session.get(m.ChatTurn, accepted["turn_id"])
                    report["tool_calls"].append([item["name"] for item in turn.tool_state.values()])
                if job["job_status"] != "SUCCEEDED":
                    report["passed"] = False
                    return report
                if len(report["turns"]) == 1:
                    with factory() as session:
                        if session.scalars(select(m.Incident)).all():
                            report["passed"] = False
                            report["failure"] = (
                                "First incomplete report unexpectedly created an incident"
                            )
                            return report
            with factory() as session:
                incidents = session.scalars(select(m.Incident)).all()
                jobs = session.scalars(
                    select(m.Job).where(m.Job.job_type == "IMPACT_ANALYSIS")
                ).all()
                report["incident_count"] = len(incidents)
                report["analysis_job_count"] = len(jobs)
                report["passed"] = (
                    len(incidents) == 1 and incidents[0].delay_minutes == delay and len(jobs) == 1
                )
            if full and report["passed"]:
                try:
                    finish_lifecycle(
                        client,
                        headers,
                        password,
                        conversation_id,
                        application,
                        factory,
                        settings,
                        platform,
                        report,
                    )
                except Exception as exc:  # noqa: BLE001 - preserve report without leaking provider errors
                    report["passed"] = False
                    report["failure_type"] = type(exc).__name__
            return report
    finally:
        engine.dispose()


def finish_lifecycle(
    client, driver, password, conversation_id, app, factory, settings, platform, report
):
    """Run computations and human actions with latest IDs, never fixed plan IDs."""
    report["steps"] = []

    def request(method, path, body=None, headers=driver, expected=200):
        h = dict(headers)
        if method != "GET":
            h["Idempotency-Key"] = uuid4().hex
        response = client.request(method, "/api/v1" + path, json=body, headers=h)
        if response.status_code != expected:
            raise RuntimeError(f"{method} {path}: HTTP {response.status_code}")
        return response.json()["data"]

    def compute():
        with factory() as session:
            queued = [
                j.id
                for j in session.scalars(
                    select(m.Job).where(m.Job.job_type != "CHAT", m.Job.status == "QUEUED")
                )
            ]
        for jid in queued:
            app.state.business.run_job(jid)
            job = request("GET", "/jobs/" + jid)
            report["steps"].append({"compute": job})
            if job["job_status"] != "SUCCEEDED":
                raise RuntimeError("Computation failed")

    def chat(text):
        queued = request(
            "POST", "/chat", {"message": text, "conversation_id": conversation_id}, expected=202
        )
        run_chat(queued["job_id"], session_factory=factory, settings=settings, platform=platform)
        job = request("GET", "/jobs/" + queued["job_id"])
        report["turns"].append({"input": text, "conversation_id": conversation_id, "job": job})
        with factory() as session:
            turn = session.get(m.ChatTurn, queued["turn_id"])
            report["tool_calls"].append([item["name"] for item in turn.tool_state.values()])
        if job["job_status"] != "SUCCEEDED":
            raise RuntimeError("Model continuation failed")
        return job["result"]

    compute()
    chat("继续。请查询已有分析任务的结果，使用当前有效分析生成恢复候选，不要新建异常或重复分析。")
    compute()
    with factory() as session:
        plans = session.scalars(
            select(m.Plan).where(m.Plan.status.in_(["PENDING_APPROVAL", "APPROVED_PENDING_ACK"]))
        ).all()
        if not plans:
            report["passed"] = False
            report["failure"] = "Continuation did not generate candidate plans"
            return
        incident_id = plans[0].incident_id
    login = client.post("/api/v1/auth/login", json={"username": "dispatcher", "password": password})
    login.raise_for_status()
    dispatcher = {"Authorization": "Bearer " + login.json()["data"]["access_token"]}
    workspace = request("GET", f"/incidents/{incident_id}/workspace", headers=dispatcher)
    approved_plans = [p for p in workspace["plans"] if p["status"] == "APPROVED_PENDING_ACK"]
    plans = approved_plans or [p for p in workspace["plans"] if p["status"] == "PENDING_APPROVAL"]
    # Prefer the policy-approved candidate; otherwise simulate explicit human choice.
    plan = min(
        plans,
        key=lambda p: (p["metrics"]["total_delay_minutes"], p["metrics"]["changed_order_count"]),
    )
    if approved_plans:
        if plan.get("approval_mode") != "AUTOMATIC_RULE":
            raise RuntimeError("Unexpected approval source in isolated fixture")
        approved = plan
        report["steps"].append({"automatic_approval": approved})
    else:
        approved = approve_manually(client, driver, plan, dispatcher, request, report)
    if approved["status"] != "APPROVED_PENDING_ACK":
        raise RuntimeError("Approval must wait for driver")
    with factory() as session:
        if session.get(m.Vehicle, "V01").route_revision != 1:
            raise RuntimeError("Route changed before driver acceptance")
    accepted = request(
        "POST",
        f"/plans/{plan['plan_id']}/receipts",
        {"expected_plan_version": approved["plan_version"], "decision": "ACCEPTED"},
    )
    if accepted["status"] != "ACTIVE":
        raise RuntimeError("Driver acceptance did not activate plan")
    report["steps"].append({"driver_acceptance": accepted})
    finish_deliveries(request, settings, chat, factory, incident_id, report)


def approve_manually(client, driver, plan, dispatcher, request, report):
    premature = client.post(
        "/api/v1/plans/" + plan["plan_id"] + "/receipts",
        json={"decision": "ACCEPTED", "expected_plan_version": plan["plan_version"]},
        headers={**driver, "Idempotency-Key": uuid4().hex},
    )
    if premature.status_code < 400:
        raise RuntimeError("Plan activated without dispatcher approval")
    report["steps"].append({"human_choice": plan, "premature_receipt_http": premature.status_code})
    approved = request(
        "POST",
        f"/plans/{plan['plan_id']}/approve",
        {
            "expected_plan_version": plan["plan_version"],
            "expected_incident_version": plan["incident_version"],
            "snapshot_id": plan["snapshot_id"],
        },
        dispatcher,
    )
    if approved["status"] != "APPROVED_PENDING_ACK":
        raise RuntimeError("Approval must wait for driver")
    report["steps"].append({"approval": approved})
    return approved


def finish_deliveries(request, settings, chat, factory, incident_id, report):
    # Arrival and completion are separate human actions, in the actual active order.
    for _ in range(30):
        context = request("GET", "/delivery-context?order_no=O001")
        remaining = sorted(
            [
                s
                for s in context["route_stops"]
                if s["vehicle_id"] == "V01" and s["status"] not in {"COMPLETED", "CANCELLED"}
            ],
            key=lambda s: s["sequence_no"],
        )
        if not remaining:
            break
        stop = remaining[0]
        revision = next(
            v["route_revision"] for v in context["vehicles"] if v["vehicle_id"] == "V01"
        )
        event = "COMPLETED" if stop["status"] == "ARRIVED" else "ARRIVED"
        receipt = request(
            "POST",
            f"/stops/{stop['stop_id']}/completion",
            {
                "event_type": event,
                "expected_route_revision": revision,
                "occurred_at": settings.simulation_time,
            },
        )
        report["steps"].append({"stop_receipt": receipt})
    final = chat("查看当前事件处理结果，查询实际配送回执与事件状态，不要新建或修改异常。")
    with factory() as session:
        incident = session.get(m.Incident, incident_id)
        report["incident_count"] = len(session.scalars(select(m.Incident)).all())
        report["final_incident_status"] = incident.status
    report["passed"] = (
        final["business_state"] == "DONE"
        and report["final_incident_status"] == "RESOLVED"
        and report["incident_count"] == 1
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--mock", action="store_true")
    mode.add_argument("--live", action="store_true")
    parser.add_argument(
        "--env-file", type=Path, help="Private configuration; secrets never printed"
    )
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--full",
        action="store_true",
        help="Validate computations, human approval, ACK and deliveries",
    )
    parser.add_argument("--delay", type=int, default=20, choices=range(1, 1441), metavar="MINUTES")
    args = parser.parse_args()
    try:
        if args.full and not args.live:
            parser.error("--full currently requires --live; the mock fixture covers only two turns")
        report = validate(live=args.live, env_file=args.env_file, full=args.full, delay=args.delay)
    except Exception as exc:  # noqa: BLE001 - CLI must not print credentials or provider response bodies
        # Provider response bodies, request headers and config values are not printed.
        report = {
            "passed": False,
            "mode": "LIVE_MODEL" if args.live else "MOCK_MODEL",
            "error_type": type(exc).__name__,
            "hint": "Check dependencies, private model configuration and model access. No secret is printed.",
        }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
