"""Bounded same-vehicle resequencing. Matrix directions are never inferred."""

from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
from itertools import islice, permutations
from math import ceil, isfinite
from time import perf_counter

from pydantic import ValidationError

from .errors import DomainError
from .schemas import OrderInput, RouteStopInput, VehicleInput


def _time(value):
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Timestamp requires timezone")
    return result


def _clock(snapshot):
    return _time(snapshot["clock_time"])


def _route(snapshot, vehicle_id):
    return sorted(
        (s for s in snapshot["route_stops"] if s["vehicle_id"] == vehicle_id),
        key=lambda s: s["sequence_no"],
    )


def _violation(code, ids, message):
    return {"code": code, "entity_ids": ids, "message": message}


def _raise_incomplete(violations):
    incomplete = [v for v in violations if v["code"] == "DATA_INCOMPLETE"]
    if incomplete:
        raise DomainError(
            "DATA_INCOMPLETE",
            "Calculation inputs incomplete or inconsistent",
            422,
            missing_fields=sorted({field for v in incomplete for field in v["entity_ids"]}),
            details={"violations": violations},
        )


def _snapshot_records(snapshot):
    """Check public record shapes before indexing raw immutable snapshot data."""
    issues = []
    for collection, schema in (
        ("vehicles", VehicleInput),
        ("orders", OrderInput),
        ("route_stops", RouteStopInput),
    ):
        records = snapshot.get(collection)
        if not isinstance(records, list):
            issues.append(_violation("DATA_INCOMPLETE", [collection], "Record array missing"))
            continue
        for index, record in enumerate(records):
            path = f"{collection}[{index}]"
            if not isinstance(record, dict):
                issues.append(_violation("DATA_INCOMPLETE", [path], "Record must be an object"))
                continue
            try:
                schema.model_validate({k: record[k] for k in schema.model_fields if k in record})
            except ValidationError as exc:
                fields = [path + "." + ".".join(map(str, e["loc"])) for e in exc.errors()]
                issues.append(_violation("DATA_INCOMPLETE", fields, "Invalid public record"))
    _raise_incomplete(issues)


def _snapshot_violations(snapshot, incident=None):
    _snapshot_records(snapshot)
    issues = []

    def fail(path, message):
        issues.append(_violation("DATA_INCOMPLETE", [path], message))

    indexed = {}
    for collection, key in (
        ("vehicles", "vehicle_id"),
        ("orders", "order_no"),
        ("route_stops", "stop_id"),
    ):
        indexed[collection] = {item[key]: item for item in snapshot[collection]}
        if len(indexed[collection]) != len(snapshot[collection]):
            fail(collection, "Duplicate record identifiers")
    vehicles, orders = indexed["vehicles"], indexed["orders"]
    if incident is not None and incident.get("vehicle_id") not in vehicles:
        fail("incident.vehicle_id", "Incident vehicle missing from snapshot")
    coverage = {}
    for stop in snapshot["route_stops"]:
        order_no = stop["order_no"]
        path = f"route_stops.{stop['stop_id']}"
        coverage.setdefault(order_no, []).append(stop)
        if stop["vehicle_id"] not in vehicles:
            fail(path + ".vehicle_id", "Route vehicle missing")
        order = orders.get(order_no)
        if order is None:
            fail(path + ".order_no", "Route order missing")
            continue
        if stop["vehicle_id"] != order["assigned_vehicle_id"]:
            fail(path + ".vehicle_id", "Route differs from order assignment")
        if stop["node_id"] != order["delivery_node_id"]:
            fail(path + ".node_id", "Route differs from delivery location")
        terminal = order["status"] in ("COMPLETED", "CANCELLED")
        if (terminal and stop["status"] != order["status"]) or (
            not terminal and stop["status"] not in ("PENDING", "ARRIVED")
        ):
            fail(path + ".status", "Order and stop states disagree")
    for order_no, order in orders.items():
        path = f"orders.{order_no}"
        if order["assigned_vehicle_id"] not in vehicles:
            fail(path + ".assigned_vehicle_id", "Assigned vehicle missing")
        count = len(coverage.get(order_no, []))
        if count > 1 or (order["status"] in ("PENDING", "IN_TRANSIT") and count != 1):
            fail(path + ".route_stops", "Active order must occur exactly once across fleet routes")
    return issues


def simulate_route(snapshot, vehicle_id, order_sequence, delay_minutes):
    """Simulate one full route including preserved completed/cancelled records.

    Returns order_impacts, total_distance_m, constraints. Arrival is max(physical
    arrival, earliest_arrival); early waiting and service delay every later stop.
    No return-to-depot leg is included in this delivery-route distance.
    """
    try:
        violations = _snapshot_violations(snapshot)
    except DomainError as exc:
        violations = exc.details["violations"]
        return {
            "order_impacts": [],
            "total_distance_m": 0,
            "constraints": {
                "passed": False,
                "checked_at": _clock(snapshot).isoformat(),
                "violations": violations,
            },
        }
    impacts = []
    checked_at = _clock(snapshot).isoformat()
    vehicles = {v["vehicle_id"]: v for v in snapshot["vehicles"]}
    orders = {o["order_no"]: o for o in snapshot["orders"]}
    vehicle = vehicles.get(vehicle_id)
    if vehicle is None:
        return {
            "order_impacts": [],
            "total_distance_m": 0,
            "constraints": {
                "passed": False,
                "checked_at": checked_at,
                "violations": [_violation("DATA_INCOMPLETE", [vehicle_id], "Vehicle missing")],
            },
        }

    def fail(code, ids, message):
        violations.append(_violation(code, ids, message))

    if type(delay_minutes) is not int or not 0 <= delay_minutes <= 1440:
        raise ValueError("delay_minutes must be an integer from 0 to 1440")
    original = _route(snapshot, vehicle_id)
    expected = [s["order_no"] for s in original]
    if len(order_sequence) != len(set(order_sequence)) or set(expected) != set(order_sequence):
        fail("ORDER_COVERAGE", expected, "Route must contain each original order exactly once")
    if [s["sequence_no"] for s in original] != list(range(1, len(original) + 1)):
        fail("INVALID_ROUTE_SEQUENCE", [vehicle_id], "Original route sequence must be continuous")
    fixed = [s["order_no"] for s in original if s["status"] == "COMPLETED"]
    prefix = []
    for stop in original:
        if stop["status"] in ("COMPLETED", "CANCELLED"):
            prefix.append(stop["order_no"])
        else:
            break
    completed_prefix = [
        s["order_no"] for s in original[: len(prefix)] if s["status"] == "COMPLETED"
    ]
    if fixed != completed_prefix or any(
        index >= len(order_sequence) or order_sequence[index] != stop["order_no"]
        for index, stop in enumerate(original)
        if stop["status"] == "COMPLETED"
    ):
        fail("COMPLETED_STOP_MOVED", fixed, "Completed stops must remain in their original prefix")
    arrived = [s["order_no"] for s in original if s["status"] == "ARRIVED"]
    if arrived and any(
        index >= len(order_sequence) or order_sequence[index] != stop["order_no"]
        for index, stop in enumerate(original)
        if stop["status"] == "ARRIVED"
    ):
        fail("ARRIVED_STOP_MOVED", arrived, "Current arrived stop cannot be moved")
    cancelled = {s["order_no"] for s in original if s["status"] == "CANCELLED"}
    if vehicle["status"] == "UNAVAILABLE":
        fail("VEHICLE_UNAVAILABLE", [vehicle_id], "Vehicle is unavailable")
    now = _clock(snapshot)
    if _time(vehicle["location_time"]) > now:
        fail("FUTURE_LOCATION", [vehicle_id], "Vehicle location is later than business clock")
    time = now + timedelta(minutes=delay_minutes)
    node, distance = vehicle["location_node_id"], 0
    warehouse_pickup = node == snapshot.get("warehouse_node_id", "W")
    # Cancellation removes a delivery visit, not the goods physically on board.
    # Count the union of onboard cargo and future deliveries, without duplicates.
    load = [
        order
        for order_no, order in orders.items()
        if (order["cargo_status"] == "ON_VEHICLE" and order["assigned_vehicle_id"] == vehicle_id)
        or (
            order_no in order_sequence
            and order_no not in prefix
            and order["status"] not in ("COMPLETED", "CANCELLED")
        )
    ]
    for field, capacity_field in (("weight_kg", "capacity_kg"), ("volume_m3", "capacity_m3")):
        quantities = [o.get(field) for o in load]
        capacity = vehicle.get(capacity_field)
        if (
            not isinstance(capacity, (int, float))
            or isinstance(capacity, bool)
            or not isfinite(capacity)
            or capacity <= 0
            or any(
                not isinstance(q, (int, float)) or isinstance(q, bool) or not isfinite(q) or q < 0
                for q in quantities
            )
        ):
            fail("DATA_INCOMPLETE", [vehicle_id], "Invalid capacity or quantity")
        elif sum(quantities) > capacity:
            fail("CAPACITY_EXCEEDED", [vehicle_id], f"Route exceeds {capacity_field}")
    for order_no in order_sequence:
        if order_no not in orders:
            fail("DATA_INCOMPLETE", [order_no], "Order missing")
            continue
        order = orders[order_no]
        if order["cargo_status"] == "ON_VEHICLE" and order["assigned_vehicle_id"] != vehicle_id:
            fail("LOADED_ORDER_REASSIGNMENT", [order_no], "Loaded order cannot change vehicle")
        elif order["assigned_vehicle_id"] != vehicle_id:
            fail(
                "REASSIGNMENT_OUTSIDE_SEARCH",
                [order_no],
                "This solver only resequences same-vehicle orders",
            )
        if order_no in prefix or order_no in cancelled:
            continue
        if order["cargo_status"] == "NOT_LOADED" and not warehouse_pickup:
            fail("PICKUP_UNAVAILABLE", [order_no], "Unloaded order requires warehouse pickup")
        if order["status"] in ("COMPLETED", "CANCELLED"):
            fail(
                "COMPLETED_STOP_MOVED",
                [order_no],
                "Completed or cancelled order cannot be delivered again",
            )
            continue
        facts = order.get("facts", {})
        required = order.get("required_qualifications", facts.get("required_qualifications", []))
        qualifications = vehicle.get(
            "qualifications", vehicle.get("facts", {}).get("qualifications", [])
        )
        eligible = order.get("eligible_vehicle_ids", facts.get("eligible_vehicle_ids"))
        if not set(required).issubset(qualifications) or (
            eligible is not None and vehicle_id not in eligible
        ):
            fail(
                "VEHICLE_INELIGIBLE",
                [order_no, vehicle_id],
                "Vehicle does not satisfy order eligibility",
            )
        destination = order["delivery_node_id"]
        edge = snapshot.get("routing", {}).get(node, {}).get(destination)
        if edge is None:
            fail(
                "DATA_INCOMPLETE",
                [f"routing.{node}.{destination}"],
                "Directed routing edge missing",
            )
            break
        minutes, meters = edge.get("minutes"), edge.get("distance_m")
        if type(minutes) is not int or minutes < 0 or type(meters) is not int or meters < 0:
            fail(
                "DATA_INCOMPLETE",
                [f"routing.{node}.{destination}"],
                "Routing values must be nonnegative integers",
            )
            break
        time += timedelta(minutes=minutes)
        distance += meters
        earliest, latest = _time(order["earliest_arrival"]), _time(order["latest_arrival"])
        if earliest > latest:
            fail("INVALID_WINDOW", [order_no], "Earliest exceeds latest arrival")
        time = max(time, earliest)
        delay = max(0, ceil((time - latest).total_seconds() / 60))
        hard_late = delay > 0 and order["window_type"] == "HARD"
        if hard_late:
            fail("HARD_WINDOW_VIOLATION", [order_no], "Arrival exceeds hard time window")
        impacts.append(
            {
                "order_no": order_no,
                "updated_eta": time.isoformat(),
                "latest_arrival": latest.isoformat(),
                "delay_minutes": delay,
                "risk_type": "HARD_CONSTRAINT_VIOLATION"
                if hard_late
                else ("SOFT_WINDOW_LATE" if delay else "NONE"),
            }
        )
        service = order["service_minutes"]
        if type(service) is not int or not 0 <= service <= 1440:
            fail("DATA_INCOMPLETE", [order_no], "Invalid service minutes")
            break
        time += timedelta(minutes=service)
        node = destination
    shift_end = vehicle.get("shift_end", vehicle.get("facts", {}).get("shift_end"))
    if shift_end and time > _time(shift_end):
        fail("SHIFT_EXCEEDED", [vehicle_id], "Route ends after driver shift")
    return {
        "order_impacts": impacts,
        "total_distance_m": distance,
        "constraints": {
            "passed": not violations,
            "violations": violations,
            "checked_at": checked_at,
        },
    }


def _incident_delay(incident):
    if incident["event_type"] == "ROAD_DELAY":
        if incident.get("delay_minutes") is None:
            raise DomainError(
                "DATA_INCOMPLETE",
                "Explicit delay estimate required",
                422,
                missing_fields=["delay_minutes"],
            )
        return incident["delay_minutes"]
    return 0


def analyze_snapshot(snapshot, incident):
    _raise_incomplete(_snapshot_violations(snapshot, incident))
    if snapshot.get("missing_fields"):
        raise DomainError(
            "DATA_INCOMPLETE",
            "Snapshot missing calculation inputs",
            422,
            missing_fields=snapshot["missing_fields"],
        )
    if incident["event_type"] == "RECEIVING_WINDOW_CHANGE" and not incident.get("verified", False):
        raise DomainError("INCIDENT_NOT_READY", "Reported window must be verified")
    delay = _incident_delay(incident)
    results, all_violations = [], []
    for vehicle in snapshot["vehicles"]:
        vehicle_id = vehicle["vehicle_id"]
        seq = [s["order_no"] for s in _route(snapshot, vehicle_id)]
        before = simulate_route(snapshot, vehicle_id, seq, 0)
        after = simulate_route(
            snapshot, vehicle_id, seq, delay if vehicle_id == incident["vehicle_id"] else 0
        )
        original = {i["order_no"]: i["updated_eta"] for i in before["order_impacts"]}
        for item in after["order_impacts"]:
            order = next(o for o in snapshot["orders"] if o["order_no"] == item["order_no"])
            results.append(
                {
                    **item,
                    "original_eta": original.get(item["order_no"]),
                    "evidence_refs": [
                        f"{incident.get('incident_id', 'INCIDENT')}:v{incident.get('version', incident.get('incident_version', 1))}",
                        f"{item['order_no']}:v{order['version']}",
                        snapshot["routing_version"],
                    ],
                }
            )
        all_violations.extend(after["constraints"]["violations"])
    _raise_incomplete(all_violations)
    if incident["event_type"] == "VEHICLE_BREAKDOWN":
        affected = {
            s["order_no"]
            for s in _route(snapshot, incident["vehicle_id"])
            if s["status"] != "COMPLETED"
        }
        for item in results:
            if item["order_no"] in affected:
                item["risk_type"] = "HARD_CONSTRAINT_VIOLATION"
    return {
        "incident_id": incident.get("incident_id"),
        "incident_version": incident.get("version", incident.get("incident_version", 1)),
        "snapshot_id": snapshot["snapshot_id"],
        "analysis_status": "VALID",
        "risk_order_count": sum(i["risk_type"] != "NONE" for i in results),
        "order_impacts": results,
        "missing_fields": [],
    }


def _dependencies(snapshot, incident):
    deps = [
        {
            "entity_type": "INCIDENT",
            "entity_id": incident.get("incident_id", "INCIDENT"),
            "version": str(incident.get("version", incident.get("incident_version", 1))),
        }
    ]
    for entity_type, collection, key in (
        ("VEHICLE", "vehicles", "vehicle_id"),
        ("ORDER", "orders", "order_no"),
    ):
        deps.extend(
            {"entity_type": entity_type, "entity_id": item[key], "version": str(item["version"])}
            for item in snapshot[collection]
        )
    deps += [
        {
            "entity_type": "ROUTING",
            "entity_id": snapshot.get("routing_id", "DEMO_GRAPH"),
            "version": snapshot["routing_version"],
        },
        {
            "entity_type": "RULESET",
            "entity_id": "MIN_CHANGE_V1",
            "version": snapshot.get("ruleset_version", "MIN_CHANGE_V1"),
        },
        {
            "entity_type": "FLEET_SET",
            "entity_id": "FLEET",
            "version": snapshot.get("fleet_set_version", "1"),
        },
    ]
    return deps


def generate_candidates(snapshot, incident, strategy_profile="MIN_CHANGE_V1"):
    if strategy_profile != "MIN_CHANGE_V1":
        raise ValueError("Unknown strategy profile")
    started = perf_counter()
    _raise_incomplete(_snapshot_violations(snapshot, incident))
    vehicle_id = incident["vehicle_id"]
    route = _route(snapshot, vehicle_id)
    original = [s["order_no"] for s in route]
    delay = _incident_delay(incident)
    computed = _clock(snapshot)
    output = {
        "solution_status": "NO_FEASIBLE_PLAN_IN_SEARCH",
        "search_scope": {
            "vehicle_ids": [vehicle_id],
            "order_nos": original,
            "budget_ms": 60000,
            "method": "BOUNDED_SAME_VEHICLE_RESEQUENCE",
            "max_permutations": 720,
        },
        "plans": [],
        "infeasible_reasons": [],
        "computed_at": computed.isoformat(),
    }
    if incident["event_type"] == "VEHICLE_BREAKDOWN":
        output["infeasible_reasons"] = [
            _violation(
                "VEHICLE_UNAVAILABLE",
                [vehicle_id],
                "Breakdown requires manual handover; loaded orders stay on vehicle",
            )
        ]
        return output
    analyze_snapshot(snapshot, incident)
    pending = [s["order_no"] for s in route if s["status"] == "PENDING"]
    base_distance = sum(
        simulate_route(
            snapshot,
            v["vehicle_id"],
            [s["order_no"] for s in _route(snapshot, v["vehicle_id"])],
            delay if v["vehicle_id"] == vehicle_id else 0,
        )["total_distance_m"]
        for v in snapshot["vehicles"]
    )
    before = {
        i["order_no"]: i["updated_eta"]
        for i in simulate_route(snapshot, vehicle_id, original, 0)["order_impacts"]
    }
    orders = {o["order_no"]: o for o in snapshot["orders"]}
    reasons = {}
    for permutation in islice(permutations(pending), 720):
        if (perf_counter() - started) * 1000 >= 60000:
            break
        remaining = iter(permutation)
        sequence = [next(remaining) if s["status"] == "PENDING" else s["order_no"] for s in route]
        simulations = []
        for vehicle in snapshot["vehicles"]:
            vid = vehicle["vehicle_id"]
            seq = sequence if vid == vehicle_id else [s["order_no"] for s in _route(snapshot, vid)]
            simulations.append(
                simulate_route(snapshot, vid, seq, delay if vid == vehicle_id else 0)
            )
        violations = [v for result in simulations for v in result["constraints"]["violations"]]
        _raise_incomplete(violations)
        if violations:
            for violation in violations:
                reasons[(violation["code"], tuple(violation["entity_ids"]))] = violation
            continue
        impacts = [i for result in simulations for i in result["order_impacts"]]
        inversions = sum(
            sequence.index(a) > sequence.index(b)
            for i, a in enumerate(pending)
            for b in pending[i + 1 :]
        )
        changes = [
            {
                "change_type": "RESEQUENCE",
                "order_no": order_no,
                "from_vehicle_id": vehicle_id,
                "to_vehicle_id": vehicle_id,
                "old_sequence_no": original.index(order_no) + 1,
                "new_sequence_no": sequence.index(order_no) + 1,
            }
            for order_no in original
            if original.index(order_no) != sequence.index(order_no)
        ]
        distance = sum(result["total_distance_m"] for result in simulations)
        metrics = {
            "unresolved_order_count": 0,
            "late_order_count": sum(i["delay_minutes"] > 0 for i in impacts),
            "high_priority_delay_minutes": sum(
                i["delay_minutes"] for i in impacts if orders[i["order_no"]]["priority"] == "HIGH"
            ),
            "total_delay_minutes": sum(i["delay_minutes"] for i in impacts),
            "changed_order_count": len(changes),
            "reassigned_order_count": 0,
            "sequence_inversion_count": inversions,
            "total_distance_m": distance,
            "extra_distance_m": distance - base_distance,
            "computation_ms": max(0, int((perf_counter() - started) * 1000)),
        }
        targets = deepcopy(snapshot["route_stops"])
        for stop in targets:
            if stop["vehicle_id"] == vehicle_id:
                stop["sequence_no"] = sequence.index(stop["order_no"]) + 1
        targets.sort(key=lambda s: (s["vehicle_id"], s["sequence_no"]))
        plan_id = (
            "P"
            + sha256((snapshot["snapshot_id"] + "|" + "|".join(sequence)).encode()).hexdigest()[:16]
        )
        output["plans"].append(
            {
                "plan_id": plan_id,
                "plan_version": 1,
                "incident_id": incident.get("incident_id"),
                "incident_version": incident.get("version", incident.get("incident_version", 1)),
                "snapshot_id": snapshot["snapshot_id"],
                "status": "PENDING_APPROVAL",
                "expires_at": min(
                    computed + timedelta(seconds=300), _time(snapshot["valid_until"])
                ).isoformat(),
                "metrics": metrics,
                "changes": changes,
                "constraints": {
                    "passed": True,
                    "violations": [],
                    "checked_at": computed.isoformat(),
                },
                "dependencies": _dependencies(snapshot, incident),
                "target_routes": targets,
                "order_impacts": [
                    {**i, "original_eta": before.get(i["order_no"], i["updated_eta"])}
                    for i in impacts
                ],
            }
        )
    output["plans"].sort(
        key=lambda p: tuple(
            p["metrics"][k]
            for k in (
                "unresolved_order_count",
                "high_priority_delay_minutes",
                "total_delay_minutes",
                "reassigned_order_count",
                "sequence_inversion_count",
                "total_distance_m",
            )
        )
    )
    if output["plans"]:
        output["solution_status"] = "FEASIBLE"
    else:
        output["infeasible_reasons"] = list(reasons.values())
    return output
