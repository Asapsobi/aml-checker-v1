-- 0008_cases.sql (P9): cases, the decision chain and inference feedback. Source: docs/05-data-model.md. Append-only once released.

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
