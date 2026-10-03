-- 0007_ops.sql (P8): the watchlist (PRD F11.3, D-057). Source: docs/05-data-model.md. Append-only once released.

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
