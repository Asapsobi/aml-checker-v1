-- 0009_monitor_api.sql (P10): own wallets, monitor positions, API idempotency. Source: docs/05-data-model.md. Append-only once released.

CREATE TABLE own_wallets (
    chain         TEXT NOT NULL,
    address_norm  TEXT NOT NULL,
    name          TEXT NOT NULL,
    added_at      TEXT NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (chain, address_norm)
);

CREATE TABLE monitor_state (
    chain           TEXT NOT NULL,
    address_norm    TEXT NOT NULL,
    last_seen_time  TEXT NOT NULL,
    last_run_at     TEXT NOT NULL,
    PRIMARY KEY (chain, address_norm)
);

-- API idempotency (IETF Idempotency-Key draft). Keys never expire, like the records they point to.
CREATE TABLE api_requests (
    idempotency_key  TEXT PRIMARY KEY,
    fingerprint      TEXT NOT NULL,            -- sha256 of the request as understood
    kind             TEXT NOT NULL,            -- check | trace
    ref_id           TEXT NOT NULL,            -- check_id or trace_id
    created_at       TEXT NOT NULL
);
