"""Profile features (methodology §5): a pure function over an address's cached transfers.

The window is the classifier's (90 days) or the trace's. The caller reads at most
`[trace] hub_transfers` transfers; when the window held more, the profile is `capped` (a hub,
cheaply detected).
Shares are `None` when their denominator is zero, so "no data" is never mistaken for 0%.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from amlcheck.chain.base import History, Transfer
from amlcheck.screening.heuristics import busiest_window, fifo_holds, pass_through

_DAY = timedelta(hours=24)
_HOUR = Decimal(3600)


@dataclass(frozen=True)
class Profile:
    address: str
    since: datetime
    until: datetime
    n_in: int
    n_out: int
    distinct_senders: int
    distinct_recipients: int
    volume_in: Decimal
    volume_out: Decimal
    retained_share: Decimal | None
    first_seen: datetime | None
    last_seen: datetime | None
    median_hold_hours: Decimal | None
    pass_through_share_24h: Decimal | None
    top_recipient: str | None
    top_recipient_share_out: Decimal | None
    top_sender_share_in: Decimal | None
    small_in_share: Decimal | None
    round_share: Decimal | None
    max_senders_24h: int
    max_recipients_24h: int
    capped: bool
    is_contract: bool

    @property
    def counterparties(self) -> int:
        return self.distinct_senders + self.distinct_recipients


def profile(
    address: str,
    history: History,
    *,
    is_contract: bool,
    small_usdt: Decimal,
    round_unit_usdt: Decimal,
) -> Profile:
    xs: Sequence[Transfer] = [
        t
        for t in history.transfers
        if t.sender != t.recipient
        and address in (t.sender, t.recipient)
        and history.since <= t.time <= history.until
    ]
    inbound = [t for t in xs if t.recipient == address]
    outbound = [t for t in xs if t.sender == address]
    volume_in = sum((t.amount for t in inbound), Decimal(0))
    volume_out = sum((t.amount for t in outbound), Decimal(0))
    by_sender: dict[str, Decimal] = defaultdict(Decimal)
    for t in inbound:
        by_sender[t.sender] += t.amount
    by_recipient: dict[str, Decimal] = defaultdict(Decimal)
    for t in outbound:
        by_recipient[t.recipient] += t.amount
    top_recipient = min(by_recipient, key=lambda a: (-by_recipient[a], a)) if by_recipient else None
    top_sender = min(by_sender, key=lambda a: (-by_sender[a], a)) if by_sender else None
    times = [t.time for t in xs]
    known_first = [*times, *([history.first_activity] if history.first_activity else [])]
    pt = pass_through(address, xs, _DAY)
    return Profile(
        address=address,
        since=history.since,
        until=history.until,
        n_in=len(inbound),
        n_out=len(outbound),
        distinct_senders=len(by_sender),
        distinct_recipients=len(by_recipient),
        volume_in=volume_in,
        volume_out=volume_out,
        retained_share=max(Decimal(0), volume_in - volume_out) / volume_in if volume_in else None,
        first_seen=min(known_first, default=None),
        last_seen=max(times, default=None),
        median_hold_hours=_weighted_median_hours(fifo_holds(address, xs)),
        pass_through_share_24h=pt.share,
        top_recipient=top_recipient,
        top_recipient_share_out=(
            by_recipient[top_recipient] / volume_out if top_recipient and volume_out else None
        ),
        top_sender_share_in=(
            by_sender[top_sender] / volume_in if top_sender and volume_in else None
        ),
        small_in_share=(
            Decimal(sum(1 for t in inbound if t.amount < small_usdt)) / len(inbound)
            if inbound
            else None
        ),
        round_share=(
            Decimal(sum(1 for t in xs if t.amount % round_unit_usdt == 0)) / len(xs) if xs else None
        ),
        max_senders_24h=busiest_window([(t.time, t.sender) for t in inbound], _DAY)[0],
        max_recipients_24h=busiest_window([(t.time, t.recipient) for t in outbound], _DAY)[0],
        capped=not history.complete,
        is_contract=is_contract,
    )


def _weighted_median_hours(holds: list[tuple[timedelta, Decimal]]) -> Decimal | None:
    """Amount-weighted median of hold times: the hold at which half the spent USDT is reached."""
    total = sum((amount for _, amount in holds), Decimal(0))
    if not total:
        return None
    acc = Decimal(0)
    for hold, amount in sorted(holds, key=lambda h: h[0]):
        acc += amount
        if acc * 2 >= total:
            return Decimal(str(hold.total_seconds())) / _HOUR
    return None  # pragma: no cover - acc reaches total
