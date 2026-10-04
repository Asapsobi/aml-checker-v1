"""A stand-in for the corridor: screen a payout's counterparty through the local API (P10, T-10.06).

    export AMLCHECK_API_TOKEN=…
    uv run python scripts/corridor_mock.py <address> --amount 25000 --order 1001

- Sends `POST /v1/check` with `Idempotency-Key: order-<order>`, so a retry after a timeout never
  makes a second check (the API returns the first one).
- Prints the verdict, score and what to do, and exits like `amlcheck check`: 0 NO_HITS, 3 REVIEW,
  4 INCOMPLETE, 5 BLOCK, 1 when the API could not answer.
- The token comes from the environment only; it is never printed.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import httpx

EXIT = {"NO_HITS": 0, "REVIEW": 3, "INCOMPLETE": 4, "BLOCK": 5}


def main() -> int:
    p = argparse.ArgumentParser(description="Screen a payout counterparty via the amlcheck API.")
    p.add_argument("address")
    p.add_argument("--amount", help="USDT amount of the payout")
    p.add_argument("--order", required=True, help="the payout's id; also the Idempotency-Key")
    p.add_argument("--client", help="who the payout is for")
    p.add_argument("--api", default="http://127.0.0.1:8766", help="API base URL")
    p.add_argument("--tries", type=int, default=3, help="attempts on a timeout or connection error")
    args = p.parse_args()
    token = os.environ.get("AMLCHECK_API_TOKEN", "")
    if not token:
        print("error: set AMLCHECK_API_TOKEN in the environment", file=sys.stderr)
        return 1
    body = {"address": args.address, "amount": args.amount, "client": args.client}
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": f"order-{args.order}"}
    for attempt in range(1, args.tries + 1):
        try:
            # A check with a trace can take minutes (PRD performance); wait for it.
            r = httpx.post(f"{args.api}/v1/check", json=body, headers=headers, timeout=420)
        except httpx.TransportError as e:
            why = type(e).__name__
            print(f"attempt {attempt}: {why}; retrying with the same key", file=sys.stderr)
            time.sleep(min(30, 2**attempt))
            continue
        if r.status_code == 409:  # the first attempt is still running: same key, wait and ask again
            time.sleep(10)
            continue
        if r.status_code != 200:
            is_json = r.headers.get("content-type", "").startswith("application/")
            problem = r.json() if is_json else {}
            title = problem.get("title", r.text[:200])
            print(f"error: HTTP {r.status_code}: {title}", file=sys.stderr)
            return 1
        c = r.json()
        replayed = r.headers.get("idempotent-replayed")
        replay = " (replayed: this order was already checked)" if replayed else ""
        score = c["score"]["shown"] if c.get("score") else "-"
        print(f"{c['verdict']} · score {score} · {c['chain']} {c['address']}{replay}")
        print(c["action"])
        for f in c["findings"]:
            print(f"  {f['rule_id']}: {f['summary']}")
        print(f"check {c['check_id']} · audit {c['audit']['record_hash'][:16]}")
        return EXIT.get(c["verdict"], 1)
    print("error: the API did not answer", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
