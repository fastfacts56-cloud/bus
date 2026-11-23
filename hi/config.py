"""
Configuration for the School Bus Tracker.

Everything private is kept in the .env file at the project root.
On the very first startup this file creates .env automatically with strong
random values (admin password, Flask secret key and VAPID push keys).
"""

import base64
import os
import secrets
import string
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from dotenv import load_dotenv, set_key
from py_vapid import Vapid

# The folder that contains this file (the project root).
BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"


def _random_text(length=20):
    """Generate a random password with letters and numbers."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _generate_vapid_keys():
    """
    Create a new VAPID key pair for Web Push.

    The private key is stored as base64url-encoded DER (the format the
    pywebpush library expects). The public key is the uncompressed EC point
    that browsers need when subscribing to push notifications.
    """
    vapid = Vapid()
    vapid.generate_keys()

    private_der = vapid.private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_raw = vapid.public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )

    def b64url(data):
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    return b64url(private_der), b64url(public_raw)


def ensure_env_file():
    """
    Create .env with safe defaults if it does not exist yet.

    Returns True when a new file was created (so the caller can show the
    generated admin credentials on the console exactly once).
    """
    if ENV_FILE.exists():
        return False

    admin_password = _random_text(20)
    vapid_private, vapid_public = _generate_vapid_keys()

    values = {
        "SECRET_KEY": secrets.token_urlsafe(48),
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": admin_password,
        "VAPID_PRIVATE_KEY": vapid_private,
        "VAPID_PUBLIC_KEY": vapid_public,
        "VAPID_SUBJECT": "mailto:change-me@example.com",
        "DATABASE_PATH": "instance/school_bus.db",
        "HOST": "127.0.0.1",
        "PORT": "8000",
        "OSRM_BASE_URL": "https://router.project-osrm.org",
        "PUBLIC_BASE_URL": "https://bus.example.com",
        "SESSION_COOKIE_SECURE": "auto",
        "GPS_HISTORY_DAYS": "7",
        "BACKUP_KEEP_COUNT": "30",
        "TIMEZONE_LABEL": "Local time",
    }

    # set_key creates the file on the first call.
    for key, value in values.items():
        set_key(str(ENV_FILE), key, value, quote_mode="never")

    print("=" * 70)
    print("A new .env file was created with secure random credentials.")
    print(f"Admin username: {values['ADMIN_USERNAME']}")
    print(f"Admin password: {admin_password}")
    print("Save this password now. It is also stored in .env (keep it private).")
    print("=" * 70)

    return True


def _bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


# Create .env if needed BEFORE loading values.
ensure_env_file()
load_dotenv(ENV_FILE, override=False)


def ensure_required_values():
    """
    Fill in any required secret that is missing or blank (for example after
    copying .env.example). Existing values are never overwritten.
    """
    updates = {}
    created_password = None

    if not os.getenv("SECRET_KEY", "").strip():
        updates["SECRET_KEY"] = secrets.token_urlsafe(48)
    if not os.getenv("ADMIN_USERNAME", "").strip():
        updates["ADMIN_USERNAME"] = "admin"
    if not os.getenv("ADMIN_PASSWORD", "").strip():
        created_password = _random_text(20)
        updates["ADMIN_PASSWORD"] = created_password
    if not os.getenv("VAPID_PRIVATE_KEY", "").strip() or not os.getenv("VAPID_PUBLIC_KEY", "").strip():
        private_key, public_key = _generate_vapid_keys()
        updates["VAPID_PRIVATE_KEY"] = private_key
        updates["VAPID_PUBLIC_KEY"] = public_key
    if not os.getenv("VAPID_SUBJECT", "").strip():
        updates["VAPID_SUBJECT"] = "mailto:change-me@example.com"

    for key, value in updates.items():
        set_key(str(ENV_FILE), key, value, quote_mode="never")
        os.environ[key] = value

    if created_password:
        print("=" * 70)
        print("A new random admin password was generated and saved in .env.")
        print(f"Admin username: {os.environ.get('ADMIN_USERNAME', 'admin')}")
        print(f"Admin password: {created_password}")
        print("Save this password now and keep .env private.")
        print("=" * 70)


ensure_required_values()


def _resolve_path(value, default):
    path = Path(value or default)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


class Config:
    """Plain class holding all settings used by the Flask application."""

    SECRET_KEY = os.getenv("SECRET_KEY", "")
    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

    VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
    VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")
    VAPID_SUBJECT = os.getenv("VAPID_SUBJECT", "mailto:change-me@example.com")

    DATABASE_PATH = _resolve_path(os.getenv("DATABASE_PATH"), "instance/school_bus.db")
    BACKUP_DIR = BASE_DIR / "backups"
    LOG_DIR = BASE_DIR / "logs"

    HOST = os.getenv("HOST", "127.0.0.1")
    PORT = int(os.getenv("PORT", "8000"))
    OSRM_BASE_URL = os.getenv("OSRM_BASE_URL", "https://router.project-osrm.org").rstrip("/")
    PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://bus.example.com").rstrip("/")

    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "auto").strip().lower()
    GPS_HISTORY_DAYS = int(os.getenv("GPS_HISTORY_DAYS", "7"))
    BACKUP_KEEP_COUNT = int(os.getenv("BACKUP_KEEP_COUNT", "30"))
    TIMEZONE_LABEL = os.getenv("TIMEZONE_LABEL", "Local time")

    # Flask / cookie security defaults.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 12  # 12 hours
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024  # 2 MB request limit

    # How long a GPS position is considered "live" (seconds).
    GPS_STALE_AFTER_SECONDS = 30
    BUS_OFFLINE_AFTER_SECONDS = 60
    BUS_STOPPED_AFTER_SECONDS = 10 * 60

    # Notification defaults.
    DEFAULT_NOTIFICATION_DISTANCE_M = 1000
    MIN_NOTIFICATION_DISTANCE_M = 200
    MAX_NOTIFICATION_DISTANCE_M = 5000

    # GPS history is sampled (not every 5 seconds).
    HISTORY_SAMPLE_SECONDS = 60
    # Reject GPS jumps faster than this (m/s); generous for bad roads/GPS.
    MAX_PLAUSIBLE_SPEED_MPS = 45
