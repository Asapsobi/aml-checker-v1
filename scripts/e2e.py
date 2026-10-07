"""End-to-end test, live: every feature through the real commands, on a scratch data folder.

    uv run python scripts/e2e.py                 # a fresh temporary data folder (runs `sync`)
    uv run python scripts/e2e.py --home DIR      # reuse DIR (lists already synced: faster)

- Needs the provider keys (`.env` in this folder or in the data folder) and the network.
- Never uses `~/.amlcheck` (refused): the data folder is a temporary one, or the one you name.
- Asserts what stays true on live data: an OFAC-listed address BLOCKs; for the rest, the shape and
  consistency of the answers (exit codes, JSON fields, files, hash chains), not exact verdicts.
- The API token is made up for the run and only given to the server process; it is never printed.
- Prints PASS / FAIL per step and exits 1 when any step failed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
BLOCKED = "TA3941uFAvmVibSkQ6fMJXxmaSNovX86mz"  # OFAC SDN (CHEIL CREDIT BANK) and Tether-frozen
NBCTF_LISTED = "TB5UPBTtXYwwmSviWBkQcFM64o6R1CDXUY"  # NBCTF ASO 06/26 (VS-20)
TRON_TRACED = "TVvWhZyLcd2DT2Y78XpyrUS3SyzfLeSsWP"  # a real counterparty with a traceable history
BSC = "0x0c1e52495a1d1ed21f00f389284d0eb4e0ee1576"  # a real BSC address
GENESIS = "0" * 64
EXIT_NAMES = {0: "NO_HITS", 3: "REVIEW", 4: "INCOMPLETE", 5: "BLOCK", 6: "attention"}


class Failed(AssertionError):
    pass


def need(condition: bool, message: str) -> None:
    if not condition:
        raise Failed(message)


@dataclass
class Run:
    home: Path
    env: dict[str, str]
    work: Path
    results: list[tuple[str, bool, float, str]] = field(default_factory=list)

    def amlcheck(self, *args: str, timeout: float = 900) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - our own program, fixed arguments
            [amlcheck_exe(), *args],
            cwd=ROOT,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def step(self, name: str, fn: Callable[[], str]) -> None:
        started = time.monotonic()
        try:
            note = fn()
            ok = True
        except (Failed, subprocess.TimeoutExpired, httpx.HTTPError, OSError, ValueError) as e:
            note, ok = f"{type(e).__name__}: {e}", False
        took = time.monotonic() - started
        self.results.append((name, ok, took, note))
        print(f"{'PASS' if ok else 'FAIL'}  {name:<34} {took:6.1f} s  {note}", flush=True)


def amlcheck_exe() -> str:
    """This environment's `amlcheck` (next to the Python running this script)."""
    found = shutil.which("amlcheck", path=str(Path(sys.executable).parent))
    if found is None:
        raise SystemExit("amlcheck is not installed here: run `uv sync`, then `uv run python …`")
    return found


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


def wait_up(url: str, seconds: float = 30) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            httpx.get(url, timeout=1)
            return
        except httpx.TransportError:
            time.sleep(0.3)
    raise Failed(f"server at {url} did not start")


def chain_ok(items: list[dict[str, Any]], prev: str = GENESIS) -> bool:
    for item in items:
        body = json.dumps(item["record"], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        if item["prev_hash"] != prev:
            return False
        if hashlib.sha256((prev + body).encode()).hexdigest() != item["record_hash"]:
            return False
        prev = item["record_hash"]
    return True


def main() -> int:
    p = argparse.ArgumentParser(description="amlcheck end-to-end test (live)")
    p.add_argument("--home", type=Path, help="data folder to use (default: a fresh temporary one)")
    p.add_argument("--keep", action="store_true", help="keep the temporary data folder")
    args = p.parse_args()

    if args.home and args.home.expanduser().resolve() == (Path.home() / ".amlcheck").resolve():
        print("refusing ~/.amlcheck: give a scratch folder (it belongs to your older amlcheck)")
        return 2
    home = args.home.expanduser() if args.home else Path(tempfile.mkdtemp(prefix="amlcheck-e2e-"))
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text('[operator]\nname = "e2e"\n', encoding="utf-8")
    work = Path(tempfile.mkdtemp(prefix="amlcheck-e2e-out-"))
    env = {k: v for k, v in os.environ.items() if not k.startswith("AMLCHECK_")}
    keys = {
        k: v
        for k, v in os.environ.items()
        if k in ("AMLCHECK_TRONGRID_API_KEY", "AMLCHECK_HYPERSYNC_TOKEN")
    }
    env.update(keys)
    env["AMLCHECK_HOME"] = str(home)
    r = Run(home, env, work)
    print(f"amlcheck e2e · data folder {home} · output {work}\n", flush=True)

    def version() -> str:
        out = r.amlcheck("--version")
        need(out.returncode == 0, out.stderr.strip() or out.stdout)
        return out.stdout.strip()

    def sync() -> str:
        out = r.amlcheck("sync", timeout=1800)
        need(out.returncode == 0, (out.stdout + out.stderr).strip()[-300:])
        return out.stdout.strip().splitlines()[-1][:90]

    def status() -> str:
        out = r.amlcheck("status", "--json")
        need(out.returncode == 0, out.stderr.strip())
        data = json.loads(out.stdout)
        stale = [
            s["label"] for s in data.get("sources", []) if s.get("status") not in ("ok", "skipped")
        ]
        need(not stale, f"sources not ok: {', '.join(stale)}")
        return "sources ok"

    def block() -> str:
        out = r.amlcheck("check", BLOCKED, "--json")
        need(out.returncode == 5, f"exit {out.returncode}, expected 5 (BLOCK)")
        data = json.loads(out.stdout)
        rules = {f["rule_id"] for f in data["findings"]}
        need(data["verdict"] == "BLOCK", data["verdict"])
        need("R-SAN-01" in rules, f"no R-SAN-01 in {sorted(rules)}")
        need(data["score"]["score"] == 100, f"score {data['score']}")
        need(data.get("contract") == 2, f"contract {data.get('contract')}")
        label = (data.get("address_label") or {}).get("name", "")
        need(label.startswith("OFAC SDN"), f"address label {label!r}")
        return f"BLOCK · {', '.join(sorted(rules))} · {label}"

    def nbctf() -> str:
        # Five real orders of the official export (VS-20); ASO 06/26's address BLOCKs by name.
        sample = ROOT / "tests" / "fixtures" / "lists" / "nbctf_orders_sample.csv"
        out = r.amlcheck("lists", "import-nbctf", str(sample))
        need(out.returncode == 0, (out.stdout + out.stderr).strip())
        need("NBCTF list: 10 address(es) in all" in out.stdout, out.stdout)
        out = r.amlcheck("check", NBCTF_LISTED, "--json")
        need(out.returncode == 5, f"exit {out.returncode}, expected 5 (BLOCK)")
        data = json.loads(out.stdout)
        san = [f["summary"] for f in data["findings"] if f["rule_id"] == "R-SAN-01"]
        need(any("NBCTF list: order ASO 06/26" in x for x in san), f"R-SAN-01: {san}")
        return f"BLOCK · {san[0][:60]}…"

    def bsc_check() -> str:
        out = r.amlcheck("check", BSC, "--json")
        need(out.returncode in (0, 3, 4, 5), f"exit {out.returncode}")
        data = json.loads(out.stdout)
        fields = {"verdict", "score", "exposures", "detail_list", "findings", "sources", "audit"}
        need(fields <= set(data), "fields missing")
        return (
            f"{data['verdict']} · {data['score']['shown']} · {len(data['exposures'])} exposure(s)"
        )

    def investigate() -> str:
        out = r.amlcheck("investigate", TRON_TRACED, "--amount", "20000", "--json")
        need(out.returncode in (0, 3, 4, 5), f"exit {out.returncode}")
        data = json.loads(out.stdout)
        t = data.get("trace")
        need(t is not None, "no trace with an amount of 20,000")
        total = sum(float(v) for v in t["partition"].values())
        need(abs(total - 1) < 0.001 or not t["complete"], f"partition sums to {total}")
        need(data["trace_id"] == t["trace_id"], "the check does not carry its trace id")
        indirect = sum(1 for e in data["exposures"] if e["exposure_type"] == "indirect")
        return (
            f"{data['verdict']} · {data['score']['shown']} · coverage {t['coverage']} · "
            f"{indirect} indirect exposure(s)"
        )

    def trace_svg() -> str:
        svg = work / "trace.svg"
        out = r.amlcheck("trace", TRON_TRACED, "--svg", str(svg))
        need(out.returncode in (0, 4), f"exit {out.returncode}")
        need(svg.read_text(encoding="utf-8").startswith("<svg "), "not an SVG")
        return f"{svg.stat().st_size} bytes"

    def report() -> str:
        pdf = work / "case.pdf"
        out = r.amlcheck("cp", "report", TRON_TRACED, "--out", str(pdf))
        need(out.returncode == 0, out.stderr.strip())
        need(pdf.read_bytes().startswith(b"%PDF-"), "not a PDF")
        return f"{pdf.stat().st_size} bytes"

    def case() -> str:
        opened = r.amlcheck("case", "open", BLOCKED)
        need(opened.returncode == 0, opened.stderr.strip())
        case_id = opened.stdout.split()[1]
        decided = r.amlcheck("case", "decide", case_id, "rejected", "--note", "e2e: sanctioned")
        need(
            decided.returncode == 0 and "case closed" in decided.stdout,
            decided.stdout + decided.stderr,
        )
        exported = r.amlcheck("case", "export", "--jsonl")
        lines = [json.loads(x) for x in exported.stdout.splitlines() if x.strip()]
        need(any(x["case"]["case_id"] == case_id for x in lines), "decision not in the export")
        return f"case {case_id[:8]} rejected and exported"

    def batch() -> str:
        src = work / "batch.csv"
        src.write_text(f"address,client\n{BLOCKED},e2e\n{BSC},e2e\n", encoding="utf-8")
        out = r.amlcheck("batch", str(src))
        need(out.returncode == 5, f"exit {out.returncode}, expected 5 (one BLOCK row)")
        rows = list(csv.DictReader(io.StringIO(out.stdout)))
        need(len(rows) == 2 and rows[0]["verdict"] == "BLOCK", f"rows: {rows}")
        bad = work / "bad.csv"
        bad.write_text("address\nnot-an-address\n", encoding="utf-8")
        refused = r.amlcheck("batch", str(bad))
        need(refused.returncode == 1, "a bad file was not refused")
        return "2 rows screened; a bad file refused"

    def watch() -> str:
        need(r.amlcheck("watch", "add", BSC, "--client", "e2e").returncode == 0, "watch add")
        first = r.amlcheck("watch", "run")
        need(first.returncode in (0, 6), f"exit {first.returncode}")
        second = r.amlcheck("watch", "run", "--json")
        need(second.returncode in (0, 6), f"exit {second.returncode}")
        return first.stdout.strip().splitlines()[-1]

    def monitor() -> str:
        added = r.amlcheck("wallets", "add", TRON_TRACED, "--name", "e2e test wallet")
        need(added.returncode == 0, added.stderr.strip())
        out = r.amlcheck("monitor", "run", "--json")
        need(out.returncode in (0, 6), f"exit {out.returncode}: {out.stderr.strip()[-200:]}")
        data = json.loads(out.stdout)
        w = data["wallets"][0]
        r.amlcheck("wallets", "remove", TRON_TRACED)
        return f"{w['new_senders']} new sender(s), {len(w['screened'])} screened"

    def exports() -> str:
        out = r.amlcheck("audit", "export", "--format", "json")
        need(out.returncode == 0, out.stderr.strip())
        items = json.loads(out.stdout)
        need(chain_ok(items), "the JSON export does not re-verify")
        pdf = work / "audit.pdf"
        need(
            r.amlcheck("audit", "export", "--format", "pdf", "--out", str(pdf)).returncode == 0,
            "pdf",
        )
        csv_out = r.amlcheck("audit", "export").stdout
        need(csv_out.startswith("seq,check_id"), "csv header")
        return f"{len(items)} records; hash chain re-verified from the JSON"

    def api() -> str:
        port, token = free_port(), secrets.token_urlsafe(32)
        server = subprocess.Popen(  # noqa: S603 - our own program, fixed arguments
            [amlcheck_exe(), "api", "--port", str(port)],
            cwd=ROOT,
            env={**env, "AMLCHECK_API_TOKEN": token},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            base = f"http://127.0.0.1:{port}"
            wait_up(base + "/v2/checks/x")
            auth = {"Authorization": f"Bearer {token}"}
            need(httpx.get(base + "/v2/checks/x").status_code == 401, "no token: not 401")
            bad_host = httpx.get(base + "/v2/checks/x", headers={**auth, "Host": "evil.com"})
            need(bad_host.status_code == 400, "foreign host: not 400")
            key = {**auth, "Idempotency-Key": f"e2e-{secrets.token_hex(4)}"}
            first = httpx.post(
                base + "/v2/check", json={"address": BLOCKED}, headers=key, timeout=600
            )
            need(first.status_code == 200 and first.json()["verdict"] == "BLOCK", first.text[:200])
            again = httpx.post(
                base + "/v2/check", json={"address": BLOCKED}, headers=key, timeout=600
            )
            need(again.headers.get("idempotent-replayed") == "true", "no replay")
            need(again.json()["check_id"] == first.json()["check_id"], "replay made a new check")
            other = httpx.post(
                base + "/v2/check",
                json={"address": BLOCKED, "amount": "5"},
                headers=key,
                timeout=600,
            )
            need(other.status_code == 422, f"same key, other request: {other.status_code}")
            cp = httpx.get(f"{base}/v2/counterparties/tron/{BLOCKED}", headers=auth).json()
            need(cp["checked"] is True, "counterparty not known")
            return "401, 400, replay, 422, counterparty"
        finally:
            server.terminate()
            server.wait(timeout=10)

    def web() -> str:
        port = free_port()
        server = subprocess.Popen(  # noqa: S603 - our own program, fixed arguments
            [amlcheck_exe(), "web", "--port", str(port)],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        try:
            assert server.stdout is not None  # noqa: S101
            login = ""
            for _ in range(5):
                line = server.stdout.readline()
                m = re.search(r"(http://127\.0\.0\.1:\d+/login\?t=\S+)", line)
                if m:
                    login = m.group(1)
                    break
            need(bool(login), "no sign-in address printed")
            wait_up(f"http://127.0.0.1:{port}/static/app.css")
            with httpx.Client(follow_redirects=False) as c:
                need(c.get(f"http://127.0.0.1:{port}/").status_code == 403, "no cookie: not 403")
                need(c.get(login).status_code == 303, "sign-in failed")
                home_page = c.get(f"http://127.0.0.1:{port}/")
                need(home_page.status_code == 200, f"home {home_page.status_code}")
                need("<script" not in home_page.text.lower(), "a script in the page")
                need(
                    "default-src 'none'" in home_page.headers.get("content-security-policy", ""),
                    "CSP missing",
                )
                evil = c.get(f"http://127.0.0.1:{port}/", headers={"Host": "evil.com"})
                need(evil.status_code == 400, "foreign host: not 400")
            return "sign-in, 403 without it, 400 for a foreign host, CSP"
        finally:
            server.terminate()
            server.wait(timeout=10)

    def verify() -> str:
        out = r.amlcheck("audit", "verify")
        need(out.returncode == 0, (out.stdout + out.stderr).strip())
        need("audit log OK" in out.stdout and "decision log OK" in out.stdout, out.stdout)
        return " · ".join(x.strip() for x in out.stdout.splitlines() if "OK" in x)

    steps: list[tuple[str, Callable[[], str]]] = [
        ("version", version),
        ("sync (lists)", sync),
        ("status", status),
        ("check: OFAC address BLOCKs", block),
        ("NBCTF import; a listed address BLOCKs", nbctf),
        ("check: a BSC address", bsc_check),
        ("investigate with the trace", investigate),
        ("trace --svg", trace_svg),
        ("case report PDF", report),
        ("case: open, decide, export", case),
        ("batch (and a bad file)", batch),
        ("watchlist run", watch),
        ("own wallet + monitor run", monitor),
        ("audit export (json re-verified)", exports),
        ("API: auth, host, idempotency", api),
        ("web UI: sign-in, security", web),
        ("audit verify (both chains)", verify),
    ]
    if args.home and (home / "amlcheck.db").exists():
        steps = [s for s in steps if s[0] != "sync (lists)"]
    for name, fn in steps:
        r.step(name, fn)

    failed = [n for n, ok, _, _ in r.results if not ok]
    total = sum(t for _, _, t, _ in r.results)
    print(f"\n{len(r.results) - len(failed)} of {len(r.results)} steps passed in {total:.0f} s")
    if not args.home and not args.keep:
        shutil.rmtree(home, ignore_errors=True)
    if failed:
        print("failed: " + ", ".join(failed))
        print(f"kept the outputs in {work}")
        return 1
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
