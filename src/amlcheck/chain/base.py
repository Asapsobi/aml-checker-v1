"""Transfer, History, HistorySource and ContractLookup (architecture §3).

Amounts are exact `Decimal` USDT. Their text form is canonical (`canonical_amount`) because the
amount is part of a transfer's identity in the cache (D-030): `1.5` from TronGrid and
`1.500000000000000000` from an 18-decimal log must store the same way.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from amlcheck.core.models import Chain


@dataclass(frozen=True)
class Transfer:
    chain: Chain
    tx_hash: str
    idx: int  # BSC: log index. TRON: occurrence of identical (sender, recipient, amount) in the tx
    block: int | None  # TronGrid gives none (VS-04)
    time: datetime
    sender: str
    recipient: str
    amount: Decimal


@dataclass(frozen=True)
class History:
    transfers: tuple[Transfer, ...]  # newest first, 0-value dropped (D-012)
    since: datetime
    until: datetime
    complete: bool  # False: the window held more than the limit; only the newest are here
    first_activity: datetime | None
    zero_value: int  # 0-value transfers dropped while reading


class HistorySource(Protocol):
    chain: Chain

    async def fetch(
        self,
        address: str,
        since: datetime,
        until: datetime | None,
        limit: int,
        *,
        first_activity: bool,
    ) -> History:
        """Newest `limit` non-zero USDT transfers of `address` in `[since, until]` (None = now).

        `complete` is False only when more exist in the window. Raises `SourceError`.
        """
        ...


class ContractLookup(Protocol):
    chain: Chain

    async def is_contract(self, address: str) -> bool: ...


def amount_from_units(units: int, decimals: int) -> Decimal:
    """Exact: integer token units → USDT, built from digits so no decimal context rounds it."""
    if units < 0:
        raise ValueError("negative amount")
    return Decimal((0, tuple(int(c) for c in str(units)), -decimals))


def canonical_amount(amount: Decimal) -> str:
    """Plain decimal text without exponent or trailing zeros: `1.5`, `100`, `0.000001`."""
    text = format(amount, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def newest_first(transfers: Iterable[Transfer]) -> tuple[Transfer, ...]:
    """Newest first, fully tie-broken so order never depends on input order (CLAUDE.md #6)."""
    return tuple(
        sorted(
            transfers,
            key=lambda t: (
                -t.time.timestamp(),
                -(t.block if t.block is not None else -1),
                t.tx_hash,
                -t.idx,
                t.sender,
                t.recipient,
                canonical_amount(t.amount),
            ),
        )
    )
