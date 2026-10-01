# amlcheck — Architecture

> Stack, package layout, interfaces and flows for a from-scratch build. Claude Code creates this layout
> in P0 and fills it phase by phase. Change it only through a decision in [09-decisions.md](09-decisions.md).

---

## 1. Stack

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.12+ | Owner's main language; strong typing with mypy |
| Packaging | `uv`, `pyproject.toml`, lockfile committed | Fast, reproducible, installs as a tool (`uv tool install`) |
| CLI | Typer + Rich | Sub-commands, typed options, readable tables |
| HTTP client | `httpx` (async) + `tenacity` | One client, retries, timeouts |
| Models | Pydantic v2 for config and I/O; frozen dataclasses for domain | Validation at the edges, plain objects inside |
| Storage | SQLite (WAL), numbered append-only SQL migrations | Single file, zero ops, enough for ~1M transfers |
| Web UI | FastAPI + Jinja2 + HTMX (vendored, no CDN) | Server-rendered, small, strict CSP |
| API | FastAPI, same process family as the web UI | One stack |
| PDF | reportlab | No browser needed |
| Address checks | `base58`, `eth-hash` (Keccak for EIP-55) | Minimal dependencies |
| Tests | pytest, pytest-asyncio, respx, hypothesis | Fixtures for providers, property tests for the trace |
| Quality | ruff (lint + format), mypy `--strict` on everything | Caught early, CI-enforced |
| CI | GitHub Actions: Ubuntu + macOS × Python 3.12 / latest, Windows 3.12 | Matches where it runs |

---

## 2. Package layout

```
aml-checker-v1/
├── pyproject.toml · uv.lock · .python-version · config.example.toml · .env.example
├── CLAUDE.md · README.md · docs/ · .claude/
├── src/amlcheck/
│   ├── __init__.py              version from package metadata
│   ├── config.py                Pydantic settings: every section in §6
│   ├── cli/                     one Typer sub-app per file: check, batch, sync, status, audit,
│   │                            watch, labels, cp, intel, classify, trace, case, wallets, monitor,
│   │                            cache, web, api
│   ├── core/
│   │   ├── models.py            Chain, Address, Verdict, Severity, SourceStatus, Finding,
│   │   │                        SourceResult, CheckResult
│   │   ├── address.py           detect, validate, normalise (F1)
│   │   ├── clock.py             injectable UTC clock, ISO helpers
│   │   ├── rules.py             rule IDs, default severities, overrides, R-SYS-01, fixed rules
│   │   ├── verdict.py           precedence (methodology §2.5)
│   │   ├── engine.py            screen(): run sources concurrently → rules → verdict → score
│   │   │                        → audit append → registry upsert
│   │   ├── audit.py             hash chain: append, verify, canonical JSON
│   │   └── score.py             methodology §9
│   ├── net/
│   │   ├── http.py              shared AsyncClient, timeouts, Retry-After policy
│   │   └── limits.py            token bucket, budget pacer (reads provider budget headers)
│   ├── chain/
│   │   ├── base.py              Transfer, History, HistorySource protocol, ContractLookup protocol
│   │   ├── tron.py              TronGrid: transfers, account, contract, events, constant calls
│   │   ├── bsc.py               BSC indexer (HyperSync) transfers + public RPC eth_getCode
│   │   └── cache.py             TransferCache: persistent, covered windows, gap fetch (F2)
│   ├── screening/
│   │   ├── base.py              SourceAdapter protocol, failed()
│   │   ├── sanctions.py         OFAC download, parse, snapshots, adapter (F3)
│   │   ├── tron_freeze.py       Tether event index + isBlackListed adapter (F4)
│   │   ├── bsc_freeze.py        always-skipped adapter with reason (F4.4)
│   │   ├── exposure.py          1-hop walk adapter: R-EXP-01/02, R-HEU-01..05 (F5)
│   │   └── heuristics.py        pure pattern functions (pass-through, busiest window)
│   ├── intel/
│   │   ├── categories.py        methodology §8 as code
│   │   ├── store.py             labels, entities, membership, best_terminal()
│   │   ├── labels_csv.py        labels.csv import (F5.3)
│   │   ├── packs.py             licensed label packs (F7.4)
│   │   ├── registry.py          counterparties upsert, query, rebuild (F7.3)
│   │   └── lookalike.py         key + R-HEU-06 adapter (F7.5)
│   ├── profile/
│   │   ├── features.py          Profile + profile() pure (methodology §5)
│   │   ├── classifier.py        classify() pure + entity linking (methodology §6)
│   │   └── adapter.py           R-HEU-07 adapter
│   ├── trace/
│   │   ├── model.py             items, nodes, edges, buckets, JSON
│   │   ├── engine.py            methodology §7
│   │   ├── annotate.py          peel chains (methodology §6.1)
│   │   ├── rules.py             R-TRC-01..05
│   │   ├── adapter.py           TraceAdapter (required when it runs)
│   │   ├── jobs.py              TraceQueue: persistent jobs, one worker, progress, resume
│   │   └── graph.py             layered SVG
│   ├── cases/
│   │   ├── cases.py             open, close, confirm/reject inferences, name entities
│   │   └── decisions.py         decision hash chain
│   ├── monitor/
│   │   ├── wallets.py           own wallets
│   │   └── inbound.py           monitor run, lock, alerts
│   ├── report/
│   │   ├── export.py            audit export CSV/JSON/PDF (CSV-injection safe)
│   │   └── case_report.py       per-counterparty PDF
│   ├── storage/
│   │   ├── db.py                connect (WAL), migrate, transactions
│   │   └── migrations/0001_….sql …
│   ├── web/                     app.py, templates/, static/ (htmx vendored with licence)
│   └── api/                     app.py, schemas.py, idempotency.py, problems.py
└── tests/
    ├── unit/                    pure logic, adapters with respx
    ├── fixtures/                recorded provider answers, sample OFAC XML, DB fixtures
    └── golden/                  recorded histories for calibration (P11)
```

**Dependency direction:** `cli`, `web`, `api` → `core.engine` → `screening`, `intel`, `profile`,
`trace` → `chain`, `storage`, `net`. Nothing below imports from above. `core/models.py`,
`core/rules.py` and `intel/categories.py` import nothing from the project.

---

## 3. Key interfaces

```python
# screening/base.py
class SourceAdapter(Protocol):
    source: str                     # stable id, e.g. "ofac_sdn"
    label: str                      # shown to the operator
    required: bool                  # failure → R-SYS-01
    timeout: float | None           # None = engine default
    async def check(self, address: Address) -> SourceResult: ...
    async def health(self) -> SourceHealth: ...

# chain/base.py
@dataclass(frozen=True)
class Transfer:
    chain: Chain; tx_hash: str; idx: int; block: int | None
    time: datetime; sender: str; recipient: str; amount: Decimal

@dataclass(frozen=True)
class History:
    transfers: tuple[Transfer, ...]      # newest first
    since: datetime; until: datetime
    complete: bool                       # False: window held more than the limit
    first_activity: datetime | None
    zero_value: int

class HistorySource(Protocol):
    chain: Chain
    async def fetch(self, address: str, since: datetime, until: datetime | None,
                    limit: int, *, first_activity: bool) -> History: ...

# chain/cache.py
class TransferCache:
    async def history(self, address: str, since: datetime, until: datetime | None,
                      limit: int, *, first_activity: bool = False) -> History: ...
    def stats(self) -> CacheStats: ...
    def prune(self, keep_days: int, keep: Callable[[Chain, str], bool]) -> int: ...

# intel/store.py
class IntelStore:
    def best_terminal(self, chain: Chain, address: str) -> Terminal | None: ...
    def add_label(self, label: NewLabel) -> int: ...
    def retract(self, label_id: int, reason: str, by: str) -> None: ...
    def entity_for(self, chain: Chain, address: str) -> Entity | None: ...
    def link(self, chain: Chain, address: str, entity_id: int, role: str,
             evidence: dict[str, Any]) -> None: ...

# profile
def profile(address: str, history: History, window: tuple[datetime, datetime],
            settings: ClassifierSettings) -> Profile: ...
def classify(p: Profile, ctx: ClassifyContext, settings: ClassifierSettings,
             now: datetime) -> list[Classification]: ...

# trace/engine.py
class TraceEngine:
    async def run(self, target: Address, direction: Literal["in", "out"],
                  progress: Callable[[TraceProgress], None] | None = None) -> Trace: ...
    # raises TraceFailed(partial: Trace, reason: str)

# core/engine.py
async def screen(address: Address, sources: Sequence[SourceAdapter], *, conn, config,
                 amount: Decimal | None = None, client: str | None = None,
                 note: str | None = None, now: Clock = utcnow) -> CheckResult: ...
```

---

## 4. Flows

### 4.1 A check

| # | Step |
|---|---|
| 1 | Parse and normalise the address; refuse invalid input (no record) |
| 2 | Build sources for the chain: sanctions, freeze sources, exposure, look-alike, classification; trace if amount ≥ threshold or requested |
| 3 | Run them concurrently. All history reads go through one `TransferCache`, so the target is read once |
| 4 | Collect findings → apply severity overrides → add R-SYS-01 for gaps → verdict |
| 5 | Compute the score (never fails the check; on error the field stays unset and is logged) |
| 6 | Append the audit record (hash chain) **before** anything is shown |
| 7 | Upsert the registry; persist classifications and entity links |
| 8 | Render: human, JSON, web or API |

### 4.2 A trace job

```
request (CLI / web / API / check / monitor)
   └─► traces row: queued ─► TraceQueue worker (one per process)
          └─► TraceEngine.run ─► TransferCache (paced provider reads)
                 ├─ progress → traces.progress_json (≤ 1 write/s) → CLI bar / HTMX poll / API GET
                 ├─ done   → result_json (+ linked check if the trace was part of a check)
                 └─ failed → partial_json + reason (→ INCOMPLETE if part of a check)
```

### 4.3 Monitoring run

```
lock ─► each own wallet: new inbound transfers since monitor_state
     ─► senders not screened within rescreen_days ─► check (+ trace if amount ≥ threshold)
     ─► REVIEW/BLOCK → table, notification, optional local webhook ─► save state ─► unlock
     ─► exit 0, or 6 if anything needs attention
```

---

## 5. Concurrency & budgets

| Concern | Rule |
|---|---|
| Provider limits | One process-wide limiter per provider. The BSC indexer pacer reads the budget headers on every answer and waits for the next window rather than being refused |
| Checks | API, web and batch run checks one at a time per process |
| Traces | One worker per process; CLI `trace` runs inline but takes the same lock |
| Schedulers | `batch`, `watch run`, `monitor run` take a file lock in `~/.amlcheck/` |
| SQLite | WAL, short transactions, one writer connection per process |
| Determinism | Never iterate a set or dict without sorting; time only from the injected clock |

---

## 6. Configuration

`~/.amlcheck/config.toml` (override with `AMLCHECK_CONFIG`); data in `~/.amlcheck/` (override with
`AMLCHECK_HOME`); keys from the environment, then `./.env`, then `~/.amlcheck/.env`.

| Section | Holds |
|---|---|
| `[freshness]` | sanctions max age, TRON index max lag |
| `[network]` | timeout, max Retry-After |
| `[rules]` | severity overrides |
| `[exposure]` | lookback, max transfers, flagged inflow share |
| `[heuristics]` | R-HEU thresholds, risky tags, allowlist tag |
| `[cache]` | target TTL, history keep days |
| `[classifier]` | window, TTL, every threshold in methodology §6 |
| `[trace]` | every key in methodology §7.1 |
| `[score]` | `review_at` (0 = off) |
| `[monitor]` | rescreen days, trace amount, webhook URL |
| `[operator]` | name recorded on decisions |
| `[ofac]`, `[tron]`, `[bsc]` | URLs, contracts, rate limits |

The hash of the effective config is stored with every check.

---

## 7. Testing strategy

| Layer | How |
|---|---|
| Pure logic (address, rules, verdict, heuristics, features, classifier, trace, score) | Unit tests on synthetic data; property tests for the trace (partition = 1, determinism, budget never exceeded) |
| Adapters | `respx` against recorded real answers in `tests/fixtures/`. Never the network |
| Storage | Migrations from an empty DB and from every earlier released schema |
| Audit | Tamper tests on each hash chain |
| CLI / web / API | Typer runner and FastAPI test client against fake sources |
| Golden set | Recorded histories replayed offline (P11) |
| Live | Each phase records live runs in `docs/acceptance-results.md` |
