-- 0011_explorer_tags.sql (2.1): explorer tags (methodology §13.3, D-100). Source: docs/05-data-model.md. Append-only once released.

-- Each address's public tag as the explorer last gave it: '' when it has none. A cache: looked up
-- again after `[intel] tag_days`; a failed lookup stores nothing.
CREATE TABLE explorer_tags (
    chain         TEXT NOT NULL,
    address_norm  TEXT NOT NULL,
    public_tag    TEXT NOT NULL,
    source        TEXT NOT NULL,               -- 'tronscan'
    fetched_at    TEXT NOT NULL,
    PRIMARY KEY (chain, address_norm)
);
