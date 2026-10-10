"""The benchmark against MistTrack (methodology §14, D-093, D-092).

Each wallet keeps what MistTrack Light showed, its level (`low` or `risky`) and its risk rows, which
checks never read; and our side, a live, traced check with every exposure. So `measure` can re-score
offline, with today's settings or candidate `k`, `decay` or weights. `changes` says what a candidate
would fix and break, and `adoptable` applies D-092: fix at least 2 mismatches, break none.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal, cast

from amlcheck.config import Score as ScoreSettings
from amlcheck.core.address import AddressError, detect
from amlcheck.core.models import Chain, Verdict
from amlcheck.core.risk import Exposure, detail_list, ranked
from amlcheck.core.score import score_of

LEVELS = ("low", "moderate", "high", "severe")  # ours (§11.4)
THEIR_LEVELS = ("low", "risky")  # MistTrack Light's
#: MistTrack Light's risk types, and ours that answer each (§14.2).
TYPES: dict[str, frozenset[str]] = {
    "Sanctioned Entity": frozenset({"sanctioned_entity"}),
    "Illicit Activity": frozenset({"illicit_activity", "frozen"}),
    "Risky Exchange": frozenset({"risk_exchange"}),
    "Mixer": frozenset({"mixer"}),
    "Gambling": frozenset({"gambling"}),
    "Bridge": frozenset({"bridge"}),
}
OWN_RULES = frozenset({"R-SAN-01", "R-SAN-02", "R-FRZ-01"})  # R-SAN-02: §13.4
GAP_SHARE = Decimal(5)  # % of volume (D-093)
SAME_TARGET = Decimal("0.8")  # D-093
FIX_AT_LEAST = 2  # D-092
type Cause = Literal["unknowable", "our_miss", "method", "disputed"]
#: Why a wallet disagrees or has a gap (§14.2).
CAUSES: dict[str, str] = {
    "unknowable": "MistTrack holds information we can't have (a private label)",
    "our_miss": "something amlcheck could see and doesn't",
    "method": "the two measure differently by design",
    "disputed": "the evidence doesn't support MistTrack's view",
}


@dataclass(frozen=True)
class Recorded:
    """Our side (§14.1): a live, traced check, read back from its audit record."""

    at: str
    version: str
    config_hash: str
    check_id: str
    verdict: str
    rules: tuple[str, ...]  # the findings' rule IDs: behaviour points and BLOCK come from these
    score: int
    level: str
    k: str
    decay: str
    exposures: tuple[dict[str, Any], ...]  # every one, as the check JSON has them
    coverage_in: str | None
    coverage_out: str | None

    @classmethod
    def from_check(cls, data: Mapping[str, Any], version: str, config_hash: str) -> Recorded:
        """From a check's JSON (contract 2, `report.check_json`)."""
        score = data["score"]
        if score is None or score.get("score_version") != 2:
            raise ValueError(f"check {data['check_id']} has no version 2 score")
        trace = next((s for s in data["sources"] if s["source"] == "trace"), None)
        ev = (trace or {}).get("evidence") or {}
        return cls(
            at=data["checked_at"],
            version=version,
            config_hash=config_hash,
            check_id=data["check_id"],
            verdict=data["verdict"],
            rules=tuple(sorted({f["rule_id"] for f in data["findings"]})),
            score=score["score"],
            level=score["level"],
            k=score["k"],
            decay=score["decay"],
            exposures=tuple(data["exposures"]),
            coverage_in=ev.get("coverage"),
            coverage_out=(ev.get("out") or {}).get("coverage"),
        )

    def scored(
        self,
        k: Decimal | None = None,
        decay: Decimal | None = None,
        weights: Mapping[str, Decimal] | None = None,
    ) -> tuple[int, str]:
        """The score and level again, offline: the recorded settings unless others are given."""
        settings = ScoreSettings(
            k=k if k is not None else Decimal(self.k),
            decay=decay if decay is not None else Decimal(self.decay),
        )
        s = score_of(
            Verdict(self.verdict),
            self.rules,
            [Exposure.from_json(e) for e in self.exposures],
            settings,
            weights=weights,
        )
        return s.score, s.level

    def risks(self) -> list[str]:
        """The check's detail list (§11.5), heaviest risk type first."""
        exposures = [Exposure.from_json(e) for e in self.exposures]
        return detail_list(ranked(exposures, Decimal(self.decay)))

    @property
    def own(self) -> bool:
        """The address itself is listed, a designated entity's or frozen (R-SAN-01, R-SAN-02,
        R-FRZ-01)."""
        return bool(OWN_RULES & set(self.rules))

    def found(self) -> frozenset[str]:
        """Every risk type the check found: its exposures' and the address's own (§14.2)."""
        types = {str(e["risk_type"]) for e in self.exposures}
        if "R-SAN-01" in self.rules:
            types.add("sanctioned_entity")
        if "R-FRZ-01" in self.rules:
            types.add("frozen")
        return frozenset(types)


@dataclass(frozen=True)
class TheirRisk:
    """One row of MistTrack Light's risk table."""

    risk_type: str  # MistTrack's name: a key of TYPES
    exposure: str  # direct or indirect
    share: Decimal  # % of volume, as shown

    @property
    def own(self) -> bool:
        """A direct row at 100%: MistTrack flags the wallet itself."""
        return self.exposure == "direct" and self.share == 100

    def text(self) -> str:
        return f"{self.risk_type} {self.exposure} {self.share}%"


def parse_risks(text: str) -> tuple[TheirRisk, ...]:
    """`Illicit Activity/direct/52.07; Sanctioned Entity/indirect/15.91`; empty for none."""
    out = []
    for part in (p.strip() for p in text.split(";")):
        if not part:
            continue
        fields = [x.strip() for x in part.split("/")]
        if len(fields) != 3:
            raise ValueError(f"risk {part!r}: write it as type/direct or indirect/share")
        kind, exposure, share = fields
        if kind not in TYPES:
            raise ValueError(f"unknown MistTrack risk type {kind!r}")
        if exposure not in ("direct", "indirect"):
            raise ValueError(f"risk {part!r}: exposure must be direct or indirect")
        try:
            pct = Decimal(share)
        except InvalidOperation:
            raise ValueError(f"risk {part!r}: the share must be a number") from None
        if not 0 <= pct <= 100:
            raise ValueError(f"risk {part!r}: the share must be 0 to 100")
        out.append(TheirRisk(kind, exposure, pct))
    return tuple(out)


@dataclass(frozen=True)
class Wallet:
    chain: str
    address: str
    theirs: str  # MistTrack Light's level (D-093)
    source: str  # where and when it was looked up
    risks: tuple[TheirRisk, ...] = ()  # MistTrack Light's rows; checks never read them
    note: str | None = None
    cause: Cause | None = None  # why it disagrees or has a gap, once looked into
    reason: str | None = None
    recorded: Recorded | None = None

    def __post_init__(self) -> None:
        if self.theirs not in THEIR_LEVELS:
            raise ValueError(f"{self.address}: level must be one of {', '.join(THEIR_LEVELS)}")
        if self.cause is not None and self.cause not in CAUSES:
            raise ValueError(f"{self.address}: cause must be one of {', '.join(CAUSES)}")

    @property
    def key(self) -> tuple[str, str]:
        return (self.chain, self.address)


def load(path: Path) -> list[Wallet]:
    if not path.exists():
        return []
    out = []
    for d in json.loads(path.read_text(encoding="utf-8"))["wallets"]:
        r = d.get("recorded")
        out.append(
            Wallet(
                d["chain"],
                d["address"],
                d["theirs"],
                d["source"],
                tuple(
                    TheirRisk(x["risk_type"], x["exposure"], Decimal(x["share"]))
                    for x in d.get("risks", ())
                ),
                d.get("note"),
                d.get("cause"),
                d.get("reason"),
                Recorded(**{**r, "rules": tuple(r["rules"]), "exposures": tuple(r["exposures"])})
                if r
                else None,
            )
        )
    return out


def dump(wallets: Iterable[Wallet], path: Path) -> None:
    """Sorted, indented and stable, so a diff shows what changed."""
    rows = [asdict(w) for w in sorted(wallets, key=lambda w: w.key)]
    text = json.dumps(
        {"benchmark_version": 2, "wallets": rows}, indent=1, sort_keys=True, default=str
    )
    path.write_text(text + "\n", encoding="utf-8")


#: Columns of a lookups file; any other is ignored.
KEPT = ("address", "chain", "level", "risks", "note")


def read_lookups(path: Path, date: str) -> tuple[list[Wallet], list[str]]:
    """MistTrack Light lookups, `address,chain,level,risks,note` (`risks` as `parse_risks` reads
    them): the wallets, and the columns ignored. `chain` may be empty when the address says it."""
    out = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = [h.strip().lower() for h in reader.fieldnames or ()]
        if "address" not in fields or "level" not in fields:
            raise ValueError(f"{path.name}: needs the columns address and level")
        reader.fieldnames = fields
        for line, row in enumerate(reader, start=2):
            chain = (row.get("chain") or "").strip().lower() or None
            try:
                a = detect((row["address"] or "").strip(), Chain(chain) if chain else None)
                out.append(
                    Wallet(
                        a.chain.value,
                        a.norm,
                        (row["level"] or "").strip().lower(),
                        f"MistTrack Light, {date}",
                        parse_risks(row.get("risks") or ""),
                        (row.get("note") or "").strip() or None,
                    )
                )
            except (AddressError, ValueError) as e:
                raise ValueError(f"{path.name} line {line}: {e}") from None
    return out, sorted(set(fields) - set(KEPT))


def merge(old: Iterable[Wallet], new: Iterable[Wallet]) -> list[Wallet]:
    """A new lookup replaces the old one for the same wallet and keeps its record. A changed lookup
    drops the cause and reason, which explained a different comparison; the same one keeps them."""
    by = {w.key: w for w in old}
    for w in new:
        prev = by.get(w.key)
        if prev is not None and w.recorded is None:
            kept = (prev.theirs, prev.risks) == (w.theirs, w.risks)
            w = Wallet(
                w.chain,
                w.address,
                w.theirs,
                w.source,
                w.risks,
                w.note or prev.note,
                prev.cause if kept else None,
                prev.reason if kept else None,
                prev.recorded,
            )
        by[w.key] = w
    return sorted(by.values(), key=lambda w: w.key)


@dataclass(frozen=True)
class Row:
    wallet: Wallet
    score: int
    level: str  # ours

    @property
    def rec(self) -> Recorded:
        assert self.wallet.recorded is not None  # noqa: S101 - measure keeps recorded wallets
        return self.wallet.recorded

    @property
    def ours(self) -> str:
        """Ours on MistTrack Light's scale: low, or risky for moderate and above."""
        return "low" if self.level == "low" else "risky"

    @property
    def same(self) -> bool:
        return self.ours == self.wallet.theirs

    @property
    def gaps(self) -> list[TheirRisk]:
        """MistTrack rows at ≥ 5% whose risk type the check finds nowhere (§14.2)."""
        found = self.rec.found()
        return [
            r
            for r in self.wallet.risks
            if r.share >= GAP_SHARE
            and not (r.own and self.rec.own)
            and not TYPES[r.risk_type] & found
        ]

    @property
    def extras(self) -> list[str]:
        """Risk types the check finds that MistTrack doesn't show."""
        shown = frozenset().union(*(TYPES[r.risk_type] for r in self.wallet.risks))
        return sorted(self.rec.found() - shown)

    @property
    def lower_bound(self) -> bool:
        """An INCOMPLETE check: its score is at least this (§11.3)."""
        return self.rec.verdict == Verdict.INCOMPLETE.value


@dataclass(frozen=True)
class Agreement:
    rows: tuple[Row, ...]
    k: Decimal | None = None
    decay: Decimal | None = None
    weights: Mapping[str, Decimal] = field(default_factory=dict)

    @property
    def same(self) -> int:
        return sum(r.same for r in self.rows)

    @property
    def share(self) -> Decimal:
        return Decimal(self.same) / len(self.rows) if self.rows else Decimal(0)

    @property
    def unexplained(self) -> list[Row]:
        """A mismatch or a gap with no reason yet (D-093)."""
        return [r for r in self.rows if (not r.same or r.gaps) and r.wallet.cause is None]

    @property
    def passed(self) -> bool:
        """AT-70 (D-093)."""
        return bool(self.rows) and self.share >= SAME_TARGET and not self.unexplained


def measure(
    wallets: Iterable[Wallet],
    *,
    k: Decimal | None = None,
    decay: Decimal | None = None,
    weights: Mapping[str, Decimal] | None = None,
) -> Agreement:
    """Every recorded wallet, re-scored offline: with the recorded settings unless others are
    given."""
    rows = []
    for w in sorted(wallets, key=lambda w: w.key):
        if w.recorded is None:
            continue
        score, level = w.recorded.scored(k, decay, weights)
        rows.append(Row(w, score, level))
    return Agreement(tuple(rows), k, decay, dict(weights or {}))


def unreproduced(wallets: Iterable[Wallet]) -> list[Wallet]:
    """Records whose own settings don't give back their score: a record to look into (§14.3)."""
    return [
        w for w in wallets if w.recorded is not None and w.recorded.scored()[0] != w.recorded.score
    ]


def changes(current: Agreement, candidate: Agreement) -> tuple[list[Row], list[Row]]:
    """The wallets a candidate brings to the same level (fixed) and takes away from it (broken)."""
    now = {r.wallet.key: r for r in current.rows}
    fixed = [r for r in candidate.rows if r.same and not now[r.wallet.key].same]
    broken = [r for r in candidate.rows if not r.same and now[r.wallet.key].same]
    return fixed, broken


def adoptable(fixed: Sequence[Row], broken: Sequence[Row]) -> bool:
    """D-092: a change fixes at least 2 mismatches and breaks none."""
    return len(fixed) >= FIX_AT_LEAST and not broken


def short(address: str) -> str:
    return f"{address[:8]}…{address[-6:]}"


def _settings(a: Agreement) -> str:
    parts = []
    if a.k is not None:
        parts.append(f"k = {a.k}")
    if a.decay is not None:
        parts.append(f"decay = {a.decay}")
    parts += [f"{c} = {w}" for c, w in sorted(a.weights.items())]
    return ", ".join(parts) or "the recorded settings"


def _summary(a: Agreement) -> str:
    n = len(a.rows)
    pct = (a.share * 100).quantize(Decimal(1))
    gapped = sum(bool(r.gaps) for r in a.rows)
    return (
        f"{n} wallet(s): same level {a.same} ({pct}%), {n - a.same} mismatched, {gapped} with gaps."
    )


def markdown(current: Agreement, candidate: Agreement | None = None) -> str:
    lines = [
        "# amlcheck — Benchmark against MistTrack",
        "",
        "> P15, methodology §14, D-093, D-092. Generated from `tests/benchmark/benchmark.json`",
        "> with `uv run python scripts/benchmark.py report --write`. MistTrack's side is",
        "> MistTrack Light, the free wallet risk assessment, looked up by Claude: its level",
        "> (`low` or `risky`) and its risk rows. Checks never read them.",
        "",
        "## Agreement",
        "",
    ]
    if not current.rows:
        return "\n".join([*lines, "No wallet recorded yet (Q-37).", ""])
    lines += [
        _summary(current),
        "",
        f"**AT-70: {'passed' if current.passed else 'not passed'}** (≥ 80% same level, every "
        "mismatch and gap explained; D-093).",
        "",
        "| MistTrack ↓ · amlcheck → | " + " | ".join(LEVELS) + " |",
        "|---|" + "---|" * len(LEVELS),
    ]
    for theirs in THEIR_LEVELS:
        counts = [
            str(sum(r.wallet.theirs == theirs and r.level == ours for r in current.rows))
            for ours in LEVELS
        ]
        lines.append(f"| {theirs} | " + " | ".join(counts) + " |")
    lines += [
        "",
        "## Wallets",
        "",
        "| Wallet | MistTrack | amlcheck | Same | MistTrack's risks (≥ 5%) | Ours | Gaps "
        "| Reason |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in current.rows:
        rec = r.rec
        ours = f"{'≥ ' if r.lower_bound else ''}{r.score} · {r.level} ({rec.verdict})"
        theirs = "; ".join(x.text() for x in r.wallet.risks if x.share >= GAP_SHARE) or "—"
        mine = "; ".join(rec.risks()[:3]) or "—"
        gaps = "; ".join(x.text() for x in r.gaps) or "—"
        explain = not r.same or r.gaps
        why = (
            f"{r.wallet.cause}: {r.wallet.reason}"
            if r.wallet.cause
            else ("**to explain**" if explain else "—")
        )
        lines.append(
            f"| {r.wallet.chain.upper()} `{short(r.wallet.address)}` | {r.wallet.theirs} | {ours} "
            f"| {'yes' if r.same else '**no**'} | {theirs} | {mine} | {gaps} | {why} |"
        )
    if candidate is not None:
        fixed, broken = changes(current, candidate)
        verdict = "yes" if adoptable(fixed, broken) else "no"
        lines += [
            "",
            f"## Candidate: {_settings(candidate)}",
            "",
            _summary(candidate),
            "",
            f"Fixed: {', '.join(short(r.wallet.address) for r in fixed) or 'none'}. "
            f"Broken: {', '.join(short(r.wallet.address) for r in broken) or 'none'}. "
            f"**Adoptable under D-092: {verdict}** (fixes ≥ {FIX_AT_LEAST}, breaks none).",
        ]
    return "\n".join([*lines, ""])


def cause_of(text: str) -> Cause:
    if text not in CAUSES:
        raise ValueError(f"cause must be one of {', '.join(CAUSES)}")
    return cast(Cause, text)
