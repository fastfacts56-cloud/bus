"""
Small shared helpers: passwords, timestamps, rate limiting and validation.

Keeping these functions in one place makes the rest of the code easier to read.
"""

import math
import re
import secrets
import string
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def now_iso():
    """Current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_iso(value):
    """Turn a stored ISO timestamp back into a datetime (or None)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def seconds_since(value):
    """How many seconds have passed since an ISO timestamp."""
    moment = parse_iso(value)
    if moment is None:
        return None
    return (datetime.now(timezone.utc) - moment).total_seconds()


def format_time(value, timezone_label="Local time"):
    """Format a stored UTC timestamp for display in the browser/server."""
    moment = parse_iso(value)
    if moment is None:
        return "Never"
    # Show the server's local time because the whole system runs on one PC.
    return moment.astimezone().strftime("%d %b %Y, %I:%M:%S %p") + f" ({timezone_label})"


# ---------------------------------------------------------------------------
# Random values
# ---------------------------------------------------------------------------

# Avoid characters that are easy to confuse (0/O, 1/I/L).
PAIRING_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
PASSWORD_ALPHABET = string.ascii_letters + string.digits + "!@#$%^&*"


def generate_password(length=16):
    """
    Generate a strong random password that contains uppercase, lowercase,
    a digit and a symbol. Used for new student/driver accounts.
    """
    while True:
        password = "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))
        if (
            any(c.islower() for c in password)
            and any(c.isupper() for c in password)
            and any(c.isdigit() for c in password)
            and any(c in "!@#$%^&*" for c in password)
        ):
            return password


def generate_pairing_code():
    """12-character code with no predictable pattern (cryptographically secure)."""
    return "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(12))


# ---------------------------------------------------------------------------
# Simple in-memory rate limiter
# ---------------------------------------------------------------------------

class RateLimiter:
    """
    Allow at most `limit` actions per `window_seconds` for a key (usually IP).

    This is intentionally simple: the app runs as one Waitress process, so an
    in-memory dictionary is enough. Restarting the server clears it.
    """

    def __init__(self):
        self._events = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key, limit, window_seconds):
        """Return True if the action is allowed, False if the limit is reached."""
        current_time = time.time()
        with self._lock:
            events = self._events[key]
            while events and events[0] < current_time - window_seconds:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(current_time)
            return True

    def clear(self, key):
        with self._lock:
            self._events.pop(key, None)


def client_ip(request):
    """Get the visitor IP. ProxyFix has already applied Cloudflare headers."""
    return request.remote_addr or "unknown"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def clean_text(value, max_length=120):
    """Trim text and limit its length (extra safety for database input)."""
    return (value or "").strip()[:max_length]


def valid_email(value):
    return bool(EMAIL_PATTERN.match(value or ""))


def valid_latitude(value):
    return isinstance(value, (int, float)) and math.isfinite(value) and -90 <= value <= 90


def valid_longitude(value):
    return isinstance(value, (int, float)) and math.isfinite(value) and -180 <= value <= 180


def nice_bool(value):
    return str(value).lower() in ("1", "true", "yes", "on")
