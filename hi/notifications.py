"""
Web Push notifications for students.

A notification is only sent when ALL of these are true:
  1. The bus is on an active trip.
  2. The student's assigned stop is ahead of the bus in the trip direction.
  3. The road distance remaining to that stop is within the student's setting.
  4. The student has not already received a notification on this trip.

Because ETA/progress uses the saved road route, being geographically close to a
stop does not trigger a notification when the bus has already passed it.

Delivery state is stored in notification_deliveries with a UNIQUE
(student_id, trip_id) constraint, so a student gets at most one notification
per trip even if the server restarts.
"""

import json
import threading

from pywebpush import WebPushException, webpush

from database import database_connection
from eta import remaining_distance_m
from utils import now_iso

# One check at a time per bus. GPS updates arrive every 5 seconds, and a check
# can involve several push requests.
_bus_locks = {}
_locks_guard = threading.Lock()


def _lock_for_bus(bus_id):
    with _locks_guard:
        if bus_id not in _bus_locks:
            _bus_locks[bus_id] = threading.Lock()
        return _bus_locks[bus_id]


def start_notification_check(app, bus_id, trip_id, progress_m, previous_progress_m):
    """
    Start a background check so the ESP32 gets a fast GPS response.
    If a check for this bus is already running, skip this update safely.
    """
    lock = _lock_for_bus(bus_id)
    if not lock.acquire(blocking=False):
        return

    def worker():
        try:
            with app.app_context():
                check_and_notify(app.config, bus_id, trip_id, progress_m, previous_progress_m)
        except Exception as error:
            # Notifications must never crash GPS tracking.
            print(f"Notification check failed for bus {bus_id}: {error}")
        finally:
            lock.release()

    threading.Thread(target=worker, daemon=True, name=f"notify-bus-{bus_id}").start()


def check_and_notify(config, bus_id, trip_id, progress_m, previous_progress_m):
    if progress_m is None:
        return

    with database_connection(config["DATABASE_PATH"]) as connection:
        trip = connection.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
        if trip is None or trip["ended_at"] is not None or trip["bus_id"] != bus_id:
            return

        bus = connection.execute("SELECT * FROM buses WHERE id = ?", (bus_id,)).fetchone()
        if bus is None:
            return

        if bus["route_id"] is None:
            return

        # Is the bus actually moving towards the school (morning) or away from
        # it (afternoon)? This blocks notifications caused by GPS jitter.
        if previous_progress_m is None:
            moving_forward = False
        elif trip["trip_type"] == "morning":
            moving_forward = progress_m > previous_progress_m + 0.5
        else:
            moving_forward = progress_m < previous_progress_m - 0.5

        rows = connection.execute(
            """
            SELECT students.id AS student_id, students.name AS student_name,
                   route_stops.id AS stop_id, route_stops.name AS stop_name,
                   route_stops.distance_along_m AS stop_distance,
                   COALESCE(notification_prefs.distance_m, ?) AS notify_distance
            FROM students
            JOIN route_stops ON route_stops.id = students.stop_id
            LEFT JOIN notification_prefs ON notification_prefs.student_id = students.id
            WHERE students.bus_id = ?
              AND route_stops.route_id = ?
            """,
            (
                config["DEFAULT_NOTIFICATION_DISTANCE_M"],
                bus_id,
                bus["route_id"],
            ),
        ).fetchall()

        for row in rows:
            remaining = remaining_distance_m(progress_m, row["stop_distance"], trip["trip_type"])
            if remaining is None or remaining <= 0:
                continue  # stop is behind the bus: no notification
            if remaining > row["notify_distance"]:
                continue  # not close enough yet

            # Do not fire on a backwards GPS jitter.
            if not moving_forward:
                continue

            subscriptions = connection.execute(
                "SELECT * FROM push_subscriptions WHERE student_id = ?",
                (row["student_id"],),
            ).fetchall()
            if not subscriptions:
                continue

            # Claim this student+trip before sending. If another GPS update
            # already claimed it, rowcount is 0 and we skip.
            claimed = connection.execute(
                """
                INSERT OR IGNORE INTO notification_deliveries
                    (student_id, trip_id, distance_m, sent_at)
                VALUES (?, ?, ?, ?)
                """,
                (row["student_id"], trip_id, remaining, now_iso()),
            )
            if claimed.rowcount == 0:
                continue
            connection.commit()

            payload = {
                "title": "Bus approaching your stop",
                "body": (
                    f"{bus['name']} ({bus['registration_number']}) is about "
                    f"{int(round(remaining))} m from {row['stop_name']}. "
                    f"{'Morning pickup' if trip['trip_type'] == 'morning' else 'Afternoon drop-off'}."
                ),
                "url": "/student",
                "tag": f"trip-{trip_id}",
                "bus": bus["name"],
                "stop": row["stop_name"],
                "distance_m": int(round(remaining)),
                "trip_type": trip["trip_type"],
            }
            send_to_subscriptions(config, subscriptions, payload, connection)


def send_to_subscriptions(config, subscriptions, payload, connection):
    data = json.dumps(payload)
    for subscription in subscriptions:
        info = {
            "endpoint": subscription["endpoint"],
            "keys": {"p256dh": subscription["p256dh"], "auth": subscription["auth"]},
        }
        try:
            webpush(
                subscription_info=info,
                data=data,
                vapid_private_key=config["VAPID_PRIVATE_KEY"],
                vapid_claims={"sub": config["VAPID_SUBJECT"]},
                timeout=10,
            )
        except WebPushException as error:
            status_code = getattr(getattr(error, "response", None), "status_code", None)
            print(f"Push failed for subscription {subscription['id']} (HTTP {status_code}): {error}")
            if status_code in (404, 410):
                connection.execute(
                    "DELETE FROM push_subscriptions WHERE id = ?", (subscription["id"],)
                )
                connection.commit()
        except Exception as error:
            print(f"Push error for subscription {subscription['id']}: {error}")
