from sqlalchemy import select

from app import models as m


def test_wuhan_demo_is_explicit_scoped_and_does_not_confirm_coordinates(flow):
    response = flow.get("/map/demo")
    assert response.status_code == 200, response.text
    from app.outputs import DemoMapOutput, Envelope

    Envelope[DemoMapOutput].model_validate(response.json())
    data = response.json()["data"]
    assert data["city"] == "武汉市" and data["source"] == "SIMULATION"
    assert data["simulation"] is True and data["road_service_used"] is False
    assert data["coordinate_system"] == "GCJ02"
    assert {p["node_id"] for p in data["points"]} == set("WABC")
    assert all(p["source"] == "SIMULATION" for p in data["points"])
    route = data["routes"][0]
    assert route["vehicle_id"] == "V01" and route["node_sequence"] == list("WABC")
    assert route["geometry_kind"] == "SCHEMATIC" and route["route_kind"] == "CURRENT"
    assert route["distance_m"] is None and route["duration_seconds"] is None
    assert all(114 < p[0] < 115 and 30 < p[1] < 31 for p in route["geometry"]["coordinates"])
    with flow.factory() as session:
        assert all(n.longitude is None for n in session.scalars(select(m.Node)))
        assert session.scalar(select(m.RoutingGraph)).routing_version == "R1"
    assert flow.get("/map/route?vehicle_id=V01").status_code == 422


def test_demo_map_preserves_browser_and_vehicle_scope(flow):
    assert flow.client.get("/api/v1/map/demo").status_code == 401
    assert flow.get("/map/demo?vehicle_id=V02").status_code == 404
    assert flow.get("/map/demo?source=AMAP").status_code == 422
    response = flow.get("/map/demo", "dispatcher")
    assert response.status_code == 200, response.text
    assert len(response.json()["data"]["routes"]) == 5
    assert len(response.json()["data"]["points"]) == 9


def test_demo_candidate_preview_uses_saved_plan_without_activation(flow):
    plan = flow.generate()
    response = flow.get("/map/demo", params={"vehicle_id": "V01", "plan_id": plan["plan_id"]})
    assert response.status_code == 200, response.text
    route = response.json()["data"]["routes"][0]
    assert route["node_sequence"] == list("WBAC")
    assert route["route_kind"] == "CANDIDATE_PREVIEW"
    assert route["plan_id"] == plan["plan_id"]
    assert flow.get("/map/demo").json()["data"]["routes"][0]["node_sequence"] == list("WABC")
    assert flow.get("/map/demo", params={"plan_id": plan["plan_id"]}).status_code == 422
    flow.post(
        "/plans/" + plan["plan_id"] + "/reject",
        {"expected_plan_version": plan["plan_version"], "reason": "test rejection"},
        "dispatcher",
    )
    assert (
        flow.get("/map/demo", params={"vehicle_id": "V01", "plan_id": plan["plan_id"]}).status_code
        == 409
    )


def test_demo_uses_current_stop_facts_and_rejects_unknown_demo_nodes(flow):
    with flow.factory() as session, session.begin():
        session.get(m.RouteStop, "STOP001").status = "COMPLETED"
    route = flow.get("/map/demo").json()["data"]["routes"][0]
    assert route["node_sequence"] == list("WBC")
    with flow.factory() as session, session.begin():
        vehicle = session.get(m.Vehicle, "V01")
        session.add(
            m.Node(id="EXTRA", tenant_id=vehicle.tenant_id, name="extra", node_type="CUSTOMER")
        )
        vehicle.location_node_id = "EXTRA"
    response = flow.get("/map/demo")
    assert response.status_code == 422
    assert "simulation_nodes.EXTRA" in response.json()["error"]["missing_fields"]
