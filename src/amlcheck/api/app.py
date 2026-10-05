"""Local API (PRD F14, D-065), for the corridor and scripts.

- 127.0.0.1 only; a `Host` other than `127.0.0.1` / `localhost` → 400.
- Every request needs `Authorization: Bearer <AMLCHECK_API_TOKEN>` (≥ 32 characters, checked at
  start-up; compared in constant time) → 401 otherwise (AT-57).
- Errors are `application/problem+json`; every verdict, INCOMPLETE included, is HTTP 200 (F14.4).
- `POST /v2/check` answers with the same JSON as `check --json`, from the stored record.
- `POST /v2/check` and `POST /v2/traces` honour `Idempotency-Key`: a replay gets the original result
  (`Idempotent-Replayed: true`), the same key with another request → 422, still running → 409
  (AT-55, AT-56).
- One check and one trace at a time per process (architecture §5).
- v2 (D-073): the contract-2 check JSON. Every `/v1/…` path answers 410 Gone, pointing to `/v2/…`.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import sqlite3
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from amlcheck.api import idempotency as idem
from amlcheck.api.problems import problem
from amlcheck.api.schemas import CheckRequest, TraceRequest
from amlcheck.cases import cases as store_cases
from amlcheck.cases import decisions
from amlcheck.chain.base import canonical_amount
from amlcheck.cli import check as check_cli
from amlcheck.cli import runtime
from amlcheck.core.address import AddressError, detect
from amlcheck.core.clock import to_iso
from amlcheck.core.models import Address, Chain
from amlcheck.core.score import shown
from amlcheck.intel import registry
from amlcheck.intel.store import IntelStore
from amlcheck.monitor import watchlist
from amlcheck.monitor.wallets import is_own
from amlcheck.net.http import Mode
from amlcheck.report.check_json import check_json
from amlcheck.trace.engine import TraceFailed
from amlcheck.trace.jobs import Job, TraceJobs

ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost"})
MIN_TOKEN = 32


def _host(value: str) -> str:
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def _key_ok(key: str) -> bool:
    return 0 < len(key) <= idem.MAX_KEY and key.isprintable() and key.isascii()


def create_app(rt: runtime.Runtime, token: str) -> FastAPI:
    if len(token) < MIN_TOKEN:
        raise ValueError(f"the API token must be at least {MIN_TOKEN} characters")
    check_lock = asyncio.Lock()
    trace_lock = asyncio.Lock()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def db() -> sqlite3.Connection:
        return runtime.open_database(rt)

    @app.middleware("http")
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if _host(request.headers.get("host", "")) not in ALLOWED_HOSTS:
            return problem(400, "Host not allowed", "Use 127.0.0.1 or localhost.")
        scheme, _, given = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(given.strip(), token):
            return problem(
                401,
                "Missing or wrong token",
                "Send Authorization: Bearer <AMLCHECK_API_TOKEN>.",
                headers={"WWW-Authenticate": 'Bearer realm="amlcheck"'},
            )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, e: RequestValidationError) -> Response:
        errors = [
            {"field": ".".join(str(p) for p in err.get("loc", ())[1:]), "message": err.get("msg")}
            for err in e.errors()
        ]
        return problem(422, "Invalid request", kind="invalid-request", errors=errors)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, e: StarletteHTTPException) -> Response:
        return problem(e.status_code, str(e.detail))

    @app.api_route("/v1/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def gone(rest: str) -> Response:
        # D-073: the v1 contract ended with amlcheck 2; say where it went rather than 404.
        return problem(
            410,
            "The v1 API was removed in amlcheck 2",
            f"Use /v2/{rest}: the check JSON is contract 2 (D-073).",
            kind="gone",
            use=f"/v2/{rest}",
        )

    def _address(text: str, chain: str | None) -> Address | Response:
        try:
            return detect(text, Chain(chain) if chain else None)
        except AddressError as e:
            return problem(422, "Invalid address", str(e), kind="invalid-address")

    def _claim(
        conn: sqlite3.Connection, key: str | None, kind: str, request: dict[str, Any]
    ) -> idem.Known | Response | None:
        if key is None:
            return None
        if not _key_ok(key):
            return problem(400, "Invalid Idempotency-Key", "1 to 255 printable ASCII characters.")
        try:
            return idem.begin(conn, key, kind, idem.fingerprint(kind, request), rt.clock())
        except idem.KeyMismatch:
            return problem(
                422,
                "Idempotency-Key reused with a different request",
                "Use a new key for a new request.",
                kind="idempotency-key-mismatch",
            )
        except idem.InProgress:
            return problem(
                409,
                "Request in progress",
                "The first request with this Idempotency-Key is still running.",
                kind="idempotency-in-progress",
            )

    # --- checks --------------------------------------------------------------------------------

    @app.post("/v2/check")
    async def post_check(
        body: CheckRequest, idempotency_key: str | None = Header(default=None)
    ) -> Response:
        addr = _address(body.address, body.chain)
        if isinstance(addr, Response):
            return addr
        trace = runtime.should_trace(rt, body.trace, body.amount)
        client, note = body.client or None, body.note or None
        request = {
            "address": addr.norm,
            "chain": addr.chain.value,
            "amount": canonical_amount(body.amount) if body.amount is not None else None,
            "client": client,
            "note": note,
            "trace": trace,
        }
        conn = db()
        try:
            claim = _claim(conn, idempotency_key, "check", request)
            if isinstance(claim, Response):
                return claim
            if isinstance(claim, idem.Known):
                return JSONResponse(
                    check_json(conn, claim.ref_id), headers={"Idempotent-Replayed": "true"}
                )
            try:
                async with check_lock:
                    result = await check_cli.run_screen(
                        rt, conn, addr, body.amount, client, note, trace
                    )
            except BaseException:
                if idempotency_key:
                    idem.release(conn, idempotency_key)
                raise
            if idempotency_key:
                idem.finish(conn, idempotency_key, result.check_id)
            return JSONResponse(check_json(conn, result.check_id))
        finally:
            conn.close()

    @app.get("/v2/checks/{check_id}")
    async def get_check(check_id: str) -> Response:
        conn = db()
        try:
            data = check_json(conn, check_id)
        finally:
            conn.close()
        if data is None:
            return problem(404, "No such check", check_id)
        return JSONResponse(data)

    # --- traces --------------------------------------------------------------------------------

    async def _run_trace(trace_id: str) -> None:
        async with trace_lock:
            conn = db()
            try:
                jobs = TraceJobs(conn, rt.settings, clock=rt.clock)
                async with httpx.AsyncClient() as client:
                    engine = runtime.build_trace_engine(rt, conn, client, Mode.BACKGROUND)
                    with contextlib.suppress(TraceFailed):  # stored with the job
                        await jobs.run(trace_id, engine)
            finally:
                conn.close()

    def _trace_json(job: Job) -> dict[str, Any]:
        t = job.result or job.partial
        return {
            "trace_id": job.trace_id,
            "status": job.status,
            "chain": job.chain.value,
            "address": job.address,
            "direction": job.direction,
            "requested_by": job.requested_by,
            "created_at": to_iso(job.created_at),
            "started_at": to_iso(job.started_at) if job.started_at else None,
            "finished_at": to_iso(job.finished_at) if job.finished_at else None,
            "progress": job.progress,
            "failure_reason": job.failure_reason,
            "result": job.result.to_json() if job.result else None,
            "partial": job.partial.to_json() if job.partial and not job.result else None,
            "complete": bool(t and t.complete),
        }

    def _accepted(job: Job, replayed: bool) -> Response:
        headers = {"Location": f"/v2/traces/{job.trace_id}"}
        if replayed:
            headers["Idempotent-Replayed"] = "true"
        links = {"self": headers["Location"]}
        body = {"trace_id": job.trace_id, "status": job.status, "links": links}
        return JSONResponse(
            body,
            status_code=202,
            headers=headers,
        )

    @app.post("/v2/traces")
    async def post_trace(
        body: TraceRequest,
        background: BackgroundTasks,
        idempotency_key: str | None = Header(default=None),
    ) -> Response:
        addr = _address(body.address, body.chain)
        if isinstance(addr, Response):
            return addr
        request = {"address": addr.norm, "chain": addr.chain.value, "direction": body.direction}
        conn = db()
        try:
            claim = _claim(conn, idempotency_key, "trace", request)
            if isinstance(claim, Response):
                return claim
            jobs = TraceJobs(conn, rt.settings, clock=rt.clock)
            if isinstance(claim, idem.Known):
                known = jobs.get(claim.ref_id)
                if known is not None:
                    return _accepted(known, True)
            trace_id = jobs.create(addr.chain, addr.norm, body.direction, requested_by="api")
            if idempotency_key:
                idem.finish(conn, idempotency_key, trace_id)
            job = jobs.get(trace_id)
        finally:
            conn.close()
        assert job is not None  # noqa: S101 - just created
        background.add_task(_run_trace, trace_id)
        return _accepted(job, False)

    @app.get("/v2/traces/{trace_id}")
    async def get_trace(trace_id: str) -> Response:
        conn = db()
        try:
            job = TraceJobs(conn, rt.settings, clock=rt.clock).get(trace_id)
        finally:
            conn.close()
        if job is None:
            return problem(404, "No such trace", trace_id)
        return JSONResponse(_trace_json(job))

    # --- counterparties ------------------------------------------------------------------------

    @app.get("/v2/counterparties/{chain}/{address}")
    async def get_counterparty(chain: str, address: str) -> Response:
        if chain not in ("tron", "bsc"):
            return problem(404, "Unknown chain", "tron or bsc")
        addr = _address(address, chain)
        if isinstance(addr, Response):
            return addr
        conn = db()
        try:
            store = IntelStore(conn, clock=rt.clock)
            cp = registry.get(conn, addr.chain, addr.norm)
            entity = store.entity_for(addr.chain, addr.norm)
            terminal = store.best_terminal(addr.chain, addr.norm)
            open_cases = store_cases.cases(conn, status="open", address=addr)
            made = decisions.stored(conn) if cp else []
            mine = [d for d in made if d.decision.check_id in _checks_of(conn, addr)]
            data = {
                "chain": addr.chain.value,
                "address": addr.norm,
                "checked": cp is not None,
                "registry": {
                    "first_screened_at": cp.first_screened_at,
                    "last_screened_at": cp.last_screened_at,
                    "last_check_id": cp.last_check_id,
                    "last_verdict": cp.last_verdict,
                    "last_score": cp.last_score,
                    "last_score_shown": shown(
                        cp.last_score, cp.last_verdict, cp.last_score_version
                    ),
                    "clients": list(cp.clients),
                    "check_count": cp.check_count,
                }
                if cp
                else None,
                "category": terminal.category if terminal else None,
                "labels": [
                    {
                        "id": x.id,
                        "category": x.category,
                        "provenance": x.provenance,
                        "source_ref": x.source_ref,
                        "note": x.note,
                    }
                    for x in store.labels(addr.chain, addr.norm)
                ],
                "entity": {
                    "id": entity.id,
                    "name": entity.name,
                    "kind": entity.kind,
                    "role": entity.role,
                }
                if entity
                else None,
                "own_wallet": is_own(conn, addr.chain, addr.norm),
                "watched": any(
                    w.address == addr.norm and w.chain is addr.chain
                    for w in watchlist.watched(conn)
                ),
                "open_case": (
                    {"case_id": open_cases[0].case_id, "opened_at": open_cases[0].opened_at}
                    if open_cases
                    else None
                ),
                "latest_decision": {
                    **mine[-1].decision.body(),
                    "seq": mine[-1].seq,
                    "record_hash": mine[-1].record_hash,
                }
                if mine
                else None,
            }
        finally:
            conn.close()
        return JSONResponse(data)

    return app


def _checks_of(conn: sqlite3.Connection, addr: Address) -> set[str]:
    return {
        r[0]
        for r in conn.execute(
            "SELECT check_id FROM checks WHERE chain = ? AND address_norm = ?",
            (addr.chain.value, addr.norm),
        )
    }
