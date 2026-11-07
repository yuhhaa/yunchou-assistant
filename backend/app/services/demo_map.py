"""User-authorized Wuhan schematic dataset; no provider calls or geography writes."""

from app import models as m
from app.services.common import fail, iso
from app.services.maps import fresh_actor, map_data, route_selection, selected_vehicles

POINTS = {
    "W": (114.30, 30.59),
    "A": (114.32, 30.60),
    "B": (114.31, 30.62),
    "C": (114.35, 30.61),
    "D": (114.28, 30.61),
    "E": (114.27, 30.58),
    "F": (114.31, 30.56),
    "G": (114.34, 30.57),
    "H": (114.36, 30.59),
}


def demo_map(session, actor, settings, vehicle_id=None, plan_id=None):
    actor = fresh_actor(session, actor)
    metadata = map_data(session, actor, settings, vehicle_id)
    inputs = [
        route_selection(session, actor, v.id, plan_id)
        for v in selected_vehicles(session, actor, vehicle_id)
    ]
    ids = {p["node_id"] for p in metadata["points"]}
    ids.update(n for route in inputs for n in route["node_sequence"])
    missing = sorted(ids - POINTS.keys())
    if missing:
        fail(
            "DATA_INCOMPLETE",
            "Node is outside the Wuhan demo dataset",
            422,
            missing_fields=["simulation_nodes." + n for n in missing],
        )
    routes = []
    for selection in inputs:
        coordinates = []
        for node in selection["node_sequence"]:
            pair = list(POINTS[node])
            if not coordinates or pair != coordinates[-1]:
                coordinates.append(pair)
        routes.append(
            {
                "vehicle_id": selection["vehicle_id"],
                "route_revision": selection["route_revision"],
                "plan_id": selection["plan_id"],
                "plan_version": selection["plan_version"],
                "route_kind": "CANDIDATE_PREVIEW" if plan_id else "CURRENT",
                "node_sequence": selection["node_sequence"],
                "geometry_kind": "SCHEMATIC",
                "geometry": {"type": "LineString", "coordinates": coordinates}
                if len(coordinates) > 1
                else None,
                "distance_m": None,
                "duration_seconds": None,
            }
        )
    return {
        "city": "武汉市",
        "source": "SIMULATION",
        "simulation": True,
        "dataset_version": "WUHAN_DEMO_V1",
        "coordinate_system": "GCJ02",
        "road_service_used": False,
        "center": [114.30, 30.59],
        "clock_mode": metadata["clock_mode"],
        "clock_time": metadata["clock_time"],
        "calculation_routing_source": metadata["routing_source"],
        "calculation_routing_version": metadata["routing_version"],
        "retrieved_at": iso(m.utcnow()),
        "notice": "武汉模拟点位与示意连线；不代表真实地址、道路或实时GPS，里程和ETA请读取业务分析。",
        "points": [
            {
                "node_id": n,
                "name": "模拟仓库W" if n == "W" else "模拟配送点" + n,
                "longitude": POINTS[n][0],
                "latitude": POINTS[n][1],
                "source": "SIMULATION",
            }
            for n in sorted(ids)
        ],
        "routes": routes,
    }
