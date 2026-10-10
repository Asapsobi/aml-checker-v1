# amlcheck — Data sources

> Every external source the product uses, what is known about it, and what must be verified live before
> code relies on it. Facts below were checked by hand between **2026-09-28 and 2026-09-30**. Providers
> change: **P0 re-verifies every row marked "verify"** and records the result in
> `docs/verification-log.md` (VS-01 … VS-16, below).

---

## 1. Source map

| Need | Source | Cost | Status |
|---|---|---|---|
| Sanctions | OFAC SDN list (`SDN.XML`) | Free, public domain | Use |
| Sanctions, extra | UK OFSI consolidated list | Free | Verify format and whether it carries crypto addresses (VS-11) |
| Sanctions, second opinion | Chainalysis free sanctions API | — | **Not available**: sign-up now leads to a paid product |
| Sanctions, aggregated | OpenSanctions | Paid for commercial use (CC BY-NC 4.0) | **Out** unless a licence is bought |
| Issuer freezes, TRON | Tether USDT contract events via TronGrid | Free | Use (local index) |
| Issuer freezes, other EVM chains | Tether and Circle contracts on Ethereum, Arbitrum, Polygon, … | — | **Not in v1** (D-039): v1 covers TRC20 and BEP20 only. VS-15 findings kept for a later version |
| Issuer freezes, third-party API | Eagle Virtual and similar AML / freeze APIs | — | **Out** (D-033): only RPC providers and indexers |
| TRON chain data | TronGrid | Free with key; limits per key | Use |
| BSC chain data | Envio HyperSync | Free plan with token | Use |
| BSC chain data, paid fallback | Etherscan V2 (Lite plan) | $49/month | Fallback only, owner decision |
| BSC contract detection | Public BSC JSON-RPC `eth_getCode` at `bsc-dataseed.bnbchain.org`, no key | Free | Use (VS-12): no published limits; amlcheck paces 2/s and caches forever |
| Attribution | Commercial vendor (Chainalysis, TRM, Elliptic, Crystal) | Paid | Not in v1. Adapter point only |
| Labels | Own `labels.csv`, operator labels, licensed label packs | — | Use; packs need a recorded licence |
| Designated entities' wallets | Tronscan public tags (`/api/account/tag`, owner's key; terms: their API only) | Free with key | Use (2.1, D-100, VS-23): busy wallets a trace stops at, cached 7 days |

---

## 2. OFAC SDN list

| Fact | Detail |
|---|---|
| URL | `https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.XML` → 302 to a short-lived signed S3 URL, no key. **`…/exports/SDN_XML.ZIP` holds the same file zipped** (2.6 MB; verified 2026-10-03, D-040). The S3 copies answer `Range` requests (`Accept-Ranges: bytes`); their ETags are not plain MD5s |
| Size | ~29 MB as XML, 2.6 MB as ZIP (the "advanced" XML is ~127 MB and slow; not needed). On a slow line (12–130 KB/s seen) the XML can take 35 min and stall; amlcheck downloads the ZIP and resumes after stalls |
| Publish date | In the file: `<publshInformation><Publish_Date>MM/DD/YYYY</Publish_Date>` (OFAC's spelling) |
| Addresses | `<sdnEntry>` → `<id><idType>Digital Currency Address - XXX</idType><idNumber>…</idNumber></id>`, with `<uid>` and `<programList>` |
| Size of the crypto part (2026-09-30 list, VS-01) | 1,066 addresses on 106 entries under 20 currency labels; 341 TRON, 133 `0x` |
| **Labels are unreliable** | TRON addresses filed under XBT; USDT-labelled Bitcoin (Omni) addresses; the only "BNB" entry is a retired Beacon Chain `bnb1…` address; one `0x` address sits under a newer "BSC" label. **Match on the address string only** |
| Checksums | All TRON entries pass base58check; mixed-case `0x` entries pass EIP-55. Keep any entry that fails and log it |
| Daily deltas | `https://sanctionslistservice.ofac.treas.gov/changes/latest` |
| Freshness | OFAC does not publish daily. Measure staleness from **our** last successful download |

---

## 3. Tether USDT on TRON

| Fact | Detail |
|---|---|
| Contract | `TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t` (`TetherToken`, symbol USDT, **6 decimals**) |
| Events | `AddedBlackList(address indexed _user)`, `RemovedBlackList(address indexed _user)`, `DestroyedBlackFunds(address indexed _blackListedUser, uint256 _balance)` |
| Read functions | `isBlackListed(address) → bool`, `getBlackListStatus(address) → bool` |
| History | Events from 2020-06-26 onward: 10,923 in all on 2026-10-03 (8,722 added, 971 removed, 1,230 destroyed; 7,727 addresses frozen now). First full sync 24–64 s depending on the connection (VS-15) |
| Paging | `GET /v1/contracts/{contract}/events?event_name=…`, 200 per page via `meta.links.next`; `only_confirmed=true`, `min_block_timestamp` |
| Confirmed head | `POST /walletsolidity/getnowblock` (~1 min behind latest) |
| Address format | Event addresses come as `0x` hex without the `41` prefix → convert to base58 `T…` |
| **Retirement risk** | Contract has `deprecate()`, `deprecated()`, `upgradedAddress()`. The indexer must check `deprecated()` on every sync and treat `true` as an error (INCOMPLETE) |
| Pause | `pause()` exists; not paused today |
| Quirk | The USDT contract address is itself blacklisted |

---

## 4. USDT on BSC (BEP20)

| Fact | Detail |
|---|---|
| Contract | `0x55d398326f99059fF775485246999027B3197955` (Binance-Peg "Tether USD", **18 decimals**) |
| Proxy | No (EIP-1967 and older proxy slots empty) |
| Functions | Standard Binance BEP20Token template: BEP-20 basics + `mint`, `burn`, ownership |
| **Freeze capability** | **None.** No freeze, blacklist, pause or seize function, no blacklist event |
| Consequence | Token-level freeze check is always `skipped` on BSC; every BSC result says so |
| Other stablecoins on BSC | Some *can* freeze (AUSD, XUSD, USD0). Out of scope as tokens. Freezes of the same `0x` address on other chains are not checked in v1, and BSC results say so (D-039) |

---

## 5. Other EVM chains (not in v1)

v1 covers USDT on TRC20 and BEP20 only (D-039). A Tether or Circle freeze of the same `0x` address on
another EVM chain would matter for a BSC check (one key controls the address everywhere, D-010), but
indexing other chains is left for a later version. BSC results say plainly that it isn't checked.
What VS-15 found before the scope was narrowed (contracts, event names, counts per chain) is in the
verification log. Third-party freeze APIs are out (D-033); the P0 Eagle Virtual findings are there too.

## 6. TronGrid (TRON chain data)

| Fact | Detail |
|---|---|
| Auth | Header `TRON-PRO-API-KEY`; works without a key but IP-limited |
| Limits | **Not published**, set per key in the console. Owner's key (VS-05, 2026-10-01): **15 requests/s**; over it → `429`, body `{"Error":"The key exceeds the frequency limit(15), and the query server is suspended for 30 s"}`, **no `Retry-After` and no rate-limit headers**, key suspended 30 s. Without a key: 1 request/s, suspended 5 s. Treat 403 like 429; read the limit from config (Q-20) |
| Transfers | `GET /v1/accounts/{address}/transactions/trc20?contract_address=…&only_confirmed=true&min_timestamp=…&order_by=block_timestamp,desc&limit=200`, paged via `meta.links.next`. Rows: `transaction_id`, `block_timestamp` (ms), `from`, `to`, `value`, `type`, `token_info`. **No block number, no event index** (VS-04). One transaction can hold several USDT transfers, and which of them a history shows depends on whose history it is (Q-17) |
| Account | `POST /wallet/getaccount` → `create_time` (ms); `{}` when never activated. USDT can arrive at a never-activated address. A contract created by a contract has **no** `create_time` (VS-10) |
| Contract calls | `POST /wallet/triggerconstantcontract`; `POST /wallet/getcontract` for contract detection: `{}` for a wallet, an object with `contract_address` for a contract (no `bytecode` when a contract created it; VS-13) |
| Speed | Newest 5,000 transfers of a busy hot wallet: ~25 pages, ~20 s |

---

## 7. Envio HyperSync (BSC chain data)

| Fact | Detail |
|---|---|
| Endpoint | `POST https://bsc.hypersync.xyz/query` (also `https://56.hypersync.xyz` — use as DNS fallback) |
| Auth | `Authorization: Bearer <token>`, free token at `https://app.envio.dev/api-tokens`. No token → 401. `GET /height` needs no token |
| Height | `GET /height` → `{"height": N}` |
| Answer | `data[]` batches of `blocks`, `logs`, `transactions`; `next_block`, `archive_height`, `rollback_guard`, `total_execution_time`. **Block timestamps are hex strings**; topics in `topic1`/`topic2`; logs carry `log_index` (VS-06) |
| Order | **Oldest first only.** For newest-first reading, query block windows from the head down |
| Paging | Stops near ~1,000 logs or a few seconds of work; continue from `next_block`. No progress / no `next_block` → error |
| **Budget (free plan, checked 2026-10-01)** | 30 queries per minute: `x-ratelimit-limit: 30000, 30000;w=60`, `x-ratelimit-cost: 1000` per query, `x-ratelimit-remaining`, `x-ratelimit-reset` (seconds to the next window). **One budget per token across `bsc.` and `56.` hosts.** Refusal = 429 with empty body and **no** `Retry-After`, but `remaining: 0` and `reset` present. The cost header read `0` on 2026-09-29 — **pace from the headers, don't hard-code** |
| Completeness | 20 of 20 random real transfers found; rows matched raw logs field for field (hash, log index, block, time, from, to, amount) |
| Speed | Quiet wallet's 180 days: 1–2 queries, 2.5–8 s (221 transfers: 1 query, 7.8 s on 2026-10-01). Busy wallet's newest 5,000: ~13.5 s. First-activity scan from block 0: 1 query, 10.7 s (active wallet); 2 queries, 11.7 s (never used) |
| Terms | Data "as is", UK law |
| BSC pace | ~0.45 s per block (find a window's first block from the pace, check against headers) |

---

## 8. Sources ruled out (and why)

| Source | Why not |
|---|---|
| Etherscan V2 free | BSC is "Paid Tier Only"; BscScan API retired into Etherscan V2 |
| NodeReal MegaNode | 100,000-block range per call (~12.5 h of BSC) → 180 days ≈ 345 calls each way; too slow on free and low plans |
| Public BSC RPC `eth_getLogs` | Refused or limited to tiny block ranges |
| SQD public portal | Accurate but ~2,000 blocks per answer and frequently overloaded |
| PublicAML | No published terms or licence; missed 5 of 10 sampled transfers |
| Chainalysis free API | Closed to new users |
| OpenSanctions | Non-commercial licence |

---

## 9. Verification checklist for P0

Claude Code runs these before writing any adapter, records evidence (URL, date, response sample saved
as a fixture) in `docs/verification-log.md`, and raises a question for any change.

| ID | Verify | Blocks |
|---|---|---|
| VS-01 | OFAC `SDN.XML` URL, redirect, structure, digital-currency `idType` list | P2 |
| VS-02 | Tether TRON contract: events, `isBlackListed`, `deprecated()` still false | P2 |
| VS-03 | BEP20 USDT bytecode still has no freeze function | P2 |
| VS-04 | TronGrid TRC20 transfer endpoint: filters (`min_timestamp`, `max_timestamp`), ordering, whether any per-transfer index exists for uniqueness | P1 |
| VS-05 | TronGrid limits on the owner's key; 403/429 behaviour | P1 |
| VS-06 | HyperSync query shape for USDT `Transfer` logs by address (topic1/topic2), log index field, budget headers | P1 |
| VS-07 | HyperSync incremental read cost (from a block) and a 31-address trace budget estimate | P6 |
| VS-08 | ~~Eagle Virtual spec version, plans, credit line, `/v1/usage`, chain list~~ Dropped (D-033) | — |
| VS-09 | ~~Eagle Virtual licence terms~~ Dropped (D-033) | — |
| VS-10 | First-activity definitions: TRON `getaccount.create_time` vs first transfer; BSC first tx/log scan | P3 |
| VS-11 | UK OFSI list: crypto addresses present? format? licence? | Deferred to after v1 (D-041) |
| VS-12 | A free BSC JSON-RPC endpoint for `eth_getCode` without a key, and its limits | P5 |
| VS-13 | TRON contract detection via `getcontract` (answer for a wallet vs a contract) | P5 |
| VS-14 | Licence of any label pack before import | P4 |
| VS-15 | Issuer freezes on TRC20 and BEP20: TRON event history and sync time; BEP20 USDT emits no freeze events (narrowed by D-039) | P2 |
| VS-16 | Cost of a full-history read (TronGrid pages; HyperSync queries from block 0) for quiet, ordinary and busy wallets, up to 20,000 transfers | P13 |
| VS-17 | HyperSync multi-address queries (batching): accepted, same results; 6 quiet addresses 1 query instead of 6, busy hubs no gain | P13 |
| VS-18 | UK Sanctions List XML (FCDO): format, TRON addresses in free text, licence (OGL v3.0) | P14 |
| VS-19 | EU Financial Sanctions Files XML: public URL, TRON addresses in free text, licence (Decision 2011/833/EU) | P14 |
| VS-20 | NBCTF seizure orders: the official export (matal.mod.gov.il), its format and where wallets are | P14 |
| VS-21 | Tronscan account-tag API: path, fields, rate limit, with the owner's key | P14 |
| VS-22 | TronGrid `only_to` / `only_from` on the TRC20 transfer endpoint: one side of an address's transfers | 2.0.2 |
| VS-23 | Designated entities in the OFAC and UK lists, and Tronscan's public tags on their wallets | 2.1 |
