# amlcheck — Open questions

> Questions for the owner. Claude Code adds one whenever the docs are ambiguous or a fact changed, and
> **keeps building whatever the question does not block**. Each has a proposed answer; the owner
> replies by editing **Answer** and **Status**, and the decision goes into [09-decisions.md](09-decisions.md).

| ID | Question | Needed by | Proposed answer | Answer | Status |
|---|---|---|---|---|---|
| Q-01 | Where does amlcheck run day to day: your laptop, the corridor server, or both? | P0 | Laptop first; server install guide in P10 | Accepted as proposed (D-024) | Answered |
| Q-02 | Which freeze-vendor plan: Eagle Virtual Free (1,000 checks/day, credit line) or Business? | P2 | Free until volume needs more | Accepted as proposed (D-025) | Answered |
| Q-03 | Add the UK OFSI list if it carries crypto addresses (VS-11)? | P2 | Yes, if format and licence are clean | Accepted as proposed (D-026) | Answered |
| Q-04 | R-EXP-01 (a direct counterparty is sanctioned/frozen): REVIEW or BLOCK? | P3 | REVIEW (D-014) | | Open |
| Q-05 | How long to keep cached histories of addresses that are not counterparties, labelled or own? | P4 | 30 days | | Open |
| Q-06 | Do you have existing address lists (own wallets, known OTC partners, known bad actors) to import as labels at P4? | P4 | Import as `labels.csv` and operator labels | | Open |
| Q-07 | Accept proportional trace shares as an *estimated* metric next to absolute amounts (D-016)? | P6 | Yes | | Open |
| Q-08 | R-TRC-01 (sanctioned wallet 2–3 hops upstream, ≥ 1,000 USDT): REVIEW or BLOCK? | P6 | REVIEW | | Open |
| Q-09 | If the free HyperSync plan can't meet the trace budget, is a paid tier OK, and at what monthly budget? | P6 | Decide after VS-07 | | Open |
| Q-10 | Should the score ever change the verdict (R-SCR-01)? | P7 | Off | | Open |
| Q-11 | Notifications: macOS notification only, or also a local webhook (e.g. into a Telegram bot you run)? | P8 | Notification only; webhook optional | | Open |
| Q-12 | Operator name on decisions: one name from config, or asked each time? | P9 | From config | | Open |
| Q-13 | Which own wallets are monitored, and how often? | P10 | All registered, every 10 min | | Open |
| Q-14 | Who supplies the golden-set addresses (known clean, known exchange deposits, decided cases)? | P11 | Owner picks 20 per chain, Claude Code proposes the rest from public data | | Open |
