# amlcheck — Data model

> The SQLite schema, as the migrations Claude Code writes, phase by phase. Migrations are numbered and
> **append-only**: once released, a file is never edited; changes go in a new file. Timestamps are
> ISO-8601 UTC text; amounts are decimal text in USDT (never floats); addresses are stored normalised
> (`address_norm`: TRON base58 `T…`, EVM lowercase `0x…`).

| Migration | Phase | Tables |
|---|---|---|
| `0001_cache.sql` | P1 | `transfers`, `history_windows`, `contracts` (no `http_cache`, D-037) |
| `0002_screening.sql` | P2 | `list_snapshots`, `sanctioned_addresses`, `issuer_events`, `index_state`, `checks`, `check_sources`, `check_findings` |
| `0003_labels.sql` | P3 | `labels` |
| `0004_intel.sql` | P4 | `intel_labels`, `entities`, `entity_members`, `counterparties` |
| `0005_classifier.sql` | P5 | `classifications` |
| `0006_trace.sql` | P6 | `traces` |
| `0007_ops.sql` | P8 | `watchlist` |
| `0008_cases.sql` | P9 | `cases`, `decisions`, `inference_feedback` |
| `0009_monitor_api.sql` | P10 | `own_wallets`, `monitor_state`, `api_requests` |
| `0010_risk_v2.sql` | P12 | `counterparties.last_score_version` |

`storage/db.py` keeps the applied version in `PRAGMA user_version` and applies missing files in order,
each in one transaction.

---

## 0001_cache.sql (P1)

```sql
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
```

---

## 0002_screening.sql (P2)

```sql
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
```

---

## 0003_labels.sql (P3)

```sql
-- labels.csv, replaced whole on every import (all-or-nothing).
CREATE TABLE labels (
    chain         TEXT NOT NULL,
    address_norm  TEXT NOT NULL,
    tag           TEXT NOT NULL,               -- mixer | bridge | high_risk | allowlist | free text
    note          TEXT,
    source        TEXT
);
CREATE INDEX labels_by_address ON labels (chain, address_norm);
```

---

## 0004_intel.sql (P4)

```sql
-- Every label except labels.csv. Retracted, never deleted.
CREATE TABLE intel_labels (
    id                INTEGER PRIMARY KEY,
    chain             TEXT    NOT NULL,
    address_norm      TEXT    NOT NULL,
    category          TEXT    NOT NULL,        -- methodology §8
    provenance        TEXT    NOT NULL CHECK (provenance IN ('list', 'operator', 'import', 'inferred')),
    source_ref        TEXT    NOT NULL,        -- 'ofac:<entry>', 'pack:<name>', 'case:<id>', 'entity:<id>'
    confidence        TEXT    NOT NULL,        -- '1' for list/operator/import
    note              TEXT,
    licence           TEXT,                    -- required when provenance = 'import'
    category_version  INTEGER NOT NULL,
    created_by        TEXT,
    created_at        TEXT    NOT NULL,
    retracted_at      TEXT,
    retracted_by      TEXT,
    retract_reason    TEXT
);
CREATE INDEX intel_labels_active ON intel_labels (chain, address_norm) WHERE retracted_at IS NULL;

CREATE TABLE entities (
    id          INTEGER PRIMARY KEY,
    chain       TEXT NOT NULL,
    name        TEXT NOT NULL,                 -- 'hub-TXyz1234' until named
    kind        TEXT NOT NULL,                 -- a category, or 'unknown'
    named_by    TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE entity_members (
    entity_id      INTEGER NOT NULL REFERENCES entities (id),
    chain          TEXT    NOT NULL,
    address_norm   TEXT    NOT NULL,
    role           TEXT    NOT NULL,           -- hub | deposit | member
    provenance     TEXT    NOT NULL,           -- inferred | operator
    evidence_json  TEXT    NOT NULL,
    linked_at      TEXT    NOT NULL,
    PRIMARY KEY (chain, address_norm)          -- at most one entity per address
);

-- One row per counterparty. Derived from the audit log (`cp rebuild` recreates it exactly).
CREATE TABLE counterparties (
    chain              TEXT    NOT NULL,
    address_norm       TEXT    NOT NULL,
    lookalike_key      TEXT    NOT NULL,       -- methodology §4
    first_screened_at  TEXT    NOT NULL,
    last_screened_at   TEXT    NOT NULL,
    last_check_id      TEXT    NOT NULL REFERENCES checks (check_id),
    last_verdict       TEXT    NOT NULL,
    last_score         INTEGER,
    clients_json       TEXT    NOT NULL DEFAULT '[]',
    check_count        INTEGER NOT NULL,
    PRIMARY KEY (chain, address_norm)
);
CREATE INDEX counterparties_lookalike ON counterparties (chain, lookalike_key);
```

---

## 0005_classifier.sql (P5)

```sql
CREATE TABLE classifications (
    id                  INTEGER PRIMARY KEY,
    chain               TEXT    NOT NULL,
    address_norm        TEXT    NOT NULL,
    type                TEXT    NOT NULL,      -- methodology §6
    is_primary          INTEGER NOT NULL,
    confidence          TEXT    NOT NULL,
    features_json       TEXT    NOT NULL,      -- the profile and the conditions that held
    window_since        TEXT    NOT NULL,
    window_until        TEXT    NOT NULL,
    classifier_version  INTEGER NOT NULL,
    computed_at         TEXT    NOT NULL,
    expires_at          TEXT    NOT NULL
);
CREATE INDEX classifications_by_address ON classifications (chain, address_norm, computed_at);
```

---

## 0006_trace.sql (P6)

```sql
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
```

---

## 0007_ops.sql (P8)

```sql
CREATE TABLE watchlist (
    chain            TEXT NOT NULL,
    address_norm     TEXT NOT NULL,
    client           TEXT,
    note             TEXT,
    added_at         TEXT NOT NULL,
    last_checked_at  TEXT,
    last_verdict     TEXT,
    last_check_id    TEXT,
    PRIMARY KEY (chain, address_norm)
);
```

---

## 0008_cases.sql (P9)

```sql
CREATE TABLE cases (
    case_id       TEXT PRIMARY KEY,
    chain         TEXT NOT NULL,
    address_norm  TEXT NOT NULL,
    opened_from   TEXT NOT NULL REFERENCES checks (check_id),
    opened_by     TEXT NOT NULL,
    opened_at     TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('open', 'closed')),
    client        TEXT
);
CREATE INDEX cases_by_address ON cases (chain, address_norm);

-- A second hash chain. check_record_hash ties each decision to the exact check record.
CREATE TABLE decisions (
    seq                INTEGER PRIMARY KEY,
    decision_id        TEXT NOT NULL UNIQUE,
    case_id            TEXT NOT NULL REFERENCES cases (case_id),
    check_id           TEXT NOT NULL REFERENCES checks (check_id),
    check_record_hash  TEXT NOT NULL,
    decision           TEXT NOT NULL CHECK (decision IN ('approved', 'rejected', 'escalated')),
    note               TEXT NOT NULL,
    operator           TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    tool_version       TEXT NOT NULL,
    prev_hash          TEXT NOT NULL,
    record_hash        TEXT NOT NULL UNIQUE
);

CREATE TABLE inference_feedback (
    id                  INTEGER PRIMARY KEY,
    case_id             TEXT    NOT NULL REFERENCES cases (case_id),
    chain               TEXT    NOT NULL,
    address_norm        TEXT    NOT NULL,
    type                TEXT    NOT NULL,
    classifier_version  INTEGER NOT NULL,
    verdict             TEXT    NOT NULL CHECK (verdict IN ('confirmed', 'rejected')),
    label_id            INTEGER REFERENCES intel_labels (id),
    operator            TEXT    NOT NULL,
    created_at          TEXT    NOT NULL
);
CREATE INDEX inference_feedback_by_address ON inference_feedback (chain, address_norm);
```

---

## 0009_monitor_api.sql (P10)

```sql
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
```

---

## 0010_risk_v2.sql (P12)

```sql
-- Which score version `last_score` is (methodology §11, D-071): lists show a v1 score with its v1
-- band and a v2 score with its level. NULL = before this migration, a v1 score; `cp rebuild` fills it.
ALTER TABLE counterparties ADD COLUMN last_score_version INTEGER;
```

The check record needs no new column: exposures are in the sources' evidence, and the v2 score is
in `score_json` with `"score_version": 2`.

---

## Retention

| Data | Kept |
|---|---|
| `checks`, `check_*`, `decisions`, `cases`, `api_requests`, `intel_labels`, `inference_feedback` | Forever (no purge command) |
| `counterparties`, `entities`, `entity_members`, `own_wallets`, `watchlist`, `labels` | Forever (small) |
| `classifications` | Latest per address and type forever; older may be pruned |
| `traces` | Forever when linked to a check; others for `[cache] history_keep_days` |
| `transfers`, `history_windows` | Cache: pruned for addresses outside registry, entities, labels and own wallets after `history_keep_days` (30) |
