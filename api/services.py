import json
import math
import os
import re
from typing import Any

import requests
from django.conf import settings
from django.core.cache import cache

MILES_PER_METER = 0.000621371192
RANGE_MILES = 500.0
MPG = 10.0
MAX_CITY_OFFSET_MILES = 25.0
OSRM_URL = os.environ.get(
    "OSRM_URL", "https://router.project-osrm.org/route/v1/driving"
).rstrip("/")
USER_AGENT = os.environ.get("ROUTING_USER_AGENT", "fuel-route-assessment/1.0")
CACHE_SECONDS = 3600

type Point = tuple[float, float]
type JsonObject = dict[str, Any]
DATA: JsonObject = json.loads(
    (settings.BASE_DIR / "api" / "data.json").read_text(encoding="utf-8")
)


class RouteError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def route(start: JsonObject, finish: JsonObject) -> JsonObject:
    key = f"route:{start['lat']},{start['lon']}:{finish['lat']},{finish['lon']}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    coords = f"{start['lon']},{start['lat']};{finish['lon']},{finish['lat']}"
    try:
        response = requests.get(
            f"{OSRM_URL}/{coords}",
            params={"overview": "full", "geometries": "geojson", "steps": "false"},
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise RouteError("Routing service is temporarily unavailable.", 502) from exc
    if not isinstance(payload, dict):
        raise RouteError("Routing service returned an invalid route.", 502)
    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise RouteError("No drivable route found between those locations.", 422)
    try:
        result = payload["routes"][0]
        distance = float(result["distance"])
        duration = float(result["duration"])
        geometry = result["geometry"]
        coordinates = geometry["coordinates"]
        if (
            not math.isfinite(distance)
            or distance <= 0
            or not math.isfinite(duration)
            or duration < 0
            or geometry["type"] != "LineString"
            or not isinstance(coordinates, list)
            or len(coordinates) < 2
            or any(
                not isinstance(point, list)
                or len(point) != 2
                or any(
                    not isinstance(value, (int, float)) or not math.isfinite(value)
                    for value in point
                )
                for point in coordinates
            )
        ):
            raise ValueError("Invalid route geometry")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RouteError("Routing service returned an invalid route.", 502) from exc
    output = {
        "distance_miles": distance * MILES_PER_METER,
        "duration_hours": duration / 3600,
        "geometry": geometry,
    }
    cache.set(key, output, CACHE_SECONDS)
    return output


def haversine(a: Point, b: Point) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    x = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 3958.7613 * 2 * math.asin(min(1, math.sqrt(x)))


def candidates(geometry: JsonObject, total_miles: float) -> list[JsonObject]:
    points = [(lat, lon) for lon, lat in geometry["coordinates"]]
    if len(points) < 2:
        raise RouteError("Routing service returned an invalid route.", 502)
    lengths = [haversine(a, b) for a, b in zip(points, points[1:], strict=False)]
    cumulative = [0.0]
    for length in lengths:
        cumulative.append(cumulative[-1] + length)
    if cumulative[-1] <= 0:
        raise RouteError("Routing service returned an invalid route.", 502)
    scale = total_miles / cumulative[-1]
    # Spatial buckets avoid comparing every station with every geometry segment.
    buckets = {}
    cell_size = 0.5
    for i, ((a_lat, a_lon), (b_lat, b_lon)) in enumerate(
        zip(points, points[1:], strict=False)
    ):
        for lat_cell in range(
            math.floor((min(a_lat, b_lat) - 0.4) / cell_size),
            math.floor((max(a_lat, b_lat) + 0.4) / cell_size) + 1,
        ):
            for lon_cell in range(
                math.floor((min(a_lon, b_lon) - 0.7) / cell_size),
                math.floor((max(a_lon, b_lon) + 0.7) / cell_size) + 1,
            ):
                buckets.setdefault((lat_cell, lon_cell), []).append(i)
    min_lat = min(p[0] for p in points) - 0.5
    max_lat = max(p[0] for p in points) + 0.5
    min_lon = min(p[1] for p in points) - 0.5
    max_lon = max(p[1] for p in points) + 0.5
    found = []
    for station in DATA["stations"]:
        lat, lon = station["lat"], station["lon"]
        if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
            continue
        segment_indexes = buckets.get(
            (math.floor(lat / cell_size), math.floor(lon / cell_size)), ()
        )
        best = (float("inf"), 0.0, points[0])
        for i in segment_indexes:
            a_lat, a_lon = points[i]
            b_lat, b_lon = points[i + 1]
            cos_lat = math.cos(math.radians((a_lat + b_lat) / 2))
            dx = (b_lon - a_lon) * 69.172 * cos_lat
            dy = (b_lat - a_lat) * 69.0
            px = (lon - a_lon) * 69.172 * cos_lat
            py = (lat - a_lat) * 69.0
            t = (
                max(0.0, min(1.0, (px * dx + py * dy) / (dx * dx + dy * dy)))
                if dx * dx + dy * dy
                else 0.0
            )
            distance = math.hypot(px - t * dx, py - t * dy)
            if distance < best[0]:
                best = (
                    distance,
                    (cumulative[i] + t * lengths[i]) * scale,
                    [a_lon + t * (b_lon - a_lon), a_lat + t * (b_lat - a_lat)],
                )
        if best[0] <= MAX_CITY_OFFSET_MILES and 0 < best[1] < total_miles:
            found.append(
                {
                    **station,
                    "offset_miles": best[0],
                    "mile_marker": best[1],
                    "route_point": best[2],
                }
            )
    # Same-city stations have the same approximate coordinates. Keep the cheapest.
    by_position = {}
    for station in found:
        key = (round(station["mile_marker"], 1), station["city"], station["state"])
        if key not in by_position or station["price"] < by_position[key]["price"]:
            by_position[key] = station
    return sorted(by_position.values(), key=lambda s: s["mile_marker"])


def optimize(
    stations: list[JsonObject], start: JsonObject, total_miles: float
) -> tuple[list[JsonObject], JsonObject]:
    all_stations = DATA["stations"]
    origin = min(
        all_stations,
        key=lambda s: haversine((start["lat"], start["lon"]), (s["lat"], s["lon"])),
    )
    start_price = origin["price"]
    nodes = (
        [{"mile_marker": 0.0, "price": start_price, "origin": True}]
        + stations
        + [{"mile_marker": total_miles, "price": -1, "destination": True}]
    )
    current = 0
    fuel = 0.0
    purchases = []
    while current < len(nodes) - 1:
        here = nodes[current]
        reachable = [
            i
            for i in range(current + 1, len(nodes))
            if nodes[i]["mile_marker"] - here["mile_marker"] <= RANGE_MILES
        ]
        if not reachable:
            raise RouteError(
                "No fuel station coverage within the vehicle's 500-mile range on this route.",
                422,
            )
        cheaper = next(
            (i for i in reachable if nodes[i]["price"] < here["price"]), None
        )
        target = (
            cheaper
            if cheaper is not None
            else min(
                reachable, key=lambda i: (nodes[i]["price"], -nodes[i]["mile_marker"])
            )
        )
        distance = nodes[target]["mile_marker"] - here["mile_marker"]
        desired = (
            distance / MPG
            if cheaper is not None
            else min(RANGE_MILES / MPG, (total_miles - here["mile_marker"]) / MPG)
        )
        gallons = max(0.0, desired - fuel)
        if gallons > 1e-8:
            purchases.append(
                {"node": here, "gallons": gallons, "cost": gallons * here["price"]}
            )
        fuel = max(0.0, fuel + gallons - distance / MPG)
        current = target
    return purchases, origin


def plan(start_text: object, finish_text: object) -> JsonObject:
    locations = []
    for value in (start_text, finish_text):
        if not isinstance(value, str) or "," not in value:
            raise RouteError(
                "Use a US city and two-letter state, for example 'Chicago, IL'."
            )
        city, state = (part.strip() for part in value.rsplit(",", 1))
        if not city or len(state) != 2:
            raise RouteError(
                "Use a US city and two-letter state, for example 'Chicago, IL'."
            )
        normalized_city = re.sub(r"\bsaint\b", "st", city, flags=re.I).casefold()
        normalized_city = re.sub(r"[^a-z0-9]+", "", normalized_city)
        key = f"{normalized_city},{state.upper()}"
        point = DATA["places"].get(key)
        if point is None:
            raise RouteError(f"Unknown US city: {value}.")
        locations.append(
            {"label": f"{city}, {state.upper()}", "lat": point[0], "lon": point[1]}
        )
    start, finish = locations
    if (start["lat"], start["lon"]) == (finish["lat"], finish["lon"]):
        raise RouteError("Start and finish must be different locations.")
    result_key = f"plan:{start['lat']},{start['lon']}:{finish['lat']},{finish['lon']}"
    cached = cache.get(result_key)
    if cached is not None:
        return cached
    road = route(start, finish)
    stations = candidates(road["geometry"], road["distance_miles"])
    purchases, origin = optimize(stations, start, road["distance_miles"])
    stops = []
    for purchase in purchases:
        node = purchase["node"]
        if node.get("origin"):
            continue
        stops.append(
            {
                "station_id": node["id"],
                "name": node["name"],
                "address": node["address"],
                "city": node["city"],
                "state": node["state"],
                "price_per_gallon_usd": round(node["price"], 3),
                "gallons": round(purchase["gallons"], 2),
                "cost_usd": round(purchase["cost"], 2),
                "mile_marker": round(node["mile_marker"], 1),
                "route_point": node["route_point"],
                "station_city_center": [node["lon"], node["lat"]],
                "city_center_offset_miles": round(node["offset_miles"], 1),
            }
        )
    initial = next((p for p in purchases if p["node"].get("origin")), None)
    result = {
        "start": start,
        "finish": finish,
        "distance_miles": round(road["distance_miles"], 1),
        "duration_hours": round(road["duration_hours"], 1),
        "route": road["geometry"],
        "fuel_stops": stops,
        "initial_fuel": {
            "gallons": round(initial["gallons"], 2) if initial else 0,
            "price_per_gallon_usd": round(origin["price"], 3),
            "cost_usd": round(initial["cost"], 2) if initial else 0,
            "price_basis": f"Nearest listed station to origin: {origin['name']}, {origin['city']}, {origin['state']}",
        },
        "total_fuel_gallons": round(sum(p["gallons"] for p in purchases), 2),
        "total_fuel_cost_usd": round(sum(p["cost"] for p in purchases), 2),
        "assumptions": {
            "range_miles": RANGE_MILES,
            "mpg": MPG,
            "initial_tank": "empty; purchase at origin priced by nearest listed station",
            "station_coordinates": "US Census city centers; verify exact address and road access before travel",
            "detours": "not included in distance or fuel cost",
        },
        "attribution": {
            "routing": "OSRM / OpenStreetMap contributors",
            "places": "US Census 2025 Gazetteer",
        },
    }
    cache.set(result_key, result, CACHE_SECONDS)
    return result
