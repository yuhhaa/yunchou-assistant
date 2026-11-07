from copy import deepcopy

from sqlalchemy import select

from app import models as m
from app.schemas import OrderInput, RouteStopInput, VehicleInput
from app.services.common import expires, fail, get, iso, lock_fleet, rows, vehicle_access


def select_vehicle(session, actor, *, order_no=None, vehicle_id=None):
    if order_no:
        order = get(session, m.Order, order_no, actor)
        if vehicle_id and order.assigned_vehicle_id != vehicle_id:
            fail("INVALID_INPUT", "Order and vehicle disagree", 422)
        vehicle_id = order.assigned_vehicle_id
    if not vehicle_id:
        vehicle_id = actor.vehicle_id
    if not vehicle_id:
        fail(
            "MISSING_FIELDS",
            "Select a vehicle or order",
            422,
            missing_fields=["vehicle_id or order_no"],
            next_action="ASK_USER",
        )
    return vehicle_access(session, actor, vehicle_id)


def make_context(session, actor, settings, *, order_no=None, vehicle_id=None):
    lock_fleet(session, actor.tenant_id)
    if not actor.vehicle_id and actor.role == "DRIVER" and not vehicle_id and not order_no:
        empty = {
            "snapshot_id": m.new_id(),
            "snapshot_version": 1,
            "valid_until": iso(expires(min(settings.snapshot_ttl_seconds, 300))),
            "vehicles": [],
            "orders": [],
            "route_stops": [],
            "missing_fields": [],
            "clock_mode": settings.clock_mode,
            "clock_time": settings.simulation_time
            if settings.clock_mode == "SIMULATION"
            else iso(m.utcnow()),
            "routing_version": None,
            "source": "SIMULATION" if settings.clock_mode == "SIMULATION" else "BUSINESS",
            "selected_order_no": None,
        }
        session.add(
            m.Snapshot(
                id=empty["snapshot_id"],
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                payload=empty,
                dependencies=[],
                valid_until=expires(min(settings.snapshot_ttl_seconds, 300)),
            )
        )
        return empty
    vehicle = select_vehicle(session, actor, order_no=order_no, vehicle_id=vehicle_id)
    graph = session.scalar(
        select(m.RoutingGraph).where(m.RoutingGraph.tenant_id == actor.tenant_id)
    )
    if not graph:
        fail("DATA_INCOMPLETE", "Missing routing graph", 422, missing_fields=["routing"])
    orders = [
        o for o in rows(session, m.Order, actor.tenant_id) if o.assigned_vehicle_id == vehicle.id
    ]
    stops = sorted(
        [s for s in rows(session, m.RouteStop, actor.tenant_id) if s.vehicle_id == vehicle.id],
        key=lambda s: s.sequence_no,
    )
    payload = {
        "snapshot_id": m.new_id(),
        "snapshot_version": 1,
        "valid_until": iso(expires(min(settings.snapshot_ttl_seconds, 300))),
        "clock_mode": settings.clock_mode,
        "clock_time": settings.simulation_time
        if settings.clock_mode == "SIMULATION"
        else iso(m.utcnow()),
        "source": "SIMULATION" if settings.clock_mode == "SIMULATION" else "BUSINESS",
        "vehicles": [
            {
                **VehicleInput.model_validate(
                    {
                        **{
                            k: getattr(vehicle, k)
                            for k in VehicleInput.model_fields
                            if k != "vehicle_id"
                        },
                        "vehicle_id": vehicle.id,
                    }
                ).model_dump(mode="json"),
                "driver_id": vehicle.driver_id,
                "facts": vehicle.facts,
            }
        ],
        "orders": [
            {
                **OrderInput.model_validate(
                    {
                        **{k: getattr(o, k) for k in OrderInput.model_fields if k != "order_no"},
                        "order_no": o.id,
                        "earliest_arrival": iso(o.earliest_arrival),
                        "latest_arrival": iso(o.latest_arrival),
                    }
                ).model_dump(mode="json"),
                "facts": o.facts,
            }
            for o in orders
        ],
        "route_stops": [
            RouteStopInput.model_validate(
                {
                    **{k: getattr(s, k) for k in RouteStopInput.model_fields if k != "stop_id"},
                    "stop_id": s.id,
                }
            ).model_dump(mode="json")
            for s in stops
        ],
        "routing_id": graph.id,
        "routing_version": graph.routing_version,
        "routing_source": graph.source,
        "routing_ready": graph.ready,
        "routing": graph.matrix if graph.ready else {},
        "ruleset_version": graph.ruleset_version,
        "fleet_set_version": graph.fleet_set_version,
        "warehouse_node_id": "W",
        "missing_fields": [] if graph.ready else ["routing"],
        "selected_order_no": order_no,
    }
    dependencies = [
        {"entity_type": "VEHICLE", "entity_id": vehicle.id, "version": str(vehicle.version)}
    ]
    dependencies += [
        {"entity_type": "ORDER", "entity_id": o.id, "version": str(o.version)} for o in orders
    ]
    dependencies += [
        {"entity_type": kind, "entity_id": entity_id, "version": version}
        for kind, entity_id, version in [
            ("ROUTING", graph.id, graph.routing_version),
            ("RULESET", "MIN_CHANGE_V1", graph.ruleset_version),
            ("FLEET_SET", "FLEET", graph.fleet_set_version),
        ]
    ]
    session.add(
        m.Snapshot(
            id=payload["snapshot_id"],
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            payload=deepcopy(payload),
            dependencies=dependencies,
            valid_until=expires(min(settings.snapshot_ttl_seconds, 300)),
        )
    )
    # Routing stays in the persisted calculation input, never exposed to model.
    return {k: v for k, v in payload.items() if k != "routing"}
