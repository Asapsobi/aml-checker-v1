"""`amlcheck web`: start the local web UI (PRD F11.4, D-021, D-055).

Listens on 127.0.0.1 only. A new token is made at every start and printed once in a sign-in
address; the page keeps it in a cookie. Stop with Ctrl-C.
"""

from __future__ import annotations

from typing import Annotated

import typer

from amlcheck.cli import runtime


def web(
    port: Annotated[
        int | None, typer.Option(min=1024, max=65535, help="Port (default [web] port, 8765).")
    ] = None,
) -> None:
    """Start the local web UI on 127.0.0.1."""
    import uvicorn

    from amlcheck.web.app import create_app, new_token

    rt = runtime.load()
    runtime.open_database(rt).close()  # refuse a foreign or newer database before serving
    token = new_token()
    p = port or rt.settings.web.port
    typer.echo("amlcheck web UI, internal use only. Open this address to sign in:")
    typer.echo(f"  http://127.0.0.1:{p}/login?t={token}")
    typer.echo("Anyone with this address can use the UI until you stop it (Ctrl-C).")
    uvicorn.run(create_app(rt, token), host="127.0.0.1", port=p, log_level="warning")
