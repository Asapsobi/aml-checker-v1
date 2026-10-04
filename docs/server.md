# amlcheck — Server install (Linux, systemd)

> Running amlcheck on the corridor server: the local API always on, `sync`, `monitor run` and
> `watch run` on timers. P10 (T-10.06); D-024 (laptop first, server guide in P10), D-062, D-065.
> Internal use only (D-023).

## Layout

| What | Where |
|---|---|
| The code | `/opt/amlcheck` (a checkout of this repository, `uv sync`) |
| The user | `amlcheck` (no login shell), owns the data folder |
| Data, config, keys, logs | `/var/lib/amlcheck` = `AMLCHECK_HOME` (database, `config.toml`, `.env`, `logs/`) |
| The API | `http://127.0.0.1:8766/v1`, for the corridor on the same machine |

The API only listens on 127.0.0.1. If the corridor runs on another machine, reach it through an SSH
tunnel (`ssh -L 8766:127.0.0.1:8766 server`); never expose the port publicly or behind a proxy.

## Install

```bash
sudo useradd --system --home /var/lib/amlcheck --shell /usr/sbin/nologin amlcheck
sudo install -d -o amlcheck -g amlcheck -m 700 /var/lib/amlcheck
sudo git clone https://github.com/Asapsobi/aml-checker-v1.git /opt/amlcheck
cd /opt/amlcheck && sudo git checkout v0.10.0 && sudo uv sync --frozen --no-dev
```

`/var/lib/amlcheck/.env` (owner `amlcheck`, mode 600; never in the repository or `config.toml`):

```bash
AMLCHECK_TRONGRID_API_KEY=…
AMLCHECK_HYPERSYNC_TOKEN=…
AMLCHECK_API_TOKEN=…   # python -c "import secrets; print(secrets.token_urlsafe(32))", ≥ 32 characters
```

`/var/lib/amlcheck/config.toml` (only what differs from `config.example.toml`), at least:

```toml
[operator]
name = "ops-desk"        # decisions need a name (D-058)
```

Then, as the `amlcheck` user, once: `amlcheck sync`, `amlcheck wallets add <address> --name …` for
every own wallet, and `amlcheck status` (the lists must be fresh).

```bash
sudo -u amlcheck env AMLCHECK_HOME=/var/lib/amlcheck /opt/amlcheck/.venv/bin/amlcheck sync
```

## The API as a service

`/etc/systemd/system/amlcheck-api.service`:

```ini
[Unit]
Description=amlcheck local API
After=network-online.target
Wants=network-online.target

[Service]
User=amlcheck
Environment=AMLCHECK_HOME=/var/lib/amlcheck
WorkingDirectory=/var/lib/amlcheck
ExecStart=/opt/amlcheck/.venv/bin/amlcheck api --port 8766
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/amlcheck
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now amlcheck-api
curl -s -H "Authorization: Bearer $AMLCHECK_API_TOKEN" http://127.0.0.1:8766/v1/checks/none
# → a problem+json 404 means the API is up and the token is right
```

## The scheduled jobs

One oneshot service per job, each with a timer. They share the run lock, so they never overlap; a job
that finds another running exits 1 and the next tick picks the work up.

| Unit | Runs | Timer |
|---|---|---|
| `amlcheck-sync` | `amlcheck sync` | `OnCalendar=*-*-* 06:00` and `18:00` |
| `amlcheck-monitor` | `amlcheck monitor run` | `OnCalendar=*:0/10` (every 10 minutes, D-062) |
| `amlcheck-watch` | `amlcheck watch run` | `OnCalendar=*-*-* 07:30` |

`/etc/systemd/system/amlcheck-monitor.service` (the others differ only in `ExecStart`):

```ini
[Unit]
Description=amlcheck monitor run

[Service]
Type=oneshot
User=amlcheck
Environment=AMLCHECK_HOME=/var/lib/amlcheck
WorkingDirectory=/var/lib/amlcheck
ExecStart=/opt/amlcheck/.venv/bin/amlcheck monitor run
# 6 means "a sender needs attention": reported and alerted, not a failure of the unit
SuccessExitStatus=6
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/amlcheck
```

`/etc/systemd/system/amlcheck-monitor.timer`:

```ini
[Unit]
Description=amlcheck monitor run every 10 minutes

[Timer]
OnCalendar=*:0/10
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now amlcheck-sync.timer amlcheck-monitor.timer amlcheck-watch.timer
systemctl list-timers 'amlcheck-*'
journalctl -u amlcheck-monitor -n 50
```

A server has no desktop notifications: set `[monitor] webhook_url` (for example a local bot) to be
told about verdict changes and senders that need attention (D-054).

## Upgrading

```bash
cd /opt/amlcheck && sudo git fetch --tags && sudo git checkout v<new> && sudo uv sync --frozen --no-dev
sudo systemctl restart amlcheck-api
```

Migrations run on the next start of any command; the database is refused if it is newer than the code
(downgrades are not supported). `amlcheck audit verify` after an upgrade: both chains must verify.

## Backups

The database is the whole state: copy it with SQLite's backup, not `cp`, while things may be running:

```bash
sudo -u amlcheck sqlite3 /var/lib/amlcheck/amlcheck.db ".backup '/var/backups/amlcheck-$(date +%F).db'"
```

Keep the audit and decision chain heads (`amlcheck audit verify`) somewhere else too: a restored copy
must verify up to them.
