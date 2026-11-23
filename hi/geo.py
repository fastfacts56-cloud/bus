"""
Map/route maths.

All distances are in metres. Route geometry is stored as a list of
[latitude, longitude] pairs, exactly what Leaflet uses.

The important function is project_point_to_route(): it finds how far along the
saved road route a GPS position is. ETA and stop ordering are based on this
road distance, never on a straight line to the school.
"""

import math

EARTH_RADIUS_M = 6371000


def haversine_m(lat1, lon1, lat2, lon2):
    """Distance between two coordinates along the Earth's surface."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def cumulative_distances(geometry):
    """
    Distance from the start of the route to every geometry point.
    Example: [0, 12.5, 40.2, ...] (metres).
    """
    cumulative = [0.0]
    for index in range(1, len(geometry)):
        previous = geometry[index - 1]
        current = geometry[index]
        cumulative.append(
            cumulative[-1] + haversine_m(previous[0], previous[1], current[0], current[1])
        )
    return cumulative


def project_point_to_route(lat, lon, geometry):
    """
    Find the nearest point on the route polyline to (lat, lon).

    Returns a dict with:
      distance_along_m : how far along the route the nearest point is
      lat, lon         : the snapped point on the route
      offset_m         : how far the original point is from the route
    """
    if len(geometry) < 2:
        if geometry:
            return {
                "distance_along_m": 0.0,
                "lat": geometry[0][0],
                "lon": geometry[0][1],
                "offset_m": haversine_m(lat, lon, geometry[0][0], geometry[0][1]),
            }
        return {"distance_along_m": 0.0, "lat": lat, "lon": lon, "offset_m": 0.0}

    # Work in a local flat coordinate system around the GPS point. For the
    # short distances involved (a city route), this is accurate and fast.
    latitude_scale = math.radians(1) * EARTH_RADIUS_M
    longitude_scale = latitude_scale * math.cos(math.radians(lat))

    cumulative = cumulative_distances(geometry)
    best = None

    for index in range(len(geometry) - 1):
        start = geometry[index]
        end = geometry[index + 1]

        start_x = math.radians(start[1] - lon) * longitude_scale
        start_y = math.radians(start[0] - lat) * latitude_scale
        end_x = math.radians(end[1] - lon) * longitude_scale
        end_y = math.radians(end[0] - lat) * latitude_scale

        segment_x = end_x - start_x
        segment_y = end_y - start_y
        segment_length_squared = segment_x * segment_x + segment_y * segment_y

        if segment_length_squared == 0:
            fraction = 0.0
        else:
            # Project the origin (our GPS point) onto the segment.
            fraction = -(start_x * segment_x + start_y * segment_y) / segment_length_squared
            fraction = max(0.0, min(1.0, fraction))

        closest_x = start_x + fraction * segment_x
        closest_y = start_y + fraction * segment_y
        offset = math.hypot(closest_x, closest_y)

        snapped_lat = start[0] + fraction * (end[0] - start[0])
        snapped_lon = start[1] + fraction * (end[1] - start[1])
        segment_length = haversine_m(start[0], start[1], end[0], end[1])
        distance_along = cumulative[index] + fraction * segment_length

        if best is None or offset < best["offset_m"]:
            best = {
                "distance_along_m": distance_along,
                "lat": snapped_lat,
                "lon": snapped_lon,
                "offset_m": offset,
            }

    return best


def bearing_degrees(lat1, lon1, lat2, lon2):
    """Compass direction (0 = north, 90 = east) from point 1 to point 2."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_lon = math.radians(lon2 - lon1)
    y = math.sin(delta_lon) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(delta_lon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def point_at_distance(geometry, cumulative, distance):
    """Return the [lat, lon] at a given distance along the route."""
    if not geometry:
        return None
    distance = max(0.0, min(distance, cumulative[-1]))
    for index in range(1, len(geometry)):
        if cumulative[index] >= distance:
            previous = geometry[index - 1]
            current = geometry[index]
            segment_length = cumulative[index] - cumulative[index - 1]
            if segment_length == 0:
                return current
            fraction = (distance - cumulative[index - 1]) / segment_length
            return [
                previous[0] + fraction * (current[0] - previous[0]),
                previous[1] + fraction * (current[1] - previous[1]),
            ]
    return geometry[-1]


def reverse_geometry(geometry):
    """Return the route geometry in the opposite direction."""
    return list(reversed(geometry))


def route_ends_near_school(geometry, school_lat, school_lon):
    """
    Check whether the route ends closer to the school than it starts.

    Routes are always stored so that the school is at the END. This makes
    "morning = increasing distance" and "afternoon = decreasing distance"
    simple and reliable. If the admin drew it backwards we reverse it.
    """
    if len(geometry) < 2 or school_lat is None or school_lon is None:
        return True
    start_distance = haversine_m(geometry[0][0], geometry[0][1], school_lat, school_lon)
    end_distance = haversine_m(geometry[-1][0], geometry[-1][1], school_lat, school_lon)
    return end_distance <= start_distance
