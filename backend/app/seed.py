"""Explicit deterministic SIMULATION fixtures. Nothing seeds during application startup."""

import argparse
import os
from copy import deepcopy
from datetime import datetime

from sqlalchemy import select

from . import models
from .config import Settings
from .db import make_engine, make_session_factory
from .security import hash_password


def benchmark_snapshot():
    clock = "2026-10-26T10:00:00+08:00"
    routing = {n: {n: {"minutes": 0, "distance_m": 0}} for n in "WABC"}
    for a, b, minutes, distance in (
        ("W", "A", 15, 8000),
        ("W", "B", 10, 5000),
        ("W", "C", 25, 12000),
        ("A", "B", 10, 5000),
        ("A", "C", 20, 10000),
        ("B", "C", 15, 7000),
    ):
        routing[a][b] = {"minutes": minutes, "distance_m": distance}
        routing[b][a] = {"minutes": minutes, "distance_m": distance}
    orders, stops = [], []
    for i, (node, latest) in enumerate(zip("ABC", ("10:50", "10:35", "11:15")), 1):
        order_no = f"O{i:03}"
        orders.append(
            {
                "order_no": order_no,
                "version": 1,
                "assigned_vehicle_id": "V01",
                "cargo_status": "ON_VEHICLE",
                "status": "IN_TRANSIT",
                "delivery_node_id": node,
                "earliest_arrival": clock,
                "latest_arrival": f"2026-10-26T{latest}:00+08:00",
                "window_type": "SOFT",
                "service_minutes": 5,
                "weight_kg": 100,
                "volume_m3": 1,
                "priority": "NORMAL",
            }
        )
        stops.append(
            {
                "stop_id": f"STOP{i:03}",
                "vehicle_id": "V01",
                "order_no": order_no,
                "sequence_no": i,
                "node_id": node,
                "stop_type": "DELIVERY",
                "status": "PENDING",
            }
        )
    return {
        "snapshot_id": "S001",
        "snapshot_version": 1,
        "valid_until": "2026-10-26T10:05:00+08:00",
        "clock_mode": "SIMULATION",
        "clock_time": clock,
        "source": "SIMULATION",
        "vehicles": [
            {
                "vehicle_id": "V01",
                "vehicle_no": "DEMO01",
                "version": 1,
                "status": "ON_TASK",
                "location_node_id": "W",
                "location_time": clock,
                "capacity_kg": 1000,
                "capacity_m3": 8,
                "route_revision": 1,
                "driver_id": "DRIVER01",
            }
        ],
        "orders": orders,
        "route_stops": stops,
        "routing_version": "R1",
        "routing_id": "DEMO_GRAPH",
        "routing": routing,
        "ruleset_version": "MIN_CHANGE_V1",
        "fleet_set_version": "1",
        "missing_fields": [],
    }


def demo_snapshot():
    data = benchmark_snapshot()
    nodes = "WABCDEFGH"
    for a in nodes:
        data["routing"].setdefault(a, {})
        for b in nodes:
            if b not in data["routing"][a]:
                delta = abs(nodes.index(a) - nodes.index(b))
                data["routing"][a][b] = {
                    "minutes": 0 if a == b else 5 + delta * 2,
                    "distance_m": 0 if a == b else 2000 + delta * 500,
                }
    next_order = 4
    for i, count in ((2, 5), (3, 4), (4, 4), (5, 4)):
        vehicle = deepcopy(data["vehicles"][0])
        vehicle.update(vehicle_id=f"V{i:02}", vehicle_no=f"DEMO{i:02}", driver_id=f"DRIVER{i:02}")
        data["vehicles"].append(vehicle)
        for seq in range(1, count + 1):
            order = deepcopy(data["orders"][0])
            order_no = f"O{next_order:03}"
            node = "DEFGH"[(next_order - 4) % 5]
            order.update(
                order_no=order_no,
                assigned_vehicle_id=vehicle["vehicle_id"],
                delivery_node_id=node,
                latest_arrival="2026-10-26T15:00:00+08:00",
            )
            data["orders"].append(order)
            data["route_stops"].append(
                {
                    "stop_id": f"STOP{next_order:03}",
                    "vehicle_id": vehicle["vehicle_id"],
                    "order_no": order_no,
                    "sequence_no": seq,
                    "node_id": node,
                    "stop_type": "DELIVERY",
                    "status": "PENDING",
                }
            )
            next_order += 1
    return data


def seed_demo(session, *, dispatcher_password, driver_password, tenant_id="DEMO"):
    if not dispatcher_password or not driver_password:
        raise ValueError("Explicit demo passwords are required")
    if session.scalar(select(models.User).where(models.User.tenant_id == tenant_id)):
        raise ValueError("Demo tenant already seeded")
    data = demo_snapshot()
    session.add(
        models.User(
            id="DISPATCHER01",
            tenant_id=tenant_id,
            username="dispatcher",
            password_hash=hash_password(dispatcher_password),
            role="DISPATCHER",
        )
    )
    for node in "WABCDEFGH":
        session.add(
            models.Node(
                id=node,
                tenant_id=tenant_id,
                name=f"SIMULATION {node}",
                node_type="WAREHOUSE" if node == "W" else "CUSTOMER",
            )
        )
    for item in data["vehicles"]:
        values = deepcopy(item)
        vehicle_id = values.pop("vehicle_id")
        values["location_time"] = datetime.fromisoformat(values["location_time"])
        session.add(
            models.Vehicle(
                id=vehicle_id, tenant_id=tenant_id, **values, facts={"source": "SIMULATION"}
            )
        )
        driver = item["driver_id"]
        session.add(
            models.User(
                id=driver,
                tenant_id=tenant_id,
                username=driver.lower(),
                password_hash=hash_password(driver_password),
                role="DRIVER",
                vehicle_id=vehicle_id,
            )
        )
    for item in data["orders"]:
        values = deepcopy(item)
        order_no = values.pop("order_no")
        for field in ("earliest_arrival", "latest_arrival"):
            values[field] = datetime.fromisoformat(values[field])
        session.add(
            models.Order(id=order_no, tenant_id=tenant_id, **values, facts={"source": "SIMULATION"})
        )
    for item in data["route_stops"]:
        values = deepcopy(item)
        stop_id = values.pop("stop_id")
        session.add(models.RouteStop(id=stop_id, tenant_id=tenant_id, **values))
    session.add(
        models.RoutingGraph(
            id="DEMO_GRAPH",
            tenant_id=tenant_id,
            routing_version="R1",
            matrix=data["routing"],
            simulation_matrix=deepcopy(data["routing"]),
            simulation_routing_version="R1",
        )
    )
    session.commit()
    return data


def main():
    parser = argparse.ArgumentParser(description="Explicit SIMULATION demo seed")
    parser.add_argument("--dispatcher-password", default=os.getenv("DEMO_DISPATCHER_PASSWORD"))
    parser.add_argument("--driver-password", default=os.getenv("DEMO_DRIVER_PASSWORD"))
    args = parser.parse_args()
    if not args.dispatcher_password or not args.driver_password:
        parser.error("Provide both demo passwords by environment or CLI")
    engine = make_engine(Settings().database_url)
    with make_session_factory(engine)() as session:
        seed_demo(
            session,
            dispatcher_password=args.dispatcher_password,
            driver_password=args.driver_password,
        )
    print("SIMULATION demo created: 5 vehicles, 20 orders")


if __name__ == "__main__":
    main()
