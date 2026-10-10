-- 0012_incidents.sql (2.2): public incidents that reveal a designated entity's wallets (methodology §13.5, D-101). Source: docs/05-data-model.md. Append-only once released.

-- Every wallet an incident reveals, e.g. the 110,626 Nobitex addresses drained on 2025-06-18.
CREATE TABLE incident_addresses (
    incident      TEXT NOT NULL,               -- intel/incidents.py id
    chain         TEXT NOT NULL,
    address_norm  TEXT NOT NULL,
    first_time    TEXT NOT NULL,               -- its first transfer into the sink
    usdt          TEXT NOT NULL,               -- what it sent there
    PRIMARY KEY (chain, address_norm, incident)
);

-- One row per built incident: `sync` builds a missing one (the event is over).
CREATE TABLE incident_index (
    incident   TEXT PRIMARY KEY,
    built_at   TEXT NOT NULL,
    rule       TEXT NOT NULL,                  -- how senders were picked; another rule rebuilds
    transfers  INTEGER NOT NULL,
    addresses  INTEGER NOT NULL,
    usdt       TEXT NOT NULL
);
