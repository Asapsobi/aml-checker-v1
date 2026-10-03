-- 0005_classifier.sql (P5): classifications. Source: docs/05-data-model.md. Append-only once released.

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
