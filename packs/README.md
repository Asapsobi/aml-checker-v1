# Label packs from public sources

Imported with `amlcheck intel import-pack` (PRD F7.4). Each pack's licence check is in
`docs/verification-log.md` (VS-14), and the decision to use it is D-102. Import, or re-import after
an update (a re-import replaces the pack's earlier labels):

```bash
uv run amlcheck intel import-pack packs/htx-por-20260901.csv --name htx-por-20260901 --licence "MIT (huobiapi/Tool-Node.js-VerifyAddress, HTX's own proof-of-reserves, snapshot 2026-09-01)"
uv run amlcheck intel import-pack packs/wallet-attribution-exmo.csv --name wallet-attribution-exmo --licence "MIT (prettydeath/wallet-attribution 2026-10-01; upstream DefiLlama-Adapters cex registry)"
uv run amlcheck intel import-pack packs/openaml-bsc-fraud.csv --name openaml-bsc-fraud --licence "Apache-2.0 (finos-labs/dtcch-2025-OpenAML Data/address_fraud.csv.gz, March 2026)"
uv run amlcheck intel import-pack packs/spellbook-htx-tron.csv --name spellbook-htx-tron --licence "BSL 1.1, internal use (duneanalytics/spellbook cex_tron_addresses.sql @9f61b0dd; GPL-3.0+ from 2027-03-03)"
```

`public-reports.csv` holds addresses named in public investigations, with the source and date. They
carry no licence, so they go in as the owner's own labels (D-103), one command each:

```bash
uv run amlcheck intel label TL8TBpubVzBr1UWPXBXU8Pci5ZAip9SwEf high_risk --note "suspected Haowang Guarantee (SlowMist), 2025-06-23"
uv run amlcheck intel label T9yFi9yxwBUjMbHwBFKDdwFdBwvzUAqBfR high_risk --note "suspected Haowang Guarantee (SlowMist), 2025-06-23"
```

| Pack | Rows | Category | Source |
|---|---|---|---|
| `htx-por-20260901` | 14 TRON | `sanctioned_entity` (HTX, UK RUS3619) | HTX's own proof-of-reserves file, signed per row; without `TZ1Ssapy…`, which backs USDD (DefiLlama excludes it as not HTX's) |
| `spellbook-htx-tron` | 8 TRON | `sanctioned_entity` (HTX) | Dune Spellbook's HTX wallets (2024-04) not in HTX's current file |
| `public-reports.csv` | 2 TRON | `high_risk`, owner's labels | SlowMist's HuionePay analysis: the two addresses it calls "suspected Haowang Guarantee" |
| `wallet-attribution-exmo` | 1 TRON | `sanctioned_entity` (EXMO, UK RUS3602) | EXMO's wallet in DefiLlama's exchange registry, via wallet-attribution |
| `openaml-bsc-fraud` | 149 BSC | `scam` | OpenAML (FINOS) "Fraud" wallets on BSC |

Not used (VS-14 and VS-25 say why): Spellbook's and DefiLlama's other exchanges (a labelled exchange
ends a trace before what sits behind it is read, D-097/D-099, so it would hide risk), GoPlus, Chainabuse
and MistTrack's API (third-party risk APIs, D-033), wallet-attribution's BSC labels (scraped from BscScan, whose terms
forbid it; some wrong), its TRON frozen and OFAC rows (we read both at source), Blockchainwatch
(unsourced risk scores), evm-labels (Ethereum only), OpenAML's "Flagged" wallets (community flags, not
findings) and its models (pickle files: loading one can run code).
