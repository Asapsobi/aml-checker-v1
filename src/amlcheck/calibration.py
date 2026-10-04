"""Golden-set calibration (methodology §10, D-066, D-067).

A golden entry is an address with what it is known to be (`expect`), where that knowledge comes from
(`source`) and, once recorded live, what amlcheck saw and said (`recorded`): the classifier's inputs
(profile and context) and the outcome (types, verdict, score, band).

- `replay` re-runs the current classifier on the recorded inputs, offline: threshold changes are
  measured in CI without the network.
- `measure` gives precision per type (of the addresses predicted T whose truth is known, the share
  that are T), verdict agreement, and the clean addresses scored high or severe.
- Targets (P11 exit criteria): HUB and DEPOSIT precision ≥ 0.9, COLLECTOR ≥ 0.8, no clean address
  high or severe.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from amlcheck.config import Classifier
from amlcheck.core.clock import from_iso
from amlcheck.profile import classifier as clf
from amlcheck.profile.classifier import ClassifyContext

TARGETS: dict[str, Decimal] = {
    "HUB": Decimal("0.9"),
    "DEPOSIT": Decimal("0.9"),
    "COLLECTOR": Decimal("0.8"),
}
NOT_CLEAN_BANDS = frozenset({"high", "severe"})


@dataclass(frozen=True)
class Expect:
    verdict: str | None = None  # BLOCK, REVIEW, …: when the verdict is known
    type: str | None = None  # HUB, DEPOSIT, COLLECTOR, …: when what it is is known
    clean: bool = False  # known clean: must not score high or severe


@dataclass(frozen=True)
class Recorded:
    at: str  # when it was recorded (classification time)
    classifier_version: int
    profile: dict[str, Any]
    context: dict[str, bool]
    types: list[str]
    verdict: str
    score: int | None
    band: str | None
    lower_bound: bool
    traced: bool
    check_id: str


@dataclass(frozen=True)
class Golden:
    chain: str
    address: str
    expect: Expect
    source: str
    note: str | None = None
    recorded: Recorded | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.chain, self.address)


def load(path: Path) -> list[Golden]:
    if not path.exists():
        return []
    out = []
    for d in json.loads(path.read_text(encoding="utf-8")):
        rec = d.get("recorded")
        out.append(
            Golden(
                chain=d["chain"],
                address=d["address"],
                expect=Expect(**d["expect"]),
                source=d["source"],
                note=d.get("note"),
                recorded=Recorded(**rec) if rec else None,
            )
        )
    return out


def dump(entries: Iterable[Golden], path: Path) -> None:
    """Sorted by chain and address, so the file diffs cleanly."""
    rows = [asdict(g) for g in sorted(entries, key=lambda g: g.key)]
    path.write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def replay(g: Golden, settings: Classifier) -> list[str]:
    """The types the current classifier gives the recorded inputs (no network)."""
    if g.recorded is None:
        return []
    p = clf.profile_from_json(g.recorded.profile)
    ctx = ClassifyContext(**g.recorded.context)
    return [c.type for c in clf.classify(p, ctx, settings, from_iso(g.recorded.at))]


@dataclass
class TypeStats:
    predicted: int = 0  # predicted this type, truth known
    correct: int = 0
    missed: list[str] = field(default_factory=list)  # truth = type, not predicted
    wrong: list[str] = field(default_factory=list)  # predicted, truth is something else

    @property
    def precision(self) -> Decimal | None:
        if not self.predicted:
            return None
        return (Decimal(self.correct) / Decimal(self.predicted)).quantize(Decimal("0.001"))


@dataclass
class Measure:
    entries: int
    recorded: int
    types: dict[str, TypeStats]
    verdict_agree: int
    verdict_known: int
    verdict_misses: list[str]
    clean: int
    clean_too_high: list[str]

    def passes(self) -> bool:
        for t, target in TARGETS.items():
            p = self.types.get(t, TypeStats()).precision
            if p is not None and p < target:
                return False
        return not self.clean_too_high and not self.verdict_misses


def measure(entries: Sequence[Golden], settings: Classifier) -> Measure:
    types: dict[str, TypeStats] = {}
    agree = known = clean = 0
    misses: list[str] = []
    too_high: list[str] = []
    recorded = [g for g in entries if g.recorded is not None]
    for g in recorded:
        assert g.recorded is not None  # noqa: S101 - filtered above
        predicted = set(replay(g, settings))
        truth = g.expect.type
        if truth is not None:
            for t in predicted - {clf.FRESH}:
                s = types.setdefault(t, TypeStats())
                s.predicted += 1
                if t == truth:
                    s.correct += 1
                else:
                    s.wrong.append(f"{g.chain} {g.address} (is {truth})")
            if truth not in predicted:
                types.setdefault(truth, TypeStats()).missed.append(f"{g.chain} {g.address}")
        if g.expect.verdict is not None:
            known += 1
            if g.recorded.verdict == g.expect.verdict:
                agree += 1
            else:
                misses.append(
                    f"{g.chain} {g.address}: {g.recorded.verdict}, expected {g.expect.verdict}"
                )
        if g.expect.clean:
            clean += 1
            if g.recorded.band in NOT_CLEAN_BANDS:
                too_high.append(f"{g.chain} {g.address}: {g.recorded.score} · {g.recorded.band}")
    return Measure(
        entries=len(entries),
        recorded=len(recorded),
        types=dict(sorted(types.items())),
        verdict_agree=agree,
        verdict_known=known,
        verdict_misses=misses,
        clean=clean,
        clean_too_high=too_high,
    )


def markdown(m: Measure) -> str:
    lines = [
        f"Golden set: {m.entries} addresses, {m.recorded} recorded.",
        "",
        "| Type | Predicted (truth known) | Correct | Precision | Target | Missed |",
        "|---|---|---|---|---|---|",
    ]
    for t, s in m.types.items():
        target = TARGETS.get(t)
        p = s.precision
        lines.append(
            f"| {t} | {s.predicted} | {s.correct} | {p if p is not None else '-'} | "
            f"{f'≥ {target}' if target else '-'} | {len(s.missed)} |"
        )
    lines += [
        "",
        f"Verdicts: {m.verdict_agree} of {m.verdict_known} as expected.",
        f"Clean addresses: {m.clean}, scored high or severe: {len(m.clean_too_high)}.",
        "",
        f"**Targets {'met' if m.passes() else 'NOT met'}.**",
    ]
    details = [("Wrong type", [x for s in m.types.values() for x in s.wrong])]
    details += [("Verdict not as expected", m.verdict_misses), ("Clean but high", m.clean_too_high)]
    for title, items in details:
        if items:
            lines += ["", f"{title}:", *[f"- {x}" for x in items]]
    return "\n".join(lines) + "\n"
