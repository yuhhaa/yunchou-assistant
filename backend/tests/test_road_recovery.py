from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr

from app import models as m
from app.maps import AMapClient


@pytest.mark.parametrize("provider_crosses_area", [False, True])
def test_recovery_from_driver_location_uses_verified_detour(
    flow, monkeypatch, provider_crosses_area
):
    origin = [114.30, 30.50]
    now = datetime.now(UTC).isoformat()
    with flow.factory() as session, session.begin():
        for index, node in enumerate(("A", "B", "C")):
            point = session.get(m.Node, node)
            point.longitude, point.latitude = 114.32 + index / 100, 30.50
            point.coordinate_system = "GCJ02"

    requested = []

    def route_provider(request):
        params = request.url.params
        if request.url.path == "/v3/assistant/coordinate/convert":
            assert params["coordsys"] == "gps"
            assert params["locations"] == "114.295000,30.502000"
            return httpx.Response(200, json={"status": "1", "locations": "114.300000,30.500000"})
        start = [float(x) for x in params["origin"].split(",")]
        end = [float(x) for x in params["destination"].split(",")]
        avoiding = "avoidpolygons" in params
        requested.append((start, avoiding))
        points = (
            [start, [start[0], 30.51], [end[0], 30.51], end]
            if avoiding and not provider_crosses_area
            else [start, end]
        )
        return httpx.Response(
            200,
            json={
                "status": "1",
                "route": {
                    "paths": [
                        {
                            "distance": "1600" if avoiding else "1000",
                            "cost": {"duration": "180"},
                            "steps": [
                                {"polyline": ";".join(",".join(str(x) for x in p) for p in points)}
                            ],
                        }
                    ]
                },
            },
        )

    original = AMapClient.__init__
    flow.app.state.settings.amap_web_service_key = SecretStr("test-only")
    flow.app.state.settings.amap_road_min_interval_seconds = 0
    monkeypatch.setattr(
        AMapClient,
        "__init__",
        lambda self, settings: original(
            self,
            settings,
            transport=httpx.MockTransport(route_provider),
        ),
    )
    response = flow.post(
        "/incidents",
        {
            "mode": "CREATE",
            "event_type": "ROAD_DELAY",
            "vehicle_id": "V01",
            "description": "前方路段拥堵",
            "delay_minutes": 20,
            "road_report": {
                "location": {
                    "longitude": origin[0],
                    "latitude": origin[1],
                    "captured_at": now,
                    "source": "MAP_PIN",
                    "coordinate_system": "GCJ02",
                }
                if provider_crosses_area
                else {
                    "longitude": 114.295,
                    "latitude": 30.502,
                    "captured_at": now,
                    "source": "GPS",
                    "coordinate_system": "WGS84",
                    "accuracy_m": 12,
                },
                "congestion": {
                    "road_name": "演示路",
                    "direction": "向东",
                    "start": [114.309, 30.499],
                    "end": [114.311, 30.501],
                },
            },
        },
    )
    assert response.status_code == 201, response.text
    incident = response.json()["data"]
    endpoint = "/incidents/" + incident["incident_id"] + "/road-recovery"
    body = {"expected_incident_version": incident["incident_version"]}
    assert flow.post(endpoint, body).status_code == 403
    response = flow.post(endpoint, body, "dispatcher")
    assert response.status_code == 202, response.text
    job_id = response.json()["data"]["job_id"]
    flow.app.state.business.run_job(job_id)
    job = flow.get("/jobs/" + job_id, "dispatcher").json()["data"]
    assert job["job_status"] == "SUCCEEDED", job
    workspace = flow.get(
        "/incidents/" + incident["incident_id"] + "/workspace", "dispatcher"
    ).json()["data"]
    comparison = workspace["analyses"][-1]["road_comparison"]
    assert [v["strategy"] for v in comparison["variants"]] == ["WAIT", "DETOUR", "RESEQUENCE"]
    assert (origin, True) in requested and (origin, False) in requested
    if provider_crosses_area:
        assert all(not item["available"] for item in comparison["variants"][1:])
        assert all(p["metrics"]["road_strategy"] == "WAIT" for p in workspace["plans"])
        return
    assert (
        comparison["variants"][0]["order_impacts"][0]["updated_eta"]
        > comparison["variants"][1]["order_impacts"][0]["updated_eta"]
    )
    detour = next(p for p in workspace["plans"] if p["metrics"]["road_strategy"] == "DETOUR")
    assert not detour["changes"]
    preview = flow.get(
        "/map/route", "dispatcher", params={"vehicle_id": "V01", "plan_id": detour["plan_id"]}
    )
    assert preview.json()["data"]["geometry"]["coordinates"][0] == origin
    response = flow.approve(detour)
    assert response.status_code == 200, response.text
    assert flow.route_revision() == 1
    response = flow.post(
        "/plans/" + detour["plan_id"] + "/receipts",
        {
            "expected_plan_version": response.json()["data"]["plan_version"],
            "decision": "ACCEPTED",
        },
    )
    assert response.status_code == 200, response.text
    assert flow.route_revision() == 2
    active = flow.get("/map/route", params={"vehicle_id": "V01"})
    assert active.json()["data"]["geometry"] == preview.json()["data"]["geometry"]


def test_old_position_cannot_be_saved_as_current(flow):
    response = flow.post(
        "/incidents",
        {
            "mode": "CREATE",
            "event_type": "ROAD_DELAY",
            "vehicle_id": "V01",
            "description": "前方路段拥堵",
            "delay_minutes": 20,
            "road_report": {
                "location": {
                    "longitude": 114.30,
                    "latitude": 30.5,
                    "captured_at": (datetime.now(UTC) - timedelta(minutes=6)).isoformat(),
                    "source": "GPS",
                    "coordinate_system": "WGS84",
                    "accuracy_m": 10,
                },
                "congestion": {
                    "road_name": "演示路",
                    "direction": "向东",
                    "start": [114.309, 30.499],
                    "end": [114.311, 30.501],
                },
            },
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "STALE_LOCATION"
