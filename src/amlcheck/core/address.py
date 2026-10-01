"""Detect, validate and normalise addresses (PRD F1, D-038).

TRON: base58check `T…` (34 chars, version byte 0x41), kept as is. EVM: `0x` + 40 hex, stored
lowercase; a mixed-case address must pass its EIP-55 checksum, so a typo can't silently become
another address.
"""

from __future__ import annotations

import hashlib
import re

import base58
from eth_hash.auto import keccak

from amlcheck.core.models import Address, Chain

_TRON = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")
_EVM = re.compile(r"^0x[0-9a-fA-F]{40}$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_TRON_VERSION = 0x41


class AddressError(ValueError):
    """Not a usable address. The message is meant for the operator as is."""


def detect(raw: str, chain: Chain | None = None) -> Address:
    """Validate `raw` and return it normalised. `chain` overrides detection for EVM (PRD F1.1)."""
    text = raw.strip()
    if _TRON.match(text):
        if chain not in (None, Chain.TRON):
            raise AddressError(f"{text} is a TRON address, not {chain}")
        if not is_valid_tron(text):
            raise AddressError(f"{text} fails its TRON base58check checksum")
        return Address(Chain.TRON, text, raw)
    if _EVM.match(text):
        if chain not in (None, Chain.BSC):
            raise AddressError(
                f"{text} is an EVM address; only bsc is supported for it, not {chain}"
            )
        body = text[2:]
        if body != body.lower() and body != body.upper() and eip55(text) != text:
            raise AddressError(f"{text} fails its EIP-55 checksum (check for a typo)")
        return Address(Chain.BSC, "0x" + body.lower(), raw)
    raise AddressError(f"not a TRON (T…, 34 characters) or EVM (0x + 40 hex) address: {text!r}")


def is_valid_tron(text: str) -> bool:
    try:
        raw = base58.b58decode(text)
    except ValueError:
        return False
    if len(raw) != 25 or raw[0] != _TRON_VERSION:
        return False
    return hashlib.sha256(hashlib.sha256(raw[:21]).digest()).digest()[:4] == raw[21:]


def eip55(address: str) -> str:
    """The EIP-55 checksummed form of a `0x` address."""
    body = address[2:].lower()
    digest = keccak(body.encode()).hex()
    return "0x" + "".join(
        c.upper() if c.isalpha() and int(digest[i], 16) >= 8 else c for i, c in enumerate(body)
    )


def tron_from_hex(hex_address: str) -> str:
    """`41…`, `0x…` or bare 40-hex (as TronGrid events give them, data sources §3) → `T…`."""
    h = hex_address.lower().removeprefix("0x")
    if len(h) == 42 and h.startswith("41"):
        h = h[2:]
    if not _HEX40.match(h):
        raise AddressError(f"not a 20-byte hex address: {hex_address!r}")
    return base58.b58encode_check(bytes([_TRON_VERSION]) + bytes.fromhex(h)).decode()


def tron_to_hex(address: str) -> str:
    """`T…` → 40 lowercase hex without the `41` prefix."""
    if not is_valid_tron(address):
        raise AddressError(f"not a valid TRON address: {address!r}")
    return base58.b58decode(address)[1:21].hex()
