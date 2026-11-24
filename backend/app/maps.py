"""Bounded high-level AMap adapter; outputs contain no provider URLs or credentials."""

import asyncio
import logging
import math
from itertools import pairwise

import httpx

from app.errors import DomainError


class _HideMapURLs(logging.Filter):
    def filter(self, record):
        # httpx's INFO request line includes query credentials. Never emit it.
        return "restapi.amap.com" not in record.getMessage()


logging.getLogger("httpx").addFilter(_HideMapURLs())


def _coordinate(value):
    parts = value.split(",")
    if len(parts) != 2:
        raise ValueError("Invalid coordinate")
    point = [float(p) for p in parts]
    if not all(math.isfinite(p) for p in point) or not (
        -180 <= point[0] <= 180 and -90 <= point[1] <= 90
    ):
        raise ValueError("Invalid coordinate")
    return point


def _number(value):
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError("Invalid cost")
    return math.ceil(number)


def _text(value):
    return value if isinstance(value, str) and value else None


def geometry_parts(geometry):
    if not geometry:
        return []
    return (
        [geometry["coordinates"]] if geometry["type"] == "LineString" else geometry["coordinates"]
    )


def road_geometry(parts):
    merged = []
    for part in parts:
        if merged and merged[-1][-1] == part[0]:
            merged[-1].extend(part[1:])
        else:
            merged.append(list(part))
    if not merged:
        return None
    if len(merged) == 1:
        return {"type": "LineString", "coordinates": merged[0]}
    return {"type": "MultiLineString", "coordinates": merged}


def _gap_metres(a, b):
    return 111_320 * math.hypot(
        (a[0] - b[0]) * math.cos(math.radians((a[1] + b[1]) / 2)), a[1] - b[1]
    )


class AMapClient:
    def __init__(self, settings, transport=None):
        self.settings, self.transport = settings, transport

    def _run(self, work):
        key = self.settings.amap_web_service_key.get_secret_value()
        if not key:
            raise DomainError(
                "MAP_UNCONFIGURED",
                "Map provider is not configured",
                503,
                missing_fields=["amap_web_service_key"],
            )

        async def execute():
            async with asyncio.timeout(self.settings.amap_total_budget_seconds):
                async with httpx.AsyncClient(
                    transport=self.transport,
                    trust_env=False,
                    follow_redirects=False,
                    timeout=httpx.Timeout(self.settings.amap_request_timeout_seconds, connect=3),
                ) as client:
                    return await work(client, key)

        try:
            return asyncio.run(execute())
        except DomainError:
            raise
        except TimeoutError:
            raise DomainError(
                "MAP_TIMEOUT", "Map request budget expired", 504, retryable=True
            ) from None
        except (
            httpx.HTTPError,
            ValueError,
            TypeError,
            KeyError,
            IndexError,
            OverflowError,
            AttributeError,
        ):
            raise DomainError(
                "MAP_UNAVAILABLE",
                "Map provider response unavailable or invalid",
                503,
                retryable=True,
            ) from None

    async def _request(self, client, key, path, params):
        async with client.stream(
            "GET", "https://restapi.amap.com" + path, params={**params, "key": key}
        ) as response:
            response.raise_for_status()
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > 2_000_000:
                    raise ValueError("Map response too large")
            import json

            data = json.loads(content)
        if data.get("status") != "1":
            if str(data.get("infocode")) in {"10004", "10014", "10019", "10020", "10021"}:
                raise DomainError(
                    "MAP_RATE_LIMITED", "高德道路服务限流，请稍后重试", 503, retryable=True
                )
            raise DomainError(
                "MAP_UNAVAILABLE", "Map provider rejected request", 503, retryable=True
            )
        return data

    def geocode(self, address, city=None):
        async def work(client, key):
            data = await self._request(
                client,
                key,
                "/v3/geocode/geo",
                {"address": address, **({"city": city} if city else {})},
            )
            return [
                {
                    "longitude": _coordinate(g["location"])[0],
                    "latitude": _coordinate(g["location"])[1],
                    "formatted_address": _text(g.get("formatted_address")),
                    "province": _text(g.get("province")),
                    "city": _text(g.get("city")),
                    "district": _text(g.get("district")),
                    "adcode": _text(g.get("adcode")),
                }
                for g in data["geocodes"]
            ]

        return self._run(work)

    async def _leg(self, client, key, origin, destination, *, bounds=None):
        data = await self._request(
            client,
            key,
            "/v5/direction/driving",
            {
                "origin": f"{origin[0]:.6f},{origin[1]:.6f}",
                "destination": f"{destination[0]:.6f},{destination[1]:.6f}",
                "show_fields": "cost,polyline",
                "strategy": "32",
                **({"avoidpolygons": bounds.provider_value()} if bounds else {}),
            },
        )
        paths = data["route"]["paths"]
        if not paths:
            raise DomainError("MAP_NO_ROUTE", "Provider found no driving route", 422)
        for path in paths if bounds else paths[:1]:
            candidate = self._path(path)
            parts = geometry_parts(candidate["geometry"])
            if bounds is None or not (
                any(bounds.intersects(part) for part in parts)
                or any(bounds.intersects([a[-1], b[0]]) for a, b in pairwise(parts))
            ):
                return candidate
        raise DomainError("MAP_NO_ROUTE", "No route avoids the confirmed area", 422)

    @staticmethod
    def _path(path):
        parts = []
        for step in path["steps"]:
            segment = [_coordinate(text) for text in step["polyline"].split(";")]
            points = [p for i, p in enumerate(segment) if i == 0 or p != segment[i - 1]]
            if len(points) < 2:
                continue
            if parts and _gap_metres(parts[-1][-1], points[0]) > 100:
                raise ValueError("Disconnected road geometry")
            parts.append(points)
        geometry = road_geometry(parts)
        if not geometry:
            raise ValueError("Missing road geometry")
        return {
            "distance_m": _number(path["distance"]),
            "duration_seconds": _number(path["cost"]["duration"]),
            "geometry": geometry,
        }

    def incident_legs(self, location, destinations, remaining, bounds):
        """Resolve one observed origin and cost the bounded set of needed edges."""

        async def work(client, key):
            origin = [location["longitude"], location["latitude"]]
            loop = asyncio.get_running_loop()
            next_request = loop.time()

            async def paced(call):
                nonlocal next_request
                for attempt in range(3):
                    await asyncio.sleep(max(0, next_request - loop.time()))
                    next_request = loop.time() + self.settings.amap_road_min_interval_seconds
                    try:
                        return await call()
                    except DomainError as error:
                        if error.code != "MAP_RATE_LIMITED" or attempt == 2:
                            raise
                        next_request = max(next_request, loop.time() + 1 + attempt)

            if location["coordinate_system"] == "WGS84":
                converted = await paced(
                    lambda: self._request(
                        client,
                        key,
                        "/v3/assistant/coordinate/convert",
                        {
                            "locations": f"{origin[0]:.6f},{origin[1]:.6f}",
                            "coordsys": "gps",
                        },
                    )
                )
                origin = _coordinate(converted["locations"])
            points = {**destinations, "ROAD_ORIGIN": origin}
            direct_pairs = set(pairwise(["ROAD_ORIGIN", *remaining]))
            avoid_pairs = {(a, b) for a in points for b in destinations if a != b}
            avoid_pairs.update((b, b) for b in destinations)

            async def cost(pair, area):
                a, b = (points[n] for n in pair)
                if a == b and (area is None or not area.contains(a)):
                    return {"distance_m": 0, "duration_seconds": 0, "geometry": None}
                if area and (area.contains(a) or area.contains(b)):
                    return None
                try:
                    leg = await paced(lambda: self._leg(client, key, a, b, bounds=area))
                    parts = geometry_parts(leg["geometry"])
                    first, last = parts[0][0], parts[-1][-1]
                    if any(_gap_metres(p, q) > 100 for p, q in zip((a, b), (first, last))):
                        raise ValueError("Driving path does not reach the requested points")
                    if area and (area.intersects([a, first]) or area.intersects([last, b])):
                        return None
                    return leg
                except DomainError as err:
                    if err.code != "MAP_NO_ROUTE":
                        raise
                    return None

            direct_order, avoid_order = sorted(direct_pairs), sorted(avoid_pairs)
            tasks = [(p, None) for p in direct_order] + [(p, bounds) for p in avoid_order]
            legs = [await cost(pair, area) for pair, area in tasks]
            return (
                origin,
                dict(zip(direct_order, legs[: len(direct_order)])),
                dict(zip(avoid_order, legs[len(direct_order) :])),
            )

        return self._run(work)

    def route(self, points):
        async def work(client, key):
            distance, duration, parts = 0, 0, []
            for origin, destination in pairwise(points):
                if origin == destination:
                    continue
                leg = await self._leg(client, key, origin, destination)
                distance += leg["distance_m"]
                duration += leg["duration_seconds"]
                parts.extend(geometry_parts(leg["geometry"]))
            return {
                "distance_m": distance,
                "duration_seconds": duration,
                "geometry": road_geometry(parts),
            }

        return self._run(work)

    def matrix(self, nodes):
        if not 1 <= len(nodes) <= 21:
            raise DomainError("INVALID_INPUT", "Routing requires 1..21 points", 422)

        async def work(client, key):
            matrix = {}
            for source, origin in nodes.items():
                matrix[source] = {}
                for target, destination in nodes.items():
                    if source == target or origin == destination:
                        distance, duration = 0, 0
                    else:
                        leg = await self._leg(client, key, origin, destination)
                        distance, duration = leg["distance_m"], leg["duration_seconds"]
                    matrix[source][target] = {
                        "distance_m": distance,
                        "duration_seconds": duration,
                        "minutes": math.ceil(duration / 60),
                    }
            return matrix

        return self._run(work)
