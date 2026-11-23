"""
Road routing through the public OSRM service.

OSRM receives the points the admin clicked and returns a road-following shape.
We never draw straight lines between clicked points.

Public OSRM is a free demonstration service. This app only calls it when the
admin explicitly previews or saves a route, and the URL can be changed in .env.
Respect the service's usage policy if you change to a bigger deployment.
"""

import requests

from geo import route_ends_near_school


class RoutingError(Exception):
    """Raised when OSRM cannot calculate a route."""


def calculate_route(waypoints, osrm_base_url, timeout=20):
    """
    Ask OSRM for a driving route through the given waypoints.

    waypoints: list of [lat, lon] (at least two points)
    Returns: {"geometry": [[lat, lon], ...], "distance_m": float}
    """
    if len(waypoints) < 2:
        raise RoutingError("Add at least two route points.")
    if len(waypoints) > 100:
        raise RoutingError("Too many route points. Use at most 100.")

    # OSRM expects longitude,latitude pairs.
    coordinates = ";".join(f"{float(point[1]):.6f},{float(point[0]):.6f}" for point in waypoints)
    url = (
        f"{osrm_base_url}/route/v1/driving/{coordinates}"
        "?overview=full&geometries=geojson&steps=false"
    )

    try:
        response = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "SchoolBusTracker/1.0 (single school deployment)"},
        )
    except requests.RequestException as error:
        raise RoutingError(
            "Could not reach the routing service. Check the internet connection and OSRM_BASE_URL."
        ) from error

    if response.status_code != 200:
        raise RoutingError(
            f"The routing service returned HTTP {response.status_code}. "
            "Check that the points are on or near a road."
        )

    try:
        data = response.json()
    except ValueError as error:
        raise RoutingError("The routing service returned an invalid response.") from error

    if data.get("code") != "Ok" or not data.get("routes"):
        message = data.get("message") or data.get("code") or "No route found"
        raise RoutingError(f"No road route found: {message}")

    route = data["routes"][0]
    geometry = route.get("geometry", {}).get("coordinates", [])
    if len(geometry) < 2:
        raise RoutingError("The routing service returned an empty route.")

    # OSRM gives [lon, lat]; Leaflet and the rest of our app use [lat, lon].
    lat_lon_geometry = [[point[1], point[0]] for point in geometry]
    distance_m = float(route.get("distance", 0))

    return {"geometry": lat_lon_geometry, "distance_m": distance_m}


def orient_route_towards_school(geometry, waypoints, school):
    """
    Make sure the route ends near the school.

    Morning trips then always mean "increasing distance along the route" and
    afternoon trips mean "decreasing distance". This avoids a whole class of
    direction bugs. The admin does not need to think about which end to draw
    first, although DETAIL.md explains the convention.
    """
    if school and not route_ends_near_school(geometry, school["lat"], school["lon"]):
        return list(reversed(geometry)), list(reversed(waypoints))
    return geometry, waypoints
