import pytest

from amlcheck.core.address import (
    AddressError,
    detect,
    eip55,
    is_valid_tron,
    tron_from_hex,
    tron_to_hex,
)
from amlcheck.core.models import Chain

USDT_TRON = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
USDT_BSC = "0x55d398326f99059fF775485246999027B3197955"  # EIP-55 form


def test_tron_detected_and_kept() -> None:
    a = detect(f"  {USDT_TRON} ")
    assert (a.chain, a.norm) == (Chain.TRON, USDT_TRON)


def test_evm_detected_as_bsc_and_lowercased() -> None:
    a = detect(USDT_BSC)
    assert (a.chain, a.norm) == (Chain.BSC, USDT_BSC.lower())
    assert detect(USDT_BSC.lower()).norm == USDT_BSC.lower()
    assert detect("0x" + USDT_BSC[2:].upper()).norm == USDT_BSC.lower()


def test_chain_override() -> None:
    assert detect(USDT_BSC, Chain.BSC).chain == Chain.BSC
    with pytest.raises(AddressError, match="only bsc"):
        detect(USDT_BSC, Chain.TRON)
    with pytest.raises(AddressError, match="TRON address"):
        detect(USDT_TRON, Chain.BSC)


# AT-08 (address part): mixed-case EVM address with a bad EIP-55 checksum → error.
def test_bad_eip55_refused() -> None:
    bad = USDT_BSC.replace("fF77", "ff77")
    assert bad != USDT_BSC
    with pytest.raises(AddressError, match="EIP-55"):
        detect(bad)


# AT-07 (address part): invalid strings → error.
@pytest.mark.parametrize(
    "raw",
    [
        "",
        "hello",
        USDT_TRON[:-1],  # too short
        USDT_TRON[:-1] + "u",  # right shape, wrong checksum
        "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj60",  # 0 is not base58
        "0x" + "g" * 40,
        "0x" + "a" * 39,
        "bnb136ns6lfw4zs5hg4n85vdthaad7hq5m4gtkgf23",
    ],
)
def test_invalid_refused(raw: str) -> None:
    with pytest.raises(AddressError):
        detect(raw)


def test_eip55_known_vector() -> None:
    # EIP-55 reference vector.
    assert eip55("0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed") == (
        "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
    )


def test_tron_hex_round_trip() -> None:
    # Data sources §3: event addresses are 0x-hex without the 41 prefix (fixture: AddedBlackList).
    t = tron_from_hex("0x8728786c0786a671a3e027008805bc309ef19190")
    assert t == "TNHrhtVnRMRaTJFRsqLUqSytCKsavXJJaa"
    assert tron_to_hex(t) == "8728786c0786a671a3e027008805bc309ef19190"
    assert tron_from_hex("41" + tron_to_hex(USDT_TRON)) == USDT_TRON
    assert is_valid_tron(tron_from_hex("00" * 20))
    with pytest.raises(AddressError):
        tron_from_hex("0x1234")
