# Knowing who we deal with: an in-house counterparty intelligence tool for USDT

*A six-page narrative for management. Owner: Sobi. Date: 1 October 2026.
Read time: about 20 minutes. Details are in the PRD and specs in this repository; this memo explains
why, what and how much.*

---

## 1. Purpose

This memo asks for approval to build **amlcheck**, a local tool that screens and investigates every
address our business sends USDT to or receives USDT from, on TRON and BNB Smart Chain. The build takes
about thirteen weeks of AI-assisted development with one owner reviewing the work, runs on free or
low-cost data, and gives us a first usable screening tool in week three. We are asking for a decision
on the approach (section 5), on a small data budget held in reserve (section 8), and on three policy
questions (section 10).

## 2. Tenets

These guide the trade-offs in the design. We will follow them unless management sets better ones.

1. **Never a false "clean".** If any data we rely on is missing or out of date, the tool says so. A
   gap is never presented as safety.
2. **Every answer shows its evidence.** A result we cannot explain to a bank, an exchange or a
   regulator is worth little. Each finding names its source, time and transaction.
3. **Depth on our scope beats breadth on everyone's.** We need to know a lot about the few thousand
   addresses we deal with, not a little about every address on two blockchains.
4. **The tool advises; a person decides, and the decision is recorded.**
5. **Spend nothing until free sources run out**, and know exactly where they will.

## 3. The problem

Our settlement corridor and OTC flows move USDT between addresses we do not control. Every inbound
payment comes from somebody's wallet, and every outbound payment goes to one. If that wallet belongs
to a sanctioned party, holds stolen or frozen funds, or is part of a scam operation, the consequences
land on us. Stablecoin issuers can freeze balances on TRON. Exchanges and banks that serve us can close
accounts when they trace tainted funds through us. And sanctions exposure is a legal risk in itself,
whatever our intentions.

The risk rarely sits on the address in front of us. A wallet that looks new and clean often received
its money, one or two transfers earlier, from a wallet that is not. Scam operations collect many
small payments from victims into one address, pass them through a chain of fresh addresses, and pay
out from the end of that chain. Attackers also "poison" our transaction history with addresses that
look almost identical to our real counterparties, hoping an operator copies the wrong one. Spotting
these patterns needs more than a lookup: it needs the address's history, the history of the
addresses that funded it, and a memory of what we have seen before.

Without a dedicated tool, this work depends on an operator's time, a block explorer and whatever free
checkers are available that day. It is slow, it varies from person to person, and it leaves no
reliable record of what was checked, what was found and who decided what. When a bank or an exchange
asks us how we screened a counterparty, we need to answer in minutes with evidence, not reconstruct it
from memory.

## 4. Options we considered

**Option A: buy a commercial blockchain-analytics service.** Chainalysis, TRM Labs, Elliptic, Crystal
and AMLBot index whole blockchains with their own infrastructure and employ teams of analysts who
label addresses. Their coverage is the best available, and their reports carry weight with banks.
They are built and priced for exchanges and financial institutions, usually on annual contracts, and
the free tiers that once existed have closed (Chainalysis's free sanctions API no longer accepts new
users). Their scoring is a black box we cannot tune, and every check sends our counterparty list to a
third party. We keep this option open: the design has a plug-in point for one vendor, called only for
the cases that need it.

**Option B: build our own Chainalysis.** Run our own blockchain nodes, index every transfer on both
chains, and label addresses at scale. This is how the big providers work. It needs servers with many
terabytes of fast storage, people to keep the nodes running, and above all a permanent team of
analysts to build the labels, which is where most of those companies' value comes from. It would take
a year or more before it was useful. We reject it as out of proportion to our needs.

**Option C (proposed): build a lean, counterparty-centred tool.** Read chain data on demand from
established providers, without running nodes. Build knowledge only about the addresses we actually
deal with and the addresses their money came from. Keep everything we learn, so the tool gets better
with every check. Use free official and public data first: the US sanctions list, the stablecoin
issuer's own freeze records on TRON, and an independent freeze-history service that covers both
chains.

**Option D: keep doing it by hand.** Cheapest today, and acceptable at very low volume, but it does
not produce evidence, does not scale, and does not catch the indirect patterns described above.

Option C gives us most of the depth of Option A on our narrow scope, at a small fraction of the cost
of Option B, and we stay in control of the methods and the data.

## 5. What we will build

For every counterparty, amlcheck answers four questions.

**Can we transact with this address right now?** The tool checks the address against the US Treasury
sanctions list (held locally and refreshed daily), against stablecoin freeze and seizure records, and
against the address's own six months of USDT activity. It returns one of four answers: *Block*,
*Review*, *Incomplete* (some data was unavailable, so do not rely on the result) or *No hits* (nothing
found in the sources checked; this is deliberately not called "clear"). A check takes seconds, and
every check is written to a tamper-evident log before the operator sees the result.

**Where did its money come from?** For larger payments, the tool follows the money back up to three
steps. It reads the address's incoming payments, then the incoming payments of the largest senders,
and so on, until it reaches something it recognises: a sanctioned wallet, a frozen wallet, a known
exchange, a pattern typical of scams. It reports what share of the money came from each kind of
source, and states plainly what share it could not trace. It also shows the actual amounts on each
path, because a percentage alone can mislead. This is the capability that commercial tools charge
most for. We can offer it on our scope because we only trace what we actually receive.

**Who is this address, probably?** Many addresses have no public label. The tool recognises the
fingerprints of common address types: an exchange's main wallet (very many counterparties), a
customer deposit address at an exchange (receives from a few people and sweeps everything to the
exchange within hours), a collection address typical of scams (many small payments in, one large
payment out), and a pass-through wallet (money leaves as fast as it arrives). Each guess comes with a
confidence and the reasons behind it. Guesses never block a payment on their own. When an operator
confirms one, for example "this hub is Binance", every related address inherits the label, and the
next investigation is faster.

**How risky is it overall, and what did we decide?** A score from 0 to 100 summarises the findings
for quick comparison and trend-watching, with a breakdown that shows where each point came from.
When the answer is *Review*, the operator opens a case, looks at the evidence and the picture of the
money flow, and records a decision (approve, reject or escalate) with a note. Decisions are kept in
their own tamper-evident log and appear in every export and report. Management can see, for any
counterparty, what was checked, what was found, who decided and why.

Two further features complete the picture. **Inbound monitoring** watches our own wallets and
screens every new sender automatically, so a counterparty does not depend on someone remembering to
check it. A **look-alike guard** warns when an address resembles one of our known counterparties
without being it, which stops the most common copy-paste fraud.

The tool runs on a laptop or a small server we already have. Its data stays with us. A local
interface lets our settlement system ask for a check before it pays.

## 6. What we will not do

We will not run blockchain nodes, label the whole of either blockchain, cover other chains or tokens,
trace money across bridges between chains, or build machine-learning models in this version. We will
not block payments automatically; the tool advises and a person decides. We will not sell or show
results to clients: the data licences we rely on assume internal use. Each of these can be revisited
later with evidence from real use.

## 7. Plan

The work is split into twelve phases, each delivered as one reviewable change and one release. The
first three weeks produce a working screening check. By week seven the tool recognises unknown
addresses, by week nine it traces sources of funds, and by week thirteen it has cases, monitoring, a
local interface for the settlement system, and thresholds tuned on a test set of known addresses.

| Milestone | Week | What management can see |
|---|---|---|
| Data sources verified | 1 | A written report of what each provider offers today |
| Screening check in use | 3 | Every check recorded with evidence |
| Counterparty memory, look-alike guard | 5 | The tool knows who we have dealt with |
| Address classification | 7 | Reasons behind each "who is this" |
| Source-of-funds tracing | 9 | Flow picture and breakdown per counterparty |
| Score and one-page reports | 10 | One file per counterparty |
| Web interface, batch, re-screening, exports | 11 | Daily use without the command line |
| Cases and decisions | 12 | Decision trail for management |
| Monitoring, settlement integration, calibration | 13 | Version 1.0 signed off |

The development is done by Claude Code, an AI coding agent, against the detailed specifications in
this repository. The owner reviews each phase before it is merged, answers questions the agent raises,
and runs the live checks. We estimate four to six hours of owner time per week.

## 8. Cost

Running costs are close to zero at our expected volume, because every data source we need has a
usable free tier. We have identified, and measured, where each one stops.

| Item | Cost | When it would change |
|---|---|---|
| Machine | Existing laptop or a small server we already run | — |
| US sanctions list | Free | — |
| TRON chain data (TronGrid) | Free key | Very high volume |
| BSC chain data (Envio HyperSync) | Free plan, about 30 queries a minute | Deep traces at high volume may need a paid tier |
| Freeze history (Eagle Virtual) | Free plan, 1,000 checks a day | More than ~1,000 checks a day, or to drop the attribution line |
| Fallback BSC data (Etherscan Lite) | $49 a month | Only if HyperSync becomes unusable |
| Development | Claude Code subscription + owner review time | — |

We ask management to approve a **reserve** for paid data tiers, to be spent only if measurements in
phase six show the free tiers cannot keep traces within the time targets. The owner will bring a
measured request, not an estimate, before spending it.

## 9. Risks

The main risk is that **operators read "no hits" as "safe"**. The wording, the disclaimer on every
result and the training note address this; the tool never uses the word "clear".

The second is that **estimates are mistaken for proof**. Proportional tracing cannot say which exact
dollar went where, because money mixes inside a wallet. The tool therefore labels these shares as
estimates, always shows the real amounts on each path beside them, and uses the amounts, not the
shares, for its strongest warnings.

Third, **automatic guesses about address types will sometimes be wrong**. They never block on their
own, they always show their confidence and reasons, operators can reject them, and we will measure
their accuracy on a test set before version 1.0 (targets: at least 90% for exchange wallets and
deposit addresses).

Fourth, **free data providers can change their terms or limits**, as two have done recently (a free sanctions API closed to new users; a free BSC data API was retired into a paid plan).
Every provider sits behind a replaceable adapter, every phase re-checks the facts it depends on, and
we have priced a fallback for the one source that matters most.

Finally, **licences**. The freeze-history service allows free internal use with attribution but not
resale or building a dataset from its answers. The design never stores its answers beyond a short
cache and keeps all results internal.

## 10. Decisions we need

1. **Approve Option C** and the thirteen-week plan.
2. **Approve the data reserve** in section 8, to be drawn only on measured need.
3. **Policy on direct exposure:** when a counterparty has itself dealt directly with a sanctioned or
   frozen wallet, should the default be *Review* (proposed) or *Block*?
4. **Policy on upstream exposure:** when money is traced to a sanctioned wallet two or three steps back,
   with at least 1,000 USDT on the path, *Review* (proposed) or *Block*?
5. **Confirm internal use only**, which keeps us within free data licences.

---

## Appendix A · Frequently asked questions

**Why not just buy Chainalysis?** We may, for a small number of high-value cases, and the design
allows it. For everyday screening of our own counterparties, the cost and the loss of control are
hard to justify when free official data and on-demand chain access cover most of what we need.

**How good can this be without our own nodes?** For what we check, the data is the same. Providers
serve the same blockchain records a node would; we verified their completeness against raw chain
data (for BSC, 20 of 20 randomly sampled transfers found, field for field). What we give up is
pre-computed whole-chain analysis, which we do not need for our scope.

**Why only TRON and BSC?** That is where our USDT moves. Adding a chain later is a contained piece of
work.

**What happens when a data source is down?** The check says *Incomplete* and names the source. It
never returns a clean result over missing data.

**Can the log be altered?** Every record carries a fingerprint that includes the previous record's
fingerprint. Changing or deleting any past record breaks the chain, and the verify command reports
exactly where.

**Will this satisfy a bank or an exchange?** It gives us evidence-backed answers and a decision trail,
which is what they ask for. Some counterparties may still ask for a report from a known commercial
provider; the vendor plug-in covers those cases.

**Who maintains it after version 1.0?** The same model: the owner directs, Claude Code implements,
specifications and tests in the repository keep it consistent.

## Appendix B · Glossary

| Term | Meaning |
|---|---|
| USDT | Tether's US-dollar stablecoin; TRC20 on TRON, BEP20 on BNB Smart Chain |
| Address / wallet | A blockchain account that holds and sends tokens |
| Counterparty | An address we send to or receive from |
| Sanctions list | The US Treasury (OFAC) list, which includes blockchain addresses |
| Freeze | The issuer blocking an address from moving its stablecoins (possible on TRON USDT, not on BSC USDT) |
| Hop | One transfer step between addresses |
| Source of funds | Where the money arriving at an address came from |
| Coverage | The share of incoming money the trace could attribute to a known kind of source |
| RPC provider / indexer | A company that serves blockchain data over the internet, so we do not run our own nodes |
