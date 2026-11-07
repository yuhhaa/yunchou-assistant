"""Actual HTTP acceptance; print business IDs/statuses only, never credentials."""

import argparse
import time
from uuid import uuid4

import httpx
from dotenv import dotenv_values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--env-file", default="../.env")
    parser.add_argument("--chat", action="store_true")
    args = parser.parse_args()
    env = dotenv_values(args.env_file)
    with httpx.Client(
        base_url=args.base_url + "/api/v1",
        timeout=httpx.Timeout(10, connect=3),
        trust_env=False,
        limits=httpx.Limits(max_keepalive_connections=0),
    ) as client:

        def request(method, path, body=None, headers=None, **kwargs):
            response = client.request(method, path, json=body, headers=headers, **kwargs)
            content = response.json()
            if response.status_code >= 400:
                raise RuntimeError(f"HTTP {response.status_code} {content['code']}")
            return content["data"]

        def login(username):
            password_key = (
                "DEMO_DISPATCHER_PASSWORD" if username == "dispatcher" else "DEMO_DRIVER_PASSWORD"
            )
            data = request(
                "POST", "/auth/login", {"username": username, "password": env[password_key]}
            )
            return {"Authorization": "Bearer " + data["access_token"]}

        def post(path, body, headers):
            return request("POST", path, body, {**headers, "Idempotency-Key": uuid4().hex})

        driver, dispatcher = login("driver01"), login("dispatcher")
        token = post("/auth/task-token", {"order_no": "O001"}, driver)["task_token"]
        tool = {
            "Authorization": "Bearer " + env["TOOL_SERVICE_KEY"],
            "X-Task-Token": token,
            "X-Trace-ID": uuid4().hex,
        }
        context = request(
            "GET", "/agent-tools/delivery-context", headers=tool, params={"order_no": "O001"}
        )
        incident = post(
            "/agent-tools/incidents",
            {
                "mode": "CREATE",
                "event_type": "ROAD_DELAY",
                "vehicle_id": "V01",
                "description": "SIMULATION 仓库出口延误20分钟",
                "delay_minutes": 20,
            },
            tool,
        )
        body = {
            "incident_id": incident["incident_id"],
            "expected_incident_version": incident["incident_version"],
            "snapshot_id": context["snapshot_id"],
        }

        def poll(job_id, headers, tools=False):
            deadline = time.monotonic() + 90
            interval = 2
            while time.monotonic() < deadline:
                data = request(
                    "GET",
                    "/agent-tools/status" if tools else "/jobs/" + job_id,
                    headers=headers,
                    **({"params": {"object_type": "JOB", "object_id": job_id}} if tools else {}),
                )
                if data["job_status"] == "SUCCEEDED":
                    return data["result"]
                if data["job_status"] in {"FAILED", "UNKNOWN"}:
                    raise RuntimeError(
                        f"job_id={job_id} status={data['job_status']} code={data['error']['code']}"
                    )
                time.sleep(interval)
                interval = 5
            raise RuntimeError(
                f"job_id={job_id} polling deadline reached; recover same job, do not resubmit"
            )

        analysis = poll(post("/agent-tools/impact-analyses", body, tool)["job_id"], tool, True)
        assert (
            next(i for i in analysis["order_impacts"] if i["order_no"] == "O002")["delay_minutes"]
            == 15
        )
        generated = poll(
            post(
                "/agent-tools/recovery-plans",
                {**body, "analysis_id": analysis["analysis_id"]},
                tool,
            )["job_id"],
            tool,
            True,
        )
        plan = next(
            p
            for p in generated["plans"]
            if p["metrics"]["changed_order_count"] == 2
            and p["metrics"]["total_delay_minutes"] == 0
            and p["metrics"]["sequence_inversion_count"] == 1
        )
        assert (
            plan["metrics"]["total_distance_m"] == 20000
            and plan["metrics"]["reassigned_order_count"] == 0
        )
        approved = post(
            "/plans/" + plan["plan_id"] + "/approve",
            {
                "expected_plan_version": plan["plan_version"],
                "expected_incident_version": plan["incident_version"],
                "snapshot_id": plan["snapshot_id"],
            },
            dispatcher,
        )
        assert approved["status"] == "APPROVED_PENDING_ACK"
        activated = post(
            "/plans/" + plan["plan_id"] + "/receipts",
            {"expected_plan_version": approved["plan_version"], "decision": "ACCEPTED"},
            driver,
        )
        assert activated["status"] == "ACTIVE"
        print(
            "SIMULATION HTTP benchmark PASS: O002 delay=15; BAC delay=0, changed=2, reassigned=0, inversions=1, distance_m=20000; ACTIVE"
        )
        if args.chat:
            driver2 = login("driver02")
            first = post(
                "/chat", {"message": "预计延误20分钟", "selected_order_no": "O004"}, driver2
            )
            run = poll(first["job_id"], driver2)
            assert run["business_state"] == "PROCESSING"
            for jid in run["job_ids"]:
                poll(jid, driver2)
            second = post(
                "/chat", {"message": "继续", "conversation_id": first["conversation_id"]}, driver2
            )
            run = poll(second["job_id"], driver2)
            for jid in run["job_ids"]:
                poll(jid, driver2)
            third = post(
                "/chat",
                {"message": "查看结果", "conversation_id": first["conversation_id"]},
                driver2,
            )
            run = poll(third["job_id"], driver2)
            assert run["business_state"] == "WAITING_APPROVAL"
            assert run["reply"].startswith("SIMULATION")
            print("SIMULATION three-turn chat PASS: PROCESSING -> PROCESSING -> WAITING_APPROVAL")


if __name__ == "__main__":
    main()
