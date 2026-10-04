-- Additive migration: no local records are removed or modified.
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE TABLE IF NOT EXISTS road_coach_events (
    occurred_at TIMESTAMPTZ NOT NULL,
    event_key TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    kind TEXT NOT NULL,
    drive_id TEXT NOT NULL,
    sign_id TEXT,
    grade TEXT,
    payload JSONB NOT NULL,
    PRIMARY KEY (occurred_at, event_key)
);
SELECT create_hypertable('road_coach_events', by_range('occurred_at'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS road_coach_drive_time ON road_coach_events (drive_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS road_coach_sign_time ON road_coach_events (sign_id, occurred_at DESC);
