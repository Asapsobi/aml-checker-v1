"""Configuration: every section of architecture §6, defaults from the methodology.

Locations (D-020): data in `AMLCHECK_HOME` (default `~/.amlcheck`), config in `AMLCHECK_CONFIG`
(default `<home>/config.toml`). Keys never live in config: they come from the environment, then
`./.env`, then `<home>/.env`, and are kept out of the config hash. Only RPC provider and indexer
keys exist: no third-party AML API (D-033).

Unknown keys and invalid values are refused at load (AT-02), so a typo can't silently fall back to a
default.
"""

from __future__ import annotations

import hashlib
import json
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from amlcheck.core.models import Severity
from amlcheck.core.rules import DEFAULT_SEVERITY, FIXED, NEVER_BLOCK, NEVER_INFO

HOME_ENV = "AMLCHECK_HOME"
CONFIG_ENV = "AMLCHECK_CONFIG"
DB_NAME = "amlcheck.db"

PosInt = Annotated[int, Field(gt=0)]
NonNegInt = Annotated[int, Field(ge=0)]
PosDec = Annotated[Decimal, Field(gt=0)]
Share = Annotated[Decimal, Field(ge=0, le=1)]
PosFloat = Annotated[float, Field(gt=0)]


class ConfigError(Exception):
    """Config could not be loaded. The message is meant for the operator as is."""


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Freshness(_Section):
    """Methodology §2.1: when a required source counts as `stale`."""

    sanctions_max_age_hours: PosInt = 48
    tron_index_max_lag_minutes: PosInt = 60


class Network(_Section):
    """Methodology §2.4 and D-011."""

    timeout_seconds: PosFloat = 20.0
    max_retry_after_seconds: PosFloat = 10.0
    max_pacer_wait_seconds: PosFloat = 65.0


class Rules(_Section):
    """Severity overrides by rule ID (PRD §7.2). R-SYS-01 is fixed; inferences never BLOCK."""

    severity: dict[str, Literal["BLOCK", "REVIEW", "INFO"]] = Field(default_factory=dict)

    @field_validator("severity")
    @classmethod
    def _known_and_allowed(cls, v: dict[str, str]) -> dict[str, str]:
        for rule_id, sev in sorted(v.items()):
            if rule_id not in DEFAULT_SEVERITY:
                raise ValueError(f"unknown rule {rule_id!r}")
            if rule_id in FIXED:
                raise ValueError(f"{rule_id} is fixed at INCOMPLETE and can't be overridden")
            if rule_id in NEVER_BLOCK and sev == Severity.BLOCK:
                why = "the score" if rule_id == "R-SCR-01" else "an inference"
                raise ValueError(f"{rule_id} is {why} and can never be BLOCK (D-017, D-051)")
            if rule_id in NEVER_INFO and sev == Severity.INFO:
                raise ValueError(
                    f"{rule_id} is a hit on the address itself and always changes the verdict; "
                    "it can't be INFO (D-072)"
                )
        return v


class Exposure(_Section):
    """Methodology §3.1, §3.4, §12.1: `lookback_days` is the required window; history before it is
    read too, within `max_transfers` and `history_extension_seconds` (D-079)."""

    lookback_days: PosInt = 180
    max_transfers: PosInt = 20000
    history_extension_seconds: PosFloat = 45.0
    flagged_inflow_share: Share = Decimal("0.05")
    max_findings: PosInt = 10


class Heuristics(_Section):
    """Methodology §3.5."""

    new_address_days: PosInt = 7
    pass_through_share: Share = Decimal("0.9")
    pass_through_hours: PosInt = 24
    fan_window_hours: PosInt = 24
    fan_in_senders: PosInt = 50
    fan_in_small_usdt: PosDec = Decimal("100")
    fan_out_recipients: PosInt = 50
    risky_tags: tuple[str, ...] = ("mixer", "bridge", "high_risk")
    allowlist_tag: str = "allowlist"
    max_findings: PosInt = 10


class Cache(_Section):
    """Transfer cache and retention (data model, retention table)."""

    target_ttl_seconds: NonNegInt = 60
    history_keep_days: PosInt = 30


class Classifier(_Section):
    """Methodology §5–§6, version 1.

    Names follow `<type>_<feature>`; thresholds for confidence bonuses are `<type>_bonus_*`.
    """

    window_days: PosInt = 90
    ttl_days: PosInt = 14
    min_confidence: Share = Decimal("0.6")
    personal_confidence: Share = Decimal("0.5")
    fresh_days: PosInt = 7
    heu07_min_confidence: Share = Decimal("0.7")
    hub_min_counterparties: PosInt = 500
    deposit_min_senders: PosInt = 2
    deposit_top_recipient_share: Share = Decimal("0.9")
    deposit_max_hold_hours: PosDec = Decimal("72")
    deposit_max_retained_share: Share = Decimal("0.05")
    deposit_bonus_min_senders: PosInt = 5
    deposit_bonus_max_hold_hours: PosDec = Decimal("12")
    deposit_bonus_top_recipient_share: Share = Decimal("0.99")
    collector_min_senders: PosInt = 30
    collector_small_in_share: Share = Decimal("0.7")
    collector_top_recipient_share: Share = Decimal("0.8")
    collector_bonus_min_senders: PosInt = 100
    collector_bonus_small_in_share: Share = Decimal("0.9")
    collector_bonus_max_hold_hours: PosDec = Decimal("24")
    distributor_min_recipients_24h: PosInt = 30
    distributor_max_senders: NonNegInt = 3
    distributor_bonus_min_recipients_24h: PosInt = 100
    distributor_bonus_round_share: Share = Decimal("0.5")
    pass_through_share: Share = Decimal("0.9")
    pass_through_min_volume_usdt: PosDec = Decimal("1000")
    pass_through_bonus_share: Share = Decimal("0.98")
    pass_through_bonus_max_hold_hours: PosDec = Decimal("2")
    personal_max_counterparties: PosInt = 50
    round_unit_usdt: PosDec = Decimal("100")


class Trace(_Section):
    """Methodology §7.1."""

    auto_amount_usdt: PosDec = Decimal("10000")
    max_hops: PosInt = 3
    branch: PosInt = 5
    coverage_share: Share = Decimal("0.8")
    min_attributed_usdt: Annotated[Decimal, Field(ge=0)] = Decimal("100")
    hop_window_days: PosInt = 30
    max_nodes: PosInt = 40
    time_budget_seconds: PosInt = 300
    hub_transfers: PosInt = 1000
    min_flagged_usdt: PosDec = Decimal("1000")
    high_risk_share: Share = Decimal("0.05")
    min_coverage: Share = Decimal("0.5")
    inferred_share: Share = Decimal("0.10")


class Score(_Section):
    """Methodology §11.3. `review_at` = 0 turns R-SCR-01 off; 31 is the Moderate floor (D-072)."""

    review_at: Annotated[int, Field(ge=0, le=100)] = 31
    decay: Annotated[Decimal, Field(ge=0, lt=1)] = Decimal("0.4")
    k: PosDec = Decimal("8")
    # D-078: an indirect exposure's volume is its path's bottleneck ("path"), or the trace's
    # proportional estimate ("proportional", less sensitive).
    indirect: Literal["path", "proportional"] = "path"


class Monitor(_Section):
    """PRD F13."""

    rescreen_days: PosInt = 7
    trace_amount_usdt: PosDec = Decimal("10000")
    webhook_url: str | None = None
    max_senders_per_run: PosInt = 50  # D-063
    first_lookback_hours: PosInt = 24  # D-062

    @field_validator("webhook_url")
    @classmethod
    def _http(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith(("http://", "https://")):
            raise ValueError("must be an http:// or https:// URL (D-054)")
        return v


class Web(_Section):
    """The local web UI (PRD F11.4): always on 127.0.0.1."""

    port: Annotated[int, Field(ge=1024, le=65535)] = 8765


class Operator(_Section):
    """Name recorded on decisions (Q-12)."""

    name: str = ""


class Ofac(_Section):
    """Data sources §2."""

    # The same SDN.XML zipped: 2.6 MB, not 29 MB (VS-01, D-040). A plain SDN.XML URL works too.
    sdn_url: str = (
        "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_XML.ZIP"
    )
    changes_url: str = "https://sanctionslistservice.ofac.treas.gov/changes/latest"
    min_kept_share: Share = Decimal("0.8")  # PRD F3.4: > 20% fewer addresses → rejected


class Tron(_Section):
    """Data sources §3, §6. TronGrid limits are per key and unpublished: set them here."""

    api_url: str = "https://api.trongrid.io"
    usdt_contract: str = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
    requests_per_second: PosFloat = 10.0


class Bsc(_Section):
    """Data sources §4, §7."""

    hypersync_url: str = "https://bsc.hypersync.xyz"
    hypersync_fallback_url: str = "https://56.hypersync.xyz"
    usdt_contract: str = "0x55d398326f99059fF775485246999027B3197955"
    seconds_per_block: PosFloat = 0.45
    rpc_url: str = "https://bsc-dataseed.bnbchain.org"  # eth_getCode, no key (VS-12)
    rpc_requests_per_second: PosFloat = 2.0


class Settings(_Section):
    freshness: Freshness = Freshness()
    network: Network = Network()
    rules: Rules = Rules()
    exposure: Exposure = Exposure()
    heuristics: Heuristics = Heuristics()
    cache: Cache = Cache()
    classifier: Classifier = Classifier()
    trace: Trace = Trace()
    score: Score = Score()
    monitor: Monitor = Monitor()
    web: Web = Web()
    operator: Operator = Operator()
    ofac: Ofac = Ofac()
    tron: Tron = Tron()
    bsc: Bsc = Bsc()

    def hash(self) -> str:
        """sha256 of the effective config, stored with every check (architecture §6)."""
        canonical = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class Paths:
    home: Path
    config: Path

    @property
    def db(self) -> Path:
        return self.home / DB_NAME


@dataclass(frozen=True)
class Secrets:
    """Provider keys. `repr` hides values so they never reach logs or tracebacks."""

    trongrid_api_key: str | None = None
    hypersync_token: str | None = None
    api_token: str | None = None

    def __repr__(self) -> str:
        shown = {
            "trongrid_api_key": bool(self.trongrid_api_key),
            "hypersync_token": bool(self.hypersync_token),
            "api_token": bool(self.api_token),
        }
        return f"Secrets({shown})"


SECRET_VARS = {
    "trongrid_api_key": "AMLCHECK_TRONGRID_API_KEY",
    "hypersync_token": "AMLCHECK_HYPERSYNC_TOKEN",
    "api_token": "AMLCHECK_API_TOKEN",
}


def resolve_paths(env: Mapping[str, str] | None = None) -> Paths:
    env = os.environ if env is None else env
    home = Path(env.get(HOME_ENV) or Path.home() / ".amlcheck").expanduser()
    config = Path(env.get(CONFIG_ENV) or home / "config.toml").expanduser()
    return Paths(home=home, config=config)


def load_settings(path: Path) -> Settings:
    """Load and validate config. A missing file means all defaults."""
    if not path.exists():
        return Settings()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise ConfigError(f"{path}: not valid TOML: {e}") from e
    except OSError as e:
        raise ConfigError(f"{path}: can't read: {e}") from e
    try:
        return Settings.model_validate(data)
    except ValidationError as e:
        raise ConfigError(_format_errors(path, e)) from None


def _format_errors(path: Path, e: ValidationError) -> str:
    lines = [f"{path}: invalid config"]
    for err in e.errors():
        loc = [str(p) for p in err["loc"]]
        where = f"[{loc[0]}] {'.'.join(loc[1:])}".rstrip() if len(loc) > 1 else loc[0]
        msg = "unknown key" if err["type"] == "extra_forbidden" else err["msg"]
        lines.append(f"  {where}: {msg}")
    return "\n".join(lines)


def load_secrets(
    home: Path, cwd: Path | None = None, env: Mapping[str, str] | None = None
) -> Secrets:
    """Environment first, then `./.env`, then `<home>/.env` (architecture §6)."""
    env = os.environ if env is None else env
    cwd = Path.cwd() if cwd is None else cwd
    layers: list[Mapping[str, str | None]] = [
        env,
        dotenv_values(cwd / ".env"),
        dotenv_values(home / ".env"),
    ]

    def get(var: str) -> str | None:
        for layer in layers:
            value = layer.get(var)
            if value:
                return value.strip()
        return None

    return Secrets(
        trongrid_api_key=get(SECRET_VARS["trongrid_api_key"]),
        hypersync_token=get(SECRET_VARS["hypersync_token"]),
        api_token=get(SECRET_VARS["api_token"]),
    )
