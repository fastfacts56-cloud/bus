"""
ETA calculation.

The ETA is based on the bus's progress along the saved road route, not on a
straight line to the stop. It also uses the bus's recent smoothed speed.

This is a schedule estimate only. Public OSRM does not provide live traffic, so
the ETA does not include traffic jams, traffic lights or road works. That is
explained to students on the dashboard.
"""

from utils import format_time, seconds_since


def remaining_distance_m(progress_m, stop_distance_m, trip_type):
    """
    Road distance remaining before the bus reaches the stop.

    Morning: the bus drives towards the school, so progress increases.
    Afternoon: the bus starts at the school, so progress decreases.

    A positive number means the stop is still ahead.
    A negative number means the bus has passed the stop.
    """
    if progress_m is None or stop_distance_m is None:
        return None
    if trip_type == "morning":
        return stop_distance_m - progress_m
    return progress_m - stop_distance_m


def format_eta_text(seconds):
    if seconds is None:
        return "ETA unavailable"
    if seconds <= 60:
        return "Arriving now"
    minutes = int(round(seconds / 60))
    if minutes <= 1:
        return "About 1 min"
    return f"About {minutes} min"


def build_live_snapshot(live, trip, stop_distance_m, config, timezone_label="Local time"):
    """
    Build one dictionary with everything the student/driver page needs.

    `live` is a bus_live row, `trip` is an active/ended trip row or None.
    """
    now_live = dict(live) if live else {}
    trip_dict = dict(trip) if trip else None

    has_position = now_live.get("lat") is not None and now_live.get("lon") is not None
    age = seconds_since(now_live.get("last_update_at")) if has_position else None
    stale_after = int(config["GPS_STALE_AFTER_SECONDS"])
    is_stale = age is None or age > stale_after

    accuracy = now_live.get("accuracy_m") or 0
    weak_gps = bool(has_position and accuracy > 100)

    speed = now_live.get("speed_smooth_mps") or 0
    eta_speed = now_live.get("eta_speed_mps") or 0
    stopped_seconds = seconds_since(now_live.get("last_movement_at"))
    stopped = bool(has_position and stopped_seconds is not None
                   and stopped_seconds > config["BUS_STOPPED_AFTER_SECONDS"])

    snapshot = {
        "has_position": has_position,
        "lat": now_live.get("lat"),
        "lon": now_live.get("lon"),
        "heading": now_live.get("heading"),
        "speed_kmh": round(speed * 3.6, 1) if speed else 0,
        "accuracy_m": round(accuracy) if accuracy else None,
        "last_update_at": now_live.get("last_update_at"),
        "last_update_text": format_time(now_live.get("last_update_at"), timezone_label),
        "gps_age_seconds": int(age) if age is not None else None,
        "stale": is_stale,
        "weak_gps": weak_gps,
        "stopped": stopped,
        "trip_active": bool(trip_dict and trip_dict.get("ended_at") is None),
        "trip_type": trip_dict.get("trip_type") if trip_dict else None,
        "progress_m": now_live.get("progress_m"),
        "remaining_m": None,
        "passed": False,
        "arriving": False,
        "eta_seconds": None,
        "eta_minutes": None,
        "eta_text": "ETA unavailable",
        "status_code": "waiting",
        "status_text": "Waiting for the bus",
        "status_detail": "No live GPS position has been received yet.",
    }

    trip_type = snapshot["trip_type"]

    # Priority 1: we need a GPS position at all.
    if not has_position:
        if trip_dict is None:
            snapshot["status_code"] = "no_trip"
            snapshot["status_text"] = "Trip not active"
            snapshot["status_detail"] = "The driver has not started a trip yet."
        return snapshot

    # Priority 2: show the last known location clearly when the signal is old.
    if is_stale:
        snapshot["status_code"] = "stale"
        snapshot["status_text"] = "GPS signal lost"
        snapshot["status_detail"] = (
            f"Last position was received {snapshot['last_update_text']}. "
            "The bus may be out of network coverage. This is not a live location."
        )
        return snapshot

    if trip_dict is None or trip_dict.get("ended_at") is not None:
        snapshot["status_code"] = "no_trip"
        snapshot["status_text"] = "Trip not active"
        snapshot["status_detail"] = "The bus is not currently on a school trip."
        return snapshot

    if stop_distance_m is None or now_live.get("progress_m") is None:
        snapshot["status_code"] = "no_route"
        snapshot["status_text"] = "Stop information unavailable"
        snapshot["status_detail"] = "The bus or stop is not fully configured."
        return snapshot

    remaining = remaining_distance_m(now_live["progress_m"], stop_distance_m, trip_type)
    snapshot["remaining_m"] = max(0.0, round(remaining)) if remaining is not None else None
    snapshot["passed"] = remaining is not None and remaining < -40

    # Priority 3: a long stop means we do not pretend the old ETA is live.
    if stopped:
        snapshot["status_code"] = "stopped"
        snapshot["status_text"] = "BUS STOPPED"
        snapshot["status_detail"] = "The bus has not moved for more than 10 minutes. ETA is unavailable."
        return snapshot

    if remaining is None:
        return snapshot

    if snapshot["passed"]:
        snapshot["status_code"] = "passed"
        snapshot["status_text"] = "Bus has passed your stop"
        snapshot["status_detail"] = "The bus has already gone past this stop on this trip."
        return snapshot

    if remaining <= 25:
        snapshot["arriving"] = True
        snapshot["status_code"] = "arriving"
        snapshot["status_text"] = "Arriving now"
        snapshot["status_detail"] = "The bus is at or very close to your stop."
        snapshot["eta_seconds"] = 0
        snapshot["eta_minutes"] = 0
        snapshot["eta_text"] = "Arriving now"
        return snapshot

    # Use the last sensible moving speed. If the bus has just slowed down, we
    # keep using the previous speed for a short stop instead of showing "0 min".
    speed_for_eta = eta_speed if eta_speed >= 0.5 else speed
    if speed_for_eta < 1.0:
        # Crawling in traffic: assume a slow but realistic city-bus speed so
        # the ETA does not become infinite.
        speed_for_eta = 2.5

    speed_for_eta = max(1.5, min(speed_for_eta, 25.0))  # 5.4 - 90 km/h
    eta_seconds = int(remaining / speed_for_eta)

    snapshot["eta_seconds"] = eta_seconds
    snapshot["eta_minutes"] = max(1, int(round(eta_seconds / 60)))
    snapshot["eta_text"] = format_eta_text(eta_seconds)
    snapshot["status_code"] = "on_the_way"
    snapshot["status_text"] = snapshot["eta_text"]
    snapshot["status_detail"] = (
        f"{max(0, round(remaining))} m of road left to your stop. "
        "Estimate only - traffic is not included."
    )
    if weak_gps:
        snapshot["status_detail"] += " GPS accuracy is weak."
    return snapshot
