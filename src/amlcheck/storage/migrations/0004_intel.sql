-- 0004_intel.sql (P4): intel labels, entities, counterparty registry. Source: docs/05-data-model.md.
-- Append-only once released.

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
