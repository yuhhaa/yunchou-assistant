import json
from pathlib import Path


def test_typed_five_tool_responses_match_actual_service(flow):
    from app.outputs import ContextOutput, Envelope, IncidentOutput, QueuedOutput, StatusOutput

    context_response = flow.get("/delivery-context")
    Envelope[ContextOutput].model_validate(context_response.json())
    incident = flow.post(
        "/incidents",
        {
            "mode": "CREATE",
            "event_type": "ROAD_DELAY",
            "vehicle_id": "V01",
            "description": "traffic",
            "delay_minutes": 20,
        },
    )
    Envelope[IncidentOutput].model_validate(incident.json())
    body = {
        "snapshot_id": context_response.json()["data"]["snapshot_id"],
        "incident_id": incident.json()["data"]["incident_id"],
        "expected_incident_version": 1,
    }
    queued = flow.post("/impact-analyses", body)
    Envelope[QueuedOutput].model_validate(queued.json())
    flow.app.state.business.run_job(queued.json()["data"]["job_id"])
    job = flow.get(
        "/status", params={"object_type": "JOB", "object_id": queued.json()["data"]["job_id"]}
    )
    Envelope[StatusOutput].model_validate(job.json())
    body["analysis_id"] = job.json()["data"]["result"]["analysis_id"]
    generated = flow.post("/recovery-plans", body)
    Envelope[QueuedOutput].model_validate(generated.json())
    flow.app.state.business.run_job(generated.json()["data"]["job_id"])
    candidates = flow.get(
        "/status", params={"object_type": "JOB", "object_id": generated.json()["data"]["job_id"]}
    )
    Envelope[StatusOutput].model_validate(candidates.json())
    error = flow.post("/impact-analyses", {**body, "unexpected": True})
    Envelope[QueuedOutput].model_validate(error.json())


def test_restricted_export_is_current_exact_and_typed(flow):
    from app.export_openapi import restricted

    document = flow.app.openapi()
    export = restricted(document)
    assert export["openapi"].startswith("3.1.")
    assert len(export["paths"]) == 5
    expected = {
        "get_delivery_context",
        "upsert_incident",
        "analyze_impact",
        "generate_recovery_plans",
        "get_operation_status",
    }
    assert {
        operation["operationId"] for path in export["paths"].values() for operation in path.values()
    } == expected
    for path in export["paths"].values():
        for operation in path.values():
            assert operation["security"] == [{"ToolServiceKey": [], "TaskContext": []}]
            assert operation["summary"] and operation["description"]
            for response in operation["responses"].values():
                assert response["content"]["application/json"]["schema"].get("$ref")
    assert "BrowserLogin" not in export["components"]["securitySchemes"]
    assert json.loads(Path("openapi.json").read_text(encoding="utf-8")) == document
    assert json.loads(Path("agent-tools.openapi.json").read_text(encoding="utf-8")) == export
