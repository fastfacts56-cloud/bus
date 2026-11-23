-- School Bus Tracker database schema (SQLite)
-- All timestamps are ISO-8601 UTC text, for example 2026-11-23T08:30:00+00:00.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS admins (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS routes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL COLLATE NOCASE,
    waypoints_json    TEXT NOT NULL,   -- points the admin clicked, [[lat, lon], ...]
    geometry_json     TEXT NOT NULL,   -- road-following shape from OSRM, [[lat, lon], ...]
    total_distance_m  REAL NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS route_stops (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    route_id        INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    lat             REAL NOT NULL,
    lon             REAL NOT NULL,
    distance_along_m REAL NOT NULL DEFAULT 0,
    stop_order      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_route_stops_route ON route_stops(route_id, stop_order);
CREATE INDEX IF NOT EXISTS idx_route_stops_distance ON route_stops(route_id, distance_along_m);

CREATE TABLE IF NOT EXISTS drivers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    phone         TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS buses (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    registration_number TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name                TEXT NOT NULL,
    driver_id           INTEGER UNIQUE REFERENCES drivers(id) ON DELETE SET NULL,
    route_id            INTEGER REFERENCES routes(id) ON DELETE SET NULL,
    created_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_buses_route ON buses(route_id);
CREATE INDEX IF NOT EXISTS idx_buses_driver ON buses(driver_id);

CREATE TABLE IF NOT EXISTS students (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    usn           TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name          TEXT NOT NULL,
    gmail         TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    bus_id        INTEGER REFERENCES buses(id) ON DELETE SET NULL,
    stop_id       INTEGER REFERENCES route_stops(id) ON DELETE SET NULL,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_students_bus ON students(bus_id);
CREATE INDEX IF NOT EXISTS idx_students_stop ON students(stop_id);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS devices (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    bus_id        INTEGER NOT NULL UNIQUE REFERENCES buses(id) ON DELETE CASCADE,
    token_hash    TEXT NOT NULL UNIQUE,
    hardware_id   TEXT NOT NULL DEFAULT '',
    paired_at     TEXT NOT NULL,
    last_seen_at  TEXT
);

CREATE TABLE IF NOT EXISTS pairing_codes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bus_id      INTEGER NOT NULL REFERENCES buses(id) ON DELETE CASCADE,
    code_hash   TEXT NOT NULL UNIQUE,
    expires_at  TEXT NOT NULL,
    used_at     TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pairing_codes_hash ON pairing_codes(code_hash);
CREATE INDEX IF NOT EXISTS idx_pairing_codes_bus ON pairing_codes(bus_id);

CREATE TABLE IF NOT EXISTS trips (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    bus_id           INTEGER NOT NULL REFERENCES buses(id) ON DELETE CASCADE,
    driver_id        INTEGER REFERENCES drivers(id) ON DELETE SET NULL,
    route_id         INTEGER REFERENCES routes(id) ON DELETE SET NULL,
    trip_type        TEXT NOT NULL CHECK (trip_type IN ('morning', 'afternoon')),
    started_at       TEXT NOT NULL,
    ended_at         TEXT,
    start_progress_m REAL,
    last_progress_m  REAL
);

-- A bus can only have one active trip (ended_at IS NULL).
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_trip_per_bus
    ON trips(bus_id) WHERE ended_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_trips_bus_started ON trips(bus_id, started_at);
CREATE INDEX IF NOT EXISTS idx_trips_started ON trips(started_at);

-- The latest position of every bus (one row per bus).
CREATE TABLE IF NOT EXISTS bus_live (
    bus_id            INTEGER PRIMARY KEY REFERENCES buses(id) ON DELETE CASCADE,
    trip_id           INTEGER REFERENCES trips(id) ON DELETE SET NULL,
    lat               REAL,
    lon               REAL,
    heading           REAL,
    speed_mps         REAL DEFAULT 0,
    speed_smooth_mps  REAL DEFAULT 0,
    eta_speed_mps     REAL DEFAULT 0,
    accuracy_m        REAL DEFAULT 0,
    satellites        INTEGER,
    progress_m        REAL,
    last_gps_at       TEXT,
    last_update_at    TEXT,
    last_movement_at  TEXT,
    updated_at        TEXT
);

CREATE INDEX IF NOT EXISTS idx_bus_live_trip ON bus_live(trip_id);

-- Sampled GPS history (one point per minute, kept for 7 days by default).
CREATE TABLE IF NOT EXISTS trip_gps_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id      INTEGER NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    bus_id       INTEGER NOT NULL REFERENCES buses(id) ON DELETE CASCADE,
    lat          REAL NOT NULL,
    lon          REAL NOT NULL,
    speed_mps    REAL,
    progress_m   REAL,
    recorded_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_gps_history_trip_time ON trip_gps_history(trip_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_gps_history_recorded ON trip_gps_history(recorded_at);

CREATE TABLE IF NOT EXISTS notification_prefs (
    student_id  INTEGER PRIMARY KEY REFERENCES students(id) ON DELETE CASCADE,
    distance_m  INTEGER NOT NULL DEFAULT 1000,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id  INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    endpoint    TEXT NOT NULL UNIQUE,
    p256dh      TEXT NOT NULL,
    auth        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_push_student ON push_subscriptions(student_id);

CREATE TABLE IF NOT EXISTS notification_deliveries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id  INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    trip_id     INTEGER NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    distance_m  REAL,
    sent_at     TEXT NOT NULL,
    UNIQUE (student_id, trip_id)
);

CREATE INDEX IF NOT EXISTS idx_deliveries_trip ON notification_deliveries(trip_id);
