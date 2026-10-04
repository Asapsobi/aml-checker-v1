"""Profiler: profile + classify an address with context, link deposits, and the R-HEU-07 source.

- The profile window is `[classifier] window_days` (90), read through the transfer cache with at
  most `[trace] hub_transfers` (1,000) transfers; more makes the profile `capped` (methodology §5).
- DEPOSIT needs to know whether the top recipient is a hub: its stored classification is reused
  while unexpired, otherwise it is profiled too (one capped read).
- A DEPOSIT joins its top recipient's entity; a hub without one gets `hub-<first 8>` of kind
  `unknown` (methodology §6, PRD F8.3). An operator's membership is never overwritten.
- R-HEU-07: the target's primary type is COLLECTOR or DISTRIBUTOR with confidence ≥ 0.7 → REVIEW,
  low priority, never BLOCK (D-017). An operator, import or list label on the target wins (F8.4).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from amlcheck.chain.cache import ContractCache, TransferCache
from amlcheck.config import Classifier, Heuristics, Trace
from amlcheck.core.clock import Clock, utcnow
from amlcheck.core.models import Address, Chain, Finding, SourceResult, SourceStatus
from amlcheck.core.rules import finding
from amlcheck.intel.store import IntelStore
from amlcheck.profile import classifier as clf
from amlcheck.profile.classifier import Classification, ClassifyContext
from amlcheck.profile.features import Profile, profile
from amlcheck.screening.base import SourceHealth

SOURCE = "classifier"
LABEL = "Address classifier"
#: Labelled categories that count as "exchange or service" for DEPOSIT (methodology §6).
SERVICE_CATEGORIES = frozenset(
    {
        "exchange_regulated",
        "exchange_nokyc",
        "payment_processor",
        "otc_desk",
        "service_unattributed",
    }
)


@dataclass(frozen=True)
class Result:
    profile: Profile
    classifications: tuple[Classification, ...]
    context: ClassifyContext
    entity_id: int | None = None


class Profiler:
    def __init__(
        self,
        conn: sqlite3.Connection,
        cache: TransferCache,
        contracts: ContractCache,
        store: IntelStore,
        classifier: Classifier,
        heuristics: Heuristics,
        trace: Trace,
        *,
        clock: Clock = utcnow,
    ) -> None:
        self._conn = conn
        self._cache = cache
        self._contracts = contracts
        self._store = store
        self._s = classifier
        self._heu = heuristics
        self._trace = trace
        self._clock = clock

    async def profile_of(self, chain: Chain, address: str, now: datetime) -> Profile:
        since = now - timedelta(days=self._s.window_days)
        history = await self._cache.history(
            address, since, now, self._trace.hub_transfers, first_activity=True
        )
        return profile(
            address,
            history,
            is_contract=await self._contracts.is_contract(chain, address),
            small_usdt=self._heu.fan_in_small_usdt,
            round_unit_usdt=self._s.round_unit_usdt,
        )

    async def classify(self, chain: Chain, address: str, *, link: bool = True) -> Result:
        now = self._clock()
        p = await self.profile_of(chain, address, now)
        ctx = await self._context(chain, p, now)
        cs = tuple(clf.classify(p, ctx, self._s, now, clf.suppressed(self._conn, chain, address)))
        clf.save(self._conn, chain, p, cs, self._s, now)
        entity_id = self._link(chain, p, cs) if link else None
        return Result(p, cs, ctx, entity_id)

    async def _context(self, chain: Chain, p: Profile, now: datetime) -> ClassifyContext:
        top = p.top_recipient
        if top is None:
            return ClassifyContext()
        top_types = clf.cached(self._conn, chain, top, now)
        if top_types is None:
            top_profile = await self.profile_of(chain, top, now)
            top_types = clf.classify(
                top_profile, ClassifyContext(), self._s, now, clf.suppressed(self._conn, chain, top)
            )
            clf.save(self._conn, chain, top_profile, top_types, self._s, now)
        kinds = {c.type for c in top_types}
        terminal = self._store.best_terminal(chain, top)
        labelled_service = terminal is not None and terminal.category in SERVICE_CATEGORIES
        entity = self._store.entity_for(chain, top)
        return ClassifyContext(
            top_recipient_is_hub="HUB" in kinds or labelled_service,
            hub_entity_named=entity is not None and entity.named_by is not None,
            top_recipient_fresh_or_pass_through=bool(kinds & {"FRESH", "PASS_THROUGH"}),
        )

    def _link(self, chain: Chain, p: Profile, cs: tuple[Classification, ...]) -> int | None:
        deposit = next((c for c in cs if c.type == "DEPOSIT"), None)
        if deposit is None or p.top_recipient is None:
            return None
        current = self._conn.execute(
            "SELECT provenance FROM entity_members WHERE chain = ? AND address_norm = ?",
            (chain.value, p.address),
        ).fetchone()
        if current is not None and current[0] == "operator":
            return None  # the operator's grouping wins
        hub = p.top_recipient
        entity = self._store.entity_for(chain, hub)
        if entity is None:
            entity_id = self._store.create_entity(chain, f"hub-{hub[:8]}")
            self._store.link(chain, hub, entity_id, "hub", {"reason": "top recipient of a deposit"})
        else:
            entity_id = entity.id
        evidence: dict[str, Any] = {
            "classification": "DEPOSIT",
            "confidence": str(deposit.confidence),
            "conditions": list(deposit.conditions),
            "bonuses": list(deposit.bonuses),
            "hub": hub,
        }
        self._store.link(chain, p.address, entity_id, "deposit", evidence)
        return entity_id


class ClassifierSource:
    source = SOURCE
    label = LABEL
    required = False  # an inference: it never blocks, so its absence isn't a gap
    timeout: float | None = None

    def __init__(
        self, profiler: Profiler, store: IntelStore, settings: Classifier, *, clock: Clock = utcnow
    ) -> None:
        self._profiler = profiler
        self._store = store
        self._s = settings
        self._clock = clock

    async def check(self, address: Address) -> SourceResult:
        now = self._clock()
        terminal = self._store.best_terminal(address.chain, address.norm)
        if terminal is not None and terminal.provenance != "inferred":
            return SourceResult(
                SOURCE,
                LABEL,
                False,
                SourceStatus.SKIPPED,
                now,
                detail=f"labelled {terminal.category} ({terminal.provenance}); a label wins over "
                "inference",
            )
        r = await self._profiler.classify(address.chain, address.norm)
        top = clf.primary(r.classifications)
        tags = [c.type for c in r.classifications if not c.primary]
        detail = (
            f"{top.type} ({top.confidence})" + (f", also {', '.join(tags)}" if tags else "")
            if top
            else "no type reached its confidence floor"
        )
        findings: tuple[Finding, ...] = ()
        if (
            top
            and top.type in ("COLLECTOR", "DISTRIBUTOR")
            and top.confidence >= self._s.heu07_min_confidence
        ):
            findings = (
                finding(
                    "R-HEU-07",
                    SOURCE,
                    f"This address behaves like a {top.type.lower()} (confidence {top.confidence})",
                    now,
                    {
                        "priority": "low",
                        "type": top.type,
                        "confidence": str(top.confidence),
                        "conditions": list(top.conditions),
                        "bonuses": list(top.bonuses),
                        "inferred": True,
                    },
                ),
            )
        evidence: dict[str, Any] = {
            "types": [
                {"type": c.type, "confidence": str(c.confidence), "primary": c.primary}
                for c in r.classifications
            ],
            "profile": clf.profile_json(r.profile),
            "classifier_version": clf.CLASSIFIER_VERSION,
        }
        return SourceResult(SOURCE, LABEL, False, SourceStatus.OK, now, findings, detail, evidence)

    async def health(self) -> SourceHealth:
        return SourceHealth(
            SOURCE, LABEL, SourceStatus.OK, None, f"version {clf.CLASSIFIER_VERSION}"
        )
