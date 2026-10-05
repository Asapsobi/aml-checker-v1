# amlcheck — Verification log

> Live checks of external facts before code relies on them (data sources §9). Newest phase first;
> one entry per VS item. Keys were never printed or saved; fixtures are real answers, trimmed.

**P0 status (2026-10-01): complete.** VS-01 to VS-06 and VS-10 confirmed, VS-05, VS-06 and the BSC
half of VS-10 with the owner's TronGrid key and HyperSync token (loaded through `amlcheck.config`, never
printed). **VS-08 and VS-09 dropped** with Eagle Virtual (D-033); their entries stay below as a record.
VS-07 (P6) and VS-11 to VS-14 (later phases) are not P0 items. VS-15 was redone in P2 for TRC20 and BEP20 only (D-039); see the end.

| VS | Result | Differs from docs | Question |
|---|---|---|---|
| VS-01 | Confirmed | Counts grew; new `BSC` currency label | — |
| VS-02 | Confirmed | No | — |
| VS-03 | Confirmed | No | — |
| VS-04 | Confirmed, plus a uniqueness problem | Yes: transfer identity depends on whose history is read | Q-17 |
| VS-05 | Confirmed with key | Key limit 15/s; over it a 30 s suspension, no `Retry-After` | Q-18, Q-20 |
| VS-06 | Confirmed with token | Small: header format, `rollback_guard`, budget shared across hosts | — |
| VS-07 | Measured (3 + 3 traces, cold and warm): within G7 and G8 | No; two of our bugs found and fixed | Q-09 |
| VS-08 | Dropped (D-033) | Was: `CLEAR` can come with chains behind (spec 1.3.0) | Q-19 (superseded) |
| VS-09 | Dropped (D-033) | — | — |
| VS-10 | Confirmed (both chains) | `create_time` absent on contract-created contracts | — |
| VS-16 | Measured (7 TRON, 4 BSC addresses) | TRON cheap; a full BSC read is slow, an exchange-sized one never finishes | — |
| VS-17 | Measured: batching works, helps quiet addresses only | Pages hold ~1,000 logs | — |
| VS-18 | Confirmed: 49 TRON addresses in free text | No structured field | — |
| VS-19 | Confirmed: 3 TRON, 2 EVM addresses in free text | Public token URL, no login | — |
| VS-20 | Pending: the owner's NBCTF files | — | — |
| VS-21 | Confirmed with the owner's key: works, but no risk tags on any sanctioned or frozen address tried | Tags name only famous entities | — |

---

## VS-17 · HyperSync multi-address queries (P13)
**Checked:** 2026-10-05, with amlcheck's HyperSync client, one window (2026-09-01 to 2026-10-03,
6.5M blocks), each address alone then all six as one OR of topic values.

| Six addresses | One per query | Batched | Same transfers |
|---|---|---|---|
| Busy (589 to 16,562 transfers each) | 34 queries, 93 s | 30 queries, 150 s | yes |
| Quiet (5 to 847 each) | 6 queries, 13.2 s | **1 query, 5.8 s** | yes |

**Found:** HyperSync accepts many addresses in one query and returns the same transfers. A page holds
about 1,000 logs, so cost follows transfer volume: batching helps quiet addresses, not busy ones.
**What this changes:** not built (D-083); recorded for when BSC work resumes.

## VS-18 · UK Sanctions List (P14)
**Checked:** 2026-10-05. `https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.xml`, 200, 21.9 MB,
generated 02/10/2026; also a CSV. The single UK list since 2026-01-28 (OFSI's consolidated list
closed). Licence: Open Government Licence v3.0 (attribution).
**Found:** `<Designation>` with `UniqueID` (e.g. `GHR0190`), `Names/Name/Name6` and `NameType`,
`RegimeName`, `OtherInformation`, `UKStatementofReasons`. Crypto addresses only in free text
("Xinbi is associated with the following crypto addresses: T…; T…"). **49 TRON addresses, all
checksum-valid, 5 not on OFAC; 6 EVM.** Fixture: `tests/fixtures/lists/uk_sample.xml` (two real
designations, trimmed).

## VS-19 · EU Financial Sanctions Files (P14)
**Checked:** 2026-10-05. `https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw`,
200, 25.8 MB, `generationDate` 2026-09-22, no login. Licence: Commission Decision 2011/833/EU
(reuse for any purpose, free).
**Found:** `<sanctionEntity euReferenceNumber=… logicalId=…>` with `nameAlias wholeName=…`,
`regulation programme=…`, `address`/`identification` children whose `<remark>` text holds wallets
("Known blockchain wallet addresses: T…;"). **3 TRON (1 not on OFAC: Grinex), 2 EVM.** Fixture:
`tests/fixtures/lists/eu_sample.xml` (two real entities, trimmed).

## VS-20 · NBCTF seizure-order annexes (P14)
**Pending** the owner's downloaded files: the official site is bot-protected (connection refused from
here; not worked round). The importer reads every cell, so it needs no column names.

## VS-21 · Tronscan account tags (P14)
**Checked:** 2026-10-05 with the owner's key (header `TRON-PRO-API-KEY`, never printed). Terms (PDF,
2022-01-04) forbid scraping and "automated means or interface not provided by us"; the API is theirs.
Docs (docs.tronscan.org, Deep Analysis → Get Account Tags): `GET /api/account/tag?address=` →
`redTag` ("risk identifier"), `publicTag`, `blueTag`, `greyTag`, `chainTags` (behaviour: Assets,
Activity, DeFi, NFT, Governance), `refreshTimeInfo`. `/api/accountv2` carries the same four tags
among balances (3–38 KB). No rate-limit headers.
**Found** (14 addresses): Tether Treasury `TKHuVq…` → `publicTag` "Tether Treasury". **No `redTag`
on any risky address tried**: the OFAC/CHEIL CREDIT BANK address, a Xinbi wallet (OFAC and UK), five
Tether-frozen addresses, six busy unnamed services. `chainTags` describe size and activity ("High
Balance", "Whale", "Large Trader"), not risk. Fixtures: `tests/fixtures/tronscan/` (two answers).
**What this changes:** D-087 is not built: one call per address reached (100+ per check) for almost
no risk signal. Revisit if a sample of the owner's real counterparties shows useful public tags.

## VS-16 · Cost of a full-history read (P13)
**Checked:** 2026-10-04, with amlcheck's own readers (`TransferCache` over TronGrid and HyperSync),
a fresh data folder per address, one at a time; requests counted by the HTTP layer. Read from before
USDT existed (TRON 2018-01-01, BSC 2020-08-01), newest first, up to 20,000 transfers.

| Chain | Address | Kind | Transfers read | All of it | Requests | Time |
|---|---|---|---|---|---|---|
| TRON | `TA3941uF…X86mz` | OFAC-listed, quiet | 28 (2024-11 to 2025-02) | yes | 3 TronGrid | 1.6 s |
| TRON | `TVvWhZyL…LeSsWP` | ordinary, new | 199 | yes | 3 | 1.3 s |
| TRON | `TSArbmMU…VF2Rku` | ordinary | 394 | yes | 4 | 1.9 s |
| TRON | `TPwvbEKT…AMAzdBVe` | busy | 3,537 | yes | 20 | 8.8 s |
| TRON | `TEojgka7…ML6cAT8` | busy | 4,848 | yes | 27 | 17.8 s |
| TRON | `TE2LiJfp…xfkuL1c` | busy | 8,065 | yes | 43 | 28.5 s |
| TRON | `TDii6vao…xcqYx` | exchange-sized | 20,000 = **11 hours** | no | 103 | 59.5 s |
| BSC | `0x0c1e52…ee1576` | new | 58 | yes | 5 HyperSync | 35.8 s |
| BSC | `0x033007…e9e54a` | OFAC-listed, old | 19 (2024-09 to 2025-06) | yes | 5 | 38.8 s |
| BSC | `0x75f5c1…21c1f6ba` | ordinary | 443 | yes | 4 | 22.8 s |
| BSC | `0x8bc070…be2d4b689` | exchange-sized | — | **stopped after 20 min** | — | > 20 min |

**Found:**
- TRON: about one request per 200 transfers, ~0.15 s each at the 10 req/s limiter. Cheap.
- BSC: few HyperSync queries (4–5) for an ordinary wallet, but each scans years of blocks: 20–40 s.
  An exchange-sized wallet doesn't finish in any time a check can wait (30 queries a minute, free plan).
- The OFAC-listed TRON address has 28 transfers, all older than 180 days: v1 showed it "0 transfers".

**What this changes:** the history is read in two steps (methodology §12.1, `screening/history.py`):
the required 180 days as before, then older history only within `[exposure] history_extension_seconds`
(45 s), bounded by the source's own timeout. A finished older read is cached; an unfinished one
says where history starts. The exposure source's timeout is 150 s.

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

### VS-01 addendum · the zipped list (2026-10-03)
**Checked:** `…/exports/SDN_XML.ZIP` and `…/exports/SDN.XML`, publication of 2026-10-02.
**Found:** both redirect into the same S3 publication folder. The ZIP is 2,581,725 bytes and holds one
member, `SDN.XML`, of 29,240,384 bytes: exactly the size of `SDN.XML`, and byte-identical to it at the
start, middle and end (three 64 KB `Range` slices). S3 answers `Range` with `206` and `Content-Range`.
The ETags are not MD5s of the content (the ZIP's own MD5 differs from its ETag), so they can't be used
to compare files. Live trigger: `amlcheck sync` of the 29 MB XML failed after 35 min on a 12 KB/s line
("timed out after 3 tries"), each retry starting from zero.
**Differs from docs/04-data-sources.md:** adds the ZIP and the Range support.
**What this changes:** D-040: download the ZIP by default and resume after stalls.
**Fixtures:** none (the tests zip `sdn_sample.xml`).

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

---

## VS-07 · Trace budget, cold and warm (PRD G7, G8)
**Checked:** 2026-10-03, owner's TronGrid key and HyperSync token, trace engine v1 with default
`[trace]` settings. Targets: 3 per chain that had just received a 1k–50k USDT transfer, not contracts,
20–1,500 transfers in 180 days, ≥ 10k USDT received from ≥ 3 senders. Each cold trace ran on its own
copy of the database with every cached transfer, window, contract answer, classification and inferred
entity removed; the warm trace repeated it right after. Requests counted per provider.

| Target | Cold: requests | Cold: time | Read | Coverage | Warm: requests | Warm: time | Saving |
|---|---|---|---|---|---|---|---|
| TRON `TRwJi21T…i6vSwo` (711 transfers) | 43 TronGrid | 28 s | 12 | 34.8% | 1 | 0.6 s | 98% |
| TRON `TS5t3Vh8…iGB3hE` (110) | 105 TronGrid | 71 s | 24 | 6.6% | 1 | 0.8 s | 99% |
| TRON `TVvWhZyL…LeSsWP` (194) | 86 TronGrid | 46 s | 22 | 29.4% | 1 | 0.8 s | 99% |
| BSC `0xc613cf…a1eeda` (1,112) | 9 HyperSync | 74 s | 2 | 99.4% | 2 | 3.1 s | 78% |
| BSC `0x0c1e52…ee1576` (30) | 31 HyperSync, 14 RPC | 63 s | 15 | 15.5% | 2 | 1.3 s | 96% |
| BSC `0x090354…26600c` (1,099) | 35 HyperSync | 147 s | 6 | 43.8% | 2 | 2.0 s | 94% |

**Found:**
- **G8 met with a wide margin:** at most 35 of 120 HyperSync queries and 105 of 200 TronGrid requests.
  **G7 met:** a repeat used 78–99% fewer. **Performance met:** cold BSC ≤ 147 s (≤ 5 min), warm ≤ 3.1 s
  (≤ 90 s). All six complete; partitions identical cold and warm.
- At ~0.45 s per BSC block a 30-day hop window is ~5.76 million blocks. A quiet address costs one
  page (0.5–2 s). HyperSync sometimes takes 20–57 s for one page (2 of 54 pages), so BSC time is
  dominated by a few slow answers, not by the count.
- **Bug 1 (fixed, `4ee0a2c`):** the newest-first BSC read ignored that HyperSync's first page holds every
  log up to its `next_block`, and scanned backward to the window start. A new, busy wallet (all logs
  near the end, many 0-value spam that don't count) sent it through millions of empty blocks: ~160
  queries and 632 s for one trace (181 queries, failed on time). After the fix the same trace: 31
  queries, 63 s, complete.
- **Bug 2 (fixed, `1a6bad4`):** the 300 s time budget was checked only between addresses, so one slow
  read ran past it. Every read now runs under the time left; the trace then ends as a partial (seen
  live: stopped at 300.0 s).
- Real finding: TRON target 3 traces to an OFAC-listed wallet 2 hops away (every hop on the path moved
  ≥ 34,733 USDT; estimated share 0.7%) → R-TRC-01 REVIEW.
- Coverage is often low (6.6–44%) because pruning keeps 5 senders per node and `untraced:depth` takes the
  rest: wallets with many mid-sized senders lose most value to `untraced:pruned`. R-TRC-04 flags these
  for review, as designed. A calibration input for P11 (`branch`, `coverage_share`).
**Differs from docs/04-data-sources.md:** no (BSC pace ~0.45 s per block, as the doc says).
**What this changes:** Q-09: no paid HyperSync tier needed (proposed). No config change.
**Scripts:** run from a scratch folder, keys through `amlcheck.config` only, never printed.

---

## VS-15 · Issuer freezes on TRC20 and BEP20 (narrowed to v1's two networks, D-039)
**Checked:** 2026-10-03. TRC20: amlcheck's own `TronFreezeIndex.refresh()` (TronGrid events, owner's
key, scratch DB). BEP20: HyperSync logs of `0x55d398326f99059ff775485246999027b3197955`, full history,
topic0 = every freeze/blacklist/pause event name in use by Tether (both families) and Circle.
**Found:**

| Network | Contract | Result |
|---|---|---|
| TRC20 | `TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t` (Tether USDT) | **10,923 events**: `AddedBlackList` 8,722, `RemovedBlackList` 971, `DestroyedBlackFunds` 1,230, from 2020-06-26 to 2026-10-02. **7,727 addresses frozen now** (latest event is an add). First full sync **64 s** (≈55 pages at ≤ 10 req/s), incremental re-sync 2.6 s. Tether's newer event names (`BlockPlaced`, `BlockReleased`, `DestroyedBlockedFunds`): **0** |
| BEP20 | `0x55d398…3197955` (Binance-Peg USDT) | **No freeze, blacklist or pause event ever**, blocks 0 to 125,443,300 (7 queries, 68 s) |

**Differs from docs/04-data-sources.md:** TRON event count (~10,800 → 10,923) and first-sync time on this
connection (~24 s → 64 s). BEP20 matches VS-03 (no freeze function), now confirmed from events too.
**What this changes:** nothing in code: the TRON index reads exactly these three events; BSC freeze
stays `skipped` (D-009). Data sources §3 updated.
**Fixtures:** none new (the index tests use the P0 event fixtures).

### Earlier, before the scope was narrowed: other EVM chains (not v1)

**Checked:** 2026-10-01, contract identity by `eth_call` on public RPCs (publicnode, drpc for Polygon);
events via HyperSync (`<chain>.hypersync.xyz`, owner's token) with topic0 for
`AddedBlackList`/`RemovedBlackList`/`DestroyedBlackFunds`, `BlockPlaced`/`BlockReleased`/`DestroyedBlockedFunds`,
`Blacklisted`/`UnBlacklisted`, full history.
**Status:** stopped by the owner as too wide for v1 (D-039). Kept for a later version; not used by code.
**Found (complete history unless marked):**

| Chain | Contract | Token | Freeze events |
|---|---|---|---|
| Ethereum | `0xdAC17F95…831ec7` | USDT (Tether, not a proxy) | not counted: the address is **not indexed** (no `topic1`; it is in `data`) and the large answer broke off mid-read |
| Ethereum | `0xA0b86991…06eB48` | USDC (proxy) | `Blacklisted` 900, `UnBlacklisted` 226 (5 queries, 50 s) |
| Arbitrum | `0xFd086bC7…FcbB9` | USD₮0 (proxy) | `BlockPlaced` 37, `DestroyedBlockedFunds` 5 |
| Arbitrum | `0xaf88d065…5831` | USDC (proxy) | `Blacklisted` 585, `UnBlacklisted` 93 |
| Base | `0x833589fC…02913` | USDC (proxy) | `Blacklisted` 596, `UnBlacklisted` 93 |
| Optimism | `0x94b008aA…e58e58` | USDT (not a proxy) | **none**: a bridged copy that can't freeze |
| Optimism | `0x0b2C639c…6Ff85` | USDC (proxy) | `Blacklisted` 572, `UnBlacklisted` 93 |
| Polygon | `0xc2132D05…58e8F` | USDT0 | `BlockPlaced` 5, `BlockReleased` 1, `DestroyedBlockedFunds` 2 |
| Polygon | `0x3c499c54…c3359` | USDC | not counted (answer broke off) |
| Avalanche | `0x9702230A…4A8c7` | USDt "TetherToken" (proxy) | `BlockPlaced` 16, `BlockReleased` 2, `DestroyedBlockedFunds` 9 |
| Avalanche | `0xB97EF9Ef…48a6E` | USDC (proxy) | `Blacklisted` 578, `UnBlacklisted` 96 |

- **Tether's newer deployments (USDT0, Avalanche) use `BlockPlaced`/`BlockReleased`/`DestroyedBlockedFunds`,
  not `AddedBlackList`.** A later index must read both families.
- Every chain answered with the BSC token. In this sandbox the system resolver could not resolve
  `eth.hypersync.xyz`, `1.hypersync.xyz` or `42161.hypersync.xyz` (public DNS could); named hosts
  (`arbitrum.`, `base.`, …) worked. An environment quirk here, not a provider fact.
**Differs from docs/04-data-sources.md:** the doc listed only `AddedBlackList`-style events for Tether.
**What this changes:** nothing in v1 (D-039). Input for a later version.
**Fixtures:** none saved (not used by code).

---

## VS-14 · Label pack licence (template; fill in before every `intel import-pack`)
**Checked:** YYYY-MM-DD, against <the provider's licence / terms URL or contract reference>
**Pack:** name (as passed to `--name`), provider, version/date, row count
**Found:** may it be used internally for screening? stored locally, for how long? may it be combined
with our own labels? attribution required? any ban on resale or redistribution (we never resell)?
**Licence string recorded:** the exact `--licence` text (it is stored on every imported label)
**What this changes:** categories the pack adds, and whether `[heuristics] risky_tags` should include any
**Decision:** D-NNN if the pack changes scope or cost

---

## VS-12 · BSC contract detection without a key
**Checked:** 2026-10-03, against `https://bsc-dataseed.bnbchain.org` (`eth_getCode`, no key)
**Found:** BEP20 USDT → 4,413 bytes of code; Binance hot wallet `0x8894…d4e3` and a never-used address
→ `"0x"` (no code). 30 calls back to back (≈1/s on this connection, latency-bound): all answered, no
rate-limit headers, no refusals. The endpoint's limits are not published in its answers.
**Differs from docs/04-data-sources.md:** none (the doc asked to verify the endpoint and limits).
**What this changes:** `[bsc] rpc_url` = this endpoint; contract lookups paced at
`[bsc] rpc_requests_per_second` = 2 and cached forever (PRD F2.6), so each address costs one call ever.
**Fixtures:** `tests/fixtures/bsc_rpc/eth_getcode_usdt.json` (P0), `eth_getcode_wallet.json`,
`eth_getcode_never_used.json`

## VS-13 · TRON contract detection via getcontract
**Checked:** 2026-10-03, against TronGrid `POST /wallet/getcontract` (owner's key)
**Found:** a contract (USDT) → object with `contract_address`, `origin_address`, `bytecode`, `abi`, `name`,
`code_hash`…; a **contract created by a contract** (`TZ53…p11`) → `contract_address`, `code_hash`,
`trx_hash`, `abi` but **no `bytecode`**; a wallet and a never-used address → `{}`.
**Differs from docs/04-data-sources.md:** none, but `bytecode` can't be the test.
**What this changes:** TRON `is_contract` = the answer has `contract_address`.
**Fixtures:** `tests/fixtures/trongrid/getcontract_contract.json`, `getcontract_created_by_contract.json`,
`getcontract_wallet.json`, `getcontract_never_used.json` (bytecode and ABI trimmed)
