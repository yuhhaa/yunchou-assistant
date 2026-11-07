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

    async def _leg(self, client, key, origin, destination):
        data = await self._request(
            client,
            key,
            "/v5/direction/driving",
            {
                "origin": f"{origin[0]:.6f},{origin[1]:.6f}",
                "destination": f"{destination[0]:.6f},{destination[1]:.6f}",
                "show_fields": "cost,polyline",
                "strategy": "32",
            },
        )
        paths = data["route"]["paths"]
        if not paths:
            raise DomainError("MAP_NO_ROUTE", "Provider found no driving route", 422)
        path, points = paths[0], []
        for step in path["steps"]:
            segment = [_coordinate(text) for text in step["polyline"].split(";")]
            if points and points[-1] != segment[0]:
                raise ValueError("Disconnected road geometry")
            for point in segment:
                if not points or point != points[-1]:
                    points.append(point)
        if len(points) < 2:
            raise ValueError("Missing road geometry")
        return {
            "distance_m": _number(path["distance"]),
            "duration_seconds": _number(path["cost"]["duration"]),
            "geometry": {"type": "LineString", "coordinates": points},
        }

    def route(self, points):
        async def work(client, key):
            distance, duration, geometry = 0, 0, []
            for origin, destination in pairwise(points):
                if origin == destination:
                    continue
                leg = await self._leg(client, key, origin, destination)
                distance += leg["distance_m"]
                duration += leg["duration_seconds"]
                if geometry and geometry[-1] != leg["geometry"]["coordinates"][0]:
                    raise ValueError("Disconnected route legs")
                for point in leg["geometry"]["coordinates"]:
                    if not geometry or geometry[-1] != point:
                        geometry.append(point)
            return {
                "distance_m": distance,
                "duration_seconds": duration,
                "geometry": {"type": "LineString", "coordinates": geometry} if geometry else None,
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
