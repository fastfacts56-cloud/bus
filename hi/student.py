"""
Student dashboard and student-only API.

A student's bus and stop are always looked up from the logged-in student's
database row. The browser never sends a bus id, so one student cannot request
another student's bus by changing a URL.
"""

import json

from flask import Blueprint, current_app, g, jsonify, render_template, request

from auth import enforce_role
from database import get_db, get_school_location
from eta import build_live_snapshot
from utils import clean_text, format_time, now_iso

student_bp = Blueprint("student", __name__, url_prefix="/student")


@student_bp.before_request
def only_students():
    return enforce_role("student")


def get_student_context():
    """Everything about the logged-in student in one safe query."""
    return get_db().execute(
        """
        SELECT students.id, students.usn, students.name,
               buses.id AS bus_id, buses.name AS bus_name,
               buses.registration_number, buses.route_id,
               routes.name AS route_name, routes.geometry_json,
               route_stops.id AS stop_id, route_stops.name AS stop_name,
               route_stops.lat AS stop_lat, route_stops.lon AS stop_lon,
               route_stops.distance_along_m AS stop_distance_m,
               COALESCE(notification_prefs.distance_m, ?) AS notify_distance_m
        FROM students
        LEFT JOIN buses ON buses.id = students.bus_id
        LEFT JOIN routes ON routes.id = buses.route_id
        LEFT JOIN route_stops
               ON route_stops.id = students.stop_id
              AND route_stops.route_id = buses.route_id
        LEFT JOIN notification_prefs ON notification_prefs.student_id = students.id
        WHERE students.id = ?
        """,
        (current_app.config["DEFAULT_NOTIFICATION_DISTANCE_M"], g.user["id"]),
    ).fetchone()


def active_trip_for_student(student):
    if student is None or student["bus_id"] is None:
        return None
    return get_db().execute(
        "SELECT * FROM trips WHERE bus_id = ? AND ended_at IS NULL", (student["bus_id"],)
    ).fetchone()


@student_bp.get("/")
def dashboard():
    student = get_student_context()
    db = get_db()

    route_geometry = None
    if student and student["geometry_json"]:
        route_geometry = json.loads(student["geometry_json"])

    live = None
    if student and student["bus_id"]:
        live = db.execute(
            "SELECT * FROM bus_live WHERE bus_id = ?", (student["bus_id"],)
        ).fetchone()

    school = get_school_location()
    student_data = {
        "assigned": bool(student and student["bus_id"]),
        "hasStop": bool(student and student["stop_id"]),
        "busName": student["bus_name"] if student else None,
        "routeName": student["route_name"] if student else None,
        "stopName": student["stop_name"] if student else None,
        "stop": (
            {"lat": student["stop_lat"], "lon": student["stop_lon"]}
            if student and student["stop_id"] else None
        ),
        "school": school,
        "routeGeometry": route_geometry or [],
        "live": (
            {"lat": live["lat"], "lon": live["lon"], "heading": live["heading"]}
            if live and live["lat"] is not None else None
        ),
        "notifyDistance": student["notify_distance_m"] if student else current_app.config["DEFAULT_NOTIFICATION_DISTANCE_M"],
        "minDistance": current_app.config["MIN_NOTIFICATION_DISTANCE_M"],
        "maxDistance": current_app.config["MAX_NOTIFICATION_DISTANCE_M"],
    }

    return render_template(
        "student/dashboard.html",
        student=student,
        route_geometry=route_geometry,
        school=school,
        live=live,
        active_trip=active_trip_for_student(student),
        student_data=student_data,
        default_distance=current_app.config["DEFAULT_NOTIFICATION_DISTANCE_M"],
        min_distance=current_app.config["MIN_NOTIFICATION_DISTANCE_M"],
        max_distance=current_app.config["MAX_NOTIFICATION_DISTANCE_M"],
    )


@student_bp.get("/api/live")
def live_status():
    student = get_student_context()
    if student is None:
        return jsonify({"ok": False, "error": "Student record not found."}), 404

    if student["bus_id"] is None:
        return jsonify({
            "ok": True,
            "assigned": False,
            "status_code": "no_bus",
            "status_text": "No bus assigned",
            "status_detail": "Ask your school admin to assign a bus and pickup stop.",
            "has_position": False,
        })

    db = get_db()
    live = db.execute(
        "SELECT * FROM bus_live WHERE bus_id = ?", (student["bus_id"],)
    ).fetchone()
    trip = active_trip_for_student(student)

    if student["stop_distance_m"] is None:
        snapshot = build_live_snapshot(
            live, trip, None, current_app.config, current_app.config["TIMEZONE_LABEL"]
        )
        snapshot.update({
            "status_code": "no_stop",
            "status_text": "No pickup stop assigned",
            "status_detail": "Ask the school admin to assign your pickup stop.",
        })
    else:
        snapshot = build_live_snapshot(
            live, trip, student["stop_distance_m"], current_app.config,
            current_app.config["TIMEZONE_LABEL"],
        )

    snapshot.update({
        "ok": True,
        "assigned": True,
        "bus_name": student["bus_name"],
        "registration_number": student["registration_number"],
        "route_name": student["route_name"],
        "stop_name": student["stop_name"],
        "notify_distance_m": student["notify_distance_m"],
        "server_time_text": format_time(now_iso(), current_app.config["TIMEZONE_LABEL"]),
    })
    return jsonify(snapshot)


# ---------------------------------------------------------------------------
# Web Push subscription
# ---------------------------------------------------------------------------

@student_bp.get("/api/notifications/public-key")
def push_public_key():
    return jsonify({"public_key": current_app.config["VAPID_PUBLIC_KEY"]})


@student_bp.get("/api/notifications/status")
def notification_status():
    student = get_student_context()
    count = get_db().execute(
        "SELECT COUNT(*) AS n FROM push_subscriptions WHERE student_id = ?",
        (g.user["id"],),
    ).fetchone()["n"]
    return jsonify({
        "ok": True,
        "subscribed": count > 0,
        "subscription_count": count,
        "distance_m": student["notify_distance_m"] if student else 1000,
    })


def clamp_distance(value):
    try:
        distance = int(value)
    except (TypeError, ValueError):
        distance = current_app.config["DEFAULT_NOTIFICATION_DISTANCE_M"]
    return max(
        current_app.config["MIN_NOTIFICATION_DISTANCE_M"],
        min(current_app.config["MAX_NOTIFICATION_DISTANCE_M"], distance),
    )


@student_bp.post("/api/notifications/subscribe")
def subscribe():
    data = request.get_json(silent=True) or {}
    subscription = data.get("subscription") or {}
    endpoint = clean_text(subscription.get("endpoint"), 1000)
    keys = subscription.get("keys") or {}
    p256dh = clean_text(keys.get("p256dh"), 500)
    auth = clean_text(keys.get("auth"), 200)

    if not endpoint.startswith("https://") or not p256dh or not auth:
        return jsonify({"ok": False, "error": "Invalid push subscription."}), 400

    distance = clamp_distance(data.get("distance_m"))
    db = get_db()

    # The same browser can be used by another student later, so move the
    # subscription to the currently logged-in student.
    db.execute(
        """
        INSERT INTO push_subscriptions (student_id, endpoint, p256dh, auth, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(endpoint) DO UPDATE SET
            student_id = excluded.student_id,
            p256dh = excluded.p256dh,
            auth = excluded.auth
        """,
        (g.user["id"], endpoint, p256dh, auth, now_iso()),
    )
    db.execute(
        """
        INSERT INTO notification_prefs (student_id, distance_m, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(student_id) DO UPDATE SET
            distance_m = excluded.distance_m,
            updated_at = excluded.updated_at
        """,
        (g.user["id"], distance, now_iso()),
    )
    db.commit()
    return jsonify({
        "ok": True,
        "distance_m": distance,
        "message": "Notifications enabled for this device.",
    })


@student_bp.post("/api/notifications/unsubscribe")
def unsubscribe():
    data = request.get_json(silent=True) or {}
    endpoint = clean_text(data.get("endpoint"), 1000)
    db = get_db()
    if endpoint:
        db.execute(
            "DELETE FROM push_subscriptions WHERE student_id = ? AND endpoint = ?",
            (g.user["id"], endpoint),
        )
    else:
        db.execute("DELETE FROM push_subscriptions WHERE student_id = ?", (g.user["id"],))
    db.commit()
    return jsonify({"ok": True, "message": "Notifications disabled for this device."})


@student_bp.post("/api/notification-distance")
def update_distance():
    data = request.get_json(silent=True) or {}
    distance = clamp_distance(data.get("distance_m"))
    db = get_db()
    db.execute(
        """
        INSERT INTO notification_prefs (student_id, distance_m, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(student_id) DO UPDATE SET
            distance_m = excluded.distance_m,
            updated_at = excluded.updated_at
        """,
        (g.user["id"], distance, now_iso()),
    )
    db.commit()
    return jsonify({
        "ok": True,
        "distance_m": distance,
        "message": "Notification distance saved. It applies to future checks.",
    })
