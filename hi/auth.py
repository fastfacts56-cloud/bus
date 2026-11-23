"""
Login, logout, CSRF protection and role checks.

Security rules:
- The backend checks the role on every request. Hiding a button is not security.
- POST/PUT/DELETE browser requests need a CSRF token.
- ESP32 endpoints are exempt because they authenticate with a device token
  and are not browser sessions.
- Login attempts are rate limited to slow down password guessing.
"""

import hmac
import secrets
from functools import wraps

from flask import (
    Blueprint, current_app, flash, g, jsonify, redirect, render_template,
    request, session, url_for,
)
from werkzeug.security import check_password_hash

from database import get_db
from utils import RateLimiter, clean_text, client_ip

auth_bp = Blueprint("auth", __name__)
login_limiter = RateLimiter()

USER_TABLES = {
    "admin": "admins",
    "driver": "drivers",
    "student": "students",
}

DASHBOARDS = {
    "admin": "admin.dashboard",
    "driver": "driver.dashboard",
    "student": "student.dashboard",
}


# ---------------------------------------------------------------------------
# CSRF protection
# ---------------------------------------------------------------------------

def csrf_token():
    """Return this session's CSRF token, creating one when needed."""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def check_csrf():
    """Compare the submitted token with the session token."""
    expected = session.get("csrf_token", "")
    submitted = request.form.get("_csrf_token") or request.headers.get("X-CSRF-Token", "")
    return bool(expected) and hmac.compare_digest(str(expected), str(submitted))


def protect_state_changing_requests():
    """Flask before_request hook installed in app.py."""
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None
    if request.blueprint == "esp32_api":
        return None  # ESP32 uses its own device credential, no browser session.
    if request.endpoint == "static":
        return None
    if not check_csrf():
        if request.is_json or request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "Invalid or missing CSRF token."}), 400
        return render_template("error.html", title="Security check failed",
                               message="Your form session expired. Go back, reload the page and try again."), 400
    return None


# ---------------------------------------------------------------------------
# Current user
# ---------------------------------------------------------------------------

def load_logged_in_user():
    """Load the logged-in user from SQLite on every request."""
    role = session.get("role")
    user_id = session.get("user_id")
    g.user = None

    if not role or not user_id or role not in USER_TABLES:
        return

    table = USER_TABLES[role]
    row = get_db().execute(f"SELECT * FROM {table} WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        session.clear()
        return

    user = dict(row)
    user["role"] = role
    if role == "student":
        # Include bus and stop names for the dashboard/header.
        extra = get_db().execute(
            """
            SELECT buses.name AS bus_name, buses.registration_number,
                   route_stops.name AS stop_name
            FROM students
            LEFT JOIN buses ON buses.id = students.bus_id
            LEFT JOIN route_stops ON route_stops.id = students.stop_id
            WHERE students.id = ?
            """,
            (user_id,),
        ).fetchone()
        if extra:
            user.update(dict(extra))
    g.user = user


def enforce_role(role):
    """
    Blueprint-level check. Returns a redirect/JSON response when the user does
    not have the required role, otherwise None.
    """
    if g.get("user") and session.get("role") == role:
        return None
    if request.is_json or request.path.startswith(("/api/", "/admin/api/", "/driver/api/", "/student/api/")):
        return jsonify({"ok": False, "error": "Not signed in."}), 401
    return redirect(url_for("auth.login", next=request.path))


def role_required(role):
    """Decorator that enforces a role on the server side."""
    def decorator(view_function):
        @wraps(view_function)
        def wrapped(*args, **kwargs):
            user = g.get("user")
            if not user or session.get("role") != role:
                if request.is_json or request.path.startswith("/api/"):
                    return jsonify({"ok": False, "error": "Not signed in."}), 401
                return redirect(url_for("auth.login", next=request.path))
            return view_function(*args, **kwargs)
        return wrapped
    return decorator


# ---------------------------------------------------------------------------
# Login / logout
# ---------------------------------------------------------------------------

@auth_bp.route("/login", methods=["GET"])
def login():
    if g.get("user"):
        return redirect(url_for(DASHBOARDS[g.user["role"]]))
    return render_template("login.html")


@auth_bp.route("/login/<role>", methods=["POST"])
def login_as(role):
    if role not in USER_TABLES:
        return render_template("login.html", error="Unknown login type."), 404

    limiter_key = f"login:{role}:{client_ip(request)}"
    if not login_limiter.allow(limiter_key, limit=8, window_seconds=300):
        return render_template(
            "login.html",
            error="Too many login attempts. Wait 5 minutes and try again.",
            active_role=role,
        ), 429

    username = clean_text(request.form.get("username", ""), 120)
    password = request.form.get("password", "")

    if role == "student":
        row = get_db().execute(
            "SELECT * FROM students WHERE usn = ?", (username,)
        ).fetchone()
    elif role == "driver":
        row = get_db().execute(
            "SELECT * FROM drivers WHERE username = ?", (username,)
        ).fetchone()
    else:
        row = get_db().execute(
            "SELECT * FROM admins WHERE username = ?", (username,)
        ).fetchone()

    if row is None or not check_password_hash(row["password_hash"], password):
        return render_template(
            "login.html",
            error="Incorrect username or password.",
            active_role=role,
        ), 401

    login_limiter.clear(limiter_key)

    # A fresh session after login prevents session fixation.
    session.clear()
    session["role"] = role
    session["user_id"] = row["id"]
    session["csrf_token"] = secrets.token_urlsafe(32)
    session.permanent = True

    return redirect(url_for(DASHBOARDS[role]))


@auth_bp.post("/logout")
def logout():
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------------------
# Small helpers used by admin pages
# ---------------------------------------------------------------------------

def remember_new_credential(label, password):
    """
    Keep a newly generated password for one display in the admin page.

    The admin must copy it and give it to the student/driver. There is no email
    sending in this project by design.
    """
    session["new_credential"] = {"label": label, "password": password}


def current_credential():
    return session.get("new_credential")


def clear_new_credential():
    session.pop("new_credential", None)
