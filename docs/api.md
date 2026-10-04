# amlcheck — Local API

> For the corridor and scripts on the same machine. P10 (T-10.03–T-10.06); requirements PRD F14;
> decisions D-065. Internal use only (D-023).

## The v2 contract (D-073)

From amlcheck 2: the check JSON (`check --json`, `investigate --json`, `POST /v2/check`,
`GET /v2/checks/{id}`) says `"contract": 2` and, with the five endpoints below, keeps its fields and
meanings for all of v2. Fields may be **added**; none is removed, renamed or given a new meaning. A
test pins the field names (`tests/unit/test_contract.py`).

**Moving from v1:** every `/v1/…` path now answers **410 Gone** (problem+json, `use` names the
`/v2/…` path). The score changed meaning (methodology §11): `score.level` (`low` / `moderate` /
`high` / `severe`) replaces `band`, and `components` are `X` (exposure) and `B` (behaviour). Checks
stored by v1 still read back with their v1 score (`score_version` 1).

## Start it

```bash
# once: a token of at least 32 characters, in <AMLCHECK_HOME>/.env (never in config.toml)
python -c "import secrets; print('AMLCHECK_API_TOKEN=' + secrets.token_urlsafe(32))" >> ~/.amlcheck/.env
amlcheck api                 # http://127.0.0.1:8766/v2 ; --port to change
```

The server listens on 127.0.0.1 only and refuses to start without a token of 32+ characters.

## Every request

| Header | Value |
|---|---|
| `Authorization` | `Bearer <AMLCHECK_API_TOKEN>` — required on every request, reads included; else **401** |
| `Host` | `127.0.0.1` or `localhost` (any port); anything else **400** |
| `Idempotency-Key` | Optional on `POST`: 1–255 printable ASCII characters, e.g. `order-1001` |
| `Content-Type` | `application/json` on `POST` |

Errors are `application/problem+json` (RFC 9457): `{"type", "title", "status", "detail"?}`.
**A verdict is never an error**: every check answer, INCOMPLETE included, is HTTP 200.

| Status | When | `type` ends with |
|---|---|---|
| 400 | Host not allowed; malformed `Idempotency-Key` | — |
| 401 | Missing or wrong token (with `WWW-Authenticate: Bearer`) | — |
| 404 | No such check, trace, route or chain | — |
| 409 | The first request with this `Idempotency-Key` is still running | `/idempotency-in-progress` |
| 422 | Invalid body (`errors` lists the fields); invalid address; key reused with another request | `/invalid-request`, `/invalid-address`, `/idempotency-key-mismatch` |

## Idempotency (IETF draft "The Idempotency-Key HTTP Header Field")

- Send the same key when you retry: you get the original answer (same `check_id` or `trace_id`) with
  `Idempotent-Replayed: true`, and nothing is checked twice.
- The same key with a different request (another address, amount, client, note or trace choice) is
  **422**; a retry while the first is still running is **409** (wait and retry with the same key).
- Use one key per business event (e.g. the payout id). Keys never expire once answered.

## Endpoints

| Method and path | Body | Answer |
|---|---|---|
| `POST /v2/check` | `{"address", "chain"?, "amount"?, "client"?, "note"?, "trace"?}` | 200: the check, as `amlcheck check --json` |
| `GET /v2/checks/{check_id}` | — | 200: the same JSON, from the audit log |
| `POST /v2/traces` | `{"address", "chain"?, "direction"?: "in" \| "out"}` | 202: `{"trace_id", "status", "links"}`, `Location` header |
| `GET /v2/traces/{trace_id}` | — | 200: status, progress, then `result` (the trace) or `partial` and `failure_reason` |
| `GET /v2/counterparties/{chain}/{address}` | — | 200: registry row and last score, labels, category, entity, own wallet, watched, open case, latest decision |

- `chain` is `tron` or `bsc` (an `0x` address is BSC: TRC20 and BEP20 only, D-039). `amount` is a decimal string or number
  (USDT). `trace` defaults to on from `[trace] auto_amount_usdt` (10,000 USDT), as `check`.
- One check and one trace run at a time per server; a check with its trace can take a few minutes,
  so give the HTTP client a long timeout (the mock uses 7 minutes).

### A check

```bash
curl -s http://127.0.0.1:8766/v2/check \
  -H "Authorization: Bearer $AMLCHECK_API_TOKEN" -H "Idempotency-Key: order-1001" \
  -H "Content-Type: application/json" \
  -d '{"address": "TA3941uFAvmVibSkQ6fMJXxmaSNovX86mz", "amount": "25000", "client": "acme"}'
```

The answer has:

| Field | What |
|---|---|
| `contract` | `2` |
| `check_id`, `verdict` (`BLOCK` / `REVIEW` / `INCOMPLETE` / `NO_HITS`), `action` | The verdict and what to do |
| `score` | `score` (0–100), `level` (`low` 0–30 · `moderate` 31–70 · `high` 71–90 · `severe` 91–100), `lower_bound`, `components` (`X` exposure, `B` behaviour), `hazard` (`in`, `out`), `decay`, `k`, `score_version`, `shown` |
| `address_label` | Who the address is: `name`, `source` (`sanctions`, `freeze`, `own_wallet`, `entity`, `label`, `classifier`), `category`, `inferred`, `confidence`; or `null` |
| `detail_list` | One plain line per risk type, e.g. `Sanctioned entity: direct received 5.0%` |
| `exposures` | Heaviest first: `direction` (`in`/`out`), `exposure_type` (`direct`/`indirect`), `hop`, `address`, `entity`, `category`, `risk_type`, `volume_usdt` (exact when direct, an estimate when indirect), `percent`, `inferred`, `confidence`, `path` |
| `findings`, `sources` | Each finding's `severity` is `BLOCK`, `INCOMPLETE`, `REVIEW` or `INFO` (shown, never changes the verdict) |
| `trace_id`, `decisions`, `audit.record_hash`, `tool_version`, `config_hash`, `disclaimer` | As before |

`risk_type` is one of `sanctioned_entity`, `frozen`, `illicit_activity`, `mixer`, `gambling`,
`risk_exchange`, `bridge` (methodology §11.2). **`NO_HITS` is not a clearance**: no rule needs a
review and the score is below the review threshold, in the sources checked, as of the times shown.

### A trace

```bash
curl -s -X POST http://127.0.0.1:8766/v2/traces -H "Authorization: Bearer $AMLCHECK_API_TOKEN" \
  -H "Content-Type: application/json" -d '{"address": "TVvWhZyLcd2DT2Y78XpyrUS3SyzfLeSsWP"}'
# poll until "status" is "done" or "failed"
curl -s http://127.0.0.1:8766/v2/traces/<trace_id> -H "Authorization: Bearer $AMLCHECK_API_TOKEN"
```

## The corridor mock

`scripts/corridor_mock.py` calls the API the way the corridor would: one check per payout, the payout
id as the `Idempotency-Key`, retries with the same key, and the exit code of `amlcheck check`.

```bash
export AMLCHECK_API_TOKEN=…   # the same token as the server's .env
uv run python scripts/corridor_mock.py TA3941uFAvmVibSkQ6fMJXxmaSNovX86mz --amount 25000 --order 1001
```
