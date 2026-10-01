# amlcheck — Data sources

> Every external source the product uses, what is known about it, and what must be verified live before
> code relies on it. Facts below were checked by hand between **2026-09-28 and 2026-09-30**. Providers
> change: **P0 re-verifies every row marked "verify"** and records the result in
> `docs/verification-log.md` (VS-01 … VS-14, below).

---

## 1. Source map

| Need | Source | Cost | Status |
|---|---|---|---|
| Sanctions | OFAC SDN list (`SDN.XML`) | Free, public domain | Use |
| Sanctions, extra | UK OFSI consolidated list | Free | Verify format and whether it carries crypto addresses (VS-11) |
| Sanctions, second opinion | Chainalysis free sanctions API | — | **Not available**: sign-up now leads to a paid product |
| Sanctions, aggregated | OpenSanctions | Paid for commercial use (CC BY-NC 4.0) | **Out** unless a licence is bought |
| Issuer freezes, TRON | Tether USDT contract events via TronGrid | Free | Use (local index) |
| Issuer freezes, all chains | Eagle Virtual API | Free plan; Business for volume | Use for the **target address only** |
| TRON chain data | TronGrid | Free with key; limits per key | Use |
| BSC chain data | Envio HyperSync | Free plan with token | Use |
| BSC chain data, paid fallback | Etherscan V2 (Lite plan) | $49/month | Fallback only, owner decision |
| BSC contract detection | Public BSC JSON-RPC `eth_getCode` | Free | Verify endpoint and limits (VS-12) |
| Attribution | Commercial vendor (Chainalysis, TRM, Elliptic, Crystal) | Paid | Not in v1. Adapter point only |
| Labels | Own `labels.csv`, operator labels, licensed label packs | — | Use; packs need a recorded licence |

---

## 2. OFAC SDN list

| Fact | Detail |
|---|---|
| URL | `https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.XML` → 302 to a short-lived signed S3 URL, no key |
| Size | ~29 MB (the "advanced" XML is ~127 MB and slow; not needed) |
| Publish date | In the file: `<publshInformation><Publish_Date>MM/DD/YYYY</Publish_Date>` (OFAC's spelling) |
| Addresses | `<sdnEntry>` → `<id><idType>Digital Currency Address - XXX</idType><idNumber>…</idNumber></id>`, with `<uid>` and `<programList>` |
| Size of the crypto part (2026-09-23) | 1,059 addresses on 99 entries under 20 currency labels; 334 TRON, 133 `0x` |
| **Labels are unreliable** | TRON addresses filed under XBT; USDT-labelled Bitcoin (Omni) addresses; the only "BNB" entry is a retired Beacon Chain `bnb1…` address. **Match on the address string only** |
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
| History | Events from 2020-06-26 onward; ~10,800 events in all (2026-09-28), full fetch ~24 s |
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
| Other stablecoins on BSC | Some *can* freeze (seen via Eagle Virtual: AUSD, XUSD, USD0). Out of scope as tokens, but freezes of the same `0x` address count under R-FRZ-01 |

---

## 5. Eagle Virtual (issuer freeze history)

| Fact | Detail |
|---|---|
| Spec | Public OpenAPI at `https://eaglevirtual.com/v1/openapi.json` |
| Auth | `Authorization: Bearer ev_live_…` |
| Check | `GET /v1/check/{address}` → `verdict` ∈ `CLEAR`, `FROZEN`, `SEIZED`, `UNFROZEN`, or `null` with `verdict_reason: "coverage_unvouched"` and `coverage.not_vouched_for[]` |
| Details | `GET /v1/address/{address}` → `restriction_records[]` with chain, token, company, event kind, block, tx hash. One more call: read only when the verdict is not `CLEAR` |
| Usage | `GET /v1/usage` (free): plan, calls today, daily limit, credit line requirement |
| Coverage | ~41 chains incl. BNB Chain (56) and TRON. A `0x` address is answered for **every** EVM chain at once |
| Free plan | 1 key, 1,000 checks/day (per account), 1 request/s |
| Business plan | 5 keys, 25,000 checks/day each, 10 requests/s, no credit line |
| Errors | 400 (not an address, free), 401, 403, 429 (`Retry-After`), 503 ("we do not answer from stale data") |
| **Licence** | Free plan must show the credit line from the `x-ev-credit-line` header. **Resale, bundling, building a dataset or training a model from it needs a written agreement.** Don't store answers beyond a short cache, don't screen counterparties with it on the Free plan |
| Freshness | At most ~1 day behind chain (observed) |

---

## 6. TronGrid (TRON chain data)

| Fact | Detail |
|---|---|
| Auth | Header `TRON-PRO-API-KEY`; works without a key but IP-limited |
| Limits | **Not published.** Set per key in the console. Rate-limited answers are **429 or 403** — treat both the same; read limits from config |
| Transfers | `GET /v1/accounts/{address}/transactions/trc20?contract_address=…&only_confirmed=true&min_timestamp=…&order_by=block_timestamp,desc&limit=200`, paged via `meta.links.next`. Rows: `transaction_id`, `block_timestamp` (ms), `from`, `to`, `value`, `type`, `token_info`. **No block number, no event index** (VS-04) |
| Account | `POST /wallet/getaccount` → `create_time` (ms); `{}` when never activated. USDT can arrive at a never-activated address |
| Contract calls | `POST /wallet/triggerconstantcontract`; `POST /wallet/getcontract` for contract detection |
| Speed | Newest 5,000 transfers of a busy hot wallet: ~25 pages, ~20 s |

---

## 7. Envio HyperSync (BSC chain data)

| Fact | Detail |
|---|---|
| Endpoint | `POST https://bsc.hypersync.xyz/query` (also `https://56.hypersync.xyz` — use as DNS fallback) |
| Auth | `Authorization: Bearer <token>`, free token at `https://envio.dev/app/api-tokens`. No token → 401 |
| Height | `GET /height` → `{"height": N}` |
| Answer | `data[]` batches of `blocks`, `logs`, `transactions`; `next_block`, `archive_height`. **Block timestamps are hex strings**; topics in `topic1`/`topic2` |
| Order | **Oldest first only.** For newest-first reading, query block windows from the head down |
| Paging | Stops near ~1,000 logs or a few seconds of work; continue from `next_block`. No progress / no `next_block` → error |
| **Budget (free plan, 2026-09-30)** | ~30 queries per minute: `x-ratelimit-limit: 30000;w=60`, `x-ratelimit-cost: 1000` per query, `x-ratelimit-remaining`, `x-ratelimit-reset`. Refusal = 429 with empty body and **no** `Retry-After`. The cost header read `0` the day before — **pace from the headers, don't hard-code** |
| Completeness | 20 of 20 random real transfers found; rows matched raw logs field for field (hash, log index, block, time, from, to, amount) |
| Speed | Quiet wallet's 180 days: 2 requests, 2.5 s. Busy wallet's newest 5,000: ~13.5 s. First-activity scan from block 0: 0.3–21 s |
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
| VS-08 | Eagle Virtual spec version, plans, credit line, `/v1/usage`, chain list incl. BSC and TRON | P2 |
| VS-09 | Eagle Virtual licence terms (internal use, storage, bundling) | P2 |
| VS-10 | First-activity definitions: TRON `getaccount.create_time` vs first transfer; BSC first tx/log scan | P3 |
| VS-11 | UK OFSI list: crypto addresses present? format? licence? | P2 (optional) |
| VS-12 | A free BSC JSON-RPC endpoint for `eth_getCode` without a key, and its limits | P5 |
| VS-13 | TRON contract detection via `getcontract` (answer for a wallet vs a contract) | P5 |
| VS-14 | Licence of any label pack before import | P4 |
