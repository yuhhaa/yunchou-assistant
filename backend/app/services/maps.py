"""Map authorization, immutable input capture and fenced external-data publication."""

from copy import deepcopy

from sqlalchemy import delete, select

from app import models as m
from app.errors import DomainError
from app.maps import AMapClient
from app.services.common import (
    Actor,
    audit,
    aware,
    canonical_hash,
    dispatcher,
    expires,
    fail,
    get,
    invalidate,
    iso,
    lock_fleet,
    plan_access,
    refresh_locked,
    rows,
    vehicle_access,
)
from app.services.lifecycle import check_plan


def fresh_actor(session, actor):
    user = session.get(m.User, actor.user_id)
    if not user or not user.active or user.tenant_id != actor.tenant_id:
        fail("UNAUTHENTICATED", status=401)
    if actor.roots is not None:
        fail("FORBIDDEN", status=403)
    return Actor(user.id, user.tenant_id, user.role, user.vehicle_id)


def graph_for(session, actor):
    graph = session.scalar(
        select(m.RoutingGraph).where(m.RoutingGraph.tenant_id == actor.tenant_id)
    )
    if not graph:
        fail("DATA_INCOMPLETE", status=422, missing_fields=["routing"])
    return graph


def node_data(node):
    return {
        "node_id": node.id,
        "name": node.name,
        "node_type": node.node_type,
        "longitude": node.longitude,
        "latitude": node.latitude,
        "coordinate_system": node.coordinate_system,
        "address": node.address,
        "source": node.coordinate_source,
        "updated_at": iso(node.coordinate_updated_at),
        "version": node.version,
    }


def missing_coordinates(nodes):
    return [
        f"nodes.{n.id}.coordinates"
        for n in nodes
        if n.longitude is None or n.latitude is None or n.coordinate_system != "GCJ02"
    ]


def selected_vehicles(session, actor, vehicle_id):
    if vehicle_id:
        return [vehicle_access(session, actor, vehicle_id)]
    if actor.role == "DISPATCHER":
        return rows(session, m.Vehicle, actor.tenant_id)
    return [vehicle_access(session, actor, actor.vehicle_id)] if actor.vehicle_id else []


def map_data(session, actor, settings, vehicle_id=None):
    actor = fresh_actor(session, actor)
    vehicles = selected_vehicles(session, actor, vehicle_id)
    ids = {v.id for v in vehicles}
    node_ids = {v.location_node_id for v in vehicles}
    node_ids.update(
        s.node_id
        for s in rows(session, m.RouteStop, actor.tenant_id)
        if s.vehicle_id in ids and s.status not in {"COMPLETED", "CANCELLED"}
    )
    nodes = [
        n
        for n in rows(session, m.Node, actor.tenant_id)
        if n.id in node_ids or n.node_type == "WAREHOUSE"
    ]
    graph = graph_for(session, actor)
    return {
        "provider": "AMAP",
        "configuration_status": "CONFIGURED"
        if settings.amap_web_service_key.get_secret_value()
        else "UNCONFIGURED",
        "coordinate_system": "GCJ02",
        "clock_mode": settings.clock_mode,
        "clock_time": settings.simulation_time
        if settings.clock_mode == "SIMULATION"
        else iso(m.utcnow()),
        "routing_source": graph.source,
        "routing_version": graph.routing_version,
        "routing_ready": graph.ready,
        "missing_fields": missing_coordinates(nodes),
        "points": [node_data(n) for n in sorted(nodes, key=lambda n: n.id)],
        "vehicles": [
            {
                "vehicle_id": v.id,
                "route_revision": v.route_revision,
                "location_node_id": v.location_node_id,
                "location_basis": "NODE",
            }
            for v in sorted(vehicles, key=lambda v: v.id)
        ],
    }


def clear_cache(session, tenant_id):
    session.execute(delete(m.MapRouteCache).where(m.MapRouteCache.tenant_id == tenant_id))


def invalidate_graph(session, tenant_id):
    for vehicle in rows(session, m.Vehicle, tenant_id):
        invalidate(session, tenant_id, vehicle_id=vehicle.id)
    clear_cache(session, tenant_id)


def update_node(session, actor, node_id, body, settings, operation_id):
    actor = fresh_actor(session, actor)
    dispatcher(actor)
    node = get(session, m.Node, node_id, actor)
    if node.version != body.expected_node_version:
        fail("STALE_NODE", "Node version changed")
    node.longitude, node.latitude = body.longitude, body.latitude
    node.coordinate_system, node.coordinate_source = "GCJ02", "DISPATCHER_CONFIRMED"
    node.coordinate_updated_at = m.utcnow()
    node.version += 1
    if body.name is not None:
        node.name = body.name
    if body.address is not None:
        node.address = body.address
    graph = graph_for(session, actor)
    clear_cache(session, actor.tenant_id)
    if graph.source != "SIMULATION":
        graph.ready = False
        graph.version += 1
        graph.routing_version = "MAP_" + m.new_id()
        invalidate_graph(session, actor.tenant_id)
    audit(
        session,
        actor,
        "confirm_map_node",
        "NODE",
        node.id,
        {
            "node_version": node.version,
            "coordinate_system": "GCJ02",
            "source": node.coordinate_source,
        },
        operation_id,
    )
    return node_data(node), 200


def geocode(actor, settings, session_factory, address, city=None):
    with session_factory() as session:
        dispatcher(fresh_actor(session, actor))
    candidates = AMapClient(settings).geocode(address, city)
    with session_factory() as session:
        dispatcher(fresh_actor(session, actor))
    return {
        "source": "AMAP",
        "coordinate_system": "GCJ02",
        "candidates": candidates,
        "retrieved_at": iso(m.utcnow()),
    }


def route_selection(session, actor, vehicle_id, plan_id):
    actor = fresh_actor(session, actor)
    vehicle = vehicle_access(session, actor, vehicle_id)
    graph = graph_for(session, actor)
    plan = plan_access(session, actor, plan_id) if plan_id else None
    if plan:
        if plan.status not in {"PENDING_APPROVAL", "APPROVED_PENDING_ACK"}:
            fail("STALE_PLAN", "Only usable candidate routes can be previewed")
        check_plan(session, plan, reservation_required=plan.status == "APPROVED_PENDING_ACK")
        stops = [s for s in plan.target_routes if s["vehicle_id"] == vehicle.id]
        if not stops:
            fail("NOT_FOUND", status=404)
    else:
        stops = [
            {
                "stop_id": s.id,
                "node_id": s.node_id,
                "sequence_no": s.sequence_no,
                "status": s.status,
                "version": s.version,
            }
            for s in rows(session, m.RouteStop, actor.tenant_id)
            if s.vehicle_id == vehicle.id
        ]
    stops = sorted(
        [s for s in stops if s["status"] not in {"COMPLETED", "CANCELLED"}],
        key=lambda s: s["sequence_no"],
    )
    sequence = [vehicle.location_node_id, *[s["node_id"] for s in stops]]
    nodes = [get(session, m.Node, n, actor) for n in sequence]
    return {
        "vehicle_id": vehicle.id,
        "vehicle_version": vehicle.version,
        "route_revision": vehicle.route_revision,
        "plan_id": plan_id,
        "plan_version": plan.version if plan else None,
        "plan_status": plan.status if plan else None,
        "routing_version": graph.routing_version,
        "node_versions": {n.id: n.version for n in nodes},
        "node_sequence": sequence,
        "missing_fields": missing_coordinates(nodes),
        "points": [[n.longitude, n.latitude] for n in nodes],
        "stops": stops,
    }


def route_inputs(session, actor, vehicle_id, plan_id):
    inputs = route_selection(session, actor, vehicle_id, plan_id)
    missing = inputs.pop("missing_fields")
    if missing:
        fail(
            "DATA_INCOMPLETE",
            "Missing confirmed coordinates",
            422,
            missing_fields=sorted(set(missing)),
        )
    return inputs


def route(actor, settings, session_factory, vehicle_id, plan_id=None):
    with session_factory() as session, session.begin():
        lock_fleet(session, actor.tenant_id)
        inputs = route_inputs(session, actor, vehicle_id, plan_id)
        key = canonical_hash("map_route", inputs)
        cache = session.scalar(
            select(m.MapRouteCache).where(
                m.MapRouteCache.tenant_id == actor.tenant_id, m.MapRouteCache.cache_key == key
            )
        )
        if cache and aware(cache.expires_at) > m.utcnow():
            return {**deepcopy(cache.payload), "cached": True}
    # No session/lock survives the provider call.
    result = AMapClient(settings).route(inputs["points"])
    with session_factory() as session, session.begin():
        lock_fleet(session, actor.tenant_id)
        if inputs != route_inputs(session, actor, vehicle_id, plan_id):
            fail("STALE_MAP", "Map inputs changed during provider request")
        payload = {
            **{k: v for k, v in inputs.items() if k not in {"points", "stops", "plan_status"}},
            **result,
            "source": "AMAP",
            "coordinate_system": "GCJ02",
            "geometry_standard": "GCJ02_GEOJSON_SHAPED",
            "routing_profile": "DRIVING_CAR",
            "route_kind": "CANDIDATE_PREVIEW" if plan_id else "CURRENT",
            "retrieved_at": iso(m.utcnow()),
            "cached": False,
        }
        cache = session.scalar(
            select(m.MapRouteCache).where(
                m.MapRouteCache.tenant_id == actor.tenant_id, m.MapRouteCache.cache_key == key
            )
        )
        if cache:
            cache.payload, cache.expires_at = payload, expires(settings.amap_cache_ttl_seconds)
        else:
            session.add(
                m.MapRouteCache(
                    tenant_id=actor.tenant_id,
                    cache_key=key,
                    payload=payload,
                    expires_at=expires(settings.amap_cache_ttl_seconds),
                )
            )
        # Bounded retention: expired entries are removed during successful reads.
        session.execute(
            delete(m.MapRouteCache).where(
                m.MapRouteCache.tenant_id == actor.tenant_id,
                m.MapRouteCache.expires_at <= m.utcnow(),
            )
        )
        return payload


def refresh_inputs(session, actor, expected_version, node_ids=None):
    actor = fresh_actor(session, actor)
    dispatcher(actor)
    graph = graph_for(session, actor)
    if graph.routing_version != expected_version:
        fail("STALE_MAP", "Routing version changed")
    nodes = sorted(rows(session, m.Node, actor.tenant_id), key=lambda n: n.id)
    ids = [n.id for n in nodes]
    if not 1 <= len(ids) <= 21 or (node_ids is not None and sorted(node_ids) != ids):
        fail("INVALID_INPUT", "Refresh must cover every tenant node, at most 21 points", 422)
    missing = missing_coordinates(nodes)
    if missing:
        fail("DATA_INCOMPLETE", "Missing confirmed coordinates", 422, missing_fields=missing)
    return {
        "expected_routing_version": graph.routing_version,
        "graph_id": graph.id,
        "node_versions": {n.id: n.version for n in nodes},
        "points": {n.id: [n.longitude, n.latitude] for n in nodes},
        "fleet_versions": {
            v.id: [v.version, v.route_revision, v.location_node_id]
            for v in rows(session, m.Vehicle, actor.tenant_id)
        },
    }


def submit_refresh(session, actor, body, settings, operation_id):
    inputs = refresh_inputs(session, actor, body.expected_routing_version, body.node_ids)
    if not settings.amap_web_service_key.get_secret_value():
        fail(
            "MAP_UNCONFIGURED",
            "Map provider is not configured",
            503,
            missing_fields=["amap_web_service_key"],
        )
    job = m.Job(
        id=m.new_id(),
        tenant_id=actor.tenant_id,
        user_id=actor.user_id,
        operation_id=operation_id,
        job_type="MAP_ROUTING",
        input=inputs,
        status="QUEUED",
    )
    session.add(job)
    session.add(
        m.OutboxEvent(
            tenant_id=actor.tenant_id,
            event_type="JOB_SUBMITTED",
            payload={"job_id": job.id, "job_type": "MAP_ROUTING"},
        )
    )
    return {
        "job_id": job.id,
        "job_type": "MAP_ROUTING",
        "job_status": "QUEUED",
        "poll_after_seconds": 2,
    }, 202


def _job_inputs(session, job):
    actor = Actor(job.user_id, job.tenant_id, "DISPATCHER", None)
    inputs = refresh_inputs(session, actor, job.input["expected_routing_version"])
    if inputs != job.input:
        fail("STALE_MAP", "Routing inputs changed")
    return inputs


def _failed(job, error):
    job.status, job.error, job.result = "FAILED", error.as_dict(), None
    job.completed_at, job.lease_owner, job.lease_expires_at = m.utcnow(), None, None


def run_refresh(job_id, *, session_factory, settings, worker_id=None):
    owner = f"{worker_id or 'map'}-{m.new_id()}"[:128]
    with session_factory() as session, session.begin():
        initial = session.get(m.Job, job_id)
        if not initial:
            fail("NOT_FOUND", status=404)
        lock_fleet(session, initial.tenant_id)
        job = refresh_locked(session, m.Job, job_id)
        if job.job_type != "MAP_ROUTING":
            fail("INVALID_INPUT", status=422)
        if job.status != "QUEUED":
            return {"job_id": job.id, "job_status": job.status}
        try:
            inputs = deepcopy(_job_inputs(session, job))
        except DomainError as exc:
            _failed(job, exc)
            return {"job_id": job.id, "job_status": job.status}
        job.status, job.lease_owner = "RUNNING", owner
        job.lease_expires_at = expires(settings.amap_total_budget_seconds + 10)
        job.attempts += 1
        job.started_at = m.utcnow()
        fence = job.attempts
    error, matrix = None, None
    try:
        matrix = AMapClient(settings).matrix(inputs["points"])
    except DomainError as exc:
        error = exc
    except Exception:  # noqa: BLE001 - sanitize durable worker failures
        error = DomainError("MAP_UNAVAILABLE", "Map refresh failed", 503)
    with session_factory() as session, session.begin():
        initial = session.get(m.Job, job_id)
        lock_fleet(session, initial.tenant_id)
        job = refresh_locked(session, m.Job, job_id)
        if job.status != "RUNNING" or job.lease_owner != owner or job.attempts != fence:
            return {"job_id": job.id, "job_status": job.status}
        try:
            _job_inputs(session, job)
            if aware(job.lease_expires_at) <= m.utcnow():
                fail("OPERATION_TIMEOUT", "Map execution lease expired", 504)
            if error:
                raise error
        except DomainError as exc:
            _failed(job, exc)
            return {"job_id": job.id, "job_status": job.status}
        graph = session.get(m.RoutingGraph, inputs["graph_id"])
        if graph.source == "SIMULATION" and graph.simulation_matrix is None:
            graph.simulation_matrix = deepcopy(graph.matrix)
            graph.simulation_routing_version = graph.routing_version
        graph.matrix, graph.node_versions, graph.ready = matrix, inputs["node_versions"], True
        graph.source, graph.routing_version = "AMAP", "MAP_" + m.new_id()
        graph.version += 1
        invalidate_graph(session, job.tenant_id)
        job.result = {
            "routing_version": graph.routing_version,
            "source": "AMAP",
            "coordinate_system": "GCJ02",
            "routing_profile": "DRIVING_CAR",
            "node_versions": inputs["node_versions"],
            "node_count": len(matrix),
            "published_at": iso(m.utcnow()),
        }
        job.status, job.error = "SUCCEEDED", None
        job.completed_at, job.lease_owner, job.lease_expires_at = m.utcnow(), None, None
        audit(
            session,
            Actor(job.user_id, job.tenant_id, "DISPATCHER", None),
            "publish_map_routing",
            "JOB",
            job.id,
            {"routing_version": graph.routing_version},
            job.operation_id,
        )
        return {"job_id": job.id, "job_status": job.status}
