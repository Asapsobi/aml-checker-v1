-- 0010_risk_v2.sql (P12): risk policy v2 (methodology §11, D-071, D-073). Source: docs/05-data-model.md. Append-only once released.

-- Which score version `last_score` is, so a list shows a v1 score with its v1 band and a v2 score
-- with its level. NULL on rows from before this migration means version 1; `cp rebuild` fills it.
ALTER TABLE counterparties ADD COLUMN last_score_version INTEGER;

-- The checked address's own label at check time (methodology §11.6), JSON. Hashed in the audit
-- record only when set, so records from before P12 keep verifying.
ALTER TABLE checks ADD COLUMN label_json TEXT;
