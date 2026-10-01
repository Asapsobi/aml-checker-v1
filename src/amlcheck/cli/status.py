"""`amlcheck status`: where amlcheck keeps its data and whether it is usable.

Grows in P2 with source freshness (sanctions age, TRON index lag, freeze-vendor usage).
"""

from __future__ import annotations

import json
from importlib.metadata import version
from typing import Annotated

import typer

from amlcheck.config import ConfigError, load_settings, resolve_paths
from amlcheck.storage.db import MigrationError, load_migrations, open_db, schema_version


def status(
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Show the data folder, config file, database and schema version."""
    paths = resolve_paths()
    try:
        settings = load_settings(paths.config)
        conn = open_db(paths.db)
    except (ConfigError, MigrationError) as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from None
    try:
        db_version = schema_version(conn)
    finally:
        conn.close()
    config_hash = settings.hash()
    info: dict[str, object] = {
        "version": version("amlcheck"),
        "home": str(paths.home),
        "config": str(paths.config),
        "config_found": paths.config.exists(),
        "config_hash": config_hash,
        "db": str(paths.db),
        "db_schema": db_version,
        "db_schema_latest": len(load_migrations()),
    }
    if as_json:
        typer.echo(json.dumps(info, indent=2))
        return
    config_note = "" if info["config_found"] else "  (not found: using defaults)"
    typer.echo(f"amlcheck {info['version']}")
    typer.echo(f"home:    {info['home']}")
    typer.echo(f"config:  {info['config']}{config_note}")
    typer.echo(f"         hash {config_hash[:16]}")
    typer.echo(f"db:      {info['db']}")
    typer.echo(f"         schema {info['db_schema']} (latest {info['db_schema_latest']})")
