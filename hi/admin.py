"""
Admin dashboard and management pages.

All routes in this file require an admin session. The blueprint-level
enforce_role() check below is what actually protects the data (not CSS or
JavaScript).
"""

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from flask import (
    Blueprint, flash, g, jsonify, redirect, render_template, request, url_for,
)
from werkzeug.security import generate_password_hash

from auth import (
    current_credential, enforce_role, remember_new_credential,
)
from database import get_db, get_school_location, get_setting, set_setting
from geo import cumulative_distances, project_point_to_route
from routing import RoutingError, calculate_route, orient_route_towards_school
from utils import (
    clean_text, generate_pairing_code, generate_password, now_iso,
    seconds_since, valid_email, valid_latitude, valid_longitude,
)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,50}$")
USN_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,30}$")
REGISTRATION_PATTERN = re.compile(r"^[A-Za-z0-9 .-]{3,30}$")

PAIRING_CODE_MINUTES = 5


@admin_bp.before_request
def only_admins():
    response = enforce_role("admin")
    if response:
        return response
    return None


@admin_bp.context_processor
def inject_admin_helpers():
    return {"new_credential": current_credential(), "active_page": request.endpoint}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def optional_int(value):
    try:
        return int(value) if value not in (None, "", "0") else None
    except (TypeError, ValueError):
        return None


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def row_exists(table, row_id):
    if row_id is None:
        return False
    return get_db().execute(f"SELECT 1 FROM {table} WHERE id = ?", (row_id,)).fetchone() is not None


def get_route(route_id):
    return get_db().execute("SELECT * FROM routes WHERE id = ?", (route_id,)).fetchone()


def all_routes_with_stops():
    """Used by the students form to show only stops of the selected route."""
    db = get_db()
    routes = db.execute("SELECT * FROM routes ORDER BY name").fetchall()
    result = []
    for route in routes:
        stops = db.execute(
            "SELECT id, name, distance_along_m FROM route_stops "
            "WHERE route_id = ? ORDER BY distance_along_m",
            (route["id"],),
        ).fetchall()
        result.append({
            "id": route["id"],
            "name": route["name"],
            "stops": [dict(stop) for stop in stops],
        })
    return result


def bus_overview_rows():
    """
    One query for the Buses page and dashboard, with device + GPS status.
    """
    rows = get_db().execute(
        """
        SELECT buses.*,
               drivers.name AS driver_name,
               routes.name AS route_name,
               devices.id AS device_id,
               devices.paired_at,
               devices.last_seen_at,
               bus_live.last_update_at,
               bus_live.last_gps_at,
               bus_live.speed_smooth_mps,
               bus_live.accuracy_m,
               bus_live.satellites,
               trips.id AS active_trip_id,
               trips.trip_type AS active_trip_type
        FROM buses
        LEFT JOIN drivers ON drivers.id = buses.driver_id
        LEFT JOIN routes ON routes.id = buses.route_id
        LEFT JOIN devices ON devices.bus_id = buses.id
        LEFT JOIN bus_live ON bus_live.bus_id = buses.id
        LEFT JOIN trips ON trips.bus_id = buses.id AND trips.ended_at IS NULL
        ORDER BY buses.registration_number
        """
    ).fetchall()

    buses = []
    for row in rows:
        bus = dict(row)
        seen_age = seconds_since(row["last_seen_at"])
        bus["device_online"] = seen_age is not None and seen_age <= 60
        bus["gps_age"] = seconds_since(row["last_update_at"])
        bus["gps_stale"] = bus["gps_age"] is None or bus["gps_age"] > 30
        bus["weak_gps"] = (row["accuracy_m"] or 0) > 100 or (
            row["satellites"] is not None and row["satellites"] < 4
        )
        buses.append(bus)
    return buses


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@admin_bp.get("/")
def dashboard():
    db = get_db()
    buses = bus_overview_rows()

    stats = {
        "students": db.execute("SELECT COUNT(*) AS n FROM students").fetchone()["n"],
        "drivers": db.execute("SELECT COUNT(*) AS n FROM drivers").fetchone()["n"],
        "buses": len(buses),
        "routes": db.execute("SELECT COUNT(*) AS n FROM routes").fetchone()["n"],
        "online_buses": sum(1 for bus in buses if bus["device_online"]),
        "offline_buses": sum(1 for bus in buses if not bus["device_online"]),
        "active_trips": sum(1 for bus in buses if bus["active_trip_id"]),
        "gps_ok": sum(
            1 for bus in buses
            if bus["device_id"] and not bus["gps_stale"] and not bus["weak_gps"]
        ),
        "gps_weak": sum(
            1 for bus in buses
            if bus["device_id"] and not bus["gps_stale"] and bus["weak_gps"]
        ),
        "gps_lost": sum(
            1 for bus in buses if bus["device_id"] and bus["gps_stale"]
        ),
        "unpaired": sum(1 for bus in buses if not bus["device_id"]),
    }

    recent_trips = db.execute(
        """
        SELECT trips.*, buses.name AS bus_name, buses.registration_number,
               drivers.name AS driver_name, routes.name AS route_name
        FROM trips
        LEFT JOIN buses ON buses.id = trips.bus_id
        LEFT JOIN drivers ON drivers.id = trips.driver_id
        LEFT JOIN routes ON routes.id = trips.route_id
        ORDER BY trips.started_at DESC
        LIMIT 10
        """
    ).fetchall()

    school = get_school_location()
    return render_template(
        "admin/dashboard.html",
        stats=stats,
        buses=buses,
        recent_trips=recent_trips,
        school=school,
        new_credential=current_credential(),
    )


# ---------------------------------------------------------------------------
# Students
# ---------------------------------------------------------------------------

@admin_bp.get("/students")
def students():
    db = get_db()
    rows = db.execute(
        """
        SELECT students.*, buses.name AS bus_name, buses.registration_number,
               route_stops.name AS stop_name
        FROM students
        LEFT JOIN buses ON buses.id = students.bus_id
        LEFT JOIN route_stops ON route_stops.id = students.stop_id
        ORDER BY students.name COLLATE NOCASE
        """
    ).fetchall()
    buses = db.execute(
        """
        SELECT buses.id, buses.name, buses.registration_number, buses.route_id
        FROM buses ORDER BY buses.registration_number
        """
    ).fetchall()
    return render_template(
        "admin/students.html",
        students=rows,
        buses=buses,
        routes_with_stops=all_routes_with_stops(),
        new_credential=current_credential(),
    )


@admin_bp.post("/students/add")
def add_student():
    usn = clean_text(request.form.get("usn"), 30).upper()
    name = clean_text(request.form.get("name"), 120)
    gmail = clean_text(request.form.get("gmail"), 120).lower()

    if not USN_PATTERN.match(usn):
        flash("USN must be 3-30 letters, numbers, dots, dashes or underscores.", "error")
        return redirect(url_for("admin.students"))
    if not name:
        flash("Student name is required.", "error")
        return redirect(url_for("admin.students"))
    if not valid_email(gmail):
        flash("Enter a valid email address.", "error")
        return redirect(url_for("admin.students"))

    password = generate_password()
    db = get_db()
    try:
        db.execute(
            "INSERT INTO students (usn, name, gmail, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
            (usn, name, gmail, generate_password_hash(password), now_iso()),
        )
        db.commit()
    except sqlite3.IntegrityError:
        flash("That USN already exists.", "error")
        return redirect(url_for("admin.students"))

    remember_new_credential(f"Student {name} ({usn})", password)
    flash("Student added. Copy the generated password below and give it to the student.", "success")
    return redirect(url_for("admin.students"))


@admin_bp.get("/students/<int:student_id>/edit")
def edit_student(student_id):
    db = get_db()
    student = db.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
    if student is None:
        flash("Student not found.", "error")
        return redirect(url_for("admin.students"))
    buses = [
        dict(bus) for bus in db.execute(
            "SELECT id, name, registration_number, route_id FROM buses ORDER BY registration_number"
        ).fetchall()
    ]
    return render_template(
        "admin/student_edit.html",
        student=student,
        buses=buses,
        routes_with_stops=all_routes_with_stops(),
        new_credential=current_credential(),
    )


@admin_bp.post("/students/<int:student_id>/edit")
def update_student(student_id):
    db = get_db()
    student = db.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
    if student is None:
        flash("Student not found.", "error")
        return redirect(url_for("admin.students"))

    usn = clean_text(request.form.get("usn"), 30).upper()
    name = clean_text(request.form.get("name"), 120)
    gmail = clean_text(request.form.get("gmail"), 120).lower()
    bus_id = optional_int(request.form.get("bus_id"))
    stop_id = optional_int(request.form.get("stop_id"))

    if not USN_PATTERN.match(usn) or not name or not valid_email(gmail):
        flash("Check the USN, name and email address.", "error")
        return redirect(url_for("admin.edit_student", student_id=student_id))

    bus = db.execute("SELECT * FROM buses WHERE id = ?", (bus_id,)).fetchone() if bus_id else None
    if bus_id and bus is None:
        flash("The selected bus no longer exists.", "error")
        return redirect(url_for("admin.edit_student", student_id=student_id))

    if stop_id:
        stop = db.execute("SELECT * FROM route_stops WHERE id = ?", (stop_id,)).fetchone()
        if bus is None or stop is None or stop["route_id"] != bus["route_id"]:
            flash("Choose a stop that belongs to the assigned bus's route.", "error")
            return redirect(url_for("admin.edit_student", student_id=student_id))
    elif bus is None:
        stop_id = None

    try:
        db.execute(
            "UPDATE students SET usn = ?, name = ?, gmail = ?, bus_id = ?, stop_id = ? WHERE id = ?",
            (usn, name, gmail, bus_id, stop_id, student_id),
        )
        db.commit()
    except sqlite3.IntegrityError:
        flash("That USN already belongs to another student.", "error")
        return redirect(url_for("admin.edit_student", student_id=student_id))

    flash("Student details updated.", "success")
    return redirect(url_for("admin.students"))


@admin_bp.post("/students/<int:student_id>/reset-password")
def reset_student_password(student_id):
    db = get_db()
    student = db.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
    if student is None:
        flash("Student not found.", "error")
        return redirect(url_for("admin.students"))

    password = generate_password()
    db.execute(
        "UPDATE students SET password_hash = ? WHERE id = ?",
        (generate_password_hash(password), student_id),
    )
    db.commit()
    remember_new_credential(f"Student {student['name']} ({student['usn']})", password)
    flash("Student password reset. Copy the new password below.", "success")
    return redirect(url_for("admin.students"))


@admin_bp.post("/students/<int:student_id>/delete")
def delete_student(student_id):
    db = get_db()
    student = db.execute("SELECT name FROM students WHERE id = ?", (student_id,)).fetchone()
    if student is None:
        flash("Student not found.", "error")
        return redirect(url_for("admin.students"))
    db.execute("DELETE FROM students WHERE id = ?", (student_id,))
    db.commit()
    flash(f"Student {student['name']} was permanently deleted.", "success")
    return redirect(url_for("admin.students"))


# ---------------------------------------------------------------------------
# Drivers
# ---------------------------------------------------------------------------

@admin_bp.get("/drivers")
def drivers():
    rows = get_db().execute(
        """
        SELECT drivers.*, buses.name AS bus_name, buses.registration_number,
               (SELECT COUNT(*) FROM trips WHERE trips.driver_id = drivers.id) AS trip_count
        FROM drivers
        LEFT JOIN buses ON buses.driver_id = drivers.id
        ORDER BY drivers.name COLLATE NOCASE
        """
    ).fetchall()
    return render_template("admin/drivers.html", drivers=rows, new_credential=current_credential())


@admin_bp.post("/drivers/add")
def add_driver():
    name = clean_text(request.form.get("name"), 120)
    username = clean_text(request.form.get("username"), 50)
    phone = clean_text(request.form.get("phone"), 30)

    if not name:
        flash("Driver name is required.", "error")
        return redirect(url_for("admin.drivers"))
    if not USERNAME_PATTERN.match(username):
        flash("Username must be 3-50 letters, numbers, dots, dashes or underscores.", "error")
        return redirect(url_for("admin.drivers"))
    if not re.match(r"^[0-9+() -]{6,30}$", phone):
        flash("Enter a valid phone number (at least 6 digits).", "error")
        return redirect(url_for("admin.drivers"))

    password = generate_password()
    db = get_db()
    try:
        db.execute(
            "INSERT INTO drivers (name, username, phone, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
            (name, username, phone, generate_password_hash(password), now_iso()),
        )
        db.commit()
    except sqlite3.IntegrityError:
        flash("That driver username already exists.", "error")
        return redirect(url_for("admin.drivers"))

    remember_new_credential(f"Driver {name} ({username})", password)
    flash("Driver added. Copy the generated password below.", "success")
    return redirect(url_for("admin.drivers"))


@admin_bp.get("/drivers/<int:driver_id>/edit")
def edit_driver(driver_id):
    driver = get_db().execute("SELECT * FROM drivers WHERE id = ?", (driver_id,)).fetchone()
    if driver is None:
        flash("Driver not found.", "error")
        return redirect(url_for("admin.drivers"))
    return render_template("admin/driver_edit.html", driver=driver, new_credential=current_credential())


@admin_bp.post("/drivers/<int:driver_id>/edit")
def update_driver(driver_id):
    db = get_db()
    driver = db.execute("SELECT * FROM drivers WHERE id = ?", (driver_id,)).fetchone()
    if driver is None:
        flash("Driver not found.", "error")
        return redirect(url_for("admin.drivers"))

    name = clean_text(request.form.get("name"), 120)
    username = clean_text(request.form.get("username"), 50)
    phone = clean_text(request.form.get("phone"), 30)
    if not name or not USERNAME_PATTERN.match(username) or not re.match(r"^[0-9+() -]{6,30}$", phone):
        flash("Check the name, username and phone number.", "error")
        return redirect(url_for("admin.edit_driver", driver_id=driver_id))

    try:
        db.execute(
            "UPDATE drivers SET name = ?, username = ?, phone = ? WHERE id = ?",
            (name, username, phone, driver_id),
        )
        db.commit()
    except sqlite3.IntegrityError:
        flash("That username already belongs to another driver.", "error")
        return redirect(url_for("admin.edit_driver", driver_id=driver_id))

    flash("Driver details updated.", "success")
    return redirect(url_for("admin.drivers"))


@admin_bp.post("/drivers/<int:driver_id>/reset-password")
def reset_driver_password(driver_id):
    db = get_db()
    driver = db.execute("SELECT * FROM drivers WHERE id = ?", (driver_id,)).fetchone()
    if driver is None:
        flash("Driver not found.", "error")
        return redirect(url_for("admin.drivers"))

    password = generate_password()
    db.execute(
        "UPDATE drivers SET password_hash = ? WHERE id = ?",
        (generate_password_hash(password), driver_id),
    )
    db.commit()
    remember_new_credential(f"Driver {driver['name']} ({driver['username']})", password)
    flash("Driver password reset. Copy the new password below.", "success")
    return redirect(url_for("admin.drivers"))


@admin_bp.post("/drivers/<int:driver_id>/delete")
def delete_driver(driver_id):
    db = get_db()
    driver = db.execute("SELECT name FROM drivers WHERE id = ?", (driver_id,)).fetchone()
    if driver is None:
        flash("Driver not found.", "error")
        return redirect(url_for("admin.drivers"))
    # End trips this driver left running, otherwise the bus would look busy.
    active = db.execute(
        "SELECT id, bus_id FROM trips WHERE driver_id = ? AND ended_at IS NULL", (driver_id,)
    ).fetchall()
    for trip in active:
        db.execute("UPDATE trips SET ended_at = ? WHERE id = ?", (now_iso(), trip["id"]))
        db.execute("UPDATE bus_live SET trip_id = NULL WHERE bus_id = ?", (trip["bus_id"],))
    # buses.driver_id and trips.driver_id are set to NULL by foreign keys.
    db.execute("DELETE FROM drivers WHERE id = ?", (driver_id,))
    db.commit()
    flash(f"Driver {driver['name']} was permanently deleted.", "success")
    return redirect(url_for("admin.drivers"))


# ---------------------------------------------------------------------------
# Buses
# ---------------------------------------------------------------------------

@admin_bp.get("/buses")
def buses():
    db = get_db()
    drivers_list = db.execute("SELECT id, name, username FROM drivers ORDER BY name").fetchall()
    routes = db.execute("SELECT id, name FROM routes ORDER BY name").fetchall()
    return render_template(
        "admin/buses.html",
        buses=bus_overview_rows(),
        drivers=drivers_list,
        routes=routes,
    )


@admin_bp.post("/buses/add")
def add_bus():
    registration = clean_text(request.form.get("registration_number"), 30).upper()
    name = clean_text(request.form.get("name"), 80)
    driver_id = optional_int(request.form.get("driver_id"))
    route_id = optional_int(request.form.get("route_id"))

    if not REGISTRATION_PATTERN.match(registration):
        flash("Bus registration must be 3-30 letters, numbers, spaces, dots or dashes.", "error")
        return redirect(url_for("admin.buses"))
    if not name:
        flash("Bus name is required.", "error")
        return redirect(url_for("admin.buses"))

    db = get_db()
    if driver_id and not row_exists("drivers", driver_id):
        flash("The selected driver no longer exists.", "error")
        return redirect(url_for("admin.buses"))
    if route_id and not row_exists("routes", route_id):
        flash("The selected route no longer exists.", "error")
        return redirect(url_for("admin.buses"))
    if driver_id and db.execute(
        "SELECT 1 FROM buses WHERE driver_id = ?", (driver_id,)
    ).fetchone():
        flash("That driver is already assigned to another bus.", "error")
        return redirect(url_for("admin.buses"))

    try:
        db.execute(
            "INSERT INTO buses (registration_number, name, driver_id, route_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (registration, name, driver_id, route_id, now_iso()),
        )
        db.commit()
    except sqlite3.IntegrityError:
        flash("That bus registration number already exists.", "error")
        return redirect(url_for("admin.buses"))

    flash("Bus added. Pair its ESP32 device when the hardware is ready.", "success")
    return redirect(url_for("admin.buses"))


@admin_bp.get("/buses/<int:bus_id>/edit")
def edit_bus(bus_id):
    db = get_db()
    bus = db.execute("SELECT * FROM buses WHERE id = ?", (bus_id,)).fetchone()
    if bus is None:
        flash("Bus not found.", "error")
        return redirect(url_for("admin.buses"))
    drivers_list = db.execute("SELECT id, name, username FROM drivers ORDER BY name").fetchall()
    routes = db.execute("SELECT id, name FROM routes ORDER BY name").fetchall()
    device = db.execute("SELECT * FROM devices WHERE bus_id = ?", (bus_id,)).fetchone()
    return render_template(
        "admin/bus_edit.html", bus=bus, drivers=drivers_list, routes=routes, device=device
    )


@admin_bp.post("/buses/<int:bus_id>/edit")
def update_bus(bus_id):
    db = get_db()
    bus = db.execute("SELECT * FROM buses WHERE id = ?", (bus_id,)).fetchone()
    if bus is None:
        flash("Bus not found.", "error")
        return redirect(url_for("admin.buses"))

    registration = clean_text(request.form.get("registration_number"), 30).upper()
    name = clean_text(request.form.get("name"), 80)
    driver_id = optional_int(request.form.get("driver_id"))
    route_id = optional_int(request.form.get("route_id"))

    if not REGISTRATION_PATTERN.match(registration) or not name:
        flash("Check the bus registration and name.", "error")
        return redirect(url_for("admin.edit_bus", bus_id=bus_id))

    if driver_id and not row_exists("drivers", driver_id):
        flash("The selected driver no longer exists.", "error")
        return redirect(url_for("admin.edit_bus", bus_id=bus_id))
    if route_id and not row_exists("routes", route_id):
        flash("The selected route no longer exists.", "error")
        return redirect(url_for("admin.edit_bus", bus_id=bus_id))
    if driver_id and db.execute(
        "SELECT 1 FROM buses WHERE driver_id = ? AND id <> ?", (driver_id, bus_id)
    ).fetchone():
        flash("That driver is already assigned to another bus.", "error")
        return redirect(url_for("admin.edit_bus", bus_id=bus_id))

    try:
        if driver_id != bus["driver_id"]:
            # A different driver takes over this bus: end any trip the old
            # driver left running so the new driver is not locked out.
            db.execute(
                "UPDATE trips SET ended_at = ? WHERE bus_id = ? AND ended_at IS NULL",
                (now_iso(), bus_id),
            )
            db.execute("UPDATE bus_live SET trip_id = NULL WHERE bus_id = ?", (bus_id,))
        db.execute(
            "UPDATE buses SET registration_number = ?, name = ?, driver_id = ?, route_id = ? WHERE id = ?",
            (registration, name, driver_id, route_id, bus_id),
        )
        # If the route changes, assigned stops from the old route no longer
        # make sense, so clear them. The admin can reassign stops afterwards.
        if route_id != bus["route_id"]:
            db.execute(
                """
                UPDATE students SET stop_id = NULL
                WHERE bus_id = ? AND stop_id IS NOT NULL
                  AND stop_id NOT IN (SELECT id FROM route_stops WHERE route_id = ?)
                """,
                (bus_id, route_id),
            )
        db.commit()
    except sqlite3.IntegrityError:
        flash("That bus registration number already exists.", "error")
        return redirect(url_for("admin.edit_bus", bus_id=bus_id))

    flash("Bus updated.", "success")
    return redirect(url_for("admin.buses"))


@admin_bp.post("/buses/<int:bus_id>/delete")
def delete_bus(bus_id):
    db = get_db()
    bus = db.execute("SELECT name FROM buses WHERE id = ?", (bus_id,)).fetchone()
    if bus is None:
        flash("Bus not found.", "error")
        return redirect(url_for("admin.buses"))
    # Foreign keys remove the device and trips; students become unassigned.
    db.execute("DELETE FROM buses WHERE id = ?", (bus_id,))
    db.commit()
    flash(f"Bus {bus['name']} was deleted.", "success")
    return redirect(url_for("admin.buses"))


@admin_bp.post("/buses/<int:bus_id>/pairing-code")
def create_pairing_code(bus_id):
    db = get_db()
    bus = db.execute("SELECT * FROM buses WHERE id = ?", (bus_id,)).fetchone()
    if bus is None:
        return jsonify({"ok": False, "error": "Bus not found."}), 404
    if db.execute("SELECT 1 FROM devices WHERE bus_id = ?", (bus_id,)).fetchone():
        return jsonify({
            "ok": False,
            "error": "This bus already has a paired device. Unpair it first.",
        }), 409

    code = generate_pairing_code()
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=PAIRING_CODE_MINUTES)).isoformat()

    # Only the hash is stored. The plain code is shown once to the admin.
    db.execute("DELETE FROM pairing_codes WHERE bus_id = ? AND used_at IS NULL", (bus_id,))
    db.execute(
        "INSERT INTO pairing_codes (bus_id, code_hash, expires_at, created_at) VALUES (?, ?, ?, ?)",
        (bus_id, sha256(code), expires_at, now_iso()),
    )
    db.commit()
    return jsonify({
        "ok": True,
        "code": code,
        "expires_in_seconds": PAIRING_CODE_MINUTES * 60,
        "bus": bus["name"],
        "instructions": f"On the ESP32 serial monitor type: PAIR {code}",
    })


@admin_bp.post("/buses/<int:bus_id>/unpair")
def unpair_device(bus_id):
    db = get_db()
    bus = db.execute("SELECT name FROM buses WHERE id = ?", (bus_id,)).fetchone()
    if bus is None:
        flash("Bus not found.", "error")
        return redirect(url_for("admin.buses"))

    # End an active trip too, otherwise it would stay active with no GPS source.
    db.execute(
        "UPDATE trips SET ended_at = ? WHERE bus_id = ? AND ended_at IS NULL",
        (now_iso(), bus_id),
    )
    db.execute("DELETE FROM devices WHERE bus_id = ?", (bus_id,))
    db.execute("DELETE FROM bus_live WHERE bus_id = ?", (bus_id,))
    db.commit()
    flash(f"GPS device unpaired from {bus['name']}. The old device token no longer works.", "success")
    return redirect(url_for("admin.buses"))


@admin_bp.post("/buses/<int:bus_id>/end-trip")
def force_end_trip(bus_id):
    """Admins can clean up a trip left active after a driver problem."""
    db = get_db()
    trip = db.execute(
        "SELECT id FROM trips WHERE bus_id = ? AND ended_at IS NULL", (bus_id,)
    ).fetchone()
    if trip is None:
        flash("This bus has no active trip.", "info")
        return redirect(url_for("admin.buses"))
    db.execute("UPDATE trips SET ended_at = ? WHERE id = ?", (now_iso(), trip["id"]))
    db.execute("UPDATE bus_live SET trip_id = NULL WHERE bus_id = ?", (bus_id,))
    db.commit()
    flash("The active trip was ended by the admin.", "success")
    return redirect(url_for("admin.buses"))


# ---------------------------------------------------------------------------
# Routes and school location
# ---------------------------------------------------------------------------

@admin_bp.get("/routes")
def routes():
    db = get_db()
    route_rows = db.execute(
        """
        SELECT routes.*,
               (SELECT COUNT(*) FROM route_stops WHERE route_stops.route_id = routes.id) AS stop_count,
               (SELECT COUNT(*) FROM buses WHERE buses.route_id = routes.id) AS bus_count
        FROM routes ORDER BY routes.name
        """
    ).fetchall()
    return render_template(
        "admin/routes.html",
        routes=route_rows,
        school=get_school_location(),
        new_credential=current_credential(),
    )


@admin_bp.get("/routes/new")
def new_route():
    return render_template(
        "admin/route_editor.html",
        route=None,
        waypoints=[],
        geometry=[],
        stops=[],
        stops_data=[],
        school=get_school_location(),
        mode="new",
    )


@admin_bp.get("/routes/<int:route_id>/edit")
def edit_route(route_id):
    route = get_route(route_id)
    if route is None:
        flash("Route not found.", "error")
        return redirect(url_for("admin.routes"))
    stops = [
        dict(stop) for stop in get_db().execute(
            "SELECT * FROM route_stops WHERE route_id = ? ORDER BY distance_along_m",
            (route_id,),
        ).fetchall()
    ]
    return render_template(
        "admin/route_editor.html",
        route=route,
        waypoints=json.loads(route["waypoints_json"]),
        geometry=json.loads(route["geometry_json"]),
        stops=stops,
        stops_data=[
            {
                "id": stop["id"],
                "name": stop["name"],
                "lat": stop["lat"],
                "lon": stop["lon"],
                "distance_along_m": stop["distance_along_m"],
                "stop_order": stop["stop_order"],
            }
            for stop in stops
        ],
        school=get_school_location(),
        mode="edit",
    )


def read_route_payload():
    data = request.get_json(silent=True) or {}
    name = clean_text(data.get("name"), 100)
    waypoints = data.get("waypoints") or []
    cleaned = []
    for point in waypoints:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("Invalid route point.")
        lat, lon = float(point[0]), float(point[1])
        if not valid_latitude(lat) or not valid_longitude(lon):
            raise ValueError("Invalid route point coordinates.")
        cleaned.append([lat, lon])
    return name, cleaned


def recalculate_route_stops(db, route_id, geometry):
    """After route geometry changes, snap stops to it and update their order."""
    stops = db.execute("SELECT * FROM route_stops WHERE route_id = ?", (route_id,)).fetchall()
    for stop in stops:
        projection = project_point_to_route(stop["lat"], stop["lon"], geometry)
        db.execute(
            "UPDATE route_stops SET lat = ?, lon = ?, distance_along_m = ? WHERE id = ?",
            (projection["lat"], projection["lon"], projection["distance_along_m"], stop["id"]),
        )
    reorder_stops(db, route_id)


def reorder_stops(db, route_id):
    stops = db.execute(
        "SELECT id FROM route_stops WHERE route_id = ? ORDER BY distance_along_m",
        (route_id,),
    ).fetchall()
    for index, stop in enumerate(stops):
        db.execute("UPDATE route_stops SET stop_order = ? WHERE id = ?", (index, stop["id"]))


@admin_bp.post("/api/routes/preview")
def preview_route():
    try:
        _, waypoints = read_route_payload()
        if len(waypoints) < 2:
            return jsonify({"ok": False, "error": "Click at least two points on the map."}), 400
        result = calculate_route(waypoints, request_osrm_url())
        school = get_school_location()
        geometry, oriented_points = orient_route_towards_school(
            result["geometry"], waypoints, school
        )
        total = cumulative_distances(geometry)[-1]
        return jsonify({
            "ok": True,
            "geometry": geometry,
            "waypoints": oriented_points,
            "distance_m": round(total),
            "auto_reversed": oriented_points != waypoints,
        })
    except RoutingError as error:
        return jsonify({"ok": False, "error": str(error)}), 502
    except (TypeError, ValueError) as error:
        return jsonify({"ok": False, "error": str(error)}), 400


def request_osrm_url():
    from flask import current_app
    return current_app.config["OSRM_BASE_URL"]


def save_route_from_payload(route_id=None):
    name, waypoints = read_route_payload()
    if not name:
        return None, "Route name is required."
    if len(waypoints) < 2:
        return None, "Click at least two points on the map."

    try:
        result = calculate_route(waypoints, request_osrm_url())
    except RoutingError as error:
        return None, str(error)

    school = get_school_location()
    geometry, waypoints = orient_route_towards_school(result["geometry"], waypoints, school)
    total_distance = cumulative_distances(geometry)[-1]
    now = now_iso()

    db = get_db()
    if route_id is None:
        cursor = db.execute(
            """
            INSERT INTO routes (name, waypoints_json, geometry_json, total_distance_m, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name, json.dumps(waypoints), json.dumps(geometry), total_distance, now, now),
        )
        route_id = cursor.lastrowid
    else:
        db.execute(
            """
            UPDATE routes SET name = ?, waypoints_json = ?, geometry_json = ?,
                total_distance_m = ?, updated_at = ? WHERE id = ?
            """,
            (name, json.dumps(waypoints), json.dumps(geometry), total_distance, now, route_id),
        )
        recalculate_route_stops(db, route_id, geometry)
    db.commit()
    return route_id, None


@admin_bp.post("/routes/create")
def create_route():
    route_id, error = save_route_from_payload()
    if error:
        return jsonify({"ok": False, "error": error}), 502
    return jsonify({
        "ok": True,
        "route_id": route_id,
        "redirect": url_for("admin.edit_route", route_id=route_id),
    })


@admin_bp.post("/routes/<int:route_id>/update")
def update_route(route_id):
    if get_route(route_id) is None:
        return jsonify({"ok": False, "error": "Route not found."}), 404
    saved_id, error = save_route_from_payload(route_id)
    if error:
        return jsonify({"ok": False, "error": error}), 502
    return jsonify({
        "ok": True,
        "route_id": saved_id,
        "redirect": url_for("admin.edit_route", route_id=saved_id),
    })


@admin_bp.post("/routes/<int:route_id>/delete")
def delete_route(route_id):
    db = get_db()
    route = db.execute("SELECT name FROM routes WHERE id = ?", (route_id,)).fetchone()
    if route is None:
        flash("Route not found.", "error")
        return redirect(url_for("admin.routes"))
    db.execute("UPDATE buses SET route_id = NULL WHERE route_id = ?", (route_id,))
    db.execute("UPDATE students SET stop_id = NULL WHERE stop_id IN "
               "(SELECT id FROM route_stops WHERE route_id = ?)", (route_id,))
    db.execute("DELETE FROM routes WHERE id = ?", (route_id,))
    db.commit()
    flash(f"Route {route['name']} deleted. Assigned buses were unassigned.", "success")
    return redirect(url_for("admin.routes"))


def stop_json(stop):
    return {
        "id": stop["id"],
        "name": stop["name"],
        "lat": stop["lat"],
        "lon": stop["lon"],
        "distance_along_m": round(stop["distance_along_m"], 1),
        "stop_order": stop["stop_order"],
    }


@admin_bp.post("/routes/<int:route_id>/stops/add")
def add_stop(route_id):
    route = get_route(route_id)
    if route is None:
        return jsonify({"ok": False, "error": "Route not found."}), 404

    data = request.get_json(silent=True) or {}
    name = clean_text(data.get("name"), 80)
    try:
        lat = float(data.get("lat"))
        lon = float(data.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Click a valid position for the stop."}), 400
    if not name:
        return jsonify({"ok": False, "error": "Enter a name for the stop."}), 400
    if not valid_latitude(lat) or not valid_longitude(lon):
        return jsonify({"ok": False, "error": "Invalid stop coordinates."}), 400

    db = get_db()
    if db.execute(
        "SELECT 1 FROM route_stops WHERE route_id = ? AND name = ? COLLATE NOCASE",
        (route_id, name),
    ).fetchone():
        return jsonify({"ok": False, "error": "A stop with that name already exists on this route."}), 409

    geometry = json.loads(route["geometry_json"])
    projection = project_point_to_route(lat, lon, geometry)
    if projection["offset_m"] > 500:
        return jsonify({
            "ok": False,
            "error": "That point is too far from the route. Click closer to the blue route line.",
        }), 400

    cursor = db.execute(
        """
        INSERT INTO route_stops
            (route_id, name, lat, lon, distance_along_m, stop_order, created_at)
        VALUES (?, ?, ?, ?, ?, 0, ?)
        """,
        (route_id, name, projection["lat"], projection["lon"],
         projection["distance_along_m"], now_iso()),
    )
    stop_id = cursor.lastrowid
    reorder_stops(db, route_id)
    db.commit()
    stop = db.execute("SELECT * FROM route_stops WHERE id = ?", (stop_id,)).fetchone()
    return jsonify({"ok": True, "stop": stop_json(stop)})


@admin_bp.post("/stops/<int:stop_id>/update")
def update_stop(stop_id):
    db = get_db()
    stop = db.execute("SELECT * FROM route_stops WHERE id = ?", (stop_id,)).fetchone()
    if stop is None:
        return jsonify({"ok": False, "error": "Stop not found."}), 404

    data = request.get_json(silent=True) or {}
    name = clean_text(data.get("name", stop["name"]), 80)
    if not name:
        return jsonify({"ok": False, "error": "Stop name is required."}), 400

    duplicate = db.execute(
        "SELECT 1 FROM route_stops WHERE route_id = ? AND name = ? COLLATE NOCASE AND id <> ?",
        (stop["route_id"], name, stop_id),
    ).fetchone()
    if duplicate:
        return jsonify({"ok": False, "error": "Another stop on this route already has that name."}), 409

    lat, lon = stop["lat"], stop["lon"]
    if data.get("lat") is not None and data.get("lon") is not None:
        try:
            lat = float(data["lat"])
            lon = float(data["lon"])
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Invalid stop coordinates."}), 400
        if not valid_latitude(lat) or not valid_longitude(lon):
            return jsonify({"ok": False, "error": "Invalid stop coordinates."}), 400

        route = get_route(stop["route_id"])
        projection = project_point_to_route(lat, lon, json.loads(route["geometry_json"]))
        if projection["offset_m"] > 500:
            return jsonify({
                "ok": False,
                "error": "Keep the stop within 500 m of the route line.",
            }), 400
        lat, lon = projection["lat"], projection["lon"]
        distance_along = projection["distance_along_m"]
    else:
        distance_along = stop["distance_along_m"]

    db.execute(
        "UPDATE route_stops SET name = ?, lat = ?, lon = ?, distance_along_m = ? WHERE id = ?",
        (name, lat, lon, distance_along, stop_id),
    )
    reorder_stops(db, stop["route_id"])
    db.commit()
    updated = db.execute("SELECT * FROM route_stops WHERE id = ?", (stop_id,)).fetchone()
    return jsonify({"ok": True, "stop": stop_json(updated)})


@admin_bp.post("/stops/<int:stop_id>/delete")
def delete_stop(stop_id):
    db = get_db()
    stop = db.execute("SELECT * FROM route_stops WHERE id = ?", (stop_id,)).fetchone()
    if stop is None:
        return jsonify({"ok": False, "error": "Stop not found."}), 404
    # Students assigned to this stop become unassigned (foreign key SET NULL).
    db.execute("DELETE FROM route_stops WHERE id = ?", (stop_id,))
    reorder_stops(db, stop["route_id"])
    db.commit()
    return jsonify({"ok": True})


@admin_bp.post("/api/school-location")
def save_school_location():
    data = request.get_json(silent=True) or {}
    try:
        lat = float(data.get("lat"))
        lon = float(data.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Click a valid position on the map."}), 400
    if not valid_latitude(lat) or not valid_longitude(lon):
        return jsonify({"ok": False, "error": "Invalid school coordinates."}), 400

    set_setting("school_lat", f"{lat:.7f}")
    set_setting("school_lon", f"{lon:.7f}")
    return jsonify({
        "ok": True,
        "lat": lat,
        "lon": lon,
        "message": "School location saved. It applies to every route.",
    })
