"""
Driver dashboard and trip controls.

Drivers can only ever act on the bus assigned to them. That bus is found from
the logged-in driver's database row, never from a bus id sent by the browser.
"""

import json
import sqlite3

from flask import (
    Blueprint, current_app, jsonify, render_template, request,
)
from flask import g

from auth import enforce_role
from database import get_db, get_school_location
from utils import format_time, now_iso, seconds_since

driver_bp = Blueprint("driver", __name__, url_prefix="/driver")


@driver_bp.before_request
def only_drivers():
    return enforce_role("driver")


def get_my_bus():
    """The single bus assigned to the logged-in driver (or None)."""
    return get_db().execute(
        """
        SELECT buses.*, routes.name AS route_name, routes.geometry_json,
               routes.waypoints_json
        FROM buses
        LEFT JOIN routes ON routes.id = buses.route_id
        WHERE buses.driver_id = ?
        """,
        (g.user["id"],),
    ).fetchone()


def active_trip_for_bus(bus_id):
    return get_db().execute(
        "SELECT * FROM trips WHERE bus_id = ? AND ended_at IS NULL", (bus_id,)
    ).fetchone()


@driver_bp.get("/")
def dashboard():
    bus = get_my_bus()
    route_geometry = None
    school = get_school_location()
    trip = None

    if bus and bus["geometry_json"]:
        route_geometry = json.loads(bus["geometry_json"])
    if bus:
        trip = active_trip_for_bus(bus["id"])

    return render_template(
        "driver/dashboard.html",
        bus=bus,
        route_geometry=route_geometry,
        school=school,
        active_trip=trip,
    )


@driver_bp.get("/api/status")
def status():
    bus = get_my_bus()
    if bus is None:
        return jsonify({
            "ok": True,
            "has_bus": False,
            "message": "No bus is assigned to you yet. Ask the admin to assign one.",
        })

    db = get_db()
    device = db.execute("SELECT * FROM devices WHERE bus_id = ?", (bus["id"],)).fetchone()
    live = db.execute("SELECT * FROM bus_live WHERE bus_id = ?", (bus["id"],)).fetchone()
    trip = active_trip_for_bus(bus["id"])

    device_age = seconds_since(device["last_seen_at"]) if device and device["last_seen_at"] else None
    gps_age = seconds_since(live["last_update_at"]) if live and live["last_update_at"] else None
    stale_after = current_app.config["GPS_STALE_AFTER_SECONDS"]

    return jsonify({
        "ok": True,
        "has_bus": True,
        "bus": {
            "id": bus["id"],
            "name": bus["name"],
            "registration_number": bus["registration_number"],
            "route_name": bus["route_name"],
            "has_route": bus["route_id"] is not None,
        },
        "device": {
            "paired": device is not None,
            "online": device_age is not None and device_age <= 60,
            "last_seen_text": format_time(device["last_seen_at"]) if device else "Never",
        },
        "gps": {
            "has_position": bool(live and live["lat"] is not None),
            "lat": live["lat"] if live else None,
            "lon": live["lon"] if live else None,
            "heading": live["heading"] if live else None,
            "speed_kmh": round((live["speed_smooth_mps"] or 0) * 3.6, 1) if live else 0,
            "accuracy_m": round(live["accuracy_m"]) if live and live["accuracy_m"] else None,
            "satellites": live["satellites"] if live else None,
            "stale": gps_age is None or gps_age > stale_after,
            "weak": bool(live and ((live["accuracy_m"] or 0) > 100)),
            "age_seconds": int(gps_age) if gps_age is not None else None,
            "last_update_text": format_time(live["last_update_at"]) if live else "Never",
        },
        "trip": {
            "active": trip is not None,
            "id": trip["id"] if trip else None,
            "trip_type": trip["trip_type"] if trip else None,
            "started_at_text": format_time(trip["started_at"]) if trip else None,
        },
    })


@driver_bp.post("/api/trips/start")
def start_trip():
    bus = get_my_bus()
    if bus is None:
        return jsonify({"ok": False, "error": "No bus is assigned to you."}), 403

    data = request.get_json(silent=True) or {}
    trip_type = data.get("trip_type")
    if trip_type not in ("morning", "afternoon"):
        return jsonify({"ok": False, "error": "Choose Morning Pickup or Afternoon Drop-off."}), 400

    if bus["route_id"] is None:
        return jsonify({
            "ok": False,
            "error": "Your bus has no route assigned. Ask the admin to assign one.",
        }), 400

    db = get_db()
    if active_trip_for_bus(bus["id"]) is not None:
        return jsonify({"ok": False, "error": "This bus already has an active trip."}), 409

    live = db.execute("SELECT * FROM bus_live WHERE bus_id = ?", (bus["id"],)).fetchone()
    start_progress = live["progress_m"] if live else None

    try:
        cursor = db.execute(
            """
            INSERT INTO trips
                (bus_id, driver_id, route_id, trip_type, started_at, start_progress_m, last_progress_m)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (bus["id"], g.user["id"], bus["route_id"], trip_type, now_iso(),
             start_progress, start_progress),
        )
        trip_id = cursor.lastrowid
        if live:
            db.execute("UPDATE bus_live SET trip_id = ? WHERE bus_id = ?", (trip_id, bus["id"]))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return jsonify({"ok": False, "error": "This bus already has an active trip."}), 409

    return jsonify({
        "ok": True,
        "trip_id": trip_id,
        "trip_type": trip_type,
        "message": "Trip started. GPS tracking is now active for this bus.",
    })


@driver_bp.post("/api/trips/end")
def end_trip():
    bus = get_my_bus()
    if bus is None:
        return jsonify({"ok": False, "error": "No bus is assigned to you."}), 403

    db = get_db()
    trip = active_trip_for_bus(bus["id"])
    if trip is None:
        return jsonify({"ok": False, "error": "There is no active trip to end."}), 409

    db.execute("UPDATE trips SET ended_at = ? WHERE id = ?", (now_iso(), trip["id"]))
    db.execute("UPDATE bus_live SET trip_id = NULL WHERE bus_id = ?", (bus["id"],))
    db.commit()
    return jsonify({"ok": True, "message": "Trip ended. GPS updates for this trip are now rejected."})
