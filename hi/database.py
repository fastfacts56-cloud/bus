"""
Database access for the School Bus Tracker.

SQLite is perfect for a single-school deployment on one PC. Every request gets
its own short-lived connection. WAL mode allows the ESP32 GPS writes, browser
reads and admin writes to happen at the same time without blocking each other.
"""

import os
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import g

from utils import now_iso, parse_iso
from werkzeug.security import check_password_hash, generate_password_hash

SCHEMA_FILE = Path(__file__).resolve().parent / "schema.sql"


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------

def connect(database_path):
    """Open a connection with the settings we want for every connection."""
    connection = sqlite3.connect(database_path, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 15000")
    return connection


def get_db():
    """Get the connection belonging to the current Flask request."""
    if "db" not in g:
        g.db = connect(current_database_path())
    return g.db


def open_db_for_request():
    """Flask before_request hook: open the connection and return nothing."""
    get_db()
    return None


def current_database_path():
    # Imported lazily so this module can also be used by scripts that have no app.
    from flask import current_app
    return str(current_app.config["DATABASE_PATH"])


@contextmanager
def database_connection(database_path):
    """Connection for background threads and scripts (not tied to a request)."""
    connection = connect(str(database_path))
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def init_db(app):
    """Create the folders, create missing tables and prepare SQLite."""
    database_path = Path(app.config["DATABASE_PATH"])
    database_path.parent.mkdir(parents=True, exist_ok=True)

    connection = connect(str(database_path))
    try:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        schema = SCHEMA_FILE.read_text(encoding="utf-8")
        connection.executescript(schema)
        connection.commit()
    finally:
        connection.close()

    seed_admin(app.config)
    end_old_ghost_trips(database_path)


def seed_admin(config):
    """
    Create the admin account from .env, or update it if .env was changed.

    This is the supported admin password reset procedure:
      1. Edit ADMIN_PASSWORD in .env
      2. Restart the application
    Only hashes are stored in SQLite, never the plaintext password.
    """
    username = config["ADMIN_USERNAME"].strip()
    password = config["ADMIN_PASSWORD"]
    if not username or not password:
        raise RuntimeError(
            "ADMIN_USERNAME and ADMIN_PASSWORD must be set in the .env file."
        )

    connection = connect(str(config["DATABASE_PATH"]))
    try:
        row = connection.execute("SELECT * FROM admins LIMIT 1").fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO admins (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, generate_password_hash(password), now_iso()),
            )
        elif row["username"] != username or not check_password_hash(row["password_hash"], password):
            connection.execute(
                "UPDATE admins SET username = ?, password_hash = ? WHERE id = ?",
                (username, generate_password_hash(password), row["id"]),
            )
            print("Admin credentials were updated from .env")
        connection.commit()
    finally:
        connection.close()


def end_old_ghost_trips(database_path):
    """
    If the PC was switched off during a trip, that trip would stay active
    forever. End trips older than 12 hours at startup. A restart during a
    normal school trip (under 12 hours) keeps the trip active on purpose.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=12)).replace(microsecond=0).isoformat()
    with database_connection(database_path) as connection:
        old_trips = connection.execute(
            "SELECT id FROM trips WHERE ended_at IS NULL AND started_at < ?", (cutoff,)
        ).fetchall()
        for trip in old_trips:
            connection.execute(
                "UPDATE trips SET ended_at = ? WHERE id = ?", (now_iso(), trip["id"])
            )
            connection.execute(
                "UPDATE bus_live SET trip_id = NULL WHERE trip_id = ?", (trip["id"],)
            )
        connection.execute(
            "DELETE FROM pairing_codes WHERE expires_at < ? OR used_at IS NOT NULL",
            ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),),
        )


# ---------------------------------------------------------------------------
# Settings (school location)
# ---------------------------------------------------------------------------

def get_setting(key, default=None):
    from flask import current_app
    connection = connect(str(current_app.config["DATABASE_PATH"]))
    try:
        row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        connection.close()


def set_setting(key, value):
    db = get_db()
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    db.commit()


def get_school_location():
    """Return {'lat': float, 'lon': float} or None when not configured."""
    lat = get_setting("school_lat")
    lon = get_setting("school_lon")
    if lat is None or lon is None:
        return None
    try:
        return {"lat": float(lat), "lon": float(lon)}
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Backups
# ---------------------------------------------------------------------------

def backup_database(config):
    """
    Create one consistent backup per day using SQLite's own backup API.

    Copying an open database file can produce a broken backup. The backup API
    safely copies a snapshot even while the app is writing.
    """
    backup_dir = Path(config["BACKUP_DIR"])
    backup_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    target = backup_dir / f"school_bus_{today}.db"
    if target.exists():
        return target

    source = sqlite3.connect(str(config["DATABASE_PATH"]))
    destination = sqlite3.connect(str(target))
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()

    cleanup_old_backups(config)
    print(f"Daily database backup created: {target}")
    return target


def cleanup_old_backups(config):
    """Keep only the newest N backup files."""
    backup_dir = Path(config["BACKUP_DIR"])
    files = sorted(backup_dir.glob("school_bus_*.db"), key=lambda path: path.stat().st_mtime)
    keep = int(config["BACKUP_KEEP_COUNT"])
    for old_file in files[:-keep]:
        try:
            old_file.unlink()
        except OSError:
            pass


def restore_hint():
    """Text used in DETAIL.md and the admin dashboard."""
    return "Stop the server, replace instance/school_bus.db with a backup, then restart."


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------

def cleanup_gps_history(config):
    """Delete GPS history older than the configured number of days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=int(config["GPS_HISTORY_DAYS"]))).replace(
        microsecond=0
    ).isoformat()
    with database_connection(config["DATABASE_PATH"]) as connection:
        deleted = connection.execute(
            "DELETE FROM trip_gps_history WHERE recorded_at < ?", (cutoff,)
        ).rowcount
    if deleted:
        print(f"Deleted {deleted} old GPS history rows")


def cleanup_old_pairing_codes(config):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    with database_connection(config["DATABASE_PATH"]) as connection:
        connection.execute(
            "DELETE FROM pairing_codes WHERE expires_at < ? OR used_at IS NOT NULL", (cutoff,)
        )


def run_maintenance(config):
    """Called at startup and hourly by a background thread."""
    try:
        backup_database(config)
        cleanup_gps_history(config)
        cleanup_old_pairing_codes(config)
    except Exception as error:  # never let maintenance kill the server
        print(f"Maintenance warning: {error}")
