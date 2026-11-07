import importlib
from copy import deepcopy

import pytest


def module(name):
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        pytest.fail(f"Required foundation module {name} is not implemented")


@pytest.fixture
def snapshot():
    return module("app.seed").benchmark_snapshot()


def test_benchmark_resequence(snapshot):
    solver = module("app.solver")
    incident = {
        "incident_id": "E001",
        "version": 1,
        "event_type": "ROAD_DELAY",
        "vehicle_id": "V01",
        "delay_minutes": 20,
    }
    impacts = solver.analyze_snapshot(snapshot, incident)
    b = next(o for o in impacts["order_impacts"] if o["order_no"] == "O002")
    assert b["original_eta"].endswith("10:30:00+08:00")
    assert b["updated_eta"].endswith("10:50:00+08:00")
    assert b["delay_minutes"] == 15
    result = solver.generate_candidates(snapshot, incident)
    assert result["solution_status"] == "FEASIBLE"
    best = result["plans"][0]
    assert best["metrics"]["total_delay_minutes"] == 0
    assert best["metrics"]["sequence_inversion_count"] == 1
    assert best["metrics"]["changed_order_count"] == 2
    assert best["metrics"]["reassigned_order_count"] == 0
    assert best["metrics"]["total_distance_m"] == 20000
    assert best["constraints"]["passed"]
    assert {d["entity_type"] for d in best["dependencies"]} == {
        "INCIDENT",
        "VEHICLE",
        "ORDER",
        "ROUTING",
        "RULESET",
        "FLEET_SET",
    }


def test_asymmetric_waiting_and_hard_windows(snapshot):
    solver = module("app.solver")
    snapshot["routing"]["A"]["B"]["minutes"] = 30
    snapshot["orders"][0]["earliest_arrival"] = "2026-10-26T10:25:00+08:00"
    snapshot["orders"][1]["window_type"] = "HARD"
    result = solver.simulate_route(snapshot, "V01", ["O001", "O002", "O003"], 0)
    assert result["order_impacts"][0]["updated_eta"].endswith("10:25:00+08:00")
    assert result["order_impacts"][1]["updated_eta"].endswith("11:00:00+08:00")
    assert not result["constraints"]["passed"]


def test_loaded_protection_capacity_availability_and_missing_data(snapshot):
    solver = module("app.solver")
    snapshot["orders"][0]["assigned_vehicle_id"] = "V02"
    result = solver.simulate_route(snapshot, "V01", ["O001", "O002", "O003"], 0)
    assert "LOADED_ORDER_REASSIGNMENT" in {v["code"] for v in result["constraints"]["violations"]}
    snapshot["vehicles"][0]["capacity_kg"] = 150
    snapshot["vehicles"][0]["status"] = "UNAVAILABLE"
    del snapshot["routing"]["A"]["B"]
    result = solver.simulate_route(snapshot, "V01", ["O001", "O002", "O003"], 0)
    codes = {v["code"] for v in result["constraints"]["violations"]}
    assert {"CAPACITY_EXCEEDED", "VEHICLE_UNAVAILABLE", "DATA_INCOMPLETE"} <= codes


def test_completed_prefix_and_unaffected_routes_preserved(snapshot):
    solver = module("app.solver")
    other = deepcopy(snapshot["vehicles"][0])
    other["vehicle_id"] = "V02"
    snapshot["vehicles"].append(other)
    order = deepcopy(snapshot["orders"][0])
    order.update(order_no="O004", assigned_vehicle_id="V02")
    snapshot["orders"].append(order)
    stop = deepcopy(snapshot["route_stops"][0])
    stop.update(stop_id="STOP004", vehicle_id="V02", order_no="O004")
    snapshot["route_stops"].append(stop)
    snapshot["route_stops"][0]["status"] = "COMPLETED"
    snapshot["orders"][0].update(status="COMPLETED", cargo_status="DELIVERED")
    snapshot["vehicles"][0]["location_node_id"] = "A"
    incident = {"event_type": "ROAD_DELAY", "vehicle_id": "V01", "delay_minutes": 20}
    result = solver.generate_candidates(snapshot, incident)
    assert result["solution_status"] == "FEASIBLE"
    assert result["plans"]
    for plan in result["plans"]:
        assert snapshot["route_stops"][0] in plan["target_routes"]
        assert stop in plan["target_routes"]
    invalid = solver.simulate_route(snapshot, "V01", ["O002", "O001", "O003"], 0)
    assert not invalid["constraints"]["passed"]


def test_breakdown_is_bounded_no_solution(snapshot):
    result = module("app.solver").generate_candidates(
        snapshot, {"event_type": "VEHICLE_BREAKDOWN", "vehicle_id": "V01"}
    )
    assert result["solution_status"] == "NO_FEASIBLE_PLAN_IN_SEARCH"
    assert result["plans"] == []


def test_database_models_and_explicit_seed():
    db, models, seed = (module("app." + n) for n in ("db", "models", "seed"))
    engine = db.make_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    with db.make_session_factory(engine)() as session:
        seed.seed_demo(
            session,
            dispatcher_password="dispatch-demo-password",
            driver_password="driver-demo-password",
        )
        from sqlalchemy import func, select

        assert session.scalar(select(func.count()).select_from(models.Vehicle)) == 5
        assert session.scalar(select(func.count()).select_from(models.Order)) == 20
        assert session.scalar(select(func.count()).select_from(models.User)) == 6


def test_warehouse_pickup_covers_all_unloaded_orders(snapshot):
    for order in snapshot["orders"]:
        order["cargo_status"] = "NOT_LOADED"
    result = module("app.solver").simulate_route(snapshot, "V01", ["O001", "O002", "O003"], 0)
    assert result["constraints"]["passed"]


def test_total_delay_sums_orders_and_priority_precedes_total(snapshot):
    for order in snapshot["orders"]:
        order["latest_arrival"] = "2026-10-26T10:00:00+08:00"
    snapshot["orders"][2]["priority"] = "HIGH"
    solver = module("app.solver")
    result = solver.generate_candidates(
        snapshot, {"event_type": "ROAD_DELAY", "vehicle_id": "V01", "delay_minutes": 0}
    )
    best = result["plans"][0]
    assert best["target_routes"][0]["order_no"] == "O003"
    assert best["metrics"]["total_delay_minutes"] == sum(
        i["delay_minutes"] for i in best["order_impacts"]
    )


def test_notification_dedup_constraint():
    from sqlalchemy import UniqueConstraint

    model = module("app.models").Notification
    assert any(
        isinstance(c, UniqueConstraint)
        and {col.name for col in c.columns} == {"tenant_id", "user_id", "event_id"}
        for c in model.__table__.constraints
    )


@pytest.mark.parametrize("operation", ["analyze_snapshot", "generate_candidates"])
@pytest.mark.parametrize(
    "defect",
    [
        "missing_stop",
        "unassigned_pending",
        "unknown_vehicle",
        "unknown_stop_vehicle",
        "duplicate_route",
        "duplicate_order",
        "missing_collection",
        "malformed_collection",
        "terminal_stop_for_active_order",
        "wrong_delivery_node",
    ],
)
def test_incomplete_full_snapshot_cannot_hide_work(snapshot, operation, defect):
    from app.errors import DomainError

    incident = {"event_type": "ROAD_DELAY", "vehicle_id": "V01", "delay_minutes": 20}
    if defect == "missing_stop":
        snapshot["route_stops"].pop()
    elif defect == "unassigned_pending":
        snapshot["orders"][2].update(
            status="PENDING", cargo_status="NOT_LOADED", assigned_vehicle_id=None
        )
        snapshot["route_stops"].pop()
    elif defect == "unknown_vehicle":
        incident["vehicle_id"] = "V999"
    elif defect == "unknown_stop_vehicle":
        snapshot["route_stops"][2]["vehicle_id"] = "V999"
    elif defect == "duplicate_route":
        vehicle = deepcopy(snapshot["vehicles"][0])
        vehicle["vehicle_id"] = "V02"
        snapshot["vehicles"].append(vehicle)
        stop = deepcopy(snapshot["route_stops"][0])
        stop.update(stop_id="OTHER", vehicle_id="V02")
        snapshot["route_stops"].append(stop)
    elif defect == "duplicate_order":
        snapshot["orders"].append(deepcopy(snapshot["orders"][0]))
    elif defect == "missing_collection":
        del snapshot["orders"]
    elif defect == "malformed_collection":
        snapshot["route_stops"] = [None]
    elif defect == "terminal_stop_for_active_order":
        snapshot["route_stops"][2]["status"] = "CANCELLED"
    else:
        snapshot["route_stops"][2]["node_id"] = "A"
    with pytest.raises(DomainError) as exc:
        getattr(module("app.solver"), operation)(snapshot, incident)
    assert exc.value.code == "DATA_INCOMPLETE"
    assert exc.value.status_code == 422
    assert exc.value.missing_fields


def test_direct_simulation_detects_omitted_active_order(snapshot):
    snapshot["route_stops"].pop()
    result = module("app.solver").simulate_route(snapshot, "V01", ["O001", "O002"], 0)
    assert not result["constraints"]["passed"]
    assert "DATA_INCOMPLETE" in {v["code"] for v in result["constraints"]["violations"]}


def test_breakdown_does_not_bypass_snapshot_validation(snapshot):
    from app.errors import DomainError

    with pytest.raises(DomainError) as exc:
        module("app.solver").generate_candidates(
            snapshot, {"event_type": "VEHICLE_BREAKDOWN", "vehicle_id": "V999"}
        )
    assert exc.value.code == "DATA_INCOMPLETE"


def test_nonleading_cancelled_stop_preserves_record_without_travel(snapshot):
    snapshot["route_stops"][1]["status"] = "CANCELLED"
    snapshot["orders"][1]["status"] = "CANCELLED"
    result = module("app.solver").generate_candidates(
        snapshot, {"event_type": "ROAD_DELAY", "vehicle_id": "V01", "delay_minutes": 20}
    )
    assert result["solution_status"] == "FEASIBLE"
    assert result["plans"]
    for plan in result["plans"]:
        assert snapshot["route_stops"][1] in plan["target_routes"]
        assert {i["order_no"] for i in plan["order_impacts"]} == {"O001", "O003"}
    direct = module("app.solver").simulate_route(snapshot, "V01", ["O001", "O002", "O003"], 20)
    assert direct["constraints"]["passed"]
    assert direct["total_distance_m"] == 18000


def test_arrived_stop_remains_fixed_with_future_cancellation(snapshot):
    snapshot["route_stops"][0]["status"] = "ARRIVED"
    snapshot["route_stops"][1]["status"] = "CANCELLED"
    snapshot["orders"][1]["status"] = "CANCELLED"
    result = module("app.solver").generate_candidates(
        snapshot, {"event_type": "ROAD_DELAY", "vehicle_id": "V01", "delay_minutes": 0}
    )
    assert result["solution_status"] == "FEASIBLE"
    assert result["plans"]
    assert all(p["target_routes"][0] == snapshot["route_stops"][0] for p in result["plans"])
    invalid = module("app.solver").simulate_route(snapshot, "V01", ["O003", "O002", "O001"], 0)
    assert "ARRIVED_STOP_MOVED" in {v["code"] for v in invalid["constraints"]["violations"]}


@pytest.mark.parametrize("capacity_field, capacity", [("capacity_kg", 250), ("capacity_m3", 2.5)])
@pytest.mark.parametrize("cancelled_stop_present", [True, False])
def test_cancelled_onboard_cargo_still_counts_toward_capacity(
    snapshot, capacity_field, capacity, cancelled_stop_present
):
    snapshot["orders"][1]["status"] = "CANCELLED"
    snapshot["route_stops"][1]["status"] = "CANCELLED"
    if not cancelled_stop_present:
        snapshot["route_stops"].pop(1)
        snapshot["route_stops"][1]["sequence_no"] = 2
    snapshot["vehicles"][0][capacity_field] = capacity
    solver = module("app.solver")
    sequence = [s["order_no"] for s in snapshot["route_stops"]]
    direct = solver.simulate_route(snapshot, "V01", sequence, 0)
    assert "CAPACITY_EXCEEDED" in {v["code"] for v in direct["constraints"]["violations"]}
    incident = {"event_type": "ROAD_DELAY", "vehicle_id": "V01", "delay_minutes": 0}
    result = solver.generate_candidates(snapshot, incident)
    assert result["solution_status"] == "NO_FEASIBLE_PLAN_IN_SEARCH"
    assert not result["plans"]
    assert "CAPACITY_EXCEEDED" in {v["code"] for v in result["infeasible_reasons"]}

    # Exactly fitting physical cargo is feasible, without counting active goods twice.
    snapshot["vehicles"][0][capacity_field] = 300 if capacity_field == "capacity_kg" else 3
    fitting = solver.generate_candidates(snapshot, incident)
    assert fitting["solution_status"] == "FEASIBLE"
    assert fitting["plans"]


def test_cancelled_unloaded_order_is_not_picked_up(snapshot):
    snapshot["orders"][1].update(status="CANCELLED", cargo_status="NOT_LOADED")
    snapshot["route_stops"][1]["status"] = "CANCELLED"
    snapshot["vehicles"][0].update(capacity_kg=200, capacity_m3=2)
    result = module("app.solver").generate_candidates(
        snapshot, {"event_type": "ROAD_DELAY", "vehicle_id": "V01", "delay_minutes": 0}
    )
    assert result["solution_status"] == "FEASIBLE"
    assert result["plans"]


def test_cancelled_onboard_cargo_cannot_change_vehicle(snapshot):
    snapshot["orders"][1].update(status="CANCELLED", assigned_vehicle_id="V02")
    snapshot["route_stops"][1]["status"] = "CANCELLED"
    direct = module("app.solver").simulate_route(snapshot, "V01", ["O001", "O002", "O003"], 0)
    assert "LOADED_ORDER_REASSIGNMENT" in {v["code"] for v in direct["constraints"]["violations"]}


@pytest.mark.parametrize("hard_window", [False, True])
def test_missing_candidate_edges_are_incomplete_even_if_some_plans_are_valid(snapshot, hard_window):
    from app.errors import DomainError

    if hard_window:
        snapshot["orders"][1]["window_type"] = "HARD"
    snapshot["routing"] = {
        a: {b: snapshot["routing"][a][b]} for a, b in [("W", "A"), ("A", "B"), ("B", "C")]
    }
    with pytest.raises(DomainError) as exc:
        module("app.solver").generate_candidates(
            snapshot, {"event_type": "ROAD_DELAY", "vehicle_id": "V01", "delay_minutes": 20}
        )
    assert exc.value.code == "DATA_INCOMPLETE"
    assert exc.value.status_code == 422
    assert exc.value.missing_fields
