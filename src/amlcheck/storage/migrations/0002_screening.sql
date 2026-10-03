-- 0002_screening.sql (P2): sanctions, freeze index, audit log. Source: docs/05-data-model.md.
-- Append-only once released.

-- Sanctions: one row per accepted snapshot, and the addresses parsed from it.
CREATE TABLE list_snapshots (
    id             INTEGER PRIMARY KEY,
    source         TEXT    NOT NULL,           -- 'ofac_sdn'
    fetched_at     TEXT    NOT NULL,
    published_at   TEXT,
    sha256         TEXT    NOT NULL,
    entry_count    INTEGER NOT NULL,
    address_count  INTEGER NOT NULL
);

CREATE TABLE sanctioned_addresses (
    snapshot_id     INTEGER NOT NULL REFERENCES list_snapshots (id),
    address_norm    TEXT    NOT NULL,
    currency_label  TEXT    NOT NULL,
    list_entry_id   TEXT    NOT NULL,
    entity_name     TEXT,
    program         TEXT,
    checksum_ok     INTEGER NOT NULL
);
CREATE INDEX sanctioned_by_address ON sanctioned_addresses (address_norm, snapshot_id);

-- Local index of issuer blacklist events (Tether USDT on TRON; v1 has no other index, D-039).
CREATE TABLE issuer_events (
    chain           TEXT    NOT NULL,
    token_contract  TEXT    NOT NULL,
    address_norm    TEXT    NOT NULL,
    event_type      TEXT    NOT NULL,          -- AddedBlackList | RemovedBlackList | DestroyedBlackFunds
    amount          TEXT,                      -- DestroyedBlackFunds balance
    tx_hash         TEXT    NOT NULL,
    event_index     INTEGER NOT NULL,
    block           INTEGER NOT NULL,
    block_time      TEXT    NOT NULL,
    UNIQUE (chain, token_contract, tx_hash, event_index)
);
CREATE INDEX issuer_events_by_address ON issuer_events (chain, address_norm, block);

CREATE TABLE index_state (
    source           TEXT    PRIMARY KEY,
    last_block       INTEGER NOT NULL,
    last_block_time  TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL
);

-- Audit log: append-only, hash-chained.
-- record_hash = sha256(prev_hash || canonical_json(check + sources + findings)).
-- Nullable columns are part of the hashed record only when set, so adding one later never breaks
-- records written before it.
CREATE TABLE checks (
    seq            INTEGER PRIMARY KEY,
    check_id       TEXT    NOT NULL UNIQUE,
    created_at     TEXT    NOT NULL,
    chain          TEXT    NOT NULL,
    address_norm   TEXT    NOT NULL,
    verdict        TEXT    NOT NULL CHECK (verdict IN ('BLOCK', 'INCOMPLETE', 'REVIEW', 'NO_HITS')),
    amount         TEXT,
    client         TEXT,
    operator_note  TEXT,
    score_json     TEXT,                       -- P7: {"score_version":1,"score":66,"band":"high",…}
    trace_id       TEXT,                       -- P6: the trace this check included
    tool_version   TEXT    NOT NULL,
    rules_version  INTEGER NOT NULL,
    config_hash    TEXT    NOT NULL,
    prev_hash      TEXT    NOT NULL,
    record_hash    TEXT    NOT NULL UNIQUE
);
CREATE INDEX checks_by_address ON checks (chain, address_norm);
CREATE INDEX checks_by_time    ON checks (created_at);
CREATE INDEX checks_by_client  ON checks (client COLLATE NOCASE);

CREATE TABLE check_sources (
    check_id            TEXT    NOT NULL REFERENCES checks (check_id),
    source              TEXT    NOT NULL,
    required            INTEGER NOT NULL,
    status              TEXT    NOT NULL CHECK (status IN ('ok', 'error', 'stale', 'skipped')),
    as_of               TEXT,
    summary             TEXT    NOT NULL,
    evidence_meta_json  TEXT    NOT NULL,
    attribution         TEXT                   -- e.g. a provider's required credit line
);
CREATE INDEX check_sources_by_check ON check_sources (check_id);

CREATE TABLE check_findings (
    check_id       TEXT NOT NULL REFERENCES checks (check_id),
    rule_id        TEXT NOT NULL,
    severity       TEXT NOT NULL,
    source         TEXT NOT NULL,
    summary        TEXT NOT NULL,
    evidence_json  TEXT NOT NULL,              -- includes "priority": "low" where relevant
    observed_at    TEXT NOT NULL
);
CREATE INDEX check_findings_by_check ON check_findings (check_id);
