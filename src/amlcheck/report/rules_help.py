"""Plain-English help for every rule (methodology §2–§4, §7, §11.5, §13): what it means, when it
fires, what to look at. Shown on the web UI's Rules page; a test keeps it in step with
`core.rules.DEFAULT_SEVERITY`, so a new rule can't ship without its explanation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuleHelp:
    rule_id: str
    title: str
    means: str  # what the rule says about the address
    fires: str  # exactly when it fires
    check: str  # what the operator should look at


@dataclass(frozen=True)
class Group:
    name: str
    about: str
    rules: tuple[RuleHelp, ...]


GROUPS: tuple[Group, ...] = (
    Group(
        "Sanctions, designations and freezes",
        "Facts about the address itself: who lists it, who froze it, who it belongs to.",
        (
            RuleHelp(
                "R-SAN-01",
                "On a sanctions list",
                "The address itself is on an official sanctions list: OFAC SDN (US), the UK "
                "Sanctions List, the EU sanctions list, or Israel's NBCTF seizure orders.",
                "The address equals a crypto address in the latest snapshot of one of those lists.",
                "The list and entry named in the finding. Do not transact: this is a legal block.",
            ),
            RuleHelp(
                "R-SAN-02",
                "A designated entity's wallet",
                "The address belongs to an exchange or service a sanctions list designates as a "
                "whole (HTX by the UK, Nobitex, Wallex, Garantex… by OFAC), although the list "
                "doesn't name this wallet.",
                'Its public Tronscan tag names the entity ("HTX 4"); it is in the Nobitex hack '
                "index (inferred); or you or a label pack labelled it `sanctioned_entity`.",
                "Which entity and list entry; where the attribution comes from (tag, index or "
                "label). Whether your jurisdiction lets you deal with that entity.",
            ),
            RuleHelp(
                "R-LBL-01",
                "Labelled risky",
                "The address itself carries a risk label: scam, stolen funds, darknet, mixer, "
                "high-risk, gambling, no-KYC exchange or bridge.",
                "You or an imported label pack labelled the checked address with one of those "
                "categories (the most severe one is shown).",
                "Who labelled it and why: the note, and for a pack its name and licence.",
            ),
            RuleHelp(
                "R-FRZ-01",
                "Frozen by Tether",
                "Tether has blacklisted this address on its USDT contract: its USDT can't move.",
                "Tether's contract reports the address as blacklisted now, or our freeze index "
                "shows it was added (or its funds destroyed) and not released.",
                "When it was frozen. A frozen wallet is usually tied to fraud, hacks or "
                "sanctions; do not transact.",
            ),
            RuleHelp(
                "R-FRZ-02",
                "Frozen before, released",
                "Tether froze this address in the past and later released it.",
                "Our freeze index shows an add followed by a removal, with no later add.",
                "Why it was frozen and why released: a release is not a clearance.",
            ),
        ),
    ),
    Group(
        "Direct exposure",
        "Who the address dealt with directly, from its own USDT history.",
        (
            RuleHelp(
                "R-EXP-01",
                "Dealt with a sanctioned or frozen wallet",
                "The address sent USDT to, or received USDT from, an address that is on a "
                "sanctions list or frozen by Tether.",
                "Any of its direct counterparties is sanctioned or frozen (at most 10 shown, "
                "largest first).",
                "The counterparty table: amounts and dates both ways, and the 3 largest transfers.",
            ),
            RuleHelp(
                "R-EXP-02",
                "A large share came from flagged wallets",
                "A meaningful part of what the address received came straight from flagged "
                "wallets.",
                "5% or more of the USDT it received came from addresses that are sanctioned, "
                "frozen, or labelled mixer, bridge or high-risk "
                "(`[exposure] flagged_inflow_share`).",
                "Which flagged senders, and how much each.",
            ),
        ),
    ),
    Group(
        "Behaviour",
        "Patterns in the address's own transfers. They add behaviour points to the score "
        "(at most 30 in all); they are not proof of anything on their own.",
        (
            RuleHelp(
                "R-HEU-01",
                "New wallet",
                "The address is new: its first USDT activity was under 7 days ago, or it has none.",
                "First activity less than 7 days before the check (low priority).",
                "Is a brand-new wallet expected for this client?",
            ),
            RuleHelp(
                "R-HEU-02",
                "Pass-through",
                "Money comes in and leaves again almost at once: typical of exchange deposit "
                "addresses and payment hops, odd for a business that holds funds.",
                "90% or more of the USDT received left within 24 hours of arriving.",
                "Who it passes money on to, and whether that fits the client's business.",
            ),
            RuleHelp(
                "R-HEU-03",
                "Fan-in: many small senders",
                "Many different wallets each sent it a small amount in a short time: collection "
                "patterns (payouts, scams, structuring).",
                "More than 50 distinct senders, each under 100 USDT, within any 24 hours.",
                "Who the senders are; whether the address is a known service or merchant.",
            ),
            RuleHelp(
                "R-HEU-04",
                "Fan-out: many recipients",
                "It sent money to many different wallets in a short time: distribution patterns "
                "(payouts, splitting funds).",
                "More than 50 distinct recipients within any 24 hours.",
                "Where the money went; whether a payout business explains it.",
            ),
            RuleHelp(
                "R-HEU-05",
                "Dealt with a risky-labelled wallet",
                "A direct counterparty carries one of your risky labels (mixer, bridge or "
                "high-risk).",
                "A counterparty's label is in `[heuristics] risky_tags` (at most 10 shown).",
                "Which counterparty and label. These now also count as direct exposures.",
            ),
            RuleHelp(
                "R-HEU-06",
                "Look-alike address (address poisoning)",
                "The address looks like one you know (a past counterparty or your own wallet) but "
                "is a different address: a common trick to make you copy the wrong one.",
                "Same first 4 and last 4 characters as a known address on the same chain, "
                "different middle.",
                "Compare the full address character by character with the real one shown.",
            ),
            RuleHelp(
                "R-HEU-07",
                "Collector or distributor",
                "By its own transfers, the address behaves like a collector (gathers funds from "
                "many) or a distributor (spreads them out). An inference.",
                "The classifier's primary type is COLLECTOR or DISTRIBUTOR with confidence 0.7 or "
                "more (low priority; never BLOCK).",
                "The classification and the conditions that held.",
            ),
        ),
    ),
    Group(
        "Source of funds (trace)",
        "Where the money came from, followed back through other wallets (and where it went: "
        "outbound risk shows as exposures).",
        (
            RuleHelp(
                "R-TRC-01",
                "Money traces back to a sanctioned wallet",
                "Following the money back, a path reaches a sanctioned wallet two or more hops "
                "away.",
                "A path of 2 or more hops ends at a sanctioned wallet, and every hop on it moved "
                "at least 1,000 USDT (the bottleneck; `[trace] min_flagged_usdt`).",
                "The path: each hop moved at least the bottleneck amount.",
            ),
            RuleHelp(
                "R-TRC-02",
                "Money traces back to a frozen wallet",
                "As R-TRC-01, ending at a Tether-frozen wallet.",
                "A path of 2 or more hops ends at a frozen wallet, bottleneck 1,000 USDT or more.",
                "The path and the freeze.",
            ),
            RuleHelp(
                "R-TRC-03",
                "High-risk share of the money",
                "A noticeable share of the incoming money traces to high-risk categories "
                "(sanctioned, designated entities, frozen, stolen funds, darknet, mixers, scams, "
                "high-risk labels).",
                "5% or more of the traced inflow ends in those categories "
                "(`[trace] high_risk_share`).",
                "The trace breakdown: which categories, how much each.",
            ),
            RuleHelp(
                "R-TRC-04",
                "Low trace coverage",
                "Less than half of the incoming money could be traced to a known end; the rest "
                "stopped at limits (depth, time, small amounts).",
                "Coverage under 50% (`[trace] min_coverage`, low priority).",
                "How much is untraced and why. Low coverage is itself a reason to look closer.",
            ),
            RuleHelp(
                "R-TRC-05",
                "Suspicious patterns on the way",
                "A share of the money passed through wallets that look like collectors or "
                "layering steps. An inference.",
                "10% or more of the traced inflow went through inferred suspicious patterns "
                "(`[trace] inferred_share`, low priority, never BLOCK).",
                "The collectors and layering steps on the paths.",
            ),
        ),
    ),
    Group(
        "Score and system",
        "",
        (
            RuleHelp(
                "R-SCR-01",
                "Score at or above the review line",
                "The risk score (0–100, from all exposures and behaviour) is moderate or worse. "
                "This rule only turns the score into a REVIEW; it never blocks.",
                "Score 31 or more (`[score] review_at`; 0 turns the rule off).",
                "The lines under the score: which risk types make it up, direct or indirect.",
            ),
            RuleHelp(
                "R-SYS-01",
                "A required source failed",
                "A source the check needs could not answer, or its data is too old (a sanctions "
                "list over 48 hours old, a trace read that failed). The check is INCOMPLETE: "
                "never a clean result over a gap.",
                "A required source is in error or stale. Fixed at INCOMPLETE; can't be changed.",
                "Which source. Usually `amlcheck sync` (lists) or a retry (network) fixes it; "
                "until then treat the address as REVIEW.",
            ),
        ),
    ),
)

BY_ID: dict[str, RuleHelp] = {r.rule_id: r for g in GROUPS for r in g.rules}
