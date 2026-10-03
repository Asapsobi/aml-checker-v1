# amlcheck — Open questions

> Questions for the owner. Claude Code adds one whenever the docs are ambiguous or a fact changed, and
> **keeps building whatever the question does not block**. Each has a proposed answer; the owner
> replies by editing **Answer** and **Status**, and the decision goes into [09-decisions.md](09-decisions.md).

| ID | Question | Needed by | Proposed answer | Answer | Status |
|---|---|---|---|---|---|
| Q-01 | Where does amlcheck run day to day: your laptop, the corridor server, or both? | P0 | Laptop first; server install guide in P10 | Accepted as proposed (D-024) | Answered |
| Q-02 | Which freeze-vendor plan: Eagle Virtual Free (1,000 checks/day, credit line) or Business? | P2 | Free until volume needs more | Accepted as proposed (D-025), then superseded: no third-party freeze API (D-033) | Superseded |
| Q-03 | Add the UK OFSI list if it carries crypto addresses (VS-11)? | P2 | Yes, if format and licence are clean | Accepted as proposed (D-026), then deferred to after v1 (D-041) | Answered |
| Q-04 | R-EXP-01 (a direct counterparty is sanctioned/frozen): REVIEW or BLOCK? | P3 | REVIEW (D-014) | REVIEW, as proposed (D-014 accepted) | Answered |
| Q-05 | How long to keep cached histories of addresses that are not counterparties, labelled or own? | P4 | 30 days | 30 days, as proposed (= `[cache] history_keep_days`; D-043) | Answered |
| Q-06 | Do you have existing address lists (own wallets, known OTC partners, known bad actors) to import as labels at P4? | P4 | Import as `labels.csv` and operator labels | | Open |
| Q-07 | Accept proportional trace shares as an *estimated* metric next to absolute amounts (D-016)? | P6 | Yes | | Open |
| Q-08 | R-TRC-01 (sanctioned wallet 2–3 hops upstream, ≥ 1,000 USDT): REVIEW or BLOCK? | P6 | REVIEW | | Open |
| Q-09 | If the free HyperSync plan can't meet the trace budget, is a paid tier OK, and at what monthly budget? | P6 | Decide after VS-07 | | Open |
| Q-10 | Should the score ever change the verdict (R-SCR-01)? | P7 | Off | | Open |
| Q-11 | Notifications: macOS notification only, or also a local webhook (e.g. into a Telegram bot you run)? | P8 | Notification only; webhook optional | | Open |
| Q-12 | Operator name on decisions: one name from config, or asked each time? | P9 | From config | | Open |
| Q-13 | Which own wallets are monitored, and how often? | P10 | All registered, every 10 min | | Open |
| Q-14 | Who supplies the golden-set addresses (known clean, known exchange deposits, decided cases)? | P11 | Owner picks 20 per chain, Claude Code proposes the rest from public data | | Open |
| Q-15 | `[cache] target_ttl_seconds`: how old may the cached tail of a **target's** history be before a check re-reads it up to now? The docs name the key but give no default | P1 | 60 s (a check always sees transfers older than a minute) | Accepted as proposed (D-028) | Answered |
| Q-16 | Defaults the docs don't give: `[network] timeout_seconds`, `[tron] requests_per_second`, `[eagle_virtual] quota_warn_share` | P1 | 20 s; 10 req/s until VS-05 measures the key's limit; warn at 80% of the daily quota | Accepted as proposed (D-029) | Answered |
| Q-17 | TronGrid transfer rows have no event index, and one transaction can hold several USDT transfers that show up differently in the sender's and the recipient's history (VS-04). How do we identify a TRON transfer in the shared cache? | P1 | `idx` = how many earlier rows of the same transaction have the identical (`from`, `to`, `value`); the TRON key becomes (`tx_hash`, `sender`, `recipient`, `amount`, `idx`). Same answer from either side, no extra calls. Exact event indexes only if a later phase needs them, via `/v1/transactions/{tx}/events` | Accepted as proposed (D-030) | Answered |
| Q-18 | Keyless TronGrid answers 429 without a `Retry-After` header (the wait is only in the message, "suspended for 5 s"). What should the client do with a 429/403 that has no `Retry-After`? | P1 | Wait 5 s once and retry (within the ≤ 10 s rule of D-011); a second refusal is a source error. Re-check once VS-05 runs with your key | Accepted as proposed (D-031) | Answered |
| Q-19 | Eagle Virtual 1.3.0 can answer `CLEAR` while some chains are behind (`not_vouched_for`). When is that a gap? | P2 | `stale` → INCOMPLETE when the target's own chain (TRON, or BNB Chain for BSC) is listed. Other EVM chains behind: note it in the evidence, not a gap. Keeps "never a clean result over a gap" for the chain we transact on | Accepted as proposed (D-032), then superseded: Eagle Virtual dropped (D-033) | Superseded |
| Q-20 | With your key TronGrid allows 15 requests/s, and going over **suspends the key for 30 s** (429, no `Retry-After`; VS-05). D-031's "wait 5 s once" would fail anyway. What should happen? | P1 | Prevent it: one process-wide TronGrid limiter at `[tron] requests_per_second` = 10. If a 429/403 without `Retry-After` still comes, a **check** treats the source as `error` at once (a 30 s wait breaks the 10 s rule); **traces and syncs** wait it out through the pacer, using the seconds in "suspended for N s" or 30 s if absent (within the 65 s pacer limit). Supersedes D-031 | Accepted as proposed (D-036) | Answered |
