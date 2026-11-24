-- RAKURS: future append-only archival schema. NOT DEPLOYED/NOT WRITING.
-- Separate from Home Assistant Recorder. Suitable for a future dedicated
-- SQLite archive database on an independently backed-up volume after
-- source, ingest rate and space are verified. SQLite is illustrative;
-- schema may be adapted to PostgreSQL or MariaDB before commissioning.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY,
    device_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK(kind IN ('deye_inverter','battery_module','meter','gateway')),
    model TEXT,
    serial_number TEXT,
    commissioned_at_utc TEXT,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY,
    signal_key TEXT NOT NULL UNIQUE,
    label_uk TEXT NOT NULL,
    unit TEXT,
    source_register TEXT,
    function_code INTEGER CHECK(function_code IN (3,4)),
    scale_expression TEXT,
    state_class TEXT CHECK(state_class IN ('measurement','total','total_increasing') OR state_class IS NULL),
    verified INTEGER NOT NULL DEFAULT 0 CHECK(verified IN (0,1)),
    verified_at_utc TEXT,
    verification_note TEXT
);

CREATE TABLE IF NOT EXISTS samples (
    device_id INTEGER NOT NULL REFERENCES devices(id),
    signal_id INTEGER NOT NULL REFERENCES signals(id),
    observed_at_utc TEXT NOT NULL,
    received_at_utc TEXT NOT NULL,
    value_real REAL,
    raw_value INTEGER,
    quality TEXT NOT NULL CHECK(quality IN ('good','unavailable','invalid','stale')),
    ingest_source TEXT NOT NULL,
    PRIMARY KEY (device_id, signal_id, observed_at_utc)
);
CREATE INDEX IF NOT EXISTS idx_samples_signal_time
    ON samples(signal_id, observed_at_utc);

CREATE TABLE IF NOT EXISTS device_events (
    id INTEGER PRIMARY KEY,
    device_id INTEGER NOT NULL REFERENCES devices(id),
    observed_at_utc TEXT NOT NULL,
    severity TEXT NOT NULL CHECK(severity IN ('info','warning','alarm','critical')),
    event_code TEXT NOT NULL,
    message TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_time
    ON device_events(observed_at_utc);

CREATE TABLE IF NOT EXISTS ingest_batches (
    id INTEGER PRIMARY KEY,
    device_id INTEGER NOT NULL REFERENCES devices(id),
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT,
    received_count INTEGER NOT NULL DEFAULT 0,
    accepted_count INTEGER NOT NULL DEFAULT 0,
    rejected_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK(status IN ('started','success','partial','failed')),
    error_code TEXT
);

-- Intentionally NO DROP/DELETE triggers, NO polling code, NO insert of fake samples.
-- Retention, off-site backup, WAL checkpoint policy, and permissions must be
-- specified before enabling ingestion. Never modify the active HA database.
