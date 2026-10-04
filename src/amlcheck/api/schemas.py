"""API request bodies (PRD F14.1). Unknown fields are refused, like config."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CheckRequest(_Body):
    address: str = Field(min_length=1, max_length=100)
    chain: Literal["tron", "bsc"] | None = None
    amount: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    client: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=2000)
    trace: bool | None = None  # default: on from [trace] auto_amount_usdt, as `check`


class TraceRequest(_Body):
    address: str = Field(min_length=1, max_length=100)
    chain: Literal["tron", "bsc"] | None = None
    direction: Literal["in", "out"] = "in"
