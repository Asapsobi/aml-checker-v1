"""Local web UI (PRD F11.4, F11.5, D-021, D-055).

Security, in order of what a request meets:
- Host allow-list: only `127.0.0.1` and `localhost` (any port). Anything else is 400, which also
  stops DNS rebinding (AT-48).
- Start-up token: `amlcheck web` prints a one-time login URL; `/login` checks the token and sets it
  as an HttpOnly, SameSite=Strict cookie. Every page needs the cookie (403 without), and every POST
  also carries the token in the form (403 without, AT-48).
- No scripts at all (D-055): `default-src 'none'`, same-origin styles and form actions only, no
  framing, no referrer. A running trace's page refreshes itself with a meta tag.
- Templates autoescape everything; the server only listens on 127.0.0.1.

Pages: check form and recent checks, a check's detail (the case report's data, explorer links, the
PDF), history, counterparties and one counterparty, and traces with progress. One check and one
trace at a time per process (architecture §5).
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import secrets
import sqlite3
from collections.abc import Awaitable, Callable
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.middleware.trustedhost import TrustedHostMiddleware

from amlcheck.cli import check as check_cli
from amlcheck.cli import runtime
from amlcheck.core.address import AddressError, detect
from amlcheck.core.models import Address, Chain, Verdict
from amlcheck.core.score import from_json, shown
from amlcheck.core.verdict import ACTION
from amlcheck.intel import registry
from amlcheck.intel.lookalike import lookalikes
from amlcheck.intel.store import IntelStore
from amlcheck.monitor import watchlist
from amlcheck.net.http import Mode
from amlcheck.report import case_report
from amlcheck.report import export as exporter
from amlcheck.trace.engine import TraceFailed
from amlcheck.trace.graph import render, short
from amlcheck.trace.jobs import TraceJobs
from amlcheck.trace.model import dec, pct

HERE = Path(__file__).parent
COOKIE = "amlcheck_token"
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
CSP = (
    "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; "
    "frame-ancestors 'none'; base-uri 'none'"
)
EXPLORER = {
    Chain.TRON: ("https://tronscan.org/#/address/{}", "https://tronscan.org/#/transaction/{}"),
    Chain.BSC: ("https://bscscan.com/address/{}", "https://bscscan.com/tx/{}"),
}
VERDICT_CLASS = {
    "BLOCK": "block",
    "INCOMPLETE": "incomplete",
    "REVIEW": "review",
    "NO_HITS": "nohits",
}


def new_token() -> str:
    return secrets.token_urlsafe(32)


def explorer(chain: str, address: str) -> str:
    return EXPLORER[Chain(chain)][0].format(address)


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(HERE / "templates"),
        autoescape=select_autoescape(default=True, default_for_string=True),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals.update(
        explorer=explorer,
        cents=Decimal("0.01"),
        short=short,
        pct=pct,
        dec=dec,
        shown=shown,
        verdict_class=lambda v: VERDICT_CLASS.get(str(v), ""),
        action=lambda v: ACTION[Verdict(v)],
    )
    return env


def create_app(rt: runtime.Runtime, token: str) -> FastAPI:
    env = _env()
    check_lock = asyncio.Lock()  # one check at a time per process (architecture §5)
    trace_lock = asyncio.Lock()  # one trace worker per process (PRD F9.5)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def page(request: Request, name: str, status: int = 200, **ctx: Any) -> HTMLResponse:
        html = env.get_template(name).render(token=token, path=request.url.path, **ctx)
        return HTMLResponse(html, status_code=status)

    def db() -> sqlite3.Connection:
        return runtime.open_database(rt)

    @app.middleware("http")
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if path != "/login" and not path.startswith("/static/"):
            cookie = request.cookies.get(COOKIE, "")
            if not hmac.compare_digest(cookie, token):
                return _forbidden("Open the address printed by `amlcheck web` to sign in.")
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        return response

    # Added after `guard`, so it runs first: a bad Host never reaches the token check.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    def posted(form_token: str) -> Response | None:
        if not hmac.compare_digest(form_token, token):
            return _forbidden("This form is missing the session token.")
        return None

    @app.get("/login")
    async def login(t: str = "") -> Response:
        if not hmac.compare_digest(t, token):
            return _forbidden("Wrong or missing token: use the address printed by `amlcheck web`.")
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict", path="/")
        return response

    # --- checks --------------------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> HTMLResponse:
        conn = db()
        try:
            recent = list(reversed(exporter.rows(conn, exporter.Filter())))[:10]
        finally:
            conn.close()
        return page(request, "index.html", recent=recent, form={}, error=None)

    @app.post("/check")
    async def run_check(
        request: Request,
        address: str = Form(""),
        chain: str = Form(""),
        amount: str = Form(""),
        client: str = Form(""),
        note: str = Form(""),
        trace: str = Form(""),
        token_: str = Form("", alias="token"),
    ) -> Response:
        if (refused := posted(token_)) is not None:
            return refused
        form = {
            "address": address,
            "chain": chain,
            "amount": amount,
            "client": client,
            "note": note,
            "trace": trace,
        }
        try:
            addr = detect(address.strip(), Chain(chain) if chain else None)
            value = Decimal(amount.replace(",", "")) if amount.strip() else None
            if value is not None and (not value.is_finite() or value < 0):
                raise InvalidOperation
        except AddressError as e:
            return page(request, "index.html", 400, recent=[], form=form, error=str(e))
        except (InvalidOperation, ValueError):
            return page(
                request,
                "index.html",
                400,
                recent=[],
                form=form,
                error="Amount must be a positive number.",
            )
        run_trace = bool(trace) or (
            value is not None and value >= rt.settings.trace.auto_amount_usdt
        )
        async with check_lock:
            conn = db()
            try:
                result = await check_cli.run_screen(
                    rt, conn, addr, value, client.strip() or None, note.strip() or None, run_trace
                )
            finally:
                conn.close()
        return RedirectResponse(f"/checks/{result.check_id}", status_code=303)

    @app.get("/checks", response_class=HTMLResponse)
    async def history(
        request: Request, address: str = "", verdict: str = "", client: str = ""
    ) -> HTMLResponse:
        f = exporter.Filter(verdict=verdict or None, client=client or None)
        error = None
        if address:
            try:
                a = detect(address.strip())
                f = exporter.Filter(
                    chain=a.chain.value, address=a.norm, verdict=f.verdict, client=f.client
                )
            except AddressError as e:
                error = str(e)
        conn = db()
        try:
            found = [] if error else list(reversed(exporter.rows(conn, f)))[:200]
        finally:
            conn.close()
        return page(
            request,
            "history.html",
            rows=found,
            error=error,
            q={"address": address, "verdict": verdict, "client": client},
        )

    def _check_address(conn: sqlite3.Connection, check_id: str) -> Address | None:
        row = conn.execute(
            "SELECT chain, address_norm FROM checks WHERE check_id = ?", (check_id,)
        ).fetchone()
        return detect(row[1], Chain(row[0])) if row else None

    @app.get("/checks/{check_id}", response_class=HTMLResponse)
    async def check_detail(request: Request, check_id: str) -> HTMLResponse:
        conn = db()
        try:
            addr = _check_address(conn, check_id)
            if addr is None:
                return page(request, "error.html", 404, message=f"No check {check_id}.")
            data = case_report.gather(conn, addr, check_id)
        finally:
            conn.close()
        score = from_json(data.record.score_json) if data.record.score_json else None
        exposure = next(
            (s.evidence for s in data.record.sources if s.source == "exposure" and s.evidence),
            None,
        )
        return page(
            request, "check.html", d=data, score=score, exposure=exposure, labels=case_report.LABELS
        )

    @app.get("/checks/{check_id}/report.pdf")
    async def check_pdf(check_id: str) -> Response:
        conn = db()
        try:
            addr = _check_address(conn, check_id)
            if addr is None:
                return PlainTextResponse("no such check", status_code=404)
            pdf = case_report.render(case_report.gather(conn, addr, check_id))
        finally:
            conn.close()
        name = f"case-{addr.chain.value}-{addr.norm[:10]}-{check_id[:8]}.pdf"
        return Response(
            pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    # --- counterparties ------------------------------------------------------------------------

    @app.get("/counterparties", response_class=HTMLResponse)
    async def counterparties(
        request: Request, chain: str = "", verdict: str = "", client: str = ""
    ) -> HTMLResponse:
        conn = db()
        try:
            rows = registry.query(
                conn,
                chain=Chain(chain) if chain in ("tron", "bsc") else None,
                verdict=verdict or None,
                client=client or None,
                limit=500,
            )
        finally:
            conn.close()
        return page(
            request,
            "counterparties.html",
            rows=rows,
            q={"chain": chain, "verdict": verdict, "client": client},
        )

    @app.get("/counterparties/{chain}/{address}", response_class=HTMLResponse)
    async def counterparty(request: Request, chain: str, address: str) -> HTMLResponse:
        try:
            addr = detect(address, Chain(chain))
        except (AddressError, ValueError):
            return page(request, "error.html", 404, message="Not a valid address.")
        conn = db()
        try:
            store = IntelStore(conn, clock=rt.clock)
            info: dict[str, Any] = {
                "cp": registry.get(conn, addr.chain, addr.norm),
                "labels": store.labels(addr.chain, addr.norm),
                "entity": store.entity_for(addr.chain, addr.norm),
                "terminal": store.best_terminal(addr.chain, addr.norm),
                "looks_like": lookalikes(conn, addr),
                "checks": list(
                    reversed(
                        exporter.rows(
                            conn, exporter.Filter(chain=addr.chain.value, address=addr.norm)
                        )
                    )
                )[:50],
                "watched": any(
                    w.address == addr.norm and w.chain is addr.chain
                    for w in watchlist.watched(conn)
                ),
                "trace": TraceJobs(conn, rt.settings, clock=rt.clock).latest(addr.chain, addr.norm),
            }
        finally:
            conn.close()
        return page(request, "counterparty.html", a=addr, **info)

    # --- traces --------------------------------------------------------------------------------

    async def _run_trace(trace_id: str) -> None:
        async with trace_lock:
            conn = db()
            try:
                jobs = TraceJobs(conn, rt.settings, clock=rt.clock)
                async with httpx.AsyncClient() as client:
                    engine = runtime.build_trace_engine(rt, conn, client, Mode.BACKGROUND)
                    # A failure is stored with the job; the page shows the partial and the reason.
                    with contextlib.suppress(TraceFailed):
                        await jobs.run(trace_id, engine)
            finally:
                conn.close()

    @app.post("/trace")
    async def start_trace(
        request: Request,
        background: BackgroundTasks,
        address: str = Form(""),
        direction: str = Form("in"),
        token_: str = Form("", alias="token"),
    ) -> Response:
        if (refused := posted(token_)) is not None:
            return refused
        try:
            addr = detect(address.strip())
        except AddressError as e:
            return page(request, "error.html", 400, message=str(e))
        conn = db()
        try:
            trace_id = TraceJobs(conn, rt.settings, clock=rt.clock).create(
                addr.chain, addr.norm, "out" if direction == "out" else "in", requested_by="web"
            )
        finally:
            conn.close()
        background.add_task(_run_trace, trace_id)
        return RedirectResponse(f"/traces/{trace_id}", status_code=303)

    @app.get("/traces/{trace_id}", response_class=HTMLResponse)
    async def trace_page(request: Request, trace_id: str) -> HTMLResponse:
        conn = db()
        try:
            job = TraceJobs(conn, rt.settings, clock=rt.clock).get(trace_id)
        finally:
            conn.close()
        if job is None:
            return page(request, "error.html", 404, message=f"No trace {trace_id}.")
        t = job.result or job.partial
        svg = render(t) if t is not None else None
        return page(
            request,
            "trace.html",
            job=job,
            t=t,
            svg=svg,
            running=job.status in ("queued", "running"),
        )

    return app


def _forbidden(message: str) -> Response:
    return PlainTextResponse(message, status_code=403, headers={"Content-Security-Policy": CSP})
