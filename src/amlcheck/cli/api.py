"""`amlcheck api`: start the local API (PRD F14, D-065).

Listens on 127.0.0.1 only. The token comes from `AMLCHECK_API_TOKEN` (environment or `.env`, never
config) and must be at least 32 characters, or the server does not start (AT-57).
"""

from __future__ import annotations

from typing import Annotated

import typer

from amlcheck.cli import runtime

DEFAULT_PORT = 8766


def api(
    port: Annotated[
        int, typer.Option(min=1024, max=65535, help="Port on 127.0.0.1.")
    ] = DEFAULT_PORT,
) -> None:
    """Start the local API on 127.0.0.1 (Bearer token from AMLCHECK_API_TOKEN)."""
    import uvicorn

    from amlcheck.api.app import MIN_TOKEN, create_app

    rt = runtime.load()
    token = rt.secrets.api_token or ""
    if len(token) < MIN_TOKEN:
        runtime.fail(
            f"set AMLCHECK_API_TOKEN in .env, at least {MIN_TOKEN} characters "
            '(e.g. python -c "import secrets; print(secrets.token_urlsafe(32))"); not starting'
        )
    runtime.open_database(rt).close()  # refuse a foreign or newer database before serving
    typer.echo(f"amlcheck API on http://127.0.0.1:{port}/v1 (internal use only); Ctrl-C to stop")
    uvicorn.run(create_app(rt, token), host="127.0.0.1", port=port, log_level="warning")
