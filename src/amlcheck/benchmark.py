"""The benchmark against MistTrack (methodology §14, D-091, D-092).

Each wallet keeps the owner's MistTrack level and nothing else of MistTrack's, and our side: a
live, traced check with every exposure. So `measure` can re-score offline, with today's settings or
candidate `k`, `decay` or weights. `changes` says what a candidate would fix and break, and
`adoptable` applies D-092: fix at least 2 mismatches, break none.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast

from amlcheck.config import Score as ScoreSettings
from amlcheck.core.address import AddressError, detect
from amlcheck.core.models import Chain, Verdict
from amlcheck.core.risk import Exposure, detail_list, ranked
from amlcheck.core.score import score_of

LEVELS = ("low", "moderate", "high", "severe")
type Cause = Literal["unknowable", "gap", "disputed"]
#: Why a wallet disagrees (§14.2). Only `unknowable` excuses a missed wallet (D-091).
CAUSES: dict[str, str] = {
    "unknowable": "MistTrack holds information we can't have (a private label)",
    "gap": "something amlcheck could see and doesn't",
    "disputed": "the evidence doesn't support MistTrack's level",
}
SAME_TARGET = Decimal("0.7")  # D-091
FIX_AT_LEAST = 2  # D-092


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


@dataclass(frozen=True)
class Wallet:
    chain: str
    address: str
    expected: str  # the owner's MistTrack level (D-091)
    source: str  # who looked it up, where, when
    note: str | None = None
    cause: Cause | None = None  # why it disagrees, once looked into
    reason: str | None = None
    recorded: Recorded | None = None

    def __post_init__(self) -> None:
        if self.expected not in LEVELS:
            raise ValueError(f"{self.address}: level must be one of {', '.join(LEVELS)}")
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
                d["expected"],
                d["source"],
                d.get("note"),
                d.get("cause"),
                d.get("reason"),
                Recorded(
                    **{
                        **r,
                        "rules": tuple(r["rules"]),
                        "exposures": tuple(r["exposures"]),
                    }
                )
                if r
                else None,
            )
        )
    return out


def dump(wallets: Iterable[Wallet], path: Path) -> None:
    """Sorted, indented and stable, so a diff shows what changed."""
    rows = [asdict(w) for w in sorted(wallets, key=lambda w: w.key)]
    text = json.dumps({"benchmark_version": 1, "wallets": rows}, indent=1, sort_keys=True)
    path.write_text(text + "\n", encoding="utf-8")


#: Columns kept from the owner's file; any other (a MistTrack score, its risk types) is ignored.
KEPT = ("address", "chain", "level", "note")


def read_levels(path: Path, date: str) -> tuple[list[Wallet], list[str]]:
    """The owner's file, `address,chain,level[,note]`: the wallets, and the columns ignored
    (D-091 keeps only the level). `chain` may be empty when the address says it."""
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
                        f"owner, MistTrack, {date}",
                        (row.get("note") or "").strip() or None,
                    )
                )
            except (AddressError, ValueError) as e:
                raise ValueError(f"{path.name} line {line}: {e}") from None
    return out, sorted(set(fields) - set(KEPT))


def merge(old: Iterable[Wallet], new: Iterable[Wallet]) -> list[Wallet]:
    """A new lookup replaces the old one for the same wallet and keeps its record. A changed level
    drops the cause and reason, which explained a different comparison; the same level keeps
    them."""
    by = {w.key: w for w in old}
    for w in new:
        prev = by.get(w.key)
        if prev is not None and w.recorded is None:
            kept = prev.expected == w.expected
            w = Wallet(
                w.chain,
                w.address,
                w.expected,
                w.source,
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
    level: str

    @property
    def apart(self) -> int:
        """Levels between ours and MistTrack's: + when ours is higher."""
        return LEVELS.index(self.level) - LEVELS.index(self.wallet.expected)

    @property
    def same(self) -> bool:
        return self.apart == 0

    @property
    def missed(self) -> bool:
        return self.wallet.expected in ("high", "severe") and self.level == "low"

    @property
    def over(self) -> bool:
        return self.wallet.expected == "low" and self.level in ("high", "severe")

    @property
    def lower_bound(self) -> bool:
        """An INCOMPLETE check: its score is at least this (§11.3)."""
        r = self.wallet.recorded
        return r is not None and r.verdict == Verdict.INCOMPLETE.value


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
    def far(self) -> list[Row]:
        """More than one level apart."""
        return [r for r in self.rows if abs(r.apart) > 1]

    @property
    def unexcused(self) -> list[Row]:
        """Missed, with no reason that is information we can't have (D-091)."""
        return [r for r in self.rows if r.missed and r.wallet.cause != "unknowable"]

    @property
    def passed(self) -> bool:
        """AT-70 (D-091)."""
        return bool(self.rows) and self.share >= SAME_TARGET and not self.far and not self.unexcused


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
    within = n - len(a.far)
    missed = sum(r.missed for r in a.rows)
    over = sum(r.over for r in a.rows)
    return (
        f"{n} wallet(s): same level {a.same} ({pct}%), within one level {within}, "
        f"missed {missed}, over {over}."
    )


def markdown(current: Agreement, candidate: Agreement | None = None) -> str:
    lines = [
        "# amlcheck — Benchmark against MistTrack",
        "",
        "> P15, methodology §14, D-091, D-092. Generated from `tests/benchmark/benchmark.json`",
        "> with `uv run python scripts/benchmark.py report --write`. Only the owner's MistTrack",
        "> level is kept per wallet; nothing else of MistTrack's.",
        "",
        "## Agreement",
        "",
    ]
    if not current.rows:
        return "\n".join([*lines, "No wallet recorded yet (Q-37).", ""])
    lines += [
        _summary(current),
        "",
        f"**AT-70: {'passed' if current.passed else 'not passed'}** (≥ 70% same level, every "
        "wallet within one level, none missed without a reason we can't have; D-091).",
        "",
        "| MistTrack ↓ · amlcheck → | " + " | ".join(LEVELS) + " |",
        "|---|" + "---|" * len(LEVELS),
    ]
    for exp in LEVELS:
        counts = [
            str(sum(r.wallet.expected == exp and r.level == ours for r in current.rows))
            for ours in LEVELS
        ]
        lines.append(f"| {exp} | " + " | ".join(counts) + " |")
    lines += [
        "",
        "## Wallets",
        "",
        "| Wallet | MistTrack | amlcheck | Apart | Risk | Coverage in / out | Reason |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in current.rows:
        rec = r.wallet.recorded
        assert rec is not None  # noqa: S101 - measure keeps recorded wallets only
        ours = f"{'≥ ' if r.lower_bound else ''}{r.score} · {r.level} ({rec.verdict})"
        apart = "=" if r.same else f"{r.apart:+d}"
        risk = "; ".join(rec.risks()[:2]) or "—"
        cov = f"{_pct(rec.coverage_in)} / {_pct(rec.coverage_out)}"
        why = (
            f"{r.wallet.cause}: {r.wallet.reason}"
            if r.wallet.cause
            else ("—" if r.same else "**to explain**")
        )
        lines.append(
            f"| {r.wallet.chain.upper()} `{short(r.wallet.address)}` | {r.wallet.expected} "
            f"| {ours} | {apart} | {risk} | {cov} | {why} |"
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


def _pct(share: str | None) -> str:
    if share is None:
        return "–"
    return f"{(Decimal(share) * 100).quantize(Decimal('0.1'))}%"


def cause_of(text: str) -> Cause:
    if text not in CAUSES:
        raise ValueError(f"cause must be one of {', '.join(CAUSES)}")
    return cast(Cause, text)
