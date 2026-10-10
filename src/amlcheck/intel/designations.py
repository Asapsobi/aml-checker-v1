"""Designated entities: exchanges and services a sanctions list names as a whole (methodology §13.4,
D-100). Imports nothing from the project (architecture §2).

The lists we sync name these entities but publish few or none of their wallets: OFAC designated
Nobitex with no TRON address, the UK designated HTX with none. A wallet is theirs when its public
explorer tag names them (§13.3) or the operator labels it `sanctioned_entity`. Each entry cites its
list entries as read on 2026-10-10 (VS-23); a list that drops an entity drops it here, by hand,
with a decision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Designation:
    entity: str  # the name shown
    aliases: tuple[str, ...]  # whole words, any case, that name it in a public tag
    basis: tuple[str, ...]  # the list entries that designate it

    @property
    def text(self) -> str:
        """`HTX (UK sanctions RUS3619)`: the name with its first basis, for people."""
        return f"{self.entity} ({self.basis[0]})"


DESIGNATIONS: tuple[Designation, ...] = (
    Designation("HTX", ("HTX", "Huobi"), ("UK sanctions RUS3619",)),
    Designation("Garantex", ("Garantex",), ("OFAC SDN 36025", "UK sanctions RUS1421")),
    Designation("Grinex", ("Grinex",), ("OFAC SDN 55045", "UK sanctions RUS2983")),
    Designation("Cryptex", ("Cryptex",), ("OFAC SDN 50641",)),
    Designation("SUEX", ("SUEX",), ("OFAC SDN 33151",)),
    Designation("Chatex", ("Chatex",), ("OFAC SDN 33854",)),
    Designation("Bitpapa", ("Bitpapa",), ("OFAC SDN 48096",)),
    Designation("Xinbi Guarantee", ("Xinbi",), ("OFAC SDN 58361", "UK sanctions GHR0190")),
    Designation("Nobitex", ("Nobitex",), ("OFAC SDN 56981",)),
    Designation("Wallex", ("Wallex",), ("OFAC SDN 57091",)),
    Designation("Ramzinex", ("Ramzinex",), ("OFAC SDN 57092",)),
    Designation("Bitpin", ("Bitpin",), ("OFAC SDN 57090",)),
    Designation("Aban Tether", ("AbanTether", "Aban Tether"), ("OFAC SDN 58239",)),
    Designation("Zedcex", ("Zedcex",), ("OFAC SDN 56865",)),
    Designation("EXMO", ("EXMO",), ("UK sanctions RUS3602",)),
    Designation("ABCEX", ("ABCEX",), ("UK sanctions RUS3603",)),
    Designation("Rapira", ("Rapira",), ("UK sanctions RUS3605",)),
    Designation("Aifory", ("Aifory",), ("UK sanctions RUS3611",)),
    Designation("Tokenspot", ("Tokenspot",), ("UK sanctions RUS3758",)),
    Designation("Byex", ("Byex",), ("UK sanctions GHR0174",)),
)

_PATTERNS = tuple(
    (
        re.compile(
            r"(?<![A-Za-z0-9])(?:" + "|".join(map(re.escape, d.aliases)) + r")(?![A-Za-z0-9])",
            re.IGNORECASE,
        ),
        d,
    )
    for d in DESIGNATIONS
)


def designation_for(tag: str | None) -> Designation | None:
    """The designated entity a public tag names (`HTX 4` → HTX), or None. A whole word only:
    `Huobi-Hot 12` names HTX, `EXMOney` names no one. The first in list order wins a tie."""
    if not tag:
        return None
    return next((d for pattern, d in _PATTERNS if pattern.search(tag)), None)
