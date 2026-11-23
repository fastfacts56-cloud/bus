"""
API used only by the ESP32.

Pairing:
    POST /api/esp32/pair   {"code": "ABCD...", "hardware_id": "..."}
    -> returns a device token ONCE. The ESP32 stores it in NVS.

GPS:
    POST /api/esp32/gps    Authorization: Bearer <device token>
    -> accepted only if the token is valid and the bus has an active trip.

These endpoints deliberately have no CSRF token: they are not browser sessions.
They are rate limited and the device token is stored only as a SHA-256 hash.
"""

import hashlib
import secrets

from flask import Blueprint, current_app, jsonify, request
from sqlite3 import IntegrityError

from database import get_db
from geo import bearing_degrees, haversine_m, project_point_to_route
from notifications import start_notification_check
from utils import RateLimiter, client_ip, now_iso, parse_iso, seconds_since

esp32_bp = Blueprint("esp32_api", __name__, url_prefix="/api/esp32")

pair_limiter = RateLimiter()
gps_limiter = RateLimiter()

PAIRING_CODE_LENGTH = 12
MAX_GPS_ACCURACY_M = 5000


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def json_error(message, status=400, **extra):
    payload = {"ok": False, "error": message}
    payload.update(extra)
    return jsonify(payload), status


# ---------------------------------------------------------------------------
# Pairing
# ---------------------------------------------------------------------------

@esp32_bp.post("/pair")
def pair_device():
    if not pair_limiter.allow(f"pair:{client_ip(request)}", limit=10, window_seconds=300):
        return json_error("Too many pairing attempts. Wait a few minutes.", 429)

    data = request.get_json(silent=True) or {}
    code = str(data.get("code", "")).strip().upper().replace(" ", "")
    hardware_id = str(data.get("hardware_id", ""))[:100]

    if len(code) != PAIRING_CODE_LENGTH:
        return json_error("Invalid or expired pairing code.", 401)

    code_hash = sha256(code)
    db = get_db()
    row = db.execute(
        """
        SELECT pairing_codes.*, buses.name AS bus_name,
               buses.registration_number, buses.route_id
        FROM pairing_codes
        JOIN buses ON buses.id = pairing_codes.bus_id
        WHERE pairing_codes.code_hash = ?
        """,
        (code_hash,),
    ).fetchone()

    if row is None or row["used_at"] is not None:
        return json_error("Invalid or expired pairing code.", 401)

    code_age = seconds_since(row["expires_at"])
    # A future expiry gives a negative "seconds since", so a positive value
    # means the five-minute window has ended.
    if code_age is None or code_age > 0:
        return json_error("Invalid or expired pairing code.", 401)

    if db.execute("SELECT 1 FROM devices WHERE bus_id = ?", (row["bus_id"],)).fetchone():
        return json_error(
            "This bus already has a paired device. Ask the admin to unpair it first.", 409
        )

    device_token = secrets.token_urlsafe(32)

    try:
        # Mark the code used first; the WHERE clause makes this one-time even
        # if two devices try the same code at the same moment.
        used = db.execute(
            "UPDATE pairing_codes SET used_at = ? WHERE id = ? AND used_at IS NULL",
            (now_iso(), row["id"]),
        )
        if used.rowcount == 0:
            db.rollback()
            return json_error("Invalid or expired pairing code.", 401)

        db.execute(
            """
            INSERT INTO devices (bus_id, token_hash, hardware_id, paired_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (row["bus_id"], sha256(device_token), hardware_id, now_iso(), None),
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        return json_error("Pairing failed because the device state changed. Try again.", 409)

    return jsonify({
        "ok": True,
        "device_token": device_token,
        "bus_id": row["bus_id"],
        "bus_name": row["bus_name"],
        "registration_number": row["registration_number"],
        "message": "Paired successfully. Store the device token securely.",
    })


# ---------------------------------------------------------------------------
# GPS updates
# ---------------------------------------------------------------------------

def get_device_from_token():
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    token = header[7:].strip()
    if not token:
        return None
    return get_db().execute(
        """
        SELECT devices.*, buses.name AS bus_name, buses.registration_number,
               buses.route_id, buses.driver_id
        FROM devices
        JOIN buses ON buses.id = devices.bus_id
        WHERE devices.token_hash = ?
        """,
        (sha256(token),),
    ).fetchone()


@esp32_bp.get("/status")
def device_status():
    """
    Let the ESP32 check whether it should send GPS now.
    The device uses this to avoid sending when the bus has no active trip.
    """
    device = get_device_from_token()
    if device is None:
        return json_error("Invalid device credential.", 401)

    db = get_db()
    trip = db.execute(
        "SELECT * FROM trips WHERE bus_id = ? AND ended_at IS NULL", (device["bus_id"],)
    ).fetchone()
    timestamp = now_iso()
    db.execute("UPDATE devices SET last_seen_at = ? WHERE id = ?", (timestamp, device["id"]))
    db.commit()

    return jsonify({
        "ok": True,
        "paired": True,
        "bus_id": device["bus_id"],
        "bus_name": device["bus_name"],
        "registration_number": device["registration_number"],
        "active_trip": trip is not None,
        "trip_type": trip["trip_type"] if trip else None,
        "server_time": timestamp,
    })


@esp32_bp.post("/gps")
def receive_gps():
    device = get_device_from_token()
    if device is None:
        return json_error("Invalid device credential.", 401)

    if not gps_limiter.allow(f"gps:{device['id']}", limit=3, window_seconds=3):
        return json_error("GPS updates are too frequent.", 429)

    data = request.get_json(silent=True) or {}
    try:
        lat = float(data["lat"])
        lon = float(data["lon"])
        speed = float(data.get("speed", 0) or 0)
        accuracy = float(data.get("accuracy", 0) or 0)
        heading = data.get("heading")
        satellites = data.get("satellites")
    except (KeyError, TypeError, ValueError):
        return json_error("lat and lon are required numbers.", 400)

    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return json_error("Invalid GPS coordinates.", 400)
    if not (0 <= speed <= 60):
        return json_error("Invalid GPS speed.", 400)
    if not (0 <= accuracy <= MAX_GPS_ACCURACY_M):
        return json_error("Invalid GPS accuracy.", 400)
    if heading is not None:
        try:
            heading = float(heading) % 360
        except (TypeError, ValueError):
            heading = None
    if satellites is not None:
        try:
            satellites = max(0, min(50, int(satellites)))
        except (TypeError, ValueError):
            satellites = None

    db = get_db()
    trip = db.execute(
        "SELECT * FROM trips WHERE bus_id = ? AND ended_at IS NULL", (device["bus_id"],)
    ).fetchone()
    if trip is None:
        # Normal when the driver has not started a trip. The firmware stays
        # quiet until the next active-trip check.
        return json_error("No active trip for this bus. GPS was not stored.", 409,
                          accepted=False, reason="no_active_trip")

    live = db.execute("SELECT * FROM bus_live WHERE bus_id = ?", (device["bus_id"],)).fetchone()

    # Reject impossible jumps (bad NEO-6M fixes, or a spoofed request).
    if live and live["lat"] is not None and live["last_update_at"]:
        elapsed = seconds_since(live["last_update_at"]) or 0
        if elapsed < 300:
            jump = haversine_m(live["lat"], live["lon"], lat, lon)
            allowed = max(300.0, elapsed * current_app.config["MAX_PLAUSIBLE_SPEED_MPS"] + 150)
            if jump > allowed:
                return json_error("GPS jump rejected as implausible.", 422,
                                  accepted=False, reason="gps_jump")

    # Road progress (distance along the saved route).
    progress = None
    if device["route_id"] is not None:
        route = db.execute(
            "SELECT geometry_json FROM routes WHERE id = ?", (device["route_id"],)
        ).fetchone()
        if route:
            import json
            geometry = json.loads(route["geometry_json"])
            projection = project_point_to_route(lat, lon, geometry)
            progress = projection["distance_along_m"]

    # Smooth the speed so the ETA does not jump around.
    previous_smooth = (live["speed_smooth_mps"] if live else 0) or 0
    speed_smooth = speed if previous_smooth <= 0 else (previous_smooth * 0.7 + speed * 0.3)

    # For ETA, remember the last clearly moving speed during short stops.
    previous_eta_speed = (live["eta_speed_mps"] if live else 0) or 0
    if speed >= 2.0:
        eta_speed = speed if previous_eta_speed <= 0 else (previous_eta_speed * 0.8 + speed * 0.2)
    else:
        eta_speed = previous_eta_speed
    eta_speed = max(0, min(eta_speed, 25))

    # Movement detection: GPS speed or a real change in route progress.
    last_movement_at = (live["last_movement_at"] if live else None) or now_iso()
    previous_progress = live["progress_m"] if live else None
    moved = speed >= 1.0
    if progress is not None and previous_progress is not None:
        if abs(progress - previous_progress) >= 8:
            moved = True
    if moved:
        last_movement_at = now_iso()

    # Heading: use the GPS course when available, otherwise the last movement.
    if heading is None and live and live["lat"] is not None:
        if haversine_m(live["lat"], live["lon"], lat, lon) > 5:
            heading = bearing_degrees(live["lat"], live["lon"], lat, lon)
    if heading is None:
        heading = live["heading"] if live else None

    timestamp = now_iso()

    db.execute(
        """
        INSERT INTO bus_live (
            bus_id, trip_id, lat, lon, heading, speed_mps, speed_smooth_mps,
            eta_speed_mps, accuracy_m, satellites, progress_m,
            last_gps_at, last_update_at, last_movement_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(bus_id) DO UPDATE SET
            trip_id = excluded.trip_id,
            lat = excluded.lat,
            lon = excluded.lon,
            heading = excluded.heading,
            speed_mps = excluded.speed_mps,
            speed_smooth_mps = excluded.speed_smooth_mps,
            eta_speed_mps = excluded.eta_speed_mps,
            accuracy_m = excluded.accuracy_m,
            satellites = excluded.satellites,
            progress_m = excluded.progress_m,
            last_gps_at = excluded.last_gps_at,
            last_update_at = excluded.last_update_at,
            last_movement_at = excluded.last_movement_at,
            updated_at = excluded.updated_at
        """,
        (
            device["bus_id"], trip["id"], lat, lon, heading, speed, speed_smooth,
            eta_speed, accuracy, satellites, progress, timestamp, timestamp,
            last_movement_at, timestamp,
        ),
    )

    # Save a sampled history point (not every 5-second update).
    last_history = db.execute(
        "SELECT recorded_at FROM trip_gps_history WHERE trip_id = ? ORDER BY recorded_at DESC LIMIT 1",
        (trip["id"],),
    ).fetchone()
    history_age = seconds_since(last_history["recorded_at"]) if last_history else None
    if history_age is None or history_age >= current_app.config["HISTORY_SAMPLE_SECONDS"]:
        db.execute(
            """
            INSERT INTO trip_gps_history
                (trip_id, bus_id, lat, lon, speed_mps, progress_m, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (trip["id"], device["bus_id"], lat, lon, speed, progress, timestamp),
        )

    db.execute("UPDATE devices SET last_seen_at = ? WHERE id = ?", (timestamp, device["id"]))
    db.commit()

    # Notifications are checked after the response data is safely committed.
    previous_for_trip = previous_progress if live and live["trip_id"] == trip["id"] else None
    start_notification_check(
        current_app._get_current_object(), device["bus_id"], trip["id"], progress, previous_for_trip
    )

    return jsonify({
        "ok": True,
        "accepted": True,
        "trip_id": trip["id"],
        "trip_type": trip["trip_type"],
        "progress_m": round(progress, 1) if progress is not None else None,
    })
