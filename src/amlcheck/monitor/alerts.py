"""Alerts for scheduled runs: a macOS notification and an optional local webhook (D-054).

Shared by `watch run` (P8) and `monitor run` (P10). An alert never fails the run: the run's output
and its exit code 6 already carry the news, so a failed notification only prints a warning.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Sequence
from typing import Any

import httpx

TITLE = "amlcheck"
# The message reaches AppleScript as an argument, never inside the script text: no quoting, no
# injection through an address, client name or note.
_SCRIPT = (
    "on run argv",
    "display notification (item 1 of argv) with title (item 2 of argv)",
    "end run",
)


def notify(message: str) -> bool:
    """A macOS notification; False where there is none (other systems, no `osascript`)."""
    osascript = shutil.which("osascript") if sys.platform == "darwin" else None
    if osascript is None:
        return False
    args = [osascript]
    for line in _SCRIPT:
        args += ["-e", line]
    try:
        subprocess.run(  # noqa: S603 - fixed program and script; the message is an argument
            [*args, message, TITLE], check=True, capture_output=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def post(url: str, payload: dict[str, Any], *, timeout: float) -> str | None:
    """POST the changes as JSON to the owner's webhook. A problem, or None when delivered."""
    try:
        resp = httpx.post(url, json=payload, timeout=timeout)
    except httpx.HTTPError as e:
        return f"webhook not reached ({type(e).__name__})"
    if resp.status_code >= 400:
        return f"webhook answered HTTP {resp.status_code}"
    return None


def summary(changes: Sequence[dict[str, Any]]) -> str:
    first = changes[0]
    more = f" and {len(changes) - 1} more" if len(changes) > 1 else ""
    short = f"{first['address'][:8]}…{first['address'][-6:]}"
    return f"{short}: {first['before']} → {first['after']}{more}"
