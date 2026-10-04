"""Errors as `application/problem+json` (RFC 9457, PRD F14.4).

Every error the API gives has `type`, `title`, `status` and, where it helps, `detail`. A verdict is
never an error: every check answer, INCOMPLETE included, is HTTP 200.
"""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

MEDIA_TYPE = "application/problem+json"
BASE = "https://amlcheck.local/problems/"  # identifiers, not pages (RFC 9457 §3.1.1)


def problem(
    status: int,
    title: str,
    detail: str | None = None,
    *,
    kind: str = "about:blank",
    headers: dict[str, str] | None = None,
    **extra: Any,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": kind if kind == "about:blank" else BASE + kind,
        "title": title,
        "status": status,
    }
    if detail:
        body["detail"] = detail
    body.update(extra)
    return JSONResponse(body, status_code=status, media_type=MEDIA_TYPE, headers=headers)
