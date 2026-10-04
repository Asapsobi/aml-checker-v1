from decimal import Decimal
from pathlib import Path

import pytest

from amlcheck.config import (
    ConfigError,
    Secrets,
    Settings,
    load_secrets,
    load_settings,
    resolve_paths,
)


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(text, encoding="utf-8")
    return p


def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    s = load_settings(tmp_path / "nope.toml")
    assert s == Settings()


def test_defaults_match_methodology() -> None:
    s = Settings()
    assert s.freshness.sanctions_max_age_hours == 48
    assert s.network.max_retry_after_seconds == 10
    assert s.network.max_pacer_wait_seconds == 65
    assert s.exposure.lookback_days == 180
    assert s.exposure.max_transfers == 5000
    assert s.exposure.flagged_inflow_share == Decimal("0.05")
    assert s.heuristics.risky_tags == ("mixer", "bridge", "high_risk")
    assert s.classifier.window_days == 90
    assert s.classifier.ttl_days == 14
    assert s.trace.max_hops == 3
    assert s.trace.max_nodes == 40
    assert s.trace.coverage_share == Decimal("0.8")
    assert s.score.review_at == 31  # D-072
    assert s.score.decay == Decimal("0.4")
    assert s.score.k == 8
    assert s.freshness.tron_index_max_lag_minutes == 60


def test_valid_overrides_load(tmp_path: Path) -> None:
    p = write(
        tmp_path,
        """
[exposure]
lookback_days = 90
flagged_inflow_share = 0.1

[rules.severity]
"R-EXP-01" = "BLOCK"
""",
    )
    s = load_settings(p)
    assert s.exposure.lookback_days == 90
    assert s.exposure.flagged_inflow_share == Decimal("0.1")
    assert s.rules.severity == {"R-EXP-01": "BLOCK"}


# AT-02: unknown key or invalid value → refused at load with a clear message.
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("[exposure]\nlookbak_days = 90\n", "[exposure] lookbak_days: unknown key"),
        ("[nonsense]\nx = 1\n", "nonsense: unknown key"),
        ("[exposure]\nlookback_days = -1\n", "[exposure] lookback_days"),
        ('[exposure]\nlookback_days = "lots"\n', "[exposure] lookback_days"),
        ("[exposure]\nflagged_inflow_share = 1.5\n", "[exposure] flagged_inflow_share"),
        ("[freshness]\ntron_index_max_lag_minutes = 0\n", "[freshness] tron_index_max_lag_minutes"),
        ('[eagle_virtual]\nbase_url = "x"\n', "eagle_virtual: unknown key"),  # D-033
        ('[rules.severity]\n"R-XYZ-01" = "BLOCK"\n', "unknown rule 'R-XYZ-01'"),
        ('[rules.severity]\n"R-SYS-01" = "REVIEW"\n', "R-SYS-01 is fixed"),
        ('[rules.severity]\n"R-HEU-07" = "BLOCK"\n', "R-HEU-07 is an inference"),
        ('[rules.severity]\n"R-TRC-05" = "BLOCK"\n', "R-TRC-05 is an inference"),
        ('[rules.severity]\n"R-SCR-01" = "BLOCK"\n', "R-SCR-01 is the score"),
        ("[score]\nreview_at = 101\n", "[score] review_at"),
        ('[rules.severity]\n"R-EXP-01" = "MAYBE"\n', "[rules] severity.R-EXP-01"),
        ("this is = = not toml", "not valid TOML"),
    ],
)
def test_bad_config_refused(tmp_path: Path, text: str, expected: str) -> None:
    p = write(tmp_path, text)
    with pytest.raises(ConfigError) as exc:
        load_settings(p)
    assert expected in str(exc.value)
    assert str(p) in str(exc.value)


def test_hash_is_stable_and_sensitive(tmp_path: Path) -> None:
    a = Settings().hash()
    assert a == Settings().hash()
    assert len(a) == 64
    changed = load_settings(write(tmp_path, "[trace]\nbranch = 6\n"))
    assert changed.hash() != a


def test_float_thresholds_become_exact_decimals(tmp_path: Path) -> None:
    s = load_settings(write(tmp_path, "[trace]\ninferred_share = 0.1\n"))
    assert s.trace.inferred_share == Decimal("0.1")


def test_paths_default_and_override(tmp_path: Path) -> None:
    p = resolve_paths({})
    assert p.home == Path.home() / ".amlcheck"
    assert p.config == p.home / "config.toml"
    p = resolve_paths({"AMLCHECK_HOME": str(tmp_path)})
    assert p.home == tmp_path
    assert p.db == tmp_path / "amlcheck.db"
    other = tmp_path / "other.toml"
    assert resolve_paths({"AMLCHECK_CONFIG": str(other)}).config == other


def test_secrets_order_env_then_cwd_then_home(tmp_path: Path) -> None:
    home, cwd = tmp_path / "home", tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()
    (home / ".env").write_text(
        "AMLCHECK_TRONGRID_API_KEY=home\nAMLCHECK_HYPERSYNC_TOKEN=home\nAMLCHECK_API_TOKEN=home\n"
    )
    (cwd / ".env").write_text("AMLCHECK_TRONGRID_API_KEY=cwd\nAMLCHECK_HYPERSYNC_TOKEN=cwd\n")
    s = load_secrets(home, cwd=cwd, env={"AMLCHECK_TRONGRID_API_KEY": "env"})
    assert s.trongrid_api_key == "env"
    assert s.hypersync_token == "cwd"
    assert s.api_token == "home"


def test_secrets_repr_hides_values(tmp_path: Path) -> None:
    s = load_secrets(tmp_path, cwd=tmp_path, env={"AMLCHECK_TRONGRID_API_KEY": " sekret-123 "})
    assert s.trongrid_api_key == "sekret-123"
    assert "sekret" not in repr(s)
    assert repr(Secrets()) == (
        "Secrets({'trongrid_api_key': False, 'hypersync_token': False, 'api_token': False})"
    )


def test_secrets_never_in_settings() -> None:
    dumped = str(Settings().model_dump())
    assert "api_key" not in dumped
    assert "token" not in dumped
