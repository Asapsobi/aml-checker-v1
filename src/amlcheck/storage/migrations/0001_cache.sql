-- 0001_cache.sql (P1): the transfer cache. Source: docs/05-data-model.md. Append-only once released.

-- Every USDT transfer read for any address, stored once. `idx` tells transfers in one transaction
-- apart: on BSC the log index; on TRON (no index in TronGrid rows, VS-04) the number of earlier rows
-- of the same tx with the identical (sender, recipient, amount), so either side's history yields the
-- same key (D-030).
CREATE TABLE transfers (
    chain       TEXT    NOT NULL,
    tx_hash     TEXT    NOT NULL,
    idx         INTEGER NOT NULL,
    block       INTEGER,
    time        TEXT    NOT NULL,
    sender      TEXT    NOT NULL,
    recipient   TEXT    NOT NULL,
    amount      TEXT    NOT NULL,
    PRIMARY KEY (chain, tx_hash, sender, recipient, amount, idx)
);
CREATE INDEX transfers_in  ON transfers (chain, recipient, time);
CREATE INDEX transfers_out ON transfers (chain, sender, time);

-- Which part of an address's history is in `transfers`. Reads ask providers only for the gaps.
CREATE TABLE history_windows (
    chain           TEXT    NOT NULL,
    address_norm    TEXT    NOT NULL,
    since           TEXT    NOT NULL,
    until           TEXT    NOT NULL,
    complete        INTEGER NOT NULL,          -- 0 when the window held more than the read limit
    transfer_count  INTEGER NOT NULL,
    zero_value      INTEGER NOT NULL DEFAULT 0,
    first_activity  TEXT,
    fetched_at      TEXT    NOT NULL,
    last_used_at    TEXT    NOT NULL,
    PRIMARY KEY (chain, address_norm, since)
);

-- Whether an address is a contract never changes: cached forever.
CREATE TABLE contracts (
    chain         TEXT    NOT NULL,
    address_norm  TEXT    NOT NULL,
    is_contract   INTEGER NOT NULL,
    checked_at    TEXT    NOT NULL,
    source        TEXT    NOT NULL,
    PRIMARY KEY (chain, address_norm)
);
