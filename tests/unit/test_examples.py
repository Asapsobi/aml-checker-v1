"""The example files must match the config model (T-0.08): a new key can't ship undocumented."""

import re
import tomllib
from pathlib import Path

from amlcheck.config import SECRET_VARS, Settings, load_settings

ROOT = Path(__file__).resolve().parents[2]


def test_config_example_loads_to_defaults() -> None:
    assert load_settings(ROOT / "config.example.toml") == Settings()


def test_config_example_lists_every_key() -> None:
    data = tomllib.loads((ROOT / "config.example.toml").read_text(encoding="utf-8"))
    text = (ROOT / "config.example.toml").read_text(encoding="utf-8")
    assert list(data) == list(Settings.model_fields)
    for name in Settings.model_fields:
        section = getattr(Settings(), name)
        for key in type(section).model_fields:
            if getattr(section, key) in (None, {}):
                # Keys without a default are shown commented out.
                assert re.search(rf"^# ?(\[{name}\.)?{key}\b", text, re.M), f"[{name}] {key}"
            else:
                assert key in data[name], f"[{name}] {key} missing from config.example.toml"


def test_env_example_lists_every_secret() -> None:
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    names = re.findall(r"^([A-Z_]+)=", text, re.M)
    assert sorted(names) == sorted(SECRET_VARS.values())
    assert all(line.endswith("=") for line in text.splitlines() if re.match(r"^[A-Z_]+=", line))
