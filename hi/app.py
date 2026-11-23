"""
Flask application setup.

Run this through run_server.py (Waitress) or with start.bat. Do not use
`flask run` for the real deployment: the Flask development server is not built
for real traffic.
"""

import threading
import time

from flask import Flask, g, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

import auth
import database
from admin import admin_bp
from config import Config
from driver import driver_bp
from gps_api import esp32_bp
from student import student_bp
from utils import format_time

maintenance_started = False


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    app.config["SESSION_COOKIE_NAME"] = "school_bus_session"

    # "auto" means: production is served over HTTPS by Cloudflare Tunnel.
    # For local HTTP testing, set SESSION_COOKIE_SECURE=false in .env.
    secure_setting = str(app.config["SESSION_COOKIE_SECURE"]).lower()
    app.config["SESSION_COOKIE_SECURE"] = secure_setting not in ("false", "0", "no", "off")

    # Cloudflare sends X-Forwarded-Proto / X-Forwarded-For headers.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    database.init_db(app)
    database.run_maintenance(app.config)  # backup if due + clean old history

    app.teardown_appcontext(database.close_db)

    app.register_blueprint(auth.auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(driver_bp)
    app.register_blueprint(student_bp)
    app.register_blueprint(esp32_bp)

    # Order matters: CSRF is checked before the view runs; the user is loaded
    # so role checks can use g.user.
    app.before_request(database.open_db_for_request)
    app.before_request(auth.load_logged_in_user)

    @app.before_request
    def security_checks():
        return auth.protect_state_changing_requests()

    @app.context_processor
    def inject_globals():
        return {
            "csrf_token": auth.csrf_token,
            "current_user": g.get("user"),
        }

    @app.template_filter("pretty_time")
    def pretty_time(value):
        return format_time(value)

    @app.route("/sw.js")
    def service_worker():
        # A service worker must be served from the site root to control the
        # whole app and receive push messages for /student.
        response = send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")
        response.headers["Service-Worker-Allowed"] = "/"
        return response

    @app.route("/")
    def home():
        if g.get("user"):
            return redirect(url_for(auth.DASHBOARDS[g.user["role"]]))
        return redirect(url_for("auth.login"))

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https://*.tile.openstreetmap.org https://tile.openstreetmap.org; "
            "connect-src 'self'; "
            "worker-src 'self'; "
            "manifest-src 'self'; "
            "frame-ancestors 'self'; "
            "base-uri 'self'; "
            "form-action 'self'",
        )
        if request.path.startswith("/api/") or request.path.endswith("/api/live"):
            response.headers["Cache-Control"] = "no-store"
        if request.path == "/sw.js":
            response.headers["Cache-Control"] = "no-cache"
            response.headers["Service-Worker-Allowed"] = "/"
        return response

    @app.errorhandler(404)
    def not_found(error):
        if request.path.startswith("/api/") or request.is_json:
            return jsonify({"ok": False, "error": "Not found."}), 404
        return render_template("error.html", title="Page not found",
                               message="That page does not exist."), 404

    @app.errorhandler(413)
    def too_large(error):
        return render_template("error.html", title="Request too large",
                               message="The submitted data is too large."), 413

    @app.errorhandler(500)
    def server_error(error):
        if request.path.startswith("/api/") or request.is_json:
            return jsonify({"ok": False, "error": "Server error."}), 500
        return render_template(
            "error.html", title="Something went wrong",
            message="The server could not complete that request. Check logs/server.log.",
        ), 500

    start_maintenance_thread(app)
    return app


def start_maintenance_thread(app):
    """Run backup/history cleanup once an hour in the background."""
    global maintenance_started
    if maintenance_started:
        return
    maintenance_started = True

    def loop():
        while True:
            time.sleep(3600)
            database.run_maintenance(app.config)

    threading.Thread(target=loop, daemon=True, name="maintenance").start()


app = create_app()
