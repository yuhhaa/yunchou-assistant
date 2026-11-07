import asyncio
import importlib
import time

import httpx
import pytest
from sqlalchemy import select

from app import models as m
from app.config import Settings
from app.errors import DomainError


def point(flow, node="W", version=1, actor="dispatcher", key=None, **changes):
    return flow.client.put(
        "/api/v1/map/nodes/" + node,
        headers=flow.headers(actor, key),
        json={
            "longitude": 116.4,
            "latitude": 39.9,
            "coordinate_system": "GCJ02",
            "expected_node_version": version,
            **changes,
        },
    )


def locate(flow):
    with flow.factory() as session:
        ids = list(session.scalars(select(m.Node.id)))
    for index, node in enumerate(ids):
        assert point(flow, node, longitude=116.4 + index / 100).status_code == 200
    return ids


def provider(request):
    if request.url.path.endswith("/geo"):
        return httpx.Response(
            200,
            json={
                "status": "1",
                "geocodes": [{"location": "116.4,39.9", "formatted_address": "北京市", "city": []}],
            },
        )
    origin, destination = request.url.params["origin"], request.url.params["destination"]
    return httpx.Response(
        200,
        json={
            "status": "1",
            "route": {
                "paths": [
                    {
                        "distance": "1200",
                        "cost": {"duration": "125"},
                        "steps": [
                            {"polyline": origin + ";116.41,39.91"},
                            {"polyline": "116.41,39.91;" + destination},
                        ],
                    }
                ]
            },
        },
    )


def client():
    assert importlib.util.find_spec("app.maps"), "AMap adapter is missing"
    return importlib.import_module("app.maps").AMapClient(
        Settings(amap_web_service_key="secret-test"), transport=httpx.MockTransport(provider)
    )


def test_map_scope_missing_and_node_write(flow):
    response = flow.get("/map")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["configuration_status"] == "UNCONFIGURED"
    assert [v["vehicle_id"] for v in data["vehicles"]] == ["V01"]
    assert data["missing_fields"] and all(p["longitude"] is None for p in data["points"])
    assert flow.get("/map?vehicle_id=V02").status_code == 404
    assert point(flow, actor="driver01").status_code == 403
    result = point(flow, key="node-write")
    assert result.status_code == 200, result.text
    assert result.json()["data"]["source"] == "DISPATCHER_CONFIRMED"
    assert point(flow, key="node-write").json()["meta"]["replayed"]
    assert point(flow).status_code == 409
    with flow.factory() as session:
        assert session.get(m.Node, "W").version == 2
        assert session.scalar(select(m.RoutingGraph)).source == "SIMULATION"


@pytest.mark.parametrize(
    "change",
    [
        {"longitude": "116.4"},
        {"latitude": True},
        {"longitude": 181},
        {"latitude": -91},
        {"coordinate_system": "WGS84"},
        {"source": "AMAP"},
        {"expected_node_version": 0},
    ],
)
def test_coordinates_strict(flow, change):
    assert point(flow, **change).status_code == 422


def test_provider_parses_real_geometry_cost_and_geocode():
    amap = client()
    route = amap.route([(116.4, 39.9), (116.42, 39.92)])
    assert route["distance_m"] == 1200 and route["duration_seconds"] == 125
    assert len(route["geometry"]["coordinates"]) == 3
    assert amap.geocode("北京市")[0]["city"] is None


def test_route_missing_no_fallback(flow):
    response = flow.get("/map/route?vehicle_id=V01")
    assert response.status_code == 422, response.text
    assert response.json()["error"]["missing_fields"]


def test_route_rechecks_versions_cache_and_no_transaction(flow, monkeypatch):
    locate(flow)
    amap = client()
    service = importlib.import_module("app.services.maps")
    monkeypatch.setattr(service, "AMapClient", lambda settings: amap)
    original = amap.route
    calls = []

    def routing(points):
        calls.append(points)
        return original(points)

    monkeypatch.setattr(amap, "route", routing)
    first = flow.get("/map/route?vehicle_id=V01")
    assert first.status_code == 200, first.text
    assert first.json()["data"]["routing_profile"] == "DRIVING_CAR"
    assert flow.get("/map/route?vehicle_id=V01").status_code == 200
    assert len(calls) == 1
    point(flow, version=2)

    def changed(points):
        with flow.factory() as session, session.begin():
            session.get(m.Vehicle, "V01").route_revision += 1
        return original(points)

    monkeypatch.setattr(amap, "route", changed)
    assert flow.get("/map/route?vehicle_id=V01").status_code == 409


def test_refresh_publishes_full_matrix_and_invalidates(flow, monkeypatch):
    plan = flow.generate()
    assert flow.approve(plan).status_code == 200
    locate(flow)
    amap = client()
    flow.app.state.settings.amap_web_service_key = amap.settings.amap_web_service_key
    monkeypatch.setattr(importlib.import_module("app.services.maps"), "AMapClient", lambda s: amap)
    response = flow.post(
        "/map/routing/refresh", {"expected_routing_version": "R1"}, "dispatcher", "refresh"
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["data"]["job_id"]
    assert flow.app.state.business.run_job(job_id)["job_status"] == "SUCCEEDED"
    result = flow.get("/jobs/" + job_id, "dispatcher").json()["data"]
    assert result["result_kind"] == "MAP_ROUTING"
    with flow.factory() as session:
        graph = session.scalar(select(m.RoutingGraph))
        assert graph.source == "AMAP" and graph.ready
        assert graph.simulation_matrix
        assert all(len(row) == len(graph.matrix) for row in graph.matrix.values())
        assert graph.matrix["W"]["W"]["minutes"] == 0
        assert session.get(m.Plan, plan["plan_id"]).status == "STALE"
        assert not session.scalars(select(m.Reservation)).all()
        version = graph.routing_version
    context = flow.context()
    assert context["routing_version"] == version and context["routing_source"] == "AMAP"
    assert point(flow, version=2).status_code == 200
    assert "routing" in flow.context()["missing_fields"]


def test_provider_errors_do_not_leak_secret():
    amap = client()
    amap.transport = httpx.MockTransport(lambda r: httpx.Response(500, text="secret-test"))
    with pytest.raises(DomainError) as exc:
        amap.route([(116.4, 39.9), (116.42, 39.92)])
    assert "secret-test" not in str(exc.value)


def test_disconnected_provider_steps_never_create_a_straight_connector():
    amap = client()

    def broken(request):
        data = provider(request).json()
        data["route"]["paths"][0]["steps"][1]["polyline"] = "117,40;117.1,40.1"
        return httpx.Response(200, json=data)

    amap.transport = httpx.MockTransport(broken)
    with pytest.raises(DomainError):
        amap.route([(116.4, 39.9), (116.42, 39.92)])


def test_provider_trickling_response_obeys_total_budget():
    amap = client()
    amap.settings.amap_total_budget_seconds = 1

    class Trickle(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                await asyncio.sleep(0.1)
                yield b" "

    amap.transport = httpx.MockTransport(lambda r: httpx.Response(200, stream=Trickle()))
    started = time.monotonic()
    with pytest.raises(DomainError) as exc:
        amap.route([(116.4, 39.9), (116.42, 39.92)])
    assert exc.value.code == "MAP_TIMEOUT"
    assert time.monotonic() - started < 2


@pytest.mark.parametrize("mode", ["failure", "node", "fleet", "owner", "lease", "role"])
def test_failed_or_superseded_refresh_never_changes_graph(flow, monkeypatch, mode):
    from datetime import timedelta

    from pydantic import SecretStr

    locate(flow)
    flow.app.state.settings.amap_web_service_key = SecretStr("test-map")
    job_id = flow.post(
        "/map/routing/refresh", {"expected_routing_version": "R1"}, "dispatcher"
    ).json()["data"]["job_id"]
    amap = client()
    original = amap.matrix

    def change(points):
        if mode == "failure":
            raise DomainError("MAP_UNAVAILABLE", "Provider unavailable", 503)
        with flow.factory() as session, session.begin():
            job = session.get(m.Job, job_id)
            if mode == "node":
                session.get(m.Node, "W").version += 1
            elif mode == "fleet":
                session.get(m.Vehicle, "V01").route_revision += 1
            elif mode == "owner":
                job.lease_owner, job.attempts = "replacement", job.attempts + 1
            elif mode == "lease":
                job.lease_expires_at = m.utcnow() - timedelta(seconds=1)
            elif mode == "role":
                session.get(m.User, job.user_id).role = "DRIVER"
        return original(points)

    monkeypatch.setattr(amap, "matrix", change)
    monkeypatch.setattr(importlib.import_module("app.services.maps"), "AMapClient", lambda s: amap)
    flow.app.state.business.run_job(job_id)
    with flow.factory() as session:
        graph = session.scalar(select(m.RoutingGraph))
        assert graph.source == "SIMULATION" and graph.routing_version == "R1"
        job = session.get(m.Job, job_id)
        assert job.status == ("RUNNING" if mode == "owner" else "FAILED")


def test_map_candidate_sequence_and_browser_job_schema_and_tool_boundary(flow, monkeypatch):
    from app.outputs import Envelope, JobOutput, MapRouteOutput, StatusOutput
    from app.services.common import Actor
    from app.services.status import job_data

    plan = flow.generate()
    locate(flow)
    amap = client()
    flow.app.state.settings.amap_web_service_key = amap.settings.amap_web_service_key
    monkeypatch.setattr(importlib.import_module("app.services.maps"), "AMapClient", lambda s: amap)
    response = flow.get("/map/route?vehicle_id=V01&plan_id=" + plan["plan_id"])
    assert response.status_code == 200, response.text
    parsed = Envelope[MapRouteOutput].model_validate(response.json()).data
    expected = [
        s["node_id"]
        for s in sorted(plan["target_routes"], key=lambda s: s["sequence_no"])
        if s["vehicle_id"] == "V01" and s["status"] not in {"COMPLETED", "CANCELLED"}
    ]
    assert parsed.node_sequence[1:] == expected and parsed.route_kind == "CANDIDATE_PREVIEW"
    assert (
        flow.get("/map/route?vehicle_id=V01&plan_id=" + plan["plan_id"], "driver02").status_code
        == 404
    )
    response = flow.post(
        "/map/routing/refresh", {"expected_routing_version": "R1"}, "dispatcher", "map-once"
    )
    job_id = response.json()["data"]["job_id"]
    assert flow.post(
        "/map/routing/refresh", {"expected_routing_version": "R1"}, "dispatcher", "map-once"
    ).json()["meta"]["replayed"]
    assert flow.get("/jobs/" + job_id).status_code == 404
    Envelope[JobOutput].model_validate(flow.get("/jobs/" + job_id, "dispatcher").json())
    flow.app.state.business.run_job(job_id)
    Envelope[StatusOutput].model_validate(
        flow.get("/status?object_type=JOB&object_id=" + job_id, "dispatcher").json()
    )
    with flow.factory() as session:
        job = session.get(m.Job, job_id)
        actor = Actor(job.user_id, job.tenant_id, "DISPATCHER", None, ("V01",))
        with pytest.raises(DomainError):
            job_data(session, actor, job.id, tool=True)


def test_refresh_requires_whole_set_and_key_and_dispatcher(flow):
    locate(flow)
    assert flow.post("/map/routing/refresh", {"expected_routing_version": "R1"}).status_code == 403
    assert (
        flow.post(
            "/map/routing/refresh",
            {"expected_routing_version": "R1", "node_ids": ["W"]},
            "dispatcher",
        ).status_code
        == 422
    )
    assert (
        flow.post(
            "/map/routing/refresh", {"expected_routing_version": "R1"}, "dispatcher"
        ).status_code
        == 503
    )
    assert flow.get("/map/geocode?address=Beijing").status_code == 403
    assert flow.get("/map/geocode?address=Beijing", "dispatcher").status_code == 503


def test_restricted_tool_schema_does_not_advertise_map_or_chat_jobs(flow):
    from app.export_openapi import restricted

    document = restricted(flow.app.openapi())
    assert "MapRoutingOutput" not in document["components"]["schemas"]
    assert "RunResult" not in document["components"]["schemas"]


def test_map_network_calls_own_no_database_session(flow, monkeypatch):
    from contextlib import contextmanager

    from app.services import maps
    from app.services.common import Actor

    locate(flow)
    live = []

    @contextmanager
    def factory():
        with flow.factory() as session:
            live.append(session)
            try:
                yield session
            finally:
                live.remove(session)

    amap = client()
    original = amap.route

    def observe(points):
        assert live == []
        return original(points)

    monkeypatch.setattr(amap, "route", observe)
    monkeypatch.setattr(maps, "AMapClient", lambda s: amap)
    actor = Actor("DRIVER01", "DEMO", "DRIVER", "V01")
    assert maps.route(actor, flow.app.state.settings, factory, "V01")["source"] == "AMAP"


@pytest.mark.parametrize(
    "payload",
    [[], {"status": "1", "route": []}, {"status": "1", "route": {"paths": [{"distance": "nan"}]}}],
)
def test_malformed_provider_response_is_sanitized(payload):
    amap = client()
    amap.transport = httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    with pytest.raises(DomainError) as exc:
        amap.route([(116.4, 39.9), (116.42, 39.92)])
    assert exc.value.code == "MAP_UNAVAILABLE"


def test_coordinate_nonfinite_and_refresh_cap():
    from pydantic import ValidationError

    from app.schemas import MapNodeInput

    for value in (float("nan"), float("inf"), -float("inf")):
        with pytest.raises(ValidationError):
            MapNodeInput(
                expected_node_version=1, longitude=value, latitude=39.9, coordinate_system="GCJ02"
            )
    with pytest.raises(DomainError) as exc:
        client().matrix({str(i): [116.4, 39.9] for i in range(22)})
    assert exc.value.status_code == 422


def test_map_jobs_recover_and_use_compute_queue(flow, monkeypatch):
    from datetime import timedelta

    from app import worker
    from app.maintenance import recover_jobs

    locate(flow)
    flow.app.state.settings.amap_web_service_key = client().settings.amap_web_service_key
    job_id = flow.post(
        "/map/routing/refresh", {"expected_routing_version": "R1"}, "dispatcher"
    ).json()["data"]["job_id"]
    with flow.factory() as session, session.begin():
        job = session.get(m.Job, job_id)
        job.status, job.lease_owner = "RUNNING", "lost-worker"
        job.attempts = 1
        job.lease_expires_at = m.utcnow() - timedelta(seconds=1)
    recover_jobs(flow.factory)
    with flow.factory() as session:
        job = session.get(m.Job, job_id)
        assert job.status == "QUEUED" and job.lease_owner is None
        assert session.scalars(
            select(m.OutboxEvent).where(m.OutboxEvent.event_type == "JOB_SUBMITTED")
        ).all()
    sent = []
    monkeypatch.setattr(worker.celery, "send_task", lambda *a, **kw: sent.append((a, kw)))
    worker.publish("MAP_ROUTING", job_id)
    assert sent == [(("fleet.compute",), {"args": [job_id], "queue": "compute"})]
