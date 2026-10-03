-- 0003_labels.sql (P3): labels.csv. Source: docs/05-data-model.md. Append-only once released.

-- labels.csv, replaced whole on every import (all-or-nothing).
CREATE TABLE labels (
    chain         TEXT NOT NULL,
    address_norm  TEXT NOT NULL,
    tag           TEXT NOT NULL,               -- mixer | bridge | high_risk | allowlist | free text
    note          TEXT,
    source        TEXT
);
CREATE INDEX labels_by_address ON labels (chain, address_norm);
