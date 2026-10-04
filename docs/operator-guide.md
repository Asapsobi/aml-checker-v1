# amlcheck — Operator guide

> How to use amlcheck day to day: screening a counterparty, reading the result, deciding, and keeping
> the data fresh. Internal use only (D-023). Commands in full: [README](../README.md).

## The daily rhythm

| When | What | How |
|---|---|---|
| Automatically, twice a day | Refresh the OFAC list and the Tether TRON freeze index | `amlcheck sync` on a timer ([scheduling](scheduling.md)) |
| Automatically, every 10 minutes | Screen new senders to your own wallets | `amlcheck monitor run` (exit 6 = look at it) |
| Automatically, once a day | Re-screen the watchlist | `amlcheck watch run` (exit 6 = a verdict changed) |
| Before every payout | Screen the counterparty | `amlcheck check <address> --amount N --client NAME` (or the web UI, or the API) |
| When a result is REVIEW, BLOCK or INCOMPLETE | Decide, and record why | A case: `amlcheck case open <address>`, then `case decide` |
| Now and then | Check the audit trail | `amlcheck audit verify`; keep the two head hashes it prints somewhere else |

Before anything else each morning, `amlcheck status`: the OFAC list must be younger than 48 hours and
the freeze index current, or every check comes back INCOMPLETE.

## Screening a counterparty

```bash
amlcheck check TXyz… --amount 25000 --client acme --note "invoice 1042"
```

- Paste addresses; never type them. A wrong character makes a valid but different address.
- TRON addresses start with `T`; `0x…` addresses are BNB Smart Chain (BEP20) in this version.
- `--amount` matters: from 10,000 USDT the source-of-funds trace runs too (a few minutes).
- `amlcheck investigate` is the same check with the trace always on and the full breakdown.
- Every check is recorded in the audit log **before** it is shown.

## Reading the result

| Verdict | Meaning | What to do |
|---|---|---|
| **BLOCK** | The address is on a sanctions list, or frozen by Tether, now | Do not transact. Escalate. Open a case and record the decision |
| **INCOMPLETE** | A required source failed or is stale, so a clean result can't be given | Retry. If it persists, `amlcheck status` (stale lists → `sync`; provider errors → try later). Treat as REVIEW meanwhile |
| **REVIEW** | Something needs a human look (see the findings) | Read the findings, the counterparties and the trace. Decide in a case |
| **NO_HITS** | Nothing was found in the sources checked, as of the times shown | Proceed per policy. **This is not a clearance** |

**Who** is the address itself: its sanctions entry, Tether's freeze, your own wallet, a named entity
or label, or the classifier's guess marked *inferred* with its confidence.

The **score** (0–100) ranks how worrying the money's history looks, on MistTrack's scale
(methodology §11):

| Level | Score | What to do |
|---|---|---|
| low | 0–30 | Minimal supervision. Proceed per policy; not a clearance |
| moderate | 31–70 | Review by hand before transacting (REVIEW from 31) |
| high | 71–90 | Investigate before transacting; keep under close watch |
| severe | 91–100 | Do not transact; escalate (BLOCK is always 100) |

It is built from the **exposures**: money the address received from or sent to risk categories
(sanctioned, Tether-frozen, illicit activity, mixer, gambling, high-risk exchange, bridge), directly
(a counterparty, exact amounts) or through others (2–3 hops, the smallest amount on the path). Each
extra hop counts 40% less. Behaviour (new, pass-through, fan-in, fan-out, look-alike) adds at most
30 points, so behaviour alone stays low. Unknown services add nothing. `≥ 34 · moderate+` means a
lower bound: a source was missing.

The lines under the score say it in one line per risk type, e.g. `Sanctioned entity: indirect
received 16.9%`. Findings marked **INFO** explain the score; they don't change the verdict.

### Findings

REVIEW comes from R-EXP-01, R-FRZ-02, R-HEU-06 and R-SCR-01; BLOCK from R-SAN-01 and R-FRZ-01. The
others are INFO: they explain the score (D-072; `[rules] severity` can change that).

| Rule | Means | Look at |
|---|---|---|
| R-SAN-01 | On a sanctions list (OFAC) | The list entry named in the finding |
| R-FRZ-01 / 02 | Frozen by Tether now / frozen before and released | When; why it was released |
| R-EXP-01 | Dealt directly with a sanctioned or frozen address | The counterparty table: amounts and dates |
| R-EXP-02 | 5% or more of what it received came from flagged addresses | Which ones, how much |
| R-HEU-01 | New: first activity under 7 days ago (low priority) | Expected for a new client wallet? |
| R-HEU-02 | Pass-through: 90%+ of what comes in leaves within 24 h | Normal for exchange deposits; odd for a business |
| R-HEU-03 / 04 | Fan-in / fan-out: many small senders / recipients in a day | Collection or distribution pattern |
| R-HEU-05 | Dealt with an address labelled mixer, bridge or high-risk | Your labels and imported packs |
| R-HEU-06 | **Looks like** a known counterparty or own wallet but isn't | Address poisoning: compare the full address character by character |
| R-HEU-07 | The address behaves like a collector or distributor (low priority, an inference) | The classification and its conditions |
| R-TRC-01 / 02 | The money traces back to a sanctioned / frozen wallet 2–3 hops away | The path; every hop moved at least the bottleneck amount |
| R-TRC-03 | 5% or more of the money traces to high-risk categories | The trace breakdown |
| R-TRC-04 | Under 50% of the money could be traced (low priority) | How much is untraced and why |
| R-TRC-05 | 10% or more passed through inferred suspicious patterns (low priority) | Collectors, layering |
| R-SYS-01 | A required source failed or is stale | Makes the check INCOMPLETE |
| R-SCR-01 | The score is 31 or more (moderate or worse) | The exposures and the lines under the score |

**Inferences are not facts.** Types like COLLECTOR or DEPOSIT, and everything marked "inferred", come
from the address's own transfers and are never a reason to BLOCK on their own.

### The source of funds

- **Shares are estimates**: USDT is fungible, so money is split proportionally at every hop. Trust the
  ranking, not the decimals.
- **Bottleneck** on a path: the smallest hop on it; every hop moved at least that much. The estimate
  next to it is the proportional share.
- **Untraced** is said out loud: `pruned` (small senders left out), `depth` (beyond 3 hops),
  `budget`, `cycle`, `no_inflow`. A low coverage is itself a reason to look closer.
- `amlcheck trace <address> --svg graph.svg` draws it; the web UI shows it on the trace page.

## Deciding: cases

```bash
amlcheck case open TXyz…                      # from its latest REVIEW, BLOCK or INCOMPLETE check
amlcheck case show <case>                     # the check, its types, decisions so far
amlcheck case decide <case> approved --note "known OTC client; source of funds matches invoice 1042"
```

- `approved` and `rejected` close the case; `escalated` keeps it open for someone else.
- The note is the record: say what you looked at and why. Your name comes from `[operator] name` in
  `config.toml` (set it once; decisions without a name are refused).
- Decisions are hash-chained and tied to the exact check record; they are never edited.
- Teach amlcheck while you're there: `case confirm <case> DEPOSIT --name Binance --kind
  exchange_regulated`, `case confirm <case> COLLECTOR --category scam`, or `case reject <case>
  COLLECTOR` when an inference is wrong (it won't come back until the classifier changes).
- `amlcheck cp report <address>` makes the PDF for the file: verdict, score, findings, history,
  trace, decisions.

## Keeping the intelligence useful

| Task | Command |
|---|---|
| Register your own wallets (trusted, monitored, protected from look-alikes) | `amlcheck wallets add <address> --name "TRON hot wallet"` |
| Your own tags (`mixer`, `bridge`, `high_risk`, `allowlist`) | `amlcheck labels import labels.csv` |
| A label on one address | `amlcheck intel label <address> scam --note …` |
| A licensed third-party pack | `amlcheck intel import-pack file.csv --name … --licence "…"` |
| Name an exchange's entity (its deposits then resolve locally in traces) | `amlcheck intel entity name <id> Binance --kind exchange_regulated` |
| Watch an address daily | `amlcheck watch add <address> --client acme` |

## When something goes wrong

| Symptom | Likely cause | Fix |
|---|---|---|
| Every check INCOMPLETE: "OFAC SDN list stale: … downloaded 52 h ago …; run `amlcheck sync`" | `sync` hasn't run | `amlcheck sync`; check the timer |
| INCOMPLETE "rate limited" or "HTTP 5xx" on TronGrid / HyperSync | Provider trouble or limits | Retry later; `amlcheck status` |
| "another batch or watch run is in progress" | Two scheduled runs overlapped | Nothing: the next run catches up |
| `audit verify` reports a break | The database was changed outside amlcheck | Stop; restore from backup; compare with your kept head hashes |
| "this database was not created by this amlcheck" | `AMLCHECK_HOME` points at another tool's data | Point it at the right folder |

## Limits to keep in mind

- TRC20 (TRON) and BEP20 (BNB Smart Chain) USDT only in this version.
- BEP20 USDT cannot be frozen by its issuer: the freeze check is skipped there, never "clean".
- Sources are public lists and chain data only; no commercial AML provider is consulted (D-033).
- NO_HITS is never a clearance; a decision is always yours.
