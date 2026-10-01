# amlcheck — Verification log

> Live checks of external facts before code relies on them (data sources §9). Newest phase first;
> one entry per VS item. Keys were never printed or saved; fixtures are real answers, trimmed.

**P0 status (2026-10-01): complete.** VS-01 to VS-06 and VS-10 confirmed, VS-05, VS-06 and the BSC
half of VS-10 with the owner's TronGrid key and HyperSync token (loaded through `amlcheck.config`, never
printed). **VS-08 and VS-09 dropped** with Eagle Virtual (D-033); their entries stay below as a record.
VS-07 (P6), VS-11 to VS-14 (later phases) and VS-15 (EVM freeze index, P2, D-034) are not P0 items.

| VS | Result | Differs from docs | Question |
|---|---|---|---|
| VS-01 | Confirmed | Counts grew; new `BSC` currency label | — |
| VS-02 | Confirmed | No | — |
| VS-03 | Confirmed | No | — |
| VS-04 | Confirmed, plus a uniqueness problem | Yes: transfer identity depends on whose history is read | Q-17 |
| VS-05 | Confirmed with key | Key limit 15/s; over it a 30 s suspension, no `Retry-After` | Q-18, Q-20 |
| VS-06 | Confirmed with token | Small: header format, `rollback_guard`, budget shared across hosts | — |
| VS-08 | Dropped (D-033) | Was: `CLEAR` can come with chains behind (spec 1.3.0) | Q-19 (superseded) |
| VS-09 | Dropped (D-033) | — | — |
| VS-10 | Confirmed (both chains) | `create_time` absent on contract-created contracts | — |

---

## VS-01 · OFAC: SDN.XML URL, redirect, structure, digital-currency idTypes
**Checked:** 2026-10-01, against `https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.XML`
**Found:**
- `GET` → `302` to a signed, short-lived `s3.us-gov-west-1.amazonaws.com/…/2026-09-29/…/SDN.XML?…`
  URL, no key. File 29,187,997 bytes (6 min 17 s on this connection).
- Root `<sdnList xmlns="https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/XML">`;
  `<publshInformation><Publish_Date>09/30/2026</Publish_Date><Record_Count>19452</Record_Count>`.
- Addresses in `<sdnEntry>/<idList>/<id>` with `<idType>Digital Currency Address - XXX</idType>` and
  `<idNumber>`; entry `<uid>`, `<lastName>`, `<programList><program>`.
- **1,066 addresses on 106 entries under 20 labels** (ARB, BCH, BNB, BSC, BSV, BTG, DASH, DOGE, ETC, ETH,
  LTC, SOL, TRX, USDC, USDT, XBT, XMR, XRP, XVG, ZEC). TRON-format: 341 (TRX 261, USDT 79, XBT 1).
  `0x`: 133 (ETH 120, USDT 8, USDC 2, ETC 1, ARB 1, BSC 1). The only BNB entry is still `bnb1…`.
- All 341 TRON addresses pass base58check; all 69 mixed-case `0x` addresses pass EIP-55.
**Differs from docs/04-data-sources.md:** counts (was 1,059 / 99 / 334 TRON on 2026-09-23); a new
`Digital Currency Address - BSC` label exists. Matching on the address string (D-006) already covers it.
**What this changes:** data sources §2 updated. Parser must not filter by currency label.
**Fixtures:** `tests/fixtures/ofac/sdn_sample.xml` (6 real entries: TRON under USDT, TRON under XBT,
mixed-case ETH, the BSC label, the BNB label, one non-crypto entry; `Record_Count` set to 6)

## VS-02 · Tether TRON contract: events, isBlackListed, deprecated()
**Checked:** 2026-10-01, against `https://api.trongrid.io` (no key)
**Found:**
- `GET /v1/contracts/TR7N…jLj6t/events?event_name=AddedBlackList|RemovedBlackList|DestroyedBlackFunds&only_confirmed=true`:
  rows have `block_number`, `block_timestamp` (ms), `event_index`, `event_name`, `event`,
  `transaction_id`, `result` (`_user`, or `_blackListedUser` + `_balance` as a decimal string),
  `result_type`. Addresses are `0x` + 40 hex **without** the `41` prefix.
- Paging: `meta.links.next` keeps `event_name`, `fingerprint`, `limit`, `min_block_timestamp`,
  `only_confirmed`, `order_by`. `order_by=block_timestamp,asc` works; the first `AddedBlackList` is
  2020-06-26.
- `POST /wallet/triggerconstantcontract`: `deprecated()` → 0 (false), `paused()` → 0, `decimals()` → 6.
  `isBlackListed` → 1 for a recently added user, 1 for the USDT contract itself, 0 for an ordinary
  address. `POST /walletsolidity/getnowblock` answers with `block_header.raw_data.number`/`timestamp`.
**Differs from docs/04-data-sources.md:** none.
**What this changes:** nothing. Without a key the event endpoint allows about 1 request/s (see VS-05).
**Fixtures:** `tests/fixtures/trongrid/events_*.json`, `constant_*.json`, `walletsolidity_getnowblock.json`
(transactions removed)

## VS-03 · BEP20 USDT: still no freeze function
**Checked:** 2026-10-01, against `https://bsc-dataseed.bnbchain.org` (`eth_getCode`, `eth_getStorageAt`), block 125,088,958
**Found:** 4,413 bytes of code. The dispatcher's 19 selectors, each matched by Keccak: `name`, `symbol`,
`decimals`, `_name`, `_symbol`, `_decimals`, `totalSupply`, `balanceOf`, `transfer`, `transferFrom`,
`approve`, `allowance`, `increaseAllowance`, `decreaseAllowance`, `mint`, `burn`, `owner`, `getOwner`,
`renounceOwnership`, `transferOwnership`. None of `pause`, `paused`, `addBlackList`, `isBlackListed`,
`destroyBlackFunds`, `freeze`, `blacklist`, `freezeAccount` and similar. EIP-1967 implementation and
admin slots and the legacy OpenZeppelin slot are zero (not a proxy).
**Differs from docs/04-data-sources.md:** none.
**What this changes:** confirms D-009. (Side note for VS-12: `bsc-dataseed.bnbchain.org` answered
`eth_getCode` without a key.)
**Fixtures:** `tests/fixtures/bsc_rpc/eth_getcode_usdt.json`

## VS-04 · TronGrid TRC20 transfers: filters, ordering, per-transfer index
**Checked:** 2026-10-01, against `GET https://api.trongrid.io/v1/accounts/{address}/transactions/trc20` (no key)
**Found:**
- Row fields: `transaction_id`, `block_timestamp` (ms), `from`, `to`, `value` (string, 6 decimals),
  `type` (`Transfer`), `token_info` (`symbol`, `address`, `decimals`, `name`). **No block number and no
  event index.**
- `order_by=block_timestamp,desc` is newest first; `asc` works too. `min_timestamp` and
  `max_timestamp` filter **inclusively at both ends**. Paging via `meta.links.next`.
- Empty answer: `{"data": [], "success": true, "meta": {"at": …, "page_size": 0}}`.
- **One transaction can hold several USDT transfers.** A GasFree transaction (`7372016f…` in the fixture)
  emits `Transfer` at `event_index` 0 (fee, 1.5 USDT) and 1 (payment, 10,933 USDT), plus a
  `GasFreeTransfer` event. The sender's history shows **2 rows** for it, in the order index 1, then 0;
  the recipient's history shows **1 row**.
- `GET /v1/transactions/{tx}/events` returns every event of a transaction with its `event_index`.
**Differs from docs/04-data-sources.md:** the doc says to use "a stable ordinal within the tx" when there is
no index. An ordinal **can't be stable**: it depends on whose history was read (the payment is ordinal 0
for the recipient and 0 or 1 for the sender), so the shared `transfers` table would store one transfer
twice or merge two.
**What this changes:** the `idx` rule for TRON in data model `0001_cache.sql` (P1). Raised as **Q-17**.
**Fixtures:** `tests/fixtures/trongrid/trc20_transfers_desc.json`, `trc20_transfers_empty.json`,
`trc20_two_in_one_tx_sender_view.json`, `trc20_two_in_one_tx_recipient_view.json`,
`events_transfer_two_in_one_tx.json`, `tx_events_gasfree.json`

## VS-05 · TronGrid limits and 403/429 behaviour
**Checked:** 2026-10-01, against `https://api.trongrid.io` **without a key** (no key in the environment)
**Found:** a burst of 4 calls in about 2 s → `429`, `Content-Type: application/json`, **no
`Retry-After` and no rate-limit headers**. Body:
`{"Error":"request rate of (getTrc20TransactionsByAccount) exceeded the allowed_rps(1), and the query server is suspended for 5 s. …"}`.
**Differs from docs/04-data-sources.md:** the Retry-After policy (D-011) assumes the header exists; here it
doesn't. Unknown yet whether keyed answers carry it.
**What this changes:** `net/http.py` needs a rule for a 429/403 without `Retry-After`. Raised as **Q-18**.
**Fixtures:** `tests/fixtures/trongrid/rate_limited_429_no_key.json`

**With the owner's key (2026-10-01, `TRON-PRO-API-KEY` header):**
- A normal answer carries no rate-limit headers (only `X-Trace-Id`), so the limit can't be read live.
- 20 sequential calls: all OK (about 0.7 s each on this connection). 40 calls 20 at a time (~21/s
  attempted): 27 OK, 13 refused.
- Refusal: `429`, `Content-Type: application/json`, **no `Retry-After`, no rate-limit headers**, body
  `{"Error":"The key exceeds the frequency limit(15), and the query server is suspended for 30 s"}`.
  No 403 seen.
- **The key's limit is 15 requests/s, and going over suspends the key for 30 s**, not 5 s as without a key.
**Differs from docs/04-data-sources.md:** limits were unknown; now 15/s and a 30 s suspension. D-031 ("wait
5 s once") would wait too little and then fail.
**What this changes:** `[tron] requests_per_second` stays 10 (below 15); the refusal rule needs a
revisit. Raised as **Q-20**. Data sources §6 updated. Calls used: 61.
**Fixtures:** `tests/fixtures/trongrid/rate_limited_with_key.json`

## VS-06 · HyperSync query shape, log index, budget headers
**Checked:** 2026-10-01, against `https://bsc.hypersync.xyz` and `https://56.hypersync.xyz` (no token)
**Found:** `GET /height` → `{"height": 125089212}` on both hosts without a token. `POST /query` without a
token → `401` `{"error":"Your token is malformed. API Tokens can be created at https://app.envio.dev/api-tokens. …"}`.
**With the owner's token (2026-10-01, `Authorization: Bearer`):**
- Query shape that works: `{"from_block", "to_block", "logs": [{"address": [USDT], "topics": [[Transfer],
  [addr32], []]}, {"address": [USDT], "topics": [[Transfer], [], [addr32]]}], "field_selection": {"block":
  ["number", "timestamp"], "log": ["block_number", "log_index", "transaction_hash", "address", "topic0",
  "topic1", "topic2", "data"]}}`. Two log selections are OR-ed: sent and received in one query.
- Answer: `archive_height`, `data[]` (batches of `blocks`, `logs`), `next_block`, `rollback_guard`,
  `total_execution_time`. Logs carry `log_index`, `transaction_hash`, `block_number`, `topic1`/`topic2`
  (32-byte padded), `data` (hex amount, 18 decimals). Block `timestamp` is hex (`"0x6abe3f7b"`).
- Paging: a busy hot wallet over 2,000 blocks returned 1,033 logs in 7 batches and stopped with
  `next_block` short of `to_block` (continue from it).
- Budget: `x-ratelimit-cost: 1000`, `x-ratelimit-limit: 30000, 30000;w=60`, `x-ratelimit-remaining`,
  `x-ratelimit-reset` (seconds). **The budget is per token across `bsc.` and `56.` hosts.** The 30th
  query in a window → `429`, empty body, no `Retry-After`, `remaining: 0`, `reset: 43`.
- Speed: 180 days of a wallet with 221 transfers, 1 query, 7.8 s. BSC pace 0.450 s/block over 1M blocks.
- `https://56.hypersync.xyz` accepts the same token.
**Differs from docs/04-data-sources.md:** small: limit header now `30000, 30000;w=60`; new `rollback_guard`
field; budget shared between hosts (the fallback host is not extra budget).
**What this changes:** the pacer (T-1.02) reads `remaining`/`reset` and treats both hosts as one budget.
Data sources §7 updated. Queries used: 38.
**Fixtures:** `tests/fixtures/hypersync/query_401_no_token.json`, `query_usdt_transfers_by_address.json`,
`rate_limited_429.json`

## VS-08 · Eagle Virtual: spec version, plans, credit line, usage, chains
**Checked:** 2026-10-01, against `https://eaglevirtual.com/v1/openapi.json`, `/pricing`
**Found:**
- OpenAPI 3.1.0, **API version 1.3.0**. Bearer auth (`ev_…` keys). Without a key `/v1/check` and
  `/v1/usage` → 401.
- Paths: `/v1/check/{address}`, `/v1/address/{address}` (alias `/v1/addresses/{address}`),
  `POST /v1/addresses/batch`, `/v1/chains`, `/v1/usage`, `/v1/events/rows` (Business+), `/v1/openapi.json`.
  `/v1/check` and `/v1/address` answer 200, 400, 401, 403, 429, 503.
- `CheckResult`: `address` (lowercase for EVM), `address_display`, `address_family`
  (`evm|tron|solana|stellar`), `verdict` (`CLEAR|FROZEN|SEIZED|UNFROZEN|null`), `verdict_reason`
  (`coverage_unvouched|null`), `as_of`, `checked_at` (UTC seconds), `record_count`, `coverage`
  (`chains_claimed`, `chains_vouched`, `as_of`, `not_vouched_for[]` of `chain_id`, `name`,
  `last_verified_block`, `reason` ∈ `never scanned|scan behind`), `url`. Header `x-ev-credit-line` on 200.
- Precedence: SEIZED > FROZEN > UNFROZEN. **Since 1.3.0 (2026-09-28) the verdict is `CLEAR` for the
  chains that are current even when others are behind; `not_vouched_for` names the ones behind.**
  `null` only when none of the address's chains is current.
- `/v1/usage`: `plan` (`free|business|enterprise`), `day`, `calls_today` (nullable), `daily_limit`,
  `requests_per_second`, `credit_line_required`, `credit_line`.
- Plans: Free = 1 key, 1,000 checks/day, 1/s, must show "Data from Eagle Virtual". Business = 5 keys,
  25,000/day each, 10/s, no credit line.
**Differs from docs/04-data-sources.md:** the doc (and methodology §2.1) treat a gap only as `verdict: null`.
Under 1.3.0 a `CLEAR` can sit beside the target's own chain being behind, which our rules would read as
clean. New endpoints (`/v1/chains`, batch) aren't in the doc.
**What this changes:** freeze-vendor adapter (T-2.07) must read `not_vouched_for` on every answer.
Raised as **Q-19**. Data sources §5 updated.
**Pending:** `/v1/usage` and `/v1/chains` (chain IDs for BSC and TRON). Needs `AMLCHECK_EAGLE_VIRTUAL_KEYS`.
**Fixtures:** `tests/fixtures/eagle_virtual/openapi_v1.3.0.json`

## VS-09 · Eagle Virtual licence terms
**Checked:** 2026-10-01, against `https://eaglevirtual.com/terms` (effective 2026-09-29),
`https://eaglevirtual.com/license` (effective 2026-09-29), `/pricing`
**Found:**
- Free API use is allowed after signing in; data must be shown with "Data from Eagle Virtual".
  Paid plans may use results internally without per-result attribution.
- A written agreement is needed to resell the dataset, offer it as a feed, reconstruct a restricted
  dataset by systematic queries, redistribute in bulk, bundle into a product for third parties, or
  train models on substantial portions.
- No presenting the data as identifying a person, accusing of a crime, determining a sanction,
  **certifying an address as safe**, or recommending a compliance action.
- Terms: Florida law; searched addresses are not tied to the account.
**Differs from docs/04-data-sources.md:** the doc's "don't store answers beyond a short cache, don't screen
counterparties with it on the Free plan" are **our** rules (D-008), not literal vendor terms. They stay:
they keep us clear of "reconstructing a dataset" and inside the quota.
**What this changes:** data sources §5 licence row reworded. The NO_HITS "not a clearance" wording
already fits the "certifying as safe" rule.
**Fixtures:** none (web pages)

## VS-10 · First-activity definitions
**Checked:** 2026-10-01, against TronGrid `POST /wallet/getaccount`, `/v1/accounts/{a}/transactions/trc20?order_by=block_timestamp,asc&limit=1`
**Found (TRON):**
- Activated wallet: `create_time` (ms) present; first USDT transfer 57 s later.
- Older wallet: `create_time` 2022-04-29, first USDT transfer 2025-02-26.
- **Contract created by a contract** (`type: "Contract"`, `account_name: "CreatedByContract"`):
  **no `create_time`**, but USDT history from 2025-07-02.
- Never-activated address: `{}`.
**Differs from docs/04-data-sources.md:** `create_time` can be absent on an existing account, not only on a
never-activated one.
**What this changes:** first activity on TRON = earliest of `create_time` (when present) and the first
USDT transfer. Data sources §6 updated.
**Found (BSC, HyperSync, 2026-10-01):** one query from block 0 with `transactions: [{"from": [a]},
{"to": [a]}]` and the two Transfer-log selections (any token) returns the earliest hit first. Active
wallet: first activity block 50,980,453 (a transaction), 1 query, 10.7 s. Never-used address: whole
chain scanned, 2 queries, 11.7 s, no hit.
**Fixtures:** `tests/fixtures/trongrid/getaccount_active.json`, `getaccount_contract_no_create_time.json`,
`getaccount_never_activated.json` (permissions and resources removed)
