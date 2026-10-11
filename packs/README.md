# Label packs from public sources

Imported with `amlcheck intel import-pack` (PRD F7.4). Each pack's licence check is in
`docs/verification-log.md` (VS-14), and the decision to use it is D-102. Import, or re-import after
an update (a re-import replaces the pack's earlier labels):

```bash
uv run amlcheck intel import-pack packs/htx-por-20260901.csv --name htx-por-20260901 --licence "MIT (huobiapi/Tool-Node.js-VerifyAddress, HTX's own proof-of-reserves, snapshot 2026-09-01)"
uv run amlcheck intel import-pack packs/wallet-attribution-exmo.csv --name wallet-attribution-exmo --licence "MIT (prettydeath/wallet-attribution 2026-10-01; upstream DefiLlama-Adapters cex registry)"
uv run amlcheck intel import-pack packs/openaml-bsc-fraud.csv --name openaml-bsc-fraud --licence "Apache-2.0 (finos-labs/dtcch-2025-OpenAML Data/address_fraud.csv.gz, March 2026)"
```

| Pack | Rows | Category | Source |
|---|---|---|---|
| `htx-por-20260901` | 15 TRON | `sanctioned_entity` (HTX, UK RUS3619) | HTX's own proof-of-reserves file, signed per row |
| `wallet-attribution-exmo` | 1 TRON | `sanctioned_entity` (EXMO, UK RUS3602) | EXMO's wallet in DefiLlama's exchange registry, via wallet-attribution |
| `openaml-bsc-fraud` | 149 BSC | `scam` | OpenAML (FINOS) "Fraud" wallets on BSC |

Not used (VS-14 entries say why): wallet-attribution's BSC labels (scraped from BscScan, whose terms
forbid it; some wrong), its TRON frozen and OFAC rows (we read both at source), Blockchainwatch
(unsourced risk scores), evm-labels (Ethereum only), OpenAML's "Flagged" wallets (community flags, not
findings) and its models (pickle files: loading one can run code).
