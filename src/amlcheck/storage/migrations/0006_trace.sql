-- 0006_trace.sql (P6): trace jobs and results. Source: docs/05-data-model.md. Append-only once released.

CREATE TABLE traces (
    trace_id        TEXT PRIMARY KEY,
    chain           TEXT NOT NULL,
    address_norm    TEXT NOT NULL,
    direction       TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    status          TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed')),
    requested_by    TEXT NOT NULL,             -- cli | web | api | check | monitor
    idempotency_key TEXT UNIQUE,
    settings_json   TEXT NOT NULL,             -- [trace] values + trace_version
    as_of           TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT,
    progress_json   TEXT,
    result_json     TEXT,                      -- methodology §7.8
    partial_json    TEXT,
    failure_reason  TEXT
);
CREATE INDEX traces_by_address ON traces (chain, address_norm, created_at);
CREATE INDEX traces_by_status  ON traces (status);
