"""One scheduled run at a time: `batch`, `watch run` (and `monitor run`, P10) share a lock file in
the data folder (architecture §5).

Two such runs side by side would each pace TronGrid at the process limit and together go over the
key's limit (VS-05, D-036), so the second one stops at once instead of waiting. The lock is an OS
file lock: it goes away with the process, so a crash never leaves it stuck.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

LOCK_NAME = "runs.lock"


class Busy(RuntimeError):
    """Another run holds the lock."""


@contextmanager
def run_lock(home: Path, what: str) -> Iterator[None]:
    home.mkdir(parents=True, exist_ok=True)
    fd = os.open(home / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Busy(
                f"another batch or watch run is in progress (lock {home / LOCK_NAME}); "
                f"{what} not started"
            ) from None
        yield
    finally:
        os.close(fd)  # closing releases the lock
