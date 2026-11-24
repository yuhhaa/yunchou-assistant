"""Incident-scoped driving comparisons. Global routing data is never rewritten."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise, permutations
from math import ceil, cos, dist, radians
from time import perf_counter

from app import models as m
from app.maps import AMapClient, geometry_parts
from app.schemas import ImpactInput
from app.services.common import dispatcher, fail, get, incident_access, iso
from app.solver import _dependencies, analyze_snapshot, simulate_route

ORIGIN = "ROAD_ORIGIN"
MAX_STOPS = 5
LOCATION_TTL = timedelta(minutes=5)


@dataclass(frozen=True)
class AvoidArea:
    west: float
    south: float
    east: float
    north: float

    @classmethod
    def from_segment(cls, start, end):
        scale = 111_320 * cos(radians((start[1] + end[1]) / 2))
        length = dist((start[0] * scale, start[1] * 111_320), (end[0] * scale, end[1] * 111_320))
        if scale < 1_000 or not 15 <= length <= 2_000:
            fail("INVALID_INPUT", "拥堵路段两端距离需在 15 米至 2 公里之间", 422)
        dx, dy = 15 / scale, 15 / 111_320
        return cls(
            round(min(start[0], end[0]) - dx, 6),
            round(min(start[1], end[1]) - dy, 6),
            round(max(start[0], end[0]) + dx, 6),
            round(max(start[1], end[1]) + dy, 6),
        )

    def ring(self):
        return [
            [self.west, self.south],
            [self.east, self.south],
            [self.east, self.north],
            [self.west, self.north],
        ]

    def provider_value(self):
        return ";".join(f"{x:.6f},{y:.6f}" for x, y in self.ring())

    def contains(self, point):
        x, y = point
        return self.west <= x <= self.east and self.south <= y <= self.north

    def intersects(self, points):
        for a, b in pairwise(points):
            low, high = 0.0, 1.0
            for start, end, minimum, maximum in zip(
                a, b, (self.west, self.south), (self.east, self.north)
            ):
                delta = end - start
                if delta == 0:
                    if not minimum <= start <= maximum:
                        break
                    continue
                entry, leave = sorted(((minimum - start) / delta, (maximum - start) / delta))
                low, high = max(low, entry), min(high, leave)
                if low > high:
                    break
            else:
                return True
        return False


def location_deadline(location):
    captured = datetime.fromisoformat(location["captured_at"])
    deadline = captured + LOCATION_TTL
    now = m.utcnow()
    if captured > now + timedelta(seconds=30) or deadline <= now:
        fail("STALE_LOCATION", "车辆位置已过期或时间有误，请重新确认当前位置", 422)
    return deadline


def report_facts(report):
    facts = report.model_dump(mode="json")
    location_deadline(facts["location"])
    segment = facts["congestion"]
    AvoidArea.from_segment(segment["start"], segment["end"])
    return facts


def submit(session, actor, incident_id, body, settings, operation_id):
    from app.services import context, jobs, maps

    dispatcher(actor)
    incident = incident_access(session, actor, incident_id)
    if incident.version != body.expected_incident_version:
        fail("STALE_INCIDENT")
    report = incident.facts.get("road_report")
    if incident.event_type != "ROAD_DELAY" or not report:
        fail("DATA_INCOMPLETE", "需要确认车辆当前位置和拥堵路段", 422)
    deadline = location_deadline(report["location"])
    if not settings.amap_web_service_key.get_secret_value():
        fail("MAP_UNCONFIGURED", "请配置后端高德 Web 服务 Key", 503)
    if incident.current_calculation_job_id:
        current = session.get(m.Job, incident.current_calculation_job_id)
        if current and current.status in {"QUEUED", "RUNNING"}:
            fail("INCIDENT_NOT_READY", "已有路线计算正在进行，请等待结果")
    baseline = context.make_context(session, actor, settings, vehicle_id=incident.vehicle_id)
    snapshot = session.get(m.Snapshot, baseline["snapshot_id"])
    pending = [
        s for s in snapshot.payload["route_stops"] if s["status"] not in {"COMPLETED", "CANCELLED"}
    ]
    if not 1 <= len(pending) <= MAX_STOPS or any(s["status"] != "PENDING" for s in pending):
        fail("INVALID_INPUT", "现场绕行支持 1 至 5 个待配送站点，请先完成已到站订单", 422)
    nodes = {s["node_id"]: get(session, m.Node, s["node_id"], actor) for s in pending}
    missing = maps.missing_coordinates(nodes.values())
    if missing:
        fail("DATA_INCOMPLETE", "请先确认剩余配送点的高德坐标", 422, missing_fields=missing)
    snapshot.dependencies = [
        *snapshot.dependencies,
        *(
            {"entity_type": "NODE", "entity_id": n.id, "version": str(n.version)}
            for n in nodes.values()
        ),
    ]
    snapshot.valid_until = min(deadline, datetime.fromisoformat(baseline["valid_until"]))
    snapshot.payload = {
        **snapshot.payload,
        "valid_until": iso(snapshot.valid_until),
        "routing_source": "AMAP",
        "missing_fields": [],
        "road_request": {
            "report": report,
            "destinations": {n.id: [n.longitude, n.latitude] for n in nodes.values()},
        },
    }
    return jobs.submit(
        session,
        actor,
        ImpactInput(
            incident_id=incident.id,
            expected_incident_version=incident.version,
            snapshot_id=snapshot.id,
        ),
        settings,
        operation_id,
        "PLAN_GENERATION",
        road=True,
    )


def _matrix(legs):
    result = {}
    for (a, b), leg in legs.items():
        if leg is not None:
            result.setdefault(a, {})[b] = {
                "minutes": ceil(leg["duration_seconds"] / 60),
                "distance_m": leg["distance_m"],
            }
    return result


def compute(payload, event, settings):
    started = perf_counter()
    request = payload["road_request"]
    report = request["report"]
    location_deadline(report["location"])
    segment = report["congestion"]
    area = AvoidArea.from_segment(segment["start"], segment["end"])
    remaining = [s["node_id"] for s in payload["route_stops"] if s["status"] == "PENDING"]
    if ORIGIN in request["destinations"]:
        fail("INVALID_INPUT", "配送节点使用了现场定位保留编号", 422)
    origin, direct, avoided = AMapClient(settings).incident_legs(
        report["location"], request["destinations"], remaining, area
    )
    return evaluate_road_candidates(payload, event, origin, direct, avoided, area, started=started)


def evaluate_road_candidates(payload, event, origin, direct, avoided, area, *, started=None):
    """Compare supplied, already-costed road edges without calling a map provider."""
    started = perf_counter() if started is None else started
    stops = payload["route_stops"]
    pending = [s for s in stops if s["status"] == "PENDING"]
    original = [s["order_no"] for s in pending]
    route_nodes = {s["order_no"]: s["node_id"] for s in pending}
    order_by_id = {order["order_no"]: order for order in payload["orders"]}
    vehicle = {
        **payload["vehicles"][0],
        "location_node_id": ORIGIN,
        "location_time": payload["clock_time"],
    }
    payload = {**payload, "vehicles": [vehicle], "routing": _matrix(direct)}
    wait_minutes = event["delay_minutes"]
    safe_routing = _matrix(avoided)
    safe_snapshot = {**payload, "routing": safe_routing}

    def evaluate(sequence, legs, snapshot, wait):
        node_sequence = [ORIGIN, *(route_nodes[order] for order in sequence)]
        edges = [legs.get(pair) for pair in pairwise(node_sequence)]
        if any(edge is None for edge in edges):
            return None
        pending_order = iter(sequence)
        full_sequence = [
            next(pending_order) if s["status"] == "PENDING" else s["order_no"] for s in stops
        ]
        calculated = simulate_route(snapshot, event["vehicle_id"], full_sequence, wait)
        parts = [part for edge in edges for part in geometry_parts(edge["geometry"])]
        duration = sum(edge["duration_seconds"] for edge in edges)
        return calculated, full_sequence, node_sequence, parts, duration

    wait = evaluate(original, direct, payload, wait_minutes)
    analysis = (
        analyze_snapshot(payload, event)
        if wait
        else {
            "incident_id": event["incident_id"],
            "incident_version": event["version"],
            "snapshot_id": payload["snapshot_id"],
            "analysis_status": "VALID",
            "risk_order_count": 0,
            "order_impacts": [],
            "missing_fields": [],
        }
    )
    detour = evaluate(original, avoided, safe_snapshot, 0)
    reordered = [
        candidate
        for seq in permutations(original)
        if list(seq) != original
        if (candidate := evaluate(seq, avoided, safe_snapshot, 0))
        and candidate[0]["constraints"]["passed"]
    ]

    def rank(candidate):
        calculated, sequence, *_ = candidate
        delays = calculated["order_impacts"]
        return (
            sum(
                i["delay_minutes"]
                for i in delays
                if order_by_id[i["order_no"]]["priority"] == "HIGH"
            ),
            sum(i["delay_minutes"] for i in delays),
            sum(s["order_no"] != order for s, order in zip(stops, sequence)),
            calculated["total_distance_m"],
        )

    variants, plans, stored = [], [], {}
    before = {i["order_no"]: i["original_eta"] for i in analysis["order_impacts"]}
    baseline_distance = wait[0]["total_distance_m"] if wait else None
    for strategy, candidate in (
        ("WAIT", wait),
        ("DETOUR", detour),
        ("RESEQUENCE", min(reordered, key=rank, default=None)),
    ):
        if candidate is None:
            variants.append(
                {"strategy": strategy, "available": False, "reason": "没有符合要求的道路或配送方案"}
            )
            continue
        calculated, sequence, node_sequence, parts, duration = candidate
        impacts = [
            {
                **i,
                "original_eta": before.get(i["order_no"]),
                "evidence_refs": ["AMAP_INCIDENT_ROUTE"],
            }
            for i in calculated["order_impacts"]
        ]
        changes = [
            {
                "change_type": "RESEQUENCE",
                "order_no": old["order_no"],
                "from_vehicle_id": event["vehicle_id"],
                "to_vehicle_id": event["vehicle_id"],
                "old_sequence_no": old["sequence_no"],
                "new_sequence_no": sequence.index(old["order_no"]) + 1,
            }
            for old in stops
            if old["order_no"] != sequence[old["sequence_no"] - 1]
        ]
        metrics = {
            "road_strategy": strategy,
            "unresolved_order_count": 0,
            "late_order_count": sum(i["delay_minutes"] > 0 for i in impacts),
            "high_priority_delay_minutes": sum(
                i["delay_minutes"]
                for i in impacts
                if order_by_id[i["order_no"]]["priority"] == "HIGH"
            ),
            "total_delay_minutes": sum(i["delay_minutes"] for i in impacts),
            "changed_order_count": len(changes),
            "reassigned_order_count": 0,
            "sequence_inversion_count": sum(
                sequence.index(a) > sequence.index(b)
                for idx, a in enumerate(original)
                for b in original[idx + 1 :]
            ),
            "total_distance_m": calculated["total_distance_m"],
            "extra_distance_m": (
                calculated["total_distance_m"] - baseline_distance
                if baseline_distance is not None
                else None
            ),
            "computation_ms": int((perf_counter() - started) * 1000),
        }
        passed = calculated["constraints"]["passed"]
        variants.append(
            {
                "strategy": strategy,
                "available": passed,
                "metrics": metrics,
                "constraints": calculated["constraints"],
                "order_impacts": impacts,
            }
        )
        if not passed:
            continue
        targets = sorted(
            ({**s, "sequence_no": sequence.index(s["order_no"]) + 1} for s in stops),
            key=lambda s: s["sequence_no"],
        )
        plans.append(
            {
                "metrics": metrics,
                "changes": changes,
                "constraints": calculated["constraints"],
                "dependencies": _dependencies(payload, event),
                "target_routes": targets,
            }
        )
        stored[strategy] = {
            "node_sequence": node_sequence,
            "parts": parts,
            "duration_seconds": duration,
            "distance_m": calculated["total_distance_m"],
        }
    snapshot_patch = {
        "vehicles": payload["vehicles"],
        "routing": payload["routing"],
        "road_routes": stored,
        "road_avoid_routing": safe_routing,
        "road_origin": origin,
    }
    plans.sort(
        key=lambda p: tuple(
            p["metrics"][key]
            for key in (
                "high_priority_delay_minutes",
                "total_delay_minutes",
                "changed_order_count",
                "total_distance_m",
            )
        )
    )
    analysis["road_comparison"] = {
        "origin": origin,
        "avoid_area": area.ring(),
        "source": "AMAP",
        "wait_minutes": wait_minutes,
        "variants": variants,
        "recommended_strategy": plans[0]["metrics"]["road_strategy"] if plans else None,
    }
    return {
        "solution_status": "FEASIBLE" if plans else "NO_FEASIBLE_PLAN_IN_SEARCH",
        "plans": plans,
        "search_scope": {
            "vehicle_ids": [event["vehicle_id"]],
            "order_nos": original,
            "budget_ms": 60000,
            "method": "BOUNDED_SAME_VEHICLE_RESEQUENCE",
            "max_permutations": 120,
        },
        "infeasible_reasons": [
            v for item in variants for v in item.get("constraints", {}).get("violations", [])
        ],
        "road_snapshot_patch": snapshot_patch,
        "road_analysis": analysis,
    }


def simulation_inputs(payload, strategy, delay):
    if strategy == "WAIT" or not strategy:
        return payload, delay
    return {**payload, "routing": payload["road_avoid_routing"]}, 0
